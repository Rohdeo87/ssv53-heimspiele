const assert = require("node:assert/strict");
const test = require("node:test");
const {spawnSync} = require("node:child_process");
const {html, sourceOf, viewModel, snapshot} = require("./helpers/platzwart_template");
const view = viewModel();

test("Winter-Schalter trennt heutigen Plan und bestätigte Änderung ab morgen", () => {
  const s = snapshot();
  s.trainingControl = {available:true, active:false, pending:null, effectiveAt:null, nextEffectiveAt:"2026-09-09T22:00:00Z", trainingRevision:"a".repeat(64)};
  let t = view.trainingControlView(s);
  assert.equal(t.selected, false); assert.equal(t.choice, "Aus"); assert.equal(t.disabled, false);
  assert.equal(t.current, "Heute gilt der Sommertrainingsplan."); assert.equal(t.pending, "");
  assert.equal(t.changeDate, "10.09.2026");
  s.trainingControl.pending = true; s.trainingControl.effectiveAt = "2026-09-09T22:00:00Z";
  t = view.trainingControlView(s);
  assert.equal(t.selected, true); assert.equal(t.current, "Heute gilt der Sommertrainingsplan.");
  assert.equal(t.pending, "Ab 10.09.2026: Wintertrainingsplan.");
  s.trainingControl.active = true; s.trainingControl.pending = false;
  assert.equal(view.trainingControlView(s).pending, "Ab 10.09.2026: Sommertrainingsplan.");
});

test("Fehlender Trainingsstand erlaubt keine Änderung und behauptet keinen Sommerplan", () => {
  const s=snapshot();
  assert.equal(view.trainingControlView(s).disabled, true);
  assert.equal(view.trainingControlView(s).current, "Trainingsplan fehlt. Bitte aktualisieren.");
  s.trainingControl={available:true,active:false,pending:true,effectiveAt:"invalid",nextEffectiveAt:"invalid"};
  assert.equal(view.trainingControlView(s).available, false);
  assert.match(view.friendlyError({code:"TRAINING_CONTROL_CHANGED",status:409},"action"),/aktualisieren und erneut wählen/);
  assert.match(view.friendlyError({code:"IRRIGATION_WINDOW_CANNOT_FIT",status:400},"action"),/03:30 Uhr.*08:00 Uhr/);
});

test("Unpassende Bewässerungszeit zeigt Handlung statt einer alten Startzusage", () => {
  const s=snapshot(); s.overall.code="IRRIGATION_WINDOW_CANNOT_FIT";
  assert.equal(view.dashboardMessage(s).title,"Bewässerung passt nicht mehr");
  assert.match(view.dashboardMessage(s).text,/bis 08:00 Uhr fertig/);
  assert.equal(view.nextWaterStart(s),"Bitte Plan prüfen");
  s.overall.code="IRRIGATION_OPERATING_WINDOW";
  assert.match(view.dashboardMessage(s).text,/03:30 Uhr.*08:00 Uhr/);
  assert.equal(view.nextWaterStart(s),"Noch offen");
});

test("Laufendes Wasser außerhalb der erlaubten Zeit verlangt eine klare Handlung", () => {
  const s=snapshot();s.automation.irrigationPhase="RUNNING";s.irrigation.safety.active_zone_count=1;
  for(const at of ["2026-09-09T01:29:00Z","2026-09-09T06:00:00Z"]){
    s.generatedAt=at;
    assert.equal(view.dashboardMessage(s).title,"Bewässerung bitte beenden");
    assert.match(view.dashboardMessage(s).text,/vor Ort prüfen und beenden/);
  }
  s.generatedAt="2026-09-09T01:30:00Z";
  assert.equal(view.dashboardMessage(s).title,"Bewässerung läuft");
  s.mower.activity="MOWING";
  assert.equal(view.dashboardMessage(s).title,"Mäher bitte stoppen");
  s.mower.activity="CHARGING";s.overall.code="IRRIGATION_ACTIVE_OUTSIDE_OPERATING_WINDOW";
  s.automation.irrigationPhase="FAILED";s.generatedAt="2026-09-09T06:05:00Z";
  assert.equal(view.dashboardMessage(s).title,"Bewässerung bitte beenden");
  s.irrigation.safety.active_zone_count=0;
  assert.equal(view.dashboardMessage(s).title,"Bewässerung bitte prüfen");
});

