"""Isolated worker/guard fixtures; never install tasks or touch live services."""
from __future__ import annotations

import copy
import json
import sys
import threading
from contextlib import contextmanager
from pathlib import Path
from types import SimpleNamespace

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
sys.path.insert(0, str(Path(__file__).resolve().parent))
from test_stock_daily_selected_cache import case
import stock_daily_candidate_runtime as runtime
import stock_daily_selected_cache as cache
import stock_daily_selected_cache_worker as worker
import stock_daily_selected_deployment as deployment


@pytest.fixture
def worker_case(case, monkeypatch):
    monkeypatch.setattr(worker, "_prepare_comparison_history", lambda: {"status": "fixture_not_requested"})
    binding = {"activation_id": "fixture-activation-1", "model_path": str(case.model_path),
        "model_sha256": runtime._sha_file(case.model_path), "cache_dir": str(case.cache)}
    state = SimpleNamespace(binding=binding)
    monkeypatch.setattr(worker, "SINGLE_ROOT", case.root / "dstock_single")
    monkeypatch.setattr(worker, "_background_policy_path", lambda: case.root / "fixture-background-policy.json")
    worker.set_background_paused(False)
    monkeypatch.setattr(deployment, "active_selected_binding", lambda: copy.deepcopy(state.binding))
    monkeypatch.setattr(deployment, "_head_binding", lambda: {"head_sha256": case.state.head})

    def selected_context(pin, active):
        case.pins.append(pin)
        assert active["model_sha256"] == runtime._sha_file(case.model_path)
        return case.context

    monkeypatch.setattr(deployment, "load_selected_context", selected_context)
    monkeypatch.setattr(runtime, "_load_context", lambda **kwargs: pytest.fail("must use selected context, not shared model"))
    return SimpleNamespace(case=case, state=state, root=worker.SINGLE_ROOT)


def _ready_bytes():
    return (worker._state_root() / "ready.json").read_bytes()


def test_inactive_is_quiet_and_does_not_read_head_or_prepare(worker_case, monkeypatch, capsys):
    worker_case.state.binding = None
    monkeypatch.setattr(deployment, "_head_binding", lambda: pytest.fail("inactive must not read head"))
    monkeypatch.setattr(cache, "prepare_selected_cache", lambda *args, **kwargs: pytest.fail("inactive prepare"))
    result = worker.run_once()
    assert result["status"] == "inactive" and result["ready"] is False
    assert not worker_case.case.cache.exists()
    assert capsys.readouterr().out == ""


def test_new_head_prepares_all_ordinary_with_explicit_pin_and_self_owned_state(worker_case):
    result = worker.run_once()
    assert result["status"] == "ready" and result["prepared"] is True and result["fast_path"] is False
    assert worker_case.case.pins == ["a" * 64]
    receipt = worker._read_sealed(result["receipt"]["path"])[0]
    assert receipt["all_stocks"] is True and receipt["requested_all_stocks"] is True
    assert receipt["stock_count"] == 3 and receipt["horizons"] == [1, 63]
    assert (worker_case.root / "state" / "selected_cache_preparation" / "ready.json").is_file()
    assert list((worker_case.root / "logs" / "selected_cache_preparation").glob("*.json"))


def test_same_head_fast_path_never_prepares_reverifies_payloads_or_scans_market(worker_case, monkeypatch):
    worker.run_once()
    before = _ready_bytes()
    worker_case.case.state.streams.clear()
    worker_case.case.state.queries.clear()
    monkeypatch.setattr(cache, "prepare_selected_cache", lambda *args, **kwargs: pytest.fail("repeat prepare"))
    monkeypatch.setattr(worker, "_verify_receipt", lambda *args, **kwargs: pytest.fail("repeat full cache verification"))
    result = worker.run_once()
    assert result["status"] == "ready" and result["fast_path"] is True
    assert _ready_bytes() == before
    assert not worker_case.case.state.streams and not worker_case.case.state.queries


def test_manual_all_ordinary_receipt_is_adopted_without_preparing_again(worker_case, monkeypatch):
    cache.prepare_selected_cache(worker_case.case.model_path, worker_case.case.cache, pinned_head_sha256="a" * 64)
    worker_case.case.state.streams.clear()
    worker_case.case.state.queries.clear()
    monkeypatch.setattr(cache, "prepare_selected_cache", lambda *args, **kwargs: pytest.fail("verified manual receipt available"))
    result = worker.run_once()
    assert result["ready"] is True and result["prepared"] is False
    assert not worker_case.case.state.streams and not worker_case.case.state.queries


