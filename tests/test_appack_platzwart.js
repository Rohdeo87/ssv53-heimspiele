const assert = require("node:assert/strict");
const fs = require("node:fs");
const path = require("node:path");
const test = require("node:test");
const vm = require("node:vm");

const html = fs.readFileSync(
  path.join(__dirname, "..", "appack-platzwart-dashboard.html"),
  "utf8"
);

test("Platzwart-Seite ist syntaktisch gültig und enthält keine Zugangsdaten", () => {
  const scripts = Array.from(html.matchAll(/<script(?![^>]*\bsrc=)[^>]*>([\s\S]*?)<\/script>/gi));
  assert.ok(scripts.length > 0);
  scripts.forEach((match, index) => {
    const rendered = match[1].replace(
      /\[#if profile_json\?has_content\]\$\{profile_json\}\[#else\]\{\}\[\/\#if\]/g,
      "{}"
    );
    assert.doesNotThrow(() => new vm.Script(rendered, { filename: `platzwart-${index}.js` }));
  });
  assert.match(html, /platzwart/i);
  assert.match(html, /STOP_IRRIGATION_AFTER_ZONE/);
  assert.match(html, /STOP_IRRIGATION_NOW/);
  assert.match(html, /START_IRRIGATION_ZONE/);
  assert.match(html, /Automatik einschalten/);
  assert.match(html, /refresh-spinner/);
  assert.match(html, /clubhouse-events/);
  assert.match(html, /Wird ausgeführt/);
  assert.match(html, /waitForAction/);
  assert.match(html, /Gebucht von:/);
  assert.match(html, /function eventDate\(value\)/);
  assert.match(html, /weekday:"short",day:"2-digit",month:"2-digit",year:"2-digit"/);
  assert.match(html, /EVENT_TIME_ZONE="Europe\/Berlin"/);
  assert.match(html, /startTime\+"–"\+endTime/);
  assert.doesNotMatch(html, /month:"long"/);
  assert.match(html, /function submitPin\(\)/);
  assert.doesNotMatch(html, /id="login-button"/);
  assert.match(html, /function clearActionError\(\)/);
  assert.match(html, /Mäher-, Beregnungs-, Platzbelegungs- und Vereinsheimdaten konnten nicht geladen werden/);
  assert.match(html, /Bedienaktion konnte nicht an die Platzpflege-Steuerung übermittelt werden/);
  assert.doesNotMatch(html, />Failed to fetch</);
  assert.match(html, /continuousMowingOwned/);
  assert.match(html, /Automatik aktiv/);
  assert.match(html, /Automatik ausgeschaltet/);
  assert.match(html, />Mäher parken</);
  assert.match(html, />Alle Zonen starten</);
  assert.match(html, /class="btn water-start"/);
  assert.match(html, /id="mow-park" class="btn mower-action hidden"/);
  assert.match(html, /id="mow-start" class="btn mower-action"/);
  assert.match(html, /id="irrigation-stop" class="btn stop hidden"/);
  assert.match(html, /\.btn\.water-start,\.btn\.stop,\.btn\.goldbtn\{background:var\(--blue\)/);
  assert.match(html, /\.btn\.mower-action\{background:var\(--red\);color:#fff/);
  assert.doesNotMatch(html, /\.btn[^\{]*\{[^}]*background:var\(--gold\)/);
  assert.match(html, /\.btn:disabled\{background:#eef1f5!important;color:#929cab!important/);
  assert.match(html, /id="mower-next-start"/);
  assert.match(html, /class="next-start-fact hidden"/);
  assert.match(html, /Geschätzt:/);
  assert.match(html, /Nach Plan:/);
  assert.match(html, /Nach Beregnungsende \+ 120 Min\./);
  assert.match(html, /startButton\.classList\.toggle\("hidden"/);
  assert.match(html, /parkButton\.classList\.toggle\("hidden"/);
  assert.match(html, /function mowerActions\(s\)/);
  assert.match(html, /function irrigationActions\(s\)/);
  assert.doesNotMatch(html, />Mäher sicher parken</);
  assert.doesNotMatch(html, />Sieben Zonen sicher starten</);
  assert.match(html, /Mäher nicht erreichbar/);
  assert.match(html, /Sucht Satellitensignal/);
  assert.doesNotMatch(html, /Mäher sucht Verbindung/);
  assert.match(html, /mower-connection/);
  assert.match(html, /cutting-height-current/);
  assert.match(html, /class="mower-error-fact hidden"/);
  assert.match(html, /className="running-dots"/);
  assert.match(html, /@keyframes dot-run/);
  assert.match(html, /id="occupancy-primary"/);
  assert.match(html, /id="occupancy-list"/);
  assert.match(html, /id="occupancy-block-details"/);
  assert.match(html, /id="occupancy-block-list"/);
  assert.match(html, /Termine in diesem Sperrblock/);
  assert.match(html, /\.occupancy-list\{display:flex!important;flex-direction:column!important;width:100%/);
  assert.match(html, /\.occupancy-list>li\{display:block!important;width:100%!important/);
  assert.match(html, /SET_CUTTING_HEIGHT/);
  assert.match(html, /Mäherstatistiken/);
  assert.match(html, /Gemähte Rasenflächen · 7 Tage/);
  assert.match(html, /Mähzeit heute/);
  assert.match(html, /Ø Heimfahrdauer · 7 Tage/);
  assert.match(html, /id="water-stats-open"/);
  assert.match(html, /Beregnungsstatistiken/);
  assert.match(html, /Beregnungszeit · 7 Tage/);
  assert.match(html, /Vollständige Durchläufe · 7 Tage/);
  assert.match(html, /Zuletzt vollständig beregnet/);
  assert.match(html, /Letzte Beregnungsdauer/);
  assert.match(html, /Beregnungszeit je Zone · 7 Tage/);
  assert.match(html, /Planänderungen · 7 Tage/);
  assert.match(html, /function renderIrrigationStatistics\(stats\)/);
  assert.match(html, /id="water-plan-open"/);
  assert.match(html, /Beregnungsplan anpassen/);
  assert.match(html, /Nächste Beregnung aussetzen/);
  assert.match(html, /Beregnung pausieren/);
  assert.match(html, /Nächsten Lauf anpassen/);
  assert.match(html, /Automatik wieder aktivieren/);
  assert.match(html, /SKIP_NEXT_IRRIGATION/);
  assert.match(html, /PAUSE_IRRIGATION_UNTIL/);
  assert.match(html, /RESUME_IRRIGATION_SCHEDULE/);
  assert.match(html, /CUSTOMIZE_NEXT_IRRIGATION/);
  assert.match(html, /mindestens 45 Minuten sicheren Vorlauf/);
  assert.match(html, /Mindestens eine Zone muss aktiviert bleiben/);
  assert.match(html, /Jede Änderung wird erst nach vollständiger Hydrawise-Prüfung wirksam/);
  assert.match(html, /function renderIrrigationSchedule\(schedule,automation,controlsAvailable\)/);
  assert.match(html, /tatsächlich bestätigten Minutenzyklen und Zonenläufe/);
  assert.match(html, /function areaEquivalent\(value\)/);
  assert.doesNotMatch(html, /Gesamte Schneidezeit/);
  assert.doesNotMatch(html, /Ladezyklen/);
  assert.match(html, /Klingenlaufzeit zurücksetzen/);
  assert.match(html, /RESET_BLADE_USAGE/);
  assert.match(html, /Wurden die Klingen wirklich gewechselt/);
  assert.match(html, /Die Klingenlaufzeit wird bei Husqvarna zurückgesetzt/);
  assert.match(html, /function bladeResetFailed\(s\)/);
  assert.match(html, /Zurücksetzen fehlgeschlagen/);
  assert.match(html, /Die Klingenlaufzeit konnte bei Husqvarna nicht zurückgesetzt werden/);
  assert.match(html, /Empfohlen: mindestens 30 mm/);
  assert.match(html, /value<25/);
  assert.match(html, /dringend empfohlen/);
  assert.doesNotMatch(html, /type="range"/);
  assert.doesNotMatch(html, /cutting-height-range/);
  assert.match(html, /state\.heightChoice\+=delta/);
  assert.match(html, /changeHeight\(-1\)/);
  assert.match(html, /changeHeight\(1\)/);
  assert.match(html, /openAction\("SET_CUTTING_HEIGHT"/);
  assert.doesNotMatch(html, /mowe_forced/i);
  assert.doesNotMatch(html, /HUSQVARNA_CLIENT_SECRET|HYDRAWISE_API_KEY|SSV53_PLATZWART_PIN_HASH/);
});

test("EPOS-Suchzustand bleibt verbunden und wird als Satellitensuche angezeigt", () => {
  const names = ["isSearching(m)", "activity(m)"];
  const source = names.map((name) => html.split("\n").find((line) => line.includes(`function ${name}`))).join("\n");
  const getActivity = new Function(`${source}\nreturn {isSearching,activity};`)();
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
  assert.match(html, /disconnected\?"Getrennt":m\.connected===true\?"Verbunden"/);
});

test("Platzbelegung nutzt echte Zeiten und trennt Termine innerhalb eines Sperrblocks", () => {
  const names = ["localDay(value)", "calendarTime(v,referenceValue)", "occupancyItems(block)", "occupancySchedule(block)", "occupancyDisplayItems(block)", "occupancyName(block)", "occupancyWhen(block,current,reference)", "occupancyBlockWhen(block,current,reference)"];
  const source = names.map((name) => html.split("\n").find((line) => line.includes(`function ${name}`))).join("\n");
  const occupancy = new Function(`var EVENT_TIME_ZONE="Europe/Berlin";\n${source}\nreturn {occupancyName,occupancyWhen,occupancyDisplayItems,occupancyBlockWhen};`)();
  const irrigation = {
    start: "2026-08-21T01:45:00Z",
    end: "2026-08-21T05:00:00Z",
    source: "irrigation",
    title: "Beregnung Zone 1; Beregnung Zone 2",
    details: { items: [
      { start: "2026-08-21T01:45:00Z", end: "2026-08-21T02:55:00Z", details: { irrigation_start: "2026-08-21T02:15:00Z", irrigation_end: "2026-08-21T02:35:00Z" } },
      { start: "2026-08-21T02:05:00Z", end: "2026-08-21T03:25:00Z", details: { irrigation_start: "2026-08-21T02:35:00Z", irrigation_end: "2026-08-21T02:55:00Z" } },
    ] },
  };
  assert.equal(occupancy.occupancyName(irrigation), "Beregnung");
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
  assert.match(occupancy.occupancyBlockWhen(merged, false, "2026-08-20T20:00:00Z"), /^Sperrzeit: .*16:30 Uhr · bis 22:30 Uhr$/);
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
    /^Fr\., 19:30 Uhr$/
  );
});

test("Mäheraktionen sind für Fahren, Laden, Sperren und manuelle Bedienung eindeutig", () => {
  const searchingFunction = html.split("\n").find((line) => line.includes("function isSearching(m)"));
  const actionsFunction = html.split("\n").find((line) => line.includes("function mowerActions(s)"));
  assert.ok(searchingFunction);
  assert.ok(actionsFunction);
  const actions = new Function(`${searchingFunction}\n${actionsFunction}\nreturn mowerActions;`)();
  const safe = { available: true, fresh: true, clear_now: true };

  assert.deepEqual(
    { ...actions({ mower: { activity: "MOWING", connected: true, errorCode: 0 }, irrigation: { safety: safe }, automation: { continuousMowingOwned: true }, occupancy: {} }), startQuestion: undefined },
    { showPark: true, showStart: false, startLabel: "Mäher starten", startQuestion: undefined, occupancyOverrideKey: "" }
  );
  const searching = actions({ mower: { activity: "MOWING", displayActivity: "SEARCHING_FOR_POSITION", connected: true, errorCode: 0 }, irrigation: { safety: safe }, automation: { continuousMowingOwned: true }, occupancy: {} });
  assert.equal(searching.showPark, true);
  assert.equal(searching.showStart, false);
  const charging = actions({ mower: { activity: "CHARGING", batteryPercent: 70, connected: true, errorCode: 0 }, irrigation: { safety: safe }, automation: { continuousMowingOwned: true }, occupancy: {} });
  assert.equal(charging.showPark, false);
  assert.equal(charging.showStart, true);
  assert.equal(charging.startLabel, "Mäher starten");
  assert.match(charging.startQuestion, /lädt noch bei 70 %/);

  const blocked = actions({ mower: { activity: "CHARGING", connected: true, errorCode: 0 }, irrigation: { safety: safe }, automation: {}, occupancy: { current: { title: "Training" } } });
  assert.equal(blocked.showPark, false);
  assert.equal(blocked.showStart, false);

  const occupied = actions({ mower: { activity: "PARKED_IN_CS", connected: true, errorCode: 0 }, irrigation: { safety: safe }, automation: {}, occupancy: { current: { title: "Training A", start: "2026-08-20T18:00:00+02:00", end: "2026-08-20T20:00:00+02:00", source: "training" }, parking: { title: "Training A" } } });
  assert.equal(occupied.showStart, true);
  assert.equal(occupied.occupancyOverrideKey, "2026-08-20T18:00:00+02:00|2026-08-20T20:00:00+02:00|training");
  assert.match(occupied.startQuestion, /Beregnungs- und Gerätesperren bleiben aktiv/);

  const irrigationUnsafe = actions({ mower: { activity: "PARKED_IN_CS", connected: true, errorCode: 0 }, irrigation: { safety: { ...safe, clear_now: false } }, automation: {}, occupancy: { current: { title: "Training", start: "a", end: "b", source: "training" } } });
  assert.equal(irrigationUnsafe.showStart, false);

  const manual = actions({ overall: { code: "EXTERNAL_OVERRIDE" }, mower: { activity: "CHARGING", connected: true, errorCode: 0 }, irrigation: { safety: safe }, automation: {}, occupancy: { current: { title: "Training", start: "2026-08-20T18:00:00+02:00", end: "2026-08-20T20:00:00+02:00", source: "training" } } });
  assert.equal(manual.showStart, true);
  assert.equal(manual.startLabel, "Mäher starten");
  assert.ok(manual.occupancyOverrideKey);

  const irrigationBlock = actions({ mower: { activity: "PARKED_IN_CS", connected: true, errorCode: 0 }, irrigation: { safety: safe }, automation: {}, occupancy: { current: { title: "Mischblock", start: "a", end: "b", source: "training+irrigation" } } });
  assert.equal(irrigationBlock.showStart, false);

  const disconnected = actions({ mower: { activity: "MOWING", connected: false, errorCode: 0 }, irrigation: { safety: safe }, automation: {}, occupancy: {} });
  assert.equal(disconnected.showPark, false);
  assert.equal(disconnected.showStart, false);

  const displayOnly = actions({ controlsAvailable: false, mower: { activity: "MOWING", connected: true, errorCode: 0 }, irrigation: { safety: safe }, automation: { continuousMowingOwned: true }, occupancy: {} });
  assert.equal(displayOnly.showPark, false);
  assert.equal(displayOnly.showStart, false);
});

test("nächster Mäherstart unterscheidet Plan, Beregnung und Ladeschätzung", () => {
  const names = ["localDay(value)", "calendarTime(v,referenceValue)", "time(v)", "isSearching(m)", "nextMowerStart(s)"];
  const source = names.map((name) => html.split("\n").find((line) => line.includes(`function ${name}`))).join("\n");
  const nextStart = new Function(`var EVENT_TIME_ZONE="Europe/Berlin";\n${source}\nreturn nextMowerStart;`)();
  const generatedAt = "2026-08-20T16:25:00Z";
  const currentShortWindow = {
    start: "2026-08-20T16:00:00Z",
    command_deadline: "2026-08-20T16:50:00Z",
    minimum_mowing_minutes: 30,
  };
  const afterTraining = {
    start: "2026-08-20T19:00:00Z",
    command_deadline: "2026-08-20T20:50:00Z",
    minimum_mowing_minutes: 30,
  };
  const longCurrentWindow = {
    start: "2026-08-20T16:00:00Z",
    command_deadline: "2026-08-20T18:00:00Z",
    minimum_mowing_minutes: 30,
  };

  assert.equal(nextStart({ generatedAt, mower: { activity: "CHARGING", connected: true, errorCode: 0, batteryPercent: 99, restartBatteryPercent: 90 }, automation: { continuousMowingOwned: true }, occupancy: { safeWindows: [longCurrentWindow] } }), "Startet in Kürze");
  assert.match(nextStart({ generatedAt, mower: { activity: "CHARGING", connected: true, errorCode: 0, batteryPercent: 99, restartBatteryPercent: 90 }, automation: { continuousMowingOwned: true }, occupancy: { safeWindows: [currentShortWindow, afterTraining] } }), /^Nach Plan: .*21:00 Uhr$/);
  assert.match(nextStart({ generatedAt, mower: { activity: "CHARGING", connected: true, errorCode: 0, batteryPercent: 70, restartBatteryPercent: 90 }, automation: { continuousMowingOwned: true }, occupancy: { safeWindows: [longCurrentWindow] } }), /^Geschätzt: /);
  assert.match(nextStart({ generatedAt, mower: { activity: "CHARGING", connected: true, errorCode: 0, batteryPercent: 99, restartBatteryPercent: 90 }, automation: { continuousMowingOwned: true, hydrawiseClearSince: "2026-08-20T16:25:00Z", hydrawiseClearOrigin: "DATA_GAP" }, occupancy: { safeWindows: [longCurrentWindow] } }), /^Nach Sicherheitsprüfung: .*18:27 Uhr$/);
  assert.match(nextStart({ generatedAt, mower: { activity: "CHARGING", connected: true, errorCode: 0, batteryPercent: 99, restartBatteryPercent: 90 }, automation: { continuousMowingOwned: true, hydrawiseClearSince: "2026-08-20T16:25:00Z", hydrawiseClearOrigin: "IRRIGATION_END" }, occupancy: { safeWindows: [{ ...longCurrentWindow, command_deadline: "2026-08-20T20:00:00Z" }] } }), /^Nach Sicherheitsprüfung: .*20:25 Uhr$/);
  assert.equal(nextStart({ mower: { activity: "PARKED_IN_CS" }, automation: { irrigationPhase: "RUNNING" }, occupancy: {} }), "Nach Beregnungsende + 120 Min.");
  assert.equal(nextStart({ mower: { activity: "MOWING" }, automation: {}, occupancy: {} }), null);
  assert.equal(nextStart({ mower: { activity: "LEAVING" }, automation: {}, occupancy: {} }), null);
  assert.equal(nextStart({ mower: { activity: "GOING_HOME" }, automation: {}, occupancy: {} }), null);
  assert.equal(nextStart({ mower: { activity: "CHARGING", connected: false }, automation: {}, occupancy: {} }), null);
  assert.equal(nextStart({ overall: { code: "EXTERNAL_OVERRIDE" }, mower: { activity: "CHARGING", connected: true, errorCode: 0 }, automation: {}, occupancy: {} }), "Nach Einschalten der Automatik");
  assert.equal(nextStart({ mower: { activity: "CHARGING", connected: true, errorCode: 0, batteryPercent: null }, automation: {}, occupancy: {} }), "Wartet auf aktuellen Akkustand");
});

test("Beregnungsaktionen erscheinen nur im passenden Zustand", () => {
  const actionsFunction = html.split("\n").find((line) => line.includes("function irrigationActions(s)"));
  assert.ok(actionsFunction);
  const actions = new Function(`${actionsFunction}\nreturn irrigationActions;`)();
  const safe = { available: true, fresh: true };
  assert.deepEqual(actions({ automation: {}, irrigation: { safety: safe } }), { showStart: true, showStop: false });
  assert.deepEqual(actions({ automation: { irrigationPhase: "RUNNING" }, irrigation: { safety: safe } }), { showStart: false, showStop: true });
  assert.deepEqual(actions({ automation: { irrigationPhase: "COMPLETE_HOLD" }, irrigation: { safety: safe } }), { showStart: false, showStop: false });
  assert.deepEqual(actions({ automation: {}, irrigation: { safety: { available: false, fresh: false } } }), { showStart: false, showStop: false });
  assert.deepEqual(actions({ controlsAvailable: false, automation: {}, irrigation: { safety: safe } }), { showStart: false, showStop: false });
  assert.deepEqual(actions({ controlsAvailable: false, automation: { irrigationPhase: "RUNNING" }, irrigation: { safety: safe } }), { showStart: false, showStop: false });
});

test("Anzeige bleibt bei Sicherheitsplan- oder Lesefehlern bedienungslos verfügbar", () => {
  assert.match(html, /Nur Anzeige – Automatik gesperrt/);
  assert.match(html, /Daten momentan nicht verfügbar/);
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
