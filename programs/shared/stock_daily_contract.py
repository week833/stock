"""Common verified daily inputs, model binding and bounded forward ledger bridge."""
from __future__ import annotations

import contextlib
import copy
import hashlib
import importlib.util
import json
import math
import os
from collections.abc import Mapping
from pathlib import Path

DEFAULT_POLICY_MODULE = Path(r"D:\TWStockData\services\market_update\daily_policy.py")
_POLICY_MODULES = {}
DATA_TIME_SCHEMA = "stock-daily-data-time-v1"
_TAIPEI_OFFSET_HOURS = 8
_REQUIRED_CORE_ROUTES = ("official_calendar", "official_market_daily", "finmind_stock_info", "finmind_price_adj")


def _load_error_types():
    """Exception split (plan 0.2): integrity raises, version drift is only collected.

    The classes live in the adoption module (the lowest shared layer) so every
    loader of this file (MIX exec, Top10 alias, single-stock import) shares them.
    """
    try:
        import stock_daily_contract_adoption as adoption
    except ImportError:
        import sys
        path = Path(__file__).resolve().with_name("stock_daily_contract_adoption.py")
        spec = importlib.util.spec_from_file_location("stock_daily_contract_adoption", path)
        adoption = importlib.util.module_from_spec(spec)
        sys.modules["stock_daily_contract_adoption"] = adoption
        spec.loader.exec_module(adoption)
    return adoption.C0IntegrityError, adoption.C0VersionDrift


C0IntegrityError, C0VersionDrift = _load_error_types()


def _canonical(value):
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False).encode("utf-8")


def load_daily_policy():
    path = Path(os.environ.get("STOCK_DAILY_POLICY_MODULE_PATH") or DEFAULT_POLICY_MODULE).resolve(strict=True)
    info = path.stat()
    key = (str(path), info.st_size, info.st_mtime_ns)
    module = _POLICY_MODULES.get(key)
    if module is None:
        specification = importlib.util.spec_from_file_location("_stock_daily_requirements_policy", path)
        if specification is None or specification.loader is None:
            raise C0IntegrityError("daily_policy_module_unavailable")
        module = importlib.util.module_from_spec(specification)
        specification.loader.exec_module(module)
        _POLICY_MODULES[key] = module
    explicit_policy = os.environ.get("STOCK_DAILY_REQUIREMENTS_PATH")
    policy = module.load_policy(path=explicit_policy,
        registry_path=str(Path(explicit_policy).resolve().parent / "requirements_v1.json") if explicit_policy else None)
    # Schema major version is a parse requirement; other profiles beside C0 are allowed.
    if (policy.get("schema") != "tw-stock-daily-requirements-v2"
            or policy.get("schema_version") != 2 or "C0" not in (policy.get("profiles") or {})):
        raise C0IntegrityError("daily_current_only_policy_required")
    return module, policy


def _number(value):
    if isinstance(value, bool):
        return None
    try:
        result = float(value)
    except (TypeError, ValueError):
        return None
    return result if math.isfinite(result) else None


def _entry_metadata(item, key):
    entry = item.get("metadata") or {}
    return (entry.get("metadata") or {}).get(key, entry.get(key))


def _physical_store_ref(root, reference):
    # A retired EvidenceRetirement is audit metadata, never a live ContentRef.
    if not isinstance(reference, Mapping) or reference.get("physical_state") == "retired":
        raise C0IntegrityError("c0_retired_observation_cannot_be_runtime_reference")
    return _identity_ref_bytes(root, reference, "")


def _load_application_scope(sources, catalog, active, drift=None, *, selected_single_build=False):
    """Read the same-head application scope.  Bindings between head and routes,
    and the policy the scope was generated under, are labels; seals still raise."""
    drift = drift if drift is not None else C0VersionDrift()
    roots = {str(Path(items[0]["root"]).resolve()) for items in catalog.values()}
    if len(roots) != 1:
        raise C0IntegrityError("c0_current_scope_requires_one_canonical_published_root")
    root = next(iter(roots))
    snapshot = sources.descriptor() if selected_single_build else sources.snapshot_descriptor()
    published = snapshot.get("sources") or []
    if len(published) != 1:
        raise C0IntegrityError("c0_current_scope_immutable_head_ambiguous")
    head_path = published[0].get("manifest_path")
    head = _identity_json(_stable_file_bytes(head_path))
    head_metadata = head.get("metadata") or {}
    references = [_entry_metadata(items[0], "application_retention_scope") for items in catalog.values()]
    head_reference = head_metadata.get("application_retention_scope")
    # The head's own scope reference is authoritative; route copies are compared only.
    reference = head_reference if isinstance(head_reference, Mapping) else next(
        (ref for ref in references if isinstance(ref, Mapping)), None)
    if not isinstance(reference, Mapping):
        # Without any scope the analysis axis is unknown; nothing can be analysed honestly.
        raise C0IntegrityError("c0_same_head_application_retention_scope_missing")
    if any(not isinstance(ref, Mapping) or dict(ref) != dict(reference) for ref in references):
        drift.note("route_scope_binding_differs", recorded=reference.get("scope_id"),
            actual=sorted({str((ref or {}).get("scope_id")) for ref in references}),
            detail={"analysed": "head_application_retention_scope"})
    if head_reference != reference:
        drift.note("head_scope_binding_differs", recorded=(head_reference or {}).get("scope_id")
            if isinstance(head_reference, Mapping) else None, actual=reference.get("scope_id"))
    raw, binding = _physical_store_ref(root, reference)
    scope = _identity_json(raw)
    import stock_daily_application_scope as scope_contract
    scope_labels = []
    scope = scope_contract.validate_application_retention_scope(scope, scope_labels)
    capture_ref = head_metadata.get("application_capture_scope")
    if not isinstance(capture_ref, Mapping):
        capture_ref = next((ref for ref in (_entry_metadata(items[0], "application_capture_scope")
            for items in catalog.values()) if isinstance(ref, Mapping)), None)
    if any(_entry_metadata(items[0], "application_capture_scope") != capture_ref for items in catalog.values()):
        drift.note("route_capture_scope_binding_differs", recorded=(capture_ref or {}).get("scope_id"),
            detail={"analysed": "head_application_capture_scope"})
    capture_raw, capture_binding = _physical_store_ref(root, capture_ref)
    capture = _identity_json(capture_raw)
    scope_contract.validate_capture_scope_pair(scope, capture, scope_labels)
    drift.extend(scope_labels)
    if (capture_ref.get("scope_id") != capture["scope_id"]
            or (capture_ref.get("document_content_sha256") is not None
                and capture_ref["document_content_sha256"] != capture["document_content_sha256"])):
        raise C0IntegrityError("c0_capture_descriptor_domain_or_content_seal_mismatch")
    # Which policy the scope was generated under is information, never a gate.
    current = active.get("current_policy_binding") or {}
    generated = {"policy_sha256": (scope.get("requirements_ref") or {}).get("sha256"),
        "registry_sha256": ((capture.get("source_binding") or {}).get("registry_ref") or {}).get("sha256"),
        "capture_requirements_sha256": ((capture.get("source_binding") or {}).get("capture_requirements_ref") or {}).get("sha256")}
    present = {"policy_sha256": current.get("policy_sha256"), "registry_sha256": current.get("requirements_raw_sha256"),
        "capture_requirements_sha256": (current.get("application_capture_binding") or {}).get("raw_sha256")}
    if generated["policy_sha256"] != present["policy_sha256"]:
        drift.note("scope_policy_differs", recorded=generated["policy_sha256"], actual=present["policy_sha256"],
            detail={"meaning": "scope_generated_under_a_different_daily_policy"})
    if (generated["registry_sha256"], generated["capture_requirements_sha256"]) != (
            present["registry_sha256"], present["capture_requirements_sha256"]):
        drift.note("scope_registry_or_capture_binding_differs",
            recorded={key: generated[key] for key in ("registry_sha256", "capture_requirements_sha256")},
            actual={key: present[key] for key in ("registry_sha256", "capture_requirements_sha256")})
    if head_metadata.get("current_ordinary_identity") != _entry_metadata(catalog["finmind_stock_info"][0], "current_ordinary_identity"):
        drift.note("head_identity_binding_differs", detail={"analysed": "finmind_stock_info_route_sidecar"})
    if head_metadata.get("verified_market_calendar") != _entry_metadata(catalog["official_calendar"][0], "verified_market_calendar"):
        drift.note("head_calendar_binding_differs", detail={"analysed": "official_calendar_route_sidecar"})
    if reference.get("scope_id") != scope["scope_id"] or reference.get("capture_scope_id") != scope["capture_scope_id"]:
        raise C0IntegrityError("c0_application_scope_descriptor_identity_mismatch")
    return {"body": scope, "binding": binding, "capture": capture,
        "capture_binding": capture_binding, "root": root,
        "generated_under": {"scope_generated_under_policy_sha": generated["policy_sha256"],
            "current_policy_sha": present["policy_sha256"], "revision": scope.get("revision"),
            "registry_sha256": generated["registry_sha256"],
            "capture_requirements_sha256": generated["capture_requirements_sha256"]}}


# Former source pins of the calendar validators (recorded for information only;
# the calendar *content* is still fully validated by those modules).
_CALENDAR_VALIDATOR_SHA256 = "a3c191c05e5477a095f09dd9a5e9713004041bd5530bd4e57ac98b197d8386da"
_CALENDAR_MONTH_PARSER_SHA256 = "f3ac8cef2bdb1648511000c4df86331695e72a47caa23b8901614a77ece547aa"


def published_calendar_axes(context, start=None, end=None, *, application_scope=None, drift=None):
    """Read exact bounded market axes; retired observations never become physical proof.

    Calendar content (proof, derivation, monthly sources, physical axes) is
    validated and raises.  The validator sources' sha is information
    (``code_identity``); scope/asof differences between calendar and scope are labels.
    """
    drift = drift if drift is not None else C0VersionDrift()
    sources = _published_sources(context)
    return _published_calendar_axes_from_sources(sources, start, end, application_scope=application_scope, drift=drift)


def _published_calendar_axes_from_sources(sources, start=None, end=None, *, application_scope=None,
                                         drift=None, snapshot_fingerprint=None):
    drift = drift if drift is not None else C0VersionDrift()
    items = tuple(sources.projection_artifacts("official_calendar"))
    if len(items) != 1:
        raise C0IntegrityError("calendar_reference_ambiguous_or_missing")
    item = items[0]
    reference = _entry_metadata(item, "verified_market_calendar")
    if (not isinstance(reference, Mapping) or reference.get("schema") != "twstock-updater.verified-market-calendar.v2"
            or reference.get("schema_version") != 2):
        raise C0IntegrityError("calendar_current_bounded_verified_sidecar_missing")
    raw, binding = _identity_ref_bytes(item["root"], reference, "_calendar/verified/")
    module_path = Path(__file__).resolve().with_name("stock_daily_calendar.py").resolve(strict=True)
    # Path whitelist kept (same directory); the bytes are executed as read and their sha recorded.
    validator_bytes = _stable_file_bytes(module_path)
    code_identity = {"calendar_validator_path": str(module_path),
        "calendar_validator_sha256": hashlib.sha256(validator_bytes).hexdigest(),
        "calendar_validator_recorded_sha256": _CALENDAR_VALIDATOR_SHA256,
        "calendar_month_parser_sha256": None, "calendar_month_parser_recorded_sha256": _CALENDAR_MONTH_PARSER_SHA256}
    spec = importlib.util.spec_from_file_location("_stock_daily_bounded_calendar_validator", module_path)
    if spec is None or spec.loader is None:
        raise C0IntegrityError("calendar_validator_unavailable")
    module = importlib.util.module_from_spec(spec)
    exec(compile(validator_bytes.decode("utf-8-sig"), str(module_path), "exec"), module.__dict__)
    proof = module.validate_verified_market_calendar_v2(_identity_json(raw))
    if application_scope is None:
        drift.note("calendar_scope_not_supplied", detail={"analysed": "calendar_proof_axes_only"})
        scoped = None
    else:
        scoped = application_scope.get("body", application_scope)
    if scoped is not None and (proof["scope_id"] != scoped["scope_id"] or proof["verified_asof"] != scoped["asof"]
            or proof["retention_scope_binding"]["capture_scope_id"] != scoped["capture_scope_id"]
            or reference.get("scope_id") != scoped["scope_id"]
            or reference.get("verified_asof") != scoped["asof"]):
        drift.note("calendar_scope_differs", recorded={"scope_id": scoped["scope_id"], "asof": scoped["asof"]},
            actual={"scope_id": proof["scope_id"], "verified_asof": proof["verified_asof"]},
            detail={"analysed": "verified_calendar_axes"})
    checked, physical, axes, checked_parser = {}, [], {}, {}
    def read_ref(ref):
        key = (ref["ref"], ref["sha256"], ref["size"])
        if key not in checked:
            value, ref_binding = _identity_ref_bytes(item["root"], ref, "_calendar/")
            checked[key] = value
            physical.append(ref_binding)
        return checked[key]
    # Traverse only v2 live physical fields.  Retired evidence remains a digest
    # and is deliberately excluded from reads and runtime root accounting.
    derivation = _identity_json(read_ref(proof["source_receipt_ref"]))
    module.validate_calendar_scope_derivation(proof, derivation)
    for source in proof["sources"]:
        source_raw = read_ref(source["raw_ref"])
        receipt = _identity_json(read_ref(source["http_receipt_ref"]))
        if (receipt.get("http_status", receipt.get("status")) != 200
                or receipt.get("source_url", receipt.get("url")) != source["source_url"]):
            raise C0IntegrityError("calendar_source_http_receipt_mismatch")
        for field in ("sha256", "raw_sha256"):
            if field in receipt and receipt[field] != source["raw_ref"]["sha256"]:
                raise C0IntegrityError("calendar_source_raw_receipt_digest_mismatch")
        native = source.get("native_unit") or {}
        if native.get("kind") == "calendar_month":
            contract = _identity_json(read_ref(native["monthly_contract_ref"]))
            html_raw = read_ref(contract["raw_ref"])
            html_http = _identity_json(read_ref(contract["http_receipt_ref"]))
            original_http = (_identity_json(read_ref(receipt["original_http_receipt_ref"]))
                if receipt.get("original_http_receipt_ref") is not None else None)
            companion_path = module_path.with_name("stock_daily_calendar_base.py")
            if "_parser" not in checked_parser:
                parser_bytes = _stable_file_bytes(companion_path)
                code_identity["calendar_month_parser_sha256"] = hashlib.sha256(parser_bytes).hexdigest()
                parser_spec = importlib.util.spec_from_file_location("_stock_daily_month_parser", companion_path)
                if parser_spec is None or parser_spec.loader is None:
                    raise C0IntegrityError("calendar_month_parser_unavailable")
                parser = importlib.util.module_from_spec(parser_spec)
                exec(compile(parser_bytes.decode("utf-8-sig"), str(companion_path), "exec"), parser.__dict__)
                checked_parser["_parser"] = parser
            parser = checked_parser["_parser"]
            module.validate_calendar_monthly_source(source, source_raw, receipt, contract, html_raw, html_http,
                monthly_report_validator=parser._monthly_report, original_http_body=original_http)
    for market, value in proof["markets"].items():
        scope_market = (scoped or {}).get("markets", {}).get(market)
        if scope_market is not None and (value["historical_sessions"] != scope_market["historical_sessions"]
                or value["future_sessions"] != scope_market["future_sessions"]):
            drift.note("calendar_scope_axis_differs", detail={"market": market,
                "calendar_last_historical": (value["historical_sessions"] or [None])[-1],
                "scope_last_historical": (scope_market["historical_sessions"] or [None])[-1],
                "analysed": "verified_calendar_axes_with_scope_window"})
        for key, expected in (("axis_ref", value["sessions"]),
                              ("historical_axis_ref", value["historical_sessions"]),
                              ("future_axis_ref", value["future_sessions"])):
            ref = value[key]
            if ref is None:
                if expected:
                    raise C0IntegrityError("calendar_nonempty_axis_reference_missing")
                continue
            decoded = json.loads(read_ref(ref).decode("utf-8-sig"))
            if decoded != expected:
                raise C0IntegrityError("calendar_physical_inline_axis_mismatch")
        values = value["sessions"]
        if start is not None:
            values = [day for day in values if day >= _identity_day(start)]
        if end is not None:
            values = [day for day in values if day <= _identity_day(end)]
        axes[market.upper()] = values
    scope_id = scoped["scope_id"] if scoped is not None else proof["scope_id"]
    if snapshot_fingerprint is None:
        snapshot_fingerprint = sources.snapshot_descriptor()["snapshot_fingerprint"]
    calendar_binding = {"verified_market_calendar": binding, "physical_refs": physical,
        "application_retention_scope": application_scope.get("binding") if application_scope is not None else None,
        "scope_id": scope_id, "snapshot_fingerprint": snapshot_fingerprint}
    calendar_binding["binding_sha256"] = hashlib.sha256(_canonical(calendar_binding)).hexdigest()
    return axes, {"route_id": "official_calendar", "calendar_markets": sorted(axes),
        "calendar_verified_by_market": {market: True for market in axes}, "unsupported_markets": [],
        "date_unit": "market_trading_session", "future_targets_verified": proof["future_targets_verified"],
        "future_axis_verified_by_market": {key.upper(): value["future_axis_verified"] for key, value in proof["markets"].items()},
        "historical_axis_verified": True, "binding": calendar_binding,
        "verified_asof": proof["verified_asof"], "scope_id": scope_id,
        "retired_historical_observations": copy.deepcopy(proof["retired_historical_observations"]),
        "code_identity": code_identity}


