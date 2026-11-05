"""Offline tests of the H63 U7 forward record (stock_h63_xs_ledger; ADR-0009)."""
from __future__ import annotations

import datetime as dt
import json
import os
import sys
from pathlib import Path

import numpy as np
import pandas as pd
import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import stock_h63_xs_ledger as ledger  # noqa: E402
import stock_h63_xs_reader as reader  # noqa: E402
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


def _flaky(monkeypatch, name, target, owner=ledger):
    """<owner>.<name>(target, ...) fails once, like a file another process holds open; other paths pass through.
    read_index(paths) counts as a call on paths.index; owner=Path makes a Path method (target.read_bytes()) fail."""
    real = getattr(owner, name)

    def call(path, *args, **kwargs):
        if Path(path.index if isinstance(path, ledger.Paths) else path) == target:
            monkeypatch.setattr(owner, name, real)
            raise PermissionError(13, "file in use", str(target))
        return real(path, *args, **kwargs)
    monkeypatch.setattr(owner, name, call)


def test_repeat_publishes_the_latest_pointer_an_interrupted_run_missed(tmp_path, monkeypatch):
    # latest.json 被讀取端開著而無法取代時，區塊與 index 已寫入；相同輸入重跑要補發 latest（原 recorded_at，區塊不動）
    paths = ledger.Paths(tmp_path)
    _flaky(monkeypatch, "replace_json", paths.latest)
    with pytest.raises(OSError):
        write_fixture(tmp_path, signal_date="2026-10-16")
    with pytest.raises(reader.H63XsUnavailable, match="h63xs_no_latest"):
        reader.load_latest(tmp_path)
    blocks = {p: p.read_bytes() for p in paths.predictions.glob("2026/*")}
    again = write_fixture(tmp_path, signal_date="2026-10-16",
                          recorded_at=dt.datetime(2026, 10, 17, 1, 0, tzinfo=dt.timezone.utc))
    snap = reader.load_latest(tmp_path)
    assert snap.signal_date == "2026-10-16" and snap.latest["recorded_at"] == "2026-10-16T23:15:00.000000Z"
    assert {p: p.read_bytes() for p in paths.predictions.glob("2026/*")} == blocks
    assert again["block"]["status"] == "already_recorded" and again["block"]["completed"] == ["latest"]
    # 下一個決策日的 latest 也失敗：消費端停在舊快照，直到重跑補發；重跑較舊的區塊不得把 latest 拉回
    ledger.record_refusal(tmp_path, "h63xs_daily_gate_failed", "earlier attempt")
    _flaky(monkeypatch, "replace_json", paths.latest)
    later = dt.datetime(2026, 10, 19, 23, 15, tzinfo=dt.timezone.utc)
    with pytest.raises(OSError):
        write_fixture(tmp_path, signal_date="2026-10-19", recorded_at=later)
    assert reader.load_latest(tmp_path).signal_date == "2026-10-16" and paths.refusal.exists()
    again = write_fixture(tmp_path, signal_date="2026-10-19", recorded_at=later)
    assert reader.load_latest(tmp_path).signal_date == "2026-10-19" and not paths.refusal.exists()
    assert again["block"]["completed"] == ["latest"]
    assert write_fixture(tmp_path, signal_date="2026-10-16")["block"]["completed"] == []
    assert reader.load_latest(tmp_path).signal_date == "2026-10-19"
    lines, chain = ledger.read_index(paths)
    assert [line["decision_date"] for line in lines] == ["2026-10-16", "2026-10-19"] and chain["ok"]


