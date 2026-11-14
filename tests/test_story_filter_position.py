import sys
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock, patch

import numpy as np
from PIL import Image

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT/'agent'))

from costume_unlock import FlowError
from mining_stories import StoryMiningFlow, member_filter_box


BOX = [129, 34, 1022, 653]


def hit(text, box):
    return SimpleNamespace(text=text, box=box)


class StoryFilterPositionTests(unittest.TestCase):
    def flow(self, visible_after=0):
        flow = StoryMiningFlow.__new__(StoryMiningFlow)
        flow.ctx = SimpleNamespace(tasker=SimpleNamespace(stopping=False))
        flow.image = np.full((720, 1280, 3), 255, dtype=np.uint8)
        flow.snap = Mock()
        flow.swipe = Mock()
        flow.reco = Mock(return_value=True)
        flow.require_clear_notification_overlay = Mock()
        toggle = hit('全选/取消全选', [950, 60, 175, 40])
        label = hit('种类', [164, 171, 88, 30])
        flow.hit_text = Mock(side_effect=lambda roi, expected:
            toggle if '全选' in expected else label if flow.swipe.call_count >= visible_after else None)
        return flow, toggle, label

    def position(self, flow):
        with patch('mining_stories.member_filter_box', return_value=BOX):
            return flow.position_filter_top(BOX)

    def test_visible_controls_and_checkbox_row_need_no_scroll(self):
        flow, toggle, label = self.flow()
        self.assertEqual(self.position(flow), (toggle, label))
        flow.swipe.assert_not_called()

    def test_stop_scrolling_immediately_when_controls_become_visible(self):
        for count in (1, 2, 8):
            with self.subTest(count=count):
                flow, toggle, label = self.flow(visible_after=count)
                self.assertEqual(self.position(flow), (toggle, label))
                self.assertEqual(flow.swipe.call_count, count)
                flow.swipe.assert_called_with(1121, 194, 434)

    def test_label_without_visible_checkbox_row_still_needs_positioning(self):
        flow, toggle, label = self.flow()
        clipped = hit('属性', [164, 530, 88, 30])
        flow.hit_text.side_effect = lambda roi, expected: (
            toggle if '全选' in expected else label if flow.swipe.call_count else clipped)
        self.assertEqual(self.position(flow), (toggle, label))
        flow.swipe.assert_called_once()

    def test_changed_page_never_scrolls_using_old_filter_coordinates(self):
        flow, _, _ = self.flow(visible_after=2)
        flow.reco.return_value = None
        with self.assertRaisesRegex(FlowError, '弹窗已变化'):
            self.position(flow)
        flow.swipe.assert_not_called()
        flow.hit_text.assert_not_called()

    def test_page_change_after_one_scroll_stops_further_scrolling(self):
        flow, _, _ = self.flow(visible_after=3)
        flow.reco.side_effect = lambda name: flow.swipe.call_count == 0
        with self.assertRaisesRegex(FlowError, '弹窗已变化'):
            self.position(flow)
        flow.swipe.assert_called_once()

    def test_changed_panel_position_does_not_reuse_old_scroll_coordinates(self):
        flow, _, _ = self.flow(visible_after=2)
        with patch('mining_stories.member_filter_box', return_value=[205, 75, 870, 570]):
            with self.assertRaisesRegex(FlowError, '位置已变化'):
                flow.position_filter_top(BOX)
        flow.swipe.assert_not_called()

    def test_no_effect_scrolls_have_an_eight_attempt_limit(self):
        flow, _, _ = self.flow(visible_after=20)
        with self.assertRaisesRegex(FlowError, '回顶已达上限'):
            self.position(flow)
        self.assertEqual(flow.swipe.call_count, 8)
        self.assertEqual(flow.snap.call_count, 9)

    def test_stop_request_prevents_initial_and_subsequent_scrolls(self):
        for after_first in (False, True):
            with self.subTest(after_first=after_first):
                flow, _, _ = self.flow(visible_after=2)
                if after_first:
                    flow.swipe.side_effect = lambda *args: setattr(flow.ctx.tasker, 'stopping', True)
                else:
                    flow.ctx.tasker.stopping = True
                with self.assertRaisesRegex(FlowError, '用户停止'):
                    self.position(flow)
                self.assertEqual(flow.swipe.call_count, int(after_first))

    def test_recorded_filter_pages_with_real_ocr_need_no_scroll(self):
        if not (ROOT/'assets/resource/model/ocr/rec.onnx').is_file():
            self.skipTest('Local OCR models required')
        from maa.controller import CustomController
        from maa.custom_action import CustomAction
        from maa.resource import Resource
        from maa.tasker import Tasker

        class Controller(CustomController):
            def connect(self): return True
            def request_uuid(self): return 'story-filter-position-fixtures'
            def screencap(self): return np.zeros((720, 1280, 3), dtype=np.uint8)

        resource = Resource()
        self.assertTrue(resource.post_bundle(ROOT/'assets/resource').wait().succeeded)
        controller = Controller()
        self.assertTrue(controller.post_connection().wait().succeeded)
        tasker = Tasker()
        tasker.bind(resource, controller)
        owner, errors = self, []

        class Check(CustomAction):
            def run(self, context, argv):
                try:
                    for filename in ('skill_practice/filter.png', 'lowres/member_filter.png'):
                        flow = StoryMiningFlow.__new__(StoryMiningFlow)
                        flow.ctx = context
                        flow.image = np.array(Image.open(ROOT/'tests/fixtures'/filename)
                                              .convert('RGB'))[:, :, ::-1].copy()
                        flow.snap = Mock()
                        flow.swipe = Mock()
                        box = member_filter_box(flow.image)
                        toggle, label = flow.position_filter_top(box)
                        owner.assertIsNotNone(toggle)
                        owner.assertIsNotNone(label)
                        y = label.box[1]+label.box[3]//2+54
                        owner.assertTrue(all(flow.checkbox(box[0]+offset, y)
                                             for offset in (55, 254, 453, 652)))
                        flow.swipe.assert_not_called()
                    return True
                except Exception as error:
                    errors.append(error)
                    return False

        resource.register_custom_action('CheckFilterPosition', Check())
        job = tasker.post_task('CheckFilterPosition', {'CheckFilterPosition': {
            'action': 'Custom', 'custom_action': 'CheckFilterPosition'}}).wait()
        if errors:
            raise errors[0]
        self.assertTrue(job.succeeded)


if __name__ == '__main__':
    unittest.main()
