"""Deterministic, in-memory report context and evidence-based Chinese commentary.

These are display facts, never model inputs. Each frame is visited once. The
caller may reuse the context for the same stock/asof across prediction horizons.
No files, clocks, services, source validation, or model code are used here.
"""

from __future__ import annotations

from collections import defaultdict
from collections.abc import Mapping
from datetime import date, datetime, time, timedelta, timezone
import math
from numbers import Integral
import re


_TAIPEI = timezone(timedelta(hours=8))
_KEYS = ("summary", "technical", "fundamentals", "flows", "context", "data_notes")
_MACRO = {
    "global_market_history", "finmind_exchange_rate",
    "finmind_government_bonds_yield", "finmind_crude_oil_prices", "finmind_gold_price",
}
_FINANCIAL = {
    "finmind_financial_statements", "finmind_balance_sheet", "finmind_cash_flows_statement",
}
_NEWS = {"news_and_hot_topics_raw"}
_INDUSTRY = {"finmind_industry_chain_flow", "official_sector_features", "official_sector_observations"}
_IDENTITY = ("stock_id", "stock", "security_code", "公司代號", "證券代號", "SecuritiesCompanyCode")
_TIME_FIELDS = ("available_at", "available_time", "available_at_utc", "source_publication_time", "published_at", "source_close_at")
_NON_VALUES = {
    *_IDENTITY, "date", "Date", "year", "month", "revenue_year", "revenue_month",
    "month_revenue_year", "month_revenue_month", "period_start", "period_end",
    "report_date", "source_asof", "classification_observed_date", "row_count",
    "stock_count", "level", "holding_level", "持股分級", "source_row_number",
    *_TIME_FIELDS, "entity_id", "data_id", "symbol", "indicator", "type", "field",
    "feature_name", "instrument", "name", "origin_name", "label", "label_zh", "name_zh",
    "route_id", "source_id", "source_route", "category", "usage", "availability", "status",
    "scope_id", "row_schema", "schema", "schema_version", "period", "period_id",
    "period_basis", "period_kind", "period_type", "currency", "market", "board",
    "stock_name", "company_name", "公司名稱", "證券名稱", "code", "Code", "StockCode", "StockNo",
    "CompanyName", "InternationalCode", "query", "provider", "attribution", "record_key", "raw_record_key",
    "sector", "sector_key", "sector_label", "industry", "industry_category", "source_industry",
    "unit", "units", "measure_unit", "value_unit", "quantity_unit", "volume_unit", "price_unit",
    "create_time", "created_at", "retrieved_at", "event_time", "observed_at",
    "source_size", "object_size", "source_row_count", "classification_effective_date",
    "AnnouncementDate", "AnnouncementTime", "CashDividendPaymentDate", "CashExDividendTradingDate",
    "StockExDividendTradingDate", "RecentlyDeclareDate",
}
_VALUE_NAMES = {
    "value", "close", "adjusted_close", "revenue", "cash_flow_value", "per", "pbr",
    "PER", "PBR", "volume", "foreign_investors_net", "investment_trust_net", "dealer_net",
}
_REASONS = {
    "available": "已取得本股或明確相關的觀測。",
    "partial_observations": "本股觀測存在缺值、衝突或已明示的缺期，保留可讀部分。",
    "missing_source": "本次未提供此來源的記憶體資料。",
    "no_matching_observations_unconfirmed": "篩選後沒有適格觀測，不能推定無事件。",
    "confirmed_no_event": "已有股票、日期與來源綁定的已驗證正向零事件證據。",
    "not_selected": "本次未選用，不視為資料缺失。",
    "not_applicable": "已有本股、日期與來源綁定的明確不適用證據。",
    "unmapped_fields": "部分觀測缺少可辨識的日期或數值欄位，未猜測欄位意義。",
    "unknown_unit": "部分數值單位未核實，不作跨來源換算或占比。",
    "unknown_publication_time": "部分觀測的具時區公開可用時點未核實，只作當期背景。",
    "insufficient_comparable_history": "部分序列沒有同口徑前值，不能判定其歷史變化。",
}


def _text(value):
    if value is None or isinstance(value, (Mapping, list, tuple)):
        return ""
    result = str(value).strip()
    return "" if result.lower() in {"nan", "nat", "<na>", "none", "null"} else result


def _number(value):
    if isinstance(value, bool) or type(value).__name__ == "bool_":
        return None
    try:
        result = float(value)
    except (TypeError, ValueError, OverflowError):
        return None
    if not math.isfinite(result):
        return None
    return int(value) if isinstance(value, Integral) else result


def _first(row, keys):
    return next((row[key] for key in keys if _text(row.get(key))), None)


def _day(value):
    text = _text(value)
    match = re.match(r"^(\d{4})[-/]?(\d{2})[-/]?(\d{2})(?:\D|$)", text)
    if not match:
        return None
    try:
        return date(*(int(part) for part in match.groups()))
    except ValueError:
        return None


def _timestamp(value):
    text = _text(value)
    if "T" not in text and " " not in text:
        return None
    try:
        parsed = datetime.fromisoformat(text.replace("Z", "+00:00"))
    except ValueError:
        return None
    return parsed.astimezone(_TAIPEI) if parsed.tzinfo is not None else None


def _record_day(row):
    result = _day(_first(row, ("date", "Date", "report_date", "period_end", "source_asof", "classification_observed_date")))
    if result is not None:
        return result
    year = _number(_first(row, ("revenue_year", "month_revenue_year")))
    month = _number(_first(row, ("revenue_month", "month_revenue_month")))
    if year is not None and month is not None:
        try:
            return date(int(year), int(month), 1)
        except ValueError:
            pass
    return None


def _market(value):
    text = _text(value)
    return {"上市": "TWSE", "上櫃": "TPEx", "TPEX": "TPEx", "興櫃": "Emerging"}.get(text, text)


def _single_stock_scope(manifest, stock_id):
    query = manifest.get("query_binding") or {}
    predicate = query.get("predicate") or query.get("cached_scope") or {}
    codes = predicate.get("stock_ids") or []
    return bool(codes) and {str(code) for code in codes} == {stock_id}


