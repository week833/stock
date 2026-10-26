"""Focused preflight tests. PDF creation/rendering belongs to centralized QA."""

from __future__ import annotations

import copy
import json
import os
import subprocess
import sys
import textwrap
from datetime import date, timedelta
from pathlib import Path
from types import SimpleNamespace

import pytest

SRC = Path(__file__).resolve().parents[1] / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))
import stock_candidate_report as report
import report_comparison_bridge as comparison_bridge


_CACHE_CHILD = r"""
import copy, hashlib, json, os, sys, types
from pathlib import Path
request = json.loads(Path(sys.argv[1]).read_text(encoding="utf-8"))
sys.path.insert(0, request["bridge_dir"])
import report_comparison_bridge as bridge
bridge._comparison_temp_root = lambda: Path(request["bridge_dir"]) / "consumer_temp"
engine = types.ModuleType("stock_report_comparison")
engine.__file__ = request["engine_file"]
engine.SCHEMA = request.get("engine_schema", "stock-report-comparison-v1")
engine.CENTRAL_ROOT = Path(request["central_root"])
calls = []
def build(stocks, signal, *, head_sha=None, **kwargs):
    calls.append((stocks, signal, head_sha))
    return {stock: {"schema": engine.SCHEMA, "stock_id": stock, "signal_date": bridge._signal_date(signal),
        "source_head_sha256": head_sha or "c" * 64, "source_status": "PARTIAL",
        "metrics": [{"項目": "月營收", "資料期間": "202609", "本期值": None, "比較": "部分資料",
                     "解讀": "缺值保留，未補零。", "資料日期": "20261009"}],
        "notes": ["PARTIAL；舊資料日期20261009，缺值原樣保留。"]} for stock in stocks}
engine.build_comparisons = build
sys.modules["stock_report_comparison"] = engine
def guard(event, args):
    if event in {"socket.connect", "subprocess.Popen", "os.system"}:
        raise AssertionError("fixture must not use network or grandchildren")
    if event == "open" and isinstance(args[0], (str, bytes, os.PathLike)):
        path = os.path.normcase(os.path.abspath(os.fsdecode(args[0])))
        if path.startswith(os.path.normcase(r"D:\TWStockData\shared_market_data") + os.sep):
            raise AssertionError("fixture must not read real Central")
sys.addaudithook(guard)
if request.get("read_error"):
    def denied(*args, **kwargs):
        raise PermissionError("fixture cache read denied")
    bridge._read_cache = denied
if request.get("write_error"):
    original_open = Path.open
    def failing_open(path, mode="r", *args, **kwargs):
        if Path(path).name == bridge.CACHE_TEMP_NAME and mode == "xb":
            raise PermissionError("fixture cache write denied")
        return original_open(path, mode, *args, **kwargs)
    Path.open = failing_open
payload = request["payload"]
before = copy.deepcopy(payload)
block = bridge.attach_report_comparison(payload)
assert payload["prediction"] == before["prediction"]
assert payload["model_inputs"] == before["model_inputs"]
unsigned = {key:value for key,value in payload.items() if key != "runtime_payload_sha256"}
assert payload["runtime_payload_sha256"] == hashlib.sha256(bridge._canonical(unsigned)).hexdigest()
Path(request["result"]).write_text(json.dumps({"pid":os.getpid(), "calls":len(calls),
    "payload":payload, "block":block}, ensure_ascii=False, allow_nan=False), encoding="utf-8", newline="\n")
"""


def _handoff_payload(horizon, *, kind="selected", head="b" * 64, stock="2330", signal="2026-10-16"):
    payload = {"stock": {"stock_id": stock}, "horizon_sessions": horizon,
               "prediction": {"prob_up": .61 if horizon == 63 else .47,
                              "expected_return": .032 if horizon == 63 else -.004},
               "model_inputs": {"unchanged_feature": 1.25, "missing_feature": None},
               "runtime_payload_sha256": "recomputed-by-existing-seal"}
    if kind == "c0":
        payload.update(analysis_asof=signal, published_snapshot={"sources": [{"manifest_sha256": head}]})
    else:
        payload.update(asof=signal, snapshot_binding={"head_sha256": head})
    return payload


def _run_handoff_child(tmp_path, request, environment, label):
    request_path = tmp_path / (label + "_request.json")
    result_path = tmp_path / (label + "_result.json")
    request = {**request, "result": str(result_path)}
    request_path.write_text(json.dumps(request, ensure_ascii=False), encoding="utf-8", newline="\n")
    script = tmp_path / "comparison_child.py"
    if not script.exists():
        script.write_text(textwrap.dedent(_CACHE_CHILD), encoding="utf-8", newline="\n")
    child_env = {key: value for key, value in os.environ.items()
                 if not key.casefold().startswith(comparison_bridge.CACHE_ENV_PREFIX.casefold())}
    child_env.update(environment)
    child_env.update(PYTHONDONTWRITEBYTECODE="1", PYTHONUTF8="1", PYTHONIOENCODING="utf-8")
    result = subprocess.run([sys.executable, "-B", str(script), str(request_path)],
                            env=child_env, capture_output=True, text=True, encoding="utf-8", timeout=30)
    assert result.returncode == 0, result.stdout + result.stderr
    return json.loads(result_path.read_text(encoding="utf-8"))


@pytest.mark.parametrize("case,kind", [
    ("hit", "selected"), ("hit", "c0"), ("date_spelling", "selected"),
    ("missing", "selected"), ("truncated", "selected"), ("bytes_tamper", "selected"),
    ("block_tamper", "selected"), ("context", "selected"), ("job", "selected"),
    ("head", "selected"), ("stock", "selected"), ("asof", "selected"),
    ("schema", "selected"), ("root", "selected"), ("engine", "selected"), ("bridge", "selected"),
    ("no_expected_sha", "selected"), ("no_head", "selected"),
    ("read_error", "selected"), ("write_error", "selected"),
])
def test_job_comparison_handoff_uses_two_fresh_children_and_preserves_predictions(tmp_path, monkeypatch, case, kind):
    """No model, real Central, formal outputs, Telegram or service is used."""
    source = Path(comparison_bridge.__file__)
    private_bridge = tmp_path / source.name
    private_bridge.write_bytes(source.read_bytes())
    engine_file = tmp_path / "fixture_engine.py"
    engine_file.write_text("# fixture engine identity\n", encoding="utf-8", newline="\n")
    engine = SimpleNamespace(__file__=str(engine_file), CENTRAL_ROOT=tmp_path / "fixture_central",
                             SCHEMA="stock-report-comparison-v1")
    engine_sha = comparison_bridge.hashlib.sha256(engine_file.read_bytes()).hexdigest()
    monkeypatch.setattr(comparison_bridge, "_engine", lambda: engine)
    monkeypatch.setitem(comparison_bridge._ENGINE_SOURCE_BINDINGS, id(engine), (engine, engine_sha))
    nonce = "a" * 32
    cache_dir = tmp_path / (".report_comparison_" + nonce)
    cache_dir.mkdir()
    prefix = comparison_bridge.CACHE_ENV_PREFIX
    environment = {prefix + "DIR": str(cache_dir), prefix + "NONCE": nonce, prefix + "PHASE": "write"}
    base = {"bridge_dir": str(tmp_path), "engine_file": str(engine_file), "central_root": str(engine.CENTRAL_ROOT)}
    first_payload = _handoff_payload(63, kind=kind)
    first = _run_handoff_child(tmp_path, {**base, "payload": first_payload, "write_error": case == "write_error"},
                               environment, "first")
    assert first["calls"] == 1
    expected = comparison_bridge.cache_handoff_digest(cache_dir, nonce, first["payload"])
    if case == "write_error":
        assert expected is None
    else:
        assert expected
    environment[prefix + "PHASE"] = "read"
    if expected:
        environment[prefix + "READ_SHA256"] = expected
    second_payload = _handoff_payload(1, kind=kind)
    second_request = {**base, "payload": second_payload}
    cache_file = cache_dir / comparison_bridge.CACHE_FILE_NAME
    if case == "missing":
        cache_file.unlink()
    elif case == "truncated":
        cache_file.write_bytes(b'{"schema":')
    elif case == "bytes_tamper":
        cache_file.write_bytes(cache_file.read_bytes() + b" ")
    elif case in {"block_tamper", "context"}:
        envelope = json.loads(cache_file.read_text(encoding="utf-8"))
        if case == "block_tamper":
            envelope["block"]["notes"].append("unauthorized fixture mutation")
        else:
            envelope["identity"]["context"] = "other-consumer"
        cache_file.write_bytes(comparison_bridge._canonical(envelope))
        environment[prefix + "READ_SHA256"] = comparison_bridge.hashlib.sha256(cache_file.read_bytes()).hexdigest()
    elif case == "job":
        nonce = "d" * 32
        other = tmp_path / (".report_comparison_" + nonce)
        other.mkdir()
        (other / comparison_bridge.CACHE_FILE_NAME).write_bytes(cache_file.read_bytes())
        environment.update({prefix + "DIR": str(other), prefix + "NONCE": nonce})
    elif case == "head":
        second_payload = _handoff_payload(1, kind=kind, head="c" * 64)
    elif case == "stock":
        second_payload = _handoff_payload(1, kind=kind, stock="2317")
    elif case == "asof":
        second_payload = _handoff_payload(1, kind=kind, signal="2026-10-15")
    elif case == "date_spelling":
        second_payload = _handoff_payload(1, kind=kind, signal="20261016")
    elif case == "schema":
        second_request["engine_schema"] = "fixture-comparison-schema-v2"
    elif case == "root":
        second_request["central_root"] = str(tmp_path / "different_central")
    elif case == "engine":
        engine_file.write_text("# changed fixture engine identity\n", encoding="utf-8", newline="\n")
    elif case == "bridge":
        private_bridge.write_bytes(private_bridge.read_bytes() + b"\n# changed fixture bridge identity\n")
    elif case == "no_expected_sha":
        environment.pop(prefix + "READ_SHA256", None)
    elif case == "no_head":
        second_payload = _handoff_payload(1, kind=kind, head=None)
    elif case == "read_error":
        second_request["read_error"] = True
    second_request["payload"] = second_payload
    second = _run_handoff_child(tmp_path, second_request, environment, "second")
    assert first["pid"] != second["pid"]
    assert second["calls"] == (0 if case in {"hit", "date_spelling"} else 1)
    assert first["payload"]["prediction"] == first_payload["prediction"]
    assert second["payload"]["prediction"] == second_payload["prediction"]
    assert first["payload"]["model_inputs"] == second["payload"]["model_inputs"] == first_payload["model_inputs"]
    assert first["block"]["source_status"] == second["block"]["source_status"] == "PARTIAL"
    assert first["block"]["metrics"][0]["本期值"] is second["block"]["metrics"][0]["本期值"] is None
    assert first["block"]["notes"] == second["block"]["notes"]
    if case in {"hit", "date_spelling"}:
        assert first["block"] == second["block"]
    else:
        assert second["block"]["stock_id"] == second_payload["stock"]["stock_id"]
        assert second["block"]["signal_date"] == comparison_bridge._signal_date(
            second_payload.get("asof") or second_payload["analysis_asof"])


def test_comparison_sections_preserve_values_without_per_row_caution_column(payload, content_converter):
    original = copy.deepcopy(payload["prediction"])
    payload["report_comparison"] = {
        "schema": "stock-report-comparison-v1", "stock_id": "2330", "signal_date": "20261016",
        "source_head_sha256": "b" * 64,
        "metrics": [{"項目": "月營收", "資料期間": "202609", "本期值": "120（單位未核實）",
                     "比較": "去年同月100；變化+20.00%", "解讀": "同比增加不代表獲利改善。", "資料日期": "20261009"}],
        "notes": ["實際比較202309～202609；不宣稱五年齊全。"]}
    text = section_text(report.build_report_sections(payload), "fundamentals")
    assert "去年同月100" in text and "20261009" in text and "不宣稱五年齊全" in text
    sections = report.build_report_sections(payload)
    tables = next(s for s in sections if s["id"] == "fundamentals")["tables"]
    comparison = next(table for table in tables if "比較" in table["columns"])
    assert comparison["columns"] == ["項目", "資料期間", "本期值", "比較", "資料日期"]
    assert "同比增加不代表獲利改善" not in section_text(sections)
    assert payload["report_comparison"]["metrics"][0]["解讀"] == "同比增加不代表獲利改善。"
    assert payload["prediction"] == original


def test_all_42_comparison_metrics_keep_their_numbers_in_the_correct_chapter(payload, content_converter):
    financial = ["Revenue", "GrossProfit", "OperatingIncome", "NetIncome", "EPS", "TotalAssets",
                 "TotalLiabilities", "Equity", "CashAndCashEquivalents", "Inventories",
                 "AccountsReceivableNet", "CapitalStock", "RetainedEarnings",
                 "CashFlowsFromOperatingActivities", "CashFlowsFromInvestingActivities"]
    groups = [
        ("fundamentals", "營收比較", "finmind_month_revenue",
         ["revenue_monthly", "revenue_ytd", "revenue_3m", "revenue_12m", "revenue_same_month"]),
        ("fundamentals", "財務比較", "finmind_financial_statements",
         ["financial_" + name + "_quarter" for name in financial]),
        ("fundamentals", "估值比較", "finmind_per", ["valuation_per", "valuation_pbr"]),
        ("flows", "法人買賣比較", "finmind_institutional",
         ["institutional_" + name + "_" + str(window) for name in ("foreign", "trust", "dealer") for window in (5, 20)]),
        ("flows", "融資融券比較", "finmind_margin", ["margin_purchase_balance", "short_sale_balance"]),
        ("flows", "集保持股分級比較", "tdcc_share_distribution_raw", ["tdcc_" + str(i) for i in range(12)]),
    ]
    expected = []
    metrics = []
    for chapter, title, route, keys in groups:
        for key in keys:
            index = len(metrics)
            current, prior, delta = f"{1000 + index}.25", f"{2000 + index}.5", f"{3000 + index}.75"
            metrics.append({"id": key, "source_route": route, "current_value": float(current),
                "項目": "比較科目" + str(index), "資料期間": "20260630", "本期值": current,
                "比較": "基期 " + prior + "；差額 " + delta, "資料日期": "20261016",
                "解讀": "期末日不等於公告日；累計不當成單季，當期比較不宣稱回測時已公開。"})
            expected.append((chapter, title, current, prior, delta))
    assert len(metrics) == 42
    payload["report_comparison"]["metrics"] = metrics
    original = copy.deepcopy(payload)
    sections = report.build_report_sections(payload)
    all_cells = [str(cell) for section in sections for table in section["tables"]
                 for row in table["rows"] for cell in row]
    for chapter, title, current, prior, delta in expected:
        table = next(table for section in sections if section["id"] == chapter
                     for table in section["tables"] if table["title"] == title)
        assert sum(current == str(cell) for cell in all_cells) == 1
        assert any(current == row[2] and prior in row[3] and delta in row[3] for row in table["rows"])
        assert all("解讀" not in table["columns"] for section in sections for table in section["tables"])
    assert "期末日不等於公告日；累計不當成單季，當期比較不宣稱回測時已公開。" not in section_text(sections)
    assert payload == original


def test_comparison_replaces_only_matching_latest_indicators(payload, content_converter):
    payload["analysis_context"]["sources"] = [
        {"route_id": "finmind_per", "records": [{"date": "2026-10-16", "PER": 20.5, "PBR": 4.1, "dividend_yield": 1.8}]},
        {"route_id": "finmind_financial_statements", "records": [
            {"date": "2026-06-30", "type": "Revenue", "value": 123456},
            {"date": "2026-06-30", "type": "OperatingIncome", "value": 56789}]},
        {"route_id": "finmind_margin", "records": [{"date": "2026-10-16", "margin_purchase_balance": 1234,
                                                     "margin_purchase_change": -34}]},
    ]
    rows = [
        ("valuation_per", "finmind_per", "本益比", "20261016", 20.5, "倍"),
        ("financial_Revenue_期間口徑未核實", "finmind_financial_statements", "營收", "20260630", 123456, "單位未核實"),
        ("margin_purchase_balance", "finmind_margin", "融資餘額", "20261016", 1234, "單位未核實"),
    ]
    payload["report_comparison"]["metrics"] = [{"id": key, "source_route": route,
        "current_value": value, "unit": unit, "項目": label, "資料期間": period,
        "本期值": f"{value:,}", "比較": "前期值保留", "解讀": "固定警語", "資料日期": "20261016"}
        for key, route, label, period, value, unit in rows]
    payload["report_comparison"]["metrics"].append(copy.deepcopy(payload["report_comparison"]["metrics"][0]))
    sections = report.build_report_sections(payload)
    latest = [row for section in sections for table in section["tables"]
              if table["title"] == "最新重點資料" for row in table["rows"]]
    assert all(row[1] not in {"PER 本益比", "營收", "融資餘額"} for row in latest)
    for preserved in ("PBR 股價淨值比", "現金殖利率", "營業利益", "融資餘額變動"):
        assert any(row[1] == preserved for row in latest)
    assert all(value in section_text(sections) for value in ("20.5", "123,456", "1,234"))
    assert section_text(sections).count("20.5") == 1


