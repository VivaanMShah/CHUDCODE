"""Settings, data loading, and area lookup.

  python -m app.data         print the data schema report
  python -m app.data zips    rebuild app/zip_centers.csv (needs internet)
"""
from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field
from pathlib import Path
import collections
import csv
import json
import logging
import math
import os
import pickle
import sys
import time
import urllib.error
import urllib.request

import networkx as nx
import numpy as np
import pandas as pd


# ============================================================================
# SETTINGS
# Paths and tunable constants. Placeholders are labeled so they show up in meta.assumptions.
# ============================================================================

ROOT = Path(__file__).resolve().parent.parent

try:  # local runs: pick up AWS credentials and settings from .env. In AWS there is no .env.
    from dotenv import load_dotenv

    load_dotenv(ROOT / ".env")
except ImportError:
    pass
DATA_DIR = ROOT / "data"
CACHE_DIR = DATA_DIR / "_cache"
LOG_DIR = ROOT / "logs"
# In the Docker image only: processed data built from the raw files (about 150 KB).
BUNDLE = Path(os.getenv("STORE_BUNDLE", ROOT / "bundle" / "store.pkl"))
ZIP_TABLE = ROOT / "app" / "zip_centers.csv"   # public ZIP -> city + center point
WEATHER_MODEL = ROOT / "app" / "weather_model.json"  # trained by python -m app.train_weather

FILES = {
    "arcos": DATA_DIR / "Data1" / "arcos_prod_damage_09_28_Clip.shp",
    "customers": DATA_DIR / "Data1" / "Customer_Clip.shp",
    "poles": DATA_DIR / "Data1" / "Pole_Clip.shp",
    "primary": DATA_DIR / "Data1" / "Primary_ExportFeatures.shp",
    "fuses": DATA_DIR / "Data1" / "Fuse_Bank_Clip.shp",
    "reclosers": DATA_DIR / "Data2" / "Recloser_Bank_Clip.shp",
    "switches": DATA_DIR / "Data2" / "Switch_Clip.shp",
    "transformers": DATA_DIR / "Data2" / "Transformer_Clip.shp",
    "hospitals": DATA_DIR / "Data1" / "Hospitals_Medic_Pairwis_Clip.shp",
    "fire": DATA_DIR / "Data1" / "Fire_Stations_E_Pairwis_Clip.shp",
    "ambulance": DATA_DIR / "Data1" / "Ambulance_Servi_Pairwis_Clip.shp",
    "topology": DATA_DIR / "matched_upstream" / "matched_upstream.txt",
    # Not in the data package yet. Restoration uses labeled placeholders until they arrive.
    "storms_labor": DATA_DIR / "storms_labor_hours.xlsx",
    "indirect_labor": DATA_DIR / "indirect_labor.xlsx",
}

# Projected CRS for distance math (UTM 17N covers the Roanoke service area).
METRIC_CRS = "EPSG:32617"

# Locations: one per circuit with at least this many customers.
MIN_CUSTOMERS_PER_LOCATION = 50

# Critical facility "nearby" = within this distance of the circuit's primary lines.
CRITICAL_FACILITY_RADIUS_M = 800

# A ZIP counts as served when at least this many customers sit closest to its center.
MIN_CUSTOMERS_PER_ZIP = 100

# Monte Carlo (fixed so the demo is repeatable)
MC_SAMPLES = 2000
MC_SEED = 42

# Placeholder crew-hours per damage item, used until the STORMS labor-hour file is provided.
PLACEHOLDER_LABOR_HOURS = {
    "pole": 6.0,
    "primary_wire": 2.5,
    "secondary_wire": 1.5,
    "transformer": 3.0,
    "fuse_switch": 1.0,
    "recloser": 3.0,
    "other_equipment": 2.0,
}
PLACEHOLDER_TRAVEL_MULT = 1.25
PLACEHOLDER_NONPRODUCTIVE_MULT = 1.15
BLOCKED_ROADS_TRAVEL_MULT = 1.5
CREW_AVAILABILITY = 0.85  # chance each assigned crew is actually working at a given time

