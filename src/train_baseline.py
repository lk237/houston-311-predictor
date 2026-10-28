"""Train baseline models that predict whether a 311 request will be closed late.

Only uses information known when the request is created (request title and type,
department, channel, district, SLA, day/hour/month), so the model could be used on new requests.
The split is by time: train on older requests, test on the most recent ones.

Usage:
    python src/train_baseline.py
"""
from __future__ import annotations

import json
import logging
from pathlib import Path

import duckdb
import matplotlib
matplotlib.use("Agg")            # draw charts to files only (no pop-up windows)
import matplotlib.pyplot as plt
import numpy as np
import pandas as pd
import yaml
from sklearn.compose import ColumnTransformer
from sklearn.dummy import DummyClassifier
from sklearn.ensemble import HistGradientBoostingClassifier
from sklearn.inspection import permutation_importance
from sklearn.linear_model import LogisticRegression
from sklearn.metrics import (accuracy_score, f1_score, precision_score, recall_score,
                             roc_auc_score, RocCurveDisplay)
from sklearn.impute import SimpleImputer
from sklearn.pipeline import Pipeline
from sklearn.preprocessing import OneHotEncoder, OrdinalEncoder, StandardScaler

log = logging.getLogger("train")
ROOT = Path(__file__).resolve().parents[1]   # project folder (one level above src/)

# Model inputs ("features"). Everything here is known the moment a request is
# created. We never use closed_at or resolution_hours as inputs: those are only
# known AFTER the outcome, so using them would be "data leakage" (cheating).
# Categorical = text labels; numeric = numbers.
CATEGORICAL = ["title", "case_type", "department", "division", "channel", "district", "sla_unit"]
NUMERIC = ["sla_days", "created_dow", "created_hour", "created_month_num"]


def load_data(db_path: Path) -> pd.DataFrame:
    """Pull the labeled requests out of DuckDB, oldest first."""
    con = duckdb.connect(str(db_path), read_only=True)
    df = con.execute("""
        SELECT title, case_type, department, division, channel, district, sla_unit,
               sla_days, created_dow, created_hour, MONTH(created_at) AS created_month_num,
               created_at, is_overdue::INT AS is_overdue
        FROM requests
        WHERE is_overdue IS NOT NULL      -- skip requests whose outcome isn't known yet
        ORDER BY created_at               -- sorted by time so we can split by date
    """).df()
    con.close()
    # Missing text labels become their own category instead of an error.
    for c in CATEGORICAL:
        df[c] = df[c].fillna("Unknown").astype(str)
    return df


def time_split(df: pd.DataFrame, test_size: float):
    """Train on the oldest 80% of requests and test on the newest 20%.

    A random split would let the model "see the future" (train on December,
    test on March). Splitting by time matches real use: learn from the past,
    predict what comes next.
    """
    cut = int(len(df) * (1 - test_size))
    return df.iloc[:cut], df.iloc[cut:]


def build_models(random_state: int) -> dict[str, Pipeline]:
    """Three models, each wrapped in a Pipeline so the same preparation steps
    are applied to training and test data automatically."""

    # --- Preparation for logistic regression ---------------------------------
    # Logistic regression can't take missing numbers (some requests have no SLA),
    # so fill them with the median, add a "was missing" flag, and scale.
    # Scaling puts all numbers on a similar range so the model trains reliably.
    numeric_lr = Pipeline([
        ("impute", SimpleImputer(strategy="median", add_indicator=True)),
        ("scale", StandardScaler()),
    ])
    # One-hot encoding turns each text label into its own 0/1 column
    # (e.g. channel=Phone -> channel_Phone=1). Labels seen fewer than 20 times
    # are grouped as "infrequent" so the model doesn't memorize rare cases.
    onehot = ColumnTransformer([
        ("cat", OneHotEncoder(handle_unknown="infrequent_if_exist", min_frequency=20), CATEGORICAL),
        ("num", numeric_lr, NUMERIC),
    ])

    # --- Preparation for gradient boosting ------------------------------------
    # Gradient boosting handles missing numbers itself, but allows at most ~255
    # categories per column, so rare categories are grouped together.
    # Ordinal encoding gives each label a number (Phone=0, Email=1, ...), and the
    # model is told these columns are categories, not real quantities.
    ordinal = ColumnTransformer([
        ("cat", OrdinalEncoder(handle_unknown="use_encoded_value", unknown_value=-1,
                               min_frequency=20, max_categories=250), CATEGORICAL),
        ("num", "passthrough", NUMERIC),
    ])
    n_cat = len(CATEGORICAL)

    return {
        # Baseline: ignores the inputs and always predicts the overall late rate.
        # Any real model must beat this, or it isn't learning anything useful.
        "baseline_majority": Pipeline([("prep", onehot), ("clf", DummyClassifier(strategy="prior"))]),
        # Logistic regression: simple and easy to explain; each feature pushes the
        # chance of "late" up or down. class_weight="balanced" stops it from
        # ignoring the smaller class (late requests are ~40% of the data).
        "logistic_regression": Pipeline([("prep", onehot),
                                         ("clf", LogisticRegression(max_iter=2000, class_weight="balanced"))]),
        # Gradient boosting: builds many small decision trees, each fixing the
        # previous ones' mistakes. Usually stronger, and can capture combinations
        # (e.g. "potholes in district B by phone").
        "gradient_boosting": Pipeline([("prep", ordinal),
                                       ("clf", HistGradientBoostingClassifier(
                                           categorical_features=list(range(n_cat)),
                                           class_weight="balanced", random_state=random_state))]),
    }


