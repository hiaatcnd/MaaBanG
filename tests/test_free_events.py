import sys
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock

import numpy as np

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT/'agent'))
from daily_policy import verify_event_free_confirmation
from daily_tasks import DailyFlow
from costume_unlock import FlowError


FREE = '黄金周纪念1日1次免费10连招募 进行招募。确认吗？ ※不会消耗星石'


class EventFreeTests(unittest.TestCase):
    def test_inheritance_during_entry_rewind_and_pool_switch_preserves_scan(self):
        from unittest.mock import patch
        from test_notifications import inheritance_notice
        for when in ('entry','rewind','switch'):
            with self.subTest(when=when):
                f=self.event_flow(['paid','free','ticket'])
                frame,hits=inheritance_notice()
                state={'pending':when=='entry','shown':False}
                refresh=f.wait.side_effect
                def snap(*args):
                    refresh()
                    if state['pending']: f.image=frame.copy()
                def show():
                    if not state['shown']: state['pending']=True
                def acknowledge(button):
                    if state['pending']:
                        self.assertEqual(button.text,'确定')
                        state.update(pending=False,shown=True)
                tap=f.tap.side_effect
                def change_pool(x,y):
                    self.assertFalse(state['pending'],'Must close the notice before background clicks')
                    tap(x,y)
                    if when=='switch' and (x,y)==(130,318): show()
                def rewind(*args):
                    self.assertFalse(state['pending'],'Must close the notice before scrolling')
                    if when=='rewind': show()
                f.wait.side_effect=snap; f.snap=Mock(side_effect=snap)
                f.tap.side_effect=change_pool; f.swipe.side_effect=rewind
                f.ocr=Mock(return_value=hits); f.tap_hit.side_effect=acknowledge
                with patch('daily_tasks.time.sleep'):
                    f.recruit_events()
                self.assertTrue(state['shown'])
                self.assertEqual(f.report['recruit_tabs_checked'],3)
                self.assertEqual(f.report['draws'],1)
                self.assertEqual(f.report['event_draws'][0]['pool'],1)
                self.assertEqual([c.args for c in f.tap.call_args_list].count((770,476)),1)

    def test_unrecognized_recruit_overlay_blocks_scrolling_and_drawing(self):
        from test_notifications import inheritance_notice
        f=self.event_flow(['free'])
        frame,hits=inheritance_notice()
        f.wait.side_effect=lambda *_:setattr(f,'image',frame.copy())
        f.ocr=Mock(return_value=[h for h in hits if h.text!='继承结果如下。'])
        with self.assertRaisesRegex(FlowError,'未识别的弹窗'):
            f.recruit_events()
        f.swipe.assert_not_called()
        f.tap.assert_not_called()
        f.tap_hit.assert_not_called()
        f.event_free_button.assert_not_called()

    def test_confirmation_requires_free_offer_and_explicit_zero_cost(self):
        verify_event_free_confirmation(FREE)
        verify_event_free_confirmation(FREE.replace('10', '１０'))
        for text in (FREE.replace('不会消耗星石', ''),
                     FREE.replace('免费10连招募', '10连招募'),
                     FREE+'消耗2500星石', FREE+'使用1张招募券',
                     FREE+'付费限定', FREE.replace('不会消耗', '消耗'),
                     '每天免费10连', '不会消耗星石'):
            with self.subTest(text=text), self.assertRaises(ValueError):
                verify_event_free_confirmation(text)

    def event_flow(self, pools, *, stuck=False, confirmation=FREE):
        f=DailyFlow(SimpleNamespace(tasker=SimpleNamespace(controller=None,stopping=False)))
        state={'index':0, 'drawn':False}
        def snap(*args):
            f.image=np.zeros((720,1280,3),dtype=np.uint8)
            f.image[180:255,45:215]=20+state['index']*20
            if state['index'] < len(pools)-1:
                f.image[285:345,42:49]=255
        def tap(x,y):
            if (x,y)==(130,318):
                state['index']+=1
                state['drawn']=False
            elif (x,y)==(770,476):
                state['drawn']=True
            snap()
        snap()
        f.home=Mock(); f.click=Mock(); f.swipe=Mock(); f.wait=Mock(side_effect=snap)
        f.tap=Mock(side_effect=tap); f.tap_hit=Mock()
        f.reco=Mock(side_effect=lambda node:pools[state['index']]=='daily')
        f.text=Mock(return_value=confirmation)
        f.event_free_button=Mock(side_effect=lambda:
            pools[state['index']]=='free' and (not state['drawn'] or stuck))
        f.finish_draw=Mock(side_effect=snap)
        return f

    def test_traverses_paid_and_ticket_pools_and_collects_free_offer(self):
        f=self.event_flow(['paid','free','ticket','daily'])
        from unittest.mock import patch
        with patch('daily_tasks.time.sleep'):
            f.recruit_events()
        self.assertEqual(f.report['recruit_tabs_checked'],4)
        self.assertEqual(f.report['draws'],1)
        self.assertEqual(f.report['event_draws'][0]['status'],'success_confirmed')
        self.assertFalse(f.report['draw_in_flight'])
        self.assertEqual([c.args for c in f.tap.call_args_list].count((770,476)),1)
        f.finish_draw.assert_called_once_with('DY_RecruitDetails')

    def test_same_free_confirmation_cannot_be_submitted_twice(self):
        f=self.event_flow(['free'],stuck=True)
        from unittest.mock import patch
        with patch('daily_tasks.time.sleep'):
            f.recruit_events()
        self.assertEqual([c.args for c in f.tap.call_args_list].count((770,476)),1)
        self.assertEqual(f.report['draws'],1)
        self.assertTrue(f.report['warnings'])
        self.assertIn((510,476),[c.args for c in f.tap.call_args_list])

    def test_identical_confirmation_in_different_pools_does_not_block_free_draws(self):
        f=self.event_flow(['free','free'])
        from unittest.mock import patch
        with patch('daily_tasks.time.sleep'):
            f.recruit_events()
        self.assertEqual(f.report['draws'],2)
        self.assertEqual([row['pool'] for row in f.report['event_draws']],[0,1])

    def test_paid_confirmation_and_uncertain_result_never_retry(self):
        from unittest.mock import patch
        f=self.event_flow(['free'],confirmation=FREE+'消耗2500星石')
        with patch('daily_tasks.time.sleep'), self.assertRaises(ValueError):
            f.recruit_events()
        f.tap.assert_not_called()
        f=self.event_flow(['free'])
        f.finish_draw.side_effect=FlowError('结果不明')
        with patch('daily_tasks.time.sleep'), self.assertRaises(FlowError):
            f.recruit_events()
        self.assertEqual([c.args for c in f.tap.call_args_list].count((770,476)),1)
        self.assertTrue(f.report['draw_in_flight'])
        self.assertEqual(f.report['draws'],0)

    def test_real_free_button_paid_button_and_confirmation_ocr(self):
        if not (ROOT/'assets/resource/model/ocr/rec.onnx').is_file():
            self.skipTest('Local OCR models required')
        from PIL import Image
        from maa.controller import CustomController
        from maa.custom_action import CustomAction
        from maa.resource import Resource
        from maa.tasker import Tasker
        def frame(name,box):
            out=np.zeros((720,1280,3),dtype=np.uint8)
            x,y,w,h=box
            out[y:y+h,x:x+w]=np.asarray(Image.open(
                ROOT/f'tests/fixtures/daily/{name}.png').convert('RGB'))[:,:,::-1]
            return out
        free=frame('event_free_button',(880,612,380,88))
        paid=frame('paid_recruit_button',(880,612,380,88))
        exhausted=frame('exhausted_free_button',(880,612,380,88))
        confirm=frame('event_free_confirm',(330,175,620,370))
        class Controller(CustomController):
            def connect(self): return True
            def request_uuid(self): return 'event-free-fixtures'
            def screencap(self): return free
        resource=Resource(); self.assertTrue(resource.post_bundle(ROOT/'assets/resource').wait().succeeded)
        controller=Controller(); self.assertTrue(controller.post_connection().wait().succeeded)
        tasker=Tasker(); tasker.bind(resource,controller)
        case=self; errors=[]
        class Check(CustomAction):
            def run(self,ctx,argv):
                try:
                    f=DailyFlow(ctx)
                    f.image=free; case.assertIsNotNone(f.event_free_button())
                    f.image=paid; case.assertIsNone(f.event_free_button())
                    f.image=exhausted; case.assertIsNone(f.event_free_button())
                    # Free advertising outside the button must not trigger a draw.
                    f.image=np.zeros_like(free)
                    f.image[300:388,300:680]=free[612:700,880:1260]
                    case.assertIsNone(f.event_free_button())
                    f.image=confirm; case.assertTrue(f.reco('DY_EventFreeConfirm'))
                    verify_event_free_confirmation(f.text([370,260,540,160]))
                    with case.assertRaises(FlowError): f.event_free_button()
                    return True
                except Exception as exc:
                    errors.append(repr(exc)); return False
        resource.register_custom_action('EventCheck',Check())
        self.assertTrue(tasker.post_task('EventCheck',{'EventCheck':{
            'action':'Custom','custom_action':'EventCheck'}}).wait().succeeded,errors)


if __name__=='__main__':
    unittest.main()