def validate_selected_snapshot(descriptor, expected):
    """Bind the report's already verified selections to the reader's pinned catalog."""
    if not isinstance(descriptor, Mapping) or not isinstance(expected, Mapping):
        raise C0IntegrityError("daily_snapshot_descriptor_missing")
    raw = dict(descriptor)
    digest = raw.pop("descriptor_sha256", None)
    if digest != hashlib.sha256(_canonical(raw)).hexdigest():
        raise C0IntegrityError("daily_snapshot_descriptor_digest_mismatch")
    if (descriptor.get("schema") != "stock-data-published-inputs-v1"
            or descriptor.get("snapshot_fingerprint") != expected.get("snapshot_fingerprint")
            or descriptor.get("sources") != expected.get("sources")):
        raise C0IntegrityError("daily_snapshot_identity_mismatch")
    fields = ("route_id", "source_id", "root", "path", "sha256", "size")
    catalog = {tuple(item.get(name) for name in fields) for item in expected.get("artifacts", [])}
    selected = descriptor.get("artifacts")
    if not isinstance(selected, list) or not selected:
        raise C0IntegrityError("daily_snapshot_no_verified_artifacts")
    for item in selected:
        if not isinstance(item, Mapping) or tuple(item.get(name) for name in fields) not in catalog:
            raise C0IntegrityError("daily_snapshot_selected_artifact_mismatch")
    return descriptor["snapshot_fingerprint"]


def _selected_context_pin(sources):
    descriptor = sources.descriptor()
    return _canonical({"sources": descriptor["sources"],
                       "snapshot_fingerprint": descriptor["snapshot_fingerprint"]})


def _check_selected_context_pin(sources, expected, proof):
    if (proof.get("repinned") or proof.get("descriptor_refreshed")
            or _selected_context_pin(sources) != expected):
        raise C0IntegrityError("selected_context_pin_changed_during_build")


def _published_sources(context_or_sources, *, selected_single_build=False):
    """Resolve an existing reader or an exact immutable-head mapping, never a mutable QA file."""
    owned_reader = False
    if isinstance(context_or_sources, Mapping) and context_or_sources.get("sources") is not None:
        sources = context_or_sources["sources"]
    elif callable(getattr(context_or_sources, "projection_artifacts", None)):
        sources = context_or_sources
    elif isinstance(context_or_sources, Mapping):
        import re
        import stock_data_sources as data_sources
        root = Path(context_or_sources.get("root") or "").resolve(strict=True)
        path = Path(context_or_sources.get("manifest_path") or "").resolve(strict=True)
        expected = context_or_sources.get("manifest_sha256")
        if (not isinstance(expected, str) or not re.fullmatch("[0-9a-f]{64}", expected)
                or path.parent != root / "_manifests" or path.name != expected + ".json"):
            raise C0IntegrityError("daily_immutable_head_reference_invalid")
        sources = data_sources.PublishedSources([{"kind": "manifest_json", "root": str(root),
                    "manifest_path": str(path), "source_id": "shared_market_data"}])
        owned_reader = True
        try:
            descriptor = sources.descriptor() if selected_single_build else sources.snapshot_descriptor()
            published = descriptor.get("sources") or []
            if len(published) != 1 or published[0].get("manifest_sha256") != expected:
                raise C0IntegrityError("daily_immutable_head_digest_mismatch")
        except BaseException:
            sources.close()
            raise
    else:
        raise C0IntegrityError("daily_published_reader_missing")
    try:
        if (not callable(getattr(sources, "projection_artifacts", None))
                or not callable(getattr(sources, "validate_pinned_context", None))
                or not callable(getattr(sources, "snapshot_descriptor", None))):
            raise C0IntegrityError("daily_published_reader_contract_missing")
        selected_pin = _selected_context_pin(sources) if selected_single_build else None
        proof = sources.validate_pinned_context(sources.snapshot_descriptor())
        if selected_single_build:
            _check_selected_context_pin(sources, selected_pin, proof)
        return sources
    except BaseException:
        if owned_reader:
            sources.close()
        raise


def _identity_ref_bytes(root, reference, required_prefix):
    import re
    if not isinstance(reference, Mapping):
        raise C0IntegrityError("current_identity_content_reference_missing")
    relative, digest, size = reference.get("ref"), reference.get("sha256"), reference.get("size")
    if (not isinstance(relative, str) or not relative.startswith(required_prefix)
            or not isinstance(digest, str) or not re.fullmatch("[0-9a-f]{64}", digest)
            or type(size) is not int or size < 0):
        raise C0IntegrityError("current_identity_content_reference_invalid")
    rel = Path(relative)
    base = Path(root).resolve(strict=True)
    if rel.is_absolute() or ".." in rel.parts or digest not in rel.name:
        raise C0IntegrityError("current_identity_content_reference_outside_store")
    path = (base / rel).resolve(strict=True)
    if not path.is_relative_to(base):
        raise C0IntegrityError("current_identity_content_reference_outside_store")
    identity = lambda value: (value.st_dev, value.st_ino, value.st_size, value.st_mtime_ns)
    with path.open("rb") as handle:
        before = identity(os.fstat(handle.fileno()))
        raw = handle.read(size + 1)
        after = identity(os.fstat(handle.fileno()))
    if before != after or after != identity(path.stat()):
        raise C0IntegrityError("current_identity_content_changed_during_read")
    if len(raw) != size or hashlib.sha256(raw).hexdigest() != digest:
        raise C0IntegrityError("current_identity_content_digest_or_size_mismatch")
    return raw, {"root": str(base), "ref": relative, "path": str(path), "sha256": digest, "size": size}


def _identity_json(raw):
    def unique(pairs):
        result = {}
        for key, value in pairs:
            if key in result:
                raise C0IntegrityError("current_identity_duplicate_json_key")
            result[key] = value
        return result
    result = json.loads(raw.decode("utf-8-sig"), object_pairs_hook=unique,
                        parse_constant=lambda _: (_ for _ in ()).throw(ValueError("current_identity_nonfinite_json")))
    if not isinstance(result, Mapping):
        raise C0IntegrityError("current_identity_document_shape_invalid")
    return result


def _identity_day(value):
    from datetime import date
    if not isinstance(value, str):
        raise C0IntegrityError("current_identity_calendar_day_invalid")
    return date.fromisoformat(value).isoformat()


def load_current_ordinary_identity(context_or_sources, *, decision_asof=None, historical=False):
    """Verify the current classification sidecar adopted into the caller's exact pinned head."""
    if type(historical) is not bool:
        raise TypeError("current_identity_historical_flag_invalid")
    sources = _published_sources(context_or_sources)
    return _load_current_ordinary_identity_from_sources(sources, decision_asof=decision_asof, historical=historical)


def _load_current_ordinary_identity_from_sources(sources, *, decision_asof=None, historical=False,
                                                snapshot_fingerprint=None):
    items = tuple(sources.projection_artifacts("finmind_stock_info"))
    if len(items) != 1:
        raise C0IntegrityError("current_identity_stock_info_reference_ambiguous_or_missing")
    item = items[0]
    # A partial stock_info publication is labelled; only verified positives are
    # eligible, so absent/unknown stocks (incl. every ETF) stay excluded.
    publication_status = str(item.get("artifact_descriptor", {}).get("status", "")).lower()
    publication_incomplete = publication_status not in {"success", "complete"}
    entry = item.get("metadata") or {}
    reference = (entry.get("metadata") or {}).get("current_ordinary_identity")
    if (not isinstance(reference, Mapping) or reference.get("schema") != "tw-stock-current-ordinary-identity-v1"
            or reference.get("historical") is not False or reference.get("effective_date") is not None):
        raise C0IntegrityError("current_identity_pinned_sidecar_missing_or_scope_invalid")
    raw, binding = _identity_ref_bytes(item["root"], reference, "_identity/current_ordinary/")
    document = _identity_json(raw)
    if document.get("schema") != reference["schema"] or type(document.get("schema_version")) is not int or document.get("schema_version") != 1:
        raise C0IntegrityError("current_identity_schema_mismatch")
    source_asof = _identity_day(reference.get("source_asof"))
    verified_asof = _identity_day(reference.get("verified_asof"))
    observed_date = _identity_day(reference.get("observed_date"))
    if (document.get("source_asof") != source_asof
            or document.get("classification_verified_asof") != verified_asof
            or document.get("classification_observed_date") != observed_date
            or document.get("classification_scope") != "current_inference_only"
            or document.get("classification_effective_date") is not None
            or document.get("historical_classification_verified") is not False
            or document.get("historical_universe_verified") is not False):
        raise C0IntegrityError("current_identity_time_or_historical_scope_mismatch")
    if historical:
        raise C0IntegrityError("current_identity_historical_qualification_unavailable")
    source_evidence = document.get("sources")
    if not isinstance(source_evidence, list) or not source_evidence:
        raise C0IntegrityError("current_identity_official_source_evidence_missing")
    by_market, verified_sources = {}, []
    for source in source_evidence:
        market = str(source.get("board") or "").upper()
        if market not in {"TWSE", "TPEX"} or market in by_market:
            raise C0IntegrityError("current_identity_official_market_evidence_invalid")
        expected_url = "https://isin.twse.com.tw/isin/C_public.jsp?strMode=" + ("2" if market == "TWSE" else "4")
        if source.get("source_url") != expected_url:
            raise C0IntegrityError("current_identity_official_source_authority_invalid")
        raw_ref = source.get("raw_ref")
        _, raw_binding = _identity_ref_bytes(item["root"], raw_ref, "_identity/raw/")
        if raw_binding["sha256"] != source.get("raw_sha256") or raw_binding["size"] != source.get("raw_size"):
            raise C0IntegrityError("current_identity_official_raw_binding_mismatch")
        receipt_raw, receipt_binding = _identity_ref_bytes(item["root"], source.get("http_receipt_ref"), "_identity/receipts/")
        receipt = _identity_json(receipt_raw)
        if (receipt.get("schema") != "twstock-updater.current-ordinary-source-receipt.v1"
                or receipt.get("schema_version") != 1 or str(receipt.get("market") or "").upper() != market
                or receipt.get("http_status") != 200 or source.get("http_status") != 200
                or receipt.get("source_url") != source.get("source_url")
                or receipt.get("observed_at") != source.get("observed_at")
                or receipt.get("observed_date") != observed_date
                or receipt.get("raw") != raw_ref or receipt.get("classification_schema") != document["schema"]):
            raise C0IntegrityError("current_identity_official_receipt_binding_mismatch")
        by_market[market] = source
        verified_sources.append({"market": market, "source_url": source["source_url"],
            "source_asof": source.get("source_asof"), "observed_at": source["observed_at"],
            "raw": raw_binding, "http_receipt": receipt_binding})
    records = document.get("records")
    if not isinstance(records, list) or type(document.get("record_count")) is not int or document["record_count"] != len(records):
        raise C0IntegrityError("current_identity_records_count_mismatch")
    positives, seen = [], set()
    for record in records:
        if not isinstance(record, Mapping):
            raise C0IntegrityError("current_identity_record_shape_invalid")
        pair = (str(record.get("stock_id") or ""), str(record.get("market") or "").upper())
        source = by_market.get(pair[1])
        if (not pair[0] or pair in seen or source is None or str(record.get("board") or "").upper() != pair[1]
                or record.get("is_ordinary_share") is not True or record.get("security_type") != "ordinary_share"
                or record.get("current_ordinary_classification_verified") is not True
                or record.get("source_security_section") not in {"股票", "創新板"}
                or not str(record.get("source_cfi") or "").startswith("ES")
                or record.get("source_sha256") != source.get("raw_sha256")
                or record.get("classification_effective_date") is not None
                or record.get("historical_classification_verified") is not False):
            raise C0IntegrityError("current_identity_positive_record_evidence_invalid")
        seen.add(pair); positives.append(list(pair))
    # The adopted artifact currently records positive classifications only. Absence is unknown.
    if document.get("known_excluded_records") not in (None, []):
        raise C0IntegrityError("current_identity_negative_evidence_schema_not_supported")
    after = decision_asof is not None and observed_date > _identity_day(decision_asof)
    if snapshot_fingerprint is None:
        snapshot_fingerprint = sources.snapshot_descriptor().get("snapshot_fingerprint")
    full_binding = {"schema": "stock-current-ordinary-identity-binding-v1", "identity": binding,
        "head_sha256": item.get("parent_identity"), "snapshot_fingerprint": snapshot_fingerprint,
        "source_id": item.get("source_id"), "source_identity": item.get("source_identity"),
        "verified_sources": verified_sources, "verification": "pinned_head_and_identity_raw_receipt_bytes_verified"}
    full_binding["binding_sha256"] = hashlib.sha256(_canonical(full_binding)).hexdigest()
    return {"schema": "stock-current-ordinary-identity-reader-v1", "verified": True,
        "verification_status": "verified_current_only", "current_positive_pairs": positives,
        "current_negative_pairs": [], "records": records, "known_excluded_records": [],
        "source_asof": source_asof, "classification_verified_asof": verified_asof,
        "classification_observed_at": document.get("classification_observed_at"), "observed_date": observed_date,
        "effective_date": None, "historical_classification_verified": False, "historical_universe_verified": False,
        "after_decision_asof": after, "warnings": (["current_classification_observed_after_data_asof"] if after else [])
            + (["stock_info_publication_not_complete:" + publication_status] if publication_incomplete else []),
        "binding": full_binding}


