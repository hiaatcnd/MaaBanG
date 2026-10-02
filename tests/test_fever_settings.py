import json
import sys
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock

import numpy as np
from PIL import Image

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT/'agent'))
from auto_live import LiveFlow
from chart_policy import ChartOptions
from live_policy import LiveOptions
from costume_unlock import FlowError


class FeverTests(unittest.TestCase):
    def flow(self, initial=False, remaining=12, stamps=142, refused=False, saved=None):
        f = LiveFlow(SimpleNamespace(tasker=SimpleNamespace(controller=None, stopping=False)), LiveOptions())
        state = {'enabled': initial, 'page': 'dialog', 'saves': 0}
        frames = {value: np.asarray(Image.open(ROOT/f'tests/fixtures/fever/{name}.png').convert('RGB'))[:, :, ::-1].copy()
                  for value, name in [(False, 'off'), (True, 'on')]}
        f.snap = Mock(side_effect=lambda: setattr(f, 'image', frames[state['enabled']]))
        f.reco = Mock(side_effect=lambda node: state['page'] == ('dialog' if node=='LV_FeverDialog' else 'menu'))
        f.wait = Mock(side_effect=lambda *args: f.snap())
        f.text = Mock(return_value=f'本期剩余使用次数：{remaining}次 所持Fever印章：{stamps}')
        f.require_clear_notification_overlay = Mock()
        def tap(x, y):
            if x==547 or not refused:
                state['enabled'] = x==435
        f.tap = Mock(side_effect=tap)
        def click(node):
            if node=='LV_FeverConfirm':
                state['page']='menu'; state['saves']+=1
                if saved is not None: state['enabled']=saved
            elif node=='LV_FeverEntry': state['page']='dialog'
        f.click = Mock(side_effect=click)
        f.tap_hit = Mock(side_effect=lambda hit: state.update(page='dialog'))
        return f, state

    def test_on_and_off_save_and_verify_real_radio_pixels(self):
        for initial, target in [(False, 'on'), (True, 'off'), (True, 'on'), (False, 'off')]:
            with self.subTest(initial=initial, target=target):
                f, state = self.flow(initial)
                self.assertEqual(f.configure_fever(target), target=='on')
                self.assertEqual(state['page'], 'menu')
                self.assertEqual(state['saves'], 2)
                self.assertEqual(f.tap.call_count, int(initial != (target=='on')))

    def test_exhausted_or_missing_stamps_continues_off_without_enabling(self):
        for initial in (False, True):
            for remaining, stamps, reason in [(0,142,'次数'), (12,0,'不足')]:
                with self.subTest(initial=initial, remaining=remaining, stamps=stamps):
                    f, state = self.flow(initial, remaining, stamps)
                    self.assertFalse(f.configure_fever('on'))
                    self.assertNotIn((435,332), [c.args for c in f.tap.call_args_list])
                    self.assertIn(reason, f.report['fever_settings'][-1]['reason'])

    def test_game_refusal_and_unsaved_on_continue_off_without_retries(self):
        for kwargs in [{'refused': True}, {'saved': False}]:
            f, state = self.flow(**kwargs)
            self.assertFalse(f.configure_fever('on'))
            f.tap.assert_called_once_with(435,332)
            self.assertTrue(f.report['fever_settings'][-1]['reason'])
            self.assertEqual(state['page'], 'menu')

    def test_unsaved_off_is_not_allowed_to_spend_stamps(self):
        f, _ = self.flow(True, saved=True)
        with self.assertRaisesRegex(FlowError, '关闭状态未保存'):
            f.configure_fever('off')

    def test_ambiguous_radio_state_stops(self):
        f, _ = self.flow()
        f.snap=Mock(side_effect=lambda: setattr(f,'image',np.zeros((720,1280,3),dtype=np.uint8)))
        with self.assertRaisesRegex(FlowError, '开关状态'):
            f.configure_fever('on')
        f.tap.assert_not_called()

    def test_unavailable_event_entry_does_not_block_live(self):
        f, state = self.flow()
        state['page']='menu'
        f.reco=Mock(side_effect=lambda node: node=='LV_Menu')
        self.assertFalse(f.configure_fever('on'))
        f.tap.assert_not_called(); f.click.assert_not_called(); f.tap_hit.assert_not_called()

    def test_unknown_modal_after_enable_stops(self):
        f, _ = self.flow()
        f.reco=Mock(return_value=False)
        f.text=Mock(return_value='未知弹窗')
        with self.assertRaisesRegex(FlowError, '未识别页面'):
            f.dismiss_fever_refusal()
        f.tap_hit.assert_not_called()

    def test_explicit_unavailable_notice_can_be_closed(self):
        f, _ = self.flow()
        f.reco=Mock(return_value=False)
        f.text=Mock(return_value='已获得全部活动点数报酬，无法使用Fever印章')
        f.hit_text=Mock(return_value=object())
        self.assertIn('无法', f.dismiss_fever_refusal())
        f.tap_hit.assert_called_once()

    def test_options_default_off_and_ui_only_binds_preset_task(self):
        interface=json.loads((ROOT/'assets/interface.json').read_text(encoding='utf8'))
        preset=next(t for t in interface['task'] if t['entry']=='LivePresets')
        self.assertIn('预设Fever印章',preset['option'])
        self.assertEqual(interface['option']['预设Fever印章']['default_case'],'关')
        for entry, name, node, parser in [('ChartLive','谱面Fever印章','CL_fever',ChartOptions),
                                         ('AutoLive','演出Fever印章','LV_Fever',LiveOptions)]:
            self.assertEqual(parser.parse({}).fever,'off')
            self.assertNotIn(name,next(t for t in interface['task'] if t['entry']==entry)['option'])
            for case in interface['option']['预设Fever印章']['cases']:
                value=case['pipeline_override']['LP_fever']['attach']['value']
                self.assertEqual(parser.parse({'fever':value}).fever,value)
            for invalid in (True, 1, None, 'auto'):
                with self.assertRaises(ValueError): parser.parse({'fever':invalid})


if __name__=='__main__': unittest.main()
