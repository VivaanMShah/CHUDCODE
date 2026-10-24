import { useEffect, useState, type KeyboardEvent } from "react";
import {
  fetchEvacZones,
  fetchRadar,
  fetchSafetyPlaces,
} from "../api/samples";
import type {
  EvacData,
  EvacStatus,
  EvacZone,
  MilePoint,
  RadarCell,
  RadarData,
  RadarLayer,
  SafetyPlace,
  SafetyPlaceKind,
} from "../types";
import "./Radar.css";

// ---- Map geometry ------------------------------------------------------------
// The user sits at the center of the map. 1 mile = PX_PER_MILE svg units.
const VIEW_W = 300;
const VIEW_H = 340;
const CENTER_X = VIEW_W / 2;
const CENTER_Y = VIEW_H / 2;
const PX_PER_MILE = 3;
const RINGS_MI = [15, 30, 45];

const toX = (xMi: number) => CENTER_X + xMi * PX_PER_MILE;
const toY = (yMi: number) => CENTER_Y - yMi * PX_PER_MILE;

const LAYERS: { id: RadarLayer; label: string }[] = [
  { id: "rain", label: "Rain" },
  { id: "lightning", label: "Lightning" },
  { id: "wind", label: "Wind" },
  { id: "clouds", label: "Clouds" },
];

// Legend text per layer
const LEGEND: Record<RadarLayer, { title: string; low: string; high: string }> = {
  rain: { title: "Rain intensity", low: "Light", high: "Heavy" },
  lightning: { title: "Lightning density", low: "Sparse", high: "Dense" },
  wind: { title: "Wind speed", low: "Calm", high: "Strong" },
  clouds: { title: "Cloud cover", low: "Clear", high: "Overcast" },
};

// Which gradient (defined in <defs> below) a cell uses.
// TODO: when your backend returns real lightning / wind / cloud data, feed it
// into `cells` per layer instead of reusing the rain cells like this.
function gradientFor(layer: RadarLayer, cell: RadarCell): string {
  if (layer === "lightning") return "url(#blob-lightning)";
  if (layer === "wind") return "url(#blob-wind)";
  if (layer === "clouds") return "url(#blob-clouds)";
  if (cell.intensity >= 0.8) return "url(#blob-heavy)";
  if (cell.intensity >= 0.6) return "url(#blob-medium)";
  return "url(#blob-light)";
}

function frameLabel(minutesAgo: number): string {
  return minutesAgo === 0 ? "Now" : `${minutesAgo} min ago`;
}

// ---- Evacuation traffic-light colors ------------------------------------------
// level 0 = safe (green) → yellow → orange → 1 = evacuate (red)
const COLOR_STOPS: { at: number; rgb: [number, number, number] }[] = [
  { at: 0, rgb: [47, 191, 113] },
  { at: 0.35, rgb: [245, 212, 61] },
  { at: 0.7, rgb: [240, 138, 60] },
  { at: 1, rgb: [239, 75, 75] },
];

function levelColor(level: number): string {
  const t = Math.min(1, Math.max(0, level));
  for (let i = 1; i < COLOR_STOPS.length; i++) {
    const a = COLOR_STOPS[i - 1];
    const b = COLOR_STOPS[i];
    if (t <= b.at) {
      const k = (t - a.at) / (b.at - a.at);
      const [r, g, bl] = a.rgb.map((v, j) => Math.round(v + (b.rgb[j] - v) * k));
      return `rgb(${r}, ${g}, ${bl})`;
    }
  }
  const [r, g, b] = COLOR_STOPS[COLOR_STOPS.length - 1].rgb;
  return `rgb(${r}, ${g}, ${b})`;
}

// Words as well as colors, so the map never relies on color alone.
const STATUS_LABEL: Record<EvacStatus, string> = {
  safe: "Safe",
  monitor: "Monitor",
  prepare: "Be ready to leave",
  evacuate: "Evacuate now",
};

