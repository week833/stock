"""Read pinned published raw prices into a project-local research cache."""
from __future__ import annotations

import calendar as calendar_module
import datetime as dt
import json
import hashlib
from pathlib import Path

import duckdb
import pandas as pd
import numpy as np

READER_VERSION = "raw-pit-demand-v3"


def _date(value):
    text = str(value or "").strip()
    try:
        if len(text) == 7 and text.isdigit():
            return dt.date(int(text[:3]) + 1911, int(text[3:5]), int(text[5:7])).isoformat()
        return dt.date.fromisoformat(text[:10]).isoformat()
    except ValueError:
        return None


def _quoted(value):
    return "'" + str(value).replace("'", "''") + "'"


def _file_identity(path):
    digest = hashlib.sha256()
    size = 0
    with Path(path).open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            size += len(block)
            digest.update(block)
    return {"parquet_size_bytes": size, "parquet_sha256": digest.hexdigest()}


def _validated_demand_cache(cache_path, counts_path, cache_identity):
    cache_path, counts_path = Path(cache_path), Path(counts_path)
    if not cache_path.is_file() or not counts_path.is_file():
        return False, None, "derived_cache_missing"
    try:
        counts = json.loads(counts_path.read_text(encoding="utf-8"))
        if counts.get("cache_identity") != cache_identity:
            return False, None, "cache_identity_mismatch"
        if any(counts.get(name) != value for name, value in _file_identity(cache_path).items()):
            return False, None, "derived_parquet_identity_mismatch"
        return True, counts, None
    except (OSError, json.JSONDecodeError, TypeError, ValueError) as exc:
        return False, None, "derived_cache_manifest_invalid:" + str(exc)[:200]


def calendar_contract(rows) -> dict:
    """Interpret published calendar semantics without inventing historical sessions."""
    opened, closed, conflicts = set(), set(), set()
    references = {}
    for row in rows:
        day = _date(row.get("date") or row.get("Date"))
        if not day:
            continue
        text = str(row.get("Name") or "") + " " + str(row.get("Description") or "")
        trading_marker = any(word in text for word in ("開始交易", "最後交易"))
        closed_marker = (row.get("source_id") == "calendar_holidays"
                         and any(word in text for word in ("休市", "放假", "補假", "無交易")))
        is_open = (row.get("is_open") is True or str(row.get("status", "")).lower() == "open"
                   or (row.get("source_id") == "calendar_sessions" and bool(row.get("Date")))
                   or trading_marker)
        is_closed = row.get("is_open") is False or closed_marker
        if is_open:
            opened.add(day)
        if is_closed:
            closed.add(day)
        if is_open and is_closed:
            conflicts.add(day)
        for key in ("next_session", "next_trading_date"):
            nxt = _date(row.get(key))
            if nxt:
                references.setdefault(day, set()).add(nxt)
    conflicts.update(opened & closed)
    opened.difference_update(conflicts)
    closed.difference_update(conflicts)
    trusted, rejected = {}, []
    for day, values in sorted(references.items()):
        reasons = []
        if len(values) != 1:
            reasons.append("multiple_next_sessions")
        if any(nxt <= day for nxt in values):
            reasons.append("next_not_after_date")
        if day in closed or any(nxt in closed for nxt in values):
            reasons.append("closed_endpoint")
        if day in conflicts or any(nxt in conflicts for nxt in values):
            reasons.append("conflicting_endpoint")
        if any(any(day < session < nxt for session in opened) for nxt in values):
            reasons.append("skips_known_open_session")
        if reasons:
            rejected.append(dict(date=day, next_dates=sorted(values), reasons=reasons))
        else:
            trusted[day] = next(iter(values))
    return dict(trusted_next_sessions=trusted, open_dates=sorted(opened),
                closed_dates=sorted(closed), conflict_dates=sorted(conflicts), rejected=rejected)


def _next_session(rows, asof, warnings):
    contract = calendar_contract(rows)
    explicit = contract["trusted_next_sessions"].get(asof)
    if explicit:
        return explicit, "published_explicit"
    if any(item["date"] == asof for item in contract["rejected"]):
        warnings.append("忽略上游日曆矛盾的 next_session，改用明示推算。")
    holidays = set(contract["closed_dates"])
    day = dt.date.fromisoformat(asof) + dt.timedelta(days=1)
    while day.weekday() >= 5 or day.isoformat() in holidays:
        day += dt.timedelta(days=1)
    warnings.append("次交易日按平日減已發布假日推算，未涵蓋未知臨時休市。")
    return day.isoformat(), "estimated_weekday_minus_published_holidays"


def check_inputs(project_dir: Path, source_root: Path = Path(r"D:\TWStockData\shared_market_data")) -> dict:
    from supplemental_features import SUPPLEMENTAL_ROUTES

    source_root = Path(source_root).resolve()
    head_path = source_root / "_metadata" / "stock_data_current.json"
    head_bytes = head_path.read_bytes()
    head = json.loads(head_bytes.decode("utf-8-sig"))
    if head.get("schema") != "stock-data-publication-v1":
        raise ValueError(f"不支援發布格式：{head_path}")
    entries = {entry["route_id"]: entry for entry in head.get("datasets", [])}
    required = {"official_market_daily", "finmind_stock_info", "official_calendar"}
    descriptors, optional_errors, optional_source_metadata = {}, {}, {}
    for route in [*sorted(required), "finmind_trading_daily_report", *SUPPLEMENTAL_ROUTES]:
        try:
            if route not in entries:
                raise ValueError(f"已發布 head 缺少 {route}")
            descriptor = dict(entries[route])
            descriptor["source_relative_path"] = descriptor.get("relative_path", descriptor.get("path"))
            path = (source_root / descriptor["path"]).resolve()
            if not path.is_relative_to(source_root) or not path.is_file():
                raise ValueError(f"發布路徑無效：{route}")
            if path.stat().st_size != descriptor.get("size"):
                raise ValueError(f"發布物件 size 不一致：{route}")
            descriptor["path"] = str(path)
            descriptors[route] = descriptor
        except (OSError, KeyError, ValueError) as exc:
            if route in required:
                raise
            optional_errors[route] = str(exc)
            optional_source_metadata[route] = dict(entries.get(route, {}))
    return dict(source_head=str(head_path), run_id=head.get("run_id"), published_at=head.get("published_at"),
                snapshot_fingerprint=hashlib.sha256(head_bytes).hexdigest(), descriptors=descriptors,
                optional_errors=optional_errors, optional_source_metadata=optional_source_metadata, hash_verification="pinned_descriptor_not_content_rehash")


PROOF_COLUMNS = ["available_at", "published_at", "publication_time_verified", "availability_basis", "price_basis",
    "source_id", "reference_price", "previous_reference_price", "upper_limit", "lower_limit",
    "price_rule_verified", "price_exception_status", "rule_effective_session", "rule_source",
    "next_reference_price", "next_reference_session", "next_reference_published_at", "open_is_proxy"]


def _true(value):
    return (isinstance(value, (bool, np.bool_)) and bool(value)) or (isinstance(value, str) and value.casefold() == "true")


def _text(value, default=""):
    return default if value is None or pd.isna(value) else str(value)


def _aware(value):
    value = pd.to_datetime(value, errors="coerce")
    return value if not pd.isna(value) and value.tzinfo is not None else None


