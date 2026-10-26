"""Isolated adoption/report contracts; execution belongs to centralized QA."""
from __future__ import annotations

import copy
import hashlib
import json
import os
import sys
from datetime import date, datetime, timezone
from pathlib import Path
from types import SimpleNamespace

import numpy as np
import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import stock_daily_selected_deployment as selected


def assert_native_clone(original, cloned):
    assert type(cloned) is type(original)
    if type(original) is dict:
        assert cloned is not original
        assert list(cloned) == list(original)
        for key in original:
            assert_native_clone(original[key], cloned[key])
    elif type(original) is list:
        assert cloned is not original
        assert len(cloned) == len(original)
        for item, other in zip(original, cloned):
            assert_native_clone(item, other)
    elif type(original) is float:
        assert cloned.hex() == original.hex()
    else:
        assert cloned == original


@pytest.mark.parametrize("value,encoded", [
    pytest.param(None, "null", id="null"),
    pytest.param(True, "true", id="bool"),
    pytest.param(2 ** 63, "9223372036854775808", id="integer"),
    pytest.param(1.25, "1.25", id="finite-float"),
    pytest.param(-0.0, "-0.0", id="negative-zero"),
    pytest.param(float.fromhex("0x0.0000000000001p-1022"), "5e-324", id="subnormal"),
    pytest.param(float.fromhex("0x1.fffffffffffffp+1023"), "1.7976931348623157e+308", id="max-float"),
    pytest.param("台積電\n\"2330\"", '"台積電\\n\\"2330\\""', id="unicode-escaping"),
    pytest.param([], "[]", id="empty-list"),
    pytest.param({}, "{}", id="empty-dict"),
    pytest.param({"z": [None, False, -0.0, {"b": 1.25, "a": "中文"}], "a": 7},
        '{"a":7,"z":[null,false,-0.0,{"a":"中文","b":1.25}]}', id="nested-key-order"),
])
def test_native_canonical_and_detached_clone_preserve_bytes_types_and_order(monkeypatch, value, encoded):
    expected = encoded.encode("utf-8")
    assert selected.runtime._canonical(value) == expected
    monkeypatch.setattr(selected.runtime, "_canonical", lambda *a: pytest.fail("native must not clean again"))
    monkeypatch.setattr(selected.copy, "deepcopy", lambda *a: pytest.fail("native must not deepcopy"))
    assert selected._canonical_compatible(value) == expected
    assert selected._digest(value) == hashlib.sha256(expected).hexdigest()
    assert_native_clone(value, selected._detached_clone(value))


class NativeLikeString(str):
    pass


class NativeLikeDict(dict):
    pass


class NativeLikeList(list):
    pass


class NativeLikeInt(int):
    pass


class NativeLikeFloat(float):
    pass


@pytest.mark.parametrize("value", [
    pytest.param({True: 1}, id="bool-key"),
    pytest.param({1: "integer", "1": "string"}, id="key-collision-string-last"),
    pytest.param({"1": "string", 1: "integer"}, id="key-collision-integer-last"),
    pytest.param({None: "null-key"}, id="none-key"),
    pytest.param((1, {"nested": [2]}), id="tuple"),
    pytest.param({7}, id="set"),
    pytest.param(np.int64(7), id="numpy-int"),
    pytest.param(np.float64(1.25), id="numpy-float"),
    pytest.param(np.bool_(True), id="numpy-bool"),
    pytest.param(np.array(3), id="numpy-array"),
    pytest.param(date(2026, 10, 18), id="date"),
    pytest.param(datetime(2026, 10, 18, 1, 2, tzinfo=timezone.utc), id="datetime"),
    pytest.param(float("nan"), id="nan"),
    pytest.param(float("inf"), id="positive-infinity"),
    pytest.param(float("-inf"), id="negative-infinity"),
    pytest.param(NativeLikeString("中文"), id="str-subclass"),
    pytest.param(NativeLikeDict(value=[1]), id="dict-subclass"),
    pytest.param(NativeLikeList([1]), id="list-subclass"),
    pytest.param(NativeLikeInt(7), id="int-subclass"),
    pytest.param(NativeLikeFloat(1.25), id="float-subclass"),
    pytest.param({NativeLikeString("key"): 1}, id="str-subclass-key"),
    pytest.param({"nested": [np.float64(1.25)]}, id="nested-nonnative"),
])
def test_non_native_canonical_and_clone_use_unchanged_fallback(monkeypatch, value):
    canonical, deepcopy = selected.runtime._canonical, copy.deepcopy
    expected = canonical(value)
    expected_clone = deepcopy(value)
    calls = []

    def old_canonical(item):
        assert item is value
        calls.append("canonical")
        return canonical(item)

    def old_deepcopy(item):
        assert item is value
        calls.append("deepcopy")
        return deepcopy(item)

    monkeypatch.setattr(selected.runtime, "_canonical", old_canonical)
    monkeypatch.setattr(selected.copy, "deepcopy", old_deepcopy)
    assert selected._canonical_compatible(value) == expected
    cloned = selected._detached_clone(value)
    assert type(cloned) is type(expected_clone)
    assert canonical(cloned) == canonical(expected_clone)
    if isinstance(value, dict):
        assert [type(key) for key in cloned] == [type(key) for key in expected_clone]
    if isinstance(value, np.ndarray):
        assert cloned is not value
        assert cloned.dtype == value.dtype
        assert np.array_equal(cloned, expected_clone)
    assert calls == ["canonical", "deepcopy"]


@pytest.mark.parametrize("value", [object(), np.array([1, 2])], ids=["unsupported-object", "multi-item-array"])
def test_non_native_canonical_retains_original_exception(value):
    with pytest.raises((TypeError, ValueError)) as original:
        selected.runtime._canonical(value)
    with pytest.raises(type(original.value)) as compatible:
        selected._canonical_compatible(value)
    assert str(compatible.value) == str(original.value)


@pytest.mark.parametrize("shared", [[], {}], ids=["list-alias", "dict-alias"])
def test_shared_containers_keep_deepcopy_alias_and_input_isolation(monkeypatch, shared):
    value = {"left": shared, "nested": [shared]}
    canonical, deepcopy = selected.runtime._canonical, copy.deepcopy
    expected = canonical(value)
    calls = []

    def old_canonical(item):
        calls.append("canonical")
        return canonical(item)

    def old_deepcopy(item):
        calls.append("deepcopy")
        return deepcopy(item)

    monkeypatch.setattr(selected.runtime, "_canonical", old_canonical)
    monkeypatch.setattr(selected.copy, "deepcopy", old_deepcopy)
    assert selected._canonical_compatible(value) == expected
    cloned = selected._detached_clone(value)
    assert calls == ["canonical", "deepcopy"]
    assert cloned["left"] is cloned["nested"][0]
    assert cloned["left"] is not shared
    if type(shared) is list:
        cloned["left"].append("changed")
        assert shared == []
    else:
        cloned["left"]["changed"] = True
        assert shared == {}


