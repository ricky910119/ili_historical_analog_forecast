"""Artifact writers. Raw floats on disk; rounding belongs to the figures only."""
from __future__ import annotations

import math

from .analog import normalised, segment_values
from .data_pg import expected_cells
from .util import digest_file, jsonable, save_json, sha256_text, write_csv

WEEKLY_ACTUALS = "weekly_actuals.csv"
CANDIDATE_SCORES = "candidate_scores.csv"
MATCHED_WINDOW = "matched_window.csv"
FORECAST = "forecast.csv"
EVALUATION = "evaluation.csv"
METRICS = "metrics.json"
RUN = "run.json"


def write_weekly_actuals(out_dir, weeks, weekly):
    rows = []
    for week in weeks:
        row = weekly[week.yearweek]
        rows.append((row.yearweek, row.start, row.end, row.day_count, row.nhi_opd, row.rods,
                     row.ili_total, row.missing_nhi_opd, row.missing_rods,
                     expected_cells(week), row.status))
    write_csv(out_dir / WEEKLY_ACTUALS,
              ["yearweek", "week_start", "week_end", "dim_day_count", "nhi_opd", "rods",
               "ili_total", "missing_cells_nhi_opd", "missing_cells_rods",
               "expected_cells_per_source", "completeness_status"], rows)
    return rows


def write_candidate_scores(out_dir, result):
    rows = []
    for candidate in result.candidates:
        compare, future = candidate.compare_weeks, candidate.future_weeks
        rows.append((
            compare[0].yearweek if compare else "",
            compare[0].start if compare else "",
            compare[-1].yearweek if compare else "",
            compare[-1].end if compare else "",
            future[0].yearweek if future else "",
            future[-1].yearweek if future else "",
            "true" if candidate.eligible else "false",
            ";".join(candidate.reasons),
            candidate.distance if candidate.eligible else math.nan,
            candidate.rank if candidate.rank else "",
            "true" if candidate.selected else "false"))
    write_csv(out_dir / CANDIDATE_SCORES,
              ["compare_start_yearweek", "compare_start_date", "compare_end_yearweek",
               "compare_end_date", "future_start_yearweek", "future_end_yearweek", "eligible",
               "exclusion_reasons", "distance", "rank", "selected"], rows)
    return rows


def write_matched_window(out_dir, result, weekly):
    reference = segment_values(weekly, result.selected.compare_weeks)
    u, v = normalised(list(result.current_values)), normalised(reference)
    rows = []
    for i, (cw, rw) in enumerate(zip(result.current_weeks, result.selected.compare_weeks), 1):
        rows.append((i, cw.yearweek, cw.start, cw.end, result.current_values[i - 1], u[i - 1],
                     rw.yearweek, rw.start, rw.end, reference[i - 1], v[i - 1],
                     abs(u[i - 1] - v[i - 1])))
    write_csv(out_dir / MATCHED_WINDOW,
              ["position", "current_yearweek", "current_week_start", "current_week_end",
               "current_count", "current_normalised", "reference_yearweek",
               "reference_week_start", "reference_week_end", "reference_count",
               "reference_normalised", "absolute_difference"], rows)
    return rows


def write_forecast(out_dir, result, weekly):
    y8 = weekly[result.selected.compare_weeks[-1].yearweek].ili_total
    rows = []
    for h, (target, source, prediction) in enumerate(
            zip(result.target_weeks, result.selected.future_weeks, result.predictions), 1):
        source_count = weekly[source.yearweek].ili_total
        rows.append((result.origin_week.yearweek, h, target.yearweek, target.start, target.end,
                     len(target.days), source.yearweek, source.start, source.end, source_count,
                     source_count / y8, result.x8, y8, prediction))
    write_csv(out_dir / FORECAST,
              ["origin_yearweek", "horizon", "target_yearweek", "target_week_start",
               "target_week_end", "target_dim_day_count", "analog_source_yearweek",
               "analog_source_week_start", "analog_source_week_end", "analog_source_count",
               "relative_ratio_to_reference_denominator", "current_denominator_x8",
               "reference_denominator_y8", "prediction"], rows)
    return rows


def write_evaluation(out_dir, records, filename=EVALUATION):
    """Scores live in their own file; no generated forecast.csv is ever rewritten."""
    rows = [(r["model"], r["origin_yearweek"], r["horizon"], r["target_yearweek"],
             r["target_week_start"], r["target_week_end"], r["prediction"], r["actual"],
             r["actual_status"], r["error"], r["absolute_error"]) for r in records]
    write_csv(out_dir / filename,
              ["model", "origin_yearweek", "horizon", "target_yearweek", "target_week_start",
               "target_week_end", "prediction", "actual", "actual_status", "error",
               "absolute_error"], rows)
    return rows


def input_digest(weekly_rows, spring_digest, config_text):
    """One digest over the weekly input panel, the holiday config and the run settings."""
    panel = "\n".join(f"{r[0]},{r[1]},{r[2]},{r[3]},{r[4]!r},{r[5]!r},{r[6]!r},{r[7]},{r[8]},{r[10]}"
                      for r in weekly_rows)
    return {"weekly_input_digest": sha256_text(panel),
            "spring_festival_config_digest": spring_digest,
            "settings_digest": sha256_text(config_text)}


def finalize(out_dir, report):
    report["artifact_digests"] = {p.name: digest_file(p) for p in sorted(out_dir.iterdir())
                                  if p.is_file() and p.name != RUN}
    save_json(out_dir / RUN, jsonable(report))
    return out_dir / RUN
