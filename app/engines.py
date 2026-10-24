"""The engines: risk model, restoration simulation, rules, live weather, and the request orchestration.

  python -m app.engines train-weather    retrain the weather model (needs internet and the data/ folder)
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date, datetime, timedelta, timezone
from zoneinfo import ZoneInfo
import json
import logging
import math
import sys
import time
import urllib.parse
import urllib.request

from . import data
from .assistant import generate, validate
from .data import Store, resolve, upstream_band
from sklearn.linear_model import LogisticRegression
from sklearn.metrics import average_precision_score, brier_score_loss, roc_auc_score
from sklearn.model_selection import StratifiedKFold, cross_val_predict
from sklearn.pipeline import make_pipeline
from sklearn.preprocessing import StandardScaler
import numpy as np
import pandas as pd

C = data  # settings live at the top of data.py
log = logging.getLogger("engines")

# ============================================================================
# RISK MODEL
# Risk engine: logistic regression on circuit exposure + damage history.
# ============================================================================


RISK_FEATURES = ["log_primary_miles", "log_customers", "log_history"]


@dataclass
class RiskModel:
    pipe: object = None
    medians: dict = field(default_factory=dict)
    auc: float | None = None
    brier: float | None = None
    base_rate: float | None = None
    n_train: int = 0
    hit_rates: dict = field(default_factory=dict)  # held-out: share with damage, by predicted band
    coef: dict = field(default_factory=dict)
    means: dict = field(default_factory=dict)
    stds: dict = field(default_factory=dict)

    @property
    def ok(self) -> bool:
        return self.pipe is not None


def _frame(circ: pd.DataFrame, history_col: str) -> pd.DataFrame:
    return pd.DataFrame({
        "log_primary_miles": np.log1p(circ["primary_miles"]),
        "log_customers": np.log1p(circ["n_customers"]),
        "log_history": np.log1p(circ[history_col]),
    }, index=circ.index)


def train_risk(store: Store) -> RiskModel:
    m = RiskModel()
    circ = store.circuits
    if circ is None or not store.has("arcos") or "primary_miles" not in circ:
        log.warning("risk: missing data, model not trained")
        return m
    circ = circ[circ.get("n_customers", 0) >= C.MIN_CUSTOMERS_PER_LOCATION]
    X = _frame(circ, "pre_records")
    m.medians = X.median().to_dict()
    X = X.fillna(m.medians)
    y = (circ["ref_records"] >= 1).astype(int).values
    if y.sum() < 5 or (len(y) - y.sum()) < 5:
        log.warning("risk: target too unbalanced to train")
        return m
    pipe = make_pipeline(StandardScaler(), LogisticRegression(C=1.0, max_iter=1000))
    cv = StratifiedKFold(n_splits=5, shuffle=True, random_state=C.MC_SEED)
    p_cv = cross_val_predict(pipe, X, y, cv=cv, method="predict_proba")[:, 1]
    m.auc = float(roc_auc_score(y, p_cv))
    m.brier = float(brier_score_loss(y, p_cv))
    m.base_rate = float(y.mean())
    m.n_train = int(len(y))
    bands = np.array([_band(p) for p in p_cv])
    for b in ("high", "low"):
        sel = bands == b
        m.hit_rates[b] = {"areas": int(sel.sum()),
                          "rate": float(y[sel].mean()) if sel.any() else None}
    pipe.fit(X, y)
    m.pipe = pipe
    scaler, lr = pipe.steps[0][1], pipe.steps[1][1]
    m.coef = dict(zip(RISK_FEATURES, lr.coef_[0]))
    m.means = dict(zip(RISK_FEATURES, scaler.mean_))
    m.stds = dict(zip(RISK_FEATURES, scaler.scale_))
    log.info("risk: trained on %d circuits, backtest AUC %.3f, Brier %.3f, base rate %.2f",
             m.n_train, m.auc, m.brier, m.base_rate)
    return m


def _band(p: float) -> str:
    for cut, name in C.RISK_BANDS:
        if p < cut:
            return name
    return "high"


def _logit(p):
    p = min(max(p, 1e-4), 1 - 1e-4)
    return math.log(p / (1 - p))


def _sigmoid(z):
    return 1 / (1 + math.exp(-z))


def _feature_text(name: str, f: dict, above: bool) -> str:
    if name == "log_primary_miles":
        miles = round(f.get("primary_miles") or 0)
        if above:
            return f"This area has about {miles} miles of overhead power lines, more than most areas."
        return f"This area has only about {miles} miles of overhead power lines, fewer than most areas."
    if name == "log_customers":
        if above:
            return "This area serves more homes and businesses than most, so there is more equipment storms can damage."
        return "This area serves fewer homes and businesses than most, so there is less equipment to damage."
    if name == "log_history":
        n = int(f.get("hist_records") or 0)
        if n == 0:
            return "We have no record of storm damage in this area."
        word = "report" if n == 1 else "reports"
        if above:
            return f"This area has had frequent storm damage in past events ({n} damage {word})."
        return f"This area has had less storm damage than most in past events ({n} damage {word})."
    return name


def score_risk(model: RiskModel, feats: dict, scenario) -> dict:
    """Return risk_score, band, reasons, and the model's backtest stats."""
    sev = scenario.storm_severity
    poles = scenario.damaged_poles
    contribs = []  # (factor, contribution in log-odds, text)

    if model.ok:
        row = pd.DataFrame([{
            "log_primary_miles": np.log1p(feats.get("primary_miles") or np.nan),
            "log_customers": np.log1p(feats.get("n_customers") or np.nan),
            "log_history": np.log1p(feats.get("hist_records") or 0),
        }], columns=RISK_FEATURES).astype(float).fillna(model.medians)
        p_storm = float(model.pipe.predict_proba(row)[0, 1])
        for name in RISK_FEATURES:
            z = (row.iloc[0][name] - model.means[name]) / model.stds[name]
            c = model.coef[name] * z
            contribs.append((name, c, _feature_text(name, feats, z > 0)))
    else:
        # Fallback heuristic when the model can't be trained (data missing).
        p_storm = 0.5
    z = _logit(p_storm) + C.SEVERITY_LOGIT_STEP * (sev - C.REFERENCE_SEVERITY)
    p = _sigmoid(z)
    contribs.append(("storm_severity", C.SEVERITY_LOGIT_STEP * (sev - 3) + 0.01,
                     f"The storm is rated {sev} out of 5."))
    if poles > 0:
        p = 1 - (1 - p) * math.exp(-0.5 * poles)
        contribs.append(("damaged_poles", 1.0 + 0.3 * poles,
                         f"{poles} damaged poles are assumed in this scenario."))

    top = sorted(contribs, key=lambda c: abs(c[1]), reverse=True)[:3]
    reasons = [{"factor": "history" if f == "log_history" else f.replace("log_", ""),
                "text": t,
                "direction": "increases_risk" if c > 0 else "decreases_risk"} for f, c, t in top]
    return {"risk_score": round(p, 2), "risk_band": _band(p), "reasons": reasons,
            "p_reference_storm": p_storm}


