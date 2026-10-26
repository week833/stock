"""Selected activation dispatch, explicit research and explicit legacy QA."""
from pathlib import Path
import sys
from types import SimpleNamespace

import pytest

import tw_single_stock_c0 as single


@pytest.fixture(autouse=True)
def isolated_activation(monkeypatch):
    monkeypatch.setattr(single.selected, "analysis_binding", lambda: None)


def test_absent_activation_keeps_existing_generator(monkeypatch, tmp_path):
    calls = []
    monkeypatch.setattr(single, "generate_estimate", lambda *args, **kwargs: calls.append((args, kwargs)))
    monkeypatch.setitem(sys.modules, "stock_daily_candidate_runtime", SimpleNamespace(
        generate_candidate_reports=lambda *args, **kwargs: pytest.fail("候選模式不應被載入")))
    assert single.main(63, ["--stock", "2330", "--output-dir", str(tmp_path)]) == 0
    assert calls[0][0] == (63, "2330")
    assert calls[0][1]["model_path"] is None
    assert calls[0][1]["both_horizons"] is False


def test_selected_activation_is_default_and_keeps_same_output_dir(monkeypatch, tmp_path):
    calls = []
    heads = []

    class Lease:
        def __init__(self):
            self.closes = 0

        def close(self):
            self.closes += 1

    original_lease, pinned_lease = Lease(), Lease()
    binding = single.selected._HeadLeaseBinding({"schema": "single-stock-selected-runtime-binding-v1",
        "deployment_binding": {"activation_id": "fixture"}}, original_lease)

    def head_binding(digest):
        head = single.selected._HeadLeaseBinding({"head_sha256": digest}, pinned_lease)
        heads.append(head)
        return head

    def generate_selected_reports(*args, **kwargs):
        active = kwargs["activation_binding"]
        try:
            calls.append((args, kwargs))
            assert original_lease.closes == 1 and pinned_lease.closes == 0
            assert len(heads) == 1 and heads[0]._source_lease is None
            assert not heads[0]._source_finalizer.alive
            assert active._source_lease is pinned_lease and active._source_finalizer.alive
        finally:
            single.selected.close_source_lease(active)

    monkeypatch.delenv(single.selected.BINDING_ENV, raising=False)
    monkeypatch.setattr(single.selected, "analysis_binding", lambda: binding)
    monkeypatch.setattr(single.selected, "_head_binding", head_binding)
    monkeypatch.setattr(single.selected, "generate_selected_reports", generate_selected_reports)
    monkeypatch.setattr(single, "generate_estimate", lambda *a, **k: pytest.fail("selected must not use legacy"))
    monkeypatch.setattr(single.contract, "load_active_c0_model", lambda *a, **k: pytest.fail("shared current must not load"))
    assert single.main(63, ["--stock", "2330", "--both-horizons", "--output-dir", str(tmp_path),
        "--pinned-head-sha256", "a" * 64]) == 0
    assert calls == [(("2330",), {"output_dir": tmp_path, "cache_dir": None, "horizons": (1, 63),
        "pinned_head_sha256": "a" * 64, "activation_binding": {**binding, "head_sha256": "a" * 64}})]
    assert original_lease.closes == pinned_lease.closes == 1
    assert not binding._source_finalizer.alive
    assert not calls[0][1]["activation_binding"]._source_finalizer.alive
    for owner in (binding, heads[0], calls[0][1]["activation_binding"]):
        single.selected.close_source_lease(owner)
    assert original_lease.closes == pinned_lease.closes == 1


def test_explicit_c0_is_qa_even_when_selected_pointer_is_invalid(monkeypatch, tmp_path):
    calls = []
    monkeypatch.setattr(single.selected, "analysis_binding", lambda: pytest.fail("explicit QA must not load activation"))
    monkeypatch.setattr(single, "generate_estimate", lambda *args, **kwargs: calls.append(kwargs))
    assert single.main(1, ["--stock", "2330", "--c0-model", "qa.json", "--output-dir", str(tmp_path)]) == 0
    assert calls[0]["model_path"] == Path("qa.json")


@pytest.mark.parametrize("fail_at", ["binding", "generation"])
def test_selected_failure_never_falls_back(monkeypatch, tmp_path, fail_at):
    def fail(*args, **kwargs):
        raise ValueError("selected_integrity_failure")
    monkeypatch.setattr(single.selected, "analysis_binding", fail if fail_at == "binding" else lambda: {"fixture": True})
    monkeypatch.setattr(single.selected, "generate_selected_reports", fail)
    monkeypatch.setattr(single, "generate_estimate", lambda *a, **k: pytest.fail("no legacy fallback"))
    assert single.main(1, ["--stock", "2330", "--output-dir", str(tmp_path)]) == 2


