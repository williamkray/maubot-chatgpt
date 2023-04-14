import asyncio
import json
import os
import re
from datetime import datetime

from typing import Type, Deque, Dict
from mautrix.client import Client
from collections import deque, defaultdict
from maubot.handlers import command, event
from maubot import Plugin, MessageEvent
from mautrix.errors import MNotFound, MatrixRequestError
from mautrix.types import TextMessageEventContent, EventType, RoomID, UserID
from mautrix.util.config import BaseProxyConfig, ConfigUpdateHelper

GPT_API_URL = "https://api.openai.com/v1/chat/completions"
EVENT_CACHE_LENGTH = 10


class Config(BaseProxyConfig):
    def do_update(self, helper: ConfigUpdateHelper) -> None:
        helper.copy("gpt_api_key")
        helper.copy("model")
        helper.copy("max_tokens")
        helper.copy("enable_multi_user")
        helper.copy("system_prompt")
        helper.copy("name")
        helper.copy("allowed_users")
        helper.copy("addl_context")

class GPTPlugin(Plugin):

    prev_room_events: Dict[RoomID, Deque[Dict]]

    async def start(self) -> None:
        await super().start()
        self.config.load_and_update()
        self.name = self.config['name'] if self.config['name'] else self.client.parse_user_id(self.client.mxid)[0]
        self.log.debug(f"DEBUG gpt plugin started with bot name: {self.name}")
        self.prev_room_events = defaultdict(lambda: deque(maxlen=EVENT_CACHE_LENGTH))

    @event.on(EventType.ROOM_MESSAGE)
    async def on_message(self, event: MessageEvent) -> None:
        role = ''
        user = ''
        content = ''
        timestamp = datetime.today().strftime('%Y-%m-%d %H:%M:%S')

        if event.sender == self.client.mxid:
            role = 'assistant'
        else:
            role = 'user'
            if self.config['enable_multi_user']:
                user = self.client.parse_user_id(event.sender)[0] + ': ' # only use the localpart

        # keep track of all messages, even if the bot sent them
        self.prev_room_events[event.room_id].append({"role": role , "content": 
                                                     user + event['content']['body'].lower()})

        # if the bot sent the message or another command was issued, just pass
        if event.sender == self.client.mxid or event.content.body.startswith('!'):
            return

        joined_members = await self.client.get_joined_members(event.room_id)

        try:
            # Check if the message contains the bot's ID
            match_name = re.search("(^|\s)(@)?" + self.name + "([ :,.!?]|$)", event.content.body, re.IGNORECASE)
            if match_name or len(joined_members) == 2:
                if len(self.config['allowed_users']) > 0 and event.sender not in self.config['allowed_users']:
                    await event.respond("sorry, you're not allowed to use this functionality.")
                    return

                prompt = self.config['system_prompt'].format(name=self.name, timestamp=timestamp)
                system_prompt = {"role": "system", "content": prompt}

                await event.mark_read()
                
                context = self.prev_room_events.get(event.room_id, [])
                # if our short history is already at max capacity, drop the oldest messages
                # to make room for our more important system prompt(s)
                addl_context = json.loads(json.dumps(self.config['addl_context']))
                # full prompt count is number of messages provided in config, plus system prompt
                prompt_count = len(addl_context) + 1

                # too many prompts? that's a problem, just bomb out.
                # we'll always want to save the last message in the cache because that's our prompt
                if prompt_count > EVENT_CACHE_LENGTH - 1:
                    await event.respond("sorry, my configuration has too many prompts and i'll never see your message.\
                                        update my config to have fewer messages and i'll be able to answer your\
                                        questions!")
                    return

                # find out how many spots we need to open up to prepend our prompts
                if prompt_count >= EVENT_CACHE_LENGTH - len(context):
                    for c in range(1, prompt_count):
                        context.popleft()

                for m in addl_context:
                    context.appendleft(addl_context.pop())
                context.appendleft(system_prompt)

                #self.log.debug(f"CONTEXT: {context}")

                # Call the chatGPT API to get a response
                await self.client.set_typing(event.room_id, timeout=99999)
                response = await self._call_gpt(context)
                
                # Send the response back to the chat room
                await self.client.set_typing(event.room_id, timeout=0)
                await event.respond(f"{response}")
        except Exception as e:
            self.log.error(f"Something went wrong: {e}")
            pass

    async def _call_gpt(self, prompt):
        full_context = []
        if self.config['enable_multi_user']:
            full_context.append({'role': 'system', 'content': 'user messages are in the context of\
                    multiperson chatrooms. each message indicates its sender by prefixing\
                    the message with the sender\'s name followed by a colon, such as this example:\
                    \
                    "username: hello world."\
                    \
                    in this case, the user called "username" sent the\
                    message "hello world.". you should not follow this convention in your responses.\
                    your response instead could be "hello username!" without including any colons,\
                    because you are the only one sending your responses there is no need to prefix them.'})

        full_context.extend(list(prompt))
        headers = {
            "Content-Type": "application/json",
            "Authorization": f"Bearer {self.config['gpt_api_key']}"
        }
        data = {
            "model": self.config['model'],
            "messages": full_context,
            "max_tokens": self.config['max_tokens']
        }
        
        async with self.http.post(
            GPT_API_URL, headers=headers, data=json.dumps(data)
        ) as response:
            if response.status != 200:
                return f"Error: {await response.text()}"
            response_json = await response.json()
            content = response_json["choices"][0]["message"]["content"]
            # strip off extra colons which the model seems to keep adding no matter how
            # much you tell it not to
            content = re.sub('^\w?\:+\s+', '', content)
            #self.log.debug(content)
            return content

    @command.new(name='gpt', help='control chatGPT functionality', require_subcommand=True)
    async def gpt(self, evt: MessageEvent) -> None:
        pass

    @gpt.subcommand("clear", help="clear the cache of context and return the bot to its original system prompt")
    async def clear_cache(self, evt: MessageEvent) -> None:
        self.prev_room_events.pop(evt.room_id)
        await evt.react('✅')


    @classmethod
    def get_config_class(cls) -> Type[BaseProxyConfig]:
        return Config