@pytest.mark.parametrize("kind", [list, dict], ids=["list-cycle", "dict-cycle"])
def test_cycles_keep_canonical_failure_and_deepcopy_graph(monkeypatch, kind):
    value = kind()
    if kind is list:
        value.append(value)
    else:
        value["self"] = value
    canonical, deepcopy = selected.runtime._canonical, copy.deepcopy
    calls = []

    def old_canonical(item):
        calls.append("canonical")
        return canonical(item)

    def old_deepcopy(item):
        calls.append("deepcopy")
        return deepcopy(item)

    monkeypatch.setattr(selected.runtime, "_canonical", old_canonical)
    monkeypatch.setattr(selected.copy, "deepcopy", old_deepcopy)
    with pytest.raises(RecursionError):
        selected._canonical_compatible(value)
    cloned = selected._detached_clone(value)
    assert cloned is not value
    assert (cloned[0] if kind is list else cloned["self"]) is cloned
    assert calls == ["canonical", "deepcopy"]


def test_native_clone_and_digest_recheck_caller_mutation():
    value = {"left": [{"number": -0.0}], "right": []}
    cloned = selected._detached_clone(value)
    first_digest = selected._digest(value)
    value["left"][0]["number"] = 2.5
    assert cloned["left"][0]["number"].hex() == "-0x0.0p+0"
    assert selected._digest(value) != first_digest
    assert selected._digest(value) == hashlib.sha256(selected.runtime._canonical(value)).hexdigest()
    value["right"] = value["left"]
    alias_clone = selected._detached_clone(value)
    assert alias_clone["left"] is alias_clone["right"]
    alias_clone["left"][0]["number"] = 3.5
    assert value["left"][0]["number"] == 2.5
    value["left"][0]["number"] = np.float64(4.5)
    nonnative_clone = selected._detached_clone(value)
    assert type(nonnative_clone["left"][0]["number"]) is np.float64


def test_native_clone_retains_deepcopy_support_for_large_integers():
    value = {"integer": 1 << 20000}
    cloned = selected._detached_clone(value)
    assert cloned == value and cloned is not value
    assert type(cloned["integer"]) is int


def save(path, value):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, ensure_ascii=False, sort_keys=True), encoding="utf-8")
    return {"path": str(path), "sha256": hashlib.sha256(path.read_bytes()).hexdigest()}


@pytest.fixture
def adoption(monkeypatch, tmp_path):
    root = tmp_path / "single"
    pointer = root / "models" / "selected_current.json"
    model = tmp_path / "research" / "candidate_model.json"
    model.parent.mkdir()
    model.write_bytes(b'{"frozen_model_fixture":true}')
    model_hash = hashlib.sha256(model.read_bytes()).hexdigest()
    model.with_suffix(".json.sha256").write_text(model_hash, encoding="utf-8")
    monkeypatch.setattr(selected, "SINGLE_ROOT", root)
    monkeypatch.setattr(selected, "SELECTED_CURRENT", pointer)
    monkeypatch.setattr(selected, "APPROVED_MODEL_PATH", model)
    monkeypatch.delenv(selected.BINDING_ENV, raising=False)
    selected._DIGEST_CACHE.clear()
    selected._MODEL_CACHE.clear()
    feature = Path(selected.__file__).with_name("stock_daily_feature_catalog.py")
    feature_hash = selected._file_sha(feature)
    monkeypatch.setattr(selected, "_load_model", lambda binding: ({"metadata": {
        "runtime_input_contract": {"feature_module_sha256": feature_hash}}}, model))
    note = "User explicitly authorizes the existing model despite mixed benchmark metrics."
    research = save(tmp_path / "research_acceptance.json", {"acceptance": "passed_research_reports_only",
        "research_only": True, "promotion": False, "can_deliver_research_report": True, "model_sha256": model_hash})
    tests = save(tmp_path / "focused_tests.json", {"exit_code": 0, "command": "python -m pytest selected_tests"})
    source = tmp_path / "report_source.py"
    source.write_text("version = 1\n", encoding="utf-8")
    acceptance = tmp_path / "preactivation_acceptance.json"
    save(acceptance, {"schema": selected.ACCEPTANCE_SCHEMA, "consumer_id": "single",
        "status": "passed_preactivation_review", "formal_adoption_authorized": True,
        "model_path": str(model), "model_sha256": model_hash,
        "authorization_note_sha256": selected._authorization(note)["sha256"],
        "research_acceptance": research, "focused_tests": tests,
        "source_bindings": [{"path": str(source), "sha256": selected._file_sha(source)}]})
    published = tmp_path / "published"
    raw = b'{"schema":"head-fixture"}'
    head_hash = hashlib.sha256(raw).hexdigest()
    (published / "_metadata").mkdir(parents=True)
    (published / "_manifests").mkdir()
    (published / "_metadata" / "stock_data_current.json").write_bytes(raw)
    (published / "_manifests" / (head_hash + ".json")).write_bytes(raw)
    monkeypatch.setattr(selected.runtime, "PUBLISHED_ROOT", published)
    binding = {"schema": selected.DEPLOYMENT_SCHEMA, "consumer_id": "single",
        "activation_id": "preactivation-fixture", "approved_at": "2026-10-17T12:00:00+00:00",
        "model_path": str(model), "model_sha256": model_hash, "cache_dir": str(tmp_path / "cache"),
        "authorization": selected._authorization(note), "prior": None}
    return SimpleNamespace(root=root, pointer=pointer, model=model, model_hash=model_hash,
        note=note, acceptance=acceptance, binding=binding, source=source, published=published, head_hash=head_hash)


def activate(case):
    return selected.activate_selected_model(case.model, cache_dir=case.binding["cache_dir"],
        authorization_note=case.note, acceptance_path=case.acceptance)


