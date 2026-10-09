"""Shared catalog conversion for explicit updates and release builds."""
import hashlib
import json
import os
from datetime import datetime, timezone
from pathlib import Path
from tempfile import NamedTemporaryFile, gettempdir
from contextlib import contextmanager
from urllib.request import Request, urlopen

SONGS_URL = 'https://bestdori.com/api/songs/all.7.json'
BANDS_URL = 'https://bestdori.com/api/bands/all.1.json'
DIFFICULTIES = ('easy', 'normal', 'hard', 'expert', 'special')
CN = 3


def cn(values):
    return values[CN] if isinstance(values, list) and len(values) > CN else None


def build_catalog(songs, bands, now_ms):
    result = []
    for song_id, source in sorted(songs.items(), key=lambda pair: int(pair[0])):
        published = cn(source.get('publishedAt'))
        if published is None or int(published) > now_ms:
            continue
        title = cn(source.get('musicTitle'))
        if not title:
            raise ValueError(f'Missing CN title: {song_id}')
        closed = cn(source.get('closedAt'))
        band_names = bands.get(str(source['bandId']), {}).get('bandName', [])
        charts = {}
        for key, chart in source.get('difficulty', {}).items():
            if key not in tuple(map(str, range(5))):
                continue
            # Missing override inherits the song's release date. Explicit null
            # remains unknown/unavailable; never borrow a JP SPECIAL release.
            released = cn(chart['publishedAt']) if 'publishedAt' in chart else published
            charts[DIFFICULTIES[int(key)]] = {
                'level': chart['playLevel'],
                'notes': source.get('notes', {}).get(key),
                'published_at': int(released) if released is not None else None,
                'available': released is not None and int(released) <= now_ms,
            }
        result.append({
            'id': str(song_id), 'title': title,
            'aliases': list(dict.fromkeys(v for v in source['musicTitle'] if v)),
            'band_id': source['bandId'], 'band': cn(band_names) or next(iter(band_names), ''),
            'band_aliases': list(dict.fromkeys(v for v in band_names if v)),
            'tag': source.get('tag'), 'length_seconds': source.get('length'),
            'published_at': int(published), 'closed_at': int(closed) if closed else None,
            'active': not closed or int(closed) > now_ms,
            'difficulties': charts, 'jacket_images': source.get('jacketImage', []),
            'url': f'https://bestdori.com/info/songs/{song_id}',
        })
    if not result:
        raise ValueError('Empty CN catalog; refusing to replace local data')
    return result


def playable(catalog):
    return [s for s in catalog if s['active'] and
            s['difficulties'].get('expert', {}).get('available')]


def build_recognition_catalog(songs, bands):
    """Keep every song identity; game-visible difficulty decides what can be played.

    Release dates deliberately do not filter this catalog. Bestdori may lag CN
    releases, while its chart metadata and names are already present elsewhere.
    This catalog must never be used to generate user-selectable song choices.
    """
    result = []
    for song_id, source in sorted(songs.items(), key=lambda pair: int(pair[0])):
        names = source.get('musicTitle', [])
        aliases = list(dict.fromkeys(v for v in names if isinstance(v, str) and v))
        if not aliases:
            raise ValueError(f'Missing song titles: {song_id}')
        band_names = bands.get(str(source['bandId']), {}).get('bandName', [])
        band_aliases = list(dict.fromkeys(v for v in band_names if isinstance(v, str) and v))
        charts = {}
        for key, chart in source.get('difficulty', {}).items():
            if key not in tuple(map(str, range(5))):
                continue
            level = chart.get('playLevel')
            notes = source.get('notes', {}).get(key)
            if not isinstance(level, int) or level <= 0:
                raise ValueError(f'Invalid chart level: {song_id}/{key}')
            if notes is not None and (not isinstance(notes, int) or notes <= 0):
                raise ValueError(f'Invalid chart notes: {song_id}/{key}')
            charts[DIFFICULTIES[int(key)]] = {
                'level': level, 'notes': notes, 'available': True,
            }
        result.append({
            'id': str(song_id), 'title': cn(names) or aliases[0], 'aliases': aliases,
            'band_id': source['bandId'], 'band': cn(band_names) or next(iter(band_aliases), ''),
            'band_aliases': band_aliases, 'difficulties': charts,
            'url': f'https://bestdori.com/info/songs/{song_id}',
        })
    if not result:
        raise ValueError('Empty recognition catalog; refusing to replace local data')
    return result


