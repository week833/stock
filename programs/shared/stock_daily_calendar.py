"""Strict bounded calendar grammar. Physical references are verified by callers.

This module performs no I/O. A retirement observation is deliberately not a
ContentRef: it records a past observation, never a claim of available bytes.
Axis bytes retain the established calendar CRLF codec on every platform.
"""
from __future__ import annotations

from collections.abc import Mapping
import datetime as dt
import hashlib
import json
import re
from urllib.parse import parse_qs, urlsplit

SCHEMA = "twstock-updater.verified-market-calendar.v2"
RETIREMENT_SCHEMA = "twstock.evidence-retirement.v1"
ADVANCE_SCHEMA = "twstock-updater.market-calendar-bounded-advance.v1"
MARKETS = ("TWSE", "TPEx")
_SHA = re.compile(r"[0-9a-f]{64}")
_TOP = {"schema", "schema_version", "verified_asof", "scope_id",
        "retention_scope_binding", "historical_axis_verified", "future_targets_verified",
        "markets", "source_receipt_ref", "sources", "retired_historical_observations",
        "derivation", "proof_sha256"}
_MARKET_KEYS = {"calendar_verified", "future_axis_verified", "historical_sessions",
                "future_sessions", "sessions", "axis_ref", "historical_axis_ref",
                "future_axis_ref", "axis_sha256"}
_DATA_FIELDS = {"data_start", "data_end", "data_sessions"}
_RETIREMENT_KEYS = {"schema", "schema_version", "market", "source_url",
                    "request_identity", "native_unit", "observed_at", "verified_at",
                    "historical_ref", "raw_sha256", "raw_size", "reason", "retired_at",
                    "migration_id", "physical_state", "reverification_available"}


def _fail(reason):
    raise ValueError("verified_market_calendar_v2_" + reason)


def _plain(value):
    try:
        return json.loads(json.dumps(value, ensure_ascii=False, allow_nan=False))
    except (TypeError, ValueError) as exc:
        raise ValueError("verified_market_calendar_v2_non_json") from exc


def _digest(value):
    return hashlib.sha256(json.dumps(value, ensure_ascii=False, sort_keys=True,
                                    separators=(",", ":"), allow_nan=False).encode("utf-8")).hexdigest()


def _sha(value):
    if not isinstance(value, str) or _SHA.fullmatch(value) is None:
        _fail("digest_invalid")
    return value


def _day(value):
    if not isinstance(value, str) or not re.fullmatch(r"\d{4}-\d{2}-\d{2}", value):
        _fail("date_invalid")
    try:
        if dt.date.fromisoformat(value).isoformat() != value:
            _fail("date_invalid")
    except ValueError as exc:
        raise ValueError("verified_market_calendar_v2_date_invalid") from exc
    return value


def _instant(value):
    if not isinstance(value, str):
        _fail("time_invalid")
    try:
        parsed = dt.datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError as exc:
        raise ValueError("verified_market_calendar_v2_time_invalid") from exc
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        _fail("time_unaware")
    return value


def _sessions(value, *, empty=False):
    if not isinstance(value, list) or (not value and not empty):
        _fail("sessions_invalid")
    days = [_day(day) for day in value]
    if days != sorted(set(days)):
        _fail("sessions_conflict")
    return days


def axis_bytes(sessions):
    """The existing immutable calendar axis codec (not the proof codec)."""
    return (json.dumps(sessions, ensure_ascii=False) + "\r\n").encode("utf-8")


def _content_ref(value):
    if not isinstance(value, Mapping) or set(value) != {"ref", "sha256", "size"}:
        _fail("physical_content_ref_invalid")
    claim = dict(value)
    _relative(claim["ref"])
    _sha(claim["sha256"])
    if (not claim["ref"].startswith("_calendar/") or type(claim["size"]) is not int
            or claim["size"] <= 0):
        _fail("physical_content_ref_invalid")
    return claim


def _relative(value):
    if (not isinstance(value, str) or not value or value.strip() != value or value.startswith("/")
            or any(char in value for char in "\\:\x00")
            or any(part in {"", ".", ".."} for part in value.split("/"))
            or value.split("/")[0] == ".private"):
        _fail("relative_reference_invalid")
    return value


