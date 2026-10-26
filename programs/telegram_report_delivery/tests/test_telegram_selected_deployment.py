from __future__ import annotations

import hashlib
import json
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


STOCK = bot.StockMatch("2330", "台積電", "exact_id", 1.0)
HEAD_SHA = "d" * 64
MODEL_SHA = "a" * 64
PYTHON = r"D:\stock\.venv\Scripts\python.exe"


def _binding() -> dict:
    return {
        "schema": "single-stock-selected-runtime-binding-v1",
        "deployment_binding": {
            "schema": bot.SELECTED_DEPLOYMENT_SCHEMA,
            "consumer_id": "single", "activation_id": "approved-activation",
            "model_path": r"D:\TWStockData\program_data\dstock_single\models\selected.json",
            "model_sha256": MODEL_SHA,
            "cache_dir": r"D:\TWStockData\program_data\dstock_single\cache\selected",
        },
        "asof": "2026-10-16", "head_sha256": HEAD_SHA, "snapshot_fingerprint": "f" * 64,
        "published_sources": [{"manifest_sha256": HEAD_SHA, "published_at": "2026-10-16T08:00:00+00:00"}],
        "model_binding": {"sha256": MODEL_SHA},
    }


def _payload(horizon: int = 63) -> dict:
    binding = _binding()
    return {
        "schema": bot.SELECTED_SINGLE_REPORT_SCHEMA,
        "research_only": False, "promotion": True,
        "stock": {"stock_id": "2330", "stock_name": "台積電", "market": "TWSE"},
        "horizon_sessions": horizon, "asof": "2026-10-16", "target_session_date": "2027-01-18",
        "prediction": {"status": "predicted", "prob_up": 0.6123, "expected_return": 0.1234},
        "deployment_binding": binding["deployment_binding"],
        "snapshot_binding": {"head_sha256": HEAD_SHA, "snapshot_fingerprint": "f" * 64,
            "descriptor": {"sources": binding["published_sources"]}},
        "generated_at": "2026-10-16T08:30:00+00:00",
    }


def _write_report(
    directory: Path, *, bundled: bool = False, payload: dict | None = None, write_log: bool = True,
) -> tuple[Path, Path, dict]:
    directory.mkdir(parents=True, exist_ok=True)
    pdf, data = directory / "result.pdf", directory / "result.json"
    pdf_bytes = b"%PDF-1.7\n%%EOF\n"
    pdf.write_bytes(pdf_bytes)
    document = dict(payload if payload is not None else _payload())
    document.update(output_pdf=str(pdf), output_json=str(data), pdf_size=len(pdf_bytes),
                    pdf_sha256=hashlib.sha256(pdf_bytes).hexdigest())
    if bundled:
        document = {"schema": "single-stock-selected-formal-report-bundle-v1", "payload": document}
    data.write_text(json.dumps(document, ensure_ascii=False), encoding="utf-8", newline="\n")
    if write_log:
        (directory / "analysis.log").write_text(f"PDF：{pdf}\nJSON：{data}\n", encoding="utf-8", newline="\n")
    return pdf, data, document


