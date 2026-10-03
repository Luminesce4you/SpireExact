const assert=require('node:assert/strict');
const {totals,coefficients,gateHTML,bestOutcome}=require('../dashboard/web/gate_model.js');
const gate={act:2,ordinal:1,encounter:'AEONGLASS',entries:30,fitted_entries:24,pairs:19,fitted_pairs:0,
 survived_entries:3,best_outcome:1.6,residual_spread:0.125,
 top:[['card:BASH',1.5],['up:SHRUG_IT_OFF',2.2],['potion:FIRE_POTION',-0.2]],
 bottom:[['card:BASH',1.5],['card:ANGER',-3],['card:<script>',-1]]};
const original=JSON.stringify(gate);
assert.equal(coefficients(gate).length,5);
assert.equal(coefficients(gate,{type:'up'})[0].name,'耸肩无视');
assert.equal(coefficients(gate,{search:'痛击'}).length,1);
assert.equal(coefficients(gate,{search:'BASH'}).length,1);
assert.deepEqual(totals({version:7,gates:[gate,{entries:10,fitted_entries:0,survived_entries:0}]}),
 {gates:2,entries:40,fitted:24,survived:3,version:7});
assert.equal(totals(null).entries,0);
const html=gateHTML(gate);
assert.ok(html.includes('第二首领'));
assert.ok(html.includes('永世沙漏'));
assert.ok(html.includes('6 个样本待更新'));
assert.ok(html.includes('观察比例 10.0% · 非预测概率'));
assert.ok(html.includes('&lt;script&gt;'));
assert.ok(!html.includes('<script>'));
assert.equal(bestOutcome({best_outcome:null}),'尚无结果');
assert.equal(bestOutcome({best_outcome:.99}),'未达到过关标签');
assert.equal(bestOutcome({best_outcome:2.1}),'已达到过关标签');
assert.ok(gateHTML({...gate,entries:0,fitted_entries:0,top:[],bottom:[],survived_entries:0}).includes('尚未拟合'));
assert.equal(JSON.stringify(gate),original);
console.log('Gate model snapshots, coefficient search/deduplication, empty states and escaping passed.');
