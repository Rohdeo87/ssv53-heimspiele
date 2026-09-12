const test=require('node:test');
const assert=require('node:assert/strict');
const {sourceOf,viewModel,snapshot}=require('./helpers/platzwart_template');
const view=viewModel();
const explain=new Function(...Object.keys(view),sourceOf('pfWaterStartExplanation')+';return pfWaterStartExplanation')(...Object.values(view));
test('The reported stopped mower + completed water hold explains the missing start without inventing drying',()=>{
 const s=snapshot();s.mower.state='STOPPED';s.mower.telemetryFresh=false;s.coordination.dryUntil=null;s.coordination.blockers=[];
 assert.match(explain(s,{}),/Station ist noch nicht bestätigt/);assert.match(explain(s,{}),/darf gestoppt bleiben/);
 assert.doesNotMatch(explain(s,{}),/trockn|03:30|08:00|vor Ort freigeben/);
 assert.equal(view.irrigationActionContext(s,{}).showStart,false);
 s.mower.state='RESTRICTED';s.mower.activity='PARKED_IN_CS';s.mower.telemetryFresh=true;
 assert.match(explain(s,{}),/letzte Durchlauf ist noch nicht freigegeben/);
 s.automation.irrigationPhase=null;assert.equal(explain(s,{}),'');
});
test('Unavailable data, waiting requests, per-zone-only permission and active water have specific explanations',()=>{
 const s=snapshot();s.automation={};s.coordination.blockers=[];
 s.irrigation.safety.fresh=false;assert.match(explain(s,{}),/aktuelle Rückmeldung/);
 s.irrigation.safety.fresh=true;assert.match(explain(s,{inFlight:{START_IRRIGATION:'request'}}),/Anfrage bestätigt/);
 s.actionCapabilities={START_IRRIGATION:{available:false},START_IRRIGATION_ZONE:{available:true}};
 assert.match(explain(s,{}),/nur einzelne Zonen/);
 s.automation.irrigationPhase='RUNNING';s.irrigation.safety.active_zone_count=1;assert.equal(explain(s,{}),'');
 s.mower.state='STOPPED';assert.equal(explain(s,{}),'');
});
test('Water and zone explanations are removed after release without leaving stale text',()=>{
 const s=snapshot(),elements={};for(const id of ['pf-water-start-note','pf-zone-start-note','irrigation-stop','irrigation-start-all','stop-now','stop-after-zone'])elements[id]={hidden:false,disabled:false,textContent:'',classList:{toggle(){}}};
 const render=new Function('irrigationActionContext','pfWaterStartExplanation','state','document','pfDecorate',sourceOf('pfWaterActions')+';return pfWaterActions')(view.irrigationActionContext,explain,{}, {getElementById:id=>elements[id],querySelectorAll:()=>[]},()=>{});
 s.mower.state='STOPPED';render(s);assert.equal(elements['pf-water-start-note'].hidden,false);assert.equal(elements['pf-zone-start-note'].hidden,false);
 s.mower.state='IN_OPERATION';s.automation={};s.coordination.blockers=[];render(s);assert.equal(elements['pf-water-start-note'].hidden,true);assert.equal(elements['pf-water-start-note'].textContent,'');
});