def candidate_payload(case, horizon=1):
    payload = {"schema": selected.runtime.REPORT_SCHEMA, "language": "zh-TW", "research_only": True, "promotion": False,
        "stock": {"stock_id": "2330", "stock_name": "台積電", "market": "TWSE"},
        "horizon_sessions": horizon, "asof": "2026-10-16", "target_session_date": None,
        "prediction": {"prob_up": 0.58, "expected_return": -0.0123},
        "model_binding": {"path": str(case.model), "sha256": case.model_hash},
        "snapshot_binding": {"head_sha256": case.head_hash, "version_labels": []},
        "source_manifest": [{"route_id": "finmind_month_revenue", "status": "PARTIAL"}],
        "feature_values": [{"value": 0.023}], "analysis_context": {}, "metrics": {"brier": 0.29},
        "feature_limitations": [], "limitations": ["本報告為候選研究，未正式採用，不更新既有模型、指標或前瞻帳本。"],
        "price_history": [{"date": "2026-10-16", "close": 102.0}], "price_volume_unit": "股",
        "data_quality_assessment": {"status": "complete"}, "generated_at": "2026-10-17T12:00:00+00:00"}
    payload["report_context"] = {"schema": "single-stock-report-context-v1", "stock_id": "2330",
        "asof": payload["asof"], "sources": [], "statuses": {}}
    payload["runtime_payload_sha256"] = selected._digest(payload)
    return payload


def test_absent_pointer_has_no_model_or_snapshot_work(adoption, monkeypatch):
    monkeypatch.setattr(selected, "_model_digest", lambda *a: pytest.fail("absent must not read model"))
    monkeypatch.setattr(selected, "load_selected_context", lambda *a: pytest.fail("environment must not build context"))
    assert selected.active_selected_binding() is None
    assert selected.selected_analysis_environment() is None


def test_explicit_activation_is_isolated_and_receipts_are_create_only(adoption):
    shared = adoption.pointer.parents[2] / "shared_current.json"
    shared.write_bytes(b"unchanged-shared-current")
    before = adoption.model.read_bytes()
    first = activate(adoption)
    receipts = {path: path.read_bytes() for path in adoption.pointer.parent.joinpath("selected_receipts").glob("*.json")}
    assert len(receipts) == 2
    assert first == selected.active_selected_binding()
    second = activate(adoption)
    assert second["prior"]["activation_id"] == first["activation_id"]
    assert second["activation_id"] != first["activation_id"]
    assert all(path.read_bytes() == raw for path, raw in receipts.items())
    assert shared.read_bytes() == b"unchanged-shared-current"
    assert adoption.model.read_bytes() == before


def test_old_research_receipt_alone_is_not_activation_acceptance(adoption):
    old = json.loads(adoption.acceptance.read_text(encoding="utf-8"))["research_acceptance"]["path"]
    with pytest.raises(ValueError, match="preactivation_acceptance_required"):
        selected.activate_selected_model(adoption.model, cache_dir=adoption.binding["cache_dir"],
            authorization_note=adoption.note, acceptance_path=old)
    assert not adoption.pointer.exists()


@pytest.mark.parametrize("field,value", [("formal_adoption_authorized", False),
    ("authorization_note_sha256", "0" * 64), ("model_sha256", "0" * 64)])
def test_acceptance_cannot_substitute_authorization_or_model(adoption, field, value):
    document = json.loads(adoption.acceptance.read_text(encoding="utf-8"))
    document[field] = value
    save(adoption.acceptance, document)
    with pytest.raises(ValueError):
        activate(adoption)
    assert not adoption.pointer.exists()


def test_deployment_checks_source_but_later_non_numeric_drift_is_label(adoption):
    adoption.source.write_text("version = 2\n", encoding="utf-8")
    with pytest.raises(ValueError, match="accepted_source_changed"):
        activate(adoption)
    adoption.source.write_text("version = 1\n", encoding="utf-8")
    binding = activate(adoption)
    adoption.source.write_text("version = 3\n", encoding="utf-8")
    assert selected.active_selected_binding() == binding
    environment, job = selected.selected_analysis_environment()
    assert any(item["code"] == "selected_non_numeric_source_differs" for item in job["version_labels"])
    assert json.loads(environment[selected.BINDING_ENV]) == job


def test_environment_refreshes_activation_without_context_or_model_parse(adoption, monkeypatch):
    first = activate(adoption)
    monkeypatch.setattr(selected, "load_selected_context", lambda *a, **k: pytest.fail("no full context"))
    monkeypatch.setattr(selected, "_load_model", lambda *a, **k: pytest.fail("no model parse"))
    env, job = selected.selected_analysis_environment()
    assert job["deployment_binding"] == first
    assert job["head_sha256"] == adoption.head_hash
    assert job["report_context_schema"] == selected.REPORT_CONTEXT_SCHEMA
    identities = [("shared_market_data", adoption.head_hash, None)]
    assert job["snapshot_fingerprint"] == hashlib.sha256(json.dumps(identities, ensure_ascii=True).encode()).hexdigest()
    assert json.loads(env[selected.BINDING_ENV]) == job


def test_model_same_size_restored_mtime_tamper_is_not_cached(adoption):
    activate(adoption)
    stat = adoption.model.stat()
    raw = adoption.model.read_bytes()
    adoption.model.write_bytes(b"x" * len(raw))
    os.utime(adoption.model, ns=(stat.st_atime_ns, stat.st_mtime_ns))
    with pytest.raises(ValueError, match="model_digest_mismatch"):
        selected.active_selected_binding()


def test_acceptance_byte_tamper_is_not_version_drift(adoption):
    activate(adoption)
    with adoption.acceptance.open("a", encoding="utf-8") as handle:
        handle.write("\n")
    with pytest.raises(ValueError, match="evidence_digest_mismatch"):
        selected.active_selected_binding()


def test_job_retains_legal_activation_and_head_after_new_activation(adoption, monkeypatch):
    first = activate(adoption)
    env, job = selected.selected_analysis_environment()
    second = activate(adoption)
    monkeypatch.setenv(selected.BINDING_ENV, env[selected.BINDING_ENV])
    pinned = selected.analysis_binding()
    assert pinned["deployment_binding"] == first
    assert pinned["head_sha256"] == job["head_sha256"]
    assert any(label["code"] == "selected_activation_changed_after_job_start" for label in pinned["version_labels"])
    assert second["activation_id"] != first["activation_id"]


def test_preactivation_needs_no_pointer_and_keeps_all_numbers(adoption):
    original = candidate_payload(adoption)
    payload = selected._formal_payload(original, adoption.binding, audit=True)
    assert not adoption.pointer.exists()
    assert payload["schema"] == selected.REPORT_SCHEMA
    assert payload["research_only"] is False and payload["promotion"] is True
    assert payload["deployment_status"] == "preactivation_audit"
    for field in ("prediction", "metrics", "feature_values", "price_history", "source_manifest"):
        assert payload[field] == original[field]
    assert payload["target_session_date"] is None
    assert original["schema"] == selected.runtime.REPORT_SCHEMA