test("Veralteter Belegungsplan verspricht keinen sicheren physischen Mäherstopp", () => {
  const s = snapshot(); s.controlsAvailable = false; s.dataQuality = {code: "CONFIG_STALE"};
  assert.equal(view.activity(s.mower), "Lädt");
  s.mower.activity = "MOWING";
  const message = view.dashboardMessage(s);
  assert.equal(message.title, "Belegungsplan nicht aktuell");
  assert.match(message.text, /vor Ort prüfen/);
  assert.match(message.text, /erst nach bestätigtem Mäherstopp/);
  assert.equal(view.nextMowerStart(s), "Noch offen");
  s.dataQuality.code = "IRRIGATION_STATUS_UNAVAILABLE";
  assert.equal(view.dashboardMessage(s).title, "Bewässerungsstand fehlt");
  assert.match(view.dashboardMessage(s).text, /Keine Geräte starten/);
});

test("Startzeit berücksichtigt Laden, Wartezeit und das nächste ausreichend lange Fenster", () => {
  const s = snapshot();
  assert.equal(view.nextMowerStart(s), "Heute, 14:30 Uhr");
  assert.equal(view.calendarTime(view.chargingEnd(s), s.generatedAt), "Heute, 13:45 Uhr");
  s.occupancy.current = {start: s.generatedAt, end: "2026-09-09T12:55:00Z"};
  assert.equal(view.nextMowerStart(s), "Heute, 14:55 Uhr");
  s.occupancy.safeWindows[0].command_deadline = "2026-09-09T13:20:00Z";
  s.occupancy.safeWindows.push({start: "2026-09-09T17:31:00Z", command_deadline: "2026-09-09T18:05:00Z", minimum_mowing_minutes: 30});
  assert.equal(view.nextMowerStart(s), "Heute, 19:35 Uhr");
  s.occupancy.safeWindows[1].command_deadline = "2026-09-09T18:04:00Z";
  assert.equal(view.nextMowerStart(s), "Noch offen");
});

test("Parkvorlauf wird in der angezeigten Startzeit berücksichtigt", () => {
  const s = snapshot();
  s.occupancy.parking = {end: "2026-09-09T13:00:00Z"};
  assert.equal(view.nextMowerStart(s), "Heute, 15:00 Uhr");
  s.occupancy.parking.end = "invalid";
  assert.equal(view.nextMowerStart(s), "Noch offen");
});

test("Ohne belastbares Ladeende entsteht aus dem Akkustand keine Startuhrzeit", () => {
  for (const batteryPercent of [null, 0, 70, 90, 99, 100]) {
    const s = snapshot(); s.mower.batteryPercent = batteryPercent; s.coordination.chargingEndEstimate = null;
    assert.equal(view.chargingEnd(s), null);
    assert.equal(view.nextMowerStart(s), "Noch offen");
  }
  for (const at of ["invalid", "2026-09-09T09:59:00Z", "2026-09-09T10:00:00Z", "2026-09-11T10:00:00Z"]) {
    const s = snapshot(); s.coordination.chargingEndEstimate.at = at;
    assert.equal(view.chargingEnd(s), null);
  }
});

test("Fehlende Freigaben und unbestätigte Aktionen unterdrücken die Startprognose", () => {
  const cases = [
    s => s.controlsAvailable = false, s => s.mower.connected = false,
    s => s.mower.errorActive = true, s => s.coordination = undefined,
    s => s.automation.mowerStartOutcomeUnconfirmed = true,
    s => s.automation.pendingAction = "START_MOWING",
    s => s.irrigationSchedule.override = {kind: "PAUSE", status: "APPLYING"},
    s => s.automation.irrigationPhase = "RUNNING",
    s => s.coordination.releaseNotBefore = null,
    s => s.automation.continuousMowingOwned = false,
    s => s.occupancy.safeWindows = [],
    ...["CONTROLLER_STALE", "MOWER_TELEMETRY", "DATA_QUALITY", "IRRIGATION_TELEMETRY", "START_UNCONFIRMED", "IRRIGATION_ACTIVE_OR_DUE", "IRRIGATION_SEQUENCE", "FUTURE_UNKNOWN_BLOCK"].map(code => s => s.coordination.blockers.push({code}))
  ];
  for (const [index, change] of cases.entries()) {
    const s = snapshot();
    if (index === 2) s.mower.errorCode = 93;
    change(s);
    assert.equal(view.nextMowerStart(s), "Noch offen", `case ${index}`);
  }
});