def _related(row, route, stock, manifest):
    code = str(stock["stock_id"])
    identity = _text(_first(row, _IDENTITY))
    if identity and identity != code:
        return False
    row_market = _market(row.get("market"))
    if row_market and row_market in {"TWSE", "TPEx", "Emerging"} and row_market != _market(stock.get("market")):
        return False
    entity = _text(row.get("entity_id"))
    if entity.startswith("stock:") and entity.split(":")[-1] != code:
        return False
    if identity or entity in {"stock:" + code, code}:
        return True
    if route == "official_sector_observations":
        # An explicit matching label is usable; never equate an unrelated sector key.
        industry = _text(stock.get("industry"))
        return (stock.get("classification_verified") is True and bool(industry)
            and any(_text(row.get(key)) == industry for key in ("sector_label", "industry_category", "source_industry")))
    if route in _MACRO or route == "official_calendar":
        return True
    industries = {_text(stock.get(key)) for key in ("industry", "industry_category", "sector", "sector_key", "sector_label")}
    industries.discard("")
    row_industries = {_text(row.get(key)) for key in ("industry", "industry_category", "sector", "sector_key", "sector_label")}
    if entity.startswith("sector:"):
        row_industries.add(entity.split(":", 1)[1])
    if route in _INDUSTRY:
        return bool(industries & row_industries)
    if route in _NEWS:
        title = _text(_first(row, ("title", "Title", "主旨")))
        names = {_text(stock.get(key)) for key in ("stock_name", "name", "company_name")}
        names.discard("")
        return bool(re.search(r"(?<![A-Za-z0-9])" + re.escape(code) + r"(?![A-Za-z0-9])", title)
                    or any(name in title for name in names)
                    or industries & row_industries
                    or any(industry in title for industry in industries))
    return _single_stock_scope(manifest, code)


def _unmapped_sector_row(row, route):
    return (route == "official_sector_observations" and not _text(_first(row, _IDENTITY))
        and not _text(row.get("entity_id")).startswith("stock:")
        and bool(_first(row, ("sector_key", "sector_label", "sector", "industry")) or _text(row.get("entity_id")).startswith("sector:")))


def _classification_candidate(row, stock, asof_day, cutoff):
    verified = lambda value: value is True or (type(value).__name__ == "bool_" and bool(value))
    observed = _day(row.get("classification_observed_date"))
    observed_at = _timestamp(row.get("classification_observed_at"))
    if (str(_first(row, _IDENTITY)) != str(stock["stock_id"])
        or _market(row.get("market")) != _market(stock.get("market"))
        or not verified(row.get("classification_verified"))
        or not verified(row.get("current_ordinary_classification_verified"))
        or observed is None or observed > asof_day
        or (observed_at is not None and observed_at > cutoff)):
        return None
    labels = {_text(row.get(key)) for key in ("sector", "industry_category", "source_industry")}
    labels.discard("")
    return {"labels": labels, "observed": observed.isoformat(),
        "stock_name": _text(_first(row, ("stock_name", "company_name"))) or _text(_first(stock, ("stock_name", "name")))} if labels else None


def _deadline_metadata_record(row):
    fields = ("stock_id", "market", "company_name", "period_id", "period_kind", "period_start", "period_end",
        "date", "request_date", "base_deadline", "derived_deadline", "holiday_adjusted_deadline", "status",
        "classification_status", "classification_reason", "observed_filed_at", "observed_source_status",
        "rule_class", "rule_source_url", "deadline_exception_status", "monthly_revenue_data_status")
    record = {}
    for key in fields:
        if key not in row:
            continue
        value = row[key]
        if value is None or not _text(value):
            record[key] = None
        elif isinstance(value, (date, datetime)):
            record[key] = value.isoformat()
        elif isinstance(value, bool) or type(value).__name__ == "bool_":
            record[key] = bool(value)
        elif isinstance(value, str):
            record[key] = value
        else:
            number = _number(value)
            record[key] = number if number is not None else _text(value)
    return record


def _bound_proof(manifest, key, stock, asof_day, outcome):
    proof = manifest.get(key)
    if not isinstance(proof, Mapping) or proof.get("verified") is not True:
        return False
    scope = proof.get("scope") if isinstance(proof.get("scope"), Mapping) else proof
    if str(scope.get("stock_id", "")) != str(stock["stock_id"]):
        return False
    if scope.get("route_id", proof.get("route_id")) != manifest.get("route_id"):
        return False
    if scope.get("market") and _market(scope["market"]) != _market(stock.get("market")):
        return False
    start = _day(_first(scope, ("start", "start_date", "date", "asof")))
    end = _day(_first(scope, ("end", "end_date", "date", "asof")))
    if start is None or end is None or not start <= asof_day <= end:
        return False
    if proof.get("pending_release") is True or proof.get("not_yet_published") is True:
        return False
    if outcome == "not_applicable":
        return proof.get("applicable") is False and bool(_text(proof.get("reason_zh", proof.get("reason"))))
    return ((proof.get("outcome") == "no_event" or proof.get("no_event_confirmed") is True)
            and type(proof.get("row_count")) is int and proof["row_count"] == 0)


def _binding(manifest):
    artifact = manifest.get("object") if isinstance(manifest.get("object"), Mapping) else {}
    identity = manifest.get("source_identity") if isinstance(manifest.get("source_identity"), Mapping) else {}
    query = manifest.get("query_binding") if isinstance(manifest.get("query_binding"), Mapping) else {}
    evidence = manifest.get("evidence") if isinstance(manifest.get("evidence"), Mapping) else {}
    metadata_identity = evidence.get("identity") if isinstance(evidence.get("identity"), Mapping) else {}
    metadata_binding = evidence.get("binding") if isinstance(evidence.get("binding"), Mapping) else {}
    return {
        key: _text(value) for key, value in {
            "source_object_sha256": artifact.get("sha256"),
            "source_object_path": artifact.get("path"),
            "snapshot_id": identity.get("snapshot_id"),
            "producer_id": identity.get("producer_id"),
            "publication_run_id": identity.get("publication_run_id"),
            "rows_sha256": query.get("rows_sha256"),
            "metadata_binding_sha256": evidence.get("binding_sha256", metadata_binding.get("binding_sha256")),
            "identity_sha256": metadata_identity.get("sha256"),
        }.items() if _text(value)
    }


def _verified_metadata(manifest, stock):
    """Recognize the caller's verified sidecars, without validating them again."""
    route = manifest.get("route_id")
    evidence = manifest.get("evidence") or {}
    reader = manifest.get("reader", (manifest.get("query_binding") or {}).get("reader"))
    if not isinstance(evidence, Mapping) or not evidence:
        return None
    if route == "official_calendar":
        markets = evidence.get("calendar_verified_by_market") or {}
        verified = any(_market(market) == _market(stock.get("market")) and value is True for market, value in markets.items())
        if verified or (not markets and reader == "verified_context" and evidence.get("historical_axis_verified") is True):
            return {"market": _market(stock.get("market")), "historical_axis_verified": evidence.get("historical_axis_verified") is True,
                "future_targets_verified": evidence.get("future_targets_verified") is True,
                "verified_asof": _text(evidence.get("verified_asof")) or None}
    if route == "finmind_stock_info" and manifest.get("current_identity_verified") is True:
        return {"stock_id": str(stock["stock_id"]), "market": _market(stock.get("market")),
            "stock_name": _text(_first(stock, ("stock_name", "name"))),
            "industry_category": _text(_first(stock, ("industry", "industry_category", "sector"))),
            "current_identity_verified": True,
            "historical_membership_verified": manifest.get("historical_membership_verified") is True}
    return None


