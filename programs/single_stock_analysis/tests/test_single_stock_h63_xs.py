"""H63 單股疊加層（ADR-0009）：U7 三個月相對強弱取代 expanded_linear 的 H63 顯示；H1 與機器欄位不變。"""
from __future__ import annotations

import copy
import json
import os
import re
from pathlib import Path

import pytest

import single_stock_h63_xs as h63xs
import single_stock_preopen as preopen
import stock_candidate_report as report
import tw_single_stock_c0 as single
from stock_h63_xs_fixture import write_fixture

from test_single_stock_preopen import _sign, _stub_rendering  # noqa: E402
from test_stock_candidate_report import formal_payload, payload  # noqa: E402,F401

STOCK_2330 = {"stock_id": "2330", "market": "TWSE"}
STOCK_REMINDER = "本股提醒：本股目前處置交易，須留意成交限制。"
ISO_DATE = re.compile(r"(?<![0-9A-Za-z_])\d{4}-\d{2}-\d{2}(?![0-9A-Za-z_])")


@pytest.fixture
def xs_root(tmp_path, monkeypatch):
    root = tmp_path / "h63_xs"
    monkeypatch.setenv("STOCK_H63_XS_ROOT", str(root))
    monkeypatch.delenv(h63xs.SOURCE_ENV, raising=False)
    return root


def _h63(formal_payload):
    return _sign(dict(copy.deepcopy(formal_payload), horizon_sessions=63))


def _text(sections):
    return "\n".join(report._visible_strings(sections))


def test_source_precedence_cli_env_config_default(xs_root, monkeypatch):
    assert h63xs.resolve_source() == "auto"
    (xs_root / "config").mkdir(parents=True)
    (xs_root / "config" / "display.json").write_text('{"mode": "legacy"}', encoding="utf-8")
    assert h63xs.resolve_source() == "legacy"
    monkeypatch.setenv(h63xs.SOURCE_ENV, "off")
    assert h63xs.resolve_source() == "off"
    assert h63xs.resolve_source("xs") == "xs"
    with pytest.raises(ValueError, match="h63_source_invalid:bogus"):
        h63xs.resolve_source("bogus")


def test_used_block_carries_probability_band_factors_identity(xs_root):
    write_fixture(xs_root, signal_date="2026-10-16")
    block = h63xs.build_h63_block(STOCK_2330, "2026-10-16")
    assert block["status"] == "used" and block["display_source"] == "u7_xs"
    assert block["p"] == pytest.approx(0.584) and block["relative_strength_zh"] == "相對偏強"
    assert block["rank_eligible"] == 312 and block["eligible_count"] == 3
    assert block["band"]["q10"] == pytest.approx(-0.211) and block["band"]["q90"] == pytest.approx(0.332)
    assert len(block["factors"]) == 7 and all(len(block["identity"][k]) == 64 for k in ("entry_key", "row_sha256"))
    assert json.loads(json.dumps(block)) == block  # JSON native


def test_stale_snapshot_is_noted_and_too_old_falls_back(xs_root):
    write_fixture(xs_root, signal_date="2026-10-16")
    block = h63xs.build_h63_block(STOCK_2330, "2026-10-21")
    assert block["status"] == "used" and block["snapshot_lag_weekdays"] == 3
    assert any("沿用較舊快照：訊號日 20261016" in note for note in block["notes_zh"])
    old = h63xs.build_h63_block(STOCK_2330, "2026-10-27")
    assert old["status"] == "fallback" and old["reason"] == "h63xs_stale" and "p" not in old


@pytest.mark.parametrize("stock,reason", [({"stock_id": "9999", "market": "TWSE"}, "h63xs_stock_not_in_snapshot"),
                                          ({"stock_id": "2330", "market": "TPEX"}, "h63xs_market_mismatch")])
def test_row_problems_fall_back_without_numbers(xs_root, stock, reason):
    write_fixture(xs_root, signal_date="2026-10-16")
    block = h63xs.build_h63_block(stock, "2026-10-16")
    assert block["status"] == "fallback" and block["reason"] == reason
    assert not any(key in block for key in ("p", "band", "factors", "rank_eligible"))