def build_universe(records):
    """Unknown classification stays research; code length never proves eligibility."""
    frame = pd.DataFrame(records)
    required = {"stock_id", "date", "stock_name", "industry_category"}
    if not required.issubset(frame) or not ({"market", "type"} & set(frame)):
        raise ValueError("finmind_stock_info 缺少代碼、日期、名稱、類別或市場欄位。")
    frame["stock_id"] = frame["stock_id"].astype(str)
    frame["date"] = frame["date"].map(_date)
    frame["market"] = frame.get("market", frame.get("type")).fillna("").astype(str).str.upper()
    frame = frame[frame["date"].notna()].copy()
    # A code whose newest snapshot has no TWSE/TPEX row (e.g. back to 興櫃) is not listed now.
    newest = frame[frame["date"].eq(frame.groupby("stock_id")["date"].transform("max"))]
    listed = set(newest.loc[newest["market"].isin(["TWSE", "TPEX"]), "stock_id"])
    frame = frame[frame["market"].isin(["TWSE", "TPEX"]) & frame["stock_id"].isin(listed)].copy()
    frame = frame[frame["date"].eq(frame.groupby(["market", "stock_id"])["date"].transform("max"))]
    output = []
    for (market, code), rows in frame.groupby(["market", "stock_id"], sort=True):
        name = str(rows.iloc[0]["stock_name"] or "")
        industries = " / ".join(sorted(set(rows["industry_category"].fillna("").astype(str))))
        types = {_text(row.get("security_type")).casefold() for _, row in rows.iterrows()}
        excluded = any(item in {"etf", "etn", "preferred_share", "depositary_receipt", "warrant", "fund"} for item in types)
        excluded |= bool(pd.Series([industries]).str.contains("ETF|ETN|存託憑證|受益證券", case=False, regex=True).iloc[0])
        # Preferred names put the series before 特 (乙特/甲特); warrants end in 購/售 plus a series.
        excluded |= bool(pd.Series([name]).str.contains(
            r"特別股|特[甲乙丙丁戊己]|特[0-9A-Z]|[甲乙丙丁戊己庚辛壬癸]特|[購售]\d{2}$", regex=True).iloc[0])
        # A letter-suffixed four-digit code is a preferred-share series (2881A, 2887Z1).
        excluded |= bool(pd.Series([code]).str.fullmatch(r"\d{4}[A-Z]\d?").iloc[0])
        # Codes beginning with 0 are ETF/ETN/beneficiary securities even when industry is blank.
        excluded |= code.startswith("0")
        if excluded or not code.strip():
            continue
        proofs = []
        for _, row in rows.iterrows():
            declared = _true(row.get("is_ordinary_share")) or _text(row.get("security_type")).casefold() in {"ordinary_share", "ordinary_equity", "common_stock"}
            source = _text(row.get("classification_source"))
            effective = _date(row.get("classification_effective_date"))
            proofs.append((declared and _true(row.get("classification_verified")) and source.casefold() not in {"", "unknown", "unverified"} and bool(effective) and effective <= row["date"], source, effective))
        verified = bool(proofs) and all(item[0] for item in proofs) and len(set(proofs)) == 1
        listing_dates = {_date(row.get("listing_session")) for _, row in rows.iterrows()
                         if _true(row.get("listing_session_verified")) and _text(row.get("listing_source")) and _date(row.get("listing_session"))}
        output.append(dict(stock_id=code, market=market, name=name, industry=industries,
            ordinary_share_verified=verified, eligibility_status="verified_ordinary_share" if verified else "research_unverified",
            eligibility_reason="explicit_classification_evidence" if verified else "ordinary_share_classification_evidence_missing_or_conflicting",
            classification_source=proofs[0][1] if verified else "unknown",
            classification_effective_date=proofs[0][2] if verified else None,
            listing_session=next(iter(listing_dates)) if len(listing_dates) == 1 else None))
    result = pd.DataFrame(output)
    if result.empty:
        raise ValueError("已發布分類無可用 TWSE/TPEX 研究股票池。")
    return result


def qualify_prices(prices, universe, calendar, *, raw_descriptor, snapshot_id, legacy=False):
    """Vectorized qualification over the already projected frame, without row-dict copies."""
    prices = prices.copy()
    def text_column(name):
        return prices[name].fillna("").astype(str) if name in prices else pd.Series("", index=prices.index)
    source_available = text_column("available_at")
    source_available = source_available.where(source_available.ne(""), text_column("published_at"))
    aware = source_available.str.contains(r"(?:Z|[+-]\d{2}:\d{2})$", regex=True)
    publication = pd.to_datetime(source_available.where(aware), errors="coerce", utc=True, format="mixed")
    basis = text_column("availability_basis").replace("", "unknown")
    verified = (~pd.isna(publication) & text_column("publication_time_verified").str.lower().eq("true")
        & basis.isin(["official_published_at", "verified_published_at", "publication_time_verified"]))
    raw = text_column("price_basis").str.lower().isin(["raw", "unadjusted", "raw_unadjusted", "official_raw"])
    raw &= ~text_column("open_is_proxy").str.lower().eq("true")
    if legacy:
        verified[:] = False
        raw[:] = False
    day = prices["date"].astype(str).str[:10]
    cutoff = pd.to_datetime(day + "T22:00:00+08:00", errors="coerce", utc=True)
    on_time = verified & publication.le(cutoff)
    nxt = day.map(calendar.get("trusted_next_sessions", {}))
    opened = set(calendar.get("open_dates", []))
    calendar_verified = day.isin(opened) & nxt.isin(opened) & nxt.notna()
    info = universe.set_index(["market", "stock_id"])
    identities = pd.MultiIndex.from_frame(prices[["market", "stock_id"]])
    classifications = info.reindex(identities)
    classifications.index = prices.index
    effective = classifications["classification_effective_date"].fillna("").astype(str)
    ordinary = classifications["ordinary_share_verified"].fillna(False).astype(bool) & effective.ne("") & effective.le(day)
    rules = [pd.to_numeric(text_column(name), errors="coerce") for name in ("reference_price", "upper_limit", "lower_limit")]
    rule = text_column("price_rule_verified").str.lower().eq("true") & ~text_column("rule_source").str.lower().isin(["", "unknown", "unverified"])
    for values in rules:
        rule &= np.isfinite(values) & values.gt(0)
    rule &= rules[2].le(rules[0]) & rules[0].le(rules[1])
    rule &= text_column("rule_effective_session").eq(nxt) & text_column("price_exception_status").isin(["verified_standard", "verified_exception"])
    if legacy:
        rule[:] = False
    prices["available_at"] = publication.map(lambda value: value.isoformat() if pd.notna(value) else None)
    prices["publication_time_verified"] = verified
    prices["availability_basis"] = basis
    declared_basis = text_column("price_basis").str.lower().replace("", "unknown")
    prices["declared_price_basis"] = declared_basis
    prices["known_nonraw"] = ~declared_basis.isin(["raw", "unadjusted", "raw_unadjusted", "official_raw", "unknown", "unverified"]) | text_column("open_is_proxy").str.lower().eq("true")
    fallback_basis = declared_basis.where(prices["known_nonraw"], "unknown")
    prices["price_basis"] = np.where(raw, "raw", fallback_basis)
    prices["source_id"] = text_column("source_id").replace("", raw_descriptor.get("route_id", "unknown"))
    prices["source_hash"] = raw_descriptor.get("sha256")
    prices["source_snapshot_id"] = snapshot_id
    prices["source_status"] = raw_descriptor.get("status", "unknown")
    prices["publication_available_by_t22"] = on_time
    prices["calendar_next_session"] = nxt.where(nxt.notna(), None)
    prices["calendar_next_verified"] = calendar_verified
    prices["ordinary_share_verified"] = ordinary
    prices["classification_source"] = classifications["classification_source"].fillna("unknown")
    prices["classification_effective_date"] = effective.where(effective.ne(""), None)
    prices["rule_verified"] = rule
    prices["core_qualification"] = np.where(on_time & raw & calendar_verified & ordinary & rule, "verified", "research_unverified")
    reasons = pd.Series("", index=prices.index)
    for okay, reason in [(verified, "publication_unverified;"), (on_time | ~verified, "publication_after_t22;"), (raw, "raw_basis_unverified;"),
            (calendar_verified, "official_next_session_unverified;"), (ordinary, "ordinary_share_unverified;"), (rule, "price_rule_unverified;")]:
        reasons += np.where(okay, "", reason)
    prices["qualification_reason"] = reasons.str.rstrip(";")
    prices.attrs["reader_version"] = READER_VERSION
    prices.attrs["proof_origin"] = "legacy_cache_missing_proof" if legacy else "pinned_row_fields"
    return prices

