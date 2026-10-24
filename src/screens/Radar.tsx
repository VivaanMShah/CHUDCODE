import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { useAssessment } from "../assessment";
import { SHELTER_FINDER, UTILITY_PHONE_DISPLAY, UTILITY_PHONE_TEL, mapsHospitals, nwsForecast } from "../config";
import { useLocation } from "../location";
import { drawRadar } from "../radar/draw";
import { RANGE_MI, compass, describeRate, hourAt, makeCells, rainRateAt, seedFrom, synthHours } from "../radar/field";
import "./Radar.css";

const SIZE = 480;
const SECONDS_PER_LOOP = 14;

export default function Radar() {
  const { location } = useLocation();
  const { live, useLive, scenario } = useAssessment();
  const zip = location?.zip ?? "";

  // Real hourly forecast in live mode; a made-up storm when simulating.
  const hours = useMemo(
    () => (useLive && live && live.hourly.length >= 6
      ? live.hourly.map((h) => ({ label: h.label, precip_in: h.precip_in, gust_mph: h.gust_mph, wind_from_deg: h.wind_from_deg }))
      : synthHours(scenario.storm_severity)),
    [useLive, live, scenario.storm_severity],
  );
  const cells = useMemo(() => makeCells(seedFrom(zip)), [zip]);
  const maxT = hours.length - 1;

  const reduceMotion = useMemo(() => window.matchMedia?.("(prefers-reduced-motion: reduce)").matches ?? false, []);
  const [playing, setPlaying] = useState(!reduceMotion);
  const [layer, setLayer] = useState<"rain" | "wind">("rain");
  const [t, setT] = useState(0);
  const [tap, setTap] = useState<{ x: number; y: number } | null>(null);

  const canvasRef = useRef<HTMLCanvasElement>(null);
  const fieldRef = useRef<HTMLCanvasElement | null>(null);
  const tRef = useRef(0);
  const settings = useRef({ cells, hours, layer, tap, playing });
  settings.current = { cells, hours, layer, tap, playing };

  const draw = useCallback((now: number) => {
    const ctx = canvasRef.current?.getContext("2d");
    if (!ctx) return;
    if (!fieldRef.current) {
      fieldRef.current = document.createElement("canvas");
      fieldRef.current.width = fieldRef.current.height = 150;
    }
    const s = settings.current;
    drawRadar(ctx, fieldRef.current, SIZE, {
      cells: s.cells, hours: s.hours, t: tRef.current, layer: s.layer, tap: s.tap,
      sweep: s.playing ? ((now / 5000) * Math.PI * 2) % (Math.PI * 2) : null,
    });
  }, []);

  // Animation loop (only while playing)
  useEffect(() => {
    if (!playing) return;
    let raf = 0;
    let last = performance.now();
    let lastState = 0;
    const step = (now: number) => {
      tRef.current += ((now - last) / 1000) * (maxT / SECONDS_PER_LOOP);
      if (tRef.current > maxT) tRef.current = 0;
      last = now;
      draw(now);
      if (now - lastState > 120) {
        setT(tRef.current);
        lastState = now;
      }
      raf = requestAnimationFrame(step);
    };
    raf = requestAnimationFrame(step);
    return () => cancelAnimationFrame(raf);
  }, [playing, maxT, draw]);

  // Redraw once when paused and something changes
  useEffect(() => {
    if (!playing) draw(0);
  }, [playing, draw, hours, cells, layer, tap, t]);

  function scrub(value: number) {
    tRef.current = value;
    setT(value);
    setPlaying(false);
  }

  function onTap(e: React.MouseEvent<HTMLCanvasElement>) {
    const r = e.currentTarget.getBoundingClientRect();
    const x = ((e.clientX - r.left) / r.width - 0.5) * 2 * RANGE_MI;
    const y = (0.5 - (e.clientY - r.top) / r.height) * 2 * RANGE_MI;
    setTap({ x, y });
    setPlaying(false);
  }

  const now = hourAt(hours, t);
  const peak = hours.reduce((k, h, i) => (h.precip_in > hours[k].precip_in ? i : k), 0);
  const dry = hours[peak].precip_in < 0.005;
  const summary = dry
    ? "Dry for the next 12 hours."
    : `Rain peaks around ${hours[peak].label} (${describeRate(hours[peak].precip_in)}). Gusts up to ${Math.max(...hours.map((h) => h.gust_mph))} mph.`;

  const tapRate = tap ? rainRateAt(cells, hours, t, tap.x, tap.y) : 0;
  const tapDist = tap ? Math.hypot(tap.x, tap.y) : 0;
  const tapBearing = tap ? (Math.atan2(tap.x, tap.y) * 180) / Math.PI : 0;
  const alerts = live?.alerts ?? [];

  return (
    <div className="screen radar">
      <header className="screen-header">
        <div>
          <h1>Radar</h1>
          <p>{location?.label ?? "Enter your ZIP"}</p>
        </div>
        <span className="sample-tag">{useLive && live && !live.fallback ? "Forecast" : "Simulated"}</span>
      </header>

      <div className="stack">
        {alerts.length > 0 && (
          <div className="note note--alert">
            <strong>{alerts[0].event}</strong>
            <span>{alerts[0].headline}</span>
          </div>
        )}

        <section className="card radar__card">
          <div className="radar__frame">
            <canvas ref={canvasRef} width={SIZE} height={SIZE} onClick={onTap} className="radar__canvas"
              role="img" aria-label={`Forecast radar around ZIP ${zip}. ${summary} Tap a spot for details.`} />
            <span className="radar__time">{now.label}</span>
          </div>

          <div className="radar__controls">
            <button type="button" className="radar__play" onClick={() => setPlaying((p) => !p)}
              aria-label={playing ? "Pause" : "Play"}>{playing ? "❚❚" : "▶"}</button>
            <input type="range" min={0} max={maxT} step={0.05} value={t}
              onChange={(e) => scrub(Number(e.target.value))} aria-label="Time, next 12 hours" />
          </div>

          <div className="radar__row">
            <div className="chips">
              {(["rain", "wind"] as const).map((l) => (
                <button key={l} type="button" aria-pressed={layer === l}
                  className={`chip${layer === l ? " is-on" : ""}`} onClick={() => setLayer(l)}>
                  {l === "rain" ? "Rain" : "Wind"}
                </button>
              ))}
            </div>
            <span className="radar__legend" aria-hidden><i />light<b />heavy</span>
          </div>

          <p className="radar__summary">{summary}</p>
          <p className="radar__hint">
            {tap
              ? tapDist < 3
                ? `Over your ZIP center at ${now.label}: ${describeRate(tapRate)}, gusts ${Math.round(now.gust)} mph.`
                : `${Math.round(tapDist)} mi ${compass(tapBearing)} of your ZIP at ${now.label}: ${describeRate(tapRate)}, gusts ${Math.round(now.gust)} mph.`
              : "Tap the map to see conditions at a spot."}
          </p>
          <p className="radar__caption">
            Illustration drawn from the hourly forecast{useLive ? "" : " and the storm you chose"}. It is not live radar imagery.{" "}
            <a href={nwsForecast(zip)} target="_blank" rel="noopener noreferrer">National Weather Service forecast</a>
          </p>
        </section>

        <section className="card help" aria-label="Need help">
          <h2>Need help?</h2>
          <div className="help__grid">
            <a className="help__btn help__btn--urgent" href="tel:911">
              <strong>Call 911</strong><small>Emergency</small>
            </a>
            <a className="help__btn" href={UTILITY_PHONE_TEL}>
              <strong>Call the power company</strong><small>{UTILITY_PHONE_DISPLAY}</small>
            </a>
            <a className="help__btn" href={mapsHospitals(zip)} target="_blank" rel="noopener noreferrer">
              <strong>Find a hospital</strong><small>Opens maps near your ZIP</small>
            </a>
            <a className="help__btn" href={SHELTER_FINDER} target="_blank" rel="noopener noreferrer">
              <strong>Find a shelter</strong><small>Red Cross</small>
            </a>
          </div>
          <p className="help__note">
            Calls open your phone. The maps link shares only your ZIP. Downed power line? Stay 30 feet away and call 911.
          </p>
        </section>
      </div>
    </div>
  );
}
