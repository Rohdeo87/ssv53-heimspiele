const assert = require('node:assert/strict');
const test = require('node:test');
const {sourceOf,viewModel,snapshot}=require('./helpers/platzwart_template');
const view=viewModel();
function ready(){
  const s=snapshot();s.automation={};s.coordination={blockers:[]};
  s.manualControl={enabled:true,status:'AUTOMATIC',canStart:true,canPark:true,canResume:true};
  s.irrigation.safety.imminent_zone_count=0;
  return s;
}
const keys=['manualStart','manualPark','manualResume','husqvarnaStart','husqvarnaPark'];
test('18,480 combinations: physical stop, authority, pending actions and moving mower invariants',()=>{
  const physical=[['STOPPED','NOT_APPLICABLE'],['OFF','NOT_APPLICABLE'],['IN_OPERATION','MOWING'],['IN_OPERATION','LEAVING'],['IN_OPERATION','GOING_HOME'],['IN_OPERATION','CHARGING'],['RESTRICTED','PARKED_IN_CS'],['PAUSED','NOT_APPLICABLE'],['ERROR','NOT_APPLICABLE'],['FATAL_ERROR','NOT_APPLICABLE'],['UNKNOWN','UNKNOWN']];
  const statuses=['AUTOMATIC','MANUAL_PARKED','PARKING','MANUAL_MOWING','WAITING_WATER','PREPARED','UNKNOWN'];
  const guards=['clear','water','training','game','drying','unconfirmed','readOnly','permissionDenied'];
  let count=0;
  for(const [state,activity] of physical)for(const status of statuses)for(const connected of [true,false,null])for(const fresh of [true,false])for(const guard of guards)for(const pending of ['none','START','PARK','server','water']){
    const s=ready(),local={inFlight:{}};
    Object.assign(s.mower,{state,activity,connected,telemetryFresh:fresh});s.manualControl.status=status;
    if(guard==='water'){s.manualControl.confirmations={waterChoiceRequired:true};s.irrigation.safety.active_zone_count=1;s.irrigation.safety.clear_now=false;}
    if(['training','game'].includes(guard)){s.occupancy.current={source:guard==='game'?'match':'training'};s.manualControl.confirmations={occupancyRequired:true};}
    if(guard==='drying'){s.manualControl.confirmations={dryingRequired:true};s.coordination.dryUntil='2026-09-09T12:00:00Z';}
    if(guard==='unconfirmed')s.automation.mowerStartOutcomeUnconfirmed=true;
    if(guard==='readOnly')s.controlsAvailable=false;
    if(guard==='permissionDenied')Object.assign(s.manualControl,{canStart:false,canPark:false,canResume:false});
    if(['START','PARK'].includes(pending)){local.inFlight.MANUAL_CONTROL='request';local.manualControlInFlightOperation=pending;}
    if(pending==='server')s.manualControl.requestStatus='PENDING';
    if(pending==='water')local.inFlight.START_IRRIGATION='water-request';
    const a=view.manualMowerActions(s,local),label=JSON.stringify({state,activity,status,connected,fresh,guard,pending});
    if(['STOPPED','OFF'].includes(state)||connected!==true||guard==='permissionDenied')for(const key of keys)assert.equal(a[key],false,label+' '+key);
    if(pending!=='none'||guard==='unconfirmed'||guard==='readOnly'||status==='PREPARED'||status==='UNKNOWN')assert.equal(a.manualStart,false,label);
    if(['ERROR','FATAL_ERROR','UNKNOWN'].includes(state))assert.equal(a.manualStart,false,label);
    if(['MOWING','LEAVING','GOING_HOME'].includes(activity)&&guard!=='water')assert.equal(a.manualStart,false,label);
    if(a.manualStart){assert.equal(s.manualControl.canStart,true);assert.equal(a.startLabel,guard==='water'?'Mähen oder bewässern':'Mäher starten');}
    assert.equal(a.manualStart,a.husqvarnaStart,label);assert.equal(a.manualPark,a.husqvarnaPark,label);
    if(a.manualPark)assert.equal(s.manualControl.canPark,true,label);
    if(a.manualResume)assert.equal(s.manualControl.canResume,true,label);
    count++;
  }
  assert.equal(count,18480);
});

