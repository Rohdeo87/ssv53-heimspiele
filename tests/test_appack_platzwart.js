const assert = require("node:assert/strict");
const fs = require("node:fs");
const path = require("node:path");
const test = require("node:test");
const vm = require("node:vm");

const html = fs.readFileSync(
  path.join(__dirname, "..", "appack-platzwart-dashboard.html"),
  "utf8"
);

test("Platzwart-Seite bleibt syntaktisch gültig, geschützt und vollständig als Appack-Kopie", () => {
  const scripts = Array.from(html.matchAll(/<script(?![^>]*\bsrc=)[^>]*>([\s\S]*?)<\/script>/gi));
  assert.ok(scripts.length > 0);
  scripts.forEach((match, index) => {
    const rendered = match[1].replace(/\[#if profile_json\?has_content\]\$\{profile_json\}\[#else\]\{\}\[\/#if\]/g, "{}");
    assert.doesNotThrow(() => new vm.Script(rendered, {filename: `platzwart-${index}.js`}));
  });
  for (const value of ["STOP_IRRIGATION_AFTER_ZONE", "STOP_IRRIGATION_NOW", "START_IRRIGATION_ZONE", "START_MOWING_OCCUPANCY_OVERRIDE", "SKIP_NEXT_IRRIGATION", "PAUSE_IRRIGATION_UNTIL", "RESUME_IRRIGATION_SCHEDULE", "CUSTOMIZE_NEXT_IRRIGATION", "SET_CUTTING_HEIGHT", "RESET_BLADE_USAGE", "AbortController", "visibilitychange", "waitForAction", "aria-live", "minimum_mowing_minutes"])
    assert.ok(html.includes(value), value);
  assert.doesNotMatch(html, /HUSQVARNA_CLIENT_SECRET|HYDRAWISE_API_KEY|SSV53_PLATZWART_PIN_HASH|mowe_forced/i);
  assert.equal(fs.readFileSync(path.join(__dirname, "..", "appack-platzwart-dashboard.txt"), "utf8"), html);
});

test("Großtextmodus reagiert nur auf echten Überlauf", () => {
  const names = ["ssvElementVisible(element)", "ssvElementOverflows(element)", "ssvChildrenClipped(element)"];
  const source = names.map((name) => html.split("\n").find((line) => line.includes(`function ${name}`))).join("\n");
  const helpers = new Function(
    `var window={getComputedStyle:function(){return {visibility:"visible"}}};\n${source}\nreturn {ssvElementOverflows,ssvChildrenClipped};`
  )();
  const element = {
    isConnected: true,
    clientWidth: 100,
    clientHeight: 50,
    scrollWidth: 100,
    scrollHeight: 50,
    children: [],
    getBoundingClientRect() { return {left: 0, right: 100, bottom: 50, width: 100, height: 50}; }
  };
  assert.equal(helpers.ssvElementOverflows(element), false);
  assert.equal(helpers.ssvElementOverflows({...element, scrollWidth: 104}), true);
  const child = {
    ...element,
    getBoundingClientRect() { return {left: 0, right: 104, bottom: 45, width: 104, height: 45}; }
  };
  assert.equal(helpers.ssvChildrenClipped({...element, children: [child]}), true);
});

test("EPOS-Suchzustand bleibt verbunden und wird als Satellitensuche angezeigt", () => {
  const names = ["isSearching(m)", "hasActiveMowerError(m)", "activity(m)"];
  const source = names.map((name) => html.split("\n").find((line) => line.includes(`function ${name}`))).join("\n");
  const getActivity = new Function(`${source}\nreturn {isSearching,hasActiveMowerError,activity};`)();
  const mower = {
    activity: "NOT_APPLICABLE",
    displayActivity: "SEARCHING_FOR_POSITION",
    state: "IN_OPERATION",
    mode: "HOME",
    connected: true,
  };
  assert.equal(getActivity.isSearching(mower), true);
  assert.equal(getActivity.activity(mower), "Sucht Satellitensignal");
  assert.equal(
    getActivity.activity({ ...mower, activity: "MOWING", displayActivity: undefined, inactiveReason: "SEARCHING_FOR_SATELLITES" }),
    "Sucht Satellitensignal"
  );
  assert.equal(
    getActivity.activity({ ...mower, displayActivity: undefined, activity: "NOT_APPLICABLE" }),
    "Sucht Satellitensignal"
  );
  assert.equal(
    getActivity.activity({ activity: "MOWING", displayActivity: "MOWING", inactiveReason: "NONE", model: "Husqvarna Automower 580 EPOS", state: "IN_OPERATION", connected: true }),
    "Auf dem Platz aktiv"
  );
  assert.equal(
    getActivity.activity({ activity: "MOWING", displayActivity: "MOWING", inactiveReason: "NONE", model: "Automower 450X", state: "IN_OPERATION", connected: true }),
    "Mäht"
  );
  assert.equal(
    getActivity.activity({ activity: "NOT_APPLICABLE", state: "PAUSED", connected: true, errorCode: 0 }),
    "Pausiert"
  );
  assert.equal(
    getActivity.activity({ activity: "NOT_APPLICABLE", state: "ERROR", connected: true, errorCode: 93, errorMessage: "Keine genaue Satellitenposition" }),
    "Bitte am Mäher nachsehen"
  );
  assert.equal(
    getActivity.activity({ activity: "NOT_APPLICABLE", state: "UNKNOWN", connected: true, errorCode: 0 }),
    "Status wird geprüft"
  );
  assert.equal(
    getActivity.hasActiveMowerError({ state: "ERROR", errorCode: 93, errorActive: true }),
    true
  );
  assert.equal(
    getActivity.hasActiveMowerError({ state: "PAUSED", errorCode: 93, errorActive: false }),
    false
  );
  assert.match(html, /mowerTelemetryFresh\(s\)\?"Verbunden"/);
  assert.doesNotMatch(html, /m\.activity\|\|"Unbekannt"/);
});

test("Pausierter Mäher zeigt nur eine tatsächlich verfügbare Handlung", () => {
  const {viewModel, snapshot} = require("./helpers/platzwart_template");
  const view = viewModel(), s = snapshot();
  s.mower = {state: "PAUSED", activity: "NOT_APPLICABLE", connected: true, telemetryFresh: true};
  s.automation = {parkedByAutomation: true}; s.coordination.blockers = [{code:"MANUAL_STOP"}];
  s.coordination.dryUntil = null;
  assert.equal(view.dashboardMessage(s).title, "Mäher pausiert");
  assert.equal(view.simpleStatus(s), "Zum Fortsetzen „Mäher starten“ wählen.");
  s.automation.irrigationPhase = "READY";
  assert.match(view.simpleStatus(s), /Bewässerung wird vorbereitet/);
  s.automation = {};
  assert.equal(view.simpleStatus(s), "Bitte am Mäher nachsehen. Die Pause bleibt bestehen.");
});

test("Platzbelegung nutzt echte Zeiten und trennt Termine innerhalb eines Sperrblocks", () => {
  const names = ["localDay(value)", "calendarTime(v,referenceValue)", "occupancyItems(block)", "occupancySchedule(block)", "occupancyDisplayItems(block)", "occupancyName(block)", "intervalEnd(start,end,reference)", "occupancyWhen(block,current,reference)", "occupancyBlockWhen(block,current,reference)"];
  const source = names.map((name) => html.split("\n").find((line) => line.includes(`function ${name}`))).join("\n");
  const occupancy = new Function(`var EVENT_TIME_ZONE="Europe/Berlin";\n${source}\nreturn {occupancyName,occupancyWhen,occupancyDisplayItems,occupancyBlockWhen};`)();
  const irrigation = {
    start: "2026-08-21T01:45:00Z",
    end: "2026-08-21T05:00:00Z",
    source: "irrigation",
    title: "Bewässerung Zone 1; Bewässerung Zone 2",
    details: { items: [
      { start: "2026-08-21T01:45:00Z", end: "2026-08-21T02:55:00Z", details: { irrigation_start: "2026-08-21T02:15:00Z", irrigation_end: "2026-08-21T02:35:00Z" } },
      { start: "2026-08-21T02:05:00Z", end: "2026-08-21T03:25:00Z", details: { irrigation_start: "2026-08-21T02:35:00Z", irrigation_end: "2026-08-21T02:55:00Z" } },
    ] },
  };
  assert.equal(occupancy.occupancyName(irrigation), "Bewässerung");
  assert.match(occupancy.occupancyWhen(irrigation, false, "2026-08-20T20:00:00Z"), /04:15 Uhr · bis 04:55 Uhr$/);
  const training = { source: "training", title: "Training A", start: "2026-08-21T14:30:00Z", end: "2026-08-21T17:00:00Z", details: { items: [{ start: "2026-08-21T14:30:00Z", end: "2026-08-21T17:00:00Z", details: { nominal_start: "2026-08-21T15:00:00Z", nominal_end: "2026-08-21T16:30:00Z" } }] } };
  assert.equal(occupancy.occupancyName(training), "Training A");
  assert.match(occupancy.occupancyWhen(training, false, "2026-08-20T20:00:00Z"), /17:00 Uhr · bis 18:30 Uhr$/);
  const merged = {
    source: "match+training",
    title: "Training A; Spiel Ü40",
    start: "2026-08-21T14:30:00Z",
    end: "2026-08-21T20:30:00Z",
    details: { items: [
      { source: "training", title: "Training A", start: "2026-08-21T14:30:00Z", end: "2026-08-21T17:00:00Z", details: { nominal_start: "2026-08-21T15:00:00Z", nominal_end: "2026-08-21T16:30:00Z" } },
      { source: "match", title: "Spiel Ü40", start: "2026-08-21T17:20:00Z", end: "2026-08-21T20:30:00Z", details: { kickoff: "2026-08-21T18:30:00Z", match_end: "2026-08-21T20:00:00Z" } },
    ] },
  };
  const appointments = occupancy.occupancyDisplayItems(merged);
  assert.equal(appointments.length, 2);
  assert.deepEqual(appointments.map(occupancy.occupancyName), ["Training A", "Spiel Ü40"]);
  assert.match(occupancy.occupancyBlockWhen(merged, false, "2026-08-20T20:00:00Z"), /^Platz gesperrt: .*16:30 Uhr · bis 22:30 Uhr$/);
});

test("nur aktive Rollen werden geprüft und requestedRoles bleiben unberücksichtigt", () => {
  assert.match(html, /p\.roleKeys/);
  assert.match(html, /p\.roles/);
  assert.doesNotMatch(html, /requestedRoles/);
});

test("Vereinsheimtermine werden kompakt in Berliner Zeit dargestellt", () => {
  process.env.TZ = "Europe/Berlin";
  const dateFunction = html.split("\n").find((line) => line.includes("function eventDate(value)"));
  const clockFunction = html.split("\n").find((line) => line.includes("function eventClock(value)"));
  const dayFunction = html.split("\n").find((line) => line.includes("function eventDay(value)"));
  const timeFunction = html.split("\n").find((line) => line.includes("function eventTime(startValue,endValue)"));
  assert.ok(dateFunction);
  assert.ok(clockFunction);
  assert.ok(dayFunction);
  assert.ok(timeFunction);
  const format = new Function(
    `var EVENT_TIME_ZONE="Europe/Berlin";\n${dateFunction}\n${clockFunction}\n${dayFunction}\n${timeFunction}\nreturn eventTime;`
  )();

  assert.match(
    format("2026-08-23T16:00:00Z", "2026-08-23T20:00:00Z"),
    /^So\., 23\.08\.26 · 18:00–22:00 Uhr$/
  );
  assert.match(
    format("2026-09-05T05:00:00Z", "2026-09-05T22:00:00Z"),
    /^Sa\., 05\.09\.26 · 07:00–24:00 Uhr$/
  );
});

test("heutige Platzbelegung wird als Heute mit Uhrzeit dargestellt", () => {
  const localDayFunction = html.split("\n").find((line) => line.includes("function localDay(value)"));
  const calendarFunction = html.split("\n").find((line) => line.includes("function calendarTime(v,referenceValue)"));
  assert.ok(localDayFunction);
  assert.ok(calendarFunction);
  const format = new Function(
    `var EVENT_TIME_ZONE="Europe/Berlin";\n${localDayFunction}\n${calendarFunction}\nreturn calendarTime;`
  )();
  assert.equal(
    format("2026-08-20T17:30:00Z", "2026-08-20T08:00:00Z"),
    "Heute, 19:30 Uhr"
  );
  assert.match(
    format("2026-08-21T17:30:00Z", "2026-08-20T08:00:00Z"),
    /^Fr\., 21\.08\.26, 19:30 Uhr$/
  );
});

test("Mäheraktionen sind für Fahren, Laden, Sperren und manuelle Bedienung eindeutig", () => {
  const searchingFunction = html.split("\n").find((line) => line.includes("function isSearching(m)"));
  const pausedFunction = html.split("\n").find((line) => line.includes("function isMowerPaused(m)"));
  const schedulePendingFunction = html.split("\n").find((line) => line.includes("function irrigationScheduleChangePending(s)"));
  const actionsFunction = html.split("\n").find((line) => line.includes("function mowerActions(s)"));
  const deviceGateFunction = html.split("\n").find((line) => line.includes("function deviceControlsOpen(s)"));
  const telemetryFunction = html.split("\n").find((line) => line.includes("function mowerTelemetryFresh(s)"));
  const stationConfirmedFunction = html.split("\n").find((line) => line.includes("function stationConfirmed(s)"));
  const actionAllowedFunction = html.split("\n").find((line) => line.includes("function deviceActionAllowed(s,action)"));
  const effectiveActionsFunction = html.split("\n").find((line) => line.includes("function effectiveMowerActions(s)"));
  const coordinationBlockedFunction = html.split("\n").find((line) => line.includes("function coordinationExecutionBlocked(s)"));
  assert.ok(searchingFunction);
  assert.ok(pausedFunction);
  assert.ok(schedulePendingFunction);
  assert.ok(actionsFunction); assert.ok(stationConfirmedFunction);
  assert.ok(effectiveActionsFunction); assert.ok(coordinationBlockedFunction);
  const contextFunction = html.split("\n").find(line => line.includes("function mowerActionContext(s)"));
  const rawActions = new Function(`${contextFunction}\n${searchingFunction}\n${pausedFunction}\n${schedulePendingFunction}\n${deviceGateFunction}\n${telemetryFunction}\n${stationConfirmedFunction}\n${actionAllowedFunction}\n${actionsFunction}\n${coordinationBlockedFunction}\n${effectiveActionsFunction}\nreturn effectiveMowerActions;`)();
  const actions = (s) => rawActions({deviceControlsAvailable: true, ...s, mower: {connected: true, telemetryFresh: true, ...(s.mower || {})}, coordination: {blockers: [], ...(s.coordination || {})}});
  const safe = { available: true, fresh: true, clear_now: true };

  assert.deepEqual(
    { ...actions({ mower: { activity: "MOWING", connected: true, errorCode: 0 }, irrigation: { safety: safe }, automation: { continuousMowingOwned: true }, occupancy: {} }), startQuestion: undefined },
    { showPark: true, showStart: false, startLabel: "Mäher starten", parkLabel: "Mäher parken", parkQuestion: "Der Mäher fährt zur Station und bleibt dort, bis du ihn wieder freigibst.", startQuestion: undefined, occupancyOverrideKey: "" }
  );
  const searching = actions({ mower: { activity: "MOWING", displayActivity: "SEARCHING_FOR_POSITION", connected: true, errorCode: 0 }, irrigation: { safety: safe }, automation: { continuousMowingOwned: true }, occupancy: {} });
  assert.equal(searching.showPark, true);
  assert.equal(searching.showStart, false);
  const charging = actions({ mower: { activity: "CHARGING", batteryPercent: 70, connected: true, errorCode: 0 }, irrigation: { safety: safe }, automation: { continuousMowingOwned: true }, occupancy: {} });
  assert.equal(charging.showPark, false);
  assert.equal(charging.showStart, true);
  assert.equal(charging.startLabel, "Mäher starten");
  assert.match(charging.startQuestion, /lädt noch bei 70 %/);

  const blocked = actions({ mower: { activity: "CHARGING", connected: true, errorCode: 0 }, irrigation: { safety: safe }, automation: {}, occupancy: { overrideAllowed: true, current: { title: "Training" } } });
  assert.equal(blocked.showPark, false);
  assert.equal(blocked.showStart, false);

  const occupied = actions({ mower: { activity: "PARKED_IN_CS", connected: true, errorCode: 0 }, irrigation: { safety: safe }, automation: {}, occupancy: { overrideAllowed: true, current: { title: "Training A", start: "2026-08-20T18:00:00+02:00", end: "2026-08-20T20:00:00+02:00", source: "training" }, parking: { title: "Training A" } } });
  assert.equal(occupied.showStart, true);
  assert.equal(occupied.occupancyOverrideKey, "2026-08-20T18:00:00+02:00|2026-08-20T20:00:00+02:00|training");
  assert.match(occupied.startQuestion, /Bei Bewässerung oder Störungen bleibt der Start gesperrt/);

  const irrigationUnsafe = actions({ mower: { activity: "PARKED_IN_CS", connected: true, errorCode: 0 }, irrigation: { safety: { ...safe, clear_now: false } }, automation: {}, occupancy: { overrideAllowed: true, current: { title: "Training", start: "a", end: "b", source: "training" } } });
  assert.equal(irrigationUnsafe.showStart, false);

  const manual = actions({ overall: { code: "EXTERNAL_OVERRIDE" }, mower: { activity: "CHARGING", connected: true, errorCode: 0 }, irrigation: { safety: safe }, automation: {}, occupancy: { overrideAllowed: true, current: { title: "Training", start: "2026-08-20T18:00:00+02:00", end: "2026-08-20T20:00:00+02:00", source: "training" } } });
  assert.equal(manual.showStart, true);
  assert.equal(manual.startLabel, "Mäher starten");
  assert.ok(manual.occupancyOverrideKey);

  const pausedOwned = actions({ mower: { activity: "NOT_APPLICABLE", state: "PAUSED", connected: true, errorCode: 0 }, irrigation: { safety: safe }, automation: { parkedByAutomation: true }, occupancy: {} });
  assert.equal(pausedOwned.showStart, true);
  assert.equal(pausedOwned.startLabel, "Mäher starten");
  assert.match(pausedOwned.startQuestion, /manuell pausiert/);

  const pausedUnowned = actions({ mower: { activity: "NOT_APPLICABLE", state: "PAUSED", connected: true, errorCode: 0 }, irrigation: { safety: safe }, automation: {}, occupancy: {} });
  assert.equal(pausedUnowned.showStart, false);

  const pausedDuringIrrigation = actions({ mower: { activity: "NOT_APPLICABLE", state: "PAUSED", connected: true, errorCode: 0 }, irrigation: { safety: safe }, automation: { parkedByAutomation: true, irrigationPhase: "READY" }, occupancy: {} });
  assert.equal(pausedDuringIrrigation.showStart, false);

  const scheduleChange = actions({ mower: { activity: "PARKED_IN_CS", connected: true, errorCode: 0 }, irrigation: { safety: safe }, irrigationSchedule: { override: { kind: "SKIP_NEXT", status: "APPLYING" } }, automation: { parkedByAutomation: true }, occupancy: {} });
  assert.equal(scheduleChange.showStart, false);

  const movingDuringScheduleChange = actions({ mower: { activity: "MOWING", connected: true, errorCode: 0 }, irrigation: { safety: safe }, irrigationSchedule: { override: { kind: "PAUSE", status: "CONFIRMING" } }, automation: { continuousMowingOwned: true }, occupancy: {} });
  assert.equal(movingDuringScheduleChange.showPark, true);
  assert.equal(movingDuringScheduleChange.showStart, false);

  const activeSkip = actions({ mower: { activity: "PARKED_IN_CS", connected: true, errorCode: 0 }, irrigation: { safety: safe }, irrigationSchedule: { override: { kind: "SKIP_NEXT", status: "ACTIVE" } }, automation: { parkedByAutomation: true }, occupancy: {} });
  assert.equal(activeSkip.showStart, true);

  const irrigationBlock = actions({ mower: { activity: "PARKED_IN_CS", connected: true, errorCode: 0 }, irrigation: { safety: safe }, automation: {}, occupancy: { overrideAllowed: true, current: { title: "Mischblock", start: "a", end: "b", source: "training+irrigation" } } });
  assert.equal(irrigationBlock.showStart, false);

  const disconnected = actions({ mower: { activity: "MOWING", connected: false, errorCode: 0 }, irrigation: { safety: safe }, automation: {}, occupancy: {} });
  assert.equal(disconnected.showPark, false);
  assert.equal(disconnected.showStart, false);

  const displayOnly = actions({ controlsAvailable: false, mower: { activity: "MOWING", connected: true, errorCode: 0 }, irrigation: { safety: safe }, automation: { continuousMowingOwned: true }, occupancy: {} });
  assert.equal(displayOnly.showPark, false);
  assert.equal(displayOnly.showStart, false);
});

test("Bewässerungsaktionen erscheinen nur im passenden Zustand", () => {
  const actionsFunction = html.split("\n").find((line) => line.includes("function irrigationActions(s)"));
  const deviceGateFunction = html.split("\n").find((line) => line.includes("function deviceControlsOpen(s)"));
  const telemetryFunction = html.split("\n").find((line) => line.includes("function mowerTelemetryFresh(s)"));
  const actionAllowedFunction = html.split("\n").find((line) => line.includes("function deviceActionAllowed(s,action)"));
  assert.ok(actionsFunction);
  const coordinationBlockedFunction = html.split("\n").find((line) => line.includes("function coordinationExecutionBlocked(s)"));
  const startFunctions = ["irrigationStartAllowed", "irrigationScheduleChangePending", "hasActiveMowerError"].map(name => html.split("\n").find(line => line.startsWith("    function "+name+"("))).join("\n");
  const rawActions = new Function(`${startFunctions}\n${coordinationBlockedFunction}\n${deviceGateFunction}\n${telemetryFunction}\n${actionAllowedFunction}\n${actionsFunction}\nreturn irrigationActions;`)();
  const actions = (s) => rawActions({deviceControlsAvailable: true, ...s, mower: {connected: true, telemetryFresh: true, ...(s.mower || {})}, coordination: {blockers: [], ...(s.coordination || {})}});
  const safe = { available: true, fresh: true, clear_now: true, active_zone_count: 0, imminent_zone_count: 0 };
  assert.deepEqual(actions({ automation: {}, irrigation: { safety: safe } }), { showStart: true, showStop: false });
  assert.deepEqual(actions({ automation: { irrigationPhase: "RUNNING" }, irrigation: { safety: safe } }), { showStart: false, showStop: true });
  assert.deepEqual(actions({ automation: { irrigationPhase: "COMPLETE_HOLD" }, irrigation: { safety: safe } }), { showStart: false, showStop: false });
  assert.deepEqual(actions({ automation: {}, irrigation: { safety: { available: false, fresh: false } } }), { showStart: false, showStop: false });
  assert.deepEqual(actions({ controlsAvailable: false, automation: {}, irrigation: { safety: safe } }), { showStart: false, showStop: false });
  assert.deepEqual(actions({ controlsAvailable: false, automation: { irrigationPhase: "RUNNING" }, irrigation: { safety: safe } }), { showStart: false, showStop: false });
});

test("Anzeige bleibt bei Sicherheitsplan- oder Lesefehlern bedienungslos verfügbar", () => {
  assert.match(html, /Aktuelle Angaben fehlen/);
  assert.match(html, /Verbindung unterbrochen/);
  assert.match(html, /controlsAvailable===false/);
  assert.match(html, /controlsAvailable!==false/);
});

test("eine bestätigte Klingenlaufzeit von null hebt eine alte Reset-Fehlermeldung auf", () => {
  const failureFunction = html.split("\n").find((line) => line.includes("function bladeResetFailed(s)"));
  assert.ok(failureFunction);
  const failed = new Function(`${failureFunction}\nreturn bladeResetFailed;`)();
  const rejected = { automation: { lastOperatorAction: "RESET_BLADE_USAGE", lastOperatorStatus: "REJECTED" } };
  assert.equal(failed({ ...rejected, statistics: { bladeUsageSeconds: 146886 } }), true);
  assert.equal(failed({ ...rejected, statistics: { bladeUsageSeconds: 0 } }), false);
});

test("gemähte Rasenflächen werden mit deutschem Dezimalkomma angezeigt", () => {
  const equivalentFunction = html.split("\n").find((line) => line.includes("function areaEquivalent(value)"));
  assert.ok(equivalentFunction);
  const format = new Function(`${equivalentFunction}\nreturn areaEquivalent;`)();
  assert.equal(format(3.4), "3,4 ×");
  assert.equal(format(1), "1,0 ×");
  assert.equal(format(null), "–");
});
