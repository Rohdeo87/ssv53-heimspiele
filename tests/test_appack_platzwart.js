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
  assert.match(html, /id="mower-next-start"/);
  assert.match(html, /Geschätzt:/);
  assert.match(html, /Nach Plan:/);
  assert.match(html, /Nach Beregnungsende \+ 120 Min\./);
  assert.match(html, /startButton\.classList\.toggle\("hidden"/);
  assert.match(html, /parkButton\.classList\.toggle\("hidden"/);
  assert.match(html, /function mowerActions\(s\)/);
  assert.doesNotMatch(html, />Mäher sicher parken</);
  assert.doesNotMatch(html, />Sieben Zonen sicher starten</);
  assert.match(html, /Mäher nicht erreichbar/);
  assert.match(html, /Mäher sucht Verbindung/);
  assert.match(html, /mower-connection/);
  assert.match(html, /cutting-height-current/);
  assert.match(html, /SET_CUTTING_HEIGHT/);
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
    { showPark: true, showStart: false, startLabel: "Mäher starten", startQuestion: undefined }
  );
  const charging = actions({ mower: { activity: "CHARGING", batteryPercent: 70, connected: true, errorCode: 0 }, irrigation: { safety: safe }, automation: { continuousMowingOwned: true }, occupancy: {} });
  assert.equal(charging.showPark, false);
  assert.equal(charging.showStart, true);
  assert.equal(charging.startLabel, "Mäher starten");
  assert.match(charging.startQuestion, /lädt noch bei 70 %/);

  const blocked = actions({ mower: { activity: "CHARGING", connected: true, errorCode: 0 }, irrigation: { safety: safe }, automation: {}, occupancy: { current: { title: "Training" } } });
  assert.equal(blocked.showPark, false);
  assert.equal(blocked.showStart, false);

  const manual = actions({ overall: { code: "EXTERNAL_OVERRIDE" }, mower: { activity: "CHARGING", connected: true, errorCode: 0 }, irrigation: { safety: safe }, automation: {}, occupancy: { current: { title: "Training" } } });
  assert.equal(manual.showStart, true);
  assert.equal(manual.startLabel, "Automatik einschalten");

  const disconnected = actions({ mower: { activity: "MOWING", connected: false, errorCode: 0 }, irrigation: { safety: safe }, automation: {}, occupancy: {} });
  assert.equal(disconnected.showPark, false);
  assert.equal(disconnected.showStart, false);
});

test("nächster Mäherstart unterscheidet Plan, Beregnung und Ladeschätzung", () => {
  const names = ["localDay(value)", "calendarTime(v,referenceValue)", "time(v)", "nextMowerStart(s)"];
  const source = names.map((name) => html.split("\n").find((line) => line.includes(`function ${name}`))).join("\n");
  const nextStart = new Function(`var EVENT_TIME_ZONE="Europe/Berlin";\n${source}\nreturn nextMowerStart;`)();
  const future = new Date(Date.now() + 60 * 60000).toISOString();
  const later = new Date(Date.now() + 120 * 60000).toISOString();

  assert.match(nextStart({ mower: { activity: "CHARGING", batteryPercent: 70, nextStartAt: future }, automation: { continuousMowingOwned: true }, occupancy: {} }), /^Geschätzt: /);
  assert.match(nextStart({ mower: { activity: "PARKED_IN_CS" }, automation: {}, occupancy: { current: { end: later } } }), /^Nach Plan: /);
  assert.equal(nextStart({ mower: { activity: "PARKED_IN_CS" }, automation: { irrigationPhase: "RUNNING" }, occupancy: {} }), "Nach Beregnungsende + 120 Min.");
  assert.equal(nextStart({ mower: { activity: "MOWING" }, automation: {}, occupancy: {} }), "Bereits gestartet");
});
