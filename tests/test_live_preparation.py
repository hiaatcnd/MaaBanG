"""Reuse observation-only preparation frames, retaining post-input boundaries."""
from pathlib import Path
import sys
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import Mock, patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'agent'))
from auto_live import LiveFlow
from chart_live import ChartLiveFlow
from chart_policy import ChartOptions
from costume_unlock import FlowError
from cp_live import CPLiveFlow
from live_policy import LiveOptions
from song_catalog import RECOGNITION_BY_ID


class LivePreparationTests(unittest.TestCase):
    def context(self):
        return SimpleNamespace(tasker=SimpleNamespace(
            controller=SimpleNamespace(info={}), stopping=False))

    def auto_flow(self):
        flow = LiveFlow(self.context(), LiveOptions.parse({'max_rounds': 1, 'fire': 1}))
        flow.snap = Mock()
        flow.dismiss_daily_reward = Mock(return_value=False)
        flow.reco = Mock(side_effect=lambda node: node in ('LV_FreeReady', 'LV_AutoOn'))
        flow.text = Mock(side_effect=lambda roi: 'EXPERT' if roi[0] == 113 else flow.options.song)
        flow.remaining = Mock(return_value=10)
        flow.fire_balance = Mock(return_value=10)
        flow.fire_preview = Mock(return_value=(10, 9))
        flow.tap = Mock()
        return flow

    def chart_flow(self, folder):
        flow = ChartLiveFlow(self.context(), ChartOptions.parse({'max_rounds': 1, 'fire': 1}), folder)
        state = {'title': flow.settings.selections[0].song['title'], 'balance': 10}
        flow.snap = Mock()
        flow.dismiss_daily_reward = Mock(return_value=False)
        flow.reco = Mock(side_effect=lambda node: node in ('LV_FreeReady', 'LV_AutoOff'))
        flow.text = Mock(side_effect=lambda roi: 'EXPERT' if roi[0] == 113 else state['title'])
        flow.fire_balance = Mock(side_effect=lambda: state['balance'])
        flow.fire_preview = Mock(return_value=(10, 9))
        flow.tap = Mock(side_effect=AssertionError('unchanged ready page needs no settings clicks'))
        flow.disable_mv = Mock()
        flow.save_frame = Mock()
        return flow, state

    def worker(self, *, change=None, destinations=None):
        def launch(args, **kwargs):
            destination = Path(args[-1]).parent
            if destinations is not None:
                destinations.append(destination)
            (destination / 'armed').write_text('ready')
            (destination / 'playback.json').write_text('{"status":"input_complete"}')
            if change:
                change()
            return SimpleNamespace(poll=lambda: 0, returncode=0)
        return launch

    def test_already_enabled_auto_uses_one_default_ready_capture(self):
        flow = self.auto_flow()
        self.assertEqual(flow.verify_start(1, 'expert', 1, 1), (10, 10))
        flow.snap.assert_called_once()
        flow.tap.assert_not_called()

    def test_verified_auto_frame_is_reused_for_identity_quota_and_cost(self):
        flow = self.auto_flow()
        flow.wait_ready()
        self.assertEqual(flow.verify_start(1, 'expert', 1, 1, fresh=False), (10, 10))
        flow.snap.assert_called_once()
        flow.fire_preview.assert_called_once()
        flow.tap.assert_not_called()

    def test_auto_toggle_still_refreshes_after_input(self):
        flow = self.auto_flow()
        state = {'auto': False}
        flow.reco.side_effect = lambda node: node == 'LV_FreeReady' or (
            node == ('LV_AutoOn' if state['auto'] else 'LV_AutoOff'))
        flow.tap.side_effect = lambda *args: state.update(auto=True)
        self.assertEqual(flow.verify_start(1, 'expert', 1, 1), (10, 10))
        self.assertEqual(flow.snap.call_count, 3)  # Initial, AutoOn, then ready.
        flow.tap.assert_called_once_with(883, 580)

    def test_auto_run_has_two_preparation_captures_on_unchanged_ready_page(self):
        flow = self.auto_flow()
        flow.prepare_round = Mock(side_effect=lambda: (flow.wait_ready(), 'expert')[1])
        flow.disable_mv = Mock()
        flow.wait_song = Mock()
        flow.settle_results = Mock()
        flow.home = Mock()
        flow.run()
        self.assertEqual(flow.snap.call_count, 2)
        flow.tap.assert_called_once_with(1127, 626)
        self.assertEqual(flow.report['completed_rounds'], 1)

    def test_reused_auto_frame_still_rejects_wrong_cost_before_submit(self):
        flow = self.auto_flow()
        flow.fire_preview.return_value = (10, 8)
        with self.assertRaisesRegex(FlowError, '火数预览'):
            flow.verify_start(1, 'expert', 1, 1, fresh=False)
        flow.tap.assert_not_called()

    def test_refill_defaults_to_fresh_but_can_reuse_just_checked_balance(self):
        with tempfile.TemporaryDirectory() as folder:
            flow, _ = self.chart_flow(folder)
            self.assertTrue(flow.refill_fire(1))
            flow.snap.assert_called_once()
            self.assertTrue(flow.refill_fire(1, fresh=False))
            flow.snap.assert_called_once()
            flow.tap.assert_not_called()

    def test_cp_song_reads_one_verified_frame_and_refreshes_after_selection(self):
        for already_selected in (True, False):
            with self.subTest(already_selected=already_selected), tempfile.TemporaryDirectory() as folder:
                flow = CPLiveFlow(self.context(), ChartOptions.parse({'mode': 'challenge', 'cp_song': '306'}), folder)
                selected = {'song': RECOGNITION_BY_ID['306' if already_selected else '91']}
                flow.snap = Mock()
                flow.reco = Mock(side_effect=lambda node: node in ('CP_Select', 'CP_Header'))
                flow.text = Mock(side_effect=lambda roi: selected['song']['title' if roi[1] == 329 else 'band'])
                button = SimpleNamespace(text=RECOGNITION_BY_ID['306']['title'])
                flow.ocr = Mock(return_value=[button])
                flow.tap_hit = Mock(side_effect=lambda hit: selected.update(song=RECOGNITION_BY_ID['306']))
                self.assertEqual(flow.select_event_song()['id'], '306')
                self.assertEqual(flow.snap.call_count, 1 if already_selected else 2)
                self.assertEqual(flow.tap_hit.call_count, 0 if already_selected else 1)

    def test_cp_song_rejects_changed_page_before_title_or_input(self):
        with tempfile.TemporaryDirectory() as folder:
            flow = CPLiveFlow(self.context(), ChartOptions.parse({'mode': 'challenge', 'cp_song': '306'}), folder)
            flow.snap = Mock()
            flow.reco = Mock(side_effect=lambda node: node == 'CP_Select')
            flow.text = Mock()
            flow.tap_hit = Mock()
            with self.assertRaisesRegex(FlowError, '选曲页面'):
                flow.select_event_song()
            flow.snap.assert_called_once()
            flow.text.assert_not_called()
            flow.tap_hit.assert_not_called()

    def test_chart_run_keeps_four_captures_and_fresh_final_checks(self):
        with tempfile.TemporaryDirectory() as folder:
            flow, _ = self.chart_flow(folder)
            flow.prepare_round = Mock(side_effect=lambda: (
                flow.wait_ready(), (flow.settings.selections, [(None, {'duration': 1})]))[1])
            flow.await_chart_result = Mock()
            flow.settle_results = Mock()
            flow.home = Mock()
            destinations = []
            with patch('chart_live.subprocess.Popen', side_effect=self.worker(destinations=destinations)):
                flow.run()
            self.assertEqual(flow.snap.call_count, 4)
            self.assertEqual(flow.report['completed_rounds'], 1)
            self.assertTrue((destinations[0] / 'start').exists())
            flow.fire_preview.assert_called_once()

    def test_worker_preparation_cannot_reuse_old_song_or_balance(self):
        for change, error in (({'title': 'Jumpin\''}, '歌曲与谱面'), ({'balance': 0}, '火数预览')):
            with self.subTest(change=change), tempfile.TemporaryDirectory() as folder:
                flow, state = self.chart_flow(folder)
                # Only captures expose the changed device state to recognition.
                observed = state.copy()
                flow.snap.side_effect = lambda: observed.update(state)
                flow.text.side_effect = lambda roi: 'EXPERT' if roi[0] == 113 else observed['title']
                flow.fire_balance.side_effect = lambda: observed['balance']
                destinations = []
                with patch('chart_live.subprocess.Popen', side_effect=self.worker(
                        change=lambda: state.update(change), destinations=destinations)):
                    with self.assertRaisesRegex(FlowError, error):
                        flow.play_chart(1, flow.settings.selections[0], {'duration': 1}, 1, {})
                self.assertEqual(flow.snap.call_count, 2)
                self.assertFalse((destinations[0] / 'start').exists())
                flow.disable_mv.assert_not_called()


if __name__ == '__main__':
    unittest.main()
