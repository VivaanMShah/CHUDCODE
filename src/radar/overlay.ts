import { RANGE_MI, colorFor, hourAt, rainRateAt, type RadarHour } from "./field";

const GRID = 150; // rain field resolution; the browser smooths it when it is stretched over the map
const SIZE = 300; // overlay image size in pixels

type Cells = Parameters<typeof rainRateAt>[0];

export interface OverlayState {
  cells: Cells;
  hours: RadarHour[];
  t: number;
  layer: "rain" | "wind";
}

// Draws the rain (or wind) layer on a transparent canvas covering 40 miles in every direction of the
// ZIP center. The map places this picture over its real geography.
export function renderOverlay(canvas: HTMLCanvasElement, field: HTMLCanvasElement, st: OverlayState): string {
  canvas.width = canvas.height = SIZE;
  const ctx = canvas.getContext("2d");
  const fctx = field.getContext("2d");
  if (!ctx || !fctx) return "";
  ctx.clearRect(0, 0, SIZE, SIZE);

  if (st.layer === "rain") {
    field.width = field.height = GRID;
    const img = fctx.createImageData(GRID, GRID);
    for (let j = 0; j < GRID; j++) {
      for (let i = 0; i < GRID; i++) {
        const x = ((i + 0.5) / GRID - 0.5) * 2 * RANGE_MI;
        const y = (0.5 - (j + 0.5) / GRID) * 2 * RANGE_MI;
        const [r, g, b, a] = colorFor(rainRateAt(st.cells, st.hours, st.t, x, y));
        const o = (j * GRID + i) * 4;
        img.data[o] = r;
        img.data[o + 1] = g;
        img.data[o + 2] = b;
        img.data[o + 3] = a;
      }
    }
    fctx.putImageData(img, 0, 0);
    ctx.imageSmoothingEnabled = true;
    ctx.imageSmoothingQuality = "high";
    ctx.drawImage(field, 0, 0, SIZE, SIZE);
  } else {
    drawWind(ctx, hourAt(st.hours, st.t), st.t);
  }
  return canvas.toDataURL("image/png");
}

function drawWind(ctx: CanvasRenderingContext2D, h: { gust: number; from: number }, t: number) {
  const n = 7;
  const toward = ((h.from + 180) * Math.PI) / 180;
  for (let j = 0; j < n; j++) {
    for (let i = 0; i < n; i++) {
      const cx = ((i + 0.5) / n) * SIZE;
      const cy = ((j + 0.5) / n) * SIZE;
      const wobble = 0.85 + 0.3 * Math.sin(t * 1.3 + i * 1.7 + j * 2.1);
      const len = Math.min(1, (h.gust * wobble) / 60 + 0.15) * (SIZE / n) * 0.8;
      const dx = Math.sin(toward) * len;
      const dy = -Math.cos(toward) * len;
      ctx.strokeStyle = h.gust < 20 ? "rgba(170,205,250,0.95)" : h.gust < 35 ? "rgba(240,205,110,0.95)" : "rgba(235,150,95,0.98)";
      ctx.lineWidth = 2.5;
      ctx.lineCap = "round";
      ctx.beginPath();
      ctx.moveTo(cx - dx / 2, cy - dy / 2);
      ctx.lineTo(cx + dx / 2, cy + dy / 2);
      const a = Math.atan2(dy, dx);
      ctx.lineTo(cx + dx / 2 - 7 * Math.cos(a - 0.5), cy + dy / 2 - 7 * Math.sin(a - 0.5));
      ctx.moveTo(cx + dx / 2, cy + dy / 2);
      ctx.lineTo(cx + dx / 2 - 7 * Math.cos(a + 0.5), cy + dy / 2 - 7 * Math.sin(a + 0.5));
      ctx.stroke();
    }
  }
}
