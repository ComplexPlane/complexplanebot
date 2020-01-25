import collections
import urllib
import requests
import datetime
import re

from .exn import GetError

# URI for the Story Mode All Levels (NTSC) leaderboard
SMAL_VAR = 'wl3vv981'
SMAL_VAL = '5q8kgmyq'
SMAL_URI = 'https://www.speedrun.com/api/v1/leaderboards/nd2ervd0/category/zd3l7ydn?var-wl3vv981=5q8kgmyq'

RunInfo = collections.namedtuple('RunInfo', ['player', 'location', 'date', 'duration', 'place_str'])


def _safe_get_json(uri, valid404=False):
    try:
        response = requests.get(uri, timeout=2)
        if valid404 and response.status_code == 404:
            return None
        response.raise_for_status()
        return response.json()
    except requests.exceptions.RequestException:
        site = urllib.parse.urlparse(uri).netloc
        raise GetError(f'Failed to fetch info from speedrun.com. Rate-limiting is likely in effect. Please try again later.')


def _decode_place(place):
    place_regex = '^([0-9]+)([a-zA-Z]+)$'
    match = re.match(place_regex, place)
    if match is None:
        return None

    place = int(match.group(1))
    suffix = match.group(2)
    if place == 0:
        return None

    if place % 10 == 1 and place != 11:
        if suffix != 'st':
            return None
    elif place % 10 == 2 and place != 12:
        if suffix != 'nd':
            return None
    elif place % 10 == 3 and place != 13:
        if suffix != 'rd':
            return None
    elif suffix != 'th':
        return None

    return place


def _encode_place(place):
    if place < 1:
        return None

    if place % 10 == 1:
        if place == 11:
            return f'{place}th'
        return f'{place}st'

    if place % 10 == 2:
        if place == 12:
            return f'{place}th'
        return f'{place}nd'

    if place % 10 == 3:
        if place == 13:
            return f'{place}th'
        return f'{place}rd'

    return f'{place}th'


def _speedrun_com_run_info(run):
    player_uri = run['run']['players'][0]['uri']
    player_json = _safe_get_json(player_uri)

    try:
        player_name = player_json['data']['names']['international']
    except (KeyError, TypeError):
        try:
            player_name = player_json['data']['name']
        except (KeyError, TypeError):
            player_name = '(unknown player name)'

    try:
        player_location = player_json['data']['location']['region']['names']['international']
    except (KeyError, TypeError):
        try:
            player_location = player_json['data']['location']['country']['names']['international']
        except (KeyError, TypeError):
            player_location = '(unknown location)'

    date_recorded = run['run']['date']
    if date_recorded is None:
        date_recorded = '(unknown date)'
    place_str = _encode_place(run['place'])

    time_sec = run['run']['times']['primary_t']
    time_str = str(datetime.timedelta(seconds=time_sec))
    if time_str.startswith('0:'):
        time_str = time_str[2:]

    return RunInfo(
        player=player_name,
        date=date_recorded,
        duration=time_str,
        place_str=place_str,
        location=player_location,
    )


def leaderboards_rank_lookup(place_str):
    place = _decode_place(place_str)
    if place is None:
        return None

    smal_json = _safe_get_json(SMAL_URI)
    runs_in_place = list(filter(lambda run: run['place'] == place, smal_json['data']['runs']))
    run_infos = list(map(_speedrun_com_run_info, runs_in_place))

    place_str_normalized = _encode_place(place)

    if place == 1:
        place_text = 'The world record'
    else:
        place_text = f'{place_str_normalized} place'

    if len(runs_in_place) == 0:
        return f'Sorry, there is nobody in {place_str_normalized} place.'

    if len(runs_in_place) == 1:
        run_info = run_infos[0]

        return f'{place_text} for Super Monkey Ball 2: Story Mode All Levels is {run_info.duration} by {run_info.player}, set on {run_info.date}. {run_info.player} is from {run_info.location}.'

    # There is a tie

    names_list = list(map(lambda run: run.player, run_infos))
    if len(run_infos) == 2:
        names_str = f'{names_list[0]} and {names_list[1]}'
    else:
        names_str = ', '.join(names_list[:-1]) + f'and {names_list[-1]}'

    return f'{place_text} for Super Monkey Ball 2: Story Mode All Levels is {run_infos[0].duration}, a tie between {names_str}.'


def leaderboards_user_lookup(user):
    if user == '':
        return 'Please provide a valid speedrun.com username to lookup.'
    if re.match(r'^\w+$', user) is None:
        return f'Invalid username: {user}'

    pbs = _safe_get_json(f'https://www.speedrun.com/api/v1/users/{user}/personal-bests', valid404=True)
    if pbs is None:
        return f'User {user} does not exist on speedrun.com.'

    for pb in pbs['data']:
        # We can identify the SMAL run just by checking the "All Levels" variable IDs
        if SMAL_VAR in pb['run']['values'] and pb['run']['values'][SMAL_VAR] == SMAL_VAL:
            run_info = _speedrun_com_run_info(pb)
            return f'{user} has {run_info.place_str} place in SMB2 SMAL, with a time of {run_info.duration}. It was set on {run_info.date}.'
    else:
        return f'{user} has not submitted a SMB2 SMAL time to the speedrun.com leaderboards.'


def leaderboards_latest_run():
    smal_json = _safe_get_json(SMAL_URI)

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

    run_info = _speedrun_com_run_info(latest_run)
    return f'The leaderboard\'s latest SMB2 SMAL run was submitted on {run_info.date} by {run_info.player}, with a time of {run_info.duration} ({run_info.place_str}). {run_info.player} is from {run_info.location}.'


def leaderboards_upcheck():
    try:
        _safe_get_json(SMAL_URI)
        return True
    except GetError:
        return False