@pytest.mark.parametrize("audit", [False, True])
def test_formal_payload_native_path_matches_old_bytes_and_isolates_callers(adoption, monkeypatch, audit):
    original = candidate_payload(adoption)
    binding = adoption.binding
    expected = copy.deepcopy(original)
    candidate_sha = expected.pop("runtime_payload_sha256")
    expected.update(schema=selected.REPORT_SCHEMA, language="zh-TW", research_only=False, promotion=True,
        deployment_binding=copy.deepcopy(binding), deployment_status="preactivation_audit" if audit else "active",
        candidate_runtime_payload_sha256=candidate_sha, report_context_schema=selected.REPORT_CONTEXT_SCHEMA)
    expected["limitations"] = ["本報告為啟用前獨立驗收，不代表已啟用或可傳送。" if audit else
        "本模型已獲授權用於單股報告；採用不代表全面勝過基準，不改共用模型或前瞻帳本。"]
    expected["runtime_payload_sha256"] = hashlib.sha256(selected.runtime._canonical(expected)).hexdigest()
    expected_bytes = selected.runtime._canonical(expected)
    monkeypatch.setattr(selected.runtime, "_canonical", lambda *a: pytest.fail("native formal payload must not reclean"))
    monkeypatch.setattr(selected.copy, "deepcopy", lambda *a: pytest.fail("native formal payload must not deepcopy"))
    result = selected._formal_payload(original, binding, audit=audit)
    second = selected._formal_payload(original, binding, audit=audit)
    assert json.dumps(result, ensure_ascii=False, sort_keys=True,
        separators=(",", ":"), allow_nan=False).encode("utf-8") == expected_bytes
    assert_native_clone(expected, result)
    result["prediction"]["prob_up"] = 0.1
    result["deployment_binding"]["authorization"]["note"] = "caller mutation"
    assert original["prediction"]["prob_up"] == second["prediction"]["prob_up"] == 0.58
    assert binding["authorization"]["note"] == second["deployment_binding"]["authorization"]["note"] == adoption.note
    original["source_manifest"][0]["status"] = "caller mutation"
    assert second["source_manifest"][0]["status"] == "PARTIAL"


@pytest.mark.parametrize("audit", [False, True])
@pytest.mark.parametrize("notes", [None, [],
    ["保留原始限制。"],
    ["本報告為候選研究，未正式採用，不更新既有模型、指標或前瞻帳本。"],
    ["保留原始限制。", "本報告為候選研究，未正式採用，不更新既有模型、指標或前瞻帳本。",
        "本報告為候選研究，未正式採用，不更新既有模型、指標或前瞻帳本。",
        "本報告為候選研究，未正式採用，不更新既有模型、指標或前瞻帳本。 "]])
def test_formal_limitations_preserve_exact_values_and_input(notes, audit):
    before = copy.deepcopy(notes)
    old = "本報告為候選研究，未正式採用，不更新既有模型、指標或前瞻帳本。"
    replacement = ("本報告為啟用前獨立驗收，不代表已啟用或可傳送。" if audit else
        "本模型已獲授權用於單股報告；採用不代表全面勝過基準，不改共用模型或前瞻帳本。")
    expected = [replacement if note == old else note for note in (notes or [])]
    result = selected._formal_limitations(notes, audit=audit)
    assert result == expected
    assert result is not notes
    assert notes == before


@pytest.mark.parametrize("audit", [False, True])
def test_unsealed_formal_payload_preserves_final_digest_and_input(adoption, monkeypatch, audit):
    original = candidate_payload(adoption)
    before = selected.runtime._canonical(original)
    binding_before = selected.runtime._canonical(adoption.binding)
    sealed = selected._formal_payload(original, adoption.binding, audit=audit)
    monkeypatch.setattr(selected, "_digest", lambda *a: pytest.fail("unsealed payload must not hash"))
    unsealed = selected._formal_payload(original, adoption.binding, audit=audit, seal=False)
    assert "runtime_payload_sha256" not in unsealed
    assert unsealed == {key: value for key, value in sealed.items() if key != "runtime_payload_sha256"}
    assert unsealed["candidate_runtime_payload_sha256"] == original["runtime_payload_sha256"]
    for value in (sealed, unsealed):
        value["inference_horizons"] = [1, 63]
        value["inference_cache_dir"] = adoption.binding["cache_dir"]
        unsigned = {key: item for key, item in value.items() if key != "runtime_payload_sha256"}
        raw = json.dumps(unsigned, ensure_ascii=False, sort_keys=True,
            separators=(",", ":"), allow_nan=False).encode("utf-8")
        value["runtime_payload_sha256"] = hashlib.sha256(raw).hexdigest()
    assert unsealed == sealed
    unsealed["prediction"]["prob_up"] = 0.1
    unsealed["deployment_binding"]["authorization"]["note"] = "caller mutation"
    assert selected.runtime._canonical(original) == before
    assert selected.runtime._canonical(adoption.binding) == binding_before


def test_preactivation_report_cannot_pass_delivery_validator(adoption):
    payload = selected._formal_payload(candidate_payload(adoption), adoption.binding, audit=True)
    result = selected.validate_selected_report(payload, b"%PDF-1.4\n%%EOF\n", stock_id="2330", horizon=1)
    assert result["valid"] is False
    assert "preactivation_report_not_deliverable" in str(result["errors"])


def test_pdf_marker_is_not_a_real_pdf_validation(adoption, monkeypatch):
    import stock_candidate_report as report
    monkeypatch.setattr(report, "build_report_sections", lambda payload: [])
    payload = selected._formal_payload(candidate_payload(adoption), adoption.binding, audit=True)
    raw = b"%PDF-1.4\nmarker-only\n%%EOF\n"
    bundle = {"schema": report._BUNDLE_V1, "payload": payload, "payload_sha256": report._digest(payload),
        "sections": [], "sections_sha256": report._digest([]),
        "pdf": {"sha256": hashlib.sha256(raw).hexdigest(), "size": len(raw)}}
    checked = selected.validate_selected_report(bundle, raw, stock_id="2330", horizon=1, require_active=False)
    assert checked["valid"] is False
    assert "PDF" in str(checked["errors"])


@pytest.fixture
def legacy_reader_plan(monkeypatch):
    # These comparison tests stub inference; real locator replay is covered separately.
    monkeypatch.setattr(selected.runtime, "read_plan_from_manifest", lambda manifest, cache, head:
        selected.runtime._seal_read_plan(selected.runtime._empty_read_plan(cache, head, legacy=True)))


