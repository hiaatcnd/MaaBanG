import sys
import unittest
from pathlib import Path
from types import SimpleNamespace
import numpy as np

sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'agent'))
from costume_unlock import CostumeFlow
from costume_policy import ROSTER


class BandNavigationTests(unittest.TestCase):
    def make_flow(self, visible, target, selected=False, reveal_after=1, stationary=False):
        flow=CostumeFlow(SimpleNamespace(tasker=SimpleNamespace(controller=None,stopping=False)),1)
        state={'visible':visible,'selected':selected,'page':0}
        actions=[]
        def snap():
            flow.image=np.full((720,1280,3),state['page']*10,dtype=np.uint8)
        def reco(node, **kwargs):
            if node=='CU_SelectedBandFrame': return state['selected']
            if node.startswith('CU_Band_'):
                band=node.removeprefix('CU_Band_')
                if 'roi' in kwargs and not state['selected']: return None
                if band in state['visible']: return SimpleNamespace(box=[48,193,186,58])
            return None
        def swipe(x,y1,y2):
            actions.append(('swipe',y1,y2))
            if len([a for a in actions if a[0]=='swipe'])>=reveal_after:
                state['visible']=[target]; state['page']+=1
            elif not stationary: state['page']+=1
        def tap(*_):
            actions.append(('tap',));state['selected']=True
        flow.snap=snap
        flow.wait=lambda *_: snap()
        flow.reco=reco
        flow.swipe=swipe
        flow.tap=tap
        return flow,actions

    def test_visible_target_stops_without_swiping(self):
        flow,actions=self.make_flow(['roselia'],'roselia')
        flow.select_band('roselia')
        self.assertEqual(actions,[('tap',)])

    def test_selected_target_needs_no_input(self):
        flow,actions=self.make_flow(['roselia'],'roselia',selected=True)
        flow.select_band('roselia')
        self.assertEqual(actions,[])

    def test_target_above_scrolls_up_once_then_stops(self):
        flow,actions=self.make_flow(['raise_a_suilen','mygo'],'poppin_party')
        flow.select_band('poppin_party')
        self.assertEqual(actions,[('swipe',350,570),('tap',)])

    def test_stationary_boundary_reverses_without_twelve_retries(self):
        flow,actions=self.make_flow([],'poppin_party',reveal_after=2,stationary=True)
        flow.select_band('poppin_party')
        self.assertEqual(actions,[('swipe',570,350),('swipe',350,570),('tap',)])


if __name__=='__main__': unittest.main()
