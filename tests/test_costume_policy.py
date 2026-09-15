import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'agent'))
from costume_policy import parse_ratio, parse_target, purchase_decision, ROSTER


class CostumePolicyTests(unittest.TestCase):
    def test_target_requires_bounded_integer(self):
        for value in [-1, 1000, True, 1.5, '1e2', '', ' 1', None]:
            with self.subTest(value=value), self.assertRaises(ValueError):
                parse_target(value)
        self.assertEqual(parse_target('66'), 66)
        self.assertEqual(parse_target(0), 0)

    def test_ratio_reads_numerator_not_milestone(self):
        self.assertEqual(parse_ratio('65/66'), (65, 66))
        self.assertEqual(parse_ratio('3571864  / 10000'), (3571864, 10000))
        self.assertEqual(parse_ratio('６５／６６'), (65, 66))
        for text in ['65', '65/0', '6S/66', '65/66 1/2', '65/66金币']:
            with self.subTest(text=text), self.assertRaises(ValueError):
                parse_ratio(text)

    def test_goal_is_checked_before_spending(self):
        self.assertEqual(purchase_decision(66, 66, (0, 200), (0, 10000)), 'target_reached')
        self.assertEqual(purchase_decision(67, 66, (999, 200), (99999, 10000)), 'target_reached')

    def test_resources_and_exact_boundary(self):
        self.assertEqual(purchase_decision(65, 66, (199, 200), (10000, 10000)), 'insufficient_kits')
        self.assertEqual(purchase_decision(65, 66, (200, 200), (9999, 10000)), 'insufficient_coins')
        self.assertEqual(purchase_decision(65, 66, (200, 200), (10000, 10000)), 'unlock')

    def test_roster_has_40_unique_characters(self):
        self.assertEqual(len(ROSTER), 8)
        self.assertTrue(all(len(members)==5 for _, members in ROSTER))
        self.assertEqual(len({name for _, members in ROSTER for name in members}), 40)


if __name__ == '__main__':
    unittest.main()
