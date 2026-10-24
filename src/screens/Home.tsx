import { useEffect, useState } from "react";
import { fetchHomeSummary } from "api/placeholders";
import { useLocation } from "../location";
import type { HomeSummary } from "../types";
import "./Home.css";

interface HomeProps {
  onOpenAlerts: () => void; // tapping the red banner jumps to the Alerts tab
}

// Bar color by chance: blue (low) → yellow (medium) → red (high)
function chanceColor(percent: number): string {
  if (percent >= 65) return "var(--red)";
  if (percent >= 40) return "var(--yellow)";
  return "var(--blue)";
}

export default function Home({ onOpenAlerts }: HomeProps) {
  const { location, coords, openPicker } = useLocation();
  const lat = coords?.lat;
  const lon = coords?.lon;

  const [data, setData] = useState<HomeSummary | null>(null);
  const [error, setError] = useState(false);

  // Load conditions, and reload whenever the user picks a different location.
  useEffect(() => {
    let cancelled = false;
    const c = lat !== undefined && lon !== undefined ? { lat, lon } : undefined;
    setData(null);
    setError(false);
    fetchHomeSummary(c)
      .then((d) => !cancelled && setData(d))
      .catch(() => !cancelled && setError(true));
    return () => {
      cancelled = true;
    };
  }, [lat, lon]);

  if (error) {
    return (
      <div className="screen">
        <p className="dim">Couldn't load conditions. Try again in a moment.</p>
      </div>
    );
  }

  if (!data) {
    return (
      <div className="screen">
        <p className="dim">Loading conditions…</p>
      </div>
    );
  }

  const { stormRisk, hourlyChance, stats, topAlert } = data;

  return (
    <div className="screen home">
      {/* Header: location + temperature */}
      <header className="home__header">
        <div>
          {/* Tap the place name to change location */}
          <h1 className="home__heading">
          <button
            type="button"
            className="home__location"
            onClick={openPicker}
            aria-label={`Location: ${location?.label ?? data.locationName}. Change location`}
          >
            <svg
              width="14"
              height="14"
              viewBox="0 0 24 24"
              fill="none"
              stroke="currentColor"
              strokeWidth="2"
              strokeLinecap="round"
              strokeLinejoin="round"
              aria-hidden
            >
              <path d="M12 21s-7-6.2-7-11.2A7 7 0 0 1 19 9.8C19 14.8 12 21 12 21Z" />
              <circle cx="12" cy="9.8" r="2.4" />
            </svg>
            <span className="home__city">{location?.label ?? data.locationName}</span>
            <svg
              className="home__location-chevron"
              width="14"
              height="14"
              viewBox="0 0 24 24"
              fill="none"
              stroke="currentColor"
              strokeWidth="2"
              strokeLinecap="round"
              strokeLinejoin="round"
              aria-hidden
            >
              <path d="m6 9 6 6 6-6" />
            </svg>
          </button>
          </h1>
          <p className="home__updated">Updated {data.updatedAt}</p>
        </div>
        <div className="home__temp">
          <span className="home__temp-value">{data.temperatureF}°</span>
          <span className="home__temp-label">{data.conditionLabel}</span>
        </div>
      </header>

      <div className="stack">
        {/* Top alert banner */}
        {topAlert && (
          <button
            type="button"
            className="home-banner"
            onClick={onOpenAlerts}
            aria-label={`${topAlert.title}. ${topAlert.actionLabel}`}
          >
            <span className="home-banner__dot" />
            <span className="home-banner__text">
              <strong>{topAlert.title}</strong>
              <span>{topAlert.actionLabel}</span>
            </span>
            <svg
              className="home-banner__chevron"
              width="16"
              height="16"
              viewBox="0 0 24 24"
              fill="none"
              stroke="currentColor"
              strokeWidth="2"
              strokeLinecap="round"
              strokeLinejoin="round"
              aria-hidden
            >
              <path d="m9 6 6 6-6 6" />
            </svg>
          </button>
        )}

        {/* Storm risk */}
        <section className="card">
          <div className="risk__top">
            <span className="eyebrow">Storm risk · {stormRisk.windowLabel}</span>
            <span className="badge badge--high">{stormRisk.level}</span>
          </div>

          <div className="risk__value">
            {stormRisk.percent}
            <span>%</span>
          </div>

          <p className="risk__summary">{stormRisk.summary}</p>

          <div className="risk__meter" role="img" aria-label={`${stormRisk.percent} percent storm risk`}>
            <div className="risk__track" />
            <div
              className="risk__marker"
              style={{ left: `${stormRisk.percent}%` }}
            />
          </div>
          <div className="risk__scale">
            <span>Low</span>
            <span>Extreme</span>
          </div>
        </section>

        {/* Hourly chance of storms */}
        <section className="card">
          <span className="eyebrow">Chance of storms</span>
          <div className="chance">
            {hourlyChance.map((h) => (
              <div className="chance__col" key={h.label}>
                <span className="chance__label">{h.label}</span>
                <div className="chance__bar-area">
                  <div
                    className="chance__bar"
                    style={{
                      height: `${Math.max(8, h.percent * 0.7)}px`,
                      background: chanceColor(h.percent),
                    }}
                  />
                </div>
                <span className="chance__percent">{h.percent}%</span>
              </div>
            ))}
          </div>
        </section>

        {/* Quick stats */}
        <section className="home-stats">
          <div className="home-stats__item">
            <span className="home-stats__label">Wind gusts</span>
            <span className="home-stats__value">
              {stats.windGustMph}
              <small> mph</small>
            </span>
          </div>
          <div className="home-stats__item">
            <span className="home-stats__label">Pressure</span>
            <span className="home-stats__value">
              {stats.pressureInHg}
              <small> inHg</small>
            </span>
          </div>
          <div className="home-stats__item">
            <span className="home-stats__label">Lightning</span>
            <span className="home-stats__value">
              {stats.lightningCount}
              <small>
                {" "}
                in {stats.lightningWindowMin} min · {stats.lightningRadiusMi} mi
              </small>
            </span>
          </div>
        </section>
      </div>
    </div>
  );
}