@pytest.mark.parametrize("basis,unit", [("quarter", "TWD"), ("year_to_date", "USD")])
def test_equal_numbers_with_different_financial_basis_or_units_are_not_hidden(payload, content_converter, basis, unit):
    payload["analysis_context"]["sources"] = [{"route_id": "finmind_financial_statements", "records": [
        {"date": "2026-06-30", "type": "Revenue", "value": 123456, "period_basis": basis, "unit": unit}]}]
    payload["report_comparison"]["metrics"] = [{"id": "financial_Revenue_year_to_date", "period_basis": "year_to_date",
        "source_route": "finmind_financial_statements", "current_value": 123456, "unit": "TWD", "項目": "營收",
        "資料期間": "20260630（年初累計）", "本期值": "123,456（新臺幣元）", "比較": "去年同口徑100,000",
        "解讀": "固定警語", "資料日期": "20261016"}]
    section = next(s for s in report.build_report_sections(payload) if s["id"] == "fundamentals")
    assert any(table["title"] == "最新重點資料" and any("123,456" in row[2] for row in table["rows"])
               for table in section["tables"])


def test_same_month_rank_and_incomplete_period_details_remain_visible(payload, content_converter):
    payload["report_comparison"]["metrics"] = [
        {"id": "revenue_same_month", "項目": "歷年同月位置", "資料期間": "2023、2024、2025、2026 年 09 月",
         "本期值": "120", "比較": "2023年 100；2024年 90；2025年 110；2026年 120", "資料日期": "20261016",
         "解讀": "實際 4 次同月觀測（含本年），由高至低第 1 名；同值同名次，不宣稱未提供年份齊全。"},
        {"id": "revenue_3m", "項目": "近三月營收", "資料期間": "202607～202609", "本期值": "已知 2／3 月合計 220",
         "比較": "去年同期未提供", "資料日期": "20261016", "解讀": "缺少 202608；已知合計不是完整期間營收，不補零。"},
    ]
    text = section_text(report.build_report_sections(payload))
    assert "實際 4 次同月觀測" in text and "第 1 名" in text
    assert "缺少 202608" in text and "不是完整期間營收" in text


@pytest.mark.parametrize("placement", ["caution_only", "before_fact", "after_fact"])
def test_fixed_period_caution_is_removed_by_content_without_losing_same_month_rank(
        payload, content_converter, placement):
    caution = "期末日不等於公告日；累計不當成單季，當期比較不宣稱回測時已公開。"
    fact = "實際 4 次同月觀測，由高至低第 1 名。"
    detail = {"caution_only": caution, "before_fact": caution + fact, "after_fact": fact + caution}[placement]
    payload["report_comparison"]["metrics"] = [{"id": "revenue_same_month", "source_route": "finmind_month_revenue",
        "項目": "歷年同月位置", "資料期間": "202609", "本期值": "120", "比較": "2025年 100；2026年 120",
        "解讀": detail, "資料日期": "20261016"}]
    original = copy.deepcopy(payload)
    text = section_text(report.build_report_sections(payload))
    assert caution not in text
    assert "2025年 100；2026年 120" in text
    assert (fact in text) is (placement != "caution_only")
    assert text.count("期間口徑：財報期末不是公告時間，累計不當單季；比較保留來源單位。") == 1
    assert payload == original


@pytest.mark.parametrize("horizon", [1, 63])
def test_bound_report_context_and_insights_replace_legacy_display_without_mutation(
        payload, content_converter, monkeypatch, horizon):
    payload["horizon_sessions"] = horizon
    payload["report_context"] = {"schema": "single-stock-report-context-v1", "stock_id": "2330",
        "asof": "2026-10-16", "sources": [
            {"route_id": "finmind_per", "records": [{"date": "2026-10-16", "PER": 31.25}]},
            {"route_id": "global_market_history", "records": [
                {"date": "2026-10-15", "symbol": "^SOX", "close": 6789.25, "unit": "points"}]}], "statuses": []}
    def insights(item):
        assert item["report_context"]["stock_id"] == "2330" and item["horizon_sessions"] == horizon
        return {"summary": ["預估方向與報酬的正負號不一致，結論保留分歧。"],
                "technical": ["收盤低於短期均線，量能未同步放大。"],
                "fundamentals": ["營收成長而營業利益下降，兩者尚未同步改善。"],
                "flows": ["外資近五日買超但近二十日仍賣超，短長期籌碼方向不同。"],
                "context": ["海外半導體與本股訊號分歧，背景不等於模型成因。"],
                "data_notes": ["本股財報缺去年同口徑基期，不能計算同比。"]}
    monkeypatch.setattr(report, "build_report_insights", insights)
    original = copy.deepcopy(payload)
    sections = report.build_report_sections(payload)
    text = section_text(sections)
    assert "31.25" in text and "6,789.25" in text
    assert "20.5（倍）" not in text and "5,000.25" not in text
    for key, phrase in (("summary", "正負號不一致"), ("technical", "量能未同步放大"),
                        ("fundamentals", "尚未同步改善"), ("flows", "短長期籌碼方向不同"),
                        ("context", "背景不等於模型成因")):
        assert phrase in section_text(sections, key)
    assert text.count("本股財報缺去年同口徑基期") == 1
    assert payload == original


def test_unknown_report_context_schema_uses_legacy_but_wrong_bound_stock_is_rejected(payload, content_converter, monkeypatch):
    monkeypatch.setattr(report, "build_report_insights", lambda item: pytest.fail("Unrecognized context is not an insights input"))
    payload["report_context"] = {"schema": "future-context-v2", "stock_id": "2330", "asof": payload["asof"], "sources": []}
    assert "20.5（倍）" in section_text(report.build_report_sections(payload))
    payload["report_context"].update(schema="single-stock-report-context-v1", stock_id="2376")
    with pytest.raises(report.CandidateReportError) as caught:
        report.build_report_sections(payload)
    assert caught.value.code == "report_context_binding_invalid"


@pytest.mark.parametrize("horizon", [1, 63])
def test_verified_official_stock_profile_fills_only_missing_company_fields(
        payload, content_converter, monkeypatch, horizon):
    payload["stock"].update(stock_id="2376", stock_name=None, industry=None)
    payload["horizon_sessions"] = horizon
    payload["report_comparison"]["stock_id"] = "2376"
    payload["report_context"] = {"schema": "single-stock-report-context-v1", "stock_id": "2376",
        "asof": payload["asof"], "sources": [], "statuses": [], "stock_profile": {
            "stock_id": "2376", "market": "TWSE", "stock_name": "技嘉科技", "industry": "電腦及週邊設備業",
            "classification_verified": True, "classification_observed_date": "2026-10-15",
            "source_route": "official_sector_features"}}
    monkeypatch.setattr(report, "build_report_insights", lambda item: {
        key: [] for key in ("summary", "technical", "fundamentals", "flows", "context", "data_notes")})
    original = copy.deepcopy(payload)
    text = section_text(report.build_report_sections(payload), "summary")
    assert "技嘉科技" in text and "電腦及週邊設備業" in text
    assert "58.00%" in text and "-1.23%" in text
    assert payload == original
    payload["stock"].update(stock_name="既有公司名", industry="既有產業")
    assert report._company_profile(payload) == {"stock_name": "既有公司名", "industry": "既有產業"}
    assert payload["report_context"]["stock_profile"] == original["report_context"]["stock_profile"]


@pytest.mark.parametrize("field,value", [
    ("stock_id", "9999"), ("market", "TPEX"), ("classification_verified", False),
    ("classification_verified", "true"), ("classification_observed_date", "2026-10-17"),
    ("classification_observed_date", None), ("source_route", "finmind_stock_info"),
])
def test_unbound_or_unverified_official_stock_profile_is_not_used(
        payload, content_converter, monkeypatch, field, value):
    payload["stock"].update(stock_id="2376", stock_name=None, industry=None)
    payload["report_context"] = {"schema": "single-stock-report-context-v1", "stock_id": "2376",
        "asof": payload["asof"], "sources": [], "statuses": [], "stock_profile": {
            "stock_id": "2376", "market": "TWSE", "stock_name": "技嘉科技", "industry": "電腦及週邊設備業",
            "classification_verified": True, "classification_observed_date": "2026-10-15",
            "source_route": "official_sector_features"}}
    payload["report_context"]["stock_profile"][field] = value
    monkeypatch.setattr(report, "build_report_insights", lambda item: {
        key: [] for key in ("summary", "technical", "fundamentals", "flows", "context", "data_notes")})
    original = copy.deepcopy(payload)
    text = section_text(report.build_report_sections(payload), "summary")
    assert "技嘉科技" not in text and "電腦及週邊設備業" not in text
    assert report._company_profile(payload) == {}
    assert payload == original


def test_compact_context_points_render_declared_fields_without_guessing(payload, content_converter, monkeypatch):
    payload["report_context"] = {"schema": "single-stock-report-context-v1", "stock_id": "2330", "asof": payload["asof"],
        "sources": [
            {"route_id": "finmind_per", "records": [{"entity_id": "2330", "date": "2026-10-16", "type": "",
                "field": "PER", "value": 31.25, "unit": "times", "observation_role": "latest"}]},
            {"route_id": "finmind_institutional", "records": [{"entity_id": "2330", "date": "2026-10-16",
                "type": "Foreign_Investor", "field": "buy", "value": 2345, "unit": "shares", "observation_role": "latest"}]},
            {"route_id": "global_market_history", "records": [{"symbol": "^SOX", "date": "2026-10-15", "type": "",
                "field": "close", "value": 6789.25, "unit": "points", "observation_role": "latest"}]},
        ], "statuses": []}
    monkeypatch.setattr(report, "build_report_insights", lambda item: {
        key: [] for key in ("summary", "technical", "fundamentals", "flows", "context", "data_notes")})
    original = copy.deepcopy(payload)
    sections = report.build_report_sections(payload)
    assert "31.25（倍）" in section_text(sections, "fundamentals")
    assert "外資／買進數量" in section_text(sections, "flows")
    assert "2,345（股）" in section_text(sections, "flows")
    assert "6,789.25（點）" in section_text(sections, "context")
    assert payload == original


def test_normalized_revenue_uses_original_month_and_does_not_promote_older_points_after_deduplication(
        payload, content_converter, monkeypatch):
    payload["report_context"] = {"schema": "single-stock-report-context-v1", "stock_id": "2330", "asof": payload["asof"],
        "sources": [{"route_id": "finmind_month_revenue", "records": [
            {"date": "2026-10-10", "period_end": "2026-10-10", "entity_id": "2330", "type": "", "field": "revenue",
             "value": 1200, "unit": "TWD", "revenue_year": 2026, "revenue_month": 9, "observation_role": "latest"},
            {"date": "2026-10-12", "period_end": "2026-10-12", "entity_id": "2330", "type": "", "field": "revenue",
             "value": 900, "unit": "TWD", "revenue_year": 2026, "revenue_month": 8, "observation_role": "previous"},
        ]}], "statuses": []}
    monkeypatch.setattr(report, "build_report_insights", lambda item: {
        key: [] for key in ("summary", "technical", "fundamentals", "flows", "context", "data_notes")})
    sections = report.build_report_sections(payload)
    latest = next(table for section in sections if section["id"] == "fundamentals"
                  for table in section["tables"] if table["title"] == "最新重點資料")
    assert latest["rows"] == [["2026 年 9 月", "營收", "1,200（新臺幣元）"]]
    payload["report_comparison"]["metrics"] = [{"id": "revenue_monthly", "source_route": "finmind_month_revenue",
        "current_value": 1200, "unit": "TWD", "項目": "月營收", "資料期間": "202609", "本期值": "1,200（新臺幣元）",
        "比較": "前月（202608）900（新臺幣元）；去年同月600（新臺幣元）", "解讀": "固定警語", "資料日期": "20261010"}]
    original = copy.deepcopy(payload)
    sections = report.build_report_sections(payload)
    tables = next(section for section in sections if section["id"] == "fundamentals")["tables"]
    assert not any(table["title"] == "最新重點資料" for table in tables)
    assert "202609" in section_text(sections, "fundamentals") and "900（新臺幣元）" in section_text(sections, "fundamentals")
    assert payload == original


def test_unverified_target_date_is_disclosed_once_without_hiding_other_unverified_gaps(
        payload, content_converter, monkeypatch):
    payload["target_session_date"] = None
    payload["prediction"]["target_date_status"] = "unverified_future_calendar"
    payload["report_context"] = {"schema": "single-stock-report-context-v1", "stock_id": "2330", "asof": payload["asof"],
                                 "sources": [], "statuses": []}
    monkeypatch.setattr(report, "build_report_insights", lambda item: {
        "summary": [], "technical": [], "fundamentals": [], "flows": [], "context": [],
        "data_notes": ["目標交易日尚未驗證，未以平日推算日期。",
                       "營業現金流量的期間口徑未核實，未比較獲利與現金實現程度。"]})
    sections = report.build_report_sections(payload)
    text = section_text(sections)
    assert "未來交易日曆未核實" in section_text(sections, "summary")
    assert text.count("不以平日推算") == 1 and "2026-10-19" not in text
    assert "期間口徑未核實" in text and "未比較獲利與現金實現程度" in text
    assert payload["target_session_date"] is None


def test_insights_cannot_bypass_existing_model_estimate_binding_checks(payload, content_converter, monkeypatch):
    payload.pop("model_binding")
    payload["report_context"] = {"schema": "single-stock-report-context-v1", "stock_id": "2330", "asof": payload["asof"],
                                 "sources": [], "statuses": []}
    monkeypatch.setattr(report, "build_report_insights", lambda item: {
        "summary": ["模型上漲機率58.00%與預估報酬-1.23%。"], "technical": [], "fundamentals": [],
        "flows": [], "context": [], "data_notes": []})
    text = section_text(report.build_report_sections(payload), "summary")
    assert "58.00%" not in text and "-1.23%" not in text
    assert "模型綁定或分支未確認" in text


@pytest.mark.parametrize("route,record,label", [
    ("global_market_history", {"symbol": "us_sp500", "adj_close": 5012.75}, "美國標普500指數"),
    ("finmind_government_bonds_yield", {"name_zh": "美國十年期公債", "value": 4.25}, "美國十年期公債"),
    ("finmind_crude_oil_prices", {"symbol": "CL=F", "price": 72.5}, "WTI 原油期貨"),
    ("finmind_gold_price", {"symbol": "GC=F", "Price": 2680.5}, "黃金期貨"),
])
def test_verified_macro_field_and_instrument_mappings_are_visible(payload, content_converter, route, record, label):
    tables, notes = report._brief_context(payload, [{"route_id": route, "records": [dict(record, date="2026-10-16")]}], "context")
    assert len(tables) == 1 and label in str(tables)
    assert not any("無明確對照" in note for note in notes)


def test_whole_route_partial_and_not_selected_are_not_individual_stock_gaps(payload, content_converter):
    payload["report_comparison"]["notes"] = [
        "估值來源狀態 PARTIAL；可讀部分保留，不宣稱完整。",
        "營收比較不可得：來源未提供或物件不唯一。"]
    payload["analysis_context"]["sources"][0]["status"] = "PARTIAL"
    payload["analysis_context"]["sources"].append({"route_id": "finmind_trading_daily_report",
        "usage": "not-selected", "availability": "missing", "records": []})
    text = section_text(report.build_report_sections(payload))
    assert "PARTIAL" not in text and "not-selected" not in text
    assert "券商分點交易：資料缺失" not in text and "估值缺失" not in text
    assert "營收比較不可得" in text and "來源未提供或物件不唯一" in text
    assert "20.5" in text


