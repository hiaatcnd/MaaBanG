import json
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


def inheritance_notice():
    from PIL import Image
    root=Path(__file__).parent/'fixtures/notifications'
    frame=np.array(Image.open(root/'recruit_inheritance.png').convert('RGB'))[:,:,::-1].copy()
    hits=[SimpleNamespace(**row) for row in json.loads(
        (root/'recruit_inheritance.json').read_text(encoding='utf-8'))]
    return frame,hits


class NotificationTests(unittest.TestCase):
    @patch('notifications.time.sleep')
    def test_recorded_recruit_inheritance_result_is_acknowledged(self,_):
        frame,hits=inheritance_notice()
        f=self.flow(hits); f.image=frame
        self.assertTrue(f.dismiss_notifications())
        f.tap_hit.assert_called_once_with(next(h for h in hits if h.text=='确定'))
        f.snap.assert_called_once()
        f.require_clear_notification_overlay()

    def test_inheritance_requires_result_markers_and_own_button(self):
        for missing in ('继承','继承结果如下。','招募贴纸','确定'):
            with self.subTest(missing=missing):
                frame,hits=inheritance_notice()
                f=self.flow([h for h in hits if h.text!=missing]); f.image=frame
                self.assertFalse(f.dismiss_notifications())
                f.tap_hit.assert_not_called()
                with self.assertRaisesRegex(FlowError,'未识别的弹窗'):
                    f.require_clear_notification_overlay()

    def test_inheritance_result_does_not_allow_transaction_choices(self):
        for text in ('是否继承','取消','消耗2500星石','购买','花费','交换','兑换',
                     '恢复体力','继续演出','进行招募','确认吗','使用招募券','付费','有偿'):
            with self.subTest(text=text):
                frame,hits=inheritance_notice()
                f=self.flow(hits+[hit(text,440,450)]); f.image=frame
                self.assertFalse(f.dismiss_notifications())
                f.tap_hit.assert_not_called()

    @patch('notifications.time.sleep')
    def test_real_inheritance_result_ocr(self,_):
        root=Path(__file__).resolve().parents[1]
        if not (root/'assets/resource/model/ocr/rec.onnx').is_file():
            self.skipTest('Local OCR models required')
        from maa.controller import CustomController
        from maa.custom_action import CustomAction
        from maa.resource import Resource
        from maa.tasker import Tasker
        from daily_tasks import DailyFlow
        frame,_hits=inheritance_notice()
        class Controller(CustomController):
            def connect(self): return True
            def request_uuid(self): return 'inheritance-result-fixture'
            def screencap(self): return frame
        resource=Resource(); self.assertTrue(resource.post_bundle(root/'assets/resource').wait().succeeded)
        controller=Controller(); self.assertTrue(controller.post_connection().wait().succeeded)
        tasker=Tasker(); tasker.bind(resource,controller)
        case=self; errors=[]
        class Check(CustomAction):
            def run(self,ctx,argv):
                try:
                    f=DailyFlow(ctx); f.image=frame
                    f.tap=Mock()
                    f.snap=Mock(side_effect=lambda:setattr(f,'image',np.zeros_like(frame)))
                    case.assertTrue(f.dismiss_notifications())
                    f.tap.assert_called_once()
                    x,y=f.tap.call_args.args
                    case.assertTrue(522<=x<=758 and 569<=y<=635,(x,y))
                    f.require_clear_notification_overlay()
                    return True
                except Exception as exc:
                    errors.append(repr(exc)); return False
        resource.register_custom_action('InheritanceCheck',Check())
        self.assertTrue(tasker.post_task('InheritanceCheck',{'InheritanceCheck':{
            'action':'Custom','custom_action':'InheritanceCheck'}}).wait().succeeded,errors)

    def test_loading_comic_is_not_a_dialog(self):
        from PIL import Image
        path=Path(__file__).parent/'fixtures'/'coop'/'loading.png'
        image=np.array(Image.open(path).convert('RGB'))[:,:,::-1].copy()
        self.assertIsNone(dialog_box(image))

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
                              ('song_achievement.png',True),('song_story.png',True)):
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

    @patch('notifications.time.sleep')
    def test_song_story_is_deferred_without_reading_or_clicking_background(self,_):
        later=hit('稍后再读',440,610,140)
        f=self.flow([hit('阅读与这首歌曲相关的故事吧！',400,64,450),
                     later,hit('阅读',739,610),hit('确定',1020,630)])
        f.image=panel(36,688)
        self.assertTrue(f.dismiss_notifications())
        f.tap_hit.assert_called_once_with(later)
        f.snap.assert_called_once()
        f.require_clear_notification_overlay()

    def test_song_story_requires_title_and_unique_contained_reading_choices(self):
        title=hit('阅读与这首歌曲相关的故事吧！',400,64,450)
        later=hit('稍后再读',440,610,140)
        read=hit('阅读',739,610)
        for hits in ([later,read], [title,later], [title,read],
                     [title,later,later,read], [title,later,read,read],
                     [title,hit('稍后再读',970,610),read],
                     [title,later,hit('阅读',250,610)],
                     [title,hit('稍后再读',440,350),read],
                     [title,hit('稍后再读',739,610),hit('阅读',440,610)],
                     [title,later,read,hit('是否消耗星石',440,450)]):
            with self.subTest(text=[h.text for h in hits]):
                f=self.flow(hits+[hit('确定',1020,630)])
                f.image=panel(36,688)
                self.assertFalse(f.dismiss_notifications())
                f.tap_hit.assert_not_called()
                with self.assertRaisesRegex(FlowError,'未识别的弹窗'):
                    f.require_clear_notification_overlay()

    @patch('notifications.time.sleep')
    def test_song_story_stuck_after_click_stops_after_three_attempts(self,_):
        f=self.flow([hit('阅读与这首歌曲相关的故事吧!',400,64,450),
                     hit('稍后再读',440,610,140),hit('阅读',739,610)])
        f.image=panel(36,688)
        f.snap=Mock()
        with self.assertRaisesRegex(FlowError,'未消失'):
            f.dismiss_notifications()
        self.assertEqual(f.tap_hit.call_count,3)

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
