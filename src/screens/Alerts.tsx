import { useEffect, useState } from "react";
import { getMessage } from "../api/backend";
import type { Band, MessageResponse, MessageType } from "../api/contract";
import { CHANNEL_LABEL, bandLabel, useAssessment } from "../assessment";
import { useLocation } from "../location";
import { useProfile } from "../profile";
import type { AlertSeverity, WeatherAlert } from "../types";
import "./Alerts.css";

function Chevron({ open }: { open: boolean }) {
  return (
    <svg
      className={`alert__chevron${open ? " is-open" : ""}`}
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
      <path d="m6 9 6 6 6-6" />
    </svg>
  );
}

interface AlertCardProps {
  alert: WeatherAlert;
  open: boolean;
  onToggle: () => void;
}

function AlertCard({ alert, open, onToggle }: AlertCardProps) {
  const panelId = `alert-panel-${alert.id}`;
  return (
    <article className={`alert alert--${alert.severity}${open ? " is-open" : ""}`}>
      <button
        type="button"
        className="alert__head"
        onClick={onToggle}
        aria-expanded={open}
        aria-controls={panelId}
      >
        <div className="alert__meta">
          <span className={`badge badge--${alert.severity}`}>{alert.severity}</span>
          <span className="alert__until mono">{alert.untilLabel}</span>
        </div>
        <div className="alert__title-row">
          <h2 className="alert__title">{alert.title}</h2>
          <Chevron open={open} />
        </div>
      </button>

      {open && (
        <div className="alert__body" id={panelId}>
          <p className="alert__desc">{alert.description}</p>
          <div className="alert__todo">
            <span className="alert__todo-label">What to do</span>
            <p>{alert.whatToDo}</p>
          </div>
        </div>
      )}
    </article>
  );
}

const SEVERITY: Record<Band, AlertSeverity> = {
  high: "warning",
  medium: "watch",
  low: "advisory",
};

const STAGES: { id: MessageType; label: string; hint: string }[] = [
  { id: "pre_storm", label: "Before the storm", hint: "Get ready" },
  { id: "outage_confirmed", label: "Outage confirmed", hint: "Power is out" },
  { id: "estimate_updated", label: "Estimate updated", hint: "Repairs underway" },
  { id: "restoration_complete", label: "Power restored", hint: "All clear" },
];

const PUSH_KEY = "storm-watch:push";

function loadPush(): boolean {
  try {
    return localStorage.getItem(PUSH_KEY) !== "off";
  } catch {
    return true;
  }
}