def evaluate(model, X, y) -> dict:
    """Score a trained model on the test set.

    roc_auc   - how well the model RANKS late requests above on-time ones
                (0.5 = random guessing, 1.0 = perfect). Main metric: it doesn't
                depend on the 0.5 cut-off and isn't fooled by class imbalance.
    precision - of the requests we flagged as late, the share that really were late
    recall    - of the requests that really were late, the share we flagged
    f1        - one number balancing precision and recall
    accuracy  - share of all predictions that were right (can be misleading:
                always guessing "on time" already scores ~58% here)
    """
    proba = model.predict_proba(X)[:, 1]      # predicted chance each request is late
    pred = (proba >= 0.5).astype(int)          # flag as late if chance >= 50%
    return {
        "roc_auc": round(float(roc_auc_score(y, proba)), 4) if y.nunique() > 1 else None,
        "f1": round(float(f1_score(y, pred, zero_division=0)), 4),
        "precision": round(float(precision_score(y, pred, zero_division=0)), 4),
        "recall": round(float(recall_score(y, pred, zero_division=0)), 4),
        "accuracy": round(float(accuracy_score(y, pred)), 4),
    }


def main() -> None:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    cfg = yaml.safe_load(open(ROOT / "config.yaml"))
    reports = ROOT / cfg["paths"]["reports"]
    reports.mkdir(parents=True, exist_ok=True)

    # 1. Load and split the data (X = inputs, y = answer: 1 late, 0 on time)
    df = load_data(ROOT / cfg["paths"]["database"])
    train, test = time_split(df, cfg["model"]["test_size"])
    feats = CATEGORICAL + NUMERIC
    X_tr, y_tr, X_te, y_te = train[feats], train["is_overdue"], test[feats], test["is_overdue"]
    log.info("Train: %d rows (%s to %s) | Test: %d rows (%s to %s) | overdue rate %.1f%%",
             len(train), train.created_at.min().date(), train.created_at.max().date(),
             len(test), test.created_at.min().date(), test.created_at.max().date(),
             100 * df.is_overdue.mean())

    # 2. Train each model on the older data and score it on the newer data
    results, fitted = {}, {}
    for name, model in build_models(cfg["model"]["random_state"]).items():
        model.fit(X_tr, y_tr)
        fitted[name] = model
        results[name] = evaluate(model, X_te, y_te)
        log.info("%-20s %s", name, results[name])

    # 3. ROC curve chart: the further a curve bends toward the top-left corner,
    #    the better the model separates late from on-time requests.
    fig, ax = plt.subplots(figsize=(6, 5))
    for name, model in fitted.items():
        if name != "baseline_majority":
            RocCurveDisplay.from_estimator(model, X_te, y_te, name=name.replace("_", " "), ax=ax)
    ax.plot([0, 1], [0, 1], "--", color="grey", label="chance")
    ax.set_title("Predicting late 311 requests (test set)")
    ax.legend(loc="lower right")
    fig.tight_layout()
    fig.savefig(reports / "roc_curves.png", dpi=150)
    plt.close(fig)

    # 4. Which features matter most for the best model?
    #    Permutation importance: shuffle one column at a time and measure how much
    #    the ROC-AUC drops. A big drop means the model relies on that column.
    #    Uses a 5,000-row sample of the test set to keep it fast.
    best = max((n for n in results if n != "baseline_majority"),
               key=lambda n: results[n]["roc_auc"] or 0)
    sample = X_te.sample(min(len(X_te), 5000), random_state=0)
    imp = permutation_importance(fitted[best], sample, y_te.loc[sample.index],
                                 scoring="roc_auc", n_repeats=5, random_state=0)
    importance = (pd.Series(imp.importances_mean, index=feats)
                  .sort_values(ascending=False).round(4))

    # 5. Save everything to reports/metrics.json and print it
    summary = {
        "rows": len(df), "train_rows": len(train), "test_rows": len(test),
        "overdue_rate": round(float(df.is_overdue.mean()), 4),
        "best_model": best, "metrics": results,
        "feature_importance_roc_auc_drop": importance.to_dict(),
    }
    (reports / "metrics.json").write_text(json.dumps(summary, indent=2))
    print(json.dumps(summary, indent=2))


if __name__ == "__main__":
    main()