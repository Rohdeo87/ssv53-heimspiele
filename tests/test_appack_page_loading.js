const assert=require('node:assert/strict');
const test=require('node:test');
const {sourceOf,snapshot}=require('./helpers/platzwart_template');
const merge=new Function(sourceOf('mergeDisplayDetails')+';return mergeDisplayDetails;')();
const deferred=()=>{let resolve,reject;const promise=new Promise((yes,no)=>{resolve=yes;reject=no});return{promise,resolve,reject}};
const settle=()=>new Promise(resolve=>setImmediate(resolve));

function harness(){
 const state={status:null,loadPromise:null,detailsPromise:null,detailsEpoch:0,detailsRetryAt:0,liveReceivedAt:0},calls=[],renders=[],extras=[],nodes=new Map();
 let token='session-a';
 const document={hidden:false,getElementById(id){if(!nodes.has(id))nodes.set(id,{classList:{add(){},remove(){},toggle(){}},textContent:'',disabled:false});return nodes.get(id)},querySelectorAll(){return[]}};
 const dependencies={state,document,session:()=>({token}),stored:()=>({deviceId:'test'}),DEVICE_KEY:'device',SESSION_KEY:'session',
  sessionStorage:{removeItem(){}},show(){},text(){},clearActionError(){},friendlyError:()=> 'Fehler',
  api(path){const pending=deferred();calls.push({path,...pending});return pending.promise},
  render(s){state.status=s;renders.push(s)},renderStatistics(s){extras.push(s)},renderIrrigationStatistics(){},renderClubhouse(){},renderCoordination(){},
  bladeResetFailed:()=>false,deviceControlsOpen:s=>s.deviceControlsAvailable===true,renderOccupancy(){},renderTrainingControl(){}};
 const methods=new Function(...Object.keys(dependencies),['mergeDisplayDetails','unavailableDisplayDetails','loadDetails','pollStatus','load'].map(sourceOf).join('\n')+';return {load,loadDetails,pollStatus};')(...Object.values(dependencies));
 return{...methods,state,calls,renders,extras,document,setToken:t=>token=t};
}

test('Langsame Zusatzdaten halten den ersten Status nicht auf; parallele Aufrufer warten auf dieselbe neue Antwort',async()=>{
 const h=harness(),a=h.load(),b=h.load();assert.equal(a,b);assert.equal(h.calls.length,1);assert.equal(h.calls[0].path,'/status?view=live');
 const fresh={...snapshot(),detailsDeferred:true};h.calls[0].resolve(fresh);
 assert.equal(await a,fresh);assert.equal(await b,fresh);assert.equal(h.renders.length,1);
 assert.equal(h.calls.length,2);assert.equal(h.calls[1].path,'/status');assert.equal(h.state.loading,false);
 h.calls[1].resolve({...fresh,detailsDeferred:false,statistics:{available:true,mowingMinutes7d:120}});await settle();
 assert.equal(h.extras[0].mowingMinutes7d,120);assert.equal(h.renders.length,1,'late detail response never renders controls');
});

test('Späte Details überschreiben weder neue Sperren noch aktuelle Gerätewerte',()=>{
 const now=snapshot();now.mower.statusTimestamp=123;now.mower.activity='MOWING';now.statistics={currentAreaProgress:50,bladeUsageSeconds:900,totalRunningSeconds:1000};
 now.controlsAvailable=false;now.manualControl={status:'STOPPED'};now.coordination={blockers:[{code:'IRRIGATION_ACTIVE'}]};
 const old={...snapshot(),mower:{activity:'CHARGING',telemetryFresh:true,statusTimestamp:123,batteryPercent:73},statistics:{estimatedAreaCycles7d:2,currentAreaProgress:1,bladeUsageSeconds:0},irrigationStatistics:{available:true},clubhouse:{events:[]}};
 const result=merge(now,old);
 for(const key of ['mower','controlsAvailable','manualControl','coordination','occupancy','irrigation','automation'])assert.equal(result[key],now[key],key);
 assert.equal(result.statistics.currentAreaProgress,50);assert.equal(result.statistics.bladeUsageSeconds,900);assert.equal(result.statistics.mownAreaEquivalents7d,2.5);
});

