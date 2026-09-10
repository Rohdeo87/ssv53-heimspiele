const {chromium}=require('playwright');
const fs=require('node:fs/promises');
const path=require('node:path');
const {pathToFileURL}=require('node:url');
const assert=require('node:assert/strict');
(async()=>{
 const root=path.resolve('docs/ui-2026-09-10/parked-status'),results=[];
 const browser=await chromium.launch({channel:'msedge',headless:true});
 try {for(const name of ['training','missing-report'])for(const width of [320,390]){
  const context=await browser.newContext({viewport:{width,height:1200},locale:'de-DE',timezoneId:'Europe/Berlin'});
  const page=await context.newPage(),errors=[];page.on('pageerror',e=>errors.push(e.message));
  await page.route('**/*',r=>r.request().url().startsWith('file:')?r.continue():r.abort());
  await page.goto(pathToFileURL(path.join(root,name,'appack-preview.html')).href);
  const expected=name==='training'?'Platz ist belegt':'Mähermeldung ist älter';
  await page.waitForFunction(t=>document.getElementById('overall-title').textContent===t,expected);
  const title=await page.locator('#overall-title').innerText(),at=await page.locator('#mower-next-start').innerText(),note=await page.locator('#next-start-note').innerText();
  assert.equal(at,name==='training'?'Heute, 22:00 Uhr':'Noch offen');
  assert.equal(note,name==='training'?'Geplant · Neue Mähermeldung erforderlich.':'');
  assert.equal(await page.locator('#mower-title').innerText(),'Zuletzt: In der Station');
  assert.equal(await page.locator('#manual-start').isDisabled(),true);
  assert.equal(await page.locator('#manual-park').isDisabled(),false);
  assert.equal(await page.locator('#irrigation-start-all').isVisible(),false);
  assert.equal(await page.locator('#drying-end-row').isVisible(),false);
  assert.equal(await page.evaluate(()=>document.documentElement.scrollWidth>innerWidth),false);
  assert.deepEqual(errors,[]);
  await page.screenshot({path:path.join(root,`${name}-${width}.png`)});
  results.push({case:name,width,title,at,note,manualStartDisabled:true,parkAvailable:true,wateringStartHidden:true,errors,passed:true});
  await context.close();
 }}finally{await browser.close()}
 await fs.writeFile(path.join(root,'browser-check.json'),JSON.stringify({synthetic:true,networkBlocked:true,deviceAcceptance:false,results},null,2)+'\n');
 console.log(JSON.stringify({passed:results.length,networkBlocked:true}));
})().catch(e=>{console.error(e);process.exitCode=1});
