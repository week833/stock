"""Small temporary fixtures for selected-only read plans; run in centralized QA."""

from __future__ import annotations

import contextlib
import copy
import hashlib
import json
import os
import sys
import tempfile
from pathlib import Path
from types import SimpleNamespace

import pytest

SHARED = Path(__file__).resolve().parents[1]
for directory in (SHARED, SHARED.parent / "single_stock_analysis" / "src"):
    if str(directory) not in sys.path:
        sys.path.insert(0, str(directory))

import stock_daily_candidate_runtime as runtime
import stock_daily_selected_deployment as selected
import stock_query_projection as query


@pytest.fixture
def case(monkeypatch):
    with tempfile.TemporaryDirectory(prefix="selected-read-only-tests-") as directory:
        root = Path(directory)
        central, cache = root / "central", root / "consumer"
        central.mkdir()
        rows = [{"stock_id": "2330", "date": "2026-09-30", "revenue": 120, "available_time": "2026-10-10"},
            {"stock_id": "2317", "date": "2026-09-30", "revenue": 999},
            {"stock_id": "2330", "observation_date": "2026-08-31", "revenue": 100},
            {"period_end": "2026-09-30", "shared": 7},
            {"stock_id": "2330", "date": "unknown", "revenue": None},
            {"stock_id": "2330", "date": "2020-01-01", "revenue": 1}]
        raw = runtime._canonical({"records": rows})
        source = central / "source.json"
        source.write_bytes(raw)
        route, head = "finmind_month_revenue", "b" * 64
        item = {"root": central, "artifact_descriptor": {"path": "source.json",
            "sha256": hashlib.sha256(raw).hexdigest(), "size": len(raw), "status": "complete"},
            "source_identity": {"source": "fixture"}, "parent_identity": head}
        start, end, stocks = "2025-01-01", "2026-10-23", ["2330"]
        # Native shards start from parsed source bytes, including their field order.
        expected = [row for row in json.loads(raw)["records"]
            if runtime._row_selected(row, route, stocks, start, end)]
        state = SimpleNamespace(streams=0, exhausted=0, iterator_closed=0, examined=0,
            native_calls=0, native_closed=0, sources_closed=0, spool=False, native_failure=False, scratch=[])
        monkeypatch.setattr(runtime, "PUBLISHED_ROOT", central)

        class Sources:
            fingerprint = "c" * 64

            def projection_artifacts(self, requested):
                return (item,) if requested in {route, "official_calendar", "finmind_stock_info"} else ()

            def snapshot_descriptor(self):
                return {"sources": [{"root": str(central), "manifest_sha256": head}]}

            def close(self):
                state.sources_closed += 1

            def iter_artifact_rows(self, requested):
                assert requested == route
                state.streams += 1
                artifact = SimpleNamespace(content_sha256=item["artifact_descriptor"]["sha256"],
                    snapshot_id=head, status="complete", metadata={"selected_ref": {
                        "sha256": item["artifact_descriptor"]["sha256"], "size": len(raw)}})

                def iterator():
                    try:
                        current = source.read_bytes()
                        for row in json.loads(current)["records"]:
                            state.examined += 1
                            yield copy.deepcopy(row)
                        if len(current) != len(raw) or hashlib.sha256(current).hexdigest() != artifact.content_sha256:
                            raise ValueError("fixture source full hash/size mismatch at EOF")
                        state.exhausted += 1
                    finally:
                        state.iterator_closed += 1
                yield artifact, iterator()

        native_root = runtime.cache_lifecycle.source_registry(central)
        key = "a" * 64

        class NativeResult:
            def __init__(self):
                self.cache_key = key
                self.index_manifest_path = native_root / "indexes" / (key + ".json")
                self.completion_path = native_root / "complete" / (key + ".json")
                self.predicate = {"stock_ids": stocks, "start_date": start, "end_date": end,
                    "before_count": 0, "carry_dates": [], "carry_dates_by_stock": {}, "carry_dates_missing_stock": []}
                self.lineage, self.metrics = {"source": head}, {"cache_hit": True}
                self.consumer_temp_root = None
                count = query._MAX_OPEN_ROWS_SHARDS + 1 if state.spool else 1
                self.selected = [{"first_ordinal": 0, "last_ordinal": 10, "size_bytes": 1024,
                    "path": str(index)} for index in range(count)]

            def iter_rows(self):
                if state.spool:
                    assert self.consumer_temp_root is not None and Path(self.consumer_temp_root).is_dir()
                    path = Path(self.consumer_temp_root) / "fixture.sqlite"
                    path.write_bytes(b"temporary scratch")
                    state.scratch.append(path)
                if state.native_failure:
                    raise OSError("fixture native read failure")
                yield from copy.deepcopy(expected)

            def close(self):
                state.native_closed += 1

        def native(*args, **kwargs):
            assert kwargs["allow_build"] is False and kwargs.get("columns") is None
            state.native_calls += 1
            if not (native_root / "indexes" / (key + ".json")).is_file():
                raise query.ProjectionUnavailableError("projection_not_built")
            return NativeResult()

        monkeypatch.setattr(query, "query_published_rows", native)

        def seed_native():
            for name, content in (("indexes", b'{"fixture":"index"}'), ("complete", b'{"fixture":"complete"}')):
                path = native_root / name / (key + ".json")
                path.parent.mkdir(parents=True, exist_ok=True)
                path.write_bytes(content)

        def seed_cache():
            evidence = {"reader": "sealed_stream_fallback", "fallback_reason": "fixture original stream",
                "artifacts": [{"sha256": item["artifact_descriptor"]["sha256"], "snapshot_id": head, "status": "complete"}],
                "predicate": {"stock_ids": stocks, "start": start, "end": end},
                "source_provenance": runtime._cache_provenance(item)}
            return runtime._save_consumer_cache(cache, runtime._cache_identity(item, route),
                runtime._query_scope(route, stocks, start, end), expected, evidence)

        def forbid_writes():
            def forbidden(*args, **kwargs):
                pytest.fail("read-only mode attempted cache publication or GC")
            for name in ("_save_consumer_cache", "_save_cache_document", "_write_cache_component",
                    "_atomic_bytes", "_prune_after_cache_build"):
                monkeypatch.setattr(runtime, name, forbidden)

        def read(plan=None, *, legacy=False, legacy_binding=None, **kwargs):
            return runtime._read_route(Sources(), item, route, stocks, start, end, cache,
                read_only=True, persist_cache=False, reader_plan=plan, legacy_plan=legacy,
                legacy_binding=legacy_binding, **kwargs)

        def manifest(binding):
            return [{"route_id": route, "usage": "used", "status": "complete",
                "object": item["artifact_descriptor"], "query_binding": copy.deepcopy(binding)}]

        yield SimpleNamespace(root=root, central=central, cache=cache, source=source, raw=raw, rows=rows,
            expected=expected, route=route, head=head, item=item, start=start, end=end, stocks=stocks,
            state=state, read=read, manifest=manifest, seed_cache=seed_cache, seed_native=seed_native,
            forbid_writes=forbid_writes, native_root=native_root, key=key, Sources=Sources)