def classify_current_ordinary(identity, stock_id, market):
    """Three-state classification; an absent positive never becomes a negative classification."""
    unknown = {"status": "unknown", "eligible": False, "reason": "current_official_classification_unknown", "record": None}
    if (not isinstance(identity, Mapping) or identity.get("schema") != "stock-current-ordinary-identity-reader-v1"
            or identity.get("verified") is not True):
        return dict(unknown, reason="current_identity_not_verified")
    pair = (str(stock_id), str(market).upper())
    for record in identity.get("records") or []:
        if (str(record.get("stock_id")), str(record.get("market") or "").upper()) == pair:
            return {"status": "verified_ordinary", "eligible": True, "reason": "official_current_positive", "record": record}
    for record in identity.get("known_excluded_records") or []:
        if (str(record.get("stock_id")), str(record.get("market") or "").upper()) == pair:
            return {"status": "verified_nonordinary", "eligible": False, "reason": "official_current_negative", "record": record}
    return unknown

C0_CURRENT_PATH = Path(r"D:\TWStockData\program_data\stock_daily_c0\models\current.json")
C0_REPORT_SCHEMA = "single-stock-daily-c0-estimate-v1"


def _stable_file_bytes(path):
    selected = Path(path).resolve(strict=True)
    identity = lambda stat: (stat.st_dev, stat.st_ino, stat.st_size, stat.st_mtime_ns)
    with selected.open("rb") as handle:
        before = identity(os.fstat(handle.fileno()))
        raw = handle.read()
        after = identity(os.fstat(handle.fileno()))
    if before != after or after != identity(selected.stat()):
        raise C0IntegrityError("daily_artifact_changed_during_read")
    return raw


def c0_policy_binding(consumer_id=None):
    module, policy = load_daily_policy()
    requirements = module.requirements_for(policy, "C0", consumer_id=consumer_id)
    return {"profile_id": "C0", "requirements_version": requirements["requirements_version"],
        "feature_version": requirements["feature_version"], "policy_sha256": requirements["policy_binding"]["sha256"],
        "requirements_raw_sha256": requirements["requirements_binding"]["raw_sha256"],
        "requirements_binding": requirements["requirements_binding"], "policy_binding": requirements["policy_binding"],
        "application_capture_binding": copy.deepcopy(policy["application_capture_binding"]),
        "consumer_id": consumer_id}


def _current_contract_inputs(consumer_id=None):
    import stock_daily_contract_adoption as adoption
    module, policy = load_daily_policy()
    runtime = policy["runtime_binding"]
    path = Path(runtime["policy_path"])
    registry_path = Path(runtime["registry_path"])
    module_path = Path(module.__file__).resolve(strict=True)
    current = {"daily_policy_ref": adoption.content_ref(path, schema="tw-stock-daily-requirements-v2"),
        "registry_ref": adoption.content_ref(registry_path, schema="shared-market-data-requirements-v3"),
        "capture_requirements_ref": adoption.content_ref(path.parent / "application_capture_requirements_v1.json",
            schema="twstock.application-capture-requirements.v1"),
        "policy_module_ref": adoption.content_ref(module_path),
        "requirements_version": policy["requirements_version"], "feature_version": policy["feature_version"]}
    raw_policy = _identity_json(_stable_file_bytes(path))
    registry = _identity_json(_stable_file_bytes(registry_path))
    return module, policy, current, raw_policy, registry


_DUAL_BINDING_KEYS = ("current_policy_binding", "original_model_feature_contract", "contract_adoption_ref",
    "effective_C0_semantics_projection_sha256", "original_effective_C0_semantics_projection_sha256",
    "contract_adoption_verified", "model_activation_epoch")


def _dual_bindings(active):
    return {key: copy.deepcopy(active[key]) for key in _DUAL_BINDING_KEYS}


def _merge_same_labels(labels):
    """One label per (code, recorded, actual); details from repeated sources are merged."""
    merged, index = [], {}
    for label in labels:
        key = _canonical([label.get("code"), label.get("recorded"), label.get("actual")])
        if key in index and isinstance(index[key].get("detail"), dict) and isinstance(label.get("detail"), dict):
            for name, value in label["detail"].items():
                index[key]["detail"].setdefault(name, value)
            continue
        if key in index:
            continue
        item = copy.deepcopy(label)
        index[key] = item
        merged.append(item)
    labels[:] = merged
    return labels


def _adoption_audit(adoption, pointer, current_contract, policy, registry, drift):
    """Optional administrative audit of the pointer's adoption receipt.

    Returns (status, verified-dict-or-None, mismatches).  A missing receipt is
    a label; tampered content-addressed bytes (receipt, projections, sealed core,
    original policy/registry extract) raise ``C0IntegrityError``.
    """
    receipt_ref = pointer.get("contract_adoption_ref")
    if not isinstance(receipt_ref, Mapping):
        drift.note("contract_adoption_receipt_missing", detail={"pointer_field": "contract_adoption_ref"})
        return "missing", None, []
    try:
        receipt, _ = adoption.read_content_ref(receipt_ref, expected_schema=adoption.SCHEMA)
    except FileNotFoundError:
        drift.note("contract_adoption_receipt_missing", recorded=receipt_ref.get("sha256"),
            detail={"path": receipt_ref.get("path")})
        return "missing", None, []
    derivation = receipt.get("projection_derivation") or {}
    try:
        sealed, _ = adoption.read_content_ref(derivation.get("sealed_core_semantics_ref"),
            expected_schema="tw-stock-c0-sealed-core-semantics-v1")
        original_policy, _ = adoption.read_content_ref(derivation.get("original_policy_ref"))
        original_registry, _ = adoption.read_content_ref(derivation.get("original_registry_extract_ref"))
    except FileNotFoundError as exc:
        # The audit inputs are gone (not tampered): the audit cannot run, analysis continues.
        drift.note("contract_adoption_receipt_missing", recorded=receipt_ref.get("sha256"),
            detail={"missing_audit_input": str(getattr(exc, "filename", None) or exc)})
        return "missing", None, []
    audit = C0VersionDrift()
    try:
        original_projection = adoption.effective_c0_projection(original_policy, original_registry,
            sealed_core_semantics=sealed, original=True)
    except ValueError as exc:
        original_projection = None
        audit.note("policy_version_mismatch", detail={"field": "original_projection", "reason": str(exc)})
    try:
        current_projection = adoption.effective_c0_projection(policy, registry, sealed_core_semantics=sealed, drift=audit)
    except ValueError as exc:
        current_projection = None
        audit.note("policy_version_mismatch", detail={"field": "current_projection", "reason": str(exc)})
    try:
        verified = adoption.validate_contract_adoption(receipt_ref, pointer,
            expected_current_contract=current_contract, expected_current_projection=current_projection,
            expected_original_projection=original_projection)
    except FileNotFoundError as exc:
        drift.note("contract_adoption_receipt_missing", recorded=receipt_ref.get("sha256"),
            detail={"missing_audit_input": str(getattr(exc, "filename", None) or exc)})
        return "missing", None, []
    mismatches = audit.labels + [item for item in verified["mismatches"] if item not in audit.labels]
    drift.extend(verified.get("code_labels"))
    if mismatches:
        drift.extend(mismatches)
        drift.note("contract_adoption_unverified", actual="drift", detail={"mismatch_count": len(mismatches),
            "forward_statistics": "isolated_by_forward_ledger"})
    return ("verified" if not mismatches else "drift"), verified, mismatches


def load_active_c0_model(model_path=None, *, consumer_id=None, pointer_path=None):
    """Load the pointer's scientific artifact; the adoption chain is an optional audit.

    Kept (raised): pointer schema, artifact bytes == pointer/sidecar, artifact
    self-consistency, activation epoch.  Labelled (``version_labels``): adoption
    receipt status/mismatches (``contract_adoption_status``), program sha
    differences (``inference_code_differs_from_training``), policy differences.
    """
    import stock_daily_linear as linear
    import stock_daily_contract_adoption as adoption
    drift = C0VersionDrift()
    _, _, current_contract, policy, registry = _current_contract_inputs(consumer_id)
    selected_pointer = Path(pointer_path) if pointer_path is not None else C0_CURRENT_PATH
    pointer_raw = _stable_file_bytes(selected_pointer)
    pointer = _identity_json(pointer_raw)
    if pointer.get("schema") != "tw-stock-daily-linear-current-v1":
        raise C0IntegrityError("c0_current_pointer_schema_invalid")
    explicit_differs = (model_path is not None and Path(model_path).resolve(strict=True)
        != Path(pointer.get("artifact_path") or "").resolve(strict=True))
    effective_pointer = pointer
    if explicit_differs:
        # A QA/explicit model that is not the pointer's: analysed as qa_override and
        # never treated as the adopted model (adoption is reported unverified).
        explicit = Path(model_path).resolve(strict=True)
        explicit_raw = _stable_file_bytes(explicit)
        effective_pointer = dict(pointer, artifact_path=str(explicit), artifact_sha256=hashlib.sha256(explicit_raw).hexdigest(),
            artifact_size=len(explicit_raw))
        drift.note("explicit_model_differs_from_pointer", recorded=pointer.get("artifact_sha256"),
            actual=effective_pointer["artifact_sha256"], detail={"path": str(explicit), "qa_override": True})
    status, verified, mismatches = _adoption_audit(adoption, effective_pointer, current_contract, policy, registry, drift)
    if verified is not None:
        original, anchor = verified["original_model"], verified["original_model_feature_contract"]
    else:
        original, anchor, _ = adoption.original_model_binding(effective_pointer, drift)
    if explicit_differs:
        status = "not_applicable_explicit_model"
    path = Path(effective_pointer["artifact_path"]).resolve(strict=True)
    # The pure core receives its real original five-key feature contract.  New
    # operational policy identity is exposed separately, never substituted here.
    linear_labels = []
    model = linear.load_artifact(path, expected_policy_binding=anchor, labels=linear_labels)
    drift.extend(linear_labels)
    if hashlib.sha256(_stable_file_bytes(path)).hexdigest() != original["sha256"]:
        raise C0IntegrityError("c0_adoption_original_artifact_mismatch")
    from datetime import datetime
    epoch = pointer.get("activated_at_utc")
    if not isinstance(epoch, str) or datetime.fromisoformat(epoch).tzinfo is None:
        raise C0IntegrityError("c0_original_model_activation_epoch_missing")
    identity = linear.module_identity(model)
    code_identity = {"model_module_sha256": identity["model_module_sha256"],
        "current_module_sha256": identity["current_module_sha256"], "loaded_linear_path": identity["loaded_linear_path"],
        "loaded_linear_sha256": identity["current_module_sha256"],
        "pointer_module_path": pointer.get("module_path"), "policy_module": current_contract["policy_module_ref"],
        "contract_path": str(Path(__file__).resolve())}
    if identity["differs"]:
        drift.note("inference_code_differs_from_training", recorded=identity["model_module_sha256"],
            actual=identity["current_module_sha256"], detail={"loaded_linear_path": identity["loaded_linear_path"],
                "meaning": "model_built_by_a_different_program_version"})
    _merge_same_labels(drift.labels)
    adopted = status == "verified"
    binding = {"model": model, "policy_binding": copy.deepcopy(anchor),
        "current_policy_binding": c0_policy_binding(consumer_id),
        "original_model_feature_contract": copy.deepcopy(anchor),
        "contract_adoption_ref": verified["contract_adoption_ref"] if verified is not None
            else copy.deepcopy(pointer.get("contract_adoption_ref")),
        "effective_C0_semantics_projection_sha256": verified["effective_C0_semantics_projection_sha256"]
            if verified is not None else None,
        "original_effective_C0_semantics_projection_sha256": verified["original_effective_C0_semantics_projection_sha256"]
            if verified is not None else None,
        "contract_adoption_verified": adopted, "model_activation_epoch": epoch,
        "contract_adoption_status": status, "contract_adoption_mismatches": mismatches,
        "code_identity": code_identity, "version_labels": drift.labels,
        "pointer_binding": {"path": str(selected_pointer.resolve()), "schema": pointer["schema"],
            "sha256": hashlib.sha256(pointer_raw).hexdigest(), "size": len(pointer_raw)},
        "artifact_binding": {"path": str(path), "sha256": original["sha256"],
            "size": original["size"], "module_sha256": model["metadata"]["module_sha256"],
            "feature_schema": model["metadata"]["feature_schema"], "feature_order": model["metadata"]["feature_order"],
            "qa_override": model_path is not None or selected_pointer.resolve() != C0_CURRENT_PATH.resolve()}}
    return binding

def _single_support_path():
    import sys
    location = Path(__file__).resolve().parents[1] / "single_stock_analysis" / "src"
    if str(location) not in sys.path:
        sys.path.insert(0, str(location))
    return location


def _current_c0_sources(*, single_build=False):
    _single_support_path()
    import stock_cache_lifecycle as lifecycle
    root = Path(r"D:\TWStockData\shared_market_data")
    with lifecycle.catalog_lock(lifecycle.source_registry(root)):
        raw = _stable_file_bytes(root / "_metadata" / "stock_data_current.json")
        digest = hashlib.sha256(raw).hexdigest()
        reference = {"root": str(root), "manifest_path": str(root / "_manifests" / (digest + ".json")),
                     "manifest_sha256": digest}
        return (_published_sources(reference, selected_single_build=True) if single_build
                else _published_sources(reference))


def _price_actual_max_date(item):
    metadata = (item or {}).get("metadata") or {}
    value = metadata.get("actual_max_date") or (metadata.get("metadata") or {}).get("actual_max_date")
    try:
        return _identity_day(value) if value is not None else None
    except (ValueError, TypeError):
        return None


def _taipei_now(now=None):
    from datetime import datetime, timedelta, timezone
    moment = now if now is not None else datetime.now(timezone.utc)
    return moment.astimezone(timezone(timedelta(hours=_TAIPEI_OFFSET_HOURS)))


def _latest_expected_session(days, now=None):
    """Most recent verified session before Taipei today (or today once 14:30 has passed)."""
    taipei = _taipei_now(now)
    today = taipei.date().isoformat()
    closed_today = (taipei.hour, taipei.minute) >= (14, 30)
    candidates = [str(day)[:10] for day in days or [] if str(day)[:10] < today or (str(day)[:10] == today and closed_today)]
    return max(candidates) if candidates else None


