"""The fixed v1 historical-analog algorithm.

Current year: x[1..8] are the eight complete DIM weeks ending at the origin.
Reference year: y[1..8] are an eligible eight-week candidate segment.
Both are divided by their own last week:  u[i] = x[i]/x[8],  v[i] = y[i]/y[8].
distance = mean(|u[i] - v[i]|) over i = 1..8; the single smallest distance wins, ties
broken toward the earlier candidate end week.
prediction[h] = x[8] * y_future[h] / y[8]  for h = 1..8.

Deliberately absent, and never to be added to v1: z-score normalisation, DTW, time warping,
multi-segment or top-k averaging, recent-trend blending, manual reshaping, smoothing,
injected noise, forced upward drift, and any use of future actuals to pick or adjust a
segment.
"""
from __future__ import annotations

import math
from collections import namedtuple

from .dim_calendar import year_of
from .util import Ineligible, require

ALGORITHM_VERSION = "historical-analog-v1"
LOOKBACK_WEEKS = 8
FORECAST_HORIZONS = 8
SUPPORTED_REFERENCE_YEAR = 2025
SUPPORTED_TARGET_YEAR = 2026
HISTORY_PLOT_WEEKS = 26
MIN_HISTORY_PLOT_WEEKS = 16

ALGORITHM_NOTES = (
    "normalisation: divide each 8-week segment by its own last week (never z-score); "
    "distance: mean absolute difference of the two normalised segments; "
    "selection: single nearest segment, ties broken toward the earlier candidate end week; "
    "scaling: prediction[h] = x8 * y_future[h] / y8; "
    "no DTW, time warping, top-k averaging, trend blending, smoothing, noise or reshaping"
)

Candidate = namedtuple(
    "Candidate",
    "end_position compare_weeks future_weeks eligible reasons distance rank selected")

AnalogResult = namedtuple(
    "AnalogResult",
    "origin_week current_weeks current_values x8 target_weeks candidates selected "
    "predictions history_weeks")


def check_supported_years(reference_year, target_year):
    """v1 is pinned to 2025 -> 2026. Other years are refused, not silently generalised."""
    require(int(reference_year) == SUPPORTED_REFERENCE_YEAR
            and int(target_year) == SUPPORTED_TARGET_YEAR,
            f"{ALGORITHM_VERSION} supports only reference-year {SUPPORTED_REFERENCE_YEAR} "
            f"with target-year {SUPPORTED_TARGET_YEAR}; got {reference_year} -> {target_year}. "
            "The method is not generalised to other year pairs and this run refuses to "
            "pretend otherwise.")


def resolve_origin(calendar, origin_yearweek, settled_cutoff):
    """The origin must exist in DIM and end no later than the explicit settled cutoff."""
    week = calendar.week(origin_yearweek)
    require(week.end <= settled_cutoff,
            f"Origin {origin_yearweek} ends {week.end}, after the settled cutoff "
            f"{settled_cutoff}; a live forecast may not use unsettled weeks")
    return week


def candidate_end_positions(calendar, reference_year):
    """Enumeration set: every DIM week of the reference year is offered as a candidate end.

    Ineligible ones stay in candidate_scores.csv with their exclusion reason rather than
    being silently dropped, so the candidate denominator is auditable.
    """
    return [i for i, week in enumerate(calendar.weeks)
            if year_of(week.yearweek) == reference_year]


def plan_weeks(calendar, origin_week, reference_year, history_plot_weeks=HISTORY_PLOT_WEEKS):
    """Every DIM week whose actuals this run needs, and the date range to read.

    Candidates whose compare or future window leaves the reference year are excluded on the
    calendar alone, so their weeks are never read.
    """
    origin_position = calendar.position(origin_week.yearweek)
    needed = {w.yearweek: w for w in calendar.weeks if year_of(w.yearweek) == reference_year}
    first_history = max(0, origin_position - max(history_plot_weeks, LOOKBACK_WEEKS) + 1)
    for week in calendar.slice(first_history, origin_position):
        needed[week.yearweek] = week
    weeks = sorted(needed.values(), key=lambda w: w.start)
    require(bool(weeks), "No DIM weeks to read")
    return weeks, weeks[0].start, origin_week.end


def segment_values(weekly, weeks):
    return [weekly[w.yearweek].ili_total for w in weeks]


def normalised(values):
    """values divided by the segment's own last value; the denominator is checked upstream."""
    denominator = values[-1]
    return [v / denominator for v in values]


def segment_distance(current_normalised, candidate_normalised):
    require(len(current_normalised) == len(candidate_normalised) == LOOKBACK_WEEKS,
            "Segment length must be exactly the fixed lookback")
    return math.fsum(abs(a - b) for a, b in
                     zip(current_normalised, candidate_normalised)) / LOOKBACK_WEEKS


