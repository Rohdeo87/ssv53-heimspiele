const assert = require("node:assert/strict");
const fs = require("node:fs");
const path = require("node:path");
const test = require("node:test");
const vm = require("node:vm");

const html = fs.readFileSync(
  path.join(__dirname, "..", "appack-platzwart-dashboard.html"),
  "utf8"
);

test("Platzwart-Seite ist syntaktisch gültig und enthält keine Zugangsdaten", () => {
  const scripts = Array.from(html.matchAll(/<script(?![^>]*\bsrc=)[^>]*>([\s\S]*?)<\/script>/gi));
  assert.ok(scripts.length > 0);
  scripts.forEach((match, index) => {
    const rendered = match[1].replace(
      /\[#if profile_json\?has_content\]\$\{profile_json\}\[#else\]\{\}\[\/\#if\]/g,
      "{}"
    );
    assert.doesNotThrow(() => new vm.Script(rendered, { filename: `platzwart-${index}.js` }));
  });
  assert.match(html, /platzwart/i);
  assert.match(html, /STOP_IRRIGATION_AFTER_ZONE/);
  assert.match(html, /STOP_IRRIGATION_NOW/);
  assert.match(html, /START_IRRIGATION_ZONE/);
  assert.match(html, /Automatik einschalten/);
  assert.match(html, /refresh-spinner/);
  assert.match(html, /clubhouse-events/);
  assert.match(html, /Wird ausgeführt/);
  assert.match(html, /waitForAction/);
  assert.match(html, /Gebucht von:/);
  assert.match(html, /weekday:"long",day:"2-digit",month:"long",year:"numeric"/);
  assert.match(html, /function submitPin\(\)/);
  assert.doesNotMatch(html, /id="login-button"/);
  assert.match(html, /continuousMowingOwned/);
  assert.match(html, /Automatik aktiv/);
  assert.match(html, /Mäher nicht erreichbar/);
  assert.match(html, /Mäher sucht Verbindung/);
  assert.match(html, /mower-connection/);
  assert.doesNotMatch(html, /mowe_forced/i);
  assert.doesNotMatch(html, /HUSQVARNA_CLIENT_SECRET|HYDRAWISE_API_KEY|SSV53_PLATZWART_PIN_HASH/);
});

test("nur aktive Rollen werden geprüft und requestedRoles bleiben unberücksichtigt", () => {
  assert.match(html, /p\.roleKeys/);
  assert.match(html, /p\.roles/);
  assert.doesNotMatch(html, /requestedRoles/);
});
