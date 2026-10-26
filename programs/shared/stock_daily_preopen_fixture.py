"""Test helper: write a synthetic pre-open snapshot (latest.json + parquet) under a temporary root.

Only for tests of consumers; production snapshots are written by stock_daily_preopen.py.
Usage in tests: set STOCK_DAILY_PREOPEN_ROOT to tmp_path and call write_fixture(tmp_path, ...).
"""
from __future__ import annotations

import datetime as dt
import hashlib
import json
from pathlib import Path

import numpy as np
import pandas as pd

from stock_daily_preopen_reader import CHIP_ROUTES, LATEST_SCHEMA, SNAPSHOT_SCHEMA


def _previous_weekday(iso_day):
    day = dt.date.fromisoformat(iso_day) - dt.timedelta(days=1)
    while day.weekday() >= 5:
        day -= dt.timedelta(days=1)
    return day.isoformat()


def write_fixture(root, signal_date="2026-10-16", target_date="2026-10-19", stock_ids=None, markets=None,
                  us_status="ok", gate_us_up=True, seed=0, model_sha256="f" * 64, chips_status="ok", stale_routes=None,
                  partial_routes=None, partial_coverage=(100, 1215)):
    """``chips_status``: "ok"（預設，五條 t 日籌碼路由都更新到訊號日）或 "stale"（全部停在前一個平日）；
    ``stale_routes`` 只讓指定路由過期（給非空清單即視為 stale）。``partial_routes`` 讓指定路由（未過期者）的中央狀態為
    PARTIAL、coverage 為 ``partial_coverage``（成功, 應有），其餘為 1215/1215。manifest 另有 degraded（美股缺漏或籌碼 stale）。"""
    root = Path(root)
    if chips_status not in ("ok", "stale"):
        raise ValueError(f"chips_status 必須是 ok 或 stale：{chips_status!r}")
    stale = [str(r) for r in (stale_routes or [])]
    unknown = sorted(set(stale) - set(CHIP_ROUTES))
    if unknown:
        raise ValueError(f"stale_routes 含未知路由：{unknown}")
    if chips_status == "stale" and not stale:
        stale = list(CHIP_ROUTES)
    elif stale:
        chips_status = "stale"
    partial = [str(r) for r in (partial_routes or []) if str(r) not in stale]
    unknown = sorted(set(partial) - set(CHIP_ROUTES))
    if unknown:
        raise ValueError(f"partial_routes 含未知路由：{unknown}")
    success, total = partial_coverage
    stock_ids = [str(s) for s in (stock_ids or ["2330", "2317", "2454", "6488", "3008", "1101", "2603", "8069",
                                                "5347", "6147", "3105", "4966"])]
    markets = markets or ["TWSE" if int(s) < 3500 or s in ("3008",) else "TPEx" for s in stock_ids]
    rng = np.random.default_rng(seed)
    n = len(stock_ids)
    p_raw = rng.uniform(0.3, 0.75, n)
    r_raw = rng.normal(0.0, 0.01, n)
    frame = pd.DataFrame({
        "signal_date": signal_date, "target_date": target_date, "stock_id": stock_ids, "market": markets,
        "p_up": np.clip(p_raw * 0.95, 0.01, 0.99), "p_up_raw": p_raw,
        "exp_ret": r_raw * 0.8, "exp_ret_raw": r_raw,
        "signal_close": rng.uniform(20, 900, n).round(2), "adj_close": rng.uniform(20, 900, n),
        "amt20": rng.uniform(1e7, 5e9, n), "feature_coverage": rng.uniform(0.85, 1.0, n),
    })
    frame["tradable"] = (frame["amt20"] >= 2e7) & (frame["signal_close"] >= 10)
    order = frame.sort_values(["p_up", "exp_ret", "market", "stock_id"], ascending=[False, False, True, True]).index
    frame.loc[order, "rank"] = np.arange(1, n + 1)
    tr = frame[frame["tradable"]].sort_values(["p_up", "exp_ret", "market", "stock_id"], ascending=[False, False, True, True]).index
    frame["rank_tradable"] = np.nan
    frame.loc[tr, "rank_tradable"] = np.arange(1, len(tr) + 1)
    frame["rank"] = frame["rank"].astype("int32")
    run_id = f"fixture_{signal_date.replace('-', '')}"
    rel = Path("predictions") / signal_date / f"{run_id}.parquet"
    out = root / rel
    out.parent.mkdir(parents=True, exist_ok=True)
    frame.to_parquet(out, index=False)
    sha = hashlib.sha256(out.read_bytes()).hexdigest()
    csv_out = out.with_suffix(".csv")
    frame.to_csv(csv_out, index=False, encoding="utf-8")
    csv_sha = hashlib.sha256(csv_out.read_bytes()).hexdigest()
    meta = {
        "schema": LATEST_SCHEMA, "snapshot_schema": SNAPSHOT_SCHEMA, "run_id": run_id,
        "signal_date": signal_date, "target_date": target_date, "target_date_source": "fixture",
        "snapshot": rel.as_posix(), "snapshot_sha256": sha, "rows": int(n),
        "snapshot_csv": rel.with_suffix(".csv").as_posix(), "snapshot_csv_sha256": csv_sha,
        "model_id": "preopen_fixture", "model_sha256": model_sha256, "created_at": "2026-10-19T00:05:00Z",
        "us_overnight": {"status": us_status, "expected_us_session": signal_date, "latest_us_session": signal_date,
                         "on_us_sp500": 0.004 if gate_us_up else -0.004},
        "gate_us_up": gate_us_up, "central_head_sha256": "0" * 64,
        "chips_freshness": {"status": chips_status, "signal_date": signal_date,
                            "routes": {r: (_previous_weekday(signal_date) if r in stale else signal_date) for r in CHIP_ROUTES},
                            "route_status": {r: ("PARTIAL" if r in partial else "SUCCESS") for r in CHIP_ROUTES},
                            "coverage": {r: ({"success": int(success), "total": int(total)} if r in partial
                                            else {"success": 1215, "total": 1215}) for r in CHIP_ROUTES},
                            "stale_routes": stale, "partial_routes": partial},
        "degraded": us_status == "missing" or chips_status == "stale", "fixture": True,
    }
    (root / "predictions" / "latest.json").write_text(json.dumps(meta, ensure_ascii=False, indent=2), encoding="utf-8")
    return meta
