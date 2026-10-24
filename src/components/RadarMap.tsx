import L from "leaflet";
import "leaflet/dist/leaflet.css";
import { forwardRef, useEffect, useImperativeHandle, useRef } from "react";
import { RANGE_MI, compass } from "../radar/field";
import { renderOverlay, type OverlayState } from "../radar/overlay";
import { KIND_LABEL, type Place, type PlaceKind } from "../radar/places";

const MI_M = 1609.34;
const BLANK = "data:image/gif;base64,R0lGODlhAQABAAAAACH5BAEKAAEALAAAAAABAAEAAAICTAEAOw==";
export const PIN_LETTER: Record<PlaceKind, string> = { hospital: "H", fire: "F", police: "P" };

export interface RadarMapHandle {
  draw: (t: number) => void; // redraw the rain or wind layer for hour t
  focus: (placeId: string) => void; // fly to a pin and open it
}

interface Props {
  center: { lat: number; lon: number };
  overlay: Omit<OverlayState, "t">;
  places: Place[];
  tap: { x: number; y: number } | null; // miles east and north of the center
  onTap: (p: { x: number; y: number }) => void;
}

// Built with DOM calls, never HTML strings, because place names come from a public database.
function popupFor(p: Place): HTMLElement {
  const box = document.createElement("div");
  box.className = "pin-popup";
  const name = document.createElement("strong");
  name.textContent = p.name;
  const sub = document.createElement("span");
  sub.textContent = `${KIND_LABEL[p.kind]} · ${p.miles.toFixed(1)} mi ${compass(p.bearing)}`;
  box.append(name, sub);
  const row = document.createElement("div");
  row.className = "pin-popup__row";
  if (p.phone) {
    const call = document.createElement("a");
    call.href = `tel:${p.phone.replace(/[^+\d]/g, "")}`;
    call.textContent = "Call";
    row.append(call);
  }
  const dir = document.createElement("a");
  dir.href = `https://www.google.com/maps/dir/?api=1&destination=${p.lat},${p.lon}`;
  dir.target = "_blank";
  dir.rel = "noopener noreferrer";
  dir.textContent = "Directions";
  row.append(dir);
  box.append(row);
  return box;
}

const RadarMap = forwardRef<RadarMapHandle, Props>(function RadarMap(props, ref) {
  const elRef = useRef<HTMLDivElement>(null);
  const map = useRef<L.Map | null>(null);
  const image = useRef<L.ImageOverlay | null>(null);
  const pinLayer = useRef<L.LayerGroup | null>(null);
  const tapLayer = useRef<L.LayerGroup | null>(null);
  const markers = useRef(new Map<string, L.Marker>());
  const canvases = useRef<{ out: HTMLCanvasElement; field: HTMLCanvasElement } | null>(null);
  const latest = useRef(props);
  latest.current = props;
  const tNow = useRef(0);

  const redraw = (t: number) => {
    tNow.current = t;
    if (!image.current) return;
    canvases.current ??= { out: document.createElement("canvas"), field: document.createElement("canvas") };
    const url = renderOverlay(canvases.current.out, canvases.current.field, { ...latest.current.overlay, t });
    if (url) image.current.setUrl(url);
  };

  useImperativeHandle(ref, () => ({
    draw: redraw,
    focus: (id) => {
      const m = markers.current.get(id);
      if (m && map.current) {
        map.current.flyTo(m.getLatLng(), Math.max(map.current.getZoom(), 11));
        m.openPopup();
      }
    },
  }), []);

  // Build the map once per ZIP area.
  useEffect(() => {
    const el = elRef.current;
    if (!el) return;
    const { lat, lon } = latest.current.center;
    const cos = Math.cos((lat * Math.PI) / 180);
    const m = L.map(el, { center: [lat, lon], zoom: 9, minZoom: 6, maxZoom: 14 });
    L.tileLayer("https://{s}.basemaps.cartocdn.com/dark_all/{z}/{x}/{y}{r}.png", {
      subdomains: "abcd", maxZoom: 19,
      attribution: '&copy; <a href="https://www.openstreetmap.org/copyright">OpenStreetMap</a> &copy; <a href="https://carto.com/attributions">CARTO</a>',
    }).addTo(m);
    const dLat = RANGE_MI / 69;
    const dLon = RANGE_MI / (69 * cos);
    const bounds = L.latLngBounds([lat - dLat, lon - dLon], [lat + dLat, lon + dLon]);
    m.fitBounds(bounds);
    image.current = L.imageOverlay(BLANK, bounds, { opacity: 0.9, interactive: false, className: "radar-overlay" }).addTo(m);
    for (const miles of [10, 20, 30, 40]) {
      L.circle([lat, lon], { radius: miles * MI_M, color: "#ffffff", weight: 1, opacity: 0.2, fill: false, interactive: false }).addTo(m);
    }
    L.circleMarker([lat, lon], { radius: 6, color: "#ffffff", weight: 2, fillColor: "#7aa7e8", fillOpacity: 1 })
      .bindTooltip("Your ZIP area").addTo(m);
    pinLayer.current = L.layerGroup().addTo(m);
    tapLayer.current = L.layerGroup().addTo(m);
    m.on("click", (e: L.LeafletMouseEvent) => {
      latest.current.onTap({ x: (e.latlng.lng - lon) * 69 * cos, y: (e.latlng.lat - lat) * 69 });
    });
    map.current = m;
    redraw(tNow.current);
    return () => {
      m.remove();
      map.current = image.current = pinLayer.current = tapLayer.current = null;
      markers.current.clear();
    };
  }, [props.center.lat, props.center.lon]);

  // Rain or wind layer settings changed (not the clock).
  useEffect(() => redraw(tNow.current), [props.overlay.layer, props.overlay.hours, props.overlay.cells]);

  // Pins
  useEffect(() => {
    const g = pinLayer.current;
    if (!g) return;
    g.clearLayers();
    markers.current.clear();
    for (const p of props.places) {
      const icon = L.divIcon({
        className: "", iconSize: [28, 28], iconAnchor: [14, 14], popupAnchor: [0, -14],
        html: `<span class="pin pin--${p.kind}" aria-hidden="true">${PIN_LETTER[p.kind]}</span>`,
      });
      const mk = L.marker([p.lat, p.lon], { icon, title: `${p.name} (${KIND_LABEL[p.kind]})`, riseOnHover: true });
      mk.bindPopup(() => popupFor(p));
      mk.addTo(g);
      markers.current.set(p.id, mk);
    }
  }, [props.places, props.center.lat, props.center.lon]);

  // The spot the user tapped
  useEffect(() => {
    const g = tapLayer.current;
    if (!g) return;
    g.clearLayers();
    if (!props.tap) return;
    const { lat, lon } = props.center;
    L.circleMarker([lat + props.tap.y / 69, lon + props.tap.x / (69 * Math.cos((lat * Math.PI) / 180))], {
      radius: 9, color: "#ffffff", weight: 2, fill: false, interactive: false,
    }).addTo(g);
  }, [props.tap, props.center.lat, props.center.lon]);

  return <div ref={elRef} className="radar__map" role="application" aria-label="Map with forecast rain and nearby places" />;
});

export default RadarMap;