def build_data_time(context, *, analysis_asof=None, price_max_date=None, price_collected_at_max=None,
                    requested_asof=None, labels=None, model=None, now=None):
    """The ``data_time`` object (plan 4.1): what data the analysis actually used.

    ``published_at`` is the producer's declaration (never verified here).
    ``is_stale`` compares the analysis day with the latest session the verified
    calendar says should exist before Taipei today; past the calendar's end it
    is unknown and reported via ``calendar_axis_end``.
    """
    from datetime import datetime, timezone
    descriptor = context.get("descriptor") or {}
    head = ((descriptor.get("sources") or [{}]) or [{}])[0]
    identity = context.get("current_identity") or {}
    calendar = context.get("calendar_evidence") or {}
    axes = context.get("market_sessions") or {}
    analysis = analysis_asof or context.get("asof")
    # The day each market was actually analysed (falls back to the scope days).
    by_market = copy.deepcopy(context.get("analysis_asof_by_market") or context.get("asof_by_market") or {})
    expected_by_market = {market: _latest_expected_session(days, now) for market, days in sorted(axes.items())}
    expected_values = [value for value in expected_by_market.values() if value is not None]
    latest_expected = max(expected_values) if expected_values else None
    calendar_end = max((str(day)[:10] for days in axes.values() for day in days), default=None)
    requested = _identity_day(requested_asof) if requested_asof is not None else None
    # Per market: a lagging market is stale even when another market is current.
    stale_markets = {market: [by_market.get(market, analysis), day] for market, day in expected_by_market.items()
                     if day is not None and (by_market.get(market, analysis) or "") < day}
    stale = bool(latest_expected and analysis and analysis < latest_expected) or bool(stale_markets)
    # The verified future axis already proves which later days are (not) sessions;
    # only weekdays beyond the whole verified axis are unknown (holidays are not
    # reported as unverified when the future axis covers them).
    future_verified = calendar.get("future_axis_verified_by_market") or {}
    covered = calendar_end if (calendar_end and future_verified and all(v is True for v in future_verified.values())) else None
    known_until = max((day for day in (covered, calendar.get("verified_asof")) if day), default=None) or calendar_end
    unverified = _weekdays_after(known_until, now)
    # Plan default #13: judged by the verified calendar; beyond it freshness is unknown.
    freshness = "stale" if stale else "calendar_unverified_after" if unverified else "latest"
    active = model or {}
    model_metadata = ((active.get("model") or {}).get("metadata") or {})
    return {"schema": DATA_TIME_SCHEMA, "analysis_asof": analysis, "asof_by_market": by_market,
        "price_max_date": price_max_date if price_max_date is not None else context.get("price_max_date"),
        "scope_asof": context.get("scope_asof"), "scope_cutoff": context.get("scope_cutoff"),
        "calendar_verified_asof": calendar.get("verified_asof"), "calendar_axis_end": calendar_end,
        "identity": {key: identity.get(key) for key in ("source_asof", "classification_verified_asof",
            "classification_observed_at", "observed_date")},
        "price_collected_at_max": price_collected_at_max,
        "head": {"sha256": head.get("manifest_sha256"), "published_at": head.get("published_at"),
            "publication_run_id": head.get("publication_run_id"), "producer_id": head.get("producer_id")},
        "published_at_verified": False,
        "model": {"training_asof": model_metadata.get("asof"), "activated_at_utc": active.get("model_activation_epoch"),
            "artifact_sha256": (active.get("artifact_binding") or {}).get("sha256")},
        "read_at_utc": context.get("read_at_utc") or datetime.now(timezone.utc).isoformat(),
        "requested_asof": requested, "is_older_than_requested": bool(requested and analysis and analysis < requested),
        "latest_expected_session": latest_expected, "latest_expected_session_by_market": expected_by_market,
        "is_stale": stale, "stale_markets": stale_markets, "freshness_status": freshness, "weekdays_after_calendar": unverified,
        "labels": copy.deepcopy(labels if labels is not None else context.get("version_labels") or [])}


def _weekdays_after(day, now=None):
    """Weekdays after the verified calendar up to the latest closed Taipei day (holidays unknown)."""
    from datetime import date, timedelta
    if not day:
        return []
    taipei = _taipei_now(now)
    last = taipei.date() if (taipei.hour, taipei.minute) >= (14, 30) else taipei.date() - timedelta(days=1)
    cursor, result = date.fromisoformat(str(day)[:10]) + timedelta(days=1), []
    while cursor <= last:
        if cursor.weekday() < 5:
            result.append(cursor.isoformat())
        cursor += timedelta(days=1)
    return result


def load_c0_published_context(context_or_sources=None, *, consumer_id=None, runtime_binding=None,
                              selected_single_build=False):
    """Verify a standalone context with the existing default/single pin checks."""
    return _load_c0_published_context(context_or_sources, consumer_id=consumer_id,
        runtime_binding=runtime_binding, selected_single_build=selected_single_build)


def _load_c0_published_context(context_or_sources=None, *, consumer_id=None, runtime_binding=None,
                               selected_single_build=False, _top10_single_build=False):
    """Verify small current-model/same-head scope references without price inference.

    The analysis day is the data actually published: ``min(scope asof, price
    actual_max_date)``.  Version/binding differences are ``version_labels``;
    the result carries ``data_time``.  Integrity and content gates still raise.
    """
    from datetime import datetime, timezone
    if type(selected_single_build) is not bool:
        raise TypeError("selected_single_build_flag_invalid")
    if selected_single_build and (consumer_id != "single" or not isinstance(context_or_sources, Mapping)
            or context_or_sources.get("sources") is not None
            or callable(getattr(context_or_sources, "projection_artifacts", None)) or runtime_binding is None):
        raise C0IntegrityError("selected_single_build_requires_single_immutable_reference")
    _single_support_path()
    active = runtime_binding if runtime_binding is not None else load_active_c0_model(consumer_id=consumer_id)
    drift = C0VersionDrift()
    drift.extend(active.get("version_labels"))
    if active.get("contract_adoption_verified") is not True:
        drift.note("contract_adoption_unverified", actual=active.get("contract_adoption_status", "unverified"),
            detail={"forward_statistics": "isolated_by_forward_ledger"})
    read_at = datetime.now(timezone.utc).isoformat()
    single_build = selected_single_build or _top10_single_build
    if single_build:
        sources = (_current_c0_sources(single_build=True) if context_or_sources is None
                   else _published_sources(context_or_sources, selected_single_build=True))
        selected_pin = _selected_context_pin(sources)
        selected_fingerprint = sources.fingerprint
    else:
        sources = _current_c0_sources() if context_or_sources is None else _published_sources(context_or_sources)
    try:
        if _top10_single_build:
            published = sources.descriptor().get("sources") or []
            if len(published) != 1:
                raise C0IntegrityError("top10_single_build_requires_one_immutable_head")
            source = published[0]
            root = Path(source.get("root") or "").resolve(strict=True)
            manifest = Path(source.get("manifest_path") or "").resolve(strict=True)
            digest = source.get("manifest_sha256")
            if (not isinstance(digest, str) or len(digest) != 64
                    or any(value not in "0123456789abcdef" for value in digest)
                    or manifest.parent != root / "_manifests" or manifest.name != digest + ".json"):
                raise C0IntegrityError("top10_single_build_requires_immutable_head")
        module, policy = load_daily_policy()
        required = module.requirements_for(policy, "C0", consumer_id=consumer_id)["required_routes"]
        catalog, missing_routes = {}, []
        for route in dict.fromkeys(list(required) + list(_REQUIRED_CORE_ROUTES)):
            items = tuple(sources.projection_artifacts(route))
            if len(items) > 1:
                raise C0IntegrityError("c0_required_route_reference_ambiguous_or_missing")
            if not items:
                if route in _REQUIRED_CORE_ROUTES:
                    # Price, calendar, identity and official board are needed to analyse at all.
                    raise C0IntegrityError("c0_required_route_reference_ambiguous_or_missing")
                missing_routes.append(route)
                continue
            catalog[route] = items
        if missing_routes:
            drift.note("optional_route_unavailable", actual=missing_routes, detail={"analysed": "without_dividend_routes"})
        if single_build:
            scope = _load_application_scope(sources, catalog, active, drift, selected_single_build=True)
        else:
            scope = _load_application_scope(sources, catalog, active, drift)
        asof_by_market = {market.upper(): values["historical_sessions"][-1]
            for market, values in scope["body"]["markets"].items()}
        scope_asof = max(asof_by_market.values())
        maximum = _price_actual_max_date(catalog["finmind_price_adj"][0])
        asof = scope_asof
        if maximum is None:
            drift.note("price_actual_max_date_unknown", detail={"analysed": "scope_asof", "scope_asof": scope_asof})
        elif maximum < scope_asof:
            # Analyse the data actually published: the price route ends earlier than the scope.
            asof = maximum
            drift.note("price_lags_scope", recorded=scope_asof, actual=maximum, detail={"analysis_asof": maximum})
        # The day each market is actually analysed: its own verified scope day, never
        # after the published price data.  ``asof`` is the latest of these days.
        analysis_by_market = {market: min(day, asof) for market, day in asof_by_market.items()}
        if single_build:
            identity = _load_current_ordinary_identity_from_sources(sources, decision_asof=asof,
                snapshot_fingerprint=selected_fingerprint)
        else:
            identity = load_current_ordinary_identity(sources, decision_asof=asof)
        for warning in identity.get("warnings") or []:
            if str(warning).startswith("stock_info_publication_not_complete"):
                drift.note("stock_info_publication_not_complete", actual=str(warning).split(":", 1)[-1],
                    detail={"unknown_classification": "excluded"})
        body = scope["body"]
        scope_pairs = {(value["stock_id"], value["market"].upper()) for value in body["allowed_stock_market_pairs"]}
        identity_pairs = {tuple(value) for value in identity["current_positive_pairs"]}
        if (body["ordinary_universe_ref"].get("sha256") != identity["binding"]["identity"]["sha256"]
                or scope_pairs != identity_pairs):
            # Analyse the intersection; a stock outside the verified positives stays unknown (excluded).
            drift.note("scope_identity_universe_differs", recorded=body["ordinary_universe_ref"].get("sha256"),
                actual=identity["binding"]["identity"]["sha256"],
                detail={"scope_only": len(scope_pairs - identity_pairs), "identity_only": len(identity_pairs - scope_pairs),
                        "analysed": "intersection"})
            identity = dict(identity)
            keep = scope_pairs & identity_pairs
            identity["records"] = [record for record in identity["records"]
                if (str(record.get("stock_id")), str(record.get("market") or "").upper()) in keep]
            identity["current_positive_pairs"] = [list(pair) for pair in identity["current_positive_pairs"] if tuple(pair) in keep]
        if single_build:
            axes, calendar = _published_calendar_axes_from_sources(sources, application_scope=scope, drift=drift,
                snapshot_fingerprint=selected_fingerprint)
        else:
            axes, calendar = published_calendar_axes(sources, application_scope=scope, drift=drift)
        route_evidence = {}
        descriptor = copy.deepcopy(sources.descriptor())
        descriptor.pop("descriptor_sha256", None)
        descriptor["artifacts"] = []
        for route, items in catalog.items():
            item = items[0]
            artifact = item["artifact_descriptor"]
            metadata = item.get("metadata") or {}
            native = metadata.get("metadata") or {}
            # Successful physical verification is availability evidence.  A route
            # status or readable hash does not invent completion for PARTIAL data.
            route_evidence[route] = {"verified": True, "physical_available": True,
                "status": artifact.get("status"), "completion_verified": native.get("completion_verified") is True,
                "coverage_verified": native.get("coverage_verified") is True,
                "artifact": {"path": artifact["path"], "sha256": artifact["sha256"], "size": artifact["size"]},
                "application_scope_id": body["scope_id"]}
            descriptor["artifacts"].append({"route_id": route, "source_id": item["source_id"], "root": str(item["root"]),
                "path": artifact["path"], "sha256": artifact["sha256"], "size": artifact["size"]})
        descriptor["descriptor_sha256"] = hashlib.sha256(_canonical(descriptor)).hexdigest()
        try:
            bound = module.bind_snapshot(policy, "C0", dict(descriptor, verified=True,
                application_capture_scope_id=body["capture_scope_id"]))
            readiness = module.assess_readiness(policy, "C0", route_evidence, bound)
        except (ValueError, KeyError) as exc:
            if not missing_routes:
                raise
            readiness = {"status": "UNKNOWN", "reason": "optional_route_unavailable", "missing_routes": missing_routes}
            drift.note("readiness_unavailable", actual=str(exc), detail={"missing_routes": missing_routes})
        # readiness (route completeness assessment) is returned as before; it was never a gate.
        for market, day in sorted(asof_by_market.items()):
            if day != max(asof_by_market.values()):
                # Never an exclusion: each market is analysed with its own verified day
                # and its own exact 253-session window (load_c0_published_panel).
                latest = max(analysis_by_market.values())
                drift.note("market_asof_differs", recorded=max(asof_by_market.values()), actual=day,
                    detail={"market": market, "market_asof": analysis_by_market[market], "latest_market_asof": latest,
                            "per_market_inference": ("applied_own_market_asof" if analysis_by_market[market] != latest
                                                     else "not_needed_same_analysis_asof")})
        result = {"sources": sources, "catalog": catalog, "current_identity": identity,
            "application_scope": scope, "descriptor": descriptor, "asof": asof,
            "asof_by_market": asof_by_market, "analysis_asof_by_market": analysis_by_market,
            "scope_cutoff": scope["body"]["asof"],
            "scope_asof": scope_asof, "price_max_date": maximum, "read_at_utc": read_at,
            "market_sessions": axes, "calendar_evidence": calendar,
            "required_route_evidence": route_evidence, "readiness": readiness,
            "missing_optional_routes": missing_routes,
            "policy_binding": copy.deepcopy(active["policy_binding"]), **_dual_bindings(active)}
        result["version_labels"] = drift.labels
        result["data_time"] = build_data_time(result, labels=drift.labels, model=active)
        if selected_single_build:
            # This proof stays local to one build; sidecars retain their original per-read gates.
            proof = sources.validate_pinned_context(sources.snapshot_descriptor())
            _check_selected_context_pin(sources, selected_pin, proof)
        if _top10_single_build:
            return result, (sources, selected_pin, selected_fingerprint)
        return result
    except BaseException:
        if (context_or_sources is None or isinstance(context_or_sources, Mapping)
                and context_or_sources.get("sources") is None):
            if _top10_single_build:
                with contextlib.suppress(Exception):
                    sources.close()
            else:
                sources.close()
        raise


def load_c0_published_panel(stock_ids=None, *, context_or_sources=None, consumer_id=None, cache_dir=None,
                            asof=None, application_scope=None, runtime_binding=None, top10_single_build=False):
    """Top10 may reuse one immutable context between complete entry/exit pin checks."""
    if type(top10_single_build) is not bool:
        raise TypeError("top10_single_build_flag_invalid")
    if top10_single_build and consumer_id != "top10":
        raise C0IntegrityError("top10_single_build_requires_top10_consumer")
    if not top10_single_build:
        return _load_c0_published_panel(stock_ids, context_or_sources=context_or_sources,
            consumer_id=consumer_id, cache_dir=cache_dir, asof=asof,
            application_scope=application_scope, runtime_binding=runtime_binding)
    active = runtime_binding if runtime_binding is not None else load_active_c0_model(consumer_id=consumer_id)
    context, entry = _load_c0_published_context(context_or_sources, consumer_id=consumer_id,
        runtime_binding=active, _top10_single_build=True)
    sources, entry_pin, entry_fingerprint = entry
    try:
        result = _load_c0_published_panel(stock_ids, context_or_sources=context_or_sources,
            consumer_id=consumer_id, cache_dir=cache_dir, asof=asof,
            application_scope=application_scope, runtime_binding=active, _prepared_context=context,
            _stock_shard_order=True)
        if context.get("sources") is not sources or result.get("sources") is not sources:
            raise C0IntegrityError("top10_context_reader_changed_during_build")
        proof = sources.validate_pinned_context(sources.snapshot_descriptor())
        _check_selected_context_pin(sources, entry_pin, proof)
        if sources.fingerprint != entry_fingerprint:
            raise C0IntegrityError("top10_context_fingerprint_changed_during_build")
        return result
    except BaseException:
        if (context_or_sources is None or isinstance(context_or_sources, Mapping)
                and context_or_sources.get("sources") is None):
            with contextlib.suppress(Exception):
                sources.close()
        raise


