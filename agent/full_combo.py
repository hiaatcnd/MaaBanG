"""Resolve the optional FC-history policy from stars on the final ready page."""
import numpy as np

from live_policy import DIFFICULTIES

ONLY_UNFC = 'unfc'
AVOID_FULL_COMBO_CHOICES = (('关', False), ('开', True), ('仅避免未 FC 歌曲 FC', ONLY_UNFC))


def avoid_full_combo_option(value):
    if isinstance(value, bool) or isinstance(value, str) and value == ONLY_UNFC:
        return value
    raise ValueError('避免 Full Combo 必须为关、开或仅避免未 FC 歌曲 FC')


def _ready_star(image, x):
    """Use the star interior, with the white surround rejecting covered pages."""
    patch = image[575:607, x-16:x+17].astype(float)
    if patch.shape != (32, 33, 3):
        return 'unknown'
    rim = np.concatenate((patch[0], patch[-1], patch[:, 0], patch[:, -1]))
    if np.mean(rim.min(axis=1) > 240) < .8:
        return 'unknown'
    core = image[587:596, x-3:x+4].astype(float)
    b, g, r = core[:, :, 0], core[:, :, 1], core[:, :, 2]
    # Both the pink/red FC star and the rainbow AP star contain this highlight.
    if np.mean((b > 130) & (r > 170) & (r-g > 20)) > .22:
        return 'full_combo'
    if np.mean((r > 180) & (g > 130) & (b < 130)) > .22:
        return 'clear'
    if np.mean((np.ptp(core, axis=2) < 18) & (g > 120) & (g < 235)) > .65:
        return 'unplayed'
    return 'unknown'


def ready_star_row(image):
    """Return one complete EASY–EXPERT row, optionally including SPECIAL.

    Game frames use BGR at 1280×720. Solo/stage/CP and online/tour ready pages
    have different footer offsets. Never interpret a lone colored patch as FC.
    """
    if image is None or image.shape != (720, 1280, 3):
        return None
    rows = []
    for first in (207, 317):
        states = tuple(_ready_star(image, round(first+30.7*i)) for i in range(5))
        if 'unknown' not in states[:4]:
            rows.append((first, states))
    return rows[0] if len(rows) == 1 else None


def ready_full_combo_state(image, difficulty):
    row = ready_star_row(image)
    if row is None or difficulty not in DIFFICULTIES:
        return 'unknown'
    return row[1][DIFFICULTIES.index(difficulty)]
