import json
from pathlib import Path
import sys
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import Mock

sys.path.insert(0, str(Path(__file__).resolve().parents[1]/'agent'))
from playback_diagnostics import finalize_playback, timed_touch


class PlaybackDiagnosticsTests(unittest.TestCase):
    def test_submission_and_wait_are_measured_independently_for_each_action(self):
        for action in ('down', 'move', 'up'):
            with self.subTest(action=action):
                controller = Mock()
                post = getattr(controller, 'post_touch_'+action)
                post.return_value.wait.return_value = SimpleNamespace(succeeded=True)
                record = {}
                self.assertTrue(timed_touch(controller, action, 10, 20, 2, record,
                                           iter([1, 1.402, 1.607]).__next__))
                self.assertAlmostEqual(record['submit_ms'], 402)
                self.assertAlmostEqual(record['wait_ms'], 205)
                self.assertAlmostEqual(record['api_ms'], 607)
                self.assertEqual(len(controller.mock_calls), 2)
                post.assert_called_once_with(*((2,) if action == 'up' else (10, 20)),
                                             **({} if action == 'up' else {'contact': 2}))

    def test_uncertain_submission_is_recorded_and_never_retried(self):
        controller = Mock()
        controller.post_touch_down.side_effect = RuntimeError('uncertain submission')
        record = {}
        with self.assertRaisesRegex(RuntimeError, 'uncertain'):
            timed_touch(controller, 'down', 10, 20, 2, record, iter([1, 1.4]).__next__)
        self.assertEqual(controller.post_touch_down.call_count, 1)
        self.assertFalse(record['ok'])
        self.assertIsNone(record['wait_ms'])
        self.assertAlmostEqual(record['api_ms'], 400)

    def test_failed_or_raised_wait_does_not_retry_or_hide_timing(self):
        for raises in (False, True):
            with self.subTest(raises=raises):
                controller = Mock()
                wait = controller.post_touch_move.return_value.wait
                wait.return_value = SimpleNamespace(succeeded=False)
                record = {}
                if raises:
                    wait.side_effect = RuntimeError('uncertain completion')
                    with self.assertRaisesRegex(RuntimeError, 'uncertain'):
                        timed_touch(controller, 'move', 10, 20, 2, record,
                                    iter([1, 1.001, 1.2]).__next__)
                else:
                    self.assertFalse(timed_touch(controller, 'move', 10, 20, 2, record,
                                                 iter([1, 1.001, 1.2]).__next__))
                controller.post_touch_move.assert_called_once()
                wait.assert_called_once()
                self.assertFalse(record['ok'])
                self.assertAlmostEqual(record['wait_ms'], 199)

    def test_finalize_attempts_each_release_once_and_persists_original_failure(self):
        controller=Mock()
        controller.post_touch_up.side_effect=[RuntimeError('release 0 uncertain'),
            SimpleNamespace(wait=lambda:SimpleNamespace(succeeded=False)),
            SimpleNamespace(wait=lambda:SimpleNamespace(succeeded=True))]
        report={'status':'error','error':'original move failure',
                'events':[{'submit_ms':402,'wait_ms':205,'ok':False}]}
        active={0,1,2}
        with tempfile.TemporaryDirectory() as folder:
            path=Path(folder)/'playback.json'
            finalize_playback(controller,active,report,path,allow_pause=True,
                              save_diagnostics=Mock(side_effect=OSError('image unavailable')))
            saved=json.loads(path.read_text(encoding='utf8'))
        self.assertEqual(active,set())
        self.assertEqual([call.args for call in controller.post_touch_up.call_args_list],[(0,),(1,),(2,)])
        controller.post_click.assert_not_called()
        self.assertEqual(saved['error'],'original move failure')
        self.assertEqual(saved['events'],report['events'])
        self.assertEqual(len(saved['cleanup_errors']),2)
        self.assertEqual(saved['diagnostic_errors'],['image unavailable'])
        self.assertTrue(saved['input_failed'])

    def test_finalize_failed_pause_is_not_retried_and_report_is_written(self):
        for failure in (RuntimeError('pause uncertain'),None):
            with self.subTest(failure=failure),tempfile.TemporaryDirectory() as folder:
                controller=Mock()
                controller.post_click.side_effect=failure
                controller.post_click.return_value.wait.return_value.succeeded=False
                path=Path(folder)/'report.json'
                finalize_playback(controller,set(),{'status':'error'},path,allow_pause=True)
                controller.post_click.assert_called_once_with(1240,50)
                saved=json.loads(path.read_text(encoding='utf8'))
                self.assertTrue(saved['input_failed'])
                self.assertEqual(saved['cleanup_errors'][0]['action'],'pause')

    def test_finalize_never_pauses_after_uncertain_input_even_if_stage_was_seen(self):
        controller=Mock()
        with tempfile.TemporaryDirectory() as folder:
            finalize_playback(controller,set(),{'status':'error','input_failed':True},
                              Path(folder)/'report.json',allow_pause=True)
        controller.post_click.assert_not_called()


if __name__ == '__main__':
    unittest.main()
