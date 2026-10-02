import json
from pathlib import Path
import sys
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import Mock

import numpy as np

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT/'agent'))
sys.path.insert(0,str(ROOT/'tools'))
from auto_live import LiveFlow
from chart_live import ChartLiveFlow
from chart_policy import ChartOptions
from live_policy import LiveOptions
from live_presets import LivePresetFlow
from costume_unlock import FlowError
from update_live_presets_interface import update, REMOVED


class PresetTests(unittest.TestCase):
    def flow(self):
        flow = LiveFlow(SimpleNamespace(tasker=SimpleNamespace(controller=None,stopping=False)),LiveOptions())
        for name in ('wait','tap','snap','require_clear_notification_overlay'):
            setattr(flow,name,Mock())
        flow.reco=Mock(return_value=True)
        flow.image=np.full((720,1280,3),255,dtype=np.uint8)
        return flow

    def test_saved_fire_read_never_touches_radio_buttons(self):
        for amount in range(4):
            flow=self.flow()
            y=133+77*amount
            flow.image[y-9:y+10,993:1012]=[100,80,255]
            self.assertEqual(flow.menu_fire(),amount)
            self.assertEqual([c.args for c in flow.tap.call_args_list],[(1119,145),(640,641)])

    def test_ambiguous_or_full_consumption_stops_without_start(self):
        for selected in ([],[0,1],[4]):
            flow=self.flow()
            for amount in selected:
                y=(133,210,287,364,518)[amount]
                flow.image[y-9:y+10,993:1012]=[100,80,255]
            with self.assertRaises(FlowError):flow.menu_fire()
            flow.tap.assert_called_once_with(1119,145)

    def test_actual_game_fire_overrides_old_task_configuration(self):
        flow=self.flow()
        flow.menu_fire=Mock(return_value=2)
        flow.inherit_menu_fire()
        flow.inherit_menu_fire()
        flow.menu_fire.assert_called_once()
        self.assertEqual(flow.options.fire,2)
        self.assertEqual(flow.options.shortage,'stop')
        with tempfile.TemporaryDirectory() as folder:
            flow=ChartLiveFlow(SimpleNamespace(tasker=SimpleNamespace(controller=None,stopping=False)),
                               ChartOptions.parse({'fire':3}),folder)
            flow.menu_fire=Mock(return_value=0)
            flow.inherit_menu_fire()
            self.assertEqual(flow.settings.fire,0)

    def test_standalone_preset_saves_and_reopens_fire_before_fever(self):
        with tempfile.TemporaryDirectory() as folder:
            flow=LivePresetFlow(SimpleNamespace(tasker=SimpleNamespace(controller=None,stopping=False)),folder)
            calls=Mock()
            for name in ('navigate_menu','menu_fire','configure_fever','configure_stage','home'):
                setattr(flow,name,getattr(calls,name))
            flow.menu_fire.return_value=0
            flow.apply(stage=False,fire=0,fever='off')
            self.assertEqual([c.args for c in flow.menu_fire.call_args_list],[(0,),()])
            flow.configure_stage.assert_not_called()
            self.assertLess(str(calls.mock_calls).index('menu_fire()'),str(calls.mock_calls).index('configure_fever'))
            self.assertEqual(flow.report['status'],'finished')

    def test_failed_save_stops_before_fever_and_home(self):
        with tempfile.TemporaryDirectory() as folder:
            flow=LivePresetFlow(SimpleNamespace(tasker=SimpleNamespace(controller=None,stopping=False)),folder)
            flow.navigate_menu=Mock(); flow.menu_fire=Mock(side_effect=[0,3])
            flow.configure_fever=Mock();flow.home=Mock()
            with self.assertRaises(FlowError):flow.apply(stage=False,fire=0)
            flow.configure_fever.assert_not_called();flow.home.assert_not_called()

    def test_single_ui_owner_and_idempotent_generation(self):
        interface=json.loads((ROOT/'assets/interface.json').read_text(encoding='utf8'))
        updated=update(json.loads(json.dumps(interface)))
        self.assertEqual(interface,updated)
        self.assertEqual(interface['task'][0]['entry'],'LivePresets')
        self.assertFalse(REMOVED.intersection(interface['option']))
        for task in interface['task']:
            if task['entry']!='LivePresets':
                self.assertFalse(set(task.get('option',[])).intersection({'预设火数','预设Fever印章','预设演出设定'}))

if __name__=='__main__':unittest.main()
