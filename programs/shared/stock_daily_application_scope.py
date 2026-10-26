"""Consumer-side validation of the adopted bounded scope data grammar.

This module has no producer executable dependency or acquisition path.  It
checks the publisher's exact digests and binding; it cannot invent membership,
sessions, completeness or a usable scope from a blocked declaration.
"""
from __future__ import annotations

import copy
import hashlib
import json
import re
from collections.abc import Mapping
from datetime import date, datetime, timedelta, timezone
from urllib.parse import urlsplit, parse_qsl

ROUTES = {"official_calendar", "official_market_daily", "finmind_stock_info",
    "finmind_price_adj", "finmind_dividend", "finmind_dividend_result"}
_CORE_ROUTES = {"official_calendar", "official_market_daily", "finmind_stock_info", "finmind_price_adj"}
MARKETS = {"TWSE", "TPEx"}
_COMPACT_FIELDS = {"source_unit_domain_version", "necessary_keys_basis"}
_COMPACT_VERSION = "compact-necessary-keys-v1"
_BASIS_SCHEMA = "twstock-updater.necessary-keys-basis.v1"
_SELECTOR_SCHEMA = "twstock-updater.necessary-keys-selector.v1"
_OFFICIAL_HISTORY_MEMBERSHIP = "current_stock_ids_any_supported_market"


def _require(value, reason):
    if not value:
        raise ValueError(reason)


def _note(labels, code, *, recorded=None, actual=None, detail=None):
    """A version/declaration difference: recorded (``version_labels``), never raised."""
    if labels is None:
        return
    item = json.loads(json.dumps({"code": code, "recorded": recorded, "actual": actual, "detail": detail},
                                 ensure_ascii=False, default=str))
    if item not in labels:
        labels.append(item)


def _digest(value):
    return hashlib.sha256(json.dumps(value, ensure_ascii=False, sort_keys=True,
        separators=(",", ":"), allow_nan=False).encode("utf-8")).hexdigest()


def _sha(value):
    _require(isinstance(value, str) and re.fullmatch("[0-9a-f]{64}", value), "application_scope_digest_invalid")
    return value


def _day(value):
    _require(isinstance(value, str) and date.fromisoformat(value).isoformat() == value,
        "application_scope_date_invalid")
    return value


def _sessions(values, *, count=None):
    _require(isinstance(values, list), "application_scope_sessions_unknown")
    result = [_day(value) for value in values]
    _require(result == sorted(set(result)) and (count is None or len(result) == count),
        "application_scope_sessions_invalid")
    return result


def _ref(value):
    _require(isinstance(value, Mapping) and value.get("physical_state") != "retired",
        "application_scope_live_reference_missing")
    _sha(value.get("sha256"))
    _require(isinstance(value.get("ref", value.get("path")), str)
        and type(value.get("size")) is int and value["size"] >= 0, "application_scope_live_reference_invalid")


def _pairs(values):
    _require(isinstance(values, list) and bool(values), "application_scope_ordinary_pairs_unknown")
    result = []
    for value in values:
        _require(isinstance(value, Mapping) and value.get("market") in MARKETS
            and isinstance(value.get("stock_id"), str) and value["stock_id"], "application_scope_ordinary_pair_invalid")
        result.append((value["stock_id"], value["market"]))
    _require(len(set(result)) == len(result), "application_scope_duplicate_pair")
    return set(result)


