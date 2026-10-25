"""Area exposure, outage probability, and explanations.

Edit LOW_BELOW / MEDIUM_BELOW for customer risk bands.
Edit _model_inputs() and area_score() together when changing model inputs.
"""
from __future__ import annotations

import logging
import math
from dataclasses import dataclass, field

import numpy as np
import pandas as pd
from sklearn.ensemble import RandomForestClassifier
from sklearn.impute import SimpleImputer
from sklearn.linear_model import LogisticRegression
from sklearn.metrics import brier_score_loss, roc_auc_score
from sklearn.model_selection import StratifiedKFold, cross_val_predict
from sklearn.pipeline import make_pipeline
from sklearn.preprocessing import StandardScaler

from . import data as C
from .data import Store

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

# Change this to "logistic_regression" to compare with the original model.
RISK_MODEL = "random_forest"
FOREST_TREES = 200
FOREST_MAX_DEPTH = 4
FOREST_MIN_LEAF = 4


def make_risk_pipeline(kind: str):
    """All preprocessing is fitted inside each training fold, including missing values."""
    if kind == "logistic_regression":
        estimator = LogisticRegression(C=1.0, max_iter=1000)
        return make_pipeline(SimpleImputer(strategy="median"), StandardScaler(), estimator)
    if kind == "random_forest":
        estimator = RandomForestClassifier(
            n_estimators=FOREST_TREES, max_depth=FOREST_MAX_DEPTH,
            min_samples_leaf=FOREST_MIN_LEAF, random_state=C.MC_SEED,
        )
        return make_pipeline(SimpleImputer(strategy="median"), estimator)
    raise ValueError(f"Unknown risk model: {kind}")

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
    kind: str = RISK_MODEL
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
    test_targets: object = None                     # held-out labels and scores for comparison graphs
    test_scores: object = None

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


def risk_training_data(store: Store) -> tuple[pd.DataFrame, np.ndarray]:
    """Pre-storm features and damage labels from the reference storm."""
    circuits = store.circuits
    circuits = circuits[circuits["n_customers"] >= C.MIN_CUSTOMERS_PER_LOCATION]
    return _model_inputs(circuits, "pre_records"), (circuits["ref_records"] >= 1).astype(int).values


def train_risk(store: Store, kind: str = RISK_MODEL) -> RiskModel:
    """Train the exposure model: which circuits had damage in the biggest storm?

    Features use history BEFORE that storm. Five-fold validation holds out circuits,
    not entire storms; this does not measure performance on a future storm."""
    model = RiskModel(kind=kind)
    circuits = store.circuits
    if circuits is None or not store.has("arcos") or "primary_miles" not in circuits:
        log.warning("risk: missing data, model not trained")
        return model

    inputs, had_damage = risk_training_data(store)
    if had_damage.sum() < 5 or (len(had_damage) - had_damage.sum()) < 5:
        log.warning("risk: target too unbalanced to train")
        return model

    pipe = make_risk_pipeline(kind)

    # Test: 5-fold cross-validation. Each circuit is predicted by a model that did not see it.
    folds = StratifiedKFold(n_splits=5, shuffle=True, random_state=C.MC_SEED)
    tested = cross_val_predict(pipe, inputs, had_damage, cv=folds, method="predict_proba")[:, 1]
    model.auc = float(roc_auc_score(had_damage, tested))
    model.brier = float(brier_score_loss(had_damage, tested))
    model.base_rate = float(had_damage.mean())
    model.n_train = int(len(had_damage))
    model.test_targets, model.test_scores = had_damage, tested
    predicted_band = np.array([_backtest_band(p) for p in tested])
    for band in ("high", "low"):
        in_band = predicted_band == band
        model.hit_rates[band] = {"areas": int(in_band.sum()),
                                 "rate": float(had_damage[in_band].mean()) if in_band.any() else None}

    # Final model: trained on every circuit.
    pipe.fit(inputs, had_damage)
    model.pipe = pipe
    model.medians = dict(zip(RISK_FEATURES, pipe.named_steps["simpleimputer"].statistics_))
    if kind == "logistic_regression":
        scaler, logistic = pipe.steps[-2][1], pipe.steps[-1][1]
        model.coef = dict(zip(RISK_FEATURES, logistic.coef_[0]))
        model.means = dict(zip(RISK_FEATURES, scaler.mean_))
        model.stds = dict(zip(RISK_FEATURES, scaler.scale_))
    model.ref_sorted = np.sort(pipe.predict_proba(inputs)[:, 1])
    log.info("risk: %s trained on %d circuits, backtest AUC %.3f, Brier %.3f, base rate %.2f",
             kind, model.n_train, model.auc, model.brier, model.base_rate)
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
        if model.kind == "logistic_regression":
            for name in RISK_FEATURES:
                how_far_from_average = (row.iloc[0][name] - model.means[name]) / model.stds[name]
                pushes.append((name, model.coef[name] * how_far_from_average,
                               _feature_text(name, feats, how_far_from_average > 0)))
        else:
            # Compare with the same area after replacing ONE input by the training median.
            # These local sensitivity checks are not additive contributions or causal effects.
            alternatives = pd.concat([row] * len(RISK_FEATURES), ignore_index=True)
            for i, name in enumerate(RISK_FEATURES):
                alternatives.loc[i, name] = model.medians[name]
            comparison = model.pipe.predict_proba(alternatives)[:, 1]
            labels = {"log_primary_miles": "overhead line miles", "log_customers": "customer count",
                      "log_history": "past damage history"}
            for name, baseline in zip(RISK_FEATURES, comparison):
                push = score - float(baseline)
                if abs(push) < 1e-9:
                    continue
                direction = "higher" if push > 0 else "lower"
                text = (f"The model rates this area's exposure {direction} using its {labels[name]} "
                        "than when that input is replaced with a typical area's value.")
                pushes.append((name, push, text))
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
