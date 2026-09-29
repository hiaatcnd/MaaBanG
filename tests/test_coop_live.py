"""Cooperative room regression checks against captured game screens."""
import tempfile
import unittest
import sys
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock, patch

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT/'agent'))
from chart_policy import ChartOptions, ChartSelection
from online_live import OnlineLiveFlow


class CoopTests(unittest.TestCase):
    def test_unknown_modal_never_clicks_background_result(self):
        with tempfile.TemporaryDirectory() as folder:
            flow=OnlineLiveFlow(SimpleNamespace(tasker=SimpleNamespace(controller=None,stopping=False)),
                                ChartOptions.parse({'mode':'coop'}),folder)
            flow.snap=Mock();flow.result_modals=Mock(return_value=False)
            flow.dismiss_talk=Mock(return_value=False);flow.reco=Mock(return_value=False)
            flow.pause=Mock();flow.hit_text=Mock();flow.tap_hit=Mock()
            flow.require_clear_notification_overlay=Mock(side_effect=RuntimeError('unknown modal'))
            with patch('online_live.dialog_box',return_value=[100,100,1000,500]),patch('online_live.time.monotonic',side_effect=[0,1,1,1,5,5]):
                with self.assertRaisesRegex(RuntimeError,'unknown modal'):
                    flow.settle_online({})
            flow.hit_text.assert_not_called()
            flow.tap_hit.assert_not_called()

    def test_group_result_advances_without_counting_song_yet(self):
        with tempfile.TemporaryDirectory() as folder:
            flow=OnlineLiveFlow(SimpleNamespace(tasker=SimpleNamespace(controller=None,stopping=False)),
                                ChartOptions.parse({'mode':'coop','fire':0}),folder)
            flow.snap=Mock();flow.result_modals=Mock(return_value=False)
            flow.dismiss_talk=Mock(return_value=False);flow.save_frame=Mock()
            flow.reco=Mock(side_effect=lambda node:node=='OL_CoopGroupResult')
            button=object()
            flow.hit_text=Mock(side_effect=lambda roi,pattern:button if '^下一步$' in pattern else None)
            flow.tap_hit=Mock(side_effect=RuntimeError('advanced group page'))
            with self.assertRaisesRegex(RuntimeError,'advanced group page'):
                flow.settle_online({})
            flow.tap_hit.assert_called_once_with(button)
            self.assertEqual(flow.report['completed_rounds'],0)

    def test_cost_requires_two_matching_previews_without_top_currency_bar(self):
        for second,accepted in [((18,18),True),((18,17),False)]:
            with self.subTest(second=second),tempfile.TemporaryDirectory() as folder:
                flow=OnlineLiveFlow(SimpleNamespace(tasker=SimpleNamespace(controller=None,stopping=False)),
                                    ChartOptions.parse({'mode':'coop','fire':0}),folder)
                flow.verify_final_selection=Mock();flow.snap=Mock();flow.pause=Mock()
                flow.reco=Mock(return_value=True)
                flow.fire_balance=Mock(side_effect=AssertionError('No top counter in coop'))
                flow.fire_preview=Mock(side_effect=[(18,18),second,(18,18),second])
                if accepted:
                    self.assertEqual(flow.verify_chart_start(1,ChartSelection('91','expert'),0),18)
                else:
                    with self.assertRaisesRegex(RuntimeError,'读数不稳定'):
                        flow.verify_chart_start(1,ChartSelection('91','expert'),0)

    def test_real_coop_pages_song_difficulty_and_zero_fire(self):
        if not (ROOT/'assets/resource/model/ocr/rec.onnx').is_file():
            self.skipTest('Local OCR models required')
        import numpy as np
        from PIL import Image
        from maa.controller import CustomController
        from maa.custom_action import CustomAction
        from maa.resource import Resource
        from maa.tasker import Tasker
        frames={name:np.asarray(Image.open(ROOT/f'tests/fixtures/coop/{name}.png').convert('RGB'))[:,:,::-1].copy()
                for name in ('ready','song','shuffle','group_result','achievement')}
        current=[frames['ready']]
        class Controller(CustomController):
            def connect(self): return True
            def request_uuid(self): return 'coop-fixture'
            def screencap(self): return current[0]
        resource=Resource();self.assertTrue(resource.post_bundle(ROOT/'assets/resource').wait().succeeded)
        controller=Controller();self.assertTrue(controller.post_connection().wait().succeeded)
        tasker=Tasker();tasker.bind(resource,controller)
        observed={};case=self
        with tempfile.TemporaryDirectory() as folder:
            class Check(CustomAction):
                def run(self,context,argv):
                    try:
                        flow=OnlineLiveFlow(context,ChartOptions.parse({'mode':'coop','fire':0}),folder)
                        for name,node in [('song','OL_CoopSongPage'),('shuffle','OL_RandomSong'),('group_result','OL_CoopGroupResult'),('ready','OL_FinalConfirm')]:
                            current[0]=frames[name];flow.snap()
                            case.assertTrue(flow.reco(node),(name,node))
                        case.assertEqual(flow.read_final_song()['id'],'91')
                        flow.verify_final_selection(ChartSelection('91','expert'))
                        case.assertEqual(flow.verify_chart_start(1,ChartSelection('91','expert'),0),18)
                        case.assertTrue(flow.hit_text([1010,580,225,105],'^准备完毕[！!]?$'))
                        current[0]=frames['song'];flow.snap()
                        case.assertTrue(flow.hit_text([685,605,200,80],'^不指定歌曲$'))
                        current[0]=frames['achievement'];flow.snap()
                        flow.tap_hit=Mock(side_effect=lambda hit:current.__setitem__(0,frames['ready']))
                        case.assertTrue(flow.dismiss_notifications())
                        flow.tap_hit.assert_called_once()
                        return True
                    except Exception as exc:
                        observed['error']=repr(exc);return False
            resource.register_custom_action('CoopCheck',Check())
            job=tasker.post_task('CoopCheck',{'CoopCheck':{'action':'Custom','custom_action':'CoopCheck'}}).wait()
            self.assertTrue(job.succeeded,observed)


if __name__=='__main__': unittest.main()