def _project_daily(con, raw, universe, cache_path, counts_path):
    before = Path(raw["path"]).stat()
    from progress import stage

    with stage(f"擷取近五年行情（來源約 {before.st_size / (1024 ** 3):.1f} GB）") as progress:
        con.register("candidate_pool", universe[["market", "stock_id"]])
        columns = {name: "VARCHAR" for name in ["stock_id", "date", "market", "name", "open", "high", "low", "close", "volume", "volume_unit", *PROOF_COLUMNS]}
        con.read_json(raw["path"], format="array", columns=columns).create_view("raw_input")
        end = _date(raw.get("actual_max_date"))
        if not end:
            raise ValueError("raw descriptor 缺少有效 actual_max_date。")
        end_day = dt.date.fromisoformat(end)
        start = end_day.replace(year=end_day.year - 5, day=min(end_day.day, calendar_module.monthrange(end_day.year - 5, end_day.month)[1])).isoformat()
        numeric = ", ".join(f"try_cast(replace(r.{name}, ',', '') AS DOUBLE) AS {name}" for name in ["open", "high", "low", "close", "volume"])
        proof_sql = ", ".join("r." + name for name in PROOF_COLUMNS)
        con.execute(f"""CREATE TEMP TABLE selected AS SELECT r.stock_id, substr(r.date,1,10) AS date,
            upper(r.market) AS market, r.name, {numeric}, r.volume_unit, {proof_sql}
            FROM raw_input r JOIN candidate_pool p ON r.stock_id=p.stock_id AND upper(r.market)=p.market
            WHERE substr(r.date,1,10) BETWEEN ? AND ?""", [start, end])
        progress.advance(detail=f"擷取範圍 {start} 至 {end}")

    with stage("檢查行情與重複資料") as progress:
        after = Path(raw["path"]).stat()
        if (before.st_size, before.st_mtime_ns, before.st_ctime_ns) != (after.st_size, after.st_mtime_ns, after.st_ctime_ns):
            raise ValueError("pinned daily object changed while projecting")
        total = con.execute("SELECT count(*) FROM selected").fetchone()[0]
        unknown = con.execute("SELECT count(*) FROM selected WHERE volume_unit IS NULL OR volume_unit NOT IN ('股','千股')").fetchone()[0]
        con.execute(f"""CREATE TEMP TABLE valid AS SELECT date,stock_id,market,name,open,high,low,close,
            volume * CASE volume_unit WHEN '千股' THEN 1000 ELSE 1 END AS volume, {', '.join(PROOF_COLUMNS)}
            FROM selected WHERE try_cast(date AS DATE) IS NOT NULL AND volume_unit IN ('股','千股') AND volume>0
            AND open>0 AND high>0 AND low>0 AND close>0
            AND isfinite(open) AND isfinite(high) AND isfinite(low) AND isfinite(close) AND isfinite(volume)
            AND high>=greatest(open,close) AND low<=least(open,close) AND high>=low""")
        valid_count = con.execute("SELECT count(*) FROM valid").fetchone()[0]
        con.execute("""CREATE TEMP TABLE conflicts AS SELECT market,stock_id,date FROM
            (SELECT DISTINCT market,stock_id,date,open,high,low,close,volume FROM valid)
            GROUP BY market,stock_id,date HAVING count(*)>1""")
        counts = dict(selected_rows=total, invalid_rows=total-valid_count, unknown_volume_unit_rows=unknown,
            conflicting_keys=con.execute("SELECT count(*) FROM conflicts").fetchone()[0],
            conflicting_rows=con.execute("SELECT count(*) FROM valid JOIN conflicts USING(market,stock_id,date)").fetchone()[0])
        progress.advance(detail=f"來源 {total:,} 筆，有效 {valid_count:,} 筆")

    with stage("寫入行情快取") as progress:
        # Conflicting or partially missing duplicate proof cannot become verified.
        proof_aggregates = ", ".join(f"CASE WHEN count(*)=count(v.{name}) AND count(DISTINCT v.{name})=1 THEN min(v.{name}) ELSE NULL END AS {name}" for name in PROOF_COLUMNS)
        temporary = cache_path.with_suffix(".parquet.tmp")
        con.execute(f"""COPY (SELECT v.date,v.stock_id,v.market,min(v.name) AS name,v.open,v.high,v.low,v.close,v.volume,
            {proof_aggregates} FROM valid v ANTI JOIN conflicts c USING(market,stock_id,date)
            GROUP BY v.date,v.stock_id,v.market,v.open,v.high,v.low,v.close,v.volume)
            TO {_quoted(temporary)} (FORMAT PARQUET)""")
        temporary.replace(cache_path)
        temporary_counts = counts_path.with_suffix(".json.tmp")
        temporary_counts.write_text(json.dumps(counts, ensure_ascii=False), encoding="utf-8")
        temporary_counts.replace(counts_path)
        progress.advance(detail=f"已寫入 {counts['selected_rows']:,} 筆來源列統計")
    return counts


def _query_shards_view(con, result, view_name):
    """Open only selected, already hash-verified physical projection shards."""
    shards = list(result.iter_verified_shards())
    if not shards:
        return [], 0
    paths = [item["path"] for item in shards]
    con.read_json(
        paths,
        format="newline_delimited",
        columns={
            "__sequence": "UBIGINT",
            "__date": "VARCHAR",
            "__stock": "VARCHAR",
            "row": "JSON",
        },
    ).create_view(view_name)
    return shards, sum(int(item["size_bytes"]) for item in shards)


def _create_raw_price_view(con, result):
    shards, projection_bytes = _query_shards_view(con, result, "price_projection_envelopes")
    if not shards:
        return shards, projection_bytes
    expressions = [
        "coalesce(json_extract_string(p.\"row\", '$.stock_id'), p.__stock) AS stock_id",
        "coalesce(json_extract_string(p.\"row\", '$.date'), p.__date) AS date",
        "upper(json_extract_string(p.\"row\", '$.market')) AS market",
        "json_extract_string(p.\"row\", '$.name') AS name",
        *[
            "try_cast(replace(trim(json_extract_string(p.\"row\", '$.{0}')), ',', '') AS DOUBLE) AS {0}".format(name)
            for name in ("open", "high", "low", "close", "volume")
        ],
        "json_extract_string(p.\"row\", '$.volume_unit') AS volume_unit",
        *[
            "json_extract_string(p.\"row\", '$.{0}') AS {0}".format(name)
            for name in PROOF_COLUMNS
        ],
    ]
    con.execute(
        "CREATE TEMP VIEW raw_projection_rows AS SELECT "
        + ", ".join(expressions)
        + " FROM price_projection_envelopes p"
    )
    return shards, projection_bytes