def take_plan(binding):
    return binding.pop("_selected_read_plan")


def cache_contents(root):
    if not root.exists():
        return {}
    return {path.relative_to(root).as_posix(): path.read_bytes() for path in root.rglob("*") if path.is_file()}


def test_cold_stream_does_not_create_consumer_root_and_exhausts_full_source(case):
    case.forbid_writes()
    rows, binding = case.read()
    plan = take_plan(binding)
    assert rows == case.expected and plan["reader"] == "sealed_stream_fallback"
    assert binding["fallback_reason"] == "read_only_verified_stream_v1"
    assert case.state.examined == len(case.rows) and case.state.exhausted == case.state.iterator_closed == 1
    assert not case.cache.exists() and not plan["files"]
    assert "consumer_cache" not in binding


def test_forced_stream_ignores_an_index_built_between_producer_and_validator(case):
    _, binding = case.read()
    plan = take_plan(binding)
    case.seed_native()
    case.forbid_writes()
    rows, reproduced = case.read(plan)
    assert rows == case.expected and take_plan(reproduced) == plan and reproduced == binding
    assert case.state.native_calls == 0 and case.state.exhausted == 2


def test_warm_cache_read_and_forced_reproduction_do_not_change_existing_files(case):
    case.seed_cache()
    before = cache_contents(case.cache)
    case.forbid_writes()
    rows, binding = case.read()
    plan = take_plan(binding)
    repeated, checked = case.read(plan)
    assert rows == repeated == case.expected
    assert plan == take_plan(checked) and checked == binding
    assert plan["reader"] == "consumer_sealed_cache"
    assert case.state.native_calls == case.state.streams == 0
    assert cache_contents(case.cache) == before
    assert not list(case.cache.rglob("*.lease.json"))


def test_forced_native_ignores_a_consumer_cache_created_later(case):
    case.seed_native()
    _, binding = case.read()
    plan = take_plan(binding)
    case.seed_cache()
    before = cache_contents(case.cache)
    case.forbid_writes()
    rows, checked = case.read(plan)
    assert rows == case.expected and take_plan(checked) == plan and checked == binding
    assert case.state.native_calls == case.state.native_closed == 2 and case.state.streams == 0
    assert cache_contents(case.cache) == before


