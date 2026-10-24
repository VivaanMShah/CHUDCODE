"""Risk engine: logistic regression on circuit exposure + damage history.

Training target: did the circuit get any damage report in the largest storm in ARCOS
(the reference event, Feb 2025)? Features use only history from *before* that storm,
so cross-validated AUC on it is a real held-out-event backtest.

At serve time the same model rolls forward: the history feature uses all records to
date. Storm severity shifts the log-odds (severity 4 = reference storm).
"""
from __future__ import annotations

import logging
import math
from dataclasses import dataclass, field

import numpy as np
import pandas as pd
from sklearn.linear_model import LogisticRegression
from sklearn.metrics import brier_score_loss, roc_auc_score
from sklearn.model_selection import StratifiedKFold, cross_val_predict
from sklearn.pipeline import make_pipeline
from sklearn.preprocessing import StandardScaler

from . import config as C
from .data import Store

log = logging.getLogger("risk")

FEATURES = ["log_primary_miles", "log_customers", "log_history"]


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


def train(store: Store) -> RiskModel:
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
    m.coef = dict(zip(FEATURES, lr.coef_[0]))
    m.means = dict(zip(FEATURES, scaler.mean_))
    m.stds = dict(zip(FEATURES, scaler.scale_))
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


def score(model: RiskModel, feats: dict, scenario) -> dict:
    """Return risk_score, band, reasons, and the model's backtest stats."""
    sev = scenario.storm_severity
    poles = scenario.damaged_poles
    contribs = []  # (factor, contribution in log-odds, text)

    if model.ok:
        row = pd.DataFrame([{
            "log_primary_miles": np.log1p(feats.get("primary_miles") or np.nan),
            "log_customers": np.log1p(feats.get("n_customers") or np.nan),
            "log_history": np.log1p(feats.get("hist_records") or 0),
        }], columns=FEATURES).astype(float).fillna(model.medians)
        p_storm = float(model.pipe.predict_proba(row)[0, 1])
        for name in FEATURES:
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
