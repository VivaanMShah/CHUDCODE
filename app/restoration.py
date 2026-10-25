"""Simulate repair times and compare storm/damage scenarios.

Edit PATH_ITEMS to change assumed damage by storm strength.
Crew count and road access are currently unused by this empirical model.
"""
from __future__ import annotations

import numpy as np

from . import data as C

# ============================================================================
# 2. RESTORATION
#
# "How long until the power is back?" is answered with a range, using real repair times:
#   - The records give the time it took to repair each damaged item in the biggest storm.
#   - We simulate 2000 customers. Each one has a few damaged items between them and the substation,
#     each repaired in a time drawn from the real list. Their power returns when the SLOWEST repair is done.
#   - The range shown is the 10th to 90th percentile of those 2000 results.
#
# The one assumption: how many damaged items sit on a customer's path (PATH_ITEMS below). The records
# do not say, so it grows with storm strength: about 1 on a calm day, about 4 in a severe storm.
# ============================================================================

MAX_ITEMS, MAX_POLES = 12, 20
PATH_ITEMS = [0.5, 1.0, 2.0, 3.0, 4.0]   # average damaged items on the path, for strength 1 to 5 (an assumption)


# Labor model. Hours come from the organizer's labor workbook (Install_Labor_HackOHIO.xlsx).
POLE_HOURS = 2.50 + 0.93          # pole, medium size: average install + removal crew-hours
ITEM_HOURS = 0.87                 # other damaged item (crossarm, bracket, transformer, connector, lead, anchor): average of their install + removal hours
INDIRECT_MULTIPLIER = 4.5         # workbook "ILM": drive time, safety talks, setup. One number, so travel and non-productive time are not split.
ACCESS_ADDER_HOURS = 0.5 + 1.312  # workbook adders per item when access is hard: "access constraint" + "remote location"
ROAD_CLEAR_HOURS = (3.0, 10.0)    # assumption: hours to clear blocked roads before repairs start
REGION_ITEMS = [2, 5, 12, 28, 55]  # assumption: damaged items crews must repair across the area, for strength 1 to 5
REFERENCE_CREWS = 2               # the real repair times reflect typical staffing, assumed to be this many crews


def _poisson_draw(uniform: np.ndarray, average: float) -> np.ndarray:
    """Turns random numbers between 0 and 1 into whole counts that average `average` (a Poisson count)."""
    if average <= 1e-9:
        return np.zeros_like(uniform)
    from scipy.stats import poisson

    largest = int(average + 12 * np.sqrt(average) + 15)
    cumulative = poisson.cdf(np.arange(largest + 1), average)
    return np.minimum(np.searchsorted(cumulative, uniform, side="left"), largest).astype(float)


class Draws:
    """The random numbers for one request.

    They come from a fixed seed, so the demo gives the same answer every time. Every "what if" reuses the
    same numbers, so the only thing that changes between the base answer and a what-if is the thing you changed."""

    def __init__(self, analog: dict, size: int = C.MC_SAMPLES, seed: int = C.MC_SEED):
        rng = np.random.default_rng(seed)
        self.count_random = rng.random(size)                     # decides how many items are on each path
        self.item_pick = rng.random((size, MAX_ITEMS))           # which real repair time each item gets
        self.item_within_day = rng.random((size, MAX_ITEMS))     # where in the day it finished
        self.pole_pick = rng.random((size, MAX_POLES))
        self.pole_within_day = rng.random((size, MAX_POLES))
        self.queue_position = rng.random(size)                   # where in the repair line this customer sits (0 = first)
        self.road_clear_random = rng.random(size)                # how long blocked roads take to clear
        self.repair_days = np.array(analog["repair_days"], float)         # real repair times, in whole days
        self.pole_repair_days = np.array(analog["pole_repair_days"], float)

    def hours_until_power_returns(self, severity: int, damaged_poles: int,
                                  crews: int = REFERENCE_CREWS, access_ok: bool = True) -> np.ndarray:
        """One result per simulated customer."""
        items_on_path = np.clip(_poisson_draw(self.count_random, PATH_ITEMS[severity - 1]), 1, MAX_ITEMS)

        # Real repair times are whole days, so spread each one randomly within its day.
        days = self.repair_days[(self.item_pick * len(self.repair_days)).astype(int)]
        hours = (days + self.item_within_day) * 24

        # Only the first `items_on_path` columns count for each customer. The slowest one decides.
        counts = np.arange(MAX_ITEMS)[None, :] < items_on_path[:, None]
        wait = np.where(counts, hours, 0.0).max(axis=1)

        poles = min(damaged_poles, MAX_POLES)
        if poles > 0:
            # Each damaged pole the customer entered adds a repair drawn from real pole repair times.
            pole_days = self.pole_repair_days[(self.pole_pick[:, :poles] * len(self.pole_repair_days)).astype(int)]
            wait = np.maximum(wait, ((pole_days + self.pole_within_day[:, :poles]) * 24).max(axis=1))

        # Crews and road access. The real repair times above reflect typical staffing, so we only add the
        # difference: how long this customer waits in the repair line with these crews, minus with typical crews.
        adder = 0.0 if access_ok else ACCESS_ADDER_HOURS
        region_hours = lambda extra: (poles * (POLE_HOURS + extra) + REGION_ITEMS[severity - 1] * (ITEM_HOURS + extra)) * INDIRECT_MULTIPLIER
        line_wait = self.queue_position * (region_hours(adder) / crews - region_hours(0.0) / REFERENCE_CREWS)
        road_delay = 0.0 if access_ok else ROAD_CLEAR_HOURS[0] + (ROAD_CLEAR_HOURS[1] - ROAD_CLEAR_HOURS[0]) * self.road_clear_random
        own_work = items_on_path * (ITEM_HOURS + adder) * INDIRECT_MULTIPLIER   # the repairs on this customer's own path
        return np.maximum(wait + line_wait + road_delay, own_work + road_delay + 1.0)


