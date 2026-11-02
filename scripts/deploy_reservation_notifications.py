"""Patch only the new mail module and its registration in the active application.

Run under the repository's Azure OIDC deployment identity. No credentials or raw
provider responses are printed. Existing production source is preserved.
"""
import ast,base64,hashlib,json,os,re,subprocess,time,zipfile
from urllib.parse import urlparse
from datetime import datetime,timezone
from pathlib import Path
import requests

APP='func-ssv53platzpflege-prod-q7kbw54s'
RG='rg-ssv53-platzpflege-prod'
SCM='https://'+APP+'.scm.azurewebsites.net'
ROOT=Path(__file__).resolve().parents[1]
MARKER='# SSV53 reservation notifications registration'
API_VERSION='2024-04-01'
FEATURE_SETTINGS={
    'SSV53_RESERVATION_NOTIFICATIONS_ENABLED',
    'SSV53_RESERVATION_NOTIFICATIONS_FROM_UTC',
    'SSV53_RESERVATION_OFFICE_EMAIL',
}


def arm(session, site_url, method, path, **kwargs):
    """Use the supported site API without CLI runtime/slot probes on Flex."""
    response=session.request(method,site_url+path+'?api-version='+API_VERSION,
                             timeout=90,allow_redirects=False,**kwargs)
    if response.status_code not in (200,201,202,204):
        # Responses may contain settings or keys: never include their body.
        raise RuntimeError('Azure site API failed: '+method+' '+path+' HTTP '+str(response.status_code))
    return response


def feature_settings(session, site_url, changes):
    if not changes or not set(changes)<=FEATURE_SETTINGS:
        raise ValueError('Only reservation notification settings may be changed')
    response=arm(session,site_url,'POST','/config/appsettings/list')
    values=response.json().get('properties')
    if not isinstance(values,dict):
        raise RuntimeError('Application settings are unavailable')
    # ARM replaces the settings dictionary. Preserve every other value in memory;
    # do not save or print credentials, SMTP settings or device configuration.
    updated=dict(values);updated.update(changes)
    headers={'If-Match':response.headers['ETag']} if response.headers.get('ETag') else {}
    arm(session,site_url,'PUT','/config/appsettings',json={'properties':updated},headers=headers)
    for _ in range(12):
        actual=arm(session,site_url,'POST','/config/appsettings/list').json().get('properties',{})
        if all(actual.get(k)==v for k,v in changes.items()):
            print(json.dumps({'feature_settings_verified':sorted(changes)}),flush=True)
            return
        time.sleep(5)
    raise RuntimeError('Reservation notification settings did not become effective')


def existing_mail_key(session, site_url):
    """Read the existing function key before restarting the app; never log it."""
    for _ in range(24):
        response=session.post(site_url+'/functions/ssv53_order_mail/listkeys?api-version='+API_VERSION,
                              timeout=30,allow_redirects=False)
        if response.status_code==200:
            keys=response.json()
            keys=keys.get('properties',keys)
            key=keys.get('appack-order-admin') or keys.get('default')
            if key:
                return key
        elif response.status_code not in (404,503):
            raise RuntimeError('Existing mail function credential unavailable HTTP '+str(response.status_code))
        time.sleep(5)
    raise RuntimeError('Existing mail function credential did not become available')


def deploy_flex(session, package):
    """Use the same OAuth publish API as Azure CLI, preserving built dependencies."""
    with package.open('rb') as source:
        response=session.post(SCM+'/api/publish',params={'RemoteBuild':'false','Deployer':'ssv53-reservation-notifications'},
            data=source,headers={'Content-Type':'application/zip','Cache-Control':'no-cache'},
            timeout=180,allow_redirects=False)
    if response.status_code!=202:
        raise RuntimeError('Flex package publish failed HTTP '+str(response.status_code))
    print(json.dumps({'flex_package_accepted':True}),flush=True)
    # Flex uses status 4 for successful publication. Do not treat an HTTP 202 or
    # a temporarily missing status endpoint as evidence of a healthy rollout.
    for _ in range(150):
        result=session.get(SCM+'/api/deployments/latest',timeout=30,allow_redirects=False)
        if result.status_code in (200,202):
            try:status=result.json().get('status')
            except ValueError:status=None
            if status==4:
                print(json.dumps({'flex_package_published':True}),flush=True)
                return
            if status in (-1,3,5,6):
                raise RuntimeError('Flex package deployment failed status '+str(status))
        elif result.status_code!=404:
            raise RuntimeError('Flex deployment status unavailable HTTP '+str(result.status_code))
        time.sleep(5)
    raise RuntimeError('Flex deployment did not report successful publication')


