"""User-facing Agent stdout protocol consumed by MFA's task log panel.

Keep the lower-case level at the very beginning of each line. MFA filters
ordinary stdout into its diagnostic file instead of showing it in the UI.
No controller calls or Maa callbacks belong here (playback is time-sensitive).
"""


def log(message, *, level='info'):
    if level not in ('info', 'success', 'warn', 'error'):
        raise ValueError('Unsupported task log level: ' + level)
    # Prefix every line, including multiline exception messages.
    for line in str(message).splitlines():
        if line.strip():
            print(f'{level}: {line}', flush=True)


STATUS_TEXT = {
    'finished': '执行完毕',
    'target_reached': '已达到目标',
    'max_rounds_reached': '已达到设定次数',
    'scan_finished': '未 FC 谱面扫描完毕',
    'challenges_finished': '舞台挑战扫描完毕',
    'insufficient_auto_lives': '自动演出剩余次数不足，停止演出',
    'insufficient_fire': '火不足，停止演出',
    'insufficient_cp': 'CP 不足，停止演出',
    'insufficient_stickers': '米歇尔贴纸不足，停止交换',
    'insufficient_materials': '材料不足，停止解锁',
    'insufficient_kits': '裁缝工具套装不足，停止解锁',
    'insufficient_coins': '金币不足，停止解锁',
    'no_categories_selected': '未选择交换分类，跳过交换',
    'no_difficulties_selected': '未选择难度，跳过挖矿',
    'no_stars_selected': '未选择成员星级，跳过故事任务',
    'free_draw_not_available': '当前没有可用的每日免费招募',
    'read_reward_confirmed': '故事已读，奖励已确认',
    'read_reward_unconfirmed': '故事已读，但未确认奖励',
    'practice_disabled_or_ineligible': '未开启练习或成员不符合练习条件，跳过',
    'insufficient_practice_tickets': '练习券不足，跳过',
    'level_locked': '成员等级不足，跳过',
    'material_unlock_disabled': '未开启材料解锁，跳过',
    'insufficient_or_unreadable_materials': '材料不足或无法确认数量，跳过',
    'no_unlockable_costumes': '没有可解锁的服装',
    'inspected': '仅检查，未执行解锁',
    'collection_completed': '收集任务已全部完成',
}

COMPLETE = {'finished', 'target_reached', 'max_rounds_reached',
            'scan_finished', 'challenges_finished'}


def status_text(status):
    return STATUS_TEXT.get(status, f'任务结束，状态：{status}')


def finish(label, report):
    status = report.get('status', 'unknown')
    details = []
    for key, title in (('completed_rounds', '完成轮数'), ('attempted', '尝试次数'),
                       ('draws', '免费招募次数'), ('read', '故事阅读数')):
        if key in report:
            details.append(f'{title} {report[key]}')
    for key, title in (('claims', '领取批次'), ('exchanges', '交换项目'),
                       ('full_combos', '确认 FC'), ('characters', '检查成员'),
                       ('skipped', '跳过项目')):
        if key in report:
            details.append(f'{title} {len(report[key])}')
    if report.get('selection_source') == 'user' and status == 'finished':
        text = '指定谱面输入完成，任务结束（未检查结算）'
    else:
        text = status_text(status)
    if report.get('inspect_only'):
        details.append('仅检查模式')
    if details:
        text += '；' + '，'.join(details)
    log(f'[{label}] {text}', level='success' if status in COMPLETE else 'warn')


def failure(label, error, context):
    stopped = bool(getattr(getattr(context, 'tasker', None), 'stopping', False))
    log(f'[{label}] ' + ('已按用户请求停止：' if stopped else '执行失败：') + str(error),
        level='warn' if stopped else 'error')