def summarize(hours: np.ndarray) -> dict:
    """Turn 2000 results into the range we show: 10th percentile, middle, 90th percentile."""
    low, middle, high = np.percentile(hours, [10, 50, 90])
    return {"low_hours": round(float(low), 1), "median_hours": round(float(middle), 1),
            "high_hours": round(float(max(high, low + 0.5)), 1), "method": "empirical_monte_carlo_p10_p90"}


def _change_text(base: dict, new: dict) -> str:
    """'about 3.5 hours faster', comparing the middle of two ranges."""
    saved = base["median_hours"] - new["median_hours"]
    if abs(saved) < 0.5:
        return "about the same"
    return f"about {abs(saved):.1f} hours {'faster' if saved > 0 else 'slower'}"


def histogram(hours: np.ndarray, bins: int = 12) -> list[dict]:
    """How many of the 2000 results fell in each time bucket (ignoring the 2% most extreme at each end)."""
    low, high = np.percentile(hours, [2, 98])
    if high - low < 1e-6:
        high = low + 1.0
    counts, edges = np.histogram(np.clip(hours, low, high), bins=bins, range=(low, high))
    total = max(int(counts.sum()), 1)
    return [{"low_hours": round(float(edges[i]), 1), "high_hours": round(float(edges[i + 1]), 1),
             "share": float(round(counts[i] / total, 3))} for i in range(bins)]


# Milestones for "will I outlast it?". Food-safety times are the USDA guidance: a closed fridge keeps food
# safe about 4 hours, a full freezer about 48.
MILESTONES = [(4, "Fridge food is still safe"), (12, "Overnight"), (24, "One full day"),
              (48, "Full freezer is still safe"), (72, "Three days")]


def odds_by_milestone(hours: np.ndarray) -> list[dict]:
    """The chance power is back by each milestone, from the same simulated results as the range."""
    return [{"hours": h, "chance": float(round((hours <= h).mean(), 3)), "label": label} for h, label in MILESTONES]


def estimate_restoration(analog: dict, scenario) -> dict:
    """The restoration range, plus "what if" ranges: a weaker storm, a stronger one, half the poles."""
    draws = Draws(analog)
    strength, poles = scenario.storm_severity, scenario.damaged_poles

    
    crews, access_ok = scenario.crews, scenario.access_ok

    def run(**changes):
        args = {"severity": strength, "damaged_poles": poles, "crews": crews, "access_ok": access_ok, **changes}
        return draws.hours_until_power_returns(**args)

    base_hours = run()
    base = summarize(base_hours)

    what_ifs = []   # (label, arguments to change, scenario fields to change to try it)
    if not access_ok:
        what_ifs.append(("roads cleared", {"access_ok": True}, {"access_ok": True}))
    if crews < 50:
        what_ifs.append((f"{min(crews + 2, 50) - crews} more crews", {"crews": min(crews + 2, 50)}, {"crews": min(crews + 2, 50)}))
    if crews > 1:
        what_ifs.append(("one fewer crew", {"crews": crews - 1}, {"crews": crews - 1}))
    if strength > 1:
        what_ifs.append(("storm one level weaker", {"severity": strength - 1}, {"storm_severity": strength - 1}))
    if strength < 5:
        what_ifs.append(("storm one level stronger", {"severity": strength + 1}, {"storm_severity": strength + 1}))
    if poles > 1:
        what_ifs.append(("half as many damaged poles", {"damaged_poles": poles // 2}, {"damaged_poles": poles // 2}))

    deltas = []
    for label, changes, patch in what_ifs:
        new = summarize(run(**changes))
        deltas.append({"change": label, "new_range": new, "delta_text": _change_text(base, new), "scenario_patch": patch})

    spread = (base["high_hours"] - base["low_hours"]) / max(base["median_hours"], 1e-6)
    return {"range": base, "deltas": deltas, "spread": spread,
            "histogram": histogram(base_hours), "odds": odds_by_milestone(base_hours), "simulations": int(base_hours.size)}


def spread_confidence(spread: float) -> str:
    """A narrow range (compared with its middle) means we are more sure."""
    if spread <= 1.0:
        return "high"
    return "medium" if spread <= 2.0 else "low"