def test_repeat_indexes_a_block_whose_index_append_failed(tmp_path, monkeypatch):
    # index 追加失敗只記 unindexed；相同輸入重跑要補上和正常寫入相同的 index 行（原 recorded_at、run_id）
    paths = ledger.Paths(tmp_path / "ledger")
    _flaky(monkeypatch, "append_line", paths.index)
    first = write_fixture(paths.root)
    assert first["block"]["indexed"] is False and ledger.read_index(paths)[0] == []
    header = {"decision_date": "2026-10-16", "run_id": "rerun", "key_material": {"decision_date": "2026-10-16",
              "fixture": True, "model_sha256": first["model"]["model_sha256"]}}
    again = ledger.record_block(paths.root, first["frame"], header,
                                recorded_at=dt.datetime(2026, 10, 18, tzinfo=dt.timezone.utc))
    clean = ledger.Paths(tmp_path / "clean")
    write_fixture(clean.root)
    assert ledger.read_index(paths) == ledger.read_index(clean)
    assert json.loads(paths.latest.read_text(encoding="utf-8")) == json.loads(clean.latest.read_text(encoding="utf-8"))
    assert again["status"] == "already_recorded" and again["indexed"] is True
    assert again["completed"] == ["index", "latest"]  # latest 的 indexed 一併更正


def test_repeat_indexes_onto_a_healthy_index(tmp_path, monkeypatch):
    # 健康的帳冊（鏈完好、index 以換行結尾）重跑照常補 index：latest 指向此區塊時連 latest 一併更正；latest 已是較新的
    # 區塊時只補 index、latest 不動，新行接在既有行之後
    paths = ledger.Paths(tmp_path)
    utc = dt.timezone.utc
    write_fixture(tmp_path, signal_date="2026-10-15", recorded_at=dt.datetime(2026, 10, 15, 23, 15, tzinfo=utc))
    _flaky(monkeypatch, "append_line", paths.index)
    assert write_fixture(tmp_path)["block"]["indexed"] is False  # 2026-10-16，latest 指向此區塊
    again = write_fixture(tmp_path)["block"]
    assert again["indexed"] is True and again["completed"] == ["index", "latest"]
    _flaky(monkeypatch, "append_line", paths.index)
    late = dt.datetime(2026, 10, 19, 23, 15, tzinfo=utc)
    assert write_fixture(tmp_path, signal_date="2026-10-19", recorded_at=late)["block"]["indexed"] is False
    write_fixture(tmp_path, signal_date="2026-10-20", recorded_at=late + dt.timedelta(days=1))  # 較新的區塊取得 latest
    older = write_fixture(tmp_path, signal_date="2026-10-19", recorded_at=late)["block"]
    assert older["indexed"] is True and older["completed"] == ["index"]
    lines, chain = ledger.read_index(paths)
    assert [line["decision_date"] for line in lines] == ["2026-10-15", "2026-10-16", "2026-10-20", "2026-10-19"]
    assert chain["ok"] and reader.load_latest(tmp_path).signal_date == "2026-10-20"


def test_repeat_never_publishes_a_changed_block(tmp_path, monkeypatch):
    # 補發前先驗證既有區塊：parquet 和 header 不符、或 header 和 index 記錄的不同，都不補發 latest
    damaged = ledger.Paths(tmp_path / "parquet")
    _flaky(monkeypatch, "replace_json", damaged.latest)
    with pytest.raises(OSError):
        write_fixture(damaged.root)
    parquet = next(damaged.predictions.glob("2026/*.parquet"))
    raw = bytearray(parquet.read_bytes())
    raw[-12] ^= 1
    os.chmod(parquet, 0o666)
    parquet.write_bytes(bytes(raw))
    assert write_fixture(damaged.root)["block"]["status"] == "conflicting_repeat"
    assert not damaged.latest.exists()
    edited = ledger.Paths(tmp_path / "header")
    _flaky(monkeypatch, "replace_json", edited.latest)
    with pytest.raises(OSError):
        write_fixture(edited.root)
    header = next(edited.predictions.glob("2026/*.json"))
    value = json.loads(header.read_text(encoding="utf-8"))
    value["recorded_at"] = "2026-10-15T23:15:00.000000Z"
    os.chmod(header, 0o666)
    header.write_text(json.dumps(value), encoding="utf-8")
    with pytest.raises(ledger.LedgerError, match="h63xs_block_sha_mismatch"):
        write_fixture(edited.root)
    assert not edited.latest.exists()