def _valid_market_latest_from_projection(result, universe):
    import duckdb

    con = duckdb.connect()
    try:
        con.register("candidate_pool", universe[["market", "stock_id"]])
        shards, projection_bytes = _create_raw_price_view(con, result)
        if not shards:
            return {}, 0, projection_bytes
        con.execute("""CREATE TEMP TABLE valid_price_dates AS
            SELECT substr(r.date,1,10) AS date, upper(r.market) AS market,
                   cast(r.stock_id AS VARCHAR) AS stock_id,
                   r.open,r.high,r.low,r.close,
                   r.volume * CASE r.volume_unit WHEN '千股' THEN 1000 ELSE 1 END AS volume
            FROM raw_projection_rows r JOIN candidate_pool p
              ON cast(r.stock_id AS VARCHAR)=cast(p.stock_id AS VARCHAR)
             AND upper(r.market)=upper(p.market)
            WHERE try_cast(r.date AS DATE) IS NOT NULL
              AND r.volume_unit IN ('股','千股') AND r.volume>0
              AND r.open>0 AND r.high>0 AND r.low>0 AND r.close>0
              AND isfinite(r.open) AND isfinite(r.high) AND isfinite(r.low)
              AND isfinite(r.close) AND isfinite(r.volume)
              AND r.high>=greatest(r.open,r.close)
              AND r.low<=least(r.open,r.close) AND r.high>=r.low""")
        con.execute("""CREATE TEMP TABLE asof_conflicts AS SELECT market,stock_id,date FROM
            (SELECT DISTINCT market,stock_id,date,open,high,low,close,volume FROM valid_price_dates)
            GROUP BY market,stock_id,date HAVING count(*)>1""")
        con.execute("""CREATE TEMP TABLE asof_usable_dates AS
            SELECT DISTINCT v.market,v.date FROM valid_price_dates v
            ANTI JOIN asof_conflicts c USING(market,stock_id,date)""")
        count = int(con.execute("SELECT count(*) FROM asof_usable_dates").fetchone()[0])
        rows = con.execute(
            "SELECT market,max(date) FROM asof_usable_dates GROUP BY market"
        ).fetchall()
        result.record_consumer_scan(
            rows_scanned=count, projection_bytes_read=projection_bytes)
        return {str(market): str(day)[:10] for market, day in rows}, count, projection_bytes
    finally:
        con.close()


def _legacy_origin_start(raw_descriptor):
    end = _date(raw_descriptor.get("actual_max_date"))
    if not end:
        raise ValueError("raw descriptor 缺少有效 actual_max_date。")
    end_day = dt.date.fromisoformat(end)
    origin = end_day.replace(
        year=end_day.year - 5,
        day=min(end_day.day, calendar_module.monthrange(end_day.year - 5, end_day.month)[1]),
    )
    return origin.isoformat(), end


def _published_price_query(source_root, raw, universe, *, start_date, end_date,
                           snapshot_fingerprint, source_identity):
    from stock_query_projection import query_published_rows

    descriptor = dict(raw)
    relative_path = descriptor.get("source_relative_path")
    if not relative_path:
        raise ValueError("pinned route 缺少相對於 publication root 的路徑。")
    descriptor["path"] = relative_path
    descriptor["relative_path"] = relative_path
    return query_published_rows(
        source_root,
        descriptor,
        "official_market_daily",
        stock_ids=universe["stock_id"].astype(str).drop_duplicates().tolist(),
        start_date=start_date,
        end_date=end_date,
        source_identity=source_identity,
        parent_identity=snapshot_fingerprint,
        field_map={"stock_field": "stock_id", "date_field": "date"},
    )


def _discover_asof(source_root, raw, universe, *, snapshot_fingerprint,
                   source_identity, legacy_origin_start):
    """Read a bounded tail first and expand only if that tail has no valid close."""
    end = _date(raw.get("actual_max_date"))
    if not end:
        raise ValueError("raw descriptor 缺少有效 actual_max_date。")
    end_day = dt.date.fromisoformat(end)
    span_days = 1
    accumulated = []
    while True:
        start_day = max(
            dt.date.fromisoformat(legacy_origin_start),
            end_day - dt.timedelta(days=span_days),
        )
        result = _published_price_query(
            source_root, raw, universe, start_date=start_day.isoformat(),
            end_date=end, snapshot_fingerprint=snapshot_fingerprint,
            source_identity=source_identity,
        )
        latest, valid_rows, projection_bytes = _valid_market_latest_from_projection(
            result, universe)
        accumulated.append({
            "status": result.status,
            "metrics": dict(result.metrics),
            "start_date": start_day.isoformat(),
            "end_date": end,
            "valid_rows": valid_rows,
            "selected_projection_bytes": projection_bytes,
        })
        found_both = {"TWSE", "TPEX"}.issubset(latest)
        exhausted = start_day.isoformat() == legacy_origin_start
        if found_both or exhausted:
            if not latest:
                raise ValueError("已發布近五年 raw 資料無有效研究行情。")
            return min(latest.values()), latest, accumulated
        span_days = min(span_days * 2, (end_day - dt.date.fromisoformat(legacy_origin_start)).days)


def _project_daily_from_query(con, raw, universe, result, cache_path, counts_path,
                              *, start_date, end_date, cache_identity):
    from progress import stage

    con.register("candidate_pool", universe[["market", "stock_id"]])
    shards, projection_bytes = _create_raw_price_view(con, result)
    if not shards:
        raise ValueError("published price query selected no verified shards")
    total_source_rows = int(con.execute("SELECT count(*) FROM raw_projection_rows").fetchone()[0])
    con.execute("""CREATE TEMP TABLE selected AS SELECT r.stock_id,
        substr(r.date,1,10) AS date, upper(r.market) AS market, r.name,
        r.open,r.high,r.low,r.close,r.volume,r.volume_unit,
        available_at,published_at,publication_time_verified,availability_basis,price_basis,
        source_id,reference_price,previous_reference_price,upper_limit,lower_limit,
        price_rule_verified,price_exception_status,rule_effective_session,rule_source,
        next_reference_price,next_reference_session,next_reference_published_at,open_is_proxy
        FROM raw_projection_rows r JOIN candidate_pool p
          ON cast(r.stock_id AS VARCHAR)=cast(p.stock_id AS VARCHAR)
         AND upper(r.market)=upper(p.market)
        WHERE try_cast(substr(r.date,1,10) AS DATE) BETWEEN ?::DATE AND ?::DATE""",
        [start_date, end_date])
    total = int(con.execute("SELECT count(*) FROM selected").fetchone()[0])
    unknown = int(con.execute(
        "SELECT count(*) FROM selected WHERE volume_unit IS NULL OR volume_unit NOT IN ('股','千股')"
    ).fetchone()[0])
    con.execute(f"""CREATE TEMP TABLE valid AS SELECT date,stock_id,market,name,open,high,low,close,
        volume * CASE volume_unit WHEN '千股' THEN 1000 ELSE 1 END AS volume,
        {', '.join(PROOF_COLUMNS)}
        FROM selected WHERE try_cast(date AS DATE) IS NOT NULL
          AND volume_unit IN ('股','千股') AND volume>0
          AND open>0 AND high>0 AND low>0 AND close>0
          AND isfinite(open) AND isfinite(high) AND isfinite(low)
          AND isfinite(close) AND isfinite(volume)
          AND high>=greatest(open,close) AND low<=least(open,close) AND high>=low""")
    valid_count = int(con.execute("SELECT count(*) FROM valid").fetchone()[0])
    con.execute("""CREATE TEMP TABLE conflicts AS SELECT market,stock_id,date FROM
        (SELECT DISTINCT market,stock_id,date,open,high,low,close,volume FROM valid)
        GROUP BY market,stock_id,date HAVING count(*)>1""")
    counts = dict(
        selected_rows=total,
        invalid_rows=total-valid_count,
        unknown_volume_unit_rows=unknown,
        conflicting_keys=int(con.execute("SELECT count(*) FROM conflicts").fetchone()[0]),
        conflicting_rows=int(con.execute(
            "SELECT count(*) FROM valid JOIN conflicts USING(market,stock_id,date)"
        ).fetchone()[0]),
        source_projection_rows=total_source_rows,
    )
    coverage = con.execute("""SELECT min(session_count) AS minimum_distinct_sessions,
        median(session_count) AS median_distinct_sessions,
        max(session_count) AS maximum_distinct_sessions,
        count(*) FILTER (WHERE session_count=0) AS identities_without_observations,
        count(*) AS candidate_identities
        FROM (SELECT p.market,p.stock_id,count(DISTINCT v.date) AS session_count
            FROM candidate_pool p LEFT JOIN valid v
              ON v.market=p.market AND v.stock_id=p.stock_id
            GROUP BY p.market,p.stock_id) coverage""").fetchone()
    counts["observation_coverage"] = {
        "minimum_distinct_sessions": int(coverage[0] or 0),
        "median_distinct_sessions": float(coverage[1] or 0),
        "maximum_distinct_sessions": int(coverage[2] or 0),
        "identities_without_observations": int(coverage[3] or 0),
        "candidate_identities": int(coverage[4] or 0),
    }
    with stage("寫入依需求切片的行情快取"):
        proof_aggregates = ", ".join(
            f"CASE WHEN count(*)=count(v.{name}) AND count(DISTINCT v.{name})=1 "
            f"THEN min(v.{name}) ELSE NULL END AS {name}"
            for name in PROOF_COLUMNS
        )
        temporary = cache_path.with_suffix(".parquet.tmp")
        con.execute(f"""COPY (SELECT v.date,v.stock_id,v.market,min(v.name) AS name,
            v.open,v.high,v.low,v.close,v.volume,{proof_aggregates}
            FROM valid v ANTI JOIN conflicts c USING(market,stock_id,date)
            GROUP BY v.date,v.stock_id,v.market,v.open,v.high,v.low,v.close,v.volume)
            TO {_quoted(temporary)} (FORMAT PARQUET)""")
        temporary.replace(cache_path)
        result.record_consumer_scan(
            rows_scanned=total_source_rows, projection_bytes_read=projection_bytes)
        counts["query_io"] = dict(result.metrics)
        counts["cache_identity"] = cache_identity
        counts.update(_file_identity(cache_path))
        temporary_counts = counts_path.with_suffix(".json.tmp")
        temporary_counts.write_text(
            json.dumps(counts, ensure_ascii=False), encoding="utf-8")
        temporary_counts.replace(counts_path)
    return counts


