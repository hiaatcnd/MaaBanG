"""Explicit, standalone application of the game's persistent live settings."""
import json
from pathlib import Path
import time

from maa.custom_action import CustomAction
from chart_live import ChartLiveFlow
from chart_policy import ChartOptions
from costume_unlock import FlowError, normalized
from live_policy import number, fever_option

DEFAULTS = {'stage': True, 'fever': 'off', 'fire': 1}
NODES = {key: 'LP_'+key for key in DEFAULTS}


class LivePresetFlow(ChartLiveFlow):
    def __init__(self, context, output):
        super().__init__(context, ChartOptions.parse({}), output)

    def settings_row(self, pattern, x=190, width=895):
        for _ in range(10):
            self.snap()
            hit=self.hit_text([x,200,width,330],pattern)
            if hit:
                y=hit.box[1]+hit.box[3]//2+54
                if y<522:
                    return y
            self.swipe(1090,490,350)
        raise FlowError(f'未找到演出设置：{pattern}')

    def set_number(self, roi, target, minus, plus, scale=1, limit=100):
        for _ in range(limit):
            self.snap()
            text=normalized(self.text(roi)).replace('%','')
            try:
                current=round(float(text)*scale)
            except ValueError:
                raise FlowError(f'无法读取设置值：{text}')
            if current==target:
                return
            self.tap(*(plus if current<target else minus))
        raise FlowError('设置调整未收敛')

    def configure_stage(self):
        self.disable_mv()
        self.tap(951,650)
        self.tap(295,155)
        # Restore scroll position, then adjust the decimal speed with bounded feedback.
        for _ in range(3):
            self.swipe(1090,230,530)
        for _ in range(40):
            self.snap()
            value=normalized(self.text([365,288,110,55]))
            try:
                difference=980-round(float(value)*100)
            except ValueError:
                raise FlowError(f'无法读取音符速度：{value}')
            if difference==0:
                break
            step=100 if abs(difference)>=100 else 10 if abs(difference)>=10 else 1
            x=({100:635,10:574,1:513} if difference>0 else {100:206,10:267,1:328})[step]
            self.tap(x,312)
        else:
            raise FlowError('无法将音符速度调整至 9.80')
        self.tap(522,441)  # note size default 100%
        self.snap()
        if normalized(self.text([244,422,111,41]))!='100%':
            raise FlowError('音符大小未恢复为 100%')
        self.save_frame('settings_speed.png')
        y=self.settings_row('判定调节',190,400)
        self.set_number([244,y-24,110,46],0,(205,y),(391,y))
        self.set_number([707,y-24,110,46],0,(668,y),(851,y))
        y=self.settings_row('节奏图标的出现位置')
        self.tap(645,y)
        y=self.settings_row('镜像',795,290)
        self.tap(917,y)  # mirror OFF
        self.tap(610,y)  # color assistance OFF
        self.snap()
        if not all(self.pink(self.image[y-8:y+9,x-8:x+9]) for x in (917,610)):
            raise FlowError('镜像或色觉辅助未关闭')
        self.tap(527,155)
        for _ in range(3):
            self.swipe(1090,230,530)
        self.tap(303,317)  # 3D effects OFF
        self.tap(906,317)  # lightweight animation
        self.snap()
        if not self.pink(self.image[309:326,897:914]):
            raise FlowError('轻量模式未选中')
        self.tap(750,155)
        for _ in range(3):
            self.swipe(1090,230,530)
        self.tap(997,225)  # default skin restores the calibrated cyan/green lane layout
        self.tap(640,601)
        self.wait_ready()
        self.report['stage_settings']={'speed':9.8,'note_size':100,'mirror':False,
                                      'color_assist':False,'light_mode':True,'skin':'default'}

    def apply(self, *, stage=True, fever='off', fire=1):
        fire = number(fire, '每首火数', 3)
        fever = fever_option(fever)
        if not isinstance(stage, bool):
            raise ValueError('演出设定选项必须为布尔值')
        self.report['requested'] = dict(stage=stage, fever=fever, fire=fire)
        self.navigate_menu()
        if stage:
            self.open_page('LV_FreeEntry', 'LV_SongPage')
            self.reset_inherited_song_filters()
            self.tap(1070,648)
            self.wait_ready()
            self.configure_stage()
            self.navigate_menu()
        # Set fire first: the game's full-consumption mode forbids Fever.
        self.menu_fire(fire)
        if self.menu_fire() != fire:
            raise FlowError('火数设定未保存')
        self.report['saved_fire'] = fire
        self.configure_fever(fever)
        self.report['status'] = 'finished'
        self.home()


class LivePresets(CustomAction):
    def run(self, context, argv):
        output = Path('debug/live_presets')/time.strftime('%Y%m%d-%H%M%S')
        flow = LivePresetFlow(context, output)
        try:
            values = {key: (context.get_node_data(node) or {}).get('attach', {}).get('value', DEFAULTS[key])
                      for key, node in NODES.items()}
            flow.apply(**values)
            return True
        except Exception as exc:
            flow.report.update(status='error', error=str(exc))
            print(f'[演出预先设置] 已停止：{exc}', flush=True)
            if flow.image is not None:
                flow.save_frame('error.png')
            return False
        finally:
            (output/'report.json').write_text(json.dumps(flow.report, ensure_ascii=False, indent=2), encoding='utf8')
