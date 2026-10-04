// Browser-independent checks for the follow-along route guide.
const assert=require('node:assert/strict');
const {outline,floorOf,groups,instruction,instructionText,effects,stateText,flagReport,cardName}=require('../dashboard/web/guide.js');
const step=(index,act,floor,room,phase,kind,turn=null)=>({index,number:index+1,act,floor,room,phase,kind,turn,title:kind});
const steps=[step(0,0,1,'Event','event','event'),step(1,0,1,'Event','select_cards','select_cards'),step(2,0,1,null,'map','map'),
 step(3,0,2,'Monster','combat','play',1),step(4,0,2,'Monster','combat','end_turn',1),step(5,0,2,'Monster','combat','play',2),
 step(6,0,2,'Monster','select_cards','select_cards',2),step(7,0,2,'Monster','rewards','reward'),step(8,0,2,'Monster','card_reward','card_reward'),step(9,0,2,null,'map','map')];
const floors=outline(steps);
assert.deepEqual(floors.map(f=>[f.key,f.room,f.first,f.last,f.count]),[['0:1','Event',0,2,3],['0:2','Monster',3,9,7]]);
assert.equal(floorOf(floors,5).key,'0:2');
assert.deepEqual(groups(steps.slice(0,3)).map(g=>[g.label,g.steps.length]),[['事件',2],['前往下一处',1]]);
assert.deepEqual(groups(steps.slice(3)).map(g=>[g.label,g.steps.length]),[['回合 1',2],['回合 2',2],['奖励',2],['前往下一处',1]]);
assert.equal(cardName({id:'BASH',upgrade:1}),'痛击+');
assert.equal(cardName({id:'NEW_UNKNOWN_CARD',upgrade:0}),'NEW_UNKNOWN_CARD');

const hand=[{id:'STRIKE_IRONCLAD',upgrade:0},{id:'DEFEND_IRONCLAD',upgrade:0},{id:'BASH',upgrade:1}];
const enemies=[{id:'TWIG_SLIME_S',combat_id:1,hp:'11',block:'0'},{id:'TWIG_SLIME_S',combat_id:2,hp:'9',block:'0'}];
const play={action:{kind:'play',index:2,card:'BASH',target:2},before:{hp:'64',max_hp:'80',energy:3,block:'0',gold:99,hand,enemies,potions:[null,null],deck:[],relics:[]},
 after:{hp:'64',max_hp:'80',energy:1,block:'0',gold:99,hand:[hand[0],hand[1],{id:'POMMEL_STRIKE',upgrade:0}],enemies:[enemies[0],{...enemies[1],hp:'1'}],potions:[null,null],deck:[],relics:[]},options:[]};
const before=JSON.stringify(play);
const bash=instruction(play);
assert.equal(bash.verb,'打出');assert.equal(bash.object,'痛击+');
assert.equal(bash.where,'手牌左起第 3 张（共 3 张）');
assert.equal(bash.target.name,'树枝史莱姆（小）');assert.equal(bash.target.hp,'9');assert.equal(bash.target.where,'敌人列表第 2 个（共 2 个）');
assert.equal(instructionText(bash),'打出 痛击+ → 树枝史莱姆（小）（HP 9） · 手牌左起第 3 张（共 3 张）');
const fx=effects(play).map(f=>`${f.label??''}|${f.text}`);
assert.deepEqual(fx,['能量|3 → 1','树枝史莱姆（小）|HP 9 → 1','新入手牌|剑柄打击']);
assert.equal(JSON.stringify(play),before);
assert.equal(instruction({action:{kind:'use_potion',slot:0,potion:'FIRE_POTION',target:0},before:{enemies}}).target.name,'自身');

const endTurn={action:{kind:'end_turn'},before:play.after,after:{...play.after,hp:'58',energy:3,hand:[hand[0]],enemies:[]},options:[]};
assert.deepEqual(effects(endTurn).map(f=>f.text),['64 → 58','战斗结束']);
assert.deepEqual(effects({...endTurn,terminal:true}).map(f=>f.tone),['good']);

const map=instruction({action:{kind:'map',col:3,row:13},before:{},options:[
 {index:0,chosen:false,action:{kind:'map',col:1,row:13},labels:['go:Elite@a0']},{index:1,chosen:true,action:{kind:'map',col:3,row:13},labels:['go:Shop@a0']}]});
assert.equal(map.object,'商店');assert.equal(map.where,'可选节点左起第 2 个（共 2 个）');
assert.deepEqual(map.offered.map(o=>o.chosen),[false,true]);
const reward=instruction({action:{kind:'card_reward',index:1,card:'TREMBLE'},before:{},options:[
 {index:0,chosen:false,action:{kind:'card_reward',index:0,card:'SECOND_WIND'},labels:[]},{index:1,chosen:true,action:{kind:'card_reward',index:1,card:'TREMBLE'},labels:[]},
 {index:2,chosen:false,action:{kind:'card_alternative',index:0},labels:['card_skip']}]});