def test_real_reproduction_rejects_changed_prediction(adoption, monkeypatch, legacy_reader_plan):
    import stock_candidate_report as report
    original = candidate_payload(adoption)
    payload = selected._formal_payload(original, adoption.binding, audit=True)
    payload["prediction"]["prob_up"] = 0.99
    payload["runtime_payload_sha256"] = selected._digest({key: value for key, value in payload.items()
        if key != "runtime_payload_sha256"})
    monkeypatch.setattr(report, "build_report_sections", lambda payload: [])
    monkeypatch.setattr(report, "_inspect_structure", lambda *a: {"pages": 1})
    monkeypatch.setattr(selected, "_inference", lambda *a, **k: [copy.deepcopy(original)])
    raw = b"%PDF-1.4\nfixture-isolated-structure\n%%EOF\n"
    bundle = {"schema": report._BUNDLE_V1, "payload": payload, "payload_sha256": report._digest(payload),
        "sections": [], "sections_sha256": report._digest([]),
        "pdf": {"sha256": hashlib.sha256(raw).hexdigest(), "size": len(raw)}}
    checked = selected.validate_selected_report(bundle, raw, stock_id="2330", horizon=1, require_active=False)
    assert checked["valid"] is False
    assert "prediction_or_evidence_mismatch:prediction" in str(checked["errors"])


@pytest.mark.parametrize("scope,field", [
    ("payload", field) for field in ("stock", "asof", "target_session_date", "prediction", "model_binding",
        "feature_values", "analysis_context", "metrics", "feature_limitations", "limitations", "price_history",
        "price_volume_unit", "data_quality_assessment", "source_manifest", "report_context")
] + [
    ("snapshot_binding", field) for field in ("head_sha256", "snapshot_fingerprint", "descriptor",
        "identity_binding", "retention_binding", "capture_binding", "asof_by_market", "window_by_market")
])
def test_formal_validator_keeps_each_evidence_comparison(adoption, monkeypatch, legacy_reader_plan, scope, field):
    import stock_candidate_report as report
    original = candidate_payload(adoption)
    payload = selected._formal_payload(original, adoption.binding, audit=True)
    target = payload if scope == "payload" else payload[scope]
    if field == "head_sha256":
        target[field] = "0" * 64
    elif isinstance(target.get(field), dict):
        target[field]["tampered"] = True
    elif isinstance(target.get(field), list):
        target[field].append("tampered")
    else:
        target[field] = "tampered"
    if scope == "payload" and field == "asof":
        payload["report_context"]["asof"] = payload["asof"]
    payload["runtime_payload_sha256"] = selected._digest({key: item for key, item in payload.items()
        if key != "runtime_payload_sha256"})
    monkeypatch.setattr(report, "build_report_sections", lambda payload: [])
    monkeypatch.setattr(report, "_inspect_structure", lambda *a: {"pages": 1})
    calls = []

    def inference(*args, read_only, read_plan):
        assert read_only is True and read_plan["legacy"] is True
        calls.append(args)
        return [copy.deepcopy(original)]

    monkeypatch.setattr(selected, "_inference", inference)
    raw = b"%PDF-1.4\nfixture-isolated-structure\n%%EOF\n"
    bundle = {"schema": report._BUNDLE_V1, "payload": payload, "payload_sha256": report._digest(payload),
        "sections": [], "sections_sha256": report._digest([]),
        "pdf": {"sha256": hashlib.sha256(raw).hexdigest(), "size": len(raw)}}
    result = selected.validate_selected_report(bundle, raw, stock_id="2330", horizon=1, require_active=False)
    assert result["valid"] is False and result["prediction_reproduced"] is False
    mismatch = "prediction_or_evidence_mismatch:" if scope == "payload" else "snapshot_reproduction_mismatch:"
    assert mismatch + field in str(result["errors"])
    assert len(calls) == 1


@pytest.mark.parametrize("audit", [False, True])
def test_validator_reproduces_every_call_without_mutating_input(adoption, monkeypatch, legacy_reader_plan, audit):
    import stock_candidate_report as report
    originals = [candidate_payload(adoption, horizon) for horizon in (1, 63)]
    originals_before = selected.runtime._canonical(originals)
    payload = selected._formal_payload(originals[0], adoption.binding, audit=audit)
    payload["inference_horizons"] = [1, 63]
    payload["runtime_payload_sha256"] = selected._digest({key: item for key, item in payload.items()
        if key != "runtime_payload_sha256"})
    raw = b"%PDF-1.4\nfixture-isolated-structure\n%%EOF\n"
    bundle = {"schema": report._BUNDLE_V1, "payload": payload, "payload_sha256": report._digest(payload),
        "sections": [], "sections_sha256": report._digest([]),
        "pdf": {"sha256": hashlib.sha256(raw).hexdigest(), "size": len(raw)}}
    before = selected.runtime._canonical(bundle)
    calls = []
    clone_inputs, formal_digests = [], []
    detached_clone, digest = selected._detached_clone, selected._digest

    def clone(value):
        assert all(value is not original for original in originals), "reproduced evidence must not be cloned"
        clone_inputs.append(value)
        return detached_clone(value)

    def counted_digest(value):
        if value.get("schema") == selected.REPORT_SCHEMA:
            formal_digests.append(value)
        return digest(value)

    def inference(stock_query, binding, horizons, cache, head, *, read_only, read_plan):
        assert read_only is True and read_plan["legacy"] is True
        calls.append((stock_query, horizons, head))
        return originals

    monkeypatch.setattr(report, "build_report_sections", lambda payload: [])
    monkeypatch.setattr(report, "_inspect_structure", lambda *a: {"pages": 1})
    monkeypatch.setattr(selected, "_inference", inference)
    monkeypatch.setattr(selected, "_detached_clone", clone)
    monkeypatch.setattr(selected, "_digest", counted_digest)
    for _ in range(2):
        result = selected.validate_selected_report(bundle, raw, stock_id="2330", horizon=1, require_active=False)
        assert result["valid"] is True and result["prediction_reproduced"] is True
        assert result["activation_verified"] is False
    assert calls == [("2330", (1, 63), adoption.head_hash)] * 2
    assert len(clone_inputs) == 2 and all(value is payload for value in clone_inputs)
    assert len(formal_digests) == 2
    assert selected.runtime._canonical(bundle) == before
    assert selected.runtime._canonical(originals) == originals_before


