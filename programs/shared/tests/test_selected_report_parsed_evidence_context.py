"""Offline selected-validator context tests; no real model, producer, or Telegram job."""

from __future__ import annotations

import copy
import os
import sys
import tempfile
from pathlib import Path
from types import SimpleNamespace

import pytest

SHARED = Path(__file__).resolve().parents[1]
REPORT_SRC = SHARED.parent / "single_stock_analysis" / "src"
for directory in (SHARED, REPORT_SRC):
    if str(directory) not in sys.path:
        sys.path.insert(0, str(directory))

import stock_daily_selected_deployment as selected
import stock_candidate_report as report


@pytest.fixture
def validation_case(tmp_path, monkeypatch):
    """Real compact evidence/binding logic with small local files and inference spies."""
    actual_inference = selected._inference
    monkeypatch.setattr(report, "_converter", lambda: lambda text: text)
    monkeypatch.setattr(report, "_check_output_path", lambda *args: None)
    monkeypatch.setattr(report, "_load_fonts", lambda: SimpleNamespace(check=lambda texts: None))
    monkeypatch.setattr(report, "_inspect_structure", lambda *args: {"pages": 1, "fixture_only": True})
    monkeypatch.setattr(report, "_extract_if_available", lambda *args: {"status": "fixture_only"})
    monkeypatch.setattr(selected.runtime, "_consumer_path", lambda path: Path(path))
    head = "b" * 64
    descriptor = {"objects": [{"sha256": "d" * 64, "size": 123}], "full_evidence": "fixture" * 64}
    model = {"schema": "focused-model-v1", "metadata": {
        "snapshot_binding": {"head_sha256": head, "descriptor": descriptor}},
        "weights": {"direction": [1.25, -0.5], "return": [0.125, 2.0]}}
    model_path = tmp_path / "model.json"
    model_raw = report._json_bytes(model)
    model_path.write_bytes(model_raw)
    model_sha = report.hashlib.sha256(model_raw).hexdigest()
    binding = {"schema": selected.DEPLOYMENT_SCHEMA, "consumer_id": "single",
        "activation_id": "focused-context-activation", "model_path": str(model_path),
        "model_sha256": model_sha, "cache_dir": str(tmp_path / "cache")}
    payload = {"schema": selected.REPORT_SCHEMA, "language": "zh-TW", "research_only": False,
        "promotion": True, "deployment_binding": binding, "deployment_status": "active",
        "stock": {"stock_id": "2330", "stock_name": "Fixture", "market": "TWSE"},
        "horizon_sessions": 1, "inference_horizons": [1, 63], "asof": "2026-10-23",
        "target_session_date": "2026-10-26", "prediction": {"prob_up": 0.6,
            "expected_return": 0.015, "selected_direction_kind": "expanded_linear",
            "selected_return_kind": "expanded_linear", "prediction_status": "formal_estimate"},
        "model_binding": {"path": str(model_path), "sha256": model_sha,
            "schema": model["schema"], "feature_schema": "focused-features-v1"},
        "snapshot_binding": {"head_sha256": head, "snapshot_fingerprint": "c" * 64,
            "descriptor": descriptor}, "feature_values": [], "analysis_context": {"sources": []},
        "metrics": {}, "feature_limitations": [], "limitations": [], "price_history": [],
        "price_volume_unit": "shares", "data_quality_assessment": {}, "source_manifest": []}
    payload["runtime_payload_sha256"] = selected._digest(payload)
    expected = copy.deepcopy(payload)
    stored, original_digest, _ = report._prepare_report_payload(payload, tmp_path)
    sections = report.build_report_sections(payload)
    pdf_path = tmp_path / "report.pdf"
    pdf_raw = b"%PDF-1.4\nOffline context fixture, not a rendered PDF.\n%%EOF\n"
    pdf_path.write_bytes(pdf_raw)
    bundle = {"schema": report._BUNDLE_V2, "payload": stored, "sections": sections,
        "payload_sha256": original_digest, "original_payload_sha256": original_digest,
        "stored_payload_sha256": report._digest(stored),
        "runtime_payload_sha256": payload["runtime_payload_sha256"],
        "sections_sha256": report._digest(sections), "pdf": report._file_info(pdf_path)}
    json_path = pdf_path.with_suffix(".json")
    json_path.write_bytes(report._json_bytes(bundle))
    job = {"schema": "single-stock-selected-runtime-binding-v1", "deployment_binding": binding,
        "head_sha256": head, "snapshot_fingerprint": payload["snapshot_binding"]["snapshot_fingerprint"]}
    observed = SimpleNamespace(parses=0, hashes=[], reads=[], identities=0, scopes=[], documents=[],
        inferences=[], bindings=[], receipts=[], bypass_reuse=False, fail_inference=False,
        change_prediction=False)
    model_inputs = {model_raw}
    active_fds = set()
    original_open, original_fstat = Path.open, report.os.fstat
    original_sha256, original_loads = report.hashlib.sha256, report.json.loads
    original_stable = report._stable_json

    class ModelStream:
        def __init__(self, stream):
            self.stream = stream

        def __enter__(self):
            self.stream.__enter__()
            active_fds.add(self.stream.fileno())
            return self

        def __exit__(self, *args):
            active_fds.discard(self.stream.fileno())
            return self.stream.__exit__(*args)

        def fileno(self):
            return self.stream.fileno()

        def read(self, *args):
            raw = self.stream.read(*args)
            observed.reads.append(raw)
            return raw

    def open_file(path, mode="r", *args, **kwargs):
        stream = original_open(path, mode, *args, **kwargs)
        return ModelStream(stream) if mode == "rb" and Path(path).resolve() == model_path.resolve() else stream

    def fstat(fd):
        if fd in active_fds:
            observed.identities += 1
        return original_fstat(fd)

    def sha256(raw=b"", *args, **kwargs):
        if type(raw) is bytes and raw in model_inputs:
            observed.hashes.append(raw)
        return original_sha256(raw, *args, **kwargs)

    def loads(raw, *args, **kwargs):
        if kwargs.get("parse_constant") is report._reject_json_constant:
            observed.parses += 1
        return original_loads(raw, *args, **kwargs)

    def stable(path, **kwargs):
        observed.scopes.append(report._PARSED_EVIDENCE.get())
        token = report._PARSED_EVIDENCE.set(None) if observed.bypass_reuse else None
        try:
            result = original_stable(path, **kwargs)
        finally:
            if token is not None:
                report._PARSED_EVIDENCE.reset(token)
        observed.documents.append(result[0])
        return result

    def validate_binding(value):
        assert value == binding
        observed.bindings.append(copy.deepcopy(value))
        return copy.deepcopy(value)

    def validate_receipt(value):
        assert value == binding
        observed.receipts.append(copy.deepcopy(value))

    def inference(stock, current_binding, horizons, cache, pinned_head, *, read_only, read_plan):
        assert read_only is True and read_plan["legacy"] is True
        observed.inferences.append((stock, copy.deepcopy(current_binding), horizons, str(cache), pinned_head))
        if observed.fail_inference:
            raise RuntimeError("fixture inference failure")
        reproduced = copy.deepcopy(expected)
        if observed.change_prediction:
            reproduced["prediction"]["prob_up"] = 0.9
        return [reproduced]

    monkeypatch.setattr(Path, "open", open_file)
    monkeypatch.setattr(report.os, "fstat", fstat)
    monkeypatch.setattr(report.hashlib, "sha256", sha256)
    monkeypatch.setattr(report.json, "loads", loads)
    monkeypatch.setattr(report, "_stable_json", stable)
    monkeypatch.setattr(selected, "_validate_binding", validate_binding)
    monkeypatch.setattr(selected, "_validate_receipt", validate_receipt)
    monkeypatch.setattr(selected, "_inference", inference)

    def validate(**kwargs):
        arguments = {"payload_or_bundle": bundle, "pdf_bytes": pdf_raw, "stock_id": "2330",
            "horizon": 1, "expected_runtime_binding": job, "pdf_path": pdf_path, "require_active": True}
        arguments.update(kwargs)
        return selected.validate_selected_report(**arguments)

    def reset_counts():
        observed.parses = observed.identities = 0
        for field in ("hashes", "reads", "scopes", "documents", "inferences", "bindings", "receipts"):
            getattr(observed, field).clear()

    return SimpleNamespace(validate=validate, observed=observed, reset_counts=reset_counts,
        payload=payload, bundle=bundle, binding=binding, job=job, expected=expected,
        model_path=model_path, model_raw=model_raw, model_inputs=model_inputs,
        pdf_path=pdf_path, pdf_raw=pdf_raw, json_path=json_path, actual_inference=actual_inference)


