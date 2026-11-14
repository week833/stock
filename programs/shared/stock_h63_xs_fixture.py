"""Test-only fixture: a valid H63 U7 data root (active model pointer + one recorded snapshot block).

Uses the real ledger and reader formats, so the single-stock overlay and the Telegram caption tests exercise the
same verification as production.  Never used by the producer; never writes outside the given root.
"""
from __future__ import annotations

import datetime as dt
import hashlib
import json
from pathlib import Path
from typing import Any, Dict, Iterable, Optional

import numpy as np
import pandas as pd

import stock_h63_xs_core as core
import stock_h63_xs_ledger as ledger
import stock_h63_xs_reader as reader

FIXTURE_ROWS = (
    # stock_id, market, main, eligible, p, strength, rank, q10, q90, band_status
    ("2330", "TWSE", True, True, 0.584, "relatively_strong", 312, -0.211, 0.332, "ok"),
    ("2317", "TWSE", True, True, 0.501, "neutral", 530, -0.178, 0.288, "ok"),
    ("6488", "TPEx", True, False, 0.430, "relatively_weak", None, -0.30, 0.41, "own_vol60_missing"),
    ("2454", "TWSE", True, True, 0.611, "relatively_strong", 120, -0.25, 0.40, "ok_no_cqr"),
)


def _frame(signal_date: str, rows: Iterable[tuple]) -> pd.DataFrame:
    records = []
    for sid, market, main, eligible, p, strength, rank, q10, q90, band_status in rows:
        rec: Dict[str, Any] = {"stock_id": sid, "decision_date": signal_date, "market": market, "main": main,
                               "in_scope": main, "eligible": eligible, "n_components": 7}
        for i, name in enumerate(core.FEATURES):
            rec[name] = 0.01 * (i + 1)
            rec["rk_" + name] = 0.1 * (i - 3)
        rec.update(f_vol60=0.32, vol252=0.30, f_money60=2e8, f_nhist=800.0, score=0.05, score_universe_mean=0.0,
                   score_centred=0.05, p_raw=p, p=p, p_status="ok" if main else "out_of_scope",
                   relative_strength=strength, rank_eligible=float(rank) if rank is not None else np.nan,
                   sigma63=0.16, sigma63_b3=0.155, own_vol60_missing=band_status == "own_vol60_missing",
                   b3_bucket=2, b3_q10=q10 + 0.02, b3_q50=0.03, b3_q90=q90 - 0.02,
                   b3cqr_q10=q10, b3cqr_q90=q90, band_status=band_status, revenue_period="2026-09",
                   revenue_available_at="2026-10-13", revenue_stale=False, per_date=signal_date,
                   integrity_flags="", signal_adj_close=100.0, signal_raw_close=100.0)
        records.append(rec)
    frame = pd.DataFrame.from_records(records)
    for c in ("n_components", "b3_bucket"):
        frame[c] = frame[c].astype(np.int64)
    return frame[list(ledger.SNAPSHOT_COLUMNS)].sort_values("stock_id", kind="mergesort").reset_index(drop=True)


def write_model(root: Path, *, b: float = 0.969) -> Dict[str, Any]:
    artifact = {"schema": reader.ARTIFACT_SCHEMA, "model_id": "h63_xs_u7_fixture",
                "recipe": {"name": core.RECIPE["name"], "frozen_sha256": core.RECIPE_SHA256},
                "slope": {"b": b, "fit_cutoff_exclusive": "2026-10-31"}, "first_signal_date": "2026-10-30",
                "data_provenance": {"supplement": {"manifest_sha256": "f" * 64}}}
    raw = (json.dumps(artifact, ensure_ascii=False, sort_keys=True, indent=1) + "\n").encode("utf-8")
    sha = hashlib.sha256(raw).hexdigest()
    path = Path(root) / "models" / "artifacts" / (sha + ".json")
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(raw)
    pointer = {"schema": reader.CURRENT_SCHEMA, "consumer_id": "single_h63", "activation_id": "h63xs-fixture",
               "model_sha256": sha, "model_id": artifact["model_id"], "recipe": artifact["recipe"],
               "first_signal_date": artifact["first_signal_date"], "activated_at": "2026-11-01T00:00:00.000000Z",
               "authorization": {"kind": "initial_activation", "text": "fixture"}, "acceptance": None,
               "previous_model_sha256": None}
    pointer["integrity_sha256"] = reader.pointer_integrity(pointer)
    ledger.replace_json(Path(root) / "models" / "current.json", pointer)
    return {"model_sha256": sha, "pointer": pointer}


def write_fixture(root: Any, *, signal_date: str = "2026-10-16", rows: Optional[Iterable[tuple]] = None,
                  recorded_at: Optional[dt.datetime] = None) -> Dict[str, Any]:
    root = Path(root)
    model = write_model(root)
    frame = _frame(signal_date, rows or FIXTURE_ROWS)
    header = {"decision_date": signal_date, "run_id": "20261016T231500Z_fixture0", "trigger": "manual",
              "key_material": {"decision_date": signal_date, "fixture": True, "model_sha256": model["model_sha256"]},
              "data_provenance": {"central_head_sha256": "c" * 64, "supplement": {"manifest_sha256": "f" * 64}},
              "model": {"model_sha256": model["model_sha256"], "model_id": "h63_xs_u7_fixture", "b": 0.969,
                        "fit_cutoff_exclusive": "2026-10-31", "activation_id": "h63xs-fixture",
                        "recipe": {"name": core.RECIPE["name"], "frozen_sha256": core.RECIPE_SHA256}},
              "band": {"method": "B3+CQR252", "b3_table": {"pool": 1119387}, "cqr": {"Q": 0.109, "cal_rows": 278769}},
              "gate": {"ok": True}}
    result = ledger.record_block(root, frame, header,
                                 recorded_at=recorded_at or dt.datetime(2026, 10, 16, 23, 15, tzinfo=dt.timezone.utc))
    return {"block": result, "model": model, "frame": frame}
