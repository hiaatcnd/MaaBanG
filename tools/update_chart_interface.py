"""Generate chart-task options from the bundled China-server song catalog."""
import json
from pathlib import Path
import sys

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT/'agent'))
from chart_policy import OPTION_DEFAULTS, OPTION_NODES, JITTER_PROFILES
from song_catalog import BY_ID, SONGS, resolve_song, available_difficulties
from live_policy import DIFFICULTIES


def choice(label,key,value,options=None):
    case={'name':label,'pipeline_override':{OPTION_NODES[key]:{'attach':{'value':value}}}}
    if options:
        case['option']=options
    return case


def update(interface):
    interface['task']=[t for t in interface['task'] if t['entry']!='ChartLive']
    interface['task'].insert(0,{'name':'Maa代打演出','entry':'ChartLive','default_check':False,
        'description':'根据乐谱自动操作，保留随机偏差，允许偶发漏键。支持中国服已开放歌曲和难度；歌曲需已解锁。支持自由演出、自由巡演及课题巡演。自动调整速度为9.80、默认演出皮肤、轻量模式并关闭镜像。当前版本需MuMu安卓15、16:9横屏（支持2560×1440，内部自动缩放）。首次使用曲目需联网下载谱面。巡演三首计一次；道具补火仅在你选择启用时执行，不使用星石。',
        'option':['谱面演出模式','谱面随机偏差','谱面每首火数','谱面火不足策略','谱面最大演出次数']})
    options=interface['option']
    for key in list(options):
        if key.startswith('谱面'):
            del options[key]
    for slot in range(1,4):
        cases=[]
        for key in SONGS:
            song=resolve_song(key)
            names=available_difficulties(song)
            profile=f'谱面第{slot}首难度_'+'_'.join(names)
            options[profile]={'type':'select','label':f'第{slot}首难度','default_case':'EXPERT',
                'cases':[choice(name.upper(),f'difficulty{slot}',name) for name in DIFFICULTIES if name in names]}
            item=choice(key,f'song{slot}',song['id'],[profile])
            item['label']=f"{song['title']} · {song['band']}"
            cases.append(item)
        options[f'谱面第{slot}首歌曲']={'type':'select','default_case':'SAVIOR OF SONG',
                                    'label':f'第{slot}首歌曲','cases':cases}
        options[f'谱面课题第{slot}首难度']={'type':'select','default_case':'EXPERT',
            'label':f'课题第{slot}首难度',
            'description':'读取当前课题固定歌曲；所选难度不存在时停止，不替换难度。',
            'cases':[choice(name.upper(),f'difficulty{slot}',name) for name in DIFFICULTIES]}
    selectors=[f'谱面第{i}首歌曲' for i in range(1,4)]
    options['谱面演出模式']={'type':'select','label':'演出模式','cases':[
        choice('自由演出','mode','free',[selectors[0]]),
        choice('自由巡演（三首自选）','mode','tour_free',selectors),
        choice('课题巡演（左侧固定歌曲）','mode','tour_fixed',[f'谱面课题第{i}首难度' for i in range(1,4)])]}
    names=['极小偏差（优先准确）','小偏差','中等偏差','大偏差','很大偏差（可能频繁MISS）']
    options['谱面随机偏差']={'type':'select','label':'随机偏差','default_case':'小偏差',
        'description':'时间和位置均采用以0为中心的截断正态分布：小偏差常见，大偏差少见，标准差为上限的1/3。各档均非零，不提供关闭；不保证ALL PERFECT，大偏差可能导致演出失败。',
        'cases':[dict(choice(label,'jitter',key),description=f'时间上限 ±{time:g} 毫秒，位置上限 ±{space:g} 像素。')
                 for label,(key,(time,space)) in zip(names,JITTER_PROFILES.items())]}
    options['谱面每首火数']={'type':'select','label':'每首火数','default_case':'1火',
        'cases':[choice(f'{i}火','fire',i) for i in range(4)]}
    options['谱面火不足策略']={'type':'select','label':'火不足策略','cases':[
        choice('停止','shortage','stop'),choice('使用回复道具补火','shortage','items')],
        'description':'优先小型饮料，不足时使用普通饮料。只补足本轮需要的火；不用星石。'}
    options['谱面最大演出次数']={'type':'input','label':'最大演出次数','inputs':[{'name':'次数','label':'最大演出次数',
        'description':'自由演出一首计一次；巡演完整三首计一次。留空持续运行，直到停止、火或道具不足。',
        'default':'1','verify':'^$|^[1-9][0-9]{0,2}$','pattern_msg':'留空不限次数，或填写1–999'}],
        'pipeline_override':{OPTION_NODES['max_rounds']:{'attach':{'value':'{次数}'}}}}
    return interface


def main():
    path=ROOT/'assets/interface.json'
    interface=update(json.loads(path.read_text(encoding='utf8')))
    path.write_text(json.dumps(interface,ensure_ascii=False,indent=4)+'\n',encoding='utf8')
    nodes={'ChartLive':{'action':'Custom','custom_action':'ChartLive'}}
    nodes.update({OPTION_NODES[key]:{'attach':{'value':value}} for key,value in OPTION_DEFAULTS.items()})
    (ROOT/'assets/resource/pipeline/chart_live.json').write_text(json.dumps(nodes,ensure_ascii=False,indent=4)+'\n',encoding='utf8')
    print(f'Generated ChartLive options for {len(BY_ID)} songs')


if __name__=='__main__':
    main()
