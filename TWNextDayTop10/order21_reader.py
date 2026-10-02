"""Optional report-only Order21 reader; bounded SQL normalization, no model inputs."""
from __future__ import annotations

import calendar
import hashlib
import itertools
import json
import re
from datetime import date
from pathlib import Path

import duckdb

from order21_support import build_order21_support, order21_query_demand, support_text
from top10_data_demand_specs import ORDER21_RETENTION_CALENDAR_MONTHS
from stock_query_projection import (
    ProjectionUnavailableError,
    UnsupportedProjectionError,
    query_published_rows,
)

VERSION = "order21-daily-rollup-v3-four-calendar-month-window"
ROUTE_ID = "finmind_trading_daily_report"
CACHE_SEAL_VERSION = "order21-derived-cache-seal-v2-retention-window"


def _quoted(value):
    return "'" + str(value).replace("'", "''") + "'"


def _digest(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True, ensure_ascii=False, default=str).encode("utf-8")).hexdigest()


def _hash_file(path):
    digest = hashlib.sha256()
    size = 0
    with Path(path).open("rb") as stream:
        while True:
            block = stream.read(1024 * 1024)
            if not block:
                break
            size += len(block)
            digest.update(block)
    return digest.hexdigest(), size


def _declared_size(descriptor):
    values = []
    for name in ("size_bytes", "size"):
        value = descriptor.get(name)
        if value is None:
            continue
        if isinstance(value, bool):
            raise ValueError("pinned Order21 artifact size is invalid")
        try:
            parsed = int(value)
        except (TypeError, ValueError) as exc:
            raise ValueError("pinned Order21 artifact size is invalid") from exc
        if parsed < 0 or (isinstance(value, float) and value != parsed):
            raise ValueError("pinned Order21 artifact size is invalid")
        values.append(parsed)
    if len(set(values)) > 1:
        raise ValueError("pinned Order21 size and size_bytes disagree")
    return values[0] if values else None


def order21_retention_window(asof):
    """Return the inclusive four-calendar-month source window ending at asof."""
    end = date.fromisoformat(str(asof)[:10])
    month_index = end.year * 12 + (end.month - 1) - ORDER21_RETENTION_CALENDAR_MONTHS
    year, month_zero = divmod(month_index, 12)
    month = month_zero + 1
    day = min(end.day, calendar.monthrange(year, month)[1])
    start = date(year, month, day)
    return {
        "start_date": start.isoformat(),
        "end_date": end.isoformat(),
        "calendar_months": ORDER21_RETENTION_CALENDAR_MONTHS,
    }


def _bounded_query_demand(asof, retention_window):
    demand = order21_query_demand(asof)
    start_date = retention_window["start_date"]
    end_date = retention_window["end_date"]
    demand["score"].update(
        history_scope="eligible_observations_within_four_calendar_month_window",
        start_date=start_date,
    )
    demand["projection"].update(
        start_date=start_date,
        end_date=end_date,
        before_count=0,
        reason="bound_score_and_source_rows_to_four_calendar_month_window",
    )
    return demand


def _cache_identity(artifact_sha, artifact_size, asof, retention_window, stocks):
    return {
        "version": VERSION,
        "artifact_sha256": str(artifact_sha).lower(),
        "artifact_size_bytes": artifact_size,
        "asof": asof,
        "retention_window": dict(retention_window),
        "universe_key": _digest(sorted(stocks)),
    }


def _cache_seal_path(cache_path):
    return cache_path.with_suffix(".complete.json")


def _verify_derived_cache(cache_path, stats_path, seal_path, identity):
    """Return a cache hit only when the atomic completion seal validates both outputs."""
    try:
        seal = json.loads(seal_path.read_text(encoding="utf-8"))
    except FileNotFoundError:
        return False, "seal_missing", None, 0
    except (OSError, UnicodeError, ValueError, TypeError):
        return False, "seal_unreadable", None, 0
    if not isinstance(seal, dict) or seal.get("schema_version") != CACHE_SEAL_VERSION:
        return False, "seal_schema_mismatch", None, 0
    if seal.get("identity") != identity:
        return False, "seal_identity_mismatch", None, 0
    artifacts = seal.get("artifacts")
    if not isinstance(artifacts, dict):
        return False, "seal_artifacts_missing", None, 0
    verified_bytes = 0
    for name, path in (("parquet", cache_path), ("stats", stats_path)):
        record = artifacts.get(name)
        if not isinstance(record, dict) or record.get("name") != path.name:
            return False, f"{name}_seal_mismatch", None, verified_bytes
        if not path.is_file():
            return False, f"{name}_missing", None, verified_bytes
        digest, size = _hash_file(path)
        verified_bytes += size
        if record.get("size_bytes") != size:
            return False, f"{name}_size_mismatch", None, verified_bytes
        if record.get("sha256") != digest:
            return False, f"{name}_hash_mismatch", None, verified_bytes
    try:
        stats = json.loads(stats_path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, ValueError, TypeError):
        return False, "stats_unreadable", None, verified_bytes
    if not isinstance(stats, dict):
        return False, "stats_invalid", None, verified_bytes
    return True, None, stats, verified_bytes


