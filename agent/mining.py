"""Mining custom actions and report persistence."""
import json
import time
from pathlib import Path
from maa.custom_action import CustomAction
from mining_policy import DEFAULTS, NODES, DIFFICULTY_NODES, MiningOptions
from mining_live import MiningLiveFlow, ChallengeMiningFlow
from mining_stories import StoryMiningFlow


class MiningAction(CustomAction):
    flow_type = MiningLiveFlow

    def run(self, context, argv):
        output = Path('debug/mining')/(self.__class__.__name__+'-'+time.strftime('%Y%m%d-%H%M%S'))
        output.mkdir(parents=True,exist_ok=True)
        report = {'status':'error'}
        flow = None
        try:
            values = {key:(context.get_node_data(node) or {}).get('attach',{}).get('value',DEFAULTS[key])
                      for key,node in NODES.items()}
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
            return True
        except Exception as exc:
            report.update(status='error',error=str(exc))
            print(f'[挖矿] 已停止：{exc}',flush=True)
            return False
        finally:
            (output/'report.json').write_text(json.dumps(report,ensure_ascii=False,indent=2),encoding='utf8')
            if flow is not None and report['status']=='error' and flow.image is not None:
                flow.save_frame('error.png')


class MineFullCombo(MiningAction):
    pass


class MineStories(MiningAction):
    flow_type = StoryMiningFlow


class MineChallenges(MiningAction):
    flow_type = ChallengeMiningFlow
