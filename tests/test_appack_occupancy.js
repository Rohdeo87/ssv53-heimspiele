const assert = require("node:assert/strict");
const fs = require("node:fs");
const path = require("node:path");
const test = require("node:test");

const html = fs.readFileSync(
  path.join(__dirname, "..", "appack-platzbelegungsplan-azure.html"),
  "utf8"
);

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
    "const state = { calendars: { rasen: { color: '#285ea7', textColor: '#fff' } } };",
    "function normalizeCssColor(value, fallback) { return value || fallback; }",
    "function getContrastColor() { return '#fff'; }",
    "function buildAzureEventDescription(item) { return item.description || ''; }",
    extractFunction("mapAzureOccupancyEvent"),
    extractFunction("getVisibleEventTimes"),
    "return { mapAzureOccupancyEvent, getVisibleEventTimes };"
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
  const visible = api.getVisibleEventTimes({
    start: mapped.start,
    end: mapped.end,
    extendedProps: mapped
  });
  assert.equal(visible.start.toISOString(), "2026-08-21T17:00:00.000Z");
  assert.equal(visible.end.toISOString(), "2026-08-21T18:30:00.000Z");
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