def test_comparison_bridge_uses_analysis_pin_and_reuses_horizons(payload, monkeypatch, tmp_path):
    comparison_bridge._pinned_block.cache_clear()
    calls = []
    def build(stocks, signal, **kwargs):
        calls.append((stocks, signal, kwargs))
        return {"2330": {"schema": "stock-report-comparison-v1", "metrics": [], "notes": []}}
    monkeypatch.setattr(comparison_bridge, "_engine", lambda: SimpleNamespace(build_comparisons=build))
    monkeypatch.setattr(comparison_bridge, "_comparison_temp_root", lambda: tmp_path)
    first, second = copy.deepcopy(payload), copy.deepcopy(payload)
    first.pop("report_comparison", None)
    second.pop("report_comparison", None)
    second["horizon_sessions"] = 63
    prediction = copy.deepcopy(first["prediction"])
    comparison_bridge.attach_report_comparison(first)
    comparison_bridge.attach_report_comparison(second)
    assert len(calls) == 1
    assert calls[0] == (["2330"], "2026-10-16", {"head_sha": "b" * 64,
                       "use_verified_projection": True, "consumer_temp_root": tmp_path})
    assert first["prediction"] == prediction
    comparison_bridge._pinned_block.cache_clear()


def test_comparison_bridge_new_pin_also_opts_in(payload, monkeypatch, tmp_path):
    calls = []
    def build(stocks, signal, **kwargs):
        calls.append(kwargs)
        return {"2330": {"schema": "stock-report-comparison-v1", "metrics": [], "notes": []}}
    monkeypatch.setattr(comparison_bridge, "_engine", lambda: SimpleNamespace(build_comparisons=build))
    monkeypatch.setattr(comparison_bridge, "analysis_head", lambda payload: None)
    monkeypatch.setattr(comparison_bridge, "_comparison_temp_root", lambda: tmp_path)
    payload.pop("report_comparison", None)
    comparison_bridge.attach_report_comparison(payload)
    assert calls == [{"head_sha": None, "use_verified_projection": True, "consumer_temp_root": tmp_path}]


def test_comparison_bridge_temp_uses_single_runtime_boundary(monkeypatch, tmp_path):
    import stock_daily_candidate_runtime as runtime
    import stock_daily_selected_deployment as deployment

    monkeypatch.setattr(deployment, "SINGLE_ROOT", tmp_path)
    assert comparison_bridge._comparison_temp_root() == runtime._consumer_path(tmp_path / "cache" / "report_comparison")
    monkeypatch.setattr(deployment, "SINGLE_ROOT", runtime.PUBLISHED_ROOT)
    with pytest.raises(runtime.CandidateRuntimeError):
        comparison_bridge._comparison_temp_root()


def test_missing_comparison_engine_retains_context_and_prediction(payload, monkeypatch):
    comparison_bridge._pinned_block.cache_clear()
    def missing():
        raise ImportError("optional engine unavailable")
    monkeypatch.setattr(comparison_bridge, "_engine", missing)
    payload.pop("report_comparison", None)
    before = copy.deepcopy(payload)
    block = comparison_bridge.attach_report_comparison(payload)
    assert "比較不可得" in block["notes"][0]
    assert payload["prediction"] == before["prediction"]
    assert payload["analysis_context"] == before["analysis_context"]


@pytest.fixture
def payload():
    return {
        "schema": report.SCHEMA, "language": "zh-TW", "research_only": True, "promotion": False,
        "stock": {"stock_id": "2330", "stock_name": "台灣積體電路製造", "market": "TWSE", "industry": "半導體業"},
        "horizon_sessions": 1, "asof": "2026-10-16", "target_session_date": "2026-10-19",
        "prediction": {"prob_up": 0.58, "expected_return": -0.0123, "prediction_status": "predicted",
                       "selected_direction_kind": "expanded_linear", "selected_return_kind": "expanded_hgb",
                       "calibration_status": "uncalibrated", "reason_zh": "模型估計尚未正式採用。"},
        "model_binding": {"path": "D:/isolated/candidate_model.json", "sha256": "a" * 64,
                          "schema": "candidate-model-v1", "feature_schema": "candidate-features-v1"},
        "snapshot_binding": {"head_sha256": "b" * 64, "published_at": "2026-10-17T14:00:00+08:00"},
        "source_manifest": [
            {"route_id": "finmind_per", "name_zh": "本益比與股價淨值比", "usage": "used", "availability": "available",
             "status": "SUCCESS", "reason_zh": "依事前規則採用。", "latest_data_date": "2026-10-16"},
            {"route_id": "finmind_trading_daily_report", "usage": "not-selected", "availability": "missing", "reason_zh": "本期限未選用。"},
            {"route_id": "finmind_balance_sheet", "usage": "context-only", "availability": "missing", "reason_zh": "本股資料缺漏。"},
            {"route_id": "finmind_block_trade", "usage": "context-only", "availability": "legitimate_empty",
             "reason_zh": "期間內官方確認無事件。", "empty_evidence": {"stock_id": "2330", "period": "2026-10"}},
        ],
        "feature_values": [
            {"name": "return_5", "label_zh": "五日還原收盤報酬", "value": 0.023, "unit": "比例", "selected": True,
             "source_route": "finmind_price_adj", "calculation_zh": "當期還原收盤價除以五日前收盤價減一。",
             "selected_by_task": {"direction": {"selected": True, "numeric": True, "missing_indicator": False}},
             "feature_validity": {"direction": {"state": "fit_informative", "numeric_effective": True,
                                                   "missing_indicator_effective": False, "n_fit_finite": 500, "n_fit_rows": 510}},
             "coverage": {"training_by_task": {"direction": {"fit": {"n_finite": 500, "n_denominator": 510, "coverage": 500 / 510}}}}},
        ],
        "analysis_context": {"stock_id": "2330", "sources": [
            {"route_id": "finmind_per", "name_zh": "本益比與股價淨值比", "usage": "used", "availability": "available",
             "records": [{"date": "2026-10-16", "PER": 20.5, "PBR": 4.1, "dividend_yield": 1.8}]},
            {"route_id": "finmind_financial_statements", "usage": "context-only", "availability": "unknown_availability",
             "records": [{"date": "2026-06-30", "type": "Revenue", "value": 123456},
                         {"date": "2026-06-30", "type": "OperatingIncome", "value": 56789}]},
            {"route_id": "finmind_institutional", "usage": "used", "availability": "unknown_availability",
             "records": [{"date": "2026-10-16", "name": "Foreign_Investor", "buy": 2000, "sell": 1500}]},
            {"route_id": "global_market_history", "usage": "used", "availability": "available",
             "records": [{"date": "2026-10-15", "name": "SOX", "close": 5000.25, "unit": "點"}]},
            {"route_id": "news_and_hot_topics_raw", "usage": "context-only", "availability": "available",
             "records": [{"date": "2026-10-15", "title": "半導體產業公布營收資料", "summary_zh": "僅為來源事件摘要，不推論股價。",
                          "source": "公開新聞來源", "published_at": "2026-10-15T12:00:00+08:00"}]},
        ]},
        "metrics": {
            "reason_zh": "選模已鎖定，保留測試僅供評估。",
            "comparison": {"expanded_linear": {"status": "trained", "n_denominator": 1000,
                "selection": {"direction": {"accuracy": 0.51, "brier": 0.27, "n_effective_signal_days": 22}},
                "test": {"direction": {"accuracy": 0.48, "n_predicted": 200, "n_denominator": 210}}}},
            "selection": {"selected": {"direction": "expanded_linear", "return": "expanded_hgb"}, "lock_sha256": "c" * 64},
            "holdout": {"expanded_linear": {"direction": {"accuracy": 0.48, "brier": 0.29, "n_evaluated": 200,
                                                           "class_counts": {"up": 90, "not_up": 110}}}},
            "baselines": {"test": {"direction": {"brier": 0.25}, "return": {"mae": 0.02}}},
            "paired_differences": {"test": {"direction": {"metric": "brier", "model_minus_baseline": 0.04,
                                                             "worse_than_baseline": True, "n_paired": 200}}},
        },
        "limitations": ["缺少歷史公告時點證據，不宣稱已驗證獲利能力。"],
        "price_history": [{"date": "2026-10-15", "close": 101, "volume": 1000, "price_basis": "adjusted"},
                          {"date": "2026-10-16", "close": 102, "volume": 1500, "price_basis": "adjusted"}],
        "generated_at": "2026-10-17T10:00:00+08:00",
        "report_comparison": {"schema": "stock-report-comparison-v1", "stock_id": "2330",
                              "signal_date": "2026-10-16", "source_head_sha256": "b" * 64,
                              "metrics": [], "notes": ["離線報告夾具未提供比較數字。"]},
    }


@pytest.fixture
def content_converter(monkeypatch):
    """Content tests can run on non-Windows without pretending native conversion exists."""
    monkeypatch.setattr(report, "_converter", lambda: lambda text: text)


def section_text(sections, key=None):
    selected = [s for s in sections if s["id"] == key] if key else sections
    return "\n".join(report._visible_strings(selected))


@pytest.fixture
def formal_payload(payload):
    result = copy.deepcopy(payload)
    result.update(schema=report.FORMAL_SCHEMA, research_only=False, promotion=True,
                  deployment_binding={"schema": "single-stock-selected-deployment-v1", "consumer_id": "single",
                                      "activation_id": "activation-20261017", "model_sha256": payload["model_binding"]["sha256"]})
    result["prediction"].update(prediction_status="formal_estimate", reason_zh="採用已啟用模型的估計。")
    return result


def test_formal_payload_title_boundary_and_no_pointer_reads(formal_payload, content_converter, monkeypatch):
    def forbidden(*args, **kwargs):
        raise AssertionError("The report builder must not authorize or read deployment pointers.")
    monkeypatch.setattr(report, "_stable_json", forbidden)
    formal_payload["prediction"]["reason_zh"] = "候選研究，未正式採用；排序不足兩筆。"
    sections = report.build_report_sections(formal_payload)
    assert report._report_title(formal_payload) == "正式個股分析報告"
    assert len(sections) == 6
    text = section_text(sections)
    assert "已啟用模型" in text and "58.00%" in text
    assert "候選研究" not in text and "未正式採用" not in text and "不足兩筆" not in text
    assert "估計狀態與原因" not in text
    assert "activation-20261017" not in text


@pytest.mark.parametrize("location", ["payload", "binding"])
def test_preactivation_audit_is_not_described_as_activated(formal_payload, content_converter, location):
    target = formal_payload if location == "payload" else formal_payload["deployment_binding"]
    target["deployment_status"] = "preactivation_audit"
    formal_payload["prediction"]["reason_zh"] = "候選研究，未正式採用，排序不足兩筆。"
    text = section_text(report.build_report_sections(formal_payload), "summary")
    assert "啟用前驗收" in report._report_title(formal_payload)
    assert "啟用前驗收，尚未啟用" in text
    assert "已啟用模型" not in text and "不足兩筆" not in text
    assert "58.00%" in text and "-1.23%" in text


@pytest.mark.parametrize("status", ["no_estimate", "not_predicted"])
def test_only_no_estimate_shows_actual_blocker_without_internal_boilerplate(formal_payload, content_converter, status):
    formal_payload["prediction"].update(prediction_status=status,
        reason_zh="候選研究，未正式採用。推論特徵缺少最新價格。排序不足兩筆。")
    text = section_text(report.build_report_sections(formal_payload), "summary")
    assert "無法估計原因" in text and "推論特徵缺少最新價格" in text
    assert "候選研究" not in text and "未正式採用" not in text and "不足兩筆" not in text
    assert "58.00%" not in text and "-1.23%" not in text


def test_no_estimate_keeps_genuine_insufficient_price_samples(formal_payload, content_converter):
    formal_payload["prediction"].update(prediction_status="no_estimate", reason_zh="有效收盤資料不足兩筆，無法計算模型輸入。")
    text = section_text(report.build_report_sections(formal_payload), "summary")
    assert "有效收盤資料不足兩筆" in text and "無法計算模型輸入" in text


@pytest.mark.parametrize("key,value", [
    ("schema", "wrong"), ("consumer_id", "telegram"), ("activation_id", ""), ("activation_id", "  "),
    ("activation_id", 1), ("model_sha256", "b" * 64), ("model_sha256", None),
])
def test_formal_deployment_binding_must_be_consistent(formal_payload, key, value):
    formal_payload["deployment_binding"][key] = value
    with pytest.raises(report.CandidateReportError) as caught:
        report.build_report_sections(formal_payload)
    assert caught.value.code == "deployment_binding_invalid"


@pytest.mark.parametrize("binding", [None, {}, [], "activation"])
def test_formal_payload_requires_deployment_binding(formal_payload, binding):
    formal_payload["deployment_binding"] = binding
    with pytest.raises(report.CandidateReportError) as caught:
        report.build_report_sections(formal_payload)
    assert caught.value.code == "deployment_binding_invalid"


@pytest.mark.parametrize("key,value", [("language", "zh-CN"), ("research_only", True), ("promotion", False)])
def test_formal_flags_cannot_claim_research(formal_payload, key, value):
    formal_payload[key] = value
    with pytest.raises(report.CandidateReportError) as caught:
        report.build_report_sections(formal_payload)
    assert caught.value.code == "report_boundary_invalid"


@pytest.mark.parametrize("relative", ["candidate/reports", "Research/reports", "staging/reports", "reports/candidate_2330.pdf"])
def test_formal_output_rejects_nonformal_directory_or_filename(formal_payload, relative):
    with pytest.raises(report.CandidateReportError) as caught:
        report._check_output_path(formal_payload, Path("D:/formal_reports") / relative)
    assert caught.value.code == "formal_output_path_invalid"


def test_formal_output_rejection_precedes_fonts_or_writes(formal_payload, content_converter, monkeypatch):
    monkeypatch.setattr(report, "_load_fonts", lambda: pytest.fail("Must reject path before font loading or writes."))
    with pytest.raises(report.CandidateReportError) as caught:
        report.write_candidate_report(formal_payload, Path("D:/research/reports"))
    assert caught.value.code == "formal_output_path_invalid"
    checked = report.validate_candidate_report(formal_payload, Path("D:/staging/single_stock_2330.pdf"))
    assert checked["valid"] is False and checked["errors"][0]["code"] == "formal_output_path_invalid"


@pytest.mark.parametrize("formal", [False, True])
def test_bundle_paths_flags_and_complete_payload_preserved(payload, formal_payload, content_converter, monkeypatch, tmp_path, formal):
    item = formal_payload if formal else payload
    item["source_manifest"].append({"route_id": "unmapped_source", "raw_original": {"keep": 123}})
    item["limitations"].append("完整限制原文留在附件，不因精簡正文刪除。")
    original = copy.deepcopy(item)
    monkeypatch.setattr(report, "_load_fonts", lambda: SimpleNamespace(check=lambda texts: None))
    # Central QA may itself live beneath a candidates directory; path policy has separate tests above.
    monkeypatch.setattr(report, "_check_output_path", lambda *args: None)
    monkeypatch.setattr(report, "_render_pdf", lambda path, *args: path.write_bytes(b"mock preflight fixture, not a PDF"))
    monkeypatch.setattr(report, "_inspect_structure", lambda *args: {"pages": 1, "embedded_truetype_subsets": 1})
    monkeypatch.setattr(report, "_extract_if_available", lambda *args: {"status": "pending_external"})
    paths = report.write_candidate_report(item, tmp_path / "reports")
    assert {"pdf_path", "json_path", "bundle_dir", "pdf", "json", "sections", "validation"} <= paths.keys()
    assert paths["research_only"] is (not formal) and paths["promotion"] is formal
    assert paths["validation"]["research_only"] is (not formal) and paths["validation"]["promotion"] is formal
    assert paths["validation"]["sections_layout"] == "concise_six_chapters"
    assert paths["deliverable"] is False and paths["render_status"] == "pending_external"
    prefix = "single_stock_" if formal else "candidate_"
    assert Path(paths["pdf_path"]).name.startswith(prefix)
    if formal:
        for key in ("pdf_path", "json_path", "bundle_dir"):
            assert not any(word in Path(paths[key]).name.lower() for word in ("candidate", "research", "staging"))
    sidecar = json.loads(Path(paths["json_path"]).read_text(encoding="utf-8"))
    assert sidecar["payload"] == original
    evidence = sidecar["machine_evidence"]
    assert evidence["prediction_reason_zh"] == original["prediction"]["reason_zh"]
    assert "歷史公告時點未驗證" in evidence["interpretation_notes"]["flows"]
    assert "不由估計報酬反推合理價" in evidence["interpretation_notes"]["fundamentals"]
    assert sidecar["payload_sha256"] == report._digest(original)
    assert item == original