def validate_application_retention_scope(value, labels=None):
    """Validate the scope's data grammar; producer declarations and versions become labels.

    Kept (raised): schema/profile, the document seal, 253-session axes, refs.
    Labelled (``labels`` list): revision, a non-recomputable scope identity, a
    producer-declared blocked/unknown certainty with its missing proofs, route
    inventory differences and retired-evidence shape.
    """
    _require(isinstance(value, Mapping), "application_retention_scope_missing")
    body = copy.deepcopy(dict(value))
    _require(body.get("schema") == "twstock.application-retention-scope.v1" and body.get("schema_version") == 1
        and body.get("profile_id") == "C0", "application_retention_scope_schema_invalid")
    _require(body.get("document_sha256") == _digest({key: child for key, child in body.items() if key != "document_sha256"}),
        "application_retention_scope_document_digest_mismatch")
    if "purpose_resolution" in body:
        purpose = body["purpose_resolution"]
        _require(isinstance(purpose, Mapping)
            and purpose.get("schema") == "twstock.model-purpose-resolution.v2"
            and purpose.get("schema_version") == 2 and purpose.get("purpose_id") == "preopen_training_history"
            and purpose.get("asof") == body.get("asof"), "application_model_purpose_schema_invalid")
    compact = _compact_scope_mode(body)
    if compact:
        _validate_compact_necessary_keys(_retention_capture_context(body), body["raw_unit_exceptions"], labels)
    projected = retention_scope_domain_projection(body, labels)
    identity_matches = body.get("scope_id") == _digest(projected)
    legacy_format = (not compact and not any(name in body["ordinary_universe_ref"] for name in
        ("source_asof", "verified_asof", "observed_date", "observed_at")))
    if legacy_format and not identity_matches:
        # The previous noncompact v1 grammar did not project the proof clock.
        # Its exact sealed digest remains accepted; compact has no fallback.
        legacy = dict(projected)
        legacy.pop("identity_snapshot_cutoff")
        identity_matches = body.get("scope_id") == _digest(legacy)
    if _policy_version(body) == 2:
        _require(identity_matches, 'application_policy2_retention_identity_mismatch')
    if not identity_matches:
        # Tampering is caught by the document seal above; a domain projection this
        # consumer cannot recompute is a producer/consumer version difference.
        _note(labels, "scope_identity_not_recomputable", recorded=body.get("scope_id"), actual=_digest(projected),
              detail={"document": "application_retention_scope"})
    _sha(body["capture_scope_id"])
    asof = _day(body["asof"])
    if body.get("scope_certainty") != "known" or body.get("missing_proofs"):
        _note(labels, "scope_declared_blocked", recorded="known", actual=body.get("scope_certainty"),
              detail={"missing_proofs": _scope_codes(body.get("missing_proofs") or [])})
    _require(set(body.get("markets") or {}) == MARKETS, "application_retention_scope_inventory_mismatch")
    routes = set(body.get("route_rules") or {})
    # Plan 2.2: the route set must at least contain the routes the analysis reads
    # (calendar, board, identity, price); optional/extra routes are labels.
    _require(_CORE_ROUTES.issubset(routes), "application_retention_scope_inventory_mismatch")
    if routes != ROUTES:
        _note(labels, "scope_route_inventory_differs", recorded=sorted(ROUTES), actual=sorted(routes))
    _pairs(body.get("allowed_stock_market_pairs"))
    _ref(body.get("ordinary_universe_ref"))
    _ref(body.get("requirements_ref"))
    for market, values in body["markets"].items():
        history = _sessions(values.get("historical_sessions"), count=None if "data_sessions" in values else 253)
        if "data_sessions" in values:
            # 18-month acquisition axis: analysis remains the last 253 sessions.
            _require(len(history) >= 253 and values["data_sessions"] == history
                and _day(values.get("data_start")) <= history[0] and _day(values.get("data_end")) >= asof,
                "application_retention_scope_axis_invalid")
        future = _sessions(values.get("future_sessions"))
        _require(history[-1] <= asof and len(future) <= 63 and all(day > asof for day in future)
            and values.get("historical_session_sha256") == _digest(history), "application_retention_scope_axis_invalid")
        _ref(values.get("calendar_ref"))
        if future:
            _ref(values.get("future_calendar_ref"))
        else:
            _require(values.get("future_calendar_ref") is None, "application_retention_scope_empty_future_reference")
    for retired in body.get("historical_observations", []):
        if not (isinstance(retired, Mapping) and retired.get("physical_state") == "retired"
                and retired.get("reverification_available") is False):
            # Retired evidence is audit metadata only; it is never read as proof.
            _note(labels, "scope_retired_evidence_shape_differs",
                  detail={"physical_state": retired.get("physical_state") if isinstance(retired, Mapping) else None})
    return body


