"""Pure decisions for default 3D costume unlocks (no device access)."""
import re
import unicodedata


def parse_target(value):
    if isinstance(value, bool) or not re.fullmatch(r"[0-9]+", str(value)):
        raise ValueError("目标数量 x 必须为非负整数")
    value = int(value)
    if not 0 <= value <= 999:
        raise ValueError("目标数量 x 必须在 0–999 之间")
    return value


def parse_ratio(text):
    text = unicodedata.normalize("NFKC", text)
    match = re.fullmatch(r"\s*(\d+)\s*/\s*(\d+)\s*", text)
    if not match:
        raise ValueError(f"无法可靠读取数量：{text!r}")
    owned, required = map(int, match.groups())
    if required <= 0:
        raise ValueError("数量分母必须大于零")
    return owned, required


def purchase_decision(current, target, kits, coins):
    if current >= target:
        return "target_reached"
    if kits[0] < kits[1]:
        return "insufficient_kits"
    if coins[0] < coins[1]:
        return "insufficient_coins"
    return "unlock"


# Visible left-to-right order in both the rating and costume character lists.
ROSTER = [
    ("poppin_party", ["牛込里美", "山吹沙绫", "户山香澄", "市谷有咲", "花园多惠"]),
    ("afterglow", ["上原绯玛丽", "羽泽鸫", "美竹兰", "宇田川巴", "青叶摩卡"]),
    ("pastel_palettes", ["白鹭千圣", "若宫伊芙", "丸山彩", "大和麻弥", "冰川日菜"]),
    ("roselia", ["冰川纱夜", "宇田川亚子", "凑友希那", "白金燐子", "今井莉莎"]),
    ("hello_happy_world", ["北泽育美", "奥泽美咲", "弦卷心", "濑田薰", "松原花音"]),
    ("morfonica", ["八潮瑠唯", "广町七深", "仓田真白", "二叶筑紫", "桐谷透子"]),
    ("raise_a_suilen", ["PAREO", "MASKING", "LAYER", "CHU²", "LOCK"]),
    ("mygo", ["千早爱音", "长崎爽世", "高松灯", "椎名立希", "要乐奈"]),
]


BAND_NAMES = dict(zip((band for band, _ in ROSTER), [
    "Poppin'Party", "Afterglow", "Pastel＊Palettes", "Roselia",
    "Hello, Happy World!", "Morfonica", "RAISE A SUILEN", "MyGO!!!!!",
]))


def scope_members(kind="all", value=""):
    """Validate selection before any device action; preserve roster ordering elsewhere."""
    if kind == "all":
        return {name for _, members in ROSTER for name in members}
    if kind == "band":
        for band, members in ROSTER:
            if value in (band, BAND_NAMES[band]):
                return set(members)
    elif kind == "member":
        for _, members in ROSTER:
            for name in members:
                if unicodedata.normalize("NFKC", str(value)) == unicodedata.normalize("NFKC", name):
                    return {name}
    raise ValueError(f"无效的执行范围：{kind} / {value}")
