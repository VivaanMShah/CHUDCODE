import { useEffect, useState } from "react";
import { fetchAlerts, savePushSettings } from "api/placeholders";
import type { AlertsData, WeatherAlert } from "../types";
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

export default function Alerts() {
  const [data, setData] = useState<AlertsData | null>(null);
  const [error, setError] = useState(false);
  const [openId, setOpenId] = useState<string | null>(null);
  const [pushEnabled, setPushEnabled] = useState(false);

  useEffect(() => {
    let cancelled = false;
    fetchAlerts()
      .then((d) => {
        if (cancelled) return;
        setData(d);
        setPushEnabled(d.pushEnabled);
        // Start with the most severe (first) alert expanded.
        setOpenId(d.alerts[0]?.id ?? null);
      })
      .catch(() => !cancelled && setError(true));
    return () => {
      cancelled = true;
    };
  }, []);

  function handlePushToggle() {
    if (!data) return;
    const next = !pushEnabled;
    setPushEnabled(next); // optimistic update
    savePushSettings(next, data.pushThresholdPercent).catch(() => {
      setPushEnabled(!next); // roll back if the save fails
    });
  }

  if (error) {
    return (
      <div className="screen">
        <p className="dim">Couldn't load alerts. Try again in a moment.</p>
      </div>
    );
  }

  if (!data) {
    return (
      <div className="screen">
        <p className="dim">Loading alerts…</p>
      </div>
    );
  }

  return (
    <div className="screen alerts">
      <header className="screen-header">
        <div>
          <h1>Alerts</h1>
          <p>
            {data.alerts.length} active for {data.locationName}
          </p>
        </div>
      </header>

      <div className="stack">
        {data.alerts.length === 0 && (
          <div className="card">
            <p className="dim">No active alerts for your area right now.</p>
          </div>
        )}

        {data.alerts.map((alert) => (
          <AlertCard
            key={alert.id}
            alert={alert}
            open={openId === alert.id}
            onToggle={() => setOpenId(openId === alert.id ? null : alert.id)}
          />
        ))}

        {/* Push notifications */}
        <section className="card push">
          <div className="push__text">
            <strong>Push notifications</strong>
            <span>Notify me when storm risk passes {data.pushThresholdPercent}%</span>
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