"""Conservative list-level and sorting decisions for memory-story screening."""

import re
import unicodedata

import numpy as np

from mining_policy import member_cards
from skill_list import accounted_edge_cards, framed_member_cards


def parse_list_level(number_text, footer_text):
    """Accept only agreeing numeric and complete Lv-prefixed footer readings."""
    if not isinstance(number_text, str) or not isinstance(footer_text, str):
        return None
    number = unicodedata.normalize('NFKC', number_text).strip()
    footer = unicodedata.normalize('NFKC', footer_text).strip()
    if not re.fullmatch(r'[1-9][0-9]{0,2}', number):
        return None
    match = re.fullmatch(r'Lv\s*[.]?\s*([1-9][0-9]{0,2})', footer, re.I)
    if not match:
        return None
    value = int(number)
    return value if 1 <= value <= 100 and int(match[1]) == value else None


def memory_minimum_level(stars):
    """Return a safe rarity floor, not proof that a particular story is unlocked.

    A member below this floor can be excluded when practice is disabled. Members
    at or above it still require the existing detail/story requirement checks.
    """
    if isinstance(stars, (bool, np.bool_)) or not isinstance(stars, (int, np.integer)):
        return None
    return {1: 20, 2: 30, 3: 50, 4: 60, 5: 60}.get(int(stars))


def level_sort_direction(image):
    """Read the known list sort triangle; down means descending in the game.

    The caller separately confirms the member-list page and the selected level
    sort label. Partial, malformed and ambiguous arrows return no direction.
    """
    if not isinstance(image, np.ndarray) or image.shape != (720, 1280, 3):
        return None
    for x, y in ((1134, 119), (1160, 119), (1134, 144), (1160, 144)):
        if np.mean(image[y-1:y+2, x-1:x+2].min(axis=2) > 235) < .85:
            return None
    area = image[115:148, 1130:1167].astype(np.int16)
    b, g, r = (area[:, :, channel] for channel in range(3))
    pink = (r > 200) & (r-g > 80) & (r-b > 20)
    ys, xs = np.where(pink)
    if not len(xs):
        return None
    left, right = int(xs.min()), int(xs.max())
    top, bottom = int(ys.min()), int(ys.max())
    width, height = right-left+1, bottom-top+1
    if (not 11 <= width <= 19 or not 8 <= height <= 13
            or abs(1130+(left+right)/2-1148) > 3
            or abs(115+(top+bottom)/2-132) > 4):
        return None
    triangle = pink[top:bottom+1, left:right+1]
    widths = triangle.sum(axis=1)
    if not .42 <= triangle.mean() <= .78 or np.any(widths == 0):
        return None
    for row in triangle:
        occupied = np.flatnonzero(row)
        if len(occupied) > 1 and np.any(np.diff(occupied) != 1):
            return None
    first, last = widths[:3].mean(), widths[-3:].mean()
    changes = np.diff(widths[1:-1].astype(np.int16))
    # Ignore only the outermost rows, where antialiasing rounds the broad edge.
    if first-last >= 4 and first > 1.8*last and np.all(changes <= 1):
        return 'descending'
    if last-first >= 4 and last > 1.8*first and np.all(changes >= -1):
        return 'ascending'
    return None


def member_list_at_top(image):
    """Read the scrollbar, or prove an entire short list fits in the viewport."""
    thumb = _member_scroll_thumb(image)
    if thumb == 'short':
        return _short_list_at_top(image)
    return bool(thumb[0] <= 188) if thumb is not None else None


def member_list_at_bottom(image):
    """Require the scrollbar thumb at the bottom, or a fully visible short list."""
    thumb = _member_scroll_thumb(image)
    if thumb == 'short':
        return _short_list_at_top(image)
    return bool(thumb[1] >= 661) if thumb is not None else None


