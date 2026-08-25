const assert = require("node:assert/strict");
const fs = require("node:fs");
const path = require("node:path");
const {spawnSync} = require("node:child_process");
const test = require("node:test");

const html = fs.readFileSync(
  path.join(__dirname, "..", "appack-platzbelegungsplan-azure.html"),
  "utf8"
);
const productionMatches = JSON.parse(fs.readFileSync(
  path.join(__dirname, "..", "public", "matches.json"),
  "utf8"
)).matches;

function extractFunction(name) {
  const marker = `function ${name}(`;
  const start = html.indexOf(marker);
  assert.notEqual(start, -1, `${name} fehlt`);
  const bodyStart = html.indexOf("{", start);
  let depth = 0;
  let quote = "";
  let escaped = false;
  for (let index = bodyStart; index < html.length; index += 1) {
    const char = html[index];
    if (quote) {
      if (escaped) escaped = false;
      else if (char === "\\") escaped = true;
      else if (char === quote) quote = "";
      continue;
    }
    if (char === "'" || char === '"' || char === "`") {
      quote = char;
      continue;
    }
    if (char === "{") depth += 1;
    if (char === "}") depth -= 1;
    if (depth === 0) return html.slice(start, index + 1);
  }
  throw new Error(`${name} ist unvollständig`);
}

function harness() {
  const source = [
    "const state = { calendars: { rasen: { color: '#285ea7', textColor: '#fff' }, kunstrasen: { color: '#285ea7', textColor: '#fff' } } };",
    "function normalizeCssColor(value, fallback) { return value || fallback; }",
    "function getContrastColor() { return '#fff'; }",
    "function buildAzureEventDescription(item) { return item.description || ''; }",
    extractFunction("normalizeOccupancyPerson"),
    extractFunction("toClubWallClockDate"),
    extractFunction("mapAzureOccupancyEvent"),
    extractFunction("enforceOccupancyGeometry"),
    extractFunction("getVisibleEventTimes"),
    extractFunction("getOccupancyEventTimes"),
    extractFunction("getCalendarEventTimeText"),
    extractFunction("formatTime"),
    extractFunction("formatTimeRange"),
    extractFunction("getPopupTimeText"),
    "return { normalizeOccupancyPerson, mapAzureOccupancyEvent, enforceOccupancyGeometry, getVisibleEventTimes, getCalendarEventTimeText, getPopupTimeText };"
  ].join("\n\n");
  return new Function(source)();
}

