"""Live forecasts, weather features, and saved-model predictions.

Training uses make_features() too; keep feature calculations in this file.
Edit ARRIVAL_GUST_MPH / ARRIVAL_RAIN_IN to change storm arrival thresholds.
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

from . import data as C

log = logging.getLogger("engines")

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


# Storm types, worst first. The first word list that matches a National Weather Service alert name wins.
STORM_TYPES = [
    ("hurricane", "Hurricane or tropical storm", ("hurricane", "tropical", "storm surge")),
    ("tornado", "Tornado", ("tornado",)),
    ("thunderstorm", "Thunderstorm", ("thunderstorm",)),
    ("winter", "Winter storm (snow or ice)", ("winter", "ice storm", "blizzard", "snow", "freez", "sleet")),
    ("flood", "Flooding rain", ("flood",)),
    ("wind", "High wind", ("wind",)),
]
STORM_LABELS = {key: label for key, label, _ in STORM_TYPES}
STORM_LABELS.update({"rain": "Heavy rain", "none": "No storm expected"})

# An hour counts as "storm conditions" at these levels. The storm "arrives" at the first such hour.
ARRIVAL_GUST_MPH, ARRIVAL_RAIN_IN = 35, 0.2


def classify_storm(alerts: list[dict] | None, hours: list[dict]) -> str:
    """What kind of storm is it? Official alert names first, then the forecast itself."""
    for key, _, words in STORM_TYPES:
        for alert in alerts or []:
            if any(word in alert["event"].lower() for word in words):
                return key
    # No alert: read the hours coming up.
    worst_gust = max((h["gust_mph"] for h in hours), default=0)
    worst_rain = max((h["precip_in"] for h in hours), default=0)
    coldest = min((h["temp_f"] for h in hours if h["precip_in"] >= 0.05), default=99)
    if coldest <= 34:
        return "winter"
    if worst_gust >= 58:
        return "wind"
    if worst_gust >= ARRIVAL_GUST_MPH and worst_rain >= 0.1:
        return "thunderstorm"
    if worst_rain >= 0.3:
        return "rain"
    if worst_gust >= 40:
        return "wind"
    return "none"


def hours_until_storm(hours: list[dict]) -> int | None:
    """Hours from now until conditions first reach storm level. None if they do not in the next 48 hours."""
    for hour in hours:
        if hour["gust_mph"] >= ARRIVAL_GUST_MPH or hour["precip_in"] >= ARRIVAL_RAIN_IN:
            return hour["hours_ahead"]
    return None


def arrival_text(storm_type: str, hours: int | None) -> str:
    if storm_type == "none" or hours is None:
        return "No storm is expected in the next 48 hours."
    kind = STORM_LABELS[storm_type].lower()
    if hours <= 0:
        return f"Storm conditions ({kind}) are here now."
    return f"Storm conditions ({kind}) are expected in about {hours} hour{'s' if hours != 1 else ''}."


def live_weather(model: dict | None, lat: float, lon: float) -> dict:
    """Storm strength for today and tomorrow, why, the next 12 hours, and NWS alerts.

    It never raises an error. If anything goes wrong it returns fallback=True with a reason, and the
    app falls back to letting the customer pick a strength by hand."""
    answer = {"updated_at": datetime.now(TZ).isoformat(timespec="seconds"), "alerts": None, "hourly": [],
              "severity": None, "severity_label": None, "chance_pct": None, "peak_day": None, "reasons": [],
              "fallback": True, "fallback_reason": None, "model": None,
              "storm_type": None, "storm_type_label": None, "arrival_hours": None, "arrival_text": None}
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
        next_48 = _next_hours(forecast, 48)
        alerts = fetch_alerts(lat, lon)
        storm_type = classify_storm(alerts, next_48)
        arrival = hours_until_storm(next_48) if storm_type != "none" else None
        answer.update({
            "storm_type": storm_type, "storm_type_label": STORM_LABELS[storm_type],
            "arrival_hours": arrival, "arrival_text": arrival_text(storm_type, arrival),
            "severity": strength, "severity_label": SEVERITY_LABELS[strength], "chance_pct": round(chance * 100),
            "peak_day": "today" if worst == upcoming[0] else "tomorrow",
            "reasons": explain(model, features.iloc[worst], day_facts), "hourly": next_48[:13],
            "alerts": alerts, "fallback": False,
            "model": {"auc_out_of_time": metrics["auc"], "damage_days": metrics["damage_days"], "days": metrics["days"]},
        })
        return answer
    except Exception as error:  # noqa: BLE001
        log.warning("weather: live lookup failed: %s", error)
        answer["fallback_reason"] = "Live weather is unavailable right now, so storm strength is not auto-set."
        return answer
