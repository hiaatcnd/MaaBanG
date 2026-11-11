"""Frame reuse must not weaken input, stop, configuration or capture boundaries."""
import copy
from pathlib import Path
import sys
from types import SimpleNamespace
import unittest
from unittest.mock import Mock, patch

import numpy as np

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'agent'))
from costume_unlock import CostumeFlow, FlowError
from frame_recognition import FrameRecognitionMixin


class Flow(FrameRecognitionMixin, CostumeFlow):
    pass


def node(kind='OCR', **params):
    return {'enabled': True, 'recognition': {'type': kind,
            'param': {'roi': [0, 0, 100, 100], 'expected': ['.+'], **params}}}


class FrameRecognitionTests(unittest.TestCase):
    def flow(self, *, config=None, result=None):
        image = np.zeros((720, 1280, 3), dtype=np.uint8)
        job = SimpleNamespace(wait=Mock(return_value=SimpleNamespace(succeeded=True)))
        controller = SimpleNamespace(cached_image=image, post_screencap=Mock(return_value=job),
                                     post_click=Mock(return_value=job), post_swipe=Mock(return_value=job))
        self.config = node() if config is None else config
        context = SimpleNamespace(tasker=SimpleNamespace(controller=controller, stopping=False),
            get_node_data=Mock(side_effect=lambda name: copy.deepcopy(self.config)),
            run_recognition=Mock(return_value=result or SimpleNamespace(hit=True, box=[1, 2, 3, 4])))
        flow = Flow(context, 0)
        flow.snap()
        return flow

    def test_repeated_hits_and_confirmed_misses_run_once_per_frame(self):
        for hit in (True, False):
            with self.subTest(hit=hit):
                flow = self.flow(result=SimpleNamespace(hit=hit))
                first = flow.reco('page')
                self.assertIs(flow.reco('page'), first)
                self.assertEqual(first is not None, hit)
                flow.ctx.run_recognition.assert_called_once()

    def test_all_override_values_and_node_names_are_part_of_the_key(self):
        flow = self.flow()
        inputs = [dict(roi=[1, 2, 30, 40], expected='A', only_rec=False),
                  dict(roi=[2, 2, 30, 40], expected='A', only_rec=False),
                  dict(roi=[1, 2, 30, 40], expected='B', only_rec=False),
                  dict(roi=[1, 2, 30, 40], expected='A', only_rec=True),
                  dict(roi=[1, 2, 30, 40], expected='A', only_rec=False, threshold=.9)]
        for options in inputs:
            flow.reco('ocr', **options)
            flow.reco('ocr', **dict(reversed(list(options.items()))))
        flow.reco('other', **inputs[0])
        self.assertEqual(flow.ctx.run_recognition.call_count, len(inputs) + 1)

    def test_mutating_override_list_does_not_mutate_an_existing_key(self):
        flow = self.flow()
        roi = [1, 2, 30, 40]
        flow.reco('ocr', roi=roi)
        roi[0] = 100
        flow.reco('ocr', roi=roi)
        self.assertEqual(flow.ctx.run_recognition.call_count, 2)

    def test_node_configuration_changes_invalidate_the_answer(self):
        flow = self.flow()
        flow.reco('ocr')
        self.config['recognition']['param']['threshold'] = .95
        flow.reco('ocr')
        self.config['enabled'] = False
        flow.ctx.run_recognition.return_value = None
        self.assertIsNone(flow.reco('ocr'))
        self.assertEqual(flow.ctx.run_recognition.call_count, 3)

    def test_custom_composite_referenced_roi_and_color_filter_are_never_cached(self):
        configs = [node('Custom'), node('And'), node(roi='other'), node(roi=True),
                   node(color_filter='mask'), None]
        for config in configs:
            with self.subTest(config=config):
                flow = self.flow()
                self.config = config
                flow.reco('node')
                flow.reco('node')
                self.assertEqual(flow.ctx.run_recognition.call_count, 2)

    def test_override_that_changes_recognizer_or_references_context_is_not_cached(self):
        for override in ({'recognition': 'Custom'}, {'roi': 'other'}, {'color_filter': 'mask'}):
            with self.subTest(override=override):
                flow = self.flow()
                flow.reco('node', **override)
                flow.reco('node', **override)
                self.assertEqual(flow.ctx.run_recognition.call_count, 2)

    def test_template_match_is_cached(self):
        flow = self.flow(config=node('TemplateMatch', template=['button.png']))
        flow.reco('button')
        flow.reco('button')
        flow.ctx.run_recognition.assert_called_once()

    def test_new_capture_invalidates_even_with_the_same_controller_buffer(self):
        flow = self.flow()
        image = flow.image
        flow.reco('page')
        image[0, 0] = 255
        flow.snap()
        self.assertIs(flow.image, image)
        flow.reco('page')
        self.assertEqual(flow.ctx.run_recognition.call_count, 2)

    def test_direct_image_replacement_and_each_flow_have_separate_caches(self):
        flow = self.flow()
        flow.reco('page')
        flow.image = flow.image.copy()
        flow.reco('page')
        other = Flow(flow.ctx, 0)
        other.image = flow.image
        other.reco('page')
        self.assertEqual(flow.ctx.run_recognition.call_count, 3)

    def test_input_attempt_invalidates_even_when_controller_reports_failure(self):
        for operation in ('tap', 'swipe'):
            for succeeded in (True, False):
                with self.subTest(operation=operation, succeeded=succeeded):
                    flow = self.flow()
                    flow.reco('page')
                    job = (flow.controller.post_click if operation == 'tap' else
                           flow.controller.post_swipe).return_value
                    job.wait.return_value.succeeded = succeeded
                    args = (1, 2) if operation == 'tap' else (1, 2, 3)
                    with patch('costume_unlock.time.sleep'):
                        if succeeded:
                            getattr(flow, operation)(*args)
                        else:
                            with self.assertRaises(FlowError):
                                getattr(flow, operation)(*args)
                    flow.reco('page')
                    self.assertEqual(flow.ctx.run_recognition.call_count, 2)

    def test_failed_capture_cannot_reuse_preceding_cached_answer(self):
        flow = self.flow()
        flow.reco('page')
        flow.controller.post_screencap.return_value.wait.return_value.succeeded = False
        with self.assertRaisesRegex(FlowError, '截图失败'):
            flow.snap()
        flow.ctx.run_recognition.return_value = SimpleNamespace(hit=False)
        self.assertIsNone(flow.reco('page'))
        self.assertEqual(flow.ctx.run_recognition.call_count, 2)

    def test_none_and_recognition_exceptions_are_not_cached_as_misses(self):
        for failure in (None, RuntimeError('recognition failed')):
            with self.subTest(failure=failure):
                flow = self.flow()
                success = SimpleNamespace(hit=True)
                flow.ctx.run_recognition.side_effect = [failure, success]
                if isinstance(failure, Exception):
                    with self.assertRaisesRegex(RuntimeError, 'recognition failed'):
                        flow.reco('page')
                else:
                    self.assertIsNone(flow.reco('page'))
                self.assertIs(flow.reco('page'), success)
                self.assertEqual(flow.ctx.run_recognition.call_count, 2)

    def test_cache_hit_still_checks_stop(self):
        flow = self.flow()
        flow.reco('page')
        flow.ctx.tasker.stopping = True
        with self.assertRaisesRegex(FlowError, '停止'):
            flow.reco('page')
        flow.ctx.run_recognition.assert_called_once()

    def test_no_image_never_caches_even_if_recognizer_returns_a_detail(self):
        flow = self.flow()
        flow.image = None
        flow.reco('page')
        flow.reco('page')
        self.assertEqual(flow.ctx.run_recognition.call_count, 2)

    def test_real_framework_reuses_same_frame_ocr_without_mutating_node(self):
        from PIL import Image
        from maa.controller import CustomController
        from maa.custom_action import CustomAction
        from maa.resource import Resource
        from maa.tasker import Tasker
        model = ROOT / 'assets/resource/model/ocr'
        if not all((model / name).is_file() for name in ('det.onnx', 'rec.onnx', 'keys.txt')):
            self.skipTest('Local OCR models are required for the screenshot integration test')
        frame = np.asarray(Image.open(ROOT / 'tests/fixtures/coop/ready.png').convert('RGB'))[:, :, ::-1].copy()

        class Screens(CustomController):
            def connect(self): return True
            def request_uuid(self): return 'frame-recognition-regression'
            def screencap(self): return frame

        controller = Screens()
        self.assertTrue(controller.post_connection().wait().succeeded)
        resource = Resource()
        self.assertTrue(resource.post_bundle(ROOT / 'assets/resource').wait().succeeded)
        tasker = Tasker()
        self.assertTrue(tasker.bind(resource, controller))
        case = self
        observed = {}

        class Check(CustomAction):
            def run(self, context, argv):
                try:
                    flow = Flow(context, 0)
                    flow.snap()
                    original = copy.deepcopy(context.get_node_data('CU_OCR'))
                    with patch.object(context, 'run_recognition', wraps=context.run_recognition) as recognize:
                        first = flow.ocr([540, 550, 452, 65], 'EASY|NORMAL|HARD|EXPERT|SPECIAL')
                        case.assertTrue(first)
                        again = flow.ocr([540, 550, 452, 65], 'EASY|NORMAL|HARD|EXPERT|SPECIAL')
                        case.assertEqual([h.text for h in first], [h.text for h in again])
                        case.assertEqual(recognize.call_count, 1)
                        flow.snap()
                        flow.ocr([540, 550, 452, 65], 'EASY|NORMAL|HARD|EXPERT|SPECIAL')
                        case.assertEqual(recognize.call_count, 2)
                    case.assertEqual(context.get_node_data('CU_OCR'), original)
                    observed['ok'] = True
                    return True
                except Exception as exc:
                    observed['error'] = repr(exc)
                    return False

        resource.register_custom_action('FrameRecognitionFixture', Check())
        job = tasker.post_task('FrameRecognitionFixture', {
            'FrameRecognitionFixture': {'action': 'Custom', 'custom_action': 'FrameRecognitionFixture'}}).wait()
        self.assertTrue(job.succeeded, observed)


if __name__ == '__main__':
    unittest.main()
