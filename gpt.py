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
        helper.copy("system_prompt")
        helper.copy("name")
        helper.copy("allowed_users")

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
        content = ''
        timestamp = datetime.today().strftime('%Y-%m-%d %H:%M:%S')

        if event.sender == self.client.mxid:
            role = 'assistant'
        else:
            role = 'user'

        # keep track of all messages, even if the bot sent them
        self.prev_room_events[event.room_id].append({"role": role , "content": event['content']['body'].lower()})

        # if the bot sent the message or another command was issued, just pass
        if event.sender == self.client.mxid or event.content.body.startswith('!'):
            return

        joined_members = await self.client.get_joined_members(event.room_id)

        try:
            # Check if the message contains the bot's ID
            match_name = re.search("(^|\s)(@)?" + self.name + "(\s|\,|(\?)?$)", event.content.body, re.IGNORECASE)
            if match_name or len(joined_members) == 2:
                if len(self.config['allowed_users']) > 0 and event.sender not in self.config['allowed_users']:
                    await event.respond("sorry, you're not allowed to use this functionality.")
                    return

                prompt = self.config['system_prompt'].format(name=self.name, timestamp=timestamp)
                system_prompt = {"role": "system", "content": prompt}
                self.log.debug(f"DEBUG gpt plugin system prompt set to: {system_prompt}")

                await event.mark_read()
                
                context = self.prev_room_events.get(event.room_id, [])
                # if our short history is already at max capacity, drop the oldest message
                # to make room for our more important system prompt
                if len(context) == EVENT_CACHE_LENGTH:
                    context.popleft()

                context.appendleft(system_prompt)

                # Call the chatGPT API to get a response
                await self.client.set_typing(event.room_id, timeout=9999)
                response = await self._call_gpt(context)
                
                # Send the response back to the chat room
                await self.client.set_typing(event.room_id, timeout=0)
                await event.respond(f"{response}")
        except Exception as e:
            self.log.error(f"Something went wrong: {e}")
            pass

    async def _call_gpt(self, prompt):
        headers = {
            "Content-Type": "application/json",
            "Authorization": f"Bearer {self.config['gpt_api_key']}"
        }
        data = {
            "model": self.config['model'],
            "messages": list(prompt),
            "max_tokens": self.config['max_tokens']
        }
        
        async with self.http.post(
            GPT_API_URL, headers=headers, data=json.dumps(data)
        ) as response:
            if response.status != 200:
                return f"Error: {await response.text()}"
            response_json = await response.json()
            return response_json["choices"][0]["message"]["content"]


    @classmethod
    def get_config_class(cls) -> Type[BaseProxyConfig]:
        return Config


