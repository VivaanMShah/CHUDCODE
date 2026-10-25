"""Render output/training/risk_comparison.json; requires matplotlib, not model libraries."""
import json
from pathlib import Path

import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
import numpy as np


def plot():
    folder = Path(__file__).resolve().parents[1] / "output" / "training"
    report = json.loads((folder / "risk_comparison.json").read_text(encoding="utf-8"))
    plt.rcParams.update({"font.size": 11, "axes.spines.top": False, "axes.spines.right": False,
                         "figure.facecolor": "#f8fafc", "axes.facecolor": "#f8fafc"})
    fig, axes = plt.subplots(2, 2, figsize=(13, 9))
    colors = ["#2563eb", "#059669"]
    labels = ["Logistic regression", "Random forest"]
    for ax, (kind, result), color, label in zip(axes[0], report["models"].items(), colors, labels):
        sizes = result["training_sizes"]
        for prefix, curve_label, style in [("training", "Training", "--"), ("validation", "Validation", "-")]:
            mean = np.array(result[f"{prefix}_auc_mean"])
            std = np.array(result[f"{prefix}_auc_std"])
            ax.plot(sizes, mean, style, marker="o", color=color, alpha=0.5 if prefix == "training" else 1,
                    label=curve_label)
            if prefix == "validation":
                ax.fill_between(sizes, np.clip(mean - std, 0, 1), np.clip(mean + std, 0, 1), color=color, alpha=0.13)
        ax.set(title=label + " learning curve", xlabel="Areas used to train each fold", ylabel="ROC AUC", ylim=(0.4, 1.02))
        ax.axhline(0.5, color="#94a3b8", linewidth=1)
        ax.grid(alpha=0.15)
        ax.legend(loc="lower right", frameon=False)
    for result, color, label in zip(report["models"].values(), colors, labels):
        axes[1, 0].plot(result["roc_false_positive"], result["roc_true_positive"], color=color,
                        label=f"{label}: {result['auc']:.3f}")
    axes[1, 0].plot([0, 1], [0, 1], "--", color="#94a3b8", label="Chance: 0.500")
    axes[1, 0].set(title="Held-out predictions: ROC curve", xlabel="False positive rate", ylabel="True positive rate")
    axes[1, 0].legend(loc="lower right", frameon=False)
    values = [result["brier"] for result in report["models"].values()]
    bars = axes[1, 1].bar(labels, values, color=colors, width=0.5)
    axes[1, 1].bar_label(bars, fmt="%.4f", padding=5)
    axes[1, 1].set(title="Held-out probability error", ylabel="Brier score (lower is better)", ylim=(0, max(values) * 1.4))
    fig.suptitle("Outage risk: logistic regression vs. random forest", fontsize=19, fontweight="bold", y=0.98)
    fig.text(0.5, 0.93, f"{report['areas']} areas | {report['damaged_areas']} with damage | {report['event']} | identical 5-fold splits", ha="center")
    fig.text(0.5, 0.035, "Shading: +/- 1 fold standard deviation, not a confidence interval. Learning curves use training-set size, not epochs.\n"
             "Validation holds out areas within one storm. It does not establish performance on future storms.", ha="center", fontsize=10, color="#475569")
    fig.tight_layout(rect=(0.02, 0.09, 0.98, 0.90), h_pad=2.8, w_pad=2.5)
    fig.savefig(folder / "risk_training_comparison.png", dpi=180)
    fig.savefig(folder / "risk_training_comparison.svg")
    print("Saved training graphs to", folder)


if __name__ == "__main__":
    plot()
