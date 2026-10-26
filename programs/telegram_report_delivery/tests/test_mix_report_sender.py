from __future__ import annotations

import hashlib
import json
import sys
from datetime import datetime
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest


SUITE_DIR = Path(__file__).resolve().parents[1]
if str(SUITE_DIR) not in sys.path:
    sys.path.insert(0, str(SUITE_DIR))

import mix_report_sender as sender


def test_sender_resolves_shared_runtime_directory() -> None:
    expected = Path(__file__).resolve().parents[2] / "shared"
    assert sender.SHARED_DIR == expected
    assert (expected / "central_evidence_session.py").is_file()


@pytest.mark.parametrize("code,text", [
    ("preopen_open_entry_negative", "隔日開盤買進 Top10 平均報酬為負"),          # v1：不變
    ("preopen_holdout_evaluated", "開盤前版保留期已評估（2026 年 1～10 月）"),    # v1／v2 共用：不變
    ("preopen_open_entry_near_zero", "隔日開盤買進 Top10 平均報酬約為零（保留期約 +0.01%／日）"),
    ("preopen_open_entry_unknown_recipe", "開盤前版配方未知，隔日開盤買進報酬未評估"),
    ("preopen_holdout_unknown_recipe", "開盤前版配方未知，沒有保留期評估"),
])
def test_preopen_recipe_status_codes_have_chinese_labels(code: str, text: str) -> None:
    """MIX 配方 v1／v2／未知配方的狀態碼（DISPLAY_TEXT 複本）：v1 代碼不變，v2 與未知配方各有中文，不落到通用文字。"""
    assert sender._human_label(code) == text


def test_0800_entrypoints_are_mix_only() -> None:
    suite = Path(__file__).resolve().parents[1]
    manual_wrapper = (suite / "08_傳送MIX正式報告.cmd").read_text(encoding="utf-8")
    canonical_wrapper = (suite / "RUN_SEND_MIX_REPORT.cmd").read_text(encoding="utf-8")
    definition = json.loads(
        (suite / "MIX_WEEKDAY_0800_TASK_DEFINITION.json").read_text(encoding="utf-8")
    )

    assert 'call "RUN_SEND_MIX_REPORT.cmd" %*' in manual_wrapper
    assert "mix_report_sender.py" in canonical_wrapper
    for source in (manual_wrapper, canonical_wrapper, json.dumps(definition)):
        assert "RUN_SEND_LATEST_REPORT.cmd" not in source
        assert "telegram_report_sender.py" not in source
    assert definition["task_name"] == "MIX Telegram Report 08-00"
    assert definition["trigger"]["start_time"] == "08:00"
    assert definition["trigger"]["days_of_week"] == [
        "Monday",
        "Tuesday",
        "Wednesday",
        "Thursday",
        "Friday",
    ]
    assert definition["settings"]["multiple_instances"] == "IgnoreNew"
    assert definition["settings"]["execution_time_limit_seconds"] == 0
    assert definition["action"]["max_wait_seconds"] == 0
    assert "--max-wait-seconds 0" in definition["action"]["arguments"]
    assert "--poll-seconds 60" in definition["action"]["arguments"]
    assert definition["publication_contract"]["selection_policy"] == "formal_only"
    assert definition["publication_contract"]["no_action_action"] == "reject_no_action_report"
    assert definition["publication_contract"]["source"] == (
        r"D:\TWStockData\program_data\mix\reports\LATEST_C0_RUN.json"
    )
    # The schedule definition must name exactly the pointer the sender reads.
    assert definition["publication_contract"]["source"] == str(sender.MIX_POINTER_PATH)
    assert definition["publication_contract"]["required_pointer_schema"] == sender.MIX_POINTER_SCHEMA
    assert definition["publication_contract"]["required_status"] == sender.MIX_STATUS


def test_scheduled_zero_wait_does_not_timeout_immediately(monkeypatch: pytest.MonkeyPatch) -> None:
    attempts = 0
    ready = object()

    def fake_load(*args: object, **kwargs: object) -> object:
        nonlocal attempts
        attempts += 1
        if attempts < 3:
            raise sender.MixReportDeferredError("formal report pending")
        return ready

    monkeypatch.setattr(sender, "load_mix_bundle", fake_load)
    monkeypatch.setattr(sender.time, "sleep", lambda _: None)
    args = SimpleNamespace(
        max_wait_seconds=0,
        poll_seconds=1,
        expected_asof="",
        target="",
    )
    assert sender._load_bundle_with_wait(args, None) is ready
    assert attempts == 3


def test_positive_wait_remains_bounded(monkeypatch: pytest.MonkeyPatch) -> None:
    clock = iter((100.0, 102.0))
    attempts = 0

    def fake_load(*args: object, **kwargs: object) -> object:
        nonlocal attempts
        attempts += 1
        raise sender.MixReportDeferredError("formal report pending")

    monkeypatch.setattr(sender, "load_mix_bundle", fake_load)
    monkeypatch.setattr(sender.time, "monotonic", lambda: next(clock))
    monkeypatch.setattr(sender.time, "sleep", lambda _: None)
    args = SimpleNamespace(
        max_wait_seconds=1,
        poll_seconds=1,
        expected_asof="",
        target="",
    )
    with pytest.raises(sender.MixReportDeferredError, match="formal report pending"):
        sender._load_bundle_with_wait(args, None)
    assert attempts == 1


def test_manual_sender_default_is_bounded_and_zero_requires_explicit_argument() -> None:
    assert sender._parser().parse_args([]).max_wait_seconds == 4 * 60 * 60
    assert sender._parser().parse_args(["--max-wait-seconds", "0"]).max_wait_seconds == 0


def _sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _record(path: Path) -> dict[str, Any]:
    return {"path": str(path.resolve()), "size": path.stat().st_size, "sha256": _sha(path)}


@pytest.mark.parametrize(
    "raw",
    [
        "token=123456:ABCDEFGHIJKLMNOPQRSTUVWXYZ chat_id=998877 query=https://example.test/?secret=abc",
        "https://api.telegram.org/bot123456:ABCDEFGHIJKLMNOPQRSTUVWXYZ/sendMessage?chat_id=998877",
        '{"token":"123456:ABCDEFGHIJKLMNOPQRSTUVWXYZ","chat_id":"998877","query":"https://example.test/?secret=abc"}',
    ],
)
def test_sensitive_log_boundary_redacts_token_chat_and_query(raw: str) -> None:
    safe = sender._redact_sensitive(raw)
    assert "123456:ABCDEFGHIJKLMNOPQRSTUVWXYZ" not in safe
    assert "998877" not in safe
    assert "secret=abc" not in safe
    assert "[REDACTED]" in safe


