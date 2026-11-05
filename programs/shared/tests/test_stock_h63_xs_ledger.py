"""Offline tests of the H63 U7 forward record (stock_h63_xs_ledger; ADR-0009)."""
from __future__ import annotations

import datetime as dt
import json
import sys
from pathlib import Path

import numpy as np
import pandas as pd
import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import stock_h63_xs_ledger as ledger  # noqa: E402
from stock_h63_xs_fixture import _frame, FIXTURE_ROWS, write_fixture  # noqa: E402

PREREG_V3_SHA256 = "f0afc46b0f6ceb86f572e7d8a1179b1dd490a351bc03e857430dcfceca052356"


def test_preregistered_review_v3_is_frozen():
    # Frozen before the first forward record; any change needs a new registration.
    assert ledger.PREREGISTERED_REVIEW_V3_SHA256 == PREREG_V3_SHA256
    v3 = ledger.PREREGISTERED_REVIEW_V3
    assert v3["expected"]["band_coverage_80"] == [0.72, 0.88]
    assert v3["rollback"]["band_coverage_80_outside"] == [0.64, 0.96]
    assert v3["base"]["sha256"].startswith("947cdbef")


def test_block_is_written_once_and_repeats_are_classified(tmp_path):
    first = write_fixture(tmp_path)
    assert first["block"]["status"] == "recorded" and first["block"]["indexed"] is True
    latest = json.loads((tmp_path / "predictions" / "latest.json").read_text(encoding="utf-8"))
    assert latest["schema"] == ledger.LATEST_SCHEMA and latest["decision_date"] == "2026-10-16"
    again = write_fixture(tmp_path)
    assert again["block"]["status"] == "already_recorded"
    header = {"decision_date": "2026-10-16", "run_id": "x", "key_material": {"decision_date": "2026-10-16",
              "fixture": True, "model_sha256": first["model"]["model_sha256"]}}
    changed = first["frame"].copy()
    changed.loc[0, "p"] = 0.51
    out = ledger.record_block(tmp_path, changed, header)
    assert out["status"] == "conflicting_repeat"
    events = [json.loads(line)["event"] for line in (tmp_path / "state" / "forward_events.jsonl").read_text(
        encoding="utf-8").splitlines()]
    assert events.count("already_recorded") == 1 and events.count("conflicting_repeat") == 1
    lines, chain = ledger.read_index(ledger.Paths(tmp_path))
    assert len(lines) == 1 and chain["ok"]


def test_index_hash_chain_detects_edits(tmp_path):
    write_fixture(tmp_path, signal_date="2026-10-15")
    write_fixture(tmp_path, signal_date="2026-10-16")
    path = tmp_path / "state" / "forward_index.jsonl"
    lines, chain = ledger.read_index(ledger.Paths(tmp_path))
    assert chain["ok"] and len(lines) == 2
    text = path.read_text(encoding="utf-8").splitlines()
    text[0] = text[0].replace('"row_count":4', '"row_count":5')
    path.write_text("\n".join(text) + "\n", encoding="utf-8")
    _, chain = ledger.read_index(ledger.Paths(tmp_path))
    assert not chain["ok"] and chain["broken_at"] == 2


def test_rows_sha_is_format_independent():
    frame = _frame("2026-10-16", FIXTURE_ROWS)
    roundtrip = pd.read_parquet(__import__("io").BytesIO(frame.to_parquet(index=False)))
    assert ledger.rows_sha256(frame) == ledger.rows_sha256(roundtrip)


def test_prospective_status_uses_the_next_verified_session():
    sessions = pd.DatetimeIndex(["2026-10-16", "2026-10-19"])
    assert ledger.prospective_status("2026-10-18T23:00:00.000000Z", "2026-10-16", sessions) == "prospective"
    assert ledger.prospective_status("2026-10-19T01:30:00.000000Z", "2026-10-16", sessions) == "late"
    assert ledger.prospective_status("2026-10-17T00:00:00.000000Z", "2026-10-19", sessions) == "unknown"


def _panel(sessions, decision, r63):
    rows = []
    for sid, value in r63.items():
        for day in sessions:
            matured = day == decision and value is not None
            rows.append({"stock_id": sid, "decision_date": day, "close": 100.0,
                         "r63": value if matured else np.nan,
                         "label_end": sessions[-1] if matured else pd.NaT})
    return pd.DataFrame(rows)


def test_settle_freeze_and_review_are_derived_only(tmp_path):
    rec = write_fixture(tmp_path, signal_date="2026-10-16",
                        recorded_at=dt.datetime(2026, 10, 16, 15, 0, tzinfo=dt.timezone.utc))
    block_before = Path(rec["block"]["parquet"]).read_bytes()
    sessions = pd.bdate_range("2026-10-16", periods=80)
    P = _panel(sessions, pd.Timestamp("2026-10-16"), {"2330": 0.10, "2317": -0.05, "6488": 0.02, "2454": 0.30})
    out = ledger.settle(tmp_path, P, sessions, price_vintage="fixture")
    assert out["frozen_now"] == 1 and out["status_counts"] == {"matured": 4}
    settled = pd.read_parquet(tmp_path / "state" / "settlement" / "settled_rows.parquet")
    assert set(settled["prospective"]) == {"prospective"}
    frozen = pd.read_parquet(next((tmp_path / "state" / "settlement" / "frozen").glob("*.parquet")))
    # median of eligible matured rows (2330, 2317, 2454) = 0.10
    assert frozen["block_median"].iloc[0] == pytest.approx(0.10)
    again = ledger.settle(tmp_path, P, sessions, price_vintage="fixture")
    assert again["frozen_now"] == 0  # frozen once
    assert Path(rec["block"]["parquet"]).read_bytes() == block_before  # never rewrites a prediction row
    summary = ledger.review(tmp_path)
    assert summary["verdict"] == "insufficient_data" and summary["verdict_zh"] == "資料不足"
    assert summary["prereg_review_sha256"] == PREREG_V3_SHA256
    assert "資料不足" in (tmp_path / "state" / "review_zh.txt").read_text(encoding="utf-8")


def test_pending_stock_that_leaves_the_head_is_archived_once(tmp_path):
    write_fixture(tmp_path, signal_date="2026-10-16")
    sessions = pd.bdate_range("2026-10-16", periods=10)
    P = _panel(sessions, pd.Timestamp("2026-10-16"), {"2330": None, "2317": None, "6488": None, "2454": None})
    ledger.settle(tmp_path, P, sessions, price_vintage="v1")
    later = P[P["stock_id"] != "6488"]
    ledger.settle(tmp_path, later, sessions, price_vintage="v2")
    archived = list((tmp_path / "state" / "settlement" / "delisted_prices").glob("6488_*.parquet"))
    assert len(archived) == 1


def test_verdict_rules_on_synthetic_blocks():
    blocks = pd.DataFrame({"valid": [True] * 4, "ic": [0.1] * 4, "auc": [0.56] * 4, "brier": [0.247] * 4,
                           "coverage": [0.80] * 4, "accuracy": [0.55] * 4, "always_beats": [0.5] * 4,
                           "min_bucket_coverage": [0.75] * 4})
    assert ledger._verdict(blocks)[0] == "as_expected"
    worse = blocks.assign(ic=-0.01)
    assert ledger._verdict(worse)[0] == "attention"
    severe = blocks.copy()
    severe.loc[3, "coverage"] = 0.6
    assert ledger._verdict(severe)[0] == "rollback_suggested"
    assert ledger._verdict(blocks.iloc[:2])[0] == "insufficient_data"