const KIND_LABEL: Record<SafetyPlaceKind, string> = {
  hospital: "Hospital",
  shelter: "Emergency shelter",
  fire: "Fire station",
  police: "Police station",
};

// ---- Small geometry helpers -----------------------------------------------------

function pointsAttr(polygon: MilePoint[]): string {
  return polygon.map((p) => `${toX(p.xMi)},${toY(p.yMi)}`).join(" ");
}

function centroid(polygon: MilePoint[]): { x: number; y: number } {
  const n = polygon.length || 1;
  const sx = polygon.reduce((s, p) => s + p.xMi, 0) / n;
  const sy = polygon.reduce((s, p) => s + p.yMi, 0) / n;
  return { x: toX(sx), y: toY(sy) };
}

// Ray-casting test: is the point inside the polygon?
function pointInPolygon(x: number, y: number, polygon: MilePoint[]): boolean {
  let inside = false;
  for (let i = 0, j = polygon.length - 1; i < polygon.length; j = i++) {
    const a = polygon[i];
    const b = polygon[j];
    const crosses =
      a.yMi > y !== b.yMi > y &&
      x < ((b.xMi - a.xMi) * (y - a.yMi)) / (b.yMi - a.yMi) + a.xMi;
    if (crosses) inside = !inside;
  }
  return inside;
}

// Space / Enter activate our clickable SVG shapes, like a real button.
function onActivate(handler: () => void) {
  return (e: KeyboardEvent) => {
    if (e.key === "Enter" || e.key === " ") {
      e.preventDefault();
      handler();
    }
  };
}

// ---- Place icons ----------------------------------------------------------------
// Each icon is a round badge drawn in a 24x24 box (used on the map and legend).

function PlaceGlyph({ kind }: { kind: SafetyPlaceKind }) {
  switch (kind) {
    case "hospital":
      return (
        <>
          <circle cx="12" cy="12" r="12" fill="#ffffff" />
          <path d="M10 5h4v5h5v4h-5v5h-4v-5H5v-4h5z" fill="#e53935" />
        </>
      );
    case "shelter":
      return (
        <>
          <circle cx="12" cy="12" r="12" fill="#3a7bff" />
          <path d="M12 4.5 4 12h2.5v7h11v-7H20L12 4.5Z" fill="#ffffff" />
        </>
      );
    case "fire":
      return (
        <>
          <circle cx="12" cy="12" r="12" fill="#f0782c" />
          <path
            d="M12 4c.8 3 4.6 4.6 4.6 9a4.6 4.6 0 0 1-9.2 0c0-1.8.9-3 1.9-3.9.1 1.7.9 2.6 1.8 2.9-.3-3-.1-5.4.9-8Z"
            fill="#ffffff"
          />
        </>
      );
    case "police":
      return (
        <>
          <circle cx="12" cy="12" r="12" fill="#2b4a8c" />
          <path
            d="M12 4 5.5 6.6v4.8c0 4.2 2.8 7.4 6.5 9.1 3.7-1.7 6.5-4.9 6.5-9.1V6.6L12 4Z"
            fill="#ffffff"
          />
        </>
      );
  }
}

function PlaceIcon({ kind, size = 18 }: { kind: SafetyPlaceKind; size?: number }) {
  return (
    <svg width={size} height={size} viewBox="0 0 24 24" aria-hidden>
      <PlaceGlyph kind={kind} />
    </svg>
  );
}

// ---- Selection ----------------------------------------------------------------------

type Selection = { type: "zone" | "place"; id: string } | null;