def validate_capture_scope_pair(scope, capture, labels=None):
    """Pair the capture scope with its retention scope.

    Kept (raised): schema/profile, the capture document seal.  Labelled: a
    non-bounded status, identity not recomputable here, and universe/refs/
    selectors/axis/revision differences (the retention scope is analysed).
    """
    _require(isinstance(capture, Mapping) and capture.get("schema") == "twstock.application-capture-scope.v1"
        and capture.get("schema_version") == 1 and capture.get("profile_id") == "C0", "application_capture_scope_not_bounded")
    if capture.get("status") != "bounded":
        _note(labels, "capture_scope_not_bounded", recorded="bounded", actual=capture.get("status"),
              detail={"missing_proofs": _scope_codes(capture.get("missing_proofs") or [])
                      if isinstance(capture.get("missing_proofs"), list) else None})
    if _compact_scope_mode(capture):
        _validate_compact_necessary_keys(capture, capture.get("source_unit_exceptions"), labels)
    try:
        computed = scope_content_digest(capture)
    except (ValueError, KeyError, TypeError) as exc:
        computed = None
        _note(labels, "scope_identity_not_recomputable", recorded=capture.get("scope_id"),
              detail={"document": "application_capture_scope", "reason": str(exc)})
    _require(capture.get("document_content_sha256") == capture_document_digest(capture),
        "application_capture_document_digest_mismatch")
    if _policy_version(capture) == 2:
        _require(computed == capture.get('scope_id'), 'application_policy2_capture_identity_mismatch')
        _require(_policy_version(scope) == 2 and _policy2_domain(capture['current_demand'])
                 == _policy2_domain(scope['current_demand']), 'application_policy2_capture_retention_mismatch')
    if computed is not None and capture.get("scope_id") != computed:
        _note(labels, "scope_identity_not_recomputable", recorded=capture.get("scope_id"), actual=computed,
              detail={"document": "application_capture_scope"})
    if capture.get("scope_id") != scope["capture_scope_id"] or capture.get("asof") != scope["asof"]:
        _note(labels, "capture_retention_identity_differs",
              recorded={"scope_id": scope["capture_scope_id"], "asof": scope["asof"]},
              actual={"scope_id": capture.get("scope_id"), "asof": capture.get("asof")})
    try:
        same_pairs = _pairs(capture.get("allowed_stock_market_pairs")) == _pairs(scope["allowed_stock_market_pairs"])
    except ValueError as exc:
        same_pairs = False
        _note(labels, "capture_retention_universe_differs", detail={"reason": str(exc)})
    if not same_pairs:
        _note(labels, "capture_retention_universe_differs", detail={"analysed": "retention_scope_pairs"})
    if (capture.get("requirements_ref") != scope.get("requirements_ref")
            or capture.get("ordinary_universe_ref") != scope.get("ordinary_universe_ref")):
        _note(labels, "capture_retention_refs_differ")
    if capture.get("forward_settlement", []) != scope.get("forward_settlement", []):
        _note(labels, "capture_retention_forward_selectors_differ")
    if capture.get("purpose_resolution") != scope.get("purpose_resolution"):
        _note(labels, "capture_retention_model_purpose_differs")
    windows = capture.get("market_windows")
    if not (isinstance(windows, Mapping) and set(windows) == MARKETS):
        _note(labels, "capture_market_windows_missing", actual=sorted(windows) if isinstance(windows, Mapping) else None)
        windows = {}
    for market in sorted(set(windows) & MARKETS):
        declared, adopted = windows[market], scope["markets"][market]
        analysis = adopted["historical_sessions"][-253:]
        if not (isinstance(declared, Mapping) and declared.get("sessions") == analysis
                and declared.get("future_sessions") == adopted["future_sessions"]
                and declared.get("start") == analysis[0]
                and declared.get("end") == analysis[-1]
                and all(declared.get(name) == adopted[name] for name in ("data_start", "data_end", "data_sessions")
                        if "data_sessions" in adopted)):
            _note(labels, "capture_retention_axis_differs", detail={"market": market,
                  "analysed": "retention_scope_axis"})
    if capture.get("revision") != scope.get("revision") or capture.get("scope_version") != "application_capture_scope_v1":
        _note(labels, "capture_revision_differs", recorded={"revision": scope.get("revision"),
              "scope_version": "application_capture_scope_v1"},
              actual={"revision": capture.get("revision"), "scope_version": capture.get("scope_version")})
    return copy.deepcopy(dict(capture))


def _json_copy(value):
    return json.loads(json.dumps(value, ensure_ascii=False, allow_nan=False))


def _instant(value):
    _require(isinstance(value, str), "application_scope_timestamp_invalid")
    parsed = datetime.fromisoformat(value)
    _require(parsed.tzinfo is not None, "application_scope_timestamp_timezone_missing")
    return parsed.astimezone(timezone.utc)


def _request_parts(url):
    _require(isinstance(url, str) and url and all(ord(char) >= 33 for char in url), "application_scope_url_invalid")
    parts = urlsplit(url)
    _require(parts.scheme == "https" and parts.hostname and not parts.username and not parts.password
        and not parts.fragment and parts.port in {None, 443}, "application_scope_url_invalid")
    pairs = parse_qsl(parts.query, keep_blank_values=True, strict_parsing=True)
    query = dict(pairs)
    _require(len(query) == len(pairs), "application_scope_duplicate_query")
    _require(not any(key.casefold() in {"token", "api_key", "apikey", "authorization", "password"}
        for key in query), "application_scope_credentials_in_url")
    return parts.hostname.lower(), parts.path, tuple(sorted(query.items()))


def _canonical_bytes(value):
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(',', ':'), allow_nan=False).encode('utf-8')


def _scope_fail(reason):
    raise ValueError(reason)


def _scope_codes(values):
    fields = ("reason", "reason_code", "code", "proof", "state", "field", "route_id",
              "order", "market", "stock_id", "consumer_id", "record_id")
    result = []
    for item in values:
        if not isinstance(item, dict):
            _scope_fail("retention_missing_scope_proof_invalid")
        selected = {name: _json_copy(item[name]) for name in fields if name in item}
        result.append(selected or {"reason": "scope_proof_missing"})
    return sorted(result, key=_canonical_bytes)


def _demand_selector(value):
    if isinstance(value, dict):
        return {key: _demand_selector(child) for key, child in value.items()
                if not key.endswith("_ref") and key not in {"ref", "sha256", "size", "byte_size", "path",
                    "document_sha256", "document_content_sha256", "scope_id"}}
    if isinstance(value, list):
        return [_demand_selector(child) for child in value]
    return value


def _compact_scope_mode(body):
    present = _COMPACT_FIELDS.intersection(body)
    if not present:
        return False
    _require(present == _COMPACT_FIELDS and body.get("source_unit_domain_version") == _COMPACT_VERSION,
             "application_scope_compact_domain_invalid")
    return True


