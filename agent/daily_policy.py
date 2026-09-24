"""Decisions shared by the daily tasks; no device access."""
import re
import unicodedata

EXCHANGE_CATEGORIES = ("成员", "表情", "服装", "背景", "其他")
EXCHANGE_OPTION_NODES = dict(zip(EXCHANGE_CATEGORIES, (
    "DY_ExchangeMembers", "DY_ExchangeEmotes", "DY_ExchangeCostumes",
    "DY_ExchangeBackgrounds", "DY_ExchangeOther",
)))


def selected_exchange_categories(context):
    selected = []
    for category, node in EXCHANGE_OPTION_NODES.items():
        data = context.get_node_data(node)
        enabled = (data or {}).get("attach", {}).get("enabled")
        if not isinstance(enabled, bool):
            raise ValueError(f"交换分类配置缺失或无效：{category}")
        if enabled:
            selected.append(category)
    return selected


def integer(text):
    text = unicodedata.normalize("NFKC", text).strip()
    if not re.fullmatch(r"\d+", text):
        raise ValueError(f"不能可靠读取整数：{text!r}")
    return int(text)


def remaining_draws(text):
    text = re.sub(r"\s+", "", unicodedata.normalize("NFKC", text))
    match = re.fullmatch(r"剩余([0-3])(?:回|次)", text)
    if not match:
        raise ValueError(f"不能可靠读取免费招募次数：{text!r}")
    return int(match[1])


def verify_exchange(category, quantity, before, after, cost):
    if category not in EXCHANGE_CATEGORIES:
        raise ValueError("不在指定的贴纸交换分类内")
    if quantity != 1:
        raise ValueError("未拥有项目每次只交换一份")
    if cost <= 0 or before < cost or after < 0 or before - after != cost:
        raise ValueError("交换费用与贴纸余额不一致")


def verify_free_confirmation(text):
    text = re.sub(r"\s+", "", unicodedata.normalize("NFKC", text))
    if "每日3次免费" not in text or "演出招募" not in text:
        raise ValueError("不是每日三次免费演出招募确认页")
    if any(word in text for word in ("消耗", "付费", "250", "2500")):
        raise ValueError("招募确认页含有收费信息")
