import sys
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock, patch

import numpy as np

sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'agent'))
from notifications import NotificationMixin, dialog_box
from costume_unlock import FlowError


def panel(y=130, bottom=590):
    image=np.full((720,1280,3),90,dtype=np.uint8)
    image[y:bottom,333:947]=255
    image[y+60:y+63,365:915]=[140,50,255]
    return image


def hit(text,x,y,w=100,h=30):
    return SimpleNamespace(text=text,box=[x,y,w,h])


class NotificationTests(unittest.TestCase):
    @patch('notifications.time.sleep')
    def test_stage_reward_ticket_is_an_item_not_recruitment(self,_):
        button=hit('OK',606,521)
        f=self.flow([hit('舞台挑战达成报酬获得',400,160),hit('星石招募券',460,300),button])
        self.assertTrue(f.dismiss_notifications())
        f.tap_hit.assert_called_once_with(button)

    def test_stage_ticket_exception_does_not_allow_purchase_or_cancel(self):
        for body in ('是否消耗星石','购买星石招募券','取消','招募'):
            f=self.flow([hit('舞台挑战达成报酬获得',400,160),hit('星石招募券',460,300),
                         hit(body,440,350),hit('OK',606,521)])
            self.assertFalse(f.dismiss_notifications())
            f.tap_hit.assert_not_called()

    def flow(self,hits):
        f=NotificationMixin()
        f.image=panel()
        f.ocr=Mock(return_value=hits)
        f.tap_hit=Mock()
        f.check_stop=Mock()
        f.snap=Mock(side_effect=lambda:setattr(f,'image',np.zeros((720,1280,3),dtype=np.uint8)))
        return f

    def test_geometry_handles_varying_heights_and_rejects_plain_page(self):
        for y,bottom in ((34,686),(90,650),(110,608),(130,590),(170,550)):
            box=dialog_box(panel(y,bottom))
            self.assertIsNotNone(box)
            self.assertLessEqual(abs(box[1]-y),4)
            self.assertEqual(box[1]+box[3],bottom)
        self.assertIsNone(dialog_box(np.full((720,1280,3),255,dtype=np.uint8)))

    def test_real_result_panel_is_not_a_modal(self):
        from PIL import Image
        root=Path(__file__).parent/'fixtures'/'notifications'
        for name,expected in (('experience.png',False),('event_result.png',False),
                              ('reward.png',True),('area_unlock.png',True),('costume.png',True),
                              ('song_achievement.png',True)):
            with self.subTest(name=name):
                image=np.array(Image.open(root/name).convert('RGB'))[:,:,::-1].copy()
                self.assertEqual(dialog_box(image) is not None,expected)
                if not expected:
                    f=NotificationMixin(); f.image=image
                    f.require_clear_notification_overlay()

    @patch('notifications.time.sleep')
    def test_notify_uses_own_button_and_observes_next_frame(self,_):
        for title in ('区域解锁','新活动的未知提示','OCR ??? 完全无法读懂',''):
            for label in ('确定','确认','关闭','下一步','OK'):
                button=hit(label,606,521)
                f=self.flow([hit(title,400,160),button])
                self.assertTrue(f.dismiss_notifications())
                f.tap_hit.assert_called_once_with(button)
                f.snap.assert_called_once()

    def test_transaction_dialogs_are_not_confirmed(self):
        for title,body in (('购买确认','是否消耗星石'),('获得报酬','消耗100星石'),
                           ('获得报酬','取消'),('未知提示','购买100星石')):
            f=self.flow([hit(title,400,160),hit(body,410,300),hit('确定',606,521)])
            self.assertFalse(f.dismiss_notifications())
            f.tap_hit.assert_not_called()

    def test_missing_or_ambiguous_button_blocks_background(self):
        for buttons in ([],[hit('下一步',1060,645)], [hit('确认',900,521)]):
            f=self.flow([hit('区域解锁',400,160)]+buttons)
            self.assertFalse(f.dismiss_notifications())
            f.tap_hit.assert_not_called()
        f=self.flow([hit('确定',606,521),hit('关闭',610,550)])
        with self.assertRaises(FlowError): f.dismiss_notifications()
        f.tap_hit.assert_not_called()

    @patch('notifications.time.sleep')
    def test_stuck_dialog_has_bounded_retries(self,_):
        f=self.flow([hit('区域解锁',400,160),hit('确定',606,521)])
        f.snap=Mock()
        with self.assertRaisesRegex(FlowError,'未消失'): f.dismiss_notifications()
        self.assertEqual(f.tap_hit.call_count,3)

    @patch('notifications.time.sleep')
    def test_consecutive_different_notifications_are_drained(self,_):
        f=self.flow([])
        f.ocr.side_effect=[[hit('区域解锁',400,160),hit('确定',606,521)],
                           [hit('获得报酬',400,160),hit('确定',606,521)]]
        def advance():
            if f.tap_hit.call_count==2: f.image[:]=0
        f.snap=Mock(side_effect=advance)
        self.assertTrue(f.dismiss_notifications())
        self.assertEqual(f.tap_hit.call_count,2)


if __name__=='__main__': unittest.main()
