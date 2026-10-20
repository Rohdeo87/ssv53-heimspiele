// Actual browser history against the shipping template, with all network/device calls blocked.
const {chromium}=require('playwright');
const fs=require('node:fs/promises');
const path=require('node:path');
const assert=require('node:assert/strict');
(async()=>{
 const out=path.resolve(process.env.SSV53_UI_OUTPUT||'docs/ui-2026-09-11/phone-back');
 const html=await fs.readFile(path.join(out,'appack-preview.html'),'utf8');
 const browser=await chromium.launch({channel:'msedge',headless:true}),records=[];
 try{
  for(const width of [320,390,768]){
   const context=await browser.newContext({viewport:{width,height:844},locale:'de-DE',timezoneId:'Europe/Berlin'});
   const page=await context.newPage(),errors=[];page.on('pageerror',e=>errors.push(e.message));
   await page.route('**/*',r=>{
    const url=new URL(r.request().url());
    if(url.origin==='http://platzpflege.test')return r.fulfill({contentType:'text/html',body:url.pathname==='/care'?html:'<h1>App-Hauptmenü (Simulation)</h1><a href="/care?token=synthetic#charging">Platzpflege</a>'});
    return r.abort();
   });
   async function at(key){await page.locator('#pf-page-'+key).waitFor({state:'visible'});}
   async function step(key){await page.waitForFunction(k=>history.state.ssv53Platzpflege.plan===k,key);}
   async function settled(){await page.waitForFunction(()=>!history.state.ssv53Platzpflege.dialog);}
   async function open(scenario='charging'){
    await page.goto('http://platzpflege.test/entry');await page.locator('a').click();
    if(scenario!=='charging'){await page.goto('http://platzpflege.test/care?token=synthetic#'+scenario);await page.reload();}
    await at('home');await page.waitForFunction(()=>!document.getElementById('refresh').disabled);
    await page.evaluate(()=>{window.postAttempts=0;const fetch=window.fetch;window.fetch=(url,options)=>{if(options&&options.method==='POST')window.postAttempts++;return fetch(url,options);}});
   }
   await open();const url=page.url(),initialLength=await page.evaluate(()=>history.length);
   await page.locator('[data-pf-nav="more"]').click();await at('more');
   await page.locator('[data-pf-nav="more"]').click();
   assert.equal(await page.evaluate(()=>history.length),initialLength+1);
   await page.locator('[data-pf-target="mower"]').click();await at('mower');
   await page.locator('[data-pf-target="height"]').click();await at('height');
   assert.equal(page.url(),url,'Identity parameters/fragment unchanged');
   await page.locator('#height-plus').click();
   await page.locator('#height-save').click();await page.locator('#confirm-dialog').waitFor({state:'visible'});
   await page.goBack();await page.locator('#confirm-dialog').waitFor({state:'hidden'});await at('height');
   await page.goForward();await at('height');
   assert.equal(await page.locator('dialog[open]').count(),0,'Forward must not replay a confirmation');
   await page.goBack();await at('height');
   await page.locator('#pf-back').click();await at('mower');
   await page.goBack();await at('more');await page.goBack();await at('home');
   await page.goBack();assert.match(page.url(),/\/entry$/,'Initial root must allow leaving the module');
   records.push({width,case:'hardware-style back, on-screen back, forward, no root trap',passed:true});

   await open();await page.locator('[data-pf-nav="more"]').click();
   await page.locator('[data-pf-target="mower"]').click();await page.locator('[data-pf-target="height"]').click();
   const length=await page.evaluate(()=>history.length);
   await page.locator('#refresh').click();await page.waitForFunction(()=>!document.getElementById('refresh').disabled);
   assert.equal(await page.evaluate(()=>history.length),length,'Refresh must not add a page');
   await page.reload();await at('height');await page.goBack();await at('mower');
   await page.locator('[data-pf-nav="today"]').click();await at('today');await page.goBack();await at('mower');
   records.push({width,case:'reload restores subpage, refresh and bottom tabs',passed:true});

   await open();await page.locator('#pf-home-links [data-pf-target="water"]').click();
   await page.locator('#water-plan-open').click();await at('water-plan');
   await page.locator('#plan-custom-open').click();await step('custom');
   await page.locator('#plan-start-time').fill('04:15');
   await page.goBack();await step('home');
   await page.goForward();await step('custom');assert.equal(await page.locator('#plan-start-time').inputValue(),'04:15');
   await page.locator('#pf-back').click();await step('home');
   await page.locator('#plan-pause-open').click();await step('pause');
   await page.goBack();await step('home');
   await page.locator('#plan-skip').click();await page.locator('#confirm-dialog').waitFor({state:'visible'});
   await page.keyboard.press('Escape');await settled();await at('water-plan');
   await page.goBack();await at('water');
   await page.locator('#water-stats-open').click();await at('water-stats');
   await page.goBack();await at('water');
   assert.equal(await page.evaluate(()=>window.postAttempts),0);
   await page.screenshot({path:path.join(out,'back-water-'+width+'.png')});
   records.push({width,case:'plan substeps, Escape, statistics, no device requests',passed:true});

   await open('watering');await page.locator('#irrigation-stop').click();await page.locator('#stop-dialog').waitFor({state:'visible'});
   const stopIndex=await page.evaluate(()=>history.state.ssv53Platzpflege.index);
   await page.locator('#stop-now').click();await page.locator('#confirm-dialog').waitFor({state:'visible'});
   assert.equal(await page.evaluate(()=>history.state.ssv53Platzpflege.index),stopIndex,'Dialog transition stays one step');
   await page.goBack();await settled();await at('home');
   assert.equal(await page.locator('dialog[open]').count(),0);
   assert.equal(await page.evaluate(()=>window.postAttempts),0);
   await page.locator('#manual-start').click();await page.locator('#confirm-dialog').waitFor({state:'visible'});
   await page.locator('#confirm-cancel').click();await settled();
   assert.equal(await page.evaluate(()=>window.postAttempts),0);
   assert.deepEqual(errors,[]);
   records.push({width,case:'stop choice to confirmation, cancel without action',passed:true});
   await context.close();
  }
  await fs.writeFile(path.join(out,'back-browser-check.json'),JSON.stringify({synthetic:true,networkBlocked:true,nativeAppackPhoneVerified:false,records},null,2)+'\n');
  console.log(JSON.stringify({checks:records.length,errors:0,deviceRequests:0}));
 }finally{await browser.close()}
})().catch(e=>{console.error(e.stack);process.exitCode=1});
