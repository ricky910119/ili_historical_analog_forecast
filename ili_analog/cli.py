"""Command line entry points: preflight, forecast and backtest.

Run as:  python -m ili_analog.cli <mode> ...

The forecast path never reads a target actual after the origin. The backtest path keeps
generation and scoring in two separate phases: every forecast is written to disk before any
future actual is read, and no score can reach back into candidate selection.
"""
from __future__ import annotations

import argparse
import json
import math
import platform
import sys
import time
from datetime import date, datetime, timezone
from pathlib import Path

from . import artifacts
from .analog import (ALGORITHM_NOTES, ALGORITHM_VERSION, FORECAST_HORIZONS,
                     HISTORY_PLOT_WEEKS, LOOKBACK_WEEKS, MIN_HISTORY_PLOT_WEEKS,
                     check_supported_years, plan_weeks, resolve_origin, run_analog,
                     screen_candidates)
from .data_pg import (COUNTIES, SOURCES, panel_metadata, read_calendar, read_panel,
                      weekly_rows)
from .dim_calendar import CALENDAR_RULES
from .evaluate import (ANALOG_NAME, BASELINE_DEFINITION, BASELINE_NAME, EVALUATION_CAVEAT,
                       EVALUATION_LABEL, SNAPSHOT_CAVEAT, TIMESFM_NAME,
                       load_timesfm_predictions, persistence_baseline, summarise)
from .holidays import load_spring_festival
from .plots import plot_analog_comparison, plot_forecast
from .util import Ineligible, command, jsonable, require, save_json

DEFAULT_CONFIG = Path(__file__).resolve().parent.parent / "configs" / "spring_festival.json"


def algorithm_block():
    return {
        "version": ALGORITHM_VERSION,
        "lookback_weeks": LOOKBACK_WEEKS,
        "forecast_horizons": FORECAST_HORIZONS,
        "target": "national ILI = nhi_opd + rods, weekly visit counts on DIM weeks",
        "normalisation": "u[i] = x[i]/x[8]; v[i] = y[i]/y[8] (never z-score)",
        "distance": "mean(abs(u[i] - v[i])) for i = 1..8",
        "selection": "single smallest distance; ties broken toward the earlier candidate end week",
        "scaling": "prediction[h] = x[8] * y_future[h] / y[8]",
        "notes": ALGORITHM_NOTES,
        "probabilistic_model": None,
        "interval_policy": "no calibrated uncertainty model exists in v1; no interval is produced",
    }


def environment_block(repo):
    return {"python": platform.python_version(), "platform": platform.platform(),
            "git_head": command(["git", "rev-parse", "HEAD"], repo),
            "git_status": command(["git", "status", "--short", "--branch"], repo)}


def make_output_dir(root, mode):
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S_%fZ")
    out = Path(root) / f"{mode}_{stamp}"
    out.mkdir(parents=True, exist_ok=False)
    return out


def settings_block(args, origins):
    return {"mode": args.mode, "origins": list(origins),
            "settled_cutoff": args.settled_cutoff.isoformat(),
            "settled_cutoff_source": "explicit --settled-cutoff (required; never clock-derived)",
            "reference_year": args.reference_year, "target_year": args.target_year,
            "history_plot_weeks": args.history_weeks,
            "spring_festival_config": str(args.spring_festival_config),
            "output_dir": str(args.output_dir)}


def load_environment(args):
    """Config and DIM calendar. No target table is touched here."""
    spring = load_spring_festival(args.spring_festival_config)
    require(spring.covers_year(args.reference_year),
            f"Spring Festival config has no entry for reference year {args.reference_year}")
    require(spring.covers_year(args.target_year),
            f"Spring Festival config has no entry for target year {args.target_year}")
    calendar = read_calendar()
    return calendar, spring


def prepare_panel(args, calendar, origin_weeks):
    """One read covering every DIM week the requested origins need."""
    plans = [plan_weeks(calendar, week, args.reference_year, args.history_weeks)
             for week in origin_weeks]
    weeks = sorted({w.yearweek: w for plan in plans for w in plan[0]}.values(),
                   key=lambda w: w.start)
    first, last = min(p[1] for p in plans), max(p[2] for p in plans)
    panel, rows_read = read_panel(first, last)
    return weeks, weekly_rows(panel, weeks), panel_metadata(first, last, rows_read)


def as_of(weeks, weekly, origin_week):
    """Hard isolation: an origin only ever sees DIM weeks ending at or before itself."""
    visible = [w for w in weeks if w.end <= origin_week.end]
    return visible, {w.yearweek: weekly[w.yearweek] for w in visible}