def _unit(row, manifest, field):
    value = None
    for mapping in (row.get("units"), manifest.get("units")):
        if isinstance(mapping, Mapping) and _text(mapping.get(field)):
            value = mapping[field]
            break
    if value is None:
        value = _first(row, ("unit", "measure_unit", "value_unit"))
    if value is None:
        fields = manifest.get("fields") or []
        if isinstance(fields, list):
            value = next((item.get("unit") for item in fields if isinstance(item, Mapping) and item.get("name") == field), None)
    result = _text(value)
    return "unknown" if result in {"", "unknown", "單位未核實", "未核實"} else result


def _basis(row, route):
    raw = _text(_first(row, ("period_basis", "period_kind", "period_type")))
    basis = {"年初累計": "year_to_date", "累計": "year_to_date", "單季": "quarter", "single_quarter": "quarter",
             "年度": "annual", "期末存量": "instant", "point_in_time": "instant"}.get(raw, raw)
    if basis not in {"year_to_date", "quarter", "annual", "instant", "monthly"}:
        basis = "instant" if route == "finmind_balance_sheet" else "unknown" if route in _FINANCIAL else "observation"
    start, end = _day(row.get("period_start")), _day(row.get("period_end")) or _record_day(row)
    span = ""
    if basis == "year_to_date":
        # Half-year cumulative and first-quarter cumulative are different series.
        span = str(end.month) if end is not None else "unknown"
        if start is not None:
            span = f"{start.month}-{end.month}" if end is not None else "unknown"
    return basis, span


def _numeric_fields(row, route):
    if route in _NEWS:
        return []
    if route in _FINANCIAL and ("value" in row or "cash_flow_value" in row):
        return ["value" if "value" in row else "cash_flow_value"]
    if route in _MACRO and route != "finmind_exchange_rate":
        field = next((key for key in ("close", "adj_close", "value", "price") if key in row), None)
        return [field] if field else []
    return sorted(key for key, value in row.items() if key not in _NON_VALUES and not key.startswith("_")
                  and not key.lower().endswith(("_id", "_code", "_date", "_time", "_at", "_sha256", "_fingerprint", "_verified"))
                  and (_number(value) is not None or key in _VALUE_NAMES))


def _revenue_period_fields(row):
    result = {}
    for canonical, native, maximum in (("revenue_year", "month_revenue_year", 9999), ("revenue_month", "month_revenue_month", 12)):
        for key in (canonical, native):
            value = _number(row.get(key))
            if value is not None and value == int(value) and 1 <= value <= maximum:
                result[key] = int(value)
        if canonical not in result and native in result:
            result[canonical] = result[native]
    return result


def _series_key(row, route, field, unit):
    basis, span = _basis(row, route)
    return tuple(_text(value) for value in (
        _first(row, ("entity_id", "stock_id", "stock", "sector_key", "industry")),
        row.get("symbol"), _first(row, ("indicator", "feature_name", "instrument")),
        _first(row, ("type", "field", "name")), row.get("currency"),
        _first(row, ("level", "holding_level", "持股分級")), field, unit, basis, span,
    ))


def _series_summary(key, points, conflicts):
    fields = ("entity_id", "symbol", "indicator", "type", "currency", "level", "field", "unit", "period_basis", "period_span")
    identity = dict(zip(fields, key))
    clean = [point for day, point in sorted(points.items()) if day not in conflicts]
    if not clean:
        return None
    values = [point["value"] for point in clean]
    latest = clean[-1]
    previous = clean[-2] if len(clean) > 1 else None
    comparable = previous is not None and identity["period_basis"] != "unknown"
    comparison = {"comparable": comparable}
    if comparable:
        comparison["delta"] = latest["value"] - previous["value"]
        if previous["value"] > 0:
            comparison["change_ratio"] = latest["value"] / previous["value"] - 1
    positive = [value for value in values if value > 0]
    if latest["value"] > 0 and positive and identity["period_basis"] != "unknown":
        comparison["positive_history_count"] = len(positive)
        comparison["positive_history_percentile"] = sum(value <= latest["value"] for value in positive) / len(positive)
    windows = {}
    if identity["period_basis"] == "observation":
        for size in (5, 20):
            window = clean[-size:]
            windows[str(size)] = {"observed_count": len(window), "requested_count": size,
                "start": window[0]["date"], "end": window[-1]["date"],
                "sum": sum(point["value"] for point in window),
                "mean": sum(point["value"] for point in window) / len(window)}
    statuses = []
    if identity["unit"] == "unknown":
        statuses.append("unknown_unit")
    if not comparable:
        statuses.append("insufficient_comparable_history")
    if conflicts:
        statuses.append("partial_observations")
    return {"identity": identity, "observation_count": len(clean),
        "observed_period": {"start": clean[0]["date"], "end": latest["date"]},
        "latest": latest, "previous": previous, "comparison": comparison,
        "windows": windows, "conflicting_dates": len(conflicts), "statuses": statuses}


def _local_gap(manifest, stock_id):
    coverage = manifest.get("coverage") or {}
    local = (coverage.get("by_stock") or {}).get(stock_id, {})
    if str(coverage.get("stock_id", "")) == stock_id:
        local = coverage
    if not isinstance(local, Mapping):
        return False
    return bool(local.get("missing_dates") or local.get("missing_periods") or (_number(local.get("missing_observations")) or 0) > 0)