test("Veraltete Mäherdaten sperren Start und bleiben als letzter Stand erkennbar", () => {
  const s = snapshot();
  s.mower.telemetryFresh = false;
  s.mower.statusTimestamp = "2026-09-09T09:57:00Z";
  s.mower.activity = "CHARGING";
  s.mower.displayActivity = "RESTRICTED";
  s.coordination.blockers.push({code: "MOWER_TELEMETRY"});
  assert.equal(view.dashboardMessage(s).title, "Mähermeldung ist älter");
  assert.equal(view.effectiveMowerActions(s).showStart, false);
  s.mower.activity = "MOWING";
  s.automation.irrigationPhase = "RUNNING";
  assert.equal(view.effectiveMowerActions(s).showPark, true);
  assert.equal(view.irrigationActions(s).showStop, true);
  assert.equal(view.deviceControlsOpen(s), true);
  s.deviceControlsAvailable = false;
  assert.equal(view.effectiveMowerActions(s).showStart, false);
  delete s.deviceControlsAvailable;
  assert.equal(view.deviceControlsOpen(s), false);
  assert.equal(view.effectiveMowerActions(s).showStart, false);
});

test("Trocknungsanzeige rundet nur nach oben und behandelt Mitternacht", () => {
  assert.equal(view.dryingTime("2026-09-09T20:41:51.487Z", "2026-09-09T20:00:00Z"), "Heute, 22:42 Uhr");
  assert.equal(view.dryingTime("2026-09-09T20:42:00Z", "2026-09-09T20:00:00Z"), "Heute, 22:42 Uhr");
  assert.equal(view.dryingTime("2026-09-09T21:59:59Z", "2026-09-09T20:00:00Z"), "Do., 10.09.26, 00:00 Uhr");
});

test("Manueller Stopp bleibt als Pause sichtbar, auch mit berechenbarem Ladeende", () => {
  const s = snapshot(); s.coordination.blockers.push({code: "MANUAL_STOP"});
  assert.equal(view.nextMowerStart(s), "Automatik pausiert");
  assert.equal(view.dashboardMessage(s).title, "Mäher pausiert");
  assert.match(view.dashboardMessage(s).text, /abwarten/);
  s.automation.irrigationPhase = null;
  s.overall.code = "OPERATOR_PARK_HOLD";
  assert.match(view.dashboardMessage(s).text, /„Mäher starten“/);
  s.mower.state = "PAUSED"; s.mower.activity = "NOT_APPLICABLE"; s.automation = {};
  assert.match(view.dashboardMessage(s).text, /am Mäher nachsehen/);
});

test("Fehler und Sicherheitskonflikte erhalten eine klare Handlung ohne Rohmeldungen", () => {
  const s = snapshot(); s.mower.state = "ERROR"; s.mower.errorCode = 93; s.mower.errorMessage = "EPOS raw API trace";
  assert.deepEqual(view.dashboardMessage(s), {title: "Mäher braucht Hilfe", text: "Standort nicht gefunden. Bitte am Mäher nachsehen.", tone: "bad"});
  s.automation.mowerStartOutcomeUnconfirmed = true;
  assert.match(view.dashboardMessage(s).text, /Nicht erneut starten/);
  assert.doesNotMatch(view.dashboardMessage(s).text, /EPOS|API|93/);
  const collision = snapshot(); collision.mower.activity = "MOWING";
  assert.equal(view.dashboardMessage(collision).title, "Mäher bitte stoppen");
  assert.match(view.dashboardMessage(collision).text, /vor Ort stoppen/);
  const unknown = view.dashboardMessage({});
  assert.notEqual(unknown.tone, "good");
  assert.match(unknown.text, /aktualisieren/);
});

test("Bewässerung von geräteeigenen Zeitplänen erscheint als laufend und bei Ausfällen unbekannt", () => {
  const s = snapshot(); s.generatedAt="2026-09-09T04:00:00Z"; s.automation.irrigationPhase = null;
  s.irrigation.safety.active_zone_count = 1; s.irrigation.safety.clear_now = false;
  assert.equal(view.waterTitle(s), "Läuft");
  assert.equal(view.dashboardMessage(s).title, "Bewässerung läuft");
  s.irrigation.safety.fresh = false;
  assert.equal(view.waterTitle(s), "Rückmeldung fehlt");
  assert.equal(view.nextWaterStart(s), "Noch offen");
  assert.match(view.simpleWater(s.irrigation), /aktualisieren/);
});

