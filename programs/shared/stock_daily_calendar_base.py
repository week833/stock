"""Pure construction and validation of publisher-adopted market calendars.

ContentRef bytes, native source adoption, publication CAS and ACK are verified
by the caller. This module never reads files, obtains a clock, or fetches data.
Axis bytes use UTF-8 ``json.dumps(sessions, ensure_ascii=False) + '\r\n'``,
matching the frozen Windows source-axis bytes explicitly on every platform.
"""
from __future__ import annotations

from collections.abc import Mapping
from copy import deepcopy
import datetime as dt
import hashlib
import json
import math
import re
from urllib.parse import parse_qs, urlsplit

SCHEMA = "twstock-updater.verified-market-calendar.v1"
_SHA = re.compile(r"[0-9a-f]{64}")
_MARKETS = ("TWSE", "TPEx")
_NATIVE = {"TWSE": ("twse", "twse_daily_all"), "TPEx": ("tpex", "tpex_daily_all")}
_MONTHLY = {
    "TWSE": ("/rwd/zh/afterTrading/FMTQIK", "https://www.twse.com.tw/zh/trading/historical/fmtqik.html"),
    "TPEx": ("/www/zh-tw/indexInfo/inx", "https://www.tpex.org.tw/zh-tw/indices/stock-index/industrial/inxh.html"),
}
_MONTHLY_SEMANTICS = "complete_non_paginated_month_array_or_prefix_through_last_positive_session"


def _fail(message):
    raise ValueError("verified_market_calendar_" + message)


