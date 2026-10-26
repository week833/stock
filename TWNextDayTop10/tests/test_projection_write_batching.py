"""Bounded projection writes: legacy bytes, fewer opens, and failure cleanup."""
from __future__ import annotations

import builtins
import collections
import hashlib
import json
import sys
from pathlib import Path

import pytest

SOURCE = Path(__file__).resolve().parents[2] / "programs" / "single_stock_analysis" / "src"
SHARED = Path(__file__).resolve().parents[2] / "programs" / "shared"
for directory in (SOURCE, SHARED):
    if str(directory) not in sys.path:
        sys.path.insert(0, str(directory))

import stock_query_projection as projection  # noqa: E402


def _records(stocks=80, rounds=5, *, large_row=False):
    records = []
    for round_index in range(rounds):
        for index in range(stocks):
            stock = str(1000 + index)
            day = None if index == 0 and round_index == 1 else f"2026-10-{round_index + 1:02d}"
            row = {"stock_id": stock, "date": day, "close": 100.0 + round_index,
                   "name": "\u53f0\u80a1", "nested": {"values": [1, None, "x"]}}
            if large_row and index == 1 and round_index == 2:
                row["large"] = "x" * 70000
            records.append((stock, "__undated__" if day is None else "__stock__",
                            day, len(records), row))
    return records


def _legacy_chunks(records, chunk_rows, chunk_bytes):
    """Reference bytes grouped from the original source-order envelope contract."""
    grouped = collections.OrderedDict()
    for stock, period, day, ordinal, row in records:
        wrapper = {"__sequence": ordinal, "__date": day, "__stock": stock, "row": row}
        line = json.dumps(wrapper, ensure_ascii=False, separators=(",", ":"),
                          allow_nan=False).encode("utf-8") + b"\n"
        grouped.setdefault((stock, period), []).append(line)
    expected = {}
    for key, lines in grouped.items():
        chunks, pending, pending_size = [], [], 0
        for line in lines:
            if pending and (len(pending) == chunk_rows or pending_size + len(line) > chunk_bytes):
                chunks.append(b"".join(pending))
                pending, pending_size = [], 0
            pending.append(line)
            pending_size += len(line)
        if pending:
            chunks.append(b"".join(pending))
        for index, content in enumerate(chunks):
            expected[(*key, index)] = content
    return expected


@pytest.mark.parametrize(("chunk_rows", "chunk_bytes"), [(2, 1 << 20), (999, 512)])
def test_writer_preserves_legacy_bytes_hashes_ordinals_and_chunk_boundaries(
        tmp_path, chunk_rows, chunk_bytes):
    records = _records(large_row=True)
    expected = _legacy_chunks(records, chunk_rows, chunk_bytes)
    pool = projection._WriterPool(tmp_path)
    for record in records:
        pool.append(*record, chunk_rows, chunk_bytes)
    actual = pool.finish()
    assert len(actual) == len(expected)
    for shard in actual:
        key = (shard["stock_key"], shard["period"], shard["chunk_index"])
        content = Path(shard["temp_path"]).read_bytes()
        assert content == expected.pop(key)
        envelopes = [json.loads(line) for line in content.splitlines()]
        assert shard["sha256"] == hashlib.sha256(content).hexdigest()
        assert shard["size_bytes"] == len(content)
        assert shard["rows"] == len(envelopes)
        assert shard["dates"] == sorted({row["__date"] for row in envelopes if row["__date"]})
        assert shard["first_ordinal"] == envelopes[0]["__sequence"]
        assert shard["last_ordinal"] == envelopes[-1]["__sequence"]
    assert not expected
    assert not pool.pending and pool.pending_bytes == 0 and not pool.handles


