    // Presentation only. Existing authenticated handlers and backend permits remain authoritative.
    var pfIconNodes = __PF_ICON_DATA__;
    var pfView = "home", pfHistory = [], pfPages = {}, pfReady = false;
    function pfIcon(name) {
      var wrap=document.createElement("span"),svg=document.createElementNS("http://www.w3.org/2000/svg","svg");
      wrap.className="pf-symbol";wrap.dataset.pfIcon=name;wrap.setAttribute("aria-hidden","true");
      svg.setAttribute("viewBox","0 0 24 24");svg.setAttribute("fill","none");svg.setAttribute("stroke","currentColor");svg.setAttribute("stroke-width","2");svg.setAttribute("stroke-linecap","round");svg.setAttribute("stroke-linejoin","round");
      (pfIconNodes[name]||pfIconNodes.Info).forEach(function(part){var node=document.createElementNS(svg.namespaceURI,part[0]);Object.keys(part[1]).forEach(function(key){node.setAttribute(key,part[1][key])});svg.appendChild(node)});
      wrap.appendChild(svg);return wrap;
    }
    function pfButton(label,icon,handler,description) {
      var button=document.createElement("button"),copy=document.createElement("span"),title=document.createElement("strong");
      button.type="button";button.className="pf-tile";button.appendChild(pfIcon(icon));title.textContent=label;copy.appendChild(title);
      if(description){var note=document.createElement("small");note.textContent=description;copy.appendChild(note);button.classList.add("pf-category")}
      button.appendChild(copy);button.onclick=handler;return button;
    }
    function pfDecorate(element,icon,label) {
      if(!element)return;if(label!==undefined)element.textContent=label;
      var current=element.querySelector(":scope > .pf-symbol");if(current&&current.dataset.pfIcon!==icon){current.replaceWith(pfIcon(icon));return}
      if(!current){var old=element.querySelector(":scope > svg");if(old)old.remove();element.prepend(pfIcon(icon))}
    }
    function pfMove(id,parent){var el=document.getElementById(id);if(el)parent.appendChild(el);return el}
    function pfCreatePage(key,title,icon) {
      var page=document.createElement("section"),heading=document.createElement("h2"),label=document.createElement("span");
      page.id="pf-page-"+key;page.className="pf-page";page.hidden=key!=="home";
      if(title){heading.className="pf-page-title";heading.appendChild(pfIcon(icon));label.textContent=title;heading.appendChild(label);page.appendChild(heading)}
      document.getElementById("pf-pages").appendChild(page);pfPages[key]=page;return page;
    }
    function pfGo(key,back) {
      if(!pfPages[key])return;if(!back&&key!==pfView)pfHistory.push(pfView);pfView=key;
      Object.keys(pfPages).forEach(function(name){pfPages[name].hidden=name!==key});
      document.getElementById("dashboard").dataset.pfPage=key;
      document.getElementById("pf-back").hidden=key==="home";
      document.getElementById("pf-back").textContent="Zurück";pfDecorate(document.getElementById("pf-back"),"ArrowLeft");
      var actions=document.getElementById("pf-mower-actions");(key==="controls"?pfPages.controls:pfPages.home).appendChild(actions);
      if(key==="home")pfPages.home.insertBefore(actions,document.getElementById("pf-home-links"));
      document.querySelectorAll("[data-pf-nav]").forEach(function(b){var active=b.dataset.pfNav===(key==="home"?"home":["today","training","grounds"].indexOf(key)>=0?"today":"more");b.classList.toggle("pf-current",active);if(active)b.setAttribute("aria-current","page");else b.removeAttribute("aria-current")});
      if(pfReady){var focus=pfPages[key].querySelector("h2")||document.getElementById("pf-back");focus.setAttribute("tabindex","-1");focus.focus({preventScroll:true});document.getElementById("dashboard").scrollIntoView({block:"start",behavior:"auto"})}
    }
    function pfNavigationButton(parent,key,label,icon,description) {var b=pfButton(label,icon,function(){pfGo(key)},description);b.dataset.pfTarget=key;parent.appendChild(b);return b}
    function pfCalendarLink(parent,label,icon) {
      // Technical module name read from the authenticated CMS bookmark on 2026-09-11.
      // Appack's documented nav:// scheme keeps navigation and user identity in the app.
      var link=document.createElement("a"),text=document.createElement("strong");link.className="pf-tile";link.href="nav://ssv53_TextImage_1761902353516";link.dataset.pfCalendar="true";link.appendChild(pfIcon(icon));text.textContent=label;link.appendChild(text);parent.appendChild(link);return link;
    }
    function pfMountDesign() {
      var dashboard=document.getElementById("dashboard");document.body.classList.add("pflege-design");
      var meta=document.createElement("div");meta.className="pf-meta";pfMove("updated",meta);pfMove("refresh",meta);dashboard.prepend(meta);
      var back=pfButton("Zurück","ArrowLeft",function(){pfGo(pfHistory.pop()||"home",true)});back.id="pf-back";back.className="pf-back";back.hidden=true;dashboard.insertBefore(back,document.getElementById("overall"));
      var safety=document.createElement("div");safety.id="pf-safety-actions";safety.className="pf-safety-actions";pfMove("irrigation-stop",safety);dashboard.insertBefore(safety,document.getElementById("overall").nextSibling);
      var pages=document.createElement("div");pages.id="pf-pages";dashboard.appendChild(pages);
      pfCreatePage("home");pfCreatePage("more","Sonstiges","LayoutGrid");pfCreatePage("mower","Mähroboter","Bot");pfCreatePage("controls","Mäher bedienen","Bot");pfCreatePage("height","Schnitthöhe","MoveVertical");pfCreatePage("blades","Klingen","Scissors");pfCreatePage("husqvarna","Am Mäher oder über Husqvarna","Smartphone");pfCreatePage("water","Bewässerung","Droplets");pfCreatePage("zones","Einzelne Zonen","Grid2x2");pfCreatePage("grounds","Platz & Training","CalendarDays");pfCreatePage("today","Platzbelegung","CalendarDays");pfCreatePage("training","Trainingsplan","Snowflake");pfCreatePage("clubhouse","Vereinsheim","Building2");pfCreatePage("history","Letzte Mäheraktionen","History");
      pfMove("coordination-card",pfPages.home);var charge=document.createElement("div");charge.className="pf-charge";pfMove("charge-end-row",charge);pfPages.home.appendChild(charge);
      var mowerCard=document.getElementById("mower-title").closest("article"),waterCard=document.getElementById("water-title").closest("article"),occupancyCard=document.getElementById("occupancy-title").closest("article"),clubhouseCard=document.getElementById("clubhouse-events").closest("article");
      pfPages.controls.appendChild(mowerCard);pfPages.water.appendChild(waterCard);pfPages.today.appendChild(occupancyCard);pfPages.clubhouse.appendChild(clubhouseCard);pfMove("training-control-card",pfPages.training);
      var actions=document.createElement("div");actions.id="pf-mower-actions";actions.className="pf-home-actions";["manual-start","manual-park","manual-resume","mow-start","mow-park"].forEach(function(id){pfMove(id,actions)});pfPages.home.appendChild(actions);
      var links=document.createElement("div");links.id="pf-home-links";links.className="pf-home-links";pfNavigationButton(links,"water","Bewässerung","Droplets");pfNavigationButton(links,"today","Platzbelegung","CalendarDays");pfPages.home.appendChild(links);
      var height=pfMove("height-panel",pfPages.height);height.open=true;var current=document.createElement("p");current.id="pf-height-current";current.className="pf-height-current";height.prepend(current);var newHeight=document.createElement("p");newHeight.className="pf-height-label";newHeight.textContent="Neue Höhe";height.insertBefore(newHeight,height.querySelector(".height-control"));var delta=document.createElement("p");delta.id="pf-height-delta";delta.className="pf-height-delta";delta.setAttribute("aria-live","polite");height.querySelector(".height-control").after(delta);pfMove("height-status",pfPages.height);
      var bladeValue=document.createElement("div");bladeValue.className="pf-blade-value";bladeValue.innerHTML='<span>Seit dem letzten Wechsel</span><strong id="pf-blade-hours">–</strong>';pfPages.blades.appendChild(bladeValue);pfMove("blade-reset",pfPages.blades);
      var husqvarna=document.getElementById("manual-husqvarna").closest("details");husqvarna.open=true;pfPages.husqvarna.appendChild(husqvarna);
      var waterGrid=document.createElement("div");waterGrid.className="pf-grid";pfPages.water.appendChild(waterGrid);pfMove("water-plan-open",waterGrid);pfNavigationButton(waterGrid,"zones","Einzelne Zonen","Grid2x2");pfMove("irrigation-start-all",waterGrid);pfMove("water-stats-open",waterGrid);
      var zones=document.getElementById("zones").closest("details");zones.open=true;pfPages.zones.appendChild(zones);pfMove("drying-end-row",waterCard);
      var menu=document.createElement("div");menu.className="pf-categories";pfPages.more.appendChild(menu);pfNavigationButton(menu,"mower","Mähroboter","Bot","Schnitthöhe, Klingen und Bedienung");pfNavigationButton(menu,"water","Bewässerung","Droplets","Zeitplan, Zonen und Laufzeiten");pfNavigationButton(menu,"grounds","Platz & Training","CalendarDays","Belegung und Trainingsplan");pfNavigationButton(menu,"clubhouse","Vereinsheim","Building2","Termine im Vereinsheim");
      var mowerGrid=document.createElement("div");mowerGrid.className="pf-grid";pfPages.mower.appendChild(mowerGrid);pfNavigationButton(mowerGrid,"height","Schnitthöhe","MoveVertical");pfNavigationButton(mowerGrid,"blades","Klingen","Scissors");pfNavigationButton(mowerGrid,"controls","Bedienung","Play");pfMove("stats-open",mowerGrid);pfNavigationButton(mowerGrid,"history","Letzte Aktionen","History");pfNavigationButton(mowerGrid,"husqvarna","Husqvarna","Smartphone");
      var groundsGrid=document.createElement("div");groundsGrid.className="pf-grid";pfPages.grounds.appendChild(groundsGrid);pfNavigationButton(groundsGrid,"today","Platzbelegung","CalendarDays");pfNavigationButton(groundsGrid,"training","Trainingsplan","Snowflake");
      pfCalendarLink(groundsGrid,"Training verschieben","CalendarClock");pfCalendarLink(groundsGrid,"Termine & Sperren","CalendarDays");pfCalendarLink(pfPages.today,"Vollständigen Kalender öffnen","CalendarDays");
      var history=document.createElement("div");history.id="pf-command-history";history.className="pf-command-history";pfPages.history.appendChild(history);
      var footer=document.createElement("nav");footer.className="pf-nav";footer.setAttribute("aria-label","Platzpflegebereiche");[["home","Übersicht","House"],["today","Heute","CalendarDays"],["more","Sonstiges","Ellipsis"]].forEach(function(item){var b=pfButton(item[1],item[2],function(){pfHistory=[];pfGo(item[0],true)});b.dataset.pfNav=item[0];footer.appendChild(b)});dashboard.appendChild(footer);
      pfMove("action-error",safety);pfStyleStatic();pfGo("home",true);pfReady=true;
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
    }
    function pfVisibility(s) {
      var m=s.mower||{},manual=manualControlView(s),fresh=mowerTelemetryFresh(s),moving=fresh&&["MOWING","LEAVING","GOING_HOME"].indexOf(m.activity)>=0,busy=operatorActionPending(s,"MANUAL_CONTROL")||!!(state.inFlight&&state.inFlight.MANUAL_CONTROL),parked=manual.status==="MANUAL_PARKED"||manual.status==="PARKING",resumeMeaningful=manual.enabled&&manual.status!=="AUTOMATIC"&&manual.status!=="UNKNOWN";
      return {manualStart:manual.enabled&&manual.canStart&&deviceControlsOpen(s)&&(!moving||manual.waterRequired)&&!busy,manualPark:manual.enabled&&manual.canPark&&!parked,manualResume:manual.enabled&&manual.canResume&&resumeMeaningful&&!busy,husqvarnaStart:manual.enabled&&manual.canStart&&deviceControlsOpen(s)&&(!moving||manual.waterRequired)&&!busy,husqvarnaPark:manual.enabled&&manual.canPark&&!parked,height:m.cuttingHeightSupported===true,parkLabel:fresh&&["CHARGING","PARKED_IN_CS"].indexOf(m.activity)>=0?"In Station lassen":"Mäher parken"};
    }
    function pfNextMoment(s) {
      var m=s.mower||{},manual=manualControlView(s),info=nextStartInfo(s),now=new Date(s.generatedAt),o=s.occupancy||{};
      if(manual.enabled&&["MANUAL_PARKED","PARKING"].indexOf(manual.status)>=0)return {label:"Nächster Mähstart",at:null,text:"Du entscheidest",note:"Erst nach deiner Freigabe."};
      if(mowerTelemetryFresh(s)&&["MOWING","LEAVING"].indexOf(m.activity)>=0&&o.available!==false){var ends=(o.upcoming||[]).concat(o.next?[o.next]:[]).map(function(x){return new Date(x.start)}).filter(function(d){return !isNaN(d)&&d>now}).sort(function(a,b){return a-b});if(ends.length)return {label:"Spätestens in der Station",at:ends[0],text:"",note:"Vor der nächsten Platzbelegung."}}
      return {label:"Nächster Mähstart",at:info.at,text:info.text,note:info.at?(info.awaitingMowerReport?"Geplant · Neue Mähermeldung erforderlich.":"Voraussichtlich"):""};
    }
    function pfRenderMoment(s) {
      if(!pfReady)return;var moment=pfNextMoment(s),el=document.getElementById("mower-next-start"),now=new Date(s.generatedAt);
      document.querySelector("#coordination-card .time-label").textContent=moment.label;
      el.classList.toggle("pf-unknown-time",!moment.at);el.textContent=moment.at?new Date(moment.at).toLocaleTimeString("de-DE",{timeZone:EVENT_TIME_ZONE,hour:"2-digit",minute:"2-digit",hourCycle:"h23"})+" Uhr":moment.text;
      document.getElementById("next-start-note").textContent=moment.at?(localDay(moment.at)!==localDay(now)?calendarTime(moment.at,now)+" · ":"")+moment.note:moment.note;
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
    function pfUpdate(s) {
      if(!pfReady||!s)return;var v=pfVisibility(s),manual=manualControlView(s),overview=dashboardMessage(s),safe=s.irrigation&&s.irrigation.safety||{};
      [["manual-start",v.manualStart,"Play","Mäher starten"],["manual-park",v.manualPark,"House",v.parkLabel],["manual-resume",v.manualResume,"Repeat2","Automatik einschalten"],["manual-husqvarna",v.husqvarnaStart,"Play","Über Husqvarna starten"],["manual-husqvarna-park",v.husqvarnaPark,"House","Über Husqvarna parken"]].forEach(function(row){var b=document.getElementById(row[0]);b.classList.toggle("hidden",!row[1]);pfDecorate(b,row[2],row[3])});
      ["mow-start","mow-park"].forEach(function(id){var b=document.getElementById(id);if(b.disabled)b.classList.add("hidden");pfDecorate(b,id==="mow-start"?"Play":"House")});
      document.getElementById("pf-mower-actions").classList.toggle("pf-single-action",document.querySelectorAll('#pf-mower-actions>.btn:not(.hidden)').length===1);
      document.querySelectorAll('[data-pf-target="height"]').forEach(function(b){b.hidden=!v.height});
      document.querySelectorAll('[data-pf-target="husqvarna"]').forEach(function(b){b.hidden=!manual.enabled});
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
      pfRenderHeight(s);pfRenderMoment(s);pfRenderHistory(s);
    }
    pfMountDesign();
    var pfRenderBase=render;render=function(s){pfRenderBase(s);pfUpdate(s)};
    var pfCoordinationBase=renderCoordination;renderCoordination=function(s){pfCoordinationBase(s);pfRenderMoment(s);if(pfReady)document.getElementById("pf-blade-hours").textContent=duration(s.statistics&&s.statistics.bladeUsageSeconds)};
    var pfHeightBase=renderHeightChoice;renderHeightChoice=function(){pfHeightBase();pfRenderHeight(state.status)};
    var pfLoadBase=load;load=function(){return pfLoadBase().then(function(s){if(!s&&state.status)pfUpdate(state.status);return s})};
    var pfActionBase=openAction;openAction=function(){var result=pfActionBase.apply(this,arguments);pfConfirmationIcons();return result};
    var pfManualBase=manualControlPrepare;manualControlPrepare=function(){var result=pfManualBase.apply(this,arguments);pfConfirmationIcons();return result};
    var pfZonesBase=buildPlanZones;buildPlanZones=function(zones){pfZonesBase(zones);pfPlanIcons()};
    var pfPlanBase=renderIrrigationSchedule;renderIrrigationSchedule=function(){pfPlanBase.apply(this,arguments);["plan-skip","plan-pause-open","plan-custom-open","plan-resume","plan-pause"].forEach(function(id){var b=document.getElementById(id);b.classList.toggle("hidden",b.disabled)});pfDecorate(document.getElementById("plan-history-toggle"),"History")};
    var pfWaterStatsBase=renderIrrigationStatistics;renderIrrigationStatistics=function(stats){pfWaterStatsBase(stats);document.querySelectorAll("#water-stat-zones li").forEach(function(li){pfDecorate(li,"Droplets")})};
    var pfAttentionBase=renderIrrigationAttention;renderIrrigationAttention=function(stats){pfAttentionBase(stats);pfDecorate(document.getElementById("water-attention-title"),"TriangleAlert");pfDecorate(document.getElementById("water-attention-open"),"TriangleAlert")};
