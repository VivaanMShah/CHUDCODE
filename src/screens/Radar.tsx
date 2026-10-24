import { useEffect, useMemo, useRef, useState } from "react";
import { useAssessment } from "../assessment";
import RadarMap, { PIN_LETTER, type RadarMapHandle } from "../components/RadarMap";
import { SHELTER_FINDER, UTILITY_PHONE_DISPLAY, UTILITY_PHONE_TEL, nwsForecast } from "../config";
import { useLocation } from "../location";
import { compass, describeRate, hourAt, makeCells, rainRateAt, seedFrom, synthHours } from "../radar/field";
import { KIND_LABEL, loadPlaces, type Place, type PlaceKind } from "../radar/places";
import "./Radar.css";

const SECONDS_PER_LOOP = 14;
const KINDS: PlaceKind[] = ["hospital", "fire", "police"];

// ZIP centers are public. They ship with the site as a small file, so our API never returns coordinates.
let centersPromise: Promise<Record<string, [number, number]>> | null = null;
function zipCenter(zip: string): Promise<{ lat: number; lon: number } | null> {
  centersPromise ??= fetch("/zip-centers.json").then((r) => r.json());
  return centersPromise.then((c) => (c[zip] ? { lat: c[zip][0], lon: c[zip][1] } : null)).catch(() => {
    centersPromise = null;
    return null;
  });
}

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
  const [center, setCenter] = useState<{ lat: number; lon: number } | null>(null);
  const [places, setPlaces] = useState<Place[]>([]);
  const [placesState, setPlacesState] = useState<"loading" | "ready" | "error">("loading");
  const [kinds, setKinds] = useState<PlaceKind[]>(KINDS);
  const [retry, setRetry] = useState(0);

  const mapRef = useRef<RadarMapHandle>(null);
  const tRef = useRef(0);

  useEffect(() => {
    setCenter(null);
    if (zip) void zipCenter(zip).then(setCenter);
  }, [zip]);

  useEffect(() => {
    if (!center) return;
    const ctrl = new AbortController();
    setPlacesState("loading");
    loadPlaces(zip, center.lat, center.lon, ctrl.signal)
      .then((p) => {
        setPlaces(p);
        setPlacesState("ready");
      })
      .catch(() => !ctrl.signal.aborted && setPlacesState("error"));
    return () => ctrl.abort();
  }, [zip, center, retry]);

  // Animation loop (only while playing), about 10 frames a second
  useEffect(() => {
    if (!playing) return;
    let raf = 0;
    let last = performance.now();
    let lastDraw = 0;
    const step = (now: number) => {
      tRef.current += ((now - last) / 1000) * (maxT / SECONDS_PER_LOOP);
      if (tRef.current > maxT) tRef.current = 0;
      last = now;
      if (now - lastDraw > 100) {
        mapRef.current?.draw(tRef.current);
        setT(tRef.current);
        lastDraw = now;
      }
      raf = requestAnimationFrame(step);
    };
    raf = requestAnimationFrame(step);
    return () => cancelAnimationFrame(raf);
  }, [playing, maxT]);

  function scrub(value: number) {
    tRef.current = value;
    setT(value);
    setPlaying(false);
    mapRef.current?.draw(value);
  }

  const shown = places.filter((p) => kinds.includes(p.kind));
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
            {center ? (
              <RadarMap
                ref={mapRef}
                center={center}
                overlay={{ cells, hours, layer }}
                places={shown}
                tap={tap}
                onTap={(p) => {
                  setTap(p);
                  setPlaying(false);
                }}
              />
            ) : (
              <div className="radar__map radar__map--empty">{zip ? "Loading map…" : "Enter your ZIP to see the map."}</div>
            )}
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
            <span className="radar__legend" aria-hidden><i />light to heavy</span>
          </div>

          <p className="radar__summary">{summary}</p>
          <p className="radar__hint">
            {tap
              ? tapDist < 3
                ? `Over your ZIP center at ${now.label}: ${describeRate(tapRate)}, gusts ${Math.round(now.gust)} mph.`
                : `${Math.round(tapDist)} mi ${compass(tapBearing)} of your ZIP center at ${now.label}: ${describeRate(tapRate)}, gusts ${Math.round(now.gust)} mph.`
              : "Tap the map for conditions at a spot, or tap a pin for details."}
          </p>
          <p className="radar__caption">
            Rain and wind are drawn from the hourly forecast. They are not live radar imagery. Map by OpenStreetMap and CARTO,
            and nearby places from OpenStreetMap. Your browser contacts them with your ZIP's area, never your exact location.{" "}
            <a href={nwsForecast(zip)} target="_blank" rel="noopener noreferrer">National Weather Service forecast</a>
          </p>
        </section>

        <section className="card places" aria-label="Nearby places">
          <div className="risk__top">
            <span className="eyebrow">Nearby help</span>
            <div className="chips">
              {KINDS.map((k) => (
                <button key={k} type="button" aria-pressed={kinds.includes(k)}
                  className={`chip${kinds.includes(k) ? " is-on" : ""}`}
                  onClick={() => setKinds((ks) => (ks.includes(k) ? ks.filter((x) => x !== k) : [...ks, k]))}>
                  {KIND_LABEL[k]}
                </button>
              ))}
            </div>
          </div>
          {placesState === "loading" && <p className="dim places__note">Finding hospitals and stations nearby…</p>}
          {placesState === "error" && (
            <p className="dim places__note">
              Couldn't load nearby places right now. The map still works.{" "}
              <button type="button" className="link" onClick={() => setRetry((n) => n + 1)}>Try again</button>
            </p>
          )}
          {placesState === "ready" && shown.length === 0 && <p className="dim places__note">No places to show.</p>}
          <ul className="places__list">
            {shown.slice(0, 8).map((p) => (
              <li key={p.id}>
                <button type="button" className="places__main" onClick={() => mapRef.current?.focus(p.id)}>
                  <span className={`pin pin--${p.kind} pin--small`} aria-hidden>{PIN_LETTER[p.kind]}</span>
                  <span>
                    <strong>{p.name}</strong>
                    <small>{KIND_LABEL[p.kind]} · {p.miles.toFixed(1)} mi {compass(p.bearing)}</small>
                  </span>
                </button>
                <span className="places__actions">
                  {p.phone && <a href={`tel:${p.phone.replace(/[^+\d]/g, "")}`}>Call</a>}
                  <a href={`https://www.google.com/maps/dir/?api=1&destination=${p.lat},${p.lon}`} target="_blank" rel="noopener noreferrer">Directions</a>
                </span>
              </li>
            ))}
          </ul>
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
            <a className="help__btn" href={SHELTER_FINDER} target="_blank" rel="noopener noreferrer">
              <strong>Find a shelter</strong><small>Red Cross</small>
            </a>
          </div>
          <p className="help__note">Downed power line? Stay 30 feet away and call 911.</p>
        </section>
      </div>
    </div>
  );
}
