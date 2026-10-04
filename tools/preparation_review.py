"""Offline description of one explicitly selected run; never a solver or proof.

    python tools/preparation_review.py RUN --out preparation-review.json

Only this run's ledger/result and named evaluation files are read. No panels,
other seeds, HOLDOUT directories, native binaries or project business modules
are discovered or executed. Missing native telemetry stays unknown. This tool
was written for i075 without running, compiling or testing it.
"""
from __future__ import annotations
from collections import Counter,defaultdict
import argparse
import gzip
import hashlib
import json
import math
import os
from pathlib import Path
import re
import statistics

PROPOSALS={'macro_shop_route','macro_low_hp_route','macro_card_skip','macro_forge'}
SYNTHETIC={'gate_probe','paired_card_probe','real_card_menu_probe'}
RESOURCE_EVENTS=('use_attempt','used_removed','obtained','discarded','discard_attempt')
ROUTE_SCHEMA='spire-map-route-result/v1'
RESOURCE_SCHEMA='spire-resource-telemetry/v1'


def inside(path,root):
    path=Path(path).resolve()
    if path!=root and root not in path.parents:
        raise ValueError('input escapes the explicitly selected run: '+str(path))
    return path


def read_object(path):
    def reject_constant(value):raise ValueError('nonfinite JSON constant: '+value)
    if path.suffix=='.gz':
        with gzip.open(path,'rt',encoding='utf-8-sig')as stream:value=json.load(stream,parse_constant=reject_constant)
    else:value=json.loads(path.read_text(encoding='utf-8-sig'),parse_constant=reject_constant)
    if type(value)is not dict:raise ValueError('expected a JSON object: '+str(path))
    return value


def nonnegative_integer(value):
    return value if type(value)is int and value>=0 else None


def number(value):
    if type(value)not in(int,float,str):return None
    try:value=float(value)
    except (TypeError,ValueError,OverflowError):return None
    return value if math.isfinite(value)else None


def context_of(document):
    context=document.get('context')
    source=context if type(context)is dict else document
    keys=('seed','character','ascension','unlocks')
    result={key:source[key]for key in keys if key in source}
    return result if result else None


class Tally:
    def __init__(self):self.measured=0;self.known=0;self.unknown=0
    def add(self,value):
        value=nonnegative_integer(value)
        if value is None:self.unknown+=1
        else:self.measured+=value;self.known+=1
    def report(self):
        return {'measured_sum':self.measured,'known_reports':self.known,'unknown_reports':self.unknown,
                'total':self.measured if self.known>0 and self.unknown==0 else None}


def distribution(values,unknown):
    return {'known_samples':len(values),'unknown_samples':unknown,
            'minimum':min(values)if values else None,'maximum':max(values)if values else None,
            'median':statistics.median(values)if values else None,
            'mean':math.fsum(values)/len(values)if values else None}


def paths_for(run,evaluations=None,result=None):
    run=Path(run).resolve()
    if not run.is_dir():raise ValueError('run must be one existing directory')
    manifest_path=inside(run/'validation-manifest.json',run)
    manifest=read_object(manifest_path)if manifest_path.is_file()else{}
    target=run
    if evaluations is None and not(run/'evaluations.jsonl').is_file():
        seed=manifest.get('seed')
        if type(seed)not in(str,int)or type(seed)is bool or re.fullmatch(r'[A-Za-z0-9_-]+',str(seed))is None:
            raise ValueError('select a seed directory or provide --evaluations; no other seeds are searched')
        target=inside(run/('seed-'+str(seed)),run)
    ledger=inside(evaluations or target/'evaluations.jsonl',run)
    result_path=inside(result or ledger.parent/'result.json',run)
    return run,ledger,result_path,manifest


