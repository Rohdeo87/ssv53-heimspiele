const assert=require('node:assert/strict');
const test=require('node:test');
const {sourceOf,viewModel,snapshot}=require('./helpers/platzwart_template');
const view=viewModel();
const manual=new Function(sourceOf('manualControlView')+';return manualControlView')();
const nextMoment=new Function('manualControlView',...Object.keys(view),sourceOf('pfNextMoment')+';return pfNextMoment')(manual,...Object.values(view));

// Reconstruction from controller observations of 11 September, not a captured app response.
function returning(){
  const s=snapshot();
  s.generatedAt='2026-09-11T14:19:00Z';
  s.overall.code='MOWER_LOW_BATTERY_HOME_ALLOWED';
  s.mower={...s.mower,activity:'GOING_HOME',state:'RESTRICTED',mode:'MAIN_AREA',batteryPercent:30,restartBatteryPercent:90};
  s.automation={continuousMowingOwned:true,irrigationPhase:null};
  s.protection={automaticStartEnabled:true};
  s.manualControl={enabled:true,status:'AUTOMATIC',canStart:true,canPark:true};
  s.coordination={telemetryConfirmed:true,dryUntil:'2026-09-11T07:40:00Z',releaseNotBefore:'2026-09-11T07:40:00Z',dryingReason:'IRRIGATION_END',blockers:[]};
  s.occupancy={available:true,current:null,parking:null,next:{title:'Training C',source:'training',start:'2026-09-11T16:30:00+02:00',end:'2026-09-11T19:00:00+02:00'},safeWindows:[{start:'2026-09-11T19:00:00+02:00',command_deadline:'2026-09-12T03:50:00+02:00',minimum_mowing_minutes:30}]};
  return s;
}

test('Heimfahrt, Station und Trainingssperre zeigen dasselbe früheste Mähfenster',()=>{
  const s=returning();
  for(const [at,activity,parking,title] of [
    ['14:19','GOING_HOME',false,'Mäher fährt zur Station'],
    ['14:19','PARKED_IN_CS',false,'Platz wird bald belegt'],
    ['14:20','PARKED_IN_CS',true,'Platz wird bald belegt'],
    ['14:21','CHARGING',true,'Platz wird bald belegt'],
  ]){
    s.generatedAt='2026-09-11T'+at+':00Z';s.mower.activity=activity;s.mower.batteryPercent=27;
    s.occupancy.parking=parking?s.occupancy.next:null;
    assert.equal(view.dashboardMessage(s).title,title);
    assert.match(view.dashboardMessage(s).text,/19:00 Uhr/);
    assert.equal(view.nextStartInfo(s).at,null,'Strict start calculation still waits for readiness');
    const moment=nextMoment(s);
    assert.equal(moment.at,'2026-09-11T17:00:00.000Z');
    assert.equal(moment.label,'Frühester Mähstart');
    assert.match(moment.note,/Akku muss bereit sein/);
  }
  s.generatedAt='2026-09-11T14:31:00Z';s.occupancy.current=s.occupancy.next;
  assert.equal(view.dashboardMessage(s).title,'Platz ist belegt');
  assert.equal(nextMoment(s).at,'2026-09-11T17:00:00.000Z');
});

test('Heimfahrt bleibt sichtbar, eine tatsächliche Trockenfrist bleibt wirksam',()=>{
  const s=returning();s.coordination.dryUntil='2026-09-11T18:00:00Z';s.coordination.releaseNotBefore=s.coordination.dryUntil;
  s.coordination.blockers=[{code:'DRYING_OR_CONFIRMATION'}];
  assert.equal(view.dashboardMessage(s).title,'Mäher fährt zur Station');
  assert.equal(nextMoment(s).at,'2026-09-11T18:00:00.000Z');
  s.occupancy={available:true};s.mower.activity='PARKED_IN_CS';
  assert.equal(view.dashboardMessage(s).title,'Rasen trocknet');
});

test('Niedriger Akku in der Station behauptet kein bestätigtes Laden',()=>{
  const s=returning();s.mower.activity='PARKED_IN_CS';s.occupancy={available:true};
  assert.equal(view.dashboardMessage(s).title,'Mäher in der Station');
  assert.match(view.dashboardMessage(s).text,/Akku 30 %/);
  assert.equal(view.dashboardMessage(s).icon,'House');
});

