from pathlib import Path
import sys
from types import SimpleNamespace
import unittest
from unittest.mock import Mock,patch
import numpy as np

sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'agent'))
from mining_live import MiningLiveFlow
from costume_unlock import FlowError


def frame(row,selected=False,ss_selected=False):
    image=np.full((720,1280,3),255,dtype=np.uint8)
    image[row-10:row+10,735:810]=0
    if selected:image[row-8:row+9,703:720]=(80,60,240)
    if ss_selected:image[row+40:row+57,703:720]=(80,60,240)
    return image


class MiningFilterTests(unittest.TestCase):
    def flow(self):
        f=MiningLiveFlow.__new__(MiningLiveFlow)
        f.image=frame(329);f.tap=Mock();f.snap=Mock();f.wait_filter_stable=Mock()
        return f

    def test_scroll_during_ocr_discards_old_coordinate(self):
        f=self.flow()
        f.hit_text=Mock(side_effect=[SimpleNamespace(box=[731,319,76,21]),
                                    SimpleNamespace(box=[731,259,76,21]),
                                    SimpleNamespace(box=[731,259,76,21])])
        frames=iter([frame(269),frame(269),frame(269,True)])
        f.snap=lambda:setattr(f,'image',next(frames))
        self.assertTrue(f.select_pending_filter())
        f.tap.assert_called_once_with(711,269)

    def test_wrong_ss_selection_does_not_pass_post_click_check(self):
        f=self.flow();f.image=frame(269,ss_selected=True)
        f.hit_text=Mock(return_value=SimpleNamespace(box=[731,259,76,21]))
        with self.assertRaisesRegex(FlowError,'未确认未 FULL COMBO'):
            f.select_pending_filter()
        self.assertEqual(f.tap.call_count,3)

    def test_already_selected_does_not_toggle_or_click(self):
        f=self.flow();f.image=frame(269,True)
        f.hit_text=Mock(return_value=SimpleNamespace(box=[731,259,76,21]))
        self.assertTrue(f.select_pending_filter());f.tap.assert_not_called()

    def test_unseen_option_returns_for_further_scroll(self):
        f=self.flow();f.hit_text=Mock(return_value=None)
        self.assertFalse(f.select_pending_filter());f.tap.assert_not_called()

    def test_stability_ignores_background_but_detects_moving_panel(self):
        before=frame(329);after=before.copy();after[:,:650]=0
        self.assertTrue(MiningLiveFlow.filter_frame_matches(before,after))
        self.assertFalse(MiningLiveFlow.filter_frame_matches(before,frame(269)))

    def test_continuous_motion_times_out_without_input(self):
        f=self.flow();del f.wait_filter_stable
        clock=[0.];rows=iter([269,329]*30)
        f.pause=lambda seconds:clock.__setitem__(0,clock[0]+seconds)
        f.snap=lambda:setattr(f,'image',frame(next(rows)))
        with patch('mining_live.time.monotonic',side_effect=lambda:clock[0]):
            with self.assertRaisesRegex(FlowError,'未稳定'):f.wait_filter_stable(timeout=1)
        f.tap.assert_not_called()


if __name__=='__main__':unittest.main()