# Storm severity scale. The largest storm in ARCOS history (Feb 2025) is treated as severity 4.
REFERENCE_SEVERITY = 4
SEVERITY_LOGIT_STEP = 1.6  # risk log-odds change per severity level (assumed, not fitted)
SEVERITY_DAMAGE_MULT = {1: 0.05, 2: 0.2, 3: 0.5, 4: 1.0, 5: 1.8}

RISK_BANDS = [(0.35, "low"), (0.65, "medium"), (1.01, "high")]

# Backup battery life we assume for home medical equipment when deciding escalation.
MEDICAL_BACKUP_HOURS = 8

# ============================================================================
# DATA LOADER
# Load organizer data once at startup, build per-circuit features, cache to disk.
# ============================================================================

log = logging.getLogger("data")

CACHE_VERSION = 6

# ARCOS asset type -> damage class used by the restoration engine.
ARCOS_CLASS = {
    "pole": "pole",
    "primary": "primary_wire",
    "secondary": "secondary_wire",
    "transformer": "transformer",
    "step transformer": "transformer",
    "fuse bank": "fuse_switch",
    "switch": "fuse_switch",
    "recloser bank": "recloser",
}
DAMAGE_CLASSES = list(PLACEHOLDER_LABOR_HOURS.keys())
NON_WOOD = {"fiberglass", "steel", "concrete", "aluminum", "composite", "unknown", "other"}

REQUIRED_COLUMNS = {
    "arcos": ["Substation", "Circuit", "Report_Tim", "Repaired", "Repaired_D", "Asset_Type", "Inform_Wor", "Access_Iss"],
    "customers": ["aep_xfmr_l", "circuit_id", "kind_of_se", "city", "premise_nu", "service_po", "service__1"],
    "transformers": ["location_n", "aep_statio", "aep_circui", "circuit_id"],
    "primary": ["aep_statio", "aep_circui", "usage", "report_len"],
    "poles": ["date_insta", "material", "owner_type"],
    "fuses": ["aep_statio", "aep_circui", "circuit_id"],
    "reclosers": ["aep_statio", "aep_circui", "circuit_id"],
    "switches": ["aep_statio", "aep_circui", "circuit_id"],
    "hospitals": [],
    "fire": [],
    "ambulance": [],
}


@dataclass
class Store:
    report: dict = field(default_factory=dict)       # file key -> {"status", "rows", "detail"}
    circuits: pd.DataFrame | None = None             # index = circuit key "STATION|CIR"
    locations: pd.DataFrame | None = None            # location_id, key, label, band, has_history
    zip_index: pd.DataFrame | None = None            # zip, city, lat, lon, customers (server-side only)
    zip_weights: pd.DataFrame | None = None          # zip, key, w: circuits serving the ZIP, weights sum to 1
    arcos: pd.DataFrame | None = None                # damage records, no coordinates
    reference_event: dict | None = None              # largest storm in history
    events: list = field(default_factory=list)
    damage_mix: dict = field(default_factory=dict)   # system-wide damage class share in the reference event
    load_seconds: float = 0.0
    from_cache: bool = False

    def has(self, key: str) -> bool:
        return self.report.get(key, {}).get("status") == "ok"

    @property
    def live(self) -> bool:
        return self.locations is not None and len(self.locations) > 0 and self.has("customers")


# ---------------------------------------------------------------- helpers

def _key(station, circuit) -> pd.Series:
    return station.astype(str).str.strip() + "|" + circuit.astype(str).str.strip()


def _read(store: Store, name: str, columns=None, geometry=False):
    """Read one shapefile, check required columns, record the outcome. Returns None on failure."""
    import pyogrio

    path = FILES[name]
    if not path.exists():
        store.report[name] = {"status": "missing", "rows": 0, "detail": f"{path.name} not found"}
        log.warning("data: %s missing (%s)", name, path)
        return None
    try:
        info = pyogrio.read_info(path)
        have = set(info["fields"])
        need = REQUIRED_COLUMNS.get(name, [])
        missing = [c for c in need if c not in have]
        if missing:
            store.report[name] = {"status": "schema_error", "rows": int(info["features"]),
                                  "detail": f"missing columns: {missing}"}
            log.error("data: %s schema problem, missing %s", name, missing)
            return None
        cols = [c for c in (columns or need) if c in have]
        df = pyogrio.read_dataframe(path, columns=cols, read_geometry=geometry)
        store.report[name] = {"status": "ok", "rows": len(df), "detail": ""}
        return df
    except Exception as e:  # noqa: BLE001 - any read error must not crash startup
        store.report[name] = {"status": "error", "rows": 0, "detail": str(e)[:200]}
        log.exception("data: failed reading %s", name)
        return None