def _load_c0_published_panel(stock_ids=None, *, context_or_sources=None, consumer_id=None, cache_dir=None,
                             asof=None, application_scope=None, runtime_binding=None, _prepared_context=None,
                             _stock_shard_order=False):
    """Read requested current ordinary stocks through existing sealed queries; never train or fetch.

    Markets whose latest verified day differs are all analysed: each market uses
    its own day (``asof_by_market[market]``) and its own exact 253-session window,
    and no row after that day is read for its stocks.  ``asof`` is the latest day
    among the markets of the eligible stocks.  Build/predict per market with
    ``predict_c0_by_market`` (or ``panel_by_market``); never one ``build_panel``
    over several markets with ``asof``.
    """
    _single_support_path()
    import pandas as pd
    import numpy as np
    import stock_query_projection as query
    import stock_daily_price_quality as quality
    active = runtime_binding if runtime_binding is not None else load_active_c0_model(consumer_id=consumer_id)
    context = (_prepared_context if _prepared_context is not None else
               load_c0_published_context(context_or_sources, consumer_id=consumer_id, runtime_binding=active))
    drift = C0VersionDrift()
    drift.extend(context.get("version_labels"))
    sources = context["sources"]
    if application_scope is not None and application_scope != context["application_scope"]:
        # The data was republished after the caller pinned its scope: analyse what is published now.
        drift.note("data_updated_since_caller_scope",
            recorded=((application_scope.get("body") or {}).get("scope_id") if isinstance(application_scope, Mapping) else None),
            actual=context["application_scope"]["body"]["scope_id"])
    catalog = {route: tuple(sources.projection_artifacts(route)) for route in ("finmind_price_adj", "official_market_daily")}
    if any(len(items) != 1 for items in catalog.values()):
        raise C0IntegrityError("c0_price_or_official_board_reference_ambiguous_or_missing")
    maximum = context.get("price_max_date")
    data_asof = context["asof"]
    # Each market is analysed with its own verified day (never after the price data).
    market_asof_by_market = copy.deepcopy(context.get("analysis_asof_by_market")
        or {market: min(day, data_asof) for market, day in context["asof_by_market"].items()})
    requested_asof = _identity_day(asof) if asof is not None else None
    identity = context["current_identity"]
    positives = {(str(record["stock_id"]), str(record["market"]).upper()): record
                 for record in identity["records"]}
    positive_markets = {}
    for stock, market in positives:
        positive_markets.setdefault(stock, []).append(market)
    requested = list(dict.fromkeys(str(value) for value in
        (stock_ids if stock_ids is not None else [record["stock_id"] for record in identity["records"]])))
    requested_pairs = [(stock, market) for stock in requested for market in positive_markets.get(stock, [""])]
    if not requested_pairs:
        raise C0IntegrityError("c0_requested_stocks_empty")
    axes, calendar = context["market_sessions"], context["calendar_evidence"]
    verified = calendar.get("calendar_verified_by_market") or {}
    assessments = []
    coverage = {"requested": len(requested_pairs), "eligible": 0, "excluded": [], "records": [],
                "data_quality_assessments": assessments,
                "scope_asof_by_market": copy.deepcopy(context["asof_by_market"]), "inference_asof": data_asof,
                "source_asof": identity["source_asof"], "historical_membership_verified": False, "universe_name": "current_official_ordinary", "scope_kind": "current_all_ordinary" if stock_ids is None else "explicit_current_ordinary_filter"}
    pairs, windows = [], {}
    for stock, market in requested_pairs:
        record = positives.get((stock, market))
        reason = None
        if record is None:
            reason = "current_official_classification_unknown"
        elif verified.get(market) is not True or market not in axes:
            reason = "market_calendar_applicability_or_known_range_unverified"
        else:
            scope_market = "TPEx" if market == "TPEX" else market
            history = context["application_scope"]["body"]["markets"][scope_market]["historical_sessions"][-253:]
            market_asof = market_asof_by_market.get(market, data_asof)
            if history and history[-1] > market_asof:
                # Price lags the scope: the exact 253 verified sessions ending at the
                # market's analysed day (only from the verified calendar axis; never invented).
                axis = [day for day in axes.get(market, []) if day <= market_asof]
                history = axis[-253:] if len(axis) >= 253 and axis[-1] == market_asof else []
            if len(history) != 253 or not history or history[-1] != market_asof:
                reason = "market_calendar_exact_253_sessions_unavailable"
            else:
                windows[market] = history
        if reason:
            coverage["excluded"].append({"stock_id": stock, "market": market or None, "reason": reason})
        else:
            pairs.append((stock, market))
    if not pairs:
        raise C0IntegrityError("c0_no_stock_with_verified_current_identity_and_exact_market_calendar:" +
                         json.dumps(coverage["excluded"], ensure_ascii=False))
    start = min(window[0] for window in windows.values())
    frames, query_bindings = {}, {}
    for route, items in catalog.items():
        item = items[0]
        selected = query.query_published_rows(item["root"], item["artifact_descriptor"], route,
            stock_ids=list(dict.fromkeys(stock for stock, _ in pairs)), start_date=start, end_date=data_asof,
            source_identity=item.get("source_identity"), parent_identity=item.get("parent_identity"),
            allow_build=True, consumer_temp_root=cache_dir)
        query_failed = True
        try:
            rows = list(selected.iter_rows(order="stock_shard") if _stock_shard_order else selected.iter_rows())
            frame = pd.DataFrame(rows)
            if frame.empty and route == "finmind_price_adj":
                # An empty stock query is absent history, not a fabricated price row.
                frame = pd.DataFrame(columns=["stock_id", "date", "close", "open", "spread",
                                              "Trading_Volume", "_shared_collected_at"])
            elif frame.empty and route == "official_market_daily" and frames["finmind_price_adj"].empty:
                frame = pd.DataFrame(columns=["stock_id", "date", "close", "volume", "market"])
            if ((frame.empty and route != "finmind_price_adj" and not frames["finmind_price_adj"].empty)
                    or not {"stock_id", "date", "close"}.issubset(frame.columns)):
                raise C0IntegrityError("c0_verified_query_price_or_board_rows_empty_or_schema_invalid:" + route)
            frame["stock_id"] = frame["stock_id"].astype(str)
            frame["date"] = pd.to_datetime(frame["date"].map(_identity_day))
            if "market" in frame.columns:
                if route == "finmind_price_adj":
                    frame["market"] = frame["market"].astype("string").str.strip().str.upper()
                else:
                    frame["market"] = frame["market"].astype(str).str.upper()
            frames[route] = frame
            completion = _stable_file_bytes(selected.completion_path)
            index = _stable_file_bytes(selected.index_manifest_path)
            query_bindings[route] = {"cache_key": selected.cache_key, "predicate": selected.predicate,
                "completion_path": str(selected.completion_path), "completion_sha256": hashlib.sha256(completion).hexdigest(),
                "index_manifest_path": str(selected.index_manifest_path), "index_sha256": hashlib.sha256(index).hexdigest(),
                "lineage": selected.lineage, "query_metrics": dict(selected.metrics)}
            query_failed = False
        finally:
            close_query = getattr(selected, "close", None)
            if callable(close_query):
                if query_failed:
                    with contextlib.suppress(Exception):
                        close_query()
                else:
                    close_query()
    px, board = frames["finmind_price_adj"], frames["official_market_daily"]
    if not {"open", "spread", "Trading_Volume", "_shared_collected_at"}.issubset(px.columns):
        raise C0IntegrityError("c0_price_consistency_fields_missing")
    if not {"volume", "market"}.issubset(board.columns):
        raise C0IntegrityError("c0_official_board_consistency_fields_missing")
    price_positions = px.groupby("stock_id", sort=False).indices
    board_positions = board.groupby("stock_id", sort=False).indices
    window_bounds = {market: (pd.Timestamp(days[0]), pd.Timestamp(days[-1]))
                     for market, days in windows.items()}
    valid_pairs, kept = [], []
    for stock, market in pairs:
        # Anti-lookahead per market: never read a row after this market's own analysed day.
        market_start, market_end = window_bounds[market]
        price_index = price_positions.get(stock)
        board_index = board_positions.get(stock)
        stock_price = px.iloc[0:0] if price_index is None else px.iloc[price_index]
        stock_board = board.iloc[0:0] if board_index is None else board.iloc[board_index]
        stock_price = stock_price.loc[(stock_price["date"] >= market_start)
            & (stock_price["date"] <= market_end)]
        multiple_markets = len(positive_markets.get(stock, [])) > 1
        has_price_market = "market" in stock_price.columns
        ambiguous_price = multiple_markets and not has_price_market
        price_market_mismatch = False
        if has_price_market:
            stated_market = stock_price["market"].notna() & stock_price["market"].ne("")
            if multiple_markets:
                ambiguous_price = (not stated_market.all()
                    or not stock_price["market"].isin(positive_markets[stock]).all())
                if not ambiguous_price:
                    stock_price = stock_price.loc[stock_price["market"] == market]
        if ambiguous_price:
            stock_price = stock_price.iloc[0:0]
        price = stock_price.copy()
        official = stock_board.loc[(stock_board["date"] >= market_start)
            & (stock_board["date"] <= market_end)]
        if multiple_markets and has_price_market:
            official = official.loc[official["market"] == market]
        # Only fully equal business observations may collapse; conflicts stay visible.
        official = official.drop_duplicates(["stock_id", "date", "market", "close", "volume"], keep="first")
        reasons = ["price_stock_market_ambiguous"] if ambiguous_price else []
        notes, missing_reasons = [], []
        market_segment = None
        if price.duplicated(["stock_id", "date"]).any():
            reasons.append("duplicate_price_key")
        if not ambiguous_price:
            other_markets = set(official["market"]) - {market}
            if other_markets:
                # A current identity cannot qualify an earlier issuer/market.
                # Only a single, non-overlapping old->current observation change
                # can delimit the usable current segment; it never joins history.
                same_day_markets = official.groupby("date")["market"].nunique()
                ordered = official.sort_values("date", kind="mergesort")
                changes = ordered.loc[ordered["market"].ne(ordered["market"].shift()), ["date", "market"]]
                segment_usable = (set(official["market"]).issubset({"TWSE", "TPEX"})
                    and not same_day_markets.gt(1).any()
                    and len(changes) == 2
                    and changes.iloc[0]["market"] != market
                    and changes.iloc[1]["market"] == market
                    and ordered.iloc[-1]["date"] == market_end)
                if not segment_usable:
                    reasons.append("price_window_official_market_mismatch")
                elif official.duplicated(["stock_id", "date"]).any():
                    reasons.append("conflicting_official_board_key")
                else:
                    segment_start = changes.iloc[1]["date"]
                    previous_market = str(changes.iloc[0]["market"])
                    truncated_rows = int(price["date"].lt(segment_start).sum())
                    price = price.loc[price["date"] >= segment_start].copy()
                    official = official.loc[official["date"] >= segment_start].copy()
                    market_segment = {"status": "current_market_segment_only",
                        "start_date": segment_start.date().isoformat(), "end_date": market_end.date().isoformat(),
                        "previous_market": previous_market, "current_market": market,
                        "truncated_price_rows": truncated_rows, "retained_price_rows": int(len(price)),
                        "historical_issuer_continuity_verified": False, "historical_membership_verified": False}
                    notes.append(f"只使用 {market} 最新連續觀測段 {segment_start.date().isoformat()}～"
                        f"{market_end.date().isoformat()}；較早 {previous_market} 區段的 {truncated_rows} 筆價格未混接，"
                        "歷史發行人連續性未驗證。")
            elif official.duplicated(["stock_id", "date"]).any():
                reasons.append("conflicting_official_board_key")
        if has_price_market and not multiple_markets:
            # Optional missing market values may inherit the unique current
            # identity; an explicit mismatch in the selected segment may not.
            stated_market = price["market"].notna() & price["market"].ne("")
            price_market_mismatch = bool((stated_market & price["market"].ne(market)).any())
        if price_market_mismatch:
            reasons.append("price_window_identity_market_mismatch")
        if not len(price) and not ambiguous_price:
            reasons.append("price_data_unavailable")
        elif price["_shared_collected_at"].isna().any():
            reasons.append("price_provenance_observation_missing")
        if not reasons:
            try:
                checks = quality.series_checks(price, official, market_end, expected_sessions=windows[market])
                drift.extend(checks.get("version_labels"))
                for reason in checks["reasons"].get(stock, []):
                    if reason == "missing_rows":
                        missing_reasons.append(reason)
                    else:
                        reasons.append(reason)
            except quality.PriceSeriesQualityError as exc:
                reasons.append(str(exc))
        if not reasons and set(price["date"].dt.strftime("%Y-%m-%d")) - set(windows[market]):
            reasons.append("price_row_outside_exact_verified_market_axis")
        close = price.set_index("date")["close"].reindex(pd.DatetimeIndex(windows[market])) if not reasons else None
        if close is not None and (not np.isfinite(price["close"]).all() or not (price["close"] > 0).all()):
            reasons.append("price_close_invalid")
        actual_close = pd.to_numeric(price["close"], errors="coerce")
        actual_valid = actual_close.notna() & np.isfinite(actual_close) & (actual_close > 0)
        observed = int(price.loc[actual_valid, "date"].nunique())
        missing = len(windows[market]) - observed
        if close is not None and missing:
            missing_reasons.append("latest_exact_253_market_sessions_incomplete")
            notes.append(f"價格資料 {observed}／{len(windows[market])} 個交易日，缺 {missing} 日；分析可信度較低。")
        if not len(price):
            notes.append("無可用價格資料；價格相關指標無法計算。")
        assessment = {"schema": "stock-data-quality-assessment-v1", "stock_id": stock, "market": market,
            "stock_name": str(positives[(stock, market)].get("stock_name") or positives[(stock, market)].get("name") or stock),
            "expected_sessions": len(windows[market]), "observed_sessions": observed, "missing_sessions": missing,
            "first_price_date": price["date"].min().date().isoformat() if len(price) else None,
            "last_price_date": price["date"].max().date().isoformat() if len(price) else None,
            "status": "unavailable" if reasons else "partial" if missing_reasons else "complete",
            "reason_codes": sorted(set(reasons + missing_reasons)), "notes": notes, "imputed_features": []}
        if market_segment is not None:
            assessment["market_segment"] = market_segment
        assessments.append(assessment)
        if reasons:
            coverage["excluded"].append({"stock_id": stock, "market": market, "reason": ",".join(sorted(set(reasons)))})
        else:
            price["market"] = market; price["session_date"] = price["date"].dt.strftime("%Y-%m-%d")
            price["adjusted_close"] = price["close"]
            kept.append(price); valid_pairs.append([stock, market]); coverage["records"].append(positives[(stock, market)])
    coverage["eligible"] = len(valid_pairs)
    if not kept:
        error = C0IntegrityError("c0_no_stock_passed_current_price_scope_quality:" + json.dumps(coverage["excluded"], ensure_ascii=False))
        error.coverage = coverage
        error.data_quality_assessments = assessments
        raise error
    panel = pd.concat(kept, ignore_index=True)
    # Compare vintages once across the retained pairs; single-series checks cannot
    # identify a stock whose latest observed batch is older than another stock's.
    stamp_values = panel["_shared_collected_at"].astype(str)
    distinct_stamps = sorted(stamp_values.unique())
    latest_stamp = distinct_stamps[-1]
    if len(distinct_stamps) > quality.MAX_STAMP_VALUES:
        drift.note("price_series_stamp_values_exceed_expected", recorded=quality.MAX_STAMP_VALUES,
            actual=len(distinct_stamps), detail={"latest": latest_stamp, "earliest": distinct_stamps[0]})
    pair_stamps = stamp_values.groupby([panel["stock_id"], panel["market"]], sort=False).max()
    older_pairs = pair_stamps.index[pair_stamps.to_numpy() < latest_stamp]
    if len(older_pairs):
        examples = [[stock, str(pair_stamps.loc[(stock, market)])] for stock, market in list(older_pairs)[:12]]
        drift.note("price_series_vintage_stale", recorded=latest_stamp, actual=len(older_pairs),
            detail={"examples": examples, "stock_market_pairs": [list(pair) for pair in list(older_pairs)[:12]]})
    # scope["asof"] is the latest analysed day among the markets of the eligible
    # stocks; each market's own day is scope["asof_by_market"][market].  With one
    # common day (the usual case) every value below is exactly as before.
    eligible_markets = sorted({market for _, market in valid_pairs})
    eligible_asof = {market: windows[market][-1] for market in eligible_markets}
    result_asof = max(eligible_asof.values())
    per_market = len(set(eligible_asof.values())) > 1
    coverage["inference_asof"] = result_asof
    if per_market:
        coverage["inference_asof_by_market"] = copy.deepcopy(eligible_asof)
    if requested_asof is not None and requested_asof != result_asof:
        # Never a gate: the analysis uses the data actually published (and says so).
        drift.note("requested_asof_differs", recorded=requested_asof, actual=result_asof,
            detail={"data_is_older_than_requested": result_asof < requested_asof})
    scope_window = {"stock_market_pairs": valid_pairs, "start": start, "end": result_asof, "exact_window_sessions": 253}
    if per_market:
        scope_window["end_by_market"] = copy.deepcopy(eligible_asof)
    source_binding = {"snapshot_fingerprint": sources.fingerprint, "asof": result_asof,
        "current_ordinary_identity": identity["binding"], "calendar_evidence": calendar,
        "route_statuses": {route: value["status"] for route, value in context["required_route_evidence"].items()},
        "required_route_evidence": context["required_route_evidence"], "readiness": context["readiness"],
        "application_retention_scope": context["application_scope"]["binding"],
        "application_capture_scope": context["application_scope"]["capture_binding"],
        "query_bindings": query_bindings, "scope": scope_window, "source_publication_time_verified": False}
    if per_market:
        source_binding["asof_by_market"] = copy.deepcopy(eligible_asof)
    evidence = {"current_ordinary_verified": True, "price_scope_verified": True, "snapshot_verified": True,
        "calendar_verified_by_market": {market: verified[market] for _, market in valid_pairs},
        "eligible_stock_market_pairs": valid_pairs, "known_future_information": False,
        "historical_membership_status": "unknown", "pit_status": "unknown", "corporate_actions_status": "unknown",
        "source_binding": source_binding, "stock_data_quality_assessments": assessments}
    descriptor = copy.deepcopy(context["descriptor"])
    if per_market:
        # Both dates are disclosed; the lagging market's stocks are analysed, not excluded.
        counts = {market: sum(1 for _, value in valid_pairs if value == market) for market in eligible_markets}
        drift.note("market_asof_differs_per_market_inference", recorded=result_asof, actual=copy.deepcopy(eligible_asof),
            detail={"stocks_by_market": counts, "analysis": "each_market_own_asof_and_exact_253_session_window"})
    collected = None
    try:
        stamps = pd.to_datetime(panel["_shared_collected_at"], utc=True, errors="coerce").dropna()
        collected = stamps.max().isoformat() if len(stamps) else None
    except (TypeError, ValueError):
        collected = None
    result = {"panel": panel, "market_sessions": {market: axes[market] for _, market in valid_pairs},
        "forward_market_sessions": copy.deepcopy(axes), "asof_by_market": copy.deepcopy(market_asof_by_market),
        "evidence": evidence, "policy_binding": copy.deepcopy(active["policy_binding"]), "source_binding": source_binding,
        "sources": sources, "application_scope": context["application_scope"], "current_identity": identity,
        "calendar_evidence": calendar, "required_route_evidence": context["required_route_evidence"],
        "readiness": context["readiness"], "descriptor": descriptor, "coverage": coverage, "asof": result_asof,
        "data_quality_assessments": assessments,
        **_dual_bindings(active)}
    result["version_labels"] = drift.labels
    result["data_time"] = build_data_time(dict(context, descriptor=descriptor,
        analysis_asof_by_market=copy.deepcopy(market_asof_by_market)), analysis_asof=result_asof,
        price_max_date=maximum, price_collected_at_max=collected, requested_asof=requested_asof,
        labels=drift.labels, model=active)
    return result