def test_off_never_reads_and_xs_requires_the_snapshot(xs_root):
    block = h63xs.build_h63_block(STOCK_2330, "2026-10-16", source="off")
    assert block["status"] == "disabled" and "已暫停顯示" in block["notice"]
    with pytest.raises(h63xs.H63XsRequiredError):
        h63xs.build_h63_block(STOCK_2330, "2026-10-16", source="xs")


def test_selected_h63_report_shows_relative_strength_only(formal_payload, xs_root, tmp_path, monkeypatch):
    import stock_daily_selected_deployment as selected
    _stub_rendering(monkeypatch)
    write_fixture(xs_root, signal_date="2026-10-16")
    item = _h63(formal_payload)
    original = copy.deepcopy(item)
    with h63xs.selected_h63_overlay("auto"):
        result = report.write_candidate_report(item, tmp_path / "reports")
    assert item["h63_xs"]["status"] == "used"
    # Only h63_xs is added; the stable fields reproduced by validate_selected_report are unchanged.
    for key in ("prediction", "metrics", "model_binding", "limitations", "asof", "target_session_date"):
        assert item[key] == original[key]
    unsigned = {k: v for k, v in item.items() if k != "runtime_payload_sha256"}
    assert item["runtime_payload_sha256"] == selected._digest(unsigned)
    assert result["validation"]["valid"] is True
    sidecar = json.loads(Path(result["json_path"]).read_text(encoding="utf-8"))
    assert sidecar["payload"]["h63_xs"] == item["h63_xs"]
    assert sidecar["machine_evidence"]["h63_disposition"]["display_source"] == "u7_xs"
    text = _text(sidecar["sections"])
    for needle in ("跑贏同期多數股票的機率", "58.4%", "相對偏強", "第 312 名／共 3 檔", "-21.1% 至 +33.2%",
                   "H63 必要揭露", "六、H63 模型驗證與限制", "20261016", "ADR-0009"):
        assert needle in text, needle
    for needle in ("上漲機率：", "預估報酬", "機率尚未校準", "選中模型最終測試"):
        assert needle not in text, needle
    assert "上漲機率" not in text.replace("不是上漲機率", "")
    assert not ISO_DATE.search(text), ISO_DATE.search(text)
    charts = [s["chart"] for s in sidecar["sections"] if s.get("chart")]
    assert all(not ISO_DATE.search(json.dumps(c.get("dates") or [])) for c in charts)
    assert report.validate_candidate_report(item, Path(result["pdf_path"]))["valid"] is True
    assert report._H63_XS_ATTACHER.get() is None


def test_fallback_report_shows_reason_and_no_h63_number(formal_payload, xs_root, tmp_path, monkeypatch):
    _stub_rendering(monkeypatch)
    item = _h63(formal_payload)
    with h63xs.selected_h63_overlay("auto"):
        result = report.write_candidate_report(item, tmp_path / "reports")
    assert item["h63_xs"]["status"] == "fallback" and item["h63_xs"]["reason"] == "h63xs_no_latest"
    sections = json.loads(Path(result["json_path"]).read_text(encoding="utf-8"))["sections"]
    assert sections[0]["paragraphs"][0].startswith("【注意】三個月相對強弱暫不可用（尚未產生三個月相對強弱快照）")
    text = _text(sections)
    assert "58.00%" not in text and "上漲機率：" not in text and "跑贏同期多數股票的機率" not in text


@pytest.mark.parametrize("asof,source,status", [("2026-10-16", "auto", "used"), ("2026-10-30", "auto", "fallback"),
                                                ("2026-10-16", "off", "disabled")])
