const assert = require("node:assert/strict");
const fs = require("node:fs");
const path = require("node:path");
const test = require("node:test");

const repositoryRoot = path.resolve(__dirname, "..");
const html = fs.readFileSync(
  path.join(repositoryRoot, "appack-platzbelegungsplan-azure.html"),
  "utf8"
);

function extractFunction(name) {
  const lines = html.split(/\r?\n/);
  const start = lines.findIndex((line) =>
    line.includes(`function ${name}(`)
  );
  assert.notEqual(start, -1, `Funktion ${name} fehlt in der Appack-Datei`);

  const end = lines.findIndex(
    (line, index) => index > start && /^      }$/.test(line)
  );
  assert.notEqual(end, -1, `Funktionsende von ${name} wurde nicht gefunden`);
  return lines.slice(start, end + 1).join("\n").trimStart();
}

function createHarness(contacts) {
  const state = { trainerContacts: contacts };
  const functionNames = [
    "normalizeMatchText",
    "addMatchesToSet",
    "extractTeamKeys",
    "extractContactTeamKeys",
    "extractEventTeamKeys",
    "reduceEventTeamKeys",
    "getEventContactProfile",
    "findTrainerContacts"
  ];
  const source = [
    "function plainTextFromHtml(value) {",
    "  return String(value || '').replace(/<[^>]*>/g, ' ');",
    "}",
    ...functionNames.map(extractFunction),
    "return { getEventContactProfile, findTrainerContacts };"
  ].join("\n\n");
  return new Function("state", source)(state);
}

function contact(name, key, sort = 0) {
  return { name, keys: new Set([key]), sort };
}

function event({ source, title, team = "", teamCategory = "", description = "" }) {
  return {
    title,
    start: new Date("2026-08-21T19:00:00+02:00"),
    extendedProps: {
      source,
      sourceType: source === "match" ? "official-match-feed" : "azure-occupancy",
      team,
      teamCategory,
      description,
      categories: []
    }
  };
}

test("Ü40-Pokalspiel zeigt ausschließlich den Ü40-Ansprechpartner", () => {
  const harness = createHarness([
    contact("Kontakt Ü40", "team:ue40"),
    contact("Kontakt Herren", "team:herren")
  ]);
  const match = event({
    source: "match",
    title: "Schönwalder SV (Ü40) – SG Bornim Ü40",
    team: "Schönwalder SV (Ü40)",
    description: "<p>Herren Ü40 · Kreispokal</p>"
  });

  assert.equal(harness.getEventContactProfile(match).eligible, true);
  assert.deepEqual(
    harness.findTrainerContacts(match).map((item) => item.name),
    ["Kontakt Ü40"]
  );
});

test("E2-Heimspiel wertet keine Jugendkennung des Gegners aus", () => {
  const harness = createHarness([
    contact("Kontakt E2", "team:e2"),
    contact("Kontakt D1", "team:d1")
  ]);
  const match = event({
    source: "match",
    title: "Schönwalder SV E2 – Gegnerische D1",
    team: "Schönwalder SV E2",
    description: "<p>E-Junioren · Pokal</p>"
  });

  assert.deepEqual(
    harness.findTrainerContacts(match).map((item) => item.name),
    ["Kontakt E2"]
  );
});

test("Training E2 behält die bestehende Ansprechpartnerzuordnung", () => {
  const harness = createHarness([
    contact("Kontakt E2", "team:e2"),
    contact("Kontakt D1", "team:d1")
  ]);
  const training = event({
    source: "training",
    title: "E2",
    team: "E2"
  });

  assert.deepEqual(
    harness.findTrainerContacts(training).map((item) => item.name),
    ["Kontakt E2"]
  );
});

test("Platzsperre und Platzpflege bleiben von Ansprechpartnern ausgeschlossen", () => {
  const harness = createHarness([contact("Kontakt E2", "team:e2")]);
  const closure = event({
    source: "special",
    title: "Platzsperre wegen Rasenpflege",
    description: "<p>Pflegeveranstaltung</p>"
  });

  assert.equal(harness.getEventContactProfile(closure).eligible, false);
  assert.deepEqual(harness.findTrainerContacts(closure), []);
});

test("Heimspiel ohne eigenes team rät nicht anhand des Gegners", () => {
  const harness = createHarness([
    contact("Kontakt E2", "team:e2"),
    contact("Kontakt D1", "team:d1")
  ]);
  const match = event({
    source: "match",
    title: "Schönwalder SV E2 – Gegnerische D1",
    team: "",
    description: "<p>Punktspiel</p>"
  });

  assert.equal(harness.getEventContactProfile(match).eligible, false);
  assert.deepEqual(harness.findTrainerContacts(match), []);
});

test("Herren-Spiel ohne Workbook-Kontakt bleibt eligible und zeigt den Leerzustand", () => {
  const harness = createHarness([contact("Kontakt Ü40", "team:ue40")]);
  const match = event({
    source: "match",
    title: "Spielgemeinschaft Schönwalde-Perwenitz-Paaren – VfL Nauen II",
    team: "Spielgemeinschaft Schönwalde-Perwenitz-Paaren",
    teamCategory: "Herren | Kreisliga",
    description: "<p>Herren · Kreisliga</p>"
  });

  assert.equal(harness.getEventContactProfile(match).eligible, true);
  assert.deepEqual(harness.findTrainerContacts(match), []);
  assert.match(html, /setTrainerContactStatus\(\s*"Kein Ansprechpartner hinterlegt"/);
});
