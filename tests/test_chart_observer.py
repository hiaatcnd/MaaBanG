from pathlib import Path
import sys
import unittest
from unittest.mock import Mock

sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'agent'))
from chart_worker import capture_observer_frame


class ChartObserverTests(unittest.TestCase):
    def observer(self, succeeded):
        observer=Mock()
        observer.post_screencap.return_value.wait.return_value.succeeded=succeeded
        observer.cached_image=object()
        return observer

    def test_success_returns_the_captured_frame_for_both_modes(self):
        for online in (True,False):
            with self.subTest(online=online):
                observer=self.observer(True)
                self.assertIs(capture_observer_frame(observer,online),observer.cached_image)
                observer.post_screencap.assert_called_once_with()
                observer.post_screencap.return_value.wait.assert_called_once_with()

    def test_online_failure_raises_instead_of_leaving_input_unmonitored(self):
        observer=self.observer(False)
        with self.assertRaisesRegex(RuntimeError,'监视截图失败'):
            capture_observer_frame(observer,True)

    def test_offline_failure_still_allows_retry_without_reusing_stale_frame(self):
        observer=self.observer(False)
        self.assertIsNone(capture_observer_frame(observer,False))


if __name__=='__main__':
    unittest.main()
