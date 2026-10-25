"""Train and evaluate weather predictions, then save weather_model.json.

Run: python -m app.train_weather (requires organizer data and internet).
Edit the named training settings below, then retrain; do not hand-edit model JSON.
"""
from __future__ import annotations

import json
import urllib.parse
from datetime import date, datetime, timedelta, timezone

import numpy as np
import pandas as pd
from sklearn.linear_model import LogisticRegression
from sklearn.metrics import average_precision_score, brier_score_loss, roc_auc_score
from sklearn.pipeline import make_pipeline
from sklearn.preprocessing import StandardScaler

from . import data
from . import data as C
from .risk import area_score, train_risk
from .weather import WEATHER_FEATURES, _get_json, load_weather_model, make_features, predict

# ============================================================================
# 5. TRAINING   python -m app.train_weather
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
        "exposure_model": exposure_model.kind,
        "rate": np.round(rates, 5).tolist(), "all": np.round(all_areas, 5).tolist(), "days": days_in_row,
        "base_rate": round(float(damaged_within_2_days[:, strength == 1].mean()), 5),   # a calm day
        "cuts": [round(cut, 5) for cut in cuts],
        "repair_days": capped(biggest.repair_days),
        "pole_repair_days": capped(biggest[biggest.damage_class == "pole"].repair_days),
        "repair_source": f"{len(biggest)} repaired tickets from the {store.reference_event['label']}",
    }


def refresh_analog() -> None:
    """Rebuild area-risk tables after changing RISK_MODEL; keep weather weights unchanged."""
    model = load_weather_model()
    if model is None:
        raise RuntimeError("Train the weather model first.")
    store = data.load()
    if not store.live:
        raise RuntimeError("Organizer data is required to rebuild risk tables.")
    start, end = (date.fromisoformat(model["history"][key]) for key in ("start", "end"))
    cache = C.CACHE_DIR / f"weather_{start}_{end}.csv"
    if cache.exists():
        daily = pd.read_csv(cache, parse_dates=["date"])
    else:
        daily = fetch_history(start, end).dropna().reset_index(drop=True)
        cache.parent.mkdir(parents=True, exist_ok=True)
        daily.to_csv(cache, index=False)
    chances = predict(model, make_features(daily))
    cuts = [float(np.quantile(chances, q)) for q in SEVERITY_QUANTILES]
    analog = build_analog(store, daily, chances, cuts)
    if not np.isfinite(np.asarray(analog["rate"])).all() or min(analog["days"]) < 1:
        raise ValueError("Incomplete history tables; existing model was not overwritten.")
    model["analog"] = analog
    model["analog_updated_at"] = datetime.now(timezone.utc).isoformat(timespec="seconds")
    C.WEATHER_MODEL.write_text(json.dumps(model, indent=2), encoding="utf-8")
    print("Rebuilt area-risk tables for", analog["exposure_model"])


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


if __name__ == "__main__":
    train_weather()