test("Planänderungen werden erst nach Bestätigung als gültig angezeigt", () => {
  const s = snapshot();
  assert.equal(view.nextWaterStart(s), "Do., 10.09.26, 04:00 Uhr");
  for (const status of ["APPLYING", "UNKNOWN"]) {
    s.irrigationSchedule.override = {kind: "PAUSE", status, error: "Hydrawise RAW_BACKEND_ERROR"};
    assert.equal(view.nextWaterStart(s), "Wird bestätigt");
    assert.doesNotMatch(view.planStatusText(s.irrigationSchedule.override).body, /Hydrawise|RAW_BACKEND/);
  }
  s.irrigationSchedule.override.status = "REJECTED";
  assert.equal(view.nextWaterStart(s), "Bitte Plan prüfen");
  assert.equal(view.dashboardMessage(s).title, "Bewässerungsplan bitte prüfen");
  s.irrigationSchedule.override.status = "ACTIVE";
  assert.equal(view.nextWaterStart(s), "Pausiert");
  s.irrigationSchedule.override = null;
  s.irrigationSchedule.nextRun.start = s.generatedAt;
  assert.equal(view.nextWaterStart(s), "Noch offen");
});

test("Unzulässige künftige Wasserzeiten werden nicht als nächster Start versprochen", () => {
  for (const start of ["2026-09-10T01:29:00Z", "2026-09-10T06:00:00Z", "2026-09-10T10:00:00Z"]) {
    const s = snapshot();
    s.irrigationSchedule.nextRun.start = start;
    assert.equal(view.nextWaterStart(s), "Bitte Plan prüfen");
  }
});

test("Fehlermeldungen und Fortschritt unterscheiden Anfrage und tatsächliche Ausführung", () => {
  assert.match(view.actionProgressText("START_MOWING"), /angefragt/);
  assert.match(view.actionProgressText("PARK_MOWER"), /Rückmeldung/);
  for (const status of [409, 403, 500]) {
    const error = view.friendlyError({status, message: "raw token error details"}, "action");
    assert.doesNotMatch(error, /raw|token|error details/);
  }
  assert.match(view.friendlyError({status: 500}, "action"), /prüfen/);
  assert.equal((html.match(/id="mower-next-start"/g) || []).length, 1);
  assert.equal((html.match(/id="charge-end-time"/g) || []).length, 1);
  assert.ok(!html.includes('id="diagnostic-blockers"'));
});

for (const timezone of ["UTC", "Europe/Berlin", "America/Los_Angeles"]) {
  test(`Uhrzeiten und Datumseingaben bleiben Berliner Zeit bei Gerätezeitzone ${timezone}`, () => {
    const code = `const assert=require('node:assert/strict');const view=require('./tests/helpers/platzwart_template').viewModel();
      assert.equal(view.inputDateTime(new Date('2026-09-09T10:00:00Z')),'2026-09-09T12:00');
      assert.match(view.planDate('2026-09-09T10:00:00Z'), /12:00 Uhr$/);
      assert.equal(view.parsePlanDateTime('2026-09-09T12:00').toISOString(),'2026-09-09T10:00:00.000Z');
      assert.equal(view.parsePlanDateTime('2026-12-09T12:00').toISOString(),'2026-12-09T11:00:00.000Z');
      assert.equal(view.parsePlanDateTime('2026-03-08T02:30').toISOString(),'2026-03-08T01:30:00.000Z');
      for(const value of ['2026-03-29T02:30','2026-10-25T02:30','2026-02-30T12:00','bad'])assert.ok(Number.isNaN(view.parsePlanDateTime(value).getTime()),value);
      assert.equal(view.planPauseEnd(1,'2026-03-28T11:00:00Z').toISOString(),'2026-03-29T10:00:00.000Z');
      assert.equal(view.planPauseEnd(1,'2026-10-24T10:00:00Z').toISOString(),'2026-10-25T11:00:00.000Z');
      assert.equal(view.calendarTime('2026-09-09T22:00:00Z','2026-09-09T21:30:00Z'),'Do., 10.09.26, 00:00 Uhr');`;
    const result = spawnSync(process.execPath, ["-e", code], {cwd: require("node:path").join(__dirname, ".."), env: {...process.env, TZ: timezone}, encoding: "utf8"});
    assert.equal(result.status, 0, result.stderr || result.stdout);
  });
}

