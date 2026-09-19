"""Refresh China-server song metadata and dependent UI options from Bestdori."""
import argparse
import csv
from datetime import datetime, timezone
import hashlib
import io
import json
from pathlib import Path
from urllib.request import Request, urlopen

ROOT = Path(__file__).resolve().parents[1]
SONGS_URL = 'https://bestdori.com/api/songs/all.7.json'
BANDS_URL = 'https://bestdori.com/api/bands/all.1.json'
DIFFICULTIES = ('easy', 'normal', 'hard', 'expert', 'special')
CN = 3  # Bestdori's $servers: jp, en, tw, cn, kr.


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


def song_key(song, songs):
    return (song['title'] if sum(s['title'] == song['title'] for s in songs) == 1
            else f"{song['title']} [{song['id']}]")


def update_interface(interface, catalog):
    songs = playable(catalog)
    options = interface['option']
    for key in list(options):
        if key.startswith('歌曲难度_'):
            del options[key]
    cases = []
    for song in songs:
        names = [name for name in DIFFICULTIES
                 if song['difficulties'].get(name, {}).get('available')]
        profile = '歌曲难度_' + '_'.join(names)
        options[profile] = {
            'type': 'select', 'label': '演出难度', 'default_case': 'EXPERT',
            'cases': [{'name': name.upper(), 'pipeline_override': {
                'LV_Difficulty': {'attach': {'value': name}}}} for name in names],
        }
        cases.append({'name': song_key(song, songs),
                      'label': f"{song['title']} · {song['band']}",
                      'option': [profile],
                      'pipeline_override': {'LV_Song': {'attach': {'value': song_key(song, songs)}}}})
    options['演出歌曲'].update(cases=cases, default_case='SAVIOR OF SONG',
        label='演出歌曲',
        description='从已解锁歌曲选择，无需收藏；进入游戏后按所属乐队筛选查找。难度候选随歌曲变化，同名歌曲按乐队区分。')
    for key in list(options):
        if key.startswith('清火筛选_'):
            del options[key]
    for task in interface['task']:
        if task['entry'] == 'AutoLive':
            task['description']=task['description'].replace('从收藏选择歌曲','从全部已解锁歌曲按乐队筛选选歌，无需收藏')
            task['option'] = ['演出歌曲' if name in ('演出歌曲','清火筛选_乐队') else name
                              for name in task['option'] if name != '演出难度']
    return interface


def fetch(url):
    with urlopen(Request(url, headers={'User-Agent': 'MaaBanG-song-catalog/1.0'}), timeout=40) as response:
        return response.read()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--songs-file', type=Path)
    parser.add_argument('--bands-file', type=Path)
    args = parser.parse_args()
    now = datetime.now(timezone.utc)
    raw = args.songs_file.read_bytes() if args.songs_file else fetch(SONGS_URL)
    bands_raw = args.bands_file.read_bytes() if args.bands_file else fetch(BANDS_URL)
    songs = build_catalog(json.loads(raw.decode('utf-8-sig')),
                          json.loads(bands_raw.decode('utf-8-sig')), int(now.timestamp()*1000))
    payload = {'server': 'cn', 'server_index': CN, 'fetched_at': now.isoformat(),
               'sources': [SONGS_URL, BANDS_URL],
               'source_sha256': hashlib.sha256(raw).hexdigest(), 'songs': songs}
    interface_path = ROOT/'assets/interface.json'
    interface = update_interface(json.loads(interface_path.read_text(encoding='utf-8')), songs)
    out = ROOT/'agent/data/songs_cn.json'
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(payload, ensure_ascii=False, indent=2)+'\n', encoding='utf-8')
    # Import after writing the catalog so generated chart choices use the refresh.
    from update_chart_interface import update as update_chart_interface
    interface = update_chart_interface(interface)
    interface_path.write_text(json.dumps(interface, ensure_ascii=False, indent=4)+'\n', encoding='utf-8')
    csv_out = io.StringIO(newline='')
    writer = csv.writer(csv_out)
    writer.writerow(['ID', '中国服歌名', '乐队', '可用', '时长(秒)', *DIFFICULTIES, '来源'])
    for s in songs:
        writer.writerow([s['id'], s['title'], s['band'], s['active'], s['length_seconds'],
                         *[s['difficulties'].get(d, {}).get('level')
                           if s['difficulties'].get(d, {}).get('available') else ''
                           for d in DIFFICULTIES], s['url']])
    csv_path = ROOT/'docs/data/songs_cn.csv'
    csv_path.parent.mkdir(parents=True, exist_ok=True)
    csv_path.write_text(csv_out.getvalue(), encoding='utf-8-sig')
    print(f'CN released: {len(songs)}; active: {sum(s["active"] for s in songs)}; selectable: {len(playable(songs))}')


if __name__ == '__main__':
    main()
