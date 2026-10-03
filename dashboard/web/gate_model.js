(function(){
'use strict';
const zh=typeof module!=='undefined'&&module.exports?require('./route_zh.js'):window.routeZh;
const esc=s=>String(s??'').replace(/[&<>"']/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
const count=v=>Number.isFinite(v)?v:0;
const number=v=>v==null||!Number.isFinite(Number(v))?'—':Number(v).toLocaleString('zh-CN');
const decimal=(v,d=2)=>v==null||!Number.isFinite(Number(v))?'—':Number(v).toFixed(d);
const gateKey=g=>`${g.act}:${g.ordinal}`;
const gateTitle=g=>`第 ${Number(g.act)+1} 幕 · ${Number(g.ordinal)>0?'第二首领':'第一首领'}`;
function totals(model){
 const gates=model?.gates||[];
 return {gates:gates.length,entries:gates.reduce((n,g)=>n+count(g.entries),0),fitted:gates.reduce((n,g)=>n+count(g.fitted_entries),0),
  survived:gates.reduce((n,g)=>n+count(g.survived_entries),0),version:model?.version??null};
}
function coefficients(gate,{type='',search=''}={}){
 const rows=new Map();
 for(const [key,value]of [...(gate.top||[]),...(gate.bottom||[])]){
  if(!Number.isFinite(value))continue;
  const split=key.indexOf(':'),head=key.slice(0,split),id=key.slice(split+1);
  const kind={card:'卡牌',up:'升级',relic:'遗物',potion:'药水'}[head]||head;
  rows.set(key,{key,value,head,kind,id,name:zh.name(id)});
 }
 const query=search.trim().toLowerCase();
 return [...rows.values()].filter(r=>(!type||r.head===type)&&(!query||`${r.key} ${r.name}`.toLowerCase().includes(query))).sort((a,b)=>b.value-a.value||a.key.localeCompare(b.key));
}
function bestOutcome(gate){
 const v=gate.best_outcome;
 if(v==null)return '尚无结果';
 return Number(v)>=1?'已达到过关标签':'未达到过关标签';
}
function rankingHTML(gate,filters){
 const rows=coefficients(gate,filters),scale=Math.max(1,...coefficients(gate).map(r=>Math.abs(r.value)));
 if(!rows.length)return `<p class="model-empty">${gate.fitted_entries?'没有匹配的已保存特征。':'此关口尚未拟合，等待更多入场样本。'}</p>`;
 return '<ol class="model-ranking">'+rows.map(r=>`<li><div class="model-feature"><span class="model-feature-kind">${esc(r.kind)}</span><strong>${esc(r.name)}</strong><small>${esc(r.id)}</small></div><div class="model-signal"><div class="model-signal-track"><i class="${r.value<0?'negative':'positive'}" style="width:${Math.abs(r.value)/scale*50}%;${r.value<0?'right':'left'}:50%"></i></div><b class="${r.value<0?'negative':'positive'}">${r.value>0?'+':''}${decimal(r.value)}</b></div></li>`).join('')+'</ol>';
}
function gateHTML(gate,filters={}){
 const entries=count(gate.entries),fitted=count(gate.fitted_entries),survived=count(gate.survived_entries);
 const ratio=entries?`${decimal(survived/entries*100,1)}%`:'—';
 return `<div class="model-detail-heading"><div><p>${esc(gateTitle(gate))}</p><h3>${esc(zh.name(gate.encounter??'尚未记录遭遇'))}</h3><small>${esc(gate.encounter??'')}</small></div><span class="model-badge ${fitted?'trained':''}">${fitted?'已拟合':'收集样本'}</span></div>
 <dl class="model-detail-metrics"><div><dt>已用于拟合 / 入场</dt><dd>${number(fitted)} / ${number(entries)}</dd><small>${number(Math.max(0,entries-fitted))} 个样本待更新</small></div><div><dt>已拟合 / 成对样本</dt><dd>${number(gate.fitted_pairs)} / ${number(gate.pairs)}</dd><small>父子路线的入场状态差分</small></div><div><dt>记录过关 / 入场</dt><dd>${number(survived)} / ${number(entries)}</dd><small>观察比例 ${ratio} · 非预测概率</small></div><div><dt>训练残差</dt><dd>${decimal(gate.residual_spread,3)}</dd><small>训练集内的结果分数波动</small></div></dl>
 <div class="model-best"><strong>最好结果分数 ${decimal(gate.best_outcome,3)}</strong><span>${esc(bestOutcome(gate))}</span></div>
 <div class="model-ranking-heading"><h4>当前特征偏好</h4><span>正值偏好 · 负值回避</span></div>${rankingHTML(gate,filters)}
 <p class="model-caption">显示快照保留的最高、最低各至多 12 个特征，去除重复；不是完整权重表。数字是标准化排序信号，不能据此断言某张牌导致过关。药水特征用于描述入场状态，目前不直接给用药选项评分。</p>
 ${gate.probes_are?`<div class="model-probe-note">合成探针单独记录：${number(gate.probe_tables)} 张表，${number(gate.probe_labels)} 个选项；不计入真实入场样本。</div>`:''}`;
}
if(typeof module!=='undefined'&&module.exports){module.exports={totals,coefficients,gateHTML,bestOutcome,gateKey};return}
const get=id=>document.getElementById(id);
const view={run:null,gate:null,data:null,histories:new Map(),stamp:null};
function observe(d){
 if(!d)return;
 const model=d.gate_models;
 if(!model||!Array.isArray(model.gates))return;
 const t=totals(model),signature=JSON.stringify([t.version,...model.gates.map(g=>[gateKey(g),g.entries,g.fitted_entries,g.survived_entries])]);
 let record=view.histories.get(d.id);
 if(!record){record={signature:null,points:[]};view.histories.set(d.id,record);if(view.histories.size>24)view.histories.delete(view.histories.keys().next().value)}
 if(record.signature!==signature){
  record.signature=signature;
  const seconds=d.gate_model_meta?.elapsed_seconds;
  record.points.push({x:seconds==null?record.points.length:Number(seconds),y:t.entries,fitted:t.fitted,label:`模型更新 ${t.version} · 已拟合 ${t.fitted}`});
  if(record.points.length>256)record.points.shift();
 }
}
function drawDetails(){
 const gate=(view.data?.gate_models?.gates||[]).find(g=>gateKey(g)===view.gate);
 if(!gate)return;
 get('model-detail').innerHTML=gateHTML(gate,{type:get('model-feature-type').value,search:get('model-feature-search').value});
 get('model-gates').querySelectorAll('button').forEach(b=>{const active=b.dataset.gate===view.gate;b.classList.toggle('selected',active);b.setAttribute('aria-pressed',String(active))});
}
function sync(d){
 if(state.tab!=='model'||!d)return;
 view.data=d;
 const model=d.gate_models,meta=d.gate_model_meta||{};
 if(!model||!Array.isArray(model.gates)){
  get('model-content').classList.add('hidden');get('model-status').textContent=d.kind==='component'?'此运行是组件实验，没有在线关口模型。':'此运行未保存关口模型快照。可选择使用 focus / 在线先验的单种子实验。';return;
 }
 if(view.run!==d.id){view.run=d.id;view.gate=null;get('model-feature-search').value='';get('model-feature-type').value=''}
 const gates=model.gates||[],t=totals(model);
 get('model-content').classList.toggle('hidden',!gates.length);
 const time=meta.snapshot_at?new Date(meta.snapshot_at*1000).toLocaleString('zh-CN',{hour12:false}):'时间未记录';
 get('model-status').textContent=`只统计这次运行（种子 ${d.seed}）· ${gates.length?'最新模型快照':'模型已启用，等待首领入场样本'} · ${time}`;
 if(!gates.length)return;
 get('model-samples').textContent=number(t.entries);get('model-fitted').textContent=number(t.fitted);
 get('model-version').textContent=number(t.version);get('model-survived').textContent=`${number(t.survived)} / ${number(t.entries)}`;
 get('model-gates').innerHTML=gates.map(g=>`<button data-gate="${esc(gateKey(g))}" aria-pressed="false"><span>${esc(gateTitle(g))}</span><strong>${esc(zh.name(g.encounter??'未知遭遇'))}</strong><small>${number(g.entries)} 个入场 · ${number(g.fitted_entries)} 个已拟合</small><div class="model-training-track"><i style="width:${g.entries?Math.min(100,count(g.fitted_entries)/g.entries*100):0}%"></i></div></button>`).join('');
 if(!gates.some(g=>gateKey(g)===view.gate))view.gate=gateKey(gates[0]);
 get('model-gates').querySelectorAll('button').forEach(b=>b.onclick=()=>{view.gate=b.dataset.gate;drawDetails()});drawDetails();
 const history=view.histories.get(d.id)?.points||[];
 get('model-history-count').textContent=`${history.length} 个页面观察快照`;
 if(history.length>1)chart('model-training-chart',history,{step:true,xLabel:meta.elapsed_seconds==null?'观察快照':'运行时间',format:v=>Math.round(v)});
 else get('model-training-chart').innerHTML='<div class="empty">已记录当前快照，模型再次更新后显示曲线。</div>';
 get('model-raw').textContent=JSON.stringify({source:meta.source,snapshot_at:meta.snapshot_at,elapsed_seconds:meta.elapsed_seconds,...model},null,2);
}
window.gateView={observe,sync,activate:()=>sync(state.data?.selected)};
get('model-feature-search').oninput=get('model-feature-type').onchange=drawDetails;
if(state.data?.selected)observe(state.data.selected);
if(state.tab==='model')window.gateView.activate();
})();
