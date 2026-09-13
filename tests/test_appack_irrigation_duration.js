const test = require('node:test');
const assert = require('node:assert/strict');
const {sourceOf, html} = require('./helpers/platzwart_template');
const format = new Function(['duration','minuteDuration','irrigationDuration'].map(sourceOf).join('\n') + '\nreturn irrigationDuration;')();

test('Bestätigte Bewässerung zeigt 2:40, eine Schätzung steht klar als ca. dabei', () => {
  assert.equal(format(160, false), '2 Std. 40 Min.');
  assert.equal(format(152, true), 'ca. 2 Std. 32 Min.');
  assert.equal(format(null, true), '–');
  assert.equal(format(0, false), '0 Min.');
});

test('Gesamtdauer, letzter Lauf und jede Zone verwenden die Kennzeichnung', () => {
  const source = sourceOf('renderIrrigationStatistics');
  assert.match(source, /irrigationDuration\(stats.wateringMinutes7d,stats.wateringDurationEstimated\)/);
  assert.match(source, /irrigationDuration\(stats.lastCompletedDurationMinutes,stats.lastCompletedDurationEstimated\)/);
  assert.match(source, /irrigationDuration\(zone.minutes,\(stats.durationEstimatedRelayIds/);
  assert.equal(html, require('node:fs').readFileSync(require('node:path').join(__dirname, '../appack-platzwart-dashboard.txt'), 'utf8'));
});