def test_latest_meaningful_context_no_nulls_unknown_accounts_or_duplicate_aliases(payload, content_converter):
    records = payload["analysis_context"]["sources"][0]["records"]
    records[:] = [{"date": "2026-10-15", "PER": 10, "PBR": None},
                  {"date": "2026-10-16", "PER": 20.5, "per": 20.5, "PBR": None},
                  {"date": "2026-10-20", "PER": 99}]
    tables, notes = report._brief_context(payload, [payload["analysis_context"]["sources"][0]], "fundamentals")
    assert len(tables[0]["rows"]) == 1
    assert tables[0]["rows"][0] == ["2026-10-16", "PER 本益比", "20.5（倍）"]
    assert "晚於資料日" in str(notes)


@pytest.mark.parametrize("canonical,alias,expected", [(None, 20.5, "20.5（倍）"), (20.5, 21, "同日數值衝突")])
def test_alias_does_not_hide_valid_value_or_conflict(payload, content_converter, canonical, alias, expected):
    source = payload["analysis_context"]["sources"][0]
    source["records"] = [{"date": "2026-10-16", "PER": canonical, "per": alias}]
    tables, _ = report._brief_context(payload, [source], "fundamentals")
    assert len(tables[0]["rows"]) == 1
    assert expected in tables[0]["rows"][0][2]


def test_context_close_metadata_stays_in_payload_not_visible_table(payload, content_converter):
    payload["analysis_context"]["sources"][3]["records"][0]["source_close_at"] = "2026-10-15T20:00:00Z"
    text = section_text(report.build_report_sections(payload), "context")
    assert "2026-10-15T20:00:00Z" not in text
    assert payload["analysis_context"]["sources"][3]["records"][0]["source_close_at"] == "2026-10-15T20:00:00Z"


def test_irrelevant_industry_and_news_not_attributed_to_stock(payload, content_converter):
    payload["analysis_context"]["sources"].extend([
        {"route_id": "official_sector_observations", "records": [
            {"date": "2026-10-15", "sector_label": "航運／航空", "value": 88, "unit": "%"}]},
        {"route_id": "news_and_hot_topics_raw", "records": [
            {"date": "2026-10-15", "title": "海外法院判決新聞", "published_at": "2026-10-15T12:00:00Z", "source": "新聞"}]},
    ])
    text = section_text(report.build_report_sections(payload), "context")
    assert "航運／航空" not in text and "海外法院判決新聞" not in text
    assert "半導體產業公布營收資料" in text


def test_selected_finaltest_only_with_mae_denominators_and_worse_results(payload, content_converter):
    metrics = payload["metrics"]
    metrics["holdout"]["expanded_hgb"] = {"return": {"mae": 0.14, "n_denominator": 1000, "n_evaluated": 987,
        "n_failed": 5, "n_unscored": 13, "n_effective_signal_days": 20, "n_stocks": 50}}
    metrics["holdout"]["unselected"] = {"direction": {"accuracy": 0.99}}
    metrics["baselines"]["test"]["return"].update(mae=0.13, n_denominator=1000, n_evaluated=987)
    metrics["paired_differences"]["test"]["return"] = {
        "metric": "mae", "model_minus_baseline": 0.01, "n_paired": 987, "worse_than_baseline": False}
    original = copy.deepcopy(payload)
    text = section_text(report.build_report_sections(payload), "evaluation")
    for item in ("14.00 個百分點", "13.00 個百分點", "987／1,000", "5／13", "MAE 劣於基準"):
        assert item in text
    for detail in ("1.00 個百分點", "基準分母", "有效訊號日", "股票數", "成對 987", "Brier"):
        assert detail not in text
    assert "99.00%" not in text and "51.00%" not in text
    assert "14.00%" not in text
    assert payload == original


def test_selected_conflicting_finaltest_remains_visible_without_picking_better(payload, content_converter):
    payload["metrics"]["holdout"]["expanded_linear"]["direction"]["accuracy"] = 0.49
    text = section_text(report.build_report_sections(payload), "evaluation")
    assert "48.00%" in text and "49.00%" in text
    assert "兩份並列，不擇優採用" in text


def test_prediction_selection_mismatch_does_not_reuse_other_models_paired_score(payload, content_converter):
    payload["metrics"]["selection"]["selected"]["direction"] = "expanded_hgb"
    text = section_text(report.build_report_sections(payload), "evaluation")
    assert "推論模型與選模紀錄不一致" in text
    assert "48.00%" in text
    assert "0.04" not in text


def test_reminders_group_critical_gaps_without_full_limitations_chapter(payload, content_converter):
    payload["horizon_sessions"] = 63
    payload["target_session_date"] = None
    payload["limitations"].extend(["本股目前處置交易，須留意成交限制。", "一般機器證據敘述不展開正文。"])
    sections = report.build_report_sections(payload)
    summary = next(section for section in sections if section["id"] == "summary")
    reminders = report._brief_reminders(payload, ["第 63 交易日日期未驗證", "長期限標籤重疊，訊號日數不等於獨立樣本"])
    assert len(reminders) <= 2
    for important in ("機率尚未校準", "處置交易"):
        assert important in "\n".join(reminders)
    for omitted in ("日期未驗證", "訊號日數", "資產負債表", "資料缺口", "不能視為完整分析"):
        assert omitted not in section_text([summary])
    assert "歷史公告時點或歷史成分股資格仍有未驗證項" not in "\n".join(reminders)
    assert "一般機器證據敘述" not in section_text(sections)


def test_chapter_diagnostics_show_impacts_without_repeating_machine_cautions(payload, content_converter):
    sections = report.build_report_sections(payload)
    text = section_text(sections)
    evidence = report._report_machine_evidence(payload)
    assert "離線報告夾具未提供比較數字" in text
    assert text.count(report._RAW_VALUE_NOTE) == 1
    for caution in ("期末日不等於公告日", "分母未核實", "歷史公告時點未驗證", "反推合理價", "缺交易日未另核實"):
        assert caution not in text
    assert evidence["prediction_reason_zh"] == payload["prediction"]["reason_zh"]
    assert evidence["interpretation_notes"] == report._MACHINE_INTERPRETATION_NOTES
    assert evidence["technical_diagnostics"]
    assert evidence["context_diagnostics"]["fundamentals"]


def test_unknown_availability_never_becomes_current_data_gap_list(payload, content_converter):
    payload["limitations"] = []
    payload["source_manifest"] = [{"route_id": "route_" + str(i), "name_zh": "歷史狀態來源" + str(i),
        "usage": "used", "availability": "unknown_availability"} for i in range(20)]
    payload["target_session_date"] = None
    payload["horizon_sessions"] = 63
    sections = report.build_report_sections(payload)
    summary = section_text(sections, "summary")
    for omitted in ("資料缺口", "歷史狀態來源", "日期未驗證", "不能視為完整分析"):
        assert omitted not in summary
    assert "第 63 個交易日收盤" in summary
    assert "機率尚未校準" in summary
    assert not report._historical_timing_unconfirmed(payload)
    assert report._HISTORICAL_TIMING_RISK not in section_text(sections)
    assert len(payload["source_manifest"]) == 20


@pytest.mark.parametrize("evidence_kind", ["limitation", "historical_flag", "lagged_publication"])
def test_historical_timing_risk_is_one_evaluation_sentence_not_current_gap(payload, content_converter, evidence_kind):
    payload["limitations"] = []
    if evidence_kind == "limitation":
        payload["limitations"] = ["缺少歷史公告時點證據。"]
    elif evidence_kind == "historical_flag":
        payload["source_manifest"][0]["historical_PIT_unverified"] = True
    else:
        payload["source_manifest"][0].update(available_time_basis="lagged-date", publication_time_verified=False)
    sections = report.build_report_sections(payload)
    text = section_text(sections)
    assert text.count(report._HISTORICAL_TIMING_RISK) == 1
    assert report._HISTORICAL_TIMING_RISK in section_text(sections, "evaluation")
    assert "資料缺口" not in section_text(sections, "summary")
    assert report._report_machine_evidence(payload)["historical_timing_unconfirmed"] is True


def test_undated_company_profile_moves_to_summary_and_undated_numbers_stay_json(payload, content_converter):
    payload["stock"].update(stock_name=None, industry=None)
    payload["analysis_context"]["sources"].append({"route_id": "finmind_stock_info", "records": [
        {"stock_id": "2330", "stock_name": "台積電", "source_industry": "半導體業"}]})
    payload["analysis_context"]["sources"][1]["records"].append({"type": "EPS", "value": 999})
    before = copy.deepcopy(payload)
    sections = report.build_report_sections(payload)
    assert "台積電" in section_text(sections, "summary") and "半導體業" in section_text(sections, "summary")
    for omitted in ("公司名稱", "期間未提供", "999"):
        assert omitted not in section_text(sections, "fundamentals")
    assert payload == before


def test_unknown_units_show_raw_values_with_one_table_note_and_sidecar_diagnostics(payload, content_converter):
    sources = [{"route_id": "finmind_financial_statements", "records": [
        {"date": "2026-06-30", "type": "Revenue", "value": 1270380250000},
        {"date": "2026-06-30", "type": "EPS", "value": 27.25}]}]
    original = copy.deepcopy(sources)
    tables, notes = report._brief_context(payload, sources, "fundamentals")
    assert [row[2] for row in tables[0]["rows"]] == ["1,270,380,250,000", "27.25"]
    assert tables[0]["note"] == report._RAW_VALUE_NOTE
    assert "單位未提供" not in str(tables[0]["rows"])
    assert sum("單位未提供" in note for note in notes) == 2
    assert sources == original


@pytest.mark.parametrize("symbol,label", [
    ("^TNX", "美國10年期公債殖利率"), ("GC=F", "黃金期貨"), ("CL=F", "WTI 原油期貨"),
    ("JPY=X", "美元兌日圓"), ("KRW=X", "美元兌韓元"), ("TWD=X", "美元兌新臺幣"),
])
def test_global_exact_symbol_close_alias_keeps_raw_value_and_no_utc(payload, content_converter, symbol, label):
    sources = [{"route_id": "global_market_history", "records": [
        {"date": "2026-10-16", "symbol": symbol, "instrument": "internal_raw_code", "close": 5.098,
         "source_close_at": "2026-10-16T12:20:00Z"}]}]
    before = copy.deepcopy(sources)
    tables, _ = report._brief_context(payload, sources, "context")
    assert tables[0]["rows"] == [["2026-10-16", label + "（" + symbol + "）", "5.098"]]
    shown = str(tables)
    for omitted in ("未核對", "未識別", "internal_raw_code", "T12:20", "單位未提供"):
        assert omitted not in shown
    assert sources == before


def test_global_alias_requires_exact_symbol_and_field(payload, content_converter):
    sources = [{"route_id": "global_market_history", "records": [
        {"date": "2026-10-16", "symbol": "UNKNOWN", "close": 999},
        {"date": "2026-10-16", "symbol": "^TNX", "value": 888}]}]
    tables, notes = report._brief_context(payload, sources, "context")
    assert not tables and notes
    assert len(sources[0]["records"]) == 2


def test_compact_finaltest_all_and_stock_market_scores_counts_and_baselines(payload, content_converter):
    for task, kind, score in (("direction", "expanded_linear", {"accuracy": 0.48}), ("return", "expanded_hgb", {"mae": 0.14})):
        stats = {**score, "n_evaluated": 987, "n_denominator": 1000, "n_failed": 5, "n_unscored": 13,
                 "n_effective_signal_days": 20, "n_stocks": 50, "brier": 0.29,
                 "by_market": {"TWSE": {**score, "n_evaluated": 520, "n_denominator": 520, "n_failed": 0, "n_unscored": 0}}}
        payload["metrics"]["holdout"][kind] = {task: stats}
        payload["metrics"]["comparison"].setdefault(kind, {})["test"] = {task: copy.deepcopy(stats)}
        baseline = {"accuracy": 0.55} if task == "direction" else {"mae": 0.13}
        payload["metrics"]["baselines"]["test"][task] = {**baseline, "by_market": {"TWSE": baseline}}
    tables, _ = report._selected_metric_tables(payload)
    assert tables[0]["columns"] == ["樣本／指標", "模型", "基準", "可評分／原始樣本", "失敗／未評分", "基準對照"]
    assert len(tables[0]["rows"]) == 4
    text = str(tables)
    for expected in ("全體／方向準確率", "本股市場（TWSE）／報酬 MAE", "48.00%", "55.00%", "14.00 個百分點",
                     "13.00 個百分點", "987／1,000", "520／520", "5／13", "0／0", "準確率低於基準", "MAE 劣於基準"):
        assert expected in text
    for omitted in ("Brier", "有效訊號日", "股票數", "成對", "0.29", "基準分母"):
        assert omitted not in text


def test_technical_summary_uses_supplied_returns_volume_ratio_and_ma_relations(payload, content_converter):
    payload["feature_values"].extend([
        {"name": "return_20", "value": -0.0123, "unit": "比例"},
        {"name": "return_60", "value": 0.1, "unit": "ratio"},
        {"name": "volume_ratio_20", "value": 0.5236523993, "unit": "倍"},
    ])
    closes = [100] * 39 + [110] * 20 + [105]
    asof = date.fromisoformat(payload["asof"])
    payload["price_history"] = [{"date": (asof - timedelta(days=59 - i)).isoformat(), "close": close,
                                 "volume": 1000, "price_basis": "adjusted"} for i, close in enumerate(closes)]
    before = copy.deepcopy(payload)
    sections = report.build_report_sections(payload)
    technical = next(section for section in sections if section["id"] == "technical")
    rows = dict(technical["tables"][0]["rows"])
    assert rows["近 5 日報酬"] == "2.30%"
    assert rows["近 20 日報酬"] == "-1.23%"
    assert rows["近 60 日報酬"] == "10.00%"
    assert rows["20 日量比"] == "0.52 倍"
    summary = "\n".join(technical["paragraphs"])
    assert "收盤高於 60 日均線、低於 5／20 日均線" in summary
    assert "20 日量比 0.52 倍（低於 1 倍）" in summary
    for recommendation in ("買進", "賣出", "推薦", "因為", "帶動股價"):
        assert recommendation not in summary
    assert payload == before


@pytest.mark.parametrize("new_context", [False, True])
def test_new_technical_insights_replace_covered_legacy_sentence_but_keep_real_warnings(
        payload, content_converter, monkeypatch, new_context):
    payload["feature_values"].extend([
        {"name": "return_20", "value": -0.0123, "unit": "ratio"},
        {"name": "return_60", "value": 0.1, "unit": "ratio"},
        {"name": "volume_ratio_20", "value": 0.52, "unit": "times"}])
    asof = date.fromisoformat(payload["asof"])
    payload["price_history"] = [{"date": (asof - timedelta(days=59 - i)).isoformat(), "close": 100,
                                 "volume": 1000, "price_basis": "adjusted"} for i in range(60)]
    if new_context:
        payload["report_context"] = {"schema": "single-stock-report-context-v1", "stock_id": "2330", "asof": payload["asof"],
                                     "sources": [], "statuses": []}
    insights = ["收盤100，5筆均價100、20筆均價100、60筆均價100；收盤等於上述均價。",
                "既有5日報酬+2.30%、20日-1.23%、60日+10.00%，短中期方向不同。",
                "20日量比0.52倍，量能低於20日基準。", "既有20日波動指標2.01%、60日2.68%，短窗口波動較低。"]
    monkeypatch.setattr(report, "build_report_insights", lambda item: {
        "summary": [], "technical": insights, "fundamentals": [], "flows": [], "context": [], "data_notes": []})
    real_summary = report._technical_summary
    warning = "訊號日價格有衝突，未以任一筆補值。"
    def with_warning(item, view):
        rows, paragraphs = real_summary(item, view)
        return rows, [*paragraphs, warning]
    monkeypatch.setattr(report, "_technical_summary", with_warning)
    original = copy.deepcopy(payload)
    technical = next(s for s in report.build_report_sections(payload) if s["id"] == "technical")
    text = section_text([technical])
    assert text.count(warning) == 1
    assert ("近 5 日報酬 2.30%" in "\n".join(technical["paragraphs"])) is (not new_context)
    if new_context:
        assert technical["paragraphs"] == [*insights, warning]
    rows = dict(technical["tables"][0]["rows"])
    assert rows["近 20 日報酬"] == "-1.23%" and rows["20 日量比"] == "0.52 倍"
    assert technical["chart"]["close"] == [100] * 60
    assert payload == original


