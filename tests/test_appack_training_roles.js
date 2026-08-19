const assert = require("node:assert/strict");
const fs = require("node:fs");
const path = require("node:path");
const test = require("node:test");

const html = fs.readFileSync(
  path.join(__dirname, "..", "appack-platzbelegungsplan-azure.html"),
  "utf8"
);

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

const api = new Function(
  "window",
  [
    extractFunction("normalizeAppackRoleText"),
    extractFunction("hasActiveTrainerRole"),
    extractFunction("hasActiveAppAdministratorRole"),
    extractFunction("getAppackProfileValue"),
    extractFunction("unwrapAppackImageValue"),
    extractFunction("getAppackProfileImage"),
    extractFunction("getCurrentAppackCreator"),
    "return { hasActiveTrainerRole, hasActiveAppAdministratorRole, getCurrentAppackCreator };"
  ].join("\n\n")
)({ console: { warn() {} } });

test("aktive technische Trainerrolle TR wird freigeschaltet", () => {
  assert.equal(api.hasActiveTrainerRole({ roleKeys: ["M", "TR"] }), true);
  assert.equal(
    api.hasActiveTrainerRole({ roles: [{ enumKey: "TR", value: "Trainer*in / Mitarbeiter*in" }] }),
    true
  );
});

test("aktive ausgeschriebene Trainer- oder Mitarbeiterrolle wird erkannt", () => {
  assert.equal(api.hasActiveTrainerRole({ roles: [{ value: "Trainerin" }] }), true);
  assert.equal(api.hasActiveTrainerRole({ roles: [{ value: "Mitarbeiter" }] }), true);
});

test("beantragte Rolle, Vorstand und fehlendes Profil bleiben verborgen", () => {
  assert.equal(api.hasActiveTrainerRole({ requestedRoles: [{ enumKey: "TR" }] }), false);
  assert.equal(api.hasActiveTrainerRole({ roleKeys: ["VS"] }), false);
  assert.equal(api.hasActiveTrainerRole({ roles: [{ enumKey: "VS", value: "Vorstand" }] }), false);
  assert.equal(api.hasActiveTrainerRole(null), false);
});

test("App-Administrator und Profildaten werden aus dem aktiven Appack-Profil gelesen", () => {
  assert.equal(api.hasActiveAppAdministratorRole({roles: [{value: "App-Administrator"}]}), true);
  const creator = api.getCurrentAppackCreator({
    id: "user-42",
    firstName: "Jule",
    lastName: "Beispiel",
    contact: {mobilePhone: "+49 170 123", email: "jule@example.de", chatId: "chat-42"},
    media: {profileImage: {downloadUrl: "https://cdn.appack.de/profile/user-42.jpg"}}
  });
  assert.deepEqual(creator, {
    id: "user-42", name: "Jule Beispiel", phone: "", mobile: "+49 170 123",
    email: "jule@example.de", chatId: "chat-42", image: "https://cdn.appack.de/profile/user-42.jpg"
  });
});

