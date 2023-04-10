# chatGPT 

maubot plugin to allow your maubot instance to return queries from openAI GPT API endpoints. 

add your openAI API key to the config, and modify as you see fit. if you don't know what the options are, you
probably shouldn't be using this. please refer to the openai documentation.

be warned, this plugin keeps a cache of the last 10 messages it has seen across all rooms, and submits the ones most
recently sent in the current room to provide additional context to the conversational API endpoint. this means you can
have reasonable success with contextual follow-up questions, but may be seen as a significant security leak for
private rooms, and you may be using significantly more tokens than you think with each request. use this bot responsibly.