def _axis_ref(value, sessions):
    claim = _content_ref(value)
    raw = axis_bytes(sessions)
    digest = hashlib.sha256(raw).hexdigest()
    if (claim["sha256"] != digest or claim["size"] != len(raw)
            or claim["ref"] != "_calendar/axes/" + digest + ".json"):
        _fail("axis_ref_mismatch")
    return claim


def _url(value, market):
    if not isinstance(value, str) or any(ord(char) <= 32 for char in value):
        _fail("source_url_invalid")
    parsed = urlsplit(value)
    hosts = {"www.twse.com.tw", "openapi.twse.com.tw"} if market == "TWSE" else {"www.tpex.org.tw"}
    if (parsed.scheme != "https" or parsed.hostname not in hosts or parsed.username
            or parsed.password or parsed.fragment or parsed.netloc != parsed.hostname):
        _fail("source_url_invalid")
    if any(key.lower() in {"token", "api_key", "apikey", "authorization", "password"}
           for key in parse_qs(parsed.query)):
        _fail("secret_source_url")
    return value


def _observation_url(value):
    if not isinstance(value, str) or any(ord(char) <= 32 for char in value):
        _fail("retirement_source_url_invalid")
    parsed = urlsplit(value)
    if (parsed.scheme not in {"http", "https"} or not parsed.hostname or parsed.username
            or parsed.password or parsed.fragment):
        _fail("retirement_source_url_invalid")
    if any(key.lower() in {"token", "api_key", "apikey", "authorization", "password"}
           for key in parse_qs(parsed.query)):
        _fail("secret_source_url")
    return value


def validate_evidence_retirement(value):
    """Validate a historical observation; never return a physical reference."""
    body = _plain(value)
    if (not isinstance(body, dict) or set(body) != _RETIREMENT_KEYS
            or body.get("schema") != RETIREMENT_SCHEMA or type(body.get("schema_version")) is not int
            or body["schema_version"] != 1 or body.get("market") not in (*MARKETS, None)
            or body.get("physical_state") != "retired"
            or body.get("reverification_available") is not False
            or body.get("reason") != "retired_out_of_scope"):
        _fail("retirement_invalid")
    _observation_url(body["source_url"])
    _sha(body["raw_sha256"])
    _sha(body["migration_id"])
    _relative(body["historical_ref"])
    if type(body["raw_size"]) is not int or body["raw_size"] < 0:
        _fail("retirement_size_invalid")
    for name in ("observed_at", "verified_at", "retired_at"):
        _instant(body[name])
    times = [dt.datetime.fromisoformat(body[name].replace("Z", "+00:00"))
             for name in ("observed_at", "verified_at", "retired_at")]
    if times != sorted(times):
        _fail("retirement_observation_order_invalid")
    for name in ("request_identity", "native_unit"):
        if not isinstance(body[name], dict) or not body[name]:
            _fail("retirement_native_identity_missing")
    # These fields cannot recursively smuggle a ContentRef into live traversal.
    def check(item):
        if isinstance(item, dict):
            if (isinstance(item.get("ref", item.get("path")), str) and "sha256" in item
                    and ("size" in item or "byte_size" in item)):
                _fail("retirement_contains_physical_ref")
            for key, child in item.items():
                if key.lower() in {"token", "api_key", "apikey", "authorization", "password"}:
                    _fail("retirement_contains_secret")
                check(child)
        elif isinstance(item, list):
            for child in item:
                check(child)
    check(body["request_identity"])
    check(body["native_unit"])
    return body


def _historical_refs(value):
    if isinstance(value, dict):
        if (isinstance(value.get("ref", value.get("path")), str) and "sha256" in value
                and ("size" in value or "byte_size" in value)):
            return {"historical_ref": value.get("ref", value.get("path")), "sha256": value["sha256"],
                    "size": value.get("size", value.get("byte_size")),
                    "physical_state": "retired", "reverification_available": False}
        return {key: _historical_refs(child) for key, child in value.items()}
    if isinstance(value, list):
        return [_historical_refs(child) for child in value]
    return value


