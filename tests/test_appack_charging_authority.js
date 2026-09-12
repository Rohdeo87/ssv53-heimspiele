const assert=require('node:assert/strict');
const test=require('node:test');
const {viewModel,sourceOf,snapshot}=require('./helpers/platzwart_template');
const view=viewModel();
const merge=new Function(sourceOf('mergeDisplayDetails')+';return mergeDisplayDetails;')();

test('Ladeanzeige ersetzt fehlende Herstellerangabe nicht durch die Planungsschätzung',()=>{
 const s=snapshot();
 assert.ok(view.chargingEnd(s));
 assert.equal(view.chargingEnd(s,true),null);
 s.coordination.chargingDisplayEstimate={estimated:true,displayOnly:true,at:'2026-09-09T10:30:00Z',source:'HUSQVARNA_REMAINING_CHARGING_TIME'};
 assert.equal(view.chargingEnd(s,true).toISOString(),'2026-09-09T10:30:00.000Z');
 assert.equal(view.chargingEnd(s).toISOString(),'2026-09-09T11:45:00.000Z');
});

test('Langsame Zusatzdaten können die aktuelle Ladeuhrzeit und Quelle nicht austauschen',()=>{
 const s=snapshot();s.mower.statusTimestamp=123;
 s.coordination.chargingDisplayEstimate={estimated:true,displayOnly:true,at:'2026-09-09T10:30:00Z',source:'HUSQVARNA_REMAINING_CHARGING_TIME'};
 const later=structuredClone(s);later.coordination.chargingDisplayEstimate={estimated:true,at:'2026-09-09T11:10:00Z',source:'OLD_ESTIMATE'};
 const combined=merge(s,later);
 assert.deepEqual(combined.coordination.chargingDisplayEstimate,s.coordination.chargingDisplayEstimate);
 assert.equal(view.chargingEnd(combined,true).toISOString(),'2026-09-09T10:30:00.000Z');
});

test('Alle Ladezeitdarstellungen benutzen dieselbe Anzeigequelle',()=>{
 assert.match(sourceOf('renderCoordination'),/charge=chargingEnd\(s,true\)/);
 assert.match(sourceOf('pfChargingInfo'),/at:chargingEnd\(s,true\)/);
});
