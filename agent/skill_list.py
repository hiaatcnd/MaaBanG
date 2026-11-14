"""Conservative backup detection of complete cards in the skill target list."""

import numpy as np


def framed_member_cards(image):
    """Locate closed card frames independently of the interior artwork detector.

    Only the seven known list columns are searched. Both vertical borders must
    run together beside white gutters, and the top/bottom borders plus textured
    artwork must be present. Cropped cards, badges and isolated text are rejected.
    The caller still verifies the skill list page and the selected detail page.
    """
    if image.shape != (720, 1280, 3):
        return []
    occupied = image.min(axis=2) < 235
    # The target grid has white side margins. The lounge material page contains
    # similarly sized cards but its left detail artwork occupies this margin.
    if (occupied[200:650, 273:283].mean() > .02
            or occupied[200:650, 1142:1148].mean() > .02):
        return []
    cards = []
    for x in (344, 467, 590, 713, 836, 959, 1082):
        left = occupied[185:665, x-52:x-48].any(axis=1)
        right = occupied[185:665, x+49:x+53].any(axis=1)
        gutters = np.concatenate((occupied[185:665, x-59:x-55],
                                  occupied[185:665, x+56:x+60]), axis=1)
        ys = np.flatnonzero(left & right & (gutters.mean(axis=1) < .15))
        for group in np.split(ys, np.where(np.diff(ys) > 2)[0]+1):
            if not len(group):
                continue
            first, last = int(group[0])+185, int(group[-1])+185
            if not 98 <= last-first+1 <= 108 or len(group)/(last-first+1) < .94:
                continue
            y = (first+last)//2
            if y-55 < 185 or y+56 >= 665:
                continue
            # A broad horizontal edge must close each end of the frame. A
            # little tolerance accommodates rounded corners and rarity effects.
            top = occupied[y-55:y-47, x-45:x+46].mean(axis=1).max()
            bottom = occupied[y+47:y+56, x-45:x+46].mean(axis=1).max()
            artwork = image[y-43:y+39, x-42:x+43].astype(np.float32)
            if (top < .85 or bottom < .85
                    or np.mean(artwork.min(axis=2) < 235) < .3
                    or np.std(artwork, axis=(0, 1)).max() < 15):
                continue
            cards.append((x, y))
    rows = []
    for card in sorted(cards, key=lambda point: point[1]):
        if not rows or card[1]-rows[-1][0][1] > 20:
            rows.append([])
        rows[-1].append(card)
    return [card for row in rows for card in sorted(row)]


def accounted_edge_cards(image, seen_signatures):
    """Identify known cropped portraits for completion masks, never for clicks.

    At least twenty rows of textured interior artwork must match a previously
    confirmed member. Short badge/footer fragments and unknown artwork remain
    unresolved. Keep the same pixel tolerances as ``same_member_portrait``.
    """
    if image.shape != (720, 1280, 3):
        return []
    valid = [signature for signature in seen_signatures
             if isinstance(signature, np.ndarray) and signature.shape == (64, 80, 3)]
    if not valid:
        return []
    references = np.stack(valid).astype(np.float32)[:, 12:52, 14:66]
    # Averaging each row's left/right halves gives a cheap lower bound on the
    # full absolute difference. It cannot reject a valid full-pixel match.
    reference_rows = references.reshape(len(valid), 40, 2, 26, 3).mean(axis=3)
    pixels = image.astype(np.float32)
    points = []
    for x in (344, 467, 590, 713, 836, 959, 1082):
        for centers in (range(185, 240), range(609, 666)):
            matches = []
            for y in centers:
                first, last = max(-20, 185-y), min(20, 665-y)
                if last-first < 20:
                    continue
                ref_first, ref_last = first+20, last+20
                for dx in range(-3, 4):
                    area = pixels[y+first:y+last, x-26+dx:x+26+dx]
                    rows = area.reshape(last-first, 2, 26, 3).mean(axis=2)
                    scores = np.abs(reference_rows[:, ref_first:ref_last]-rows).mean(axis=(1, 2, 3))
                    possible = np.flatnonzero(scores < 4)
                    if not len(possible) or np.std(area, axis=(0, 1)).max() < 15:
                        continue
                    for index in possible:
                        reference = references[index, ref_first:ref_last]
                        if np.std(reference, axis=(0, 1)).max() < 15:
                            continue
                        difference = np.abs(area-reference)
                        score = difference.mean()
                        if score < 4 and np.percentile(difference, 95) < 16:
                            matches.append((float(score), y))
            if not matches:
                continue
            _, best_y = min(matches)
            # Repeating textures at distant positions cannot locate a unique
            # cropped card even if their average pixel scores happen to agree.
            if any(abs(y-best_y) > 8 for _, y in matches):
                continue
            points.append((x, best_y))
    return points
