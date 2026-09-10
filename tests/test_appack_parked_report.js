const assert = require('node:assert/strict');
const test = require('node:test');
const {viewModel, snapshot} = require('./helpers/platzwart_template');
const view = viewModel();

function parked() {
  const s = snapshot();
  s.generatedAt = '2026-09-10T18:31:00Z';
  s.overall.code = 'OCCUPANCY_OR_IRRIGATION_HOLD';
  s.protection = {automaticStartEnabled:true, protectiveParkingEnabled:true};
  s.mower = {...s.mower, activity:'PARKED_IN_CS',state:'RESTRICTED',mode:'HOME',
    telemetryFresh:false,statusAgeSeconds:720,statusTimestamp:Date.parse('2026-09-10T18:19:00Z'),
    batteryPercent:100,restartBatteryPercent:90};
  s.automation = {parkedByAutomation:true,continuousMowingOwned:false,irrigationPhase:null};
  s.manualControl = {enabled:true,status:'AUTOMATIC',canStart:false,canPark:true,canResume:false};
  s.coordination = {blockers:[{code:'MOWER_TELEMETRY'},{code:'OCCUPANCY',until:'2026-09-10T20:00:00Z'}],
    dryUntil:'2026-09-10T16:25:00Z',releaseNotBefore:'2026-09-10T16:25:00Z'};
  s.occupancy = {current:{start:'2026-09-10T14:30:00Z',end:'2026-09-10T20:00:00Z',source:'training'},
    safeWindows:[{start:'2026-09-10T20:00:00Z',command_deadline:'2026-09-11T01:56:00Z',minimum_mowing_minutes:30}]};
  return s;
}

test('Fotoablauf: Trainingsende und bedingter Start bleiben sichtbar, Freigaben bleiben gesperrt', () => {
  const s=parked(), before=JSON.stringify(s);
  assert.equal(view.dashboardMessage(s).title,'Platz ist belegt');
  assert.match(view.dashboardMessage(s).text,/22:00 Uhr/);
  assert.equal(view.nextMowerStart(s),'Heute, 22:00 Uhr');
  assert.equal(view.nextStartInfo(s).awaitingMowerReport,true);
  assert.equal(view.mowerTelemetryFresh(s),false);
  assert.equal(view.effectiveMowerActions(s).showStart,false);
  assert.equal(view.irrigationActions(s).showStart,false);
  assert.equal(JSON.stringify(s),before,'display must not mutate any safety or manual permission');
  s.generatedAt='2026-09-10T17:28:00Z';
  s.mower.statusTimestamp=Date.parse('2026-09-10T17:17:00Z');s.mower.statusAgeSeconds=660;
  assert.equal(view.nextMowerStart(s),'Heute, 22:00 Uhr');
});

test('Anzeigegrenze ist beschränkt und ersetzt nicht die 180-Sekunden-Sicherheitsgrenze', () => {
  for(const [age, accepted] of [[180,false],[181,true],[930,true],[1200,true],[1201,false],[-1,false]]) {
    const s=parked();s.mower.statusAgeSeconds=age;s.mower.statusTimestamp=Date.parse(s.generatedAt)-age*1000;
    assert.equal(view.parkedReportPending(s),accepted,`age ${age}`);
    assert.equal(view.nextStartInfo(s).at!==null,accepted,`plan at age ${age}`);
    assert.equal(view.effectiveMowerActions(s).showStart,false);
  }
});

