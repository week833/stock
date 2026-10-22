"""Order21 is report-only: bounded resources degrade support, integrity still fails closed."""
import hashlib
import shutil
from collections import namedtuple
from pathlib import Path

import duckdb

import order21_reader
from data_reader import build_universe
from test_order21_query_demand import history_calendar, history_rows, load_projected, pinned_route
from test_reader_order21_v4 import stocks


def _fixture(tmp_path, label):
    root = tmp_path / "published"
    calendar = history_calendar(count=12)
    universe = build_universe(stocks())
    descriptor = pinned_route(root, history_rows(calendar["open_dates"]))
    parent = hashlib.sha256(label.encode("utf-8")).hexdigest()
    return root, calendar, universe, descriptor, parent


def test_strict_out_of_memory_degrades_without_sealing_cache(tmp_path, monkeypatch):
    root, calendar, universe, descriptor, parent = _fixture(tmp_path, "oom manifest")

    def exhausted(*args, **kwargs):
        raise duckdb.OutOfMemoryException("Out of Memory Error: failed to pin block")

    monkeypatch.setattr(order21_reader, "_project", exhausted)
    result = load_projected(tmp_path / "cache", root, descriptor, universe, calendar, parent)
    reader = result["order21_reader"]
    assert reader["cache_read_mode"] == "resource_unavailable"
    assert reader["external_read"]["mode"] == "resource_unavailable"
    assert reader["reader_error"].startswith("OutOfMemoryException")
    assert not reader["cache_hit"]
    assert not Path(reader["cache_path"]).with_suffix(".complete.json").exists()
    support = result["order21_support_by_identity"]
    assert support and all(value["order21_reader_error"] == reader["reader_error"] for value in support.values())
    assert all(not value["order21_branch_analysis"] for value in support.values())


def test_insufficient_stage_space_skips_before_reading_source(tmp_path, monkeypatch):
    root, calendar, universe, descriptor, parent = _fixture(tmp_path, "disk manifest")
    usage = namedtuple("usage", "total used free")
    monkeypatch.setattr(order21_reader.shutil, "disk_usage", lambda path: usage(10, 10, 0))

    def forbidden(*args, **kwargs):
        raise AssertionError("source must not be hashed or scanned without stage space")

    monkeypatch.setattr(order21_reader, "_verify_source_artifact", forbidden)
    monkeypatch.setattr(order21_reader, "_project", forbidden)
    result = load_projected(tmp_path / "cache", root, descriptor, universe, calendar, parent)
    reader = result["order21_reader"]
    assert reader["cache_read_mode"] == "resource_unavailable"
    assert "insufficient_stage_disk_space" in reader["reader_error"]


def test_staged_partitions_are_removed_after_projection(tmp_path):
    root, calendar, universe, descriptor, parent = _fixture(tmp_path, "cleanup manifest")
    cache = tmp_path / "cache"
    result = load_projected(cache, root, descriptor, universe, calendar, parent)
    assert result["order21_reader"]["reader_error"] is None
    assert Path(result["order21_reader"]["cache_path"]).with_suffix(".complete.json").is_file()
    assert not [path for path in (cache / "tmp").iterdir() if path.name.endswith("_stage")]
