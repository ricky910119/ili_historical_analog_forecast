"""Scoring, kept strictly downstream of segment selection.

Nothing in this module is imported by the forecast path: predictions are generated and
written first, then target actuals are read and scored in a separate step. No score can
reach back into candidate selection, and no generated forecast file is rewritten here.
"""
from __future__ import annotations

import csv
import math

from .util import require

EVALUATION_LABEL = "exploratory retrospective evaluation"
EVALUATION_CAVEAT = (
    "This method was proposed after looking at the 2026 curve. A 2026 backtest is an "
    "exploratory retrospective evaluation only: it is not an untouched holdout, not an "
    "out-of-sample test and not promotion evidence."
)
SNAPSHOT_CAVEAT = ("current database snapshot; not historical as-of replay. Truncating the "
                   "input at the origin bounds availability in time only; it does not resolve "
                   "historical data-revision leakage.")
BASELINE_NAME = "persistence_origin_week"
BASELINE_DEFINITION = "hold the origin week count x[8] flat across H1-H8"
ANALOG_NAME = "historical_analog_v1"
TIMESFM_NAME = "timesfm"


def persistence_baseline(x8, horizons):
    return [float(x8)] * horizons


def metrics(pairs):
    """MAE, WAPE and signed bias over (prediction, actual) pairs with a revealed actual.

    A zero actual denominator is reported as not computable; no epsilon is ever added.
    """
    scorable = [(p, a) for p, a in pairs
                if isinstance(a, float) and math.isfinite(a) and math.isfinite(p)]
    result = {"scorable": len(scorable), "submitted": len(pairs),
              "pending": len(pairs) - len(scorable)}
    if not scorable:
        result.update({"mae": None, "wape": None, "signed_bias_ratio": None,
                       "actual_sum": None,
                       "note": "no revealed actual; metrics not computable"})
        return result
    errors = [p - a for p, a in scorable]
    actual_sum = math.fsum(a for _, a in scorable)
    result["mae"] = math.fsum(abs(e) for e in errors) / len(errors)
    result["actual_sum"] = actual_sum
    if actual_sum == 0:
        result.update({"wape": None, "signed_bias_ratio": None,
                       "note": "sum(actual) is zero; WAPE and signed bias are not computable "
                               "and no epsilon is substituted"})
    else:
        result["wape"] = math.fsum(abs(e) for e in errors) / actual_sum
        result["signed_bias_ratio"] = math.fsum(errors) / actual_sum
        result["note"] = None
    return result


def by_horizon(records, model):
    """Per-horizon metrics for one model over evaluation records."""
    horizons = sorted({r["horizon"] for r in records})
    return {str(h): metrics([(r["prediction"], r["actual"]) for r in records
                             if r["model"] == model and r["horizon"] == h])
            for h in horizons}


def complete_cohort_origins(records, horizons):
    """Origins whose full H1-H8 target actuals are revealed, for a like-for-like comparison."""
    revealed = {}
    for record in records:
        key = record["origin_yearweek"]
        revealed.setdefault(key, set())
        if isinstance(record["actual"], float) and math.isfinite(record["actual"]):
            revealed[key].add(record["horizon"])
    return sorted(o for o, hs in revealed.items() if len(hs) == horizons)


def summarise(records, horizons):
    """Overall, per-horizon and complete-cohort metrics per model on shared scorable rows."""
    models = sorted({r["model"] for r in records})
    cohort = set(complete_cohort_origins(records, horizons))
    summary = {"models": models,
               "evaluation_label": EVALUATION_LABEL,
               "evaluation_caveat": EVALUATION_CAVEAT,
               "snapshot_semantics": SNAPSHOT_CAVEAT,
               "alignment": ("all models are scored on the same origins, the same target DIM "
                             "weeks, the same national nhi_opd+rods target definition and the "
                             "same commonly scorable rows"),
               "complete_h1_h8_origins": sorted(cohort),
               "per_model": {}}
    for model in models:
        rows = [r for r in records if r["model"] == model]
        summary["per_model"][model] = {
            "overall": metrics([(r["prediction"], r["actual"]) for r in rows]),
            "by_horizon": by_horizon(records, model),
            "complete_h1_h8_cohort": metrics(
                [(r["prediction"], r["actual"]) for r in rows
                 if r["origin_yearweek"] in cohort]),
        }
    return summary


def load_timesfm_predictions(path):
    """Optional external TimesFM forecast file. TimesFM is never re-invoked from this repo.

    Expected columns: origin_yearweek, yearweek (target DIM week), prediction.
    """
    if path is None:
        return None, {"status": "NOT AVAILABLE",
                      "note": "no --timesfm-forecast-csv supplied; TimesFM was not re-run"}
    require(path.exists(), f"TimesFM forecast file not found: {path}")
    rows = {}
    with path.open("r", encoding="utf-8-sig", newline="") as stream:
        for row in csv.DictReader(stream):
            require({"origin_yearweek", "yearweek", "prediction"} <= set(row),
                    "TimesFM CSV needs origin_yearweek, yearweek and prediction columns")
            key = (int(row["origin_yearweek"]), int(row["yearweek"]))
            require(key not in rows, f"Duplicate TimesFM prediction for {key}")
            value = float(row["prediction"])
            require(math.isfinite(value), f"Non-finite TimesFM prediction for {key}")
            rows[key] = value
    return rows, {"status": "LOADED", "path": str(path), "rows": len(rows),
                  "note": "supplied by the user from a previous TimesFM run; not re-invoked here"}
