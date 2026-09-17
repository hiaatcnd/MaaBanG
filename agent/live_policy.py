"""Configuration and spending decisions for the game's built-in auto live."""
from dataclasses import dataclass
import re
import unicodedata

SONGS = ("SAVIOR OF SONG", "EXIST")
DIFFICULTIES = ("easy", "normal", "hard", "expert", "special")


def number(value, name, maximum):
    if isinstance(value, bool):
        raise ValueError(f"{name}必须为整数")
    text = unicodedata.normalize("NFKC", str(value)).strip()
    if not re.fullmatch(r"[0-9]+", text) or int(text) > maximum:
        raise ValueError(f"{name}必须为 0–{maximum} 的整数")
    return int(text)


@dataclass(frozen=True)
class LiveOptions:
    mode: str = "free"
    song: str = "SAVIOR OF SONG"
    difficulty: str = "expert"
    fire: int = 3
    shortage: str = "stop"
    max_rounds: int | None = None

    @classmethod
    def parse(cls, data):
        mode = data.get("mode", "free")
        song = data.get("song", SONGS[0])
        difficulty = data.get("difficulty", "expert")
        shortage = data.get("shortage", "stop")
        if mode not in ("free", "tour") or song not in SONGS:
            raise ValueError("不支持的演出模式或歌曲")
        if difficulty not in DIFFICULTIES or shortage not in ("stop", "lower"):
            raise ValueError("不支持的难度或火不足策略")
        fire = number(data.get("fire", 3), "每首火数", 3)
        limit = data.get("max_rounds", "")
        max_rounds = None if limit is None or limit == "" else number(limit, "最大演出次数", 999)
        if max_rounds == 0:
            raise ValueError("最大演出次数请留空或填写 1–999")
        return cls(mode, song, difficulty, fire, shortage, max_rounds)

    @property
    def songs_per_round(self):
        return 3 if self.mode == "tour" else 1


def parse_auto_remaining(text):
    text = re.sub(r"\s+", "", unicodedata.normalize("NFKC", text))
    match = re.fullmatch(r"(?:还有|剩余)([0-9]+)次", text)
    if not match or int(match[1]) > 10:
        raise ValueError(f"无法确认自动演出剩余次数：{text!r}")
    return int(match[1])


def fire_for_song(requested, available, shortage):
    requested = number(requested, "每首火数", 3)
    available = number(available, "持有火数", 999)
    if shortage not in ("stop", "lower"):
        raise ValueError("未知火不足策略")
    if available >= requested:
        return requested
    return None if shortage == "stop" else available


def round_stop_reason(options, completed, auto_remaining, available_fire):
    if options.max_rounds is not None and completed >= options.max_rounds:
        return "max_rounds_reached"
    if auto_remaining < options.songs_per_round:
        return "insufficient_auto_lives"
    if options.shortage == "stop" and available_fire < options.fire * options.songs_per_round:
        return "insufficient_fire"
    return None


def effective_difficulty(requested, has_special):
    if requested not in DIFFICULTIES:
        raise ValueError("未知演出难度")
    return "expert" if requested == "special" and not has_special else requested