def panel_by_market(scope):
    """``{market: (panel_subset, asof)}`` for a ``load_c0_published_panel`` result.

    Every market must be built with its OWN analysed day, e.g.
    ``linear.build_panel(subset, scope["market_sessions"], asof=asof, ...)``;
    ``scope["asof"]`` is only the latest of these days.  ``predict_c0_by_market``
    does the build/predict for all markets.
    """
    panel = scope["panel"]
    by_market = scope.get("asof_by_market") or {}
    output = {}
    for market in sorted(set(panel["market"].astype(str))):
        subset = panel.loc[panel["market"] == market].reset_index(drop=True)
        day = by_market.get(market, scope["asof"])
        # Missing tail quotes remain absent; build_panel retains their calendar
        # rows so prediction can report null without inventing a raw price.
        if str(subset["session_date"].max())[:10] > day:
            raise C0IntegrityError("c0_market_panel_endpoint_differs_from_market_asof:" + market)
        output[market] = (subset, day)
    return output


def predict_c0_by_market(model, scope, *, horizons, policy_binding=None, batch_pairs=None):
    """Build features and predict each market with its own analysed day.

    One common day (the usual case) is exactly the previous single
    ``build_panel(scope["panel"], ..., asof=scope["asof"])`` call.  Several days:
    each market separately (no feature uses a later day than its market's own),
    concatenated in the same (stock_id, market, horizon) order.  ``batch_pairs``
    optionally splits each build into ordered stock batches (same rows).
    Returns ``(prepared_by_market, predictions)``.
    """
    import pandas as pd
    import stock_daily_linear as linear
    binding = policy_binding if policy_binding is not None else scope["policy_binding"]
    parts = panel_by_market(scope)
    if len({day for _, day in parts.values()}) <= 1:
        parts = {"*": (scope["panel"], scope["asof"])}
    prepared, frames = {}, []
    for market, (panel, day) in parts.items():
        groups = panel.groupby(["stock_id", "market"], sort=True).indices
        pairs = sorted(groups)
        size = len(pairs) if not batch_pairs else int(batch_pairs)
        for start in range(0, len(pairs), max(size, 1)):
            rows = sorted(index for pair in pairs[start:start + size] for index in groups[pair])
            batch = panel if size >= len(pairs) else panel.iloc[rows]
            built = linear.build_panel(batch, scope["market_sessions"], asof=day, horizons=list(horizons),
                evidence=scope["evidence"], policy_binding=binding)
            prepared.setdefault(market, built)
            frames.append(linear.predict_baseline(model, built))
    if len(frames) == 1:
        return prepared, frames[0]
    predictions = pd.DataFrame([row for frame in frames for row in frame.to_dict("records")])
    order = {value: index for index, value in enumerate(sorted(set(int(h) for h in horizons)))}
    predictions["_order"] = predictions["horizon"].map(lambda value: order.get(int(value), len(order)))
    predictions = predictions.sort_values(["stock_id", "market", "_order"], kind="stable").drop(columns="_order")
    return prepared, predictions.reset_index(drop=True)

def c0_runtime_binding(context_or_sources=None, runtime_binding=None, *, consumer_id="single"):
    """Bind the same pinned job to its current contract and unchanged scientific model."""
    active = runtime_binding if runtime_binding is not None else load_active_c0_model(consumer_id=consumer_id)
    context = load_c0_published_context(context_or_sources, consumer_id=consumer_id, runtime_binding=active)
    descriptor = context["descriptor"]
    return {"schema": "stock-daily-c0-runtime-binding-v2", "asof": context["asof"],
        "snapshot_fingerprint": descriptor["snapshot_fingerprint"], "published_sources": descriptor["sources"],
        "identity_binding": context["current_identity"]["binding"],
        "calendar_binding": context["calendar_evidence"]["binding"],
        "calendar_verified_by_market": context["calendar_evidence"]["calendar_verified_by_market"],
        "application_scope_binding": context["application_scope"]["binding"],
        "capture_scope_binding": context["application_scope"]["capture_binding"],
        "required_route_evidence": context["required_route_evidence"], "readiness": context["readiness"],
        "model_binding": active["artifact_binding"], "pointer_binding": active["pointer_binding"],
        "policy_binding": active["policy_binding"], **_dual_bindings(active)}