def build_report_context(source_frames, source_manifest, *, stock, asof) -> dict:
    """Return a native JSON tree; preserve the caller's asof identity verbatim.

    sources contain compact latest/previous records and per-series counts,
    windows and bindings. statuses is a flat list of route/status/reason_zh.
    Explicit zero/applicability evidence must be verified and bound to this
    route, stock, and date (or covering interval); an empty response is no proof.
    """
    if not isinstance(asof, (str, int)) or isinstance(asof, bool):
        raise ValueError("report_context_asof_must_be_json_date_scalar")
    asof_day = _day(asof)
    if asof_day is None:
        raise ValueError("report_context_asof_invalid")
    code = str(stock["stock_id"])
    cutoff = _timestamp(asof) or datetime.combine(asof_day, time.max, _TAIPEI)
    manifests = source_manifest.values() if isinstance(source_manifest, Mapping) else source_manifest or []
    by_route = {str(item["route_id"]): item for item in manifests if isinstance(item, Mapping) and item.get("route_id")}
    routes = sorted(set(by_route) | set(source_frames), key=lambda route: (route != "official_sector_features", route))
    working_stock, stock_profile = dict(stock), None
    sources, statuses = [], []
    for route in routes:
        manifest = by_route.get(route, {"route_id": route})
        usage = _text(manifest.get("usage")) or "context-only"
        source = {"route_id": str(route), "name_zh": _text(manifest.get("name_zh")) or str(route),
            "usage": usage, "availability": "available", "latest_data_date": None,
            "context_scope_zh": "完整已讀資料先篩選個股、關聯與可用時間；每序列僅留最新與前值，僅供報告背景。",
            "records": [], "series": [], "observation_count": 0, "observed_period": {"start": None, "end": None},
            "excluded_counts": {"irrelevant": 0, "future": 0, "unknown_date": 0},
            "unmapped_observation_count": 0,
            "source_binding": _binding(manifest), "statuses": []}
        flags = set()
        if usage in {"not-selected", "not_selected"} or manifest.get("status") in {"not-selected", "not_selected"}:
            primary = "not_selected"
        elif _bound_proof(manifest, "applicability_evidence", stock, asof_day, "not_applicable"):
            primary = "not_applicable"
        elif (metadata := _verified_metadata(manifest, stock)) is not None:
            primary = "available"
            source["metadata_only"] = True
            source["metadata"] = metadata
            source["records"] = [metadata.copy()]
            source["context_scope_zh"] = "呼叫端已驗證的metadata側錄；不要求行情式日期，也不據此推定歷史成分或未來日曆完整。"
        elif route not in source_frames or source_frames[route] is None:
            primary = "missing_source"
        else:
            frame = source_frames[route]
            columns = [str(column) for column in frame.columns]
            groups, conflicts = {}, defaultdict(set)
            text_records = []
            deadline_records = []
            classification_candidates = []
            dates = set()
            for values in frame.itertuples(index=False, name=None):
                row = dict(zip(columns, values))
                related = _related(row, route, working_stock, manifest)
                unmapped_sector = not related and _unmapped_sector_row(row, route)
                if not related and not unmapped_sector:
                    source["excluded_counts"]["irrelevant"] += 1
                    continue
                day = _record_day(row)
                if day is None:
                    source["excluded_counts"]["unknown_date"] += 1
                    flags.add("unmapped_fields")
                    continue
                timestamps = [_timestamp(row.get(key)) for key in _TIME_FIELDS]
                known_times = [value for value in timestamps if value is not None]
                if day > asof_day or any(value > cutoff for value in known_times):
                    source["excluded_counts"]["future"] += 1
                    continue
                if unmapped_sector:
                    source["unmapped_observation_count"] += 1
                    source["unmapped_reason_zh"] = "已讀取產業觀測，但官方本股產業與觀測用sector_key的權威對照未建立。"
                    flags.add("unmapped_fields")
                    continue
                if route == "official_sector_features":
                    candidate = _classification_candidate(row, stock, asof_day, cutoff)
                    if candidate is not None:
                        classification_candidates.append(candidate)
                if route == "official_finance_reporting_deadline":
                    # Filing schedules are metadata, not financial observations.
                    source["observation_count"] += 1
                    dates.add(day.isoformat())
                    deadline_records.append(_deadline_metadata_record(row))
                    if (_timestamp(row.get("observed_filed_at")) is None
                        or _text(row.get("observed_source_status")).upper() == "UNAVAILABLE"):
                        flags.add("unknown_publication_time")
                    continue
                if not known_times or any(row.get(key) is False for key in ("source_publication_time_verified", "publication_time_verified", "available_time_verified")):
                    flags.add("unknown_publication_time")
                source["observation_count"] += 1
                dates.add(day.isoformat())
                fields = _numeric_fields(row, route)
                if route in _NEWS:
                    title = _text(_first(row, ("title", "Title", "主旨")))
                    if title:
                        text_records.append({"date": day.isoformat(), "title": title[:240],
                            "publisher": _text(row.get("publisher")), "url": _text(_first(row, ("url", "source_url", "Url"))),
                            "publication_time": max(known_times).isoformat() if known_times else None})
                    else:
                        flags.add("unmapped_fields")
                for field in fields:
                    value = _number(row.get(field))
                    if value is None:
                        flags.add("partial_observations")
                        continue
                    unit = _unit(row, manifest, field)
                    key = _series_key(row, route, field, unit)
                    point = {"date": day.isoformat(), "value": value,
                        "period_start": (_day(row.get("period_start")) or day).isoformat() if row.get("period_start") is not None else None,
                        "period_end": (_day(row.get("period_end")) or day).isoformat(),
                        "label_zh": _text(_first(row, ("origin_name", "label", "name_zh"))) or field}
                    if route == "finmind_month_revenue":
                        point.update(_revenue_period_fields(row))
                    points = groups.setdefault(key, {})
                    if day.isoformat() in points and points[day.isoformat()] != point:
                        conflicts[key].add(day.isoformat())
                    else:
                        points[day.isoformat()] = point
                if not fields and route not in _NEWS:
                    # Identity-only rows are useful without pretending to be numeric history.
                    identity = {key: _text(row[key]) for key in ("stock_id", "stock_name", "sector", "industry_category", "status") if _text(row.get(key))}
                    if identity:
                        text_records.append({"date": day.isoformat(), **identity})
                    elif route != "official_calendar":
                        flags.add("unmapped_fields")
            if route == "official_sector_features" and classification_candidates:
                labels = set().union(*(item["labels"] for item in classification_candidates))
                supplied = _text(stock.get("industry"))
                if len(labels) == 1 and (not supplied or supplied in labels):
                    latest = max(classification_candidates, key=lambda item: (item["observed"], item["stock_name"]))
                    stock_profile = {"stock_id": code, "market": _market(stock.get("market")),
                        "stock_name": latest["stock_name"], "industry": next(iter(labels)),
                        "classification_verified": True, "classification_observed_date": latest["observed"],
                        "source_route": "official_sector_features"}
                    if not supplied:
                        working_stock["industry"] = stock_profile["industry"]
                    working_stock["classification_verified"] = True
                else:
                    flags.add("unmapped_fields")
                    source["unmapped_reason_zh"] = "本股已驗證官方產業分類存在衝突，未選擇或推測產業對照。"
            for key in sorted(groups):
                series = _series_summary(key, groups[key], conflicts[key])
                if series is not None:
                    source["series"].append(series)
                    flags.update(series["statuses"])
                    for role in ("latest", "previous"):
                        if series[role] is not None:
                            source["records"].append({**series["identity"], **series[role], "observation_role": role})
                elif conflicts[key]:
                    flags.add("partial_observations")
            # Limit presentation only after all relevant observations were examined.
            unique_text = {tuple(sorted(item.items())): item for item in text_records}
            source["records"].extend(sorted(unique_text.values(), key=lambda item: (item["date"], str(sorted(item.items()))), reverse=True)[:6])
            if deadline_records:
                source["metadata_only"] = True
                source["context_scope_zh"] = "各期申報時程metadata；未觀察到申報時間不代表財報或營收缺失。"
                unique_deadlines = {tuple(sorted(item.items())): item for item in deadline_records}
                source["records"].extend(sorted(unique_deadlines.values(), key=lambda item: (
                    _text(item.get("period_end")), _text(item.get("period_id")), str(sorted(item.items())))))
            if dates:
                source["observed_period"] = {"start": min(dates), "end": max(dates)}
                source["latest_data_date"] = max(dates)
            if _local_gap(manifest, code):
                flags.add("partial_observations")
                if route == "official_finance_reporting_deadline":
                    source["partial_reason_zh"] = "本股申報時程有明確缺期，僅表示時程觀測不足，不代表財報數值缺失。"
            if source["observation_count"]:
                primary = "partial_observations" if "partial_observations" in flags else "available"
            elif source["unmapped_observation_count"]:
                primary = "unmapped_fields"
            elif _bound_proof(manifest, "zero_evidence", stock, asof_day, "no_event"):
                primary = "confirmed_no_event"
            else:
                primary = "no_matching_observations_unconfirmed"
        source["availability"] = primary
        source["statuses"] = [primary, *sorted(flags - {primary})]
        for status in source["statuses"]:
            reason = source.get("unmapped_reason_zh", _REASONS[status]) if status == "unmapped_fields" else _REASONS[status]
            if status == "partial_observations":
                reason = source.get("partial_reason_zh", reason)
            statuses.append({"route_id": str(route), "status": status, "reason_zh": reason})
        sources.append(source)
    sources.sort(key=lambda source: source["route_id"])
    status_order = {source["route_id"]: {status: index for index, status in enumerate(source["statuses"])} for source in sources}
    statuses.sort(key=lambda item: (item["route_id"], status_order[item["route_id"]][item["status"]]))
    result = {"schema": "single-stock-report-context-v1", "stock_id": code, "asof": asof,
        "sources": sources, "statuses": statuses}
    if stock_profile is not None:
        result["stock_profile"] = stock_profile
    return result


