"""Versioned real gate entries and a standalone component request builder.

No planner source is edited. This uses read-only parsers from the workspace.
Library entries are sampled in recorded absorption order, per run and gate.
Old checkpoints keep their strict host identity; new hosts replay prefixes.
"""
import argparse
from collections import Counter
from copy import deepcopy
import gzip
import hashlib
import json
from pathlib import Path
import sqlite3
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.dont_write_bytecode = True
sys.path.insert(0, str(ROOT))
from tools.codex_checkpoint_freeze import ART, affinity
from spire_exact.planning.gatemodel import boss_fight_rows
from spire_exact.planning.plugins.reroll import boss_search_groups, first_call

def canonical(value):
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(',', ':'))

def read(path):
    if str(path).endswith('.gz'):
        with gzip.open(path, 'rt', encoding='utf-8-sig') as f: return json.load(f)
    return json.loads(path.read_text(encoding='utf-8-sig'))

def gate_name(gate):
    return ('A1','A2','F1')[gate[0]] if gate[1] == 0 else 'F2'

def collect(a):
    affinity()
    if a.db.exists(): raise SystemExit('Never overwrite a library; select a new path')
    a.db.parent.mkdir(parents=True, exist_ok=True)
    db=sqlite3.connect(a.db)
    db.execute('CREATE TABLE entries (id TEXT PRIMARY KEY, gate TEXT, seed TEXT, solver_seed INTEGER, run TEXT, host_sha256 TEXT, solver_sha256 TEXT, record TEXT NOT NULL)')
    db.execute('CREATE INDEX gate_query ON entries(gate,seed,run)')
    frozen={}
    if a.freeze_manifest and a.freeze_manifest.exists():
        manifest=read(a.freeze_manifest)
        frozen={str((ART / r['file']).resolve()).lower():str(Path(manifest['freeze_root'])/r['frozen_file']) for r in manifest['files']}
    summary=dict(schema='spire-gate-library/v1', per_run_gate=a.per_gate, max_evaluations_scanned_per_run=a.max_evals,
                 counts={}, runs=[], missing_decisions=0, records=0, control_identity_includes_solver=True)
    for group in ('iteration-105-ablation','iteration-104-i054a-r20b'):
        for run in sorted((ART/group).iterdir()):
            if not run.is_dir(): continue
            counts=Counter(); seen=set(); scanned=0; missing=0
            manifest=read(run/'validation-manifest.json') if (run/'validation-manifest.json').exists() else {}
            for seed_dir in sorted(run.glob('seed-*')):
                ledger=seed_dir/'evaluations.jsonl'
                if not ledger.is_file(): continue
                with ledger.open(encoding='utf-8-sig') as stream:
                    for line in stream:
                        if scanned>=a.max_evals or all(counts[g]>=a.per_gate for g in ('A1','A2','F1','F2')): break
                        try: ev=json.loads(line)
                        except ValueError: continue
                        if ev.get('cache_hit') or ev.get('classification') not in ('NATIVE_ROUTE_DEATH','NATIVE_WIN_CANDIDATE'): continue
                        scanned+=1
                        folder=seed_dir/ev['label']; path=folder/'data/decision.json.gz'
                        if not path.exists(): path=folder/'data/decision.json'
                        if not path.exists(): missing+=1; continue
                        decision=read(path); request=read(folder/'request.json')
                        rows=boss_fight_rows(decision)
                        played=[r for r in rows if r['index']>=len(request.get('history',[]))]
                        groups=boss_search_groups(decision)
                        identity=read(folder/'data/identity.json')
                        advisor=request.get('advisor') or {}
                        solver_hash=next((v for k,v in (advisor.get('binary_identity') or {}).items() if Path(k).name=='CombatSolver.dll'),None)
                        for row in played:
                            gate=gate_name(row['gate'])
                            if counts[gate]>=a.per_gate: continue
                            cut=row['index']; prefix=decision['trace'][:cut]
                            digest=hashlib.sha256(canonical(prefix).encode('utf-8')).hexdigest()
                            if digest in seen: continue
                            seen.add(digest)
                            evidence=decision['decision_evidence']
                            end=next((j for j in range(cut+1,len(evidence)) if (evidence[j].get('observation') or {}).get('turn') is None),len(decision['trace']))
                            after=evidence[end]['observation'] if end<len(evidence) else decision['observation']
                            checkpoints=[cp for cp in decision.get('checkpoints',[]) if cp['prefix_length']<=cut]
                            cp=next((cp for cp in sorted(checkpoints,key=lambda c:c['prefix_length'],reverse=True) if Path(cp['path']).exists()),None)
                            cp_path=None if cp is None else frozen.get(str(Path(cp['path']).resolve()).lower(),cp['path'])
                            call=first_call(groups[played.index(row)]) if len(groups)==len(played) else []
                            record=dict(schema='spire-gate-entry/v1',group=group,run=run.name,seed=request['seed'],
                                solver_seed=manifest.get('solver_seed'),label=ev['label'],gate=gate,gate_tuple=row['gate'],
                                prefix=prefix,prefix_sha256=digest,entry=row['entry'],entry_hp=row['entry'].get('hp'),
                                entry_potions=row['entry'].get('potions'),checkpoint=cp_path,
                                checkpoint_prefix=cp['prefix_length'] if cp else 0,
                                checkpoint_status='available for original host only' if cp else '需前缀重建',
                                recorded_identity=identity,solver_sha256=solver_hash,recorded_request=request,
                                expected=dict(won=row['lost'] is None,outcome=row['outcome'],lost=row['lost'],
                                    observation=after,actions=decision['trace'][cut:end],terminal_combat=decision.get('terminal_combat') if row['lost'] else None),
                                first_search=call,lost_f2=any(r['gate'][1]==1 and r['lost'] is not None for r in rows))
                            eid=hashlib.sha256((group+'/'+run.name+'/'+ev['label']+'/'+gate+'/'+digest).encode()).hexdigest()[:24]
                            record['id']=eid
                            db.execute('INSERT INTO entries VALUES (?,?,?,?,?,?,?,?)',(eid,gate,request['seed'],record['solver_seed'],run.name,identity['host_sha256'],solver_hash,canonical(record)))
                            counts[gate]+=1; summary['records']+=1
                db.commit()
            summary['runs'].append(dict(group=group,run=run.name,scanned=scanned,missing_decisions=missing,counts=dict(counts)))
            summary['missing_decisions']+=missing
            print(run.name,dict(counts),flush=True)
    summary['counts']=dict(db.execute('SELECT gate,COUNT(*) FROM entries GROUP BY gate'))
    db.execute('CREATE TABLE metadata (record TEXT)'); db.execute('INSERT INTO metadata VALUES (?)',(canonical(summary),)); db.commit(); db.close()
    a.db.with_suffix('.summary.json').write_text(json.dumps(summary,ensure_ascii=False,indent=2),encoding='utf-8')
    print(canonical({k:v for k,v in summary.items() if k!='runs'}))