@pytest.mark.parametrize("change", ["missing", "bytes", "escape"])
def test_forced_cache_reference_cannot_fallback_or_escape(case, change):
    case.seed_cache()
    _, binding = case.read()
    plan = take_plan(binding)
    path = case.cache / plan["files"]["manifest"]["path"]
    if change == "missing":
        path.unlink()
    elif change == "bytes":
        path.write_bytes(path.read_bytes() + b" ")
    else:
        plan["files"]["manifest"]["path"] = "../../source.json"
    case.forbid_writes()
    with pytest.raises((OSError, ValueError)):
        case.read(plan)
    assert case.state.native_calls == case.state.streams == 0


@pytest.mark.parametrize("kind", ["rows", "ordinals"])
def test_forced_cache_rehashes_rows_and_ordinals_even_with_same_size_and_mtime(case, kind):
    case.seed_cache()
    _, binding = case.read()
    plan = take_plan(binding)
    manifest = case.cache / plan["files"]["manifest"]["path"]
    document = runtime._cache_document(manifest)
    descriptor = document["rows"] if kind == "rows" else document["rows"]["ordinals"]
    path = manifest.parent.parent / kind / (descriptor["sha256"] + ".jsonl")
    raw, stamp = path.read_bytes(), path.stat()
    changed = raw.replace(b"120", b"999", 1) if kind == "rows" else b"2" + raw[1:]
    assert changed != raw and len(changed) == len(raw)
    path.write_bytes(changed)
    os.utime(path, ns=(stamp.st_atime_ns, stamp.st_mtime_ns))
    case.forbid_writes()
    with pytest.raises(ValueError):
        case.read(plan)
    assert case.state.native_calls == case.state.streams == 0


@pytest.mark.parametrize("field", ["route_id", "predicate", "source_identity", "parent_identity", "binding_sha256"])
def test_forced_plan_rejects_route_source_predicate_or_binding_changes(case, field):
    _, binding = case.read()
    plan = take_plan(binding)
    plan[field] = "invalid" if field != "binding_sha256" else "0" * 64
    case.forbid_writes()
    with pytest.raises(ValueError):
        case.read(plan)


def test_native_file_hash_change_is_failclosed_not_stream_fallback(case):
    case.seed_native()
    _, binding = case.read()
    plan = take_plan(binding)
    (case.native_root / plan["files"]["index"]["path"]).write_bytes(b"changed")
    case.forbid_writes()
    with pytest.raises(ValueError):
        case.read(plan)
    assert case.state.native_calls == case.state.native_closed == 1 and case.state.streams == 0


def test_native_without_spool_does_not_require_disk_capacity(case, monkeypatch):
    case.seed_native()
    monkeypatch.setattr(runtime.shutil, "disk_usage", lambda *args: pytest.fail("pure read must not check scratch space"))
    monkeypatch.setattr(runtime.tempfile, "gettempdir", lambda *args: pytest.fail("pure read must not resolve scratch"))
    monkeypatch.setattr(runtime.tempfile, "TemporaryDirectory", lambda *args, **kwargs:
        pytest.fail("pure read must not create scratch"))
    case.forbid_writes()
    rows, binding = case.read()
    assert rows == case.expected and take_plan(binding)["reader"] == "sealed_projection"
    assert not case.state.scratch and not case.cache.exists()


def test_auto_native_spool_low_space_chooses_stream_but_forced_native_refuses(case, monkeypatch):
    case.seed_native()
    case.state.spool = True
    free = [10 ** 12]
    monkeypatch.setattr(runtime.shutil, "disk_usage", lambda *args: SimpleNamespace(total=10 ** 12, free=free[0]))
    _, native_binding = case.read()
    native_plan = take_plan(native_binding)
    assert case.state.scratch and all(not path.exists() for path in case.state.scratch)
    free[0] = 0
    case.forbid_writes()
    rows, binding = case.read()
    assert rows == case.expected and take_plan(binding)["reader"] == "sealed_stream_fallback"
    count = case.state.streams
    with pytest.raises(ValueError, match="read_plan_native_unavailable"):
        case.read(native_plan)
    assert case.state.streams == count and all(not path.exists() for path in case.state.scratch)


def test_spool_capacity_is_based_on_selected_shards_and_failure_cleans_scratch(case, monkeypatch):
    case.seed_native()
    case.state.spool = True
    selected_size = (query._MAX_OPEN_ROWS_SHARDS + 1) * 1024
    monkeypatch.setattr(runtime.shutil, "disk_usage", lambda *args: SimpleNamespace(
        total=100 * 1024 ** 3, free=selected_size * 8 + 64 * 1024 * 1024 + 20 * 1024 ** 3 + 1))
    case.state.native_failure = True
    case.forbid_writes()
    with pytest.raises(OSError, match="fixture native"):
        case.read()
    assert case.state.scratch and all(not path.exists() for path in case.state.scratch)
    assert case.state.native_closed == 1 and case.state.streams == 0 and not case.cache.exists()


