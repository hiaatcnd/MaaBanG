"""Refresh CN song choices and the all-server recognition catalog from Bestdori."""
import argparse
import csv
import io
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
import sys
sys.path.insert(0, str(ROOT/'agent'))
from catalog_update import (SONGS_URL, BANDS_URL, DIFFICULTIES, CN, cn,
                            build_catalog, playable, build_recognition_catalog,
                            apply_verified_availability, song_key, refresh_catalog)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--songs-file', type=Path)
    parser.add_argument('--bands-file', type=Path)
    args = parser.parse_args()
    payload, recognition_payload = refresh_catalog(
        ROOT/'agent/data',
        songs_raw=args.songs_file.read_bytes() if args.songs_file else None,
        bands_raw=args.bands_file.read_bytes() if args.bands_file else None)
    songs = payload['songs']
    recognition_songs = recognition_payload['songs']
    csv_out = io.StringIO(newline='')
    writer = csv.writer(csv_out, lineterminator='\n')
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
    print(f'All-server recognition: {len(recognition_songs)}')


if __name__ == '__main__':
    main()
