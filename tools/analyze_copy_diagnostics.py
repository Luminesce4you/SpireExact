"""Close separate node/transition/fork universes; retain copy coverage uncertainty."""
import argparse
from collections import Counter,defaultdict
import json
from pathlib import Path
import sys

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT))
from spire_exact.planning.io import read_json,write_json
from spire_exact.planning.budget_audit import clock_gate_audit


def checked_search(search):
    d=search['copy_diagnostics'];groups=d['fork_fates']
    checks={
        'fork_partition':sum(g['forks']for g in groups.values())==d['forks'],
        'node_partition':sum(d['child_node_fates'].values())==d['child_nodes'],
        'transition_partition':sum(g['counted_transitions']for g in groups.values())==d['counted_transitions'],
        'transition_counter':d['counted_transitions']==search['work_metrics']['counts']['TransitionCount'],
        'no_unattributed_transition':d['unattributed_transitions']==0,
        'expansion_counter':d['expansion_events']==search['expanded_nodes'],
        'unique_expanded_nodes':d['child_node_fates'].get('expanded',0)+d['root_expansion_events']==d['expansion_events'],
        'no_late_freeze':d['late_snapshot_freezes']==0,
        'clock_gates_excluded':clock_gate_audit(search)['clock_gates_excluded'],
    }
    if d['measurement_mode']=='writes':checks['same_value_store_selftest']=d['same_value_store_selftest']
    for fate,g in groups.items():
        for kind,copied in g['copies'].items():
            written=g['actual_written_lifetime'].get(kind,0)
            before=g['actual_written_before_snapshot'].get(kind,0)
            if not 0<=before<=written<=copied:raise ValueError('Invalid copied/written lifetime counts')
            all_written=g.get('actual_written_including_fork_links',g['actual_written_lifetime']).get(kind,0)
            if not written<=all_written<=copied:raise ValueError('Invalid initialized/write count ordering')
    if not all(checks.values()):raise ValueError('Diagnostic accounting failed: '+str(checks))
    return checks


def size_fit(rows,field,scale=1.):
    """Descriptive weighted plane; buckets share searches, so no iid p-values."""
    weight=sum(r['forks']for r in rows)
    means=[sum(r['forks']*r[k]for r in rows)/weight for k in('cards','powers',field)]
    covariance=lambda a,b:sum(r['forks']*(r[a]-means[('cards','powers',field).index(a)])*(r[b]-means[('cards','powers',field).index(b)])for r in rows)/weight
    xx=covariance('cards','cards');yy=covariance('powers','powers');xy=covariance('cards','powers')
    xz=covariance('cards',field);yz=covariance('powers',field);det=xx*yy-xy*xy
    if det<=1e-12:return {'identifiable':False,'reason':'insufficient independent card/power variation'}
    b=(xz*yy-yz*xy)/det;c=(yz*xx-xz*xy)/det;a=means[2]-b*means[0]-c*means[1]
    residual=sum(r['forks']*(r[field]-a-b*r['cards']-c*r['powers'])**2 for r in rows)
    variance=sum(r['forks']*(r[field]-means[2])**2 for r in rows)
    return {'identifiable':True,'intercept':a*scale,'per_card':b*scale,'per_power':c*scale,
        'weighted_R2':1-residual/variance if variance else None,'buckets':len(rows),'forks':weight,
        'scope':'pooled instrumented descriptive association; model/type/action and GC confounding remain; not causal or production timing'}


