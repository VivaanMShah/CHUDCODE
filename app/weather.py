"""Live weather -> storm severity.

One small, explainable model: logistic regression that predicts the chance that a day
is a "damage day" (the utility logged storm damage) from gusts and rain. It is trained
by `python -m app.train_weather` and saved to app/weather_model.json.

Training and serving use the same `make_features`, and training uses *archived forecasts*
(what the forecast said at the time), so the model sees the same kind of input live.

Privacy: the only thing sent to weather services is a ZIP-center point (public,
rounded to 0.01 degree). Nothing about the customer goes out.
"""
from __future__ import annotations

import json
import logging
import time
import urllib.parse
import urllib.request
from datetime import datetime
from zoneinfo import ZoneInfo

import numpy as np
import pandas as pd

from . import config as C

log = logging.getLogger("weather")

TZ = ZoneInfo("America/New_York")
FEATURES = ["gust", "gust_max2", "precip", "precip_prev", "precip3"]
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

def load_model() -> dict | None:
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