test("Belegungsplan nutzt Appacks profile_json und prüft auch den Handler", () => {
  assert.match(
    html,
    /var profileJSON = \[#if profile_json\?has_content\]\$\{profile_json\}\[#else\]\{\}\[\/#if\];/
  );
  assert.match(html, /canManageTrainingCancellations: hasActiveTrainerRole\(profileJSON\)/);
  assert.match(
    html,
    /!state\.canManageTrainingCancellations \|\|\s*!isTrainingCancellationEvent\(event\)/
  );
  assert.match(html, /id="training-cancellation-action"[\s\S]*?hidden/);
});

test("Trainingsabsage verwendet einen CORS-safelisted POST ohne Preflight", () => {
  const cancellationHandler = extractFunction("changeTrainingCancellation");
  assert.match(
    cancellationHandler,
    /"Content-Type": "text\/plain;charset=UTF-8"/
  );
  assert.doesNotMatch(
    cancellationHandler,
    /"Content-Type": "application\/json"/
  );
});

test("Absageaktion ist rot und zeigt zustandsabhängige Symbole", () => {
  assert.match(
    html,
    /\.dialog-button--training-action\s*\{[\s\S]*?background: #c6281e;/
  );
  assert.match(
    html,
    /training-cancellation-action-icon--cancel[\s\S]*?aria-hidden="true"/
  );
  assert.match(
    html,
    /training-cancellation-action-icon--restore[\s\S]*?aria-hidden="true"/
  );
  assert.match(
    html,
    /<span class="training-cancellation-action-label">Termin absagen<\/span>/
  );
  assert.match(
    extractFunction("renderTrainingCancellationAction"),
    /actionLabel\.textContent = cancelled \? "Absage widerrufen" : "Termin absagen"/
  );
});

test("Verlegen ist eine eigenständige blaue Aktion und nicht rot", () => {
  assert.match(
    html,
    /\.dialog-button--move-action\s*\{[\s\S]*?linear-gradient\([\s\S]*?#285ea7/
  );
  assert.match(
    html,
    /id="trainer-occupancy-move"[^>]*class="dialog-button dialog-button--move-action"/
  );
  assert.doesNotMatch(
    html,
    /id="trainer-occupancy-move"[^>]*dialog-button--training-action/
  );
});

test("nur Trainer können eine Appack-Belegung anlegen", () => {
  assert.match(html, /canCreateTrainerOccupancies: hasActiveTrainerRole\(profileJSON\)/);
  assert.match(html, /id="trainer-add-occupancy"[^>]*aria-label="Belegung hinzufügen"[^>]*hidden/);
  const opener = extractFunction("openTrainerOccupancyDialog");
  const saver = extractFunction("saveTrainerOccupancy");
  assert.match(opener, /!state\.canCreateTrainerOccupancies/);
  assert.match(saver, /!state\.canCreateTrainerOccupancies/);
  assert.match(saver, /fetch\(TRAINER_OCCUPANCY_API_URL/);
  assert.match(saver, /"Content-Type": "text\/plain;charset=UTF-8"/);
  assert.match(saver, /confirmation: "TRAINER_BELEGUNG_SPEICHERN"/);
  assert.match(saver, /overlapConfirmation = "UEBERSCHNEIDUNG_TROTZDEM_SPEICHERN"/);
  assert.match(html, /id="trainer-occupancy-delete"[^>]*hidden/);
  assert.match(html, /id="trainer-occupancy-move"[^>]*hidden/);
  const mover = extractFunction("moveTrainerOccupancy");
  assert.match(mover, /confirmation: "TRAINER_BELEGUNG_VERSCHIEBEN"/);
  assert.match(mover, /overlapConfirmation = "UEBERSCHNEIDUNG_TROTZDEM_SPEICHERN"/);
  assert.match(mover, /targetResource === "rasen"/);
});

test("Trainerbelegung schreibt nur validierte strukturierte Felder", () => {
  const saver = extractFunction("saveTrainerOccupancy");
  assert.match(saver, /end <= start \|\| end - start > maximumDuration/);
  assert.match(saver, /start: start\.toISOString\(\)/);
  assert.match(saver, /end: end\.toISOString\(\)/);
  assert.match(saver, /resourceId: resourceId/);
  assert.match(saver, /resourceId === "rasen"/);
  assert.match(html, /Kunstrasen-Einträge erscheinen in Sommer und Winter/);
  assert.match(html, /type="date" required/);
  assert.match(html, /type="time" step="300" required/);
  assert.match(html, /new Date\(start\.getTime\(\) \+ 60 \* 60 \* 1000\)/);
  assert.match(saver, /mergeCreatorContact/);
  assert.match(saver, /findCurrentCreatorWorkbookContact/);
  assert.match(html, />\s*Termin anlegen\s*<\/button>/);
  assert.match(html, /id="calendar-today-button"[^>]*>Heute<\/button>/);
  assert.match(html, /elements\.todayButton\.addEventListener\("click"/);
});

test("verlegte Trainings behalten Teamkontakte und zeigen die verschiebende Person separat", () => {
  const mapper = extractFunction("mapAzureOccupancyEvent");
  const profile = extractFunction("getEventContactProfile");
  const renderer = extractFunction("renderTrainerContactsForEvent");
  assert.match(mapper, /team: String\(item\.team \|\| ""\)/);
  assert.match(mapper, /movedBy: item\.movedBy/);
  assert.match(profile, /const structuredTeam = String\(props\.team \|\| ""\)\.trim\(\)/);
  assert.match(renderer, /Boolean\(props\.replacesTrainingEventId\)/);
  assert.match(renderer, /const isMovedSpecial/);
  assert.match(renderer, /Ursprüngliche Ansprechpartner\*innen/);
  assert.match(renderer, /Erstellt von/);
  assert.match(renderer, /Verlegt von/);
  assert.match(renderer, /findTrainerContacts\(event\)/);
  assert.match(renderer, /resolveProfileContact\(movedBy\)/);
  assert.match(extractFunction("buildAzureEventDescription"), /item\.replacesTrainingEventId/);
});

test("Kalender öffnet die heutige Woche und Heute behält die gewählte Ansicht", () => {
  const initialLoader = extractFunction("loadInitialData");
  const focusToday = extractFunction("focusTodayInCurrentView");
  const weekLanding = extractFunction("applyPendingWeekLanding");

  assert.match(html, /activeView: "resourceTimeGridWeek"/);
  assert.match(html, /const TODAY_AFTERNOON_SCROLL_TIME = "14:00:00"/);
  assert.match(html, /initialDate: new Date\(\)/);
  assert.match(initialLoader, /state\.activeView = "resourceTimeGridWeek"/);
  assert.match(initialLoader, /state\.pendingWeekLanding = "today"/);
  assert.doesNotMatch(focusToday, /changeView\(/);
  assert.match(focusToday, /normalizeCalendarViewType\(state\.calendar\.view\.type\)/);
  assert.match(focusToday, /gotoDate\(today\)/);
  assert.match(focusToday, /scrollToTime\(TODAY_AFTERNOON_SCROLL_TIME\)/);
  assert.match(weekLanding, /data-date=/);
  assert.match(weekLanding, /todayLeft \+ todayRight/);
  assert.match(html, /elements\.todayButton\.addEventListener\("click", function \(\) \{\s*focusTodayInCurrentView\(\)/);
});

test("Appack übermittelt keine Trainer-Kontakte für Kollisionsmails", () => {
  assert.doesNotMatch(html, /occupancy-contact-register/);
  assert.doesNotMatch(html, /registerOccupancyNotificationContacts/);
  assert.doesNotMatch(html, /APPACK_KONTAKTE_VERIFIZIEREN/);
});
