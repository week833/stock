# RETIRED TEST MODULE -- not collected (tests/conftest.py collect_ignore = ["_retired"]).
# Moved verbatim from tests/test_telegram_stock_command_bot.py on 2026-10-12; see tests/_retired/README.md
# for the removed module/feature each test exercised and the evidence that it was intentionally removed.
from __future__ import annotations

import json
import hashlib
import os
import sys
import threading
from pathlib import Path

import pytest


SUITE_DIR = Path(__file__).resolve().parents[1]
if str(SUITE_DIR) not in sys.path:
    sys.path.insert(0, str(SUITE_DIR))

import telegram_stock_command_bot as bot


TEST_ANALYSIS_PYTHON = r"D:\stock\GitHub\.venv\Scripts\python.exe"


@pytest.mark.parametrize("gate_fails,raw_date,expected", [
    (True, "2026-09-18", "provisional"),
    (False, "2026-09-18", "provisional"),
    (False, "2026-08-21", "formal"),
    (False, "2026-08-20", "formal"),
])
def test_select_job_source_only_downgrades_at_initial_gate(monkeypatch, caplog, gate_fails, raw_date, expected):
    monkeypatch.setattr(bot, "shared_current_head_exists", lambda: True)
    instance = object.__new__(bot.TelegramStockCommandBot)
    messages = []
    instance.client = type("Client", (), {"send_message": lambda self, text: messages.append(text)})()
    def refresh():
        if gate_fails:
            raise bot.AnalysisProcessError("gate")
        instance.analysis_env = {bot.CENTRAL_EXPECTED_ASOF_ENV: "2026-08-21"}
    instance.refresh_analysis_environment = refresh
    monkeypatch.setattr(bot, "read_shared_raw_pin", lambda: {"manifestSha256": "a" * 64, "priceDate": raw_date})
    with caplog.at_level("INFO", logger=bot.LOGGER.name):
        instance.select_job_source()
    assert instance.report_class == expected
    assert messages == []
    if expected == "provisional":
        assert bot.CENTRAL_READY_ENV not in instance.analysis_env
        assert instance.analysis_env[bot.CENTRAL_NETWORK_DISABLED_ENV] == "1"
        assert "採用 provisional 個股來源：reason=" in caplog.text


def test_missing_shared_head_uses_raw_without_central(monkeypatch):
    instance = object.__new__(bot.TelegramStockCommandBot)
    messages = []
    instance.client = type("Client", (), {"send_message": lambda self, text: messages.append(text)})()
    instance.refresh_analysis_environment = lambda: pytest.fail("Central must not be used")
    monkeypatch.setattr(bot, "shared_current_head_exists", lambda: False)
    monkeypatch.setattr(bot, "read_shared_raw_pin", lambda: {"manifestSha256": "a" * 64, "priceDate": "2026-09-18"})
    instance.select_job_source()
    assert instance.report_class == "provisional"
    assert messages == []


@pytest.mark.parametrize("gate_fails", [False, True])
def test_unavailable_raw_preserves_formal_gate(monkeypatch, gate_fails):
    instance = object.__new__(bot.TelegramStockCommandBot)
    calls = []
    def unavailable():
        raise bot.AnalysisProcessError("raw unavailable")
    def refresh():
        calls.append("formal")
        if gate_fails:
            raise bot.AnalysisProcessError("formal gate failed")
    instance.refresh_analysis_environment = refresh
    monkeypatch.setattr(bot, "read_shared_raw_pin", unavailable)
    monkeypatch.setattr(bot, "shared_current_head_exists", lambda: pytest.fail("raw unavailable"))
    if gate_fails:
        with pytest.raises(bot.AnalysisProcessError, match="formal gate failed"):
            instance.select_job_source()
    else:
        instance.select_job_source()
    assert calls == ["formal"]
    assert instance.report_class == "formal"


def test_shared_head_discovery_uses_contract(tmp_path, monkeypatch):
    head = tmp_path / "head.json"
    contract = tmp_path / "contract.json"
    contract.write_text(json.dumps({"shared_control": {"current_head_path": str(head)}}), encoding="utf-8")
    monkeypatch.setattr(bot, "PATH_CONTRACT_PATH", contract)
    assert not bot.shared_current_head_exists()
    head.write_text("{}", encoding="utf-8")
    assert bot.shared_current_head_exists()


