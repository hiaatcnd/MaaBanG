"""Play one user-selected chart directly from the final live preparation page."""
from task_logging import log
from dataclasses import asdict

from chart_live import ChartLive, ChartLiveFlow
from chart_policy import ChartOptions
from costume_unlock import FlowError

OPTION_DEFAULTS = dict(song1='306', difficulty1='expert', jitter='small', avoid_full_combo=False)
OPTION_NODES = {key: 'DL_'+key for key in OPTION_DEFAULTS}


class DirectChartLiveFlow(ChartLiveFlow):
    def wait_ready(self, index=1, timeout=25):
        # The user owns song, difficulty and gameplay settings. Only inspect the
        # start button; never route through ready-page title/difficulty checks.
        self.snap()
        if self.hit_text([1010,595,235,75], '^演出开始$'):
            mode='click'
        elif self.hit_text([1010,580,225,105], '^准备(?:开始|完毕)[！!]?$'):
            mode='online_click'
        else:
            raise FlowError('请停在有“演出开始”或协力“准备开始 / 准备完毕”按钮的最后准备页')
        # Keep the worker's synchronization mode consistent with the page that
        # was inspected before chart loading and controller initialization.
        if getattr(self,'_start_mode',mode)!=mode:
            raise FlowError('最后准备页的开演方式已变化，请重新启动任务')
        self._start_mode=mode

    def playback_start_mode(self):
        return self._start_mode

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
              f'{selection.difficulty.upper()}；开演前统一关闭演出背景和 3D Cut in')
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
