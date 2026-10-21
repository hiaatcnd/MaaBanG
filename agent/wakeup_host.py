"""Cancellable emulator bootstrap and exact Android-user launch (stdlib only).

The desktop calls this before creating its Maa controller. stdout is one JSON
result; progress goes to stderr. No shell commands, device guessing or ADB reset.
"""
from dataclasses import dataclass
import json
import os
from pathlib import Path
import re
import shlex
import subprocess
import sys
import time


class WakeupError(RuntimeError):
    pass


def progress(message):
    print('[开始唤醒] ' + message, file=sys.stderr, flush=True)


def command(args, *, timeout=15, check_stop=lambda: None, check=True):
    check_stop()
    with subprocess.Popen([str(x) for x in args], stdout=subprocess.PIPE,
                          stderr=subprocess.PIPE, encoding='utf-8', errors='replace',
                          creationflags=getattr(subprocess, 'CREATE_NO_WINDOW', 0)) as process:
        deadline = time.monotonic() + timeout
        try:
            while True:
                check_stop()
                try:
                    out, err = process.communicate(timeout=min(.25, max(.01, deadline-time.monotonic())))
                    break
                except subprocess.TimeoutExpired:
                    if time.monotonic() >= deadline:
                        raise WakeupError(f'命令超时：{Path(str(args[0])).name}')
        except BaseException:
            process.kill()
            process.communicate()
            raise
    if check and process.returncode:
        raise WakeupError((err or out).strip()[:500] or f'命令失败：{process.returncode}')
    return out.strip()


def pause(seconds, check_stop):
    deadline = time.monotonic() + seconds
    while time.monotonic() < deadline:
        check_stop()
        time.sleep(min(.2, max(0, deadline-time.monotonic())))


def integer(value, name, minimum, maximum):
    if isinstance(value, bool) or not re.fullmatch(r'[0-9]+', str(value)):
        raise WakeupError(f'{name}必须为整数')
    result = int(value)
    if not minimum <= result <= maximum:
        raise WakeupError(f'{name}须在 {minimum}–{maximum} 之间')
    return result


def executable(value, name):
    path = Path(os.path.expandvars(str(value).strip().strip('"'))).expanduser()
    if not path.is_absolute() or not path.is_file():
        raise WakeupError(f'请填写有效的{name}绝对路径：{value}')
    if os.name == 'nt' and path.suffix.lower() != '.exe':
        raise WakeupError(f'{name}须指向 .exe 程序')
    return path


@dataclass(frozen=True)
class Connection:
    adb_path: str
    address: str


def bootstrap(values, check_stop=lambda: None):
    kind = values.get('emulator', 'mumu')
    if kind not in ('mumu', 'custom', 'none'):
        raise WakeupError('不支持的模拟器类型')
    timeout = integer(values.get('boot_timeout', 180), '启动超时', 10, 1800)
    index = integer(values.get('instance', 0), '模拟器实例号', 0, 9999)
    address = str(values.get('address', '')).strip()
    adb_value = os.path.expandvars(str(values.get('adb_path', '')).strip().strip('"'))
    emulator_value = os.path.expandvars(str(values.get('emulator_path', '')).strip().strip('"'))
    cli = None
    if kind == 'mumu':
        # Current MuMu uses mumu-cli; old MuMu versions can use custom launch.
        candidates = [Path(emulator_value)] if emulator_value else []
        if adb_value:
            candidates.append(Path(adb_value).parent / 'mumu-cli.exe')
        candidates.append(Path(os.environ.get('ProgramFiles', 'C:/Program Files')) /
                          'Netease/MuMu/nx_main/mumu-cli.exe')
        cli = next((p for p in candidates if p.is_file() and p.name.lower() == 'mumu-cli.exe'), None)
        if cli is None:
            raise WakeupError('找不到 mumu-cli.exe，请指定路径；旧版 MuMu 请选择自定义模拟器')
        cli = executable(cli, 'MuMu CLI')
        if not adb_value:
            adb_value = str(cli.parent / 'adb.exe')
    adb = executable(adb_value, 'ADB 程序')
    if kind == 'custom':
        emulator = executable(emulator_value, '模拟器程序')
        try:
            arguments = json.loads(values.get('arguments', '[]') or '[]')
        except (ValueError, TypeError) as exc:
            raise WakeupError('启动参数须为 JSON 字符串数组，如 ["--index", "0"]') from exc
        if not isinstance(arguments, list) or not all(isinstance(a, str) for a in arguments):
            raise WakeupError('启动参数须为 JSON 字符串数组')
    if not address and kind != 'mumu':
        raise WakeupError('请指定连接地址，不会自动选择其他设备')
    if address and (address.startswith('-') or re.search(r'\s', address)):
        raise WakeupError('连接地址格式无效')

    deadline = time.monotonic() + timeout
    def info():
        raw = command([cli, 'info', '--vmindex', str(index)], check_stop=check_stop)
        try:
            result = json.loads(raw)
            vm = result if str(result.get('index')) == str(index) else result[str(index)]
        except (ValueError, KeyError, TypeError) as exc:
            raise WakeupError(f'MuMu 中不存在实例 {index}，请检查多开器编号') from exc
        port = vm.get('adb_port')
        detected = f"{vm.get('adb_host_ip', '127.0.0.1')}:{port}" if port else ''
        if address and detected and address != detected:
            raise WakeupError(f'实例 {index} 的地址是 {detected}，与配置 {address} 不符')
        return vm, detected

    if cli:
        vm, detected = info()
        launched = False
        if not vm.get('is_process_started'):
            progress(f'正在启动 MuMu 实例 {index}')
            command([cli, 'control', '--vmindex', str(index), 'launch'], check_stop=check_stop)
            launched = True
        while time.monotonic() < deadline:
            vm, detected = info()
            # Shutdown can finish between the initial probe and this readiness check.
            if not vm.get('is_process_started') and not launched:
                progress(f'正在启动 MuMu 实例 {index}')
                command([cli, 'control', '--vmindex', str(index), 'launch'], check_stop=check_stop)
                launched = True
            if detected and vm.get('is_android_started'):
                address = address or detected
                break
            pause(1, check_stop)
        else:
            raise WakeupError('等待 MuMu Android 启动超时')
    elif kind == 'custom':
        state = command([adb, '-s', address, 'get-state'], check=False, check_stop=check_stop)
        if state != 'device':
            progress('正在启动自定义模拟器')
            # Leave the emulator running when the task is canceled.
            subprocess.Popen([str(emulator), *arguments], cwd=emulator.parent,
                             stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                             creationflags=getattr(subprocess, 'CREATE_NO_WINDOW', 0))

    progress(f'等待 ADB 和 Android 就绪：{address}')
    while time.monotonic() < deadline:
        check_stop()
        try:
            if ':' in address:
                command([adb, 'connect', address], check=False, timeout=5, check_stop=check_stop)
            boot = command([adb, '-s', address, 'shell', 'getprop', 'sys.boot_completed'],
                           check=False, timeout=5, check_stop=check_stop)
            if boot == '1':
                return Connection(str(adb), address)
        except WakeupError:
            pass
        pause(1, check_stop)
    raise WakeupError(f'连接超时：{address}；请检查模拟器 ADB 调试和连接地址')


