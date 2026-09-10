"""The fixed v1 historical-analog algorithm.

Current year: x[1..8] are the eight complete DIM weeks ending at the origin.
Reference pool: y[1..8] is an eligible eight-week candidate segment drawn from the
reference-year set. A segment may cross a year boundary, and all 16 of its weeks (8 compare
+ 8 following) must end at or before the origin week.
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
SUPPORTED_REFERENCE_YEARS = (2023, 2024, 2025, 2026)
DEFAULT_REFERENCE_YEARS = (2023, 2024, 2025, 2026)
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
REFERENCE_POOL_NOTES = (
    "a candidate segment may sit anywhere inside the reference-year set and may cross a year "
    "boundary; every one of its 16 DIM weeks (8 compare + 8 following) must end at or before "
    "the origin week, so no candidate can ever read an actual the forecast itself may not see. "
    "A larger pool only widens the search: still one nearest segment, never an average"
)

Candidate = namedtuple(
    "Candidate",
    "end_position compare_weeks future_weeks eligible reasons distance rank selected")

AnalogResult = namedtuple(
    "AnalogResult",
    "origin_week current_weeks current_values x8 target_weeks candidates selected "
    "predictions history_weeks")


def check_supported_years(reference_years, target_year):
    """The reference pool must stay inside the years this method was designed for.

    v1 forecasts 2026 only. Reference years outside 2023-2026 are refused rather than
    silently accepted: 2020-2022 carry the pandemic disruption that the other ILI research
    projects already exclude, and nothing here has been validated against them.
    """
    years = tuple(sorted({int(year) for year in reference_years}))
    require(bool(years), "At least one reference year is required")
    unsupported = [year for year in years if year not in SUPPORTED_REFERENCE_YEARS]
    require(not unsupported,
            f"{ALGORITHM_VERSION} supports reference years {SUPPORTED_REFERENCE_YEARS}; got "
            f"{unsupported}. The method is not generalised to those years and this run refuses "
            "to pretend otherwise.")
    require(int(target_year) == SUPPORTED_TARGET_YEAR,
            f"{ALGORITHM_VERSION} supports only target-year {SUPPORTED_TARGET_YEAR}; got "
            f"{target_year}.")
    return years


def resolve_origin(calendar, origin_yearweek, settled_cutoff):
    """The origin must exist in DIM and end no later than the explicit settled cutoff."""
    week = calendar.week(origin_yearweek)
    require(week.end <= settled_cutoff,
            f"Origin {origin_yearweek} ends {week.end}, after the settled cutoff "
            f"{settled_cutoff}; a live forecast may not use unsettled weeks")
    return week


def candidate_end_positions(calendar, reference_years, origin_week):
    """Enumeration set: every DIM week of the reference-year set that ends at or before origin.

    Ineligible ones stay in candidate_scores.csv with their exclusion reason rather than
    being silently dropped, so the candidate denominator is auditable. A week ending after the
    origin is not enumerated at all: it is not a candidate this run is allowed to consider.
    """
    years = set(reference_years)
    return [i for i, week in enumerate(calendar.weeks)
            if year_of(week.yearweek) in years and week.end <= origin_week.end]


def plan_weeks(calendar, origin_week, reference_years, history_plot_weeks=HISTORY_PLOT_WEEKS):
    """Every DIM week whose actuals this run needs, and the date range to read.

    Only weeks inside the reference-year set that end at or before the origin are read, plus
    the recent weeks the forecast figure shows. Candidates reaching outside that set are
    excluded on the calendar alone, so their weeks are never read.
    """
    years = set(reference_years)
    origin_position = calendar.position(origin_week.yearweek)
    needed = {w.yearweek: w for w in calendar.weeks
              if year_of(w.yearweek) in years and w.end <= origin_week.end}
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


def screen_candidates(calendar, weekly, spring, reference_years, origin_week):
    """Structural eligibility of every candidate, independent of this year's window.

    Screening never looks at the current-year window, so preflight can report the candidate
    pool even when the current-year window itself is ineligible.

    A candidate segment may cross a year boundary, but all 16 of its DIM weeks must sit inside
    the reference-year set and must end at or before the origin week. That last rule is what
    keeps the target year usable as its own reference year: a segment whose following weeks
    reach past the origin would be reading an actual the forecast is not allowed to see.
    """
    years = set(reference_years)
    records = []
    for position in candidate_end_positions(calendar, reference_years, origin_week):
        reasons, compare, future = [], (), ()
        if position - LOOKBACK_WEEKS + 1 < 0:
            reasons.append("dim_calendar_missing_compare_weeks")
        else:
            compare = calendar.slice(position - LOOKBACK_WEEKS + 1, position)
        if position + FORECAST_HORIZONS >= len(calendar):
            reasons.append("dim_calendar_missing_future_weeks")
        else:
            future = calendar.slice(position + 1, position + FORECAST_HORIZONS)
        if compare and any(year_of(w.yearweek) not in years for w in compare):
            reasons.append("compare_window_outside_reference_years")
        if future and any(year_of(w.yearweek) not in years for w in future):
            reasons.append("future_window_outside_reference_years")
        if future and future[-1].end > origin_week.end:
            # The only rule that makes the target year safe to use as its own reference year.
            reasons.append("candidate_window_reaches_past_origin")
        # The Spring Festival denominator rule needs only the DIM calendar, so it is recorded
        # for every candidate that has a denominator week, even one already excluded on the
        # calendar. Weekly counts exist only for weeks this run read, so the completeness and
        # denominator checks below stay behind the calendar tier.
        if compare and spring.overlap(compare[-1]):
            reasons.append("spring_festival_denominator_week")
        if not [r for r in reasons if r != "spring_festival_denominator_week"]:
            if any(weekly[w.yearweek].status != "complete" for w in compare):
                reasons.append("incomplete_compare_week")
            if any(weekly[w.yearweek].status != "complete" for w in future):
                reasons.append("incomplete_future_week")
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


def run_analog(calendar, weekly, spring, origin_week, reference_years, target_year,
               history_plot_weeks=HISTORY_PLOT_WEEKS):
    """Full selection and scaling for one origin. Reads no actual after the origin."""
    reference_years = check_supported_years(reference_years, target_year)
    current_weeks, current_values = build_current_window(
        calendar, weekly, spring, origin_week, target_year)
    x8 = current_values[-1]
    candidates, selected = score_candidates(
        screen_candidates(calendar, weekly, spring, reference_years, origin_week), weekly,
        normalised(current_values))
    y8 = weekly[selected.compare_weeks[-1].yearweek].ili_total
    target_weeks = calendar.window_after(origin_week.yearweek, FORECAST_HORIZONS)
    predictions = [x8 * weekly[w.yearweek].ili_total / y8 for w in selected.future_weeks]
    require(all(math.isfinite(v) for v in predictions),
            "Non-finite prediction from the selected segment")
    require(selected.future_weeks[-1].end <= origin_week.end,
            "Selected segment reaches past the origin; the candidate screen is broken")
    origin_position = calendar.position(origin_week.yearweek)
    first_history = max(0, origin_position - history_plot_weeks + 1)
    history_weeks = calendar.slice(first_history, origin_position)
    return AnalogResult(origin_week, tuple(current_weeks), tuple(current_values), x8,
                        tuple(target_weeks), tuple(candidates), selected, tuple(predictions),
                        tuple(history_weeks))
