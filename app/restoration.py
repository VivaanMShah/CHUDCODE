"""Restoration engine: Monte Carlo over damage counts, labor, travel, crew availability.

hours = dispatch_delay + queue_position * shift_factor(
            sum(count_c * labor_hours_c * labor_noise_c) * travel * nonproductive / available_crews)

Returns the 10th-90th percentile range. Fixed seed and common random numbers, so
counterfactuals ("+1 crew") differ only because of the input that changed.
"""
from __future__ import annotations

import numpy as np

from . import config as C

CLASSES = list(C.PLACEHOLDER_LABOR_HOURS.keys())
WORK_HOURS_PER_DAY = 16  # storm crews typically work 16-hour shifts


def expected_damage(feats: dict, store, severity: int, damaged_poles: int) -> dict:
    """Expected damage items per class for this area and storm.

    Blends this area's own damage in the reference storm with the system average per
    mile of line (50/50), then scales by severity. If the user sets damaged_poles, poles
    are fixed and other damage follows the system's wire-per-pole mix.
    """
    circ = store.circuits
    miles = feats.get("primary_miles") or float(circ.primary_miles.median())
    total_miles = float(circ.primary_miles.sum())
    lam = {}
    for c in CLASSES:
        own = float(feats.get(f"ref_{c}") or 0)
        system_rate = float(circ[f"ref_{c}"].sum()) / total_miles if f"ref_{c}" in circ else 0.0
        lam[c] = (0.5 * own + 0.5 * system_rate * miles) * C.SEVERITY_DAMAGE_MULT[severity]
    if damaged_poles > 0:
        mix = store.damage_mix
        pole_share = mix.get("pole") or 0.2
        for c in CLASSES:
            if c != "pole":
                lam[c] = max(lam[c], damaged_poles * mix.get(c, 0.0) / pole_share)
        lam["pole"] = float(damaged_poles)
    return lam


def _poisson_inverse(u: np.ndarray, lam: float) -> np.ndarray:
    """Poisson quantile for uniform draws u (inverse CDF via a small lookup table)."""
    if lam <= 1e-9:
        return np.zeros_like(u)
    from scipy.stats import poisson

    kmax = int(lam + 12 * np.sqrt(lam) + 15)
    cdf = poisson.cdf(np.arange(kmax + 1), lam)
    return np.minimum(np.searchsorted(cdf, u, side="left"), kmax).astype(float)


class Simulator:
    """Holds the random draws for one request so every counterfactual reuses them."""

    def __init__(self, n: int = C.MC_SAMPLES, seed: int = C.MC_SEED):
        rng = np.random.default_rng(seed)
        self.n = n
        self.u_counts = rng.random((n, len(CLASSES)))
        self.labor_noise = rng.lognormal(0.0, 0.25, (n, len(CLASSES)))
        self.travel_noise = rng.lognormal(0.0, 0.2, n)
        self.blocked_noise = rng.uniform(0.8, 1.3, n)
        self.u_crews = rng.random((n, 50))
        self.delay_u = rng.random(n)
        self.queue = rng.beta(3, 2, n)  # where this area falls in the repair order

    def run(self, lam: dict, poles_fixed: bool, crews: int, access_ok: bool, severity: int) -> np.ndarray:
        counts = np.empty((self.n, len(CLASSES)))
        for j, c in enumerate(CLASSES):
            counts[:, j] = _poisson_inverse(self.u_counts[:, j], lam[c])
        if poles_fixed:
            counts[:, CLASSES.index("pole")] = lam["pole"]
        # Restoration is conditional on an outage: at least one repair item.
        none = counts.sum(axis=1) == 0
        counts[none, CLASSES.index("fuse_switch")] = 1
        hours = np.array([C.PLACEHOLDER_LABOR_HOURS[c] for c in CLASSES])
        labor = (counts * hours * self.labor_noise).sum(axis=1)
        travel = C.PLACEHOLDER_TRAVEL_MULT * self.travel_noise
        if not access_ok:
            travel = travel * C.BLOCKED_ROADS_TRAVEL_MULT * self.blocked_noise
        crews_avail = np.maximum(1, (self.u_crews[:, :crews] < C.CREW_AVAILABILITY).sum(axis=1))
        work = labor * travel * C.PLACEHOLDER_NONPRODUCTIVE_MULT / crews_avail
        wall = work * self.queue * (24 / WORK_HOURS_PER_DAY)
        delay = 1.0 + self.delay_u * (1.0 + severity)  # damage assessment and dispatch
        return delay + wall


def summarize(samples: np.ndarray) -> dict:
    lo, med, hi = np.percentile(samples, [10, 50, 90])
    def r(x):
        return float(round(x, 1))
    return {"low_hours": r(lo), "median_hours": r(med), "high_hours": r(max(hi, lo + 0.5)),
            "method": "monte_carlo_p10_p90"}


def _delta_text(base: dict, new: dict) -> str:
    d = base["median_hours"] - new["median_hours"]
    if abs(d) < 0.5:
        return "about the same"
    return f"about {abs(d):.1f} hours {'faster' if d > 0 else 'slower'}"


def histogram(samples: np.ndarray, bins: int = 12) -> list[dict]:
    """Share of simulations in each time bucket, between the 2nd and 98th percentile."""
    lo, hi = np.percentile(samples, [2, 98])
    if hi - lo < 1e-6:
        hi = lo + 1.0
    counts, edges = np.histogram(np.clip(samples, lo, hi), bins=bins, range=(lo, hi))
    total = max(int(counts.sum()), 1)
    return [{"low_hours": float(round(edges[i], 1)), "high_hours": float(round(edges[i + 1], 1)),
             "share": float(round(counts[i] / total, 3))} for i in range(bins)]


# Scenario field names used in what-if patches (match the API's Scenario model).
_FIELD = {"sev": "storm_severity", "poles": "damaged_poles", "crews": "crews", "access": "access_ok"}


def estimate(feats: dict, store, scenario) -> dict:
    sim = Simulator()
    sev, poles, crews, access = (scenario.storm_severity, scenario.damaged_poles,
                                 scenario.crews, scenario.access_ok)

    def go(sev=sev, poles=poles, crews=crews, access=access):
        lam = expected_damage(feats, store, sev, poles)
        samples = sim.run(lam, poles > 0, min(crews, 50), access, sev)
        return summarize(samples), lam, samples

    base, lam, samples = go()
    variants = []
    if crews < 50:
        variants.append(("+1 crew", dict(crews=crews + 1)))
    if crews <= 48:
        variants.append(("+2 crews", dict(crews=crews + 2)))
    variants.append(("roads cleared", dict(access=True)) if not access else ("roads blocked", dict(access=False)))
    if poles > 1:
        variants.append(("half as many damaged poles", dict(poles=poles // 2)))
    if sev > 1:
        variants.append(("storm one level weaker", dict(sev=sev - 1)))
    deltas = []
    for label, kw in variants:
        new, _, _ = go(**kw)
        deltas.append({"change": label, "new_range": new, "delta_text": _delta_text(base, new),
                       "scenario_patch": {_FIELD[k]: v for k, v in kw.items()}})
    spread = (base["high_hours"] - base["low_hours"]) / max(base["median_hours"], 1e-6)
    return {"range": base, "deltas": deltas, "expected_damage": lam, "spread": spread,
            "histogram": histogram(samples), "simulations": int(samples.size)}


def spread_confidence(spread: float) -> str:
    if spread <= 1.0:
        return "high"
    if spread <= 2.0:
        return "medium"
    return "low"