def _write_derived_cache_seal(cache_path, stats_path, seal_path, identity):
    artifacts = {}
    for name, path in (("parquet", cache_path), ("stats", stats_path)):
        digest, size = _hash_file(path)
        artifacts[name] = {"name": path.name, "sha256": digest, "size_bytes": size}
    seal = {
        "schema_version": CACHE_SEAL_VERSION,
        "identity": identity,
        "artifacts": artifacts,
    }
    temporary = seal_path.with_name(seal_path.name + ".tmp")
    temporary.write_text(json.dumps(seal, sort_keys=True, separators=(",", ":")), encoding="utf-8")
    temporary.replace(seal_path)


def _verify_source_artifact(root, entry, descriptor):
    """Hash the pinned raw object before DuckDB opens it on a cold fallback."""
    expected_sha = descriptor.get("sha256")
    if not isinstance(expected_sha, str) or re.fullmatch(r"[0-9a-fA-F]{64}", expected_sha) is None:
        raise ValueError("pinned Order21 artifact SHA-256 is required")
    expected_size = _declared_size(descriptor)
    if expected_size is None:
        raise ValueError("pinned Order21 artifact size is required")
    root = Path(root).resolve(strict=True)
    candidate = root.joinpath(*entry["relative_path"].split("/"))
    path = candidate.resolve(strict=True)
    try:
        path.relative_to(root)
    except ValueError as exc:
        raise ValueError("pinned Order21 artifact escaped source root") from exc
    if not path.is_file():
        raise ValueError("pinned Order21 artifact is not a file")
    digest, size = _hash_file(path)
    if size != expected_size:
        raise ValueError("pinned Order21 artifact size mismatch")
    if digest.lower() != expected_sha.lower():
        raise ValueError("pinned Order21 artifact SHA-256 mismatch")
    return path, size, digest


def _projection_descriptor(descriptor, source_root):
    """Adapt the caller's pinned route entry to the projection API's relative path contract."""
    root = Path(source_root).resolve()
    entry = dict(descriptor)
    path_value = entry.get("path")
    relative_value = entry.get("source_relative_path") or entry.get("relative_path")
    def clean_relative(value):
        text = str(value).replace("\\", "/")
        parts = [part for part in text.split("/") if part not in ("", ".")]
        if text.startswith("/") or any(part == ".." for part in parts):
            raise ValueError("pinned Order21 relative artifact path is invalid")
        return "/".join(parts)

    if path_value is not None and Path(str(path_value)).is_absolute():
        absolute = Path(path_value).resolve(strict=False)
        try:
            resolved_relative = absolute.relative_to(root).as_posix()
        except ValueError as exc:
            raise ValueError("pinned Order21 artifact escaped source root") from exc
        if relative_value is not None:
            declared_relative = clean_relative(relative_value)
            if declared_relative.casefold() != resolved_relative.casefold():
                raise ValueError("pinned Order21 source-relative path disagrees with path")
        relative_value = resolved_relative
    elif path_value is not None and relative_value is not None:
        if clean_relative(relative_value).casefold() != clean_relative(path_value).casefold():
            raise ValueError("pinned Order21 source-relative path disagrees with path")
    elif relative_value is None and path_value is not None:
        relative_value = str(path_value).replace("\\", "/")
    if relative_value is None:
        raise ValueError("pinned Order21 relative artifact path is missing")
    relative = clean_relative(relative_value)
    entry.pop("path", None)
    entry.pop("source_relative_path", None)
    entry["relative_path"] = relative
    return root, entry