@pytest.mark.parametrize("pressured", [False, True])
def test_interleaved_stocks_keep_pending_and_handle_bounds_and_reduce_opens(
        tmp_path, monkeypatch, pressured):
    if pressured:
        monkeypatch.setattr(projection, "_WRITE_BUFFER_BYTES", 1024)
        monkeypatch.setattr(projection, "_MAX_PENDING_WRITE_BYTES", 65536)
    records = _records(stocks=96, rounds=12)
    opens = []
    native_open = builtins.open

    def counted_open(path, mode="r", *args, **kwargs):
        if mode == "ab":
            opens.append(str(path))
        return native_open(path, mode, *args, **kwargs)

    monkeypatch.setattr(projection, "open", counted_open, raising=False)
    pool = projection._WriterPool(tmp_path)
    for record in records:
        pool.append(*record, 4096, 8 * 1024 * 1024)
        assert pool.max_open == 64 and len(pool.handles) <= 64
        assert 0 <= pool.pending_bytes <= pool.max_pending_bytes
        assert pool.pending_bytes == sum(len(state["pending"]) for state in pool.pending.values())
        assert all(len(state["pending"]) <= pool.buffer_bytes for state in pool.states.values())
    chunks = pool.finish()
    assert sum(shard["rows"] for shard in chunks) == len(records)
    assert len(opens) < len(records) // 2
    if not pressured:
        # Each current stock shard, plus the one undated shard, is opened once.
        assert len(opens) == 97
    assert not pool.pending and pool.pending_bytes == 0 and not pool.handles


@pytest.mark.parametrize("failure", ["nonfinite", "not_serializable", "flush"])
def test_builder_keeps_serialization_errors_and_aborts_pending_writes(
        tmp_path, monkeypatch, failure):
    valid = {"stock_id": "2330", "date": "2026-10-16", "close": 100.0}
    if failure == "nonfinite":
        raw = (b'[{"stock_id":"2330","date":"2026-10-16","close":100.0},'
               b'{"stock_id":"2454","date":"2026-10-16","close":1e999}]')
    else:
        raw = json.dumps([valid, dict(valid, stock_id="2454")]).encode("utf-8")
    source = tmp_path / "source.json"
    source.write_bytes(raw)
    descriptor = {"path": source.name, "sha256": hashlib.sha256(raw).hexdigest(), "size": len(raw)}
    prepared = projection._prepare_descriptor(tmp_path, descriptor, "official_market_daily", "b" * 64, None)
    identity = projection._physical_identity(prepared)
    key = projection._cache_key(prepared)
    registry = tmp_path / "registry"
    native_pool = projection._WriterPool
    pools = []

    class RecordingPool(native_pool):
        def __init__(self, *args, **kwargs):
            super().__init__(*args, **kwargs)
            pools.append(self)

    monkeypatch.setattr(projection, "_WriterPool", RecordingPool)
    if failure == "not_serializable":
        bad = dict(valid, stock_id="2454", invalid={1})
        monkeypatch.setattr(projection, "_iter_json_rows", lambda stream: iter([valid, bad]))
    elif failure == "flush":
        native_flush = native_pool._flush_state

        def fail_second_flush(self, state):
            if self.handles:
                raise OSError("fixture_flush_failure")
            return native_flush(self, state)

        monkeypatch.setattr(RecordingPool, "_flush_state", fail_second_flush)

    redundant_row_serializations = []
    native_dumps = json.dumps

    def counted_dumps(value, *args, **kwargs):
        if isinstance(value, dict) and "stock_id" in value and "date" in value:
            redundant_row_serializations.append(value)
        return native_dumps(value, *args, **kwargs)

    monkeypatch.setattr(projection.json, "dumps", counted_dumps)
    with pytest.raises(projection.ProjectionIntegrityError, match="^pinned_artifact_parse_failed$"):
        projection._build_projection(prepared, registry, key, identity, None, 4096, 8 * 1024 * 1024)
    assert not redundant_row_serializations
    assert len(pools) == 1
    assert not pools[0].pending and pools[0].pending_bytes == 0 and not pools[0].handles
    assert all(not state["pending"] for state in pools[0].states.values())
    assert not list((registry / "staging").iterdir())
    assert not (registry / "indexes").exists()
    assert not (registry / "complete").exists()

    if failure != "flush":
        direct_root = tmp_path / "direct"
        direct_root.mkdir()
        direct = native_pool(direct_root)
        direct.append("2330", "__stock__", "2026-10-16", 0, valid, 4096, 8 * 1024 * 1024)
        bad = dict(valid, invalid=float("inf") if failure == "nonfinite" else {1})
        with pytest.raises(projection.ProjectionIntegrityError, match="^row_not_json_serializable$"):
            direct.append("2330", "__stock__", "2026-10-16", 1, bad, 4096, 8 * 1024 * 1024)
        direct.abort()
        assert not direct.pending and direct.pending_bytes == 0 and not direct.handles