def _drop_event_header_shas(paths):
    """Rewrite the events without header_sha256, as written before the unindexed event recorded one."""
    events = [json.loads(x) for x in paths.events.read_text(encoding="utf-8").splitlines()]
    paths.events.write_text("".join(json.dumps({k: v for k, v in e.items() if k != "header_sha256"}) + "\n"
                                    for e in events), encoding="utf-8", newline="\n")


def test_repeat_never_indexes_a_changed_header(tmp_path, monkeypatch):
    # index 缺行時改以 latest.json 記錄的 header sha 驗證：header 被改過（recorded_at 提前）就不補進 index
    paths = ledger.Paths(tmp_path)
    _flaky(monkeypatch, "append_line", paths.index)
    assert write_fixture(tmp_path)["block"]["indexed"] is False
    published = paths.latest.read_bytes()
    header = next(paths.predictions.glob("2026/*.json"))
    value = json.loads(header.read_text(encoding="utf-8"))
    value["recorded_at"] = "2026-10-15T23:15:00.000000Z"
    os.chmod(header, 0o666)
    header.write_text(json.dumps(value), encoding="utf-8")
    with pytest.raises(ledger.LedgerError, match="h63xs_block_sha_mismatch"):
        write_fixture(tmp_path)
    _drop_event_header_shas(paths)  # 事件不含 header sha（本輪之前寫的）時只剩 latest 的記錄，也要擋下
    with pytest.raises(ledger.LedgerError, match="h63xs_block_sha_mismatch"):
        write_fixture(tmp_path)
    assert ledger.read_index(paths)[0] == [] and paths.latest.read_bytes() == published


def test_repeat_never_indexes_when_its_records_disagree(tmp_path, monkeypatch):
    # 背書記錄彼此矛盾時一律擋下：與被改的 header 相符的事件不能蓋過 latest 記錄的原 sha；header 未改、latest 相符，
    # 但另有一筆 sha 不同的 unindexed 事件時，也不補 index
    for case in ("edited_header", "stale_event"):
        paths = ledger.Paths(tmp_path / case)
        _flaky(monkeypatch, "append_line", paths.index)
        write_fixture(paths.root)  # latest 指向此區塊，unindexed 事件記原 header sha
        header = next(paths.predictions.glob("2026/*.json"))
        event = next(e for e in map(json.loads, paths.events.read_text(encoding="utf-8").splitlines())
                     if e["event"] == "unindexed")
        if case == "edited_header":
            value = dict(json.loads(header.read_text(encoding="utf-8")), recorded_at="2026-10-15T23:15:00.000000Z")
            os.chmod(header, 0o666)
            header.write_text(json.dumps(value), encoding="utf-8")
            event["header_sha256"] = ledger.sha256_file(header)
        else:
            event["header_sha256"] = "0" * 64
        with open(paths.events, "a", encoding="utf-8", newline="\n") as stream:
            stream.write(json.dumps(event) + "\n")
        with pytest.raises(ledger.LedgerError, match="h63xs_block_sha_mismatch"):
            write_fixture(paths.root)
        assert ledger.read_index(paths)[0] == [], case


def test_repeat_of_a_header_claiming_another_block_is_conflicting(tmp_path, monkeypatch):
    # header 的身分欄位（entry_key、decision_date）和重跑算出的不同就不是同一區塊：entry_key 連同 recorded_at 被改時，
    # index 與 latest 的比對都查不到它；index 已有此區塊的行、或追加失敗而缺行，重跑都不得新增 index 行
    for case, edit in (("indexed", {"entry_key": "f" * 64}), ("unindexed", {"entry_key": "f" * 64}),
                       ("dated", {"decision_date": "2026-10-15"})):
        paths = ledger.Paths(tmp_path / case)
        if case == "unindexed":
            _flaky(monkeypatch, "append_line", paths.index)
        write_fixture(paths.root)
        header = next(paths.predictions.glob("2026/*.json"))
        value = dict(json.loads(header.read_text(encoding="utf-8")), recorded_at="2026-10-15T23:15:00.000000Z", **edit)
        os.chmod(header, 0o666)
        header.write_text(json.dumps(value), encoding="utf-8")
        indexed = ledger.read_index(paths)
        assert write_fixture(paths.root)["block"]["status"] == "conflicting_repeat"
        assert ledger.read_index(paths) == indexed


