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
  assert.match(html, /function eventDate\(value\)/);
  assert.match(html, /weekday:"short",day:"2-digit",month:"2-digit",year:"2-digit"/);
  assert.match(html, /EVENT_TIME_ZONE="Europe\/Berlin"/);
  assert.match(html, /startTime\+"–"\+endTime/);
  assert.doesNotMatch(html, /month:"long"/);
  assert.match(html, /function submitPin\(\)/);
  assert.doesNotMatch(html, /id="login-button"/);
  assert.match(html, /function clearActionError\(\)/);
  assert.match(html, /Mäher-, Beregnungs-, Platzbelegungs- und Vereinsheimdaten konnten nicht geladen werden/);
  assert.match(html, /Bedienaktion konnte nicht an die Platzpflege-Steuerung übermittelt werden/);
  assert.doesNotMatch(html, />Failed to fetch</);
  assert.match(html, /continuousMowingOwned/);
  assert.match(html, /Automatik aktiv/);
  assert.match(html, /Mäher nicht erreichbar/);
  assert.match(html, /Mäher sucht Verbindung/);
  assert.match(html, /mower-connection/);
  assert.match(html, /cutting-height-current/);
  assert.match(html, /SET_CUTTING_HEIGHT/);
  assert.match(html, /Empfohlen: mindestens 30 mm/);
  assert.match(html, /value<25/);
  assert.match(html, /dringend empfohlen/);
  assert.doesNotMatch(html, /mowe_forced/i);
  assert.doesNotMatch(html, /HUSQVARNA_CLIENT_SECRET|HYDRAWISE_API_KEY|SSV53_PLATZWART_PIN_HASH/);
});

test("nur aktive Rollen werden geprüft und requestedRoles bleiben unberücksichtigt", () => {
  assert.match(html, /p\.roleKeys/);
  assert.match(html, /p\.roles/);
  assert.doesNotMatch(html, /requestedRoles/);
});

test("Vereinsheimtermine werden kompakt in Berliner Zeit dargestellt", () => {
  process.env.TZ = "Europe/Berlin";
  const dateFunction = html.split("\n").find((line) => line.includes("function eventDate(value)"));
  const clockFunction = html.split("\n").find((line) => line.includes("function eventClock(value)"));
  const dayFunction = html.split("\n").find((line) => line.includes("function eventDay(value)"));
  const timeFunction = html.split("\n").find((line) => line.includes("function eventTime(startValue,endValue)"));
  assert.ok(dateFunction);
  assert.ok(clockFunction);
  assert.ok(dayFunction);
  assert.ok(timeFunction);
  const format = new Function(
    `var EVENT_TIME_ZONE="Europe/Berlin";\n${dateFunction}\n${clockFunction}\n${dayFunction}\n${timeFunction}\nreturn eventTime;`
  )();

  assert.match(
    format("2026-08-23T16:00:00Z", "2026-08-23T20:00:00Z"),
    /^So\., 23\.08\.26 · 18:00–22:00 Uhr$/
  );
  assert.match(
    format("2026-09-05T05:00:00Z", "2026-09-05T22:00:00Z"),
    /^Sa\., 05\.09\.26 · 07:00–24:00 Uhr$/
  );
});