def _events(dates: pd.Series, gap_days: int = 3) -> list[dict]:
    """Group report dates into storm events: a new event starts after a gap of > gap_days."""
    counts = dates.dt.normalize().value_counts().sort_index()
    events, cur = [], None
    for d, n in counts.items():
        if cur is None or (d - cur["end"]).days > gap_days:
            cur = {"start": d, "end": d, "records": 0}
            events.append(cur)
        cur["end"] = d
        cur["records"] += int(n)
    for i, e in enumerate(events):
        e["id"] = i
    return events


def _customers_band(n: float) -> str:
    if n < 500:
        return "under 500"
    if n < 1000:
        return "500 to 1,000"
    if n < 2000:
        return "1,000 to 2,000"
    return "over 2,000"


# ---------------------------------------------------------------- build steps

def _build(store: Store) -> None:
    # --- circuit key lookup from device tables (transformer location -> circuit key) ---
    xf = _read(store, "transformers")
    dev_frames = []
    xf_to_key = {}
    if xf is not None:
        xf["key"] = _key(xf.aep_statio, xf.aep_circui)
        xf_to_key = dict(zip(xf.location_n, xf.key))
        dev_frames.append(xf[["circuit_id", "key"]])

    device_counts = collections.Counter()
    for name in ("fuses", "reclosers", "switches"):
        df = _read(store, name)
        if df is None:
            continue
        df["key"] = _key(df.aep_statio, df.aep_circui)
        dev_frames.append(df[["circuit_id", "key"]])
        if name in ("fuses", "reclosers"):
            device_counts.update(df["key"])

    circuit_id_to_key = {}
    if dev_frames:
        d = pd.concat(dev_frames)
        circuit_id_to_key = d.groupby("circuit_id")["key"].agg(lambda s: s.value_counts().index[0]).to_dict()

    # --- customers: aggregate only. No coordinates, addresses or life-support flag loaded. ---
    cust = _read(store, "customers", columns=["aep_xfmr_l", "circuit_id", "kind_of_se", "city", "premise_nu",
                                              "service_po", "service__1"])
    zip_by_key = None
    premise_to_key = {}
    feats = pd.DataFrame()
    if cust is not None:
        k = cust.aep_xfmr_l.map(xf_to_key)
        k = k.fillna(cust.circuit_id.map(circuit_id_to_key))
        cust["key"] = k
        cust = cust.dropna(subset=["key"])
        premise_to_key = dict(zip(cust.premise_nu, cust.key))
        zip_by_key = _zip_counts(store, cust)  # uses customer points once, then they are dropped
        g = cust.groupby("key")
        feats = pd.DataFrame({
            "n_customers": g.size(),
            "overhead_share": g.kind_of_se.agg(lambda s: (s == "Overhead").mean()),
            "city": g.city.agg(lambda s: s[s != "??"].value_counts().index[0] if (s != "??").any() else "Local"),
        })
        del cust

    # --- primary lines: exposure features, also used for facility proximity and pole matching ---
    prim = _read(store, "primary", geometry=True)
    prim_m = None
    if prim is not None:
        prim["key"] = _key(prim.aep_statio, prim.aep_circui)
        g = prim.groupby("key")
        pf = pd.DataFrame({
            "primary_miles": g.report_len.sum() / 5280.0,  # report_len is in feet
            "backbone_share": g.usage.agg(lambda s: (s == "Backbone").mean()),
        })
        feats = feats.join(pf, how="outer") if len(feats) else pf
        prim_m = prim[["key", "geometry"]].to_crs(METRIC_CRS)
        del prim

    # --- poles: match each pole to the nearest primary line to get its circuit ---
    poles = _read(store, "poles", geometry=True)
    if poles is not None and prim_m is not None:
        import geopandas as gpd

        poles = poles[poles.owner_type == "Company Owned"].to_crs(METRIC_CRS)
        j = gpd.sjoin_nearest(poles, prim_m, how="inner", max_distance=30)
        j = j[~j.index.duplicated()]
        age = (pd.Timestamp("2026-01-01") - j.date_insta).dt.days / 365.25
        mat = j.material.astype(str).str.lower()
        j = j.assign(age=age, wood=~mat.isin(NON_WOOD))
        g = j.groupby("key")
        pl = pd.DataFrame({"n_poles": g.size(), "pole_age_years": g.age.mean(), "wood_share": g.wood.mean()})
        feats = feats.join(pl, how="left")
        del poles, j

    if len(feats):
        feats["protective_devices"] = pd.Series(device_counts)
        feats["protective_devices"] = feats["protective_devices"].fillna(0)

    # --- critical facilities: reduce to a per-circuit yes/no, then drop everything else ---
    feats["critical_facility_nearby"] = False
    if prim_m is not None and len(feats):
        import geopandas as gpd

        fac = []
        for name in ("hospitals", "fire", "ambulance"):
            f = _read(store, name, columns=[], geometry=True)
            if f is not None:
                fac.append(f[["geometry"]].to_crs(METRIC_CRS))
        if fac:
            fac = gpd.GeoDataFrame(pd.concat(fac, ignore_index=True), crs=METRIC_CRS)
            fac["geometry"] = fac.geometry.buffer(CRITICAL_FACILITY_RADIUS_M)
            hit = gpd.sjoin(prim_m, fac, how="inner", predicate="intersects")
            near_keys = set(hit["key"])
            feats["critical_facility_nearby"] = feats.index.isin(near_keys)
            del fac, hit
    del prim_m

    # --- topology: premise -> upstream devices. Zone device = nearest recloser, else breaker. ---
    if FILES["topology"].exists() and premise_to_key:
        try:
            graph = nx.DiGraph()
            behind = collections.Counter()   # customers behind each device (any level)
            chains_of_key = collections.defaultdict(collections.Counter)
            zone_of_key = collections.defaultdict(collections.Counter)
            depth_sum = collections.Counter()
            depth_n = collections.Counter()
            rows = 0
            with open(FILES["topology"], encoding="utf-8", errors="replace") as fh:
                for line in fh:
                    parts = line.rstrip("\n").split("\t")
                    key = premise_to_key.get(parts[0])
                    if key is None or len(parts) < 3:
                        continue
                    rows += 1
                    chain = []
                    for p in parts[1:]:
                        f = p.split("*")
                        if len(f) >= 4:
                            chain.append((f[2], f[3]))
                    for (_, a), (_, b) in zip(chain, chain[1:]):
                        graph.add_edge(a, b)  # downstream -> upstream
                    zone = next((c for c in chain if c[0].startswith("Recloser")), chain[-1])
                    zone_type = "recloser" if zone[0].startswith("Recloser") else "circuit breaker"
                    for _, dev_id in chain:
                        behind[dev_id] += 1
                    chains_of_key[key][tuple(chain)] += 1
                    zone_of_key[key][(zone_type, zone[1])] += 1
                    depth_sum[key] += sum(1 for c in chain[1:] if c[0] != "Circuit Breaker")
                    depth_n[key] += 1
            topo = {}
            for key, zones in zone_of_key.items():
                (ztype, zid), _ = zones.most_common(1)[0]
                topo[key] = {
                    "upstream_device_type": ztype,
                    "customers_on_upstream_device": behind[zid],
                    "devices_to_breaker": depth_sum[key] / max(depth_n[key], 1),
                    "upstream_chain": _chain_steps(chains_of_key[key].most_common(1)[0][0], behind),
                }
            feats = feats.join(pd.DataFrame.from_dict(topo, orient="index"), how="left")
            store.report["topology"] = {"status": "ok", "rows": rows,
                                        "detail": f"graph {graph.number_of_nodes()} devices"}
        except Exception as e:  # noqa: BLE001
            store.report["topology"] = {"status": "error", "rows": 0, "detail": str(e)[:200]}
            log.exception("data: topology parse failed")
    else:
        store.report["topology"] = {"status": "missing", "rows": 0, "detail": "file or customers missing"}

    # --- ARCOS damage history ---
    arcos = _read(store, "arcos")
    if arcos is not None:
        arcos = arcos.drop(columns=[c for c in ("Longitude", "Latitude") if c in arcos.columns])
        arcos["key"] = _key(arcos.Substation, arcos.Circuit)
        arcos["damage_class"] = arcos.Asset_Type.astype(str).str.lower().map(ARCOS_CLASS).fillna("other_equipment")
        arcos["repair_days"] = (arcos.Repaired_D - arcos.Report_Tim).dt.days
        events = _events(arcos.Report_Tim)
        bounds = [(e["start"], e["end"]) for e in events]
        arcos["event_id"] = [next(i for i, (s, e) in enumerate(bounds) if s <= d <= e)
                             for d in arcos.Report_Tim.dt.normalize()]
        ref = max(events, key=lambda e: e["records"])
        store.events = events
        store.reference_event = {
            "id": ref["id"], "start": ref["start"].date().isoformat(), "end": ref["end"].date().isoformat(),
            "records": ref["records"], "label": ref["start"].strftime("%B %Y") + " storm",
        }
        in_ref = arcos.event_id == ref["id"]
        pre = arcos.Report_Tim < ref["start"]
        g = arcos.groupby("key")
        hist = pd.DataFrame({
            "hist_records": g.size(),
            "hist_events": g.event_id.nunique(),
            "hist_access_rate": g.Access_Iss.agg(lambda s: (s == "Yes").mean()),
            "open_damage": g.Repaired.agg(lambda s: (s == "No").sum()),
            "median_repair_days": arcos[arcos.Repaired == "Yes"].groupby("key").repair_days.median(),
        })
        hist["pre_records"] = arcos[pre].groupby("key").size()
        hist["ref_records"] = arcos[in_ref].groupby("key").size()
        for cls in DAMAGE_CLASSES:
            hist[f"ref_{cls}"] = arcos[in_ref & (arcos.damage_class == cls)].groupby("key").size()
        mix = arcos[in_ref].damage_class.value_counts(normalize=True)
        store.damage_mix = {c: float(mix.get(c, 0.0)) for c in DAMAGE_CLASSES}
        feats = feats.join(hist, how="left")
        store.arcos = arcos[["key", "Report_Tim", "Repaired", "damage_class", "Access_Iss", "event_id", "repair_days"]]

    if not len(feats):
        return

    count_cols = ["hist_records", "hist_events", "open_damage", "pre_records", "ref_records"] + \
                 [f"ref_{c}" for c in DAMAGE_CLASSES]
    for c in count_cols:
        if c in feats:
            feats[c] = feats[c].fillna(0).astype(int)
    store.circuits = feats

    # --- selectable anonymized locations ---
    if "n_customers" in feats:
        loc = feats[feats.n_customers >= MIN_CUSTOMERS_PER_LOCATION].copy().sort_index()
        loc["area_no"] = loc.groupby("city").cumcount() + 1
        loc = loc.reset_index(names="key")
        loc["location_id"] = [f"LOC-{i + 1:03d}" for i in range(len(loc))]
        loc["label"] = loc.city.str.title() + " area " + loc.area_no.astype(str)
        loc["customers_served_band"] = loc.n_customers.map(_customers_band)
        loc["has_storm_history"] = loc.get("hist_records", pd.Series(0, index=loc.index)) > 0
        store.locations = loc[["location_id", "key", "label", "customers_served_band", "has_storm_history"]]
        store.zip_index, store.zip_weights = _zip_index(zip_by_key, set(loc.key))


