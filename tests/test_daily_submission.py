"""A missed free-confirmation tap may be retried, an accepted draw may not."""
import sys
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock, patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'agent'))
from daily_tasks import DailyFlow
from costume_unlock import FlowError

FREE = '每日最多3次免费！演出招募 进行招募。确认吗？'


class DailySubmissionTests(unittest.TestCase):
    def test_recorded_confirmation_ocr_retries_only_until_dialog_disappears(self):
        import os
        import numpy as np
        from PIL import Image
        from maa.controller import CustomController
        from maa.custom_action import CustomAction
        from maa.resource import Resource
        from maa.tasker import Tasker
        root = Path(__file__).resolve().parents[1]
        bundle = Path(os.environ.get('MAABANG_TEST_RESOURCE_DIR', root/'assets/resource'))
        if not (bundle/'model/ocr/rec.onnx').is_file():
            self.skipTest('Local OCR models required')
        frame = np.zeros((720,1280,3), dtype=np.uint8)
        frame[175:545,330:950] = np.asarray(Image.open(
            root/'tests/fixtures/daily/daily_free_confirm.png').convert('RGB'))[:,:,::-1]
        class Controller(CustomController):
            def connect(self): return True
            def request_uuid(self): return 'daily-submit-recorded'
            def screencap(self): return frame
        resource = Resource()
        self.assertTrue(resource.post_bundle(bundle).wait().succeeded)
        controller = Controller()
        self.assertTrue(controller.post_connection().wait().succeeded)
        tasker = Tasker()
        tasker.bind(resource, controller)
        evidence = []
        class Check(CustomAction):
            def run(self, ctx, argv):
                flow = DailyFlow(ctx)
                flow.image = frame.copy()
                flow.tap = Mock()
                flow.snap = Mock(side_effect=lambda: setattr(flow, 'image',
                    frame.copy() if flow.tap.call_count < 2 else np.zeros_like(frame)))
                with patch('daily_tasks.time.monotonic', side_effect=range(0,100,2)):
                    flow.submit_daily_draw()
                evidence.extend([call.args for call in flow.tap.call_args_list])
                return True
        resource.register_custom_action('CheckDailySubmit', Check())
        self.assertTrue(tasker.post_task('CheckDailySubmit', {'CheckDailySubmit': {
            'action': 'Custom', 'custom_action': 'CheckDailySubmit'}}).wait().succeeded)
        self.assertEqual(evidence, [(770,476)] * 2)

    def flow(self, confirmations, text=FREE):
        flow = DailyFlow(SimpleNamespace(tasker=SimpleNamespace(controller=None, stopping=False)))
        flow.text = Mock(return_value=text)
        flow.snap = Mock()
        flow.reco = Mock(side_effect=confirmations)
        flow.tap = Mock()
        return flow

    @patch('daily_tasks.time.monotonic', side_effect=range(0, 100, 2))
    def test_missed_tap_retries_same_free_dialog_then_finishes_once(self, _):
        flow = self.flow([True, False])
        flow.report.update(draws=2, draw_stage='result', draw_attempts={'result': 1})
        flow.submit_daily_draw()
        self.assertEqual([call.args for call in flow.tap.call_args_list], [(770,476)] * 2)
        self.assertEqual(flow.report['draw_submit_attempts'], 2)
        self.assertEqual(flow.report['draw_stage'], 'submitting')
        self.assertEqual(flow.report['draw_attempts'], {})
        self.assertEqual(flow.report['draws'], 2)  # Only finish_draw can confirm completion.
        self.assertTrue(flow.report['draw_in_flight'])

    @patch('daily_tasks.time.monotonic', side_effect=range(100))
    def test_loading_or_animation_handoff_never_rechecks_or_resubmits(self, _):
        flow = self.flow([False, True])
        flow.submit_daily_draw()
        flow.tap.assert_called_once_with(770,476)
        self.assertEqual(flow.reco.call_count, 1)

    @patch('daily_tasks.time.monotonic', side_effect=range(0, 100, 1))
    @patch('daily_tasks.time.sleep')
    def test_persistent_confirmation_is_bounded(self, *_):
        flow = self.flow([True] * 20)
        with self.assertRaisesRegex(FlowError, '仍未关闭'):
            flow.submit_daily_draw()
        self.assertEqual(flow.tap.call_count, 3)
        self.assertEqual(flow.report['draws'], 0)

    @patch('daily_tasks.time.monotonic', side_effect=[0, 1, 1, 2, 2, 3])
    @patch('daily_tasks.time.sleep')
    def test_confirmation_is_not_clicked_again_before_cooldown(self, *_):
        flow = self.flow([True, True, False])
        flow.submit_daily_draw()
        flow.tap.assert_called_once_with(770,476)

    def test_paid_initial_confirmation_is_never_submitted(self):
        flow = self.flow([], FREE + ' 消耗100星石')
        with self.assertRaises(ValueError):
            flow.submit_daily_draw()
        flow.tap.assert_not_called()
        self.assertFalse(flow.report.get('draw_in_flight', False))

    @patch('daily_tasks.time.monotonic', side_effect=range(100))
    def test_paid_or_changed_confirmation_never_retries(self, _):
        for changed in (FREE + ' 消耗100星石', FREE + ' 内容改变'):
            with self.subTest(changed=changed):
                flow = self.flow([True])
                flow.text.side_effect = [FREE, changed]
                with self.assertRaises((ValueError, FlowError)):
                    flow.submit_daily_draw()
                flow.tap.assert_called_once_with(770,476)


if __name__ == '__main__':
    unittest.main()
