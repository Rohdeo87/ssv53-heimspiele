const assert = require("node:assert/strict");
const fs = require("node:fs");
const path = require("node:path");
const test = require("node:test");
const {sourceOf, viewModel, snapshot} = require("./helpers/platzwart_template");

const html = fs.readFileSync(path.join(__dirname, "..", "appack-platzwart-dashboard.html"), "utf8");

function lastSourceOf(name) {
  const matches = html.split("\n").filter(line => line.startsWith(`    function ${name}(`));
  if (!matches.length) throw new Error(`Missing template function: ${name}`);
  return matches[matches.length - 1];
}

function element() {
  return {checked: false, textContent: "", classList: {add() {}, remove() {}, toggle() {}}};
}

function harness(manualControl) {
  const elements = Object.fromEntries([
    "manual-occupancy", "manual-drying", "manual-occupancy-label", "manual-drying-label",
    "manual-water-choice", "manual-confirmations", "manual-confirmation-error", "action-error",
    "confirm-title", "confirm-question",
  ].map(id => [id, element()]));
  const radios = [Object.assign(element(), {value: "MOWER"}), Object.assign(element(), {value: "IRRIGATION"})];
  const document = {
    getElementById(id) { return elements[id] || (elements[id] = element()); },
    querySelector(selector) { return selector === 'input[name="manual-water"]:checked' ? radios.find(item => item.checked) || null : null; },
    querySelectorAll(selector) { return selector === 'input[name="manual-water"]' ? radios : []; },
  };
  const state = {status: {...snapshot(), manualControl}, pendingAction: null};
  const requests = [];
  const dialog = {showModal() {}, close() { this.closed = true; }};
  const code = [
    ...["manualControlView", "mowerActionContext", "manualMowerActions", "mowerTelemetryFresh", "stationConfirmed", "deviceControlsOpen", "hasActiveMowerError", "mowerFaultNotice", "operatorActionPending"].map(sourceOf), sourceOf("manualControlNeedsConfirmation"),
    sourceOf("manualControlPayload"), lastSourceOf("manualControlPrepare"),
    lastSourceOf("submitManualControl"),
  ].join("\n");
  const view = new Function(
    "state", "document", "dialog", "crypto", "api", "waitForAction", "load", "friendlyError", "text",
    `${code}\nreturn {manualControlPrepare, submitManualControl};`,
  )(
    state, document, dialog, {randomUUID: () => "request-1"},
    (url, options) => { requests.push({url, options}); return Promise.resolve({manualControl}); },
    () => Promise.resolve(manualControl), () => Promise.resolve(manualControl), () => "Fehler", (id,value) => {document.getElementById(id).textContent=value;},
  );
  elements["confirm-go"] = element();
  elements["confirm-go"].onclick = () => view.submitManualControl();
  return {view, state, elements, radios, requests};
}

function manual(status, changes = {}) {
  return {
    enabled: true, status, title: "Serverzustand", message: "", contextToken: "context-original",
    sessionId: "session-1", epoch: 4, canStart: true, canPark: true, canResume: true,
    confirmations: {occupancyRequired: true, dryingRequired: true, waterChoiceRequired: true}, ...changes,
  };
}

test("Physical STOP removes dialog actions and a STOP received during confirmation sends nothing", async () => {
  const h=harness(manual("AUTOMATIC"));
  h.view.manualControlPrepare("PARK","APP");
  assert.equal(h.elements['confirm-title'].textContent,'In Station lassen');
  assert.match(h.elements['confirm-question'].textContent,/bis du ihn wieder freigibst/);
  h.state.status.mower.state='STOPPED';
  assert.equal(h.view.submitManualControl(),false);assert.equal(h.requests.length,0);
  for(const operation of ['START','PARK','RESUME'])assert.equal(h.view.manualControlPrepare(operation,'APP'),false);
});

test("Water conflict has the same plain label in action and confirmation", () => {
  const h=harness(manual('WAITING_WATER'));h.view.manualControlPrepare('START','APP');
  assert.equal(h.elements['confirm-title'].textContent,'Mähen oder bewässern');
  assert.match(h.elements['confirm-question'].textContent,/zwischen Mähen und Bewässern/);
});

test("START posts the flat action envelope with the frozen nested manual-control contract", async () => {
  const h = harness(manual("READY"));
  assert.equal(h.view.manualControlPrepare("START", "APP"), true);
  h.elements["manual-occupancy"].checked = true;
  h.elements["manual-drying"].checked = true;
  h.radios[0].checked = true;
  await h.elements["confirm-go"].onclick();
  assert.deepEqual(h.requests, [{
    url: "/action",
    options: {method: "POST", body: {
      action: "MANUAL_CONTROL", confirmation: "MANUAL_CONTROL", clientContractVersion: 3, requestId: "request-1",
      manualControl: {operation: "START", source: "APP", contextToken: "context-original", approveOccupancy: true, approveDrying: true, waterChoice: "MOWER", sessionId: "session-1"},
    }},
  }]);
});