def active_package_name(session, blobs):
    candidates=[b['name'] for b in blobs if b['name'].endswith('.zip')]
    # OneDeploy creates versioned blobs on each publication (also on rollback).
    # Match the successful deployment ID rather than selecting the newest blob.
    response=session.get(SCM+'/api/deployments/latest',timeout=30,allow_redirects=False)
    if response.status_code==200:
        deployment=response.json()
        deployment_id=str(deployment.get('id') or '')
        if deployment.get('status')==4 and deployment.get('active') is not False:
            matches=[name for name in candidates if name in (deployment_id,deployment_id+'.zip')]
            if len(matches)==1:
                return matches[0]
        print(json.dumps({'package_selection':{
            'zip_names':candidates,
            'deployment_fields':sorted(deployment),
            'deployment_id':deployment_id,
            'deployment_status':deployment.get('status'),
            'deployment_active':deployment.get('active'),
        }}),flush=True)
    if len(candidates)==1:
        return candidates[0]
    raise RuntimeError('Active package cannot be identified uniquely')


def az(*args, timeout=180):
    p=subprocess.run(['az',*args,'--output','json'],capture_output=True,text=True,timeout=timeout)
    if p.returncode:
        # Provider error codes only, never raw CLI output or credential values.
        found=re.search(r'ErrorCode[:=]\s*([A-Za-z0-9_.-]+)',p.stderr) or re.search(r'ERROR:\s*\(([A-Za-z0-9_.-]+)\)',p.stderr)
        code=found.group(1) if found else 'UNCLASSIFIED'
        if 'You do not have the required permissions' in p.stderr:
            code='MISSING_BLOB_READ'
        raise RuntimeError('Azure CLI operation failed: '+args[0]+' code='+code)
    return json.loads(p.stdout) if p.stdout.strip() else None