def build_evidence_retirement(source, *, request_identity, verified_at, retired_at, migration_id,
                              native_unit=None, observed_at=None):
    """Preserve original nonsecret identities while dropping physical-ref grammar."""
    source = _plain(source)
    raw = source.get("raw_ref")
    if not isinstance(raw, dict) or set(raw) != {"ref", "sha256", "size"}:
        _fail("retirement_original_raw_identity_missing")
    identity = _historical_refs(_plain(request_identity))
    http = source.get("http_receipt_ref")
    if isinstance(http, dict):
        identity["original_http_observation"] = _historical_refs(http)
    body = {"schema": RETIREMENT_SCHEMA, "schema_version": 1, "market": source.get("market"),
        "source_url": source["source_url"], "request_identity": identity,
        "native_unit": _historical_refs(_plain(native_unit if native_unit is not None else source.get("native_unit"))),
        "observed_at": observed_at if observed_at is not None else source.get("observed_at"),
        "verified_at": verified_at, "historical_ref": raw["ref"], "raw_sha256": raw["sha256"], "raw_size": raw["size"],
        "reason": "retired_out_of_scope", "retired_at": retired_at, "migration_id": migration_id,
        "physical_state": "retired", "reverification_available": False}
    return validate_evidence_retirement(body)


def validate_verified_market_calendar_v2(proof):
    """Return detached strict v2 grammar; caller verifies *every live* ref."""
    body = _plain(proof)
    if (not isinstance(body, dict) or set(body) != _TOP or body.get("schema") != SCHEMA
            or type(body.get("schema_version")) is not int or body["schema_version"] != 2
            or body.get("historical_axis_verified") is not True):
        _fail("body_contract_invalid")
    asof, scope_id = _day(body["verified_asof"]), _sha(body["scope_id"])
    binding = body["retention_scope_binding"]
    if (not isinstance(binding, dict)
            or set(binding) != {"scope_id", "capture_scope_id", "asof", "market_session_sha256"}
            or binding["scope_id"] != scope_id or binding["asof"] != asof):
        _fail("scope_binding_invalid")
    _sha(binding["capture_scope_id"])
    markets = body["markets"]
    if not isinstance(markets, dict) or set(markets) != set(MARKETS):
        _fail("market_keys_invalid")
    session_digests = {}
    future_complete = True
    for market in MARKETS:
        item = markets[market]
        present = _DATA_FIELDS.intersection(item) if isinstance(item, dict) else set()
        if (present and present != _DATA_FIELDS or not isinstance(item, dict)
                or set(item) != _MARKET_KEYS | present
                or item.get("calendar_verified") is not True):
            _fail("market_contract_invalid")
        historical = _sessions(item["historical_sessions"])
        future = _sessions(item["future_sessions"], empty=True)
        if ((len(historical) < 253 if present else len(historical) != 253)
                or historical[-1] > asof or len(future) > 63):
            _fail("market_scope_invalid")
        if present:
            start, end = _day(item["data_start"]), _day(item["data_end"])
            if (start > end or item["data_sessions"] != historical
                    or any(not start <= day <= end for day in historical)):
                _fail("market_data_scope_invalid")
        if future and future[0] <= asof:
            _fail("future_scope_invalid")
        if item["sessions"] != historical + future:
            _fail("axis_union_invalid")
        _axis_ref(item["axis_ref"], item["sessions"])
        _axis_ref(item["historical_axis_ref"], historical)
        if future:
            _axis_ref(item["future_axis_ref"], future)
        elif item["future_axis_ref"] is not None:
            _fail("empty_future_ref_invalid")
        if item["future_axis_verified"] is not bool(future):
            _fail("future_verification_invalid")
        if item["axis_sha256"] != item["axis_ref"]["sha256"]:
            _fail("axis_digest_mismatch")
        session_digests[market] = _digest(historical)
        future_complete = future_complete and len(future) == 63
    if body["future_targets_verified"] is not future_complete:
        _fail("future_completeness_invalid")
    if binding["market_session_sha256"] != session_digests:
        _fail("scope_axis_binding_invalid")
    _content_ref(body["source_receipt_ref"])
    if not isinstance(body["sources"], list):
        _fail("sources_invalid")
    identities = set()
    for item in body["sources"]:
        if (not isinstance(item, dict) or set(item) != {"market", "source_url", "native_unit",
                                                       "observed_at", "raw_ref", "http_receipt_ref"}
                or item["market"] not in MARKETS or not isinstance(item["native_unit"], dict)
                or not item["native_unit"]):
            _fail("source_invalid")
        _url(item["source_url"], item["market"])
        _instant(item["observed_at"])
        _content_ref(item["raw_ref"])
        _content_ref(item["http_receipt_ref"])
        if item["native_unit"].get("kind") == "calendar_month":
            unit = item["native_unit"]
            _content_ref(unit.get("monthly_contract_ref"))
            if (unit.get("market") != item["market"] or unit.get("request_complete") is not True
                    or unit.get("source_scope_complete") is not True
                    or unit.get("completion_mode") not in {"full_month", "month_to_date"}):
                _fail("monthly_native_unit_invalid")
            _day(str(unit.get("month")) + "-01")
            _day(unit.get("coverage_start"))
            _day(unit.get("coverage_end"))
            sessions = _sessions(unit.get("session_dates"))
            if (len(sessions) > 31 or type(unit.get("source_reported_row_count")) is not int
                    or unit["source_reported_row_count"] != len(sessions)
                    or any(not unit["coverage_start"] <= day <= unit["coverage_end"] for day in sessions)):
                _fail("monthly_native_session_count_invalid")
        identity = (item["market"], _digest(item["native_unit"]), item["raw_ref"]["sha256"])
        if identity in identities:
            _fail("source_conflict")
        identities.add(identity)
    observations = body["retired_historical_observations"]
    if not isinstance(observations, list):
        _fail("retirements_invalid")
    for item in observations:
        validate_evidence_retirement(item)
        if item["market"] not in MARKETS:
            _fail("calendar_retirement_market_invalid")
    if not body["sources"] and (not observations or {item["market"] for item in observations} != set(MARKETS)):
        _fail("derived_source_audit_missing")
    derivation = body["derivation"]
    if (not isinstance(derivation, dict) or set(derivation) != {"parent_proof_sha256",
            "retained_values_unchanged", "market_session_sha256"}
            or derivation["retained_values_unchanged"] is not True
            or derivation["market_session_sha256"] != session_digests):
        _fail("derivation_invalid")
    _sha(derivation["parent_proof_sha256"])
    if body["proof_sha256"] != _digest({key: value for key, value in body.items() if key != "proof_sha256"}):
        _fail("body_digest_mismatch")
    return body


