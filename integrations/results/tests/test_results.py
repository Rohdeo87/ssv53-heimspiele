"""Synthetic fixtures: these tests do not prove a successful live deployment."""
import copy
import json
from datetime import datetime, timezone
from pathlib import Path
import pytest
from bs4 import BeautifulSoup
from ssv_results import parsers as p
from ssv_results.collector import Fetcher, FetchError, collect, check_loss

BASE = 'https://hvbrandenburg-handball.liga.nu/cgi-bin/WebObjects/nuLigaHBDE.woa/wa/groupPage?championship=HVBrandenburg+2026+%2F+2027&group=123'
VB = 'https://ksv-volleyball-oberhavel.de/mixed_2/ergebnisse-26-27_1.html'
SSV = 'Schönwalder SV 53'

def table(headers, values):
    return '<table><thead><tr>' + ''.join('<th>' + h + '</th>' for h in headers) + '</tr></thead><tbody>' + ''.join('<tr>' + ''.join('<td>' + str(c) + '</td>' for c in r) + '</tr>' for r in values) + '</tbody></table>'

def hb_standings():
    return '<h2>Tabelle</h2>' + table(['','Rang','Mannschaft','Begegnungen','S','U','N','Tore','+/-','Punkte'], [
        ['',1,'Gegner A',2,2,0,0,'40:20','+20','4:0'], ['',2,SSV,2,0,0,2,'20:40','-20','0:4']])

def hb_games(values=None):
    return table(['Tag','Datum','Zeit','Ort','Nr.','Heimmannschaft','Gastmannschaft','','',''], values or [
        ['Sa','12.09.2026','15:00','4620','1001',SSV,'Gegner A','27:39','',''],
        ['','','18:00','4620','1002','Gegner B',SSV,'Sch./Must.','',''],
        ['Sa','10.10.2026','18:15','4620','1003',SSV,'Gegner C','','','']])

def hb_teams_html():
    return '<h1>Schönwalder SV 53</h1>' + table(['Mannschaft','Liga','Mannschaftsverantwortlicher'], [
        ['Männer',f'<a href="{BASE}">Testliga</a>','Dieser Kontakt darf nicht übernommen werden'],
        ['Vorjahr',f'<a href="{BASE.replace("2026","2025").replace("2027","2026")}&old=1">Vorjahresliga</a>','Privatkontakt']])

def vb_main():
    return '<nav><img src="/file/i/logo.jpg"></nav><h1>2. Kreisklasse Mixed</h1><p><img src="/file/i/table.jpg"></p><h6>Stand: 19.07.2026</h6><p><a href="/mixed_2/ergebnisse-26-27_1.html">Ergebnisse Saison 26/27</a></p>' + table(['Datum und Zeit','Nr.','Heimteam','Ergebnis','Gastteam','Sätze'],[])

def vb_games(score='-', sets=''):
    return table(['Datum und Zeit','Nr.','Heimteam','Ergebnis','Gastteam','Sätze'],[
        ['Spieltag 1','','','','',''],['Di, 22.09.26, 19:45',1,'Schönwalder SV 1953',score,'SG Vehlefanz 2',sets],
        ['Do, 24.09.26, 20:00',2,'Gegner A','-','Gegner B','']])

@pytest.mark.parametrize('name', [SSV,'Schönwalder SV 1953','Schoenwalder SV 53','Schönwalder SV 53 II'])
def test_team_aliases(name):
    assert p.is_ssv(name)

@pytest.mark.parametrize('name', ['SSV Falkensee','SSV','SV Schönwalde','Gegner Schönwalder SV 53'])
def test_no_false_team_alias(name):
    assert not p.is_ssv(name)

@pytest.mark.parametrize('date,offset', [('12.09.2026','+02:00'),('12.01.2027','+01:00')])
def test_timezone(date,offset):
    assert p.date_fields(date,'15:00')['startsAt'].endswith(offset)

def test_unknown_time_stays_unknown():
    data=p.date_fields('12.09.2026','offen')
    assert data['date']=='2026-09-12' and data['startsAt'] is None and data['time'] is None

def test_invalid_date_rejected():
    with pytest.raises(p.ParseError): p.date_fields('31.02.2026','15:00')

def test_rowspan():
    html='<table><tr><td rowspan="2">Datum</td><td>1</td></tr><tr><td>2</td></tr></table>'
    grid=p.rows(BeautifulSoup(html,'html.parser').table)
    assert [[p.text(c) for c in r] for r in grid]==[['Datum','1'],['Datum','2']]