def main():
    token=az('account','get-access-token','--resource','https://management.azure.com')['accessToken']
    subscription=az('account','show')['id']
    site_url=('https://management.azure.com/subscriptions/'+subscription+
              '/resourceGroups/'+RG+'/providers/Microsoft.Web/sites/'+APP)
    session=requests.Session();session.headers.update(Authorization='Bearer '+token)
    def read(name):
        r=session.get(SCM+'/api/vfs/site/wwwroot/'+name,timeout=60,allow_redirects=False)
        if r.status_code!=200:raise RuntimeError('Cannot read active source: '+name+' HTTP '+str(r.status_code))
        return r.content,r.headers.get('ETag','*')
    def write(name,body,etag):
        r=session.put(SCM+'/api/vfs/site/wwwroot/'+name,data=body,headers={'If-Match':etag,'Content-Type':'application/octet-stream'},timeout=90,allow_redirects=False)
        if r.status_code not in (200,201,204):raise RuntimeError('Scoped source write failed: '+name+' HTTP '+str(r.status_code))
        return r.headers.get('ETag','*')
    package = None
    try:
        original,etag=read('function_app.py')
        active_mail,_=read('order_mail.py')
    except RuntimeError as error:
        if 'HTTP 404' not in str(error): raise
        # Flex Consumption exposes deployment storage, not Kudu VFS.
        # CLI's Site model drops functionAppConfig in some installed versions.
        site=arm(session,site_url,'GET','').json()['properties']
        storage=site.get('functionAppConfig',{}).get('deployment',{}).get('storage',{})
        parsed=urlparse(storage.get('value',''))
        if not parsed.hostname or not parsed.hostname.endswith('.blob.core.windows.net'):
            raise RuntimeError('Deployment package location unavailable')
        account=parsed.hostname.split('.')[0];container=parsed.path.strip('/')
        blob_args=('storage','blob','list','--account-name',account,'--container-name',container,'--auth-mode','login')
        try:
            blobs=az(*blob_args)
        except RuntimeError as error:
            if 'code=MISSING_BLOB_READ' not in str(error):raise
            claims=json.loads(base64.urlsafe_b64decode(token.split('.')[1]+'==='))
            scope=('/subscriptions/'+subscription+'/resourceGroups/'+RG+
                   '/providers/Microsoft.Storage/storageAccounts/'+account+
                   '/blobServices/default/containers/'+container)
            print(json.dumps({'activation_blocked':True,
                              'deployment_principal_object_id':claims['oid'],
                              'deployment_client_id':claims.get('appid') or claims.get('azp'),
                              'required_role':'Storage Blob Data Reader',
                              'required_scope':scope}))
            raise RuntimeError('Deployment identity needs read-only access to its deployment container; no production changes were made') from None
        name=active_package_name(session,blobs)
        package=Path('/tmp/ssv53-active-package.zip')
        az('storage','blob','download','--account-name',account,'--container-name',container,'--name',name,'--auth-mode','login','--file',str(package),'--overwrite',timeout=300)
        with zipfile.ZipFile(package) as archive:
            original=archive.read('function_app.py');active_mail=archive.read('order_mail.py')
        etag=None
        print(json.dumps({'active_package_read':True,'source':'flex-deployment-storage'}),flush=True)
    names={n.name for n in ast.parse(active_mail).body if isinstance(n,(ast.FunctionDef,ast.ClassDef))}
    required={'OrderMailSettings','OrderMailStore','_validate_ready_request','_plain_order_details','_product_rows_html','_open_authenticated_smtp'}
    if not required<=names:raise RuntimeError('Active mail service does not match required interface')
    # The ARM function-key endpoint is transiently absent while the host indexes
    # functions after a restart. Obtain the unchanged credential before deployment.
    key=existing_mail_key(session,site_url)
    print(json.dumps({'existing_mail_credential_available':True}),flush=True)
    # Never replace the active application's order_mail.py or control code.
    module=(ROOT/'reservation_notifications.py').read_bytes();ast.parse(module)
    registration='\n\n'+MARKER+'\nfrom reservation_notifications import notification_blueprint\napp.register_functions(notification_blueprint)\n'
    updated=original if MARKER.encode() in original else original+registration.encode()
    ast.parse(updated)
    patched_package=None
    if package:
        patched_package=Path('/tmp/ssv53-reservation-notifications.zip')
        replacements={'function_app.py':updated,'reservation_notifications.py':module}
        with zipfile.ZipFile(package) as before, zipfile.ZipFile(patched_package,'w',zipfile.ZIP_DEFLATED) as after:
            for info in before.infolist():
                if info.filename not in replacements:
                    after.writestr(info,before.read(info.filename))
            for name,content in replacements.items():after.writestr(name,content)
        # Assert byte-for-byte preservation of every unrelated package entry.
        with zipfile.ZipFile(package) as before, zipfile.ZipFile(patched_package) as after:
            for name in before.namelist():
                if name not in replacements and before.read(name)!=after.read(name):
                    raise RuntimeError('Unrelated production file changed')
    try:
        # Config values are fixed non-secret feature settings only; pickup setting is unchanged.
        # Install with sending disabled, then verify SMTP using the existing no-send check.
        feature_settings(session,site_url,{'SSV53_RESERVATION_NOTIFICATIONS_ENABLED':'false'})
        if patched_package:
            deploy_flex(session,patched_package)
        else:
            write('reservation_notifications.py',module,'*')
            write('function_app.py',updated,etag)
        arm(session,site_url,'POST','/restart')
        smtp_ok=False
        for _ in range(24):
            try:
                r=requests.post('https://'+APP+'.azurewebsites.net/api/order-mail',headers={'x-functions-key':key},json={'action':'check'},timeout=45)
                if r.status_code==200 and r.json().get('ok') is True:smtp_ok=True;break
            except (requests.RequestException,ValueError):pass
            time.sleep(5)
        if not smtp_ok:raise RuntimeError('SMTP no-send verification failed; notifications remain disabled')
        cutoff=datetime.now(timezone.utc).isoformat()
        feature_settings(session,site_url,{
           'SSV53_RESERVATION_OFFICE_EMAIL':'info@ssv53.de',
           'SSV53_RESERVATION_NOTIFICATIONS_FROM_UTC':cutoff,
           'SSV53_RESERVATION_NOTIFICATIONS_ENABLED':'true'})
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
        feature_settings(session,site_url,{'SSV53_RESERVATION_NOTIFICATIONS_ENABLED':'false'})
        if package:
            deploy_flex(session,package)
        else:
            current,current_etag=read('function_app.py')
            if current==updated:
                write('function_app.py',original,current_etag)
                arm(session,site_url,'POST','/restart')
        raise

if __name__=='__main__':
    try:main()
    except Exception as e:raise SystemExit(str(e))
