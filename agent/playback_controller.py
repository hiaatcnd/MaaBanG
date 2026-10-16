"""Reuse MaaFramework device configuration for low-latency chart playback."""
from copy import deepcopy
from dataclasses import dataclass
import ntpath
from operator import index


_UINT64 = (1 << 64) - 1
_INPUT_EXTRAS = 8
_INPUT_ADB = 1
_INPUT_DEFAULT = _UINT64 & ~_INPUT_EXTRAS
_CONTACT_METHODS = 2 | 4 | _INPUT_EXTRAS


@dataclass(frozen=True)
class PlaybackOptions:
    adb_path: str
    address: str
    config: dict
    # Candidates, not the backend that won MaaFramework initialization.
    input_methods: int
    input_source: str
    configured_input_methods: int | None = None
    discovered_input_methods: int | None = None


def _input_mask(value):
    if value is None:
        return None
    try:
        mask = index(value)
    except TypeError:
        raise ValueError('ADB input_methods 必须是整数位掩码') from None
    if isinstance(value, bool) or not -(1 << 63) <= mask <= _UINT64:
        raise ValueError('ADB input_methods 必须是 64 位整数位掩码')
    return mask


def _config(value):
    if value is None:
        return {}
    if not isinstance(value, dict):
        raise ValueError('ADB 连接配置格式无效')
    if 'extras' in value and not isinstance(value['extras'], dict):
        raise ValueError('ADB extras 配置格式无效')
    return deepcopy(value)


def _merge_config(discovered, explicit):
    """Preserve overrides without borrowing capabilities from another instance."""
    discovered = _config(discovered)
    existing_extras = explicit.get('extras', {})
    found_extras = discovered.get('extras', {})
    existing_vendors = {name for name, cfg in existing_extras.items()
                        if isinstance(cfg, dict) and cfg.get('enable') is True}
    found_vendors = {name for name, cfg in found_extras.items()
                     if isinstance(cfg, dict) and cfg.get('enable') is True}
    if existing_vendors and found_vendors and existing_vendors != found_vendors:
        raise ValueError('当前 extras 与设备发现的模拟器类型冲突，请刷新并确认设备')
    for vendor in existing_extras.keys() & found_extras.keys():
        old, new = existing_extras[vendor], found_extras[vendor]
        if not isinstance(old, dict) or not isinstance(new, dict):
            continue
        for key in ('path', 'index'):
            if key not in old or key not in new:
                continue
            a, b = old[key], new[key]
            if key == 'path' and isinstance(a, str) and isinstance(b, str):
                a, b = ntpath.normcase(ntpath.normpath(a)), ntpath.normcase(ntpath.normpath(b))
            if a != b:
                raise ValueError('当前 extras 与设备发现的 path/index 冲突，请刷新并确认设备')

    def overlay(base, values):
        for key, value in values.items():
            if isinstance(value, dict) and isinstance(base.get(key), dict):
                overlay(base[key], value)
            else:
                base[key] = deepcopy(value)
        return base

    return overlay(discovered, explicit)


def playback_options(info, find_devices=None):
    """Resolve native candidates before arming, without probing any input backend.

    Missing input methods and the signed/unsigned Default sentinel refresh full
    Toolkit discovery. All with existing extras already permits native detection.
    Concrete masks are explicit: in particular 7 is not the Default sentinel.
    Toolkit owns emulator/version detection; never infer input support from a
    vendor name. Its initialization priority and fallback remain native, except
    AdbShell is excluded in automatic mode because it lacks per-contact input.
    Runtime input failure must not cause backend switching or touch replay.
    """
    if info.get('type') != 'adb':
        raise ValueError('谱面演出需要支持截图增强和多点触控的 ADB 连接')
    adb, address = info['adb_path'], info['adb_serial']
    config = _config(info.get('config'))
    screenshot = info.get('screencap_methods')
    if screenshot is not None and not int(screenshot) & 64:
        raise ValueError('谱面演出需要截图增强，请在连接设置中启用 EmulatorExtras')
    configured = _input_mask(info.get('input_methods'))
    normalized = None if configured is None else configured & _UINT64
    automatic = normalized in (None, _INPUT_DEFAULT, _UINT64)
    discovered = None
    source = 'connection_explicit'

    if automatic and (normalized != _UINT64 or not config.get('extras')):
        if find_devices is None:
            from maa.toolkit import Toolkit
            find_devices = Toolkit.find_adb_devices
        # A specified adb path skips vendor-tool discovery on some platforms.
        # Always use the complete finder and exact address, never a guessed port.
        matches = [device for device in find_devices() if device.address == address]
        if len(matches) > 1:
            raise ValueError('当前 ADB 地址对应多个设备发现结果，请刷新并确认唯一设备')
        if matches:
            device = matches[0]
            config = _merge_config(device.config, config)
            discovered = _input_mask(device.input_methods)
            if discovered is None:
                raise ValueError('设备发现未返回 input_methods，请更新 MaaFramework 并刷新设备')
            requested = discovered & _UINT64
            source = 'toolkit_discovery'
        elif config.get('extras'):
            # Manually configured/remote devices may not appear in local discovery.
            # Keep their original compatibility policy; do not guess enhancement.
            requested = _INPUT_DEFAULT if normalized is None else normalized
            source = 'connection_default_no_match'
        else:
            raise ValueError('未找到当前 ADB 地址的唯一截图增强配置，请刷新设备并启用截图增强')
    else:
        if not config.get('extras'):
            _, _, config = controller_config(info, find_devices)
            config = _config(config)
        requested = normalized
        if automatic:
            source = 'connection_all'

    if not config.get('extras'):
        raise ValueError('谱面演出需要截图增强，请刷新设备并启用 EmulatorExtras')
    androws = config['extras'].get('androws', {})
    virtual_display = isinstance(androws, dict) and androws.get('enable') is True
    if virtual_display and requested != _INPUT_EXTRAS:
        raise ValueError('应用宝虚拟显示需要仅使用 EmulatorExtras，不能回退到普通输入；请刷新或修正连接配置')
    if automatic:
        requested &= ~_INPUT_ADB
    elif requested & _INPUT_ADB:
        raise ValueError('谱面演出需要分触点输入，显式 input_methods 含不支持的 AdbShell；请选 MaaTouch、MinitouchAndAdbKey 或 EmulatorExtras')
    if not requested & _CONTACT_METHODS:
        raise ValueError('谱面演出需要启用 MaaTouch、MinitouchAndAdbKey 或 EmulatorExtras，当前输入配置不支持分触点输入')
    return PlaybackOptions(adb, address, config, requested, source, configured, discovered)


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
