"""Reproduce out-of-combat potion timeout after map checkpoint restore."""
from pathlib import Path
import argparse,ctypes as c,gzip,json,os,shutil,sys
ROOT=Path(__file__).resolve().parents[1];sys.path.insert(0,str(ROOT))
from spire_exact.planning.pool import NativePool
from spire_exact.planning.resources import ResourcePlan
from spire_exact.planning.io import read_json,write_json
from spire_exact.canonical import canonical

def main():
    p=argparse.ArgumentParser();p.add_argument('--out',type=Path,required=True);a=p.parse_args()
    out=a.out.resolve();out.mkdir(parents=True,exist_ok=False)
    k=c.WinDLL('kernel32',use_last_error=True);k.GetCurrentProcess.restype=c.c_void_p
    k.SetProcessAffinityMask.argtypes=[c.c_void_p,c.c_size_t]
    if not k.SetProcessAffinityMask(k.GetCurrentProcess(),sum(1<<i for i in range(16,24))):raise c.WinError(c.get_last_error())
    os.environ['SPIRE_TARGET_CPUS']='8';os.environ['SPIRE_MEMORY_BUDGET_MIB']='2048'
    source=ROOT/'experiments/frozen-i023/experiments/iteration-022/m0-ordered16-dev00-s271828/seed-564940356'
    labels=['eval-0044-macro_failure','eval-0050-macro_failure','eval-0107-macro_preparation','eval-0119-macro_preparation']
    report={'passed':False,'complete':False,'cases':[],'scope':'diagnostic stored-prefix replay, not planning wins'}
    data=ROOT/'runtime/steamapps/common/Slay the Spire 2/data_sts2_windows_x86_64'
    with NativePool(data,out/'workers',ResourcePlan.detect(1,1,768,1024))as pool:
        probe=None
        for label in labels:
            old=read_json(source/label/'data/decision.json');original=read_json(source/label/'request.json')
            req={k:original[k]for k in ['seed','character','ascension','unlocks']}
            req.update(history=old['trace'],generate_candidate=False,low_io=True,event_driven_settle=True)
            fresh,_=pool.run({**req,'capture_checkpoints':True},out/(label+'-fresh'),120,fresh=True)
            cp_row=max((r for r in fresh['checkpoints']if r['prefix_length']<=len(old['trace'])-1),key=lambda r:r['prefix_length'])
            cp=cp_row['path']
            if probe is None:probe=(dict(req),Path(cp))
            restored,_=pool.run({**req,'checkpoint':cp},out/(label+'-restore'),120,fresh=True)
            same=all(canonical(fresh.get(k))==canonical(restored.get(k))for k in ['status','phase','observation','trace','decision_evidence','value','native_terminal_observed'])
            row={'label':label,'potion':old['trace'][-1],'fresh_status':fresh['status'],'fresh_reason':fresh.get('reason'),
                 'restored_status':restored['status'],'restored_reason':restored.get('reason'),'full_game_evidence_equal':same,
                 'checkpoint':cp,'checkpoint_prefix':cp_row['prefix_length'],
                 'exact_potion_boundary_checkpoint':cp_row['prefix_length']==len(old['trace'])-1,
                 'fresh_hp':fresh['observation']['hp'],'restored_hp':restored['observation']['hp']}
            row['passed']=same and fresh.get('reason')is None and restored.get('reason')is None
            report['cases'].append(row);report['complete']=len(report['cases'])==len(labels)
            report['passed']=report['complete']and all(r['passed']for r in report['cases'])
            write_json(out/'report.json',report);print(json.dumps(row),flush=True)
        # Mutate only a disposable evidence copy. The logical checksum must
        # reject it; sharing a file must not weaken checkpoint integrity.
        req,cp=probe;raw=read_json(cp,resolve_checkpoint=False)
        if 'evidence_ref'in raw:
            target=out/'tampered-evidence';(target/'checkpoints').mkdir(parents=True)
            copied=target/'checkpoints'/cp.name;shutil.copy2(cp,copied)
            decision=read_json(cp.parent.parent/'decision.json.gz')
            decision['decision_evidence'][0]['observation']['hp']='INVALID_TEST_VALUE'
            with gzip.open(target/'decision.json.gz','wt',encoding='utf-8')as f:json.dump(decision,f)
            bad,_=pool.run({**req,'checkpoint':str(copied)},out/'reject-tampered-evidence',120,fresh=True)
            rejected=bad['status']=='UNSUPPORTED'and'CHECKPOINT_CHECKSUM'in str(bad.get('reason'))
            report['shared_evidence_tamper_rejected']=rejected;report['passed'] &=rejected
            write_json(out/'report.json',report);print(json.dumps({'shared_evidence_tamper_rejected':rejected}),flush=True)
    raise SystemExit(0 if report['passed']else 1)

if __name__=='__main__':main()
