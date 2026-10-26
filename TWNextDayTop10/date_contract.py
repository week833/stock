"""Current report calendar-day adapters; instants and sealed bytes remain separate.

ISO inputs remain accepted; report projections use date8 for display.
New report projections use date8 without mutating their source metadata.
"""
from datetime import date, datetime
import re

DATE_ENCODING = "YYYYMMDD"
_ISO = re.compile(r"^[0-9]{4}-[0-9]{2}-[0-9]{2}$")
_DATE8 = re.compile(r"^[0-9]{8}$")


def parse_day(value):
    """Accept only a real pure ISO/date8 day (or a date, never a datetime)."""
    if isinstance(value, datetime):
        raise ValueError("timestamp_is_not_a_calendar_day")
    if isinstance(value, date):
        return value
    if not isinstance(value, str):
        raise ValueError("calendar_day_must_be_date_or_string")
    text = value.strip()
    if _DATE8.fullmatch(text):
        text = text[:4] + "-" + text[4:6] + "-" + text[6:]
    elif not _ISO.fullmatch(text):
        raise ValueError("calendar_day_must_be_YYYYMMDD_or_ISO")
    return date.fromisoformat(text)


def iso_day(value):
    return parse_day(value).isoformat()


def date8(value):
    day = parse_day(value)
    return f"{day.year:04d}{day.month:02d}{day.day:02d}"


_DAY_FIELDS = {
    "date", "Date", "asof", "observed_asof", "data_asof", "analysis_asof", "dates", "origin_start",
    "source_max", "source_min", "raw_max", "raw_min", "through_date", "latest_date",
    "holdout_start", "holdout_end", "missing_sessions", "evaluation_dates",
}
_DAY_MAP_FIELDS = {"trusted_next_sessions"}


def _day_field(key):
    return key in _DAY_FIELDS or key.endswith(("_date", "_dates", "_session", "_sessions", "_start", "_end"))


def report_dates(value, key=""):
    """Copy only typed day fields for a new report; never touch hashes/IDs/instants.

Pure-day checking deliberately leaves period labels and aware timestamps intact,
including timestamp-valued fields whose name also ends in ``_start``/``_end``.
"""
    if isinstance(value, dict):
        if key in _DAY_MAP_FIELDS:
            return {date8(k): date8(v) for k, v in value.items()}
        return {k: report_dates(v, str(k)) for k, v in value.items()}
    if isinstance(value, (list, tuple)):
        return [report_dates(item, key) for item in value]
    if isinstance(value, datetime):
        return value.isoformat()
    if _day_field(key) or isinstance(value, date):
        try:
            return date8(value)
        except (TypeError, ValueError):
            pass
    return value
