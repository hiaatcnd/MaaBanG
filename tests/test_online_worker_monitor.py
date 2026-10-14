"""Worker visual monitoring with captured-frame stubs, without device access."""
import json
from pathlib import Path
import sys
import tempfile
from threading import Event
from types import SimpleNamespace
import unittest
from unittest.mock import Mock, patch

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT/'agent'))
from chart_worker import check_playback_overlay
from costume_unlock import FlowError
from online_live import OnlineFatal, OnlineLiveFlow
from online_policy import RoomClock, RoomInterrupted


def recognized(*texts):
    hits=[SimpleNamespace(text=text) for text in texts]
    return SimpleNamespace(hit=True,filtered_results=hits,best_result=hits[0])


class OnlineWorkerMonitorTests(unittest.TestCase):
    def context(self, hits=None):
        pipeline=json.loads((ROOT/'assets/resource/pipeline/online_live.json').read_text(encoding='utf8'))
        nodes={name:{'enabled':True,'inverse':False,'recognition':{
            'type':'OCR','param':{'index':0,**{key:value for key,value in pipeline[name].items()
                                            if key!='recognition'}}}}
               for name in ('OL_Fatal','OL_Disconnected')}
        hits=hits or {}
        return SimpleNamespace(get_node_data=Mock(side_effect=nodes.get),
            run_recognition=Mock(side_effect=lambda name,frame,override:hits.get(name)))

    def test_fatal_has_priority_and_stops_without_retryable_interruption(self):
        context=self.context({'OL_Fatal':recognized('房间已解散','账号受到限制')})
        report={'status':'playing'}
        failed=Event()
        self.assertFalse(check_playback_overlay(context,object(),report,failed,True))
        self.assertTrue(failed.is_set())
        self.assertTrue(report['fatal'])
        self.assertNotIn('interrupted',report)
        self.assertIn('账号受到限制',report['observer_error'])
        self.assertEqual(context.run_recognition.call_count,1)

    def test_disconnection_stops_and_remains_retryable(self):
        context=self.context({'OL_Fatal':recognized('房间已解散')})
        report={'status':'playing'}
        failed=Event()
        self.assertFalse(check_playback_overlay(context,object(),report,failed,True))
        self.assertTrue(failed.is_set())
        self.assertTrue(report['interrupted'])
        self.assertNotIn('fatal',report)
        self.assertIn('房间已解散',report['observer_error'])

    def test_playing_omits_unused_waiting_members_recognition(self):
        context=self.context()
        report={'status':'playing'}
        failed=Event()
        frame=object()
        self.assertFalse(check_playback_overlay(context,frame,report,failed,True))
        self.assertFalse(failed.is_set())
        names=[call.args[0] for call in context.run_recognition.call_args_list]
        self.assertNotIn('OL_WaitingMembers',names)
        self.assertIn('ChartWorkerOverlay',names)
        self.assertTrue(all(call.args[1] is frame for call in context.run_recognition.call_args_list))

    def test_preparing_still_identifies_waiting_members_without_stopping(self):
        context=self.context({'OL_WaitingMembers':recognized('其他成员正在准备中')})
        report={'status':'preparing'}
        failed=Event()
        self.assertTrue(check_playback_overlay(context,object(),report,failed,True))
        self.assertFalse(failed.is_set())
        self.assertNotIn('observer_error',report)
        self.assertIn('ChartWorkerOverlay',[call.args[0] for call in context.run_recognition.call_args_list])

    def test_every_known_exit_stops_and_remains_retryable(self):
        for node in ('CU_HomeBand','LV_Menu','OL_RoomPage','OL_TeamHome'):
            with self.subTest(node=node):
                context=self.context({node:recognized('exit')})
                report={'status':'playing'}
                failed=Event()
                self.assertFalse(check_playback_overlay(context,object(),report,failed,True))
                self.assertTrue(failed.is_set())
                self.assertTrue(report['interrupted'])
                self.assertNotIn('fatal',report)

    def test_pause_still_stops_online_and_solo_input(self):
        for online in (True,False):
            with self.subTest(online=online):
                context=self.context({'ChartWorkerOverlay':recognized('暂停')})
                report={'status':'playing'}
                failed=Event()
                self.assertFalse(check_playback_overlay(context,object(),report,failed,online))
                self.assertTrue(failed.is_set())
                self.assertIn('暂停',report['observer_error'])
                self.assertNotIn('fatal',report)
                self.assertNotIn('interrupted',report)
                if not online:
                    context.get_node_data.assert_not_called()
                    self.assertEqual(context.run_recognition.call_count,1)


