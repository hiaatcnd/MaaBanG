"""Conservative member-grid identity matching against public card thumbnails."""
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path
import re

import numpy as np
from PIL import Image

from bestdori_media import CardMedia
from bestdori_profiles import in_scope


class CardPortraits:
    def __init__(self):
        self.ids = []
        self.templates = []

    def add(self, id_, path):
        with Image.open(path) as image:
            if image.size != (180, 180):
                raise ValueError('Unexpected thumbnail size')
            rgb = image.convert('RGBA').convert('RGB')
            # The grid displays 104 px artwork; the centre is clear of rarity,
            # attribute, rank and level overlays. Keep both artwork variants.
            for size in (102, 104, 106):
                pixels = np.asarray(rgb.resize((size, size), Image.Resampling.BILINEAR))[:, :, ::-1]
                middle = size//2
                crop = pixels[middle-20:middle+20, middle-26:middle+26].astype(np.float32)
                self.templates.append(crop.reshape(10, 4, 13, 4, 3).mean((1, 3)))
                self.ids.append(id_)

    def finish(self):
        self.ids = np.asarray(self.ids, dtype=np.int32)
        self.templates = np.asarray(self.templates, dtype=np.float32)
        return self

    def identify(self, portrait):
        if not len(self.ids) or portrait.shape != (64, 80, 3):
            return None
        best = np.full(len(self.ids), np.inf)
        tails = np.full(len(self.ids), np.inf)
        for dy in range(-6, 7):
            for dx in range(-3, 4):
                patch = portrait[12+dy:52+dy, 14+dx:66+dx].reshape(10, 4, 13, 4, 3).mean((1, 3))
                diff = np.abs(self.templates-patch)
                scores = diff.mean((1, 2, 3))
                improved = scores < best
                plausible = improved & (scores <= 6)
                if plausible.any():
                    tails[plausible] = np.percentile(diff[plausible], 95, axis=(1, 2, 3))
                best[improved] = scores[improved]
        winner = int(np.argmin(best))
        if best[winner] > 6 or tails[winner] > 18:
            return None
        id_ = int(self.ids[winner])
        rivals = best[self.ids != id_]
        if len(rivals) and float(rivals.min()) < best[winner]+5:
            return None
        return id_

    @classmethod
    def prepare(cls, root, catalog, scope, existing_ids, check_stop, progress):
        root = Path(root)
        media = CardMedia(root, catalog)
        missing = []
        for id_ in sorted(existing_ids):
            variants = (0, 1) if catalog['cards'][str(id_)]['rarity'] >= 3 else (0,)
            if any(not (root/f'thumbnails/{id_}-{variant}.png').is_file() for variant in variants):
                missing.append(id_)
        if missing:
            progress(f'正在准备已有卡小图：0/{len(missing)}')
            def fetch(id_):
                check_stop()
                try:
                    media.get(id_, both=True)
                except (OSError, ValueError):
                    pass
            with ThreadPoolExecutor(max_workers=3) as pool:
                futures = [pool.submit(fetch, id_) for id_ in missing]
                try:
                    for number, future in enumerate(as_completed(futures), 1):
                        check_stop()
                        future.result()
                        progress(f'正在准备已有卡小图：{number}/{len(missing)}')
                finally:
                    for future in futures:
                        future.cancel()
        matcher = cls()
        # Cached cards outside the base compete too: close or duplicated art
        # must not become an automatic skip. Missing art falls back to details.
        for path in sorted((root/'thumbnails').glob('*.png')):
            check_stop()
            match = re.fullmatch(r'(\d+)-[01]\.png', path.name)
            if not match or not in_scope(int(match[1]), scope, catalog):
                continue
            try:
                matcher.add(int(match[1]), path)
            except (OSError, ValueError):
                continue
        return matcher.finish()