def _append_verified_shards(con, query_result):
    """Stream verified JSONL shards into DuckDB without collecting their rows in Python."""
    con.execute("""CREATE TEMP TABLE order21_raw (
        source_ordinal UBIGINT, date VARCHAR, stock_id VARCHAR, market VARCHAR,
        securities_trader_id VARCHAR, broker_id VARCHAR, broker VARCHAR,
        price VARCHAR, buy VARCHAR, sell VARCHAR, available_at VARCHAR,
        published_at VARCHAR, publication_time_verified JSON,
        availability_basis VARCHAR, unit VARCHAR, volume_unit VARCHAR)""")
    columns = {
        "__sequence": "UBIGINT", "__date": "VARCHAR", "__stock": "VARCHAR",
        "row": "JSON",
    }
    rows_scanned = 0
    projection_bytes_read = 0
    shards = iter(query_result.iter_verified_shards())
    while True:
        batch = list(itertools.islice(shards, 128))
        if not batch:
            break
        con.read_json(
            [shard["path"] for shard in batch],
            format="newline_delimited",
            columns=columns,
        ).create_view("order21_verified_shard")
        con.execute("""INSERT INTO order21_raw
            SELECT __sequence,
                json_extract_string("row", '$.date'),
                json_extract_string("row", '$.stock_id'),
                json_extract_string("row", '$.market'),
                json_extract_string("row", '$.securities_trader_id'),
                json_extract_string("row", '$.broker_id'),
                json_extract_string("row", '$.broker'),
                json_extract_string("row", '$.price'),
                json_extract_string("row", '$.buy'),
                json_extract_string("row", '$.sell'),
                json_extract_string("row", '$.available_at'),
                json_extract_string("row", '$.published_at'),
                json_extract("row", '$.publication_time_verified'),
                json_extract_string("row", '$.availability_basis'),
                json_extract_string("row", '$.unit'),
                json_extract_string("row", '$.volume_unit')
            FROM order21_verified_shard""")
        con.execute("DROP VIEW order21_verified_shard")
        rows_scanned += sum(int(shard["rows"]) for shard in batch)
        projection_bytes_read += sum(int(shard["size_bytes"]) for shard in batch)
    query_result.record_consumer_scan(
        rows_scanned=rows_scanned,
        projection_bytes_read=projection_bytes_read,
    )
    return rows_scanned, projection_bytes_read


