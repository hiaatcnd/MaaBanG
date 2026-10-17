"""Private-service integration fixture: all account traffic stays in memory."""
import copy
import json
import os
import sys

sys.path.insert(0, os.environ['MAABANG_UI_TEST_AGENT'])
import bestdori_desktop
from bestdori_client import BestdoriClient
from bestdori_profiles import empty_profile, export_profile


class MockCloud(BestdoriClient):
    def __init__(self):
        super().__init__()
        self.username = '界面测试账号'
        self.remote = [{'name': '保持不变', 'future': {'opaque': 7}},
                       json.loads(export_profile(empty_profile('要更新的档案'), compressed=True))]

    def request(self, endpoint, data=None):
        assert endpoint == 'user/profiles', 'fixture permits profiles only'
        if data is not None:
            assert len(data['profiles']) == 2
            assert data['profiles'][0] == self.remote[0]
            self.remote = copy.deepcopy(data['profiles'])
        return {'result': True, 'profiles': copy.deepcopy(self.remote)}


original_workspace = bestdori_desktop.DesktopWorkspace


class Workspace(original_workspace):
    def __init__(self):
        super().__init__(client=MockCloud())


bestdori_desktop.DesktopWorkspace = Workspace
bestdori_desktop.serve_desktop()
