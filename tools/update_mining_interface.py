"""Generate mining task options without replacing other task definitions."""
import json
import sys
from pathlib import Path
ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT/'agent'))
from mining_policy import DEFAULTS, NODES, DIFFICULTY_NODES, STAR_NODES


def update(interface):
    tasks = [
        dict(name='挖矿：自由演出 Full Combo',entry='MineFullCombo',default_check=False,
             description='按歌曲各难度的星星颜色扫描未FC谱面，以极小偏差演奏，结算后复核星星。可选难度、每首火数和火不足策略。每谱面最多尝试3次；最大演出数按实际尝试计数。歌曲需已解锁且谱面在中国服目录内。',
             option=['挖矿自由演出难度','挖矿每首火数','挖矿火不足策略','挖矿最大演出数']),
        dict(name='挖矿：成员小故事',entry='MineStories',default_check=False,
             pipeline_override={NODES['stars']:{'attach':{'checkbox':True}},
                                **{node:{'attach':{'enabled':False}} for node in STAR_NODES.values()}},
             description='在游戏内按成员星级筛选未读小故事和回忆小故事，跳过阅读并确认奖励。开启“练习至满级”后，允许使用练习券、自动特训材料及故事解锁材料；关闭时只读已解锁故事。材料或练习券不足时跳过。',
             option=['挖矿阅读小故事','挖矿阅读回忆','挖矿练习星级','挖矿练习满级']),
        dict(name='挖矿：舞台挑战',entry='MineChallenges',default_check=False,
             description='按乐队列表顺序、舞台编号从小到大挑战。可选未完成或未满星，每关每轮最多尝试一次。使用推荐编组，以EXPERT、极小偏差演出。',
             option=['挖矿舞台类型','挖矿挑战目标','挖矿每首火数','挖矿火不足策略','挖矿最大演出数'])]
    names = {t['entry'] for t in tasks}
    interface['task'] = [t for t in interface['task'] if t['entry'] not in names]+tasks
    options = interface['option']
    options['挖矿自由演出难度'] = dict(type='checkbox',
        description='只挖勾选的难度，默认全选；全部取消时不演出，直接返回主页。',
        default_case=[d.upper() for d in DIFFICULTY_NODES],
        cases=[dict(name=d.upper(),pipeline_override={node:{'attach':{'enabled':True}}})
               for d,node in DIFFICULTY_NODES.items()])
    options['挖矿每首火数'] = dict(type='select',default_case='0火',cases=[dict(name=f'{value}火',
        pipeline_override={NODES['fire']:{'attach':{'value':value}}}) for value in range(4)])
    options['挖矿火不足策略'] = dict(type='select',default_case='停止',
        description='使用回复道具时优先小型饮料，不足再用普通饮料；不使用星石。道具不足则停止。',
        cases=[dict(name=name,pipeline_override={NODES['shortage']:{'attach':{'value':value}}})
               for name,value in [('停止','stop'),('使用回复道具','items')]])
    for label,key in [('挖矿阅读小故事','stories'),('挖矿阅读回忆','memories')]:
        values = [DEFAULTS[key],not DEFAULTS[key]]
        options[label] = dict(type='select',cases=[dict(name='启用' if value else '关闭',
            pipeline_override={NODES[key]:{'attach':{'value':value}}}) for value in values])
    options['挖矿舞台类型'] = dict(type='select',cases=[dict(name=name,
        pipeline_override={NODES['stage']:{'attach':{'value':value}}})
        for name,value in [('主舞台','main'),('特别舞台','special')]])
    options['挖矿挑战目标'] = dict(type='select',default_case='未完成',
        description='未完成：仅挑战已解锁的0星舞台；未满星：挑战已解锁的0–2星舞台。跳过满星及未解锁舞台。',
        cases=[dict(name=name,pipeline_override={NODES['challenge_target']:{'attach':{'value':value}}})
               for name,value in [('未完成','uncleared'),('未满星','not_full_stars')]])
    options.pop('挖矿材料解锁',None)
    options['挖矿练习满级'] = dict(type='select',label='练习至满级',default_case='关闭',
        description='开启后，允许为所选星级使用练习券和自动特训材料练至满级，并使用材料解锁故事；材料不足则跳过。关闭时只读已解锁故事。',
        cases=[dict(name='启用' if enabled else '关闭',pipeline_override={
            NODES[key]:{'attach':{'value':enabled}} for key in ('practice','unlock')}) for enabled in (False,True)])
    options['挖矿练习星级'] = dict(type='checkbox',default_case=['1★','2★','3★'],
        description='直接用于游戏内成员筛选；关闭练习至满级时也生效。全部取消则跳过成员故事任务。',
        cases=[dict(name=f'{stars}★',pipeline_override={node:{'attach':{'enabled':True}}})
               for stars,node in STAR_NODES.items()])
    for label,key,name,default,verify,message in [
        ('挖矿最大演出数','max_rounds','次数','',r'^$|^[1-9][0-9]{0,2}$','留空不限，或填写1–999')]:
        options[label] = dict(type='input',inputs=[dict(name=name,label=label,default=default,
            verify=verify,pattern_msg=message)],pipeline_override={NODES[key]:{'attach':{'value':'{'+name+'}'}}})
    # Keep saved option IDs and values; display concise labels in the client.
    for task in tasks:
        for name in task['option']:
            option=options[name]
            label={'挖矿练习星级':'成员星级','挖矿练习满级':'练习至满级',
                   '挖矿最大演出数':'最大演出次数'}.get(name,name.removeprefix('挖矿'))
            option['label']=label
            for field in option.get('inputs',[]):
                field['label']=label
    from update_live_presets_interface import update as update_presets
    return update_presets(interface)


if __name__ == '__main__':
    path = ROOT/'assets/interface.json'
    path.write_text(json.dumps(update(json.loads(path.read_text(encoding='utf8'))),ensure_ascii=False,indent=4)+'\n',encoding='utf8')
