"""Expose an independent single-song task using the same CN chart selectors."""
from copy import deepcopy
import json
from pathlib import Path
from interface_descriptions import TASK_DESCRIPTIONS, update as update_descriptions

ROOT=Path(__file__).resolve().parents[1]


def update(interface):
    options=interface['option']
    for key in list(options):
        if key.startswith('直接演出'):
            del options[key]
    names=['谱面第1首歌曲','谱面随机偏差']
    names += [key for key in options if key.startswith('谱面第1首难度_')]
    mapping={key:key.replace('谱面第1首','直接演出').replace('谱面随机','直接演出随机') for key in names}
    def rewrite(value):
        if isinstance(value,dict):
            return {key.replace('CL_','DL_'):rewrite(item) for key,item in value.items()}
        if isinstance(value,list):
            return [rewrite(item) for item in value]
        if isinstance(value,str):
            return mapping.get(value,value)
        return value
    for name in names:
        item=rewrite(deepcopy(options[name]))
        item['label']=item['label'].replace('第1首','')
        options[mapping[name]]=item
    interface['task']=[t for t in interface['task'] if t['entry']!='DirectChartLive']
    index=next(i for i,t in enumerate(interface['task']) if t['entry']=='ChartLive')+1
    interface['task'].insert(index,dict(name='指定谱面直接演出',entry='DirectChartLive',default_check=False,
        description=TASK_DESCRIPTIONS['DirectChartLive'],
        option=[mapping['谱面第1首歌曲'],mapping['谱面随机偏差']]))
    return update_descriptions(interface)


def write_pipeline():
    # Keep generation usable without importing MaaFramework runtime modules.
    defaults=dict(song1='306',difficulty1='expert',jitter='small')
    nodes={'DirectChartLive':{'action':'Custom','custom_action':'DirectChartLive'}}
    nodes.update({'DL_'+key:{'attach':{'value':value}} for key,value in defaults.items()})
    (ROOT/'assets/resource/pipeline/direct_chart_live.json').write_text(
        json.dumps(nodes,ensure_ascii=False,indent=4)+'\n',encoding='utf8')