def _scope_markets(scope):
    markets = scope.get("markets")
    if not isinstance(markets, dict) or set(markets) != set(MARKETS):
        _fail("retention_market_missing")
    return markets


def validate_calendar_scope_derivation(proof, value):
    """Bind an actually read derivation receipt to the final bounded proof."""
    proof = validate_verified_market_calendar_v2(proof)
    body = _plain(value)
    keys = {"schema", "schema_version", "migration_id", "retained_values_unchanged", "parent_proof_sha256",
            "scope_id", "capture_scope_id", "verified_asof", "market_session_sha256",
            "market_future_session_sha256", "sources_sha256", "retired_historical_observations"}
    if (not isinstance(body, dict) or set(body) != keys
            or body.get("schema") != "twstock-updater.calendar-scope-derivation.v1"
            or type(body.get("schema_version")) is not int or body["schema_version"] != 1
            or body.get("retained_values_unchanged") is not True
            or body.get("parent_proof_sha256") != proof["derivation"]["parent_proof_sha256"]
            or body.get("scope_id") != proof["scope_id"]
            or body.get("capture_scope_id") != proof["retention_scope_binding"]["capture_scope_id"]
            or body.get("verified_asof") != proof["verified_asof"]
            or body.get("market_session_sha256") != proof["retention_scope_binding"]["market_session_sha256"]
            or body.get("market_future_session_sha256") != {
                market: _digest(item["future_sessions"]) for market, item in proof["markets"].items()}
            or body.get("sources_sha256") != _digest(proof["sources"])
            or body.get("retired_historical_observations") != proof["retired_historical_observations"]):
        _fail("scope_derivation_binding_invalid")
    _sha(body["migration_id"])
    return body


