const {chromium}=require('playwright');
const fs=require('node:fs/promises');
const path=require('node:path');
const {pathToFileURL}=require('node:url');
(async()=>{
 const out=path.resolve('docs/audit-2026-09-10/manual-control-preview');
 const browser=await chromium.launch({channel:'msedge',headless:true});
 const records=[];
 try {
  for (const width of [320,390,1120]) {
   const context=await browser.newContext({viewport:{width,height:844},locale:'de-DE',timezoneId:'Europe/Berlin'});
   const page=await context.newPage();const errors=[];page.on('pageerror',e=>errors.push(e.message));
   await page.route('**/*',route=>route.request().url().startsWith('file:')?route.continue():route.abort());
   for(const fragment of ['moving','park','conflict']){
    await page.goto(pathToFileURL(path.join(out,'appack-preview.html')).href+'#'+fragment);
    await page.reload();
    await page.locator('#manual-control').waitFor({state:'visible'});
    const title=await page.locator('#overall-title').innerText();
    const expected={moving:'Manuell gestartet',park:'Mäher in Station geparkt',conflict:'Bewässerung läuft'}[fragment];
    if(title!==expected)throw new Error(fragment+': '+title);
    if(await page.locator('#manual-park').isDisabled())throw new Error('Park blocked');
    if(await page.evaluate(()=>document.documentElement.scrollWidth>innerWidth))throw new Error('Horizontal overflow');
    await page.screenshot({path:path.join(out,fragment+'-'+width+'.png'),fullPage:true});
    if(fragment==='conflict'){
     await page.locator('#manual-start').click();
     await page.locator('#confirm-dialog').waitFor({state:'visible'});
     if(await page.locator('#manual-occupancy').isChecked()||await page.locator('#manual-drying').isChecked())throw new Error('Prechecked confirmation');
     await page.screenshot({path:path.join(out,'confirmation-'+width+'.png')});
     await page.locator('#confirm-cancel').click();
     await page.locator('#manual-park').click();
     if(await page.locator('#manual-confirmations').isVisible())throw new Error('Park blocked by hidden start confirmations');
     await page.locator('#confirm-cancel').click();
    }
    records.push({fragment,width,title,errors:[...errors],overflow:false});
   }
   await context.close();
  }
  if(records.some(r=>r.errors.length))throw new Error(JSON.stringify(records));
  await fs.writeFile(path.join(out,'browser-check.json'),JSON.stringify({synthetic:true,productionRequestsAllowed:false,deviceAcceptance:false,records},null,2)+'\n');
  process.stdout.write(JSON.stringify(records));
 }finally{await browser.close();}
})().catch(e=>{process.stderr.write(e.stack);process.exitCode=1});