test('No irrigation start when mower cannot return: disconnected, physical STOP or active fault',()=>{
  for(const changes of [{connected:false},{connected:null},{state:'STOPPED'},{state:'OFF'},{state:'ERROR',errorCode:0},{state:'FATAL_ERROR'},{state:'UNKNOWN'},{errorCode:93,errorActive:true}]){
    const s=ready();Object.assign(s.mower,changes);const a=view.irrigationActionContext(s,{});
    assert.equal(a.showStart,false,JSON.stringify(changes));assert.equal(a.showZoneStart,false);
  }
});

test('Protective park remains available on blocked pitches and unconfirmed starts; no duplicate park',()=>{
  const s=ready();s.mower.activity='MOWING';s.controlsAvailable=false;s.automation.mowerStartOutcomeUnconfirmed=true;
  s.occupancy.current={source:'training'};s.irrigation.safety.active_zone_count=1;
  assert.equal(view.manualMowerActions(s,{inFlight:{MANUAL_CONTROL:'1'},manualControlInFlightOperation:'START'}).manualPark,true);
  assert.equal(view.manualMowerActions(s,{inFlight:{MANUAL_CONTROL:'2'},manualControlInFlightOperation:'PARK'}).manualPark,false);
  s.manualControl.status='MANUAL_PARKED';s.manualControl.requestStatus='CONFIRMED';
  assert.equal(view.manualMowerActions(s,{}).manualPark,true,'new movement after a confirmed manual park still permits protective return');
  s.mower.activity='GOING_HOME';assert.equal(view.manualMowerActions(s,{}).manualPark,false);
});

test('Return/charging/held station use clear wording and release restores the available actions',()=>{
  const s=ready();
  for(const activity of ['CHARGING','PARKED_IN_CS','GOING_HOME']){
    s.mower.activity=activity;
    const a=view.manualMowerActions(s,{});assert.equal(a.parkLabel,'In Station lassen');assert.match(a.parkQuestion,/bis du ihn wieder freigibst/);
  }
  s.mower.activity='PARKED_IN_CS';s.manualControl.status='MANUAL_PARKED';
  assert.equal(view.manualMowerActions(s,{}).manualPark,false);assert.equal(view.manualMowerActions(s,{}).manualResume,true);
  s.manualControl.status='AUTOMATIC';assert.equal(view.manualMowerActions(s,{}).manualPark,true);assert.equal(view.manualMowerActions(s,{}).manualResume,false);
  s.manualControl.status='PREPARED';s.manualControl.source='HUSQVARNA';assert.equal(view.manualMowerActions(s,{}).manualStart,false);assert.equal(view.manualMowerActions(s,{}).manualResume,true);
});

test('Manual permission still requires occupancy/drying/water confirmation; unknown water never invents authority',()=>{
  const s=ready();s.manualControl.confirmations={occupancyRequired:true,dryingRequired:true,waterChoiceRequired:true};
  assert.equal(view.manualMowerActions(s,{}).startLabel,'Mähen oder bewässern');
  assert.deepEqual(view.manualControlView(s).occupancyRequired,true);assert.equal(view.manualControlView(s).dryingRequired,true);
  s.manualControl.canStart=false;s.irrigation.safety.fresh=false;assert.equal(view.manualMowerActions(s,{}).manualStart,false);
});

