const assert=require('node:assert/strict');
const test=require('node:test');
const {sourceOf,html}=require('./helpers/platzwart_template');
const point=new Function(sourceOf('pfMapPoint')+';return pfMapPoint')();
const covered=new Function(sourceOf('pfMapCovered')+';return pfMapCovered')();

test('Map does not coerce missing, string, boolean or invalid GPS into a position',()=>{
 for(const p of [null,{}, {latitude:null,longitude:13}, {latitude:true,longitude:13}, {latitude:'52',longitude:13}, {latitude:NaN,longitude:13},{latitude:52,longitude:Infinity},{latitude:91,longitude:13},{latitude:0,longitude:0}])assert.equal(point({mower:{position:p}}),null);
 assert.deepEqual(point({mower:{position:{latitude:52.53,longitude:13.12}}}),[52.53,13.12]);
 assert.equal(covered([0,13]),false);assert.equal(covered([52.53,13.12]),true);
 assert.equal(covered([51.301,13]),false);assert.equal(covered([52,11.15]),false);
});

test('GPS map never grants actions or asks for the phone location',()=>{
 const body=['pfMountMap','pfRenderMap','pfMapCenter','pfLoadMapAssets'].map(sourceOf).join('\n');
 assert.doesNotMatch(body,/geolocation|api\(|START_MOWING|PARK_MOWER|\/action|localStorage|sessionStorage/);
 assert.match(sourceOf('pfRenderMap'),/pfView!=="map"/);
 assert.match(sourceOf('pfRenderMap'),/Die Position kann verzögert sein/);
 assert.match(sourceOf('pfRenderMap'),/pfMapMarker.remove\(\)/);
});

test('Navigation uses existing page history; map assets are lazy and integrity pinned',()=>{
 assert.match(sourceOf('pfGo'),/if\(key==="map"\)pfRenderMap\(\)/);
 assert.match(sourceOf('pfMountMap'),/pfCreatePage\("map"/);
 assert.match(sourceOf('pfLoadMapAssets'),/el.integrity=asset\[2\]/);
 assert.match(sourceOf('pfLoadMapAssets'),/12000/);
 assert.match(sourceOf('pfMountMap'),/GeoBasis-DE\/LGB/);
 assert.doesNotMatch(html,/<script[^>]+src="https:\/\/unpkg.com\/leaflet/);
});
