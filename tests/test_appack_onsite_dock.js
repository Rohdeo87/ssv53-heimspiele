const test=require('node:test');
const assert=require('node:assert/strict');
const {html,sourceOf,viewModel,snapshot}=require('./helpers/platzwart_template');
const view=viewModel();
function ready(){
  const s=snapshot();s.automation={};s.coordination={blockers:[{code:'MANUAL_STOP'}]};s.overall={code:'MANUAL_OR_ERROR_HOLD'};
  Object.assign(s.mower,{state:'STOPPED',activity:'NOT_APPLICABLE',mode:'HOME',errorCode:0});
  s.irrigation.safety.imminent_zone_count=0;
  s.irrigationDockConfirmation={enabled:true,required:true,canConfirm:true,contextToken:'a'.repeat(64)};
  return s;
}
function harness(){
  const state={status:ready(),inFlight:{}},elements={},requests=[];
  const document={getElementById(id){return elements[id]||(elements[id]={classList:{add(){},remove(){}},checked:false,textContent:''})},querySelectorAll(){return []}};
  const dialog={showModal(){},close(){}},setOverallTitle=()=>{},text=(id,value)=>document.getElementById(id).textContent=value;
  const start=html.indexOf('document.getElementById("confirm-go").onclick=function(){');
  const end=html.indexOf('document.getElementById("refresh").onclick',start);
  const code=['clearOnsiteDockConfirmation','prepareOnsiteDockPayload','openAction'].map(sourceOf).join('\n')+'\n'+html.slice(start,end)+'\nreturn {openAction,prepareOnsiteDockPayload};';
  const fn=new Function(...Object.keys(view),'state','document','dialog','setOverallTitle','text','crypto','api','waitForAction',code);
  const actions=fn(...Object.values(view),state,document,dialog,setOverallTitle,text,{randomUUID:()=> 'water-request'},(url,opts)=>{requests.push({url,...opts});return Promise.resolve({})},()=>Promise.resolve());
  return {state,elements,document,actions,requests};
}
test('Only explicit backend permission opens stopped-dock irrigation; no legacy or fault shortcut',()=>{
  const s=ready();assert.equal(view.irrigationActionContext(s,{}).showStart,true);
  for(const change of [{enabled:false},{canConfirm:false},{contextToken:null},{contextToken:'invalid'}]){
    const copy=structuredClone(s);Object.assign(copy.irrigationDockConfirmation,change);assert.equal(view.irrigationActionContext(copy,{}).showStart,false);
  }
  for(const change of [{state:'ERROR',errorCode:9},{state:'OFF'},{connected:false},{activity:'MOWING'},{mode:'MAIN_AREA'},{errorCode:78},{telemetryFresh:false}]){
    const copy=structuredClone(s);Object.assign(copy.mower,change);assert.equal(view.irrigationActionContext(copy,{}).showStart,false);
  }
  delete s.irrigationDockConfirmation;assert.equal(view.irrigationActionContext(s,{}).showStart,false);
});
test('Unchecked confirmation sends nothing; checked all-zones request sends version4 with frozen proof',async()=>{
  const h=harness();h.actions.openAction('START_IRRIGATION','Alle Zonen starten','Starten?',{});
  h.elements['confirm-go'].onclick();assert.equal(h.requests.length,0);
  assert.match(h.elements['onsite-dock-error'].textContent,/bestätigen/);
  h.elements['onsite-dock-check'].checked=true;h.elements['confirm-go'].onclick();
  await Promise.resolve();
  assert.equal(h.requests.length,1);const body=h.requests[0].body;
  assert.equal(body.action,'START_IRRIGATION');assert.equal(body.confirmation,'START_IRRIGATION');assert.equal(body.requestId,'water-request');assert.equal(body.clientContractVersion,4);
  assert.deepEqual(body.manualControl,{operation:'CONFIRM_DOCK_FOR_IRRIGATION',confirmed:true,contextToken:'a'.repeat(64)});
});
test('State change during sighting confirmation cannot send a stale or retargeted water request',()=>{
  for(const change of ['token','error','water','offline']){
    const h=harness();h.actions.openAction('START_IRRIGATION','Alle Zonen starten','Starten?',{});h.elements['onsite-dock-check'].checked=true;
    if(change==='token')h.state.status.irrigationDockConfirmation.contextToken='b'.repeat(64);
    if(change==='error')Object.assign(h.state.status.mower,{state:'ERROR',errorCode:9});
    if(change==='water')h.state.status.irrigation.safety.active_zone_count=1;
    if(change==='offline')h.state.status.mower.connected=false;
    h.elements['confirm-go'].onclick();assert.equal(h.requests.length,0,change);
  }
});
test('Normal docked irrigation keeps version2; per-zone proof preserves zone and duration',()=>{
  const normal=harness();Object.assign(normal.state.status.mower,{state:'RESTRICTED',activity:'PARKED_IN_CS'});normal.state.status.irrigationDockConfirmation={enabled:false};
  normal.actions.openAction('START_IRRIGATION','Alle Zonen starten','Starten?',{});normal.elements['confirm-go'].onclick();
  assert.equal(normal.requests[0].body.clientContractVersion,2);assert.equal(normal.requests[0].body.manualControl,undefined);
  const h=harness();h.actions.openAction('START_IRRIGATION_ZONE','Zone starten','Starten?',{zone:3,runSeconds:600});h.elements['onsite-dock-check'].checked=true;h.elements['confirm-go'].onclick();
  assert.equal(h.requests[0].body.zone,3);assert.equal(h.requests[0].body.runSeconds,600);assert.equal(h.requests[0].body.clientContractVersion,4);
});