test('Ladeende darf nur zur identischen frischen Lademeldung ergänzt werden',()=>{
 const live=snapshot();live.mower.statusTimestamp=123;live.coordination.chargingEndEstimate=null;
 const details=structuredClone(live);details.coordination.chargingEndEstimate={estimated:true,at:'2026-09-09T11:45:00Z'};
 assert.deepEqual(merge(live,details).coordination.chargingEndEstimate,details.coordination.chargingEndEstimate);
 for(const change of [{statusTimestamp:124},{batteryPercent:74},{telemetryFresh:false},{activity:'PARKED_IN_CS'}]){
  const other=structuredClone(live);Object.assign(other.mower,change);assert.equal(merge(other,details).coordination.chargingEndEstimate,null);
 }
 details.generatedAt='2026-09-09T09:59:29Z';assert.equal(merge(live,details).coordination.chargingEndEstimate,null);
});

test('Zusatzdatenfehler lässt aktuelle Bedienregeln unverändert und bremst Wiederholungen',async()=>{
 const h=harness(),ready=h.load(),s={...snapshot(),detailsDeferred:true};h.calls[0].resolve(s);await ready;
 h.calls[1].reject(new Error('timeout'));await settle();assert.equal(h.state.status,s);assert.equal(h.renders.length,1);
 h.loadDetails(s);assert.equal(h.calls.length,2);assert.ok(h.state.detailsRetryAt>Date.now());
});

test('Nach Statusfehler darf eine ältere Detailantwort keine Freigabe wiederherstellen',async()=>{
 const h=harness(),first=h.load();h.calls[0].resolve({...snapshot(),detailsDeferred:true});await first;
 const next=h.load();h.calls[2].reject(new Error('offline'));assert.equal(await next,null);
 h.calls[1].resolve({...snapshot(),statistics:{available:true}});await settle();
 assert.equal(h.state.status.controlsAvailable,false);assert.equal(h.extras.length,0);assert.equal(h.renders.length,1);
});

test('Alte Sitzung und verborgene Seite erhalten keine späten Details',async()=>{
 for(const kind of ['session','hidden']){
  const h=harness(),first=h.load();h.calls[0].resolve({...snapshot(),detailsDeferred:true});await first;
  if(kind==='session')h.setToken('session-b');else h.document.hidden=true;
  h.calls[1].resolve(snapshot());await settle();assert.equal(h.extras.length,0);
 }
 const h=harness(),first=h.load();h.setToken('session-b');h.calls[0].resolve(snapshot());assert.equal(await first,null);assert.equal(h.renders.length,0);
});

test('Kein Hintergrundpolling und keine zusätzliche Abfrage bei warmem Cache oder älterem Backend',async()=>{
 const h=harness();h.document.hidden=true;h.pollStatus();assert.equal(h.calls.length,0);
 h.document.hidden=false;const read=h.pollStatus();h.calls[0].resolve(snapshot());await read;
 assert.equal(h.calls.length,1);assert.equal(h.renders.length,1);
});

test('Abgelaufene Anmeldung beim Nachladen entzieht die angezeigte Bedienfreigabe sofort',async()=>{
 const h=harness(),first=h.load();h.calls[0].resolve({...snapshot(),deviceControlsAvailable:true,detailsDeferred:true});await first;
 h.calls[1].reject({status:401});await settle();assert.equal(h.state.status.controlsAvailable,false);
 assert.equal(h.state.status.deviceControlsAvailable,false);assert.equal(h.state.detailsEpoch,1);assert.equal(h.state.liveReceivedAt,0);
});

test('Auch fehlerhafte Zusatzdaten in HTTP 200 bremsen Wiederholungen während Aktionspolling',async()=>{
 const h=harness(),first=h.load(),s={...snapshot(),detailsDeferred:true,statistics:{available:false,loading:true}};
 h.calls[0].resolve(s);await first;h.calls[1].resolve(s);await settle();
 assert.ok(h.state.detailsRetryAt>Date.now());assert.equal(h.extras[0].loading,false);
 for(let i=0;i<3;i++){const next=h.load();h.calls.at(-1).resolve(s);await next;}
 assert.equal(h.calls.filter(x=>x.path==='/status').length,1);assert.equal(h.state.status.statistics.loading,false);
});