def request_for(record, *, original=False, plan=None, nodes=None):
    req=deepcopy(record['recorded_request'])
    for key in ('out','checkpoint','probe','expected_evidence','stop_at_floor','stop_at_strategic_decision'):
        req.pop(key,None)
    req.update(history=record['prefix'],generate_candidate=True,capture_checkpoints=False,low_io=True,
               max_decisions=len(record['prefix'])+4000,stop_after_combat=True)
    # Prefix replay is required when the host changes. No checkpoint identity
    # bypass or rewriting of the checkpoint payload is permitted.
    if original and record.get('checkpoint'): req['checkpoint']=record['checkpoint']
    if plan is not None: req['advisor'].setdefault('gate_plans',{})[record['gate'] if record['gate'] in ('A1','A2') else 'FinalBoss'+record['gate'][1]]=plan
    if plan is not None and record['gate'] in ('A1','A2'):
        req['advisor']['gate_plans'].pop(record['gate'],None); req['advisor']['gate_plans']['Boss']=plan
    if nodes is not None:
        if nodes<1: raise ValueError('node budget must be positive')
        req['advisor']['nodes']=nodes
        for p in req['advisor'].get('gate_plans',{}).values():
            for member in p['members']: member['nodes']=nodes
    return req

def main():
    p=argparse.ArgumentParser(); sub=p.add_subparsers(dest='command',required=True)
    c=sub.add_parser('collect'); c.add_argument('--db',type=Path,required=True); c.add_argument('--per-gate',type=int,default=4); c.add_argument('--max-evals',type=int,default=64); c.add_argument('--freeze-manifest',type=Path)
    q=sub.add_parser('query'); q.add_argument('--db',type=Path,required=True); q.add_argument('--gate',choices=['A1','A2','F1','F2']); q.add_argument('--limit',type=int,default=20)
    r=sub.add_parser('request'); r.add_argument('--db',type=Path,required=True); r.add_argument('--id',required=True); r.add_argument('--out',type=Path,required=True); r.add_argument('--plan',type=Path); r.add_argument('--nodes',type=int); r.add_argument('--original-host',action='store_true')
    a=p.parse_args()
    if a.command=='collect': return collect(a)
    affinity(); db=sqlite3.connect(f'file:{a.db.resolve().as_posix()}?mode=ro',uri=True)
    if a.command=='query':
        rows=db.execute('SELECT id,gate,seed,run,host_sha256,solver_sha256 FROM entries WHERE (? IS NULL OR gate=?) LIMIT ?',(a.gate,a.gate,a.limit)).fetchall()
        print(json.dumps(rows,ensure_ascii=False,indent=2)); return
    record=json.loads(db.execute('SELECT record FROM entries WHERE id=?',(a.id,)).fetchone()[0])
    if a.out.exists(): raise SystemExit('Never overwrite an output request')
    a.out.write_text(canonical(request_for(record,original=a.original_host,plan=read(a.plan) if a.plan else None,nodes=a.nodes)),encoding='utf-8')

if __name__=='__main__': main()
