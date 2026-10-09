"""Explicit metadata refresh task; never interacts with the game controller."""
from task_logging import log, failure
from pathlib import Path

from maa.custom_action import CustomAction

from catalog_update import playable, refresh_catalog


class UpdateSongCatalog(CustomAction):
    def run(self, context, argv):
        agent = Path(__file__).resolve().parent
        try:
            log('[更新歌曲列表] 正在下载歌曲和乐队资料……')
            catalog, recognition = refresh_catalog(
                agent/'data',
                check_stop=lambda: self.check_stop(context))
            from song_catalog import reload_catalog
            reload_catalog()
            log(f'[更新歌曲列表] 更新成功：国服可选 {len(playable(catalog["songs"]))} 首，'
                  f'识别资料 {len(recognition["songs"])} 首。歌曲列表已动态加载。', level='success')
            return True
        except Exception as exc:
            failure('更新歌曲列表', exc, context)
            return False

    @staticmethod
    def check_stop(context):
        if context.tasker.stopping:
            raise RuntimeError('任务已停止，未应用更新')
