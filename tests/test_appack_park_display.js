const assert = require("node:assert/strict");
const test = require("node:test");
const fs = require("node:fs");
const path = require("node:path");
const {sourceOf, viewModel, snapshot} = require("./helpers/platzwart_template");

function parked() {
  const s = snapshot();
  s.manualControl = {enabled: true, status: "MANUAL_PARKED", canStart: false, canPark: true, canResume: true,
    title: "Parken angefordert", message: "Bitte warten, bis der Mäher die Station erreicht hat.",
    epoch: 7, sessionId: "test-park", contextToken: "test-context"};
  s.automation = {};
  s.coordination.blockers = [{code: "MANUAL_STOP"}];
  s.mower = {...s.mower, mode: "MAIN_AREA", activity: "PARKED_IN_CS", state: "RESTRICTED",
    statusTimestamp: new Date(s.generatedAt).getTime() - 12000};
  return s;
}

test("station position is displayed independently of MAIN_AREA or HOME", () => {
  const v = viewModel();
  for (const mode of ["MAIN_AREA", "HOME"]) {
    const s = parked(); s.mower.mode = mode;
    assert.equal(v.dashboardMessage(s).title, "Mäher in Station");
    assert.match(v.dashboardMessage(s).text, /Automatik ist pausiert/);
  }
});

test("old docked observation is historical and does not refresh telemetry or allow a start", () => {
  const v = viewModel(), s = parked();
  s.mower.telemetryFresh = false;
  s.mower.statusAgeSeconds = 900;
  s.mower.statusTimestamp = new Date(s.generatedAt).getTime() - 900000;
  s.coordination.blockers.push({code: "MOWER_TELEMETRY"});
  const before = JSON.stringify(s);
  assert.equal(v.dashboardMessage(s).title, "Zuletzt in Station gemeldet");
  assert.match(v.dashboardMessage(s).text, /Mähermeldung:/);
  assert.equal(v.mowerTelemetryFresh(s), false);
  assert.equal(v.deviceActionAllowed(s, "START_MOWING"), false);
  assert.equal(JSON.stringify(s), before);
});

test("missing timestamp cannot become a fresh station confirmation", () => {
  const v = viewModel(), s = parked();
  s.mower.telemetryFresh = false; delete s.mower.statusTimestamp;
  assert.equal(v.manualParkPositionDisplay(s).title, "Automatik pausiert");
  assert.match(v.manualParkPositionDisplay(s).text, /nicht bestätigt/);
});

test("moving mower, uncertain session, start hold and errors do not get a parked message", () => {
  const v = viewModel();
  for (const change of [s => s.mower.activity = "GOING_HOME", s => s.manualControl.status = "UNKNOWN",
    s => s.automation.mowerStartOutcomeUnconfirmed = true, s => {s.mower.errorCode = 42; s.mower.state = "ERROR";}]) {
    const s = parked(); change(s);
    assert.equal(v.manualParkPositionDisplay(s), null);
    assert.notEqual(v.dashboardMessage(s).title, "Mäher in Station");
  }
});

test("pending or uncertain park request keeps its existing confirmation display", () => {
  const v = viewModel();
  for (const requestStatus of ["PENDING", "RESERVED", "SENT_UNCONFIRMED", "UNKNOWN"]) {
    const s = parked(); s.manualControl.requestStatus = requestStatus;
    assert.equal(v.manualParkPositionDisplay(s), null);
  }
});

test("future or invalid old report is not presented as a confirmed historical position", () => {
  const v = viewModel();
  for (const statusTimestamp of [0, "invalid", new Date("2026-09-10T10:00:00Z").getTime()]) {
    const s = parked(); s.mower.telemetryFresh = false; s.mower.statusTimestamp = statusTimestamp;
    assert.equal(v.manualParkPositionDisplay(s).title, "Automatik pausiert");
  }
});

test("watering and other higher-priority warnings remain visible", () => {
  const v = viewModel(), s = parked();
  s.irrigation.safety.active_zone_count = 1;
  s.irrigation.safety.clear_now = false;
  assert.match(v.dashboardMessage(s).title, /Bewässerung/);
  s.irrigation.safety.active_zone_count = 0;
  s.controlsAvailable = false;
  s.dataQuality = {code: "CONFIG_STALE"};
  assert.equal(v.dashboardMessage(s).title, "Belegungsplan nicht aktuell");
});

test("manual view correction preserves every control and confirmation field", () => {
  const display = fs.readFileSync(path.join(__dirname, "../ui/platzpflege/park-display.js"), "utf8");
  const v = viewModel();
  const raw = new Function(`${sourceOf("manualControlView")}\nreturn manualControlView;`)();
  const corrected = new Function("dashboardMessage", "mowerServiceView", "mowerTelemetryFresh", "hasActiveMowerError", "calendarTime",
    `${sourceOf("manualControlView")}\n${display}\nreturn manualControlView;`)(v.dashboardMessage, v.mowerServiceView, v.mowerTelemetryFresh, () => false, () => "Zeit");
  const s = parked(), before = raw(s), after = corrected(s);
  for (const key of Object.keys(before).filter(k => !["title", "message"].includes(k))) assert.deepEqual(after[key], before[key], key);
  assert.equal(after.title, "Mäher in Station");
  assert.match(after.message, /Automatik ist pausiert/);
});
