# RETIRED TEST MODULE -- not collected (tests/conftest.py collect_ignore = ["_retired"]).
# Moved verbatim from tests/test_telegram_report_sender.py on 2026-10-12; see tests/_retired/README.md
# for the removed module/feature each test exercised and the evidence that it was intentionally removed.
from __future__ import annotations

import json
import hashlib
import re
import sys
from pathlib import Path
from typing import Any

import pytest


SUITE_DIR = Path(__file__).resolve().parents[1]
if str(SUITE_DIR) not in sys.path:
    sys.path.insert(0, str(SUITE_DIR))

import telegram_report_sender as sender


def make_bundle(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> tuple[Path, Path, Path]:
    report_dir = tmp_path / "reports"
    individual_dir = report_dir / "individual"
    individual_dir.mkdir(parents=True)
    excel = report_dir / "台股完整分析報告_20260713.xlsx"
    pdf = individual_dir / "推薦個股完整分析_20260713.pdf"
    summary = report_dir / "台股完整分析摘要_20260713.md"
    payload = report_dir / "台股完整分析_payload_20260713.json"
    excel.write_bytes(b"xlsx-data")
    pdf.write_bytes(b"%PDF-1.7\n1 0 obj\n<<>>\nendobj\ntrailer\n<<>>\n%%EOF\n")
    payload.write_text(
        json.dumps(
            {
                "metadata": {"dataDate": "20260709", "forecastTargetDate": "20260713"},
                "tomorrowTop10": [{"code": "2330", "name": "台積電"}],
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )
    summary.write_text(
        "\n".join(
            (
                "## 下一交易日最終收盤推薦（最多10檔，本次1檔）",
                "|排名|門檻|qualification_status|來源|代號|股票|無條件預期報酬|上漲機率|機率估計來源|機率信賴|",
                "|---:|---|---|---|---|---|---:|---:|---|---|",
                "|1|達門檻|qualified|達標前10|2330|台積電|+2.00%|55.00%|model|高|",
                "## 兩個月內每日預期淨獲利最高（最多10檔，本次1檔）",
                "|排名|門檻|qualification_status|來源|代號|股票|持有交易日|無條件預期報酬|上漲機率|機率來源|機率信賴|每日無條件預期報酬|",
                "|---:|---|---|---|---|---|---:|---:|---:|---|---|---:|",
                "|1|達門檻|qualified|達標前10|2317|鴻海|10|+8.00%|60.00%|model|高|+0.70%|",
            )
        ),
        encoding="utf-8",
    )
    central_dir = tmp_path / "central"
    central_dir.mkdir(parents=True)
    registry = central_dir / "registry.json"
    registry.write_text('{"datasets": ["fixture"]}\n', encoding="utf-8")
    central_receipt = central_dir / "latest_manifest.json"
    registry_sha256 = hashlib.sha256(registry.read_bytes()).hexdigest()
    central_receipt.write_text(
        json.dumps(
            {
                "manifest_schema": "shared-market-data-update-manifest-v1",
                "status": "complete",
                "expected_asof": "20260709",
                "run_id": "central-fixture-20260709",
                "publishable": True,
                "fail_closed": False,
                "registry_sha256": registry_sha256,
                "coverage": {"status": "complete"},
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )
    artifact_manifest = {
        "master_payload": {
            "path": str(payload.resolve()),
            "sha256": hashlib.sha256(payload.read_bytes()).hexdigest(),
            "size": payload.stat().st_size,
        },
        "summary_markdown": {
            "path": str(summary.resolve()),
            "sha256": hashlib.sha256(summary.read_bytes()).hexdigest(),
            "size": summary.stat().st_size,
        },
        "master_excel": {
            "path": str(excel.resolve()),
            "sha256": hashlib.sha256(excel.read_bytes()).hexdigest(),
            "size": excel.stat().st_size,
        },
        "individual_report_1": {
            "path": str(pdf.resolve()),
            "sha256": hashlib.sha256(pdf.read_bytes()).hexdigest(),
            "size": pdf.stat().st_size,
        },
    }
    central_sha256 = hashlib.sha256(central_receipt.read_bytes()).hexdigest()
    manifest = report_dir / "latest_report_manifest.json"
    manifest.write_text(
        json.dumps(
            {
                "schema": sender.REPORT_MANIFEST_SCHEMA,
                "schema_version": 2,
                "run_id": "fixture-formal-20260713",
                "status": "publishable",
                "official": True,
                "actionable": True,
                "no_action": False,
                "publishable": True,
                "artifact_publishable": True,
                "execution": {
                    "status": "success",
                    "success": True,
                    "exit_code": 0,
                    "fallback_used": False,
                },
                "publication": {
                    "status": "publishable",
                    "official": True,
                    "actionable": True,
                    "no_action": False,
                    "publishable": True,
                    "artifact_publishable": True,
                },
                "generatedAt": "2026-07-12T14:28:46+08:00",
                "dataDate": "20260709",
                "forecastTargetDate": "20260713",
                "central": {
                    "receipt_path": str(central_receipt.resolve()),
                    "receipt_sha256": central_sha256,
                    "registry_sha256": registry_sha256,
                    "top_run_id": "central-fixture-20260709",
                },
                "central_receipt_sha256": central_sha256,
                "central_registry_sha256": registry_sha256,
                "central_expected_asof": "20260709",
                "masterPayload": str(payload),
                "masterPayloadSha256": hashlib.sha256(payload.read_bytes()).hexdigest(),
                "summaryMarkdown": str(summary),
                "masterExcel": str(excel),
                "overlapCodes": ["2330"],
                "individualReports": [{"name": "推薦個股完整分析", "path": str(pdf)}],
                "artifact_manifest": artifact_manifest,
                "artifact_list_sha256": sender._canonical_sha256(artifact_manifest),
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )
    # Keep the fixture in a temporary formal namespace without weakening the
    # production exact-canonical-path checks.
    monkeypatch.setattr(sender, "DEFAULT_MANIFEST_PATH", manifest)
    monkeypatch.setattr(sender, "REPORT_ROOT", report_dir)
    monkeypatch.setattr(sender, "DSTOCK_DAILY_ROOT", report_dir)
    monkeypatch.setattr(sender, "CENTRAL_RECEIPT_PATH", central_receipt)
    return manifest, excel, pdf


def test_missing_manifest_fails_closed(tmp_path: Path) -> None:
    with pytest.raises(sender.ReportUnavailableError, match="找不到最新報告 manifest"):
        sender.load_report_bundle(tmp_path / "missing" / "latest_report_manifest.json")


def test_manifest_selects_only_excel_and_pdf_and_builds_summary(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    manifest, excel, pdf = make_bundle(tmp_path, monkeypatch)
    bundle = sender.load_report_bundle(manifest)
    assert bundle.manifest_path == manifest.resolve()
    assert [item.path for item in bundle.attachments] == [excel.resolve(), pdf.resolve()]
    assert all(item.path.suffix.lower() != ".json" for item in bundle.attachments)
    assert "2330 台積電" in bundle.summary_text
    assert "2317 鴻海" in bundle.summary_text
    assert re.search(r"預測交易日：(2026-07-13|20260713)(?!\d)", bundle.summary_text)
    assert re.search(r"資料日：(2026-07-09|20260709)(?!\d)", bundle.summary_text)
    first_fingerprint = bundle.fingerprint
    excel.write_bytes(b"xlsx-data-updated")
    metadata = json.loads(manifest.read_text(encoding="utf-8"))
    metadata["artifact_manifest"]["master_excel"].update(
        {
            "sha256": hashlib.sha256(excel.read_bytes()).hexdigest(),
            "size": excel.stat().st_size,
        }
    )
    metadata["artifact_list_sha256"] = sender._canonical_sha256(metadata["artifact_manifest"])
    manifest.write_text(json.dumps(metadata, ensure_ascii=False), encoding="utf-8")
    assert sender.load_report_bundle(manifest).fingerprint != first_fingerprint


def test_dry_run_does_not_construct_telegram_client(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    manifest, _, _ = make_bundle(tmp_path, monkeypatch)

    def fail_if_constructed(*_: Any, **__: Any) -> Any:
        raise AssertionError("dry-run 不應建立 Telegram client")

    monkeypatch.setattr(sender, "TelegramClient", fail_if_constructed)
    args = sender.build_parser().parse_args(
        [
            "--dry-run",
            "--manifest",
            str(manifest),
            "--log-dir",
            str(tmp_path / "logs"),
            "--wait-minutes",
            "0",
            "--poll-seconds",
            "1",
        ]
    )
    assert sender.run(args) == 0


def test_manifest_rejects_attachment_outside_reports(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    manifest, _, _ = make_bundle(tmp_path, monkeypatch)
    outside = tmp_path / "secret.txt"
    outside.write_text("do-not-send", encoding="utf-8")
    payload = json.loads(manifest.read_text(encoding="utf-8"))
    payload["masterExcel"] = str(outside)
    manifest.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")
    with pytest.raises(sender.ReportUnavailableError, match="reports 目錄以外"):
        sender.load_report_bundle(manifest)


def test_manifest_rejects_tampered_payload(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    manifest, _, _ = make_bundle(tmp_path, monkeypatch)
    payload = Path(json.loads(manifest.read_text(encoding="utf-8"))["masterPayload"])
    payload.write_text(payload.read_text(encoding="utf-8") + "\n tampered", encoding="utf-8")
    with pytest.raises(sender.ReportUnavailableError, match="artifact_manifest.master_payload hash/size 不一致"):
        sender.load_report_bundle(manifest)


def test_manifest_rejects_payload_that_drifted_from_declared_payload_hash(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    manifest, _, _ = make_bundle(tmp_path, monkeypatch)
    metadata = json.loads(manifest.read_text(encoding="utf-8"))
    payload = Path(metadata["masterPayload"])
    payload.write_text(payload.read_text(encoding="utf-8") + "\n", encoding="utf-8")
    metadata["artifact_manifest"]["master_payload"].update(
        {"sha256": hashlib.sha256(payload.read_bytes()).hexdigest(), "size": payload.stat().st_size}
    )
    metadata["artifact_list_sha256"] = sender._canonical_sha256(metadata["artifact_manifest"])
    manifest.write_text(json.dumps(metadata, ensure_ascii=False), encoding="utf-8")
    with pytest.raises(sender.ReportUnavailableError, match="最新報告 payload SHA-256 不一致"):
        sender.load_report_bundle(manifest)


def test_manifest_rejects_attachment_with_wrong_forecast_date(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    manifest, _, _ = make_bundle(tmp_path, monkeypatch)
    payload = json.loads(manifest.read_text(encoding="utf-8"))
    payload["masterExcel"] = str(Path(payload["masterExcel"]).with_name("台股完整分析報告_20260712.xlsx"))
    Path(payload["masterExcel"]).write_bytes(b"wrong-date")
    manifest.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")
    with pytest.raises(sender.ReportUnavailableError, match="檔名與 manifest 預測日不一致"):
        sender.load_report_bundle(manifest)


@pytest.mark.parametrize(
    "bad_path",
    [
        "台股完整分析報告_*.xlsx",
        "candidate/台股完整分析報告_20260713.xlsx",
        "staging/台股完整分析報告_20260713.xlsx",
    ],
)
def test_manifest_rejects_glob_or_nonformal_attachment_path(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, bad_path: str
) -> None:
    manifest, _, _ = make_bundle(tmp_path, monkeypatch)
    metadata = json.loads(manifest.read_text(encoding="utf-8"))
    metadata["masterExcel"] = str(manifest.parent / bad_path)
    manifest.write_text(json.dumps(metadata, ensure_ascii=False), encoding="utf-8")
    with pytest.raises(sender.ReportUnavailableError, match="glob|candidate|staging"):
        sender.load_report_bundle(manifest)


def test_versioned_no_action_manifest_is_rejected_before_delivery(tmp_path: Path) -> None:
    manifest = tmp_path / "reports" / "latest_report_manifest.json"
    manifest.parent.mkdir(parents=True)
    metadata = {
        "schema": sender.REPORT_MANIFEST_SCHEMA,
        "schema_version": 2,
        "run_id": "fixture-no-action-20260713",
        "status": "no_action",
    }
    with pytest.raises(sender.ReportDeferredError, match="拒絕 no_action/空推薦"):
        sender._validate_versioned_manifest(metadata, manifest)


def test_versioned_empty_next_day_payload_is_rejected(tmp_path: Path) -> None:
    reports = tmp_path / "reports"
    reports.mkdir(parents=True)
    payload = reports / "payload.json"
    payload.write_text(
        json.dumps(
            {
                "metadata": {
                    "dataDate": "20260709",
                    "forecastTargetDate": "20260713",
                },
                "tomorrowTop10": [],
            }
        ),
        encoding="utf-8",
    )
    payload_record = {
        "path": str(payload.resolve()),
        "sha256": hashlib.sha256(payload.read_bytes()).hexdigest(),
        "size": payload.stat().st_size,
    }
    metadata = {
        "schema": sender.REPORT_MANIFEST_SCHEMA,
        "schema_version": 2,
        "dataDate": "20260709",
        "forecastTargetDate": "20260713",
        "masterPayload": str(payload),
        "masterPayloadSha256": hashlib.sha256(payload.read_bytes()).hexdigest(),
        "artifact_manifest": {"master_payload": payload_record},
    }
    with pytest.raises(sender.ReportDeferredError, match="沒有 next-day 正式可見推薦"):
        sender._validate_manifest_payload(metadata, reports)


def test_scheduled_bundle_accepts_fresh_and_rejects_stale_target(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    manifest, _, _ = make_bundle(tmp_path, monkeypatch)
    bundle = sender.wait_for_stable_bundle(
        manifest,
        wait_minutes=0,
        poll_seconds=1,
        stable_seconds=0,
        required_target_date="20260713",
        require_final_manifest=True,
    )
    assert bundle.manifest_path.name == "latest_report_manifest.json"
    assert bundle.metadata["forecastTargetDate"] == "20260713"
    with pytest.raises(sender.ReportUnavailableError, match="等待 20260714"):
        sender.wait_for_stable_bundle(
            manifest,
            wait_minutes=0,
            poll_seconds=1,
            stable_seconds=0,
            required_target_date="20260714",
            require_final_manifest=True,
        )


def test_scheduled_bundle_rejects_noncanonical_final_manifest(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    manifest, _, _ = make_bundle(tmp_path, monkeypatch)
    candidate = manifest.with_name("candidate_report_manifest.json")
    candidate.write_text(manifest.read_text(encoding="utf-8"), encoding="utf-8")
    with pytest.raises(sender.ReportDeferredError, match="尚未就緒") as error:
        sender.wait_for_stable_bundle(
            candidate,
            wait_minutes=0,
            poll_seconds=1,
            stable_seconds=0,
            required_target_date="20260713",
            require_final_manifest=True,
        )
    message = str(error.value)
    assert f"只接受 canonical {manifest}" in message
    assert f"實際為 {candidate.resolve()}" in message


class FakeTelegramClient:
    def __init__(self, fail_once_on: str = "") -> None:
        self.calls: list[tuple[str, str]] = []
        self.fail_once_on = fail_once_on
        self.failed = False

    def send_message(self, text: str) -> dict[str, Any]:
        self.calls.append(("message", text))
        return {"message_id": len(self.calls)}

    def send_document(self, path: Path, caption: str) -> dict[str, Any]:
        if path.name == self.fail_once_on and not self.failed:
            self.failed = True
            raise sender.TelegramApiError("temporary test failure", 500)
        self.calls.append(("document", path.name))
        return {"message_id": len(self.calls)}


def test_delivery_does_not_write_complete_for_no_action_bundle(tmp_path: Path) -> None:
    manifest = tmp_path / "reports" / "latest_report_manifest.json"
    manifest.parent.mkdir(parents=True)
    bundle = sender.ReportBundle(
        manifest_path=manifest,
        metadata={
            "schema": sender.REPORT_MANIFEST_SCHEMA,
            "schema_version": 2,
            "run_id": "fixture-no-action-20260713",
            "status": "no_action",
        },
        attachments=(),
        summary_text="no action",
        fingerprint="empty-formal",
    )
    state_path = tmp_path / "state.json"
    with pytest.raises(sender.ReportDeferredError, match="拒絕 no_action/空推薦"):
        sender.deliver_bundle(bundle, FakeTelegramClient(), state_path)
    assert not state_path.exists()


def test_delivery_deduplicates_completed_bundle(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    manifest, _, _ = make_bundle(tmp_path, monkeypatch)
    bundle = sender.load_report_bundle(manifest)
    state_path = tmp_path / "state.json"
    first_client = FakeTelegramClient()
    delivered, count = sender.deliver_bundle(bundle, first_client, state_path)
    assert delivered is True
    assert count == 3
    assert [kind for kind, _ in first_client.calls] == ["message", "document", "document"]

    second_client = FakeTelegramClient()
    delivered, count = sender.deliver_bundle(bundle, second_client, state_path)
    assert delivered is False
    assert count == 0
    assert second_client.calls == []


def test_delivery_resumes_after_partial_failure(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    manifest, _, pdf = make_bundle(tmp_path, monkeypatch)
    bundle = sender.load_report_bundle(manifest)
    state_path = tmp_path / "state.json"
    failing_client = FakeTelegramClient(fail_once_on=pdf.name)
    with pytest.raises(sender.TelegramApiError):
        sender.deliver_bundle(bundle, failing_client, state_path)
    assert [kind for kind, _ in failing_client.calls] == ["message", "document"]

    retry_client = FakeTelegramClient()
    delivered, count = sender.deliver_bundle(bundle, retry_client, state_path)
    assert delivered is True
    assert count == 1
    assert retry_client.calls == [("document", pdf.name)]