# ------------------------------------------------------------------------------------------------------------------
# current MIX C0 report refs (LATEST_C0_RUN.json -> c0/<run_id>/manifest.json -> artifacts)
#
# Same-run integrity (exact refs, bytes, schema/status/H, manifest/payload agreement, ETF/non-ordinary
# exclusion, prediction counts, no formal-model claim) is KEEP and raises.  Differences from the
# *current* published state (newer data, changed identity, unavailable runtime) are labels: the
# report is still sent and the summary shows the report's data day next to the current one.
# ------------------------------------------------------------------------------------------------------------------
C0_RUN_ID = "c0_20260821T080000000000Z_0123456789"
C0_ASOF = "2026-08-21"
C0_TARGET = "2026-08-24"
C0_PAIRS = (("2330", "TWSE"), ("6488", "TPEX"))
C0_MODEL = {
    "formal_model_qualified": False,
    "artifact_binding": {"sha256": "a" * 64},
    "pointer_binding": {"sha256": "b" * 64},
}


def _write_json(path: Path, value: dict[str, Any]) -> dict[str, Any]:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return _record(path)


def _c0_header(schema: str) -> dict[str, Any]:
    return {
        "schema": schema,
        "profile": "C0",
        "status": sender.MIX_STATUS,
        "horizons": list(sender.HORIZONS),
        "run_id": C0_RUN_ID,
        "as_of_date": C0_ASOF,
    }


def use_current(
    monkeypatch: pytest.MonkeyPatch,
    *,
    asof: str = C0_ASOF,
    pairs: tuple[tuple[str, str], ...] | None = C0_PAIRS,
    available: bool = True,
) -> None:
    """Replace the read of the current published state (labels only) with a fixed answer."""

    def capture() -> dict[str, Any]:
        if not available:
            return {
                "available": False, "asof": None, "binding": None, "pairs": None, "readiness": None,
                "data_time": None, "version_labels": [],
                "labels": [{"code": "current_runtime_unavailable", "recorded": None,
                            "actual": "OSError: head unreadable", "detail": None}],
            }
        return {
            "available": True,
            "asof": asof,
            "binding": {"schema": "stock-daily-c0-runtime-binding-v2", "asof": asof,
                        "snapshot_fingerprint": hashlib.sha256(asof.encode()).hexdigest()},
            "pairs": None if pairs is None else tuple(sorted(pairs)),
            "readiness": {"ready": True, "status": "ready"},
            "data_time": None,
            "version_labels": [],
            "labels": [],
        }

    monkeypatch.setattr(sender, "_capture_runtime", capture)


def make_c0_fixture(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    *,
    payload_update=None,
    manifest_update=None,
    pointer_update=None,
    omit_artifacts: tuple[str, ...] = (),
) -> tuple[sender.MixSenderPaths, dict[str, Path]]:
    """A producer-shaped current MIX C0 run; ``*_update`` edit a document before it is sealed by its ref."""
    use_current(monkeypatch)
    reports = tmp_path / "mix" / "reports"
    run_root = reports / "c0" / C0_RUN_ID
    run_root.mkdir(parents=True)
    telegram = tmp_path / "telegram"
    (telegram / "data").mkdir(parents=True)
    (telegram / "logs").mkdir()
    files = {
        "html": run_root / "report.html",
        "excel": run_root / "MIX_C0.xlsx",
        "input_sources": run_root / "published_inputs.json",
        "payload": run_root / "report_payload.json",
        "manifest": run_root / "manifest.json",
        "pointer": reports / "LATEST_C0_RUN.json",
    }
    files["html"].write_bytes(b"<html><body>MIX C0</body></html>\n")
    files["excel"].write_bytes(b"xlsx-c0-fixture")
    refs = {"html": _record(files["html"]), "excel": _record(files["excel"])}
    refs["input_sources"] = _write_json(
        files["input_sources"], {"schema": "stock-data-published-inputs-v1", "snapshot_fingerprint": "f" * 64}
    )
    predictions = [
        {
            "stock_id": stock_id, "market": market, "horizon": horizon, "prediction_status": "predicted",
            "prob_up": 0.55, "expected_return": 0.004,
            "target_session_date": C0_TARGET if horizon == 1 else None,
        }
        for stock_id, market in C0_PAIRS
        for horizon in sender.HORIZONS
    ]
    forward = {"base_model_version": "c0-linear-fixture", "version_labels": []}
    payload: dict[str, Any] = {
        **_c0_header(sender.MIX_PAYLOAD_SCHEMA),
        "created_at": "2026-08-21T16:05:00+08:00",
        "predictions": predictions,
        "prediction_count": len(predictions),
        "usable_prediction_count": len(predictions),
        "universe_policy": {
            "selection": "current_verified_ordinary_twse_tpex",
            "training_scope_is_inference_allowlist": False,
        },
        "coverage": {
            "records": [
                {"stock_id": stock_id, "market": market, "is_ordinary_share": True, "security_type": "ordinary_share"}
                for stock_id, market in C0_PAIRS
            ],
            "excluded": [],
        },
        "fit_performed": False,
        "backtests_performed": False,
        "stock_order_is_ranking": False,
        "model": dict(C0_MODEL),
        "delivery_status": "not_requested",
        "forward_binding": dict(forward),
        "input_sources": refs["input_sources"],
        "source_binding": {"route_statuses": {"finmind_price_adj": "COMPLETE"},
                           "readiness": {"ready": True, "status": "ready"}},
        "runtime_evidence": {"consumer_id": "mix"},
        "data_time": {"freshness_status": "latest", "head": {"published_at": "2026-08-21T15:40:00+08:00"}},
    }
    if payload_update is not None:
        payload_update(payload)
    refs["payload"] = _write_json(files["payload"], payload)
    manifest: dict[str, Any] = {
        **_c0_header(sender.MIX_MANIFEST_SCHEMA),
        "created_at": "2026-08-21T16:05:01+08:00",
        "artifacts": {key: value for key, value in refs.items() if key not in omit_artifacts},
        "prediction_count": payload["prediction_count"],
        "usable_prediction_count": payload["usable_prediction_count"],
        "formal_model_qualified": False,
        "delivery_status": "not_requested",
        "forward_binding": dict(forward),
        "model": dict(C0_MODEL),
    }
    if manifest_update is not None:
        manifest_update(manifest)
    pointer: dict[str, Any] = {**_c0_header(sender.MIX_POINTER_SCHEMA), "manifest": _write_json(files["manifest"], manifest)}
    if pointer_update is not None:
        pointer_update(pointer)
    _write_json(files["pointer"], pointer)
    paths = sender.MixSenderPaths(
        namespace_root=tmp_path / "mix",
        reports_root=reports,
        pointer_path=files["pointer"],
        telegram_root=telegram,
        state_path=telegram / "data" / "mix_report_delivery_state.json",
        lock_path=telegram / "data" / "mix_report_delivery.lock",
        log_dir=telegram / "logs",
    )
    return paths, files


