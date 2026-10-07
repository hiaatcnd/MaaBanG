"""Keep all persistent live settings in one explicit task."""
import json
from pathlib import Path
from interface_descriptions import OPTION_DESCRIPTIONS, TASK_DESCRIPTIONS, update as update_descriptions

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
        description=TASK_DESCRIPTIONS['LivePresets'],
        option=['预设演出设定', '预设Fever印章', '预设火数']))
    interface['task'] = tasks
    def choice(label, key, value):
        return dict(name=label, pipeline_override={'LP_'+key:{'attach':{'value':value}}})
    interface['option'].update({
        '预设演出设定': dict(type='select', label='演出设定', default_case='代打推荐设定',
            description=OPTION_DESCRIPTIONS['预设演出设定'],
            cases=[choice('代打推荐设定','stage',True),choice('保留当前演出设定','stage',False)]),
        '预设Fever印章': dict(type='select', label='使用Fever印章', default_case='关',
            description=OPTION_DESCRIPTIONS['预设Fever印章'],
            cases=[choice('关','fever','off'),choice('开','fever','on')]),
        '预设火数': dict(type='select', label='每首消耗火数', default_case='1火',
            description=OPTION_DESCRIPTIONS['预设火数'],
            cases=[choice(f'{i}火','fire',i) for i in range(4)])})
    return update_descriptions(interface)


def main():
    path = ROOT/'assets/interface.json'
    path.write_text(json.dumps(update(json.loads(path.read_text(encoding='utf8'))),ensure_ascii=False,indent=4)+'\n',encoding='utf8')
    nodes = {'LivePresets':{'action':'Custom','custom_action':'LivePresets'}}
    nodes.update({'LP_'+key:{'attach':{'value':value}} for key,value in dict(stage=True,fever='off',fire=1).items()})
    (ROOT/'assets/resource/pipeline/live_presets.json').write_text(json.dumps(nodes,ensure_ascii=False,indent=4)+'\n',encoding='utf8')

if __name__ == '__main__':
    main()
