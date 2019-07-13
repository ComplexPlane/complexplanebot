#!/usr/bin/env python3

import requests

twitter = 'http://twitter.com/ComplexPlaneRun/status/1125411285531807744'

response = requests.post(url, json={'content': twitter})
print(response.json())
