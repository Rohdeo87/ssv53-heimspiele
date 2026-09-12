const test = require('node:test');
const assert = require('node:assert/strict');
const {viewModel, snapshot} = require('./helpers/platzwart_template');
const view = viewModel();

function stopped(fresh=true) {
  const s=snapshot();
  s.generatedAt='2026-09-12T09:01:00Z';
  s.mower={state:'STOPPED',activity:'NOT_APPLICABLE',mode:'HOME',connected:true,errorCode:0,
    telemetryFresh:fresh,statusTimestamp:1789203520127};
  s.automation={parkedByAutomation:true,irrigationPhase:'COMPLETE_HOLD'};
  s.coordination={dryUntil:'2026-09-11T07:40:00Z',telemetryConfirmed:true,
    blockers:[{code:'MANUAL_STOP'},...(fresh?[]:[{code:'MOWER_TELEMETRY'}])]};
  s.manualControl={enabled:true,status:'AUTOMATIC',canStart:false,canPark:true,stationConfirmed:false};
  s.occupancy.current={source:'match',start:'2026-09-12T08:00:00Z',end:'2026-09-12T11:45:00Z'};
  return s;
}

test('STOP-Meldung nennt den Gerätestopp statt abgeschlossener Bewässerung',()=>{
  const s=stopped(),msg=view.dashboardMessage(s);
  assert.equal(msg.title,'Manuell gestoppt');
  assert.equal(msg.text,'Zum Fortsetzen bitte den Mäher vor Ort freigeben.');
  assert.doesNotMatch(msg.text,/Bewässerung|trocknet|abwarten/);
  assert.equal(msg.icon,'Square');
  assert.equal(view.nextStartInfo(s,true).manualStop,true);
  assert.equal(view.effectiveMowerActions(s).showStart,false);
  assert.equal(s.manualControl.canStart,false);
});

test('Auch nach Ablauf der drei Minuten bleibt der zuletzt gemeldete STOP verständlich',()=>{
  const s=stopped(false);s.generatedAt='2026-09-12T09:02:00Z';
  assert.equal(view.dashboardMessage(s).title,'Manuell gestoppt');
  assert.doesNotMatch(view.dashboardMessage(s).text,/Letzte Meldung|älter|10:58/);
  assert.equal(view.nextStartInfo(s,true).manualStop,true);
  assert.equal(view.effectiveMowerActions(s).showStart,false);
  s.mower.telemetryFresh=true;s.mower.state='RESTRICTED';s.mower.activity='PARKED_IN_CS';
  s.coordination.blockers=[];
  assert.equal(view.mowerStopNotice(s),null);
  assert.equal(view.dashboardMessage(s).title,'Platz ist belegt');
});

test('Laufendes Wasser und unsichere Starts werden nicht vom STOP-Hinweis verdeckt',()=>{
  for(const fresh of [true,false]){
    const s=stopped(fresh);
    s.irrigation.intent={source:'MANUAL_OPERATOR',verified:true,controllerManaged:true,automaticWindowApplies:false};
    s.irrigation.safety.active_zone_count=1;s.irrigation.safety.clear_now=false;
    assert.match(view.dashboardMessage(s).title,/^Bewässerung (läuft|bitte beenden)$/);
    s.automation.mowerStartOutcomeUnconfirmed=true;
    assert.equal(view.dashboardMessage(s).title,'Mäherstart nicht bestätigt');
    assert.notEqual(view.nextStartInfo(s,true).manualStop,true);
  }
  const unknown=stopped(false);unknown.automation.irrigationPhase='RUNNING';unknown.irrigation.safety.fresh=false;
  assert.equal(view.dashboardMessage(unknown).title,'Bewässerung nicht bestätigt');
});

test('Abgeschlossener Wasserlauf erfindet beim manuellen Fortsetzen keine Trockenfrist',()=>{
  const s=stopped();s.mower.state='PAUSED';s.manualControl.canStart=true;
  assert.match(view.simpleStatus(s),/„Mäher starten“/);
  assert.doesNotMatch(view.simpleStatus(s),/Bewässerung|trocknet|abwarten/);
  s.manualControl.canStart=false;
  assert.match(view.simpleStatus(s),/am Mäher nachsehen/);
  s.coordination.dryUntil='2026-09-12T09:30:00Z';
  assert.match(view.simpleStatus(s),/trocknet bis Heute, 11:30 Uhr/);
  s.coordination.dryUntil=null;s.coordination.blockers=[{code:'DRYING_OR_CONFIRMATION'}];
  assert.match(view.simpleStatus(s),/Bewässerungsdaten werden noch geprüft/);
  assert.doesNotMatch(view.simpleStatus(s),/trocknet/);
});

test('Ältere Offline- und Fehlermeldungen werden nicht als bestätigter sicherer Stopp verkauft',()=>{
  const s=stopped(false);s.mower.connected=false;
  assert.equal(view.dashboardMessage(s).title,'Mäher nicht erreichbar');
  assert.equal(view.nextStartInfo(s,true).text,'Noch offen');
  s.mower.connected=true;s.mower.state='ERROR';s.mower.errorCode=93;s.mower.errorActive=true;
  assert.equal(view.dashboardMessage(s).title,'Keine genaue Satellitenposition');
  s.mower.state='OFF';s.mower.errorCode=0;s.mower.errorActive=false;
  assert.equal(view.dashboardMessage(s).title,'Mäher ausgeschaltet');
});

test('Unveränderter STOP bleibt bei neun oder dreißig Minuten klar, echte Ausfälle haben Vorrang',()=>{
  const s=stopped(false);
  for(const minutes of [9,30]){
    s.mower.statusAgeSeconds=minutes*60;
    s.mower.statusTimestamp=new Date(s.generatedAt).getTime()-minutes*60000;
    assert.equal(view.dashboardMessage(s).title,'Manuell gestoppt');
    assert.equal(view.mowerTelemetryFresh(s),false);
    assert.equal(view.effectiveMowerActions(s).showStart,false);
    assert.equal(view.stationConfirmed(s),false);
  }
  s.coordination.blockers.push({code:'CONTROLLER_STALE'});
  assert.equal(view.dashboardMessage(s).title,'Automatik antwortet nicht');
  s.controlsAvailable=false;s.dataQuality={code:'CONFIG_STALE'};
  assert.equal(view.dashboardMessage(s).title,'Belegungsplan nicht aktuell');
});
