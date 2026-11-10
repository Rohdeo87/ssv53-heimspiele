const assert = require("node:assert/strict");
const test = require("node:test");
const {html, sourceOf, viewModel, snapshot} = require("./helpers/platzwart_template");

function serviceStatus(changes = {}) {
  const s = snapshot();
  s.mowerService = {available: true, active: true, reason: "WINTER", label: "Winterpause", revision: 3};
  s.actionCapabilities = {SET_MOWER_SERVICE: {available: true}, STOP_IRRIGATION_NOW: {available: true}};
  s.coordination.blockers = [{code: "MOWER_TELEMETRY"}];
  s.mower = {...s.mower, connected: false, telemetryFresh: false};
  return {...s, ...changes};
}

test("expected winter absence shows Winterpause and no speculative mower start", () => {
  const v = viewModel(), s = serviceStatus();
  assert.deepEqual(v.nextStartInfo(s), {at: null, text: "Winterpause"});
  assert.equal(v.dashboardMessage(s).title, "Winterpause");
  for (const action of ["START_MOWING", "PARK_MOWER", "SET_CUTTING_HEIGHT", "RESET_BLADE_USAGE", "MANUAL_CONTROL"])
    assert.equal(v.deviceActionAllowed(s, action), false, action);
  assert.equal(v.deviceActionAllowed(s, "SET_MOWER_SERVICE"), true);
  assert.equal(v.deviceActionAllowed(s, "STOP_IRRIGATION_NOW"), true);
});

test("winter display keeps real water failures and unexpected mower movement visible", () => {
  const v = viewModel();
  assert.match(v.dashboardMessage(serviceStatus({automation: {irrigationPhase: "FAILED"}})).title, /Bewässerung prüfen/);
  assert.match(v.dashboardMessage(serviceStatus({irrigation: {safety: {available: false}}})).title, /Bewässerungsstand fehlt/);
  assert.match(v.dashboardMessage(serviceStatus({irrigation: {safety: {available: true, fresh: true, active_zone_count: 1}}})).title, /Bewässerung/);
  assert.match(v.dashboardMessage(serviceStatus({automation: {irrigationPhase: "READY"}})).title, /Bewässerungsablauf aktiv/);
  assert.match(v.dashboardMessage(serviceStatus({controlsAvailable: false, dataQuality: {code: "CONFIG_STALE"}})).title, /Belegungsplan prüfen/);
  const moving = serviceStatus();
  moving.mower = {...moving.mower, connected: true, telemetryFresh: true, activity: "MOWING"};
  moving.coordination.blockers = [];
  assert.equal(v.dashboardMessage(moving).tone, "bad");
  assert.match(v.dashboardMessage(moving).title, /trotz Wartung/);
});

test("missing or old-server switch state never permits a change", () => {
  const v = viewModel();
  assert.equal(v.mowerServiceView(snapshot()).known, false);
  assert.equal(v.deviceActionAllowed(snapshot(), "SET_MOWER_SERVICE"), false);
  assert.equal(v.mowerServiceView(serviceStatus({mowerService: {available: true, active: false}})).known, false);
});

test("switch opens confirmation with explicit intended state and server revision", () => {
  const requests = [];
  const state = {status: serviceStatus()};
  const document = {getElementById: () => ({value: "MAINTENANCE"})};
  const v = viewModel();
  const open = new Function("state", "document", "deviceActionAllowed", "openAction",
    `${sourceOf("mowerServiceView")}\n${sourceOf("openMowerService")}\nreturn openMowerService;`)(
    state, document, v.deviceActionAllowed, (...args) => requests.push(args));
  open();
  assert.equal(requests[0][0], "SET_MOWER_SERVICE");
  assert.match(requests[0][1], /wieder in Betrieb/);
  assert.match(requests[0][2], /Frische, fehlerfreie/);
  assert.deepEqual(requests[0][3], {mowerService: {enabled: false, reason: "WINTER", revision: 3}});
  state.status.mowerService.active = false;
  open();
  assert.match(requests[1][2], /keinen Stoppbefehl/);
  assert.deepEqual(requests[1][3], {mowerService: {enabled: true, reason: "MAINTENANCE", revision: 3}});
});

test("switch has accessible state on the separate maintenance page", () => {
  assert.match(html, /id="mower-service-switch"[^>]+role="switch"[^>]+aria-checked="false"/);
  assert.ok(html.includes('pfMove("mower-service-card",pfPages.maintenance)'));
  assert.ok(html.includes('document.getElementById("mower-service-switch").onclick=openMowerService'));
});