def _basis_ref_identity(value):
    outer = value
    if isinstance(value, Mapping) and isinstance(value.get("ref"), Mapping):
        value = value["ref"]
    _require(isinstance(value, Mapping) and value.get("physical_state") != "retired",
             "application_scope_basis_ref_invalid")
    _sha(value.get("sha256"))
    size = value.get("size", value.get("byte_size"))
    _require(type(size) is int and size > 0 and ("size" not in value or "byte_size" not in value
             or value["size"] == value["byte_size"]), "application_scope_basis_ref_invalid")
    clocks = {}
    for source in (value, outer):
        if not isinstance(source, Mapping):
            continue
        for name in ("source_asof", "verified_asof", "observed_date", "observed_at", "historical", "effective_date"):
            if name not in source:
                continue
            raw = source[name]
            if raw is None:
                clock = None
            elif name == "observed_at":
                clock = _instant(raw).isoformat()
            elif name == "historical":
                _require(type(raw) is bool, "application_scope_basis_ref_clock_invalid")
                clock = raw
            else:
                clock = _day(raw)
            _require(name not in clocks or clocks[name] == clock, "application_scope_basis_ref_clock_conflict")
            clocks[name] = clock
    return value["sha256"], size, clocks


def necessary_keys_basis_domain(basis):
    basis = _json_copy(basis)
    fields = {"schema", "schema_version", "positive_ordinary", "ordinary_universe_ref",
              "ordered_pairs", "market_axes", "basis_sha256"}
    _require(isinstance(basis, Mapping) and set(basis) == fields
             and basis["schema"] == _BASIS_SCHEMA and type(basis["schema_version"]) is int
             and basis["schema_version"] == 1 and basis["positive_ordinary"] is True,
             "application_scope_basis_schema_invalid")
    _basis_ref_identity(basis["ordinary_universe_ref"])
    pairs = basis["ordered_pairs"]
    _pairs(pairs)
    _require(all(set(pair) == {"stock_id", "market"} for pair in pairs), "application_scope_basis_pairs_invalid")
    axes = basis["market_axes"]
    _require(isinstance(axes, Mapping) and set(axes) == MARKETS, "application_scope_basis_axes_invalid")
    axis_domain = {}
    for market, axis in axes.items():
        data_fields = {"data_start", "data_end", "data_sessions_sha256"}
        present = data_fields.intersection(axis) if isinstance(axis, Mapping) else set()
        _require(not present or present == data_fields, "application_scope_basis_axis_invalid")
        _require(isinstance(axis, Mapping) and set(axis) == {
            "asof", "start", "end", "exact_window_sessions", "sessions_sha256", "future_sessions_sha256",
            "calendar_ref", "future_calendar_ref"} | present, "application_scope_basis_axis_invalid")
        if present:
            _require(_day(axis["data_start"]) <= _day(axis["start"]) <= _day(axis["end"]) <= _day(axis["data_end"]),
                     "application_scope_basis_axis_bounds_invalid")
            _sha(axis["data_sessions_sha256"])
        _require(type(axis["exact_window_sessions"]) is int and axis["exact_window_sessions"] == 253
                 and _day(axis["start"]) <= _day(axis["end"]) == _day(axis["asof"]),
                 "application_scope_basis_axis_bounds_invalid")
        _basis_ref_identity(axis["calendar_ref"])
        if axis["future_calendar_ref"] is not None:
            _basis_ref_identity(axis["future_calendar_ref"])
        _sha(axis["sessions_sha256"])
        _sha(axis["future_sessions_sha256"])
        axis_domain[market] = {key: child for key, child in axis.items() if not key.endswith("_ref")}
    domain = {"schema": basis["schema"], "schema_version": 1, "positive_ordinary": True,
              "ordered_pairs": pairs, "market_axes": axis_domain}
    _require(basis["basis_sha256"] == _digest(domain), "application_scope_basis_digest_mismatch")
    return domain


def necessary_keys_domain(declaration):
    value = _json_copy(declaration)
    if isinstance(value, list):
        _require(bool(value) and all(isinstance(key, Mapping) for key in value), "application_scope_exception_key_invalid")
        return value
    _require(isinstance(value, Mapping), "application_scope_selector_invalid")
    kind = value.get("kind")
    fields = {"schema", "schema_version", "kind", "basis_sha256", "markets", "expanded_count", "expanded_sha256"}
    if kind in {"ordinary_day", "ordinary_stock_day"}:
        fields.add("date")
        _day(value.get("date"))
    _require(set(value) == fields and value.get("schema") == _SELECTOR_SCHEMA
             and type(value.get("schema_version")) is int and value["schema_version"] == 1
             and kind in {"ordinary_day", "ordinary_stock_day", "ordinary_identity"}, "application_scope_selector_schema_invalid")
    markets = value["markets"]
    _require(isinstance(markets, list) and bool(markets) and len(markets) == len(set(markets))
             and all(market in MARKETS for market in markets), "application_scope_selector_markets_invalid")
    _require(type(value["expanded_count"]) is int and value["expanded_count"] > 0,
             "application_scope_selector_count_invalid")
    _sha(value["basis_sha256"])
    _sha(value["expanded_sha256"])
    return value


