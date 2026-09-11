// Isolated rendering: observed controller states, synthetic nonessential UI fields.
const {chromium}=require('playwright');
const path=require('node:path'),fs=require('node:fs/promises');
const {pathToFileURL}=require('node:url');
const assert=require('node:assert/strict');
(async()=>{
 const out=path.resolve('docs/ui-2026-09-11/return-status');
 const browser=await chromium.launch({channel:'msedge',headless:true}),records=[];
 try{
  for(const width of [320,390,768]){
   for(const [scenario,title] of [['homeward','Mäher fährt zur Station'],['docked','Platz wird bald belegt'],['training-wait','Platz wird bald belegt']]){
    const context=await browser.newContext({viewport:{width,height:844},locale:'de-DE',timezoneId:'Europe/Berlin'});
    const page=await context.newPage(),errors=[];
    page.on('pageerror',e=>errors.push(e.message));
    await page.route('**/*',r=>r.request().url().startsWith('file:')?r.continue():r.abort());
    await page.goto(pathToFileURL(path.resolve('dist/return-status-replay/appack-preview.html')).href+'#'+scenario);
    await page.waitForFunction(()=>!document.getElementById('refresh').disabled);
    assert.equal(await page.locator('#overall-title').innerText(),title);
    const card=await page.locator('#coordination-card').innerText();
    assert.match(card,/Frühester Mähstart/);assert.match(card,/19:00 Uhr/);assert.match(card,/Akku muss bereit sein/);
    assert.equal(await page.evaluate(()=>document.documentElement.scrollWidth>innerWidth),false);
    assert.deepEqual(errors,[]);
    if(width===390)await page.screenshot({path:path.join(out,scenario+'-390.png'),fullPage:true});
    records.push({scenario,width,title,card,passed:true});await context.close();
   }
  }
 }finally{await browser.close()}
 await fs.writeFile(path.join(out,'browser-replay.json'),JSON.stringify({network:'blocked',deviceCommands:0,capturedAppResponse:false,records},null,2)+'\n');
 console.log(JSON.stringify({passed:records.length}));
})().catch(e=>{console.error(e);process.exit(1)});