test('Keine Plan-Ausnahme bei fehlenden, widersprüchlichen oder unsicheren Angaben', () => {
  const cases=[
    s=>s.mower.connected=false,s=>s.mower.mode='MAIN_AREA',s=>s.mower.activity='MOWING',
    s=>s.mower.activity='LEAVING',s=>s.mower.activity='CHARGING',s=>s.mower.state='STOPPED',
    s=>s.mower.errorCode=93,s=>s.mower.statusTimestamp=null,s=>s.mower.statusTimestamp='invalid',
    s=>s.mower.statusAgeSeconds=12,s=>s.mower.statusAgeSeconds=null,s=>s.mower.statusAgeSeconds=NaN,
    s=>s.controlsAvailable=false,s=>s.deviceControlsAvailable=false,s=>s.protection.automaticStartEnabled=false,
    s=>s.operationMode='MANUAL',s=>s.mower.operationMode='MANUAL',s=>s.manualControl.status='UNKNOWN',
    s=>s.manualControl.status='MANUAL_PARKED',s=>s.automation.parkedByAutomation=false,
    s=>s.automation.mowerStartOutcomeUnconfirmed=true,s=>s.automation.pendingAction='PARK_MOWER',
    s=>s.automation.irrigationPhase='RUNNING',s=>s.irrigation.safety.available=false,
    s=>s.irrigation.safety.fresh=false,s=>s.irrigation.safety.clear_now=false,
    s=>s.irrigation.safety.active_zone_count=1,s=>s.occupancy.current=null,
    s=>s.occupancy.current.source='manual',s=>s.occupancy.current.end=s.generatedAt,
    s=>s.occupancy.current.start='2026-09-10T19:00:00Z',s=>s.occupancy.current.end='invalid',
    s=>s.occupancy.safeWindows=[],s=>s.mower.batteryPercent=40,
    s=>s.coordination.blockers=[{code:'MOWER_TELEMETRY'}],
    s=>s.coordination.blockers[1].until='2026-09-10T21:00:00Z',
    s=>s.coordination.blockers[1].until=null,
    ...[null,'',undefined,'0',false,NaN].flatMap(value=>[s=>s.mower.errorCode=value,s=>s.irrigation.safety.active_zone_count=value]),
    s=>s.irrigationSchedule.override={kind:'PAUSE',status:'APPLYING'},
    ...['CONTROLLER_STALE','DATA_QUALITY','IRRIGATION_TELEMETRY','START_UNCONFIRMED','MANUAL_STOP','IRRIGATION_ACTIVE_OR_DUE','NEW_BLOCKER'].map(code=>s=>s.coordination.blockers.push({code})),
    ...['START_MOWING','PARK_MOWER'].flatMap(action=>['PENDING','RESERVED','SENT_UNCONFIRMED','UNKNOWN'].map(status=>s=>s.operatorCommands={[action]:{status}})),
  ];
  cases.forEach((change,index)=>{const s=parked();change(s);assert.equal(view.nextStartInfo(s).at,null,`case ${index}`)});
});

test('Frische Meldung ersetzt den Vorbehalt; keine Startzusage nach Ende der Belegung aus alter Meldung', () => {
  const s=parked();s.mower.telemetryFresh=true;s.mower.statusAgeSeconds=0;s.mower.statusTimestamp=Date.parse(s.generatedAt);
  s.coordination.blockers=[{code:'OCCUPANCY'}];
  assert.equal(view.nextStartInfo(s).awaitingMowerReport,false);
  assert.equal(view.nextMowerStart(s),'Heute, 22:00 Uhr');
  s.mower.telemetryFresh=false;s.coordination.blockers.push({code:'MOWER_TELEMETRY'});
  s.generatedAt='2026-09-10T20:00:00Z';s.mower.statusTimestamp=Date.parse(s.generatedAt)-720000;s.mower.statusAgeSeconds=720;
  assert.equal(view.nextMowerStart(s),'Noch offen');
});

test('Aktuelles Wasser wird trotz alter Mähermeldung als Konflikt angezeigt', () => {
  const s=parked();s.irrigation.safety.active_zone_count=1;s.mower.activity='MOWING';
  assert.equal(view.dashboardMessage(s).title,'Bewässerung läuft');
  assert.match(view.dashboardMessage(s).text,/Mäher parken/);
  s.mower.activity='PARKED_IN_CS';
  assert.equal(view.dashboardMessage(s).title,'Bewässerung bitte beenden');
});

test('Bedingter Plan benutzt auch über Mitternacht und Zeitumstellung nur absolute Belegungsfenster', () => {
  for(const [now,end,expected] of [
    ['2026-09-10T21:50:00Z','2026-09-10T22:10:00Z','Fr., 11.09.26, 00:10 Uhr'],
    ['2026-10-25T00:50:00Z','2026-10-25T01:10:00Z','Heute, 02:10 Uhr'],
    ['2027-03-28T00:50:00Z','2027-03-28T01:10:00Z','Heute, 03:10 Uhr']]) {
    const s=parked();s.generatedAt=now;s.mower.statusTimestamp=Date.parse(now)-720000;
    s.occupancy.current={source:'training',start:new Date(Date.parse(now)-3600000).toISOString(),end};
    s.coordination.blockers[1].until=end;
    s.occupancy.safeWindows=[{start:end,command_deadline:new Date(Date.parse(end)+3600000).toISOString(),minimum_mowing_minutes:30}];
    assert.equal(view.nextStartInfo(s).at,end.replace('Z','.000Z'));
    assert.equal(view.nextMowerStart(s),expected);
  }
});