def completeness_block(weeks, weekly):
    incomplete = [weekly[w.yearweek] for w in weeks if weekly[w.yearweek].status != "complete"]
    return {"policy": ("a source-week is complete only when every DIM date x all 22 counties "
                       "has a valid observed row; nothing is zero-filled or stitched over"),
            "weeks_evaluated": len(weeks), "complete": len(weeks) - len(incomplete),
            "incomplete": len(incomplete),
            "incomplete_weeks": [{"yearweek": r.yearweek, "week_start": r.start.isoformat(),
                                  "week_end": r.end.isoformat(),
                                  "missing_cells_nhi_opd": r.missing_nhi_opd,
                                  "missing_cells_rods": r.missing_rods} for r in incomplete[:200]],
            "incomplete_weeks_truncated": len(incomplete) > 200,
            "counties": len(COUNTIES), "sources": list(SOURCES)}


def candidate_block(records, selected=None):
    reasons = {}
    for record in records:
        for reason in record.reasons:
            reasons[reason] = reasons.get(reason, 0) + 1
    block = {"enumeration": ("every DIM week of the reference year is offered as a candidate "
                             "end week; excluded ones stay in candidate_scores.csv with a reason"),
             "total": len(records), "eligible": sum(1 for r in records if r.eligible),
             "excluded": sum(1 for r in records if not r.eligible),
             "exclusion_reason_counts": dict(sorted(reasons.items()))}
    if selected is not None:
        block["selected"] = {
            "compare_start_yearweek": selected.compare_weeks[0].yearweek,
            "compare_end_yearweek": selected.compare_weeks[-1].yearweek,
            "compare_start_date": selected.compare_weeks[0].start.isoformat(),
            "compare_end_date": selected.compare_weeks[-1].end.isoformat(),
            "future_start_yearweek": selected.future_weeks[0].yearweek,
            "future_end_yearweek": selected.future_weeks[-1].yearweek,
            "distance": selected.distance, "rank": selected.rank}
    return block


def dim_block(calendar, weeks, origin_week=None, target_weeks=()):
    block = {"rules": CALENDAR_RULES, "weeks_in_calendar": len(calendar),
             "first_week": calendar.at(0).yearweek, "last_week": calendar.at(-1).yearweek,
             "weeks_used": len(weeks),
             "day_counts_used": sorted({len(w.days) for w in weeks})}
    if origin_week is not None:
        block["origin_week"] = {"yearweek": origin_week.yearweek,
                                "week_start": origin_week.start.isoformat(),
                                "week_end": origin_week.end.isoformat(),
                                "dim_day_count": len(origin_week.days)}
    if target_weeks:
        block["target_weeks"] = [{"horizon": h, "yearweek": w.yearweek,
                                  "week_start": w.start.isoformat(),
                                  "week_end": w.end.isoformat(),
                                  "dim_day_count": len(w.days)}
                                 for h, w in enumerate(target_weeks, 1)]
    return block


def write_forecast_artifacts(out_dir, result, weekly, weeks, report, make_plots=True):
    weekly_rows_written = artifacts.write_weekly_actuals(out_dir, weeks, weekly)
    artifacts.write_candidate_scores(out_dir, result)
    artifacts.write_matched_window(out_dir, result, weekly)
    artifacts.write_forecast(out_dir, result, weekly)
    y8 = weekly[result.selected.compare_weeks[-1].yearweek].ili_total
    report["levels"] = {
        "current_denominator_yearweek": result.current_weeks[-1].yearweek,
        "current_denominator_x8": result.x8,
        "reference_denominator_yearweek": result.selected.compare_weeks[-1].yearweek,
        "reference_denominator_y8": y8, "scale_ratio_x8_over_y8": result.x8 / y8}
    report["forecast"] = [
        {"horizon": h, "target_yearweek": t.yearweek, "week_start": t.start.isoformat(),
         "week_end": t.end.isoformat(), "analog_source_yearweek": s.yearweek,
         "prediction": p}
        for h, (t, s, p) in enumerate(
            zip(result.target_weeks, result.selected.future_weeks, result.predictions), 1)]
    if make_plots:
        figure_a, font = plot_analog_comparison(out_dir, result, weekly)
        figure_b, _ = plot_forecast(out_dir, result, weekly)
        report["figures"] = {"analog_comparison": figure_a, "forecast": figure_b,
                             "font": font,
                             "rounding": "figures round for display only; CSV keeps raw floats",
                             "interval_policy": "no uncertainty band is drawn"}
    return weekly_rows_written


