"""The engines: everything that turns a location and a storm into an answer.

HOW ONE REQUEST FLOWS (this is what `assess()` at the bottom of the file does)
    1. Look up the location          -> data.resolve()           which circuit(s), how long are the lines, ...
    2. Rate the risk                 -> area_risk()              "chance of storm damage near you"
    3. Estimate restoration time     -> estimate_restoration()   a range of hours, from real repair times
    4. Work out how sure we are      -> _confidence()            from sample sizes and model tests
    5. Decide tips, alerts, channel  -> build_rules()
    6. Package it all up             -> assess()

THE FILE IS IN SIX PARTS
    1. RISK          how exposed is an area, and what is the chance of damage in a storm this strong
    2. RESTORATION   how long until the power is back (a range, never one number)
    3. RULES         tips, safety messages, who gets alerted, by what channel
    4. WEATHER       live forecast -> storm strength 1 to 5
    5. TRAINING      builds the tables the weather part uses (python -m app.engines train-weather)
    6. REQUEST FLOW  startup, assess(), message()

Nothing here makes up a number without saying so. Where a number is an assumption, it is named as one
and listed in the response under meta.assumptions.
"""
from __future__ import annotations

import json
import logging
import math
import sys
import time
import urllib.parse
import urllib.request
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta, timezone
from zoneinfo import ZoneInfo

import numpy as np
import pandas as pd
from sklearn.linear_model import LogisticRegression
from sklearn.metrics import average_precision_score, brier_score_loss, roc_auc_score
from sklearn.model_selection import StratifiedKFold, cross_val_predict
from sklearn.pipeline import make_pipeline
from sklearn.preprocessing import StandardScaler

from . import data
from .assistant import generate, validate
from .data import Store, resolve, upstream_band

C = data  # settings (sizes, paths, seeds) live at the top of data.py
log = logging.getLogger("engines")


# ============================================================================
# 1. RISK
#
# Two ideas are combined:
#   a) EXPOSURE: some areas are simply more likely to be hit (more miles of line, more customers,
#      more past damage). A small model learned this from the biggest storm on record.
#   b) STRENGTH: how often areas actually had storm damage on days of each storm strength.
#      These rates come from the utility's damage records (see build_analog in the TRAINING part).
#
# The risk shown to the customer is looked up from (b) for areas as exposed as theirs (a).
# ============================================================================

RISK_FEATURES = ["log_primary_miles", "log_customers", "log_history"]

# Bands for the rating. "chance" means: chance of storm damage within about 2 days.
LOW_BELOW, MEDIUM_BELOW = 0.01, 0.04  # under 1% is low (about a normal day), under 4% medium, else high

# Confidence from the number of similar days in the records.
SIMILAR_DAYS_FULL, SIMILAR_DAYS_OK = 30, 8

# Only used if the trained tables file is missing. It has no similar days, so confidence is low and
# the app shows its "we can't predict this reliably" state.
FALLBACK_ANALOG = {
    "rate": [[0.001, 0.002, 0.003], [0.004, 0.01, 0.02], [0.01, 0.03, 0.06], [0.03, 0.08, 0.17]],
    "all": [0.002, 0.011, 0.03, 0.08], "days": [0, 0, 0, 0], "base_rate": 0.002, "cuts": [0.04, 0.1, 0.24, 0.39],
    "repair_days": [0, 1, 1, 1, 2, 2, 3], "pole_repair_days": [0, 1, 2, 3],
    "repair_source": "placeholder values (the trained tables are missing)",
}


@dataclass
class RiskModel:
    """The area-exposure model plus the numbers from testing it."""
    pipe: object = None                             # the trained scikit-learn model
    medians: dict = field(default_factory=dict)     # fills in missing inputs
    auc: float | None = None                        # test score (1.0 perfect, 0.5 coin flip)
    brier: float | None = None
    base_rate: float | None = None                  # share of areas damaged in the reference storm
    n_train: int = 0                                # how many areas it learned from
    hit_rates: dict = field(default_factory=dict)   # tested: share of areas with damage, by predicted band
    coef: dict = field(default_factory=dict)
    means: dict = field(default_factory=dict)
    stds: dict = field(default_factory=dict)
    ref_sorted: object = None                       # every area's score, sorted, to rank one area against the rest

    @property
    def ok(self) -> bool:
        return self.pipe is not None


def _model_inputs(circuits: pd.DataFrame, history_column: str) -> pd.DataFrame:
    """The three numbers the exposure model looks at, for many circuits at once."""
    return pd.DataFrame({
        "log_primary_miles": np.log1p(circuits["primary_miles"]),
        "log_customers": np.log1p(circuits["n_customers"]),
        "log_history": np.log1p(circuits[history_column]),
    }, index=circuits.index)


def _backtest_band(p: float) -> str:
    """Sorts the exposure model's test predictions into low / medium / high (only for the backtest)."""
    for cutoff, name in C.RISK_BANDS:
        if p < cutoff:
            return name
    return "high"


def train_risk(store: Store) -> RiskModel:
    """Train the exposure model: which circuits had damage in the biggest storm?

    It learns only from history BEFORE that storm, and is tested on that storm, so the test score
    is honest (the model never saw the answer)."""
    model = RiskModel()
    circuits = store.circuits
    if circuits is None or not store.has("arcos") or "primary_miles" not in circuits:
        log.warning("risk: missing data, model not trained")
        return model

    circuits = circuits[circuits.get("n_customers", 0) >= C.MIN_CUSTOMERS_PER_LOCATION]
    inputs = _model_inputs(circuits, "pre_records")        # history before the storm
    model.medians = inputs.median().to_dict()
    inputs = inputs.fillna(model.medians)
    had_damage = (circuits["ref_records"] >= 1).astype(int).values   # 1 = this circuit was hit in the storm
    if had_damage.sum() < 5 or (len(had_damage) - had_damage.sum()) < 5:
        log.warning("risk: target too unbalanced to train")
        return model

    pipe = make_pipeline(StandardScaler(), LogisticRegression(C=1.0, max_iter=1000))

    # Test: 5-fold cross-validation. Each circuit is predicted by a model that did not see it.
    folds = StratifiedKFold(n_splits=5, shuffle=True, random_state=C.MC_SEED)
    tested = cross_val_predict(pipe, inputs, had_damage, cv=folds, method="predict_proba")[:, 1]
    model.auc = float(roc_auc_score(had_damage, tested))
    model.brier = float(brier_score_loss(had_damage, tested))
    model.base_rate = float(had_damage.mean())
    model.n_train = int(len(had_damage))
    predicted_band = np.array([_backtest_band(p) for p in tested])
    for band in ("high", "low"):
        in_band = predicted_band == band
        model.hit_rates[band] = {"areas": int(in_band.sum()),
                                 "rate": float(had_damage[in_band].mean()) if in_band.any() else None}

    # Final model: trained on every circuit.
    pipe.fit(inputs, had_damage)
    model.pipe = pipe
    scaler, logistic = pipe.steps[0][1], pipe.steps[1][1]
    model.coef = dict(zip(RISK_FEATURES, logistic.coef_[0]))
    model.means = dict(zip(RISK_FEATURES, scaler.mean_))
    model.stds = dict(zip(RISK_FEATURES, scaler.scale_))
    model.ref_sorted = np.sort(pipe.predict_proba(inputs)[:, 1])
    log.info("risk: trained on %d circuits, backtest AUC %.3f, Brier %.3f, base rate %.2f",
             model.n_train, model.auc, model.brier, model.base_rate)
    return model


