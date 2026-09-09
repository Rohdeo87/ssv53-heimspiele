const assert = require('node:assert/strict');
const test = require('node:test');
const { sourceOf, viewModel, snapshot } = require('./helpers/platzwart_template');
const view = viewModel();

function ready(code) {
  const s = snapshot();
  s.generatedAt = '2026-09-09T04:00:00Z';
  s.overall.code = code;
  s.automation.irrigationPhase = null;
  s.coordination.dryUntil = null;
  s.coordination.blockers = [];
  return s;
}

for (const suffix of ['RESERVED', 'SCHEDULED', 'REVALIDATION_FAILED', 'DISABLED_HOLD',
  'DISABLED_READY_HOLD', 'COMMAND_RESERVATION_FAILED', 'COMMAND_UNCERTAIN', 'START_BLOCKED', 'GAP_INVALID']) {
  test(`Ungeklärte Koordination ${suffix} bietet keine neue Freigabe an`, () => {
    const s = ready('COORDINATION_EXECUTION_' + suffix);
    assert.equal(view.nextMowerStart(s), 'Noch offen');
    assert.equal(view.nextWaterStart(s), 'Noch offen');
    assert.equal(view.effectiveMowerActions(s).showStart, false);
    assert.equal(view.irrigationActions(s).showStart, false);
    assert.doesNotMatch(view.dashboardMessage(s).text, /COORDINATION|API|CAS|JSON/);
    s.automation.irrigationPhase = 'RUNNING';
    s.irrigation.safety.active_zone_count = 1;
    assert.equal(view.irrigationActions(s).showStop, true);
    assert.equal(view.dashboardMessage(s).title, 'Bewässerung läuft');
  });
}

test('Bewegung auf einem gesperrten Platz hat Vorrang vor der Vormerkung', () => {
  for (const code of ['COORDINATION_EXECUTION_RESERVED', 'COORDINATION_EXECUTION_START_BLOCKED']) {
    const s = ready(code);
    s.mower.activity = 'MOWING';
    s.occupancy.current = { start: s.generatedAt, end: '2026-09-09T11:00:00Z' };
    assert.equal(view.dashboardMessage(s).title, 'Mäher bitte stoppen');
    assert.equal(view.effectiveMowerActions(s).showPark, true);
  }
});

test('Bestätigte Zonenpause zeigt die nächste vorgesehene Uhrzeit als Schätzung', () => {
  const s = ready('COORDINATION_EXECUTION_ZONE_GAP_WAIT');
  s.automation.irrigationPhase = 'READY';
  s.irrigationSchedule.override = { kind: 'CUSTOM_NEXT', status: 'EXECUTING', zones: [
    { start: '2026-09-09T03:45:00Z', selected: true },
    { start: '2026-09-09T04:20:00Z', selected: true },
  ] };
  assert.equal(view.nextWaterStart(s), 'Voraussichtlich Heute, 06:20 Uhr');
  assert.equal(view.dashboardMessage(s).title, 'Bewässerung macht Pause');
  assert.equal(view.irrigationActions(s).showStart, false);
  s.irrigation.safety.fresh = false;
  assert.equal(view.nextWaterStart(s), 'Noch offen');
});

test('Der tatsächliche Plan-Renderer sperrt konkurrierende Änderungen', () => {
  const nodes = new Map();
  function node() {
    return { children: [], value: '', classList: { toggle() {}, contains() { return false; } },
      querySelector: () => node(), appendChild(child) { this.children.push(child); } };
  }
  const document = { getElementById(id) { if (!nodes.has(id)) nodes.set(id, node()); return nodes.get(id); },
    querySelector: () => node(), createElement: () => node() };
  const renderer = new Function('document', 'view', `
    const { planDate, planStatusText, inputDateTime, coordinationExecutionBlocked } = view;
    const buildPlanZones=()=>{},setPlanProfile=()=>{},setPlanDateTime=()=>{};
    ${sourceOf('renderIrrigationSchedule')}
    return renderIrrigationSchedule;`)(document, view);
  for (const code of ['COORDINATION_EXECUTION_RESERVED', 'COORDINATION_EXECUTION_COMMAND_UNCERTAIN']) {
    const s = ready(code);
    s.irrigationSchedule.nextRun.zones = Array.from({ length: 7 }, (_, i) => ({ zone: i + 1 }));
    renderer(s.irrigationSchedule, s.automation, true, s);
    for (const id of ['plan-skip', 'plan-pause-open', 'plan-custom-open', 'plan-pause', 'plan-resume']) {
      assert.equal(nodes.get(id).disabled, true, id);
    }
  }
});
