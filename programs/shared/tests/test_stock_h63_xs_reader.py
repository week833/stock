"""Offline tests of the read-only H63 U7 snapshot API (stock_h63_xs_reader; ADR-0009)."""
from __future__ import annotations

import json
import os
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import stock_h63_xs_ledger as ledger  # noqa: E402
import stock_h63_xs_reader as reader  # noqa: E402
from stock_h63_xs_fixture import write_fixture  # noqa: E402


def _tree(root):
    return sorted((str(p.relative_to(root)), p.stat().st_mtime_ns, p.stat().st_size) for p in root.rglob("*") if p.is_file())


def test_latest_snapshot_is_verified_and_read_only(tmp_path):
    write_fixture(tmp_path, signal_date="2026-10-16")
    before = _tree(tmp_path)
    snap = reader.load_latest(tmp_path, expected_signal_date="20261016")
    row = snap.row("2330")
    assert row["p"] == pytest.approx(0.584) and len(row["row_sha256"]) == 64
    assert snap.row("9999") is None and snap.lag_weekdays == 0 and snap.eligible_count == 3
    assert _tree(tmp_path) == before  # the reader never writes


@pytest.mark.parametrize("expected,reason,lag", [
    ("2026-10-15", "h63xs_future_signal", None),
    ("2026-10-26", "h63xs_stale", None),   # 6 weekdays after Friday 20261016
    ("2026-10-23", None, 5),
    ("2026-10-22", None, 4),
])
def test_signal_date_rules(tmp_path, expected, reason, lag):
    write_fixture(tmp_path, signal_date="2026-10-16")
    if reason is None:
        assert reader.load_latest(tmp_path, expected_signal_date=expected).lag_weekdays == lag
    else:
        with pytest.raises(reader.H63XsUnavailable) as caught:
            reader.load_latest(tmp_path, expected_signal_date=expected)
        assert caught.value.reason == reason and caught.value.reason_zh


def test_missing_or_tampered_snapshots_are_unavailable(tmp_path):
    with pytest.raises(reader.H63XsUnavailable) as caught:
        reader.load_latest(tmp_path)
    assert caught.value.reason == "h63xs_no_latest"
    rec = write_fixture(tmp_path)
    parquet = Path(rec["block"]["parquet"])
    raw = bytearray(parquet.read_bytes())
    raw[-12] ^= 1
    os.chmod(parquet, 0o666)
    parquet.write_bytes(bytes(raw))
    with pytest.raises(reader.H63XsUnavailable) as caught:
        reader.load_latest(tmp_path)
    assert caught.value.reason == "h63xs_snapshot_sha_mismatch"


def test_model_pointer_integrity_is_required(tmp_path):
    write_fixture(tmp_path)
    pointer_path = tmp_path / "models" / "current.json"
    pointer = json.loads(pointer_path.read_text(encoding="utf-8"))
    pointer["model_id"] = "edited"
    pointer_path.write_text(json.dumps(pointer), encoding="utf-8")
    with pytest.raises(reader.H63XsUnavailable) as caught:
        reader.load_latest(tmp_path)
    assert caught.value.reason == "h63xs_model_integrity"


@pytest.mark.parametrize("content,mode,valid", [
    (None, "auto", True), ('{"mode": "off", "reason_zh": "資料異常"}', "off", True),
    ('{"mode": "legacy"}', "legacy", True), ('{"mode": "bogus"}', "auto", False), ("not json", "auto", False),
])
def test_display_config(tmp_path, content, mode, valid):
    if content is not None:
        (tmp_path / "config").mkdir()
        (tmp_path / "config" / "display.json").write_text(content, encoding="utf-8")
    config = reader.read_display_config(tmp_path)
    assert config["mode"] == mode and config["valid"] is valid


def test_every_reason_has_chinese_text_and_disclosure_is_versioned():
    assert all(text.strip() for text in reader.REASON_TEXT_ZH.values())
    assert reader.DISCLOSURE_VERSION == "h63-xs-disclosure-v1"
    text = json.dumps(reader.DISCLOSURE_ROWS_ZH, ensure_ascii=False)
    assert "不是上漲機率" in text and "上漲機率" not in text.replace("不是上漲機率", "")
    assert "2026-" not in text  # people see YYYYMMDD


def test_forward_summary_is_optional(tmp_path):
    assert reader.load_forward_summary(tmp_path) is None
    write_fixture(tmp_path)
    ledger.review(tmp_path)
    assert reader.load_forward_summary(tmp_path)["verdict"] == "insufficient_data"