@pytest.mark.parametrize("route", ["normal", "fast", "adopt", "status"])
def test_ready_paths_do_not_call_gc_scan_or_delete(worker_case, monkeypatch, route):
    receipt = None
    before_ready = None
    if route in {"fast", "status"}:
        assert worker.run_once()["ready"] is True
        before_ready = _ready_bytes()
    elif route == "adopt":
        receipt = _legacy_receipt(worker_case)

    def forbidden(*args, **kwargs):
        pytest.fail("ready preparation/check/adoption must not scan or prune GC candidates")

    monkeypatch.setattr(worker, "_prune_consumer_cache", forbidden)
    monkeypatch.setattr(cache, "prune_selected_cache", forbidden)
    sentinel = worker_case.case.cache / "gc-decoupling-sentinel.txt"
    sentinel.parent.mkdir(parents=True, exist_ok=True)
    sentinel.write_bytes(b"unrelated cache bytes must remain untouched\n")
    if route == "adopt":
        result = worker.adopt_verified_cache(receipt)
    elif route == "status":
        result = worker.read_status()
    else:
        result = worker.run_once()
    assert result["ready"] is True
    if route != "status":
        assert result["gc"] == {"status": "deferred", "no_delete": True,
            "reason": "maintenance_not_part_of_preparation", "scan_performed": False,
            "candidate_files": None, "deleted_files": 0, "deleted_bytes": 0}
    if before_ready is not None:
        assert _ready_bytes() == before_ready
    assert sentinel.read_bytes() == b"unrelated cache bytes must remain untouched\n"


def test_new_head_never_calls_old_ready_marker_current(worker_case):
    old = worker.run_once()
    worker_case.case.state.head = "8" * 64
    assert worker.read_status()["ready"] is False
    new = worker.run_once()
    assert new["ready"] is True and new["target"]["head_sha256"] == "8" * 64
    assert old["receipt"] != new["receipt"]
    assert Path(old["receipt"]["path"]).is_file()


def test_partial_source_is_cache_ready_but_data_completeness_stays_false(worker_case):
    worker_case.case.state.partial.add("finmind_month_revenue")
    result = worker.run_once()
    assert result["ready"] is True and result["source_partial"] is True
    assert result["coverage_status"] == "source_partial" and result["data_completeness_verified"] is False
    status = worker.read_status()
    assert status["ready"] is True and status["source_partial"] is True


def test_missing_selected_route_retains_last_good_and_reports_partial(worker_case):
    worker.run_once()
    before = _ready_bytes()
    worker_case.case.state.head = "8" * 64
    worker_case.case.state.missing.add("finmind_per")
    result = worker.run_once()
    assert result["status"] == "partial" and result["ready"] is False
    assert _ready_bytes() == before
    assert worker.read_status()["ready"] is False


@pytest.mark.parametrize("failure", ["projection_failure", "wrong_hash", "fail_tail"])
def test_integrity_failure_does_not_fallback_to_old_head_or_promote_ready(worker_case, failure):
    worker.run_once()
    before = _ready_bytes()
    worker_case.case.state.head = "8" * 64
    # A head-only change may reuse verified bytes; change content to exercise the failed source read.
    worker_case.case.payloads["finmind_price_adj"][0]["value"] += 1
    getattr(worker_case.case.state, failure).add("finmind_price_adj")
    result = worker.run_once()
    assert result["status"] == "failed" and result["ready"] is False
    assert result["error"]["type"] in {"ProjectionIntegrityError", "CandidateRuntimeError", "ValueError"}
    assert _ready_bytes() == before
    assert worker.read_status()["status"] == "failed"


@pytest.mark.parametrize("change", ["head", "activation", "inactive"])
def test_target_changed_during_preparation_is_pending_without_marker_overwrite(worker_case, monkeypatch, change):
    worker.run_once()
    before = _ready_bytes()
    worker_case.case.state.head = "8" * 64
    original = cache.prepare_selected_cache

    def changed(*args, **kwargs):
        receipt = original(*args, **kwargs)
        if change == "head":
            worker_case.case.state.head = "9" * 64
        elif change == "activation":
            worker_case.state.binding["activation_id"] = "fixture-activation-2"
        else:
            worker_case.state.binding = None
        return receipt

    monkeypatch.setattr(cache, "prepare_selected_cache", changed)
    result = worker.run_once()
    assert result["status"] == "pending" and result["ready"] is False
    assert result["reason"] == "target_changed_during_preparation"
    assert _ready_bytes() == before and Path(result["receipt"]["path"]).is_file()