assert.equal(reward.where,'第 2 张（共 2 张）');assert.equal(reward.offered.length,2);assert.ok(reward.offered[1].chosen);
const fromHand=instruction({action:{kind:'select_cards',indices:[0,2]},before:{hand,selection:{purpose:'ChooseForCombat',native_method:'FromHand',source:'ASHWATER'}},options:[]});
assert.equal(fromHand.object,'打击（手牌第 1 张） → 痛击+（手牌第 3 张）');assert.ok(fromHand.where.includes('按此顺序'));
const upgrade=instruction({action:{kind:'select_cards',indices:[9]},before:{selection:{purpose:'Upgrade',native_method:'FromDeckForUpgrade'}},options:[{index:9,chosen:true,action:{kind:'select_cards',indices:[9]},labels:['upgrade:BASH']}]});
assert.equal(upgrade.verb,'升级');assert.equal(upgrade.object,'痛击');
assert.ok(instruction({action:{kind:'select_cards',indices:[4]},before:{},options:[]}).note.includes('原始索引'));
const oldRecord=instruction({action:{kind:'select_cards',indices:[10]},before:{deck:[{id:'BASH',upgrade:0},{id:'STRIKE_IRONCLAD',upgrade:0}],selection:{purpose:'Upgrade',native_method:'FromDeckForUpgrade',prompt:'TO_UPGRADE'}},
 after:{deck:[{id:'BASH',upgrade:1},{id:'STRIKE_IRONCLAD',upgrade:0}]},options:[]});
assert.equal(instructionText(oldRecord),'升级 痛击 · 从牌组中选');assert.ok(oldRecord.note.includes('推断'));assert.ok(!oldRecord.unnamed);
// Pile contents are not recorded: the guide can only give the position and must say so.
const headbutt=instruction({action:{kind:'select_cards',indices:[2]},before:{hand,selection:{purpose:'ChooseForCombat',native_method:'FromCombatPile',prompt:'HEADBUTT.selectionScreenPrompt',source:null}},after:{hand},options:[0,1,2,3].map(i=>({index:i,chosen:i===2,action:{kind:'select_cards',indices:[i]},labels:[]}))});
assert.equal(instructionText(headbutt),'选择 选牌界面第 3 张（共 4 张） · 从牌堆中选 · 来源：头槌');assert.ok(headbutt.unnamed);assert.ok(headbutt.note.includes('没有牌名'));
assert.equal(instruction({action:{kind:'select_cards',indices:[]},before:{},options:[]}).verb,'不选牌');
const pick=indices=>({index:0,chosen:false,action:{kind:'select_cards',indices},labels:[]});
// Armaments lists only upgradable cards, so the recorded index is not a hand position.
const armaments=instruction({action:{kind:'select_cards',indices:[0]},before:{hand,selection:{purpose:'Upgrade',native_method:'FromHandForUpgrade',source:'ARMAMENTS'}},
 after:{hand:[hand[0],{id:'DEFEND_IRONCLAD',upgrade:1},hand[2]]},options:[pick([0]),pick([1])]});