def _plain(value):
    if isinstance(value, Mapping):
        return {key: _plain(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [_plain(item) for item in value]
    return deepcopy(value)


def _market(value):
    if value == "TPEX":
        return "TPEx"
    if value not in _MARKETS:
        _fail("market_invalid")
    return value


def _day(value):
    if not isinstance(value, str) or not re.fullmatch(r"\d{4}-\d{2}-\d{2}", value):
        _fail("date_invalid")
    try:
        if dt.date.fromisoformat(value).isoformat() != value:
            _fail("date_invalid")
    except ValueError:
        _fail("date_invalid")
    return value


def _native_day(value):
    if isinstance(value, str) and re.fullmatch(r"\d{8}", value):
        value = value[:4] + "-" + value[4:6] + "-" + value[6:]
    return _day(value)


def _content_ref(value):
    if hasattr(value, "to_dict"):
        value = value.to_dict()
    if not isinstance(value, Mapping) or set(value) != {"ref", "sha256", "size"}:
        _fail("content_ref_invalid")
    path, digest, size = value["ref"], value["sha256"], value["size"]
    if (not isinstance(path, str) or not path or path.strip() != path
            or not path.startswith("_calendar/") or any(char in path for char in "\\:\x00")
            or any(part in {"", ".", ".."} for part in path.split("/"))
            or not isinstance(digest, str) or _SHA.fullmatch(digest) is None
            or type(size) is not int or size <= 0):
        _fail("content_ref_invalid")
    return dict(value)


def _official_url(value, market):
    if not isinstance(value, str) or any(ord(char) <= 32 for char in value):
        _fail("source_url_invalid")
    parts = urlsplit(value)
    allowed = {"www.twse.com.tw", "openapi.twse.com.tw"} if market == "TWSE" else {"www.tpex.org.tw"}
    if (parts.scheme != "https" or parts.hostname not in allowed or parts.username
            or parts.password or parts.fragment or parts.netloc != parts.hostname):
        _fail("source_url_market_invalid")
    return value


def _sessions(value):
    if not isinstance(value, list) or not value:
        _fail("sessions_invalid")
    values = [_day(day) for day in value]
    if values != sorted(set(values)):
        _fail("sessions_conflict")
    return values


def _axis_identity(sessions):
    raw = (json.dumps(sessions, ensure_ascii=False) + "\r\n").encode("utf-8")
    return hashlib.sha256(raw).hexdigest(), len(raw)


def _axis_ref(value, sessions):
    claim = _content_ref(value)
    digest, size = _axis_identity(sessions)
    if (claim["sha256"] != digest or claim["size"] != size
            or claim["ref"] != "_calendar/axes/" + digest + ".json"):
        _fail("axis_ref_mismatch")
    return claim


def _body_digest(body):
    unsigned = {key: value for key, value in body.items() if key != "proof_sha256"}
    raw = json.dumps(unsigned, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False).encode("utf-8")
    return hashlib.sha256(raw).hexdigest()


def _seal(body):
    body["proof_sha256"] = _body_digest(body)
    return body


def _sources(values):
    if not isinstance(values, list) or not values:
        _fail("sources_missing")
    result, identities = [], set()
    for value in values:
        if not isinstance(value, Mapping) or set(value) != {"market", "source_url", "raw_ref", "http_receipt_ref"}:
            _fail("source_invalid")
        market = _market(value["market"])
        item = {"market": market, "source_url": _official_url(value["source_url"], market),
                "raw_ref": _content_ref(value["raw_ref"]), "http_receipt_ref": _content_ref(value["http_receipt_ref"])}
        identity = (market, item["source_url"])
        if identity in identities:
            _fail("source_conflict")
        identities.add(identity)
        result.append(item)
    if {item["market"] for item in result} != set(_MARKETS):
        _fail("source_market_missing")
    return result


def _positive_unit(value, market):
    if not isinstance(value, Mapping):
        _fail("native_quote_invalid")
    value = dict(value)
    unit, evidence, adoption = (value.get(key) for key in ("unit", "source_evidence", "adoption"))
    if any(not isinstance(item, Mapping) for item in (unit, evidence, adoption)):
        _fail("native_quote_invalid")
    source, dataset = _NATIVE[market]
    if (unit.get("route_id") != "official_market_daily" or unit.get("kind") != "session"
            or unit.get("source") != source or unit.get("dataset") != dataset
            or _market((unit.get("selector") or {}).get("market")) != market
            or value.get("state") != "complete" or adoption.get("adopted") is not True
            or not isinstance(adoption.get("object_sha256"), str)
            or _SHA.fullmatch(adoption["object_sha256"]) is None):
        _fail("native_quote_market_or_adoption_invalid")
    day = _native_day(unit.get("key", unit.get("date")))
    if unit.get("date") is not None and _native_day(unit["date"]) != day:
        _fail("native_quote_date_conflict")
    if (type(evidence.get("http_status")) is not int or evidence["http_status"] != 200
            or type(evidence.get("row_count")) is not int or evidence["row_count"] <= 0
            or evidence.get("status") not in {"SUCCESS", "complete", "available"}
            or evidence.get("request_complete") is not True or evidence.get("source_scope_complete") is not True
            or type(evidence.get("invalid_row_count", 0)) is not int or evidence.get("invalid_row_count", 0) != 0
            or any(evidence.get(key) for key in ("error", "errors", "gaps", "not_yet_published", "unresolved_conflict", "schema_missing_fields"))
            or evidence.get("source_id") is not None and evidence["source_id"] != dataset
            or _market(evidence.get("market")) != market
            or evidence.get("date") is not None and _native_day(evidence["date"]) != day):
        _fail("native_quote_not_positive_complete")
    bounds = adoption.get("row_bounds")
    if (not isinstance(bounds, Mapping) or _native_day(bounds.get("min_date")) != day
            or _native_day(bounds.get("max_date")) != day):
        _fail("native_quote_adopted_date_scope_invalid")
    try:
        observed = dt.datetime.fromisoformat(evidence.get("observed_at", "").replace("Z", "+00:00"))
    except (ValueError, TypeError, AttributeError):
        _fail("native_quote_observation_missing")
    if observed.tzinfo is None or observed.utcoffset() is None:
        _fail("native_quote_observation_missing")
    _official_url(evidence.get("source_url", evidence.get("url")), market)
    raw_ref, http_ref = _content_ref(value.get("raw_ref")), _content_ref(value.get("http_receipt_ref"))
    hashes = [evidence[key] for key in ("source_sha256", "sha256", "raw_sha256", "source_response_sha256") if key in evidence]
    sizes = [evidence[key] for key in ("source_size", "size", "size_bytes", "raw_response_byte_size", "source_response_size", "raw_bytes") if key in evidence]
    if (not hashes or not sizes or any(item != raw_ref["sha256"] for item in hashes)
            or any(type(item) is not int or item != raw_ref["size"] for item in sizes)):
        _fail("native_quote_raw_ref_mismatch")
    # Preserve the actual native proof's necessary binding, without embedding
    # physical runtime paths or a large publication projection in this sidecar.
    fields = ("schema", "status", "http_status", "market", "request_complete", "source_scope_complete",
              "row_count", "invalid_row_count", "source_id", "date", "observed_at", "url", "source_url",
              "source_sha256", "sha256", "raw_sha256", "source_response_sha256", "source_size", "size",
              "size_bytes", "raw_response_byte_size", "source_response_size", "raw_bytes")
    compact = {"unit": _plain(unit), "state": "complete",
               "source_evidence": {key: _plain(evidence[key]) for key in fields if key in evidence},
               "adoption": {key: _plain(adoption[key]) for key in ("adopted", "object_sha256", "body_scope", "row_bounds") if key in adoption},
               "raw_ref": raw_ref, "http_receipt_ref": http_ref}
    return day, compact


def _month_bounds(value):
    if not isinstance(value, str) or re.fullmatch(r"\d{4}-\d{2}", value) is None:
        _fail("monthly_month_invalid")
    first = dt.date.fromisoformat(_day(value + "-01"))
    next_month = dt.date(first.year + (first.month == 12), first.month % 12 + 1, 1)
    return first, next_month - dt.timedelta(days=1)


def _monthly_contract(value, reference, market):
    keys = {"schema", "market", "source_url", "report_path", "query_format",
            "completion_semantics", "raw_ref", "http_receipt_ref"}
    if (not isinstance(value, Mapping) or set(value) != keys
            or value.get("schema") != "twstock-updater.official-monthly-session-contract.v1"
            or _market(value.get("market")) != market or value.get("report_path") != _MONTHLY[market][0]
            or value.get("source_url") != _MONTHLY[market][1] or value.get("query_format") != "M"
            or value.get("completion_semantics") != _MONTHLY_SEMANTICS):
        _fail("monthly_contract_invalid")
    result = _plain(value)
    result["raw_ref"] = _content_ref(value["raw_ref"])
    result["http_receipt_ref"] = _content_ref(value["http_receipt_ref"])
    claim = _content_ref(reference)
    raw = (json.dumps(result, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False) + "\n").encode("utf-8")
    if claim["sha256"] != hashlib.sha256(raw).hexdigest() or claim["size"] != len(raw):
        _fail("monthly_contract_ref_mismatch")
    return result, claim


def _source_day(value):
    if not isinstance(value, str) or re.fullmatch(r"\d{3,4}/\d{2}/\d{2}", value) is None:
        _fail("monthly_source_date_invalid")
    year, month, day = (int(part) for part in value.split("/"))
    if year < 1911:
        year += 1911
    try:
        return dt.date(year, month, day).isoformat()
    except ValueError:
        _fail("monthly_source_date_invalid")


def _positive_number(value):
    try:
        number = float(value.replace(",", "") if isinstance(value, str) else value)
    except (ValueError, TypeError):
        _fail("monthly_non_positive_row")
    if isinstance(value, bool) or not math.isfinite(number) or number <= 0:
        _fail("monthly_non_positive_row")


def _strict_object(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            _fail("monthly_duplicate_json_key")
        result[key] = value
    return result


def _monthly_report(value, market, *, stored=False):
    keys = {"market", "month", "raw_ref", "http_receipt_ref", "monthly_contract_ref", "monthly_contract",
            "source_url", "observed_at", "http_status", "request_complete", "source_scope_complete",
            "completion_mode", "coverage_start", "coverage_end", "source_reported_row_count"}
    extra = {"raw_response_utf8", "session_dates"} if stored else {"raw_response"}
    if not isinstance(value, Mapping) or set(value) != keys | extra or _market(value.get("market")) != market:
        _fail("monthly_report_shape_invalid")
    if (type(value.get("http_status")) is not int or value["http_status"] != 200
            or value.get("request_complete") is not True or value.get("source_scope_complete") is not True
            or type(value.get("source_reported_row_count")) is not int or value["source_reported_row_count"] <= 0):
        _fail("monthly_report_not_complete")
    first, month_end = _month_bounds(value["month"])
    start, end = _day(value["coverage_start"]), _day(value["coverage_end"])
    if start != first.isoformat() or not start <= end <= month_end.isoformat():
        _fail("monthly_coverage_invalid")
    try:
        observed = dt.datetime.fromisoformat(value["observed_at"].replace("Z", "+00:00"))
    except (ValueError, TypeError, AttributeError):
        _fail("monthly_observation_invalid")
    if observed.tzinfo is None or observed.utcoffset() is None:
        _fail("monthly_observation_invalid")
    observed_day = observed.astimezone(dt.timezone(dt.timedelta(hours=8))).date()
    url = _official_url(value["source_url"], market)
    parts = urlsplit(url)
    query = parse_qs(parts.query, keep_blank_values=True)
    request_date = first.strftime("%Y%m%d") if market == "TWSE" else first.strftime("%Y/%m/%d")
    if (parts.hostname != ("www.twse.com.tw" if market == "TWSE" else "www.tpex.org.tw")
            or parts.path != _MONTHLY[market][0] or query != {"date": [request_date], "response": ["json"]}):
        _fail("monthly_source_query_invalid")
    raw_ref, http_ref = _content_ref(value["raw_ref"]), _content_ref(value["http_receipt_ref"])
    contract, contract_ref = _monthly_contract(value["monthly_contract"], value["monthly_contract_ref"], market)
    try:
        if stored:
            if not isinstance(value["raw_response_utf8"], str):
                _fail("monthly_raw_invalid")
            raw = value["raw_response_utf8"].encode("utf-8")
        else:
            if not isinstance(value["raw_response"], bytes):
                _fail("monthly_raw_invalid")
            raw = value["raw_response"]
        text = raw.decode("utf-8")
    except UnicodeError:
        _fail("monthly_raw_encoding_invalid")
    if hashlib.sha256(raw).hexdigest() != raw_ref["sha256"] or len(raw) != raw_ref["size"]:
        _fail("monthly_raw_ref_mismatch")
    try:
        data = json.loads(text.lstrip("\ufeff"), object_pairs_hook=_strict_object)
    except (ValueError, TypeError):
        _fail("monthly_json_invalid")
    if (not isinstance(data, Mapping) or str(data.get("stat", "")).lower() != "ok"
            or data.get("date") != first.strftime("%Y%m%d")):
        _fail("monthly_response_scope_invalid")
    paging = {"next", "nextPage", "cursor", "offset", "paging", "page", "pageSize", "hasMore"}
    if paging & set(data):
        _fail("monthly_paging_unknown")
    if market == "TWSE":
        table = data
        if (data.get("title") != f"{first.year - 1911}年{first.month:02d}月市場成交資訊"
                or data.get("fields", [])[:4] != ["日期", "成交股數", "成交金額", "成交筆數"]):
            _fail("monthly_fields_invalid")
        positive_columns = (1, 2, 3)
    else:
        tables = data.get("tables")
        if not isinstance(tables, list) or len(tables) != 1 or not isinstance(tables[0], Mapping):
            _fail("monthly_tables_invalid")
        table = tables[0]
        if (table.get("title") != "櫃買指數(月查詢)" or table.get("date") != f"{first.year - 1911}/{first.month:02d}"
                or table.get("fields", [])[:5] != ["日期", "開市", "最高", "最低", "收市"]):
            _fail("monthly_fields_invalid")
        positive_columns = (1, 4)
    if paging & set(table):
        _fail("monthly_paging_unknown")
    rows = table.get("data")
    if not isinstance(rows, list) or not rows or len(rows) != value["source_reported_row_count"]:
        _fail("monthly_row_count_invalid")
    count = table.get("total") if market == "TWSE" else table.get("totalCount")
    if ((market == "TPEx" or count is not None) and (type(count) is not int or count != len(rows))):
        _fail("monthly_reported_count_mismatch")
    dates = []
    for row in rows:
        if not isinstance(row, list) or len(row) <= max(positive_columns):
            _fail("monthly_row_invalid")
        day = _source_day(row[0])
        if not start <= day <= end:
            _fail("monthly_row_outside_coverage")
        for column in positive_columns:
            _positive_number(row[column])
        dates.append(day)
    if dates != sorted(set(dates)):
        _fail("monthly_session_conflict")
    mode = value["completion_mode"]
    if mode == "full_month":
        if end != month_end.isoformat() or observed_day <= month_end:
            _fail("monthly_full_month_not_elapsed")
    elif mode == "month_to_date":
        if end != dates[-1] or observed_day.isoformat() < end:
            _fail("monthly_prefix_not_positive_bound")
    else:
        _fail("monthly_completion_mode_invalid")
    result = {key: _plain(value[key]) for key in keys}
    result.update(market=market, raw_ref=raw_ref, http_receipt_ref=http_ref,
                  monthly_contract=contract, monthly_contract_ref=contract_ref,
                  raw_response_utf8=text, session_dates=dates)
    if stored and value["session_dates"] != dates:
        _fail("monthly_inline_axis_mismatch")
    return result


def _extension_axes(body, first, last):
    base = body.get("base_known_range", body["known_range"])
    if (not isinstance(base, Mapping) or set(base) != {"from", "to"}
            or _day(base["from"]) != first or not first <= _day(base["to"]) <= last):
        _fail("base_range_invalid")
    extensions = body.get("coverage_extensions", [])
    if not isinstance(extensions, list) or bool(extensions) != (base["to"] < last):
        _fail("coverage_extensions_invalid")
    cursor, axes = base["to"], {market: [] for market in _MARKETS}
    for extension in extensions:
        if not isinstance(extension, Mapping) or set(extension) != {"from", "to", "markets"}:
            _fail("coverage_extension_invalid")
        start, end = _day(extension["from"]), _day(extension["to"])
        if start != (dt.date.fromisoformat(cursor) + dt.timedelta(days=1)).isoformat() or not start <= end <= last:
            _fail("coverage_extension_gap")
        reports = extension["markets"]
        if not isinstance(reports, Mapping) or set(reports) != set(_MARKETS):
            _fail("coverage_market_missing")
        for market in _MARKETS:
            if not isinstance(reports[market], list) or not reports[market]:
                _fail("coverage_report_missing")
            checked = [_monthly_report(value, market, stored=True) for value in reports[market]]
            if len({value["month"] for value in checked}) != len(checked):
                _fail("coverage_month_conflict")
            day = dt.date.fromisoformat(start)
            while day.isoformat() <= end:
                matches = [value for value in checked if value["coverage_start"] <= day.isoformat() <= value["coverage_end"]]
                if len(matches) != 1:
                    _fail("coverage_daily_evidence_gap")
                day += dt.timedelta(days=1)
            found = sorted(day for value in checked for day in value["session_dates"] if start <= day <= end)
            if found != sorted(set(found)):
                _fail("coverage_session_conflict")
            axes[market].extend(found)
            # A later complete monthly array must retain every already-verified
            # open and closed date in its overlapping part of the same month.
            for value in checked:
                overlap_start, overlap_end = max(first, value["coverage_start"]), min(cursor, value["coverage_end"])
                if overlap_start <= overlap_end:
                    item = body["markets"][market]
                    if not isinstance(item, Mapping):
                        _fail("market_calendar_unverified")
                    previous = [day for day in _sessions(item.get("sessions")) if overlap_start <= day <= overlap_end]
                    reported = [day for day in value["session_dates"] if overlap_start <= day <= overlap_end]
                    if previous != reported:
                        _fail("coverage_historical_month_conflict")
        cursor = end
    if cursor != last:
        _fail("coverage_incomplete")
    return base["to"], axes


def validate_verified_market_calendar(proof):
    """Return a detached validated body; invalid claims raise ``ValueError``.

    This validates structure and in-body identities only. The caller must also
    verify the descriptor and every ContentRef against immutable stored bytes.
    """
    if not isinstance(proof, Mapping):
        _fail("body_invalid")
    body = _plain(proof)
    if (body.get("schema") != SCHEMA or type(body.get("schema_version")) is not int
            or body["schema_version"] != 1 or body.get("historical_complete_in_known_range") is not True
            or body.get("future_targets_verified") is not False):
        _fail("body_contract_invalid")
    known = body.get("known_range")
    if not isinstance(known, Mapping) or set(known) != {"from", "to"}:
        _fail("range_invalid")
    first, last = _day(known["from"]), _day(known["to"])
    if first > last or _day(body.get("verified_asof")) != last:
        _fail("range_invalid")
    markets = body.get("markets")
    if not isinstance(markets, Mapping) or set(markets) != set(_MARKETS):
        _fail("market_keys_invalid")
    base_last, extension_axes = _extension_axes(body, first, last)
    _content_ref(body.get("source_receipt_ref"))
    sources = _sources(body.get("sources"))
    if len(sources) != 18 or any(sum(item["market"] == market for item in sources) != 9 for market in _MARKETS):
        _fail("source_inventory_incomplete")
    refs = body.get("corroboration_refs")
    if not isinstance(refs, list) or not refs:
        _fail("corroboration_refs_missing")
    for value in refs:
        _content_ref(value)
    for market, item in markets.items():
        if not isinstance(item, Mapping) or item.get("calendar_verified") is not True:
            _fail("market_calendar_unverified")
        sessions = _sessions(item.get("sessions"))
        _axis_ref(item.get("axis_ref"), sessions)
        if item.get("axis_sha256") != item["axis_ref"]["sha256"]:
            _fail("axis_digest_mismatch")
        historical = [day for day in sessions if first <= day <= base_last]
        if not historical or historical[0] != first or historical[-1] != base_last or sessions[0] != first:
            _fail("axis_range_mismatch")
        _axis_ref(item.get("historical_axis_ref"), historical)
        if [day for day in sessions if base_last < day <= last] != extension_axes[market]:
            _fail("coverage_market_axis_mismatch")
        appended = item.get("appended_native_quote_units")
        if not isinstance(appended, list):
            _fail("appended_units_invalid")
        dates = []
        for value in appended:
            day, normalized = _positive_unit(value, market)
            if day <= base_last or day not in sessions:
                _fail("appended_date_outside_extension")
            dates.append(day)
        if dates != sorted(set(dates)) or [day for day in dates if day > last] != [day for day in sessions if day > last]:
            _fail("appended_axis_proof_conflict")
    if body.get("proof_sha256") != _body_digest(body):
        _fail("body_digest_mismatch")
    return body


def build_verified_market_calendar(receipt, axes_by_market, *, source_refs):
    """Build the scoped body from caller-verified receipt, axes and ContentRefs.

    ``source_refs`` requires ``source_receipt_ref``, ``axis_refs`` (TWSE/TPEx),
    ``sources`` (market/source_url/raw_ref/http_receipt_ref), and a nonempty
    ``corroboration_refs`` ContentRef list. No QA path is retained in the body.
    """
    if (not isinstance(receipt, Mapping) or receipt.get("schema") != "official-market-calendar-scoped-reconciliation-v1"
            or receipt.get("status") != "SCOPED_HISTORICAL_AXIS_EVIDENCE_READY_FOR_PRODUCER_REVIEW"
            or receipt.get("residual_unknown_date_candidates_in_historical_scope") != []
            or not isinstance(source_refs, Mapping)):
        _fail("receipt_invalid")
    if set(source_refs) != {"source_receipt_ref", "axis_refs", "sources", "corroboration_refs"}:
        _fail("source_refs_shape_invalid")
    if not isinstance(axes_by_market, Mapping) or not isinstance(source_refs["axis_refs"], Mapping):
        _fail("axis_input_invalid")
    axes, axis_refs = {}, {}
    for supplied_market, values in axes_by_market.items():
        market = _market(supplied_market)
        if market in axes:
            _fail("market_alias_conflict")
        axes[market] = _sessions(values)
    for supplied_market, values in source_refs["axis_refs"].items():
        market = _market(supplied_market)
        if market in axis_refs:
            _fail("market_alias_conflict")
        axis_refs[market] = values
    if set(axes) != set(_MARKETS) or set(axis_refs) != set(_MARKETS):
        _fail("market_missing")
    expected_sources = {}
    market_bodies = {}
    for market in _MARKETS:
        declared_markets = receipt.get("markets") or {}
        if not isinstance(declared_markets, Mapping) or ("TPEx" in declared_markets and "TPEX" in declared_markets):
            _fail("receipt_market_alias_conflict")
        evidence = declared_markets.get(market, declared_markets.get("TPEX") if market == "TPEx" else None)
        if (not isinstance(evidence, Mapping) or evidence.get("scoped_axis_reconciliation_complete") is not True
                or evidence.get("non_synthetic_dates") is not True or evidence.get("market_applicability_verified_separately") is not True
                or evidence.get("session_count") != len(axes[market]) or evidence.get("covered_first") != axes[market][0]
                or evidence.get("covered_last") != axes[market][-1]):
            _fail("receipt_market_axis_mismatch")
        identity = evidence.get("market_sessions_ref") or {}
        axis_ref = _axis_ref(axis_refs[market], axes[market])
        if identity.get("sha256") != axis_ref["sha256"] or identity.get("size_bytes") != axis_ref["size"]:
            _fail("receipt_axis_digest_mismatch")
        positive = evidence.get("positive_observed_market_quote_corroboration") or {}
        if (positive.get("each_candidate_session_has_at_least_one_positive_own_market_quote") is not True
                or positive.get("candidate_sessions_with_positive_quote") != len(axes[market])
                or positive.get("positive_observed_dates_outside_corrected_axis") != []):
            _fail("receipt_positive_scope_unproved")
        for source in evidence.get("annual_planned_closure_proofs") or []:
            key = (market, _official_url(source.get("official_url"), market))
            expected_sources[key] = (source.get("sha256"), source.get("size_bytes"), None)
        for source in evidence.get("complete_official_month_reports") or []:
            raw, http = source.get("source_ref") or {}, source.get("source_receipt_ref") or {}
            if source.get("month_complete") is not True:
                _fail("receipt_month_incomplete")
            key = (market, _official_url(source.get("official_url"), market))
            expected_sources[key] = (raw.get("sha256"), raw.get("size_bytes"), (http.get("sha256"), http.get("size_bytes")))
        market_bodies[market] = {"sessions": axes[market], "axis_sha256": axis_ref["sha256"],
                                 "axis_ref": axis_ref, "historical_axis_ref": deepcopy(axis_ref),
                                 "calendar_verified": True, "appended_native_quote_units": []}
    sources = _sources(source_refs["sources"])
    if len(expected_sources) != 18 or len(sources) != 18:
        _fail("receipt_source_inventory_incomplete")
    for source in sources:
        expected = expected_sources.get((source["market"], source["source_url"]))
        # The publisher may wrap a retained original HTTP receipt with its
        # immutable origin/raw refs. Its different bytes legitimately have a
        # different hash; wrapper-to-origin and physical checks belong there.
        if expected is None or (source["raw_ref"]["sha256"], source["raw_ref"]["size"]) != expected[:2]:
            _fail("receipt_source_binding_mismatch")
    if axes["TWSE"][0] != axes["TPEx"][0] or axes["TWSE"][-1] != axes["TPEx"][-1]:
        _fail("receipt_market_range_conflict")
    body = {"schema": SCHEMA, "schema_version": 1, "verified_asof": axes["TWSE"][-1],
            "known_range": {"from": axes["TWSE"][0], "to": axes["TWSE"][-1]},
            "markets": market_bodies, "sources": sources,
            "source_receipt_ref": _content_ref(source_refs["source_receipt_ref"]),
            "corroboration_refs": [_content_ref(value) for value in source_refs["corroboration_refs"]],
            "historical_complete_in_known_range": True, "future_targets_verified": False}
    return validate_verified_market_calendar(_seal(body))


def append_verified_sessions(proof, market, native_quote_units):
    """Append only caller-adopted positive own-market official native units.

    This function alone preserves the complete historical range. A later
    observed date does not prove intervening days or a future target calendar.
    The caller stores the returned axis bytes before publishing the new ref.
    """
    body = validate_verified_market_calendar(proof)
    market = _market(market)
    if not isinstance(native_quote_units, (list, tuple)):
        _fail("native_quote_units_invalid")
    item = body["markets"][market]
    last = body["known_range"]["to"]
    existing = {day: value for value in item["appended_native_quote_units"] for day, _ in [_positive_unit(value, market)]}
    changed = False
    for value in native_quote_units:
        day, checked = _positive_unit(value, market)
        if day <= last:
            if day not in item["sessions"]:
                _fail("native_quote_historical_conflict")
            continue
        if day in existing:
            if existing[day] != checked:
                _fail("native_quote_duplicate_conflict")
            continue
        existing[day] = checked
        changed = True
    if not changed:
        return body
    item["appended_native_quote_units"] = [existing[day] for day in sorted(existing)]
    item["sessions"] = sorted(set(item["sessions"]) | set(existing))
    digest, size = _axis_identity(item["sessions"])
    item["axis_sha256"] = digest
    item["axis_ref"] = {"ref": "_calendar/axes/" + digest + ".json", "sha256": digest, "size": size}
    return validate_verified_market_calendar(_seal(body))


def advance_verified_market_calendar(proof, monthly_reports_by_market, *, through):
    """Extend complete coverage with independent official monthly arrays.

    Every natural day from the previous bound through ``through`` must belong
    to an explicitly complete own-market report in both markets. Missing rows
    only mean closed within that complete bound. A month-to-date bound ends at
    its final positive returned session; a full month must have elapsed when
    fetched. No weekday rule, price-date union, HTTP, or publication occurs.

    Input reports carry exact raw bytes, immutable raw/HTTP/contract refs and
    their declared count, monthly query, observation and completion bounds.
    Output retains raw UTF-8 for independent pure validation. Callers verify
    all physical refs and official HTML monthly-query contract markers, store
    updated axis bytes, and publish the new descriptor through their own CAS.
    """
    body = validate_verified_market_calendar(proof)
    through = _day(through)
    previous = body["known_range"]["to"]
    if through < previous:
        _fail("advance_range_regression")
    if through == previous:
        return body
    if not isinstance(monthly_reports_by_market, Mapping):
        _fail("advance_reports_missing")
    reports = {}
    for supplied, values in monthly_reports_by_market.items():
        market = _market(supplied)
        if market in reports:
            _fail("advance_market_alias_conflict")
        if not isinstance(values, (list, tuple)) or not values:
            _fail("advance_market_report_missing")
        reports[market] = [_monthly_report(value, market) for value in values]
    if set(reports) != set(_MARKETS):
        _fail("advance_market_missing")
    start = (dt.date.fromisoformat(previous) + dt.timedelta(days=1)).isoformat()
    for market in _MARKETS:
        reports[market].sort(key=lambda value: value["month"])
        if len({value["month"] for value in reports[market]}) != len(reports[market]):
            _fail("advance_month_conflict")
        item = body["markets"][market]
        for value in reports[market]:
            if value["coverage_end"] < start or value["coverage_start"] > through:
                _fail("advance_report_outside_range")
            known = [day for day in item["sessions"] if start <= day <= through]
            reported = [day for day in value["session_dates"] if start <= day <= through]
            within_report = [day for day in known if value["coverage_start"] <= day <= value["coverage_end"]]
            if not set(within_report) <= set(reported):
                _fail("advance_native_positive_conflict")
        added = {day for value in reports[market] for day in value["session_dates"] if start <= day <= through}
        item["sessions"] = sorted(set(item["sessions"]) | added)
        digest, size = _axis_identity(item["sessions"])
        item["axis_sha256"] = digest
        item["axis_ref"] = {"ref": "_calendar/axes/" + digest + ".json", "sha256": digest, "size": size}
    body.setdefault("base_known_range", deepcopy(body["known_range"]))
    body.setdefault("coverage_extensions", []).append({"from": start, "to": through, "markets": reports})
    body["known_range"]["to"] = through
    body["verified_asof"] = through
    return validate_verified_market_calendar(_seal(body))