def test_partial_technical_insights_do_not_hide_uncovered_legacy_description(payload, content_converter, monkeypatch):
    payload["report_context"] = {"schema": "single-stock-report-context-v1", "stock_id": "2330", "asof": payload["asof"],
                                 "sources": [], "statuses": []}
    monkeypatch.setattr(report, "build_report_insights", lambda item: {
        "summary": [], "technical": ["20日波動指標2.01%，短窗口波動較低。"], "fundamentals": [],
        "flows": [], "context": [], "data_notes": []})
    technical = next(s for s in report.build_report_sections(payload) if s["id"] == "technical")
    assert "近 5 日報酬 2.30%" in "\n".join(technical["paragraphs"])


def test_technical_summary_does_not_compute_missing_features_from_prices(payload, content_converter):
    payload["feature_values"] = [
        {"name": "return_5", "value": None, "unit": "比例"},
        {"name": "return_20", "value": "NaN", "unit": "比例"},
        {"name": "return_60", "value": True, "unit": "比例"},
        {"name": "volume_ratio_20", "value": "Infinity", "unit": "倍"},
    ]
    asof = date.fromisoformat(payload["asof"])
    payload["price_history"] = [{"date": (asof - timedelta(days=69 - i)).isoformat(), "close": 100 + i,
                                 "volume": 1000, "price_basis": "adjusted"} for i in range(70)]
    technical = next(section for section in report.build_report_sections(payload) if section["id"] == "technical")
    rows = dict(technical["tables"][0]["rows"])
    assert all("近 " + str(n) + " 日報酬" not in rows for n in (5, 20, 60))
    assert "20 日量比" not in rows
    assert "收盤高於 5／20／60 日均線" in "".join(technical["paragraphs"])
    assert len(payload["price_history"]) == 70


def test_technical_conflicting_latest_close_does_not_create_ma_trend(payload, content_converter):
    payload["price_history"].append({"date": payload["asof"], "close": 99999, "volume": 1})
    text = section_text(report.build_report_sections(payload), "technical")
    assert "近 5 日報酬" in text and "2.30%" in text
    assert "收盤高於" not in text and "收盤低於" not in text and "收盤等於" not in text
    assert "99,999" not in text


def test_technical_features_must_match_stock_date_and_declared_scale(payload, content_converter):
    payload["feature_values"] = [
        {"name": "return_5", "value": 2.3, "unit": "percent"},
        {"name": "return_20", "value": 0.5, "unit": "比例", "stock_id": "2317"},
        {"name": "return_60", "value": 0.6, "unit": "比例", "asof": "2026-10-15"},
        {"name": "volume_ratio_20", "value": 1.1, "unit": "times"},
    ]
    technical = next(section for section in report.build_report_sections(payload) if section["id"] == "technical")
    rows = dict(technical["tables"][0]["rows"])
    assert rows["近 5 日報酬"] == "2.3%"
    assert "近 20 日報酬" not in rows and "近 60 日報酬" not in rows
    assert "20 日量比 1.10 倍（高於 1 倍）" in "".join(technical["paragraphs"])


def test_windows_traditional_conversion():
    if os.name != "nt":
        with pytest.raises(report.CandidateReportError, match="非 Windows"):
            report._converter()
        return
    assert report._tc("汉语转换、财务、营收、机构") == "漢語轉換、財務、營收、機構"


def test_every_visible_string_crosses_conversion(payload, monkeypatch):
    seen = []
    def convert(text):
        seen.append(text)
        return text.replace("简体", "簡體").replace("财务", "財務")
    monkeypatch.setattr(report, "_converter", lambda: convert)
    payload["stock"]["stock_name"] = "简体公司"
    payload["analysis_context"]["sources"][1]["records"][0]["origin_name"] = "财务營收"
    sections = report.build_report_sections(payload)
    text = section_text(sections)
    assert "简体" not in text and "财务" not in text
    assert "簡體公司" in text
    assert seen
    for section in sections:
        assert section["title"] in seen


def test_sections_have_candidate_boundary_and_execution_caveat(payload, content_converter):
    sections = report.build_report_sections(payload)
    assert [s["id"] for s in sections] == ["summary", "technical", "fundamentals", "flows", "context", "evaluation"]
    assert "58.00%" in section_text(sections, "summary")
    assert "-1.23%" in section_text(sections, "summary")
    assert "尚未正式採用" in section_text(sections, "summary")
    assert "收盤對收盤報酬，非實際成交報酬" in section_text(sections, "summary")
    assert "第 1 個交易日收盤" in section_text(sections, "summary")
    assert "2026-10-19" in section_text(sections, "summary")
    assert "機率與報酬皆有誤差" in section_text(sections, "summary")
    text = section_text(sections)
    for hidden in ("a" * 64, "b" * 64, "c" * 64, "candidate-model-v1", "完整限制與不確定性", "機器證據附錄"):
        assert hidden not in text


@pytest.mark.parametrize("status,expected", [
    ("research_estimate", "研究估計"),
    ("partial_research_estimate", "部分研究估計"),
])
def test_valid_engine_prediction_status_stays_machine_side(payload, content_converter, status, expected):
    payload["prediction"]["prediction_status"] = status
    sections = report.build_report_sections(payload)
    summary = next(s for s in sections if s["id"] == "summary")
    assert all(row[0] != "估計狀態與原因" for row in summary["tables"][0]["rows"])
    assert "58.00%" in section_text(sections, "summary")
    assert report._state(status) == expected
    assert report._report_machine_evidence(payload)["prediction_status"] == status


@pytest.mark.parametrize("status,expected", [
    ("uncalibrated", "未校準"),
    ("uncalibrated_insufficient_candidate_coverage", "未校準（候選校準覆蓋或方向類別不足）"),
    ("uncalibrated_calibration_failed", "未校準（校準執行失敗）"),
    ("calibrated_independent_20_days", "以獨立且足額的校準區間校準（依提供者封存紀錄）"),
])
def test_actual_engine_calibration_status_preserves_qualification(payload, content_converter, status, expected):
    payload["prediction"]["calibration_status"] = status
    sections = report.build_report_sections(payload)
    summary = next(s for s in sections if s["id"] == "summary")
    shown = next(row[1] for row in summary["tables"][0]["rows"] if row[0] == "機率校準")
    assert shown == expected
    assert "未確認" not in shown
    assert status not in shown
    if status.startswith("uncalibrated"):
        assert shown.startswith("未校準")
        assert "已校準" not in shown


def test_actual_projected_sources_not_empty_fixed_key_fallback(payload, content_converter):
    sections = report.build_report_sections(payload)
    fundamentals = section_text(sections, "fundamentals")
    assert "PER 本益比" in fundamentals and "20.5" in fundamentals
    assert "PBR 股價淨值比" in fundamentals and "4.1" in fundamentals
    assert "123,456" in fundamentals and "營業利益" in fundamentals
    assert "單位未提供，不自行推定" not in fundamentals
    assert section_text(sections).count(report._RAW_VALUE_NOTE) == 1
    assert "2026-06-30" in fundamentals
    assert "外資／買進數量" in section_text(sections, "flows")
    assert "歷史公告時點未驗證" not in section_text(sections, "flows")
    assert "model_input" not in [section["id"] for section in sections]
    assert "5,000.25" in section_text(sections, "context")
    assert "半導體產業公布營收資料" in section_text(sections, "context")
    assert "僅為來源事件摘要" in section_text(sections, "context")


def test_unknown_financial_topic_and_numeric_fields_stay_machine_side(payload, content_converter):
    payload["analysis_context"]["sources"][1]["records"].append(
        {"date": "2026-06-30", "type": "UnknownAccountingField", "value": 888, "unknown_raw_field": 999})
    text = section_text(report.build_report_sections(payload), "fundamentals")
    assert "未映射科目" not in text and "888" not in text
    assert "未映射數值欄位" not in text
    assert "UnknownAccountingField" not in text and "unknown_raw_field" not in text
    assert "缺少中文或口徑對照" not in text
    diagnostics = report._report_machine_evidence(payload)["context_diagnostics"]["fundamentals"]
    assert any("財務科目" in note and "中文或口徑對照" in note for note in diagnostics)
    assert payload["analysis_context"]["sources"][1]["records"][-1]["unknown_raw_field"] == 999


def test_context_machine_counts_stay_in_evidence_but_conflicts_and_dates_remain_visible(
        payload, content_converter, monkeypatch):
    payload["report_context"] = {"schema": "single-stock-report-context-v1", "stock_id": "2330", "asof": payload["asof"],
        "sources": [
            {"route_id": "finmind_financial_statements", "records": [
                {"date": "2026-06-30", "type": "UnknownAccountingField", "value": 888},
                {"date": "2026-10-20", "type": "EPS", "value": 999}]},
            {"route_id": "finmind_per", "records": [{"date": "2026-10-16", "PER": 20.5}, {"date": "2026-10-16", "PER": 21}]},
            {"route_id": "official_finance_reporting_deadline", "metadata_only": True, "records": [
                {"date": "2026-10-16", "stock_id": "2330", "status": "NOT_DUE", "deadline": 20261114, "period_id": 202603}]},
        ], "statuses": []}
    monkeypatch.setattr(report, "build_report_insights", lambda item: {
        "summary": [], "technical": [], "fundamentals": [], "flows": [], "context": [],
        "data_notes": ["真正缺口：本股現金流缺同口徑基期，未比較盈餘現金實現程度。"]})
    original = copy.deepcopy(payload)
    sections = report.build_report_sections(payload)
    text = section_text(sections)
    assert "缺少中文或口徑對照" not in text and "NOT_DUE" not in text and "20261114" not in text
    assert text.count("晚於資料日的觀測") == 1 and text.count("同日數值衝突") == 1
    assert text.count("本股現金流缺同口徑基期") == 1
    evidence = report._report_machine_evidence(payload)
    assert any("缺少中文或口徑對照" in note for note in evidence["context_diagnostics"]["fundamentals"])
    assert payload == original


def test_news_display_shares_normalizer_and_deduplicates_only_same_day_titles(payload, content_converter):
    title = "台積電公布2026年營收與製程進展"
    records = [
        {"date": "2026-10-15", "stock_id": "2330", "title": title + " - Yahoo股市", "publisher": "Yahoo股市",
         "published_at": "2026-10-15T12:00:00+08:00", "summary_zh": "營收增加。"},
        {"date": "2026-10-15", "stock_id": "2330", "title": "  " + title + "  ", "publisher": "Yahoo股市",
         "published_at": "2026-10-15T13:00:00+08:00", "summary_zh": "營收增加。"},
        {"date": "2026-10-15", "stock_id": "2330", "title": title + " - 鉅亨網", "publisher": "鉅亨網",
         "published_at": "2026-10-15T14:00:00+08:00", "summary_zh": "製程進度更新。"},
        {"date": "2026-10-14", "stock_id": "2330", "title": title, "publisher": "Yahoo股市",
         "published_at": "2026-10-14T12:00:00+08:00"},
        {"date": "2026-10-15", "stock_id": "2330", "title": title + " - 不明刊物", "publisher": "Yahoo股市",
         "published_at": "2026-10-15T15:00:00+08:00"},
    ]
    source = {"route_id": "news_and_hot_topics_raw", "records": records}
    original = copy.deepcopy(source)
    tables, _ = report._brief_context(payload, [source], "context")
    rows = tables[0]["rows"]
    assert len(rows) == 3
    current = next(row for row in rows if row[0] == "2026-10-15" and row[2].split("；", 1)[0] == title)
    assert current[2].count("營收增加。") == 1 and "製程進度更新。" in current[2]
    assert any(row[0] == "2026-10-14" and row[2] == title for row in rows)
    assert any(title + " - 不明刊物" in row[2] for row in rows)
    assert "同日數值衝突" not in str(rows)
    assert source == original


def test_grouped_data_notes_do_not_repeat_summary_disclosures_or_common_cautions(payload, content_converter, monkeypatch):
    payload["target_session_date"] = None
    payload["prediction"]["target_date_status"] = "unverified_future_calendar"
    payload["report_context"] = {"schema": "single-stock-report-context-v1", "stock_id": "2330", "asof": payload["asof"],
                                 "sources": copy.deepcopy(payload["analysis_context"]["sources"]), "statuses": []}
    payload["report_comparison"]["metrics"] = [{"id": "valuation_per", "source_route": "finmind_per", "current_value": 20.5,
        "unit": "倍", "項目": "本益比", "資料期間": "20261016", "本期值": "20.5（倍）", "比較": "正值歷史250筆；位置45.0%",
        "解讀": "固定警語", "資料日期": "20261016"}]
    monkeypatch.setattr(report, "build_report_insights", lambda item: {
        "summary": [], "technical": [], "fundamentals": [], "flows": [], "context": [], "data_notes": [
            "單位與比較口徑：金額與數量保留原始單位，不作跨來源換算或占比；財報期間口徑未核實，累計不當單季。",
            "真正缺口：營業現金流缺去年同口徑基期，未比較盈餘現金實現程度。",
            "時點與回測：上漲機率尚未校準，不能直接視為實際命中率；目標交易日尚未驗證，未以平日推算日期；歷史公告時點未核實，回測可能偏樂觀。"]})
    original = copy.deepcopy(payload)
    sections = report.build_report_sections(payload)
    evaluation = section_text(sections, "evaluation")
    summary = section_text(sections, "summary")
    assert summary.count("機率尚未校準") == 1 and summary.count("未來交易日曆未核實") == 1
    assert "機率尚未校準" not in evaluation and "目標交易日尚未驗證" not in evaluation
    assert "同口徑基期" in evaluation and "歷史公告時點未核實，回測可能偏樂觀" in evaluation
    assert report._RAW_VALUE_NOTE not in evaluation and report._HISTORICAL_TIMING_RISK not in evaluation
    assert "期間口徑：財報期末不是公告時間" not in evaluation
    groups = next(s for s in sections if s["id"] == "evaluation")["paragraphs"]
    assert sum(note.startswith(("單位與比較口徑：", "真正缺口：", "時點與回測：")) for note in groups) == 3
    assert "20.5（倍）" in section_text(sections) and "正值歷史250筆；位置45.0%" in section_text(sections)
    assert payload == original


def test_news_without_source_or_time_is_not_analysis(payload, content_converter):
    payload["analysis_context"]["sources"][-1]["records"] = [{"stock_id": "2330", "title": "未證實題材將帶來獲利"}]
    text = section_text(report.build_report_sections(payload), "context")
    assert "未證實題材將帶來獲利" not in text
    assert text.count("缺中文標題、來源或發布日期") == 1
    evidence = report._report_machine_evidence(payload)
    assert "缺中文標題、來源或發布日期" in str(evidence["context_diagnostics"]["context"])


def test_missing_not_selected_and_legitimate_empty_separate(payload, content_converter):
    tables = {t["title"]: t for t in report._source_tables(payload)}
    assert len(tables["未選用來源（不算缺失）"]["rows"]) == 1
    assert "券商分點交易" in tables["未選用來源（不算缺失）"]["rows"][0][0]
    assert len(tables["選中但缺失或可得性未知"]["rows"]) == 1
    assert "資產負債表" in tables["選中但缺失或可得性未知"]["rows"][0][0]
    assert len(tables["合法無資料與不適用（保留證據限制）"]["rows"]) == 1
    text = str(tables)
    assert "not-selected" not in text and "legitimate_empty" not in text