def _fmt(value, digits=2):
    return f"{value:,.{digits}f}".rstrip("0").rstrip(".")


def _pct(value):
    return f"{value * 100:+.2f}%"


def _metric_end(metric):
    explicit = _day(metric.get("period_end"))
    if explicit:
        return explicit
    tokens = re.findall(r"\d{4}-\d{2}-\d{2}|\d{8}|\d{6}", _text(metric.get("資料期間", metric.get("period"))))
    if not tokens:
        return None
    token = tokens[-1]
    return _day(token + "01" if len(token) == 6 else token)


def _known_unit(value):
    return _text(value) not in {"", "unknown", "單位未核實", "未核實"}


def normalize_news_title(title, publisher=None) -> str:
    """Collapse whitespace and remove only an exact, separator-led publisher suffix.

    Numbers, punctuation and title content are otherwise preserved. With no
    publisher, only whitespace is normalized. This function has no I/O or state.
    """
    normalized = " ".join(_text(title).split())
    publisher = " ".join(_text(publisher).split())
    if publisher:
        suffix = re.search(r"\s*[-|｜–—]\s*" + re.escape(publisher) + r"$", normalized)
        if suffix is not None and normalized[:suffix.start()].strip():
            normalized = normalized[:suffix.start()].strip()
    return normalized


# Display aliases mirror the renderer's common symbols; they are not data mappings.
_DISPLAY_MACRO_LABELS = {
    "^GSPC": "美國標普500指數", "^IXIC": "美國那斯達克指數",
    "^SOX": "美國費城半導體指數", "^DJI": "美國道瓊工業指數",
    "TWD=X": "美元兌新臺幣", "JPY=X": "美元兌日圓", "KRW=X": "美元兌韓元",
    "^TNX": "美國10年期公債殖利率", "GC=F": "黃金期貨", "CL=F": "WTI 原油期貨",
}
_DISPLAY_MACRO_ALIASES = {
    **{key: key for key in _DISPLAY_MACRO_LABELS},
    **{label: key for key, label in _DISPLAY_MACRO_LABELS.items()},
    "us_sp500": "^GSPC", "SP500": "^GSPC", "us_nasdaq": "^IXIC", "NASDAQ": "^IXIC",
    "us_semiconductor": "^SOX", "SOX": "^SOX", "us_dow": "^DJI",
    "usd_twd": "TWD=X", "usd_jpy": "JPY=X", "usd_krw": "KRW=X",
    "us10y": "^TNX", "United States 10-Year": "^TNX", "gold": "GC=F", "wti": "CL=F",
}


def _macro_insights(sources, asof, add):
    candidates = defaultdict(list)
    currencies = {"USD": "美元", "JPY": "日圓", "KRW": "韓元"}
    for source in sources:
        route = source.get("route_id")
        if route not in _MACRO or source.get("availability") in {"not_selected", "not_applicable", "confirmed_no_event"}:
            continue
        for series in source.get("series") or []:
            identity, latest = series.get("identity") or {}, series.get("latest") or {}
            value, day = _number(latest.get("value")), _day(latest.get("date"))
            if value is None or day is None or (asof is not None and day > asof):
                continue
            field = identity.get("field")
            if route == "finmind_exchange_rate":
                currency = identity.get("currency")
                if currency not in currencies or field not in {"spot_buy", "spot_sell"}:
                    continue
                target = ("spot", currency, field)
                label = currencies[currency] + ("即期買入" if field == "spot_buy" else "即期賣出")
            else:
                mapped = {_DISPLAY_MACRO_ALIASES[text] for text in (
                    _text(identity.get("symbol")), _text(identity.get("indicator")),
                    _text(identity.get("type")), _text(latest.get("label_zh"))) if text in _DISPLAY_MACRO_ALIASES}
                if len(mapped) != 1:
                    continue
                canonical = next(iter(mapped))
                if route == "global_market_history" and field not in {"close", "adj_close", "adjusted_close"}:
                    continue
                if route == "finmind_government_bonds_yield" and (canonical != "^TNX" or field not in {"value", "yield"}):
                    continue
                if route == "finmind_crude_oil_prices" and (canonical != "CL=F" or field not in {"value", "price"}):
                    continue
                if route == "finmind_gold_price" and (canonical != "GC=F" or field not in {"value", "price", "Price"}):
                    continue
                target, label = ("global", canonical), _DISPLAY_MACRO_LABELS[canonical]
            unit = _text(identity.get("unit")) if _known_unit(identity.get("unit")) else ""
            preferred = 0 if route == "finmind_government_bonds_yield" else 1
            candidates[target].append({"label": label, "day": day, "value": value, "unit": unit,
                "change": _number((series.get("comparison") or {}).get("change_ratio")),
                "order": (preferred, -day.toordinal(), field != "close", str(route), str(field), value, unit)})
    chosen = {key: min(values, key=lambda item: item["order"]) for key, values in candidates.items()}
    groups = []
    indices = [chosen[("global", key)] for key in ("^GSPC", "^IXIC", "^SOX", "^DJI") if ("global", key) in chosen]
    if indices:
        groups.append(indices)
    spot_currency = next((currency for currency in ("USD", "JPY", "KRW")
        if any(("spot", currency, field) in chosen for field in ("spot_buy", "spot_sell"))), None)
    if spot_currency:
        groups.append([chosen[("spot", spot_currency, field)] for field in ("spot_buy", "spot_sell")
                       if ("spot", spot_currency, field) in chosen])
    else:
        exchange = [chosen[("global", key)] for key in ("TWD=X", "JPY=X", "KRW=X") if ("global", key) in chosen]
        if exchange:
            groups.append(exchange[:2])
    if ("global", "^TNX") in chosen:
        groups.append([chosen[("global", "^TNX")]])
    if not indices:
        commodities = [chosen[("global", key)] for key in ("GC=F", "CL=F") if ("global", key) in chosen]
        if commodities:
            groups.append(commodities)
    for group in groups:
        details = []
        for item in group:
            suffix = f"，同序列前值變動{_pct(item['change'])}" if item["change"] is not None else ""
            details.append(f"{item['label']}於{item['day'].isoformat()}為{_fmt(item['value'])}{item['unit']}{suffix}")
            if not item["unit"]:
                add("data_notes", "", kind="unknown_unit")
        add("context", "；".join(details) + "。")