for(const [name,change] of [
  ['Manuelle Stoppsperre',s=>{s.coordination.blockers=[{code:'MANUAL_STOP'}]}],
  ['Manuell geparkt',s=>{s.manualControl.status='MANUAL_PARKED'}],
  ['Unbestätigte manuelle Aktion',s=>{s.manualControl.status='UNKNOWN'}],
  ['Automatik aus',s=>{s.protection.automaticStartEnabled=false}],
  ['Alte Mähermeldung',s=>{s.mower.telemetryFresh=false}],
  ['Mäherstörung',s=>{s.mower.errorCode=93;s.mower.state='ERROR'}],
  ['Start unbestätigt',s=>{s.automation.mowerStartOutcomeUnconfirmed=true}],
  ['Befehl offen',s=>{s.automation.pendingAction='PARK_MOWER'}],
  ['Start nur im Auftragsjournal offen',s=>{s.operatorCommands={START_MOWING:{status:'SENT_UNCONFIRMED'}}}],
  ['Belegung fehlt',s=>{s.occupancy.available=false}],
  ['Bewässerung läuft',s=>{s.irrigation.safety.active_zone_count=1;s.irrigation.safety.clear_now=false}],
  ['Bewässerung unbekannt',s=>{s.irrigation.safety.fresh=false}],
  ['Wassermeldung nicht bestätigt',s=>{s.coordination.telemetryConfirmed=false}],
  ['Wasserlücke unklar',s=>{s.coordination.dryingReason='POSSIBLE_IRRIGATION_DURING_GAP'}],
  ['Keine sicheren Fenster',s=>{s.occupancy.safeWindows=[]}],
  ['Fenster zu kurz',s=>{s.occupancy.safeWindows[0].command_deadline='2026-09-11T17:20:00Z'}],
  ['Sperrende ungültig',s=>{s.occupancy.next.end='unknown'}],
  ['Unbekannte Sperre',s=>{s.coordination.blockers=[{code:'UNKNOWN'}]}],
])test('Frühester Start bleibt offen: '+name,()=>{const s=returning();change(s);assert.equal(nextMoment(s).at,null)});

test('Eine Belegungsendzeit allein ersetzt kein freies Fenster und keine Ladeprognose',()=>{
  const s=returning();s.occupancy.safeWindows[0].start='2026-09-11T21:00:00+02:00';
  assert.equal(nextMoment(s).at,'2026-09-11T19:00:00.000Z');
  s.mower.activity='CHARGING';s.coordination.chargingEndEstimate={at:'2026-09-11T21:30:00+02:00',estimated:true};
  assert.equal(nextMoment(s).at,'2026-09-11T19:30:00.000Z');
  assert.equal(nextMoment(s).label,'Nächster Mähstart');
});

test('Zeitnahe Belegung wird nicht vorgezogen, wenn vorher ein nutzbares Fenster bleibt',()=>{
  const s=returning();s.generatedAt='2026-09-11T14:00:00Z';
  s.occupancy.safeWindows.unshift({start:s.generatedAt,command_deadline:'2026-09-11T14:30:00Z',minimum_mowing_minutes:30});
  assert.equal(view.displayOccupancyBlock(s),null);
  assert.equal(nextMoment(s).at,null);
});

test('Fehlender Akkustand in der Station erzeugt keine Uhrzeit',()=>{
  const s=returning();s.mower.activity='PARKED_IN_CS';
  for(const battery of [null,NaN,-1,101,'27']){s.mower.batteryPercent=battery;assert.equal(nextMoment(s).at,null)}
});

test('Heimfahrt während einer Belegung lässt die Belegungswarnung sichtbar',()=>{
  const s=returning();s.generatedAt='2026-09-11T14:31:00Z';s.occupancy.current=s.occupancy.next;
  assert.equal(view.dashboardMessage(s).title,'Platz ist belegt');
  assert.match(view.dashboardMessage(s).text,/fährt noch zur Station/);
});

module.exports={returning};
