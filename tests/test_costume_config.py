import json
import unittest
from pathlib import Path
from maa.resource import Resource

ROOT=Path(__file__).resolve().parents[1]


class ConfigTests(unittest.TestCase):
    def test_user_target_and_mode_survive_independent_overrides(self):
        resource=Resource()
        self.assertTrue(resource.post_bundle(ROOT/'assets/resource').wait().succeeded)
        interface=json.loads((ROOT/'assets/interface.json').read_text(encoding='utf-8'))
        target=interface['option']['服装目标数量']['pipeline_override']
        target=json.loads(json.dumps(target, ensure_ascii=False).replace('"{目标数量}"','66'))
        mode=interface['option']['服装执行模式']['cases'][1]['pipeline_override']
        self.assertTrue(resource.override_pipeline(target))
        self.assertTrue(resource.override_pipeline(mode))
        self.assertEqual(resource.get_node_data('UnlockCostumes')['action']['param']['custom_action_param']['target'],66)
        self.assertIs(resource.get_node_data('CU_ExecutionOptions')['attach']['inspect_only'],True)
        for key, index in [('服装执行范围',2), ('服装指定成员',3), ('服装指定乐队',3)]:
            self.assertTrue(resource.override_pipeline(interface['option'][key]['cases'][index]['pipeline_override']))
        self.assertEqual(resource.get_node_data('CU_ScopeMode')['attach']['kind'],'member')
        self.assertEqual(resource.get_node_data('CU_SelectedMember')['attach']['value'],'市谷有咲')
        self.assertEqual(resource.get_node_data('UnlockCostumes')['action']['param']['custom_action_param']['target'],66)
        self.assertIs(resource.get_node_data('CU_ExecutionOptions')['attach']['inspect_only'],True)
        self.assertTrue(resource.override_pipeline(interface['option']['服装执行范围']['cases'][0]['pipeline_override']))
        self.assertEqual(resource.get_node_data('CU_ScopeMode')['attach']['kind'],'all')


if __name__=='__main__': unittest.main()
