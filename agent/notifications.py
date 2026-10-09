"""Shared, bounded handling of informational game dialogs."""
from task_logging import log
import re
import time

import numpy as np

from costume_unlock import FlowError, normalized


BUTTON = re.compile(r'^(?:确定|确认|关闭|下一步|OK)$', re.I)
STORY_PROMPT = re.compile(r'^阅读与这首歌曲相关的故事吧[!！]?$')
# Recruitment tickets can be reward items. Actual purchases/choices remain
# owned by the calling task, even if their affirmative button says "OK".
TRANSACTION = re.compile(r'是否|取消|消耗|购买|花费|招募(?!券)|交换|兑换|恢复体力|继续演出')


def dialog_box(image):
    """Find a centered white dialog using its top and uninterrupted side margins.

    No OpenCV dependency; positions are derived from the current 1280x720 frame.
    A single in-dialog button and absence of transaction choices are checked separately.
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
            # Side decorations can end the detected panel inside a loading
            # illustration. Require an actual white bottom margin as well.
            if white[bottom-8:bottom,x+24:end-24].mean()<.75:
                continue
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

        Generic confirmations do not require a title allowlist. Story prompts
        additionally require their title and both reading choices. Transaction
        dialogs remain owned by their task. Missing buttons may still be
        animating; callers must keep the overlay in front of background actions.
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
            title = ' '.join(titles) or '提示'
            text = ' '.join(normalized(hit.text) for hit in hits)
            if TRANSACTION.search(text):
                return handled
            footer = [hit for hit in hits if hit.box[1] > y+h*.55
                       and x <= hit.box[0] and hit.box[0]+hit.box[2] <= x+w
                       and hit.box[1]+hit.box[3] <= y+h]
            if STORY_PROMPT.fullmatch(normalized(title)):
                # Defer this specific two-choice prompt; never enter its story
                # or click the still-visible result confirmation behind it.
                later = [hit for hit in footer if normalized(hit.text) == '稍后再读']
                read = [hit for hit in footer if normalized(hit.text) == '阅读']
                if len(later) != 1 or len(read) != 1 or later[0].box[0]+later[0].box[2] > read[0].box[0]:
                    return handled
                buttons = later
            else:
                buttons = [hit for hit in footer if BUTTON.fullmatch(normalized(hit.text))]
            if not buttons:
                return handled
            if len(buttons) != 1:
                raise FlowError(f'通知弹窗未找到唯一确认按钮：{title}')
            fingerprint = (title, text)
            repeats = repeats+1 if fingerprint == previous else 0
            if repeats >= 3:
                raise FlowError(f'通知弹窗点击后未消失：{title}')
            previous = fingerprint
            self.tap_hit(buttons[0])
            handled = True
            action = '已选择稍后再读' if normalized(buttons[0].text) == '稍后再读' else '已确认'
            log(f'[通知] {action}：{title}')
            # Observe after animation; every subsequent click uses fresh OCR.
            for _ in range(6):
                self.check_stop()
                time.sleep(.2)
            self.snap()
        raise FlowError('连续通知超过12次，保留现场')