def _flow_insights(metrics, add):
    incompatible, unaligned = [], []
    for entity, label in (("foreign", "外資"), ("trust", "投信"), ("dealer", "自營商")):
        short, long = metrics.get(f"institutional_{entity}_5"), metrics.get(f"institutional_{entity}_20")
        if not short or not long:
            continue
        a, b = _number(short.get("current_value")), _number(long.get("current_value"))
        if a is None or b is None:
            continue
        n5, n20 = _number(short.get("observed_days")), _number(long.get("observed_days"))
        if n5 is None or n20 is None:
            continue
        if short.get("unit") != long.get("unit") or short.get("source_route") != long.get("source_route"):
            incompatible.append(label)
            continue
        end5, end20 = _metric_end(short), _metric_end(long)
        aligned = end5 is not None and end5 == end20 and n5 == 5 and n20 == 20
        if not aligned:
            tail = "兩窗口僅分列觀測淨額"
            unaligned.append(label)
        elif a > 0 > b:
            tail = "短期淨買，中期累計仍淨賣"
        elif a < 0 < b:
            tail = "短期淨賣，中期累計仍淨買"
        elif a > 0 and b > 0:
            tail = "短中期累計方向均為淨買"
        elif a < 0 and b < 0:
            tail = "短中期累計方向均為淨賣"
        else:
            tail = "至少一個窗口淨額為零"
        unit = f"（{_text(short.get('unit'))}）" if _known_unit(short.get("unit")) else ""
        if not unit:
            add("data_notes", "", kind="unknown_unit")
        add("flows", f"{label}5筆窗口實際{int(n5)}個有資料日淨額{_fmt(a)}、20筆窗口實際{int(n20)}個有資料日淨額{_fmt(b)}{unit}；{tail}。")
    if incompatible:
        add("data_notes", "、".join(incompatible) + "短中期淨買賣的來源或單位不一致，未判定資金方向反轉。")
    if unaligned:
        add("data_notes", "、".join(unaligned) + "的5／20筆窗口不足或截止日未對齊，未合併判定短中期反轉。")
    for key, label in (("margin_purchase_balance", "融資"), ("short_sale_balance", "融券")):
        metric = metrics.get(key, {})
        value, delta = _number(metric.get("current_value")), _number(metric.get("delta"))
        if value is not None and delta is not None:
            unit = f"（{_text(metric.get('unit'))}）" if _known_unit(metric.get("unit")) else ""
            if not unit:
                add("data_notes", "", kind="unknown_unit")
            add("flows", f"{label}餘額{_fmt(value)}，較前次觀測{'增加' if delta > 0 else '減少' if delta < 0 else '不變'}{_fmt(abs(delta))}{unit}。")
    distribution = [metric for key, metric in metrics.items() if key.startswith("tdcc_")
        and metric.get("unit") == "%" and _number(metric.get("delta")) is not None]
    if distribution:
        chosen = sorted(distribution, key=lambda item: (-abs(float(item["delta"])), str(item["id"])))[:2]
        details = "、".join(f"{_text(item.get('項目'))}變動{float(item['delta']):+.2f}個百分點" for item in chosen)
        add("flows", f"{details}。")
        if any(item.get("weekly_comparable") is False for item in chosen):
            add("data_notes", "集保級距變動採前次實際觀測，非完整週比較。")


def _fundamental_insights(metrics, add):
    monthly = metrics.get("revenue_monthly", {})
    yoy, mom = _number(monthly.get("yoy")), _number(monthly.get("mom"))
    if yoy is not None:
        detail = f"、月比{_pct(mom)}" if mom is not None else ""
        add("fundamentals", f"{_text(monthly.get('資料期間'))}月營收年增率{_pct(yoy)}{detail}。")
    windows = [(label, metrics.get(key, {})) for label, key in (("近三月", "revenue_3m"), ("年初累計", "revenue_ytd"), ("近十二月", "revenue_12m"))]
    valid = [(label, metric, _number(metric.get("yoy"))) for label, metric in windows
             if metric.get("complete") is not False and _number(metric.get("yoy")) is not None]
    if len(valid) > 1:
        ends = [_metric_end(metric) for _, metric, _ in valid]
        aligned = ends[0] is not None and len(set(ends)) == 1
        detail = "、".join(f"{label}年增{_pct(value)}" for label, _, value in valid)
        recent = next((value for label, _, value in valid if label == "近三月"), None)
        longer = [value for label, _, value in valid if label != "近三月"]
        if aligned and recent is not None and longer and recent > max(longer):
            conclusion = "近期營收同比動能強於較長窗口"
        elif aligned and recent is not None and longer and recent < min(longer):
            conclusion = "近期營收同比動能弱於較長窗口"
        else:
            conclusion = "各窗口同比動能強弱不一" if aligned else "各窗口分列比較"
        if not aligned:
            add("data_notes", "營收各比較窗口的截止月份未對齊，未合併判定動能加速。")
        add("fundamentals", f"{detail}；{conclusion}。")
    valuations = []
    for key, label in (("valuation_per", "本益比"), ("valuation_pbr", "股價淨值比")):
        metric = metrics.get(key, {})
        value, percentile, count = (_number(metric.get(field)) for field in ("current_value", "percentile", "historical_count"))
        if value is not None and value > 0 and percentile is not None and 0 <= percentile <= 1 and count and metric.get("unit") == "倍":
            valuations.append(f"{label}{_fmt(value)}倍，位於自身{int(count)}筆正值歷史的{percentile * 100:.1f}%位置")
    if valuations:
        add("fundamentals", "；".join(valuations) + "。")
    financial = [metric for key, metric in metrics.items() if key.startswith("financial_")]
    cash = next((metric for metric in financial if "OperatingActivities" in str(metric.get("id")) or metric.get("項目") == "營業活動現金流量"), None)
    profit = next((metric for metric in financial if "IncomeAfterTaxes" in str(metric.get("id")) or metric.get("項目") == "稅後淨利"), None)
    if cash:
        current, prior = _number(cash.get("current_value")), _number(cash.get("year_ago_value"))
        if current is not None and prior is not None and prior > 0 > current:
            unit = f"（{_text(cash.get('unit'))}）" if _known_unit(cash.get("unit")) else ""
            if not unit:
                add("data_notes", "", kind="unknown_unit")
            add("fundamentals", f"{_text(cash.get('資料期間'))}營業現金流由去年同口徑{_fmt(prior)}轉為{_fmt(current)}{unit}，現金流轉弱。")
    if cash and profit:
        cv, pv = _number(cash.get("current_value")), _number(profit.get("current_value"))
        basis = _text(cash.get("period_basis"))
        comparable = (cv is not None and pv is not None and basis in {"year_to_date", "quarter", "annual"}
            and basis == profit.get("period_basis") and _metric_end(cash) is not None and _metric_end(cash) == _metric_end(profit)
            and _known_unit(cash.get("unit")) and cash.get("unit") == profit.get("unit")
            and _day(cash.get("period_start")) is not None and _day(cash.get("period_start")) == _day(profit.get("period_start")))
        if comparable:
            relation = "獲利與現金流方向背離" if pv * cv < 0 else "獲利與現金流正負方向一致" if pv * cv > 0 else "至少一項為零"
            add("fundamentals", f"同期間稅後淨利{_fmt(pv)}、營業現金流{_fmt(cv)}（{_text(cash['unit'])}），{relation}。")
        else:
            add("data_notes", "獲利與營業現金流的期間起訖、口徑或單位尚未同時核實，未做盈餘現金實現程度比較。")


