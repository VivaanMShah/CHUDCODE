import { useEffect, useState } from "react";
import { getStaging, getWeakSpots } from "../api/backend";
import type { Meta, StagingResponse, WeakSpotsResponse } from "../api/contract";
import StagingMap from "../components/StagingMap";
import { SEVERITY_LABEL } from "../assessment";
import "./Planner.css";

type View = "staging" | "weak";

const DEVICE_LABEL: Record<string, string> = { fuse: "Fuse", recloser: "Recloser", switch: "Switch" };

function useFetch<T>(load: (signal: AbortSignal) => Promise<T>, deps: unknown[]) {
  const [data, setData] = useState<T | null>(null);
  const [error, setError] = useState(false);
  useEffect(() => {
    const ctl = new AbortController();
    setError(false);
    load(ctl.signal).then(setData).catch((e) => { if (e?.name !== "AbortError") setError(true); });
    return () => ctl.abort();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, deps);
  return { data, error };
}

function Notes({ meta }: { meta: Meta }) {
  return (
    <details className="planner__notes">
      <summary>How this is worked out</summary>
      <h3>Assumptions</h3>
      <ul>{meta.assumptions.map((a) => <li key={a}>{a}</li>)}</ul>
      <h3>Limits</h3>
      <ul>{meta.limits.map((l) => <li key={l}>{l}</li>)}</ul>
    </details>
  );
}

function Staging() {
  const [severity, setSeverity] = useState(4);
  const { data, error } = useFetch<StagingResponse>((s) => getStaging(severity, s), [severity]);

  return (
    <>
      <section className="card">
        <span className="eyebrow">Storm strength</span>
        <div className="segmented" role="radiogroup" aria-label="Storm strength">
          {[1, 2, 3, 4, 5].map((n) => (
            <button key={n} type="button" role="radio" aria-checked={severity === n}
              aria-label={`${n}, ${SEVERITY_LABEL[n]}`}
              className={`segmented__item${severity === n ? " is-on" : ""}`} onClick={() => setSeverity(n)}>{n}</button>
          ))}
        </div>
        <small className="dim">{SEVERITY_LABEL[severity]}. Change it to see where crews should wait.</small>
      </section>

      {error && <section className="card"><p className="dim">Could not load the staging plan. Try again.</p></section>}
      {!data && !error && <section className="card"><p className="dim">Planning…</p></section>}

      {data && !data.available && (
        <section className="card fallback"><h2 className="fallback__title">Not available</h2><p className="fallback__reason">{data.headline}</p></section>
      )}

      {data?.available && (
        <>
          <section className="card planner__hero">
            <span className="eyebrow">Before the storm</span>
            <p className="planner__headline">{data.headline}</p>
            {data.travel && (
              <div className="planner__stats">
                <div><strong>~{Math.round(data.expected_total ?? 0).toLocaleString()}</strong><small>customers expected to lose power</small></div>
                <div><strong>{data.travel.depot_minutes} min</strong><small>from one central depot</small></div>
                <div className="planner__arrow" aria-hidden>→</div>
                <div><strong>{data.travel.staged_minutes} min</strong><small>from the nearest zone</small></div>
              </div>
            )}
          </section>

          <section className="card planner__mapcard">
            <StagingMap data={data} />
            <p className="planner__legend"><span className="planner__ramp" aria-hidden /> Less expected damage to more. Lettered pins are suggested staging zones.</p>
          </section>

          <section className="card">
            <span className="eyebrow">Suggested staging zones</span>
            <ul className="planner__zones">
              {data.zones.map((z) => (
                <li key={z.label}>
                  <span className="planner__pin" aria-hidden>{z.label.replace("Zone ", "")}</span>
                  <div>
                    <strong>Stage near {z.anchor_city}</strong>
                    <small>ZIP {z.anchor_zip} · covers {z.zips.length} ZIP{z.zips.length === 1 ? "" : "s"}</small>
                  </div>
                  <span className="planner__share">{Math.round(z.share_of_expected * 100)}%<small>of expected outages</small></span>
                </li>
              ))}
            </ul>
          </section>
          <Notes meta={data.meta} />
        </>
      )}
    </>
  );
}

function WeakSpots() {
  const { data, error } = useFetch<WeakSpotsResponse>((s) => getWeakSpots(s), []);

  if (error) return <section className="card"><p className="dim">Could not load the weak-spot list. Try again.</p></section>;
  if (!data) return <section className="card"><p className="dim">Looking across past storms…</p></section>;
  if (!data.available) {
    return <section className="card fallback"><h2 className="fallback__title">Not available</h2><p className="fallback__reason">{data.headline}</p></section>;
  }
  const biggest = Math.max(...data.top.map((t) => t.share_of_customer_hours), 0.001);

  return (
    <>
      <section className="card planner__hero">
        <span className="eyebrow">Where to harden the grid</span>
        <p className="planner__headline">{data.headline}</p>
        <div className="planner__stats">
          <div><strong>{Math.round((data.top_share ?? 0) * 100)}%</strong><small>of customer-hours lost</small></div>
          <div><strong>{data.top.length} of {data.devices_analyzed}</strong><small>devices with damage</small></div>
        </div>
      </section>

      <section className="card">
        <span className="eyebrow">Worst devices</span>
        <ol className="planner__spots">
          {data.top.map((t) => (
            <li key={t.rank}>
              <div className="planner__spothead">
                <strong>{t.rank}. {DEVICE_LABEL[t.device_type] ?? t.device_type} · {t.area}</strong>
                <span>{Math.round(t.share_of_customer_hours * 100)}%</span>
              </div>
              <span className="planner__bar" aria-hidden><span style={{ width: `${(t.share_of_customer_hours / biggest) * 100}%` }} /></span>
              <small>
                About {t.customers_below_approx.toLocaleString()} customers below it · {t.damage_count} damage report{t.damage_count === 1 ? "" : "s"}
                {" "}in {t.storms} storm{t.storms === 1 ? "" : "s"} · repairs took about {t.avg_repair_hours < 48 ? `${t.avg_repair_hours} hours` : `${Math.round(t.avg_repair_hours / 24)} days`}
              </small>
            </li>
          ))}
        </ol>
      </section>
      <Notes meta={data.meta} />
    </>
  );
}

// Utility planning view: separate from the customer screens. Same model, aimed at crews and planners.
export default function Planner() {
  const [view, setView] = useState<View>("staging");
  return (
    <div className="screen planner">
      <header className="screen-header">
        <h1>Utility planner</h1>
        <p>For crew leads and planners. Shows areas and device types only, never exact locations.</p>
      </header>
      <div className="stack">
        <div className="segmented segmented--two" role="tablist" aria-label="Planner view">
          <button type="button" role="tab" aria-selected={view === "staging"} className={`segmented__item${view === "staging" ? " is-on" : ""}`}
            onClick={() => setView("staging")}>Crew staging</button>
          <button type="button" role="tab" aria-selected={view === "weak"} className={`segmented__item${view === "weak" ? " is-on" : ""}`}
            onClick={() => setView("weak")}>Weak spots</button>
        </div>
        {view === "staging" ? <Staging /> : <WeakSpots />}
      </div>
    </div>
  );
}
