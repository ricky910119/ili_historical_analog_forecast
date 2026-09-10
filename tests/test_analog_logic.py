"""Pure-logic tests. No database, no network, no model. Run them on the server.

    cd /path/to/ili_historical_analog_forecast && python -m pytest tests -q
"""
from __future__ import annotations

import json
import math
from datetime import date, timedelta
from pathlib import Path

import pytest

from ili_analog.analog import (LOOKBACK_WEEKS, build_current_window, check_supported_years,
                               normalised, run_analog, screen_candidates, score_candidates,
                               segment_distance)
from ili_analog.data_pg import WeeklyRow, weekly_rows
from ili_analog.dim_calendar import build_calendar, year_of
from ili_analog.evaluate import metrics, persistence_baseline
from ili_analog.holidays import SpringFestivalCalendar
from ili_analog.util import Ineligible

FIRST_DAY = date(2023, 12, 31)  # a Sunday: the synthetic calendar starts a week here


def make_calendar(weeks=160, day_count=7):
    rows, day = [], FIRST_DAY
    for index in range(weeks):
        year = 2024 + (index // 52)
        yearweek = year * 100 + (index % 52) + 1
        for _ in range(day_count):
            rows.append((day, yearweek))
            day += timedelta(days=1)
    return build_calendar(rows)


def make_spring(first="2025-01-25", last="2025-02-02", second=("2026-02-14", "2026-02-22")):
    payload = {"config_version": "test", "confirmed_by_user": True, "holidays": [
        {"year": 2025, "event": "LNY 2025", "first_day": first, "last_day": last,
         "source": "test fixture"},
        {"year": 2026, "event": "LNY 2026", "first_day": second[0], "last_day": second[1],
         "source": "test fixture"}]}
    return SpringFestivalCalendar(Path("test-fixture.json"), payload)


def make_weekly(calendar, values, incomplete=()):
    """values: {yearweek: ili_total}; anything missing is marked incomplete."""
    rows = {}
    for week in calendar.weeks:
        total = values.get(week.yearweek)
        complete = total is not None and week.yearweek not in incomplete
        rows[week.yearweek] = WeeklyRow(
            week.yearweek, week.start, week.end, len(week.days),
            total / 2 if complete else math.nan, total / 2 if complete else math.nan,
            total if complete else math.nan, 0 if complete else 1, 0,
            "complete" if complete else "incomplete")
    return rows


def full_values(calendar, level=1000.0):
    return {w.yearweek: level + (w.yearweek % 100) for w in calendar.weeks}


def test_calendar_rejects_duplicate_date():
    with pytest.raises(ValueError):
        build_calendar([(date(2025, 1, 1), 202501), (date(2025, 1, 1), 202502)])


def test_calendar_rejects_gap():
    rows = [(date(2025, 1, 5) + timedelta(days=i), 202502) for i in range(7)]
    rows += [(date(2025, 1, 20) + timedelta(days=i), 202504) for i in range(7)]
    with pytest.raises(ValueError):
        build_calendar(rows)


def test_calendar_accepts_short_week():
    """DIM day counts come from the actual date set, never from a fixed seven."""
    rows = [(date(2025, 1, 5) + timedelta(days=i), 202502) for i in range(7)]
    rows += [(date(2025, 1, 12) + timedelta(days=i), 202503) for i in range(4)]
    calendar = build_calendar(rows)
    assert [len(w.days) for w in calendar.weeks] == [7, 4]


def test_year_of_reads_the_dim_yearweek():
    assert year_of(202601) == 2026 and year_of(202552) == 2025


def test_normalisation_is_ratio_to_last_week():
    assert normalised([2.0, 4.0, 8.0])[-1] == 1.0
    assert normalised([2.0, 4.0, 8.0])[0] == 0.25


def test_distance_is_mean_absolute_difference():
    a = [1.0] * LOOKBACK_WEEKS
    b = [1.0] * (LOOKBACK_WEEKS - 1) + [2.0]
    assert segment_distance(a, b) == pytest.approx(1.0 / LOOKBACK_WEEKS)


def test_scaling_is_exact_and_identical_shape_wins():
    calendar = make_calendar()
    spring = make_spring()
    values = full_values(calendar)
    # Make the 2026 window an exact multiple of one 2025 segment so the match is known.
    reference_end = 202530
    reference = calendar.week(reference_end)
    position = calendar.position(reference_end)
    segment = calendar.slice(position - LOOKBACK_WEEKS + 1, position)
    origin = calendar.at(calendar.position(202620))
    current = calendar.window_ending_at(origin.yearweek, LOOKBACK_WEEKS)
    for source, target in zip(segment, current):
        values[target.yearweek] = values[source.yearweek] * 3.0
    weekly = make_weekly(calendar, values)
    result = run_analog(calendar, weekly, spring, origin, 2025, 2026)
    assert result.selected.compare_weeks[-1].yearweek == reference_end
    assert result.selected.distance == pytest.approx(0.0, abs=1e-12)
    future = calendar.slice(position + 1, position + 8)
    expected = [values[w.yearweek] * 3.0 for w in future]
    assert list(result.predictions) == pytest.approx(expected)
    assert [w.yearweek for w in result.target_weeks] == \
        [w.yearweek for w in calendar.window_after(origin.yearweek, 8)]
    assert reference.end < origin.start  # the analog is strictly historical


def test_current_window_must_be_inside_the_target_year():
    calendar = make_calendar()
    weekly = make_weekly(calendar, full_values(calendar))
    with pytest.raises(Ineligible, match="current_window_not_in_target_year"):
        build_current_window(calendar, weekly, make_spring(), calendar.week(202605), 2026)


def test_incomplete_current_week_is_ineligible_not_skipped():
    calendar = make_calendar()
    weekly = make_weekly(calendar, full_values(calendar), incomplete={202615})
    with pytest.raises(Ineligible, match="current_window_incomplete"):
        build_current_window(calendar, weekly, make_spring(), calendar.week(202620), 2026)


def test_spring_festival_origin_is_ineligible():
    calendar = make_calendar()
    weekly = make_weekly(calendar, full_values(calendar))
    origin = next(w for w in calendar.weeks
                  if w.start <= date(2026, 2, 17) <= w.end)
    with pytest.raises(Ineligible, match="current_denominator_spring_festival_week"):
        build_current_window(calendar, weekly, make_spring(), origin, 2026)


def test_zero_origin_denominator_is_ineligible():
    calendar = make_calendar()
    values = full_values(calendar)
    values[202620] = 0.0
    weekly = make_weekly(calendar, values)
    with pytest.raises(Ineligible, match="current_denominator_not_positive"):
        build_current_window(calendar, weekly, make_spring(), calendar.week(202620), 2026)


def test_candidates_outside_the_reference_year_are_excluded_with_a_reason():
    calendar = make_calendar()
    weekly = make_weekly(calendar, full_values(calendar))
    screened = screen_candidates(calendar, weekly, make_spring(), 2025)
    early = next(c for c in screened if c.end_position == calendar.position(202503))
    late = next(c for c in screened if c.end_position == calendar.position(202550))
    assert "compare_window_not_in_reference_year" in early.reasons
    assert "future_window_not_in_reference_year" in late.reasons
    assert all(year_of(c.compare_weeks[-1].yearweek) == 2025 for c in screened if c.eligible)


def test_spring_festival_excludes_only_the_candidate_denominator_week():
    calendar = make_calendar()
    weekly = make_weekly(calendar, full_values(calendar))
    screened = screen_candidates(calendar, weekly, make_spring(), 2025)
    excluded = [c for c in screened if "spring_festival_denominator_week" in c.reasons]
    assert excluded, "the 2025 holiday should exclude at least one denominator week"
    for candidate in excluded:
        assert make_spring().overlap(candidate.compare_weeks[-1]) is not None
    # Weeks that merely contain the holiday elsewhere in a segment stay eligible.
    assert any(c.eligible and any(make_spring().overlap(w) for w in c.compare_weeks[:-1])
               for c in screened)


def test_tie_is_broken_toward_the_earlier_end_week():
    calendar = make_calendar()
    values = {w.yearweek: 100.0 for w in calendar.weeks}
    weekly = make_weekly(calendar, values)
    screened = screen_candidates(calendar, weekly, make_spring(), 2025)
    scored, selected = score_candidates(screened, weekly, [1.0] * LOOKBACK_WEEKS)
    eligible = [c for c in scored if c.eligible]
    assert len({c.distance for c in eligible}) == 1  # a genuine tie across the pool
    assert selected.compare_weeks[-1].start == min(c.compare_weeks[-1].start for c in eligible)
    assert selected.rank == 1


def test_no_eligible_candidate_raises_ineligible():
    calendar = make_calendar()
    values = full_values(calendar)
    weekly = make_weekly(calendar, values, incomplete={w.yearweek for w in calendar.weeks
                                                       if year_of(w.yearweek) == 2025})
    screened = screen_candidates(calendar, weekly, make_spring(), 2025)
    with pytest.raises(Ineligible, match="no_eligible_candidate"):
        score_candidates(screened, weekly, [1.0] * LOOKBACK_WEEKS)


def test_weekly_rows_never_zero_fill_a_missing_cell():
    calendar = make_calendar(weeks=2)
    week = calendar.at(0)
    panel = {}
    from ili_analog.data_pg import COUNTIES, SOURCES
    for source in SOURCES:
        for county in COUNTIES:
            for day in week.days:
                panel[(source, county, day)] = 1.0
    assert weekly_rows(panel, [week])[week.yearweek].ili_total == pytest.approx(
        2 * len(COUNTIES) * len(week.days))
    del panel[("rods", COUNTIES[0], week.days[0])]
    row = weekly_rows(panel, [week])[week.yearweek]
    assert row.status == "incomplete" and math.isnan(row.ili_total) and row.missing_rods == 1


def test_observed_zero_is_kept():
    calendar = make_calendar(weeks=2)
    week = calendar.at(0)
    from ili_analog.data_pg import COUNTIES, SOURCES
    panel = {(s, c, d): 0.0 for s in SOURCES for c in COUNTIES for d in week.days}
    row = weekly_rows(panel, [week])[week.yearweek]
    assert row.status == "complete" and row.ili_total == 0.0


def test_metrics_and_zero_denominator():
    assert metrics([(2.0, 1.0), (0.0, 1.0)])["mae"] == pytest.approx(1.0)
    assert metrics([(2.0, 1.0), (0.0, 1.0)])["wape"] == pytest.approx(1.0)
    assert metrics([(2.0, 1.0), (0.0, 1.0)])["signed_bias_ratio"] == pytest.approx(0.0)
    zero = metrics([(3.0, 0.0)])
    assert zero["wape"] is None and zero["signed_bias_ratio"] is None
    pending = metrics([(3.0, math.nan)])
    assert pending["scorable"] == 0 and pending["mae"] is None and pending["pending"] == 1


def test_persistence_baseline_holds_the_origin_week():
    assert persistence_baseline(12.5, 8) == [12.5] * 8


def test_other_year_pairs_are_refused():
    with pytest.raises(ValueError, match="supports only reference-year"):
        check_supported_years(2024, 2025)
    check_supported_years(2025, 2026)


def test_spring_festival_config_shipped_in_the_repo_is_parsable():
    path = Path(__file__).resolve().parent.parent / "configs" / "spring_festival.json"
    payload = json.loads(path.read_text(encoding="utf-8"))
    calendar = SpringFestivalCalendar(path, payload)
    assert calendar.covers_year(2025) and calendar.covers_year(2026)
    assert all(entry["source"] for entry in calendar.entries)
