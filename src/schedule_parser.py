"""Computes the next expected fire time from a real AWS EventBridge
`ScheduleExpression` string (`rate(...)` or `cron(...)`) - never guessed,
never registry-driven. Deliberately independent of freshness.py's Fresh/
Delayed/Stale classification (which stays untouched by this module): this
only answers "when will this next fire," not "is the pipeline healthy."

Conservative by the same rule as the rest of this project: an expression
this module doesn't fully understand (an unsupported cron special character,
a malformed rate()) returns (None, reason) - never a best-effort guess.
"""
from __future__ import annotations

import re
from datetime import datetime, timedelta
from typing import FrozenSet, Optional, Tuple, Union

# Safety bound against a schedule that (almost) never matches (e.g. a cron
# expression describing Feb 30) - not a realistic gap for any real AWS
# schedule: daily/hourly/weekly/monthly schedules all resolve within days,
# and even a genuine once-a-year schedule needs at most ~366 days of
# search. ~416 days of minute-by-minute search - comfortably covers a full
# year with margin, while still bounding a pathological expression.
_MAX_CRON_SEARCH_MINUTES = 600_000

_RATE_UNIT_MINUTES = {
    "minute": 1, "minutes": 1,
    "hour": 60, "hours": 60,
    "day": 60 * 24, "days": 60 * 24,
}
_RATE_PATTERN = re.compile(r"^rate\(\s*(\d+)\s+(minute|minutes|hour|hours|day|days)\s*\)$", re.IGNORECASE)
_CRON_PATTERN = re.compile(r"^cron\((.+)\)$", re.IGNORECASE)

_MONTH_NAMES = {name: i + 1 for i, name in enumerate(
    ["JAN", "FEB", "MAR", "APR", "MAY", "JUN", "JUL", "AUG", "SEP", "OCT", "NOV", "DEC"]
)}
# AWS's own convention (not standard Unix cron): 1 = Sunday, not Monday.
_DOW_NAMES = {name: i + 1 for i, name in enumerate(["SUN", "MON", "TUE", "WED", "THU", "FRI", "SAT"])}

# (min, max, name-alias map or None) per field, in AWS's 6-field order.
_CRON_FIELD_BOUNDS = [
    (0, 59, None),          # minutes
    (0, 23, None),          # hours
    (1, 31, None),          # day-of-month
    (1, 12, _MONTH_NAMES),  # month
    (1, 7, _DOW_NAMES),     # day-of-week
    (1970, 2199, None),     # year
]

_WILDCARD = "*"  # sentinel: this field matches any value


class _UnsupportedCronExpression(Exception):
    """Raised internally when a cron field uses syntax this parser doesn't
    handle (L/W/#, or anything else non-numeric/non-listed) - caught at the
    top level and turned into an honest (None, reason), never a guess.
    """


def _parse_cron_field(raw: str, lo: int, hi: int, name_map: Optional[dict]) -> Union[str, FrozenSet[int]]:
    if raw in ("*", "?"):
        return _WILDCARD

    values = set()
    for token in raw.split(","):
        token = token.strip().upper()
        step = 1
        if "/" in token:
            token, step_str = token.split("/", 1)
            if not step_str.isdigit():
                raise _UnsupportedCronExpression(f"unsupported step value: {step_str!r}")
            step = int(step_str)

        if name_map:
            token = "-".join(str(name_map[part]) if part in name_map else part for part in token.split("-"))

        if token == "*":
            range_lo, range_hi = lo, hi
        elif "-" in token:
            parts = token.split("-")
            if len(parts) != 2 or not all(p.lstrip("-").isdigit() for p in parts):
                raise _UnsupportedCronExpression(f"unsupported range: {token!r}")
            range_lo, range_hi = int(parts[0]), int(parts[1])
        elif token.isdigit():
            range_lo = range_hi = int(token)
        else:
            # L, W, #, or anything else this parser doesn't model - honest
            # "not supported," never a guess at what it might mean.
            raise _UnsupportedCronExpression(f"unsupported field syntax: {token!r}")

        values.update(range(range_lo, range_hi + 1, step))

    if not values or min(values) < lo or max(values) > hi:
        raise _UnsupportedCronExpression(f"value out of range in {raw!r}")
    return frozenset(values)


