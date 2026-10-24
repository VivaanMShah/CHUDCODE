import { useState } from "react";
import type { Band, DeclaredNeed, FactorDelta, HistogramBin, RestorationRange } from "../api/contract";
import {
  bandLabel,
  formatHours,
  formatRange,
  plainRule,
  useAssessment,
} from "../assessment";
import { useLocation } from "../location";
import "./Home.css";

interface HomeProps {
  onOpenAlerts: () => void; // tapping the alert badge jumps to the Alerts tab
}

const BAND_BADGE: Record<Band, string> = {
  low: "badge--advisory",
  medium: "badge--watch",
  high: "badge--warning",
};

// Color is never the only signal: every band also gets an icon and a word.
const BAND_ICON: Record<Band, string> = { low: "●", medium: "▲", high: "■" };

const SEVERITY_LABEL = ["", "Minor", "Moderate", "Strong", "Major", "Severe"];

const NEEDS: { id: DeclaredNeed; label: string }[] = [
  { id: "medical_equipment", label: "I use medical equipment that needs power" },
  { id: "accessibility", label: "I have mobility or accessibility needs" },
  { id: "critical_facility", label: "I run a critical facility" },
];

const DEVICE_LABEL: Record<string, string> = {
  "circuit breaker": "Substation breaker",
  recloser: "Recloser (automatic switch)",
  fuse: "Fuse",
  transformer: "Neighborhood transformer",
};