def validate_c0_estimate(payload, pdf_bytes, *, stock_id, horizon,
                         expected_snapshot_fingerprint=None, allow_qa_override=False, expected_runtime_binding=None):
    """Verify an independently named C0 report against current published inputs/model.

    This does not upgrade research qualification or accept provisional raw files.
    Attachment root/path checks remain the delivery caller's responsibility.
    """
    import stock_daily_linear as linear
    if (not isinstance(payload, Mapping) or payload.get("report_schema_version") != C0_REPORT_SCHEMA
            or payload.get("report_class") != "daily_estimate_effect_unknown" or payload.get("profile") != "C0"
            or payload.get("source_mode") != "published" or payload.get("horizon_sessions") != horizon
            or payload.get("target_definition") != "signal_close_to_same_market_H_session_close"
            or str((payload.get("stock") or {}).get("stock_id")) != str(stock_id)):
        raise C0IntegrityError("c0_report_identity_schema_or_target_invalid")
    if payload.get("qa_override") is not False and not allow_qa_override:
        raise C0IntegrityError("c0_qa_model_override_cannot_be_formal_attachment")
    if (payload.get("historical_pit_verified") is not False or payload.get("historical_membership_verified") is not False
            or payload.get("corporate_actions_verified") is not False or payload.get("effectiveness_status") != "unknown"
            or payload.get("trading_effect") != "unverified"):
        raise C0IntegrityError("c0_report_claims_unverified_historical_or_effectiveness_status")
    unsigned = dict(payload); digest = unsigned.pop("estimate_binding_sha256", None)
    if hashlib.sha256(_canonical(unsigned)).hexdigest() != digest:
        raise C0IntegrityError("c0_report_binding_digest_mismatch")
    if (len(pdf_bytes) != payload.get("pdf_size") or hashlib.sha256(pdf_bytes).hexdigest() != payload.get("pdf_sha256")):
        raise C0IntegrityError("c0_report_pdf_digest_or_size_mismatch")
    import stock_daily_forward as forward
    drift = C0VersionDrift()
    snapshot = payload.get("published_snapshot") or {}
    report_head = ((snapshot.get("sources") or [{}]) or [{}])[0]
    # 1. Current head: information only (newer data never invalidates an older report).
    try:
        current_descriptor = _current_c0_sources().descriptor()
        current_head = ((current_descriptor.get("sources") or [{}]) or [{}])[0]
        if snapshot.get("snapshot_fingerprint") != current_descriptor["snapshot_fingerprint"]:
            drift.note("newer_data_available", recorded={"snapshot_fingerprint": snapshot.get("snapshot_fingerprint"),
                "published_at": report_head.get("published_at"), "head_sha256": report_head.get("manifest_sha256")},
                actual={"snapshot_fingerprint": current_descriptor["snapshot_fingerprint"],
                    "published_at": current_head.get("published_at"), "head_sha256": current_head.get("manifest_sha256")})
    except (OSError, ValueError) as exc:
        drift.note("current_head_unreadable", actual=type(exc).__name__ + ":" + str(exc)[:200])
    if expected_snapshot_fingerprint and snapshot.get("snapshot_fingerprint") != expected_snapshot_fingerprint:
        drift.note("job_published_snapshot_changed", recorded=expected_snapshot_fingerprint,
            actual=snapshot.get("snapshot_fingerprint"))
    # 2. Current model/policy versus the report's own recorded binding: labels.
    incoming_model = payload.get("model_binding") or {}
    try:
        loaded = load_active_c0_model(incoming_model.get("path") if payload.get("qa_override") else None, consumer_id="single",
            pointer_path=(payload.get("pointer_binding") or {}).get("path") if payload.get("qa_override") and allow_qa_override else None)
    except OSError as exc:
        # Only an absent current model is a label; tampering/integrity of the
        # current model (C0IntegrityError, artifact digest) still raises.
        loaded = None
        drift.note("current_model_unreadable", actual=type(exc).__name__ + ":" + str(exc)[:200])
    changed = []
    if loaded is not None:
        if loaded["artifact_binding"] != incoming_model or loaded["pointer_binding"] != payload.get("pointer_binding"):
            changed.append("model_binding")
        if loaded["policy_binding"] != payload.get("policy_binding"):
            changed.append("policy_binding")
        changed.extend(name for name, value in _dual_bindings(loaded).items() if payload.get(name) != value)
        if changed:
            drift.note("model_or_policy_changed_since_report", recorded=incoming_model.get("sha256"),
                actual=loaded["artifact_binding"].get("sha256"), detail={"fields": changed})
    # 3. Reproduce with the report's OWN recorded inputs (pinned head + recorded model).
    status, reproduced, runtime, repro = "reproduced", False, None, None
    try:
        sources = _published_sources({"root": report_head.get("root"), "manifest_path": report_head.get("manifest_path"),
            "manifest_sha256": report_head.get("manifest_sha256")})
        repro = loaded if loaded is not None and not changed else _report_runtime_binding(payload, incoming_model)
    except (FileNotFoundError, _ReportInputsGone) as exc:
        status = "not_reproducible_inputs_gone"
        drift.note("reproduction_inputs_gone", detail={"reason": str(exc)[:200]})
    inputs = None
    if repro is not None:
        try:
            inputs = load_c0_published_panel([str(stock_id)], context_or_sources=sources, consumer_id="single",
                cache_dir=Path(str(payload["output_json"])).parent / "verification_cache", runtime_binding=repro)
        except FileNotFoundError as exc:
            status = "not_reproducible_inputs_gone"
            drift.note("reproduction_inputs_gone", detail={"reason": str(exc)[:200]})
        except C0IntegrityError as exc:
            # Absent structures (e.g. a head published before application scopes
            # existed) make reproduction impossible; tampering still raises.
            if str(exc).split(":", 1)[0] not in _REPORT_INPUTS_ABSENT:
                raise
            status = "not_reproducible_inputs_gone"
            drift.note("reproduction_inputs_gone", detail={"reason": str(exc)[:200]})
    if inputs is not None:
        try:
            validate_selected_snapshot(snapshot, inputs["descriptor"])
        except C0IntegrityError as exc:
            if str(exc) not in {"daily_snapshot_identity_mismatch", "daily_snapshot_selected_artifact_mismatch"}:
                raise
            status = "not_reproducible_inputs_differ"
            drift.note("report_snapshot_descriptor_differs", detail={"reason": str(exc)})
        runtime = c0_runtime_binding(inputs, repro, consumer_id="single")
        if expected_runtime_binding is not None and _canonical(runtime) != _canonical(expected_runtime_binding):
            drift.note("job_runtime_contract_changed", detail={"fields": sorted(key for key in set(runtime) | set(expected_runtime_binding)
                if _canonical(runtime.get(key)) != _canonical(expected_runtime_binding.get(key)))})
        # The validated stock's own market day (a lagging market keeps its own asof).
        stock_asof = _stock_market_asof(inputs, stock_id)
        if stock_asof != payload.get("latest_data_date") or stock_asof != payload.get("analysis_asof"):
            status = "not_reproducible_inputs_differ"
            drift.note("report_asof_differs_from_inputs", recorded=payload.get("analysis_asof"), actual=stock_asof)
        data = payload.get("data_binding") or {}
        differing = [name for name, left, right in (
            ("current_ordinary_identity", data.get("current_ordinary_identity"), inputs["source_binding"]["current_ordinary_identity"]),
            ("calendar_evidence.binding", (data.get("calendar_evidence") or {}).get("binding"),
                inputs["source_binding"]["calendar_evidence"]["binding"]),
            ("route_statuses", data.get("route_statuses"), inputs["source_binding"]["route_statuses"]),
            ("application_retention_scope", data.get("application_retention_scope"), inputs["source_binding"]["application_retention_scope"]),
            ("application_capture_scope", data.get("application_capture_scope"), inputs["source_binding"]["application_capture_scope"]))
            if left != right]
        if differing:
            status = "not_reproducible_inputs_differ"
            drift.note("report_data_binding_differs", detail={"fields": differing})
        forward_binding = payload.get("forward_binding") or {}
        base = repro["artifact_binding"]["sha256"]
        if (forward_binding.get("schema") != "stock-daily-c0-forward-binding-v1" or forward_binding.get("base_model_version") != base
                or (not allow_qa_override and Path(forward_binding.get("db_path") or "").resolve() != Path(forward.DEFAULT_DB_PATH).resolve())):
            drift.note("report_forward_binding_differs", recorded=forward_binding.get("base_model_version"), actual=base,
                detail={"db_path": forward_binding.get("db_path")})
        if payload.get("coverage") != inputs["coverage"]:
            drift.note("report_inference_coverage_differs")
        training_pairs = repro["model"]["metadata"]["source_evidence"]["eligible_stock_market_pairs"]
        if payload.get("training_scope") != {"stock_count": len(training_pairs), "stock_market_pairs": training_pairs}:
            drift.note("report_training_scope_differs")
        if status == "reproduced":
            prepared = linear.build_panel(inputs["panel"], inputs["market_sessions"], asof=stock_asof, horizons=[horizon],
                evidence=inputs["evidence"], policy_binding=inputs["policy_binding"])
            prediction = linear.predict_baseline(repro["model"], prepared)
            selected = prediction.loc[(prediction.stock_id == str(stock_id)) & (prediction.horizon == horizon)]
            # With the same inputs the prediction must reproduce exactly (content KEEP).
            if len(selected) != 1 or selected.iloc[0]["prediction_status"] != "predicted":
                raise C0IntegrityError("c0_report_prediction_not_reproducible")
            rows = selected.to_dict("records")
            recorded = {(int(ref.get("horizon")), str(ref.get("market"))): ref.get("calibration")
                for ref in forward_binding.get("calibrations") or [] if isinstance(ref, Mapping) and ref.get("horizon") is not None}
            calibration = recorded.get((int(horizon), str(rows[0]["market"])))
            expected = forward.apply_forward_calibration(rows, calibration, base_model_version=base)[0]
            if forward_binding.get("db_path"):
                _, now_calibrations = _forward_calibrated_rows(selected.to_dict("records"), base, forward,
                    Path(forward_binding["db_path"]))
                if now_calibrations != forward_binding.get("calibrations"):
                    drift.note("forward_calibration_changed_since_report",
                        detail={"reproduced_with": "report_recorded_calibration"})
            actual = payload.get("prediction") or {}
            for name in ("prob_up", "expected_return", "prob_up_uncalibrated"):
                value = _number(actual.get(name)); reference = _number(expected.get(name))
                if value is None or reference is None or not math.isclose(value, reference, rel_tol=1e-12, abs_tol=1e-12):
                    raise C0IntegrityError("c0_report_prediction_value_mismatch:" + name)
            for name in ("target_session_date", "future_calendar_status", "calibration_status", "selected_direction_kind", "selected_return_kind",
                         "probability_constant_across_stocks", "expected_return_zero_baseline", "candidate_predictions", "calibration_version", "forward_calibration_status", "model_version"):
                if actual.get(name) != expected.get(name):
                    raise C0IntegrityError("c0_report_prediction_semantics_mismatch:" + name)
            if (payload.get("prob_up") != actual["prob_up"] or payload.get("expected_return") != actual["expected_return"]
                    or payload.get("target_session_date") != expected["target_session_date"]
                    or payload.get("future_calendar_status") != expected["future_calendar_status"]
                    or payload.get("calibration_status") != expected["calibration_status"]):
                raise C0IntegrityError("c0_report_public_prediction_fields_mismatch")
            reproduced = True
    reference = repro if repro is not None else (loaded or {})
    return {"schema": "stock-daily-c0-report-verification-v2", "verified": True,
        "snapshot_fingerprint": snapshot.get("snapshot_fingerprint"),
        "model_artifact_sha256": incoming_model.get("sha256"),
        "target_definition": payload["target_definition"], "historical_pit_verified": False,
        "effectiveness_status": "unknown", "prediction_reproduced": reproduced, "reproduction_status": status,
        "version_labels": drift.labels, "runtime_binding": runtime,
        **{key: copy.deepcopy(reference.get(key)) for key in _DUAL_BINDING_KEYS}}


_REPORT_INPUTS_ABSENT = frozenset({"c0_same_head_application_retention_scope_missing",
    "c0_required_route_reference_ambiguous_or_missing", "calendar_reference_ambiguous_or_missing",
    "calendar_current_bounded_verified_sidecar_missing", "current_identity_stock_info_reference_ambiguous_or_missing",
    "current_identity_pinned_sidecar_missing_or_scope_invalid", "c0_price_or_official_board_reference_ambiguous_or_missing"})


class _ReportInputsGone(Exception):
    """The report's own recorded inputs are no longer available (not an integrity failure)."""


def _report_runtime_binding(payload, incoming_model):
    """Rebuild the runtime binding a report was made with, from its own records.

    The recorded artifact must still exist and be byte-identical (sidecar
    verified); otherwise reproduction is reported as impossible, never faked.
    """
    import stock_daily_linear as linear
    path = Path(str(incoming_model.get("path") or ""))
    if not path.is_absolute() or not path.is_file():
        raise _ReportInputsGone("report_model_artifact_missing:" + str(path))
    if hashlib.sha256(_stable_file_bytes(path)).hexdigest() != incoming_model.get("sha256"):
        # Artifacts are content-addressed and written once: different bytes at the
        # recorded path are tampering, never "inputs gone" (integrity KEEP).
        raise C0IntegrityError("c0_report_model_artifact_bytes_differ:" + str(path))
    model = linear.load_artifact(path)
    binding = {key: copy.deepcopy(payload.get(key)) for key in _DUAL_BINDING_KEYS}
    binding.update(model=model, policy_binding=copy.deepcopy(payload.get("policy_binding")),
        artifact_binding=copy.deepcopy(dict(incoming_model)), pointer_binding=copy.deepcopy(payload.get("pointer_binding")),
        contract_adoption_status="recorded_by_report", version_labels=[])
    return binding


def _forward_context(inputs, loaded):
    artifacts = [item for item in inputs["descriptor"]["artifacts"] if item["route_id"] == "finmind_price_adj"]
    if len(artifacts) != 1:
        raise C0IntegrityError("c0_forward_price_artifact_reference_ambiguous_or_missing")
    price_sha = artifacts[0]["sha256"]
    feature = loaded["artifact_binding"]
    base = feature["sha256"]
    bindings = {"base_model_version": base, "model_sha256": base, "snapshot_binding": inputs["source_binding"],
        "price_artifact_sha256": price_sha, "price_basis": "adjusted_close", "feature_schema": feature["feature_schema"],
        "feature_order": feature["feature_order"], "policy_binding": inputs["policy_binding"],
        "current_policy_binding": loaded["current_policy_binding"],
        "original_model_feature_contract": loaded["original_model_feature_contract"],
        "effective_C0_semantics_projection_sha256": loaded["effective_C0_semantics_projection_sha256"],
        "model_activation_epoch": loaded["model_activation_epoch"],
        "contract_adoption_ref": loaded["contract_adoption_ref"],
        "original_ordinary_verified": inputs["evidence"].get("current_ordinary_verified") is True}
    scope = inputs["source_binding"]["scope"]
    evidence = dict(inputs["evidence"], price_artifact_sha256=price_sha, price_basis="adjusted_close",
                    price_scope_start=scope["start"], price_scope_end=scope["end"])
    return bindings, evidence


def _forward_retention_refs(loaded, forward):
    feature = loaded["artifact_binding"]
    current = {"base_model_version": feature["sha256"], "model_sha256": feature["sha256"],
        "profile_id": "C0", "model_activation_epoch": loaded["model_activation_epoch"],
        **{key: copy.deepcopy(loaded[key]) for key in ("effective_C0_semantics_projection_sha256",
            "original_effective_C0_semantics_projection_sha256", "contract_adoption_ref",
            "contract_adoption_verified", "current_policy_binding")},
        "original_anchor": copy.deepcopy(loaded["original_model_feature_contract"])}
    return {"schema": forward.RETENTION_REFS_SCHEMA, "current": current, "rollback": None,
        "pending": None, "checkpoint": None, "protected_record_ids": [], "protected_calibration_versions": []}


def _forward_calibrated_rows(rows, base, forward, database):
    groups = {}
    for row in rows:
        key = (int(row["horizon"]), str(row["market"]))
        groups.setdefault(key, []).append(row)
    output, references = [], []
    for (horizon, market), batch in groups.items():
        calibration = forward.latest_calibration(base, horizon, market, "adjusted_close", db_path=database)
        references.append({"horizon": horizon, "market": market, "price_basis": "adjusted_close", "calibration": calibration})
        output.extend(forward.apply_forward_calibration(batch, calibration, base_model_version=base))
    return output, references


def _forward_target_prices(inputs, bindings, evidence):
    """Query only same-head, accepted settlement endpoints, including exited stocks.

    The original ordinary qualification and signal scalar belong to the sealed
    forecast.  Current inference membership never qualifies or disqualifies it.
    No missing signal window is downloaded or reconstructed here.
    """
    import pandas as pd
    import stock_query_projection as query
    import stock_daily_price_quality as quality
    application = inputs.get("application_scope") or {}
    accepted = (application.get("body") or {}).get("forward_settlement", [])
    axes = inputs.get("forward_market_sessions", inputs["market_sessions"])
    verified = inputs["calendar_evidence"].get("calendar_verified_by_market", evidence["calendar_verified_by_market"])
    current_pairs = {tuple(pair) for pair in inputs["evidence"]["eligible_stock_market_pairs"]}
    market_end = _market_asof_map(inputs)
    market_start = _market_window_start_map(inputs)
    selected, blocked = [], []
    for row in accepted:
        pair = (str(row.get("stock_id") or ""), str(row.get("market") or "").upper())
        target, signal, horizon = row.get("target_date"), row.get("signal_date"), row.get("horizon")
        scalar = _number(row.get("signal_close"))
        axis = axes.get(pair[1]) or []
        reason = None
        end = min(evidence["price_scope_end"], market_end.get(pair[1], evidence["price_scope_end"]))
        # The market's own window start: a lagging market's earlier window never
        # makes another market's older target look covered by the panel.
        start = max(evidence["price_scope_start"], market_start.get(pair[1], evidence["price_scope_start"]))
        if pair in current_pairs and start <= str(target) <= end:
            continue  # Already covered by the current, fully verified 253-row panel.
        if (row.get("purpose") != "forward_settlement" or row.get("profile") != "C0"
                or row.get("record_kind") != "current_forecast" or row.get("original_ordinary") is not True
                or row.get("signal_scalar_stored") is not True or row.get("forward_eligible") is not True
                or row.get("target_mature") is not True or scalar is None or scalar <= 0):
            reason = "accepted_forward_qualification_invalid"
        elif not isinstance(row.get("record_id"), str) or not row["record_id"] or type(horizon) is not int or horizon <= 0:
            reason = "accepted_forward_record_or_horizon_unknown"
        elif verified.get(pair[1]) is not True or signal not in axis or target not in axis:
            reason = "accepted_forward_signal_or_target_axis_unavailable"
        elif axis.index(signal) + horizon >= len(axis) or axis[axis.index(signal) + horizon] != target:
            reason = "accepted_forward_exact_h_target_mismatch"
        elif target > inputs.get("asof_by_market", {}).get(pair[1], inputs["asof"]):
            reason = "accepted_forward_target_not_mature"
        if reason:
            blocked.append({"record_id": row.get("record_id"), "reason": reason})
        else:
            selected.append(row)
    if not selected:
        return None, {"selected_record_ids": [], "blocked": blocked, "query_bindings": []}
    sources = inputs["sources"]
    catalog = {route: tuple(sources.projection_artifacts(route))
        for route in ("finmind_price_adj", "official_market_daily")}
    if any(len(items) != 1 for items in catalog.values()):
        raise C0IntegrityError("c0_forward_endpoint_artifact_ambiguous_or_missing")
    if catalog["finmind_price_adj"][0]["artifact_descriptor"].get("sha256") != bindings["price_artifact_sha256"]:
        raise C0IntegrityError("c0_forward_endpoint_price_basis_artifact_mismatch")
    targets = {}
    for row in selected:
        targets.setdefault(row["target_date"], set()).add(str(row["stock_id"]))
    lookups, query_bindings = {}, []
    for target, stocks in sorted(targets.items()):
        frames = {}
        for route, items in catalog.items():
            item = items[0]
            result = query.query_published_rows(item["root"], item["artifact_descriptor"], route,
                stock_ids=sorted(stocks), start_date=target, end_date=target,
                source_identity=item.get("source_identity"), parent_identity=item.get("parent_identity"), allow_build=True)
            frame = pd.DataFrame(list(result.iter_rows()))
            columns = {"stock_id", "date", "close", "open", "spread", "Trading_Volume", "_shared_collected_at"} if route == "finmind_price_adj" else {"stock_id", "date", "close", "volume", "market"}
            if not frame.empty and not columns.issubset(frame.columns):
                raise C0IntegrityError("c0_forward_endpoint_native_schema_invalid:" + route)
            if frame.empty:
                frame = pd.DataFrame(columns=sorted(columns))
            frame["stock_id"] = frame["stock_id"].astype(str)
            frame["date"] = pd.to_datetime(frame["date"].map(_identity_day))
            frames[route] = frame
            query_bindings.append({"route_id": route, "target_date": target, "cache_key": result.cache_key,
                "predicate": result.predicate, "lineage": result.lineage,
                "completion_path": str(result.completion_path),
                "completion_sha256": hashlib.sha256(_stable_file_bytes(result.completion_path)).hexdigest(),
                "index_manifest_path": str(result.index_manifest_path),
                "index_sha256": hashlib.sha256(_stable_file_bytes(result.index_manifest_path)).hexdigest()})
        lookups[target] = frames
    prices, pairs = [], set()
    for row in selected:
        stock, market, target = str(row["stock_id"]), str(row["market"]).upper(), row["target_date"]
        frames = lookups[target]
        price = frames["finmind_price_adj"].loc[lambda frame: frame["stock_id"] == stock].copy()
        official = frames["official_market_daily"].loc[lambda frame: frame["stock_id"] == stock].copy()
        reason = None
        if len(price) != 1 or price["date"].dt.strftime("%Y-%m-%d").tolist() != [target]:
            reason = "forward_target_price_missing_or_duplicate"
        elif price["_shared_collected_at"].isna().any():
            reason = "forward_target_price_provenance_missing"
        elif len(official) and (official.duplicated(["stock_id", "date"]).any()
                or set(official["market"].astype(str).str.upper()) != {market}):
            reason = "forward_target_official_market_or_key_mismatch"
        else:
            value = _number(price.iloc[0]["close"])
            if value is None or value <= 0:
                reason = "forward_target_price_invalid"
            else:
                try:
                    checks = quality.series_checks(price, official, pd.Timestamp(target))
                    if checks["reasons"].get(stock):
                        reason = "forward_target_price_native_quality:" + ",".join(checks["reasons"][stock])
                except quality.PriceSeriesQualityError as exc:
                    reason = str(exc)
        pairs.add((stock, market))
        if reason:
            blocked.append({"record_id": row["record_id"], "reason": reason})
        else:
            prices.append({"stock_id": stock, "market": market, "session_date": target, "adjusted_close": value})
    endpoint_evidence = dict(evidence, calendar_verified_by_market=verified,
        price_scope_start=min(targets), price_scope_end=max(targets),
        forward_settlement_record_ids=[row["record_id"] for row in selected],
        source_binding={"published_source_binding": inputs["source_binding"],
            "purpose": "forward_settlement", "accepted_scope_binding": application.get("binding"),
            "selected_record_ids": [row["record_id"] for row in selected], "query_bindings": query_bindings,
            "missing_endpoints": blocked})
    return {"prices": prices, "scope_pairs": [list(pair) for pair in sorted(pairs)], "evidence": endpoint_evidence}, {
        "selected_record_ids": [row["record_id"] for row in selected], "blocked": blocked, "query_bindings": query_bindings}


