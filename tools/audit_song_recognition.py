"""Read-only, resumable audit of every free-live song in the actual game list.

Only the song-list column is clicked. Never confirms a song or starts a live.
"""
import argparse
import json
from pathlib import Path
import sys
import time

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT/'agent'))


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--adb',required=True)
    parser.add_argument('--address',required=True)
    parser.add_argument('--output',type=Path,default=ROOT/'debug/free-song-audit')
    parser.add_argument('--limit',type=int,default=1200)
    parser.add_argument('--resume',action='store_true')
    args=parser.parse_args()
    import cv2
    import numpy as np
    from maa.controller import AdbController
    from maa.custom_action import CustomAction
    from maa.resource import Resource
    from maa.tasker import Tasker
    from maa.toolkit import Toolkit
    from chart_live import ChartLiveFlow
    from chart_policy import ChartOptions
    from online_policy import final_song
    from song_catalog import BY_ID
    from costume_unlock import normalized

    args.output.mkdir(parents=True,exist_ok=True)
    Toolkit.init_option(str(args.output/'runtime'))
    resource=Resource()
    assert resource.post_bundle(ROOT/'assets/resource').wait().succeeded
    controller=AdbController(args.adb,args.address)
    controller.set_screenshot_target_short_side(720)
    assert controller.post_connection().wait().succeeded
    tasker=Tasker();tasker.bind(resource,controller)
    records_path=args.output/'records.jsonl'
    rows=[json.loads(line) for line in records_path.read_text(encoding='utf-8').splitlines()] if args.resume and records_path.exists() else []
    if records_path.exists() and not args.resume:
        raise RuntimeError('Use a new directory or --resume')

    class Audit(CustomAction):
        def run(self,ctx,argv):
            flow=ChartLiveFlow(ctx,ChartOptions.parse({'fire':0}),args.output)
            def read():
                flow.snap()
                if not flow.reco('LV_SongPage'):
                    raise RuntimeError('Left song selection: no further clicks allowed')
                return flow.text([210,332,356,32]),flow.text([201,365,374,35])
            def next_row():
                # On this carousel, the selected card stays at y=300..394.
                # Its immediate successor is the first short card at y=399..452.
                assert controller.post_click(380,428).wait().succeeded
                flow.pause(.5)
            status='partial'
            try:
                previous=None
                unchanged=0
                if args.resume and rows:
                    current=read()
                    if current!=tuple(rows[-1]['selected']):
                        raise RuntimeError('Resume position differs from last saved song')
                    previous=flow.image[300:398,200:575].copy()
                    next_row()
                for _ in range(args.limit):
                    first=read()
                    flow.pause(.18)
                    selected=read()
                    for retry in range(6):
                        if first==selected: break
                        first=selected;flow.pause(.2);selected=read()
                    else:
                        raise RuntimeError('Song title did not settle')
                    signature=flow.image[300:398,200:575].copy()
                    if previous is not None and np.mean(np.abs(signature.astype(float)-previous.astype(float)))<1:
                        card=flow.image[402:449,211:558].astype(float)
                        has_next=((card.max(2)-card.min(2)<12)&(card.mean(2)>65)&(card.mean(2)<155)).mean()>.5
                        if not has_next:
                            status='list_end'
                            break
                        unchanged+=1
                        if unchanged>5:
                            raise RuntimeError('Next song card still visible but selection did not advance')
                        next_row()
                        continue
                    unchanged=0
                    index=len(rows)+1
                    matches=[s for s in BY_ID.values() if flow.title_matches(selected[0],s)]
                    if len(matches)>1:
                        matches=[s for s in matches if normalized(selected[1]) in
                                 {normalized(v) for v in s['band_aliases']}]
                    online_id=None
                    try: online_id=final_song(selected[0],selected[1])['id']
                    except ValueError: pass
                    row={'index':index,'selected':list(selected),'free_ids':[s['id'] for s in matches],
                         'online_id':online_id,'next_text':flow.text([205,417,355,30]),
                         'frame':f'frames/{index:04d}.jpg'}
                    (args.output/'frames').mkdir(exist_ok=True)
                    (args.output/'titles').mkdir(exist_ok=True)
                    cv2.imwrite(str(args.output/row['frame']),flow.image,[cv2.IMWRITE_JPEG_QUALITY,85])
                    cv2.imwrite(str(args.output/'titles'/f'{index:04d}.png'),flow.image[298:398,195:575])
                    rows.append(row)
                    with records_path.open('a',encoding='utf-8') as stream:
                        stream.write(json.dumps(row,ensure_ascii=False)+'\n')
                    if index%10==0 or len(matches)!=1 or online_id is None:
                        print(json.dumps(row,ensure_ascii=False),flush=True)
                    previous=signature
                    if _+1<args.limit: next_row()
                return True
            except Exception as exc:
                status='error'
                print(str(exc),flush=True)
                if flow.image is not None: flow.save_frame('audit_error.png')
                return False
            finally:
                matched={row['free_ids'][0] for row in rows if len(row['free_ids'])==1}
                summary={'status':status,'visited':len(rows),'unique_free_matches':len(matched),
                         'unmatched_rows':[row['index'] for row in rows if len(row['free_ids'])!=1],
                         'unmatched_catalog':[sid for sid in BY_ID if sid not in matched],
                         'catalog_size':len(BY_ID),'spent_fire':0}
                (args.output/'summary.json').write_text(json.dumps(summary,ensure_ascii=False,indent=2),encoding='utf-8')
                print(json.dumps({k:v for k,v in summary.items() if k!='unmatched_catalog'},ensure_ascii=False),flush=True)

    resource.register_custom_action('AuditSongs',Audit())
    job=tasker.post_task('AuditSongs',{'AuditSongs':{'action':'Custom','custom_action':'AuditSongs'}})
    try:
        while not job.done: time.sleep(.2)
    except KeyboardInterrupt:
        tasker.post_stop().wait()
        return 130
    return 0 if job.succeeded else 1


if __name__=='__main__':
    raise SystemExit(main())
