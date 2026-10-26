"""Isolated reference/storage tests; no live data, PDF rendering or background work."""
from __future__ import annotations

import copy
import hashlib
import json
import os
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent))
from test_stock_candidate_report import content_converter, descriptor_payload, payload
import stock_candidate_report as report


@pytest.fixture
def published_case(descriptor_payload, tmp_path, monkeypatch):
    item, model_path = descriptor_payload
    item = copy.deepcopy(item)
    root = tmp_path / "central"
    (root / "_manifests").mkdir(parents=True)
    raw = report._json_bytes({"schema": "stock-data-publication-v2", "datasets": [], "revision": 1})
    head = hashlib.sha256(raw).hexdigest()
    path = root / "_manifests" / (head + ".json")
    path.write_bytes(raw)
    fingerprint = hashlib.sha256(json.dumps([("shared_market_data", head, None)], ensure_ascii=True).encode()).hexdigest()
    identity = {"manifest_path": str(path), "manifest_sha256": head,
                "publication_schema": "stock-data-publication-v2"}
    source = {"source_id": "shared_market_data", "kind": "manifest_json", "root": str(root),
              "snapshot_id": head, **identity}
    descriptor = {"schema": "stock-data-published-inputs-v1", "snapshot_fingerprint": fingerprint,
        "sources": [source], "artifacts": [{"route_id": "official_calendar", "sha256": "a" * 64}],
        "diagnostics": ["既有證據與缺口保留"], "published_at": "2026-10-23T00:00:00+00:00",
        "price_actual_max_date": "2026-10-22", "runtime_route_errors": [], "payload_validation": "artifacts_only",
        "pinned_sources": [{"source_id": "shared_market_data", "kind": "manifest_json", "root": str(root),
            "snapshot_id": head, "identity": identity, "entries": {"catalog": "完整中央證據不可另存" * 4096},
            "errors": {}, "index": 0}]}
    descriptor["descriptor_sha256"] = report._digest(descriptor)
    item.update(schema=report.FORMAL_SCHEMA, research_only=False, promotion=True,
                storage_policy=report._READ_ONLY_STORAGE_POLICY)
    item["deployment_binding"] = {"schema": "single-stock-selected-deployment-v1", "consumer_id": "single",
        "activation_id": "fixture-selected", "model_sha256": item["model_binding"]["sha256"]}
    item["snapshot_binding"].update(head_sha256=head, snapshot_fingerprint=fingerprint, descriptor=descriptor)
    item["runtime_payload_sha256"] = report._digest(item)
    case = SimpleNamespace(payload=item, model_path=model_path, root=root, path=path, head=head,
        descriptor=copy.deepcopy(descriptor), loads=[], closes=[], contexts=[], fail_snapshot=False,
        fail_close=False, policy={"consumer_id": "single", "policy_sha256": "f" * 64})

    class Sources:
        def snapshot_descriptor(self):
            if case.fail_snapshot:
                raise ValueError("fixture reconstruction failure")
            return copy.deepcopy(case.descriptor)

        def close(self):
            case.closes.append(self)
            if case.fail_close:
                raise ValueError("fixture close failure")

    def load_selected_context(pin, binding):
        assert pin == head and binding == item["deployment_binding"]
        case.loads.append((pin, copy.deepcopy(binding)))
        context = {"sources": Sources(), "current_identity": {"records": [item["stock"]]},
                   "calendar_evidence": {"fixture": "full context, not descriptor only"}}
        case.contexts.append(context)
        return context

    def policy_binding(*, consumer_id):
        assert consumer_id == "single"
        return copy.deepcopy(case.policy)

    monkeypatch.setattr(report, "_PUBLISHED_ROOT", root)
    monkeypatch.setitem(sys.modules, "stock_daily_selected_deployment", SimpleNamespace(
        runtime=SimpleNamespace(PUBLISHED_ROOT=root), load_selected_context=load_selected_context,
        contract=SimpleNamespace(c0_policy_binding=policy_binding)))
    return case


