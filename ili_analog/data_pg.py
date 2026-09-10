"""PostgreSQL reads for the national ILI target, using the existing eic_utils contract.

No connection is opened on import, and no password is ever written here: credentials and
the connection lifecycle stay owned by the installed eic_utils, reached through
conn.deco.postgres(dbname=...). Query values use the existing "?" placeholder contract.
"""
from __future__ import annotations

import math
from collections import namedtuple

from .dim_calendar import build_calendar
from .util import as_date, is_observed, normalize_county, require

DATABASE = "postgres"
CALENDAR_DATABASE = "DIM_DATA"
CALENDAR_TABLE = "public.dim_weekdate"
CONNECTION_API = "eic_utils.conn.deco.postgres"

SOURCES = ("nhi_opd", "rods")
TABLES = {
    "nhi_opd": "disease_forecast_data.model_nhi_opd_daily_county",
    "rods": "disease_forecast_data.model_rods_daily_county",
}
VALUE_COLUMN = "ili"
COVERAGE_COLUMN = "coverage_observed"

# Explicit 22-county allowlist. Rows outside it are rejected, never folded into a total.
COUNTIES = ("臺北市", "新北市", "桃園市", "臺中市", "臺南市", "高雄市",
            "基隆市", "新竹市", "嘉義市", "新竹縣", "苗栗縣", "彰化縣",
            "南投縣", "雲林縣", "嘉義縣", "屏東縣", "宜蘭縣", "花蓮縣",
            "臺東縣", "澎湖縣", "金門縣", "連江縣")
COUNTY_ALIAS_VERSION = "tw22-tai-alias-v1"

TARGET_DEFINITION = (
    "national ILI = nhi_opd_total + rods_total, each source first summed over the 22 "
    "counties and over the DIM week's own dates; this is the combined visit count of two "
    "surveillance sources, not a de-duplicated infected population"
)
SCI_POLICY = ("severe influenza (SCI) is not part of the target and is not a v1 input feature")
COMPLETENESS_POLICY = (
    "a source-week is complete only when every DIM date of that week x all 22 counties has a "
    "valid row with coverage_observed true; missing rows, NULLs and uncovered cells are never "
    "read as 0; a legitimate observed 0 is kept"
)

WeeklyRow = namedtuple(
    "WeeklyRow",
    "yearweek start end day_count nhi_opd rods ili_total missing_nhi_opd missing_rods status")


def read_calendar():
    from eic_utils import conn

    @conn.deco.postgres(dbname=CALENDAR_DATABASE)
    def query(cur=None):
        cur.execute(f"SELECT date, yearweek FROM {CALENDAR_TABLE} "
                    "WHERE yearweek IS NOT NULL ORDER BY date")
        return cur.fetchall()

    return build_calendar(query())


def read_panel(first_day, last_day):
    """Daily county panel per source over an inclusive date range.

    A cell is float NaN when the row is absent, the value is NULL, or coverage_observed is
    not affirmative. Structurally invalid data (unknown county, duplicate key after alias
    normalisation, negative, infinite or unparsable value, out-of-range date) is rejected.
    """
    require(first_day <= last_day, "Empty date range requested")
    from eic_utils import conn

    panel, rows_read = {}, {}
    for source in SOURCES:
        table = TABLES[source]

        @conn.deco.postgres(dbname=DATABASE)
        def query(cur=None, _table=table):
            # The identifier comes only from TABLES; every value uses the existing "?" contract.
            cur.execute(f"SELECT date, county, {VALUE_COLUMN}, {COVERAGE_COLUMN} FROM {_table} "
                        "WHERE date >= ?::date AND date <= ?::date ORDER BY date, county",
                        first_day.isoformat(), last_day.isoformat())
            return cur.fetchall()

        raw = query()
        rows_read[source] = len(raw)
        for raw_day, raw_county, raw_value, raw_coverage in raw:
            day = as_date(raw_day)
            require(first_day <= day <= last_day,
                    f"{source} returned {day} outside the requested range")
            county = normalize_county(raw_county)
            require(county in COUNTIES, f"Unknown county in {source}: {raw_county!r}")
            key = (source, county, day)
            require(key not in panel,
                    f"Duplicate or alias-colliding key in {source}: {county} {day}")
            if raw_value is None:
                panel[key] = math.nan
                continue
            value = float(raw_value)
            require(math.isfinite(value), f"Non-finite {VALUE_COLUMN} in {source}: {county} {day}")
            require(0 <= value < 1e20, f"Invalid {VALUE_COLUMN} in {source}: {county} {day} = {value}")
            panel[key] = value if is_observed(raw_coverage) else math.nan
    return panel, rows_read


def weekly_source_total(panel, source, week):
    """(total, missing_cells) for one source over one DIM week's own dates x 22 counties."""
    values, missing = [], 0
    for day in week.days:
        for county in COUNTIES:
            value = panel.get((source, county, day), math.nan)
            if isinstance(value, float) and not math.isfinite(value):
                missing += 1
            else:
                values.append(value)
    if missing:
        return math.nan, missing
    return math.fsum(values), 0


def weekly_rows(panel, weeks):
    """Weekly national totals per DIM week. One incomplete source makes the week missing."""
    rows = {}
    for week in weeks:
        nhi, missing_nhi = weekly_source_total(panel, "nhi_opd", week)
        rods, missing_rods = weekly_source_total(panel, "rods", week)
        complete = missing_nhi == 0 and missing_rods == 0
        total = nhi + rods if complete else math.nan
        rows[week.yearweek] = WeeklyRow(
            week.yearweek, week.start, week.end, len(week.days), nhi, rods, total,
            missing_nhi, missing_rods, "complete" if complete else "incomplete")
    return rows


def expected_cells(week):
    return len(week.days) * len(COUNTIES)


def panel_metadata(first_day, last_day, rows_read):
    return {
        "database": DATABASE,
        "connection_api": CONNECTION_API,
        "credentials": "owned by installed eic_utils; never written in this repository",
        "placeholder_style": "? (existing eic_utils query contract; ?::date for date bounds)",
        "tables": dict(TABLES),
        "value_column": VALUE_COLUMN,
        "coverage_column": COVERAGE_COLUMN,
        "calendar_database": CALENDAR_DATABASE,
        "calendar_table": CALENDAR_TABLE,
        "counties": list(COUNTIES),
        "county_alias_version": COUNTY_ALIAS_VERSION,
        "target_definition": TARGET_DEFINITION,
        "sci_policy": SCI_POLICY,
        "completeness_policy": COMPLETENESS_POLICY,
        "read_first_day": first_day.isoformat(),
        "read_last_day": last_day.isoformat(),
        "rows_read": rows_read,
        "snapshot_semantics": "current database snapshot; not historical as-of replay",
    }
