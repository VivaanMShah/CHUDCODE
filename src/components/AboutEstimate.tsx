import type { Meta, WeatherResponse } from "../api/contract";

// The "About this estimate" panel: what the numbers are, where they come from, and their limits.
export default function AboutEstimate({ meta, live }: { meta: Meta; live: WeatherResponse | null }) {
  const bt = meta.backtest;
  return (
    <div className="about">
      <p className="about__label">Model estimate, not confirmed by AEP.</p>

      {bt && (
        <>
          <h3>How well does the outage model work?</h3>
          <p>
            We trained on storms before the {bt.event_label} and tested on it. Across {bt.areas} areas
            it scored {bt.auc.toFixed(2)} AUC (1.0 is perfect, 0.5 is a coin flip).
            {bt.high_band_hit_rate != null &&
              ` Of ${bt.high_band_areas} areas it rated high risk, ${Math.round(bt.high_band_hit_rate * 100)}% had damage.`}
            {bt.low_band_hit_rate != null &&
              ` Of ${bt.low_band_areas} rated low, ${Math.round(bt.low_band_hit_rate * 100)}% did.`}
          </p>
        </>
      )}

      {live?.model && (
        <>
          <h3>How is storm strength set?</h3>
          <p>
            A small model reads the wind and rain forecast and estimates the chance of a damaging day.
            It learned from {live.model.days.toLocaleString()} days, {live.model.damage_days} of them with
            damage. Tested on days it had never seen, it scored {live.model.auc_out_of_time.toFixed(2)} AUC.
            That is a modest signal, so treat it as a guide.
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
      <h3>How we handle your data</h3>
      <ul className="checklist">
        <li>We only ask for a ZIP code. It stays on your device.</li>
        <li>Every number is labeled as an estimate or a confirmed utility record.</li>
        <li>Confidence is shown, with where it comes from.</li>
        <li>When we can't predict reliably, we say so instead of guessing.</li>
        <li>No coordinates, names, addresses, or employee info leave the server.</li>
        <li>Hospitals and emergency services are only a yes/no flag.</li>
        <li>Your needs are only what you tell us. We never infer them.</li>
        <li>AI never writes numbers. Every number in a message is checked against the data.</li>
        <li>Requests are logged without personal data, and ZIPs are shortened to 3 digits.</li>
      </ul>
      <p className="faint mono about__id">Request {meta.request_id}</p>
    </div>
  );
}