def test_resolver_does_not_load_any_model(monkeypatch, tmp_path, capsys):
    context = {"current_identity": {"records": [{"stock_id": "2330", "stock_name": "台積電", "market": "TWSE"}]},
        "descriptor": {"snapshot_fingerprint": "a" * 64}, "asof": "2026-10-16", "data_time": {}}
    monkeypatch.setattr(single.selected, "load_selected_context", lambda *a, **k: context)
    monkeypatch.setattr(single.contract, "load_active_c0_model", lambda *a, **k: pytest.fail("resolver must not load C0"))
    monkeypatch.setattr(single.contract, "build_data_time", lambda *a, **k: {})
    output = tmp_path / "resolution.json"
    assert single.main(1, ["--resolve-stock", "台積電", "--output-json", str(output)]) == 0
    assert "1. 2330 台積電｜exact｜score=1.0" in capsys.readouterr().out
    import json
    assert json.loads(output.read_text(encoding="utf-8"))["schema"] == "single-current-selected-stock-resolution-v1"


def test_candidate_flag_dispatches_without_legacy_contract_validator(monkeypatch, tmp_path):
    calls = []
    monkeypatch.setattr(single, "generate_estimate", lambda *args, **kwargs: pytest.fail("不可分派至舊C0"))
    monkeypatch.setattr(single.contract, "load_active_c0_model", lambda *args, **kwargs: pytest.fail("入口不可先載入舊C0模型"))
    monkeypatch.setitem(sys.modules, "stock_daily_candidate_runtime", SimpleNamespace(
        _horizons=lambda text: tuple(int(value) for value in text.split(",")),
        generate_candidate_reports=lambda *args, **kwargs: calls.append((args, kwargs))))
    model = tmp_path / "candidate.json"
    assert single.main(63, ["--stock", "2330", "--candidate-model", str(model),
        "--horizons", "1,63", "--output-dir", str(tmp_path), "--pinned-head-sha256", "a" * 64]) == 0
    assert calls[0][0] == ("2330",)
    assert calls[0][1]["model_path"] == model
    assert calls[0][1]["horizons"] == (1, 63)
    assert calls[0][1]["pinned_head_sha256"] == "a" * 64
    assert "forward_db" not in calls[0][1]


def test_candidate_default_output_is_research_run_not_formal_report_root(monkeypatch, tmp_path):
    calls = []
    monkeypatch.setitem(sys.modules, "stock_daily_candidate_runtime", SimpleNamespace(
        CANDIDATE_ROOT=tmp_path / "candidates", _new_run=lambda root: root / "fixture-run",
        generate_candidate_reports=lambda *args, **kwargs: calls.append(kwargs)))
    assert single.main(1, ["--stock", "2330", "--candidate-model", str(tmp_path / "model.json")]) == 0
    assert calls[0]["horizons"] == (1,)
    assert calls[0]["output_dir"] == tmp_path / "candidates" / "reports" / "fixture-run" / "reports"
    assert calls[0]["output_dir"] != single.DEFAULT_OUTPUT


@pytest.mark.parametrize("extra", [["--c0-model", "old.json"], ["--forward-db", "ledger.sqlite"], ["--resolve-only"]])
def test_candidate_rejects_formal_qa_or_resolution_arguments(extra):
    with pytest.raises(SystemExit) as exc:
        single.main(1, ["--stock", "2330", "--candidate-model", "candidate.json", *extra])
    assert exc.value.code == 2


def test_horizons_without_candidate_flag_is_not_new_legacy_behavior():
    with pytest.raises(SystemExit) as exc:
        single.main(63, ["--stock", "2330", "--horizons", "1,63"])
    assert exc.value.code == 2


def test_candidate_failure_returns_nonzero_and_never_falls_back(monkeypatch, tmp_path):
    def fail(*args, **kwargs):
        raise ValueError("候選模型缺失")

    monkeypatch.setattr(single, "generate_estimate", lambda *args, **kwargs: pytest.fail("不可退回正式模型"))
    monkeypatch.setitem(sys.modules, "stock_daily_candidate_runtime", SimpleNamespace(generate_candidate_reports=fail))
    assert single.main(1, ["--stock", "2330", "--candidate-model", "missing.json",
        "--output-dir", str(tmp_path)]) == 2


def test_fixed_candidate_entry_is_explicit_subcommand_and_no_auto_training():
    entry = Path(__file__).resolve().parents[1] / "tools" / "RUN_SHARED_MODEL_CANDIDATE.cmd"
    text = entry.read_text(encoding="utf-8")
    assert '"%ENTRY%" %*' in text
    assert '"%ENTRY%" --help' in text
    assert "find_python.cmd\" analysis" in text
    assert "current.json" not in text