test("Bedienelemente wechseln nur bei echtem Überlauf in den Großtextmodus", () => {
  assert.match(html, /html\.ssv-large-text #booking-controls/);
  assert.match(html, /html\.ssv-large-text #calendar-navigation/);
  assert.match(html, /html\.ssv-large-text \.dialog-footer/);
  assert.match(html, /html\.ssv-large-text \.trainer-occupancy-datetime-group/);
  assert.match(html, /MutationObserver/);
  assert.match(html, /document\.fonts\.ready/);
  assert.match(html, /orientationchange/);
  const detector = extractFunction("ssvNeedsLargeTextLayout");
  assert.doesNotMatch(detector, /fc-event|event-content|event-title/);

  const source = [extractFunction("ssvElementVisible"), extractFunction("ssvElementOverflows")].join("\n");
  const overflow = new Function(
    `var window={getComputedStyle:function(){return {visibility:"visible"}}};\n${source}\nreturn ssvElementOverflows;`
  )();
  const element = {
    isConnected: true,
    clientWidth: 100,
    clientHeight: 44,
    scrollWidth: 100,
    scrollHeight: 44,
    getBoundingClientRect() { return {width: 100, height: 44}; }
  };
  assert.equal(overflow(element), false);
  assert.equal(overflow({...element, scrollHeight: 48}), true);
});

test("Azure-Zeiten werden auf allen Geräten als Berliner Vereinszeit angezeigt", () => {
  const helper = extractFunction("toClubWallClockDate");
  const script = [
    helper,
    "const value = toClubWallClockDate('2026-08-20T18:30:00+02:00');",
    "process.stdout.write([value.getFullYear(), value.getMonth() + 1, value.getDate(), value.getHours(), value.getMinutes()].join('-'));"
  ].join("\n");
  const result = spawnSync(process.execPath, ["-e", script], {
    encoding: "utf8",
    env: {...process.env, TZ: "America/New_York"}
  });
  assert.equal(result.status, 0, result.stderr);
  assert.equal(result.stdout, "2026-8-20-18-30");
});

test("Spiel blockiert occupancyStart bis occupancyEnd, zeigt aber start bis end", () => {
  const api = harness();
  const mapped = api.mapAzureOccupancyEvent({
    id: "match:1",
    resourceId: "rasen",
    source: "match",
    title: "Schönwalder SV (Ü40) – SG Bornim Ü40",
    team: "Schönwalder SV (Ü40)",
    start: "2026-08-21T19:00:00+02:00",
    end: "2026-08-21T20:30:00+02:00",
    occupancyStart: "2026-08-21T18:00:00+02:00",
    occupancyEnd: "2026-08-21T21:30:00+02:00",
    matchDurationMinutes: 90,
    durationRule: "fk-havelland-ue40-2026-27-dfb-standard",
    competitionFormat: "cup"
  });

  assert.equal(mapped.start.toISOString(), "2026-08-21T16:00:00.000Z");
  assert.equal(mapped.end.toISOString(), "2026-08-21T19:30:00.000Z");
  assert.equal(mapped.extendedProps.eventKind, "match");
  assert.equal(Object.hasOwn(mapped, "source"), false);
  const visible = api.getVisibleEventTimes({
    start: mapped.start,
    end: mapped.end,
    extendedProps: mapped.extendedProps
  });
  assert.equal(visible.start.toISOString(), "2026-08-21T17:00:00.000Z");
  assert.equal(visible.end.toISOString(), "2026-08-21T18:30:00.000Z");
  assert.equal(
    api.getPopupTimeText({ start: mapped.start, end: mapped.end, extendedProps: mapped.extendedProps }),
    "Anstoß: 19:00 Uhr · Spielzeit: 19:00–20:30 Uhr · Platz gesperrt: 18:00–21:30 Uhr"
  );
  assert.equal(
    api.getCalendarEventTimeText({
      start: mapped.start,
      end: mapped.end,
      extendedProps: mapped.extendedProps
    }),
    "Anstoß 19:00 Uhr"
  );
});

test("FullCalendar-Transformation erzwingt occupancyStart bis occupancyEnd", () => {
  const api = harness();
  const transformed = api.enforceOccupancyGeometry({
    start: "2026-08-21T19:00:00+02:00",
    end: "2026-08-21T20:30:00+02:00",
    extendedProps: {
      eventKind: "match",
      sourceType: "official-match-feed",
      occupancyStart: "2026-08-21T18:00:00+02:00",
      occupancyEnd: "2026-08-21T21:30:00+02:00"
    }
  });

  assert.equal(transformed.start.toISOString(), "2026-08-21T16:00:00.000Z");
  assert.equal(transformed.end.toISOString(), "2026-08-21T19:30:00.000Z");
  assert.match(html, /eventDataTransform:\s*enforceOccupancyGeometry/);
});

test("reale C- und D-Juniorenspiele behalten jeweils Spiel- und Sperrzeit", () => {
  const api = harness();
  const samples = ["C-Junioren", "D-Junioren"].map((category) =>
    productionMatches.find((item) => item.teamCategory.startsWith(category))
  );
  assert.ok(samples.every(Boolean), "C- und D-Produktionsbeispiele fehlen");

  samples.forEach((item) => {
    const mapped = api.mapAzureOccupancyEvent({
      ...item,
      resourceId: item.place,
      source: "match"
    });
    assert.equal(mapped.start.toISOString(), new Date(item.occupancyStart).toISOString());
    assert.equal(mapped.end.toISOString(), new Date(item.occupancyEnd).toISOString());
    const event = {
      start: mapped.start,
      end: mapped.end,
      extendedProps: mapped.extendedProps
    };
    assert.equal(
      api.getVisibleEventTimes(event).start.toISOString(),
      new Date(item.start).toISOString()
    );
    assert.match(api.getPopupTimeText(event), /Platz gesperrt:/);
  });
});

test("Training behält seine echte Kalendergeometrie", () => {
  const api = harness();
  const mapped = api.mapAzureOccupancyEvent({
    id: "training:1",
    resourceId: "rasen",
    source: "training",
    title: "E2",
    start: "2026-08-21T17:00:00+02:00",
    end: "2026-08-21T18:30:00+02:00"
  });
  assert.equal(mapped.start.toISOString(), "2026-08-21T15:00:00.000Z");
  assert.equal(mapped.end.toISOString(), "2026-08-21T16:30:00.000Z");
});

test("öffentliche Erstellerdaten werden vor der Kalenderanzeige strikt minimiert", () => {
  const api = harness();
  const mapped = api.mapAzureOccupancyEvent({
    id: "one-off:trainer-privacy",
    resourceId: "rasen",
    source: "special",
    title: "Zusatztraining",
    start: "2026-08-21T17:00:00+02:00",
    end: "2026-08-21T18:00:00+02:00",
    creator: {
      id: "trainer-17",
      name: "Marco Hartwig",
      role: "Trainer A",
      contactRef: "opaque:trainer-17",
      email: "privat@example.de",
      mobile: "+49 170 1234567",
      image: "data:image/jpeg;base64,privat"
    },
    movedBy: {
      id: "admin-1",
      name: "App Administrator",
      role: "App-Administrator",
      phone: "03322 12345",
      chatId: "private-chat"
    }
  });

  assert.deepEqual(mapped.extendedProps.creator, {
    id: "trainer-17",
    name: "Marco Hartwig",
    role: "Trainer A",
    contactRef: "opaque:trainer-17"
  });
  assert.deepEqual(mapped.extendedProps.movedBy, {
    id: "admin-1",
    name: "App Administrator",
    role: "App-Administrator",
    contactRef: ""
  });
  assert.equal(Object.hasOwn(mapped.extendedProps.creator, "email"), false);
  assert.equal(Object.hasOwn(mapped.extendedProps.creator, "mobile"), false);
  assert.equal(Object.hasOwn(mapped.extendedProps.creator, "image"), false);
  assert.equal(Object.hasOwn(mapped.extendedProps.movedBy, "phone"), false);
  assert.equal(Object.hasOwn(mapped.extendedProps.movedBy, "chatId"), false);
});

test("minimierte Appack-IDs und Rollen behalten die Backend-Vertragslänge", () => {
  const api = harness();
  const id = "i".repeat(180);
  const role = "r".repeat(180);
  const normalized = api.normalizeOccupancyPerson({id, name: "Test", role});
  assert.equal(normalized.id, id);
  assert.equal(normalized.role, role);
});

test("abgesagtes Training bleibt grau mit Status und stabiler Termin-ID sichtbar", () => {
  const api = harness();
  const mapped = api.mapAzureOccupancyEvent({
    id: "training:sommer:som-ras-c-mi:2026-08-26",
    resourceId: "rasen",
    source: "training",
    title: "C",
    start: "2026-08-26T17:30:00+02:00",
    end: "2026-08-26T19:00:00+02:00",
    cancelled: true
  });
  assert.equal(mapped.extendedProps.cancelled, true);
  assert.equal(
    mapped.extendedProps.occurrenceId,
    "training:sommer:som-ras-c-mi:2026-08-26"
  );
  assert.deepEqual(mapped.classNames, ["ssv-training-cancelled"]);
  assert.equal(mapped.color, "#8a8f8c");
  assert.match(html, /cancelledBadge\.textContent = "Abgesagt"/);
  assert.match(html, /"Absage widerrufen" : "Termin absagen"/);
  assert.match(html, /await requestActionConfirmation\([\s\S]*?question/);
  assert.doesNotMatch(html, /window\.confirm\s*\(/);
});

test("Absageaktionen gelten für reguläre und verlegte Trainings", () => {
  assert.match(
    html,
    /const regularTraining = props\.eventKind === "training"/
  );
  assert.match(
    html,
    /const relocatedTraining = props\.eventKind === "special"/
  );
  assert.match(html, /Boolean\(props\.replacesTrainingEventId\)/);
  assert.match(html, /TRAINING_FAELLT_AUS/);
  assert.match(html, /TRAINING_WIEDER_AKTIV/);
});

test("Spiel ohne gültigen Sicherheitsblock wird abgelehnt", () => {
  const api = harness();
  assert.throws(() => api.mapAzureOccupancyEvent({
    id: "match:unsafe",
    resourceId: "rasen",
    source: "match",
    title: "Unsicher",
    start: "2026-08-21T19:00:00+02:00",
    end: "2026-08-21T20:30:00+02:00"
  }), /ungültiger Platzbelegung/);
});

test("Spiel mit verkürztem Puffer wird abgelehnt", () => {
  const api = harness();
  assert.throws(() => api.mapAzureOccupancyEvent({
    id: "match:unsafe-buffer",
    resourceId: "rasen",
    source: "match",
    title: "Unsicher",
    start: "2026-08-21T19:00:00+02:00",
    end: "2026-08-21T20:30:00+02:00",
    occupancyStart: "2026-08-21T18:30:00+02:00",
    occupancyEnd: "2026-08-21T21:30:00+02:00"
  }), /60-Minuten-Puffer/);
});
