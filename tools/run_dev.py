"""Launch a local UI or command with persistent, isolated development settings."""
import argparse
import os
from pathlib import Path
import subprocess

ROOT = Path(__file__).resolve().parents[1]


def development_environment(data_dir=None):
    env = os.environ.copy()
    selected = data_dir or env.get('MAABANG_DATA_DIR') or ROOT / '.maabang-dev'
    env['MAABANG_DATA_DIR'] = str(Path(selected).expanduser().resolve())
    env['PYTHONUTF8'] = '1'
    if os.name == 'nt':
        # Keep the short Windows IPC path used by the production launcher.
        temp = Path.home() / '.maabang/temp'
        temp.mkdir(parents=True, exist_ok=True)
        env['TEMP'] = env['TMP'] = str(temp)
    return env


def ui_executable(path):
    path = Path(path).expanduser().resolve()
    if path.is_dir():
        path = path / 'MFAAvalonia.exe' if path.name == 'app' else path / 'app/MFAAvalonia.exe'
    elif path.name.lower() == 'maabang.exe':
        path = path.parent / 'app/MFAAvalonia.exe'
    if path.name.lower() != 'mfaavalonia.exe' or not path.is_file():
        raise ValueError(f'找不到开发界面：{path}')
    return path


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--app', type=Path, help='本地构建包目录或 MaaBanG.exe 路径')
    parser.add_argument('--data-dir', type=Path, help='覆盖默认 .maabang-dev，可用于全新配置测试')
    parser.add_argument('command', nargs=argparse.REMAINDER, help='-- 后指定开发命令')
    args = parser.parse_args(argv)
    command = args.command[1:] if args.command[:1] == ['--'] else args.command
    if bool(args.app) == bool(command):
        parser.error('请指定 --app 或 -- 后的开发命令，二选一')
    if args.app:
        try:
            executable = ui_executable(args.app)
        except ValueError as error:
            parser.error(str(error))
        # Bypass portable-config migration: a new development profile starts empty.
        command, cwd = [str(executable)], executable.parent
    else:
        cwd = ROOT
    env = development_environment(args.data_dir)
    print(f'开发数据目录：{env["MAABANG_DATA_DIR"]}', flush=True)
    return subprocess.call(command, cwd=cwd, env=env)


if __name__ == '__main__':
    raise SystemExit(main())
