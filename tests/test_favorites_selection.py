"""Favorites keeps ordinary filters; only an already selected song skips them."""
import json
from pathlib import Path
import sys
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import Mock

import numpy as np

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT/'agent'))
from auto_live import LiveFlow, OPTION_NODES as AUTO_NODES
from chart_live import ChartLiveFlow
from chart_policy import ChartOptions, ChartSelection, OPTION_NODES as CHART_NODES
from costume_unlock import FlowError
from live_policy import LiveOptions
from online_live import OnlineLiveFlow
from song_catalog import BY_ID
from song_navigation import BAND_BUTTONS


class FavoritesSelectionTests(unittest.TestCase):
    def flow(self, favorites=False):
        return LiveFlow(SimpleNamespace(tasker=SimpleNamespace(controller=None,stopping=False)),
                        LiveOptions.parse({'song':'96','difficulty':'easy','from_favorites':favorites}))

    def test_ui_choices_reach_runtime_and_default_to_all_songs(self):
        data=json.loads((ROOT/'assets/interface.json').read_text(encoding='utf8'))
        for name,parser,nodes,file in (
                ('演出从收藏选择',LiveOptions,AUTO_NODES,'live'),
                ('谱面从收藏选择',ChartOptions,CHART_NODES,'chart_live')):
            option=data['option'][name]
            self.assertEqual(option['default_case'],'关')
            pipeline=json.loads((ROOT/f'assets/resource/pipeline/{file}.json').read_text(encoding='utf8'))
            self.assertFalse(pipeline[nodes['from_favorites']]['attach']['value'])
            self.assertFalse(parser.parse({}).from_favorites)
            for case in option['cases']:
                value=case['pipeline_override'][nodes['from_favorites']]['attach']['value']
                self.assertEqual(parser.parse({'from_favorites':value}).from_favorites,case['name']=='开')
            for invalid in ('false','off',1,None):
                with self.assertRaises(ValueError):
                    parser.parse({'from_favorites':invalid})

    def test_current_target_confirms_without_any_filter_for_both_tasks(self):
        for chart in (False,True):
            for favorites in (False,True):
                with self.subTest(chart=chart,favorites=favorites),tempfile.TemporaryDirectory() as folder:
                    if chart:
                        flow=ChartLiveFlow(SimpleNamespace(tasker=SimpleNamespace(controller=None,stopping=False)),
                            ChartOptions.parse({'song1':'96','difficulty1':'easy','from_favorites':favorites}),folder)
                    else:
                        flow=self.flow(favorites)
                    flow.wait=Mock()
                    flow.selected_difficulty=Mock(return_value='easy')
                    flow.selected_song_matches=Mock(return_value=True)
                    flow.all_songs=Mock();flow.favorites_category=Mock()
                    flow.reset_inherited_song_filters=Mock();flow.clear_song_level_filter=Mock()
                    flow.find_filtered_song=Mock();flow.tap=Mock()
                    flow.choose_difficulty=Mock(return_value='easy');flow.choose_difficulty_exact=Mock()
                    if chart:
                        flow.select_song(ChartSelection('96','easy'))
                        flow.choose_difficulty_exact.assert_called_once_with('easy')
                    else:
                        self.assertEqual(flow.choose_song(),'easy')
                    for name in ('all_songs','favorites_category','reset_inherited_song_filters',
                                 'clear_song_level_filter','find_filtered_song'):
                        getattr(flow,name).assert_not_called()
                    flow.tap.assert_called_once_with(1070,648)

    def test_difficulty_change_cannot_confirm_a_different_song(self):
        flow=self.flow(True)
        flow.wait=Mock();flow.selected_song_matches=Mock(side_effect=[True,False])
        flow.selected_difficulty=Mock(return_value='easy')
        flow.choose_difficulty=Mock(return_value='easy');flow.tap=Mock()
        with self.assertRaisesRegex(FlowError,'选中歌曲发生变化'):
            flow.choose_song()
        flow.tap.assert_not_called()

    def test_requested_difficulty_is_selected_before_checking_current_song(self):
        flow=self.flow(True)
        events=[]
        flow.wait=Mock();flow.tap=Mock(side_effect=lambda *args:events.append(('tap',args)))
        flow.selected_difficulty=Mock(return_value='expert')
        flow.selected_song_matches=Mock(side_effect=lambda song:events.append(('check',song['id'])) or True)
        flow.all_songs=Mock()
        self.assertFalse(flow.find_song(BY_ID['96'],difficulty='easy'))
        flow.all_songs.assert_not_called()
        self.assertEqual(events,[('tap',(714,540)),('check','96')])

    def test_hidden_target_after_preselect_uses_normal_search_without_restoring_difficulty(self):
        flow=self.flow(True)
        flow.wait=Mock();flow.tap=Mock()
        flow.selected_song_matches=Mock(return_value=False)
        flow.selected_difficulty=Mock(return_value='expert')
        flow.all_songs=Mock();flow.find_filtered_song=Mock()
        self.assertTrue(flow.find_song(BY_ID['96'],difficulty='easy'))
        flow.all_songs.assert_called_once_with(BY_ID['96'],quick=False,from_favorites=True)
        flow.tap.assert_called_once_with(714,540)

    def test_favorites_and_all_songs_apply_same_band_and_level_filters(self):
        for favorites in (False,True):
            with self.subTest(favorites=favorites):
                flow=self.flow(favorites)
                flow.wait=Mock();flow.snap=Mock();flow.tap=Mock()
                flow.selected_song_matches=Mock(return_value=False)
                flow.all_songs_category=Mock();flow.favorites_category=Mock()
                flow.hit_text=Mock(return_value=SimpleNamespace(box=[708,139,45,29]));flow.find_filtered_song=Mock()
                flow.filter_expert_level=Mock()
                flow.image=np.zeros((720,1280,3),dtype=np.uint8)
                x,y=BAND_BUTTONS[BY_ID['96']['band_id']]
                flow.image[y-25:y-17,x-42:x+42]=[80,60,255]
                flow.image[532:549,703:720]=[80,60,255]
                self.assertTrue(flow.find_song(BY_ID['96']))
                selected=flow.favorites_category if favorites else flow.all_songs_category
                other=flow.all_songs_category if favorites else flow.favorites_category
                selected.assert_called_once_with(quick=False);other.assert_not_called()
                self.assertEqual([c.args for c in flow.tap.call_args_list],
                                 [(1116,55),(1165,44),(x,y),(711,540),(963,652)])
                flow.filter_expert_level.assert_called_once_with(BY_ID['96']['difficulties']['expert']['level'])
                self.assertEqual(flow.find_filtered_song.call_args.kwargs['from_favorites'],favorites)

    def test_favorites_nonexpert_search_still_clears_new_level_filter(self):
        flow=self.flow(True)
        flow.wait=Mock();flow.selected_song_matches=Mock(side_effect=[False,True])
        flow.selected_difficulty=Mock(return_value='easy')
        flow.all_songs=Mock();flow.find_filtered_song=Mock();flow.clear_song_level_filter=Mock()
        flow.choose_difficulty=Mock(return_value='easy');flow.tap=Mock()
        flow.choose_song()
        flow.all_songs.assert_called_once_with(BY_ID['96'],quick=False,from_favorites=True)
        flow.clear_song_level_filter.assert_called_once_with(BY_ID['96'])
        flow.tap.assert_called_once_with(1070,648)

    def test_coop_search_passes_favorites_and_countdown_limits(self):
        for favorites in (False,True):
            with self.subTest(favorites=favorites),tempfile.TemporaryDirectory() as folder:
                flow=OnlineLiveFlow(SimpleNamespace(tasker=SimpleNamespace(controller=None,stopping=False)),
                    ChartOptions.parse({'mode':'coop','coop_song':'361','from_favorites':favorites}),folder)
                flow.current_attempt={};flow.wait=Mock();flow.save_frame=Mock()
                flow.selected_song_matches=Mock(side_effect=[False,True])
                flow.all_songs=Mock();flow.find_filtered_song=Mock()
                flow.reco=Mock(return_value=True);flow.pink=Mock(return_value=True)
                flow.image=np.zeros((720,1280,3),dtype=np.uint8)
                button=object()
                flow.hit_text=Mock(side_effect=lambda roi,pattern:button if pattern in ('^确定$','^不指定歌曲$') else None)
                flow.tap_hit=Mock()
                self.assertTrue(flow.submit_coop_song())
                flow.all_songs.assert_called_once_with(BY_ID['361'],quick=True,from_favorites=favorites)
                flow.find_filtered_song.assert_called_once_with(BY_ID['361'],max_steps=8,
                    forward_first=True,quick=True,from_favorites=favorites)
                flow.tap_hit.assert_called_once_with(button)

    def test_missing_favorite_never_falls_back_to_all_songs(self):
        flow=self.flow(True)
        flow.wait=Mock();flow.selected_song_matches=Mock(return_value=False)
        flow.all_songs=Mock();flow.ocr=Mock(return_value=[]);flow.step_song_list=Mock()
        flow.image=np.zeros((720,1280,3),dtype=np.uint8)
        with self.assertRaisesRegex(FlowError,'已收藏并解锁'):
            flow.find_song(BY_ID['96'],max_steps=2)
        flow.all_songs.assert_called_once_with(BY_ID['96'],quick=False,from_favorites=True)

    def test_leaving_favorites_drags_a_visible_category_instead_of_a_row_gap(self):
        flow=self.flow()
        flow.wait=Mock();flow.tap_hit=Mock()
        main=SimpleNamespace(box=[9,120,58,37])
        label=SimpleNamespace(box=[13,236,95,28])
        moved=[False]
        def hit(roi,pattern):
            if pattern=='^所有$':return main if moved[0] else None
            if pattern=='.+':return label
            return None
        def swipe(x,y,end):
            self.assertEqual((x,y,end),(90,250,650))
            moved[0]=True
        flow.hit_text=Mock(side_effect=hit);flow.swipe=Mock(side_effect=swipe)
        flow.all_songs_category()
        flow.swipe.assert_called_once()
        flow.tap_hit.assert_called_once_with(main)


if __name__=='__main__':unittest.main()
