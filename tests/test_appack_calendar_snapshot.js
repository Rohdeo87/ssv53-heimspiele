const test = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const vm = require('node:vm');
const path = require('node:path');
const html = fs.readFileSync(path.join(__dirname, '../appack-platzbelegungsplan-azure.html'), 'utf8');
const code = html.slice(html.indexOf('// Last successful public calendar snapshots.'), html.indexOf('\nfunction loadCurrentRange'));
const start = new Date('2026-09-28T00:00:00Z'), end = new Date('2026-10-05T00:00:00Z');
const payload = () => ({data_source:'azure', resources:[], events:[{id:'training-1', calendarId:'rasen'}], training_calendar:{}, match_source_fresh:true});
function harness(storage = new Map(), quota = false) {
 const ctx = {Date, Set, JSON, Number, state:{fetchToken:0,activeSeason:'sommer'}, elements:{trainerAddOccupancy:{}},
 OCCUPANCY_API_URL:'https://example.test/api/occupancy',
 storageKey:s => s, storageGet:k => storage.get(k), storageSet:(k,v) => {if (!quota) storage.set(k,v);},
 updateAzureResourceMetadata(){},applySharedTrainingCalendar(){},mapAzureOccupancyEvent:e=>e,
 filterMappedEventsToRange:e=>e,deduplicateMappedEvents:e=>e,updateMatchResourceVisibility(){},
 renderEvents:e=>{ctx.shown=e;},setStatus:(type,message,retry)=>{ctx.status={type,message,retry};},
 sharedTrainingSeason:p=>p.sharedSeason || null, trainingHistoryWarning:()=>'',getOccupancyFailureText:()=> 'Fehler',window:{console:{error(){}}},
 fetchOccupancyPayload:async()=>payload()};
 ctx.state.calendar={removeAllEvents:()=>{ctx.shown=[];}};
 vm.createContext(ctx);vm.runInContext(code,ctx);return ctx;
}
test('network failure keeps events, timestamp and read-only state; recovery clears warning', async()=>{
 const h=harness();await h.loadRangeEvents(start,end);assert.equal(h.state.calendarReadOnly,false);
 h.fetchOccupancyPayload=async()=>{throw Error('503');};await h.loadRangeEvents(start,end);
 assert.equal(h.shown[0].id,'training-1');assert.equal(h.status.type,null);
 assert.equal(h.elements.trainerAddOccupancy.disabled,false);
 assert.equal(h.state.calendarReadOnly,true);
 h.fetchOccupancyPayload=async()=>payload();await h.loadRangeEvents(start,end);assert.equal(h.state.calendarReadOnly,false);
});
test('persisted snapshot survives reopening offline',async()=>{
 const storage=new Map();await harness(storage).loadRangeEvents(start,end);
 const h=harness(storage);h.fetchOccupancyPayload=async()=>{throw Error('offline');};await h.loadRangeEvents(start,end);
 assert.equal(h.shown.length,1);assert.equal(h.state.calendarReadOnly,true);
});
test('cached week covers day, not another week or season',async()=>{
 const h=harness();await h.loadRangeEvents(start,end);
 assert.ok(h.findCalendarSnapshot(new Date('2026-09-29'),new Date('2026-09-30')));
 h.fetchOccupancyPayload=async()=>{throw Error('offline');};await h.loadRangeEvents(end,new Date('2026-10-12'));
 assert.equal(h.shown.length,0);assert.match(h.status.message,/kein gespeicherter Kalender/);
 h.state.activeSeason='winter';assert.equal(h.findCalendarSnapshot(start,end),null);
});
test('storage unavailable still preserves in-memory snapshot',async()=>{
 const h=harness(new Map(),true);await h.loadRangeEvents(start,end);
 h.fetchOccupancyPayload=async()=>{throw Error('offline');};await h.loadRangeEvents(start,end);assert.equal(h.shown.length,1);
});
test('damaged storage does not stop live loading',async()=>{
 const h=harness(new Map([['occupancy-snapshots-v1:https://example.test/api/occupancy','broken']]));
 await h.loadRangeEvents(start,end);assert.equal(h.shown.length,1);assert.equal(h.state.calendarReadOnly,false);
});
test('partial or stale responses cannot replace complete snapshot or enable writes',async()=>{
 for(const extra of [{match_source_fresh:false},{match_source_fresh:true,match_source_fallback:true},
 {training_calendar:{display_only:true}},{training_calendar:{partial:true}},
 {training_calendar:{fail_closed:true}},{training_cancellations:{available:false}},{special_occupancy:{available:false}}]){
 const h=harness();await h.loadRangeEvents(start,end);
 h.fetchOccupancyPayload=async()=>({...payload(),...extra,events:[]});await h.loadRangeEvents(start,end);
 assert.equal(h.state.calendarReadOnly,true);assert.equal(h.findCalendarSnapshot(start,end).payload.events.length,1);
 }
});

