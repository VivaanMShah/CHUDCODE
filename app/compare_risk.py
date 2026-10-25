"""Save reproducible model comparisons: python -m app.compare_risk.

Only aggregate scores are exported; no circuit IDs or customer data.
Plot the saved results with scripts/plot_risk_training.py (requires matplotlib).
"""
import json
from pathlib import Path

import numpy as np
from sklearn.metrics import roc_curve
from sklearn.model_selection import StratifiedKFold, learning_curve

from . import data
from .risk import make_risk_pipeline, risk_training_data, train_risk


def compare():
    store = data.load()
    if not store.live:
        raise RuntimeError("Organizer data is required for a real model comparison.")
    inputs, targets = risk_training_data(store)
    folds = StratifiedKFold(n_splits=5, shuffle=True, random_state=data.MC_SEED)
    report = {
        "areas": len(inputs), "damaged_areas": int(targets.sum()),
        "event": store.reference_event["label"], "seed": data.MC_SEED,
        "validation": "5-fold stratified cross-validation of areas within one storm; not a future-storm test",
        "preprocessing": "Median imputation (and logistic scaling) fitted inside every fold",
        "models": {},
    }
    for kind in ["logistic_regression", "random_forest"]:
        model = train_risk(store, kind)
        sizes, training, validation = learning_curve(
            make_risk_pipeline(kind), inputs, targets, cv=folds,
            train_sizes=np.linspace(0.35, 1.0, 6), scoring="roc_auc",
            shuffle=True, random_state=data.MC_SEED, error_score="raise",
        )
        false_positive, true_positive, _ = roc_curve(model.test_targets, model.test_scores)
        report["models"][kind] = {
            "auc": model.auc, "brier": model.brier,
            "parameters": {key: value for key, value in model.pipe.steps[-1][1].get_params().items()
                           if isinstance(value, (str, int, float, bool)) or value is None},
            "training_sizes": sizes.tolist(),
            "training_auc_mean": training.mean(axis=1).tolist(),
            "training_auc_std": training.std(axis=1).tolist(),
            "validation_auc_mean": validation.mean(axis=1).tolist(),
            "validation_auc_std": validation.std(axis=1).tolist(),
            "roc_false_positive": false_positive.tolist(), "roc_true_positive": true_positive.tolist(),
        }
        print(f"{kind}: held-out AUC={model.auc:.3f}, Brier={model.brier:.4f}", flush=True)
    output = Path("output/training")
    output.mkdir(parents=True, exist_ok=True)
    (output / "risk_comparison.json").write_text(json.dumps(report, indent=2), encoding="utf-8")
    print("Saved", output / "risk_comparison.json")


if __name__ == "__main__":
    compare()
