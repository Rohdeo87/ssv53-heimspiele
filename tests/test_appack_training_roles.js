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
    "return { hasActiveTrainerRole };"
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
