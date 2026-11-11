"""Reuse FC observations without crossing capture, input or worker boundaries.

OCR and star classification are mocked; the production capture/invalidation and
playback handshakes run against a fake controller and fake worker, never a game.
"""
import contextlib
import io
import json
from pathlib import Path
import sys
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import Mock, call, patch

import numpy as np

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'agent'))
from chart_live import ChartLiveFlow
from chart_policy import ChartOptions, ChartSelection
from costume_unlock import FlowError
from full_combo import ONLY_UNFC
from online_live import OnlineLiveFlow
from song_catalog import BY_ID


FC_ROW = (207, ('full_combo', 'unplayed', 'full_combo', 'full_combo', 'unknown'))
CLEAR_ROW = (207, ('full_combo', 'unplayed', 'full_combo', 'clear', 'unknown'))
SELECTION = ChartSelection('91', 'expert')


class OnlineFullComboReuseTests(unittest.TestCase):
    def setUp(self):
        self.folder = tempfile.TemporaryDirectory()
        self.addCleanup(self.folder.cleanup)
        self.now = 10.
        self.events = []
        self.created = 0
        clock = patch('online_live.time.monotonic', side_effect=lambda: self.now)
        clock.start()
        self.addCleanup(clock.stop)
        sleep = patch('costume_unlock.time.sleep')
        sleep.start()
        self.addCleanup(sleep.stop)
        stars = patch('online_live.ready_star_row', return_value=FC_ROW)
        self.stars = stars.start()
        self.addCleanup(stars.stop)

    def flow(self, *, mode='coop', policy=ONLY_UNFC):
        self.created += 1
        job = SimpleNamespace(wait=Mock(return_value=SimpleNamespace(succeeded=True)))
        controller = SimpleNamespace(info={}, cached_image=np.empty((720, 1280, 3), dtype=np.uint8),
            post_click=Mock(return_value=job), post_swipe=Mock(return_value=job))
        def capture():
            self.events.append('snap')
            return job
        controller.post_screencap = Mock(side_effect=capture)
        context = SimpleNamespace(tasker=SimpleNamespace(controller=controller, stopping=False))
        flow = OnlineLiveFlow(context, ChartOptions.parse({'mode': mode, 'fire': 0,
                              'avoid_full_combo': policy}), Path(self.folder.name) / str(self.created))
        flow.current_attempt = {'attempt': 1}
        flow.report['attempts'] = 1
        flow.image = controller.cached_image
        flow.reco = Mock(return_value=None)  # No fatal dialog on fake captures.
        flow.room_error = Mock(return_value=None)
        flow.is_final_page = Mock(return_value=True)
        flow.text = Mock(return_value=BY_ID['91']['title'])
        flow.ocr = Mock(return_value=[SimpleNamespace(text='EXPERT', box=[780, 570, 80, 40])])
        flow.difficulty_selected = Mock(return_value=True)
        flow.snap = Mock(wraps=flow.snap)
        def pause(seconds):
            self.events.append(('wait', seconds))
            self.now += seconds
        flow.pause = Mock(side_effect=pause)
        return flow

    def discover(self, flow):
        self.assertEqual(flow.read_final_selection(), SELECTION)
        self.assertEqual(flow.snap.call_count, 2)
        flow.snap.reset_mock()
        flow.controller.post_screencap.reset_mock()
        flow.pause.reset_mock()
        self.events.clear()

    def test_two_song_samples_supply_initial_fc_decision_once_without_more_captures(self):
        flow = self.flow()
        self.discover(flow)
        with contextlib.redirect_stdout(io.StringIO()):
            row = {}
            self.assertFalse(flow.resolve_avoid_full_combo(SELECTION, row))
        self.assertEqual(row['full_combo_check']['state'], 'full_combo')
        flow.snap.assert_not_called()
        self.assertEqual(flow.read_full_combo_state(SELECTION), 'full_combo')
        self.assertEqual(flow.snap.call_count, 2, 'The proof must be consumed only once')
        flow.pause.assert_called_once_with(.12)

    def test_unstable_or_missing_song_star_samples_fall_back_to_two_new_frames(self):
        for rows in ((FC_ROW, CLEAR_ROW), (None, FC_ROW), (FC_ROW, None)):
            with self.subTest(rows=rows):
                flow = self.flow()
                self.stars.side_effect = rows
                self.discover(flow)
                self.stars.side_effect = None
                self.assertEqual(flow.read_full_combo_state(SELECTION), 'full_combo')
                self.assertEqual(flow.snap.call_count, 2)

    def test_known_song_single_read_does_not_create_two_sample_proof(self):
        flow = self.flow()
        flow.selection = SELECTION
        self.assertEqual(flow.read_final_song(expected_id='91')['id'], '91')
        self.assertEqual(flow.snap.call_count, 1)
        flow.snap.reset_mock()
        self.assertEqual(flow.read_full_combo_state(SELECTION), 'full_combo')
        self.assertEqual(flow.snap.call_count, 2)

    def test_failed_capture_discards_previously_stable_proof(self):
        flow = self.flow()
        self.discover(flow)
        failed = SimpleNamespace(wait=Mock(return_value=SimpleNamespace(succeeded=False)))
        original = flow.controller.post_screencap.side_effect
        flow.controller.post_screencap.side_effect = lambda: failed
        with self.assertRaisesRegex(FlowError, '截图失败'):
            flow.snap()
        flow.controller.post_screencap.side_effect = original
        flow.snap.reset_mock()
        self.assertEqual(flow.read_full_combo_state(SELECTION), 'full_combo')
        self.assertEqual(flow.snap.call_count, 2)

    def test_ambiguous_catalog_identity_cannot_authorize_fc_exemption(self):
        flow = self.flow()
        with patch('online_live.final_song', side_effect=ValueError('ambiguous title')):
            self.assertEqual(flow.read_full_combo_state(SELECTION, fresh=False), 'unknown')
        self.assertEqual(flow.snap.call_count, 3)
        flow.controller.post_click.assert_not_called()

    def test_expired_or_negative_age_proof_is_rejected(self):
        for elapsed, captures in ((0., 0), (1., 0), (1.001, 2), (-.001, 2)):
            with self.subTest(elapsed=elapsed):
                flow = self.flow()
                self.discover(flow)
                self.now += elapsed
                self.assertEqual(flow.read_full_combo_state(SELECTION), 'full_combo')
                self.assertEqual(flow.snap.call_count, captures)

    def test_capture_input_image_attempt_and_selection_changes_invalidate_proof(self):
        mutations = {
            'capture-reusing-same-buffer': lambda flow: flow.snap(),
            'tap': lambda flow: flow.tap(1, 2),
            'swipe': lambda flow: flow.swipe(1, 2, 3),
            'quick-tap': lambda flow: flow.quick_tap(1, 2),
            'quick-swipe': lambda flow: flow.quick_swipe(1, 2, 3),
            'image-replacement': lambda flow: setattr(flow, 'image', flow.image.copy()),
            'different-attempt-with-equal-value': lambda flow: setattr(flow, 'current_attempt', {'attempt': 1}),
            'different-selection': lambda flow: setattr(flow, 'selection', ChartSelection('91', 'normal')),
        }
        for name, mutate in mutations.items():
            with self.subTest(mutation=name):
                flow = self.flow()
                self.discover(flow)
                mutate(flow)
                flow.snap.reset_mock()
                self.assertEqual(flow.read_full_combo_state(SELECTION), 'full_combo')
                self.assertEqual(flow.snap.call_count, 2)

    def test_difficulty_change_during_selection_drops_discovery_proof(self):
        flow = self.flow()
        flow.difficulty_selected.side_effect = [False, True]
        self.assertEqual(flow.read_final_selection(), SELECTION)
        flow.controller.post_click.assert_called_once_with(820, flow.difficulty_y)
        self.assertEqual(flow.snap.call_count, 3)
        flow.snap.reset_mock()
        flow.difficulty_selected.side_effect = None
        self.assertEqual(flow.read_full_combo_state(SELECTION), 'full_combo')
        self.assertEqual(flow.snap.call_count, 2)

    def test_proof_for_another_song_never_exempts_requested_chart(self):
        flow = self.flow()
        self.discover(flow)
        requested = ChartSelection('595', 'expert')
        flow.selection = requested
        self.assertEqual(flow.read_full_combo_state(requested), 'unknown')
        self.assertEqual(flow.snap.call_count, 4)

    def test_legacy_boolean_options_do_not_collect_stars_during_song_discovery(self):
        for enabled in (False, True):
            with self.subTest(enabled=enabled):
                flow = self.flow(policy=enabled)
                self.stars.reset_mock()
                self.discover(flow)
                self.stars.assert_not_called()

    def test_reused_first_frame_still_waits_for_a_new_independent_second_frame(self):
        flow = self.flow()
        self.discover(flow)
        flow._full_combo_sample = Mock(side_effect=lambda selection: self.events.append('sample') or FC_ROW)
        self.assertEqual(flow.read_full_combo_state(SELECTION, fresh=False), 'full_combo')
        self.assertEqual(self.events, ['sample', ('wait', .12), 'snap', 'sample'])
        self.assertEqual(flow.snap.call_count, 1)
        # fresh=False deliberately does not consume the discovery proof as an
        # answer. It also leaves no such proof for a subsequent authorization.
        flow.snap.reset_mock()
        self.assertEqual(flow.read_full_combo_state(SELECTION), 'full_combo')
        self.assertEqual(flow.snap.call_count, 2)

    def test_reuse_without_image_acquires_both_samples(self):
        flow = self.flow()
        flow.image = None
        self.assertEqual(flow.read_full_combo_state(SELECTION, fresh=False), 'full_combo')
        self.assertEqual(flow.snap.call_count, 2)

    def test_uncertain_samples_remain_bounded_to_four_observations(self):
        for fresh in (False, True):
            with self.subTest(fresh=fresh):
                flow = self.flow()
                flow._full_combo_sample = Mock(side_effect=[FC_ROW, CLEAR_ROW, FC_ROW, None])
                self.assertEqual(flow.read_full_combo_state(SELECTION, fresh=fresh), 'unknown')
                self.assertEqual(flow._full_combo_sample.call_count, 4)
                self.assertEqual(flow.snap.call_count, 4 if fresh else 3)
                self.assertEqual(flow.pause.call_args_list, [call(.12)] * 3)

    def test_page_song_difficulty_or_stars_change_rejects_even_existing_proof(self):
        changes = {
            'page-left': lambda flow: setattr(flow.is_final_page, 'return_value', False),
            'song-changed': lambda flow: setattr(flow.text, 'return_value', BY_ID['595']['title']),
            'difficulty-missing': lambda flow: setattr(flow.ocr, 'return_value', []),
            'difficulty-changed': lambda flow: setattr(flow.difficulty_selected, 'return_value', False),
            'stars-unknown': lambda flow: setattr(self.stars, 'return_value', None),
        }
        for name, change in changes.items():
            with self.subTest(change=name):
                self.stars.return_value = FC_ROW
                flow = self.flow()
                self.discover(flow)
                change(flow)
                self.assertEqual(flow.read_full_combo_state(SELECTION, fresh=False), 'unknown')
                self.assertEqual(flow.snap.call_count, 3)
                flow.controller.post_click.assert_not_called()

    def test_reused_proof_and_reused_first_frame_both_obey_stop(self):
        for fresh in (False, True):
            with self.subTest(fresh=fresh):
                flow = self.flow()
                self.discover(flow)
                flow.ctx.tasker.stopping = True
                with self.assertRaisesRegex(FlowError, '停止'):
                    flow.read_full_combo_state(SELECTION, fresh=fresh)
                flow.snap.assert_not_called()

    def playback(self, flow, *, depart_after_verification=False):
        flow.save_frame = Mock()
        flow.fire_preview = Mock(return_value=(18, 18))
        flow.disable_mv = Mock()
        flow.submit_online_ready = Mock()
        if depart_after_verification:
            flow.disable_mv.side_effect = lambda **kw: setattr(flow.is_final_page, 'return_value', False)
        destination = flow.output / 'attempt1_playback'
        process = Mock()
        process.poll.return_value = None if depart_after_verification else 0
        process.returncode = 0
        configs = []
        streams = []
        def spawn(args, **kwargs):
            configs.append(json.loads(Path(args[-1]).read_text(encoding='utf8')))
            (destination / 'armed').write_text('ready')
            (destination / 'playback.json').write_text('{"status":"input_complete"}')
            streams.append(kwargs['stdout'])
            return process
        original = flow.read_full_combo_state
        flow.read_full_combo_state = Mock(wraps=original)
        row = {}
        with patch('chart_live.subprocess.Popen', side_effect=spawn), contextlib.redirect_stdout(io.StringIO()):
            if depart_after_verification:
                with self.assertRaisesRegex(FlowError, '星标'):
                    flow.play_chart(1, SELECTION, {'duration': 1}, 0, row)
            else:
                flow.play_chart(1, SELECTION, {'duration': 1}, 0, row)
        self.assertFalse(configs[0]['avoid_full_combo'])
        self.assertTrue(streams[0].closed)
        self.assertEqual(flow.read_full_combo_state.call_args_list,
                         [call(SELECTION), call(SELECTION, fresh=False)])
        return destination, process, row

    def test_worker_armed_verification_keeps_two_current_fc_samples(self):
        flow = self.flow()
        self.discover(flow)
        destination, process, row = self.playback(flow)
        # One armed-page identity capture plus one new FC confirmation capture;
        # initial discovery proof does not substitute for either of them.
        self.assertEqual(flow.snap.call_count, 2)
        flow.pause.assert_called_once_with(.12)
        self.assertTrue((destination / 'start').exists())
        self.assertFalse((destination / 'stop').exists())
        self.assertEqual(row['status'], 'submitted')
        flow.submit_online_ready.assert_called_once_with(SELECTION)

    def test_unknown_after_worker_is_armed_stops_without_authorizing_or_ready_click(self):
        flow = self.flow()
        self.discover(flow)
        destination, process, row = self.playback(flow, depart_after_verification=True)
        self.assertFalse((destination / 'start').exists())
        self.assertEqual((destination / 'stop').read_text(), 'stop')
        self.assertNotEqual(row.get('status'), 'submitted')
        flow.save_frame.assert_not_called()
        flow.submit_online_ready.assert_not_called()
        process.wait.assert_called_once_with(timeout=8)

    def test_single_player_default_still_captures_two_fresh_samples(self):
        flow = self.flow()
        flow._full_combo_sample = Mock(return_value=FC_ROW)
        self.assertEqual(ChartLiveFlow.read_full_combo_state(flow, SELECTION), 'full_combo')
        self.assertEqual(flow.snap.call_count, 2)
        flow.pause.assert_called_once_with(.12)


if __name__ == '__main__':
    unittest.main()