@pytest.fixture
def published_validation_case(validation_case, monkeypatch):
    """Real resolver and inference entrypoints; only frozen domain computation is stubbed."""
    import stock_report_analysis as analysis
    case = validation_case
    with tempfile.TemporaryDirectory(prefix="selected-context-borrow-") as directory:
        root = Path(directory) / "published"
        (root / "_manifests").mkdir(parents=True)
        raw = b'{"schema":"stock-data-publication-v2","fixture":1}\n'
        head = report.hashlib.sha256(raw).hexdigest()
        head_path = root / "_manifests" / (head + ".json")
        head_path.write_bytes(raw)
        fingerprint = report._digest({"head": head})
        source = {"source_id": "shared_market_data", "kind": "manifest_json", "root": str(root),
            "snapshot_id": head, "manifest_path": str(head_path), "manifest_sha256": head,
            "publication_schema": "stock-data-publication-v2"}
        descriptor = {"schema": "stock-data-published-inputs-v1", "snapshot_fingerprint": fingerprint,
            "sources": [source], "pinned_sources": [{"source_id": source["source_id"], "kind": source["kind"],
                "root": str(root), "snapshot_id": head, "identity": {key: source[key]
                    for key in ("manifest_path", "manifest_sha256", "publication_schema")}}]}
        descriptor["descriptor_sha256"] = report._digest(descriptor)
        feature_path = Path(directory) / "feature-source.py"
        feature_path.write_bytes(b"# frozen feature fixture\n")
        frozen = {"metadata": {"snapshot_binding": {"head_sha256": head}, "runtime_input_contract": {
                "horizons": [1, 63], "feature_module_sha256": selected._file_sha(feature_path),
                "source_plan_by_horizon": {"1": [], "63": []}}},
            "horizons": {"1": {"selected": {}}, "63": {"selected": {}}}}
        state = SimpleNamespace(loads=[], closes=[], inputs=[], events=[], report_builds=[], models=0, features=0,
            predictions=0, failure=None)
        source_frames = {"report_only": [{"stock_id": "2330", "value": 7.0}]}

        def build_report_context(frames, manifest, *, stock, asof):
            assert frames is source_frames and manifest == []
            assert stock == case.payload["stock"] and asof == case.payload["asof"]
            assert state.events[-1][0] == "prediction"
            state.report_builds.append((state.inputs[-1], frames, asof))
            return {"schema": selected.REPORT_CONTEXT_SCHEMA, "stock_id": stock["stock_id"], "asof": asof,
                "sources": [], "statuses": {"memory_value": frames["report_only"][0]["value"]}}

        def runtime_payload(value):
            result = {key: copy.deepcopy(item) for key, item in value.items() if key not in {
                "deployment_binding", "deployment_status", "candidate_runtime_payload_sha256",
                "report_context_schema", "runtime_payload_sha256"}}
            result.update(schema=selected.runtime.REPORT_SCHEMA, research_only=True, promotion=False)
            return result

        class Sources:
            def __init__(self):
                self.closed = False

            def snapshot_descriptor(self):
                assert not self.closed
                return copy.deepcopy(descriptor)

            def close(self):
                assert not self.closed, "operation must release each context once"
                self.closed = True
                state.closes.append(self)
                state.events.append(("close", self))

        def load_context(pinned, binding):
            assert pinned == head and binding == case.binding
            context = {"sources": Sources(), "current_identity": {"records": [copy.deepcopy(case.payload["stock"])]}}
            state.loads.append(context)
            state.events.append(("load", context))
            return context

        def load_model(binding):
            state.models += 1
            assert binding == case.binding
            assert selected._file_sha(case.model_path) == binding["model_sha256"]
            if state.failure == "model":
                raise RuntimeError("fixture model failure")
            model = copy.deepcopy(frozen)
            if state.failure == "feature_code":
                model["metadata"]["runtime_input_contract"]["feature_module_sha256"] = "0" * 64
            return model, case.model_path

        def inputs(ids, **kwargs):
            context = kwargs["context"]
            assert ids == ["2330"] and any(context is loaded for loaded in state.loads)
            assert not context["sources"].closed
            assert kwargs["consumer_id"] == "single" and kwargs["horizons"] == [1, 63]
            assert kwargs["read_only"] is True and kwargs["persist_cache"] is False and kwargs["allow_full_scan"] is True
            assert kwargs["read_plan"] == case.payload["read_plan"]
            state.inputs.append(context)
            state.events.append(("inputs", context))
            if state.failure == "inputs":
                raise RuntimeError("fixture inputs failure")
            return {"prepared": {}, "source_frames": source_frames, "source_manifest": [],
                "read_plan": copy.deepcopy(kwargs["read_plan"]),
                "snapshot": {"head_sha256": head, "version_labels": []}}

        def apply_frozen(prepared, model, horizons, plan):
            assert model == frozen and horizons == (1, 63) and plan == []
            assert not state.inputs[-1]["sources"].closed
            state.features += 1

        def predict(model, path, loaded, horizons, **kwargs):
            assert model == frozen and path == case.model_path and horizons == (1, 63)
            assert kwargs["expected_model_sha256"] == case.binding["model_sha256"]
            assert not state.inputs[-1]["sources"].closed
            state.predictions += 1
            state.events.append(("prediction", state.inputs[-1]))
            if state.failure == "prediction":
                raise RuntimeError("fixture prediction failure")
            return [runtime_payload(case.expected)]

        monkeypatch.setattr(report, "_PUBLISHED_ROOT", root)
        monkeypatch.setattr(selected.runtime, "PUBLISHED_ROOT", root)
        monkeypatch.setattr(selected.contract, "c0_policy_binding", lambda **kwargs:
            {"schema": "focused-policy-v1", "consumer_id": kwargs["consumer_id"]})
        monkeypatch.setattr(selected, "load_selected_context", load_context)
        monkeypatch.setattr(selected, "_load_model", load_model)
        monkeypatch.setattr(selected, "_inference", case.actual_inference)
        monkeypatch.setitem(sys.modules, "stock_daily_feature_catalog", SimpleNamespace(__file__=str(feature_path)))
        monkeypatch.setattr(analysis, "build_report_context", build_report_context)
        monkeypatch.setattr(selected.runtime, "load_candidate_inputs", inputs)
        monkeypatch.setattr(selected.runtime, "_apply_frozen_schema", apply_frozen)
        monkeypatch.setattr(selected.runtime, "_predict_payloads", predict)
        case.payload["snapshot_binding"].update(head_sha256=head, snapshot_fingerprint=fingerprint, descriptor=descriptor)
        case.payload["storage_policy"] = selected.READ_ONLY_STORAGE_POLICY
        case.payload["read_plan"] = selected.runtime._seal_read_plan(
            selected.runtime._empty_read_plan(case.binding["cache_dir"], head))
        case.payload["report_context_schema"] = selected.REPORT_CONTEXT_SCHEMA
        case.payload["report_context"] = {"schema": selected.REPORT_CONTEXT_SCHEMA, "stock_id": "2330",
            "asof": case.payload["asof"], "sources": [], "statuses": {"memory_value": 7.0}}
        case.payload["candidate_runtime_payload_sha256"] = selected._digest(runtime_payload(case.payload))
        case.payload["runtime_payload_sha256"] = selected._digest(
            {key: value for key, value in case.payload.items() if key != "runtime_payload_sha256"})
        case.expected.clear()
        case.expected.update(copy.deepcopy(case.payload))
        case.job.update(head_sha256=head, snapshot_fingerprint=fingerprint, storage_policy=selected.READ_ONLY_STORAGE_POLICY,
            report_context_schema=selected.REPORT_CONTEXT_SCHEMA)
        stored, original_digest, reference = report._prepare_report_payload(case.payload, case.pdf_path.parent)
        assert reference["kind"] == "published_snapshot_descriptor"
        sections = report.build_report_sections(case.payload)
        case.bundle.clear()
        case.bundle.update(schema=report._BUNDLE_V2, payload=stored, sections=sections,
            payload_sha256=original_digest, original_payload_sha256=original_digest,
            stored_payload_sha256=report._digest(stored), runtime_payload_sha256=case.payload["runtime_payload_sha256"],
            sections_sha256=report._digest(sections), pdf=report._file_info(case.pdf_path))
        case.json_path.write_bytes(report._json_bytes(case.bundle))
        assert all(context["sources"].closed for context in state.loads)

        def reset_counts():
            for field in ("loads", "closes", "inputs", "events", "report_builds"):
                getattr(state, field).clear()
            state.models = state.features = state.predictions = 0

        reset_counts()
        yield SimpleNamespace(case=case, state=state, head=head, head_path=head_path, head_raw=raw,
            reset_counts=reset_counts)


