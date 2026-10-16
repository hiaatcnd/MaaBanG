"""Every playback entry point shares the same observed-control policy.

Control state and real screenshot OCR replays live in test_live_effects.py.
"""
import sys
from pathlib import Path
import unittest

sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'agent'))
from auto_live import LiveFlow
from chart_live import ChartLiveFlow
from cp_live import CPLiveFlow
from direct_chart_live import DirectChartLiveFlow
from live_effects import LiveEffectsMixin
from live_presets import LivePresetFlow
from mining_live import MiningLiveFlow, ChallengeMiningFlow
from online_live import OnlineLiveFlow


class MvOffTests(unittest.TestCase):
    def test_all_playback_and_preset_flows_use_the_same_effects_policy(self):
        for flow_type in (LiveFlow,ChartLiveFlow,OnlineLiveFlow,CPLiveFlow,
                          DirectChartLiveFlow,MiningLiveFlow,ChallengeMiningFlow,
                          LivePresetFlow):
            with self.subTest(flow=flow_type.__name__):
                self.assertIs(flow_type.disable_mv,LiveEffectsMixin.disable_mv)


if __name__=='__main__':unittest.main()