@pytest.mark.parametrize("horizon", [1, 63])
def test_new_head_reference_restores_exact_payload_without_descriptor_copy(
        published_case, tmp_path, monkeypatch, content_converter, horizon):
    case = published_case
    item = copy.deepcopy(case.payload)
    item["horizon_sessions"] = horizon
    original = copy.deepcopy(item)
    model_bytes = case.model_path.read_bytes()
    output = tmp_path / "formal_reports"
    monkeypatch.setattr(report, "_save_descriptor_evidence", lambda *a: pytest.fail("No descriptor evidence copy"))
    with report._parsed_evidence_operation():
        stored, original_digest, reference = report._prepare_report_payload(item, output)
        restored = report._restore_descriptor(stored, report._resolve_descriptor_reference(reference, stored))
    assert reference["kind"] == "published_snapshot_descriptor"
    assert reference["path"] == str(case.path) and reference["sha256"] == case.head
    assert reference["descriptor_sha256"] == report._digest(original["snapshot_binding"]["descriptor"])
    assert restored == original and original_digest == report._digest(original)
    assert report.build_report_sections(stored) == report.build_report_sections(original)
    assert stored["runtime_payload_sha256"] == original["runtime_payload_sha256"]
    assert "descriptor" not in stored["snapshot_binding"]
    assert "完整中央證據不可另存".encode() not in report._json_bytes(stored)
    assert len(report._json_bytes(reference)) < 2048 and not output.exists()
    assert item == original and case.model_path.read_bytes() == model_bytes
    assert len(case.loads) == len(case.closes) == 1


def test_new_policy_same_model_descriptor_keeps_original_model_reference(descriptor_payload, tmp_path, monkeypatch):
    item, _ = descriptor_payload
    item.update(schema=report.FORMAL_SCHEMA, storage_policy=report._READ_ONLY_STORAGE_POLICY)
    monkeypatch.setattr(report, "_make_published_descriptor_reference", lambda *a: pytest.fail("Use original model reference"))
    stored, original_digest, reference = report._prepare_report_payload(item, tmp_path / "reports")
    assert reference["kind"] == "model_json"
    assert report._restore_descriptor(stored, report._resolve_descriptor_reference(reference, stored)) == item
    assert original_digest == report._digest(item)
    assert not (tmp_path / "reports").exists()


@pytest.mark.parametrize("formal", [False, True])
def test_legacy_reference_behavior_is_unchanged_without_exact_formal_opt_in(
        descriptor_payload, tmp_path, monkeypatch, formal):
    item, _ = descriptor_payload
    item["snapshot_binding"]["descriptor"] = {"legacy_evidence": ["完整證據不省略"]}
    if formal:
        item["schema"] = report.FORMAL_SCHEMA
    else:
        item["storage_policy"] = report._READ_ONLY_STORAGE_POLICY
    monkeypatch.setattr(report, "_make_published_descriptor_reference", lambda *a: pytest.fail("No implicit opt-in"))
    stored, _, reference = report._prepare_report_payload(item, tmp_path / "reports")
    assert reference["kind"] == "report_evidence_json"
    assert report._restore_descriptor(stored, report._resolve_descriptor_reference(reference, stored)) == item


@pytest.mark.parametrize("field,value", [
    ("head_sha256", "0" * 64), ("sha256", "0" * 64), ("descriptor_sha256", "0" * 64),
    ("snapshot_fingerprint", "0" * 64), ("deployment_binding_sha256", "0" * 64),
    ("container_schema", "unknown"), ("reconstruction", "unknown"), ("size", True),
    ("root", "outside"), ("path", "outside.json"), ("extra", "untrusted"),
])
def test_published_reference_rejects_wrong_pin_path_schema_and_digest(
        published_case, tmp_path, monkeypatch, field, value):
    stored, _, reference = report._prepare_report_payload(published_case.payload, tmp_path / "reports")
    reference[field] = value
    real_read = report._stable_json
    reads = []

    def read(path, **kwargs):
        reads.append(Path(path))
        assert Path(path) in {published_case.path, published_case.model_path}
        return real_read(path, **kwargs)

    monkeypatch.setattr(report, "_stable_json", read)
    with pytest.raises(report.CandidateReportError) as caught:
        report._resolve_descriptor_reference(reference, stored)
    assert caught.value.code.startswith("evidence_")
    assert len(published_case.loads) == len(published_case.closes)