def model_confidence(model: RiskModel) -> str:
    if not model.ok or model.auc is None:
        return "low"
    if model.auc >= 0.80:
        return "high"
    if model.auc >= 0.70:
        return "medium"
    return "low"


def history_confidence(feats: dict) -> str:
    n = int(feats.get("hist_records") or 0)
    if n >= 10:
        return "high"
    if n >= 1:
        return "medium"
    return "low"

# ============================================================================
# RESTORATION (Monte Carlo)
# Restoration engine: Monte Carlo over damage counts, labor, travel, crew availability.
# ============================================================================

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


def estimate_restoration(feats: dict, store, scenario) -> dict:
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

# ============================================================================
# RULES (tips, alerts, channel, escalation)
# Rules engine: prep tips, safety messages, alert eligibility, channel, timing, escalation.
# ============================================================================

SAFETY = [
    "Stay at least 30 feet from downed power lines and report them right away.",
    "Never run a generator indoors or in a garage.",
]
SAFETY_LONG = "If you use a generator, keep it outside and at least 20 feet from doors and windows."
SAFETY_FLOOD = "Do not walk or drive through water near downed lines."


def actions_for(band: str, high_hours: float, needs: list[str], access_ok: bool,
                backup_hours: float | None = None) -> tuple[list[str], list[str]]:
    actions: list[str] = []
    if "medical_equipment" in needs:
        actions.append("Confirm backup power for medical equipment, or plan where you will go if it runs out.")
        if backup_hours is not None and high_hours > backup_hours:
            actions.append("This outage could outlast your backup. Arrange a place with power now.")
    if "accessibility" in needs:
        actions.append("Ask a neighbor, friend, or family member to check on you during the storm.")
    if "critical_facility" in needs:
        actions.append("Test your backup generator and confirm you have enough fuel for a long outage.")

    actions.append("Charge phones and power banks.")
    if band in ("medium", "high"):
        actions.append("Keep flashlights and batteries where you can find them.")
        actions.append("Store at least a day of drinking water.")
    if high_hours >= 12:
        actions.append("Plan to keep the fridge closed to keep food cold.")
    if high_hours >= 24:
        actions.append("Keep a few days of medicine and shelf-stable food on hand.")
    if high_hours >= 72:
        actions.append("Pick a place you can stay, like family or a shelter, if the outage lasts several days.")
    if not access_ok:
        actions.append("Roads may be blocked. Avoid travel and stay off closed roads so crews can get through.")
    if band == "low" and len(actions) < 3:
        actions.append("Sign up for outage alerts so you hear about changes quickly.")

    safety = list(SAFETY)
    if high_hours >= 24:
        safety.append(SAFETY_LONG)
    if not access_ok:
        safety.append(SAFETY_FLOOD)
    return actions[:6], safety