def _load_zip_table() -> pd.DataFrame | None:
    if not ZIP_TABLE.exists():
        return None
    z = pd.read_csv(ZIP_TABLE, dtype={"zip": str})
    return z[z.state == "VA"].reset_index(drop=True)


def _zip_counts(store: Store, cust: pd.DataFrame) -> pd.DataFrame | None:
    """Count customers per (ZIP center, circuit). Each customer goes to the nearest ZIP center.

    ZIPs that share a center point (PO-box ZIPs) are treated as one place. Approximation:
    real ZIP borders are not in the data, so this is nearest-center, not exact."""
    z = _load_zip_table()
    if z is None:
        store.report["zip_table"] = {"status": "missing", "rows": 0, "detail": f"{ZIP_TABLE.name} not found"}
        return None
    from scipy.spatial import cKDTree

    centers = z.drop_duplicates(["lat", "lon"]).reset_index(drop=True)
    k = np.cos(np.radians(37.3))  # shrink longitude so distances are roughly equal in both directions
    tree = cKDTree(np.c_[centers.lat, centers.lon * k])
    dist, idx = tree.query(np.c_[cust.service__1.values, cust.service_po.values * k])
    ok = dist < 0.25  # about 25 km; farther customers are outside our ZIP table
    counts = (pd.DataFrame({"c": idx[ok], "key": cust.key.values[ok]}).groupby(["c", "key"]).size()
              .rename("n").reset_index())
    counts["lat"] = centers.lat.values[counts.c]
    counts["lon"] = centers.lon.values[counts.c]
    store.report["zip_table"] = {"status": "ok", "rows": len(z), "detail": f"{len(centers)} distinct centers"}
    return counts


