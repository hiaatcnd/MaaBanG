"""Reuse MaaFramework device configuration for low-latency chart playback."""
from copy import deepcopy


def controller_config(info, find_devices=None):
    """Keep the current device's extras intact; discover them only if absent.

    Device discovery and vendor-specific library selection belong to MaaFramework.
    Never substitute another connected device or guess an emulator installation.
    """
    if info.get('type') != 'adb':
        raise ValueError('谱面演出需要支持截图增强和多点触控的 ADB 连接')
    adb, address = info['adb_path'], info['adb_serial']
    config = deepcopy(info.get('config') or {})
    if not isinstance(config, dict):
        raise ValueError('ADB 连接配置格式无效')
    methods = info.get('screencap_methods')
    if methods is not None and not int(methods) & 64:
        raise ValueError('谱面演出需要截图增强，请在连接设置中启用 EmulatorExtras')
    if not config.get('extras'):
        if find_devices is None:
            from maa.toolkit import Toolkit
            find_devices = Toolkit.find_adb_devices
        matches = [device for device in find_devices()
                   if device.address == address and int(device.screencap_methods) & 64
                   and device.config.get('extras')]
        if len(matches) != 1:
            raise ValueError('未找到当前 ADB 地址的唯一截图增强配置，请刷新设备并启用截图增强')
        # Keep all discovered backend options, with explicit connection options
        # taking precedence. An empty extras object is the missing value here.
        discovered=deepcopy(matches[0].config)
        discovered.update({key:value for key,value in config.items() if key!='extras'})
        config=discovered
    return adb, address, config
