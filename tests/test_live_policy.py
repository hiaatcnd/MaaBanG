import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "agent"))
from live_policy import (LiveOptions, fire_for_song, round_stop_reason,
                         parse_auto_remaining, effective_difficulty)


class LivePolicyTests(unittest.TestCase):
    def test_optional_limit_and_valid_configuration(self):
        self.assertIsNone(LiveOptions.parse({}).max_rounds)
        self.assertEqual(LiveOptions.parse({'max_rounds':'2','mode':'tour'}).songs_per_round,3)
        for value in ('0','-1','1.5','abc',True,1000):
            with self.assertRaises(ValueError): LiveOptions.parse({'max_rounds':value})

    def test_fire_shortage_and_zero_fire(self):
        self.assertIsNone(fire_for_song(3,2,'stop'))
        self.assertEqual(fire_for_song(3,2,'lower'),2)
        self.assertEqual(fire_for_song(3,0,'lower'),0)
        self.assertEqual(fire_for_song(0,0,'stop'),0)
        for value in (-1,4,True):
            with self.assertRaises(ValueError): LiveOptions.parse({'fire':value})

    def test_whole_tour_must_have_three_auto_uses(self):
        opts=LiveOptions.parse({'mode':'tour','fire':1})
        self.assertEqual(round_stop_reason(opts,0,2,99),'insufficient_auto_lives')
        self.assertEqual(round_stop_reason(opts,0,3,2),'insufficient_fire')
        self.assertIsNone(round_stop_reason(opts,0,3,3))
        opts=LiveOptions.parse({'mode':'tour','shortage':'lower'})
        self.assertIsNone(round_stop_reason(opts,0,3,0))

    def test_limit_counts_rounds_and_zero_fire_is_bounded_by_auto_uses(self):
        opts=LiveOptions.parse({'max_rounds':'2','fire':0})
        self.assertEqual(round_stop_reason(opts,2,8,0),'max_rounds_reached')
        self.assertEqual(round_stop_reason(opts,0,0,0),'insufficient_auto_lives')
        self.assertIsNone(round_stop_reason(opts,0,1,0))

    def test_auto_counter_is_strict(self):
        self.assertEqual(parse_auto_remaining('还有 ４ 次'),4)
        for text in ('还有11次','还有-1次','4','自动演出 开','还有4次 付费'):
            with self.assertRaises(ValueError): parse_auto_remaining(text)

    def test_special_falls_back_only_if_missing(self):
        self.assertEqual(effective_difficulty('special',False),'expert')
        self.assertEqual(effective_difficulty('special',True),'special')
        for difficulty in ('easy','normal','hard','expert'):
            self.assertEqual(effective_difficulty(difficulty,False),difficulty)


if __name__ == '__main__':
    unittest.main()
