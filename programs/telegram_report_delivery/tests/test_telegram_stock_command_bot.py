from __future__ import annotations

import copy
import json
import hashlib
import io
import os
import sys
import threading
from pathlib import Path
from types import SimpleNamespace

import pytest


SUITE_DIR = Path(__file__).resolve().parents[1]
if str(SUITE_DIR) not in sys.path:
    sys.path.insert(0, str(SUITE_DIR))

import telegram_stock_command_bot as bot


def _job_comparison_fixture(tmp_path, monkeypatch):
    monkeypatch.setattr(bot, "DSTOCK_SINGLE_ROOT", tmp_path / "dstock_single")
    monkeypatch.setattr(bot, "JOBS_DIR", tmp_path / "dstock_single" / "telegram_jobs")
    bridge = bot.TelegramStockCommandBot._comparison_bridge()
    engine_file = tmp_path / "comparison_fixture_engine.py"
    engine_file.write_text("# isolated fixture identity\n", encoding="utf-8", newline="\n")
    engine = SimpleNamespace(__file__=str(engine_file), CENTRAL_ROOT=tmp_path / "fixture_central",
                             SCHEMA="stock-report-comparison-v1")
    monkeypatch.setattr(bridge, "_engine", lambda: engine)
    monkeypatch.setitem(bridge._ENGINE_SOURCE_BINDINGS, id(engine),
                        (engine, hashlib.sha256(engine_file.read_bytes()).hexdigest()))
    block = {"schema": engine.SCHEMA, "stock_id": "2330", "signal_date": "2026-08-21",
             "source_head_sha256": HEAD_SHA, "source_status": "PARTIAL",
             "metrics": [{"value": None}], "notes": ["部分完成與缺值原樣保留。"]}
    return bridge, block


def test_job_comparison_cache_environment_and_parent_handoff_are_isolated(tmp_path, monkeypatch):
    bridge, block = _job_comparison_fixture(tmp_path, monkeypatch)
    instance = _c0_job_instance()
    instance._job_comparison_cache = None
    prefix = bot.REPORT_COMPARISON_CACHE_PREFIX
    polluted = {prefix + "READ_SHA256": "e" * 64, prefix.lower() + "dir": "old-job",
                "PYTHONUTF8": "1"}
    instance._begin_comparison_cache("first")
    context = instance._job_comparison_cache
    assert context and context["expected_sha256"] is None
    first_dir, first_nonce = context["directory"], context["nonce"]
    before = dict(polluted)
    h63 = instance._comparison_child_environment(polluted, bot.MODE_H63)
    assert h63[prefix + "PHASE"] == "write" and prefix + "READ_SHA256" not in h63
    assert prefix.lower() + "dir" not in h63 and polluted == before
    h1_before = instance._comparison_child_environment(polluted, bot.MODE_H1)
    assert not any(name.casefold().startswith(prefix.casefold()) for name in h1_before)
    payload = _c0_estimate_payload()
    payload["report_comparison"] = copy.deepcopy(block)
    identity = bridge._cache_identity(payload, first_nonce)
    bridge._write_cache(payload, block, (first_dir, first_nonce, "write"), identity)
    raw = json.dumps(payload, ensure_ascii=False).encode("utf-8")
    report_json = tmp_path / "verified_h63.json"
    report_json.write_bytes(raw)
    generated = bot.GeneratedReport(bot.MODE_H63, tmp_path / "h63.pdf", report_json, "fixture",
                                    json_sha256=hashlib.sha256(raw).hexdigest())
    instance._promote_comparison_cache(generated)
    h1 = instance._comparison_child_environment(polluted, bot.MODE_H1)
    cache_file = first_dir / bridge.CACHE_FILE_NAME
    assert h1[prefix + "READ_SHA256"] == hashlib.sha256(cache_file.read_bytes()).hexdigest()
    assert h1[prefix + "NONCE"] == first_nonce and h1[prefix + "PHASE"] == "read"
    # A report changed after the original validation must never promote a cache.
    report_json.write_bytes(raw + b" ")
    instance._promote_comparison_cache(generated)
    assert instance._job_comparison_cache["expected_sha256"] is None
    instance._finish_comparison_cache()
    assert not first_dir.exists()
    instance._begin_comparison_cache("second")
    assert instance._job_comparison_cache["nonce"] != first_nonce
    assert instance._job_comparison_cache["expected_sha256"] is None
    instance._finish_comparison_cache()
    assert polluted == before


def test_job_comparison_cleanup_removes_only_owned_exact_files(tmp_path, monkeypatch):
    bridge, _ = _job_comparison_fixture(tmp_path, monkeypatch)
    instance = _c0_job_instance()
    instance._job_comparison_cache = None
    instance._begin_comparison_cache("cleanup")
    directory = instance._job_comparison_cache["directory"]
    for name in (bridge.CACHE_FILE_NAME, bridge.CACHE_TEMP_NAME):
        (directory / name).write_bytes(b"owned temporary cache bytes")
    foreign = directory / "leave-this-file.txt"
    foreign.write_bytes(b"unrelated")
    other_job = bot.JOBS_DIR / "other"
    other_job.mkdir()
    other_file = other_job / bridge.CACHE_FILE_NAME
    other_file.write_bytes(b"other job")
    instance._finish_comparison_cache()
    assert instance._job_comparison_cache is None
    assert foreign.read_bytes() == b"unrelated" and other_file.read_bytes() == b"other job"
    assert not (directory / bridge.CACHE_FILE_NAME).exists()
    assert not (directory / bridge.CACHE_TEMP_NAME).exists()
    assert directory.is_dir()  # no recursive deletion of the unknown file


def test_job_comparison_cache_creation_failure_keeps_original_environment(tmp_path, monkeypatch):
    _job_comparison_fixture(tmp_path, monkeypatch)
    instance = _c0_job_instance()
    instance._job_comparison_cache = None
    def denied():
        raise PermissionError("fixture optional cache denied")
    monkeypatch.setattr(bot.uuid, "uuid4", denied)
    instance._begin_comparison_cache("unwritable")
    assert instance._job_comparison_cache is None
    prefix = bot.REPORT_COMPARISON_CACHE_PREFIX
    environment = {prefix + "DIR": "other-job", "PYTHONUTF8": "1"}
    assert instance._comparison_child_environment(environment, bot.MODE_H1) == {"PYTHONUTF8": "1"}
    assert environment[prefix + "DIR"] == "other-job"


@pytest.mark.parametrize("failure", [
    None, "H63-analysis", "H63-validation", "H63-send",
    "H1-analysis", "H1-validation", "H1-send",
])
def test_both_horizon_comparison_handoff_preserves_delivery_failure_contract(tmp_path, monkeypatch, failure):
    bridge, block = _job_comparison_fixture(tmp_path, monkeypatch)
    instance = _c0_job_instance()
    instance._job_comparison_cache = None
    instance.analysis_env[bot.REPORT_COMPARISON_CACHE_PREFIX + "READ_SHA256"] = "old-job-value"
    job = {"jobId": "handoff-job", "status": "queued", "query": "2330", "mode": bot.MODE_BOTH}
    events, sent, appended, updates, directories = [], [], [], [], []
    prefix = bot.REPORT_COMPARISON_CACHE_PREFIX
    def update(job_id, **fields):
        assert job_id == job["jobId"]
        updates.append(fields)
        job.update(fields)
    def run(job_id, stock, mode, progress):
        events.append(mode + "-analysis")
        context = instance._job_comparison_cache
        assert context
        directories.append(context["directory"])
        environment = instance._comparison_child_environment(instance.analysis_env, mode)
        if mode == bot.MODE_H1:
            assert appended == [bot.MODE_H63]
            assert environment[prefix + "READ_SHA256"]
            assert events.index("H63-send") < events.index("H63-handoff") < events.index("H1-analysis")
        else:
            assert prefix + "READ_SHA256" not in environment
        if failure == mode + "-analysis":
            raise bot.AnalysisProcessError("fixture " + failure)
        payload = _c0_estimate_payload(horizon=bot.MODE_HORIZONS[mode])
        payload["prediction"] = {"prob_up": .61 if mode == bot.MODE_H63 else .47,
                                 "expected_return": .032 if mode == bot.MODE_H63 else -.004}
        before = copy.deepcopy(payload["prediction"])
        if mode == bot.MODE_H63:
            bridge._write_cache(payload, block, (context["directory"], context["nonce"], "write"),
                                bridge._cache_identity(payload, context["nonce"]))
            payload["report_comparison"] = copy.deepcopy(block)
        else:
            with monkeypatch.context() as scoped:
                for key, value in environment.items():
                    if key.startswith(prefix):
                        scoped.setenv(key, value)
                cached = bridge._cached_block(payload, (context["directory"], context["nonce"], "read"))
            assert cached == block
            payload["report_comparison"] = cached
        assert payload["prediction"] == before
        path = tmp_path / (mode + ".json")
        raw = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        path.write_bytes(raw)
        return bot.GeneratedReport(mode, tmp_path / (mode + ".pdf"), path, "fixture",
                                    json_sha256=hashlib.sha256(raw).hexdigest())
    def validate(job_id, stock, generated):
        events.append(generated.mode + "-validation")
        if failure == generated.mode + "-validation":
            raise bot.AnalysisProcessError("fixture " + failure)
        return generated
    def send(path, caption):
        mode = path.stem
        events.append(mode + "-send")
        if failure == mode + "-send":
            raise bot.TelegramApiError("fixture " + failure)
        sent.append(mode)
        return {"message_id": len(sent)}
    def append(job_id, generated):
        appended.append(generated.mode)
        events.append(generated.mode + "-append")
    original_promote = instance._promote_comparison_cache
    def promote(generated):
        assert generated.mode in sent and generated.mode in appended
        events.append(generated.mode + "-handoff")
        original_promote(generated)
    instance.store = SimpleNamespace(get_job=lambda job_id: dict(job), update_job=update, append_report=append)
    instance.client = SimpleNamespace(send_message=lambda text: None, send_chat_action=lambda action: None,
                                      send_document=send)
    instance.resolve_stock = lambda query: [STOCK_2330]
    instance.run_analysis_mode = run
    instance.validate_report_before_upload = validate
    instance._promote_comparison_cache = promote
    instance._record_delivery_evidence = lambda *args, **kwargs: None
    instance.process_job(job["jobId"])
    assert directories and all(not directory.exists() for directory in directories)
    assert instance._job_comparison_cache is None
    if failure is None:
        assert sent == appended == [bot.MODE_H63, bot.MODE_H1]
        assert job["status"] == "complete"
    elif failure.startswith("H63"):
        assert sent == appended == [] and "H1-analysis" not in events
        assert job["status"] == "failed"
    else:
        assert sent == appended == [bot.MODE_H63] and job["status"] == "failed"
        assert (tmp_path / "H63.json").exists()
    if failure and failure.endswith(("analysis", "validation")):
        assert job["unsentModes"] == ([bot.MODE_H1] if failure.startswith("H1") else [bot.MODE_H63, bot.MODE_H1])
    else:
        assert "unsentModes" not in job  # preserve the original send-error bookkeeping


@pytest.fixture(autouse=True)
def isolate_fixture_ancestor_from_formal_path_check(tmp_path, monkeypatch):
    """The approved QA root contains 'candidates'; validate fixture-owned parts.

    Production validation is unchanged. A candidate/staging directory created
    *inside* the fixture is still rejected by the real guard below.
    """
    original = bot._require_formal_report_path
    root = tmp_path.resolve()
    def check(path):
        try:
            relative = Path(path).resolve().relative_to(root)
        except ValueError:
            return original(path)
        return original(Path("Z:/formal-test-fixture") / relative)
    monkeypatch.setattr(bot, "_require_formal_report_path", check)


@pytest.mark.parametrize("directory", ["candidate", "candidates", "smoke", "staging", "stage"])
def test_fixture_isolation_keeps_nested_nonformal_paths_rejected(tmp_path, directory):
    with pytest.raises(bot.AnalysisProcessError, match="拒絕傳送"):
        bot._require_formal_report_path(tmp_path / directory / "report.pdf")


def test_comparison_caption_consumes_sealed_block_without_reloading_central(monkeypatch):
    import stock_report_comparison as comparison
    def forbidden(*args, **kwargs):
        raise AssertionError("captions must never load Central")
    monkeypatch.setattr(comparison, "build_comparisons", forbidden)
    payload = _c0_estimate_payload()
    prediction_before = dict(payload["prediction"])
    payload["report_comparison"] = {
        "schema": "stock-report-comparison-v1", "stock_id": "2330", "signal_date": "20260821",
        "source_head_sha256": HEAD_SHA,
        "metrics": [{"項目": "月營收", "資料期間": "202607", "本期值": "120（單位未核實）",
                     "比較": "去年同月100；變化+20.00%", "解讀": "同比增加不代表獲利改善。", "資料日期": "20260810"}],
        "notes": []}
    caption = bot.report_caption(bot.MODE_H63, STOCK_2330, payload)
    assert "202607" in caption and "+20.00%" in caption and "20260810" in caption
    assert "上漲機率" in caption and "預估報酬" in caption
    assert payload["prediction"] == prediction_before


