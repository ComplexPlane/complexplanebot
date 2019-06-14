#!/usr/bin/env python3

import collections
import datetime
import re
import socket
import ssl
import time
import traceback
import urllib
import requests
from secret import secret

SERVER = 'irc.chat.twitch.tv'
PORT = 6697
BOT_NAME = 'complexplanebot'
RECONNECT_TIME = 10

MY_CHANNEL = 'complexplane'
FRIEND_CHANNELS = {BOT_NAME, 'alist_', 'stevencw_', 'petresinc'}

# URI for the Story Mode All Levels (NTSC) leaderboard
SMAL_VAR = 'wl3vv981'
SMAL_VAL = '5q8kgmyq'
SMAL_URI = 'https://www.speedrun.com/api/v1/leaderboards/nd2ervd0/category/zd3l7ydn?var-wl3vv981=5q8kgmyq'

"""
TODO:
- timers (stay hydrated? speedrun.com up check? social?)
- ties
- Async
- Periodic reminders of bot's existence
- Declarative commands and permissions?
- Separate secret into secret folder, then gitignore
"""

def safe_get_json(uri, valid404=False):
    try:
        response = requests.get(uri, timeout=2)
        if valid404 and response.status_code == 404:
            return None
        response.raise_for_status()
        return response.json()
    except requests.exceptions.RequestException:
        site = urllib.parse.urlparse(uri).netloc
        raise GetError(f'Failed to communicate with {site}, is it down?')

class NetworkError(Exception):
    def __init__(self, msg, exn):
        self.msg = msg
        self.exn = exn

class GetError(Exception):
    def __init__(self, msg):
        self.msg = msg

def place_to_rank_index(place):
    rank_regex = '^([0-9]+)([a-zA-Z]+)$'
    match = re.match(rank_regex, place)
    if match is None:
        return None

    rank = int(match.group(1))
    suffix = match.group(2)
    if rank == 0:
        return None

    if rank % 10 == 1 and rank != 11:
        if suffix != 'st':
            return None
    elif rank % 10 == 2 and rank != 12:
        if suffix != 'nd':
            return None
    elif rank % 10 == 3 and rank != 13:
        if suffix != 'rd':
            return None
    elif suffix != 'th':
        return None

    return rank - 1

def rank_index_to_place(rank_index):
    rank = rank_index + 1
    if rank < 1:
        return None

    if rank % 10 == 1:
        if rank == 11:
            return f'{rank}th'
        return f'{rank}st'

    if rank % 10 == 2:
        if rank == 12:
            return f'{rank}th'
        return f'{rank}nd'

    if rank % 10 == 3:
        if rank == 13:
            return f'{rank}th'
        return f'{rank}rd'

    return f'{rank}th'


RunInfo = collections.namedtuple('RunInfo', ['player', 'location', 'date', 'duration', 'place_str'])


def speedrun_com_run_info(run):
    player_uri = run['run']['players'][0]['uri']
    player_json = safe_get_json(player_uri)
    player_name = player_json['data']['names']['international']

    try:
        player_location = player_json['data']['location']['region']['names']['international']
    except (KeyError, TypeError):
        try:
            player_location = player_json['data']['location']['country']['names']['international']
        except (KeyError, TypeError):
            player_location = 'unknown location'

    date_recorded = run['run']['date']
    place_str = rank_index_to_place(run['place'] - 1)

    time_sec = run['run']['times']['primary_t']
    time_str = str(datetime.timedelta(seconds=time_sec))
    if time_str.startswith('0:'):
        time_str = time_str[2:]

    return RunInfo(player=player_name, location=player_location, date=date_recorded, duration=time_str, place_str=place_str)

def leaderboards_rank_lookup(cmd):
    rank_index = place_to_rank_index(cmd)
    if rank_index is None:
        return None

    smal_json = safe_get_json(SMAL_URI)

    runs = smal_json['data']['runs']
    place = rank_index_to_place(rank_index)
    if len(runs) - 1 < rank_index:
        return f'Sorry, there is nobody in {place} place.'
    run = runs[rank_index]

    run_info = speedrun_com_run_info(run)

    if rank_index == 0:
        place_text = 'world record'
    else:
        place_text = f'{place} place record'

    return f'The {place_text} for Super Monkey Ball 2: Story Mode All Levels is {run_info.duration} by {run_info.player}, set on {run_info.date}. {run_info.player} is from {run_info.location}.'

