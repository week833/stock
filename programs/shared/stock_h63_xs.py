r"""H63 U7 model series (ADR-0009): train / predict / settle / review / daily / status / rollback / supplement.

Writes only under the data root (default D:\TWStockData\program_data\stock_h63_xs, env STOCK_H63_XS_ROOT); the
central store is read-only (pinned head + reader lease).  One full-market snapshot per decision date D (the latest
session of the pinned head) is both what single-stock reports show and the immutable forward-record block.

Exit codes: 0 done / already recorded / skipped by design, 2 usage or configuration, 3 data not ready,
4 gate, integrity or model, 5 IO or unexpected.
People see dates as YYYYMMDD; machine fields stay ISO.
"""
from __future__ import annotations

import argparse
import datetime as dt
import hashlib
import json
import os
import platform
import sys
import time
import traceback
import uuid
from pathlib import Path
from typing import Any, Dict, Optional

import numpy as np
import pandas as pd

import date_format as cd
import stock_h63_xs_core as core
import stock_h63_xs_data as data
import stock_h63_xs_ledger as ledger
import stock_h63_xs_reader as reader

EXIT_OK, EXIT_USAGE, EXIT_NOT_READY, EXIT_INTEGRITY, EXIT_IO = 0, 2, 3, 4, 5
SUPPLEMENT_DIR = Path("reference") / "price_supplement"
ROUTINE_REFIT_TEXT = "routine refit per ADR-0009"
T0 = time.time()


def say(message: str) -> None:
    print("[%s %6.1fs] %s" % (dt.datetime.now().strftime("%H:%M:%S"), time.time() - T0, message), flush=True)


class CliError(RuntimeError):
    def __init__(self, code: str, detail: str = "", exit_code: int = EXIT_INTEGRITY):
        super().__init__(code + (": " + detail if detail else ""))
        self.code, self.detail, self.exit_code = code, detail, exit_code


def formal_root() -> Path:
    return reader.DEFAULT_ROOT


def is_formal(root: Path) -> bool:
    return os.path.normcase(str(Path(root).resolve())) == os.path.normcase(str(formal_root().resolve()))


def module_shas() -> Dict[str, str]:
    out = {}
    for module in (core, data, ledger, reader, cd):
        out[Path(module.__file__).name] = data.sha256_file(module.__file__)
    out[Path(__file__).name] = data.sha256_file(__file__)
    return out


def runtime_versions() -> Dict[str, str]:
    import pyarrow
    return {"python": platform.python_version(), "numpy": np.__version__, "pandas": pd.__version__,
            "pyarrow": pyarrow.__version__}


def run_id() -> str:
    return dt.datetime.now(dt.timezone.utc).strftime("%Y%m%dT%H%M%SZ") + "_" + uuid.uuid4().hex[:8]


def canonical_bytes(value: Any) -> bytes:
    return (json.dumps(core.jsonable(value), ensure_ascii=False, sort_keys=True, indent=1, allow_nan=False)
            + "\n").encode("utf-8")


def ymd(value: Any) -> str:
    return cd.display_day(cd.calendar_day_of(value) if value is not None else None)


# ------------------------------------------------------------------------------------------------------------------
# model artifacts and activation
# ------------------------------------------------------------------------------------------------------------------
def models_dir(root: Path) -> Path:
    return Path(root) / "models"


def current_model(root: Path) -> Optional[Dict[str, Any]]:
    if not (models_dir(root) / "current.json").exists():
        return None
    try:
        return reader.load_model(root)
    except reader.H63XsUnavailable as exc:
        raise CliError("h63xs_model_integrity", exc.detail, EXIT_INTEGRITY) from exc


def write_artifact(root: Path, artifact: Dict[str, Any]) -> str:
    raw = canonical_bytes(artifact)
    sha = hashlib.sha256(raw).hexdigest()
    path = models_dir(root) / "artifacts" / (sha + ".json")
    if path.exists():
        if data.sha256_file(path) != sha:
            raise CliError("h63xs_model_integrity", "existing artifact differs: " + sha[:12], EXIT_INTEGRITY)
        return sha
    ledger._write_new(path, lambda tmp: Path(tmp).write_bytes(raw))
    return sha