def _zip_index(counts: pd.DataFrame | None, valid_keys: set):
    """ZIP -> the (up to 5) circuits serving most of the customers near its center.

    A ZIP is served by many circuits, so no single one represents it. The weights let the
    resolver build a 'typical circuit' for the ZIP."""
    z = _load_zip_table()
    if counts is None or z is None:
        return None, None
    counts = counts[counts.key.isin(valid_keys)]
    customers = counts.groupby("c").n.sum().rename("customers")
    top = (counts.sort_values("n", ascending=False).groupby("c").head(5)
           .assign(w=lambda t: t.n / t.groupby("c").n.transform("sum")))
    centers = counts.drop_duplicates("c").set_index("c")[["lat", "lon"]].join(customers)
    centers = centers[centers.customers >= MIN_CUSTOMERS_PER_ZIP].reset_index()
    index = z.merge(centers, on=["lat", "lon"], how="inner")
    index["lat"], index["lon"] = index.lat.round(2), index.lon.round(2)
    weights = z.merge(top[["c", "key", "w", "lat", "lon"]], on=["lat", "lon"], how="inner")[["zip", "key", "w"]]
    weights = weights[weights.zip.isin(index.zip)]
    return index[["zip", "city", "lat", "lon", "customers"]].reset_index(drop=True), weights.reset_index(drop=True)