def test_activation_only_change_reuses_cache_not_old_activation_ready_claim(worker_case):
    worker.run_once()
    worker_case.state.binding["activation_id"] = "fixture-activation-2"
    worker_case.case.state.streams.clear()
    worker_case.case.state.queries.clear()
    assert worker.read_status()["ready"] is False
    result = worker.run_once()
    assert result["ready"] is True and result["prepared"] is False
    assert result["target"]["activation_id"] == "fixture-activation-2"
    assert not worker_case.case.state.streams and not worker_case.case.state.queries


def test_manual_guard_busy_is_deferred_and_does_not_remove_guard_or_last_good(worker_case):
    worker.run_once()
    before = _ready_bytes()
    results = []
    with cache.consumer_cache_guard(worker_case.case.cache):
        thread = threading.Thread(target=lambda: results.append(worker.run_once()))
        thread.start()
        thread.join(timeout=20)
        assert not thread.is_alive()
        assert results[0]["status"] == "deferred" and results[0]["ready"] is False
        assert (worker_case.case.cache / ".selected_cache_prepare.guard").is_file()
    assert _ready_bytes() == before


@pytest.mark.parametrize("kind", ["rows", "manifest", "receipt", "inventory"])
def test_fast_path_detects_modified_verified_files_without_preparing_or_fallback(worker_case, monkeypatch, kind):
    result = worker.run_once()
    before = _ready_bytes()
    if kind == "rows":
        path = next((worker_case.case.cache / "sealed_candidate_rows").rglob("*.jsonl"))
    elif kind == "manifest":
        path = next((worker_case.case.cache / "sealed_candidate_rows").glob("*/queries/*.json"))
    elif kind == "receipt":
        path = Path(result["receipt"]["path"])
    else:
        marker = worker._read_sealed(worker._state_root() / "ready.json")[0]
        path = Path(marker["inventory"]["path"])
    path.write_bytes(path.read_bytes() + b" ")
    monkeypatch.setattr(cache, "prepare_selected_cache", lambda *args, **kwargs: pytest.fail("integrity error must not rebuild"))
    failed = worker.run_once()
    assert failed["status"] == "failed" and failed["ready"] is False
    assert _ready_bytes() == before


def test_runtime_code_change_invalidates_ready_and_receipt_without_market_reread(worker_case, monkeypatch):
    worker.run_once()
    worker_case.case.state.streams.clear()
    worker_case.case.state.queries.clear()
    original = cache.preparation_code_binding()
    changed = dict(original, preparer_sha256="9" * 64)
    monkeypatch.setattr(cache, "preparation_code_binding", lambda: copy.deepcopy(changed))
    assert worker.read_status()["ready"] is False
    result = worker.run_once()
    assert result["ready"] is True and result["prepared"] is True
    assert not worker_case.case.state.streams and not worker_case.case.state.queries


def test_status_is_read_only_before_and_after_ready(worker_case):
    before = {str(path) for path in worker_case.case.root.rglob("*")}
    assert worker.read_status()["status"] == "pending"
    assert before == {str(path) for path in worker_case.case.root.rglob("*")}
    worker.run_once()
    before = {str(path): path.stat().st_mtime_ns for path in worker_case.root.rglob("*") if path.is_file()}
    assert worker.read_status()["ready"] is True
    assert before == {str(path): path.stat().st_mtime_ns for path in worker_case.root.rglob("*") if path.is_file()}


def test_pythonw_once_writes_output_and_state_without_console(worker_case, monkeypatch):
    worker_case.state.binding = None
    monkeypatch.setattr(sys, "stdout", None)
    monkeypatch.setattr(sys, "stderr", None)
    assert worker.main(["--once"]) == 0
    state = worker._read_sealed(worker._state_root() / "latest.json")[0]
    assert state["status"] == "inactive"
    assert (worker._log_root() / "worker_output.log").is_file()


