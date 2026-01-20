# chatGPT 
[![Chat on Matrix](https://img.shields.io/badge/chat_on_matrix-%23dev:mssj.me-green)](https://matrix.to/#/#dev:mssj.me)

maubot plugin to allow your maubot instance to return queries from any openAI-compatible API endpoints.

## Usage

1. Create the bot instance in Maubot Manager, add your openAI API key to the config, and modify as you see fit. Note
   that you can change the API to point at any backend service that is openAI-API compatible and this should work
   just fine, just be sure to restart the plugin (full stop then start) if you change it.
2. Invite the bot to a room.
3. Ping the bot by mentioning it by name.

## Features

See `base-config.yaml` for more details.

* Tested with openAI, local.ai, llamafile, Digital Ocean agent inference endpoints, and DeepSeek. Even more may work!
* Uses the most recent messages in the room for context, or replies to queries in threads to keep conversations isolated
* Multi-user awareness:
  * Option to prefix every context message with the display name of the sender, and adds a system prompt informing the model of that fact. this increases personal data shared with openAI as well as your used token count, but makes the bot work a little better in multi-person chat rooms
* Configurable system prompt and option to pre-append messages to the context
