import { RANGE_MI, colorFor, hourAt, rainRateAt, type RadarHour } from "./field";

const GRID = 150; // rain field resolution; scaled up and smoothed when drawn

type Cells = Parameters<typeof rainRateAt>[0];

export function drawRadar(
  ctx: CanvasRenderingContext2D,
  field: HTMLCanvasElement,
  size: number,
  opts: { cells: Cells; hours: RadarHour[]; t: number; layer: "rain" | "wind"; tap: { x: number; y: number } | null; sweep: number | null },
) {
  const { cells, hours, t, layer, tap, sweep } = opts;
  const px = (m: number) => (m / (2 * RANGE_MI) + 0.5) * size;

  // Background
  const bg = ctx.createRadialGradient(size / 2, size / 2, 0, size / 2, size / 2, size * 0.75);
  bg.addColorStop(0, "#182433");
  bg.addColorStop(1, "#0b121b");
  ctx.fillStyle = bg;
  ctx.fillRect(0, 0, size, size);

  // Faint grid every 10 miles
  ctx.strokeStyle = "rgba(255,255,255,0.045)";
  ctx.lineWidth = 1;
  for (let m = -RANGE_MI; m <= RANGE_MI; m += 10) {
    ctx.beginPath();
    ctx.moveTo(px(m), 0); ctx.lineTo(px(m), size);
    ctx.moveTo(0, px(m)); ctx.lineTo(size, px(m));
    ctx.stroke();
  }

  if (layer === "rain") {
    const fctx = field.getContext("2d");
    if (fctx) {
      const img = fctx.createImageData(GRID, GRID);
      for (let j = 0; j < GRID; j++) {
        for (let i = 0; i < GRID; i++) {
          const x = ((i + 0.5) / GRID - 0.5) * 2 * RANGE_MI;
          const y = (0.5 - (j + 0.5) / GRID) * 2 * RANGE_MI;
          const [r, g, b, a] = colorFor(rainRateAt(cells, hours, t, x, y));
          const o = (j * GRID + i) * 4;
          img.data[o] = r; img.data[o + 1] = g; img.data[o + 2] = b; img.data[o + 3] = a;
        }
      }
      fctx.putImageData(img, 0, 0);
      ctx.imageSmoothingEnabled = true;
      ctx.imageSmoothingQuality = "high";
      ctx.filter = "blur(2px)";
      ctx.drawImage(field, 0, 0, size, size);
      ctx.filter = "none";
    }
  } else {
    drawWind(ctx, size, hourAt(hours, t), t);
  }

  // Range rings and compass
  ctx.strokeStyle = "rgba(255,255,255,0.14)";
  ctx.fillStyle = "rgba(255,255,255,0.45)";
  ctx.font = `${size * 0.026}px Inter, sans-serif`;
  for (const m of [10, 20, 30, 40]) {
    ctx.beginPath();
    ctx.arc(size / 2, size / 2, px(m) - size / 2, 0, Math.PI * 2);
    ctx.stroke();
    ctx.fillText(`${m} mi`, size / 2 + 4, size / 2 - (px(m) - size / 2) + size * 0.03);
  }
  ctx.font = `600 ${size * 0.03}px Inter, sans-serif`;
  ctx.textAlign = "center";
  ctx.fillText("N", size / 2, size * 0.035);
  ctx.fillText("S", size / 2, size * 0.985);
  ctx.fillText("E", size * 0.975, size / 2 + 4);
  ctx.fillText("W", size * 0.025, size / 2 + 4);
  ctx.textAlign = "start";

  // Sweep (a faint rotating wedge)
  if (sweep !== null && "createConicGradient" in ctx) {
    const g = ctx.createConicGradient(sweep, size / 2, size / 2);
    g.addColorStop(0, "rgba(140,200,255,0.0)");
    g.addColorStop(0.9, "rgba(140,200,255,0.0)");
    g.addColorStop(1, "rgba(140,200,255,0.10)");
    ctx.fillStyle = g;
    ctx.beginPath();
    ctx.arc(size / 2, size / 2, size / 2, 0, Math.PI * 2);
    ctx.fill();
  }

  // You
  ctx.fillStyle = "#7aa7e8";
  ctx.beginPath();
  ctx.arc(size / 2, size / 2, size * 0.012, 0, Math.PI * 2);
  ctx.fill();
  ctx.strokeStyle = "rgba(122,167,232,0.5)";
  ctx.beginPath();
  ctx.arc(size / 2, size / 2, size * 0.026, 0, Math.PI * 2);
  ctx.stroke();

  // Tapped spot
  if (tap) {
    ctx.strokeStyle = "#fff";
    ctx.lineWidth = 2;
    ctx.beginPath();
    ctx.arc(px(tap.x), size - px(tap.y), size * 0.02, 0, Math.PI * 2);
    ctx.stroke();
    ctx.lineWidth = 1;
  }
}

function drawWind(ctx: CanvasRenderingContext2D, size: number, h: { gust: number; from: number }, t: number) {
  const n = 7;
  const toward = ((h.from + 180) * Math.PI) / 180;
  for (let j = 0; j < n; j++) {
    for (let i = 0; i < n; i++) {
      const cx = ((i + 0.5) / n) * size;
      const cy = ((j + 0.5) / n) * size;
      const wobble = 0.85 + 0.3 * Math.sin(t * 1.3 + i * 1.7 + j * 2.1);
      const len = Math.min(1, (h.gust * wobble) / 60 + 0.15) * (size / n) * 0.8;
      const dx = Math.sin(toward) * len;
      const dy = -Math.cos(toward) * len;
      ctx.strokeStyle = h.gust < 20 ? "rgba(150,185,230,0.7)" : h.gust < 35 ? "rgba(230,196,109,0.85)" : "rgba(227,154,100,0.95)";
      ctx.lineWidth = 2;
      ctx.beginPath();
      ctx.moveTo(cx - dx / 2, cy - dy / 2);
      ctx.lineTo(cx + dx / 2, cy + dy / 2);
      const a = Math.atan2(dy, dx);
      ctx.lineTo(cx + dx / 2 - 6 * Math.cos(a - 0.5), cy + dy / 2 - 6 * Math.sin(a - 0.5));
      ctx.moveTo(cx + dx / 2, cy + dy / 2);
      ctx.lineTo(cx + dx / 2 - 6 * Math.cos(a + 0.5), cy + dy / 2 - 6 * Math.sin(a + 0.5));
      ctx.stroke();
    }
  }
}