def assert_model_work(case, *, parses, reads, inferences):
    observed = case.observed
    assert observed.parses == parses
    assert len(observed.reads) == len(observed.hashes) == reads
    assert observed.reads == observed.hashes
    assert observed.identities == reads * 4
    assert len(observed.inferences) == inferences
    assert report._PARSED_EVIDENCE.get() is None
    assert all(scope == {} for scope in observed.scopes if scope is not None)


def record_work(record_property, prefix, observed):
    for name, value in (("parses", observed.parses), ("full_reads", len(observed.reads)),
            ("full_hashes", len(observed.hashes)), ("handle_identity_checks", observed.identities),
            ("independent_inference_calls", len(observed.inferences))):
        record_property(prefix + "_" + name, value)


@pytest.mark.parametrize("with_pdf_path", [True, False])
def test_selected_nested_context_reuses_parse_without_reducing_hash_or_inference(validation_case, with_pdf_path, record_property):
    case = validation_case
    before = tuple(report._json_bytes(value) for value in (case.payload, case.bundle, case.expected))
    original_documents = (case.pdf_path.read_bytes(), case.json_path.read_bytes())
    case.observed.bypass_reuse = True
    arguments = {} if with_pdf_path else {"pdf_path": None}
    baseline = case.validate(**arguments)
    assert baseline["valid"] is True, baseline.get("errors")
    assert_model_work(case, parses=2, reads=2, inferences=1)
    record_work(record_property, "baseline", case.observed)
    baseline_inferences = copy.deepcopy(case.observed.inferences)
    case.reset_counts()
    case.observed.bypass_reuse = False
    optimized = case.validate(**arguments)
    assert optimized == baseline
    assert_model_work(case, parses=1, reads=2, inferences=1)
    record_work(record_property, "optimized", case.observed)
    assert case.observed.inferences == baseline_inferences
    assert len(case.observed.bindings) == len(case.observed.receipts) == 1
    assert case.observed.scopes[0] is case.observed.scopes[1]
    assert case.observed.documents[0] is case.observed.documents[1]
    assert report._json_bytes(case.observed.documents[0]) == case.model_raw
    assert tuple(report._json_bytes(value) for value in (case.payload, case.bundle, case.expected)) == before
    assert (case.pdf_path.read_bytes(), case.json_path.read_bytes()) == original_documents
    assert case.model_path.read_bytes() == case.model_raw


