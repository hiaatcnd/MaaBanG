"""Keep all persistent live settings in one explicit task."""
import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
REMOVED = {'谱面Fever印章', '演出Fever印章', '谱面每首火数', '每首消耗火数', '挖矿每首火数', '火不足策略'}


def update(interface):
    def prune(value):
        if isinstance(value, dict):
            for key, item in value.items():
                if key == 'option' and isinstance(item, list):
                    value[key] = [name for name in item if name not in REMOVED]
                else:
                    prune(item)
        elif isinstance(value, list):
            for item in value:
                prune(item)
    prune(interface)
    for name in REMOVED:
        interface['option'].pop(name, None)
    tasks = [task for task in interface['task'] if task['entry'] != 'LivePresets']
    tasks.insert(0, dict(name='演出预先设置', entry='LivePresets', default_check=False,
        description='单独运行一次，保存演出设定、Fever印章和火数。后续代打、自动演出与挖矿沿用游戏设置，不重复修改。需要更改时重新运行本任务。不开演，不消耗火或印章。',
        option=['预设演出设定', '预设Fever印章', '预设火数']))
    interface['task'] = tasks
    for task in tasks:
        if task['entry'] == 'ChartLive':
            task['description'] = task['description'].replace('自动调整速度为9.80、默认演出皮肤、轻量模式并关闭镜像。', '')
        if task['entry'] in ('ChartLive','AutoLive','MineFullCombo','MineChallenges'):
            task['description'] = task['description'].replace('可选难度、每首火数和火不足策略。','可选难度和火不足策略。')
            note = ' 演出前请先运行“演出预先设置”；火数与Fever沿用游戏已保存设置，开演前关闭MV、3D演出和3D Cut in。'
            if note not in task['description']:
                task['description'] += note
    def choice(label, key, value):
        return dict(name=label, pipeline_override={'LP_'+key:{'attach':{'value':value}}})
    interface['option'].update({
        '预设演出设定': dict(type='select', label='演出设定', default_case='代打推荐设定',
            description='速度9.80、音符大小100%、判定调节0、默认出现位置和皮肤、关闭镜像及色觉辅助、使用轻量模式。仅在此任务应用。',
            cases=[choice('代打推荐设定','stage',True),choice('保留当前演出设定','stage',False)]),
        '预设Fever印章': dict(type='select', label='使用Fever印章', default_case='关',
            description='开启受次数、印章和游戏条件限制；无法开启时记录原因并保持关闭。后续演出不重新尝试开启。',
            cases=[choice('关','fever','off'),choice('开','fever','on')]),
        '预设火数': dict(type='select', label='每首消耗火数', default_case='1火',
            description='保存游戏的LIVE BOOST消费档位，后续演出不改档。火不足时停止或按演出任务的选项使用回复道具；CP挑战仍使用独立CP档位。',
            cases=[choice(f'{i}火','fire',i) for i in range(4)])})
    return interface


def main():
    path = ROOT/'assets/interface.json'
    path.write_text(json.dumps(update(json.loads(path.read_text(encoding='utf8'))),ensure_ascii=False,indent=4)+'\n',encoding='utf8')
    nodes = {'LivePresets':{'action':'Custom','custom_action':'LivePresets'}}
    nodes.update({'LP_'+key:{'attach':{'value':value}} for key,value in dict(stage=True,fever='off',fire=1).items()})
    (ROOT/'assets/resource/pipeline/live_presets.json').write_text(json.dumps(nodes,ensure_ascii=False,indent=4)+'\n',encoding='utf8')

if __name__ == '__main__':
    main()