def test_legitimate_empty_without_evidence_does_not_claim_verified(payload, content_converter):
    del payload["source_manifest"][-1]["empty_evidence"]
    text = str(report._source_tables(payload))
    assert "未取得正向證據細節，不視為已驗證" in text


def test_lagged_date_is_not_pit(payload, content_converter):
    payload["source_manifest"][0]["available_time_basis"] = "lagged-date"
    text = str(report._source_tables(payload))
    assert "延後交易日的日期代理" in text
    assert "歷史公告時點未驗證" in text
    assert "不是歷史時點可得性已驗證" in text


@pytest.mark.parametrize("state", ["fit_all_missing", "fit_constant"])
def test_inference_only_long_feature_not_claimed_learned(payload, content_converter, state):
    feature = copy.deepcopy(payload["feature_values"][0])
    feature.update(name="return_252", label_zh="二百五十二日報酬", value=0.3, selected=False)
    feature["selected_by_task"] = {"direction": {"selected": False, "numeric": False, "missing_indicator": False}}
    feature["feature_validity"]["direction"].update(state=state, numeric_effective=False, n_fit_finite=0 if state == "fit_all_missing" else 510)
    payload["feature_values"].append(feature)
    actual, other = report._feature_rows(payload)
    assert not any("二百五十二" in row[0] for row in actual)
    assert any("二百五十二" in row[0] for row in other)
    assert "不能當成訓練時已學會" in str(other)
    assert "二百五十二" not in section_text(report.build_report_sections(payload))


def test_missing_indicator_role_not_numeric_causality(payload, content_converter):
    feature = payload["feature_values"][0]
    feature["selected_by_task"]["direction"].update(numeric=False, missing_indicator=True)
    feature["feature_validity"]["direction"].update(state="fit_constant", numeric_effective=False, missing_indicator_effective=True)
    text = str(report._feature_rows(payload))
    assert "僅有效缺值指示輸入" in text
    assert "訓練有限值 500／510" in text
    assert "僅有效缺值指示輸入" not in section_text(report.build_report_sections(payload))


def test_no_validity_evidence_no_learning_claim(payload, content_converter):
    payload["feature_values"][0].pop("feature_validity")
    payload["feature_values"][0].pop("selected_by_task")
    adopted, other = report._feature_rows(payload)
    assert not adopted and len(other) == 1
    assert "不宣稱已參與學習" in str(other)
    assert "五日還原收盤報酬" not in section_text(report.build_report_sections(payload))


@pytest.mark.parametrize("kind", [None, "fit_up_rate_baseline", "zero_return_baseline"])
def test_baseline_or_unselected_head_never_claimed_model(payload, content_converter, kind):
    payload["prediction"]["selected_direction_kind"] = kind
    summary = section_text(report.build_report_sections(payload), "summary")
    assert "58.00%" not in summary
    assert "分支未確認，或僅為比較基準" in summary


def test_missing_model_binding_no_prediction_falseclaim(payload, content_converter):
    payload["model_binding"] = {}
    summary = section_text(report.build_report_sections(payload), "summary")
    assert "58.00%" not in summary and "-1.23%" not in summary
    assert "模型綁定或分支未確認" in summary


def test_nested_model_metrics_baselines_and_degradation_retained(payload, content_converter):
    text = section_text(report.build_report_sections(payload), "evaluation")
    for value in ("48.00%", "200／210", "機率誤差劣於基準"):
        assert value in text
    assert "51.00%" not in text  # Selection scores are not finaltest results.
    for detail in ("Brier", "0.29", "0.25", "0.04"):
        assert detail not in text
    assert payload["metrics"]["holdout"]["expanded_linear"]["direction"]["brier"] == 0.29
    assert payload["metrics"]["paired_differences"]["test"]["direction"]["model_minus_baseline"] == 0.04
    assert "不是本股未來準確率" not in text
    assert "不是本股未來準確率" in report._report_machine_evidence(payload)["interpretation_notes"]["evaluation"]
    assert "報酬分支未提供選中模型的最終保留測試結果" in text


def test_long_names_preserved_but_full_financial_ledger_stays_in_payload(payload, content_converter):
    long_name = "台灣半導體與精密製造股份有限公司" * 20
    long_sentence = "這是有來源但尚未驗證公告時點的完整長句，應換行而不能裁切。" * 100
    payload["stock"]["stock_name"] = long_name
    payload["limitations"].append(long_sentence)
    records = payload["analysis_context"]["sources"][1]["records"]
    records[:] = [{"date": "2026-06-30", "type": "Revenue", "value": i, "unit": "單位待來源確認"} for i in range(300)]
    sections = report.build_report_sections(payload)
    assert long_name in section_text(sections, "summary")
    assert long_sentence not in section_text(sections)
    assert payload["limitations"][-1] == long_sentence
    finance = next(s for s in sections if s["id"] == "fundamentals")
    assert len(finance["tables"][0]["rows"]) == 4  # PER/PBR/yield plus one conflicting revenue observation.
    assert "同日數值衝突" in section_text([finance])
    assert len(records) == 300


def test_18_month_chart_real_data_no_future_or_conflict_fill(payload, content_converter):
    start = date(2025, 1, 1)
    payload["price_history"] = [{"date": (start + timedelta(days=i)).isoformat(), "close": 100 + i, "volume": 1000,
                                 "price_basis": "adjusted"} for i in range(700)]
    payload["price_history"].append({"date": "2026-10-16", "close": 99999, "volume": 1})
    view = report._price_view(payload)
    assert min(view["dates"]) == "2025-04-16"
    assert max(view["dates"]) == "2026-10-16"
    assert view["close"][-1] is None  # Conflicting same-day values are not selected arbitrarily.
    assert view["averages"]["5"][-1] is None
    assert view["volume_label"] == "成交量"
    assert "單位未提供" in view["volume_unit"]
    assert "不補造" in "".join(view["notes"])


def test_english_machine_reasons_not_visible_and_input_unchanged(payload, content_converter):
    payload["prediction"]["reason_zh"] = "raw error underlying encoder model feature failed"
    payload["source_manifest"][0]["reason_zh"] = "來源失敗 current_contract_physical_claim_changed"
    original = copy.deepcopy(payload)
    text = section_text(report.build_report_sections(payload))
    assert "raw error" not in text
    assert "current_contract_physical_claim_changed" not in text
    assert payload == original


@pytest.mark.parametrize("key,value", [("language", "zh-CN"), ("research_only", False), ("promotion", True), ("schema", "other")])
def test_boundary_fail_closed(payload, key, value):
    payload[key] = value
    with pytest.raises(report.CandidateReportError):
        report.build_report_sections(payload)


def test_unknown_glyph_rejected_without_font_fallback_lie():
    font = SimpleNamespace(face=SimpleNamespace(charToGlyph={ord("中"): 1, ord(" "): 2}))
    fonts = report._Fonts([("fake", font, False)])
    fonts.check(["中 中"])
    with pytest.raises(report.CandidateReportError) as caught:
        fonts.check(["中\U0010ffff"])
    assert caught.value.code == "unsupported_glyph"
    assert caught.value.details["codepoints"] == ["U+10FFFF"]


def test_font_fallback_runs_are_explicit():
    a = SimpleNamespace(face=SimpleNamespace(charToGlyph={ord("中"): 1}))
    b = SimpleNamespace(face=SimpleNamespace(charToGlyph={ord("文"): 1}))
    fonts = report._Fonts([("primary", a, False), ("fallback", b, False)])
    assert list(fonts.runs("中文中")) == [("primary", "中"), ("fallback", "文"), ("primary", "中")]


def test_real_windows_font_preflight_without_pdf(payload):
    if os.name != "nt":
        pytest.skip("Windows 字型與轉繁 API 預檢")
    pytest.importorskip("reportlab")
    sections = report.build_report_sections(payload)
    fonts = report._load_fonts()
    fonts.check(report._visible_strings(sections))
    assert fonts.faces
    assert all(font.face.charToGlyph.get(ord("繁"), 0) for _, font, _ in fonts.faces)


@pytest.mark.parametrize("formal", [False, True])
def test_long_table_layout_configuration_without_pdf(payload, formal_payload, content_converter, monkeypatch, tmp_path, formal):
    pytest.importorskip("reportlab")
    from reportlab.platypus import CondPageBreak, LongTable, Paragraph, SimpleDocTemplate
    payload = formal_payload if formal else payload
    records = payload["analysis_context"]["sources"][1]["records"]
    records[:] = [{"date": "2026-06-30", "type": "Revenue", "value": i} for i in range(300)]
    sections = report.build_report_sections(payload)
    font = SimpleNamespace(primary="Helvetica", runs=lambda text, bold=False: iter([("Helvetica", str(text))]))
    captured = {}
    def capture(document, story, **kwargs):
        captured.update(document=document, story=story, callbacks=kwargs)
    monkeypatch.setattr(SimpleDocTemplate, "build", capture)
    monkeypatch.setattr(report, "_chart", lambda *args: None)
    report._render_pdf(tmp_path / "not_created.pdf", payload, sections, font)
    tables = [item for item in captured["story"] if isinstance(item, LongTable)]
    assert all(len(table._cellvalues) < 40 for table in tables)
    assert all(table.repeatRows == 1 and table.splitInRow == 1 for table in tables)
    assert captured["document"].bottomMargin == 48
    assert captured["document"].title == report._report_title(payload)
    assert captured["document"].leftMargin + 499 + captured["document"].rightMargin == 595
    assert "onFirstPage" in captured["callbacks"] and "onLaterPages" in captured["callbacks"]
    breaks = [item for item in captured["story"] if isinstance(item, CondPageBreak)]
    assert breaks and all(0 < item.height <= 756 for item in breaks)
    assert any(item.height > 74 for item in breaks)
    evaluation_index = next(index for index, item in enumerate(captured["story"])
                            if isinstance(item, Paragraph) and item.getPlainText() == sections[-1]["title"])
    assert isinstance(captured["story"][evaluation_index - 1], CondPageBreak)
    first_table = next(index for index in range(evaluation_index + 1, len(captured["story"]))
                       if isinstance(captured["story"][index], LongTable))
    assert not any(isinstance(item, CondPageBreak) for item in captured["story"][evaluation_index + 1:first_table])
    assert all(not item.getKeepWithNext() for item in captured["story"] if isinstance(item, Paragraph))
    assert not (tmp_path / "not_created.pdf").exists()


def test_insight_paragraphs_reserve_heading_and_first_row_without_creating_pdf(
        payload, content_converter, monkeypatch, tmp_path):
    pytest.importorskip("reportlab")
    from reportlab.platypus import CondPageBreak, LongTable, Paragraph, SimpleDocTemplate
    payload["report_context"] = {"schema": "single-stock-report-context-v1", "stock_id": "2330",
        "asof": payload["asof"], "sources": copy.deepcopy(payload["analysis_context"]["sources"]), "statuses": []}
    explanation = "外資近五日轉為買超，但近二十日仍為賣超，短期回補尚未改變較長期間的賣超格局。"
    monkeypatch.setattr(report, "build_report_insights", lambda item: {
        "summary": [], "technical": [], "fundamentals": [], "flows": [explanation] * 3,
        "context": [], "data_notes": []})
    sections = report.build_report_sections(payload)
    captured = {}
    monkeypatch.setattr(SimpleDocTemplate, "build", lambda document, story, **kwargs: captured.update(story=story))
    monkeypatch.setattr(report, "_chart", lambda *args: None)
    font = SimpleNamespace(primary="Helvetica", runs=lambda text, bold=False: iter([("Helvetica", str(text))]))
    target = tmp_path / "not_created.pdf"
    report._render_pdf(target, payload, sections, font)
    story = captured["story"]
    heading = next(i for i, item in enumerate(story) if isinstance(item, Paragraph)
                   and item.getPlainText() == next(s["title"] for s in sections if s["id"] == "flows"))
    first_table = next(i for i in range(heading + 1, len(story)) if isinstance(story[i], LongTable))
    paragraphs = [item for item in story[heading:first_table] if isinstance(item, Paragraph)]
    required_text_height = sum(item.wrap(487, 10000)[1] + item.getSpaceAfter() for item in paragraphs)
    reservation = story[heading - 1]
    assert isinstance(reservation, CondPageBreak)
    assert reservation.height >= required_text_height + 12
    assert all(item.style.leading >= item.style.fontSize + 3 for item in paragraphs)
    assert not target.exists()


def test_validator_never_returns_empty_success(payload, content_converter, monkeypatch, tmp_path):
    monkeypatch.setattr(report, "_load_fonts", lambda: SimpleNamespace(check=lambda texts: None))
    result = report.validate_candidate_report(payload, tmp_path / "absent.pdf")
    assert result["valid"] is False and result["errors"]
    assert result["deliverable"] is False


def test_validator_no_optional_pdf_parser_requirement(payload, content_converter, monkeypatch, tmp_path):
    sections = report.build_report_sections(payload)
    path = tmp_path / "structural_fixture.bin"
    path.write_bytes(b"structural fixture, not an authored PDF")
    sidecar = {"schema": "single-stock-candidate-report-bundle-v1", "payload": payload, "sections": sections, "payload_sha256": report._digest(payload),
               "sections_sha256": report._digest(sections), "pdf": report._file_info(path)}
    path.with_suffix(".json").write_text(json.dumps(sidecar, ensure_ascii=False), encoding="utf-8")
    monkeypatch.setattr(report, "_load_fonts", lambda: SimpleNamespace(check=lambda texts: None))
    monkeypatch.setattr(report, "_inspect_structure", lambda *args: {"pages": 1, "embedded_truetype_subsets": 1})
    monkeypatch.setattr(report.importlib.util, "find_spec", lambda name: None)
    result = report.validate_candidate_report(payload, path)
    assert result["valid"] is True
    assert result["text_extraction"]["status"] == "pending_external"
    assert result["render_status"] == "pending_external" and result["deliverable"] is False
    sidecar["payload"] = copy.deepcopy(payload)
    sidecar["payload"]["stock"]["stock_id"] = "9999"
    path.with_suffix(".json").write_text(json.dumps(sidecar, ensure_ascii=False), encoding="utf-8")
    assert report.validate_candidate_report(payload, path)["valid"] is False


def test_truncated_pdf_structure_rejected(tmp_path):
    path = tmp_path / "truncated_fixture.bin"
    path.write_bytes(b"%PDF-1.4\n")
    with pytest.raises(report.CandidateReportError) as caught:
        report._inspect_structure(path, [])
    assert caught.value.code == "pdf_incomplete"


def test_writer_stops_before_publication_on_glyph_failure(payload, content_converter, monkeypatch, tmp_path):
    def fail(texts):
        raise report.CandidateReportError("unsupported_glyph", "測試字型不支援，停止產出。")
    monkeypatch.setattr(report, "_load_fonts", lambda: SimpleNamespace(check=fail))
    target = tmp_path / "must_not_exist"
    with pytest.raises(report.CandidateReportError):
        report.write_candidate_report(payload, target)
    assert not target.exists()