def _years_before(day, years):
    value = dt.date.fromisoformat(str(day)[:10])
    target_year = value.year - int(years)
    return value.replace(
        year=target_year,
        day=min(value.day, calendar_module.monthrange(target_year, value.month)[1]),
    ).isoformat()


def _load_price_input_from_demand(project_dir, source_root, checked, raw, master,
                                  universe, cache_dir, demand_context):
    from analysis_data_demand import build_data_plan
    from top10_data_demand_specs import (
        LEGACY_FEATURE_ORIGIN,
        ORDER21_ROUTE,
        PRICE_ROUTE,
        order21_report_requirement,
        top10_feature_specs,
    )
    from order21_reader import order21_retention_window

    origin_start, source_max = _legacy_origin_start(raw)
    origin_policy = (demand_context.get("feature_computation_origin_policy")
                     or LEGACY_FEATURE_ORIGIN["policy"])
    if origin_policy != LEGACY_FEATURE_ORIGIN["policy"]:
        raise ValueError("unsupported_feature_computation_origin_policy:" + str(origin_policy))
    source_relative_path = raw.get("source_relative_path") or raw.get("relative_path")
    if not source_relative_path:
        raise ValueError("pinned price route 缺少 publication root 相對路徑。")
    source_identity = dict(
        route_id=raw.get("route_id", PRICE_ROUTE),
        source_relative_path=source_relative_path,
        sha256=raw.get("sha256"),
        size=raw.get("size"),
    )
    asof, market_latest, bootstrap_reads = _discover_asof(
        source_root,
        raw,
        universe,
        snapshot_fingerprint=checked["snapshot_fingerprint"],
        source_identity=source_identity,
        legacy_origin_start=origin_start,
    )

    from supplemental_features import SUPPLEMENTAL_FEATURE_COLUMNS, SUPPLEMENTAL_ROUTES
    selected = list(dict.fromkeys(demand_context.get("feature_columns") or []))
    if not selected:
        raise ValueError("resolved_model_feature_columns_missing")
    supplemental_columns = set(SUPPLEMENTAL_FEATURE_COLUMNS)
    selected_supplemental = sorted(set(selected) & supplemental_columns)
    selected_core = sorted(set(selected) - supplemental_columns)
    minimum = int(demand_context.get("minimum_feature_sessions", 21))
    desired = int(demand_context.get("desired_feature_lookback_sessions", 179))
    specs = top10_feature_specs(
        selected_core,
        selected_supplemental,
        origin_start=origin_start,
        minimum_feature_sessions=minimum,
        desired_feature_lookback_sessions=desired,
    )
    fit_lookback_years = int(demand_context.get("recipe_lookback_years", 5))
    sample_start = (
        max(origin_start, _years_before(asof, fit_lookback_years))
        if demand_context.get("purpose") == "weekend_candidate_replay"
        else None
    )
    resolved = demand_context["feature_columns"]
    extras = {"routes": {}, "metadata": {
        "source_snapshot_fingerprint": checked["snapshot_fingerprint"],
        "price_source_sha256": raw.get("sha256"),
        "master_source_sha256": master.get("sha256"),
        "universe_fingerprint": hashlib.sha256("\n".join(
            f"{row.market}:{row.stock_id}" for row in universe.sort_values(
                ["market", "stock_id"]).itertuples(index=False)
        ).encode("utf-8")).hexdigest(),
        "feature_computation_origin": dict(LEGACY_FEATURE_ORIGIN,
            start_date=origin_start, end_date=min(source_max, asof)),
        "recipe_lookback_years": (fit_lookback_years if sample_start else None),
        "candidate_recipe_ids": [item.get("recipe_id") for item in
            demand_context.get("candidate_recipes", []) if item.get("recipe_id")],
    }}
    extras["routes"][PRICE_ROUTE] = {
        "unit": "sessions",
        "start_date": origin_start,
        "end_date": asof,
        "all_symbols": True,
        "metadata": {
            "computation_origin_policy": LEGACY_FEATURE_ORIGIN["policy"],
            "computation_origin_start_date": origin_start,
            "computation_origin_anchor": LEGACY_FEATURE_ORIGIN["anchor"],
            "minimum_feature_sessions": minimum,
            "finite_formula_lookback_sessions": specs["formula_lookback_sessions"],
            "persisted_carry_in_state": False,
        },
    }
    resolved_routes = set()
    from analysis_data_demand import resolve_feature_requirements
    resolved_routes.update(resolve_feature_requirements(resolved, specs))
    for route in sorted(resolved_routes - {PRICE_ROUTE}):
        if route not in SUPPLEMENTAL_ROUTES:
            raise ValueError("unsupported_model_data_route:" + route)
        extras["routes"][route] = {
            "unit": "sessions",
            "start_date": origin_start,
            "end_date": asof,
            "all_symbols": True,
            "metadata": {"purpose": "model_or_candidate_feature_history"},
        }
    order21_window = order21_retention_window(asof)
    order21_requirement = order21_report_requirement()
    extras["routes"][ORDER21_ROUTE] = {
        **order21_requirement,
        "metadata": {
            "purpose": "report_only_order21_score",
            "display_windows": order21_requirement["display_windows"],
            "score_history": "four_calendar_month_window",
            "retention_window": order21_window,
        },
    }
    plan = build_data_plan(
        "tw_nextday_top10",
        demand_context.get("purpose", "daily_frozen_inference"),
        asof,
        demand_context.get("model_identity") or {},
        resolved,
        specs,
        sample_start=sample_start,
        extra_requirements=extras,
    )
    universe_fingerprint = extras["metadata"]["universe_fingerprint"]
    cache_identity = {
        "reader_version": READER_VERSION,
        "data_plan_digest": plan["digest"],
        "snapshot_fingerprint": checked["snapshot_fingerprint"],
        "price_sha256": raw.get("sha256"),
        "master_sha256": master.get("sha256"),
        "universe_fingerprint": universe_fingerprint,
        "feature_formula_spec_version": specs["formula_spec_version"],
        "feature_demand_spec_version": specs["spec_version"],
        "origin_policy": LEGACY_FEATURE_ORIGIN,
        "origin_start_date": origin_start,
    }
    cache_digest = hashlib.sha256(json.dumps(
        cache_identity, sort_keys=True, ensure_ascii=False,
        separators=(",", ":"), default=str).encode("utf-8")).hexdigest()
    key = "_".join((READER_VERSION, cache_digest))
    cache_path = cache_dir / f"{key}.parquet"
    counts_path = cache_dir / f"{key}.json"
    cache_hit, counts, cache_invalid_reason = _validated_demand_cache(
        cache_path, counts_path, cache_identity)
    if cache_invalid_reason == "derived_cache_missing":
        cache_invalid_reason = None
    query_metrics = {
        "cache_hit": cache_hit,
        "main_query_skipped": cache_hit,
        "invalid_cache_rebuilt": bool(cache_invalid_reason),
        "invalid_cache_reason": cache_invalid_reason,
    }
    if cache_hit:
        con = duckdb.connect()
        try:
            prices = con.read_parquet(str(cache_path)).df()
        finally:
            con.close()
    else:
        result = _published_price_query(
            source_root,
            raw,
            universe,
            start_date=origin_start,
            end_date=asof,
            snapshot_fingerprint=checked["snapshot_fingerprint"],
            source_identity=source_identity,
        )
        con = duckdb.connect()
        try:
            con.execute("SET memory_limit='2GB'")
            con.execute("SET threads=2")
            cache_dir.mkdir(parents=True, exist_ok=True)
            (cache_dir / "tmp").mkdir(exist_ok=True)
            con.execute("SET temp_directory=" + _quoted(cache_dir / "tmp"))
            counts = _project_daily_from_query(
                con, raw, universe, result, cache_path, counts_path,
                start_date=origin_start, end_date=asof, cache_identity=cache_identity,
            )
            prices = con.read_parquet(str(cache_path)).df()
        finally:
            con.close()
        query_metrics = dict(result.metrics, status=result.status,
            selected_shards=len(result.selected), query_cache_key=result.manifest.get("cache_key"),
            invalid_cache_rebuilt=bool(cache_invalid_reason),
            invalid_cache_reason=cache_invalid_reason)
    prices = prices.loc[prices["date"].astype(str).str[:10].le(asof)].copy()
    return {
        "prices": prices,
        "counts": counts,
        "asof": asof,
        "market_latest": market_latest,
        "origin_start": origin_start,
        "source_max": source_max,
        "cache_path": str(cache_path),
        "cache_hit": cache_hit,
        "legacy": False,
        "plan": plan,
        "plan_inputs": {
            "consumer": "tw_nextday_top10",
            "purpose": demand_context.get("purpose", "daily_frozen_inference"),
            "asof": asof,
            "model_identity": demand_context.get("model_identity") or {},
            "feature_columns": resolved,
            "specs": specs,
            "sample_start": sample_start,
            "extra_requirements": extras,
        },
        "cache_identity": cache_identity,
        "query_metrics": query_metrics,
        "bootstrap_reads": bootstrap_reads,
    }


