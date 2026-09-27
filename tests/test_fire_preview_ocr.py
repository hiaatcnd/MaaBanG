"""Real Maa OCR replay; no device connection or input is permitted."""
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import Mock

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT/'agent'))


class FirePreviewOCRTests(unittest.TestCase):
    def test_team_six_is_not_nine_and_production_confirmation_passes(self):
        if not (ROOT/'assets/resource/model/ocr/rec.onnx').is_file():
            self.skipTest('Local OCR models required')
        import numpy as np
        from PIL import Image
        from maa.controller import CustomController
        from maa.custom_action import CustomAction
        from maa.resource import Resource
        from maa.tasker import Tasker
        from online_live import OnlineLiveFlow
        from chart_policy import ChartOptions,ChartSelection
        frame=np.array(Image.open(ROOT/'tests/fixtures/fire_preview/team_6_to_3.png'))[:,:,::-1].copy()
        class Screens(CustomController):
            def connect(self):return True
            def request_uuid(self):return 'fire-preview-test'
            def screencap(self):return frame
        controller=Screens()
        self.assertTrue(controller.post_connection().wait().succeeded)
        resource=Resource()
        self.assertTrue(resource.post_bundle(ROOT/'assets/resource').wait().succeeded)
        tasker=Tasker();tasker.bind(resource,controller)
        case=self;observed={}
        with tempfile.TemporaryDirectory() as folder:
            class Check(CustomAction):
                def run(self,context,argv):
                    try:
                        f=OnlineLiveFlow(context,ChartOptions.parse({'mode':'team','fire':3}),folder)
                        f.image=frame
                        f.tap=Mock(side_effect=AssertionError('No game input during replay'))
                        f.pause=Mock()
                        case.assertEqual(f.fire_preview(),(6,3))
                        case.assertEqual(f.fire_balance(),6)
                        case.assertEqual(f.verify_chart_start(1,ChartSelection('646','expert'),3),6)
                        with case.assertRaisesRegex(RuntimeError,'火数预览不符'):
                            f.verify_chart_start(1,ChartSelection('646','expert'),2)
                        f.tap.assert_not_called()
                        return True
                    except Exception as error:
                        observed['error']=repr(error);return False
            resource.register_custom_action('FirePreviewCheck',Check())
            job=tasker.post_task('FirePreviewCheck',{'FirePreviewCheck':{'action':'Custom','custom_action':'FirePreviewCheck'}}).wait()
            self.assertTrue(job.succeeded,observed)


if __name__=='__main__':unittest.main()
