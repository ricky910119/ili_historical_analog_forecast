"""Small shared helpers. Nothing here opens a database connection."""
from __future__ import annotations

import csv
import hashlib
import json
import math
import subprocess
from datetime import date, datetime


def require(condition, message):
    if not condition:
        raise ValueError(message)


class Ineligible(Exception):
    """The run cannot produce a forecast under the fixed v1 rules.

    Raised instead of falling back to a default forecast, an earlier origin or a
    partially complete window.
    """


def normalize_county(value):
    """The existing county rule shared with the other ILI research projects."""
    return str(value).strip().replace("台", "臺")


def as_date(value):
    if isinstance(value, datetime):
        require(value.time().isoformat() == "00:00:00", "Non-midnight date in database")
        return value.date()
    if isinstance(value, date):
        return value
    return date.fromisoformat(str(value))


def is_observed(value):
    """coverage_observed is affirmative only; anything else is an unobserved cell."""
    return str(value).strip().lower() in {"true", "t", "1", "1.0", "yes", "y"}


def sha256_text(text):
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def digest_file(path):
    hasher = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            hasher.update(chunk)
    return hasher.hexdigest()


def save_json(path, value):
    # allow_nan=False: a NaN must be written as an explicit null/pending marker,
    # never smuggled into JSON as a bare NaN token.
    path.write_text(json.dumps(value, indent=2, ensure_ascii=False, allow_nan=False),
                    encoding="utf-8")


def jsonable(value):
    """NaN becomes None so run.json never carries a fabricated number."""
    if isinstance(value, float) and not math.isfinite(value):
        return None
    if isinstance(value, dict):
        return {k: jsonable(v) for k, v in value.items()}
    if isinstance(value, (list, tuple)):
        return [jsonable(v) for v in value]
    if isinstance(value, (date, datetime)):
        return value.isoformat()
    return value


def csv_cell(value):
    """Full float precision on disk. Rounding happens only in the plots."""
    if isinstance(value, float):
        return "" if not math.isfinite(value) else repr(value)
    if isinstance(value, (date, datetime)):
        return value.isoformat()
    return value


def write_csv(path, header, rows):
    with path.open("w", encoding="utf-8", newline="") as stream:
        writer = csv.writer(stream)
        writer.writerow(header)
        for row in rows:
            writer.writerow([csv_cell(v) for v in row])


def command(args, cwd=None):
    result = subprocess.run(args, cwd=cwd, capture_output=True, text=True, timeout=30)
    if result.returncode != 0:
        return None
    return result.stdout.strip()
