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

# The area-exposure model is backtested on the largest storm; these bands only sort that backtest.
RISK_BANDS = [(0.35, "low"), (0.65, "medium"), (1.01, "high")]

# Backup battery life we assume for home medical equipment when deciding escalation.
MEDICAL_BACKUP_HOURS = 8

# ============================================================================
# DATA LOADER
# Load organizer data once at startup, build per-circuit features, cache to disk.
# ============================================================================

log = logging.getLogger("data")

CACHE_VERSION = 7

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
DAMAGE_CLASSES = ["pole", "primary_wire", "secondary_wire", "transformer", "fuse_switch", "recloser", "other_equipment"]
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
#
# _build() turns the raw files into one table with one row per circuit ("features"). It does this in
# small steps, and each step is its own function below so you can read them one at a time:
#
#   1. _circuit_lookup      work out which circuit each transformer / device belongs to
#   2. _customer_features   count customers per circuit (customer coordinates are used once, then dropped)
#   3. _line_features       miles of power line per circuit
#   4. _pole_features       pole age and material per circuit (optional, not used by the models today)
#   5. _critical_facility_flags   yes/no: is a hospital / fire / ambulance station near the circuit's lines
#   6. _topology_features   which device sits upstream of each circuit, and how many customers it serves
#   7. _damage_history      past storm damage per circuit, from the ARCOS records
#   8. _make_locations      the anonymous list of areas people can pick (LOC-001, LOC-002, ...)
#
# A "circuit key" looks like "STATION12|CIR34". It is an internal id and is never sent to the app.

def _build(store: Store) -> None:
    transformer_to_circuit, circuit_id_to_key, devices_per_circuit = _circuit_lookup(store)

    features, premise_to_circuit, zip_counts = _customer_features(store, transformer_to_circuit, circuit_id_to_key)

    line_features, lines_in_meters = _line_features(store)
    if line_features is not None:
        features = features.join(line_features, how="outer") if len(features) else line_features

    if lines_in_meters is not None:
        pole_features = _pole_features(store, lines_in_meters)
        if pole_features is not None:
            features = features.join(pole_features, how="left")

    if len(features):
        features["protective_devices"] = pd.Series(devices_per_circuit).reindex(features.index).fillna(0)

    features["critical_facility_nearby"] = False
    if lines_in_meters is not None and len(features):
        features["critical_facility_nearby"] = features.index.isin(_critical_facility_flags(store, lines_in_meters))

    topology = _topology_features(store, premise_to_circuit)
    if topology is not None:
        features = features.join(topology, how="left")

    history = _damage_history(store)
    if history is not None:
        features = features.join(history, how="left")

    if not len(features):
        return

    # Counts that are missing mean "none", so fill them with 0.
    count_columns = ["hist_records", "hist_events", "open_damage", "pre_records", "ref_records"]
    count_columns += [f"ref_{c}" for c in DAMAGE_CLASSES]
    for column in count_columns:
        if column in features:
            features[column] = features[column].fillna(0).astype(int)
    store.circuits = features

    if "n_customers" in features:
        _make_locations(store, features, zip_counts)


def _circuit_lookup(store: Store):
    """Step 1. Find the circuit key for every transformer, and for every circuit id.

    The customer file says which transformer serves each customer. The transformer file says which
    station and circuit that transformer is on. Fuses, reclosers and switches give a second way to
    map a circuit id to the same key, which we use when a customer's transformer is missing.

    Returns: transformer_to_circuit, circuit_id_to_key, devices_per_circuit (fuses + reclosers only)."""
    id_and_key_tables = []
    transformer_to_circuit = {}

    transformers = _read(store, "transformers")
    if transformers is not None:
        transformers["key"] = _key(transformers.aep_statio, transformers.aep_circui)
        transformer_to_circuit = dict(zip(transformers.location_n, transformers.key))
        id_and_key_tables.append(transformers[["circuit_id", "key"]])

    devices_per_circuit = collections.Counter()
    for name in ("fuses", "reclosers", "switches"):
        devices = _read(store, name)
        if devices is None:
            continue
        devices["key"] = _key(devices.aep_statio, devices.aep_circui)
        id_and_key_tables.append(devices[["circuit_id", "key"]])
        if name in ("fuses", "reclosers"):
            devices_per_circuit.update(devices["key"])

    circuit_id_to_key = {}
    if id_and_key_tables:
        all_pairs = pd.concat(id_and_key_tables)
        # A circuit id can appear with slightly different keys. Use the most common one.
        circuit_id_to_key = all_pairs.groupby("circuit_id")["key"].agg(lambda keys: keys.value_counts().index[0]).to_dict()
    return transformer_to_circuit, circuit_id_to_key, devices_per_circuit