def alert(band: str, high_hours: float, needs: list[str], backup_hours: float | None = None) -> tuple[bool, str]:
    if "medical_equipment" in needs:
        backup = backup_hours if backup_hours is not None else C.MEDICAL_BACKUP_HOURS
        if high_hours > backup:
            return True, (f"R2: declared medical equipment, and the outage could last longer than "
                          f"your backup ({backup:g} hours)")
        if band != "low":
            return True, "R2: declared medical equipment need and outage risk"
    if band in ("medium", "high"):
        return True, "R1: risk band medium or high"
    if high_hours >= 24:
        return True, "R3: restoration could take more than 24 hours"
    return False, "R0: low risk and short expected outage, no proactive alert"


def channel_and_timing(band: str, needs: list[str]) -> tuple[str, str]:
    if "medical_equipment" in needs:
        # 24 hours earlier than the standard high-risk alert
        return "phone_call", "Call 48 hours before the storm arrives"
    if "critical_facility" in needs:
        return "phone_call", "Call 24 hours before the storm arrives"
    if band == "high":
        return "sms", "Send 24 hours before the storm arrives"
    if band == "medium":
        return "push", "Send 12 hours before the storm arrives"
    return "email", "Include in the daily storm update"


def escalation(high_hours: float, needs: list[str], backup_hours: float | None = None) -> dict:
    if "medical_equipment" in needs:
        if backup_hours is not None:
            return {"trigger": f"Outage expected to last longer than your backup ({backup_hours:g} hours)",
                    "action": "Flag for a utility follow-up call"}
        return {"trigger": f"Outage expected to last longer than backup power (about {C.MEDICAL_BACKUP_HOURS} hours)",
                "action": "Flag for a utility follow-up call"}
    if "critical_facility" in needs:
        return {"trigger": "Facility outage expected to last more than 4 hours",
                "action": "Notify the utility account representative"}
    if high_hours >= 48:
        return {"trigger": "Estimate grows by more than 12 hours",
                "action": "Send an estimate_updated message and share shelter information"}
    return {"trigger": "Estimate grows by more than 6 hours",
            "action": "Send an estimate_updated message"}


def build_rules(band: str, rng: dict, scenario) -> dict:
    needs = [n.value if hasattr(n, "value") else n for n in scenario.declared_needs]
    hi = rng["high_hours"]
    backup = getattr(scenario, "backup_hours", None) if "medical_equipment" in needs else None
    actions, safety = actions_for(band, hi, needs, scenario.access_ok, backup)
    eligible, rule = alert(band, hi, needs, backup)
    channel, timing = channel_and_timing(band, needs)
    return {
        "customer_actions": {"actions": actions, "safety_messages": safety, "channel": channel,
                             "send_timing": timing, "escalation": escalation(hi, needs, backup)},
        "alert_eligible": eligible,
        "alert_rule": rule,
    }

# ============================================================================
# LIVE WEATHER -> STORM SEVERITY
# Live weather -> storm severity.
# ============================================================================


TZ = ZoneInfo("America/New_York")
WEATHER_FEATURES = ["gust", "gust_max2", "precip", "precip_prev", "precip3"]
SEVERITY_LABELS = {1: "Calm", 2: "Breezy or wet", 3: "Stormy", 4: "Damaging storm", 5: "Severe storm"}
# Chance-of-damage-day cut points for severity 2, 3, 4, 5.
SEVERITY_CUTS = [0.05, 0.12, 0.30, 0.55]

CACHE_SECONDS = 600
_cache: dict = {}


# ---------------------------------------------------------------- features

