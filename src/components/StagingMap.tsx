import L from "leaflet";
import "leaflet/dist/leaflet.css";
import { useEffect, useRef } from "react";
import type { StagingResponse } from "../api/contract";

// ZIP centers are public. They ship with the site as a small file, so our API never returns coordinates.
let centersPromise: Promise<Record<string, [number, number]>> | null = null;
function loadCenters(): Promise<Record<string, [number, number]>> {
  centersPromise ??= fetch("/zip-centers.json").then((r) => r.json()).catch(() => {
    centersPromise = null;
    return {};
  });
  return centersPromise;
}

// Heat from pale yellow to deep red.
function heatColor(h: number): string {
  const hue = 55 - 55 * Math.min(Math.max(h, 0), 1);
  return `hsl(${hue}, 95%, ${60 - 15 * h}%)`;
}

// A heat map of expected damage by ZIP, with the suggested staging zones as lettered pins.
export default function StagingMap({ data }: { data: StagingResponse }) {
  const el = useRef<HTMLDivElement>(null);
  const map = useRef<L.Map | null>(null);
  const layer = useRef<L.LayerGroup | null>(null);

  useEffect(() => {
    if (!el.current) return;
    const m = L.map(el.current, { center: [37.27, -79.95], zoom: 10, minZoom: 7, maxZoom: 13, attributionControl: true });
    L.tileLayer("https://basemap.nationalmap.gov/arcgis/rest/services/USGSTopo/MapServer/tile/{z}/{y}/{x}", {
      maxNativeZoom: 16, maxZoom: 19,
      attribution: 'Map: <a href="https://www.usgs.gov/programs/national-geospatial-program/national-map">U.S. Geological Survey</a>',
    }).addTo(m);
    layer.current = L.layerGroup().addTo(m);
    map.current = m;
    return () => { m.remove(); map.current = layer.current = null; };
  }, []);

  useEffect(() => {
    let live = true;
    loadCenters().then((centers) => {
      const g = layer.current, m = map.current;
      if (!live || !g || !m) return;
      g.clearLayers();

      // Many ZIPs share one center, so add them up and draw one blob per place.
      const places = new Map<string, { lat: number; lon: number; expected: number; zips: string[] }>();
      for (const z of data.zips) {
        const c = centers[z.zip];
        if (!c) continue;
        const key = `${c[0]},${c[1]}`;
        const p = places.get(key) ?? { lat: c[0], lon: c[1], expected: 0, zips: [] };
        p.expected += z.expected_customers;
        p.zips.push(z.zip);
        places.set(key, p);
      }
      const peak = Math.max(...[...places.values()].map((p) => p.expected), 1e-9);
      for (const p of places.values()) {
        const h = p.expected / peak;
        // Two stacked circles give a soft glow.
        L.circle([p.lat, p.lon], { radius: 4500, stroke: false, fillColor: heatColor(h), fillOpacity: 0.12 + 0.2 * h, interactive: false }).addTo(g);
        L.circle([p.lat, p.lon], { radius: 2200, stroke: false, fillColor: heatColor(h), fillOpacity: 0.25 + 0.5 * h })
          .bindTooltip(`ZIP ${p.zips.slice(0, 3).join(", ")}${p.zips.length > 3 ? ` +${p.zips.length - 3}` : ""} · about ${Math.round(p.expected)} customers expected to lose power`)
          .addTo(g);
      }

      const bounds: L.LatLngTuple[] = [...places.values()].map((p) => [p.lat, p.lon]);
      for (const zone of data.zones) {
        const c = centers[zone.anchor_zip];
        if (!c) continue;
        const icon = L.divIcon({
          className: "staging-pin",
          html: `<span>${zone.label.replace("Zone ", "")}</span>`,
          iconSize: [30, 30], iconAnchor: [15, 15],
        });
        L.marker([c[0], c[1]], { icon, keyboard: false })
          .bindTooltip(`${zone.label}: stage near ${zone.anchor_city} (ZIP ${zone.anchor_zip})`).addTo(g);
      }
      m.invalidateSize();
      if (bounds.length) m.fitBounds(L.latLngBounds(bounds).pad(0.15), { maxZoom: 11 });
    });
    return () => { live = false; };
  }, [data]);

  return <div ref={el} className="staging-map" role="img" aria-label="Map of expected storm damage by ZIP code, with suggested staging zones" />;
}