def test_repeat_indexes_a_block_latest_moved_past_only_as_its_event_recorded(tmp_path, monkeypatch):
    # latest 已移到較新的區塊後，index 缺行的區塊只能以 unindexed 事件記錄的 header sha 背書：相符照常補 index；header
    # 被改過（recorded_at 提前）丟 h63xs_block_sha_mismatch；沒有記錄（本輪之前寫的事件不含 header sha）丟
    # h63xs_block_unverified，交給人工；兩者都不新增 index 行。事件檔暫時讀不到就丟出讀取錯誤，不當成沒有記錄
    paths = ledger.Paths(tmp_path)
    for day in ("2026-10-14", "2026-10-15", "2026-10-16"):
        _flaky(monkeypatch, "append_line", paths.index)
        assert write_fixture(tmp_path, signal_date=day)["block"]["indexed"] is False
    later = dt.datetime(2026, 10, 19, 23, 15, tzinfo=dt.timezone.utc)
    write_fixture(tmp_path, signal_date="2026-10-19", recorded_at=later)  # latest 移到較新的區塊
    header = next(paths.predictions.glob("2026/20261015__*.json"))
    value = json.loads(header.read_text(encoding="utf-8"))
    value["recorded_at"] = "2026-10-14T23:15:00.000000Z"
    os.chmod(header, 0o666)
    header.write_text(json.dumps(value), encoding="utf-8")
    with pytest.raises(ledger.LedgerError, match="h63xs_block_sha_mismatch"):
        write_fixture(tmp_path, signal_date="2026-10-15")
    _flaky(monkeypatch, "read_bytes", paths.events, owner=Path)
    with pytest.raises(PermissionError):
        write_fixture(tmp_path, signal_date="2026-10-16")
    assert write_fixture(tmp_path, signal_date="2026-10-16")["block"]["completed"] == ["index"]
    _drop_event_header_shas(paths)
    with pytest.raises(ledger.LedgerError, match="h63xs_block_unverified"):
        write_fixture(tmp_path, signal_date="2026-10-14")
    lines, chain = ledger.read_index(paths)
    assert [line["decision_date"] for line in lines] == ["2026-10-19", "2026-10-16"] and chain["ok"]
    assert reader.load_latest(tmp_path).signal_date == "2026-10-19"


def test_repeat_raises_when_the_index_cannot_be_read(tmp_path, monkeypatch):
    # 讀不到 index 不等於追加失敗：重跑直接丟出讀取錯誤，已發布的 latest 不改寫、不記 unindexed；之後重跑什麼都不動
    paths = ledger.Paths(tmp_path)
    write_fixture(tmp_path)
    published = paths.latest.read_bytes()
    _flaky(monkeypatch, "read_index", paths.index)
    with pytest.raises(PermissionError):
        write_fixture(tmp_path)
    events = [json.loads(x)["event"] for x in paths.events.read_text(encoding="utf-8").splitlines()]
    assert paths.latest.read_bytes() == published and "unindexed" not in events
    assert write_fixture(tmp_path)["block"]["completed"] == [] and paths.latest.read_bytes() == published


def test_repeat_raises_when_the_parquet_cannot_be_read(tmp_path, monkeypatch):
    # 暫時讀不到 parquet（被其他程序開著）不等於內容不同：重跑直接丟出讀取錯誤、不記 conflicting_repeat；之後重跑照常
    paths = ledger.Paths(tmp_path)
    write_fixture(tmp_path)
    _flaky(monkeypatch, "sha256_file", next(paths.predictions.glob("2026/*.parquet")))
    with pytest.raises(PermissionError):
        write_fixture(tmp_path)
    again = write_fixture(tmp_path)["block"]
    events = [json.loads(x)["event"] for x in paths.events.read_text(encoding="utf-8").splitlines()]
    assert again["status"] == "already_recorded" and again["completed"] == [] and events == ["already_recorded"]