@pytest.mark.parametrize("failure", ["missing", "changed_same_size_mtime", "size", "model_sha"])
def test_published_reference_rechecks_full_head_and_model_bytes(published_case, tmp_path, failure):
    case = published_case
    with report._parsed_evidence_operation():
        stored, _, reference = report._prepare_report_payload(case.payload, tmp_path / "reports")
        if failure == "missing":
            case.path.unlink()
        elif failure == "changed_same_size_mtime":
            stamp = case.path.stat()
            case.path.write_bytes(case.path.read_bytes().replace(b'"revision":1', b'"revision":2'))
            os.utime(case.path, ns=(stamp.st_atime_ns, stamp.st_mtime_ns))
        elif failure == "size":
            reference["size"] += 1
        else:
            case.model_path.write_bytes(case.model_path.read_bytes() + b"\n")
        with pytest.raises(report.CandidateReportError) as caught:
            report._resolve_descriptor_reference(reference, stored)
    assert caught.value.code in {"evidence_read_failed", "evidence_sha256_mismatch", "evidence_size_mismatch"}
    assert len(case.loads) == len(case.closes) == 1


@pytest.mark.parametrize("failure", ["snapshot_error", "different_descriptor", "internal_digest", "foreign_root"])
def test_reconstruction_must_be_exact_and_closes_sources_without_large_fallback(
        published_case, tmp_path, monkeypatch, failure):
    case = published_case
    if failure == "snapshot_error":
        case.fail_snapshot = True
    elif failure == "different_descriptor":
        case.descriptor["diagnostics"].append("不能捏造相同內容")
        case.descriptor["descriptor_sha256"] = report._digest(
            {k: v for k, v in case.descriptor.items() if k != "descriptor_sha256"})
    elif failure == "internal_digest":
        case.descriptor["descriptor_sha256"] = "0" * 64
    else:
        case.descriptor["sources"][0]["root"] = str(tmp_path / "other")
    output = tmp_path / "reports"
    monkeypatch.setattr(report, "_save_descriptor_evidence", lambda *a: pytest.fail("No large fallback copy"))
    with pytest.raises(report.CandidateReportError) as caught:
        report._prepare_report_payload(case.payload, output)
    assert caught.value.code in {"evidence_reconstruction_failed", "evidence_descriptor_mismatch"}
    assert len(case.loads) == len(case.closes) == 1 and not output.exists()


def test_head_changed_during_reconstruction_is_rejected_and_sources_closed(published_case, tmp_path, monkeypatch):
    case = published_case
    deployment = sys.modules["stock_daily_selected_deployment"]
    load = deployment.load_selected_context

    def changed(*args):
        context = load(*args)
        case.path.write_bytes(case.path.read_bytes() + b"\n")
        return context

    monkeypatch.setattr(deployment, "load_selected_context", changed)
    with pytest.raises(report.CandidateReportError) as caught:
        report._prepare_report_payload(case.payload, tmp_path / "reports")
    assert caught.value.code == "evidence_sha256_mismatch"
    assert len(case.loads) == len(case.closes) == 1


def test_published_operation_reuses_context_but_rehashes_head_every_resolve(published_case, tmp_path, monkeypatch):
    case = published_case
    real_read = report._stable_json
    reads = []

    def read(path, **kwargs):
        value = real_read(path, **kwargs)
        if Path(path) == case.path:
            reads.append(value[1])
        return value

    monkeypatch.setattr(report, "_stable_json", read)
    with report._parsed_evidence_operation():
        stored, _, reference = report._prepare_report_payload(case.payload, tmp_path / "reports")
        report._resolve_descriptor_reference(reference, stored)
        report._resolve_descriptor_reference(reference, stored)
    assert len(case.loads) == len(case.closes) == 1 and len(reads) == 4
    assert all(info["sha256"] == case.head and info["size"] == case.path.stat().st_size for info in reads)
    assert report._PARSED_EVIDENCE.get() is None