def _member_scroll_thumb(image):
    if not isinstance(image, np.ndarray) or image.shape != (720, 1280, 3):
        return None
    for first, last in ((1145, 1148), (1159, 1162)):
        if np.mean(image[190:660, first:last].min(axis=2) > 235) < .95:
            return None
    strip = image[185:665, 1151:1156].astype(np.int16)
    neutral = np.ptp(strip, axis=2) < 12
    dark = ((strip.max(axis=2) < 145) & neutral).mean(axis=1) > .6
    ys = np.flatnonzero(dark)
    no_scrollbar = np.mean(strip.min(axis=2) > 235) > .98
    full_thumb = np.mean(dark) > .995
    if no_scrollbar or full_thumb:
        return 'short'
    if len(ys) < 12 or len(ys) >= 478 or np.any(np.diff(ys) != 1):
        return None
    track = (strip.min(axis=2) >= 145) & (strip.max(axis=2) < 235) & neutral
    if np.mean(track[~dark]) < .85:
        return None
    return int(ys[0])+185, int(ys[-1])+185


def _short_list_at_top(image):
    """Require complete card frames and no unexplained pixels around them."""
    cards = framed_member_cards(image)
    if not cards or not 250 <= min(y for _, y in cards) <= 267:
        return None
    last_y = max(y for _, y in cards)
    if last_y+57 > 645:
        return None
    panel = image[185:665, 285:1140].copy()
    for x, y in cards:
        # The 123px grid cells include the rounded bonus badge's outer pixels.
        panel[max(0, y-80-185):min(480, y+57-185),
              max(0, x-62-285):min(855, x+62-285)] = 255
    occupied = panel.min(axis=2) <= 235
    blocks = occupied.reshape(32, 15, 57, 15).mean(axis=(1, 3))
    return True if np.max(blocks) < .03 else None


def story_member_cards(image):
    """Merge detectors and use frame alignment to correct large center errors."""
    cards = list(member_cards(image))
    for point in framed_member_cards(image):
        # Interior artwork can split or join a badge and move the old center by
        # over ten pixels. Real row centers are about 123px apart, so this wider
        # same-column band merges one card without merging neighboring rows.
        nearby = [(x, y) for x, y in cards if point[0] == x and abs(point[1]-y) <= 24]
        prior = min(nearby, key=lambda old: abs(point[1]-old[1])) if nearby else None
        selected = prior if prior is not None and abs(point[1]-prior[1]) <= 8 else point
        cards = [(x, y) for x, y in cards if point[0] != x or abs(point[1]-y) > 24]
        cards.append(selected)
    rows = []
    for point in sorted(cards, key=lambda point: point[1]):
        if not rows or point[1]-rows[-1][0][1] > 20:
            rows.append([])
        rows[-1].append(point)
    return [point for row in rows for point in sorted(row)]


def boundary_prefix_accounted(image, target, cards, seen_signatures):
    """Verify visible content before a proposed low-level stopping boundary.

    Complete identified cards may be covered; clipped artwork is covered only
    when it matches a previously confirmed member. The sentinel (1280, 720)
    requests coverage of the entire visible grid for end-of-list confirmation.
    """
    if not isinstance(image, np.ndarray) or image.shape != (720, 1280, 3):
        return False
    full_grid = tuple(target) == (1280, 720)
    if not full_grid and not any(target[0] == x and abs(target[1]-y) <= 8
                                  for x, y in cards):
        return False
    panel = image[185:665, 285:1140].copy()
    covered = list(cards)+accounted_edge_cards(image, seen_signatures)
    for x, y in covered:
        panel[max(0, y-80-185):min(480, y+57-185),
              max(0, x-62-285):min(855, x+62-285)] = 255
    prefix = np.ones((480, 855), dtype=bool)
    if not full_grid:
        x, y = target
        # Include earlier rows across the full width, then only this row's
        # columns through the boundary. Unidentified content crossing the row's
        # upper edge remains unresolved instead of being guessed to be a badge.
        prefix[:] = False
        row_top = max(0, min(480, y-55-185))
        row_bottom = max(0, min(480, y+57-185))
        right = max(0, min(855, x+62-285))
        prefix[:row_top] = True
        prefix[row_top:row_bottom, :right] = True
    unexplained = (panel.min(axis=2) <= 235) & prefix
    blocks = unexplained.reshape(32, 15, 57, 15).mean(axis=(1, 3))
    return bool(np.max(blocks) < .03)