def _project(con, descriptor, universe, asof, cutoff, cache_path, *,
             start_date, end_date, projection_result=None):
    """Scan raw JSON or verified shards, quarantine conflicts, and persist daily aggregates.

    Aggregated rows are used only for the v1 helper's daily net/count statistics;
    original source status, unit ambiguity and rejection counts remain attached.
    """
    fields = ["date", "stock_id", "market", "securities_trader_id", "broker_id", "broker", "price", "buy", "sell",
        "available_at", "published_at", "publication_time_verified", "availability_basis", "unit", "volume_unit"]
    con.register("order21_universe", universe[["market", "stock_id"]])
    if projection_result is None:
        before = Path(descriptor["path"]).stat()
        con.read_json(descriptor["path"], format="array", columns={name: "JSON" if name=="publication_time_verified" else "VARCHAR" for name in fields}).create_view("order21_raw")
        con.execute("SET preserve_insertion_order=true")
        con.execute("SET threads=1")
        con.execute("""CREATE TEMP TABLE ordinal_rows AS
            SELECT row_number() OVER () AS source_ordinal,* FROM order21_raw
            WHERE try_cast(substr(date,1,10) AS DATE) BETWEEN ?::DATE AND ?::DATE""",
            [start_date, end_date])
    else:
        before = None
        _append_verified_shards(con, projection_result)
        con.execute("""CREATE TEMP TABLE ordinal_rows AS SELECT * FROM order21_raw
            WHERE try_cast(substr(date,1,10) AS DATE) BETWEEN ?::DATE AND ?::DATE""",
            [start_date, end_date])
    con.execute("SET threads=2")
    con.execute("""CREATE TEMP TABLE projected AS
        WITH pool AS (SELECT *, count(*) OVER (PARTITION BY stock_id) AS markets FROM order21_universe)
        SELECT r.source_ordinal,p.market,r.stock_id,CASE WHEN regexp_matches(substr(r.date,1,10),'^[0-9]{4}-[0-9]{2}-[0-9]{2}$') THEN try_cast(substr(r.date,1,10) AS DATE) ELSE NULL END AS date,
          trim(coalesce(nullif(r.securities_trader_id,''),nullif(r.broker_id,''),r.broker,'')) AS broker_id,
          try_cast(r.price AS DOUBLE) AS price,
          try_cast(r.buy AS DOUBLE) AS buy,
          try_cast(r.sell AS DOUBLE) AS sell,
          coalesce(nullif(r.available_at,''),r.published_at) AS available_text,
          coalesce(json_type(r.publication_time_verified)='BOOLEAN' AND try_cast(r.publication_time_verified AS BOOLEAN),false) AS proof,
          coalesce(r.availability_basis,'unknown') AS basis,
          coalesce(nullif(r.unit,''),nullif(r.volume_unit,''),'unknown') AS unit
        FROM ordinal_rows r JOIN pool p ON r.stock_id=p.stock_id
          AND ((nullif(r.market,'') IS NULL AND p.markets=1) OR upper(r.market)=p.market)
        WHERE try_cast(substr(r.date,1,10) AS DATE) BETWEEN ?::DATE AND ?::DATE""",
        [start_date, end_date])
    if before is not None:
        after = Path(descriptor["path"]).stat()
        if (before.st_size,before.st_mtime_ns,before.st_ctime_ns) != (after.st_size,after.st_mtime_ns,after.st_ctime_ns):
            raise ValueError("pinned Order21 object changed while projecting")
    con.execute("""CREATE TEMP TABLE normalized AS SELECT *,
        CASE WHEN regexp_matches(available_text,'(Z|[+-][0-9]{2}:[0-9]{2})$')
             THEN try_cast(available_text AS TIMESTAMPTZ) ELSE NULL END AS available_at,
        (date IS NOT NULL AND broker_id<>'' AND buy IS NOT NULL AND sell IS NOT NULL
         AND isfinite(buy) AND isfinite(sell) AND buy>=0 AND sell>=0) AS valid
        FROM projected""")
    con.execute("""CREATE TEMP TABLE scoped AS SELECT *,
        valid AND (available_at IS NULL OR available_at<=?::TIMESTAMPTZ) AS usable
        FROM normalized WHERE date<=?::DATE OR date IS NULL""", [cutoff, asof])
    con.execute("""CREATE TEMP TABLE natural_keys AS SELECT market,stock_id,date,broker_id,
        CASE WHEN isfinite(price) THEN price ELSE NULL END AS price,
        min(buy) AS buy,min(sell) AS sell,first(available_at ORDER BY source_ordinal) AS available_at,
        first(available_at ORDER BY source_ordinal) IS NOT NULL
            AND first(proof ORDER BY source_ordinal)
            AND first(basis ORDER BY source_ordinal)<>'estimated_conservative_lag' AS verified,
        first(basis ORDER BY source_ordinal)='estimated_conservative_lag' AS estimated,
        first(unit ORDER BY source_ordinal) AS unit,count(DISTINCT unit)>1 AS unit_conflict,
        count(*) AS source_row_count,
        count(DISTINCT buy)>1 OR count(DISTINCT sell)>1 AS conflict
        FROM scoped WHERE usable GROUP BY market,stock_id,date,broker_id,
            CASE WHEN isfinite(price) THEN price ELSE NULL END""")
    con.execute("""CREATE TEMP TABLE day_units AS SELECT market,stock_id,date,
        count(DISTINCT unit)>1 OR bool_or(unit_conflict) AS conflict
        FROM natural_keys WHERE NOT conflict GROUP BY market,stock_id,date""")
    con.execute("""CREATE TEMP TABLE daily AS SELECT n.market,n.stock_id,n.date,
        sum(n.buy) AS buy,sum(n.sell) AS sell,max(n.available_at) AS available_at,
        bool_and(n.verified) AS publication_time_verified,
        CASE WHEN bool_and(n.verified) THEN 'verified_published_at'
             WHEN bool_or(n.estimated) THEN 'estimated_conservative_lag' ELSE 'unknown' END AS availability_basis,
        n.unit,count(*) AS natural_key_count,sum(n.source_row_count) AS source_row_count
        FROM natural_keys n WHERE NOT n.conflict GROUP BY n.market,n.stock_id,n.date,n.unit""")
    stats = {}
    for market, code, count in con.execute("SELECT market,stock_id,count(*) FROM projected GROUP BY market,stock_id").fetchall():
        stats.setdefault(f"{market}:{code}", {})["source_rows_seen"] = count
    for market, code, count in con.execute("SELECT market,stock_id,count(*) FROM normalized WHERE date>?::DATE GROUP BY market,stock_id", [asof]).fetchall():
        stats.setdefault(f"{market}:{code}", {})["outside_asof_rows"] = count
    for market, code, count in con.execute("SELECT market,stock_id,count(*) FROM scoped WHERE NOT coalesce(usable,false) GROUP BY market,stock_id").fetchall():
        stats.setdefault(f"{market}:{code}", {})["rejected_source_rows"] = count
    for market, code, count in con.execute("SELECT market,stock_id,count(*) FROM natural_keys WHERE conflict GROUP BY market,stock_id").fetchall():
        stats.setdefault(f"{market}:{code}", {})["conflicting_natural_keys"] = count
    for market, code, count in con.execute("SELECT market,stock_id,count(*) FROM day_units WHERE conflict GROUP BY market,stock_id").fetchall():
        stats.setdefault(f"{market}:{code}", {})["conflicting_unit_days"] = count
    for market, code, count, keys, days in con.execute("SELECT market,stock_id,sum(source_row_count),sum(natural_key_count),count(DISTINCT date) FROM daily GROUP BY market,stock_id").fetchall():
        stats.setdefault(f"{market}:{code}", {}).update(valid_source_rows=count,
            distinct_natural_keys=keys,distinct_valid_days=days)
    # Report actual broker totals for the latest usable day, not synthetic Q rows.
    con.execute("""CREATE TEMP TABLE latest_brokers AS SELECT n.market,n.stock_id,n.date,n.broker_id,
        sum(n.buy) AS buy,sum(n.sell) AS sell,sum(n.buy-n.sell) AS net,n.unit,
        sum(n.source_row_count) AS source_row_count,count(*) AS price_level_keys
        FROM natural_keys n JOIN (SELECT market,stock_id,max(date) AS date FROM daily GROUP BY market,stock_id) d
          USING(market,stock_id,date)
        WHERE NOT n.conflict AND NOT n.unit_conflict GROUP BY n.market,n.stock_id,n.date,n.broker_id,n.unit""")
    for side, direction, predicate in [("net_buy", "DESC", "net>0"), ("net_sell", "ASC", "net<0")]:
        query = f"""SELECT * FROM latest_brokers WHERE {predicate}
            QUALIFY row_number() OVER(PARTITION BY market,stock_id,unit ORDER BY net {direction},broker_id)<=3
            ORDER BY market,stock_id,net {direction},broker_id"""
        for market, code, day, broker, buy, sell, net, unit, count, levels in con.execute(query).fetchall():
            stats.setdefault(f"{market}:{code}", {}).setdefault("latest_branch_distribution", []).append(dict(
                side=side,date=str(day),broker_id=broker,buy=buy,sell=sell,net=net,unit=unit,
                source_row_count=count,distinct_price_level_keys=levels,aggregation="same_broker_day_across_price_levels"))
    temporary = cache_path.with_suffix(".parquet.tmp")
    con.execute("COPY (SELECT * FROM daily ORDER BY market,stock_id,date) TO " + _quoted(temporary) + " (FORMAT PARQUET)")
    temporary.replace(cache_path)
    return stats