test('528 irrigation combinations: stop whitelist, current zone, data quality and separate capabilities',()=>{
  let count=0;
  for(const phase of [null,'PLANNED','SUSPENDING','READY','START_RESERVED','RUNNING','STOPPING','COMPLETE_HOLD','FAILED','UNKNOWN','NEW_PHASE'])for(const fresh of [true,false])for(const active of [0,1,2])for(const imminent of [0,1])for(const capability of [true,false])for(const busy of [true,false]){
    const s=ready();s.automation.irrigationPhase=phase;s.irrigation.safety={available:true,fresh,clear_now:active===0,active_zone_count:active,imminent_zone_count:imminent};
    s.actionCapabilities={STOP_IRRIGATION_NOW:{available:capability},STOP_IRRIGATION_AFTER_ZONE:{available:capability}};
    const local={inFlight:busy?{STOP_IRRIGATION_NOW:'request'}:{}};const a=view.irrigationActionContext(s,local);
    if(active||imminent||!fresh||phase){assert.equal(a.showStart,false);assert.equal(a.showZoneStart,false);}
    assert.equal(a.showStop,capability&&!busy&&['PLANNED','SUSPENDING','READY','START_RESERVED','RUNNING','STOPPING'].includes(phase));
    assert.equal(a.showStopAfterZone,capability&&!busy&&phase==='RUNNING'&&fresh&&active===1);
    count++;
  }
  assert.equal(count,528);
  const s=ready();s.actionCapabilities={START_IRRIGATION:{available:false},START_IRRIGATION_ZONE:{available:true}};
  assert.equal(view.irrigationActionContext(s,{}).showStart,false);assert.equal(view.irrigationActionContext(s,{}).showZoneStart,true);
  s.irrigation.zones=[{zone:1,running:true}];assert.equal(view.irrigationActionContext(s,{}).showZoneStart,false);
  s.irrigation.zones=[];assert.equal(view.irrigationActionContext(s,{inFlight:{START_IRRIGATION_ZONE:'request'}}).showZoneStart,false);
});

function mockElements(ids){return Object.fromEntries(ids.map(id=>[id,{disabled:false,hidden:false,classList:{toggle(name,hide){this[name]=hide}},querySelector(){return {textContent:''}}}]))}
test('Water buttons recover after pending requests and every displayed action has its own capability',()=>{
  const ids=['irrigation-stop','irrigation-start-all','stop-now','stop-after-zone'],elements=mockElements(ids),zone=mockElements(['zone']).zone,local={inFlight:{}};
  const doc={getElementById:id=>elements[id],querySelectorAll:selector=>selector==='.zone-start'?[zone]:[]};
  const render=new Function('document','state','irrigationActionContext','pfDecorate',sourceOf('pfWaterActions')+';return pfWaterActions')(doc,local,view.irrigationActionContext,()=>{});
  const s=ready();render(s);assert.equal(elements['irrigation-start-all'].disabled,false);
  local.inFlight.START_IRRIGATION='pending';render(s);assert.equal(elements['irrigation-start-all'].classList.hidden,true);
  delete local.inFlight.START_IRRIGATION;render(s);assert.equal(elements['irrigation-start-all'].classList.hidden,false);
  s.automation.irrigationPhase='RUNNING';s.irrigation.safety.active_zone_count=1;render(s);assert.equal(elements['stop-after-zone'].disabled,false);
  s.irrigation.safety.active_zone_count=0;render(s);assert.equal(elements['stop-after-zone'].classList.hidden,true);assert.equal(elements['stop-now'].disabled,false);
});

test('Plan buttons recover after permission/phase/pending changes, including already open custom form',()=>{
  const ids=['plan-skip','plan-pause-open','plan-custom-open','plan-resume','plan-pause','plan-save-custom'],elements=mockElements(ids),local={inFlight:{}};
  const render=new Function('document','state',...Object.keys(view),sourceOf('pfPlanActions')+';return pfPlanActions')({getElementById:id=>elements[id]},local,...Object.values(view));
  const s=ready();s.irrigationSchedule.nextRun.zones=Array.from({length:7},(_,i)=>({zone:i+1}));
  render(s);assert.equal(elements['plan-save-custom'].disabled,false);
  for(const phase of ['RUNNING','COMPLETE_HOLD','UNKNOWN']){s.automation.irrigationPhase=phase;render(s);assert.equal(elements['plan-save-custom'].disabled,true);}
  s.automation.irrigationPhase=null;s.actionCapabilities={CUSTOMIZE_NEXT_IRRIGATION:{available:false}};render(s);assert.equal(elements['plan-custom-open'].disabled,true);assert.equal(elements['plan-skip'].disabled,false);
  s.actionCapabilities.CUSTOMIZE_NEXT_IRRIGATION.available=true;render(s);assert.equal(elements['plan-save-custom'].disabled,false);
  local.inFlight.SKIP_NEXT_IRRIGATION='request';render(s);assert.equal(elements['plan-pause'].disabled,true);
  delete local.inFlight.SKIP_NEXT_IRRIGATION;render(s);assert.equal(elements['plan-pause'].disabled,false);
  s.irrigationSchedule.available=false;render(s);for(const id of ids)assert.equal(elements[id].disabled,true);
});
