import sys
import unittest
import statistics
from unittest.mock import Mock
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]/'agent'))
from chart_timing import TempoMap, compile_basic_chart, compile_chart, first_anchor, bounded_normal


class ChartTimingTests(unittest.TestCase):
    def test_normal_tails_are_resampled_instead_of_clipped(self):
        rng=Mock()
        rng.gauss.side_effect=[4.,-5.,.25]
        self.assertEqual(bounded_normal(rng,3.),.25)
        self.assertEqual(rng.gauss.call_count,3)
        self.assertEqual(bounded_normal(rng,0.),0.)

    def test_compiled_time_and_position_jitter_are_centered_normal(self):
        chart=[{'type':'BPM','beat':0,'bpm':60}]+[
            {'type':'Single','beat':i+1,'lane':3} for i in range(12000)]
        events=compile_chart(chart,seed=71324,jitter_ms=90,position_jitter=12)
        heads=[e for e in events if e.action=='down']
        samples=[[(e.time-i-1)*1000 for i,e in enumerate(heads)],
                 [e.dx for e in heads],[e.y-590 for e in heads]]
        for values,bound in zip(samples,(90,12,6)):
            self.assertTrue(all(-bound < v < bound for v in values))
            self.assertLess(abs(statistics.mean(values)),bound*.02)
            self.assertAlmostEqual(statistics.pstdev(values),bound/3,delta=bound*.02)
            near=sum(abs(v)<=bound/3 for v in values)/len(values)
            middle=sum(abs(v)<=2*bound/3 for v in values)/len(values)
            self.assertTrue(.66<near<.71,near)
            self.assertTrue(.94<middle<.97,middle)

    def test_tempo_boundary_and_later_segment(self):
        tempo = TempoMap([{'type':'BPM','beat':0,'bpm':120},
                          {'type':'BPM','beat':4,'bpm':240}])
        self.assertEqual(tempo.seconds(4), 2)
        self.assertEqual(tempo.seconds(6), 2.5)

    def test_hold_and_simultaneous_tap_use_different_contacts(self):
        chart = [{'type':'BPM','beat':0,'bpm':120},
                 {'type':'Long','connections':[{'beat':1,'lane':1},{'beat':4,'lane':1}]},
                 {'type':'Single','beat':1,'lane':5},
                 {'type':'Single','beat':2,'lane':3}]
        events = compile_basic_chart(chart)
        downs = [e for e in events if e.action == 'down']
        hold = next(e for e in downs if e.lane == 1)
        taps = [e for e in downs if e.lane != 1]
        self.assertTrue(all(e.contact != hold.contact for e in taps))
        self.assertEqual(taps[0].contact, taps[1].contact)
        self.assertEqual(next(e.time for e in events if e.contact == hold.contact and e.action == 'up'), 2)

    def test_unsupported_gesture_rejected(self):
        for note in [{'type':'Slide','connections':[]},
                     {'type':'Single','beat':1,'lane':2,'flick':True}]:
            with self.assertRaises(ValueError):
                compile_basic_chart([{'type':'BPM','beat':0,'bpm':120},note])

    def test_duplicate_bpm_uses_last_source_record(self):
        tempo=TempoMap([{'type':'BPM','beat':0,'bpm':103},
                       {'type':'BPM','beat':0,'bpm':97}])
        self.assertAlmostEqual(tempo.seconds(1),60/97)

    def test_identical_duplicate_tempo_records_in_official_charts(self):
        tempo=TempoMap([{'type':'BPM','beat':0,'bpm':185}]*2)
        self.assertAlmostEqual(tempo.seconds(185),60.)

    def test_contact_exhaustion_rejected(self):
        chart = [{'type':'BPM','beat':0,'bpm':120}]
        chart += [{'type':'Single','beat':1,'lane':i} for i in range(3)]
        with self.assertRaises(ValueError): compile_basic_chart(chart)

    def test_slide_hidden_path_and_tempo_boundary(self):
        chart = [{'type':'BPM','beat':0,'bpm':120},
                 {'type':'BPM','beat':2,'bpm':240},
                 {'type':'Slide','connections':[{'beat':1,'lane':0},
                   {'beat':2,'lane':2.5,'hidden':True},{'beat':3,'lane':5}]}]
        events = compile_chart(chart)
        self.assertEqual(len([e for e in events if e.action=='down']), 1)
        self.assertTrue(any(e.time==1 and e.lane==2.5 for e in events))
        self.assertTrue(any(e.time==1.25 and e.lane==5 for e in events))
        self.assertEqual({e.contact for e in events}, {0})

    def test_flicks_move_in_requested_direction(self):
        for direction, sign in [('Left',-1),('Right',1)]:
            chart = [{'type':'BPM','beat':0,'bpm':120},
                     {'type':'Directional','beat':1,'lane':3,'width':2,'direction':direction}]
            e = compile_chart(chart)
            self.assertEqual(e[0].action, 'down')
            self.assertEqual(e[-1].action, 'up')
            self.assertGreater(sign*(e[-1].lane-e[0].lane), .5)

    def test_slide_motion_calibration_preserves_head_and_tail_flick(self):
        chart=[{'type':'BPM','beat':0,'bpm':120},
               {'type':'Slide','connections':[{'beat':1,'lane':0},
                 {'beat':2,'lane':3},{'beat':3,'lane':6,'flick':True}]},
               {'type':'Single','beat':1,'lane':5}]
        original=compile_chart(chart,seed=22,jitter_ms=6)
        delayed=compile_chart(chart,seed=22,jitter_ms=6,slide_motion_delay=.020)
        self.assertEqual([e for e in original if e.action=='down'],
                         [e for e in delayed if e.action=='down'])
        contact=next(e.contact for e in original if e.action=='down' and e.lane==0)
        before=[e for e in original if e.contact==contact and e.action!='down']
        after=[e for e in delayed if e.contact==contact and e.action!='down']
        self.assertEqual(len(before),len(after))
        for a,b in zip(before,after):
            self.assertAlmostEqual(b.time-a.time,.020)
            self.assertEqual((a.lane,a.y,a.action),(b.lane,b.y,b.action))
        for value in (-.001,.041,float('nan')):
            with self.assertRaises(ValueError):compile_chart(chart,slide_motion_delay=value)

    def test_seeded_jitter_preserves_chord_and_hold(self):
        chart = [{'type':'BPM','beat':0,'bpm':120},
                 {'type':'Long','connections':[{'beat':1,'lane':1},{'beat':3,'lane':1}]},
                 {'type':'Single','beat':1,'lane':5}]
        a = compile_chart(chart,seed=41,jitter_ms=6,position_jitter=3)
        self.assertEqual(a,compile_chart(chart,seed=41,jitter_ms=6,position_jitter=3))
        downs = [e for e in a if e.action=='down']
        self.assertEqual(downs[0].time,downs[1].time)
        self.assertLessEqual(abs(downs[0].time-.5),.006)
        self.assertTrue(all(abs(e.dx)<=3 and abs(e.y-590)<=1.5 for e in a))
        hold = next(e for e in downs if e.lane==1)
        up = next(e for e in a if e.contact==hold.contact and e.action=='up')
        self.assertAlmostEqual(up.time-hold.time,1.008)
        self.assertEqual(first_anchor(chart),(.5,5,'cyan'))

    def test_gesture_lifecycle_never_reuses_active_contact(self):
        chart = [{'type':'BPM','beat':0,'bpm':240}]
        chart += [{'type':'Single','beat':i*.125,'lane':i%7,'flick':i%2==0} for i in range(50)]
        for seed in range(10):
            active=set()
            for e in compile_chart(chart,seed=seed,jitter_ms=8):
                if e.action=='down':
                    self.assertNotIn(e.contact,active); active.add(e.contact)
                else:
                    self.assertIn(e.contact,active)
                    if e.action=='up':active.remove(e.contact)
            self.assertFalse(active)

    def test_unsupported_or_invalid_points_fail_closed(self):
        for note in [{'type':'Unknown','beat':1,'lane':1},
                     {'type':'Slide','connections':[{'beat':1,'lane':0},{'beat':0,'lane':1}]}]:
            with self.assertRaises(ValueError):
                compile_chart([{'type':'BPM','beat':0,'bpm':120},note])


if __name__ == '__main__':
    unittest.main()
