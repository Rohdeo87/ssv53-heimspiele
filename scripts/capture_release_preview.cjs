// Review the actual local template; no production access or device action.
const { chromium } = require('playwright');
const fs = require('node:fs/promises');
const path = require('node:path');
const { pathToFileURL } = require('node:url');

(async () => {
  const output = path.resolve('docs/ui-2026-09-09/final-preflight');
  const browser = await chromium.launch({ channel: 'msedge', headless: true });
  const records = [];
  try {
    for (const [fragment, width, expected] of [
      ['stale-plan', 390, 'Belegungsplan nicht aktuell'],
      ['water-missing', 320, 'Bewässerungsstand fehlt'],
      ['charging', 390, 'Rasen trocknet'],
      ['stale-plan', 1120, 'Belegungsplan nicht aktuell'],
      ['winter-planned', 320, 'Rasen trocknet'],
      ['winter-planned', 390, 'Rasen trocknet'],
    ]) {
      const context = await browser.newContext({ viewport: { width, height: 844 }, locale: 'de-DE', timezoneId: 'Europe/Berlin' });
      const page = await context.newPage();
      const errors = [];
      page.on('pageerror', error => errors.push(error.message));
      await page.route('**/*', route => route.request().url().startsWith('file:') ? route.continue() : route.abort());
      await page.goto(pathToFileURL(path.join(output, 'appack-preview.html')).href + '#' + fragment);
      await page.waitForFunction(title => document.getElementById('overall-title').textContent === title, expected);
      const content = await page.locator('#overall').innerText();
      const overflow = await page.evaluate(() => document.documentElement.scrollWidth > window.innerWidth);
      const next = await page.locator('#mower-next-start').innerText();
      if (['stale-plan', 'water-missing'].includes(fragment) && next !== 'Noch offen') throw new Error('False start time during missing data');
      if (fragment === 'stale-plan' && await page.locator('#occupancy-name').innerText() !== 'Keine verlässlichen Zeiten') throw new Error('False occupancy certainty');
      if (overflow || errors.length) {
        const clipped = await page.evaluate(() => [...document.querySelectorAll('body *')].filter(e => { const r=e.getBoundingClientRect(); return r.width && (r.left<0 || r.right>innerWidth); }).slice(0,10).map(e=>({tag:e.tagName,id:e.id,classes:e.className,text:e.textContent.slice(0,80)})));
        throw new Error('Layout or JavaScript error: ' + JSON.stringify({ fragment, width, overflow, errors, clipped }));
      }
      const name = fragment + '-' + width;
      await page.screenshot({ path: path.join(output, name + '.png'), fullPage: true });
      if (fragment === 'winter-planned') {
        const toggle = page.locator('#winter-training-switch');
        if (await toggle.getAttribute('aria-checked') !== 'true') throw new Error('Pending winter choice missing');
        if (await page.locator('#training-current').innerText() !== 'Heute gilt der Sommertrainingsplan.') throw new Error('Pending change released current plan');
        if (await page.locator('#training-pending').innerText() !== 'Ab 10.09.2026: Wintertrainingsplan.') throw new Error('Effective date missing');
        await page.locator('#training-control-card').screenshot({ path: path.join(output, 'winter-switch-' + width + '.png') });
        await toggle.click();
        const question = await page.locator('#confirm-question').innerText();
        if (!question.includes('10.09.2026') || !question.includes('Sommertrainingsplan')) throw new Error('Incorrect toggle reversal confirmation');
        await page.locator('#confirm-cancel').click();
        if (await toggle.getAttribute('aria-checked') !== 'true') throw new Error('Cancelled action changed selection');
      }
      records.push({ name, width, expected, content, next, overflow, errors });
      await context.close();
    }
    await fs.writeFile(path.join(output, 'browser-check.json'), JSON.stringify({ synthetic: true, productionRequestsAllowed: false, deviceAcceptance: false, records }, null, 2) + '\n');
    process.stdout.write(JSON.stringify(records));
  } finally { await browser.close(); }
})().catch(error => { process.stderr.write(error.stack); process.exitCode = 1; });
