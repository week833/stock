"""Selected build isolation and native pin checks; run only in delegated QA."""
from __future__ import annotations

import copy
import datetime
import hashlib
import json
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "single_stock_analysis" / "src"))
import stock_daily_contract as contract
import stock_data_sources as published


def _write_json(root, relative, value):
    raw = json.dumps(value, ensure_ascii=True, sort_keys=True, separators=(",", ":")).encode("utf-8")
    digest = hashlib.sha256(raw).hexdigest()
    relative = (Path(relative).parent / (digest + ".json")).as_posix()
    path = root / relative
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(raw)
    return {"ref": relative, "sha256": digest, "size": len(raw)}


@pytest.fixture
def case(tmp_path, monkeypatch):
    day = "2026-10-16"
    observed = "2026-10-17T00:00:00+00:00"
    url = "https://isin.twse.com.tw/isin/C_public.jsp?strMode=2"
    raw_ref = _write_json(tmp_path, "_identity/raw/authority.json", {"fixture": "official-source"})
    receipt = {"schema": "twstock-updater.current-ordinary-source-receipt.v1", "schema_version": 1,
        "market": "TWSE", "http_status": 200, "source_url": url, "observed_at": observed,
        "observed_date": day, "raw": raw_ref, "classification_schema": "tw-stock-current-ordinary-identity-v1"}
    receipt_ref = _write_json(tmp_path, "_identity/receipts/authority.json", receipt)
    record = {"stock_id": "2330", "market": "TWSE", "stock_name": "fixture",
        "board": "TWSE", "is_ordinary_share": True, "security_type": "ordinary_share",
        "current_ordinary_classification_verified": True, "source_security_section": "\u80a1\u7968",
        "source_cfi": "ESVUFR", "source_sha256": raw_ref["sha256"],
        "classification_effective_date": None, "historical_classification_verified": False}
    identity = {"schema": "tw-stock-current-ordinary-identity-v1", "schema_version": 1,
        "source_asof": day, "classification_verified_asof": day, "classification_observed_date": day,
        "classification_observed_at": observed, "classification_scope": "current_inference_only",
        "classification_effective_date": None, "historical_classification_verified": False,
        "historical_universe_verified": False, "record_count": 1, "records": [record],
        "known_excluded_records": [], "sources": [{"board": "TWSE", "source_url": url,
            "raw_ref": raw_ref, "raw_sha256": raw_ref["sha256"], "raw_size": raw_ref["size"],
            "http_receipt_ref": receipt_ref, "http_status": 200, "observed_at": observed, "source_asof": day}]}
    scope_ref = _write_json(tmp_path, "_scope/retention.json", {"fixture": "retention"})
    capture_ref = _write_json(tmp_path, "_scope/capture.json", {"fixture": "capture"})
    calendar_ref = _write_json(tmp_path, "_calendar/verified/calendar.json", {"fixture": "invalid-proof"})
    calendar_ref.update(schema="twstock-updater.verified-market-calendar.v2", schema_version=2)
    native_scope = contract._load_application_scope
    native_calendar = contract._published_calendar_axes_from_sources
    state = SimpleNamespace(root=tmp_path, day=day, identity=identity, raw_ref=raw_ref,
        receipt=receipt, receipt_ref=receipt_ref, scope_ref=scope_ref, calendar_ref=calendar_ref,
        native_scope=native_scope, native_calendar=native_calendar, calendar_hook=None)

    def publish():
        reference = _write_json(tmp_path, "_identity/current_ordinary/identity.json", state.identity)
        reference.update(schema=state.identity["schema"], historical=False, effective_date=None,
            source_asof=day, verified_asof=day, observed_date=day)
        state.identity_ref = reference
        metadata = {"application_retention_scope": scope_ref, "application_capture_scope": capture_ref,
            "current_ordinary_identity": reference, "verified_market_calendar": calendar_ref,
            "actual_max_date": day}
        document = {"schema": "stock-data-publication-v1", "producer_id": "fixture",
            "run_id": "selected-context-fixture", "published_at": observed, "metadata": metadata,
            "datasets": [{"route_id": route, "path": "data/" + route + ".json", "sha256": "a" * 64,
                "size": 2, "status": "SUCCESS", "metadata": copy.deepcopy(metadata)}
                for route in contract._REQUIRED_CORE_ROUTES]}
        raw = contract._canonical(document)
        digest = hashlib.sha256(raw).hexdigest()
        archive = tmp_path / "_manifests" / (digest + ".json")
        archive.parent.mkdir(parents=True, exist_ok=True)
        archive.write_bytes(raw)
        current = tmp_path / "_metadata" / "stock_data_current.json"
        current.parent.mkdir(parents=True, exist_ok=True)
        current.write_bytes(raw)
        state.archive, state.current, state.head = archive, current, digest
        state.reference = {"root": str(tmp_path), "manifest_path": str(archive), "manifest_sha256": digest}

    state.publish = publish
    publish()
    state.scope = {"root": str(tmp_path), "binding": dict(scope_ref), "capture_binding": dict(capture_ref),
        "body": {"scope_id": "fixture-scope", "capture_scope_id": "fixture-capture", "asof": day,
            "ordinary_universe_ref": {"sha256": state.identity_ref["sha256"]},
            "allowed_stock_market_pairs": [{"stock_id": "2330", "market": "TWSE"}],
            "markets": {"TWSE": {"historical_sessions": ["2026-10-15", day], "future_sessions": []}}}}

    def scope(sources, catalog, active, drift=None, *, selected_single_build=False):
        # Only policy/scope fixture assembly is stubbed; PublishedSources validation is native.
        descriptor = sources.descriptor() if selected_single_build else sources.snapshot_descriptor()
        assert descriptor["sources"][0]["manifest_sha256"] == state.head
        result = copy.deepcopy(state.scope)
        result["body"]["ordinary_universe_ref"]["sha256"] = state.identity_ref["sha256"]
        return result

    def calendar(sources, start=None, end=None, *, application_scope=None, drift=None,
                 snapshot_fingerprint=None):
        if state.calendar_hook is not None:
            state.calendar_hook(sources)
        if snapshot_fingerprint is None:
            snapshot_fingerprint = sources.snapshot_descriptor()["snapshot_fingerprint"]
        return {"TWSE": ["2026-10-15", day]}, {"calendar_verified_by_market": {"TWSE": True},
            "verified_asof": day, "binding": {"snapshot_fingerprint": snapshot_fingerprint,
                "physical_refs": [copy.deepcopy(calendar_ref)]}}

    policy = SimpleNamespace(requirements_for=lambda *a, **k: {"required_routes": list(contract._REQUIRED_CORE_ROUTES)},
        bind_snapshot=lambda policy, profile, descriptor: descriptor,
        assess_readiness=lambda *a: {"status": "fixture_only"})
    monkeypatch.setattr(contract, "load_daily_policy", lambda: (policy, {}))
    monkeypatch.setattr(contract, "_load_application_scope", scope)
    monkeypatch.setattr(contract, "_published_calendar_axes_from_sources", calendar)
    monkeypatch.setattr(contract, "load_active_c0_model", lambda **k: pytest.fail("must not load shared C0"))
    real_datetime = datetime.datetime

    class FrozenDatetime(real_datetime):
        @classmethod
        def now(cls, tz=None):
            value = cls(2026, 10, 17, 8, tzinfo=datetime.timezone.utc)
            return value.astimezone(tz) if tz is not None else value.replace(tzinfo=None)

    monkeypatch.setattr(datetime, "datetime", FrozenDatetime)
    state.active = {key: None for key in contract._DUAL_BINDING_KEYS}
    state.active.update(policy_binding={}, current_policy_binding={}, contract_adoption_verified=False,
        contract_adoption_status="not_applicable_selected_model", artifact_binding={"sha256": "b" * 64})
    return state


