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
  assert.equal(view.dashboardMessage(s).title,"Bewässerung läuft");
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
  assert.equal(view.nextMowerStart(s), "Mäher läuft bereits");
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

test("Bestätigte Station ersetzt nur die reine Alterswarnung und ändert nicht die Startfreigabe", () => {
  const s = snapshot();
  s.mower = {...s.mower, activity: "PARKED_IN_CS", state: "RESTRICTED", mode: "HOME", telemetryFresh: false, errorCode: 0};
  s.manualControl = {stationConfirmed: true, canStart: false};
  s.automation = {continuousMowingOwned: true, irrigationPhase: null};
  s.coordination = {...s.coordination, dryUntil: null, blockers: [{code: "MOWER_TELEMETRY"}]};
  s.occupancy = {current: null, parking: null};
  assert.equal(view.stationConfirmed(s), true);
  assert.equal(view.mowerTelemetryFresh(s), false);
  assert.equal(view.dashboardMessage(s).title, "Mäher ist in der Station");
  assert.equal(view.dashboardMessage(s).text, "Der Mäher wartet in der Station.");
  s.manualControl.canStart = true;
  assert.equal(view.dashboardMessage(s).text, "Du kannst den Mäher starten.");
  assert.equal(view.effectiveMowerActions(s).showStart, false);
  assert.equal(view.effectiveMowerActions(s).parkLabel, "In Station lassen");
});

test("Bestätigte Station überstimmt keine Wasser-, Belegungs- oder Fehlerlage", () => {
  const cases = [
    s => { s.irrigation.safety.active_zone_count = 1; },
    s => { s.occupancy.current = {start: s.generatedAt, end: "2026-09-09T12:00:00Z"}; },
    s => { s.mower.errorCode = 93; },
    s => { s.coordination.dryUntil = "2026-09-09T12:30:00Z"; }
  ];
  for (const change of cases) {
    const s = snapshot();
    s.mower = {...s.mower, activity: "PARKED_IN_CS", state: "RESTRICTED", mode: "HOME", telemetryFresh: false, errorCode: 0};
    s.manualControl = {stationConfirmed: true};
    s.automation = {continuousMowingOwned: true, irrigationPhase: null};
    s.coordination = {...s.coordination, blockers: [{code: "MOWER_TELEMETRY"}]};
    s.occupancy = {current: null, parking: null};
    change(s);
    assert.notEqual(view.dashboardMessage(s).title, "Mäher in Station geparkt");
  }
});

test("Trocknungsanzeige rundet nur nach oben und behandelt Mitternacht", () => {
  assert.equal(view.dryingTime("2026-09-09T20:41:51.487Z", "2026-09-09T20:00:00Z"), "Heute, 22:42 Uhr");
  assert.equal(view.dryingTime("2026-09-09T20:42:00Z", "2026-09-09T20:00:00Z"), "Heute, 22:42 Uhr");
  assert.equal(view.dryingTime("2026-09-09T21:59:59Z", "2026-09-09T20:00:00Z"), "Do., 10.09.26, 00:00 Uhr");
});

test("Manueller Betrieb bleibt verständlich und priorisiert echte Sperren", () => {
  const s = snapshot();
  s.operationMode = "MANUAL"; s.mower.activity = "MOWING"; s.coordination.blockers = [];
  s.coordination.dryUntil = "2026-09-09T12:28:01Z";
  assert.equal(view.dashboardMessage(s).title, "Manueller Betrieb");
  assert.match(view.dashboardMessage(s).text, /Rasenpause bis/);
  s.coordination.dryUntil = null; s.occupancy.current = {start: "2026-09-09T10:00:00Z", end: "2026-09-09T11:00:00Z"};
  assert.equal(view.dashboardMessage(s).title, "Manueller Betrieb");
  assert.match(view.dashboardMessage(s).text, /belegt/);
  s.occupancy.current = null; s.irrigation.safety.active_zone_count = 1;
  assert.equal(view.dashboardMessage(s).title, "Bewässerung läuft");
});