def activate(root: Path, model_sha: str, artifact: Dict[str, Any], authorization: Dict[str, Any],
             previous: Optional[str], acceptance: Optional[Dict[str, Any]]) -> Dict[str, Any]:
    pointer = {"schema": reader.CURRENT_SCHEMA, "consumer_id": "single_h63",
               "activation_id": "h63xs-" + uuid.uuid4().hex, "model_sha256": model_sha,
               "model_id": artifact["model_id"], "recipe": artifact["recipe"],
               "first_signal_date": artifact["first_signal_date"],
               "activated_at": ledger.stamp_utc(ledger.utc_now()), "authorization": authorization,
               "acceptance": acceptance, "previous_model_sha256": previous}
    pointer["integrity_sha256"] = reader.pointer_integrity(pointer)
    base = models_dir(root)
    stamp = dt.datetime.now(dt.timezone.utc).strftime("%Y%m%dT%H%M%S%fZ")
    ledger.write_new_json(base / "activations" / ("%s_%s.json" % (stamp, model_sha[:12])), pointer)
    ledger.write_new_json(base / "receipts" / (pointer["activation_id"] + ".prepared.json"),
                          {"activation_id": pointer["activation_id"], "model_sha256": model_sha,
                           "guards": artifact.get("guards"), "acceptance": acceptance,
                           "prepared_at": ledger.stamp_utc(ledger.utc_now())})
    ledger.replace_json(base / "current.json", pointer)
    verified = reader.load_model(root)
    if verified["pointer"]["activation_id"] != pointer["activation_id"]:
        raise CliError("h63xs_model_integrity", "activation not visible to the reader", EXIT_INTEGRITY)
    ledger.write_new_json(base / "receipts" / (pointer["activation_id"] + ".activated.json"),
                          {"activation_id": pointer["activation_id"], "model_sha256": model_sha,
                           "pointer_sha256": data.sha256_file(base / "current.json"),
                           "reader_verified_at": ledger.stamp_utc(ledger.utc_now())})
    return pointer


def authorization_record(kind: str, text: str, decided_at: Optional[str], proposal: Optional[str]) -> Dict[str, Any]:
    return {"kind": kind, "text": text, "text_sha256": hashlib.sha256(text.encode("utf-8")).hexdigest(),
            "decided_at_taipei": decided_at, "proposal_summary": proposal,
            "proposal_sha256": hashlib.sha256(proposal.encode("utf-8")).hexdigest() if proposal else None,
            "adr": "ADR-0009", "source": "chat" if kind != "routine_refit" else "adr"}


# ------------------------------------------------------------------------------------------------------------------
# one pinned run: inputs -> panel -> gates -> scores
# ------------------------------------------------------------------------------------------------------------------
class Run:
    pass


def supplement_folder(root: Path, manifest_sha: str) -> Path:
    return Path(root) / SUPPLEMENT_DIR / manifest_sha[:12]


