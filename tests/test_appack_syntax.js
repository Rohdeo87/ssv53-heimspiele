const assert = require("node:assert/strict");
const fs = require("node:fs");
const path = require("node:path");
const test = require("node:test");
const vm = require("node:vm");

test("alle eingebetteten Appack-Skripte sind syntaktisch gültig", () => {
  const html = fs.readFileSync(
    path.join(__dirname, "..", "appack-platzbelegungsplan-azure.html"),
    "utf8"
  );
  const scripts = Array.from(html.matchAll(/<script(?![^>]*\bsrc=)[^>]*>([\s\S]*?)<\/script>/gi));
  assert.ok(scripts.length > 0, "Kein eingebettetes Skript gefunden");
  scripts.forEach((match, index) => {
    assert.doesNotThrow(
      () => new vm.Script(match[1], { filename: `appack-inline-${index + 1}.js` })
    );
  });
});
