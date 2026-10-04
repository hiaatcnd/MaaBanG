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


def mission_page(text):
    text = re.sub(r"\s+", "", unicodedata.normalize("NFKC", text))
    match = re.fullmatch(r"(\d+)/(\d+)", text)
    if not match or not 1 <= int(match[1]) <= int(match[2]) <= 100:
        raise ValueError(f"无法可靠读取任务页码：{text!r}")
    return int(match[1]), int(match[2])


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
    if not re.search(r"每日(?:最多)?3次免费", text) or "演出招募" not in text:
        raise ValueError("不是每日三次免费演出招募确认页")
    if any(word in text for word in ("消耗", "付费", "250", "2500")):
        raise ValueError("招募确认页含有收费信息")


def verify_event_free_confirmation(text):
    text = re.sub(r"\s+", "", unicodedata.normalize("NFKC", text))
    if not (re.search(r"免费(?:\d+|十)?(?:连|次)?招募", text)
            and "进行招募" in text and "确认吗" in text
            and "不会消耗星石" in text):
        raise ValueError("未确认活动招募免费且不会消耗星石")
    remainder = text.replace("不会消耗星石", "")
    if any(word in remainder for word in ("消耗", "付费", "有偿", "花费", "使用", "星石", "招募券")):
        raise ValueError("活动招募确认页含有收费或招募券信息")
