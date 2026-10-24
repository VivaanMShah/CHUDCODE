"""Location ID -> area features. Two kinds of ID:

  LOC-012      one circuit (a neighborhood-sized area)
  ZIP-24014    a typical circuit for that ZIP: the customer-weighted blend of the circuits serving it

Only coarse, privacy-safe fields leave this module.
"""
from __future__ import annotations

import math
from dataclasses import dataclass

import numpy as np

from .data import Store, _customers_band


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
