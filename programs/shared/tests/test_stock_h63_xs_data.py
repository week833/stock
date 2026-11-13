"""Offline tests of the H63 U7 input layer (stock_h63_xs_data; ADR-0009): streaming object reader, supplement
pinning, coverage / board / calendar checks.  No central data is read."""
from __future__ import annotations

import hashlib
import json
import sys
from pathlib import Path

import numpy as np
import pandas as pd
import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import stock_h63_xs_core as core  # noqa: E402
import stock_h63_xs_data as data  # noqa: E402


def _object(tmp_path, rows):
    raw = json.dumps(rows, separators=(",", ":")).encode()
    path = tmp_path / "obj.json"
    path.write_bytes(raw)
    return path, hashlib.sha256(raw).hexdigest(), len(raw)


ROWS = [{"date": "20261029", "stock_id": "2330", "close": 1000.5, "extra": "ignored"},
        {"date": "20261030", "stock_id": "2330", "close": 1010.0},
        {"date": "20261030", "stock_id": "0050", "close": None}]


def test_stream_reader_verifies_sha_size_and_count(tmp_path, monkeypatch):
    monkeypatch.setattr(data, "CHUNK", 16)  # many pieces: the split at '},{' must not lose or join rows
    path, sha, size = _object(tmp_path, ROWS)
    cols = {"date": data.S, "stock_id": data.S, "close": data.F}
    table, info = data.stream_json_array(path, cols, sha256=sha, size=size, record_count=3)
    frame = table.to_pandas()
    assert frame["close"].tolist()[:2] == [1000.5, 1010.0] and np.isnan(frame["close"].iloc[2])
    assert info["records"] == 3 and info["sha256"] == sha
    for kwargs, code in (({"sha256": "0" * 64, "size": size, "record_count": 3}, "sha256"),
                         ({"sha256": sha, "size": size + 1, "record_count": 3}, "size"),
                         ({"sha256": sha, "size": size, "record_count": 4}, "record count")):
        with pytest.raises(data.H63XsDataError) as caught:
            data.stream_json_array(path, cols, **kwargs)
        assert caught.value.code == "h63xs_object_invalid" and code in caught.value.detail


def test_stream_reader_fails_closed_on_a_truncated_object(tmp_path):
    path, sha, size = _object(tmp_path, ROWS)
    path.write_bytes(path.read_bytes()[:-1])
    with pytest.raises(data.H63XsDataError) as caught:
        data.stream_json_array(path, {"date": data.S}, sha256=sha, size=size, record_count=3)
    assert caught.value.code == "h63xs_object_invalid"


def _supplement(folder):
    folder.mkdir(parents=True)
    price = pd.DataFrame({"date": pd.to_datetime(["2025-12-30", "2025-12-31"]).date, "stock_id": "1101",
                          "open": 1.0, "high": 1.0, "low": 1.0, "close": 1.0, "Trading_Volume": 1, "Trading_money": 1})
    board = pd.DataFrame({"date": pd.to_datetime(["2025-12-30", "2025-12-31"]).date, "stock_id": "1101",
                          "market": "TWSE", "close": 1.0})
    price.to_parquet(folder / "price_hist.parquet", index=False)
    board.to_parquet(folder / "board_hist.parquet", index=False)
    manifest = {"files": {name: {"sha256": data.sha256_file(folder / name)} for name in data.SUPPLEMENT_FILES}}
    (folder / "manifest.json").write_text(json.dumps(manifest), encoding="utf-8")
    return data.sha256_file(folder / "manifest.json")


def test_supplement_is_pinned_by_manifest_and_file_sha(tmp_path):
    folder = tmp_path / "supp"
    msha = _supplement(folder)
    price, board, info = data.load_supplement(folder, msha)
    assert len(price) == 2 and info["manifest_sha256"] == msha
    assert pd.api.types.is_datetime64_any_dtype(price["date"]) and pd.api.types.is_datetime64_any_dtype(board["date"])
    with pytest.raises(data.H63XsDataError) as caught:
        data.load_supplement(folder, "0" * 64)
    assert caught.value.code == "h63xs_supplement_sha_mismatch" and caught.value.exit_code == 4
    raw = bytearray((folder / "price_hist.parquet").read_bytes())
    raw[-10] ^= 1
    (folder / "price_hist.parquet").write_bytes(bytes(raw))
    with pytest.raises(data.H63XsDataError):
        data.load_supplement(folder, msha)


