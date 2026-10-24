// Nearby hospitals, fire stations, and police stations from OpenStreetMap (public data).
// The browser asks OpenStreetMap's Overpass service directly. Our own server never returns facility
// locations, and the organizers' priority-locations file is not used for these pins.

export type PlaceKind = "hospital" | "fire" | "police";

export interface Place {
  id: string;
  kind: PlaceKind;
  name: string;
  lat: number;
  lon: number;
  phone: string | null; // from OpenStreetMap when it lists one
  miles: number; // from the ZIP center
  bearing: number; // degrees clockwise from north
}

export const KIND_LABEL: Record<PlaceKind, string> = { hospital: "Hospital", fire: "Fire station", police: "Police" };
const AMENITY: Record<string, PlaceKind> = { hospital: "hospital", fire_station: "fire", police: "police" };

const MIRRORS = [
  "https://overpass-api.de/api/interpreter",
  "https://overpass.kumi.systems/api/interpreter",
  "https://overpass.private.coffee/api/interpreter",
];
const CACHE_PREFIX = "storm-watch:places:";
const CACHE_MS = 7 * 24 * 60 * 60 * 1000;
const RADIUS_M = 48280; // 30 miles

export function milesBetween(lat1: number, lon1: number, lat2: number, lon2: number): number {
  const r = 3958.8;
  const rad = (d: number) => (d * Math.PI) / 180;
  const a = Math.sin(rad(lat2 - lat1) / 2) ** 2 + Math.cos(rad(lat1)) * Math.cos(rad(lat2)) * Math.sin(rad(lon2 - lon1) / 2) ** 2;
  return 2 * r * Math.asin(Math.sqrt(a));
}

function bearingTo(lat1: number, lon1: number, lat2: number, lon2: number): number {
  const rad = (d: number) => (d * Math.PI) / 180;
  const y = Math.sin(rad(lon2 - lon1)) * Math.cos(rad(lat2));
  const x = Math.cos(rad(lat1)) * Math.sin(rad(lat2)) - Math.sin(rad(lat1)) * Math.cos(rad(lat2)) * Math.cos(rad(lon2 - lon1));
  return ((Math.atan2(y, x) * 180) / Math.PI + 360) % 360;
}

// Only keep phone numbers that look like phone numbers, so a bad tag can't become a strange link.
export function cleanPhone(raw: string | undefined): string | null {
  const first = raw?.split(";")[0]?.trim();
  return first && /^[+()\d\s.-]{7,22}$/.test(first) ? first : null;
}

interface OverpassElement {
  type: string;
  id: number;
  lat?: number;
  lon?: number;
  center?: { lat: number; lon: number };
  tags?: Record<string, string>;
}

function toPlaces(els: OverpassElement[], lat0: number, lon0: number): Place[] {
  const seen = new Set<string>();
  const out: Place[] = [];
  for (const e of els) {
    const tags = e.tags ?? {};
    const kind = AMENITY[tags.amenity ?? ""];
    const lat = e.lat ?? e.center?.lat;
    const lon = e.lon ?? e.center?.lon;
    if (!kind || lat === undefined || lon === undefined) continue;
    const name = (tags.name ?? "").trim() || KIND_LABEL[kind];
    const key = `${kind}:${name}`;
    if (seen.has(key) && name !== KIND_LABEL[kind]) continue; // one pin per named place
    seen.add(key);
    out.push({
      id: `${e.type}${e.id}`, kind, name, lat, lon,
      phone: cleanPhone(tags.phone ?? tags["contact:phone"]),
      miles: milesBetween(lat0, lon0, lat, lon), bearing: bearingTo(lat0, lon0, lat, lon),
    });
  }
  return out.sort((a, b) => a.miles - b.miles).slice(0, 80);
}

export async function loadPlaces(zip: string, lat: number, lon: number, signal?: AbortSignal): Promise<Place[]> {
  const key = CACHE_PREFIX + zip;
  try {
    const hit = JSON.parse(localStorage.getItem(key) ?? "null");
    if (hit && Date.now() - hit.at < CACHE_MS) return hit.places as Place[];
  } catch {
    /* no cache */
  }
  // Exact-match tags and a 30-mile radius keep this light for the free public servers.
  const q = (tag: string) => `nwr["amenity"="${tag}"](around:${RADIUS_M},${lat},${lon});`;
  const query = `[out:json][timeout:20];(${q("hospital")}${q("fire_station")}${q("police")});out center tags 200;`;
  // Ask the servers one after another, 4 seconds apart. The first good answer wins and cancels the rest.
  const all = new AbortController();
  signal?.addEventListener("abort", () => all.abort(), { once: true });
  const overall = setTimeout(() => all.abort(), 40000);
  try {
    const els = await Promise.any(
      MIRRORS.map(async (mirror, i) => {
        await new Promise((r) => setTimeout(r, i * 4000));
        if (all.signal.aborted) throw new Error("cancelled");
        const res = await fetch(`${mirror}?data=${encodeURIComponent(query)}`, { signal: all.signal });
        if (!res.ok) throw new Error(`places ${res.status}`);
        return ((await res.json()) as { elements: OverpassElement[] }).elements;
      }),
    );
    all.abort();
    const places = toPlaces(els, lat, lon);
    try {
      localStorage.setItem(key, JSON.stringify({ at: Date.now(), places }));
    } catch {
      /* storage full: fine */
    }
    return places;
  } finally {
    clearTimeout(overall);
  }
}
