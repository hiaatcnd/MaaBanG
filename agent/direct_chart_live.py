"""Play one user-selected chart directly from the final live preparation page."""
from task_logging import log
from dataclasses import asdict

from chart_live import ChartLive, ChartLiveFlow
from chart_policy import ChartOptions
from costume_unlock import FlowError

OPTION_DEFAULTS = dict(song1='306', difficulty1='expert', jitter='small')
OPTION_NODES = {key: 'DL_'+key for key in OPTION_DEFAULTS}


class DirectChartLiveFlow(ChartLiveFlow):
    def wait_ready(self, index=1, timeout=25):
        # The user owns song, difficulty and stage settings. Only inspect the
        # start button; never route through ready-page title/difficulty checks.
        self.snap()
        if not self.hit_text([1010,595,235,75], '^演出开始$'):
            raise FlowError('请停在点击“演出开始”即可打歌的最后准备页')

    def disable_mv(self):
        # Direct mode must not change the user's prepared screen or settings.
        pass

    def verify_chart_start(self, index, selection, amount):
        self.wait_ready(index)
        return None

    def prepare_round(self):
        log('[指定谱面直接演出] 正在检查当前准备页并读取指定谱面')
        self.wait_ready()
        selection=self.settings.selections[0]
        log(f'[指定谱面直接演出] 读取 {selection.song["title"]} / {selection.difficulty.upper()} 谱面（缺失时下载）')
        chart=self.store.get(selection, check_stop=self.check_stop)
        log('[指定谱面直接演出] 谱面已就绪')
        return (selection,), (chart,)

    def run(self):
        self.report['selection_source']='user'
        selections,charts=self.prepare_round()
        selection=selections[0]
        song={'index':1, **asdict(selection), 'status':'prepared'}
        row={'songs':[song], 'status':'running'}
        self.report['rounds'].append(row)
        log(f'[指定谱面直接演出] {selection.song["title"]} '
              f'{selection.difficulty.upper()}；沿用当前准备页设置')
        self.play_chart(1,selection,charts[0][1],None,song)
        # play_chart returns only after the worker confirms all chart input.
        # Direct mode finishes here without inspecting or advancing results.
        song['status']='input_complete'
        row['status']='finished'
        self.report.update(status='finished',completed_rounds=1)


class DirectChartLive(ChartLive):
    task_label = '指定谱面直接演出'
    option_defaults = OPTION_DEFAULTS
    option_nodes = OPTION_NODES
    report_directory = 'direct_chart_live'

    def create_flow(self, context, values, destination):
        # Ignore unrelated task settings, including old repeat/refill options.
        options=ChartOptions.parse({**{key:values[key] for key in OPTION_DEFAULTS},
                                   'mode':'free','max_rounds':1,'fire':0,'shortage':'stop'})
        return DirectChartLiveFlow(context,options,destination)