def test_price_coverage_detects_a_missing_history():
    days = pd.bdate_range("2022-01-03", periods=10)
    board = pd.DataFrame({"stock_id": "2330", "date": days, "market": "TWSE", "close": 500.0})
    full = pd.DataFrame({"stock_id": "2330", "date": days, "close": 500.0})
    assert data.price_coverage(full, board, days[0], days[-1])["missing_keys"] == 0
    gap = data.price_coverage(full.iloc[:2], board, days[0], days[-1])
    assert gap["missing_keys"] == 8 and gap["missing_share"] > data.MAX_MISSING_PRICE_SHARE


def test_board_gate_counts_rows_and_ratio():
    rows = []
    for day, n_twse, n_tpex in (("2026-10-29", 1000, 800), ("2026-10-30", 700, 800)):
        rows += [{"stock_id": f"{1000 + i}", "date": pd.Timestamp(day), "market": "TWSE"} for i in range(n_twse)]
        rows += [{"stock_id": f"{5000 + i}", "date": pd.Timestamp(day), "market": "TPEx"} for i in range(n_tpex)]
    board = pd.DataFrame(rows)
    px = pd.DataFrame({"stock_id": ["1000", "9999"], "date": pd.Timestamp("2026-10-30")})
    out = data.board_gate(board, px, "2026-10-30", "2026-10-29")
    assert not out["ok"] and out["price_rows_without_board"] == 1
    assert any("TWSE rows 700 < 773" in p for p in out["problems"]) and any("previous" in p for p in out["problems"])
    ok = data.board_gate(board, px.iloc[:1], "2026-10-29", None)
    assert ok["ok"]


def test_deadline_classes_keep_monthly_revenue_rules_only():
    frame = pd.DataFrame({"stock_id": ["2816", "2885", "1101", "1240"],
                          "rule_class": ["monthly_revenue_insurance_115", "monthly_revenue_exception_unconfirmed",
                                         "monthly_revenue_general", "annual_3_months"]})
    classes = data.deadline_classes(frame)
    assert classes == {"2816": "monthly_revenue_insurance_115", "2885": "monthly_revenue_exception_unconfirmed",
                       "1101": "monthly_revenue_general"}
    assert set(core.late_revenue_codes([], classes)) >= {"2816", "2885"}


def test_column_signature_matches_the_parquet_cache_naming():
    # Same naming as the pre-open cache (<object sha>_<sha256(json.dumps(cols))[:10]>.parquet).
    cols = ["date", "stock_id", "open", "high", "low", "close", "Trading_Volume", "Trading_money"]
    assert data.column_signature(cols) == "db24159024"


def _calendar_head(central, markets, asof, ref_asof=None, sha=None):
    """A pinned head whose calendar sidecar is a central-style v2 document written under ``central``."""
    raw = json.dumps({"schema": "twstock-updater.verified-market-calendar.v2", "verified_asof": asof,
                      "markets": markets}).encode()
    (central / "_calendar").mkdir(exist_ok=True)
    (central / "_calendar" / "cal.json").write_bytes(raw)
    ref = {"ref": "_calendar/cal.json", "sha256": sha or hashlib.sha256(raw).hexdigest(), "size": len(raw),
           "verified_asof": ref_asof or asof}
    return data.PinnedHead("a" * 64, {}, {}, ref, None, central)


