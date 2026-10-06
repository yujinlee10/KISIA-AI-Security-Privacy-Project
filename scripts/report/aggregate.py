"""POC 결과 집계. 실행: python scripts/report/aggregate.py run_rd run_onto"""
from __future__ import annotations
import json, csv, sys
from collections import defaultdict
from pathlib import Path
RUNS_DIR = Path(__file__).parents[2] / 'runs'
def load_config(d): return json.loads((d/'run_config.json').read_text())
def load_log(d):
    with open(d/'experiment_log.csv',newline='') as f: return list(csv.DictReader(f))
def load_trace(d):
    recs=[]
    with open(d/'trace.jsonl') as f:
        for l in f:
            l=l.strip()
            if l: recs.append(json.loads(l))
    return recs
def agg_session(se):
    c=se['coverage']
    return dict(arm=se['arm'],turns=se['turns'],stop=se['stop_reason'],k0=se['k_initial'],kf=se['k_final'],km=se['k_min'],viol=se.get('first_violation_turn'),unres=se.get('unresolved_released',0),qi_cov=c['qi_coverage'],pair_cov=c['pair_coverage'],states=c['unique_states'],interesting=c['interesting_turns'],qi_seen=c.get('qi_seen',[]))
def agg_turns(recs):
    paths=defaultdict(int); mp=pd=um=ti=to=ei=eo=0; gen=sup=ado=0; lat=0.0
    for r in recs:
        if r.get('type')!='turn': continue
        rel=r.get('release',{}); paths[rel.get('path','UNKNOWN')]+=1
        if rel.get('generalized'): gen+=1
        if rel.get('suppressed'): sup+=1
        if rel.get('adopted_ids'): ado+=1
        for o in r.get('raw_normalized',[]):
            s=o.get('status','')
            if s=='MAPPED': mp+=1
            elif s=='PENDING': pd+=1
            elif s=='UNMAPPED': um+=1
        t=r.get('target',{}); ti+=t.get('input_tokens',0); to+=t.get('output_tokens',0); lat+=t.get('latency_ms',0.0)
        e=r.get('raw_extraction',{}); ei+=e.get('input_tokens',0); eo+=e.get('output_tokens',0)
    return dict(paths=dict(paths),mp=mp,pd=pd,um=um,gen=gen,sup=sup,ado=ado,ti=ti,to=to,ei=ei,eo=eo,lat=lat)
def run(exp_id):
    d=RUNS_DIR/exp_id
    if not d.exists(): print(f'[WARN] {d} 없음'); return
    cfg=load_config(d); log=load_log(d); tr=load_trace(d)
    bc=cfg.get('backend',{}); bn=bc.get('name') if isinstance(bc,dict) else str(bc)
    arms=cfg.get('arms',cfg.get('conditions',[]))
    print(f'
## {exp_id}  backend={bn}  arms={arms}
')
    sessions=[r for r in tr if r.get('type')=='session_end']
    print('| arm | turns | stop | k0 | kf | km | viol | unres | qi_cov | pair_cov | states | interesting |')
    print('|-----|-------|------|----|----|----|------|-------|--------|----------|--------|-------------|')
    for se in sessions:
        a=agg_session(se); v=str(a['viol']) if a['viol'] else '-'
        print(f"| {a['arm']} | {a['turns']} | {a['stop']} | {a['k0']:,} | {a['kf']:,} | {a['km']:,} | {v} | {a['unres']} | {a['qi_cov']:.4f} | {a['pair_cov']:.4f} | {a['states']} | {a['interesting']} |")
    print()
    for arm in sorted({r['condition'] for r in log}):
        recs=[r for r in tr if r.get('type')=='turn' and r.get('arm')==arm]
        t=agg_turns(recs)
        print(f"**{arm}** paths={t['paths']} MAPPED={t['mp']} PENDING={t['pd']} UNMAPPED={t['um']} gen={t['gen']} sup={t['sup']} ado={t['ado']}")
        print(f"  target: in={t['ti']:,} out={t['to']:,} | extractor: in={t['ei']:,} out={t['eo']:,} | latency={t['lat']:.0f}ms")
    seeds={r['seed_id'] for r in log}
    print(f'
seeds: {len(seeds)} — {sorted(seeds)}')
def main():
    ids=sys.argv[1:] if len(sys.argv)>1 else sorted(d.name for d in RUNS_DIR.iterdir() if d.is_dir())
    for i in ids: run(i)
if __name__=='__main__': main()
