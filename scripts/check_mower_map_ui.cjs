// Isolated browser test, no production credentials and no device commands.
const {chromium}=require('playwright');
const fs=require('node:fs/promises');
const path=require('node:path');
const {pathToFileURL}=require('node:url');
const assert=require('node:assert/strict');
(async()=>{
 const out=path.resolve('docs/ui-2026-09-12/mower-map');
 const browser=await chromium.launch({channel:'msedge',headless:true});
 const records=[];
 try{
  for(const width of [390,320]){
   const page=await browser.newPage({viewport:{width,height:844},locale:'de-DE',timezoneId:'Europe/Berlin'});
   const errors=[],requests=[];page.on('pageerror',e=>errors.push(e.message));
   await page.route('**/*',route=>{
    const u=route.request().url();requests.push(u);
    if(u.startsWith('file:')||u.startsWith('https://unpkg.com/leaflet@1.9.4/dist/')||u.startsWith('https://isk.geobasis-bb.de/mapproxy/dop20c/service/wms'))return route.continue();
    return route.abort();
   });
   await page.goto(pathToFileURL(path.join(out,'preview/appack-preview.html')).href);
   await page.locator('#pf-map-home').waitFor({state:'visible'});
   assert.equal(requests.filter(u=>u.includes('unpkg.com')||u.includes('geobasis-bb')).length,0,'map assets eagerly loaded');
   await page.locator('#pf-map-home').click();
   await page.locator('.pf-map-marker').waitFor({state:'visible',timeout:25000});
   await page.waitForFunction(()=>[...document.querySelectorAll('.leaflet-tile')].some(t=>t.complete&&t.naturalWidth>0),{},{timeout:30000});
   await page.locator('#pf-map-error').waitFor({state:'hidden',timeout:30000});
   await page.getByRole('button',{name:'Vergrößern',exact:true}).click();
   await page.getByRole('button',{name:'Verkleinern',exact:true}).click();
   await page.getByRole('button',{name:'Mäher zeigen',exact:true}).click();
   assert.equal(await page.evaluate(()=>document.documentElement.scrollWidth>innerWidth),false,'horizontal overflow');
   await page.screenshot({path:path.join(out,'map-'+width+'.png'),fullPage:true});
   // Browser back reaches the previous internal page, and forward restores map.
   await page.goBack();assert.equal(await page.locator('#pf-page-home').isVisible(),true);
   await page.goForward();assert.equal(await page.locator('#pf-page-map').isVisible(),true);
   await page.evaluate(()=>{window.auditFixture.mower.position=null;document.getElementById('refresh').click()});
   await page.waitForFunction(()=>document.getElementById('pf-map-note').textContent.includes('nicht verfügbar'));
   assert.equal(await page.locator('#pf-mower-map').isVisible(),false);
   assert.equal(await page.locator('.pf-map-marker').count(),0);
   assert.deepEqual(errors,[]);
   records.push({width,lazy:true,imageryLoaded:true,zoom:true,backForward:true,missingPosition:true,noOverflow:true,errors});
   await page.close();
  }
 }finally{await browser.close()}
 await fs.writeFile(path.join(out,'ui-checks.json'),JSON.stringify({synthetic:true,deviceCommands:false,records},null,2)+'\n');
 console.log(JSON.stringify(records));
})().catch(e=>{console.error(e);process.exitCode=1});
