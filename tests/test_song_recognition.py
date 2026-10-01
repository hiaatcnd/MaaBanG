"""Regression coverage for tolerant OCR matching without ambiguous song choices."""
import csv
from pathlib import Path
import sys
import unittest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT/'agent'))
from online_policy import final_song
from song_catalog import BY_ID, RECOGNITION_BY_ID, TITLE_OCR_ALIASES, recognition_titles
from song_navigation import SongNavigationMixin, title_key


class SongRecognitionTests(unittest.TestCase):
    def test_all_server_only_song_matches_offline_with_all_its_names(self):
        from unittest.mock import patch
        song=next(s for sid,s in RECOGNITION_BY_ID.items()
                  if sid not in BY_ID and all(
                      sum(title_key(name) in {title_key(v) for v in recognition_titles(other)}
                          for other in RECOGNITION_BY_ID.values())==1
                      for name in recognition_titles(s)))
        with patch('urllib.request.urlopen',side_effect=AssertionError('matching must be offline')):
            for title in recognition_titles(song):
                with self.subTest(title=title):
                    self.assertEqual(final_song(title)['id'],song['id'])

    def test_ocr_punctuation_and_boundary_quotes_are_formatting(self):
        for title in ('“Say cheese!!!!!”', '"Say cheese!!!!!"”"', '“Say cheese!!!!!"', '“Say cheese”'):
            self.assertEqual(final_song(title)['id'], '646')
        for title in ('「僕は….」', '「僕は……」', '僕は...', '「僕は...」'):
            self.assertEqual(final_song(title)['id'], '595')
        self.assertEqual(final_song('COMIC PANIC')['title'], 'COMIC PANIC!!!')

    def test_small_ocr_errors_match_when_one_candidate_is_clear(self):
        from unittest.mock import patch
        songs={'a':{'id':'a','title':'Wonderful Melody','aliases':[], 'band':'A','band_aliases':[]},
               'b':{'id':'b','title':'Unrelated Anthem','aliases':[], 'band':'B','band_aliases':[]}}
        with patch('online_policy.RECOGNITION_BY_ID',songs):
            for title in ('Wonderfu1 Melody', 'Wonderul Melody', 'Womderful Me1ody'):
                self.assertEqual(final_song(title)['id'],'a')
            for title in ('Wonderful Melo', 'random unknown song', 'Wonderful'):
                with self.assertRaises(ValueError): final_song(title)

    def test_near_ties_and_punctuation_collisions_are_not_guessed(self):
        from unittest.mock import patch
        songs={str(i):{'id':str(i),'title':title,'aliases':[], 'band':str(i),'band_aliases':[]}
               for i,title in enumerate(('Melody dawn','Melody down','Spark!','Spark?'))}
        with patch('online_policy.RECOGNITION_BY_ID',songs):
            for title in ('Melody d0wn','Spark…'):
                with self.assertRaises(ValueError): final_song(title)
            self.assertEqual(final_song('Spark!')['id'],'2')
            self.assertEqual(final_song('Spark…','3')['id'],'3')
            self.assertEqual(final_song('Melody d0wn','1')['id'],'1')

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
        for title in ('Jump', 'CiRCLE THANKS', 'Singing OUR', 'Second to Non'):
            with self.subTest(title=title), self.assertRaises(ValueError):
                final_song(title)


if __name__ == '__main__':
    unittest.main()
