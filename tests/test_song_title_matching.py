"""Batch title matching retains per-song decisions without repeated catalog scans."""
from pathlib import Path
import sys
import unittest
from unittest.mock import patch

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT/'agent'))
from online_policy import final_song
from song_catalog import BY_ID, RECOGNITION_BY_ID, TITLE_OCR_ALIASES
from song_navigation import SongNavigationMixin


def song(sid,title,aliases=()):
    return {'id':sid,'title':title,'aliases':list(aliases),
            'band':sid,'band_aliases':[sid]}


class BatchTitleMatchingTests(unittest.TestCase):
    def setUp(self):
        self.flow=SongNavigationMixin()

    def test_exact_duplicate_titles_remain_ambiguous_in_candidate_order(self):
        songs={sid:song(sid,'Same title') for sid in ('a','b')}
        songs['c']=song('c','Unrelated Anthem')
        with patch('online_policy.RECOGNITION_BY_ID',songs):
            self.assertEqual(self.flow.matching_songs('Same title',reversed(list(songs.values()))),
                             [songs['b'],songs['a']])
            with self.assertRaises(ValueError):
                final_song('Same title')
            self.assertEqual(final_song('Same title','b'),songs['b'])

    def test_aliases_and_reviewed_ocr_variants_keep_their_song(self):
        songs={'a':song('a','Wonderful Melody',['Reviewed alias']),
               'b':song('b','Unrelated Anthem')}
        with patch('online_policy.RECOGNITION_BY_ID',songs), patch.dict(
                TITLE_OCR_ALIASES,{'a':['Reviewed OCR variant']}):
            for text in ('Reviewed alias','Reviewed OCR variant'):
                with self.subTest(text=text):
                    self.assertEqual(self.flow.matching_songs(text,songs.values()),[songs['a']])

    def test_fuzzy_match_still_uses_full_catalog_even_for_one_candidate(self):
        songs={sid:song(sid,title) for sid,title in (
            ('a','Melody dawn'),('b','Melody down'),('c','Wonderful Melody'))}
        with patch('online_policy.RECOGNITION_BY_ID',songs):
            self.assertEqual(self.flow.matching_songs('Melody d0wn',[songs['b']]),[])
            self.assertEqual(self.flow.matching_songs('Wonderfu1 Melody',[songs['a']]),[])
            self.assertEqual(self.flow.matching_songs('Wonderfu1 Melody',[songs['c']]),[songs['c']])
            for text in ('Wonderful','random unknown song'):
                self.assertEqual(self.flow.matching_songs(text,songs.values()),[])

    def test_punctuation_collision_is_not_guessed(self):
        songs={sid:song(sid,title) for sid,title in (('a','Spark!'),('b','Spark?'))}
        with patch('online_policy.RECOGNITION_BY_ID',songs):
            self.assertEqual(self.flow.matching_songs('Spark…',songs.values()),[])
            self.assertEqual(self.flow.matching_songs('Spark!',songs.values()),[songs['a']])

    def test_exact_candidate_and_different_global_fallback_both_remain(self):
        # Candidate metadata need not be the same objects as the global catalog.
        exact=song('local','Wonderful Melody')
        global_song=song('global','Wonderful Melody')
        candidates=[global_song,exact]
        with patch('online_policy.RECOGNITION_BY_ID',{'global':global_song}):
            # Change only the candidate copy, so its match comes from ID fallback.
            candidates[0]=song('global','Unrelated Anthem')
            expected=[s for s in candidates if self.flow.title_matches('Wonderful Melody',s)]
            self.assertEqual(self.flow.matching_songs('Wonderful Melody',candidates),expected)
            self.assertEqual(expected,candidates)

    def test_catalog_and_alias_changes_are_observed_without_stale_result_cache(self):
        songs={'a':song('a','Wonderful Melody'),'b':song('b','Unrelated Anthem')}
        with patch('online_policy.RECOGNITION_BY_ID',songs):
            self.assertEqual(self.flow.matching_songs('Wonderfu1 Melody',songs.values()),[songs['a']])
            songs['b']['title']='Wonderful Melody'
            self.assertEqual(self.flow.matching_songs('Wonderfu1 Melody',songs.values()),[])
            songs['a']['aliases'].append('New reviewed name')
            self.assertEqual(self.flow.matching_songs('New reviewed name',songs.values()),[songs['a']])

    def test_known_id_exact_verification_needs_no_global_lookup(self):
        with patch('online_policy.final_song',side_effect=AssertionError('Exact ID needs no scan')):
            self.assertTrue(self.flow.title_matches(BY_ID['306']['title'],BY_ID['306']))
            self.assertEqual(self.flow.matching_songs(BY_ID['306']['title'],[BY_ID['306']]),[BY_ID['306']])
            self.assertEqual(self.flow.matching_songs('unknown',[]),[])

    def test_real_catalog_fuzzy_title_resolves_once_for_all_candidates(self):
        with patch('online_policy.final_song',wraps=final_song) as resolve:
            result=self.flow.matching_songs('かほーんっと極楽☆湯～とぴあ！',RECOGNITION_BY_ID.values())
            self.assertEqual([s['id'] for s in result],['773'])
            resolve.assert_called_once_with('かほーんっと極楽☆湯～とぴあ！')

    def test_batch_matches_old_predicate_for_ambiguous_exact_and_fuzzy_inputs(self):
        songs={sid:song(sid,title) for sid,title in (
            ('a','Melody dawn'),('b','Melody down'),('c','Wonderful Melody'),
            ('d','Same title'),('e','Same title'))}
        with patch('online_policy.RECOGNITION_BY_ID',songs):
            for text in ('Melody dawn','Melody d0wn','Wonderfu1 Melody','Same title','unknown'):
                with self.subTest(text=text):
                    expected=[s for s in songs.values() if self.flow.title_matches(text,s)]
                    with patch('online_policy.final_song',wraps=final_song) as resolve:
                        self.assertEqual(self.flow.matching_songs(text,iter(songs.values())),expected)
                        self.assertEqual(resolve.call_count,1)


if __name__=='__main__':
    unittest.main()
