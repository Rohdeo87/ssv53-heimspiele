const assert=require('node:assert/strict');
const test=require('node:test');
const fs=require('node:fs');
const path=require('node:path');
const {sourceOf,viewModel,snapshot}=require('./helpers/platzwart_template');
const views=viewModel();
const manual=new Function(sourceOf('manualControlView')+';return manualControlView')();
function presentation(name,state={inFlight:{}}){
  return new Function('state','manualControlView',...Object.keys(views),sourceOf(name)+';return '+name)(state,manual,...Object.values(views));
}
function status(){
  const s=snapshot();
  s.manualControl={enabled:true,status:'AUTOMATIC',canStart:true,canPark:true,canResume:true};
  return s;
}

test('Ladeuhrzeit nutzt separate Anzeigeprognose ohne eine Startzusage daraus zu machen',()=>{
  const s=status();s.coordination.chargingEndEstimate=null;
  const at=new Date(new Date(s.generatedAt).getTime()+7*60000).toISOString();
  s.coordination.chargingDisplayEstimate={at,estimated:true,displayOnly:true};
  assert.equal(presentation('pfChargingInfo')(s).at.toISOString(),at);
  assert.equal(views.chargingEnd(s),null);
});

function designPresentation(name){
  const source=fs.readFileSync(path.join(__dirname,'../ui/platzpflege/design.js'),'utf8');
  const start=source.indexOf('    function '+name+'('),end=source.indexOf('\n    function ',start+1);
  if(start<0||end<0)throw new Error('Missing design function: '+name);
  return new Function('mowerTelemetryFresh','hasActiveMowerError',source.slice(start,end)+';return '+name)(s=>s.mower&&s.mower.telemetryFresh===true,m=>m&&m.errorActive===true);
}

test('Mähfortschritt erscheint nur bei frischer verbundener MOWING-Meldung',()=>{
  const info=designPresentation('pfMowingInfo'),s=status();s.mower.activity='MOWING';s.mower.workAreaProgress=19;
  assert.deepEqual(info(s),{visible:true,percent:19});
  s.mower.connected=false;assert.equal(info(s).visible,false);
  s.mower.connected=true;s.mower.telemetryFresh=false;assert.equal(info(s).visible,false);
  s.mower.telemetryFresh=true;s.mower.activity='PARKED_IN_CS';assert.equal(info(s).visible,false);
});

test('Mähfortschritt übernimmt keine ungültigen oder alten Werte',()=>{
  const info=designPresentation('pfMowingInfo'),s=status();s.mower.activity='MOWING';
  for(const value of [null,undefined,-1,101,NaN,'19']){s.mower.workAreaProgress=value;assert.equal(info(s).percent,null);}
});

test('Aktuelles Laden mit 29 Prozent bleibt trotz allgemeiner Akkusperre sichtbar',()=>{
  const s=status();s.overall.code='MOWER_BATTERY_CHARGING';s.mower.batteryPercent=29;
  s.coordination.dryUntil=null;s.coordination.releaseNotBefore=null;s.coordination.blockers=[{code:'CHARGING'}];s.automation.irrigationPhase=null;
  s.coordination.chargingEndEstimate=null;
  assert.equal(views.dashboardMessage(s).title,'Mäher lädt');
  assert.deepEqual(presentation('pfChargingInfo')(s),{visible:true,percent:29,at:null});
  s.mower.activity='PARKED_IN_CS';
  assert.equal(presentation('pfChargingInfo')(s).visible,false);
  assert.notEqual(views.dashboardMessage(s).title,'Mäher lädt');
});

