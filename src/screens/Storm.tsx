import { useEffect, useState } from "react";
import { fetchStormTrack, savePrepItem } from "api/placeholders";
import type { PrepItem, StormTrack } from "../types";
import "./Storm.css";

export default function Storm() {
  const [data, setData] = useState<StormTrack | null>(null);
  const [error, setError] = useState(false);
  const [prepare, setPrepare] = useState<PrepItem[]>([]);

  useEffect(() => {
    let cancelled = false;
    fetchStormTrack()
      .then((d) => {
        if (cancelled) return;
        setData(d);
        setPrepare(d.prepare);
      })
      .catch(() => !cancelled && setError(true));
    return () => {
      cancelled = true;
    };
  }, []);

  function togglePrep(id: string) {
    const item = prepare.find((p) => p.id === id);
    if (!item) return;
    const done = !item.done;
    // Optimistic update, roll back if the save fails.
    setPrepare((list) => list.map((p) => (p.id === id ? { ...p, done } : p)));
    savePrepItem(id, done).catch(() => {
      setPrepare((list) =>
        list.map((p) => (p.id === id ? { ...p, done: !done } : p)),
      );
    });
  }

  if (error) {
    return (
      <div className="screen">
        <p className="dim">Couldn't load the storm track. Try again in a moment.</p>
      </div>
    );
  }

  if (!data) {
    return (
      <div className="screen">
        <p className="dim">Loading storm track…</p>
      </div>
    );
  }

  const doneCount = prepare.filter((p) => p.done).length;

  return (
    <div className="screen storm">
      <header className="screen-header">
        <div>
          <h1>Storm tracker</h1>
          <p>
            {data.cellName} · {data.cellType}
          </p>
        </div>
      </header>

      <div className="stack">
        {/* Estimated arrival */}
        <section className="card eta">
          <span className="eyebrow">Estimated arrival</span>
          <div className="eta__time">{data.etaLabel}</div>
          <p className="eta__sub">
            {data.etaRelative} · updated {data.updatedAgo}
          </p>

          <div className="eta__stats">
            <div className="eta__stat">
              <span className="eta__stat-value">
                {data.speedMph}
                <small> mph</small>
              </span>
              <span className="eta__stat-label">Moving {data.heading}</span>
            </div>
            <div className="eta__stat">
              <span className="eta__stat-value">
                {data.hailInches.toFixed(1)}
                <small> in</small>
              </span>
              <span className="eta__stat-label">Hail possible</span>
            </div>
            <div className="eta__stat">
              <span className="eta__stat-value">
                {data.peakGustMph}
                <small> mph</small>
              </span>
              <span className="eta__stat-label">Peak gusts</span>
            </div>
          </div>
        </section>

        {/* Projected path */}
        <section className="card">
          <span className="eyebrow">Projected path</span>
          <ol className="path">
            {data.path.map((stop) => (
              <li key={stop.id} className={`path__stop path__stop--${stop.status}`}>
                <span className="path__marker" aria-hidden />
                <span className="path__label">{stop.label}</span>
                <span className="path__time mono">{stop.timeLabel}</span>
              </li>
            ))}
          </ol>
        </section>

        {/* Prepare checklist */}
        <section className="card">
          <div className="prep__top">
            <span className="eyebrow">Prepare</span>
            <span className="prep__count">
              {doneCount} of {prepare.length} done
            </span>
          </div>
          <ul className="prep__list">
            {prepare.map((item) => (
              <li key={item.id}>
                <button
                  type="button"
                  role="checkbox"
                  aria-checked={item.done}
                  className={`prep__item${item.done ? " is-done" : ""}`}
                  onClick={() => togglePrep(item.id)}
                >
                  <span className="prep__box" aria-hidden>
                    {item.done && (
                      <svg
                        width="12"
                        height="12"
                        viewBox="0 0 24 24"
                        fill="none"
                        stroke="currentColor"
                        strokeWidth="3.5"
                        strokeLinecap="round"
                        strokeLinejoin="round"
                      >
                        <path d="m5 12.5 4.5 4.5L19 7.5" />
                      </svg>
                    )}
                  </span>
                  <span className="prep__label">{item.label}</span>
                </button>
              </li>
            ))}
          </ul>
        </section>
      </div>
    </div>
  );
}