def _retention_capture_context(body):
    windows = {}
    for market, item in body["markets"].items():
        historical, future = item["historical_sessions"], item["future_sessions"]
        analysis = historical[-253:] if "data_sessions" in item else historical
        windows[market] = {"sessions": analysis, "future_sessions": future,
            "start": analysis[0], "end": analysis[-1], "asof": analysis[-1],
            "exact_window_sessions": 253, "calendar_ref": item["calendar_ref"],
            "future_calendar_ref": item["future_calendar_ref"]}
        if "data_sessions" in item:
            windows[market].update({name: item[name] for name in ("data_start", "data_end", "data_sessions")})
    return {"ordinary_universe_ref": body["ordinary_universe_ref"],
        "allowed_stock_market_pairs": body["allowed_stock_market_pairs"], "market_windows": windows,
        "route_categories": {route: {"capture_event_dates": item["event_dates"],
            **({"history_membership": item["history_membership"]} if "history_membership" in item else {})}
            for route, item in body["route_rules"].items()},
        "source_unit_domain_version": body["source_unit_domain_version"],
        "necessary_keys_basis": body["necessary_keys_basis"]}


def _validate_compact_necessary_keys(scope, declarations, labels=None):
    """Compact acquisition selectors are producer bookkeeping: differences are labels.

    Kept (raised): the basis grammar/digest, its pair list versus the scope's
    allowed pairs, and its 253-session axis consistency with the scope windows.
    """
    if not _compact_scope_mode(scope):
        _note(labels, "scope_compact_basis_missing")
        return
    basis = scope["necessary_keys_basis"]
    necessary_keys_basis_domain(basis)
    _require(_pairs(basis["ordered_pairs"]) == _pairs(scope.get("allowed_stock_market_pairs")),
             "application_scope_basis_pair_domain_changed")
    if _basis_ref_identity(basis["ordinary_universe_ref"]) != _basis_ref_identity(scope.get("ordinary_universe_ref")):
        _note(labels, "scope_selector_basis_differs", detail={"field": "ordinary_universe_ref"})
    windows = scope.get("market_windows")
    if not (isinstance(windows, Mapping) and set(windows) == MARKETS):
        _note(labels, "scope_selector_basis_differs", detail={"field": "market_windows"})
        windows = {}
    for market in sorted(set(windows) & MARKETS):
        axis, window = basis["market_axes"][market], windows[market]
        history, future = _sessions(window["sessions"], count=253), _sessions(window["future_sessions"])
        _require(len(future) <= 63 and all(day > history[-1] for day in future)
                 and axis["sessions_sha256"] == _digest(history) and axis["future_sessions_sha256"] == _digest(future)
                 and all(axis[key] == window[key] for key in ("asof", "start", "end", "exact_window_sessions")),
                 "application_scope_basis_axis_changed")
        has_data = "data_sessions" in window
        if ("data_sessions_sha256" in axis) != has_data or has_data and not (
                axis["data_start"] == window.get("data_start") and axis["data_end"] == window.get("data_end")
                and axis["data_sessions_sha256"] == _digest(window["data_sessions"])):
            _note(labels, "scope_selector_basis_differs", detail={"field": "data_axis", "market": market})
        for key in ("calendar_ref", "future_calendar_ref"):
            expected, actual = axis[key], window.get(key)
            if not ((expected is None and actual is None) or expected is not None and actual is not None
                    and _basis_ref_identity(expected) == _basis_ref_identity(actual)):
                _note(labels, "scope_selector_basis_differs", detail={"field": key, "market": market})
    if not isinstance(declarations, list):
        _note(labels, "scope_selector_declarations_unreadable")
        return
    pairs = basis["ordered_pairs"]
    expanded, found = {}, {}
    stock_ids = None
    for exception in declarations:
        declaration = exception.get("necessary_keys") if isinstance(exception, Mapping) else None
        if isinstance(declaration, list):
            necessary_keys_domain(declaration)
            continue
        selector = necessary_keys_domain(declaration)
        problems = []
        if selector["basis_sha256"] != basis["basis_sha256"]:
            problems.append("selector_basis_changed")
        native, route = exception.get("unit_scope") or {}, exception.get("route_id")
        if exception.get("purpose", "current") != "current":
            problems.append("selector_forward_forbidden")
        if selector["kind"] == "ordinary_identity":
            expected_markets = [native.get("market")] if native.get("market") is not None else ["TWSE", "TPEx"]
            if not (route == "finmind_stock_info" and exception.get("unit_kind") == "identity_snapshot"
                    and selector["markets"] == expected_markets):
                problems.append("selector_unit_mismatch")
        else:
            if not (route in {"official_market_daily", "finmind_price_adj", "finmind_dividend", "finmind_dividend_result"}
                    and exception.get("unit_kind") in {"market_day", "finmind_market_day"}
                    and native.get("date") == selector["date"]):
                problems.append("selector_unit_mismatch")
            if native.get("market") is not None and selector["markets"] != [native["market"]]:
                problems.append("selector_unit_mismatch")
            if route in {"official_market_daily", "finmind_price_adj"} and not all(
                    selector["date"] in (windows.get(market) or {}).get(
                        "data_sessions", (windows.get(market) or {}).get("sessions", [])) for market in selector["markets"]):
                problems.append("selector_day_outside_axis")
            if route in {"finmind_dividend", "finmind_dividend_result"} and selector["date"] not in (
                    ((scope.get("route_categories") or {}).get(route) or {}).get("capture_event_dates") or []):
                problems.append("selector_event_day_unproved")
        if selector["kind"] == "ordinary_stock_day" and not (route == "official_market_daily"
                and ((scope.get("route_categories") or {}).get(route) or {}).get("history_membership")
                == _OFFICIAL_HISTORY_MEMBERSHIP):
            problems.append("selector_history_membership_mismatch")
        key = (selector["kind"], tuple(selector["markets"]), selector.get("date"))
        if key not in expanded:
            if selector["kind"] == "ordinary_identity":
                values = [dict(pair) for pair in pairs if pair["market"] in selector["markets"]]
            elif selector["kind"] == "ordinary_stock_day":
                if stock_ids is None:
                    stock_ids = sorted({pair["stock_id"] for pair in pairs})
                values = [dict(stock_id=stock, market=market, date=selector["date"])
                          for market in selector["markets"] for stock in stock_ids]
            else:
                values = [dict(pair, date=selector["date"]) for market in selector["markets"]
                          for pair in pairs if pair["market"] == market]
            expanded[key] = (len(values), _digest(values))
        if expanded[key] != (selector["expanded_count"], selector["expanded_sha256"]):
            problems.append("selector_expansion_changed")
        for problem in set(problems):
            found.setdefault(problem, []).append(exception.get("unit_key"))
    for problem, keys in sorted(found.items()):
        _note(labels, "scope_" + problem, actual=len(keys), detail={"unit_keys": sorted(map(str, keys))[:20]})


