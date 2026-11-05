"""Offline tests of the H63 U7 producer CLI (stock_h63_xs; ADR-0009): roots, activation, guards, calendar."""
from __future__ import annotations

import json
import sys
from pathlib import Path
from types import SimpleNamespace

import numpy as np
import pandas as pd
import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import stock_h63_xs as cli  # noqa: E402
import stock_h63_xs_core as core  # noqa: E402
import stock_h63_xs_reader as reader  # noqa: E402
from stock_h63_xs_fixture import write_model  # noqa: E402


def test_qa_flags_are_refused_on_the_formal_root(monkeypatch):
    monkeypatch.delenv(reader.ROOT_ENV, raising=False)
    args = SimpleNamespace(root=None, no_lease=True, recorded_at=None)
    with pytest.raises(cli.CliError) as caught:
        cli.resolve_root(args)
    assert caught.value.code == "h63xs_qa_flag_on_formal_root" and caught.value.exit_code == cli.EXIT_USAGE


def test_qa_flags_are_allowed_on_a_test_root(tmp_path):
    args = SimpleNamespace(root=str(tmp_path), no_lease=True, recorded_at="2026-10-30T23:20:00.000000Z")
    assert cli.resolve_root(args) == tmp_path


def test_calendar_check_statuses():
    sessions = pd.DatetimeIndex(pd.bdate_range("2026-10-01", "2026-10-30"))
    ok = cli.check_calendar(sessions, {"status": "ok", "sessions": sessions, "verified_asof": "2026-10-31"},
                            sessions[-1])
    assert ok["status"] == "ok"
    stale = cli.check_calendar(sessions, {"status": "ok", "sessions": sessions[:-3], "verified_asof": "2026-10-27"},
                               sessions[-1])
    assert stale["status"] == "calendar_stale"
    holiday = sessions.delete(5)
    bad = cli.check_calendar(sessions, {"status": "ok", "sessions": holiday, "verified_asof": "2026-10-31"},
                             sessions[-1])
    assert bad["status"] == "mismatch" and bad["differences"]["panel_only"] == [sessions[5].strftime("%Y%m%d")]
    assert cli.check_calendar(sessions, {"status": "calendar_unavailable"}, sessions[-1])["status"] == \
        "calendar_unavailable"


def test_first_activation_requires_authorization(tmp_path):
    run = SimpleNamespace(root=tmp_path, sessions=pd.DatetimeIndex(["2026-10-30"]), D=pd.Timestamp("2026-10-30"))
    with pytest.raises(cli.CliError) as caught:
        cli.step_train(run, kind="initial_activation", authorization_text="", decided_at=None, proposal=None,
                       force=False, acceptance=None, supplement_sha="f" * 64)
    assert caught.value.code == "h63xs_authorization_required"


def test_routine_refit_waits_63_sessions(tmp_path):
    write_model(tmp_path)  # first_signal_date 2026-10-30
    sessions = pd.DatetimeIndex(pd.bdate_range("2026-10-01", periods=60))
    run = SimpleNamespace(root=tmp_path, sessions=sessions, D=sessions[-1])
    out = cli.step_train(run, kind="routine_refit", authorization_text="", decided_at=None, proposal=None,
                         force=False, acceptance=None, supplement_sha="f" * 64)
    assert out["status"] == "not_due" and out["sessions_since_fit"] < core.HORIZON


def test_artifact_activation_and_rollback_are_append_only(tmp_path):
    first = write_model(tmp_path)
    artifact = {"schema": reader.ARTIFACT_SCHEMA, "model_id": "h63_xs_u7_20261231",
                "recipe": {"name": core.RECIPE["name"], "frozen_sha256": core.RECIPE_SHA256},
                "slope": {"b": 1.02, "fit_cutoff_exclusive": "2027-01-01"}, "first_signal_date": "2026-12-31"}
    sha = cli.write_artifact(tmp_path, artifact)
    assert cli.write_artifact(tmp_path, artifact) == sha  # idempotent, write once
    auth = cli.authorization_record("routine_refit", cli.ROUTINE_REFIT_TEXT, None, None)
    pointer = cli.activate(tmp_path, sha, artifact, auth, first["model_sha256"], None)
    assert reader.load_model(tmp_path)["pointer"]["activation_id"] == pointer["activation_id"]
    assert len(list((tmp_path / "models" / "activations").glob("*.json"))) == 1
    assert len(list((tmp_path / "models" / "receipts").glob("*.activated.json"))) == 1
    args = SimpleNamespace(root=str(tmp_path), to=first["model_sha256"], authorization="使用者核准回復",
                           decided_at="20270102 08:00")
    assert cli.cmd_rollback(args) == cli.EXIT_OK
    assert reader.load_model(tmp_path)["pointer"]["model_sha256"] == first["model_sha256"]
    assert len(list((tmp_path / "models" / "activations").glob("*.json"))) == 2
    assert (tmp_path / "models" / "artifacts" / (sha + ".json")).exists()  # never deleted


def test_out_of_range_slope_is_never_activated(tmp_path):
    artifact = {"schema": reader.ARTIFACT_SCHEMA, "model_id": "bad", "recipe": {"name": core.RECIPE["name"],
                "frozen_sha256": core.RECIPE_SHA256}, "slope": {"b": 2.5}, "first_signal_date": "2026-10-30"}
    sha = cli.write_artifact(tmp_path, artifact)
    args = SimpleNamespace(root=str(tmp_path), to=sha, authorization="x", decided_at=None)
    with pytest.raises(cli.CliError) as caught:
        cli.cmd_rollback(args)
    assert caught.value.code == "h63xs_model_integrity"


def test_main_maps_errors_to_exit_codes(tmp_path, capsys):
    rc = cli.main(["predict", "--root", str(tmp_path)])
    assert rc == cli.EXIT_USAGE  # no active model and no --supplement-sha256
    assert json.loads(capsys.readouterr().out.strip().splitlines()[-1])["code"] == "h63xs_supplement_required"
    assert cli.main(["status", "--root", str(tmp_path)]) == cli.EXIT_OK
