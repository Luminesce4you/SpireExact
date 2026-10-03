(()=>{
'use strict';
const zh=typeof module!=='undefined'&&module.exports?require('./route_zh.js'):window.routeZh;
const esc=s=>String(s??'').replace(/[&<>"']/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
const value=v=>v===null||v===undefined?'—':esc(v);
const card=c=>typeof c==='object'&&c!==null?zh.entity(c.id??'?')+(Number(c.upgrade)>0?' +'+c.upgrade:''):zh.entity(c??'—');
const kinds={event:'事件',map:'路线',play:'出牌',end_turn:'结束回合',reward:'领取奖励',card_reward:'选卡奖励',card_skip:'跳过选卡',card_alternative:'替代奖励',rewards_skip:'离开奖励',buy:'商店购买',rest:'营地',select_cards:'选牌',use_potion:'使用药水',discard_potion:'丢弃药水',next_act:'进入下一幕',treasure:'宝箱奖励',treasure_skip:'跳过宝箱',open_chest:'打开宝箱'};
const phases={combat:'战斗',event:'事件',map:'地图',shop:'商店',rest:'营地',select_cards:'选牌',rewards:'奖励',card_reward:'选卡奖励',treasure:'宝箱'};
const place=s=>`${s.act==null?'未知幕':'第 '+(Number(s.act)+1)+' 幕'} · ${s.floor==null?'楼层未记录':'累计第 '+s.floor+' 层'}${s.turn==null?'':' · 回合 '+s.turn}`;
const raw=v=>`<pre>${esc(JSON.stringify(v,null,2))}</pre>`;
function pills(items,render=x=>x){return items?.length?'<ol class="route-pills">'+items.map((x,i)=>`<li><small>${i}</small>${esc(render(x))}</li>`).join('')+'</ol>':'<span class="route-muted">未记录 / 空</span>'}
function stateHTML(o,title){
 if(!o)return `<article class="route-state"><h4>${esc(title)}</h4><p>此边界没有保存状态。</p></article>`;
 const enemies=(o.enemies||[]).map(e=>`<li><strong>${esc(zh.entity(e.id))} <small>#${value(e.combat_id)}</small></strong><span>HP ${value(e.hp)} · 格挡 ${value(e.block)}</span></li>`).join('');
 return `<article class="route-state"><h4>${esc(title)}</h4><p class="route-muted">${esc(place(o))} · ${esc(o.room==null?'未记录房间':zh.entity(o.room))}</p><dl class="route-metrics">${[['生命',`${o.hp??'—'} / ${o.max_hp??'—'}`],['能量',o.energy],['格挡',o.block],['金币',o.gold]].map(([k,v])=>`<div><dt>${k}</dt><dd>${value(v)}</dd></div>`).join('')}</dl><h5>敌人</h5>${enemies?'<ul class="route-enemies">'+enemies+'</ul>':'<p class="route-muted">此边界未记录敌人。</p>'}<h5>手牌 · 原始索引</h5>${pills(o.hand,card)}<h5>药水 · 槽位索引</h5>${pills(o.potions,x=>x==null?'空槽':zh.entity(x))}<details><summary>完整牌组 · ${(o.deck||[]).length} 张</summary>${pills(o.deck,card)}</details><details><summary>遗物 · ${(o.relics||[]).length} 件</summary>${pills(o.relics,zh.entity)}</details>${o.selection?`<details open><summary>选牌上下文 · ${esc(zh.name(o.selection.purpose))}</summary>${o.selection.source?`<p>来源：${esc(zh.entity(o.selection.source))}</p>`:''}${raw(o.selection)}</details>`:''}</article>`;
}
function changesHTML(before,after){
 if(!before||!after)return '<p class="route-muted">缺少相邻边界，无法比较。</p>';
 const items=[];
 for(const [k,label]of [['hp','生命'],['max_hp','生命上限'],['energy','能量'],['block','格挡'],['gold','金币']])if(before[k]!==after[k])items.push(`${label} ${before[k]??'—'} → ${after[k]??'—'}`);
 const counts=xs=>{const m=new Map();for(const x of xs||[]){const k=card(x);m.set(k,(m.get(k)||0)+1)}return m};
 const a=counts(before.deck),b=counts(after.deck);
 for(const name of new Set([...a.keys(),...b.keys()])){const delta=(b.get(name)||0)-(a.get(name)||0);if(delta)items.push(`牌组 ${delta>0?'+':''}${delta} × ${name}`)}
 return items.length?'<ul class="route-changes">'+items.map(x=>'<li>'+esc(x)+'</li>').join('')+'</ul>':'<p class="route-muted">生命、资源及牌组计数无变化；仍可查看完整原始观测。</p>';
}
function filteredSteps(steps,{floor='',kind='',search=''}={}){const q=search.trim().toLowerCase();return steps.filter(s=>(!floor||`${s.act}:${s.floor}`===floor)&&(!kind||s.kind===kind)&&(!q||`${s.number} ${s.title} ${zh.title(s.title)} ${s.room??''} ${zh.name(s.room??'')}`.toLowerCase().includes(q)))}
function verifiedRuns(runs,rejected=new Set()){return (runs||[]).filter(r=>r.status==='verified'&&r.wins>0&&r.seed!=null&&r.kind!=='component'&&!rejected.has(r.id))}
if(typeof module!=='undefined'&&module.exports){module.exports={esc,stateHTML,changesHTML,filteredSteps,place,verifiedRuns};return}
const byId=id=>document.getElementById(id);
const route={key:null,meta:null,run:null,index:0,page:0,pageSize:40,epoch:0,detailEpoch:0,abort:null,rejected:new Set(),pending:null,mode:'guide'};
try{if(localStorage.getItem('spireboard.route.mode')==='audit')route.mode='audit'}catch{}
const eligibleRuns=()=>verifiedRuns(state.data?.runs,route.rejected);
async function getJSON(url,signal){const response=await fetch(url,{signal});if(!response.ok){let message=`读取失败（HTTP ${response.status}）`;try{message=(await response.json()).error||message}catch{}throw new Error(message)}return response.json()}
function setStatus(message,type=''){const el=byId('route-status');el.textContent=message;el.className='route-status '+type;el.classList.toggle('hidden',!message)}
function filters(){return {floor:byId('route-floor').value,kind:byId('route-kind').value,search:byId('route-search').value}}
function clearFilters(){byId('route-floor').value='';byId('route-kind').value='';byId('route-search').value='';route.page=0}
function urlStep(){if(state.tab!=='route')return;const url=new URL(location.href);url.searchParams.set('run',route.run);url.searchParams.set('view','route');url.searchParams.set('step',String(route.index+1));history.replaceState(null,'',url)}
const modeHints={guide:'照着路线在游戏里一步步操作，并核对手牌、敌人和数值。',audit:'逐步查看当时记录的全部选项、前后状态与原始观测。'};
function drawMode(){
 for(const mode of ['guide','audit']){const on=route.mode===mode;byId('route-'+mode).classList.toggle('hidden',!on);byId('route-mode-'+mode).classList.toggle('selected',on);byId('route-mode-'+mode).setAttribute('aria-pressed',String(on))}
 byId('route-mode-hint').textContent=modeHints[route.mode];
}
function setMode(mode){
 route.mode=mode==='audit'?'audit':'guide';try{localStorage.setItem('spireboard.route.mode',route.mode)}catch{}
 drawMode();if(route.meta?.available)selectStep(route.index,true);
}
function setFocus(on){document.body.classList.toggle('focus-mode',on);byId('route-focus').textContent=on?'退出专注':'专注模式';byId('route-focus').setAttribute('aria-pressed',String(on));document.querySelector('.g-step.current')?.scrollIntoView({block:'nearest'})}
function drawList(){
 if(!route.meta)return;
 const rows=filteredSteps(route.meta.steps,filters()),pages=Math.max(1,Math.ceil(rows.length/route.pageSize));route.page=Math.min(route.page,pages-1);
 const start=route.page*route.pageSize,shown=rows.slice(start,start+route.pageSize);
 byId('route-list-count').textContent=rows.length===route.meta.total_steps?`全部 ${rows.length} 步`:`筛选出 ${rows.length} / ${route.meta.total_steps} 步`;
 byId('route-page-label').textContent=`${route.page+1} / ${pages}`;
 byId('route-prev-page').disabled=route.page===0;byId('route-next-page').disabled=route.page>=pages-1;
 byId('route-list').innerHTML=shown.map(s=>`<li><button class="route-row ${s.index===route.index?'selected':''}" data-step="${s.index}" aria-current="${s.index===route.index?'step':'false'}"><span class="route-number">${s.number}</span><span><small>${esc(place(s))} · ${esc(kinds[s.kind]||s.kind)}</small><strong>${esc(zh.title(s.title))}</strong><em>HP ${value(s.hp)} / ${value(s.max_hp)} · ${s.menu_count??'—'} 个记录选项 ${s.replay_match?'· 重放一致':'· 重放待核对'}</em></span></button></li>`).join('')||'<li class="route-empty">没有匹配的步骤。清除筛选即可查看全部轨迹。</li>';
 byId('route-list').querySelectorAll('[data-step]').forEach(b=>b.onclick=()=>selectStep(Number(b.dataset.step)));
}
function drawProof(){
 const d=route.meta,labels={candidate_replay_and_certificate_match:'候选、重放与证书一致',manifest_seed_matches:'种子一致',fresh_native_process:'不同原生进程',no_advisor_or_checkpoint:'无 advisor / 检查点',explicit_replay:'显式完整重放',full_requested_history:'完整动作请求',zero_solver_calls:'重放未调用求解器',zero_checkpoint_skips:'从初始状态重放'};
 byId('route-proof').innerHTML=`<div><strong>${d.total_steps} 个实际决策</strong><span>种子 ${esc(d.seed)} · ${esc(d.label)} · ${d.source==='independent_replay'?'独立重放记录':'候选记录'}</span></div><details><summary>证据检查与来源</summary><ul class="route-checks">${Object.entries(d.checks||{}).map(([k,v])=>`<li class="${v?'pass':'fail'}">${v?'✓':'!'} ${esc(labels[k]||k)}</li>`).join('')}</ul>${(d.issues||[]).map(x=>'<p class="route-error">'+esc(x)+'</p>').join('')}<p>${esc(d.scope)}</p><p class="route-muted">候选 PID ${value(d.candidate_pid)} · 重放 PID ${value(d.replay_pid)}</p>${raw({certificate:d.certificate,sources:d.sources})}</details>`;
}
async function selectStep(index,reveal=false){
 if(!route.meta?.available)return;index=Math.max(0,Math.min(route.meta.total_steps-1,index));route.index=index;urlStep();
 route.abort?.abort();const ticket=++route.detailEpoch,run=route.run;
 if(route.mode==='guide'){window.routeGuide.show(index);return}
 if(reveal){clearFilters();route.page=Math.floor(index/route.pageSize)}
 drawList();byId('route-list').querySelector('.route-row.selected')?.scrollIntoView({block:'nearest',inline:'nearest'});byId('route-jump').value=String(index+1);
 byId('route-step-position').textContent=`第 ${index+1} / ${route.meta.total_steps} 步`;
 byId('route-prev-step').disabled=index===0;byId('route-next-step').disabled=index===route.meta.total_steps-1;
 route.abort=new AbortController();
 byId('route-detail-body').innerHTML='<p class="route-empty">正在读取这一步的完整证据…</p>';
 try{
  const data=await getJSON(`/api/winning-route?run=${encodeURIComponent(run)}&step=${index}`,route.abort.signal);
  if(ticket!==route.detailEpoch||run!==route.run)return;
  const s=data.summary;
  byId('route-detail-body').innerHTML=`<div class="route-action-heading"><span>${esc(phases[s.phase]||s.phase)} · ${esc(place(s))}</span><h3>${esc(zh.title(s.title))}</h3><div class="route-badges"><span class="${s.menu_match?'pass':'fail'}">${s.menu_match?'动作在记录菜单中':'未匹配到记录菜单'}</span><span class="${s.replay_match?'pass':'fail'}">${s.replay_match?'候选与独立重放逐步一致':'候选 / 重放需核对'}</span></div></div><details class="route-action-json"><summary>本步实际动作 · 原始 JSON</summary>${raw(data.action)}</details><div class="route-state-grid">${stateHTML(data.before,'动作前状态')}${stateHTML(data.after,data.after_is_terminal?'原生终局状态':'下一决策前状态（含自动结算）')}</div><section class="route-change-section"><h4>相邻边界的资源与牌组变化</h4>${changesHTML(data.before,data.after)}</section><section class="route-options"><h4>当时记录的全部可选项 · ${data.options.length}</h4><p class="route-muted">标记实际选择；此菜单记录不等于穷举合法动作的证明。</p><ol>${data.options.map(o=>`<li class="${o.chosen?'chosen':''}"><div><b>${o.chosen?'✓ 实际选择':'可选项 '+(o.index+1)}</b><span>${esc(zh.title(o.title))}</span></div><code>${esc(JSON.stringify(o.action))}</code>${o.labels.length?'<small>'+esc(o.labels.map(zh.label).join(' / '))+'</small>':''}</li>`).join('')}</ol></section><details class="route-raw"><summary>完整原始决策证据（含 RNG）</summary><p class="route-muted">超出 JavaScript 精确范围的整数显示为十进制字符串；原始导出保留原数值类型。</p>${raw(data.raw_evidence)}</details><details class="route-raw"><summary>${data.after_is_terminal?'完整原生终局观测':'完整下一边界观测'}</summary>${raw(data.after)}</details>`;
 }catch(error){if(error.name==='AbortError')return;if(ticket===route.detailEpoch)byId('route-detail-body').innerHTML=`<p class="route-error">${esc(error.message)}</p>`}
}
async function sync(d){
 if(state.tab!=='route'||!d)return;
 const eligible=eligibleRuns();
 if(!eligible.some(r=>r.id===d.id)){
  route.key=null;route.meta=null;route.epoch++;route.detailEpoch++;route.abort?.abort();
  byId('route-content').classList.add('hidden');byId('route-download').disabled=true;renderRunSelect();
  if(eligible.length){setStatus('正在切换到一个验证通过的胜利运行…');if(route.pending!==eligible[0].id){route.pending=eligible[0].id;connect(route.pending)}}
  else{route.pending=null;setStatus('还没有通过证据检查的单种子胜利，所以暂时没有可看的轨迹。')}
  return;
 }
 route.pending=null;
 const key=`${d.id}:${d.status}:${d.wins}`;
 if(route.key===key){if(route.meta?.available)urlStep();return}
 route.key=key;route.run=d.id;route.meta=null;route.abort?.abort();route.detailEpoch++;
 const ticket=++route.epoch;byId('route-content').classList.add('hidden');byId('route-download').disabled=true;
 setStatus('正在读取并核对完整胜利证据…');
 try{
  const meta=await getJSON('/api/winning-route?run='+encodeURIComponent(d.id));if(ticket!==route.epoch)return;
  route.meta=meta;
  if(!meta.available||!meta.verified){route.rejected.add(d.id);route.key=null;await sync(d);return}
  setStatus('');
  byId('route-content').classList.remove('hidden');byId('route-download').disabled=false;drawProof();drawMode();
  const floors=new Map();for(const s of meta.steps)floors.set(`${s.act}:${s.floor}`,place({...s,turn:null}));
  byId('route-floor').innerHTML='<option value="">全部层</option>'+[...floors].map(([key,label])=>`<option value="${esc(key)}">${esc(label)}</option>`).join('');
  byId('route-kind').innerHTML='<option value="">全部类型</option>'+Object.keys(meta.kind_counts).map(k=>`<option value="${esc(k)}">${esc(kinds[k]||k)} (${meta.kind_counts[k]})</option>`).join('');
  byId('route-jump').max=String(meta.total_steps);clearFilters();
  window.routeGuide.open(meta,d.id,{select:i=>selectStep(i),audit:i=>{route.index=i;setMode('audit')}});
  // An explicit step link wins; otherwise resume where this browser left off.
  const params=new URLSearchParams(location.search),requested=params.get('run')===d.id?Number(params.get('step')):0,saved=window.routeGuide.saved(d.id).cursor;
  await selectStep(Number.isInteger(requested)&&requested>0?requested-1:saved??0,true);
 }catch(error){if(ticket===route.epoch){route.key=null;setStatus(error.message,'warning')}}
}
window.routeView={sync,activate:()=>sync(state.data?.selected),runs:eligibleRuns};
byId('route-mode-guide').onclick=()=>setMode('guide');byId('route-mode-audit').onclick=()=>setMode('audit');
byId('route-focus').onclick=()=>setFocus(!document.body.classList.contains('focus-mode'));
byId('route-floor').onchange=byId('route-kind').onchange=byId('route-search').oninput=()=>{route.page=0;drawList()};
byId('route-prev-page').onclick=()=>{route.page--;drawList();byId('route-list').scrollTop=0};byId('route-next-page').onclick=()=>{route.page++;drawList();byId('route-list').scrollTop=0};
byId('route-prev-step').onclick=()=>selectStep(route.index-1,true);byId('route-next-step').onclick=()=>selectStep(route.index+1,true);
function jump(){const n=Number(byId('route-jump').value);if(!Number.isInteger(n)||n<1||n>route.meta?.total_steps){toast('请输入有效步骤编号');return}selectStep(n-1,true)}
byId('route-jump-button').onclick=jump;byId('route-jump').onkeydown=e=>{if(e.key==='Enter')jump()};
byId('route-download').onclick=()=>{if(!route.meta?.available)return;const a=document.createElement('a');a.href='/api/winning-route?run='+encodeURIComponent(route.run)+'&export=1';a.download='SpireBoard-winning-route.json';a.click()};
document.addEventListener('keydown',e=>{
 if(e.key==='Escape'&&document.body.classList.contains('focus-mode')){setFocus(false);return}
 if(state.tab!=='route'||!route.meta?.available||e.ctrlKey||e.metaKey||e.altKey||['INPUT','SELECT','TEXTAREA'].includes(e.target.tagName))return;
 const arrow=e.key==='ArrowLeft'?-1:e.key==='ArrowRight'?1:0;
 if(route.mode==='guide'){
  if(arrow&&e.shiftKey){e.preventDefault();window.routeGuide.group(arrow);return}
  if((e.key===' '||e.key==='Enter')&&!['BUTTON','SUMMARY','A'].includes(e.target.tagName)){e.preventDefault();window.routeGuide.advance();return}
 }
 if(arrow){e.preventDefault();selectStep(route.index+arrow,true)}
});
drawMode();
if(state.tab==='route')window.routeView.activate();
})();
