const assert = require("node:assert/strict");
const fs = require("node:fs");
const path = require("node:path");
const test = require("node:test");

const repositoryRoot = path.resolve(__dirname, "..");
const html = fs.readFileSync(
  process.env.CALENDAR_UI_SOURCE || path.join(repositoryRoot, "appack-platzbelegungsplan-azure.html"),
  "utf8"
);

function extractFunction(name) {
  const marker = `function ${name}(`;
  const start = html.indexOf(marker);
  assert.notEqual(start, -1, `Funktion ${name} fehlt in der Appack-Datei`);
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
  assert.fail(`Funktionsende von ${name} wurde nicht gefunden`);
}

function createHarness(contacts) {
  const state = { trainerContacts: contacts };
  const functionNames = [
    "normalizeMatchText",
    "addMatchesToSet",
    "extractTeamKeys",
    "extractContactTeamKeys",
    "hasTrainerFunctionMarker",
    "extractFunctionTeamKeys",
    "extractEventTeamKeys",
    "reduceEventTeamKeys",
    "getYouthYearsFromTeamKey",
    "collectContactValueTexts",
    "splitContactValues",
    "mapContactTopCategory",
    "isFootballContactCategory",
    "getContactCategoryProfile",
    "extractContactCategoryTeamKeys",
    "getContactFunctionValues",
    "getFirstContactText",
    "parseYouthTeamKey",
    "reconcileContactTeamKeys",
    "getContactIdentity",
    "firstContactValue",
    "parseWorkbookContacts",
    "parseTrainerContacts",
    "parseCreatorContacts",
    "findCreatorWorkbookContact",
    "getEventContactProfile",
    "findTrainerContacts"
  ];
  const source = [
    "function plainTextFromHtml(value) {",
    "  return String(value || '').replace(/<[^>]*>/g, ' ');",
    "}",
    ...functionNames.map(extractFunction),
    "function sanitizeDescription(value) { return String(value || ''); }",
    "return { parseTrainerContacts, parseCreatorContacts, getEventContactProfile, findTrainerContacts, findCreatorWorkbookContact };"
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
      eventKind: source,
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

test("C-Jugend-Kontakt aus hierarchischer Appack-Kategorie wird gefunden", () => {
  const parser = createHarness([]);
  const contacts = parser.parseTrainerContacts([{
    ansKat: "Fussball / Jugend / C",
    ansFunc: "Trainerin C-Jugend",
    ansName: "Kontakt C"
  }]);
  const harness = createHarness(contacts);
  const match = event({
    source: "match",
    title: "Schönwalder SV (9er) – Gast",
    team: "Schönwalder SV (9er)",
    teamCategory: "C-Junioren | 2.Kreisklasse"
  });

  assert.deepEqual(
    harness.findTrainerContacts(match).map((item) => item.name),
    ["Kontakt C"]
  );
});

test("C-Junioren finden die im ursprünglichen Code genutzte Jahrgangszuordnung", () => {
  const parser = createHarness([]);
  const contacts = parser.parseTrainerContacts([{
    ansKat: ["Fussball", "Jugend"],
    ansFunc: "Trainerin",
    ansInfo: "<p>Jahrgang 2012 / 2013</p>",
    ansName: "Kontakt C Jahrgang"
  }]);
  const harness = createHarness(contacts);
  const match = event({
    source: "match",
    title: "Schönwalder SV (9er) – Gast",
    team: "Schönwalder SV (9er)",
    teamCategory: "C-Junioren | 2.Kreisklasse"
  });

  assert.deepEqual(
    harness.findTrainerContacts(match).map((item) => item.name),
    ["Kontakt C Jahrgang"]
  );
});

test("D-Junioren finden Kontakt über D-Kategorie und Jahrgänge", () => {
  const parser = createHarness([]);
  const contacts = parser.parseTrainerContacts([
    {
      ansKat: ["Fussball", "Jugend", "D"],
      ansFunc: "Trainer",
      ansName: "Kontakt D"
    },
    {
      ansKat: ["Handball", "D-Jugend"],
      ansFunc: "Trainer D",
      ansName: "Falscher Handballkontakt"
    }
  ]);
  const harness = createHarness(contacts);
  const match = event({
    source: "match",
    title: "Schönwalder SV – Gast",
    team: "Schönwalder SV",
    teamCategory: "D-Junioren | 1. Kreisklasse"
  });

  assert.deepEqual(
    harness.findTrainerContacts(match).map((item) => item.name),
    ["Kontakt D"]
  );
});

test("Erstellerkontakt wird auch ohne Fußball-Mannschaft vollständig aufgelöst", () => {
  const parser = createHarness([]);
  const rows = [
    {
      ansKat: ["Fussball", "Herren", "Ü40"],
      ansFunc: "Trainer Ü40",
      ansName: "Kontakt Ü40"
    },
    {
      ansKat: ["Verein", "Organisation"],
      ansFunc: "Organisation",
      ansID: "69930689120a589bc451d1f6",
      ansName: "Julius Beispiel",
      ansMail: "julius@example.invalid",
      ansHandy: "+49 170 000000",
      ansImg: "https://cdn.appack.de/verein/julius.jpg"
    }
  ];

  const trainerContacts = parser.parseTrainerContacts(rows);
  const creatorContacts = parser.parseCreatorContacts(rows);
  const creator = creatorContacts.find((item) => item.name === "Julius Beispiel");

  assert.deepEqual(trainerContacts.map((item) => item.name), ["Kontakt Ü40"]);
  assert.ok(creator);
  assert.equal(creator.contactRef, "69930689120a589bc451d1f6");
  assert.ok(creator.contactRefs.has("69930689120a589bc451d1f6"));
  assert.equal(creator.role, "Organisation");
  assert.equal(creator.email, "julius@example.invalid");
  assert.equal(creator.mobile, "+49 170 000000");
  assert.equal(creator.image, "https://cdn.appack.de/verein/julius.jpg");
});

test("Match-Erkennung hängt nicht am reservierten FullCalendar-source-Feld", () => {
  const harness = createHarness([contact("Kontakt D", "team:d")]);
  const match = event({
    source: "match",
    title: "Schönwalder SV – Gast D1",
    team: "Schönwalder SV",
    teamCategory: "D-Junioren | Kreisliga"
  });
  assert.equal(Object.hasOwn(match.extendedProps, "source"), false);
  assert.deepEqual(
    harness.findTrainerContacts(match).map((item) => item.name),
    ["Kontakt D"]
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

// Replay the data shape of the 30 October incident with synthetic people/IDs.
const sharedReferenceRows = [
  { ansName: "Alex Beispiel", ansID: "shared-profile", ansFunc: "Trainer E2",
    ansKat: "Fussball; E2-Jugend", ansMail: "alex@example.invalid",
    ansHandy: "+49 170 0000001", ansImg: "https://example.invalid/alex.png" },
  { ansName: "Robin Muster", ansID: "shared-profile", ansFunc: "Trainer A",
    ansKat: "Fussball; A-Jugend", ansMail: "robin@example.invalid",
    ansImg: "https://example.invalid/robin.png" }
];

test("copied profile ID never merges different trainers or their contact details", () => {
  for (const rows of [sharedReferenceRows, sharedReferenceRows.slice().reverse()]) {
    const contacts = createHarness([]).parseTrainerContacts(rows);
    assert.equal(contacts.length, 2);
    const harness = createHarness(contacts);
    const aMatch = event({source: "match", title: "Verein – Gast", team: "Verein",
      teamCategory: "A-Junioren | Brandenburg-Pokal"});
    const [aTrainer] = harness.findTrainerContacts(aMatch);
    assert.equal(aTrainer.name, "Robin Muster");
    assert.equal(aTrainer.role, "Trainer A");
    assert.equal(aTrainer.email, "robin@example.invalid");
    assert.equal(aTrainer.mobile, "");
    assert.equal(aTrainer.image, "https://example.invalid/robin.png");
    assert.deepEqual(harness.findTrainerContacts(event({source: "training", title: "E2", team: "E2"}))
      .map(item => item.name), ["Alex Beispiel"]);
    for (const contact of contacts) {
      assert.equal(contact.chatId, "", "ambiguous ID must not link a chat to another person");
      assert.equal(contact.contactRef, "");
      assert.equal(contact.contactRefs.has("shared-profile"), false);
    }
  }
});

test("creator lookup does not accept an ambiguous profile reference", () => {
  const contacts = createHarness([]).parseCreatorContacts(sharedReferenceRows);
  const harness = createHarness(contacts);
  assert.equal(harness.findCreatorWorkbookContact({id: "shared-profile"}), null);
  const creator = harness.findCreatorWorkbookContact({id: "shared-profile", name: "Robin Muster"});
  assert.equal(creator.name, "Robin Muster");
  assert.equal(creator.role, "Trainer A");
});

test("profile conflict is detected across sports before football filtering", () => {
  const contacts = createHarness([]).parseTrainerContacts([
    sharedReferenceRows[1],
    {...sharedReferenceRows[0], ansKat: "Handball; E-Jugend"}
  ]);
  assert.equal(contacts.length, 1);
  assert.equal(contacts[0].name, "Robin Muster");
  assert.equal(contacts[0].chatId, "");
});

test("legitimate multiple roles of one person still merge with a working chat", () => {
  const contacts = createHarness([]).parseTrainerContacts([
    sharedReferenceRows[0],
    {...sharedReferenceRows[0], ansFunc: "Trainer A", ansKat: "Fussball; A-Jugend"}
  ]);
  assert.equal(contacts.length, 1);
  assert.equal(contacts[0].role, "Trainer E2; Trainer A");
  assert.equal(contacts[0].chatId, "shared-profile");
  assert.deepEqual([...contacts[0].keys].sort(), ["team:a", "team:e2"]);
});

test("correct distinct profile references retain their own role and chat", () => {
  const contacts = createHarness([]).parseTrainerContacts([
    sharedReferenceRows[0], {...sharedReferenceRows[1], ansID: "robin-profile"}
  ]);
  assert.equal(contacts.length, 2);
  assert.equal(contacts.find(c => c.name === "Robin Muster").chatId, "robin-profile");
  assert.equal(contacts.find(c => c.name === "Alex Beispiel").role, "Trainer E2");
});
