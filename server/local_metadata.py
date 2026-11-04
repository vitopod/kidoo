"""Parent-managed descriptions for local files; never grants playback access."""
import json


def validate(value):
    if not isinstance(value, dict) or set(value) - {'title', 'summary', 'mediaType', 'seriesName', 'seasonNumber', 'episodeNumber', 'tags'}:
        raise ValueError('Invalid metadata')
    result = {}
    for key, maximum in (('title', 200), ('summary', 2000), ('seriesName', 200)):
        text = value.get(key, '')
        if not isinstance(text, str) or len(text) > maximum:
            raise ValueError('Invalid ' + key)
        result[key] = text.strip()
    kind = value.get('mediaType', 'Other')
    if kind not in ('Movie', 'Episode', 'Other'):
        raise ValueError('Choose a valid content type')
    if kind == 'Episode' and not result['seriesName']:
        raise ValueError('Episodes need a series name')
    result['mediaType'] = kind
    if kind != 'Episode': result['seriesName'] = ''
    for key in ('seasonNumber', 'episodeNumber'):
        number = value.get(key)
        if number is not None and (isinstance(number, bool) or not isinstance(number, int) or not 0 <= number <= 9999):
            raise ValueError('Invalid ' + key)
        result[key] = number if kind == 'Episode' else None
    tags = value.get('tags', [])
    if not isinstance(tags, list) or len(tags) > 20 or any(not isinstance(t, str) or not t.strip() or len(t) > 40 for t in tags):
        raise ValueError('Invalid topics')
    result['tags'] = list(dict.fromkeys(t.strip() for t in tags))
    return result


def load(db):
    return {item: json.loads(metadata) for item, metadata in db.execute('SELECT media_id,metadata FROM local_metadata')}