def leaderboards_user_lookup(user):
    if user == '':
        return 'Please provide a valid speedrun.com username to lookup.'
    if re.match(r'^\w+$', user) is None:
        return f'Invalid username: {user}'

    pbs = safe_get_json(f'https://www.speedrun.com/api/v1/users/{user}/personal-bests', valid404=True)
    if pbs is None:
        return f'User {user} does not exist on speedrun.com.'

    for pb in pbs['data']:
        # We can identify the SMAL run just by checking the "All Levels" variable IDs
        if SMAL_VAR in pb['run']['values'] and pb['run']['values'][SMAL_VAR] == SMAL_VAL:
            run_info = speedrun_com_run_info(pb)
            return f'{user} has {run_info.place_str} place in SMB2 SMAL, with a time of {run_info.duration}. It was set on {run_info.date}.'
    else:
        return f'{user} has not submitted a SMB2 SMAL time to the speedrun.com leaderboards.'

def leaderboards_latest_run():
    smal_json = safe_get_json(SMAL_URI)

    latest_date = None
    latest_run = None
    for run in smal_json['data']['runs']:
        date_str = run['run']['date']
        if date_str is None:
            continue
        year, month, day = tuple(map(int, date_str.split('-')))
        date = datetime.date(year, month, day)
        if latest_date is None or date > latest_date:
            latest_date = date
            latest_run = run
    if latest_date is None:
        return 'No runs??'

    run_info = speedrun_com_run_info(latest_run)
    return f'The leaderboard\'s latest SMB2 SMAL run was submitted on {run_info.date} by {run_info.player}, with a time of {run_info.duration} ({run_info.place_str}). {run_info.player} is from {run_info.location}.'

def leaderboards_upcheck():
    try:
        safe_get_json(SMAL_URI)
        return 'speedrun.com appears to be UP.'
    except GetError:
        return 'speerdun.com appears to be DOWN.'

