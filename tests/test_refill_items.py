"""Drink identity must follow the visible card, never its list index."""
from pathlib import Path
import sys
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import Mock

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT/'agent'))
from chart_live import ChartLiveFlow
from chart_policy import ChartOptions


class RefillItemTests(unittest.TestCase):
    def make_flow(self, cards, balance=2, actual_gains=None):
        folder=tempfile.TemporaryDirectory()
        self.addCleanup(folder.cleanup)
        flow=ChartLiveFlow(SimpleNamespace(tasker=SimpleNamespace(controller=None,stopping=False)),
                           ChartOptions.parse({'shortage':'items'}),folder.name)
        state={'preview':balance}
        titles={'小型':'小型 live boost 饮料','普通':'live boost 饮料'}
        gains={'小型':1,'普通':10}
        hits=[];inventories={};buttons={}
        for name,y,inventory in cards:
            hits.append(SimpleNamespace(text=titles.get(name,name),box=[495,y-73,190,27]))
            inventories[(406,y+13,86,35)]=str(inventory)
            buttons[y]=gains.get(name,99)
        if actual_gains:
            buttons.update(actual_gains)
        flow.snap=Mock();flow.pause=Mock();flow.tap_hit=Mock()
        flow.hit_text=Mock(return_value=object())
        flow.ocr=Mock(side_effect=lambda roi,only_rec=False:[] if only_rec else hits)
        flow.fire_balance=Mock(side_effect=lambda:state['preview'])
        flow.read_integer=Mock(side_effect=lambda roi:balance if roi==[708,471,57,42] else state['preview'])
        def text(roi):
            if tuple(roi) in inventories:
                return inventories[tuple(roi)]
            if roi==[488,302,306,72]:
                return '将要回复LIVE BOOST。确认吗？'
            if roi==[440,325,410,53]:
                return f'LIVE BOOST已回复{state["preview"]-balance}！'
            raise AssertionError(f'Unexpected OCR region: {roi}')
        flow.text=Mock(side_effect=text)
        def tap(x,y):
            if x==766:
                state['preview']+=buttons[y]
        flow.tap=Mock(side_effect=tap)
        return flow

    def test_large_drink_in_first_row_restores_ten_with_one_click(self):
        flow=self.make_flow([('普通',227,'x436')])
        self.assertTrue(flow.refill_fire(5))
        self.assertEqual([call.args for call in flow.tap.call_args_list],[(1150,39),(766,227),(770,602)])
        self.assertEqual(flow.report['refills'][0]['items'],[
            {'item':'普通','gain':10,'count':1,'inventory_before':436}])
        self.assertEqual(flow.report['refills'][0]['actual_recovered'],10)
        self.assertEqual(flow.report['refills'][0]['status'],'confirmed')
        self.assertEqual(flow.text.call_args_list[0].args,([406,240,86,35],))

    def test_large_only_rounds_up_remaining_deficit(self):
        flow=self.make_flow([('普通',227,'×436')])
        self.assertTrue(flow.refill_fire(23))
        self.assertEqual(flow.report['refills'][0]['after'],32)
        self.assertEqual(flow.report['refills'][0]['items'][0]['count'],3)

    def test_small_stays_preferred_even_if_cards_are_reordered(self):
        for cards,expected_y in (([('小型',227,'x5747'),('普通',376,'x436')],227),
                                 ([('普通',227,'x436'),('小型',376,'x5747')],376)):
            with self.subTest(cards=cards):
                flow=self.make_flow(cards)
                self.assertTrue(flow.refill_fire(5))
                clicks=[call.args for call in flow.tap.call_args_list if call.args[0]==766]
                self.assertEqual(clicks,[(766,expected_y)]*3)
                self.assertEqual(flow.report['refills'][0]['after'],5)

    def test_small_shortage_uses_large_for_remaining_deficit(self):
        flow=self.make_flow([('小型',227,'x2'),('普通',376,'x436')])
        self.assertTrue(flow.refill_fire(9))
        self.assertEqual([(item['item'],item['count']) for item in flow.report['refills'][0]['items']],
                         [('小型',2),('普通',1)])
        self.assertEqual(flow.report['refills'][0]['after'],14)

    def test_zero_stock_card_is_skipped(self):
        flow=self.make_flow([('小型',227,'x0'),('普通',376,'x436')])
        self.assertTrue(flow.refill_fire(5))
        clicks=[call.args for call in flow.tap.call_args_list if call.args[0]==766]
        self.assertEqual(clicks,[(766,376)])

    def test_no_supported_drink_cancels_without_guessing(self):
        for cards in ([],[('周年live boost饮料',227,'x30')],[('普通',227,'x0')]):
            with self.subTest(cards=cards):
                flow=self.make_flow(cards)
                self.assertFalse(flow.refill_fire(5))
                self.assertEqual([call.args for call in flow.tap.call_args_list],[(1150,39),(506,602)])
                self.assertFalse(flow.report['refills'])

    def test_insufficient_combined_stock_never_submits(self):
        flow=self.make_flow([('小型',227,'x1'),('普通',376,'x1')])
        self.assertFalse(flow.refill_fire(20))
        self.assertNotIn((770,602),[call.args for call in flow.tap.call_args_list])
        self.assertFalse(flow.report['refills'])

    def test_ambiguous_or_unreadable_inventory_stops_before_selection(self):
        for cards in ([('普通',227,'x?')],[('普通',227,'x2'),('普通',376,'x3')]):
            with self.subTest(cards=cards):
                flow=self.make_flow(cards)
                with self.assertRaisesRegex(RuntimeError,'库存|位置不唯一'):
                    flow.refill_fire(5)
                self.assertEqual([call.args for call in flow.tap.call_args_list],[(1150,39)])

    def test_wrong_gain_is_detected_after_first_click_without_submit(self):
        flow=self.make_flow([('普通',227,'x436')],actual_gains={227:1})
        with self.assertRaisesRegex(RuntimeError,'预览与所选数量不符'):
            flow.refill_fire(23)
        self.assertEqual([call.args for call in flow.tap.call_args_list],[(1150,39),(766,227)])
        self.assertFalse(flow.report['refills'])

    def test_split_single_digit_inventory_uses_direct_recognition(self):
        flow=self.make_flow([('普通',227,'+')])
        original_ocr=flow.ocr
        flow.ocr=Mock(side_effect=lambda roi,only_rec=False:
                      [SimpleNamespace(text='x1',score=.99)] if only_rec else original_ocr(roi))
        self.assertTrue(flow.refill_fire(5))
        self.assertEqual(flow.report['refills'][0]['items'][0]['inventory_before'],1)
        flow.ocr.assert_any_call([406,247,83,22],only_rec=True)

    def test_uncertain_direct_inventory_recognition_never_selects_items(self):
        for text,score in [('x1',.5),('_x1',.99),('+',.99)]:
            with self.subTest(text=text,score=score):
                flow=self.make_flow([('普通',227,'+')])
                original_ocr=flow.ocr
                flow.ocr=Mock(side_effect=lambda roi,only_rec=False:
                              [SimpleNamespace(text=text,score=score)] if only_rec else original_ocr(roi))
                with self.assertRaisesRegex(RuntimeError,'库存'):
                    flow.refill_fire(5)
                self.assertEqual([call.args for call in flow.tap.call_args_list],[(1150,39)])

    def test_row_location_follows_title_at_nonstandard_offset(self):
        flow=self.make_flow([('普通',247,'x 436')])
        self.assertTrue(flow.refill_fire(5))
        self.assertIn((766,247),[call.args for call in flow.tap.call_args_list])

    def test_partly_clipped_card_is_not_clicked(self):
        flow=self.make_flow([('普通',435,'x436')])
        self.assertFalse(flow.refill_fire(5))
        flow.text.assert_not_called()


