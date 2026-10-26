"""Generate the startup task options; keep host and Agent defaults in one place."""
import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
CONNECTION = dict(emulator='mumu', emulator_path='', instance='0', arguments='[]',
                  adb_path='', address='', boot_timeout='180')
GAME = dict(package='com.bilibili.star.bili', user='0', download=True,
            game_timeout='1200', unknown_timeout='60')


def override(node, **values):
    return {node: {'attach': values}}


def build():
    path = ROOT / 'assets/interface.json'
    interface = json.loads(path.read_text(encoding='utf-8'))
    interface['task'] = [task for task in interface['task'] if task['entry'] != 'StartWakeup']
    interface['task'].insert(0, dict(
        name='开始唤醒', entry='StartWakeup', default_check=False,
        description='自动启动模拟器、打开指定游戏应用，处理资源更新、公告和登录奖励，确认到达主页后继续后续任务。\n\n'
                    '**设备与双开**\n\nMuMu 使用 mumu-cli.exe 和多开器实例号；路径留空时尝试默认安装位置。'
                    '旧版 MuMu、雷电、蓝叠等请选择自定义并填写启动程序、参数及 ADB。'
                    '模拟器实例号与 Android 应用双开用户 ID 是两个不同设置。用户 ID 默认 0，打开原应用；应用分身一般从 10 开始。\n\n'
                    '**与上方设备的关系**\n\n控制器类型需为安卓 ADB。MuMu／自定义模式按本任务设置连接设备，'
                    '连接成功后同步上方当前设备，后续任务继续使用该设备。“仅连接已运行设备”留空时沿用上方当前连接。\n\n'
                    '**运行规则**\n\n勾选后优先执行；每个队列只允许一项。资源下载默认开启，最长等待 20 分钟。'
                    '全屏启动页支持限次点击继续。登录验证、客户端安装更新及仍无法通过的未知页面会停止队列并保存截图，不会切换到其他账号。',
        pipeline_override=override('SW_Connection', **CONNECTION) | override('SW_Game', **GAME),
        option=['唤醒模拟器类型', '唤醒连接设置', '唤醒游戏应用', '唤醒自动下载', '唤醒等待设置']))
    options = interface['option']
    options['唤醒模拟器类型'] = dict(type='select', cases=[
        dict(name=label, pipeline_override=override('SW_Connection', emulator=value), option=sub)
        for label, value, sub in [
            ('MuMu（mumu-cli）', 'mumu', ['唤醒MuMu设置']),
            ('自定义模拟器', 'custom', ['唤醒自定义模拟器设置']),
            ('仅连接已运行设备', 'none', [])]])

    def inputs(name, node, fields, description=''):
        definitions, values = [], {}
        for key, label, default, verify in fields:
            field = dict(name=key, label=label, default=default)
            if verify:
                field.update(verify=verify, pattern_msg='请输入有效的非负整数')
            definitions.append(field)
            values[key] = '{' + key + '}'
        options[name] = dict(type='input', inputs=definitions, pipeline_override=override(node, **values))
        if description:
            options[name]['description'] = description

    inputs('唤醒MuMu设置', 'SW_Connection', [
        ('emulator_path', 'MuMu 程序路径', '', None),
        ('instance', '模拟器多开实例号', '0', r'^\d{1,4}$')],
        '默认安装的 MuMu 通常不用填写路径。需要手动指定时，点击“选择…”选择安装目录中的 mumu-cli.exe。')
    options['唤醒MuMu设置']['inputs'][0]['description'] = '可留空自动查找；手动选择时，请选择 MuMu 安装目录中的 mumu-cli.exe。'
    options['唤醒MuMu设置']['inputs'][1]['description'] = '默认 0 表示第一台模拟器；与应用分身的用户 ID 不同。'
    inputs('唤醒自定义模拟器设置', 'SW_Connection', [
        ('emulator_path', '模拟器启动程序完整路径', '', None),
        ('arguments', '启动参数', '[]', None)],
        '参数直接传给程序，不执行 shell。请从该实例的快捷方式或官方说明复制参数。')
    options['唤醒自定义模拟器设置']['inputs'][1]['description'] = '填写 JSON 数组，例如 ["--index", "0"]；没有参数时保持 []。'
    inputs('唤醒连接设置', 'SW_Connection', [
        ('adb_path', 'ADB 程序路径', '', None),
        ('address', '连接地址 / ADB 序列号', '', None)],
        '**MuMu：这两项通常都不用填**，路径会自动查找，连接地址会从上面的实例号读取。\n\n'
        '**仅连接已运行设备：**留空沿用界面当前连接。\n\n'
        '**自定义模拟器：**点击“选择…”选择模拟器自带的 ADB 程序，并填写该实例的 ADB 地址，'
        '格式为 `127.0.0.1:端口`，例如 `127.0.0.1:16384`；如果设备列表显示 `emulator-5554` 等序列号，也可原样填写。'
        '这里不是文件路径，也不是实例号或应用分身用户 ID。')
    options['唤醒连接设置']['inputs'][0]['description'] = (
        'MuMu 留空自动查找；其他模拟器可点击“选择…”选择其自带的 adb.exe 或兼容 ADB 程序。')
    options['唤醒连接设置']['inputs'][1]['description'] = (
        'MuMu 可留空自动读取。手动填写时，从模拟器的 ADB 设置或本程序设备列表复制实际地址／序列号；'
        '常见形式为 127.0.0.1:16384 或 emulator-5554。不同实例的值可能不同。')
    inputs('唤醒游戏应用', 'SW_Game', [
        ('package', '游戏包名', GAME['package'], None),
        ('user', 'Android 用户 ID', GAME['user'], r'^\d{1,5}$')],
        '默认 0 为原应用；应用分身一般从 10 开始，具体以模拟器实际用户 ID 为准。'
        '严格打开指定用户安装的游戏；分身不存在时停止，不回退到原应用。')
    options['唤醒游戏应用']['inputs'][1]['description'] = '默认 0 为原应用；应用分身一般从 10 开始，具体以模拟器实际用户 ID 为准。'
    options['唤醒自动下载'] = dict(type='select', cases=[
        dict(name=name, pipeline_override=override('SW_Game', download=value))
        for name, value in [('允许下载游戏资源', True), ('遇到下载时停止', False)]])
    inputs('唤醒等待设置', 'SW_Game', [
        ('game_timeout', '游戏启动和下载总超时（30–7200 秒）', '1200', r'^\d{2,4}$'),
        ('unknown_timeout', '未知页面等待（5–300 秒）', '60', r'^\d{1,3}$')])
    # A separate input makes the pre-connection timeout visible alongside game waits.
    inputs('唤醒模拟器等待', 'SW_Connection', [
        ('boot_timeout', '模拟器启动超时（10–1800 秒）', '180', r'^\d{2,4}$')])
    interface['task'][0]['option'].append('唤醒模拟器等待')
    path.write_text(json.dumps(interface, ensure_ascii=False, indent=4)+'\n', encoding='utf-8')
    pipeline = dict(StartWakeup=dict(action='Custom', custom_action='StartWakeup'))
    pipeline.update(override('SW_Connection', **CONNECTION) | override('SW_Game', **GAME))
    (ROOT / 'assets/resource/pipeline/wakeup.json').write_text(
        json.dumps(pipeline, ensure_ascii=False, indent=4)+'\n', encoding='utf-8')


if __name__ == '__main__':
    build()
