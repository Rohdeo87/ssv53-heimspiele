const assert = require("node:assert/strict");
const fs = require("node:fs");
const path = require("node:path");
const {spawnSync} = require("node:child_process");
const test = require("node:test");

const html = fs.readFileSync(path.join(__dirname, "..", "appack-platzbelegungsplan-azure.html"), "utf8");

function extractFunction(name) {
  const start = html.indexOf(`function ${name}(`);
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
    if (depth === 0) {
      const asyncPrefix = html.slice(start - 6, start) === "async " ? "async " : "";
      return asyncPrefix + html.slice(start, index + 1);
    }
  }
  throw new Error(`${name} ist unvollständig`);
}

function harness(fixedNow = "2026-08-21T22:05:00Z") {
  const now = new Date(fixedNow).getTime();
  class Clock extends Date {
    constructor(...args) { super(...(args.length ? args : [now])); }
    static now() { return now; }
  }
  const source = [
    `const requests = [];
const form = {elements: {}, reset() {
  Object.values(this.elements).forEach(field => { field.value = ""; });
}, querySelectorAll() { return []; }};
["belegungTitel", "belegungPlatz", "belegungStartDatum", "belegungStartZeit",
 "belegungEndeDatum", "belegungEndeZeit", "belegungBereich", "belegungBeschreibung"]
.forEach(name => { form.elements[name] = {value: "", focus() {}}; });
const state = {
  calendars: {rasen: {}, kunstrasen: {}}, canCreateTrainerOccupancies: true,
  canAdministerTrainerOccupancies: true, currentCreator: {id: "test-operator"},
  trainerOccupancyMoveEvent: null, currentDialogEvent: null
};
const elements = {
  trainerOccupancyForm: form, trainerOccupancyTitle: {},
  trainerOccupancySubmit: {disabled: false}, trainerOccupancyMove: {hidden: false},
  trainerOccupancyDialogLayer: {hidden: true},
  trainerOccupancyStatus: {dataset: {}, hidden: true, removeAttribute() { delete this.dataset.error; }}
};
const window = {crypto: {randomUUID() { return "fixture-command"; }},
  requestAnimationFrame(callback) { callback(); }, setTimeout() {}};
const document = {body: {classList: {add() {}}}};
const TRAINER_OCCUPANCY_API_URL = "https://fixture.invalid/occupancy";
const fetch = async (url, options) => {
  assert.equal(url, TRAINER_OCCUPANCY_API_URL);
  requests.push(JSON.parse(options.body));
  return {ok: true, status: 200, async json() { return {ok: true}; }};
};
function occupancyWriteHeaders() { return {}; }
async function requestActionConfirmation() { return true; }
async function loadTrainerContacts() {}
function mergeCreatorContact(value) { return value; }
function findCurrentCreatorWorkbookContact() { return null; }
function closeEventDetails() {}
function closeTrainerOccupancyDialog() { state.trainerOccupancyMoveEvent = null; }
function normalizeCssColor(value, fallback) { return value || fallback; }
function getContrastColor() { return "#fff"; }
function buildAzureEventDescription() { return ""; }`,
    ...[
      "normalizeOccupancyPerson", "getClubWallClockParts", "toClubWallClockDate",
      "parseClubDateTime", "mapAzureOccupancyEvent", "clubDateTimeValue",
      "setTrainerOccupancyDateTime", "readTrainerOccupancyDateTime",
      "suggestTrainerOccupancyEnd", "formatOccupancyConflicts",
      "openTrainerOccupancyMoveDialog", "openTrainerOccupancyDialog",
      "moveTrainerOccupancy", "saveTrainerOccupancy"
    ].map(extractFunction),
    `return {requests, form, state, elements, parseClubDateTime, toClubWallClockDate,
      mapAzureOccupancyEvent, readTrainerOccupancyDateTime, suggestTrainerOccupancyEnd,
      formatOccupancyConflicts, openTrainerOccupancyMoveDialog,
      openTrainerOccupancyDialog, saveTrainerOccupancy};`
  ].join("\n\n");
  return new Function("assert", "Date", source)(assert, Clock);
}