def _feature_text(name: str, feats: dict, above_average: bool) -> str:
    """One plain-English sentence about an input, for the 'why' list."""
    if name == "log_primary_miles":
        miles = round(feats.get("primary_miles") or 0)
        if above_average:
            return f"This area has about {miles} miles of overhead power lines, more than most areas."
        return f"This area has only about {miles} miles of overhead power lines, fewer than most areas."
    if name == "log_customers":
        if above_average:
            return "This area serves more homes and businesses than most, so there is more equipment storms can damage."
        return "This area serves fewer homes and businesses than most, so there is less equipment to damage."
    if name == "log_history":
        reports = int(feats.get("hist_records") or 0)
        if reports == 0:
            return "We have no record of storm damage in this area."
        word = "report" if reports == 1 else "reports"
        if above_average:
            return f"This area has had frequent storm damage in past events ({reports} damage {word})."
        return f"This area has had less storm damage than most in past events ({reports} damage {word})."
    return name


def area_score(model: RiskModel, feats: dict) -> dict:
    """How exposed this area is compared with every other area.

    Returns the model's score, the area's percentile (0 = least exposed, 1 = most exposed), and a list of
    (input name, push on the score, sentence) used to explain the rating."""
    pushes = []
    if model.ok:
        row = pd.DataFrame([{
            "log_primary_miles": np.log1p(feats.get("primary_miles") or np.nan),
            "log_customers": np.log1p(feats.get("n_customers") or np.nan),
            "log_history": np.log1p(feats.get("hist_records") or 0),
        }], columns=RISK_FEATURES).astype(float).fillna(model.medians)
        score = float(model.pipe.predict_proba(row)[0, 1])
        for name in RISK_FEATURES:
            how_far_from_average = (row.iloc[0][name] - model.means[name]) / model.stds[name]
            pushes.append((name, model.coef[name] * how_far_from_average,
                           _feature_text(name, feats, how_far_from_average > 0)))
    else:
        score = 0.5
    everyone = model.ref_sorted
    percentile = float(np.searchsorted(everyone, score) / len(everyone)) if everyone is not None and len(everyone) else 0.5
    return {"p_storm": score, "percentile": percentile, "contribs": pushes}


def risk_band_for(chance: float) -> str:
    if chance < LOW_BELOW:
        return "low"
    return "medium" if chance < MEDIUM_BELOW else "high"


def severity_row(severity: int) -> int:
    """Which row of the history tables to use. Strengths 4 and 5 share a row: the records hold only 15 such days."""
    return min(severity, 4) - 1


def sample_confidence(similar_days: int) -> str:
    if similar_days >= SIMILAR_DAYS_FULL:
        return "high"
    return "medium" if similar_days >= SIMILAR_DAYS_OK else "low"


def area_risk(model: RiskModel, analog: dict, feats: dict, severity: int, damaged_poles: int) -> dict:
    """The chance this area has storm damage within about 2 days.

    Steps: (1) rank the area by exposure, (2) take the row of the history table for this storm
    strength, (3) read off the rate for an area with that exposure (blending between the table's
    low / middle / high exposure columns)."""
    exposure = area_score(model, feats)
    row = severity_row(severity)

    # The table has three columns: low, middle, and high exposure (at the 1/6, 1/2 and 5/6 points of the ranking).
    rates = np.maximum(np.array(analog["rate"][row], float), 1e-4)
    chance = float(np.exp(np.interp(exposure["percentile"], [1 / 6, 1 / 2, 5 / 6], np.log(rates))))
    similar_days = int(analog["days"][row])

    if damaged_poles > 0:
        # The customer is telling us poles are down, so damage is assumed. Each pole makes damage more certain.
        chance = 1 - (1 - chance) * math.exp(-0.5 * damaged_poles)
    chance = min(max(chance, 0.0), 0.99)

    # The "why" list: storm strength first, then poles (if any), then the area's own traits.
    label = "4 or 5" if row == 3 else str(severity)
    why = [{
        "factor": "storm_severity",
        "text": (f"Storm strength is {severity} of 5. On the {similar_days} days in the records rated {label} of 5, "
                 f"{analog['all'][row] * 100:.1f}% of areas had storm damage."),
        "direction": "increases_risk" if severity >= 2 else "decreases_risk",
    }]
    if damaged_poles > 0:
        why.append({"factor": "damaged_poles", "text": f"{damaged_poles} damaged poles are assumed in this scenario.",
                    "direction": "increases_risk"})
    for name, push, sentence in sorted(exposure["contribs"], key=lambda item: abs(item[1]), reverse=True):
        why.append({"factor": "history" if name == "log_history" else name.replace("log_", ""), "text": sentence,
                    "direction": "increases_risk" if push > 0 else "decreases_risk"})

    return {
        "risk_score": round(chance, 4),
        "risk_band": risk_band_for(chance),
        "reasons": why[:3],
        "similar_days": similar_days,
        "times_normal": round(chance / max(analog["base_rate"], 1e-4), 1),   # compared with a calm day
        "percentile": exposure["percentile"],
    }


def model_confidence(model: RiskModel) -> str:
    """How well the exposure model did in its test (AUC 0.8 or more is high)."""
    if not model.ok or model.auc is None:
        return "low"
    if model.auc >= 0.80:
        return "high"
    return "medium" if model.auc >= 0.70 else "low"