def _fixture_query(root, rows, **kwargs):
    raw = json.dumps({"data": rows}, ensure_ascii=False, separators=(",", ":"),
                     allow_nan=False).encode("utf-8")
    source = root / "official.json"
    source.write_bytes(raw)
    descriptor = {"path": source.name, "sha256": hashlib.sha256(raw).hexdigest(), "size": len(raw)}
    return projection.query_published_rows(root, descriptor, "official_market_daily",
        parent_identity="b" * 64, source_identity={"fixture": True}, chunk_rows=2, **kwargs)


def test_stock_shard_order_preserves_stock_rows_predicates_and_columns_without_spooling(tmp_path):
    rows = [{"stock_id": str(1000 + stock), "date": f"2026-10-{day + 1:02d}",
             "marker": day * 130 + stock, "unused": "kept only in the sealed shard"}
            for day in range(3) for stock in range(130)]
    options = {"start_date": "2026-10-02", "end_date": "2026-10-03",
               "columns": ["stock_id", "date", "marker"]}
    source_order = _fixture_query(tmp_path, rows, **options)
    stock_order = _fixture_query(tmp_path, rows, **options)
    try:
        expected = list(source_order.iter_rows())
        actual = list(stock_order.iter_rows(order="stock_shard"))
        assert len(expected) == len(actual) == 260
        assert actual == sorted(expected, key=lambda row: row["stock_id"])
        assert all(set(row) == {"stock_id", "date", "marker"} for row in actual)
        assert source_order.metrics["spool_components"] == 1
        assert "iteration_order" not in source_order.metrics
        assert stock_order.metrics["spool_components"] == 0
        assert stock_order.metrics["spool_rows_written"] == stock_order.metrics["spool_rows_read"] == 0
        assert stock_order.metrics["iteration_order"] == "stock_shard"
        assert stock_order.metrics["stock_sequence_preserved"] is True
        assert stock_order.metrics["source_sequence_preserved"] is False
        assert stock_order.metrics["peak_open_shard_streams"] == 1
    finally:
        source_order.close()
        stock_order.close()
    with pytest.raises(projection.ProjectionUnavailableError, match="projection_query_closed"):
        list(stock_order.iter_rows(order="stock_shard"))


@pytest.mark.parametrize("invalid", ["duplicate", "outside_source"])
def test_stock_shard_order_keeps_cross_shard_sequence_integrity(tmp_path, invalid):
    rows = [{"stock_id": stock, "date": f"2026-10-{day + 1:02d}", "marker": day * 2 + index}
            for day in range(2) for index, stock in enumerate(("2330", "2454"))]

    def changed_result():
        result = _fixture_query(tmp_path, rows)
        shard = next(item for item in result.selected if item["stock_key"] == "2454")
        path = projection._query_object_path(result.query_root, shard)
        wrappers = [json.loads(line) for line in path.read_bytes().splitlines()]
        if invalid == "duplicate":
            wrappers[0]["__sequence"] = 0
            shard["first_ordinal"] = 0
        else:
            wrappers[-1]["__sequence"] = result.manifest["rows"]
            shard["last_ordinal"] = result.manifest["rows"]
        content = b"".join(json.dumps(item, ensure_ascii=False, separators=(",", ":"),
                                      allow_nan=False).encode("utf-8") + b"\n" for item in wrappers)
        digest = hashlib.sha256(content).hexdigest()
        relative = "objects/" + digest + ".jsonl"
        (result.query_root / relative).write_bytes(content)
        shard.update(path=relative, sha256=digest, size_bytes=len(content))
        return result

    if invalid == "duplicate":
        original_order = changed_result()
        try:
            with pytest.raises(projection.ProjectionIntegrityError, match="^projection_duplicate_sequence$"):
                list(original_order.iter_rows())
        finally:
            original_order.close()
    stock_order = changed_result()
    expected = "projection_duplicate_sequence" if invalid == "duplicate" else "projection_sequence_outside_source_bounds"
    try:
        with pytest.raises(projection.ProjectionIntegrityError, match="^" + expected + "$"):
            list(stock_order.iter_rows(order="stock_shard"))
    finally:
        stock_order.close()