def load_inputs(project_dir: Path, source_root: Path = Path(r"D:\TWStockData\shared_market_data"),
                namespace_root=None, *, allow_legacy_cache=False, demand_context=None):
    from paths import runtime_paths
    from order21_reader import load_order21_support
    from progress import stage
    from top10_data_demand_specs import ORDER21_ROUTE, PRICE_ROUTE

    project_dir = Path(project_dir)
    source_root = Path(source_root).resolve()
    with stage("檢查已發布來源") as progress:
        checked = check_inputs(project_dir, source_root)
        progress.advance(detail="已確認發布 head 與必要資料集")
    entries = checked["descriptors"]
    raw, master = entries["official_market_daily"], entries["finmind_stock_info"]
    with stage("讀取股票主檔並建立研究股票池") as progress:
        universe = build_universe(json.loads(Path(master["path"]).read_text(encoding="utf-8-sig")))
        progress.advance(detail=f"研究股票池 {len(universe):,} 檔")
    warnings = ["普通股資格僅依明示分類證據；證據缺失保留研究狀態，不以代碼長度證明。",
                "目前股票資訊不保證歷史股票池完整，歷史研究仍可能有存活偏差。"]
    for route, entry in entries.items():
        if entry.get("status") != "SUCCESS":
            warnings.append(f"上游 {route} 狀態為 {entry.get('status')}，保留來源缺口。")
    cache_dir = runtime_paths(project_dir, namespace_root).features
    price_input = None
    if demand_context is not None:
        price_input = _load_price_input_from_demand(
            project_dir, source_root, checked, raw, master, universe,
            cache_dir, demand_context)
        prices = price_input["prices"]
        counts = price_input["counts"]
        asof = price_input["asof"]
        market_latest = price_input["market_latest"]
        cache_path = Path(price_input["cache_path"])
        cache_hit = price_input["cache_hit"]
        legacy = False
    else:
        key = f"{READER_VERSION}_{raw['sha256']}_{master['sha256']}"
        cache_path = cache_dir / f"{key}.parquet"
        counts_path = cache_dir / f"{key}.json"
        legacy_path = project_dir / "data" / "cache" / f"raw-v1_{raw['sha256']}_{master['sha256']}.parquet"
        legacy = bool(allow_legacy_cache and not cache_path.is_file() and legacy_path.is_file())
        if legacy:
            cache_path = legacy_path
            counts_path = legacy_path.with_suffix(".json")
            warnings.append("沿用舊版唯讀行情快取；舊快取未保留的時間及價格基礎證據維持 unknown。")
        cache_hit = cache_path.is_file() and counts_path.is_file()
        if legacy and not cache_hit:
            legacy = False
            cache_path, counts_path = cache_dir / f"{key}.parquet", cache_dir / f"{key}.json"
        con = duckdb.connect()
        try:
            con.execute("SET memory_limit='2GB'")
            con.execute("SET threads=2")
            if not cache_hit:
                cache_dir.mkdir(parents=True, exist_ok=True)
                (cache_dir / "tmp").mkdir(exist_ok=True)
                con.execute("SET temp_directory=" + _quoted(cache_dir / "tmp"))
                with stage("首次建立近五年行情快取") as progress:
                    counts = _project_daily(con, raw, universe, cache_path, counts_path)
                    progress.advance(detail=f"已處理 {counts['selected_rows']:,} 筆來源列")
            else:
                with stage("讀取行情快取統計") as progress:
                    counts = json.loads(counts_path.read_text(encoding="utf-8"))
                    progress.advance()
            read_label = "讀取已命中的行情快取" if cache_hit else "載入剛建立的行情快取"
            with stage(read_label) as progress:
                prices = con.read_parquet(str(cache_path)).df()
                progress.advance(detail=f"已載入 {len(prices):,} 筆行情")
        finally:
            con.close()
        if prices.empty:
            raise ValueError("已發布 raw 資料無有效研究行情。")
        market_latest = prices.groupby("market")["date"].max().to_dict()
        asof = min(market_latest.values())
    if set(market_latest) != {"TWSE", "TPEX"}:
        warnings.append("有效行情缺少其中一個市場，基準日只依現有市場。")
    prices = prices[prices["date"] <= asof].sort_values(["stock_id", "date"]).reset_index(drop=True)
    calendar_rows = json.loads(Path(entries["official_calendar"]["path"]).read_text(encoding="utf-8-sig"))
    if not isinstance(calendar_rows, list):
        raise ValueError("official_calendar 必須為已發布 JSON array。")
    contract = calendar_contract(calendar_rows)
    next_session, next_status = _next_session(calendar_rows, asof, warnings)
    with stage("判定行情資格") as progress:
        prices = qualify_prices(prices, universe, contract, raw_descriptor=raw,
                                snapshot_id=checked["run_id"], legacy=legacy)
        progress.advance(detail=f"已判定 {len(prices):,} 筆行情")
    from supplemental_features import enrich_prices
    with stage("整理法人、融資融券與當沖補充特徵") as progress:
        prices, supplemental_metadata = enrich_prices(
            prices, universe, checked, cache_dir,
            demand_context=demand_context,
            source_root=(source_root if demand_context is not None else None),
            data_plan_digest=(price_input["plan"]["digest"] if price_input else None),
        )
        progress.advance(detail="補充特徵僅載入本次配方或報告所需來源；缺值保留標記")
    metadata = dict(checked, raw_descriptor=raw, cache_path=str(cache_path), cache_hit=cache_hit,
        reader_version=READER_VERSION, legacy_read_only=legacy, raw_status=raw.get("status"),
        raw_min=raw.get("actual_min_date"), raw_max=raw.get("actual_max_date"), asof=asof,
        market_latest=market_latest, universe_count=len(universe), price_rows=len(prices),
        invalid_or_conflicting_rows=counts, next_session=next_session, next_session_status=next_status,
        warnings=warnings, source_routes=list(entries), universe_method="explicit_classification_or_research_unverified",
        calendar_contract=contract, calendar_descriptor=entries["official_calendar"],
        calendar_verified=bool(contract["trusted_next_sessions"].get(asof)==next_session and asof in contract["open_dates"] and next_session in contract["open_dates"]),
        ordinary_universe_status="verified" if universe["ordinary_share_verified"].all() else "research_unverified",
        supplemental=supplemental_metadata)
    with stage("整理券商分點與 21 日支援資料") as progress:
        q21_descriptor = entries.get(ORDER21_ROUTE)
        q21_kwargs = {}
        if demand_context is not None and q21_descriptor is not None:
            q21_kwargs = dict(
                source_root=source_root,
                parent_identity=checked["snapshot_fingerprint"],
                source_identity={
                    "route_id": ORDER21_ROUTE,
                    "source_relative_path": q21_descriptor.get("source_relative_path"),
                    "sha256": q21_descriptor.get("sha256"),
                    "size": q21_descriptor.get("size"),
                },
            )
        order21_metadata = load_order21_support(
            cache_dir, q21_descriptor, universe,
            asof=asof, calendar_contract=contract, snapshot_id=checked["run_id"],
            optional_error=checked["optional_errors"].get(ORDER21_ROUTE),
            optional_source_metadata=checked["optional_source_metadata"].get(ORDER21_ROUTE),
            **q21_kwargs)
        metadata.update(order21_metadata)
        progress.advance(detail="券商分點與 21 日支援資料整理完成")
    if price_input is not None:
        from analysis_data_demand import build_data_plan
        plan_inputs = price_input["plan_inputs"]
        extra_requirements = plan_inputs["extra_requirements"]
        supplemental_windows = supplemental_metadata.get("query_windows", {})
        for route, window in supplemental_windows.items():
            route_extra = extra_requirements["routes"].get(route)
            if route_extra is not None:
                route_extra["start_date"] = window.get("start_date")
                route_extra["end_date"] = window.get("end_date")
                route_extra.setdefault("metadata", {})["query_window"] = window
        extra_requirements.setdefault("metadata", {})["supplemental_query_windows"] = supplemental_windows
        extra_requirements["metadata"]["price_projection_io"] = price_input["query_metrics"]
        extra_requirements["metadata"]["price_bootstrap_reads"] = price_input["bootstrap_reads"]
        available = {}
        price_coverage = (counts.get("observation_coverage") or {}).get("minimum_distinct_sessions")
        if price_coverage is not None:
            available[PRICE_ROUTE] = int(price_coverage)
        for route, route_metadata in supplemental_metadata.get("routes", {}).items():
            value = (route_metadata.get("observation_coverage") or {}).get(
                "minimum_distinct_sessions")
            if route in extra_requirements["routes"] and value is not None:
                available[route] = int(value)
        q21_demand = ((metadata.get("order21_reader") or {}).get("query_demand") or {})
        q21_available = q21_demand.get("available_observations")
        if q21_available is not None:
            available[ORDER21_ROUTE] = int(q21_available)
        final_plan = build_data_plan(
            plan_inputs["consumer"], plan_inputs["purpose"], plan_inputs["asof"],
            plan_inputs["model_identity"], plan_inputs["feature_columns"], plan_inputs["specs"],
            sample_start=plan_inputs["sample_start"],
            available_observations=available,
            extra_requirements=extra_requirements,
        )
        metadata["resolved_data_plan"] = final_plan
        metadata["data_plan_digest"] = final_plan["digest"]
        metadata["data_plan_cache_identity"] = price_input["cache_identity"]
        metadata["price_projection_query"] = price_input["query_metrics"]
        metadata["price_asof_bootstrap_reads"] = price_input["bootstrap_reads"]
        if plan_inputs["purpose"] == "daily_frozen_inference":
            metadata["_actuals_query_context"] = {
                "source_root": str(source_root),
                "source_descriptor": raw,
                "snapshot_fingerprint": checked["snapshot_fingerprint"],
                "snapshot_id": checked["run_id"],
                "calendar_contract": contract,
                "universe": universe,
            }
    return prices, universe, metadata


