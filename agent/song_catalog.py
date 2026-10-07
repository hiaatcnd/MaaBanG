"""Separate bundled catalogs for CN choices and all-server song recognition."""
import json
from pathlib import Path

DATA_DIR = Path(__file__).parent/'data'
TITLE_OCR_ALIASES = json.loads((DATA_DIR/'song_ocr_aliases.json').read_text(encoding='utf-8'))
CATALOG, RECOGNITION_CATALOG, RECOGNITION_BY_ID, BY_ID, BY_KEY = {}, {}, {}, {}, {}
_songs, SONGS = [], []
_loaded_signature = None


def reload_catalog():
    """Refresh in place so modules holding imported dictionaries see new data."""
    global _loaded_signature
    paths = [DATA_DIR/'songs_cn.json', DATA_DIR/'songs_all.json']
    signature = tuple((p.stat().st_mtime_ns, p.stat().st_size) for p in paths)
    if signature == _loaded_signature:
        return
    catalog, recognition = [json.loads(p.read_text(encoding='utf8')) for p in paths]
    if catalog['fetched_at'] != recognition['fetched_at']:
        raise ValueError('歌曲目录正在更新，请稍后重试')
    songs = [s for s in catalog['songs'] if s['active'] and
             s['difficulties'].get('expert', {}).get('available')]
    by_id = {s['id']: s for s in songs}
    recognition_by_id = {s['id']: s for s in recognition['songs']}
    by_key = {(f"{s['title']} [{s['id']}]" if sum(t['title'] == s['title'] for t in songs) > 1
               else s['title']): s for s in songs}
    if not by_key or not by_id.keys() <= recognition_by_id.keys():
        raise ValueError('歌曲目录不完整，保留已加载资料')
    keys = sorted(by_key, key=lambda key: (key != 'SAVIOR OF SONG', int(by_key[key]['id'])))
    for target, source in ((CATALOG, catalog), (RECOGNITION_CATALOG, recognition),
                           (RECOGNITION_BY_ID, recognition_by_id), (BY_ID, by_id), (BY_KEY, by_key)):
        target.clear()
        target.update(source)
    _songs[:] = songs
    SONGS[:] = keys
    _loaded_signature = signature


reload_catalog()


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


def recognition_titles(song):
    """Catalog names plus exact OCR variants reviewed against game screenshots."""
    return (song['title'], *song['aliases'], *TITLE_OCR_ALIASES.get(song['id'], ()))
