import sys
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock, patch
from PIL import Image
import numpy as np
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'agent'))
from mining_live import ChallengeMiningFlow
from mining_policy import MiningOptions,challenge_pending,challenge_star_count,challenge_row_stars

class ChallengeSelectionTests(unittest.TestCase):
    def test_unchanged_scroll_image_with_unread_rows_is_not_end_of_list(self):
        f=self.make_flow('not_full_stars')
        rows=f.ocr.return_value
        f.ocr.side_effect=[[rows[0]]]*3+[[rows[1]]]*3
        with patch('mining_policy.challenge_row_stars',
                   side_effect=lambda im,box:3 if box[1]==100 else 2):
            self.assertEqual(f.select_pending_stage(set()),2)
        f.swipe.assert_called_once()
        f.tap_hit.assert_called_once()

    def test_real_list_stars_include_selected_gray_gold_and_white_edges(self):
        folder=Path(__file__).parent/'fixtures/challenge_selection'
        image=np.array(Image.open(folder/'afterglow.png'))[:,:,::-1]
        for box,stars in [([48,190,81,39],0),([53,374,81,39],3),
                          ([54,98,80,37],0),([54,284,80,36],3),([54,468,81,36],3)]:
            self.assertEqual(challenge_row_stars(image,box),stars)
        image=np.array(Image.open(folder/'roselia.png'))[:,:,::-1]
        self.assertEqual(challenge_row_stars(image,[50,192,88,34]),2)
        self.assertEqual(challenge_row_stars(image,[50,284,88,34]),3)
        self.assertIsNone(challenge_row_stars(image,[50,550,88,34]))
        image[:]=255
        self.assertIsNone(challenge_row_stars(image,[50,192,88,34]))

    def test_batch_skip_only_opens_candidate_for_detail_confirmation(self):
        for target,expected in [('uncleared',3),('not_full_stars',2)]:
            f=self.make_flow(target)
            with patch('mining_policy.challenge_row_stars',
                       side_effect=lambda im,box:{100:3,190:2,280:0,370:0}[box[1]]):
                self.assertEqual(f.select_pending_stage(set()),expected)
            self.assertEqual(f.tap_hit.call_count,1)
            self.assertEqual(f.report['stage_scan'][-1]['source'],'detail')
            self.assertTrue(all(r['source']=='list' for r in f.report['stage_scan'][:-1]))

    def make_flow(self,target):
        f=ChallengeMiningFlow.__new__(ChallengeMiningFlow)
        f.mining=MiningOptions.parse({'challenge_target':target});f.report={}
        f.image=np.zeros((720,1280,3),dtype=np.uint8)
        f.wait=Mock();f.snap=Mock();f.swipe=Mock()
        rows=[SimpleNamespace(box=[50,100+i*90,80,30],text=f'舞台{i+1}') for i in range(4)]
        f.ocr=Mock(return_value=rows)
        f.reco=Mock(side_effect=lambda n,**kw:kw['roi'][1]>350)
        def tap(hit):
            f.current=int(hit.text[2:])
            stars={1:3,2:2,3:0}[f.current]
            for i,x in enumerate((373,435,498)):
                f.image[464:479,x-7:x+8]=(20,200,250) if i<stars else (185,185,185)
        f.tap_hit=Mock(side_effect=tap)
        f.selected_level=lambda:f.current
        return f

    def test_production_selector_skips_cleared_or_only_full_stars(self):
        for target,expected in [('uncleared',3),('not_full_stars',2)]:
            f=self.make_flow(target);attempted=set()
            self.assertEqual(f.select_pending_stage(attempted),expected)
            self.assertEqual(attempted,set(range(1,expected)))
            attempted.add(expected)
            if expected==2:
                self.assertEqual(f.select_pending_stage(attempted),3)
                attempted.add(3)
            self.assertIsNone(f.select_pending_stage(attempted))
            self.assertEqual([int(c.args[0].text[2:]) for c in f.tap_hit.call_args_list],[1,2,3])

    def test_real_ocr_order_is_reordered_top_to_bottom(self):
        # Native failure log: Roselia x=266 preceded Afterglow x=270.
        rows=[(266,494,'83 / 90'),(267,218,'90 / 90'),(267,586,'24 / 90'),
              (269,403,'90 / 90'),(270,310,'21 / 90')]
        f=ChallengeMiningFlow.__new__(ChallengeMiningFlow)
        f.image=np.zeros((720,1280,3),dtype=np.uint8)
        f.ocr=Mock(return_value=[SimpleNamespace(box=[x,y,68,30],text=t) for x,y,t in rows])
        cards=list(f.challenge_cards())
        self.assertEqual([n for y,s,n,m in cards],[90,21,90,83,24])
        self.assertEqual(next(n for y,s,n,m in cards if n<m),21)

    def test_targets_distinguish_clear_from_full_stars(self):
        self.assertEqual([challenge_pending(s,'uncleared') for s in (None,0,1,2,3)],
                         [False,True,False,False,False])
        self.assertEqual([challenge_pending(s,'not_full_stars') for s in (None,0,1,2,3)],
                         [False,True,True,True,False])
        self.assertEqual(MiningOptions.parse({}).challenge_target,'uncleared')
        with self.assertRaises(ValueError):MiningOptions.parse({'challenge_target':'all'})

    def test_unknown_stars_do_not_become_unplayed(self):
        image=np.full((720,1280,3),255,dtype=np.uint8)
        self.assertIsNone(challenge_star_count(image))
        for stars in range(4):
            for i,x in enumerate((373,435,498)):
                image[464:479,x-7:x+8]=(20,200,250) if i<stars else (185,185,185)
            self.assertEqual(challenge_star_count(image),stars)

if __name__=='__main__':unittest.main()
