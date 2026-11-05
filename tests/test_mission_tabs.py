"""Mission-label OCR recovery without conflating categories or repeating claims."""
import sys
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock, patch

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'agent'))
from daily_policy import mission_tab_index
from daily_tasks import DailyFlow
from costume_unlock import FlowError
import test_daily_tasks


class MissionTabTests(unittest.TestCase):
    def flow(self):
        f = test_daily_tasks.DailyFlowTests().flow()
        f.click = Mock()
        f.claim_mission_category = Mock()
        f.scroll_mission_tabs = Mock(return_value=False)
        return f

    def retry_flow(self, selected=None):
        f = self.flow()
        f.report['claims'] = ['已经领取的分类']
        old, target = '邀请邦友', '回归邀请任务'
        f.mission_tabs = Mock(return_value=[(old,(150,219)),(target,(150,403))])
        def hits(text):
            return [SimpleNamespace(text=text,box=[100,200,140,25])]
        f.ocr = Mock(side_effect=[hits(text) for text in selected]) if selected else Mock(return_value=hits(old))
        return f, target, [(old,(150,219)),(target,(150,310))]

    @patch('daily_tasks.time.sleep')
    def test_missed_tab_click_retries_fresh_coordinates_after_stable_observations(self, _):
        f, name, tabs = self.retry_flow(['邀请邦友']*5+['回归邀请任务'])
        self.assertEqual(f.select_mission_tab(name,tabs),name)
        self.assertEqual([c.args for c in f.tap.call_args_list],[(150,310),(150,403)])
        self.assertEqual(f.report['mission_tab_retries'],[{
            'category':name,'selected':'邀请邦友','retry':1,'point':[150,403]}])
        self.assertEqual(f.report['claims'],['已经领取的分类'])
        f.claim_mission_category.assert_not_called()

    @patch('daily_tasks.time.sleep')
    def test_delayed_tab_success_before_retry_never_clicks_again(self, _):
        f,name,tabs = self.retry_flow(['邀请邦友']*3+['回归邀请任务'])
        self.assertEqual(f.select_mission_tab(name,tabs),name)
        f.tap.assert_called_once_with(150,310)
        self.assertNotIn('mission_tab_retries',f.report)

    @patch('daily_tasks.time.sleep')
    def test_stuck_tab_stops_after_three_clicks_without_replaying_claims(self, _):
        f,name,tabs = self.retry_flow()
        with self.assertRaisesRegex(FlowError,'未确认任务标签'):
            f.select_mission_tab(name,tabs)
        self.assertEqual(f.tap.call_count,3)
        self.assertEqual(len(f.report['mission_tab_retries']),2)
        self.assertEqual(f.report['claims'],['已经领取的分类'])
        f.claim_mission_category.assert_not_called()

    @patch('daily_tasks.time.sleep')
    def test_changed_selection_or_missing_target_prevents_retry(self, _):
        for change in ('selection','missing','moving'):
            with self.subTest(change=change):
                f,name,tabs = self.retry_flow(['邀请邦友']*3+['其他任务'] if change=='selection' else None)
                if change=='missing':
                    f.mission_tabs.return_value=[('邀请邦友',(150,219))]
                if change=='moving':
                    f.mission_tabs.side_effect=[[(name,(150,y))] for y in (310,330,350,370)]
                with self.assertRaisesRegex(FlowError,'未确认任务标签'):
                    f.select_mission_tab(name,tabs)
                f.tap.assert_called_once_with(150,310)

    @patch('daily_tasks.time.sleep')
    def test_leaving_mission_page_or_stop_request_prevents_retry(self, _):
        for change in ('page','stop'):
            with self.subTest(change=change):
                f,name,tabs = self.retry_flow()
                if change=='page':
                    f.wait.side_effect=[None]*3+[FlowError('页面识别超时')]
                else:
                    f.check_stop=Mock(side_effect=[None,FlowError('任务已被用户停止')])
                with self.assertRaises(FlowError):
                    f.select_mission_tab(name,tabs)
                f.tap.assert_called_once_with(150,310)
                f.claim_mission_category.assert_not_called()

    def test_one_extra_or_missing_nonnumeric_character(self):
        clean = '限时招募券获取任务'
        for noisy in ('产' + clean, clean + '产', '限时招募产券获取任务', '限时招募获取任务'):
            with self.subTest(noisy=noisy):
                self.assertEqual(mission_tab_index(clean, [noisy, '期间限定任务']), 0)
                self.assertEqual(mission_tab_index(noisy, [clean, '期间限定任务']), 0)

    def test_empty_short_unrelated_substituted_and_numeric_labels_rejected(self):
        for selected, name in (('', ''), ('任务', '新任务'), ('每日演出奖励任务', '每月演出奖励任务'),
                               ('其他任务', '限时招募券获取任务'), ('限时招募券获取任务', '产产限时招募券获取任务'),
                               ('活动第1阶段任务', '活动第10阶段任务')):
            with self.subTest(selected=selected, name=name):
                self.assertIsNone(mission_tab_index(selected, [name]))

    def test_exact_other_category_takes_precedence_over_near_match(self):
        self.assertEqual(mission_tab_index('限时招募券获取任务',
                         ['产限时招募券获取任务', '限时招募券获取任务']), 1)

    def test_duplicate_and_ambiguous_categories_rejected(self):
        for names in (['限时招募券获取任务'] * 2,
                      ['产限时招募券获取任务', '限时招募券获取任务产']):
            self.assertIsNone(mission_tab_index('限时招募券获取任务', names))

    def test_noisy_and_corrected_names_are_both_visited_without_duplicate_claim(self):
        f = self.flow()
        noisy, clean = '产限时招募券获取任务', '限时招募券获取任务'
        f.mission_tabs = Mock(side_effect=[[(noisy, (150, 310))],
                                          [(clean, (150, 210))], [(noisy, (150, 210))]])
        f.scroll_mission_tabs = Mock(side_effect=[False, True, False])
        f.ocr = Mock(return_value=[SimpleNamespace(text=clean, box=[100, 200, 140, 25])])
        f.missions()
        f.tap.assert_called_once_with(150, 310)
        f.claim_mission_category.assert_called_once_with(clean)
        self.assertEqual(f.report['mission_tabs'], [clean])
        self.assertEqual(f.report['status'], 'finished')
        self.assertTrue(f.report['warnings'])

    @patch('daily_tasks.time.sleep')
    def test_transient_blank_read_waits_without_clicking_again(self, _):
        f = self.flow()
        f.mission_tabs = Mock(return_value=[('新增任务', (150, 310))])
        f.ocr = Mock(side_effect=[[], [SimpleNamespace(text='新增任务', box=[100, 200, 140, 25])]])
        f.missions()
        f.tap.assert_called_once_with(150, 310)
        f.claim_mission_category.assert_called_once_with('新增任务')
        self.assertEqual(f.ocr.call_count, 2)

    @patch('daily_tasks.time.sleep')
    def test_wrong_or_ambiguous_selection_stops_before_any_claim(self, _):
        for names, selected in ((['期间限定任务'], '限时招募券获取任务'),
                                (['产限时招募券获取任务', '限时招募券获取任务'], '限时招募券获取任务'),
                                (['产限时招募券获取任务', '限时招募券获取任务产'], '限时招募券获取任务'),
                                (['期间限定任务'], '')):
            with self.subTest(names=names, selected=selected):
                f = self.flow()
                f.mission_tabs = Mock(return_value=[(name, (150, 310+i*90)) for i, name in enumerate(names)])
                f.ocr = Mock(return_value=[SimpleNamespace(text=selected, box=[100, 200, 140, 25])])
                with self.assertRaisesRegex(FlowError, '未确认任务标签'):
                    f.missions()
                f.claim_mission_category.assert_not_called()
                self.assertEqual(f.ocr.call_count, 3)
                f.tap.assert_called_once_with(150, 310)

    def test_real_failure_selected_label_with_production_ocr(self):
        root = Path(__file__).resolve().parents[1]
        if not (root/'assets/resource/model/ocr/rec.onnx').is_file():
            self.skipTest('Local OCR models required')
        from PIL import Image
        from maa.controller import CustomController
        from maa.custom_action import CustomAction
        from maa.resource import Resource
        from maa.tasker import Tasker
        frame = np.zeros((720, 1280, 3), dtype=np.uint8)
        frame[:, :300] = np.asarray(Image.open(root/'tests/fixtures/daily/mission_tab_noise.png').convert('RGB'))[:, :, ::-1]

        class Controller(CustomController):
            def connect(self): return True
            def request_uuid(self): return 'mission-tab-noise-fixture'
            def screencap(self): return frame

        resource = Resource()
        self.assertTrue(resource.post_bundle(root/'assets/resource').wait().succeeded)
        controller = Controller()
        self.assertTrue(controller.post_connection().wait().succeeded)
        tasker = Tasker()
        tasker.bind(resource, controller)
        actual, errors = [], []

        class Check(CustomAction):
            def run(self, ctx, argv):
                try:
                    f = DailyFlow(ctx)
                    actual.append(f.confirm_mission_tab('产限时招募券获取任务',
                        [('期间限定任务', (150, 125)), ('产限时招募券获取任务', (150, 219)),
                         ('生日庆祝计划', (150, 310))]))
                    actual.append(f.report['warnings'])
                    return True
                except Exception as exc:
                    errors.append(repr(exc))
                    return False

        resource.register_custom_action('MissionTabCheck', Check())
        self.assertTrue(tasker.post_task('MissionTabCheck', {'MissionTabCheck': {
            'action': 'Custom', 'custom_action': 'MissionTabCheck'}}).wait().succeeded, errors)
        self.assertEqual(actual[0], '限时招募券获取任务')
        self.assertTrue(actual[1])

    @patch('daily_tasks.time.sleep')
    def test_recorded_missed_tab_recovers_without_reclaiming_previous_rewards(self, _):
        root = Path(__file__).resolve().parents[1]
        if not (root/'tests/fixtures/daily/mission_tab_missed.png').is_file():
            self.skipTest('Private failure screenshot required: mission_tab_missed.png')
        if not (root/'assets/resource/model/ocr/rec.onnx').is_file():
            self.skipTest('Local OCR models required')
        from PIL import Image
        from maa.controller import CustomController
        from maa.custom_action import CustomAction
        from maa.resource import Resource
        from maa.tasker import Tasker
        def load(name):
            return np.asarray(Image.open(root/f'tests/fixtures/daily/{name}.png').convert('RGB'))[:,:,::-1].copy()
        current = [load('mission_tab_missed')]
        destination = load('return_invitation')
        class Controller(CustomController):
            def connect(self): return True
            def request_uuid(self): return 'mission-tab-missed-fixture'
            def screencap(self): return current[0]
        resource = Resource()
        self.assertTrue(resource.post_bundle(root/'assets/resource').wait().succeeded)
        controller = Controller()
        self.assertTrue(controller.post_connection().wait().succeeded)
        tasker = Tasker(); tasker.bind(resource,controller)
        case = self; errors = []
        class Check(CustomAction):
            def run(self, ctx, argv):
                try:
                    f = DailyFlow(ctx); f.snap()
                    claims = ['限时招募券获取任务','生日庆祝计划','每月演出任务']
                    f.report['claims'] = list(claims)
                    tabs = f.mission_tabs()
                    case.assertIn('回归邀请任务',[name for name,_ in tabs])
                    taps = []
                    def navigate(x,y):
                        case.assertTrue(100 < x < 200 and 280 < y < 350,(x,y))
                        taps.append((x,y))
                        if len(taps)==2:
                            current[0] = destination
                    f.tap = Mock(side_effect=navigate)
                    f.tap_hit = Mock(side_effect=AssertionError('No reward submission expected'))
                    selected = f.select_mission_tab('回归邀请任务',tabs)
                    case.assertEqual(selected,'回归邀请任务')
                    case.assertEqual(len(taps),2)
                    f.claim_mission_category(selected)
                    case.assertEqual(f.report['claims'],claims)
                    case.assertEqual(f.report['mission_pages'],[{
                        'category':'回归邀请任务','page':1,'total':1}])
                    f.tap_hit.assert_not_called()
                    return True
                except Exception:
                    import traceback
                    errors.append(traceback.format_exc())
                    return False
        resource.register_custom_action('MissionTabRecoveryReplay',Check())
        self.assertTrue(tasker.post_task('MissionTabRecoveryReplay',{'MissionTabRecoveryReplay':{
            'action':'Custom','custom_action':'MissionTabRecoveryReplay'}}).wait().succeeded,errors)


if __name__ == '__main__':
    unittest.main()
