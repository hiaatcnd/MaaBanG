"""Replay the one-time resource update and login events with real Maa OCR."""
import json
import os
from pathlib import Path
import sys
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import patch

import numpy as np
from PIL import Image

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT/'agent'))
from wakeup import WakeupFlow, startup_decision, RULES
from notifications import dialog_box


class WakeupReplayTests(unittest.TestCase):
    def test_update_events_and_promotion_reach_only_unobstructed_home(self):
        fixture_dir = Path(os.environ.get('MAABANG_WAKEUP_FIXTURES', ROOT/'tests/fixtures/wakeup'))
        if not (fixture_dir/'download.png').is_file():
            self.skipTest('Private startup screenshots retained locally; set MAABANG_WAKEUP_FIXTURES to replay')
        resource_dir = Path(os.environ.get('MAABANG_TEST_RESOURCE_DIR', ROOT/'assets/resource'))
        if not (resource_dir/'model/ocr/rec.onnx').exists():
            if 'MAABANG_TEST_RESOURCE_DIR' in os.environ:
                self.fail('Packaged OCR model is missing')
            self.skipTest('Local OCR model required')
        from maa.controller import CustomController
        from maa.custom_action import CustomAction
        from maa.resource import Resource
        from maa.tasker import Tasker
        frames = {path.stem: np.array(Image.open(path).convert('RGB'))[:, :, ::-1].copy()
                  for path in fixture_dir.glob('*.png')}
        sequence = ['title', 'download', 'login_reward', 'event_medals', 'event_stars',
                    'event_second', 'special_notice', 'promotion', 'home']
        cursor = [0]
        class Screens(CustomController):
            def connect(self): return True
            def request_uuid(self): return 'wakeup-20261022-replay'
            def screencap(self): return frames[sequence[cursor[0]]]
            def get_custom_info(self):
                return dict(type='adb', adb_path='unused', adb_serial='fixture')
        controller = Screens()
        self.assertTrue(controller.post_connection().wait().succeeded)
        resource = Resource()
        self.assertTrue(resource.post_bundle(resource_dir).wait().succeeded)
        tasker = Tasker(); tasker.bind(resource, controller)
        case, errors = self, []
        with tempfile.TemporaryDirectory() as folder:
            class Replay(CustomAction):
                def run(self, context, argv):
                    try:
                        # The desktop supplies an ordered array of partial option
                        # overrides. Maa must preserve both default and selected fields.
                        merged = context.get_node_data('SW_Game')['attach']
                        case.assertEqual(merged['user'], '10')
                        case.assertEqual(merged['package'], 'com.bilibili.star.bili')
                        case.assertEqual(merged['game_timeout'], '1200')
                        # CustomController info is nested; replace only the read-only
                        # construction input. Every recognition uses the actual engine.
                        with patch('wakeup.AndroidGame') as game:
                            game.return_value.user = 10
                            game.return_value.package = 'com.bilibili.star.bili'
                            game.return_value.foreground.return_value = (10, 'com.bilibili.star.bili')
                            original = context.tasker.controller
                            proxy = SimpleNamespace(info=dict(type='adb', adb_path='unused', adb_serial='fixture'))
                            fake_context = SimpleNamespace(tasker=SimpleNamespace(controller=proxy, stopping=False))
                            flow = WakeupFlow(fake_context, {}, folder)
                            flow.ctx, flow.controller = context, original
                            game.return_value.selected_foreground.return_value = True
                            flow.image = frames['promotion']
                            case.assertFalse(flow.clear_home(), 'Dimmed home behind promotion is not ready')
                            flow.image = frames['download']
                            box = dialog_box(flow.image)
                            case.assertIsNotNone(box)
                            hits = flow.ocr(box)
                            case.assertEqual(startup_decision(hits, flow.rules, modal=True,
                                                             allow_download=False)[0], 'blocked')
                            flow.image = frames['reward_animation']
                            case.assertEqual(startup_decision(flow.ocr([0,0,1280,720]), flow.rules)[0], 'unknown')
                            def tap(x, y):
                                current = sequence[cursor[0]]
                                bounds = {
                                    'title': (400, 600, 900, 670),
                                    'download': (650, 540, 900, 620),
                                    'special_notice': (390, 500, 900, 670),
                                    'promotion': (680, 600, 850, 690),
                                }.get(current, (900, 600, 1200, 700))
                                left, top, right, bottom = bounds
                                case.assertTrue(left < x < right and top < y < bottom, (current, x, y))
                                cursor[0] += 1
                            flow.tap = tap
                            with patch('wakeup.pause'), patch('notifications.time.sleep'):
                                flow.run()
                            case.assertEqual(cursor[0], len(sequence)-1)
                            case.assertEqual(flow.report['status'], 'finished')
                            case.assertEqual(sum(s['action']=='活动下一步' for s in flow.report['steps']), 4)
                            case.assertTrue((Path(folder)/'trace.jsonl').exists())
                        return True
                    except Exception as exc:
                        errors.append(repr(exc))
                        return False
            resource.register_custom_action('WakeupReplay', Replay())
            job = tasker.post_task('WakeupReplay', [
                {'WakeupReplay': dict(action='Custom', custom_action='WakeupReplay')},
                {'SW_Game': {'attach': {'user': '0', 'package': 'com.bilibili.star.bili'}}},
                {'SW_Game': {'attach': {'user': '10'}}},
            ]).wait()
            self.assertTrue(job.succeeded, errors)


if __name__ == '__main__': unittest.main()