def _market_asof_map(inputs):
    """Each market's analysed day (``asof_by_market``; older inputs: the one ``asof``)."""
    by_market = inputs.get("asof_by_market")
    if not isinstance(by_market, Mapping) or not by_market:
        axes = inputs.get("forward_market_sessions", inputs.get("market_sessions")) or {}
        return {str(market): inputs["asof"] for market in axes}
    return {str(market): str(day) for market, day in by_market.items()}


def _market_window_start_map(inputs):
    """Each eligible market's first panel day (its own exact 253-session window start)."""
    panel = inputs.get("panel")
    if not hasattr(panel, "groupby") or "market" not in getattr(panel, "columns", ()) or not len(panel):
        return {}
    return {str(market): str(day)[:10] for market, day in panel.groupby("market")["session_date"].min().items()}


def _stock_market_asof(inputs, stock_id):
    """The analysed day of ``stock_id``'s own market (``inputs["asof"]`` if unknown)."""
    markets = [str(pair[1]) for pair in inputs["evidence"]["eligible_stock_market_pairs"] if str(pair[0]) == str(stock_id)]
    return _market_asof_map(inputs).get(markets[0], inputs["asof"]) if len(markets) == 1 else inputs["asof"]


def _merge_counts(left, right):
    """Sum per-market settlement counters (numbers add, lists append, dicts recurse)."""
    output = copy.deepcopy(dict(left))
    for key, value in right.items():
        current = output.get(key)
        if isinstance(value, bool) or key not in output:
            output.setdefault(key, copy.deepcopy(value))
        elif isinstance(value, (int, float)) and isinstance(current, (int, float)) and not isinstance(current, bool):
            output[key] = current + value
        elif isinstance(value, Mapping) and isinstance(current, Mapping):
            output[key] = _merge_counts(current, value)
        elif isinstance(value, list) and isinstance(current, list):
            output[key] = current + copy.deepcopy(value)
    return output


def _settle_by_market(forward, inputs, prices, evidence, market_asof, database, retention_refs):
    """Settle current-panel rows; each market with its own analysed day.

    One common day is exactly the previous single call.  Otherwise each market is
    settled separately with ``asof`` and ``price_scope_end`` set to its own day,
    so a lagging market's pending rows wait instead of being marked unobserved.
    """
    pairs = [list(pair) for pair in inputs["evidence"]["eligible_stock_market_pairs"]]
    markets = sorted({str(market) for _, market in pairs})
    days = {market: min(market_asof.get(market, inputs["asof"]), inputs["asof"]) for market in markets}
    if len(set(days.values())) <= 1:
        return forward.settle_predictions(inputs["market_sessions"], prices, asof=inputs["asof"],
            scope_pairs=inputs["evidence"]["eligible_stock_market_pairs"], evidence=evidence,
            db_path=database, retention_refs=retention_refs)
    combined, labels, by_market = None, [], {}
    starts = _market_window_start_map(inputs)
    for market in markets:
        day = days[market]
        # The market's own window [start, day]: a pending row whose target lies
        # before this market's window is never marked unobserved here.
        start = max(evidence["price_scope_start"], starts.get(market, evidence["price_scope_start"]))
        subset = [row for row in prices if str(row["market"]) == market and start <= str(row["session_date"]) <= day]
        part = forward.settle_predictions(inputs["market_sessions"], subset, asof=day,
            scope_pairs=[pair for pair in pairs if str(pair[1]) == market],
            evidence=dict(evidence, price_scope_start=start, price_scope_end=day),
            db_path=database, retention_refs=retention_refs)
        by_market[market] = {"asof": day, "result": copy.deepcopy(part)}
        labels.extend(part.get("version_labels") or [])
        body = {key: value for key, value in part.items() if key != "version_labels"}
        combined = copy.deepcopy(body) if combined is None else _merge_counts(combined, body)
    combined["version_labels"] = labels
    combined["asof_by_market"] = dict(days)
    combined["by_market"] = by_market
    return combined


def process_c0_forward(predictions, inputs, loaded, *, db_path=None):
    """One common current-prediction/settlement/calibration flow; never retrain a model."""
    import stock_daily_forward as forward
    database = Path(db_path) if db_path is not None else forward.DEFAULT_DB_PATH
    rows = predictions.to_dict("records") if hasattr(predictions, "to_dict") else [dict(row) for row in predictions]
    for row in rows:
        if row.get("prediction_status") != "predicted":
            # Mixed prediction frames represent unavailable numeric values as NaN.
            for field in ("prob_up", "expected_return", "prob_up_uncalibrated", "expected_return_uncalibrated"):
                value = row.get(field)
                if isinstance(value, float) and math.isnan(value):
                    row[field] = None
    drift = C0VersionDrift()
    if loaded.get("effective_C0_semantics_projection_sha256") is None:
        # No adoption receipt: there is no semantics identity to group forward
        # statistics by.  Predictions are still shown, read-only calibrated.
        return _forward_skipped(rows, inputs, loaded, forward, database, drift, "adoption_semantics_unknown")
    bindings, evidence = _forward_context(inputs, loaded)
    if bindings["original_ordinary_verified"] is not True:
        raise C0IntegrityError("c0_forward_original_ordinary_scope_unverified")
    eligible = {tuple(value) for value in inputs["evidence"]["eligible_stock_market_pairs"]}
    if any((str(row["stock_id"]), str(row["market"])) not in eligible for row in rows):
        raise C0IntegrityError("c0_forward_prediction_outside_verified_ordinary_price_scope")
    market_asof = _market_asof_map(inputs)
    for row in rows:
        # Anti-lookahead per market: a forecast's signal day is never after its market's analysed day.
        day = market_asof.get(str(row["market"]), inputs["asof"])
        if str(row["session_date"]) > day:
            raise C0IntegrityError("c0_forward_prediction_after_market_asof:" + str(row["market"]))
    retention_refs = _forward_retention_refs(loaded, forward)
    retention = forward.sync_retention_refs(retention_refs, db_path=database)
    prices = inputs["panel"][["stock_id", "market", "session_date", "adjusted_close"]].to_dict("records")
    settlement = _settle_by_market(forward, inputs, prices, evidence, market_asof, database, retention_refs)
    endpoints, endpoint_binding = _forward_target_prices(inputs, bindings, evidence)
    endpoint_settlement = None
    if endpoints is not None:
        # Each selected target is already at or before its own market's analysed day.
        endpoint_asof = max([inputs["asof"]] + [market_asof[market] for _, market in endpoints["scope_pairs"]
                                                if market in market_asof])
        endpoint_settlement = forward.settle_predictions(inputs.get("forward_market_sessions", inputs["market_sessions"]),
            endpoints["prices"], asof=endpoint_asof, scope_pairs=endpoints["scope_pairs"], evidence=endpoints["evidence"],
            db_path=database, retention_refs=retention_refs)
    maturity = forward.process_maturity(db_path=database, retention_refs=retention_refs)
    result, calibrations = _forward_calibrated_rows(rows, bindings["base_model_version"], forward, database)
    closes = {(str(row["stock_id"]), str(row["market"]), str(row["session_date"])): float(row["adjusted_close"]) for row in prices}
    for row in result:
        row["signal_close"] = closes.get((str(row["stock_id"]), str(row["market"]), str(row["session_date"])))
        has_prediction = row.get("prediction_status") == "predicted" or row.get("prob_up") is not None
        if not has_prediction and isinstance(row["signal_close"], float) and math.isnan(row["signal_close"]):
            row["signal_close"] = None
        if has_prediction and (row["signal_close"] is None or not math.isfinite(row["signal_close"]) or row["signal_close"] <= 0):
            raise C0IntegrityError("c0_forward_signal_close_missing")
    # Plan default #1: an in-sample (data older than training) forecast is shown,
    # labelled, and never written to the forward ledger.
    in_sample = [row for row in result if row.get("in_sample_lookahead") is True]
    if in_sample:
        drift.note("in_sample_lookahead_not_recorded", actual=len(in_sample),
            detail={"forward_write": "skipped", "forward_statistics": "excluded",
                    "data_asof": in_sample[0].get("data_asof"), "model_training_asof": in_sample[0].get("model_training_asof")})
    data_lagged = [row for row in result if row.get("data_lagged") is True]
    if data_lagged:
        drift.note("skipped_data_lagged", actual=len(data_lagged),
            detail={"forward_write": "skipped", "forward_statistics": "excluded",
                    "rows": [{"stock_id": row["stock_id"], "market": row["market"],
                        "feature_data_date": row.get("feature_data_date", row.get("session_date")),
                        "analysis_asof": row.get("analysis_asof"), "data_lag_sessions": row.get("data_lag_sessions")}
                        for row in data_lagged]})
    recorded = forward.record_predictions([row for row in result
        if row.get("in_sample_lookahead") is not True and row.get("data_lagged") is not True],
        bindings, db_path=database, retention_refs=retention_refs)
    calendar_ref = inputs["calendar_evidence"]["binding"]["verified_market_calendar"]
    export_path = Path(database).resolve().parent / "pending_settlement_requirements.json"
    export_axes = inputs.get("forward_market_sessions", inputs["market_sessions"])
    pending = forward.export_pending_settlement_requirements(export_path, export_axes,
        calendar_verified_by_market=inputs["calendar_evidence"].get("calendar_verified_by_market", evidence["calendar_verified_by_market"]),
        calendar_ref_by_market={market: calendar_ref for market in export_axes},
        asof_by_market=inputs.get("asof_by_market", {market: inputs["asof"] for market in export_axes}), db_path=database,
        qa_override=db_path is not None)
    for part in (retention, settlement, endpoint_settlement, maturity, recorded, pending):
        if isinstance(part, Mapping):
            drift.extend(part.get("version_labels"))
    return {"predictions": result, "forward_binding": {"schema": "stock-daily-c0-forward-binding-v1",
        "db_path": str(database.resolve()), "base_model_version": bindings["base_model_version"],
        "price_artifact_sha256": bindings["price_artifact_sha256"], "price_basis": bindings["price_basis"],
        "calibrations": calibrations, "settlement": settlement, "maturity": maturity, "recorded": recorded,
        "skipped_data_lagged": len(data_lagged),
        "endpoint_settlement": endpoint_settlement, "endpoint_scope_binding": endpoint_binding,
        "retention": retention, "pending_settlement_export": pending,
        "model_activation_epoch": loaded["model_activation_epoch"],
        "effective_C0_semantics_projection_sha256": loaded["effective_C0_semantics_projection_sha256"],
        "current_policy_binding": loaded["current_policy_binding"], "contract_adoption_ref": loaded["contract_adoption_ref"],
        "contract_adoption_status": loaded.get("contract_adoption_status"), "version_labels": drift.labels}}


def _forward_skipped(rows, inputs, loaded, forward, database, drift, reason):
    """Display path when the forward ledger cannot be used: read-only calibration only."""
    base = loaded["artifact_binding"]["sha256"]
    result, calibrations = _forward_calibrated_rows(rows, base, forward, database)
    closes = {(str(row["stock_id"]), str(row["market"]), str(row["session_date"])): float(row["adjusted_close"])
        for row in inputs["panel"][["stock_id", "market", "session_date", "adjusted_close"]].to_dict("records")}
    for row in result:
        row["signal_close"] = closes.get((str(row["stock_id"]), str(row["market"]), str(row["session_date"])))
        if (row.get("prediction_status") != "predicted" and row.get("prob_up") is None
                and isinstance(row["signal_close"], float) and math.isnan(row["signal_close"])):
            row["signal_close"] = None
    drift.note("forward_ledger_skipped", actual=reason, detail={"forward_write": "skipped",
        "forward_statistics": "excluded", "contract_adoption_status": loaded.get("contract_adoption_status")})
    return {"predictions": result, "forward_binding": {"schema": "stock-daily-c0-forward-binding-v1",
        "db_path": str(Path(database).resolve()), "base_model_version": base, "price_basis": "adjusted_close",
        "calibrations": calibrations, "skipped": reason, "recorded": None,
        "skipped_data_lagged": sum(row.get("data_lagged") is True for row in result),
        "model_activation_epoch": loaded["model_activation_epoch"],
        "effective_C0_semantics_projection_sha256": loaded.get("effective_C0_semantics_projection_sha256"),
        "current_policy_binding": loaded["current_policy_binding"], "contract_adoption_ref": loaded.get("contract_adoption_ref"),
        "contract_adoption_status": loaded.get("contract_adoption_status"), "version_labels": drift.labels}}
