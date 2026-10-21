"""Generate the final desktop exit task and its explicit target/scope options."""
import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SETTINGS = dict(scope='app', target='follow', shutdown_timeout='60')
CONNECTION = dict(emulator='mumu', emulator_path='', instance='0', adb_path='', address='')
GAME = dict(package='com.bilibili.star.bili', user='0')


def override(node, **values):
    return {node: {'attach': values}}


def build():
    path = ROOT/'assets/interface.json'
    interface = json.loads(path.read_text(encoding='utf-8'))
    interface['task'] = [task for task in interface['task'] if task['entry'] != 'ExitGame']
    defaults = override('EX_Settings', **SETTINGS) | override('EX_Connection', **CONNECTION) | override('EX_Game', **GAME)
    interface['task'].append(dict(name='退出', entry='ExitGame', default_check=False,
        description='在本轮普通任务结束后执行，可单独勾选运行；不会为退出而启动模拟器。停止或中断队列不会强制执行此任务。\n\n'
                    '**退出范围**\n\n关闭应用：只结束指定 Android 用户下的游戏，支持原应用与分身。\n\n'
                    '关闭模拟器实例：关闭指定 MuMu 实例，其他实例保留。\n\n'
                    '关闭模拟器程序：关闭此 MuMu 安装的全部实例，再退出主程序；其他正在运行的实例也会结束。\n\n'
                    '**退出目标**\n\n默认沿用本轮勾选的“开始唤醒”中的设备、包名和用户 ID；没有唤醒任务时使用上方当前 ADB 设备，'
                    '游戏默认原应用（用户 0）。关闭实例时按当前地址匹配 MuMu，无法唯一确定时停止。可选择指定目标单独设置。\n\n'
                    '实例和程序关闭目前适配 MuMu（mumu-cli）；其他模拟器可通过 ADB 关闭应用。执行记录保存在 debug/exit。',
        pipeline_override=defaults, option=['退出范围', '退出目标', '退出等待设置']))
    options = interface['option']
    options['退出范围'] = dict(type='select', cases=[
        dict(name=name, pipeline_override=override('EX_Settings', scope=value)) for name, value in [
            ('关闭应用', 'app'), ('关闭模拟器实例', 'instance'), ('关闭模拟器程序（全部实例）', 'program')]])
    options['退出目标'] = dict(type='select', cases=[
        dict(name='沿用本轮唤醒 / 当前设备', pipeline_override=override('EX_Settings', target='follow')),
        dict(name='指定退出目标', pipeline_override=override('EX_Settings', target='manual'),
             option=['退出模拟器类型', '退出连接设置', '退出游戏应用'])])
    options['退出模拟器类型'] = dict(type='select', cases=[
        dict(name='MuMu（mumu-cli）', pipeline_override=override('EX_Connection', emulator='mumu'), option=['退出MuMu设置']),
        dict(name='仅使用 ADB（关闭应用）', pipeline_override=override('EX_Connection', emulator='none'))])

    def inputs(name, node, fields):
        options[name] = dict(type='input', inputs=[dict(name=key, label=label, default=default,
            description=description, **(dict(verify=r'^\d{1,5}$', pattern_msg='请输入非负整数') if key in ('instance', 'user', 'shutdown_timeout') else {}))
            for key, label, default, description in fields],
            pipeline_override=override(node, **{field[0]:'{'+field[0]+'}' for field in fields}))
    inputs('退出MuMu设置', 'EX_Connection', [
        ('emulator_path', 'MuMu 程序路径', '', '留空自动查找，或选择 MuMu 安装目录中的 mumu-cli.exe。'),
        ('instance', '模拟器多开实例号', '0', '从 0 开始。关闭实例只影响此编号；关闭程序会结束该安装下的全部实例。')])
    inputs('退出连接设置', 'EX_Connection', [
        ('adb_path', 'ADB 程序路径', '', 'MuMu 可留空自动查找；仅使用 ADB 时留空沿用当前设备。'),
        ('address', '连接地址 / ADB 序列号', '', 'MuMu 可留空，按实例号读取。仅使用 ADB 时留空沿用当前设备，也可填写实际地址，如 127.0.0.1:16384。')])
    inputs('退出游戏应用', 'EX_Game', [
        ('package', '游戏包名', GAME['package'], '仅“关闭应用”使用。'),
        ('user', 'Android 用户 ID', '0', '默认 0 为原应用，应用分身一般从 10 开始；只关闭此用户的应用。')])
    inputs('退出等待设置', 'EX_Settings', [
        ('shutdown_timeout', '等待关闭超时（秒）', '60', '5–300 秒。超时保留错误记录，不强杀其他进程。')])
    path.write_text(json.dumps(interface, ensure_ascii=False, indent=4)+'\n', encoding='utf-8')
    (ROOT/'assets/resource/pipeline/exit.json').write_text(json.dumps(
        dict(ExitGame=dict(action='Custom', custom_action='ExitGame')) | defaults,
        ensure_ascii=False, indent=4)+'\n', encoding='utf-8')


if __name__ == '__main__':
    build()