@pytest.mark.parametrize("content", [None, "{", "{}", '{"shared_control": null}'])
def test_shared_head_discovery_rejects_invalid_contract(tmp_path, monkeypatch, content):
    contract = tmp_path / "contract.json"
    if content is not None:
        contract.write_text(content, encoding="utf-8")
    monkeypatch.setattr(bot, "PATH_CONTRACT_PATH", contract)
    with pytest.raises(bot.AnalysisProcessError, match="shared current head path contract") as error:
        bot.shared_current_head_exists()
    assert error.value.__cause__ is not None


def test_formal_mode_gate_failure_never_downgrades(monkeypatch):
    instance = object.__new__(bot.TelegramStockCommandBot)
    instance.report_class = "formal"
    def fail():
        raise bot.AnalysisProcessError("gate")
    instance.refresh_analysis_environment = fail
    monkeypatch.setattr(bot, "read_shared_raw_pin", lambda: pytest.fail("must not choose a new source"))
    with pytest.raises(bot.AnalysisProcessError):
        instance.refresh_analysis_environment_for_mode("job", bot.MODE_THREE_MONTH)
    assert instance.report_class == "formal"


def test_provisional_boundary_rejects_changed_manifest_without_central(monkeypatch):
    instance = object.__new__(bot.TelegramStockCommandBot)
    instance.report_class = "provisional"
    instance.shared_raw_pin = {"manifestSha256": "a" * 64, "priceDate": "2026-09-18"}
    instance.refresh_analysis_environment = lambda: pytest.fail("Central must not be used")
    monkeypatch.setattr(bot, "read_shared_raw_pin", lambda: dict(instance.shared_raw_pin))
    instance.refresh_analysis_environment_for_mode("job", "resolver")
    monkeypatch.setattr(bot, "read_shared_raw_pin", lambda: {**instance.shared_raw_pin, "manifestSha256": "b" * 64})
    with pytest.raises(bot.AnalysisProcessError, match="manifest"):
        instance.refresh_analysis_environment_for_mode("job", bot.MODE_TOMORROW_OPEN)


def test_provisional_environment_strips_inherited_readiness_and_secrets(monkeypatch):
    for key in (bot.CENTRAL_READY_ENV, "DStock_SINGLE_STOCK_FORMAL_TRANSACTION", "FINMIND_TOKEN", "TELEGRAM_CHAT_ID", "OTHER_API_KEY"):
        monkeypatch.setenv(key, "secret-value")
    environment = bot.provisional_environment()
    assert all("secret-value" != value for value in environment.values())
    assert environment[bot.CENTRAL_NETWORK_DISABLED_ENV] == "1"
    assert environment["PYTHONPATH"] == os.pathsep.join(
        (str(bot.ANALYSIS_DIR), str(bot.SHARED_DIR))
    )
    assert environment["MPLBACKEND"] == "Agg"


def test_provisional_manifest_requires_real_calendar_date(tmp_path, monkeypatch):
    manifest = tmp_path / "stock_data_current.json"
    monkeypatch.setattr(bot, "SHARED_RAW_MANIFEST", manifest)
    payload = {"schema": "stock-data-publication-v1", "datasets": [{"route_id": "finmind_price_adj", "actual_max_date": "2026-09-18", "status": "PARTIAL"}]}
    raw = json.dumps(payload).encode("utf-8")
    manifest.write_bytes(raw)
    assert bot.read_shared_raw_pin() == {"manifestSha256": hashlib.sha256(raw).hexdigest(), "priceDate": "2026-09-18"}
    payload["datasets"][0]["actual_max_date"] = "2026-02-30"
    manifest.write_text(json.dumps(payload), encoding="utf-8")
    with pytest.raises(bot.AnalysisProcessError):
        bot.read_shared_raw_pin()


