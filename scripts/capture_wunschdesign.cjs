const {chromium}=require('playwright');
const fs=require('node:fs/promises');
const path=require('node:path');
const {pathToFileURL}=require('node:url');
const assert=require('node:assert/strict');
(async()=>{
 const out=path.resolve('docs/ui-2026-09-11/wunschdesign');
 const browser=await chromium.launch({channel:'msedge',headless:true});
 const records=[];
 try {
  for(const width of [320,390,768]){
   const context=await browser.newContext({viewport:{width,height:844},locale:'de-DE',timezoneId:'Europe/Berlin'});
   const page=await context.newPage(),errors=[];page.on('pageerror',e=>errors.push(e.message));
   await page.route('**/*',r=>r.request().url().startsWith('file:')?r.continue():r.abort());
   for(const scenario of ['charging','parked','mowing','stale','watering','unconfirmed']){
    await page.goto(pathToFileURL(path.join(out,'appack-preview.html')).href+'#'+scenario);
    await page.reload();
    await page.locator('#pf-page-home').waitFor({state:'visible'});
    await page.waitForFunction(()=>!document.getElementById('overall-title').textContent.includes('geladen'));
    assert.equal(errors.length,0,errors.join('\n'));
    assert.equal(await page.evaluate(()=>document.documentElement.scrollWidth>innerWidth),false,'Overflow: '+scenario+' '+width);
    if(['stale','unconfirmed','mowing'].includes(scenario))assert.equal(await page.locator('#manual-start').isVisible(),false);
    if(scenario==='parked')assert.equal(await page.locator('#manual-park').isVisible(),false);
    if(scenario==='charging'){
     assert.equal(await page.locator('#manual-resume').isVisible(),false);
     assert.equal(await page.locator('#overall-title').innerText(),'Rasen trocknet');
     assert.equal(await page.locator('#overall > .pf-symbol').getAttribute('data-pf-icon'),'Leaf');
    }
    await page.screenshot({path:path.join(out,scenario+'-'+width+'.png'),fullPage:true});
    if(scenario==='charging'){
     await page.locator('[data-pf-nav="more"]').click();
     await page.locator('#pf-page-more [data-pf-target="mower"]').click();
     await page.screenshot({path:path.join(out,'mower-menu-'+width+'.png'),fullPage:true});
     await page.locator('#pf-page-mower [data-pf-target="height"]').click();
     assert.equal(await page.locator('#height-save').isVisible(),false);
     await page.locator('#height-plus').click();
     assert.match(await page.locator('#height-save').innerText(),/28 mm speichern/);
     assert.match(await page.locator('#pf-height-current').innerText(),/27 mm/);
     await page.screenshot({path:path.join(out,'height-'+width+'.png'),fullPage:true});
     await page.locator('#height-save').click();
     await page.locator('#confirm-dialog').waitFor({state:'visible'});
     assert.match(await page.locator('#confirm-title').innerText(),/28 mm/);
     assert.equal(await page.locator('#confirm-title [data-pf-icon="MoveVertical"]').count(),1);
     await page.locator('#confirm-cancel').click();
     await page.locator('[data-pf-nav="more"]').click();
     await page.locator('#pf-page-more [data-pf-target="mower"]').click();
     await page.locator('#stats-open').click();
     assert.equal(await page.locator('#stats-dialog .stat > .pf-symbol').count(),8);
     await page.screenshot({path:path.join(out,'stats-'+width+'.png')});
     await page.locator('#stats-close').click();
     await page.locator('[data-pf-nav="more"]').click();
     await page.locator('#pf-page-more [data-pf-target="water"]').click();
     await page.locator('#irrigation-start-all').click();
     assert.equal(await page.locator('#confirm-dialog').isVisible(),true);
     assert.doesNotMatch(await page.locator('#confirm-question').innerText(),/03:30|08:00/);
     await page.locator('#confirm-cancel').click();
     await page.locator('#water-stats-open').click();
     assert.equal(await page.locator('#water-stats-dialog .stat > .pf-symbol').count(),6);
     await page.locator('#water-stats-close').click();
     await page.locator('#water-plan-open').click();
     await page.locator('#water-plan-dialog').waitFor({state:'visible'});
     assert.equal(await page.locator('#plan-resume').isVisible(),false);
     await page.screenshot({path:path.join(out,'water-plan-'+width+'.png')});
     await page.locator('#water-plan-close').click();
     await page.locator('[data-pf-nav="more"]').click();
     await page.locator('#pf-page-more [data-pf-target="grounds"]').click();
     assert.equal(await page.locator('#pf-page-grounds [data-pf-calendar]').count(),2);
     assert.equal(await page.locator('#pf-page-grounds [data-pf-calendar]').first().getAttribute('href'),'nav://ssv53_TextImage_1761902353516');
     await page.locator('#pf-page-grounds [data-pf-target="training"]').click();
     await page.screenshot({path:path.join(out,'training-'+width+'.png'),fullPage:true});
     await page.locator('[data-pf-nav="home"]').click();
     await page.evaluate(()=>{const s=window.pfFixtures.charging;s.coordination.dryUntil=null;s.coordination.releaseNotBefore=null;s.coordination.blockers=[];s.manualControl.confirmations.dryingRequired=false});
     await page.locator('#refresh').click();
     await page.waitForFunction(()=>document.getElementById('overall-title').textContent==='Mäher lädt');
     assert.equal(await page.locator('#overall > .pf-symbol').getAttribute('data-pf-icon'),'BatteryCharging');
    }
    if(scenario==='watering'){
     assert.equal(await page.locator('#overall-title').innerText(),'Bewässerung läuft');
     assert.equal(await page.locator('#irrigation-stop').isVisible(),true);
     await page.locator('#manual-start').click();
     assert.equal(await page.locator('#manual-water-choice').isVisible(),true);
     assert.equal(await page.locator('#manual-water-choice [data-pf-icon="Droplets"]').count(),1);
     assert.equal(await page.locator('#manual-drying-label').isVisible(),false);
     await page.locator('input[name="manual-water"][value="MOWER"]').check();
     assert.equal(await page.locator('#manual-drying-label').isVisible(),true);
     await page.locator('input[name="manual-water"][value="IRRIGATION"]').check();
     assert.equal(await page.locator('#manual-drying-label').isVisible(),false);
     await page.screenshot({path:path.join(out,'water-choice-'+width+'.png')});
     await page.locator('#confirm-cancel').click();
     await page.locator('[data-pf-nav="more"]').click();
     assert.equal(await page.locator('#irrigation-stop').isVisible(),true);
     await page.locator('#irrigation-stop').click();
     await page.locator('#stop-dialog').waitFor({state:'visible'});
     await page.locator('#stop-cancel').click();
    }
    records.push({width,scenario,errors:[...errors],overflow:false});
   }
   await context.close();
  }
  await fs.writeFile(path.join(out,'browser-check.json'),JSON.stringify({synthetic:true,networkBlocked:true,deviceCommands:false,records},null,2)+'\n');
  console.log(JSON.stringify({checked:records.length,errors:0}));
 }finally{await browser.close()}
})().catch(e=>{console.error(e.stack);process.exitCode=1});
