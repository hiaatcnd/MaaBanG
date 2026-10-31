import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import Mock, patch

import numpy as np
from PIL import Image

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT/'agent'))
from bestdori_portraits import CardPortraits
from bestdori_catalog import load
from bestdori_scan import BestdoriScan
from mining_policy import member_signature

FIXTURES = ROOT/'tests/fixtures/bestdori/portraits'


class PortraitTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.catalog = load()
        cls.scope = {'bands': list(map(int, cls.catalog['bands'])),
                     'members': list(map(int, cls.catalog['characters'])),
                     'rarities': [1, 2, 3, 4, 5]}
        flow = BestdoriScan.__new__(BestdoriScan)
        flow.image = np.array(Image.open(FIXTURES/'member-list.png'))[:, :, ::-1].copy()
        cls.portraits = [member_signature(flow.image, p) for p in flow.page_cards()]

    def gallery(self):
        return CardPortraits.prepare(FIXTURES, self.catalog, self.scope, [], Mock(), Mock())

    def test_real_grid_matches_both_art_variants_despite_badges_and_stars(self):
        gallery = self.gallery()
        self.assertEqual([gallery.identify(p) for p in self.portraits[:7]],
                         [1703, 1704, 1712, 1711, 1710, 1705, 1713])
        self.assertTrue(all(gallery.identify(p) is None for p in self.portraits[7:]))
        self.assertIsNone(gallery.identify(np.zeros((64, 80, 3), dtype=np.float32)))
        self.assertIsNone(gallery.identify(self.portraits[0]*.5))

    def test_same_art_for_distinct_ids_is_ambiguous(self):
        gallery = CardPortraits()
        gallery.add(1703, FIXTURES/'thumbnails/1703-1.png')
        gallery.add(99999, FIXTURES/'thumbnails/1703-1.png')
        self.assertIsNone(gallery.finish().identify(self.portraits[0]))

    def test_missing_unavailable_or_corrupt_public_art_falls_back_to_details(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            (root/'thumbnails').mkdir()
            (root/'thumbnails/1703-1.png').write_bytes(b'broken')
            (root/'thumbnails/not-a-card.png').write_bytes(b'broken')
            with patch('bestdori_portraits.CardMedia.get', side_effect=OSError('offline')) as fetch:
                gallery = CardPortraits.prepare(root, self.catalog, self.scope, [1703], Mock(), Mock())
            fetch.assert_called_once_with(1703, both=True)
            self.assertIsNone(gallery.identify(self.portraits[0]))

    def test_cancelled_preparation_does_not_fetch_any_art(self):
        with tempfile.TemporaryDirectory() as folder, patch('bestdori_portraits.CardMedia.get') as fetch:
            with self.assertRaisesRegex(RuntimeError, 'cancelled'):
                CardPortraits.prepare(folder, self.catalog, self.scope, [1703],
                                      Mock(side_effect=RuntimeError('cancelled')), Mock())
            fetch.assert_not_called()