def _actuals_from_loaded_rows(prices, requests):
    indexed = prices.set_index(["market", "stock_id", "date"], drop=False)
    output = []
    for request in requests:
        market = str(request.get("market", ""))
        code = str(request.get("code") or request.get("stock_id") or "")
        base = _date(request.get("reference_session") or request.get("base_session"))
        target = _date(request.get("target_session"))
        item = dict(market=market, code=code, stock_id=code, base_session=base, reference_session=base,
            target_session=target, raw_verified=False, official_session_verified=False, reference_verified=False,
            availability_verified=False, diagnostic="actual_or_reference_row_missing")
        try:
            reference = indexed.loc[(market, code, base)]
            actual = indexed.loc[(market, code, target)]
        except KeyError:
            output.append(item)
            continue
        if isinstance(reference, pd.DataFrame) or isinstance(actual, pd.DataFrame):
            item["diagnostic"] = "ambiguous_loaded_raw_rows"
            output.append(item)
            continue
        raw_verified = actual.get("price_basis") == "raw" and bool(_text(actual.get("source_hash"))) and _true(actual.get("publication_time_verified"))
        reference_verified = reference.get("price_basis") == "raw" and bool(_text(reference.get("source_hash"))) and _true(reference.get("publication_time_verified"))
        requested_close = pd.to_numeric(request.get("reference_close"), errors="coerce")
        if pd.notna(requested_close) and abs(float(requested_close) - float(reference["close"])) > 1e-8:
            reference_verified = False
        official = _true(reference.get("calendar_next_verified")) and reference.get("calendar_next_session") == target
        publication = _aware(actual.get("available_at"))
        available = _true(actual.get("publication_time_verified")) and publication is not None
        item.update({field: float(actual[field]) for field in ("open", "high", "low", "close")})
        item.update(reference_close=float(reference["close"]), raw_verified=bool(raw_verified),
            reference_verified=bool(reference_verified), official_session_verified=bool(official),
            source=_text(actual.get("source_id")), sha256=_text(actual.get("source_hash")),
            source_snapshot_id=actual.get("source_snapshot_id"), price_basis=actual.get("price_basis", "unknown"),
            published_at=publication.isoformat() if publication is not None else None,
            availability_verified=bool(available), diagnostic=None if raw_verified and reference_verified and official and available else "raw_calendar_reference_or_publication_unverified")
        output.append(item)
    return output


