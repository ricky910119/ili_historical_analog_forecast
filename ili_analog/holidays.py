"""Spring Festival policy: denominator-week exclusion only.

The configured holiday ranges are event dates with a cited source. They are mapped to DIM
weeks through the DIM calendar's own date sets - never through an ISO week or a fixed
weekday. The only effect in v1 is on the ratio denominator week:

  * current year: the origin week (x[8]) overlaps a holiday -> the run is INELIGIBLE;
  * reference year: a candidate's last compare week (y[8]) overlaps -> that candidate is
    excluded and the reason is recorded.

No other week's actual value is modified, interpolated, shifted or smoothed, and no
data-derived "anomalous week" threshold is added.
"""
from __future__ import annotations

import json
from datetime import date

from .util import require, sha256_text

POLICY = ("Spring Festival affects only the ratio denominator week: an overlapping current-year "
          "origin week makes the run INELIGIBLE and an overlapping reference-year candidate "
          "denominator week excludes that candidate. No actual value is edited, interpolated, "
          "shifted or reshaped, and no data-derived anomalous-week threshold is used.")
CONFIRMATION_NOTE = ("boundary rule pending explicit user confirmation; set confirmed_by_user "
                     "true in the config after checking the cited source")


class SpringFestivalCalendar:
    def __init__(self, path, payload):
        self.path = str(path)
        self.raw = payload
        self.config_version = str(payload.get("config_version", ""))
        require(bool(self.config_version), "Spring Festival config has no config_version")
        self.confirmed_by_user = bool(payload.get("confirmed_by_user", False))
        self.entries = []
        for item in payload.get("holidays", []):
            first, last = date.fromisoformat(item["first_day"]), date.fromisoformat(item["last_day"])
            require(first <= last, f"Spring Festival range is reversed: {item}")
            require(str(item.get("source", "")).strip(),
                    f"Spring Festival entry has no source: {item}")
            require(int(item["year"]) == first.year,
                    f"Spring Festival entry year does not match first_day: {item}")
            self.entries.append({"year": int(item["year"]), "event": item["event"],
                                 "first_day": first, "last_day": last,
                                 "lunar_new_year_day": item.get("lunar_new_year_day"),
                                 "source": item["source"], "source_url": item.get("source_url"),
                                 "verified_by_user": bool(item.get("verified_by_user", False))})
        require(bool(self.entries), "Spring Festival config lists no holiday ranges")
        self.digest = sha256_text(json.dumps(payload, sort_keys=True, ensure_ascii=False))

    def covers_year(self, year):
        return any(entry["year"] == year for entry in self.entries)

    def overlap(self, week):
        """The holiday entry intersecting this DIM week's own dates, or None."""
        for entry in self.entries:
            if any(entry["first_day"] <= day <= entry["last_day"] for day in week.days):
                return entry
        return None

    def metadata(self, weeks_checked=()):
        mapped = []
        for week in weeks_checked:
            entry = self.overlap(week)
            if entry:
                mapped.append({"yearweek": week.yearweek, "week_start": week.start.isoformat(),
                               "week_end": week.end.isoformat(), "event": entry["event"],
                               "holiday_first_day": entry["first_day"].isoformat(),
                               "holiday_last_day": entry["last_day"].isoformat(),
                               "source": entry["source"]})
        return {
            "config_path": self.path,
            "config_version": self.config_version,
            "config_digest": self.digest,
            "policy": POLICY,
            "confirmed_by_user": self.confirmed_by_user,
            "confirmation_note": None if self.confirmed_by_user else CONFIRMATION_NOTE,
            "entries": [{**e, "first_day": e["first_day"].isoformat(),
                         "last_day": e["last_day"].isoformat()} for e in self.entries],
            "dim_weeks_mapped_to_holidays": mapped,
        }


def load_spring_festival(path):
    payload = json.loads(path.read_text(encoding="utf-8"))
    return SpringFestivalCalendar(path, payload)