@pytest.mark.parametrize("selected_status", ["ready", "failed", "inactive", "partial", "deferred"])
def test_full_history_phase_runs_after_selected_guard_independently(worker_case, monkeypatch, selected_status):
    phases = []
    def selected():
        with cache.consumer_cache_guard(worker_case.case.cache):
            phases.append("selected")
        return {"status": selected_status}
    def full_history():
        assert str(worker_case.case.cache.resolve()) not in getattr(cache._HELD_GUARDS, "paths", set())
        with cache.consumer_cache_guard(worker_case.case.cache):
            phases.append("full_history_outside_selected_guard")
        return {"status": "ready"}
    monkeypatch.setattr(worker, "run_once", selected)
    monkeypatch.setattr(worker, "_prepare_comparison_history", full_history)
    assert worker.main(["--once"]) == (2 if selected_status in {"failed", "partial"} else 0)
    assert phases == ["selected", "full_history_outside_selected_guard"]


def test_full_history_failure_does_not_change_selected_ready(worker_case, monkeypatch):
    worker.run_once()
    ready = _ready_bytes()
    monkeypatch.setattr(worker, "_prepare_comparison_history", lambda: {"status": "failed"})
    assert worker.main(["--once"]) == 2
    assert _ready_bytes() == ready


def test_status_cli_writes_no_logs_and_rejects_combined_modes(worker_case, capsys):
    assert worker.main(["--status"]) == 0
    result = json.loads(capsys.readouterr().out)
    assert result["status"] == "pending" and result["ready"] is False
    assert result["background_policy"]["paused"] is False
    assert not worker_case.root.exists()
    with pytest.raises(SystemExit) as exc:
        worker.main(["--once", "--status"])
    assert exc.value.code == 2


def _legacy_receipt(worker_case):
    fixture = worker_case.case
    result = cache.prepare_selected_cache(fixture.model_path, fixture.cache, pinned_head_sha256=fixture.state.head)
    path = Path(result["receipt_path"])
    document = worker._read_sealed(path)[0]
    document.pop("code_binding")
    worker._write_sealed(path, document)
    (fixture.cache / "selected_cache_preparations" / cache.ALL_ORDINARY_INDEX).unlink()
    return path


def _cache_bytes(fixture):
    paths = (fixture.cache / "sealed_candidate_rows").rglob("*")
    return {str(path): path.read_bytes() for path in paths if path.is_file()}


def test_legacy_ready_receipt_does_not_trigger_reprepare_without_explicit_adoption(worker_case, monkeypatch):
    path = _legacy_receipt(worker_case)
    original = path.read_bytes()
    target = worker._current_target()
    worker._write_sealed(path.parent / cache.ALL_ORDINARY_INDEX,
        {"schema": "stock-daily-selected-all-ordinary-index-v1", **target,
         "receipt": {"path": str(path), "sha256": runtime._sha_file(path)}})
    monkeypatch.setattr(cache, "prepare_selected_cache", lambda *args, **kwargs: pytest.fail("legacy must not reprepare"))
    result = worker.run_once()
    assert result["status"] == "failed" and result["ready"] is False
    assert "selected_cache_preparation_binding_mismatch" in result["error"]["detail"]
    assert path.read_bytes() == original and not (worker._state_root() / "ready.json").exists()


def test_explicit_adoption_preserves_original_receipt_and_rows_then_once_is_fast(worker_case, monkeypatch):
    worker_case.case.state.partial.add("finmind_month_revenue")
    path = _legacy_receipt(worker_case)
    original, rows = path.read_bytes(), _cache_bytes(worker_case.case)
    worker_case.case.state.streams.clear()
    worker_case.case.state.queries.clear()
    monkeypatch.setattr(cache, "prepare_selected_cache", lambda *args, **kwargs: pytest.fail("adoption cannot prepare"))
    monkeypatch.setattr(worker_case.case.context["sources"], "iter_artifact_rows",
        lambda *args, **kwargs: pytest.fail("adoption cannot scan routes"))
    calls = []
    file_proof = worker._file_proof

    def counted(path, *args, **kwargs):
        if Path(path).suffix == ".jsonl":
            calls.append(str(path))
        return file_proof(path, *args, **kwargs)

    monkeypatch.setattr(worker, "_file_proof", counted)
    result = worker.adopt_verified_cache(path)
    assert result["status"] == "ready" and result["operation"] == "adopt_verified_cache"
    assert result["prepared"] is False and result["source_partial"] is True
    assert result["data_completeness_verified"] is False
    assert result["source_routes_scanned"] == result["query_caches_rewritten"] == result["row_caches_rewritten"] == 0
    assert result["original_receipt"] == {"path": str(path), "sha256": runtime._sha_file(path)}
    assert path.read_bytes() == original and _cache_bytes(worker_case.case) == rows
    assert len(calls) == len(set(calls))
    assert calls and not worker_case.case.state.streams and not worker_case.case.state.queries
    evidence = worker._read_sealed(result["receipt"]["adoption"]["path"])[0]
    assert evidence["original_receipt"] == result["original_receipt"]
    index = worker._read_sealed(worker_case.case.cache / "selected_cache_preparations" / cache.ALL_ORDINARY_INDEX)[0]
    assert index["operation"] == "adopt_verified_cache"
    monkeypatch.setattr(worker, "_verify_receipt", lambda *args, **kwargs: pytest.fail("fast path cannot reread caches"))
    again = worker.run_once()
    assert again["status"] == "ready" and again["fast_path"] is True
    assert path.read_bytes() == original and _cache_bytes(worker_case.case) == rows


