import sys
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch, Mock
import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'agent'))
from costume_unlock import CostumeFlow, FlowError, scroll_displacement


class SimulatedFlow(CostumeFlow):
    def __init__(self, target, counts, results):
        context=SimpleNamespace(tasker=SimpleNamespace(controller=None,stopping=False))
        super().__init__(context,target,roster=[('poppin_party',['A','B'])])
        self.counts=iter(counts)
        self.results=iter(results)
        self.attempts=[]
        self.collect_after_unlock=Mock()
        self.return_home=Mock()

    def rating_select(self): pass
    def select_band(self, *args): pass
    def wait(self,*args): pass
    def tap(self,*args): pass
    def read_count(self, character): return next(self.counts)
    def unlock_one(self, character, current):
        self.attempts.append((character,current))
        return next(self.results)


class FlowTests(unittest.TestCase):
    def test_fast_mode_skips_completed_collection_without_grid_scan(self):
        flow=SimulatedFlow(999,[None,999],[])
        flow.count_owned_costumes=lambda *_: self.fail('fast mode must not scan the grid')
        flow.run()
        self.assertEqual(flow.attempts,[])
        self.assertEqual(flow.report['characters'][0]['status'],'collection_completed')
        self.assertIsNone(flow.report['characters'][0]['after'])

    def test_completed_collection_counts_clothing_and_hair(self):
        context=SimpleNamespace(tasker=SimpleNamespace(controller=None,stopping=False))
        flow=CostumeFlow(context,999)
        flow.goto_costumes=lambda *_: None
        flow.tap=lambda *_: None
        flow.wait=lambda *_: None
        flow.return_from_costumes=lambda *args,**kwargs: None
        flow.reopen_rating_character=lambda *_: None
        with patch.object(flow,'count_owned_grid',side_effect=[57,9]) as count:
            self.assertEqual(flow.count_owned_costumes('牛込里美'),66)
            self.assertEqual([call.args[0] for call in count.call_args_list],[999,942])
            self.assertFalse(flow.count_is_lower_bound)

    def purchase_flow(self, success):
        context=SimpleNamespace(tasker=SimpleNamespace(controller=None,stopping=False))
        flow=CostumeFlow(context,66)
        flow.goto_costumes=lambda *_: None
        flow.reset_costume_scroll=lambda: None
        flow.snap=lambda: None
        flow.tap=lambda *_: None
        flow.wait=lambda *_: None
        flow.text=lambda roi: '默认配色' if roi[1]==607 else f"测试服装{len(flow.report['purchases'])}"
        flow.stable_ratio=lambda roi: (2767,200) if roi[1]==459 else (3561864,10000)
        flow.return_from_costumes=lambda *_: None
        flow.read_count=lambda *_: self.fail('must not return to rating after each purchase')
        def reco(node):
            if node=='CU_Lock': return SimpleNamespace(filtered_results=[SimpleNamespace(box=[700,280,42,42])])
            if node=='CU_UnlockSuccess': return success
            return True
        flow.reco=reco
        flow.clicks=[]
        flow.click=lambda node: flow.clicks.append(node)
        flow.collect_after_unlock=Mock()
        flow.return_home=Mock()
        return flow

    @patch('costume_unlock.time.sleep')
    def test_success_popup_keeps_equipment_and_verifies_increment(self, _):
        flow=self.purchase_flow(True)
        self.assertEqual(flow.unlock_one('牛込里美',65),66)
        self.assertEqual(flow.clicks,['CU_UnlockButton','CU_ConfirmUnlock','CU_KeepCostume'])
        self.assertEqual(flow.report['purchases'][0]['status'],'success_confirmed')

    @patch('costume_unlock.time.sleep')
    def test_two_missing_costumes_use_one_visit_and_one_initial_read(self, _):
        flow=self.purchase_flow(True)
        flow.target=3
        flow.roster=[('poppin_party',['牛込里美'])]
        flow.rating_select=lambda: None
        flow.select_band=lambda *_: None
        flow.read_count=Mock(return_value=1)
        flow.goto_costumes=Mock()
        flow.reset_costume_scroll=Mock()
        flow.return_from_costumes=Mock()
        flow.run()
        flow.read_count.assert_called_once_with('牛込里美')
        flow.goto_costumes.assert_called_once_with('牛込里美')
        flow.reset_costume_scroll.assert_called_once()
        flow.return_from_costumes.assert_called_once_with('牛込里美')
        flow.collect_after_unlock.assert_called_once_with('牛込里美')
        flow.return_home.assert_called_once()
        self.assertEqual(flow.clicks.count('CU_ConfirmUnlock'),2)
        self.assertEqual(flow.report['characters'][0]['after'],3)

    @patch('costume_unlock.time.sleep')
    def test_batch_stops_when_next_purchase_lacks_materials(self, _):
        flow=self.purchase_flow(True)
        flow.target=68
        flow.roster=[('poppin_party',['牛込里美'])]
        flow.rating_select=lambda: None
        flow.select_band=lambda *_: None
        flow.read_count=Mock(return_value=65)
        flow.stable_ratio=Mock(side_effect=[(200,200),(20000,10000),(0,200),(10000,10000)])
        def returned(*_):
            flow.costume_character=None
        flow.return_from_costumes=Mock(side_effect=returned)
        flow.run()
        flow.read_count.assert_called_once()
        flow.return_from_costumes.assert_called_once()
        flow.collect_after_unlock.assert_called_once_with('牛込里美')
        flow.return_home.assert_called_once()
        self.assertEqual(flow.clicks.count('CU_ConfirmUnlock'),1)
        self.assertEqual(flow.report['characters'][0]['after'],66)
        self.assertEqual(flow.report['status'],'insufficient_kits')

    @patch('costume_unlock.time.sleep')
    def test_unknown_purchase_result_never_retries_payment(self, _):
        flow=self.purchase_flow(False)
        with self.assertRaisesRegex(FlowError,'结果未知'):
            flow.unlock_one('牛込里美',65)
        self.assertEqual(flow.clicks.count('CU_ConfirmUnlock'),1)
        self.assertEqual(flow.report['purchases'][0]['status'],'submitted')

    def test_skips_existing_target_and_unlocks_only_missing_amount(self):
        flow=SimulatedFlow(3,[4,1],[2,3])
        flow.run()
        self.assertEqual(flow.attempts,[('B',1),('B',2)])
        self.assertEqual(flow.report['characters'][1]['after'],3)

    def test_resource_shortage_stops_all_further_characters(self):
        for reason in ['insufficient_kits','insufficient_coins']:
            flow=SimulatedFlow(3,[1,1],[reason])
            flow.run()
            self.assertEqual(flow.attempts,[('A',1)])
            self.assertEqual(flow.report['status'],reason)

    def test_no_unlockable_items_does_not_loop_forever(self):
        flow=SimulatedFlow(3,[1,3],['no_unlockable_costumes'])
        flow.run()
        self.assertEqual(len(flow.attempts),1)
        self.assertEqual(flow.report['characters'][0]['status'],'no_unlockable_costumes')

    def test_zero_target_returns_home_without_opening_costumes(self):
        flow=SimulatedFlow(0,[],[])
        flow.rating_select=lambda: self.fail('should not navigate')
        flow.run()
        flow.return_home.assert_called_once()
        self.assertEqual(flow.attempts,[])

    def test_cancellation_is_honored_before_capture(self):
        flow=SimulatedFlow(3,[],[])
        flow.ctx.tasker.stopping=True
        with self.assertRaises(FlowError): flow.snap()

    def test_scroll_overlap_and_bottom_detection(self):
        rng=np.random.default_rng(42)
        full=rng.integers(0,256,(500,573,3),dtype=np.uint8)
        before=full[:285]
        after=full[100:385]
        self.assertEqual(scroll_displacement(before,after),100)
        self.assertEqual(scroll_displacement(after,after),0)
        with self.assertRaises(FlowError):
            scroll_displacement(before,255-before)


if __name__=='__main__': unittest.main()