test('fresh match cache and display-only training keep editing locked even without a previous snapshot',async()=>{
 for(const extra of [{match_source_fresh:true,match_source_fallback:true},{training_calendar:{display_only:true}}]) {
  const h=harness();h.fetchOccupancyPayload=async()=>({...payload(),...extra});
  await h.loadRangeEvents(start,end);
  assert.equal(h.shown[0].id,'training-1');
  assert.equal(h.state.calendarReadOnly,true);
  assert.equal(h.findCalendarSnapshot(start,end),null);
 }
});
test('late response cannot overwrite newer range',async()=>{
 const h=harness();let resolve;h.fetchOccupancyPayload=()=>new Promise(r=>{resolve=r;});
 const first=h.loadRangeEvents(start,end);h.fetchOccupancyPayload=async()=>({...payload(),events:[{id:'new'}]});
 await h.loadRangeEvents(end,new Date('2026-10-12'));resolve(payload());await first;assert.equal(h.shown[0].id,'new');
});
test('cache is bounded and empty successful calendars remain valid',async()=>{
 const h=harness();h.fetchOccupancyPayload=async()=>({...payload(),events:[]});
 for(let i=0;i<12;i++)await h.loadRangeEvents(new Date(+start+i*86400000),new Date(+end+i*86400000));
 assert.equal(h.readCalendarSnapshots().length,8);assert.equal(h.readCalendarSnapshots()[0].payload.events.length,0);
});
test('write headers enforce read-only even after an already-open confirmation',()=>{
 const source=html.slice(html.indexOf('function occupancyWriteHeaders()'),html.indexOf('const OCCUPANCY_FETCH_TIMEOUT'));
 const h={state:{calendarReadOnly:true}};vm.createContext(h);vm.runInContext(source,h);
 assert.throws(()=>h.occupancyWriteHeaders(),/nur zur Ansicht/);
});

test('shared training season survives a different device default season',async()=>{
 const storage=new Map(), h=harness(storage);
 h.fetchOccupancyPayload=async()=>({...payload(),sharedSeason:'Sommer'});await h.loadRangeEvents(start,end);
 const reopened=harness(storage);reopened.state.activeSeason='winter';
 reopened.fetchOccupancyPayload=async()=>{throw Error('offline');};await reopened.loadRangeEvents(start,end);
 assert.equal(reopened.shown.length,1);assert.equal(reopened.state.calendarReadOnly,true);
});

test('routine refresh shows no warning even with a stored calendar',async()=>{
 const h=harness();await h.loadRangeEvents(start,end);
 const snapshot=h.findCalendarSnapshot(start,end);
 for(const age of [60000,24*60*60*1000]) {
  snapshot.savedAt=Date.now()-age;h.showCalendarSnapshot(snapshot,start,end,true);
  assert.equal(h.status.type,null);assert.equal(h.shown.length,1);
 }
});
test('only a failed refresh with an older snapshot shows a dated warning',async()=>{
 const h=harness();await h.loadRangeEvents(start,end);
 const snapshot=h.findCalendarSnapshot(start,end);
 snapshot.savedAt=Date.now()-25*60*60*1000;
 h.showCalendarSnapshot(snapshot,start,end,false);
 assert.equal(h.status.type,'warning');assert.match(h.status.message,/Stand:/);
 assert.doesNotMatch(h.status.message,/Freigabe|Nur Ansicht|geändert/);
});

test('failed refresh stays quiet through exactly 24 hours and warns only beyond',async()=>{
 const h=harness();await h.loadRangeEvents(start,end);
 const snapshot=h.findCalendarSnapshot(start,end);
 const now=Date.now();h.Date=class extends Date {static now(){return now;}};
 for(const age of [60000,2*60*60*1000,24*60*60*1000]) {
  snapshot.savedAt=now-age;h.showCalendarSnapshot(snapshot,start,end,false);
  assert.equal(h.status.type,null);
 }
 snapshot.savedAt=now-24*60*60*1000-1;h.showCalendarSnapshot(snapshot,start,end,false);
 assert.equal(h.status.type,'warning');assert.equal(h.status.retry,true);
});
