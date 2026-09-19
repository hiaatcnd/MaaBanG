import json,sys,tempfile,unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock
import numpy as np
ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT/'agent'))
sys.path.insert(0,str(ROOT/'tools'))
from song_catalog import BY_ID,resolve_song
from auto_live import LiveFlow
from live_policy import LiveOptions
from song_navigation import BAND_BUTTONS,OTHER_BAND_BUTTON


class SongFilterTests(unittest.TestCase):
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
        f.text=Mock(side_effect=['5','30','28','30','28','28'])
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

    def test_user_selects_songs_directly_without_filter_options(self):
        data=json.loads((ROOT/'assets/interface.json').read_text(encoding='utf-8'))
        self.assertFalse(any(key.startswith(('清火筛选_','谱面筛选')) for key in data['option']))
        auto=next(t for t in data['task'] if t['entry']=='AutoLive')
        self.assertIn('演出歌曲',auto['option'])
        self.assertEqual(data['option']['谱面演出模式']['cases'][0]['option'],['谱面第1首歌曲'])

    def test_game_filter_uses_other_for_collaborations_and_resets_difficulty(self):
        for sid,button in [('96',BAND_BUTTONS[5]),('306',OTHER_BAND_BUTTON)]:
            f=LiveFlow(SimpleNamespace(tasker=SimpleNamespace(controller=None,stopping=False)),LiveOptions.parse({}))
            f.wait=Mock();f.snap=Mock();f.tap=Mock();f.tap_hit=Mock();f.hit_text=Mock(return_value=object())
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
        f.wait=Mock();f.snap=Mock();f.tap=Mock();f.tap_hit=Mock();f.hit_text=Mock(return_value=object())
        f.image=np.zeros((720,1280,3),dtype=np.uint8)
        with self.assertRaisesRegex(RuntimeError,'乐队筛选'):
            f.all_songs(BY_ID['96'])
        self.assertNotIn((963,652),[call.args for call in f.tap.call_args_list])