def _query_actual_request_rows(prices, requests, query_context, metrics_out):
    missing = []
    loaded_keys = set()
    if not prices.empty:
        loaded_keys = set(zip(
            prices["market"].astype(str), prices["stock_id"].astype(str),
            prices["date"].astype(str).str[:10],
        ))
    for request in requests:
        market = str(request.get("market", ""))
        code = str(request.get("code") or request.get("stock_id") or "")
        base = _date(request.get("reference_session") or request.get("base_session"))
        target = _date(request.get("target_session"))
        if code and base and target and (
                (market, code, base) not in loaded_keys
                or (market, code, target) not in loaded_keys):
            missing.extend([
                dict(market=market, stock_id=code, date=base),
                dict(market=market, stock_id=code, date=target),
            ])
    if not missing or not query_context:
        metrics_out.update(
            status=("loaded_window_only" if not missing else "missing_query_context"),
            requested_pairs=len(missing) // 2,
            external_query_skipped=True,
        )
        return prices

    import duckdb
    from stock_query_projection import query_published_rows
    descriptor = dict(query_context["source_descriptor"])
    relative_path = descriptor.get("source_relative_path") or descriptor.get("relative_path")
    if not relative_path:
        raise ValueError("actuals pinned price descriptor 缺少相對路徑。")
    descriptor["path"] = relative_path
    descriptor["relative_path"] = relative_path
    requested = pd.DataFrame(missing).drop_duplicates()
    requested["market"] = requested["market"].astype(str).str.upper()
    requested["stock_id"] = requested["stock_id"].astype(str)
    requested["date"] = requested["date"].astype(str).str[:10]
    start_by_stock = requested.groupby("stock_id")["date"].min().to_dict()
    end_by_stock = requested.groupby("stock_id")["date"].max().to_dict()
    codes = sorted(requested["stock_id"].unique().tolist())
    request_keys = sorted(set(zip(
        requested["market"].astype(str), requested["stock_id"].astype(str),
        requested["date"].astype(str),
    )))
    request_identity = hashlib.sha256(json.dumps(
        request_keys, ensure_ascii=False, separators=(",", ":")
    ).encode("utf-8")).hexdigest()
    metrics_out.update(
        requested_pairs=len(missing) // 2,
        requested_row_keys=len(request_keys),
        requested_row_keys_sha256=request_identity,
        requested_date_min=min(requested["date"].tolist()),
        requested_date_max=max(requested["date"].tolist()),
        requested_stock_count=len(codes),
    )
    source_identity = {
        "route_id": descriptor.get("route_id", "official_market_daily"),
        "source_relative_path": relative_path,
        "sha256": descriptor.get("sha256"),
        "size": descriptor.get("size"),
    }
    metrics_out["source_identity"] = source_identity
    metrics_out["snapshot_fingerprint"] = query_context["snapshot_fingerprint"]
    result = query_published_rows(
        Path(query_context["source_root"]), descriptor, "official_market_daily",
        stock_ids=codes, start_date=start_by_stock, end_date=end_by_stock,
        source_identity=source_identity,
        parent_identity=query_context["snapshot_fingerprint"],
        allow_build=True,
        field_map={"stock_field": "stock_id", "date_field": "date"},
    )
    con = duckdb.connect()
    try:
        shards, projection_bytes = _create_raw_price_view(con, result)
        if not shards:
            rows = pd.DataFrame(columns=["market", "stock_id", "date", "open", "high", "low",
                                         "close", "volume", *PROOF_COLUMNS])
            scanned = 0
        else:
            con.register("requested_actual_rows", requested)
            con.execute("""CREATE TEMP TABLE actual_selected AS SELECT r.stock_id,
                substr(r.date,1,10) AS date,upper(r.market) AS market,r.name,
                r.open,r.high,r.low,r.close,r.volume,r.volume_unit,
                available_at,published_at,publication_time_verified,availability_basis,price_basis,
                source_id,reference_price,previous_reference_price,upper_limit,lower_limit,
                price_rule_verified,price_exception_status,rule_effective_session,rule_source,
                next_reference_price,next_reference_session,next_reference_published_at,open_is_proxy
                FROM raw_projection_rows r JOIN requested_actual_rows q
                  ON upper(r.market)=q.market AND cast(r.stock_id AS VARCHAR)=q.stock_id
                 AND substr(r.date,1,10)=q.date
                WHERE try_cast(substr(r.date,1,10) AS DATE) IS NOT NULL""")
            con.execute(f"""CREATE TEMP TABLE actual_valid AS SELECT market,stock_id,date,name,
                open,high,low,close,
                volume*CASE volume_unit WHEN '千股' THEN 1000 ELSE 1 END AS volume,
                {', '.join(PROOF_COLUMNS)}
                FROM actual_selected WHERE volume_unit IN ('股','千股') AND volume>0
                  AND open>0 AND high>0 AND low>0 AND close>0
                  AND isfinite(open) AND isfinite(high) AND isfinite(low)
                  AND isfinite(close) AND isfinite(volume)
                  AND high>=greatest(open,close) AND low<=least(open,close) AND high>=low""")
            con.execute("""CREATE TEMP TABLE actual_conflicts AS SELECT market,stock_id,date FROM
                (SELECT DISTINCT market,stock_id,date,open,high,low,close,volume FROM actual_valid)
                GROUP BY market,stock_id,date HAVING count(*)>1""")
            proof_aggregates = ", ".join(
                f"CASE WHEN count(*)=count(v.{name}) AND count(DISTINCT v.{name})=1 "
                f"THEN min(v.{name}) ELSE NULL END AS {name}"
                for name in PROOF_COLUMNS
            )
            rows = con.execute(f"""SELECT v.market,v.stock_id,v.date,min(v.name) AS name,
                v.open,v.high,v.low,v.close,v.volume,{proof_aggregates}
                FROM actual_valid v ANTI JOIN actual_conflicts c USING(market,stock_id,date)
                GROUP BY v.market,v.stock_id,v.date,v.open,v.high,v.low,v.close,v.volume""").fetchdf()
            scanned = sum(int(item["rows"]) for item in shards)
        result.record_consumer_scan(rows_scanned=scanned, projection_bytes_read=projection_bytes)
        metrics_out.update(
            status=result.status,
            selected_shards=len(shards),
            source_rows_scanned=scanned,
            external_read=dict(result.metrics),
            query_cache_key=result.manifest.get("cache_key"),
            query_stock_statistics=result.query_stock_statistics,
        )
    finally:
        con.close()
    if rows.empty:
        return prices

    request_universe = query_context["universe"].copy()
    known = set(zip(request_universe["market"].astype(str), request_universe["stock_id"].astype(str)))
    extras = [
        {"market": item["market"], "stock_id": item["stock_id"],
         "classification_effective_date": "", "ordinary_share_verified": False,
         "classification_source": "historical_prediction_request"}
        for item in requested[["market", "stock_id"]].drop_duplicates().to_dict("records")
        if (item["market"], item["stock_id"]) not in known
    ]
    if extras:
        request_universe = pd.concat([request_universe, pd.DataFrame(extras)], ignore_index=True)
    qualified = qualify_prices(
        rows, request_universe, query_context["calendar_contract"],
        raw_descriptor=descriptor, snapshot_id=query_context["snapshot_id"], legacy=False)
    existing = set(zip(
        prices["market"].astype(str), prices["stock_id"].astype(str),
        prices["date"].astype(str).str[:10],
    )) if not prices.empty else set()
    new_keys = list(zip(
        qualified["market"].astype(str), qualified["stock_id"].astype(str),
        qualified["date"].astype(str).str[:10],
    ))
    qualified = qualified.loc[[key not in existing for key in new_keys]]
    return pd.concat([prices, qualified], ignore_index=True, sort=False)


def actuals_for_requests(prices, requests, *, query_context=None, metrics_out=None):
    """Resolve ledger requests, querying pinned exact old rows outside the feature slice."""
    metrics_out = metrics_out if metrics_out is not None else {}
    expanded = _query_actual_request_rows(prices, requests, query_context, metrics_out)
    return _actuals_from_loaded_rows(expanded, requests)
