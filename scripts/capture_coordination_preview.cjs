// Local browser evidence only. Every network request is blocked.
const { chromium } = require('playwright');
const fs = require('node:fs/promises');
const path = require('node:path');
const { pathToFileURL } = require('node:url');

(async () => {
  const output = path.resolve('docs/ui-2026-09-09/coordination-execution');
  const browser = await chromium.launch({ channel: 'msedge', headless: true });
  const records = [];
  try {
    for (const [name, viewport, fragment, title] of [
      ['mobile-reserved', { width: 390, height: 844 }, 'coordination', 'Bewässerung wird vorbereitet'],
      ['mobile-blocked', { width: 390, height: 844 }, 'coordination-blocked', 'Bewässerung wartet auf Prüfung'],
      ['desktop-reserved', { width: 1120, height: 950 }, 'coordination', 'Bewässerung wird vorbereitet'],
    ]) {
      const context = await browser.newContext({ viewport, locale: 'de-DE', timezoneId: 'Europe/Berlin' });
      const page = await context.newPage();
      const errors = [], network = [];
      page.on('pageerror', error => errors.push(error.message));
      await page.route('**/*', route => {
        if (route.request().url().startsWith('file:')) return route.continue();
        network.push(route.request().url());
        return route.abort();
      });
      await page.goto(pathToFileURL(path.join(output, 'appack-preview.html')).href + '#' + fragment);
      await page.waitForFunction(expected => document.getElementById('overall-title').textContent === expected, title);
      const waterStartVisible = await page.locator('#irrigation-start-all').isVisible();
      const disabledPlanActions = await page.locator('#plan-skip,#plan-pause-open,#plan-custom-open,#plan-pause,#plan-resume')
        .evaluateAll(buttons => buttons.every(button => button.disabled));
      if (waterStartVisible || !disabledPlanActions) throw new Error('Conflicting start/plan controls are available');
      await page.screenshot({ path: path.join(output, name + '.png'), fullPage: true });
      const content = await page.locator('#overall').innerText();
      records.push({ name, viewport, fragment, content, errors, waterStartVisible, disabledPlanActions, blockedNetworkRequests: network.length });
      if (errors.length) throw new Error('Preview has JavaScript errors: ' + errors.join('; '));
      await context.close();
    }
    await fs.writeFile(path.join(output, 'browser-check.json'), JSON.stringify({
      synthetic: true, deviceAcceptance: false, productionRequestsAllowed: false, records,
    }, null, 2) + '\n');
    process.stdout.write(JSON.stringify(records));
  } finally {
    await browser.close();
  }
})().catch(error => { process.stderr.write(error.stack); process.exitCode = 1; });
