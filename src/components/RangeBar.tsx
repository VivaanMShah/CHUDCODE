import type { HistogramBin, RestorationRange } from "../api/contract";
import { formatRange } from "../assessment";

// A horizontal range bar with a small histogram of simulated outcomes above it.
export default function RangeBar({ range, bins }: { range: RestorationRange; bins: HistogramBin[] }) {
  if (bins.length === 0) return null;
  const min = bins[0].low_hours;
  const max = bins[bins.length - 1].high_hours;
  const span = Math.max(max - min, 1e-6);
  const pos = (h: number) => Math.min(100, Math.max(0, ((h - min) / span) * 100));
  const peak = Math.max(...bins.map((b) => b.share), 1e-6);
  const days = max >= 48;
  const edge = (h: number) => {
    const v = days ? h / 24 : h;
    return `${v >= 10 ? Math.round(v) : Math.round(v * 10) / 10} ${days ? "d" : "h"}`;
  };

  return (
    <div
      className="rangebar"
      role="img"
      aria-label={`Simulated outcomes run from ${formatRange(min, max)}. Eight in ten fall between ${formatRange(range.low_hours, range.high_hours)}.`}
    >
      <div className="rangebar__hist">
        {bins.map((b, i) => (
          <span
            key={i}
            className={`rangebar__col${b.high_hours > range.low_hours && b.low_hours < range.high_hours ? " is-inside" : ""}`}
            style={{ height: `${Math.max(6, (b.share / peak) * 100)}%` }}
          />
        ))}
      </div>
      <div className="rangebar__track">
        <span
          className="rangebar__fill"
          style={{ left: `${pos(range.low_hours)}%`, width: `${pos(range.high_hours) - pos(range.low_hours)}%` }}
        />
        <span className="rangebar__median" style={{ left: `${pos(range.median_hours)}%` }} />
      </div>
      <div className="rangebar__scale mono">
        <span>{edge(min)}</span>
        <span>{edge(max)}</span>
      </div>
    </div>
  );
}
