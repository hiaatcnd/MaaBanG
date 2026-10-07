"""Expose an independent single-song task using the same CN chart selectors."""
from copy import deepcopy
import json
from pathlib import Path

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
        description='先在游戏中选好歌曲和难度，停在点击“演出开始”即可打歌的最后准备页，再在本任务指定同一首歌和难度。下载并校验对应谱面后直接开演，不识别歌曲、不选歌、不修改设置、不补火，每次只打一首，谱面输入完成即结束任务，不等待或操作结算页。请提前应用代打推荐设定，关闭游戏内置自动、MV及3D演出。消耗沿用当前页面设置。需要截图增强与MaaTouch，16:9横屏。',
        option=[mapping['谱面第1首歌曲'],mapping['谱面随机偏差']]))
    return interface


def write_pipeline():
    # Keep generation usable without importing MaaFramework runtime modules.
    defaults=dict(song1='306',difficulty1='expert',jitter='small')
    nodes={'DirectChartLive':{'action':'Custom','custom_action':'DirectChartLive'}}
    nodes.update({'DL_'+key:{'attach':{'value':value}} for key,value in defaults.items()})
    (ROOT/'assets/resource/pipeline/direct_chart_live.json').write_text(
        json.dumps(nodes,ensure_ascii=False,indent=4)+'\n',encoding='utf8')