def prepare(root: Path, supplement_sha: str, *, lease: bool = True, central: Any = data.CENTRAL) -> Run:
    run = Run()
    run.root = Path(root)
    run.notes = []
    say("pin head")
    run.head = data.pin_head(central, lease=lease)
    try:
        cache_dir = run.root / "cache" / "objects"
        say("head %s (published %s); load inputs" % (run.head.sha256[:12], run.head.published_at))
        run.inputs = data.load_inputs(run.head, supplement_folder(run.root, supplement_sha), supplement_sha, cache_dir)
        px, board = run.inputs["px"], run.inputs["board"]
        D_data = pd.Timestamp(px["date"].max())
        run.coverage = data.price_coverage(px, board, core.PRICE_FIRST, D_data)
        if run.coverage["missing_share"] > data.MAX_MISSING_PRICE_SHARE:
            raise data.H63XsDataError("h63xs_price_history_gap",
                                      "%.2f%% of TWSE/TPEx keys have no price" % (100 * run.coverage["missing_share"]),
                                      EXIT_INTEGRITY)
        say("build panel (price keys missing %d of %d)" % (run.coverage["missing_keys"],
                                                           run.coverage["applicable_keys"]))
        log: Dict[str, Any] = {}
        run.P, run.ctx = core.build_panel(px, board, run.inputs["per"], run.inputs["revenue"],
                                          run.inputs["late_codes"], log)
        run.panel_log = log
        run.sessions = run.ctx["sessions"]
        run.D = pd.Timestamp(run.sessions[-1])
        run.calendar = data.verified_calendar(run.head)
        run.calendar_check = check_calendar(run.sessions, run.calendar, run.D)
        if run.calendar_check["status"] == "mismatch":
            raise data.H63XsDataError("h63xs_calendar_mismatch", json.dumps(run.calendar_check["differences"]),
                                      EXIT_INTEGRITY)
        if run.calendar_check["status"] != "ok":
            run.notes.append(run.calendar_check["status"])
        run.gate, run.gate_table = core.gate_production(run.P, run.ctx["rule"], run.sessions)
        run.sc = core.score_panel(run.P)
        run.sessions_sha256 = hashlib.sha256(core.canonical_json([str(x.date()) for x in run.sessions])).hexdigest()
        say("panel %d rows, %d sessions %s..%s" % (len(run.P), len(run.sessions), ymd(run.sessions[0]), ymd(run.D)))
        return run
    except BaseException:
        run.head.close()
        raise


def check_calendar(sessions: pd.DatetimeIndex, calendar: Dict[str, Any], D: pd.Timestamp) -> Dict[str, Any]:
    if calendar.get("status") != "ok" or calendar.get("sessions") is None:
        return {"status": "calendar_unavailable", "detail": calendar.get("detail")}
    cal = calendar["sessions"]
    verified_to = min(pd.Timestamp(calendar.get("verified_asof") or cal.max()), D)
    lo = max(cal.min(), sessions.min())
    a = sessions[(sessions >= lo) & (sessions <= verified_to)]
    b = cal[(cal >= lo) & (cal <= verified_to)]
    if not a.equals(b):
        return {"status": "mismatch", "differences": {
            "panel_only": [ymd(x) for x in a.difference(b)[:10]], "calendar_only": [ymd(x) for x in b.difference(a)[:10]]}}
    return {"status": "ok" if verified_to >= D else "calendar_stale", "checked_to": str(verified_to.date())}


def check_fresh(run: Run) -> Dict[str, Any]:
    per_last = pd.Timestamp(run.inputs["per"]["date"].max())
    previous = run.sessions[-2] if len(run.sessions) > 1 else run.D
    rev_last = run.inputs["revenue"]["period"].max()
    rev_end = rev_last.to_timestamp(how="end").normalize()
    age = int((run.D - rev_end) / np.timedelta64(1, "D"))
    out = {"per_last": str(per_last.date()), "previous_session": str(pd.Timestamp(previous).date()),
           "revenue_last_period": str(rev_last), "revenue_age_days": age}
    if per_last < previous:
        raise data.H63XsDataError("h63xs_inputs_stale", "PER ends %s before %s" % (ymd(per_last), ymd(previous)),
                                  EXIT_NOT_READY)
    if age > core.REVENUE_STALE_DAYS:
        raise data.H63XsDataError("h63xs_inputs_stale", "revenue %s is %d days old" % (rev_last, age), EXIT_NOT_READY)
    return out


