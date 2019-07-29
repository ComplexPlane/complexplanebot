import collections
import time
import re
import socket
import ssl
import time
import traceback
import heapq

from .secret import secret
from .leaderboards import *
from .exn import NetworkError, GetError


"""
TODO:
- ties
- Separate bot backend for Discord and Twitch, with core of same commands?
- quotes
- parallel channel join
"""


SERVER = 'irc.chat.twitch.tv'
PORT = 6697
BOT_NAME = 'complexplanebot'

RECONNECT_TIME = 5
SOCKET_TIMEOUT = 1
PINGPONG_INTERVAL = 60
PINGPONG_TIMEOUT = 5

MY_CHANNEL = 'complexplane'
FRIEND_CHANNELS = {BOT_NAME, 'alist_', 'stevencw_', 'petresinc', 'monkeyballspeedruns'}

PING_MSG = 'PING :tmi.twitch.tv'
PONG_MSG = 'PONG :tmi.twitch.tv'

Timer = collections.namedtuple('Timer', ['interval', 'func'])


class Bot:
    def __init__(self):
        self.ssock = None
        self.joined_channels = None
        self.ping_pending = False
        self.recv_queue = collections.deque()
        self.timer_pqueue = []
        # How many times has each user tried to timeout someone else?
        self.user_timeouts = collections.defaultdict(int)
        self.init_timers()


    def loop(self):
        while True:
            try:
                self.connect()
                self.provide_chatbot()

            except NetworkError as e:
                print(f'Network error: {e.msg}')
                if e.exn is not None:
                    print(e.exn)
                self.ssock.close()

                print(f'Reconnecting in {RECONNECT_TIME} seconds')
                time.sleep(RECONNECT_TIME)

            except Exception as e:
                print(e)
                # Continue trying to function until I see the log...


    def provide_chatbot(self):
        while True:
            msg = self.recv_raw()
            if msg is None:
                self.handle_timers()

            elif msg == PING_MSG:
                self.send_raw(PONG_MSG)

            elif msg == PONG_MSG:
                self.ping_pending = False

            else:
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


    def init_timers(self):
        def social():
            self.handle_commands(BOT_NAME, MY_CHANNEL, '!social')
        def bot():
            self.handle_commands(BOT_NAME, MY_CHANNEL, '!bot')
        def src():
            self.handle_commands(BOT_NAME, MY_CHANNEL, '!issrcdown')

        self.add_timer_interval(90 * 60, social)
        self.add_timer_interval(80 * 60, bot)
        self.add_timer_interval(50 * 60, src)
        self.add_timer_interval(PINGPONG_INTERVAL, self.ping_server)


    def ping_server(self):
        self.send_raw('PING')
        self.ping_pending = True

        def check_for_pong():
            if self.ping_pending:
                raise NetworkError('Failed to ping server, must be disconnected')

        self.add_timer_oneshot(PINGPONG_TIMEOUT, check_for_pong)


    def add_timer_oneshot(self, t, func):
        due_time = time.time() + t
        heapq.heappush(self.timer_pqueue, Timer(due_time, func))


    def add_timer_interval(self, t, func):
        def readd():
            func()
            self.add_timer_oneshot(t, readd)

        self.add_timer_oneshot(t, readd)


    def handle_timers(self):
        while len(self.timer_pqueue) > 0 and self.timer_pqueue[0][0] <= time.time():
            _, task_func = heapq.heappop(self.timer_pqueue)
            task_func()


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
            send_msg('I am a Twitch bot written in Python 3 by ComplexPlane. For a full list of commands: https://git.io/fj2gV')

        elif cmd == 'wr':
            self.handle_commands(user, channel, '!1st')

        elif cmd in ['social', 'links'] and channel == MY_CHANNEL:
            send_msg('Twitter: https://twitter.com/ComplexPlaneRun')
            self.add_timer_oneshot(1, lambda: send_msg('Discord: https://discord.gg/nJWndP5'))
            self.add_timer_oneshot(2, lambda: send_msg('Youtube: https://bit.ly/2GbXGlD'))
            self.add_timer_oneshot(3, lambda: send_msg('Speedrun.com: https://bit.ly/2NSTbCI'))
            self.add_timer_oneshot(4, lambda: send_msg('Monkey Ball Community Discord: https://discord.gg/4TVgGkx'))
            self.add_timer_oneshot(5, lambda: send_msg('Monkey Ball RTA-Focused Discord: https://discord.gg/N8N8Njc'))

        elif cmd == 'schedule' and channel == MY_CHANNEL:
            send_msg("I don't have a schedule currently.")

        elif cmd == 'twitter' and channel == MY_CHANNEL:
            send_msg('Twitter: https://twitter.com/ComplexPlaneRun')

        elif cmd == 'discord' and channel == MY_CHANNEL:
            send_msg('Discord: https://discord.gg/nJWndP5')

        elif cmd == 'src' and channel == MY_CHANNEL:
            send_msg('Speedrun.com: https://bit.ly/2NSTbCI')

        elif cmd == 'gaming' and channel == MY_CHANNEL:
            send_msg('https://clips.twitch.tv/YummyTenuousMouseCharlieBitMe')

        elif cmd == 'slideintodms' and channel == MY_CHANNEL:
            send_msg(f'/w {user} heyyy ;)')

        elif cmd in ['rank', 'pb']:
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
            self.ping_pending = False

            raw_sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
            ssl_context = ssl.create_default_context()
            self.ssock = ssl_context.wrap_socket(raw_sock, server_hostname=SERVER)
            self.ssock.settimeout(SOCKET_TIMEOUT)

            # Login to the server
            print(f'Logging into {SERVER}:{PORT}')
            self.ssock.connect((SERVER, PORT))
            self.send_raw(f'USER {BOT_NAME} {BOT_NAME} {BOT_NAME}')
            self.send_raw(f'PASS {secret.CLIENT_TOKEN}', hide=True)
            self.send_raw(f'NICK {BOT_NAME}')

            self.joined_channels = set()
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
                if received == '':
                    raise NetworkError('Connection closed')

                self.recv_queue.extend(received)
                for line in received:
                    if hide:
                        print(f'Received:  {"*" * len(line)}')
                    else:
                        print(f'Received:  {line}')

            except socket.timeout:
                return None

            except Exception as e:
                raise NetworkError('Error during receive attempt', e)

        return self.recv_queue.popleft()