def _customer_features(store: Store, transformer_to_circuit: dict, circuit_id_to_key: dict):
    """Step 2. One row per circuit with the number of customers and how many are on overhead lines.

    Privacy: we never keep customer names, addresses, premise ids, or the life-support flag. Customer
    coordinates are used once to match customers to ZIP codes and are then thrown away.

    Returns: features table, premise_to_circuit (used for the topology step), zip_counts."""
    wanted = ["aep_xfmr_l", "circuit_id", "kind_of_se", "city", "premise_nu", "service_po", "service__1"]
    customers = _read(store, "customers", columns=wanted)
    if customers is None:
        return pd.DataFrame(), {}, None

    # Prefer the transformer's circuit. Fall back to the circuit id.
    customers["key"] = customers.aep_xfmr_l.map(transformer_to_circuit)
    customers["key"] = customers["key"].fillna(customers.circuit_id.map(circuit_id_to_key))
    customers = customers.dropna(subset=["key"])

    premise_to_circuit = dict(zip(customers.premise_nu, customers.key))
    zip_counts = _zip_counts(store, customers)

    def most_common_city(cities: pd.Series) -> str:
        known = cities[cities != "??"]
        return known.value_counts().index[0] if len(known) else "Local"

    by_circuit = customers.groupby("key")
    features = pd.DataFrame({
        "n_customers": by_circuit.size(),
        "overhead_share": by_circuit.kind_of_se.agg(lambda s: (s == "Overhead").mean()),
        "city": by_circuit.city.agg(most_common_city),
    })
    return features, premise_to_circuit, zip_counts


def _line_features(store: Store):
    """Step 3. Miles of primary power line per circuit. More line means more that a storm can hit.

    Returns: the features, and the line shapes in meters (kept for the pole and facility steps)."""
    lines = _read(store, "primary", geometry=True)
    if lines is None:
        return None, None
    lines["key"] = _key(lines.aep_statio, lines.aep_circui)
    by_circuit = lines.groupby("key")
    features = pd.DataFrame({
        "primary_miles": by_circuit.report_len.sum() / 5280.0,  # the file stores feet
        "backbone_share": by_circuit.usage.agg(lambda s: (s == "Backbone").mean()),
    })
    return features, lines[["key", "geometry"]].to_crs(METRIC_CRS)


def _pole_features(store: Store, lines_in_meters):
    """Step 4. Pole age and share of wood poles per circuit.

    Poles have no circuit field, so each company-owned pole is matched to the nearest power line
    (within 30 meters) and takes that line's circuit."""
    import geopandas as gpd

    poles = _read(store, "poles", geometry=True)
    if poles is None:
        return None
    poles = poles[poles.owner_type == "Company Owned"].to_crs(METRIC_CRS)
    matched = gpd.sjoin_nearest(poles, lines_in_meters, how="inner", max_distance=30)
    matched = matched[~matched.index.duplicated()]  # a pole equally close to two lines counts once
    age_years = (pd.Timestamp("2026-01-01") - matched.date_insta).dt.days / 365.25
    is_wood = ~matched.material.astype(str).str.lower().isin(NON_WOOD)
    matched = matched.assign(age=age_years, wood=is_wood)
    by_circuit = matched.groupby("key")
    return pd.DataFrame({"n_poles": by_circuit.size(), "pole_age_years": by_circuit.age.mean(),
                         "wood_share": by_circuit.wood.mean()})


