"""Matching and authorized ready-wait deadlines, without device or network input."""
import json
from pathlib import Path
import sys
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import Mock, patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT/'agent'))
from chart_policy import ChartOptions, ChartSelection
from costume_unlock import FlowError
from online_live import OnlineFatal, OnlineLiveFlow
from online_policy import ROOM_TIMEOUT, RoomClock, RoomInterrupted


class RoomClockStageTests(unittest.TestCase):
    def test_matching_retains_original_180_second_deadline_until_authorized(self):
        clock = RoomClock(0)
        self.assertIsNone(clock.ready_at)
        self.assertFalse(clock.expired(179.999))
        self.assertTrue(clock.expired(180))
        self.assertFalse(clock.started)

    def test_slow_matching_gets_independent_ready_wait_without_claiming_stage_started(self):
        clock = RoomClock(0)
        clock.begin_ready_wait(166)
        self.assertEqual(clock.entered, 0)
        self.assertEqual(clock.ready_at, 166)
        self.assertFalse(clock.started)
        self.assertFalse(clock.expired(180))
        self.assertFalse(clock.expired(345.999))
        self.assertTrue(clock.expired(346))

    def test_repeated_ready_observations_cannot_restart_wait(self):
        clock = RoomClock(0)
        clock.begin_ready_wait(166)
        for now in (170, 180, 200, 345.999):
            clock.begin_ready_wait(now)
        self.assertEqual(clock.ready_at, 166)
        self.assertTrue(clock.expired(346))

    def test_expired_matching_cannot_be_revived_by_ready_submission(self):
        for now in (180, 181, 400):
            with self.subTest(now=now):
                clock = RoomClock(0)
                with self.assertRaises(RoomInterrupted):
                    clock.begin_ready_wait(now)
                self.assertIsNone(clock.ready_at)
                self.assertFalse(clock.started)
                self.assertTrue(clock.expired(now))

    def test_expired_ready_wait_cannot_be_revived_by_repeated_submission(self):
        clock = RoomClock(0)
        clock.begin_ready_wait(166)
        try:
            clock.begin_ready_wait(400)
        except RoomInterrupted:
            pass
        self.assertEqual(clock.ready_at, 166)
        self.assertTrue(clock.expired(400))

    def test_zero_ready_timestamp_is_a_real_phase_boundary(self):
        clock = RoomClock(-20)
        clock.begin_ready_wait(0)
        self.assertFalse(clock.expired(179.999))
        self.assertTrue(clock.expired(180))

    def test_latest_allowed_transition_still_bounds_combined_wait_by_six_minutes(self):
        clock = RoomClock(0)
        clock.begin_ready_wait(ROOM_TIMEOUT-.001)
        self.assertFalse(clock.expired(2*ROOM_TIMEOUT-.002))
        self.assertTrue(clock.expired(2*ROOM_TIMEOUT))

    def test_actual_stage_start_removes_only_the_wait_deadline(self):
        clock = RoomClock(0)
        clock.begin_ready_wait(166)
        clock.started = True
        self.assertFalse(clock.expired(1000))
        self.assertEqual(clock.ready_at, 166)


