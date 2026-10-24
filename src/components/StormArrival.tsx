import type { WeatherResponse } from "../api/contract";

// What kind of storm is coming and when it starts. Comes from the live forecast and official alerts.
const ICON: Record<string, string> = {
  hurricane: "🌀", tornado: "🌪️", thunderstorm: "⛈️", winter: "❄️", flood: "🌊", wind: "💨", rain: "🌧️", none: "☀️",
};

export function StormArrival({ live }: { live: WeatherResponse | null }) {
  if (!live || live.storm_type == null) {
    return (
      <section className="card" aria-label="Storm arrival">
        <span className="eyebrow">Storm arrival</span>
        <p className="dim">{live ? live.fallback_reason ?? "Live weather is unavailable right now." : "Checking the forecast…"}</p>
      </section>
    );
  }
  const none = live.storm_type === "none";
  const hours = live.arrival_hours;
  const when = none || hours == null ? "None in the next 48 hours" : hours <= 0 ? "Here now" : `In about ${hours} hour${hours === 1 ? "" : "s"}`;
  return (
    <section className="card arrival" aria-label="Storm arrival">
      <span className="eyebrow">Storm arrival</span>
      <div className="arrival__row">
        <span className="arrival__icon" aria-hidden>{ICON[live.storm_type] ?? "⛈️"}</span>
        <div>
          <strong className="arrival__type">{live.storm_type_label}</strong>
          <div className="arrival__when">{when}</div>
        </div>
      </div>
      {!none && live.severity_label && <p className="dim">Strength: {live.severity_label}. {live.arrival_text}</p>}
      <small className="dim">Turn on phone alerts below and we'll send a notification about 5 hours before a storm arrives.</small>
    </section>
  );
}
