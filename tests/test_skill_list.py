import sys
import unittest
from pathlib import Path

import numpy as np
from PIL import Image

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT/'agent'))

from mining_policy import member_cards, member_signature
from skill_list import accounted_edge_cards, framed_member_cards


def fixture(name):
    image = np.array(Image.open(ROOT/'tests/fixtures'/name).convert('RGB'))[:, :, ::-1].copy()
    if image.shape != (720, 1280, 3):
        full = np.full((720, 1280, 3), 255, dtype=np.uint8)
        full[185:665, 285:1140] = image
        image = full
    return image


class SkillListTests(unittest.TestCase):
    def missing_portrait(self):
        source = fixture('mining/selection_anon_grid.png')
        image = np.full_like(source, 255)
        image[207:435, 408:527] = source[207:435, 408:527]
        image[207:330, 408:527] = 255
        return image

    def test_real_pale_portrait_missed_by_artwork_detection_is_recovered(self):
        image = fixture('mining/selection_anon_grid.png')
        self.assertEqual(len(member_cards(image)), 20)
        self.assertNotIn((467, 381), member_cards(image))
        cards = framed_member_cards(image)
        self.assertEqual(len(cards), 21)
        self.assertIn((467, 381), cards)

    def test_local_reported_remaining_card_is_detected_without_overlay_targets(self):
        # This desktop capture is private and intentionally stays under debug/.
        # Desktop rendering may differ from the controller's original frames.
        path = ROOT/'debug/skill-practice-last-member-20261113/list-current.png'
        if not path.is_file():
            self.skipTest('Local reported last-member capture required')
        image = np.array(Image.open(path).convert('RGB'))[:, :, ::-1].copy()
        cards = framed_member_cards(image)
        self.assertEqual(len(cards), 1)
        self.assertEqual(cards[0][0], 344)
        self.assertLessEqual(abs(cards[0][1]-258), 3)
        self.assertGreater(np.mean(image[200:650, 280:1120].min(axis=2) > 235), .9)

    def test_actual_missed_portrait_is_recovered_when_it_is_the_last_member(self):
        image = self.missing_portrait()
        self.assertEqual(member_cards(image), [])
        self.assertEqual(framed_member_cards(image), [(467, 381)])
        # The old whole-panel test wrongly regarded this occupied list as empty.
        self.assertGreater(np.mean(image[200:650, 280:1120].min(axis=2) > 235), .9)

    def test_last_missed_portrait_is_found_after_reflow_to_the_first_slot(self):
        source = fixture('mining/selection_anon_grid.png')
        image = np.full_like(source, 255)
        image[207:312, 285:404] = source[330:435, 408:527]
        self.assertEqual(member_cards(image), [])
        self.assertEqual(framed_member_cards(image), [(344, 258)])

    def test_single_real_card_keeps_detection_without_neighbors(self):
        sources = [('skill_practice/list.png', 467),
                   ('mining/selection_michelle_badge.png', 590),
                   ('mining/selection_michelle_badge.png', 1082)]
        for filename, source_x in sources:
            source = fixture(filename)
            tile = source[185:315, source_x-59:source_x+60]
            for target_x in (344, 1082):
                for target_top in (185, 308, 525):
                    with self.subTest(filename=filename, x=target_x, top=target_top):
                        image = np.full_like(source, 255)
                        image[target_top:target_top+130, target_x-59:target_x+60] = tile
                        cards = framed_member_cards(image)
                        self.assertEqual(len(cards), 1)
                        self.assertEqual(cards[0][0], target_x)
                        self.assertLess(abs(cards[0][1]-(target_top+73)), 3)

    def test_recorded_grids_keep_row_order_and_ignore_clipped_bottom_row(self):
        for filename, count in [('skill_practice/list.png', 21),
                                ('mining/selection_michelle_badge.png', 15),
                                ('mining/selection_jitter_before.png', 13),
                                ('mining/selection_jitter_after.png', 13),
                                ('mining/member_single.png', 1),
                                ('mining/member_grid.png', 21)]:
            with self.subTest(filename=filename):
                cards = framed_member_cards(fixture(filename))
                self.assertEqual(len(cards), count)
                self.assertEqual(len(set(cards)), count)
                for offset in range(0, count, 7):
                    row = cards[offset:offset+7]
                    self.assertEqual([p[0] for p in row], sorted(p[0] for p in row))

    def test_partial_top_and_bottom_portraits_are_rejected(self):
        source = fixture('mining/member_single.png')
        for shift in (-65, 390):
            with self.subTest(shift=shift):
                image = np.full_like(source, 255)
                tile = source[185:315, 285:404]
                top = 185+shift
                image[max(top, 185):min(top+130, 665), 285:404] = tile[
                    max(185-top, 0):min(665-top, 130)]
                self.assertEqual(framed_member_cards(image), [])

    def test_isolated_level_footer_and_bonus_badge_are_not_card_frames(self):
        source = fixture('mining/member_single.png')
        for region in [(slice(185, 208), slice(285, 404)),
                       (slice(291, 311), slice(335, 395))]:
            with self.subTest(region=region):
                image = np.full_like(source, 255)
                image[region] = source[region]
                self.assertEqual(framed_member_cards(image), [])

    def test_incomplete_side_or_horizontal_frame_does_not_offer_a_target(self):
        for region in [(slice(335, 430), slice(414, 422)),
                       (slice(335, 430), slice(515, 523)),
                       (slice(328, 341), slice(408, 527)),
                       (slice(423, 436), slice(408, 527))]:
            with self.subTest(region=region):
                image = self.missing_portrait()
                image[region] = 255
                self.assertEqual(framed_member_cards(image), [])

    def test_frame_without_artwork_and_solid_unknown_screens_are_rejected(self):
        image = self.missing_portrait()
        image[337:426, 423:512] = 255
        self.assertEqual(framed_member_cards(image), [])
        for value in (0, 120, 235, 255):
            self.assertEqual(framed_member_cards(np.full_like(image, value)), [])

    def test_unknown_dimensions_and_foreign_pages_are_rejected(self):
        self.assertEqual(framed_member_cards(np.zeros((360, 640, 3), dtype=np.uint8)), [])
        for filename in ('skill_practice/confirm.png', 'skill_practice/result.png',
                         'skill_practice/materials.png', 'skill_practice/filter.png'):
            with self.subTest(filename=filename):
                self.assertEqual(framed_member_cards(fixture(filename)), [])

    def clipped_known_card(self, center, dx=0):
        source = fixture('mining/selection_anon_grid.png')
        signature = member_signature(source, (467, 381))
        image = np.full_like(source, 255)
        tile = source[330:435, 408:527]
        top = center-51
        first, last = max(185, top), min(665, top+105)
        image[first:last, 285+dx:404+dx] = tile[first-top:last-top]
        return image, signature

    def test_seen_top_and_bottom_cropped_artwork_can_be_accounted(self):
        for center in (185, 215, 235, 610, 630, 665):
            with self.subTest(center=center):
                image, signature = self.clipped_known_card(center)
                self.assertEqual(accounted_edge_cards(image, [signature]), [(344, center)])

    def test_cropped_artwork_matching_allows_small_horizontal_jitter(self):
        for dx in (-3, 3):
            with self.subTest(dx=dx):
                image, signature = self.clipped_known_card(650, dx)
                self.assertEqual(accounted_edge_cards(image, [signature]), [(344, 650)])

    def test_unknown_cropped_card_is_not_accounted_by_a_different_known_card(self):
        source = fixture('mining/selection_anon_grid.png')
        different = member_signature(source, (344, 258))
        for center in (185, 215, 630, 665):
            with self.subTest(center=center):
                image, _ = self.clipped_known_card(center)
                self.assertEqual(accounted_edge_cards(image, [different]), [])

    def test_badge_footer_and_too_short_artwork_remain_unresolved(self):
        for center in (180, 675):
            with self.subTest(center=center):
                image, signature = self.clipped_known_card(center)
                self.assertEqual(accounted_edge_cards(image, [signature]), [])
        source = fixture('mining/member_single.png')
        signature = member_signature(source, (344, 259))
        for first, last in ((185, 208), (291, 311)):
            with self.subTest(first=first, last=last):
                image = np.full_like(source, 255)
                image[665-(last-first):665, 285:404] = source[first:last, 285:404]
                self.assertEqual(accounted_edge_cards(image, [signature]), [])

    def test_uniform_or_invalid_signatures_cannot_mask_unknown_pixels(self):
        image = np.full((720, 1280, 3), 120, dtype=np.uint8)
        uniform = np.full((64, 80, 3), 120, dtype=np.float32)
        self.assertEqual(accounted_edge_cards(image, [uniform]), [])
        self.assertEqual(accounted_edge_cards(image, ['unknown', np.zeros((10, 10))]), [])
        self.assertEqual(accounted_edge_cards(image, []), [])
        self.assertEqual(accounted_edge_cards(image[:360], [uniform]), [])

    def test_edge_matcher_does_not_return_fully_visible_targets(self):
        source = fixture('mining/member_single.png')
        signature = member_signature(source, (344, 259))
        self.assertEqual(accounted_edge_cards(source, [signature]), [])


if __name__ == '__main__':
    unittest.main()
