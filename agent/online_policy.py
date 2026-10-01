"""Pure decisions for online rooms; no UI or network dependencies."""
from difflib import SequenceMatcher
from functools import lru_cache
import unicodedata
from chart_policy import ChartSelection
from song_catalog import RECOGNITION_BY_ID, recognition_titles
from song_navigation import title_key

ROOM_TIMEOUT = 180.

class RoomInterrupted(RuntimeError):
    pass


def retry_delay(failures):
    return (3,6,12,24,30)[min(max(failures-1,0),4)]


@lru_cache(maxsize=8192)
def recognition_key(text):
    # OCR often changes the number/style of dots, quotes, dashes and decorations.
    return ''.join(c for c in title_key(text)
                   if not unicodedata.category(c).startswith(('P', 'S')))


def _edit_distance(left, right):
    previous = list(range(len(right)+1))
    for i, a in enumerate(left, 1):
        current = [i]
        for j, b in enumerate(right, 1):
            current.append(min(current[-1]+1, previous[j]+1, previous[j-1]+(a != b)))
        previous = current
    return previous[-1]


def final_song(title, band=''):
    error = f'最终歌曲无法唯一识别：{title} / {band}'
    def disambiguate(matches):
        if len(matches)>1 and band:
            matches=[s for s in matches if recognition_key(band) in
                     {recognition_key(v) for v in (s.get('band',''),*s.get('band_aliases',[]))}]
        if len(matches)!=1:
            raise ValueError(error)
        return matches[0]

    matches=[song for song in RECOGNITION_BY_ID.values() if title_key(title) in
             {title_key(v) for v in recognition_titles(song)}]
    if matches:
        return disambiguate(matches)
    key = recognition_key(title)
    if not key:
        raise ValueError(error)
    candidates = [(song, {recognition_key(v) for v in recognition_titles(song)})
                  for song in RECOGNITION_BY_ID.values()]
    matches = [song for song, names in candidates if key in names]
    if matches:
        return disambiguate(matches)
    # A short or visibly truncated title has too little evidence for typo matching.
    if len(key)<6 or any(key != name and (name.startswith(key) or name.endswith(key))
                         for _, names in candidates for name in names):
        raise ValueError(error)
    ranked = []
    for song, names in candidates:
        if band and recognition_key(band) not in {
                recognition_key(v) for v in (song.get('band',''), *song.get('band_aliases',[]))}:
            continue
        scored = [(SequenceMatcher(None, key, name, autojunk=False).ratio(), name)
                  for name in names if name and abs(len(name)-len(key))<=2]
        if scored:
            score, name = max(scored)
            ranked.append((score, song, name))
    ranked.sort(key=lambda row: row[0], reverse=True)
    if not ranked:
        raise ValueError(error)
    score, song, name = ranked[0]
    allowance = 1 if max(len(key),len(name))<12 else 2
    if (score<.82 or _edit_distance(key,name)>allowance or
            (len(ranked)>1 and score-ranked[1][0]<.08)):
        raise ValueError(error)
    return song


def final_selection(song, requested, special_visible):
    actual='expert' if requested=='special' and not special_visible else requested
    return ChartSelection.from_recognized(song,actual)


class RoomClock:
    def __init__(self, now):
        self.entered=now
        self.started=False

    def expired(self, now):
        return not self.started and now-self.entered>=ROOM_TIMEOUT
