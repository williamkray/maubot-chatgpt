#!/usr/bin/env bash

docker run --rm \
  -v "$PWD"/data:/data \
  -v "$PWD":/opt/chatgpt \
  --user $UID:$GID \
  --name gpt \
  dock.mau.dev/maubot/maubot:standalone \
  python3 -m maubot.standalone \
    -m /opt/chatgpt/maubot.yaml \
    -c /data/config.yaml \
    -b /opt/chatgpt/base-standalone-config.yaml

