# RETIRED TEST MODULE -- not collected (tests/conftest.py collect_ignore = ["_retired"]).
# Moved verbatim from tests/test_mix_report_sender.py on 2026-10-12; see tests/_retired/README.md
# for the removed module/feature each test exercised and the evidence that it was intentionally removed.
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


def _receipt_digest(payload: dict[str, Any]) -> str:
    semantic = dict(payload)
    semantic.pop("publication_digest", None)
    return sender._canonical_json_digest(semantic)


def make_fixture(
    tmp_path: Path,
    *,
    current_state: str = "success",
    include_actionable: bool = True,
    evaluation_now: datetime | None = None,
) -> tuple[sender.MixSenderPaths, dict[str, Path]]:
    namespace = tmp_path / "mix"
    reports = namespace / "reports"
    pdf_root = reports / "pdf" / "run-current"
    outputs = namespace / "outputs"
    feedback = namespace / "feedback"
    status_root = namespace / "logs"
    telegram = tmp_path / "telegram"
    for path in (pdf_root, outputs, feedback / "predictions", feedback / "publications", status_root, telegram / "data", telegram / "logs"):
        path.mkdir(parents=True, exist_ok=True)

    asof = "2026-08-21"
    target = "2026-08-24"
    run_id = "run-current-20260824"
    ledger_id = "ledger-current-20260824"
    payload_path = outputs / f"{run_id}_report_payload.json"
    summary_path = pdf_root / "summary.pdf"
    excel_path = outputs / "Daily report" / "2026-08-24_MIX.xlsx"
    excel_path.parent.mkdir(parents=True, exist_ok=True)
    prediction_path = feedback / "predictions" / f"{ledger_id}.json"
    manifest_path = outputs / f"{run_id}_manifest.json"
    summary_path.write_bytes(b"%PDF-current")
    excel_path.write_bytes(b"xlsx-current")
    prediction_path.write_bytes(b"prediction-current")

    payload: dict[str, Any] = {
        "schema_version": sender.MIX_PAYLOAD_SCHEMA,
        "run_id": run_id,
        "status": "final-before-open",
        "run_mode": "production",
        "official_final_before_open": True,
        "sampled_universe": False,
        "as_of_date": asof,
        "target_date": target,
        "feedback": {
            "ledger_run_id": ledger_id,
            "prediction_snapshot": str(prediction_path.resolve()),
            "prediction_snapshot_records": 1,
            "prediction_snapshot_sha256": _sha(prediction_path),
        },
    }
    recommendation_rows = {
        "next_day": [
            {
                "code": "2330",
                "name": "台積電",
                "extra": {
                    "qualification_status": "qualified",
                    "recommendation_status": "qualified",
                    "recommendation_list": "next_day",
                    "raw_rank_including_supplementary": 1,
                    "qualified_rank": 1,
                    "counts_toward_top_n": True,
                },
            }
        ],
        "medium_term": [
            {
                "code": "2330",
                "name": "台積電",
                "extra": {
                    "qualification_status": "qualified",
                    "recommendation_status": "qualified",
                    "recommendation_list": "medium_term",
                    "raw_rank_including_supplementary": 1,
                    "qualified_rank": 1,
                    "counts_toward_top_n": True,
                },
            }
        ],
    }
    horizon_publication = {
        objective: {
            "status": "actionable",
            "actionable": True,
            "requested_count": 10,
            "approved_count": 1,
            "visible_count": 1,
            "qualified_count": 1,
            "supplementary_count": 0,
        }
        for objective in ("next_day", "medium_term")
    }
    payload.update(
        {
            "selection": {
                "next_day": recommendation_rows["next_day"],
                "next_day_observations": [],
                "medium_term": recommendation_rows["medium_term"],
                "medium_term_observations": [],
            },
            "approved_recommendations": recommendation_rows,
            "horizon_publication": horizon_publication,
        }
    )
    if include_actionable:
        payload["actionable"] = True
    payload_path.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )

    mitake_cache = namespace / "cache" / "mitake_watchlist"
    mitake_logs = mitake_cache / "logs"
    mitake_user = tmp_path / "mitake-user"
    mitake_cache.mkdir(parents=True, exist_ok=True)
    mitake_logs.mkdir(parents=True, exist_ok=True)
    mitake_user.mkdir(parents=True, exist_ok=True)
    group_path = mitake_user / "MK_99_2.stk"
    userdef_path = mitake_user / "UserDefSet.set"
    group_path.write_bytes(b"2330\n")
    userdef_path.write_bytes(b"UserDefSet\n")
    target_identity = {
        "UserDefSet.set": _record(userdef_path),
        group_path.name: _record(group_path),
    }
    mitake_log_path = mitake_logs / "mitake-current.json"
    mitake_result_path = mitake_cache / "latest_result.json"
    mitake_result: dict[str, Any] = {
        "schema_version": "mix-mitake-remake-v1",
        "status": "synced",
        "complete": True,
        "push_complete": True,
        "pending": False,
        "group_name": "再製",
        "run_id": run_id,
        "as_of_date": asof,
        "target_date": target,
        "payload_sha256": sender._canonical_mitake_payload_digest(payload),
        "expected_codes": ["2330"],
        "verified_codes": ["2330"],
        "item_count": 1,
        "persistence_verification": "verified_remake_group_only",
        "group_file": str(group_path.resolve()),
        "group_file_sha256": _sha(group_path),
        "userdefset_sha256": _sha(userdef_path),
        "target_identity": target_identity,
        "target_lineage": {
            "run_id": run_id,
            "payload_sha256": sender._canonical_mitake_payload_digest(payload),
            "as_of_date": asof,
            "target_date": target,
            "group_name": "再製",
        },
        "log_path": str(mitake_log_path.resolve()),
    }
    mitake_log_path.write_text(json.dumps(mitake_result, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    mitake_result_path.write_text(json.dumps(mitake_result, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")

    pointer_hashes: dict[str, Any] = {
        "payload": _record(payload_path),
        "summary_pdf": _record(summary_path),
        "individual_pdfs": [],
        "excel": _record(excel_path),
        "prediction": _record(prediction_path),
    }
    manifest: dict[str, Any] = {
        "schema_version": sender.MIX_MANIFEST_SCHEMA,
        "run_id": run_id,
        "status": "final-before-open",
        "smoke": False,
        "as_of_date": asof,
        "target_date": target,
        "actionable": True,
        "horizon_publication": horizon_publication,
        "payload": str(payload_path.resolve()),
        "summary_pdf": str(summary_path.resolve()),
        "individual_pdfs": [],
        "excel": str(excel_path.resolve()),
        "artifact_hashes": pointer_hashes,
        "feedback_prediction": {
            "ledger_run_id": ledger_id,
            "prediction_snapshot": str(prediction_path.resolve()),
            "prediction_snapshot_records": 1,
            "prediction_snapshot_sha256": _sha(prediction_path),
        },
    }
    manifest["artifact_hashes"] = pointer_hashes
    manifest_path.write_text(json.dumps(manifest, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    manifest_sha = _sha(manifest_path)

    pointer: dict[str, Any] = {
        "schema_version": sender.MIX_POINTER_SCHEMA,
        "run_id": run_id,
        "status": "final-before-open",
        "run_mode": "production",
        "official_final_before_open": True,
        "actionable": True,
        "horizon_publication": horizon_publication,
        "as_of_date": asof,
        "target_date": target,
        "payload": str(payload_path.resolve()),
        "summary_pdf": str(summary_path.resolve()),
        "individual_pdfs": [],
        "manifest": str(manifest_path.resolve()),
        "manifest_sha256": manifest_sha,
        "excel": str(excel_path.resolve()),
        "artifact_hashes": pointer_hashes,
    }
    report_pointer = dict(pointer)
    report_pointer["schema_version"] = sender.MIX_REPORT_POINTER_SCHEMA
    pointer_path = reports / "LATEST_PRODUCTION_RUN.json"
    report_pointer_path = reports / "LATEST_REPORT_RUN.json"
    pointer_path.write_text(json.dumps(pointer, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    report_pointer_path.write_text(json.dumps(report_pointer, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")

    receipt: dict[str, Any] = {
        "schema_version": 1,
        "kind": sender.MIX_PUBLICATION_RECEIPT_KIND,
        "run_id": ledger_id,
        "report_run_id": run_id,
        "as_of_date": asof,
        "target_date": target,
        "publication_contract": sender.MIX_PUBLICATION_RECEIPT_CONTRACT,
        "artifacts": {
            "manifest": {"path": f"outputs/{manifest_path.name}", **_record(manifest_path)},
            "payload": {"path": f"outputs/{payload_path.name}", **_record(payload_path)},
            "prediction": {"path": f"data/feedback/predictions/{prediction_path.name}", **_record(prediction_path)},
            "summary_pdf": {"path": f"reports/pdf/run-current/{summary_path.name}", **_record(summary_path)},
            "excel": {"path": f"outputs/Daily report/{excel_path.name}", **_record(excel_path)},
        },
        "publication_digest": "",
        "published_at": "2026-08-23T00:00:00+00:00",
    }
    # Receipt evidence paths are canonical relative references; _record's
    # absolute path field is deliberately replaced for the receipt envelope.
    for name, relative in {
        "manifest": f"outputs/{manifest_path.name}",
        "payload": f"outputs/{payload_path.name}",
        "prediction": f"data/feedback/predictions/{prediction_path.name}",
        "summary_pdf": f"reports/pdf/run-current/{summary_path.name}",
        "excel": f"outputs/Daily report/{excel_path.name}",
    }.items():
        receipt["artifacts"][name]["path"] = relative
    receipt["publication_digest"] = _receipt_digest(receipt)
    receipt_path = feedback / "publications" / f"pub-{run_id}.json"
    receipt_path.write_text(json.dumps(receipt, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")

    registry_path = tmp_path / "requirements_v1.json"
    registry_path.write_text('{"datasets": []}\n', encoding="utf-8")
    registry_sha = sender._canonical_registry_sha256(registry_path)
    central_market_root = tmp_path / "shared_market_data"
    calendar_path = (
        central_market_root
        / "official"
        / "calendar"
        / "snapshots"
        / "official_calendar_20260821_fixture.json"
    )
    calendar_path.parent.mkdir(parents=True, exist_ok=True)
    calendar_payload = {
        "schema": sender.CENTRAL_CALENDAR_SCHEMA,
        "schema_version": 1,
        "dataset": "official_trading_calendar",
        "dataset_id": sender.CENTRAL_CALENDAR_DATASET_ID,
        "status": "complete",
        "expected_asof": asof,
        "observed_asof": asof,
        "coverage": {
            "expected_asof": asof,
            "expected_asof_covered": True,
            "next_session": target,
            "next_session_covered": True,
            "status": "complete",
            "ok": True,
        },
        "rows": [
            {"date": asof, "status": "open"},
            {"date": target, "status": "open"},
        ],
    }
    calendar_path.write_text(
        json.dumps(calendar_payload, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    calendar_sha = _sha(calendar_path)
    calendar_size = calendar_path.stat().st_size
    central_receipt = {
        "schema_version": 1,
        "manifest_schema": "shared-market-data-update-manifest-v1",
        "status": "complete",
        "update_status": "complete",
        "expected_asof": asof,
        "run_id": "central-current",
        "orchestration_run_id": "central-current",
        "publishable": True,
        "partial": False,
        "fail_closed": False,
        "registry_sha256": registry_sha,
        "coverage": {"all_verified": True},
        "checkpoint": {"status": "complete"},
        "datasets": [
            {
                "receipt_schema": sender.CENTRAL_CALENDAR_RECEIPT_SCHEMA,
                "dataset": "official_trading_calendar",
                "dataset_id": sender.CENTRAL_CALENDAR_DATASET_ID,
                "status": "complete",
                "expected_asof": asof,
                "schema_policy": sender.CENTRAL_CALENDAR_SCHEMA,
                "schema": {
                    "name": sender.CENTRAL_CALENDAR_SCHEMA,
                    "version": 1,
                    "status": "verified",
                    "ok": True,
                },
                "canonical_path": str(calendar_path.resolve()),
                "coverage": {
                    "expected_asof": asof,
                    "expected_asof_covered": True,
                    "next_session": target,
                    "next_session_covered": True,
                    "status": "complete",
                    "ok": True,
                },
                "publish": {
                    "status": "committed",
                    "artifacts": [
                        {
                            "target_relative_path": str(
                                calendar_path.relative_to(central_market_root)
                            ).replace("\\", "/"),
                            "size": calendar_size,
                            "content_sha256": calendar_sha,
                        }
                    ],
                },
            }
        ],
        "publication_repackage": {
            "schema": "shared-market-data-publication-repackage-v1",
            "status": "complete",
            "expected_asof": asof,
            "blockers": [],
            "registry_sha256": registry_sha,
        },
    }
    central_path = tmp_path / "central" / "latest_manifest.json"
    central_path.parent.mkdir(parents=True, exist_ok=True)
    central_path.write_text(json.dumps(central_receipt, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    status_common = {
        "schema_version": "mix_daily_status_v1",
        "state": current_state,
        "exit_code": 0,
        "stage": "complete",
        "central_expected_asof": asof,
        "central_target": target,
        "central_manifest_path": str(central_path.resolve()),
        "central_manifest_sha256": _sha(central_path),
        "central_registry_sha256": registry_sha,
        "artifact_manifest": str(manifest_path.resolve()),
        "published_report_official": True,
    }
    (status_root / "current_run_status.json").write_text(json.dumps(status_common), encoding="utf-8")
    (status_root / "last_run_status.json").write_text(json.dumps(status_common), encoding="utf-8")

    paths = sender.MixSenderPaths(
        namespace_root=namespace,
        reports_root=reports,
        outputs_root=outputs,
        status_root=status_root,
        feedback_root=feedback,
        pointer_path=pointer_path,
        report_pointer_path=report_pointer_path,
        central_receipt_path=central_path,
        central_registry_path=registry_path,
        central_market_root=central_market_root,
        mitake_result_path=mitake_result_path,
        telegram_root=telegram,
        state_path=telegram / "data" / "mix_report_delivery_state.json",
        lock_path=telegram / "data" / "mix_report_delivery.lock",
        log_dir=telegram / "logs",
        evaluation_now=evaluation_now
        or datetime.fromisoformat(f"{target}T08:00:00+08:00"),
    )
    return paths, {
        "pointer": pointer_path,
        "manifest": manifest_path,
        "receipt": receipt_path,
        "central": central_path,
        "mitake": mitake_result_path,
        "payload": payload_path,
        "calendar": calendar_path,
    }


def test_running_status_does_not_fallback_to_old_pointer(tmp_path: Path) -> None:
    paths, _ = make_fixture(tmp_path, current_state="running")
    with pytest.raises(sender.MixReportDeferredError, match="尚未完成"):
        sender.load_mix_bundle(paths)


def test_current_and_last_central_status_dates_must_agree(tmp_path: Path) -> None:
    paths, _ = make_fixture(tmp_path)
    current_path = paths.status_root / "current_run_status.json"
    current = json.loads(current_path.read_text(encoding="utf-8"))
    current["central_expected_asof"] = "2026-08-20"
    current_path.write_text(json.dumps(current), encoding="utf-8")
    with pytest.raises(sender.MixReportDeferredError, match="central_expected_asof"):
        sender.load_mix_bundle(paths)


def test_current_formal_bundle_requires_all_cross_bindings(tmp_path: Path) -> None:
    paths, _ = make_fixture(tmp_path)
    bundle = sender.load_mix_bundle(paths)
    assert bundle.run_id == "run-current-20260824"
    assert bundle.expected_asof == "2026-08-21"
    assert bundle.target == "2026-08-24"
    assert bundle.official is True
    assert bundle.actionable is True
    assert len(bundle.attachments) == 2
    assert bundle.publication_receipt_path.name == "pub-run-current-20260824.json"
    assert bundle.mitake_receipt_path.name == "latest_result.json"
    assert len(bundle.mitake_receipt_sha256) == 64
    assert bundle.mitake_sync_status == "synced"
    assert bundle.mitake_sync_reason == ""
    assert bundle.central["calendar"]["next_session"] == "2026-08-24"


def test_official_calendar_binds_weekend_gap_without_plus_one(tmp_path: Path) -> None:
    paths, _ = make_fixture(tmp_path)
    bundle = sender.load_mix_bundle(paths)
    assert bundle.expected_asof == "2026-08-21"  # Friday
    assert bundle.target == "2026-08-24"  # Monday, not Saturday


def test_expired_prediction_session_is_rejected(tmp_path: Path) -> None:
    paths, _ = make_fixture(
        tmp_path,
        evaluation_now=datetime.fromisoformat("2026-08-25T08:00:00+08:00"),
    )
    with pytest.raises(sender.MixReportDeferredError, match="已過期"):
        sender.load_mix_bundle(paths)


@pytest.mark.parametrize(
    ("document_name", "marker"),
    [
        ("pointer", "candidate"),
        ("manifest", "blocked"),
        ("payload", "candidate"),
        ("receipt", "blocked"),
    ],
)
def test_explicit_nonformal_markers_fail_closed(
    tmp_path: Path, document_name: str, marker: str
) -> None:
    paths, files = make_fixture(tmp_path)
    target_path = files[document_name]
    document = json.loads(target_path.read_text(encoding="utf-8"))
    document[marker] = True
    target_path.write_text(json.dumps(document), encoding="utf-8")
    with pytest.raises(sender.MixReportIntegrityError, match=marker):
        sender.load_mix_bundle(paths)


def test_nonformal_publication_mode_fails_closed(tmp_path: Path) -> None:
    paths, files = make_fixture(tmp_path)
    manifest = json.loads(files["manifest"].read_text(encoding="utf-8"))
    manifest["publication_mode"] = "candidate"
    files["manifest"].write_text(json.dumps(manifest), encoding="utf-8")
    with pytest.raises(sender.MixReportIntegrityError, match="publication_mode"):
        sender.load_mix_bundle(paths)


def test_optional_prediction_date_must_match_calendar_target(tmp_path: Path) -> None:
    paths, files = make_fixture(tmp_path)
    payload = json.loads(files["payload"].read_text(encoding="utf-8"))
    payload["prediction_date"] = "2026-08-25"
    files["payload"].write_text(json.dumps(payload), encoding="utf-8")
    with pytest.raises(sender.MixReportIntegrityError, match="prediction_date"):
        sender.load_mix_bundle(paths)


def test_calendar_receipt_target_tamper_fails_closed(tmp_path: Path) -> None:
    paths, files = make_fixture(tmp_path)
    central = json.loads(files["central"].read_text(encoding="utf-8"))
    central["datasets"][0]["coverage"]["next_session"] = "2026-08-25"
    files["central"].write_text(json.dumps(central), encoding="utf-8")
    central_sha = _sha(files["central"])
    for name in ("current_run_status.json", "last_run_status.json"):
        status_path = paths.status_root / name
        status = json.loads(status_path.read_text(encoding="utf-8"))
        status["central_manifest_sha256"] = central_sha
        status_path.write_text(json.dumps(status), encoding="utf-8")
    with pytest.raises(sender.MixReportIntegrityError, match="calendar artifact coverage"):
        sender.load_mix_bundle(paths)


def test_calendar_artifact_hash_tamper_fails_closed(tmp_path: Path) -> None:
    paths, files = make_fixture(tmp_path)
    files["calendar"].write_text("{}\n", encoding="utf-8")
    with pytest.raises(sender.MixReportIntegrityError, match="artifact (size|SHA-256)"):
        sender.load_mix_bundle(paths)


def test_tampered_pdf_fails_closed(tmp_path: Path) -> None:
    paths, files = make_fixture(tmp_path)
    files["receipt"].parent  # keep fixture path explicit for readability
    bundle_before = sender.load_mix_bundle(paths)
    assert bundle_before.fingerprint
    (paths.reports_root / "pdf" / "run-current" / "summary.pdf").write_bytes(b"tampered")
    with pytest.raises(sender.MixReportIntegrityError, match="artifact (size|SHA-256)"):
        sender.load_mix_bundle(paths)


def test_explicit_no_action_is_rejected_as_formal_recommendation(tmp_path: Path) -> None:
    paths, files = make_fixture(tmp_path)
    pointer = json.loads(files["pointer"].read_text(encoding="utf-8"))
    pointer["no_action"] = True
    files["pointer"].write_text(json.dumps(pointer), encoding="utf-8")
    with pytest.raises(sender.MixReportIntegrityError, match="no_action"):
        sender.load_mix_bundle(paths)


def test_missing_actionable_is_derived_after_formal_contract_verification(
    tmp_path: Path,
) -> None:
    paths, _ = make_fixture(tmp_path, include_actionable=False)
    bundle = sender.load_mix_bundle(paths)
    assert bundle.actionable is True


def test_empty_formal_recommendation_is_rejected(tmp_path: Path) -> None:
    paths, files = make_fixture(tmp_path)
    payload = json.loads(files["payload"].read_text(encoding="utf-8"))
    payload["approved_recommendations"]["next_day"] = []
    payload["selection"]["next_day"] = []
    payload["selection"]["next_day_observations"] = []
    files["payload"].write_text(json.dumps(payload), encoding="utf-8")
    with pytest.raises(sender.MixReportIntegrityError, match="沒有任何可見個股推薦"):
        sender.load_mix_bundle(paths)


def test_central_quality_findings_are_unmet_not_blocking(tmp_path: Path) -> None:
    paths, files = make_fixture(tmp_path)
    central = json.loads(files["central"].read_text(encoding="utf-8"))
    central["publishable"] = False
    central["partial"] = True
    central["coverage"]["all_verified"] = False
    central["checkpoint"]["status"] = "partial"
    central["publication_repackage"]["blockers"] = ["coverage_gap"]
    files["central"].write_text(json.dumps(central), encoding="utf-8")
    central_sha = _sha(files["central"])
    for name in ("current_run_status.json", "last_run_status.json"):
        status_path = paths.status_root / name
        status = json.loads(status_path.read_text(encoding="utf-8"))
        status["central_manifest_sha256"] = central_sha
        status_path.write_text(json.dumps(status), encoding="utf-8")

    bundle = sender.load_mix_bundle(paths)
    assert any("central.publishable: false" in item for item in bundle.unmet_conditions)
    assert any("central.coverage.all_verified: false" in item for item in bundle.unmet_conditions)
    assert any("central.publication_repackage.blockers" in item for item in bundle.unmet_conditions)


def test_delivery_state_is_idempotent_and_lineage_bound(tmp_path: Path) -> None:
    paths, _ = make_fixture(tmp_path)
    bundle = sender.load_mix_bundle(paths)

    class FakeTelegram:
        def __init__(self) -> None:
            self.messages = 0
            self.documents: list[Path] = []

        def send_message(self, text: str) -> dict[str, Any]:
            assert "MIX 盤前正式報告" in text
            self.messages += 1
            return {"message_id": self.messages}

        def send_document(self, path: Path, caption: str) -> dict[str, Any]:
            self.documents.append(path)
            return {"message_id": 100 + len(self.documents)}

    client = FakeTelegram()
    assert sender.deliver_mix_bundle(bundle, client, paths.state_path) == (True, 3)
    assert sender.deliver_mix_bundle(bundle, client, paths.state_path) == (False, 0)
    state = json.loads(paths.state_path.read_text(encoding="utf-8"))
    entry = state["deliveries"][bundle.fingerprint]
    assert entry["status"] == "complete"
    assert entry["run_id"] == bundle.run_id
    assert entry["central_receipt_sha256"] == bundle.central["receipt_sha256"]
    assert entry["mitake_receipt_sha256"] == bundle.mitake_receipt_sha256
    assert client.messages == 1
    assert len(client.documents) == 2


@pytest.mark.parametrize("missing", ["summary", "document"])
def test_missing_telegram_message_id_stays_retryable(
    tmp_path: Path, missing: str
) -> None:
    paths, _ = make_fixture(tmp_path)
    bundle = sender.load_mix_bundle(paths)

    class MissingMessageIdTelegram:
        def send_message(self, text: str) -> dict[str, Any]:
            return {} if missing == "summary" else {"message_id": 1}

        def send_document(self, path: Path, caption: str) -> dict[str, Any]:
            return {} if missing == "document" else {"message_id": 2}

    with pytest.raises(sender.MixReportIntegrityError, match="message_id"):
        sender.deliver_mix_bundle(bundle, MissingMessageIdTelegram(), paths.state_path)
    state = json.loads(paths.state_path.read_text(encoding="utf-8"))
    entry = state["deliveries"][bundle.fingerprint]
    assert entry["status"] == "in_progress"
    assert "last_complete_fingerprint" not in state


def test_corrupt_complete_delivery_is_reopened_for_retry(tmp_path: Path) -> None:
    paths, _ = make_fixture(tmp_path)
    bundle = sender.load_mix_bundle(paths)
    paths.state_path.write_text(
        json.dumps(
            {
                "schema_version": "mix_report_delivery_state_v1",
                "last_complete_fingerprint": bundle.fingerprint,
                "deliveries": {
                    bundle.fingerprint: {
                        "status": "complete",
                        "messages": [{"key": "summary:old", "message_id": None}],
                    }
                },
            }
        ),
        encoding="utf-8",
    )

    class UnusedTelegram:
        def send_message(self, text: str) -> dict[str, Any]:
            raise AssertionError("corrupt complete state must fail before API call")

        def send_document(self, path: Path, caption: str) -> dict[str, Any]:
            raise AssertionError("corrupt complete state must fail before API call")

    with pytest.raises(sender.MixReportIntegrityError, match="message_id"):
        sender.deliver_mix_bundle(bundle, UnusedTelegram(), paths.state_path)
    state = json.loads(paths.state_path.read_text(encoding="utf-8"))
    assert state["deliveries"][bundle.fingerprint]["status"] == "in_progress"


def test_registry_content_hash_drift_is_rejected(tmp_path: Path) -> None:
    paths, _ = make_fixture(tmp_path)
    paths.central_registry_path.write_text('{"datasets": ["changed"]}\n', encoding="utf-8")
    with pytest.raises(sender.MixReportIntegrityError, match="registry"):
        sender.load_mix_bundle(paths)


def test_pending_mitake_receipt_is_separate_from_formal_report_delivery(tmp_path: Path) -> None:
    paths, files = make_fixture(tmp_path)
    result = json.loads(files["mitake"].read_text(encoding="utf-8"))
    result.update({"status": "pending", "complete": False, "push_complete": False, "pending": True})
    files["mitake"].write_text(json.dumps(result), encoding="utf-8")
    bundle = sender.load_mix_bundle(paths)
    assert bundle.mitake_sync_status == "pending"
    assert "Mitake receipt 尚未完成" in bundle.mitake_sync_reason

    class RecordingTelegram:
        def __init__(self) -> None:
            self.calls: list[str] = []

        def send_message(self, text: str) -> dict[str, Any]:
            self.calls.append("message")
            return {"message_id": len(self.calls)}

        def send_document(self, path: Path, caption: str) -> dict[str, Any]:
            del path, caption
            self.calls.append("document")
            return {"message_id": len(self.calls)}

    client = RecordingTelegram()
    delivered, count = sender.deliver_mix_bundle(bundle, client, paths.state_path)
    assert delivered is True
    assert count == 3
    assert client.calls == ["message", "document", "document"]
    state = json.loads(paths.state_path.read_text(encoding="utf-8"))
    entry = state["deliveries"][bundle.fingerprint]
    assert entry["report_delivery_status"] == "complete"
    assert entry["status"] == "report_complete_mitake_pending"
    assert entry["mitake_sync_status"] == "pending"
    assert "last_complete_fingerprint" not in state
    assert state["last_report_complete_fingerprint"] == bundle.fingerprint


def test_mitake_lineage_or_persistence_tamper_fails_closed(tmp_path: Path) -> None:
    paths, files = make_fixture(tmp_path)
    result = json.loads(files["mitake"].read_text(encoding="utf-8"))
    result["verified_codes"] = ["9999"]
    files["mitake"].write_text(json.dumps(result), encoding="utf-8")
    with pytest.raises(sender.MixReportIntegrityError, match="expected_codes/verified_codes"):
        sender.load_mix_bundle(paths)


def test_run_local_evidence_session_reuses_unchanged_inputs(tmp_path: Path) -> None:
    paths, _ = make_fixture(tmp_path)
    first = sender.load_mix_bundle(paths)
    first_stats = paths.evidence_session.stats()
    second = sender.load_mix_bundle(paths)
    second_stats = paths.evidence_session.stats()
    third = sender.load_mix_bundle(paths)
    third_stats = paths.evidence_session.stats()

    assert second.fingerprint == first.fingerprint
    assert third.fingerprint == first.fingerprint
    assert first_stats["json_entries"] > 0
    assert first_stats["sha_entries"] > 0
    # The second call may bind the two status files to the formal lineage
    # scope discovered after the first status read; after that, the session is
    # stable and must not add more parses/hashes.
    assert third_stats == second_stats


def test_run_local_evidence_session_invalidates_on_registry_identity_change(tmp_path: Path) -> None:
    paths, _ = make_fixture(tmp_path)
    sender.load_mix_bundle(paths)
    before = paths.evidence_session.stats()
    paths.central_registry_path.write_text('{"datasets": ["changed"]}\n', encoding="utf-8")

    with pytest.raises(sender.MixReportIntegrityError, match="registry"):
        sender.load_mix_bundle(paths)

    after = paths.evidence_session.stats()
    assert (
        after["sha_loads"] > before["sha_loads"]
        or after["canonical_loads"] > before["canonical_loads"]
    )