# ------------------------------------------------------------------------------------------------------------------
# steps
# ------------------------------------------------------------------------------------------------------------------
def step_train(run: Run, *, kind: str, authorization_text: str, decided_at: Optional[str], proposal: Optional[str],
               force: bool, acceptance: Optional[Dict[str, Any]], supplement_sha: str) -> Dict[str, Any]:
    active = current_model(run.root)
    if active is not None and not force:
        first = pd.Timestamp(active["artifact"]["first_signal_date"])
        elapsed = int((run.sessions > first).sum())
        if elapsed < core.HORIZON:
            return {"status": "not_due", "sessions_since_fit": elapsed}
        kind = "routine_refit"
    if active is None and not authorization_text:
        raise CliError("h63xs_authorization_required", "first activation needs --authorization", EXIT_USAGE)
    cutoff = run.D + pd.DateOffset(days=1)
    fit = core.fit_b(run.P, run.sc["Sc"], cutoff, run.gate if core.SLOPE_GATED else None)
    guard = core.slope_guard(fit["b"])
    say("slope b=%.6f (%s, %d rows, %d stocks)" % (fit["b"], guard, fit["rows"], fit["stocks"]))
    artifact = {
        "schema": reader.ARTIFACT_SCHEMA, "model_id": "h63_xs_u7_%s" % run.D.strftime("%Y%m%d"),
        "recipe": {"name": core.RECIPE["name"], "frozen_sha256": core.RECIPE_SHA256},
        "slope": {**fit, "phase_anchor": core.SLOPE_PHASE_ANCHOR, "date_step": core.SLOPE_DATE_STEP, "C": core.SLOPE_C,
                  "fit_cutoff_exclusive": str(cutoff.date())},
        "guards": {"b_min": core.SLOPE_MIN, "b_max": core.SLOPE_MAX, "status": guard},
        "first_signal_date": str(run.D.date()),
        "data_provenance": data.provenance(run.head, run.inputs, run.calendar, core.SEAM_DATE),
        "panel_identity": {"rows": int(len(run.P)), "sessions_sha256": run.sessions_sha256},
        "code": module_shas(), "runtime": runtime_versions(), "acceptance": acceptance,
        "trained_at": ledger.stamp_utc(ledger.utc_now())}
    model_sha = write_artifact(run.root, artifact)
    if guard != "ok":
        if kind == "routine_refit" and not force:
            ledger.record_event(ledger.Paths(run.root), "refit_rejected_b_out_of_range", b=fit["b"],
                                model_sha256=model_sha)
            return {"status": "refit_rejected_b_out_of_range", "b": fit["b"], "model_sha256": model_sha}
        raise CliError("h63xs_slope_out_of_range", "b=%.6f" % fit["b"], EXIT_INTEGRITY)
    if active is not None and active["pointer"]["model_sha256"] == model_sha:
        return {"status": "unchanged", "model_sha256": model_sha}
    auth = authorization_record(kind, authorization_text or ROUTINE_REFIT_TEXT, decided_at, proposal)
    pointer = activate(run.root, model_sha, artifact, auth,
                       active["pointer"]["model_sha256"] if active else None, acceptance)
    ledger.record_event(ledger.Paths(run.root), "activated", kind=kind, model_sha256=model_sha,
                        activation_id=pointer["activation_id"], b=fit["b"])
    return {"status": "activated", "kind": kind, "model_sha256": model_sha, "b": fit["b"],
            "activation_id": pointer["activation_id"]}


