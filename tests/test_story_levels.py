import sys
import unittest
from pathlib import Path
from unittest.mock import patch

import numpy as np
from PIL import Image

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT/'agent'))

from mining_policy import member_cards, member_signature
from story_levels import (boundary_prefix_accounted, level_sort_direction,
                          member_list_at_bottom, member_list_at_top,
                          memory_minimum_level, parse_list_level, story_member_cards)


def fixture(name):
    return np.array(Image.open(ROOT/'tests/fixtures'/name).convert('RGB'))[:, :, ::-1].copy()


class StoryLevelTests(unittest.TestCase):
    def test_two_complete_agreeing_readings_are_required(self):
        for number, footer, expected in [('1', 'Lv. 1', 1), ('30', 'Lv30', 30),
                                         ('50', 'LV.50', 50), ('60', 'lv 60', 60),
                                         ('１００', 'Ｌｖ．１００', 100),
                                         (' 20 ', ' Lv. 20 ', 20)]:
            with self.subTest(number=number, footer=footer):
                self.assertEqual(parse_list_level(number, footer), expected)

    def test_unknown_conflicting_or_incomplete_readings_do_not_become_low_levels(self):
        cases = [('', 'Lv.1'), ('1', ''), ('X', 'Lv.1'), ('1', 'L.1'),
                 ('1', 'Lx.1'), ('60', '60'), ('50', 'Lv.5'), ('5', 'Lv.50'),
                 ('0', 'Lv.0'), ('101', 'Lv.101'), ('01', 'Lv.01'),
                 ('6 0', 'Lv.60'), ('60/60', 'Lv.60'), ('60', 'Lv.60/60'),
                 ('60', '等级60'), ('60', 'Lv.60 MAX'), ('-1', 'Lv.-1'),
                 ('1.0', 'Lv.1.0'), (1, 'Lv.1'), ('1', None)]
        for number, footer in cases:
            with self.subTest(number=number, footer=footer):
                self.assertIsNone(parse_list_level(number, footer))

    def test_rarity_floors_include_five_star_without_claiming_unlockability(self):
        self.assertEqual([memory_minimum_level(stars) for stars in range(1, 6)],
                         [20, 30, 50, 60, 60])
        self.assertEqual(memory_minimum_level(np.int64(3)), 50)

    def test_unknown_rarity_has_no_screening_threshold(self):
        for stars in (None, 0, 6, -1, True, np.bool_(False), '3', 3.0):
            with self.subTest(stars=stars):
                self.assertIsNone(memory_minimum_level(stars))

    def test_recorded_up_and_down_triangles_have_verified_game_directions(self):
        self.assertEqual(level_sort_direction(fixture('skill_practice/list.png')), 'ascending')
        self.assertEqual(level_sort_direction(fixture('mining/selection_michelle_badge.png')),
                         'descending')

    def test_verified_level_sort_screens(self):
        for name, direction in [('memory_level_desc.png', 'descending'),
                                ('memory_level_asc.png', 'ascending')]:
            with self.subTest(filename=name):
                # Documented normalized, redacted desktop captures.
                image = fixture('mining/'+name)
                self.assertEqual(level_sort_direction(image), direction)
                self.assertIs(member_list_at_top(image), True)

    def test_missing_or_partial_arrow_does_not_establish_direction(self):
        source = fixture('skill_practice/list.png')
        for rows in (slice(115, 148), slice(128, 131), slice(134, 137)):
            with self.subTest(rows=rows):
                image = source.copy()
                image[rows, 1130:1167] = 255
                self.assertIsNone(level_sort_direction(image))

    def test_solid_rectangle_diamond_and_split_arrow_are_unknown(self):
        source = fixture('skill_practice/list.png')
        for kind in ('rectangle', 'diamond', 'split'):
            with self.subTest(kind=kind):
                image = source.copy()
                image[115:148, 1130:1167] = 255
                if kind == 'rectangle':
                    image[128:137, 1142:1157] = (110, 50, 255)
                elif kind == 'diamond':
                    for row, half in enumerate((1, 2, 4, 6, 7, 6, 4, 2, 1)):
                        image[128+row, 1149-half:1150+half] = (110, 50, 255)
                else:
                    image[115:148, 1130:1167] = source[115:148, 1130:1167]
                    image[128:137, 1149] = 255
                self.assertIsNone(level_sort_direction(image))

    def test_wrong_shape_or_obscured_sort_button_is_unknown(self):
        self.assertIsNone(level_sort_direction(None))
        self.assertIsNone(level_sort_direction(np.zeros((719, 1278, 3), dtype=np.uint8)))
        image = fixture('skill_practice/list.png')
        image[118:121, 1133:1136] = 80
        self.assertIsNone(level_sort_direction(image))

    def test_complete_scrollbar_distinguishes_top_from_an_existing_scroll_position(self):
        image = fixture('skill_practice/list.png')
        self.assertIs(member_list_at_top(image), True)
        self.assertIs(member_list_at_bottom(image), False)
        image[185:665, 1150:1158] = np.roll(image[185:665, 1150:1158], 120, axis=0)
        self.assertIs(member_list_at_top(image), False)
        self.assertIs(member_list_at_bottom(image), False)

    def test_scrollbar_thumb_at_lower_track_edge_confirms_bottom(self):
        image = fixture('skill_practice/list.png')
        image[185:665, 1150:1158] = np.roll(image[185:665, 1150:1158], 395, axis=0)
        self.assertIs(member_list_at_top(image), False)
        self.assertIs(member_list_at_bottom(image), True)

    def test_empty_list_does_not_claim_top(self):
        self.assertIsNone(member_list_at_top(np.full((720, 1280, 3), 255, dtype=np.uint8)))
        self.assertIsNone(member_list_at_bottom(np.full((720, 1280, 3), 255, dtype=np.uint8)))

    def short_list(self):
        image = np.full((720, 1280, 3), 255, dtype=np.uint8)
        image[185:665, 285:1140] = fixture('mining/member_single.png')
        return image

    def test_complete_short_lists_without_scrollbar_are_confirmed_at_top(self):
        self.assertIs(member_list_at_top(fixture('mining/selection_michelle_badge.png')), True)
        self.assertIs(member_list_at_top(self.short_list()), True)
        self.assertIs(member_list_at_bottom(fixture('mining/selection_michelle_badge.png')), True)
        self.assertIs(member_list_at_bottom(self.short_list()), True)

    def test_short_list_with_full_length_thumb_is_confirmed_at_top(self):
        image = self.short_list()
        image[185:665, 1151:1156] = 100
        self.assertIs(member_list_at_top(image), True)

    def test_unknown_or_cropped_content_prevents_short_list_proof(self):
        source = self.short_list()
        for kind in ('unknown', 'clipped_top', 'clipped_bottom', 'shifted_first_row'):
            with self.subTest(kind=kind):
                image = source.copy()
                if kind == 'unknown':
                    image[215:302, 422:512] = 120
                elif kind == 'clipped_top':
                    image[185:218, 415:520] = 120
                elif kind == 'clipped_bottom':
                    image[630:665, 415:520] = 120
                else:
                    image[185:665, 285:1140] = 255
                    image[215:665, 285:1140] = source[185:635, 285:1140]
                self.assertIsNone(member_list_at_top(image))

    def test_partial_split_or_colored_scrollbar_is_unknown(self):
        source = fixture('skill_practice/list.png')
        for kind in ('split', 'short', 'colored', 'hidden_track', 'blocked_margin'):
            with self.subTest(kind=kind):
                image = source.copy()
                if kind == 'split':
                    image[210:220, 1150:1158] = 210
                elif kind == 'short':
                    image[190:270, 1150:1158] = 210
                elif kind == 'colored':
                    image[185:270, 1150:1158] = (100, 40, 255)
                elif kind == 'hidden_track':
                    image[270:665, 1150:1158] = 255
                else:
                    image[190:660, 1145:1148] = 100
                self.assertIsNone(member_list_at_top(image))
                self.assertIsNone(member_list_at_bottom(image))
        self.assertIsNone(member_list_at_top(None))
        self.assertIsNone(member_list_at_top(source[:360]))

    def test_merged_detection_recovers_actual_missing_card_in_reading_order(self):
        image = fixture('mining/selection_anon_grid.png')
        self.assertEqual(len(member_cards(image)), 20)
        cards = story_member_cards(image)
        self.assertEqual(len(cards), 21)
        self.assertEqual(cards[8], (467, 381))
        for offset in range(0, 21, 7):
            self.assertEqual([x for x, y in cards[offset:offset+7]],
                             [344, 467, 590, 713, 836, 959, 1082])

    def test_frame_centers_correct_large_errors_and_preserve_small_artwork_jitter(self):
        with patch('story_levels.member_cards', return_value=[(344, 257), (1082, 274), (344, 381)]), \
                patch('story_levels.framed_member_cards', return_value=[(344, 259), (1082, 260)]):
            self.assertEqual(story_member_cards(np.zeros((720, 1280, 3), dtype=np.uint8)),
                             [(344, 257), (1082, 260), (344, 381)])

    def test_actual_level_descending_frame_has_twenty_one_unique_full_cards(self):
        cards = story_member_cards(fixture('mining/memory_level_desc.png'))
        self.assertEqual(len(cards), 21)
        self.assertEqual(len(set(cards)), 21)
        self.assertEqual(len([point for point in cards if point[0] == 1082 and point[1] < 300]), 1)
        self.assertLess(abs(cards[6][1]-260), 3)

    def test_missing_earlier_card_blocks_level_boundary_until_recovered(self):
        image = fixture('mining/selection_anon_grid.png')
        old_cards = member_cards(image)
        self.assertFalse(boundary_prefix_accounted(image, (590, 381), old_cards, []))
        self.assertTrue(boundary_prefix_accounted(image, (590, 381), story_member_cards(image), []))
        # The same missed card is later than this boundary and does not block it.
        self.assertTrue(boundary_prefix_accounted(image, (344, 258), old_cards, []))

    def test_unknown_later_content_is_ignored_only_for_a_prior_boundary(self):
        image = self.short_list()
        image[210:310, 416:520] = 120
        self.assertTrue(boundary_prefix_accounted(image, (344, 259), [(344, 259)], []))
        self.assertFalse(boundary_prefix_accounted(image, (1280, 720), [(344, 259)], []))
        self.assertFalse(boundary_prefix_accounted(image, (467, 259), [(344, 259)], []))

    def test_known_edge_crop_can_be_accounted_but_unknown_crop_cannot(self):
        source = fixture('mining/selection_anon_grid.png')
        signature = member_signature(source, (467, 381))
        image = self.short_list()
        image[185:239, 408:527] = source[381:435, 408:527]
        cards = [(344, 259)]
        self.assertFalse(boundary_prefix_accounted(image, (344, 259), cards, []))
        self.assertTrue(boundary_prefix_accounted(image, (344, 259), cards, [signature]))
        self.assertTrue(boundary_prefix_accounted(image, (1280, 720), cards, [signature]))

    def test_full_grid_boundary_requires_every_unknown_part_to_be_resolved(self):
        image = self.short_list()
        cards = story_member_cards(image)
        self.assertTrue(boundary_prefix_accounted(image, (1280, 720), cards, []))
        image[640:665, 415:520] = 120
        self.assertFalse(boundary_prefix_accounted(image, (1280, 720), cards, []))
        self.assertFalse(boundary_prefix_accounted(image[:360], (1280, 720), cards, []))


if __name__ == '__main__':
    unittest.main()
