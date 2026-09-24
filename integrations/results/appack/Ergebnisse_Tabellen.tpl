<!DOCTYPE html>
<html lang="de">
<head>
<title>${userTitle}</title>
<meta charset="utf-8" />
<meta name="viewport" content="width=device-width, initial-scale=1, viewport-fit=cover" />
<link href="https://cdn.appack.de/modules/fonts/roboto.min.css" rel="stylesheet" />
<link href="https://cdn.appack.de/${appId}/workspace/styles.css" rel="stylesheet" />
<link href="https://cdn.appack.de/${appId}/workspace/app-color.css" rel="stylesheet" />
<style>
html,body{margin:0;padding:0;background:#f2f4f6}
#ssv-results{--blue:#285EA7;--blue-dark:#214f8d;--gold:#E0AA3F;--bg:#f2f4f6;--paper:#fff;--soft:#f6f8fb;--line:#dce4ee;--ink:#172333;--muted:#536174;--blue-soft:#edf3fb;--warning:#765318;box-sizing:border-box;width:100%;max-width:940px;margin:0 auto;padding:14px 14px calc(24px + env(safe-area-inset-bottom,0px));color:var(--ink);font:16px/1.5 Roboto,Arial,sans-serif}
#ssv-results *,#ssv-results *::before,#ssv-results *::after{box-sizing:border-box}
#ssv-results [hidden]{display:none!important}
#ssv-results button,#ssv-results select{font:inherit}
#ssv-results button{cursor:pointer}
#ssv-results a{color:var(--blue)}
#ssv-results button:focus-visible,#ssv-results a:focus-visible,#ssv-results select:focus-visible,#ssv-results summary:focus-visible{outline:3px solid var(--gold);outline-offset:3px}
#ssv-results .ssv-shell{background:var(--paper);border-radius:16px;padding:24px;box-shadow:0 4px 16px rgba(0,0,0,.08)}
#ssv-results .ssv-header{text-align:center}
#ssv-results .ssv-logo{display:block;width:68px;height:68px;object-fit:contain;margin:0 auto 10px}
#ssv-results .ssv-logo-text{display:inline-grid;place-items:center;width:68px;height:68px;margin-bottom:10px;border-radius:50%;color:var(--blue);border:2px solid var(--gold);font-size:18px;font-weight:800}
#ssv-results .ssv-title{margin:0 0 6px;font-size:27px;font-weight:800;line-height:1.2;color:var(--ink)}
#ssv-results .ssv-subtitle{margin:0 auto;color:var(--muted);font-size:15px}
#ssv-results .ssv-header::after{content:'';display:block;width:70%;max-width:420px;height:3px;background:var(--gold);margin:18px auto 20px;border-radius:3px}
#ssv-results .ssv-sports{display:grid;grid-template-columns:repeat(3,minmax(0,1fr));gap:10px}
#ssv-results .ssv-sport{appearance:none;display:flex;flex-direction:column;align-items:center;justify-content:center;gap:3px;min-width:0;min-height:110px;padding:14px 6px 10px;border:0;border-bottom:4px solid transparent;border-radius:13px;background:var(--blue);color:white;text-decoration:none;text-align:center;box-shadow:0 2px 6px rgba(0,0,0,.12);transition:background .15s ease}
#ssv-results .ssv-sport[aria-pressed="true"]{border-bottom-color:var(--gold);background:var(--blue-dark)}
#ssv-results .ssv-sport img{display:block;width:38px;height:38px;object-fit:contain;margin-bottom:3px}
#ssv-results .ssv-sport strong{font-size:16px;color:white}
#ssv-results .ssv-sport small{display:block;font-size:11px;line-height:1.4;color:white}
#ssv-results .ssv-sport-fallback{height:38px;line-height:38px;font-size:26px;margin-bottom:3px}
#ssv-results .ssv-controls{margin-top:24px}
#ssv-results .ssv-section-heading{display:flex;justify-content:space-between;align-items:center;gap:12px;margin-bottom:12px}
#ssv-results .ssv-section-heading h2{margin:0;color:var(--ink);font-size:21px;line-height:1.25}
#ssv-results .ssv-refresh{display:inline-flex;align-items:center;justify-content:center;gap:6px;min-height:44px;padding:8px 10px;background:var(--soft);color:var(--blue);border:1px solid var(--line);border-radius:9px;font-size:13px}
#ssv-results .ssv-refresh:disabled{cursor:wait;opacity:.6}
#ssv-results .ssv-label{display:block;color:var(--muted);font-size:12px;margin-bottom:5px}
#ssv-results .ssv-select{display:block;width:100%;min-height:48px;padding:10px 36px 10px 12px;border:1px solid var(--line);border-radius:10px;background:var(--paper);color:var(--ink);font-size:16px}
#ssv-results .ssv-league{margin:10px 0 16px;color:var(--muted);font-size:13px;overflow-wrap:anywhere}
#ssv-results .ssv-tabs{display:grid;grid-template-columns:1fr 1fr;padding:4px;background:var(--soft);border:1px solid var(--line);border-radius:11px;gap:4px}
#ssv-results .ssv-tab{min-height:43px;padding:8px;border:0;border-radius:8px;color:var(--muted);background:transparent;font-weight:600;font-size:14px}
#ssv-results .ssv-tab[aria-selected="true"]{color:var(--blue);background:white;box-shadow:0 1px 5px rgba(0,0,0,.09)}
#ssv-results .ssv-status{margin:14px 0;padding:12px 14px;background:#fff7e6;color:var(--warning);font-size:13px;line-height:1.5;border:1px solid #eed6a6;border-radius:10px}
#ssv-results .ssv-preview{margin:0 0 16px;padding:10px 12px;background:#fff7e6;color:var(--warning);border-radius:8px;font-size:12px;text-align:center}
#ssv-results .ssv-filters{display:flex;flex-wrap:wrap;gap:8px;padding:16px 0 12px}
#ssv-results .ssv-filter{border:1px solid var(--line);border-radius:20px;min-height:40px;padding:8px 12px;background:white;color:var(--muted);font-size:13px}
#ssv-results .ssv-filter[aria-pressed="true"]{border-color:var(--blue);background:var(--blue-soft);color:var(--blue);font-weight:600}
#ssv-results .ssv-game-list{display:grid;gap:12px}
#ssv-results .ssv-match{border:1px solid var(--line);border-radius:12px;background:white;overflow:hidden}
#ssv-results .ssv-match>summary{list-style:none;display:block;padding:14px 16px 10px;cursor:pointer}
#ssv-results .ssv-match>summary::-webkit-details-marker{display:none}
#ssv-results .ssv-match-meta{display:flex;flex-wrap:wrap;align-items:center;justify-content:space-between;gap:8px;margin-bottom:10px;color:var(--muted);font-size:12px}
#ssv-results .ssv-location{display:inline-block;border-radius:5px;padding:2px 7px;background:var(--soft);font-size:11px;color:var(--muted)}
#ssv-results .ssv-location.home{background:var(--blue-soft);color:var(--blue)}
#ssv-results .ssv-scoreboard{display:grid;grid-template-columns:minmax(0,1fr) 42px;gap:7px 10px;align-items:center}
#ssv-results .ssv-team-name{font-size:15px;line-height:1.4;overflow-wrap:anywhere}
#ssv-results .ssv-team-name.own{color:var(--blue);font-weight:700}
#ssv-results .ssv-score{font-size:22px;line-height:1.25;text-align:right;font-weight:800;color:var(--ink);font-variant-numeric:tabular-nums}
#ssv-results .ssv-score.pending{color:#7a8694;font-size:18px;font-weight:400}
#ssv-results .ssv-match-bottom{display:flex;align-items:baseline;justify-content:space-between;gap:10px;margin-top:12px;color:var(--muted);font-size:11px}
#ssv-results .ssv-match-bottom .ssv-result-label{min-width:0;overflow-wrap:anywhere}
#ssv-results .ssv-detail-hint{white-space:nowrap}
#ssv-results .ssv-match[open] .ssv-detail-hint{color:var(--blue)}
#ssv-results .ssv-match-info{padding:12px 16px 14px;background:var(--soft);border-top:1px solid var(--line);color:var(--muted);font-size:13px}
#ssv-results .ssv-match-info p{margin:0 0 8px}
#ssv-results .ssv-match-info a{display:inline-flex;min-height:35px;align-items:center}
#ssv-results .ssv-empty{padding:26px 16px;margin:14px 0;background:var(--soft);border:1px solid var(--line);border-radius:11px;text-align:center;color:var(--muted);font-size:14px}
#ssv-results .ssv-empty strong{display:block;color:var(--ink);margin-bottom:5px;font-size:15px}
#ssv-results .ssv-table-intro{display:flex;align-items:baseline;flex-wrap:wrap;gap:6px 10px;justify-content:space-between;margin:18px 0 10px}
#ssv-results .ssv-table-intro h3{color:var(--ink);font-size:16px;margin:0}
#ssv-results .ssv-table-intro span{color:var(--muted);font-size:11px}
#ssv-results .ssv-table-scroll{overflow-x:auto;border:1px solid var(--line);border-radius:11px}
#ssv-results table{width:100%;border-collapse:collapse;border-spacing:0;margin:0;color:var(--ink);table-layout:fixed}
#ssv-results th{text-align:left;background:var(--soft);font-size:11px;font-weight:600;color:var(--muted);padding:10px 8px}
#ssv-results td{padding:12px 8px;border-top:1px solid var(--line);font-size:13px;vertical-align:middle;overflow-wrap:anywhere}
#ssv-results th.ssv-num,#ssv-results td.ssv-num{text-align:right;font-variant-numeric:tabular-nums;white-space:nowrap}
#ssv-results .ssv-rank-col{width:34px}
#ssv-results .ssv-played-col{width:38px}
#ssv-results .ssv-points-col{width:57px}
#ssv-results tr.own td{background:var(--blue-soft);color:var(--blue);font-weight:700}
#ssv-results tr.own td:first-child{box-shadow:inset 3px 0 0 var(--gold)}
#ssv-results .ssv-withdrawn{display:block;font-size:10px;line-height:1.45;font-weight:400;margin-top:4px;color:var(--muted)}
#ssv-results .ssv-table-more{margin-top:12px}
#ssv-results .ssv-table-more>summary{padding:10px 0;min-height:44px;color:var(--blue);cursor:pointer;font-size:13px}
#ssv-results .ssv-wide-table{min-width:640px;table-layout:auto}
#ssv-results .ssv-table-note{font-size:12px;color:var(--muted);margin:12px 0 0}
#ssv-results .ssv-image-open{display:block;width:100%;padding:6px;background:white;border:1px solid var(--line);border-radius:11px}
#ssv-results .ssv-table-image{display:block;width:100%;height:auto;border-radius:6px}
#ssv-results .ssv-image-cta{display:block;padding:11px 5px 5px;color:var(--blue);font-size:13px;font-weight:600}
#ssv-results .ssv-participant{margin:0;padding:12px;border-bottom:1px solid var(--line);font-size:14px}
#ssv-results .ssv-participant.own{color:var(--blue);background:var(--blue-soft);font-weight:700}
#ssv-results .ssv-footer{display:flex;flex-wrap:wrap;align-items:center;justify-content:space-between;gap:4px 12px;margin-top:22px;padding-top:13px;border-top:1px solid var(--line);color:var(--muted);font-size:11px}
#ssv-results .ssv-footer a{display:inline-flex;align-items:center;min-height:40px}
#ssv-results .ssv-loading{padding:22px 0}
#ssv-results .ssv-skeleton{height:105px;border-radius:11px;margin:12px 0;background:var(--soft)}
#ssv-results .ssv-modal{position:fixed;z-index:99999;inset:0;padding:18px;background:rgba(15,25,40,.65);display:flex;align-items:center;justify-content:center}
#ssv-results .ssv-modal-card{min-width:0;display:flex;flex-direction:column;width:100%;max-width:1000px;max-height:90vh;max-height:90dvh;background:white;border-radius:14px;padding:16px;box-shadow:0 8px 30px rgba(0,0,0,.25)}
#ssv-results .ssv-modal-head{display:flex;gap:12px;align-items:center;justify-content:space-between}
#ssv-results .ssv-modal-head h2{margin:0;font-size:18px;line-height:1.3}
#ssv-results .ssv-modal-scroll{min-width:0;overflow:auto;margin-top:16px;padding:2px;-webkit-overflow-scrolling:touch}
#ssv-results .ssv-zoom-image{display:block;width:auto;min-width:760px;max-width:none;height:auto}
#ssv-results .ssv-sr-only{position:absolute;width:1px;height:1px;padding:0;margin:-1px;overflow:hidden;clip:rect(0,0,0,0);white-space:nowrap;border:0}
@media(hover:hover){#ssv-results .ssv-sport:hover{background:var(--blue-dark)}#ssv-results .ssv-match>summary:hover{background:#fbfcfe}}
@media(max-width:600px){#ssv-results{padding:10px 10px calc(20px + env(safe-area-inset-bottom,0px))}#ssv-results .ssv-shell{padding:20px 12px 16px}#ssv-results .ssv-title{font-size:24px}#ssv-results .ssv-subtitle{font-size:14px}#ssv-results .ssv-sports{gap:8px}#ssv-results .ssv-sport{min-height:104px}#ssv-results .ssv-sport strong{font-size:14px}#ssv-results .ssv-sport small{font-size:10px}#ssv-results .ssv-sport img{width:34px;height:34px}#ssv-results .ssv-match>summary{padding:13px 12px 9px}#ssv-results .ssv-team-name{font-size:14px}#ssv-results .ssv-filters{gap:6px}#ssv-results .ssv-filter{font-size:12px;padding:8px 10px}#ssv-results .ssv-modal{padding:10px}}
@media(prefers-reduced-motion:reduce){#ssv-results *{transition:none!important;scroll-behavior:auto!important}}
</style>
</head>
<body>
<main id="ssv-results">
<div class="ssv-shell">
<div id="ssv-preview-note" class="ssv-preview" hidden>Vorschau mit ausgewählten Beispieldaten · keine Liveansicht</div>
<header class="ssv-header">
<img class="ssv-logo" src="https://cdn.appack.de/ssv53/images/Logo-00.png.png" alt="Schönwalder SV 1953" width="68" height="68" data-fallback="ssv-logo-fallback" />
<span class="ssv-logo-text" id="ssv-logo-fallback" hidden>SSV53</span>
<h1 class="ssv-title">Ergebnisse &amp; Tabellen</h1><p class="ssv-subtitle">Unsere Mannschaften. Alle Spiele im Blick.</p>
</header>
<nav class="ssv-sports" aria-label="Abteilung auswählen">
<a class="ssv-sport" href="https://www.fussball.de/verein/schoenwalder-sv-53-brandenburg/-/id/00ES8GNBL0000049VV0AG08LVUPGND5I#!/" target="_blank" rel="noopener noreferrer" aria-label="Fußball auf FUSSBALL.DE öffnen (extern)">
<img src="https://cdn.appack.de/ssv53/images/Icon_Fussball.png" width="38" height="38" alt="" data-fallback="ssv-football-fallback" /><span class="ssv-sport-fallback" id="ssv-football-fallback" aria-hidden="true" hidden>⚽</span><strong>Fußball</strong><small>FUSSBALL.DE ↗</small></a>
<button class="ssv-sport" id="ssv-sport-handball" type="button" data-sport="handball" aria-pressed="true"><img src="https://cdn.appack.de/ssv53/images/Icon_Handball.png" width="38" height="38" alt="" data-fallback="ssv-handball-fallback" /><span class="ssv-sport-fallback" id="ssv-handball-fallback" aria-hidden="true" hidden>H</span><strong>Handball</strong><small>Spiele &amp; Tabelle</small></button>
<button class="ssv-sport" id="ssv-sport-volleyball" type="button" data-sport="volleyball" aria-pressed="false"><img src="https://cdn.appack.de/ssv53/images/Icon_Volleyball.png" width="38" height="38" alt="" data-fallback="ssv-volleyball-fallback" /><span class="ssv-sport-fallback" id="ssv-volleyball-fallback" aria-hidden="true" hidden>V</span><strong>Volleyball</strong><small>Spiele &amp; Tabelle</small></button>
</nav>
<section class="ssv-controls" aria-labelledby="ssv-section-title">
<div class="ssv-section-heading"><h2 id="ssv-section-title">Handball</h2><button class="ssv-refresh" id="ssv-refresh" type="button" title="Gespeicherten Datenstand neu laden">↻ <span>Neu laden</span></button></div>
<p class="ssv-status" id="ssv-status" role="status" aria-live="polite" hidden></p>
<div id="ssv-loading" class="ssv-loading" role="status" hidden><span>Daten werden geladen …</span><div class="ssv-skeleton" aria-hidden="true"></div></div>
<div id="ssv-data" hidden>
<div id="ssv-team-choice"><label for="ssv-team-select" class="ssv-label">Mannschaft / Wettbewerb</label><select class="ssv-select" id="ssv-team-select"></select></div>
<p class="ssv-league" id="ssv-league"></p>
<div class="ssv-tabs" role="tablist" aria-label="Ansicht auswählen"><button class="ssv-tab" type="button" role="tab" id="ssv-tab-games" aria-controls="ssv-games" aria-selected="true" tabindex="0" data-view="games">Spiele</button><button class="ssv-tab" type="button" role="tab" id="ssv-tab-table" aria-controls="ssv-table" aria-selected="false" tabindex="-1" data-view="table">Tabelle</button></div>
<div id="ssv-games" role="tabpanel" aria-labelledby="ssv-tab-games"><div class="ssv-filters" aria-label="Spiele filtern"><button class="ssv-filter" type="button" data-filter="next" aria-pressed="true">Nächste Spiele</button><button class="ssv-filter" type="button" data-filter="results" aria-pressed="false">Ergebnisse</button><button class="ssv-filter" type="button" data-filter="all" aria-pressed="false">Alle</button></div><div class="ssv-game-list" id="ssv-game-list"></div></div>
<div id="ssv-table" role="tabpanel" aria-labelledby="ssv-tab-table" hidden></div>
</div>
<footer class="ssv-footer"><span id="ssv-fetched"></span><a id="ssv-source-link" href="https://hvbrandenburg-handball.liga.nu/cgi-bin/WebObjects/nuLigaHBDE.woa/wa/clubTeams?club=33547" target="_blank" rel="noopener noreferrer">Originalquelle ↗</a></footer>
</section>
<noscript><p class="ssv-empty">Für die Ergebnisansicht bitte JavaScript aktivieren. <a href="https://ksv-volleyball-oberhavel.de/mixed_2.html" target="_blank" rel="noopener noreferrer">Volleyball-Originalquelle</a></p></noscript>
</div>
<div class="ssv-modal" id="ssv-image-modal" role="dialog" aria-modal="true" aria-labelledby="ssv-modal-title" hidden><div class="ssv-modal-card"><div class="ssv-modal-head"><h2 id="ssv-modal-title">Volleyball · Originaltabelle</h2><button class="ssv-refresh" id="ssv-close-modal" type="button">Schließen ✕</button></div><p class="ssv-table-note">Zum Lesen seitlich verschieben. Die Rangliste wird unverändert von der KSV übernommen.</p><div class="ssv-modal-scroll" tabindex="0" aria-label="Vergrößerte Tabelle, horizontal scrollbar"><img class="ssv-zoom-image" id="ssv-zoom-image" alt="Offizielle Tabelle der 2. Kreisklasse Mixed" /></div></div></div>
</main>
<script>
/* API_URL wird beim Integrations-Build gesetzt. Niemals Schlüssel eintragen.
   Eine leere URL zeigt einen Einrichtungshinweis, keine Demoergebnisse. */
(function () {
'use strict';
const API_URL = "";
const root = document.getElementById('ssv-results');
const byId = function(id){return document.getElementById(id);};
const STORAGE_KEY = 'ssv53-results-v1';
const SOURCE = {
handball:{label:'nuLiga · HV Brandenburg',url:'https://hvbrandenburg-handball.liga.nu/cgi-bin/WebObjects/nuLigaHBDE.woa/wa/clubTeams?club=33547'},
volleyball:{label:'KSV Oberhavel',url:'https://ksv-volleyball-oberhavel.de/mixed_2.html'}
};
const preview = window.__SSV_RESULTS_PREVIEW__ || null;
const state = {sport:'handball',team:{},view:'games',filter:'next',payloads:{},offline:{},request:0,controller:null,loading:false,modalFocus:null};
let saved;
try{saved=JSON.parse(localStorage.getItem(STORAGE_KEY)||'{}');}catch(_){saved={};}
if (!saved || typeof saved !== 'object') saved = {};
if(saved.sport==='handball'||saved.sport==='volleyball')state.sport=saved.sport;
if(saved.team&&typeof saved.team==='object')state.team=saved.team;
const nowDate=function(){return preview?new Date(preview.now):new Date();};
const dateTime=new Intl.DateTimeFormat('de-DE',{timeZone:'Europe/Berlin',day:'2-digit',month:'2-digit',year:'numeric',hour:'2-digit',minute:'2-digit'});
const dateOnly=new Intl.DateTimeFormat('de-DE',{timeZone:'Europe/Berlin',weekday:'short',day:'2-digit',month:'2-digit',year:'2-digit'});
const isoDay=new Intl.DateTimeFormat('en-CA',{timeZone:'Europe/Berlin',year:'numeric',month:'2-digit',day:'2-digit'});
const normal=function(s){return String(s||'').toLowerCase().normalize('NFD').replace(/[\u0300-\u036f]/g,'').replace(/[^a-z0-9]/g,'');};
const ownName=function(s){return /^sch(o|oe)nwaldersv(19)?53(i{1,3}|[1-3])?$/.test(normal(s));};
function element(tag,className,content){const e=document.createElement(tag);if(className)e.className=className;if(content!==undefined&&content!==null)e.textContent=String(content);return e;}
function sourceUrl(value){try{const u=new URL(value);return u.protocol==='https:'&&['hvbrandenburg-handball.liga.nu','ksv-volleyball-oberhavel.de'].indexOf(u.hostname)!==-1&&!u.username&&!u.password&&(!u.port||u.port==='443')?u.href:null;}catch(_){return null;}}
function externalLink(label,url){const safe=sourceUrl(url);if(!safe)return element('span','',label);const a=element('a','',label+' ↗');a.href=safe;a.target='_blank';a.rel='noopener noreferrer';return a;}
function remember(){try{localStorage.setItem(STORAGE_KEY,JSON.stringify({sport:state.sport,team:state.team}));}catch(_){}}
function validPayload(data,sport){
if(!data||data.schemaVersion!==1||data.sport!==sport||!Array.isArray(data.teams)||data.teams.length>30)return false;
return data.teams.every(function(t){return t&&typeof t.id==='string'&&typeof t.label==='string'&&typeof t.league==='string'&&Array.isArray(t.matches)&&t.matches.length<=1000&&t.standings&&['table','participants','image','unavailable'].indexOf(t.standings.kind)>=0&&Array.isArray(t.standings.rows)&&t.standings.rows.length<=40&&t.standings.rows.every(function(r){return r&&typeof r.name==='string';})&&t.matches.every(function(m){return m&&typeof m.id==='string'&&typeof m.home==='string'&&typeof m.away==='string'&&['result','scheduled','cancelled','postponed','status'].indexOf(m.state)>=0&&(m.score===null||(Array.isArray(m.score)&&m.score.length===2&&m.score.every(function(n){return Number.isInteger(n)&&n>=0&&n<1000;})));});});
}
function loadLocal(sport){try{const d=JSON.parse(localStorage.getItem(STORAGE_KEY+'-'+sport)||'null');return validPayload(d,sport)?d:null;}catch(_){return null;}}
function stamp(value){const d=new Date(value);return value&&!Number.isNaN(d.getTime())?dateTime.format(d):'noch nicht verfügbar';}
function showStatus(message){byId('ssv-status').textContent=message||'';byId('ssv-status').hidden=!message;}
function empty(parent,title,message){const box=element('div','ssv-empty');box.append(element('strong','',title),element('span','',message));parent.append(box);}
function selectedTeam(){const data=state.payloads[state.sport];if(!data||!data.teams.length)return null;return data.teams.find(function(t){return t.id===state.team[state.sport];})||data.teams.find(function(t){return t.teamStatus!=='withdrawn';})||data.teams[0];}
function isPast(m){if(m.startsAt&&!Number.isNaN(new Date(m.startsAt).getTime()))return new Date(m.startsAt).getTime()<nowDate().getTime()-4*3600000;return m.date?m.date<isoDay.format(nowDate()):false;}
function hasResult(m){return m.state==='result'||m.state==='status';}
function matchStatus(m){if(m.state==='cancelled')return m.note||'Abgesagt';if(m.state==='postponed')return m.note||'Verlegt / Termin offen';if(m.state==='status')return m.note||'Besondere Wertung laut Quelle';if(m.kind==='festival')return 'Staffeltermin · Teilnahme bitte klären';if(hasResult(m))return state.sport==='volleyball'?'Endergebnis nach Sätzen':'Endergebnis';return isPast(m)?'Ergebnis noch nicht veröffentlicht':'Angesetzt';}
function gameCard(m){
const details=element('details','ssv-match'),summary=element('summary'),meta=element('div','ssv-match-meta');
const d=m.date?new Date(m.date+'T12:00:00+02:00'):null;
const when=d&&!Number.isNaN(d.getTime())?dateOnly.format(d)+(m.time?' · '+m.time+' Uhr':' · Uhrzeit offen'):'Termin offen';
meta.append(element('span','',when),element('span','ssv-location'+(m.isHome===true?' home':''),m.kind==='festival'?'Staffelturnier':(m.isHome===true?'Heim':m.isHome===false?'Auswärts':'Spielort offen')));
const board=element('div','ssv-scoreboard'),scored=m.state==='result'&&Array.isArray(m.score);
[m.home,m.away].forEach(function(name,i){board.append(element('span','ssv-team-name'+(ownName(name)?' own':''),name),element('span','ssv-score'+(!scored?' pending':''),scored?m.score[i]:'—'));});
const bottom=element('div','ssv-match-bottom');bottom.append(element('span','ssv-result-label',matchStatus(m)),element('span','ssv-detail-hint','Details ⌄'));summary.append(meta,board,bottom);
const info=element('div','ssv-match-info');if(m.kind==='festival')info.append(element('p','','Dies ist ein veröffentlichter Staffeltermin, keine bestätigte Einzelansetzung für den SSV.'));if(m.note)info.append(element('p','',m.note));if(m.number&&m.kind!=='festival')info.append(element('p','','Spielnummer: '+m.number));
if(m.venue&&m.venue.label){const p=element('p');p.append(externalLink(m.venue.label,m.venue.url));info.append(p);}
if(Array.isArray(m.sets)&&m.sets.length)info.append(element('p','','Satzstände: '+m.sets.filter(function(s){return Array.isArray(s)&&s.length===2;}).map(function(s){return s[0]+':'+s[1];}).join(' · ')));
info.append(externalLink('Spielplan in der Originalquelle',m.sourceUrl));details.append(summary,info);return details;
}
function renderGames(team){const list=byId('ssv-game-list');list.replaceChildren();root.querySelectorAll('[data-filter]').forEach(function(b){b.setAttribute('aria-pressed',String(b.dataset.filter===state.filter));});if(team.teamStatus==='withdrawn'){empty(list,'Mannschaft zurückgezogen','Hinweis der Quelle: '+(team.teamNote||'zurückgezogen'));return;}let matches=team.matches.slice();if(state.filter==='next')matches=matches.filter(function(m){return !hasResult(m)&&(!isPast(m)||m.state==='postponed');});if(state.filter==='results')matches=matches.filter(function(m){return hasResult(m)||isPast(m);});matches.sort(function(a,b){const av=(a.date||'9999')+(a.time||'99:99'),bv=(b.date||'9999')+(b.time||'99:99');return(av<bv?-1:av>bv?1:0)*(state.filter==='results'?-1:1);});if(!matches.length){empty(list,state.filter==='results'?'Noch keine Ergebnisse':'Keine Spiele in dieser Ansicht',team.stale?'Die Quelle konnte nicht vollständig geladen werden. Bitte die Originalquelle prüfen.':'Wechsle die Auswahl oder schau später wieder vorbei.');return;}matches.forEach(function(m){list.append(gameCard(m));});}
function statCell(tag,cls,value){return element(tag,cls,value===null||value===undefined?'—':value);}
function standingsTable(rows,full){
const wrap=element('div','ssv-table-scroll');if(full){wrap.tabIndex=0;wrap.setAttribute('role','region');wrap.setAttribute('aria-label','Weitere Tabellenwerte, horizontal scrollbar');}const table=element('table',full?'ssv-wide-table':'');table.append(element('caption','ssv-sr-only',full?'Ausführliche Handballtabelle':'Handballtabelle in offizieller Reihenfolge'));
if(!full){const cols=element('colgroup');['ssv-rank-col','','ssv-played-col','ssv-points-col'].forEach(function(c){cols.append(element('col',c));});table.append(cols);}
const thead=element('thead'),header=element('tr');const headings=full?['Pl.','Mannschaft','Sp.','S','U','N','Tore','Diff.','Pkt.']:['Pl.','Mannschaft','Sp.','Pkt.'];const descriptions=full?['Platz','Mannschaft','Spiele','Siege','Unentschieden','Niederlagen','Tore','Tordifferenz','Punkte']:['Platz','Mannschaft','Spiele','Punkte'];headings.forEach(function(h,i){const th=element('th',i===1?'':'ssv-num',h);th.scope='col';th.title=descriptions[i];th.setAttribute('aria-label',descriptions[i]);header.append(th);});thead.append(header);table.append(thead);const tbody=element('tbody');
rows.forEach(function(r){const tr=element('tr',r.isSSV?'own':'');tr.append(statCell('td','ssv-num',r.rank));const name=element('td','',r.name);if(r.status==='withdrawn')name.append(element('small','ssv-withdrawn',r.note||'zurückgezogen'));tr.append(name,statCell('td','ssv-num',r.played));if(full){['won','drawn','lost'].forEach(function(k){tr.append(statCell('td','ssv-num',r[k]));});tr.append(statCell('td','ssv-num',Array.isArray(r.goals)?r.goals.join(':'):null),statCell('td','ssv-num',r.difference>0?'+'+r.difference:r.difference));}tr.append(statCell('td','ssv-num',r.points));tbody.append(tr);});table.append(tbody);wrap.append(table);return wrap;
}
function imageSource(standing){if(typeof standing.imageData==='string'&&standing.imageData.length<1500000&&/^data:image\/(jpeg|png|webp);base64,[A-Za-z0-9+/=]+$/.test(standing.imageData))return standing.imageData;return preview?sourceUrl(standing.imageUrl):null;}
function openImage(src,trigger){state.modalFocus=trigger;byId('ssv-zoom-image').src=src;byId('ssv-image-modal').hidden=false;document.body.dataset.ssvOldOverflow=document.body.style.overflow;document.body.style.overflow='hidden';root.querySelector('.ssv-shell').setAttribute('inert','');byId('ssv-close-modal').focus();}
function closeImage(){byId('ssv-image-modal').hidden=true;document.body.style.overflow=document.body.dataset.ssvOldOverflow||'';root.querySelector('.ssv-shell').removeAttribute('inert');if(state.modalFocus&&state.modalFocus.isConnected)state.modalFocus.focus();}
function renderTable(team){
const target=byId('ssv-table');target.replaceChildren();const s=team.standings;const intro=element('div','ssv-table-intro');intro.append(element('h3','',s.kind==='participants'?'Teilnehmende Mannschaften':'Tabelle'),element('span','',team.season||''));target.append(intro);
if(s.kind==='table'){target.append(standingsTable(s.rows,false));const more=element('details','ssv-table-more');more.append(element('summary','','Weitere Tabellenwerte'),standingsTable(s.rows,true));target.append(more);target.append(element('p','ssv-table-note','Offizielle Reihenfolge der Quelle. Die SSV-Zeile ist hervorgehoben.'));}
else if(s.kind==='participants'){const list=element('div','ssv-table-scroll');s.rows.forEach(function(r){list.append(element('p','ssv-participant'+(r.isSSV?' own':''),r.name));});target.append(list,element('p','ssv-table-note',s.message));}
else if(s.kind==='image'){const src=imageSource(s);if(!src){empty(target,'Tabellengrafik nicht verfügbar','Bitte die Originalquelle öffnen.');return;}const button=element('button','ssv-image-open');button.type='button';button.setAttribute('aria-label','Originaltabelle in vergrößerter Ansicht öffnen');const image=element('img','ssv-table-image');image.src=src;image.alt='Offizielle Tabelle der 2. Kreisklasse Mixed';image.loading='lazy';const cta=element('span','ssv-image-cta','Antippen zum Vergrößern ↗');image.addEventListener('error',function(){image.hidden=true;cta.textContent='Tabellengrafik konnte nicht geladen werden. Bitte Originalquelle öffnen.';button.disabled=true;});button.append(image,cta);button.addEventListener('click',function(){openImage(src,button);});target.append(button,element('p','ssv-table-note',s.message));if(s.sourcePublishedAt)target.append(element('p','ssv-table-note','Veröffentlichter Tabellenstand der KSV: '+s.sourcePublishedAt+'. Ein neuer Abruf bedeutet nicht automatisch einen neuen Tabellenstand.'));}
else empty(target,'Keine Tabelle verfügbar',s.message||'Die Quelle veröffentlicht derzeit keine auslesbare Tabelle.');
}
function setView(view,focus){state.view=view;root.querySelectorAll('[data-view]').forEach(function(b){const active=b.dataset.view===view;b.setAttribute('aria-selected',String(active));b.tabIndex=active?0:-1;if(active&&focus)b.focus();});byId('ssv-games').hidden=view!=='games';byId('ssv-table').hidden=view!=='table';}
function render(){
root.querySelectorAll('[data-sport]').forEach(function(b){b.setAttribute('aria-pressed',String(b.dataset.sport===state.sport));});byId('ssv-section-title').textContent=state.sport==='handball'?'Handball':'Volleyball';const payload=state.payloads[state.sport],team=selectedTeam(),source=SOURCE[state.sport];byId('ssv-source-link').href=team&&sourceUrl(team.sourceUrl)||source.url;byId('ssv-source-link').textContent=source.label+' ↗';byId('ssv-data').hidden=!team;byId('ssv-refresh').disabled=state.loading;byId('ssv-loading').hidden=!state.loading||!!team;if(!team){byId('ssv-fetched').textContent='';return;}state.team[state.sport]=team.id;remember();const select=byId('ssv-team-select');select.replaceChildren();payload.teams.forEach(function(t){const o=element('option','',t.label+(t.teamStatus==='withdrawn'?' · zurückgezogen':''));o.value=t.id;select.append(o);});select.value=team.id;byId('ssv-team-choice').hidden=payload.teams.length<2;byId('ssv-league').textContent=team.league+(team.season?' · '+team.season:'');byId('ssv-fetched').textContent=(preview?'Vorschau · Quellenbeispiel vom ':'Letzter erfolgreicher Abruf: ')+stamp(team.fetchedAt);const age=nowDate().getTime()-new Date(team.fetchedAt).getTime();let warning='';if(state.offline[state.sport])warning='Verbindung unterbrochen. Du siehst den zuletzt auf diesem Gerät gespeicherten Stand.';else if(team.stale||!team.fetchedAt)warning=team.warning||'Quelle vorübergehend nicht erreichbar. Letzter verfügbarer Stand.';else if(!preview&&(!Number.isFinite(age)||age>2*3600000))warning='Die Aktualisierung ist verzögert. Bitte beachte den angezeigten Abrufzeitpunkt.';if(team.teamStatus==='withdrawn')warning=(warning?warning+' ':'')+'Diese Mannschaft ist laut Quelle '+(team.teamNote||'zurückgezogen')+'.';showStatus(warning);renderGames(team);renderTable(team);setView(state.view,false);
}
async function load(sport){
const request=++state.request;if(state.controller)state.controller.abort();state.controller=null;state.loading=true;showStatus('');if(!state.payloads[sport]&&!preview){const local=loadLocal(sport);if(local){state.payloads[sport]=local;state.offline[sport]=true;}}render();let timeout;
try{let data;if(preview){data=preview[sport];}else{if(!API_URL)throw new Error('Die Datenanbindung ist noch nicht eingerichtet. Der Ergebnis-Endpunkt muss einmal in dieser Vorlage eingetragen werden.');const u=new URL(API_URL);if(u.protocol!=='https:'&&u.hostname!=='localhost'&&u.hostname!=='127.0.0.1')throw new Error('Der Ergebnis-Endpunkt benötigt HTTPS.');u.searchParams.set('sport',sport);const controller=new AbortController();state.controller=controller;timeout=setTimeout(function(){controller.abort();},12000);const response=await fetch(u.href,{method:'GET',mode:'cors',credentials:'omit',signal:controller.signal});if(!response.ok)throw new Error(response.status===503?'Der Ergebnisdienst ist noch nicht bereit oder vorübergehend nicht erreichbar.':'Der Ergebnisdienst konnte nicht geladen werden.');const raw=await response.text();if(raw.length>2500000)throw new Error('Unerwartet große Datenantwort.');data=JSON.parse(raw);}
if(request!==state.request)return;if(!validPayload(data,sport))throw new Error('Die Datenantwort hat nicht das erwartete Format.');if(!data.teams.length&&data.stale&&state.payloads[sport]&&state.payloads[sport].teams.length)throw new Error('Die Quelle liefert derzeit keine verwertbaren Daten.');state.payloads[sport]=data;state.offline[sport]=false;if(!preview&&data.teams.length){try{localStorage.setItem(STORAGE_KEY+'-'+sport,JSON.stringify(data));}catch(_){}}state.loading=false;render();if(!data.teams.length)showStatus('Noch keine Daten verfügbar. Die Originalquelle ist unten verlinkt.');
}catch(error){if(request!==state.request)return;state.loading=false;if(state.payloads[sport]){state.offline[sport]=true;render();}else{render();showStatus(error.name==='AbortError'?'Das Laden dauert zu lange. Bitte erneut versuchen oder die Originalquelle öffnen.':error.message);}}
finally{clearTimeout(timeout);if(request===state.request){state.loading=false;byId('ssv-refresh').disabled=false;byId('ssv-loading').hidden=true;}}
}
root.querySelectorAll('[data-sport]').forEach(function(b){b.addEventListener('click',function(){if(state.sport===b.dataset.sport)return;state.sport=b.dataset.sport;state.view='games';remember();load(state.sport);});});
root.querySelectorAll('[data-filter]').forEach(function(b){b.addEventListener('click',function(){state.filter=b.dataset.filter;const team=selectedTeam();if(team)renderGames(team);});});
root.querySelectorAll('[data-view]').forEach(function(b){b.addEventListener('click',function(){setView(b.dataset.view,false);});b.addEventListener('keydown',function(event){if(['ArrowLeft','ArrowRight','Home','End'].indexOf(event.key)!==-1){event.preventDefault();setView(event.key==='Home'?'games':event.key==='End'?'table':(state.view==='games'?'table':'games'),true);}});});
byId('ssv-team-select').addEventListener('change',function(e){state.team[state.sport]=e.target.value;remember();render();});byId('ssv-refresh').addEventListener('click',function(){load(state.sport);});byId('ssv-close-modal').addEventListener('click',closeImage);byId('ssv-image-modal').addEventListener('click',function(e){if(e.target===byId('ssv-image-modal'))closeImage();});
byId('ssv-image-modal').addEventListener('keydown',function(e){if(e.key==='Escape'){e.preventDefault();closeImage();}if(e.key==='Tab'){const focusables=[byId('ssv-close-modal'),root.querySelector('.ssv-modal-scroll')];if(e.shiftKey&&document.activeElement===focusables[0]){e.preventDefault();focusables[1].focus();}else if(!e.shiftKey&&document.activeElement===focusables[1]){e.preventDefault();focusables[0].focus();}}});
root.querySelectorAll('img[data-fallback]').forEach(function(img){const failed=function(){img.hidden=true;const fallback=byId(img.dataset.fallback);if(fallback)fallback.hidden=false;};img.addEventListener('error',failed);if(img.complete&&img.naturalWidth===0)failed();});
byId('ssv-preview-note').hidden=!preview;load(state.sport);
}());
</script>
</body>
</html>
