const assert = require("node:assert/strict");
const fs = require("node:fs");
const path = require("node:path");
const test = require("node:test");
const vm = require("node:vm");

const html = fs.readFileSync(
  path.join(__dirname, "..", "appack-platzbelegungsplan-hilfe.html"),
  "utf8"
);

test("Hilfeseite ist vollständiges und syntaktisch gültiges Appack-HTML", () => {
  assert.match(html, /^<!DOCTYPE html>/);
  assert.match(html, /<title>\$\{userTitle\}<\/title>/);
  assert.match(html, /workspace\/styles\.css/);
  assert.match(html, /workspace\/app-color\.css/);
  const scripts = Array.from(html.matchAll(/<script(?![^>]*\bsrc=)[^>]*>([\s\S]*?)<\/script>/gi));
  assert.equal(scripts.length, 1);
  assert.doesNotThrow(() => new vm.Script(scripts[0][1], { filename: "appack-help.js" }));
});

test("Hilfeseite erklärt Bedienung, DFB-Daten und die drei Spielzeiten", () => {
  assert.match(html, /Belegungsplan bedienen/);
  assert.match(html, /Sommer\/Winter/);
  assert.match(html, /automatisch aus den strukturierten DFB-Daten übernommen/);
  assert.match(html, /Anstoß/);
  assert.match(html, /nominelle Spielzeit/);
  assert.match(html, /Platz gesperrt/);
  assert.match(html, /60 Minuten vor dem Anstoß bis 60 Minuten nach/);
});

test("Hilfeseite dokumentiert Kontakte und Trainingsabsage vollständig", () => {
  assert.match(html, /ausschließlich die <strong>eigene Mannschaft<\/strong>/);
  assert.match(html, /Kein Ansprechpartner hinterlegt/);
  assert.match(html, /Trainer\*in \/ Mitarbeiter\*in/);
  assert.match(html, /roten Button <strong>„Training absagen“<\/strong>/);
  assert.match(html, /Absage widerrufen/);
  assert.match(html, /frühestens 30 Minuten nach der Absage/);
  assert.match(html, /Abgesagt/);
});

test("Hilfeseite beschreibt Mäherpuffer und manuelle Bedienung korrekt", () => {
  assert.match(html, /spätestens 30 Minuten vor Trainingsbeginn/);
  assert.match(html, /30 Minuten nach Trainingsende/);
  assert.match(html, /spätestens 60 Minuten vor dem Anstoß/);
  assert.match(html, /60 Minuten nach dem berechneten Spielende/);
  assert.match(html, /Mäher möglichst nicht manuell stoppen oder parken/);
  assert.match(html, /Beregnungszeiten werden unabhängig vom Belegungsplan überwacht/);
});

test("Hilfeseite hat zugängliche Akkordeons und keine Datenzugriffe", () => {
  const toggles = Array.from(html.matchAll(/class="accordion-toggle"/g));
  assert.equal(toggles.length, 7);
  assert.equal(Array.from(html.matchAll(/aria-controls="help-panel-/g)).length, 7);
  assert.equal(Array.from(html.matchAll(/role="region"/g)).length, 7);
  assert.match(html, /button\.setAttribute\("aria-expanded"/);
  assert.match(html, /panel\.setAttribute\("aria-hidden"/);
  assert.doesNotMatch(html, /\bfetch\s*\(/);
  assert.doesNotMatch(html, /azurewebsites\.net/);
});
