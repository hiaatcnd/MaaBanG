"""Direct SDK runner for the daily tasks (same actions as the UI)."""
import argparse
from pathlib import Path
import sys
import time

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT/'agent'))


def main():
    for stream in (sys.stdout,sys.stderr):
        if hasattr(stream,'reconfigure'):
            stream.reconfigure(encoding='utf-8',errors='replace')
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('task',choices=['ClaimHomeGifts','ClaimHomeMissions','ExchangeMichelle','DailyFreeRecruit'])
    parser.add_argument('--adb',required=True)
    parser.add_argument('--address',default='127.0.0.1:16416')
    parser.add_argument('--categories',nargs='+',choices=['成员','表情','服装','背景','其他'],
                        help='贴纸交换分类，可填写多个；省略时全部交换')
    args = parser.parse_args()
    if args.categories is not None and args.task != 'ExchangeMichelle':
        parser.error('--categories 仅适用于 ExchangeMichelle')
    from maa.resource import Resource
    from maa.controller import AdbController
    from maa.tasker import Tasker
    from maa.toolkit import Toolkit
    import daily_tasks

    Toolkit.init_option(ROOT)
    resource=Resource()
    assert resource.post_bundle(ROOT/'assets/resource').wait().succeeded
    resource.register_custom_action(args.task,getattr(daily_tasks,args.task)())
    controller=AdbController(args.adb,args.address)
    controller.set_screenshot_target_short_side(720)
    assert controller.post_connection().wait().succeeded
    tasker=Tasker()
    tasker.bind(resource,controller)
    from daily_policy import EXCHANGE_CATEGORIES, EXCHANGE_OPTION_NODES
    overrides = {}
    if args.task == 'ExchangeMichelle':
        selected = args.categories if args.categories is not None else EXCHANGE_CATEGORIES
        overrides = {node: {'attach': {'enabled': category in selected}}
                     for category, node in EXCHANGE_OPTION_NODES.items()}
    job=tasker.post_task(args.task, overrides)
    try:
        while not job.done:
            time.sleep(.2)
    except KeyboardInterrupt:
        tasker.post_stop().wait()
        raise
    print('Task succeeded:',job.succeeded)
    return 0 if job.succeeded else 1


if __name__=='__main__':
    sys.exit(main())