def load_order21_support(cache_dir, descriptor, universe, *, asof, calendar_contract,
                         snapshot_id, optional_error=None,
                         optional_source_metadata=None, source_root=None,
                         parent_identity=None, source_identity=None):
    retention_window = order21_retention_window(asof)
    asof = retention_window["end_date"]
    retention_start = retention_window["start_date"]
    query_demand = _bounded_query_demand(asof, retention_window)
    cutoff = f"{asof}T22:00:00+08:00"
    sessions = sorted(
        day for day in calendar_contract.get("open_dates", [])
        if retention_start <= day <= asof
    )
    # Q needs a verified full axis: every consecutive published open day must
    # have a matching explicit next-session link, not a weekday-generated axis.
    links = calendar_contract.get("trusted_next_sessions", {})
    calendar_verified = bool(sessions and sessions[-1] == asof and all(links.get(a)==b for a,b in zip(sessions,sessions[1:])))
    source = dict(descriptor or optional_source_metadata or {})
    source_identity = dict(source_identity or {})
    source_identity["retention_window"] = dict(retention_window)
    request_parent_identity = (
        parent_identity
        if parent_identity is not None
        else source.get("parent_identity")
    )
    if source_root is not None and (
        not isinstance(request_parent_identity, str)
        or re.fullmatch(r"[0-9a-fA-F]{64}", request_parent_identity) is None
    ):
        raise ValueError("pinned canonical manifest SHA-256 is required")
    metadata = dict(status=source.get("status", "MISSING"), unit="unknown",
        source_refs=[{key: source.get(key) for key in ("route_id", "sha256", "path", "status")} ] if source else [])
    support, texts = {}, {}
    by_code = {}
    stocks = {(str(row.market),str(row.stock_id)): row for row in universe.itertuples()}
    error = optional_error
    stats = {}
    cache_path = None
    cache_hit = False
    rows_by_identity = {}
    cache_descriptor = descriptor if descriptor is not None else source
    artifact_sha = cache_descriptor.get("sha256") if cache_descriptor else None
    if source_root is not None:
        valid_artifact_sha = (
            isinstance(artifact_sha, str)
            and re.fullmatch(r"[0-9a-fA-F]{64}", artifact_sha) is not None
        )
        if descriptor is not None and not valid_artifact_sha:
            raise ValueError("pinned Order21 artifact SHA-256 is required")
        if descriptor is not None and _declared_size(descriptor) is None:
            raise ValueError("pinned Order21 artifact size is required")
        if descriptor is None and not valid_artifact_sha:
            cache_descriptor = None
    artifact_size = _declared_size(cache_descriptor) if cache_descriptor else None
    valid_cache_identity = (
        isinstance(artifact_sha, str)
        and re.fullmatch(r"[0-9a-fA-F]{64}", artifact_sha) is not None
        and artifact_size is not None
    )
    derived_cache_key = None
    read_mode = "source_unavailable" if descriptor is None else "not_started"
    external_read = {
        "mode": read_mode,
        "projection_invoked": False,
        "source_bytes_read": 0,
        "projection_bytes_read": 0,
        "rows_scanned": 0,
        "shards_read": 0,
    }
    projection_lineage = None
    projection_fallback_reason = None
    cold_fullscan_reason = None
    cache_invalidated_reason = None
    derived_cache_bytes_verified = 0
    cache_identity = None
    strict_projection_mode = source_root is not None
    if cache_descriptor:
        con = None
        try:
            con = duckdb.connect()
            cache_dir = Path(cache_dir)
            derived_cache_key = _digest(
                [VERSION, artifact_sha, asof, retention_start, sorted(stocks)]
            )
            cache_path = cache_dir / f"order21_{derived_cache_key}.parquet"
            stats_path = cache_path.with_suffix(".json")
            seal_path = _cache_seal_path(cache_path)
            if valid_cache_identity:
                cache_identity = _cache_identity(
                    artifact_sha, artifact_size, asof, retention_window, stocks
                )
                cache_hit, cache_invalidated_reason, sealed_stats, derived_cache_bytes_verified = _verify_derived_cache(
                    cache_path, stats_path, seal_path, cache_identity
                )
            else:
                cache_hit = False
                cache_invalidated_reason = "pinned_identity_missing"
            con.execute("SET memory_limit='2GB'")
            con.execute("SET threads=2")
            if not cache_hit:
                error = optional_error
                seal_path.unlink(missing_ok=True)
                if descriptor is None:
                    error = error or "pinned Order21 source descriptor is unavailable"
                    read_mode = "source_unavailable"
                else:
                    cache_dir.mkdir(parents=True, exist_ok=True)
                    (cache_dir / "tmp").mkdir(exist_ok=True)
                    con.execute("SET temp_directory=" + _quoted(cache_dir / "tmp"))
                    if source_root is not None:
                        root, artifact_entry = _projection_descriptor(
                            descriptor, source_root
                        )
                        try:
                            projection_result = query_published_rows(
                                root,
                                artifact_entry,
                                ROUTE_ID,
                                stock_ids=sorted({identity[1] for identity in stocks}),
                                start_date=retention_start,
                                end_date=asof,
                                before_count=0,
                                source_identity=source_identity,
                                parent_identity=request_parent_identity,
                                allow_build=False,
                                field_map={
                                    "stock_field": "stock_id",
                                    "date_field": "date",
                                },
                            )
                        except UnsupportedProjectionError as exc:
                            projection_fallback_reason = str(exc)
                            projection_result = None
                        except ProjectionUnavailableError as exc:
                            if str(exc) != "projection_not_built":
                                raise
                            cold_fullscan_reason = (
                                "projection_not_built/four_calendar_month_window"
                            )
                            projection_result = None
                        if projection_result is None:
                            # Unsupported or unavailable projections use the
                            # established reader, but its row stream remains
                            # constrained to the same retention window.
                            verified_path, source_size, source_digest = _verify_source_artifact(
                                root, artifact_entry, descriptor
                            )
                            fallback_descriptor = dict(descriptor)
                            fallback_descriptor["path"] = str(verified_path)
                            stats = _project(
                                con, fallback_descriptor, universe, asof, cutoff,
                                cache_path, start_date=retention_start, end_date=asof
                            )
                            read_mode = (
                                "cold_fullscan_no_projection"
                                if cold_fullscan_reason
                                else "unsupported_projection_fallback"
                            )
                            external_read = {
                                "mode": read_mode,
                                "projection_invoked": True,
                                "projection_status": (
                                    "unsupported"
                                    if projection_fallback_reason
                                    else "unavailable"
                                ),
                                "fallback_reason": (
                                    projection_fallback_reason
                                    or cold_fullscan_reason
                                ),
                                "source_bytes_read": source_size * 2,
                                "source_hash_bytes_read": source_size,
                                "duckdb_source_bytes_read": source_size,
                                "source_read_passes": 2,
                                "source_sha256_verified": source_digest,
                                "projection_bytes_read": 0,
                                "rows_scanned": None,
                                "matched_rows_scanned": sum(
                                    int(item.get("source_rows_seen", 0))
                                    for item in stats.values()
                                ),
                                "shards_read": 0,
                            }
                        else:
                            stats = _project(
                                con,
                                descriptor,
                                universe,
                                asof,
                                cutoff,
                                cache_path,
                                start_date=retention_start,
                                end_date=asof,
                                projection_result=projection_result,
                            )
                            read_mode = "query_projection"
                            projection_lineage = projection_result.lineage
                            external_read = {
                                "mode": read_mode,
                                "projection_invoked": True,
                                "projection_status": projection_result.status,
                                **dict(projection_result.metrics),
                                "verified_shards_selected": len(
                                    projection_result.selected
                                ),
                            }
                    else:
                        stats = _project(
                            con, descriptor, universe, asof, cutoff, cache_path,
                            start_date=retention_start, end_date=asof,
                        )
                        read_mode = "legacy_direct"
                        source_size = Path(descriptor["path"]).stat().st_size
                        external_read = {
                            "mode": read_mode,
                            "projection_invoked": False,
                            "source_bytes_read": source_size,
                            "source_hash_bytes_read": 0,
                            "duckdb_source_bytes_read": source_size,
                            "source_read_passes": 1,
                            "projection_bytes_read": 0,
                            "rows_scanned": None,
                            "matched_rows_scanned": sum(
                                int(item.get("source_rows_seen", 0))
                                for item in stats.values()
                            ),
                            "shards_read": 0,
                        }
                    temporary = stats_path.with_suffix(".json.tmp")
                    temporary.write_text(json.dumps(stats, ensure_ascii=False), encoding="utf-8")
                    temporary.replace(stats_path)
                    if cache_identity is not None:
                        _write_derived_cache_seal(
                            cache_path, stats_path, seal_path, cache_identity
                        )
            else:
                stats = sealed_stats
                error = None
                read_mode = "derived_cache_hit"
                external_read = {
                    "mode": read_mode,
                    "projection_invoked": False,
                    "source_bytes_read": 0,
                    "projection_bytes_read": 0,
                    "rows_scanned": 0,
                    "shards_read": 0,
                }
            cursor = con.execute("SELECT market,stock_id,date,buy,sell,available_at,publication_time_verified,availability_basis,unit FROM read_parquet(?) ORDER BY market,stock_id,date", [str(cache_path)])
            def daily_rows():
                while True:
                    batch = cursor.fetchmany(2048)
                    if not batch:
                        return
                    yield from batch
            for identity, group in itertools.groupby(daily_rows(), key=lambda row:(str(row[0]),str(row[1]))):
                # This is a daily rollup, never a broker-row list. Memory is bounded
                # by one security's distinct dates rather than 74M source rows.
                daily = list(group)
                units = {row[8] for row in daily}
                item_meta = dict(metadata, unit=next(iter(units)) if len(units)==1 and "__conflicting_unit__" not in units else "mixed_original_units")
                normalized = (dict(date=str(row[2]),stock_id=identity[1],broker_id="__daily_rollup_unit_" + str(row[8]),price=None,
                    buy=row[3],sell=row[4],available_at=row[5].isoformat() if row[5] is not None else None,
                    publication_time_verified=bool(row[6]),availability_basis=row[7]) for row in daily)
                listing = getattr(stocks.get(identity), "listing_session", None)
                value = build_order21_support(normalized, sessions, asof, cutoff if calendar_verified else None, item_meta, listing_session=listing)
                key_identity = ":".join(identity)
                quality = stats.get(key_identity, {})
                value["order21_conflicting_natural_keys"] += quality.get("conflicting_natural_keys", 0)
                value["order21_source_rejections"] = quality
                value["order21_requested_cutoff"] = cutoff
                value["order21_calendar_verified"] = calendar_verified
                value["order21_aggregation"] = "natural_key_checked_daily_unit_totals"
                value["order21_time_evidence_status"] = ("estimated" if any(row[7]=="estimated_conservative_lag" for row in daily) else
                    "verified" if all(row[6] for row in daily) else "unknown")
                # Never publish a net window combining explicitly different units.
                if len(units)>1 or quality.get("conflicting_unit_days", 0):
                    value["order21_unit"] = "mixed_original_units"
                    for window in value["order21_feature_observation_counts"].values():
                        window["observed_net_mean"] = window["complete_net_mean"] = None
                        window["reason"] = "incompatible_original_units"
                        window["unit"] = "mixed_original_units"
                rows_by_identity[identity] = value
        except (OSError, ValueError, duckdb.Error) as exc:
            if strict_projection_mode:
                raise
            error = f"{type(exc).__name__}: {exc}"
            rows_by_identity = {}
            stats = {}
        finally:
            if con is not None:
                con.close()
    for identity, stock in stocks.items():
        key = ":".join(identity)
        value = rows_by_identity.get(identity)
        if value is None:
            value = build_order21_support([], sessions, asof, cutoff if calendar_verified else None,
                metadata, listing_session=getattr(stock,"listing_session",None))
            value["order21_calendar_verified"] = calendar_verified
            value["order21_requested_cutoff"] = cutoff
            value["order21_source_rejections"] = stats.get(key, {})
            value["order21_conflicting_natural_keys"] = stats.get(key, {}).get("conflicting_natural_keys", 0)
        if error:
            value["order21_reader_error"] = error
        quality = stats.get(key, {})
        value["order21_rejected_rows"] = sum(quality.get(name, 0) for name in ("rejected_source_rows", "outside_asof_rows", "conflicting_natural_keys"))
        value["order21_cutoff"] = cutoff
        if not calendar_verified and value.get("order21_valid_sessions"):
            value["order21_score_reason"] = "calendar_publication_coverage_unverified"
        value["order21_branch_analysis"] = stats.get(key, {}).get("latest_branch_distribution", [])
        support[key] = value
        branch_text = "；".join("{date} 分點 {broker_id} 買 {buy}、賣 {sell}、淨額 {net}（原單位 {unit}；{distinct_price_level_keys} 個價位鍵彙總）".format(**item) for item in value["order21_branch_analysis"])
        texts[key] = dict(order21_support_text=support_text(value), order21_analysis_text=support_text(value,detail=True) + ("；分點觀察：" + branch_text if branch_text else "；分點觀察：無可用資料或淨額為零"),
            contribution_mode=value["order21_contribution_mode"])
    counts = universe.groupby("stock_id").size().to_dict()
    for (market, code) in stocks:
        if counts.get(code)==1:
            by_code[code] = support[f"{market}:{code}"]
    relative_source_path = (
        source.get("source_relative_path")
        or source.get("relative_path")
        or (source.get("path") if source_root is None else None)
    )
    pinned_source_identity = {
        "route_id": source.get("route_id", ROUTE_ID),
        "relative_path": relative_source_path,
        "sha256": artifact_sha,
        "size_bytes": source.get("size_bytes", source.get("size")),
        "parent_identity": request_parent_identity,
        "source_identity": (
            source_identity
            if source_identity is not None
            else source.get("source_identity")
        ),
    }
    return dict(order21_support_by_identity=support, order21_support_by_code=by_code, order21_text_by_identity=texts,
        order21_reader=dict(version=VERSION,snapshot_id=snapshot_id,source_descriptor=source or None,
            optional_error=optional_error,cache_path=str(cache_path) if cache_path else None,cache_hit=cache_hit,
        derived_cache_key=derived_cache_key, retention_window=retention_window,
        query_demand=query_demand,
            cache_invalidated_reason=cache_invalidated_reason,
            derived_cache_bytes_verified=derived_cache_bytes_verified,
            source_identity=pinned_source_identity, external_read=external_read,
            projection_lineage=projection_lineage,
            projection_fallback_reason=projection_fallback_reason,
            cold_fullscan_reason=cold_fullscan_reason,
            cache_read_mode=read_mode, calendar_verified=calendar_verified,
            row_count_scope="matched_market_code_universe_within_retention_window",
            duplicate_order_policy="single_thread_json_array_ordinal_before_join_first_accepted",
            contribution_mode="report_only_unverified_gain"))
