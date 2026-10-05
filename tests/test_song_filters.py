import json,sys,tempfile,unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock,patch
import numpy as np
ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT/'agent'))
sys.path.insert(0,str(ROOT/'tools'))
from song_catalog import BY_ID,resolve_song
from auto_live import LiveFlow
from live_policy import LiveOptions
from song_navigation import BAND_BUTTONS,OTHER_BAND_BUTTON


class SongFilterTests(unittest.TestCase):
    def test_selected_song_fast_path_does_not_touch_filters(self):
        f=LiveFlow(SimpleNamespace(tasker=SimpleNamespace(controller=None,stopping=False)),LiveOptions.parse({}))
        f.wait=Mock();f.snap=Mock();f.tap=Mock();f.hit_text=Mock(return_value=object())
        f.selected_song_matches=Mock(return_value=True);f.all_songs=Mock()
        f.find_song(BY_ID['200'])
        f.tap.assert_not_called()
        f.find_song(BY_ID['200'])
        f.tap.assert_not_called()
        f.all_songs.assert_not_called()

    def test_failed_filter_cleanup_does_not_mark_it_complete(self):
        f=LiveFlow(SimpleNamespace(tasker=SimpleNamespace(controller=None,stopping=False)),LiveOptions.parse({}))
        f.wait=Mock();f.snap=Mock();f.tap=Mock();f.hit_text=Mock(return_value=None)
        with self.assertRaisesRegex(RuntimeError,'清理'):
            f.reset_inherited_song_filters()
        self.assertFalse(getattr(f,'_inherited_filters_cleared',False))

    def slider_flow(self):
        controller=Mock()
        for method in ('post_touch_down','post_touch_move','post_touch_up'):
            getattr(controller,method).return_value.wait.return_value.succeeded=True
        f=LiveFlow(SimpleNamespace(tasker=SimpleNamespace(controller=controller,stopping=False)),LiveOptions.parse({}))
        f.snap=Mock();f.pause=Mock()
        f.hit_text=Mock(return_value=SimpleNamespace(box=[697,307,125,30]))
        f.image=np.full((720,1280,3),255,dtype=np.uint8)
        for x in (760,1140):f.image[360:414,x:x+23]=238
        return f,controller

    def test_level_range_is_confirmed_only_when_both_ends_match(self):
        f,c=self.slider_flow()
        f.text=Mock(side_effect=['5','30','5','28','28','28'])
        f.filter_expert_level(28)
        self.assertEqual(f.report['song_filters'],[{'verified_range':[28,28]}])
        self.assertEqual(c.post_touch_down.call_count,2)
        self.assertEqual(c.post_touch_up.call_count,2)

    def test_failed_level_drag_releases_touch_and_does_not_confirm(self):
        f,c=self.slider_flow()
        f.text=Mock(side_effect=['5','30'])
        c.post_touch_move.return_value.wait.return_value.succeeded=False
        with self.assertRaisesRegex(RuntimeError,'拖动失败'):
            f.filter_expert_level(28)
        c.post_touch_up.assert_called_once()
        self.assertNotIn('song_filters',f.report)

    def test_oscillating_upper_bound_can_settle_in_outward_margin(self):
        f,c=self.slider_flow()
        f.text=Mock(side_effect=['5','30','5','23','5','25','23','25'])
        f.filter_expert_level(24)
        self.assertEqual([row['index'] for row in f.report['level_filter_steps'][:-1]],
                         [1,1,0])
        self.assertEqual(c.post_touch_down.call_count,3)
        self.assertEqual(f.report['song_filters'],[
            {'verified_range':[23,25],'target_range':[24,24],'tolerance':1}])

    def test_tolerance_never_excludes_target_or_accepts_broad_range(self):
        for bounds in ([23,23],[25,25],[22,25],[23,26]):
            with self.subTest(bounds=bounds):
                f,c=self.slider_flow()
                f.text=Mock(side_effect=[*map(str,bounds),'24','24'])
                f.filter_expert_level(24)
                self.assertEqual(c.post_touch_down.call_count,1)
                self.assertEqual(f.report['song_filters'],[{'verified_range':[24,24]}])

    def test_tolerance_is_clamped_at_game_level_limits(self):
        for target,bounds in ((5,[5,6]),(30,[29,30])):
            f,c=self.slider_flow()
            f.text=Mock(side_effect=list(map(str,bounds)))
            f.filter_expert_level(target)
            c.post_touch_down.assert_not_called()
            self.assertEqual(f.report['song_filters'][0]['verified_range'],bounds)

    def test_clearing_level_filter_still_requires_full_range(self):
        f,c=self.slider_flow()
        f.text=Mock(side_effect=['6','29','5','29','5','30'])
        f.set_song_level_range(5,30)
        self.assertEqual(c.post_touch_down.call_count,2)
        self.assertEqual(f.report['song_filters'],[{'verified_range':[5,30]}])

    def test_endpoint_drags_go_beyond_rail_and_require_ocr_confirmation(self):
        f,c=self.slider_flow()
        f.text=Mock(side_effect=['6','29','6','30','5','30'])
        f.set_song_level_range(5,30)
        self.assertEqual([step['x'] for step in f.report['level_filter_steps'][:-1]],
                         [1185,735])
        self.assertEqual(f.report['song_filters'],[{'verified_range':[5,30]}])
        self.assertEqual(c.post_touch_up.call_count,2)

    def test_slider_above_viewport_is_found_after_resetting_panel_scroll(self):
        f,c=self.slider_flow()
        f.swipe=Mock()
        f.hit_text=Mock(side_effect=[None,SimpleNamespace(box=[697,510,125,30]),
                                   SimpleNamespace(box=[697,307,125,30])])
        f.text=Mock(side_effect=['5','30'])
        f.set_song_level_range(5,30)
        self.assertEqual([call.args for call in f.swipe.call_args_list],
                         [(1200,200,550),(1200,200,550),(1200,550,290)])
        c.post_touch_down.assert_not_called()

    def test_missing_slider_never_drags_a_guessed_position(self):
        f,c=self.slider_flow()
        f.swipe=Mock();f.hit_text=Mock(return_value=None)
        with self.assertRaisesRegex(RuntimeError,'未定位乐曲等级'):
            f.set_song_level_range(5,30)
        self.assertEqual(f.swipe.call_count,7)
        c.post_touch_down.assert_not_called()

    def test_stalled_single_level_drag_increases_until_value_changes(self):
        for bounds,target,changed,direction in (([12,17],[12,18],[12,18],1),
                                               ([13,18],[12,18],[12,18],-1)):
            with self.subTest(bounds=bounds):
                f,c=self.slider_flow()
                f.text=Mock(side_effect=list(map(str,bounds*2+changed)))
                with patch('song_navigation.level_slider_handles',return_value=[890,970]):
                    f.set_song_level_range(*target)
                steps=f.report['level_filter_steps']
                self.assertEqual(steps[1]['x']-steps[0]['x'],direction*8)
                self.assertEqual(f.report['song_filters'],[{'verified_range':changed}])

    def test_unresponsive_slider_is_bounded_and_never_reported_as_success(self):
        f,c=self.slider_flow()
        f.text=Mock(side_effect=lambda roi:'6' if roi[0]==695 else '30')
        with self.assertRaisesRegex(RuntimeError,'未能将乐曲等级范围'):
            f.set_song_level_range(5,30)
        self.assertEqual(c.post_touch_down.call_count,20)
        self.assertEqual(c.post_touch_up.call_count,20)
        self.assertNotIn('song_filters',f.report)

    def test_stalled_near_endpoint_correction_is_not_clamped_inside_rail(self):
        f,c=self.slider_flow()
        f.text=Mock(side_effect=['7','7']*3+['6','6'])
        with patch('song_navigation.level_slider_handles',return_value=[800,833]):
            f.set_song_level_range(6,6)
        self.assertLess(f.report['level_filter_steps'][-2]['x'],772)
        f,c=self.slider_flow()
        f.text=Mock(side_effect=['28','28']*3+['29','29'])
        with patch('song_navigation.level_slider_handles',return_value=[1102,1136]):
            f.set_song_level_range(29,29)
        self.assertGreater(f.report['level_filter_steps'][-2]['x'],1152)

    def test_user_selects_songs_directly_without_filter_options(self):
        data=json.loads((ROOT/'assets/interface.json').read_text(encoding='utf-8'))
        self.assertFalse(any(key.startswith(('清火筛选_','谱面筛选')) for key in data['option']))
        auto=next(t for t in data['task'] if t['entry']=='AutoLive')
        self.assertIn('演出歌曲',auto['option'])
        self.assertEqual(data['option']['谱面演出模式']['cases'][0]['option'],
                         ['谱面第1首歌曲','谱面从收藏选择','谱面火不足策略'])

    def test_game_filter_uses_other_for_collaborations_and_resets_difficulty(self):
        for sid,button in [('96',BAND_BUTTONS[5]),('306',OTHER_BAND_BUTTON)]:
            f=LiveFlow(SimpleNamespace(tasker=SimpleNamespace(controller=None,stopping=False)),LiveOptions.parse({}))
            f.wait=Mock();f.snap=Mock();f.tap=Mock();f.tap_hit=Mock();f.hit_text=Mock(return_value=SimpleNamespace(box=[708,139,45,29]))
            f.image=np.zeros((720,1280,3),dtype=np.uint8)
            x,y=button
            f.image[y-25:y-17,x-42:x+42]=[80,60,255]
            f.image[532:549,703:720]=[80,60,255]
            f.filter_expert_level=Mock()
            f.all_songs(BY_ID[sid])
            f.filter_expert_level.assert_called_once_with(BY_ID[sid]['difficulties']['expert']['level'])
            self.assertEqual([call.args for call in f.tap.call_args_list],
                             [(1116,55),(1165,44),button,(711,540),(963,652)])

    def test_unconfirmed_band_never_starts_scrolling_song_list(self):
        f=LiveFlow(SimpleNamespace(tasker=SimpleNamespace(controller=None,stopping=False)),LiveOptions.parse({}))
        f.wait=Mock();f.snap=Mock();f.tap=Mock();f.tap_hit=Mock();f.hit_text=Mock(return_value=SimpleNamespace(box=[708,139,45,29]))
        f.image=np.zeros((720,1280,3),dtype=np.uint8)
        with self.assertRaisesRegex(RuntimeError,'乐队筛选'):
            f.all_songs(BY_ID['96'])
        self.assertNotIn((963,652),[call.args for call in f.tap.call_args_list])

    def test_partially_scrolled_band_header_is_not_ready_for_fixed_buttons(self):
        f=LiveFlow(SimpleNamespace(tasker=SimpleNamespace(controller=None,stopping=False)),LiveOptions.parse({}))
        f.wait=Mock();f.snap=Mock();f.tap=Mock();f.swipe=Mock();f.all_songs_category=Mock()
        f.hit_text=Mock(side_effect=[object(),SimpleNamespace(box=[707,104,46,30]),
                                     SimpleNamespace(box=[708,139,45,29])])
        f.image=np.zeros((720,1280,3),dtype=np.uint8)
        x,y=BAND_BUTTONS[BY_ID['96']['band_id']]
        f.image[y-25:y-17,x-42:x+42]=[80,60,255]
        f.image[532:549,703:720]=[80,60,255]
        f.filter_expert_level=Mock()
        f.all_songs(BY_ID['96'])
        f.swipe.assert_called_once_with(1200,200,550)
        f.filter_expert_level.assert_called_once_with(BY_ID['96']['difficulties']['expert']['level'])