def _technical_insights(payload, features, add):
    asof = _day(payload.get("asof"))
    prices = {}
    conflicts = set()
    for row in payload.get("price_history") or []:
        if not isinstance(row, Mapping):
            continue
        day = _day(row.get("date"))
        value = _number(row.get("adjusted_close", row.get("close")))
        if day is not None and value is not None and value > 0 and (asof is None or day <= asof):
            basis = _text(row.get("price_basis")) or ("adjusted" if "adjusted_close" in row else "unknown")
            if day in prices and prices[day] != (value, basis):
                conflicts.add(day)
            else:
                prices[day] = (value, basis)
    for day in conflicts:
        prices.pop(day, None)
    ordered = sorted(prices)
    if ordered:
        last = prices[ordered[-1]]
        values = [prices[day][0] for day in ordered if prices[day][1] == last[1]]
        means = [(size, sum(values[-size:]) / size) for size in (5, 20, 60) if len(values) >= size]
        if means and last[1] != "unknown":
            detail = "、".join(f"{size}筆均價{_fmt(mean)}" for size, mean in means)
            above = sum(last[0] > mean for _, mean in means)
            relation = "低於所有上述均價" if above == 0 and all(last[0] < mean for _, mean in means) else "高於所有上述均價" if above == len(means) else "與各期均價位置不一致"
            add("technical", f"{ordered[-1].isoformat()}同口徑收盤{_fmt(last[0])}，{detail}；收盤{relation}。")
    r5, r20, r60 = (_number(features.get(name)) for name in ("return_5", "return_20", "return_60"))
    if r5 is not None and r20 is not None:
        detail = f"、60日{_pct(r60)}" if r60 is not None else ""
        relation = "短期反彈與中期回落並存" if r5 > 0 > r20 else "短期回落與中期上漲並存" if r5 < 0 < r20 else "短中期方向一致" if r5 * r20 > 0 else "至少一期報酬為零"
        add("technical", f"既有5日報酬{_pct(r5)}、20日{_pct(r20)}{detail}；{relation}。")
    volume = _number(features.get("volume_ratio_20"))
    if volume is not None and volume >= 0:
        relation = "量能低於自身20日基準，價格變動未伴隨放量" if volume < 1 else "量能高於自身20日基準" if volume > 1 else "量能與自身20日基準相同"
        add("technical", f"20日量比{_fmt(volume)}倍，{relation}。")
    v20, v60 = _number(features.get("volatility_20")), _number(features.get("volatility_60"))
    if v20 is not None and v60 is not None and min(v20, v60) >= 0:
        relation = "較低" if v20 < v60 else "較高" if v20 > v60 else "相同"
        add("technical", f"既有20日波動指標{v20 * 100:.2f}%、60日{v60 * 100:.2f}%，短窗口波動{relation}。")


def _summary_insights(metrics, features, feature_dates, asof, add):
    """Join only explicit, aligned facts; never infer a missing financial period."""
    day = _day(asof)
    r5, r20, volume = (_number(features.get(name)) for name in ("return_5", "return_20", "volume_ratio_20"))
    aligned = day is not None and all(feature_dates.get(name) == day for name in ("return_5", "return_20", "volume_ratio_20"))
    if aligned and r5 is not None and r20 is not None and volume is not None and r5 != 0 and r20 != 0 and volume > 0:
        if r5 > 0 and r20 > 0:
            direction = "短中期均上漲"
        elif r5 < 0 and r20 < 0:
            direction = "短中期均回落"
        elif r5 > 0 > r20:
            direction = "呈弱量反彈" if volume < 1 else "短期反彈，中期仍回落"
        else:
            direction = "短期回落，中期仍上漲"
        activity = "量能低於20日基準" if volume < 1 else "量能高於20日基準" if volume > 1 else "量能等於20日基準"
        sentence = f"5日{_pct(r5)}對照20日{_pct(r20)}、量比{_fmt(volume)}倍：{direction}，{activity}"
        short, long = metrics.get("institutional_foreign_5", {}), metrics.get("institutional_foreign_20", {})
        a, b = _number(short.get("current_value")), _number(long.get("current_value"))
        if (a is not None and b is not None and a > 0 > b and short.get("observed_days") == 5 and long.get("observed_days") == 20
            and _metric_end(short) == day and _metric_end(long) == day
            and short.get("source_route") and short.get("source_route") == long.get("source_route")
            and short.get("unit") and short.get("unit") == long.get("unit")):
            sentence += "；同日截止的外資短期淨買，中期累計仍淨賣（5／20個有資料日窗口）"
        add("summary", sentence + "。")
    recent, longer = metrics.get("revenue_3m", {}), metrics.get("revenue_12m", {})
    r3, r12 = _number(recent.get("yoy")), _number(longer.get("yoy"))
    revenue_end, longer_end = _metric_end(recent), _metric_end(longer)
    cash = next((item for key, item in metrics.items() if key.startswith("financial_")
        and ("OperatingActivities" in key or item.get("項目") == "營業活動現金流量")), {})
    current, prior = _number(cash.get("current_value")), _number(cash.get("year_ago_value"))
    start, end = _day(cash.get("period_start")), _day(cash.get("period_end"))
    prior_start, prior_end = _day(cash.get("year_ago_period_start")), _day(cash.get("year_ago_period_end"))
    comparable = (start is not None and end is not None and prior_start is not None and prior_end is not None
        and start <= end and prior_start <= prior_end
        and start.year == prior_start.year + 1 and end.year == prior_end.year + 1
        and (start.month, start.day, end.month, end.day) == (prior_start.month, prior_start.day, prior_end.month, prior_end.day)
        and cash.get("period_basis") in {"year_to_date", "quarter", "annual"}
        and cash.get("year_ago_period_basis", cash.get("period_basis")) == cash.get("period_basis")
        and _known_unit(cash.get("unit")) and cash.get("year_ago_unit", cash.get("unit")) == cash.get("unit"))
    if (r3 is not None and r12 is not None and r3 > max(r12, 0) and recent.get("complete") is True and longer.get("complete") is True
        and current is not None and prior is not None and current < 0 < prior and comparable
        and revenue_end is not None and longer_end is not None
        and (revenue_end.year, revenue_end.month) == (longer_end.year, longer_end.month) == (end.year, end.month)):
        basis = {"year_to_date": "年初累計", "quarter": "單季", "annual": "全年"}[cash["period_basis"]]
        add("summary", f"截至{end:%Y-%m}，近三月營收同比{_pct(r3)}強於近十二月{_pct(r12)}；{basis}營業現金流由正轉負，呈成長與現金流背離。")


