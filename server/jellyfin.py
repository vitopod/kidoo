"""Jellyfin connection and read-only catalogue adapter."""
import argparse
import getpass
import json
import os
import re
from pathlib import Path
import sqlite3
import tempfile
from urllib.error import HTTPError, URLError
from urllib.parse import urlencode, urlsplit, parse_qsl, urljoin
from urllib.request import HTTPRedirectHandler, Request, build_opener

TICKS_PER_SECOND = 10_000_000
AUTH_HEADER = 'MediaBrowser Client="Kidoo", Device="Kidoo Server", DeviceId="kidoo-server", Version="0.1"'


class JellyfinError(Exception):
    pass


class NoRedirect(HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


def valid_url(value):
    parts = urlsplit(value)
    if parts.scheme not in ('http', 'https') or not parts.hostname or parts.username or parts.password or parts.query or parts.fragment:
        raise ValueError('Server must be an HTTP(S) URL without credentials, query, or fragment')
    _ = parts.port
    return value.rstrip('/')


def request(base, path, token=None, data=None):
    headers = {'Accept': 'application/json', 'Authorization': AUTH_HEADER + (', Token="' + token + '"' if token else '')}
    body = None
    if data is not None:
        headers['Content-Type'] = 'application/json'
        body = json.dumps(data).encode()
    req = Request(valid_url(base) + path, data=body, headers=headers)
    try:
        with build_opener(NoRedirect).open(req, timeout=10) as response:
            payload = response.read()
            return json.loads(payload) if payload else None
    except HTTPError as exc:
        if exc.code in (401, 403):
            raise JellyfinError('Jellyfin refused access. Check the username, password and account permissions.') from exc
        raise JellyfinError('Jellyfin returned an error. Check the server address and try again.') from exc
    except (URLError, OSError) as exc:
        raise JellyfinError('Could not reach Jellyfin. Check that it is running, the address is correct, and its HTTPS certificate is trusted.') from exc
    except ValueError as exc:
        raise JellyfinError('Jellyfin returned an unreadable response. Check that the address points to Jellyfin.') from exc


def authenticate(base, username, password):
    result = request(base, '/Users/AuthenticateByName', data={'Username': username, 'Pw': password})
    try:
        token, user_id = result['AccessToken'], result['User']['Id']
        if not isinstance(token, str) or not token or not isinstance(user_id, str) or not user_id.isalnum():
            raise ValueError()
        return {'server': valid_url(base), 'userId': user_id, 'token': token}
    except (KeyError, TypeError, ValueError) as exc:
        raise JellyfinError('Jellyfin returned an invalid authentication response') from exc


def load_config(data_dir):
    path = Path(data_dir) / 'jellyfin.json'
    if not path.exists():
        return {}
    try:
        data = json.loads(path.read_text(encoding='utf-8'))
        if not isinstance(data, dict):
            raise ValueError()
        return data
    except (OSError, ValueError) as exc:
        raise JellyfinError('Could not read Jellyfin configuration') from exc


def save_config(data_dir, profile, connection):
    data_dir = Path(data_dir)
    data_dir.mkdir(parents=True, exist_ok=True, mode=0o700)
    data = load_config(data_dir)
    data[profile] = connection
    with tempfile.NamedTemporaryFile(mode='w', encoding='utf-8', dir=data_dir, prefix='.jellyfin-', delete=False) as output:
        os.fchmod(output.fileno(), 0o600)
        json.dump(data, output, ensure_ascii=False, indent=2)
        filename = output.name
    os.replace(filename, data_dir / 'jellyfin.json')


def list_items(connection, start=0, limit=50, resume=False, search=''):
    if not isinstance(connection, dict) or not all(isinstance(connection.get(k), str) and connection[k] for k in ('server', 'token', 'userId')) or not connection['userId'].isalnum():
        raise JellyfinError('Invalid Jellyfin profile configuration')
    params = {'Recursive': 'true', 'IncludeItemTypes': 'Movie,Episode,Video', 'Fields': 'Overview,PrimaryImageAspectRatio,Genres,OfficialRating,ProviderIds', 'SortBy': 'SortName', 'StartIndex': start, 'Limit': limit}
    if search: params['SearchTerm'] = search
    query = urlencode(params)
    endpoint = '/Users/' + connection['userId'] + ('/Items/Resume?' if resume else '/Items?')
    result = request(connection['server'], endpoint + query, connection['token'])
    try:
        items = []
        for item in result['Items']:
            user = item.get('UserData') or {}
            position = user.get('PlaybackPositionTicks') or 0
            duration = item.get('RunTimeTicks') or 0
            items.append({'id': 'jellyfin:' + item['Id'], 'source': 'jellyfin', 'title': item.get('Name', ''),
                          'type': item.get('Type'), 'overview': item.get('Overview'),
                          'rating':item.get('OfficialRating'), 'providerIds':item.get('ProviderIds') or {}, 'genres':item.get('Genres') or [], 'year':item.get('ProductionYear'),
                          'series': item.get('SeriesName'), 'seriesId': item.get('SeriesId'), 'seasonNumber': item.get('ParentIndexNumber'),
                          'episodeNumber': item.get('IndexNumber'), 'duration': duration / TICKS_PER_SECOND,
                          'progress': {'position': position / TICKS_PER_SECOND, 'completed': bool(user.get('Played')), 'playCount':user.get('PlayCount') or 0,
                                       'updatedAt': user.get('LastPlayedDate')},
                          'hasArtwork': bool((item.get('ImageTags') or {}).get('Primary')),
                          'hasBackdrop': bool(item.get('BackdropImageTags')),
                          'artworkTag': (item.get('ImageTags') or {}).get('Primary'),
                          'backdropTag': (item.get('BackdropImageTags') or [None])[0],
                          'artworkAspectRatio': item.get('PrimaryImageAspectRatio')})
        return {'items': items, 'total': result['TotalRecordCount'], 'start': start, 'limit': limit}
    except (KeyError, TypeError, ValueError) as exc:
        raise JellyfinError('Jellyfin returned an invalid library response') from exc


def list_series_artwork(connection):
    """Read series covers separately; episode images are not series posters."""
    result = {}
    start = 0
    while True:
        query = urlencode({'Recursive':'true', 'IncludeItemTypes':'Series',
                           'Fields':'PrimaryImageAspectRatio', 'StartIndex':start, 'Limit':1000})
        data = request(connection['server'], '/Users/' + connection['userId'] + '/Items?' + query, connection['token'])
        try:
            items = data['Items']
            total = data['TotalRecordCount']
            for item in items:
                if item.get('Type') != 'Series' or not isinstance(item.get('Id'),str) or not item['Id'].isalnum(): continue
                result[item['Id']] = {
                    'hasArtwork':bool((item.get('ImageTags') or {}).get('Primary')),
                    'hasBackdrop':bool(item.get('BackdropImageTags')),
                    'artworkTag':(item.get('ImageTags') or {}).get('Primary'),
                    'backdropTag':(item.get('BackdropImageTags') or [None])[0],
                    'artworkAspectRatio':item.get('PrimaryImageAspectRatio')}
            start += len(items)
            if not items or start >= total: break
        except (KeyError, TypeError, ValueError) as exc:
            raise JellyfinError('Jellyfin returned invalid series artwork') from exc
    return result


def series_has_approved_episode(connection, series_id, approved_ids):
    """Allow a series cover only when an accessible episode is approved."""
    if not series_id.isalnum() or not approved_ids:
        return False
    start = 0
    while True:
        query = urlencode({'ParentId': series_id, 'Recursive': 'true',
                           'IncludeItemTypes': 'Episode', 'Fields': 'SeriesId',
                           'StartIndex': start, 'Limit': 100})
        data = request(connection['server'], '/Users/' + connection['userId'] + '/Items?' + query, connection['token'])
        try:
            items, total = data['Items'], data['TotalRecordCount']
            if any(item.get('Type') == 'Episode' and item.get('SeriesId') == series_id
                   and item.get('Id') in approved_ids for item in items):
                return True
            start += len(items)
            if not items or start >= total:
                return False
        except (KeyError, TypeError, ValueError) as exc:
            raise JellyfinError('Jellyfin returned invalid episode data') from exc


def get_item(connection, item_id):
    if not isinstance(item_id, str) or not item_id.isalnum() or not connection['userId'].isalnum():
        raise ValueError('Invalid item ID')
    return request(connection['server'], '/Users/' + connection['userId'] + '/Items/' + item_id, connection['token'])


def playback_info(connection, item_id):
    if not isinstance(item_id, str) or not item_id.isalnum():
        raise ValueError('Invalid item ID')
    params = urlencode({'UserId': connection['userId']})
    result = request(connection['server'], '/Items/' + item_id + '/PlaybackInfo?' + params, connection['token'])
    if not isinstance(result, dict) or not isinstance(result.get('PlaySessionId'), str):
        raise JellyfinError('Jellyfin did not provide a playback session')
    return result


def transcode_playback_info(connection, item_id):
    if not isinstance(item_id, str) or not item_id.isalnum(): raise ValueError('Invalid item ID')
    profile={'Name':'Kidoo TV','MaxStreamingBitrate':8000000,'DirectPlayProfiles':[],
             'TranscodingProfiles':[{'Type':'Video','Container':'ts','Protocol':'hls',
                                    'VideoCodec':'h264','AudioCodec':'aac','MaxAudioChannels':'2',
                                    'MinSegments':1,'BreakOnNonKeyFrames':True}]}
    result=request(connection['server'], '/Items/'+item_id+'/PlaybackInfo', connection['token'],
                   {'UserId':connection['userId'],'EnableDirectPlay':False,'EnableDirectStream':False,
                    'EnableTranscoding':True,'DeviceProfile':profile})
    if not isinstance(result,dict) or not isinstance(result.get('PlaySessionId'),str):
        raise JellyfinError('Jellyfin did not provide a playback session')
    source=next((v for v in result.get('MediaSources',[]) if v.get('TranscodingUrl')),None)
    if not source: raise JellyfinError('Jellyfin cannot transcode this video')
    return {'PlaySessionId':result['PlaySessionId'],'path':safe_transcode_path(source['TranscodingUrl'],item_id)}


def safe_transcode_path(value, item_id, parent=None):
    """Accept only this item's Jellyfin HLS URLs and keep credentials on the server."""
    if not isinstance(value,str) or len(value)>8192: raise ValueError('Invalid transcoding URL')
    raw=urlsplit(value)
    if raw.scheme or raw.netloc or raw.fragment: raise ValueError('External transcoding URL denied')
    path=urlsplit(urljoin(parent or '/',value))
    parts=path.path.split('/')
    if len(parts)<4 or parts[1].lower()!='videos' or parts[2].replace('-','').lower()!=item_id.replace('-','').lower():
        raise ValueError('Transcoding URL does not belong to this video')
    if any(not re.fullmatch(r'[A-Za-z0-9._-]+',part) or part in ('.','..') for part in parts[1:]) or not path.path.lower().endswith(('.m3u8','.ts')):
        raise ValueError('Unsupported transcoding resource')
    query=urlencode([(k,v) for k,v in parse_qsl(path.query,keep_blank_values=True) if k.casefold() not in ('apikey','api_key','access_token','token','x-emby-token','x-mediabrowser-token')])
    return path.path + ('?'+query if query else '')


def open_transcode(connection, path):
    headers={'Authorization':AUTH_HEADER + ', Token="' + connection['token'] + '"'}
    try: return build_opener(NoRedirect).open(Request(valid_url(connection['server']) + path, headers=headers), timeout=30)
    except (HTTPError, URLError, OSError) as exc: raise JellyfinError('Could not open the transcoded stream') from exc


def report_playback(connection, kind, item_id, position, play_session_id, paused=False, play_method='DirectStream'):
    if kind not in ('start', 'progress', 'stop') or not isinstance(item_id, str) or not item_id.isalnum():
        raise ValueError('Invalid playback event')
    endpoints = {'start': '/Sessions/Playing', 'progress': '/Sessions/Playing/Progress', 'stop': '/Sessions/Playing/Stopped'}
    payload = {'ItemId': item_id, 'PositionTicks': round(position * TICKS_PER_SECOND),
               'PlaySessionId': play_session_id, 'CanSeek': True, 'IsPaused': paused, 'IsMuted': False,
               'PlayMethod': play_method}
    request(connection['server'], endpoints[kind], connection['token'], payload)


def open_stream(connection, item_id, range_header=None):
    if not item_id.isalnum():
        raise ValueError('Invalid item ID')
    headers = {'Authorization': AUTH_HEADER + ', Token="' + connection['token'] + '"'}
    if range_header:
        headers['Range'] = range_header
    url = valid_url(connection['server']) + '/Videos/' + item_id + '/stream?static=true'
    try:
        return build_opener(NoRedirect).open(Request(url, headers=headers), timeout=30)
    except (HTTPError, URLError, OSError) as exc:
        raise JellyfinError('Could not open the Jellyfin stream') from exc


def artwork(connection, item_id, image_type='Primary'):
    if not isinstance(item_id, str) or not item_id.isalnum():
        raise ValueError('Invalid item ID')
    if image_type not in ('Primary', 'Backdrop'): raise ValueError('Invalid image type')
    headers = {'Authorization': AUTH_HEADER + ', Token="' + connection['token'] + '"'}
    url = valid_url(connection['server']) + '/Items/' + item_id + '/Images/' + image_type + '?maxWidth=960&quality=90'
    try:
        with build_opener(NoRedirect).open(Request(url, headers=headers), timeout=10) as response:
            content_type = response.headers.get_content_type()
            if content_type not in ('image/jpeg', 'image/png', 'image/webp'):
                raise JellyfinError('Invalid artwork type')
            content = response.read(2 * 1024 * 1024 + 1)
            if len(content) > 2 * 1024 * 1024: raise JellyfinError('Artwork too large')
            return content_type, content
    except (HTTPError, URLError, OSError) as exc:
        raise JellyfinError('Jellyfin artwork unavailable') from exc


def main():
    parser = argparse.ArgumentParser(description='Map a Kidoo profile to an existing Jellyfin user')
    parser.add_argument('--data-dir', default=str(Path.home() / '.kidoo'))
    parser.add_argument('--profile', required=True)
    parser.add_argument('--server', required=True, help='Jellyfin server URL')
    parser.add_argument('--username', required=True, help='Existing Jellyfin user')
    args = parser.parse_args()
    db = Path(args.data_dir) / 'kidoo.db'
    if not db.is_file():
        parser.error('Kidoo database not found; start the Kidoo server first')
    with sqlite3.connect(db) as connection:
        if not connection.execute('SELECT 1 FROM profiles WHERE id=?', (args.profile,)).fetchone():
            parser.error('Unknown Kidoo profile ID')
    try:
        server = valid_url(args.server)
        password = getpass.getpass('Jellyfin password: ')
        mapping = authenticate(server, args.username, password)
        save_config(args.data_dir, args.profile, mapping)
    except (ValueError, JellyfinError, OSError) as exc:
        parser.exit(1, f'Setup failed: {exc}\n')
    print('Profile mapped. The password was not stored; the access token is in the Kidoo data directory.')


if __name__ == '__main__':
    main()