def test_two_selected_operations_never_share_parsed_objects(validation_case, record_property):
    case = validation_case
    first, second = case.validate(), case.validate()
    assert first == second and first["valid"] is True
    assert_model_work(case, parses=2, reads=4, inferences=2)
    record_work(record_property, "two_operations", case.observed)
    assert case.observed.documents[0] is case.observed.documents[1]
    assert case.observed.documents[2] is case.observed.documents[3]
    assert case.observed.documents[0] is not case.observed.documents[2]
    assert case.observed.scopes[0] is not case.observed.scopes[2]


def test_selected_operation_does_not_borrow_an_ambient_producer_context(validation_case):
    case = validation_case
    with report._parsed_evidence_operation():
        producer_scope = report._PARSED_EVIDENCE.get()
        producer, _ = report._stable_json(case.model_path)
        assert case.validate()["valid"] is True
        assert report._PARSED_EVIDENCE.get() is producer_scope and len(producer_scope) == 1
        assert case.observed.documents[1] is case.observed.documents[2]
        assert case.observed.documents[1] is not producer
    assert_model_work(case, parses=2, reads=3, inferences=1)


@pytest.mark.parametrize("field,code", [
    ("sha256", "evidence_sha256_mismatch"),
    ("size", "evidence_size_mismatch"),
    ("schema", "evidence_schema_mismatch"),
])
def test_nested_warm_context_keeps_each_expected_evidence_check(validation_case, field, code):
    case = validation_case
    expected = {"sha256": case.binding["model_sha256"], "size": len(case.model_raw), "schema": "focused-model-v1"}
    with report._parsed_evidence_operation():
        report._stable_json(case.model_path, **expected)
        expected[field] = {"sha256": "0" * 64, "size": len(case.model_raw) + 1, "schema": "wrong-schema"}[field]
        with report._parsed_evidence_operation(reuse=True):
            with pytest.raises(report.CandidateReportError) as caught:
                report._stable_json(case.model_path, **expected)
        assert caught.value.code == code
    assert_model_work(case, parses=1, reads=2, inferences=0)


