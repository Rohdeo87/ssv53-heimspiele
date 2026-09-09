const fs = require("node:fs");
const path = require("node:path");

const html = fs.readFileSync(path.join(__dirname, "../../appack-platzwart-dashboard.html"), "utf8");

function sourceOf(name) {
  const lines = html.split("\n");
  const start = lines.findIndex(line => line.startsWith(`    function ${name}(`));
  if (start < 0) throw new Error(`Missing template function: ${name}`);
  if (!lines[start].trimEnd().endsWith("{")) return lines[start];
  const end = lines.findIndex((line, index) => index > start && /^    }\s*$/.test(line));
  if (end < 0) throw new Error(`Missing function end: ${name}`);
  return lines.slice(start, end + 1).join("\n");
}

const viewFunctions = [
  "localDay", "calendarTime", "intervalEnd", "isSearching", "hasActiveMowerError", "activity", "trainingControlView", "irrigationOutsideWindow",
  "isMowerPaused", "irrigationScheduleChangePending", "coordinationExecutionBlocked", "mowerActions", "effectiveMowerActions",
  "simpleStatus", "chargingEnd", "nextStartInfo", "nextMowerStart", "actionProgressText", "irrigationActions",
  "dashboardMessage", "friendlyError", "phase", "waterTitle", "simpleWater", "nextWaterStart",
  "clubClockParts", "inputDateTime", "parsePlanDateTime", "planPauseEnd", "localDateTime", "planDate", "planStatusText"
];

function viewModel() {
  return new Function(`var EVENT_TIME_ZONE="Europe/Berlin";\n${viewFunctions.map(sourceOf).join("\n")}\nreturn {${viewFunctions.join(",")}};`)();
}

function snapshot() {
  return {
    generatedAt: "2026-09-09T10:00:00Z", controlsAvailable: true,
    overall: {code: "HYDRAWISE_CLEAR_CONFIRMATION"},
    mower: {activity: "CHARGING", state: "IN_OPERATION", connected: true, errorCode: 0, batteryPercent: 73},
    automation: {continuousMowingOwned: true, irrigationPhase: "COMPLETE_HOLD"},
    irrigation: {safety: {available: true, fresh: true, clear_now: true, active_zone_count: 0}},
    irrigationSchedule: {available: true, nextRun: {start: "2026-09-10T02:00:00Z"}},
    occupancy: {current: null, safeWindows: [{start: "2026-09-09T10:00:00Z", command_deadline: "2026-09-09T14:00:00Z", minimum_mowing_minutes: 30}]},
    coordination: {
      dryUntil: "2026-09-09T12:28:00Z", releaseNotBefore: "2026-09-09T12:30:00Z",
      blockers: [{code: "CHARGING"}, {code: "DRYING_OR_CONFIRMATION"}],
      chargingEndEstimate: {at: "2026-09-09T11:45:00Z", estimated: true}
    }
  };
}

module.exports = {html, sourceOf, viewModel, snapshot};
