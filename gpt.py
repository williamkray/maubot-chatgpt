import asyncio
import json
import os
import re
from datetime import datetime

from typing import Type, Deque, Dict, Generator
from mautrix.client import Client
from collections import deque, defaultdict
from maubot.handlers import command, event
from maubot import Plugin, MessageEvent
from mautrix.errors import MNotFound, MatrixRequestError
from mautrix.types import Format, TextMessageEventContent, EventType, RoomID, UserID, MessageType, RelationType, EncryptedEvent
from mautrix.util import markdown
from mautrix.util.config import BaseProxyConfig, ConfigUpdateHelper

class Config(BaseProxyConfig):
    def do_update(self, helper: ConfigUpdateHelper) -> None:
        helper.copy("api_endpoint")
        helper.copy("gpt_api_key")
        helper.copy("model")
        helper.copy("max_tokens")
        helper.copy("enable_multi_user")
        helper.copy("system_prompt")
        helper.copy("name")
        helper.copy("allowed_users")
        helper.copy("addl_context")
        helper.copy("max_words")
        helper.copy("max_context_messages")
        helper.copy("reply_in_thread")
        helper.copy("temperature")
        helper.copy("respond_to_replies")

class GPTPlugin(Plugin):

    name: str # name of the bot

    async def start(self) -> None:
        await super().start()
        self.config.load_and_update()
        self.name = self.config['name'] or \
            await self.client.get_displayname(self.client.mxid) or \
            self.client.parse_user_id(self.client.mxid)[0]
        self.api_endpoint = self.config['api_endpoint']
        self.log.debug(f"DEBUG gpt plugin started with bot name: {self.name}")
        self.log.debug(f"DEBUG gpt endpoint set: {self.api_endpoint}")

    def user_allowed(self, mxid) -> bool:
        for u in self.config['allowed_users']:
            self.log.debug(f"DEBUG {mxid} vs. {u}")
            if re.match(u, mxid):
                return True
            else:
                self.log.debug(f"DEBUG {mxid} doesn't match {u}")
                pass

    def extract_json_from_response(self, content: str) -> str:
        """
        Extract JSON from the model response, handling cases where the model
        includes extra text before or after the JSON.
        """
        # Try to find JSON object in the response
        # Look for JSON objects that contain both "sender" and "message" fields
        json_pattern = r'\{[^{}]*"sender"\s*:\s*"[^"]*"[^{}]*"message"\s*:\s*"[^"]*"[^{}]*\}'
        json_match = re.search(json_pattern, content, re.DOTALL)
        
        if json_match:
            try:
                json_str = json_match.group(0)
                parsed_json = json.loads(json_str)
                
                # Validate the JSON structure
                if "sender" in parsed_json and "message" in parsed_json:
                    self.log.debug(f"Successfully extracted JSON: {parsed_json}")
                    return parsed_json["message"]
                else:
                    self.log.warning(f"JSON found but missing required fields: {parsed_json}")
            except json.JSONDecodeError as e:
                self.log.warning(f"Failed to parse JSON: {e}")
        
        # Try a more flexible approach - look for any JSON object
        try:
            # Find the first { and last } to extract potential JSON
            start = content.find('{')
            end = content.rfind('}')
            if start != -1 and end != -1 and end > start:
                json_str = content[start:end+1]
                parsed_json = json.loads(json_str)
                if "sender" in parsed_json and "message" in parsed_json:
                    self.log.debug(f"Successfully extracted JSON with flexible parsing: {parsed_json}")
                    return parsed_json["message"]
        except (json.JSONDecodeError, ValueError) as e:
            self.log.debug(f"Flexible JSON parsing failed: {e}")
        
        # Fallback: if no valid JSON found, return the original content
        # but strip any potential name prefixes
        self.log.warning(f"No valid JSON found in response, falling back to original content")
        content = re.sub('^\w*\:+\s+', '', content)
        return content

    async def should_respond(self, event: MessageEvent) -> bool:
        """ Determine if we should respond to an event """

        if (event.sender == self.client.mxid or  # Ignore ourselves
                event.content.body.startswith('!') or # Ignore commands
                event.content['msgtype'] != MessageType.TEXT or  # Don't respond to media or notices
                event.content.relates_to['rel_type'] == RelationType.REPLACE):  # Ignore edits
            return False

        # Check if the message contains the bot's ID
        if (
                re.search("(^|\s)(@)?" + self.name + "([ :,.!?]|$)", event.content.body, re.IGNORECASE) or
                (event.content['m.mentions'] and self.client.mxid in event.content['m.mentions']['user_ids'])
            ):
            if len(self.config['allowed_users']) > 0 and not self.user_allowed(event.sender):
                await event.respond("sorry, you're not allowed to use this functionality.")
                return False
            else:
                return True

        # Reply to all DMs as long as the person is allowed
        if len(await self.client.get_joined_members(event.room_id)) == 2:
            if len(self.config['allowed_users']) > 0 and not self.user_allowed(event.sender):
                await event.respond("sorry, you're not allowed to use this functionality.")
                return False
            else:
                return True

        # Reply to threads if the thread's parent should be replied to
        if self.config['reply_in_thread'] and event.content.relates_to.rel_type == RelationType.THREAD:
            parent_event = await self.client.get_event(room_id=event.room_id, event_id=event.content.get_thread_parent())
            return await self.should_respond(parent_event)

        # Reply to messages replying to the bot by checking if the parent message as the `org.jobmachine.chatgpt` key
        if event.content.relates_to.in_reply_to:
            parent_event = await self.client.get_event(room_id=event.room_id, event_id=event.content.get_reply_to())
            if parent_event.sender == self.client.mxid and "org.jobmachine.chatgpt" in parent_event.content:
                return True

        return False


    @event.on(EventType.ROOM_MESSAGE)
    async def on_message(self, event: MessageEvent) -> None:

        if not await self.should_respond(event):
            return

        try:
            context = await self.get_context(event)
            await event.mark_read()

            # Call the chatGPT API to get a response
            await self.client.set_typing(event.room_id, timeout=99999)
            response = await self._call_gpt(context)

            # Send the response back to the chat room
            await self.client.set_typing(event.room_id, timeout=0)

            content = TextMessageEventContent(msgtype=MessageType.NOTICE, body=response, format=Format.HTML,
                                              formatted_body=markdown.render(response))
            content["org.jobmachine.chatgpt"] = True
            await event.respond(content, in_thread=self.config['reply_in_thread'])

        except Exception as e:
            self.log.exception(f"Something went wrong: {e}")
            await event.respond(f"Something went wrong: {e}")
            pass

    async def _call_gpt(self, prompt):
        full_context = []


        full_context.extend(list(prompt))
        headers = {
            "Content-Type": "application/json",
            "Authorization": f"Bearer {self.config['gpt_api_key']}"
        }
        data = {
            "model": self.config['model'],
            "messages": full_context,
        }

        if 'max_tokens' in self.config and self.config['max_tokens']:
            data["max_tokens"] = self.config['max_tokens']


        if 'temperature' in self.config and self.config['temperature']:
            data["temperature"] = self.config['temperature']

        self.log.debug("CONTEXT:\n" + "\n".join([f'{m["role"]}: {m["content"]}' for m in full_context]))

        async with self.http.post(
            self.api_endpoint, headers=headers, data=json.dumps(data)
        ) as response:
            if response.status != 200:
                return f"Error: {await response.text()}"
            response_json = await response.json()
            content = response_json["choices"][0]["message"]["content"]
            self.log.debug(f'GPT tokens used: {response_json["usage"]}')
            self.log.debug(f'Raw response content: {content}')
            
            # Extract JSON from the response and return only the message content
            extracted_message = self.extract_json_from_response(content)
            return str(extracted_message)

    @command.new(name='gpt', help='control chatGPT functionality', require_subcommand=True)
    async def gpt(self, evt: MessageEvent) -> None:
        pass


    async def get_context(self, event: MessageEvent):

        system_context = deque()
        timestamp = datetime.today().strftime('%Y-%m-%d %H:%M:%S')
        # System prompt as a JSON message
        system_prompt_content = self.config['system_prompt'].format(name=self.name, timestamp=timestamp)
        system_prompt_instruction = (
            'IMPORTANT: All messages in this conversation are formatted as JSON with "sender" and "message" fields. '
            'Each message you receive will be in the format: {"sender": "username", "message": "their message"}. '
            'You must respond with valid JSON in the following format: '
            '{"sender": "your_name", "message": "your response content"}. '
            'Do not include any text before or after the JSON. Only return the JSON object with your response in the "message" field.'
        )
        if self.config['enable_multi_user']:
            system_prompt_instruction += (
                ' User messages are in the context of multiperson chatrooms.'
            )
        system_json = json.dumps({
            "sender": "system",
            "message": f"{system_prompt_content}\n{system_prompt_instruction}"
        })
        system_context.append({"role": "system", "content": system_json})

        addl_context = json.loads(json.dumps(self.config['addl_context']))
        if addl_context:
            for item in addl_context:
                # Convert OpenAI-style context to JSON format if needed
                if isinstance(item, dict) and 'role' in item and 'content' in item:
                    if item['role'] == 'user':
                        sender = 'chat user'
                    elif item['role'] == 'assistant':
                        sender = self.name
                    elif item['role'] == 'system':
                        sender = 'system'
                    else:
                        sender = item['role']
                    item_json = {"sender": sender, "message": item['content']}
                    system_context.append({"role": item['role'], "content": json.dumps(item_json)})
                else:
                    # Already in JSON or unknown format, wrap as system
                    if not (isinstance(item, dict) and "sender" in item and "message" in item):
                        item = {"sender": "system", "message": str(item)}
                    system_context.append({"role": "system", "content": json.dumps(item)})
            if len(addl_context) > self.config["max_context_messages"] - 1:
                raise ValueError(f"sorry, my configuration has too many additional prompts "
                                 f"({self.config['max_context_messages']}) and i'll never see your message. "
                                    f"Update my config to have fewer messages and i'll be able to answer your questions!")

        chat_context = deque()
        word_count = sum([len(json.loads(m["content"])['message'].split()) for m in system_context])
        message_count = len(system_context) - 1
        async for next_event in self.generate_context_messages(event):
            # Ignore events that aren't text messages
            try:
                if not next_event.content.msgtype.is_text:
                    continue
            except (KeyError,  AttributeError):
                continue

            role = 'assistant' if next_event.sender == self.client.mxid else 'user'
            message = next_event['content']['body']
            if self.config['enable_multi_user']:
                sender_name = await self.client.get_displayname(next_event.sender) or \
                            self.client.parse_user_id(next_event.sender)[0]
            else:
                sender_name = "chat user"
            json_message = json.dumps({"sender": sender_name, "message": message})

            word_count += len(message.split())
            message_count += 1
            if word_count >= self.config['max_words'] or message_count >= self.config['max_context_messages']:
                break

            chat_context.appendleft({"role": role, "content": json_message})

        return system_context + chat_context

    async def generate_context_messages(self, evt: MessageEvent) -> Generator[MessageEvent, None, None]:
        yield evt
        if self.config['reply_in_thread']:
            while evt.content.relates_to.in_reply_to:
                evt = await self.client.get_event(room_id=evt.room_id, event_id=evt.content.get_reply_to())
                yield evt
        else:
            event_context = await self.client.get_event_context(room_id=evt.room_id, event_id=evt.event_id, limit=self.config["max_context_messages"]*2)
            previous_messages = iter(event_context.events_before)
            for evt in previous_messages:

                # We already have the event, but currently, get_event_context doesn't automatically decrypt events
                if isinstance(evt, EncryptedEvent) and self.client.crypto:
                    evt = await self.client.get_event(event_id=evt.event_id, room_id=evt.room_id)
                    if not evt:
                        raise ValueError("Decryption error!")

                yield evt

    @classmethod
    def get_config_class(cls) -> Type[BaseProxyConfig]:
        return Config


