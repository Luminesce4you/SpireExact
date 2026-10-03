"""Build small, explicitly synthetic fixtures. No original STS2 card data."""
from pathlib import Path
import json
ROOT = Path(__file__).resolve().parents[1]
def get(p): return {'get': p}
def op(name, *args): return {name: list(args)}
def set_(p, v): return {'op': 'set', 'path': p, 'value': v}
def inc(p, v): return {'op': 'inc', 'path': p, 'value': v}
def cond(c, yes, no=()): return {'op': 'if', 'condition': c, 'then': list(yes), 'else': list(no)}
def draw(p, stream, lo, hi): return {'op':'rng_int','path':p,'stream':stream,'min':lo,'max':hi}
combat = op('in', get('phase'), ['fight1', 'boss'])
start_boss = [set_('phase','boss'), set_('enemy_hp',12), set_('energy',2),set_('block',0),set_('round',1),
              set_('cards',{'lit':{'strike_A':1,'strike_B':1,'strike_C':1,'guard_A':1}}),
              draw('enemy_attack','encounters',6,8)]
actions = [{'id':'start','when':op('eq',get('phase'),'start'),
            'effects':[draw('enemy_attack','encounters',4,5),set_('phase','fight1')]}]
for card in ('strike_A','strike_B','strike_C'):
    actions.append({'id':'play_'+card,
                    'when':op('and',combat,op('gt',get('energy'),0),op('gt',get('cards.'+card),0)),
                    'effects':[inc('energy',-1),inc('cards.'+card,-1),
                               set_('enemy_hp',op('max',0,op('sub',get('enemy_hp'),op('add',3,get('damage_bonus')))))]})
actions.extend([
    {'id':'play_guard_A','when':op('and',combat,op('gt',get('energy'),0),op('gt',get('cards.guard_A'),0)),
     'effects':[inc('energy',-1),inc('cards.guard_A',-1),inc('block',4)]},
    {'id':'use_potion','when':op('and',combat,op('gt',get('potion'),0)),
     'effects':[inc('potion',-1),set_('enemy_hp',op('max',0,op('sub',get('enemy_hp'),6)))]},
    {'id':'end_turn','when':combat,'effects':[
        set_('hp',op('max',0,op('sub',get('hp'),op('max',0,op('sub',get('enemy_attack'),get('block')))))),
        set_('block',0),set_('energy',2),inc('round',1)]},
    {'id':'camp_rest','when':op('eq',get('phase'),'camp'),
     'effects':[set_('hp',op('min',get('max_hp'),op('add',get('hp'),3)))]+start_boss},
    {'id':'camp_upgrade','when':op('eq',get('phase'),'camp'),
     'effects':[inc('damage_bonus',1)]+start_boss}
])
spec = {
    'schema':'spire-reference-ir/v1','model_id':'reference-two-room-resource-tradeoff-v1',
    'description':'Synthetic two-room example. Not STS2 rules or native RNG. All played cards exhaust; no recycling.',
    'initial':{'phase':'start','win':0,'hp':12,'max_hp':12,'potion':1,'enemy_hp':9,'enemy_attack':0,
               'energy':2,'block':0,'round':1,'damage_bonus':0,
               'cards':{'strike_A':1,'strike_B':1,'strike_C':1,'guard_A':1}},
    'rng_streams':['encounters','rewards'],
    'terminal':op('in',get('phase'),['won','dead']),
    'objective':[get('win'),get('hp'),get('potion')],
    'actions':actions,
    'after_each':[
        cond(op('and',op('eq',get('phase'),'fight1'),op('le',get('enemy_hp'),0)),[set_('phase','camp')]),
        cond(op('and',op('eq',get('phase'),'boss'),op('le',get('enemy_hp'),0)),[set_('phase','won'),set_('win',1)]),
        cond(op('le',get('hp'),0),[set_('phase','dead'),set_('win',0)])]
}
(ROOT/'examples/reference_campaign.json').write_text(json.dumps(spec,indent=2)+'\n',encoding='utf-8')
# A deliberately deceptive immediate-reward branch and a genuine no-op cycle.
graph={'schema':'spire-explicit-model/v1','model_id':'delayed-reward-and-cycle', 'root':'root','states':{
    'root':{'edges':[{'action':{'id':'greedy'},'to':'cheap'},{'action':{'id':'setup'},'to':'setup'},
                     {'action':{'id':'wait'},'to':'root'}]},
    'cheap':{'value':[1,5]},
    'setup':{'edges':[{'action':{'id':'finish'},'to':'best'}]},
    'best':{'value':[1,10]}}}
(ROOT/'examples/delayed_reward.json').write_text(json.dumps(graph,indent=2)+'\n',encoding='utf-8')
