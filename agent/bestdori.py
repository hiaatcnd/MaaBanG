"""Game-reading action used by the independent Bestdori tool page."""
import json
from maa.custom_action import CustomAction
from task_logging import log, failure


class BestdoriProfiles(CustomAction):
    def run(self, context, argv):
        try:
            from bestdori_desktop import run_scan
            options = json.loads(argv.custom_action_param)
            log('[Bestdori] 正在读取游戏卡牌，结果将返回工具页。')
            report = run_scan(context, options.get('scan_id'))
            complete = report['status'] == 'complete'
            log('[Bestdori] ' + ('扫描完成，请在工具页检查合并预览。' if complete else
                                 '扫描未全部完成，已保留读到的记录，请在工具页检查。'),
                level='success' if complete else 'warn')
            return complete
        except Exception as exc:
            failure('Bestdori 档案', exc, context)
            return False