def decision_for(seed_dir,label,run):
    if type(label)is not str or label in('.','..')or re.fullmatch(r'[A-Za-z0-9_.-]+',label)is None:
        raise ValueError('invalid evaluation label')
    folder=inside(seed_dir/label,run)
    for relative in ('data/decision.json.gz','data/decision.json','cached.json'):
        path=inside(folder/relative,run)
        if not path.is_file():continue
        document=read_object(path)
        if relative=='cached.json':
            document=document.get('result')
            if type(document)is not dict:raise ValueError('invalid cached result')
        return document,str(path)
    raise FileNotFoundError('no saved native decision for '+label)


def proposal_summary(rows,result):
    observed=Counter(row.get('kind')for row in rows if type(row.get('kind'))is str and row['kind']in PROPOSALS)
    metrics=result.get('search_metrics');snapshot=metrics.get('preparation')if type(metrics)is dict else None
    return {'ledger_proposal_records':dict(observed),
            'planner_snapshot':snapshot if type(snapshot)is dict else None,
            'planner_snapshot_state':'reported'if type(snapshot)is dict else'unknown',
            'scope':'ledger records are dispatched attempts; planner proposals may include pending work'}


def inspect(run,evaluations=None,result=None):
    run,ledger,result_path,manifest=paths_for(run,evaluations,result)
    final=read_object(result_path)if result_path.is_file()else{}
    context=context_of(manifest)or context_of(final)
    if context_of(manifest)and context_of(final):
        for key in set(context_of(manifest))&set(context_of(final)):
            if str(context_of(manifest)[key])!=str(context_of(final)[key]):
                raise ValueError('manifest/result context mismatch: '+key)
    rows=[];issues=[]
    with ledger.open(encoding='utf-8-sig')as stream:
        for line_number,line in enumerate(stream,1):
            if not line.strip():continue
            try:row=json.loads(line)
            except ValueError:
                issues.append({'line':line_number,'reason':'unreadable or incomplete ledger line; stopped here'});break
            if type(row)is not dict:
                issues.append({'line':line_number,'reason':'non-object ledger row'});continue
            if context and 'seed'in row and str(row['seed'])!=str(context.get('seed')):
                raise ValueError('ledger contains another seed')
            rows.append(row)

    resources={event:Tally()for event in RESOURCE_EVENTS}
    trace_attempts={event:Tally()for event in ('use_potion','discard_potion')}
    resource_by_room=defaultdict(Counter);resource_by_id=defaultdict(Counter)
    partial_prefix=0;complete_prefix=0;unknown_coverage=0;invalid_resource_events=0
    capture_indices=Counter();restored_indices=Counter()
    native_reports=0;missing_decisions=0;cached_rows=0;synthetic_rows=0
    route_checks=Counter();route_moves={'planned':Tally(),'executed':Tally(),'actual_arrivals':Tally()}
    inventory_reports=Tally();shop_menu_samples=[];buy_reports=Tally();buy_attempts=Counter();buy_successes=Counter()
    purchase_success= Tally();purchase_unknown_success=0;route_details=[];purchase_samples=[]
    gate_reports=Tally();f1_samples=[];seen_f1=set();duplicate_f1=0
    f1_values={name:[]for name in('hp','max_hp','hp_fraction','gold','potions','deck_size')}
    f1_unknown=Counter()

    for row in rows:
        label=row.get('label');kind=row.get('kind')
        if type(kind)is not str:
            issues.append({'label':label,'reason':'unknown/malformed evaluation kind'});kind=None
        if row.get('synthetic')is True or kind in SYNTHETIC:
            synthetic_rows+=1;continue
        if row.get('cache_hit')is True:
            cached_rows+=1;continue
        native_reports+=1
        try:document,document_path=decision_for(ledger.parent,label,run)
        except (OSError,ValueError,TypeError)as error:
            document={};document_path=None;missing_decisions+=1
            issues.append({'label':label,'reason':str(error),'classification':row.get('classification')})

        trace=document.get('trace')
        valid_trace=type(trace)is list and all(type(action)is dict for action in trace)
        for action_kind,tally in trace_attempts.items():
            tally.add(sum(action.get('kind')==action_kind for action in trace)if valid_trace else None)

        telemetry=document.get('resource_telemetry');coverage=telemetry.get('coverage')if type(telemetry)is dict else None
        valid_telemetry=(type(telemetry)is dict and telemetry.get('schema')==RESOURCE_SCHEMA
            and telemetry.get('enabled')is True and type(coverage)is dict
            and coverage.get('attached_after_setup')is True
            and coverage.get('restored_inventory_not_counted')is True
            and type(coverage.get('complete_prefix'))is bool
            and nonnegative_integer(coverage.get('restored_prefix'))is not None
            and nonnegative_integer(coverage.get('capture_from_index'))is not None
            and coverage['capture_from_index']>=coverage['restored_prefix']
            and coverage['complete_prefix']==(coverage['capture_from_index']==0))
        if valid_telemetry:
            if coverage['complete_prefix']:complete_prefix+=1
            else:partial_prefix+=1
            capture_indices[str(coverage['capture_from_index'])]+=1
            restored_indices[str(coverage['restored_prefix'])]+=1
        else:unknown_coverage+=1
        counters=telemetry.get('counters')if valid_telemetry else None
        for event,tally in resources.items():tally.add(counters.get(event)if type(counters)is dict else None)
        events=telemetry.get('events')if valid_telemetry else None
        if type(events)is list:
            for event in events:
                if type(event)is not dict or event.get('event')not in RESOURCE_EVENTS:
                    invalid_resource_events+=1;continue
                name=event['event'];act=event.get('act');room=event.get('room')
                room_key=f'act={act if type(act)is int else "unknown"};room={room if type(room)is str else "unknown"}'
                resource_by_room[room_key][name]+=1
                potion=event.get('potion');potion_id=potion.get('id')if type(potion)is dict else None
                resource_by_id[potion_id if type(potion_id)is str and potion_id else'unknown'][name]+=1

        route=document.get('map_route_result')
        if kind in('macro_shop_route','macro_low_hp_route'):
            valid_route=type(route)is dict and route.get('schema')==ROUTE_SCHEMA and route.get('requested')is True
            if not valid_route:
                route_checks['unknown_native_route_report']+=1
                for tally in route_moves.values():tally.add(None)
            else:
                for field in('entry_checked','complete'):
                    state=route.get(field);route_checks[field+('_true'if state is True else'_false'if state is False else'_unknown')]+=1
                route_moves['planned'].add(route.get('planned_moves'));route_moves['executed'].add(route.get('executed_moves'))
                arrived=route.get('arrived_shops')
                actual=(type(arrived)is list and all(type(item)is dict and item.get('native_room')=='MerchantRoom'for item in arrived))
                route_moves['actual_arrivals'].add(len(arrived)if actual else None)
                route_details.append({'label':label,'kind':kind,'entry_checked':route.get('entry_checked'),
                    'complete':route.get('complete'),'failed_guard':route.get('failed_guard'),'failure_reason':route.get('failure_reason'),
                    'target_shops':route.get('target_shops'),'arrived_shops':arrived if actual else None})

        inventory=document.get('shop_inventory_sources')
        inventory_reports.add(len(inventory)if type(inventory)is list else None)
        seen_shops=set()
        if type(inventory)is list:
            for menu in inventory:
                if type(menu)is not dict:continue
                coord=menu.get('coord')
                key=json.dumps([menu.get('act'),menu.get('floor'),coord],sort_keys=True,ensure_ascii=False,allow_nan=False)
                if key in seen_shops:continue
                seen_shops.add(key)
                shop_menu_samples.append({'label':label,'act':menu.get('act'),'floor':menu.get('floor'),'coord':coord,
                    'gold':menu.get('gold'),'empty_potion_slots':menu.get('empty_potion_slots')})
        purchases=document.get('shop_purchase_events')
        buy_reports.add(len(purchases)if type(purchases)is list else None)
        if type(purchases)is list:
            known_successes=0;unknown_successes=0
            for purchase in purchases:
                if type(purchase)is not dict:
                    purchase_unknown_success+=1;unknown_successes+=1;continue
                item=purchase.get('item');item=item if type(item)is dict else{}
                item_key=f'{item.get("semantic")or "unknown"}:{item.get("id")or "unknown"}'
                buy_attempts[item_key]+=1
                succeeded=purchase.get('purchase_succeeded')
                purchase_samples.append({'label':label,'index':purchase.get('index'),'act':purchase.get('act'),
                    'floor':purchase.get('floor'),'coord':purchase.get('coord'),
                    'item':{field:item.get(field)for field in('index','item_type','semantic','id','upgrade','cost')},
                    'purchase_succeeded':succeeded if type(succeeded)is bool else None,
                    'gold_before':number(purchase.get('gold_before')),'gold_after':number(purchase.get('gold_after')),
                    'potion_slots_before':purchase.get('potion_slots_before')if type(purchase.get('potion_slots_before'))is list else None,
                    'potion_slots_after':purchase.get('potion_slots_after')if type(purchase.get('potion_slots_after'))is list else None,
                    'removal_count_before':nonnegative_integer(purchase.get('removal_count_before')),
                    'removal_count_after':nonnegative_integer(purchase.get('removal_count_after')),'shop_policy':purchase.get('shop_policy')})
                if type(succeeded)is bool:
                    if succeeded:buy_successes[item_key]+=1;known_successes+=1
                else:purchase_unknown_success+=1;unknown_successes+=1
            if unknown_successes:
                purchase_success.measured+=known_successes;purchase_success.add(None)
            else:purchase_success.add(known_successes)
        else:purchase_success.add(None)

        gates=document.get('gate_entry_sources')
        gate_reports.add(len(gates)if type(gates)is list else None)
        if type(gates)is list:
            for entry in gates:
                if type(entry)is not dict or entry.get('is_final_first_boss')is not True:continue
                index=nonnegative_integer(entry.get('index'));observation=entry.get('observation')
                if not valid_trace or index is None or index>len(trace)or type(observation)is not dict:
                    issues.append({'label':label,'reason':'incomplete native F1 entry'});continue
                key=hashlib.sha256(json.dumps({'prefix':trace[:index],'act':entry.get('act'),'encounter':entry.get('encounter')},
                    sort_keys=True,ensure_ascii=False,separators=(',',':'),allow_nan=False).encode('utf-8')).hexdigest()
                if key in seen_f1:duplicate_f1+=1;continue
                seen_f1.add(key)
                hp=number(observation.get('hp'));maximum=number(observation.get('max_hp'))
                potions=observation.get('potions');deck=observation.get('deck')
                potion_count=(sum(value is not None for value in potions)if type(potions)is list
                              and all(value is None or type(value)is str for value in potions)else None)
                deck_size=(len(deck)if type(deck)is list and all(type(card)is dict and type(card.get('id'))is str for card in deck)else None)
                sample={'label':label,'prefix_sha256':key,'index':index,'act':entry.get('act'),'encounter':entry.get('encounter'),
                    'hp':hp,'max_hp':maximum,'hp_fraction':hp/maximum if hp is not None and maximum is not None and maximum>0 else None,
                    'gold':number(observation.get('gold')),'potions':potion_count,'deck_size':deck_size,'native_report':document_path}
                f1_samples.append(sample)
                for name in f1_values:
                    value=sample[name]
                    if value is None:f1_unknown[name]+=1
                    else:f1_values[name].append(value)

    return {'schema':'spire-preparation-review/v1','run':str(run),'evaluations_path':str(ledger),'result_path':str(result_path),
        'context':context,'context_state':'reported'if context else'unknown','result_status':final.get('status'),
        'records':len(rows),'native_noncache_records':native_reports,'synthetic_records_excluded':synthetic_rows,
        'cache_records_excluded_from_new_native_activity':cached_rows,'missing_native_decisions':missing_decisions,
        'preparation':proposal_summary(rows,final),'routes':{'checks':dict(route_checks),
            'work':{key:tally.report()for key,tally in route_moves.items()},'consumers':route_details},
        'resources':{'native_counters':{key:tally.report()for key,tally in resources.items()},
            'stored_trace_selected_actions':{key:tally.report()for key,tally in trace_attempts.items()},
            'coverage':{'complete_prefix_reports':complete_prefix,'partial_prefix_reports':partial_prefix,'unknown_reports':unknown_coverage,
                        'capture_from_index_reports':dict(capture_indices),'restored_prefix_reports':dict(restored_indices),
                        'requires_attached_after_setup':True,'requires_restored_inventory_not_counted':True},
            'native_events_by_act_room':{key:dict(value)for key,value in resource_by_room.items()},
            'native_events_by_potion_id':{key:dict(value)for key,value in resource_by_id.items()},'malformed_or_unknown_events':invalid_resource_events,
            'scope':'trace use/discard are selected attempts; used_removed/obtained/discarded require native events; counters cover after setup, not skipped CP history'},
        'shops':{'inventory_menu_reports':inventory_reports.report(),'observed_menu_samples':shop_menu_samples,
            'purchase_event_reports':buy_reports.report(),'attempted_items':dict(buy_attempts),'successfully_bought_items':dict(buy_successes),
            'purchases':purchase_samples,
            'native_success_count':purchase_success.report(),'unknown_purchase_success_events':purchase_unknown_success,
            'scope':'inventory menus are observations, not arrival events; actual target arrivals are native map_route_result.arrived_shops'},
        'f1':{'native_gate_entry_list_reports':gate_reports.report(),'distinct_stored_prefix_entries':len(f1_samples),
            'duplicate_prefix_reports':duplicate_f1,'entry_metrics':{key:distribution(value,f1_unknown[key])for key,value in f1_values.items()},
            'entries':f1_samples,'scope':'native first-menu observations; prefix dedup is descriptive, not semantic state equality or victory'},
        'issues':issues,'scope':'one explicitly selected run only; unknown is not zero; descriptive allocation diagnostics, no pruning, speedup, infeasibility or win certificate'}


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('run',type=Path);parser.add_argument('--evaluations',type=Path);parser.add_argument('--result',type=Path)
    parser.add_argument('--out',type=Path);parser.add_argument('--cpu-mask',type=lambda value:int(value,0),default=0xFFFF0000)
    args=parser.parse_args()
    if os.name=='nt':
        import ctypes
        from ctypes import wintypes
        kernel=ctypes.WinDLL('kernel32',use_last_error=True)
        kernel.GetCurrentProcess.argtypes=[];kernel.GetCurrentProcess.restype=wintypes.HANDLE
        kernel.SetProcessAffinityMask.argtypes=[wintypes.HANDLE,ctypes.c_size_t]
        kernel.SetProcessAffinityMask.restype=wintypes.BOOL
        kernel.SetPriorityClass.argtypes=[wintypes.HANDLE,wintypes.DWORD]
        kernel.SetPriorityClass.restype=wintypes.BOOL
        process=kernel.GetCurrentProcess()
        if args.cpu_mask<=0 or not kernel.SetProcessAffinityMask(process,args.cpu_mask):
            raise OSError('cannot apply analysis CPU mask; select a valid --cpu-mask explicitly')
        if not kernel.SetPriorityClass(process,0x00004000):  # below normal
            raise ctypes.WinError(ctypes.get_last_error())
    report=inspect(args.run,args.evaluations,args.result)
    text=json.dumps(report,ensure_ascii=False,indent=2,allow_nan=False)
    if args.out:
        args.out.parent.mkdir(parents=True,exist_ok=True);args.out.write_text(text+'\n',encoding='utf-8')
    else:print(text)


if __name__=='__main__':main()
