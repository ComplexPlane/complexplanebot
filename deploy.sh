#!/usr/bin/env bash

fname=twitchbot.py

ssh aws "mkdir -p tmp && tee tmp/$fname >/dev/null && sudo install -o twitchbot tmp/$fname /home/twitchbot/twitchbot/$fname" <$fname
