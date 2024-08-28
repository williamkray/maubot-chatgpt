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


    async def should_respond(self, event: MessageEvent) -> bool:
        """ Determine if we should respond to an event """

        if (event.sender == self.client.mxid or  # Ignore ourselves
                event.content.body.startswith('!') or # Ignore commands
                event.content['msgtype'] != MessageType.TEXT or  # Don't respond to media or notices
                event.content.relates_to['rel_type'] == RelationType.REPLACE):  # Ignore edits
            return False

        # Check if the message contains the bot's ID
        if re.search("(^|\s)(@)?" + self.name + "([ :,.!?]|$)", event.content.body, re.IGNORECASE):
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
            # strip off extra colons which the model seems to keep adding no matter how
            # much you tell it not to
            content = re.sub('^\w*\:+\s+', '', content)
            return str(content)

    @command.new(name='gpt', help='control chatGPT functionality', require_subcommand=True)
    async def gpt(self, evt: MessageEvent) -> None:
        pass


    async def get_context(self, event: MessageEvent):

        system_context = deque()
        timestamp = datetime.today().strftime('%Y-%m-%d %H:%M:%S')
        system_prompt = {"role": "system",
                         "content": self.config['system_prompt'].format(name=self.name, timestamp=timestamp)}
        if self.config['enable_multi_user']:
            system_prompt["content"] += """
User messages are in the context of multiperson chatrooms.
Each message indicates its sender by prefixing the message with the sender's name followed by a colon, for example:
"username: hello world."
In this case, the user called "username" sent the message "hello world.". You should not follow this convention in your responses.
your response instead could be "hello username!" without including any colons, because you are the only one sending your responses there is no need to prefix them.
"""
        system_context.append(system_prompt)

        addl_context = json.loads(json.dumps(self.config['addl_context']))
        if addl_context:
            for item in addl_context:
                system_context.append(item)
            if len(addl_context) > self.config["max_context_messages"] - 1:
                raise ValueError(f"sorry, my configuration has too many additional prompts "
                                 f"({self.config['max_context_messages']}) and i'll never see your message. "
                                    f"Update my config to have fewer messages and i'll be able to answer your questions!")


        chat_context = deque()
        word_count = sum([len(m["content"].split()) for m in system_context])
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
            user = ''
            if self.config['enable_multi_user']:
                user = (await self.client.get_displayname(next_event.sender) or \
                            self.client.parse_user_id(next_event.sender)[0]) + ": "

            word_count += len(message.split())
            message_count += 1
            if word_count >= self.config['max_words'] or message_count >= self.config['max_context_messages']:
                break

            chat_context.appendleft({"role": role, "content": user + message})

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