@pytest.mark.parametrize("with_pdf_path", [False, True])
def test_validator_builds_sections_once_per_branch(adoption, monkeypatch, legacy_reader_plan, tmp_path, with_pdf_path):
    import stock_candidate_report as report
    original = candidate_payload(adoption)
    payload = selected._formal_payload(original, adoption.binding, audit=True)
    raw = b"%PDF-1.4\nfixture-isolated-structure\n%%EOF\n"
    bundle = {"schema": report._BUNDLE_V1, "payload": payload, "payload_sha256": report._digest(payload),
        "sections": [], "sections_sha256": report._digest([]),
        "pdf": {"sha256": hashlib.sha256(raw).hexdigest(), "size": len(raw)}}
    section_calls, structure_calls = [], []

    def sections(value):
        section_calls.append(value)
        return []

    def structure(path, values):
        structure_calls.append((path, values))
        return {"pages": 1}

    monkeypatch.setattr(report, "build_report_sections", sections)
    monkeypatch.setattr(report, "_inspect_structure", structure)
    monkeypatch.setattr(report, "_load_fonts", lambda: SimpleNamespace(check=lambda values: None))
    monkeypatch.setattr(report, "_extract_if_available", lambda *a: {"status": "passed"})
    monkeypatch.setattr(selected, "_inference", lambda *a, **k: [original])
    pdf_path = None
    if with_pdf_path:
        pdf_path = tmp_path / "report.pdf"
        pdf_path.write_bytes(raw)
        pdf_path.with_suffix(".json").write_text(json.dumps(bundle, ensure_ascii=False), encoding="utf-8")
    result = selected.validate_selected_report(bundle, raw, stock_id="2330", horizon=1,
        pdf_path=pdf_path, require_active=False)
    assert result["valid"] is True, result["errors"]
    assert len(section_calls) == len(structure_calls) == 1
    if with_pdf_path:
        assert result["pdf_validation"]["scope"] == "language_font_sections_pdf_integrity_preflight"
        assert Path(structure_calls[0][0]) == pdf_path


def test_output_boundaries_and_interactive_scan_policy(adoption):
    with pytest.raises(ValueError, match="output_outside_consumer_scope"):
        selected._output_root(adoption.root / "reports", audit=True)
    assert selected._output_root(adoption.root / "audit" / "preactivation", audit=True).is_relative_to(adoption.root)
    source = Path(selected.__file__).read_text(encoding="utf-8")
    assert "allow_full_scan=read_only" in source and "read_only=True" in source
    assert "load_active_c0_model(" not in source


@pytest.mark.parametrize("pinned", [False, True])
def test_selected_context_never_loads_old_model(adoption, monkeypatch, pinned):
    calls = []
    monkeypatch.setattr(selected.contract, "c0_policy_binding", lambda **k: {"consumer_id": "single"})
    monkeypatch.setattr(selected.contract, "load_active_c0_model", lambda *a, **k: pytest.fail("no old model"))
    monkeypatch.setattr(selected.contract, "load_c0_published_context", lambda *a, **k: calls.append((a, k)) or {})
    selected.load_selected_context(adoption.head_hash if pinned else None, adoption.binding)
    assert calls[0][1]["consumer_id"] == "single"
    assert calls[0][1]["runtime_binding"]["artifact_binding"]["sha256"] == adoption.model_hash
    assert calls[0][1].get("selected_single_build", False) is pinned
    assert (calls[0][0][0] is not None) is pinned


@pytest.mark.parametrize("cold", [False, True])
@pytest.mark.parametrize("borrowed", [False, True])
def test_non_training_stock_uses_projection_but_cold_effective_feature_blocks(adoption, monkeypatch, cold, borrowed):
    feature = Path(selected.__file__).with_name("stock_daily_feature_catalog.py")
    model = {"metadata": {"feature_metadata": {"effective_x": {"source_route": "finmind_per"}},
        "runtime_input_contract": {"horizons": [1], "feature_module_sha256": selected._file_sha(feature),
            "source_plan_by_horizon": {"1": [{"route_id": "finmind_per", "usage": "used"}]}}},
        "horizons": {"1": {"selected": {"direction": "expanded_linear"},
            "candidates": {"expanded_linear": {"transformer": {"effective_feature_order": ["effective_x"]}}}}}}
    monkeypatch.setattr(selected, "_load_model", lambda binding: (model, adoption.model))
    source_closes, context_loads = [], []
    sources = SimpleNamespace(close=lambda: source_closes.append(True))
    context = {
        "sources": sources,
        "current_identity": {"records": [{"stock_id": "2330", "stock_name": "台積電", "market": "TWSE"}]}}
    monkeypatch.setattr(selected, "load_selected_context", lambda *a: context_loads.append(a) or context)
    calls = []
    def inputs(stock_ids, **kwargs):
        calls.append((stock_ids, kwargs))
        return {"prepared": {}, "source_frames": {}, "source_manifest": [{"route_id": "finmind_per",
            "status": "projection_not_ready" if cold else "SUCCESS"}],
            "snapshot": {"head_sha256": adoption.head_hash, "version_labels": []}}
    monkeypatch.setattr(selected.runtime, "load_candidate_inputs", inputs)
    monkeypatch.setattr(selected.runtime, "_apply_frozen_schema", lambda *a: None)
    monkeypatch.setattr(selected.runtime, "_predict_payloads", lambda *a, **k: [candidate_payload(adoption)])
    monkeypatch.setitem(sys.modules, "stock_report_analysis", SimpleNamespace(build_report_context=lambda *a, **k:
        copy.deepcopy(candidate_payload(adoption)["report_context"])))
    options = {"verified_context": context} if borrowed else {}
    if cold:
        with pytest.raises(ValueError, match="required_feature_projection_not_ready:finmind_per"):
            selected._inference("2330", adoption.binding, (1,), adoption.root / "cache", adoption.head_hash, **options)
    else:
        assert selected._inference("2330", adoption.binding, (1,), adoption.root / "cache", adoption.head_hash, **options)
    assert calls[0][0] == ["2330"]
    assert calls[0][1]["allow_full_scan"] is False
    assert calls[0][1]["context"]["sources"] is sources
    assert len(context_loads) == (0 if borrowed else 1)
    assert source_closes == ([] if borrowed else [True])


