"""Pure decisions for online rooms; no UI or network dependencies."""
from chart_policy import ChartSelection
from song_catalog import BY_ID, recognition_titles
from song_navigation import title_key

ROOM_TIMEOUT = 180.

class RoomInterrupted(RuntimeError):
    pass


def retry_delay(failures):
    return (3,6,12,24,30)[min(max(failures-1,0),4)]


def final_song(title, band=''):
    matches=[song for song in BY_ID.values() if title_key(title) in
             {title_key(v) for v in recognition_titles(song)}]
    if len(matches)>1 and band:
        matches=[s for s in matches if title_key(band) in
                 {title_key(v) for v in (s['band'],*s['band_aliases'])}]
    if len(matches)!=1:
        raise ValueError(f'最终歌曲无法唯一识别：{title} / {band}')
    return matches[0]


def final_selection(song, requested, special_visible):
    actual='expert' if requested=='special' and not special_visible else requested
    return ChartSelection.parse(song['id'],actual)


class RoomClock:
    def __init__(self, now):
        self.entered=now
        self.started=False

    def expired(self, now):
        return not self.started and now-self.entered>=ROOM_TIMEOUT