@pytest.fixture
def authority(monkeypatch: pytest.MonkeyPatch) -> dict:
    binding = _binding()
    environment = {"SystemRoot": os.environ.get("SystemRoot", r"C:\Windows"),
        "DSTOCK_SELECTED_BINDING": "job-pin", "TELEGRAM_BOT_TOKEN": "private-token"}
    state = {"selected": (environment, binding), "active": binding["deployment_binding"],
             "environment_calls": 0, "active_calls": 0, "validation_calls": [], "error": None}

    def selected_environment():
        state["environment_calls"] += 1
        if state["error"] is not None:
            raise state["error"]
        return state["selected"]

    def active_binding():
        state["active_calls"] += 1
        if state["error"] is not None:
            raise state["error"]
        return state["active"]

    def validate(document, pdf_bytes, *, stock_id, horizon, expected_runtime_binding, pdf_path, require_active):
        assert pdf_path.read_bytes() == pdf_bytes
        assert require_active is True
        state["validation_calls"].append((document, pdf_bytes, stock_id, horizon, expected_runtime_binding))
        if state["error"] is not None:
            raise state["error"]
        payload = document.get("payload", document)
        if payload["stock"]["stock_id"] != stock_id or payload["horizon_sessions"] != horizon:
            raise ValueError("selected_identity_or_horizon_mismatch")
        if payload["deployment_binding"] != expected_runtime_binding["deployment_binding"]:
            raise ValueError("selected_deployment_binding_mismatch")
        if payload["snapshot_binding"]["head_sha256"] != bot.c0_binding_head_sha256(expected_runtime_binding):
            raise ValueError("selected_head_mismatch")
        if payload["pdf_size"] != len(pdf_bytes) or payload["pdf_sha256"] != hashlib.sha256(pdf_bytes).hexdigest():
            raise ValueError("selected_pdf_digest_or_size_mismatch")
        return state.get("verification_result", {
            "valid": True, "verified": True, "prediction_reproduced": True,
            "activation_verified": True, "version_labels": [],
        })

    monkeypatch.setitem(sys.modules, "stock_daily_selected_deployment", SimpleNamespace(
        selected_analysis_environment=selected_environment, active_selected_binding=active_binding,
        validate_selected_report=validate,
    ))
    return state


def _instance() -> bot.TelegramStockCommandBot:
    instance = bot.TelegramStockCommandBot.__new__(bot.TelegramStockCommandBot)
    instance.analysis_secret_values = ()
    instance._job_version_labels = []
    instance.select_job_source()
    return instance


def test_selected_authority_precedes_old_c0_and_sanitizes_environment(authority, monkeypatch):
    def forbidden_c0():
        raise AssertionError("old active C0 must not be preflighted")

    monkeypatch.setattr(bot, "c0_analysis_environment", forbidden_c0)
    environment, binding = bot.analysis_environment()
    assert binding is authority["selected"][1]
    assert environment["DSTOCK_SELECTED_BINDING"] == "job-pin"
    assert "TELEGRAM_BOT_TOKEN" not in environment
    assert environment[bot.CURRENT_NETWORK_DISABLED_ENV] == "1"
    assert environment["PYTHONDONTWRITEBYTECODE"] == "1"
    assert bot.c0_binding_head_sha256(binding) == HEAD_SHA


def test_only_explicit_absence_falls_back_to_legacy_c0(authority, monkeypatch):
    legacy = ({"legacy": "1"}, {"schema": "stock-daily-c0-runtime-binding-v2"})
    monkeypatch.setattr(bot, "c0_analysis_environment", lambda: legacy)
    authority["selected"] = None
    assert bot.analysis_environment() is legacy
    authority["error"] = ValueError("selected_model_sidecar_digest_mismatch")
    with pytest.raises(bot.SelectedBindingIntegrityError, match="sidecar_digest"):
        bot.analysis_environment()


@pytest.mark.parametrize("selected", [(), ({},), ({}, {}), ({"BAD": 1}, _binding())])
def test_malformed_selected_authority_never_becomes_c0(authority, selected, monkeypatch):
    authority["selected"] = selected
    monkeypatch.setattr(bot, "c0_analysis_environment", lambda: pytest.fail("unexpected legacy fallback"))
    with pytest.raises(bot.SelectedBindingIntegrityError):
        bot.analysis_environment()


def test_job_reuses_one_full_environment_and_only_lightweight_deployment_reads(authority, monkeypatch):
    instance = _instance()
    pinned = dict(instance.analysis_env)
    for mode in ("resolver", bot.MODE_H63, bot.MODE_H1, "upload"):
        instance.refresh_analysis_environment_for_mode("tg-1", mode)
        assert instance.analysis_env == pinned
    assert authority["environment_calls"] == 1
    assert authority["active_calls"] == 4
    assert instance._job_c0_binding == _binding()


