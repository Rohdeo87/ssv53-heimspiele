const assert = require("node:assert/strict");
const fs = require("node:fs");
const path = require("node:path");
const {spawnSync} = require("node:child_process");
const test = require("node:test");

const html = fs.readFileSync(
  path.join(__dirname, "..", "appack-platzbelegungsplan-azure.html"),
  "utf8"
);
const productionMatches = JSON.parse(fs.readFileSync(
  path.join(__dirname, "..", "public", "matches.json"),
  "utf8"
)).matches;

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
    "const state = { calendars: { rasen: { color: '#285ea7', textColor: '#fff' }, kunstrasen: { color: '#285ea7', textColor: '#fff' } } };",
    "function normalizeCssColor(value, fallback) { return value || fallback; }",
    "function getContrastColor() { return '#fff'; }",
    "function buildAzureEventDescription(item) { return item.description || ''; }",
    extractFunction("normalizeOccupancyPerson"),
    extractFunction("getClubWallClockParts"),
    extractFunction("toClubWallClockDate"),
    extractFunction("mapAzureOccupancyEvent"),
    extractFunction("enforceOccupancyGeometry"),
    extractFunction("getVisibleEventTimes"),
    extractFunction("getOccupancyEventTimes"),
    extractFunction("getCalendarEventTimeText"),
    extractFunction("formatTime"),
    extractFunction("formatTimeRange"),
    extractFunction("getPopupTimeText"),
    "return { normalizeOccupancyPerson, mapAzureOccupancyEvent, enforceOccupancyGeometry, getVisibleEventTimes, getCalendarEventTimeText, getPopupTimeText };"
  ].join("\n\n");
  return new Function(source)();
}

function wallClock(date) {
  const pad = (value) => String(value).padStart(2, "0");
  return `${date.getFullYear()}-${pad(date.getMonth() + 1)}-${pad(date.getDate())}T${pad(date.getHours())}:${pad(date.getMinutes())}`;
}

test("Verbindlicher Trainingskalender zeigt beide Plätze ohne Saisonwahl", () => {
  assert.match(html, /<div class="ssv-control-group ssv-control-group--season" hidden>/, 'Saisonwahl ist schon vor dem Laden von JavaScript verborgen');
  const classes = new Map();
  const group = {hidden: true, style: {}, parentElement: {classList: {toggle: (key,value)=>classes.set(key,value)}}};
  const state = {activeSeason: "Winter", activeCalendarId: "kunstrasen", resources: []};
  const persisted = [];
  const ui = new Function("state", "document", "storageSet", [
    "const SUMMER_RESOURCE_IDS=['rasen','kunstrasen']; const WINTER_RESOURCE_IDS=['kunstrasen'];",
    "function storageKey(k){return k;} function ensureActiveCalendarSelection(){} function updateResources(){} function renderPlaceFilters(){}",
    extractFunction("getAllowedCalendarIds"), extractFunction("sharedTrainingSeason"), extractFunction("applySharedTrainingCalendar"), extractFunction("setSeason"),
    "return {applySharedTrainingCalendar, getAllowedCalendarIds, setSeason, sharedTrainingSeason};"
  ].join("\n"))(state, {querySelector: () => group}, (...args) => persisted.push(args));
  const activePayload = {training_calendar: {active: true, mode: "ACTIVE", trainingControl: {available: true, fail_closed: false, season: "Winter"}}};
  ui.applySharedTrainingCalendar(activePayload);
  assert.deepEqual(ui.getAllowedCalendarIds(), ["rasen", "kunstrasen"]);
  assert.equal(state.activeCalendarId, "all");
  assert.equal(group.hidden, true);
  assert.equal(group.style.display, "none");
  assert.equal(classes.get('ssv-season-choice'), false);
  ui.setSeason("Sommer");
  assert.equal(state.activeSeason, "Winter");
  state.activeCalendarId = "rasen";
  ui.applySharedTrainingCalendar(activePayload);
  assert.equal(state.activeCalendarId, "rasen");
  assert.equal(persisted.length, 1);
  ui.applySharedTrainingCalendar({training_calendar: {active: false}});
  assert.equal(group.hidden, false);
  assert.equal(group.style.display, "");
  assert.equal(classes.get('ssv-season-choice'), true);
  assert.deepEqual(ui.getAllowedCalendarIds(), ["kunstrasen"]);
});

test("Unklare Trainingstermine nennen den Platzwart und behalten die Sperre", () => {
  const text = new Function(extractFunction("getOccupancyFailureText") + ";return getOccupancyFailureText;")();
  assert.match(text({code: "TRAINING_SOURCE_UNAVAILABLE"}), /Platzwart kontaktieren/);
  assert.match(text({code: "TRAINING_SOURCE_UNAVAILABLE"}), /nicht freigeben/);
  assert.doesNotMatch(text({code: "TRAINING_SOURCE_UNAVAILABLE"}), /Azure|SHA|Envelope|Internet/);
  assert.match(text(new Error("raw technical details")), /erneut versuchen/);
  assert.doesNotMatch(text(new Error("raw technical details")), /raw technical/);
});