assert.equal(armaments.verb,'升级');assert.equal(armaments.object,'防御（手牌第 2 张）');assert.deepEqual(armaments.mark,[1]);assert.ok(armaments.note.includes('推断'));
assert.deepEqual(effects({action:{kind:'select_cards',indices:[0]},before:{hand},after:{hand:[hand[0],{id:'DEFEND_IRONCLAD',upgrade:1},hand[2]]}}).map(f=>`${f.label}|${f.text}`),['手牌升级|防御 → 防御+']);
const generated={action:{kind:'select_cards',indices:[1]},before:{hand,selection:{purpose:'Obtain',native_method:'FromChooseACardScreen'}},after:{hand:[...hand,{id:'POMMEL_STRIKE',upgrade:0}]},options:[pick([]),pick([0]),pick([1]),pick([2])]};
assert.equal(instructionText(instruction(generated)),'选择 剑柄打击 · 选牌界面第 2 张（共 3 张）');
const unnamed=instruction({...generated,before:{hand:[],selection:generated.before.selection}});
assert.equal(unnamed.object,'选牌界面第 2 张（共 3 张）');assert.ok(unnamed.unnamed);assert.ok(!instruction(generated).unnamed);assert.ok(unnamed.note.includes('没有候选牌的名称'));
assert.equal(instruction({action:{kind:'select_cards',indices:[3]},before:{selection:{purpose:'Other',native_method:'FromDeckForEnchantment',source:'SHARP'}},options:[{index:3,chosen:true,action:{kind:'select_cards',indices:[3]},labels:['other:BASH']}]}).verb,'附魔');
// Records that carry the candidates (selection.cards) name the cards without inference.
const pileCards=[{id:'BASH',upgrade:0},{id:'STRIKE_IRONCLAD',upgrade:0},{id:'POMMEL_STRIKE',upgrade:1},{id:'BASH',upgrade:0}];
const named=instruction({action:{kind:'select_cards',indices:[2]},before:{hand,selection:{purpose:'ChooseForCombat',native_method:'FromCombatPile',prompt:'HEADBUTT.selectionScreenPrompt',source:null,cards:pileCards}},after:{hand},options:[]});
assert.equal(instructionText(named),'选择 剑柄打击+ · 从牌堆中选 · 来源：头槌');assert.ok(!named.unnamed);assert.ok(named.note.includes('按牌名'));
assert.deepEqual(named.offered.map(o=>`${o.text}|${o.chosen}`),['痛击|false','打击|false','剑柄打击+|true','痛击|false']);
const screen=instruction({...generated,before:{hand:[],selection:{...generated.before.selection,cards:[{id:'BASH',upgrade:0},{id:'POMMEL_STRIKE',upgrade:0},{id:'STRIKE_IRONCLAD',upgrade:0}]}}});
assert.equal(instructionText(screen),'选择 剑柄打击 · 选牌界面第 2 张（共 3 张）');assert.ok(!screen.unnamed);assert.ok(!screen.note);assert.equal(screen.offered.length,3);
const listed=instruction({action:{kind:'select_cards',indices:[1]},before:{hand,selection:{purpose:'Upgrade',native_method:'FromHandForUpgrade',source:'ARMAMENTS',cards:[hand[0],hand[1]]}},options:[]});
assert.equal(listed.object,'防御（手牌第 2 张）');assert.deepEqual(listed.mark,[1]);assert.ok(!listed.note);
const partHand=instruction({action:{kind:'select_cards',indices:[1,0]},before:{hand,selection:{purpose:'Exhaust',native_method:'FromHand',cards:[hand[1],hand[2]]}},options:[]});
assert.equal(partHand.object,'痛击+（手牌第 3 张） → 防御（手牌第 2 张）');assert.deepEqual(partHand.mark,[2,1]);assert.ok(partHand.where.includes('按此顺序'));
const removal=instruction({action:{kind:'select_cards',indices:[0]},before:{selection:{purpose:'Remove',native_method:'FromDeckForRemoval',cards:[{id:'BASH',upgrade:1},{id:'STRIKE_IRONCLAD',upgrade:0}]}},options:[]});
assert.equal(instructionText(removal),'移除 痛击+ · 从牌组中选');assert.equal(removal.offered,null);
const leaveShop={action:{kind:'map',col:3,row:14},before:{},options:[{index:0,chosen:false,action:{kind:'buy',index:0},labels:['card:ANGER']},{index:1,chosen:true,action:{kind:'map',col:3,row:14},labels:[]}]};
assert.equal(instruction(leaveShop).object,'下一个节点');
assert.equal(instructionText(instruction(leaveShop,{kind:'play',room:'Elite'})),'前往 精英 · 先离开商店 · 唯一可选节点');
const buy=instruction({action:{kind:'buy',index:10,item_type:'MerchantPotionEntry',cost:50},before:{},options:[{index:6,chosen:true,action:{kind:'buy',index:10},labels:['potion:FIRE_POTION']}]});
assert.equal(buy.object,'火焰药水');assert.equal(buy.where,'药水 · 50 金币');
assert.equal(instruction({action:{kind:'rest',index:1,option:'SMITH'},before:{},options:[]}).object,'升级卡牌');
assert.equal(instruction({action:{kind:'mystery'},summary:{title:'打出 BASH'},before:{},options:[]}).verb,'打出 痛击（BASH）');
const gained=effects({action:{kind:'rest'},before:{hp:'50',max_hp:'80',gold:10,deck:[{id:'BASH',upgrade:0}],relics:['BURNING_BLOOD'],potions:[null]},
 after:{hp:'50',max_hp:'80',gold:35,deck:[{id:'BASH',upgrade:1}],relics:['BURNING_BLOOD','ART_OF_WAR'],potions:['FIRE_POTION']}}).map(f=>`${f.label}|${f.text}`);
assert.deepEqual(gained,['金币|10 → 35','获得药水|火焰药水（第 1 格）','升级|痛击 → 痛击+','获得遗物|孙子兵法']);
assert.ok(stateText(play.before).includes('手牌 打击、防御、痛击+'));
const report=flagReport({run:'r1',seed:'42',character:'IRONCLAD',ascension:10},[{number:4,place:'第 1 幕 · 第 2 层 · 回合 1',instruction:instructionText(bash),expected:stateText(play.before),note:'手牌不同'}]);
assert.ok(report.includes('不属于求解证据'));assert.ok(report.includes('种子 42 · 铁甲战士 · 进阶 10'));assert.ok(report.includes('实际看到：手牌不同'));
console.log('Guide outline, grouping, instructions, expected changes and mismatch report passed.');
