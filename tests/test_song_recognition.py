"""Regression coverage for screenshot-reviewed, exact song identities."""
import csv
from pathlib import Path
import sys
import unittest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT/'agent'))
from online_policy import final_song
from song_catalog import BY_ID, TITLE_OCR_ALIASES, recognition_titles
from song_navigation import SongNavigationMixin, title_key


class SongRecognitionTests(unittest.TestCase):
    def test_ocr_boundary_quotes_are_formatting_but_body_is_exact(self):
        for title in ('“Say cheese!!!!!”', '"Say cheese!!!!!"”"', '“Say cheese!!!!!"'):
            self.assertEqual(final_song(title)['id'], '646')
        with self.assertRaises(ValueError): final_song('“Say cheese”')

    def test_complete_device_audit_corpus(self):
        with (ROOT/'docs/data/free_song_recognition_audit.csv').open(encoding='utf-8-sig',newline='') as stream:
            rows = list(csv.DictReader(stream))
        self.assertEqual(len(rows), 736)
        self.assertEqual(len({r['song_id'] for r in rows}), len(rows))
        for row in rows:
            sid = row['song_id']
            with self.subTest(index=row['index'], song=sid):
                self.assertEqual(final_song(row['selected_ocr'], row['band_ocr'])['id'], sid)
                self.assertTrue(SongNavigationMixin().title_matches(row['selected_ocr'], BY_ID[sid]))
                if row['list_ocr']:
                    self.assertTrue(SongNavigationMixin().title_matches(row['list_ocr'], BY_ID[sid]))
                    self.assertEqual(final_song(row['list_ocr'], row['band_ocr'])['id'], sid)

    def test_reviewed_aliases_do_not_collide_with_other_song_names(self):
        for sid, variants in TITLE_OCR_ALIASES.items():
            for variant in variants:
                with self.subTest(song=sid, text=variant):
                    matches = {song['id'] for song in BY_ID.values()
                               if title_key(variant) in {title_key(v) for v in recognition_titles(song)}}
                    self.assertEqual(matches, {sid})
                    self.assertEqual(final_song(variant)['id'], sid)
                    self.assertTrue(SongNavigationMixin().title_matches(variant, BY_ID[sid]))

    def test_unreviewed_truncations_and_similar_titles_are_not_guessed(self):
        for title in ('Jump', 'CiRCLE THANKS', 'COMIC PANIC', 'Singing OUR', 'Second to Non'):
            with self.subTest(title=title), self.assertRaises(ValueError):
                final_song(title)


if __name__ == '__main__':
    unittest.main()
