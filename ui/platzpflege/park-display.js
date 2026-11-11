/* SSV53_PARK_POSITION_DISPLAY_START */
    function manualParkPositionDisplay(s) {
      var manual=s&&s.manualControl||{},m=s&&s.mower||{},a=s&&s.automation||{};
      if(manual.enabled!==true||manual.status!=="MANUAL_PARKED"||["PENDING","QUEUED","RESERVED","SENT_UNCONFIRMED","UNKNOWN"].indexOf(manual.requestStatus)>=0||mowerServiceView(s).active||a.mowerStartOutcomeUnconfirmed||hasActiveMowerError(m))return null;
      if(["PARKED_IN_CS","CHARGING"].indexOf(m.activity)<0)return null;
      var fresh=m.connected===true&&mowerTelemetryFresh(s),stamp=new Date(m.statusTimestamp),now=new Date(s.generatedAt),hasStamp=m.statusTimestamp!=null&&!isNaN(stamp.getTime())&&stamp.getTime()>0&&!isNaN(now.getTime())&&stamp<=now;
      if(fresh)return {title:"Mäher in Station",text:"Die Automatik ist pausiert. Du entscheidest über die Freigabe.",tone:"good",icon:"House"};
      if(hasStamp)return {title:"Zuletzt in Station gemeldet",text:"Mähermeldung: "+calendarTime(stamp,now)+". Die Automatik bleibt pausiert.",tone:"warn",icon:"House"};
      return {title:"Automatik pausiert",text:"Der aktuelle Parkstatus ist nicht bestätigt. Bitte aktualisieren.",tone:"warn",icon:"House"};
    }
    var parkPositionDashboardBase=dashboardMessage;
    dashboardMessage=function(s){var result=parkPositionDashboardBase(s),park=manualParkPositionDisplay(s);return park&&["Parken angefordert","Mähermeldung ist älter","Mäher in Station geparkt"].indexOf(result.title)>=0?park:result};
    var parkPositionManualViewBase=manualControlView;
    manualControlView=function(s){var result=parkPositionManualViewBase(s),park=manualParkPositionDisplay(s);if(park){var original=String(result.message||""),waiting="Bitte warten, bis der Mäher die Station erreicht hat.",message=original.indexOf(waiting)===0?park.text+original.slice(waiting.length):original;return Object.assign({},result,{title:park.title,message:message})}return result};
/* SSV53_PARK_POSITION_DISPLAY_END */