def test_actual_hgb_kind_and_central_wide_fields(payload, content_converter):
    payload["prediction"]["selected_return_kind"] = "hist_gradient_boosting"
    payload["analysis_context"]["sources"] += [
        {"route_id": "finmind_margin", "usage": "used", "records": [{"date": "2026-10-16",
            "margin_purchase_balance": 1234, "margin_purchase_change": -34, "short_sale_balance": 567,
            "short_sale_change": 8, "volume_unit": "lots"}]},
        {"route_id": "finmind_day_trading", "usage": "used", "records": [{"date": "2026-10-16", "day_trade_volume": 9876}]},
        {"route_id": "finmind_shareholding", "usage": "context-only", "records": [{"date": "2026-10-16", "holding_percentage": 0.25,
            "NumberOfSharesIssued": 10000, "units": {"holding_percentage": "fraction", "NumberOfSharesIssued": "shares"}}]},
        {"route_id": "finmind_government_bank", "usage": "context-only", "records": [{"date": "2026-10-16", "BuyAmount": 100, "SellAmount": 80,
            "currency_unit": "TWD"}]},
        {"route_id": "finmind_stock_info", "usage": "context-only", "records": [{"stock_name": "公司當期背景", "market": "TWSE"}]},
    ]
    row = payload["analysis_context"]["sources"][2]["records"][0]
    row.update(foreign_investors_net=500, investment_trust_net=30, dealer_net=-20, total_net=510,
               units={"foreign_investors_net": "shares", "investment_trust_net": "shares", "dealer_net": "shares", "total_net": "shares"})
    sections = report.build_report_sections(payload)
    assert "擴充特徵直方圖梯度提升模型" in section_text(sections, "summary")
    flows = section_text(sections, "flows")
    for label in ("外資淨買賣數量", "投信淨買賣數量", "自營商淨買賣數量", "法人合計淨買賣數量",
                  "融資餘額", "融資餘額變動", "融券餘額", "融券餘額變動", "當沖成交量", "買進金額", "賣出金額",
                  "持股比例", "已發行股數"):
        assert label in flows
    for value in ("1,234（張）", "500（股）", "0.25（比例）", "100（新臺幣元）", "9,876"):
        assert value in flows
    assert "公司當期背景" not in section_text(sections, "fundamentals")
    assert "期間未提供" not in section_text(sections)
    assert "單位未提供" not in flows


@pytest.fixture
def descriptor_payload(payload, tmp_path):
    payload = copy.deepcopy(payload)
    descriptor = {"objects": [{"path": "發布物件", "sha256": "d" * 64}],
                  "full_evidence": "完整證據內容不裁切" * 4096}
    payload["snapshot_binding"].update(descriptor=descriptor,
        query_predicates={"stock_id": "1203", "asof": "2026-10-16"},
        source_gaps=[{"route_id": "finmind_balance_sheet", "status": "missing"}])
    model = {"schema": payload["model_binding"]["schema"], "metadata": {
        "feature_schema": payload["model_binding"]["feature_schema"],
        "snapshot_binding": {"head_sha256": payload["snapshot_binding"]["head_sha256"], "descriptor": descriptor}}}
    path = tmp_path / "candidate_model.json"
    path.write_bytes(report._json_bytes(model))
    payload["model_binding"].update(path=str(path), sha256=report._file_info(path)["sha256"])
    payload["runtime_payload_sha256"] = report._digest(payload)
    return payload, path


def _compact_sidecar_fixture(descriptor_payload, tmp_path, monkeypatch):
    """Unit fixture only: mocks PDF inspection, and never authors a PDF."""
    payload, model_path = descriptor_payload
    output = tmp_path / "reports_compact"
    stored, original_digest, reference = report._prepare_report_payload(payload, output)
    folder = output / "fixture_bundle"
    folder.mkdir(parents=True)
    path = folder / "integrity_fixture.bin"
    path.write_bytes(b"Not a PDF; reference and payload binding unit fixture only.")
    sections = report.build_report_sections(payload)
    data = {"schema": report._BUNDLE_V2, "payload": stored, "sections": sections,
            "payload_sha256": original_digest, "original_payload_sha256": original_digest,
            "stored_payload_sha256": report._digest(stored), "runtime_payload_sha256": payload["runtime_payload_sha256"],
            "sections_sha256": report._digest(sections), "pdf": report._file_info(path)}
    path.with_suffix(".json").write_bytes(report._json_bytes(data))
    monkeypatch.setattr(report, "_load_fonts", lambda: SimpleNamespace(check=lambda texts: None))
    monkeypatch.setattr(report, "_inspect_structure", lambda *args: {"pages": 1, "embedded_truetype_subsets": 1})
    monkeypatch.setattr(report, "_extract_if_available", lambda *args: {"status": "pending_external"})
    return payload, stored, reference, path, data, model_path


def test_model_descriptor_reference_complete_and_visible_content_unchanged(descriptor_payload, tmp_path, content_converter):
    payload, model_path = descriptor_payload
    original = copy.deepcopy(payload)
    output = tmp_path / "reports"
    stored, original_digest, reference = report._prepare_report_payload(payload, output)
    assert reference == {"schema": report._REFERENCE_SCHEMA, "kind": "model_json", "path": str(model_path.resolve()),
        **report._file_info(model_path), "container_schema": payload["model_binding"]["schema"],
        "json_pointer": "/metadata/snapshot_binding/descriptor", "head_sha256": payload["snapshot_binding"]["head_sha256"],
        "descriptor_sha256": report._digest(payload["snapshot_binding"]["descriptor"])}
    assert "descriptor" not in stored["snapshot_binding"]
    descriptor = report._resolve_descriptor_reference(reference, stored)
    assert descriptor == payload["snapshot_binding"]["descriptor"]
    assert report._restore_descriptor(stored, descriptor) == original
    assert original_digest == report._digest(original)
    assert stored["runtime_payload_sha256"] == original["runtime_payload_sha256"]
    assert stored["snapshot_binding"]["query_predicates"] == original["snapshot_binding"]["query_predicates"]
    assert stored["snapshot_binding"]["source_gaps"] == original["snapshot_binding"]["source_gaps"]
    for key in ("asof", "metrics", "prediction", "feature_values", "source_manifest", "analysis_context", "limitations"):
        assert stored[key] == original[key]
    assert report.build_report_sections(stored) == report.build_report_sections(original)
    assert payload == original
    assert not output.exists()  # Reusing the model creates no descriptor copy.


def test_h1_h63_reference_same_model_without_evidence_copy(descriptor_payload, tmp_path):
    payload, model_path = descriptor_payload
    before = model_path.read_bytes()
    refs = []
    for horizon in (1, 63):
        item = {**payload, "horizon_sessions": horizon}
        stored, _, reference = report._prepare_report_payload(item, tmp_path / "reports" / ("H" + str(horizon)))
        refs.append(reference)
        assert "descriptor" not in stored["snapshot_binding"]
        assert len(report._json_bytes(stored)) < len(report._json_bytes(item))
    assert refs[0] == refs[1]
    assert not (tmp_path / "reports").exists()
    assert model_path.read_bytes() == before


def test_original_and_compact_payload_validate_and_receipt_has_no_full_snapshot(descriptor_payload, tmp_path, content_converter, monkeypatch):
    payload, stored, reference, path, _, _ = _compact_sidecar_fixture(descriptor_payload, tmp_path, monkeypatch)
    full_checked = report.validate_candidate_report(payload, path)
    compact_checked = report.validate_candidate_report(stored, path)
    assert full_checked["valid"] is True and compact_checked["valid"] is True
    assert full_checked["payload_sha256"] == compact_checked["payload_sha256"] == report._digest(payload)
    assert full_checked["runtime_payload_sha256"] == payload["runtime_payload_sha256"]
    assert full_checked["evidence"]["descriptor_reference"] == reference
    assert "snapshot_binding" not in full_checked["evidence"]
    receipt = {"result": {"validation": full_checked}, "validation": compact_checked}
    raw = report._json_bytes(receipt)
    assert "完整證據內容不裁切".encode("utf-8") not in raw
    assert len(raw) < len(report._json_bytes(payload["snapshot_binding"]["descriptor"]))


@pytest.mark.parametrize("failure", ["missing_file", "file_sha", "reference_sha", "size", "descriptor_digest", "head", "pointer", "schema"])
def test_reference_missing_tampered_or_misbound_is_invalid(descriptor_payload, tmp_path, content_converter, monkeypatch, failure):
    payload, stored, reference, path, data, model_path = _compact_sidecar_fixture(descriptor_payload, tmp_path, monkeypatch)
    if failure == "missing_file":
        model_path.unlink()
    elif failure == "file_sha":
        model_path.write_bytes(model_path.read_bytes() + b"\n")
    elif failure == "reference_sha":
        reference["sha256"] = "0" * 64
    elif failure == "size":
        reference["size"] += 1
    elif failure == "descriptor_digest":
        reference["descriptor_sha256"] = "0" * 64
    elif failure == "head":
        reference["head_sha256"] = "0" * 64
    elif failure == "pointer":
        reference["json_pointer"] = "/metadata/missing"
    elif failure == "schema":
        reference["container_schema"] = "incorrect-container"
    data["stored_payload_sha256"] = report._digest(stored)
    path.with_suffix(".json").write_bytes(report._json_bytes(data))
    checked = report.validate_candidate_report(payload, path)
    assert checked["valid"] is False and checked["errors"]
    assert checked["errors"][0]["code"].startswith("evidence_")


def test_bad_model_sha_never_falls_back_to_new_evidence(descriptor_payload, tmp_path):
    payload, _ = descriptor_payload
    payload["model_binding"]["sha256"] = "0" * 64
    output = tmp_path / "must_not_write"
    with pytest.raises(report.CandidateReportError) as caught:
        report._prepare_report_payload(payload, output)
    assert caught.value.code == "evidence_sha256_mismatch"
    assert not output.exists()


def test_different_current_descriptor_preserved_once_inside_output_dir(descriptor_payload, tmp_path, content_converter):
    payload, model_path = descriptor_payload
    model_before = model_path.read_bytes()
    payload["snapshot_binding"]["descriptor"] = {"current_evidence": ["完整當期資料", {"gap": "缺口保留"}]}
    payload["snapshot_binding"]["head_sha256"] = "e" * 64
    output = tmp_path / "reports_compact"
    stored, original_digest, reference = report._prepare_report_payload(payload, output)
    assert reference["kind"] == "report_evidence_json"
    assert Path(reference["path"]).is_relative_to(output.resolve())
    assert report._resolve_descriptor_reference(reference, stored) == payload["snapshot_binding"]["descriptor"]
    assert original_digest == report._digest(payload)
    assert report.build_report_sections(stored) == report.build_report_sections(payload)
    _, _, repeated = report._prepare_report_payload(payload, output)
    assert repeated == reference
    assert len(list((output / "evidence").glob("*.json"))) == 1
    assert model_path.read_bytes() == model_before
    evidence = json.loads(Path(reference["path"]).read_text(encoding="utf-8"))
    assert evidence["descriptor"] == payload["snapshot_binding"]["descriptor"]
    Path(reference["path"]).unlink()
    with pytest.raises(report.CandidateReportError) as caught:
        report._resolve_descriptor_reference(reference, stored)
    assert caught.value.code == "evidence_read_failed"


def test_fallback_evidence_reference_cannot_escape_output(descriptor_payload, tmp_path):
    payload, _ = descriptor_payload
    payload["snapshot_binding"]["descriptor"] = {"different": True}
    stored, _, reference = report._prepare_report_payload(payload, tmp_path / "reports_compact")
    reference["path"] = str(tmp_path / "outside.json")
    with pytest.raises(report.CandidateReportError) as caught:
        report._resolve_descriptor_reference(reference, stored)
    assert caught.value.code == "evidence_output_escape"


def test_v1_old_sections_require_revalidation_not_template_reconstruction(payload, content_converter, monkeypatch, tmp_path):
    sections = report.build_report_sections(payload)
    path = tmp_path / "legacy_fixture.bin"
    path.write_bytes(b"Existing legacy artifact must stay unchanged.")
    sections[5]["tables"] = [{"title": "舊版逐項直列", "columns": ["項目", "值"], "rows": [["舊指標", "0.5"]], "widths": None}]
    data = {"schema": report._BUNDLE_V1, "payload": payload, "payload_sha256": report._digest(payload),
            "sections": sections, "sections_sha256": report._digest(sections), "pdf": report._file_info(path)}
    path.with_suffix(".json").write_bytes(report._json_bytes(data))
    before = path.read_bytes()
    checked = report.validate_candidate_report(payload, path)
    assert checked["valid"] is False
    assert checked["errors"][0]["code"] == "legacy_render_requires_revalidation"
    assert path.read_bytes() == before


def test_horizontal_metrics_deduplicate_same_holdout_and_keep_market_failures(payload, content_converter):
    metrics = payload["metrics"]
    stats = {"accuracy": 0.48, "balanced_accuracy": 0.45, "brier": 0.29, "log_loss": 0.71, "auc": 0.43,
             "n_denominator": 210, "n_labeled": 205, "n_evaluated": 200, "n_predicted": 202, "n_failed": 8,
             "n_unscored": 10, "coverage": 202 / 210, "evaluation_coverage": 200 / 210,
             "n_signal_days": 22, "n_effective_signal_days": 20, "n_stocks": 10,
             "class_counts": {"up": 90, "not_up": 110}, "reason_zh": "缺估計與失敗均保留。"}
    stats["by_market"] = {"TWSE": {**stats, "reason_zh": "上市市場失敗保留。"}}
    branches = {"direction": stats, "return": {**stats, "mae": 0.03, "rmse": 0.04}}
    metrics["comparison"]["expanded_linear"]["test"] = copy.deepcopy(branches)
    metrics["holdout"]["expanded_linear"] = copy.deepcopy(branches)
    before = copy.deepcopy(metrics)
    tables = report._metric_tables(metrics)
    direction = next(table for table in tables if table["title"].startswith("候選比較") and table["title"].endswith("方向指標"))
    holdout_rows = [row for row in direction["rows"] if "保留測試" in row[0]]
    assert len(holdout_rows) == 2  # Aggregate and TWSE, not comparison/test plus holdout again.
    assert all(len(row) == len(direction["columns"]) == 8 for row in direction["rows"])
    text = "\n".join("\n".join(row) for row in holdout_rows)
    for item in ("48.00%", "45.00%", "0.29", "0.71", "0.43", "210／205／200", "202／8／10", "上市市場失敗保留"):
        assert item in text
    assert any("MAE" in "".join(table["columns"]) for table in tables)
    assert "是（模型劣化保留）" in str(tables)
    assert metrics == before


def test_conflicting_holdout_metrics_are_not_silently_discarded(payload, content_converter):
    tables = report._metric_tables(payload["metrics"])
    scores = next(table for table in tables if table["title"].startswith("候選比較") and table["title"].endswith("方向指標"))
    assert any("候選紀錄" in row[0] for row in scores["rows"])
    assert any("彙整紀錄，與候選不同" in row[0] for row in scores["rows"])


def test_identical_feature_training_details_merge_but_differences_remain(payload, content_converter):
    feature = payload["feature_values"][0]
    feature["selected_by_task"]["return"] = copy.deepcopy(feature["selected_by_task"]["direction"])
    feature["feature_validity"]["return"] = copy.deepcopy(feature["feature_validity"]["direction"])
    feature["coverage"]["training_by_task"]["return"] = copy.deepcopy(feature["coverage"]["training_by_task"]["direction"])
    adopted, _ = report._feature_rows(payload)
    text = adopted[0][3]
    assert "方向分支／報酬分支" in text
    assert text.count("訓練有效 500／510") == 1
    assert text.count("訓練有限值 500／510") == 1
    assert adopted[0][0] == feature["label_zh"]
    assert "0.023（比例）" == adopted[0][1]
    assert feature["calculation_zh"] in adopted[0][2] and "還原股價" in adopted[0][2]
    feature["feature_validity"]["return"]["n_fit_finite"] = 400
    adopted, _ = report._feature_rows(payload)
    assert "訓練有效 500／510" in adopted[0][3] and "訓練有效 400／510" in adopted[0][3]
    assert "方向分支／報酬分支" not in adopted[0][3]


@pytest.fixture
def stable_json_evidence(tmp_path):
    path = tmp_path / "stable_evidence.json"
    path.write_bytes(b'{"schema":"stable-evidence-test-v1","value":123}')
    return path, report._file_info(path)


def test_stable_json_reopens_path_with_same_handle_stat_api(stable_json_evidence, monkeypatch):
    path, info = stable_json_evidence
    actual_fstat, observed = report.os.fstat, []
    def record(fd):
        result = actual_fstat(fd)
        observed.append((fd, result.st_dev, result.st_ino, result.st_size, result.st_mtime_ns, result.st_ctime_ns))
        return result
    monkeypatch.setattr(report.os, "fstat", record)
    document, verified = report._stable_json(path, sha256=info["sha256"], size=info["size"], schema="stable-evidence-test-v1")
    assert document == {"schema": "stable-evidence-test-v1", "value": 123}
    assert verified["sha256"] == info["sha256"] and verified["size"] == info["size"]
    assert len(observed) == 4
    assert observed[0][0] == observed[1][0] == observed[3][0]
    assert observed[2][0] != observed[0][0]
    assert all(item[1:] == observed[0][1:] for item in observed)