def run_preflight(args, report, out_dir):
    calendar, spring = load_environment(args)
    origin_week = resolve_origin(calendar, args.origin_yearweek, args.settled_cutoff)
    weeks, weekly, panel_meta = prepare_panel(args, calendar, [origin_week])
    weeks, weekly = as_of(weeks, weekly, origin_week)
    report["data"] = panel_meta
    report["dim_calendar"] = dim_block(calendar, weeks, origin_week)
    report["spring_festival"] = spring.metadata(weeks)
    report["completeness"] = completeness_block(weeks, weekly)
    screened = screen_candidates(calendar, weekly, spring, args.reference_year)
    report["candidates"] = candidate_block(screened)
    weekly_written = artifacts.write_weekly_actuals(out_dir, weeks, weekly)
    report["digests"] = artifacts.input_digest(weekly_written, spring.digest,
                                               json.dumps(report["settings"], sort_keys=True))
    try:
        result = run_analog(calendar, weekly, spring, origin_week, args.reference_year,
                            args.target_year, args.history_weeks)
    except Ineligible as exc:
        report["status"] = "INELIGIBLE"
        report["ineligible_reason"] = str(exc)
        report["forecast"] = "NOT PRODUCED"
        return
    artifacts.write_candidate_scores(out_dir, result)
    report["candidates"] = candidate_block(result.candidates, result.selected)
    report["dim_calendar"] = dim_block(calendar, weeks, origin_week, result.target_weeks)
    report["status"] = "PREFLIGHT_PASSED"
    report["forecast"] = "NOT PRODUCED (preflight only)"


def run_forecast(args, report, out_dir):
    calendar, spring = load_environment(args)
    origin_week = resolve_origin(calendar, args.origin_yearweek, args.settled_cutoff)
    weeks, weekly, panel_meta = prepare_panel(args, calendar, [origin_week])
    weeks, weekly = as_of(weeks, weekly, origin_week)
    report["data"] = panel_meta
    report["spring_festival"] = spring.metadata(weeks)
    report["completeness"] = completeness_block(weeks, weekly)
    try:
        result = run_analog(calendar, weekly, spring, origin_week, args.reference_year,
                            args.target_year, args.history_weeks)
    except Ineligible as exc:
        report["status"] = "INELIGIBLE"
        report["ineligible_reason"] = str(exc)
        report["forecast"] = "NOT PRODUCED"
        report["dim_calendar"] = dim_block(calendar, weeks, origin_week)
        report["candidates"] = candidate_block(
            screen_candidates(calendar, weekly, spring, args.reference_year))
        weekly_written = artifacts.write_weekly_actuals(out_dir, weeks, weekly)
        report["digests"] = artifacts.input_digest(
            weekly_written, spring.digest, json.dumps(report["settings"], sort_keys=True))
        return
    report["dim_calendar"] = dim_block(calendar, weeks, origin_week, result.target_weeks)
    report["candidates"] = candidate_block(result.candidates, result.selected)
    weekly_written = write_forecast_artifacts(out_dir, result, weekly, weeks, report)
    report["digests"] = artifacts.input_digest(weekly_written, spring.digest,
                                               json.dumps(report["settings"], sort_keys=True))
    report["status"] = "FORECAST_PRODUCED"


def read_target_actuals(args, calendar, target_weeks):
    """Phase two only: future actuals, read after every forecast is already on disk."""
    weeks = sorted({w.yearweek: w for w in target_weeks}.values(), key=lambda w: w.start)
    panel, rows_read = read_panel(weeks[0].start, weeks[-1].end)
    rows = weekly_rows(panel, weeks)
    actuals, status = {}, {}
    for week in weeks:
        row = rows[week.yearweek]
        if week.end > args.settled_cutoff:
            actuals[week.yearweek], status[week.yearweek] = math.nan, "pending_unsettled"
        elif row.status != "complete":
            actuals[week.yearweek], status[week.yearweek] = math.nan, "pending_incomplete"
        else:
            actuals[week.yearweek], status[week.yearweek] = row.ili_total, "revealed"
    return actuals, status, rows, panel_metadata(weeks[0].start, weeks[-1].end, rows_read)


