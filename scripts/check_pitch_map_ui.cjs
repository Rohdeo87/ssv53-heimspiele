// Isolated fixture; only public imagery/CDN requests, never device commands.
const {chromium}=require('playwright');
const fs=require('node:fs/promises');
const path=require('node:path');
const {pathToFileURL}=require('node:url');
const assert=require('node:assert/strict');
(async()=>{
 const out=path.resolve('docs/ui-2026-09-13/pitch-map');
 const preview=path.join(out,'preview/appack-preview.html');
 // Test-only reference to the actual Leaflet map; production exposes no map state.
 const source=await fs.readFile(preview,'utf8');
 const testFile=path.join(out,'preview/map-test.html');
 await fs.writeFile(testFile,source.replace('pfMap=window.L.map(','pfMap=window.__testMap=window.L.map('));
 const browser=await chromium.launch({channel:'msedge',headless:true});
 const records=[];
 try{
  for(const [width,height] of [[320,568],[390,844],[430,932],[844,390],[768,1024]]){
   const page=await browser.newPage({viewport:{width,height},locale:'de-DE',timezoneId:'Europe/Berlin'});
   const errors=[],requests=[];page.on('pageerror',e=>errors.push(e.message));
   await page.route('**/*',route=>{
    const u=route.request().url();requests.push(u);
    if(u.startsWith('file:')||u.startsWith('https://unpkg.com/leaflet@1.9.4/dist/')||u.startsWith('https://isk.geobasis-bb.de/mapproxy/dop20c/service/wms'))return route.continue();
    return route.abort();
   });
   await page.goto(pathToFileURL(testFile).href);
   assert.equal(requests.some(u=>u.includes('unpkg.com')||u.includes('geobasis-bb')),false);
   await page.locator('#pf-map-home').click();
   await page.locator('.pf-map-marker').waitFor({state:'visible',timeout:25000});
   await page.waitForFunction(()=>[...document.querySelectorAll('.leaflet-tile')].some(t=>t.complete&&t.naturalWidth>0),{},{timeout:30000});
   async function frame(){return page.evaluate(()=>{
    const m=window.__testMap,s=m.getSize(),a=m.latLngToContainerPoint([52.5941851,13.1295181]),b=m.latLngToContainerPoint([52.5952119,13.1308979]);
    return {contained:a.x>=10&&b.x<=s.x-10&&b.y>=10&&a.y<=s.y-10,center:m.getCenter(),zoom:m.getZoom(),size:s};
   })}
   assert.equal((await frame()).contained,true,'whole pitch on initial viewport '+width);
   const first=await frame();
   // Refreshing position at the far edge must not move the pitch overview.
   await page.evaluate(()=>{window.auditFixture.mower.position={latitude:52.5942,longitude:13.1308};document.getElementById('refresh').click()});
   await page.waitForFunction(()=>document.getElementById('refresh').disabled===false);
   const refreshed=await frame();
   assert.ok(Math.abs(refreshed.center.lat-first.center.lat)<0.00001&&Math.abs(refreshed.center.lng-first.center.lng)<0.00001,'position refresh shifted the map');
   assert.equal(refreshed.contained,true);
   await page.getByRole('button',{name:'Vergrößern',exact:true}).click();
   await page.waitForFunction(z=>window.__testMap.getZoom()>z&&!window.__testMap._animatingZoom,first.zoom);
   const zoomed=await frame();assert.ok(zoomed.zoom>first.zoom);
   await page.evaluate(()=>document.getElementById('refresh').click());
   await page.waitForFunction(()=>document.getElementById('refresh').disabled===false);
   assert.equal((await frame()).zoom,zoomed.zoom,'refresh lost manual zoom');
   await page.getByRole('button',{name:'Platz zeigen',exact:true}).click();
   assert.equal((await frame()).contained,true);
   await page.getByRole('button',{name:'Mäher zeigen',exact:true}).click();
   // Leaflet rounds a pan to screen pixels; tolerate less than one map pixel.
   assert.ok(Math.abs((await frame()).center.lat-52.5942)<0.00001,JSON.stringify(await frame()));
   await page.goBack();await page.goForward();
   assert.equal((await frame()).contained,true,'reopen did not reset to full pitch');
   // Rotate and wait for the real ResizeObserver/Leaflet resize processing.
   await page.setViewportSize({width:height,height:width});
   await page.waitForFunction(()=>{
    const m=window.__testMap,s=m.getSize(),a=m.latLngToContainerPoint([52.5941851,13.1295181]),b=m.latLngToContainerPoint([52.5952119,13.1308979]);
    const box=document.getElementById('pf-mower-map');
    return s.x===box.clientWidth&&s.y===box.clientHeight&&a.x>=10&&b.x<=s.x-10&&b.y>=10&&a.y<=s.y-10;
   });
   await page.setViewportSize({width,height});
   await page.getByRole('button',{name:'Platz zeigen',exact:true}).click();
   assert.equal(await page.evaluate(()=>document.documentElement.scrollWidth>innerWidth),false);
   await page.locator('#pf-page-map').screenshot({path:path.join(out,'pitch-'+width+'.png')});
   await page.evaluate(()=>{window.auditFixture.mower.position=null;document.getElementById('refresh').click()});
   await page.waitForFunction(()=>document.getElementById('pf-map-note').textContent.includes('nicht verfügbar'));
   assert.equal(await page.locator('#pf-mower-map').isVisible(),true);
   assert.equal(await page.locator('.pf-map-marker').count(),0);
   assert.equal(await page.getByRole('button',{name:'Mäher zeigen',exact:true}).isVisible(),false);
   assert.equal((await frame()).contained,true);
   assert.deepEqual(errors,[]);
   records.push({width,height,initial:first,refreshKeepsFrame:true,manualZoomPreserved:true,reopenResets:true,rotationFits:true,missingGpsShowsPitch:true,errors});
   await page.close();
  }
 }finally{await browser.close()}
 await fs.writeFile(path.join(out,'ui-checks.json'),JSON.stringify({synthetic:true,deviceCommands:false,records},null,2)+'\n');
 console.log(JSON.stringify(records));
})().catch(e=>{console.error(e);process.exitCode=1});
