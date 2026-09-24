"""Shared, bounded handling of informational game dialogs."""
import re
import time

import numpy as np

from costume_unlock import FlowError, normalized


NOTICE_TITLE = re.compile(
    r'^(?:区域解锁|(?:解锁.*故事)|(?:.*故事解锁)|'
    r'(?:获得|领取|已领取|达成)(?:奖励|报酬)(?:一览)?|'
    r'.+达成(?:报酬|奖励)一览|'
    r'获得(?:服装|新服装|成员|道具|称号|背景)|'
    r'舞台挑战达成(?:报酬|奖励)获得|'
    r'(?:等级提升|等级上升|玩家等级提升|升级)|首次.*奖励)$')
BUTTON = re.compile(r'^(?:确定|确认|关闭|OK)$', re.I)
TRANSACTION = re.compile(r'是否|取消|消耗|购买|花费|招募|交换|兑换|恢复体力|继续演出')


def dialog_box(image):
    """Find a centered white dialog using its top and uninterrupted side margins.

    No OpenCV dependency; positions are derived from the current 1280x720 frame.
    Header semantics and a single in-dialog button are checked separately.
    """
    if not isinstance(image, np.ndarray) or image.shape[:2] != (720, 1280):
        return None
    white = (image.min(axis=2) > 235) & (np.ptp(image, axis=2) < 18)
    for y in range(24, 241, 4):
        if not white[y, 640]:
            continue
        dark = np.flatnonzero(~white[y])
        left = dark[dark < 640]
        right = dark[dark > 640]
        x = int(left[-1]+1) if len(left) else 0
        end = int(right[0]) if len(right) else 1280
        width = end-x
        if not (65 <= x and end <= 1215 and 380 <= width <= 1150
                and abs((x+end)/2-640) < 65):
            continue
        sides = white[y:, x+16] & white[y:, end-17]
        gaps = np.flatnonzero(~sides)
        bottom = y+int(gaps[0]) if len(gaps) else 720
        if 180 <= bottom-y <= 660 and bottom < 690:
            # White result panels share the same geometry. A modal additionally
            # has the long pink separator under its title, inside this panel.
            header=image[y+15:min(y+135,bottom),x+12:end-12].astype(np.int16)
            b,g,r=(header[:,:,i] for i in range(3))
            pink=(r>200)&(g<145)&(b>70)&(r-g>65)
            if np.any(np.mean(pink,axis=1)>.65):
                return [x, y, width, bottom-y]
    return None


class NotificationMixin:
    def require_clear_notification_overlay(self):
        if dialog_box(getattr(self, 'image', None)) is not None:
            raise FlowError('存在未识别的弹窗，停止操作背景页面并保留现场')

    def dismiss_notifications(self):
        """Drain safe notifications, verifying progress before another click.

        Unknown/transaction dialogs remain owned by their task. A recognized
        notification without a unique button blocks background-page actions.
        """
        handled = False
        previous = None
        repeats = 0
        for _ in range(12):
            box = dialog_box(getattr(self, 'image', None))
            if box is None:
                return handled
            x,y,w,h = box
            hits = self.ocr([x+12,y+8,w-24,h-16])
            titles = [normalized(hit.text) for hit in hits
                      if hit.box[1] < y+min(115,h*.3)]
            title = next((t for t in titles if NOTICE_TITLE.fullmatch(t)), None)
            if title is None:
                return handled
            text = ' '.join(normalized(hit.text) for hit in hits)
            if TRANSACTION.search(text):
                raise FlowError(f'通知包含交易或选择内容，交由任务处理：{title}')
            buttons = [hit for hit in hits if BUTTON.fullmatch(normalized(hit.text))
                       and hit.box[1] > y+h*.55
                       and abs(hit.box[0]+hit.box[2]/2-(x+w/2)) < w*.23]
            if len(buttons) != 1:
                raise FlowError(f'通知弹窗未找到唯一确认按钮：{title}')
            fingerprint = (title, text)
            repeats = repeats+1 if fingerprint == previous else 0
            if repeats >= 3:
                raise FlowError(f'通知弹窗点击后未消失：{title}')
            previous = fingerprint
            self.tap_hit(buttons[0])
            handled = True
            print(f'[通知] 已确认：{title}', flush=True)
            # Observe after animation; every subsequent click uses fresh OCR.
            for _ in range(6):
                self.check_stop()
                time.sleep(.2)
            self.snap()
        raise FlowError('连续通知超过12次，保留现场')