@pytest.mark.parametrize("outcome", [
    "needs_input", "analysis_failure", "validation_failure", "send_failure", "complete",
])
def test_process_job_retains_selected_source_lease_until_terminal_cleanup(authority, monkeypatch, tmp_path, outcome):
    class SourceBinding(dict):
        def __init__(self):
            super().__init__(_binding())
            self.close_calls = 0

        def close_source_lease(self):
            self.close_calls += 1

    leases = []
    environment = dict(authority["selected"][0])

    def selected_environment():
        binding = SourceBinding()
        leases.append(binding)
        return dict(environment), binding

    monkeypatch.setattr(sys.modules["stock_daily_selected_deployment"],
                        "selected_analysis_environment", selected_environment)
    job = {"jobId": "tg-lease", "status": "queued", "query": "2330", "mode": bot.MODE_H63}
    stages, sent, appended = [], [], []
    instance = bot.TelegramStockCommandBot.__new__(bot.TelegramStockCommandBot)
    instance.analysis_secret_values = ()

    def observe(stage):
        binding = instance._job_c0_binding
        assert binding is leases[0] and binding.close_calls == 0
        assert bot.c0_binding_head_sha256(binding) == HEAD_SHA
        assert instance._job_analysis_env["DSTOCK_SELECTED_BINDING"] == "job-pin"
        stages.append(stage)

    def update_job(job_id, **fields):
        assert job_id == job["jobId"]
        job.update(fields)

    def resolve(query):
        observe("resolve")
        return [] if outcome == "needs_input" else [STOCK]

    report = bot.GeneratedReport(bot.MODE_H63, tmp_path / "report.pdf", None, "fixture caption")

    def run(job_id, stock, mode, progress):
        observe("analysis")
        if outcome == "analysis_failure":
            raise bot.AnalysisProcessError("fixture analysis failure")
        return report

    def validate(job_id, stock, generated):
        observe("validation")
        assert generated is report
        if outcome == "validation_failure":
            raise bot.AnalysisProcessError("fixture integrity failure")
        return generated

    def send(path, caption):
        observe("send")
        if outcome == "send_failure":
            raise bot.TelegramApiError("fixture transport failure")
        sent.append(path)
        return {"message_id": 42}

    def append(job_id, generated):
        observe("append")
        appended.append(generated)

    instance.store = SimpleNamespace(get_job=lambda job_id: dict(job), update_job=update_job, append_report=append)
    instance.client = SimpleNamespace(send_message=lambda text: None, send_chat_action=lambda action: None,
                                      send_document=send)
    instance.resolve_stock = resolve
    instance.run_analysis_mode = run
    instance.validate_report_before_upload = validate
    instance.process_job(job["jobId"])

    assert len(leases) == 1 and leases[0].close_calls == 1
    assert instance._job_c0_binding is None
    assert job["selectedPublishedModelBinding"] is leases[0]
    assert bot.c0_binding_head_sha256(job["selectedPublishedModelBinding"]) == HEAD_SHA
    if outcome == "complete":
        assert job["status"] == "complete"
        assert stages == ["resolve", "analysis", "validation", "send", "append"]
        assert sent == [report.pdf_path] and appended == [report]
    else:
        assert job["status"] == ("needs_input" if outcome == "needs_input" else "failed")
        assert sent == appended == []
        if outcome in {"analysis_failure", "validation_failure"}:
            assert job["unsentModes"] == [bot.MODE_H63]
        if outcome == "validation_failure":
            assert "send" not in stages


