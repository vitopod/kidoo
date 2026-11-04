"""Conservative, opt-in Jellyfin rating policy; ratings are not a safety guarantee."""
import re
import sqlite3

# A parent selects a classification system and the highest label they accept.
# These are ordered classifications, not inferred ages (PG is guidance, not an age).
RATINGS = {
    'us': ('G', 'PG', 'PG-13', 'R', 'NC-17'),
    'gb': ('U', 'PG', '12', '12A', '15', '18', 'R18'),
    'it': ('T', '6+', '10+', '12+', '14+', '18+'),
    'fr': ('0+', '6+', '9+', '10', '12', '14+', '16', '18', 'X'),
}
ALIASES = {
    'gb': {'12A':'12'},
    'it': {'VM6':'6+', 'VM10':'10+', 'VM12':'12+', 'VM14':'14+', 'VM18':'18+'},
    'fr': {'TOUS PUBLICS':'0+', 'TP':'0+', 'U':'0+',
           'INTERDIT AUX MOINS DE 10 ANS':'10', 'INTERDIT AUX MOINS DE 12 ANS':'12',
           'INTERDIT AUX MOINS DE 16 ANS':'16', 'INTERDIT AUX MOINS DE 18 ANS':'18',
           '–10':'10', '–12':'12', '–16':'16', '–18':'18',
           '-10':'10', '-12':'12', '-16':'16', '-18':'18'},
}
DEFAULT = {'mode': 'manual', 'system': 'us', 'maximum': 'G', 'seriesFallback': False}


def init(db):
    db.execute('''CREATE TABLE IF NOT EXISTS jellyfin_age_policies (
        profile_id TEXT PRIMARY KEY, mode TEXT NOT NULL, system TEXT NOT NULL,
        maximum TEXT NOT NULL, series_fallback INTEGER NOT NULL DEFAULT 0)''')
    db.execute('''CREATE TABLE IF NOT EXISTS jellyfin_series_overrides (
        profile_id TEXT NOT NULL, namespace TEXT NOT NULL, item_id TEXT NOT NULL,
        PRIMARY KEY(profile_id,namespace,item_id))''')


def load(db, profile):
    row = db.execute('SELECT mode,system,maximum,series_fallback FROM jellyfin_age_policies WHERE profile_id=?', (profile,)).fetchone()
    return {'mode':row[0], 'system':row[1], 'maximum':row[2], 'seriesFallback':bool(row[3])} if row else dict(DEFAULT)


def validate(policy):
    if not isinstance(policy, dict): raise ValueError('Invalid policy')
    mode, system, maximum, fallback = (policy.get(k) for k in ('mode','system','maximum','seriesFallback'))
    if mode not in ('manual','suggest','automatic') or system not in RATINGS or maximum not in RATINGS[system] or not isinstance(fallback, bool):
        raise ValueError('Invalid policy')
    return {'mode':mode,'system':system,'maximum':maximum,'seriesFallback':fallback}


def save(db, profile, policy):
    policy=validate(policy)
    if not db.execute('SELECT 1 FROM profiles WHERE id=?',(profile,)).fetchone(): raise ValueError('Unknown profile')
    db.execute('INSERT OR REPLACE INTO jellyfin_age_policies VALUES (?,?,?,?,?)',
               (profile,policy['mode'],policy['system'],policy['maximum'],int(policy['seriesFallback'])))
    return policy


def recognized(item):
    ids=item.get('ProviderIds') or {}
    if not isinstance(ids,dict): return False
    for name,value in ids.items():
        if not isinstance(name,str) or not isinstance(value,str): continue
        value=value.strip()
        if name.casefold() == 'imdb' and re.fullmatch(r'tt[0-9]{5,12}',value): return True
        if name.casefold() in ('tmdb','tvdb') and re.fullmatch(r'[1-9][0-9]{0,11}',value): return True
    return False


def evaluate(item, policy, series=None):
    """Return an explanation; a missing or ambiguous classification never passes."""
    policy=validate(policy)
    if not isinstance(item,dict) or item.get('Type') not in ('Movie','Episode'):
        return {'eligible':False,'reason':'Unsupported or unrecognized type'}
    if item.get('Type')=='Episode':
        if not isinstance(series,dict) or series.get('Type')!='Series' or series.get('Id') != item.get('SeriesId') or not recognized(series):
            return {'eligible':False,'reason':'Series identity not recognized'}
    elif not recognized(item):
        return {'eligible':False,'reason':'Identity not recognized'}
    rating=item.get('OfficialRating')
    if not rating and item.get('Type')=='Episode' and policy['seriesFallback']:
        rating=series.get('OfficialRating')
    if not isinstance(rating,str) or not rating.strip():
        return {'eligible':False,'reason':'No age rating'}
    rating=rating.strip().upper()
    # Jellyfin may prefix a country, but never translate it across systems.
    prefixed=re.fullmatch(r'([A-Z]{2})\s*[:-]\s*(.+)',rating)
    if prefixed and rating not in RATINGS[policy['system']]:
        prefix,rating=prefixed.groups()
        prefixes={'US':'us','GB':'gb','UK':'gb','IT':'it','FR':'fr'}
        if prefixes.get(prefix.strip())!=policy['system']:
            return {'eligible':False,'reason':'Rating belongs to another or unknown system'}
        rating=rating.strip()
    labels=RATINGS[policy['system']]
    normalized=ALIASES.get(policy['system'],{}).get(rating,rating)
    if normalized not in labels:
        return {'eligible':False,'reason':'Rating not recognized for this system','rating':rating}
    maximum=ALIASES.get(policy['system'],{}).get(policy['maximum'],policy['maximum'])
    if labels.index(normalized)>labels.index(maximum):
        return {'eligible':False,'reason':'Rating exceeds the selected limit','rating':rating}
    return {'eligible':True,'reason':'Rating meets the selected limit','rating':rating}