def make_features(daily: pd.DataFrame) -> pd.DataFrame:
    """daily has columns: gust (mph, daily max) and precip (inches, daily total), one row per day,
    oldest first. Rolling features need the previous days, so serving fetches a few past days."""
    g, p = daily["gust"], daily["precip"]
    return pd.DataFrame({
        "gust": g,
        "gust_max2": np.maximum(g, g.shift(1)),
        "precip": p,
        "precip_prev": p.shift(1),
        "precip3": p.rolling(3).sum(),
    }, index=daily.index).fillna(0.0)


def severity_for(p: float) -> int:
    return 1 + sum(p >= c for c in SEVERITY_CUTS)


# ---------------------------------------------------------------- model

def load_weather_model() -> dict | None:
    try:
        with open(C.WEATHER_MODEL, encoding="utf-8") as fh:
            return json.load(fh)
    except (OSError, ValueError):
        log.warning("weather: no trained model at %s (run python -m app.train_weather)", C.WEATHER_MODEL)
        return None


def predict(model: dict, feats: pd.DataFrame) -> np.ndarray:
    x = (feats[model["features"]].values - np.array(model["mean"])) / np.array(model["scale"])
    z = x @ np.array(model["coef"]) + model["intercept"]
    return 1 / (1 + np.exp(-z))


def explain(model: dict, row: pd.Series, daily_row: dict) -> list[dict]:
    """Top reasons in plain English, from each feature's push on the log-odds."""
    contrib = {f: model["coef"][i] * (row[f] - model["mean"][i]) / model["scale"][i]
               for i, f in enumerate(model["features"])}
    gust_push = contrib["gust"] + contrib["gust_max2"]
    rain_push = contrib["precip"] + contrib["precip_prev"] + contrib["precip3"]
    items = [
        ("wind", gust_push, f"Wind gusts up to {round(daily_row['gust'])} mph are expected."),
        ("rain", rain_push, f"About {daily_row['precip3']:.1f} inches of rain over the last 3 days."),
    ]
    out = []
    for factor, push, text in sorted(items, key=lambda t: abs(t[1]), reverse=True):
        out.append({"factor": factor, "text": text,
                    "direction": "increases_risk" if push > 0 else "decreases_risk"})
    return out


# ---------------------------------------------------------------- fetching

def _get_json(url: str, headers: dict | None = None, timeout: float = 6.0) -> dict:
    req = urllib.request.Request(url, headers=headers or {})
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return json.load(r)


def _cached(key, ttl, fn):
    hit = _cache.get(key)
    if hit and time.time() - hit[0] < ttl:
        return hit[1]
    val = fn()
    _cache[key] = (time.time(), val)
    return val


def fetch_forecast(lat: float, lon: float) -> dict:
    q = urllib.parse.urlencode({
        "latitude": lat, "longitude": lon, "timezone": "America/New_York",
        "wind_speed_unit": "mph", "precipitation_unit": "inch", "temperature_unit": "fahrenheit",
        "past_days": 3, "forecast_days": 2,
        "daily": "wind_gusts_10m_max,precipitation_sum",
        "hourly": "precipitation,wind_gusts_10m,wind_direction_10m,temperature_2m",
    })
    return _cached(("fc", lat, lon), CACHE_SECONDS,
                   lambda: _get_json(f"https://api.open-meteo.com/v1/forecast?{q}"))


def fetch_alerts(lat: float, lon: float) -> list[dict] | None:
    """Active National Weather Service alerts for the point. None means we couldn't check."""
    def go():
        d = _get_json(f"https://api.weather.gov/alerts/active?point={lat},{lon}",
                      headers={"User-Agent": "outage-concierge-hackathon", "Accept": "application/geo+json"})
        out = []
        for f in d.get("features", []):
            p = f.get("properties", {})
            out.append({
                "event": p.get("event", "Weather alert"),
                "severity": p.get("severity", "Unknown"),
                "headline": p.get("headline") or p.get("event", ""),
                "ends": p.get("ends") or p.get("expires"),
                "instruction": (p.get("instruction") or "")[:400] or None,
            })
        return out
    try:
        return _cached(("nws", lat, lon), 300, go)
    except Exception:  # noqa: BLE001 - alerts are a bonus; never break the main answer
        log.warning("weather: NWS alerts unavailable", exc_info=True)
        return None


# ---------------------------------------------------------------- main entry