class RecordingTelegram:
    def __init__(self, *, missing: str = "") -> None:
        self.messages: list[str] = []
        self.documents: list[tuple[Path, str]] = []
        self.missing = missing

    def send_message(self, text: str) -> dict[str, Any]:
        self.messages.append(text)
        return {} if self.missing == "summary" else {"message_id": len(self.messages)}

    def send_document(self, path: Path, caption: str) -> dict[str, Any]:
        self.documents.append((path, caption))
        return {} if self.missing == "document" else {"message_id": 100 + len(self.documents)}


def _codes(labels) -> list[str]:
    return [item["code"] for item in labels]


def test_c0_current_bundle_verifies_same_run_refs_and_lists_attachments(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    paths, files = make_c0_fixture(tmp_path, monkeypatch)
    bundle = sender.load_mix_bundle(paths)
    assert bundle.run_id == C0_RUN_ID
    assert bundle.expected_asof == C0_ASOF
    assert bundle.target == ""
    assert bundle.status == sender.MIX_STATUS
    # Only the readable Excel report is uploaded; manifest, payload and HTML are verified but stay on disk.
    assert [item.path.name for item in bundle.attachments] == ["MIX_C0.xlsx"]
    assert {item.path for item in bundle.verified_files} == {
        files[name].resolve() for name in ("pointer", "manifest", "payload", "html", "excel", "input_sources")
    }
    assert bundle.current_positive_pairs == tuple(sorted(C0_PAIRS))
    assert bundle.current_asof == C0_ASOF
    assert "報告資料日：20260821" in bundle.summary_text
    assert "目前：最新資料日與報告相同（20260821）" in bundle.summary_text
    assert "報告產生時是否為最新資料：是（依已驗證交易日曆）" in bundle.summary_text
    # Report fields the fixture does not carry (adoption fields) are labels, never a gate.
    assert "report_field_missing" in _codes(bundle.version_labels)
    assert "newer_data_available" not in _codes(bundle.version_labels)


def test_c0_missing_pointer_is_deferred_and_never_falls_back(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    paths, files = make_c0_fixture(tmp_path, monkeypatch)
    # An old formal pointer next to it must never be picked up as a fallback.
    (paths.reports_root / "LATEST_PRODUCTION_RUN.json").write_text(files["pointer"].read_text("utf-8"), "utf-8")
    files["pointer"].unlink()
    with pytest.raises(sender.MixReportDeferredError, match="尚未產生"):
        sender.load_mix_bundle(paths)


@pytest.mark.parametrize("name", ["manifest", "payload", "html", "excel", "input_sources"])
def test_c0_tampered_artifact_bytes_fail_closed(tmp_path: Path, monkeypatch: pytest.MonkeyPatch, name: str) -> None:
    paths, files = make_c0_fixture(tmp_path, monkeypatch)
    files[name].write_bytes(files[name].read_bytes() + b" ")
    with pytest.raises(sender.MixReportIntegrityError, match="size/SHA-256 不一致"):
        sender.load_mix_bundle(paths)


@pytest.mark.parametrize(
    ("field", "value"),
    [("status", "candidate"), ("schema", "mix-production-run-v1"), ("profile", "C1"), ("horizons", [1, 5, 20])],
)
def test_c0_pointer_schema_status_or_horizons_mismatch_fails_closed(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, field: str, value: object
) -> None:
    paths, _ = make_c0_fixture(tmp_path, monkeypatch, pointer_update=lambda pointer: pointer.update({field: value}))
    with pytest.raises(sender.MixReportIntegrityError, match="schema/status/H"):
        sender.load_mix_bundle(paths)


@pytest.mark.parametrize("document", ["manifest", "payload"])
def test_c0_resealed_document_with_other_status_still_fails_closed(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, document: str
) -> None:
    update = lambda value: value.update({"status": "blocked"})  # noqa: E731
    kwargs = {"manifest_update": update} if document == "manifest" else {"payload_update": update}
    paths, _ = make_c0_fixture(tmp_path, monkeypatch, **kwargs)
    with pytest.raises(sender.MixReportIntegrityError, match="schema/status/H"):
        sender.load_mix_bundle(paths)


def test_c0_run_id_outside_immutable_namespace_is_rejected(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    paths, _ = make_c0_fixture(tmp_path, monkeypatch, pointer_update=lambda pointer: pointer.update(run_id="latest"))
    with pytest.raises(sender.MixReportIntegrityError, match="immutable run namespace"):
        sender.load_mix_bundle(paths)


def test_c0_manifest_ref_outside_the_run_root_is_rejected(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    paths, files = make_c0_fixture(tmp_path, monkeypatch)
    elsewhere = paths.reports_root / "candidate" / "manifest.json"
    elsewhere.parent.mkdir()
    elsewhere.write_bytes(files["manifest"].read_bytes())
    pointer = json.loads(files["pointer"].read_text(encoding="utf-8"))
    pointer["manifest"] = _record(elsewhere)
    files["pointer"].write_text(json.dumps(pointer), encoding="utf-8")
    with pytest.raises(sender.MixReportIntegrityError, match="路徑不符 current run ref"):
        sender.load_mix_bundle(paths)


def test_c0_duplicate_json_key_fails_closed(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    paths, files = make_c0_fixture(tmp_path, monkeypatch)
    text = files["pointer"].read_text(encoding="utf-8")
    files["pointer"].write_text(text.replace('"profile": "C0",', '"profile": "C0", "profile": "C0",', 1), "utf-8")
    with pytest.raises(sender.MixReportIntegrityError, match="無法讀取或解析"):
        sender.load_mix_bundle(paths)


def test_c0_unknown_artifact_ref_is_rejected(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    def add_unknown(manifest: dict[str, Any]) -> None:
        manifest["artifacts"]["summary_pdf"] = dict(manifest["artifacts"]["html"])

    paths, _ = make_c0_fixture(tmp_path, monkeypatch, manifest_update=add_unknown)
    with pytest.raises(sender.MixReportIntegrityError, match="未知 artifact ref"):
        sender.load_mix_bundle(paths)


def test_c0_missing_optional_artifact_is_labelled_and_the_rest_is_sent(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    paths, _ = make_c0_fixture(tmp_path, monkeypatch, omit_artifacts=("html",))
    bundle = sender.load_mix_bundle(paths)
    assert [item.path.name for item in bundle.attachments] == ["MIX_C0.xlsx"]
    missing = [item for item in bundle.version_labels if item["code"] == "report_artifact_missing"]
    assert [item["detail"]["artifact"] for item in missing] == ["html"]


def test_c0_missing_excel_sends_the_summary_only(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    paths, _ = make_c0_fixture(tmp_path, monkeypatch, omit_artifacts=("excel",))
    bundle = sender.load_mix_bundle(paths)
    assert bundle.attachments == ()
    missing = [item for item in bundle.version_labels if item["code"] == "report_artifact_missing"]
    assert [item["detail"]["artifact"] for item in missing] == ["excel"]
    client = RecordingTelegram()
    assert sender.deliver_mix_bundle(bundle, client, paths.state_path) == (True, 1)
    assert len(client.messages) == 1 and client.documents == []


def test_c0_newer_published_data_is_a_label_and_the_report_is_still_sent(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    paths, _ = make_c0_fixture(tmp_path, monkeypatch)
    same_day = sender.load_mix_bundle(paths)
    use_current(monkeypatch, asof="2026-08-24")
    bundle = sender.load_mix_bundle(paths)
    newer = [item for item in bundle.version_labels if item["code"] == "newer_data_available"]
    assert newer and newer[0]["recorded"] == C0_ASOF and newer[0]["actual"] == "2026-08-24"
    assert bundle.expected_asof == C0_ASOF and bundle.current_asof == "2026-08-24"
    assert "目前：報告資料日 20260821；目前最新資料日 20260824（本報告不是最新資料）" in bundle.summary_text
    assert "目前:newer_data_available" in bundle.unmet_conditions
    # The dedup identity and the report-only summary never depend on the current state.
    assert bundle.fingerprint == same_day.fingerprint
    assert bundle.summary_key_text == same_day.summary_key_text


def test_c0_unavailable_current_runtime_is_a_label(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    paths, _ = make_c0_fixture(tmp_path, monkeypatch)
    use_current(monkeypatch, available=False)
    bundle = sender.load_mix_bundle(paths)
    assert "current_runtime_unavailable" in _codes(bundle.version_labels)
    # The human summary shows the translated label; the raw code stays in version_labels.
    assert "目前：無法取得目前已發布資料狀態（目前已發布資料暫時無法讀取）；報告資料日 20260821" in bundle.summary_text


def test_c0_runtime_pointer_naming_another_run_is_labelled(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    paths, _ = make_c0_fixture(tmp_path, monkeypatch)
    other = "c0_20260822T080000000000Z_abcdefabcd"
    _write_json(paths.reports_root / sender.MIX_RUNTIME_POINTER_NAME, {"run_id": other, "as_of_date": C0_ASOF})
    bundle = sender.load_mix_bundle(paths)
    label = next(item for item in bundle.version_labels if item["code"] == "newer_run_not_selected")
    assert (label["recorded"], label["actual"]) == (C0_RUN_ID, other)


def test_c0_expected_asof_mismatch_is_reported_at_once_without_polling(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    paths, _ = make_c0_fixture(tmp_path, monkeypatch)
    assert sender.load_mix_bundle(paths, expected_asof="20260821").run_id == C0_RUN_ID
    with pytest.raises(sender.MixReportRequestMismatchError, match="20260824 與 MIX 報告資料日 20260821 不符") as raised:
        sender.load_mix_bundle(paths, expected_asof="2026-08-24")
    assert raised.value.retryable is False
    monkeypatch.setattr(sender.time, "sleep", lambda _: pytest.fail("a request mismatch must not be polled"))
    args = SimpleNamespace(max_wait_seconds=60, poll_seconds=1, expected_asof="20260824", target="")
    with pytest.raises(sender.MixReportRequestMismatchError):
        sender._load_bundle_with_wait(args, paths)


def test_c0_explicit_target_requires_a_matching_h1_endpoint(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    paths, _ = make_c0_fixture(tmp_path, monkeypatch)
    assert sender.load_mix_bundle(paths, target="20260824").target == C0_TARGET
    with pytest.raises(sender.MixReportIntegrityError, match="--target"):
        sender.load_mix_bundle(paths, target="2026-08-25")


def _add_etf(payload: dict[str, Any], record: dict[str, Any] | None = None) -> None:
    payload["coverage"]["records"].append(
        record or {"stock_id": "0050", "market": "TWSE", "is_ordinary_share": False, "security_type": "etf"}
    )
    payload["predictions"].extend(
        {"stock_id": "0050", "market": "TWSE", "horizon": horizon, "prediction_status": "predicted",
         "prob_up": 0.5, "expected_return": 0.0, "target_session_date": C0_TARGET if horizon == 1 else None}
        for horizon in sender.HORIZONS
    )
    payload["prediction_count"] = payload["usable_prediction_count"] = len(payload["predictions"])


@pytest.mark.parametrize(
    "record",
    [
        {"is_ordinary_share": False, "security_type": "etf"},
        # Each half of the positive ordinary evidence is required on its own.
        {"is_ordinary_share": True, "security_type": "etf"},
        {"is_ordinary_share": False, "security_type": "ordinary_share"},
        {"security_type": "ordinary_share"},
        {"is_ordinary_share": True, "security_type": "ordinary_share",
         "current_ordinary_classification_verified": False},
    ],
    ids=["etf", "ordinary_flag_but_etf_type", "ordinary_type_but_flag_false", "flag_missing", "unverified"],
)
def test_c0_etf_or_non_ordinary_prediction_fails_closed(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, record: dict[str, Any]
) -> None:
    full = {"stock_id": "0050", "market": "TWSE", **record}
    paths, _ = make_c0_fixture(tmp_path, monkeypatch, payload_update=lambda payload: _add_etf(payload, full),
                               manifest_update=lambda manifest: manifest.update(prediction_count=12, usable_prediction_count=12))
    # Even when the current ordinary identity would allow the pair, the report's own classification wins.
    use_current(monkeypatch, pairs=(*C0_PAIRS, ("0050", "TWSE")))
    with pytest.raises(sender.MixReportIntegrityError, match="非普通股"):
        sender.load_mix_bundle(paths)


def test_c0_prediction_outside_the_report_coverage_fails_closed(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    def drop_coverage(payload: dict[str, Any]) -> None:
        payload["coverage"]["records"] = payload["coverage"]["records"][:1]

    paths, _ = make_c0_fixture(tmp_path, monkeypatch, payload_update=drop_coverage)
    with pytest.raises(sender.MixReportIntegrityError, match="不在報告自身的普通股 coverage"):
        sender.load_mix_bundle(paths)


def test_c0_without_report_coverage_uses_current_identity_or_fails_closed(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    paths, _ = make_c0_fixture(tmp_path, monkeypatch, payload_update=lambda payload: payload.pop("coverage"))
    bundle = sender.load_mix_bundle(paths)
    assert any(item["code"] == "report_field_missing" and item["detail"]["field"] == "payload.coverage.records"
               for item in bundle.version_labels)
    use_current(monkeypatch, pairs=(("2330", "TWSE"),))
    with pytest.raises(sender.MixReportIntegrityError, match="不在目前普通股名單"):
        sender.load_mix_bundle(paths)
    use_current(monkeypatch, pairs=None)
    with pytest.raises(sender.MixReportIntegrityError, match="無法確認 ETF／非普通股已排除"):
        sender.load_mix_bundle(paths)


def test_c0_pair_dropped_from_the_current_identity_is_labelled(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    paths, _ = make_c0_fixture(tmp_path, monkeypatch)
    use_current(monkeypatch, pairs=(("2330", "TWSE"),))
    bundle = sender.load_mix_bundle(paths)
    label = next(item for item in bundle.version_labels if item["code"] == "prediction_pair_not_in_current_identity")
    assert label["detail"]["examples"] == ["6488/TPEX"]


def test_c0_missing_or_unusable_predictions_are_deferred(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    paths, _ = make_c0_fixture(tmp_path / "empty", monkeypatch, payload_update=lambda payload: payload.update(predictions=[]))
    with pytest.raises(sender.MixReportDeferredError, match="沒有 prediction rows"):
        sender.load_mix_bundle(paths)

    def unusable(payload: dict[str, Any]) -> None:
        for row in payload["predictions"]:
            row.update(prediction_status="insufficient_history", prob_up=None, expected_return=None)
        payload["usable_prediction_count"] = 0

    paths, _ = make_c0_fixture(tmp_path / "unusable", monkeypatch, payload_update=unusable,
                               manifest_update=lambda manifest: manifest.update(usable_prediction_count=0))
    with pytest.raises(sender.MixReportDeferredError, match="沒有 usable prediction"):
        sender.load_mix_bundle(paths)


def test_c0_prediction_counts_and_exact_horizons_are_enforced(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    paths, _ = make_c0_fixture(tmp_path / "count", monkeypatch,
                               manifest_update=lambda manifest: manifest.update(prediction_count=7))
    with pytest.raises(sender.MixReportIntegrityError, match="manifest prediction counts 不一致"):
        sender.load_mix_bundle(paths)

    def drop_h60(payload: dict[str, Any]) -> None:
        payload["predictions"] = [row for row in payload["predictions"] if row["horizon"] != 60]
        payload["prediction_count"] = payload["usable_prediction_count"] = len(payload["predictions"])

    paths, _ = make_c0_fixture(tmp_path / "h60", monkeypatch, payload_update=drop_h60,
                               manifest_update=lambda manifest: manifest.update(prediction_count=6, usable_prediction_count=6))
    with pytest.raises(sender.MixReportIntegrityError, match="exact H1/5/20/60"):
        sender.load_mix_bundle(paths)


@pytest.mark.parametrize(
    ("update", "message"),
    [
        (lambda payload: payload["model"].update(formal_model_qualified=True), "formal model qualified"),
        (lambda payload: payload.update(fit_performed=True), "fit_performed"),
        (lambda payload: payload.update(stock_order_is_ranking=True), "stock_order_is_ranking"),
        (lambda payload: payload.update(delivery_status="sent"), "delivery_status"),
        (lambda payload: payload["predictions"][0].update(prob_up=1.5), "prob_up 不在 0..1"),
        (lambda payload: payload["universe_policy"].update(training_scope_is_inference_allowlist=True), "universe"),
    ],
)
def test_c0_formal_or_unsupported_claims_fail_closed(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, update, message: str
) -> None:
    def apply(payload: dict[str, Any]) -> None:
        payload["model"] = dict(payload["model"])
        update(payload)

    if message == "formal model qualified":
        # The same run must agree on the model block, so reseal it in the manifest too.
        manifest_update = lambda manifest: manifest.update(model={**C0_MODEL, "formal_model_qualified": True})  # noqa: E731
    else:
        manifest_update = None
    paths, _ = make_c0_fixture(tmp_path, monkeypatch, payload_update=apply, manifest_update=manifest_update)
    with pytest.raises(sender.MixReportIntegrityError, match=message):
        sender.load_mix_bundle(paths)


def test_c0_same_run_manifest_and_payload_bindings_must_agree(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    paths, _ = make_c0_fixture(tmp_path / "forward", monkeypatch,
                               manifest_update=lambda manifest: manifest.update(forward_binding={"base_model_version": "other"}))
    with pytest.raises(sender.MixReportIntegrityError, match="forward_binding"):
        sender.load_mix_bundle(paths)
    paths, _ = make_c0_fixture(tmp_path / "model", monkeypatch,
                               manifest_update=lambda manifest: manifest.update(
                                   model={**C0_MODEL, "artifact_binding": {"sha256": "c" * 64}}))
    with pytest.raises(sender.MixReportIntegrityError, match="model.artifact_binding"):
        sender.load_mix_bundle(paths)


def test_c0_delivery_is_idempotent_and_records_report_and_current_data_days(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    paths, _ = make_c0_fixture(tmp_path, monkeypatch)
    bundle = sender.load_mix_bundle(paths)
    client = RecordingTelegram()
    assert sender.deliver_mix_bundle(bundle, client, paths.state_path) == (True, 2)
    assert sender.deliver_mix_bundle(bundle, client, paths.state_path) == (False, 0)
    assert len(client.messages) == 1 and "MIX C0 有限基準" in client.messages[0]
    assert [path.name for path, _ in client.documents] == [item.path.name for item in bundle.attachments]
    assert all("報告資料日：20260821" in caption for _, caption in client.documents)
    state = json.loads(paths.state_path.read_text(encoding="utf-8"))
    entry = state["deliveries"][bundle.fingerprint]
    assert entry["status"] == entry["report_delivery_status"] == "complete"
    assert entry["run_id"] == C0_RUN_ID
    assert entry["report_asof"] == C0_ASOF and entry["current_asof_at_send"] == C0_ASOF
    assert entry["formal_model_qualified"] is False
    assert len(entry["messages"]) == 2 and all(item["message_id"] > 0 for item in entry["messages"])
    assert state["last_complete_fingerprint"] == bundle.fingerprint


def test_c0_newer_data_after_delivery_does_not_resend(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    paths, _ = make_c0_fixture(tmp_path, monkeypatch)
    client = RecordingTelegram()
    assert sender.deliver_mix_bundle(sender.load_mix_bundle(paths), client, paths.state_path) == (True, 2)
    use_current(monkeypatch, asof="2026-08-24")
    assert sender.deliver_mix_bundle(sender.load_mix_bundle(paths), client, paths.state_path) == (False, 0)
    assert len(client.messages) == 1


@pytest.mark.parametrize("missing", ["summary", "document"])
def test_c0_missing_telegram_message_id_stays_retryable(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, missing: str
) -> None:
    paths, _ = make_c0_fixture(tmp_path, monkeypatch)
    bundle = sender.load_mix_bundle(paths)
    with pytest.raises(sender.MixReportIntegrityError, match="message_id"):
        sender.deliver_mix_bundle(bundle, RecordingTelegram(missing=missing), paths.state_path)
    state = json.loads(paths.state_path.read_text(encoding="utf-8"))
    entry = state["deliveries"][bundle.fingerprint]
    assert entry["status"] == "in_progress"
    assert "last_complete_fingerprint" not in state
    retry = RecordingTelegram()
    delivered, count = sender.deliver_mix_bundle(bundle, retry, paths.state_path)
    assert delivered is True
    # Only the items without a recorded receipt are sent again.
    assert count == (2 if missing == "summary" else 1)
    assert len(retry.messages) == (1 if missing == "summary" else 0)


class UnusedTelegram:
    def send_message(self, text: str) -> dict[str, Any]:
        raise AssertionError("must not call the Telegram API")

    def send_document(self, path: Path, caption: str) -> dict[str, Any]:
        raise AssertionError("must not call the Telegram API")


def _summary_key(bundle) -> str:
    text = bundle.summary_key_text or bundle.summary_text
    return "summary:" + hashlib.sha256(text.encode("utf-8")).hexdigest()


def _write_complete_state(paths, bundle, messages, sent_items=None) -> None:
    entry = {"status": "complete", "report_delivery_status": "complete", "completed_at": "2026-08-22T08:00:00+08:00",
             "messages": messages}
    if sent_items is not None:
        entry["sent_items"] = sent_items
    paths.state_path.write_text(json.dumps({
        "schema_version": "mix_report_delivery_state_v1",
        "last_complete_fingerprint": bundle.fingerprint,
        "deliveries": {bundle.fingerprint: entry},
    }), encoding="utf-8")


def _reopen_label(entry) -> dict[str, Any]:
    labels = [item for item in entry["version_labels"] if item["code"] == "complete_receipt_corrupt_reopened"]
    assert len(labels) == 1
    return labels[0]


def test_c0_corrupt_complete_delivery_is_reopened_and_retried_without_force(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    paths, _ = make_c0_fixture(tmp_path, monkeypatch)
    bundle = sender.load_mix_bundle(paths)
    _write_complete_state(paths, bundle, [{"key": "summary:old", "message_id": None}])
    client = RecordingTelegram()
    # No --force: the corrupt complete receipt is reopened and every unproven item is retried.
    assert sender.deliver_mix_bundle(bundle, client, paths.state_path) == (True, 2)
    assert len(client.messages) == 1 and len(client.documents) == len(bundle.attachments)
    state = json.loads(paths.state_path.read_text(encoding="utf-8"))
    entry = state["deliveries"][bundle.fingerprint]
    assert entry["status"] == entry["report_delivery_status"] == "complete"
    assert entry["completed_at"] != "2026-08-22T08:00:00+08:00"
    label = _reopen_label(entry)
    assert label["recorded"] == "complete" and label["actual"] == "in_progress"
    assert "message_id" in label["detail"]["reason"]
    assert label["detail"]["kept_receipts"] == 0 and label["detail"]["dropped_receipts"] == 1
    assert label["detail"]["previous_completed_at"] == "2026-08-22T08:00:00+08:00"
    assert entry["reopen_history"] == [label["detail"]]
    assert {item["key"] for item in entry["messages"]} == {_summary_key(bundle), *(a.key for a in bundle.attachments)}
    assert state["last_complete_fingerprint"] == bundle.fingerprint
    # Now a valid complete receipt: already delivered, no API call, no second reopen.
    assert sender.deliver_mix_bundle(bundle, UnusedTelegram(), paths.state_path) == (False, 0)


def test_c0_reopened_complete_never_resends_items_with_valid_receipts(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    paths, _ = make_c0_fixture(tmp_path, monkeypatch)
    bundle = sender.load_mix_bundle(paths)
    (excel,) = bundle.attachments
    messages = [
        {"key": _summary_key(bundle), "message_id": 7},
        {"key": _summary_key(bundle), "message_id": 9},  # duplicate key: dropped, first receipt kept
        {"key": excel.key, "message_id": 0},  # invalid id: unproven, retried
        {"key": "attachment:unknown", "message_id": 10},  # not in the verified bundle: dropped
        "not-a-mapping",
    ]
    _write_complete_state(paths, bundle, messages, sent_items=[_summary_key(bundle), excel.key])
    client = RecordingTelegram()
    assert sender.deliver_mix_bundle(bundle, client, paths.state_path) == (True, 1)
    assert client.messages == []  # summary had a valid receipt
    assert [path for path, _ in client.documents] == [excel.path]
    entry = json.loads(paths.state_path.read_text(encoding="utf-8"))["deliveries"][bundle.fingerprint]
    assert entry["status"] == "complete"
    by_key = {item["key"]: item for item in entry["messages"]}
    assert len(by_key) == len(entry["messages"]) == 1 + len(bundle.attachments)
    assert by_key[_summary_key(bundle)]["message_id"] == 7 and by_key[excel.key]["message_id"] > 0
    detail = _reopen_label(entry)["detail"]
    assert detail["kept_receipts"] == 1 and detail["dropped_receipts"] == 4


def test_c0_complete_with_valid_receipts_but_corrupt_sent_items_reopens_without_resending(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    paths, _ = make_c0_fixture(tmp_path, monkeypatch)
    bundle = sender.load_mix_bundle(paths)
    messages = [{"key": _summary_key(bundle), "message_id": 1}] + [
        {"key": item.key, "message_id": 100 + index} for index, item in enumerate(bundle.attachments, 1)]
    _write_complete_state(paths, bundle, messages, sent_items=None)
    # Every item has a valid receipt, so nothing is re-sent; only the bookkeeping is repaired.
    assert sender.deliver_mix_bundle(bundle, UnusedTelegram(), paths.state_path) == (True, 0)
    entry = json.loads(paths.state_path.read_text(encoding="utf-8"))["deliveries"][bundle.fingerprint]
    assert entry["status"] == "complete" and set(entry["sent_items"]) == {item["key"] for item in messages}
    assert "sent_items" in _reopen_label(entry)["detail"]["reason"]
    assert sender.deliver_mix_bundle(bundle, UnusedTelegram(), paths.state_path) == (False, 0)


def test_c0_valid_complete_delivery_makes_no_api_call(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    paths, _ = make_c0_fixture(tmp_path, monkeypatch)
    bundle = sender.load_mix_bundle(paths)
    assert sender.deliver_mix_bundle(bundle, RecordingTelegram(), paths.state_path) == (True, 2)
    before = paths.state_path.read_bytes()
    assert sender.deliver_mix_bundle(bundle, UnusedTelegram(), paths.state_path) == (False, 0)
    assert paths.state_path.read_bytes() == before
    entry = json.loads(before)["deliveries"][bundle.fingerprint]
    assert not any(item["code"] == "complete_receipt_corrupt_reopened" for item in entry["version_labels"])


def test_mix_logger_writes_to_the_delivery_log_file(tmp_path: Path) -> None:
    import logging
    sender.setup_logging(tmp_path, sender.LOGGER)
    try:
        sender.LOGGER.error("FAILED: fixture mix error")
        for handler in sender.LOGGER.handlers:
            handler.flush()
        (log_path,) = tmp_path.glob("telegram_delivery_*.log")
        assert "FAILED: fixture mix error" in log_path.read_text(encoding="utf-8")
    finally:
        for handler in list(sender.LOGGER.handlers):
            if isinstance(handler, logging.FileHandler):
                handler.close()


def test_unexpected_error_is_logged_and_still_raised(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    import logging
    log_dir = tmp_path / "telegram" / "logs"
    monkeypatch.setattr(sender, "TELEGRAM_ROOT", tmp_path / "telegram")
    monkeypatch.setattr(sys, "argv", ["mix_report_sender.py", "--dry-run", "--log-dir", str(log_dir),
                                      "--state", str(tmp_path / "telegram" / "state.json"),
                                      "--lock", str(tmp_path / "telegram" / "mix.lock")])

    def locked(*args, **kwargs):
        raise OSError("fixture state file locked")

    monkeypatch.setattr(sender, "_load_bundle_with_wait", locked)
    try:
        with pytest.raises(OSError, match="fixture state file locked"):
            sender.main()
        for handler in sender.LOGGER.handlers:
            handler.flush()
        (log_path,) = log_dir.glob("telegram_delivery_*.log")
        text = log_path.read_text(encoding="utf-8")
        assert "FAILED（未預期錯誤）" in text and "fixture state file locked" in text
    finally:
        for handler in list(sender.LOGGER.handlers):
            if isinstance(handler, logging.FileHandler):
                handler.close()


def test_c0_delivery_state_schema_mismatch_still_fails_closed(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    paths, _ = make_c0_fixture(tmp_path, monkeypatch)
    bundle = sender.load_mix_bundle(paths)
    paths.state_path.write_text(json.dumps({"schema_version": "other", "deliveries": {}}), encoding="utf-8")
    with pytest.raises(sender.MixReportIntegrityError, match="state schema 不符"):
        sender.deliver_mix_bundle(bundle, UnusedTelegram(), paths.state_path)


def test_c0_artifact_changed_after_verification_blocks_upload(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    paths, files = make_c0_fixture(tmp_path, monkeypatch)
    bundle = sender.load_mix_bundle(paths)
    files["excel"].write_bytes(b"xlsx-changed-after-verification")
    client = RecordingTelegram()
    with pytest.raises(sender.MixReportIntegrityError, match="same-job artifact changed"):
        sender.deliver_mix_bundle(bundle, client, paths.state_path)
    assert client.messages == [] and client.documents == []


# ------------------------------------------------------------------------------------------------------------------
# H1 model source (MIX shows the pre-open model on H1 when its snapshot is fresh, else the C0 fallback).
# Reports without ``h1_model_source`` keep their exact summary text, so earlier deliveries are never resent.
# ------------------------------------------------------------------------------------------------------------------
# summary_key_text SHA-256 of the base fixture, computed with the sender before the H1 lines existed.
PRE_H1_SUMMARY_KEY_SHA256 = "d4bcaf8dce985523fafc68772a0e70cf1806fa624c80aba2ac7e5589e3f6247a"
H1_TOP10 = [{"rank_tradable": 1, "stock_id": "2330", "market": "TWSE", "stock_name": "台積電", "in_report": True},
            {"rank_tradable": 2, "stock_id": "6488", "market": "TPEx", "stock_name": None, "in_report": True}]
H1_DISCLOSURE = "開盤前版預測下一交易日收盤方向；命中率不代表交易獲利。"


def _h1_payload(source: str, *, requested: str = "auto", reason: str | None = None, signal: str = C0_ASOF,
                gate: bool | None = True, disclosure: str = H1_DISCLOSURE):
    def update(payload: dict[str, Any]) -> None:
        payload["h1_model_source"] = source
        payload["h1_model_source_requested"] = requested
        payload["h1_preopen"] = {"schema": "mix-h1-preopen-binding-v1", "used": source == "preopen",
                                 "fallback_reason": reason, "top10": H1_TOP10 if source == "preopen" else [],
                                 "disclosure": {"summary": disclosure}}
        payload["preopen_meta"] = ({"signal_date": signal, "target_date": C0_TARGET, "gate_us_up": gate}
                                   if source == "preopen" else None)
        for record in payload["predictions"]:
            record["model_source"] = "preopen" if source == "preopen" and record["horizon"] == 1 else "c0"
    return update


def _h1_manifest(source: str):
    return lambda manifest: manifest.update({"h1_model_source": source})


def test_report_without_h1_fields_keeps_the_previous_summary_identity(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    paths, _ = make_c0_fixture(tmp_path, monkeypatch)
    bundle = sender.load_mix_bundle(paths)
    assert hashlib.sha256(bundle.summary_key_text.encode("utf-8")).hexdigest() == PRE_H1_SUMMARY_KEY_SHA256
    assert "H1：" not in bundle.summary_text


def test_preopen_h1_summary_shows_source_top10_and_disclosure(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    paths, _ = make_c0_fixture(tmp_path, monkeypatch, payload_update=_h1_payload("preopen"),
                               manifest_update=_h1_manifest("preopen"))
    bundle = sender.load_mix_bundle(paths)
    for text in (bundle.summary_text, bundle.summary_key_text):
        assert text.startswith("MIX 台股每日估計（H1 開盤前版＋H5／H20／H60 C0 有限基準）\n")
        assert "H1：開盤前版（訊號日 20260821 → 目標日 20260824；前一晚美股收漲）" in text
        assert "H1 可交易 Top10（依上漲機率→預估報酬）：2330 台積電、6488" in text
        assert "H5／H20／H60：C0 有限基準" in text and H1_DISCLOSURE in text
    assert hashlib.sha256(bundle.summary_key_text.encode("utf-8")).hexdigest() != PRE_H1_SUMMARY_KEY_SHA256
    client = RecordingTelegram()
    assert sender.deliver_mix_bundle(bundle, client, paths.state_path) == (True, 2)
    assert "H1：開盤前版" in client.messages[0]


def test_preopen_h1_summary_gate_and_disclosure_dates_are_display_formatted(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    # Older producer wording (ISO month range / ISO day) is shown the way MIX writes it now; no nested brackets.
    old_disclosure = "開盤前版預測下一交易日收盤方向；保留期（2026-01～10）方向準確率 59.0%；訊號日 2026-08-21 命中率不代表交易獲利。"
    paths, _ = make_c0_fixture(tmp_path, monkeypatch, payload_update=_h1_payload("preopen", gate=False, disclosure=old_disclosure),
                               manifest_update=_h1_manifest("preopen"))
    bundle = sender.load_mix_bundle(paths)
    assert "H1：開盤前版（訊號日 20260821 → 目標日 20260824；前一晚美股未收漲，非建議日）" in bundle.summary_text
    assert "保留期（2026 年 1～10 月）方向準確率 59.0%；訊號日 20260821 命中率不代表交易獲利。" in bundle.summary_text
    assert "2026-01～10" not in bundle.summary_text and "2026-08-21" not in bundle.summary_text


@pytest.mark.parametrize(("requested", "reason", "line"), [
    # The reason is「代碼：中文」(the same text MIX prints); unknown codes keep the code with a generic label.
    ("auto", "preopen_stale", "H1：開盤前版不可用（preopen_stale：開盤前版快照不是本次資料日，已過期），本次 H1 改用 C0 基準"),
    ("auto", "preopen_no_latest", "H1：開盤前版不可用（preopen_no_latest：找不到開盤前版最新快照），本次 H1 改用 C0 基準"),
    ("auto", "bogus_reason", "H1：開盤前版不可用（bogus_reason：其他原因，詳見原始資料），本次 H1 改用 C0 基準"),
    ("auto", None, "H1：開盤前版不可用（原因未記錄），本次 H1 改用 C0 基準"),
    ("c0", "h1_model_source_c0_requested", "H1：C0 有限基準（依設定使用 C0）"),
])
def test_c0_h1_summary_states_the_fallback(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, requested: str, reason: str, line: str
) -> None:
    paths, _ = make_c0_fixture(tmp_path, monkeypatch, payload_update=_h1_payload("c0", requested=requested, reason=reason),
                               manifest_update=_h1_manifest("c0"))
    bundle = sender.load_mix_bundle(paths)
    assert line in bundle.summary_text and line in bundle.summary_key_text


@pytest.mark.parametrize("case", ["manifest_differs", "unknown_source", "long_horizon_preopen", "stale_signal"])
def test_inconsistent_h1_report_fails_closed(tmp_path: Path, monkeypatch: pytest.MonkeyPatch, case: str) -> None:
    payload_update = _h1_payload("preopen", signal="2026-08-20" if case == "stale_signal" else C0_ASOF)
    manifest_source = "c0" if case == "manifest_differs" else "preopen"

    def update(payload: dict[str, Any]) -> None:
        payload_update(payload)
        if case == "unknown_source":
            payload["h1_model_source"] = "hgb"
        if case == "long_horizon_preopen":
            next(item for item in payload["predictions"] if item["horizon"] == 5)["model_source"] = "preopen"

    paths, _ = make_c0_fixture(tmp_path, monkeypatch, payload_update=update,
                               manifest_update=_h1_manifest("hgb" if case == "unknown_source" else manifest_source))
    with pytest.raises(sender.MixReportIntegrityError, match="H1|開盤前版"):
        sender.load_mix_bundle(paths)
