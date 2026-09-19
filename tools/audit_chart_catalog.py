"""Audit every available CN chart against the playback compiler and jitter profiles."""
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timezone
import json
from pathlib import Path
import sys

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT/'agent'))
from chart_policy import ChartSelection, JITTER_PROFILES
from chart_store import ChartStore
from chart_timing import compile_chart, first_anchor
from song_catalog import BY_ID, available_difficulties


def check(selection):
    try:
        chart,metadata=ChartStore().get(selection)
        for profile,(jitter,space) in JITTER_PROFILES.items():
            active=set()
            for e in compile_chart(chart,seed=20260919,jitter_ms=jitter,position_jitter=space):
                if not (0<=197+147.7*e.lane+e.dx<1280 and 0<=e.y<720):
                    raise ValueError(f'{profile}: out-of-screen event')
                if e.action=='down':
                    if e.contact in active:
                        raise ValueError('contact collision')
                    active.add(e.contact)
                elif e.contact not in active:
                    raise ValueError('missing contact')
                elif e.action=='up':
                    active.remove(e.contact)
            if active:
                raise ValueError('unreleased contact')
        return {'song':selection.song_id,'difficulty':selection.difficulty,'ok':True,
                'sha256':metadata['sha256'],'notes':metadata['notes'],
                'first_anchor':first_anchor(chart)}
    except Exception as exc:
        return {'song':selection.song_id,'difficulty':selection.difficulty,'ok':False,'error':str(exc)}


if __name__=='__main__':
    selections=[ChartSelection(sid,d) for sid,song in BY_ID.items() for d in available_difficulties(song)]
    rows=[]
    with ThreadPoolExecutor(max_workers=4) as pool:
        for future in as_completed([pool.submit(check,s) for s in selections]):
            row=future.result();rows.append(row)
            if not row['ok']:
                print('ISSUE',row,flush=True)
            if len(rows)%100==0:
                print(f'{len(rows)}/{len(selections)} checked',flush=True)
    report={'checked_at':datetime.now(timezone.utc).isoformat(),'songs':len(BY_ID),
            'charts':len(selections),'profiles':JITTER_PROFILES,'passed':sum(r['ok'] for r in rows),
            'rows':sorted(rows,key=lambda r:(int(r['song']),r['difficulty']))}
    (ROOT/'docs/data/chart_catalog_audit.json').write_text(json.dumps(report,ensure_ascii=False,indent=2)+'\n',encoding='utf8')
    print(f"Done {report['passed']}/{len(rows)}",flush=True)