def apply_verified_availability(catalog, overrides):
    """Use explicit CN game observations when upstream chart dates are missing.

    Do not infer release dates or extend closed/future songs. Identity and level
    changes invalidate the observation and require another review.
    """
    for song in catalog:
        evidence = overrides.get(song['id'])
        if not evidence or not song['active']:
            continue
        if (song['title'], song['band_id']) != (evidence['title'], evidence['band_id']):
            raise ValueError(f"CN availability observation identity changed: {song['id']}")
        for difficulty, level in evidence['levels'].items():
            chart = song['difficulties'].get(difficulty)
            if not chart or chart['level'] != level:
                raise ValueError(f"CN availability observation level changed: {song['id']}/{difficulty}")
            if not chart['available'] and chart['published_at'] is None:
                chart['available'] = True
                chart['availability_verified_at'] = evidence['verified_at']
    return catalog


def song_key(song, songs):
    return (song['title'] if sum(s['title'] == song['title'] for s in songs) == 1
            else f"{song['title']} [{song['id']}]")



def fetch(url):
    with urlopen(Request(url, headers={'User-Agent': 'MaaBanG-song-catalog/1.0'}), timeout=40) as response:
        return response.read()


def replace_files(contents):
    """Stage all outputs before replacing any, rolling back on write failures."""
    backups = {path: path.read_bytes() for path in contents}
    staged = {}
    replaced = []
    try:
        for path, content in contents.items():
            with NamedTemporaryFile(dir=path.parent, prefix=path.name+'.', suffix='.tmp', delete=False) as f:
                staged[path] = Path(f.name)
                f.write(content)
                f.flush()
                os.fsync(f.fileno())
        for path, temp in staged.items():
            os.replace(temp, path)
            replaced.append(path)
    except Exception:
        for path in reversed(replaced):
            path.write_bytes(backups[path])
        raise
    finally:
        for temp in staged.values():
            temp.unlink(missing_ok=True)


@contextmanager
def catalog_update_lock(data_dir):
    """Serialize writers across instances; the OS releases the lock on exit/crash."""
    key = hashlib.sha256(str(data_dir.resolve()).casefold().encode()).hexdigest()[:24]
    path = Path(gettempdir())/f'maabang-song-catalog-{key}.lock'
    with path.open('a+b') as lock:
        if path.stat().st_size == 0:
            lock.write(b'0')
            lock.flush()
        lock.seek(0)
        if os.name == 'nt':
            import msvcrt
            acquire = lambda: msvcrt.locking(lock.fileno(), msvcrt.LK_NBLCK, 1)
            release = lambda: msvcrt.locking(lock.fileno(), msvcrt.LK_UNLCK, 1)
        else:
            import fcntl
            acquire = lambda: fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
            release = lambda: fcntl.flock(lock, fcntl.LOCK_UN)
        try:
            acquire()
        except OSError as exc:
            raise RuntimeError('另一个实例正在更新歌曲列表，请稍后重试') from exc
        try:
            yield
        finally:
            release()


def refresh_catalog(data_dir, *, songs_raw=None, bands_raw=None, check_stop=lambda: None):
    with catalog_update_lock(data_dir):
        return _refresh_catalog(data_dir, songs_raw=songs_raw,
                                bands_raw=bands_raw, check_stop=check_stop)


def _refresh_catalog(data_dir, *, songs_raw, bands_raw, check_stop):
    check_stop()
    raw = fetch(SONGS_URL) if songs_raw is None else songs_raw
    check_stop()
    bands_raw = fetch(BANDS_URL) if bands_raw is None else bands_raw
    check_stop()
    now = datetime.now(timezone.utc)
    source_songs = json.loads(raw.decode('utf-8-sig'))
    bands = json.loads(bands_raw.decode('utf-8-sig'))
    songs = build_catalog(source_songs, bands, int(now.timestamp()*1000))
    recognition = build_recognition_catalog(source_songs, bands)
    observations = data_dir/'song_cn_verified_availability.json'
    if observations.exists():
        apply_verified_availability(songs, json.loads(observations.read_text(encoding='utf8')))
    metadata = {'fetched_at': now.isoformat(), 'sources': [SONGS_URL, BANDS_URL],
                'source_sha256': hashlib.sha256(raw).hexdigest(),
                'bands_source_sha256': hashlib.sha256(bands_raw).hexdigest()}
    catalog = dict(metadata, server='cn', server_index=CN, songs=songs)
    recognition_catalog = dict(metadata, scope='all_servers', songs=recognition)
    if not playable(songs):
        raise ValueError('Empty playable CN catalog; refusing to replace local data')
    check_stop()
    contents = {data_dir/'songs_cn.json': catalog, data_dir/'songs_all.json': recognition_catalog}
    replace_files({path: (json.dumps(value, ensure_ascii=False, indent=2)+'\n').encode('utf8')
                   for path, value in contents.items()})
    return catalog, recognition_catalog