def test_verified_calendar_uses_only_historical_sessions(tmp_path):
    # Since 2026-11-11 the central calendar appends the exchanges' announced future sessions to ``sessions``, with a
    # different length per market (TWSE about 63, TPEx only to the end of its published year).  Reading ``sessions``
    # made every run "calendar_unavailable" (TWSE != TPEx) and would count sessions that have not happened.
    import stock_h63_xs as cli
    import stock_h63_xs_ledger as ledger

    hist = ["2026-11-09", "2026-11-10", "2026-11-11", "2026-11-12"]
    markets = {"TWSE": {"calendar_verified": True, "historical_sessions": hist,
                        "future_sessions": ["2026-11-13", "2026-11-16", "2026-11-17"]},
               "TPEx": {"calendar_verified": True, "historical_sessions": hist, "future_sessions": ["2026-11-13"]}}
    for item in markets.values():
        item["sessions"] = item["historical_sessions"] + item["future_sessions"]
    cal = data.verified_calendar(_calendar_head(tmp_path, markets, "2026-11-12", ref_asof="2026-11-11"))
    assert cal["status"] == "ok" and cal["verified_asof"] == "2026-11-12"  # from the document, not the head's ref
    assert [str(d.date()) for d in cal["sessions"]] == hist
    panel = pd.DatetimeIndex(pd.to_datetime(hist))
    assert cli.check_calendar(panel, cal, panel[-1]) == {"status": "ok", "checked_to": "2026-11-12"}
    # settlement counts only sessions that already happened (no_label / freeze / prospective)
    assert ledger._sessions_after(cal["sessions"], "2026-11-10") == 2
    assert ledger.prospective_status("2026-11-12T15:30:00.000000Z", "2026-11-12", cal["sessions"]) == "unknown"
    # already-traded sessions that really differ between the markets still make the calendar unavailable (also when
    # only a middle day differs)
    for other in (hist[:-1], ["2026-11-06"] + hist[1:]):
        markets["TPEx"]["historical_sessions"] = other
        differ = data.verified_calendar(_calendar_head(tmp_path, markets, "2026-11-12"))
        assert differ["status"] == "calendar_unavailable" and differ["detail"] == "TWSE and TPEx historical sessions differ"
    markets["TPEx"]["historical_sessions"] = hist
    # no silent fallback to ``sessions`` (which includes the announced future) when historical_sessions is missing,
    # not a list or empty, in either market
    for market, bad in (("TWSE", None), ("TPEx", None), ("TWSE", "2026-11-12"), ("TPEx", [])):
        markets[market]["historical_sessions"] = bad
        missing = data.verified_calendar(_calendar_head(tmp_path, markets, "2026-11-12"))
        assert missing["status"] == "calendar_unavailable" and missing["detail"] == market + " historical_sessions missing"
        markets[market]["historical_sessions"] = hist


def test_verified_calendar_output_and_guards(tmp_path):
    # sessions come back sorted and normalised to dates whatever the order / time of day in the document
    raw_days = ["2026-11-10T09:00:00", "2026-11-09T13:30:00", "2026-11-12T00:00:00", "2026-11-11T10:00:00"]
    markets = {m: {"calendar_verified": True, "historical_sessions": raw_days, "future_sessions": [], "sessions": raw_days}
               for m in ("TWSE", "TPEx")}
    cal = data.verified_calendar(_calendar_head(tmp_path, markets, "2026-11-12"))
    assert cal["status"] == "ok"
    assert cal["sessions"].equals(pd.DatetimeIndex(pd.to_datetime(["2026-11-09", "2026-11-10", "2026-11-11", "2026-11-12"])))
    # the existing guards: content sha, per-market verification, no calendar on the head, malformed markets
    bad_sha = data.verified_calendar(_calendar_head(tmp_path, markets, "2026-11-12", sha="0" * 64))
    assert bad_sha["status"] == "calendar_unavailable" and bad_sha["detail"] == "calendar sha mismatch"
    markets["TWSE"]["calendar_verified"] = False
    unverified = data.verified_calendar(_calendar_head(tmp_path, markets, "2026-11-12"))
    assert unverified["status"] == "calendar_unavailable" and unverified["detail"] == "TWSE calendar not verified"
    no_calendar = data.verified_calendar(data.PinnedHead("a" * 64, {}, {}, None, None, tmp_path))
    assert no_calendar["status"] == "calendar_unavailable" and no_calendar["detail"].startswith("KeyError")
    malformed = data.verified_calendar(_calendar_head(tmp_path, {"TWSE": ["2026-11-12"], "TPEx": ["2026-11-12"]},
                                                      "2026-11-12"))
    assert malformed["status"] == "calendar_unavailable" and malformed["detail"].startswith("AttributeError")