class RefillItemOCRTests(unittest.TestCase):
    def test_real_cards_keep_identity_when_large_moves_to_first_row(self):
        if not (ROOT/'assets/resource/model/ocr/rec.onnx').is_file():
            self.skipTest('Local OCR models required')
        import numpy as np
        from PIL import Image
        from maa.controller import CustomController
        from maa.custom_action import CustomAction
        from maa.resource import Resource
        from maa.tasker import Tasker

        frame=np.zeros((720,1280,3),dtype=np.uint8)
        class Screens(CustomController):
            def connect(self): return True
            def request_uuid(self): return 'refill-item-replay'
            def screencap(self): return frame
        controller=Screens()
        self.assertTrue(controller.post_connection().wait().succeeded)
        resource=Resource()
        self.assertTrue(resource.post_bundle(ROOT/'assets/resource').wait().succeeded)
        tasker=Tasker();tasker.bind(resource,controller)
        case=self;observed={}
        with tempfile.TemporaryDirectory() as folder:
            class Check(CustomAction):
                def run(self,context,argv):
                    try:
                        flow=ChartLiveFlow(context,ChartOptions.parse({'shortage':'items'}),folder)
                        flow.tap=Mock(side_effect=AssertionError('No device input during OCR replay'))
                        cases=[('both',[('小型',1,5747,227),('普通',10,436,376)]),
                               ('large_first',[('普通',10,436,226)]),
                               ('depleted_small',[('小型',1,0,227),('普通',10,1,376)])]
                        for filename,expected in cases:
                            crop=np.array(Image.open(ROOT/f'tests/fixtures/refill/{filename}.png').convert('RGB'))
                            frame[135:455,340:940]=crop[:,:,::-1]
                            flow.image=frame
                            items=flow.refill_items()
                            case.assertEqual([(i['item'],i['gain'],i['inventory_before']) for i in items],
                                             [row[:3] for row in expected],filename)
                            for item,row in zip(items,expected):
                                case.assertLessEqual(abs(item['y']-row[3]),2)
                        flow.tap.assert_not_called()
                        observed['complete']=True
                        return True
                    except Exception as error:
                        observed['error']=repr(error)
                        return False
            resource.register_custom_action('RefillItemCheck',Check())
            job=tasker.post_task('RefillItemCheck',{
                'RefillItemCheck':{'action':'Custom','custom_action':'RefillItemCheck'}}).wait()
            self.assertTrue(job.succeeded,observed)
            self.assertTrue(observed.get('complete'),observed)


if __name__=='__main__':
    unittest.main()
