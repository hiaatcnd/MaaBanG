"""Song carousel traversal must use clicks, including reverse and quick search."""
from pathlib import Path
import sys
from types import SimpleNamespace
import unittest
from unittest.mock import Mock

import numpy as np

sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'agent'))
from costume_unlock import FlowError
from song_navigation import SongNavigationMixin


class SongNavigationTests(unittest.TestCase):
    def flow(self,index=2):
        flow=SongNavigationMixin()
        state={'index':index}
        flow.report={}
        def refresh(*_):
            flow.image=np.full((720,1280,3),state['index']*30,dtype=np.uint8)
        def tap(x,y):
            self.assertEqual(x,380)
            self.assertIn(y,(270,428))
            state['index']=max(0,min(4,state['index']+(1 if y==428 else -1)))
        flow.tap=Mock(side_effect=tap)
        flow.quick_tap=Mock(side_effect=tap)
        flow.pause=Mock()
        flow.wait=Mock(side_effect=refresh)
        flow.swipe=Mock(side_effect=AssertionError('song list must not be dragged'))
        flow.quick_swipe=Mock(side_effect=AssertionError('song list must not be dragged'))
        flow.selected_song_matches=lambda song:state['index']==song['id']
        flow.title_matches=lambda text,song:text==song['title']
        flow.ocr=Mock(side_effect=lambda roi:[SimpleNamespace(text=f"song{state['index']}")])
        flow.tap_hit=Mock()
        refresh()
        return flow,state

    def test_search_reaches_target_by_clicking_previous_or_next_cards(self):
        for target,forward,quick in ((0,False,False),(4,True,False),(4,True,True)):
            with self.subTest(target=target,quick=quick):
                flow,state=self.flow()
                flow.find_filtered_song({'id':target,'title':f'song{target}'},
                                        forward_first=forward,quick=quick)
                self.assertEqual(state['index'],target)
                clicks=flow.quick_tap if quick else flow.tap
                self.assertEqual([call.args for call in clicks.call_args_list],
                                 [(380,428 if forward else 270)]*2)
                flow.swipe.assert_not_called();flow.quick_swipe.assert_not_called()

    def test_target_on_other_side_is_found_after_reaching_start(self):
        flow,state=self.flow()
        flow.find_filtered_song({'id':4,'title':'song4'})
        self.assertEqual(state['index'],4)
        self.assertEqual([call.args[1] for call in flow.tap.call_args_list],
                         [270,270,270,428,428,428,428])

    def test_missing_target_stops_at_both_ends(self):
        flow,_=self.flow()
        with self.assertRaisesRegex(FlowError,'未找到或未解锁歌曲'):
            flow.find_filtered_song({'id':9,'title':'missing'})
        self.assertEqual(flow.tap.call_count,8)
        flow.swipe.assert_not_called();flow.quick_swipe.assert_not_called()

    def test_visible_target_is_clicked_without_advancing_list(self):
        flow,_=self.flow()
        hit=SimpleNamespace(text='target')
        flow.ocr=Mock(return_value=[hit])
        flow.selected_song_matches=Mock(side_effect=[False,True])
        flow.find_filtered_song({'id':9,'title':'target'})
        flow.tap_hit.assert_called_once_with(hit)
        flow.tap.assert_not_called();flow.quick_tap.assert_not_called()


if __name__=='__main__':unittest.main()