export default function Home({ onOpenAlerts }: HomeProps) {
  const { location, openPicker } = useLocation();
  const { scenario, updateScenario, resetScenario, data, savedAt, loading, error, retry } =
    useAssessment();
  const [showAbout, setShowAbout] = useState(false);

  const hasMedical = scenario.declared_needs.includes("medical_equipment");

  const toggleNeed = (id: DeclaredNeed) => {
    const on = scenario.declared_needs.includes(id);
    updateScenario({
      declared_needs: on
        ? scenario.declared_needs.filter((n) => n !== id)
        : [...scenario.declared_needs, id],
      // backup hours only make sense with medical equipment
      ...(id === "medical_equipment" && on ? { backup_hours: null } : {}),
    });
  };

  const est = data?.model_estimate;
  const meta = data?.meta;
  const pct = est ? Math.round(est.risk_score * 100) : 0;
  const range = est?.restoration_range;
  const chain = data?.area?.upstream_chain ?? [];

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
          <p className="home__updated" aria-live="polite">
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
        {savedAt && (
          <div className="card offline" role="status">
            <strong>You're offline.</strong> Showing the estimate saved at {savedAt}. It won't
            change until you're back online.
            <button type="button" className="scenario__reset" onClick={retry}>
              Try again
            </button>
          </div>
        )}

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
              <span id="sev-label">Storm strength</span>
              <strong>
                {scenario.storm_severity} · {SEVERITY_LABEL[scenario.storm_severity]}
              </strong>
            </div>
            <div className="segmented" role="radiogroup" aria-labelledby="sev-label">
              {[1, 2, 3, 4, 5].map((n) => (
                <button
                  key={n}
                  type="button"
                  role="radio"
                  aria-checked={scenario.storm_severity === n}
                  aria-label={`${n}, ${SEVERITY_LABEL[n]}`}
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
              <span>Roads blocked</span>
              <small>{scenario.access_ok ? "Crews can reach the area" : "Crews will be slowed down"}</small>
            </div>
            <button
              type="button"
              role="switch"
              aria-checked={!scenario.access_ok}
              aria-label="Roads blocked"
              className={`switch${!scenario.access_ok ? " is-on" : ""}`}
              onClick={() => updateScenario({ access_ok: !scenario.access_ok })}
            >
              <span className="switch__thumb" />
            </button>
          </div>

          <div className="scenario__row">
            <div className="scenario__label">
              <span>Your needs (optional)</span>
              <small>We only use what you tell us. We never guess.</small>
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

          {hasMedical && (
            <label className="scenario__row">
              <div className="scenario__label">
                <span>How long does your backup power last?</span>
                <strong>
                  {scenario.backup_hours == null ? "Not set" : `${scenario.backup_hours} h`}
                </strong>
              </div>
              <input
                type="range"
                min={1}
                max={72}
                value={scenario.backup_hours ?? 8}
                onChange={(e) => updateScenario({ backup_hours: Number(e.target.value) })}
              />
            </label>
          )}
        </section>

        {error && !data && (
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
            {/* Fallback: a distinct gray card, the reason, and what to do anyway */}
            {meta.fallback && (
              <section className="card fallback" role="status">
                <span className="eyebrow">Low confidence</span>
                <h2 className="fallback__title">We can't predict this area reliably right now</h2>
                <p className="fallback__reason">{meta.fallback_reason}</p>
                <span className="fallback__label">What to do anyway</span>
                <ul className="fallback__list">
                  {data.customer_actions.safety_messages.slice(0, 2).map((m) => (
                    <li key={m}>{m}</li>
                  ))}
                  <li>Charge phones and keep a flashlight handy.</li>
                </ul>
              </section>
            )}

            {/* Alert eligibility, with the rule that decided it */}
            <button
              type="button"
              className={`qualify${est.alert_eligible ? " is-on" : ""}`}
              onClick={onOpenAlerts}
            >
              <span className="qualify__icon" aria-hidden>
                {est.alert_eligible ? "✓" : "–"}
              </span>
              <span className="qualify__text">
                <strong>
                  {est.alert_eligible ? "You qualify for outage alerts" : "No alert needed right now"}
                </strong>
                <span>Because: {plainRule(est.alert_rule)}</span>
              </span>
            </button>

            {/* Outage risk */}
            <section className={`card${meta.fallback ? " is-muted" : ""}`} aria-label="Outage risk">
              <div className="risk__top">
                <span className="eyebrow">Outage risk · model estimate</span>
                <span className={`badge ${BAND_BADGE[est.risk_band]}`}>
                  <span aria-hidden>{BAND_ICON[est.risk_band]}&nbsp;</span>
                  {bandLabel(est.risk_band)} risk
                </span>
              </div>

              <div className={`risk__value risk__value--${est.risk_band}`}>
                {pct}
                <span>%</span>
              </div>

              <ul className="reasons" aria-label="Top reasons">
                {est.reasons.map((r) => (
                  <li key={r.factor} className={`reasons__item reasons__item--${r.direction}`}>
                    <span aria-label={r.direction === "increases_risk" ? "raises risk" : "lowers risk"}>
                      {r.direction === "increases_risk" ? "▲" : "▼"}
                    </span>
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

              <div className="confidence">
                <span className={`pill pill--${est.confidence}`}>
                  {bandLabel(est.confidence)} confidence
                </span>
                <span>{est.confidence_reason}</span>
              </div>
            </section>

            {/* Restoration: always a range, never one number */}
            <section className={`card${meta.fallback ? " is-muted" : ""}`} aria-label="Estimated restoration">
              <span className="eyebrow">If the power goes out · estimated restoration</span>
              <div className="restore__range">{formatRange(range.low_hours, range.high_hours)}</div>
              <RangeBar range={range} bins={est.restoration_histogram ?? []} />
              <p className="dim restore__sub">
                In 80% of {est.simulations ? est.simulations.toLocaleString() : "the"} simulated
                storms, power was back between {formatRange(range.low_hours, range.high_hours)}. Most
                likely {formatHours(range.median_hours)}.
              </p>

              {est.factor_deltas.length > 0 && (
                <>
                  <span className="eyebrow deltas__title">What moves this · tap to try</span>
                  <div className="delta-chips">
                    {est.factor_deltas.map((d) => (
                      <DeltaChip
                        key={d.change}
                        delta={d}
                        onApply={() => d.scenario_patch && updateScenario(d.scenario_patch)}
                      />
                    ))}
                  </div>
                </>
              )}
            </section>

            {/* Upstream dependency chain */}
            {chain.length > 0 && (
              <section className="card" aria-label="What your power depends on">
                <span className="eyebrow">What your power depends on</span>
                <p className="chain__intro">
                  Power reaches your area through {chain.length} pieces of equipment. If one fails,
                  everyone below it loses power. Repairs usually start at the top.
                </p>
                <ol className="chain">
                  {chain.map((step, i) => (
                    <li key={i} className="chain__step">
                      <span className="chain__node" aria-hidden />
                      <span className="chain__name">{DEVICE_LABEL[step.device_type] ?? step.device_type}</span>
                      {step.customers_approx != null && (
                        <span className="chain__count">
                          about {step.customers_approx.toLocaleString()} customers
                        </span>
                      )}
                    </li>
                  ))}
                  <li className="chain__step chain__step--you">
                    <span className="chain__node" aria-hidden />
                    <span className="chain__name">Your area</span>
                  </li>
                </ol>
              </section>
            )}

            {/* Confirmed facts: real utility records only */}
            <section className="card" aria-label="From utility records">
              <span className="eyebrow">Confirmed · from utility records</span>
              {data.confirmed.length === 0 ? (
                <p className="dim facts__empty">No utility damage records for this area.</p>
              ) : (
                <ul className="facts">
                  {data.confirmed.map((f) => (
                    <li key={f.fact}>{f.text}</li>
                  ))}
                </ul>
              )}
              {data.area?.critical_facility_nearby != null && (
                <ul className="facts facts--area">
                  <li>
                    Hospital or emergency service on your circuit:{" "}
                    {data.area.critical_facility_nearby ? "Yes" : "No"}
                  </li>
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
              <p className="about__label">Model estimate, not confirmed by AEP.</p>
              {showAbout && (
                <div className="about">
                  {meta.backtest && (
                    <>
                      <h3>How well does it work?</h3>
                      <p>
                        We trained on storms before the {meta.backtest.event_label} and tested on
                        it. Across {meta.backtest.areas} areas the model scored{" "}
                        {meta.backtest.auc.toFixed(2)} AUC (1.0 is perfect, 0.5 is a coin flip).
                        {meta.backtest.high_band_hit_rate != null &&
                          ` Of ${meta.backtest.high_band_areas} areas it rated high risk, ${Math.round(
                            meta.backtest.high_band_hit_rate * 100,
                          )}% had damage.`}
                        {meta.backtest.low_band_hit_rate != null &&
                          ` Of ${meta.backtest.low_band_areas} rated low, ${Math.round(
                            meta.backtest.low_band_hit_rate * 100,
                          )}% did.`}
                      </p>
                    </>
                  )}
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
                  <h3>How we handle data responsibly</h3>
                  <ul className="checklist">
                    <li>Every number is labeled as an estimate or confirmed utility record.</li>
                    <li>Confidence is shown, with where it comes from.</li>
                    <li>When we can't predict reliably, we say so instead of guessing.</li>
                    <li>No coordinates, names, addresses, or employee info leave the server.</li>
                    <li>Hospitals and emergency services are only a yes/no flag.</li>
                    <li>Your needs are only what you tell us. We never infer them.</li>
                    <li>AI never writes numbers. Every number in a message is checked against the data.</li>
                    <li>Every request is logged without personal data.</li>
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

// Horizontal range bar with a small histogram of simulated outcomes above it.
function RangeBar({ range, bins }: { range: RestorationRange; bins: HistogramBin[] }) {
  if (bins.length === 0) return null;
  const min = bins[0].low_hours;
  const max = bins[bins.length - 1].high_hours;
  const span = Math.max(max - min, 1e-6);
  const pos = (h: number) => `${Math.min(100, Math.max(0, ((h - min) / span) * 100))}%`;
  const peak = Math.max(...bins.map((b) => b.share), 1e-6);
  return (
    <div className="rangebar" role="img"
      aria-label={`Simulated outcomes from ${formatRange(min, max)}; 80% fall between ${formatRange(range.low_hours, range.high_hours)}`}>
      <div className="rangebar__hist">
        {bins.map((b, i) => {
          const inside = b.high_hours > range.low_hours && b.low_hours < range.high_hours;
          return (
            <span
              key={i}
              className={`rangebar__col${inside ? " is-inside" : ""}`}
              style={{ height: `${Math.max(4, (b.share / peak) * 100)}%` }}
            />
          );
        })}
      </div>
      <div className="rangebar__track">
        <span
          className="rangebar__fill"
          style={{ left: pos(range.low_hours), width: `calc(${pos(range.high_hours)} - ${pos(range.low_hours)})` }}
        />
        <span className="rangebar__median" style={{ left: pos(range.median_hours) }} />
      </div>
      <div className="rangebar__scale mono">
        <span>{scaleLabel(min, max >= 48)}</span>
        <span>{scaleLabel(max, max >= 48)}</span>
      </div>
    </div>
  );
}

function scaleLabel(h: number, days: boolean): string {
  const v = days ? h / 24 : h;
  const r = v >= 10 ? Math.round(v) : Math.round(v * 10) / 10;
  return `${r} ${days ? "d" : "h"}`;
}

function DeltaChip({ delta, onApply }: { delta: FactorDelta; onApply: () => void }) {
  const faster = delta.delta_text.includes("faster");
  const slower = delta.delta_text.includes("slower");
  return (
    <button
      type="button"
      className={`delta-chip${faster ? " is-faster" : slower ? " is-slower" : ""}`}
      onClick={onApply}
      disabled={!delta.scenario_patch}
      aria-label={`Try ${delta.change}: ${formatRange(delta.new_range.low_hours, delta.new_range.high_hours)}, ${delta.delta_text}`}
    >
      <strong>{delta.change}</strong>
      <span className="mono">{formatRange(delta.new_range.low_hours, delta.new_range.high_hours)}</span>
      <small>{delta.delta_text}</small>
    </button>
  );
}
