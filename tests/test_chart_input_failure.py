"""Run the real worker input loop and teardown against controllable native jobs."""
import hashlib
import json
from pathlib import Path
import sys
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import Mock, patch

import numpy as np

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT/'agent'))
sys.path.insert(0,str(ROOT/'tools'))
from chart_timing import GestureEvent
from chart_worker import play
from playback_controller import PlaybackOptions
import run_chart_probe


class ChartInputFailureTests(unittest.TestCase):
    def run_worker(self,folder,action,kind,*,stall=False,cleanup_fails=False,complete=False):
        output=Path(folder)
        raw=json.dumps([{'type':'BPM','beat':0,'bpm':120},
                        {'type':'Single','beat':2,'lane':3}]).encode('utf8')
        (output/'chart.json').write_bytes(raw)
        config={'controller':{},'chart':{'path':str(output/'chart.json'),
                'sha256':hashlib.sha256(raw).hexdigest(),'duration':1},
                'jitter':'small','seed':1,'start_mode':'click'}
        (output/'config.json').write_text(json.dumps(config),encoding='utf8')
        (output/'start').write_text('start')
        now=[10.]
        success=SimpleNamespace(job_id=123,wait=lambda:SimpleNamespace(succeeded=True))
        controller,observer=Mock(),Mock()
        for item in (controller,observer):
            item.cached_image=np.zeros((720,1280,3),dtype=np.uint8)
            item.post_connection.return_value=success
            item.post_screencap.return_value=success
            item.post_click.return_value=success
            item.post_touch_down.return_value=success
            item.post_touch_move.return_value=success
            item.post_touch_up.return_value=success
        failure=getattr(controller,'post_touch_'+action)
        if kind=='submit':
            failure.side_effect=RuntimeError('uncertain '+action+' submission')
        elif kind=='wait':
            failure.return_value=SimpleNamespace(wait=Mock(side_effect=RuntimeError('uncertain '+action+' completion')))
        else:
            failure.return_value=SimpleNamespace(wait=lambda:SimpleNamespace(succeeded=False))
        events=[GestureEvent(0,1,0,3,'down')]
        if cleanup_fails:
            events.append(GestureEvent(0,1,1,4,'down'))
            controller.post_touch_up.side_effect=[RuntimeError('cleanup 0 uncertain'),success]
        if action!='down' and not complete:
            events.append(GestureEvent(0,0,0,3,action))
        if not complete:
            events.append(GestureEvent(0,1,2,5,'down'))  # Must never run after failure.
        if stall:
            def wait_down():
                now[0]=10.2
                return SimpleNamespace(succeeded=True)
            controller.post_touch_down.return_value=SimpleNamespace(wait=wait_down)
        lock=Mock(points=[])
        lock.observe.return_value={'crossing':0.}
        tracker=Mock(phase_reference=.037)
        tracker.origin_from_anchor.return_value=10.
        resource=Mock()
        resource.post_bundle.return_value=success
        with patch('maa.controller.AdbController',side_effect=[controller,observer]), \
                patch('maa.resource.Resource',return_value=resource), \
                patch('maa.tasker.Tasker'),patch('maa.toolkit.Toolkit.init_option'), \
                patch('chart_worker.playback_options',return_value=PlaybackOptions('fake','fake',{},4,'connection_explicit',4)), \
                patch('chart_worker.compile_chart',return_value=events), \
                patch('chart_worker.time.perf_counter',side_effect=lambda:now[0]), \
                patch('chart_worker.threading.Thread'), \
                patch('chart_sync.FirstNoteLock',return_value=lock), \
                patch('chart_sync.ChartPhaseTracker',return_value=tracker), \
                patch('chart_sync.stage_state',return_value=True), \
                patch('chart_sync.locate_note_y',return_value=500.), \
                patch('PIL.Image.fromarray'):
            result=play(output/'config.json')
        return result,json.loads((output/'playback.json').read_text(encoding='utf8')),controller

    def test_failed_up_is_never_replayed_and_evidence_survives(self):
        for kind in ('submit','wait','failed'):
            with self.subTest(kind=kind),tempfile.TemporaryDirectory() as folder:
                result,report,controller=self.run_worker(folder,'up',kind)
                self.assertEqual(result,1)
                self.assertTrue(report['input_failed'])
                controller.post_touch_up.assert_called_once_with(0)
                self.assertEqual(controller.post_touch_down.call_count,1)
                controller.post_click.assert_called_once_with(1130,616)
                self.assertFalse(report['events'][-1]['ok'])
                self.assertIn('submit_ms',report['events'][-1])
                self.assertNotIn('cleanup_inputs',report)

    def test_uncertain_down_or_move_gets_one_release_but_no_replay_or_pause(self):
        for action in ('down','move'):
            for kind in ('submit','wait','failed'):
                with self.subTest(action=action,kind=kind),tempfile.TemporaryDirectory() as folder:
                    result,report,controller=self.run_worker(folder,action,kind)
                    self.assertEqual(result,1)
                    controller.post_touch_down.assert_called_once()
                    getattr(controller,'post_touch_'+action).assert_called_once()
                    controller.post_touch_up.assert_called_once_with(0)
                    controller.post_click.assert_called_once_with(1130,616)
                    self.assertTrue(report['input_failed'])
                    self.assertTrue(report['cleanup_inputs'][0]['ok'])

    def test_recovery_release_failure_is_not_retried_during_final_cleanup(self):
        for kind in ('submit','wait','failed'):
            with self.subTest(kind=kind),tempfile.TemporaryDirectory() as folder:
                result,report,controller=self.run_worker(folder,'up',kind,stall=True)
                self.assertEqual(result,1)
                self.assertTrue(report['input_failed'])
                controller.post_touch_up.assert_called_once_with(0)
                controller.post_click.assert_called_once_with(1130,616)
                self.assertEqual(len(report['recoveries']),1)
                self.assertNotIn('cleanup_inputs',report)
                release=report['recoveries'][0]['release_inputs'][0]
                self.assertFalse(release['ok'])
                self.assertIn('submit_ms',release)
                self.assertIn('api_ms',release)
                if kind=='submit':
                    self.assertIsNone(release['wait_ms'])
                else:
                    self.assertIsNotNone(release['wait_ms'])

    def test_completed_worker_loop_returns_failure_if_final_release_fails(self):
        for kind in ('submit','wait','failed'):
            with self.subTest(kind=kind),tempfile.TemporaryDirectory() as folder:
                result,report,controller=self.run_worker(folder,'up',kind,complete=True)
                self.assertEqual(result,1)
                self.assertEqual(report['status'],'error')
                self.assertTrue(report['input_failed'])
                self.assertTrue(all(event['ok'] for event in report['events']))
                self.assertEqual(report['error'],'Cleanup input failed: '+report['cleanup_errors'][0]['error'])
                self.assertFalse(report['cleanup_inputs'][0]['ok'])
                controller.post_touch_up.assert_called_once_with(0)
                controller.post_click.assert_called_once_with(1130,616)

    def test_cleanup_failure_does_not_hide_input_error_or_skip_other_contacts(self):
        with tempfile.TemporaryDirectory() as folder:
            result,report,controller=self.run_worker(folder,'move','wait',cleanup_fails=True)
            self.assertEqual(result,1)
            self.assertEqual(report['error'],'uncertain move completion')
            self.assertEqual([call.args for call in controller.post_touch_up.call_args_list],[(0,),(1,)])
            self.assertEqual(report['cleanup_errors'][0]['contact'],0)
            self.assertEqual(report['cleanup_inputs'][1]['ok'],True)
            controller.post_click.assert_called_once_with(1130,616)