def test_borrow_miss_never_constructs_context_and_no_operation_keeps_immediate_close(published_case, tmp_path):
    case = published_case
    binding = case.payload["deployment_binding"]
    assert report._borrow_selected_context(case.head, binding) is None
    with report._parsed_evidence_operation():
        assert report._borrow_selected_context(case.head, binding) is None
        assert case.loads == case.closes == []
    report._prepare_report_payload(case.payload, tmp_path / "reports")
    assert len(case.loads) == len(case.closes) == 1
    assert report._borrow_selected_context(case.head, binding) is None
    assert len(case.loads) == 1


def test_borrow_returns_full_context_with_lease_until_operation_end(published_case, tmp_path, monkeypatch):
    case = published_case
    binding = case.payload["deployment_binding"]
    real_read = report._stable_json
    head_reads = []

    def read(path, **kwargs):
        value = real_read(path, **kwargs)
        if Path(path) == case.path:
            head_reads.append(value[1])
        return value

    monkeypatch.setattr(report, "_stable_json", read)
    with report._parsed_evidence_operation():
        stored, _, reference = report._prepare_report_payload(case.payload, tmp_path / "reports")
        context = report._borrow_selected_context(case.head, binding)
        assert context is case.contexts[0]
        assert context["current_identity"]["records"] == [case.payload["stock"]]
        assert report._borrow_selected_context(case.head, binding) is context
        assert len(case.loads) == 1 and case.closes == []
        assert len(head_reads) == 4  # Two reconstruction reads, plus one full read per borrow.
        case.descriptor["diagnostics"].append("later source observations must not change the original descriptor")
        restored = report._resolve_descriptor_reference(reference, stored)
        assert restored == case.payload["snapshot_binding"]["descriptor"]
        assert case.closes == []
    assert case.closes == [context["sources"]]
    assert report._PARSED_EVIDENCE.get() is report._SELECTED_CONTEXTS.get() is None
    assert report._borrow_selected_context(case.head, binding) is None


@pytest.mark.parametrize("change", ["head", "binding", "policy", "recipe", "root"])
def test_borrow_key_mismatch_never_reuses_or_constructs_context(published_case, tmp_path, monkeypatch, change):
    case = published_case
    binding, head = copy.deepcopy(case.payload["deployment_binding"]), case.head
    with report._parsed_evidence_operation():
        report._prepare_report_payload(case.payload, tmp_path / "reports")
        if change == "head":
            head = "0" * 64
        elif change == "binding":
            binding["approved_at"] = "different complete activation binding"
        elif change == "policy":
            case.policy["policy_sha256"] = "0" * 64
        elif change == "recipe":
            monkeypatch.setattr(report, "_PUBLISHED_RECONSTRUCTION", "different-recipe")
        else:
            root = tmp_path / "other-central"
            monkeypatch.setattr(report, "_PUBLISHED_ROOT", root)
            monkeypatch.setattr(sys.modules["stock_daily_selected_deployment"].runtime, "PUBLISHED_ROOT", root)
        assert report._borrow_selected_context(head, binding) is None
        assert len(case.loads) == 1 and case.closes == []
    assert len(case.closes) == 1


def test_borrow_rehash_rejects_same_size_mtime_head_tamper_and_closes_once(published_case, tmp_path):
    case = published_case
    with pytest.raises(report.CandidateReportError) as caught:
        with report._parsed_evidence_operation():
            report._prepare_report_payload(case.payload, tmp_path / "reports")
            stamp = case.path.stat()
            case.path.write_bytes(case.path.read_bytes().replace(b'"revision":1', b'"revision":2'))
            os.utime(case.path, ns=(stamp.st_atime_ns, stamp.st_mtime_ns))
            report._borrow_selected_context(case.head, case.payload["deployment_binding"])
    assert caught.value.code == "evidence_sha256_mismatch"
    assert len(case.loads) == len(case.closes) == 1
    assert report._PARSED_EVIDENCE.get() is report._SELECTED_CONTEXTS.get() is None