class OnlineRoomDeadlineFlowTests(unittest.TestCase):
    def setUp(self):
        self.now = 166.
        clock = patch('online_live.time.monotonic', side_effect=lambda: self.now)
        clock.start()
        self.addCleanup(clock.stop)
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.output = Path(temporary.name)
        self.selection = ChartSelection('564', 'expert')
        self.ready = SimpleNamespace(box=[1010, 580, 220, 100])
        self.recognized = {'OL_FinalConfirm': self.ready}

        flow = self.flow = OnlineLiveFlow.__new__(OnlineLiveFlow)
        flow.settings = ChartOptions.parse({'mode': 'coop', 'fire': 0})
        flow.output = self.output
        flow.controller = SimpleNamespace(info={'test': True})
        flow.room_clock = RoomClock(0)
        flow.room_active = True
        flow.left_entry = True
        flow.home_node = 'OL_RoomPage'
        flow._last_worker_check = 0.
        flow._last_foreground_check = 0.
        flow.current_attempt = {}
        flow.report = {'state': 'preparing', 'attempts': 1, 'completed_rounds': 0}
        flow.image = object()
        flow.state = Mock()
        flow.check_stop = Mock()
        flow.pause = Mock(side_effect=self.pause)
        flow.reco = Mock(side_effect=lambda name, **kw: self.recognized.get(name))
        flow.room_error = Mock(return_value=None)
        flow.foreground_package = Mock(return_value='com.bilibili.star.bili')
        flow.snap = Mock(side_effect=self.snap)
        flow.quick_tap = Mock()
        flow.hit_text = Mock(return_value=self.ready)
        flow.loading_final_page = Mock(return_value=False)
        flow.save_frame = Mock()

    def pause(self, seconds):
        self.flow.check_stop()
        self.now += seconds

    def snap(self):
        # Keep the production room guard; replace only image acquisition.
        self.flow.check_stop()
        self.flow.guard_room()
        return self.flow.image

    def observer_files(self, *, armed=True, observing=True, stage=False):
        destination = self.output/'observer'
        destination.mkdir(exist_ok=True)
        for enabled, name in ((armed, 'armed'), (observing, 'observer_ready'), (stage, 'stage_started')):
            if enabled:
                (destination/name).write_text('ready', encoding='utf8')
        return destination

    def test_submit_records_phase_then_guard_and_observer_use_new_deadline(self):
        self.flow.submit_online_ready(self.selection)
        self.assertEqual(self.flow.room_clock.ready_at, 166)
        self.assertEqual(self.flow.current_attempt['ready_wait_started_seconds'], 166)
        self.assertFalse(self.flow.room_clock.started)
        self.flow.quick_tap.assert_called_once_with(1120, 630)
        destination = self.observer_files()
        for stamp in (180., 200., 345.):
            self.now = stamp
            self.flow.guard_room()
            self.flow.monitor_online_worker(destination)
        self.now = 346
        with self.assertRaises(RoomInterrupted):
            self.flow.guard_room()
        with self.assertRaises(RoomInterrupted):
            self.flow.monitor_online_worker(destination)

    def test_repeated_submission_does_not_renew_phase_or_report_timestamp(self):
        self.flow.submit_online_ready(self.selection)
        self.now = 200
        self.flow.submit_online_ready(self.selection)
        self.assertEqual(self.flow.room_clock.ready_at, 166)
        self.assertEqual(self.flow.current_attempt['ready_wait_started_seconds'], 166)
        self.now = 346
        with self.assertRaises(RoomInterrupted):
            self.flow.snap()

    def test_armed_and_observer_ready_without_authorization_do_not_extend_matching(self):
        for armed, observing in ((False, False), (True, False), (True, True)):
            with self.subTest(armed=armed, observing=observing):
                self.flow.room_clock = RoomClock(0)
                self.flow._last_worker_check = 0
                with tempfile.TemporaryDirectory() as folder:
                    destination = Path(folder)
                    if armed:
                        (destination/'armed').write_text('ready')
                    if observing:
                        (destination/'observer_ready').write_text('ready')
                    self.now = 181
                    with self.assertRaises(RoomInterrupted):
                        self.flow.monitor_online_worker(destination)
                self.assertIsNone(self.flow.room_clock.ready_at)
                self.assertFalse(self.flow.room_clock.started)

    def test_submission_after_matching_deadline_never_clicks_or_revives_room(self):
        self.now = 180
        with self.assertRaises(RoomInterrupted):
            self.flow.submit_online_ready(self.selection)
        self.assertIsNone(self.flow.room_clock.ready_at)
        self.flow.quick_tap.assert_not_called()

    def test_loading_and_waiting_members_do_not_renew_authorized_wait(self):
        self.flow.submit_online_ready(self.selection)
        destination = self.observer_files()
        for stamp, nodes in ((180, {'OL_WaitingMembers': self.ready}),
                             (200, {}), (300, {'OL_FinalConfirm': self.ready})):
            self.now = stamp
            self.recognized = nodes
            self.flow.loading_final_page.return_value = not nodes
            self.flow.snap()
            self.flow.monitor_online_worker(destination)
            self.assertEqual(self.flow.room_clock.ready_at, 166)
            self.assertFalse(self.flow.room_clock.started)
        self.now = 346
        with self.assertRaises(RoomInterrupted):
            self.flow.monitor_online_worker(destination)
        self.flow.quick_tap.assert_called_once_with(1120, 630)

    def test_loading_at_submission_still_gets_wait_without_clicking_covered_button(self):
        self.recognized = {}
        self.flow.loading_final_page.return_value = True
        self.flow.submit_online_ready(self.selection)
        self.assertEqual(self.flow.room_clock.ready_at, 166)
        self.assertFalse(self.flow.room_clock.started)
        self.flow.quick_tap.assert_not_called()

    def test_only_actual_stage_marker_disables_wait_timeout(self):
        self.flow.submit_online_ready(self.selection)
        destination = self.observer_files()
        self.now = 345
        self.flow.monitor_online_worker(destination)
        self.assertFalse(self.flow.room_clock.started)
        (destination/'stage_started').write_text('started')
        self.now = 400
        self.flow.monitor_online_worker(destination)
        self.assertTrue(self.flow.room_clock.started)
        self.flow.state.assert_any_call('谱面代打中')
        self.assertEqual(self.flow.room_clock.ready_at, 166)

    def test_disconnect_and_fatal_checks_remain_active_after_extension(self):
        self.flow.submit_online_ready(self.selection)
        self.now = 200
        self.flow.room_error.return_value = ('OL_Disconnected', '房间已解散')
        with self.assertRaisesRegex(RoomInterrupted, '房间已解散'):
            self.flow.snap()
        self.flow.room_error.return_value = ('OL_Fatal', '服务器维护')
        with self.assertRaisesRegex(OnlineFatal, '服务器维护'):
            self.flow.snap()
        self.flow.quick_tap.assert_called_once_with(1120, 630)

    def test_background_and_stop_checks_remain_active_after_extension(self):
        self.flow.submit_online_ready(self.selection)
        destination = self.observer_files()
        self.now = 200
        self.flow.foreground_package.return_value = 'com.mumu.launcher'
        with self.assertRaisesRegex(RoomInterrupted, '切到后台'):
            self.flow.monitor_online_worker(destination)
        self.flow.foreground_package.return_value = 'com.bilibili.star.bili'
        self.flow.check_stop.side_effect = FlowError('任务已被用户停止')
        with self.assertRaisesRegex(FlowError, '用户停止'):
            self.flow.snap()
        self.flow.quick_tap.assert_called_once_with(1120, 630)

    def worker(self):
        destination = self.output/'attempt1_playback'
        process = Mock()
        process.returncode = 0
        def poll():
            if (destination/'start').exists() and self.flow.room_clock.ready_at is not None:
                (destination/'playback.json').write_text(json.dumps({'status': 'input_complete'}))
                return 0
            return None
        process.poll.side_effect = poll
        def spawn(*args, **kwargs):
            (destination/'armed').write_text('ready')
            return process
        self.flow.disable_mv = Mock()
        self.flow.verify_chart_start = Mock(return_value=0)
        return destination, process, spawn

    def test_play_chart_grants_ready_wait_after_final_checks_before_start_authorization(self):
        destination, process, spawn = self.worker()
        original_submit = self.flow.submit_online_ready
        def save_ready(name):
            self.assertIsNone(self.flow.room_clock.ready_at)
            self.flow.verify_chart_start.assert_called_once()
            self.flow.disable_mv.assert_called_once()
            self.assertFalse((destination/'start').exists())
        self.flow.save_frame.side_effect = save_ready
        def submit(selection):
            self.assertEqual(self.flow.room_clock.ready_at, 166)
            self.flow.verify_chart_start.assert_called_once()
            self.flow.disable_mv.assert_called_once()
            self.assertTrue((destination/'start').exists())
            original_submit(selection)
        self.flow.submit_online_ready = Mock(side_effect=submit)
        with patch('chart_live.subprocess.Popen', side_effect=spawn):
            self.flow.play_chart(1, self.selection, {'duration': 1}, 0, {})
        self.assertEqual(self.flow.room_clock.ready_at, 166)
        self.assertFalse(self.flow.room_clock.started)
        self.flow.submit_online_ready.assert_called_once_with(self.selection)
        self.assertFalse((destination/'stop').exists())
        process.terminate.assert_not_called()

    def test_failed_final_verification_never_grants_ready_time_or_authorizes_input(self):
        destination, process, spawn = self.worker()
        self.flow.verify_chart_start.side_effect = FlowError('最终歌曲发生变化')
        with patch('chart_live.subprocess.Popen', side_effect=spawn):
            with self.assertRaisesRegex(FlowError, '最终歌曲发生变化'):
                self.flow.play_chart(1, self.selection, {'duration': 1}, 0, {})
        self.assertIsNone(self.flow.room_clock.ready_at)
        self.assertFalse((destination/'start').exists())
        self.assertEqual((destination/'stop').read_text(), 'stop')
        self.flow.quick_tap.assert_not_called()
        process.wait.assert_called_once_with(timeout=8)

    def test_slow_ready_screenshot_crossing_matching_deadline_does_not_authorize_worker(self):
        destination, process, spawn = self.worker()
        def save_ready(name):
            self.assertIsNone(self.flow.room_clock.ready_at)
            self.now = 181
        self.flow.save_frame.side_effect = save_ready
        with patch('chart_live.subprocess.Popen', side_effect=spawn):
            with self.assertRaises(RoomInterrupted):
                self.flow.play_chart(1, self.selection, {'duration': 1}, 0, {})
        self.assertIsNone(self.flow.room_clock.ready_at)
        self.assertFalse((destination/'start').exists())
        self.assertEqual((destination/'stop').read_text(), 'stop')
        self.flow.quick_tap.assert_not_called()
        process.wait.assert_called_once_with(timeout=8)

    def test_stop_during_ready_screenshot_does_not_authorize_worker_or_grant_time(self):
        destination, process, spawn = self.worker()
        def save_ready(name):
            self.assertIsNone(self.flow.room_clock.ready_at)
            self.flow.check_stop.side_effect = FlowError('任务已被用户停止')
        self.flow.save_frame.side_effect = save_ready
        with patch('chart_live.subprocess.Popen', side_effect=spawn):
            with self.assertRaisesRegex(FlowError, '用户停止'):
                self.flow.play_chart(1, self.selection, {'duration': 1}, 0, {})
        self.assertIsNone(self.flow.room_clock.ready_at)
        self.assertFalse((destination/'start').exists())
        self.assertEqual((destination/'stop').read_text(), 'stop')
        self.flow.quick_tap.assert_not_called()
        process.wait.assert_called_once_with(timeout=8)


if __name__ == '__main__':
    unittest.main()
