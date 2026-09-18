"""Offline China-server song catalog, bundled with the Agent."""
import json
from pathlib import Path

CATALOG = json.loads((Path(__file__).parent/'data/songs_cn.json').read_text(encoding='utf-8'))
_songs = [s for s in CATALOG['songs'] if s['active'] and
          s['difficulties'].get('expert', {}).get('available')]
BY_ID = {song['id']: song for song in _songs}
BY_KEY = {}
for song in _songs:
    duplicate = sum(s['title'] == song['title'] for s in _songs) > 1
    key = f"{song['title']} [{song['id']}]" if duplicate else song['title']
    BY_KEY[key] = song
SONGS = tuple(sorted(BY_KEY, key=lambda key: (key != 'SAVIOR OF SONG', int(BY_KEY[key]['id']))))


def resolve_song(value):
    song = BY_KEY.get(value) or BY_ID.get(value)
    if song is None:
        raise ValueError('歌曲不在中国服可选目录中；同名歌曲请指定歌曲ID')
    return song


def available_difficulties(song):
    return tuple(name for name, chart in song['difficulties'].items() if chart['available'])


def needs_band_check(song_id):
    song = BY_ID[song_id]
    return sum(s['title'] == song['title'] for s in _songs) > 1