def test_borrowed_context_isolated_between_nested_and_independent_operations(published_case, tmp_path):
    case = published_case
    binding = case.payload["deployment_binding"]
    with report._parsed_evidence_operation():
        report._prepare_report_payload(case.payload, tmp_path / "reports")
        outer = report._borrow_selected_context(case.head, binding)
        with report._parsed_evidence_operation(reuse=True):
            assert report._borrow_selected_context(case.head, binding) is outer
        with report._parsed_evidence_operation():
            assert report._borrow_selected_context(case.head, binding) is None
            report._prepare_report_payload(case.payload, tmp_path / "reports")
            inner = report._borrow_selected_context(case.head, binding)
            assert inner is not outer and case.closes == []
        assert case.closes == [inner["sources"]]
        assert report._borrow_selected_context(case.head, binding) is outer
    assert case.closes == [inner["sources"], outer["sources"]]
    with report._parsed_evidence_operation():
        assert report._borrow_selected_context(case.head, binding) is None
    assert len(case.loads) == 2
    assert report._PARSED_EVIDENCE.get() is report._SELECTED_CONTEXTS.get() is None


@pytest.mark.parametrize("close_error", [False, True])
def test_borrowed_context_exception_closes_once_and_resets_both_contextvars(published_case, tmp_path, close_error):
    case = published_case
    case.fail_close = close_error
    with pytest.raises(ValueError, match="fixture close failure" if close_error else "fixture inference failure"):
        with report._parsed_evidence_operation():
            report._prepare_report_payload(case.payload, tmp_path / "reports")
            assert report._borrow_selected_context(case.head, case.payload["deployment_binding"]) is case.contexts[0]
            if not close_error:
                raise ValueError("fixture inference failure")
    assert len(case.loads) == len(case.closes) == 1
    assert report._PARSED_EVIDENCE.get() is report._SELECTED_CONTEXTS.get() is None


def test_policy_change_during_reconstruction_never_registers_context(published_case, tmp_path, monkeypatch):
    case = published_case
    deployment = sys.modules["stock_daily_selected_deployment"]
    load = deployment.load_selected_context

    def changed(*args):
        context = load(*args)
        case.policy["policy_sha256"] = "0" * 64
        return context

    monkeypatch.setattr(deployment, "load_selected_context", changed)
    with pytest.raises(report.CandidateReportError) as caught:
        with report._parsed_evidence_operation():
            report._prepare_report_payload(case.payload, tmp_path / "reports")
    assert caught.value.code == "evidence_context_binding_changed"
    assert len(case.loads) == len(case.closes) == 1
    assert report._PARSED_EVIDENCE.get() is report._SELECTED_CONTEXTS.get() is None


def test_policy_change_during_borrow_head_read_fails_closed(published_case, tmp_path, monkeypatch):
    case = published_case
    real_read = report._stable_json

    def changed(path, **kwargs):
        result = real_read(path, **kwargs)
        case.policy["policy_sha256"] = "0" * 64
        return result

    with pytest.raises(report.CandidateReportError) as caught:
        with report._parsed_evidence_operation():
            report._prepare_report_payload(case.payload, tmp_path / "reports")
            monkeypatch.setattr(report, "_stable_json", changed)
            report._borrow_selected_context(case.head, case.payload["deployment_binding"])
    assert caught.value.code == "evidence_context_binding_changed"
    assert len(case.loads) == len(case.closes) == 1
    assert report._PARSED_EVIDENCE.get() is report._SELECTED_CONTEXTS.get() is None


