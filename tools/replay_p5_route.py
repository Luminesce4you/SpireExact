"""Independently replay a recorded candidate from initial state; no advisor/checkpoint."""
import argparse,sys
from pathlib import Path
ROOT=Path(__file__).resolve().parents[1];sys.path.insert(0,str(ROOT))
from spire_exact.planning.io import read_json,write_json
from spire_exact.planning.pool import NativePool
from spire_exact.planning.resources import ResourcePlan
from spire_exact.native import game_data
from spire_exact.canonical import canonical
from spire_exact.mode1 import context,is_winning_candidate,check_winning_replay

def main():
    p=argparse.ArgumentParser();p.add_argument('--candidate',type=Path,required=True)
    p.add_argument('--request',type=Path,required=True,help='original candidate request with initial seed/role/ascension/unlocks')
    p.add_argument('--out',type=Path,required=True);p.add_argument('--game-dir',type=Path)
    p.add_argument('--seconds',type=int,default=120);p.add_argument('--worker-memory-mib',type=int,default=1000)
    a=p.parse_args();a.out=a.out.resolve()
    if a.out.exists() and any(a.out.iterdir()):p.error('use a new output directory')
    c=read_json(a.candidate);original=read_json(a.request)
    req={k:original[k] for k in ('seed','character','ascension','unlocks')}
    req.update(history=c['trace'],generate_candidate=False,capture_checkpoints=False,expected_evidence=c['decision_evidence'])
    with NativePool(game_data(a.game_dir),a.out/'workers',ResourcePlan.detect(1,1,a.worker_memory_mib)) as pool:
        r,identity=pool.run(req,a.out/'replay',a.seconds)
        same=(canonical(r['trace'])==canonical(c['trace']) and
              canonical(r['decision_evidence'])==canonical(c['decision_evidence']) and
              canonical(r['observation'])==canonical(c['observation']) and r['reason'] is None)
        certificate=None
        if same and is_winning_candidate(c):
            # This revalidates the sequence under the CURRENT executable identity.
            # It does not reuse any old candidate/version's optimality assertion.
            bound_identity={'context':context(req['seed'],req['character'],req['ascension'],req['unlocks']),'native':identity}
            second,second_identity=pool.run(req,a.out/'second-independent-replay',a.seconds,fresh=True)
            certificate=check_winning_replay(r,second,bound_identity,
                {'context':bound_identity['context'],'native':second_identity})
        report={'scope':'fresh initial replay in actual supplied DLL offline TestMode',
            'successful':same,'candidate':str(a.candidate),'actions':len(c['trace']),
            'status':r['status'],'reason':r.get('reason'),'observation':r.get('observation'),
            'normal_godot_parity':False,'whole_run_verified':certificate is not None,
            'identity':identity,'performance':r['performance'],'certificate':certificate}
        write_json(a.out/'report.json',report)
        print({'successful':same,'status':r['status'],'whole_run_verified':certificate is not None,'report':str(a.out/'report.json')})
        return 0 if same else 1
if __name__=='__main__':raise SystemExit(main())