def live_weather(model: dict | None, lat: float, lon: float) -> dict:
    """Severity now/tomorrow, reasons, next-12-hour series for the radar, and NWS alerts.
    Never raises: on any failure it returns fallback=True with a reason."""
    now_iso = datetime.now(TZ).isoformat(timespec="seconds")
    base = {"updated_at": now_iso, "alerts": None, "hourly": [], "severity": None, "severity_label": None,
            "chance_pct": None, "peak_day": None, "reasons": [], "fallback": True, "fallback_reason": None,
            "model": None}
    if model is None:
        base["fallback_reason"] = "The weather model has not been trained yet."
        return base
    try:
        fc = fetch_forecast(lat, lon)
        daily = pd.DataFrame({"date": pd.to_datetime(fc["daily"]["time"]),
                              "gust": fc["daily"]["wind_gusts_10m_max"],
                              "precip": fc["daily"]["precipitation_sum"]})
        feats = make_features(daily)
        probs = predict(model, feats)
        today = pd.Timestamp(datetime.now(TZ).date())
        idx = [i for i, d in enumerate(daily.date) if d >= today][:2]  # today and tomorrow
        i_peak = max(idx, key=lambda i: probs[i])
        p = float(probs[i_peak])
        sev = severity_for(p)
        row, drow = feats.iloc[i_peak], daily.iloc[i_peak]
        drow = {"gust": drow.gust, "precip3": row["precip3"]}

        h = fc["hourly"]
        now_hour = datetime.now(TZ).replace(minute=0, second=0, microsecond=0, tzinfo=None)
        hourly = []
        for t, pr, g, wd, tf in zip(h["time"], h["precipitation"], h["wind_gusts_10m"],
                                    h["wind_direction_10m"], h["temperature_2m"]):
            dt = datetime.fromisoformat(t)
            ahead = round((dt - now_hour).total_seconds() / 3600)
            if 0 <= ahead <= 12:
                hourly.append({"hours_ahead": ahead, "label": "Now" if ahead == 0 else dt.strftime("%I %p").lstrip("0"),
                               "precip_in": round(pr or 0, 2), "gust_mph": round(g or 0),
                               "wind_from_deg": round(wd or 0), "temp_f": round(tf or 0)})
        m = model["metrics"]
        base.update({
            "severity": sev, "severity_label": SEVERITY_LABELS[sev], "chance_pct": round(p * 100),
            "peak_day": "today" if i_peak == idx[0] else "tomorrow",
            "reasons": explain(model, row, drow), "hourly": hourly,
            "alerts": fetch_alerts(lat, lon), "fallback": False,
            "model": {"auc_out_of_time": m["auc"], "damage_days": m["damage_days"], "days": m["days"]},
        })
        return base
    except Exception as e:  # noqa: BLE001
        log.warning("weather: live lookup failed: %s", e)
        base["fallback_reason"] = "Live weather is unavailable right now, so storm strength is not auto-set."
        return base

# ============================================================================
# WEATHER MODEL TRAINING
# Train the weather -> damage-day model.   python -m app.train_weather
# ============================================================================

# Center of the service area. The label covers the whole service area, so one point is used.
LAT, LON = 37.27, -79.94
FOLDS, GAP_DAYS, C_REG = 6, 5, 0.3


def fetch_history(start: date, end: date) -> pd.DataFrame:
    q = urllib.parse.urlencode({
        "latitude": LAT, "longitude": LON, "start_date": start, "end_date": end,
        "timezone": "America/New_York", "wind_speed_unit": "mph", "precipitation_unit": "inch",
        "daily": "wind_gusts_10m_max,precipitation_sum"})
    d = _get_json(f"https://historical-forecast-api.open-meteo.com/v1/forecast?{q}", timeout=60)["daily"]
    return pd.DataFrame({"date": pd.to_datetime(d["time"]), "gust": d["wind_gusts_10m_max"],
                         "precip": d["precipitation_sum"]})