class AndroidGame:
    def __init__(self, connection, package='com.bilibili.star.bili', user='0', check_stop=lambda: None):
        if not re.fullmatch(r'[A-Za-z][\w]*(?:\.[A-Za-z][\w]*)+', package):
            raise WakeupError('游戏包名无效')
        self.connection, self.package = connection, package
        self.user = integer(user, 'Android 用户 ID', 0, 99999)
        self.check_stop = check_stop
        self.observe = lambda: None

    def shell(self, *args, timeout=15):
        # adb joins shell arguments; quote activity aliases containing '$' too.
        return command([self.connection.adb_path, '-s', self.connection.address, 'shell', shlex.join(args)],
                       timeout=timeout, check_stop=self.check_stop)

    def foreground(self):
        output = self.shell('dumpsys', 'activity', 'activities')
        for line in output.splitlines():
            if 'topResumedActivity=' in line or 'mResumedActivity:' in line or 'mResumedActivity=' in line:
                match = re.search(r'\bu(\d+)\s+([\w.]+)/', line)
                if match:
                    return int(match[1]), match[2]
        return None

    def selected_foreground(self):
        return self.foreground() == (self.user, self.package)

    def launch(self):
        self.observe()
        users = self.shell('pm', 'list', 'users')
        if not re.search(r'UserInfo\{' + str(self.user) + ':', users):
            raise WakeupError(f'Android 用户 {self.user} 不存在，请检查应用双开设置')
        installed = self.shell('pm', 'list', 'packages', '--user', str(self.user), self.package)
        if 'package:' + self.package not in installed.splitlines():
            raise WakeupError(f'用户 {self.user} 未安装 {self.package}，不会改开另一个账号')
        if self.selected_foreground():
            progress(f'游戏已在前台（Android 用户 {self.user}）')
            return
        if self.user and not re.search(r'UserInfo\{' + str(self.user) + r':[^\n]+\brunning\b', users):
            # MuMu keeps clone profiles stopped until first use after boot.
            # Starting an activity in that profile can otherwise silently do nothing.
            output = self.shell('am', 'start-user', str(self.user))
            if 'Success' not in output:
                raise WakeupError(f'无法启动分身用户 {self.user}：{output[:300]}')
            for _ in range(30):
                users = self.shell('pm', 'list', 'users')
                if re.search(r'UserInfo\{' + str(self.user) + r':[^\n]+\brunning\b', users):
                    break
                pause(.5, self.check_stop)
            else:
                raise WakeupError('等待分身用户启动超时')
        result = self.shell('cmd', 'package', 'resolve-activity', '--brief', '--user', str(self.user),
                            '-a', 'android.intent.action.MAIN', '-c', 'android.intent.category.LAUNCHER', self.package)
        components = [line.strip() for line in result.splitlines()
                      if re.fullmatch(re.escape(self.package) + r'/[\w.$]+', line.strip())]
        if len(components) != 1:
            raise WakeupError('无法确定游戏启动 Activity')
        progress(f'正在打开游戏（Android 用户 {self.user}）')
        output = self.shell('am', 'start', '--user', str(self.user),
                            '-a', 'android.intent.action.MAIN', '-c', 'android.intent.category.LAUNCHER',
                            '-n', components[0])
        if re.search(r'Error:|Exception|Permission Denial', output, re.I):
            raise WakeupError(output[:500])
        # A successful am command alone does not prove the clone was selected.
        for _ in range(30):
            self.observe()
            if self.selected_foreground():
                return
            pause(.5, self.check_stop)
        raise WakeupError(f'未确认游戏属于用户 {self.user}；可能出现双开选择器，请保留现场')


def main():
    for stream in (sys.stdout, sys.stderr):
        if hasattr(stream, 'reconfigure'):
            stream.reconfigure(encoding='utf-8')
    try:
        values = json.load(sys.stdin)
        result = bootstrap(values)
        print(json.dumps(vars(result), ensure_ascii=False), flush=True)
        return 0
    except Exception as exc:
        progress(str(exc))
        return 1


if __name__ == '__main__':
    sys.exit(main())