def step_predict(run: Run, *, trigger: str, record: bool, recorded_at: Optional[dt.datetime] = None
                 ) -> Dict[str, Any]:
    model = current_model(run.root)
    if model is None:
        raise CliError("h63xs_model_missing", "no active model (train first)", EXIT_INTEGRITY)
    art = model["artifact"]
    supp = (art.get("data_provenance") or {}).get("supplement") or {}
    if supp.get("manifest_sha256") != run.inputs["supplement"]["manifest_sha256"]:
        raise CliError("h63xs_supplement_sha_mismatch", "model pins another supplement", EXIT_INTEGRITY)
    D = run.D
    paths = ledger.Paths(run.root)
    try:
        fresh = check_fresh(run)
        previous = run.sessions[-2] if len(run.sessions) > 1 else None
        board_check = data.board_gate(run.inputs["board"], run.inputs["px"], D, previous)
        if not board_check["ok"]:
            raise data.H63XsDataError("h63xs_board_gate_failed", "; ".join(board_check["problems"]), EXIT_INTEGRITY)
        gate_row = core.gate_table_row(run.gate_table, D)
        if not gate_row["ok"]:
            raise data.H63XsDataError("h63xs_daily_gate_failed", json.dumps(gate_row), EXIT_INTEGRITY)
    except data.H63XsDataError as exc:
        ledger.record_refusal(run.root, exc.code, exc.detail, decision_date=str(D.date()),
                              central_head_sha256=run.head.sha256)
        raise
    b = float(art["slope"]["b"])
    pool = core.BandPool(run.P, run.sc["sigma63_b3"])
    rows, meta = core.infer_rows(run.P, run.sc, b, D, pool, run.gate)
    flags = data.integrity_flags(run.inputs["px"], run.inputs["board"], D, core.SEAM_DATE, rows["stock_id"])
    bd = run.inputs["board"]
    raw = bd[bd["date"] == D].set_index("stock_id")["close"]
    frame = ledger.snapshot_frame(rows, flags, {str(k): float(v) for k, v in raw.items() if pd.notna(v)})
    objects = {rid: e.get("sha256") for rid, e in run.head.routes.items()
               if rid in data.REQUIRED_ROUTES + data.OPTIONAL_ROUTES}
    key_material = {"decision_date": str(D.date()), "objects": objects,
                    "supplement_manifest_sha256": run.inputs["supplement"]["manifest_sha256"],
                    "seam_date": core.SEAM_DATE, "sessions_sha256": run.sessions_sha256,
                    "late_revenue_codes_sha256": run.inputs["late_codes_sha256"], "code": module_shas(),
                    "model_sha256": model["pointer"]["model_sha256"], "recipe_sha256": core.RECIPE_SHA256,
                    "probability_semantics": core.PROBABILITY_SEMANTICS, "label_rule": core.LABEL_RULE}
    header = {"decision_date": str(D.date()), "key_material": key_material, "run_id": run_id(), "trigger": trigger,
              "runtime": runtime_versions(),
              "data_provenance": data.provenance(run.head, run.inputs, run.calendar, core.SEAM_DATE),
              "price_coverage": run.coverage, "panel": run.panel_log, "fresh": fresh, "board_gate": board_check,
              "model": {"model_sha256": model["pointer"]["model_sha256"], "model_id": art["model_id"], "b": b,
                        "fit_cutoff_exclusive": art["slope"].get("fit_cutoff_exclusive"),
                        "activation_id": model["pointer"]["activation_id"],
                        "recipe": {"name": core.RECIPE["name"], "frozen_sha256": core.RECIPE_SHA256}},
              "band": {"method": "B3+CQR252", "nominal": core.BAND_NOMINAL, "b3_table": meta["b3_table"],
                       "cqr": meta["cqr"]},
              "gate": gate_row, "calendar": {"status": run.calendar_check["status"],
                                             "sha256": run.calendar.get("sha256"),
                                             "verified_asof": run.calendar.get("verified_asof"),
                                             "decision_is_verified_session": run.calendar_check["status"] == "ok"},
              "counts": {"rows": meta["rows"], "main": meta["main"], "eligible": meta["eligible"]},
              "notes": list(run.notes), "integrity_flag_stocks": len(flags)}
    if not record:
        return {"status": "not_recorded", "decision_date": str(D.date()), "rows": int(len(frame))}
    result = ledger.record_block(run.root, frame, header, recorded_at=recorded_at)
    result.update(decision_date=str(D.date()), rows=int(len(frame)), b=b, cqr_q=meta["cqr"]["Q"])
    if result["status"] == "conflicting_repeat":
        raise CliError("h63xs_conflicting_repeat", result["entry_key"][:12], EXIT_INTEGRITY)
    return result


def step_settle(run: Run) -> Dict[str, Any]:
    sessions = run.calendar.get("sessions") if run.calendar.get("status") == "ok" else None
    return ledger.settle(run.root, run.P, sessions, price_vintage=run.inputs["supplement"]["manifest_sha256"][:12]
                         + "+" + (run.head.routes["finmind_price_adj"]["sha256"][:12]))


# ------------------------------------------------------------------------------------------------------------------
# commands
# ------------------------------------------------------------------------------------------------------------------
def resolve_root(args: argparse.Namespace) -> Path:
    root = Path(args.root) if getattr(args, "root", None) else reader.root_dir()
    qa = [flag for flag in ("no_lease", "recorded_at") if getattr(args, flag, None)]
    if qa and is_formal(root):
        raise CliError("h63xs_qa_flag_on_formal_root", ",".join(qa), EXIT_USAGE)
    return root


