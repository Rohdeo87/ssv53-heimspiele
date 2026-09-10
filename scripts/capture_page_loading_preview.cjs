// Reproducible browser latency/race check. All fetches are synthetic; network is blocked.
const {chromium}=require('playwright');
const fs=require('node:fs/promises');
const path=require('node:path');
const {pathToFileURL}=require('node:url');
const assert=require('node:assert/strict');
const {execFileSync}=require('node:child_process');
(async()=>{
 const out=path.resolve('docs/ui-2026-09-10/page-loading');await fs.mkdir(out,{recursive:true});
 const current=await fs.readFile('appack-platzwart-dashboard.html','utf8');
 const before=execFileSync('git',['show','1123bfb2119a1ec42d079e1f5bcd957c5bdf85c7:appack-platzwart-dashboard.html'],{encoding:'utf8'});
 const fixture=JSON.parse(await fs.readFile('docs/ui-2026-09-10/parked-status/training/fixture.json','utf8')).payload;
 Object.assign(fixture,{statistics:{available:true,mowingMinutes7d:120},irrigationStatistics:{available:true},clubhouse:{available:true,events:[]}});
 const setup=`<meta http-equiv="Content-Security-Policy" content="default-src 'none'; script-src 'unsafe-inline'; style-src 'unsafe-inline'; img-src data:; connect-src 'none'; base-uri 'none'; form-action 'none'">
<script>
window.fixture=${JSON.stringify(fixture)};window.requests=[];window.timings={};
localStorage.setItem('ssv53_platzwart_device_v1',JSON.stringify({deviceId:'synthetic',deviceToken:'not-a-credential'}));
sessionStorage.setItem('ssv53_platzwart_session_v1',JSON.stringify({token:'synthetic-not-signed',expiresAt:'2099-01-01T00:00:00Z'}));
window.fetch=async function(url,options){
 if(options&&options.method!=='GET')throw new Error('No mutations in preview');
 var fast=url.indexOf('view=live')>=0;window.requests.push({fast,at:performance.now()});
 await new Promise(resolve=>setTimeout(resolve,fast?200:2700));
 var s=JSON.parse(JSON.stringify(window.fixture));
 if(fast){s.detailsDeferred=true;s.statistics={available:false,loading:true};s.irrigationStatistics={available:false,loading:true};s.clubhouse={available:false,loading:true};}
 return {ok:true,status:200,json:async()=>s};
};
new MutationObserver(()=>{var el=document.getElementById('overall-title');if(el&&el.textContent==='Platz ist belegt'&&!window.timings.statusMs)window.timings.statusMs=performance.now();var stat=document.getElementById('stat-mowing-7d');if(stat&&stat.textContent==='2 Std. 0 Min.'&&!window.timings.detailsMs)window.timings.detailsMs=performance.now();}).observe(document.documentElement,{subtree:true,childList:true,characterData:true});
</script>`;
 function preview(source){return source.replace('[#if profile_json?has_content]${profile_json}[#else]{}[/#if]','{"roleKeys":["Platzwart"]}')
  .replace('https://func-ssv53platzpflege-prod-q7kbw54s.azurewebsites.net/api/platzwart','/synthetic/platzwart')
  .replace(/<img class="logo"[^>]*>/,'<div class="logo" style="margin:auto;font-weight:800;color:#285ea7;padding:20px 0">SSV53</div>')
  .replace('<head>','<head>'+setup).replace('<body>','<body><p style="text-align:center">SIMULATION · Keine Gerätebefehle</p>');}
 const results=[],browser=await chromium.launch({channel:'msedge',headless:true});
 try{for(const width of [320,390])for(const version of ['before','after']){
  const file=path.join(out,`${version}.html`);await fs.writeFile(file,preview(version==='before'?before:current));
  const context=await browser.newContext({viewport:{width,height:1200},locale:'de-DE',timezoneId:'Europe/Berlin'}),page=await context.newPage(),errors=[];
  page.on('pageerror',e=>errors.push(e.message));await page.route('**/*',r=>r.request().url().startsWith('file:')?r.continue():r.abort());
  await page.goto(pathToFileURL(file).href);await page.waitForFunction(()=>window.timings.statusMs>0);
  const statusMs=await page.evaluate(()=>window.timings.statusMs);
  assert.ok(version==='before'?statusMs>=2700:statusMs<1200,`unexpected status latency: ${statusMs}`);
  assert.equal(await page.locator('#mower-next-start').innerText(),'Heute, 22:00 Uhr');
  assert.equal(await page.locator('#manual-start').isDisabled(),true);assert.equal(await page.locator('#manual-park').isDisabled(),false);
  if(version==='after'){
   assert.equal(await page.locator('#stats-message').innerText(),'Die Statistik wird geladen.');
   await page.screenshot({path:path.join(out,`first-status-${width}.png`)});
   await page.waitForFunction(()=>document.getElementById('stats-message').classList.contains('hidden'));
  }
  assert.equal(await page.evaluate(()=>document.documentElement.scrollWidth>innerWidth),false);
  assert.deepEqual(errors,[]);
  results.push({version,width,status_ms:Math.round(statusMs),requests:await page.evaluate(()=>window.requests.length),passed:true});
  if(version==='after')await page.screenshot({path:path.join(out,`loaded-${width}.png`)});
  await context.close();
 }}finally{await browser.close()}
 await fs.writeFile(path.join(out,'browser-check.json'),JSON.stringify({synthetic:true,networkBlocked:true,deviceAcceptance:false,assumedCoreMs:200,assumedOptionalMs:2500,results},null,2)+'\n');
 console.log(JSON.stringify(results));
})().catch(e=>{console.error(e);process.exitCode=1});
