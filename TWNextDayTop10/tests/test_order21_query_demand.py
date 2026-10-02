import hashlib
import json
from pathlib import Path

import pandas as pd
import pytest

import order21_reader
from data_reader import build_universe
from order21_reader import load_order21_support
from order21_support import REPORT_WINDOW_SESSIONS
from stock_query_projection import (
    ProjectionIntegrityError,
    ProjectionLockTimeoutError,
    ProjectionUnavailableError,
    UnsupportedProjectionError,
    query_published_rows,
)

from test_reader_order21_v4 import order_row, stocks, write_source


ROUTE_ID = "finmind_trading_daily_report"
FIELD_MAP = {"stock_field": "stock_id", "date_field": "date"}


def history_calendar(asof="2026-09-22", count=80):
    days = [day.strftime("%Y-%m-%d") for day in pd.bdate_range(end=asof, periods=count)]
    return dict(
        open_dates=days,
        closed_dates=[],
        conflict_dates=[],
        rejected=[],
        trusted_next_sessions={left: right for left, right in zip(days, days[1:])},
    )


def history_rows(days, *, include_unparseable_date=True):
    rows = [
        order_row(date=day, available_at=day + "T16:00:00+08:00")
        for day in days
    ]
    if include_unparseable_date:
        rows.append(order_row(date="not-a-session"))
    conflict_day = days[10]
    rows.extend(
        [
            order_row(
                date=conflict_day,
                securities_trader_id="001",
                available_at=conflict_day + "T16:00:00+08:00",
                buy=999.0,
            ),
            order_row(
                date=conflict_day,
                securities_trader_id="002",
                available_at=conflict_day + "T16:00:00+08:00",
                buy=12.0,
                sell=3.0,
            ),
            order_row(
                date=days[-1],
                securities_trader_id="001",
                available_at=days[-1] + "T16:00:00+08:00",
            ),
            order_row(
                date="2026-09-23",
                available_at="2026-09-23T16:00:00+08:00",
            ),
            order_row(market="TPEX"),
            order_row(stock_id="9999", market="TWSE"),
        ]
    )
    return rows


def pinned_route(root, rows, *, status="PARTIAL"):
    descriptor = write_source(root, ROUTE_ID, rows, status)
    artifact_path = Path(descriptor["path"])
    descriptor["source_relative_path"] = artifact_path.relative_to(root).as_posix()
    return descriptor


def build_projection(root, descriptor, universe, parent_identity):
    entry = {
        "route_id": ROUTE_ID,
        "relative_path": descriptor["source_relative_path"],
        "sha256": descriptor["sha256"],
        "size": descriptor["size"],
    }
    return query_published_rows(
        root,
        entry,
        ROUTE_ID,
        stock_ids=sorted(set(universe["stock_id"].astype(str))),
        parent_identity=parent_identity,
        source_identity={"route_id": ROUTE_ID, "artifact_sha256": descriptor["sha256"]},
        allow_build=True,
        field_map=FIELD_MAP,
    )


def load_projected(cache_dir, root, descriptor, universe, calendar, parent_identity):
    return load_order21_support(
        cache_dir,
        descriptor,
        universe,
        asof=calendar["open_dates"][-1],
        calendar_contract=calendar,
        snapshot_id="fixture-snapshot",
        source_root=root,
        parent_identity=parent_identity,
        source_identity={"route_id": ROUTE_ID, "artifact_sha256": descriptor["sha256"]},
    )