test("missing START confirmation makes no POST", () => {
  const h = harness(manual("READY"));
  h.view.manualControlPrepare("START", "APP");
  assert.equal(h.elements["confirm-go"].onclick(), false);
  assert.equal(h.requests.length, 0);
});

test("PARK ignores hidden START-only confirmations even with occupancy and water", async () => {
  const h = harness(manual("READY"));
  h.view.manualControlPrepare("PARK", "APP");
  await h.elements["confirm-go"].onclick();
  assert.equal(h.requests.length, 1);
  assert.deepEqual(h.requests[0].options.body.manualControl, {
    operation: "PARK", source: "APP", contextToken: "context-original", approveOccupancy: false,
    approveDrying: false, waterChoice: null, sessionId: "session-1",
  });
});

test("a dialog keeps the displayed token and session even when status refreshes", async () => {
  const h = harness(manual("READY"));
  h.view.manualControlPrepare("START", "APP");
  h.state.status.manualControl.contextToken = "context-new";
  h.state.status.manualControl.sessionId = "session-new";
  h.elements["manual-occupancy"].checked = true;
  h.elements["manual-drying"].checked = true;
  h.radios[1].checked = true;
  await h.elements["confirm-go"].onclick();
  assert.equal(h.requests[0].options.body.manualControl.contextToken, "context-original");
  assert.equal(h.requests[0].options.body.manualControl.sessionId, "session-1");
});

test("WAITING_WATER reopens the existing START session as DECIDE", async () => {
  const h = harness(manual("WAITING_WATER", {sessionId: "existing-start"}));
  h.view.manualControlPrepare("START", "APP");
  h.elements["manual-occupancy"].checked = true;
  h.elements["manual-drying"].checked = true;
  h.radios[0].checked = true;
  await h.elements["confirm-go"].onclick();
  assert.equal(h.requests[0].options.body.manualControl.operation, "DECIDE");
  assert.equal(h.requests[0].options.body.manualControl.sessionId, "existing-start");
});

test("manual polling accepts current or superseding persisted epochs without claiming physical completion", () => {
  const source = [sourceOf("manualControlView"), sourceOf("manualControlResponseStable"), sourceOf("actionRequestPending")].join("\n");
  const pending = new Function(`${source}\nreturn actionRequestPending;`)();
  const expected = {enabled: true, status: "WAITING_WATER", sessionId: "session-1", epoch: 5};
  const same = {manualControl: {...expected, title: "völlig anderer Text", contextToken: "new"}};
  const changedEpoch = {manualControl: {...expected, epoch: 6}};
  assert.equal(pending(same, "MANUAL_CONTROL", "unrelated-request", true, expected), false);
  assert.equal(pending(changedEpoch, "MANUAL_CONTROL", "unrelated-request", true, expected), false);
  assert.equal(pending({manualControl:{...expected,status:"MANUAL_MOWING"}}, "MANUAL_CONTROL", "x", true, expected), false);
  assert.equal(pending({manualControl:{...expected,epoch:4}}, "MANUAL_CONTROL", "x", true, expected), true);
});

test("the main message reports confirmed manual mowing without masking water or occupancy holds", () => {
  const view = viewModel();
  const s = snapshot();
  s.mower.activity = "MOWING";
  s.automation.irrigationPhase = null;
  s.coordination.blockers = [];
  s.coordination.dryUntil = null;
  s.irrigation.safety.active_zone_count = 0;
  s.manualControl = {enabled: true, status: "MANUAL_MOWING"};
  assert.equal(view.dashboardMessage(s).title, "Manuell gestartet");
  assert.match(view.dashboardMessage(s).text, /nächsten Ladefahrt/);
  s.irrigation.safety.active_zone_count = 1;
  assert.equal(view.dashboardMessage(s).title, "Bewässerung läuft");
  s.irrigation.safety.active_zone_count = 0;
  s.occupancy.current = {start: s.generatedAt, end: "2026-09-09T12:00:00Z"};
  assert.equal(view.dashboardMessage(s).title, "Mäher bitte stoppen");
  s.manualControl.occupancyOverrideActive = true;
  assert.equal(view.dashboardMessage(s).title, "Manuell gestartet");
  s.coordination.dryUntil = "2026-09-09T14:00:00Z";
  assert.notEqual(view.dashboardMessage(s).title, "Manuell gestartet");
  s.manualControl.dryingOverrideActive = true;
  assert.equal(view.dashboardMessage(s).title, "Manuell gestartet");
  s.irrigation.safety.active_zone_count = 1;
  assert.equal(view.dashboardMessage(s).title, "Bewässerung läuft");
});


test("choosing irrigation does not ask the operator to claim a wet pitch is dry", async () => {
 const h=harness(manual("WAITING_WATER"));
 h.view.manualControlPrepare("START","APP");
 h.elements["manual-occupancy"].checked=true;
 h.radios[1].checked=true;
 await h.elements["confirm-go"].onclick();
 assert.equal(h.requests.length,1);
 assert.equal(h.requests[0].options.body.manualControl.approveDrying,false);
 assert.equal(h.requests[0].options.body.manualControl.waterChoice,"IRRIGATION");
});