def test_nested_structural_validation_rejects_same_size_mtime_model_tamper(validation_case, monkeypatch):
    case = validation_case
    actual_validate = report.validate_candidate_report
    before = case.model_path.stat()
    changed = case.model_raw.replace(b"1.25", b"9.25", 1)
    assert len(changed) == len(case.model_raw) and changed != case.model_raw
    case.model_inputs.add(changed)
    structural = []

    def tamper_then_validate(*args, **kwargs):
        case.model_path.write_bytes(changed)
        os.utime(case.model_path, ns=(before.st_atime_ns, before.st_mtime_ns))
        assert case.model_path.stat().st_size == before.st_size
        assert case.model_path.stat().st_mtime_ns == before.st_mtime_ns
        result = actual_validate(*args, **kwargs)
        structural.append(result)
        return result

    monkeypatch.setattr(report, "validate_candidate_report", tamper_then_validate)
    failed = case.validate()
    assert failed["valid"] is False
    assert "selected_pdf_or_sidecar_validation_failed" in str(failed["errors"])
    assert structural[0]["errors"][0]["code"] == "evidence_sha256_mismatch"
    assert_model_work(case, parses=1, reads=2, inferences=0)
    case.model_path.write_bytes(case.model_raw)
    os.utime(case.model_path, ns=(before.st_atime_ns, before.st_mtime_ns))
    monkeypatch.setattr(report, "validate_candidate_report", actual_validate)
    assert case.validate()["valid"] is True
    assert_model_work(case, parses=2, reads=4, inferences=1)


