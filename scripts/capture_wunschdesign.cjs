const {chromium}=require('playwright');
const fs=require('node:fs/promises');
const path=require('node:path');
const {pathToFileURL}=require('node:url');
const assert=require('node:assert/strict');
(async()=>{
 const out=path.resolve(process.env.SSV53_UI_OUTPUT||'docs/ui-2026-09-11/wunschdesign');
 const browser=await chromium.launch({channel:'msedge',headless:true});
 const records=[];
 try {
  for(const width of [320,390,768]){
   const context=await browser.newContext({viewport:{width,height:844},locale:'de-DE',timezoneId:'Europe/Berlin'});
   const page=await context.newPage(),errors=[];page.on('pageerror',e=>errors.push(e.message));
   await page.route('**/*',r=>r.request().url().startsWith('file:')?r.continue():r.abort());
   for(const scenario of ['charging','charging-unknown','charging-display','parked','mowing','stale','watering','unconfirmed']){
    await page.goto(pathToFileURL(path.join(out,'appack-preview.html')).href+'#'+scenario);
    await page.reload();
    await page.locator('#pf-page-home').waitFor({state:'visible'});
    await page.waitForFunction(()=>!document.getElementById('overall-title').textContent.includes('geladen'));
    assert.equal(errors.length,0,errors.join('\n'));
    assert.equal(await page.evaluate(()=>document.documentElement.scrollWidth>innerWidth),false,'Overflow: '+scenario+' '+width);
    assert.equal(await page.locator('.shell > .head').count(),0);
    if(scenario==='stale')assert.equal(await page.locator('#pf-charge').isVisible(),false);
    if(scenario==='mowing'){
     assert.match(await page.locator('#pf-mowing-progress-caption').innerText(),/Fläche gemäht 19 %/);
     assert.equal(await page.locator('#pf-mowing-progress-bar').getAttribute('value'),'19');
     assert.equal(await page.locator('#pf-mowing-progress').evaluate(e=>getComputedStyle(e).gridColumn),'1 / -1');
    }else assert.equal(await page.locator('#pf-mowing-progress').isVisible(),false);
    if(scenario==='charging-display'){
     assert.match(await page.locator('#charge-end-time').innerText(),/10:22 Uhr/);
     assert.equal(await page.locator('#charge-end-note').innerText(),'Voraussichtlich');
     assert.equal(await page.locator('#coordination-card').isVisible(),false);
    }
    if(scenario==='charging-unknown'){
     assert.equal(await page.locator('#overall-title').innerText(),'Mäher lädt');
     assert.equal(await page.locator('#pf-charge-caption').innerText(),'Akku 29 %');
     assert.equal(await page.locator('#pf-charge-progress').getAttribute('value'),'29');
     assert.equal(await page.locator('#coordination-card').isVisible(),false);
     assert.equal(await page.locator('#charge-end-time').innerText(),'Noch nicht bekannt');
     await page.evaluate(()=>{const original=window.fetch;window.fetch=(...args)=>new Promise(resolve=>{window.finishRefresh=()=>resolve(original(...args))})});
     const before=await page.locator('#refresh').boundingBox();
     await page.locator('#refresh').click();
     assert.equal(await page.locator('#refresh-label').innerText(),'Aktualisieren');
     assert.equal(await page.locator('#refresh').getAttribute('aria-busy'),'true');
     assert.equal(await page.locator('#overall-title').innerText(),'Mäher lädt');
     assert.equal(await page.locator('dialog[open]').count(),0);
     const during=await page.locator('#refresh').boundingBox();assert.equal(before.width,during.width);
     await page.screenshot({path:path.join(out,'refresh-'+width+'.png')});
     await page.evaluate(()=>window.finishRefresh());await page.waitForFunction(()=>document.getElementById('refresh').getAttribute('aria-busy')==='false');
     await page.locator('#manual-start').click();
     assert.equal(await page.locator('#confirm-dialog').isVisible(),true);
     for(const id of ['confirm-go','confirm-cancel']){
      assert.equal(await page.locator('#'+id+' > .pf-label').count(),1);
      const centered=await page.locator('#'+id).evaluate(b=>{const r=b.getBoundingClientRect();return Array.from(b.children).every(e=>{const x=e.getBoundingClientRect();return x.left>=r.left&&x.right<=r.right})});assert.equal(centered,true);
     }
     await page.screenshot({path:path.join(out,'confirmation-'+width+'.png')});
     await page.locator('#confirm-cancel').click();
     await page.locator('#pf-home-links [data-pf-target="water"]').click();
     const styles=await page.locator('#pf-page-water .pf-grid > .pf-tile').evaluateAll(nodes=>nodes.map(b=>{const s=getComputedStyle(b),i=getComputedStyle(b.querySelector('.pf-symbol'));return {weight:s.fontWeight,align:s.textAlign,bg:i.backgroundColor,color:i.color,width:i.width,height:i.height}}));
     assert.equal(styles.length,4);for(const style of styles)assert.deepEqual(style,styles[0]);
     await page.screenshot({path:path.join(out,'water-menu-'+width+'.png'),fullPage:true});
     await page.locator('[data-pf-nav="home"]').click();
    }
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
     await page.locator('#pf-back').click();
     await page.locator('[data-pf-nav="more"]').click();
     await page.locator('#pf-page-more [data-pf-target="water"]').click();
     await page.locator('#irrigation-start-all').click();
     assert.equal(await page.locator('#confirm-dialog').isVisible(),true);
     assert.doesNotMatch(await page.locator('#confirm-question').innerText(),/03:30|08:00/);
     await page.locator('#confirm-cancel').click();
     await page.locator('#water-stats-open').click();
     assert.equal(await page.locator('#water-stats-dialog .stat > .pf-symbol').count(),6);
     assert.equal(await page.locator('dialog[open]').count(),0);
     assert.equal(await page.locator('#pf-back').isVisible(),true);
     await page.screenshot({path:path.join(out,'water-stats-'+width+'.png'),fullPage:true});
     await page.locator('#pf-back').click();
     await page.locator('#water-plan-open').click();
     await page.locator('#water-plan-dialog').waitFor({state:'visible'});
     assert.equal(await page.locator('#plan-resume').isVisible(),false);
     await page.screenshot({path:path.join(out,'water-plan-'+width+'.png')});
     assert.equal(await page.locator('dialog[open]').count(),0);
     assert.equal(await page.locator('#plan-history-toggle > .pf-symbol').count(),1);
     await page.locator('#plan-history-toggle').click();
     assert.equal(await page.locator('#plan-history-toggle > .pf-symbol').count(),1);
     const planStyles=await page.locator('#plan-main-actions > .plan-choice').evaluateAll(nodes=>nodes.map(b=>({background:getComputedStyle(b).backgroundColor,icon:getComputedStyle(b.querySelector('.pf-symbol')).backgroundColor})));
     for(const style of planStyles)assert.deepEqual(style,planStyles[0]);
     await page.locator('#plan-custom-open').click();
     await page.locator('#plan-start-time').fill('04:15');
     const beforeMinutes=await page.locator('#plan-zones .plan-zone').first().getAttribute('data-minutes');
     await page.locator('#plan-zones .duration-stepper button').nth(1).click();
     await page.locator('#refresh').click();await page.waitForFunction(()=>!document.getElementById('refresh').disabled);
     assert.equal(await page.locator('#plan-start-time').inputValue(),'04:15');
     assert.equal(Number(await page.locator('#plan-zones .plan-zone').first().getAttribute('data-minutes')),Number(beforeMinutes)+1);
     await page.screenshot({path:path.join(out,'water-custom-'+width+'.png'),fullPage:true});
     await page.locator('#pf-back').click();assert.equal(await page.locator('#plan-home').isVisible(),true);
     await page.locator('#plan-skip').click();assert.equal(await page.locator('#confirm-dialog').isVisible(),true);
     await page.locator('#confirm-cancel').click();assert.equal(await page.locator('#pf-page-water-plan').isVisible(),true);
     await page.locator('#pf-back').click();assert.equal(await page.locator('#pf-page-water').isVisible(),true);
     await page.locator('#pf-page-water [data-pf-target="zones"]').click();
     await page.screenshot({path:path.join(out,'zones-'+width+'.png'),fullPage:true});
     await page.locator('[data-pf-nav="more"]').click();
     await page.locator('#pf-page-more [data-pf-target="grounds"]').click();
     assert.equal(await page.locator('#pf-page-grounds [data-pf-calendar]').count(),2);
     assert.equal(await page.locator('#pf-page-grounds [data-pf-calendar]').first().getAttribute('href'),'nav://ssv53_TextImage_1761902353516');
     await page.locator('#pf-page-grounds [data-pf-target="training"]').click();
     await page.screenshot({path:path.join(out,'training-'+width+'.png'),fullPage:true});
     await page.locator('[data-pf-nav="home"]').click();
     await page.locator('[data-pf-nav="more"]').click();await page.locator('#pf-page-more [data-pf-target="mower"]').click();await page.locator('#pf-page-mower [data-pf-target="controls"]').click();
     assert.equal(await page.locator('#pf-page-controls .facts .pf-symbol').count(),5);
     await page.screenshot({path:path.join(out,'controls-'+width+'.png'),fullPage:true});await page.locator('[data-pf-nav="home"]').click();
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
