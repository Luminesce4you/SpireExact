"""Caller-conditioned, disjoint leaf attribution of retained SolveCore samples."""
import argparse
from collections import Counter
import ctypes
import json
import os
from pathlib import Path


def analyze(document):
    names=[f['name'] for f in document['shared']['frames']]
    sample=Counter();clone=Counter();total=0.;fork=0.
    for profile in document['profiles']:
        if profile.get('type')!='evented':continue
        factor={'milliseconds':1.,'seconds':1000.,'microseconds':.001,'nanoseconds':.000001}.get(profile['unit'])
        if factor is None:raise ValueError('Unsupported unit')
        stack=[];previous=profile.get('startValue',0.)
        for event in profile['events']:
            dt=(event['at']-previous)*factor
            if dt<0:raise ValueError('Nonmonotonic profile')
            frames=[names[i] for i in stack]
            if dt and any('CombatBeamSolver.SolveCore(' in n for n in frames):
                total+=dt
                in_fork=any('CombatPredictionSimulator.Fork(' in n for n in frames)
                if any('CloneModelForSimulation(' in n for n in frames):
                    clone['fork' if in_fork else 'outside_fork']+=dt
                    if not in_fork:
                        clone['outside_fork_mutable_preview' if any('get_MutablePreview(' in n for n in frames) else 'outside_fork_other_or_inlined']+=dt
                if in_fork:
                    fork+=dt
                    # Priority gives one leaf time to one caller subtree, never adds inclusive costs.
                    if any('ForkPower(' in n for n in frames):category='power'
                    elif any('PredictedCard.Fork(' in n for n in frames):category='card_wrapper'
                    elif any('SimCardPile.Fork(' in n for n in frames):category='pile_non_wrapper'
                    elif any('RestoreHookListenerCaches(' in n or 'RemapHook' in n for n in frames):category='listener_remap'
                    elif any('PredictionStateStore.Fork(' in n for n in frames):category='store'
                    elif any('CombatPredictionHistory.Fork(' in n for n in frames):category='history'
                    elif any('CombatPredictionRngSet.Fork(' in n for n in frames):category='rng'
                    elif any('CombatPredictionState.Fork(' in n for n in frames):category='prediction_state_non_pile'
                    elif any('Forkable' in n and '.Fork(' in n for n in frames):category='forkable_collection'
                    elif any('SimulatedCombatState.' in n for n in frames):category='simulated_state_other'
                    else:category='fork_other'
                    sample[category]+=dt
            if event['type']=='O':stack.append(event['frame'])
            elif event['type']=='C':
                if not stack or stack.pop()!=event['frame']:raise ValueError('Unbalanced stack')
            else:raise ValueError('Unknown event type')
            previous=event['at']
        if stack:raise ValueError('Unclosed stack')
    return {'search_sample_ms':total,'fork_sample_ms':fork,'fork_percent':fork/total*100 if total else None,
            'disjoint_fork_subtrees':dict(sample),'clone_sample_ms_by_caller':dict(clone),
            'scope':'Retained P7 sampled thread time conditioned on SolveCore; categories are disjoint caller-subtrees, not exact copy stopwatch costs.'}


def main():
    if os.name=='nt':
        process=ctypes.windll.kernel32.GetCurrentProcess()
        ctypes.windll.kernel32.SetProcessAffinityMask(process,ctypes.c_size_t((1<<24)|(1<<25)))
        ctypes.windll.kernel32.SetPriorityClass(process,0x4000)
    p=argparse.ArgumentParser();p.add_argument('profile',type=Path);p.add_argument('--out',type=Path,required=True);a=p.parse_args()
    result=analyze(json.loads(a.profile.read_text(encoding='utf-8')))
    result['source']=str(a.profile.resolve());a.out.parent.mkdir(parents=True,exist_ok=True)
    a.out.write_text(json.dumps(result,indent=2),encoding='utf-8');print(json.dumps(result))


if __name__=='__main__':main()