def parse_rate_expression(expr: str) -> Optional[timedelta]:
    """rate(N unit) -> timedelta, or None if the expression doesn't match
    AWS's rate() syntax. N=0 is invalid per AWS and also rejected here.
    """
    m = _RATE_PATTERN.match(expr.strip())
    if not m:
        return None
    value = int(m.group(1))
    if value <= 0:
        return None
    unit_minutes = _RATE_UNIT_MINUTES[m.group(2).lower()]
    return timedelta(minutes=value * unit_minutes)


def _aws_day_of_week(dt: datetime) -> int:
    # Python's weekday(): Monday=0 ... Sunday=6. AWS: Sunday=1 ... Saturday=7.
    return ((dt.weekday() + 1) % 7) + 1


def parse_cron_expression(expr: str) -> Tuple[Optional[list], Optional[str]]:
    """Parses an AWS 6-field cron() expression into per-field match sets.
    Returns (fields, None) on success, or (None, reason) if any field uses
    syntax this parser doesn't model - never a guess.
    """
    m = _CRON_PATTERN.match(expr.strip())
    if not m:
        return None, f"{expr!r} is not a recognizable cron() expression"

    raw_fields = m.group(1).split()
    if len(raw_fields) != 6:
        return None, f"expected 6 fields (minute hour day-of-month month day-of-week year), got {len(raw_fields)}"

    parsed = []
    for raw, (lo, hi, name_map) in zip(raw_fields, _CRON_FIELD_BOUNDS):
        try:
            parsed.append(_parse_cron_field(raw, lo, hi, name_map))
        except _UnsupportedCronExpression as exc:
            return None, f"cron field {raw!r} is not supported for next-run calculation: {exc}"

    return parsed, None


def _cron_matches(dt: datetime, fields: list) -> bool:
    minute_f, hour_f, dom_f, month_f, dow_f, year_f = fields
    if minute_f != _WILDCARD and dt.minute not in minute_f:
        return False
    if hour_f != _WILDCARD and dt.hour not in hour_f:
        return False
    if month_f != _WILDCARD and dt.month not in month_f:
        return False
    if year_f != _WILDCARD and dt.year not in year_f:
        return False
    # AWS requires exactly one of day-of-month/day-of-week to be a
    # wildcard ("?"); this AND-based check is correct for every valid AWS
    # cron expression (at most one side is ever actually restrictive).
    if dom_f != _WILDCARD and dt.day not in dom_f:
        return False
    if dow_f != _WILDCARD and _aws_day_of_week(dt) not in dow_f:
        return False
    return True


def compute_next_run(schedule_expression: Optional[str], after: datetime) -> Tuple[Optional[datetime], Optional[str]]:
    """Given a real AWS ScheduleExpression and a reference time, returns
    (next_run, None) on success or (None, reason) when it can't be
    determined - never a guess. `after` is exclusive: the result is always
    strictly later than `after`.
    """
    if not schedule_expression:
        return None, "No trigger was identified for this pipeline, so no schedule is known."

    expr = schedule_expression.strip()

    rate_delta = parse_rate_expression(expr)
    if rate_delta is not None:
        return after + rate_delta, None

    if expr.lower().startswith("cron("):
        fields, reason = parse_cron_expression(expr)
        if fields is None:
            return None, reason
        candidate = after.replace(second=0, microsecond=0) + timedelta(minutes=1)
        for _ in range(_MAX_CRON_SEARCH_MINUTES):
            if _cron_matches(candidate, fields):
                return candidate, None
            candidate += timedelta(minutes=1)
        return None, f"cron expression {expr!r} did not match any time within a reasonable search window"

    return None, f"{expr!r} is not a recognized rate() or cron() schedule expression"
