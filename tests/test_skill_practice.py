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
                            same_member_slots, verify_confirmation)
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

    def test_all_filter_toggle_restores_checked_state_if_previously_all_selected(self):
        f = self.flow()
        toggle = hit('全选/取消全选', [950, 60, 175, 40])
        label = hit('种类', [164, 171, 88, 30])
        confirm = hit('确定', [650, 590, 240, 65])
        f.hit_text = Mock(side_effect=[toggle, label, confirm])
        def click(h):
            if h is toggle:
                f.image[:] = (120, 50, 255) if f.tap_hit.call_count == 2 else 255
        f.tap_hit.side_effect = click
        with patch('skill_practice.member_filter_box', return_value=[129, 34, 1022, 653]):
            f.select_all_filters(materials=False)
        self.assertEqual(f.tap_hit.call_args_list[0].args, (toggle,))
        self.assertEqual(f.tap_hit.call_args_list[1].args, (toggle,))
        self.assertEqual(f.tap_hit.call_args_list[2].args, (confirm,))

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
        f.home = Mock()
        remaining = ['first', 'second']
        f.text = lambda roi: remaining[0]
        f.select_batch = Mock(return_value=True)
        def submit(row):
            self.assertEqual(row['member'], remaining.pop(0))
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
        f.home.assert_called_once()

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
                for name, expected, selected in [('materials', (1, 1, 0), False),
                                                  ('selected', (1, 2, 135), True),
                                                  ('max_preview', (3, 5, 1500), True)]:
                    frame(name)
                    test.assertEqual(f.preview(), expected)
                    slots = f.material_slots()
                    test.assertEqual(len(slots), 2 if name == 'max_preview' else 1)
                    test.assertTrue(all(f.selected(point) == selected for point in slots))
                    test.assertFalse(f.clipped_same_members())
                frame('no_same_members')
                test.assertEqual(f.material_slots(), [])
                frame('list')
                test.assertTrue(f.reco('SP_Filter'))
                test.assertTrue(pink_checked(f.image, 294, 130))
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
