import { useState } from "react";
import type { Band, DeclaredNeed } from "../api/contract";
import {
  bandLabel,
  formatHours,
  formatRange,
  useAssessment,
} from "../assessment";
import { useLocation } from "../location";
import "./Home.css";

interface HomeProps {
  onOpenAlerts: () => void; // tapping the alert banner jumps to the Alerts tab
}

const BAND_BADGE: Record<Band, string> = {
  low: "badge--advisory",
  medium: "badge--watch",
  high: "badge--warning",
};

const SEVERITY_LABEL = ["", "Minor", "Moderate", "Strong", "Major", "Severe"];

const NEEDS: { id: DeclaredNeed; label: string }[] = [
  { id: "medical_equipment", label: "Medical equipment" },
  { id: "accessibility", label: "Accessibility needs" },
  { id: "critical_facility", label: "Critical facility" },
];

export default function Home({ onOpenAlerts }: HomeProps) {
  const { location, openPicker } = useLocation();
  const { scenario, updateScenario, resetScenario, data, loading, error, retry } =
    useAssessment();
  const [showAbout, setShowAbout] = useState(false);

  const toggleNeed = (id: DeclaredNeed) =>
    updateScenario({
      declared_needs: scenario.declared_needs.includes(id)
        ? scenario.declared_needs.filter((n) => n !== id)
        : [...scenario.declared_needs, id],
    });

  const est = data?.model_estimate;
  const meta = data?.meta;
  const pct = est ? Math.round(est.risk_score * 100) : 0;
  const range = est?.restoration_range;

  return (
    <div className="screen home">
      {/* Header: area + status */}
      <header className="home__header">
        <div>
          <h1 className="home__heading">
            <button
              type="button"
              className="home__location"
              onClick={openPicker}
              aria-label={`Area: ${location?.label ?? "none"}. Change area`}
            >
              <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor"
                strokeWidth="2" strokeLinecap="round" strokeLinejoin="round" aria-hidden>
                <path d="M12 21s-7-6.2-7-11.2A7 7 0 0 1 19 9.8C19 14.8 12 21 12 21Z" />
                <circle cx="12" cy="9.8" r="2.4" />
              </svg>
              <span className="home__city">{location?.label ?? "Pick an area"}</span>
              <svg className="home__location-chevron" width="14" height="14" viewBox="0 0 24 24"
                fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round"
                strokeLinejoin="round" aria-hidden>
                <path d="m6 9 6 6 6-6" />
              </svg>
            </button>
          </h1>
          <p className="home__updated">
            {data?.area ? `Serves ${data.area.customers_served_band} customers` : "Outage estimate"}
            {loading && data ? " · updating…" : ""}
          </p>
        </div>
        {meta && (
          <span className={`home__mode${meta.data_mode === "live" ? " is-live" : ""}`}>
            {meta.data_mode === "live" ? "Utility data" : "Sample data"}
          </span>
        )}
      </header>

      <div className="stack">
        {/* Scenario controls: judges change these and watch everything update */}
        <section className="card scenario" aria-label="Storm scenario">
          <div className="risk__top">
            <span className="eyebrow">Storm scenario</span>
            <button type="button" className="scenario__reset" onClick={resetScenario}>
              Reset
            </button>
          </div>

          <div className="scenario__row">
            <div className="scenario__label">
              <span>Storm strength</span>
              <strong>
                {scenario.storm_severity} · {SEVERITY_LABEL[scenario.storm_severity]}
              </strong>
            </div>
            <div className="segmented" role="radiogroup" aria-label="Storm strength">
              {[1, 2, 3, 4, 5].map((n) => (
                <button
                  key={n}
                  type="button"
                  role="radio"
                  aria-checked={scenario.storm_severity === n}
                  className={`segmented__item${scenario.storm_severity === n ? " is-on" : ""}`}
                  onClick={() => updateScenario({ storm_severity: n })}
                >
                  {n}
                </button>
              ))}
            </div>
          </div>

          <label className="scenario__row">
            <div className="scenario__label">
              <span>Damaged poles</span>
              <strong>{scenario.damaged_poles}</strong>
            </div>
            <input
              type="range"
              min={0}
              max={60}
              value={scenario.damaged_poles}
              onChange={(e) => updateScenario({ damaged_poles: Number(e.target.value) })}
            />
          </label>

          <label className="scenario__row">
            <div className="scenario__label">
              <span>Repair crews</span>
              <strong>{scenario.crews}</strong>
            </div>
            <input
              type="range"
              min={1}
              max={20}
              value={scenario.crews}
              onChange={(e) => updateScenario({ crews: Number(e.target.value) })}
            />
          </label>

          <div className="scenario__row scenario__row--inline">
            <div className="scenario__label">
              <span>Roads open</span>
              <small>{scenario.access_ok ? "Crews can reach the area" : "Roads blocked"}</small>
            </div>
            <button
              type="button"
              role="switch"
              aria-checked={scenario.access_ok}
              aria-label="Roads open"
              className={`switch${scenario.access_ok ? " is-on" : ""}`}
              onClick={() => updateScenario({ access_ok: !scenario.access_ok })}
            >
              <span className="switch__thumb" />
            </button>
          </div>

          <div className="scenario__row">
            <div className="scenario__label">
              <span>Your needs (optional)</span>
              <small>Only used if you choose to share them.</small>
            </div>
            <div className="chips">
              {NEEDS.map((n) => (
                <button
                  key={n.id}
                  type="button"
                  aria-pressed={scenario.declared_needs.includes(n.id)}
                  className={`chip${scenario.declared_needs.includes(n.id) ? " is-on" : ""}`}
                  onClick={() => toggleNeed(n.id)}
                >
                  {n.label}
                </button>
              ))}
            </div>
          </div>
        </section>

        {error && (
          <div className="card">
            <p className="dim">{error}</p>
            <button type="button" className="scenario__reset" onClick={retry}>
              Try again
            </button>
          </div>
        )}

        {!data && !error && (
          <div className="card">
            <p className="dim">{location ? "Loading estimate…" : "Pick an area to see an estimate."}</p>
          </div>
        )}

        {data && est && meta && range && (
          <>
            {/* Low confidence or missing data: say so plainly */}
            {meta.fallback && (
              <div className="home-banner home-banner--fallback" role="status">
                <span className="home-banner__dot" />
                <span className="home-banner__text">
                  <strong>We can't predict this reliably</strong>
                  <span>{meta.fallback_reason}</span>
                </span>
              </div>
            )}

            {est.alert_eligible && (
              <button
                type="button"
                className="home-banner"
                onClick={onOpenAlerts}
                aria-label="Outage alert. View alerts"
              >
                <span className="home-banner__dot" />
                <span className="home-banner__text">
                  <strong>Outage alert for your area</strong>
                  <span>View what to do and how we'll reach you</span>
                </span>
                <svg className="home-banner__chevron" width="16" height="16" viewBox="0 0 24 24"
                  fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round"
                  strokeLinejoin="round" aria-hidden>
                  <path d="m9 6 6 6-6 6" />
                </svg>
              </button>
            )}

            {/* Outage risk */}
            <section className={`card${meta.fallback ? " is-muted" : ""}`}>
              <div className="risk__top">
                <span className="eyebrow">Outage risk · estimate</span>
                <span className={`badge ${BAND_BADGE[est.risk_band]}`}>
                  {bandLabel(est.risk_band)}
                </span>
              </div>

              <div className={`risk__value risk__value--${est.risk_band}`}>
                {pct}
                <span>%</span>
              </div>

              <ul className="reasons">
                {est.reasons.map((r) => (
                  <li key={r.factor} className={`reasons__item reasons__item--${r.direction}`}>
                    <span aria-hidden>{r.direction === "increases_risk" ? "▲" : "▼"}</span>
                    <span>{r.text}</span>
                  </li>
                ))}
              </ul>

              <div className="risk__meter" role="img" aria-label={`${pct} percent outage risk`}>
                <div className="risk__track" />
                <div className="risk__marker" style={{ left: `${pct}%` }} />
              </div>
              <div className="risk__scale">
                <span>Low</span>
                <span>High</span>
              </div>

              <p className="confidence">
                <span className={`badge ${BAND_BADGE[est.confidence === "high" ? "low" : est.confidence === "low" ? "high" : "medium"]}`}>
                  {bandLabel(est.confidence)} confidence
                </span>
                <span>{est.confidence_reason}</span>
              </p>
            </section>

            {/* Restoration range: always a range, never one number */}
            <section className={`card${meta.fallback ? " is-muted" : ""}`}>
              <span className="eyebrow">If the power goes out · estimated restoration</span>
              <div className="restore__range">{formatRange(range.low_hours, range.high_hours)}</div>
              <p className="dim restore__sub">
                Most likely {formatHours(range.median_hours)}. The range covers the middle 80% of
                simulated outcomes.
              </p>

              {est.factor_deltas.length > 0 && (
                <ul className="deltas">
                  {est.factor_deltas.map((d) => {
                    const faster = d.delta_text.includes("faster");
                    const slower = d.delta_text.includes("slower");
                    return (
                      <li key={d.change} className="deltas__item">
                        <span className="deltas__change">What if: {d.change}</span>
                        <span className="deltas__range mono">
                          {formatRange(d.new_range.low_hours, d.new_range.high_hours)}
                        </span>
                        <span className={`deltas__text${faster ? " is-faster" : slower ? " is-slower" : ""}`}>
                          {d.delta_text}
                        </span>
                      </li>
                    );
                  })}
                </ul>
              )}
            </section>

            {/* Confirmed facts: real utility records only */}
            <section className="card">
              <span className="eyebrow">From utility records</span>
              {data.confirmed.length === 0 ? (
                <p className="dim facts__empty">No utility damage records for this area.</p>
              ) : (
                <ul className="facts">
                  {data.confirmed.map((f) => (
                    <li key={f.fact}>{f.text}</li>
                  ))}
                </ul>
              )}
              {data.area && (
                <ul className="facts facts--area">
                  {data.area.upstream_device_type && (
                    <li>
                      Power here runs through a {data.area.upstream_device_type}
                      {data.area.customers_on_upstream_device_band
                        ? ` that serves ${data.area.customers_on_upstream_device_band} customers`
                        : ""}
                      .
                    </li>
                  )}
                  {data.area.critical_facility_nearby !== null && (
                    <li>
                      Hospital, fire, or EMS station nearby:{" "}
                      {data.area.critical_facility_nearby ? "yes" : "no"}
                    </li>
                  )}
                </ul>
              )}
            </section>

            {/* About this estimate */}
            <section className="card">
              <button
                type="button"
                className="about__toggle"
                aria-expanded={showAbout}
                onClick={() => setShowAbout((v) => !v)}
              >
                <span className="eyebrow">About this estimate</span>
                <span className="dim">{showAbout ? "Hide" : "Show"}</span>
              </button>
              {showAbout && (
                <div className="about">
                  <h3>Assumptions</h3>
                  <ul>
                    {meta.assumptions.map((a) => (
                      <li key={a}>{a}</li>
                    ))}
                  </ul>
                  <h3>Limits</h3>
                  <ul>
                    {meta.limits.map((l) => (
                      <li key={l}>{l}</li>
                    ))}
                  </ul>
                  <p className="faint mono about__id">Request {meta.request_id}</p>
                </div>
              )}
            </section>
          </>
        )}
      </div>
    </div>
  );
}