def _critical_facility_flags(store: Store, lines_in_meters) -> set:
    """Step 5. The circuits that have a hospital, fire station, or ambulance station nearby.

    Privacy: only a yes/no per circuit is kept. Names and locations of the facilities are dropped
    straight away and never leave this function."""
    import geopandas as gpd

    shapes = []
    for name in ("hospitals", "fire", "ambulance"):
        places = _read(store, name, columns=[], geometry=True)
        if places is not None:
            shapes.append(places[["geometry"]].to_crs(METRIC_CRS))
    if not shapes:
        return set()
    nearby_zones = gpd.GeoDataFrame(pd.concat(shapes, ignore_index=True), crs=METRIC_CRS)
    nearby_zones["geometry"] = nearby_zones.geometry.buffer(CRITICAL_FACILITY_RADIUS_M)
    touching = gpd.sjoin(lines_in_meters, nearby_zones, how="inner", predicate="intersects")
    return set(touching["key"])


def _topology_features(store: Store, premise_to_circuit: dict):
    """Step 6. For each circuit, which device sits upstream and how many customers depend on it.

    The topology file has one line per customer premise: a chain of devices from the premise's
    transformer up to the substation breaker, separated by tabs. For each circuit we keep:
      - the most common chain of devices (shown as "what your power depends on")
      - the closest recloser upstream, or the breaker if there is none (the "zone" device)
      - how many customers sit behind each device (to say "serves about 650 customers")"""
    path = FILES["topology"]
    if not path.exists() or not premise_to_circuit:
        store.report["topology"] = {"status": "missing", "rows": 0, "detail": "file or customers missing"}
        return None
    try:
        customers_behind = collections.Counter()                             # device id -> customers
        chain_counts = collections.defaultdict(collections.Counter)          # circuit -> chain -> premises
        zone_counts = collections.defaultdict(collections.Counter)           # circuit -> zone device -> premises
        devices_between = collections.Counter()                              # circuit -> total non-breaker devices
        premises = collections.Counter()                                     # circuit -> premises read
        seen_devices = set()
        rows = 0

        with open(path, encoding="utf-8", errors="replace") as handle:
            for line in handle:
                fields = line.rstrip("\n").split("\t")
                circuit = premise_to_circuit.get(fields[0])
                if circuit is None or len(fields) < 3:
                    continue
                rows += 1
                # Each device looks like "table*id*Type*location". We want (Type, id).
                chain = []
                for text in fields[1:]:
                    parts = text.split("*")
                    if len(parts) >= 4:
                        chain.append((parts[2], parts[3]))
                if not chain:
                    continue
                for _, device_id in chain:
                    customers_behind[device_id] += 1
                    seen_devices.add(device_id)
                zone_type, zone_id = next(((t, i) for t, i in chain if t.startswith("Recloser")), chain[-1])
                zone_type = "recloser" if zone_type.startswith("Recloser") else "circuit breaker"
                chain_counts[circuit][tuple(chain)] += 1
                zone_counts[circuit][(zone_type, zone_id)] += 1
                devices_between[circuit] += sum(1 for t, _ in chain[1:] if t != "Circuit Breaker")
                premises[circuit] += 1

        per_circuit = {}
        for circuit, zones in zone_counts.items():
            (zone_type, zone_id), _ = zones.most_common(1)[0]
            typical_chain = chain_counts[circuit].most_common(1)[0][0]
            per_circuit[circuit] = {
                "upstream_device_type": zone_type,
                "customers_on_upstream_device": customers_behind[zone_id],
                "devices_to_breaker": devices_between[circuit] / max(premises[circuit], 1),
                "upstream_chain": _chain_steps(typical_chain, customers_behind),
            }
        store.report["topology"] = {"status": "ok", "rows": rows, "detail": f"{len(seen_devices)} devices"}
        return pd.DataFrame.from_dict(per_circuit, orient="index")
    except Exception as error:  # noqa: BLE001 - a bad topology file must not stop the app
        store.report["topology"] = {"status": "error", "rows": 0, "detail": str(error)[:200]}
        log.exception("data: topology parse failed")
        return None