def run_backtest(args, report, out_dir):
    calendar, spring = load_environment(args)
    origins = [resolve_origin(calendar, yw, args.settled_cutoff)
               for yw in sorted(set(args.origin_yearweek))]
    weeks, weekly, panel_meta = prepare_panel(args, calendar, origins)
    report["data"] = panel_meta
    report["spring_festival"] = spring.metadata(weeks)
    report["completeness"] = completeness_block(weeks, weekly)
    report["isolation"] = (
        "phase 1 generates and writes every forecast using only DIM weeks ending at or before "
        "its own origin; phase 2 then reads target actuals and scores them. Selection never "
        "sees a future actual and no generated forecast file is modified by scoring.")
    report["evaluation_label"] = EVALUATION_LABEL
    report["evaluation_caveat"] = EVALUATION_CAVEAT
    report["snapshot_semantics"] = SNAPSHOT_CAVEAT

    # ---- phase 1: generation ----
    origins_dir = out_dir / "origins"
    origins_dir.mkdir()
    results, report["origins"] = {}, []
    for origin_week in origins:
        origin_dir = origins_dir / str(origin_week.yearweek)
        origin_dir.mkdir()
        visible_weeks, visible_weekly = as_of(weeks, weekly, origin_week)
        entry = {"origin_yearweek": origin_week.yearweek,
                 "week_start": origin_week.start.isoformat(),
                 "week_end": origin_week.end.isoformat(),
                 "output_dir": origin_dir.name, "status": "FAILED"}
        report["origins"].append(entry)
        sub = {"status": "FAILED", "algorithm": algorithm_block(),
               "settings": {**report["settings"], "origins": [origin_week.yearweek]},
               "data": panel_meta, "snapshot_semantics": SNAPSHOT_CAVEAT,
               "spring_festival": spring.metadata(visible_weeks),
               "completeness": completeness_block(visible_weeks, visible_weekly),
               "environment": report["environment"]}
        try:
            result = run_analog(calendar, visible_weekly, spring, origin_week,
                                args.reference_year, args.target_year, args.history_weeks)
        except Ineligible as exc:
            sub["status"] = entry["status"] = "INELIGIBLE"
            sub["ineligible_reason"] = entry["ineligible_reason"] = str(exc)
            sub["dim_calendar"] = dim_block(calendar, visible_weeks, origin_week)
            sub["candidates"] = candidate_block(
                screen_candidates(calendar, visible_weekly, spring, args.reference_year))
            written = artifacts.write_weekly_actuals(origin_dir, visible_weeks, visible_weekly)
            sub["digests"] = artifacts.input_digest(
                written, spring.digest, json.dumps(sub["settings"], sort_keys=True))
            artifacts.finalize(origin_dir, sub)
            continue
        sub["dim_calendar"] = dim_block(calendar, visible_weeks, origin_week, result.target_weeks)
        sub["candidates"] = candidate_block(result.candidates, result.selected)
        written = write_forecast_artifacts(origin_dir, result, visible_weekly, visible_weeks, sub)
        sub["digests"] = artifacts.input_digest(
            written, spring.digest, json.dumps(sub["settings"], sort_keys=True))
        sub["status"] = entry["status"] = "FORECAST_PRODUCED"
        entry["selected_compare_end_yearweek"] = result.selected.compare_weeks[-1].yearweek
        entry["distance"] = result.selected.distance
        artifacts.finalize(origin_dir, sub)
        results[origin_week.yearweek] = (result, visible_weekly, origin_dir)
    report["generated_origins"] = sorted(results)
    if not results:
        report["status"] = "INELIGIBLE"
        report["scoring"] = "NOT RUN (no forecast was produced)"
        return
    if args.no_score:
        report["status"] = "BACKTEST_FORECASTS_PRODUCED"
        report["scoring"] = "NOT RUN (--no-score)"
        return

    # ---- phase 2: scoring, strictly after every forecast is on disk ----
    timesfm, timesfm_meta = load_timesfm_predictions(args.timesfm_forecast_csv)
    report["timesfm_comparison"] = timesfm_meta
    target_weeks = [w for result, _, _ in results.values() for w in result.target_weeks]
    actuals, statuses, _, actual_meta = read_target_actuals(args, calendar, target_weeks)
    report["target_actual_read"] = actual_meta
    records = []
    for origin_yearweek, (result, _, _) in sorted(results.items()):
        baseline = persistence_baseline(result.x8, len(result.target_weeks))
        series = {ANALOG_NAME: list(result.predictions), BASELINE_NAME: baseline}
        if timesfm is not None:
            values = [timesfm.get((origin_yearweek, w.yearweek), math.nan)
                      for w in result.target_weeks]
            if any(math.isfinite(v) for v in values):
                series[TIMESFM_NAME] = values
        for model, values in series.items():
            for h, (week, prediction) in enumerate(zip(result.target_weeks, values), 1):
                actual = actuals[week.yearweek]
                scorable = math.isfinite(actual) and math.isfinite(prediction)
                records.append({
                    "model": model, "origin_yearweek": origin_yearweek, "horizon": h,
                    "target_yearweek": week.yearweek, "target_week_start": week.start,
                    "target_week_end": week.end, "prediction": prediction, "actual": actual,
                    "actual_status": statuses[week.yearweek],
                    "error": prediction - actual if scorable else math.nan,
                    "absolute_error": abs(prediction - actual) if scorable else math.nan})
    artifacts.write_evaluation(out_dir, records)
    summary = summarise(records, FORECAST_HORIZONS)
    summary["baseline"] = {"name": BASELINE_NAME, "definition": BASELINE_DEFINITION}
    summary["timesfm"] = timesfm_meta
    summary["pending_policy"] = ("an unsettled or incomplete target week stays pending and is "
                                 "excluded from every metric; it is never treated as 0")
    save_json(out_dir / artifacts.METRICS, jsonable(summary))
    report["metrics"] = summary
    # A second figure per origin with revealed actuals; the phase-1 figure is left untouched.
    for origin_yearweek, (result, visible_weekly, origin_dir) in sorted(results.items()):
        plot_forecast(origin_dir, result, visible_weekly, actuals, "_scored")
    report["status"] = "BACKTEST_SCORED"