def train_weather() -> None:
    import pyogrio  # training only; not installed in the Docker image

    arcos = pyogrio.read_dataframe(C.FILES["arcos"], read_geometry=False, columns=["Report_Tim"])
    days = arcos.Report_Tim.dt.normalize()
    start = days.min().date() - timedelta(days=7)
    end = min(days.max().date(), date.today() - timedelta(days=3))
    daily = fetch_history(start, end).dropna().reset_index(drop=True)
    y = daily.date.isin(set(days)).astype(int).values
    X = make_features(daily)[WEATHER_FEATURES]
    n = len(daily)
    print(f"{n} days, {y.sum()} damage days ({y.mean():.1%}) from {daily.date.min().date()} to {daily.date.max().date()}")

    def new_model():
        return make_pipeline(StandardScaler(), LogisticRegression(C=C_REG, max_iter=2000))

    # Out-of-time predictions
    edges = np.linspace(int(n * 0.4), n, FOLDS + 1).astype(int)
    pred = np.full(n, np.nan)
    for a, b in zip(edges[:-1], edges[1:]):
        train, test = np.arange(0, a - GAP_DAYS), np.arange(a, b)
        pred[test] = new_model().fit(X.iloc[train], y[train]).predict_proba(X.iloc[test])[:, 1]
    ok = ~np.isnan(pred)
    metrics = {
        "auc": round(float(roc_auc_score(y[ok], pred[ok])), 3),
        "average_precision": round(float(average_precision_score(y[ok], pred[ok])), 3),
        "brier": round(float(brier_score_loss(y[ok], pred[ok])), 4),
        "brier_baseline": round(float(brier_score_loss(y[ok], np.full(ok.sum(), y[:edges[0]].mean()))), 4),
        "test_days": int(ok.sum()), "test_damage_days": int(y[ok].sum()),
        "days": int(n), "damage_days": int(y.sum()), "folds": FOLDS, "gap_days": GAP_DAYS,
    }
    print("out-of-time:", metrics)

    final = new_model().fit(X, y)
    sc, lr = final.steps[0][1], final.steps[1][1]
    out = {
        "features": WEATHER_FEATURES, "mean": sc.mean_.tolist(), "scale": sc.scale_.tolist(),
        "coef": lr.coef_[0].tolist(), "intercept": float(lr.intercept_[0]),
        "severity_cuts": SEVERITY_CUTS, "metrics": metrics,
        "trained_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "history": {"start": str(daily.date.min().date()), "end": str(daily.date.max().date())},
    }
    C.WEATHER_MODEL.write_text(json.dumps(out, indent=2), encoding="utf-8")
    print("coefficients:", dict(zip(WEATHER_FEATURES, np.round(out["coef"], 2))))
    print("saved", C.WEATHER_MODEL)

# ============================================================================
# REQUEST ORCHESTRATION
# Orchestrates resolver -> risk -> restoration -> rules -> messages for one request.
# ============================================================================


LEVELS = {"low": 0, "medium": 1, "high": 2}


@dataclass
class State:
    store: data.Store
    model: RiskModel
    data_mode: str
    weather_model: dict | None = None


def startup() -> State:
    store = data.load()
    mode = "live"
    if not store.live:
        log.warning("engine: real data unavailable, running in mock mode")
        store = data.mock_store(store.report)
        mode = "mock"
    model = train_risk(store) if mode == "live" else RiskModel()
    log.info("engine: ready in %s mode\n%s", mode, data.schema_report(store))
    return State(store, model, mode, load_weather_model())


def backtest(state: State) -> dict | None:
    m = state.model
    if not m.ok:
        return None
    hi, lo = m.hit_rates.get("high", {}), m.hit_rates.get("low", {})
    return {
        "event_label": (state.store.reference_event or {}).get("label", "largest storm on record"),
        "areas": m.n_train,
        "auc": round(m.auc, 3),
        "high_band_hit_rate": None if hi.get("rate") is None else round(hi["rate"], 2),
        "low_band_hit_rate": None if lo.get("rate") is None else round(lo["rate"], 2),
        "high_band_areas": hi.get("areas", 0),
        "low_band_areas": lo.get("areas", 0),
    }