def _damage_history(store: Store):
    """Step 7. Past storm damage per circuit, from the ARCOS damage records.

    Also remembers (on the store) the records themselves without coordinates, the storm events, and
    which event was the biggest (the "reference storm", Feb 2025)."""
    records = _read(store, "arcos")
    if records is None:
        return None
    records = records.drop(columns=[c for c in ("Longitude", "Latitude") if c in records.columns])  # privacy
    records["key"] = _key(records.Substation, records.Circuit)
    records["damage_class"] = records.Asset_Type.astype(str).str.lower().map(ARCOS_CLASS).fillna("other_equipment")
    records["repair_days"] = (records.Repaired_D - records.Report_Tim).dt.days

    # Label every record with the storm event it belongs to.
    events = _events(records.Report_Tim)
    event_of_day = {}
    for event in events:
        for day in pd.date_range(event["start"], event["end"]):
            event_of_day[day] = event["id"]
    records["event_id"] = records.Report_Tim.dt.normalize().map(event_of_day)

    biggest = max(events, key=lambda e: e["records"])
    store.events = events
    store.reference_event = {
        "id": biggest["id"], "start": biggest["start"].date().isoformat(), "end": biggest["end"].date().isoformat(),
        "records": biggest["records"], "label": biggest["start"].strftime("%B %Y") + " storm",
    }

    in_biggest = records.event_id == biggest["id"]
    before_biggest = records.Report_Tim < biggest["start"]
    by_circuit = records.groupby("key")
    history = pd.DataFrame({
        "hist_records": by_circuit.size(),
        "hist_events": by_circuit.event_id.nunique(),
        "hist_access_rate": by_circuit.Access_Iss.agg(lambda s: (s == "Yes").mean()),
        "open_damage": by_circuit.Repaired.agg(lambda s: (s == "No").sum()),
        "median_repair_days": records[records.Repaired == "Yes"].groupby("key").repair_days.median(),
    })
    history["pre_records"] = records[before_biggest].groupby("key").size()   # history before the big storm
    history["ref_records"] = records[in_biggest].groupby("key").size()       # damage in the big storm
    for damage_class in DAMAGE_CLASSES:
        history[f"ref_{damage_class}"] = records[in_biggest & (records.damage_class == damage_class)].groupby("key").size()

    mix = records[in_biggest].damage_class.value_counts(normalize=True)
    store.damage_mix = {c: float(mix.get(c, 0.0)) for c in DAMAGE_CLASSES}
    store.arcos = records[["key", "Report_Tim", "Repaired", "damage_class", "Access_Iss", "event_id", "repair_days"]]
    return history


def _make_locations(store: Store, features: pd.DataFrame, zip_counts) -> None:
    """Step 8. The anonymous list of areas: one per circuit with enough customers.

    Names are the city plus a number ("Roanoke area 3"). Internal circuit keys are never shown."""
    eligible = features[features.n_customers >= MIN_CUSTOMERS_PER_LOCATION].copy().sort_index()
    eligible["area_number"] = eligible.groupby("city").cumcount() + 1
    eligible = eligible.reset_index(names="key")
    eligible["location_id"] = [f"LOC-{i + 1:03d}" for i in range(len(eligible))]
    eligible["label"] = eligible.city.str.title() + " area " + eligible.area_number.astype(str)
    eligible["customers_served_band"] = eligible.n_customers.map(_customers_band)
    eligible["has_storm_history"] = eligible.get("hist_records", pd.Series(0, index=eligible.index)) > 0
    store.locations = eligible[["location_id", "key", "label", "customers_served_band", "has_storm_history"]]
    store.zip_index, store.zip_weights = _zip_index(zip_counts, set(eligible.key))


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
