// Browser-independent regression checks for view URLs and run filtering.
const assert=require('node:assert/strict');
const fs=require('node:fs');
const vm=require('node:vm');
const {verifiedRuns}=require('../dashboard/web/route.js');
const app=fs.readFileSync(require.resolve('../dashboard/web/app.js'),'utf8');
function boot(href){
 const elements=new Map();
 const element=id=>{if(!elements.has(id))elements.set(id,{value:id==='protocol-filter'?'A10':id==='status-filter'?'all':'',textContent:'',innerHTML:'',disabled:false,classList:{toggle(){},add(){},remove(){}},addEventListener(){},querySelectorAll(){return []}});return elements.get(id)};
 const location={href,get search(){return new URL(this.href).search}};
 const context=vm.createContext({URL,URLSearchParams,location,console,EventSource:class{close(){}addEventListener(){}},
  history:{replaceState(_,__,url){location.href=String(url)}},
  document:{getElementById:element,querySelectorAll(){return []}},
  window:{addEventListener(){},routeView:{activate(){},runs(){return verifiedRuns(context.fixture)}}},
  requestAnimationFrame(){},setInterval(){},setTimeout(){},clearTimeout(){}});
 vm.runInContext(app,context);
 return {context,element,location};
}
const first=boot('http://127.0.0.1:8765/?run=winner&view=route&step=10');
assert.equal(vm.runInContext('state.tab',first.context),'route');
vm.runInContext("switchTab('runs')",first.context);
assert.equal(new URL(first.location.href).searchParams.get('view'),'runs');
assert.equal(new URL(first.location.href).searchParams.has('step'),false);
const refreshed=boot(first.location.href);
assert.equal(vm.runInContext('state.tab',refreshed.context),'runs');
vm.runInContext("switchTab('model')",refreshed.context);
assert.equal(vm.runInContext('state.tab',boot(refreshed.location.href).context),'model');
vm.runInContext("switchTab('config');connect('another-run')",refreshed.context);
assert.equal(new URL(refreshed.location.href).searchParams.get('view'),'config');
assert.equal(new URL(refreshed.location.href).searchParams.get('run'),'another-run');
assert.equal(vm.runInContext('state.tab',boot('http://127.0.0.1:8765/?view=invalid').context),'scalars');
first.context.fixture=[{id:'winner',status:'verified',wins:1,seed:'42',seeds:['42'],protocol:'A10-seed-v2'},
 {id:'unfinished',status:'completed',wins:0,seed:'43',seeds:['43'],protocol:'A10-seed-v2'}];
vm.runInContext("state.data={runs:fixture};state.selected='winner';switchTab('route')",first.context);
assert.ok(first.element('run-select').innerHTML.includes('winner'));
assert.ok(!first.element('run-select').innerHTML.includes('unfinished'));
vm.runInContext("switchTab('runs')",first.context);
assert.ok(first.element('run-select').innerHTML.includes('unfinished'));
first.element('status-filter').value='verified';
vm.runInContext('renderRuns()',first.context);
assert.ok(first.element('runs-body').innerHTML.includes('winner'));
assert.ok(!first.element('runs-body').innerHTML.includes('unfinished'));
// A delayed route response must not restore view=route after switching away.
const routeSource=fs.readFileSync(require.resolve('../dashboard/web/route.js'),'utf8');
const urlStep=routeSource.match(/^function urlStep\(\).*$/m)[0];
vm.runInContext("const route={run:'winner',index:9};"+urlStep+';urlStep()',first.context);
assert.equal(new URL(first.location.href).searchParams.get('view'),'runs');
vm.runInContext("state.live={available:true,alive:false};",first.context);
first.context.stopped={status:'stopped',search_elapsed_seconds:225,started_at:1,resources:{wall_seconds:1000}};
assert.equal(vm.runInContext('elapsed(stopped)',first.context),225);
assert.equal(vm.runInContext('currentStatus(stopped)[0]',first.context),'已停止（缺少结束报告）');
first.context.auditSnapshot={selected:{id:'old-run',status:'pending',gate_model_meta:{elapsed_seconds:225}},
 runs:[{id:'old-run',status:'pending'}],live:{available:true,alive:false}};
const originalAudit=JSON.stringify(first.context.auditSnapshot);
const projected=vm.runInContext('displaySnapshot(auditSnapshot)',first.context);
assert.equal(projected.selected.status,'stopped');assert.equal(projected.runs[0].status,'stopped');
assert.equal(JSON.stringify(first.context.auditSnapshot),originalAudit);
assert.equal(vm.runInContext('elapsed(displaySnapshot(auditSnapshot).selected)',first.context),225);
console.log('Navigation, refresh, delayed route URL and verified filters passed.');
