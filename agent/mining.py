"""Mining custom actions and report persistence."""
from task_logging import log, finish, failure
import json
import time
from pathlib import Path
from maa.custom_action import CustomAction
from mining_policy import DEFAULTS, NODES, DIFFICULTY_NODES, STAR_NODES, MiningOptions
from mining_live import MiningLiveFlow, ChallengeMiningFlow
from mining_stories import StoryMiningFlow


class MiningAction(CustomAction):
    task_label = "挖矿：自由演出 Full Combo"
    flow_type = MiningLiveFlow

    def run(self, context, argv):
        log(f'[{self.task_label}] 开始执行')
        output = Path('debug/mining')/(self.__class__.__name__+'-'+time.strftime('%Y%m%d-%H%M%S'))
        output.mkdir(parents=True,exist_ok=True)
        report = {'status':'error'}
        flow = None
        try:
            values = {key:(context.get_node_data(node) or {}).get('attach',{}).get('value',DEFAULTS[key])
                      for key,node in NODES.items()}
            star_settings=(context.get_node_data(NODES['stars']) or {}).get('attach',{})
            if star_settings.get('checkbox',False):
                selected=[]
                for stars,node in STAR_NODES.items():
                    enabled=(context.get_node_data(node) or {}).get('attach',{}).get('enabled',False)
                    if not isinstance(enabled,bool):
                        raise ValueError('成员星级配置无效：'+str(stars))
                    if enabled:
                        selected.append(str(stars))
                values['stars']=','.join(selected)
            values['difficulties'] = []
            for difficulty,node in DIFFICULTY_NODES.items():
                enabled = (context.get_node_data(node) or {}).get('attach',{}).get('enabled',False)
                if not isinstance(enabled,bool):
                    raise ValueError('挖矿难度配置无效：'+difficulty)
                if enabled:
                    values['difficulties'].append(difficulty)
            flow = self.flow_type(context,MiningOptions.parse(values),output)
            report = flow.report
            flow.run()
            finish(self.task_label, report)
            return True
        except Exception as exc:
            report.update(status='error',error=str(exc))
            failure(self.task_label, exc, context)
            return False
        finally:
            (output/'report.json').write_text(json.dumps(report,ensure_ascii=False,indent=2),encoding='utf8')
            if flow is not None and report['status']=='error' and flow.image is not None:
                flow.save_frame('error.png')


class MineFullCombo(MiningAction):
    pass


class MineStories(MiningAction):
    task_label = "挖矿：成员小故事"
    flow_type = StoryMiningFlow


class MineChallenges(MiningAction):
    task_label = "挖矿：舞台挑战"
    flow_type = ChallengeMiningFlow
