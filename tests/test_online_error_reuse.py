"""Exercise production error classification/cache with a counted framework boundary."""
import copy
import json
from pathlib import Path
import re
import sys
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import Mock, patch

import numpy as np

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT/'agent'))
from chart_policy import ChartOptions
from costume_unlock import FlowError
from online_live import OnlineFatal, OnlineLiveFlow


class OnlineErrorReuseTests(unittest.TestCase):
    def make_flow(self):
        folder=tempfile.TemporaryDirectory()
        self.addCleanup(folder.cleanup)
        pipeline=json.loads((ROOT/'assets/resource/pipeline/online_live.json').read_text(encoding='utf-8'))
        self.nodes={name:{'enabled':True,'inverse':False,'recognition':{'type':'OCR',
                    'param':{'index':0,**{k:v for k,v in pipeline[name].items() if k!='recognition'}}}}
                    for name in ('OL_Fatal','OL_Disconnected')}
        self.texts=[]
        self.page=None
        job=SimpleNamespace(wait=Mock(return_value=SimpleNamespace(succeeded=True)))
        controller=SimpleNamespace(cached_image=np.zeros((720,1280,3),dtype=np.uint8),
            post_screencap=Mock(return_value=job),post_click=Mock(return_value=job))

        def recognize(name,image,override=None):
            if name in self.nodes:
                params={**self.nodes[name]['recognition']['param'],**(override or {}).get(name,{})}
                patterns=params['expected']
                hits=[SimpleNamespace(text=text,score=.99,box=[400,300,150,30])
                      for text in self.texts if any(re.search(p,text) for p in patterns)]
            else:
                hits=[SimpleNamespace(text=name)] if name==self.page else []
            return SimpleNamespace(hit=bool(hits),filtered_results=hits,
                                   best_result=hits[0] if hits else None)

        context=SimpleNamespace(tasker=SimpleNamespace(controller=controller,stopping=False),
            get_node_data=Mock(side_effect=lambda name:copy.deepcopy(self.nodes.get(name))),
            run_recognition=Mock(side_effect=recognize))
        flow=OnlineLiveFlow(context,ChartOptions.parse({'mode':'coop','fire':0}),folder.name)
        flow.foreground_package=Mock(return_value='com.bilibili.star.bili')
        return flow

    def test_snap_and_disconnect_consumers_share_one_framework_recognition(self):
        for texts in ([],['连接失败']):
            with self.subTest(texts=texts):
                flow=self.make_flow()
                self.texts=texts
                flow.snap()  # Outside an active room: only fatal errors stop capture.
                self.assertIsNone(flow.room_error(fatal_only=True))
                self.assertEqual(flow.room_disconnected(),bool(texts))
                self.assertEqual(flow.room_error() is not None,bool(texts))
                flow.ctx.run_recognition.assert_called_once()
                self.assertEqual(flow.ctx.run_recognition.call_args.args[0],'OL_Fatal')
                patterns=flow.ctx.run_recognition.call_args.args[2]['OL_Fatal']['expected']
                self.assertIn('正在维护',patterns)
                self.assertIn('连接失败',patterns)
                flow.controller.post_click.assert_not_called()

    def test_new_capture_rechecks_even_if_controller_reuses_same_buffer(self):
        flow=self.make_flow()
        flow.snap()
        frame=flow.image
        self.assertFalse(flow.room_disconnected())
        self.texts=['连接失败']
        frame[200,400]=255
        flow.snap()
        self.assertIs(flow.image,frame)
        self.assertTrue(flow.room_disconnected())
        self.assertEqual(flow.ctx.run_recognition.call_count,2)

    def test_configuration_changes_recheck_same_frame_and_preserve_fatal_priority(self):
        flow=self.make_flow()
        self.texts=['新的房间错误']
        flow.snap()
        self.assertFalse(flow.room_disconnected())
        self.nodes['OL_Disconnected']['recognition']['param']['expected']=['新的房间错误']
        self.assertTrue(flow.room_disconnected())
        self.assertEqual(flow.ctx.run_recognition.call_count,2)
        self.nodes['OL_Fatal']['recognition']['param']['expected']=['新的房间错误']
        with self.assertRaisesRegex(OnlineFatal,'新的房间错误'):
            flow.room_disconnected()
        self.assertEqual(flow.ctx.run_recognition.call_count,3)

    def test_incompatible_thresholds_keep_separate_checks_but_reuse_each_same_frame(self):
        flow=self.make_flow()
        self.nodes['OL_Disconnected']['recognition']['param']['threshold']=.95
        self.texts=['连接失败']
        flow.snap()
        flow.ctx.run_recognition.assert_called_once()
        self.assertEqual(flow.ctx.run_recognition.call_args.args[0],'OL_Fatal')
        self.assertTrue(flow.room_disconnected())
        self.assertEqual([call.args[0] for call in flow.ctx.run_recognition.call_args_list],
                         ['OL_Fatal','OL_Disconnected'])
        self.assertTrue(all(call.args[2] is None for call in flow.ctx.run_recognition.call_args_list))

    def test_custom_recognition_keeps_dynamic_fallback_without_caching_its_miss(self):
        flow=self.make_flow()
        self.nodes['OL_Disconnected']['recognition']['type']='Custom'
        flow.snap()
        self.assertEqual([call.args[0] for call in flow.ctx.run_recognition.call_args_list],['OL_Fatal'])
        self.assertFalse(flow.room_disconnected())
        self.texts=['连接失败']
        self.assertTrue(flow.room_disconnected())
        self.assertEqual([call.args[0] for call in flow.ctx.run_recognition.call_args_list],
                         ['OL_Fatal','OL_Disconnected','OL_Disconnected'])

    def test_incompatible_custom_fatal_check_does_not_run_disconnect_during_snap(self):
        flow=self.make_flow()
        self.nodes['OL_Fatal']['recognition']['type']='Custom'
        flow.snap()
        self.assertEqual([call.args[0] for call in flow.ctx.run_recognition.call_args_list],['OL_Fatal'])
        self.texts=['正在维护','连接失败']
        with self.assertRaisesRegex(OnlineFatal,'正在维护'):
            flow.room_disconnected()
        self.assertEqual([call.args[0] for call in flow.ctx.run_recognition.call_args_list],
                         ['OL_Fatal','OL_Fatal'])

    def test_inactive_snap_never_evaluates_disconnected_with_incompatible_parameters(self):
        changes=[('param','roi',[0,0,100,100]),('param','expected',[]),
                 ('param','index',1),('node','enabled',False),('node','inverse',True),
                 ('recognition','type','TemplateMatch')]
        for scope,key,value in changes:
            with self.subTest(scope=scope,key=key):
                flow=self.make_flow()
                node=self.nodes['OL_Disconnected']
                target=(node['recognition']['param'] if scope=='param' else
                        node['recognition'] if scope=='recognition' else node)
                target[key]=value
                flow.snap()
                flow.ctx.run_recognition.assert_called_once()
                self.assertEqual(flow.ctx.run_recognition.call_args.args[0],'OL_Fatal')
                self.assertIsNone(flow.ctx.run_recognition.call_args.args[2])

    def test_input_invalidates_and_cached_read_still_honors_stop(self):
        flow=self.make_flow()
        flow.snap()
        with patch('costume_unlock.time.sleep'):
            flow.tap(640,526)
        self.texts=['连接失败']
        self.assertTrue(flow.room_disconnected())
        self.assertEqual(flow.ctx.run_recognition.call_count,2)
        flow.ctx.tasker.stopping=True
        with self.assertRaisesRegex(FlowError,'停止'):
            flow.room_disconnected()
        self.assertEqual(flow.ctx.run_recognition.call_count,2)

    def test_counted_settlement_reuses_capture_error_without_recounting(self):
        flow=self.make_flow()
        self.texts=['连接失败']
        flow.report['completed_rounds']=1
        flow.recover_room=Mock()
        row={'counted':True}
        flow.settle_online(row)
        flow.recover_room.assert_called_once_with()
        flow.controller.post_screencap.assert_called_once()
        flow.ctx.run_recognition.assert_called_once()
        self.assertEqual(flow.report['completed_rounds'],1)

    def test_counted_clean_settlement_reads_error_once_before_returning_home(self):
        flow=self.make_flow()
        flow.report['completed_rounds']=1
        flow.result_modals=Mock(return_value=False)
        flow.dismiss_talk=Mock(return_value=False)
        flow.hit_text=Mock(return_value=None)
        self.page=flow.home_node
        flow.settle_online({'counted':True})
        errors=[call for call in flow.ctx.run_recognition.call_args_list
                if call.args[0] in self.nodes]
        self.assertEqual(len(errors),1)
        self.assertEqual(flow.report['completed_rounds'],1)

    def test_fatal_precedes_disconnect_in_settlement_and_recovery_without_input(self):
        for operation in ('settle_online','recover_room'):
            with self.subTest(operation=operation):
                flow=self.make_flow()
                self.texts=['连接失败','正在维护']
                flow.tap_hit=Mock()
                flow.back=Mock()
                args=({'counted':True},) if operation=='settle_online' else ()
                with self.assertRaisesRegex(OnlineFatal,'正在维护'):
                    getattr(flow,operation)(*args)
                flow.ctx.run_recognition.assert_called_once()
                flow.controller.post_click.assert_not_called()
                flow.tap_hit.assert_not_called()
                flow.back.assert_not_called()