def test_inference_exception_clears_context_and_next_operation_reproduces_again(validation_case):
    case = validation_case
    case.observed.fail_inference = True
    failed = case.validate()
    assert failed["valid"] is False and failed["errors"][0] == {
        "code": "RuntimeError", "message": "fixture inference failure"}
    assert_model_work(case, parses=1, reads=2, inferences=1)
    case.observed.fail_inference = False
    assert case.validate()["valid"] is True
    assert_model_work(case, parses=2, reads=4, inferences=2)
    assert case.observed.documents[0] is not case.observed.documents[2]


@pytest.mark.parametrize("pdf_bytes", [b"not-pdf\n%%EOF", b"%PDF-1.4\nmissing end", b"%PDF-1.4\n%%EOF\ntrailing"])
def test_pdf_start_and_end_checks_still_reject_each_invalid_operation(validation_case, pdf_bytes):
    case = validation_case
    failed = case.validate(pdf_bytes=pdf_bytes)
    assert failed["valid"] is False and "selected_pdf_incomplete" in str(failed["errors"])
    assert_model_work(case, parses=1, reads=1, inferences=0)
    assert case.validate()["valid"] is True
    assert_model_work(case, parses=2, reads=3, inferences=1)


def test_independent_reproduction_still_rejects_a_different_prediction(validation_case):
    case = validation_case
    before = report._json_bytes(case.bundle)
    case.observed.change_prediction = True
    failed = case.validate()
    assert failed["valid"] is False
    assert "selected_prediction_or_evidence_mismatch:prediction" in str(failed["errors"])
    assert_model_work(case, parses=1, reads=2, inferences=1)
    assert report._json_bytes(case.bundle) == before and case.model_path.read_bytes() == case.model_raw


def test_selected_import_failure_preserves_invalid_result_and_parent_context(validation_case, monkeypatch):
    actual_import = selected.importlib.import_module

    def unavailable(name, *args, **kwargs):
        if name == "stock_candidate_report":
            raise ImportError("fixture report import failure")
        return actual_import(name, *args, **kwargs)

    monkeypatch.setattr(selected.importlib, "import_module", unavailable)
    with report._parsed_evidence_operation():
        parent = report._PARSED_EVIDENCE.get()
        result = validation_case.validate()
        assert result["valid"] is False and result["errors"][0] == {
            "code": "ImportError", "message": "fixture report import failure"}
        assert report._PARSED_EVIDENCE.get() is parent
    assert_model_work(validation_case, parses=0, reads=0, inferences=0)


