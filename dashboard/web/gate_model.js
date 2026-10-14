(function(){
'use strict';
const zh=typeof module!=='undefined'&&module.exports?require('./route_zh.js'):window.routeZh;
const esc=s=>String(s??'').replace(/[&<>"']/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
const count=v=>Number.isFinite(v)?v:0;
const number=v=>v==null||!Number.isFinite(Number(v))?'—':Number(v).toLocaleString('zh-CN');
const decimal=(v,d=2)=>v==null||!Number.isFinite(Number(v))?'—':Number(v).toFixed(d);
const signed=(v,d=3)=>v==null||!Number.isFinite(Number(v))?'—':(v>0?'+':'')+Number(v).toFixed(d);
const JOINT='joint';
const gateKey=g=>`${g.act}:${g.ordinal}`;
const gateTitle=g=>`第 ${Number(g.act)+1} 幕 · ${Number(g.ordinal)>0?'第二首领':'第一首领'}`;
const sameGate=(g,pair)=>Array.isArray(pair)&&Number(pair[0])===Number(g.act)&&Number(pair[1])===Number(g.ordinal);
const SOURCE={real_gate:'真实入场模型',joint_f1_real_plus_full_hp_f2:'联合模型'};
const REJECTION={missing_exact_entry_key:'缺少精确入场键',unknown_or_incomplete_f2_samples:'第二首领样本未完成',
 unknown_or_invalid_real_f1_entry:'第一首领真实结果缺失或无效',same_key_different_features:'同一入场键特征不一致',
 same_key_different_or_shorter_samples:'同一入场键样本不一致或更少'};
const REUSE_REJECTION={paired_initial_baseline_mismatch:'请求上下文不一致',paired_entry_guard_mismatch:'没有匹配的入场'};
const GROUP_STATE={DEAD_AT_FULL_HP:['满血全输','dead'],VIABLE:['有过关','viable'],PENDING:['待第二批','pending'],UNKNOWN:['未完成','']};
const PROBE_STATE={ACTIVE:'运行中',INACTIVE:'未启用',WAITING_FOR_NATIVE_METADATA:'等待原生元数据',WAITING_FOR_FINAL_F1:'等待第 3 幕第一首领入场'};
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
function signalHTML(value,scale,digits=2){
 return `<div class="model-signal"><div class="model-signal-track"><i class="${value<0?'negative':'positive'}" style="width:${Math.abs(value)/scale*50}%;${value<0?'right':'left'}:50%"></i></div><b class="${value<0?'negative':'positive'}">${signed(value,digits)}</b></div>`;
}
function rankingHTML(gate,filters){
 const rows=coefficients(gate,filters),scale=Math.max(1,...coefficients(gate).map(r=>Math.abs(r.value)));
 if(!rows.length)return `<p class="model-empty">${gate.fitted_entries?'没有匹配的已保存特征。':'此关口尚未拟合，等待更多入场样本。'}</p>`;
 return '<ol class="model-ranking">'+rows.map(r=>`<li><div class="model-feature"><span class="model-feature-kind">${esc(r.kind)}</span><strong>${esc(r.name)}</strong><small>${esc(r.id)}</small></div>${signalHTML(r.value,scale)}</li>`).join('')+'</ol>';
}
// The i082 joint table (real final-act F1 + full-HP synthetic F2), placed beside the gate it takes over.
function jointGate(model){
 const j=model?.f2_joint_model;
 if(!j||typeof j!=='object')return null;
 const gates=model.gates||[],acts=gates.map(g=>Number(g.act)).filter(Number.isFinite);
 const act=Array.isArray(j.replaces_gate)?Number(j.replaces_gate[0]):acts.length?Math.max(...acts):2;
 const find=o=>gates.find(g=>Number(g.act)===act&&Number(g.ordinal)===o)?.encounter??null;
 return {...j,act,first:find(0),second:find(1)};
}
function sourcesHTML(j){
 const rows=(j.tier_sources||[]).filter(r=>Array.isArray(r.gates)&&r.gates.length);
 if(!rows.length)return '';
 return `<div class="model-ranking-heading"><h4>档位来源</h4><span>每一幕的选项按这些关口的表排序</span></div><ul class="model-sources">${rows.map(r=>`<li><span>第 ${Number(r.act)+1} 幕的选项</span>${r.gates.map(x=>{const g={act:x.gate?.[0],ordinal:x.gate?.[1]},joint=x.source!=='real_gate';return `<em class="model-source${joint?' joint':''}">${esc(gateTitle(g))} · ${esc(SOURCE[x.source]||x.source)}</em>`}).join('')}</li>`).join('')}</ul>`;
}
function jointHTML(j,filters={},paired=null){
 const entries=count(j.entries),fitted=count(j.fitted_entries),on=Array.isArray(j.replaces_gate);
 const rejected=Object.entries(j.rejections||{}).filter(([,n])=>n>0);
 const rows=count(paired?.joint_rows);
 return `<div class="model-detail-heading"><div><p>第 ${j.act+1} 幕 · 两个首领联合</p><h3>${esc(zh.name(j.first??'第一首领'))} + ${esc(zh.name(j.second??'第二首领'))}</h3><small>真实第一首领 + 满血合成第二首领</small></div><span class="model-badge ${fitted?'trained':''}">${on?'已接管档位':fitted?'已拟合':'收集样本'}</span></div>
 <dl class="model-detail-metrics"><div><dt>已用于拟合 / 样本行</dt><dd>${number(fitted)} / ${number(entries)}</dd><small>${number(j.dirty_entries)} 行待更新 · 满 ${number(j.minimum_entries)} 行首次拟合，之后每 ${number(j.refresh_entries)} 行更新</small></div><div><dt>第二首领样本组</dt><dd>${number(j.known_sample_groups)} / ${number(j.sample_groups)}</dd><small>多行可共用一组样本，彼此相关${count(j.unknown_sample_group_rows)?` · ${number(j.unknown_sample_group_rows)} 行样本未完成`:''}</small></div><div><dt>生命分母 第一 / 第二首领</dt><dd>${number(j.f1_life_total)} / ${number(j.f2_life_total)}</dd><small>各自统一，不同入场可比</small></div><div><dt>训练残差</dt><dd>${decimal(j.residual_spread,3)}</dd><small>训练集内的结果分数波动</small></div></dl>
 <div class="model-best"><strong>结果分数 = 第一首领真实结果 + 第二首领满血样本平均分</strong><span>每项 0–2：没过关是打掉的敌方生命比例，过关是 1 + 保留生命比例；合计 0–4</span></div>
 ${on?'':`<div class="model-tier-note">还没有接管档位：满 ${number(j.minimum_entries)} 行才首次拟合，在那之前第 ${j.act+1} 幕第一首领的档位仍来自真实入场模型。</div>`}
 ${sourcesHTML(j)}
 ${rejected.length?`<div class="model-probe-note">没有进表的行：${rejected.map(([k,n])=>`${esc(REJECTION[k]||k)} ${number(n)}`).join('，')}</div>`:''}
 <div class="model-ranking-heading"><h4>当前特征偏好</h4><span>正值偏好 · 负值回避</span></div>${rankingHTML(j,filters)}
 <p class="model-caption">一行是一个有完整第二首领样本的入场${rows?`；其中 ${number(rows)} 行来自第三幕选牌对照：加一张牌后的 5 场满血第二首领样本，第一首领部分借用原入场的真实结果（这张牌对第一首领的作用按没有算）`:''}。合成样本只用于这张表和档位排序，不进入真实关口的入场行、轨迹、检查点、结果缓存或胜利证据。第二首领按满血打，和真实路线带进去的生命不同；数字是排序信号，不是过关概率。</p>`;
}
function gateHTML(gate,filters={},joint=null){
 const entries=count(gate.entries),fitted=count(gate.fitted_entries),survived=count(gate.survived_entries);
 const ratio=entries?`${decimal(survived/entries*100,1)}%`:'—';
 return `<div class="model-detail-heading"><div><p>${esc(gateTitle(gate))}</p><h3>${esc(zh.name(gate.encounter??'尚未记录遭遇'))}</h3><small>${esc(gate.encounter??'')}</small></div><span class="model-badge ${fitted?'trained':''}">${fitted?'已拟合':'收集样本'}</span></div>
 ${sameGate(gate,joint?.replaces_gate)?'<div class="model-tier-note">这一关的档位现在由「两个首领联合」表给出。下面这张真实入场表照常收样本、照常拟合，只是不再用来排选项。</div>':''}
 <dl class="model-detail-metrics"><div><dt>已用于拟合 / 入场</dt><dd>${number(fitted)} / ${number(entries)}</dd><small>${number(Math.max(0,entries-fitted))} 个样本待更新</small></div><div><dt>已拟合 / 成对样本</dt><dd>${number(gate.fitted_pairs)} / ${number(gate.pairs)}</dd><small>父子路线的入场状态差分</small></div><div><dt>记录过关 / 入场</dt><dd>${number(survived)} / ${number(entries)}</dd><small>观察比例 ${ratio} · 非预测概率</small></div><div><dt>训练残差</dt><dd>${decimal(gate.residual_spread,3)}</dd><small>训练集内的结果分数波动</small></div></dl>
 <div class="model-best"><strong>最好结果分数 ${decimal(gate.best_outcome,3)}</strong><span>${esc(bestOutcome(gate))}</span></div>
 <div class="model-ranking-heading"><h4>当前特征偏好</h4><span>正值偏好 · 负值回避</span></div>${rankingHTML(gate,filters)}
 <p class="model-caption">显示快照保留的最高、最低各至多 12 个特征，去除重复；不是完整权重表。数字是标准化排序信号，不能据此断言某张牌导致过关。药水特征用于描述入场状态，目前不直接给用药选项评分。</p>
 ${gate.probes_are?`<div class="model-probe-note">合成探针单独记录：${number(gate.probe_tables)} 张表，${number(gate.probe_labels)} 个选项；不计入真实入场样本。</div>`:''}`;
}
// Paired third-act card arms against card_skip, merged the way the snapshot says: i082 'mean'
// averages every complete table; older snapshots (no merge field, 'latest') keep the latest table.
function pairedRows(p){
 const tables=(p?.tables||[]).filter(t=>t.status==='PAIRED_SIGNAL'&&t.paired_gains),mean=p?.merge==='mean';
 const labels=new Set([...Object.keys(p?.signal_tables||{}),...Object.keys(p?.tiers||{}).map(k=>k.replace(/^paired:/,'')),...tables.flatMap(t=>Object.keys(t.paired_gains))]);
 return [...labels].map(label=>{
  const gains=tables.map(t=>t.paired_gains[label]).filter(Number.isFinite);
  const id=label.startsWith('card:')?label.slice(5):null;
  return {label,id,name:id?zh.name(id):'跳过（基准）',gain:!gains.length?null:mean?gains.reduce((a,b)=>a+b,0)/gains.length:gains[gains.length-1],
   tables:p.signal_tables?.[label]??gains.length,tier:p.tiers?.['paired:'+label]?.[2]??null};
 }).sort((a,b)=>(b.gain??-9)-(a.gain??-9)||a.label.localeCompare(b.label));
}
function pairedHTML(p){
 const rows=pairedRows(p),scale=Math.max(1e-9,...rows.map(r=>Math.abs(r.gain??0)));
 const facts=[`${number(p.tables_with_signal)} / ${number(p.tables_scheduled)} 张表有结果`,`可用 ${number(p.usable_probes)} / ${number(p.attempted_probes)} 个探针`,
  `原生 ${number(Math.round(count(p.measured_probe_native_seconds??p.native_wall_seconds)))} 秒`,p.joint_rows!=null?`进入联合表 ${number(p.joint_rows)} 行`:null,
  p.next_entry_threshold!=null?`下一张表：第 ${number(p.next_entry_threshold)} 个真实入场`:null].filter(Boolean);
 return `<div class="model-ranking-heading"><h4>第三幕选牌对照（合成）</h4><span>${esc(PROBE_STATE[p.state]||p.state||'—')}</span></div>
 <p class="model-subtle">第 3 幕第一首领门前，把同一个选牌奖励里的每张牌分别加进牌组，各打 ${number(p.samples_per_arm)} 场满血第二首领（各臂用同一组随机数），和跳过比较。</p>
 <div class="model-facts">${facts.map(f=>`<span>${esc(f)}</span>`).join('')}</div>
 ${rows.length?'<ol class="model-ranking">'+rows.map(r=>`<li><div class="model-feature"><span class="model-feature-kind">${r.id?'卡牌':'跳过'}</span><strong>${esc(r.name)}</strong><small>${number(r.tables)} 张表 · 第 3 幕档位 ${r.tier==null?'—':signed(r.tier,0)}</small></div>${r.gain==null?'<div class="model-signal"><span></span><b>—</b></div>':signalHTML(r.gain,scale,3)}</li>`).join('')+'</ol>':'<p class="model-empty">还没有完成的对照表。</p>'}
 <p class="model-caption">差值 = 加这张牌后第二首领平均分 − 跳过的平均分（0–2 刻度），${p.merge==='mean'?'按有结果的表取平均':'取最近一张有结果的表'}。每张表每臂只有 ${number(p.samples_per_arm)} 场，洗牌噪声很大；只用来排第 3 幕选牌菜单里这些牌的先后，不判断合法性、不剪枝。</p>`;
}
function readinessPart(r){
 const states=r.states||{},c=r.counts||{},groups=r.groups||[];
 const share=Number.isFinite(r.worker_share)?`${decimal(r.worker_share*100,1)}%`:'—';
 const done=groups.filter(g=>g.status!=='UNKNOWN'&&(g.samples||[]).length).sort((a,b)=>(b.mean??-1)-(a.mean??-1)||a.group-b.group);
 const reuse=Object.entries(r.source_rejections||{}).filter(([,n])=>n>0);
 const sched=[`每 ${number(r.every)} 个真实评估派一批`,`已派 ${number(c.dispatched_stages)} 批 ${number(c.scheduled_probes)} 个探针`,
  r.joint_focus?.enabled?`联合排序比较 ${number(r.joint_focus.focus_allocations_compared)} 次，改变派发顺序 ${number(r.joint_focus.dispatch_order_changes)} 次`:null,
  r.dead_retry?.enabled?`满血全输后放改变决策 ${number(r.dead_retry.decisions_changed)} 次`:null].filter(Boolean);
 return `<div class="card-heading"><div><h3>第二首领就绪度（合成探针）</h3><p>每个真实的第 3 幕第一首领入场，满血打第二首领：先 5 场，全输再打 5 场</p></div><span class="chart-pill">${esc(PROBE_STATE[r.state]||r.state||'—')}</span></div>
 ${r.inactive_reason?`<div class="model-tier-note">未启用：${esc(r.inactive_reason)}</div>`:''}
 <dl class="model-detail-metrics"><div><dt>真实入场 / 样本组</dt><dd>${number(r.distinct_real_entries)} / ${number(r.independent_f2_sample_groups)}</dd><small>牌组相同的入场共用一组样本</small></div><div><dt>满血 10 场全输</dt><dd>${number(count(states.DEAD_AT_FULL_HP))} 个入场</dd><small>只用于分配 · 不代表这副牌赢不了</small></div><div><dt>有样本过关</dt><dd>${number(count(states.VIABLE))} 个入场</dd><small>待第二批 ${number(count(states.PENDING))} · 未完成 ${number(count(states.UNKNOWN))}</small></div><div><dt>占原生时间</dt><dd>${share}</dd><small>目标约 1/8，未自动调节 · 可用 ${number(c.usable_probes)} / ${number(c.attempted_probes)}</small></div></dl>
 <div class="model-facts">${sched.map(f=>`<span>${esc(f)}</span>`).join('')}</div>
 <div class="model-ranking-heading"><h4>已完成的样本组</h4><span>按第二首领平均分排列（0–2 刻度）</span></div>
 ${done.length?`<ol class="model-groups">${done.map(g=>{const [text,cls]=GROUP_STATE[g.status]||[g.status,''];return `<li><span class="model-state ${cls}">${esc(text)}</span><span class="model-dots" aria-label="${(g.samples||[]).filter(s=>s.outcome?.won).length} / ${(g.samples||[]).length} 场过关">${(g.samples||[]).map(s=>`<i class="${s.outcome?.won?'won':''}"></i>`).join('')}</span><b>${decimal(g.mean,2)}</b><small>样本组 ${number(g.group)} · ${number((g.shared_entries||[]).length)} 个入场共用${count(g.paired_received)?` · 复用选牌对照 ${number(g.paired_received)} 场`:''}</small></li>`}).join('')}</ol>`:'<p class="model-empty">还没有完成的样本组。</p>'}
 ${reuse.length?`<div class="model-probe-note">选牌对照的跳过臂没能复用为就绪度样本：${reuse.map(([k,n])=>`${esc(REUSE_REJECTION[k]||k)} ${number(n)} 次`).join('，')}</div>`:''}
 <p class="model-caption">探针只在一次性进程里跑，结果不进入真实轨迹、检查点、结果缓存或胜利证据。未完成不算输；满血 10 场全输也不证明这副牌赢不了，只是分配信号。</p>`;
}
function readinessHTML(search){
 const r=search?.f2_readiness,p=search?.paired_card_probes;
 const parts=[r&&typeof r==='object'?readinessPart(r):'',p&&typeof p==='object'&&p.enabled!==false?pairedHTML(p):''].filter(Boolean);
 return parts.join('<hr class="model-rule">');
}
const CLINIC_VERDICT={ROOT:['根不对','dead'],DEPTH:['深度不够','pending'],UNDECIDED:['未定',''],PASSED:['已过关','viable']};
const CLINIC_KIND={RESOURCE:'缺资源（血量）',TACTICAL:'缺打法',MIXED:'资源和打法都缺'};
const CLINIC_EVIDENCE={'root:saturated':'多次入场没有刷新纪录','root:endpoint_below_pass':'进步趋势外推仍到不了过关',
 'root:far_from_pass':'最好成绩离过关很远','root:tactically_insensitive':'重试换打法几乎不提升',
 'root:full_hp_f2_dead':'满血打第二首领全输','root:structural_deficit':'牌组结构落后于结构先验',
 'root:full_hp_projection_short':'外推到满血入场也不够','depth:near_miss':'差一点过关','depth:retry_lift':'重试换打法有明显提升',
 'depth:improving':'最近仍在刷新纪录','depth:viable_at_full_hp':'满血打第二首领能赢','depth:hp_sensitive':'外推到满血入场可过关',
 gate_passed:'已过关',no_entries:'还没有入场'};
const CLINIC_FEATURE={burden:'负担牌',damage_per_draw:'每抽伤害',deck_size:'牌组大小',draw_per_card:'每张牌抽牌',
 energy_per_card:'每张牌能量',hp_fraction:'血量比例',scaling_total:'成长合计',steady_block_per_draw:'每抽稳定格挡',upgrade_density:'升级密度'};
const CLINIC_CLASS={final_gauntlet:'最终幕首领连战',act_boss:'前两幕首领'};
const clinicGate=g=>Array.isArray(g)?gateTitle({act:g[0],ordinal:g[1]}):'未知关口';
const clinicVerdict=v=>{const [text,cls]=CLINIC_VERDICT[v]||[v||'—',''];return `<span class="model-state ${cls}">${esc(text)}</span>`};
const percent=v=>v==null||!Number.isFinite(Number(v))?'—':`${Math.round(Number(v)*100)}%`;
function clinicRows(clinic){
 const picks=new Map((clinic?.allocation||[]).map(a=>[`${a.stem}|${a.gate}`,a]));
 const order={ROOT:0,DEPTH:1,UNDECIDED:2,PASSED:3};
 return (clinic?.gates||[]).map(g=>{const a=picks.get(`${g.stem}|${g.gate}`)||{};return {...g,reroot_picks:count(a.reroot_picks),local_picks:count(a.local_picks)}})
  .sort((a,b)=>(order[a.verdict]??4)-(order[b.verdict]??4)||(b.reroot_picks+b.local_picks)-(a.reroot_picks+a.local_picks)||count(b.distinct_entries)-count(a.distinct_entries));
}
function clinicHTML(clinic,limit=30){
 if(!clinic||typeof clinic!=='object')return '';
 const counts=clinic.counts||{},modes=clinic.focus_modes||{},shares=clinic.shares_percent||{},rows=clinicRows(clinic);
 const verdicts=['ROOT','DEPTH','UNDECIDED','PASSED'].map(v=>[v,rows.filter(r=>r.verdict===v).length]).filter(([,n])=>n);
 const share=['ROOT','UNDECIDED','DEPTH'].filter(v=>shares[v]!=null).map(v=>`${CLINIC_VERDICT[v][0]} ${number(shares[v])}%`).join(' · ');
 const table=rows.length?`<div class="clinic-table-wrap"><table class="clinic-table"><thead><tr><th>分支</th><th>关口</th><th>判定</th><th>入场 / 过关</th><th>最好进度</th><th>根 / 深</th><th>依据</th><th>已分配（扎根 / 局部）</th></tr></thead><tbody>${rows.slice(0,limit).map(r=>`<tr>
<td><code>${esc(String(r.stem||'').slice(0,8))}</code></td><td>${esc(clinicGate(r.gate))}</td>
<td>${clinicVerdict(r.verdict)}${r.kind?`<small>${esc(CLINIC_KIND[r.kind]||r.kind)}</small>`:''}${r.target_act!=null?`<small>回到第 ${number(Number(r.target_act)+1)} 幕重选</small>`:''}</td>
<td>${number(r.distinct_entries)} / ${number(r.passes)}</td><td>${percent(r.best_progress)}</td><td>${number(r.root_score)} / ${number(r.depth_score)}</td>
<td class="clinic-evidence">${(r.evidence||[]).map(e=>`<span class="${String(e).startsWith('root:')?'root':String(e).startsWith('depth:')?'depth':''}">${esc(CLINIC_EVIDENCE[e]||e)}</span>`).join('')}</td>
<td>${number(r.reroot_picks)} / ${number(r.local_picks)}</td></tr>`).join('')}</tbody></table></div>${rows.length>limit?`<p class="model-caption">只显示前 ${number(limit)} 行（共 ${number(rows.length)} 行），按判定和已分配次数排列。</p>`:''}`
  :'<div class="model-empty">还没有关口达到判定所需的入场数。</div>';
 const events=(clinic.events||[]).slice(-8).reverse();
 const eventList=events.length?`<ol class="clinic-events">${events.map(e=>`<li><span>#${number(e.serial)}</span><span>${esc(clinicGate(e.gate))} · <code>${esc(String(e.stem||'').slice(0,8))}</code></span><span>${clinicVerdict(e.from)} → ${clinicVerdict(e.to)}</span><small>入场 ${number(e.entries)} · 最好 ${percent(e.best)}${e.structural_deficit!=null?` · 结构差距 ${decimal(e.structural_deficit)}`:''}</small></li>`).join('')}</ol>`
  :'<div class="model-empty">还没有判定变化。</div>';
 const posterior=clinic.structural_prior?.posterior||{};
 const priors=Object.entries(posterior).filter(([,p])=>p&&p.weights).map(([cls,p])=>{
  const w=Object.entries(p.weights).filter(([,v])=>Number.isFinite(v)).sort((a,b)=>Math.abs(b[1])-Math.abs(a[1]));
  const top=Math.max(1e-9,...w.map(([,v])=>Math.abs(v)));
  return `<div class="clinic-prior"><h4>${esc(CLINIC_CLASS[cls]||cls)} <small>${number(p.rows)} 行${p.fitted?' · 已拟合':' · 未拟合'}</small></h4><ul>${w.map(([k,v])=>`<li><span>${esc(CLINIC_FEATURE[k]||k)}</span><i class="${v<0?'neg':'pos'}" style="width:${(Math.abs(v)/top*50).toFixed(1)}%"></i><b>${signed(v)}</b></li>`).join('')}</ul></div>`}).join('');
 const t=clinic.thresholds||{};
 return `<div class="card-heading"><div><h3>关口诊所 · 诊断模型</h3><p>每个分支在每个首领关口判一次：根不对（该回更早一幕重选）还是深度不够（在附近继续深挖）</p></div><span class="chart-pill">${clinic.contended?'多分支竞争中':'无竞争'}</span></div>
<div class="model-kpis clinic-kpis"><article><span>入账结果</span><strong>${number(clinic.records)}</strong><small>真实结果，合成或缓存的不算（忽略 ${number(counts.ignored)}）</small></article>
<article><span>分支</span><strong>${number(clinic.stems)}</strong><small>按首领前的决策序列划分</small></article>
<article><span>关口判定</span><strong>${verdicts.map(([v,n])=>`${esc(CLINIC_VERDICT[v][0])} ${number(n)}`).join(' · ')||'—'}</strong><small>当前分支 × 关口的判定</small></article>
<article><span>已分配的搜索</span><strong>扎根 ${number(modes.reroot)} · 局部 ${number(modes.local)}</strong><small>修复提示触发的重新扎根 ${number(clinic.hinted_reroots)}</small></article></div>
<p class="model-caption">重新扎根比例：${esc(share||'—')}。判定需要至少 ${number(t.min_entries)} 次不同入场；进度 ≥ ${percent(t.near)} 算接近，&lt; ${percent(t.far)} 算很远。</p>
${table}
<div class="clinic-columns"><section><h4>最近的判定变化</h4>${eventList}</section><section><h4>结构先验（本次运行的后验权重）</h4>${priors||'<div class="model-empty">结构先验未启用。</div>'}<p class="model-caption">只是结构方向，正值表示这个特征越高越容易过关。</p></section></div>
<p class="model-caption">只分配不剪枝：诊断只决定下一次搜索从哪里出发，不删除候选，不是上界，也不证明某条路线赢不了。</p>`;
}
if(typeof module!=='undefined'&&module.exports){module.exports={totals,coefficients,gateHTML,bestOutcome,gateKey,jointGate,jointHTML,pairedRows,readinessHTML,clinicRows,clinicHTML};return}
const get=id=>document.getElementById(id);
const view={run:null,gate:null,data:null,histories:new Map(),stamp:null};
function observe(d){
 if(!d)return;
 const model=d.gate_models;
 if(!model||!Array.isArray(model.gates))return;
 const t=totals(model),j=model.f2_joint_model;
 const signature=JSON.stringify([t.version,...model.gates.map(g=>[gateKey(g),g.entries,g.fitted_entries,g.survived_entries]),j?[j.version,j.entries,j.fitted_entries]:null]);
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
 const model=view.data?.gate_models,joint=jointGate(model),filters={type:get('model-feature-type').value,search:get('model-feature-search').value};
 if(view.gate===JOINT&&joint)get('model-detail').innerHTML=jointHTML(joint,filters,view.data?.search?.paired_card_probes);
 else{
  const gate=(model?.gates||[]).find(g=>gateKey(g)===view.gate);
  if(!gate)return;
  get('model-detail').innerHTML=gateHTML(gate,filters,joint);
 }
 get('model-gates').querySelectorAll('button').forEach(b=>{const active=b.dataset.gate===view.gate;b.classList.toggle('selected',active);b.setAttribute('aria-pressed',String(active))});
}
const track=(fitted,entries)=>`<div class="model-training-track"><i style="width:${entries?Math.min(100,count(fitted)/entries*100):0}%"></i></div>`;
function sync(d){
 if(state.tab!=='model'||!d)return;
 view.data=d;
 const clinic=clinicHTML(d.search?.gate_clinic);get('model-clinic').classList.toggle('hidden',!clinic);get('model-clinic').innerHTML=clinic;
 const model=d.gate_models,meta=d.gate_model_meta||{},readiness=get('model-readiness');
 if(!model||!Array.isArray(model.gates)){
  readiness.classList.add('hidden');
  get('model-content').classList.add('hidden');get('model-status').textContent=d.kind==='component'?'此运行是组件实验，没有在线关口模型。':'此运行未保存关口模型快照。可选择使用 focus / 在线先验的单种子实验。';return;
 }
 if(view.run!==d.id){view.run=d.id;view.gate=null;get('model-feature-search').value='';get('model-feature-type').value=''}
 const gates=model.gates||[],t=totals(model),joint=jointGate(model);
 get('model-content').classList.toggle('hidden',!gates.length);
 const time=meta.snapshot_at?new Date(meta.snapshot_at*1000).toLocaleString('zh-CN',{hour12:false}):'时间未记录';
 get('model-status').textContent=`只统计这次运行（种子 ${d.seed}）· ${gates.length?'最新模型快照':'模型已启用，等待首领入场样本'} · ${time}`;
 if(!gates.length)return;
 get('model-samples').textContent=number(t.entries);get('model-fitted').textContent=number(t.fitted);
 get('model-version').textContent=number(t.version);get('model-survived').textContent=`${number(t.survived)} / ${number(t.entries)}`;
 get('model-gates').innerHTML=gates.map(g=>`<button data-gate="${esc(gateKey(g))}" aria-pressed="false"><span>${esc(gateTitle(g))}</span><strong>${esc(zh.name(g.encounter??'未知遭遇'))}</strong><small>${number(g.entries)} 个入场 · ${number(g.fitted_entries)} 个已拟合</small>${sameGate(g,joint?.replaces_gate)?'<em class="model-tier-tag">档位改由联合表给出</em>':''}${track(g.fitted_entries,g.entries)}</button>`).join('')
  +(joint?`<p class="model-joint-divider">联合表（含合成样本）</p><button data-gate="${JOINT}" aria-pressed="false"><span>第 ${joint.act+1} 幕 · 两个首领联合</span><strong>${esc(zh.name(joint.first??'第一首领'))} + ${esc(zh.name(joint.second??'第二首领'))}</strong><small>${number(joint.entries)} 行 · ${number(joint.fitted_entries)} 行已拟合</small>${track(joint.fitted_entries,joint.entries)}</button>`:'');
 if(!(view.gate===JOINT&&joint)&&!gates.some(g=>gateKey(g)===view.gate))view.gate=gateKey(gates[0]);
 get('model-gates').querySelectorAll('button').forEach(b=>b.onclick=()=>{view.gate=b.dataset.gate;drawDetails()});drawDetails();
 const probes=readinessHTML(d.search);
 readiness.classList.toggle('hidden',!probes);readiness.innerHTML=probes;
 const history=view.histories.get(d.id)?.points||[];
 get('model-history-count').textContent=`${history.length} 个页面观察快照`;
 if(history.length>1)chart('model-training-chart',history,{step:true,xLabel:meta.elapsed_seconds==null?'观察快照':'运行时间',format:v=>Math.round(v)});
 else get('model-training-chart').innerHTML='<div class="empty">已记录当前快照，模型再次更新后显示曲线。</div>';
 get('model-raw').textContent=JSON.stringify({source:meta.source,snapshot_at:meta.snapshot_at,elapsed_seconds:meta.elapsed_seconds,...model,
  ...(d.search?.f2_readiness?{f2_readiness:d.search.f2_readiness}:{}),...(d.search?.paired_card_probes?{paired_card_probes:d.search.paired_card_probes}:{})},null,2);
}
window.gateView={observe,sync,activate:()=>sync(state.data?.selected)};
get('model-feature-search').oninput=get('model-feature-type').onchange=drawDetails;
if(state.data?.selected)observe(state.data.selected);
if(state.tab==='model')window.gateView.activate();
})();
