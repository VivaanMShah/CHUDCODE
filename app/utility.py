"""Utility-side planning views: where to stage crews before a storm, and which devices to harden.

Both views use only public ZIP codes and area labels. They never return coordinates or device ids.
(ZIP center points stay on the server; the app draws the map from its own public ZIP-center file.)
"""
from __future__ import annotations

import math

import numpy as np
import pandas as pd

from . import data
from .risk import area_risk

ROAD_FACTOR = 1.3        # assumption: driving distance is about 1.3x the straight line
DRIVE_MPH = 30           # assumption: average storm-response speed on rural and local roads
COVERAGE = 0.8           # staging zones cover the ZIPs that hold this share of expected outages
MIN_ZONE_ZIPS = 8
MAX_ZONES = 4

_cache: dict = {}        # (id of the loaded state, storm strength) -> per-ZIP table


def _km(lat1, lon1, lat2, lon2):
    """Straight-line distance in km (haversine). Works on numbers or arrays."""
    p1, p2 = np.radians(lat1), np.radians(lat2)
    a = np.sin((p2 - p1) / 2) ** 2 + np.cos(p1) * np.cos(p2) * np.sin(np.radians(lon2 - lon1) / 2) ** 2
    return 2 * 6371.0 * np.arcsin(np.sqrt(a))


def _minutes(km) -> float:
    return km * ROAD_FACTOR / (DRIVE_MPH * 1.609) * 60


def _meta(state, assumptions: list[str], limits: list[str], fallback=False, reason=None) -> dict:
    return {"fallback": fallback, "fallback_reason": reason, "assumptions": assumptions, "limits": limits,
            "data_mode": state.data_mode, "request_id": ""}


def _unavailable(state, reason: str) -> dict:
    return {"available": False, "headline": reason,
            "meta": _meta(state, [], ["This view needs the real utility data."], True, reason)}


# ---------------------------------------------------------------- crew staging

def _circuit_table(state, severity: int) -> pd.DataFrame:
    """One row per circuit: its chance of damage in a storm of this strength and its expected customer-outages."""
    key = (id(state), severity)
    if key in _cache:
        return _cache[key]
    from .engines import _analog_of

    analog = _analog_of(state)
    rows = []
    for circuit_key, row in state.store.circuits.iterrows():
        if not (row.get("n_customers", 0) > 0) or pd.isna(row.get("center_lat")):
            continue
        chance = area_risk(state.model, analog, data._clean(row.to_dict()), severity, 0)["risk_score"]
        rows.append({"key": circuit_key, "lat": float(row.center_lat), "lon": float(row.center_lon),
                     "customers": float(row.n_customers), "risk": chance, "expected": chance * float(row.n_customers)})
    table = pd.DataFrame(rows)
    _cache[key] = table
    return table


def _zip_heat(state, circuits: pd.DataFrame) -> pd.DataFrame:
    """Spread each circuit's expected outages over the ZIPs it serves, so the map can show them by ZIP."""
    store = state.store
    expected = dict(zip(circuits.key, circuits.expected))
    customers = dict(zip(circuits.key, circuits.customers))
    rows = []
    for z in store.zip_index.itertuples():
        w = store.zip_weights[store.zip_weights.zip == z.zip]
        share = float(np.average([expected.get(k, 0.0) / max(customers.get(k, 1.0), 1.0) for k in w.key], weights=w.w)) if len(w) else 0.0
        rows.append({"zip": z.zip, "city": z.city, "lat": float(z.lat), "lon": float(z.lon),
                     "expected": share * float(z.customers)})
    return pd.DataFrame(rows)


def _nearest(table: pd.DataFrame, lat: float, lon: float) -> pd.Series:
    return table.iloc[int(np.argmin(_km(table.lat.values, table.lon.values, lat, lon)))]


def warm(state) -> None:
    """Work out every storm strength once at startup, so requests are answered from the cache."""
    try:
        for severity in range(1, 6):
            staging(state, severity)
    except Exception:  # noqa: BLE001 - warming is only an optimization
        pass


