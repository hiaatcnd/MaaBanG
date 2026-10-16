"""Queue overlap and preparation failure boundaries; no device access."""
import hashlib
import json
from pathlib import Path
import sys
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import Mock, patch

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT/'agent'))
from chart_worker import initialize_playback_backend, play
from playback_controller import PlaybackOptions


def fake_backends(failure=None, bad_shape=None):
    calls=[]
    def job(name):
        calls.append('post:'+name)
        def wait():
            calls.append('wait:'+name)
            return SimpleNamespace(succeeded=name!=failure)
        return SimpleNamespace(job_id=name,wait=wait)
    controllers=[]
    for name in ('touch','observer'):
        controllers.append(SimpleNamespace(
            set_screenshot_target_short_side=Mock(),
            post_connection=lambda name=name:job(name+'.connect'),
            post_screencap=lambda name=name:job(name+'.capture'),
            cached_image=SimpleNamespace(shape=(1,1,3) if name==bad_shape else (720,1280,3)),
            post_click=Mock(),post_touch_up=Mock()))
    resource=SimpleNamespace(post_bundle=lambda bundle:job('resource'),register_custom_action=Mock())
    return controllers,resource,calls


class ChartWorkerStartupTests(unittest.TestCase):
    def test_all_connection_capture_and_resource_jobs_are_queued_before_waiting(self):
        controllers,resource,calls=fake_backends()
        initialize_playback_backend(controllers,resource,Path('resource'))
        self.assertEqual(calls,[
            'post:resource','post:touch.connect','post:touch.capture',
            'post:observer.connect','post:observer.capture',
            'wait:touch.connect','wait:touch.capture',
            'wait:observer.connect','wait:observer.capture','wait:resource'])
        for controller in controllers:
            controller.set_screenshot_target_short_side.assert_called_once_with(720)
            controller.post_click.assert_not_called()
            controller.post_touch_up.assert_not_called()

    def run_preparation(self, folder, failure=None, bad_shape=None):
        controllers,resource,calls=fake_backends(failure,bad_shape)
        destination=Path(folder)
        chart=destination/'chart.json'
        chart.write_text(json.dumps([
            {'type':'BPM','beat':0,'bpm':120},
            {'type':'Single','beat':2,'lane':3}]),encoding='utf8')
        config=destination/'config.json'
        config.write_text(json.dumps({'chart':{'path':str(chart),
            'sha256':hashlib.sha256(chart.read_bytes()).hexdigest()},
            'controller':{},'jitter':'medium','seed':7,'start_mode':'online'}),encoding='utf8')
        # If preparation succeeds, immediately stop while awaiting authorization.
        # This lets the real entrypoint finish without ever creating a start file.
        (destination/'stop').write_text('stop')
        with patch('maa.controller.AdbController',side_effect=controllers), \
                patch('maa.resource.Resource',return_value=resource), \
                patch('maa.tasker.Tasker'),patch('maa.toolkit.Toolkit.init_option'), \
                patch('chart_worker.playback_options',return_value=PlaybackOptions(
                    'unused','unused',{},12,'toolkit_discovery',-9,-1)):
            exit_code=play(config)
        report=json.loads((destination/'playback.json').read_text(encoding='utf8'))
        for controller in controllers:
            controller.post_click.assert_not_called()
            controller.post_touch_up.assert_not_called()
        return exit_code,report,resource,calls

    def test_each_failed_async_job_prevents_arming_or_starting(self):
        for failure in ('touch.connect','touch.capture','observer.connect','observer.capture','resource'):
            with self.subTest(failure=failure),tempfile.TemporaryDirectory() as folder:
                exit_code,report,resource,_=self.run_preparation(folder,failure=failure)
                self.assertEqual(exit_code,1)
                self.assertEqual(report['status'],'error')
                self.assertNotIn('ready_total',report['startup_timings_ms'])
                self.assertIn('controllers_and_resource',report['startup_timings_ms'])
                self.assertFalse((Path(folder)/'armed').exists())
                resource.register_custom_action.assert_not_called()

    def test_invalid_dimensions_on_either_controller_prevent_arming(self):
        for name in ('touch','observer'):
            with self.subTest(controller=name),tempfile.TemporaryDirectory() as folder:
                exit_code,report,resource,_=self.run_preparation(folder,bad_shape=name)
                self.assertEqual(exit_code,1)
                self.assertIn('16:9',report['error'])
                self.assertFalse((Path(folder)/'armed').exists())
                resource.register_custom_action.assert_not_called()

    def test_successful_preparation_records_timings_and_arms_without_starting(self):
        with tempfile.TemporaryDirectory() as folder:
            _,report,resource,_=self.run_preparation(folder)
            self.assertTrue((Path(folder)/'armed').exists())
            self.assertFalse((Path(folder)/'start').exists())
            resource.register_custom_action.assert_called_once()
            timings=report['startup_timings_ms']
            self.assertEqual(set(timings),{'chart_compile','controllers_and_resource','ready_total'})
            self.assertTrue(all(value>=0 for value in timings.values()))
            self.assertGreaterEqual(timings['ready_total'],timings['chart_compile']+timings['controllers_and_resource'])
            self.assertIn('已停止',report['error'])
            backend=report['backend']
            self.assertEqual(backend['allowed_input_methods'],12)
            self.assertIsNone(backend['active_backend'])
            self.assertFalse(backend['runtime_fallback'])
            self.assertEqual([row['role'] for row in backend['connections']],['touch','observer'])
            self.assertTrue(all(row['connected'] for row in backend['connections']))
            self.assertEqual([row['job_id'] for row in backend['connections']],
                             ['touch.connect','observer.connect'])


if __name__=='__main__':
    unittest.main()