@pytest.fixture
def evidence_parses(monkeypatch):
    actual_loads, parsed = report.json.loads, []
    def observe(raw, *args, **kwargs):
        if kwargs.get("parse_constant") is report._reject_json_constant:
            parsed.append(raw)
        return actual_loads(raw, *args, **kwargs)
    monkeypatch.setattr(report.json, "loads", observe)
    return parsed


def test_parsed_evidence_reuse_keeps_every_read_identity_hash_and_info(stable_json_evidence, monkeypatch, evidence_parses):
    path, info = stable_json_evidence
    actual_fstat, actual_sha256 = report.os.fstat, report.hashlib.sha256
    stats, hashes = [], []
    def observe_stat(fd):
        stats.append(fd)
        return actual_fstat(fd)
    def observe_hash(raw=b"", *args, **kwargs):
        hashes.append(raw)
        return actual_sha256(raw, *args, **kwargs)
    monkeypatch.setattr(report.os, "fstat", observe_stat)
    monkeypatch.setattr(report.hashlib, "sha256", observe_hash)
    with report._parsed_evidence_operation():
        first, first_info = report._stable_json(path, **info, schema="stable-evidence-test-v1")
        second, second_info = report._stable_json(path, **info, schema="stable-evidence-test-v1")
        assert first is second  # Private read-only consumers, not a public payload.
    assert first_info == second_info == {"path": str(path.resolve()), **info}
    assert len(evidence_parses) == 1 and len(stats) == 8
    assert hashes == [path.read_bytes()] * 2
    assert report._PARSED_EVIDENCE.get() is None


def test_parsed_evidence_scope_is_private_nested_and_cleared_on_failure(stable_json_evidence, evidence_parses):
    path, info = stable_json_evidence
    outside, _ = report._stable_json(path, **info)
    outside["value"] = "caller mutation"
    with report._parsed_evidence_operation():
        outer_cache = report._PARSED_EVIDENCE.get()
        outer, _ = report._stable_json(path, **info)
        assert outer["value"] == 123
        with pytest.raises(RuntimeError, match="fixture failure"):
            with report._parsed_evidence_operation():
                inner_cache = report._PARSED_EVIDENCE.get()
                inner, _ = report._stable_json(path, **info)
                assert inner == outer and inner is not outer
                raise RuntimeError("fixture failure")
        assert inner_cache == {} and report._PARSED_EVIDENCE.get() is outer_cache
        again, _ = report._stable_json(path, **info)
        assert again is outer
    assert outer_cache == {} and report._PARSED_EVIDENCE.get() is None
    with report._parsed_evidence_operation():
        next_job, _ = report._stable_json(path, **info)
        assert next_job == outer and next_job is not outer
    assert len(evidence_parses) == 4


@pytest.mark.parametrize("bound", [False, True])
def test_parsed_evidence_changed_bytes_with_same_size_and_mtime_never_hit(stable_json_evidence, evidence_parses, bound):
    path, info = stable_json_evidence
    before = path.stat()
    with report._parsed_evidence_operation():
        original, _ = report._stable_json(path, **info)
        changed = path.read_bytes().replace(b"123", b"456")
        path.write_bytes(changed)
        os.utime(path, ns=(before.st_atime_ns, before.st_mtime_ns))
        assert path.stat().st_size == info["size"] and path.stat().st_mtime_ns == before.st_mtime_ns
        if bound:
            with pytest.raises(report.CandidateReportError) as caught:
                report._stable_json(path, **info)
            assert caught.value.code == "evidence_sha256_mismatch"
        else:
            updated, updated_info = report._stable_json(path, size=info["size"])
            assert updated["value"] == 456 and updated is not original
            assert updated_info["sha256"] != info["sha256"]
            assert len(report._PARSED_EVIDENCE.get()) == 1
    assert len(evidence_parses) == (1 if bound else 2)


@pytest.mark.parametrize("raw,code", [
    (b'{"schema":', "evidence_json_invalid"),
    (b'{"schema":"stable-evidence-test-v1","value":NaN}', "evidence_json_invalid"),
    (b'[]', "evidence_schema_mismatch"),
    (b'\xff', "evidence_json_invalid"),
])
def test_parsed_evidence_invalid_json_is_never_cached(stable_json_evidence, raw, code):
    path, _ = stable_json_evidence
    path.write_bytes(raw)
    with report._parsed_evidence_operation():
        for _ in range(2):
            with pytest.raises(report.CandidateReportError) as caught:
                report._stable_json(path)
            assert caught.value.code == code
            assert report._PARSED_EVIDENCE.get() == {}


@pytest.mark.parametrize("primed", [False, True])
@pytest.mark.parametrize("change_number,field", [
    (2, "st_mtime_ns"),
    (3, "st_dev"),
    (3, "st_ino"),
    (3, "st_size"),
    (3, "st_mtime_ns"),
    (3, "st_ctime_ns"),
    (4, "st_ctime_ns"),
])
def test_stable_json_rejects_changed_or_replaced_handle_identity(stable_json_evidence, monkeypatch, change_number, field, primed):
    path, info = stable_json_evidence
    actual_fstat, calls = report.os.fstat, 0
    def changed(fd):
        nonlocal calls
        calls += 1
        result = actual_fstat(fd)
        if calls != change_number:
            return result
        fields = {key: getattr(result, key) for key in ("st_mode", "st_dev", "st_ino", "st_size", "st_mtime_ns", "st_ctime_ns")}
        fields[field] += 1
        return SimpleNamespace(**fields)
    with report._parsed_evidence_operation():
        if primed:
            report._stable_json(path, **info, schema="stable-evidence-test-v1")
        monkeypatch.setattr(report.os, "fstat", changed)
        with pytest.raises(report.CandidateReportError) as caught:
            report._stable_json(path, **info, schema="stable-evidence-test-v1")
    assert caught.value.code == "evidence_changed_during_read"


@pytest.mark.parametrize("primed", [False, True])
@pytest.mark.parametrize("mismatch,expected_code", [
    ("sha256", "evidence_sha256_mismatch"),
    ("size", "evidence_size_mismatch"),
    ("schema", "evidence_schema_mismatch"),
])
def test_stable_json_expected_hash_size_schema_remain_strict(stable_json_evidence, mismatch, expected_code, primed):
    path, info = stable_json_evidence
    expected = {"sha256": info["sha256"], "size": info["size"], "schema": "stable-evidence-test-v1"}
    expected[mismatch] = {"sha256": "0" * 64, "size": info["size"] + 1, "schema": "wrong-schema"}[mismatch]
    with report._parsed_evidence_operation():
        if primed:
            report._stable_json(path, **info, schema="stable-evidence-test-v1")
        with pytest.raises(report.CandidateReportError) as caught:
            report._stable_json(path, **expected)
    assert caught.value.code == expected_code


@pytest.mark.parametrize("compact", [False, True])
@pytest.mark.parametrize("fallback", [False, True])
def test_report_parsed_evidence_reuse_is_byte_equivalent_and_does_not_escape(
        descriptor_payload, tmp_path, content_converter, monkeypatch, evidence_parses, compact, fallback):
    """Offline unit bundle only; no real PDF, model loading/training, or publication."""
    payload, model_path = descriptor_payload
    output = tmp_path / "reports"
    if fallback:
        payload["snapshot_binding"]["descriptor"] = {"current_evidence": ["fixture", {"gap": None}]}
    if compact:
        payload, _, _ = report._prepare_report_payload(payload, output)
    original = copy.deepcopy(payload)
    model_raw = model_path.read_bytes()
    monkeypatch.setattr(report, "attach_report_comparison", lambda payload: None)
    monkeypatch.setattr(report, "_load_fonts", lambda: SimpleNamespace(check=lambda texts: None))
    monkeypatch.setattr(report, "_render_pdf", lambda path, *args: path.write_bytes(b"Offline binding fixture, not a PDF."))
    monkeypatch.setattr(report, "_inspect_structure", lambda *args: {"pages": 1, "embedded_truetype_subsets": 1})
    monkeypatch.setattr(report, "_extract_if_available", lambda *args: {"status": "pending_external"})
    actual_stable, uncached, documents = report._stable_json, [True], []
    def observe(path, **kwargs):
        token = report._PARSED_EVIDENCE.set(None) if uncached[0] else None
        try:
            result = actual_stable(path, **kwargs)
        finally:
            if token is not None:
                report._PARSED_EVIDENCE.reset(token)
        if Path(path).resolve() == model_path.resolve():
            documents.append(result[0])
        return result
    monkeypatch.setattr(report, "_stable_json", observe)
    evidence_parses.clear()
    baseline = report.write_candidate_report(payload, output)
    assert evidence_parses.count(model_raw.decode("utf-8")) == 2
    evidence_parses.clear()
    documents.clear()
    uncached[0] = False
    optimized = report.write_candidate_report(payload, output)
    assert evidence_parses.count(model_raw.decode("utf-8")) == 1
    assert len(evidence_parses) == (2 if fallback else 1)
    assert len(documents) == 2 and documents[0] is documents[1]
    assert report._json_bytes(documents[0]) == model_raw  # All private downstream consumers stayed read-only.
    def container_ids(value):
        if isinstance(value, (dict, list)):
            yield id(value)
            for child in value.values() if isinstance(value, dict) else value:
                yield from container_ids(child)
    assert not set(container_ids(documents[0])) & (set(container_ids(payload)) | set(container_ids(optimized)))
    for key in ("pdf_path", "json_path", "html_path"):
        assert Path(baseline[key]).read_bytes() == Path(optimized[key]).read_bytes()
    assert baseline["validation"] == optimized["validation"]
    assert baseline["pdf"] == optimized["pdf"] and baseline["json"] == optimized["json"]
    assert payload == original and model_path.read_bytes() == model_raw
    assert report._PARSED_EVIDENCE.get() is None
    first_job_document = documents[0]
    evidence_parses.clear()
    documents.clear()
    checked = report.validate_candidate_report(payload, Path(optimized["pdf_path"]))
    assert checked == optimized["validation"]
    assert evidence_parses.count(model_raw.decode("utf-8")) == 1
    assert documents[0] is not first_job_document
    assert report._PARSED_EVIDENCE.get() is None


def test_report_parsed_evidence_reuse_rejects_tamper_after_preparation(
        descriptor_payload, tmp_path, content_converter, monkeypatch, evidence_parses):
    payload, model_path = descriptor_payload
    before, original = model_path.stat(), model_path.read_bytes()
    monkeypatch.setattr(report, "attach_report_comparison", lambda payload: None)
    monkeypatch.setattr(report, "_load_fonts", lambda: SimpleNamespace(check=lambda texts: None))
    def tamper(path, *args):
        path.write_bytes(b"Offline binding fixture, not a PDF.")
        model_path.write_bytes(original.replace(b'"metadata":', b'"metadatX":', 1))
        os.utime(model_path, ns=(before.st_atime_ns, before.st_mtime_ns))
    monkeypatch.setattr(report, "_render_pdf", tamper)
    monkeypatch.setattr(report, "_inspect_structure", lambda *args: {"pages": 1, "embedded_truetype_subsets": 1})
    with pytest.raises(report.CandidateReportError) as caught:
        report.write_candidate_report(payload, tmp_path / "reports")
    assert caught.value.code == "report_preflight_failed"
    assert caught.value.details["validation"]["errors"][0]["code"] == "evidence_sha256_mismatch"
    assert len(evidence_parses) == 1 and report._PARSED_EVIDENCE.get() is None
    assert not list((tmp_path / "reports").glob("candidate_*"))


@pytest.mark.parametrize("value", list(report._KINDS))
def test_known_kind_does_not_eagerly_format_fallback(monkeypatch, value):
    monkeypatch.setattr(report, "_code", lambda value: pytest.fail("Known kind must not evaluate fallback."))
    assert report._kind(value) == report._KINDS[value]


def test_unknown_kind_preserves_fallback_calls_and_errors(monkeypatch):
    calls = []
    def code(value):
        calls.append(value)
        if value == "broken-kind":
            raise report.CandidateReportError("fixture_code_error", "fixture")
        return "fixture-code"
    monkeypatch.setattr(report, "_code", code)
    assert report._kind(None) == "未選取模型" and calls == []
    assert report._kind("unknown-kind") == "研究模型（模型識別：fixture-code）"
    assert calls == ["unknown-kind"]
    with pytest.raises(report.CandidateReportError) as caught:
        report._kind("broken-kind")
    assert caught.value.code == "fixture_code_error" and calls == ["unknown-kind", "broken-kind"]


@pytest.mark.parametrize("field,code", [
    ("symbol", "^GSPC"), ("symbol", "^TNX"), ("symbol", "DX-Y.NYB"), ("symbol", "CL=F"),
    ("instrument", "unverified-instrument"), ("series_id", "UNVERIFIED_SERIES"),
])
def test_context_identity_retains_untranslated_instrument_codes(content_converter, field, code):
    record = {"date": "2026-10-16", "close": 5.098, "name_zh": None, "name": "", field: code}
    sources = [{"route_id": "global_market_history", "usage": "used", "records": [record]}]
    original = copy.deepcopy(sources)
    tables, _ = report._context_tables(sources)
    shown = tables[0]["rows"][0]
    assert "標的／指標代碼：" + code in shown[1]
    assert "中文名稱未核對" in shown[1]
    assert "收盤值" in shown[1] and shown[2].startswith("5.098（")
    assert sources == original


def test_context_identity_distinguishes_multiple_symbols_and_sector_series(content_converter):
    sources = [
        {"route_id": "global_market_history", "records": [
            {"date": "2026-10-16", "symbol": "^TNX", "instrument": "us10y", "close": 5.098},
            {"date": "2026-10-16", "symbol": "GC=F", "instrument": "gold", "close": 4186.700195},
            {"date": "2026-10-16", "symbol": "DX-Y.NYB", "close": 99.040001}]},
        {"route_id": "official_sector_observations", "records": [
            {"date": "2026-10-16", "label": "產業觀測指標", "feature_name": "sector_proxy", "entity_id": "sector-a", "value": 12.3}]},
    ]
    tables, _ = report._context_tables(sources)
    rows = tables[0]["rows"]
    assert "^TNX、us10y" in rows[0][1]
    assert "GC=F、gold" in rows[1][1]
    assert "DX-Y.NYB" in rows[2][1]
    assert [row[2].split("（", 1)[0] for row in rows] == ["5.098", "4,186.700195", "99.040001"]
    assert "產業觀測指標" in tables[1]["rows"][0][1]
    assert "sector_proxy、sector-a" in tables[1]["rows"][0][1]


def test_context_identity_missing_or_english_name_is_explicit(content_converter):
    sources = [{"route_id": "finmind_government_bonds_yield", "records": [
        {"date": "2026-10-16", "name": "United States 10-Year", "value": 5.098},
        {"date": "2026-10-16", "value": 5.099}]}]
    tables, _ = report._context_tables(sources)
    assert "標的／指標識別：United States 10-Year（中文名稱未核對）" in tables[0]["rows"][0][1]
    assert "標的未識別／觀測值" == tables[0]["rows"][1][1]


def test_source_close_time_is_visible_and_not_claimed_publication(content_converter):
    close_at, collected_at = "2026-10-16T12:20:00Z", "2026-10-17T18:00:00Z"
    sources = [{"route_id": "global_market_history", "records": [
        {"date": "2026-10-16", "symbol": "^TNX", "close": 5.098, "source_close_at": close_at,
         "_shared_collected_at": collected_at}]}]
    tables, _ = report._context_tables(sources)
    timing = tables[0]["rows"][0][3]
    assert "來源市場收盤時間 " + close_at in timing
    assert "歷史公告時點未驗證" in timing
    assert "宣告發布時間 " + close_at not in timing
    assert collected_at not in timing
    sources[0]["records"][0]["published_at"] = "2026-10-16T12:30:00Z"
    tables, _ = report._context_tables(sources)
    assert "宣告發布時間 2026-10-16T12:30:00Z" in tables[0]["rows"][0][3]
    assert "來源市場收盤時間 " + close_at in tables[0]["rows"][0][3]