@pytest.mark.parametrize("horizons", [(1,), (1, 63)])
def test_preactivation_generation_keeps_exact_output_protocol(adoption, monkeypatch, capsys, horizons):
    originals = [candidate_payload(adoption, horizon) for horizon in horizons]
    for original in originals:
        original["storage_policy"] = selected.READ_ONLY_STORAGE_POLICY
        original["read_plan"] = selected.runtime._seal_read_plan(
            selected.runtime._empty_read_plan(adoption.binding["cache_dir"], adoption.head_hash))
        original["runtime_payload_sha256"] = selected._digest(
            {key: value for key, value in original.items() if key != "runtime_payload_sha256"})
    before = selected.runtime._canonical(originals)
    expected_payloads = [selected._formal_payload(value, adoption.binding, audit=True) for value in originals]
    for expected in expected_payloads:
        expected["inference_horizons"] = list(horizons)
        unsigned = {key: value for key, value in expected.items() if key != "runtime_payload_sha256"}
        expected["runtime_payload_sha256"] = hashlib.sha256(json.dumps(unsigned, ensure_ascii=False,
            sort_keys=True, separators=(",", ":"), allow_nan=False).encode("utf-8")).hexdigest()
    formal_digests, rendered = [], []
    digest = selected._digest

    def counted_digest(value):
        if value.get("schema") == selected.REPORT_SCHEMA:
            formal_digests.append(value)
        return digest(value)

    def write(payload, output):
        index = len(rendered)
        assert payload == expected_payloads[index]
        assert len(formal_digests) == index + 1
        rendered.append(copy.deepcopy(payload))
        directory = output / ("selected_2330_H" + str(payload["horizon_sessions"])) / "unique"
        directory.mkdir(parents=True)
        stem = "selected_2330_H" + str(payload["horizon_sessions"])
        pdf, data = directory / (stem + ".pdf"), directory / (stem + ".json")
        pdf.write_bytes(b"%PDF-1.4\nfixture-dispatch-only\n%%EOF\n")
        data.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")
        return {"pdf_path": str(pdf), "json_path": str(data)}
    monkeypatch.setitem(sys.modules, "stock_candidate_report", SimpleNamespace(write_candidate_report=write))
    def inference(*args, read_only):
        assert read_only is True
        return originals

    monkeypatch.setattr(selected, "_inference", inference)
    monkeypatch.setattr(selected, "_digest", counted_digest)
    def validate(payload, *args, **kwargs):
        assert kwargs["expected_runtime_binding"]["report_context_schema"] == selected.REPORT_CONTEXT_SCHEMA
        assert payload["report_context_schema"] == selected.REPORT_CONTEXT_SCHEMA
        return {"valid": True}

    monkeypatch.setattr(selected, "validate_selected_report", validate)
    output = adoption.root / "audit" / "preactivation"
    payloads = selected.generate_selected_reports("2330", output_dir=output,
        horizons=horizons, activation_binding=adoption.binding)
    assert not adoption.pointer.exists()
    lines = capsys.readouterr().out.splitlines()
    assert lines == [line for payload in payloads
        for line in ("PDF：" + payload["output_pdf"], "JSON：" + payload["output_json"])]
    assert len(formal_digests) == len(rendered) == len(horizons)
    assert rendered == expected_payloads
    assert selected.runtime._canonical(originals) == before
    for payload in payloads:
        assert Path(payload["output_pdf"]).parent == Path(payload["output_json"]).parent
        assert output in Path(payload["output_pdf"]).parents


@pytest.mark.parametrize("change", ["missing", "schema", "stock_id", "asof", "sources", "statuses", "nonfinite"])
def test_new_formal_payload_requires_bound_json_report_context(adoption, change):
    original = candidate_payload(adoption)
    context = original["report_context"]
    if change == "missing":
        original.pop("report_context")
    elif change in {"sources", "statuses"}:
        context.pop(change)
    elif change == "nonfinite":
        context["unexpected"] = float("nan")
    else:
        context[change] = "wrong"
    with pytest.raises(selected.SelectedDeploymentError, match="selected_report_context"):
        selected._formal_payload(original, adoption.binding, audit=True)


@pytest.mark.parametrize("removed", [("report_context",), ("report_context_schema",),
    ("report_context", "report_context_schema")])
def test_current_job_cannot_downgrade_to_context_free_legacy(adoption, removed):
    payload = selected._formal_payload(candidate_payload(adoption), adoption.binding, audit=True)
    for key in removed:
        payload.pop(key)
    payload["runtime_payload_sha256"] = selected._digest(
        {key: value for key, value in payload.items() if key != "runtime_payload_sha256"})
    job = {"deployment_binding": adoption.binding, "report_context_schema": selected.REPORT_CONTEXT_SCHEMA}
    checked = selected.validate_selected_report(payload, b"%PDF-1.4\n%%EOF\n", stock_id="2330", horizon=1,
        expected_runtime_binding=job, require_active=False)
    assert checked["valid"] is False
    assert "selected_report_context" in str(checked["errors"])


@pytest.mark.parametrize("version", [None, "single-stock-report-context-v99"])
def test_unknown_job_context_policy_is_not_legacy(adoption, version):
    payload = selected._formal_payload(candidate_payload(adoption), adoption.binding, audit=True)
    checked = selected.validate_selected_report(payload, b"%PDF-1.4\n%%EOF\n", stock_id="2330", horizon=1,
        expected_runtime_binding={"deployment_binding": adoption.binding, "report_context_schema": version},
        require_active=False)
    assert checked["valid"] is False
    assert "selected_job_report_context_schema_invalid" in str(checked["errors"])


def test_unmarked_legacy_report_still_reproduces_existing_evidence(adoption, monkeypatch, legacy_reader_plan):
    import stock_candidate_report as report
    original = candidate_payload(adoption)
    payload = selected._formal_payload(original, adoption.binding, audit=True)
    payload.pop("report_context")
    payload.pop("report_context_schema")
    payload["runtime_payload_sha256"] = selected._digest(
        {key: value for key, value in payload.items() if key != "runtime_payload_sha256"})
    raw = b"%PDF-1.4\nfixture-legacy-comparison\n%%EOF\n"
    bundle = {"schema": report._BUNDLE_V1, "payload": payload, "payload_sha256": report._digest(payload),
        "sections": [], "sections_sha256": report._digest([]),
        "pdf": {"sha256": hashlib.sha256(raw).hexdigest(), "size": len(raw)}}
    monkeypatch.setattr(report, "build_report_sections", lambda payload: [])
    monkeypatch.setattr(report, "_inspect_structure", lambda *a: {"pages": 1})
    calls = []
    monkeypatch.setattr(selected, "_inference", lambda *a, **k: calls.append((a, k)) or [original])
    result = selected.validate_selected_report(bundle, raw, stock_id="2330", horizon=1, require_active=False)
    assert result["valid"] is True, result["errors"]
    assert result["prediction_reproduced"] is True and len(calls) == 1