def test_selected_resolver_uses_the_same_job_head_without_old_c0_preflight(authority, monkeypatch):
    instance = _instance()
    instance.resolve_timeout_seconds = 180
    monkeypatch.setattr(bot, "resolve_analysis_python", lambda env: PYTHON)
    commands = []

    def run(command, **kwargs):
        commands.append(command)
        assert kwargs["env"]["DSTOCK_SELECTED_BINDING"] == "job-pin"
        return SimpleNamespace(returncode=0, stdout="1. 2330 台積電｜exact_id｜score=1.0\n")

    monkeypatch.setattr(bot.subprocess, "run", run)
    assert instance.resolve_stock("2330")[0].code == "2330"
    assert commands[0][-2:] == ["--pinned-head-sha256", HEAD_SHA]
    assert authority["environment_calls"] == 1


def test_selected_self_check_uses_authority_once_and_pins_its_resolver(authority, tmp_path, monkeypatch):
    heartbeat = tmp_path / "heartbeat.json"
    owner = tmp_path / "owner.json"
    heartbeat.write_text(json.dumps({"pid": 42, "sourceSha256": bot.SOURCE_SHA256}), encoding="utf-8")
    owner.write_text(json.dumps({"processId": 42, "sourceSha256": bot.SOURCE_SHA256}), encoding="utf-8")
    monkeypatch.setattr(bot, "HEARTBEAT_PATH", heartbeat)
    monkeypatch.setattr(bot, "OWNER_PATH", owner)
    monkeypatch.setattr(bot, "resolve_analysis_python", lambda env: PYTHON)
    monkeypatch.setattr(bot, "c0_analysis_environment", lambda: pytest.fail("legacy C0 preflight"))

    def run(command, **kwargs):
        assert command[-2:] == ["--pinned-head-sha256", HEAD_SHA]
        return SimpleNamespace(returncode=0, stdout="1. 2303 聯電｜exact_id｜score=1.0\n")

    monkeypatch.setattr(bot.subprocess, "run", run)
    assert bot.run_non_queue_self_check()["status"] == "ok"
    assert authority["environment_calls"] == 1
    assert authority["active_calls"] == 1


@pytest.mark.parametrize("drift", [None, {**_binding()["deployment_binding"], "activation_id": "other"}])
def test_selected_deployment_drift_labels_but_keeps_the_sealed_job(authority, drift):
    instance = _instance()
    pinned_environment = dict(instance.analysis_env)
    authority["active"] = drift
    instance.refresh_analysis_environment_for_mode("tg-1", bot.MODE_H63)
    assert instance.analysis_env == pinned_environment
    assert instance._job_c0_binding == _binding()
    assert instance._job_version_labels[0]["code"] == "runtime_contract_changed_during_job"
    caption = bot.report_caption(bot.MODE_H63, STOCK, _payload(), instance._job_caption_context())
    assert "仍使用工作開始時的封存模型" in caption
    assert authority["environment_calls"] == 1


def test_selected_current_integrity_failure_is_not_a_version_label(authority):
    instance = _instance()
    authority["error"] = ValueError("selected_pointer_integrity_mismatch")
    with pytest.raises(bot.SelectedBindingIntegrityError, match="pointer_integrity"):
        instance.refresh_analysis_environment_for_mode("tg-1", bot.MODE_H63)


@pytest.mark.parametrize("bundled", [False, True])
def test_explicit_formal_schema_dispatches_original_document_and_exact_bytes(authority, tmp_path, monkeypatch, bundled):
    import stock_daily_contract

    monkeypatch.setattr(stock_daily_contract, "validate_c0_estimate", lambda *a, **k: pytest.fail("C0 validator used"))
    pdf, data, document = _write_report(tmp_path / "job", bundled=bundled)
    declared_pdf, declared_json = bot._declared_report_paths(pdf.parent / "analysis.log", pdf.parent)
    path, payload, pdf_sha, json_sha = bot._validate_generated_report(
        bot.MODE_H63, STOCK, pdf.parent, declared_pdf, declared_json, _binding(),
    )
    assert path == data.resolve()
    assert payload["schema"] == bot.SELECTED_SINGLE_REPORT_SCHEMA
    assert pdf_sha == hashlib.sha256(pdf.read_bytes()).hexdigest()
    assert json_sha == hashlib.sha256(data.read_bytes()).hexdigest()
    assert authority["validation_calls"] == [(document, pdf.read_bytes(), "2330", 63, _binding())]