test("Höhenstatus unterscheidet ausstehend, bestätigt und abgewiesen", () => {
  const s = snapshot(); s.operatorCommands = {SET_CUTTING_HEIGHT: {targetMm: 26, status: "PENDING"}};
  assert.equal(view.heightStatusText(s), "26 mm angefragt – Bestätigung steht aus.");
  s.operatorCommands.SET_CUTTING_HEIGHT.status = "CONFIRMED";
  assert.equal(view.heightStatusText(s), "26 mm bestätigt.");
  s.operatorCommands.SET_CUTTING_HEIGHT.status = "REJECTED";
  assert.equal(view.heightStatusText(s), "Änderung nicht bestätigt. Bitte aktuellen Wert prüfen.");
});

test("Manueller Stopp bleibt als Pause sichtbar, auch mit berechenbarem Ladeende", () => {
  const s = snapshot(); s.coordination.blockers.push({code: "MANUAL_STOP"});
  assert.equal(view.nextMowerStart(s), "Automatik pausiert");
  assert.equal(view.dashboardMessage(s).title, "Mäher pausiert");
  assert.match(view.dashboardMessage(s).text, /abwarten/);
  s.automation.irrigationPhase = null;
  s.overall.code = "OPERATOR_PARK_HOLD";
  s.coordination.dryUntil = null;
  s.coordination.blockers = [{code: "MANUAL_STOP"}];
  assert.match(view.dashboardMessage(s).text, /„Mäher starten“/);
  s.mower.state = "PAUSED"; s.mower.activity = "NOT_APPLICABLE"; s.automation = {};
  assert.match(view.dashboardMessage(s).text, /am Mäher nachsehen/);
});

test("Fehler und Sicherheitskonflikte erhalten eine klare Handlung ohne Rohmeldungen", () => {
  const s = snapshot(); s.mower.state = "ERROR"; s.mower.errorCode = 93; s.mower.errorMessage = "EPOS raw API trace";
  assert.deepEqual(view.dashboardMessage(s), {title: "Keine genaue Satellitenposition", text: "Bitte den Satellitenempfang am Mäher prüfen.", tone: "bad", icon: "TriangleAlert"});
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
  assert.equal(view.friendlyError({status: 409}, "action"), "Änderung nicht angenommen. Bitte aktualisieren und Meldung prüfen.");
  assert.match(view.friendlyError({status: 409, code: "ACTION_PENDING"}, "action"), /andere Änderung/);
  assert.equal((html.match(/id="mower-next-start"/g) || []).length, 1);
  assert.equal((html.match(/id="charge-end-time"/g) || []).length, 1);
  assert.ok(!html.includes('id="diagnostic-blockers"'));
});