@pytest.mark.parametrize("mismatch", ["model", "head", "horizons", "universe", "plan", "scope", "parser", "rows"])
def test_adoption_incompatible_evidence_fails_closed_without_prepare_or_rewriting(worker_case, monkeypatch, mismatch):
    path = _legacy_receipt(worker_case)
    receipt = worker._read_sealed(path)[0]
    if mismatch in {"model", "head"}:
        receipt["model_sha256" if mismatch == "model" else "head_sha256"] = "9" * 64
    elif mismatch == "horizons":
        receipt["horizons"] = [1]
    elif mismatch == "universe":
        receipt["stock_ids"] = ["2330"]
        receipt["stock_count"] = receipt["official_stock_count"] = 1
    elif mismatch == "plan":
        receipt["source_plan"][0]["reason_zh"] = "not the frozen plan"
        receipt["source_plan_sha256"] = worker._digest(receipt["source_plan"])
    elif mismatch == "scope":
        route = next(item for item in receipt["routes"] if item["route_id"] == "finmind_per")
        route["stocks"]["2330"]["scope"]["start"] = {"2330": "2010-01-01"}
    elif mismatch == "parser":
        monkeypatch.setattr(runtime, "CACHE_SEMANTICS", "incompatible-parser-semantics")
    else:
        row_path = next((worker_case.case.cache / "sealed_candidate_rows").rglob("*.jsonl"))
        row_path.write_bytes(b'{"tampered":true}\n')
    if mismatch not in {"parser", "rows"}:
        worker._write_sealed(path, receipt)
    original, rows = path.read_bytes(), _cache_bytes(worker_case.case)
    monkeypatch.setattr(cache, "prepare_selected_cache", lambda *args, **kwargs: pytest.fail("cannot repair during adoption"))
    worker_case.case.state.streams.clear()
    worker_case.case.state.queries.clear()
    result = worker.adopt_verified_cache(path)
    assert result["status"] == "failed" and result["ready"] is False
    assert result["operation"] == "adopt_verified_cache"
    assert not (worker._state_root() / "ready.json").exists()
    assert original == path.read_bytes() and rows == _cache_bytes(worker_case.case)
    assert not worker_case.case.state.streams and not worker_case.case.state.queries


def test_adoption_cli_is_explicit_and_does_not_call_once(worker_case, monkeypatch):
    worker.set_background_paused(True)
    calls = []

    def adopt(path):
        calls.append(path)
        return {"status": "ready"}

    monkeypatch.setattr(worker, "adopt_verified_cache", adopt)
    monkeypatch.setattr(worker, "run_once", lambda: pytest.fail("adoption must not call once/prepare"))
    path = worker_case.case.cache / "selected_cache_preparations" / "existing.json"
    assert worker.main(["--adopt-receipt", str(path)]) == 0
    assert calls == [path]


