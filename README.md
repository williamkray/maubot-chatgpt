# Deepseek Bot 

Maubot plugin to allow your maubot instance to return queries from deepseek API endpoints. A _barely_ modified fork of [williamkray's ChatGPT plugin.](https://github.com/williamkray/maubot-chatgpt).

## Usage

1. Create the bot instance in Maubot Manager, add your deepseek API key to the config, and modify as you see fit.
2. Invite the bot to a room.
3. Ping the bot by mentioning it by name.

## Features

See `base-config.yaml` for more details.

* Uses the most recent messages in the room for context, or replies to queries in threads to keep conversations isolated
* Multi-user awareness:
  * Option to prefix every context message with the display name of the sender, and adds a system prompt informing the model of that fact. this increases personal data shared with openAI as well as your used token count, but makes the bot work a little better in multi-person chat rooms
* Configurable system prompt and option to pre-append messages to the context
