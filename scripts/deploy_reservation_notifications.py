"""Patch only the new mail module and its registration in the active application.

Run under the repository's Azure OIDC deployment identity. No credentials or raw
provider responses are printed. Existing production source is preserved.
"""
import ast,hashlib,json,os,subprocess,time
from datetime import datetime,timezone
from pathlib import Path
import requests

APP='func-ssv53platzpflege-prod-q7kbw54s'
RG='rg-ssv53-platzpflege-prod'
SCM='https://'+APP+'.scm.azurewebsites.net'
ROOT=Path(__file__).resolve().parents[1]
MARKER='# SSV53 reservation notifications registration'


def az(*args):
    p=subprocess.run(['az',*args,'--output','json'],capture_output=True,text=True,timeout=180)
    if p.returncode:raise RuntimeError('Azure CLI operation failed: '+args[0])
    return json.loads(p.stdout) if p.stdout.strip() else None


def main():
    token=az('account','get-access-token','--resource','https://management.azure.com')['accessToken']
    session=requests.Session();session.headers.update(Authorization='Bearer '+token)
    def read(name):
        r=session.get(SCM+'/api/vfs/site/wwwroot/'+name,timeout=60,allow_redirects=False)
        if r.status_code!=200:raise RuntimeError('Cannot read active source: '+name+' HTTP '+str(r.status_code))
        return r.content,r.headers.get('ETag','*')
    def write(name,body,etag):
        r=session.put(SCM+'/api/vfs/site/wwwroot/'+name,data=body,headers={'If-Match':etag,'Content-Type':'application/octet-stream'},timeout=90,allow_redirects=False)
        if r.status_code not in (200,201,204):raise RuntimeError('Scoped source write failed: '+name+' HTTP '+str(r.status_code))
        return r.headers.get('ETag','*')
    original,etag=read('function_app.py')
    active_mail,_=read('order_mail.py')
    names={n.name for n in ast.parse(active_mail).body if isinstance(n,(ast.FunctionDef,ast.ClassDef))}
    required={'OrderMailSettings','OrderMailStore','_validate_ready_request','_plain_order_details','_product_rows_html','_open_authenticated_smtp'}
    if not required<=names:raise RuntimeError('Active mail service does not match required interface')
    # Never replace the active application's order_mail.py or control code.
    module=(ROOT/'reservation_notifications.py').read_bytes();ast.parse(module)
    registration='\n\n'+MARKER+'\nfrom reservation_notifications import notification_blueprint\napp.register_functions(notification_blueprint)\n'
    updated=original if MARKER.encode() in original else original+registration.encode()
    ast.parse(updated)
    write('reservation_notifications.py',module,'*')
    new_etag=write('function_app.py',updated,etag)
    try:
        # Config values are fixed non-secret feature settings only; pickup setting is unchanged.
        # Install with sending disabled, then verify SMTP using the existing no-send check.
        az('functionapp','config','appsettings','set','--resource-group',RG,'--name',APP,'--settings',
           'SSV53_RESERVATION_NOTIFICATIONS_ENABLED=false')
        az('functionapp','restart','--resource-group',RG,'--name',APP)
        keys=az('functionapp','function','keys','list','--resource-group',RG,'--name',APP,'--function-name','ssv53_order_mail')
        key=keys.get('appack-order-admin') or keys.get('default')
        if not key:raise RuntimeError('Existing mail function credential unavailable')
        smtp_ok=False
        for _ in range(12):
            try:
                r=requests.post('https://'+APP+'.azurewebsites.net/api/order-mail',headers={'x-functions-key':key},json={'action':'check'},timeout=45)
                if r.status_code==200 and r.json().get('ok') is True:smtp_ok=True;break
            except (requests.RequestException,ValueError):pass
            time.sleep(5)
        if not smtp_ok:raise RuntimeError('SMTP no-send verification failed; notifications remain disabled')
        cutoff=datetime.now(timezone.utc).isoformat()
        az('functionapp','config','appsettings','set','--resource-group',RG,'--name',APP,'--settings',
           'SSV53_RESERVATION_OFFICE_EMAIL=info@ssv53.de',
           'SSV53_RESERVATION_NOTIFICATIONS_FROM_UTC='+cutoff,
           'SSV53_RESERVATION_NOTIFICATIONS_ENABLED=true')
        verified=False
        for _ in range(18):
            try:
                r=requests.get('https://'+APP+'.azurewebsites.net/api/reservation-notifications',params={'orderId':'SSV53-261014-183248-H1KD'},timeout=30)
                if r.status_code==200 and r.json().get('enabled') is True and r.json().get('applicable') is False:verified=True;break
            except (requests.RequestException,ValueError):pass
            time.sleep(5)
        if not verified:raise RuntimeError('New notification route did not pass old-order exclusion verification')
        print(json.dumps({'deployed':True,'smtp_check_without_mail':True,'old_order_excluded':True,'enabled_from_utc':cutoff,'office':'info@ssv53.de','preserved_order_mail_sha256':hashlib.sha256(active_mail).hexdigest(),'changed_source_files':['reservation_notifications.py','function_app.py (registration only)']}))
    except Exception:
        # Disable first; do not undo any unrelated concurrent deployment.
        az('functionapp','config','appsettings','set','--resource-group',RG,'--name',APP,'--settings','SSV53_RESERVATION_NOTIFICATIONS_ENABLED=false')
        current,current_etag=read('function_app.py')
        if current==updated:
            write('function_app.py',original,current_etag)
            az('functionapp','restart','--resource-group',RG,'--name',APP)
        raise

if __name__=='__main__':
    try:main()
    except Exception as e:raise SystemExit(str(e))
