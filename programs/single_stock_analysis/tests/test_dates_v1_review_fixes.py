"""Date directive 20261006, label dates_v1: fixes of the two adversarial reviews of the date batch.

* F2 (high): codex's Order21 detail/summary guard in published_single_stock_projection._select (deployed 10-04) keys a
  stock-day and the duplicate-content comparison by the calendar-day VALUE, so an ISO detail row and a YYYYMMDD
  summary row of the same stock-day are still the same stock-day (rejected as mixed), and one row published in two
  spellings is one row (not a natural-key conflict).  PublishedSources.rows already did this (set 1); both read
  paths, with and without the per-stock row cache, must agree.
* C1: chart date ticks print YYYYMMDD for short histories (matplotlib's default day format is YYYY-MM-DD).
* C3: training console lines (train_shared._progress) and the analyzer / tomorrow CLI error lines print YYYYMMDD.
* raw_price_reference_display_note (codex 10-04): the observation day prints YYYYMMDD; the machine field stays ISO.
* F4: the shared date helper is a training source (a change between training and --apply-run fails closed).
Uses codex's literal Order21 fixtures (test_order21_mixed_detail_summary_v1).  Offline, below tmp_path only.
"""
from __future__ import annotations

import copy
import hashlib
import inspect
import json
from pathlib import Path

import numpy as np
import pandas as pd
import pytest

import date_format as cd
import published_single_stock_projection as projection
from stock_data_sources import PublishedSources, SourceSpec
from test_order21_mixed_detail_summary_v1 import DETAIL, ROUTE, SESSIONS, SUMMARY


def _compact(rows):
    out = copy.deepcopy(rows)
    for row in out:
        row["date"] = row["date"].replace("-", "")
    return out


def _reader(root: Path, rows, *, cache: bool):
    root.mkdir(parents=True)
    raw = json.dumps(rows, separators=(",", ":")).encode("utf-8")
    (root / "rows.json").write_bytes(raw)
    entry = dict(route_id=ROUTE, path="rows.json", sha256=hashlib.sha256(raw).hexdigest(), size=len(raw),
                 record_count=len(rows), status="SUCCESS", metadata={})
    (root / "manifest.json").write_text(json.dumps(dict(schema="stock-data-publication-v1", datasets=[entry])),
                                        encoding="utf-8")
    return PublishedSources([SourceSpec("manifest_json", root, "manifest.json", "fixture")],
                            row_cache_dir=root / "cache" if cache else None)


def _both_paths(tmp_path, rows):
    """{(path, cache): result} for projection._select and PublishedSources.rows, with and without the row cache."""
    out = {}
    for cache in (False, True):
        out[("select", cache)] = projection._select(_reader(tmp_path / f"s{int(cache)}", rows, cache=cache), ROUTE,
                                                    "2330", None, None)
        out[("rows", cache)] = _reader(tmp_path / f"r{int(cache)}", rows, cache=cache).rows(ROUTE, stock_id="2330")
    return out


@pytest.mark.parametrize("detail_compact,summary_compact", [(False, True), (True, False), (True, True)])
def test_mixed_detail_summary_stockday_is_rejected_whatever_the_spelling(tmp_path, detail_compact, summary_compact):
    detail = _compact(DETAIL[:3]) if detail_compact else copy.deepcopy(DETAIL[:3])
    summary = _compact(SUMMARY) if summary_compact else copy.deepcopy(SUMMARY)
    for key, item in _both_paths(tmp_path, detail + summary).items():
        assert "order21_mixed_detail_summary:2330|2026-09-24" in item["gaps"], key
        kept = [cd.iso_day(row["date"]) for row in item["records"]]
        assert kept == [SESSIONS[1], SESSIONS[1], SESSIONS[3]], key       # 09-24 excluded in every spelling
        assert item["status"] == "partial" and not item["model_usable"], key
        # rows are never rewritten: what is kept is exactly what was published
        assert all(row in detail + summary for row in item["records"]), key


def test_one_row_in_two_spellings_is_one_row_not_a_conflict(tmp_path):
    rows = copy.deepcopy(SUMMARY) + _compact(SUMMARY[:1])
    for key, item in _both_paths(tmp_path, rows).items():
        assert list(item["records"]) == SUMMARY, key                    # the first published spelling is kept
        assert not [gap for gap in item["gaps"] if "conflict" in gap], key
        assert item["status"] == "available", key


def test_a_real_content_conflict_is_still_reported_across_spellings(tmp_path):
    changed = _compact(SUMMARY[:1])
    changed[0]["securities_trader"] = "other name"
    item = projection._select(_reader(tmp_path / "s", copy.deepcopy(SUMMARY) + changed, cache=False), ROUTE,
                              "2330", None, None)
    assert "trading_daily_report_natural_key_conflict" in item["gaps"]
    assert list(item["records"]) == SUMMARY


def test_iso_only_order21_result_is_unchanged_by_the_value_keys(tmp_path):
    # zero-diff on today's all-ISO data: codex's own expectations (test_order21_mixed_detail_summary_v1)
    item = projection._select(_reader(tmp_path / "s", DETAIL[:3] + SUMMARY, cache=False), ROUTE, "2330", None, None)
    assert "order21_mixed_detail_summary:2330|2026-09-24" in item["gaps"]
    assert [row["date"] for row in item["records"]] == [SESSIONS[1], SESSIONS[1], SESSIONS[3]]


# ---------------------------------------------------------------------------------------------------------------
# C1: chart ticks
# ---------------------------------------------------------------------------------------------------------------
