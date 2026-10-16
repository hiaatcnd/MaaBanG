"""Shared ready-page controls for MV/3D backgrounds and 3D Cut in."""
from dataclasses import dataclass
import re

import numpy as np

from costume_unlock import FlowError, normalized


@dataclass(frozen=True)
class LiveEffectsState:
    background: str | None
    cut_in: bool | None


class LiveEffectsMixin:
    def _live_effects_state(self):
        """Read actual controls, including layouts that replace Cut in with members."""
        if self.image is None or self.image.shape[:2] != (720, 1280):
            raise FlowError('无法读取演出背景设置：需要准备页截图')
        # One small OCR pass covers the selector and Cut in label. In 3D mode,
        # x343 is the default-members checkbox and x500 is a portrait, not Cut in.
        hits = self.ocr([110, 615, 610, 75])
        selector = []
        cut_in_labels = []
        for hit in sorted(hits, key=lambda hit: (hit.box[0], hit.box[1])):
            x, y, w, h = hit.box
            text = normalized(hit.text).upper()
            if not (615 <= y + h / 2 <= 690):
                continue
            if 175 <= x < 460 and not re.search('默认|乐队成员|进行演出', text):
                selector.append(text)
            if 470 <= x < 710:
                cut_in_labels.append(text)
        label = ''.join(selector)
        if label == 'OFF':
            background = 'off'
        elif 'OFF' not in label and re.search(r'[23]D|MV|^ON$|^演奏$', label):
            background = 'on'
        elif not label and np.mean(np.all(self.image[620:680, 110:320] > 245, axis=2)) > .98:
            # No playable MV is a property of this song/page, not the live mode.
            # Blank OCR alone cannot establish that the selector is absent.
            ready = ''.join(normalized(hit.text) for hit in self.ocr([1010, 590, 245, 85]))
            if not re.search(r'演出开始|准备(?:完毕|开始)|第[123]曲开始', ready):
                raise FlowError('无法确认演出准备页，不跳过背景开关')
            background = None
        else:
            raise FlowError(f'无法确认 MV/3D 演奏开关：{label!r}')

        cut_in = None
        if 'CUTIN' in ''.join(cut_in_labels):
            area = self.image[637:663, 487:513].astype(float)
            b, g, r = (area[:, :, i] for i in range(3))
            pink = np.mean((r > 180) & (r - g > 60) & (g < 170)) > .45
            grey = np.mean((area.max(axis=2) - area.min(axis=2) < 18) &
                           (area.mean(axis=2) > 75) & (area.mean(axis=2) < 200)) > .45
            if pink:
                cut_in = True
            elif grey:
                cut_in = False
            else:
                raise FlowError('无法确认 3D Cut in 开关状态')
        elif background != 'on':
            raise FlowError('无法确认 3D Cut in 开关位置')
        return LiveEffectsState(background, cut_in)

    def disable_mv(self, *, fresh=True):
        """Turn off both effects and verify on the resulting frame before starting."""
        if fresh:
            self.snap()
        selector_taps = 0
        cut_in_taps = 0
        while True:
            state = self._live_effects_state()
            if state.background == 'on':
                if selector_taps >= 4:
                    raise FlowError('未能关闭 MV/3D 演奏')
                point = (145, 650)
                selector_taps += 1
            elif state.cut_in:
                if cut_in_taps:
                    raise FlowError('未能关闭 3D Cut in')
                point = (500, 650)
                cut_in_taps += 1
            else:
                return state
            # Online flows provide a short click wait; other flows retain their
            # existing input timing. Only changed controls require a new frame.
            tap = getattr(self, 'quick_tap', None) or self.tap
            tap(*point)
            self.snap()