test('Ladeanzeige erfindet weder Akkustand noch Ladung aus alten Daten',()=>{
  const s=status(),info=presentation('pfChargingInfo');
  for(const value of [null,undefined,-1,101,NaN,'29']){s.mower.batteryPercent=value;assert.equal(info(s).percent,null);}
  s.mower.batteryPercent=29;s.mower.telemetryFresh=false;
  assert.equal(info(s).visible,false);
  s.mower.telemetryFresh=true;s.controlsAvailable=false;assert.equal(info(s).visible,false);
  s.controlsAvailable=true;s.mower.errorCode=93;s.mower.errorActive=true;assert.equal(info(s).visible,false);
});

test('Statusicon gehört zur Meldung, auch wenn der Mäher gleichzeitig lädt',()=>{
  const s=status(),message=views.dashboardMessage;
  assert.equal(s.mower.activity,'CHARGING');
  assert.equal(message(s).title,'Rasen trocknet');
  assert.equal(message(s).icon,'Leaf');
  s.occupancy.current={start:s.generatedAt,end:'2026-09-09T14:00:00Z'};
  assert.equal(message(s).title,'Platz ist belegt');
  assert.equal(message(s).icon,'CalendarDays');
  s.occupancy.current=null;s.coordination.dryUntil=null;s.coordination.blockers=[];
  assert.equal(message(s).title,'Mäher lädt');
  assert.equal(message(s).icon,'BatteryCharging');
  s.generatedAt='2026-09-09T03:00:00Z';s.irrigation.safety.active_zone_count=1;
  assert.equal(message(s).title,'Bewässerung läuft');
  assert.equal(message(s).icon,'Droplets');
  s.automation.mowerStartOutcomeUnconfirmed=true;
  assert.equal(message(s).title,'Mäherstart nicht bestätigt');
  assert.equal(message(s).icon,'TriangleAlert');
});

test('Design blendet unpassende Aktionen aus, ohne Parken mit Starts zu sperren',()=>{
  const visibility=presentation('pfVisibility'),s=status();
  assert.equal(visibility(s).manualStart,true);
  assert.equal(visibility(s).manualResume,false);
  s.manualControl.status='MANUAL_PARKED';
  assert.equal(visibility(s).manualPark,false);
  assert.equal(visibility(s).manualResume,true);
  s.manualControl.status='MANUAL_MOWING';s.mower.activity='MOWING';
  assert.equal(visibility(s).manualStart,false);
  assert.equal(visibility(s).manualPark,true);
  s.controlsAvailable=false;s.deviceControlsAvailable=false;s.manualControl.canStart=false;
  assert.equal(visibility(s).manualStart,false);
  assert.equal(visibility(s).manualPark,true);
  s.manualControl.canPark=false;
  assert.equal(visibility(s).manualPark,false);
});

test('Start zur Konfliktentscheidung bleibt erreichbar; laufende Anfrage bietet keinen zweiten Start',()=>{
  const s=status();s.mower.activity='MOWING';s.manualControl.confirmations={waterChoiceRequired:true};
  assert.equal(presentation('pfVisibility')(s).manualStart,true);
  const busy=presentation('pfVisibility',{inFlight:{MANUAL_CONTROL:'request-1'}});
  assert.equal(busy(s).manualStart,false);
  assert.equal(busy(s).manualPark,true);
});

test('Große Uhrzeit nutzt die Platzsperre und zeigt bei manueller Parksperre keine Startzusage',()=>{
  const moment=presentation('pfNextMoment'),s=status();
  s.manualControl.status='MANUAL_PARKED';
  assert.deepEqual(moment(s),{label:'Nächster Mähstart',at:null,text:'Du entscheidest',note:'Erst nach deiner Freigabe.'});
  s.manualControl.status='AUTOMATIC';s.mower.activity='MOWING';
  s.occupancy.upcoming=[{start:'2026-09-09T15:00:00Z',kickoff:'2026-09-09T16:00:00Z'}];
  assert.equal(moment(s).at.toISOString(),'2026-09-09T15:00:00.000Z');
  s.mower.activity='CHARGING';s.coordination.chargingEndEstimate=null;
  assert.equal(moment(s).at,null);
});
