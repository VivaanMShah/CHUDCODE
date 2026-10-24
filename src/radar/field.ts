// Radar math: a rain field built from the hourly forecast. No coordinates anywhere.
// Distances are miles east (x) and north (y) of the ZIP center.

export interface RadarHour {
  label: string;
  precip_in: number;
  gust_mph: number;
  wind_from_deg: number;
}

export const RANGE_MI = 40;

// Same shapes every frame: a seeded random generator, not Math.random.
function rng(seed: number) {
  let a = seed >>> 0;
  return () => {
    a = (a + 0x6d2b79f5) >>> 0;
    let t = Math.imul(a ^ (a >>> 15), 1 | a);
    t = (t + Math.imul(t ^ (t >>> 7), 61 | t)) ^ t;
    return ((t ^ (t >>> 14)) >>> 0) / 4294967296;
  };
}

interface Blob { dx: number; dy: number; r: number; w: number }
interface Cell { ox: number; oy: number; blobs: Blob[] }

export function makeCells(seed: number): Cell[] {
  const r = rng(seed);
  return Array.from({ length: 7 }, (_, i) => {
    const big = i === 0;
    const spread = big ? 16 : 10;
    return {
      ox: big ? 0 : (r() - 0.5) * 44,
      oy: big ? 0 : (r() - 0.5) * 44,
      blobs: Array.from({ length: big ? 9 : 6 }, () => ({
        dx: (r() - 0.5) * spread,
        dy: (r() - 0.5) * spread,
        r: (big ? 5 : 3) + r() * (big ? 5 : 3),
        w: 0.5 + r() * 0.7,
      })),
    };
  });
}

export function seedFrom(text: string | undefined): number {
  let h = 7;
  for (const c of text ?? "") h = (h * 31 + c.charCodeAt(0)) >>> 0;
  return h;
}

// A forecast for the simulator: a storm that peaks about 4 hours from now.
export function synthHours(severity: number): RadarHour[] {
  const peak = [0, 0.04, 0.15, 0.4, 0.9][severity - 1] ?? 0;
  return Array.from({ length: 13 }, (_, h) => ({
    label: h === 0 ? "Now" : `+${h}h`,
    precip_in: +(peak * Math.exp(-(((h - 4) / 2.2) ** 2))).toFixed(3),
    gust_mph: Math.round(10 + severity * 9 + 6 * Math.exp(-(((h - 4) / 2.5) ** 2))),
    wind_from_deg: 225, // from the southwest, so the storm moves northeast
  }));
}

const lerp = (a: number, b: number, f: number) => a + (b - a) * f;

export function hourAt(hours: RadarHour[], t: number) {
  const i = Math.min(Math.floor(t), hours.length - 1);
  const j = Math.min(i + 1, hours.length - 1);
  const f = t - i;
  return {
    precip: lerp(hours[i].precip_in, hours[j].precip_in, f),
    gust: lerp(hours[i].gust_mph, hours[j].gust_mph, f),
    from: hours[i].wind_from_deg,
    label: hours[Math.round(t) < hours.length ? Math.round(t) : hours.length - 1].label,
  };
}

function peakHour(hours: RadarHour[]): number {
  let k = 0;
  hours.forEach((h, i) => {
    if (h.precip_in > hours[k].precip_in) k = i;
  });
  return k;
}

function density(cells: Cell[], shiftX: number, shiftY: number, x: number, y: number): number {
  let d = 0;
  for (const c of cells) {
    for (const b of c.blobs) {
      const dx = x - (c.ox + b.dx + shiftX);
      const dy = y - (c.oy + b.dy + shiftY);
      const lim = b.r * 3;
      if (Math.abs(dx) > lim || Math.abs(dy) > lim) continue;
      d += b.w * Math.exp(-(dx * dx + dy * dy) / (2 * b.r * b.r));
    }
  }
  return d;
}

// Rain rate (inches per hour) at a spot and time. Storm cells drift downwind and pass over
// the center at the forecast's wettest hour.
export function rainRateAt(cells: Cell[], hours: RadarHour[], t: number, x: number, y: number): number {
  const k = peakHour(hours);
  const peak = hours[k].precip_in;
  if (peak < 0.005) return 0;
  const speed = Math.max(8, hours[k].gust_mph * 0.45); // miles per hour
  const bearing = ((hours[k].wind_from_deg + 180) * Math.PI) / 180;
  const shift = speed * (t - k);
  const sx = Math.sin(bearing) * shift;
  const sy = Math.cos(bearing) * shift;
  const d0 = density(cells, 0, 0, 0, 0) || 1; // so the center gets the forecast rate at the peak hour
  const fade = 0.35 + 0.65 * (hourAt(hours, t).precip / peak);
  return (peak * density(cells, sx, sy, x, y) * fade) / d0;
}

// Standard radar-style colors: green, yellow, orange, red, magenta.
const STOPS: [number, number, number, number, number][] = [
  [0.004, 90, 190, 130, 0],
  [0.01, 90, 190, 130, 0.45],
  [0.03, 60, 175, 105, 0.7],
  [0.07, 40, 150, 80, 0.8],
  [0.12, 235, 215, 70, 0.85],
  [0.25, 240, 160, 50, 0.9],
  [0.5, 225, 70, 60, 0.92],
  [0.9, 200, 60, 190, 0.95],
];

export function colorFor(rate: number): [number, number, number, number] {
  if (rate <= STOPS[0][0]) return [0, 0, 0, 0];
  for (let i = 1; i < STOPS.length; i++) {
    if (rate <= STOPS[i][0]) {
      const a = STOPS[i - 1];
      const b = STOPS[i];
      const f = (rate - a[0]) / (b[0] - a[0]);
      return [lerp(a[1], b[1], f), lerp(a[2], b[2], f), lerp(a[3], b[3], f), lerp(a[4], b[4], f) * 255];
    }
  }
  const s = STOPS[STOPS.length - 1];
  return [s[1], s[2], s[3], s[4] * 255];
}

export function describeRate(rate: number): string {
  if (rate < 0.01) return "dry";
  if (rate < 0.05) return "light rain";
  if (rate < 0.15) return "moderate rain";
  if (rate < 0.4) return "heavy rain";
  return "very heavy rain";
}

const COMPASS = ["N", "NE", "E", "SE", "S", "SW", "W", "NW"];
export const compass = (deg: number) => COMPASS[Math.round(((deg % 360) + 360) % 360 / 45) % 8];