def test_discovery_current_season_no_contacts():
    teams=p.handball_teams(hb_teams_html(),2026)
    assert len(teams)==1 and teams[0]['label']=='Männer'
    assert 'Kontakt' not in json.dumps(teams)

def test_unknown_season_rejected():
    with pytest.raises(p.ParseError): p.handball_teams(hb_teams_html(),2024)

def test_handball_table():
    rows=p.handball_standings(hb_standings())['rows']
    assert rows[1]['isSSV'] and rows[1]['points']=='0:4' and rows[1]['difference']==-20

def test_table_column_reordering():
    result=p.handball_standings(table(['Punkte','Mannschaft','Tore','Rang','Begegnungen'],[['0:0',SSV,'0:0',1,0]]))
    assert result['rows'][0]['rank']==1

def test_withdrawn_row_not_zero():
    html=hb_standings().replace('<td>2</td><td>Schönwalder SV 53</td><td>2</td><td>0</td><td>0</td><td>2</td><td>20:40</td><td>-20</td><td>0:4</td>', '<td>2</td><td>Schönwalder SV 53</td><td></td><td colspan="6">zurückgezogen am 20.07.2026</td>')
    row=p.handball_standings(html)['rows'][1]
    assert row['status']=='withdrawn' and row['played'] is None and row['points'] is None

def test_minis_participants_not_standings():
    result=p.handball_standings('<h2>Teilnehmende Mannschaften</h2>'+table(['Raster','WR','Mannschaft'],[['-','',SSV],['-','','Gegner']]))
    assert result['kind']=='participants' and 'rank' not in result['rows'][0]

def test_missing_expected_table_is_error():
    with pytest.raises(p.ParseError): p.handball_standings('<h2>Tabelle</h2><p>Wartung</p>')

def test_schedule_link_discovered_not_guessed():
    target=BASE+'&displayDetail=meetings&displayTyp=vorrunde'
    assert p.handball_schedule_url(f'<a href="{target}">Spielplan (Gesamt)</a>',BASE)==target

def test_cup_direct_schedule():
    url=BASE.replace('HVBrandenburg','HVBr+Pokal')
    assert p.handball_schedule_url('<h2>Spielplan</h2>'+hb_games(),url)==url

def test_schedule_wrong_league_rejected():
    with pytest.raises(p.ParseError): p.handball_schedule_url(f'<a href="{BASE.replace("group=123","group=456")}">Spielplan (Gesamt)</a>',BASE)

def test_scores_and_date_inheritance_no_referees():
    result=p.handball_matches(hb_games(),'123',BASE,2026)
    assert len(result)==3 and result[0]['score']==[27,39]
    assert result[1]['date']=='2026-09-12' and result[1]['score'] is None
    assert 'Must' not in json.dumps(result)

def test_cancelled_game_not_finished():
    html=hb_games([['Sa','12.09.2026','abgesagt','4620','123',SSV,'Gegner','','','']])
    result=p.handball_matches(html,'123',BASE,2026)
    assert result[0]['state']=='cancelled' and result[0]['score'] is None

def test_non_numeric_official_status_not_score():
    html=hb_games([['Sa','12.09.2026','15:00','4620','123',SSV,'Gegner','WG','','']])
    item=p.handball_matches(html,'123',BASE,2026)[0]
    assert item['state']=='status' and item['score'] is None

def test_festival_not_claimed_as_ssv_match():
    html=hb_games([['Sa','10.10.2026','10:00','1405','0','Oranienburger HC','angemeldete Mannschaften','','','']])
    item=p.handball_matches(html,'123',BASE,2026,festivals=True)[0]
    assert item['kind']=='festival' and item['isHome'] is None and 'Teilnahme' in item['note']

def test_duplicate_game_conflict_rejected():
    html=hb_games([['Sa','12.09.2026','15:00','','123',SSV,'Gegner','10:20','',''],['Sa','12.09.2026','15:00','','123',SSV,'Gegner','10:21','','']])
    with pytest.raises(p.ParseError): p.handball_matches(html,'123',BASE,2026)

def test_old_season_game_rejected():
    with pytest.raises(p.ParseError): p.handball_matches(hb_games().replace('12.09.2026','12.09.2025'),'123',BASE,2026)

