import { useState } from "react";
import type { Band } from "../api/contract";
import AboutEstimate from "../components/AboutEstimate";
import RangeBar from "../components/RangeBar";
import {
  SEVERITY_LABEL,
  bandLabel,
  formatHours,
  formatRange,
  plainRule,
  useAssessment,
} from "../assessment";
import { useLocation } from "../location";
import { tipsFor, useProfile } from "../profile";
import "./Home.css";

interface HomeProps {
  onOpenAlerts: () => void;
  onOpenPrepare: () => void;
}

// Color is never the only signal: each band also has a symbol and a word.
const BAND_ICON: Record<Band, string> = { low: "●", medium: "▲", high: "■" };

const DEVICE_LABEL: Record<string, string> = {
  "circuit breaker": "Substation breaker",
  recloser: "Recloser (automatic switch)",
  fuse: "Fuse",
  transformer: "Neighborhood transformer",
};

export default function Home({ onOpenAlerts, onOpenPrepare }: HomeProps) {
  const { location, openPicker } = useLocation();
  const { scenario, updateScenario, resetScenario, live, useLive, setUseLive, data, quiet, savedAt, loading, error, retry } =
    useAssessment();
  const [simulating, setSimulating] = useState(false);

  const est = data?.model_estimate;
  const meta = data?.meta;
  const range = est?.restoration_range;
  const chain = data?.area?.upstream_chain ?? [];
  const liveOk = !!live && live.severity !== null;
  const weatherAlerts = live?.alerts ?? [];

  const { profile, isEmpty, openSheet, promptDismissed, dismissPrompt } = useProfile();
  const tips = tipsFor(profile, { severity: scenario.storm_severity, tempF: live?.hourly[0]?.temp_f ?? null });
  const todo = [...tips, ...(data?.customer_actions.actions ?? [])].slice(0, 3);

  const updated = live ? new Date(live.updated_at).toLocaleTimeString([], { hour: "numeric", minute: "2-digit" }) : null;

  return (
    <div className="screen home">
      <header className="home__header">
        <button type="button" className="home__location" onClick={openPicker}
          aria-label={`${location?.label ?? "No ZIP"}. Change ZIP code`}>
          <span className="home__city">{location?.label ?? "Enter your ZIP"}</span>
          <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor"
            strokeWidth="2" strokeLinecap="round" strokeLinejoin="round" aria-hidden>
            <path d="m6 9 6 6 6-6" />
          </svg>
        </button>
        <span className={`status${liveOk && useLive ? " is-live" : ""}`} aria-live="polite">
          {liveOk && useLive ? `Live · ${updated}` : liveOk ? "Simulating" : "Live weather unavailable"}
        </span>
      </header>

      <div className="stack">
        {savedAt && (
          <div className="note" role="status">
            You're offline. Showing the estimate saved at {savedAt}.{" "}
            <button type="button" className="link" onClick={retry}>Try again</button>
          </div>
        )}

        {weatherAlerts.length > 0 && (
          <button type="button" className="note note--alert" onClick={onOpenAlerts}>
            <strong>{weatherAlerts[0].event}</strong>
            <span>{weatherAlerts.length > 1 ? `+${weatherAlerts.length - 1} more · ` : ""}See details</span>
          </button>
        )}

        {error && !data && (
          <div className="card">
            <p className="dim">{error}</p>
            <button type="button" className="link" onClick={retry}>Try again</button>
          </div>
        )}
        {!data && !error && (
          <div className="card"><p className="dim">{location ? "Loading your estimate…" : "Enter your ZIP to begin."}</p></div>
        )}

        {data && isEmpty && !promptDismissed && (
          <section className="card prompt">
            <strong>Make this fit you</strong>
            <p>Optional and private. It takes about 30 seconds.</p>
            <div className="row-links">
              <button type="button" className="link" onClick={openSheet}>Add details</button>
              <button type="button" className="link link--dim" onClick={dismissPrompt}>Not now</button>
            </div>
          </section>
        )}

        {data && est && meta && range && (
          <>
            {meta.fallback && (
              <section className="card fallback" role="status">
                <h2 className="fallback__title">We can't predict this reliably right now</h2>
                <p className="fallback__reason">{meta.fallback_reason}</p>
                {!quiet && (
                  <>
                    <span className="fallback__label">What to do anyway</span>
                    <ul className="fallback__list">
                      {data.customer_actions.safety_messages.slice(0, 2).map((m) => <li key={m}>{m}</li>)}
                      <li>Charge phones and keep a flashlight handy.</li>
                    </ul>
                  </>
                )}
              </section>
            )}

            {/* The one answer */}
            <section className={`card hero${meta.fallback ? " is-muted" : ""}`} aria-label="Your outage estimate">
              <span className="eyebrow">{useLive && liveOk ? "Weather right now" : "Simulated storm"}</span>
              <h1 className="hero__weather">{SEVERITY_LABEL[scenario.storm_severity]}</h1>
              <p className="hero__sub">
                {useLive && liveOk && live?.reasons[0]
                  ? `${live.reasons[0].text}${live.chance_pct !== null ? ` Chance of storm damage: ${live.chance_pct}%.` : ""}`
                  : `Storm strength ${scenario.storm_severity} of 5.`}
              </p>

              <div className="hero__row">
                <div>
                  <span className="eyebrow">Outage risk</span>
                  <div className={`hero__risk hero__risk--${est.risk_band}`}>
                    <span aria-hidden>{BAND_ICON[est.risk_band]}</span> {bandLabel(est.risk_band)}
                  </div>
                </div>
                <span className={`pill pill--${est.confidence}`}>{bandLabel(est.confidence)} confidence</span>
              </div>
              {est.reasons[0] && <p className="hero__why">{est.reasons[0].text}</p>}

              <div className="hero__divider" />
              <span className="eyebrow">If the power goes out</span>
              <div className="hero__range">{formatRange(range.low_hours, range.high_hours)}</div>
              <p className="hero__sub">
                Most likely {formatHours(range.median_hours)}. In 8 of 10 simulated storms, power was back in this range.
              </p>
              <RangeBar range={range} bins={est.restoration_histogram ?? []} />

              {est.factor_deltas.length > 0 && (
                <div className="chips chips--scroll" aria-label="What could change this. Tap to try.">
                  {est.factor_deltas.map((d) => (
                    <button key={d.change} type="button" className="chip chip--delta"
                      disabled={!d.scenario_patch}
                      onClick={() => d.scenario_patch && updateScenario(d.scenario_patch)}
                      aria-label={`Try ${d.change}: ${formatRange(d.new_range.low_hours, d.new_range.high_hours)}, ${d.delta_text}`}>
                      {d.change}
                      <small className={d.delta_text.includes("faster") ? "is-faster" : d.delta_text.includes("slower") ? "is-slower" : ""}>
                        {d.delta_text.replace("about ", "")}
                      </small>
                    </button>
                  ))}
                </div>
              )}
              {loading && <span className="hero__updating">Updating…</span>}
            </section>

            {/* Alert eligibility + next steps. With no storm, say so and stop. */}
            {quiet ? (
              <section className="card quiet-card">
                <strong>All clear</strong>
                <p>No storm is expected, so there is nothing you need to do right now. We'll tell you if that changes.</p>
                <div className="row-links">
                  <button type="button" className="link" onClick={onOpenAlerts}>Text alerts</button>
                  <button type="button" className="link" onClick={openSheet}>About you</button>
                </div>
              </section>
            ) : (
            <section className="card">
              <div className="qualify">
                <span className={`qualify__icon${est.alert_eligible ? " is-on" : ""}`} aria-hidden>
                  {est.alert_eligible ? "✓" : "–"}
                </span>
                <div>
                  <strong>{est.alert_eligible ? "You qualify for outage alerts" : "No alert needed right now"}</strong>
                  <p>Because: {plainRule(est.alert_rule)}</p>
                </div>
              </div>
              <span className="eyebrow">Do these next</span>
              <ul className="todo">
                {todo.map((a) => <li key={a}>{a}</li>)}
              </ul>
              <div className="row-links">
                <button type="button" className="link" onClick={onOpenPrepare}>Full checklist</button>
                <button type="button" className="link" onClick={onOpenAlerts}>How we'd contact you</button>
                <button type="button" className="link" onClick={openSheet}>About you</button>
              </div>
            </section>
            )}
          </>
        )}

        {/* Try a storm */}
        <section className="card simulate">
          <button type="button" className="simulate__toggle" aria-expanded={simulating}
            onClick={() => setSimulating((v) => !v)}>
            <span>
              <strong>Try a storm</strong>
              <small>See how the estimate changes</small>
            </span>
            <span className="dim" aria-hidden>{simulating ? "−" : "+"}</span>
          </button>

          {simulating && (
            <div className="simulate__body">
              <div className="scenario__row">
                <div className="scenario__label">
                  <span id="sev-label">Storm strength</span>
                  <strong>{scenario.storm_severity} · {SEVERITY_LABEL[scenario.storm_severity]}</strong>
                </div>
                <div className="segmented" role="radiogroup" aria-labelledby="sev-label">
                  {[1, 2, 3, 4, 5].map((n) => (
                    <button key={n} type="button" role="radio"
                      aria-checked={!useLive && scenario.storm_severity === n}
                      aria-label={`${n}, ${SEVERITY_LABEL[n]}`}
                      className={`segmented__item${!useLive && scenario.storm_severity === n ? " is-on" : ""}`}
                      onClick={() => updateScenario({ storm_severity: n })}>{n}</button>
                  ))}
                </div>
                {liveOk && !useLive && (
                  <button type="button" className="link" onClick={() => setUseLive(true)}>Back to live weather</button>
                )}
                {useLive && <small className="dim">Showing live weather. Tap a number to simulate.</small>}
              </div>

              <label className="scenario__row">
                <div className="scenario__label"><span>Damaged poles</span><strong>{scenario.damaged_poles}</strong></div>
                <input type="range" min={0} max={60} value={scenario.damaged_poles}
                  onChange={(e) => updateScenario({ damaged_poles: Number(e.target.value) })} />
              </label>

              <label className="scenario__row">
                <div className="scenario__label"><span>Repair crews</span><strong>{scenario.crews}</strong></div>
                <input type="range" min={1} max={20} value={scenario.crews}
                  onChange={(e) => updateScenario({ crews: Number(e.target.value) })} />
              </label>

              <div className="scenario__row scenario__row--inline">
                <div className="scenario__label">
                  <span>Roads blocked</span>
                  <small>{scenario.access_ok ? "Crews can reach the area" : "Crews will be slowed down"}</small>
                </div>
                <button type="button" role="switch" aria-checked={!scenario.access_ok} aria-label="Roads blocked"
                  className={`switch${!scenario.access_ok ? " is-on" : ""}`}
                  onClick={() => updateScenario({ access_ok: !scenario.access_ok })}>
                  <span className="switch__thumb" />
                </button>
              </div>

              <p className="details__small dim">
                Medical equipment, backup power, and other needs are set in{" "}
                <button type="button" className="link" onClick={openSheet}>About you</button>.
              </p>

              <button type="button" className="link" onClick={resetScenario}>Reset</button>
            </div>
          )}
        </section>

        {/* Details, one tap away */}
        {data && est && meta && (
          <div className="details">
            <details>
              <summary>Why we think this</summary>
              <ul className="reasons">
                {est.reasons.map((r) => (
                  <li key={r.factor}>
                    <span aria-label={r.direction === "increases_risk" ? "raises risk" : "lowers risk"}>
                      {r.direction === "increases_risk" ? "▲" : "▼"}
                    </span>
                    <span>{r.text}</span>
                  </li>
                ))}
              </ul>
              <p className="dim details__small">{est.confidence_reason}</p>
            </details>

            {chain.length > 0 && (
              <details>
                <summary>What your power depends on</summary>
                <p className="details__small dim">
                  Power reaches you through {chain.length} pieces of equipment. If one fails, everyone below it loses
                  power. Repairs usually start at the top.
                </p>
                <ol className="chain">
                  {chain.map((s, i) => (
                    <li key={i} className="chain__step">
                      <span className="chain__node" aria-hidden />
                      <span>{DEVICE_LABEL[s.device_type] ?? s.device_type}</span>
                      {s.customers_approx != null && <small>about {s.customers_approx.toLocaleString()} customers</small>}
                    </li>
                  ))}
                  <li className="chain__step chain__step--you">
                    <span className="chain__node" aria-hidden /><span>Your area</span>
                  </li>
                </ol>
              </details>
            )}

            <details>
              <summary>Confirmed by utility records</summary>
              {data.confirmed.length === 0 ? (
                <p className="dim details__small">No utility damage records for this area.</p>
              ) : (
                <ul className="facts">{data.confirmed.map((f) => <li key={f.fact}>{f.text}</li>)}</ul>
              )}
              {data.area?.critical_facility_nearby != null && (
                <p className="details__small dim">
                  Hospital or emergency service on your circuit: {data.area.critical_facility_nearby ? "Yes" : "No"}
                </p>
              )}
            </details>

            <details>
              <summary>About this estimate</summary>
              <AboutEstimate meta={meta} live={live} />
            </details>
          </div>
        )}
      </div>
    </div>
  );
}