test("Bedienkette verwendet Vertragsversion und Journalabfrage mit Request-ID", () => {
  assert.match(html, /clientContractVersion=payload\.manualControl&&payload\.manualControl\.operation==="CONFIRM_DOCK_FOR_IRRIGATION"\?4:2/);
  assert.match(html, /operatorCommands/);
  assert.match(html, /QUEUED/);
  assert.match(html, /SENT_UNCONFIRMED/);
  assert.match(html, /pendingRequestId/);
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
  assert.equal(view.dashboardMessage(s).title,"Mäher in der Station");
  assert.match(view.dashboardMessage(s).text,/Akku 25 %/);
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

test("Aktionsfähigkeiten sind pro Aktion maßgeblich und Parken bleibt bei Höhenanfrage möglich", () => {
  const s = snapshot();
  s.deviceControlsAvailable = false;
  s.actionCapabilities = {
    PARK_MOWER: {available: true, reason: "PROTECTIVE_PARK"},
    SET_CUTTING_HEIGHT: {available: false, reason: "OPERATOR_ACTION_UNCONFIRMED"}
  };
  s.mower.activity = "MOWING";
  s.mower.errorCode = 93;
  s.automation.pendingAction = "SET_CUTTING_HEIGHT";
  assert.equal(view.deviceActionAllowed(s, "PARK_MOWER"), true);
  assert.equal(view.deviceActionAllowed(s, "SET_CUTTING_HEIGHT"), false);
  assert.equal(view.effectiveMowerActions(s).showPark, true);
  assert.equal(view.effectiveMowerActions(s).showStart, false);
});

test("Operatorjournal zeigt Parkanfrage und unklaren Ausgang verständlich", () => {
  const s = snapshot();
  s.mower.activity = "PARKED_IN_CS";
  for (const status of ["QUEUED", "RESERVED", "SENT_UNCONFIRMED", "CONFIRMING"]) {
    s.operatorCommands = {PARK_MOWER: {status, requestId: "park-1"}};
    assert.equal(view.dashboardMessage(s).title, "Parken angefragt – Bestätigung steht aus", status);
  }
  s.operatorCommands.PARK_MOWER.status = "UNKNOWN";
  assert.equal(view.dashboardMessage(s).title, "Parken nicht bestätigt");
  assert.match(view.dashboardMessage(s).text, /nicht automatisch/);
  s.operatorCommands.PARK_MOWER.status = "CONFIRMED";
  assert.equal(view.dashboardMessage(s).title, "Rasen trocknet");
});

test("Journalstatus der Schnitthöhe bleibt ausstehend statt Erfolg zu behaupten", () => {
  const s = snapshot();
  s.operatorCommands = {SET_CUTTING_HEIGHT: {targetMm: 26, status: "QUEUED"}};
  for (const status of ["QUEUED", "RESERVED", "SENT_UNCONFIRMED", "UNKNOWN"]) {
    s.operatorCommands.SET_CUTTING_HEIGHT.status = status;
    assert.equal(view.heightStatusText(s), "26 mm angefragt – Bestätigung steht aus.", status);
  }
  s.operatorCommands.SET_CUTTING_HEIGHT.status = "CONFIRMED";
  assert.equal(view.heightStatusText(s), "26 mm bestätigt.");
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


test("Historische Bestätigungen verdecken keinen neuen manuellen Betrieb", () => {
 const s=snapshot();s.deviceControlsAvailable=false;s.mower.activity="MOWING";s.mower.operationMode="MANUAL";s.mower.cuttingHeightMm=25;s.automation={};s.coordination.dryUntil=null;s.coordination.blockers=[];
 s.operatorCommands={PARK_MOWER:{status:"CONFIRMED",requestId:"old-park"},SET_CUTTING_HEIGHT:{status:"CONFIRMED",targetMm:26,requestId:"old-height"}};
 assert.equal(view.dashboardMessage(s).title,"Mäher mäht");
 assert.match(view.dashboardMessage(s).text,/Manuell gestartet/);
 assert.equal(view.heightStatusText(s),"");
 assert.equal(view.nextMowerStart(s),"Mäher läuft bereits");
});

test("Eine alte Bestätigung beendet keine neue Anfrage", () => {
 const s=snapshot();s.operatorCommands={SET_CUTTING_HEIGHT:{status:"CONFIRMED",requestId:"old"}};
 assert.equal(view.actionRequestPending(s,"SET_CUTTING_HEIGHT","new",true),true);
 s.operatorCommands.SET_CUTTING_HEIGHT={status:"SENT_UNCONFIRMED",requestId:"new"};
 assert.equal(view.actionRequestPending(s,"SET_CUTTING_HEIGHT","new",true),true);
 s.operatorCommands.SET_CUTTING_HEIGHT.status="CONFIRMED";
 assert.equal(view.actionRequestPending(s,"SET_CUTTING_HEIGHT","new",true),false);
 s.operatorCommands.SET_CUTTING_HEIGHT.status="UNKNOWN";
 assert.equal(view.actionRequestPending(s,"SET_CUTTING_HEIGHT","new",true),false);
});

test("Parken bleibt auch beim Laden erreichbar und Ladezustand wird nicht durch Bedienrechte ersetzt", () => {
 const s=snapshot();s.deviceControlsAvailable=false;s.actionCapabilities={PARK_MOWER:{available:true}};s.automation={};s.coordination.blockers=[];s.coordination.dryUntil=null;
 assert.equal(view.dashboardMessage(s).title,"Mäher lädt");
 assert.equal(view.effectiveMowerActions(s).showPark,true);
});

test("Schutzflags erklären ausgeschaltete Automatik ohne aktive Warnung zu verdecken", () => {
 const s=snapshot();s.protection={automaticStartEnabled:false,protectiveParkingEnabled:false};s.mower.activity="CHARGING";s.coordination.blockers=[];s.coordination.dryUntil=null;
 assert.equal(view.dashboardMessage(s).title,"Mäher lädt");
 assert.equal(view.protectionNotice(s),"Automatik aus. Mäher vor Bewässerung und Platzbelegung parken.");
 s.protection.protectiveParkingEnabled=true;
 assert.equal(view.dashboardMessage(s).title,"Mäher lädt");
 assert.equal(view.protectionNotice(s),"Automatisches Starten aus. Mäherstart nur manuell.");
 s.mower.activity="MOWING";s.mower.operationMode="MANUAL";
 assert.equal(view.dashboardMessage(s).title,"Mäher mäht");
 assert.match(view.dashboardMessage(s).text,/Manuell gestartet/);
 assert.equal(view.protectionNotice(s),"Automatisches Starten aus. Mäherstart nur manuell.");
});

test("Aktive Bewässerungswarnung hat Vorrang vor dem Automatikschutz-Hinweis", () => {
 const s=snapshot();s.protection={automaticStartEnabled:false,protectiveParkingEnabled:false};s.generatedAt="2026-09-09T04:00:00Z";s.mower.activity="CHARGING";s.automation.irrigationPhase="RUNNING";s.irrigation.safety.active_zone_count=1;s.coordination.blockers=[];
 assert.equal(view.dashboardMessage(s).title,"Bewässerung läuft");
});

test("Ausgeschaltete Automatik verspricht keine berechnete Startuhrzeit", () => {
 const s=snapshot();s.protection={automaticStartEnabled:false,protectiveParkingEnabled:true};s.mower.activity="PARKED_IN_CS";s.mower.batteryPercent=100;s.mower.restartBatteryPercent=90;s.automation.irrigationPhase=null;s.coordination.blockers=[];s.coordination.dryUntil=null;
 assert.equal(view.nextMowerStart(s),"Start nur manuell");
 s.operatorCommands={START_MOWING:{status:"QUEUED",requestId:"start-1"}};
 assert.equal(view.nextMowerStart(s),"Mäherstart angefragt");
});


test("Statuspolling wartet tatsächlich auf die passende neue Bestätigung", async () => {
 const {sourceOf}=require("./helpers/platzwart_template");
 const states=[{operatorCommands:{SET_CUTTING_HEIGHT:{requestId:"old",status:"CONFIRMED"}}},{operatorCommands:{SET_CUTTING_HEIGHT:{requestId:"new",status:"SENT_UNCONFIRMED"}}},{operatorCommands:{SET_CUTTING_HEIGHT:{requestId:"new",status:"CONFIRMED"}}}];let reads=0;
 const poll=new Function("load","setTimeout",sourceOf("operatorActionPending")+sourceOf("actionRequestPending")+sourceOf("waitForAction")+";return waitForAction;")(()=>Promise.resolve(states[reads++]),(callback)=>callback());
 const answer=await poll(0,"SET_CUTTING_HEIGHT","new",true);
 assert.equal(reads,3);assert.equal(answer.operatorCommands.SET_CUTTING_HEIGHT.requestId,"new");
});

test("Bestätigter manueller Wasserlauf hat keine Morgen-Zeitsperre, auch bei geparktem Mäher", () => {
  const s=snapshot();s.automation.irrigationPhase="RUNNING";s.irrigation.safety.active_zone_count=1;
  s.irrigation.intent={source:"MANUAL_OPERATOR",verified:true,controllerManaged:true,automaticWindowApplies:false};
  s.manualControl={enabled:true,status:"MANUAL_PARKED"};s.mower.mode="HOME";
  for(const at of ["2026-09-09T00:00:00Z","2026-09-09T06:00:00Z","2026-09-09T21:00:00Z"]){
    s.generatedAt=at;
    assert.equal(view.dashboardMessage(s).title,"Bewässerung läuft");
    assert.doesNotMatch(view.dashboardMessage(s).text,/03:30|08:00/);
    s.coordination.blockers=[{code:"MOWER_TELEMETRY"}];
    assert.equal(view.dashboardMessage(s).title,"Bewässerung läuft");
    s.coordination.blockers=[];
  }
  s.mower.activity="MOWING";
  assert.match(view.dashboardMessage(s).text,/Mäher parken/);
  assert.equal(view.dashboardMessage(s).tone,"bad");
});

test("Fehlende oder unbestätigte Herkunft nimmt fremdes Wasser nicht von der Zeitwarnung aus", () => {
  const s=snapshot();s.automation.irrigationPhase="RUNNING";s.irrigation.safety.active_zone_count=1;
  const verified={source:"MANUAL_OPERATOR",verified:true,controllerManaged:true,automaticWindowApplies:false};
  for(const intent of [null,{...verified,source:"AUTOMATIC"},{...verified,verified:false},{...verified,controllerManaged:false},{...verified,automaticWindowApplies:true}]){
    s.irrigation.intent=intent;
    assert.equal(view.manualIrrigationRun(s),false);
    assert.equal(view.dashboardMessage(s).title,"Bewässerung bitte beenden");
  }
  s.irrigation.intent=verified;
  for(const phase of ["COMPLETE_HOLD","FAILED","PLANNED",null]){
    s.automation.irrigationPhase=phase;
    assert.equal(view.manualIrrigationRun(s),false);
  }
});

test("Unklare Bewässerungslücke behauptet keine sichere Trockenzeit", () => {
 const s=snapshot();s.mower.activity="PARKED_IN_CS";s.automation.irrigationPhase=null;s.coordination.blockers=[];s.coordination.dryUntil="2026-09-09T16:25:00Z";s.coordination.dryingReason="POSSIBLE_IRRIGATION_DURING_GAP";s.generatedAt="2026-09-09T14:30:00Z";
 const message=view.dashboardMessage(s);assert.equal(message.title,"Rasenpause zur Sicherheit");assert.equal(message.text,"Letzte Bewässerung unklar. Bitte den Platz prüfen.");
});

test("Bekannte Trockenzeit und Datenlücke zeigen Grund und Ablaufzeit in der Zeitleiste", () => {
 const s=snapshot();s.mower.activity="PARKED_IN_CS";s.automation.irrigationPhase=null;s.coordination.blockers=[];s.coordination.dryUntil="2026-09-09T16:25:00Z";s.generatedAt="2026-09-09T14:30:00Z";
 assert.equal(view.dashboardMessage(s).title,"Rasen trocknet");s.coordination.dryingReason="DATA_GAP";assert.match(view.dashboardMessage(s).title,/Kurze Prüfung bis/);
 const nodes = new Map();
 const document = {getElementById(id) {
   if (!nodes.has(id)) nodes.set(id, {textContent:"", classList:{toggle(name, enabled){this[name]=enabled;}}});
   return nodes.get(id);
 }};
 const render = new Function("document", "text", "nextStartInfo", "chargingEnd", "calendarTime", "dryingTime", "nextWaterStart", sourceOf("renderCoordination") + ";return renderCoordination;")(
   document, (id,value)=>document.getElementById(id).textContent=value,
   view.nextStartInfo,view.chargingEnd,view.calendarTime,view.dryingTime,view.nextWaterStart);
 for (const [reason,label] of [["IRRIGATION_END","Wartezeit nach Bewässerung bis"],["DATA_GAP","Kurze Prüfung bis"],["POSSIBLE_IRRIGATION_DURING_GAP","Rasenpause zur Sicherheit bis"]]) {
   s.coordination.dryingReason=reason;render(s);
   assert.equal(nodes.get("drying-end-label").textContent,label);
   assert.equal(nodes.get("drying-end-time").textContent,"Heute, 18:25 Uhr");
   assert.equal(nodes.get("drying-end-row").classList.hidden,false);
 }
 s.generatedAt="2026-09-09T16:26:00Z";render(s);
 assert.equal(nodes.get("drying-end-row").classList.hidden,true);
});


test("Übernommener Einsatz zeigt Mähen, Fortschritt bleibt getrennt und Konflikte behalten Vorrang", () => {
 const s=snapshot();s.mower.activity="MOWING";s.mower.operationMode="AUTOMATIC";s.automation.irrigationPhase=null;
 s.automation.continuousMowingOwned=true;s.coordination.blockers=[];s.coordination.dryUntil=null;
 s.manualControl={enabled:true,status:"AUTOMATIC"};
 assert.equal(view.dashboardMessage(s).title,"Mäher mäht");
 assert.equal(view.dashboardMessage(s).tone,"good");
 assert.match(view.dashboardMessage(s).text,/automatisch/);
 s.irrigation.safety.active_zone_count=1;
 assert.equal(view.dashboardMessage(s).title,"Bewässerung läuft");
 assert.equal(view.dashboardMessage(s).tone,"bad");
});
