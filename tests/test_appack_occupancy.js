const assert = require("node:assert/strict");
const fs = require("node:fs");
const path = require("node:path");
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
    extractFunction("mapAzureOccupancyEvent"),
    extractFunction("enforceOccupancyGeometry"),
    extractFunction("getVisibleEventTimes"),
    extractFunction("getOccupancyEventTimes"),
    extractFunction("getCalendarEventTimeText"),
    extractFunction("formatTime"),
    extractFunction("formatTimeRange"),
    extractFunction("getPopupTimeText"),
    "return { mapAzureOccupancyEvent, enforceOccupancyGeometry, getVisibleEventTimes, getCalendarEventTimeText, getPopupTimeText };"
  ].join("\n\n");
  return new Function(source)();
}

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
  assert.match(html, /window\.confirm\(question\)/);
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
