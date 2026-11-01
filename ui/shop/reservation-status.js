/* SSV53_RESERVATION_STATUS_START */
(function () {
  "use strict";
  const root = document.getElementById("orders");
  if (!root) return;
  const endpoint = "https://func-ssv53platzpflege-prod-q7kbw54s.azurewebsites.net/api/reservation-notifications";
  const cache = new Map(), inFlight = new Set();
  const states = {sent:"Versendet",sending:"Versand wird geprüft",failed:"Fehlgeschlagen",queued:"Ausstehend",expired:"Bitte manuell prüfen"};
  function paint(card, result) {
    let box = card.querySelector(".reservation-notification-state");
    if (!box) {
      box = document.createElement("div");box.className="reservation-notification-state";
      Object.assign(box.style,{margin:"10px 0",padding:"10px",borderRadius:"10px",background:"#f3f6fb",fontSize:"13px",lineHeight:"1.5"});
      card.insertBefore(box,card.querySelector(".controls"));
    }
    box.replaceChildren();
    const title = document.createElement("strong");title.textContent="Benachrichtigungen zur Vormerkung";box.appendChild(title);
    function line(text) { const p=document.createElement("div");p.textContent=text;box.appendChild(p); }
    if (!result) line("Versandstatus wird geladen …");
    else if (result.error) line("Versandstatus gerade nicht verfügbar.");
    else if (!result.enabled) line("Automatische Benachrichtigungen sind noch nicht aktiv.");
    else if (!result.applicable) line("Für diesen Vorgang erfolgt kein automatischer Nachversand.");
    else {
      [["customer","Eingangsbestätigung"],["office","Geschäftsstelle"]].forEach(function(pair){
        const state=(result.channels||{})[pair[0]]||{};
        let text=pair[1]+": "+(states[state.state]||"Unbekannt");
        if (state.state==="sent" && Number.isFinite(Date.parse(state.sentAtUtc))) text+=" · "+new Date(state.sentAtUtc).toLocaleString("de-DE",{timeZone:"Europe/Berlin"});
        line(text);
      });
    }
    const button=document.createElement("button");button.type="button";button.textContent="Versandstatus aktualisieren";
    Object.assign(button.style,{marginTop:"6px",padding:"5px 8px",border:"1px solid #dce2e8",borderRadius:"7px",background:"white",color:"#285ea7",cursor:"pointer"});
    button.onclick=function(){load(card.dataset.id,true);};box.appendChild(button);
  }
  function show(id,result) { root.querySelectorAll(".order").forEach(function(card){if(card.dataset.id===id)paint(card,result);}); }
  function load(id,force) {
    if(!/^SSV53-\d{6}-\d{6}-[A-Z0-9]{4}$/.test(id)||inFlight.has(id))return;
    if(!force&&cache.has(id)){show(id,cache.get(id));return;}
    inFlight.add(id);show(id,null);
    fetch(endpoint+"?orderId="+encodeURIComponent(id),{method:"GET",credentials:"omit",cache:"no-store"})
      .then(function(r){if(!r.ok)throw new Error("unavailable");return r.json();})
      .then(function(result){if(result.orderId!==id)throw new Error("mismatch");cache.set(id,result);show(id,result);})
      .catch(function(){show(id,{error:true});})
      .finally(function(){inFlight.delete(id);});
  }
  function refresh(){root.querySelectorAll(".order").forEach(function(card){load(card.dataset.id,false);});}
  new MutationObserver(refresh).observe(root,{childList:true});
  refresh();
})();
/* SSV53_RESERVATION_STATUS_END */
