const test = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const vm = require('node:vm');
const path = require('node:path');
const html = fs.readFileSync(path.join(__dirname, '../appack-platzbelegungsplan-azure.html'), 'utf8');
function fn(name) {
 const match = html.match(new RegExp('(?:async )?function '+name+'\\([^\\n]*[\\s\\S]*?(?=\\n(?:async )?function |$)'));
 assert.ok(match, name); return match[0];
}
function element(extra={}) {
 const attributes = new Map();
 return {hidden:true,disabled:false,textContent:'',innerHTML:'',label:{textContent:''},
 querySelector(){return this.label;},setAttribute(k,v){attributes.set(k,v);},
 removeAttribute(k){attributes.delete(k);},hasAttribute(k){return attributes.has(k);},...extra};
}
function harness() {
 const ctx={Date,Set,JSON,Number,Map,
 state:{calendarReadOnly:true,calendarLoading:false,fetchToken:0,activeSeason:'Sommer',
 canManageTrainings:true,canManageTrainingCancellations:true,canCreateTrainerOccupancies:true,
 canAdministerTrainerOccupancies:true,currentCreator:{id:'trainer-1'}},
 elements:{},window:{console:{error(){}},requestAnimationFrame:f=>f()},
 OCCUPANCY_API_URL:'https://example.test/api/occupancy',storageKey:s=>s,storageGet:()=>null,storageSet(){},
 updateAzureResourceMetadata(){},applySharedTrainingCalendar(){},mapAzureOccupancyEvent:e=>e,
 filterMappedEventsToRange:e=>e,deduplicateMappedEvents:e=>e,updateMatchResourceVisibility(){},
 sharedTrainingSeason:()=>null,trainingHistoryWarning:()=>'',getOccupancyFailureText:()=>'Fehler',
 setStatus(type,message){ctx.topStatus={type,message};},getPopupTimeText:e=>e.time || '',sanitizePopupDescription:s=>s || '',
 renderEvents(events){ctx.shown=events;},getCurrentRange:()=>({start:new Date('2026-10-05Z'),end:new Date('2026-10-12Z')}),
 fetchOccupancyPayload:async()=>({data_source:'azure',events:[{id:'training-1'}],resources:[],training_calendar:{}})};
 ['trainerAddOccupancy','trainerOccupancySubmit','trainerOccupancyDelete','trainerOccupancyMove',
 'trainingCancellationAction','trainingCancellationStatus','trainerOccupancyStatus','trainerOccupancyRetry',
 'popupTitle','popupTime','popupDescription','popupLink','dialogFooter','dialogLayer','trainerOccupancyDialogLayer'].forEach(k=>ctx.elements[k]=element());
 ctx.state.calendar={removeAllEvents(){},getEventById:id=>(ctx.shown||[]).find(e=>e.id===id)};
 const names=['getDialogEventKey','isTrainingCancellationEvent','renderTrainingCancellationAction','isTrainerCreatedOccupancy',
 'renderTrainerOccupancyDeleteAction','renderTrainerOccupancyMoveAction','loadCurrentRange'];
 const snapshot=html.slice(html.indexOf('// Last successful public calendar snapshots.'),html.indexOf('\nfunction loadCurrentRange'));
 vm.createContext(ctx);vm.runInContext(snapshot+'\n'+names.map(fn).join('\n'),ctx);
 return ctx;
}
const start=new Date('2026-10-05Z'), end=new Date('2026-10-12Z');
const training=(extra={})=>({id:'training-1',title:'A',start:new Date('2026-10-08T17:30Z'),time:'19:30',
 extendedProps:{eventKind:'training',occurrenceId:'training-1',description:'Aktueller Termin',...extra}});

