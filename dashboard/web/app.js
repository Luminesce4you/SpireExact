const $=id=>document.getElementById(id);
const state={data:null,live:{},history:[],tab:'scalars',axis:'time',log:false,smoothing:0,source:null,selected:null,connected:false,controlPending:null};
const escape=s=>String(s??'').replace(/[&<>"']/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
const number=n=>Number.isFinite(Number(n))?Number(n).toLocaleString('en-US'):'—';
const short=n=>!Number.isFinite(n)?'—':n>=1e6?(n/1e6).toFixed(2)+' M':n>=1e3?(n/1e3).toFixed(1)+' K':Math.round(n).toString();
const duration=n=>{n=Math.max(0,Math.floor(n||0));return [Math.floor(n/3600),Math.floor(n/60)%60,n%60].map(x=>String(x).padStart(2,'0')).join(':')};
const timeLabel=n=>n>=3600?(n/3600).toFixed(1)+' h':n>=60?(n/60).toFixed(1)+' m':Math.round(n)+' s';
const dateLabel=t=>t?new Date(t*1000).toLocaleString('zh-CN',{month:'2-digit',day:'2-digit',hour:'2-digit',minute:'2-digit',hour12:false}):'—';
const titles={scalars:'概览',runs:'全部运行',route:'胜利轨迹',model:'关口模型',events:'事件日志',config:'运行配置'};
const classifications={COMPONENT_REQUEST_COMPLETED:'已完成组件请求',TACTICAL_RESCUE_VERIFIED:'战斗救援重放通过',NATIVE_ROUTE_DEATH:'候选战斗死亡',SEARCH_BUDGET:'计数预算边界',TIMEOUT:'任务保险截断',MECHANISM_OR_HOST_GAP:'机制或宿主缺口',NATIVE_WIN_CANDIDATE:'原生胜利候选',SEARCH_CANCELLED:'已取消候选',DECISION_BOUNDARY:'决策边界',UNKNOWN:'尚未确定',RESOURCE_LIMIT:'资源上限',NATIVE_CRASH:'原生进程退出',INVALID_STATE:'状态校验失败',RESTORE_OR_REPLAY_MISMATCH:'恢复或重放不一致'};
const colors={NATIVE_ROUTE_DEATH:'#9aa5b8',SEARCH_BUDGET:'#e0a04a',TIMEOUT:'#cf9a75',MECHANISM_OR_HOST_GAP:'#cf7f83',NATIVE_WIN_CANDIDATE:'#43ac7e',DECISION_BOUNDARY:'#8c85ce'};
const store={get(key){try{return localStorage.getItem(key)}catch{return null}},set(key,value){try{localStorage.setItem(key,value)}catch{}}};
function toast(text){$('toast').textContent=text;$('toast').classList.remove('hidden');clearTimeout(state.toast);state.toast=setTimeout(()=>$('toast').classList.add('hidden'),2600)}
async function copyText(text,message='已复制'){
 try{await navigator.clipboard.writeText(text)}
 catch{const area=document.createElement('textarea');area.value=text;area.style.position='fixed';area.style.opacity='0';document.body.appendChild(area);area.select();document.execCommand('copy');area.remove()}
 toast(message);
}
function setConnection(ok,label){state.connected=ok;$('connection').className='connection '+(ok?'':'offline');$('connection').innerHTML='<i></i>'+escape(label)}
function displaySnapshot(data){
 const d=data.selected,live=data.live||{};
 if(jobControl(d)?.phase==='paused')return data;
 if(!d||d.kind==='component'||d.status!=='pending'||!live.available||live.alive!==false)return data;
 const stopped={...d,status:'stopped',status_reason:'作业进程已停止，尚无最终结束报告。'};
 return {...data,selected:stopped,runs:data.runs.map(r=>r.id===d.id?{...r,status:'stopped'}:r)};
}
function connect(run){
  if(run){const url=new URL(location.href);url.searchParams.set('run',run);history.replaceState(null,'',url)}
  state.source?.close();setConnection(false,'正在连接');
  const source=new EventSource('/api/events'+(run?'?run='+encodeURIComponent(run):''));state.source=source;
  source.addEventListener('snapshot',e=>{if(source!==state.source)return;const data=displaySnapshot(JSON.parse(e.data));state.data=data;state.selected=data.selected?.id;state.live=data.live||{};state.history=data.telemetry_history||[];setConnection(true,'实时连接');render();});
  source.addEventListener('telemetry',e=>{if(source!==state.source)return;const p=JSON.parse(e.data);if(p.run!==state.selected)return;state.live=p.sample;if(p.sample.alive){state.history.push(p.sample);if(state.history.length>3600)state.history.shift()}setConnection(true,'实时连接');const projected=displaySnapshot({...state.data,live:p.sample});if(projected.selected!==state.data?.selected){state.data=projected;render()}else{renderTelemetry();updateElapsed()}});
  source.addEventListener('watch_error',e=>toast(JSON.parse(e.data).message));
  source.onerror=()=>{if(source===state.source)setConnection(false,'连接中断，正在重连')};
}
function currentStatus(d){const control=jobControl(d),phase=control?.phase;if(phase==='paused')return ['已暂停（状态已冻结）','paused'];if(d.status==='verified')return ['验证通过','verified'];if(phase==='stopped')return control.user_cancelled===false?['已停止（缺少结束报告）','stopped']:['已退出求解','stopped'];if(phase==='running')return ['运行中','running'];if(d.status==='stopped'||(d.status==='pending'&&state.live.available&&state.live.alive===false))return ['已停止（缺少结束报告）','stopped'];if(d.status==='queued')return ['排队中',''];if(d.status==='starting')return ['正在准备',''];if(d.status==='paused')return ['已按要求暂停','paused'];if(d.status==='invalid')return ['测试已作废','error'];if(d.status==='error')return ['异常结束','error'];if(d.status==='completed')return ['已完成',''];if(state.live.alive)return ['运行中','running'];return ['状态待确认',''];}
// Status of a row in the run list, where only the summary fields are known.
function runStatus(r){
 if(r.status==='paused')return ['已暂停','paused'];
 if(r.alive)return ['运行中','running'];
 return {queued:['排队中',''],starting:['正在准备',''],paused:['已暂停',''],stopped:['已停止（缺少结束报告）','stopped'],invalid:['测试作废','error'],verified:['验证通过','verified'],error:['异常结束','error'],completed:['已完成','']}[r.status]||['待确认',''];
}
function elapsed(d){const control=jobControl(d);if(control&&Number.isFinite(control.elapsed_seconds)){const extra=control.phase==='running'&&Number.isFinite(control.sampled_at)?Math.max(0,Date.now()/1000-control.sampled_at):0;return control.elapsed_seconds+extra}if(d.status==='stopped'||(d.status==='pending'&&state.live.available&&state.live.alive===false))return d.search_elapsed_seconds??d.gate_model_meta?.elapsed_seconds??d.resources?.wall_seconds??0;if(d.status==='queued'||d.status==='starting')return 0;if(d.report?.wall_seconds!=null)return d.report.wall_seconds;if(d.resources?.wall_seconds!=null&&!state.live.alive)return d.resources.wall_seconds;if(d.performance?.complete)return d.performance.mean_seconds||0;return d.started_at?Math.max(0,Date.now()/1000-d.started_at):0}
function jobControl(d){return d&&d.kind!=='component'&&d.job_control?d.job_control:null}
function renderJobControls(){
 const d=state.data?.selected,control=jobControl(d),pending=state.controlPending,here=pending?.run===d?.id;
 $('job-controls').classList.toggle('hidden',!d||d.kind==='component');
 const pause=$('pause-solve'),stop=$('stop-solve');pause.disabled=true;stop.disabled=true;
 pause.textContent='暂停求解';stop.textContent='退出求解';pause.title='';stop.title='';
 if(!d||d.kind==='component')return;
 if(!control||control.registered!==true){
  $('job-control-note').textContent=['verified','completed','error','stopped'].includes(d.status)?'求解已结束，已有记录仍可查看。':control?.control_unavailable_reason||'此任务未接入作业控制，无法暂停或退出。';
  return;
 }
 const resume=control.phase==='paused';pause.textContent=here&&pending.action!=='stop'?(pending.action==='resume'?'正在继续…':'正在暂停…'):resume?'继续求解':'暂停求解';
 stop.textContent=here&&pending.action==='stop'?'正在退出…':'退出求解';
 pause.disabled=!!pending||!(resume?control.can_resume:control.can_pause);stop.disabled=!!pending||!control.can_stop;
 pause.title=resume?'从冻结的当前状态继续求解':control.phase==='queued'||control.phase==='starting'?'求解开始后可暂停':'冻结当前求解状态';
 if(control.pause_unavailable_reason)pause.title=control.pause_unavailable_reason;
 stop.title='结束这次求解并保留已有记录';
 $('job-control-note').textContent=here?({pause:'正在冻结求解状态…',resume:'正在恢复求解…',stop:'正在退出求解…'}[pending.action]):control.control_unavailable_reason||control.pause_unavailable_reason||(control.phase==='paused'?'当前状态已冻结，点击继续求解。':control.phase==='stopped'?'求解已退出，已有记录仍可查看。':control.phase==='queued'?'等待启动，可退出队列。':control.phase==='starting'?'正在准备，可退出求解。':control.phase==='running'?'暂停后保留当前状态，继续后接着求解。':['completed','error'].includes(control.phase)?'求解已结束，已有记录仍可查看。':'当前暂不可操作。');
}
async function controlJob(action){
 const d=state.data?.selected,control=jobControl(d),allowed=control?.registered===true?{pause:control.can_pause,resume:control.can_resume,stop:control.can_stop}:{};
 if(state.controlPending||!allowed[action])return;
 state.controlPending={run:d.id,action};renderJobControls();
 try{
  const token=state.data?.launch?.token;if(!token)throw new Error('尚未连接后端，请稍后重试。');
  const response=await fetch('/api/jobs/'+encodeURIComponent(d.id)+'/'+action,{method:'POST',headers:{'Content-Type':'application/json','X-SpireBoard-Token':token},body:'{}'});
  const result=await response.json();if(!response.ok)throw new Error(result.error||'求解控制失败');
  if(result.run_id!==d.id||!result.job_control)throw new Error('后端未确认求解状态，请重新连接查看。');
  if(state.data?.selected?.id===d.id){state.data={...state.data,selected:{...state.data.selected,job_control:result.job_control}};updateElapsed();}
  toast({pause:'求解已暂停，状态已冻结。',resume:'求解已继续。',stop:'已退出求解。'}[action]);
 }catch(error){toast(error.message)}finally{state.controlPending=null;renderJobControls();}
}
function setting(m,key,fallback='—'){const i=(m.settings||[]).indexOf(key);return i>=0?m.settings[i+1]:fallback}
function updateElapsed(){const d=state.data?.selected;if(!d)return;const t=elapsed(d),budget=d.manifest.wall_cap_seconds||d.manifest.resource_limits?.wall_seconds||180;$('elapsed').textContent=duration(t);$('budget-progress').style.width=Math.min(100,t/budget*100)+'%';$('budget-note').textContent=d.performance.complete?`平均每种子用时，上限 ${Math.round(budget/60)} 分钟`:`已用 ${Math.min(100,t/budget*100).toFixed(1)}%，上限 ${Math.round(budget/60)} 分钟`;
 // The 150-minute report point only exists inside a longer budget.
 $('budget-mark').classList.toggle('hidden',budget<=9000||d.kind==='component');$('budget-mark').style.left=(9000/budget*100)+'%';
 if(d.kind==='component'){$('budget-progress').style.width=(d.evaluations/d.planned*100)+'%';$('budget-note').textContent=`${d.evaluations} / ${d.planned} 请求，每配置安全上限 ${Math.round(budget/60)} 分钟`;}
 if(d.status==='stopped')$('budget-note').textContent='最后保存的搜索时长，缺少最终结束报告';
 if(d.status==='queued')$('budget-note').textContent='等待已有任务结束，时间预算尚未开始';
 if(jobControl(d)?.phase==='paused')$('budget-note').textContent='已暂停，暂停时间不计入求解预算';
 if(jobControl(d)?.phase==='stopped')$('budget-note').textContent='已退出求解，显示最后保存的运行时长';
 renderJobControls();
 const [label,kind]=currentStatus(d);$('run-status').textContent=label;$('run-status').className='status-badge '+kind;}
function renderRunSelect(){const route=state.tab==='route',rows=route?(window.routeView?.runs()||[]):(state.data?.runs||[]);$('run-select-label').textContent=route?'胜利运行':'当前运行';$('run-select').disabled=!rows.length;$('run-select').innerHTML=rows.map(r=>`<option value="${escape(r.id)}" ${r.id===state.selected?'selected':''}>${r.alive?'● ':r.status==='verified'?'✓ ':''}${escape(r.id)}${r.seed!=null&&!String(r.id).includes(String(r.seed))?' · 种子 '+escape(r.seed):''}</option>`).join('')||(route?'<option value="">暂无验证通过的胜利</option>':'');}
function render(){
 const launch=state.data?.launch,ready=launch?.ready===true;
 const profile=launch?.feature_profile||'i100';
 $('new-solve').disabled=!ready;$('new-solve').title=ready?`启动已准备的 ${profile} 配置`:launch?.unavailable_reason||'正在连接本地服务';
 $('source-ready').textContent=ready?`${profile} · 已就绪`:`${profile} · 尚未准备`;$('source-ready').title=$('new-solve').title;
 const data=state.data,d=data?.selected;const maxMinutes=data?.launch?.max_minutes||15;for(const option of $('solve-minutes').options)option.disabled=Number(option.value)>maxMinutes;if(Number($('solve-minutes').value)>maxMinutes)$('solve-minutes').value=String(maxMinutes);renderJobControls();if(!d){$('run-name').textContent='尚未发现任何运行';return}
 const m=d.manifest,component=d.kind==='component',c=d.component;
 const chip=(label,value,copy)=>`<span class="chip">${label}<b>${escape(value)}</b>${copy?`<button class="chip-copy" data-copy="${escape(value)}" title="复制${label}">复制</button>`:''}</span>`;
 $('run-name').textContent=d.id;
 $('run-meta').innerHTML=['<span class="chip"><b>铁甲战士</b></span>',chip('进阶',setting(m,'--ascension',d.protocol==='A10-seed-v2'?'10':'0')),d.seed?chip('种子',d.seed,true):chip('种子数',d.planned),m.solver_seed!=null?chip('求解器种子',m.solver_seed):'',chip('协议',d.protocol),chip('版本',(m.version||'—').slice(0,10))].join('');
 $('floor').innerHTML=(d.furthest_floor?number(d.furthest_floor):'—')+'<small>层</small>';$('act-note').textContent=d.act?`第 ${d.act} 幕，取自已完成的原生评估`:'取自已完成的原生评估';
 $('nodes').textContent=d.nodes?short(d.nodes):'—';$('eval-count').textContent=number(d.evaluations);
 $('wins').innerHTML=d.seed?(d.wins?'1<small>次</small>':'0<small>次</small>'):`${d.wins}<small>/ ${d.tested} 个已测种子</small>`;
 $('win-note').textContent=d.seed&&!d.wins?'还没有找到通过独立重放的路线':'只有独立完整重放通过才计为胜利';
 $('floor-label').textContent=component?'当前配置进度':'最远到达层数';
 $('wins-label').textContent=component?'配置对照进度':'已验证胜利';
 $('progress-title').textContent=component?'性能测试进度':'最远到达层数';
 $('progress-note').textContent=component?'已完成配置的累计请求数':'每次评估结束后更新的最远楼层';
 if(component){
   $('run-name').textContent=c.title||('P 核超线程对照 · '+(c.arm||'等待启动'));
   $('run-meta').innerHTML=`<span class="chip">${escape(c.description||`第 ${c.repeat+1} / 2 轮 · ${c.logical_cpus} 逻辑线程 / ${c.physical_cores??'—'} 个 P 核 · ${c.workers} workers · ${c.runtime_profile}`)}</span>`;
   $('floor').innerHTML=`${c.completed_requests}<small>/ ${c.requests_per_arm} 请求</small>`;
   $('act-note').textContent='同一批原生请求，比较吞吐与结果一致性';
   $('wins').innerHTML=`${c.completed_arms}<small>/ ${c.total_arms} 配置</small>`;
   $('win-note').textContent='组件性能实验，不计为新局胜利';
 }
 renderRunSelect();
 updateElapsed();renderCharts();renderTelemetry();renderEvents();renderRuns();renderConfig();window.routeView?.sync(d);window.gateView?.observe(d);window.gateView?.sync(d);
 $('classification').innerHTML=Object.entries(d.classes).sort((a,b)=>b[1]-a[1]).map(([key,n])=>`<div class="dist-row"><span>${escape(classifications[key]||key)}</span><div class="dist-bar"><i style="width:${n/Math.max(d.evaluations,1)*100}%;background:${colors[key]||'#9aa5b8'}"></i></div><span class="dist-number">${number(n)}</span></div>`).join('')||'<p class="empty">首个评估完成后显示。</p>';
}
function chart(id,series,{color='var(--chart-floor)',unit='',yMax=null,step=false,xLabel='运行时间',format=short,smooth=false,empty='暂无数据'}={}){
 const el=$(id),width=Math.max(el.clientWidth,260),height=el.clientHeight||220,pad={l:48,r:16,t:14,b:30},w=width-pad.l-pad.r,h=height-pad.t-pad.b;
 const data=series.filter(p=>Number.isFinite(p.x)&&Number.isFinite(p.y));
 if(!data.length){el.innerHTML=`<div class="empty">${escape(empty)}</div>`;el.onmousemove=null;return}
 const xs=data.map(p=>p.x),ys=data.map(p=>p.y);let lo=Math.min(...xs),hi=Math.max(...xs),max=yMax??Math.max(...ys)*1.15;
 if(hi===lo){lo=Math.max(0,lo-1);hi+=Math.max(1,hi*.1)}if(!max)max=1;
 const tx=x=>state.log&&xLabel!=='实时采样'?Math.log1p(Math.max(0,x)):x;
 const px=x=>pad.l+(tx(x)-tx(lo))/(tx(hi)-tx(lo))*w,py=y=>pad.t+h-Math.min(max,Math.max(0,y))/max*h;
 const tick=x=>xLabel==='评估次数'||xLabel==='观察快照'?String(Math.round(x)):timeLabel(x);
 let svg=`<svg viewBox="0 0 ${width} ${height}" aria-hidden="true">`;
 for(let i=0;i<=4;i++){const value=max*i/4,y=py(value);svg+=`<line class="grid" x1="${pad.l}" x2="${width-pad.r}" y1="${y}" y2="${y}"/><text x="${pad.l-8}" y="${y+4}" text-anchor="end">${escape(format(value))}${unit}</text>`}
 for(let i=0;i<=4;i++){const x=lo+(hi-lo)*i/4;svg+=`<text x="${px(x)}" y="${height-8}" text-anchor="middle">${escape(tick(x))}</text>`}
 let shown=data;if(smooth&&state.smoothing){let last=data[0].y;shown=data.map(p=>{last=last*state.smoothing+p.y*(1-state.smoothing);return {...p,y:last}})}
 const path=rows=>rows.map((p,i)=>i?(step?`H${px(p.x)} V${py(p.y)}`:`L${px(p.x)},${py(p.y)}`):`M${px(p.x)},${py(p.y)}`).join(' ');
 const raw=path(data),line=path(shown);
 if(data.length>1)svg+=`<path d="${line} L${px(data.at(-1).x)},${pad.t+h} L${px(data[0].x)},${pad.t+h} Z" style="fill:${color}" opacity=".08"/>`;
 if(shown!==data)svg+=`<path d="${raw}" fill="none" style="stroke:${color}" stroke-width="1" opacity=".3"/>`;
 svg+=`<path d="${line}" fill="none" style="stroke:${color}" stroke-width="2" stroke-linejoin="round" stroke-linecap="round"/>`;
 const last=shown.at(-1);svg+=`<circle cx="${px(last.x)}" cy="${py(last.y)}" r="3.5" style="fill:var(--surface);stroke:${color}" stroke-width="2"/>`;
 svg+=`<line class="cross" y1="${pad.t}" y2="${pad.t+h}" visibility="hidden"/><circle class="cross-dot" r="4" style="fill:${color}" visibility="hidden"/></svg>`;
 el.innerHTML=svg;
 const cross=el.querySelector('.cross'),dot=el.querySelector('.cross-dot');
 el.onmousemove=e=>{const rect=el.getBoundingClientRect(),x=(e.clientX-rect.left)*width/rect.width;const point=data.reduce((a,b)=>Math.abs(px(b.x)-x)<Math.abs(px(a.x)-x)?b:a);
  cross.setAttribute('x1',px(point.x));cross.setAttribute('x2',px(point.x));cross.setAttribute('visibility','visible');dot.setAttribute('cx',px(point.x));dot.setAttribute('cy',py(point.y));dot.setAttribute('visibility','visible');
  $('tooltip').innerHTML=`<small>${escape(xLabel)} ${escape(tick(point.x))}</small><b>${escape(format(point.y))}${unit}</b>${point.label?'<small>'+escape(point.label)+'</small>':''}`;$('tooltip').style.left=Math.min(e.clientX+14,innerWidth-240)+'px';$('tooltip').style.top=Math.max(8,e.clientY-64)+'px';$('tooltip').classList.remove('hidden')};
 el.onmouseleave=()=>{$('tooltip').classList.add('hidden');cross.setAttribute('visibility','hidden');dot.setAttribute('visibility','hidden')};
}
function renderCharts(){const d=state.data?.selected;if(!d)return;const points=d.points.map(p=>({...p,x:state.axis==='step'?p.step:p.seconds})).filter(p=>p.x!=null);const xLabel=state.axis==='step'?'评估次数':'运行时间',empty='首个评估完成后显示';chart('progress-chart',points.map(p=>({x:p.x,y:p.best_floor,label:p.label})),{step:true,xLabel,yMax:d.kind==='component'?d.planned:Math.max(10,d.furthest_floor+5),format:v=>Math.round(v),empty});chart('nodes-chart',points.map(p=>({x:p.x,y:p.nodes,label:p.label})),{color:'var(--chart-nodes)',xLabel,smooth:true,empty});$('floor-pill').textContent=d.kind==='component'?`${d.evaluations} / ${d.planned} 请求`:d.furthest_floor?'第 '+d.furthest_floor+' 层':'等待评估';$('nodes-pill').textContent=d.nodes?number(d.nodes):'等待评估';}
function renderTelemetry(){const d=state.data?.selected;if(!d)return;const live=state.live,cpus=d.manifest.cpu_set?.length||d.manifest.resource_limits?.cpus||8;let history=state.history.filter(p=>p.alive);const origin=history[0]?.sampled_at||0,empty='只在运行期间采样，当前没有采样数据',limit=(d.manifest.workload_bytes||d.resources.workload_limit_bytes||6442450944)/1073741824;
 chart('cpu-chart',history.filter(p=>p.cpu_cores!=null).map(p=>({x:p.sampled_at-origin,y:p.cpu_cores/cpus*100})),{color:'var(--chart-cpu)',unit:'%',yMax:100,xLabel:'实时采样',format:v=>v.toFixed(0),empty});
 chart('memory-chart',history.map(p=>({x:p.sampled_at-origin,y:p.private_bytes/1073741824})),{color:'var(--chart-memory)',yMax:limit,xLabel:'实时采样',format:v=>v.toFixed(1),empty});
 $('cpu-pill').textContent=live.alive&&live.cpu_cores!=null?(live.cpu_cores/cpus*100).toFixed(1)+'%':'未在采样';$('memory-pill').textContent=live.alive?(live.private_bytes/1073741824).toFixed(2)+' GiB':'未在采样';
 $('memory-note').textContent=`进程树私有提交量（GiB），限额 ${limit} GiB`;
 $('cpu-note').textContent=`Windows 采样值，不等于 worker 占用率；${cpus} 个逻辑 CPU，${live.processes??'—'} 个进程`;
 if(live.sampled_at)$('last-update').textContent='更新于 '+new Date(live.sampled_at*1000).toLocaleTimeString('zh-CN',{hour12:false});
}
function eventInfo(e){const components={runtime_benchmark_completed:['运行时对照完成',e.passed?'结果与节点一致性通过':'一致性检查未通过',e.passed?'good':'warn'],preparation_diagnosis_started:['本幕准备诊断已启动','固定前幕，仅搜索当前幕','good'],preparation_root_started:['开始搜索幕入口',e.root,''],preparation_root_completed:['幕入口搜索完成',e.root+(e.rescued?' · 关口救援重放通过':' · 预算内未救回'),e.rescued?'good':''],preparation_diagnosis_completed:['本幕准备诊断完成','保存全部根与重放结果','good'],component_paused:['超线程测试已暂停','按用户要求保留已完成数据',''],tactical_diagnosis_started:['战术配对实验已启动','节点与搜索宽度对照','good'],tactical_case_completed:['战斗样本完成',e.case+' / '+e.arm+(e.rescued?' · 已救回':' · 未救回'),''],tactical_diagnosis_completed:['战术配对实验完成','检查各配置救援结果','good'],component_started:['性能配置已启动',e.arm+' · 第 '+(e.repeat+1)+' 轮','good'],component_completed:['性能配置已完成',e.arm+' · '+Number(e.component_wall_seconds??e.wall_seconds).toFixed(1)+' s','good'],component_failed:['性能配置异常',e.arm+' · 退出码 '+e.exit_code,'warn'],smt_benchmark_completed:['超线程对照已完成',e.passed?'结果与节点一致性通过':'一致性检查未通过',e.passed?'good':'warn']};if(components[e.event])return components[e.event];const map={baseline_started:['长基线已启动','全新开局 · '+(e.protocol||'A10-seed-v2'),'good'],started:['资源隔离已就绪',(e.cpus||[]).length+' 个逻辑 CPU · 受限作业','good'],new_furthest_evaluation:['探索到第 '+e.floor+' 层',e.label+' · '+(classifications[e.classification]||e.classification),''],native_win_candidate:['发现原生胜利候选','等待独立完整重放','warn'],report_point_150min:['到达 150 分钟报告点','资源记录已发布，搜索继续','warn'],baseline_completed:['基线运行已结束',e.verified_win?'独立重放验证通过':'本次预算内未得到验证胜利',e.verified_win?'good':''],baseline_failed:['基线执行异常','退出码 '+e.exit_code,'warn'],censored:['达到安全上限',e.reason||'未解出不能证明无解','warn'],failed:['运行中止',e.reason||'查看执行记录','warn'],process_finished:['求解进程已结束',e.result_exists?'结果文件已生成':'结果文件尚未生成','']};return map[e.event]||[e.event,'运行事件',''];}
function eventRows(events){return events.length?events.map(e=>{const [title,detail,type]=eventInfo(e);return `<div class="event-row"><span class="event-symbol ${type}">${type==='good'?'✓':type==='warn'?'!':'·'}</span><div class="event-title">${escape(title)}<small>${escape(detail)}</small></div><span class="event-time">${duration(e.wall_seconds)}</span></div>`}).join(''):'<p class="empty">这次运行还没有事件记录。</p>'}
function renderEvents(){const d=state.data?.selected;if(!d)return;const events=[...d.events].reverse();$('recent-events').innerHTML=eventRows(events.slice(0,3));$('all-events').innerHTML=eventRows($('event-filter').value==='important'?events.filter(e=>e.event!=='new_furthest_evaluation'):events);}
function renderRuns(){if(!state.data)return;const query=$('run-search').value.toLowerCase(),filter=$('protocol-filter').value,statusFilter=$('status-filter').value;const rows=state.data.runs.filter(r=>(statusFilter==='all'||(statusFilter==='running'?r.alive:r.status===statusFilter))&&(filter==='all'||r.protocol.startsWith('A10'))&&(r.id+' '+r.seed+' '+r.seeds.join(' ')).toLowerCase().includes(query));$('runs-count').textContent=`${rows.length} / ${state.data.runs.length} 个运行`;$('runs-body').innerHTML=rows.map(r=>{const [label,kind]=runStatus(r),winner=r.status==='verified'&&r.wins>0&&r.seed!=null&&r.kind!=='component';return `<tr data-run="${escape(r.id)}" tabindex="0" class="${r.id===state.selected?'current':''}"><td><strong>${escape(r.id)}</strong><small>${escape(r.protocol)}</small></td><td class="mono">${r.seed!=null?escape(r.seed):escape(r.seeds.length+' 个种子')}</td><td><span class="status-badge ${kind}">${label}</span></td><td class="numeric">${r.furthest_floor?number(r.furthest_floor):'—'}</td><td class="numeric">${r.kind==='component'?'性能对照':r.wins+' / '+r.planned}</td><td class="numeric">${number(r.evaluations)}</td><td>${dateLabel(r.started_at)}</td><td class="row-actions">${winner?`<button class="link-button" data-route="${escape(r.id)}">胜利轨迹</button>`:''}</td></tr>`}).join('')||'<tr><td colspan="8" class="empty">没有符合筛选条件的运行</td></tr>'}
function openRun(target){const route=target.closest?.('[data-route]'),row=target.closest?.('[data-run]');if(route){connect(route.dataset.route);switchTab('route')}else if(row){connect(row.dataset.run);switchTab('scalars')}}
function renderConfig(){const d=state.data?.selected;if(!d)return;const m=d.manifest;const pairs=[['协议',d.protocol],['角色','铁甲战士（IRONCLAD）'],['进阶',setting(m,'--ascension','—')],['游戏种子',d.seed||d.seeds.join(', ')],['求解器种子',m.solver_seed??'—'],['CPU 集合',(m.cpu_set||[]).join(', ')||'见历史资源清单'],['内存预算',((m.workload_bytes||d.resources.workload_limit_bytes||6442450944)/1073741824)+' GiB + 2 GiB 预留'],['Worker / DOP',setting(m,'--workers')+' / '+setting(m,'--dop')],['战斗节点预算',number(Number(setting(m,'--nodes',NaN)))],['时间上限',m.wall_cap_seconds?Math.round(m.wall_cap_seconds/60)+' 分钟':'180 秒（旧协议）'],['求解器版本',(m.version||'—').slice(0,16)],['原生游戏指纹',(m.native_build?.inputs?.dependencies?.['sts2.dll']||m.game_sha256||'—').slice(0,20)]];$('config-list').innerHTML=pairs.map(([k,v])=>`<div><dt>${escape(k)}</dt><dd>${escape(v)}</dd></div>`).join('');$('raw-config').textContent=JSON.stringify(m,null,2);}
function switchTab(tab){if(!Object.hasOwn(titles,tab))return;state.tab=tab;const url=new URL(location.href);url.searchParams.set('view',tab);if(tab!=='route')url.searchParams.delete('step');history.replaceState(null,'',url);document.querySelectorAll('.view').forEach(v=>v.classList.toggle('hidden',v.id!==tab+'-view'));document.querySelectorAll('.nav').forEach(b=>{const on=b.dataset.tab===tab;b.classList.toggle('active',on);if(on)b.setAttribute('aria-current','page');else b.removeAttribute('aria-current')});$('page-title').textContent=titles[tab];$('run-context').classList.toggle('hidden',tab==='runs');renderRunSelect();if(tab==='scalars')requestAnimationFrame(()=>{renderCharts();renderTelemetry()});if(tab==='route')window.routeView?.activate();if(tab==='model')window.gateView?.activate();}
document.querySelectorAll('[data-tab]').forEach(b=>b.onclick=()=>switchTab(b.dataset.tab));document.querySelectorAll('[data-goto]').forEach(b=>b.onclick=()=>switchTab(b.dataset.goto));
document.querySelectorAll('[data-axis]').forEach(b=>b.onclick=()=>{state.axis=b.dataset.axis;document.querySelectorAll('[data-axis]').forEach(x=>x.classList.toggle('selected',x===b));renderCharts()});
$('log-x').onchange=e=>{state.log=e.target.checked;renderCharts()};$('smoothing').oninput=e=>{state.smoothing=e.target.value/100;$('smooth-value').textContent=state.smoothing.toFixed(2);renderCharts()};
$('run-select').onchange=e=>connect(e.target.value);$('refresh').onclick=()=>{connect(state.selected);toast('已重新连接实时数据')};$('run-search').oninput=renderRuns;$('protocol-filter').onchange=renderRuns;$('status-filter').onchange=renderRuns;$('event-filter').onchange=renderEvents;
$('runs-body').onclick=e=>openRun(e.target);$('runs-body').onkeydown=e=>{if(e.key==='Enter'&&e.target.tagName==='TR')openRun(e.target)};
$('export').onclick=()=>{if(!state.data?.selected)return;const a=document.createElement('a'),url=URL.createObjectURL(new Blob([JSON.stringify({...state.data,live:state.live,telemetry_history:state.history},null,2)],{type:'application/json'}));a.href=url;a.download='SpireBoard-'+state.selected+'.json';a.click();URL.revokeObjectURL(url);toast('已导出当前运行的数据快照')};
document.addEventListener?.('click',e=>{const b=e.target.closest?.('[data-copy]');if(b){e.preventDefault();copyText(b.dataset.copy,'已复制 '+b.dataset.copy)}});
const themeNow=()=>document.documentElement?.dataset.theme||(typeof matchMedia==='function'&&matchMedia('(prefers-color-scheme: dark)').matches?'dark':'light');
function showTheme(){$('theme-toggle').textContent=themeNow()==='dark'?'浅色':'深色'}
$('theme-toggle').onclick=()=>{const next=themeNow()==='dark'?'light':'dark';document.documentElement.dataset.theme=next;store.set('spireboard.theme',next);showTheme()};showTheme();
$('new-solve').onclick=()=>{$('launch-dialog').showModal();$('solve-seed').focus()};$('launch-close').onclick=()=>$('launch-dialog').close();
$('pause-solve').onclick=()=>controlJob(jobControl(state.data?.selected)?.phase==='paused'?'resume':'pause');$('stop-solve').onclick=()=>controlJob('stop');
let resize;window.addEventListener('resize',()=>{clearTimeout(resize);resize=setTimeout(()=>{renderCharts();renderTelemetry()},100)});
setInterval(updateElapsed,1000);const requestedView=new URLSearchParams(location.search).get('view');if(Object.hasOwn(titles,requestedView))switchTab(requestedView);connect(new URLSearchParams(location.search).get('run'));

$('launch-form').addEventListener('submit',async event=>{
 event.preventDefault();const button=$('launch-button'),message=$('launch-message');button.disabled=true;message.className='';message.textContent='正在提交…';
 try{
  const token=state.data?.launch?.token;if(!token)throw new Error('尚未连接后端，请稍后重试。');
  const response=await fetch('/api/jobs',{method:'POST',headers:{'Content-Type':'application/json','X-SpireBoard-Token':token},body:JSON.stringify({seed:$('solve-seed').value.trim(),minutes:Number($('solve-minutes').value)})});
  const result=await response.json();if(!response.ok)throw new Error(result.error||'启动失败');
  message.className='success';message.textContent=result.queued?'已加入队列，前一任务结束后自动开始。':'任务已提交，正在启动原生求解器。';
  history.replaceState(null,'',result.url);connect(result.run_id);switchTab('scalars');$('launch-dialog').close();toast(message.textContent);
 }catch(error){message.className='error';message.textContent=error.message;}finally{button.disabled=false;}
});