@pytest.mark.parametrize("change,message", [
    ({"schema": "single-stock-selected-candidate-report-v1"}, "selected_formal_report_schema"),
    ({"research_only": True}, "formal_qualification"),
    ({"promotion": False}, "formal_qualification"),
    ({"report_schema_version": bot.C0_SINGLE_REPORT_SCHEMA}, "dispatch_ambiguous"),
    ({"horizon_sessions": 1}, "identity_or_horizon"),
    ({"stock": {"stock_id": "2303"}}, "identity_or_horizon"),
    ({"deployment_binding": {**_binding()["deployment_binding"], "model_sha256": "b" * 64}}, "deployment_binding"),
    ({"snapshot_binding": {"head_sha256": "e" * 64}}, "head_mismatch"),
    ({"snapshot_binding": {"head_sha256": HEAD_SHA, "snapshot_fingerprint": "e" * 64}}, "snapshot_fingerprint_mismatch"),
])
def test_formal_schema_does_not_relax_identity_qualification_or_binding(authority, tmp_path, change, message):
    payload = _payload()
    payload.update(change)
    pdf, data, _ = _write_report(tmp_path / "job", payload=payload)
    with pytest.raises(bot.AnalysisProcessError, match=message):
        bot._validate_generated_report(bot.MODE_H63, STOCK, pdf.parent, pdf, data, _binding())


@pytest.mark.parametrize("message", [
    "selected_model_digest_mismatch", "selected_sidecar_digest_mismatch", "selected_immutable_head_digest_mismatch",
    "selected_prediction_reproduction_failed", "selected_bundle_payload_digest_mismatch",
])
def test_shared_integrity_and_reproduction_errors_remain_blocking(authority, tmp_path, message):
    pdf, data, _ = _write_report(tmp_path / "job")
    authority["error"] = ValueError(message)
    with pytest.raises(bot.AnalysisProcessError, match=message):
        bot._validate_generated_report(bot.MODE_H63, STOCK, pdf.parent, pdf, data, _binding())


@pytest.mark.parametrize("field", ["valid", "verified", "prediction_reproduced", "activation_verified"])
def test_selected_validator_must_confirm_every_formal_acceptance_result(authority, tmp_path, field):
    pdf, data, _ = _write_report(tmp_path / "job")
    authority["verification_result"] = dict.fromkeys(
        ("valid", "verified", "prediction_reproduced", "activation_verified"), True,
    )
    authority["verification_result"][field] = False
    with pytest.raises(bot.AnalysisProcessError, match="validation_not_verified"):
        bot._validate_generated_report(bot.MODE_H63, STOCK, pdf.parent, pdf, data, _binding())


def test_selected_report_requires_selected_job_pin(authority, tmp_path):
    pdf, data, _ = _write_report(tmp_path / "job")
    with pytest.raises(bot.AnalysisProcessError, match="selected_job_binding"):
        bot._validate_generated_report(bot.MODE_H63, STOCK, pdf.parent, pdf, data, {"schema": "legacy-c0"})