def _load(case, **kwargs):
    return contract.load_c0_published_context(case.reference, consumer_id="single",
        runtime_binding=case.active, **kwargs)


def test_selected_build_matches_complete_default_context_and_snapshot(case):
    baseline = _load(case)
    selected = _load(case, selected_single_build=True)
    assert baseline["sources"].snapshot_descriptor() == selected["sources"].snapshot_descriptor()
    assert {key: value for key, value in baseline.items() if key != "sources"} == {
        key: value for key, value in selected.items() if key != "sources"}
    assert contract.classify_current_ordinary(selected["current_identity"], "2330", "TWSE")["eligible"] is True
    assert contract.classify_current_ordinary(selected["current_identity"], "0050", "TWSE")["eligible"] is False


def test_selected_build_uses_two_native_checks_and_two_snapshots(case, monkeypatch):
    calls = {"snapshot": 0, "restore": 0, "proofs": [], "readers": []}
    original_snapshot = published.PublishedSources.snapshot_descriptor
    original_restore = published.PublishedSources.from_descriptor
    original_validate = published.PublishedSources.validate_pinned_context

    def snapshot(self, *args, **kwargs):
        calls["snapshot"] += 1
        return original_snapshot(self, *args, **kwargs)

    def restore(cls, *args, **kwargs):
        calls["restore"] += 1
        return original_restore(*args, **kwargs)

    def validate(self, *args, **kwargs):
        proof = original_validate(self, *args, **kwargs)
        calls["readers"].append(id(self))
        calls["proofs"].append(proof)
        return proof

    monkeypatch.setattr(published.PublishedSources, "snapshot_descriptor", snapshot)
    monkeypatch.setattr(published.PublishedSources, "from_descriptor", classmethod(restore))
    monkeypatch.setattr(published.PublishedSources, "validate_pinned_context", validate)
    selected = _load(case, selected_single_build=True)
    assert calls["snapshot"] == calls["restore"] == len(calls["proofs"]) == 2
    assert calls["readers"] == [id(selected["sources"])] * 2
    assert all(proof["verified_pin_paths"] == (str(case.archive),) for proof in calls["proofs"])
    assert all(proof["pin_bytes_read"] == case.archive.stat().st_size for proof in calls["proofs"])
    assert all(proof["repinned"] is False and proof["large_artifacts_verified"] is False for proof in calls["proofs"])