@pytest.mark.parametrize("missing", ["json", "parquet"])
def test_repeat_with_a_block_file_missing_is_conflicting(tmp_path, missing):
    # 區塊檔不存在（例如 parquet 寫完、header 寫入前中斷）和內容不同一樣是 conflicting_repeat，不當成讀取錯誤丟出
    paths = ledger.Paths(tmp_path)
    write_fixture(tmp_path)
    target = next(paths.predictions.glob("2026/*." + missing))
    os.chmod(target, 0o666)
    target.unlink()
    assert write_fixture(tmp_path)["block"]["status"] == "conflicting_repeat"
    events = [json.loads(x)["event"] for x in paths.events.read_text(encoding="utf-8").splitlines()]
    assert events == ["conflicting_repeat"]


def test_repeat_never_appends_to_a_broken_index(tmp_path, monkeypatch):
    # index 缺此區塊的行時，鏈已損壞（此區塊的行無法解析）或檔尾不是完整的行，都不自動補 index（不修復鏈），交給人工
    own = ledger.Paths(tmp_path / "own")
    write_fixture(own.root)
    broken = own.index.read_bytes()[:-20] + b"\n"
    own.index.write_bytes(broken)
    with pytest.raises(ledger.LedgerError, match="h63xs_index_broken"):
        write_fixture(own.root)
    assert own.index.read_bytes() == broken
    tail = ledger.Paths(tmp_path / "tail")
    write_fixture(tail.root, signal_date="2026-10-15")
    tail.index.write_bytes(tail.index.read_bytes()[:-1])  # 前次追加停在換行之前：行完整但檔尾沒有換行
    _flaky(monkeypatch, "append_line", tail.index)
    assert write_fixture(tail.root)["block"]["indexed"] is False
    with pytest.raises(ledger.LedgerError, match="h63xs_index_broken"):
        write_fixture(tail.root)
    lines, chain = ledger.read_index(tail)
    assert [line["decision_date"] for line in lines] == ["2026-10-15"] and chain["ok"]  # 前一行沒被新行黏上而毀損