def _monthly_native_unit(report, market):
    return {"kind": "calendar_month", "market": market, "month": report["month"],
        "coverage_start": report["coverage_start"], "coverage_end": report["coverage_end"],
        "completion_mode": report["completion_mode"], "request_complete": True,
        "source_scope_complete": True, "monthly_contract_ref": report["monthly_contract_ref"],
        "source_reported_row_count": report["source_reported_row_count"], "session_dates": report["session_dates"]}


def validate_calendar_monthly_source(source, raw_bytes, http_body, monthly_contract,
                                     html_raw_bytes, html_http_body, *,
                                     monthly_report_validator=None, original_http_body=None):
    """Verify physical monthly/HTML bodies with the existing strict parser.

    Consumer copies may pass their pinned local daily_calendar._monthly_report;
    no producer path import or alternate month parser is required. The caller
    has already verified the ContentRefs of all read bodies, including an
    optional wrapped original HTTP receipt. This function performs no I/O.
    """
    source, http, contract, html_http = map(_plain, (source, http_body, monthly_contract, html_http_body))
    if not isinstance(raw_bytes, bytes) or not isinstance(html_raw_bytes, bytes):
        _fail("monthly_physical_bytes_required")
    unit = source.get("native_unit")
    if not isinstance(unit, dict) or unit.get("kind") != "calendar_month":
        _fail("monthly_native_unit_required")
    raw_ref = _content_ref(source.get("raw_ref"))
    if (hashlib.sha256(raw_bytes).hexdigest() != raw_ref["sha256"] or len(raw_bytes) != raw_ref["size"]
            or http.get("schema") != "twstock-updater.market-calendar-source-receipt.v1"
            or http.get("http_status") != 200 or http.get("market") != source["market"]
            or http.get("raw") != raw_ref or http.get("source_url") != source["source_url"]
            or http.get("observed_at") != source["observed_at"]):
        _fail("monthly_source_http_binding_invalid")
    if http.get("original_http_receipt_ref") is not None:
        if original_http_body is None:
            _fail("monthly_original_http_body_required")
        original = _plain(original_http_body)
        if (original.get("status") != 200 or original.get("url") != source["source_url"]
                or original.get("sha256") != raw_ref["sha256"]
                or original.get("size_bytes") != raw_ref["size"]
                or original.get("retrieved_at_utc") != source["observed_at"]):
            _fail("monthly_original_http_binding_invalid")
    if not isinstance(contract, dict) or contract.get("schema") != "twstock-updater.official-monthly-session-contract.v1":
        _fail("monthly_contract_invalid")
    html_ref = _content_ref(contract.get("raw_ref"))
    if (hashlib.sha256(html_raw_bytes).hexdigest() != html_ref["sha256"] or len(html_raw_bytes) != html_ref["size"]
            or html_http.get("status") != 200 or html_http.get("url") != contract.get("source_url")
            or html_http.get("sha256") != html_ref["sha256"] or html_http.get("size_bytes") != html_ref["size"]):
        _fail("monthly_html_http_binding_invalid")
    _instant(html_http.get("retrieved_at_utc"))
    markers = {"TWSE": (b'data-api="/afterTrading/FMTQIK"', b'data-date="b:1990,e:0,f:M"'),
               "TPEx": (b'data-format="M"', b'action:"indexInfo/inx"')}
    if source.get("market") not in MARKETS or any(marker not in html_raw_bytes for marker in markers[source["market"]]):
        _fail("monthly_html_markers_invalid")
    if monthly_report_validator is None:
        from . import daily_calendar
        monthly_report_validator = daily_calendar._monthly_report
    if not callable(monthly_report_validator):
        _fail("monthly_existing_parser_required")
    report = {"market": source["market"], "month": unit["month"], "raw_ref": raw_ref,
        "http_receipt_ref": _content_ref(source["http_receipt_ref"]),
        "monthly_contract_ref": _content_ref(unit["monthly_contract_ref"]), "monthly_contract": contract,
        "source_url": source["source_url"], "observed_at": source["observed_at"], "http_status": 200,
        "request_complete": unit.get("request_complete"), "source_scope_complete": unit.get("source_scope_complete"),
        "completion_mode": unit["completion_mode"], "coverage_start": unit["coverage_start"],
        "coverage_end": unit["coverage_end"], "source_reported_row_count": unit["source_reported_row_count"],
        "raw_response": raw_bytes}
    verified = monthly_report_validator(report, source["market"])
    if verified["session_dates"] != unit.get("session_dates"):
        _fail("monthly_native_session_binding_invalid")
    return _plain(verified)