def history_confidence(feats: dict) -> str:
    """How much damage history this particular area has."""
    reports = int(feats.get("hist_records") or 0)
    if reports >= 10:
        return "high"
    return "medium" if reports >= 1 else "low"


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
        self.repair_days = np.array(analog["repair_days"], float)         # real repair times, in whole days
        self.pole_repair_days = np.array(analog["pole_repair_days"], float)

    def hours_until_power_returns(self, severity: int, damaged_poles: int) -> np.ndarray:
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
        return np.maximum(wait, 1.0)


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


def estimate_restoration(analog: dict, scenario) -> dict:
    """The restoration range, plus "what if" ranges: a weaker storm, a stronger one, half the poles."""
    draws = Draws(analog)
    strength, poles = scenario.storm_severity, scenario.damaged_poles

    base_hours = draws.hours_until_power_returns(strength, poles)
    base = summarize(base_hours)

    what_ifs = []   # (label, storm strength to use, poles to use, scenario fields to change to try it)
    if strength > 1:
        what_ifs.append(("storm one level weaker", strength - 1, poles, {"storm_severity": strength - 1}))
    if strength < 5:
        what_ifs.append(("storm one level stronger", strength + 1, poles, {"storm_severity": strength + 1}))
    if poles > 1:
        what_ifs.append(("half as many damaged poles", strength, poles // 2, {"damaged_poles": poles // 2}))

    deltas = []
    for label, what_if_strength, what_if_poles, patch in what_ifs:
        new = summarize(draws.hours_until_power_returns(what_if_strength, what_if_poles))
        deltas.append({"change": label, "new_range": new, "delta_text": _change_text(base, new), "scenario_patch": patch})

    spread = (base["high_hours"] - base["low_hours"]) / max(base["median_hours"], 1e-6)
    return {"range": base, "deltas": deltas, "spread": spread,
            "histogram": histogram(base_hours), "simulations": int(base_hours.size)}


def spread_confidence(spread: float) -> str:
    """A narrow range (compared with its middle) means we are more sure."""
    if spread <= 1.0:
        return "high"
    return "medium" if spread <= 2.0 else "low"


# ============================================================================
# 3. RULES
#
# Plain if-this-then-that rules. Each decision names the rule that made it (R0, R1, ...) so you can trace
# why someone was or was not alerted. Needs such as medical equipment come ONLY from what the customer
# chose to tell us. Nothing is guessed.
# ============================================================================

SAFETY_MESSAGES = [
    "Stay at least 30 feet from downed power lines and report them right away.",
    "Never run a generator indoors or in a garage.",
]


def _needs_of(scenario) -> list[str]:
    """The needs the customer declared, as plain text."""
    return [need.value if hasattr(need, "value") else need for need in scenario.declared_needs]


def tips_and_safety(band: str, longest_hours: float, needs: list[str], backup_hours: float | None) -> tuple[list[str], list[str]]:
    """What to do (up to 6 tips) and safety messages, based on risk band, how long it could last, and declared needs."""
    tips = []
    if "medical_equipment" in needs:
        tips.append("Confirm backup power for medical equipment, or plan where you will go if it runs out.")
        if backup_hours is not None and longest_hours > backup_hours:
            tips.append("This outage could outlast your backup. Arrange a place with power now.")
    if "accessibility" in needs:
        tips.append("Ask a neighbor, friend, or family member to check on you during the storm.")
    if "critical_facility" in needs:
        tips.append("Test your backup generator and confirm you have enough fuel for a long outage.")

    tips.append("Charge phones and power banks.")
    if band in ("medium", "high"):
        tips.append("Keep flashlights and batteries where you can find them.")
        tips.append("Store at least a day of drinking water.")
    if longest_hours >= 12:
        tips.append("Plan to keep the fridge closed to keep food cold.")
    if longest_hours >= 24:
        tips.append("Keep a few days of medicine and shelf-stable food on hand.")
    if longest_hours >= 72:
        tips.append("Pick a place you can stay, like family or a shelter, if the outage lasts several days.")
    if band == "low" and len(tips) < 3:
        tips.append("Sign up for outage alerts so you hear about changes quickly.")

    safety = list(SAFETY_MESSAGES)
    if longest_hours >= 24:
        safety.append("If you use a generator, keep it outside and at least 20 feet from doors and windows.")
    return tips[:6], safety


def alert_decision(band: str, longest_hours: float, needs: list[str], backup_hours: float | None) -> tuple[bool, str]:
    """Should this customer get a proactive alert? Returns (yes or no, the rule that decided)."""
    if "medical_equipment" in needs:
        backup = backup_hours if backup_hours is not None else C.MEDICAL_BACKUP_HOURS
        if longest_hours > backup:
            return True, f"R2: declared medical equipment, and the outage could last longer than your backup ({backup:g} hours)"
        if band != "low":
            return True, "R2: declared medical equipment need and outage risk"
    if band in ("medium", "high"):
        return True, "R1: risk band medium or high"
    if longest_hours >= 24 and band != "low":
        return True, "R3: restoration could take more than 24 hours"
    return False, "R0: low risk and short expected outage, no proactive alert"


def channel_and_timing(band: str, needs: list[str]) -> tuple[str, str]:
    """How and when we would reach this customer."""
    if "medical_equipment" in needs:
        return "phone_call", "Call 48 hours before the storm arrives"   # a day earlier than the standard high-risk alert
    if "critical_facility" in needs:
        return "phone_call", "Call 24 hours before the storm arrives"
    if band == "high":
        return "sms", "Send 24 hours before the storm arrives"
    if band == "medium":
        return "push", "Send 12 hours before the storm arrives"
    return "email", "Include in the daily storm update"


def escalation_plan(longest_hours: float, needs: list[str], backup_hours: float | None) -> dict:
    """What triggers a follow-up, and what the follow-up is."""
    if "medical_equipment" in needs:
        limit = f"your backup ({backup_hours:g} hours)" if backup_hours is not None else f"backup power (about {C.MEDICAL_BACKUP_HOURS} hours)"
        return {"trigger": f"Outage expected to last longer than {limit}", "action": "Flag for a utility follow-up call"}
    if "critical_facility" in needs:
        return {"trigger": "Facility outage expected to last more than 4 hours", "action": "Notify the utility account representative"}
    if longest_hours >= 48:
        return {"trigger": "Estimate grows by more than 12 hours", "action": "Send an estimate_updated message and share shelter information"}
    return {"trigger": "Estimate grows by more than 6 hours", "action": "Send an estimate_updated message"}


def build_rules(band: str, restoration_range: dict, scenario) -> dict:
    """Runs all the rules above and returns what the app shows under 'customer_actions'."""
    needs = _needs_of(scenario)
    longest = restoration_range["high_hours"]
    backup = scenario.backup_hours if "medical_equipment" in needs else None   # only used if they told us
    tips, safety = tips_and_safety(band, longest, needs, backup)
    eligible, rule = alert_decision(band, longest, needs, backup)
    channel, timing = channel_and_timing(band, needs)
    return {
        "customer_actions": {"actions": tips, "safety_messages": safety, "channel": channel,
                             "send_timing": timing, "escalation": escalation_plan(longest, needs, backup)},
        "alert_eligible": eligible,
        "alert_rule": rule,
    }


# ============================================================================
# 4. WEATHER
#
# Live forecast -> storm strength 1 to 5.
#   - We ask Open-Meteo (free, no key) for the forecast at the ZIP's center point.
#   - A small model turns gusts and rain into the chance that the day is a "damaging day".
#   - That chance becomes a strength from 1 to 5 using cut points built during training.
# Only the ZIP's public center point is ever sent to the weather services.
# ============================================================================

TZ = ZoneInfo("America/New_York")
WEATHER_FEATURES = ["gust", "gust_max2", "precip", "precip_prev", "precip3"]
SEVERITY_LABELS = {1: "Calm", 2: "Breezy or wet", 3: "Stormy", 4: "Damaging storm", 5: "Severe storm"}
CACHE_SECONDS = 600           # forecasts are reused for 10 minutes
_cache: dict = {}


def make_features(daily: pd.DataFrame) -> pd.DataFrame:
    """The five numbers the weather model uses, for each day.

    `daily` has one row per day, oldest first, with the day's top gust (mph) and total rain (inches).
    Training and live use share this function, so they always compute the numbers the same way."""
    gust, rain = daily["gust"], daily["precip"]
    return pd.DataFrame({
        "gust": gust,
        "gust_max2": np.maximum(gust, gust.shift(1)),   # strongest gust today or yesterday
        "precip": rain,
        "precip_prev": rain.shift(1),                   # yesterday's rain
        "precip3": rain.rolling(3).sum(),               # rain over the last 3 days
    }, index=daily.index).fillna(0.0)


def severity_for(chance: float, cuts: list[float]) -> int:
    """Storm strength 1 to 5: one step up for every cut point the chance is at or above."""
    return 1 + sum(chance >= cut for cut in cuts)


def load_weather_model() -> dict | None:
    """Reads app/weather_model.json (made by training). Returns None if it is missing."""
    try:
        with open(C.WEATHER_MODEL, encoding="utf-8") as handle:
            return json.load(handle)
    except (OSError, ValueError):
        log.warning("weather: no trained model at %s (run python -m app.engines train-weather)", C.WEATHER_MODEL)
        return None


def predict(model: dict, features: pd.DataFrame) -> np.ndarray:
    """The saved model's chance of a damaging day, for each row of features."""
    scaled = (features[model["features"]].values - np.array(model["mean"])) / np.array(model["scale"])
    score = scaled @ np.array(model["coef"]) + model["intercept"]
    return 1 / (1 + np.exp(-score))


def explain(model: dict, row: pd.Series, day: dict) -> list[dict]:
    """Two plain sentences on what is driving the strength: wind and rain, biggest push first."""
    push = {name: model["coef"][i] * (row[name] - model["mean"][i]) / model["scale"][i]
            for i, name in enumerate(model["features"])}
    wind_push = push["gust"] + push["gust_max2"]
    rain_push = push["precip"] + push["precip_prev"] + push["precip3"]
    items = [
        ("wind", wind_push, f"Wind gusts up to {round(day['gust'])} mph are expected."),
        ("rain", rain_push, f"About {day['precip3']:.1f} inches of rain over the last 3 days."),
    ]
    items.sort(key=lambda item: abs(item[1]), reverse=True)
    return [{"factor": factor, "text": text, "direction": "increases_risk" if p > 0 else "decreases_risk"}
            for factor, p, text in items]


def _get_json(url: str, headers: dict | None = None, timeout: float = 6.0) -> dict:
    request = urllib.request.Request(url, headers=headers or {})
    with urllib.request.urlopen(request, timeout=timeout) as response:
        return json.load(response)


def _cached(key, seconds: int, fetch):
    """Reuse a recent answer instead of asking the weather service again."""
    hit = _cache.get(key)
    if hit and time.time() - hit[0] < seconds:
        return hit[1]
    value = fetch()
    _cache[key] = (time.time(), value)
    return value


def fetch_forecast(lat: float, lon: float) -> dict:
    """Open-Meteo forecast: the last 3 days and the next 2, daily and hourly."""
    query = urllib.parse.urlencode({
        "latitude": lat, "longitude": lon, "timezone": "America/New_York",
        "wind_speed_unit": "mph", "precipitation_unit": "inch", "temperature_unit": "fahrenheit",
        "past_days": 3, "forecast_days": 2,
        "daily": "wind_gusts_10m_max,precipitation_sum",
        "hourly": "precipitation,wind_gusts_10m,wind_direction_10m,temperature_2m",
    })
    return _cached(("forecast", lat, lon), CACHE_SECONDS,
                   lambda: _get_json(f"https://api.open-meteo.com/v1/forecast?{query}"))


def fetch_alerts(lat: float, lon: float) -> list[dict] | None:
    """Active National Weather Service alerts for the point. None means we could not check."""
    def ask():
        reply = _get_json(f"https://api.weather.gov/alerts/active?point={lat},{lon}",
                          headers={"User-Agent": "outage-concierge-hackathon", "Accept": "application/geo+json"})
        alerts = []
        for feature in reply.get("features", []):
            props = feature.get("properties", {})
            alerts.append({
                "event": props.get("event", "Weather alert"),
                "severity": props.get("severity", "Unknown"),
                "headline": props.get("headline") or props.get("event", ""),
                "ends": props.get("ends") or props.get("expires"),
                "instruction": (props.get("instruction") or "")[:400] or None,
            })
        return alerts

    try:
        return _cached(("alerts", lat, lon), 300, ask)
    except Exception:  # noqa: BLE001 - alerts are a bonus; they must never break the main answer
        log.warning("weather: NWS alerts unavailable", exc_info=True)
        return None


def _next_hours(forecast: dict, hours: int = 12) -> list[dict]:
    """The next 12 hours of the forecast, used to draw the radar."""
    hourly = forecast["hourly"]
    this_hour = datetime.now(TZ).replace(minute=0, second=0, microsecond=0, tzinfo=None)
    out = []
    for when, rain, gust, wind_from, temp in zip(hourly["time"], hourly["precipitation"], hourly["wind_gusts_10m"],
                                                 hourly["wind_direction_10m"], hourly["temperature_2m"]):
        moment = datetime.fromisoformat(when)
        ahead = round((moment - this_hour).total_seconds() / 3600)
        if 0 <= ahead <= hours:
            out.append({"hours_ahead": ahead, "label": "Now" if ahead == 0 else moment.strftime("%I %p").lstrip("0"),
                        "precip_in": round(rain or 0, 2), "gust_mph": round(gust or 0),
                        "wind_from_deg": round(wind_from or 0), "temp_f": round(temp or 0)})
    return out


def live_weather(model: dict | None, lat: float, lon: float) -> dict:
    """Storm strength for today and tomorrow, why, the next 12 hours, and NWS alerts.

    It never raises an error. If anything goes wrong it returns fallback=True with a reason, and the
    app falls back to letting the customer pick a strength by hand."""
    answer = {"updated_at": datetime.now(TZ).isoformat(timespec="seconds"), "alerts": None, "hourly": [],
              "severity": None, "severity_label": None, "chance_pct": None, "peak_day": None, "reasons": [],
              "fallback": True, "fallback_reason": None, "model": None}
    if model is None:
        answer["fallback_reason"] = "The weather model has not been trained yet."
        return answer
    try:
        forecast = fetch_forecast(lat, lon)
        daily = pd.DataFrame({"date": pd.to_datetime(forecast["daily"]["time"]),
                              "gust": forecast["daily"]["wind_gusts_10m_max"],
                              "precip": forecast["daily"]["precipitation_sum"]})
        features = make_features(daily)
        chances = predict(model, features)

        # Look at today and tomorrow and report whichever is worse.
        today = pd.Timestamp(datetime.now(TZ).date())
        upcoming = [i for i, day in enumerate(daily.date) if day >= today][:2]
        worst = max(upcoming, key=lambda i: chances[i])
        chance = float(chances[worst])
        strength = severity_for(chance, model["analog"]["cuts"])
        day_facts = {"gust": daily.iloc[worst].gust, "precip3": features.iloc[worst]["precip3"]}

        metrics = model["metrics"]
        answer.update({
            "severity": strength, "severity_label": SEVERITY_LABELS[strength], "chance_pct": round(chance * 100),
            "peak_day": "today" if worst == upcoming[0] else "tomorrow",
            "reasons": explain(model, features.iloc[worst], day_facts), "hourly": _next_hours(forecast),
            "alerts": fetch_alerts(lat, lon), "fallback": False,
            "model": {"auc_out_of_time": metrics["auc"], "damage_days": metrics["damage_days"], "days": metrics["days"]},
        })
        return answer
    except Exception as error:  # noqa: BLE001
        log.warning("weather: live lookup failed: %s", error)
        answer["fallback_reason"] = "Live weather is unavailable right now, so storm strength is not auto-set."
        return answer


# ============================================================================
# 5. TRAINING   python -m app.engines train-weather
#
# Run this when you want to rebuild app/weather_model.json (needs internet and the data/ folder).
# It does two things:
#   a) Trains the weather model: given a day's weather, how likely is a "damaging day"?
#      (a damaging day = the utility logged 5 or more damage reports).
#      It is tested only on days it never saw, so the test score is honest.
#   b) Builds the history tables ("analog"): what actually happened on days of each strength.
#
# Why "5 or more reports"? Days with 1 to 4 reports look random (equipment failures, not weather), and
# the weather model could not predict them. Days with 5+ are mostly real storms.
# ============================================================================

SIGNIFICANT_REPORTS = 5
SEVERITY_QUANTILES = [0.88, 0.96, 0.99, 0.997]   # strengths 2 to 5 are the top 12%, 4%, 1% and 0.3% of days
REPAIR_CAP_DAYS = 14   # a few repair dates equal the date the data was pulled; cap them
LAT, LON = 37.27, -79.94   # center of the service area (the label covers the whole area, so one point)
TEST_FOLDS, GAP_DAYS, REGULARIZATION = 6, 5, 0.3


def fetch_history(start: date, end: date) -> pd.DataFrame:
    """Past weather as it was FORECAST at the time (so training matches what we see live)."""
    query = urllib.parse.urlencode({
        "latitude": LAT, "longitude": LON, "start_date": start, "end_date": end,
        "timezone": "America/New_York", "wind_speed_unit": "mph", "precipitation_unit": "inch",
        "daily": "wind_gusts_10m_max,precipitation_sum"})
    daily = _get_json(f"https://historical-forecast-api.open-meteo.com/v1/forecast?{query}", timeout=60)["daily"]
    return pd.DataFrame({"date": pd.to_datetime(daily["time"]), "gust": daily["wind_gusts_10m_max"],
                         "precip": daily["precipitation_sum"]})


def _clean_features(row: dict) -> dict:
    """Replace missing values (NaN) with None so the area model can fill them in."""
    return {name: (value if isinstance(value, (list, str, bool)) or not pd.isna(value) else None)
            for name, value in row.items()}


def build_analog(store, daily: pd.DataFrame, chances: np.ndarray, cuts: list[float]) -> dict:
    """The history tables: what actually happened on days of each storm strength.

    For every strength row (1, 2, 3, and 4-or-5) we measure the share of circuits that had storm damage
    that day or the next (reports lag the weather), split into low / middle / high exposure circuits.
    We also collect the real repair times from the biggest storm."""
    circuits = store.circuits[store.circuits.n_customers >= C.MIN_CUSTOMERS_PER_LOCATION]
    keys = list(circuits.index)
    circuit_row = {key: i for i, key in enumerate(keys)}
    day_column = {pd.Timestamp(day): i for i, day in enumerate(daily.date)}

    # damaged[c, d] is True when circuit c had a damage report on day d.
    damaged = np.zeros((len(keys), len(daily)), bool)
    records = store.arcos[store.arcos.key.isin(keys)]
    for key, day in zip(records.key, records.Report_Tim.dt.normalize()):
        if day in day_column:
            damaged[circuit_row[key], day_column[day]] = True
    damaged_within_2_days = damaged.copy()
    damaged_within_2_days[:, :-1] |= damaged[:, 1:]

    # Sort circuits into low / middle / high exposure thirds.
    exposure_model = train_risk(store)
    scores = np.array([area_score(exposure_model, _clean_features(circuits.loc[key].to_dict()))["p_storm"] for key in keys])
    third = np.digitize(scores, np.quantile(scores, [1 / 3, 2 / 3]))      # 0, 1 or 2 for each circuit

    strength = 1 + sum((chances >= cut).astype(int) for cut in cuts)       # each historical day's strength
    row_of_day = np.minimum(strength, 4) - 1                               # strengths 4 and 5 share a row

    rates, all_areas, days_in_row = [], [], []
    for row in range(4):
        days = np.where(row_of_day == row)[0]
        days_in_row.append(int(len(days)))
        all_areas.append(float(damaged_within_2_days[:, days].mean()))
        rates.append([float(damaged_within_2_days[np.ix_(third == t, days)].mean()) for t in range(3)])
    rates = np.maximum(np.maximum.accumulate(np.array(rates), axis=0), 3e-4)   # a stronger storm never looks safer
    all_areas = np.maximum.accumulate(np.array(all_areas))

    biggest = records[(records.event_id == store.reference_event["id"]) & (records.Repaired == "Yes") & (records.repair_days >= 0)]
    capped = lambda days: np.minimum(days, REPAIR_CAP_DAYS).astype(int).tolist()  # noqa: E731
    return {
        "rate": np.round(rates, 5).tolist(), "all": np.round(all_areas, 5).tolist(), "days": days_in_row,
        "base_rate": round(float(damaged_within_2_days[:, strength == 1].mean()), 5),   # a calm day
        "cuts": [round(cut, 5) for cut in cuts],
        "repair_days": capped(biggest.repair_days),
        "pole_repair_days": capped(biggest[biggest.damage_class == "pole"].repair_days),
        "repair_source": f"{len(biggest)} repaired tickets from the {store.reference_event['label']}",
    }


def train_weather() -> None:
    """Train the weather model, test it on unseen days, build the history tables, and save everything."""
    import pyogrio  # only needed for training; not installed in the Docker image

    store = data.load()
    reports = pyogrio.read_dataframe(C.FILES["arcos"], read_geometry=False, columns=["Report_Tim"])
    reports_per_day = reports.Report_Tim.dt.normalize().value_counts()

    start = reports_per_day.index.min().date() - timedelta(days=7)
    end = min(reports_per_day.index.max().date(), date.today() - timedelta(days=3))
    daily = fetch_history(start, end).dropna().reset_index(drop=True)
    damaging = (daily.date.map(reports_per_day).fillna(0).values >= SIGNIFICANT_REPORTS).astype(int)
    features = make_features(daily)[WEATHER_FEATURES]
    n_days = len(daily)
    print(f"{n_days} days, {damaging.sum()} damaging days ({damaging.mean():.1%}) "
          f"from {daily.date.min().date()} to {daily.date.max().date()}")

    def new_model():
        return make_pipeline(StandardScaler(), LogisticRegression(C=REGULARIZATION, max_iter=2000))

    # Honest test: walk forward through time. Each block of days is predicted by a model trained only on
    # earlier days (with a gap so one storm can't straddle the split).
    edges = np.linspace(int(n_days * 0.4), n_days, TEST_FOLDS + 1).astype(int)
    tested = np.full(n_days, np.nan)
    for test_start, test_end in zip(edges[:-1], edges[1:]):
        train_days = np.arange(0, test_start - GAP_DAYS)
        test_days = np.arange(test_start, test_end)
        fitted = new_model().fit(features.iloc[train_days], damaging[train_days])
        tested[test_days] = fitted.predict_proba(features.iloc[test_days])[:, 1]
    was_tested = ~np.isnan(tested)
    metrics = {
        "auc": round(float(roc_auc_score(damaging[was_tested], tested[was_tested])), 3),
        "average_precision": round(float(average_precision_score(damaging[was_tested], tested[was_tested])), 3),
        "brier": round(float(brier_score_loss(damaging[was_tested], tested[was_tested])), 4),
        "brier_baseline": round(float(brier_score_loss(damaging[was_tested], np.full(was_tested.sum(), damaging[:edges[0]].mean()))), 4),
        "test_days": int(was_tested.sum()), "test_damage_days": int(damaging[was_tested].sum()),
        "days": int(n_days), "damage_days": int(damaging.sum()), "folds": TEST_FOLDS, "gap_days": GAP_DAYS,
    }
    print("tested on unseen days:", metrics)

    # Final model on all days, then the strength cut points and history tables.
    final = new_model().fit(features, damaging)
    scaler, logistic = final.steps[0][1], final.steps[1][1]
    chances = final.predict_proba(features)[:, 1]
    cuts = [float(np.quantile(chances, q)) for q in SEVERITY_QUANTILES]
    analog = build_analog(store, daily, chances, cuts)

    saved = {
        "features": WEATHER_FEATURES, "mean": scaler.mean_.tolist(), "scale": scaler.scale_.tolist(),
        "coef": logistic.coef_[0].tolist(), "intercept": float(logistic.intercept_[0]),
        "label": f"a day with at least {SIGNIFICANT_REPORTS} damage reports", "metrics": metrics, "analog": analog,
        "trained_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "history": {"start": str(daily.date.min().date()), "end": str(daily.date.max().date())},
    }
    C.WEATHER_MODEL.write_text(json.dumps(saved, indent=2), encoding="utf-8")
    print("days per strength row:", analog["days"], "| share of areas damaged:", analog["all"])
    print("saved", C.WEATHER_MODEL)


# ============================================================================
# 6. REQUEST FLOW
#
# State holds everything loaded at startup. assess() answers one request using the parts above.
# ============================================================================

LEVELS = {"low": 0, "medium": 1, "high": 2}   # for picking the lowest of several confidence levels


@dataclass
class State:
    store: data.Store             # the loaded data
    model: RiskModel              # the area-exposure model
    data_mode: str                # "live" (real data) or "mock" (data files missing)
    weather_model: dict | None = None


def startup() -> State:
    """Load the data, train the exposure model, and load the weather model. Runs once when the server starts."""
    store = data.load()
    mode = "live"
    if not store.live:
        log.warning("engine: real data unavailable, running in mock mode")
        store = data.mock_store(store.report)
        mode = "mock"
    model = train_risk(store) if mode == "live" else RiskModel()
    log.info("engine: ready in %s mode\n%s", mode, data.schema_report(store))
    return State(store, model, mode, load_weather_model())


def _analog_of(state: State) -> dict:
    """The history tables (or the stand-in if the trained file is missing)."""
    return (state.weather_model or {}).get("analog") or FALLBACK_ANALOG


def backtest(state: State) -> dict | None:
    """The exposure model's test results, shown under 'About this estimate'."""
    model = state.model
    if not model.ok:
        return None
    high, low = model.hit_rates.get("high", {}), model.hit_rates.get("low", {})
    return {
        "event_label": (state.store.reference_event or {}).get("label", "largest storm on record"),
        "areas": model.n_train,
        "auc": round(model.auc, 3),
        "high_band_hit_rate": None if high.get("rate") is None else round(high["rate"], 2),
        "low_band_hit_rate": None if low.get("rate") is None else round(low["rate"], 2),
        "high_band_areas": high.get("areas", 0),
        "low_band_areas": low.get("areas", 0),
    }


def _assumptions(state: State, scenario) -> list[str]:
    """Every assumption behind the numbers, in plain English. Shown to the customer."""
    model = state.model
    big_storm = (state.store.reference_event or {}).get("label", "largest storm on record")
    analog = _analog_of(state)

    notes = [
        "Storm strength (1 to 5) comes from the forecast. A small model scores how likely a day is to cause real damage "
        f"({SIGNIFICANT_REPORTS} or more damage reports). Strengths 2 to 5 are the top 12%, 4%, 1%, and 0.3% of days since late 2022.",
        "Your risk is what actually happened on days of this strength in the utility's records: the share of areas with "
        "storm damage within about 2 days, for areas as exposed as yours.",
    ]
    if model.ok:
        notes.append(f"How exposed an area is comes from a model of miles of overhead line, customers served, and past damage. "
                     f"It was trained before the {big_storm} and backtested on it (AUC {model.auc:.2f} across {model.n_train} areas).")
        high, low = model.hit_rates.get("high", {}), model.hit_rates.get("low", {})
        if high.get("rate") is not None and low.get("rate") is not None:
            notes.append(f"Held-out check: of {high['areas']} areas it rated high for that storm, {round(high['rate'] * 100)}% had damage; "
                         f"of {low['areas']} rated low, {round(low['rate'] * 100)}% did.")
    else:
        notes.append("Area exposure is unavailable, so a middle value is used.")

    notes += [
        f"Restoration uses real repair times: {analog.get('repair_source', 'the damage records')}. Dates in the records have "
        "day precision, so each time is spread within its day.",
        "Repair dates for other storms looked unreliable (many equal the date the data was pulled), so only that storm is used.",
        "How many damaged items sit between you and the substation is an assumption: about 1 on a calm day, up to 4 in a severe storm. "
        "Your power returns when the slowest of those repairs is done.",
    ]

    if "medical_equipment" in _needs_of(scenario):
        if scenario.backup_hours is not None:
            notes.append(f"You told us your backup power lasts {scenario.backup_hours:g} hours. We compare that to the high end of the range.")
        else:
            notes.append(f"Backup power for medical equipment is assumed to last {C.MEDICAL_BACKUP_HOURS} hours unless you tell us otherwise.")
    if scenario.damaged_poles > 0:
        notes.append("Each damaged pole you enter adds a repair drawn from real pole repair times, and damage is assumed.")
    return notes


def _limits(state: State, is_zip: bool) -> list[str]:
    """What these numbers cannot tell you. Shown to the customer."""
    notes = [
        "Estimates are model output, not confirmed restoration times from the utility.",
        f"The range covers the 10th to 90th percentile of {C.MC_SAMPLES} simulations; actual times can fall outside it.",
        "The records cover a few storms and one large one, so rates for strong storms rest on few days. "
        "Strengths 4 and 5 share one row of history (15 days).",
        "The weather model ranks damaging days better than chance, but it misses many small damage days.",
        "Locations are neighborhood-level areas (one circuit), not individual homes.",
        "Damage records have day-level dates only, so past repair times are approximate.",
    ]
    if is_zip:
        notes.append("A ZIP code is served by several circuits. The estimate is for a typical one, and ZIP borders are approximate.")
    if state.model.ok:
        notes.append(f"The area-exposure model learned from {state.model.n_train} areas and one major storm; "
                     "other storm types may behave differently.")
    if state.data_mode == "mock":
        notes.append("Mock data is in use; numbers are illustrative only.")
    return notes


def _confirmed_facts(state: State, feats: dict, is_zip: bool) -> list[dict]:
    """Facts that come straight from the utility's records (nothing predicted goes here)."""
    if state.data_mode != "live":
        return []
    facts = []
    reports = int(feats.get("hist_records") or 0)
    storms = int(feats.get("hist_events") or 0)
    where = "typical circuit serving this ZIP" if is_zip else "circuit here"
    if reports:
        facts.append({"fact": "past_damage_reports", "value": reports, "source": "ARCOS damage assessment records",
                      "text": f"Utility records show {reports} storm damage reports on a {where} across {storms} past "
                              f"storm{'s' if storms != 1 else ''}."})
    big_storm = state.store.reference_event
    in_big_storm = int(feats.get("ref_records") or 0)
    if big_storm and in_big_storm:
        facts.append({"fact": "reference_storm_damage", "value": in_big_storm, "source": "ARCOS damage assessment records",
                      "text": f"{in_big_storm} of those reports came from the {big_storm['label']}."})
    still_open = int(feats.get("open_damage") or 0)
    if still_open:
        facts.append({"fact": "open_damage_reports", "value": still_open, "source": "ARCOS damage assessment records",
                      "text": f"{still_open} past damage report{'s are' if still_open != 1 else ' is'} not yet marked repaired."})
    return facts


def _confidence(state: State, feats: dict, risk: dict, restoration: dict) -> tuple[str, str, dict]:
    """How sure are we? The answer is the LOWEST of four checks, each from something real:
        model    - how well the exposure model did in its test
        sample   - how many similar days are in the records
        history  - how much damage history this area has
        spread   - how wide the restoration range is
    Returns (level, a sentence explaining it, the four levels)."""
    checks = {
        "model": model_confidence(state.model),
        "sample": sample_confidence(risk["similar_days"]),
        "history": history_confidence(feats),
        "spread": spread_confidence(restoration["spread"]),
    }
    level = min(checks.values(), key=LEVELS.get)

    reports = int(feats.get("hist_records") or 0)
    facts = []
    if state.model.ok:
        best = state.model.hit_rates.get("high", {}).get("rate")
        text = (f"model backtest AUC {state.model.auc:.2f} on the "
                f"{(state.store.reference_event or {}).get('label', 'largest storm')}")
        if best is not None:
            text += f" ({round(best * 100)}% of areas it rated high had damage)"
        facts.append(text)
    facts.append(f"{risk['similar_days']} similar days in the records")
    facts.append(f"{reports} past damage report{'s' if reports != 1 else ''} in this area")
    facts.append(f"restoration range spread {restoration['spread']:.1f} times the median")
    return level, f"Confidence is {level}: " + "; ".join(facts) + ".", checks


def _fallback(state: State, level: str, checks: dict) -> tuple[bool, str | None]:
    """Whether to show the "we can't predict this reliably" state, and why."""
    if state.data_mode == "mock":
        return True, "Real utility data is not loaded, so these numbers are illustrative only."
    if level != "low":
        return False, None
    if checks["sample"] == "low":
        return True, "We have too few days like this on record to rate it reliably. Treat the estimate as a rough guide."
    if checks["history"] == "low":
        return True, ("We have no past storm damage records for this area, so we can't check the "
                      "model here. Treat the estimate as a rough guide.")
    if checks["model"] == "low":
        return True, "The risk model did not pass its backtest, so the estimate is not reliable."
    return True, ("The restoration range is very wide for this scenario, so we can't give a "
                  "reliable time. We'll update you as crews assess damage.")


def _area_summary(state: State, place, feats: dict) -> dict:
    """The coarse, privacy-safe facts about the area that the app may show."""
    chain = feats.get("upstream_chain")
    return {
        "label": place.label,
        "customers_served_band": place.customers_served_band,
        "upstream_device_type": feats.get("upstream_device_type"),
        "customers_on_upstream_device_band": upstream_band(feats.get("customers_on_upstream_device")),
        "critical_facility_nearby": bool(feats.get("critical_facility_nearby")) if state.data_mode == "live" else None,
        "upstream_chain": chain if isinstance(chain, list) else None,
    }


def assess(state: State, location_id: str, scenario, request_id: str) -> dict | None:
    """Answer one request. Returns None if the location does not exist."""
    place = resolve(state.store, location_id)                                           # 1. find the place
    if place is None:
        return None
    feats = place.features
    analog = _analog_of(state)

    risk = area_risk(state.model, analog, feats, scenario.storm_severity, scenario.damaged_poles)   # 2. risk
    restoration = estimate_restoration(analog, scenario)                                            # 3. time
    level, level_reason, checks = _confidence(state, feats, risk, restoration)                      # 4. certainty
    fallback, fallback_reason = _fallback(state, level, checks)
    rules = build_rules(risk["risk_band"], restoration["range"], scenario)                          # 5. rules

    return {                                                                                        # 6. package
        "location_id": location_id,
        "confirmed": _confirmed_facts(state, feats, place.is_zip),
        "model_estimate": {
            "risk_band": risk["risk_band"],
            "risk_score": risk["risk_score"],
            "confidence": level,
            "confidence_reason": level_reason,
            "reasons": risk["reasons"],
            "restoration_range": restoration["range"],
            "factor_deltas": restoration["deltas"],
            "alert_eligible": rules["alert_eligible"],
            "alert_rule": rules["alert_rule"],
            "restoration_histogram": restoration["histogram"],
            "simulations": restoration["simulations"],
            "similar_days": risk["similar_days"],
            "times_normal": risk["times_normal"],
        },
        "customer_actions": rules["customer_actions"],
        "meta": {
            "fallback": fallback,
            "fallback_reason": fallback_reason,
            "assumptions": _assumptions(state, scenario),
            "limits": _limits(state, place.is_zip),
            "data_mode": state.data_mode,
            "request_id": request_id,
            "backtest": backtest(state),
        },
        "area": _area_summary(state, place, feats),
    }


# When each message type is sent (the "before the storm" timing comes from the rules instead).
SEND_TIMING = {
    "outage_confirmed": "Send as soon as the outage is confirmed",
    "estimate_updated": "Send when the estimate changes by more than 2 hours",
    "restoration_complete": "Send when power is restored",
}

# Used instead of the normal text when we are not confident: no numbers, just honesty.
FALLBACK_TEXT = {
    "pre_storm": "A storm is coming. We can't give a reliable outage estimate for your area yet. "
                 "Please charge your phone and keep flashlights handy. We'll update you as we learn more.",
    "outage_confirmed": "We've confirmed an outage in your area. Crews are assessing the damage, and we'll "
                        "send a restoration estimate as soon as we have a reliable one.",
    "estimate_updated": "Crews are still assessing damage in your area. We'll send a restoration estimate "
                        "as soon as we have a reliable one.",
}


def message(state: State, location_id: str, message_type: str, scenario, request_id: str) -> dict | None:
    """The customer message for one stage (before storm, outage confirmed, estimate updated, restored)."""
    answer = assess(state, location_id, scenario, request_id)
    if answer is None:
        return None
    estimate, actions = answer["model_estimate"], answer["customer_actions"]

    # Every number the message may use. The number checker rejects any number not in this list.
    facts = {
        "low_hours": estimate["restoration_range"]["low_hours"],
        "high_hours": estimate["restoration_range"]["high_hours"],
        "median_hours": estimate["restoration_range"]["median_hours"],
        "risk_band": estimate["risk_band"],
        "top_action": actions["actions"][0] if actions["actions"] else "",
    }
    chain = (answer.get("area") or {}).get("upstream_chain") or []
    main_line = next((step for step in chain if step["device_type"] == "recloser"), chain[0] if chain else None)
    if main_line and main_line.get("customers_approx"):
        facts["line_customers"] = main_line["customers_approx"]

    if answer["meta"]["fallback"] and message_type in FALLBACK_TEXT:
        text = FALLBACK_TEXT[message_type]
        validated = validate(text, facts)
    else:
        text, validated, _ = generate(message_type, facts)

    timing = actions["send_timing"] if message_type == "pre_storm" else SEND_TIMING[message_type]
    return {
        "location_id": location_id,
        "message_type": message_type,
        "text": text,
        "channel": actions["channel"],
        "send_timing": timing,
        "validated": validated,
        "meta": answer["meta"],
        "_risk_band": estimate["risk_band"],
    }


if __name__ == "__main__":
    if sys.argv[1:] == ["train-weather"]:
        train_weather()
    else:
        print("usage: python -m app.engines train-weather")