def parse_args(argv=None):
    parser = argparse.ArgumentParser(prog="python -m ili_analog.cli", description=__doc__)
    subparsers = parser.add_subparsers(dest="mode", required=True)
    for name, help_text in (("preflight", "check config, DIM, completeness and candidate pool"),
                            ("forecast", "produce H1-H8 for one origin"),
                            ("backtest", "rebuild inputs per origin, then score separately")):
        sub = subparsers.add_parser(name, help=help_text)
        sub.add_argument("--settled-cutoff", required=True,
                         help="ISO date of the last settled week end; required, never derived "
                              "from the run clock")
        sub.add_argument("--reference-year", type=int, default=2025)
        sub.add_argument("--target-year", type=int, default=2026)
        sub.add_argument("--output-dir", default="outputs")
        sub.add_argument("--spring-festival-config", default=str(DEFAULT_CONFIG))
        sub.add_argument("--history-weeks", type=int, default=HISTORY_PLOT_WEEKS,
                         help=f"DIM weeks of current actuals shown in the forecast figure "
                              f"(minimum {MIN_HISTORY_PLOT_WEEKS})")
        if name == "backtest":
            sub.add_argument("--origin-yearweek", type=int, action="append", required=True,
                             help="repeat once per origin")
            sub.add_argument("--timesfm-forecast-csv", default=None,
                             help="optional existing TimesFM forecast CSV "
                                  "(origin_yearweek, yearweek, prediction); TimesFM is never "
                                  "re-invoked from this repository")
            sub.add_argument("--no-score", action="store_true",
                             help="stop after phase 1; read no target actual")
        else:
            sub.add_argument("--origin-yearweek", type=int, required=True)
    args = parser.parse_args(argv)
    args.settled_cutoff = date.fromisoformat(args.settled_cutoff)
    args.spring_festival_config = Path(args.spring_festival_config)
    args.output_dir = Path(args.output_dir)
    if getattr(args, "timesfm_forecast_csv", None):
        args.timesfm_forecast_csv = Path(args.timesfm_forecast_csv)
    require(args.history_weeks >= MIN_HISTORY_PLOT_WEEKS,
            f"--history-weeks must be at least {MIN_HISTORY_PLOT_WEEKS}")
    check_supported_years(args.reference_year, args.target_year)
    return args


def main(argv=None):
    args = parse_args(argv)
    origins = (args.origin_yearweek if isinstance(args.origin_yearweek, list)
               else [args.origin_yearweek])
    out_dir = make_output_dir(args.output_dir, args.mode)
    repo = Path(__file__).resolve().parent.parent
    report = {"status": "FAILED", "algorithm": algorithm_block(),
              "settings": settings_block(args, origins),
              "snapshot_semantics": SNAPSHOT_CAVEAT,
              "scope": "isolated research model; not a candidate in the "
                       "disease_forecast_llm_model_router production pool",
              "environment": environment_block(repo)}
    started = time.perf_counter()
    try:
        {"preflight": run_preflight, "forecast": run_forecast,
         "backtest": run_backtest}[args.mode](args, report, out_dir)
    except Exception as exc:  # recorded in run.json, then re-raised
        report["status"] = "FAILED"
        report["error"] = f"{type(exc).__name__}: {exc}"
        raise
    finally:
        report["seconds"] = time.perf_counter() - started
        artifacts.finalize(out_dir, report)
        print(out_dir.resolve())
        print(f"status={report['status']}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