@pytest.mark.parametrize("consumer", ["single", "mix", None])
def test_default_consumers_keep_three_native_checks(case, monkeypatch, consumer):
    original = published.PublishedSources.validate_pinned_context
    calls = []

    def validate(self, descriptor):
        calls.append(self)
        return original(self, descriptor)

    monkeypatch.setattr(published.PublishedSources, "validate_pinned_context", validate)
    contract.load_c0_published_context(case.reference, consumer_id=consumer, runtime_binding=case.active)
    assert len(calls) == 3
    assert all(reader is calls[0] for reader in calls)


@pytest.mark.parametrize("change", ["consumer", "no_reference", "reader_mapping", "reader_like_mapping",
                                   "no_runtime", "mutable_head"])
def test_opt_in_requires_single_and_explicit_immutable_reference(case, change):
    reference, consumer, active = case.reference, "single", case.active
    if change == "consumer":
        consumer = "mix"
    elif change == "no_reference":
        reference = None
    elif change == "reader_mapping":
        reference = {"sources": object()}
    elif change == "reader_like_mapping":
        class ReaderMapping(dict):
            def projection_artifacts(self, route):
                return ()

        reference = ReaderMapping(reference)
    elif change == "no_runtime":
        active = None
    else:
        reference = dict(reference, manifest_path=str(case.current))
    with pytest.raises(ValueError):
        contract.load_c0_published_context(reference, consumer_id=consumer, runtime_binding=active,
            selected_single_build=True)


@pytest.mark.parametrize("enabled", [False, True])
def test_identity_classification_gate_is_not_bypassed(case, enabled):
    case.identity["records"][0]["is_ordinary_share"] = False
    case.publish()
    with pytest.raises(ValueError, match="current_identity_positive_record_evidence_invalid"):
        _load(case, selected_single_build=enabled)


@pytest.mark.parametrize("reference", ["identity_ref", "raw_ref", "receipt_ref"])
def test_selected_identity_sidecars_still_verify_exact_bytes(case, reference):
    (case.root / getattr(case, reference)["ref"]).write_bytes(b"tampered")
    with pytest.raises(ValueError, match="current_identity_content_digest_or_size_mismatch"):
        _load(case, selected_single_build=True)


@pytest.mark.parametrize("part", ["scope", "calendar"])
def test_private_content_functions_keep_sidecar_hash_and_schema_gates(case, part):
    reader = contract._published_sources(case.reference)
    catalog = {route: tuple(reader.projection_artifacts(route)) for route in contract._REQUIRED_CORE_ROUTES}
    if part == "scope":
        path = case.root / case.scope_ref["ref"]
        call = lambda: case.native_scope(reader, catalog, case.active, selected_single_build=True)
    else:
        path = case.root / case.calendar_ref["ref"]
        call = lambda: case.native_calendar(reader, application_scope=case.scope,
            snapshot_fingerprint=reader.fingerprint)
    # The bound fixture intentionally has invalid domain content, not a fake successful validator.
    with pytest.raises(ValueError):
        call()
    path.write_bytes(b"tampered")
    with pytest.raises(ValueError, match="current_identity_content_digest_or_size_mismatch"):
        call()


@pytest.mark.parametrize("change", ["bytes", "missing_archive", "catalog", "fingerprint"])
def test_exit_native_postcheck_rejects_pin_drift_or_repin(case, change):
    def change_during_calendar(reader):
        if change == "bytes":
            case.archive.write_bytes(case.archive.read_bytes() + b" ")
        elif change == "missing_archive":
            case.archive.unlink()
        elif change == "catalog":
            reader._sources[0]["entries"]["finmind_price_adj"][0][1]["status"] = "PARTIAL"
        else:
            reader.fingerprint = "c" * 64

    case.calendar_hook = change_during_calendar
    with pytest.raises(ValueError):
        _load(case, selected_single_build=True)


def test_entry_repin_is_rejected_before_any_sidecar_work(case, monkeypatch):
    original = published.PublishedSources.validate_pinned_context
    calls = []

    def remove_archive_before_native_validation(reader, descriptor):
        calls.append(reader)
        case.archive.unlink()
        return original(reader, descriptor)

    monkeypatch.setattr(published.PublishedSources, "validate_pinned_context", remove_archive_before_native_validation)
    monkeypatch.setattr(contract, "_load_application_scope", lambda *a, **k: pytest.fail("entry repin must stop"))
    with pytest.raises(ValueError, match="selected_context_pin_changed_during_build"):
        _load(case, selected_single_build=True)
    assert len(calls) == 1


def test_current_head_advance_keeps_immutable_pin_and_builds_are_isolated(case):
    first = _load(case, selected_single_build=True)
    advanced = json.loads(case.current.read_text(encoding="utf-8"))
    advanced["run_id"] = "new-current-not-adopted"
    case.current.write_bytes(contract._canonical(advanced))
    second = _load(case, selected_single_build=True)
    assert second["descriptor"]["sources"] == first["descriptor"]["sources"]
    assert second["sources"] is not first["sources"]
    assert second["sources"].snapshot_descriptor() == first["sources"].snapshot_descriptor()
    assert not any("selected_single_build" in key for key in second)
