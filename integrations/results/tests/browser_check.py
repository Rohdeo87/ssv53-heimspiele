"""Mobile UI regression with synthetic responses. No external network access."""
from pathlib import Path
import base64
import copy
import json
import shutil
from playwright.sync_api import sync_playwright

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / '.test-output'
OUT.mkdir(exist_ok=True)
html = (ROOT/'appack/Ergebnisse_Tabellen.tpl').read_text('utf-8')
source = 'https://hvbrandenburg-handball.liga.nu/cgi-bin/WebObjects/nuLigaHBDE.woa/wa/clubTeams?club=33547'
ssv = 'Schönwalder SV 53'
match = {'id':'synthetic-1','home':ssv,'away':'Testgegner','isHome':True,'score':None,'state':'scheduled','date':'2099-09-24','time':'18:00','startsAt':'2099-09-24T18:00:00+02:00','sets':[],'sourceUrl':source}
result = dict(match, id='synthetic-2', date='2000-09-20', startsAt='2000-09-20T18:00:00+02:00', score=[27,24],state='result')
row = {'rank':1,'name':ssv,'isSSV':True,'played':1,'points':'2:0','goals':[27,24],'difference':3,'won':1,'drawn':0,'lost':0}
team = {'id':'hb-synthetic','label':'Testmannschaft','league':'Synthetische Testliga','season':'Test','fetchedAt':'2026-09-24T12:00:00Z','teamStatus':'active','matches':[match,result], 'standings':{'kind':'table','rows':[row]},'sourceUrl':source}
withdrawn = dict(copy.deepcopy(team),id='hb-withdrawn',label='Zurückgezogene Testmannschaft',teamStatus='withdrawn',teamNote='zurückgezogen')
withdrawn['standings']['rows'][0].update(status='withdrawn',played=None,points=None)
pixel='iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mP8/x8AAwMCAO+j4gAAAABJRU5ErkJggg=='
vb = dict(copy.deepcopy(team),id='vb-synthetic',label='Mixed',sourceUrl='https://ksv-volleyball-oberhavel.de/mixed_2.html')
vb['standings']={'kind':'image','rows':[],'imageData':'data:image/png;base64,'+pixel,'message':'Synthetische Grafik für UI-Test'}
payloads={'handball':{'schemaVersion':1,'sport':'handball','teams':[team,withdrawn]},'volleyball':{'schemaVersion':1,'sport':'volleyball','teams':[vb]}}
checks=[]
def check(name, ok):
    assert ok, name
    checks.append(name)

with sync_playwright() as p:
    browser=p.chromium.launch(executable_path=shutil.which('chromium') or shutil.which('chromium-browser'),headless=True,args=['--no-sandbox'])
    context=browser.new_context(locale='de-DE',timezone_id='Europe/Berlin',viewport={'width':390,'height':900})
    context.route('**/*',lambda r:r.abort())
    page=context.new_page(); errors=[]; page.on('pageerror',lambda e:errors.append(str(e)))
    page.set_content(html,wait_until='load')
    check('Unconfigured endpoint is disclosed','noch nicht eingerichtet' in page.locator('#ssv-status').inner_text())
    check('No fake production results',page.locator('#ssv-data').is_hidden())
    api_html=html.replace('const API_URL = "";', 'const API_URL = "https://example.test/api/ssv-results";')
    page.evaluate('''data=>{window.__data=data;window.__fail=false;window.fetch=async url=>new Response(JSON.stringify(window.__fail?{}:window.__data[new URL(url).searchParams.get('sport')]),{status:window.__fail?503:200});}''',payloads)
    page.set_content(api_html,wait_until='load'); page.locator('#ssv-data').wait_for(state='visible')
    check('Team selection',page.locator('#ssv-team-select option').count()==2)
    check('Missing score is not zero',page.locator('.ssv-score.pending').count()==2)
    for width in [320,360,390,430,600,768,1024,1440]:
        page.set_viewport_size({'width':width,'height':900})
        check('No horizontal document overflow '+str(width),page.evaluate('document.documentElement.scrollWidth<=innerWidth+1'))
    page.set_viewport_size({'width':390,'height':900})
    page.screenshot(path=str(OUT/'mobile-games.png'),full_page=True)
    page.locator('[data-filter="results"]').click()
    check('Scored match shown',page.locator('#ssv-game-list .ssv-match').count()==1 and '27' in page.locator('#ssv-game-list').inner_text())
    page.locator('#ssv-game-list summary').click()
    check('Match details expand',page.locator('#ssv-game-list details[open]').count()==1)
    page.locator('#ssv-tab-table').click()
    check('Own team highlighted',page.locator('#ssv-table > .ssv-table-scroll tr.own').count()==1)
    page.screenshot(path=str(OUT/'mobile-table.png'),full_page=True)
    page.locator('#ssv-tab-table').press('ArrowLeft')
    check('Keyboard navigation',page.locator('#ssv-tab-games').get_attribute('aria-selected')=='true')
    before=page.locator('#ssv-fetched').inner_text();page.evaluate('window.__fail=true');page.locator('#ssv-refresh').click()
    page.wait_for_function("document.getElementById('ssv-status').textContent.includes('Verbindung unterbrochen')")
    check('Offline keeps last successful timestamp',page.locator('#ssv-fetched').inner_text()==before and page.locator('#ssv-data').is_visible())
    page.evaluate('window.__fail=false');page.locator('#ssv-team-select').select_option('hb-withdrawn')
    check('Withdrawn team warning','zurückgezogen' in page.locator('#ssv-status').inner_text())
    page.locator('#ssv-sport-volleyball').click()
    page.wait_for_function("document.getElementById('ssv-data').hidden===false && document.getElementById('ssv-team-choice').hidden")
    check('Single team hides dropdown',page.locator('#ssv-team-choice').is_hidden())
    page.locator('#ssv-tab-table').click();page.locator('.ssv-image-open').click()
    check('Table image zoom',page.locator('#ssv-image-modal').is_visible())
    page.locator('#ssv-close-modal').press('Escape')
    check('Zoom closes and restores focus',page.locator('#ssv-image-modal').is_hidden() and page.locator('.ssv-image-open').evaluate('(e)=>document.activeElement===e'))
    check('Football stays external','fussball.de/verein/' in page.locator('a.ssv-sport').get_attribute('href'))
    check('No JavaScript exceptions',not errors)
    browser.close()
(OUT/'browser-test-report.json').write_text(json.dumps({'passed':len(checks),'checks':checks,'scope':'Synthetic responses; Chromium; no live HTTP, Azure, Appack or remote CSS validation'},ensure_ascii=False,indent=2),'utf-8')
print(json.dumps({'passed':len(checks),'checks':checks},ensure_ascii=False,indent=2))