@pytest.mark.parametrize("bad_policy", ["missing", "malformed", "duplicate", "boolean", "schema", "oversized", "unreadable"])
@pytest.mark.parametrize("cli", [False, True])
def test_background_pause_is_fail_closed_before_target_log_or_cache(tmp_path, monkeypatch, bad_policy, cli):
    monkeypatch.setattr(worker, "SINGLE_ROOT", tmp_path / "consumer")
    path = worker._background_policy_path()
    bodies = {"malformed": b"{", "duplicate": b'{"schema":"single-stock-selected-cache-background-policy-v1","paused":true,"paused":false}',
        "boolean": b'{"schema":"single-stock-selected-cache-background-policy-v1","paused":0}',
        "schema": b'{"schema":"unknown","paused":false}', "oversized": b" " * 4097}
    if bad_policy in bodies:
        path.parent.mkdir(parents=True)
        path.write_bytes(bodies[bad_policy])
    elif bad_policy == "unreadable":
        original_open = Path.open

        def denied(selected, *args, **kwargs):
            if selected == path:
                raise PermissionError("fixture policy access denied")
            return original_open(selected, *args, **kwargs)

        monkeypatch.setattr(Path, "open", denied)
    before = {str(item): item.stat().st_mtime_ns for item in tmp_path.rglob("*") if item.is_file()}

    def forbidden(*args, **kwargs):
        pytest.fail("paused background must not touch target, logs, cache or receipts")

    for name in ("_current_target", "_log_root", "_finish", "_matching_ready", "_prune_consumer_cache"):
        monkeypatch.setattr(worker, name, forbidden)
    monkeypatch.setattr(cache, "consumer_cache_guard", forbidden)
    monkeypatch.setattr(cache, "prepare_selected_cache", forbidden)
    if cli:
        assert worker.main(["--once"]) == 0
    else:
        result = worker.run_once()
        assert result["status"] == "paused" and result["writes"] is result["prepared"] is False
    assert before == {str(item): item.stat().st_mtime_ns for item in tmp_path.rglob("*") if item.is_file()}


def test_explicit_pause_resume_only_access_small_policy(tmp_path, monkeypatch, capsys):
    monkeypatch.setattr(worker, "SINGLE_ROOT", tmp_path / "consumer")

    def forbidden(*args, **kwargs):
        pytest.fail("policy commands must not inspect cache or invoke worker")

    for name in ("_current_target", "_log_root", "run_once", "read_status", "adopt_verified_cache"):
        monkeypatch.setattr(worker, name, forbidden)
    assert worker.read_background_policy()["reason"] == "background_policy_missing"
    assert not worker.SINGLE_ROOT.exists()
    for mode, paused in (("--resume-background", False), ("--pause-background", True)):
        assert worker.main([mode]) == 0
        result = json.loads(capsys.readouterr().out)
        assert result["paused"] is paused and result["policy_valid"] is True
        path = worker._background_policy_path()
        raw, stamp = path.read_bytes(), path.stat().st_mtime_ns
        assert worker.read_background_policy()["paused"] is paused
        assert path.read_bytes() == raw and path.stat().st_mtime_ns == stamp
        assert [item for item in worker.SINGLE_ROOT.rglob("*") if item.is_file()] == [path]


def test_status_cli_preserves_cache_readiness_while_background_paused(worker_case, monkeypatch, capsys):
    assert worker.run_once()["ready"] is True
    worker.set_background_paused(True)
    expected = worker.read_status()
    before = {str(path): path.stat().st_mtime_ns for path in worker_case.case.root.rglob("*") if path.is_file()}
    monkeypatch.setattr(worker, "_log_root", lambda: pytest.fail("manual status cannot open logs"))
    monkeypatch.setattr(worker, "run_once", lambda: pytest.fail("status cannot invoke background work"))
    assert worker.main(["--status"]) == 0
    result = json.loads(capsys.readouterr().out)
    policy = result.pop("background_policy")
    assert result == expected and result["status"] == "ready" and result["ready"] is True
    assert policy["paused"] is True and policy["policy_valid"] is True
    assert before == {str(path): path.stat().st_mtime_ns for path in worker_case.case.root.rglob("*") if path.is_file()}


def test_explicit_pause_noops_and_manual_preparation_still_works(worker_case, monkeypatch):
    worker.set_background_paused(True)
    result = worker.run_once()
    assert result["status"] == "paused" and not worker_case.case.cache.exists()
    receipt = cache.prepare_selected_cache(worker_case.case.model_path, worker_case.case.cache, pinned_head_sha256="a" * 64)
    assert receipt["ready"] is True
    assert worker.read_background_policy()["paused"] is True


def test_pause_is_rechecked_after_cache_guard_before_preparation(worker_case, monkeypatch):
    @contextmanager
    def guard(cache_dir):
        worker.set_background_paused(True)
        yield

    monkeypatch.setattr(cache, "consumer_cache_guard", guard)
    monkeypatch.setattr(cache, "prepare_selected_cache", lambda *a, **k: pytest.fail("pause arrived before prepare"))
    monkeypatch.setattr(worker, "_matching_ready", lambda *a: pytest.fail("paused cache read"))
    assert worker.run_once()["status"] == "paused"