def build_current_window(calendar, weekly, spring, origin_week, target_year):
    """x[1..8]: the eight DIM weeks ending at the origin, all inside the target year."""
    weeks = calendar.window_ending_at(origin_week.yearweek, LOOKBACK_WEEKS)
    outside = [w.yearweek for w in weeks if year_of(w.yearweek) != target_year]
    if outside:
        raise Ineligible(
            f"current_window_not_in_target_year: the {LOOKBACK_WEEKS}-week window ending at "
            f"{origin_week.yearweek} includes DIM weeks outside {target_year} ({outside}); "
            "the window is never borrowed from the previous year")
    incomplete = [w.yearweek for w in weeks if weekly[w.yearweek].status != "complete"]
    if incomplete:
        raise Ineligible(
            f"current_window_incomplete: DIM week(s) {incomplete} are not complete for all "
            "22 counties and both sources; gaps are never skipped, stitched over, zero-filled "
            "or worked around by silently choosing an earlier origin")
    holiday = spring.overlap(origin_week)
    if holiday:
        raise Ineligible(
            f"current_denominator_spring_festival_week: origin {origin_week.yearweek} "
            f"({origin_week.start}..{origin_week.end}) overlaps {holiday['event']} "
            f"({holiday['first_day']}..{holiday['last_day']}); v1 refuses to use a Spring "
            "Festival week as the ratio denominator")
    values = segment_values(weekly, weeks)
    if not values[-1] > 0:
        raise Ineligible(
            f"current_denominator_not_positive: x[8] at {origin_week.yearweek} is "
            f"{values[-1]}; the ratio denominator must be strictly positive")
    return weeks, values


def screen_candidates(calendar, weekly, spring, reference_year):
    """Structural eligibility of every reference-year candidate, independent of this year.

    Screening never looks at the current-year window, so preflight can report the candidate
    pool even when the current-year window itself is ineligible.
    """
    records = []
    for position in candidate_end_positions(calendar, reference_year):
        reasons, compare, future = [], (), ()
        if position - LOOKBACK_WEEKS + 1 < 0:
            reasons.append("dim_calendar_missing_compare_weeks")
        else:
            compare = calendar.slice(position - LOOKBACK_WEEKS + 1, position)
        if position + FORECAST_HORIZONS >= len(calendar):
            reasons.append("dim_calendar_missing_future_weeks")
        else:
            future = calendar.slice(position + 1, position + FORECAST_HORIZONS)
        if compare and any(year_of(w.yearweek) != reference_year for w in compare):
            reasons.append("compare_window_not_in_reference_year")
        if future and any(year_of(w.yearweek) != reference_year for w in future):
            reasons.append("future_window_not_in_reference_year")
        if not reasons:
            if any(weekly[w.yearweek].status != "complete" for w in compare):
                reasons.append("incomplete_compare_week")
            if any(weekly[w.yearweek].status != "complete" for w in future):
                reasons.append("incomplete_future_week")
            if spring.overlap(compare[-1]):
                reasons.append("spring_festival_denominator_week")
        if not reasons and not segment_values(weekly, compare)[-1] > 0:
            reasons.append("nonpositive_denominator")
        records.append(Candidate(position, tuple(compare), tuple(future), not reasons,
                                 tuple(reasons), math.nan, None, False))
    return records


def score_candidates(records, weekly, current_normalised):
    """Attach the distance, rank and selection to already-screened candidates."""
    records = [c._replace(distance=segment_distance(
        current_normalised, normalised(segment_values(weekly, c.compare_weeks)))
        if c.eligible else math.nan) for c in records]
    # Deterministic order: smallest distance first, ties to the earlier candidate end week.
    order = sorted((c for c in records if c.eligible),
                   key=lambda c: (c.distance, c.compare_weeks[-1].start))
    ranked = {c.end_position: rank for rank, c in enumerate(order, 1)}
    records = [c._replace(rank=ranked.get(c.end_position),
                          selected=ranked.get(c.end_position) == 1) for c in records]
    selected = next((c for c in records if c.selected), None)
    if selected is None:
        raise Ineligible(
            "no_eligible_candidate: no reference-year segment satisfies the completeness, "
            "calendar-year, positive-denominator and Spring Festival rules; v1 does not fall "
            "back to a default forecast")
    return records, selected


def run_analog(calendar, weekly, spring, origin_week, reference_year, target_year,
               history_plot_weeks=HISTORY_PLOT_WEEKS):
    """Full selection and scaling for one origin. Reads no actual after the origin."""
    check_supported_years(reference_year, target_year)
    current_weeks, current_values = build_current_window(
        calendar, weekly, spring, origin_week, target_year)
    x8 = current_values[-1]
    candidates, selected = score_candidates(
        screen_candidates(calendar, weekly, spring, reference_year), weekly,
        normalised(current_values))
    y8 = weekly[selected.compare_weeks[-1].yearweek].ili_total
    target_weeks = calendar.window_after(origin_week.yearweek, FORECAST_HORIZONS)
    predictions = [x8 * weekly[w.yearweek].ili_total / y8 for w in selected.future_weeks]
    require(all(math.isfinite(v) for v in predictions),
            "Non-finite prediction from the selected segment")
    origin_position = calendar.position(origin_week.yearweek)
    first_history = max(0, origin_position - history_plot_weeks + 1)
    history_weeks = calendar.slice(first_history, origin_position)
    return AnalogResult(origin_week, tuple(current_weeks), tuple(current_values), x8,
                        tuple(target_weeks), tuple(candidates), selected, tuple(predictions),
                        tuple(history_weeks))