def build_report_insights(payload) -> dict[str, list[str]]:
    """Read frozen values and compact context; return six deduplicated text lists."""
    output = {key: [] for key in _KEYS}
    seen = set()
    note_groups, note_seen, note_flags = [[], [], []], set(), set()

    def add(key, sentence, *, topic=0, kind=None):
        if key == "data_notes":
            if kind:
                note_flags.add(kind)
            normalized = sentence.rstrip("。；")
            if normalized and normalized not in note_seen:
                note_seen.add(normalized)
                note_groups[topic].append(normalized)
            return
        if sentence and sentence not in seen:
            output[key].append(sentence)
            seen.add(sentence)

    asof = _day(payload.get("asof"))
    code = str((payload.get("stock") or {}).get("stock_id", ""))
    metrics = {}
    for item in (payload.get("report_comparison") or {}).get("metrics") or []:
        if not isinstance(item, Mapping) or not item.get("id"):
            continue
        end = _metric_end(item)
        if item.get("stock_id") is not None and str(item["stock_id"]) != code:
            continue
        if end is not None and asof is not None and end > asof:
            continue
        metrics[str(item["id"])] = item
    features, feature_dates = {}, {}
    unknown_feature_units = []
    for item in payload.get("feature_values") or []:
        if not isinstance(item, Mapping) or not item.get("name"):
            continue
        name = str(item["name"])
        if name in {"return_5", "return_20", "return_60", "volatility_20", "volatility_60", "volume_ratio_20"}:
            expected_unit = "倍" if name == "volume_ratio_20" else "比例"
            if item.get("unit") != expected_unit:
                if _number(item.get("value")) is not None:
                    unknown_feature_units.append(name)
                continue
        features[name] = item.get("value")
        feature_dates[name] = _day(_first(item, ("asof", "date", "data_date", "signal_date")) or payload.get("asof"))
    if unknown_feature_units:
        add("data_notes", "", kind="feature_unit")
    _summary_insights(metrics, features, feature_dates, payload.get("asof"), add)
    _technical_insights(payload, features, add)
    _fundamental_insights(metrics, add)
    _flow_insights(metrics, add)
    context = payload.get("report_context") or {}
    if context and (context.get("stock_id") != code or context.get("asof") != payload.get("asof")):
        add("data_notes", "補充背景的股票或資料日與本報告不一致，未引用該背景。", topic=1)
        context = {}
    source_names = {}
    news_candidates = []
    for source in context.get("sources") or []:
        route = source.get("route_id")
        source_names[route] = _text(source.get("name_zh")) or str(route)
        if route in _NEWS:
            for record in source.get("records") or []:
                title = normalize_news_title(record.get("title"), record.get("publisher"))
                day = _day(record.get("date"))
                if title and day is not None and (asof is None or day <= asof):
                    news_candidates.append((day.isoformat(), title))
    _macro_insights(context.get("sources") or [], asof, add)
    unique_news = sorted(set(news_candidates), reverse=True)
    if unique_news:
        add("context", "本股或產業相關新聞：" + "；".join(f"{day}「{title}」" for day, title in unique_news[:2]) + "。")
    grouped, unmapped_reasons = defaultdict(dict), set()
    for status in context.get("statuses") or []:
        if isinstance(status, Mapping) and status.get("status") not in {"available", "not_selected", "not-selected", "not_applicable", "not-applicable", "N/A", "n/a", "confirmed_no_event", None}:
            grouped[str(status["status"])][status.get("route_id")] = source_names.get(status.get("route_id"), str(status.get("route_id")))
            if status.get("status") == "unmapped_fields" and _text(status.get("reason_zh")):
                unmapped_reasons.add(_text(status["reason_zh"]))
    for status in sorted(grouped):
        names = sorted(set(grouped[status].values()))
        label = "、".join(names)
        if status == "unknown_unit":
            add("data_notes", "", kind="unknown_unit")
        elif status == "unknown_publication_time":
            add("data_notes", f"{len(grouped[status])}個來源的具時區歷史可用時點未核實，回測仍有時點偏誤風險。", topic=2)
        elif status == "insufficient_comparable_history":
            add("data_notes", f"{label}的部分序列缺少同口徑前值。")
        elif status == "partial_observations":
            if "official_finance_reporting_deadline" in grouped[status]:
                add("data_notes", "本股申報時程有明確缺期，表示時程觀測不足，不代表財報數值缺失。", topic=1)
                other_names = sorted({name for route, name in grouped[status].items() if route != "official_finance_reporting_deadline"})
                label = "、".join(other_names)
            if label:
                add("data_notes", f"{label}存在局部觀測或欄位缺口，已保留可讀部分。", topic=1)
        elif status == "no_matching_observations_unconfirmed":
            add("data_notes", f"{label}本次無適格觀測，尚未證實無事件。", topic=1)
        elif status == "missing_source":
            add("data_notes", f"本次未提供來源：{label}。", topic=1)
        else:
            reason = "；".join(sorted(unmapped_reasons)) if status == "unmapped_fields" and unmapped_reasons else _REASONS.get(status, "來源狀態尚待確認。")
            add("data_notes", f"{label}：{reason}", topic=1)
    if context.get("sources") or any(item.get("selected") is False and item.get("category") in {"基本面", "財務", "營收"} for item in payload.get("feature_values") or [] if isinstance(item, Mapping)):
        add("data_notes", "補充背景非模型輸入，亦不代表本股因果。", topic=2)
    if "unknown_unit" in note_flags:
        note_groups[0].insert(0, "部分數值單位未核實，保留原值，不作跨來源換算或占比")
    if "feature_unit" in note_flags:
        note_groups[0].append("部分價量特徵的單位未明示為比例或倍數，未予換算")
    titles = ("單位與期間可比性", "觀測與分類缺口", "歷史可用時點與回測限制")
    output["data_notes"] = [title + "：" + "；".join(clauses) + "。" for title, clauses in zip(titles, note_groups) if clauses]
    return output
