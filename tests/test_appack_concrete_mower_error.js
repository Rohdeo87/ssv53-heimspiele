const test=require('node:test');
const assert=require('node:assert/strict');
const {viewModel,sourceOf,snapshot}=require('./helpers/platzwart_template');
const view=viewModel();
const nextMoment=new Function(...Object.keys(view),sourceOf('pfNextMoment')+';return pfNextMoment')(...Object.values(view));
function trapped(){
  const s=snapshot();
  Object.assign(s.mower,{state:'ERROR',activity:'NOT_APPLICABLE',mode:'MAIN_AREA',errorCode:9,errorActive:true,errorMessage:'Gerätefehler (Code 9)'});
  s.manualControl={enabled:true,status:'AUTOMATIC',canStart:false,canPark:true,canResume:false};
  s.coordination.blockers=[{code:'MOWER_ERROR'}];
  return s;
}
test('Live error 9 is Trapped, with a clear instruction and no ineffective controls or unknown clock',()=>{
  const s=trapped();
  assert.deepEqual(view.dashboardMessage(s),{title:'Mäher steckt fest',text:'Bitte vor Ort prüfen und Hindernisse entfernen.',tone:'bad',icon:'TriangleAlert'});
  for(const fresh of [true,false]){
    s.mower.telemetryFresh=fresh;
    assert.equal(view.dashboardMessage(s).title,'Mäher steckt fest');
    assert.equal(nextMoment(s).hidden,true);
    const a=view.manualMowerActions(s,{});
    for(const key of ['manualPark','manualStart','husqvarnaPark','husqvarnaStart'])assert.equal(a[key],false,key);
    assert.equal(view.irrigationActionContext(s,{}).showStart,false);
  }
});
test('Manufacturer codes 9 and 10 remain distinct; known errors do not expose raw backend text',()=>{
  const s=trapped();s.mower.errorCode=10;s.mower.errorMessage='raw API trace';
  assert.equal(view.dashboardMessage(s).title,'Mäher ist umgekippt');
  assert.doesNotMatch(view.dashboardMessage(s).text,/raw|API|Code/);
});
test('Unknown codes remain diagnosable without inventing a cause',()=>{
  const s=trapped();s.mower.errorCode=9876;s.mower.errorMessage='Gerätefehler (Code 9876)';
  assert.equal(view.dashboardMessage(s).title,'Mäher meldet eine Störung');
  assert.match(view.dashboardMessage(s).text,/Fehler 9876/);
  s.mower.errorMessage='Ladekontakt prüfen';assert.equal(view.dashboardMessage(s).title,'Ladekontakt prüfen');
});
test('Cleared historical code is not presented as an active fault',()=>{
  const s=trapped();Object.assign(s.mower,{state:'IN_OPERATION',activity:'CHARGING',errorActive:false});
  s.coordination={blockers:[]};s.automation={};s.overall={code:'MOWER_CHARGING'};
  assert.equal(view.dashboardMessage(s).title,'Mäher lädt');
  assert.notEqual(nextMoment(s).hidden,true);
});
test('A moving mower retains its protective park action, and running water retains stop',()=>{
  const s=trapped();s.mower.activity='MOWING';
  assert.equal(view.manualMowerActions(s,{}).manualPark,true);
  s.automation.irrigationPhase='RUNNING';s.irrigation.safety.active_zone_count=1;
  assert.equal(view.irrigationActionContext(s,{}).showStop,true);
});
