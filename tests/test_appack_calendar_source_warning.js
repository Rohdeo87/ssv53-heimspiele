const test = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const vm = require('node:vm');
const path = require('node:path');
const html = fs.readFileSync(process.env.CALENDAR_UI_SOURCE || path.join(__dirname, '../appack-platzbelegungsplan-azure.html'), 'utf8');
const code = html.slice(html.indexOf('// Last successful public calendar snapshots.'), html.indexOf('\nfunction loadCurrentRange'));
const NOW = Date.parse('2026-10-15T06:33:00Z');
const HOUR = 60 * 60 * 1000;
const start = new Date('2026-10-12T00:00:00+02:00');
const end = new Date('2026-10-19T00:00:00+02:00');
class Clock extends Date { static now() { return NOW; } }
function payload(age = HOUR, extra = {}) {
 return {data_source:'azure', resources:[], events:[{id:'training-1', calendarId:'rasen'}],
  generated_at_utc: new Date(NOW).toISOString(),
  match_source_generated_at_utc:new Date(NOW-age).toISOString(),
  match_source_fresh:true, training_calendar:{}, ...extra};
}
function harness() {
 const storage = new Map();
 const ctx = {Date:Clock, Set, JSON, Number, state:{fetchToken:0,activeSeason:'Sommer'},
  elements:{trainerAddOccupancy:{}}, OCCUPANCY_API_URL:'https://example.test/api/occupancy',
  storageKey:s=>s, storageGet:k=>storage.get(k), storageSet:(k,v)=>storage.set(k,v),
  updateAzureResourceMetadata(){},applySharedTrainingCalendar(){},mapAzureOccupancyEvent:e=>e,
  filterMappedEventsToRange:e=>e,deduplicateMappedEvents:e=>e,updateMatchResourceVisibility(){},
  renderEvents:e=>{ctx.shown=e;},setStatus:(type,message,retry)=>{ctx.status={type,message,retry};},
  sharedTrainingSeason:()=>null,trainingHistoryWarning:()=>'',getOccupancyFailureText:()=>'Fehler',
  window:{console:{error(){}}},fetchOccupancyPayload:async()=>payload()};
 ctx.state.calendar={removeAllEvents:()=>{ctx.shown=[];}};
 vm.createContext(ctx);vm.runInContext(code,ctx);return ctx;
}
test('HTTP 200 stale response remains quiet through exactly 24h without enabling writes',async()=>{
 for(const age of [0,18*HOUR,24*HOUR-1,24*HOUR]) {
  const h=harness();h.fetchOccupancyPayload=async()=>payload(age,{match_source_fresh:false,training_calendar:{display_only:true}});
  await h.loadRangeEvents(start,end);
  assert.equal(h.status.type,null);assert.equal(h.shown.length,1);
  assert.equal(h.state.calendarReadOnly,true);assert.equal(h.findCalendarSnapshot(start,end),null);
 }
});
test('24h plus one millisecond warns using actual source date, never response generation date',async()=>{
 const h=harness();const stale=payload(24*HOUR+1,{match_source_fresh:false});
 h.fetchOccupancyPayload=async()=>stale;await h.loadRangeEvents(start,end);
 assert.equal(h.status.type,'warning');assert.equal(h.status.retry,true);
 assert.match(h.status.message,/14\.10\.26/);assert.doesNotMatch(h.status.message,/15\.10\.26/);
 assert.doesNotMatch(h.status.message,/Nur Ansicht|werden gerade aktualisiert/);
 assert.equal(h.state.calendarReadOnly,true);
});
test('successful HTTP response cannot hide 30h source age using a fresh response timestamp or age counter',async()=>{
 const h=harness();h.fetchOccupancyPayload=async()=>payload(30*HOUR,{match_source_fresh:false,match_source_age_minutes:0});
 await h.loadRangeEvents(start,end);assert.equal(h.status.type,'warning');
});
test('missing source timestamp uses valid numeric source age, never manufactures a timestamp',()=>{
 const h=harness();
 for(const stamp of [undefined,'invalid']) {
  for(const age of [undefined,null,'1500',NaN,-1,0,1440]) {
   assert.equal(h.calendarSourceUpdateWarning({match_source_generated_at_utc:stamp,match_source_age_minutes:age}), '');
  }
  const message=h.calendarSourceUpdateWarning({match_source_generated_at_utc:stamp,match_source_age_minutes:1440.01});
  assert.match(message,/älter als 24 Stunden/);assert.doesNotMatch(message,/Stand:/);
 }
 assert.equal(h.calendarSourceUpdateWarning({match_source_generated_at_utc:new Date(NOW+HOUR).toISOString()}),'');
});
test('stale day and week payloads preserve last complete cached snapshot and timestamp',async()=>{
 const h=harness();await h.loadRangeEvents(start,end);
 const original=h.findCalendarSnapshot(start,end);const savedAt=original.savedAt;
 for(const [from,to] of [[start,end],[new Date('2026-10-15T00:00:00+02:00'),new Date('2026-10-16T00:00:00+02:00')]]) {
  h.fetchOccupancyPayload=async()=>payload(18*HOUR,{match_source_fresh:false,events:[{id:'new-readonly'}]});
  await h.loadRangeEvents(from,to);assert.equal(h.status.type,null);assert.equal(h.state.calendarReadOnly,true);
  assert.equal(h.findCalendarSnapshot(from,to).savedAt,savedAt);
  assert.equal(h.findCalendarSnapshot(from,to).payload.events[0].id,'training-1');
  assert.equal(h.readCalendarSnapshots().length,1);
 }
});
test('an old-range stale response never overwrites a newer range or its warning',async()=>{
 const h=harness();let resolve;h.fetchOccupancyPayload=()=>new Promise(r=>{resolve=r;});
 const first=h.loadRangeEvents(start,end);
 h.fetchOccupancyPayload=async()=>payload(18*HOUR,{match_source_fresh:false,events:[{id:'new-range'}]});
 await h.loadRangeEvents(end,new Date('2026-10-26T00:00:00+01:00'));
 resolve(payload(30*HOUR,{match_source_fresh:false}));await first;
 assert.equal(h.shown[0].id,'new-range');assert.equal(h.status.type,null);assert.equal(h.state.calendarReadOnly,true);
});
test('successful recovery removes an old warning and re-enables edits',async()=>{
 const h=harness();h.fetchOccupancyPayload=async()=>payload(30*HOUR,{match_source_fresh:false});
 await h.loadRangeEvents(start,end);assert.equal(h.status.type,'warning');
 h.fetchOccupancyPayload=async()=>payload();await h.loadRangeEvents(start,end);
 assert.equal(h.status.type,null);assert.equal(h.state.calendarReadOnly,false);
 assert.equal(h.findCalendarSnapshot(start,end).payload.match_source_fresh,true);
});
test('real unavailable historical ranges keep their own warning and read-only state',async()=>{
 const h=harness();h.trainingHistoryWarning=()=> 'Frühere Trainings bis 8. September sind hier nicht verfügbar. Aktuelle Termine werden angezeigt.';
 h.fetchOccupancyPayload=async()=>payload(HOUR,{training_calendar:{partial:true,unavailableRanges:[{scope:'training'}]}});
 await h.loadRangeEvents(start,end);
 assert.equal(h.status.type,'warning');assert.match(h.status.message,/Frühere Trainings/);
 assert.doesNotMatch(h.status.message,/Aktualisierung gerade|Nur Ansicht/);
 assert.equal(h.state.calendarReadOnly,true);assert.equal(h.findCalendarSnapshot(start,end),null);
});