def supplement_sha_for(root: Path, args: argparse.Namespace) -> str:
    explicit = getattr(args, "supplement_sha256", None)
    active = current_model(root)
    if active is not None:
        pinned = ((active["artifact"].get("data_provenance") or {}).get("supplement") or {}).get("manifest_sha256")
        if explicit and explicit != pinned:
            raise CliError("h63xs_supplement_sha_mismatch", "explicit sha differs from the active model", EXIT_USAGE)
        return pinned
    if not explicit:
        raise CliError("h63xs_supplement_required", "--supplement-sha256 is required before the first model",
                       EXIT_USAGE)
    return explicit


def cmd_supplement_build(args: argparse.Namespace) -> int:
    result = data.build_supplement(args.out, historic_head=args.historic_head,
                                   builder_sha256=data.sha256_file(data.__file__))
    print(json.dumps({"manifest_sha256": result["manifest_sha256"],
                      "files": {k: v["sha256"] for k, v in result["manifest"]["files"].items()},
                      "seconds": result["receipt"]["seconds_total"]}, ensure_ascii=False, indent=1))
    return EXIT_OK


def cmd_supplement_verify(args: argparse.Namespace) -> int:
    price, board, info = data.load_supplement(args.dir, args.manifest_sha256)
    print(json.dumps({**info, "price_rows": int(len(price)), "board_rows": int(len(board))}, indent=1))
    return EXIT_OK


def _acceptance(args: argparse.Namespace) -> Optional[Dict[str, Any]]:
    path = getattr(args, "acceptance_receipt", None)
    if not path:
        return None
    return {"receipt_path": str(Path(path).resolve()), "receipt_sha256": data.sha256_file(path)}


def cmd_train(args: argparse.Namespace) -> int:
    root = resolve_root(args)
    supp = supplement_sha_for(root, args)
    run = prepare(root, supp, lease=not args.no_lease)
    try:
        result = step_train(run, kind=args.kind, authorization_text=args.authorization or "",
                            decided_at=args.decided_at, proposal=args.proposal, force=args.force,
                            acceptance=_acceptance(args), supplement_sha=supp)
    finally:
        run.head.close()
    print(json.dumps(core.jsonable(result), ensure_ascii=False, indent=1))
    return EXIT_OK


def cmd_predict(args: argparse.Namespace) -> int:
    root = resolve_root(args)
    supp = supplement_sha_for(root, args)
    run = prepare(root, supp, lease=not args.no_lease)
    try:
        moment = ledger.parse_utc(args.recorded_at) if args.recorded_at else None
        result = step_predict(run, trigger=args.trigger, record=not args.no_record, recorded_at=moment)
    finally:
        run.head.close()
    print(json.dumps(core.jsonable(result), ensure_ascii=False, indent=1))
    return EXIT_OK


def cmd_settle(args: argparse.Namespace) -> int:
    root = resolve_root(args)
    run = prepare(root, supplement_sha_for(root, args), lease=not args.no_lease)
    try:
        result = step_settle(run)
    finally:
        run.head.close()
    print(json.dumps(core.jsonable(result), ensure_ascii=False, indent=1))
    return EXIT_OK


def cmd_review(args: argparse.Namespace) -> int:
    result = ledger.review(resolve_root(args))
    print(json.dumps(core.jsonable(result), ensure_ascii=False, indent=1))
    return EXIT_OK


