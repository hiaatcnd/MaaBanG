"""Offline recognition against real screenshots; the controller cannot send inputs."""
import sys
import tempfile
import unittest
from itertools import product
from types import SimpleNamespace
from pathlib import Path
from unittest.mock import Mock, patch

import numpy as np
from PIL import Image
from maa.controller import CustomController
from maa.custom_action import CustomAction
from maa.resource import Resource
from maa.tasker import Tasker
from maa.toolkit import Toolkit

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT/'agent'))
from bestdori_scan import BestdoriScan, read_ratio, character_filter_rows, same_page_portrait, align_page
from bestdori_catalog import load, identify
from bestdori_profiles import ATTRIBUTES
from costume_unlock import FlowError


class Images(CustomController):
    def connect(self): return True
    def request_uuid(self): return 'bestdori-offline-fixtures'
    def get_features(self): return 0
    def screencap(self): return np.zeros((720, 1280, 3), dtype=np.uint8)
    def forbidden(self, *args): raise AssertionError('Offline test attempted an input')
    click = swipe = touch_down = touch_move = touch_up = click_key = input_text = key_down = key_up = start_app = stop_app = forbidden


class ScanTests(unittest.TestCase):
    def test_ocr_ids_rainbow_stars_and_master_zero_and_four(self):
        if not (ROOT/'assets/resource/model/ocr/rec.onnx').is_file():
            self.skipTest('Local OCR models required')
        with tempfile.TemporaryDirectory() as folder:
            # MaaFramework keeps its log handle open until process exit on Windows.
            Toolkit.init_option(ROOT/'debug/bestdori-fixtures')
            resource, controller, tasker = Resource(), Images(), Tasker()
            self.assertTrue(resource.post_bundle(ROOT/'assets/resource').wait().succeeded)
            self.assertTrue(controller.post_connection().wait().succeeded)
            self.assertTrue(tasker.bind(resource, controller))
            errors = []
            owner = self
            class Check(CustomAction):
                def run(self, context, argv):
                    try:
                        catalog = load()
                        flow = BestdoriScan(context, catalog, {'bands': [1, 3], 'members': [1, 16], 'rarities': [1, 5]}, folder)
                        for name, expected in [('detail1', (1, 1, 20, 4)), ('detail5', (1703, 5, 60, 4)),
                                               ('detail5_rainbow', (1704, 5, 60, 4)),
                                               ('detail_misaki5', (1754, 5, 60, 4)),
                                               ('detail_rimi5', (2247, 5, 60, 0)),
                                               ('detail_kaoru5', (2430, 5, 60, 0)),
                                               ('detail_chisato5', (2431, 5, 60, 0))]:
                            flow.image = np.array(Image.open(ROOT/f'tests/fixtures/bestdori/{name}.png'))[:, :, ::-1].copy()
                            actual = (identify(flow.text([290, 156, 305, 34]), flow.text([290, 190, 305, 40]), flow.rarity(), catalog),
                                      flow.rarity(), read_ratio(flow.text([650, 350, 160, 40]))[0],
                                      read_ratio(flow.text([650, 478, 130, 40]), skill=True)[0]-1)
                            owner.assertEqual(actual, expected, name)
                        for name, value in [('master0', 0), ('master4', 4)]:
                            flow.image = np.array(Image.open(ROOT/f'tests/fixtures/bestdori/{name}.png'))[:, :, ::-1].copy()
                            actual = int(''.join(h.text for h in flow.ocr([920, 196, 30, 30], '.+', True)))
                            owner.assertEqual(actual, value, name)
                        for name,value in [('detail_master_zero_pink',0),('detail_master_one',1),('detail_master_two',2),('detail_master_four',4)]:
                            flow.image=np.array(Image.open(ROOT/f'tests/fixtures/bestdori/{name}.png'))[:,:,::-1].copy()
                            owner.assertEqual(flow.detail_master(),value,name)
                        flow.image = np.array(Image.open(ROOT/'tests/fixtures/bestdori/detail4_rainbow.png'))[:, :, ::-1].copy()
                        owner.assertEqual(flow.rarity(), 4)
                        owner.assertEqual(identify(flow.text([290, 156, 305, 34]), flow.text([290, 190, 305, 40]), flow.rarity(), catalog), 20)
                        flow.image = np.array(Image.open(ROOT/'tests/fixtures/bestdori/filter_characters.png'))[:, :, ::-1].copy()
                        rows = dict(character_filter_rows(flow.ocr([149,124,966,433]), catalog, (129,34,1021,653)))
                        owner.assertEqual(set(rows[3]), {11,12,13,14,15})
                        owner.assertEqual(set(rows[21]), {26,27,28,29,30})
                        flow.image = np.array(Image.open(ROOT/'tests/fixtures/bestdori/filter_single_name.png'))[:,:,::-1].copy()
                        band = next(h for h in flow.ocr([149,124,966,433]) if 'Pastel' in h.text)
                        cy = band.box[1]+band.box[3]/2
                        owner.assertEqual(''.join(h.text for h in flow.ocr([418,round(cy)+10,80,42],'.+',True)), '彩')
                        flow.image = np.array(Image.open(ROOT/'tests/fixtures/lowres/member_filter.png'))[:,:,::-1].copy()
                        label = flow.hit_text([149,134,180,423], '^种类$|^属性$')
                        owner.assertIsNotNone(label)
                        y = label.box[1]+label.box[3]//2+54
                        owner.assertTrue(all(flow.checkbox(x,y) for x in (184,383,582,781)))
                        return True
                    except Exception as exc:
                        errors.append(exc)
                        return False
            resource.register_custom_action('BD_Fixture', Check())
            job = tasker.post_task('BD_Fixture', {'BD_Fixture': {'action': 'Custom', 'custom_action': 'BD_Fixture'}}).wait()
            if errors:
                raise errors[0]
            self.assertTrue(job.succeeded)

    def test_incomplete_ocr_never_guesses_a_level(self):
        for value in ('60', 'Lv. 60 /', '5/3', 'O/4', '101/101'):
            with self.subTest(value=value), self.assertRaises(ValueError):
                read_ratio(value)
        self.assertEqual(read_ratio('Lv. 60 / 60'), (60, 60))

    def identity_flow(self):
        flow = BestdoriScan.__new__(BestdoriScan)
        flow.existing_ids = set()
        flow.catalog = load()
        flow.scope = {'bands': [1, 3], 'members': [3, 15], 'rarities': [5]}
        flow.maximize = dict.fromkeys(('level', 'skill', 'master'), False)
        flow.wait = flow.save_frame = flow.snap = flow.require_clear_notification_overlay = Mock()
        flow.text = lambda roi: {156: '放学后，雨天和好友', 190: '牛込里美',
                                350: '60/60', 478: '1/5'}[roi[1]]
        flow.detail_master = lambda: 0
        return flow

    @patch('bestdori_scan.time.sleep')
    def test_missing_stars_retry_until_two_valid_frames_agree(self, _):
        flow = self.identity_flow()
        flow.rarity = Mock(side_effect=[None, None, None, 5, None, 5, 5])
        row = flow.read_detail(1)
        self.assertEqual((row['id'], row['rarity'], row['level'], row['train']), (2247, 5, 60, 1))
        self.assertNotIn('warning', row)
        self.assertEqual(flow.rarity.call_count, 7)

    @patch('bestdori_scan.time.sleep')
    def test_persistently_missing_or_unstable_stars_preserve_unconfirmed_values(self, _):
        for readings in ([None]*7, [None, None, 5, 4, 5, 4, 5]):
            with self.subTest(readings=readings):
                flow = self.identity_flow()
                flow.rarity = Mock(side_effect=readings)
                row = flow.read_detail(1)
                self.assertIsNone(row['id'])
                self.assertIsNone(row['rarity'])
                self.assertEqual((row['level'], row['skill'], row['master']), (60, 0, 0))
                self.assertNotIn('train', row)
                self.assertIn('星级未读清', row['warning'])

    @patch('bestdori_scan.time.sleep')
    def test_member_alias_cannot_bypass_scan_scope(self, _):
        flow = self.identity_flow()
        flow.scope['members'] = [3]
        flow.text = lambda roi: '未知新卡名' if roi[1] == 156 else '奥泽美咲'
        flow.rarity = Mock(return_value=5)
        self.assertIsNone(flow.read_detail(1))

    def test_each_maximum_setting_skips_its_read_and_all_three_skip_tab_inputs(self):
        catalog = load()
        for flags in product((False, True), repeat=3):
            with self.subTest(flags=flags):
                flow = BestdoriScan.__new__(BestdoriScan)
                flow.catalog = catalog
                flow.existing_ids = set()
                flow.scope = {'bands': [1], 'members': [1], 'rarities': [5]}
                flow.maximize = dict(zip(('level', 'skill', 'master'), flags))
                flow.wait = flow.save_frame = Mock()
                flow.hit_text = Mock(return_value=SimpleNamespace(box=[700, 120, 50, 30]))
                flow.tap_hit = Mock()
                flow.rarity = Mock(return_value=5)
                flow.stable = lambda read: read()
                def text(roi):
                    if roi[1] == 156: return '开始打工了！'
                    if roi[1] == 190: return '户山香澄'
                    if roi[1] == 350:
                        self.assertFalse(flags[0], 'Maximum level must not be read')
                        return '60/60'
                    if roi[1] == 478:
                        self.assertFalse(flags[1], 'Maximum skill must not be read')
                        return '3/5'
                    raise AssertionError('Unexpected OCR ROI')
                flow.text = text
                def master():
                    self.assertFalse(flags[2], 'Maximum master rank must not be read')
                    return 2
                flow.detail_master = master
                row = flow.read_detail(1)
                self.assertEqual(row['id'], 1703)
                for field, flag in flow.maximize.items():
                    self.assertEqual(field in row, not flag)
                self.assertEqual('train' in row, not flags[0])
                flow.tap_hit.assert_not_called()

    def test_scroll_endpoint_ignores_border_animation_but_not_movement(self):
        flow = BestdoriScan.__new__(BestdoriScan)
        flow.image = np.array(Image.open(ROOT/'tests/fixtures/mining/selection_jitter_before.png'))[:, :, ::-1]
        before = flow.page_signature()
        flow.image = np.array(Image.open(ROOT/'tests/fixtures/mining/selection_jitter_after.png'))[:, :, ::-1]
        self.assertTrue(flow.same_page(before))
        shifted = [((point[0], point[1]+50), image) for point, image in before]
        self.assertFalse(flow.same_page(shifted))
        self.assertFalse(flow.same_page([]))

    def test_real_scrolling_overlap_last_row_and_bottom_thumb(self):
        flow = BestdoriScan.__new__(BestdoriScan)
        pages = []
        for name in ('scroll_overlap_before','scroll_overlap_after','scroll_bottom'):
            flow.image = np.array(Image.open(ROOT/f'tests/fixtures/bestdori/{name}.png'))[:,:,::-1].copy()
            pages.append(flow.page_signature())
            self.assertGreaterEqual(len(pages[-1]), 21)
        self.assertTrue(flow.list_bottom(flow.page_cards()))
        matches = [(p[0],p[1]-q[1]) for p,a in pages[0] for q,b in pages[1]
                   if p[0]==q[0] and 2<p[1]-q[1]<300 and same_page_portrait(a,b)]
        self.assertGreaterEqual(len({x for x,d in matches}),3)
        self.assertEqual(len(pages[0]),28)
        self.assertEqual(len(pages[1]),28)
        overlap=align_page(pages[0],pages[1],15,300)
        self.assertIsNotNone(overlap)
        self.assertGreaterEqual(len(overlap),7)

    def test_return_pixel_shift_and_animated_art_keep_exact_slot_order(self):
        flow=BestdoriScan.__new__(BestdoriScan)
        flow.image=np.array(Image.open(ROOT/'tests/fixtures/bestdori/return_before.png'))[:,:,::-1].copy()
        before=flow.page_signature()
        flow.image=np.array(Image.open(ROOT/'tests/fixtures/bestdori/return_shifted.png'))[:,:,::-1].copy()
        self.assertTrue(flow.same_page(before))
        self.assertEqual(align_page(before,flow.page_signature()),dict(enumerate(range(21))))
        switched=before.copy(); switched[:7],switched[7:14]=before[7:14],before[:7]
        # Reordered coordinates are not a valid same-order return.
        self.assertNotEqual(align_page(switched,flow.page_signature()),dict(enumerate(range(21))))

    def test_cancel_retains_records_and_reports_partial(self):
        flow = BestdoriScan.__new__(BestdoriScan)
        flow.existing_ids = set()
        flow.report = {'status': 'running', 'observations': [{'id': 1, 'level': 20}], 'errors': []}
        flow.open_list = Mock(side_effect=FlowError('已取消扫描'))
        flow.image = None
        flow.save_report = Mock()
        result = flow.run()
        self.assertEqual(result['status'], 'partial')
        self.assertEqual(result['observations'], [{'id': 1, 'level': 20}])
        self.assertIn('已取消扫描', result['errors'])

    def test_previous_story_and_animation_filters_are_cleared(self):
        flow = BestdoriScan.__new__(BestdoriScan)
        flow.member_filter_box = (129,34,1021,653)
        flow.report = {}
        flow.snap = flow.save_frame = Mock()
        flow.reco = Mock(return_value=True)
        flow.swipe = Mock()
        flow.hit_text = lambda roi,expected: SimpleNamespace(box=(180,200 if '小故事' in expected else 380,80,24))
        selected = set()
        flow.checkbox = lambda x,y,radio=False: (x,y) in selected
        flow.tap = lambda x,y: selected.add((x,y))
        flow.clear_extra_filters()
        self.assertEqual(selected,{(184,266),(184,446)})
        self.assertEqual(flow.report['extra_filters'],'all')
        flow.swipe.assert_not_called()

    def test_attribute_filter_applies_each_nonempty_combination_and_checks_clicks(self):
        for flags in product((False, True), repeat=4):
            if not any(flags):
                continue
            with self.subTest(flags=flags):
                flow = BestdoriScan.__new__(BestdoriScan)
                flow.scope = {'attributes': [kind for kind, flag in zip(ATTRIBUTES, flags) if flag]}
                flow.snap = flow.save_frame = Mock()
                selected = {184, 383, 582, 781}
                flow.checkbox = lambda x,y: x in selected
                flow.tap = lambda x,y: selected.symmetric_difference_update({x})
                flow.filter_attributes((184,383,582,781), 238)
                self.assertEqual(selected, {x for x, flag in zip((184,383,582,781), flags) if flag})
        flow.scope = {'attributes': ['cool']}
        flow.checkbox = Mock(return_value=True)
        flow.tap = Mock()
        with self.assertRaisesRegex(FlowError, '种类筛选'):
            flow.filter_attributes((184,383,582,781), 238)

    def fake_scan(self, pages, details, *, stuck=False, overlap=None):
        flow = BestdoriScan.__new__(BestdoriScan)
        flow.existing_ids = set()
        flow.portraits = None
        flow.catalog = load()
        flow.scope = {'bands': [1], 'members': [1], 'rarities': [1, 5]}
        flow.portraits_root = ROOT/'tests/fixtures/bestdori/portraits'
        flow.report = {'status':'running', 'observations':[], 'errors':[]}
        flow.image = None
        flow.open_list = flow.wait = flow.require_clear_notification_overlay = flow.check_stop = Mock()
        flow.save_frame = flow.save_report = flow.progress = flow.tap = flow.back = Mock()
        index=[0]
        flow.page_signature = lambda:[(p,None) for p in pages[index[0]]]
        flow.return_to_list = lambda before:before
        flow.open_card = lambda before,slot:before
        flow.same_page = Mock(return_value=True)
        flow.page_cards = Mock(side_effect=pages)
        flow.read_detail = Mock(side_effect=details)
        def advance():
            if stuck:raise FlowError('下滑无进展')
            index[0]+=1
            flow.overlap_slots=set(overlap or [])
            return index[0]<len(pages)
        flow.advance_page = Mock(side_effect=advance)
        return flow

    def test_confirmed_overlap_skips_detail_read_for_visited_slots(self):
        row = lambda id_: {'id':id_, 'level':20, 'skill':4, 'master':0, 'train':0}
        flow = self.fake_scan([[(344,260),(467,260)],[(344,260),(467,260)]], [row(1),row(2),row(3)],overlap=[0])
        result = flow.run()
        self.assertEqual(result['status'], 'complete')
        self.assertEqual([r['id'] for r in result['observations']], [1,2,3])
        self.assertEqual(flow.read_detail.call_count, 3)
        self.assertEqual(result['overlap_skipped'],1)

    def test_existing_thumbnails_skip_opening_but_new_and_uncertain_cards_open(self):
        flow = self.fake_scan([[(344,260),(467,260),(590,260)]], [{'id':1704}, {'id':1712}])
        flow.open_card = Mock(side_effect=lambda before, slot: before)
        flow.existing_ids = {1703, 1704}
        gallery = Mock()
        gallery.identify.side_effect = [1703, None, 1712]
        with patch('bestdori_scan.CardPortraits.prepare', return_value=gallery):
            report = flow.run()
        self.assertEqual(report['status'], 'complete')
        self.assertEqual(report['observations'], [{'id':1712}])
        self.assertEqual([call.args[1] for call in flow.open_card.call_args_list], [1, 2])
        self.assertEqual(report['detail_reads'], 2)
        self.assertEqual(report['summary'], {'existing_skipped':2, 'thumbnail_skipped':1})

    def test_pages_of_existing_cards_make_progress_and_still_reach_new_card(self):
        flow = self.fake_scan([[(344,260)]]*6, [{'id':1703}])
        flow.existing_ids = {1,2,3,4,5}
        gallery = Mock()
        gallery.identify.side_effect = [1,2,3,4,5,None]
        with patch('bestdori_scan.CardPortraits.prepare', return_value=gallery):
            report = flow.run()
        self.assertEqual(report['status'], 'complete')
        self.assertEqual(report['detail_reads'], 1)
        self.assertEqual(report['observations'], [{'id':1703}])
        self.assertEqual(report['summary']['existing_skipped'], 5)

    def test_all_existing_cards_complete_without_opening_any(self):
        flow = self.fake_scan([[(344,260),(467,260)]], [])
        flow.existing_ids = {1703,1704}
        gallery = Mock()
        gallery.identify.side_effect = [1703,1704]
        with patch('bestdori_scan.CardPortraits.prepare', return_value=gallery):
            report = flow.run()
        self.assertEqual(report['status'], 'complete')
        self.assertEqual(report['detail_reads'], 0)
        self.assertEqual(report['observations'], [])
        flow.read_detail.assert_not_called()

    @patch('bestdori_scan.time.sleep')
    def test_existing_detail_fallback_reads_identity_only(self, _):
        flow = self.identity_flow()
        flow.existing_ids = {2247}
        flow.rarity = Mock(return_value=5)
        original_text = flow.text
        flow.text = Mock(side_effect=original_text)
        flow.detail_master = Mock()
        row = flow.read_detail(1)
        self.assertEqual(row['id'], 2247)
        self.assertFalse(any(key in row for key in ('level','skill','master','train')))
        self.assertEqual({call.args[0][1] for call in flow.text.call_args_list}, {156,190})
        flow.detail_master.assert_not_called()

    def test_unchecked_skip_does_not_prepare_gallery_or_omit_records(self):
        flow = self.fake_scan([[(344,260)]], [{'id':1703, 'skill':3}])
        with patch('bestdori_scan.CardPortraits.prepare') as prepare:
            report = flow.run()
        prepare.assert_not_called()
        self.assertEqual(report['observations'], [{'id':1703, 'skill':3}])

    def test_stalled_scroll_and_repeated_pages_are_partial(self):
        flow = self.fake_scan([[(344,260)]], [{'id':1}], stuck=True)
        self.assertEqual(flow.run()['status'], 'partial')
        self.assertEqual(flow.report['observations'], [{'id':1}])
        flow = self.fake_scan([[(344,260)]]*6, [{'id':1}]*6)
        result = flow.run()
        self.assertEqual(result['status'], 'partial')
        self.assertLess(flow.read_detail.call_count, 6)
        self.assertIn('连续翻页没有新卡片', result['errors'][0])

    def test_repeated_identity_with_different_stats_needs_confirmation(self):
        flow = self.fake_scan([[(344,260),(467,260)]], [{'id':1,'skill':2},{'id':1,'skill':3}])
        row = flow.run()['observations'][0]
        self.assertTrue(row['conflict'])
        self.assertEqual(row['skill'], 2)

    def test_unchanged_swipes_need_visible_bottom_and_try_two_locations(self):
        flow = BestdoriScan.__new__(BestdoriScan)
        flow.page_signature = Mock(return_value=[((344,260),None)])
        flow.swipe = Mock(); flow.settle_list = flow.wait = flow.require_clear_notification_overlay = Mock()
        flow.same_page = Mock(return_value=True)
        flow.page_cards = Mock(return_value=[(344,260)])
        flow.list_bottom = Mock(return_value=False)
        flow.snap = Mock()
        with self.assertRaisesRegex(FlowError, '未确认到底'):
            flow.advance_page()
        self.assertEqual(flow.swipe.call_args_list[0].args, (1140,580,360))
        flow.list_bottom.return_value = True
        self.assertFalse(flow.advance_page())

    def test_scroll_jump_without_overlap_is_never_complete(self):
        flow = BestdoriScan.__new__(BestdoriScan)
        flow.page_signature = Mock(side_effect=[[((344,260),None)],[((467,260),None)]])
        flow.page_cards = Mock(return_value=[(344,260)])
        flow.list_bottom = Mock(return_value=False)
        flow.swipe = Mock(); flow.settle_list = flow.wait = flow.require_clear_notification_overlay = Mock()
        flow.same_page = Mock(return_value=False)
        with self.assertRaisesRegex(FlowError, '重叠卡片未能确认'):
            flow.advance_page()

    def test_ignored_card_tap_retries_only_on_confirmed_same_list(self):
        flow=BestdoriScan.__new__(BestdoriScan)
        before=[((344,260),None)]
        flow.report={};flow.tap=flow.snap=flow.require_clear_notification_overlay=Mock()
        flow.reco=Mock(side_effect=[True,True])
        flow.same_page=Mock(return_value=True)
        flow.page_signature=Mock(return_value=before)
        with patch('bestdori_scan.time.monotonic',side_effect=[0,3,4,4.5]):
            self.assertEqual(flow.open_card(before,0),before)
        self.assertEqual(flow.report['open_retries'],1)
        flow.tap=Mock();flow.reco=Mock(return_value=False)
        with patch('bestdori_scan.time.monotonic',side_effect=[0,3]):
            with self.assertRaisesRegex(FlowError,'页面发生变化'):flow.open_card(before,0)
        flow.tap.assert_called_once()


if __name__ == '__main__':
    unittest.main()
