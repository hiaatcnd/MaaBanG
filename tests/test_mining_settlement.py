import sys
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock, call, patch

import numpy as np
from PIL import Image

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'agent'))
from costume_unlock import FlowError
from mining_live import MiningLiveFlow


class MiningSettlementTests(unittest.TestCase):
    def flow(self):
        flow = MiningLiveFlow.__new__(MiningLiveFlow)
        flow.report = {'rounds': [{}]}
        flow.image = self.frame('song_achievement_nested.png')
        flow.check_stop = Mock()
        flow.reco = Mock(return_value=None)
        # Background remains readable even with the reward in front.
        flow.hit_text = Mock(return_value=SimpleNamespace(box=[601,604,72,46]))
        flow.tap_hit = Mock()
        flow.snap = Mock()
        return flow

    def frame(self, name):
        return np.array(Image.open(ROOT / 'tests/fixtures/notifications' / name).convert('RGB'))[:, :, ::-1].copy()

    def hits(self, title, label, y):
        return [SimpleNamespace(text=title, box=[400,150 if y == 525 else 60,200,30]),
                SimpleNamespace(text=label, box=[605,y,70,35])]

    @patch('notifications.time.sleep')
    def test_nested_reward_is_confirmed_before_achievement_list(self, _):
        flow = self.flow()
        inner = self.hits('达成报酬', '确定', 525)
        outer = self.hits('达成报酬一览', '关闭', 605)
        flow.ocr = Mock(side_effect=[inner, outer])
        frames = iter([self.frame('song_achievement.png'), np.zeros_like(flow.image)])
        flow.snap.side_effect = lambda: setattr(flow, 'image', next(frames))
        self.assertTrue(flow.result_modals())
        self.assertEqual(flow.tap_hit.call_args_list, [call(inner[-1]), call(outer[-1])])
        flow.hit_text.assert_not_called()

    def test_unrecognized_front_dialog_blocks_readable_background_close(self):
        flow = self.flow()
        flow.ocr = Mock(return_value=self.hits('达成报酬', '识别失败', 525))
        with self.assertRaisesRegex(FlowError, '未识别的弹窗'):
            flow.result_modals()
        flow.tap_hit.assert_not_called()
        self.assertFalse(any(args.args[0] == [510,580,260,80] for args in flow.hit_text.call_args_list))

    def test_story_skip_confirmation_remains_owned_by_settlement(self):
        flow = self.flow()
        flow.ocr = Mock(return_value=self.hits('确定跳过这个故事吗？', '取消', 525))
        flow.hit_text = Mock(return_value=None)
        self.assertFalse(flow.result_modals())
        flow.tap_hit.assert_not_called()

    @patch('notifications.time.sleep')
    def test_stuck_front_dialog_stops_after_three_attempts(self, _):
        flow = self.flow()
        inner = self.hits('达成报酬', '确定', 525)
        flow.ocr = Mock(return_value=inner)
        with self.assertRaisesRegex(FlowError, '未消失'):
            flow.result_modals()
        self.assertEqual(flow.tap_hit.call_args_list, [call(inner[-1])] * 3)
        flow.hit_text.assert_not_called()

    def test_uncovered_full_combo_result_is_still_recorded(self):
        flow = self.flow()
        flow.image = np.zeros_like(flow.image)
        flow.hit_text.side_effect = [True, False]
        self.assertFalse(flow.result_modals())
        self.assertTrue(flow.report['rounds'][-1]['full_combo_confirmed'])
        flow.tap_hit.assert_not_called()


if __name__ == '__main__':
    unittest.main()
