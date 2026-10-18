"""Submit the room's current selection without looking up or changing songs."""
from pathlib import Path
import sys
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import Mock

import numpy as np

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT/'agent'))
from chart_policy import ChartOptions, CURRENT_SONG
from online_live import OnlineLiveFlow


class CurrentSongTests(unittest.TestCase):
    def flow(self,mode):
        folder=tempfile.TemporaryDirectory()
        self.addCleanup(folder.cleanup)
        flow=OnlineLiveFlow(SimpleNamespace(tasker=SimpleNamespace(controller=None,stopping=False)),
                            ChartOptions.parse({'mode':mode,mode+'_song':CURRENT_SONG,
                                                'versus_room':'free','from_favorites':True}),folder.name)
        flow.current_attempt={}
        flow.image=np.zeros((720,1280,3),dtype=np.uint8)
        flow.snap=Mock();flow.save_frame=Mock();flow.tap_hit=Mock()
        flow.find_song=Mock(side_effect=AssertionError('Current selection must not be changed'))
        flow.selected_song_matches=Mock(side_effect=AssertionError('No list-title OCR in current mode'))
        flow.navigate_menu=Mock();flow.open_page=Mock()
        flow.reco=Mock(side_effect=lambda name:name=='OL_CoopSongPage')
        flow.pink=Mock(return_value=True)
        flow.confirm_button=object()
        flow.hit_text=Mock(side_effect=lambda roi,pattern:
                           flow.confirm_button if pattern=='^确定$' else
                           object() if pattern=='^不指定歌曲$' else None)
        return flow

    def test_both_modes_skip_preparation_and_submit_current_selection_once(self):
        for mode in ('coop','versus'):
            with self.subTest(mode=mode):
                flow=self.flow(mode)
                self.assertEqual(flow.requested_song,CURRENT_SONG)
                flow.prepare_settings()
                flow.navigate_menu.assert_not_called();flow.open_page.assert_not_called()
                self.assertTrue(flow.submit_coop_song())
                self.assertTrue(flow.submit_coop_song())
                flow.tap_hit.assert_called_once_with(flow.confirm_button)
                self.assertEqual(flow.current_attempt['submitted_song'],CURRENT_SONG)
                flow.find_song.assert_not_called();flow.selected_song_matches.assert_not_called()

    def test_loading_or_missing_buttons_wait_without_submission(self):
        for mode in ('coop','versus'):
            for loading in (False,True):
                with self.subTest(mode=mode,loading=loading):
                    flow=self.flow(mode)
                    flow.ready_loading=Mock(return_value=loading)
                    flow.hit_text=Mock(return_value=None)
                    self.assertFalse(flow.submit_coop_song())
                    flow.tap_hit.assert_not_called()
                    self.assertFalse(flow.coop_song_submitted)

    def test_unconfirmed_page_or_disabled_confirmation_never_submits(self):
        for mode in ('coop','versus'):
            for failure in ('page','button','color'):
                with self.subTest(mode=mode,failure=failure):
                    flow=self.flow(mode)
                    if failure=='page':
                        flow.reco=Mock(return_value=False)
                    elif failure=='button':
                        flow.hit_text=Mock(side_effect=lambda roi,pattern:object() if pattern=='^不指定歌曲$' else None)
                    else:
                        flow.pink=Mock(return_value=False)
                    with self.assertRaisesRegex(RuntimeError,'未确认所选歌曲|确认按钮不可用'):
                        flow.submit_coop_song()
                    flow.tap_hit.assert_not_called()
                    self.assertFalse(flow.coop_song_submitted)

    def test_final_page_requires_current_selection_to_have_been_submitted(self):
        for mode in ('coop','versus'):
            with self.subTest(mode=mode):
                flow=self.flow(mode)
                flow.is_final_page=Mock(return_value=True)
                with self.assertRaisesRegex(RuntimeError,'未确认提交指定歌曲'):
                    flow.await_final()
                flow.coop_song_submitted=True
                flow.await_final()
                self.assertEqual(flow.report['state'],'确认最终歌曲和难度')

    def test_event_room_ignores_current_song_and_favorites(self):
        with tempfile.TemporaryDirectory() as folder:
            flow=OnlineLiveFlow(SimpleNamespace(tasker=SimpleNamespace(controller=None,stopping=False)),
                                ChartOptions.parse({'mode':'versus','versus_room':'event',
                                                    'versus_song':CURRENT_SONG,'from_favorites':True}),folder)
            self.assertEqual(flow.requested_song,'')
            self.assertFalse(flow.chooses_song)
            flow.navigate_menu=Mock();flow.find_song=Mock()
            flow.prepare_settings()
            flow.navigate_menu.assert_not_called();flow.find_song.assert_not_called()


if __name__=='__main__':
    unittest.main()
