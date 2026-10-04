// Targeted DOM checks; no browser, native game or solver is started.
const assert=require('node:assert/strict');
const fs=require('node:fs');
const vm=require('node:vm');
const app=fs.readFileSync(require.resolve('../dashboard/web/app.js'),'utf8');
const elements=new Map(),requests=[];
let now=1000000;
function element(id){
 if(!elements.has(id)){
  const classes=new Set();
  elements.set(id,{textContent:'',innerHTML:'',value:'',disabled:false,title:'',style:{},
   classList:{toggle(name,on){if(on)classes.add(name);else classes.delete(name)},add(name){classes.add(name)},remove(name){classes.delete(name)},contains(name){return classes.has(name)}},
   addEventListener(){},querySelectorAll(){return []}});
 }
 return elements.get(id);
}
const location={href:'http://127.0.0.1:8765/',search:''};
const context=vm.createContext({URL,URLSearchParams,location,console,
 Date:class extends Date{static now(){return now}},
 history:{replaceState(_,__,url){location.href=String(url)}},
 document:{getElementById:element,querySelectorAll(){return []}},
 window:{addEventListener(){}},EventSource:class{close(){}addEventListener(){}},
 requestAnimationFrame(){},setInterval(){},setTimeout(){},clearTimeout(){},
 fetch(url,options){return new Promise(resolve=>requests.push({url,options,resolve}))}});
vm.runInContext(app,context);
function selected(phase,extra={}){
 const active=phase==='running',paused=phase==='paused',waiting=['starting','queued'].includes(phase);
 context.fixture={selected:{id:'manual-test',status:'pending',manifest:{manual:true,wall_cap_seconds:10800},performance:{},resources:{},
  job_control:{phase,can_pause:active,can_resume:paused,can_stop:active||paused||waiting,elapsed_seconds:120,sampled_at:1000},...extra},launch:{token:'local-token'}};
 vm.runInContext('state.data=fixture;state.live={available:true,alive:true};renderJobControls()',context);
}
function resolve(index,body,ok=true){requests[index].resolve({ok,json:async()=>body})}
async function checks(){
 selected('running');
 assert.equal(element('job-controls').classList.contains('hidden'),false);
 assert.equal(element('pause-solve').textContent,'暂停求解');
 assert.equal(element('pause-solve').disabled,false);assert.equal(element('stop-solve').disabled,false);
 const pause=vm.runInContext("controlJob('pause')",context);
 assert.equal(requests[0].url,'/api/jobs/manual-test/pause');
 assert.equal(requests[0].options.headers['X-SpireBoard-Token'],'local-token');
 assert.equal(requests[0].options.method,'POST');assert.deepEqual(JSON.parse(requests[0].options.body),{});
 assert.equal(element('pause-solve').disabled,true);assert.equal(element('stop-solve').disabled,true);
 assert.equal(vm.runInContext('state.data.selected.job_control.phase',context),'running','request alone must not claim a frozen state');
 await vm.runInContext("controlJob('stop')",context);assert.equal(requests.length,1,'duplicate controls must not race');
 resolve(0,{run_id:'manual-test',job_control:{phase:'paused',can_pause:false,can_resume:true,can_stop:true,elapsed_seconds:123,sampled_at:1000}});
 await pause;
 assert.equal(element('pause-solve').textContent,'继续求解');assert.equal(element('pause-solve').disabled,false);
 assert.equal(vm.runInContext('currentStatus(state.data.selected)[0]',context),'已暂停（状态已冻结）','a live frozen process is paused');
 now+=15000;assert.equal(vm.runInContext('elapsed(state.data.selected)',context),123);
 assert.equal(vm.runInContext('displaySnapshot({selected:state.data.selected,runs:[],live:{available:true,alive:false}}).selected.job_control.phase',context),'paused');
 const failedResume=vm.runInContext("controlJob('resume')",context);
 resolve(1,{error:'恢复失败'},false);await failedResume;
 assert.equal(vm.runInContext('state.data.selected.job_control.phase',context),'paused');
 assert.equal(element('toast').textContent,'恢复失败');assert.equal(element('pause-solve').disabled,false);
 const resume=element('pause-solve').onclick();
 assert.equal(requests[2].url,'/api/jobs/manual-test/resume');
 resolve(2,{run_id:'manual-test',job_control:{phase:'running',can_pause:true,can_resume:false,can_stop:true,elapsed_seconds:123,sampled_at:1015}});await resume;
 now+=2000;assert.equal(vm.runInContext('elapsed(state.data.selected)',context),125);
 const stop=element('stop-solve').onclick();
 assert.equal(requests[3].url,'/api/jobs/manual-test/stop');
 resolve(3,{run_id:'manual-test',job_control:{phase:'stopped',can_pause:false,can_resume:false,can_stop:false,elapsed_seconds:125,sampled_at:1017}});await stop;
 assert.equal(element('pause-solve').disabled,true);assert.equal(element('stop-solve').disabled,true);
 assert.equal(vm.runInContext('currentStatus(state.data.selected)[0]',context),'已退出求解');
 now+=30000;assert.equal(vm.runInContext('elapsed(state.data.selected)',context),125);
 selected('queued');assert.equal(element('pause-solve').disabled,true);assert.equal(element('stop-solve').disabled,false);
 await vm.runInContext("controlJob('pause')",context);assert.equal(requests.length,4);
 selected('completed');assert.equal(element('pause-solve').disabled,true);assert.equal(element('stop-solve').disabled,true);
 selected('running',{manifest:{manual:false}});assert.equal(element('job-controls').classList.contains('hidden'),true);
 selected('running');
 const wrong=vm.runInContext("controlJob('pause')",context);
 resolve(4,{run_id:'another-run',job_control:{phase:'paused'}});await wrong;
 assert.equal(vm.runInContext('state.data.selected.job_control.phase',context),'running');
 assert.match(element('toast').textContent,/未确认/);
 console.log('Dashboard controls: confirmed pause/resume/stop, frozen elapsed, state guards and request isolation passed.');
}
checks().catch(error=>{console.error(error);process.exitCode=1});
