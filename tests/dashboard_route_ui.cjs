const assert=require('node:assert/strict');
const {esc,stateHTML,changesHTML,filteredSteps,verifiedRuns}=require('../dashboard/web/route.js');
const zh=require('../dashboard/web/route_zh.js');
assert.equal(esc('<img onerror="x">'),'&lt;img onerror=&quot;x&quot;&gt;');
const steps=Array.from({length:856},(_,i)=>({index:i,number:i+1,act:Math.floor(i/300),floor:i,kind:i%2?'play':'event',title:`CARD_${i}`}));
assert.equal(filteredSteps(steps).length,856);
assert.equal(filteredSteps(steps,{search:'CARD_855'})[0].number,856);
assert.equal(filteredSteps(steps,{kind:'play'}).length,428);
assert.equal(filteredSteps(steps,{floor:'2:855'}).length,1);
const html=stateHTML({act:0,floor:0,hp:'0',max_hp:'80',energy:0,gold:0,block:'0',hand:[{id:'<script>',upgrade:1}],deck:[],potions:[null],enemies:[]},'动作前');
assert.ok(html.includes('0 / 80'));assert.ok(html.includes('&lt;script&gt;'));assert.ok(!html.includes('<script>'));
const changes=changesHTML({hp:'80',deck:[{id:'A',upgrade:0}]},{hp:'70',deck:[{id:'A',upgrade:1}]});
assert.ok(changes.includes('80 → 70'));assert.ok(changes.includes('-1 × A'));assert.ok(changes.includes('+1 × A +1'));
const nativeTitle='打出 BASH +1 · 手牌索引 3 → TWIG_SLIME_S（战斗 ID 2）';
assert.ok(zh.title(nativeTitle).includes('痛击（BASH） +1'));
assert.ok(zh.title(nativeTitle).includes('树枝史莱姆'));
assert.equal(zh.entity('DAZED'),'晕眩（DAZED）');
assert.equal(zh.entity('PotionShapedRock'),'药水形状的石头（PotionShapedRock）');
assert.equal(zh.entity('NEW_UNKNOWN_CARD'),'NEW_UNKNOWN_CARD');
assert.ok(zh.title('事件选项：THIS_OR_THAT.pages.INITIAL.options.ORNATE').includes('这个还是那个？ · 那个'));
assert.ok(zh.title('购买 card:BASH · 50 金币').includes('卡牌：痛击（BASH）'));
assert.ok(zh.label('go:Elite@a2').includes('精英 · 第 3 幕'));
assert.ok(zh.title('THE_ARCHITECT.dialogue.0').includes('建筑师 · 对话 1'));
assert.equal(zh.entity('WITHER'),'凋萎（WITHER）');
assert.equal(zh.entity('INFECTION'),'感染（INFECTION）');
const localizedSteps=[{index:0,number:1,kind:'play',title:nativeTitle,room:'Monster'}];
const preserved=JSON.stringify(localizedSteps);
assert.equal(filteredSteps(localizedSteps,{search:'痛击'}).length,1);
assert.equal(filteredSteps(localizedSteps,{search:'BASH'}).length,1);
assert.equal(filteredSteps(localizedSteps,{search:'普通战斗'}).length,1);
assert.equal(JSON.stringify(localizedSteps),preserved);
const stateBefore={hand:[{id:'BASH',upgrade:1}],potions:['FIRE_POTION',null],relics:['BURNING_BLOOD'],enemies:[{id:'TWIG_SLIME_S',combat_id:2}]};
const serialized=JSON.stringify(stateBefore),localizedHTML=stateHTML(stateBefore,'动作前');
assert.ok(localizedHTML.includes('痛击（BASH） +1'));
assert.ok(localizedHTML.includes('火焰药水（FIRE_POTION）'));
assert.ok(localizedHTML.includes('燃烧之血（BURNING_BLOOD）'));
assert.equal(JSON.stringify(stateBefore),serialized);
assert.deepEqual(verifiedRuns([
 {id:'win',status:'verified',wins:1,seed:'42'},
 {id:'candidate',status:'running',wins:1,seed:'43'},
 {id:'death',status:'completed',wins:0,seed:'44'},
 {id:'batch',status:'verified',wins:5,seed:null},
 {id:'component',status:'verified',wins:1,seed:'45',kind:'component'}
]).map(r=>r.id),['win']);
assert.equal(verifiedRuns([{id:'bad-certificate',status:'verified',wins:1,seed:'42'}],new Set(['bad-certificate'])).length,0);
console.log('Route UI formatter checks passed; no browser visual validation.');
