"""Guard for clients without MaaBanG's desktop exit dispatcher."""
from maa.custom_action import CustomAction
from task_logging import failure


class ExitGame(CustomAction):
    def run(self, context, argv):
        failure('退出', RuntimeError('退出任务需要新版 MaaBanG 桌面端执行，请更新主程序'), context)
        return False
