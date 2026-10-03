"""Read-only, bounded cache for complete winning transcripts and replay evidence.

Only paths below an indexed run are read. A certificate filename or summary flag
is never sufficient for a verified badge. No game process is started here.
"""
from collections import Counter, OrderedDict
from pathlib import Path
import json
import threading

from spire_exact.canonical import canonical
from spire_exact.mode1 import check_winning_replay
from spire_exact.planning.io import read_json


def child(root, *parts):
    root = Path(root).resolve()
    path = root.joinpath(*parts).resolve()
    if not path.is_relative_to(root):
        raise ValueError('Evidence path escaped its run directory')
    return path


def actual_file(path):
    path = Path(path)
    return path if path.exists() else Path(str(path)+'.gz')


def file_signature(paths):
    answer=[]
    for path in paths:
        path=Path(path)
        try:
            info=path.stat();answer.append((str(path),info.st_mtime_ns,info.st_size))
        except FileNotFoundError:answer.append((str(path),None,None))
    return tuple(answer)


def browser_values(value):
    """JSON.parse cannot exactly represent native 64-bit RNG integers."""
    if type(value) is int and abs(value) > 2**53-1:
        return str(value)
    if isinstance(value, dict): return {k: browser_values(v) for k,v in value.items()}
    if isinstance(value, list): return [browser_values(v) for v in value]
    return value


def card_name(card):
    if not isinstance(card, dict): return str(card)
    upgrade = card.get('upgrade') or 0
    return str(card.get('id', '?')) + (f' +{upgrade}' if upgrade else '')


def action_title(action, observation, labels=()):
    kind = action.get('kind', '?')
    label = ' / '.join(str(x) for x in labels if x)
    def target():
        ident = action.get('target')
        if ident is None: return '未指定目标'
        enemy = next((e for e in observation.get('enemies') or [] if e.get('combat_id') == ident), None)
        return f'{enemy.get("id")}（战斗 ID {ident}）' if enemy else f'战斗 ID {ident}'
    if kind == 'play':
        cards = observation.get('hand') or []
        index = action.get('index')
        card = cards[index] if type(index) is int and 0<=index<len(cards) and cards[index].get('id')==action.get('card') else {'id':action.get('card')}
        return f'打出 {card_name(card)} · 手牌索引 {index} → {target()}'
    if kind == 'end_turn': return '结束回合'
    if kind == 'map': return f'选择地图位置 · 行 {action.get("row")} / 列 {action.get("col")}'
    if kind == 'next_act': return '进入下一幕'
    if kind == 'event': return f'事件选项：{action.get("key", action.get("index"))}'
    if kind == 'buy': return f'购买 {label or action.get("item_type", "商店项目")} · {action.get("cost", "?")} 金币 · 原始索引 {action.get("index")}'
    if kind == 'rest': return '营地：'+{'HEAL':'休息回血','SMITH':'升级卡牌'}.get(action.get('option'),str(action.get('option')))
    if kind == 'select_cards': return f'选择卡牌索引 {action.get("indices", [])}'+(' · '+label if label else '')
    if kind == 'use_potion': return f'使用药水 {action.get("potion")} · 槽位索引 {action.get("slot")} → {target()}'
    if kind == 'discard_potion': return f'丢弃药水 {action.get("potion")} · 槽位索引 {action.get("slot")}'
    if kind == 'card_reward': return f'选取奖励卡牌 {action.get("card")}'
    if kind == 'card_alternative': return '选择卡牌奖励的替代选项：'+(label or str(action.get('alternative')))
    if kind == 'reward': return '领取奖励：'+{'GoldReward':'金币','CardReward':'卡牌','PotionReward':'药水','RelicReward':'遗物'}.get(action.get('reward'),str(action.get('reward')))
    if kind == 'treasure': return f'领取宝箱遗物 {action.get("relic")}'
    names={'open_chest':'打开宝箱','treasure_skip':'跳过宝箱奖励','card_skip':'跳过选卡','rewards_skip':'离开奖励界面','shop_exit':'离开商店'}
    return names.get(kind, kind+' · '+json.dumps(action,ensure_ascii=False,separators=(',',':')))


