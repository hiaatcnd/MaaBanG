"""Generate chart-task options from the bundled China-server song catalog."""
import json
from pathlib import Path
import sys

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT/'agent'))
from chart_policy import OPTION_DEFAULTS, OPTION_NODES, JITTER_PROFILES, COOP_ROOMS, COOP_ROOM_GROUPS
from song_catalog import BY_ID, SONGS, resolve_song, available_difficulties
from live_policy import DIFFICULTIES
from interface_descriptions import OPTION_DESCRIPTIONS, INPUT_DESCRIPTIONS, TASK_DESCRIPTIONS


def choice(label,key,value,options=None):
    case={'name':label,'pipeline_override':{OPTION_NODES[key]:{'attach':{'value':value}}}}
    if options:
        case['option']=options
    return case


def update(interface):
    interface['task']=[t for t in interface['task'] if t['entry']!='ChartLive']
    interface['task'].insert(0,{'name':'Maa代打演出','entry':'ChartLive','default_check':False,
        'description':TASK_DESCRIPTIONS['ChartLive'],
        'option':['谱面演出模式','谱面Fever印章','谱面随机偏差','谱面避免FullCombo','谱面最大演出次数']})
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
    options['谱面从收藏选择']={'type':'select','label':'从收藏选择','default_case':'关',
        'description':OPTION_DESCRIPTIONS['谱面从收藏选择'],
        'cases':[choice(label,'from_favorites',value) for label,value in [('关',False),('开',True)]]}
    options['谱面避免FullCombo']={'type':'select','label':'避免 Full Combo','default_case':'关',
        'description':OPTION_DESCRIPTIONS['谱面避免FullCombo'],
        'cases':[choice(label,'avoid_full_combo',value) for label,value in [('关',False),('开',True)]]}
    fire_options=['谱面每首火数','谱面火不足策略']
    options['谱面演出模式']={'type':'select','label':'演出模式','cases':[
        choice('自由演出','mode','free',[selectors[0],'谱面从收藏选择',*fire_options]),
        choice('自由巡演（三首自选）','mode','tour_free',selectors+['谱面从收藏选择']+fire_options),
        choice('课题巡演（左侧固定歌曲）','mode','tour_fixed',[f'谱面课题第{i}首难度' for i in range(1,4)]+fire_options),
        choice('团队演出','mode','team',['谱面联网难度',*fire_options]),
        choice('协力演出','mode','coop',['谱面协力房间类别','谱面协力房间','谱面协力歌曲','谱面从收藏选择','谱面联网难度',*fire_options]),
        choice('挑战演出（消耗CP）','mode','challenge',['谱面挑战歌曲','谱面挑战难度','谱面每首CP'])]}
    for option,label,key,default,description in (
        ('谱面挑战歌曲','活动歌曲','cp_song','沿用当前活动歌曲',
         '从中国服歌曲列表选择。指定歌曲必须在当前活动歌单内，否则停止；不替换为其他歌曲。'),
        ('谱面协力歌曲','协力选歌','coop_song','不指定歌曲',
         '从中国服歌曲列表选择要提交的歌曲；最终演奏曲目由游戏在全房间提交的歌曲中抽选。')):
        cases=[choice(default,key,'')]
        for song_key in SONGS:
            song=resolve_song(song_key)
            cases.append(dict(choice(song_key,key,song['id']),label=f"{song['title']} · {song['band']}"))
        options[option]={'type':'select','label':label,'default_case':default,
                         'description':description,'cases':cases}
    options['谱面协力房间类别']={'type':'select','label':'房间类别','default_case':'普通',
        'cases':[choice(label,'coop_room_group',value) for value,label in COOP_ROOM_GROUPS.items()]}
    options['谱面协力房间']={'type':'select','label':'房间类型','default_case':'自由房间',
        'description':'按所选房间匹配；综合能力不足或房间不可用时停止，不自动换房。',
        'cases':[choice(label,'coop_room',value) for value,label in COOP_ROOMS.items()]}
    options['谱面挑战难度']={'type':'select','label':'挑战难度','default_case':'EXPERT',
        'description':'所选难度必须在活动歌曲中开放，不自动降级。',
        'cases':[choice(name.upper(),'difficulty1',name) for name in DIFFICULTIES]}
    options['谱面每首CP']={'type':'select','label':'每首消耗CP','default_case':'200 CP',
        'description':'不消耗火。CP不足时停止，不自动补充或改用其他档位。',
        'cases':[choice(f'{amount} CP','cp',amount) for amount in (200,400,800,1600)]}
    options['谱面联网难度']={'type':'select','label':'演出难度','default_case':'EXPERT',
        'description':OPTION_DESCRIPTIONS['谱面联网难度'],
        'cases':[choice(name.upper(),'difficulty1',name) for name in DIFFICULTIES]}
    names=['极小偏差（优先准确）','小偏差','中等偏差','中大偏差','大偏差','很大偏差（可能频繁MISS）']
    options['谱面随机偏差']={'type':'select','label':'随机偏差','default_case':'小偏差',
        'description':OPTION_DESCRIPTIONS['谱面随机偏差'],
        'cases':[dict(choice(label,'jitter',key),description=f'时间上限 ±{time:g} 毫秒，位置上限 ±{space:g} 像素。')
                 for label,(key,(time,space)) in zip(names,JITTER_PROFILES.items())]}
    options['谱面每首火数']={'type':'select','label':'每首火数','default_case':'1火',
        'cases':[choice(f'{i}火','fire',i) for i in range(4)]}
    for name,node in [('谱面Fever印章',OPTION_NODES['fever']),('演出Fever印章','LV_Fever')]:
        options[name]={'type':'select','label':'使用Fever印章','default_case':'关',
            'description':'开：按游戏规则使用Fever印章。次数用完、印章不足或游戏不允许开启时继续演出并记录原因；不补充印章。关：确认关闭后演出。',
            'cases':[{'name':label,'pipeline_override':{node:{'attach':{'value':value}}}}
                     for label,value in [('关','off'),('开','on')]]}
    for task in interface['task']:
        if task['entry']=='AutoLive' and '演出Fever印章' not in task['option']:
            task['option'].append('演出Fever印章')
    options['谱面火不足策略']={'type':'select','label':'火不足策略','cases':[
        choice('停止','shortage','stop'),choice('使用回复道具补火','shortage','items')],
        'description':OPTION_DESCRIPTIONS['谱面火不足策略']}
    options['谱面最大演出次数']={'type':'input','label':'最大演出次数','inputs':[{'name':'次数','label':'最大演出次数',
        'description':INPUT_DESCRIPTIONS[('谱面最大演出次数', '次数')],
        'default':'','verify':'^$|^[1-9][0-9]{0,2}$','pattern_msg':'留空不限，或填写1–999'}],
        'pipeline_override':{OPTION_NODES['max_rounds']:{'attach':{'value':'{次数}'}}}}
    from update_live_presets_interface import update as update_presets
    from update_direct_chart_interface import update as update_direct
    return update_presets(update_direct(interface))


def main():
    path=ROOT/'assets/interface.json'
    interface=update(json.loads(path.read_text(encoding='utf8')))
    path.write_text(json.dumps(interface,ensure_ascii=False,indent=4)+'\n',encoding='utf8')
    nodes={'ChartLive':{'action':'Custom','custom_action':'ChartLive'}}
    nodes.update({OPTION_NODES[key]:{'attach':{'value':value}} for key,value in OPTION_DEFAULTS.items()})
    (ROOT/'assets/resource/pipeline/chart_live.json').write_text(json.dumps(nodes,ensure_ascii=False,indent=4)+'\n',encoding='utf8')
    from update_direct_chart_interface import write_pipeline
    write_pipeline()
    print(f'Generated ChartLive options for {len(BY_ID)} songs')


if __name__=='__main__':
    main()