def analyze(root,profiles):
    rooms=defaultdict(lambda:{'searches':0,'expanded':0,'transitions':0,'forks':0,'children':0,
        'node_fates':Counter(),'transition_fates':Counter(),'fork_fates':Counter(),
        'copies':Counter(),'writes':Counter(),'writes_before_snapshot':Counter(),'writes_including_fork':Counter(),'uncertain':Counter(),
        'copy_ticks':Counter(),'written_copy_ticks':Counter(),'all_written_copy_ticks':Counter(),'uncertain_copy_ticks':Counter(),
        'fork_ticks':0,'unexpanded_fork_ticks':0})
    records=[];relation=[];coverage=Counter();write_sites=Counter();unsupported=set();costs_by_seed={};power_by_seed=defaultdict(Counter)
    for seed in ('10101010','1741222413','564940356'):
        folder=root/(seed+'-writes')
        for path in sorted(folder.glob('case-*/data/decision.json*')):
            decision=read_json(path)
            if decision['status']not in('BUDGET','DECISION','TERMINAL'):raise ValueError('Failed diagnostic request')
            for index,search in enumerate(decision.get('advisor_metrics',{}).get('searches',[])):
                checks=checked_search(search);d=search['copy_diagnostics'];r=rooms[search['room']]
                r['searches']+=1;r['expanded']+=search['expanded_nodes'];r['transitions']+=d['counted_transitions'];r['forks']+=d['forks'];r['children']+=d['child_nodes']
                r['node_fates'].update(d['child_node_fates']);coverage.update(d['untracked_power_reference_fields']);write_sites.update(d['raw_write_sites']);unsupported.update(d['unsupported_generic_writer_methods'])
                for fate,g in d['fork_fates'].items():
                    r['transition_fates'][fate]+=g['counted_transitions'];r['fork_fates'][fate]+=g['forks']
                    for target,field in (('copies','copies'),('writes','actual_written_lifetime'),('writes_before_snapshot','actual_written_before_snapshot'),
                        ('uncertain','power_reference_graph_uncertainty'),('copy_ticks','measured_copy_ticks_inclusive'),
                        ('written_copy_ticks','actual_written_copy_ticks_inclusive'),('uncertain_copy_ticks','uncertain_copy_ticks_inclusive')):
                        r[target].update(g[field])
                    r['writes_including_fork'].update(g['actual_written_including_fork_links'])
                    r['all_written_copy_ticks'].update(g['actual_written_including_fork_copy_ticks'])
                    power_by_seed[seed]['copy']+=g['measured_copy_ticks_inclusive'].get('power',0)
                    power_by_seed[seed]['written']+=g['actual_written_including_fork_copy_ticks'].get('power',0)
                    power_by_seed[seed]['uncertain']+=g['uncertain_copy_ticks_inclusive'].get('power',0)
                    r['fork_ticks']+=g['instrumented_fork_ticks']
                    if fate!='expanded':r['unexpanded_fork_ticks']+=g['instrumented_fork_ticks']
                records.append({'seed':seed,'path':str(path),'search':index,'room':search['room'],'checks':checks,
                    'expanded':search['expanded_nodes'],'transitions':d['counted_transitions'],'forks':d['forks'],'children':d['child_nodes'],
                    'node_fates':d['child_node_fates'],'transition_fates':{f:g['counted_transitions']for f,g in d['fork_fates'].items()}})
        # Costs-only mode removes the write transpilers/graph registration overhead.
        costs=root/(seed+'-costs-assembled')
        if not costs.exists():costs=root/(seed+'-costs')
        fork_cost=Counter()
        for path in sorted(costs.glob('case-*/data/decision.json*')):
            for index,search in enumerate(read_json(path).get('advisor_metrics',{}).get('searches',[])):
                checked_search(search);d=search['copy_diagnostics']
                for fate,g in d['fork_fates'].items():
                    ticks=g['fork_ticks_excluding_observed_bookkeeping'];byte_count=g['fork_bytes_excluding_observed_bookkeeping']
                    if ticks<0 or byte_count<0:raise ValueError('Negative corrected cost')
                    fork_cost['ticks']+=ticks;fork_cost['bytes']+=byte_count
                    if fate!='expanded':fork_cost['unexpanded_ticks']+=ticks;fork_cost['unexpanded_bytes']+=byte_count
                for row in d['size_relation']:
                    relation.append({'seed':seed,'case':path.parent.parent.name,'search':index,'room':search['room'],**row,
                        'frequency':d['stopwatch_frequency']})
        costs_by_seed[seed]=dict(fork_cost)
    if not records:raise ValueError('No native diagnostics')
    total=defaultdict(Counter)
    for room,r in rooms.items():
        r['transitions_per_expanded']=r['transitions']/r['expanded']
        r['forks_per_expanded']=r['forks']/r['expanded']
        r['unexpanded_fork_count_fraction']=1-r['fork_fates'].get('expanded',0)/r['forks']
        r['writes_fraction']={k:r['writes'][k]/v for k,v in r['copies'].items()}
        r['writes_including_fork_fraction']={k:r['writes_including_fork'][k]/v for k,v in r['copies'].items()}
        r['node_fates_per_expanded']={k:v/r['expanded']for k,v in r['node_fates'].items()}
        r['transition_fates_per_expanded']={k:v/r['expanded']for k,v in r['transition_fates'].items()}
        for k in ('copies','writes','writes_including_fork','uncertain','copy_ticks','written_copy_ticks','all_written_copy_ticks','uncertain_copy_ticks'):total[k].update(r[k])
    power_ticks=total['copy_ticks']['power'];power_written=total['all_written_copy_ticks']['power'];power_uncertain=total['uncertain_copy_ticks']['power']
    power_unused_lower=max(0,1-(power_written+power_uncertain)/power_ticks)if power_ticks else 0
    power_unused_upper=1-power_written/power_ticks if power_ticks else 1
    copies=sum(r['forks']for r in rooms.values());expanded=sum(r['fork_fates'].get('expanded',0)for r in rooms.values())
    undo_count_fraction=1-expanded/copies
    sampled=[]
    for profile in profiles:
        p=read_json(profile);search=p['search_sample_ms'];fork=p['fork_sample_ms'];power=p['disjoint_fork_subtrees'].get('power',0)
        seed=next(seed for seed in power_by_seed if seed in str(profile))
        q=power_by_seed[seed];unused_low=max(0,1-(q['written']+q['uncertain'])/q['copy']);unused_high=1-q['written']/q['copy']
        cost=costs_by_seed[seed];unexpanded_cost_fraction=cost['unexpanded_ticks']/cost['ticks']
        sampled.append({'profile':str(profile),'search_ms':search,'fork_ms':fork,'power_ms':power,
            'fork_share':fork/search,'power_share':power/search,
            'U_lazy_identified_power_estimate_interval':[power/search*unused_low,power/search*unused_high],
            'U_lazy_total_conservative_interval':[0,fork/search],
            'U_undo_equal_cost_transfer_estimate':fork/search*undo_count_fraction,
            'U_undo_corrected_time_transfer_estimate':fork/search*unexpanded_cost_fraction,
            'unexpanded_corrected_fork_time_fraction':unexpanded_cost_fraction,
            'U_undo_total_conservative_interval':[0,fork/search]})
    return {'schema':'spire-copy-work-summary/v1','accounting_passed':True,'requests_scope':'18 retained entry requests, fixed 10000 node component budget; no campaign win claim',
        'searches':len(records),'rooms':dict(rooms),'total':dict(total),'search_records':records,'size_relation':relation,
        'size_fits':{'allocated_bytes':size_fit(relation,'mean_instrumented_bytes'),
            'microseconds':size_fit(relation,'mean_instrumented_ticks',1e6/relation[0]['frequency']),
            'bytes_excluding_bookkeeping':size_fit(relation,'mean_bytes_excluding_bookkeeping'),
            'microseconds_excluding_bookkeeping':size_fit(relation,'mean_ticks_excluding_bookkeeping',1e6/relation[0]['frequency'])},
        'corrected_fork_costs_by_seed':costs_by_seed,
        'power_reference_coverage_uncertainty':dict(coverage),'unsupported_writer_methods':sorted(unsupported),'actual_write_sites':dict(write_sites),
        'power_instrumented_copy_cost_unused_fraction_interval':[power_unused_lower,power_unused_upper],
        'unexpanded_physical_fork_fraction':undo_count_fraction,'p7_sampled_opportunity':sampled,
        'opportunity_scope':'P7 sampled original-thread search time weights. E diagnostic ratios transferred to P7 are estimates, not rigorous elapsed-time bounds; whole-Fork conservative intervals explicitly include unmeasured state/history/RNG and inlined wrapper costs. Inclusive child timers overlap. No claimed production speedup or default promotion.'}


def main():
    p=argparse.ArgumentParser();p.add_argument('--root',type=Path,required=True);p.add_argument('--out',type=Path,required=True);p.add_argument('--profiles',type=Path,nargs='+',required=True);a=p.parse_args()
    from tools.fight_bench import _affinity
    _affinity('e');report=analyze(a.root,a.profiles);write_json(a.out,report)
    print(json.dumps({'accounting_passed':True,'searches':report['searches'],'rooms':{k:{x:v[x]for x in('expanded','transitions','forks','writes_fraction')}for k,v in report['rooms'].items()}},ensure_ascii=False))


if __name__=='__main__':main()
