    // Presentation only. Existing authenticated handlers and backend permits remain authoritative.
    var pfIconNodes = __PF_ICON_DATA__;
    var pfView = "home", pfHistory = [], pfPages = {}, pfReady = false;
    var pfBrowserHistory = false, pfRestoringHistory = false, pfHistoryIndex = 0, pfDialogCloseTimer = null;
    function pfPlanStep() {
      return !document.getElementById("plan-custom").classList.contains("hidden")?"custom":!document.getElementById("plan-pause-step").classList.contains("hidden")?"pause":"home";
    }
    function pfHistoryRoute(value) {
      var r=value&&value.ssv53Platzpflege;
      return r&&r.version===1&&pfPages[r.page]&&Number.isInteger(r.index)&&r.index>=0&&["home","pause","custom"].indexOf(r.plan)>=0?r:null;
    }
    function pfRoute(dialogId) {
      // Only navigation data belongs in browser history, never approvals or device commands.
      return {version:1,index:pfHistoryIndex,page:pfView,plan:pfPlanStep(),dialog:dialogId||null,scrollY:window.scrollY};
    }
    function pfWriteHistory(route,replace) {
      if(!pfBrowserHistory||pfRestoringHistory)return;
      try {
        var entry=Object.assign({},window.history.state||{});entry.ssv53Platzpflege=route;
        // Keep the URL, including Appack identity parameters and any existing fragment.
        window.history[replace?"replaceState":"pushState"](entry,"");pfHistoryIndex=route.index;
      } catch(error) {pfBrowserHistory=false}
    }
    function pfRememberScroll() {
      var r=pfHistoryRoute(window.history.state);if(r){r.scrollY=window.scrollY;pfWriteHistory(r,true)}
    }
    function pfPushRoute(page,plan,dialogId) {
      pfRememberScroll();var r=pfRoute(dialogId);r.index=pfHistoryIndex+1;r.page=page;r.plan=plan;r.scrollY=0;pfWriteHistory(r,false);
    }
    function pfCancelDialog(dialog) {
      if(dialog.id==="confirm-dialog"){
        state.pendingAction=null;state.pendingPayload=null;state.pendingRequestId=null;
        state.manualControlDialog=null;state.manualControlOperation=null;state.manualControlSource=null;
      }
      dialog.close();
    }
    function pfRestoreRoute(route) {
      pfRestoringHistory=true;
      try {
        clearTimeout(pfDialogCloseTimer);pfDialogCloseTimer=null;
        document.querySelectorAll("dialog[open]").forEach(pfCancelDialog);
        pfHistoryIndex=route.index;pfGo(route.page,true);showPlanView(route.plan);
        // Forward/reload may revisit a dialog marker, but must never replay a confirmation.
        var clean=Object.assign({},route,{dialog:null});
        var entry=Object.assign({},window.history.state||{},{ssv53Platzpflege:clean});
        window.history.replaceState(entry,"");
        requestAnimationFrame(function(){window.scrollTo(0,Number.isFinite(route.scrollY)?route.scrollY:0)});
      } finally {pfRestoringHistory=false}
    }
    function pfBindDialogHistory(dialog) {
      var show=dialog.showModal.bind(dialog),close=dialog.close.bind(dialog);
      dialog.showModal=function(){
        var alreadyOpen=dialog.open;show();if(alreadyOpen||!pfBrowserHistory||pfRestoringHistory)return;
        if(pfDialogCloseTimer!==null){
          // "How to stop" -> confirmation replaces the same dialog step.
          clearTimeout(pfDialogCloseTimer);pfDialogCloseTimer=null;pfWriteHistory(pfRoute(dialog.id),true);
        }else pfPushRoute(pfView,pfPlanStep(),dialog.id);
      };
      dialog.close=function(){
        var wasOpen=dialog.open;close();if(!wasOpen||!pfBrowserHistory||pfRestoringHistory)return;
        clearTimeout(pfDialogCloseTimer);
        pfDialogCloseTimer=setTimeout(function(){
          pfDialogCloseTimer=null;var r=pfHistoryRoute(window.history.state);
          if(r&&r.dialog&&r.index>0&&!document.querySelector("dialog[open]"))window.history.back();
        },0);
      };
      dialog.addEventListener("cancel",function(event){event.preventDefault();pfCancelDialog(dialog)});
    }
    function pfInitHistory() {
      var saved=pfHistoryRoute(window.history.state);
      pfBrowserHistory=!!(window.history&&window.history.pushState&&window.history.replaceState);
      if(!pfBrowserHistory)return;
      if(saved)pfRestoreRoute(saved);else pfWriteHistory(pfRoute(),true);
      window.addEventListener("popstate",function(event){var route=pfHistoryRoute(event.state);if(route)pfRestoreRoute(route)});
      var planView=showPlanView;
      showPlanView=function(view){
        if(pfView==="water-plan"&&view!==pfPlanStep()&&!pfRestoringHistory)pfPushRoute(pfView,view);
        return planView(view);
      };
      document.querySelectorAll("dialog").forEach(pfBindDialogHistory);
    }
    function pfIcon(name) {
      var wrap=document.createElement("span"),svg=document.createElementNS("http://www.w3.org/2000/svg","svg");
      wrap.className="pf-symbol";wrap.dataset.pfIcon=name;wrap.setAttribute("aria-hidden","true");
      svg.setAttribute("viewBox","0 0 24 24");svg.setAttribute("fill","none");svg.setAttribute("stroke","currentColor");svg.setAttribute("stroke-width","2");svg.setAttribute("stroke-linecap","round");svg.setAttribute("stroke-linejoin","round");
      (pfIconNodes[name]||pfIconNodes.Info).forEach(function(part){var node=document.createElementNS(svg.namespaceURI,part[0]);Object.keys(part[1]).forEach(function(key){node.setAttribute(key,part[1][key])});svg.appendChild(node)});
      wrap.appendChild(svg);return wrap;
    }
    function pfButton(label,icon,handler,description) {
      var button=document.createElement("button"),copy=document.createElement("span"),title=document.createElement("strong");
      button.type="button";button.className="pf-tile";copy.className="pf-label";button.appendChild(pfIcon(icon));title.textContent=label;copy.appendChild(title);
      if(description){var note=document.createElement("small");note.textContent=description;copy.appendChild(note);button.classList.add("pf-category")}
      button.appendChild(copy);button.onclick=handler;return button;
    }
    function pfDecorate(element,icon,label) {
      if(!element)return;if(label!==undefined)element.textContent=label;
      var current=element.querySelector(":scope > .pf-symbol");if(current&&current.dataset.pfIcon!==icon){current.replaceWith(pfIcon(icon));current=element.querySelector(":scope > .pf-symbol")}
      if(!current){var old=element.querySelector(":scope > svg");if(old)old.remove();element.prepend(pfIcon(icon))}
      if(element.tagName==="BUTTON")Array.from(element.childNodes).filter(function(n){return n.nodeType===3&&n.textContent.trim()}).forEach(function(n){var copy=document.createElement("span");copy.className="pf-label";n.replaceWith(copy);copy.appendChild(n)});
    }
    function pfMove(id,parent){var el=document.getElementById(id);if(el)parent.appendChild(el);return el}
    function pfCreatePage(key,title,icon) {
      var page=document.createElement("section"),heading=document.createElement("h2"),label=document.createElement("span");
      page.id="pf-page-"+key;page.className="pf-page";page.hidden=key!=="home";
      if(title){heading.className="pf-page-title";heading.appendChild(pfIcon(icon));label.textContent=title;heading.appendChild(label);page.appendChild(heading)}
      document.getElementById("pf-pages").appendChild(page);pfPages[key]=page;return page;
    }
    function pfGo(key,back) {
      if(!pfPages[key])return;if(!back&&key!==pfView){pfHistory.push(pfView);pfPushRoute(key,key==="water-plan"?pfPlanStep():"home")}pfView=key;
      Object.keys(pfPages).forEach(function(name){pfPages[name].hidden=name!==key});
      document.getElementById("dashboard").dataset.pfPage=key;
      document.getElementById("pf-back").hidden=key==="home";
      document.getElementById("pf-back").textContent="Zurück";pfDecorate(document.getElementById("pf-back"),"ArrowLeft");
      var actions=document.getElementById("pf-mower-actions");(key==="controls"?pfPages.controls:pfPages.home).appendChild(actions);
      if(key==="home")pfPages.home.insertBefore(actions,document.getElementById("pf-home-links"));
      document.querySelectorAll("[data-pf-nav]").forEach(function(b){var active=b.dataset.pfNav===(key==="home"?"home":["today","training","grounds"].indexOf(key)>=0?"today":"more");b.classList.toggle("pf-current",active);if(active)b.setAttribute("aria-current","page");else b.removeAttribute("aria-current")});
      if(pfReady){var focus=pfPages[key].querySelector("h2")||document.getElementById(key==="home"?"overall":"pf-back");focus.setAttribute("tabindex","-1");focus.focus({preventScroll:true});document.getElementById("dashboard").scrollIntoView({block:"start",behavior:"auto"})}
    }
    function pfNavigationButton(parent,key,label,icon,description) {var b=pfButton(label,icon,function(){pfGo(key)},description);b.dataset.pfTarget=key;parent.appendChild(b);return b}
    function pfCalendarLink(parent,label,icon) {
      // Technical module name read from the authenticated CMS bookmark on 2026-09-11.
      // Appack's documented nav:// scheme keeps navigation and user identity in the app.
      var link=document.createElement("a"),text=document.createElement("strong");link.className="pf-tile";link.href="nav://ssv53_TextImage_1761902353516";link.dataset.pfCalendar="true";link.appendChild(pfIcon(icon));text.textContent=label;link.appendChild(text);parent.appendChild(link);return link;
    }
    function pfInformationPage(id,key,icon) {
      var old=document.getElementById(id),panel=document.createElement("section"),page=pfCreatePage(key),heading=old.querySelector("h2");
      panel.id=id;panel.className="pf-inline-panel";while(old.firstChild)panel.appendChild(old.firstChild);old.replaceWith(panel);
      heading.className="pf-page-title";pfDecorate(heading,icon);page.appendChild(heading);panel.querySelector(".stats-head").remove();page.appendChild(panel);
      // Keep the original action handlers; opening information now navigates.
      // close() before a confirmation keeps its originating page underneath.
      panel.showModal=function(){pfGo(key)};panel.close=function(){};
      Object.defineProperty(panel,"open",{get:function(){return pfView===key&&!page.hidden}});
      return panel;
    }
    function pfBack() {
      if(pfBrowserHistory&&pfHistoryIndex>0){window.history.back();return}
      if(pfView==="water-plan"&&document.getElementById("plan-home").classList.contains("hidden")){showPlanView("home");return}
      pfGo(pfHistory.pop()||"home",true);
    }
    function pfMountDesign() {
      var dashboard=document.getElementById("dashboard");document.body.classList.add("pflege-design");
      var header=document.querySelector(".shell > .head");if(header)header.remove();
      var meta=document.createElement("div");meta.className="pf-meta";pfMove("updated",meta);pfMove("refresh",meta);dashboard.prepend(meta);
      var back=pfButton("Zurück","ArrowLeft",pfBack);back.id="pf-back";back.className="pf-back";back.hidden=true;dashboard.insertBefore(back,document.getElementById("overall"));
      var safety=document.createElement("div");safety.id="pf-safety-actions";safety.className="pf-safety-actions";pfMove("irrigation-stop",safety);dashboard.insertBefore(safety,document.getElementById("overall").nextSibling);
      var pages=document.createElement("div");pages.id="pf-pages";dashboard.appendChild(pages);
      pfCreatePage("home");pfCreatePage("more","Sonstiges","LayoutGrid");pfCreatePage("mower","Mähroboter","Bot");pfCreatePage("controls","Mäher bedienen","Bot");pfCreatePage("height","Schnitthöhe","MoveVertical");pfCreatePage("blades","Klingen","Scissors");pfCreatePage("husqvarna","Am Mäher oder über Husqvarna","Smartphone");pfCreatePage("water","Bewässerung","Droplets");pfCreatePage("zones","Einzelne Zonen","Grid2x2");pfCreatePage("grounds","Platz & Training","CalendarDays");pfCreatePage("today","Platzbelegung","CalendarDays");pfCreatePage("training","Trainingsplan","Snowflake");pfCreatePage("clubhouse","Vereinsheim","Building2");pfCreatePage("history","Letzte Mäheraktionen","History");
      pfMove("coordination-card",pfPages.home);var charge=document.createElement("div");charge.id="pf-charge";charge.className="pf-charge";charge.hidden=true;
      var chargeCaption=document.createElement("strong");chargeCaption.id="pf-charge-caption";charge.appendChild(chargeCaption);
      var chargeProgress=document.createElement("progress");chargeProgress.id="pf-charge-progress";chargeProgress.max=100;chargeProgress.setAttribute("aria-label","Akkustand");charge.appendChild(chargeProgress);
      pfMove("charge-end-row",charge);document.getElementById("overall").appendChild(charge);
      var mowing=document.createElement("div");mowing.id="pf-mowing-progress";mowing.className="pf-charge";mowing.hidden=true;
      var mowingCaption=document.createElement("strong");mowingCaption.id="pf-mowing-progress-caption";mowing.appendChild(mowingCaption);
      var mowingBar=document.createElement("progress");mowingBar.id="pf-mowing-progress-bar";mowingBar.max=100;mowingBar.setAttribute("aria-label","Fläche gemäht");mowing.appendChild(mowingBar);
      document.getElementById("overall").appendChild(mowing);
      var mowerCard=document.getElementById("mower-title").closest("article"),waterCard=document.getElementById("water-title").closest("article"),occupancyCard=document.getElementById("occupancy-title").closest("article"),clubhouseCard=document.getElementById("clubhouse-events").closest("article");
      pfPages.controls.appendChild(mowerCard);pfPages.water.appendChild(waterCard);pfPages.today.appendChild(occupancyCard);pfPages.clubhouse.appendChild(clubhouseCard);pfMove("training-control-card",pfPages.training);
      var actions=document.createElement("div");actions.id="pf-mower-actions";actions.className="pf-home-actions";["manual-start","manual-park","manual-resume","mow-start","mow-park"].forEach(function(id){pfMove(id,actions)});pfPages.home.appendChild(actions);
      var links=document.createElement("div");links.id="pf-home-links";links.className="pf-home-links";pfNavigationButton(links,"water","Bewässerung","Droplets");pfNavigationButton(links,"today","Platzbelegung","CalendarDays");pfPages.home.appendChild(links);
      var height=pfMove("height-panel",pfPages.height);height.open=true;var current=document.createElement("p");current.id="pf-height-current";current.className="pf-height-current";height.prepend(current);var newHeight=document.createElement("p");newHeight.className="pf-height-label";newHeight.textContent="Neue Höhe";height.insertBefore(newHeight,height.querySelector(".height-control"));var delta=document.createElement("p");delta.id="pf-height-delta";delta.className="pf-height-delta";delta.setAttribute("aria-live","polite");height.querySelector(".height-control").after(delta);pfMove("height-status",pfPages.height);
      var bladeValue=document.createElement("div");bladeValue.className="pf-blade-value";bladeValue.innerHTML='<span>Seit dem letzten Wechsel</span><strong id="pf-blade-hours">–</strong>';pfPages.blades.appendChild(bladeValue);pfMove("blade-reset",pfPages.blades);
      var husqvarna=document.getElementById("manual-husqvarna").closest("details");husqvarna.open=true;pfPages.husqvarna.appendChild(husqvarna);
      var waterGrid=document.createElement("div");waterGrid.className="pf-grid";pfPages.water.appendChild(waterGrid);pfMove("water-plan-open",waterGrid);pfNavigationButton(waterGrid,"zones","Einzelne Zonen","Grid2x2");pfMove("irrigation-start-all",waterGrid);pfMove("water-stats-open",waterGrid);
      [waterCard,pfPages.zones].forEach(function(parent,index){var note=document.createElement("p");note.id=index?"pf-zone-start-note":"pf-water-start-note";note.className="muted";note.hidden=true;note.setAttribute("role","status");parent.appendChild(note)});
      var zones=document.getElementById("zones").closest("details");zones.open=true;pfPages.zones.appendChild(zones);pfMove("drying-end-row",waterCard);
      var menu=document.createElement("div");menu.className="pf-categories";pfPages.more.appendChild(menu);pfNavigationButton(menu,"mower","Mähroboter","Bot","Schnitthöhe, Klingen und Bedienung");pfNavigationButton(menu,"water","Bewässerung","Droplets","Zeitplan, Zonen und Laufzeiten");pfNavigationButton(menu,"grounds","Platz & Training","CalendarDays","Belegung und Trainingsplan");pfNavigationButton(menu,"clubhouse","Vereinsheim","Building2","Termine im Vereinsheim");
      var mowerGrid=document.createElement("div");mowerGrid.className="pf-grid";pfPages.mower.appendChild(mowerGrid);pfNavigationButton(mowerGrid,"height","Schnitthöhe","MoveVertical");pfNavigationButton(mowerGrid,"blades","Klingen","Scissors");pfNavigationButton(mowerGrid,"controls","Bedienung","Play");pfMove("stats-open",mowerGrid);pfNavigationButton(mowerGrid,"history","Letzte Aktionen","History");pfNavigationButton(mowerGrid,"husqvarna","Husqvarna","Smartphone");
      var groundsGrid=document.createElement("div");groundsGrid.className="pf-grid";pfPages.grounds.appendChild(groundsGrid);pfNavigationButton(groundsGrid,"today","Platzbelegung","CalendarDays");pfNavigationButton(groundsGrid,"training","Trainingsplan","Snowflake");
      pfCalendarLink(groundsGrid,"Training verschieben","CalendarClock");pfCalendarLink(groundsGrid,"Termine & Sperren","CalendarDays");pfCalendarLink(pfPages.today,"Vollständigen Kalender öffnen","CalendarDays");
      var history=document.createElement("div");history.id="pf-command-history";history.className="pf-command-history";pfPages.history.appendChild(history);
      var footer=document.createElement("nav");footer.className="pf-nav";footer.setAttribute("aria-label","Platzpflegebereiche");[["home","Übersicht","House"],["today","Heute","CalendarDays"],["more","Sonstiges","Ellipsis"]].forEach(function(item){var b=pfButton(item[1],item[2],function(){pfGo(item[0])});b.dataset.pfNav=item[0];footer.appendChild(b)});dashboard.appendChild(footer);
      statsDialog=pfInformationPage("stats-dialog","mower-stats","ChartNoAxesColumn");
      planDialog=pfInformationPage("water-plan-dialog","water-plan","CalendarClock");
      pfInformationPage("water-stats-dialog","water-stats","ChartNoAxesColumn");
      pfInformationPage("water-attention-dialog","water-attention","TriangleAlert");
      pfMove("action-error",safety);pfStyleStatic();pfGo("home",true);pfReady=true;pfInitHistory();
    }
    function pfStyleStatic() {
      var map={"stats-open":["ChartNoAxesColumn","Statistiken"],"water-plan-open":["CalendarClock","Zeitplan"],"water-stats-open":["ChartNoAxesColumn","Statistiken"],"irrigation-start-all":["Play","Alle Zonen starten"],"irrigation-stop":["Square","Bewässerung beenden"],"manual-husqvarna":["Play","Über Husqvarna starten"],"manual-husqvarna-park":["House","Über Husqvarna parken"],"blade-reset":["Scissors","Klingen gewechselt"],"height-minus":["Minus","1 mm niedriger"],"height-plus":["Plus","1 mm höher"],"plan-pause":["Pause","Bewässerung pausieren"],"plan-save-custom":["Save","Speichern"],"stop-now":["Square","Direkt beenden"],"stop-after-zone":["Droplets","Nach dieser Zone"],"confirm-cancel":["X","Abbrechen"],"stop-cancel":["X","Abbrechen"],"plan-skip":["CalendarX2"],"plan-pause-open":["Pause"],"plan-custom-open":["SlidersHorizontal"],"plan-resume":["Repeat2"],"plan-history-toggle":["History"],"activate-button":["KeyRound"]};
      Object.keys(map).forEach(function(id){pfDecorate(document.getElementById(id),map[id][0],map[id][1])});
      ["stats-open","water-plan-open","water-stats-open","irrigation-start-all"].forEach(function(id){var b=document.getElementById(id);b.classList.remove("icon-btn");b.classList.add("pf-tile")});
      var statistics={"stat-area-equivalents":"Grid2x2","stat-mowing-7d":"Timer","stat-average":"Clock","stat-mowing-today":"Timer","stat-progress":"Grid2x2","stat-last-complete":"CalendarCheck","stat-blade":"Scissors","stat-return-average":"Route","water-stat-minutes":"Timer","water-stat-complete":"CircleCheck","water-stat-last":"Droplets","water-stat-duration":"Timer","water-stat-zones":"Droplets","water-stat-changes":"CalendarClock"};
      Object.keys(statistics).forEach(function(id){pfDecorate(document.getElementById(id).closest(".stat"),statistics[id])});
      document.querySelectorAll("dialog h2").forEach(function(h){pfDecorate(h,h.closest("#stats-dialog")?"ChartNoAxesColumn":h.closest("#water-stats-dialog,#water-plan-dialog,#water-attention-dialog,#stop-dialog")?"Droplets":"CircleCheck")});
      document.querySelectorAll(".plan-back").forEach(function(b){pfDecorate(b,"ArrowLeft","Zurück")});
      document.querySelectorAll(".plan-datetime-group label").forEach(function(label){pfDecorate(label,label.querySelector('input[type="date"]')?"CalendarDays":"Clock")});
      document.querySelectorAll(".profile-row button").forEach(function(b){pfDecorate(b,"Droplets")});
      document.querySelectorAll(".plan-preset-pause").forEach(function(b){pfDecorate(b,"CalendarDays")});
      document.querySelectorAll('input[name="manual-water"]').forEach(function(input){input.addEventListener("change",pfManualChoiceVisibility)});
      var waterNote=pfPages.water.querySelector(".training-note");if(waterNote)waterNote.textContent="Die Zeitgrenzen 03:30–08:00 Uhr gelten für die Automatik. Manuell ist Bewässerung auch zu anderen Zeiten möglich.";
      document.querySelectorAll(".pf-inline-panel .plan-back").forEach(function(b){b.hidden=true});
      pfDecorate(document.querySelector("#plan-pause-step h3"),"Pause");pfDecorate(document.querySelector("#plan-custom h3"),"SlidersHorizontal");
      var toggle=document.getElementById("plan-history-toggle"),toggleBase=toggle.onclick;toggle.onclick=function(){toggleBase.apply(this,arguments);pfDecorate(this,"History")};
    }
    function pfVisibility(s) {
      return manualMowerActions(s,state);
    }
    function pfNextMoment(s) {
      var m=s.mower||{},manual=manualControlView(s),info=nextStartInfo(s,true),now=new Date(s.generatedAt),o=s.occupancy||{};
      if(mowerActionContext(s).stopped)return {label:"Nächster Mähstart",at:null,text:"",hidden:true,note:""};
      if(manual.enabled&&["MANUAL_PARKED","PARKING"].indexOf(manual.status)>=0)return {label:"Nächster Mähstart",at:null,text:"Du entscheidest",note:"Erst nach deiner Freigabe."};
      if(mowerTelemetryFresh(s)&&["MOWING","LEAVING"].indexOf(m.activity)>=0&&o.available!==false){
        var blocks=(o.upcoming||[]).concat(o.next?[o.next]:[]).filter(function(x){return x&&new Date(x.start)>now}).sort(function(a,b){return new Date(a.start)-new Date(b.start)});
        if(blocks.length){var first=blocks[0],sources=String(first.source||"").toLowerCase().split("+").map(function(x){return x.trim()}),water=sources.indexOf("irrigation")>=0,other=sources.some(function(x){return x&&x!=="irrigation"});return {label:"Spätestens in der Station",at:new Date(first.start),text:"",note:water?(other?"Danach sind Platzbelegung und Bewässerung geplant.":"Danach wird bewässert."):"Vor der nächsten Platzbelegung."}}
      }
      return {label:info.earliestOnly?"Frühester Mähstart":"Nächster Mähstart",at:info.at,text:info.text,hidden:info.manualStop===true,note:info.at?(info.earliestOnly?"Nach der Platzsperre. Akku muss bereit sein.":info.awaitingMowerReport?"Geplant · Neue Mähermeldung erforderlich.":"Voraussichtlich"):""};
    }
    function pfRenderMoment(s) {
      if(!pfReady)return;var moment=pfNextMoment(s),el=document.getElementById("mower-next-start"),now=new Date(s.generatedAt);
      document.querySelector("#coordination-card .time-label").textContent=moment.label;
      el.classList.toggle("pf-unknown-time",!moment.at);el.textContent=moment.at?new Date(moment.at).toLocaleTimeString("de-DE",{timeZone:EVENT_TIME_ZONE,hour:"2-digit",minute:"2-digit",hourCycle:"h23"})+" Uhr":moment.text;
      document.getElementById("next-start-note").textContent=moment.at?(localDay(moment.at)!==localDay(now)?calendarTime(moment.at,now)+" · ":"")+moment.note:moment.note;
      document.getElementById("coordination-card").hidden=moment.hidden===true||pfChargingInfo(s).visible&&!moment.at&&moment.text==="Noch offen";
    }
    function pfChargingInfo(s) {
      var m=s.mower||{},value=m.batteryPercent,percent=typeof value==="number"&&Number.isFinite(value)&&value>=0&&value<=100?Math.round(value):null;
      return {visible:m.activity==="CHARGING"&&m.connected===true&&mowerTelemetryFresh(s)&&s.controlsAvailable!==false&&!hasActiveMowerError(m),percent:percent,at:chargingEnd(s,true)};
    }
    function pfMowingInfo(s) {
      var m=s&&s.mower||{},value=m.workAreaProgress,percent=typeof value==="number"&&Number.isFinite(value)&&value>=0&&value<=100?Math.round(value):null;
      return {visible:m.activity==="MOWING"&&m.connected===true&&mowerTelemetryFresh(s)&&!hasActiveMowerError(m),percent:percent};
    }
    function pfRenderCharging(s) {
      if(!pfReady)return;var info=pfChargingInfo(s),wrap=document.getElementById("pf-charge"),title=document.getElementById("overall-title").textContent;
      wrap.hidden=!info.visible;if(!info.visible)return;
      document.getElementById("pf-charge-caption").textContent=(title==="Mäher lädt"?"":"Mäher lädt · ")+"Akku "+(info.percent===null?"unbekannt":info.percent+" %");
      var bar=document.getElementById("pf-charge-progress");bar.hidden=info.percent===null;if(info.percent!==null)bar.value=info.percent;
      document.getElementById("charge-end-row").classList.remove("hidden");
      document.getElementById("charge-end-time").textContent=info.at?calendarTime(info.at,s.generatedAt):"Noch nicht bekannt";
      document.getElementById("charge-end-note").textContent=info.at?"Voraussichtlich":"";
    }
    function pfRenderMowingProgress(s) {
      if(!pfReady)return;var info=pfMowingInfo(s),wrap=document.getElementById("pf-mowing-progress");
      wrap.hidden=!info.visible;if(!info.visible)return;
      pfDecorate(document.getElementById("pf-mowing-progress-caption"),"Grid2x2","Fläche gemäht "+(info.percent===null?"unbekannt":info.percent+" %"));
      var bar=document.getElementById("pf-mowing-progress-bar");bar.hidden=info.percent===null;if(info.percent!==null)bar.value=info.percent;
    }
    function pfRenderHeight(s) {
      if(!pfReady)return;var m=s&&s.mower||{},value=Number(state.heightChoice),old=m.cuttingHeightMm,save=document.getElementById("height-save");
      document.getElementById("pf-height-current").textContent=old==null?"Bisherige Höhe noch nicht bekannt":"Bisher am Mäher: "+old+" mm";
      document.getElementById("pf-height-delta").textContent=old==null?"":value===Number(old)?"Gleiche Höhe wie bisher":Math.abs(value-Number(old))+" mm "+(value>old?"höher":"niedriger")+" als bisher";
      pfDecorate(save,"Save",value+" mm speichern");save.classList.toggle("hidden",save.disabled);
      ["height-minus","height-plus"].forEach(function(id){document.getElementById(id).classList.toggle("hidden",document.getElementById(id).disabled)});
    }
    function pfRenderHistory(s) {
      var list=document.getElementById("pf-command-history");list.innerHTML="";var names={SET_CUTTING_HEIGHT:"Schnitthöhe ändern",START_MOWING:"Mäher starten",START_MOWING_OCCUPANCY_OVERRIDE:"Mäher starten",PARK_MOWER:"Mäher parken",RESET_BLADE_USAGE:"Klingenwechsel"};
      Object.keys(s.operatorCommands||{}).filter(function(key){return names[key]}).forEach(function(key){var c=s.operatorCommands[key],entry=document.createElement("article"),title=document.createElement("strong"),status=document.createElement("p"),code=String(c.status||"");entry.className="card";title.textContent=names[key];entry.appendChild(pfIcon(key==="SET_CUTTING_HEIGHT"?"MoveVertical":key==="RESET_BLADE_USAGE"?"Scissors":key==="PARK_MOWER"?"House":"Play"));entry.appendChild(title);status.textContent=code==="CONFIRMED"?"Bestätigt":["REJECTED","EXPIRED","FAILED"].indexOf(code)>=0?"Nicht ausgeführt – bitte den aktuellen Stand prüfen.":"Noch nicht bestätigt";entry.appendChild(status);list.appendChild(entry)});
      if(!list.children.length){var empty=document.createElement("p");empty.className="muted";empty.textContent="Keine weiteren Mäheraktionen vorhanden.";list.appendChild(empty)}
    }
    function pfConfirmationIcons() {
      var action=state.pendingAction,operation=state.manualControlOperation,icon=action==="SET_CUTTING_HEIGHT"?"MoveVertical":action==="RESET_BLADE_USAGE"?"Scissors":action==="MANUAL_CONTROL"?(operation==="PARK"?"House":operation==="RESUME"?"Repeat2":"Play"):String(action).indexOf("IRRIGATION")>=0?"Droplets":"CircleCheck";
      pfDecorate(document.getElementById("confirm-title"),icon);pfDecorate(document.getElementById("confirm-go"),"CircleCheck");
      document.querySelectorAll('#manual-water-choice label').forEach(function(label){pfDecorate(label,label.querySelector('input').value==="MOWER"?"Bot":"Droplets")});
      pfManualChoiceVisibility();
    }
    function pfManualChoiceVisibility() {
      var context=state.manualControlDialog||{},choice=document.querySelector('input[name="manual-water"]:checked'),value=choice&&choice.value;
      if(state.pendingAction!=="MANUAL_CONTROL")return;
      if(value==="IRRIGATION")document.getElementById("manual-drying").checked=false;
      document.getElementById("manual-drying-label").classList.toggle("hidden",!context.dryingRequired||(context.waterRequired&&value!=="MOWER"));
    }
    function pfPlanIcons() {
      document.querySelectorAll("#plan-zones label").forEach(function(label){pfDecorate(label,"Droplets")});
      document.querySelectorAll("#plan-zones .duration-stepper").forEach(function(stepper){var buttons=stepper.querySelectorAll("button");buttons.forEach(function(b,index){b.setAttribute("aria-label",index?"Eine Minute länger":"Eine Minute kürzer");pfDecorate(b,index?"Plus":"Minus","")})});
    }
    function pfWaterActions(s) {
      var v=irrigationActionContext(s,state);
      var reason=pfWaterStartExplanation(s,state);["pf-water-start-note","pf-zone-start-note"].forEach(function(id){var note=document.getElementById(id);note.hidden=!reason;note.textContent=reason});
      [["irrigation-stop",v.showStop,"Square",v.stopLabel],["irrigation-start-all",v.showStart,"Play","Alle Zonen starten"],["stop-now",v.showStop,"Square","Direkt beenden"],["stop-after-zone",v.showStopAfterZone,"Droplets","Nach dieser Zone"]].forEach(function(row){var b=document.getElementById(row[0]);b.disabled=!row[1];b.classList.toggle("hidden",!row[1]);pfDecorate(b,row[2],row[3])});
      document.querySelectorAll(".zone-controls").forEach(function(el){el.classList.toggle("hidden",!v.showZoneStart)});
      document.querySelectorAll(".zone-start").forEach(function(b){b.disabled=!v.showZoneStart;b.classList.toggle("hidden",!v.showZoneStart);pfDecorate(b,"Play","Starten")});
    }
    function pfWaterStartExplanation(s,local) {
      var v=irrigationActionContext(s,local),m=s.mower||{},a=s.automation||{},safe=s.irrigation&&s.irrigation.safety||{};
      if(v.showStart)return "";
      if(v.showZoneStart)return "Zurzeit können nur einzelne Zonen gestartet werden.";
      if(m.state==="STOPPED")return "Manueller Start nicht möglich: Der Mäher ist gestoppt. Bitte vor Ort freigeben und sicher parken.";
      if(m.state==="OFF")return "Manueller Start nicht möglich: Bitte den Mäher einschalten und sicher parken.";
      if(safe.available===true&&safe.fresh===true&&Number(safe.active_zone_count)>0)return "";
      if(a.pendingAction||local&&local.inFlight&&Object.keys(local.inFlight).length)return "Bitte warten, bis die aktuelle Anfrage bestätigt ist.";
      if(m.connected!==true)return "Manueller Start nicht möglich: Die Verbindung zum Mäher fehlt. Bitte am Mäher nachsehen.";
      if(hasActiveMowerError(m)||["ERROR","FATAL_ERROR","ERROR_AT_POWER_UP"].indexOf(m.state)>=0)return "Manueller Start nicht möglich: Bitte die Störung am Mäher beheben.";
      if(a.irrigationPhase==="COMPLETE_HOLD")return "Manueller Start noch gesperrt: Der letzte Durchlauf ist noch nicht freigegeben. Bitte später aktualisieren.";
      if(a.irrigationPhase)return "Ein Bewässerungsablauf ist bereits vorbereitet oder noch aktiv.";
      if(!mowerTelemetryFresh(s)||safe.available!==true||safe.fresh!==true)return "Für einen manuellen Start fehlt eine aktuelle Rückmeldung. Bitte aktualisieren.";
      if(Number(safe.imminent_zone_count)>0)return "Die nächste Bewässerung steht bereits an.";
      if(!deviceActionAllowed(s,"START_IRRIGATION"))return "Der manuelle Start ist zurzeit nicht freigegeben.";
      return "Ein manueller Start ist noch nicht möglich. Bitte aktualisieren.";
    }
    function pfPlanActions(s) {
      var actions={"plan-skip":"SKIP_NEXT_IRRIGATION","plan-pause-open":"PAUSE_IRRIGATION_UNTIL","plan-custom-open":"CUSTOMIZE_NEXT_IRRIGATION","plan-resume":"RESUME_IRRIGATION_SCHEDULE","plan-pause":"PAUSE_IRRIGATION_UNTIL","plan-save-custom":"CUSTOMIZE_NEXT_IRRIGATION"},busy=Object.keys(actions).some(function(id){var key=actions[id];return operatorActionPending(s,key)||!!(state.inFlight&&state.inFlight[key])});
      var schedule=s.irrigationSchedule||{},a=s.automation||{},next=schedule.nextRun,override=schedule.override,zones=next&&next.zones||override&&override.zones||[],canEdit=deviceControlsOpen(s)&&schedule.available===true&&!a.pendingAction&&!a.irrigationPhase&&!coordinationExecutionBlocked(s)&&!irrigationScheduleChangePending(s);
      Object.keys(actions).forEach(function(id){var key=actions[id],b=document.getElementById(id),hasTarget=key==="RESUME_IRRIGATION_SCHEDULE"?!!override:!override,ready=hasTarget&&(key!=="SKIP_NEXT_IRRIGATION"||!!next)&&(key!=="CUSTOMIZE_NEXT_IRRIGATION"||!!next&&zones.length===7);b.disabled=!canEdit||!ready||busy||!deviceActionAllowed(s,key);b.classList.toggle("hidden",b.disabled)});
      var resume=document.getElementById("plan-resume"),kind=override&&override.kind;
      resume.querySelector("strong").textContent=kind==="SKIP_NEXT"?"Aussetzen zurücknehmen":kind==="CUSTOM_NEXT"?"Änderung zurücknehmen":"Bewässerung fortsetzen";
      resume.querySelector("small").textContent=kind==="SKIP_NEXT"?"Der nächste Lauf findet wieder statt.":"Ab jetzt gilt wieder der normale Bewässerungsplan.";
    }
    function pfUpdate(s) {
      if(!pfReady||!s)return;var v=pfVisibility(s),manual=manualControlView(s),overview=dashboardMessage(s),safe=s.irrigation&&s.irrigation.safety||{};
      [["manual-start",v.manualStart,manual.waterRequired?"Droplets":"Play",v.startLabel],["manual-park",v.manualPark,"House",v.parkLabel],["manual-resume",v.manualResume,"Repeat2","Automatik einschalten"],["manual-husqvarna",v.husqvarnaStart,manual.waterRequired?"Droplets":"Play",manual.waterRequired?v.startLabel:"Über Husqvarna starten"],["manual-husqvarna-park",v.husqvarnaPark,"House","Über Husqvarna parken"]].forEach(function(row){var b=document.getElementById(row[0]);b.classList.toggle("hidden",!row[1]);b.disabled=!row[1];pfDecorate(b,row[2],row[3])});
      ["mow-start","mow-park"].forEach(function(id){var b=document.getElementById(id);if(b.disabled)b.classList.add("hidden");pfDecorate(b,id==="mow-start"?"Play":"House")});
      pfDecorate(document.getElementById("irrigation-stop"),"Square",irrigationAwaitingStart(s)?"Bewässerung abbrechen":"Bewässerung beenden");
      document.getElementById("pf-mower-actions").classList.toggle("pf-single-action",document.querySelectorAll('#pf-mower-actions>.btn:not(.hidden)').length===1);
      document.getElementById("pf-mower-actions").hidden=document.querySelectorAll('#pf-mower-actions>.btn:not(.hidden)').length===0;
      document.querySelectorAll('[data-pf-target="height"]').forEach(function(b){b.hidden=!v.height});
      document.querySelectorAll('[data-pf-target="husqvarna"]').forEach(function(b){b.hidden=!v.husqvarnaStart&&!v.husqvarnaPark});
      document.querySelectorAll(".zone-start").forEach(function(b){pfDecorate(b,"Play","Starten");b.classList.toggle("hidden",b.disabled)});
      document.querySelectorAll(".zone-head").forEach(function(h){pfDecorate(h,"Droplets")});
      document.querySelectorAll("#plan-zones label").forEach(function(h){pfDecorate(h,"Droplets")});
      document.querySelectorAll("#water-stat-zones li").forEach(function(h){pfDecorate(h,"Droplets")});
      var all=document.getElementById("irrigation-start-all");all.classList.toggle("hidden",all.classList.contains("hidden")||all.disabled);pfDecorate(all,"Play");
      document.getElementById("dashboard").dataset.pfUrgent=String(overview.tone==="bad"||!mowerTelemetryFresh(s)||Number(safe.active_zone_count||0)>0||!!(s.automation&&s.automation.pendingAction));
      document.getElementById("pf-blade-hours").textContent=duration(s.statistics&&s.statistics.bladeUsageSeconds);
      // The icon belongs to the selected message, not to a simultaneous device state.
      // A transient error may have replaced the message after the last status response.
      pfDecorate(document.getElementById("overall"),document.getElementById("overall-title").textContent===overview.title?overview.icon:"TriangleAlert");
      [["mower-connection","Smartphone"],["battery","Battery"],["progress","Grid2x2"],["cutting-height-current","MoveVertical"],["mower-error","TriangleAlert"]].forEach(function(row){pfDecorate(document.getElementById(row[0]).previousElementSibling,row[1])});
      pfDecorate(document.getElementById("winter-training-switch"),"Snowflake");
      pfWaterActions(s);pfPlanActions(s);var blade=document.getElementById("blade-reset"),bladeReady=Number(s.statistics&&s.statistics.bladeUsageSeconds)>0&&deviceActionAllowed(s,"RESET_BLADE_USAGE")&&!operatorActionPending(s,"RESET_BLADE_USAGE")&&!(s.automation&&s.automation.pendingAction)&&!(state.inFlight&&state.inFlight.RESET_BLADE_USAGE);blade.disabled=!bladeReady;blade.classList.toggle("hidden",!bladeReady);pfRenderHeight(s);pfRenderMoment(s);pfRenderCharging(s);pfRenderMowingProgress(s);pfRenderHistory(s);
    }
    pfMountDesign();
    var pfRenderBase=render;render=function(s){pfRenderBase(s);pfUpdate(s)};
    var pfCoordinationBase=renderCoordination;renderCoordination=function(s){pfCoordinationBase(s);pfRenderMoment(s);pfRenderCharging(s);pfRenderMowingProgress(s);if(pfReady)document.getElementById("pf-blade-hours").textContent=duration(s.statistics&&s.statistics.bladeUsageSeconds)};
    var pfHeightBase=renderHeightChoice;renderHeightChoice=function(){pfHeightBase();pfRenderHeight(state.status)};
    var pfLoadBase=load;load=function(){return pfLoadBase().then(function(s){if(!s&&state.status)pfUpdate(state.status);return s})};
    var pfActionBase=openAction;openAction=function(){var result=pfActionBase.apply(this,arguments);pfConfirmationIcons();return result};
    var pfManualBase=manualControlPrepare;manualControlPrepare=function(){var result=pfManualBase.apply(this,arguments);pfConfirmationIcons();return result};
    var pfResetPlanBase=resetPlanDialog;resetPlanDialog=function(){pfResetPlanBase();pfDecorate(document.getElementById("plan-history-toggle"),"History")};
    var pfZonesBase=buildPlanZones;buildPlanZones=function(zones){pfZonesBase(zones);pfPlanIcons()};
    var pfPlanBase=renderIrrigationSchedule;renderIrrigationSchedule=function(){pfPlanBase.apply(this,arguments);if(state.status)pfPlanActions(state.status);pfDecorate(document.getElementById("plan-history-toggle"),"History")};
    var pfWaterStatsBase=renderIrrigationStatistics;renderIrrigationStatistics=function(stats){pfWaterStatsBase(stats);document.querySelectorAll("#water-stat-zones li").forEach(function(li){pfDecorate(li,"Droplets")})};
    var pfAttentionBase=renderIrrigationAttention;renderIrrigationAttention=function(stats){pfAttentionBase(stats);pfDecorate(document.getElementById("water-attention-title"),"TriangleAlert");pfDecorate(document.getElementById("water-attention-open"),"TriangleAlert")};