def advance_verified_market_calendar_v2(proof, monthly_reports_by_market, *, through):
    """Strict finite month evidence, awaiting a *new* capture/retention binding.

    The result is deliberately not a live calendar v2. Both markets must prove
    every natural day in the extension with complete official monthly arrays;
    missing positives mean closed only within those proved complete bounds.
    The controller writes new axes/source derivation, resolves the new scope,
    and calls build_verified_market_calendar_v2 on this intermediate evidence.
    """
    from . import daily_calendar
    old = validate_verified_market_calendar_v2(proof)
    through = _day(through)
    previous = old["verified_asof"]
    if through < previous:
        _fail("advance_range_regression")
    if through == previous:
        return old
    previous_day = dt.date.fromisoformat(previous)
    current_month = previous[:7]
    next_month_first = dt.date(previous_day.year + (previous_day.month == 12), previous_day.month % 12 + 1, 1)
    _, next_month_end = daily_calendar._month_bounds(next_month_first.strftime("%Y-%m"))
    if dt.date.fromisoformat(through) > next_month_end:
        _fail("advance_outside_same_or_adjacent_month")
    if not isinstance(monthly_reports_by_market, Mapping) or set(monthly_reports_by_market) != set(MARKETS):
        _fail("advance_market_missing")
    allowed_months = {current_month, next_month_first.strftime("%Y-%m")}
    start = (previous_day + dt.timedelta(days=1)).isoformat()
    reports, market_bodies, sources, units = {}, {}, _plain(old["sources"]), []
    for market in MARKETS:
        supplied = monthly_reports_by_market[market]
        if not isinstance(supplied, (list, tuple)) or not 1 <= len(supplied) <= 2:
            _fail("advance_market_report_missing")
        checked = [daily_calendar._monthly_report(value, market, stored="raw_response_utf8" in value)
                   for value in supplied]
        checked.sort(key=lambda value: value["month"])
        if (len({value["month"] for value in checked}) != len(checked)
                or any(value["month"] not in allowed_months for value in checked)):
            _fail("advance_month_scope_invalid")
        prior_sessions = old["markets"][market]["sessions"]
        for report in checked:
            if report["coverage_end"] < start or report["coverage_start"] > through:
                _fail("advance_report_outside_range")
            overlap_start = max(old["markets"][market]["historical_sessions"][0], report["coverage_start"])
            overlap_end = min(previous, report["coverage_end"])
            prior = [day for day in prior_sessions if overlap_start <= day <= overlap_end]
            reported = [day for day in report["session_dates"] if overlap_start <= day <= overlap_end]
            if prior != reported:
                _fail("advance_historical_month_conflict")
        cursor = dt.date.fromisoformat(start)
        while cursor.isoformat() <= through:
            matching = [value for value in checked if value["coverage_start"] <= cursor.isoformat() <= value["coverage_end"]]
            if len(matching) != 1:
                _fail("advance_daily_evidence_gap")
            cursor += dt.timedelta(days=1)
        appended = sorted({day for value in checked for day in value["session_dates"] if start <= day <= through})
        prior_future = [day for day in prior_sessions if start <= day <= through]
        if not set(prior_future) <= set(appended):
            _fail("advance_verified_future_conflict")
        proven = sorted(set(prior_sessions) | set(appended))
        historical = [day for day in proven if day <= through][-253:]
        future = [day for day in proven if day > through][:63]
        if len(historical) != 253:
            _fail("advance_exact253_unavailable")
        retired = [day for day in old["markets"][market]["historical_sessions"] if day not in historical]
        market_bodies[market] = {"historical_sessions": historical, "future_sessions": future,
            "sessions": historical + future, "retired_sessions": retired,
            "historical_session_sha256": _digest(historical), "appended_sessions": appended}
        reports[market] = checked
        for report in checked:
            native = _monthly_native_unit(report, market)
            source = {"market": market, "source_url": report["source_url"], "native_unit": native,
                "observed_at": report["observed_at"], "raw_ref": report["raw_ref"],
                "http_receipt_ref": report["http_receipt_ref"]}
            units.append(source)
            # Re-fetching the same unit can replace its old physical proof only
            # in the next explicitly bound scope; preserve this observation.
            if source not in sources:
                sources.append(source)
    body = {"schema": ADVANCE_SCHEMA, "schema_version": 1,
        "status": "VERIFIED_SESSIONS_AWAITING_SCOPE_REBIND", "previous_proof": old,
        "previous_proof_sha256": old["proof_sha256"], "from": start, "to": through, "verified_asof": through,
        "markets": market_bodies, "monthly_reports_by_market": reports, "sources": sources,
        "source_units": units, "retired_historical_observations": old["retired_historical_observations"]}
    body["advance_sha256"] = _digest(body)
    return body