def test_comparison_caption_unavailable_keeps_original_estimates():
    payload = _c0_estimate_payload()
    payload["report_comparison"] = {"metrics": [], "notes": ["比較不可得：來源缺漏；原有資料保留。"]}
    caption = bot.report_caption(bot.MODE_H63, STOCK_2330, payload)
    assert "比較不可得" in caption
    assert "61.23%" in caption and "預估報酬" in caption


def test_selected_comparison_caption_does_not_change_selected_numbers():
    payload = {"schema": bot.SELECTED_SINGLE_REPORT_SCHEMA, "research_only": False, "promotion": True,
               "stock": {"stock_id": "2330"}, "asof": "2026-08-21", "horizon_sessions": 63,
               "prediction": {"prob_up": .61, "expected_return": .02},
               "report_comparison": {"metrics": [{"項目": "月營收", "資料期間": "202607", "本期值": "120",
                   "比較": "去年同月100；+20.00%", "解讀": "僅為當期比較。", "資料日期": "20260810"}], "notes": []}}
    caption = bot.selected_report_caption(bot.MODE_H63, STOCK_2330, payload)
    assert "61.00%" in caption and "+2.00%" in caption and "202607" in caption



TEST_ANALYSIS_PYTHON = r"D:\stock\.venv\Scripts\python.exe"
HEAD_SHA = "d" * 64
STOCK_2330 = bot.StockMatch("2330", "台積電", "exact_id", 1.0)


