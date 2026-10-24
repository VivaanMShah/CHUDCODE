import { useAssessment } from "../assessment";

// What kind of storm is coming, when it starts, and roughly how far away it is.
// Live: from the forecast and official alerts. Simulating: from the storm the customer made up.

export const STORM_OPTIONS: { value: string; label: string; icon: string }[] = [
  { value: "hurricane", label: "Hurricane or tropical storm", icon: "🌀" },
  { value: "tornado", label: "Tornado", icon: "🌪️" },
  { value: "thunderstorm", label: "Thunderstorm", icon: "⛈️" },
  { value: "winter", label: "Winter storm (snow or ice)", icon: "❄️" },
  { value: "flood", label: "Flooding rain", icon: "🌊" },
  { value: "wind", label: "High wind", icon: "💨" },
  { value: "rain", label: "Heavy rain", icon: "🌧️" },
  { value: "none", label: "No storm expected", icon: "☀️" },
];

// Storms usually travel at about this speed. Used only to turn "hours away" into "miles away".
const STORM_MPH = 25;

export function StormArrival() {
  const { live, useLive, liveAvailable, sim } = useAssessment();

  // Pick the storm to describe: the made-up one, or the real forecast.
  let type: string | null | undefined;
  let hours: number | null | undefined;
  if (!useLive) {
    type = sim.stormType;
    hours = sim.arrivalHours;
  } else {
    type = live?.storm_type;
    hours = live?.arrival_hours;
  }

  if (type == null) {
    const note = live ? live.fallback_reason ?? "Live weather is unavailable right now." : "Checking the forecast…";
    return (
      <section className="card" aria-label="Storm arrival">
        <span className="eyebrow">Storm arrival</span>
        <p className="dim">{note}</p>
      </section>
    );
  }

  const option = STORM_OPTIONS.find((o) => o.value === type) ?? STORM_OPTIONS[2];
  const noStorm = type === "none" || hours == null;
  const hoursAway = hours ?? 0;
  const when = noStorm ? "None in the next 48 hours" : hoursAway <= 0 ? "Here now" : `In about ${hoursAway} hour${hoursAway === 1 ? "" : "s"}`;
  const away = noStorm ? null : hoursAway <= 0 ? "The storm is overhead." : `About ${Math.round(hoursAway * STORM_MPH)} miles away.`;

  return (
    <section className="card arrival" aria-label="Storm arrival">
      <span className="eyebrow">Storm arrival{useLive ? "" : " · simulated"}</span>
      <div className="arrival__row">
        <span className="arrival__icon" aria-hidden>{option.icon}</span>
        <div>
          <strong className="arrival__type">{option.label}</strong>
          <div className="arrival__when">{when}</div>
        </div>
      </div>
      {away && (
        <p className="dim">
          {away} <small>(A rough guess: storms usually move about {STORM_MPH} mph.)</small>
        </p>
      )}
      {!useLive && liveAvailable && <small className="dim">Live weather is off while you simulate.</small>}
    </section>
  );
}
