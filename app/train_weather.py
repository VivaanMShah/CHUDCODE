"""Train the weather -> damage-day model.   python -m app.train_weather

Label: did the utility log any storm damage that day (ARCOS), 1 or 0.
Inputs: daily wind gust and rain for the service area, from *archived forecasts*.

Leakage guards:
  * Time-ordered validation only (expanding window, never shuffled).
  * A 5-day gap between training and test so a storm can't straddle the split.
  * Features use today and earlier days only (no future weather, no repair data).
  * Scaler and model are fit inside each training window.
  * The reported metrics come from out-of-time predictions only.
Honest note: we compared a few feature sets on these same folds before settling on
this one, so the reported score is slightly optimistic.
"""
import json
import urllib.parse
from datetime import date, datetime, timedelta, timezone

import numpy as np
import pandas as pd
import pyogrio
from sklearn.linear_model import LogisticRegression
from sklearn.metrics import average_precision_score, brier_score_loss, roc_auc_score
from sklearn.pipeline import make_pipeline
from sklearn.preprocessing import StandardScaler

from . import config as C
from .weather import FEATURES, SEVERITY_CUTS, _get_json, make_features

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


def main() -> None:
    arcos = pyogrio.read_dataframe(C.FILES["arcos"], read_geometry=False, columns=["Report_Tim"])
    days = arcos.Report_Tim.dt.normalize()
    start = days.min().date() - timedelta(days=7)
    end = min(days.max().date(), date.today() - timedelta(days=3))
    daily = fetch_history(start, end).dropna().reset_index(drop=True)
    y = daily.date.isin(set(days)).astype(int).values
    X = make_features(daily)[FEATURES]
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
        "features": FEATURES, "mean": sc.mean_.tolist(), "scale": sc.scale_.tolist(),
        "coef": lr.coef_[0].tolist(), "intercept": float(lr.intercept_[0]),
        "severity_cuts": SEVERITY_CUTS, "metrics": metrics,
        "trained_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "history": {"start": str(daily.date.min().date()), "end": str(daily.date.max().date())},
    }
    C.WEATHER_MODEL.write_text(json.dumps(out, indent=2), encoding="utf-8")
    print("coefficients:", dict(zip(FEATURES, np.round(out["coef"], 2))))
    print("saved", C.WEATHER_MODEL)


if __name__ == "__main__":
    main()