@pytest.mark.parametrize("failure", ["none", "formal", "stock", "date", "lineage", "central", "schema", "lineage_stock", "descriptor", "receipt_path", "receipt_alias", "root", "pin", "artifact", "eof", "cached_ok", "cached_pdf", "cached_json", "cached_manifest", "cached_artifact"])
@pytest.mark.parametrize("source_gaps", [[], {"projection": [], "diagnostics": [], "datasets": []}])
def test_provisional_report_independent_validation(tmp_path, monkeypatch, failure, source_gaps):
    monkeypatch.syspath_prepend(str(bot.ANALYSIS_DIR))
    import stock_data_sources
    root = tmp_path / "raw"
    root.mkdir()
    manifest = root / "manifest.json"
    monkeypatch.setattr(bot, "SHARED_RAW_ROOT", root)
    monkeypatch.setattr(bot, "SHARED_RAW_MANIFEST", manifest)
    artifact = root / "price.json"
    artifact.write_bytes(b"[]")
    pin = {"manifestSha256": "a" * 64, "priceDate": "2026-09-18"}
    monkeypatch.setattr(bot, "read_shared_raw_pin", lambda: dict(pin))
    descriptor = {"schema": "stock-data-published-inputs-v1", "sources": [{
        "kind": "manifest_json", "source_id": "shared_market_data", "root": str(root),
        "manifest_path": str(manifest), "manifest_sha256": pin["manifestSha256"], "snapshot_id": pin["manifestSha256"],
        "publication_run_id": "producer-run",
    }], "artifacts": [{"root": str(root), "source_id": "shared_market_data", "route_id": "finmind_price_adj", "path": "price.json", "size": 2, "sha256": hashlib.sha256(b"[]").hexdigest()}]}
    descriptor["descriptor_sha256"] = hashlib.sha256(json.dumps(descriptor, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")).hexdigest()
    pdf, document = tmp_path / "report.pdf", tmp_path / "report.json"
    pdf.write_bytes(b"%PDF-1.4\n%%EOF")
    payload = {"report_schema_version": bot.PROVISIONAL_SINGLE_REPORT_SCHEMA, "report_class": "provisional", "source_mode": "published", "provisional_notice": bot.PROVISIONAL_NOTICE, "source_gaps": [], "stock": {"stock_id": "2330"}, "latest_data_date": "2026-09-18", "estimated_for": "2026-09-21", "output_pdf": str(pdf), "output_json": str(document), "input_sources": descriptor}
    payload["source_gaps"] = source_gaps
    payload["central_projection_lineage"] = {
        "source_mode": "published", "schema": "single-stock-published-projection-v1",
        "stock_id": "2330", "input_sources": descriptor,
        "run_id": "producer-run", "receipt_run_id": "producer-run",
        "publication_run_id": "producer-run", "receipt_sha256": pin["manifestSha256"],
    }
    payload["central_run_id"] = "producer-run"
    payload["central_receipt_sha256"] = pin["manifestSha256"]
    if failure == "formal": payload["report_schema_version"] = bot.FORMAL_SINGLE_REPORT_SCHEMA
    if failure == "stock": payload["stock"]["stock_id"] = "2303"
    if failure == "date": payload["estimated_for"] = "2026-02-30"
    if failure == "lineage": payload["central_run_id"] = "fake"
    if failure == "central": payload["central_projection_lineage"]["source_mode"] = "central"
    if failure == "schema": payload["central_projection_lineage"]["schema"] = "central"
    if failure == "lineage_stock": payload["central_projection_lineage"]["stock_id"] = "2303"
    if failure == "descriptor": payload["central_projection_lineage"]["input_sources"] = {}
    if failure == "receipt_path": payload["central_projection_lineage"]["receipt_path"] = str(bot.CENTRAL_LATEST_RECEIPT_PATH)
    if failure == "receipt_alias": payload["central_receipt_sha256"] = "b" * 64
    if failure == "root": descriptor["sources"][0]["root"] = str(tmp_path)
    if failure == "pin": descriptor["sources"][0]["manifest_sha256"] = "b" * 64
    if failure == "artifact": artifact.write_bytes(b"[1]")
    if failure == "eof": pdf.write_bytes(b"%PDF-1.4")
    document.write_text(json.dumps(payload), encoding="utf-8")
    args = (bot.MODE_TOMORROW_OPEN, bot.StockMatch("2330", "TSMC", "exact", 1), tmp_path, pdf, document, pin)
    if failure.startswith("cached_"):
        calls = {"verify": 0, "parse": 0}
        original_verify = stock_data_sources.verify_descriptor
        original_loads = bot.json.loads
        report_text = document.read_text(encoding="utf-8")
        def verify(value):
            calls["verify"] += 1
            return original_verify(value)
        def loads(value, *a, **kw):
            if value == report_text:
                calls["parse"] += 1
            return original_loads(value, *a, **kw)
        monkeypatch.setattr(stock_data_sources, "verify_descriptor", verify)
        monkeypatch.setattr(bot.json, "loads", loads)
        _, parsed, pdf_sha, json_sha = bot._validate_provisional_report(*args, full_verify=False)
        assert calls == {"verify": 0, "parse": 1}
        report = bot.GeneratedReport(args[0], pdf, document, "caption", pdf_sha, json_sha, parsed)
        if failure == "cached_pdf":
            pdf.write_bytes(b"%PDF-1.4\nchanged\n%%EOF")
        if failure == "cached_json":
            document.write_text(report_text + " ", encoding="utf-8")
        if failure == "cached_manifest":
            monkeypatch.setattr(bot, "read_shared_raw_pin", lambda: {**pin, "manifestSha256": "b" * 64})
        if failure == "cached_artifact":
            artifact.write_bytes(b"[1]")
        if failure == "cached_ok":
            assert bot._validate_provisional_report(*args, cached_report=report)[1] is parsed
        else:
            with pytest.raises(bot.AnalysisProcessError):
                bot._validate_provisional_report(*args, cached_report=report)
        assert calls["parse"] == 1
        assert calls["verify"] == (0 if failure in {"cached_pdf", "cached_json"} else 1)
        return
    if failure == "none":
        assert bot._validate_provisional_report(*args)[1]["report_class"] == "provisional"
    else:
        with pytest.raises(bot.AnalysisProcessError):
            bot._validate_provisional_report(*args)


def test_provisional_resolver_uses_published_without_provisional_flag(monkeypatch):
    instance = object.__new__(bot.TelegramStockCommandBot)
    instance.report_class = "provisional"
    instance.analysis_env = {}
    instance.resolve_timeout_seconds = 30
    instance.refresh_analysis_environment_for_mode = lambda job, mode: None
    monkeypatch.setattr(bot, "resolve_analysis_python", lambda _: TEST_ANALYSIS_PYTHON)
    commands = []
    def run(command, **kwargs):
        commands.append(command)
        return type("Result", (), {"returncode": 0, "stdout": ""})()
    monkeypatch.setattr(bot.subprocess, "run", run)
    assert instance.resolve_stock("2330") == []
    assert "--resolve-only" in commands[0]
    assert "--provisional" not in commands[0]
    assert commands[0][-2:] == ["--source-mode", "published"]


@pytest.mark.parametrize("mode", [bot.MODE_THREE_MONTH, bot.MODE_TOMORROW_OPEN])
@pytest.mark.parametrize("source_gaps", [[], {"projection": [], "diagnostics": [], "datasets": []}])
def test_provisional_caption_accepts_both_source_gap_shapes(mode, source_gaps):
    caption = bot.report_caption(mode, bot.StockMatch("2330", "TSMC", "exact", 1),
                                 {"report_class": "provisional", "source_gaps": source_gaps, "latest_data_date": "2026-09-18"})
    assert bot.PROVISIONAL_NOTICE in caption
    assert "2330" in caption
    assert bot.PROVISIONAL_NOTICE in caption.splitlines()[0]
    assert "20260918" in caption.splitlines()[1]  # display contract (2026-10-06): YYYYMMDD
    assert bot.PROVISIONAL_NOTICE in caption.splitlines()[1]
    assert "三個月完整分析" not in caption


def test_provisional_caption_is_short_and_does_not_expose_source_objects():
    routes = ["finmind_price_adj", "finmind_per", "finmind_month_revenue",
              "finmind_institutional", "finmind_margin", "finmind_day_trading", "secret_route"]
    datasets = {route: {"status": "partial", "rows": 123,
                        "unknown_publication_time_rows": 23,
                        "gaps": ["D:\\private\\source.json", "a" * 64]}
                for route in routes}
    text = bot.report_caption(bot.MODE_THREE_MONTH, bot.StockMatch("2330", "台積電", "exact", 1),
                              {"report_class": "provisional", "latest_data_date": "2026-09-18",
                               "source_gaps": {"datasets": datasets}})
    assert len(text) <= 950
    assert "股價筆數：123" in text
    assert "其餘 2 類" in text
    assert "partial" in text and "unknown-time" in text
    assert "partial）：7 類" in text and "unknown-time）：161 筆" in text
    assert bot.PROVISIONAL_NOTICE in text and "不保證報酬" in text
    assert not any(value in text for value in routes + ["D:\\", "a" * 64, "{", "}"])


@pytest.mark.parametrize("metadata,expected,absent", [
    ({"status": "available", "gaps": [], "unknown_publication_time_rows": 0,
      "historical_pit_verified": True},
     ["已列資料類型均可用", "發布時間未知筆數：0", "來源標示歷史可用時間已驗證"],
     ["partial", "unknown-time"]),
    ({"status": "available", "gaps": []},
     ["發布時間資料未確認", "歷史可用時間驗證狀態未確認"], ["partial", "unknown-time"]),
    ({"status": "partial", "unknown_publication_time_rows": 4, "historical_pit_verified": False},
     ["partial）：1 類", "unknown-time）：4 筆", "歷史可用時間未通過驗證"], []),
    ({}, ["完整性未確認", "發布時間資料未確認"], ["partial", "unknown-time"]),
])
def test_caption_discloses_only_supported_status(metadata, expected, absent):
    text = bot.report_caption(bot.MODE_THREE_MONTH, bot.StockMatch("2330", "台積電", "exact", 1),
                              {"report_class": "provisional",
                               "source_gaps": {"datasets": {"finmind_price_adj": metadata}}})
    assert all(value in text for value in expected)
    assert not any(value in text for value in absent)
    assert len(text) <= 950 and bot.PROVISIONAL_NOTICE in text and "不保證報酬" in text
    assert "{" not in text


def test_caption_retains_unknown_count_when_other_metadata_missing():
    text = bot.report_caption(bot.MODE_THREE_MONTH, bot.StockMatch("2330", "台積電", "exact", 1),
                              {"report_class": "provisional", "source_gaps": {"datasets": {
                                  "finmind_price_adj": {"status": "available", "unknown_publication_time_rows": 5},
                                  "finmind_margin": {"status": "available"}}}})
    assert "unknown-time）：5 筆；其餘時間資料未確認" in text


def test_provisional_list_gaps_preserve_price_count_without_raw_details():
    text = bot.report_caption(bot.MODE_TOMORROW_OPEN, bot.StockMatch("2330", "台積電", "exact", 1),
                              {"report_class": "provisional", "latest_data_date": "2026-09-18",
                               "source_gaps": ["finmind_margin:partial secret", "unknown-time"],
                               "data_status": {"股價": {"rows": 321, "source": "D:\\private"}}})
    assert "股價筆數：321" in text and "融資融券" in text
    assert "partial" in text and "unknown-time" in text
    assert "finmind_margin" not in text and "private" not in text and "secret" not in text


def test_refresh_analysis_environment_requires_receipt_hash(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    environment = _central_environment("2026-08-21")
    environment.pop(bot.CENTRAL_RECEIPT_SHA256_ENV)
    monkeypatch.setattr(bot, "analysis_environment", lambda: environment)
    instance = bot.TelegramStockCommandBot.__new__(bot.TelegramStockCommandBot)
    instance.analysis_env = _central_environment("2026-08-20")
    instance.analysis_secret_values = ()

    with pytest.raises(bot.AnalysisProcessError, match="中央 publication receipt hash 不完整"):
        instance.refresh_analysis_environment()