def _validated_advance(value):
    body = _plain(value)
    if (not isinstance(body, dict) or body.get("schema") != ADVANCE_SCHEMA
            or type(body.get("schema_version")) is not int or body["schema_version"] != 1):
        _fail("advance_schema_invalid")
    rebuilt = advance_verified_market_calendar_v2(body.get("previous_proof"),
        body.get("monthly_reports_by_market"), through=body.get("to"))
    if body != rebuilt:
        _fail("advance_identity_invalid")
    return rebuilt


def _validated_origin(value):
    if isinstance(value, Mapping) and value.get("schema") == ADVANCE_SCHEMA:
        return _validated_advance(value)
    if isinstance(value, Mapping) and value.get("schema") == SCHEMA:
        return validate_verified_market_calendar_v2(value)
    from . import daily_calendar
    return daily_calendar.validate_verified_market_calendar(value)


def _source_observations(proof):
    sources = _plain(proof["sources"])
    if proof["schema"] not in {SCHEMA, ADVANCE_SCHEMA}:
        for market, item in proof["markets"].items():
            for quote in item["appended_native_quote_units"]:
                evidence = quote["source_evidence"]
                sources.append({"market": market, "source_url": evidence.get("source_url", evidence.get("url")),
                    "raw_ref": quote["raw_ref"], "http_receipt_ref": quote["http_receipt_ref"],
                    "observed_at": evidence["observed_at"],
                    "native_unit": {"kind": "official_daily_native_quote", "unit": quote["unit"],
                                    "source_evidence": evidence, "adoption": quote["adoption"]}})
        for extension in proof.get("coverage_extensions", []):
            for market, reports in extension["markets"].items():
                for report in reports:
                    sources.append({"market": market, "source_url": report["source_url"],
                        "raw_ref": report["raw_ref"], "http_receipt_ref": report["http_receipt_ref"],
                        "observed_at": report["observed_at"], "native_unit": _monthly_native_unit(report, market)})
    unique, seen = [], set()
    for source in sources:
        key = (source["market"], source["source_url"], _digest(source["raw_ref"]),
               _digest(source["http_receipt_ref"]), _digest(source.get("native_unit")))
        if key not in seen:
            seen.add(key)
            unique.append(_plain(source))
    return unique


def calendar_source_observations(proof):
    """Enumerate every original native observation from one strict proof.

    Legacy four-field sources lack an observation clock; the caller reads the
    already referenced original HTTP body to supply it. Native quote and month
    observations retain their original request, time, unit and adoption fields.
    No physical body is read or requested here.
    """
    return _source_observations(_validated_origin(proof))


