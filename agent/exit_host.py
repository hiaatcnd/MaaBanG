"""Desktop exit task. Never boots an emulator or starts an Android user.

stdin: resolved task options; stdout: one result JSON; stderr: progress.
Every attempted command and observed state is recorded under debug/exit.
"""
from datetime import datetime
import json
import os
from pathlib import Path
import re
import sys
import time
import uuid

from wakeup_host import AndroidGame, Connection, WakeupError, command, executable, integer, pause


class ExitFlow:
    def __init__(self, values, output, check_stop=lambda: None):
        self.values = values
        self.output = Path(output)
        self.output.mkdir(parents=True, exist_ok=True)
        self.check_stop = check_stop
        self.report = dict(status='running', options=values, started_at=datetime.now().isoformat())
        self.save()

    def save(self):
        (self.output/'report.json').write_text(json.dumps(self.report, ensure_ascii=False, indent=2), encoding='utf-8')

    def record(self, event, **data):
        with (self.output/'trace.jsonl').open('a', encoding='utf-8') as stream:
            stream.write(json.dumps(dict(time=datetime.now().isoformat(), event=event, **data), ensure_ascii=False)+'\n')

    def log(self, message):
        self.record('progress', message=message)
        print('[退出] '+message, file=sys.stderr, flush=True)

    def run_command(self, args, **kwargs):
        self.record('command', args=[str(arg) for arg in args])
        result = command(args, check_stop=self.check_stop, **kwargs)
        self.record('result', output=result)
        return result

    def find_cli(self):
        explicit = str(self.values.get('emulator_path', '')).strip().strip('"')
        if explicit:
            cli = executable(explicit, 'MuMu CLI')
            if cli.name.lower() != 'mumu-cli.exe':
                raise WakeupError('MuMu 路径应选择 mumu-cli.exe')
            return cli
        adb = str(self.values.get('adb_path', '')).strip().strip('"')
        candidates = [Path(os.path.expandvars(adb)).parent/'mumu-cli.exe'] if adb else []
        candidates.append(Path(os.environ.get('ProgramFiles', 'C:/Program Files'))/'Netease/MuMu/nx_main/mumu-cli.exe')
        for candidate in candidates:
            if candidate.is_file():
                return executable(candidate, 'MuMu CLI')
        raise WakeupError('找不到 mumu-cli.exe，请在退出目标设置中选择路径；实例和程序退出目前支持 MuMu')

    def info(self, cli, index='all'):
        result = json.loads(self.run_command([cli, 'info', '--vmindex', str(index)]))
        if not isinstance(result, dict):
            raise WakeupError('MuMu 返回的实例信息无效')
        if 'index' in result:
            result = {str(result['index']): result}
        instances = {}
        for key, vm in result.items():
            if not str(key).isdigit() or not isinstance(vm, dict) or vm.get('is_main'):
                continue
            if vm.get('error_code', 0) != 0 or not isinstance(vm.get('is_process_started'), bool):
                raise WakeupError(f'无法确认 MuMu 实例 {key} 的运行状态')
            instances[str(key)] = vm
        if str(index) != 'all' and str(index) not in instances:
            raise WakeupError(f'MuMu 中不存在实例 {index}')
        return instances

    @staticmethod
    def address(vm):
        return f"{vm.get('adb_host_ip', '127.0.0.1')}:{vm['adb_port']}" if vm.get('adb_port') else ''

    def select_vm(self, cli, kind, address):
        if kind == 'mumu':
            index = str(integer(self.values.get('instance', '0'), '模拟器实例号', 0, 9999))
            vm = self.info(cli, index)[index]
            detected = self.address(vm)
            if address and detected and address != detected:
                raise WakeupError(f'实例 {index} 的地址是 {detected}，与填写的 {address} 不符，未执行关闭')
        else:
            if not address:
                raise WakeupError('没有当前设备，请选择指定目标并填写 MuMu 实例号')
            matches = [(index, vm) for index, vm in self.info(cli).items() if self.address(vm) == address]
            if len(matches) != 1:
                raise WakeupError('当前设备不能唯一对应 MuMu 实例，请选择指定目标；未关闭任何实例')
            index, vm = matches[0]
        self.report.update(instance=index, address=self.address(vm))
        self.save()
        return index, vm

    def close_app(self, connection):
        game = AndroidGame(connection, str(self.values.get('package', 'com.bilibili.star.bili')),
                           self.values.get('user', '0'), self.check_stop)
        # Reuse the safely quoted, exact-user Android shell transport, with recording.
        import shlex
        def shell(*args):
            return self.run_command([connection.adb_path, '-s', connection.address, 'shell', shlex.join(args)])
        if ':' in connection.address:
            self.run_command([connection.adb_path, 'connect', connection.address], timeout=10)
        state = self.run_command([connection.adb_path, '-s', connection.address, 'get-state'])
        if state != 'device':
            raise WakeupError('目标 ADB 设备不可用，无法确认应用状态；不会启动模拟器')
        users = shell('pm', 'list', 'users')
        if not re.search(r'UserInfo\{' + str(game.user) + ':', users):
            raise WakeupError(f'Android 用户 {game.user} 不存在，未关闭其他用户的应用')
        packages = shell('pm', 'list', 'packages', '--user', str(game.user), game.package)
        if 'package:'+game.package not in packages.splitlines():
            raise WakeupError(f'用户 {game.user} 未安装 {game.package}')
        self.log(f'关闭应用 {game.package}（Android 用户 {game.user}）')
        result = shell('am', 'force-stop', '--user', str(game.user), game.package)
        if re.search(r'Error:|Exception|Permission Denial', result, re.I):
            raise WakeupError(result[:500])
        details = shell('dumpsys', 'package', game.package)
        if not re.search(r'User\s+'+str(game.user)+r':[^\r\n]*\bstopped=true\b', details):
            raise WakeupError('关闭命令已发送，但未确认指定用户的应用已停止，请检查退出记录')
        self.report.update(package=game.package, user=game.user, address=connection.address)

    def wait_off(self, cli, indexes, deadline):
        while True:
            self.check_stop()
            states = self.info(cli)
            if any(index not in states for index in indexes):
                raise WakeupError('关闭后无法查询目标实例状态，请检查退出记录')
            if all(not states[index]['is_process_started'] for index in indexes):
                return
            if time.monotonic() >= deadline:
                raise WakeupError('等待模拟器关闭超时，请检查是否有确认窗口；未强杀进程')
            pause(.5, self.check_stop)

    def run(self):
        try:
            scope = self.values.get('scope', 'app')
            kind = self.values.get('emulator', 'none')
            if scope not in ('app', 'instance', 'program') or kind not in ('mumu', 'none', 'custom'):
                raise WakeupError('退出范围或模拟器类型无效')
            timeout = integer(self.values.get('shutdown_timeout', '60'), '退出超时', 5, 300)
            address = str(self.values.get('address', '')).strip()
            if address and (address.startswith('-') or re.search(r'\s', address)):
                raise WakeupError('连接地址格式无效')
            cli = None
            if kind == 'mumu' or scope != 'app':
                # A custom launcher is not necessarily MuMu. Do not infer by process name.
                if kind == 'custom' and scope != 'app':
                    raise WakeupError('自定义模拟器目前支持关闭应用；关闭实例或程序请指定 MuMu 目标')
                cli = self.find_cli()
                index, vm = self.select_vm(cli, kind, address)
                address = self.address(vm) or address
                if scope != 'program' and not vm['is_process_started']:
                    self.log(f'MuMu 实例 {index} 已关闭')
                    self.report['already_stopped'] = True
                    return
            if scope == 'app':
                if not address:
                    raise WakeupError('没有退出目标，请选择当前 ADB 设备或填写连接地址')
                adb = self.values.get('adb_path') or (str(cli.parent/'adb.exe') if cli else '')
                self.close_app(Connection(str(executable(adb, 'ADB 程序')), address))
            elif scope == 'instance':
                self.report['closed_addresses'] = [address] if address else []
                self.log(f'关闭 MuMu 实例 {index}，其他实例保持运行')
                self.run_command([cli, 'control', '--vmindex', index, 'shutdown'])
                self.wait_off(cli, [index], time.monotonic()+timeout)
            else:
                # Full program exit intentionally covers all VMs of this installation.
                states = self.info(cli)
                indexes = [i for i, state in states.items() if state['is_process_started']]
                self.report['instances'] = indexes
                self.report['closed_addresses'] = [self.address(state) for state in states.values() if self.address(state)]
                self.save()
                self.log('关闭此 MuMu 安装的所有实例并退出主程序：'+(', '.join(indexes) or '无运行实例'))
                for instance in indexes:
                    self.run_command([cli, 'control', '--vmindex', instance, 'shutdown'])
                self.wait_off(cli, indexes, time.monotonic()+timeout)
                self.run_command([cli, 'main', 'close'], timeout=timeout)
                self.report['main_close_accepted'] = True
        except BaseException as error:
            self.report.update(status='error', error=str(error))
            raise
        else:
            self.report['status'] = 'finished'
        finally:
            # An already-closed target is successful, without relaunching it.
            if self.report.get('already_stopped'):
                self.report['status'] = 'finished'
            self.report['finished_at'] = datetime.now().isoformat()
            self.save()


def main():
    for stream in (sys.stdout, sys.stderr):
        if hasattr(stream, 'reconfigure'):
            stream.reconfigure(encoding='utf-8')
    output = Path('debug/exit')/(datetime.now().strftime('%Y%m%d-%H%M%S')+'-'+uuid.uuid4().hex[:8])
    try:
        flow = ExitFlow(json.load(sys.stdin), output)
        flow.log('记录目录：'+str(output.resolve()))
        flow.run()
        print(json.dumps(flow.report, ensure_ascii=False), flush=True)
        return 0
    except Exception as error:
        print('[退出] '+str(error), file=sys.stderr, flush=True)
        return 1


if __name__ == '__main__':
    sys.exit(main())
