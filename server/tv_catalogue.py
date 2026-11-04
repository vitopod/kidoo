"""Server-owned native catalogue policy. No network requests or age inference."""
INTERESTS = [
    ('space','Space'),('animals','Animals'),('making','Making things'),
    ('science','How things work'),('planet','Our planet'),('art','Art & animation'),
    ('music','Music'),('stories','Stories'),('maths','Maths & patterns'),('questions','Big questions')]
GENERAL = {'observation','patterns','materials','creativity','perspective','problem solving','persistence','learning','cooperation'}
GENRE_INTERESTS = {
    'science fiction': {'science','space'}, 'sci-fi': {'science','space'},
    'documentary': {'science'}, 'nature': {'animals','planet'},
    'animation': {'art','stories'}, 'family': {'stories'},
    'fantasy': {'stories'}, 'adventure': {'stories'}, 'music': {'music'},
}

def genre_interests(genres):
    """Use explicit Jellyfin genres only; missing or unknown metadata adds no signal."""
    return set().union(*(GENRE_INTERESTS.get(str(g).strip().casefold(), set()) for g in (genres or [])))

def jellyfin_interest_signals(items, history):
    """Use meaningful viewing of approved titles, capped so one title cannot dominate."""
    signals = {}
    for item in items:
        if not item.get('approved'): continue
        measured = history.get(item.get('id'), {})
        duration, seconds = measured.get('duration', 0), measured.get('seconds', 0)
        if not duration or seconds < max(5, duration * .2): continue
        for interest in genre_interests(item.get('genres')):
            signals[interest] = min(signals.get(interest, 0) + 1, 4)
    return signals

def recommend_jellyfin(items, shows, settings, online_interests, external_signals, limit=4):
    """Suggest approved unwatched movies and series with an explicit interest match."""
    wanted=set(settings.get('interests', [])) | set(online_interests) | set(external_signals)
    candidates=[]
    for item in list(items) + list(shows):
        if not item.get('approved'): continue
        if item.get('mediaType') == 'Episode': continue
        if item.get('kind') == 'series':
            episodes=item.get('episodes') or []
            if not episodes or any((v.get('progress') or {}).get('position',0)>0 or (v.get('progress') or {}).get('completed') for v in episodes): continue
            interests=set().union(*(genre_interests(v.get('genres')) for v in episodes if v.get('approved')))
        else:
            progress=item.get('progress') or {}
            if progress.get('position',0)>0 or progress.get('completed'): continue
            interests=genre_interests(item.get('genres'))
        overlap=interests & wanted
        if overlap: candidates.append((len(overlap),item))
    candidates.sort(key=lambda pair:(-pair[0],pair[1].get('title','').casefold()))
    return [{**item,'reason':'Related to your interests'} for _,item in candidates[:limit]]



def deprioritized_library(item, preferences):
    return ((item.get('kind'),str(item.get('id',''))) in preferences or
            ('series',str(item.get('seriesId') or (item.get('id') if item.get('kind')=='series' else ''))) in preferences)
