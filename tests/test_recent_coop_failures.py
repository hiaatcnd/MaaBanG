"""Replay the November 4–5 cooperative failures with production OCR, offline."""
from pathlib import Path
import sys
import tempfile
import traceback
import unittest
from unittest.mock import Mock, patch

import numpy as np
from PIL import Image

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'agent'))
from chart_policy import ChartOptions
from notifications import dialog_box
from online_live import OnlineLiveFlow


def frame(relative):
    return np.asarray(Image.open(ROOT / 'tests/fixtures' / relative).convert('RGB'))[:, :, ::-1].copy()


class RecentCoopFailureTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        for relative in ('coop/cut_in_20261104.png', 'coop/cut_in_20261105_0005.png',
                         'coop/cut_in_20261105_1002.png', 'notifications/trial_goals.png'):
            if not (ROOT / 'tests/fixtures' / relative).is_file():
                raise unittest.SkipTest(f'Private failure screenshot required: {relative}')
        if not (ROOT / 'assets/resource/model/ocr/rec.onnx').is_file():
            raise unittest.SkipTest('Local OCR models required')
        from maa.controller import CustomController
        from maa.resource import Resource
        from maa.tasker import Tasker
        cls.current = frame('coop/ready.png')

        class Screens(CustomController):
            def connect(self): return True
            def request_uuid(self): return 'recent-coop-failure-replay'
            def screencap(self): return cls.current

        cls.resource = Resource()
        assert cls.resource.post_bundle(ROOT / 'assets/resource').wait().succeeded
        cls.controller = Screens()
        assert cls.controller.post_connection().wait().succeeded
        cls.tasker = Tasker()
        assert cls.tasker.bind(cls.resource, cls.controller)

    def run_action(self, callback):
        from maa.custom_action import CustomAction
        errors = []
        with tempfile.TemporaryDirectory() as folder:
            class Check(CustomAction):
                def run(self, context, argv):
                    try:
                        callback(context, folder)
                        return True
                    except Exception:
                        errors.append(traceback.format_exc())
                        return False
            self.resource.register_custom_action('RecentCoopReplay', Check())
            job = self.tasker.post_task('RecentCoopReplay', {'RecentCoopReplay': {
                'action': 'Custom', 'custom_action': 'RecentCoopReplay',
                'pre_delay': 0, 'post_delay': 0}}).wait()
            self.assertTrue(job.succeeded, '\n'.join(errors))

    def test_three_recorded_ready_pages_do_not_toggle_already_off_cut_in(self):
        def replay(context, folder):
            merged_boxes = 0
            for name in ('cut_in_20261104', 'cut_in_20261105_0005', 'cut_in_20261105_1002'):
                flow = OnlineLiveFlow(context, ChartOptions.parse({'mode': 'coop'}), folder)
                flow.image = frame(f'coop/{name}.png')
                flow.tap = Mock(side_effect=AssertionError('Already off: no input allowed'))
                flow.quick_tap = flow.tap
                # Original logs have x=469 in all three runs. The later error
                # screenshot may split it again; retain both observed layouts.
                hits = flow.ocr([110, 615, 610, 75])
                self.assertTrue(any('Cut in' in h.text for h in hits), name)
                merged_boxes += any(h.box[0] == 469 and 'Cut in' in h.text for h in hits)
                state = flow.disable_mv(fresh=False)
                self.assertIsNone(state.background)
                self.assertFalse(state.cut_in)
                flow.tap.assert_not_called()
            self.assertGreaterEqual(merged_boxes, 2)
        self.run_action(replay)

    def test_trial_goals_close_then_settlement_stops_at_room_without_recount(self):
        def replay(context, folder):
            type(self).current = frame('notifications/trial_goals.png')
            self.assertIsNotNone(dialog_box(self.current))
            flow = OnlineLiveFlow(context, ChartOptions.parse({'mode': 'coop', 'fire': 0}), folder)
            flow.report['completed_rounds'] = 1
            row = {'counted': True, 'status': 'result_confirmed'}
            taps = []

            def advance(x, y):
                if not taps:
                    self.assertTrue(520 < x < 760 and 610 < y < 682, (x, y))
                    type(self).current = frame('notifications/event_result.png')
                else:
                    self.assertEqual(len(taps), 1, 'Must stop when room selection is reached')
                    self.assertTrue(940 < x < 1230 and 600 < y < 705, (x, y))
                    type(self).current = frame('versus/room_event.png')
                taps.append((x, y))

            flow.tap = Mock(side_effect=advance)
            flow.pause = Mock(side_effect=AssertionError('Settlement did not advance'))
            with patch('notifications.time.sleep'):
                flow.settle_online(row)
            self.assertEqual(len(taps), 2)
            self.assertEqual(flow.report['completed_rounds'], 1)
            self.assertEqual(row, {'counted': True, 'status': 'result_confirmed'})
        self.run_action(replay)


if __name__ == '__main__':
    unittest.main()