def test_validator_borrows_one_context_without_skipping_independent_inference(published_validation_case, monkeypatch):
    fixture = published_validation_case
    case, state = fixture.case, fixture.state
    inference = selected._inference
    documents = tuple(path.read_bytes() for path in (case.model_path, case.pdf_path, case.json_path, fixture.head_path))

    def without_borrow(*args, verified_context=None, **kwargs):
        assert verified_context is not None
        return inference(*args, **kwargs)

    monkeypatch.setattr(selected, "_inference", without_borrow)
    baseline = case.validate()
    assert baseline["valid"] is True, baseline.get("errors")
    assert len(state.loads) == len(state.closes) == 2
    assert state.models == state.features == state.predictions == len(state.inputs) == 1
    old_contexts = tuple(state.loads)
    assert all(context["sources"].closed for context in old_contexts)
    fixture.reset_counts()
    monkeypatch.setattr(selected, "_inference", inference)
    optimized = case.validate()
    assert optimized == baseline and optimized["prediction_reproduced"] is True
    assert len(state.loads) == len(state.closes) == 1
    assert state.models == state.features == state.predictions == len(state.inputs) == 1
    assert len(state.report_builds) == 1 and state.report_builds[0][0] is state.inputs[0]
    assert state.inputs[0] is state.loads[0]
    assert all(state.loads[0] is not old for old in old_contexts)
    assert state.events[-1] == ("close", state.loads[0]["sources"])
    assert report._borrow_selected_context(fixture.head, case.binding) is None
    assert len(state.loads) == 1 and report._PARSED_EVIDENCE.get() is None
    assert tuple(path.read_bytes() for path in (case.model_path, case.pdf_path, case.json_path, fixture.head_path)) == documents


@pytest.mark.parametrize("failure", ["model", "feature_code", "inputs", "prediction"])
def test_borrowed_context_exception_is_released_and_next_operation_reloads(published_validation_case, failure):
    fixture = published_validation_case
    case, state = fixture.case, fixture.state
    state.failure = failure
    failed = case.validate()
    assert failed["valid"] is False and failed["prediction_reproduced"] is False
    message = "selected_frozen_feature_code_changed" if failure == "feature_code" else "fixture " + failure + " failure"
    assert message in str(failed["errors"])
    assert len(state.loads) == len(state.closes) == state.models == 1
    first_context = state.loads[0]
    assert first_context["sources"].closed and report._PARSED_EVIDENCE.get() is None
    assert report._borrow_selected_context(fixture.head, case.binding) is None
    state.failure = None
    passed = case.validate()
    assert passed["valid"] is True, passed.get("errors")
    assert len(state.loads) == len(state.closes) == state.models == 2
    assert state.loads[1] is not first_context and all(context["sources"].closed for context in state.loads)
    assert report._borrow_selected_context(fixture.head, case.binding) is None


def test_validator_does_not_borrow_the_ambient_producer_context(published_validation_case):
    fixture = published_validation_case
    case, state = fixture.case, fixture.state
    with report._parsed_evidence_operation():
        stored = case.bundle["payload"]
        reference = stored["snapshot_binding"]["descriptor_reference"]
        report._resolve_descriptor_reference(reference, stored, case.pdf_path)
        producer = report._borrow_selected_context(fixture.head, case.binding)
        assert producer is state.loads[0] and not producer["sources"].closed
        result = case.validate()
        assert result["valid"] is True, result.get("errors")
        assert len(state.loads) == 2 and len(state.closes) == 1
        assert state.inputs[0] is state.loads[1] and state.inputs[0] is not producer
        assert state.loads[1]["sources"].closed and not producer["sources"].closed
        assert report._borrow_selected_context(fixture.head, case.binding) is producer
    assert len(state.closes) == 2 and all(context["sources"].closed for context in state.loads)
    assert report._borrow_selected_context(fixture.head, case.binding) is None


