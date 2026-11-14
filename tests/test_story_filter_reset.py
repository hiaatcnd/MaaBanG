"""Global filter reset must demonstrate a change, including character coverage."""
import sys
import unittest
from contextlib import ExitStack
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock, patch

import numpy as np
from PIL import Image

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT/'agent'))
from costume_unlock import FlowError
from mining_policy import MiningOptions
from mining_stories import StoryMiningFlow, member_filter_box


class StoryFilterResetTests(unittest.TestCase):
    box=[129,34,1022,653]

    @classmethod
    def setUpClass(cls):
        with Image.open(ROOT/'tests/fixtures/skill_practice/filter.png') as image:
            cls.frame=np.array(image.convert('RGB'))[:,:,::-1].copy()

    def flow(self):
        flow=StoryMiningFlow(SimpleNamespace(tasker=SimpleNamespace(
            controller=None,stopping=False)),MiningOptions.parse({}),Path('unused'))
        state=dict(time=0.,attrs=[True]*4,characters=False,skills=False,
                   expanded=True,page='filter',shift=0,clicks=[],pending=[],
                   effects={},attempts={},on_frame=None,on_click=None,role_visible=True)

        def paint(image,x,y,value):
            image[y-6:y+7,x-6:x+7]=(110,50,255) if value is True else (
                (180,180,180) if value is False else (255,255,255))

        def snap():
            flow.check_stop()
            for due,action in list(state['pending']):
                if state['time']>=due:
                    action()
                    state['pending'].remove((due,action))
            if state['on_frame']:
                state['on_frame']()
            flow.check_stop()
            flow.image=self.frame.copy()
            for x,value in zip((184,383,582,781),state['attrs']):
                paint(flow.image,x,240+state['shift'],value)
            paint(flow.image,969,497,state['characters'])
            flow.image[433:448,1090:1093]=(110,50,255) if not state['expanded'] else 255
            return flow.image

        def hit_text(roi,pattern):
            if pattern=='^全选[/／]取消全选$':
                return SimpleNamespace(box=[950,60,175,40])
            if pattern=='^种类$|^属性$':
                return SimpleNamespace(box=[164,171+state['shift'],88,30])
            if pattern=='^选择全部$' and state['expanded'] and state['role_visible']:
                return SimpleNamespace(box=[994,484,84,26])
            if pattern=='^角色筛选$':
                return SimpleNamespace(box=[164,425,132,30])
            return None

        def effect(kind):
            if kind=='marker':
                state['attrs'][0]=not state['attrs'][0]
            elif kind=='global':
                # Model the game's documented toggle: any unchecked checkbox
                # means select all; only all checked means clear all.
                value=not(all(state['attrs']) and state['characters'] and state['skills'])
                state.update(attrs=[value]*4,characters=value,skills=value)
            else:
                state['expanded']=not state['expanded']

        def tap(x,y):
            flow.check_stop()
            self.assertEqual(state['page'],'filter')
            if x==184:
                self.assertEqual(y,240+state['shift'])
                kind='marker'
            elif x==1037:
                self.assertIs(state['attrs'][0],False,
                              'A global retry must never clear a completed selection')
                kind='global'
            else:
                self.assertEqual((x,y),(1091,440))
                self.assertFalse(state['expanded'])
                kind='expand'
            state['clicks'].append((state['time'],kind,(x,y)))
            state['attempts'][kind]=state['attempts'].get(kind,0)+1
            behavior=state['effects'].get(kind,lambda attempt:0)(state['attempts'][kind])
            if behavior is not None:
                if behavior:
                    state['pending'].append((state['time']+behavior,lambda:effect(kind)))
                else:
                    effect(kind)
            if state['on_click']:
                state['on_click'](kind)

        flow.snap=Mock(side_effect=snap)
        flow.reco=Mock(side_effect=lambda node:state['page']=='filter')
        flow.hit_text=Mock(side_effect=hit_text)
        flow.tap=Mock(side_effect=tap)
        flow.swipe=Mock()
        snap()
        return flow,state

    def clock(self,state):
        stack=ExitStack()
        stack.enter_context(patch('mining_stories.member_filter_box',return_value=self.box))
        stack.enter_context(patch('mining_stories.time.monotonic',side_effect=lambda:state['time']))
        stack.enter_context(patch('mining_stories.time.sleep',side_effect=lambda seconds:
                                  state.update(time=state['time']+seconds)))
        stack.enter_context(patch('mining_stories.log'))
        return stack

    def reset(self,flow,state):
        with self.clock(state):
            flow.reset_all_member_filters(self.box)

    def test_preselected_attributes_cannot_hide_a_missed_global_click(self):
        flow,state=self.flow()
        state['effects']['global']=lambda attempt:None if attempt==1 else 0
        self.reset(flow,state)
        self.assertEqual(state['attempts'],{'marker':1,'global':2})
        self.assertTrue(state['characters'])
        self.assertTrue(state['skills'])
        self.assertTrue(all(state['attrs']))
        flow.swipe.assert_not_called()

    def test_already_unchecked_attribute_does_not_need_marker_click(self):
        flow,state=self.flow()
        state['attrs'][0]=False
        self.reset(flow,state)
        self.assertEqual(state['attempts'],{'global':1})

    def test_no_effect_marker_click_is_retried_before_global_selection(self):
        flow,state=self.flow()
        state['effects']['marker']=lambda attempt:None if attempt==1 else 0
        self.reset(flow,state)
        self.assertEqual([kind for _,kind,_ in state['clicks']],['marker','marker','global'])

    def test_delayed_marker_and_global_changes_are_never_toggled_back(self):
        for kind in ('marker','global'):
            with self.subTest(kind=kind):
                flow,state=self.flow()
                state['effects'][kind]=lambda attempt:2.6
                self.reset(flow,state)
                self.assertEqual(state['attempts'],{'marker':1,'global':1})
                self.assertTrue(state['characters'])

    def test_marker_relocation_is_read_again_before_retry(self):
        flow,state=self.flow()
        state['effects']['marker']=lambda attempt:None if attempt==1 else 0
        state['on_click']=lambda kind:state.update(shift=2) if kind=='marker' else None
        self.reset(flow,state)
        self.assertEqual(state['clicks'][0][2],(184,240))
        self.assertEqual(state['clicks'][1][2],(184,242))

    def test_unknown_marker_is_not_assumed_unchecked(self):
        flow,state=self.flow()
        state['attrs'][0]=None
        with self.assertRaisesRegex(FlowError,'重置超时'):
            self.reset(flow,state)
        self.assertEqual(state['clicks'],[])

    def test_retry_exhaustion_cannot_report_reset_success(self):
        for kind in ('marker','global'):
            with self.subTest(kind=kind):
                flow,state=self.flow()
                state['effects'][kind]=lambda attempt:None
                with self.assertRaisesRegex(FlowError,'补点上限'):
                    self.reset(flow,state)
                self.assertEqual(state['attempts'][kind],3)
                self.assertFalse(state['characters'])

    def test_changed_page_stops_before_reusing_global_coordinates(self):
        flow,state=self.flow()
        state['on_click']=lambda kind:state.update(page='unknown')
        with self.assertRaisesRegex(FlowError,'弹窗已变化'):
            self.reset(flow,state)
        self.assertEqual(len(state['clicks']),1)

    def test_stop_request_interrupts_each_click_phase(self):
        for kind in ('marker','global','expand'):
            with self.subTest(kind=kind):
                flow,state=self.flow()
                state['expanded']=kind!='expand'
                state['on_click']=lambda clicked:setattr(flow.ctx.tasker,'stopping',True) if clicked==kind else None
                with self.assertRaisesRegex(FlowError,'用户停止'):
                    self.reset(flow,state)
                self.assertEqual(state['clicks'][-1][1],kind)

    def test_character_aggregate_is_checked_after_global_acknowledgement(self):
        flow,state=self.flow()
        state['on_click']=lambda kind:state.update(characters=False) if kind=='global' else None
        with self.assertRaisesRegex(FlowError,'角色筛选仍未全部选中'):
            self.reset(flow,state)
        self.assertEqual(state['attempts']['global'],1)

    def test_collapsed_character_section_recovers_missed_expand_click(self):
        flow,state=self.flow()
        state['expanded']=False
        state['effects']['expand']=lambda attempt:None if attempt==1 else 0
        self.reset(flow,state)
        self.assertEqual(state['attempts']['expand'],2)
        self.assertTrue(state['characters'])

    def test_delayed_character_expansion_does_not_close_it_again(self):
        flow,state=self.flow()
        state['expanded']=False
        state['effects']['expand']=lambda attempt:2.6
        self.reset(flow,state)
        self.assertEqual(state['attempts']['expand'],1)

    def test_missing_character_aggregate_is_not_success(self):
        flow,state=self.flow()
        state['role_visible']=False
        with self.assertRaisesRegex(FlowError,'未确认角色筛选'):
            self.reset(flow,state)
        self.assertNotIn('expand',state['attempts'])

    def test_changed_marker_with_other_attributes_unselected_never_replays_global_toggle(self):
        flow,state=self.flow()
        state['on_click']=lambda kind:state['attrs'].__setitem__(1,False) if kind=='global' else None
        with self.assertRaises(FlowError):
            self.reset(flow,state)
        self.assertEqual(state['attempts']['global'],1)

    def test_recorded_filter_checkbox_pixels_distinguish_unknown_from_unchecked(self):
        flow,_=self.flow()
        flow.image=self.frame.copy()
        for x,y in ((185,239),(969,497)):
            self.assertIs(flow.filter_checkbox_state(x,y),True)
            flow.image[y-6:y+7,x-6:x+7]=180
            self.assertIs(flow.filter_checkbox_state(x,y),False)
            flow.image[y-6:y+7,x-6:x+7]=255
            self.assertIsNone(flow.filter_checkbox_state(x,y))

    def test_recorded_filter_targets_and_character_aggregate_with_real_ocr(self):
        if not (ROOT/'assets/resource/model/ocr/rec.onnx').is_file():
            self.skipTest('Local OCR models required')
        from maa.controller import CustomController
        from maa.custom_action import CustomAction
        from maa.resource import Resource
        from maa.tasker import Tasker

        class Controller(CustomController):
            def connect(self): return True
            def request_uuid(self): return 'story-filter-reset-fixtures'
            def screencap(self): return np.zeros((720,1280,3),dtype=np.uint8)

        resource=Resource()
        self.assertTrue(resource.post_bundle(ROOT/'assets/resource').wait().succeeded)
        controller=Controller()
        self.assertTrue(controller.post_connection().wait().succeeded)
        tasker=Tasker()
        tasker.bind(resource,controller)
        owner,errors=self,[]

        class Check(CustomAction):
            def run(self,context,argv):
                try:
                    for filename in ('skill_practice/filter.png','lowres/member_filter.png'):
                        flow=StoryMiningFlow(context,MiningOptions.parse({}),Path('unused'))
                        flow.image=np.array(Image.open(ROOT/'tests/fixtures'/filename)
                                            .convert('RGB'))[:,:,::-1].copy()
                        flow.snap=Mock()
                        flow.tap=Mock(side_effect=AssertionError('Recorded selected filters need no tap'))
                        box=member_filter_box(flow.image)
                        toggle,points,states=flow.filter_top_state(box)
                        owner.assertIsNotNone(toggle)
                        owner.assertEqual(states,(True,)*4)
                        flow.confirm_all_characters(box)
                        flow.tap.assert_not_called()
                    return True
                except Exception as error:
                    errors.append(error)
                    return False

        resource.register_custom_action('CheckFilterReset',Check())
        result=tasker.post_task('CheckFilterReset',{'CheckFilterReset':{
            'action':'Custom','custom_action':'CheckFilterReset'}}).wait()
        if errors:
            raise errors[0]
        self.assertTrue(result.succeeded)


if __name__=='__main__':
    unittest.main()
