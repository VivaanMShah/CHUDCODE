import type { ModelEstimate, Scenario } from "../api/contract";

// "Will I outlast it?": the chance power is back by each milestone, plus one-tap what-ifs
// (more crews, roads cleared) that re-run the estimate. Both come from the same simulation as the range.
export default function OutlastCard({ est, onTry }: { est: ModelEstimate; onTry: (patch: Partial<Scenario>) => void }) {
  const odds = est.restoration_odds ?? [];
  const tries = (est.factor_deltas ?? []).filter((d) => d.scenario_patch).slice(0, 4);
  if (odds.length === 0 && tries.length === 0) return null;

  return (
    <section className="card outlast" aria-label="Will I outlast it?">
      <span className="eyebrow">Will I outlast it?</span>
      <p className="outlast__sub">Chance your power is back by…</p>
      <ul className="outlast__list">
        {odds.map((o) => {
          const pct = Math.round(o.chance * 100);
          return (
            <li key={o.hours} className="outlast__row">
              <span className="outlast__when">{o.hours < 24 ? `${o.hours} hours` : `${o.hours / 24} day${o.hours === 24 ? "" : "s"}`}</span>
              <span className="outlast__bar" aria-hidden><span style={{ width: `${pct}%` }} /></span>
              <strong className="outlast__pct">{pct}%</strong>
              <span className="outlast__label">{o.label}</span>
            </li>
          );
        })}
      </ul>

      {tries.length > 0 && (
        <>
          <p className="outlast__sub">What would change this? Tap to try it.</p>
          <div className="outlast__tries">
            {tries.map((d) => (
              <button key={d.change} type="button" className="outlast__try" onClick={() => onTry(d.scenario_patch!)}>
                <span>{d.change[0].toUpperCase() + d.change.slice(1)}</span>
                <small>{d.delta_text}</small>
              </button>
            ))}
          </div>
        </>
      )}
      <p className="outlast__note">Estimate from a simulation, not a promise. Food times follow USDA guidance.</p>
    </section>
  );
}