def mock_store(report: dict | None = None) -> Store:
    """Tiny synthetic store so every endpoint still works when real data is missing."""
    rows = []
    for i, (miles, cust, hist) in enumerate([(8, 600, 2), (15, 1100, 6), (30, 1500, 20), (50, 2200, 45), (5, 300, 0)]):
        r = {"n_customers": cust, "overhead_share": 0.6, "city": "Sample", "primary_miles": miles,
             "hist_records": hist, "pre_records": hist, "ref_records": hist // 2,
             "critical_facility_nearby": False, "upstream_device_type": None,
             "customers_on_upstream_device": None}
        for c in DAMAGE_CLASSES:
            r[f"ref_{c}"] = 0
        r["ref_pole"], r["ref_primary_wire"], r["ref_fuse_switch"] = hist // 6, hist // 3, hist // 6
        rows.append(r)
    circ = pd.DataFrame(rows, index=[f"MOCK|{i}" for i in range(len(rows))])
    s = Store(report=report or {}, circuits=circ)
    s.locations = pd.DataFrame({
        "location_id": [f"LOC-{i + 1:03d}" for i in range(len(rows))],
        "key": circ.index,
        "label": [f"Sample area {i + 1}" for i in range(len(rows))],
        "customers_served_band": [_customers_band(n) for n in circ.n_customers],
        "has_storm_history": circ.hist_records > 0,
    })
    s.zip_index = pd.DataFrame({
        "zip": ["24014", "24153", "24179", "24083", "24019"],
        "city": ["Roanoke", "Salem", "Vinton", "Daleville", "Roanoke"],
        "lat": [37.23, 37.29, 37.28, 37.41, 37.33], "lon": [-79.95, -80.07, -79.88, -79.92, -79.96],
        "customers": [900, 700, 500, 300, 600]})
    s.zip_weights = pd.DataFrame({"zip": ["24014", "24153", "24179", "24083", "24019"],
                                  "key": list(circ.index), "w": [1.0] * 5})
    s.damage_mix = {"pole": 0.22, "primary_wire": 0.49, "secondary_wire": 0.08, "transformer": 0.05,
                    "fuse_switch": 0.24, "recloser": 0.0, "other_equipment": 0.0}
    return s