def test_existing_shards_match_retained_window_rollup_and_separate_report_windows(tmp_path):
    root = tmp_path / "published"
    calendar = history_calendar()
    universe = build_universe(stocks())
    descriptor = pinned_route(
        root, history_rows(calendar["open_dates"], include_unparseable_date=False)
    )
    parent = hashlib.sha256(b"pinned manifest bytes").hexdigest()
    projection = build_projection(root, descriptor, universe, parent)
    assert projection.status == "built"

    legacy = load_order21_support(
        tmp_path / "legacy-cache",
        descriptor,
        universe,
        asof=calendar["open_dates"][-1],
        calendar_contract=calendar,
        snapshot_id="fixture-snapshot",
    )
    sharded = load_projected(
        tmp_path / "sharded-cache", root, descriptor, universe, calendar, parent
    )

    assert sharded["order21_support_by_identity"] == legacy["order21_support_by_identity"]
    assert sharded["order21_support_by_code"] == legacy["order21_support_by_code"]
    assert sharded["order21_text_by_identity"] == legacy["order21_text_by_identity"]
    actual = sharded["order21_support_by_code"]["2330"]
    assert actual["order21_valid_sessions"] == len(calendar["open_dates"])
    assert actual["order21_feature_observation_counts"]["60"]["n_expected"] == 60
    assert actual["order21_feature_observation_counts"]["60"]["n_obs"] == 60
    assert tuple(map(int, actual["order21_feature_observation_counts"])) == REPORT_WINDOW_SESSIONS
    seal = json.loads(
        Path(sharded["order21_reader"]["cache_path"])
        .with_suffix(".complete.json")
        .read_text(encoding="utf-8")
    )
    assert seal["identity"]["version"] == order21_reader.VERSION
    assert seal["identity"]["artifact_sha256"] == descriptor["sha256"]
    assert seal["identity"]["artifact_size_bytes"] == descriptor["size"]
    assert seal["identity"]["asof"] == calendar["open_dates"][-1]
    assert seal["artifacts"]["parquet"]["sha256"]
    assert seal["artifacts"]["stats"]["sha256"]

    legacy_stats = json.loads(
        Path(legacy["order21_reader"]["cache_path"]).with_suffix(".json").read_text(encoding="utf-8")
    )
    sharded_stats = json.loads(
        Path(sharded["order21_reader"]["cache_path"]).with_suffix(".json").read_text(encoding="utf-8")
    )
    assert sharded_stats == legacy_stats
    assert sharded["order21_reader"]["query_demand"]["score"]["history_scope"] == "eligible_observations_within_four_calendar_month_window"
    assert sharded["order21_reader"]["query_demand"]["projection"]["start_date"] == "2026-05-22"
    assert sharded["order21_reader"]["query_demand"]["projection"]["end_date"] == calendar["open_dates"][-1]
    assert sharded["order21_reader"]["cache_read_mode"] == "query_projection"
    assert sharded["order21_reader"]["external_read"]["cache_hit"]
    assert sharded["order21_reader"]["external_read"]["source_bytes_read"] == 0
    assert sharded["order21_reader"]["external_read"]["projection_bytes_read"] > 0
    assert sharded["order21_reader"]["external_read"]["consumer_rows_scanned"] > 0
    canonical_parent = hashlib.sha256(
        json.dumps(
            parent, ensure_ascii=False, sort_keys=True, separators=(",", ":"),
            allow_nan=False,
        ).encode("utf-8")
    ).hexdigest()
    assert sharded["order21_reader"]["projection_lineage"]["requested_parent_identity_sha256"] == canonical_parent


def test_warm_derived_cache_does_not_call_projection_or_open_raw_source(tmp_path, monkeypatch):
    root = tmp_path / "published"
    calendar = history_calendar(count=12)
    universe = build_universe(stocks())
    descriptor = pinned_route(root, history_rows(calendar["open_dates"]))
    parent = hashlib.sha256(b"warm cache manifest").hexdigest()
    build_projection(root, descriptor, universe, parent)
    cache_dir = tmp_path / "derived-cache"

    first = load_projected(cache_dir, root, descriptor, universe, calendar, parent)
    assert not first["order21_reader"]["cache_hit"]
    Path(descriptor["path"]).unlink()
    calls = []

    def forbidden_projection(*args, **kwargs):
        calls.append((args, kwargs))
        raise AssertionError("warm derived cache must bypass projection")

    monkeypatch.setattr(order21_reader, "query_published_rows", forbidden_projection)
    second = load_projected(cache_dir, root, descriptor, universe, calendar, parent)
    assert second["order21_reader"]["cache_hit"]
    assert second["order21_reader"]["cache_read_mode"] == "derived_cache_hit"
    assert second["order21_reader"]["external_read"]["source_bytes_read"] == 0
    assert second["order21_reader"]["derived_cache_bytes_verified"] > 0
    assert not calls
    assert second["order21_support_by_identity"] == first["order21_support_by_identity"]


def test_tampered_derived_cache_is_invalidated_and_rebuilt(tmp_path):
    root = tmp_path / "published"
    calendar = history_calendar(count=12)
    universe = build_universe(stocks())
    descriptor = pinned_route(root, history_rows(calendar["open_dates"]))
    parent = hashlib.sha256(b"tampered derived cache manifest").hexdigest()
    build_projection(root, descriptor, universe, parent)
    cache_dir = tmp_path / "derived-cache"
    first = load_projected(cache_dir, root, descriptor, universe, calendar, parent)
    parquet = Path(first["order21_reader"]["cache_path"])
    parquet.write_bytes(parquet.read_bytes() + b"tamper")

    rebuilt = load_projected(cache_dir, root, descriptor, universe, calendar, parent)
    assert not rebuilt["order21_reader"]["cache_hit"]
    assert rebuilt["order21_reader"]["cache_invalidated_reason"] == "parquet_size_mismatch"
    assert rebuilt["order21_reader"]["cache_read_mode"] == "query_projection"
    assert rebuilt["order21_support_by_identity"] == first["order21_support_by_identity"]
    assert parquet.with_suffix(".complete.json").is_file()


