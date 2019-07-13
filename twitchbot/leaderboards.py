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
        raise GetError(f'Failed to communicate with {site}, is it down?')


def _place_to_rank_index(place):
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


def _rank_index_to_place(rank_index):
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


def _speedrun_com_run_info(run):
    player_uri = run['run']['players'][0]['uri']
    player_json = _safe_get_json(player_uri)
    player_name = player_json['data']['names']['international']

    try:
        player_location = player_json['data']['location']['region']['names']['international']
    except (KeyError, TypeError):
        try:
            player_location = player_json['data']['location']['country']['names']['international']
        except (KeyError, TypeError):
            player_location = 'unknown location'

    date_recorded = run['run']['date']
    place_str = _rank_index_to_place(run['place'] - 1)

    time_sec = run['run']['times']['primary_t']
    time_str = str(datetime.timedelta(seconds=time_sec))
    if time_str.startswith('0:'):
        time_str = time_str[2:]

    return RunInfo(player=player_name, location=player_location, date=date_recorded, duration=time_str, place_str=place_str)


def leaderboards_rank_lookup(cmd):
    rank_index = _place_to_rank_index(cmd)
    if rank_index is None:
        return None

    smal_json = _safe_get_json(SMAL_URI)

    runs = smal_json['data']['runs']
    place = _rank_index_to_place(rank_index)
    if len(runs) - 1 < rank_index:
        return f'Sorry, there is nobody in {place} place.'
    run = runs[rank_index]

    run_info = _speedrun_com_run_info(run)

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
        return 'speedrun.com appears to be UP.'
    except GetError:
        return 'speerdun.com appears to be DOWN.'