def _cut_append(monkeypatch, target, *, raises):
    """The next ledger.append_line(target, ...) writes only half of the line (no newline), then raises like an
    interrupted write, or with raises=False returns as if the whole line was written."""
    real = ledger.append_line

    def call(path, value):
        if Path(path) != target:
            return real(path, value)
        monkeypatch.setattr(ledger, "append_line", real)
        text = json.dumps(value, sort_keys=True)
        with open(path, "a", encoding="utf-8", newline="\n") as stream:
            stream.write(text[:len(text) // 2])
        if raises:
            raise OSError(5, "write interrupted", str(path))
        return text
    monkeypatch.setattr(ledger, "append_line", call)


def test_repeat_after_an_index_append_cut_mid_line(tmp_path, monkeypatch):
    # 前次追加寫到半行（檔尾無換行）：重跑不把新行接在半行後面（交給人工），latest.indexed 維持 false；
    # 重跑自己的追加若留下無法解析的行，重讀確認不到，也不回報 indexed
    cut = ledger.Paths(tmp_path / "cut")
    write_fixture(cut.root, signal_date="2026-10-15")
    _cut_append(monkeypatch, cut.index, raises=True)
    assert write_fixture(cut.root)["block"]["indexed"] is False
    with pytest.raises(ledger.LedgerError, match="h63xs_index_broken"):
        write_fixture(cut.root)
    assert json.loads(cut.latest.read_text(encoding="utf-8"))["indexed"] is False
    silent = ledger.Paths(tmp_path / "silent")
    _flaky(monkeypatch, "append_line", silent.index)
    write_fixture(silent.root)
    _cut_append(monkeypatch, silent.index, raises=False)
    again = write_fixture(silent.root)["block"]
    assert again["indexed"] is False and again["completed"] == []
    assert json.loads(silent.latest.read_text(encoding="utf-8"))["indexed"] is False


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


def test_review_scores_only_what_was_frozen(tmp_path):
    # 凍結後才成熟的列（停牌）、價格修訂後的 r63、已到期但離開 head 的股票，都不得改變已凍結區塊的計分
    sids = ["S%02d" % i for i in range(40)]
    rows = [(s, "TWSE", True, True, 0.62 if s == "S00" else 0.40 + 0.005 * i, "neutral", i + 1, -0.30, 0.30, "ok")
            for i, s in enumerate(sids)]
    frame = _frame("2026-10-16", rows)
    frame["score_centred"] = frame["p"] - 0.5  # 分數要有高低，IC 才有定義
    ledger.record_block(tmp_path, frame, {"decision_date": "2026-10-16", "gate": {"ok": True},
                                          "key_material": {"decision_date": "2026-10-16", "scored": True}},
                        recorded_at=dt.datetime(2026, 10, 16, 23, 15, tzinfo=dt.timezone.utc))
    decision = pd.Timestamp("2026-10-16")
    r63 = {s: -0.10 + 0.01 * i for i, s in enumerate(sids)}
    early = pd.bdate_range(decision, periods=70)  # 決策日後第 69 個交易日凍結；S00 停牌，尚無第 63 個自身價格列
    ledger.settle(tmp_path, _panel(early, decision, dict(r63, S00=None)), early, price_vintage="v1")
    ledger.review(tmp_path)
    daily_path = tmp_path / "state" / "forward_daily.csv"
    at_freeze = pd.read_csv(daily_path)
    assert at_freeze["rows"].tolist() == [39]
    later = pd.bdate_range(decision, periods=80)
    ledger.settle(tmp_path, _panel(later, decision, dict(r63, S00=0.50)), later, price_vintage="v2")
    settled = pd.read_parquet(tmp_path / "state" / "settlement" / "settled_rows.parquet").set_index("stock_id")
    assert settled.at["S00", "status"] == "matured"  # 每日重算已成熟，但凍結檔記為 pending
    ledger.review(tmp_path)
    pd.testing.assert_frame_equal(pd.read_csv(daily_path), at_freeze)
    revised = dict({s: -2 * v for s, v in r63.items() if s != "S05"}, S00=0.50)
    ledger.settle(tmp_path, _panel(later, decision, revised), later, price_vintage="v3")
    ledger.review(tmp_path)
    pd.testing.assert_frame_equal(pd.read_csv(daily_path), at_freeze)


def test_review_drops_a_frozen_block_voided_later(tmp_path):
    # 作廢是區塊層級的效力：凍結後日曆修正移除該決策日，已凍結的區塊也不再計分（凍結只決定標籤與報酬，不覆蓋作廢）
    sids = ["S%02d" % i for i in range(40)]
    frame = _frame("2026-10-16", [(s, "TWSE", True, True, 0.40 + 0.005 * i, "neutral", i + 1, -0.30, 0.30, "ok")
                                  for i, s in enumerate(sids)])
    frame["score_centred"] = frame["p"] - 0.5
    ledger.record_block(tmp_path, frame, {"decision_date": "2026-10-16", "gate": {"ok": True},
                                          "key_material": {"decision_date": "2026-10-16", "scored": True}},
                        recorded_at=dt.datetime(2026, 10, 16, 23, 15, tzinfo=dt.timezone.utc))
    decision = pd.Timestamp("2026-10-16")
    sessions = pd.bdate_range(decision, periods=80)
    panel = _panel(sessions, decision, {s: -0.10 + 0.01 * i for i, s in enumerate(sids)})
    ledger.settle(tmp_path, panel, sessions, price_vintage="v1")
    assert ledger.review(tmp_path)["daily_rows"] == 1
    ledger.settle(tmp_path, panel, sessions[sessions != decision], price_vintage="v2")
    assert ledger.review(tmp_path)["daily_rows"] == 0


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