def test_changed_pinned_sha_invalidates_derived_cache_and_records_cold_fullscan(tmp_path):
    root = tmp_path / "published"
    calendar = history_calendar(count=8)
    universe = build_universe(stocks())
    parent = hashlib.sha256(b"changed-source manifest").hexdigest()
    first_descriptor = pinned_route(root, [order_row()])
    cache_dir = tmp_path / "derived-cache"
    first = load_projected(cache_dir, root, first_descriptor, universe, calendar, parent)

    changed_descriptor = pinned_route(root, [order_row(), order_row(securities_trader_id="002", buy=9.0)])
    second = load_projected(cache_dir, root, changed_descriptor, universe, calendar, parent)
    assert not second["order21_reader"]["cache_hit"]
    assert first["order21_reader"]["cache_path"] != second["order21_reader"]["cache_path"]
    assert second["order21_reader"]["cold_fullscan_reason"] == "projection_not_built/four_calendar_month_window"
    assert second["order21_reader"]["external_read"]["mode"] == "cold_fullscan_no_projection"
    external = second["order21_reader"]["external_read"]
    assert external["source_bytes_read"] == changed_descriptor["size"] * 2
    assert external["source_hash_bytes_read"] == changed_descriptor["size"]
    assert external["duckdb_source_bytes_read"] == changed_descriptor["size"]
    assert external["source_read_passes"] == 2
    assert external["source_sha256_verified"] == changed_descriptor["sha256"]


def test_cold_fullscan_rejects_raw_source_sha_mismatch(tmp_path, monkeypatch):
    root = tmp_path / "published"
    calendar = history_calendar(count=8)
    universe = build_universe(stocks())
    descriptor = pinned_route(root, [order_row()])

    def projection_missing(*args, **kwargs):
        raise ProjectionUnavailableError("projection_not_built")

    monkeypatch.setattr(order21_reader, "query_published_rows", projection_missing)
    source_path = Path(descriptor["path"])
    payload = source_path.read_bytes()
    source_path.write_bytes(bytes([payload[0] ^ 1]) + payload[1:])
    with pytest.raises(ValueError, match="SHA-256 mismatch"):
        load_projected(
            tmp_path / "mismatch-cache",
            root,
            descriptor,
            universe,
            calendar,
            hashlib.sha256(b"mismatched raw source").hexdigest(),
        )


def test_projection_unsupported_falls_back_but_integrity_lock_and_caller_errors_propagate(tmp_path, monkeypatch):
    root = tmp_path / "published"
    calendar = history_calendar(count=8)
    universe = build_universe(stocks())
    descriptor = pinned_route(root, [order_row()])
    parent = hashlib.sha256(b"fallback manifest").hexdigest()
    cache_dir = tmp_path / "derived-cache"

    def unsupported(*args, **kwargs):
        raise UnsupportedProjectionError("unsupported_fixture_route")

    monkeypatch.setattr(order21_reader, "query_published_rows", unsupported)
    result = load_projected(cache_dir, root, descriptor, universe, calendar, parent)
    assert result["order21_reader"]["cache_read_mode"] == "unsupported_projection_fallback"
    assert result["order21_reader"]["projection_fallback_reason"] == "unsupported_fixture_route"
    assert result["order21_reader"]["external_read"]["source_bytes_read"] == descriptor["size"] * 2

    for error in (
        ProjectionIntegrityError("projection_shard_hash_mismatch"),
        ProjectionLockTimeoutError("projection_lock_timeout"),
        ProjectionUnavailableError("projection_disabled_for_other_reason"),
        ValueError("invalid caller field map"),
    ):
        changed_cache = tmp_path / ("error-cache-" + type(error).__name__)

        def raise_error(*args, _error=error, **kwargs):
            raise _error

        monkeypatch.setattr(order21_reader, "query_published_rows", raise_error)
        with pytest.raises(type(error)):
            load_projected(changed_cache, root, descriptor, universe, calendar, parent)