def build_verified_market_calendar_v2(oldproof, retention_scope, source_refs):
    """Derive a bounded proof solely from an already strict verified axis.

    source_refs contains source_receipt_ref, axis_refs, historical_axis_refs,
    future_axis_refs, sources, retired_historical_observations. All supplied
    refs must have been physically verified/written by the publication caller.
    Original HTTP times and request/native identities are never rewritten.
    """
    old = _validated_origin(oldproof)
    from .retention_migration import validate_retention_scope
    scope, refs = validate_retention_scope(retention_scope), _plain(source_refs)
    if (not isinstance(scope, dict) or scope.get("schema") != "twstock.application-retention-scope.v1"
            or scope.get("scope_certainty") != "known"
            or not isinstance(refs, dict) or set(refs) != {"source_receipt_ref", "axis_refs",
                "historical_axis_refs", "future_axis_refs", "sources", "retired_historical_observations"}):
        _fail("builder_inputs_invalid")
    asof = _day(scope["asof"])
    observations = [validate_evidence_retirement(value) for value in refs["retired_historical_observations"]]
    for old_source in _source_observations(old):
        identity = (old_source["market"], old_source["source_url"],
                    old_source["raw_ref"]["sha256"], old_source["raw_ref"]["size"])
        retained_source = any((value["market"], value["source_url"], value["raw_ref"]["sha256"],
                               value["raw_ref"]["size"]) == identity
                              and value["http_receipt_ref"] == old_source["http_receipt_ref"]
                              and ("native_unit" not in old_source or value["native_unit"] == old_source["native_unit"])
                              and ("observed_at" not in old_source or value["observed_at"] == old_source["observed_at"])
                              for value in refs["sources"])
        retired_source = any((value["market"], value["source_url"], value["raw_sha256"], value["raw_size"])
                             == identity and value["request_identity"].get("original_http_observation")
                             == _historical_refs(old_source["http_receipt_ref"])
                             and ("native_unit" not in old_source or value["native_unit"] == _historical_refs(old_source["native_unit"]))
                             and ("observed_at" not in old_source or value["observed_at"] == old_source["observed_at"])
                             for value in observations)
        if not retained_source and not retired_source:
            _fail("builder_original_source_observation_missing")
    if old["schema"] in {SCHEMA, ADVANCE_SCHEMA} and any(value not in observations for value in old["retired_historical_observations"]):
        _fail("builder_historical_observation_rewritten")
    market_bodies, digests = {}, {}
    for market, item in _scope_markets(scope).items():
        historical = _sessions(item["historical_sessions"])
        future = _sessions(item["future_sessions"], empty=True)
        proven = set(old["markets"][market]["sessions"])
        if (len(historical) != 253 or historical != [day for day in old["markets"][market]["sessions"]
                if day <= asof][-253:] or not set(future) <= proven):
            _fail("builder_unverified_or_shifted_axis")
        sessions = historical + future
        market_bodies[market] = {"calendar_verified": True, "future_axis_verified": bool(future),
            "historical_sessions": historical, "future_sessions": future, "sessions": sessions,
            "axis_ref": refs["axis_refs"][market], "historical_axis_ref": refs["historical_axis_refs"][market],
            "future_axis_ref": refs["future_axis_refs"][market], "axis_sha256": refs["axis_refs"][market]["sha256"]}
        digests[market] = _digest(historical)
    body = {"schema": SCHEMA, "schema_version": 2, "verified_asof": asof,
        "scope_id": scope["scope_id"], "retention_scope_binding": {"scope_id": scope["scope_id"],
            "capture_scope_id": scope["capture_scope_id"], "asof": asof, "market_session_sha256": digests},
        "historical_axis_verified": True,
        "future_targets_verified": all(len(item["future_sessions"]) == 63 for item in market_bodies.values()),
        "markets": market_bodies, "source_receipt_ref": refs["source_receipt_ref"],
        "sources": refs["sources"], "retired_historical_observations": refs["retired_historical_observations"],
        "derivation": {"parent_proof_sha256": old.get("proof_sha256", old.get("advance_sha256")),
            "retained_values_unchanged": True, "market_session_sha256": digests}}
    body["proof_sha256"] = _digest(body)
    return validate_verified_market_calendar_v2(body)