@pytest.mark.parametrize("total", [100 * 1024 ** 3, 1024 ** 4])
def test_spool_capacity_preserves_larger_of_twenty_gib_or_five_percent(case, monkeypatch, total):
    case.seed_native()
    case.state.spool = True
    peak = (query._MAX_OPEN_ROWS_SHARDS + 1) * 1024 * 8 + 64 * 1024 * 1024
    reserve = max(20 * 1024 ** 3, (total + 19) // 20)
    free = [peak + reserve]
    monkeypatch.setattr(runtime.shutil, "disk_usage", lambda *args: SimpleNamespace(total=total, free=free[0]))
    _, binding = case.read()
    assert take_plan(binding)["reader"] == "sealed_projection"
    free[0] -= 1
    case.forbid_writes()
    _, binding = case.read()
    assert take_plan(binding)["reader"] == "sealed_stream_fallback"
    assert all(not path.exists() for path in case.state.scratch)


@pytest.mark.parametrize("unavailable", ["disk_usage", "temp_root", "mkdir"])
def test_scratch_location_unavailable_falls_back_only_for_auto_reader(case, monkeypatch, unavailable):
    case.seed_native()
    case.state.spool = True
    monkeypatch.setattr(runtime.shutil, "disk_usage", lambda *args: SimpleNamespace(total=10 ** 12, free=10 ** 12))
    _, binding = case.read()
    native_plan = take_plan(binding)

    def unusable(*args, **kwargs):
        raise OSError("fixture scratch location unavailable")

    if unavailable == "disk_usage":
        monkeypatch.setattr(runtime.shutil, "disk_usage", unusable)
    elif unavailable == "temp_root":
        monkeypatch.setattr(runtime.tempfile, "gettempdir", unusable)
    else:
        monkeypatch.setattr(runtime.tempfile, "TemporaryDirectory", unusable)
    case.forbid_writes()
    rows, binding = case.read()
    assert rows == case.expected and take_plan(binding)["reader"] == "sealed_stream_fallback"
    count = case.state.exhausted
    with pytest.raises(ValueError, match="read_plan_native_unavailable"):
        case.read(native_plan)
    assert case.state.exhausted == count and all(not path.exists() for path in case.state.scratch)


def test_query_integrity_error_is_not_scratch_unavailability(case, monkeypatch):
    case.seed_native()
    case.state.spool = True
    monkeypatch.setattr(runtime.shutil, "disk_usage", lambda *args: SimpleNamespace(total=10 ** 12, free=10 ** 12))
    original = query._ordinal_components

    def malformed(verified):
        original(verified)
        raise query.ProjectionIntegrityError("fixture native integrity failure")

    monkeypatch.setattr(query, "_ordinal_components", malformed)
    case.forbid_writes()
    with pytest.raises(query.ProjectionIntegrityError, match="native integrity"):
        case.read()
    assert case.state.streams == 0 and case.state.native_closed == 1


def test_stream_checks_hash_and_size_at_eof_without_disk_copy(case):
    case.source.write_bytes(case.raw + b" ")
    case.forbid_writes()
    with pytest.raises(ValueError, match="full hash/size"):
        case.read()
    assert case.state.iterator_closed == 1 and not case.cache.exists()


def test_legacy_cache_plan_keeps_binding_and_missing_reference_fails_without_rebuild(case):
    case.seed_cache()
    args = (case.cache, runtime._cache_identity(case.item, case.route),
        runtime._query_scope(case.route, case.stocks, case.start, case.end),
        case.route, case.stocks, case.start, case.end)
    _, original = runtime._read_consumer_cache(*args)
    plan = runtime.read_plan_from_manifest(case.manifest(original), case.cache, case.head)
    case.forbid_writes()
    rows, binding = case.read(plan["routes"][case.route], legacy=True,
        legacy_binding=plan["legacy_bindings"][case.route])
    assert rows == case.expected and take_plan(binding) == plan["routes"][case.route] and binding == original
    Path(original["cache_manifest_path"]).unlink()
    with pytest.raises((OSError, ValueError)):
        runtime.read_plan_from_manifest(case.manifest(original), case.cache, case.head)
    assert case.state.native_calls == case.state.streams == 0


def test_default_runtime_read_route_still_saves_cache_and_keeps_old_projection_failure(case, monkeypatch):
    saved = []
    monkeypatch.setattr(runtime, "_read_consumer_cache", lambda *args: None)
    monkeypatch.setattr(runtime, "_save_consumer_cache", lambda *args: saved.append(args) or {"fixture": True})
    with pytest.raises(runtime.CandidateProjectionNotReady):
        runtime._read_route(None, case.item, case.route, case.stocks, case.start, case.end, case.cache)
    case.seed_native()
    rows, binding = runtime._read_route(None, case.item, case.route, case.stocks, case.start, case.end, case.cache)
    assert rows == case.expected and len(saved) == 1 and binding["consumer_cache"] == {"fixture": True}
    assert "read_plan_policy" not in binding and "_selected_read_plan" not in binding


@pytest.mark.parametrize("field", ["integrity_sha256", "head_sha256", "cache_root", "schema", "storage_policy", "routes"])
def test_sealed_plan_rejects_invalid_shape_scope_or_digest(case, field):
    _, binding = case.read()
    plan = runtime._empty_read_plan(case.cache, case.head)
    plan["routes"][case.route] = take_plan(binding)
    plan = runtime._seal_read_plan(plan)
    plan[field] = {} if field == "routes" else "invalid"
    with pytest.raises(ValueError, match="read_plan"):
        runtime._check_read_plan(plan, case.cache, case.head, [case.route])


def test_filtered_rows_frames_availability_and_fixture_prediction_are_equivalent(case):
    streamed, _ = case.read()
    case.seed_native()
    native, _ = case.read()
    case.seed_cache()
    cached, _ = case.read()
    assert streamed == native == cached == case.expected
    frames = [runtime._normalise_frame(rows) for rows in (streamed, native, cached)]
    for frame in frames[1:]:
        assert frame.equals(frames[0])
    # Same raw input/PIT columns and order must reach the frozen feature/predict layers.
    assert "available_time" in frames[0] and frames[0]["revenue"].sum() == frames[1]["revenue"].sum()
    expected = [runtime._availability({"route_id": case.route, "usage": "used", "reason_zh": "fixture"},
        case.item, frame, {}, case.stocks) for frame in frames]
    assert expected[0] == expected[1] == expected[2]


@pytest.fixture
def loader(case, monkeypatch):
    import pandas as pd

    plan = [{"route_id": route, "usage": usage, "name_zh": route, "reason_zh": "fixture"}
        for route, usage in ((case.route, "used"), ("official_calendar", "context-only"),
            ("finmind_stock_info", "context-only"), ("finmind_dividend", "used"),
            ("finmind_gold_price", "not-selected"))]
    context = {"sources": case.Sources(), "current_identity": {"records": [
        {"stock_id": "2330", "stock_name": "Fixture", "market": "TWSE"}], "binding": {"fixture": "identity"}},
        "asof_by_market": {"TWSE": case.end}, "market_sessions": {"TWSE": ["2025-04-23", case.end]},
        "calendar_evidence": {"calendar_verified_by_market": {"TWSE": True}},
        "application_scope": {"binding": {"fixture": "retention"}, "capture_binding": {"fixture": "capture"}}}

    def build(panel, axes, **kwargs):
        frame = kwargs["source_frames"].get(case.route, pd.DataFrame())
        return {"metadata": {"feature_schema": runtime.FEATURE_SCHEMA,
                "source_manifest": copy.deepcopy(kwargs["source_manifest"])},
            "fixture_feature_values": frame.to_json(orient="split", date_format="iso")}

    features = SimpleNamespace(build_candidate_features=build)
    original_import = runtime.importlib.import_module
    monkeypatch.setattr(runtime.importlib, "import_module", lambda name, *args, **kwargs:
        features if name == "stock_daily_feature_catalog" else original_import(name, *args, **kwargs))
    monkeypatch.setattr(runtime, "_source_plan", lambda *args: copy.deepcopy(plan))
    monkeypatch.setattr(runtime, "_price_panel", lambda *args: (pd.DataFrame(), [{"status": "complete"}]))

    def load(**kwargs):
        return runtime.load_candidate_inputs(case.stocks, context=context, cache_dir=case.cache,
            horizons=(63,), **kwargs)

    return SimpleNamespace(load=load, context=context, plan=plan, features=features)


@pytest.mark.parametrize("flags", [{"read_only": True}, {"persist_cache": False}])
def test_loader_read_only_never_prunes_and_reproduces_context_missing_and_unselected_routes(case, loader, flags):
    case.forbid_writes()
    produced = loader.load(**flags)
    case.seed_native()
    checked = loader.load(read_plan=produced["read_plan"], **flags)
    assert checked["read_plan"] == produced["read_plan"]
    assert checked["source_manifest"] == produced["source_manifest"]
    assert checked["prepared"] == produced["prepared"] and checked["snapshot"] == produced["snapshot"]
    assert set(produced["read_plan"]["routes"]) == {entry["route_id"] for entry in loader.plan}
    assert produced["read_plan"]["routes"]["official_calendar"]["reader"] == "verified_context"
    assert produced["read_plan"]["routes"]["finmind_dividend"]["reader"] == "missing"
    assert produced["read_plan"]["routes"]["finmind_gold_price"]["reader"] == "not-selected"
    assert "consumer_cache_cleanup" not in produced["snapshot"] and not case.cache.exists()
    assert case.state.native_calls == 0 and case.state.exhausted == 2


def test_loader_default_still_persists_and_prunes_but_read_only_features_and_window_are_equal(case, loader, monkeypatch):
    pruned = []
    monkeypatch.setattr(runtime, "_prune_after_cache_build", lambda *args: pruned.append(args) or {"fixture": True})
    original = loader.load(allow_full_scan=True)
    assert case.cache.exists() and len(pruned) == 1
    assert original["snapshot"]["consumer_cache_cleanup"] == {"fixture": True}
    before = cache_contents(case.cache)
    case.forbid_writes()
    readonly = loader.load(read_only=True)
    assert readonly["prepared"]["fixture_feature_values"] == original["prepared"]["fixture_feature_values"]
    assert readonly["snapshot"]["window_by_market"] == original["snapshot"]["window_by_market"]
    assert readonly["source_frames"][case.route].equals(original["source_frames"][case.route])
    assert readonly["source_frames"][case.route]["available_time"].equals(
        original["source_frames"][case.route]["available_time"])
    assert cache_contents(case.cache) == before and len(pruned) == 1


def test_legacy_optional_not_ready_is_preserved_when_an_index_appears(case, loader):
    original = loader.load()
    baseline_native_calls = case.state.native_calls
    assert baseline_native_calls == 1
    legacy = runtime.read_plan_from_manifest(original["source_manifest"], case.cache, case.head)
    assert legacy["routes"][case.route]["reader"] == "not_ready"
    case.seed_native()
    before = cache_contents(case.root)
    case.forbid_writes()
    reproduced = loader.load(read_only=True, read_plan=legacy)
    assert reproduced["source_manifest"] == original["source_manifest"]
    assert reproduced["prepared"]["fixture_feature_values"] == original["prepared"]["fixture_feature_values"]
    assert case.state.native_calls == baseline_native_calls and case.state.streams == 0
    assert cache_contents(case.root) == before


@pytest.mark.parametrize("flags", [{"consumer_id": "other", "read_only": True},
    {"read_only": "yes"}, {"persist_cache": None}, {"read_plan": {}}])
def test_read_only_flags_are_single_consumer_and_explicit(case, loader, flags):
    with pytest.raises(ValueError, match="read_only"):
        loader.load(**flags)
    assert not case.cache.exists()


def validator_fixture(case, monkeypatch, *, legacy=False, reader="stream"):
    if reader == "cache":
        case.seed_cache()
    elif reader == "native":
        case.seed_native()
    if legacy:
        _, binding = runtime._read_route(case.Sources(), case.item, case.route, case.stocks,
            case.start, case.end, case.cache, allow_full_scan=True)
        plan = None
    else:
        _, binding = case.read()
        plan = runtime._empty_read_plan(case.cache, case.head)
        plan["routes"][case.route] = take_plan(binding)
        plan = runtime._seal_read_plan(plan)
    deployment = {"schema": selected.DEPLOYMENT_SCHEMA, "cache_dir": str(case.cache), "model_sha256": "d" * 64}
    payload = {"schema": selected.REPORT_SCHEMA, "language": "zh-TW", "research_only": False,
        "promotion": True, "deployment_status": "active", "deployment_binding": deployment,
        "stock": {"stock_id": "2330"}, "horizon_sessions": 63, "inference_horizons": [63],
        "snapshot_binding": {"head_sha256": case.head, "snapshot_fingerprint": "c" * 64},
        "prediction": {"expected_return": 0.015, "prob_up": 0.6}, "feature_values": [120, 100],
        "source_manifest": case.manifest(binding), "limitations": []}
    if not legacy:
        payload.update(storage_policy=selected.READ_ONLY_STORAGE_POLICY, read_plan=plan)
    job = {"schema": "single-stock-selected-runtime-binding-v1", "deployment_binding": deployment,
        "head_sha256": case.head, "snapshot_fingerprint": "c" * 64}
    if not legacy:
        job["storage_policy"] = selected.READ_ONLY_STORAGE_POLICY
    observed = SimpleNamespace(inferences=0, receipts=0, bindings=0, fail=False, changed=False)

    def validate_binding(value):
        assert value == deployment
        observed.bindings += 1
        return copy.deepcopy(value)

    def receipt(value):
        assert value == deployment
        observed.receipts += 1

    def inference(stock, current, horizons, cache, head, *, read_only, read_plan):
        observed.inferences += 1
        assert read_only is True and read_plan["legacy"] is legacy
        assert stock == "2330" and current == deployment and horizons == (63,) and head == case.head
        runtime._check_read_plan(read_plan, cache, head, [case.route])
        _, actual = case.read(read_plan["routes"][case.route], legacy=legacy,
            legacy_binding=read_plan.get("legacy_bindings", {}).get(case.route))
        actual_plan = take_plan(actual)
        assert actual_plan == read_plan["routes"][case.route]
        if observed.fail:
            raise RuntimeError("fixture inference failure")
        expected = copy.deepcopy(payload)
        expected["source_manifest"] = case.manifest(actual)
        if observed.changed:
            expected["prediction"]["prob_up"] = 0.9
        return [expected]

    monkeypatch.setattr(selected, "_validate_binding", validate_binding)
    monkeypatch.setattr(selected, "_validate_receipt", receipt)
    monkeypatch.setattr(selected, "_inference", inference)
    report = SimpleNamespace(build_report_sections=lambda value: [], _digest=selected._digest,
        _validate_sidecar_payload=lambda *args: None, _inspect_structure=lambda *args: {"fixture_only": True})
    raw = b"%PDF-1.4\nOffline read-only validator fixture.\n%%EOF\n"

    def validate():
        payload["runtime_payload_sha256"] = selected._digest(
            {key: value for key, value in payload.items() if key != "runtime_payload_sha256"})
        bundle = {"payload": copy.deepcopy(payload), "sections": [], "sections_sha256": selected._digest([]),
            "pdf": {"sha256": hashlib.sha256(raw).hexdigest(), "size": len(raw)}}
        return selected._validate_selected_report(bundle, raw, stock_id="2330", horizon=63,
            expected_runtime_binding=job, require_active=True, report=report)

    return SimpleNamespace(payload=payload, plan=plan, validate=validate, observed=observed, job=job)


@pytest.mark.parametrize("legacy", [False, True])
@pytest.mark.parametrize("reader", ["stream", "native", "cache"])
def test_selected_new_and_old_validation_are_read_only_and_independently_reproduce(case, monkeypatch, legacy, reader):
    fixture = validator_fixture(case, monkeypatch, legacy=legacy, reader=reader)
    before = cache_contents(case.root)
    initial = (case.state.streams, case.state.native_calls)
    case.forbid_writes()
    first, second = fixture.validate(), fixture.validate()
    assert first["valid"] is True, first.get("errors")
    assert second == first and fixture.observed.inferences == fixture.observed.receipts == fixture.observed.bindings == 2
    assert cache_contents(case.root) == before
    assert case.state.streams - initial[0] == (2 if reader == "stream" else 0)
    assert case.state.native_calls - initial[1] == (2 if reader == "native" else 0)
    if fixture.plan is not None:
        assert "artifact_descriptor" not in json.dumps(fixture.plan)
        assert "consumer_cache" not in fixture.payload["source_manifest"][0]["query_binding"]


@pytest.mark.parametrize("change", ["unknown", "null", "missing", "full_downgrade", "legacy_plan", "bad_hash"])
def test_selected_policy_or_plan_cannot_be_unknown_or_downgraded(case, monkeypatch, change):
    fixture = validator_fixture(case, monkeypatch)
    payload = fixture.payload
    if change == "unknown":
        payload["storage_policy"] = "other-policy"
    elif change == "null":
        payload["storage_policy"] = None
    elif change in {"missing", "full_downgrade"}:
        payload.pop("storage_policy")
        payload.pop("read_plan")
        if change == "full_downgrade":
            payload["source_manifest"][0]["query_binding"].pop("read_plan_policy")
    elif change == "legacy_plan":
        payload["read_plan"]["legacy"] = True
    else:
        payload["read_plan"]["integrity_sha256"] = "0" * 64
    case.forbid_writes()
    result = fixture.validate()
    assert result["valid"] is False and result["prediction_reproduced"] is False
    assert fixture.observed.inferences == (1 if change == "bad_hash" else 0)


@pytest.mark.parametrize("legacy", [False, True])
@pytest.mark.parametrize("change", ["missing_cache", "inference_exception", "prediction"])
def test_selected_old_and_new_evidence_or_prediction_failure_never_builds_cache(case, monkeypatch, legacy, change):
    fixture = validator_fixture(case, monkeypatch, legacy=legacy, reader="cache")
    if change == "missing_cache":
        Path(fixture.payload["source_manifest"][0]["query_binding"]["cache_manifest_path"]).unlink()
    fixture.observed.fail = change == "inference_exception"
    fixture.observed.changed = change == "prediction"
    case.forbid_writes()
    result = fixture.validate()
    assert result["valid"] is False and result["prediction_reproduced"] is False
    assert case.state.streams == case.state.native_calls == 0
    fixture.observed.fail = fixture.observed.changed = False
    if change != "missing_cache":
        assert fixture.validate()["valid"] is True


@pytest.mark.parametrize("read_only", [False, True])
def test_selected_inference_keeps_frozen_model_feature_checks_and_passes_explicit_io_mode(case, monkeypatch, read_only):
    feature_path, model_path = case.root / "features.py", case.root / "model.json"
    feature_raw, model_raw = b"# immutable fixture feature source\n", b'{"fixture":"immutable model weights"}\n'
    feature_path.write_bytes(feature_raw)
    model_path.write_bytes(model_raw)
    plan = runtime._seal_read_plan(runtime._empty_read_plan(case.cache, case.head))
    binding = {"model_sha256": hashlib.sha256(model_raw).hexdigest()}
    model = {"metadata": {"runtime_input_contract": {"horizons": [63],
            "feature_module_sha256": hashlib.sha256(feature_raw).hexdigest(),
            "source_plan_by_horizon": {"63": []}}, "snapshot_binding": {"head_sha256": case.head}},
        "horizons": {"63": {"selected": {}}}}
    original_import = selected.importlib.import_module
    monkeypatch.setattr(selected.importlib, "import_module", lambda name, *args, **kwargs:
        SimpleNamespace(__file__=str(feature_path)) if name == "stock_daily_feature_catalog"
        else original_import(name, *args, **kwargs))
    observed = SimpleNamespace(models=0, features=0, inputs=[], predictions=0)

    def load_model(current):
        assert current == binding and selected._file_sha(model_path) == binding["model_sha256"]
        observed.models += 1
        return copy.deepcopy(model), model_path

    def load_inputs(ids, **kwargs):
        observed.inputs.append(copy.deepcopy(kwargs))
        assert ids == ["2330"]
        if read_only:
            assert kwargs["read_only"] is True and kwargs["persist_cache"] is False
            assert kwargs["read_plan"] == plan and kwargs["allow_full_scan"] is True
        else:
            assert not kwargs["allow_full_scan"] and not {"read_only", "persist_cache", "read_plan"} & kwargs.keys()
        return {"prepared": {}, "source_manifest": [], "snapshot": {"head_sha256": case.head}, "read_plan": plan}

    def frozen(*args):
        observed.features += 1

    def predict(current, path, inputs, horizons, **kwargs):
        assert current == model and path == model_path and horizons == (63,)
        assert kwargs["expected_model_sha256"] == binding["model_sha256"]
        observed.predictions += 1
        return [{"horizon_sessions": 63, "feature_values": [120, 100], "prediction": {"expected_return": 0.015}}]

    monkeypatch.setattr(selected, "_load_model", load_model)
    monkeypatch.setattr(selected, "load_selected_context", lambda head, current:
        {"sources": case.Sources(), "current_identity": {"records": []}})
    monkeypatch.setattr(runtime, "_resolve_stocks", lambda *args: ["2330"])
    monkeypatch.setattr(runtime, "load_candidate_inputs", load_inputs)
    monkeypatch.setattr(runtime, "_apply_frozen_schema", frozen)
    monkeypatch.setattr(runtime, "_predict_payloads", predict)
    for _ in range(2):
        payload = selected._inference("2330", binding, (63,), case.cache, case.head,
            read_only=read_only, read_plan=plan if read_only else None)[0]
        assert payload["prediction"] == {"expected_return": 0.015} and payload["feature_values"] == [120, 100]
        if read_only:
            assert payload["storage_policy"] == "single-stock-read-only-inputs-v1" and payload["read_plan"] == plan
        else:
            assert "storage_policy" not in payload and "read_plan" not in payload
    assert observed.models == observed.features == observed.predictions == case.state.sources_closed == 2
    assert len(observed.inputs) == 2 and feature_path.read_bytes() == feature_raw and model_path.read_bytes() == model_raw


@pytest.mark.parametrize("legacy", [False, True])
def test_legacy_reference_descriptors_and_native_completion_are_not_optional(case, monkeypatch, legacy):
    case.seed_native()
    if legacy:
        _, original = runtime._read_route(case.Sources(), case.item, case.route, case.stocks,
            case.start, case.end, case.cache)
        plan = runtime.read_plan_from_manifest(case.manifest(original), case.cache, case.head)
        original["consumer_cache"]["manifest_size"] += 1
        entry = runtime._read_plan_entry(case.item, case.route,
            runtime._query_scope(case.route, case.stocks, case.start, case.end), original,
            plan["routes"][case.route]["files"])
        case.forbid_writes()
        with pytest.raises(ValueError, match="legacy_cache_reference"):
            case.read(entry, legacy=True, legacy_binding=original)
    else:
        _, binding = case.read()
        entry = take_plan(binding)
        (case.native_root / entry["files"]["complete"]["path"]).unlink()
        case.forbid_writes()
        with pytest.raises((OSError, ValueError)):
            case.read(entry)
    assert case.state.streams == 0