export default function Radar() {

  const [data, setData] = useState<RadarData | null>(null);
  const [places, setPlaces] = useState<SafetyPlace[]>([]);
  const [evac, setEvac] = useState<EvacData | null>(null);
  const [extrasFailed, setExtrasFailed] = useState(false);
  const [error, setError] = useState(false);

  const [layer, setLayer] = useState<RadarLayer>("rain");
  const [showEvac, setShowEvac] = useState(true);
  const [showPlaces, setShowPlaces] = useState(true);
  const [selected, setSelected] = useState<Selection>(null);

  const [frameIndex, setFrameIndex] = useState(0);
  const [playing, setPlaying] = useState(false);

  // Load radar, nearby safety places and evacuation zones together. The radar
  // is required; if places or zones fail, the radar still works.
  useEffect(() => {
    let cancelled = false;
    setData(null);
    setError(false);
    Promise.allSettled([fetchRadar(), fetchSafetyPlaces(), fetchEvacZones()]).then(
      ([radar, safety, zones]) => {
        if (cancelled) return;
        if (radar.status === "rejected") {
          setError(true);
          return;
        }
        setData(radar.value);
        setFrameIndex(radar.value.frames.length - 1);
        setPlaces(safety.status === "fulfilled" ? safety.value : []);
        setEvac(zones.status === "fulfilled" ? zones.value : null);
        setExtrasFailed(safety.status === "rejected" || zones.status === "rejected");
        setSelected(null);
      },
    );
    return () => {
      cancelled = true;
    };
  }, []);

  // Playback loop: advance a frame every 700ms, wrapping around.
  useEffect(() => {
    if (!playing || !data) return;
    const id = setInterval(() => {
      setFrameIndex((i) => (i + 1) % data.frames.length);
    }, 700);
    return () => clearInterval(id);
  }, [playing, data]);

  if (error) {
    return (
      <div className="screen">
        <p className="dim">Couldn't load radar. Try again in a moment.</p>
      </div>
    );
  }

  if (!data) {
    return (
      <div className="screen">
        <p className="dim">Loading radar…</p>
      </div>
    );
  }

  const frame = data.frames[frameIndex];
  const oldest = data.frames[0];
  const legend = LEGEND[layer];

  const zones: EvacZone[] = evac?.zones ?? [];
  // Which zone is the user standing in? (The user is at 0,0 on the map.)
  const myZone = zones.find((z) => pointInPolygon(0, 0, z.polygon));

  const selectedZone =
    selected?.type === "zone" ? zones.find((z) => z.id === selected.id) : undefined;
  const selectedPlace =
    selected?.type === "place" ? places.find((p) => p.id === selected.id) : undefined;

  return (
    <div className="screen radar">
      {/* Header */}
      <header className="radar__header">
        <h1>Radar</h1>
        <span className="sample-tag">Sample data</span>
      </header>
      <p className="sample-note">
        Weather radar, evacuation zones, and places are design samples. They are not connected to
        the outage backend.
      </p>

      <div className="stack">
        {/* Your area: the traffic light for where the user is right now */}
        {myZone && (
          <div
            className="radar__status"
            style={{ ["--zone-color" as string]: levelColor(myZone.level) }}
          >
            <span className="radar__status-light" aria-hidden />
            <div>
              <span className="eyebrow">Your area · {myZone.name}</span>
              <strong>{STATUS_LABEL[myZone.status]}</strong>
              <p>{myZone.instruction}</p>
            </div>
          </div>
        )}

        {/* Weather layer tabs */}
        <div className="radar__layers" role="tablist" aria-label="Radar layer">
          {LAYERS.map((l) => (
            <button
              key={l.id}
              type="button"
              role="tab"
              aria-selected={layer === l.id}
              className={`radar__layer${layer === l.id ? " is-active" : ""}`}
              onClick={() => setLayer(l.id)}
            >
              {l.label}
            </button>
          ))}
        </div>

        {/* Weather legend */}
        <div className="radar__legend card">
          <span className="eyebrow">{legend.title}</span>
          <div className={`radar__legend-bar radar__legend-bar--${layer}`} />
          <div className="radar__legend-scale">
            <span>{legend.low}</span>
            <span>{legend.high}</span>
          </div>
        </div>

        {/* Map overlay toggles */}
        <div className="radar__toggles">
          <button
            type="button"
            className={`toggle-chip${showEvac ? " is-on" : ""}`}
            aria-pressed={showEvac}
            onClick={() => setShowEvac((v) => !v)}
          >
            <span className="toggle-chip__swatch toggle-chip__swatch--evac" aria-hidden />
            Evacuation zones
          </button>
          <button
            type="button"
            className={`toggle-chip${showPlaces ? " is-on" : ""}`}
            aria-pressed={showPlaces}
            onClick={() => setShowPlaces((v) => !v)}
          >
            <span className="toggle-chip__swatch toggle-chip__swatch--help" aria-hidden />
            Nearby help
          </button>
        </div>

        {/* Map */}
        <div className="radar__map">
          <svg
            viewBox={`0 0 ${VIEW_W} ${VIEW_H}`}
            role="group"
            aria-label={`Radar map, ${frameLabel(frame.minutesAgo)}`}
          >
            <defs>
              <radialGradient id="blob-heavy">
                <stop offset="0%" stopColor="#ef4b4b" stopOpacity="0.95" />
                <stop offset="35%" stopColor="#f08a3c" stopOpacity="0.85" />
                <stop offset="65%" stopColor="#f5d43d" stopOpacity="0.6" />
                <stop offset="100%" stopColor="#3ccf91" stopOpacity="0" />
              </radialGradient>
              <radialGradient id="blob-medium">
                <stop offset="0%" stopColor="#f08a3c" stopOpacity="0.9" />
                <stop offset="45%" stopColor="#f5d43d" stopOpacity="0.7" />
                <stop offset="100%" stopColor="#3ccf91" stopOpacity="0" />
              </radialGradient>
              <radialGradient id="blob-light">
                <stop offset="0%" stopColor="#f5d43d" stopOpacity="0.75" />
                <stop offset="55%" stopColor="#3ccf91" stopOpacity="0.5" />
                <stop offset="100%" stopColor="#3ccf91" stopOpacity="0" />
              </radialGradient>
              <radialGradient id="blob-lightning">
                <stop offset="0%" stopColor="#fff2a8" stopOpacity="0.95" />
                <stop offset="50%" stopColor="#f5b83d" stopOpacity="0.55" />
                <stop offset="100%" stopColor="#f5b83d" stopOpacity="0" />
              </radialGradient>
              <radialGradient id="blob-wind">
                <stop offset="0%" stopColor="#7fe3ff" stopOpacity="0.85" />
                <stop offset="55%" stopColor="#4c8dff" stopOpacity="0.5" />
                <stop offset="100%" stopColor="#4c8dff" stopOpacity="0" />
              </radialGradient>
              <radialGradient id="blob-clouds">
                <stop offset="0%" stopColor="#dbe4f2" stopOpacity="0.7" />
                <stop offset="60%" stopColor="#9fb0c8" stopOpacity="0.4" />
                <stop offset="100%" stopColor="#9fb0c8" stopOpacity="0" />
              </radialGradient>
              <pattern id="grid" width="30" height="30" patternUnits="userSpaceOnUse">
                <path d="M30 0H0V30" fill="none" stroke="rgba(255,255,255,0.05)" strokeWidth="1" />
              </pattern>
              {/* Diagonal stripes on "evacuate" zones: readable without color */}
              <pattern
                id="hatch"
                width="7"
                height="7"
                patternUnits="userSpaceOnUse"
                patternTransform="rotate(45)"
              >
                <line x1="0" y1="0" x2="0" y2="7" stroke="rgba(255,255,255,0.4)" strokeWidth="2" />
              </pattern>
            </defs>

            {/* Grid (tapping empty map clears the selection) */}
            <rect
              width={VIEW_W}
              height={VIEW_H}
              fill="url(#grid)"
              onClick={() => setSelected(null)}
            />

            {/* Evacuation zones: traffic-light shading */}
            {showEvac &&
              zones.map((zone) => {
                const color = levelColor(zone.level);
                const isSelected = selected?.type === "zone" && selected.id === zone.id;
                const c = centroid(zone.polygon);
                const select = () => setSelected({ type: "zone", id: zone.id });
                return (
                  <g
                    key={zone.id}
                    className="radar__zone"
                    role="button"
                    tabIndex={0}
                    aria-label={`${zone.name}: ${STATUS_LABEL[zone.status]}`}
                    onClick={select}
                    onKeyDown={onActivate(select)}
                  >
                    <polygon
                      points={pointsAttr(zone.polygon)}
                      fill={color}
                      fillOpacity={0.3}
                      stroke={isSelected ? "#ffffff" : color}
                      strokeOpacity={isSelected ? 1 : 0.85}
                      strokeWidth={isSelected ? 2 : 1}
                    />
                    {zone.status === "evacuate" && (
                      <polygon
                        points={pointsAttr(zone.polygon)}
                        fill="url(#hatch)"
                        pointerEvents="none"
                      />
                    )}
                    <text x={c.x} y={c.y} className="radar__zone-label">
                      {zone.name}
                    </text>
                  </g>
                );
              })}

            {/* Distance rings */}
            {RINGS_MI.map((mi) => (
              <g key={mi} pointerEvents="none">
                <circle
                  cx={CENTER_X}
                  cy={CENTER_Y}
                  r={mi * PX_PER_MILE}
                  fill="none"
                  stroke="rgba(255,255,255,0.12)"
                  strokeWidth="1"
                />
                <text
                  x={CENTER_X + 4}
                  y={CENTER_Y - mi * PX_PER_MILE + 11}
                  className="radar__ring-label"
                >
                  {mi} mi
                </text>
              </g>
            ))}

            {/* Storm cells for the selected frame */}
            <g pointerEvents="none">
              {frame.cells.map((cell) => (
                <circle
                  key={cell.id}
                  cx={toX(cell.xMi)}
                  cy={toY(cell.yMi)}
                  r={cell.radiusMi * PX_PER_MILE}
                  fill={gradientFor(layer, cell)}
                />
              ))}
            </g>

            {/* Nearby help: hospitals, shelters, fire and police */}
            {showPlaces &&
              places.map((place) => {
                const isSelected = selected?.type === "place" && selected.id === place.id;
                const x = toX(place.xMi);
                const y = toY(place.yMi);
                const select = () => setSelected({ type: "place", id: place.id });
                return (
                  <g
                    key={place.id}
                    className="radar__place"
                    role="button"
                    tabIndex={0}
                    aria-label={`${KIND_LABEL[place.kind]}: ${place.name}, ${place.distanceMi} miles away`}
                    onClick={select}
                    onKeyDown={onActivate(select)}
                  >
                    {isSelected && (
                      <circle cx={x} cy={y} r="12" fill="none" stroke="#ffffff" strokeWidth="2" />
                    )}
                    <circle cx={x} cy={y} r="10.5" fill="rgba(5,8,15,0.75)" />
                    <g transform={`translate(${x - 8} ${y - 8}) scale(${16 / 24})`}>
                      <PlaceGlyph kind={place.kind} />
                    </g>
                  </g>
                );
              })}

            {/* You are here */}
            <g pointerEvents="none">
              <circle className="radar__user-pulse" cx={CENTER_X} cy={CENTER_Y} r="11" />
              <circle cx={CENTER_X} cy={CENTER_Y} r="11" fill="rgba(76,141,255,0.22)" />
              <circle
                cx={CENTER_X}
                cy={CENTER_Y}
                r="6.5"
                fill="#0b1220"
                stroke="#7fb0ff"
                strokeWidth="2"
              />
              <circle cx={CENTER_X} cy={CENTER_Y} r="3" fill="#4c8dff" />
              <rect
                x={CENTER_X - 14}
                y={CENTER_Y + 14}
                width="28"
                height="13"
                rx="6.5"
                fill="#4c8dff"
              />
              <text x={CENTER_X} y={CENTER_Y + 23.5} className="radar__you-label">
                You
              </text>
            </g>
          </svg>
        </div>

        {/* Details for whatever the user tapped */}
        {selectedZone && (
          <div
            className="radar__detail"
            style={{ ["--zone-color" as string]: levelColor(selectedZone.level) }}
          >
            <span className="radar__status-light" aria-hidden />
            <div className="radar__detail-body">
              <span className="eyebrow">Zone · {selectedZone.name}</span>
              <strong>{STATUS_LABEL[selectedZone.status]}</strong>
              <p>{selectedZone.instruction}</p>
            </div>
            <button
              type="button"
              className="radar__detail-close"
              onClick={() => setSelected(null)}
              aria-label="Close details"
            >
              ×
            </button>
          </div>
        )}

        {selectedPlace && (
          <div className="radar__detail">
            <PlaceIcon kind={selectedPlace.kind} size={26} />
            <div className="radar__detail-body">
              <span className="eyebrow">{KIND_LABEL[selectedPlace.kind]}</span>
              <strong>{selectedPlace.name}</strong>
              <p>
                {selectedPlace.distanceMi} mi away
                {selectedPlace.address ? ` · ${selectedPlace.address}` : ""}
              </p>
              {selectedPlace.phone && (
                <a className="radar__detail-call" href={`tel:${selectedPlace.phone}`}>
                  Call {selectedPlace.phone}
                </a>
              )}
            </div>
            <button
              type="button"
              className="radar__detail-close"
              onClick={() => setSelected(null)}
              aria-label="Close details"
            >
              ×
            </button>
          </div>
        )}

        {/* Evacuation + icon legend */}
        {(showEvac || showPlaces) && (
          <div className="card radar__key">
            {showEvac && (
              <>
                <span className="eyebrow">Evacuation status</span>
                <div className="radar__key-bar" />
                <div className="radar__key-scale">
                  <span>Safe</span>
                  <span>Monitor</span>
                  <span>Be ready</span>
                  <span>Evacuate</span>
                </div>
              </>
            )}
            {showPlaces && (
              <ul className="radar__key-icons">
                {(Object.keys(KIND_LABEL) as SafetyPlaceKind[]).map((kind) => (
                  <li key={kind}>
                    <PlaceIcon kind={kind} size={16} />
                    {KIND_LABEL[kind]}
                  </li>
                ))}
              </ul>
            )}
            <p className="radar__key-note">
              Always follow orders from local emergency officials. In an emergency, call 911.
            </p>
          </div>
        )}

        {extrasFailed && (
          <p className="radar__warn" role="status">
            Some map details (evacuation zones or nearby help) couldn't be loaded.
          </p>
        )}

        {/* Time / playback */}
        <div className="radar__time card">
          <div className="radar__time-top">
            <span className="eyebrow">Time</span>
            <span className="radar__time-now">{frameLabel(frame.minutesAgo)}</span>
          </div>

          <div className="radar__time-controls">
            <button
              type="button"
              className="radar__play"
              onClick={() => setPlaying((p) => !p)}
              aria-label={playing ? "Pause radar loop" : "Play radar loop"}
            >
              {playing ? (
                <svg width="14" height="14" viewBox="0 0 24 24" fill="currentColor" aria-hidden>
                  <rect x="6" y="5" width="4" height="14" rx="1" />
                  <rect x="14" y="5" width="4" height="14" rx="1" />
                </svg>
              ) : (
                <svg width="14" height="14" viewBox="0 0 24 24" fill="currentColor" aria-hidden>
                  <path d="M8 5v14l11-7L8 5Z" />
                </svg>
              )}
            </button>

            <div className="radar__segments">
              {data.frames.map((f, i) => (
                <button
                  key={f.minutesAgo}
                  type="button"
                  className={`radar__segment${i === frameIndex ? " is-active" : ""}`}
                  onClick={() => {
                    setPlaying(false);
                    setFrameIndex(i);
                  }}
                  aria-label={`Show radar from ${frameLabel(f.minutesAgo)}`}
                />
              ))}
            </div>
          </div>

          <div className="radar__time-scale">
            <span>{frameLabel(oldest.minutesAgo)}</span>
            <span>Now</span>
          </div>
        </div>
      </div>
    </div>
  );
}