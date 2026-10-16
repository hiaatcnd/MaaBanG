"""The fast preparation path must never bypass fresh pre-input verification."""
import contextlib
import io
from pathlib import Path
import sys
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import Mock, patch

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT/'agent'))
from chart_policy import ChartOptions, ChartSelection
from costume_unlock import FlowError
from online_live import OnlineLiveFlow


class OnlinePreparationTests(unittest.TestCase):
    def test_fresh_final_page_failure_stops_armed_worker_without_authorizing_input(self):
        for mode in ('team','coop','versus'):
            with self.subTest(mode=mode),tempfile.TemporaryDirectory() as folder:
                flow=OnlineLiveFlow.__new__(OnlineLiveFlow)
                flow.settings=ChartOptions.parse({'mode':mode,'fire':0})
                flow.output=Path(folder)
                flow.report={'attempts':1,'completed_rounds':0}
                flow.controller=SimpleNamespace(info={'test':True})
                for name in ('check_stop','save_frame','pause','monitor_online_worker',
                             'submit_online_ready','fire_preview','fire_balance'):
                    setattr(flow,name,Mock())
                flow.reco=Mock(return_value=None)
                destination=flow.output/'attempt1_playback'
                observed=[]
                def capture():
                    self.assertTrue((destination/'armed').exists())
                    self.assertFalse((destination/'start').exists())
                    observed.append('fresh capture after armed')
                flow.snap=Mock(side_effect=capture)
                process=Mock()
                process.poll.return_value=None
                streams=[]
                def spawn(command,**kwargs):
                    (destination/'armed').write_text('ready')
                    streams.append(kwargs['stdout'])
                    return process
                row={}
                with patch('chart_live.subprocess.Popen',side_effect=spawn), \
                        contextlib.redirect_stdout(io.StringIO()):
                    with self.assertRaisesRegex(FlowError,'页面已离开'):
                        flow.play_chart(1,ChartSelection('306','expert'),{'duration':1},0,row)
                self.assertEqual(observed,['fresh capture after armed'])
                self.assertFalse((destination/'start').exists())
                self.assertEqual((destination/'stop').read_text(),'stop')
                process.wait.assert_called_once_with(timeout=8)
                process.terminate.assert_not_called()
                flow.submit_online_ready.assert_not_called()
                flow.fire_preview.assert_not_called()
                flow.save_frame.assert_not_called()
                self.assertNotEqual(row.get('status'),'submitted')
                self.assertTrue(streams[0].closed)


if __name__=='__main__':
    unittest.main()