def test_validator_borrow_rejects_head_tamper_before_inference(published_validation_case, monkeypatch):
    fixture = published_validation_case
    case, state = fixture.case, fixture.state
    borrow = report._borrow_selected_context
    structural_validation = report.validate_candidate_report
    phase = {"structural_complete": False}
    stamp = fixture.head_path.stat()
    changed = fixture.head_raw.replace(b'"fixture":1', b'"fixture":2')
    assert changed != fixture.head_raw and len(changed) == len(fixture.head_raw)

    def structural_then_ready(*args, **kwargs):
        result = structural_validation(*args, **kwargs)
        phase["structural_complete"] = True
        return result

    def tamper_then_borrow(head, binding):
        if not phase["structural_complete"]:
            return borrow(head, binding)
        assert len(state.loads) == 1 and not state.loads[0]["sources"].closed
        fixture.head_path.write_bytes(changed)
        os.utime(fixture.head_path, ns=(stamp.st_atime_ns, stamp.st_mtime_ns))
        assert fixture.head_path.stat().st_size == stamp.st_size
        assert fixture.head_path.stat().st_mtime_ns == stamp.st_mtime_ns
        return borrow(head, binding)

    monkeypatch.setattr(report, "validate_candidate_report", structural_then_ready)
    monkeypatch.setattr(report, "_borrow_selected_context", tamper_then_borrow)
    failed = case.validate()
    assert failed["valid"] is False
    assert failed["errors"] == [{"code": "CandidateReportError", "message": "完整證據檔雜湊與綁定不符。"}]
    assert len(state.loads) == len(state.closes) == 1
    assert state.models == state.features == state.predictions == len(state.inputs) == 0
    assert report._PARSED_EVIDENCE.get() is None
    fixture.head_path.write_bytes(fixture.head_raw)
    os.utime(fixture.head_path, ns=(stamp.st_atime_ns, stamp.st_mtime_ns))
    monkeypatch.setattr(report, "_borrow_selected_context", borrow)
    assert case.validate()["valid"] is True
    assert len(state.loads) == len(state.closes) == 2 and state.loads[0] is not state.loads[1]


def test_legacy_compact_report_without_context_keeps_independent_validation(validation_case):
    case = validation_case
    assert "report_context" not in case.payload and "report_context_schema" not in case.job
    result = case.validate()
    assert result["valid"] is True and result["prediction_reproduced"] is True
    assert len(case.observed.inferences) == 1


def test_new_context_is_rebuilt_for_each_validator_operation(published_validation_case):
    fixture = published_validation_case
    case, state = fixture.case, fixture.state
    before = tuple(path.read_bytes() for path in (case.pdf_path, case.json_path, case.model_path, fixture.head_path))
    for _ in range(2):
        result = case.validate()
        assert result["valid"] is True, result.get("errors")
        assert result["prediction_reproduced"] is True
    assert len(state.report_builds) == state.predictions == len(state.inputs) == 2
    assert state.report_builds[0][0] is not state.report_builds[1][0]
    assert len(state.loads) == len(state.closes) == 2
    assert all(context["sources"].closed for context in state.loads)
    assert before == tuple(path.read_bytes() for path in (case.pdf_path, case.json_path, case.model_path, fixture.head_path))


def test_resealed_context_tamper_is_rejected_by_pinned_memory_rebuild(published_validation_case):
    fixture = published_validation_case
    case, state = fixture.case, fixture.state
    case.payload["report_context"]["statuses"]["memory_value"] = 99.0
    case.payload["runtime_payload_sha256"] = selected._digest(
        {key: value for key, value in case.payload.items() if key != "runtime_payload_sha256"})
    stored, original_digest, _ = report._prepare_report_payload(case.payload, case.pdf_path.parent)
    sections = report.build_report_sections(case.payload)
    case.bundle.update(payload=stored, sections=sections, payload_sha256=original_digest,
        original_payload_sha256=original_digest, stored_payload_sha256=report._digest(stored),
        runtime_payload_sha256=case.payload["runtime_payload_sha256"], sections_sha256=report._digest(sections))
    case.json_path.write_bytes(report._json_bytes(case.bundle))
    fixture.reset_counts()
    result = case.validate()
    assert result["valid"] is False and result["prediction_reproduced"] is False
    assert "selected_prediction_or_evidence_mismatch:report_context" in str(result["errors"])
    assert len(state.report_builds) == state.predictions == len(state.inputs) == 1
    assert len(state.loads) == len(state.closes) == 1
    assert case.expected["report_context"]["statuses"]["memory_value"] == 7.0