test('open detail restores authorized actions and footer immediately after successful refresh',async()=>{
 const h=harness(), current=training();h.state.currentDialogEvent=current;h.elements.dialogLayer.hidden=false;
 h.fetchOccupancyPayload=async()=>({data_source:'azure',resources:[],events:[current]});
 await h.loadRangeEvents(start,end);
 assert.equal(h.state.calendarReadOnly,false);
 assert.equal(h.elements.trainingCancellationAction.hidden,false);
 assert.equal(h.elements.trainerOccupancyMove.hidden,false);
 assert.equal(h.elements.dialogFooter.hidden,false);
 assert.equal(h.elements.trainingCancellationStatus.hidden,true);
});
test('failure gives local editing reason; later recovery updates cancellation state in open detail',async()=>{
 const h=harness();h.state.currentDialogEvent=training();h.elements.dialogLayer.hidden=false;
 h.fetchOccupancyPayload=async()=>{throw Error('503');};await h.loadRangeEvents(start,end);
 assert.equal(h.elements.trainingCancellationAction.hidden,true);
 assert.match(h.elements.trainingCancellationStatus.textContent,/Änderungen sind gerade/);
 const latest=training({cancelled:true});h.fetchOccupancyPayload=async()=>({data_source:'azure',resources:[],events:[latest]});
 await h.loadRangeEvents(start,end,true);
 assert.equal(h.state.currentDialogEvent,latest);
 assert.equal(h.elements.trainingCancellationAction.label.textContent,'Absage widerrufen');
 assert.equal(h.elements.trainerOccupancyMove.hidden,true);
 assert.equal(h.elements.trainingCancellationStatus.textContent,'Dieses Training ist abgesagt.');
});
test('removed event never re-enables actions from cached event after recovery',async()=>{
 const h=harness();h.state.currentDialogEvent=training();h.elements.dialogLayer.hidden=false;
 h.fetchOccupancyPayload=async()=>({data_source:'azure',resources:[],events:[]});await h.loadRangeEvents(start,end);
 assert.equal(h.elements.trainingCancellationAction.hidden,true);assert.equal(h.elements.trainerOccupancyMove.hidden,true);
 assert.match(h.elements.trainingCancellationStatus.textContent,/inzwischen geändert/);
});
test('draft remains visible while read-only and retry follows loading/failure/success without weakening writes',async()=>{
 const h=harness();h.elements.trainerOccupancyDialogLayer.hidden=false;
 h.state.calendarLoading=true;h.setCalendarReadOnly(true);
 assert.equal(h.elements.trainerAddOccupancy.disabled,false);assert.equal(h.elements.trainerOccupancySubmit.disabled,true);
 assert.match(h.elements.trainerOccupancyStatus.textContent,/aktualisiert/);assert.equal(h.elements.trainerOccupancyRetry.hidden,true);
 h.state.calendarLoading=false;h.refreshCalendarEditingDialogs();
 assert.match(h.elements.trainerOccupancyStatus.textContent,/Änderungen sind gerade/);assert.equal(h.elements.trainerOccupancyRetry.hidden,false);
 h.setCalendarReadOnly(false);assert.equal(h.elements.trainerOccupancySubmit.disabled,false);
 assert.equal(h.elements.trainerOccupancyStatus.hidden,true);assert.equal(h.elements.trainerOccupancyRetry.hidden,true);
 const guards=fn('occupancyWriteHeaders')+'\n'+fn('saveTrainerOccupancy');
 assert.match(guards,/if \(state.calendarReadOnly === true\) throw/);
 assert.match(guards,/if \(state.calendarReadOnly === true\) return/);
});
test('same in-flight range is shared; force refresh starts a newer request whose state wins',async()=>{
 const h=harness();let resolveFirst,resolveNext,calls=0;
 h.fetchOccupancyPayload=()=>new Promise(resolve=>{calls++;if(calls===1)resolveFirst=resolve;else resolveNext=resolve;});
 const first=h.loadRangeEvents(start,end), duplicate=h.loadRangeEvents(start,end);
 assert.equal(first,duplicate);assert.equal(calls,1);
 const forced=h.loadRangeEvents(start,end,true);assert.equal(calls,2);
 resolveNext({data_source:'azure',resources:[],events:[{id:'new'}]});await forced;
 assert.equal(h.state.calendarReadOnly,false);assert.equal(h.state.calendarLoading,false);
 resolveFirst({data_source:'azure',resources:[],events:[{id:'old'}]});await first;
 assert.equal(h.shown[0].id,'new');assert.equal(h.state.calendarReadOnly,false);
});
test('recovery never grants trainer permissions to non-role users',async()=>{
 const h=harness();h.state.canManageTrainings=false;h.state.canManageTrainingCancellations=false;
 h.state.canAdministerTrainerOccupancies=false;h.state.currentDialogEvent=training();h.elements.dialogLayer.hidden=false;
 h.fetchOccupancyPayload=async()=>({data_source:'azure',resources:[],events:[training()]});await h.loadRangeEvents(start,end);
 assert.equal(h.elements.trainingCancellationAction.hidden,true);assert.equal(h.elements.trainerOccupancyMove.hidden,true);
 assert.equal(h.elements.trainingCancellationStatus.hidden,true);
});
test('opening draft checks role, performs refresh, and does not send a write',()=>{
 const opener=fn('openTrainerOccupancyDialog');
 assert.match(opener,/!state.canCreateTrainerOccupancies/);
 assert.match(opener,/if \(state.calendarReadOnly === true\) loadCurrentRange\(false\)/);
 assert.match(opener,/trainerOccupancySubmit.disabled = state.calendarReadOnly === true/);
 assert.doesNotMatch(opener,/fetch\(|method: "POST"/);
});