@pytest.mark.parametrize("horizon", [1, 63])
def test_new_policy_bundle_contains_no_html_or_large_evidence(
        published_case, tmp_path, monkeypatch, content_converter, horizon):
    case = published_case
    case.payload["horizon_sessions"] = horizon
    case.payload["report_context"] = {"schema": "single-stock-report-context-v1", "stock_id": "2330",
        "asof": case.payload["asof"], "sources": copy.deepcopy(case.payload["analysis_context"]["sources"]),
        "statuses": []}
    case.payload["report_comparison"]["metrics"] = [{"id": "valuation_per", "source_route": "finmind_per",
        "current_value": 20.5, "unit": "倍", "項目": "本益比", "資料期間": "20261016", "本期值": "20.5（倍）",
        "比較": "自身歷史正值觀測 250 筆；累積位置 45.0%", "解讀": "完整原始固定解讀留在資料附件。", "資料日期": "20261016"}]
    comparison = copy.deepcopy(case.payload["report_comparison"])
    monkeypatch.setattr(report, "build_report_insights", lambda item: {
        "summary": ["方向與報酬分支結論不同，未合併成單一多空判斷。"], "technical": [],
        "fundamentals": ["本益比歷史位置約在中段，不能由此反推合理價。"], "flows": [],
        "context": ["海外資料僅作背景，不視為模型成因。"], "data_notes": [
            "單位與比較口徑：原始單位不跨來源換算，財報期間口徑未核實時不混用累計與單季。",
            "真正缺口：現金流缺同口徑前期，未比較盈餘現金實現程度。",
            "時點與回測：歷史公告時點未核實，回測表現可能偏樂觀。"]})
    overlays = []

    def overlay(item):
        overlays.append(item["horizon_sessions"])
        item["preopen_h1"] = {"schema": report.PREOPEN_H1_SCHEMA, "status": "used",
            "stock_id": item["stock"]["stock_id"], "p_up": 0.61, "exp_ret": 0.012,
            "model_label": "開盤前版中文說明", "fixture": True}

    monkeypatch.setattr(report, "attach_report_comparison", lambda item: None)
    monkeypatch.setattr(report, "_save_descriptor_evidence", lambda *a: pytest.fail("No large evidence copy"))
    monkeypatch.setattr(report, "report_preview_html", lambda *a: pytest.fail("No HTML for new policy"))
    monkeypatch.setattr(report, "_load_fonts", lambda: SimpleNamespace(check=lambda texts: None))
    monkeypatch.setattr(report, "_render_pdf", lambda path, *a: path.write_bytes(b"Unit fixture, not a PDF"))
    monkeypatch.setattr(report, "_inspect_structure", lambda *a: {"pages": 1})
    monkeypatch.setattr(report, "_extract_if_available", lambda *a: {"status": "pending_external"})
    with report.preopen_h1_attacher(overlay):
        result = report.write_candidate_report(case.payload, tmp_path / "formal_reports")
    sidecar = json.loads(Path(result["json_path"]).read_bytes())
    assert result["valid"] is True and result["html_path"] is None
    assert {p.suffix for p in Path(result["bundle_dir"]).iterdir()} == {".pdf", ".json"}
    assert not (tmp_path / "formal_reports" / "evidence").exists()
    assert sidecar["original_payload_sha256"] == report._digest(case.payload)
    assert sidecar["sections"] == report.build_report_sections(case.payload)
    assert sidecar["sections_sha256"] == report._digest(sidecar["sections"])
    assert "方向與報酬分支結論不同" in "\n".join(report._visible_strings(sidecar["sections"]))
    visible = "\n".join(report._visible_strings(sidecar["sections"]))
    assert report._RAW_VALUE_NOTE not in visible and report._HISTORICAL_TIMING_RISK not in visible
    assert "缺少中文或口徑對照" not in visible and visible.count("真正缺口：現金流缺同口徑前期") == 1
    stored = sidecar["payload"]
    assert stored["report_context"] == case.payload["report_context"]
    assert stored["report_comparison"] == comparison
    assert "descriptor" not in stored["snapshot_binding"]
    assert "完整中央證據不可另存".encode() not in Path(result["json_path"]).read_bytes()
    original_record = case.payload["analysis_context"]["sources"][0]["records"][0]
    stored_record = stored["analysis_context"]["sources"][0]["records"][0]
    assert list(stored_record) == list(original_record)
    assert list(stored_record) != sorted(stored_record)  # Exercise a non-canonical input field order.
    assert report._digest(stored) == sidecar["stored_payload_sha256"]
    assert report.build_report_sections(stored) == sidecar["sections"]
    checked = report.validate_candidate_report(stored, Path(result["pdf_path"]))
    assert checked["valid"] is True, checked
    assert checked["deliverable"] is False and checked["render_status"] == "pending_external"
    assert {p for p in (tmp_path / "formal_reports").rglob("*") if p.is_file()} == {
        Path(result["pdf_path"]), Path(result["json_path"])}
    assert overlays == ([1] if horizon == 1 else [])
    if horizon == 1:
        assert stored["preopen_h1"] == case.payload["preopen_h1"]
        assert "開盤前版中文說明" in "\n".join(report._visible_strings(sidecar["sections"]))
    assert len(case.loads) == len(case.closes) == 2  # One write operation and one independent validation.