class OnlineErrorReuseOCRTests(unittest.TestCase):
    def test_recorded_disconnect_uses_one_native_ocr_for_snapshot_and_consumers(self):
        if not (ROOT/'assets/resource/model/ocr/rec.onnx').is_file():
            self.skipTest('Local OCR models required')
        from PIL import Image
        from maa.controller import CustomController
        from maa.custom_action import CustomAction
        from maa.resource import Resource
        from maa.tasker import Tasker
        frame=np.asarray(Image.open(ROOT/'tests/fixtures/online_recovery/connection_failed.png')
                         .convert('RGB'))[:,:,::-1].copy()

        class Controller(CustomController):
            def connect(self):return True
            def request_uuid(self):return 'same-frame-room-error-replay'
            def screencap(self):return frame
            def click(self,x,y):raise AssertionError('No game input during replay')

        resource=Resource()
        self.assertTrue(resource.use_cpu())
        self.assertTrue(resource.post_bundle(ROOT/'assets/resource').wait().succeeded)
        controller=Controller()
        self.assertTrue(controller.post_connection().wait().succeeded)
        tasker=Tasker()
        self.assertTrue(tasker.bind(resource,controller))
        case=self
        observed={}
        with tempfile.TemporaryDirectory() as folder:
            class Check(CustomAction):
                def run(self,context,argv):
                    try:
                        boundary=Mock(wraps=context.run_recognition)
                        proxy=SimpleNamespace(tasker=context.tasker,get_node_data=context.get_node_data,
                                              run_recognition=boundary)
                        flow=OnlineLiveFlow(proxy,ChartOptions.parse({'mode':'coop','fire':0}),folder)
                        flow.snap()
                        case.assertTrue(flow.room_disconnected())
                        case.assertEqual(flow.room_error()[0],'OL_Disconnected')
                        boundary.assert_called_once()
                        case.assertIn('连接失败',flow.room_error()[1])
                        flow.snap()
                        case.assertTrue(flow.room_disconnected())
                        case.assertEqual(boundary.call_count,2)
                        return True
                    except Exception as error:
                        observed['error']=repr(error)
                        return False

            resource.register_custom_action('SameFrameRoomErrorCheck',Check())
            job=tasker.post_task('SameFrameRoomErrorCheck',{'SameFrameRoomErrorCheck':{
                'action':'Custom','custom_action':'SameFrameRoomErrorCheck'}}).wait()
            self.assertTrue(job.succeeded,observed)


if __name__=='__main__':unittest.main()