test("ACTIVE akzeptiert Request-Echo und übernimmt die persistente Backend-Saison", async () => {
  const state = {activeSeason: "Winter"};
  const payload = {
    data_source: "azure", season: "Winter", resources: [], events: [],
    training_calendar: {
      active: true, mode: "ACTIVE",
      trainingControl: {available: true, fail_closed: false, season: "Sommer"}
    }
  };
  const source = [
    "const OCCUPANCY_API_URL='https://example.invalid/occupancy'; const OCCUPANCY_FETCH_RETRIES=0; const OCCUPANCY_FETCH_TIMEOUT=1000; const OCCUPANCY_RETRY_DELAY=1;",
    "const window={};",
    extractFunction("formatApiDate"), extractFunction("sharedTrainingSeason"),
    extractFunction("getOccupancyRequestUrl"), "async "+extractFunction("fetchOccupancyPayload"),
    "return fetchOccupancyPayload;"
  ].join("\n");
  const fetchFn = async () => ({ok: true, json: async () => payload});
  const fetchPayload = await new Function("state", "fetch", "return (async function(){"+source+"})()")(state, fetchFn);
  const result = await fetchPayload("2026-09-10T00:00:00+02:00", "2026-09-11T00:00:00+02:00");
  assert.equal(result.season, "Winter");
  assert.equal(result.training_calendar.trainingControl.season, "Sommer");
});

test("Historische Trainingslücke bleibt als Hinweis sichtbar und verbirgt keine Spiele", () => {
  const warning = new Function(extractFunction("trainingHistoryWarning") + ";return trainingHistoryWarning;")();
  const payload = {
    training_calendar: {
      partial: true,
      unavailableRanges: [{
        start: "2026-09-01T00:00:00+02:00",
        end: "2026-09-10T00:00:00+02:00",
        reasonCode: "TRAINING_CONTROL_HISTORY_UNAVAILABLE",
        scope: "training"
      }]
    }
  };
  assert.equal(warning(payload), "Frühere Trainings bis 9. September sind hier nicht verfügbar. Aktuelle Termine werden angezeigt.");
  for (const [end, day] of [["2026-03-30T00:00:00+02:00", "29. März"], ["2026-10-26T00:00:00+01:00", "25. Oktober"]]) {
    const changed = JSON.parse(JSON.stringify(payload));
    changed.training_calendar.unavailableRanges[0].start = "2026-01-01T00:00:00+01:00";
    changed.training_calendar.unavailableRanges[0].end = end;
    assert.ok(warning(changed).includes("bis " + day + " sind"));
  }
  assert.equal(warning({training_calendar: {partial: false, unavailableRanges: []}}), "");
  assert.equal(warning({training_calendar: {partial: true, unavailableRanges: [{
    start: "2026-09-01T00:00:00+02:00", end: "2026-09-10T00:00:00",
    reasonCode: "TRAINING_CONTROL_HISTORY_UNAVAILABLE", scope: "training"
  }]}}), "");
  const combined = "Die Spieldaten werden gerade aktualisiert. Der letzte bestätigte Stand und bekannte Trainings bleiben sichtbar. "+warning(payload);
  assert.match(combined, /Spieldaten werden gerade aktualisiert/);
  assert.match(combined, /Frühere Trainings bis 9\. September/);
});

