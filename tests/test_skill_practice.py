import json
import sys
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock, patch

import numpy as np
from PIL import Image

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT/'agent'))
sys.path.insert(0, str(ROOT/'tools'))
from costume_unlock import FlowError
from skill_practice import (SkillPracticeFlow, skill_level, pink_checked,
                            grey_unchecked, same_member_slots, verify_confirmation,
                            target_grid_clear, target_grid_unchanged)
from interface_descriptions import TASK_DESCRIPTIONS


def hit(text, box):
    return SimpleNamespace(text=text, box=box)


class SkillPracticeTests(unittest.TestCase):
    def flow(self):
        f = SkillPracticeFlow(SimpleNamespace(tasker=SimpleNamespace(
            stopping=False, controller=None)), Path('unused'))
        f.image = np.full((720, 1280, 3), 255, dtype=np.uint8)
        for name in ('snap', 'save_frame', 'save_report', 'require_material_page',
                     'require_clear_notification_overlay', 'tap', 'tap_hit',
                     'click', 'wait', 'prepare_materials'):
            setattr(f, name, Mock())
        frames = {}
        f.save_frame.side_effect = lambda name: frames.update({name: f.image.copy()})
        f.load_frame = Mock(side_effect=lambda name: frames[name].copy())
        f.ocr = Mock(return_value=[])
        return f

    def test_strict_skill_levels(self):
        for text, level in [('Lv. 1 / 5', 1), ('５／５', 5), ('2/5', 2)]:
            self.assertEqual(skill_level(text), level)
        for text in ('', 'Lv.50/50', '0/5', '6/5', '1/6', '剩余1/5', '15/5'):
            with self.assertRaises(FlowError):
                skill_level(text)

    def test_only_complete_same_member_cards_are_candidates(self):
        slots = same_member_slots([
            hit('相同成员加成', [664, 202, 100, 21]),
            hit('相同成员加成', [790, 328, 102, 20]),
            hit('相同成员加成', [916, 455, 102, 20]),  # clipped third row
            hit('其他成员', [1045, 202, 100, 21]),
            hit('相同成员加成', [400, 202, 100, 21]),
            hit('相同成员加成', [665, 202, 100, 21]),
        ])
        self.assertEqual(slots, [(715, 274), (841, 400)])

    def test_confirmation_rejects_tickets_and_other_spending(self):
        good = '已选择的休息室成员将会消失。\n确定进行技能练习吗？'
        verify_confirmation(good)
        for text in ('确定进行练习吗？', '使用技能练习券。确定进行技能练习吗？',
                     good+'星石', good+'金币', good+'购买'):
            with self.assertRaises(FlowError):
                verify_confirmation(text)

    def selection_flow(self):
        f = self.flow()
        f.image = np.array(Image.open(ROOT/'tests/fixtures/skill_practice'/
                                      'materials.png').convert('RGB'))[:, :, ::-1].copy()
        f.controller = Mock()
        f.controller.post_click.return_value.wait.return_value.succeeded = True
        f.material_slots = Mock(return_value=[(715, 274)])
        state = {'time': 0., 'value': (2, 2, 0)}
        f.preview = Mock(side_effect=lambda: state['value'])
        return f, state

    def select_with_clock(self, f, state):
        def sleep(seconds):
            state['time'] += seconds
        with patch('skill_practice.time.monotonic', side_effect=lambda: state['time']), \
                patch('skill_practice.time.sleep', side_effect=sleep):
            return f.select_material((715, 274), (2, 2, 0))

    def mark_material_selected(self, f, state):
        f.image[234:249, 674:689] = (106, 47, 245)
        state['value'] = (2, 3, 135)

    def test_missed_material_click_retries_then_confirms_without_submitting(self):
        for successful_click in (1, 2, 3):
            with self.subTest(successful_click=successful_click):
                f, state = self.selection_flow()
                times = []
                def click(*point):
                    times.append(state['time'])
                    if len(times) == successful_click:
                        self.mark_material_selected(f, state)
                    return f.controller.post_click.return_value
                f.controller.post_click.side_effect = click
                self.assertEqual(self.select_with_clock(f, state), (2, 3, 135))
                self.assertEqual(f.controller.post_click.call_count, successful_click)
                self.assertTrue(all(b-a >= 8 for a, b in zip(times, times[1:])))
                f.click.assert_not_called()
                f.tap_hit.assert_not_called()

    def test_material_click_retries_are_bounded_and_do_not_submit(self):
        f, state = self.selection_flow()
        with self.assertRaisesRegex(FlowError, '不提交练习（已点击 3 次）'):
            self.select_with_clock(f, state)
        self.assertEqual(f.controller.post_click.call_count, 3)
        f.click.assert_not_called()
        f.tap_hit.assert_not_called()

    def test_delayed_material_response_does_not_toggle_selection_off(self):
        f, state = self.selection_flow()
        def snap():
            if state['time'] >= 7.5:
                self.mark_material_selected(f, state)
        f.snap.side_effect = snap
        self.assertEqual(self.select_with_clock(f, state), (2, 3, 135))
        f.controller.post_click.assert_called_once_with(715, 274)

    def test_any_selection_or_preview_change_prevents_retry_even_if_it_reverts(self):
        for effect in ('checkbox', 'experience', 'level'):
            with self.subTest(effect=effect):
                f, state = self.selection_flow()
                original = f.image.copy()
                def snap():
                    f.image = original.copy()
                    state['value'] = (2, 2, 0)
                    if .3 <= state['time'] < .8:
                        if effect == 'checkbox':
                            f.image[234:249, 674:689] = (106, 47, 245)
                        else:
                            state['value'] = (2, 2, 135) if effect == 'experience' else (2, 3, 0)
                f.snap.side_effect = snap
                with self.assertRaisesRegex(FlowError, '已点击 1 次'):
                    self.select_with_clock(f, state)
                f.controller.post_click.assert_called_once_with(715, 274)

    def test_changed_or_uncertain_material_page_prevents_retry(self):
        for change in ('member', 'card', 'label_missing', 'label_moved', 'unknown_checkbox'):
            with self.subTest(change=change):
                f, state = self.selection_flow()
                def snap():
                    if change == 'member':
                        f.image[145:525, 110:610] = 0
                    elif change == 'card':
                        f.image[260:310, 700:750] = 0
                    elif change == 'unknown_checkbox':
                        f.image[234:249, 674:689] = 255
                    else:
                        f.material_slots.return_value = [] if change == 'label_missing' else [(715, 400)]
                f.snap.side_effect = snap
                with self.assertRaisesRegex(FlowError, '已点击 1 次'):
                    self.select_with_clock(f, state)
                f.controller.post_click.assert_called_once_with(715, 274)

    def test_retry_requires_two_unchanged_frames_and_checks_stop(self):
        for change in ('selected', 'experience', 'member', 'stop'):
            with self.subTest(change=change):
                f, state = self.selection_flow()
                reference = f.image.copy()
                samples = []
                def snap():
                    samples.append(state['time'])
                    if len(samples) == 1:
                        return
                    if change == 'selected':
                        f.image[234:249, 674:689] = (106, 47, 245)
                    elif change == 'experience':
                        state['value'] = (2, 3, 135)
                    elif change == 'member':
                        f.image[145:525, 110:610] = 0
                    else:
                        f.ctx.tasker.stopping = True
                        f.check_stop()
                f.snap.side_effect = snap
                with patch('skill_practice.time.sleep'):
                    if change == 'stop':
                        with self.assertRaisesRegex(FlowError, '用户停止'):
                            f.material_retry_ready((715, 274), (2, 2, 0), reference)
                    else:
                        self.assertFalse(f.material_retry_ready((715, 274), (2, 2, 0), reference))
                self.assertEqual(len(samples), 2)
                f.controller.post_click.assert_not_called()

    def test_material_success_requires_consecutive_confirming_frames(self):
        f, state = self.selection_flow()
        original = f.image.copy()
        frames = []
        def snap():
            frames.append(state['time'])
            self.mark_material_selected(f, state)
            if len(frames) == 2:
                f.image = original.copy()
        f.snap.side_effect = snap
        self.assertEqual(self.select_with_clock(f, state), (2, 3, 135))
        self.assertEqual(len(frames), 4)
        f.controller.post_click.assert_called_once_with(715, 274)

    def test_selected_material_and_controller_failure_are_not_retried(self):
        f, state = self.selection_flow()
        self.mark_material_selected(f, state)
        with self.assertRaisesRegex(FlowError, '材料已选中'):
            self.select_with_clock(f, state)
        f.controller.post_click.assert_not_called()
        f, state = self.selection_flow()
        f.controller.post_click.return_value.wait.return_value.succeeded = False
        with self.assertRaisesRegex(FlowError, '选择同名材料失败'):
            self.select_with_clock(f, state)
        f.controller.post_click.assert_called_once_with(715, 274)

    def test_invalid_material_page_stops_before_retry(self):
        f, state = self.selection_flow()
        f.preview.side_effect = FlowError('未知弹窗')
        with self.assertRaisesRegex(FlowError, '未知弹窗'):
            self.select_with_clock(f, state)
        f.controller.post_click.assert_called_once_with(715, 274)

    def batch_flow(self, count, maximum_at=None):
        f = self.flow()
        selected = set()
        points = [(715+i*126, 274) for i in range(count)]
        state = {'scrolled': False, 'value': (1, 1, 0)}
        f.preview = lambda: state['value']
        f.material_slots = lambda: [] if state['scrolled'] else points
        f.clipped_same_members = Mock(return_value=False)
        f.selected = lambda p: p in selected
        def select(point, previous):
            self.assertEqual(previous, state['value'])
            selected.add(point)
            after = 5 if maximum_at == len(selected) else 2
            state['value'] = (1, after, 135*len(selected))
            return state['value']
        f.select_material = Mock(side_effect=select)
        def scroll(*args):
            state['scrolled'] = True
            f.image[:] = 230
        f.swipe = Mock(side_effect=scroll)
        return f, selected

    def test_all_same_members_selected_in_one_batch_without_submitting_per_card(self):
        f, selected = self.batch_flow(4)
        row = {}
        self.assertTrue(f.select_batch(row))
        self.assertEqual(len(selected), 4)
        self.assertEqual(row['selected_cards'], 4)
        self.assertEqual(row['selection_end'], 'no_same_members')
        f.swipe.assert_not_called()
        f.click.assert_not_called()  # submission belongs to submit_batch only

    def test_fresh_material_page_does_not_click_lounge_reset_or_scroll(self):
        f = self.flow()
        f.material_filter_ready = True
        f.select_all_filters = Mock()
        f.swipe = Mock()
        SkillPracticeFlow.prepare_materials(f)
        f.click.assert_not_called()
        f.tap.assert_not_called()
        f.swipe.assert_not_called()
        f.select_all_filters.assert_not_called()

    def test_material_filter_is_selected_all_once_without_other_setup_clicks(self):
        f = self.flow()
        f.select_all_filters = Mock()
        SkillPracticeFlow.prepare_materials(f)
        SkillPracticeFlow.prepare_materials(f)
        f.select_all_filters.assert_called_once_with(materials=True)
        f.click.assert_not_called()

    def test_unexpected_selection_is_never_mixed_into_a_new_batch(self):
        f = self.flow()
        f.preview = Mock(return_value=(1, 2, 135))
        with self.assertRaisesRegex(FlowError, '未知材料'):
            f.select_batch({})
        f.click.assert_not_called()

    def test_full_preview_stops_before_consuming_extra_cards(self):
        f, selected = self.batch_flow(4, maximum_at=2)
        row = {}
        self.assertTrue(f.select_batch(row))
        self.assertEqual(len(selected), 2)
        self.assertEqual(row['level_preview'], 5)
        self.assertEqual(row['selection_end'], 'maximum_preview')
        f.swipe.assert_not_called()

    def test_max_skill_and_no_materials_never_submit(self):
        for level in (1, 5):
            f = self.flow()
            f.preview = Mock(return_value=(level, level, 0))
            f.material_slots = Mock(return_value=[])
            f.clipped_same_members = Mock(return_value=False)
            row = {}
            self.assertFalse(f.select_batch(row))
            self.assertEqual(row['selected_cards'], 0)
            f.click.assert_not_called()

    def test_selected_duplicate_artwork_is_not_used_as_material_identity(self):
        f, selected = self.batch_flow(3)
        # Duplicate cards can have identical portraits. Only selection checkboxes
        # distinguish their slots; no portrait deduplication in the material pool.
        row = {}
        f.select_batch(row)
        self.assertEqual(row['selected_cards'], 3)

    def test_scroll_only_to_reveal_a_known_clipped_same_member(self):
        f, selected = self.batch_flow(1)
        state = {'page': 0}
        f.material_slots = lambda: [(715, 274)] if state['page'] == 0 else [(841, 274)]
        f.clipped_same_members = lambda: state['page'] == 0
        def scroll(*args):
            state['page'] += 1
            f.image[:] = 230
        f.swipe = Mock(side_effect=scroll)
        row = {}
        self.assertTrue(f.select_batch(row))
        self.assertEqual(row['selected_cards'], 2)
        f.swipe.assert_called_once()

    def test_all_filters_use_verified_reset_and_clear_target_story_radios(self):
        f = self.flow()
        order=[]
        f.reset_all_member_filters=Mock(side_effect=lambda box:order.append('reset'))
        f.clear_story_radio=Mock(side_effect=lambda box,label:order.append(label))
        f.confirm_member_filter=Mock(side_effect=lambda box,**kwargs:order.append('confirm'))
        with patch('skill_practice.member_filter_box',return_value=[129,34,1022,653]):
            f.select_all_filters(materials=False)
        self.assertEqual(order,['reset','小故事','动画','confirm'])

    def test_material_filter_uses_verified_reset_without_assuming_story_radios(self):
        f=self.flow()
        f.reset_all_member_filters=Mock()
        f.clear_story_radio=Mock()
        f.confirm_member_filter=Mock()
        f.reco=Mock(side_effect=lambda node:node in ('SP_Page','SP_Lounge'))
        with patch('skill_practice.member_filter_box',return_value=[129,34,1022,653]):
            f.select_all_filters(materials=True)
        f.reset_all_member_filters.assert_called_once()
        f.clear_story_radio.assert_not_called()
        ready=f.confirm_member_filter.call_args.kwargs['ready']
        self.assertTrue(ready())
        f.reco=Mock(return_value=True)
        self.assertFalse(ready())

    def test_unconfirmed_filter_reset_never_applies_partial_skill_filters(self):
        f=self.flow()
        f.reset_all_member_filters=Mock(side_effect=FlowError('角色筛选仍未全部选中'))
        f.confirm_member_filter=Mock()
        with patch('skill_practice.member_filter_box',return_value=[129,34,1022,653]),self.assertRaises(FlowError):
            f.select_all_filters(materials=False)
        f.confirm_member_filter.assert_not_called()

    def test_filter_is_enabled_once_and_verified(self):
        f = self.flow()
        def select(*args):
            f.image[120:141, 284:305] = (120, 50, 255)
        f.tap.side_effect = select
        f.ensure_filter()
        f.ensure_filter()
        f.tap.assert_called_once_with(294, 130)

    def test_filter_that_does_not_enable_stops(self):
        f = self.flow()
        with self.assertRaisesRegex(FlowError, '筛选已勾选'):
            f.ensure_filter()

    def test_result_timeout_never_replays_consuming_click(self):
        f = self.flow()
        f.reco = Mock(return_value=None)
        row = dict(status='submitted', selected_cards=3)
        with patch('skill_practice.time.monotonic', side_effect=[0, 1, 46]), patch('skill_practice.time.sleep'):
            with self.assertRaisesRegex(FlowError, '不重复提交'):
                f.await_result(row)
        f.click.assert_not_called()
        f.tap_hit.assert_not_called()
        self.assertEqual(f.report['consumed_cards'], 0)

    def test_repeated_result_counts_once_and_closes_only_result_button(self):
        f = self.flow()
        state = {'frame': 0}
        f.snap.side_effect = lambda: state.update(frame=state['frame']+1)
        f.reco = lambda node: (node in ('SP_Result', 'SP_ResultOK') if state['frame'] <= 2 else node == 'SP_Page')
        row = dict(member='测试成员', selected_cards=3)
        with patch('skill_practice.time.sleep'), patch('skill_practice.dialog_box', return_value=None):
            f.await_result(row)
        self.assertEqual(f.report['consumed_cards'], 3)
        self.assertEqual(f.report['practiced_members'], 1)
        self.assertEqual(f.tap_hit.call_count, 2)
        f.click.assert_not_called()

    def test_tickets_tab_and_modal_are_rejected_before_selection(self):
        f = self.flow()
        f.reco = Mock(return_value=True)
        f.image[130:136, 680:720] = (120, 50, 255)
        with self.assertRaisesRegex(FlowError, '不使用技能练习券'):
            SkillPracticeFlow.require_material_page(f)
        f.require_clear_notification_overlay.side_effect = FlowError('未知弹窗')
        with self.assertRaisesRegex(FlowError, '未知弹窗'):
            SkillPracticeFlow.require_material_page(f)

    def test_task_is_opt_in_and_description_survives_regeneration(self):
        interface = json.loads((ROOT/'assets/interface.json').read_text(encoding='utf8'))
        task = next(t for t in interface['task'] if t['entry'] == 'UpgradeSameMemberSkills')
        self.assertIs(task['default_check'], False)
        self.assertEqual(task['description'], TASK_DESCRIPTIONS[task['entry']])

    def test_material_scroll_only_when_bonus_group_continues_below_viewport(self):
        f = self.flow()
        f.bonus_hits = [hit('相同成员加成', [664, 202, 100, 21])]
        self.assertFalse(f.clipped_same_members())
        f.bonus_hits.append(hit('相同成员加成', [664, 455, 100, 21]))
        self.assertTrue(f.clipped_same_members())
        f.bonus_hits = [hit('相同成员加成', [664+x*126, 430, 100, 21]) for x in range(4)]
        self.assertTrue(f.clipped_same_members())
        f.bonus_hits.pop()
        self.assertFalse(f.clipped_same_members())

    def test_target_list_reflow_does_not_skip_the_next_member(self):
        f = self.flow()
        f.open_list = Mock()
        f.ensure_filter = Mock()
        f.back = Mock()
        f.open_target = Mock()
        f.return_to_list = Mock()
        f.home = Mock()
        remaining = ['first', 'second']
        f.text = lambda roi: remaining[0]
        f.select_batch = Mock(return_value=True)
        def submit(row):
            self.assertEqual(row['member'], remaining.pop(0))
            row['status'] = 'success_confirmed'
            if not remaining:
                self.mark_empty_list(f)
        f.submit_batch = Mock(side_effect=submit)
        f.swipe = Mock()
        def cards(image):
            return [(344, 258)] if remaining else []
        with patch('skill_practice.member_cards', side_effect=cards), patch(
                'skill_practice.member_signature', side_effect=lambda *args: remaining[0]), patch(
                'skill_practice.same_member_portrait', side_effect=lambda a, b: a == b):
            f.run()
        self.assertEqual([row['member'] for row in f.report['members']], ['first', 'second'])
        self.assertEqual(f.submit_batch.call_count, 2)
        self.assertEqual(f.report['status'], 'finished')
        self.assertEqual(f.report['completion_check'], 'empty_message_confirmed')
        f.home.assert_called_once()

    def list_flow(self):
        f = self.flow()
        for name in ('open_list', 'ensure_filter', 'home', 'swipe', 'open_target', 'return_to_list'):
            setattr(f, name, Mock())
        return f

    def single_target_image(self):
        image = np.full((720, 1280, 3), 255, dtype=np.uint8)
        image[185:665, 285:1140] = np.array(Image.open(
            ROOT/'tests/fixtures/mining/member_single.png').convert('RGB'))[:, :, ::-1]
        return image

    def mark_empty_list(self, f):
        f.image = np.array(Image.open(ROOT/'tests/fixtures/skill_practice/empty_list.png')
                           .convert('RGB'))[:, :, ::-1].copy()
        f.ocr = Mock(return_value=[hit('没有可以进行技能练习的成员。', [527, 383, 370, 32])])

    def test_single_missed_card_cannot_pass_empty_grid_check(self):
        f = self.list_flow()
        f.image = self.single_target_image()
        # This is the original bug: even a real remaining card passes 90% white.
        self.assertGreater(np.mean(f.image[200:650, 280:1120].min(axis=2) > 235), .9)
        self.assertFalse(target_grid_clear(f.image))
        with patch('skill_practice.member_cards', return_value=[]), patch(
                'skill_practice.framed_member_cards', return_value=[]):
            with self.assertRaisesRegex(FlowError, '不报告完成'):
                f.run()
        self.assertLessEqual(f.swipe.call_count, 8)
        f.open_target.assert_not_called()
        f.home.assert_not_called()
        self.assertNotEqual(f.report['status'], 'finished')
        self.assertIn(('unfinished-list.png',), [call.args for call in f.save_frame.call_args_list])

    def test_frame_fallback_recovers_last_card_after_artwork_detector_misses(self):
        f = self.list_flow()
        source = np.array(Image.open(ROOT/'tests/fixtures/mining/selection_anon_grid.png')
                          .convert('RGB'))[:, :, ::-1]
        # Real pale artwork splits into short bands in the original detector.
        # Reflow this last card into the first list slot, as the game does.
        f.image[207:312, 285:404] = source[330:435, 408:527]
        from mining_policy import member_cards
        self.assertEqual(member_cards(f.image), [])
        f.text = Mock(return_value='最后的成员')
        f.select_batch = Mock(return_value=True)
        def submit(row):
            row['status'] = 'success_confirmed'
            self.mark_empty_list(f)
        f.submit_batch = Mock(side_effect=submit)
        f.run()
        f.submit_batch.assert_called_once()
        f.open_target.assert_called_once()
        self.assertEqual(len(f.report['members']), 1)
        self.assertEqual(f.report['status'], 'finished')

    def test_unknown_card_beside_processed_card_prevents_success(self):
        f = self.list_flow()
        f.image = self.single_target_image()
        f.image[205:310, 416:519] = 80  # unrecognized second portrait
        f.text = Mock(return_value='已处理成员')
        f.select_batch = Mock(return_value=True)
        def submit(row):
            row['status'] = 'success_confirmed'
        f.submit_batch = Mock(side_effect=submit)
        with patch('skill_practice.member_cards', return_value=[(344, 259)]), patch(
                'skill_practice.framed_member_cards', return_value=[]):
            with self.assertRaisesRegex(FlowError, '不报告完成'):
                f.run()
        f.submit_batch.assert_called_once()
        f.home.assert_not_called()

    def test_known_clipped_card_is_rechecked_without_repeating_practice(self):
        f = self.list_flow()
        complete = self.single_target_image()
        clipped = np.full_like(complete, 255)
        clipped[185:251, 285:404] = complete[249:315, 285:404]
        f.image = complete.copy()
        f.text = Mock(return_value='已确认满级的成员')
        def select(row):
            row['status'] = 'already_max'
            return False
        f.select_batch = Mock(side_effect=select)
        f.submit_batch = Mock()
        f.swipe.side_effect = lambda x, start, end: setattr(
            f, 'image', complete.copy() if start < end else clipped.copy())
        f.run()
        self.assertEqual(f.report['status'], 'finished')
        f.open_target.assert_called_once()
        f.submit_batch.assert_not_called()
        self.assertGreater(f.load_frame.call_count, 0)

    def test_unknown_clipped_card_cannot_be_cleared_by_later_blank_screen(self):
        f = self.list_flow()
        complete = self.single_target_image()
        clipped = np.full_like(complete, 255)
        clipped[185:251, 285:404] = complete[249:315, 285:404]
        state = {'saw_top': False}
        def scroll(x, start, end):
            if start < end:
                f.image = clipped.copy()
                state['saw_top'] = True
            elif state['saw_top']:
                f.image[:] = 255
        f.swipe.side_effect = scroll
        with patch('skill_practice.member_cards', return_value=[]), patch(
                'skill_practice.framed_member_cards', return_value=[]):
            with self.assertRaisesRegex(FlowError, '不报告完成'):
                f.run()
        f.home.assert_not_called()

    def test_rewind_finds_last_card_without_replaying_confirmed_member(self):
        f = self.list_flow()
        f.image = self.single_target_image()
        original = f.image.copy()
        state = {'first_done': False, 'found_last': False, 'done': False}
        f.text = lambda roi: '最后成员' if state['found_last'] else '首位成员'
        f.select_batch = Mock(return_value=True)
        def submit(row):
            row['status'] = 'success_confirmed'
            if not state['first_done']:
                state['first_done'] = True
            else:
                state['done'] = True
            f.image[:] = 255
            if state['done']:
                self.mark_empty_list(f)
        f.submit_batch = Mock(side_effect=submit)
        def scroll(x, start, end):
            if start < end and state['first_done'] and not state['found_last']:
                state['found_last'] = True
                f.image = original.copy()
                f.image[225:290, 310:378] = (100, 80, 150)
        f.swipe.side_effect = scroll
        f.run()
        self.assertTrue(state['done'])
        self.assertEqual(f.submit_batch.call_count, 2)
        self.assertEqual([row['member'] for row in f.report['members']], ['首位成员', '最后成员'])
        self.assertEqual(f.report['status'], 'finished')

    def test_single_card_movement_is_not_mistaken_for_scroll_end(self):
        first = self.single_target_image()
        second = np.full_like(first, 255)
        second[345:665, 285:1140] = first[185:505, 285:1140]
        self.assertFalse(target_grid_unchanged(first, second))
        self.assertTrue(target_grid_unchanged(first, first.copy()))

    def test_real_stationary_card_badge_animation_is_not_grid_movement(self):
        images = [np.array(Image.open(ROOT/'tests/fixtures/skill_practice'/name).convert('RGB'))
                  [:, :, ::-1].copy() for name in ('target_bonus_before.png', 'target_bonus_after.png')]
        first, second = images
        changed = np.max(np.abs(first[185:665, 285:1140].astype(np.int16)
                               - second[185:665, 285:1140].astype(np.int16)), axis=2) > 24
        self.assertGreater(np.max(changed.reshape(32, 15, 57, 15).mean(axis=(1, 3))), .25)
        self.assertTrue(target_grid_unchanged(first, second))
        shifted = np.full_like(first, 255)
        shifted[285:665, 285:1140] = second[185:565, 285:1140]
        self.assertFalse(target_grid_unchanged(first, shifted))
        # A stable known card cannot conceal another unrecognized moving card.
        first[220:320, 420:515] = 80
        second[330:430, 420:515] = 80
        self.assertFalse(target_grid_unchanged(first, second))

    def test_isolated_badge_animation_is_not_ignored_without_a_matching_card(self):
        images = []
        for name in ('target_bonus_before.png', 'target_bonus_after.png'):
            source = np.array(Image.open(ROOT/'tests/fixtures/skill_practice'/name).convert('RGB'))
            image = np.full_like(source, 255)
            image[185:209, 285:405] = source[185:209, 285:405]
            images.append(image[:, :, ::-1].copy())
        self.assertFalse(target_grid_unchanged(*images))

    def test_unknown_grid_movement_hits_scroll_bound_instead_of_finished(self):
        f = self.list_flow()
        first = self.single_target_image()
        second = np.full_like(first, 255)
        second[345:665, 285:1140] = first[185:505, 285:1140]
        f.image = first.copy()
        f.swipe.side_effect = lambda *args: setattr(f, 'image',
            second.copy() if f.swipe.call_count % 2 else first.copy())
        with patch('skill_practice.member_cards', return_value=[]), patch(
                'skill_practice.framed_member_cards', return_value=[]):
            with self.assertRaisesRegex(FlowError, '滚动上限'):
                f.run()
        self.assertEqual(f.swipe.call_count, 100)
        f.home.assert_not_called()

    def test_rescan_obeys_user_stop(self):
        f = self.list_flow()
        f.swipe.side_effect = lambda *args: setattr(f.ctx.tasker, 'stopping', True)
        with self.assertRaisesRegex(FlowError, '用户停止'):
            f.run()
        f.home.assert_not_called()
        self.assertNotEqual(f.report['status'], 'finished')

    def test_real_empty_message_finishes_without_swiping_or_consumption(self):
        f = self.list_flow()
        self.mark_empty_list(f)
        f.report.update(practiced_members=1, consumed_cards=1)
        f.submit_batch = Mock()
        with patch('skill_practice.time.sleep'):
            f.run()
        f.swipe.assert_not_called()
        self.assertEqual(f.report['status'], 'finished')
        self.assertEqual(f.report['completion_check'], 'empty_message_confirmed')
        self.assertEqual(f.report['consumed_cards'], 1)
        f.open_target.assert_not_called()
        f.submit_batch.assert_not_called()

    def test_plain_blank_without_empty_message_does_not_report_finished(self):
        f = self.list_flow()
        with self.assertRaisesRegex(FlowError, '不报告完成'):
            f.run()
        f.home.assert_not_called()
        self.assertEqual(f.swipe.call_count, 6)

    def test_empty_message_requires_exact_text_and_bounded_location(self):
        for text, box in [
                ('没有可以进行技能练习的成员吗', [527, 383, 370, 32]),
                ('没有成员', [527, 383, 370, 32]),
                ('没有可以进行技能练习的成员。', [285, 185, 855, 480]),
                ('没有可以进行技能练习的成员。', [527, 200, 370, 32])]:
            with self.subTest(text=text, box=box):
                f = self.list_flow()
                self.mark_empty_list(f)
                f.ocr.return_value = [hit(text, box)]
                self.assertFalse(f.empty_target_message())

    def test_empty_message_cannot_hide_a_missed_remaining_card(self):
        f = self.list_flow()
        self.mark_empty_list(f)
        f.image[210:310, 294:395] = 120
        with patch('skill_practice.member_cards', return_value=[]), patch(
                'skill_practice.framed_member_cards', return_value=[]):
            self.assertFalse(f.empty_target_message())
            with patch('skill_practice.time.sleep'):
                self.assertFalse(f.confirm_empty_targets())
        f.home.assert_not_called()

    def test_empty_confirmation_requires_both_fresh_frames(self):
        for change in ('card', 'message_disappears'):
            with self.subTest(change=change):
                f = self.list_flow()
                self.mark_empty_list(f)
                def refresh():
                    if f.ensure_filter.call_count == 2:
                        if change == 'card':
                            f.image = self.single_target_image()
                        else:
                            f.image[:] = 255
                f.ensure_filter.side_effect = refresh
                with patch('skill_practice.time.sleep'):
                    self.assertFalse(f.confirm_empty_targets())
                f.home.assert_not_called()
                self.assertNotEqual(f.report['status'], 'finished')

    def test_empty_confirmation_stops_for_page_filter_overlay_or_stop(self):
        for guard in ('ensure_filter', 'require_clear_notification_overlay', 'stop'):
            with self.subTest(guard=guard):
                f = self.list_flow()
                self.mark_empty_list(f)
                if guard == 'stop':
                    f.ctx.tasker.stopping = True
                else:
                    getattr(f, guard).side_effect = FlowError('未确认当前页面')
                with self.assertRaises(FlowError):
                    f.confirm_empty_targets()
                f.home.assert_not_called()

    def test_material_ocr_miss_recovers_by_observation_only(self):
        f = self.flow()
        row = {}
        def select(row):
            row['status'] = 'no_same_members' if f.select_batch.call_count == 1 else 'batch_ready'
            return f.select_batch.call_count > 1
        f.select_batch = Mock(side_effect=select)
        with patch('skill_practice.time.sleep'):
            self.assertTrue(f.select_target_batch(row))
        self.assertEqual(f.select_batch.call_count, 2)
        f.tap.assert_not_called()
        f.click.assert_not_called()

    def test_unresolved_materials_are_not_treated_as_completed_member(self):
        f = self.flow()
        row = {'status': 'no_same_members'}
        f.select_batch = Mock(return_value=False)
        with patch('skill_practice.time.sleep'):
            with self.assertRaisesRegex(FlowError, '不报告完成'):
                f.select_target_batch(row)
        self.assertEqual(f.select_batch.call_count, 3)
        self.assertEqual(row['status'], 'unresolved_materials')
        f.tap.assert_not_called()

    def test_unconfirmed_submission_stops_before_rescan_or_repeat(self):
        f = self.list_flow()
        f.image = self.single_target_image()
        f.text = Mock(return_value='成员')
        f.select_batch = Mock(return_value=True)
        f.submit_batch = Mock()  # no confirmed result
        with self.assertRaisesRegex(FlowError, '尚未确认成功'):
            f.run()
        f.submit_batch.assert_called_once()
        f.swipe.assert_not_called()
        f.return_to_list.assert_not_called()

    def test_failed_grid_recognition_is_not_reported_as_empty_success(self):
        f = self.flow()
        f.open_list = Mock()
        f.ensure_filter = Mock()
        f.home = Mock()
        f.swipe = Mock()
        f.image[:] = 120
        with patch('skill_practice.member_cards', return_value=[]):
            with self.assertRaisesRegex(FlowError, '列表也未确认清空'):
                f.run()
        f.home.assert_not_called()
        self.assertEqual(f.report['status'], 'running')

    def test_recorded_pages_with_real_ocr_and_selection_pixels(self):
        if not (ROOT/'assets/resource/model/ocr/rec.onnx').is_file():
            self.skipTest('Local OCR models required')
        from maa.controller import CustomController
        from maa.custom_action import CustomAction
        from maa.resource import Resource
        from maa.tasker import Tasker
        test = self
        class Controller(CustomController):
            def connect(self): return True
            def request_uuid(self): return 'skill-practice-fixtures'
            def screencap(self): return np.zeros((720, 1280, 3), dtype=np.uint8)
        resource = Resource()
        self.assertTrue(resource.post_bundle(ROOT/'assets/resource').wait().succeeded)
        controller = Controller()
        self.assertTrue(controller.post_connection().wait().succeeded)
        tasker = Tasker()
        tasker.bind(resource, controller)
        errors = []
        class Check(CustomAction):
            def run(self, ctx, argv):
                try:
                    return self.verify(ctx)
                except Exception as exc:
                    errors.append(exc)
                    return False

            def verify(self, ctx):
                f = SkillPracticeFlow(ctx, Path('unused'))
                def frame(name):
                    f.image = np.array(Image.open(ROOT/'tests/fixtures/skill_practice'/
                                                 (name+'.png')).convert('RGB'))[:, :, ::-1].copy()
                cases = [('materials', (1, 1, 0), False),
                         ('selected', (1, 2, 135), True),
                         ('max_preview', (3, 5, 1500), True)]
                # Recorded failures remain local; public fixtures and the
                # missed-click unit tests still run in a clean checkout.
                if (ROOT/'tests/fixtures/skill_practice/selection_missed.png').is_file():
                    cases.append(('selection_missed', (2, 2, 0), False))
                for name, expected, selected in cases:
                    frame(name)
                    test.assertEqual(f.preview(), expected)
                    slots = f.material_slots()
                    test.assertEqual(len(slots), 2 if name == 'max_preview' else 1)
                    test.assertTrue(all(f.selected(point) == selected for point in slots))
                    test.assertTrue(all(grey_unchecked(f.image, x-34, y-33) == (not selected)
                                        for x, y in slots))
                    test.assertFalse(f.clipped_same_members())
                    if name == 'selection_missed':
                        reference = f.image.copy()
                        with patch.object(f, 'snap'), patch('skill_practice.time.sleep'):
                            test.assertTrue(f.material_retry_ready(slots[0], expected, reference))
                frame('no_same_members')
                test.assertEqual(f.material_slots(), [])
                frame('list')
                test.assertTrue(f.reco('SP_Filter'))
                test.assertTrue(pink_checked(f.image, 294, 130))
                frame('empty_list')
                test.assertTrue(f.reco('SP_Filter'))
                test.assertTrue(pink_checked(f.image, 294, 130))
                test.assertEqual(f.target_candidates(fallback=True), [])
                test.assertTrue(f.empty_target_message())
                frame('confirm')
                test.assertTrue(f.reco('SP_Confirm'))
                test.assertTrue(f.reco('SP_ConfirmOK'))
                verify_confirmation(f.text([370, 305, 535, 95]))
                with test.assertRaises(FlowError):
                    f.require_material_page()
                frame('result')
                test.assertTrue(f.reco('SP_Result'))
                test.assertTrue(f.reco('SP_ResultOK'))
                test.assertFalse(f.reco('SP_Confirm'))
                frame('filter')
                from mining_stories import member_filter_box
                left, top, width, height = member_filter_box(f.image)
                test.assertTrue(f.hit_text([left, top, width, 100], '^全选[/／]取消全选$'))
                label = f.hit_text([left+20, top+100, 180, height-230], '^种类$|^属性$')
                y = label.box[1]+label.box[3]//2+54
                test.assertTrue(all(pink_checked(f.image, left+x, y) for x in (55, 254, 453, 652)))
                return True
        resource.register_custom_action('SkillFixtures', Check())
        self.assertTrue(tasker.post_task('SkillFixtures', {'SkillFixtures': {
            'action': 'Custom', 'custom_action': 'SkillFixtures'}}).wait().succeeded)
        self.assertFalse(errors, repr(errors))


if __name__ == '__main__':
    unittest.main()
