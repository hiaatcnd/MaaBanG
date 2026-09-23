import json
from pathlib import Path
import sys
import unittest
from types import SimpleNamespace
from unittest.mock import Mock

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT/'agent'))
sys.path.insert(0,str(ROOT/'tools'))
from song_catalog import BY_ID, BY_KEY, resolve_song, available_difficulties
from live_policy import LiveOptions
from auto_live import LiveFlow
from update_song_catalog import build_catalog, apply_verified_availability


class SongCatalogTests(unittest.TestCase):
    def test_verified_availability_only_fills_missing_dates_for_exact_active_song(self):
        from copy import deepcopy
        source = {'id':'690', 'title':'Second to None', 'band_id':5, 'active':True,
                  'difficulties':{'expert':{'level':27, 'published_at':None, 'available':False}}}
        proof = {'690':{'title':'Second to None', 'band_id':5,
                        'levels':{'expert':27}, 'verified_at':'2026-09-23'}}
        result = apply_verified_availability([deepcopy(source)], proof)[0]
        self.assertTrue(result['difficulties']['expert']['available'])
        self.assertIsNone(result['difficulties']['expert']['published_at'])
        closed = deepcopy(source); closed['active'] = False
        self.assertFalse(apply_verified_availability([closed],proof)[0]['difficulties']['expert']['available'])
        future = deepcopy(source); future['difficulties']['expert']['published_at'] = 9999999999999
        self.assertFalse(apply_verified_availability([future],proof)[0]['difficulties']['expert']['available'])
        changed = deepcopy(source); changed['difficulties']['expert']['level'] = 28
        with self.assertRaises(ValueError): apply_verified_availability([changed],proof)

    def test_cn_release_dates_and_special_are_independent(self):
        song={'bandId':1,'musicTitle':['JP',None,None,'CN',None],
              'publishedAt':['1',None,None,'20',None],
              'closedAt':[None]*5,'difficulty':{
                  '3':{'playLevel':26},
                  '4':{'playLevel':27,'publishedAt':['1',None,None,'200',None]}}}
        future=dict(song,publishedAt=['1',None,None,'500',None])
        closed=dict(song,closedAt=[None,None,None,'50',None])
        jp_only=dict(song,publishedAt=['1',None,None,None,None])
        result=build_catalog({'1':song,'2':future,'3':closed,'4':jp_only},{},100)
        self.assertEqual([s['id'] for s in result],['1','3'])
        self.assertEqual(result[0]['title'],'CN')
        self.assertTrue(result[0]['difficulties']['expert']['available'])
        self.assertFalse(result[0]['difficulties']['special']['available'])
        self.assertFalse(result[1]['active'])

    def test_all_song_cases_offer_only_their_cn_difficulties(self):
        interface=json.loads((ROOT/'assets/interface.json').read_text(encoding='utf-8'))
        task=next(t for t in interface['task'] if t['entry']=='AutoLive')
        self.assertNotIn('演出难度',task['option'])
        cases=interface['option']['演出歌曲']['cases']
        self.assertEqual({c['name'] for c in cases},set(BY_KEY))
        for case in cases:
            song=resolve_song(case['pipeline_override']['LV_Song']['attach']['value'])
            option=interface['option'][case['option'][0]]
            difficulties={c['pipeline_override']['LV_Difficulty']['attach']['value'] for c in option['cases']}
            self.assertEqual(difficulties,set(available_difficulties(song)))
            for difficulty in difficulties:
                parsed=LiveOptions.parse({'song':case['name'],'difficulty':difficulty})
                self.assertEqual(parsed.song_id,song['id'])
                self.assertEqual(parsed.difficulty,difficulty)

    def test_legacy_special_falls_back_and_duplicates_require_identity(self):
        self.assertNotIn('special',available_difficulties(resolve_song('EXIST')))
        self.assertIn('special',available_difficulties(resolve_song('SAVIOR OF SONG')))
        self.assertEqual(LiveOptions.parse({'song':'EXIST','difficulty':'special'}).difficulty,'expert')
        with self.assertRaises(ValueError): resolve_song('オレンジ')
        self.assertNotEqual(resolve_song('316')['band_id'],resolve_song('676')['band_id'])

    def test_same_title_wrong_band_is_skipped_before_selection(self):
        options=LiveOptions.parse({'song':'676'})
        f=LiveFlow(SimpleNamespace(tasker=SimpleNamespace(controller=None,stopping=False)),options)
        f.wait=Mock(); f.tap=Mock(); f.all_songs=Mock(); f.tap_hit=Mock()
        wrong=SimpleNamespace(text=options.song); correct=SimpleNamespace(text=options.song)
        f.ocr=Mock(return_value=[wrong,correct])
        f.text=Mock(side_effect=['not selected',options.song,'wrong band',options.song,BY_ID['676']['band']])
        f.choose_difficulty=Mock(return_value='expert')
        self.assertEqual(f.choose_song(),'expert')
        self.assertEqual(f.tap_hit.call_count,2)
        f.choose_difficulty.assert_called_once()


if __name__=='__main__': unittest.main()