def _identity_snapshot_cutoff(scope, labels=None):
    """The identity proof day; an inconsistent proof clock is labelled and yields None.

    The consumer re-verifies the identity sidecar itself (contract
    load_current_ordinary_identity); this projected cutoff is producer bookkeeping.
    """
    claim = scope["ordinary_universe_ref"]
    fields = ("source_asof", "verified_asof", "observed_date", "observed_at")
    if not any(name in claim for name in fields):
        return scope["asof"]
    try:
        _require(claim.get("schema") == "tw-stock-current-ordinary-identity-v1"
                 and claim.get("historical") is False and claim.get("effective_date") is None
                 and all(isinstance(claim.get(name), str) for name in fields), "application_scope_identity_proof_clock_invalid")
        day = _day(claim["source_asof"])
        _require(day == _day(claim["verified_asof"]) == _day(claim["observed_date"]),
                 "application_scope_identity_proof_clock_invalid")
        observed_day = _instant(claim["observed_at"]).astimezone(timezone(timedelta(hours=8))).date().isoformat()
        _require(observed_day == day, "application_scope_identity_proof_clock_invalid")
    except ValueError as exc:
        _note(labels, "scope_identity_proof_clock_differs", detail={"reason": str(exc),
              "clock": {name: claim.get(name) for name in fields}})
        return None
    return day


def _policy2_domain(value):
    body = _json_copy(value)
    body.pop('observations', None)
    tdcc = body.get('tdcc', {})
    tdcc.pop('object_ref', None)
    tdcc.pop('input_observation', None)
    return body


def _policy_version(value):
    version = value.get('producer_window_policy_version', 1)
    _require(type(version) is int and version in (1, 2), 'producer_window_policy_version_invalid')
    if version == 2:
        demand = value.get('current_demand')
        _require(isinstance(demand, Mapping) and demand.get('schema') == 'twstock.current-consumer-demand.v2'
                 and demand.get('schema_version') == 2 and demand.get('sector_days') == 90
                 and demand.get('news_days') == 30 and demand.get('sector_yoy_native_lag') == 12,
                 'application_policy2_current_demand_invalid')
    else:
        _require('current_demand' not in value, 'application_policy2_version_missing')
    return version