def test_volleyball_links_select_content_not_logo():
    links=p.volleyball_links(vb_main(),2026)
    assert links['imageUrl'].endswith('/table.jpg') and links['sourcePublishedAt']=='19.07.2026'

def test_volleyball_image_ambiguity_rejected():
    with pytest.raises(p.ParseError): p.volleyball_links(vb_main().replace('</h6>','</h6><img src="/file/i/extra.jpg">'),2026)

def test_volleyball_pending_is_not_zero():
    result=p.volleyball_matches(vb_games(),VB,2026)
    assert len(result)==1 and result[0]['score'] is None and result[0]['date']=='2026-09-22'

def test_volleyball_sets():
    item=p.volleyball_matches(vb_games('3:2','25:20, 22:25, 25:23, 20:25, 15:12'),VB,2026)[0]
    assert item['score']==[3,2] and len(item['sets'])==5

@pytest.mark.parametrize('score,sets',[('0:0',''),('3:0','25:20'),('2:1','25:20, 22:25, 25:23')])
def test_volleyball_ambiguous_final_rejected(score,sets):
    with pytest.raises(p.ParseError): p.volleyball_matches(vb_games(score,sets),VB,2026)

@pytest.mark.parametrize('url',['http://ksv-volleyball-oberhavel.de/mixed_2.html','https://example.org/x','https://ksv-volleyball-oberhavel.de@evil.example/','https://ksv-volleyball-oberhavel.de:444/x'])
def test_allowlist_blocks_arbitrary_urls(url):
    with pytest.raises(p.ParseError): p.safe_source_url(url,BASE)

def test_loss_of_matches_rejected():
    with pytest.raises(p.ParseError): check_loss({'season':'2026/27','matches':[{}]*10},{'season':'2026/27','matches':[{}]*3})

def test_failure_retains_last_good_data():
    class F:
        def html(self,url): raise FetchError('timeout')
    old={'schemaVersion':1,'sport':'handball','lastSuccessAt':'2026-09-20T12:00:00+00:00','teams':[{'id':'hb-123','matches':[{'score':[10,20]}]}]}
    result=collect('handball',F(),copy.deepcopy(old),now=datetime(2026,9,23,tzinfo=timezone.utc))
    assert result['stale'] and result['teams'][0]['matches']==old['teams'][0]['matches']
    assert result['lastSuccessAt']==old['lastSuccessAt']

def test_robots_disallow_prevents_target_request():
    f=Fetcher(); urls=[]
    def request(url,limit):
        urls.append(url)
        return b'User-agent: *\nDisallow: /\n',{'content-type':'text/plain'}
    f._request=request
    try:
        with pytest.raises(FetchError): f.get(p.VB_MAIN)
        assert len(urls)==1 and urls[0].endswith('robots.txt')
    finally: f.close()

def test_robots_html_not_treated_as_permission():
    f=Fetcher(); f._request=lambda url,limit:(b'<html>Server error</html>',{'content-type':'text/html'})
    try:
        with pytest.raises(FetchError): f.get(p.VB_MAIN)
    finally: f.close()

def test_legacy_meta_encoding():
    f=Fetcher(); f.get=lambda url:(b'<meta charset="iso-8859-1"><h1>Sch\xf6nwalder</h1>',{'content-type':'text/html'})
    try: assert 'Schönwalder' in f.html(p.HB_CLUB)
    finally: f.close()

def test_collect_handball_uses_complete_schedule():
    schedule=BASE+'&displayDetail=meetings&displayTyp=vorrunde'
    class F:
        def html(self,url):
            return {p.HB_CLUB:hb_teams_html(),BASE:hb_standings()+f'<a href="{schedule}">Spielplan (Gesamt)</a>',schedule:hb_games()}[url]
    result=collect('handball',F(),now=datetime(2026,9,23,tzinfo=timezone.utc))
    assert not result['stale'] and len(result['teams'][0]['matches'])==3

def test_production_template_contains_no_demo_data():
    html=(Path(__file__).parents[1]/'appack'/'Ergebnisse_Tabellen.tpl').read_text('utf-8')
    assert 'window.__SSV_RESULTS_PREVIEW__ =' not in html
    assert 'fetch(u.href' in html and 'credentials:\'omit\'' in html
    assert 'fussball.de/verein/' in html and 'allorigins' not in html and 'corsproxy' not in html
