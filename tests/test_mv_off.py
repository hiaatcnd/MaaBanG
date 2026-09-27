import sys
from pathlib import Path
from types import SimpleNamespace
import unittest
from unittest.mock import Mock
import numpy as np

sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'agent'))
from auto_live import LiveFlow
from costume_unlock import FlowError


class MvOffTests(unittest.TestCase):
    def flow(self, modes, cut_in=False):
        f=object.__new__(LiveFlow)
        f.image=np.zeros((720,1280,3),dtype=np.uint8)
        if cut_in:f.image[637:663,487:513]=[150,30,255]
        f.snap=Mock();f.reco=Mock(return_value=False)
        f.text=Mock(side_effect=modes)
        def tap(x,y):
            if x==500:f.image[637:663,487:513]=100
        f.tap=Mock(side_effect=tap)
        return f

    def test_cycles_3d_and_mv_to_off_then_disables_cut_in(self):
        f=self.flow(['3D演奏','MVON','OFF','OFF'],cut_in=True)
        f.disable_mv()
        self.assertEqual([c.args for c in f.tap.call_args_list],[(145,650),(145,650),(500,650)])

    def test_already_off_does_not_toggle_on(self):
        f=self.flow(['OFF','OFF'])
        f.disable_mv();f.tap.assert_not_called()

    def test_unknown_or_stuck_switch_blocks_start(self):
        for modes in ([''],['演奏']*4,['OFF','ON']):
            with self.subTest(modes=modes):
                with self.assertRaises(FlowError):self.flow(modes).disable_mv()

    def test_known_challenge_page_has_no_switch(self):
        f=self.flow([]);f.reco=Mock(return_value=True)
        f.disable_mv();f.tap.assert_not_called();f.text.assert_not_called()

    def test_team_confirmation_disables_cut_in_without_absent_mv_selector(self):
        f=self.flow([],cut_in=True)
        f.reco=Mock(side_effect=lambda node:node=='OL_FinalConfirm')
        f.disable_mv();f.text.assert_not_called()
        f.tap.assert_called_once_with(500,650)

    def test_no_mv_song_requires_blank_region_and_known_ready_page(self):
        f=self.flow([])
        f.image[620:680,110:320]=255
        f.reco=Mock(side_effect=lambda n:n=='LV_FreeReady')
        f.disable_mv();f.text.assert_not_called();f.tap.assert_not_called()
        f=self.flow([''])
        f.image[620:680,110:320]=255
        with self.assertRaises(FlowError):f.disable_mv()
