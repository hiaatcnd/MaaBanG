import ast
from pathlib import Path
import sys
from types import SimpleNamespace
import unittest
from unittest.mock import Mock

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT/'agent'))
from chart_worker import check_overlay


class ChartWorkerOverlayTests(unittest.TestCase):
    def test_recognition_has_no_action_delays_and_waits_for_completion(self):
        tasker=Mock()
        completed=SimpleNamespace(succeeded=True)
        tasker.post_task.return_value.wait.return_value=completed

        self.assertIs(check_overlay(tasker),completed)

        tasker.post_task.assert_called_once_with('ChartWorkerOverlayCheck',{
            'ChartWorkerOverlayCheck':{'action':'Custom','custom_action':'ChartWorkerOverlayCheck',
                                       'pre_delay':0,'post_delay':0}})
        tasker.post_task.return_value.wait.assert_called_once_with()

    def test_failed_wait_is_not_hidden(self):
        tasker=Mock()
        tasker.post_task.return_value.wait.side_effect=RuntimeError('check failed')
        with self.assertRaisesRegex(RuntimeError,'check failed'):
            check_overlay(tasker)

    def test_failed_recognition_task_stops_before_monitoring_can_be_handed_over(self):
        tasker=Mock()
        tasker.post_task.return_value.wait.return_value=SimpleNamespace(succeeded=False)
        with self.assertRaisesRegex(RuntimeError,'弹窗检查失败'):
            check_overlay(tasker)

    def test_all_start_and_playback_checks_use_the_synchronous_helper(self):
        # Inspect the hardware entrypoint without opening an ADB connection.
        tree=ast.parse((ROOT/'agent/chart_worker.py').read_text(encoding='utf8'))
        play=next(node for node in tree.body
                  if isinstance(node,ast.FunctionDef) and node.name=='play')
        calls=[node for node in ast.walk(play) if isinstance(node,ast.Call)]
        checks=[node for node in calls
                if isinstance(node.func,ast.Name) and node.func.id=='check_overlay']
        self.assertEqual(len(checks),3)  # Waiting members, pre-stage, active playback.
        for call in checks:
            self.assertEqual(len(call.args),1)
            self.assertIsInstance(call.args[0],ast.Name)
            self.assertEqual(call.args[0].id,'popup_tasker')
        direct_tasks=[node for node in calls
                      if isinstance(node.func,ast.Attribute) and node.func.attr=='post_task'
                      and isinstance(node.func.value,ast.Name)
                      and node.func.value.id=='popup_tasker']
        self.assertEqual(direct_tasks,[])


if __name__=='__main__':
    unittest.main()