def _assumptions(state: State, scenario) -> list[str]:
    m, ref = state.model, state.store.reference_event or {}
    ref_label = ref.get("label", "largest storm on record")
    h = C.PLACEHOLDER_LABOR_HOURS
    out = []
    if m.ok:
        out.append(f"Risk model: logistic regression on miles of overhead line, customers served, and past "
                   f"damage. Trained on history before the {ref_label} and backtested on it "
                   f"(AUC {m.auc:.2f} across {m.n_train} areas).")
        hi, lo = m.hit_rates.get("high", {}), m.hit_rates.get("low", {})
        if hi.get("rate") is not None and lo.get("rate") is not None:
            out.append(f"Held-out check: of {hi['areas']} areas the model rated high risk for that storm, "
                       f"{round(hi['rate'] * 100)}% had damage; of {lo['areas']} rated low, "
                       f"{round(lo['rate'] * 100)}% did.")
    else:
        out.append("Risk model unavailable; risk uses a neutral baseline adjusted for storm severity.")
    out += [
        f"Storm severity {C.REFERENCE_SEVERITY} is calibrated to the {ref_label}, the largest in the "
        f"utility's damage records. Each level up or down changes the odds of an outage about {math.exp(C.SEVERITY_LOGIT_STEP):.0f} times (an assumption, not fitted to data).",
        f"Expected damage blends this area's damage in the {ref_label} with the system average per mile of line.",
        f"Labor hours per repair are placeholders until the STORMS file is provided: pole {h['pole']}, "
        f"primary wire {h['primary_wire']}, transformer {h['transformer']}, fuse or switch {h['fuse_switch']} crew-hours.",
        f"Travel multiplier {C.PLACEHOLDER_TRAVEL_MULT} and nonproductive multiplier "
        f"{C.PLACEHOLDER_NONPRODUCTIVE_MULT} are placeholders until the indirect labor file is provided.",
        f"Blocked roads add about {round((C.BLOCKED_ROADS_TRAVEL_MULT - 1) * 100)}% to travel and work time.",
        f"Each crew is available {round(C.CREW_AVAILABILITY * 100)}% of the time and works "
        f"{WORK_HOURS_PER_DAY}-hour shifts.",
    ]
    needs = [n.value if hasattr(n, "value") else n for n in scenario.declared_needs]
    if "medical_equipment" in needs:
        if scenario.backup_hours is not None:
            out.append(f"You told us your backup power lasts {scenario.backup_hours:g} hours. "
                       "We compare that to the high end of the range.")
        else:
            out.append(f"Backup power for medical equipment is assumed to last {C.MEDICAL_BACKUP_HOURS} hours "
                       "unless you tell us otherwise.")
    if scenario.damaged_poles > 0:
        out.append("Other damage (wires, fuses, transformers) is scaled from the pole count using the "
                   f"{ref_label} damage mix.")
    return out


def _limits(state: State, is_zip: bool = False) -> list[str]:
    out = [
        "Estimates are model output, not confirmed restoration times from the utility.",
        f"The range covers the 10th to 90th percentile of {C.MC_SAMPLES} simulations; "
        "actual times can fall outside it.",
        "Locations are neighborhood-level areas (one circuit), not individual homes.",
        "Damage records have day-level dates only, so past repair times are approximate.",
    ]
    if is_zip:
        out.append("A ZIP code is served by several circuits. The estimate is for a typical one, "
                   "and ZIP borders are approximate.")
    if state.model.ok:
        out.append(f"The risk model learned from {state.model.n_train} areas and one major storm; "
                   "other storm types may behave differently.")
    if state.data_mode == "mock":
        out.append("Mock data is in use; numbers are illustrative only.")
    return out


def _confirmed(state: State, feats: dict, is_zip: bool = False) -> list[dict]:
    """Only real utility records. Nothing predicted goes here."""
    if state.data_mode != "live":
        return []
    out = []
    n = int(feats.get("hist_records") or 0)
    ev = int(feats.get("hist_events") or 0)
    if n:
        out.append({"fact": "past_damage_reports", "value": n, "source": "ARCOS damage assessment records",
                    "text": f"Utility records show {n} storm damage reports on a {'typical circuit serving this ZIP' if is_zip else 'circuit here'} across {ev} past "
                            f"storm{'s' if ev != 1 else ''}."})
    ref = state.store.reference_event
    r = int(feats.get("ref_records") or 0)
    if ref and r:
        out.append({"fact": "reference_storm_damage", "value": r, "source": "ARCOS damage assessment records",
                    "text": f"{r} of those reports came from the {ref['label']}."})
    o = int(feats.get("open_damage") or 0)
    if o:
        out.append({"fact": "open_damage_reports", "value": o, "source": "ARCOS damage assessment records",
                    "text": f"{o} past damage report{'s are' if o != 1 else ' is'} not yet marked repaired."})
    return out


