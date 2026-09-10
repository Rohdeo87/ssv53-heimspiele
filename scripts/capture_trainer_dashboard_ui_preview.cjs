// Local-only visual review. All non-file requests are aborted.
const { chromium } = require('playwright');
const fs = require('node:fs/promises');
const path = require('node:path');
const { pathToFileURL } = require('node:url');
const assert = require('node:assert/strict');
(async () => {
  const root = path.resolve('docs/ui-2026-09-10/trainer-dashboard');
  const browser = await chromium.launch({channel:'msedge', headless:true});
  const results = [];
  try { for (const name of ['possible-gap','irrigation-end','charging','water-unknown']) for (const width of [320,390]) {
    const context = await browser.newContext({viewport:{width,height:900}, locale:'de-DE', timezoneId:'Europe/Berlin'});
    const page = await context.newPage(); const blocked=[]; const errors=[];
    page.on('pageerror', e => errors.push(e.message));
    await page.route('**/*', route => route.request().url().startsWith('file:') ? route.continue() : (blocked.push(route.request().url()), route.abort()));
    await page.goto(pathToFileURL(path.join(root,name,'appack-preview.html')).href);
    const expected = { 'possible-gap':'Rasenpause zur Sicherheit', 'irrigation-end':'Rasen trocknet', 'charging':'Mäher lädt', 'water-unknown':'Bewässerungsstand fehlt' }[name];
    await page.waitForFunction(title => document.getElementById('overall-title').textContent === title, expected);
    const title = await page.locator('#overall-title').innerText();
    const overflow = await page.evaluate(() => document.documentElement.scrollWidth > innerWidth);
    assert.equal(overflow, false, `${name}/${width}: horizontal overflow`);
    assert.deepEqual(errors, [], `${name}/${width}: JavaScript error`);
    const dryingLabel = await page.locator('#drying-end-label').innerText();
    const dryTime = await page.locator('#drying-end-time').innerText();
    const chargeTime = await page.locator('#charge-end-time').innerText();
    if (name === 'possible-gap' || name === 'irrigation-end') {
      assert.equal(dryTime,'Heute, 18:25 Uhr');
      assert.equal(dryingLabel,name === 'possible-gap' ? 'Rasenpause zur Sicherheit bis' : 'Wartezeit nach Bewässerung bis');
      assert.equal(await page.locator('#drying-end-row').isVisible(),true);
    }
    if (name === 'charging') {
      assert.equal(chargeTime,'Heute, 17:10 Uhr');
      assert.equal(await page.locator('#charge-end-note').innerText(),'Voraussichtlich');
      assert.equal(await page.locator('#charge-end-row').isVisible(),true);
    }
    if (name === 'water-unknown') {
      assert.equal(await page.locator('#mower-next-start').innerText(),'Noch offen');
      assert.equal(await page.locator('#drying-end-row').isVisible(),false);
    }
    await page.screenshot({path:path.join(root,`${name}-${width}.png`),fullPage:true});
    results.push({case:name,width,title,overall:await page.locator('#overall').innerText(),dryingLabel,dryTime,chargeTime,overflow,assertionsPassed:true,blockedNetworkRequests:blocked.length,errors});
    await context.close();
  }
  await fs.writeFile(path.join(root,'browser-check.json'),JSON.stringify({synthetic:true,networkBlocked:true,deviceAcceptance:false,results},null,2)+'\n');
  } finally { await browser.close(); }
})().catch(e => { console.error(e.stack); process.exitCode=1; });
