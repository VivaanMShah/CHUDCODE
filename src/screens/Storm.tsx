import { useEffect, useState } from "react";
import { formatRange, useAssessment } from "../assessment";
import { useLocation } from "../location";
import { tipsFor, useProfile } from "../profile";
import "./Storm.css";

// Prepare tab: the backend's prep actions as a checklist, plus safety messages.
// Checkmarks are saved on this device only.
const DONE_KEY = "storm-watch:prep-done";

function loadDone(): string[] {
  try {
    const raw = localStorage.getItem(DONE_KEY);
    const v: unknown = raw ? JSON.parse(raw) : [];
    return Array.isArray(v) ? v.filter((x): x is string => typeof x === "string") : [];
  } catch {
    return [];
  }
}

export default function Storm() {
  const { location } = useLocation();
  const { data, error, live, scenario } = useAssessment();
  const { profile } = useProfile();
  const [done, setDone] = useState<string[]>(loadDone);
  const [reporting, setReporting] = useState(false);

  useEffect(() => {
    try {
      localStorage.setItem(DONE_KEY, JSON.stringify(done));
    } catch {
      /* not critical */
    }
  }, [done]);

  const toggle = (label: string) =>
    setDone((d) => (d.includes(label) ? d.filter((x) => x !== label) : [...d, label]));

  if (error && !data) {
    return (
      <div className="screen">
        <p className="dim">{error}</p>
      </div>
    );
  }

  if (!data) {
    return (
      <div className="screen">
        <p className="dim">{location ? "Loading your prep list…" : "Pick an area on Home first."}</p>
      </div>
    );
  }

  const { safety_messages } = data.customer_actions;
  const tips = tipsFor(profile, { severity: scenario.storm_severity, tempF: live?.hourly[0]?.temp_f ?? null });
  const actions = [...tips, ...data.customer_actions.actions];
  const range = data.model_estimate.restoration_range;
  const doneCount = actions.filter((a) => done.includes(a)).length;

  return (
    <div className="screen storm">
      <header className="screen-header">
        <div>
          <h1>Get ready</h1>
          <p>
            For {location?.label} · outages here could last{" "}
            {formatRange(range.low_hours, range.high_hours)}
          </p>
        </div>
      </header>

      <div className="stack">
        <section className="card">
          <div className="prep__top">
            <span className="eyebrow">Prepare</span>
            <span className="prep__count">
              {doneCount} of {actions.length} done
            </span>
          </div>
          <ul className="prep__list">
            {actions.map((label) => {
              const isDone = done.includes(label);
              return (
                <li key={label}>
                  <button
                    type="button"
                    role="checkbox"
                    aria-checked={isDone}
                    className={`prep__item${isDone ? " is-done" : ""}`}
                    onClick={() => toggle(label)}
                  >
                    <span className="prep__box" aria-hidden>
                      {isDone && (
                        <svg width="12" height="12" viewBox="0 0 24 24" fill="none"
                          stroke="currentColor" strokeWidth="3.5" strokeLinecap="round"
                          strokeLinejoin="round">
                          <path d="m5 12.5 4.5 4.5L19 7.5" />
                        </svg>
                      )}
                    </span>
                    <span className="prep__label">{label}</span>
                  </button>
                </li>
              );
            })}
          </ul>
        </section>

        {/* Downed line: safety guidance only. There is no reporting backend (demo). */}
        <section className="card downed">
          <button
            type="button"
            className="downed__button"
            aria-expanded={reporting}
            onClick={() => setReporting((v) => !v)}
          >
            <span aria-hidden>⚠</span> Report a downed line
          </button>
          {reporting && (
            <div className="downed__body" role="alert">
              <p>
                <strong>Stay at least 30 feet away.</strong> Assume every downed line is live, even if
                it isn't sparking. Keep kids and pets away.
              </p>
              <p>If anyone is hurt or there is a fire, call 911.</p>
              <p>Otherwise, call your utility's outage line to report where the line is.</p>
              <p className="faint">Demo only: this button doesn't send a report.</p>
            </div>
          )}
        </section>

        <section className="card">
          <span className="eyebrow">Stay safe</span>
          <ul className="safety-list">
            {safety_messages.map((m) => (
              <li key={m}>{m}</li>
            ))}
          </ul>
        </section>
      </div>
    </div>
  );
}