def retention_scope_domain_projection(value, labels=None):
    """The demand identity excludes immutable wrappers and publication audits.

    Capture's domain identity already seals activated consumers, requirements,
    budgets and forward eligibility. This explicit retention projection binds
    its exact selectors while allowing replacement evidence to be written
    before the final calendar and scope documents are sealed.
    """
    body = _json_copy(value)
    compact = _compact_scope_mode(body)
    if not compact:
        _require(all(isinstance(item.get("necessary_keys"), list) for item in body["raw_unit_exceptions"]),
                 "application_scope_compact_domain_missing")
    markets = {}
    for market, item in body["markets"].items():
        markets[market] = {"historical_session_sha256": _digest(item["historical_sessions"]),
                           "future_session_sha256": _digest(item["future_sessions"])}
        if "data_sessions" in item:
            markets[market].update(data_start=item["data_start"], data_end=item["data_end"],
                                   data_session_sha256=_digest(item["data_sessions"]))
    rules = {}
    for route, item in body["route_rules"].items():
        rules[route] = {"capture_allowed": item["capture_allowed"],
            "event_dates": sorted(item["event_dates"]),
            "basis_anchor_keys": sorted(_demand_selector(item["basis_anchor_keys"]), key=_canonical_bytes),
            "missing_scope_codes": _scope_codes(item["missing_scope_proofs"])}
        for name in ("retention_window", "retention_reason"):
            if name in item:
                rules[route][name] = _json_copy(item[name])
    forward_fields = ("record_id", "stock_id", "market", "target_date", "purpose", "expires_at",
        "profile", "signal_scalar_stored", "original_ordinary", "record_kind", "recorded_at",
        "signal_date", "horizon", "not_before_session_date", "signal_close", "forecast_identity",
        "market_expected_asof", "forward_eligible", "target_mature")
    forward = []
    for item in body["forward_settlement"]:
        record = {name: _demand_selector(item[name]) for name in forward_fields if name in item}
        record["grace_session_sha256"] = _digest(item.get("grace_sessions", []))
        forward.append(record)
    exception_fields = ("route_id", "unit_key", "unit_kind", "unit_scope", "request_url",
        "max_response_bytes", "valid_until", "expires_at", "purpose", "reason", "proof_need", "necessary_keys",
        "minimal_source_unit")
    exceptions = [{name: _demand_selector(item[name]) for name in exception_fields if name in item}
                  for item in body["raw_unit_exceptions"]]
    if compact:
        for declaration, projected in zip(body["raw_unit_exceptions"], exceptions):
            projected["necessary_keys"] = necessary_keys_domain(declaration["necessary_keys"])
    consumer_fields = ("consumer_id", "entry_id", "profile", "purpose")
    consumers = [{name: _json_copy(item[name]) for name in consumer_fields if name in item}
                 for item in body["active_consumers"]]
    projected = {"schema": body["schema"], "schema_version": body["schema_version"],
        "revision": body["revision"], "asof": body["asof"], "profile_id": body["profile_id"],
        "capture_scope_id": body["capture_scope_id"], "scope_certainty": body["scope_certainty"],
        "active_consumers": sorted(consumers, key=_canonical_bytes),
        "allowed_stock_market_pairs": sorted(_json_copy(body["allowed_stock_market_pairs"]), key=_canonical_bytes),
        "markets": markets, "route_rules": rules,
        "identity_snapshot_cutoff": _identity_snapshot_cutoff(body, labels) if body["scope_certainty"] == "known" else None,
        "forward_settlement": sorted(forward, key=_canonical_bytes),
        "raw_unit_exceptions": sorted(exceptions, key=_canonical_bytes),
        "missing_scope_codes": _scope_codes(body["missing_proofs"])}
    if "retention_mode" in body:
        projected["retention_mode"] = body["retention_mode"]
    if "history_retention_start" in body:
        projected["history_retention_start"] = _day(body["history_retention_start"])
    if _policy_version(body) == 2:
        projected.update(producer_window_policy_version=2,
                         current_demand=_policy2_domain(body['current_demand']))
    if "purpose_resolution" in body:
        projected["purpose_resolution"] = _json_copy(body["purpose_resolution"])
    if compact:
        projected["source_unit_domain_version"] = body["source_unit_domain_version"]
        projected["necessary_keys_basis"] = necessary_keys_basis_domain(body["necessary_keys_basis"])
    return projected


def retention_scope_content_digest(value):
    return _digest(retention_scope_domain_projection(value))


def _selector_domain(value):
    if isinstance(value, Mapping):
        return {key: _selector_domain(item) for key, item in value.items()
                if not key.endswith("_ref") and key not in {"sha256", "size", "byte_size", "path", "document_content_sha256", "scope_id"}}
    if isinstance(value, list):
        return [_selector_domain(item) for item in value]
    return value


