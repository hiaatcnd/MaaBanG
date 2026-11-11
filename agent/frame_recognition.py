"""Reuse deterministic recognition results only within one captured live frame."""
import json


def _cache_key(name, node, override):
    """Exclude recognizers whose answer can depend on context beyond the image."""
    if not isinstance(node, dict) or 'recognition' in override:
        return None
    recognition = node.get('recognition')
    if not isinstance(recognition, dict) or recognition.get('type') not in ('OCR', 'TemplateMatch'):
        return None
    params = recognition.get('param')
    if not isinstance(params, dict):
        return None
    params = {**params, **override}
    roi = params.get('roi', [0, 0, 0, 0])
    # A node name / True can refer to a previous recognition's position.
    # OCR color_filter likewise names another recognition node.
    if (not isinstance(roi, (list, tuple)) or len(roi) != 4
            or not all(type(value) is int for value in roi)
            or params.get('color_filter')):
        return None
    try:
        # Include the current definition, not just call-site overrides: task
        # configuration may change a threshold, expected text or enabled state.
        return name, json.dumps([node, override], sort_keys=True, ensure_ascii=False, allow_nan=False)
    except (TypeError, ValueError):
        return None


class FrameRecognitionMixin:
    """Memoize reads, never observations across screenshots or input.

    The controller may reuse its ndarray buffer, so snap always invalidates even
    if object identity stays the same. Frames are read-only between captures.
    A second stability sample must still call snap; cached hits are not samples.
    Direct controller input must call invalidate_frame_recognition beforehand.
    """

    def invalidate_frame_recognition(self):
        self._recognition_image = None
        self._frame_recognitions = {}

    def snap(self):
        # Clear before capture so a failed/stopped capture cannot return a cached
        # answer from the preceding frame if the caller handles that failure.
        self.invalidate_frame_recognition()
        return super().snap()

    def tap(self, x, y):
        self.invalidate_frame_recognition()
        return super().tap(x, y)

    def swipe(self, x, y1, y2):
        self.invalidate_frame_recognition()
        return super().swipe(x, y1, y2)

    def reco(self, name, **override):
        # A cache hit must remain interruptible, just like a real recognition.
        self.check_stop()
        frame = self.image
        if frame is not getattr(self, '_recognition_image', None):
            self.invalidate_frame_recognition()
            self._recognition_image = frame
        key = None
        if frame is not None:
            key = _cache_key(name, self.ctx.get_node_data(name), override)
        if key is not None and key in self._frame_recognitions:
            return self._frame_recognitions[key]

        result = self.ctx.run_recognition(name, frame, {name: override} if override else None)
        hit = result if result is not None and result.hit else None
        # None means recognition could not start; it is not a confirmed miss.
        # Exceptions also escape without poisoning subsequent attempts.
        if key is not None and result is not None:
            self._frame_recognitions[key] = hit
        return hit
