from copy import deepcopy
from pathlib import Path
import sys
from types import SimpleNamespace
import unittest
from unittest.mock import Mock

sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'agent'))
from playback_controller import controller_config, playback_options


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


class PlaybackOptionsTests(unittest.TestCase):
    @staticmethod
    def connection(methods=None, vendor='mumu', **extra):
        return {'type':'adb','adb_path':'user-adb.exe','adb_serial':'127.0.0.1:16384',
                'screencap_methods':64,'input_methods':methods,
                'config':{'extras':{vendor:{'enable':True,**extra}}}}

    @staticmethod
    def device(methods=-1, vendor='mumu', **extra):
        return SimpleNamespace(address='127.0.0.1:16384',adb_path='discovered-adb.exe',
                               screencap_methods=64,input_methods=methods,
                               config={'extras':{vendor:{'enable':True,**extra}},
                                       'command':{'StartApp':['native','command']}})

    def test_default_signed_unsigned_and_missing_refresh_complete_official_discovery(self):
        for methods in (None, -9, 2**64-9):
            with self.subTest(methods=methods):
                info=self.connection(methods)
                info['config']['command']={'custom':['keep']}
                original=deepcopy(info)
                device=self.device(path='D:/MuMu',index=2)
                found=deepcopy(device.config)
                discover=Mock(return_value=[device])
                options=playback_options(info,discover)
                discover.assert_called_once_with()
                self.assertEqual(options.input_methods,2**64-2)
                self.assertEqual(options.input_source,'toolkit_discovery')
                self.assertEqual(options.configured_input_methods,methods)
                self.assertEqual(options.discovered_input_methods,-1)
                self.assertEqual(options.adb_path,'user-adb.exe')
                self.assertEqual(options.address,info['adb_serial'])
                self.assertEqual(options.config['extras']['mumu']['index'],2)
                self.assertEqual(options.config['command'],{'StartApp':['native','command'],'custom':['keep']})
                options.config['extras']['mumu']['index']=99
                self.assertEqual(info,original)
                self.assertEqual(device.config,found)

    def test_old_mumu_and_non_mumu_use_official_compatibility_mask(self):
        for vendor in ('mumu','ld','future'):
            with self.subTest(vendor=vendor):
                result=playback_options(self.connection(vendor=vendor),lambda:[self.device(-9,vendor)])
                self.assertEqual(result.input_methods,2**64-10)
                self.assertFalse(result.input_methods & 8)
                self.assertFalse(result.input_methods & 1)
                self.assertTrue(result.input_methods & 4)

    def test_all_with_complete_connection_config_does_not_rediscover_or_claim_backend(self):
        for mask in (-1,2**64-1):
            with self.subTest(mask=mask):
                result=playback_options(self.connection(mask),Mock(side_effect=AssertionError))
                self.assertEqual(result.input_methods,2**64-2)
                self.assertEqual(result.input_source,'connection_all')
                self.assertIsNone(result.discovered_input_methods)

    def test_explicit_contact_choices_preserved_without_discovery(self):
        for mask in (2,4,8,6,12,14):
            with self.subTest(mask=mask):
                result=playback_options(self.connection(mask),Mock(side_effect=AssertionError))
                self.assertEqual(result.input_methods,mask)
                self.assertEqual(result.input_source,'connection_explicit')

    def test_explicit_disabled_or_adb_shell_policy_rejected_without_rewriting(self):
        for mask in (0,1,3,5,7,9,15):
            with self.subTest(mask=mask):
                info=self.connection(mask)
                original=deepcopy(info)
                with self.assertRaisesRegex(ValueError,'分触点'):
                    playback_options(info,Mock(side_effect=AssertionError))
                self.assertEqual(info,original)

    def test_disabled_extras_and_nested_user_options_survive_refresh(self):
        info=self.connection(-9,enable=False,lib='custom.dll')
        result=playback_options(info,lambda:[self.device(path='D:/MuMu',index=0)])
        self.assertEqual(result.config['extras']['mumu'],
                         {'enable':False,'lib':'custom.dll','path':'D:/MuMu','index':0})

    def test_androws_uses_official_extras_only_without_normal_display_fallback(self):
        info=self.connection(vendor='androws',app_package='example.game')
        result=playback_options(info,lambda:[self.device(8,'androws')])
        self.assertEqual(result.input_methods,8)
        self.assertEqual(result.config['extras']['androws']['app_package'],'example.game')
        for methods in (-1,2**64-1,2,4,6,12,14):
            with self.subTest(methods=methods),self.assertRaisesRegex(ValueError,'虚拟显示'):
                playback_options(self.connection(methods,'androws'),Mock(side_effect=AssertionError))
        self.assertEqual(playback_options(self.connection(8,'androws')).input_methods,8)

    def test_missing_extras_uses_complete_discovery_and_never_another_address(self):
        info=self.connection()
        info['config']={'option':42}
        other=self.device()
        other.address='127.0.0.1:16416'
        target=self.device(index=0,path='D:/MuMu')
        result=playback_options(info,lambda:[other,target])
        self.assertEqual(result.config['extras']['mumu']['index'],0)
        self.assertEqual(result.config['option'],42)
        for devices in ([],[other],[target,target]):
            with self.subTest(devices=devices),self.assertRaisesRegex(ValueError,'唯一'):
                playback_options(info,lambda:devices)

    def test_manual_extras_without_local_discovery_keep_compatibility_without_guessing(self):
        result=playback_options(self.connection(),lambda:[])
        self.assertEqual(result.input_methods,2**64-10)
        self.assertEqual(result.input_source,'connection_default_no_match')
        self.assertIsNone(result.discovered_input_methods)

    def test_identity_conflicts_never_merge_another_instance_or_vendor(self):
        for explicit,found in ((self.connection(index=1),self.device(index=2)),
                               (self.connection(path='D:/MuMu'),self.device(path='E:/MuMu')),
                               (self.connection(vendor='androws'),self.device())):
            with self.subTest(explicit=explicit),self.assertRaisesRegex(ValueError,'冲突'):
                playback_options(explicit,lambda:[found])
        result=playback_options(self.connection(path='D:\\MuMu\\'),lambda:[self.device(path='d:/mumu')])
        self.assertEqual(result.config['extras']['mumu']['path'],'D:\\MuMu\\')

    def test_invalid_masks_or_config_fail_before_discovery(self):
        for mask in (True,False,'4',4.0,2**64,-2**63-1):
            with self.subTest(mask=mask),self.assertRaises(ValueError):
                playback_options(self.connection(mask),Mock(side_effect=AssertionError))
        for config in ([],False,{'extras':None},{'extras':[]}):
            info=self.connection()
            info['config']=config
            with self.subTest(config=config),self.assertRaises(ValueError):
                playback_options(info,Mock(side_effect=AssertionError))

    def test_screenshot_and_connection_requirements_checked_before_discovery(self):
        info=self.connection()
        info['screencap_methods']=2
        with self.assertRaisesRegex(ValueError,'EmulatorExtras'):
            playback_options(info,Mock(side_effect=AssertionError))
        with self.assertRaisesRegex(ValueError,'ADB'):
            playback_options({'type':'win32'},Mock(side_effect=AssertionError))


if __name__=='__main__':unittest.main()