export default function Alerts() {
  const { location } = useLocation();
  const { scenario, data, error, live } = useAssessment();
  const { profile } = useProfile();
  const [open, setOpen] = useState(true);
  const [pushEnabled, setPushEnabled] = useState(loadPush);

  // Customer message preview (POST /message)
  const [msgType, setMsgType] = useState<MessageType>("pre_storm");
  const [msg, setMsg] = useState<MessageResponse | null>(null);
  const [msgError, setMsgError] = useState(false);
  const [preStorm, setPreStorm] = useState<string | null>(null);

  const locationId = location?.locationId;

  useEffect(() => {
    if (!locationId) return;
    const ctrl = new AbortController();
    setMsgError(false);
    const timer = setTimeout(() => {
      getMessage(locationId, msgType, scenario, ctrl.signal)
        .then((m) => {
          setMsg(m);
          if (m.message_type === "pre_storm") setPreStorm(m.text);
        })
        .catch(() => !ctrl.signal.aborted && setMsgError(true));
    }, 150);
    return () => {
      clearTimeout(timer);
      ctrl.abort();
    };
  }, [locationId, msgType, scenario]);

  function handlePushToggle() {
    const next = !pushEnabled;
    setPushEnabled(next);
    try {
      localStorage.setItem(PUSH_KEY, next ? "on" : "off");
    } catch {
      /* not critical */
    }
  }

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
        <p className="dim">{location ? "Loading alerts…" : "Pick an area on Home first."}</p>
      </div>
    );
  }

  const est = data.model_estimate;
  const acts = data.customer_actions;
  const alert: WeatherAlert | null = est.alert_eligible
    ? {
        id: "outage",
        severity: SEVERITY[est.risk_band],
        title: `${bandLabel(est.risk_band)} outage risk in your area`,
        untilLabel: acts.send_timing,
        description: preStorm ?? est.reasons.map((r) => r.text).join(" "),
        whatToDo: acts.actions.slice(0, 3).join(" "),
      }
    : null;

  return (
    <div className="screen alerts">
      <header className="screen-header">
        <div>
          <h1>Alerts</h1>
          <p>
            {alert ? "Outage alert on" : "No outage alert"} for {location?.label}
          </p>
        </div>
      </header>

      <div className="stack">
        {/* Real weather alerts from the National Weather Service */}
        <section className="card" aria-label="Weather alerts">
          <span className="eyebrow">Weather alerts · National Weather Service</span>
          {live?.alerts == null ? (
            <p className="dim nws__empty">{live ? "We couldn't check weather alerts right now." : "Checking…"}</p>
          ) : live.alerts.length === 0 ? (
            <p className="dim nws__empty">No active weather alerts for your area.</p>
          ) : (
            <ul className="nws">
              {live.alerts.map((a) => (
                <li key={a.headline}>
                  <strong>{a.event}</strong>
                  <span>{a.headline}</span>
                  {a.instruction && <small>{a.instruction}</small>}
                </li>
              ))}
            </ul>
          )}
        </section>

        {alert ? (
          <AlertCard alert={alert} open={open} onToggle={() => setOpen((v) => !v)} />
        ) : (
          <div className="card">
            <p className="dim">No outage alert for this scenario.</p>
          </div>
        )}

        {/* Why: the rule that decided, and how we'd reach the customer */}
        <section className="card">
          <span className="eyebrow">Why this decision</span>
          <ul className="alert-facts">
            <li>
              <span>Rule</span>
              <strong>{est.alert_rule}</strong>
            </li>
            <li>
              <span>We'd reach you by</span>
              <strong>{CHANNEL_LABEL[acts.channel] ?? acts.channel}</strong>
            </li>
            {profile.contact && (
              <li>
                <span>You asked us to contact you by</span>
                <strong>
                  {CHANNEL_LABEL[profile.contact]}
                  {profile.needs.includes("medical_equipment") && profile.contact !== "phone_call"
                    ? " (we would still call because you use medical equipment)"
                    : ""}
                </strong>
              </li>
            )}
            <li>
              <span>When</span>
              <strong>{acts.send_timing}</strong>
            </li>
            <li>
              <span>If things get worse</span>
              <strong>
                {acts.escalation.trigger}: {acts.escalation.action.toLowerCase()}
              </strong>
            </li>
          </ul>
        </section>

        {/* Storm timeline: the whole customer journey, one stage at a time */}
        <section className="card">
          <span className="eyebrow">Storm timeline · tap a stage</span>
          <ol className="timeline">
            {STAGES.map((st, i) => {
              const on = msgType === st.id;
              return (
                <li key={st.id} className={`timeline__stage${on ? " is-on" : ""}`}>
                  <button
                    type="button"
                    className="timeline__head"
                    aria-expanded={on}
                    onClick={() => setMsgType(st.id)}
                  >
                    <span className="timeline__dot" aria-hidden>{i + 1}</span>
                    <span className="timeline__label">
                      <strong>{st.label}</strong>
                      <small>{st.hint}</small>
                    </span>
                  </button>
                  {on && (
                    <div className="timeline__body">
                      {msgError ? (
                        <p className="dim">Couldn't load this message.</p>
                      ) : msg && msg.message_type === st.id ? (
                        <div className="notif" aria-label="Message preview">
                          <div className="notif__top">
                            <span className="notif__app">Outage Concierge</span>
                            <span className="notif__channel">
                              {CHANNEL_LABEL[msg.channel] ?? msg.channel}
                            </span>
                          </div>
                          <p className="notif__text">{msg.text}</p>
                          <div className="notif__foot">
                            <span>{msg.send_timing}</span>
                            <span className={`notif__check${msg.validated ? " is-ok" : ""}`}>
                              {msg.validated ? "✓ Verified numbers" : "Numbers not verified"}
                            </span>
                          </div>
                        </div>
                      ) : (
                        <p className="dim">Loading…</p>
                      )}
                    </div>
                  )}
                </li>
              );
            })}
          </ol>
        </section>

        {/* Push notifications (saved on this device only) */}
        <section className="card push">
          <div className="push__text">
            <strong>Push notifications</strong>
            <span>Notify me when outage risk is medium or high</span>
          </div>
          <button
            type="button"
            role="switch"
            aria-checked={pushEnabled}
            aria-label="Push notifications"
            className={`switch${pushEnabled ? " is-on" : ""}`}
            onClick={handlePushToggle}
          >
            <span className="switch__thumb" />
          </button>
        </section>
      </div>
    </div>
  );
}