class ProbeInputFailureTests(unittest.TestCase):
    def run_probe(self,folder,action,kind,*,complete=False):
        root=Path(folder)
        raw=json.dumps([{'type':'BPM','beat':0,'bpm':120},
                        {'type':'Single','beat':0,'lane':3}]).encode('utf8')
        chart=root/'chart.json'
        chart.write_bytes(raw)
        manifest=root/'docs/data/chart_probe_manifest.json'
        manifest.parent.mkdir(parents=True)
        manifest.write_text(json.dumps({'charts':[{'song_id':'306','difficulty':'easy',
                            'sha256':hashlib.sha256(raw).hexdigest(),'title':'test'}]}),encoding='utf8')
        success=SimpleNamespace(wait=lambda:SimpleNamespace(succeeded=True))
        controller=Mock(cached_image=np.zeros((720,1280,3),dtype=np.uint8))
        for name in ('connection','screencap','click','touch_down','touch_move','touch_up'):
            getattr(controller,'post_'+name).return_value=success
        failure=getattr(controller,'post_click' if action=='start' else 'post_touch_'+action)
        if kind=='submit':
            failure.side_effect=RuntimeError('uncertain '+action+' submission')
        elif kind=='wait':
            failure.return_value=SimpleNamespace(wait=Mock(side_effect=RuntimeError('uncertain '+action+' completion')))
        else:
            failure.return_value=SimpleNamespace(wait=lambda:SimpleNamespace(succeeded=False))
        events=[GestureEvent(0,1,0,3,'down')]
        if action not in ('down','start') and not complete:
            events.append(GestureEvent(0,0,0,3,action))
        if not complete:
            events.append(GestureEvent(0,1,2,5,'down'))
        resource=Mock()
        resource.post_bundle.return_value=success
        tasker=Mock()
        tasker.post_task.return_value=success
        lock=Mock(points=[])
        lock.observe.return_value={'crossing':0.}
        arguments=['run_chart_probe.py','--adb','fake','--address','fake','--chart',str(chart),
                   '--output',str(root/'output'),'--execute-zero-fire']
        with patch.object(run_chart_probe,'ROOT',root),patch.object(sys,'argv',arguments), \
                patch('maa.controller.AdbController',return_value=controller), \
                patch('maa.resource.Resource',return_value=resource), \
                patch('maa.tasker.Tasker',return_value=tasker),patch('maa.toolkit.Toolkit.init_option'), \
                patch('playback_controller.playback_options',return_value=PlaybackOptions('fake','fake',{},4,'connection_explicit',4)), \
                patch('chart_timing.compile_chart',return_value=events), \
                patch('chart_timing.first_anchor',return_value=(0,3,'cyan')), \
                patch('run_chart_probe.time.perf_counter',return_value=10.), \
                patch('run_chart_probe.time.sleep',side_effect=AssertionError('must not continue after input failure')), \
                patch('chart_sync.FirstNoteLock',return_value=lock), \
                patch('chart_sync.stage_state',return_value=True), \
                patch('chart_sync.locate_note_y',return_value=500.), \
                patch('PIL.Image.fromarray'),patch('builtins.print'):
            result=run_chart_probe.main()
        tasker.post_task.assert_called_once()  # Preflight only, never result navigation.
        return result,json.loads((root/'output/report.json').read_text(encoding='utf8')),controller

    def test_probe_input_failure_stops_without_replay_or_blind_pause(self):
        for action in ('start','down','move','up'):
            for kind in ('submit','wait','failed'):
                with self.subTest(action=action,kind=kind),tempfile.TemporaryDirectory() as folder:
                    result,report,controller=self.run_probe(folder,action,kind)
                    self.assertEqual(result,1)
                    self.assertTrue(report['input_failed'])
                    controller.post_click.assert_called_once_with(1130,616)
                    if action=='start':
                        controller.post_touch_down.assert_not_called()
                        controller.post_touch_up.assert_not_called()
                    else:
                        controller.post_touch_down.assert_called_once()
                        controller.post_touch_up.assert_called_once_with(0)
                        self.assertFalse(report['events'][-1]['ok'])
                        self.assertIn('submit_ms',report['events'][-1])
                        if action=='up':
                            self.assertNotIn('cleanup_inputs',report)

    def test_completed_probe_loop_stops_before_result_navigation_if_release_fails(self):
        for kind in ('submit','wait','failed'):
            with self.subTest(kind=kind),tempfile.TemporaryDirectory() as folder:
                result,report,controller=self.run_probe(folder,'up',kind,complete=True)
                self.assertEqual(result,1)
                self.assertEqual(report['status'],'failed')
                self.assertTrue(report['input_failed'])
                self.assertTrue(all(event['ok'] for event in report['events']))
                self.assertFalse(report['cleanup_inputs'][0]['ok'])
                self.assertIn('Cleanup input failed:',report['error'])
                controller.post_touch_up.assert_called_once_with(0)
                controller.post_click.assert_called_once_with(1130,616)


if __name__=='__main__':
    unittest.main()
