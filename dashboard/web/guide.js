// Follow-along view of a verified winning route: turns recorded decisions into
// instructions a player can carry out and check against the real game.
(function(root){
'use strict';
const isNode=typeof module!=='undefined'&&module.exports;
const zh=isNode?require('./route_zh.js'):root.routeZh;
const esc=s=>String(s??'').replace(/[&<>"']/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
const num=v=>v==null||v===''||!Number.isFinite(Number(v))?null:Number(v);
const plus=c=>{const u=Number(c?.upgrade)||0;return u>1?'+'+u:u===1?'+':''};
const cardName=c=>c==null?'空':typeof c==='object'?zh.name(c.id??'?')+plus(c):zh.name(c);
const actLabel=a=>a==null?'未知幕':`第 ${Number(a)+1} 幕`;
const roomLabel=r=>r==null?'选择路线':zh.name(r);
const characters={IRONCLAD:'铁甲战士'};

// One entry per consecutive act/floor run of steps, in route order.
function outline(steps){
 const floors=[];let cur=null;
 for(const s of steps||[]){
  const key=`${s.act}:${s.floor}`;
  if(!cur||cur.key!==key){cur={key,act:s.act,floor:s.floor,room:null,first:s.index,last:s.index,count:0};floors.push(cur)}
  cur.last=s.index;cur.count++;if(cur.room==null&&s.room!=null)cur.room=s.room;
 }
 return floors;
}
const floorOf=(floors,index)=>floors.find(f=>index>=f.first&&index<=f.last)||floors[0]||null;
const groupNames={move:'前往下一处',rewards:'奖励',shop:'商店',rest:'营地',treasure:'宝箱',event:'事件',select:'选牌'};
function bucket(s){
 if(s.turn!=null&&(s.phase==='combat'||s.phase==='select_cards'))return 'turn:'+s.turn;
 if(s.kind==='map'||s.kind==='next_act')return 'move';
 if(s.phase==='select_cards')return null;
 return {rewards:'rewards',card_reward:'rewards',shop:'shop',rest:'rest',treasure:'treasure',event:'event',combat:'combat'}[s.phase]||s.phase||'other';
}
// Splits one floor into combat turns and non-combat stages.
function groups(steps){
 const out=[];let cur=null;
 for(const s of steps||[]){
  let key=bucket(s);if(key==null)key=cur?cur.key:'select';
  if(!cur||cur.key!==key){const turn=key.startsWith('turn:')?Number(key.slice(5)):null;cur={key,turn,label:turn==null?groupNames[key]||key:`回合 ${turn}`,steps:[]};out.push(cur)}
  cur.steps.push(s);
 }
 return out;
}
const rewardNames={GoldReward:'金币',CardReward:'卡牌',PotionReward:'药水',RelicReward:'遗物'};
const purposes={Upgrade:'升级',Remove:'移除',Transform:'变化',Exhaust:'消耗',Discard:'弃掉',Retain:'保留',Duplicate:'复制'};
const goRoom=o=>{const l=(o?.labels||[]).find(x=>String(x).startsWith('go:'));return l?String(l).slice(3).split('@a')[0]:null};
const labelId=l=>{const s=String(l??''),k=s.indexOf(':');return k<0?s:s.slice(k+1)};
function eventText(key){const t=zh.event(key),i=t.indexOf(' · ');return i<0?t:t.slice(i+3)}
function target(id,b){
 if(id==null)return null;
 const list=b?.enemies||[],i=list.findIndex(e=>e.combat_id===id);
 if(i<0)return {name:id===0?'自身':`战斗 ID ${id}`,id};
 const e=list[i];
 return {name:zh.name(e.id),hp:e.hp,id,where:list.length>1?`敌人列表第 ${i+1} 个（共 ${list.length} 个）`:''};
}
// Positions are the recorded zero-based indices plus one; names come from the Wiki snapshot.
// `next` is the summary of the following step; a shop exit carries no room label of its own.
function instruction(d,next){
 const a=d?.action||{},b=d?.before||{},opts=d?.options||[],chosen=opts.find(o=>o.chosen),same=k=>opts.filter(o=>o.action?.kind===k);
 switch(a.kind){
 case 'play':{
  const hand=b.hand||[],c=hand[a.index]||{id:a.card};
  return {verb:'打出',object:cardName(c),where:hand.length?`手牌左起第 ${a.index+1} 张（共 ${hand.length} 张）`:`手牌原始索引 ${a.index}`,target:target(a.target,b)};
 }
 case 'end_turn':return {verb:'结束回合'};
 case 'use_potion':return {verb:'使用药水',object:zh.name(a.potion),where:`药水栏第 ${Number(a.slot)+1} 格`,target:target(a.target,b)};
 case 'discard_potion':return {verb:'丢弃药水',object:zh.name(a.potion??b.potions?.[a.slot]??'?'),where:`药水栏第 ${Number(a.slot)+1} 格`};
 case 'select_cards':{
  const sel=b.selection||{},idx=a.indices||[],method=String(sel.native_method||''),origin=sel.source||(/^[A-Z0-9_]+\..*selectionScreenPrompt$/.test(sel.prompt||'')?sel.prompt.split('.')[0]:''),source=origin?`来源：${zh.name(origin)}`:'';
  const verb=/Enchant/.test(method)?'附魔':purposes[sel.purpose]||'选择',hand=b.hand||[],after=d?.after?.hand;
  if(!idx.length)return {verb:'不选牌',object:'直接确认',where:source};
  // Newer records list the candidates themselves (selection.cards), in the order the indices refer to.
  const cands=Array.isArray(sel.cards)&&idx.every(i=>sel.cards[i]!=null)?sel.cards:null;
  if(cands){
   if(/^FromHand/.test(method)&&hand.length){
    // The screen may list only part of the hand; the candidates keep the hand order.
    const at=[];let k=0;
    hand.forEach((c,i)=>{if(k<cands.length&&c.id===cands[k].id&&num(c.upgrade)===num(cands[k].upgrade))at[k++]=i});
    if(k===cands.length)return {verb,object:idx.map(i=>`${cardName(cands[i])}（手牌第 ${at[i]+1} 张）`).join(method==='FromHand'?' → ':'、'),
     where:['从手牌中选',source,method==='FromHand'&&idx.length>1?'按此顺序':''].filter(Boolean).join(' · '),mark:idx.map(i=>at[i])};
   }
   const pile=method==='FromCombatPile',screen=method==='FromChooseACardScreen',listed=pile||screen||/Grid/.test(method);
   const from=/Deck/.test(method)?'从牌组中选':/Grid/.test(method)?'从弹出的牌列表中选':pile?'从牌堆中选':screen?`选牌界面${idx.map(i=>`第 ${i+1} 张`).join('、')}（共 ${cands.length} 张）`:'';
   return {verb,object:idx.map(i=>cardName(cands[i])).join('、'),where:[from,source].filter(Boolean).join(' · '),
    offered:listed&&cands.length>1?cands.map((c,i)=>({text:cardName(c),chosen:idx.includes(i)})):null,
    note:pile?'游戏里牌堆的显示顺序可能和这里列出的顺序不同，请按牌名找。':''};
  }
  if(method==='FromHand'&&hand.length)return {verb,object:idx.map(i=>`${cardName(hand[i]??{id:'?'})}（手牌第 ${i+1} 张）`).join(' → '),where:['从手牌中选',source,idx.length>1?'按此顺序':''].filter(Boolean).join(' · ')};
  if(chosen?.labels?.length===idx.length)return {verb,object:chosen.labels.map(l=>zh.name(labelId(l))).join('、'),where:[/Deck/.test(method)?'从牌组中选':'',source].filter(Boolean).join(' · ')};
  // The record lists only the cards that may be picked, without names; the hand before and after still shows what happened.
  if(method==='FromHandForUpgrade'&&Array.isArray(after)){
   const hit=hand.map((c,i)=>after[i]&&after[i].id===c.id&&num(after[i].upgrade)>num(c.upgrade)?i:-1).filter(i=>i>=0);
   if(hit.length===idx.length)return {verb,object:hit.map(i=>`${cardName(hand[i])}（手牌第 ${i+1} 张）`).join('、'),where:['从手牌中选',source].filter(Boolean).join(' · '),mark:hit,
    note:'选牌界面只列出可升级的牌；这里的牌名和位置由选完后手牌的变化推断。'};
  }
  // Older records carry no labels for deck screens; the deck before and after names the cards.
  if(/Deck|Grid/.test(method)&&Array.isArray(b.deck)&&Array.isArray(d?.after?.deck)){
   const delta=difference(b.deck,d.after.deck,cardName),upgraded=delta.removed.filter(n=>delta.added.includes(n+'+'));
   const names=sel.purpose==='Upgrade'?upgraded:sel.purpose==='Obtain'?(delta.removed.length?[]:delta.added):delta.removed;
   if(names.length===idx.length)return {verb,object:names.join('、'),where:[/Deck/.test(method)?'从牌组中选':'从弹出的牌列表中选',source].filter(Boolean).join(' · '),
    note:'记录里没有候选牌的名称；这里的牌名由选完后牌组的变化推断。'};
  }
  const menu=opts.filter(o=>o.action?.kind==='select_cards'&&o.action.indices?.length===1).length,place=idx.map(i=>`第 ${i+1} 张`).join('、')+(menu?`（共 ${menu} 张）`:'');
  if(method==='FromChooseACardScreen'){
   const gain=Array.isArray(after)&&hand.length?difference(hand,after,cardName):null,known=gain&&gain.added.length===idx.length&&!gain.removed.length;
   return {verb,object:known?gain.added.join('、'):`选牌界面${place}`,where:[known?`选牌界面${place}`:'',source].filter(Boolean).join(' · '),unnamed:!known,
    note:known?'记录里没有候选牌的名称；这里的牌名由选完后手牌新增的牌推断。':'记录里没有候选牌的名称，只有它在选牌界面里的位置。'};
  }
  // Draw and discard piles are not part of the recorded observation, so only the position is known.
  return {verb,object:`选牌界面${place}`,where:[method==='FromCombatPile'?'从牌堆中选':'',source].filter(Boolean).join(' · '),unnamed:true,
   note:`记录里只有序号（原始索引 ${idx.join(', ')}），没有牌名和牌堆内容；游戏里的显示顺序可能和记录顺序不同，请结合「之后应变为」核对。`};
 }
 case 'map':{
  const menu=same('map').slice().sort((x,y)=>x.action.col-y.action.col),rank=menu.findIndex(o=>o.action.col===a.col&&o.action.row===a.row),room=goRoom(chosen)||(next&&next.kind!=='map'?next.room:null);
  return {verb:'前往',object:room?zh.name(room):'下一个节点',where:[same('buy').length?'先离开商店':'',menu.length>1&&rank>=0?`可选节点左起第 ${rank+1} 个（共 ${menu.length} 个）`:menu.length===1?'唯一可选节点':''].filter(Boolean).join(' · '),
   note:`地图原始坐标：行 ${a.row} / 列 ${a.col}`,offered:menu.length>1?menu.map(o=>({text:`${goRoom(o)?zh.name(goRoom(o)):'节点'} · 列 ${o.action.col}`,chosen:!!o.chosen})):null};
 }
 case 'event':{
  const menu=same('event'),key=String(a.key??''),host=key.includes('.')?zh.name(key.split('.')[0]):'';
  return {verb:'选择',object:eventText(key),where:[host,menu.length>1?`第 ${Number(a.index)+1} 个选项（共 ${menu.length} 个）`:''].filter(Boolean).join(' · '),
   offered:menu.length>1?menu.map(o=>({text:eventText(o.action.key),chosen:!!o.chosen})):null};
 }
 case 'reward':{
  const menu=same('reward');
  return {verb:'领取',object:rewardNames[a.reward]||zh.name(a.reward??'奖励'),where:menu.length>1?`奖励列表第 ${Number(a.index)+1} 项（共 ${menu.length} 项）`:'',
   offered:menu.length>1?menu.map(o=>({text:rewardNames[o.action.reward]||zh.name(o.action.reward??'奖励'),chosen:!!o.chosen})):null};
 }
 case 'card_reward':{
  const menu=same('card_reward');
  return {verb:'选牌',object:zh.name(a.card),where:`第 ${Number(a.index)+1} 张（共 ${menu.length||'?'} 张）`,offered:menu.map(o=>({text:zh.name(o.action.card),chosen:!!o.chosen}))};
 }
 case 'card_alternative':case 'card_skip':{
  const label=chosen?.labels?.[0],skip=a.kind==='card_skip'||label==null||label==='card_skip';
  return {verb:skip?'跳过选牌':'选择替代选项',object:skip?'':zh.label(label),note:skip?'不拿任何一张牌。':'',offered:same('card_reward').map(o=>({text:zh.name(o.action.card),chosen:false}))};
 }
 case 'rewards_skip':{
  const left=same('reward').map(o=>rewardNames[o.action.reward]||zh.name(o.action.reward??'奖励'));
  return {verb:'离开奖励界面',where:left.length?`不领取：${left.join('、')}`:''};
 }
 case 'buy':{
  const label=String(chosen?.labels?.[0]??''),k=label.indexOf(':'),type={card:'卡牌',relic:'遗物',potion:'药水'}[label.slice(0,Math.max(0,k))];
  const removal=a.item_type==='MerchantCardRemovalEntry'||label==='buy_removal';
  return {verb:'购买',object:removal?'移除卡牌服务':k>0?zh.name(label.slice(k+1)):zh.name(a.item_type??'商品'),where:[type,a.cost!=null?`${a.cost} 金币`:''].filter(Boolean).join(' · ')};
 }
 case 'rest':{
  const menu=same('rest');
  return {verb:'营地选择',object:zh.name(a.option??'?'),offered:menu.length>1?menu.map(o=>({text:zh.name(o.action.option??'?'),chosen:!!o.chosen})):null};
 }
 case 'open_chest':return {verb:'打开宝箱'};
 case 'treasure':return {verb:'拿取遗物',object:zh.name(a.relic??'?')};
 case 'treasure_skip':return {verb:'跳过宝箱遗物'};
 case 'next_act':return {verb:'进入下一幕'};
 default:return {verb:zh.title(d?.summary?.title||a.kind||'未记录的动作')};
 }
}
function instructionText(ins){
 return [ins.verb,ins.object].filter(Boolean).join(' ')+(ins.target?` → ${ins.target.name}${ins.target.hp!=null?`（HP ${ins.target.hp}）`:''}`:'')+(ins.where?` · ${ins.where}`:'');
}
function tally(xs,key){const m=new Map();for(const x of xs||[]){const k=key(x);m.set(k,(m.get(k)||0)+1)}return m}
function difference(before,after,key){
 const a=tally(before,key),b=tally(after,key),added=[],removed=[];
 for(const k of new Set([...a.keys(),...b.keys()])){const d=(b.get(k)||0)-(a.get(k)||0);for(let i=0;i<Math.abs(d);i++)(d>0?added:removed).push(k)}
 return {added,removed};
}
// What should differ at the next decision point. That boundary can include enemy
// actions and automatic resolution, so these are checks, not a single card's effect.
function effects(d){
 const b=d?.before,a=d?.after,kind=d?.action?.kind,out=[];
 if(!b||!a)return out;
 if(d.terminal)return [{text:'记录的原生终局：胜利结束',tone:'good'}];
 const stat=(k,label,tone)=>{const x=num(b[k]),y=num(a[k]);if(x!=null&&y!=null&&x!==y)out.push({label,text:`${x} → ${y}`,tone:tone?tone(x,y):''})};
 const be=b.enemies||[],ae=a.enemies||[],ended=be.length>0&&!ae.length;
 stat('hp','生命',(x,y)=>y<x?'bad':'good');
 if(kind!=='end_turn'&&!ended){stat('block','格挡');stat('energy','能量')}
 if(ended)out.push({text:'战斗结束',tone:'good'});
 else{
  const after=new Map(ae.map(e=>[e.combat_id,e])),before=new Set(be.map(e=>e.combat_id));
  for(const e of be){
   const n=after.get(e.combat_id);
   if(!n){out.push({label:zh.name(e.id),text:'离场',tone:'enemy'});continue}
   const parts=[];
   if(num(e.hp)!==num(n.hp))parts.push(`HP ${e.hp} → ${n.hp}`);
   if(num(e.block)!==num(n.block))parts.push(`格挡 ${e.block} → ${n.block}`);
   if(parts.length)out.push({label:zh.name(e.id),text:parts.join('，'),tone:'enemy'});
  }
  for(const e of ae)if(!before.has(e.combat_id))out.push({label:zh.name(e.id),text:`出现 · HP ${e.hp}`,tone:'enemy'});
 }
 if(kind!=='end_turn'&&Array.isArray(b.hand)&&Array.isArray(a.hand)&&!ended){
  const rest=kind==='play'?b.hand.filter((_,i)=>i!==d.action.index):b.hand,delta=difference(rest,a.hand,cardName);
  for(const name of [...delta.removed]){const i=delta.added.indexOf(name+'+');if(i>=0){delta.added.splice(i,1);delta.removed.splice(delta.removed.indexOf(name),1);out.push({label:'手牌升级',text:`${name} → ${name}+`})}}
  if(delta.added.length)out.push({label:'新入手牌',text:delta.added.join('、')});
  if(delta.removed.length)out.push({label:'离开手牌',text:delta.removed.join('、')});
 }
 stat('gold','金币');stat('max_hp','生命上限');
 const bp=b.potions||[],ap=a.potions||[];
 for(let i=0;i<Math.max(bp.length,ap.length);i++){
  if(bp[i]===ap[i])continue;
  if(ap[i]!=null)out.push({label:'获得药水',text:`${zh.name(ap[i])}（第 ${i+1} 格）`});
  else if(!(kind==='use_potion'&&d.action.slot===i)&&!(kind==='discard_potion'&&d.action.slot===i))out.push({label:'失去药水',text:zh.name(bp[i])});
 }
 const deck=difference(b.deck,a.deck,cardName);
 for(const name of [...deck.removed]){const i=deck.added.indexOf(name+'+');if(i>=0){deck.added.splice(i,1);deck.removed.splice(deck.removed.indexOf(name),1);out.push({label:'升级',text:`${name} → ${name}+`})}}
 if(deck.added.length)out.push({label:'加入牌组',text:deck.added.join('、')});
 if(deck.removed.length)out.push({label:'移出牌组',text:deck.removed.join('、')});
 const relics=difference(b.relics,a.relics,zh.name);
 if(relics.added.length)out.push({label:'获得遗物',text:relics.added.join('、')});
 if(relics.removed.length)out.push({label:'失去遗物',text:relics.removed.join('、')});
 return out;
}
function stateText(o){
 if(!o)return '未记录状态';
 const parts=[`生命 ${o.hp??'—'}/${o.max_hp??'—'}`];
 if(o.block!=null&&o.enemies?.length)parts.push(`格挡 ${o.block}`);
 if(o.energy!=null)parts.push(`能量 ${o.energy}`);
 if(o.gold!=null)parts.push(`金币 ${o.gold}`);
 if(o.hand?.length)parts.push('手牌 '+o.hand.map(cardName).join('、'));
 if(o.enemies?.length)parts.push('敌人 '+o.enemies.map(e=>`${zh.name(e.id)} HP ${e.hp}`).join('、'));
 return parts.join(' · ');
}
// Plain-text notes a player can paste elsewhere. They are observations, not solver evidence.
function flagReport(context,items){
 const head=['SpireBoard 人工核对记录（个人笔记，不属于求解证据）',
  `运行 ${context.run} · 种子 ${context.seed} · ${characters[context.character]||context.character||'角色未记录'} · 进阶 ${context.ascension??'—'}`];
 return head.concat(items.map(i=>[`第 ${i.number} 步（${i.place}）`,`  路线指令：${i.instruction}`,`  记录状态：${i.expected}`,`  实际看到：${i.note}`].join('\n'))).join('\n\n');
}
if(isNode){module.exports={outline,floorOf,groups,instruction,instructionText,effects,stateText,flagReport,cardName};return}

const byId=id=>document.getElementById(id);
const G={run:null,meta:null,api:null,index:0,floors:[],floor:null,cache:new Map(),inflight:new Map(),saved:null,epoch:0,flagOpen:false};
const storeKey=run=>'spireboard.guide.v1:'+run;
function loadSaved(run){
 try{const v=JSON.parse(localStorage.getItem(storeKey(run))||'null');
  if(v&&typeof v==='object')return {cursor:Number.isInteger(v.cursor)?v.cursor:null,flags:v.flags&&typeof v.flags==='object'?v.flags:{},setup:!!v.setup,finished:!!v.finished}}catch{}
 return {cursor:null,flags:{},setup:false,finished:false};
}
function persist(){try{localStorage.setItem(storeKey(G.run),JSON.stringify(G.saved))}catch{}}
const slim=o=>o&&{act:o.act,floor:o.floor,room:o.room,hp:o.hp,max_hp:o.max_hp,gold:o.gold,energy:o.energy,block:o.block,turn:o.turn,hand:o.hand,enemies:o.enemies,potions:o.potions,selection:o.selection,deck:o.deck,relics:o.relics};
// Keeps what the guide renders; combinatorial selection menus keep only the chosen entry.
function compact(d){
 const options=d.options.length>80?d.options.filter(o=>o.chosen):d.options;
 return {summary:d.summary,action:d.action,before:slim(d.before),after:slim(d.after),terminal:d.after_is_terminal,options:options.map(o=>({index:o.index,chosen:o.chosen,action:o.action,labels:o.labels}))};
}
function detail(index){
 if(G.cache.has(index))return Promise.resolve(G.cache.get(index));
 if(G.inflight.has(index))return G.inflight.get(index);
 const run=G.run,job=fetch(`/api/winning-route?run=${encodeURIComponent(run)}&step=${index}`)
  .then(r=>{if(!r.ok)throw new Error(`读取第 ${index+1} 步失败（HTTP ${r.status}）`);return r.json()})
  .then(d=>{const c=compact(d);if(G.run===run)G.cache.set(index,c);return c})
  .finally(()=>{if(G.inflight.get(index)===job)G.inflight.delete(index)});
 G.inflight.set(index,job);return job;
}
async function loadFloor(f){
 const todo=[];for(let i=f.first;i<=f.last;i++)if(!G.cache.has(i))todo.push(i);
 let next=0;const worker=async()=>{while(next<todo.length)await detail(todo[next++])};
 await Promise.all(Array.from({length:Math.min(6,todo.length)},worker));
}
const floorSteps=f=>G.meta.steps.slice(f.first,f.last+1);
const flagCount=()=>Object.keys(G.saved.flags).length;
function handHTML(hand,marked){return '<ol class="g-chips g-hand">'+hand.map((c,i)=>`<li class="${marked.includes(i)?'hit':''}" title="${esc(c?.id)}"><small>${i+1}</small>${esc(cardName(c))}</li>`).join('')+'</ol>'}
function enemiesHTML(list,hit){return '<ul class="g-chips g-enemies">'+list.map((e,i)=>`<li class="${e.combat_id===hit?'hit':''}" title="${esc(e.id)} · 战斗 ID ${esc(e.combat_id)}">${list.length>1?`<small>${i+1}</small>`:''}${esc(zh.name(e.id))}<b>HP ${esc(e.hp)}</b>${num(e.block)?`<i>格挡 ${esc(e.block)}</i>`:''}</li>`).join('')+'</ul>'}
function statsHTML(o,combat){
 const items=[['生命',`${o.hp??'—'} / ${o.max_hp??'—'}`]];
 if(combat){items.push(['格挡',o.block??'—'],['能量',o.energy??'—'])}else items.push(['金币',o.gold??'—']);
 return '<dl class="g-stats">'+items.map(([k,v])=>`<div><dt>${k}</dt><dd>${esc(v)}</dd></div>`).join('')+'</dl>';
}
// Expected screen right before the current action.
function nowHTML(d){
 const b=d.before||{},a=d.action||{},combat=Array.isArray(b.enemies)&&b.enemies.length>0;
 const marked=a.kind==='play'?[a.index]:a.kind==='select_cards'?(instruction(d).mark||(b.selection?.native_method==='FromHand'?a.indices||[]:[])):[];
 const potions=(b.potions||[]).some(p=>p!=null)?'<div class="g-now-row"><span>药水</span><ol class="g-chips">'+b.potions.map((p,i)=>`<li class="${a.kind==='use_potion'&&a.slot===i?'hit':''}${p==null?' vacant':''}"><small>${i+1}</small>${esc(p==null?'空':zh.name(p))}</li>`).join('')+'</ol></div>':'';
 return `<div class="g-now"><div class="g-now-title">此刻游戏里应是</div>${statsHTML(b,combat)}${b.hand?.length?`<div class="g-now-row"><span>手牌</span>${handHTML(b.hand,marked)}</div>`:''}${combat?`<div class="g-now-row"><span>敌人</span>${enemiesHTML(b.enemies,a.target)}</div>`:''}${potions}</div>`;
}
function flagFormHTML(flag){
 return `<form class="g-flag-form" data-flag-form><label for="g-flag-text">这一步和游戏哪里不一样？</label><textarea id="g-flag-text" rows="2" placeholder="例如：手牌第 3 张是打击；敌人生命是 44">${esc(flag?.note||'')}</textarea><div class="g-flag-actions"><button type="submit" class="primary-button">保存记录</button><button type="button" class="ghost-button" data-flag-cancel>取消</button>${flag?'<button type="button" class="ghost-button danger" data-flag-delete>删除记录</button>':''}<small>只保存在这个浏览器里，不会写入求解证据。</small></div></form>`;
}
function rowHTML(s){
 const d=G.cache.get(s.index),stage=s.index<G.index?'done':s.index===G.index?'current':'todo',flag=G.saved.flags[s.index];
 let body;
 if(!d)body=`<div class="g-line"><strong class="g-obj">${esc(zh.title(s.title))}</strong></div><div class="g-where">正在读取这一步…</div>`;
 else{
  const ins=instruction(d,G.meta.steps[s.index+1]),fx=effects(d),where=[ins.where,ins.target?.where].filter(Boolean).join(' · ');
  body=`<div class="g-line"><b class="g-verb">${esc(ins.verb)}</b>${ins.object?`<strong class="g-obj">${esc(ins.object)}</strong>`:''}${ins.target?`<span class="g-target">→ ${esc(ins.target.name)}${ins.target.hp!=null?` <small>HP ${esc(ins.target.hp)}</small>`:''}</span>`:''}</div>`
   +(where?`<div class="g-where">${esc(where)}</div>`:'')
   +(ins.offered?.length?`<ul class="g-offered" aria-label="当时的全部选项">${ins.offered.map(o=>`<li class="${o.chosen?'chosen':''}">${esc(o.text)}</li>`).join('')}</ul>`:'')
   +(stage==='current'?nowHTML(d):'')
   +(stage==='current'&&ins.note?`<div class="g-note">${esc(ins.note)}</div>`:'')
   +(fx.length?`<div class="g-effects"><span title="到下一个决策点为止的变化，可能包含敌方行动与自动结算">之后应变为</span>${fx.map(f=>`<em class="${esc(f.tone||'')}">${f.label?`<i>${esc(f.label)}</i>`:''}${esc(f.text)}</em>`).join('')}</div>`:'');
 }
 return `<li class="g-step ${stage}${flag?' flagged':''}" data-step="${s.index}"><div class="g-row" tabindex="0" role="button" aria-current="${stage==='current'?'step':'false'}"><span class="g-num">${s.number}</span><div class="g-body">${body}${flag?`<div class="g-flag-note"><b>不一致记录</b>${esc(flag.note)}</div>`:''}</div><button class="g-evidence" data-audit="${s.index}" title="查看这一步的全部记录选项与原始观测">证据</button></div>${stage==='current'&&G.flagOpen?flagFormHTML(flag):''}</li>`;
}
function groupHeadHTML(g){
 const d=G.cache.get(g.steps[0].index),b=d?.before;
 if(!b)return `<div class="g-group-head"><strong>${esc(g.label)}</strong></div>`;
 const combat=g.turn!=null;
 return `<div class="g-group-head"><strong>${esc(g.label)}</strong><span>${combat?'回合开始时':'进入时'}　${esc(combat?`生命 ${b.hp}/${b.max_hp} · 格挡 ${b.block??'—'} · 能量 ${b.energy??'—'} · 手牌 ${(b.hand||[]).length} 张`:`生命 ${b.hp}/${b.max_hp} · 金币 ${b.gold??'—'}`)}</span></div>`;
}
function drawFloor(){
 const f=G.floor;if(!f)return;
 const index=G.floors.indexOf(f),done=G.saved.finished&&G.index===G.meta.total_steps-1;
 byId('guide-floor').innerHTML=`<header class="g-floor-head"><div><small>${esc(actLabel(f.act))} · 第 ${esc(f.floor)} 层</small><h3>${esc(roomLabel(f.room))}</h3></div><div class="g-floor-nav"><button class="ghost-button" data-floor="${index-1}" ${index<=0?'disabled':''}>← 上一层</button><button class="ghost-button" data-floor="${index+1}" ${index>=G.floors.length-1?'disabled':''}>下一层 →</button></div></header>`
  +(done?'<p class="g-finished">已走完全部步骤。记录的终局是原生胜利结束；若游戏里也已通关，这条路线就在正常游戏中复现了。</p>':'')
  +groups(floorSteps(f)).map(g=>`<section class="g-group${g.steps.at(-1).index<G.index?' past':''}">${groupHeadHTML(g)}<ol class="g-steps">${g.steps.map(rowHTML).join('')}</ol></section>`).join('');
}
function drawOutline(){
 let html='',act;
 G.floors.forEach((f,i)=>{
  if(f.act!==act){if(act!==undefined)html+='</ol>';act=f.act;html+=`<h4>${esc(actLabel(act))}</h4><ol>`}
  html+=`<li><button class="g-outline-row" data-floor="${i}"><span class="g-outline-floor">${esc(f.floor)}</span><span class="g-outline-room room-${esc(f.room??'none')}">${esc(roomLabel(f.room))}</span><span class="g-outline-count">${f.count} 步</span></button></li>`;
 });
 byId('guide-outline-list').innerHTML=html+(G.floors.length?'</ol>':'');
}
function markOutline(){
 const flagged=new Set(Object.keys(G.saved.flags).map(i=>floorOf(G.floors,Number(i))));
 byId('guide-outline-list').querySelectorAll('[data-floor]').forEach(b=>{
  const f=G.floors[Number(b.dataset.floor)];
  b.classList.toggle('done',f.last<G.index);b.classList.toggle('current',f===G.floor);b.classList.toggle('flagged',flagged.has(f));
  if(f===G.floor){
   b.setAttribute('aria-current','true');
   // Scrolls the outline box only; the page itself stays where the reader left it.
   const box=b.closest('.g-outline'),outer=box.getBoundingClientRect(),row=b.getBoundingClientRect();
   if(row.top<outer.top)box.scrollTop-=outer.top-row.top+8;else if(row.bottom>outer.bottom)box.scrollTop+=row.bottom-outer.bottom+8;
  }else b.removeAttribute('aria-current');
 });
 const n=flagCount();
 byId('guide-flags').classList.toggle('hidden',!n);
 byId('guide-flags-count').textContent=`已记录 ${n} 处不一致`;
}
function drawBar(){
 const total=G.meta.total_steps,last=G.index===total-1,s=G.meta.steps[G.index];
 byId('guide-progress-label').textContent=`第 ${G.index+1} / ${total} 步`;
 byId('guide-progress').style.width=(total>1?G.index/(total-1)*100:100)+'%';
 byId('guide-prev').disabled=G.index===0;
 byId('guide-next').textContent=last?(G.saved.finished?'已标记走完':'标记路线走完'):'完成，下一步';
 byId('guide-next').disabled=last&&G.saved.finished;
 byId('guide-next-group').disabled=last;
 byId('guide-next-group').textContent=s?.turn!=null?'完成本回合':'完成本阶段';
 byId('guide-flag').textContent=G.saved.flags[G.index]?'修改不一致记录':'与游戏不一致…';
}
function drawSetup(){
 const c=G.meta.context||{},mods=(c.gameplay_mods||[]).length?c.gameplay_mods.map(zh.name).join('、'):'无';
 const cells=[['角色',characters[c.character]||zh.name(c.character??'—')],['进阶',c.ascension??'—'],['模式',c.game_mode==='Standard'?'标准模式':c.game_mode??'—'],['解锁',c.unlocks==='all'?'全部解锁':c.unlocks??'—'],['自定义模组',mods]];
 byId('guide-setup').innerHTML=`<summary><b>开局设置</b><span>种子 ${esc(c.seed)} · ${esc(cells[0][1])} · 进阶 ${esc(c.ascension??'—')}</span></summary><div class="g-setup-body"><div class="g-setup-grid"><div class="g-setup-seed"><small>种子</small><strong class="mono">${esc(c.seed)}</strong><button class="outline-button" data-copy="${esc(c.seed)}">复制</button></div>${cells.map(([k,v])=>`<div><small>${k}</small><strong>${esc(v)}</strong></div>`).join('')}</div><p>用上面的设置开一局新游戏，然后按步骤操作。卡池取决于解锁进度，所以存档需要已全部解锁。</p><p>这条路线由独立进程在原生 DLL 离线 TestMode 中从开局完整重放通过，但还没有认证它与正常游戏画面完全等价。照打时如果手牌、敌人或选项和这里不同，请在第一处不同的步骤上点「与游戏不一致」记下来——随机数一旦分叉，之后的步骤通常不再适用。</p><p>「第几张」「第几格」按记录中的原始顺序从 1 数起；对不上时先核对名称，再看「此刻游戏里应是」里的整手牌。较早的记录里，少数从抽牌堆或弃牌堆里选牌的步骤（例如头槌）只有序号、没有牌名，页面会在那一步写明；新记录带候选牌，会直接写出牌名。</p><button class="primary-button" data-setup-done>${G.saved.setup?'收起':'已按此开局，开始跟打'}</button></div>`;
 byId('guide-setup').open=!G.saved.setup;
}
// A first visit keeps the setup checklist on screen instead of jumping past it.
function reveal(){
 if(G.fresh&&G.index===0&&byId('guide-setup').open)return;
 byId('guide-floor').querySelector('.g-step.current')?.scrollIntoView({block:'nearest'});
}
function show(index){
 if(!G.meta)return;
 G.index=index;G.flagOpen=false;
 if(G.saved.cursor!==index){G.saved.cursor=index;persist()}
 const f=floorOf(G.floors,index),changed=f!==G.floor;G.floor=f;
 drawBar();markOutline();drawFloor();reveal();
 const ticket=++G.epoch;
 loadFloor(f).then(()=>{
  if(ticket!==G.epoch)return;
  drawFloor();if(changed)reveal();G.fresh=false;
  const next=G.floors[G.floors.indexOf(f)+1];if(next)loadFloor(next).catch(()=>{});
 }).catch(error=>{if(ticket===G.epoch)byId('guide-floor').insertAdjacentHTML('afterbegin',`<p class="route-error">${esc(error.message)}</p>`)});
}
function open(meta,run,api){
 if(G.run!==run){G.cache.clear();G.inflight.clear()}
 G.run=run;G.meta=meta;G.api=api;G.floors=outline(meta.steps);G.floor=null;G.saved=loadSaved(run);G.fresh=true;
 drawSetup();drawOutline();
}
// Moves to the first step of the next or previous turn/stage.
function group(direction){
 if(!G.floor)return;
 const list=groups(floorSteps(G.floor)),at=list.findIndex(g=>g.steps.some(s=>s.index===G.index)),first=list[at].steps[0].index;
 if(direction>0){const next=list[at+1];G.api.select(next?next.steps[0].index:Math.min(G.meta.total_steps-1,G.floor.last+1))}
 else if(G.index!==first)G.api.select(first);
 else{const previous=list[at-1];G.api.select(previous?previous.steps[0].index:Math.max(0,G.floor.first-1))}
}
function advance(){
 if(G.index<G.meta.total_steps-1){G.api.select(G.index+1);return}
 G.saved.finished=true;persist();drawBar();drawFloor();
}
async function copyFlags(){
 const indices=Object.keys(G.saved.flags).map(Number).sort((a,b)=>a-b),items=[];
 for(const i of indices){
  const d=await detail(i),s=G.meta.steps[i];
  items.push({number:s.number,place:`${actLabel(s.act)} · 第 ${s.floor} 层${s.turn==null?'':` · 回合 ${s.turn}`}`,instruction:instructionText(instruction(d,G.meta.steps[i+1])),expected:stateText(d.before),note:G.saved.flags[i].note});
 }
 copyText(flagReport({run:G.run,...G.meta.context},items),'核对记录已复制');
}
byId('guide-floor').onclick=e=>{
 const audit=e.target.closest('[data-audit]');if(audit){G.api.audit(Number(audit.dataset.audit));return}
 const floor=e.target.closest('[data-floor]');if(floor){const f=G.floors[Number(floor.dataset.floor)];if(f)G.api.select(f.first);return}
 if(e.target.closest('[data-flag-cancel]')){G.flagOpen=false;drawFloor();return}
 if(e.target.closest('[data-flag-delete]')){delete G.saved.flags[G.index];persist();G.flagOpen=false;drawBar();markOutline();drawFloor();return}
 if(e.target.closest('[data-flag-form]'))return;
 const row=e.target.closest('.g-step');if(row){const i=Number(row.dataset.step);if(i!==G.index)G.api.select(i)}
};
byId('guide-floor').addEventListener('submit',e=>{
 e.preventDefault();const note=byId('g-flag-text').value.trim();
 if(!note){toast('请写下和游戏不一样的地方');return}
 G.saved.flags[G.index]={note,at:Date.now()};persist();G.flagOpen=false;drawBar();markOutline();drawFloor();toast('已记录，只保存在本浏览器');
});
byId('guide-outline-list').onclick=e=>{const b=e.target.closest('[data-floor]');if(b)G.api.select(G.floors[Number(b.dataset.floor)].first)};
byId('guide-setup').onclick=e=>{if(!e.target.closest('[data-setup-done]'))return;G.saved.setup=true;persist();drawSetup();reveal()};
byId('guide-prev').onclick=()=>G.api.select(G.index-1);
byId('guide-next').onclick=advance;
byId('guide-next-group').onclick=()=>group(1);
byId('guide-flag').onclick=()=>{G.flagOpen=!G.flagOpen;drawFloor();if(G.flagOpen){byId('g-flag-text')?.focus();byId('g-flag-text')?.scrollIntoView({block:'nearest'})}};
byId('guide-flags-copy').onclick=()=>copyFlags().catch(error=>toast(error.message));
root.routeGuide={open,show,group,advance,saved:run=>loadSaved(run)};
})(typeof globalThis!=='undefined'?globalThis:this);