@pytest.mark.parametrize("horizons,distinct_asof", [((1,), False), ((1, 63), False), ((1, 63), True)])
def test_inference_context_uses_all_memory_frames_once_per_stock_asof_without_io(
        adoption, monkeypatch, horizons, distinct_asof):
    feature_sha = "f" * 64
    model = {"metadata": {"snapshot_binding": {"head_sha256": adoption.head_hash},
        "runtime_input_contract": {"horizons": [1, 63], "feature_module_sha256": feature_sha,
            "source_plan_by_horizon": {"1": [], "63": []}}},
        "horizons": {"1": {"selected": {}}, "63": {"selected": {}}}}
    frames = {"finmind_per": [{"stock_id": "2330", "value": 12.0}],
        "context_only": [{"stock_id": "2330", "value": 7.0}]}
    manifest = [{"route_id": "finmind_per", "status": "PARTIAL"},
        {"route_id": "context_only", "status": "SUCCESS"}]
    prepared = {"frozen_features": list(range(38)), "comparison_values": list(range(42))}
    plan = selected.runtime._seal_read_plan(selected.runtime._empty_read_plan(
        adoption.binding["cache_dir"], adoption.head_hash))
    loaded = {"prepared": prepared, "source_frames": frames, "source_manifest": manifest,
        "snapshot": {"head_sha256": adoption.head_hash, "version_labels": []}, "read_plan": plan}
    payloads = [candidate_payload(adoption, horizon) for horizon in horizons]
    if distinct_asof:
        payloads[-1]["asof"] = "2026-10-15"
    before = copy.deepcopy((prepared, manifest, frames, payloads, model))
    events, builds, reads = [], [], []
    context = {"current_identity": {"records": [copy.deepcopy(payloads[0]["stock"])]},
        "sources": SimpleNamespace(close=lambda: events.append("close"))}

    def inputs(ids, **kwargs):
        assert ids == ["2330"] and kwargs["context"] is context
        assert kwargs["read_only"] is True and kwargs["persist_cache"] is False
        assert kwargs["allow_full_scan"] is True and kwargs["read_plan"] is plan
        reads.append(kwargs)
        return loaded

    def apply_frozen(actual_prepared, actual_model, actual_horizons, actual_plan):
        assert actual_prepared is prepared and actual_model is model
        assert actual_horizons == horizons and actual_plan == []
        events.append("features")

    def predict(actual_model, path, actual_inputs, actual_horizons, **kwargs):
        assert actual_model is model and actual_inputs is loaded and actual_horizons == horizons
        assert kwargs["expected_model_sha256"] == adoption.model_hash
        assert prepared == before[0] and manifest == before[1]
        events.append("prediction")
        return copy.deepcopy(payloads)

    def build(actual_frames, actual_manifest, *, stock, asof):
        assert actual_frames is frames and actual_manifest is manifest
        assert stock == payloads[0]["stock"] and events[-1] in {"prediction", "report_context"}
        builds.append((stock["stock_id"], asof, loaded["snapshot"]["head_sha256"]))
        events.append("report_context")
        return {"schema": selected.REPORT_CONTEXT_SCHEMA, "stock_id": stock["stock_id"], "asof": asof,
            "sources": copy.deepcopy(actual_manifest), "statuses": {"context_only": actual_frames["context_only"][0]["value"]}}

    def forbidden(*args, **kwargs):
        pytest.fail("report context must not perform filesystem or cache I/O")

    monkeypatch.setattr(selected, "_load_model", lambda binding: (model, adoption.model))
    monkeypatch.setattr(selected, "_file_sha", lambda path: feature_sha)
    monkeypatch.setattr(selected, "load_selected_context", forbidden)
    monkeypatch.setitem(sys.modules, "stock_daily_feature_catalog", SimpleNamespace(__file__="frozen-feature-fixture.py"))
    monkeypatch.setitem(sys.modules, "stock_report_analysis", SimpleNamespace(build_report_context=build))
    monkeypatch.setattr(selected.runtime, "load_candidate_inputs", inputs)
    monkeypatch.setattr(selected.runtime, "_apply_frozen_schema", apply_frozen)
    monkeypatch.setattr(selected.runtime, "_predict_payloads", predict)
    monkeypatch.setattr("builtins.open", forbidden)
    for name in ("open", "read_bytes", "read_text", "write_bytes", "write_text", "mkdir", "unlink"):
        monkeypatch.setattr(Path, name, forbidden)
    first = selected._inference("2330", adoption.binding, horizons, adoption.binding["cache_dir"], adoption.head_hash,
        read_only=True, read_plan=plan, verified_context=context)
    count = len(set(value["asof"] for value in payloads))
    assert len(builds) == count and len(reads) == 1 and "close" not in events
    for actual, original in zip(first, payloads):
        assert {key: value for key, value in actual.items()
            if key not in {"report_context", "storage_policy", "read_plan", "runtime_payload_sha256"}} == {
            key: value for key, value in original.items() if key not in {"report_context", "runtime_payload_sha256"}}
        assert actual["storage_policy"] == selected.READ_ONLY_STORAGE_POLICY and actual["read_plan"] == plan
        assert actual["runtime_payload_sha256"] == selected._digest(
            {key: value for key, value in actual.items() if key != "runtime_payload_sha256"})
        formal = selected._formal_payload(actual, adoption.binding, audit=True)
        assert formal["candidate_runtime_payload_sha256"] == actual["runtime_payload_sha256"]
        assert formal["runtime_payload_sha256"] == selected._digest(
            {key: value for key, value in formal.items() if key != "runtime_payload_sha256"})
    if len(first) == 2 and not distinct_asof:
        assert first[0]["report_context"] == first[1]["report_context"]
        assert first[0]["report_context"] is not first[1]["report_context"]
        first[0]["report_context"]["sources"].append({"caller": "mutation"})
        assert len(first[1]["report_context"]["sources"]) == len(manifest)
    assert (prepared, manifest, frames, payloads, model) == before
    loaded["snapshot"]["head_sha256"] = "a" * 64
    frames["context_only"][0]["value"] = 9.0
    second = selected._inference("2330", adoption.binding, horizons, adoption.binding["cache_dir"], "a" * 64,
        read_only=True, read_plan=plan, verified_context=context)
    assert len(builds) == 2 * count and len(reads) == 2
    assert builds[-1][2] == "a" * 64
    assert second[0]["report_context"]["statuses"]["context_only"] == 9.0
    assert first[0]["report_context"]["statuses"]["context_only"] == 7.0
