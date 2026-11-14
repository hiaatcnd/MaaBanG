"""Skill filter resets acknowledge clicks and remove retained story radios."""
import sys
import unittest
from contextlib import ExitStack
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock,patch

import numpy as np
from PIL import Image

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT/'agent'))
from costume_unlock import FlowError
from skill_practice import SkillPracticeFlow


class SkillFilterResetTests(unittest.TestCase):
    box=[129,34,1022,653]

    def flow(self):
        flow=SkillPracticeFlow(SimpleNamespace(tasker=SimpleNamespace(
            stopping=False,controller=None)),Path('unused'))
        flow.image=np.full((720,1280,3),255,dtype=np.uint8)
        state=dict(time=0.,selected=False,visible=True,page=True,clicks=[],on_frame=None,on_click=None)
        def snap():
            flow.check_stop()
            if state['on_frame']:
                state['on_frame']()
            flow.check_stop()
            if not state['page']:
                raise FlowError('弹窗已变化')
        def tap(*point):
            flow.check_stop()
            self.assertIs(state['selected'],False)
            state['clicks'].append((state['time'],point))
            if state['on_click']:
                state['on_click']()
        flow.checked_filter_box=Mock(side_effect=lambda box:snap())
        flow.hit_text=Mock(side_effect=lambda roi,expected:SimpleNamespace(box=[190,300,70,30])
                           if expected=='^小故事$' and state['visible'] else None)
        flow.filter_radio_state=Mock(side_effect=lambda *point:state['selected'])
        flow.tap=Mock(side_effect=tap)
        flow.swipe=Mock()
        return flow,state

    def clock(self,state):
        stack=ExitStack()
        stack.enter_context(patch('skill_practice.time.monotonic',side_effect=lambda:state['time']))
        stack.enter_context(patch('skill_practice.time.sleep',side_effect=lambda seconds:
                                  state.update(time=state['time']+seconds)))
        stack.enter_context(patch('skill_practice.log'))
        return stack

    def test_missed_radio_selection_is_retried_only_while_unselected(self):
        flow,state=self.flow()
        state['on_click']=lambda:state.update(selected=True) if len(state['clicks'])==2 else None
        with self.clock(state):
            flow.clear_story_radio(self.box,'小故事')
        self.assertEqual(len(state['clicks']),2)
        self.assertGreaterEqual(state['clicks'][1][0]-state['clicks'][0][0],3)

    def test_delayed_selection_does_not_replay_the_click(self):
        flow,state=self.flow()
        state['on_frame']=lambda:state.update(selected=True) if state['clicks'] and state['time']-state['clicks'][0][0]>=2.6 else None
        with self.clock(state):
            flow.clear_story_radio(self.box,'小故事')
        self.assertEqual(len(state['clicks']),1)

    def test_already_selected_radio_requires_two_frames_without_clicking(self):
        flow,state=self.flow()
        state['selected']=True
        with self.clock(state):
            flow.clear_story_radio(self.box,'小故事')
        self.assertEqual(flow.checked_filter_box.call_count,2)
        flow.tap.assert_not_called()
        flow.swipe.assert_not_called()

    def test_unknown_circle_never_becomes_unchecked(self):
        flow,state=self.flow()
        state['selected']=None
        with self.clock(state),self.assertRaisesRegex(FlowError,'未确认小故事'):
            flow.clear_story_radio(self.box,'小故事')
        flow.tap.assert_not_called()

    def test_retry_exhaustion_does_not_claim_all_filters(self):
        flow,state=self.flow()
        with self.clock(state),self.assertRaisesRegex(FlowError,'未确认小故事'):
            flow.clear_story_radio(self.box,'小故事')
        self.assertEqual(len(state['clicks']),3)

    def test_target_loss_and_page_change_prevent_reusing_coordinates(self):
        for change in ('visible','page'):
            flow,state=self.flow()
            state['on_click']=lambda:state.update({change:False})
            with self.clock(state),self.assertRaises(FlowError):
                flow.clear_story_radio(self.box,'小故事')
            self.assertEqual(len(state['clicks']),1)

    def test_changed_selected_state_is_not_toggled_again(self):
        flow,state=self.flow()
        state['selected']=True
        state['on_frame']=lambda:state.update(selected=False) if state['time']>=.2 else None
        with self.clock(state),self.assertRaisesRegex(FlowError,'再次变化'):
            flow.clear_story_radio(self.box,'小故事')
        flow.tap.assert_not_called()

    def test_user_stop_interrupts_radio_recovery(self):
        flow,state=self.flow()
        state['on_click']=lambda:setattr(flow.ctx.tasker,'stopping',True)
        with self.clock(state),self.assertRaisesRegex(FlowError,'用户停止'):
            flow.clear_story_radio(self.box,'小故事')
        self.assertEqual(len(state['clicks']),1)

    def test_missing_radio_has_bounded_scrolls(self):
        flow,state=self.flow()
        state['visible']=False
        with self.clock(state),self.assertRaisesRegex(FlowError,'未确认小故事'):
            flow.clear_story_radio(self.box,'小故事')
        self.assertEqual(flow.swipe.call_count,20)
        flow.tap.assert_not_called()

    def test_actual_skill_class_uses_marker_transition_before_accepting_global_reset(self):
        flow,state=self.flow()
        attrs=[True]*4
        characters=False
        attempts=[]
        flow.position_filter_top=Mock()
        toggle=SimpleNamespace(box=[950,60,175,40])
        points=((184,240),(383,240),(582,240),(781,240))
        flow.filter_top_state=Mock(side_effect=lambda box:(flow.check_stop() or toggle,points,tuple(attrs)))
        def marker(*point):
            attempts.append('marker')
            attrs[0]=False
        def global_toggle(hit):
            nonlocal characters
            self.assertFalse(attrs[0])
            attempts.append('global')
            if attempts.count('global')==2:
                attrs[:]=[True]*4
                characters=True
        flow.tap=Mock(side_effect=marker)
        flow.tap_hit=Mock(side_effect=global_toggle)
        flow.confirm_all_characters=Mock(side_effect=lambda box:self.assertTrue(characters))
        with self.clock(state),patch('mining_stories.log'):
            flow.reset_all_member_filters(self.box)
        self.assertEqual(attempts,['marker','global','global'])
        self.assertTrue(characters)

    def test_real_radio_pixels_require_circle_or_filled_center(self):
        flow,_=self.flow()
        # Recorded selected animation-all and unselected animation-only circles.
        flow.image=np.array(Image.open(ROOT/'tests/fixtures/mining/memory_level_sort.png')
                            .convert('RGB'))[:,:,::-1].copy()
        del flow.filter_radio_state
        self.assertIs(flow.filter_radio_state(187,172),True)
        self.assertIs(flow.filter_radio_state(398,172),False)
        self.assertIsNone(flow.filter_radio_state(640,200))
        flow.image[152:193,378:419]=255
        self.assertIsNone(flow.filter_radio_state(398,172))

    def test_shared_confirm_waits_for_two_material_frames_without_another_click(self):
        flow,state=self.flow()
        state.update(page='filter',material_frames=0)
        def snap():
            flow.check_stop()
            if state['page']=='materials':
                state['material_frames']+=1
        flow.snap=Mock(side_effect=snap)
        flow.reco=Mock(side_effect=lambda node:
                       state['page']=='filter' if node=='MN_MemberFilter' else
                       state['page']=='materials' and node in ('SP_Page','SP_Lounge'))
        flow.hit_text=Mock(return_value=SimpleNamespace(box=[650,590,240,65]))
        flow.tap_hit=Mock(side_effect=lambda hit:state.update(page='materials'))
        ready=lambda:flow.reco('SP_Page') and flow.reco('SP_Lounge') and not flow.reco('SP_Filter')
        with self.clock(state),patch('mining_stories.member_filter_box',return_value=self.box),patch(
                'mining_stories.dialog_box',return_value=None):
            flow.confirm_member_filter(self.box,ready=ready)
        self.assertEqual(state['material_frames'],2)
        flow.tap_hit.assert_called_once()


if __name__=='__main__':
    unittest.main()