test("Bedienelemente wechseln nur bei echtem Überlauf in den Großtextmodus", () => {
  assert.match(html, /html\.ssv-large-text #booking-controls/);
  assert.match(html, /html\.ssv-large-text #calendar-navigation/);
  assert.match(html, /html\.ssv-large-text \.dialog-footer/);
  assert.match(html, /html\.ssv-large-text \.trainer-occupancy-datetime-group/);
  assert.match(html, /MutationObserver/);
  assert.match(html, /document\.fonts\.ready/);
  assert.match(html, /orientationchange/);
  const detector = extractFunction("ssvNeedsLargeTextLayout");
  assert.doesNotMatch(detector, /fc-event|event-content|event-title/);

  const source = [extractFunction("ssvElementVisible"), extractFunction("ssvElementOverflows")].join("\n");
  const overflow = new Function(
    `var window={getComputedStyle:function(){return {visibility:"visible"}}};\n${source}\nreturn ssvElementOverflows;`
  )();
  const element = {
    isConnected: true,
    clientWidth: 100,
    clientHeight: 44,
    scrollWidth: 100,
    scrollHeight: 44,
    getBoundingClientRect() { return {width: 100, height: 44}; }
  };
  assert.equal(overflow(element), false);
  assert.equal(overflow({...element, scrollHeight: 48}), true);
});

test("kurz verzögerte Spieldaten lassen den Kalender sichtbar", () => {
  assert.match(html, /payload\.match_source_fresh === false/);
  assert.match(html, /Der letzte bestätigte Stand und bekannte Trainings bleiben sichtbar/);
  assert.match(html, /#calendar-status\[data-state="warning"\]/);
});

test("Azure-Zeiten werden auf einem US-Gerät als Berliner Vereinszeit angezeigt", () => {
  const helper = extractFunction("toClubWallClockDate");
  const script = [
    extractFunction("getClubWallClockParts"),
    helper,
    "const value = toClubWallClockDate('2026-08-20T18:30:00+02:00');",
    "process.stdout.write([value.getFullYear(), value.getMonth() + 1, value.getDate(), value.getHours(), value.getMinutes()].join('-'));"
  ].join("\n");
  const result = spawnSync(process.execPath, ["-e", script], {
    encoding: "utf8",
    env: {...process.env, TZ: "America/New_York"}
  });
  assert.equal(result.status, 0, result.stderr);
  assert.equal(result.stdout, "2026-8-20-18-30");
});

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

  assert.equal(wallClock(mapped.start), "2026-08-21T18:00");
  assert.equal(wallClock(mapped.end), "2026-08-21T21:30");
  assert.equal(mapped.extendedProps.occupancyStart, "2026-08-21T16:00:00.000Z");
  assert.equal(mapped.extendedProps.occupancyEnd, "2026-08-21T19:30:00.000Z");
  assert.equal(mapped.extendedProps.eventKind, "match");
  assert.equal(Object.hasOwn(mapped, "source"), false);
  const visible = api.getVisibleEventTimes({
    start: mapped.start,
    end: mapped.end,
    extendedProps: mapped.extendedProps
  });
  assert.equal(wallClock(visible.start), "2026-08-21T19:00");
  assert.equal(wallClock(visible.end), "2026-08-21T20:30");
  assert.equal(
    api.getPopupTimeText({ start: mapped.start, end: mapped.end, extendedProps: mapped.extendedProps }),
    "Anstoß: 19:00 Uhr · Spielzeit: 19:00–20:30 Uhr · Platz gesperrt: 18:00–21:30 Uhr"
  );
  assert.equal(
    api.getCalendarEventTimeText({
      start: mapped.start,
      end: mapped.end,
      extendedProps: mapped.extendedProps
    }),
    "Anstoß 19:00 Uhr"
  );
});

test("FullCalendar-Transformation erzwingt occupancyStart bis occupancyEnd", () => {
  const api = harness();
  const transformed = api.enforceOccupancyGeometry({
    start: "2026-08-21T19:00:00+02:00",
    end: "2026-08-21T20:30:00+02:00",
    extendedProps: {
      eventKind: "match",
      sourceType: "official-match-feed",
      occupancyStart: "2026-08-21T18:00:00+02:00",
      occupancyEnd: "2026-08-21T21:30:00+02:00"
    }
  });

  assert.equal(wallClock(transformed.start), "2026-08-21T18:00");
  assert.equal(wallClock(transformed.end), "2026-08-21T21:30");
  assert.match(html, /eventDataTransform:\s*enforceOccupancyGeometry/);
});

test("reale C- und D-Juniorenspiele behalten jeweils Spiel- und Sperrzeit", () => {
  const api = harness();
  const samples = ["C-Junioren", "D-Junioren"].map((category) =>
    productionMatches.find((item) => item.teamCategory.startsWith(category))
  );
  assert.ok(samples.every(Boolean), "C- und D-Produktionsbeispiele fehlen");

  samples.forEach((item) => {
    const mapped = api.mapAzureOccupancyEvent({
      ...item,
      resourceId: item.place,
      source: "match"
    });
    assert.equal(wallClock(mapped.start), item.occupancyStart.slice(0, 16));
    assert.equal(wallClock(mapped.end), item.occupancyEnd.slice(0, 16));
    assert.equal(mapped.extendedProps.occupancyStart, new Date(item.occupancyStart).toISOString());
    assert.equal(mapped.extendedProps.occupancyEnd, new Date(item.occupancyEnd).toISOString());
    const event = {
      start: mapped.start,
      end: mapped.end,
      extendedProps: mapped.extendedProps
    };
    assert.equal(
      wallClock(api.getVisibleEventTimes(event).start),
      item.start.slice(0, 16)
    );
    assert.match(api.getPopupTimeText(event), /Platz gesperrt:/);
  });
});

test("Training behält seine Berliner Kalendergeometrie und den echten Zeitpunkt", () => {
  const api = harness();
  const mapped = api.mapAzureOccupancyEvent({
    id: "training:1",
    resourceId: "rasen",
    source: "training",
    title: "E2",
    start: "2026-08-21T17:00:00+02:00",
    end: "2026-08-21T18:30:00+02:00"
  });
  assert.equal(wallClock(mapped.start), "2026-08-21T17:00");
  assert.equal(wallClock(mapped.end), "2026-08-21T18:30");
  assert.equal(mapped.extendedProps.displayStart, "2026-08-21T15:00:00.000Z");
  assert.equal(mapped.extendedProps.displayEnd, "2026-08-21T16:30:00.000Z");
});

test("öffentliche Erstellerdaten werden vor der Kalenderanzeige strikt minimiert", () => {
  const api = harness();
  const mapped = api.mapAzureOccupancyEvent({
    id: "one-off:trainer-privacy",
    resourceId: "rasen",
    source: "special",
    title: "Zusatztraining",
    start: "2026-08-21T17:00:00+02:00",
    end: "2026-08-21T18:00:00+02:00",
    creator: {
      id: "trainer-17",
      name: "Marco Hartwig",
      role: "Trainer A",
      contactRef: "opaque:trainer-17",
      email: "privat@example.de",
      mobile: "+49 170 1234567",
      image: "data:image/jpeg;base64,privat"
    },
    movedBy: {
      id: "admin-1",
      name: "App Administrator",
      role: "App-Administrator",
      phone: "03322 12345",
      chatId: "private-chat"
    }
  });

  assert.deepEqual(mapped.extendedProps.creator, {
    id: "trainer-17",
    name: "Marco Hartwig",
    role: "Trainer A",
    contactRef: "opaque:trainer-17"
  });
  assert.deepEqual(mapped.extendedProps.movedBy, {
    id: "admin-1",
    name: "App Administrator",
    role: "App-Administrator",
    contactRef: ""
  });
  assert.equal(Object.hasOwn(mapped.extendedProps.creator, "email"), false);
  assert.equal(Object.hasOwn(mapped.extendedProps.creator, "mobile"), false);
  assert.equal(Object.hasOwn(mapped.extendedProps.creator, "image"), false);
  assert.equal(Object.hasOwn(mapped.extendedProps.movedBy, "phone"), false);
  assert.equal(Object.hasOwn(mapped.extendedProps.movedBy, "chatId"), false);
});

test("minimierte Appack-IDs und Rollen behalten die Backend-Vertragslänge", () => {
  const api = harness();
  const id = "i".repeat(180);
  const role = "r".repeat(180);
  const normalized = api.normalizeOccupancyPerson({id, name: "Test", role});
  assert.equal(normalized.id, id);
  assert.equal(normalized.role, role);
});

test("abgesagtes Training bleibt grau mit Status und stabiler Termin-ID sichtbar", () => {
  const api = harness();
  const mapped = api.mapAzureOccupancyEvent({
    id: "training:sommer:som-ras-c-mi:2026-08-26",
    resourceId: "rasen",
    source: "training",
    title: "C",
    start: "2026-08-26T17:30:00+02:00",
    end: "2026-08-26T19:00:00+02:00",
    cancelled: true
  });
  assert.equal(mapped.extendedProps.cancelled, true);
  assert.equal(
    mapped.extendedProps.occurrenceId,
    "training:sommer:som-ras-c-mi:2026-08-26"
  );
  assert.deepEqual(mapped.classNames, ["ssv-training-cancelled"]);
  assert.equal(mapped.color, "#8a8f8c");
  assert.match(html, /cancelledBadge\.textContent = "Abgesagt"/);
  assert.match(html, /"Absage widerrufen" : "Termin absagen"/);
  assert.match(html, /await requestActionConfirmation\([\s\S]*?question/);
  assert.doesNotMatch(html, /window\.confirm\s*\(/);
});

test("Absageaktionen gelten für reguläre und verlegte Trainings", () => {
  assert.match(
    html,
    /const regularTraining = props\.eventKind === "training"/
  );
  assert.match(
    html,
    /const relocatedTraining = props\.eventKind === "special"/
  );
  assert.match(html, /Boolean\(props\.replacesTrainingEventId\)/);
  assert.match(html, /TRAINING_FAELLT_AUS/);
  assert.match(html, /TRAINING_WIEDER_AKTIV/);
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

test("Spiel mit verkürztem Puffer wird abgelehnt", () => {
  const api = harness();
  assert.throws(() => api.mapAzureOccupancyEvent({
    id: "match:unsafe-buffer",
    resourceId: "rasen",
    source: "match",
    title: "Unsicher",
    start: "2026-08-21T19:00:00+02:00",
    end: "2026-08-21T20:30:00+02:00",
    occupancyStart: "2026-08-21T18:30:00+02:00",
    occupancyEnd: "2026-08-21T21:30:00+02:00"
  }), /60-Minuten-Puffer/);
});