def test_u7_report_lists_the_stock_reminder_exactly_once(formal_payload, xs_root, monkeypatch, asof, source, status):
    """處置交易、停牌、下市、減資等本股提醒在 U7 檢視（含不顯示數字時）也列一次；選定模型的校準提醒不帶入。"""
    monkeypatch.setattr(report, "_converter", lambda: lambda text: text)
    write_fixture(xs_root, signal_date="2026-10-16")
    item = dict(copy.deepcopy(formal_payload), horizon_sessions=63, asof=asof)
    item["limitations"].append("本股目前處置交易，須留意成交限制。")
    item["h63_xs"] = h63xs.build_h63_block(STOCK_2330, asof, source=source)
    assert item["h63_xs"]["status"] == status
    sections = report.build_report_sections(item)
    text = _text(sections)
    assert text.count(STOCK_REMINDER) == 1 and STOCK_REMINDER in sections[0]["paragraphs"]
    for omitted in ("不能直接視為實際命中率", "機率尚未校準", "估計限制"):
        assert omitted not in text, omitted
    basics = dict(next(table for table in sections[0]["tables"] if table["title"] == "本股基本資料")["rows"])
    if status == "used":  # 新快照：日期只留在「訊號日至目標」
        assert "資料日" not in basics
    else:  # 不顯示 U7 數字時，資料日是【注意】旁唯一的日期，必須保留
        assert basics["資料日"] == asof.replace("-", "")


@pytest.mark.parametrize("disclosed", [True, False], ids=["disclosure_has_target", "disclosure_without_target"])
def test_u7_target_definition_is_written_once(formal_payload, xs_root, monkeypatch, disclosed):
    """預測目標（不是上漲機率，也不是可成交報酬）：第六章必要揭露已有時第一章不重複；揭露沒有時第一章保留。"""
    monkeypatch.setattr(report, "_converter", lambda: lambda text: text)
    write_fixture(xs_root, signal_date="2026-10-16")
    item = dict(copy.deepcopy(formal_payload), horizon_sessions=63)
    item["h63_xs"] = h63xs.build_h63_block(STOCK_2330, "2026-10-16")
    if not disclosed:
        item["h63_xs"]["disclosure_rows"] = [row for row in item["h63_xs"]["disclosure_rows"] if row[0] != "預測目標"]
    sections = report.build_report_sections(item)
    assert _text(sections).count("不是上漲機率，也不是可成交報酬") == 1
    assert any(text.startswith("預測目標：") for text in sections[0]["paragraphs"]) is not disclosed
    disclosure = dict(next(table for table in sections[-1]["tables"] if table["title"] == "H63 必要揭露")["rows"])
    assert ("預測目標" in disclosure) is disclosed


@pytest.mark.parametrize("asof", ["2026-10-16", "2026-10-21"], ids=["fresh", "older_snapshot"])
def test_u7_summary_lists_the_signal_day_once_when_it_is_the_data_day(formal_payload, xs_root, monkeypatch, asof):
    """訊號日＝資料日時，這一天只在「訊號日至目標」寫一次；沿用較舊快照時訊號日、資料日與沿用註記都保留。"""
    monkeypatch.setattr(report, "_converter", lambda: lambda text: text)
    write_fixture(xs_root, signal_date="2026-10-16")
    item = dict(copy.deepcopy(formal_payload), horizon_sessions=63, asof=asof)
    item["h63_xs"] = h63xs.build_h63_block(STOCK_2330, asof)
    summary = report.build_report_sections(item)[0]
    main = dict(summary["tables"][0]["rows"])
    basics = dict(next(table for table in summary["tables"] if table["title"] == "本股基本資料")["rows"])
    assert main["訊號日至目標"].startswith("20261016 收盤至之後第 63 個交易日收盤")
    assert basics["股票"].startswith("2330 ") and basics["市場與產業"].startswith("上市市場")
    if asof == "2026-10-16":
        assert "資料日" not in basics and "訊號日" not in main["資料"] and main["資料"].startswith("中央資料 cccccccc；")
        assert _text([summary]).count("20261016") == 1
    else:
        assert basics["資料日"] == "20261021" and "中央資料 cccccccc（訊號日 20261016）" in main["資料"]
        assert "沿用較舊快照：訊號日 20261016；本次分析資料日 20261021（落後 3 個平日）" in main["註記"]


def test_legacy_installs_no_hook_and_h1_is_untouched(formal_payload, xs_root, tmp_path, monkeypatch):
    _stub_rendering(monkeypatch)
    write_fixture(xs_root, signal_date="2026-10-16")
    h1 = _sign(copy.deepcopy(formal_payload))
    h63 = _h63(formal_payload)
    original_h1, original_h63 = copy.deepcopy(h1), copy.deepcopy(h63)
    with h63xs.selected_h63_overlay("legacy"):
        report.write_candidate_report(h63, tmp_path / "reports")
    assert h63 == original_h63
    with h63xs.selected_h63_overlay("auto"):
        report.write_candidate_report(h1, tmp_path / "reports")
    assert h1 == original_h1 and "h63_xs" not in h1