@pytest.mark.parametrize("case,message", [
    ("pdf_marker", "PDF 格式標記"), ("pdf_size", "digest_or_size"), ("pdf_hash", "digest_or_size"),
    ("declared_path", "declared_path_mismatch"), ("duplicate_json", "duplicate_json_key"),
    ("candidate_path", "candidate/smoke/staging"), ("outside_root", "超出允許"),
])
def test_selected_exact_attachments_keep_file_integrity_gates(authority, tmp_path, case, message):
    pdf, data, document = _write_report(tmp_path / "job")
    if case == "pdf_marker":
        pdf.write_bytes(b"not a PDF")
    elif case in {"pdf_size", "pdf_hash", "declared_path"}:
        document[{"pdf_size": "pdf_size", "pdf_hash": "pdf_sha256", "declared_path": "output_json"}[case]] = {
            "pdf_size": 1, "pdf_hash": "0" * 64, "declared_path": str(pdf.parent / "other.json"),
        }[case]
        data.write_text(json.dumps(document), encoding="utf-8")
    elif case == "duplicate_json":
        data.write_bytes(data.read_bytes().replace(b'"promotion": true', b'"promotion": true, "promotion": true'))
    elif case == "candidate_path":
        data = pdf.parent / "candidate.json"
    elif case == "outside_root":
        data = tmp_path / "outside.json"
    with pytest.raises(bot.AnalysisProcessError, match=message):
        bot._validate_generated_report(bot.MODE_H63, STOCK, pdf.parent, pdf, data, _binding())


@pytest.mark.parametrize("horizon", [1, 63])
def test_selected_caption_is_concise_chinese_and_keeps_major_cautions(horizon):
    payload = _payload(horizon)
    payload["data_quality_assessment"] = {"status": "partial"}
    caption = bot.report_caption(f"H{horizon}", STOCK, payload)
    assert caption.splitlines()[0] == f"2330 台積電｜第{horizon}交易日收盤估計"
    assert "資料日：20261016" in caption
    assert "上漲機率：61.23%；預估報酬：+12.34%" in caption
    assert "PARTIAL" in caption and "未驗證" in caption
    assert all(text not in caption for text in ("activation", MODEL_SHA, "model_path", "selected_direction_kind"))
    assert len(caption) <= bot.TELEGRAM_CAPTION_BUDGET


def test_verified_selected_report_upload_rechecks_bytes_without_repeating_model_reproduction(authority, tmp_path, monkeypatch):
    instance = _instance()
    pdf, data, _ = _write_report(tmp_path / "job")
    _, payload, pdf_sha, json_sha = bot._validate_generated_report(
        bot.MODE_H63, STOCK, pdf.parent, pdf, data, instance._job_c0_binding,
    )
    report = bot.GeneratedReport(bot.MODE_H63, pdf, data, "", pdf_sha, json_sha,
                                 declared_paths=(pdf, data))
    instance._verified_selected_reports[instance._selected_report_cache_key(STOCK, report)] = (
        bot.CommandStateStore._copy(instance._job_c0_binding), payload,
    )
    checked = instance.validate_report_before_upload("tg-1", STOCK, report)
    assert "第63交易日收盤估計" in checked.caption
    assert len(authority["validation_calls"]) == 1
    assert authority["active_calls"] == 1
    assert authority["environment_calls"] == 1
    data.write_bytes(data.read_bytes() + b" ")
    with pytest.raises(bot.AnalysisProcessError, match="附件在上傳前變動"):
        instance.validate_report_before_upload("tg-1", STOCK, report)
    assert len(authority["validation_calls"]) == 1


