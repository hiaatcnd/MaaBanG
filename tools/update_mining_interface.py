"""Generate mining task options without replacing other task definitions."""
import json
import sys
from pathlib import Path
ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT/'agent'))
from mining_policy import DEFAULTS, NODES, DIFFICULTY_NODES


def update(interface):
    tasks = [
        dict(name='挖矿：自由演出 Full Combo',entry='MineFullCombo',default_check=False,
             description='按歌曲各难度的星星颜色扫描未FC谱面，以极小偏差演奏，结算后复核星星。可选难度、每首火数和火不足策略。每谱面最多尝试3次；最大演出数按实际尝试计数。歌曲需已解锁且谱面在中国服目录内。',
             option=['挖矿自由演出难度','挖矿每首火数','挖矿火不足策略','挖矿最大演出数']),
        dict(name='挖矿：成员小故事',entry='MineStories',default_check=False,
             description='在乐队培养中分别筛选未读小故事和回忆小故事，跳过阅读并确认奖励。材料解锁与练习均需开启对应选项；只练习所选星级。练习时开启游戏自动特训，可从1级一次练到特训后的满级，并消耗对应特训材料。材料或练习券不足时跳过。',
             option=['挖矿阅读小故事','挖矿阅读回忆','挖矿材料解锁','挖矿练习满级','挖矿练习星级']),
        dict(name='挖矿：舞台挑战',entry='MineChallenges',default_check=False,
             description='选择主舞台或特别舞台，使用推荐编组，确认区域道具更换，以EXPERT、极小偏差演出。可选每首火数和火不足策略。解锁下一等级则继续，否则切换其他挑战。',
             option=['挖矿舞台类型','挖矿每首火数','挖矿火不足策略','挖矿最大演出数'])]
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
    for label,key in [('挖矿阅读小故事','stories'),('挖矿阅读回忆','memories'),
                      ('挖矿材料解锁','unlock'),('挖矿练习满级','practice')]:
        values = [DEFAULTS[key],not DEFAULTS[key]]
        options[label] = dict(type='select',cases=[dict(name='启用' if value else '关闭',
            pipeline_override={NODES[key]:{'attach':{'value':value}}}) for value in values])
    options['挖矿舞台类型'] = dict(type='select',cases=[dict(name=name,
        pipeline_override={NODES['stage']:{'attach':{'value':value}}})
        for name,value in [('主舞台','main'),('特别舞台','special')]])
    options['挖矿练习满级']['description'] = '只对所选星级启用练习和游戏自动特训，使用练习券及特训材料，按特训后的上限推荐用券；提交前检查材料，完成后核对实际等级。'
    for label,key,name,default,verify,message in [
        ('挖矿最大演出数','max_rounds','次数','10',r'^$|^[1-9][0-9]{0,2}$','留空不限次数，或填写1–999'),
        ('挖矿练习星级','stars','星级','1,2,3',r'^[1-5](,[1-5])*$','使用英文逗号，例如1,2,3')]:
        options[label] = dict(type='input',inputs=[dict(name=name,label=label,default=default,
            verify=verify,pattern_msg=message)],pipeline_override={NODES[key]:{'attach':{'value':'{'+name+'}'}}})
    return interface


if __name__ == '__main__':
    path = ROOT/'assets/interface.json'
    path.write_text(json.dumps(update(json.loads(path.read_text(encoding='utf8'))),ensure_ascii=False,indent=4)+'\n',encoding='utf8')