def test_h1_report_bytes_unchanged_without_or_with_the_h63_overlay(formal_payload, xs_root):
    # H1 sections, digest and machine evidence are identical whether or not an H63 overlay is active.
    write_fixture(xs_root, signal_date="2026-10-16")
    h1 = _sign(copy.deepcopy(formal_payload))
    plain = report.build_report_sections(copy.deepcopy(h1))
    with h63xs.selected_h63_overlay("auto"):
        overlaid = report.build_report_sections(copy.deepcopy(h1))
    assert plain == overlaid and report._digest(plain) == report._digest(overlaid)
    assert "h63_disposition" not in report._report_machine_evidence(h1)


def test_block_on_wrong_horizon_or_malformed_is_rejected(formal_payload, xs_root):
    write_fixture(xs_root, signal_date="2026-10-16")
    block = h63xs.build_h63_block(STOCK_2330, "2026-10-16")
    item = copy.deepcopy(formal_payload)
    item["h63_xs"] = block  # H1 payload
    with pytest.raises(report.CandidateReportError) as caught:
        report.build_report_sections(item)
    assert caught.value.code == "h63_xs_block_invalid"
    item = dict(copy.deepcopy(formal_payload), horizon_sessions=63)
    bad = copy.deepcopy(block)
    bad["band"]["q10"] = 0.05  # band must contain zero
    item["h63_xs"] = bad
    with pytest.raises(report.CandidateReportError):
        report.build_report_sections(item)
    fallback = {"schema": h63xs.BLOCK_SCHEMA, "status": "fallback", "stock_id": "2330", "notice": "x", "p": 0.5}
    item["h63_xs"] = fallback  # a fallback block may never carry numbers
    with pytest.raises(report.CandidateReportError):
        report.build_report_sections(item)


def test_display_line_has_no_bot_path_markers(xs_root):
    write_fixture(xs_root, signal_date="2026-10-16")
    for block in (h63xs.build_h63_block(STOCK_2330, "2026-10-16"),
                  h63xs.build_h63_block({"stock_id": "9999", "market": "TWSE"}, "2026-10-16")):
        for line in h63xs.display_lines(block):
            assert "｜" not in line and not line.startswith(("PDF：", "JSON："))


def test_main_enters_the_h63_overlay_only_for_h63(monkeypatch, tmp_path, xs_root):
    binding = {"schema": "single-stock-selected-runtime-binding-v1", "deployment_binding": {"activation_id": "fixture"}}
    monkeypatch.setattr(single.selected, "analysis_binding", lambda: binding)
    monkeypatch.setenv(preopen.SOURCE_ENV, "auto")
    seen = []

    def generate(query, **kwargs):
        seen.append((kwargs["horizons"], report._H63_XS_ATTACHER.get() is not None,
                     report._PREOPEN_H1_ATTACHER.get() is not None))

    monkeypatch.setattr(single.selected, "generate_selected_reports", generate)
    base = ["--stock", "2330", "--output-dir", str(tmp_path)]
    assert single.main(1, base) == 0
    assert single.main(63, base) == 0
    assert single.main(63, [*base, "--both-horizons"]) == 0
    assert single.main(63, [*base, "--h63-source", "legacy"]) == 0
    assert seen == [((1,), False, True), ((63,), True, False), ((1, 63), True, True), ((63,), False, False)]
    assert report._H63_XS_ATTACHER.get() is None


@pytest.mark.skipif(os.name != "nt", reason="Windows 字型與轉繁 API 預檢")
def test_real_fonts_cover_every_h63_string(formal_payload, xs_root):
    pytest.importorskip("reportlab")
    write_fixture(xs_root, signal_date="2026-10-16")
    for asof in ("2026-10-16", "2026-10-30"):  # used, then stale -> fallback notice
        item = dict(copy.deepcopy(formal_payload), horizon_sessions=63)
        item["h63_xs"] = h63xs.build_h63_block(STOCK_2330, asof)
        report._load_fonts().check(report._visible_strings(report.build_report_sections(item)))
