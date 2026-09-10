"""The single week authority: DIM_DATA.public.dim_weekdate.

Every week number, week order, week boundary and in-week date set in this project
comes from this module. Nothing here derives a week from an ISO week, a fixed
weekday, a pandas resample, origin + h * 7, a fixed seven-day slice, a fixed 56-day
horizon or yearweek + 1.
"""
from __future__ import annotations

from collections import namedtuple
from datetime import timedelta

from .util import as_date, require

DimWeek = namedtuple("DimWeek", "yearweek start end days")

CALENDAR_RULES = (
    "week number, week order, week boundaries and in-week date sets come only from "
    "DIM_DATA.public.dim_weekdate; never ISO week, pandas resample, a fixed weekday, "
    "origin + h*7, a fixed 7-day slice, a fixed 56-day horizon or yearweek + 1"
)


def year_of(yearweek):
    """Calendar year of a DIM week, taken from the DIM yearweek itself."""
    return int(yearweek) // 100


def week_number_of(yearweek):
    return int(yearweek) % 100


class DimCalendar:
    """Validated, contiguous list of DIM weeks ordered by week start date."""

    def __init__(self, weeks):
        self.weeks = tuple(weeks)
        self._position = {w.yearweek: i for i, w in enumerate(self.weeks)}
        require(len(self._position) == len(self.weeks), "Duplicate DIM yearweek")

    def __len__(self):
        return len(self.weeks)

    def position(self, yearweek):
        require(yearweek in self._position,
                f"Yearweek {yearweek} is absent from DIM_DATA.public.dim_weekdate")
        return self._position[yearweek]

    def has(self, yearweek):
        return yearweek in self._position

    def week(self, yearweek):
        return self.weeks[self.position(yearweek)]

    def at(self, index):
        return self.weeks[index]

    def slice(self, first_index, last_index):
        """Inclusive positional slice; the caller has already bounds-checked it."""
        require(0 <= first_index <= last_index < len(self.weeks), "DIM slice out of range")
        return self.weeks[first_index:last_index + 1]

    def window_ending_at(self, yearweek, length):
        """The `length` consecutive DIM weeks ending at `yearweek` (inclusive)."""
        require(length >= 1, "Window length must be positive")
        end = self.position(yearweek)
        start = end - length + 1
        require(start >= 0,
                f"DIM calendar holds fewer than {length} weeks before and including {yearweek}")
        return self.weeks[start:end + 1]

    def window_after(self, yearweek, length):
        """The `length` consecutive DIM weeks following `yearweek`."""
        require(length >= 1, "Window length must be positive")
        start = self.position(yearweek) + 1
        require(start + length <= len(self.weeks),
                f"DIM calendar does not cover {length} weeks after {yearweek}; "
                "the horizon cannot be resolved")
        return self.weeks[start:start + length]

    def days_span(self, weeks):
        """Actual dates covered by consecutive DIM weeks, from their own date sets."""
        require(bool(weeks), "No DIM weeks given")
        days = [d for w in weeks for d in w.days]
        require(len(days) == len(set(days)), "Overlapping DIM weeks")
        return days


def build_calendar(rows):
    """Validate raw (date, yearweek) rows into a DimCalendar.

    Each date maps to exactly one week; each week's dates are contiguous; the weeks
    themselves tile the covered range without gap or overlap.
    """
    by_week, seen = {}, set()
    for raw_day, raw_week in rows:
        day = as_date(raw_day)
        require(day not in seen, f"Duplicate DIM date: {day}")
        seen.add(day)
        require(raw_week is not None, f"NULL DIM yearweek at {day}")
        week = int(raw_week)
        require(str(week) == str(raw_week).strip(), f"Invalid DIM yearweek: {raw_week!r}")
        require(190001 <= week <= 999953, f"DIM yearweek out of range: {week}")
        require(1 <= week_number_of(week) <= 53, f"DIM week number out of range: {week}")
        by_week.setdefault(week, []).append(day)
    require(bool(by_week), "Empty DIM_DATA.public.dim_weekdate")
    weeks = []
    for yearweek, days in by_week.items():
        days.sort()
        require(1 <= len(days) <= 7, f"Invalid DIM day_count for {yearweek}: {len(days)}")
        require((days[-1] - days[0]).days + 1 == len(days),
                f"Non-contiguous DIM week: {yearweek}")
        weeks.append(DimWeek(yearweek, days[0], days[-1], tuple(days)))
    weeks.sort(key=lambda w: w.start)
    require(all(b.start == a.end + timedelta(days=1) for a, b in zip(weeks, weeks[1:])),
            "DIM calendar has gaps or overlapping weeks")
    require(all(b.yearweek > a.yearweek for a, b in zip(weeks, weeks[1:])),
            "DIM yearweek values are not increasing with week start date")
    return DimCalendar(weeks)


def calendar_digest_payload(calendar, first_day, last_day):
    """Canonical text of the DIM weeks actually used, for the run digest."""
    return "\n".join(f"{w.yearweek},{w.start},{w.end},{len(w.days)}"
                     for w in calendar.weeks if w.start >= first_day and w.end <= last_day)
