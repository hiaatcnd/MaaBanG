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

    def test_real_3d_and_film_live_mv_labels_cycle_to_off(self):
        from PIL import Image
        from maa.controller import CustomController
        from maa.custom_action import CustomAction
        from maa.resource import Resource
        from maa.tasker import Tasker
        from live_policy import LiveOptions
        root=Path(__file__).resolve().parents[1]
        if not all((root/'assets/resource/model/ocr'/name).is_file() for name in ('det.onnx','rec.onnx','keys.txt')):
            self.skipTest('Local OCR models are required for the screenshot integration test')
        frames=[]
        for name in ('3d','film_mv','off'):
            frame=np.zeros((720,1280,3),dtype=np.uint8)
            frame[610:690,100:710]=np.asarray(Image.open(root/f'tests/fixtures/live_presets/{name}.png'))[:,:,::-1]
            frames.append(frame)
        resource=Resource()
        self.assertTrue(resource.post_bundle(root/'assets/resource').wait().succeeded)
        class Screens(CustomController):
            def connect(self):return True
            def request_uuid(self):return 'mv-preset-regression'
            def screencap(self):return frames[0]
        controller=Screens()
        self.assertTrue(controller.post_connection().wait().succeeded)
        tasker=Tasker();tasker.bind(resource,controller)
        case=self
        observed={}
        class Check(CustomAction):
            def run(self,context,argv):
                try:
                    flow=LiveFlow(context,LiveOptions())
                    index=[0]
                    flow.snap=lambda:setattr(flow,'image',frames[index[0]])
                    def tap(x,y):
                        case.assertEqual((x,y),(145,650))
                        index[0]+=1
                    flow.tap=Mock(side_effect=tap)
                    flow.disable_mv()
                    case.assertEqual(flow.tap.call_count,2)
                    observed['ok']=True
                    return True
                except Exception as exc:
                    observed['error']=repr(exc)
                    return False
        resource.register_custom_action('MvFixture',Check())
        job=tasker.post_task('MvFixture',{'MvFixture':{'action':'Custom','custom_action':'MvFixture'}}).wait()
        self.assertTrue(job.succeeded,observed)

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