class Bot:
    def __init__(self):
        self.raw_sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        ssl_context = ssl.create_default_context()
        self.ssock = ssl_context.wrap_socket(self.raw_sock, server_hostname=SERVER)

        self.recv_queue = collections.deque()

        # How many times has each user tried to timeout someone else?
        self.user_timeouts = collections.defaultdict(int)

        self.joined_channels = set()

    def loop(self):
        while True:
            try:
                self.provide_chatbot()

            except NetworkError as e:
                print(f'Network error: {e.msg}')
                print(e.exn)

                print(f'Reconnecting in {RECONNECT_TIME} seconds')
                time.sleep(RECONNECT_TIME)

            except Exception as e:
                print(e)
                # Continue trying to function until I see the log...

    def provide_chatbot(self):
        self.connect()

        while True:
            msg = self.recv_raw()

            ping = 'PING :'
            if msg.startswith(ping):
                tail = msg[len(ping):]
                self.send_raw(f'PONG :{tail}')
                continue

            chat_regex = r'^:(\w+)!(\w+)@([^ ]+) PRIVMSG #(\w+) :(.+)'
            match = re.match(chat_regex, msg)
            if match is None:
                continue

            user, channel, message = match.group(1, 4, 5)
            if user == BOT_NAME:
                continue

            try:
                if channel == MY_CHANNEL:
                    self.handle_porter(user, channel, message)
                self.handle_commands(user, channel, message)

            except GetError as e:
                self.send_msg(channel, e.msg)
            except Exception as e:
                trace = traceback.format_exc()
                print(trace)
                irc_trace = trace.replace('\n', ' ')
                self.send_msg(channel, f'Oops!! {irc_trace}')

    def handle_porter(self, user, channel, message):
        porter_references = [
            'porter',
            'robinson',
            'shelter',
            'sad machine',
            'goodbye to a world',
            'goodbye world',
            'lionhearted',
            'sea of voices',
            'divinity',
            'fellow feeling',
            'flicker',
            'fresh static snow',
            'language',
            'years of war',
            'she heals everything',
            'say my name',
            'hear the bells',
            'polygon dust',
            'shepherdess',
            'natural light',
            'the thrill',
            'madeon',
            'anamanaguchi',
            'kero kero bonito',
        ]

        inner_re = '|'.join(porter_references).lower()
        phrases_re = r'(^|\W)({})($|\W)'.format(inner_re)
        if re.match(phrases_re, message.lower()):
            self.send_msg(channel, '【=◈︿◈=】')

    def handle_timeout(self, channel, user, args):
        if channel != MY_CHANNEL:
            return

        if args == '':
            self.send_msg(channel, 'Please specify a user to timeout.')
            return

        target_user_match = re.match(r'^\w+$', args)
        if target_user_match is None:
            self.send_msg(channel, f'Invalid username to timeout: {args}')
            return
        target_user = target_user_match.group(0)

        TARGET_TIMEOUT = 5
        USER_TIMEOUT = 30

        if self.user_timeouts[user] % 3 == 0:
            self.send_msg(channel, f'/timeout {target_user} {TARGET_TIMEOUT}')
            self.send_msg(channel, f'User {target_user} timed out for {TARGET_TIMEOUT} seconds.')
        else:
            self.send_msg(channel, f'/timeout {user} {USER_TIMEOUT}')
            self.send_msg(channel, f'User {user} timed out for {USER_TIMEOUT} seconds.')

        self.user_timeouts[user] += 1

    def handle_msg_command(self, channel, user, args):
        if channel != MY_CHANNEL:
            return

        parsed = re.match(r'^(\w+) +(.*)$', args)
        if parsed is None:
            self.send_msg(channel, f'Usage example to send a message to someone else\'s stream: !msg alist_ Yo Alist, get over here')
            return

        target_channel, msg = parsed.group(1, 2)

        self.join_channel(target_channel)
        self.send_msg(target_channel, f'{user} says: {msg}')
        self.send_msg(channel, 'Message sent.')

    def handle_commands(self, user, channel, message):
        def send_msg(msg):
            self.send_msg(channel, msg)

        cmd_match = re.match(r'^!([^ ]+)(.*)', message)
        if not cmd_match:
            return

        cmd, args = cmd_match.group(1, 2)
        args = args.strip()

        # TODO replace with hashmap if it gets too big
        if (cmd in ['bot', 'help'] and channel == MY_CHANNEL) or cmd == 'complexplanebot':
            send_msg('I am a Twitch bot written in Python 3 by ComplexPlane. Usage: https://git.io/fj2gV')

        elif cmd == 'wr':
            self.handle_commands(user, channel, '!1st')

        elif cmd in ['social', 'links'] and channel == MY_CHANNEL:
            send_msg(
                'Twitter: https://twitter.com/ComplexPlaneRun, '
                'Youtube: https://www.youtube.com/channel/UC88mFCk6NkbPXFfkjzOzimQ/featured, '
                'Monkey Ball RTA-Focused Discord: https://discord.gg/N8N8Njc, '
                'Discord Username: ComplexPlane#3111, '
                'Speedrun.com: https://www.speedrun.com/user/ComplexPlane'
            )

        elif cmd == 'schedule' and channel == MY_CHANNEL:
            send_msg("I don't have a schedule currently.")

        elif cmd == 'twitter' and channel == MY_CHANNEL:
            send_msg('Twitter: https://twitter.com/ComplexPlaneRun')

        elif cmd == 'discord' and channel == MY_CHANNEL:
            send_msg("I might want to have a discord eventually, but I haven't thought of any good channels and stuff for it yet.")

        elif cmd == 'src' and channel == MY_CHANNEL:
            send_msg('Speedrun.com: https://www.speedrun.com/user/ComplexPlane')

        elif cmd == 'gaming' and channel == MY_CHANNEL:
            send_msg('https://clips.twitch.tv/YummyTenuousMouseCharlieBitMe')

        elif cmd == 'slideintodms' and channel == MY_CHANNEL:
            send_msg(f'/w {user} heyyy ;)')

        elif cmd == 'rank':
            send_msg(leaderboards_user_lookup(args))

        elif cmd == 'latest':
            send_msg(leaderboards_latest_run())

        elif cmd == 'issrcdown':
            send_msg(leaderboards_upcheck())

        elif cmd == 'pausing':
            send_msg('Pause strats are a way to perform perfectly precise movement on a stage. In Monkey Ball, there is zero RNG; if we provide exactly the same inputs on the control stick on exactly the same frames on a level, exactly the same thing will happen. To perform a pause strat, you hold the control stick in an exact direction (thanks to the Gamecube controller\'s notches), pause on a specific frame (using the timer as a reference), and repeat.')

            send_msg('Often we will pause slightly before the intended frame and then press B quickly followed by Pause to advance a small number of frames until the desired frame is reached. Pausing quickly and frame-perfectly is tricky to do consistently, so many pause strats include "backup frames" as well.')

        elif cmd == 'boosting':
            send_msg('Switching between up-left and up-right can change your momentum in certain circumstances. Boosting once at the start of a level ("frame boosting") or into angled walls ("wall boosting") can give you a speed boost. Boosting in mid-air can keep you in the air for slightly longer ("air boosting").')

        elif cmd == 'firstframe':
            send_msg('The game does not consider the stage completed until the third frame after breaking the goaltape. Leaving the stage with "Stage Select" on the first two frames results in a "first frame".')

        elif cmd == 'walls':
            send_msg('For many kinds of walls, wall boosting gives an inconsistent amount of speed. Sometimes you can smoothly roll off of them, sometimes you can just bonk and gain less speed. This inconsistency can make certain strats not RTA-viable.')

        elif cmd == 'alisters':
            send_msg('Alisters Discord: https://discord.gg/N8N8Njc')

        elif cmd == 'smh' and channel == MY_CHANNEL:
            send_msg(f'Hi, my name is {user} and you should follow me at twitch.tv/{user}  I\'m an epic speedrunner and MUCH better than this lowly gamer!!')

        elif cmd == 'timeout':
            self.handle_timeout(channel, user, args)

        elif cmd == 'msg':
            self.handle_msg_command(channel, user, args)

        elif channel == MY_CHANNEL and cmd == 'surgery':
            send_msg('https://www.youtube.com/watch?v=DywNCzt_ky8')

        else:
            leaderboards_msg = leaderboards_rank_lookup(cmd)
            if leaderboards_msg:
                send_msg(leaderboards_msg)
            elif channel == MY_CHANNEL:
                send_msg(f'!{cmd}: unrecognized command :(')

    def connect(self):
        try:
            # Login to the server
            print(f'Logging into {SERVER}:{PORT}')
            self.ssock.connect((SERVER, PORT))
            self.send_raw(f'USER {BOT_NAME} {BOT_NAME} {BOT_NAME}')
            self.send_raw(f'PASS {secret.CLIENT_TOKEN}', hide=True)
            self.send_raw(f'NICK {BOT_NAME}')

            self.join_channel(MY_CHANNEL)
            for friend in FRIEND_CHANNELS:
                self.join_channel(friend)

        except Exception as e:
            raise NetworkError(f'Error connecting to {SERVER}:{PORT}', e)

    def join_channel(self, channel):
        # TODO unrelated messages are dropped; this is difficult to handle synchronously due to
        # join recursion issues
        joined_re = r'^:{}\.tmi\.twitch\.tv \d+ {} #{} :End of /NAMES list$'.format(
            BOT_NAME, BOT_NAME, channel)

        if channel not in self.joined_channels:
            self.send_raw(f'JOIN #{channel}')
            while re.match(joined_re, self.recv_raw()) is None:
                pass

            self.joined_channels.add(channel)

    """ Send a message to the channel """
    def send_msg(self, channel, msg):
        MAX_LEN = 500
        if len(msg) > MAX_LEN:
            msg = msg[:MAX_LEN - 3] + '...'
        self.send_raw(f'PRIVMSG #{channel} :{msg}')

    def send_raw(self, msg, hide=False):
        try:
            self.ssock.send(bytes(msg + '\n', 'UTF-8'))
            if hide:
                msg = '*' * len(msg)
            print(f'Sent:      {msg}')

        except Exception as e:
            raise NetworkError(f'Error sending message {msg}', e)

    def recv_raw(self, hide=False):
        if len(self.recv_queue) == 0:
            try:
                received = self.ssock.recv(2040).decode('UTF-8').strip().split('\n')
                self.recv_queue.extend(received)
                for line in received:
                    if hide:
                        print(f'Received:  {"*" * len(line)}')
                    else:
                        print(f'Received:  {line}')

            except Exception as e:
                raise NetworkError('Error during receive attempt', e)

        return self.recv_queue.popleft()

if __name__ == '__main__':
    Bot().loop()