def test_selected_run_keeps_head_pin_progress_and_caches_verified_attachment(authority, tmp_path, monkeypatch):
    root = tmp_path / "dstock_single"
    monkeypatch.setattr(bot, "DSTOCK_SINGLE_ROOT", root)
    monkeypatch.setattr(bot, "JOBS_DIR", root / "telegram_jobs")
    monkeypatch.setattr(bot, "resolve_analysis_python", lambda env: PYTHON)
    commands = []

    class Process:
        pid = 1201
        returncode = 0

        def poll(self):
            return self.returncode

    def popen(command, **kwargs):
        commands.append(command)
        directory = Path(command[command.index("--output-dir") + 1])
        pdf, data, _ = _write_report(directory / "formal_2330_H63", bundled=True, write_log=False)
        assert kwargs["env"][bot.SINGLE_STOCK_PHASE_ENV] == "1"
        kwargs["stdout"].write(f"PDF：{pdf}\nJSON：{data}\n")
        kwargs["stdout"].flush()
        return Process()

    monkeypatch.setattr(bot.subprocess, "Popen", popen)
    instance = _instance()
    instance.analysis_timeout_seconds = 600
    instance.heartbeat_seconds = 60
    instance.stop_event = threading.Event()
    instance.current_process_lock = threading.Lock()
    instance.current_process = None
    instance.store = SimpleNamespace(update_job=lambda *a, **k: None)
    instance.write_heartbeat = lambda **kwargs: None
    report = instance.run_analysis_mode("tg-1", STOCK, bot.MODE_H63)
    assert commands[0][-2:] == ["--pinned-head-sha256", HEAD_SHA]
    assert instance._selected_report_cache_key(STOCK, report) in instance._verified_selected_reports
    instance.validate_report_before_upload("tg-1", STOCK, report)
    assert authority["environment_calls"] == 1
    assert len(authority["validation_calls"]) == 1
    assert report.declared_paths == (report.pdf_path, report.json_path)
    assert not list(report.output_dir.glob(".analysis-*"))
    assert not (report.output_dir / "analysis.log").exists()


@pytest.mark.parametrize("change", ["missing", "path", "escape", "suffix", "wildcard"])
def test_upload_keeps_exact_declaration_checks_after_temporary_log_cleanup(authority, tmp_path, change):
    instance = _instance()
    pdf, data, _ = _write_report(tmp_path / "job", write_log=False)
    report = bot.GeneratedReport(bot.MODE_H63, pdf, data, "", "a" * 64, "b" * 64,
                                 pdf.parent, (pdf, data))
    if change == "missing":
        report = bot.dataclass_replace(report, declared_paths=None)
    elif change == "path":
        report = bot.dataclass_replace(report, pdf_path=pdf.with_name("other.pdf"))
    elif change == "escape":
        report = bot.dataclass_replace(report, declared_paths=(pdf.parent.parent / "outside.pdf", data))
    elif change == "suffix":
        report = bot.dataclass_replace(report, declared_paths=(pdf, data.with_suffix(".txt")))
    else:
        report = bot.dataclass_replace(report, declared_paths=(pdf.with_name("*.pdf"), data))
    with pytest.raises(bot.AnalysisProcessError):
        instance.validate_report_before_upload("tg-1", STOCK, report)
    assert not authority["validation_calls"]
    assert not (pdf.parent / "analysis.log").exists()


@pytest.mark.parametrize("diagnostic", [
    "selected_required_feature_projection_not_ready",
    "C0分析受阻：SelectedDeploymentError: selected_required_feature_projection_not_ready:finmind_per;prepare_selected_cache_required",
    "第63個市場交易日收盤估計執行失敗；末段訊息：\nselected_required_feature_projection_not_ready:route_a,route_b",
])
def test_only_exact_selected_projection_error_maps_to_concise_notice(diagnostic):
    message = bot._analysis_failure_message("2330", "tg-cold", bot.AnalysisProcessError(diagnostic))
    assert message == (
        "資料準備中｜2330\n"
        "最新資料的個股查詢快取尚未備妥，請稍後重新查詢。\n"
        "尚未產生或傳送報告。\n工作編號：tg-cold"
    )
    assert "selected_required_feature_projection_not_ready" not in message
    assert "正在" not in message and "分鐘" not in message


@pytest.mark.parametrize("diagnostic", [
    "selected_required_feature_projection_not_ready_extra",
    "not_selected_required_feature_projection_not_ready",
    "selected_required_feature_projection_not_readyish",
    "SELECTED_REQUIRED_FEATURE_PROJECTION_NOT_READY",
    "selected_model_digest_mismatch",
    "projection_not_ready",
    "unknown analysis failure: original tail",
])
def test_unknown_and_similarly_named_errors_keep_original_failure_message(diagnostic):
    assert bot._analysis_failure_message("2330", "tg-unknown", bot.AnalysisProcessError(diagnostic)) == (
        f"分析失敗｜2330\n原因：{bot.cd.display_text(diagnostic)}\n工作編號：tg-unknown"
    )