def cmd_daily(args: argparse.Namespace) -> int:
    """settle -> train (only when due) -> predict -> review in one pinned run; each step isolated; exit = predict."""
    root = resolve_root(args)
    paths = ledger.Paths(root)
    supp = supplement_sha_for(root, args)
    run = prepare(root, supp, lease=not args.no_lease)
    report: Dict[str, Any] = {"decision_date": str(run.D.date()), "central_head_sha256": run.head.sha256}
    code = EXIT_OK
    try:
        for name, fn in (("settle", lambda: step_settle(run)),
                         ("train", lambda: step_train(run, kind="routine_refit", authorization_text="",
                                                      decided_at=None, proposal=None, force=False, acceptance=None,
                                                      supplement_sha=supp)),
                         ("predict", lambda: step_predict(run, trigger=args.trigger, record=True,
                                                          recorded_at=ledger.parse_utc(args.recorded_at)
                                                          if args.recorded_at else None))):
            try:
                report[name] = fn()
                say("%s: %s" % (name, json.dumps(core.jsonable(report[name]), ensure_ascii=False)[:300]))
            except (data.H63XsDataError, CliError, core.H63XsComputationError, ledger.LedgerError) as exc:
                report[name] = {"status": "failed", "code": exc.code, "detail": getattr(exc, "detail", "")}
                ledger.record_event(paths, name + "_failed", code=exc.code, detail=getattr(exc, "detail", ""))
                say("%s failed: %s" % (name, exc))
                if name == "predict":
                    code = getattr(exc, "exit_code", EXIT_INTEGRITY)
            except Exception as exc:  # noqa: BLE001 - every step is isolated; the reason is logged
                report[name] = {"status": "failed", "code": type(exc).__name__, "detail": str(exc)}
                ledger.record_event(paths, name + "_failed", code=type(exc).__name__, detail=str(exc))
                say("%s failed: %s\n%s" % (name, exc, traceback.format_exc()))
                if name == "predict":
                    code = EXIT_IO
    finally:
        run.head.close()
    try:
        report["review"] = {k: v for k, v in ledger.review(root).items() if k in ("verdict", "verdict_zh",
                                                                                  "recorded_blocks", "valid_blocks")}
    except Exception as exc:  # noqa: BLE001
        report["review"] = {"status": "failed", "detail": str(exc)}
    print(json.dumps(core.jsonable(report), ensure_ascii=False, indent=1))
    return code


def cmd_status(args: argparse.Namespace) -> int:
    root = resolve_root(args)
    out: Dict[str, Any] = {"root": str(root)}
    try:
        model = reader.load_model(root)
        out["model"] = {k: model["pointer"].get(k) for k in ("model_sha256", "model_id", "activation_id",
                                                             "first_signal_date", "activated_at")}
        out["model"]["b"] = model["artifact"]["slope"]["b"]
    except reader.H63XsUnavailable as exc:
        out["model"] = {"status": exc.reason}
    try:
        snap = reader.load_latest(root)
        out["latest"] = reader.describe(snap)
    except reader.H63XsUnavailable as exc:
        out["latest"] = {"status": exc.reason}
    paths = ledger.Paths(root)
    if paths.refusal.exists():
        out["latest_refusal"] = json.loads(paths.refusal.read_text(encoding="utf-8"))
    out["display"] = reader.read_display_config(root)
    out["forward"] = reader.load_forward_summary(root)
    print(json.dumps(core.jsonable(out), ensure_ascii=False, indent=1))
    return EXIT_OK


def cmd_rollback(args: argparse.Namespace) -> int:
    root = resolve_root(args)
    path = models_dir(root) / "artifacts" / (args.to + ".json")
    if not path.exists() or data.sha256_file(path) != args.to:
        raise CliError("h63xs_model_integrity", "artifact missing or changed: " + args.to[:12], EXIT_INTEGRITY)
    artifact = json.loads(path.read_text(encoding="utf-8"))
    if (artifact.get("recipe") or {}).get("frozen_sha256") != core.RECIPE_SHA256 or \
            core.slope_guard(artifact["slope"]["b"]) != "ok":
        raise CliError("h63xs_model_integrity", "recipe or guard", EXIT_INTEGRITY)
    active = current_model(root)
    pointer = activate(root, args.to, artifact, authorization_record("rollback", args.authorization,
                                                                     args.decided_at, None),
                       active["pointer"]["model_sha256"] if active else None, artifact.get("acceptance"))
    print(json.dumps({"status": "rolled_back", "activation_id": pointer["activation_id"]}, indent=1))
    return EXIT_OK