def assess(state: State, location_id: str, scenario, request_id: str) -> dict | None:
    loc = resolve(state.store, location_id)
    if loc is None:
        return None
    f = loc.features
    rk = score_risk(state.model, f, scenario)
    rs = estimate_restoration(f, state.store, scenario)
    rng = rs["range"]

    parts = {
        "model": model_confidence(state.model),
        "history": history_confidence(f),
        "spread": spread_confidence(rs["spread"]),
    }
    confidence = min(parts.values(), key=LEVELS.get)
    n = int(f.get("hist_records") or 0)
    reason_bits = []
    if state.model.ok:
        hr = state.model.hit_rates.get("high", {}).get("rate")
        bit = (f"model backtest AUC {state.model.auc:.2f} on the "
               f"{(state.store.reference_event or {}).get('label', 'largest storm')}")
        if hr is not None:
            bit += f" ({round(hr * 100)}% of areas it rated high had damage)"
        reason_bits.append(bit)
    reason_bits.append(f"{n} past damage report{'s' if n != 1 else ''} in this area")
    reason_bits.append(f"restoration range spread {rs['spread']:.1f} times the median")
    confidence_reason = (f"Confidence is {confidence}: " + "; ".join(reason_bits) + ".")

    fallback, fallback_reason = False, None
    if state.data_mode == "mock":
        fallback, fallback_reason = True, "Real utility data is not loaded, so these numbers are illustrative only."
    elif confidence == "low":
        fallback = True
        if parts["history"] == "low":
            fallback_reason = ("We have no past storm damage records for this area, so we can't check the "
                               "model here. Treat the estimate as a rough guide.")
        elif parts["model"] == "low":
            fallback_reason = "The risk model did not pass its backtest, so the estimate is not reliable."
        else:
            fallback_reason = ("The restoration range is very wide for this scenario, so we can't give a "
                               "reliable time. We'll update you as crews assess damage.")

    rl = build_rules(rk["risk_band"], rng, scenario)
    area = {
        "label": loc.label,
        "customers_served_band": loc.customers_served_band,
        "upstream_device_type": f.get("upstream_device_type"),
        "customers_on_upstream_device_band": upstream_band(f.get("customers_on_upstream_device")),
        "critical_facility_nearby": bool(f.get("critical_facility_nearby")) if state.data_mode == "live" else None,
        "upstream_chain": f.get("upstream_chain") if isinstance(f.get("upstream_chain"), list) else None,
    }
    return {
        "location_id": location_id,
        "confirmed": _confirmed(state, f, loc.is_zip),
        "model_estimate": {
            "risk_band": rk["risk_band"],
            "risk_score": rk["risk_score"],
            "confidence": confidence,
            "confidence_reason": confidence_reason,
            "reasons": rk["reasons"],
            "restoration_range": rng,
            "factor_deltas": rs["deltas"],
            "alert_eligible": rl["alert_eligible"],
            "alert_rule": rl["alert_rule"],
            "restoration_histogram": rs["histogram"],
            "simulations": rs["simulations"],
        },
        "customer_actions": rl["customer_actions"],
        "meta": {
            "fallback": fallback,
            "fallback_reason": fallback_reason,
            "assumptions": _assumptions(state, scenario),
            "limits": _limits(state, loc.is_zip),
            "data_mode": state.data_mode,
            "request_id": request_id,
            "backtest": backtest(state),
        },
        "area": area,
    }


SEND_TIMING = {
    "outage_confirmed": "Send as soon as the outage is confirmed",
    "estimate_updated": "Send when the estimate changes by more than 2 hours",
    "restoration_complete": "Send when power is restored",
}

FALLBACK_TEXT = {
    "pre_storm": "A storm is coming. We can't give a reliable outage estimate for your area yet. "
                 "Please charge your phone and keep flashlights handy. We'll update you as we learn more.",
    "outage_confirmed": "We've confirmed an outage in your area. Crews are assessing the damage, and we'll "
                        "send a restoration estimate as soon as we have a reliable one.",
    "estimate_updated": "Crews are still assessing damage in your area. We'll send a restoration estimate "
                        "as soon as we have a reliable one.",
}


def message(state: State, location_id: str, message_type: str, scenario, request_id: str) -> dict | None:
    a = assess(state, location_id, scenario, request_id)
    if a is None:
        return None
    est, acts = a["model_estimate"], a["customer_actions"]
    facts = {
        "low_hours": est["restoration_range"]["low_hours"],
        "high_hours": est["restoration_range"]["high_hours"],
        "median_hours": est["restoration_range"]["median_hours"],
        "risk_band": est["risk_band"],
        "top_action": acts["actions"][0] if acts["actions"] else "",
    }
    chain = (a.get("area") or {}).get("upstream_chain") or []
    line = next((c for c in chain if c["device_type"] == "recloser"), chain[0] if chain else None)
    if line and line.get("customers_approx"):
        facts["line_customers"] = line["customers_approx"]
    if a["meta"]["fallback"] and message_type in FALLBACK_TEXT:
        text = FALLBACK_TEXT[message_type]
        validated = validate(text, facts)
    else:
        text, validated, _ = generate(message_type, facts)
    timing = acts["send_timing"] if message_type == "pre_storm" else SEND_TIMING[message_type]
    return {
        "location_id": location_id,
        "message_type": message_type,
        "text": text,
        "channel": acts["channel"],
        "send_timing": timing,
        "validated": validated,
        "meta": a["meta"],
        "_risk_band": est["risk_band"],
    }


if __name__ == "__main__":
    if sys.argv[1:] == ["train-weather"]:
        train_weather()
    else:
        print("usage: python -m app.engines train-weather")
