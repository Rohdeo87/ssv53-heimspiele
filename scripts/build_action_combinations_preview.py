"""Reproducible UI scenarios. Network and real device commands are disabled."""
from copy import deepcopy
from pathlib import Path
from scripts.build_audit_preview import build
from scripts.build_wunschdesign_preview import fixtures

OUT = Path(__file__).resolve().parents[1] / 'docs/ui-2026-09-12/contextual-actions/preview'

def scenarios():
    base = deepcopy(fixtures()['charging-unknown'])
    base['manualControl'].update(canStart=True, canPark=True, canResume=True, source=None, requestStatus='CONFIRMED')
    base['manualControl']['confirmations'].update(waterChoiceRequired=False, dryingRequired=False, occupancyRequired=False)
    base['irrigation']['safety'].update(available=True, fresh=True, clear_now=True, active_zone_count=0, imminent_zone_count=0)
    base['mower'].update(state='IN_OPERATION', connected=True, telemetryFresh=True)
    base['automation']={}
    base['coordination'].update(dryUntil=None,releaseNotBefore=None,blockers=[])
    cases={}
    for name in ['stopped','mowing','returning','parked','water','between-zones','choice','pending','charging','offline']:
        s=deepcopy(base)
        if name=='stopped': s['mower'].update(state='STOPPED',activity='NOT_APPLICABLE',telemetryFresh=False)
        if name=='mowing': s['mower'].update(activity='MOWING',workAreaProgress=41)
        if name=='returning': s['mower'].update(activity='GOING_HOME')
        if name=='parked': s['manualControl'].update(status='MANUAL_PARKED',message='Die Automatik wartet auf deine Freigabe.')
        if name in ['water','between-zones','choice']:
            s['automation']['irrigationPhase']='RUNNING'
            s['irrigation']['safety'].update(clear_now=name=='between-zones',active_zone_count=0 if name=='between-zones' else 1)
            s['irrigation']['zones'][0]['running']=name!='between-zones'
            s['manualControl']['canStart']=False
        if name=='choice':
            s['manualControl'].update(status='WAITING_WATER',canStart=True)
            s['manualControl']['confirmations']['waterChoiceRequired']=True
        if name=='pending': s['manualControl'].update(status='PREPARED',requestStatus='PENDING',message='Bitte auf die Bestätigung des Mähers warten.')
        if name=='offline': s['mower'].update(connected=False,telemetryFresh=False)
        cases[name]=s
    return cases

if __name__=='__main__':
    cases=scenarios()
    for name,payload in cases.items(): build(output=OUT/name,fixture=payload)
    links=''.join(f'<li><a href="{name}/appack-preview.html">{name}</a></li>' for name in cases)
    (OUT/'index.html').write_text(f'<!doctype html><meta charset="utf-8"><title>Aktionen prüfen</title><h1>Simulation – keine Gerätebefehle</h1><ul>{links}</ul>',encoding='utf-8')
    print(OUT)