@pytest.mark.parametrize("previous_report_sent", [False, True])
def test_all_cold_cache_notice_preserves_diagnostics_without_send_retry_or_fallback(
    authority, monkeypatch, caplog, previous_report_sent,
):
    diagnostic = (
        "第63個市場交易日收盤估計執行失敗；末段訊息："
        "C0分析受阻：SelectedDeploymentError: selected_required_feature_projection_not_ready:finmind_per;"
        "prepare_selected_cache_required; original-private-diagnostic"
    )
    job_id = "tg-cold"
    job = {"jobId": job_id, "status": "queued", "query": "2330", "mode": bot.MODE_BOTH}
    updates, messages, documents, analysis_modes, appended = [], [], [], [], []

    def update_job(request_id, **fields):
        assert request_id == job_id
        updates.append(fields)
        job.update(fields)

    def send_message(text):
        messages.append(text)
        return None

    def send_document(path, caption):
        documents.append(path)
        return {"message_id": 42}

    first_report = bot.GeneratedReport(bot.MODE_H63, Path("already-verified.pdf"), None, "verified caption")

    def run_analysis(request_id, stock, mode, progress_callback):
        assert request_id == job_id and stock == STOCK
        analysis_modes.append(mode)
        if previous_report_sent and mode == bot.MODE_H63:
            return first_report
        raise bot.AnalysisProcessError(diagnostic)

    def validate(request_id, stock, report):
        assert previous_report_sent and report is first_report
        return report

    instance = bot.TelegramStockCommandBot.__new__(bot.TelegramStockCommandBot)
    instance.analysis_secret_values = ()
    instance.store = SimpleNamespace(
        get_job=lambda request_id: dict(job), update_job=update_job,
        append_report=lambda request_id, report: appended.append(report),
    )
    instance.client = SimpleNamespace(
        send_message=send_message, send_document=send_document, send_chat_action=lambda action: None,
    )
    instance.resolve_stock = lambda query: [STOCK]
    instance.run_analysis_mode = run_analysis
    instance.validate_report_before_upload = validate
    monkeypatch.setattr(bot, "c0_analysis_environment", lambda: pytest.fail("legacy fallback is forbidden"))
    monkeypatch.setattr(bot.subprocess, "Popen", lambda *a, **k: pytest.fail("no retry/full-scan child is allowed"))
    monkeypatch.setattr(bot, "_declared_report_paths", lambda *a, **k: pytest.fail("no latest/report fallback is allowed"))
    instance.process_job(job_id)

    failed = next(item for item in updates if item.get("status") == "failed")
    assert failed["error"] == diagnostic and diagnostic in caplog.text
    assert job["status"] == "failed"
    assert not any(item.get("status") in {"queued", "waiting", "complete"} for item in updates)
    assert messages[-1].startswith("資料準備中｜2330\n最新資料的個股查詢快取尚未備妥，請稍後重新查詢。\n")
    assert "original-private-diagnostic" not in messages[-1]
    assert "selected_required_feature_projection_not_ready" not in messages[-1]
    if previous_report_sent:
        assert analysis_modes == [bot.MODE_H63, bot.MODE_H1]
        assert documents == [first_report.pdf_path] and appended == [first_report]
        assert "本份報告尚未產生或傳送；先前已傳送的報告不受影響。" in messages[-1]
        assert job["unsentModes"] == [bot.MODE_H1]
    else:
        assert analysis_modes == [bot.MODE_H63]
        assert documents == [] and appended == []
        assert "尚未產生或傳送報告。" in messages[-1]
        assert job["unsentModes"] == [bot.MODE_H63, bot.MODE_H1]
