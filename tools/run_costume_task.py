"""Direct SDK runner, also usable where Agent IPC is unavailable."""
import argparse
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "agent"))

from costume_policy import parse_target, scope_members


def main():
    for stream in (sys.stdout, sys.stderr):
        if hasattr(stream, "reconfigure"):
            stream.reconfigure(encoding="utf-8", errors="replace")
    parser = argparse.ArgumentParser(description="按目标数量补齐每位角色的默认3D服装")
    parser.add_argument("x", type=parse_target, help="每位角色的目标数量，0–999")
    parser.add_argument("--inspect-only", action="store_true", help="仅检查并领取收集奖励，不消耗服装道具")
    parser.add_argument("--adb", required=True, help="adb.exe 路径")
    parser.add_argument("--address", default="127.0.0.1:16416")
    selection = parser.add_mutually_exclusive_group()
    selection.add_argument("--band", help="指定乐队，例如 Roselia 或 roselia")
    selection.add_argument("--member", help="指定成员，例如 牛込里美")
    args = parser.parse_args()
    kind = "band" if args.band is not None else "member" if args.member is not None else "all"
    value = args.band or args.member or ""
    try:
        scope_members(kind, value)
    except ValueError as exc:
        parser.error(str(exc))

    from maa.resource import Resource
    from maa.controller import AdbController
    from maa.tasker import Tasker
    from maa.toolkit import Toolkit
    from costume_unlock import UnlockDefault3DCostumes

    Toolkit.init_option(ROOT)
    resource = Resource()
    if not resource.post_bundle(ROOT / "assets/resource").wait().succeeded:
        parser.exit(1, "资源加载失败\n")
    resource.register_custom_action("UnlockDefault3DCostumes", UnlockDefault3DCostumes())
    controller = AdbController(args.adb, args.address)
    controller.set_screenshot_target_short_side(720)
    if not controller.post_connection().wait().succeeded:
        parser.exit(1, "ADB连接失败\n")
    tasker = Tasker()
    tasker.bind(resource, controller)
    print(f"目标 x={args.x}；模式：{'仅检查' if args.inspect_only else '实际解锁'}", flush=True)
    job = tasker.post_task("UnlockCostumes", {
        "UnlockCostumes": {"custom_action_param": {"target": args.x}},
        "CU_ExecutionOptions": {"attach": {"inspect_only": args.inspect_only}},
        "CU_ScopeMode": {"attach": {"kind": kind}},
        "CU_SelectedBand": {"attach": {"value": args.band or "poppin_party"}},
        "CU_SelectedMember": {"attach": {"value": args.member or "牛込里美"}},
    })
    try:
        while not job.done:
            time.sleep(0.2)
        succeeded = job.succeeded
    except KeyboardInterrupt:
        tasker.post_stop().wait()
        return 130
    print("任务完成" if succeeded else "任务停止，请查看 debug/costume_unlock 报告", flush=True)
    return 0 if succeeded else 1


if __name__ == "__main__":
    sys.exit(main())