def _device_label(raw: str) -> str:
    r = raw.lower()
    if r.startswith("circuit breaker"):
        return "circuit breaker"
    if r.startswith("recloser"):
        return "recloser"
    if r.startswith("fuse"):
        return "fuse"
    if r in ("distribution", "network"):
        return "transformer"
    return r


def _approx(n: int) -> int:
    """Round customer counts so they read as approximate, not exact."""
    if n < 100:
        return max(10, int(round(n, -1)))
    return int(round(n / 50.0) * 50)


def _chain_steps(chain: tuple, behind: collections.Counter) -> list[dict]:
    """Typical chain for an area, from the substation breaker down to the transformer.
    Device IDs are dropped here; only the type and an approximate customer count remain."""
    steps = []
    for raw_type, dev_id in reversed(chain):
        steps.append({"device_type": _device_label(raw_type), "customers_approx": _approx(behind[dev_id])})
    return steps


def _signature() -> dict:
    sig = {"v": CACHE_VERSION}
    for k, p in FILES.items():
        sig[k] = p.stat().st_mtime if p.exists() else None
    return sig


def load() -> Store:
    t0 = time.time()
    if BUNDLE.exists():  # Docker image: raw files are not shipped, only the processed store
        with open(BUNDLE, "rb") as fh:
            _, store = pickle.load(fh)
        store.from_cache = True
        store.load_seconds = round(time.time() - t0, 2)
        log.info("data: loaded bundled store in %.2fs", store.load_seconds)
        return store
    cache = CACHE_DIR / "store.pkl"
    sig = _signature()
    if cache.exists():
        try:
            with open(cache, "rb") as fh:
                saved_sig, store = pickle.load(fh)
            if saved_sig == sig:
                store.from_cache = True
                store.load_seconds = round(time.time() - t0, 2)
                log.info("data: loaded from cache in %.2fs", store.load_seconds)
                return store
        except Exception:  # noqa: BLE001
            log.warning("data: cache unreadable, rebuilding")
    store = Store()
    try:
        _build(store)
    except Exception:  # noqa: BLE001 - never block startup
        log.exception("data: build failed, falling back to mock mode")
    for k in ("storms_labor", "indirect_labor"):
        store.report[k] = {"status": "missing", "rows": 0,
                           "detail": "not in data package; restoration uses labeled placeholders"}
    store.load_seconds = round(time.time() - t0, 2)
    if store.live:
        try:
            CACHE_DIR.mkdir(parents=True, exist_ok=True)
            with open(cache, "wb") as fh:
                pickle.dump((sig, store), fh)
        except Exception:  # noqa: BLE001
            log.warning("data: could not write cache")
    log.info("data: built in %.1fs", store.load_seconds)
    return store


def schema_report(store: Store) -> str:
    lines = ["file            status        rows  detail"]
    for k, r in store.report.items():
        lines.append(f"{k:15s} {r['status']:12s} {r['rows']:6d}  {r['detail']}")
    if store.circuits is not None:
        lines.append(f"circuits with features: {len(store.circuits)}")
        lines.append("feature null rates: " + ", ".join(
            f"{c}={store.circuits[c].isna().mean():.0%}" for c in store.circuits.columns
            if store.circuits[c].isna().any()))
    if store.locations is not None:
        lines.append(f"selectable locations: {len(store.locations)}")
    if store.reference_event:
        lines.append(f"reference event: {store.reference_event}")
    return "\n".join(lines)