def staging(state, severity: int = 3, zones: int = 4) -> dict:
    """Where to park crews before a storm of this strength, and how much driving it saves."""
    from sklearn.cluster import KMeans

    store = state.store
    if state.data_mode != "live" or store.zip_index is None or store.zip_weights is None or "center_lat" not in store.circuits:
        return _unavailable(state, "Crew staging needs the real utility data, which is not loaded.")

    circuits = _circuit_table(state, severity)
    total = float(circuits.expected.sum())
    ranked = circuits.sort_values("expected", ascending=False)
    n_hot = max(int((ranked.expected.cumsum() / max(total, 1e-9) < COVERAGE).sum()) + 1, MIN_ZONE_ZIPS)
    hot = ranked.head(n_hot).copy()                                  # the circuits that hold most of the expected outages
    k = max(2, min(zones, MAX_ZONES, len(hot) // 2))

    xy = np.column_stack([hot.lat.values * 111.0, hot.lon.values * 111.0 * math.cos(math.radians(hot.lat.mean()))])
    weight = hot.expected.values + 1e-9
    hot["cluster"] = KMeans(n_clusters=k, n_init=10, random_state=42).fit(xy, sample_weight=weight).labels_

    zips = _zip_heat(state, circuits)
    spots = []
    for _, g in hot.groupby("cluster"):
        w = g.expected.values + 1e-9
        lat, lon = float(np.average(g.lat, weights=w)), float(np.average(g.lon, weights=w))
        spots.append({"lat": lat, "lon": lon, "anchor": _nearest(zips, lat, lon), "expected": float(g.expected.sum())})
    spots.sort(key=lambda s: s["expected"], reverse=True)

    # Travel: each likely-damage circuit's drive to the nearest staging zone, against one central depot.
    staged_km = np.min([_km(hot.lat.values, hot.lon.values, s["lat"], s["lon"]) for s in spots], axis=0)
    depot_lat = float(np.average(circuits.lat, weights=circuits.customers))
    depot_lon = float(np.average(circuits.lon, weights=circuits.customers))
    depot_km = _km(hot.lat.values, hot.lon.values, depot_lat, depot_lon)
    staged_avg, depot_avg = float(np.average(staged_km, weights=weight)), float(np.average(depot_km, weights=weight))
    cut = 1 - staged_avg / max(depot_avg, 1e-9)

    # Which ZIPs each zone covers: the ones whose centers are closest to it.
    nearest_zone = np.argmin([_km(zips.lat.values, zips.lon.values, s["lat"], s["lon"]) for s in spots], axis=0)
    peak = max(float(zips.expected.max()), 1e-9)
    zone_out = []
    for i, s in enumerate(spots):
        zone_out.append({
            "label": f"Zone {'ABCD'[i]}", "anchor_zip": s["anchor"].zip, "anchor_city": s["anchor"].city,
            "zips": sorted(zips[nearest_zone == i].zip), "share_of_expected": round(s["expected"] / max(total, 1e-9), 3),
            "expected_customers": round(s["expected"], 1),
        })

    depot_min, staged_min = round(_minutes(depot_avg)), round(_minutes(staged_avg))
    headline = (f"Staging crews in {len(zone_out)} zones cuts the average drive to likely damage from about {depot_min} to "
                f"{staged_min} minutes (about {round(cut * 100)}% shorter) than sending everyone from one central depot."
                if cut >= 0.05 else
                "Staging crews in zones barely shortens the drive for a storm this size, because the expected damage is "
                "spread out around the central depot.")
    return {
        "available": True,
        "storm_severity": severity,
        "zips": [{"zip": r.zip, "city": r.city, "heat": round(r.expected / peak, 3), "expected_customers": round(r.expected, 1)}
                 for r in zips.itertuples()],
        "zones": zone_out,
        "travel": {"depot_minutes": depot_min, "staged_minutes": staged_min, "reduction_pct": round(cut * 100),
                   "depot_label": "one central depot at the middle of the service area"},
        "expected_total": round(total, 1),
        "headline": headline,
        "meta": _meta(state, [
            "Expected customer-outages for a circuit = its chance of storm damage (the same risk model as the customer view) times customers served.",
            f"Zones come from clustering the circuits that hold {round(COVERAGE * 100)}% of expected outages, weighted by expected outages. Each zone is named by its nearest ZIP.",
            f"Travel compares each likely-damage circuit's drive to the nearest staging zone against one central depot. Driving is assumed {ROAD_FACTOR}x the straight line at {DRIVE_MPH} mph. The percent cut does not depend on those two numbers.",
            "The map shades ZIP codes by spreading each circuit's expected outages over the ZIPs it serves.",
            "The labor workbook folds travel into one indirect-labor multiplier, so this shows the direction of the saving, not a labor-hour figure.",
        ], [
            "Chance of damage is a model estimate, not a forecast for a specific street.",
            "Circuit centers are rough (the middle of each circuit's lines). Real staging also depends on yard locations, road closures and mutual aid, which this does not know.",
            "Storm strength changes how much damage is expected more than where. Circuits are ranked by size, line miles and past damage.",
        ]),
    }


# ---------------------------------------------------------------- weak spots

def weak_spots(state, top_n: int = 5) -> dict:
    """The few protective devices that lost the most customer-hours in past storms."""
    store = state.store
    w, summary = store.weak_spots, store.weak_summary
    if state.data_mode != "live" or w is None or not len(w) or not summary:
        return _unavailable(state, "The weak-spot view needs past damage records joined to the circuit map, which are not loaded.")

    labels = {}
    if store.locations is not None:
        labels = dict(zip(store.locations.key, store.locations.label))
    top = w.head(top_n)
    total = summary["total_customer_hours"]

    def area(key: str) -> str:
        if key in labels:
            return labels[key]
        city = store.circuits.city.get(key) if "city" in store.circuits else None
        return f"{str(city).title()} area" if isinstance(city, str) else "Roanoke area"

    items = []
    for i, r in enumerate(top.itertuples(), 1):
        items.append({
            "rank": i, "device_type": r.kind, "area": area(r.key), "damage_count": int(r.damage_count),
            "storms": int(r.storms), "customers_below_approx": data._approx(int(r.customers_below)),
            "avg_repair_hours": round(float(r.avg_repair_hours)), "share_of_customer_hours": round(float(r.customer_hours) / total, 3),
        })
    share = float(top.customer_hours.sum()) / total
    kinds = sorted({i["device_type"] for i in items})
    word = kinds[0] + "s" if len(kinds) == 1 else "devices"
    headline = (f"These {len(items)} {word} caused {round(share * 100)}% of all customer-hours lost to damaged fuses, reclosers "
                f"and switches across {summary['storms']} storms ({summary['first_storm']} to {summary['last_storm']}). "
                f"They are the first places to inspect and harden.")
    return {
        "available": True, "top": items, "top_share": round(share, 3), "headline": headline,
        "storms": summary["storms"], "devices_analyzed": summary["devices_with_damage"],
        "meta": _meta(state, [
            "Each damage record on a fuse, recloser or switch is matched to the nearest device of that type (within 100 meters).",
            "Customer-hours lost = customers below the device (from the circuit topology file) times hours the repair took.",
            f"Repair times over {data.WEAK_SPOT_MAX_REPAIR_DAYS} days look like bulk closing in the records, so the typical repair time for that device type is used instead.",
            f"The share is out of all customer-hours lost on the {summary['devices_with_damage']} protective devices that had damage records, not all outages.",
        ], [
            f"Only {summary['damage_records_matched']} of {summary['damage_records_device_type']} device damage records could be matched to a device. Poles and wires are not included.",
            "One big storm (Feb 2025) holds most of the records, so the ranking mostly reflects that storm.",
            "A device can rank high because many customers sit below it, not because it is poorly built. Inspect before spending.",
            "Devices are shown by type and area only. Exact locations are not shared.",
        ]),
    }