def build_parser() -> argparse.ArgumentParser:
    ap = argparse.ArgumentParser(description="H63 U7 cross-sectional rank model series (ADR-0009)")
    sub = ap.add_subparsers(dest="cmd", required=True)

    def common(p: argparse.ArgumentParser, *, qa: bool = True) -> None:
        p.add_argument("--root", help="data root (default %s or env %s)" % (reader.DEFAULT_ROOT, reader.ROOT_ENV))
        p.add_argument("--supplement-sha256", help="manifest sha256 of the price supplement (first model only)")
        if qa:
            p.add_argument("--no-lease", action="store_true", help="QA only (non-formal root)")

    p = sub.add_parser("supplement-build", help="extract the a807 price / board history into a new folder")
    p.add_argument("--out", required=True)
    p.add_argument("--historic-head", default=data.HISTORIC_HEAD)
    p.set_defaults(fn=cmd_supplement_build)
    p = sub.add_parser("supplement-verify", help="verify a supplement folder against its manifest sha")
    p.add_argument("--dir", required=True)
    p.add_argument("--manifest-sha256", required=True)
    p.set_defaults(fn=cmd_supplement_verify)
    p = sub.add_parser("train", help="fit the slope b; activate on first use, routine refit every 63 sessions")
    common(p)
    p.add_argument("--authorization", help="the user's approval text (required for the first activation)")
    p.add_argument("--decided-at", help="when the user decided (YYYYMMDD HH:MM, Asia/Taipei)")
    p.add_argument("--proposal", help="the approved proposal text")
    p.add_argument("--kind", default="initial_activation",
                   choices=("initial_activation", "routine_refit", "recipe_switch"))
    p.add_argument("--force", action="store_true", help="refit and activate even when not due")
    p.add_argument("--acceptance-receipt", help="acceptance receipt to bind into the artifact")
    p.set_defaults(fn=cmd_train)
    p = sub.add_parser("predict", help="full-market snapshot for the head's latest session (one forward block)")
    common(p)
    p.add_argument("--trigger", default="manual", choices=("manual", "daily_task"))
    p.add_argument("--no-record", action="store_true", help="compute and check only; write nothing")
    p.add_argument("--recorded-at", help="QA only: recorded time (UTC, %%Y-%%m-%%dT%%H:%%M:%%S.%%fZ)")
    p.set_defaults(fn=cmd_predict)
    p = sub.add_parser("settle", help="settle every block against the current head (derived files only)")
    common(p)
    p.set_defaults(fn=cmd_settle)
    p = sub.add_parser("review", help="pre-registered review v3 from the frozen labels")
    p.add_argument("--root")
    p.set_defaults(fn=cmd_review)
    p = sub.add_parser("daily", help="settle -> train (when due) -> predict -> review")
    common(p)
    p.add_argument("--trigger", default="daily_task", choices=("manual", "daily_task"))
    p.add_argument("--recorded-at", help=argparse.SUPPRESS)
    p.set_defaults(fn=cmd_daily)
    p = sub.add_parser("status", help="active model, latest snapshot, refusal, display mode, forward summary")
    p.add_argument("--root")
    p.set_defaults(fn=cmd_status)
    p = sub.add_parser("rollback", help="point the active model back to an earlier artifact")
    p.add_argument("--root")
    p.add_argument("--to", required=True)
    p.add_argument("--authorization", required=True)
    p.add_argument("--decided-at")
    p.set_defaults(fn=cmd_rollback)
    return ap


def main(argv: Optional[list] = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        return int(args.fn(args))
    except (CliError, data.H63XsDataError) as exc:
        print(json.dumps({"status": "failed", "code": exc.code, "detail": exc.detail}, ensure_ascii=False))
        return int(exc.exit_code)
    except core.H63XsComputationError as exc:
        print(json.dumps({"status": "failed", "code": exc.code, "detail": exc.detail}, ensure_ascii=False))
        return EXIT_INTEGRITY
    except reader.H63XsUnavailable as exc:
        print(json.dumps({"status": "failed", "code": exc.reason, "detail": exc.detail}, ensure_ascii=False))
        return EXIT_INTEGRITY
    except (ledger.LedgerError, OSError) as exc:
        print(json.dumps({"status": "failed", "code": type(exc).__name__, "detail": str(exc)}, ensure_ascii=False))
        return EXIT_IO


if __name__ == "__main__":
    sys.exit(main())