# ============================================================================
# AREA LOOKUP (location id -> circuit features)
# Location ID -> area features. Two kinds of ID:
# ============================================================================

@dataclass
class Resolved:
    location_id: str
    label: str
    features: dict
    customers_served_band: str
    is_zip: bool = False
    zip: str | None = None
    lat: float | None = None   # server-side only: used to ask the weather service, never returned
    lon: float | None = None


def _clean(d: dict) -> dict:
    return {k: (None if isinstance(v, float) and math.isnan(v) else v) for k, v in d.items()}


def resolve(store: Store, location_id: str) -> Resolved | None:
    if location_id.startswith("ZIP-"):
        return _resolve_zip(store, location_id[4:])
    locs = store.locations
    if locs is None:
        return None
    row = locs[locs.location_id == location_id]
    if row.empty:
        return None
    r = row.iloc[0]
    return Resolved(location_id, r.label, _clean(store.circuits.loc[r.key].to_dict()), r.customers_served_band)


def _resolve_zip(store: Store, zip_code: str) -> Resolved | None:
    if store.zip_index is None or store.zip_weights is None:
        return None
    z = store.zip_index[store.zip_index.zip == zip_code]
    w = store.zip_weights[store.zip_weights.zip == zip_code]
    if z.empty or w.empty:
        return None
    z = z.iloc[0]
    rows = store.circuits.loc[w.key.values]
    weights = w.w.values / w.w.sum()
    num = rows.select_dtypes("number")
    blend = {c: float(np.average(num[c].fillna(num[c].median()), weights=weights)) for c in num.columns}
    for c in ("hist_records", "hist_events", "open_damage", "pre_records", "ref_records"):
        if c in blend:
            blend[c] = round(blend[c])
    top = rows.iloc[int(np.argmax(weights))]            # the biggest circuit supplies the non-numeric parts
    blend["upstream_device_type"] = top.get("upstream_device_type")
    blend["upstream_chain"] = top.get("upstream_chain")
    blend["critical_facility_nearby"] = bool(rows.critical_facility_nearby.any())
    return Resolved(f"ZIP-{zip_code}", f"ZIP {zip_code} · {z.city}", _clean(blend),
                    _customers_band(float(z.customers)), True, zip_code, float(z.lat), float(z.lon))


def upstream_band(n) -> str | None:
    return None if n is None else _customers_band(n)

# ============================================================================
# ZIP TABLE BUILDER
# Build app/zip_centers.csv: public ZIP code -> city and center point.
# ============================================================================

ZIP_RANGE = range(24000, 24700)  # southwest Virginia, covers the Roanoke service area


def _fetch_zip(zip_code: int):
    z = f"{zip_code:05d}"
    try:
        with urllib.request.urlopen(f"https://api.zippopotam.us/us/{z}", timeout=15) as r:
            p = json.load(r)["places"][0]
        return z, p["place name"], p["state abbreviation"], round(float(p["latitude"]), 3), round(float(p["longitude"]), 3)
    except (urllib.error.HTTPError, urllib.error.URLError, KeyError, TimeoutError):
        return None


def _schema_cli() -> None:
    logging.basicConfig(level=logging.INFO)
    s = load()
    print(schema_report(s))
    pd.set_option("display.width", 250)
    pd.set_option("display.max_columns", 40)
    print(s.circuits.describe().T)


def build_zip_table() -> None:
    """Build app/zip_centers.csv: public ZIP code -> city and center point (needs internet; run once, then commit)."""
    with ThreadPoolExecutor(8) as pool:
        rows = [r for r in pool.map(_fetch_zip, ZIP_RANGE) if r and r[2] == "VA"]
    ZIP_TABLE.parent.mkdir(exist_ok=True)
    with open(ZIP_TABLE, "w", newline="", encoding="utf-8") as f:
        w = csv.writer(f)
        w.writerow(["zip", "city", "state", "lat", "lon"])
        w.writerows(sorted(rows))
    print(f"wrote {len(rows)} ZIP codes to {ZIP_TABLE}")


if __name__ == "__main__":
    if sys.argv[1:] == ["zips"]:
        build_zip_table()
    else:
        _schema_cli()
