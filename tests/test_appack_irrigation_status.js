const test = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const {snapshot, viewModel, html} = require('./helpers/platzwart_template');
const view = viewModel();

function stale(phase = 'READY') {
  const s = snapshot();
  s.generatedAt = '2026-09-12T04:56:00Z';
  Object.assign(s.mower, {activity:'PARKED_IN_CS', telemetryFresh:false, batteryPercent:100});
  s.automation.irrigationPhase = phase;
  s.coordination.blockers = [{code:'MOWER_TELEMETRY'}, {code:'IRRIGATION_SEQUENCE'}];
  s.overall.code = 'IRRIGATION_WAIT_FOR_CONFIRMED_PARK';
  return s;
}

test('06:56-Protokoll: Vorbereitung mit alter Stationsmeldung ist kein laufendes Wasser', () => {
  const evidence = JSON.parse(fs.readFileSync(path.join(__dirname,'../docs/ui-2026-09-12/irrigation-status/controller-observations.json')));
  const row = evidence.observations.find(r=>r.at.startsWith('2026-09-12T04:56:'));
  assert.ok(row);
  const s = stale(row.automation.irrigation_phase);
  s.generatedAt = row.at;
  s.mower.activity = row.mower.activity;
  s.mower.telemetryFresh = Date.parse(row.at) - row.mower.status_timestamp_ms < 180000;
  s.irrigation.safety = row.water;
  const message = view.dashboardMessage(s);
  assert.equal(message.title, 'Bewässerung wartet');
  assert.equal(message.icon, 'Droplets');
  assert.match(message.text, /aktuelle Meldung.*prüfen, ob er in der Station ist/);
  assert.equal(view.irrigationAwaitingStart(s), true);
  assert.equal(view.irrigationActions(s).showStop, true);
  assert.equal(view.effectiveMowerActions(s).showStart, false);
  assert.equal(view.nextMowerStart(s), 'Noch offen');
});

test('Laufendes Wasser überdeckt die alte Mähermeldung; Bewegungswarnung bleibt erhalten', () => {
  const s = stale('RUNNING');
  Object.assign(s.irrigation.safety, {active_zone_count:1, clear_now:false});
  assert.equal(view.dashboardMessage(s).title, 'Bewässerung läuft');
  assert.equal(view.irrigationAwaitingStart(s), false);
  s.mower.activity = 'MOWING';
  assert.equal(view.dashboardMessage(s).tone, 'bad');
  assert.match(view.dashboardMessage(s).text, /Mäher parken/);
});

test('Zwischen Zonen wird kein Wasserfluss erfunden, auch bei frischer Mähermeldung', () => {
  const s = stale('RUNNING');
  for(const fresh of [false,true]) {
    s.mower.telemetryFresh=fresh;
    s.coordination.blockers=fresh?[]:[{code:'MOWER_TELEMETRY'}];
    assert.equal(view.dashboardMessage(s).title, 'Bewässerung macht Pause');
    assert.equal(view.irrigationAwaitingStart(s), false);
    assert.equal(view.irrigationActions(s).showStop, true);
  }
});

test('Fehlende Wasserdaten und unbestätigter Start werden nicht als laufend oder sicher aus bezeichnet', () => {
  const s = stale('RUNNING');
  for(const safety of [{available:false},{available:true,fresh:false,active_zone_count:1}]) {
    s.irrigation.safety=safety;
    assert.equal(view.dashboardMessage(s).title, 'Bewässerung nicht bestätigt');
    assert.equal(view.irrigationAwaitingStart(s), false);
    assert.equal(view.effectiveMowerActions(s).showStart, false);
  }
  const reserved=stale('START_RESERVED');
  assert.equal(view.dashboardMessage(reserved).title, 'Bewässerung nicht bestätigt');
  assert.equal(view.irrigationAwaitingStart(reserved), false);
});

test('Vorbereitung abbrechen verwendet dieselbe geschützte Stoppaktion ohne angeblich laufende Zone', () => {
  assert.match(html, /if\(irrigationAwaitingStart\(state.status\)\)\{openAction\("STOP_IRRIGATION_NOW","Bewässerung abbrechen"/);
  assert.match(html, /irrigationAwaitingStart\(s\)\?"Bewässerung abbrechen":"Bewässerung beenden"/);
  for(const phase of ['PLANNED','SUSPENDING','READY']) assert.equal(view.irrigationAwaitingStart(stale(phase)),true);
  for(const phase of ['RUNNING','START_RESERVED','STOPPING','COMPLETE_HOLD','FAILED',null]) assert.equal(view.irrigationAwaitingStart(stale(phase)),false);
  const s=stale();s.irrigation.safety.active_zone_count=1;
  assert.equal(view.irrigationAwaitingStart(s),false);
});

test('Unbestätigter Mäherstart, Störung und ausgefallene Steuerung behalten Vorrang', () => {
  for(const code of ['START_UNCONFIRMED','MOWER_ERROR','CONTROLLER_STALE']) {
    const s=stale();s.coordination.blockers.push({code});
    assert.equal(view.dashboardMessage(s).tone,'bad');
    assert.notEqual(view.dashboardMessage(s).title,'Bewässerung wartet');
  }
});
