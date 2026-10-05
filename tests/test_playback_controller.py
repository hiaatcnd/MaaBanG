from copy import deepcopy
from pathlib import Path
import sys
from types import SimpleNamespace
import unittest
from unittest.mock import Mock

sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'agent'))
from playback_controller import controller_config


class PlaybackControllerTests(unittest.TestCase):
    def test_preserves_vendor_config_and_serial_without_discovery(self):
        for vendor,extra in [('ld',{'enable':True,'path':'D:/LDPlayer','index':2}),
                             ('mumu',{'enable':True,'index':0,'path':'D:/MuMu','lib':'custom.dll'}),
                             ('future',{'enable':True,'transport':'shared'})]:
            original={'type':'adb','adb_path':'D:/adb.exe','adb_serial':'emulator-7554',
                      'screencap_methods':64,'config':{'extras':{vendor:extra},'custom':{'value':1}}}
            snapshot=deepcopy(original)
            discover=Mock(side_effect=AssertionError('must reuse current connection'))
            adb,address,config=controller_config(original,discover)
            self.assertEqual((adb,address),('D:/adb.exe','emulator-7554'))
            self.assertEqual(config,original['config'])
            config['extras'][vendor]['index']=99
            self.assertEqual(original,snapshot)

    def test_missing_extras_discovers_only_exact_device_and_keeps_options(self):
        selected=SimpleNamespace(address='127.0.0.1:16384',screencap_methods=64,
                                 config={'extras':{'mumu':{'enable':True,'index':0}},
                                         'backend_option':7,'option':0})
        other=SimpleNamespace(address='127.0.0.1:16416',screencap_methods=64,
                              config={'extras':{'mumu':{'enable':True,'index':1}}})
        info={'type':'adb','adb_path':'adb.exe','adb_serial':selected.address,'config':{'option':42}}
        _,_,config=controller_config(info,lambda:[other,selected])
        self.assertEqual(config,{**selected.config,'option':42})
        self.assertEqual(info['config'],{'option':42})

    def test_missing_or_ambiguous_device_never_uses_another_instance(self):
        info={'type':'adb','adb_path':'adb.exe','adb_serial':'emulator-7554'}
        matching=SimpleNamespace(address='emulator-7554',screencap_methods=64,
                                 config={'extras':{'ld':{'enable':True}}})
        other=SimpleNamespace(address='127.0.0.1:16416',screencap_methods=64,config=matching.config)
        for devices in ([],[other],[matching,matching]):
            with self.assertRaisesRegex(ValueError,'唯一截图增强'):
                controller_config(info,lambda:devices)

    def test_respects_explicit_screenshot_method_and_disabled_extras(self):
        info={'type':'adb','adb_path':'adb.exe','adb_serial':'x','screencap_methods':2}
        with self.assertRaisesRegex(ValueError,'启用 EmulatorExtras'):
            controller_config(info,Mock(side_effect=AssertionError))
        info.update(screencap_methods=64,config={'extras':{'ld':{'enable':False}}})
        self.assertFalse(controller_config(info)[2]['extras']['ld']['enable'])

    def test_non_adb_is_rejected_without_vendor_requirement(self):
        with self.assertRaisesRegex(ValueError,'ADB'):
            controller_config({'type':'win32'})


if __name__=='__main__':unittest.main()