test("Akkusperre verhindert eine Startuhrzeit auch bei geparktem Mäher", () => {
  const s=snapshot(); s.mower.activity="PARKED_IN_CS"; s.mower.batteryPercent=25; s.mower.restartBatteryPercent=90;
  s.overall.code="MOWER_BATTERY_CHARGING"; s.automation.irrigationPhase=null; s.coordination.blockers=[]; s.coordination.dryUntil=null;
  assert.equal(view.nextMowerStart(s),"Noch offen");
  assert.equal(view.dashboardMessage(s).title,"Akku noch nicht bereit");
  s.overall.code="WAITING";
  assert.equal(view.nextMowerStart(s),"Noch offen");
  s.mower.batteryPercent=100;
  assert.equal(view.nextMowerStart(s),"Heute, 14:30 Uhr");
});
test("Vorbereitung behauptet keinen Stationsaufenthalt während der Fahrt", () => {
  const s=snapshot(); s.mower.activity="GOING_HOME"; s.automation.irrigationPhase="PLANNED";
  assert.equal(view.dashboardMessage(s).text,"Bitte warten, bis der Mäher in der Station ist.");
  s.mower.activity="LEAVING";s.coordination.dryUntil=null;s.automation.irrigationPhase=null;s.coordination.blockers=[{code:"IRRIGATION_ACTIVE_OR_DUE"}];
  assert.equal(view.dashboardMessage(s).text,"Bitte warten, bis der Mäher in der Station ist.");
});

test("Eine Sperre über Mitternacht zeigt das Datum des Endes", () => {
  assert.equal(view.intervalEnd("2026-09-09T21:30:00Z", "2026-09-09T22:30:00Z", "2026-09-09T10:00:00Z"), "Do., 10.09.26, 00:30 Uhr");
  assert.equal(view.intervalEnd("2026-09-09T10:00:00Z", "2026-09-09T11:00:00Z", "2026-09-09T10:00:00Z"), "13:00 Uhr");
});

test("Planfehler und laufendes Wasser bleiben vor allgemeinen Pausehinweisen sichtbar", () => {
  const s=snapshot(); s.irrigationSchedule.override={kind:"PAUSE",status:"REJECTED"};s.coordination.blockers.push({code:"MANUAL_STOP"});
  assert.equal(view.dashboardMessage(s).title,"Bewässerungsplan bitte prüfen");
  assert.equal(view.dashboardMessage(s).tone,"bad");
  const running=snapshot(); running.generatedAt="2026-09-09T04:00:00Z"; running.irrigationSchedule.override={kind:"CUSTOM_NEXT",status:"EXECUTING"};running.irrigation.safety.active_zone_count=1;running.automation.irrigationPhase="RUNNING";
  assert.equal(view.dashboardMessage(running).title,"Bewässerung läuft");
  assert.equal(view.nextWaterStart(running),"Noch offen");
});


test("Geschlossene Bedienung erzeugt keine Startzusage und lässt keine Geräteänderung zu", () => {
  for (const gate of [false, undefined]) {
    const s = snapshot(); s.deviceControlsAvailable = gate;
    assert.equal(view.nextMowerStart(s), "Noch offen");
    for (const action of ["START_MOWING", "PARK_MOWER", "START_IRRIGATION", "STOP_IRRIGATION_NOW", "SET_CUTTING_HEIGHT", "CUSTOMIZE_NEXT_IRRIGATION"])
      assert.equal(view.deviceActionAllowed(s, action), false, action);
  }
});

test("Alte oder widersprüchliche Meldungen sperren Start, aber erlauben verfügbare Stoppaktionen", () => {
  for (const blocks of [[{code:"MOWER_TELEMETRY"}], null, [null]]) {
    const s = snapshot(); s.coordination.blockers = blocks;
    assert.equal(view.deviceActionAllowed(s, "START_MOWING"), false);
    assert.equal(view.deviceActionAllowed(s, "START_IRRIGATION_ZONE"), false);
    assert.equal(view.deviceActionAllowed(s, "SET_CUTTING_HEIGHT"), false);
    assert.equal(view.deviceActionAllowed(s, "PARK_MOWER"), true);
    assert.equal(view.deviceActionAllowed(s, "STOP_IRRIGATION_NOW"), true);
  }
  const manual=snapshot(); manual.overall.code="EXTERNAL_OVERRIDE"; manual.automation={}; manual.mower.telemetryFresh=false;
  assert.equal(view.effectiveMowerActions(manual).showStart,false);
  const fresh=snapshot(); assert.equal(view.deviceActionAllowed(fresh,"START_MOWING"),true);
  assert.equal(view.deviceActionAllowed(fresh,"UNKNOWN_ACTION"),false);
});
