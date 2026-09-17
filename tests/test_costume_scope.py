import sys
import unittest
from pathlib import Path
from types import SimpleNamespace

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'agent'))
from costume_policy import ROSTER, scope_members
from costume_unlock import CostumeFlow


class ScopeTests(unittest.TestCase):
    def run_scope(self, kind, value):
        context = SimpleNamespace(tasker=SimpleNamespace(controller=None, stopping=False))
        flow = CostumeFlow(context, 1, scope=kind, selection=value)
        flow.rating_select = lambda: None
        flow.select_band = lambda *_: None
        flow.wait = lambda *_: None
        clicks = []
        flow.tap = lambda x, y: clicks.append((flow.active_band, flow.active_index, x))
        flow.read_count = lambda _: 1
        flow.return_home = lambda: None
        flow.run()
        return flow.report['characters'], clicks

    def test_all_and_band_scope(self):
        rows, _ = self.run_scope('all', '')
        self.assertEqual(len(rows), 40)
        rows, clicks = self.run_scope('band', 'Roselia')
        self.assertEqual([r['character'] for r in rows], dict(ROSTER)['roselia'])
        self.assertEqual([c[1] for c in clicks], [0, 1, 2, 3, 4])

    def test_every_single_member_preserves_game_column(self):
        for band, members in ROSTER:
            for index, member in enumerate(members):
                with self.subTest(member=member):
                    rows, clicks = self.run_scope('member', member)
                    self.assertEqual([r['character'] for r in rows], [member])
                    self.assertEqual(clicks, [(band, index, [460,622,783,945,1106][index])])

    def test_invalid_scope_fails_and_chu2_alias_is_supported(self):
        for kind, value in [('invalid',''), ('band','unknown'), ('member','unknown')]:
            with self.assertRaises(ValueError): scope_members(kind, value)
        self.assertEqual(scope_members('member','CHU2'), {'CHU²'})


if __name__ == '__main__': unittest.main()