function setPeriod(runtime, start, end) {
  const fields = runtime.form.elements;
  [fields.belegungStartDatum.value, fields.belegungStartZeit.value] = start.split("T");
  [fields.belegungEndeDatum.value, fields.belegungEndeZeit.value] = end.split("T");
  fields.belegungTitel.value = "Zusatztraining";
  fields.belegungPlatz.value = "rasen";
}

function selectTraining(runtime, start, end) {
  runtime.state.currentDialogEvent = runtime.mapAzureOccupancyEvent({
    id: "training:fixture", resourceId: "rasen", source: "training",
    title: "C-Junioren", start, end
  });
  runtime.openTrainerOccupancyMoveDialog();
}

if (!process.env.SSV53_OCCUPANCY_TZ_CHILD) {
  for (const zone of ["UTC", "Europe/Berlin", "America/New_York"]) {
    test(`Anzeige- und Schreibvertrag auf Host ${zone}`, () => {
      const childEnv = {...process.env, TZ: zone, SSV53_OCCUPANCY_TZ_CHILD: "1"};
      delete childEnv.NODE_TEST_CONTEXT;
      const result = spawnSync(process.execPath, ["--test", "--test-reporter=tap", __filename], {
        encoding: "utf8", timeout: 30000,
        env: childEnv
      });
      assert.equal(result.status, 0, result.stdout + result.stderr);
      assert.match(result.stdout, /# fail 0/);
    });
  }
} else {
  for (const sample of [
    ["Sommer", "2026-08-21T18:30", "2026-08-21T19:30", "2026-08-21T16:30:00.000Z", "2026-08-21T17:30:00.000Z"],
    ["Winter", "2026-12-10T18:30", "2026-12-10T19:30", "2026-12-10T17:30:00.000Z", "2026-12-10T18:30:00.000Z"],
    ["Mitternacht", "2026-08-21T23:30", "2026-08-22T00:30", "2026-08-21T21:30:00.000Z", "2026-08-21T22:30:00.000Z"],
    ["Beginn der Sommerzeit", "2026-03-29T01:30", "2026-03-29T03:30", "2026-03-29T00:30:00.000Z", "2026-03-29T01:30:00.000Z"],
    ["Ende der Sommerzeit", "2026-10-25T01:30", "2026-10-25T03:30", "2026-10-24T23:30:00.000Z", "2026-10-25T02:30:00.000Z"],
    ["Abweichender US-Sommerzeitbeginn", "2026-03-08T02:30", "2026-03-08T03:30", "2026-03-08T01:30:00.000Z", "2026-03-08T02:30:00.000Z"]
  ]) {
    test(`Create sendet echte Berliner Zeitpunkte: ${sample[0]}`, async () => {
      const runtime = harness();
      setPeriod(runtime, sample[1], sample[2]);
      await runtime.saveTrainerOccupancy({preventDefault() {}});
      assert.equal(runtime.requests.length, 1);
      assert.equal(runtime.requests[0].action, "create");
      assert.equal(runtime.requests[0].start, sample[3]);
      assert.equal(runtime.requests[0].end, sample[4]);
    });
  }

  test("Move übernimmt echte Quellzeitpunkte und erkennt einen unveränderten Zeitraum", async () => {
    const runtime = harness();
    selectTraining(runtime, "2026-08-21T18:30:00+02:00", "2026-08-21T19:30:00+02:00");
    assert.equal(runtime.form.elements.belegungStartDatum.value, "2026-08-21");
    assert.equal(runtime.form.elements.belegungStartZeit.value, "18:30");
    assert.equal(runtime.state.trainerOccupancyMoveEvent.start.toISOString(), "2026-08-21T16:30:00.000Z");
    await runtime.saveTrainerOccupancy({preventDefault() {}});
    assert.equal(runtime.requests.length, 0);
    assert.match(runtime.elements.trainerOccupancyStatus.textContent, /Bitte Platz, Beginn oder Ende ändern/);
  });

  test("Move sendet bei Mitternachtswechsel dieselben UTC-Zeitpunkte auf jedem Host", async () => {
    const runtime = harness();
    selectTraining(runtime, "2026-08-21T18:30:00+02:00", "2026-08-21T19:30:00+02:00");
    setPeriod(runtime, "2026-08-21T23:30", "2026-08-22T00:30");
    await runtime.saveTrainerOccupancy({preventDefault() {}});
    assert.equal(runtime.requests.length, 1);
    assert.equal(runtime.requests[0].action, "move");
    assert.equal(runtime.requests[0].targetStart, "2026-08-21T21:30:00.000Z");
    assert.equal(runtime.requests[0].targetEnd, "2026-08-21T22:30:00.000Z");
  });

  for (const value of ["2026-03-29T02:30", "2026-10-25T02:30", "2026-02-30T17:00"]) {
    for (const operation of ["create", "move"]) {
      test(`${operation}: ungültige oder mehrdeutige Berliner Eingabe ${value} sendet nichts`, async () => {
        const runtime = harness();
        if (operation === "move") {
          selectTraining(runtime, "2026-08-21T18:30:00+02:00", "2026-08-21T19:30:00+02:00");
        }
        setPeriod(runtime, value, value.slice(0, 10) + "T04:00");
        assert.ok(Number.isNaN(runtime.readTrainerOccupancyDateTime(runtime.form, "belegungStart").getTime()));
        await runtime.saveTrainerOccupancy({preventDefault() {}});
        assert.equal(runtime.requests.length, 0);
        assert.match(runtime.elements.trainerOccupancyStatus.textContent, /Berliner Ortszeit/);
      });
    }
  }

  test("Endvorschlag erhält die echte Dauer über den Berliner Sommerzeitwechsel", () => {
    const runtime = harness();
    selectTraining(runtime, "2026-03-29T01:30:00+01:00", "2026-03-29T03:30:00+02:00");
    setPeriod(runtime, "2026-03-30T17:00", "2026-03-30T19:00");
    runtime.suggestTrainerOccupancyEnd();
    assert.equal(runtime.form.elements.belegungEndeZeit.value, "18:00");
  });

  test("Neuer Termin beginnt am Berliner Kalendertag, unabhängig vom Gerätedatum", () => {
    const runtime = harness("2026-08-21T22:05:00Z");
    runtime.openTrainerOccupancyDialog();
    assert.equal(runtime.form.elements.belegungStartDatum.value, "2026-08-22");
    assert.equal(runtime.form.elements.belegungStartZeit.value, "00:30");
    assert.equal(runtime.form.elements.belegungEndeZeit.value, "01:30");
  });

  test("Konflikttext verwendet Berliner Zeit und den Berliner Tag", () => {
    const runtime = harness();
    const text = runtime.formatOccupancyConflicts([{
      title: "Training", start: "2026-08-21T22:30:00Z", end: "2026-08-21T23:30:00Z"
    }]);
    assert.match(text, /22\.08\.26/);
    assert.match(text, /00:30/);
    assert.match(text, /01:30/);
  });

  test("US-DST-Lücke im lokalen Kalenderträger verschiebt keinen Termin stillschweigend", () => {
    const runtime = harness();
    const instant = "2026-03-08T01:30:00Z";
    assert.equal(runtime.parseClubDateTime("2026-03-08T02:30").toISOString(), "2026-03-08T01:30:00.000Z");
    if (process.env.TZ === "America/New_York") {
      assert.throws(() => runtime.toClubWallClockDate(instant), /Gerätezeitzone/);
      assert.throws(() => runtime.mapAzureOccupancyEvent({
        id: "training:us-dst", resourceId: "rasen", source: "training", title: "Training",
        start: instant, end: "2026-03-08T02:30:00Z"
      }), /Gerätezeitzone/);
    } else {
      const wall = runtime.toClubWallClockDate(instant);
      assert.equal(wall.getDate(), 8);
      assert.equal(wall.getHours(), 2);
      assert.equal(wall.getMinutes(), 30);
    }
  });

  test("Ein Termin über beide Vorkommen derselben Berliner Uhrzeit wird nicht als leer angezeigt", () => {
    const runtime = harness();
    assert.throws(() => runtime.mapAzureOccupancyEvent({
      id: "training:berlin-fold", resourceId: "rasen", source: "training", title: "Training",
      start: "2026-10-25T02:30:00+02:00", end: "2026-10-25T02:30:00+01:00"
    }), /Zeitumstellung nicht eindeutig darstellbar/);
  });
}