def scope_domain_projection(scope: Mapping) -> dict:
    """The acquisition domain; physical wrappers remain in the document seal."""
    windows = {}
    for market, window in scope.get("market_windows", {}).items():
        windows[market] = {
            "asof": window.get("asof"), "exact_window_sessions": window.get("exact_window_sessions"),
            "ordered_sessions_sha256": _digest(window.get("sessions", [])),
            "ordered_future_sessions_sha256": _digest(window.get("future_sessions", [])),
        }
        if "data_sessions" in window:
            windows[market].update(data_start=window.get("data_start"), data_end=window.get("data_end"),
                                   ordered_data_sessions_sha256=_digest(window["data_sessions"]))
    consumers = [{key: item.get(key) for key in ("consumer_id", "entry_id", "profile", "purpose")}
                 for item in scope.get("active_consumers", [])]
    categories = {route: {key: _selector_domain(item.get(key)) for key in ("category", "capture_allowed", "event_dates", "capture_event_dates", "basis_anchor_keys")}
                  for route, item in scope.get("route_categories", {}).items()}
    history = scope.get("route_categories", {}).get("official_market_daily", {})
    if "history_membership" in history:
        categories["official_market_daily"]["history_membership"] = history["history_membership"]
    exceptions = []
    for item in scope.get("source_unit_exceptions", []):
        host, path, query = _request_parts(item["request_url"])
        domain = {key: _selector_domain(item.get(key)) for key in ("route_id", "unit_key", "unit_kind", "unit_scope",
                                                 "necessary_keys", "minimal_source_unit", "max_response_bytes")}
        domain["purpose"] = item.get("purpose", "current")
        domain["request_identity"] = {"host": host, "path": path, "query": dict(query)}
        domain["expiry"] = _instant(item["expires_at"]).isoformat() if item.get("expires_at") else item.get("valid_until")
        if isinstance(item.get("necessary_keys"), Mapping):
            _require(scope.get("source_unit_domain_version") == _COMPACT_VERSION,
                     "application_scope_compact_domain_invalid")
            domain["necessary_keys"] = necessary_keys_domain(item["necessary_keys"])
        if item.get("reason") == "calendar_scope_advance":
            need = item.get("proof_need", {})
            domain["calendar_scope_advance"] = {key: need.get(key) for key in ("market", "expected_asof", "allowed_months")}
            for key in ("data_start", "data_end", "requested_from_date"):
                if key in need:
                    domain["calendar_scope_advance"][key] = need[key]
        elif item.get("reason") == "calendar_closed_day_advance":
            domain["calendar_closed_day_advance"] = {key: _json_copy(value) for key, value in item.get("proof_need", {}).items()
                                                     if key != "expected_asof_ref"}
        exceptions.append(domain)
    forwards = [{key: item.get(key) for key in ("record_id", "stock_id", "market", "purpose", "profile",
                                               "record_kind", "recorded_at", "target_date", "expires_at",
                                               "grace_sessions", "market_expected_asof", "signal_date", "horizon",
                                               "not_before_session_date", "signal_close", "forecast_identity")}
                for item in scope.get("forward_settlement", [])]
    basis = scope.get("budget_basis") or {}
    compact = {}
    if _compact_scope_mode(scope):
        compact = {"source_unit_domain_version": _COMPACT_VERSION,
                   "necessary_keys_basis": necessary_keys_basis_domain(scope["necessary_keys_basis"])}
    return {
        **compact,
        **({'producer_window_policy_version': 2, 'current_demand': _policy2_domain(scope['current_demand'])}
           if _policy_version(scope) == 2 else {}),
        **({"producer_route_windows": _json_copy(scope["producer_route_windows"])}
           if "producer_route_windows" in scope else {}),
        **({"purpose_resolution": _json_copy(scope["purpose_resolution"])}
           if "purpose_resolution" in scope else {}),
        **({"requested_from_date": _day(scope["requested_from_date"])}
           if "requested_from_date" in scope else {}),
        **({"history_retention_start": _day(scope["history_retention_start"])}
           if "history_retention_start" in scope else {}),
        "schema": scope.get("schema"), "schema_version": scope.get("schema_version"), "revision": scope.get("revision"),
        "scope_version": scope.get("scope_version"), "profile_id": scope.get("profile_id"), "purpose": "current",
        "status": scope.get("status"), "asof": scope.get("asof"),
        "effective_c0_requirements": _json_copy(scope.get("effective_c0_requirements")),
        "active_consumers": sorted(consumers, key=lambda item: (item["consumer_id"], item["entry_id"])),
        "allowed_route_ids": scope.get("allowed_route_ids"), "route_categories": categories,
        "allowed_stock_market_pairs": _json_copy(scope.get("allowed_stock_market_pairs", [])),
        "market_windows": windows,
        "source_units": sorted(exceptions, key=lambda item: (item["route_id"], item["unit_key"])),
        "forward_settlement": sorted(forwards, key=lambda item: item["record_id"]),
        "acquisition_unit_keys": sorted(basis.get("acquisition_unit_keys", [])),
        "acquisition_units": sorted((_selector_domain({key: value for key, value in unit.items()
                                                     if key not in {"evaluation_time", "request_url"}})
                                     for unit in basis.get("acquisition_units", [])),
                                    key=lambda item: (item.get("route_id", ""), item.get("unit_key", ""))),
        "hard_budgets": _json_copy(scope.get("hard_budgets")),
        "missing_proof_codes": sorted((str(item.get("code")), str(item.get("field", ""))) for item in scope.get("missing_proofs", [])),
    }


def scope_content_digest(scope: Mapping) -> str:
    return _digest(scope_domain_projection(scope))


def capture_document_digest(scope: Mapping) -> str:
    """Canonical content seal; the external ContentRef seals serialized bytes."""
    return _digest({key: value for key, value in scope.items() if key != "document_content_sha256"})


