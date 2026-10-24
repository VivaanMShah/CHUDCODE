"""Location ID -> circuit features, upstream device, past damage. Coarse output only."""
from __future__ import annotations

import math
from dataclasses import dataclass

from .data import Store, _customers_band


@dataclass
class Resolved:
    location_id: str
    key: str            # internal circuit key, never returned to clients
    label: str
    features: dict
    customers_served_band: str


def resolve(store: Store, location_id: str) -> Resolved | None:
    locs = store.locations
    if locs is None:
        return None
    row = locs[locs.location_id == location_id]
    if row.empty:
        return None
    r = row.iloc[0]
    feats = store.circuits.loc[r.key].to_dict()
    feats = {k: (None if isinstance(v, float) and math.isnan(v) else v) for k, v in feats.items()}
    return Resolved(location_id, r.key, r.label, feats, r.customers_served_band)


def upstream_band(n) -> str | None:
    return None if n is None else _customers_band(n)