@pytest.fixture(autouse=True)
def legacy_authority_without_selected_pointer(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setitem(
        sys.modules, "stock_daily_selected_deployment",
        SimpleNamespace(selected_analysis_environment=lambda: None),
    )

# ------------------------------------------------------------------------------------------------------------------
# C0 helpers.  The bot pins the C0 runtime binding at job start (data/model/head); later differences are
# version labels that never stop a job, while integrity (C0BindingIntegrityError / C0IntegrityError, a
# completely unreadable head, report digest/schema/PDF checks) still fails closed.
# ------------------------------------------------------------------------------------------------------------------


def _c0_binding(asof: str = "2026-08-21", fingerprint: str = "f" * 64, head: str = HEAD_SHA,
                model_sha: str = "a" * 64) -> dict[str, object]:
    return {
        "schema": "stock-daily-c0-runtime-binding-v2",
        "asof": asof,
        "snapshot_fingerprint": fingerprint,
        "published_sources": [{"source_id": "shared_market_data", "manifest_sha256": head,
                               "published_at": f"{asof}T15:40:00+08:00"}],
        "model_binding": {"sha256": model_sha},
    }


def _c0_job_instance(binding: dict[str, object] | None = None) -> bot.TelegramStockCommandBot:
    """A bot whose job pin is already taken; ``refresh_analysis_environment`` is a no-op unless replaced."""
    binding = binding or _c0_binding()
    instance = bot.TelegramStockCommandBot.__new__(bot.TelegramStockCommandBot)
    instance.analysis_env = {"SYSTEMROOT": os.environ.get("SYSTEMROOT", r"C:\Windows"), "PYTHONUTF8": "1"}
    instance.c0_binding = dict(binding)
    instance._job_c0_binding = dict(binding)
    instance._job_read_at = "2026-08-21T16:00:00+08:00"
    instance._job_version_labels = []
    instance.analysis_secret_values = ()
    instance.refresh_analysis_environment = lambda *, retain_source_lease=False: None
    return instance


def _c0_estimate_payload(stock_id: str = "2330", horizon: int = 63, **overrides: object) -> dict[str, object]:
    payload: dict[str, object] = {
        "report_schema_version": bot.C0_SINGLE_REPORT_SCHEMA,
        "report_class": bot.C0_ESTIMATE_REPORT_CLASS,
        "stock": {"stock_id": stock_id, "stock_name": "台積電"},
        "horizon_sessions": horizon,
        "latest_data_date": "2026-08-21",
        "target_session_date": "2026-11-19" if horizon == 63 else "2026-08-24",
        "prediction": {"prediction_status": "predicted", "prob_up": 0.6123, "expected_return": 0.1234,
                       "calibration_status": "base_only", "selected_direction_kind": "logistic",
                       "selected_return_kind": "ridge"},
        "comparison_metrics": {"direction_accuracy": 0.64, "nonflat_direction_count": 120, "mae": 0.021},
        "model_artifact_sha256": "a" * 64,
        "requirements_version": "req-fixture-1",
        "data_time": {"analysis_asof": "2026-08-21", "read_at_utc": "2026-08-21T08:00:00+00:00",
                      "head": {"published_at": "2026-08-21T07:40:00+00:00"}},
        "published_snapshot": {"snapshot_fingerprint": "f" * 64,
                               "sources": [{"manifest_sha256": HEAD_SHA, "published_at": "2026-08-21T07:40:00+00:00"}]},
        "qa_override": False,
    }
    payload.update(overrides)
    return payload


def _write_c0_report(output_dir: Path, payload: dict[str, object], *, bom: bool = False) -> tuple[Path, Path, bytes]:
    output_dir.mkdir(parents=True, exist_ok=True)
    pdf_path, json_path = output_dir / "result.pdf", output_dir / "result.json"
    pdf_path.write_bytes(b"%PDF-1.7\n%%EOF\n")
    document = dict(payload, output_pdf=str(pdf_path), output_json=str(json_path))
    raw = (b"\xef\xbb\xbf" if bom else b"") + json.dumps(document, ensure_ascii=False).encode("utf-8")
    json_path.write_bytes(raw)
    return pdf_path, json_path, raw


@pytest.fixture
def fake_c0_estimate_validation(monkeypatch: pytest.MonkeyPatch) -> list[dict[str, object]]:
    """Replace the shared ``validate_c0_estimate`` (full reproduction needs the published data)."""
    import stock_daily_contract

    calls: list[dict[str, object]] = []

    def validate(payload, pdf_bytes, *, stock_id, horizon, expected_snapshot_fingerprint=None,
                 expected_runtime_binding=None):
        calls.append({"stock_id": stock_id, "horizon": horizon, "pdf": pdf_bytes,
                      "expected_snapshot_fingerprint": expected_snapshot_fingerprint,
                      "expected_runtime_binding": expected_runtime_binding})
        if str(payload["stock"]["stock_id"]) != str(stock_id) or payload["horizon_sessions"] != horizon:
            raise stock_daily_contract.C0IntegrityError("c0_report_identity_mismatch")
        labels = []
        if expected_snapshot_fingerprint != payload["published_snapshot"]["snapshot_fingerprint"]:
            labels.append({"code": "job_published_snapshot_changed", "recorded": expected_snapshot_fingerprint,
                           "actual": payload["published_snapshot"]["snapshot_fingerprint"], "detail": None})
        return {"verified": True, "version_labels": labels}

    monkeypatch.setattr(stock_daily_contract, "validate_c0_estimate", validate)
    return calls


def test_analysis_command_enables_unbuffered_progress_only(tmp_path):
    command = bot.build_analysis_command(bot.MODE_H63, "2330", tmp_path, TEST_ANALYSIS_PYTHON)
    assert command[:2] == [TEST_ANALYSIS_PYTHON, "-u"]
    assert command[2:4] == [str(bot.ANALYSIS_ENTRY_SCRIPT), bot.MODE_H63]
    assert "--no-progress" not in command
    assert "--pinned-head-sha256" not in command
    resolver = bot.build_resolver_command("2330", TEST_ANALYSIS_PYTHON)
    assert resolver[1:3] == [str(bot.ANALYSIS_ENTRY_SCRIPT), bot.MODE_H63]
    assert "--no-progress" in resolver
    h1 = bot.build_analysis_command(bot.MODE_H1, "2330", tmp_path, TEST_ANALYSIS_PYTHON)
    assert h1[2:4] == [str(bot.ANALYSIS_ENTRY_SCRIPT), bot.MODE_H1]
    with pytest.raises(bot.AnalysisProcessError, match="H1 或 H63"):
        bot.build_analysis_command("three_month", "2330", tmp_path, TEST_ANALYSIS_PYTHON)


def test_analysis_command_passes_the_job_start_head_pin_and_rejects_a_malformed_pin(tmp_path):
    assert bot.c0_binding_head_sha256(_c0_binding()) == HEAD_SHA
    for unpinnable in (None, {}, {"published_sources": []}, {"published_sources": [{}, {}]},
                       {"published_sources": [{"manifest_sha256": "xyz"}]}):
        assert bot.c0_binding_head_sha256(unpinnable) is None
    command = bot.build_analysis_command(bot.MODE_H1, "2330", tmp_path, TEST_ANALYSIS_PYTHON, HEAD_SHA)
    assert command[-2:] == ["--pinned-head-sha256", HEAD_SHA]
    for malformed in ("xyz", "D" * 64, HEAD_SHA + "0", ""):
        with pytest.raises(bot.AnalysisProcessError, match="pin"):
            bot.build_analysis_command(bot.MODE_H1, "2330", tmp_path, TEST_ANALYSIS_PYTHON, malformed)


def test_child_environment_strips_inherited_secrets_and_disables_network(monkeypatch: pytest.MonkeyPatch) -> None:
    for key in ("DStock_SINGLE_STOCK_FORMAL_TRANSACTION", "DSTOCK_CENTRAL_PUBLICATION_READY", "FINMIND_TOKEN",
                "TELEGRAM_CHAT_ID", "OTHER_API_KEY"):
        monkeypatch.setenv(key, "secret-value")
    environment = bot._child_analysis_environment()
    assert all("secret-value" != value for value in environment.values())
    assert environment[bot.CURRENT_NETWORK_DISABLED_ENV] == "1"
    assert environment["ENABLE_FINMIND_FETCH"] == "0"
    assert environment["PYTHONPATH"] == os.pathsep.join((str(bot.ANALYSIS_DIR), str(bot.SHARED_DIR)))
    assert environment["MPLBACKEND"] == "Agg"


def test_analysis_python_resolver_uses_fixed_analysis_mode_and_fails_closed(
    monkeypatch: pytest.MonkeyPatch
) -> None:
    environment = bot._child_analysis_environment()
    system_root_keys = [key for key in environment if key.casefold() == "systemroot"]
    assert system_root_keys == ["SYSTEMROOT"]
    calls: list[tuple[list[str], dict[str, object]]] = []

    def successful_run(command: list[str], **kwargs: object):
        calls.append((command, kwargs))
        return bot.subprocess.CompletedProcess(
            command, 0, f"noise\n{bot.ANALYSIS_PYTHON_MARKER}{TEST_ANALYSIS_PYTHON}\n", ""
        )

    monkeypatch.setattr(bot.subprocess, "run", successful_run)
    assert bot.resolve_analysis_python(environment) == TEST_ANALYSIS_PYTHON
    command, options = calls[0]
    assert command[0].endswith("\\System32\\cmd.exe")
    assert command[1:4] == ["/d", "/s", "/v:on"]
    resolver_argument = str(bot.ANALYSIS_PYTHON_RESOLVER.relative_to(bot.STOCK_ROOT))
    assert command[-1].startswith(f"call {resolver_argument} analysis ")
    assert " analysis " in command[-1]
    assert "!PY!" in command[-1]
    assert options["env"] is environment
    assert options["shell"] is False

    def failed_run(command: list[str], **kwargs: object):
        return bot.subprocess.CompletedProcess(command, 1, "", "")

    monkeypatch.setattr(bot.subprocess, "run", failed_run)
    with pytest.raises(bot.ConfigurationError, match="未選出可用 Python"):
        bot.resolve_analysis_python(environment)

    conflicting_environment = dict(environment)
    conflicting_environment["SystemRoot"] = str(Path(environment["SYSTEMROOT"]) / "conflict")
    with pytest.raises(bot.ConfigurationError, match="SystemRoot.*衝突"):
        bot.resolve_analysis_python(conflicting_environment)


def test_single_stock_phase_reader_buffers_lines_and_rejects_wrong_stock_or_regression(
    tmp_path: Path,
) -> None:
    path = tmp_path / "analysis.log"
    reader = bot.SingleStockPhaseReader("2330")

    def marker(phase: str, stock_id: str = "2330") -> bytes:
        payload = {
            "schema": bot.SINGLE_STOCK_PHASE_SCHEMA,
            "phase": phase,
            "stock_id": stock_id,
            "detail": "private detail must never be surfaced",
        }
        return bot.SINGLE_STOCK_PHASE_PREFIX + json.dumps(
            payload, ensure_ascii=True, separators=(",", ":")
        ).encode("utf-8") + b"\n"

    first = marker("data_check")
    path.write_bytes(b"ordinary analyzer output\n" + first[:len(first) // 2])
    assert reader.read_available(path) == []
    with path.open("ab") as handle:
        handle.write(first[len(first) // 2:])
    events = reader.read_available(path)
    assert [event.phase for event in events] == ["data_check"]
    assert not hasattr(events[0], "detail")

    with path.open("ab") as handle:
        handle.write(marker("model") + marker("data_prepare") + marker("pdf", "9999"))
    events = reader.read_available(path)
    assert [event.phase for event in events] == ["model"]

    bounded_path = tmp_path / "bounded.log"
    bounded_path.write_bytes(
        b"x" * (bot.SINGLE_STOCK_PHASE_READ_MAX_BYTES + 10)
        + b"\n"
        + marker("data_check")
    )
    bounded_reader = bot.SingleStockPhaseReader("2330")
    assert bounded_reader.read_available(bounded_path) == []
    assert bounded_reader.offset == bot.SINGLE_STOCK_PHASE_READ_MAX_BYTES
    assert bounded_reader.pending == bytearray()
    assert [event.phase for event in bounded_reader.read_available(bounded_path)] == ["data_check"]


def test_safe_error_tail_omits_untrusted_phase_details(tmp_path: Path) -> None:
    path = tmp_path / "analysis.log"
    marker = bot.SINGLE_STOCK_PHASE_PREFIX.decode("ascii") + json.dumps(
        {
            "schema": bot.SINGLE_STOCK_PHASE_SCHEMA,
            "phase": "data_prepare",
            "stock_id": "2330",
            "detail": "untrusted detail must not be returned",
        },
        ensure_ascii=True,
    )
    path.write_text("safe analyzer failure\n" + marker + "\n", encoding="utf-8")

    tail = bot.read_safe_log_tail(path)

    assert "safe analyzer failure" in tail
    assert "untrusted detail" not in tail
    assert bot.SINGLE_STOCK_PHASE_PREFIX.decode("ascii") not in tail


def test_bot_uses_bounded_separate_progress_transport_and_clamps_preparation_timeout(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    clients: list[tuple[float, int]] = []

    class FakeClient:
        def __init__(self, token: str, chat_id: str, *, timeout_seconds: float, max_attempts: int) -> None:
            assert token and chat_id
            clients.append((timeout_seconds, max_attempts))

    class FakeStore:
        def __init__(self, *_: object) -> None:
            pass

        def next_update_id(self) -> int:
            return 0

    monkeypatch.setattr(bot, "TelegramClient", FakeClient)
    monkeypatch.setattr(bot, "CommandStateStore", FakeStore)
    instance = bot.TelegramStockCommandBot(
        {
            "TELEGRAM_BOT_TOKEN": "123456:abcdefghijklmnopqrstuvwxyz_ABCDE",
            "TELEGRAM_CHAT_ID": "998877",
            "TELEGRAM_COMMAND_ANALYSIS_TIMEOUT_MINUTES": "10",
            bot.SINGLE_STOCK_PREPARATION_TIMEOUT_ENV: "45",
        }
    )

    assert clients == [(120, 4), (5, 1)]
    assert instance.analysis_timeout_seconds == 10 * 60
    assert instance.preparation_timeout_seconds == 10 * 60


@pytest.mark.parametrize(
    ("emit_phase", "overall_timeout", "preparation_timeout", "expected_error"),
    [
        (True, 10, 2, "資料檢查或資料準備超過允許時間"),
        (False, 2, 1, "超過允許時間"),
    ],
)
def test_run_analysis_mode_uses_preparation_limit_and_legacy_total_fallback(
    emit_phase: bool,
    overall_timeout: int,
    preparation_timeout: int,
    expected_error: str,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    dstock_single = tmp_path / "dstock_single"
    monkeypatch.setattr(bot, "DSTOCK_SINGLE_ROOT", dstock_single)
    monkeypatch.setattr(bot, "JOBS_DIR", dstock_single / "telegram_jobs")

    class FakeClock:
        now = 0.0

        def monotonic(self) -> float:
            return self.now

        def sleep(self, seconds: float) -> None:
            self.now += seconds

    clock = FakeClock()
    monkeypatch.setattr(bot, "time", clock)

    class FakeProcess:
        pid = 1301
        returncode: int | None = None

        def __init__(self) -> None:
            self.terminated = False

        def poll(self) -> int | None:
            return self.returncode

        def terminate(self) -> None:
            self.terminated = True
            self.returncode = -15

        def wait(self, timeout: float | None = None) -> int:
            del timeout
            return self.returncode or 0

        def kill(self) -> None:
            self.returncode = -9

    processes: list[FakeProcess] = []
    child_environments: list[dict[str, str]] = []

    def fake_popen(command: list[str], **kwargs: object) -> FakeProcess:
        del command
        process = FakeProcess()
        processes.append(process)
        child_environments.append(kwargs["env"])  # type: ignore[arg-type]
        if emit_phase:
            payload = {
                "schema": bot.SINGLE_STOCK_PHASE_SCHEMA,
                "phase": "data_check",
                "stock_id": "2330",
            }
            output = kwargs["stdout"]
            output.write(
                bot.SINGLE_STOCK_PHASE_PREFIX.decode("ascii")
                + json.dumps(payload, ensure_ascii=True, separators=(",", ":"))
                + "\n"
            )
            output.flush()
        return process

    class FakeStore:
        def update_job(self, job_id: str, **fields: object) -> None:
            del job_id, fields

    monkeypatch.setattr(bot.subprocess, "Popen", fake_popen)
    monkeypatch.setattr(bot, "resolve_analysis_python", lambda _: TEST_ANALYSIS_PYTHON)
    instance = _c0_job_instance()
    instance.client = object()
    instance.store = FakeStore()
    instance.heartbeat_seconds = 60
    instance.analysis_timeout_seconds = overall_timeout
    instance.preparation_timeout_seconds = preparation_timeout
    instance.stop_event = threading.Event()
    instance.current_process_lock = threading.Lock()
    instance.current_process = None
    instance.write_heartbeat = lambda **_: None
    progress: list[dict[str, object]] = []

    with pytest.raises(bot.AnalysisProcessError) as raised:
        instance.run_analysis_mode(
            "tg-phase-timeout",
            bot.StockMatch("2330", "台積電", "exact_id", 1.0),
            bot.MODE_H63,
            progress.append,
        )

    assert expected_error in str(raised.value)
    assert processes[0].terminated
    assert child_environments[0][bot.SINGLE_STOCK_PHASE_ENV] == "1"
    assert ([item["phase"] for item in progress] == ["data_check"]) == emit_phase
    assert not list(bot.JOBS_DIR.rglob("analysis.log"))
    assert not (bot.JOBS_DIR / "tg-phase-timeout").exists()


def test_run_analysis_mode_starts_model_clock_only_at_model_marker(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    dstock_single = tmp_path / "dstock_single"
    monkeypatch.setattr(bot, "DSTOCK_SINGLE_ROOT", dstock_single)
    monkeypatch.setattr(bot, "JOBS_DIR", dstock_single / "telegram_jobs")

    class FakeClock:
        now = 0.0

        def monotonic(self) -> float:
            return self.now

        def sleep(self, seconds: float) -> None:
            self.now += seconds

    clock = FakeClock()
    monkeypatch.setattr(bot, "time", clock)

    def marker(phase: str) -> str:
        return bot.SINGLE_STOCK_PHASE_PREFIX.decode("ascii") + json.dumps(
            {"schema": bot.SINGLE_STOCK_PHASE_SCHEMA, "phase": phase, "stock_id": "2330"},
            ensure_ascii=True,
            separators=(",", ":"),
        ) + "\n"

    class FakeProcess:
        pid = 1302

        def __init__(self, output: object) -> None:
            self.output = output
            self.poll_count = 0
            self.returncode: int | None = None

        def poll(self) -> int | None:
            self.poll_count += 1
            if self.poll_count == 2:
                self.output.write(marker("model"))
                self.output.flush()
            elif self.poll_count == 3:
                self.output.write(marker("pdf"))
                self.output.flush()
            elif self.poll_count >= 4:
                self.returncode = 17
            return self.returncode

        def terminate(self) -> None:
            self.returncode = -15

        def wait(self, timeout: float | None = None) -> int:
            del timeout
            return self.returncode or 0

        def kill(self) -> None:
            self.returncode = -9

    def fake_popen(command: list[str], **kwargs: object) -> FakeProcess:
        del command
        output = kwargs["stdout"]
        output.write(marker("data_check"))
        output.flush()
        return FakeProcess(output)

    class FakeStore:
        def update_job(self, job_id: str, **fields: object) -> None:
            del job_id, fields

    monkeypatch.setattr(bot.subprocess, "Popen", fake_popen)
    monkeypatch.setattr(bot, "resolve_analysis_python", lambda _: TEST_ANALYSIS_PYTHON)
    instance = _c0_job_instance()
    instance.client = object()
    instance.store = FakeStore()
    instance.heartbeat_seconds = 60
    instance.analysis_timeout_seconds = 600
    instance.preparation_timeout_seconds = 300
    instance.stop_event = threading.Event()
    instance.current_process_lock = threading.Lock()
    instance.current_process = None
    instance.write_heartbeat = lambda **_: None
    progress: list[dict[str, object]] = []

    with pytest.raises(bot.AnalysisProcessError, match="執行失敗"):
        instance.run_analysis_mode(
            "tg-model-clock",
            bot.StockMatch("2330", "台積電", "exact_id", 1.0),
            bot.MODE_H63,
            progress.append,
        )

    assert [item["phase"] for item in progress] == ["data_check", "model", "pdf"]
    assert progress[0]["preparation_elapsed_seconds"] == 0
    assert progress[1]["preparation_elapsed_seconds"] == 2
    assert progress[1]["model_elapsed_seconds"] == 0
    assert progress[2]["model_elapsed_seconds"] == 2


def test_runtime_paths_keep_telegram_state_and_logs_and_scope_jobs_to_dstock_single() -> None:
    assert bot.DATA_DIR == Path(r"D:\TWStockData\services\telegram\data")
    assert bot.LOG_DIR == Path(r"D:\TWStockData\services\telegram\logs")
    assert bot.STATE_PATH == bot.DATA_DIR / "stock_command_state.json"
    assert bot.HEARTBEAT_PATH == bot.DATA_DIR / "stock_command_bot.heartbeat.json"
    assert bot.JOBS_DIR == Path(r"D:\TWStockData\program_data\dstock_single\telegram_jobs")


def test_runtime_revision_matches_loaded_source_sha256() -> None:
    expected = hashlib.sha256(Path(bot.__file__).resolve().read_bytes()).hexdigest()
    assert bot.SOURCE_SHA256 == expected
    assert bot.RUNTIME_REVISION == f"telegram_stock_command_bot@{expected[:16]}"


def test_self_check_is_explicit_non_queue_mode() -> None:
    args = bot.build_parser().parse_args(["--self-check"])
    assert args.self_check is True
    assert args.check is False


@pytest.mark.parametrize(
    ("text", "action", "mode", "query"),
    [
        ("2330", "job", bot.MODE_H63, "2330"),
        ("台積電", "job", bot.MODE_H63, "台積電"),
        ("/stock 2330", "job", bot.MODE_H63, "2330"),
        ("/h63 2330", "job", bot.MODE_H63, "2330"),
        ("明日 2330", "job", bot.MODE_H1, "2330"),
        ("/open 台積電", "job", bot.MODE_H1, "台積電"),
        ("/tomorrow 2330", "job", bot.MODE_H1, "2330"),
        ("/h1 2330", "job", bot.MODE_H1, "2330"),
        ("全部 2330", "job", bot.MODE_BOTH, "2330"),
        ("/all@Jo_codex_GPT_bot 台積電", "job", bot.MODE_BOTH, "台積電"),
        ("/status", "status", "", ""),
        ("說明", "help", "", ""),
    ],
)
def test_parse_command(text: str, action: str, mode: str, query: str) -> None:
    parsed = bot.parse_command(text)
    assert (parsed.action, parsed.mode, parsed.query) == (action, mode, query)
    if parsed.action == "job":
        assert bot.MODE_LABELS[parsed.mode]


# /update and /refresh (the bot-triggered central update) were removed with the central gate: the bot
# only reads already published data and never asks the producer to update.
@pytest.mark.parametrize("text", ["/unknown 2330", "明日", "a b c", "2330&whoami", "", "/update", "/refresh"])
def test_parse_command_rejects_invalid_input(text: str) -> None:
    assert bot.parse_command(text).action in {"invalid", "ignore"}


def test_report_path_resolution_rejects_directory_scan_and_nonformal_markers(
    tmp_path: Path,
) -> None:
    output_dir = tmp_path / "job"
    output_dir.mkdir()
    log_path = output_dir / "analysis.log"
    log_path.write_text(
        f"PDF：{output_dir / 'job.pdf'}\n"
        f"JSON：{output_dir / 'job.json'}\n"
        f"JSON：{output_dir / 'candidate.json'}\n",
        encoding="utf-8",
    )
    with pytest.raises(bot.AnalysisProcessError, match="唯一正式 PDF/JSON"):
        bot._declared_report_paths(log_path, output_dir)


def test_parse_and_choose_resolver_matches() -> None:
    output = """
解析結果：輸入「中華」共匹配 3 檔
1. 2204 中華｜name_contains｜score=1.0000
2. 2412 中華電｜name_contains｜score=0.9400
3. 2831 中華銀｜name_contains｜score=0.9400
"""
    matches = bot.parse_resolver_output(output)
    assert [item.code for item in matches] == ["2204", "2412", "2831"]
    assert bot.choose_stock_match("中華", matches) == matches[0]
    assert bot.choose_stock_match("中華電", matches) == matches[1]
    assert bot.choose_stock_match("中", matches) is None
    assert "2412 中華電" in bot.ambiguity_message("中", matches)


def test_fuzzy_result_requires_numeric_confirmation() -> None:
    match = bot.StockMatch("2330", "台積電", "fuzzy_closest", 0.72)
    assert bot.choose_stock_match("台機電", [match]) is None
    assert bot.choose_stock_match("2330", [match]) == match


def test_state_store_deduplicates_and_persists_jobs(tmp_path: Path) -> None:
    state_path = tmp_path / "state.json"
    store = bot.CommandStateStore(state_path, max_active_jobs=2)
    first, created, position = store.add_job(100, "2330", bot.MODE_H63)
    assert created is True
    assert position == 1
    duplicate, created, _ = store.add_job(101, "2330", bot.MODE_H63)
    assert created is False
    assert duplicate["jobId"] == first["jobId"]
    second, created, position = store.add_job(102, "台積電", bot.MODE_H1)
    assert created is True
    assert position == 2
    with pytest.raises(bot.QueueFullError):
        store.add_job(103, "2317", bot.MODE_BOTH)
    reloaded = bot.CommandStateStore(state_path, max_active_jobs=2)
    assert reloaded.get_job(first["jobId"])["query"] == "2330"
    assert reloaded.get_job(second["jobId"])["mode"] == bot.MODE_H1


def test_state_store_recovers_interrupted_job(tmp_path: Path) -> None:
    store = bot.CommandStateStore(tmp_path / "state.json", max_active_jobs=3)
    job, _, _ = store.add_job(200, "2330", bot.MODE_H63)
    store.update_job(job["jobId"], status="running")
    interrupted = store.recover_interrupted_jobs()
    assert interrupted[0]["status"] == "failed"
    assert store.get_job(job["jobId"])["status"] == "failed"


def test_analysis_commands_are_argument_lists_without_shell_text(tmp_path: Path) -> None:
    resolver = bot.build_resolver_command("台積電", TEST_ANALYSIS_PYTHON)
    analysis = bot.build_analysis_command(
        bot.MODE_H63, "2330", tmp_path, TEST_ANALYSIS_PYTHON, HEAD_SHA
    )
    assert resolver[0] == TEST_ANALYSIS_PYTHON
    assert resolver[resolver.index("--resolve-stock") + 1] == "台積電"
    assert resolver[1:3] == [str(bot.ANALYSIS_ENTRY_SCRIPT), bot.MODE_H63]
    assert analysis[analysis.index("--stock") + 1] == "2330"
    assert analysis[analysis.index("--output-dir") + 1] == str(tmp_path)
    assert not any("cmd.exe" in part.lower() for part in analysis)


def test_constructor_stays_resident_when_c0_binding_is_unavailable(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    calls = 0

    def fail_c0_environment() -> tuple[dict[str, str], dict[str, object]]:
        nonlocal calls
        calls += 1
        raise bot.AnalysisProcessError("完全讀不到已發布資料")

    monkeypatch.setattr(bot, "c0_analysis_environment", fail_c0_environment)
    monkeypatch.setattr(bot, "STATE_PATH", tmp_path / "state.json")

    instance = bot.TelegramStockCommandBot(
        {
            "TELEGRAM_BOT_TOKEN": "123456:abcdefghijklmnopqrstuvwxyz_ABCDE",
            "TELEGRAM_CHAT_ID": "998877",
        }
    )

    assert calls == 0
    assert isinstance(instance.client, bot.TelegramClient)
    assert instance.work_queue.empty()
    assert instance.stop_event.is_set() is False
    assert instance.worker is None
    assert instance.heartbeat_data["status"] == "created"
    assert instance.analysis_env == {}
    assert instance.c0_binding == {}
    assert instance._job_c0_binding is None
    assert instance._job_version_labels == []


@pytest.mark.parametrize(
    "error",
    [
        bot.AnalysisProcessError("完全讀不到已發布資料：head missing"),
        bot.C0BindingIntegrityError("C0 已發布資料或模型完整性驗證失敗：daily_immutable_head_digest_mismatch"),
    ],
    ids=["unreadable_head", "integrity"],
)
def test_job_reports_unreadable_or_tampered_published_data_without_running_resolver(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, error: Exception
) -> None:
    calls = 0

    def fail_c0_environment() -> tuple[dict[str, str], dict[str, object]]:
        nonlocal calls
        calls += 1
        raise error

    monkeypatch.setattr(bot, "c0_analysis_environment", fail_c0_environment)
    monkeypatch.setattr(bot, "STATE_PATH", tmp_path / "state.json")

    class FakeStore:
        def get_job(self, job_id: str) -> dict[str, object] | None:
            assert job_id == "tg-1"
            return {"jobId": "tg-1", "status": "queued", "query": "2330", "mode": bot.MODE_H63}

        def update_job(self, job_id: str, **fields: object) -> None:
            updates.append({"job_id": job_id, **fields})

    class FakeClient:
        def send_message(self, text: str) -> None:
            messages.append(text)

    updates: list[dict[str, object]] = []
    messages: list[str] = []
    instance = bot.TelegramStockCommandBot(
        {
            "TELEGRAM_BOT_TOKEN": "123456:abcdefghijklmnopqrstuvwxyz_ABCDE",
            "TELEGRAM_CHAT_ID": "998877",
        }
    )
    instance.store = FakeStore()
    instance.client = FakeClient()
    resolver_calls = 0

    def unexpected_resolver(query: str) -> list[bot.StockMatch]:
        nonlocal resolver_calls
        resolver_calls += 1
        raise AssertionError(f"resolver must not run: {query}")

    instance.resolve_stock = unexpected_resolver

    instance.process_job("tg-1")

    failed_update = next(item for item in updates if item.get("status") == "failed")
    assert calls == 1
    assert resolver_calls == 0
    assert failed_update["error"] == str(error)
    assert any("分析失敗" in message for message in messages)
    assert instance._job_c0_binding is None
    assert not any("c0PublishedModelBinding" in item for item in updates)


@pytest.mark.parametrize("mode", [bot.MODE_H63, bot.MODE_H1, bot.MODE_BOTH])
def test_next_job_pins_the_binding_read_when_it_starts(
    mode: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    current = _c0_binding("2026-08-24", fingerprint="e" * 64, head="c" * 64)
    monkeypatch.setattr(bot, "c0_analysis_environment", lambda: ({"SYSTEMROOT": r"C:\Windows"}, dict(current)))

    updates: list[dict[str, object]] = []
    resolver_observations: list[tuple[str, object, bool, str | None]] = []

    class FakeStore:
        def get_job(self, job_id: str) -> dict[str, object] | None:
            assert job_id == "tg-1"
            return {"jobId": "tg-1", "status": "queued", "query": "3711", "mode": mode}

        def update_job(self, job_id: str, **fields: object) -> None:
            updates.append({"job_id": job_id, **fields})

    class FakeClient:
        def send_message(self, text: str) -> None:
            del text

    instance = bot.TelegramStockCommandBot.__new__(bot.TelegramStockCommandBot)
    instance.analysis_env = {"SYSTEMROOT": r"C:\Windows"}
    instance.c0_binding = _c0_binding("2026-08-21")  # read at service start
    instance.analysis_secret_values = ()
    instance.store = FakeStore()
    instance.client = FakeClient()
    instance.resolve_stock = lambda query: (
        resolver_observations.append(
            (query, instance._job_c0_binding["asof"], instance._job_read_at is not None,
             bot.c0_binding_head_sha256(instance._job_c0_binding))
        )
        or []
    )

    instance.process_job("tg-1")

    assert resolver_observations == [("3711", "2026-08-24", True, "c" * 64)]
    pinned = next(item for item in updates if "c0PublishedModelBinding" in item)
    assert pinned["reportClass"] == "C0"
    assert pinned["c0PublishedModelBinding"] == current
    assert pinned["c0JobDataTime"] == {
        "asof": "2026-08-24", "snapshot_fingerprint": "e" * 64, "published_at": "2026-08-24T15:40:00+08:00",
    }
    assert bot.c0_binding_head_sha256(pinned["c0PublishedModelBinding"]) == "c" * 64
    assert instance._job_c0_binding is None  # Terminal jobs no longer retain a source reader.
    assert updates[-1]["status"] == "needs_input"


def test_binding_failure_falls_back_to_the_published_head_and_only_unreadable_head_or_tamper_fails(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import stock_daily_contract as contract

    class Head:
        def descriptor(self) -> dict[str, object]:
            return {
                "snapshot_fingerprint": "e" * 64,
                "price_actual_max_date": "2026-08-21",
                "sources": [{"manifest_sha256": HEAD_SHA, "published_at": "2026-08-21T15:40:00+08:00"}],
            }

    def binding_unavailable(*_: object, **__: object) -> dict[str, object]:
        raise OSError("model pointer unreadable")

    monkeypatch.setattr(contract, "c0_runtime_binding", binding_unavailable)
    monkeypatch.setattr(contract, "_current_c0_sources", lambda: Head())
    environment, binding = bot.c0_analysis_environment()
    assert binding["schema"] == bot.C0_BINDING_UNAVAILABLE_SCHEMA
    assert binding["asof"] == "2026-08-21" and binding["snapshot_fingerprint"] == "e" * 64
    assert "OSError: model pointer unreadable" in binding["binding_error"]
    assert environment[bot.CURRENT_NETWORK_DISABLED_ENV] == "1"

    # A job started on the head-only binding runs on the data actually read and is labelled.
    instance = bot.TelegramStockCommandBot.__new__(bot.TelegramStockCommandBot)
    instance.analysis_secret_values = ()
    instance._job_version_labels = []
    instance.select_job_source()
    assert instance._job_c0_binding == binding
    assert [item["code"] for item in instance._job_version_labels] == ["c0_runtime_binding_unavailable"]
    assert bot.c0_binding_head_sha256(instance._job_c0_binding) == HEAD_SHA

    def head_gone() -> object:
        raise FileNotFoundError("no published head")

    monkeypatch.setattr(contract, "_current_c0_sources", head_gone)
    with pytest.raises(bot.AnalysisProcessError, match="完全讀不到已發布資料") as raised:
        bot.c0_analysis_environment()
    assert not isinstance(raised.value, bot.C0BindingIntegrityError)

    def tampered(*_: object, **__: object) -> dict[str, object]:
        raise contract.C0IntegrityError("daily_immutable_head_digest_mismatch")

    monkeypatch.setattr(contract, "c0_runtime_binding", tampered)
    monkeypatch.setattr(contract, "_current_c0_sources", lambda: pytest.fail("tamper must never fall back to the head"))
    with pytest.raises(bot.C0BindingIntegrityError, match="daily_immutable_head_digest_mismatch"):
        bot.c0_analysis_environment()


@pytest.mark.parametrize("mode", [bot.MODE_H63, bot.MODE_H1])
def test_run_analysis_mode_scopes_output_and_report_search_to_dstock_single(
    mode: str, tmp_path: Path, monkeypatch: pytest.MonkeyPatch, fake_c0_estimate_validation
) -> None:
    dstock_single = tmp_path / "dstock_single"
    jobs_root = dstock_single / "telegram_jobs"
    monkeypatch.setattr(bot, "DSTOCK_SINGLE_ROOT", dstock_single)
    monkeypatch.setattr(bot, "JOBS_DIR", jobs_root)
    commands: list[list[str]] = []
    messages: list[str] = []
    horizon = bot.MODE_HORIZONS[mode]

    class FakeProcess:
        pid = 1201
        returncode = 0

        def poll(self) -> int:
            return self.returncode

    def fake_popen(command: list[str], **kwargs: object) -> FakeProcess:
        commands.append(command)
        output_dir = Path(command[command.index("--output-dir") + 1])
        pdf_path, json_path, _ = _write_c0_report(output_dir, _c0_estimate_payload(horizon=horizon))
        output_stream = kwargs.get("stdout")
        assert hasattr(output_stream, "write")
        output_stream.write(f"PDF：{pdf_path}\nJSON：{json_path}\n")
        output_stream.flush()
        (jobs_root / "outside.pdf").write_bytes(b"wrong-report")
        return FakeProcess()

    class FakeStore:
        def update_job(self, job_id: str, **fields: object) -> None:
            del job_id, fields

    class FakeClient:
        def send_message(self, text: str) -> None:
            messages.append(text)

    monkeypatch.setattr(bot.subprocess, "Popen", fake_popen)
    monkeypatch.setattr(bot, "resolve_analysis_python", lambda _: TEST_ANALYSIS_PYTHON)
    instance = _c0_job_instance()
    refresh_calls: list[str] = []
    instance.client = FakeClient()
    instance.heartbeat_seconds = 60
    instance.analysis_timeout_seconds = 600
    instance.stop_event = threading.Event()
    instance.current_process_lock = threading.Lock()
    instance.current_process = None
    instance.store = FakeStore()
    instance.refresh_analysis_environment = lambda: refresh_calls.append(mode)
    instance.write_heartbeat = lambda **_: None

    report = instance.run_analysis_mode("tg-1", STOCK_2330, mode)

    assert len(commands) == 1
    output_dir = Path(commands[0][commands[0].index("--output-dir") + 1])
    assert output_dir.is_relative_to(dstock_single)
    assert output_dir == jobs_root / "tg-1" / output_dir.name
    assert output_dir.name.startswith(f"{mode}_")
    # The child analyses the head the job started on.
    assert commands[0][-2:] == ["--pinned-head-sha256", HEAD_SHA]
    assert report.pdf_path == (output_dir / "result.pdf").resolve()
    assert report.pdf_path != (jobs_root / "outside.pdf").resolve()
    assert report.declared_paths == (report.pdf_path, report.json_path)
    assert not list(output_dir.glob(".analysis-*"))
    assert not (output_dir / "analysis.log").exists()
    assert f"2330 台積電｜C0 H{horizon} 收盤估計" in report.caption
    # Re-read before launch and before validation; neither may stop the job.
    assert refresh_calls == [mode, mode]
    assert fake_c0_estimate_validation[0]["expected_snapshot_fingerprint"] == "f" * 64
    assert fake_c0_estimate_validation[0]["expected_runtime_binding"] == instance._job_c0_binding
    assert instance._job_version_labels == []
    assert messages == []


def test_validate_generated_report_reads_formal_json_bytes_once(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, fake_c0_estimate_validation
) -> None:
    output_dir = tmp_path / "job"
    pdf_path, json_path, json_bytes = _write_c0_report(output_dir, _c0_estimate_payload(), bom=True)

    original_read_bytes = Path.read_bytes
    original_read_text = Path.read_text
    json_read_count = 0

    def track_json_read(path: Path, *args: object, **kwargs: object) -> bytes:
        nonlocal json_read_count
        if path.resolve() == json_path.resolve():
            json_read_count += 1
        return original_read_bytes(path, *args, **kwargs)

    def reject_json_text_read(path: Path, *args: object, **kwargs: object) -> str:
        if path.resolve() == json_path.resolve():
            raise AssertionError("formal JSON must use the bytes read")
        return original_read_text(path, *args, **kwargs)

    monkeypatch.setattr(Path, "read_bytes", track_json_read)
    monkeypatch.setattr(Path, "read_text", reject_json_text_read)

    labels: list[dict[str, object]] = []
    _, payload, _, json_sha256 = bot._validate_generated_report(
        bot.MODE_H63, STOCK_2330, output_dir, pdf_path, json_path, _c0_binding(), labels,
    )

    assert payload["stock"] == {"stock_id": "2330", "stock_name": "台積電"}
    assert json_read_count == 1
    assert json_sha256 == hashlib.sha256(json_bytes).hexdigest()
    assert labels == []


def test_second_mode_labels_newer_data_or_model_change_instead_of_stopping(
    caplog: pytest.LogCaptureFixture,
) -> None:
    start = _c0_binding()
    newer = _c0_binding("2026-08-24", fingerprint="e" * 64, head="c" * 64)
    newer_model = _c0_binding("2026-08-24", fingerprint="e" * 64, head="c" * 64, model_sha="b" * 64)
    instance = _c0_job_instance(start)
    sequence = [start, newer, newer_model]
    calls = 0

    def refresh_analysis_environment() -> None:
        nonlocal calls
        instance.c0_binding = dict(sequence[min(calls, len(sequence) - 1)])
        calls += 1

    instance.refresh_analysis_environment = refresh_analysis_environment

    instance.refresh_analysis_environment_for_mode("tg-1", bot.MODE_H63)
    assert instance._job_version_labels == []
    with caplog.at_level("WARNING", logger=bot.LOGGER.name):
        instance.refresh_analysis_environment_for_mode("tg-1", bot.MODE_H1)
    assert [item["code"] for item in instance._job_version_labels] == ["newer_data_during_job"]
    label = instance._job_version_labels[0]
    assert label["recorded"]["asof"] == "2026-08-21" and label["actual"]["asof"] == "2026-08-24"
    assert "沿用開始時資料完成" in caplog.text
    instance.refresh_analysis_environment_for_mode("tg-1", bot.MODE_H1)
    assert [item["code"] for item in instance._job_version_labels] == [
        "newer_data_during_job", "runtime_contract_changed_during_job",
    ]
    assert instance._job_c0_binding == start  # the job keeps the data it started on

    def unreadable() -> None:
        raise bot.AnalysisProcessError("head temporarily locked")

    instance.refresh_analysis_environment = unreadable
    instance.refresh_analysis_environment_for_mode("tg-1", bot.MODE_H1)
    assert instance._job_version_labels[-1]["code"] == "current_binding_unreadable_during_job"

    def tampered() -> None:
        raise bot.C0BindingIntegrityError("C0 已發布資料或模型完整性驗證失敗：digest mismatch")

    instance.refresh_analysis_environment = tampered
    with pytest.raises(bot.C0BindingIntegrityError):
        instance.refresh_analysis_environment_for_mode("tg-1", bot.MODE_H1)

    instance._job_c0_binding = None
    with pytest.raises(bot.AnalysisProcessError, match="尚未建立 pin"):
        instance.refresh_analysis_environment_for_mode("tg-1", bot.MODE_H1)


@pytest.mark.parametrize("change", ["integrity", "newer_data"])
def test_process_job_blocks_upload_only_on_an_integrity_failure_before_upload(
    change: str,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
    fake_c0_estimate_validation,
) -> None:
    dstock_single = tmp_path / "dstock_single"
    jobs_root = dstock_single / "telegram_jobs"
    monkeypatch.setattr(bot, "DSTOCK_SINGLE_ROOT", dstock_single)
    monkeypatch.setattr(bot, "JOBS_DIR", jobs_root)
    commands: list[list[str]] = []
    sent_documents: list[tuple[Path, str]] = []
    updates: list[dict[str, object]] = []
    appended_modes: list[str] = []

    class FakeProcess:
        pid = 1203
        returncode = 0

        def poll(self) -> int:
            return self.returncode

    def fake_popen(command: list[str], **kwargs: object) -> FakeProcess:
        commands.append(command)
        horizon = bot.MODE_HORIZONS[command[3]]
        output_dir = Path(command[command.index("--output-dir") + 1])
        pdf_path, json_path, _ = _write_c0_report(output_dir, _c0_estimate_payload(horizon=horizon))
        output_stream = kwargs.get("stdout")
        assert hasattr(output_stream, "write")
        output_stream.write(f"PDF：{pdf_path}\nJSON：{json_path}\n")
        output_stream.flush()
        return FakeProcess()

    class FakeStore:
        def get_job(self, job_id: str) -> dict[str, object] | None:
            assert job_id == "tg-3"
            return {"jobId": "tg-3", "status": "queued", "query": "2330", "mode": bot.MODE_BOTH}

        def update_job(self, job_id: str, **fields: object) -> None:
            updates.append({"job_id": job_id, **fields})

        def append_report(self, job_id: str, report: bot.GeneratedReport) -> None:
            del job_id
            appended_modes.append(report.mode)

    class FakeClient:
        def send_message(self, text: str) -> None:
            del text

        def send_chat_action(self, action: str) -> None:
            del action

        def send_document(self, path: Path, caption: str) -> dict[str, int]:
            sent_documents.append((path, caption))
            return {"message_id": len(sent_documents)}

    monkeypatch.setattr(bot.subprocess, "Popen", fake_popen)
    monkeypatch.setattr(bot, "resolve_analysis_python", lambda _: TEST_ANALYSIS_PYTHON)
    instance = _c0_job_instance()
    instance.client = FakeClient()
    instance.heartbeat_seconds = 60
    instance.analysis_timeout_seconds = 600
    instance.stop_event = threading.Event()
    instance.current_process_lock = threading.Lock()
    instance.current_process = None
    instance.store = FakeStore()
    instance.write_heartbeat = lambda **_: None
    instance.resolve_stock = lambda query: [bot.StockMatch(query, "台積電", "exact_id", 1.0)]
    newer = _c0_binding("2026-08-24", fingerprint="e" * 64, head="c" * 64)
    refresh_calls = 0

    def refresh_analysis_environment(*, retain_source_lease: bool = False) -> None:
        nonlocal refresh_calls
        refresh_calls += 1
        assert retain_source_lease is (refresh_calls == 1)
        # 1 job start; H63: 2 launch, 3 validation, 4 pre-upload; H1: 5, 6, 7 (pre-upload)
        if refresh_calls == 7:
            if change == "integrity":
                raise bot.C0BindingIntegrityError(
                    "C0 已發布資料或模型完整性驗證失敗：daily_immutable_head_digest_mismatch"
                )
            instance.c0_binding = dict(newer)

    instance.refresh_analysis_environment = refresh_analysis_environment

    with caplog.at_level("ERROR", logger=bot.LOGGER.name):
        instance.process_job("tg-3")

    assert refresh_calls == 7
    assert len(commands) == 2
    assert [command[0] for command in commands] == [TEST_ANALYSIS_PYTHON] * 2
    assert all(command[-2:] == ["--pinned-head-sha256", HEAD_SHA] for command in commands)
    if change == "integrity":
        assert appended_modes == [bot.MODE_H63]
        assert len(sent_documents) == 1
        unsent_update = next(item for item in updates if "unsentModes" in item)
        assert unsent_update["unsentModes"] == [bot.MODE_H1]
        assert any(item.get("status") == "failed" for item in updates)
        assert "unsent_modes" in caplog.text
    else:
        # Newer data published during the job is a label; the report is sent and says so.
        assert appended_modes == [bot.MODE_H63, bot.MODE_H1]
        assert len(sent_documents) == 2
        assert "執行期間已有較新資料 20260824" in sent_documents[1][1]
        assert "執行期間已有較新資料" not in sent_documents[0][1]
        labelled = [item for item in updates if item.get("versionLabels")]
        assert labelled and labelled[-1]["versionLabels"][0]["code"] == "newer_data_during_job"
        assert any(item.get("status") == "complete" for item in updates)
        assert not any("unsentModes" in item for item in updates)


def test_process_job_progress_edits_same_start_message_and_failure_does_not_abort(
    tmp_path: Path,
) -> None:
    pdf_path = tmp_path / "result.pdf"
    pdf_path.write_bytes(b"%PDF-1.7\n%%EOF\n")
    report = bot.GeneratedReport(bot.MODE_H63, pdf_path, None, "報告 caption")
    messages: list[str] = []
    documents: list[tuple[Path, str]] = []
    evidence_kinds: list[str] = []
    progress_calls: list[tuple[str, dict[str, str]]] = []
    updates: list[dict[str, object]] = []
    appended_reports: list[bot.GeneratedReport] = []

    class FakeStore:
        def get_job(self, job_id: str) -> dict[str, object] | None:
            assert job_id == "tg-success"
            return {"jobId": job_id, "status": "queued", "query": "2330", "mode": bot.MODE_H63}

        def update_job(self, job_id: str, **fields: object) -> None:
            updates.append({"job_id": job_id, **fields})

        def append_report(self, job_id: str, value: bot.GeneratedReport) -> None:
            assert job_id == "tg-success"
            appended_reports.append(value)

    class FakeClient:
        def send_message(self, text: str) -> dict[str, int]:
            messages.append(text)
            return {"message_id": 71}

        def send_chat_action(self, action: str) -> None:
            assert action == "upload_document"

        def send_document(self, path: Path, caption: str) -> dict[str, int]:
            documents.append((path, caption))
            return {"message_id": 77}

    class FailingBoundedProgressClient:
        timeout_seconds = 5
        max_attempts = 1

        def _post_fields(self, method: str, fields: dict[str, str]) -> dict[str, object]:
            progress_calls.append((method, fields))
            raise bot.TelegramApiError("progress transport unavailable")

    instance = _c0_job_instance()
    instance.client = FakeClient()
    instance.progress_client = FailingBoundedProgressClient()
    instance.chat_id = "998877"
    instance.store = FakeStore()
    instance.select_job_source = lambda: None
    instance.resolve_stock = lambda query: [bot.StockMatch(query, "台積電", "exact_id", 1.0)]

    def run_analysis_mode(*args: object) -> bot.GeneratedReport:
        callback = args[3]
        callback({
            "phase": "data_prepare",
            "mode": bot.MODE_H63,
            "completed": 0,
            "total": 1,
            "detail": "untrusted detail must not be shown",
            "preparation_elapsed_seconds": 120,
        })
        callback({
            "phase": "model",
            "mode": bot.MODE_H63,
            "preparation_elapsed_seconds": 120,
            "model_elapsed_seconds": 420,
        })
        return report

    instance.run_analysis_mode = run_analysis_mode
    instance.validate_report_before_upload = lambda job_id, stock, value: value
    instance._record_delivery_evidence = lambda *args, **_: evidence_kinds.append(args[3])

    instance.process_job("tg-success")

    assert messages == [
        f"開始處理｜2330\n類型：{bot.MODE_LABELS[bot.MODE_H63]}\n工作編號：tg-success"
    ]
    assert len(progress_calls) == 2
    method, fields = progress_calls[0]
    assert method == "editMessageText"
    assert fields["chat_id"] == "998877"
    assert fields["message_id"] == "71"
    assert "階段：資料準備" in fields["text"]
    assert "已完成項目：0/1" in fields["text"]
    assert "untrusted detail" not in fields["text"]
    terminal_method, terminal_fields = progress_calls[1]
    assert terminal_method == "editMessageText"
    assert terminal_fields["message_id"] == fields["message_id"]
    assert "階段：工作完成" in terminal_fields["text"]
    assert "資料準備時間：2 分鐘" in terminal_fields["text"]
    assert "模型分析時間：7 分鐘" in terminal_fields["text"]
    assert documents == [(pdf_path, report.caption)]
    assert evidence_kinds == ["document"]
    assert appended_reports == [report]
    assert any(item.get("status") == "complete" for item in updates)


def test_failed_analysis_logs_bounded_safe_summary_from_output_log(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    dstock_single = tmp_path / "dstock_single"
    jobs_root = dstock_single / "telegram_jobs"
    monkeypatch.setattr(bot, "DSTOCK_SINGLE_ROOT", dstock_single)
    monkeypatch.setattr(bot, "JOBS_DIR", jobs_root)
    error_log = "TELEGRAM_BOT_TOKEN=do-not-send\n" + ("x" * 6000) + "\nanalysis failed\n"
    commands: list[list[str]] = []
    error_calls: list[tuple[object, ...]] = []
    log_paths: list[Path] = []
    safe_tails: list[str] = []
    state: dict[str, object] = {}

    class FakeProcess:
        pid = 1202
        returncode = 17

        def __init__(self, log_path: Path) -> None:
            self.log_path = log_path
            self.poll_count = 0

        def poll(self) -> int | None:
            self.poll_count += 1
            if self.poll_count == 1:
                return None
            self.log_path.write_text(error_log, encoding="utf-8")
            safe_tails.append(bot.read_safe_log_tail(self.log_path, ("do-not-send",)))
            return self.returncode

    def fake_popen(command: list[str], **kwargs: object) -> FakeProcess:
        commands.append(command)
        log_path = Path(kwargs["stdout"].name)  # type: ignore[union-attr]
        log_paths.append(log_path)
        return FakeProcess(log_path)

    class FakeStore:
        def update_job(self, job_id: str, **fields: object) -> None:
            del job_id
            state.update(fields)

    class FakeClient:
        def send_message(self, text: str) -> None:
            del text

    monkeypatch.setattr(bot.subprocess, "Popen", fake_popen)
    monkeypatch.setattr(bot, "resolve_analysis_python", lambda _: TEST_ANALYSIS_PYTHON)
    monkeypatch.setattr(bot.time, "sleep", lambda _: None)
    monkeypatch.setattr(
        bot.LOGGER,
        "error",
        lambda *args, **kwargs: error_calls.append(args),
    )
    instance = _c0_job_instance()
    instance.client = FakeClient()
    instance.heartbeat_seconds = 60
    instance.analysis_timeout_seconds = 600
    instance.stop_event = threading.Event()
    instance.current_process_lock = threading.Lock()
    instance.current_process = None
    instance.store = FakeStore()
    instance.analysis_secret_values = ("do-not-send",)
    instance.write_heartbeat = lambda **_: None

    with pytest.raises(bot.AnalysisProcessError) as raised:
        instance.run_analysis_mode("tg-2", STOCK_2330, bot.MODE_H63)

    output_dir = Path(commands[0][commands[0].index("--output-dir") + 1])
    assert not log_paths[0].exists() and not log_paths[0].parent.exists()
    assert not output_dir.exists()
    assert state["analysisLog"] is None
    assert len(error_calls) == 1
    summary = str(error_calls[0][-1])
    assert summary == safe_tails[-1]
    truncation_marker = "[分析紀錄末段已截斷]\n"
    assert len(summary) <= bot.ANALYSIS_ERROR_TAIL_MAX_CHARS + len(truncation_marker)
    assert len(summary.encode("utf-8")) <= (
        bot.ANALYSIS_ERROR_TAIL_MAX_BYTES + len(truncation_marker.encode("utf-8"))
    )
    assert "analysis failed" in summary
    error_text = str(raised.value)
    for unsafe_text in ("do-not-send", "TELEGRAM_BOT_TOKEN=do-not-send"):
        assert unsafe_text not in summary
        assert unsafe_text not in error_text
        assert unsafe_text not in str(state["error"])
    assert "analysis failed" in str(state["error"])
    assert len(str(state["error"])) <= bot.ANALYSIS_ERROR_TAIL_MAX_CHARS


def test_report_captions_use_expected_units() -> None:
    h63 = bot.report_caption(bot.MODE_H63, STOCK_2330, _c0_estimate_payload())
    assert h63.splitlines()[0] == "2330 台積電｜C0 H63 收盤估計"
    assert "上漲機率：61.23%；預估報酬：+12.34%" in h63
    assert all(text not in h63 for text in ("分母：", "MAE：", "方法：", "模型：", "需求：", "base_only"))
    assert "第63交易日收盤" in h63
    assert "效果未知" in h63 and "歷史PIT" in h63
    assert h63.endswith(bot.REPORT_CAPTION_DISCLAIMER)
    assert len(h63) <= bot.TELEGRAM_CAPTION_BUDGET
    h1 = bot.report_caption(
        bot.MODE_H1,
        STOCK_2330,
        _c0_estimate_payload(
            horizon=1,
            prediction={"prob_up": 0.584, "expected_return": 0.0123, "calibration_status": "base_only"},
            comparison_metrics={"direction_accuracy": 0.55},
        ),
    )
    assert "C0 H1 收盤估計" in h1
    assert "上漲機率：58.40%；預估報酬：+1.23%" in h1
    assert "比較方向率低於60%，不能視為可靠預測。" in h1
    with pytest.raises(bot.AnalysisProcessError, match="期限與請求不一致"):
        bot.report_caption(bot.MODE_H1, STOCK_2330, _c0_estimate_payload(horizon=63))
    with pytest.raises(bot.AnalysisProcessError, match="只接受 current C0"):
        bot.report_caption(bot.MODE_H63, STOCK_2330, _c0_estimate_payload(report_schema_version="single-stock-formal-estimate-v3"))
    with pytest.raises(bot.AnalysisProcessError, match="只接受 current C0"):
        bot.report_caption("three_month", STOCK_2330, _c0_estimate_payload())


def test_telegram_client_get_updates_unwraps_result(monkeypatch: pytest.MonkeyPatch) -> None:
    from telegram_report_sender import TelegramClient

    client = TelegramClient("123456:abcdefghijklmnopqrstuvwxyz_ABCDE", "998877")
    captured: dict[str, str] = {}

    def fake_post_fields(method: str, fields: dict[str, str]) -> dict[str, object]:
        assert method == "getUpdates"
        captured.update(fields)
        return {"result": [{"update_id": 5}, "invalid"]}

    monkeypatch.setattr(client, "_post_fields", fake_post_fields)
    assert client.get_updates(offset=4, timeout_seconds=20) == [{"update_id": 5}]
    assert captured["offset"] == "4"
    assert captured["timeout"] == "20"


def test_job_state_is_valid_json_after_concurrent_style_updates(tmp_path: Path) -> None:
    store = bot.CommandStateStore(tmp_path / "state.json", max_active_jobs=4)
    job, _, _ = store.add_job(300, "2330", bot.MODE_BOTH)
    store.update_job(job["jobId"], status="running", resolvedStock={"code": "2330", "name": "台積電"})
    report_path = tmp_path / "report.pdf"
    report_path.write_bytes(b"pdf")
    store.append_report(job["jobId"], bot.GeneratedReport(bot.MODE_H63, report_path, None, "caption"))
    payload = json.loads((tmp_path / "state.json").read_text(encoding="utf-8"))
    assert payload["jobs"][0]["reports"][0]["mode"] == bot.MODE_H63


def test_logging_supports_windowless_python(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(bot, "LOG_DIR", tmp_path)
    monkeypatch.setattr(bot.sys, "stdout", None)
    bot.setup_logging()
    bot.LOGGER.info("windowless logging")
    for handler in bot.LOGGER.handlers:
        handler.flush()
    assert not list(tmp_path.iterdir())
    assert len(bot.LOGGER.handlers) == 1
    assert isinstance(bot.LOGGER.handlers[0], bot.logging.NullHandler)


@pytest.mark.parametrize("console", [None, False, True])
def test_single_stock_logger_never_opens_disk_or_redirected_service_output(tmp_path, monkeypatch, console):
    class Stream(io.StringIO):
        def isatty(self):
            return bool(console)

    output, errors = Stream(), io.StringIO()
    logger = bot.logging.getLogger("storage-policy-fixture-" + tmp_path.name)
    old_log = tmp_path / "stock_command_bot_existing.log"
    old_log.write_bytes(b"preserved monthly log\n")
    monkeypatch.setattr(bot, "LOGGER", logger)
    monkeypatch.setattr(bot, "LOG_DIR", tmp_path)
    monkeypatch.setattr(bot.sys, "stdout", output if console is not None else None)
    monkeypatch.setattr(bot.sys, "stderr", errors)
    bot.setup_logging()
    bot.setup_logging()
    logger.warning("fixture status")
    assert len(logger.handlers) == 1 and logger.propagate is False
    assert not any(isinstance(handler, bot.logging.FileHandler) for handler in logger.handlers)
    assert ("fixture status" in output.getvalue()) is (console is True)
    assert errors.getvalue() == ""
    assert old_log.read_bytes() == b"preserved monthly log\n"
    assert list(tmp_path.iterdir()) == [old_log]


@pytest.mark.parametrize("outcome", [
    "success", "child-error", "validation-error", "declarations-error",
    "heartbeat-error", "final-heartbeat-error", "launch-error", "timeout", "unknown-output",
])
def test_analysis_temporary_log_cleanup_preserves_reports_and_existing_jobs(tmp_path, monkeypatch, outcome):
    root = tmp_path / "single"
    jobs = root / "telegram_jobs"
    monkeypatch.setattr(bot, "DSTOCK_SINGLE_ROOT", root)
    monkeypatch.setattr(bot, "JOBS_DIR", jobs)
    monkeypatch.setattr(bot, "resolve_analysis_python", lambda _: TEST_ANALYSIS_PYTHON)
    old_log = jobs / "old-failed-job" / "analysis.log"
    old_log.parent.mkdir(parents=True)
    old_log.write_bytes(b"old evidence must remain\n")

    class Clock:
        value = 0.0

        def monotonic(self):
            return self.value

        def sleep(self, seconds):
            self.value += seconds

    monkeypatch.setattr(bot, "time", Clock())
    logs, directories, processes, updates, progress = [], [], [], [], []

    class Process:
        pid = 4401

        def __init__(self):
            self.returncode = None if outcome in {"heartbeat-error", "timeout"} else (
                17 if outcome in {"child-error", "unknown-output"} else 0
            )
            self.terminated = False

        def poll(self):
            return self.returncode

        def terminate(self):
            self.terminated = True
            self.returncode = -15

        def wait(self, timeout=None):
            return self.returncode

        def kill(self):
            self.returncode = -9

    def popen(command, **kwargs):
        directory = Path(command[command.index("--output-dir") + 1])
        directories.append(directory)
        stream = kwargs["stdout"]
        logs.append(Path(stream.name))
        assert logs[-1].parent.parent == directory
        assert not (directory / "analysis.log").exists()
        assert kwargs["env"][bot.SINGLE_STOCK_PHASE_ENV] == "1"
        if outcome == "launch-error":
            raise OSError("fixture launch failed")
        if outcome in {"success", "validation-error", "declarations-error", "final-heartbeat-error"}:
            bundle = directory / "single_stock_2330_H63_fixture"
            bundle.mkdir()
            pdf, data = bundle / "result.pdf", bundle / "result.json"
            pdf.write_bytes(b"%PDF-1.7\n%%EOF\n")
            data.write_bytes(b'{"fixture":true}')
            for phase in ("data_check", "model", "pdf"):
                stream.write(bot.SINGLE_STOCK_PHASE_PREFIX.decode("ascii") + json.dumps({
                    "schema": bot.SINGLE_STOCK_PHASE_SCHEMA, "phase": phase, "stock_id": "2330",
                }) + "\n")
            stream.write(f"PDF：{pdf}\nJSON：{data}\n")
            if outcome == "declarations-error":
                stream.write(f"PDF：{pdf}\n")
        else:
            stream.write("token=fixture-secret\nfixture child error\n")
        if outcome == "unknown-output":
            (directory / "unknown.txt").write_bytes(b"do not delete")
        stream.flush()
        process = Process()
        processes.append(process)
        return process

    def validate(mode, stock, directory, pdf, data, *args):
        assert logs[-1].is_file()  # Parent validation still has the original declaration log.
        if outcome == "validation-error":
            raise bot.AnalysisProcessError("fixture validation failed")
        return data, {}, hashlib.sha256(pdf.read_bytes()).hexdigest(), hashlib.sha256(data.read_bytes()).hexdigest()

    def heartbeat(**fields):
        if ((outcome == "heartbeat-error" and fields.get("currentProcessId") is not None)
                or (outcome == "final-heartbeat-error" and fields.get("currentProcessId") is None)):
            raise PermissionError("fixture heartbeat denied")

    monkeypatch.setattr(bot.subprocess, "Popen", popen)
    monkeypatch.setattr(bot, "_validate_generated_report", validate)
    monkeypatch.setattr(bot, "report_caption", lambda *args: "fixture caption")
    instance = _c0_job_instance()
    instance.store = SimpleNamespace(update_job=lambda job_id, **fields: updates.append(fields))
    instance.analysis_timeout_seconds = 2 if outcome == "timeout" else 600
    instance.stop_event = threading.Event()
    instance.current_process_lock = threading.Lock()
    instance.current_process = None
    instance.heartbeat_lock = threading.Lock()
    instance.heartbeat_failure = None
    instance.write_heartbeat = heartbeat
    instance.analysis_secret_values = ("fixture-secret",)
    if outcome == "success":
        report = instance.run_analysis_mode("storage-job", STOCK_2330, bot.MODE_H63, progress.append)
        assert report.pdf_path.is_file() and report.json_path.is_file()
        assert report.declared_paths == (report.pdf_path, report.json_path)
        assert [item["phase"] for item in progress] == ["data_check", "model", "pdf"]
        assert all(".analysis-" not in str(value) for value in report.__dict__.values())
    else:
        expected = (PermissionError if outcome in {"heartbeat-error", "final-heartbeat-error"} else
                    OSError if outcome == "launch-error" else bot.AnalysisProcessError)
        with pytest.raises(expected):
            instance.run_analysis_mode("storage-job", STOCK_2330, bot.MODE_H63, progress.append)
        summary = next(fields["error"] for fields in reversed(updates) if "error" in fields)
        assert "fixture-secret" not in summary
        assert len(summary) <= bot.ANALYSIS_ERROR_TAIL_MAX_CHARS
    assert logs and all(not path.exists() and not path.parent.exists() for path in logs)
    assert not any(fields.get("analysisLog") for fields in updates)
    assert instance.current_process is None
    assert old_log.read_bytes() == b"old evidence must remain\n"
    if outcome in {"heartbeat-error", "timeout"}:
        assert processes[0].terminated
    if outcome in {"child-error", "heartbeat-error", "launch-error", "timeout"}:
        assert not directories[0].exists() and not (jobs / "storage-job").exists()
    if outcome == "unknown-output":
        assert (directories[0] / "unknown.txt").read_bytes() == b"do not delete"


def test_analysis_temp_cleanup_refuses_unknown_files_and_replaced_directory(tmp_path):
    output = tmp_path / "output"
    output.mkdir()
    with pytest.raises(OSError):
        with bot._owned_analysis_log(output) as (log, stream):
            unknown = log.parent / "foreign.txt"
            unknown.write_bytes(b"foreign")
    assert unknown.read_bytes() == b"foreign"
    assert not log.exists()

    with pytest.raises(bot.AnalysisProcessError, match="身分"):
        with bot._owned_analysis_log(output) as (other_log, stream):
            stream.close()
            moved = other_log.parent.with_name(other_log.parent.name + "-preserved")
            other_log.parent.rename(moved)
            other_log.parent.mkdir()
            replacement = other_log.parent / "analysis.log"
            replacement.write_bytes(b"replacement must remain")
    assert replacement.read_bytes() == b"replacement must remain"
    assert (moved / "analysis.log").exists()


def test_heartbeat_records_runtime_state(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    heartbeat_path = tmp_path / "heartbeat.json"
    monkeypatch.setattr(bot, "HEARTBEAT_PATH", heartbeat_path)
    instance = bot.TelegramStockCommandBot.__new__(bot.TelegramStockCommandBot)
    instance.heartbeat_lock = threading.Lock()
    instance.heartbeat_data = {"pid": 1, "status": "created"}

    instance.write_heartbeat(status="working", currentJobId="tg-1", currentMode="three_month")

    payload = json.loads(heartbeat_path.read_text(encoding="utf-8"))
    assert payload["pid"] == bot.os.getpid()
    assert payload["status"] == "working"
    assert payload["currentJobId"] == "tg-1"
    assert payload["currentMode"] == "three_month"
    assert payload["updatedAt"]


def test_heartbeat_retries_bounded_permission_error(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    heartbeat_path = tmp_path / "heartbeat.json"
    monkeypatch.setattr(bot, "HEARTBEAT_PATH", heartbeat_path)
    monkeypatch.setattr(bot, "HEARTBEAT_WRITE_RETRY_SECONDS", 0)
    instance = bot.TelegramStockCommandBot.__new__(bot.TelegramStockCommandBot)
    instance.heartbeat_lock = threading.Lock()
    instance.heartbeat_data = {"pid": 1, "status": "created"}
    original_atomic_write = bot.atomic_write_json
    attempts = 0

    def flaky_atomic_write(path: Path, payload: dict[str, object]) -> None:
        nonlocal attempts
        attempts += 1
        if attempts < 3:
            raise PermissionError("heartbeat destination is temporarily locked")
        original_atomic_write(path, payload)

    monkeypatch.setattr(bot, "atomic_write_json", flaky_atomic_write)
    instance.write_heartbeat(status="working")

    assert attempts == 3
    assert json.loads(heartbeat_path.read_text(encoding="utf-8"))["status"] == "working"


def test_single_instance_lock_records_and_clears_owner(tmp_path: Path) -> None:
    lock_path = tmp_path / "listener.lock"
    owner_path = tmp_path / "listener.owner.json"
    with bot.SingleInstanceLock(lock_path, owner_path):
        record = json.loads(owner_path.read_text(encoding="utf-8"))
        assert record["processId"] == bot.os.getpid()
    assert not owner_path.exists()


def test_delivery_evidence_binds_lineage_and_never_writes_secret_or_chat_id(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    audit_path = tmp_path / "delivery.jsonl"
    monkeypatch.setattr(bot, "DELIVERY_AUDIT_PATH", audit_path)
    instance = _c0_job_instance()
    instance.client = bot.TelegramClient("123456:abcdefghijklmnopqrstuvwxyz_ABCDE", "998877")
    instance._job_version_labels = [{"code": "newer_data_during_job", "recorded": None, "actual": None, "detail": None}]
    report = tmp_path / "result.pdf"
    report.write_bytes(b"%PDF-1.7\n%%EOF\n")
    document = tmp_path / "result.json"
    document.write_bytes(b'{"report":"c0"}')
    generated = bot.GeneratedReport(
        bot.MODE_H63, report, document, "", hashlib.sha256(report.read_bytes()).hexdigest(),
        hashlib.sha256(document.read_bytes()).hexdigest(), tmp_path, (report, document),
    )

    instance._record_delivery_evidence(
        "tg-91",
        "2330",
        bot.MODE_H63,
        "document",
        {"message_id": 701},
        path=report,
        json_path=document,
        report=generated,
    )

    receipt_path = tmp_path / bot.DELIVERY_RECEIPT_NAME
    raw = receipt_path.read_text(encoding="utf-8")
    record = json.loads(raw)
    assert record["requestId"] == "tg-91"
    assert record["mode"] == bot.MODE_H63
    assert record["reportClass"] == "C0"
    assert record["modelSha256"] == _c0_binding()["model_binding"]["sha256"]
    assert record["headSha256"] == HEAD_SHA
    assert "c0PublishedModelBinding" not in record
    assert record["c0JobReadAt"] == "2026-08-21T16:00:00+08:00"
    assert [item["code"] for item in record["versionLabels"]] == ["newer_data_during_job"]
    assert record["historicalPITVerified"] is False and record["effectivenessStatus"] == "unknown"
    assert record["telegramMessageId"] == "701"
    assert record["fileSha256"] == hashlib.sha256(report.read_bytes()).hexdigest()
    assert record["jsonSha256"] == hashlib.sha256(document.read_bytes()).hexdigest()
    assert "123456:abcdefghijklmnopqrstuvwxyz_ABCDE" not in raw
    assert "998877" not in raw
    assert record["fileSize"] == report.stat().st_size
    assert record["jsonSize"] == document.stat().st_size
    assert not audit_path.exists()

    with pytest.raises(bot.TelegramApiError, match="message_id"):
        instance._record_delivery_evidence("tg-91", "2330", bot.MODE_H63, "document", {"ok": True}, path=report)
    instance._job_c0_binding = None
    with pytest.raises(bot.TelegramApiError, match="C0 binding"):
        instance._record_delivery_evidence("tg-91", "2330", bot.MODE_H63, "document", {"message_id": 702}, path=report)


@pytest.mark.parametrize("mutation", [None, "message", "pdf", "json", "receipt", "hardlink", "oversize"])
def test_report_delivery_receipt_is_create_only_and_never_appends_global_audit(tmp_path, monkeypatch, mutation):
    global_audit = tmp_path / "existing-delivery.jsonl"
    global_audit.write_bytes(b"historical ACK evidence\n")
    monkeypatch.setattr(bot, "DELIVERY_AUDIT_PATH", global_audit)
    bundle = tmp_path / "job" / "single_stock_2330_H63_fixture"
    bundle.mkdir(parents=True)
    pdf, data = bundle / "result.pdf", bundle / "result.json"
    pdf.write_bytes(b"%PDF-1.7\n%%EOF\n")
    data.write_bytes(b'{"storage_policy":"single-stock-read-only-inputs-v1"}')
    generated = bot.GeneratedReport(
        bot.MODE_H63, pdf, data, "fixture", hashlib.sha256(pdf.read_bytes()).hexdigest(),
        hashlib.sha256(data.read_bytes()).hexdigest(), bundle.parent, (pdf, data),
    )
    instance = _c0_job_instance()
    instance.client = bot.TelegramClient("123456:abcdefghijklmnopqrstuvwxyz_ABCDE", "998877")
    instance._job_c0_binding["descriptor"] = {"large_metadata": "do-not-copy" * 200000}
    instance._job_c0_binding["private_token"] = "fixture-never-store"
    instance._job_version_labels = [{"code": "newer_data_during_job", "detail": "token=fixture-secret"}]
    instance.analysis_secret_values = ("fixture-secret", "fixture-never-store")
    times = iter(["2026-10-24T04:00:00+08:00", "2026-10-24T04:01:00+08:00"])
    monkeypatch.setattr(bot, "now_iso", lambda: next(times))

    def record(message_id=701):
        instance._record_delivery_evidence(
            "tg-storage", "2330", bot.MODE_H63, "document", {"message_id": message_id},
            path=pdf, json_path=data, report=generated,
        )

    record()
    receipt = bundle / bot.DELIVERY_RECEIPT_NAME
    original = receipt.read_bytes()
    original_stat = receipt.stat()
    proof = json.loads(original)
    assert proof["sourceSha256"] == bot.SOURCE_SHA256
    assert proof["headSha256"] == HEAD_SHA and proof["modelSha256"] == "a" * 64
    assert proof["jsonSize"] == data.stat().st_size
    assert len(original) < bot.DELIVERY_RECEIPT_MAX_BYTES
    assert all(secret not in original.decode("utf-8") for secret in (
        "do-not-copy", "fixture-secret", "fixture-never-store", "998877",
        "123456:abcdefghijklmnopqrstuvwxyz_ABCDE",
    ))
    message_id = 702 if mutation == "message" else 701
    if mutation in {"pdf", "json"}:
        path = pdf if mutation == "pdf" else data
        path.write_bytes(path.read_bytes() + b" ")
    elif mutation == "receipt":
        altered = dict(proof, headSha256="e" * 64)
        receipt.write_text(json.dumps(altered), encoding="utf-8", newline="\n")
    elif mutation == "hardlink":
        os.link(receipt, tmp_path / "preserved-receipt-link.json")
    elif mutation == "oversize":
        instance._job_version_labels = [{"code": "x", "detail": "x" * bot.DELIVERY_RECEIPT_MAX_BYTES}]
    before = receipt.read_bytes()
    if mutation is None:
        record()
        assert receipt.read_bytes() == original
        assert receipt.stat().st_mtime_ns == original_stat.st_mtime_ns
    else:
        with pytest.raises((bot.TelegramApiError, bot.AnalysisProcessError)):
            record(message_id)
        assert receipt.read_bytes() == before
    assert global_audit.read_bytes() == b"historical ACK evidence\n"
    assert not list(bundle.glob(".telegram-delivery-*.tmp"))


@pytest.mark.parametrize("message_id", [None, 0, -1, True, "701"])
def test_report_delivery_receipt_requires_real_message_identity(tmp_path, message_id):
    instance = _c0_job_instance()
    instance.client = bot.TelegramClient("123456:abcdefghijklmnopqrstuvwxyz_ABCDE", "998877")
    with pytest.raises(bot.TelegramApiError, match="message_id"):
        instance._record_delivery_evidence("tg-1", "2330", bot.MODE_H63, "document", {"message_id": message_id})
    assert not list(tmp_path.iterdir())


def test_report_delivery_receipt_cleans_only_its_own_failed_publication(tmp_path, monkeypatch):
    bundle = tmp_path / "bundle"
    bundle.mkdir()
    foreign = bundle / "existing-evidence.json"
    foreign.write_bytes(b"keep existing evidence")
    temporary_paths = []

    def denied_link(source, target):
        temporary_paths.append(Path(source))
        raise PermissionError("fixture create-only publication denied")

    monkeypatch.setattr(bot.os, "link", denied_link)
    with pytest.raises(PermissionError):
        bot._publish_delivery_receipt(bundle, {
            "recordedAt": "2026-10-24T04:00:00+08:00", "fileSha256": "a" * 64,
        })
    assert temporary_paths and not temporary_paths[0].exists()
    assert list(bundle.iterdir()) == [foreign]
    assert foreign.read_bytes() == b"keep existing evidence"


def test_service_start_keeps_listening_when_published_data_is_unreadable(monkeypatch: pytest.MonkeyPatch) -> None:
    def unreadable() -> tuple[dict[str, str], dict[str, object]]:
        raise bot.AnalysisProcessError("完全讀不到已發布資料：head missing")

    monkeypatch.setattr(bot, "c0_analysis_environment", unreadable)

    class FakeClient:
        def get_webhook_info(self) -> dict[str, object]:
            return {"url": ""}

        def get_me(self) -> dict[str, object]:
            return {"username": "fixture_bot"}

        def get_chat(self) -> dict[str, object]:
            return {"type": "private"}

    instance = bot.TelegramStockCommandBot.__new__(bot.TelegramStockCommandBot)
    instance.client = FakeClient()
    instance.analysis_secret_values = ()
    instance.heartbeat_lock = threading.Lock()
    instance.heartbeat_data = {}
    instance.verify()
    assert "完全讀不到已發布資料" in instance.c0_startup_warning
    assert instance.c0_binding == {}
    assert instance.analysis_env[bot.CURRENT_NETWORK_DISABLED_ENV] == "1"
    assert instance.heartbeat_data["c0StartupWarning"] == instance.c0_startup_warning


def test_c0_estimate_report_labels_flow_to_the_job_and_integrity_failures_are_rejected(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, fake_c0_estimate_validation
) -> None:
    import stock_daily_contract as contract

    output_dir = tmp_path / "job"
    pdf_path, json_path, _ = _write_c0_report(output_dir, _c0_estimate_payload())
    labels: list[dict[str, object]] = []
    # The job pin differs from the report's own snapshot: a label, not a rejection.
    bot._validate_generated_report(bot.MODE_H63, STOCK_2330, output_dir, pdf_path, json_path,
                                   _c0_binding(fingerprint="e" * 64), labels)
    assert [item["code"] for item in labels] == ["job_published_snapshot_changed"]

    with pytest.raises(bot.AnalysisProcessError, match="c0_report_identity_mismatch"):
        bot._validate_generated_report(bot.MODE_H63, bot.StockMatch("2303", "聯電", "exact_id", 1.0),
                                       output_dir, pdf_path, json_path, _c0_binding())
    with pytest.raises(bot.AnalysisProcessError, match="缺少本次工作 runtime binding"):
        bot._validate_generated_report(bot.MODE_H63, STOCK_2330, output_dir, pdf_path, json_path, {})

    def tampered(*_: object, **__: object) -> dict[str, object]:
        raise contract.C0IntegrityError("c0_report_prediction_value_mismatch:prob_up")

    monkeypatch.setattr(contract, "validate_c0_estimate", tampered)
    with pytest.raises(bot.AnalysisProcessError, match="c0_report_prediction_value_mismatch"):
        bot._validate_generated_report(bot.MODE_H63, STOCK_2330, output_dir, pdf_path, json_path, _c0_binding())


@pytest.mark.parametrize(
    ("case", "message"),
    [
        ("other_schema", "current_c0_report_schema_required"),
        ("declared_path", "c0_attachment_declared_path_mismatch"),
        ("pdf_eof", "PDF 格式標記或 EOF 不完整"),
        ("outside_output_dir", "超出允許的契約資料根目錄"),
        ("duplicate_key", "current_identity_duplicate_json_key"),
    ],
)
def test_c0_report_file_integrity_failures_are_rejected(
    tmp_path: Path, fake_c0_estimate_validation, case: str, message: str
) -> None:
    output_dir = tmp_path / "job"
    payload = _c0_estimate_payload()
    if case == "other_schema":
        payload["report_schema_version"] = "single-stock-formal-estimate-v3"
    pdf_path, json_path, raw = _write_c0_report(output_dir, payload)
    if case == "declared_path":
        document = json.loads(raw)
        document["output_pdf"] = str(output_dir / "other.pdf")
        json_path.write_text(json.dumps(document), encoding="utf-8")
    if case == "pdf_eof":
        pdf_path.write_bytes(b"%PDF-1.7\ntruncated")
    if case == "outside_output_dir":
        json_path = tmp_path / "elsewhere.json"
        json_path.write_bytes(raw)
    if case == "duplicate_key":
        json_path.write_bytes(raw.replace(b'"qa_override": false', b'"qa_override": false, "qa_override": false'))
    with pytest.raises(bot.AnalysisProcessError, match=message):
        bot._validate_generated_report(bot.MODE_H63, STOCK_2330, output_dir, pdf_path, json_path, _c0_binding())


# ------------------------------------------------------------------------------------------------------------------
# "No estimate" C0 reports (c0_no_estimate_data_time_only): a valid result that states the reason and
# the data time.  Seal, PDF digest, no estimate value, formal (non-QA) attachment and the report's own
# content-addressed head stay KEEP; newer data, a different job pin or a head gone by retention are labels.
# ------------------------------------------------------------------------------------------------------------------
NO_ESTIMATE_STOCK = bot.StockMatch("1591", "fixture", "exact_id", 1.0)


@pytest.fixture
def fake_published_heads(monkeypatch: pytest.MonkeyPatch) -> dict[str, object]:
    """``pinned``: fingerprint of the report's own head (None = gone, Exception = raised); ``current``: current head."""
    import stock_daily_contract as contract

    state: dict[str, object] = {"pinned": "f" * 64, "current": "f" * 64, "refs": []}

    class Sources:
        def __init__(self, fingerprint: object) -> None:
            self.fingerprint = fingerprint

        def snapshot_descriptor(self) -> dict[str, object]:
            return {"snapshot_fingerprint": self.fingerprint}

        def descriptor(self) -> dict[str, object]:
            return {"snapshot_fingerprint": self.fingerprint, "price_actual_max_date": "2026-10-09",
                    "sources": [{"manifest_sha256": "c" * 64, "published_at": "2026-10-09T15:40:00+08:00"}]}

    def published_sources(reference: dict[str, object]) -> Sources:
        state["refs"].append(dict(reference))
        pinned = state["pinned"]
        if pinned is None:
            raise FileNotFoundError("head removed by retention")
        if isinstance(pinned, Exception):
            raise pinned
        return Sources(pinned)

    monkeypatch.setattr(contract, "_published_sources", published_sources)
    monkeypatch.setattr(contract, "_current_c0_sources", lambda: Sources(state["current"]))
    return state


def _write_no_estimate_report(output_dir: Path, update=None, *, reseal: bool = True) -> tuple[Path, Path]:
    import stock_daily_contract as contract

    output_dir.mkdir(parents=True, exist_ok=True)
    pdf_path, json_path = output_dir / "result.pdf", output_dir / "result.json"
    pdf_bytes = b"%PDF-1.7\nno estimate\n%%EOF\n"
    pdf_path.write_bytes(pdf_bytes)
    head_root = output_dir / "shared_market_data"
    payload: dict[str, object] = {
        "report_schema_version": bot.C0_SINGLE_REPORT_SCHEMA,
        "report_class": bot.C0_NO_ESTIMATE_REPORT_CLASS,
        "estimate_status": "no_estimate",
        "profile": "C0",
        "source_mode": "published",
        "horizon_sessions": 1,
        "target_definition": "signal_close_to_same_market_H_session_close",
        "stock": {"stock_id": "1591", "board": "TPEX"},
        "qa_override": False,
        "historical_pit_verified": False,
        "historical_membership_verified": False,
        "corporate_actions_verified": False,
        "effectiveness_status": "unknown",
        "trading_effect": "unverified",
        "latest_data_date": "2026-10-08",
        "stock_last_price_date": "2026-10-08",
        "no_estimate_reason": "latest_exact_253_market_sessions_incomplete",
        "prediction": None,
        "data_time": {"analysis_asof": "2026-10-08", "read_at_utc": "2026-10-11T04:25:00+00:00",
                      "head": {"published_at": "2026-10-11T04:25:00+00:00"}},
        "published_snapshot": {
            "snapshot_fingerprint": "f" * 64,
            "sources": [{"root": str(head_root), "manifest_path": str(head_root / "_manifests" / f"{HEAD_SHA}.json"),
                         "manifest_sha256": HEAD_SHA, "published_at": "2026-10-11T04:25:00+00:00"}],
        },
        "verification": {"status": "not_applicable_no_estimate"},
        "pdf_sha256": hashlib.sha256(pdf_bytes).hexdigest(),
        "pdf_size": len(pdf_bytes),
        "output_pdf": str(pdf_path),
        "output_json": str(json_path),
    }
    sealed = dict(payload)
    sealed["estimate_binding_sha256"] = hashlib.sha256(contract._canonical(payload)).hexdigest()
    if update is not None:
        update(sealed)
        if reseal:
            unsigned = {key: value for key, value in sealed.items() if key != "estimate_binding_sha256"}
            sealed["estimate_binding_sha256"] = hashlib.sha256(contract._canonical(unsigned)).hexdigest()
    json_path.write_text(json.dumps(sealed, ensure_ascii=False), encoding="utf-8")
    return pdf_path, json_path


def test_c0_no_estimate_report_is_accepted_with_reason_and_data_time(
    tmp_path: Path, fake_published_heads: dict[str, object]
) -> None:
    pdf_path, json_path = _write_no_estimate_report(tmp_path / "job")
    labels: list[dict[str, object]] = []
    _, payload, pdf_sha256, _ = bot._validate_generated_report(
        bot.MODE_H1, NO_ESTIMATE_STOCK, tmp_path / "job", pdf_path, json_path, _c0_binding(), labels,
    )
    assert labels == []
    assert pdf_sha256 == hashlib.sha256(pdf_path.read_bytes()).hexdigest()
    assert fake_published_heads["refs"][0]["manifest_sha256"] == HEAD_SHA  # the report's own pinned head
    caption = bot.report_caption(bot.MODE_H1, NO_ESTIMATE_STOCK, payload)
    assert caption.splitlines()[0] == "1591 fixture｜C0 H1 收盤估計：無法估計"
    assert "（latest_exact_253_market_sessions_incomplete）" in caption
    assert "該股最後價格日：20261008" in caption
    assert "不含任何估計值" in caption and "上漲機率" not in caption


def test_c0_no_estimate_report_version_differences_are_labels(
    tmp_path: Path, fake_published_heads: dict[str, object]
) -> None:
    pdf_path, json_path = _write_no_estimate_report(tmp_path / "job")
    fake_published_heads["current"] = "e" * 64
    fake_published_heads["pinned"] = None
    labels: list[dict[str, object]] = []
    _, payload, _, _ = bot._validate_generated_report(
        bot.MODE_H1, NO_ESTIMATE_STOCK, tmp_path / "job", pdf_path, json_path, _c0_binding(fingerprint="9" * 64), labels,
    )
    assert [item["code"] for item in labels] == [
        "reproduction_inputs_gone", "newer_data_available", "job_published_snapshot_changed",
    ]
    caption = bot.report_caption(bot.MODE_H1, NO_ESTIMATE_STOCK, payload, {"labels": labels})
    assert "執行期間已有較新資料 20261009" in caption


@pytest.mark.parametrize(
    ("case", "message"),
    [
        ("tampered_without_reseal", "c0_report_binding_digest_mismatch"),
        ("carries_prob_up", "c0_no_estimate_report_carries_estimate"),
        ("predicted_row", "c0_no_estimate_report_carries_estimate"),
        ("qa_override", "c0_qa_model_override_cannot_be_formal_attachment"),
        ("claims_pit", "c0_report_claims_unverified_historical_or_effectiveness_status"),
        ("no_reason", "c0_no_estimate_reason_missing"),
        ("no_data_time", "c0_no_estimate_data_time_missing"),
        ("verification", "c0_no_estimate_verification_status_invalid"),
        ("other_stock", "c0_no_estimate_report_identity_schema_or_target_invalid"),
        ("other_horizon", "c0_no_estimate_report_identity_schema_or_target_invalid"),
        ("pdf_digest", "c0_report_pdf_digest_or_size_mismatch"),
        ("two_heads", "c0_no_estimate_published_snapshot_invalid"),
        ("head_fingerprint", "c0_no_estimate_snapshot_fingerprint_mismatch"),
        ("head_tampered", "daily_immutable_head_digest_mismatch"),
    ],
)
def test_c0_no_estimate_report_integrity_failures_are_rejected(
    tmp_path: Path, fake_published_heads: dict[str, object], case: str, message: str
) -> None:
    import stock_daily_contract as contract

    updates = {
        "tampered_without_reseal": lambda p: p.update(no_estimate_reason="stock_price_older_than_asof"),
        "carries_prob_up": lambda p: p.update(prob_up=0.55),
        "predicted_row": lambda p: p.update(prediction={"prediction_status": "predicted", "prob_up": 0.55}),
        "qa_override": lambda p: p.update(qa_override=True),
        "claims_pit": lambda p: p.update(historical_pit_verified=True),
        "no_reason": lambda p: p.update(no_estimate_reason=" "),
        "no_data_time": lambda p: p.update(data_time={}),
        "verification": lambda p: p.update(verification={"status": "verified"}),
        "other_stock": lambda p: p.update(stock={"stock_id": "2330"}),
        "other_horizon": lambda p: p.update(horizon_sessions=63),
        "pdf_digest": lambda p: p.update(pdf_sha256="0" * 64),
        "two_heads": lambda p: p["published_snapshot"].update(sources=p["published_snapshot"]["sources"] * 2),
    }
    update = updates.get(case)
    pdf_path, json_path = _write_no_estimate_report(tmp_path / "job", update, reseal=case != "tampered_without_reseal")
    if case == "head_fingerprint":
        fake_published_heads["pinned"] = "e" * 64
    if case == "head_tampered":
        fake_published_heads["pinned"] = contract.C0IntegrityError("daily_immutable_head_digest_mismatch")
    with pytest.raises(bot.AnalysisProcessError, match=message):
        bot._validate_generated_report(
            bot.MODE_H1, NO_ESTIMATE_STOCK, tmp_path / "job", pdf_path, json_path, _c0_binding(),
        )
