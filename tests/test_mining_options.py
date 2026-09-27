"""Verify displayed selections reach the production action without device actions."""
import copy
import json
from pathlib import Path
import sys
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import Mock,patch

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT/'agent'))
from mining import MineStories
from mining_policy import MiningOptions,STAR_NODES
from mining_stories import StoryMiningFlow
from chart_policy import ChartOptions


def merge(target,override):
    for key,value in override.items():
        if isinstance(value,dict):
            merge(target.setdefault(key,{}),value)
        else:
            target[key]=value


class MiningOptionTests(unittest.TestCase):
    def setUp(self):
        self.interface=json.loads((ROOT/'assets/interface.json').read_text(encoding='utf8'))
        self.nodes=json.loads((ROOT/'assets/resource/pipeline/mining.json').read_text(encoding='utf8'))

    def run_options(self,nodes):
        captured=[]
        class CaptureFlow:
            def __init__(self,context,options,output):
                captured.append(options);self.report={};self.image=None
            def run(self):self.report['status']='captured'
        context=SimpleNamespace(get_node_data=nodes.get)
        with tempfile.TemporaryDirectory() as folder, patch('mining.Path',return_value=Path(folder)), patch.object(
                MineStories,'flow_type',CaptureFlow):
            result=MineStories().run(context,None)
        return result,captured

    def test_checkbox_default_subset_and_empty_reach_production_options(self):
        option=self.interface['option']['挖矿练习星级']
        self.assertEqual(option['type'],self.interface['option']['挖矿自由演出难度']['type'])
        self.assertNotIn('inputs',option)
        self.assertEqual([case['name'] for case in option['cases']],['1★','2★','3★','4★','5★'])
        self.assertEqual(option['default_case'],['1★','2★','3★'])
        task=next(t for t in self.interface['task'] if t['entry']=='MineStories')
        # Leave stale saved input and previously selected stars behind: task
        # overrides must reset them before applying this run's selected tags.
        for chosen in (option['default_case'],['1★','3★','5★'],[],['4★']):
            nodes=copy.deepcopy(self.nodes)
            for node in STAR_NODES.values():nodes[node]['attach']['enabled']=True
            nodes['MN_stars']['attach']['value']='1,2,3,4,5'
            merge(nodes,task['pipeline_override'])
            for case in option['cases']:
                if case['name'] in chosen:merge(nodes,case['pipeline_override'])
            ok,options=self.run_options(nodes)
            self.assertTrue(ok)
            self.assertEqual(options[0].stars,frozenset(int(name[0]) for name in chosen))

    def test_cli_string_options_still_work_and_invalid_checkbox_does_not_start(self):
        nodes=copy.deepcopy(self.nodes);nodes['MN_stars']['attach']['value']='4,5'
        ok,options=self.run_options(nodes)
        self.assertTrue(ok);self.assertEqual(options[0].stars,{4,5})
        nodes['MN_stars']['attach']['checkbox']=True
        nodes['MN_star_1']['attach']['enabled']='false'
        ok,options=self.run_options(nodes)
        self.assertFalse(ok);self.assertEqual(options,[])

    def test_no_stars_skips_member_navigation_and_spending(self):
        flow=StoryMiningFlow.__new__(StoryMiningFlow)
        flow.mining=MiningOptions.parse({'stars':'','practice':True,'unlock':True})
        flow.report={};flow.member_list=Mock();flow.read_story=Mock()
        flow.run()
        flow.member_list.assert_not_called();flow.read_story.assert_not_called()
        self.assertEqual(flow.report['status'],'no_stars_selected')

    def test_all_performance_limits_default_to_blank_unlimited(self):
        for key in ('最大演出次数','谱面最大演出次数','挖矿最大演出数'):
            self.assertEqual(self.interface['option'][key]['inputs'][0]['default'],'')
        for path,node in (('mining','MN_max_rounds'),('chart_live','CL_max_rounds'),('live','LV_MaxRounds')):
            pipeline=json.loads((ROOT/f'assets/resource/pipeline/{path}.json').read_text(encoding='utf8'))
            self.assertEqual(pipeline[node]['attach']['value'],'')
        self.assertIsNone(MiningOptions.parse({}).max_rounds)
        self.assertIsNone(ChartOptions.parse({}).max_rounds)
        self.assertEqual(MiningOptions.parse({'max_rounds':'2'}).max_rounds,2)
        self.assertEqual(ChartOptions.parse({'max_rounds':'2'}).max_rounds,2)


if __name__=='__main__':unittest.main()