class WinningRouteStore:
    def __init__(self, max_entries=2):
        self.cache = OrderedDict()
        self.lock = threading.RLock()
        self.max_entries = max_entries

    def load(self, manifest_path):
        manifest_path = Path(manifest_path)
        key=str(manifest_path.resolve())
        with self.lock:
            old=self.cache.get(key)
            if old and file_signature(row[0]for row in old[0])==old[0]:
                self.cache.move_to_end(key);return old[1]
        manifest = read_json(manifest_path)
        run_id, seed = manifest.get('run_id'), manifest.get('seed')
        if seed is None:
            return {'available':False,'run_id':run_id,'message':'请选择单种子的胜利运行。'}
        root = child(manifest_path.parent, 'seed-'+str(seed))
        result_path = child(root, 'result.json')
        if not result_path.exists():
            return {'available':False,'run_id':run_id,'message':'尚未生成完整运行结果。'}
        result = read_json(result_path)
        if result.get('status') != 'VERIFIED_WIN_IN_NATIVE_HOST':
            return {'available':False,'run_id':run_id,'message':'此运行尚无独立重放通过的胜利轨迹。'}
        label = result.get('best_label')
        if not isinstance(label,str) or not label or label in ('.','..') or any(c in label for c in '/\\'):
            raise ValueError('Invalid winning evidence label')
        paths = {
            'candidate':child(root,label,'data/decision.json'),
            'replay':child(root,'verify-'+label,'data/decision.json'),
            'candidate_identity':child(root,label,'data/identity.json'),
            'replay_identity':child(root,'verify-'+label,'data/identity.json'),
            'replay_request':child(root,'verify-'+label,'request.json'),
            'certificate':child(root,'certificate.json'),
        }
        resolved = {name:actual_file(path) for name,path in paths.items()}
        if any(not path.resolve().is_relative_to(root) for path in resolved.values()):
            raise ValueError('Evidence link escaped its run directory')
        def signature():
            return file_signature([manifest_path,result_path,*paths.values(),*(Path(str(p)+'.gz')for p in paths.values())])
        stamp=signature()
        with self.lock:
            old=self.cache.get(key)
            if old and old[0]==stamp:
                self.cache.move_to_end(key);return old[1]
            docs,issues={},[]
            for name,path in resolved.items():
                try:docs[name]=read_json(path,resolve_checkpoint=False)
                except (OSError,ValueError) as error:
                    docs[name]={};issues.append(f'{name}: {error}')
            candidate,replay=docs['candidate'],docs['replay']
            checks={}
            try:
                certificate=check_winning_replay(candidate,replay,
                    {'context':result['context'],'native':docs['candidate_identity']},
                    {'context':result['context'],'native':docs['replay_identity']})
                checks['candidate_replay_and_certificate_match']=canonical(certificate)==canonical(docs['certificate'])
            except (ValueError,KeyError,TypeError) as error:
                checks['candidate_replay_and_certificate_match']=False;issues.append(str(error))
            request=docs['replay_request'];perf=replay.get('performance')or{};counters=perf.get('counters')or{}
            cp=(candidate.get('performance')or{}).get('pid');rp=perf.get('pid')
            checks.update(
                manifest_seed_matches=str((result.get('context')or{}).get('seed'))==str(seed),
                fresh_native_process=type(cp)is int and type(rp)is int and cp>0 and rp>0 and cp!=rp,
                no_advisor_or_checkpoint=not request.get('advisor') and not request.get('checkpoint'),
                explicit_replay=request.get('generate_candidate') is False,
                full_requested_history=canonical(request.get('history'))==canonical(replay.get('trace')),
                zero_solver_calls=type(counters.get('replay_prefix_solver_calls'))is int and counters['replay_prefix_solver_calls']==0,
                zero_checkpoint_skips=type(counters.get('checkpoint_skipped_actions'))is int and counters['checkpoint_skipped_actions']==0)
            verified=all(checks.values()) and not issues
            source=replay if verified or not candidate.get('trace') else candidate
            trace=source.get('trace') or []
            evidence=source.get('decision_evidence') or []
            steps=[]
            for index,action in enumerate(trace):
                row=evidence[index] if index<len(evidence) else {}
                obs=row.get('observation')or{};menu=row.get('available_actions')
                chosen=[i for i,option in enumerate(menu or []) if canonical(option)==canonical(action)]
                labels=(row.get('option_labels')or[])
                chosen_labels=labels[chosen[0]] if chosen and chosen[0]<len(labels) else []
                same=(index<len(candidate.get('trace')or[]) and index<len(replay.get('trace')or[])
                      and index<len(candidate.get('decision_evidence')or[]) and index<len(replay.get('decision_evidence')or[])
                      and canonical(candidate['trace'][index])==canonical(replay['trace'][index])
                      and canonical(candidate['decision_evidence'][index])==canonical(replay['decision_evidence'][index]))
                steps.append({'index':index,'number':index+1,'phase':row.get('phase'),'act':obs.get('act'),'floor':obs.get('floor'),
                              'room':obs.get('room'),'turn':obs.get('turn'),'hp':obs.get('hp'),'max_hp':obs.get('max_hp'),
                              'energy':obs.get('energy'),'block':obs.get('block'),'gold':obs.get('gold'),
                              'kind':action.get('kind'),'title':action_title(action,obs,chosen_labels),
                              'menu_count':len(menu) if isinstance(menu,list) else None,
                              'chosen_indices':chosen,'menu_match':bool(chosen) if isinstance(menu,list) else None,'replay_match':same})
            document={'available':bool(trace),'run_id':run_id,'seed':str(seed),'label':label,'verified':verified,
                      'checks':checks,'issues':issues,'source':'independent_replay' if source is replay else 'candidate',
                      'total_steps':len(trace),'evidence_steps':len(evidence),'steps':steps,
                      'kind_counts':dict(Counter(step['kind']for step in steps)),
                      'context':result.get('context'),'certificate':docs['certificate'],
                      'terminal_observation':source.get('observation'),
                      'candidate_pid':cp,'replay_pid':rp,
                      'scope':'原生 DLL 离线 TestMode；不是正常 Godot 场景等价认证。',
                      'sources':{name:str(path.relative_to(root)) for name,path in resolved.items()},
                      '_trace':trace,'_evidence':evidence,'_candidate':candidate,'_replay':replay}
            if signature()!=stamp:raise ValueError('Evidence changed while loading; retry')
            self.cache[key]=(stamp,document);self.cache.move_to_end(key)
            while len(self.cache)>self.max_entries:self.cache.popitem(last=False)
            return document

    @staticmethod
    def metadata(document):
        return browser_values({k:v for k,v in document.items() if not k.startswith('_')})

    @staticmethod
    def step(document,index):
        if not document.get('available') or not 0<=index<document['total_steps']:
            raise ValueError('Decision index out of range')
        trace,evidence=document['_trace'],document['_evidence']
        row=evidence[index] if index<len(evidence) else {}
        after=(evidence[index+1].get('observation') if index+1<len(evidence) else document['terminal_observation'])
        menu=row.get('available_actions')or[];labels=row.get('option_labels')or[]
        chosen=document['steps'][index]['chosen_indices']
        options=[{'index':i,'action':a,'chosen':i in chosen,'labels':labels[i]if i<len(labels)else[],
                  'title':action_title(a,row.get('observation')or{},labels[i]if i<len(labels)else[])}for i,a in enumerate(menu)]
        return browser_values({'summary':document['steps'][index],'action':trace[index],
            'before':row.get('observation'),'after':after,'after_is_terminal':index==len(trace)-1,
            'options':options,'raw_evidence':row,
            'integer_encoding':'integers outside the JavaScript safe range are decimal strings; raw export preserves numeric types'})

    @staticmethod
    def export(document):
        return {'schema':'spireboard-winning-route/v1','run_id':document.get('run_id'),'seed':document.get('seed'),
                'verified':document.get('verified',False),'checks':document.get('checks',{}),'issues':document.get('issues',[]),
                'certificate':document.get('certificate'),'sources':document.get('sources'),
                'trace':document.get('_trace',[]),'decision_evidence':document.get('_evidence',[]),
                'terminal_observation':document.get('terminal_observation'),
                'candidate':document.get('_candidate'),'independent_replay':document.get('_replay')}