class ParentWorkerMonitorTests(unittest.TestCase):
    def flow(self):
        flow=OnlineLiveFlow.__new__(OnlineLiveFlow)
        flow.room_clock=RoomClock(0)
        flow._last_worker_check=0.
        flow._last_foreground_check=0.
        flow.snap=Mock()
        flow.state=Mock()
        flow.foreground_package=Mock(return_value='com.bilibili.star.bili')
        return flow

    def test_before_worker_armed_parent_checks_foreground_without_extra_frames(self):
        flow=self.flow()
        with tempfile.TemporaryDirectory() as folder,patch('online_live.time.monotonic',return_value=10):
            flow.monitor_online_worker(Path(folder))
        flow.snap.assert_not_called()
        flow.foreground_package.assert_called_once_with()

    def test_authorization_keeps_parent_visual_checks_until_observer_is_ready(self):
        flow=self.flow()
        with tempfile.TemporaryDirectory() as folder,patch('online_live.time.monotonic',return_value=10):
            destination=Path(folder)
            (destination/'armed').write_text('ready')
            (destination/'start').write_text('start')
            (destination/'stage_started').write_text('started')
            flow.monitor_online_worker(destination)
        flow.snap.assert_called_once_with()

    def test_armed_worker_without_observer_still_gets_parent_visual_checks(self):
        flow=self.flow()
        with tempfile.TemporaryDirectory() as folder,patch('online_live.time.monotonic',return_value=10):
            destination=Path(folder)
            (destination/'armed').write_text('ready')
            flow.monitor_online_worker(destination)
        flow.snap.assert_called_once_with()

    def test_setup_still_aborts_on_background_switch(self):
        flow=self.flow()
        flow.foreground_package.return_value='com.mumu.launcher'
        with tempfile.TemporaryDirectory() as folder,patch('online_live.time.monotonic',return_value=10):
            with self.assertRaisesRegex(RoomInterrupted,'切到后台'):
                flow.monitor_online_worker(Path(folder))
        flow.snap.assert_not_called()

    def test_setup_keeps_absolute_room_deadline(self):
        flow=self.flow()
        with tempfile.TemporaryDirectory() as folder,patch('online_live.time.monotonic',return_value=181):
            with self.assertRaisesRegex(RoomInterrupted,'3分钟未开演'):
                flow.monitor_online_worker(Path(folder))
        flow.snap.assert_not_called()

    def test_ready_observer_owns_frames_but_foreground_check_is_preserved(self):
        flow=self.flow()
        with tempfile.TemporaryDirectory() as folder:
            destination=Path(folder)
            (destination/'armed').write_text('ready')
            (destination/'observer_ready').write_text('ready')
            for stamp in (10.,10.3,10.8,11.4):
                with patch('online_live.time.monotonic',return_value=stamp):
                    flow.monitor_online_worker(destination)
        flow.snap.assert_not_called()
        self.assertEqual(flow.foreground_package.call_count,2)

    def test_background_switch_still_interrupts_after_visual_handoff(self):
        flow=self.flow()
        flow.foreground_package.return_value='com.mumu.launcher'
        with tempfile.TemporaryDirectory() as folder,patch('online_live.time.monotonic',return_value=10):
            destination=Path(folder)
            (destination/'observer_ready').write_text('ready')
            with self.assertRaisesRegex(RoomInterrupted,'切到后台'):
                flow.monitor_online_worker(destination)
        flow.snap.assert_not_called()

    def test_stage_marker_prevents_expired_room_timer_from_stopping_playback(self):
        flow=self.flow()
        with tempfile.TemporaryDirectory() as folder,patch('online_live.time.monotonic',return_value=200):
            destination=Path(folder)
            (destination/'observer_ready').write_text('ready')
            (destination/'stage_started').write_text('started')
            flow.monitor_online_worker(destination)
        self.assertTrue(flow.room_clock.started)
        flow.state.assert_called_once_with('谱面代打中')
        flow.snap.assert_not_called()
        flow.foreground_package.assert_called_once_with()

    def test_visual_handoff_does_not_restart_timeout_measured_from_room_entry(self):
        flow=self.flow()
        flow.room_clock=RoomClock(10)
        with tempfile.TemporaryDirectory() as folder,patch('online_live.time.monotonic',return_value=191):
            destination=Path(folder)
            (destination/'observer_ready').write_text('ready')
            with self.assertRaisesRegex(RoomInterrupted,'3分钟未开演'):
                flow.monitor_online_worker(destination)
        flow.snap.assert_not_called()

    def test_fatal_report_takes_priority_and_never_becomes_retryable(self):
        flow=self.flow()
        with self.assertRaisesRegex(OnlineFatal,'maintenance'):
            flow.handle_online_playback_error({'fatal':True,'interrupted':True,
                                               'observer_error':'maintenance'})
        flow.snap.assert_not_called()

    def test_disconnection_report_still_requests_room_recovery(self):
        flow=self.flow()
        with self.assertRaisesRegex(RoomInterrupted,'disconnected'):
            flow.handle_online_playback_error({'interrupted':True,'observer_error':'disconnected'})
        flow.snap.assert_not_called()

    def test_unclassified_worker_failure_still_rechecks_current_room_state(self):
        flow=self.flow()
        flow.handle_online_playback_error({'error':'unlocked first note'})
        flow.snap.assert_called_once_with()

    def test_error_diagnostic_uses_a_fresh_frame_without_room_guard(self):
        flow=self.flow()
        flow.current_attempt={}
        old=object();fresh=object()
        flow.image=old
        flow.guard_room=Mock(side_effect=AssertionError('must not classify diagnostics'))
        saved=[]
        flow.save_frame=Mock(side_effect=lambda name:saved.append((name,flow.image)))
        def capture():
            flow.image=fresh
            return fresh
        with patch('online_live.ChartLiveFlow.snap',side_effect=capture) as snap:
            flow.save_error_frame('failed.png')
        snap.assert_called_once_with()
        flow.guard_room.assert_not_called()
        self.assertEqual(saved,[('failed.png',fresh)])

    def test_diagnostic_failure_never_saves_the_stale_ready_frame(self):
        flow=self.flow()
        flow.current_attempt={}
        flow.save_frame=Mock()
        with patch('online_live.ChartLiveFlow.snap',side_effect=RuntimeError('capture failed')):
            flow.save_error_frame('failed.png')
        flow.save_frame.assert_not_called()
        self.assertEqual(flow.current_attempt['error_frame_error'],'capture failed')

    def test_diagnostic_save_failure_is_recorded_without_raising(self):
        flow=self.flow()
        flow.current_attempt={}
        flow.save_frame=Mock(side_effect=OSError('disk full'))
        with patch('online_live.ChartLiveFlow.snap'):
            flow.save_error_frame('failed.png')
        self.assertEqual(flow.current_attempt['error_frame_error'],'disk full')


if __name__=='__main__':
    unittest.main()
