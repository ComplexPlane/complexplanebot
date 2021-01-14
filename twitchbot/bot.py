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
- Periodically choose a command at random and suggest it
- Timer cancellation
- Rewrite in Rust lol

Refactoring:
- Aggregate constants
- Splits commands into separate function for exported and non-exported commands
    - Might want to do this at the same time as splitting "commands" from "complexplanebot IRC client"
"""

SERVER = 'irc.chat.twitch.tv'
PORT = 6697
BOT_CHANNEL = 'complexplanebot'

RECONNECT_TIME = 5
SOCKET_TIMEOUT = 1
PINGPONG_INTERVAL = 60
PINGPONG_TIMEOUT = 5

MY_CHANNEL = 'complexplane'
FRIEND_CHANNELS = {BOT_CHANNEL, 'alist_', 'stevencw_', 'petresinc', 'monkeyballspeedruns'}

PING_MSG = 'PING :tmi.twitch.tv'
PONG_MSG = 'PONG :tmi.twitch.tv'

TIMEOUT_DISABLE_HOURS = 18

Timer = collections.namedtuple('Timer', ['interval', 'func'])


class Bot:
    def __init__(self):
        self.ssock = None
        self.joined_channels = None
        self.ping_pending = False
        self.timeout_cmd_enabled = True
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
                if user == BOT_CHANNEL:
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
            'your wish',
        ]

        inner_re = '|'.join(porter_references).lower()
        phrases_re = r'(^|\W)({})($|\W)'.format(inner_re)
        if re.search(phrases_re, message.lower()):
            self.send_msg(channel, '【=◈︿◈=】')

    def handle_timeout(self, channel, user, args):
        if channel != MY_CHANNEL:
            return

        if not self.timeout_cmd_enabled:
            self.send_msg(channel, 'Free-for-all timeouts are currently disabled. Try again tomorrow.')
            return

        if args == '':
            self.send_msg(channel, 'Please specify a user to timeout.')
            return

        target_user_match = re.match(r'^\w+$', args)
        if target_user_match is None:
            self.send_msg(channel, f'Invalid username to timeout: {args}')
            return
        target_user = target_user_match.group(0)

        OTHER_USER_TIMEOUT = 5
        CURRENT_USER_TIMEOUT = 30

        if self.user_timeouts[user] % 3 == 0:
            self.send_msg(channel, f'/timeout {target_user} {OTHER_USER_TIMEOUT}')
            self.send_msg(channel, f'{user} timed out {target_user} for {OTHER_USER_TIMEOUT} seconds.')
        else:
            self.send_msg(channel, f'/timeout {user} {CURRENT_USER_TIMEOUT}')
            self.send_msg(channel, f'{user} timed out for {CURRENT_USER_TIMEOUT} seconds.')

        self.user_timeouts[user] += 1

    def handle_msg_command(self, channel, user, args):
        if channel != MY_CHANNEL:
            return

        parsed = re.match(r'^(\w+) +(.*)$', args)
        if parsed is None:
            self.send_msg(channel,
                          f'Usage example to send a message to someone else\'s stream: !msg alist_ Yo Alist, get over here')
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
        if (cmd in ['bot', 'help', 'commands'] and channel == MY_CHANNEL) or cmd == 'complexplanebot':
            send_msg(
                'I am a Twitch bot written in Python 3 by ComplexPlane. For a full list of commands: https://git.io/fj2gV')

        elif cmd == 'wr' and channel == MY_CHANNEL:
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
            if leaderboards_upcheck():
                send_msg('Speedrun.com appears to be UP.')
            else:
                send_msg('Speedrun.com appears to be DOWN.')

        elif cmd == 'pausing':
            send_msg(
                'Pause strats are a way to perform perfectly precise movement on a stage. In Monkey Ball, there is zero RNG; if we provide exactly the same inputs on the control stick on exactly the same frames on a level, exactly the same thing will happen. To perform a pause strat, you hold the control stick in an exact direction (thanks to the Gamecube controller\'s notches), pause on a specific frame (using the timer as a reference), and repeat.')

            send_msg(
                'Often we will pause slightly before the intended frame and then press B quickly followed by Pause to advance a small number of frames until the desired frame is reached. Pausing quickly and frame-perfectly is tricky to do consistently, so many pause strats include "backup frames" as well.')

        elif cmd == 'boosting':
            send_msg(
                'Switching between up-left and up-right can change your momentum in certain circumstances. Boosting once at the start of a level ("frame boosting") or into angled walls ("wall boosting") can give you a speed boost. Boosting in mid-air can keep you in the air for slightly longer ("air boosting").')

        elif cmd == 'firstframe':
            send_msg(
                'The game does not consider the stage completed until the third frame after breaking the goaltape. Leaving the stage with "Stage Select" on the first two frames results in a "first frame".')

        elif cmd == 'walls':
            send_msg(
                'For many kinds of walls, wall boosting gives an inconsistent amount of speed. Sometimes you can smoothly roll off of them, sometimes you can just bonk and gain less speed. This inconsistency can make certain strats not RTA-viable.')

        elif cmd == 'alisters':
            send_msg('Alisters Discord: https://discord.gg/N8N8Njc')

        elif cmd == 'smh' and channel == MY_CHANNEL:
            send_msg(
                f'Hi, my name is {user} and you should follow me at twitch.tv/{user}  I\'m an epic speedrunner and MUCH better than this lowly gamer!!')

        elif cmd == 'timeout':
            self.handle_timeout(channel, user, args)

        elif cmd == 'enabletimeout' and channel == MY_CHANNEL:
            if user != MY_CHANNEL:
                REENABLE_TIMEOUT_TIMEOUT = 60
                send_msg(f'/timeout {user} {REENABLE_TIMEOUT_TIMEOUT}')
                send_msg(f'{user} timed out for {REENABLE_TIMEOUT_TIMEOUT} seconds for trying to reenable !timeout.')

            elif self.timeout_cmd_enabled:
                send_msg('!timeout is already enabled.')

            else:
                self.timeout_cmd_enabled = True
                send_msg('!timeout has been enabled.')

        elif cmd == 'disabletimeout' and channel == MY_CHANNEL:
            if user != MY_CHANNEL:
                return  # Silently don't work to add confusion

            if not self.timeout_cmd_enabled:
                send_msg('!timeout is already disabled.')

            else:
                self.timeout_cmd_enabled = False

                def reenable_timeout():
                    self.timeout_cmd_enabled = True
                    send_msg(f'!timeout enabled.')

                self.add_timer_oneshot(TIMEOUT_DISABLE_HOURS * 60 * 60, reenable_timeout)
                send_msg(f'!timeout disabled for {TIMEOUT_DISABLE_HOURS} hours, or until reenabled.')

        elif cmd == 'msg' and channel == MY_CHANNEL:
            self.handle_msg_command(channel, user, args)

        elif channel == MY_CHANNEL and cmd == 'surgery':
            send_msg('https://www.youtube.com/watch?v=DywNCzt_ky8')

        elif channel == MY_CHANNEL and cmd == 'peplane':
            send_msg('On December 27, 2020, myself + PetresInc (Peplane) tied the SMAL world record with two 28:17s!')

        elif channel == MY_CHANNEL and cmd in ['timesave', 'timesaves']:
            send_msg('~6s on Spinning Top (failed 2nd frame, retry) ~0.75s on Stepping Stones (too far left before first stepping stone, speed bump, got clip) ~3s on Giant Comb (idk how I failed this) ~0.2s on Beehive (slow Alist Beehive) ~0.4s on Arthropod (went off a little early so did slow ending) ~0.5s on Seesaw Bridges (got 33.63, slow first clip and wide first turn on last seesaw)')
            send_msg('~0.6s on Fluctuation (bad bounce pattern) ~0.2s on Punched Seesaws (too deep clip) ~1s on Folders (if I get 49 Folders) ~0.5s on Sieve (with faster pausing and faster goal entry) ~8s on Momentum (death) ~1.1s on Swing Shaft (missed frame)')
            send_msg('~1.3s on Guillotine (slow pausing) ~0.2s on Twin Basin (too deep clip) ~2s on Corkscrew (goal bonk) ~6.3s on Gimmick (missed frame) ~0.6s on Postmodern (repause at goal) ~0.5s on Invisible (missed retry) ~0.5s on Created By (slow adjustment)')
            
        elif channel == MY_CHANNEL and cmd == '1080p':
            send_msg('I\'m testing streaming at 1080p 60FPS, primarily so that local recordings are also 1080p. If you notice any frame drops, blurriness, or trouble watching the stream even at lower quality options, let me know!')

        elif channel == MY_CHANNEL and cmd == 'iws':
            send_msg('How many attempts does it take for me to complete an individual world deathless? https://docs.google.com/spreadsheets/d/1EcrM4PHhiGH3CB7R9fYjrXLgkKD1IayUMyeqPDBKBm0/edit?usp=sharing')

        elif channel == MY_CHANNEL and cmd == 'tryhard':
            send_msg('To help focus, I will be hiding splits after W1 and hiding chat after W3. Wish me luck!')

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
            self.send_raw(f'USER {BOT_CHANNEL} {BOT_CHANNEL} {BOT_CHANNEL}')
            self.send_raw(f'PASS {secret.CLIENT_TOKEN}', hide=True)
            self.send_raw(f'NICK {BOT_CHANNEL}')

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
            BOT_CHANNEL, BOT_CHANNEL, channel)

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
                received_str = self.ssock.recv(2040).decode('UTF-8').strip()
                if received_str == '':
                    raise NetworkError('Connection closed')

                received_lines = received_str.split('\n')
                self.recv_queue.extend(received_lines)

                for line in received_lines:
                    if hide:
                        print(f'Received:  {"*" * len(line)}')
                    else:
                        print(f'Received:  {line}')

            except socket.timeout:
                return None

            except Exception as e:
                raise NetworkError('Error during receive attempt', e)

        return self.recv_queue.popleft()
