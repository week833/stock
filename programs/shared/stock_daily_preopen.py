"""Producer for the pre-open H1 model (stock_daily_preopen), a model family parallel to C0.

Commands (run with D:\\stock\\.venv\\Scripts\\python.exe -B):
  train     refit the ACTIVE model's frozen recipe (v1 h1pre_final_nogc_xs: 504 sessions, 120-date calibration;
            v2 h1pre_v2_ens: hpC HGB ensemble, 250-date calibration); --recipe NAME --force --authorization TEXT
            switches recipes explicitly (routine monthly refits never switch)
  calibrate refit the Platt / LAD-shrink calibrator of the active recipe on its window of matured OOS dates
  predict   build features from the pinned central head and write the snapshot for the next session;
            the verified market calendar only ANNOTATES (user ruling 2026-11-01: stale data must never block
            the run or the report): manifest calendar_gate.status (ok / stale_signal / signal_gap_unverified /
            signal_not_session / target_weekend / target_fixed_closure / target_declared_closure /
            target_verified_closure / calendar_unavailable / calendar_stale / central_max_date_unknown / closures_file_invalid /
            closed_day_cli_invalid), degraded=true and degraded_reasons when not ok; latest.json is always
            updated when snapshot output succeeds; genuine input/output failures remain errors. A forward-ledger write failure (locked / corrupt
            state\\forward.sqlite3) is recorded in manifest "ledger" (status error) and the snapshot is still
            published; an unparseable --closed-day value or a missing central actual_max_date is annotated, never fatal.
            --strict-calendar (manual QA only) refuses instead (exit non-zero, predictions\\latest_refusal.json);
            --closed-day / config\\announced_closures.json declare known closures; --allow-stale-signal is a no-op
  settle    fill observed returns in the forward ledger (keyed per row, sessions_ahead from the verified
            calendar) and refresh state/summary.json + state/forward_daily.csv; --resettle re-evaluates every
            row; --price-cache / --price-source cache read closes straight from the parquet object cache
  status    print the active model, latest snapshot and forward summary

Data root (override with STOCK_DAILY_PREOPEN_ROOT): D:\\TWStockData\\program_data\\stock_daily_preopen
  models\\current.json, models\\artifacts\\<sha>.joblib(+.json), models\\activations\\*.json
  predictions\\<signal_date>\\<run_id>.parquet(+.json), predictions\\latest.json, predictions\\latest_refusal.json
  state\\forward.sqlite3, state\\summary.json, state\\forward_daily.csv, config\\announced_closures.json, cache\\
The recipe, validation and limits are documented in
D:\\TWStockData\\program_data\\stock_daily_c0\\candidates\\calibration_20261018\\report\\校正報告_20261018.md
Settlement keys, the verified-calendar gate and summary v2 (2026-10-31) are described in programs\\shared\\README.md.
"""
from __future__ import annotations

import argparse
import bisect
import datetime as dt
import hashlib
import json
import os
import re
import sqlite3
import sys
import time
import uuid
from pathlib import Path
from zoneinfo import ZoneInfo

import numpy as np
import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parent))
import stock_daily_preopen_data as pdata  # noqa: E402
import stock_daily_preopen_features as pfeat  # noqa: E402
from stock_daily_preopen_reader import CHIP_ROUTE_LABELS_ZH, CHIP_ROUTES, LATEST_SCHEMA, SNAPSHOT_SCHEMA, root_dir  # noqa: E402

TAIPEI = ZoneInfo("Asia/Taipei")
ARTIFACT_SCHEMA = "tw-stock-preopen-model-v1"
POINTER_SCHEMA = "tw-stock-preopen-current-v1"
SUMMARY_SCHEMA = "tw-stock-preopen-forward-summary-v2"
CALENDAR_SCHEMA = "twstock-updater.verified-market-calendar.v2"
CALENDAR_DIR = pdata.CENTRAL / "_calendar" / "verified"
RESEARCH_DIR = Path(r"D:\TWStockData\program_data\stock_daily_c0\candidates\calibration_20261018")
RESEARCH_OOS = RESEARCH_DIR / "results" / "holdout" / "h1pre_final_nogc_xs" / "preds.parquet"
# 台股固定（國曆）休市日（紀念日及節日實施條例）：元旦、和平紀念日、兒童節、民族掃墓節（清明）、勞動節、國慶日；
# 2025 年起增列教師節 9/28、光復節 10/25、行憲紀念日 12/25。逢週六於前一個上班日（週五）補假、逢週日於次一個上班日
# （週一）補假；兒童節與清明同日時兒童節於前一日放假（逢週四則於後一日）。規則以已驗證日曆 2021-10～2026-10 核對：
# 規則判休市而日曆有交易的日子為 0 個。農曆假日（春節、端午、中秋）、彈性放假與颱風休市無法由規則推得，須以
# config\announced_closures.json 或 --closed-day 宣告，否則只能靠已驗證日曆在事後（結算時）判定。
TW_FIXED_HOLIDAYS = frozenset({(1, 1), (2, 28), (4, 4), (5, 1), (10, 10)})
TW_FIXED_HOLIDAYS_FROM_2025 = frozenset({(9, 28), (10, 25), (12, 25)})
CLOSURES_SCHEMA = "tw-stock-preopen-announced-closures-v1"
REFUSAL_SCHEMA = "tw-stock-preopen-refusal-v1"
MIN_SUMMARY_DAYS = 21  # 樣本日數少於此值時 summary 標示 days_insufficient
BOOTSTRAP_SEED = 20261031
BOOTSTRAP_REPS = 2000
BOOTSTRAP_LEVEL = 0.90
# 結算狀態（predictions.settlement_status）
STATUS_PENDING = "pending"
LEDGER_REL = "state/forward.sqlite3"  # manifest "ledger".path（v3.1）
STATUS_SETTLED_H1 = "settled_h1"
STATUS_SETTLED_MULTI = "settled_multi_session"
STATUS_LEGACY = "legacy_unverified"  # 舊版寫入的報酬，價格來源無法重算：保留值、不計分、不進校準池
STATUS_VOID_TARGET = "void_target_not_session"
STATUS_VOID_SIGNAL = "void_signal_not_session"
STATUS_VOID_ORDER = "void_target_not_after_signal"
VOID_STATUSES = (STATUS_VOID_TARGET, STATUS_VOID_SIGNAL, STATUS_VOID_ORDER)
SETTLED_STATUSES = (STATUS_SETTLED_H1, STATUS_SETTLED_MULTI)
SOURCE_CALENDAR_ONLY = "calendar_only"  # void 列：只用日曆判定，未讀任何價格
SOURCE_LEGACY = "legacy_kept"

# Frozen validated recipe (results\frozen_configs.json, sha256 e53c2416...). Do not edit without re-validation.
RECIPE = {
    "name": "h1pre_final_nogc_xs", "frozen_sha256": "e53c2416b63d8273162c3618564813cd13145052e5ac30f5c3c375fb48c3b28f",
    "horizon": 1, "train_window": 504, "refit_every": 21, "sample_frac": 0.5, "min_hist": 120, "seed": 0,
    "hgb": {"max_iter": 200, "learning_rate": 0.05, "max_leaf_nodes": 31, "min_samples_leaf": 500,
            "l2_regularization": 1.0, "max_features": 1.0, "early_stopping": False, "random_state": 0},
    "reg_loss": "squared_error", "return_target": "xs",
    "market_features": ["on_us_sp500", "on_us_semiconductor", "on_us_nasdaq", "mkt_r1", "mkt_r5", "breadth"],
    "market_alpha": 50.0, "calib_window": 120, "calib_min_dates": 60,
    "tradable": {"min_amt20": 2e7, "min_price": 10.0},
}
RECIPE_V1 = RECIPE
# Recipe v2 (h1_improve_20261021): selected on the 2023-07..2025-12 development period of an isolated
# walk-forward study, confirmed once on 2026-01..10; see the study report referenced in "validation".
RECIPE_V2_OWN = {
    "name": "h1pre_v2_ens",
    "hgb": {
        "max_iter": 300,
        "learning_rate": 0.05,
        "max_leaf_nodes": 63,
        "min_samples_leaf": 1000,
        "l2_regularization": 5.0,
        "max_features": 0.5,
        "early_stopping": False,
        "random_state": 0
    },
    "sources": [
        {
            "seeds": [
                0,
                1,
                2
            ],
            "train_window": 504,
            "recency_halflife": None,
            "weight": 1.0
        },
        {
            "seeds": [
                0
            ],
            "train_window": 756,
            "recency_halflife": None,
            "weight": 1.0
        },
        {
            "seeds": [
                0
            ],
            "train_window": 504,
            "recency_halflife": 252,
            "weight": 1.0
        }
    ],
    "calibrator": "platt_ladshrink",
    "calib_window": 250,
    "calib_min_dates": 60,
    "calib_seed": {
        "source": "h1_improve_20261021_oos",
        "file": "h1pre_v2_ens_oos_807a1005f0ee.parquet",
        "sha256": "807a1005f0eeb1fa08eb3d046b6008d73248639252e57c362ac599683194ec97",
        "origin": "D:\\TWStockData\\program_data\\stock_daily_c0\\candidates\\h1_improve_20261021\\results\\blend_hpC_bag3+hpC_tw756+hpC_hl252__platt_w250\\preds_full.parquet"
    },
    "validation": "D:\\TWStockData\\program_data\\stock_daily_c0\\candidates\\h1_improve_20261021\\report\\H1改良研究報告_20261021.md",
    "model_tag": "preopen_h1v2",
    "frozen_sha256": "84343f31a101ac30c04a2ec997d89f4517ccc2623a3843918a93ca0671409882"
}
RECIPE_V2 = {**RECIPE_V1, **RECIPE_V2_OWN}
RECIPES = {r["name"]: r for r in (RECIPE_V1, RECIPE_V2)}
RECIPE = RECIPE_V1  # module default; train follows the active model (switch only with --recipe), predict the artifact


def active_recipe(root):
    """Recipe of the active artifact (models/current.json), or None when no model is active."""
    pointer = Path(root) / "models" / "current.json"
    if not pointer.exists():
        return None
    cur = json.loads(pointer.read_text(encoding="utf-8"))
    meta_path = Path(root) / "models" / "artifacts" / f"{cur['model_sha256']}.json"
    meta = json.loads(meta_path.read_text(encoding="utf-8")) if meta_path.exists() else {}
    return recipe_of(meta)


def train_recipe(root, requested=None):
    """(recipe to fit, active recipe). Routine refits keep the active recipe; only an explicit request switches."""
    active = active_recipe(root)
    if requested:
        return RECIPES[requested], active
    return (active or RECIPE_V1), active


def recipe_of(meta):
    """Recipe of an artifact's metadata; artifacts written before recipe v2 (or without a name) are v1."""
    return RECIPES.get(((meta or {}).get("recipe") or {}).get("name"), RECIPE_V1)


GROUPS = {
    "PRICE": ["r1", "r2", "r3", "r5", "r10", "r20", "r60", "r120", "r250", "vol5", "vol20", "vol60", "vol_ratio_5_60",
              "ma_dist5", "ma_dist10", "ma_dist20", "ma_dist60", "ma_dist120", "ma_slope20", "rsi14", "hi250", "lo250",
              "dd60", "gap", "intraday", "range", "clv", "upper_shadow", "lower_shadow", "up_ratio20", "streak",
              "limit_up", "limit_dn", "limit_up5"],
    "VOLUME": ["vratio5", "vratio20", "log_amt20", "aratio5_20", "tx_ratio20", "log_price", "avg_trade_size",
               "turnover20", "log_mcap"],
    "CHIPS": ["f_net1", "f_net5", "f_net20", "it_net1", "it_net5", "it_net20", "dl_net1", "dl_net5", "dl_net20",
              "tot_net1", "tot_net5", "tot_net20", "f_net20_sh", "it_net20_sh", "dl_net20_sh", "tot_net20_sh",
              "it_buy_days10", "m_chg1", "m_chg5", "m_chg20", "m_util", "m_sh", "s_chg5", "s_m_ratio", "dt_ratio",
              "dt_ratio5", "ep", "pbr", "dy", "fh", "fh_chg5", "fh_chg20"],
    "FUND": ["rev_yoy", "rev_mom", "rev_yoy3"],
    "REL": ["beta60", "rel_r1", "rel_r5", "rel_r20", "rel_r60", "ind_r5", "ind_r20", "rel_ind_r5", "rel_ind_r20"],
    "CTX": ["mkt_r1", "mkt_r5", "mkt_r20", "mkt_r60", "breadth", "breadth5", "mkt_vol20", "limit_up_frac",
            "limit_dn_frac", "mkt_amt_ratio", "mkt_dispersion", "mkt_f_net", "mkt_r1_TWSE", "mkt_r1_TPEx"],
    "GLOBAL_PRE": ["on_us_sp500", "on_us_nasdaq", "on_us_semiconductor", "on_us_dow", "on_us_technology",
                   "on_us_financials", "on_us10y", "on_wti", "on_gold"],
}
FEATURES = [f for g in ("PRICE", "VOLUME", "CHIPS", "FUND", "REL", "CTX", "GLOBAL_PRE") for f in GROUPS[g]] + ["is_twse"]
ON_FEATURES = GROUPS["GLOBAL_PRE"]


def log(msg):
    print(time.strftime("%H:%M:%S"), msg, flush=True)


def now_utc():
    return dt.datetime.now(dt.timezone.utc)


def sha256_file(path):
    h = hashlib.sha256()
    with open(path, "rb") as fh:
        for chunk in iter(lambda: fh.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def write_json_atomic(path, obj):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(path.name + ".tmp-" + uuid.uuid4().hex)
    tmp.write_text(json.dumps(obj, ensure_ascii=False, indent=2, default=str), encoding="utf-8")
    os.replace(tmp, path)


def write_new(path, data: bytes):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "xb") as fh:  # immutable: refuse to overwrite
        fh.write(data)


class ZScaler:
    def fit(self, X):
        self.med = np.nanmedian(X, axis=0)
        Xf = np.where(np.isnan(X), self.med, X)
        self.mu = Xf.mean(axis=0)
        self.sd = Xf.std(axis=0)
        self.sd[self.sd == 0] = 1.0
        return self

    def transform(self, X):
        Xf = np.where(np.isnan(X), self.med, X)
        return np.clip((Xf - self.mu) / self.sd, -5, 5)


# ---------------------------------------------------------------- verified market calendar
class PreopenCalendarError(RuntimeError):
    """已驗證交易日曆不可用或不符；``reason`` 為機器可讀代碼。"""

    def __init__(self, reason, detail=""):
        super().__init__(f"{reason}: {detail}" if detail else reason)
        self.reason = reason
        self.detail = detail


def calendar_from_sessions(sessions_by_market, *, path=None, sha256=None, source="sessions",
                           verified_asof=None, future_targets_verified=False):
    """以各市場 session 清單（ISO 日期）組成本模組使用的日曆物件（測試與工具用；正式路徑請用 load_verified_calendar）。"""
    sessions = {str(m): sorted({str(d)[:10] for d in days}) for m, days in dict(sessions_by_market).items()}
    union = sorted(set().union(*sessions.values())) if sessions else []
    return {"schema": CALENDAR_SCHEMA, "path": None if path is None else str(path), "sha256": sha256, "source": source,
            "verified_asof": verified_asof or (union[-1] if union else None),
            "future_targets_verified": bool(future_targets_verified), "sessions": sessions,
            "all_sessions": union, "last_session": union[-1] if union else None}


def _read_calendar_file(path, expected_sha=None):
    import stock_daily_calendar as calmod
    path = Path(path)
    raw = path.read_bytes()
    sha = hashlib.sha256(raw).hexdigest()
    if expected_sha and sha != expected_sha:
        raise PreopenCalendarError("preopen_calendar_sha_mismatch", f"{path} sha256 {sha[:12]}… 與 head 宣告 {str(expected_sha)[:12]}… 不符")
    try:
        proof = calmod.validate_verified_market_calendar_v2(json.loads(raw.decode("utf-8-sig")))
    except (ValueError, TypeError, KeyError) as exc:
        raise PreopenCalendarError("preopen_calendar_invalid", f"{path}: {exc}") from exc
    return proof, sha


def _calendar_from_proof(proof, path, sha, source):
    cal = calendar_from_sessions({m: proof["markets"][m]["sessions"] for m in proof["markets"]}, path=path, sha256=sha,
                                 source=source, verified_asof=proof["verified_asof"],
                                 future_targets_verified=proof.get("future_targets_verified"))
    cal["schema"] = proof["schema"]
    return cal


def load_verified_calendar(path=None, routes=None, central=None):
    """已驗證交易日曆（schema twstock-updater.verified-market-calendar.v2，交易日軸 markets.<市場>.sessions）。

    來源優先序：``path``（明確指定）→ 中央 head 的 official_calendar sidecar ``verified_market_calendar``
    （與價格同一個 head，驗 sha256）→ ``<central>\\_calendar\\verified\\<sha256>.json`` 中具有相符內容指紋的其他 v2 proof。
    內容以 stock_daily_calendar.validate_verified_market_calendar_v2 驗證；只用 sessions，不碰實體 axis 檔。
    注意：目前的日曆沒有未來 session（future_sessions 為空、future_axis_verified=false），所以只能判定
    「已過去的日期是不是交易日」，不能事前證明目標日是下一個交易日。
    """
    central = Path(central or pdata.CENTRAL)
    if path:
        proof, sha = _read_calendar_file(path)
        return _calendar_from_proof(proof, path, sha, "explicit")
    sidecar_error = None
    rejected_path = None
    ref = (((routes or {}).get("official_calendar") or {}).get("metadata") or {}).get("verified_market_calendar")
    if isinstance(ref, dict) and ref.get("schema") == CALENDAR_SCHEMA and isinstance(ref.get("ref"), str):
        p = central / ref["ref"]
        try:
            expected_sha = ref.get("sha256")
            if not isinstance(expected_sha, str) or re.fullmatch(r"[0-9a-f]{64}", expected_sha) is None:
                raise PreopenCalendarError("preopen_calendar_sha_missing", str(p))
            proof, sha = _read_calendar_file(p, expected_sha)
            return _calendar_from_proof(proof, p, sha, "head_sidecar")
        except (OSError, ValueError, PreopenCalendarError) as exc:
            # 否決過的同檔不得重讀洗回；其他備援仍須符合內容定址檔名及 v2 proof。
            rejected_path = p.resolve()
            sidecar_error = f"{type(exc).__name__}: {exc}"
            log(f"head sidecar 日曆不可用（{sidecar_error}），改查其他內容定址日曆")
    directory = central / "_calendar" / "verified"
    best = None
    for p in sorted(directory.glob("*.json")) if directory.exists() else []:
        if p.resolve() == rejected_path or re.fullmatch(r"[0-9a-f]{64}", p.stem) is None:
            continue
        try:
            proof, sha = _read_calendar_file(p, p.stem)
        except (OSError, ValueError, PreopenCalendarError):
            continue  # 內容定址不符或不是有效 v2 proof，不將它當成已驗日曆。
        key = (proof["verified_asof"], sha)
        if best is None or key > best[0]:
            best = (key, proof, p, sha)
    if best is None:
        raise PreopenCalendarError("preopen_calendar_unavailable", f"{directory} 沒有可驗證的 v2 日曆"
                                   + (f"；head sidecar 亦不可用（{sidecar_error}）" if sidecar_error else ""))
    cal = _calendar_from_proof(best[1], best[2], best[3], "newest_file")
    cal["sidecar_error"] = sidecar_error
    return cal


def sessions_between(calendar, signal_date, target_date, market=None):
    """(sessions_ahead, status)：signal→target 之間的已驗證 session 數（signal 不計、target 計入）。

    status：verified（兩端都是 session）、unverified_future（target 晚於日曆最後一個 session，無法判定）、
    void_target_not_session、void_signal_not_session、void_target_not_after_signal。
    """
    sess = (calendar.get("sessions") or {}).get(str(market)) if market else None
    sess = sess or calendar["all_sessions"]
    sig, tgt = str(signal_date)[:10], str(target_date)[:10]
    if not sess:
        return None, "unverified_future"
    if tgt <= sig:
        return None, STATUS_VOID_ORDER
    if tgt > sess[-1]:
        return None, "unverified_future"
    j = bisect.bisect_left(sess, tgt)
    if j >= len(sess) or sess[j] != tgt:
        return None, STATUS_VOID_TARGET
    i = bisect.bisect_left(sess, sig)
    if i >= len(sess) or sess[i] != sig:
        return None, STATUS_VOID_SIGNAL
    return int(j - i), "verified"


def qingming(year):
    """民族掃墓節（清明）：2021～2043 年落在 4/4（年份除以 4 餘 0 或 1）或 4/5（餘 2 或 3）。"""
    return dt.date(year, 4, 4) if year % 4 in (0, 1) else dt.date(year, 4, 5)


def fixed_closures(year):
    """該年由規則可推得的休市日（固定假日 + 清明 + 補假），見 TW_FIXED_HOLIDAYS 的說明。"""
    base = {dt.date(year, m, d) for m, d in TW_FIXED_HOLIDAYS}
    if year >= 2025:
        base |= {dt.date(year, m, d) for m, d in TW_FIXED_HOLIDAYS_FROM_2025}
    qm = qingming(year)
    base.add(qm)
    if qm == dt.date(year, 4, 4):  # 兒童節與民族掃墓節同日：兒童節於前一日放假，逢星期四則於後一日
        base.add(dt.date(year, 4, 5) if qm.weekday() == 3 else dt.date(year, 4, 3))
    out = set(base)
    for day in base:
        if day.weekday() == 5:
            out.add(day - dt.timedelta(days=1))
        elif day.weekday() == 6:
            out.add(day + dt.timedelta(days=1))
    return frozenset(out)


def is_fixed_holiday(day):
    return day in fixed_closures(day.year)


def is_known_closure(day, closures=()):
    """週末、規則可推得的固定假日／補假，或已宣告的休市日（config\\announced_closures.json、--closed-day）。"""
    return day.weekday() >= 5 or is_fixed_holiday(day) or day.isoformat() in closures


def closure_kind(day, calendar=None, closures=()):
    """一個日期的休市判定來源：verified_session／verified_closure（在已驗證日曆範圍內）、weekend、fixed_holiday、
    declared_closure、或 unverified（日曆範圍之外且規則／宣告都無法解釋）。"""
    iso = day.isoformat()
    if day.weekday() >= 5:
        return "weekend"
    if calendar and calendar.get("all_sessions"):
        last = calendar["last_session"]
        if iso <= last:
            return "verified_session" if iso in set(calendar["all_sessions"]) else "verified_closure"
    if is_fixed_holiday(day):
        return "fixed_holiday"
    if iso in closures:
        return "declared_closure"
    return "unverified"


def load_announced_closures(root):
    """``<root>\\config\\announced_closures.json``（schema CLOSURES_SCHEMA，``closures`` 為 ISO 日期清單）→
    (frozenset of ISO dates, path or None, error or None)。檔案不存在 → 空集合；格式錯誤 → 空集合並回傳錯誤文字
    （v3：不中止，predict 以 closures_file_invalid 註記並照常發布；設定檔由操作者修正）。"""
    path = Path(root) / "config" / "announced_closures.json"
    if not path.exists():
        return frozenset(), None, None
    try:
        body = json.loads(path.read_text(encoding="utf-8-sig"))
        if body.get("schema") != CLOSURES_SCHEMA or not isinstance(body.get("closures"), list):
            raise ValueError(f"schema 應為 {CLOSURES_SCHEMA} 且 closures 為清單")
        days = frozenset(dt.date.fromisoformat(str(d)).isoformat() for d in body["closures"])
    except (OSError, ValueError, TypeError, AttributeError) as exc:
        return frozenset(), path, f"preopen_closures_invalid: {path} 無法使用：{type(exc).__name__}: {exc}"
    return days, path, None


def parse_closed_days(values):
    """``--closed-day`` 的值（YYYY-MM-DD；date.fromisoformat 亦接受 YYYYMMDD）→ (frozenset of ISO dates, [無法解析的原值])。
    v3.1：格式錯誤不中止——predict 忽略該值、以 closed_day_cli_invalid 註記並照常發布（排程參數打錯不得擋住每日發布；
    與宣告檔的 closures_file_invalid 同一處置）。"""
    days, invalid = set(), []
    for raw in values or ():
        try:
            days.add(dt.date.fromisoformat(str(raw).strip()).isoformat())
        except (ValueError, TypeError):
            invalid.append(str(raw))
    return frozenset(days), invalid


def next_session_guess(day, closures=()):
    """下一個平日且非固定假日／已宣告休市日（日曆沒有未來 session 時的假設值）。"""
    nxt = pdata.next_weekday(day)
    while is_known_closure(nxt, closures):
        nxt = pdata.next_weekday(nxt)
    return nxt


# ---------------------------------------------------------------- panel
def build_panel(root, head_sha=None, next_session=None):
    t0 = time.time()
    inputs = pdata.load_inputs(root / "cache", head_sha=head_sha)
    log(f"inputs loaded in {time.time() - t0:.0f}s (head {inputs['provenance']['central_head_sha256'][:12]})")
    panel, dates = pfeat.build_panel(inputs["px"], inputs["ch"], inputs["rv"], inputs["info"], inputs["g"],
                                     next_session=next_session)
    didx = pd.Series(np.arange(len(dates)), index=dates)
    panel["di"] = didx.reindex(panel["date"]).to_numpy()
    panel["is_twse"] = (panel["market"] == "TWSE").astype("float32")
    log(f"panel {panel.shape} in {time.time() - t0:.0f}s")
    return panel, dates, inputs


# ---------------------------------------------------------------- model
def fit_model(panel, dates, signal_index, recipe=None):
    """Fit exactly like backtest.walk_forward for a fold starting at ``signal_index``."""
    recipe = recipe or RECIPE
    if "sources" in recipe:
        return _fit_ensemble(panel, dates, signal_index, recipe)
    RECIPE_ = recipe
    from sklearn.ensemble import HistGradientBoostingClassifier, HistGradientBoostingRegressor
    from sklearn.linear_model import Ridge
    h = RECIPE_["horizon"]
    last_train = signal_index - h - 1
    first_train = max(0, last_train - RECIPE_["train_window"] + 1)
    p = panel[panel["hist_len"] >= RECIPE_["min_hist"]]
    tr = p[(p["di"] >= first_train) & (p["di"] <= last_train) & p["y1"].notna()]
    rng = np.random.default_rng(RECIPE_["seed"])
    tr = tr.iloc[np.sort(rng.choice(len(tr), int(len(tr) * RECIPE_["sample_frac"]), replace=False))]
    X = tr[FEATURES].to_numpy("float32")
    up = (tr["y1"].to_numpy("float64") > 0).astype(int)
    clf = HistGradientBoostingClassifier(**RECIPE_["hgb"]).fit(X, up)
    reg = HistGradientBoostingRegressor(loss=RECIPE_["reg_loss"], **RECIPE_["hgb"]).fit(X, tr["yx1"].to_numpy("float64"))
    mf = RECIPE_["market_features"]
    dtr = tr.groupby("di").agg({**{f: "first" for f in mf}, "y1": "mean"})
    sc = ZScaler().fit(dtr[mf].to_numpy("float64"))
    ridge = Ridge(alpha=RECIPE_["market_alpha"]).fit(sc.transform(dtr[mf].to_numpy("float64")), dtr["y1"].to_numpy())
    info = {"train_first_date": str(dates[first_train].date()), "train_last_date": str(dates[last_train].date()),
            "train_rows": int(len(tr)), "train_dates": int(tr["di"].nunique()),
            "first_signal_date": str(dates[signal_index].date()) if signal_index < len(dates) else None}
    return {"clf": clf, "reg": reg, "market_scaler": sc, "market_ridge": ridge}, info


def _fit_ensemble(panel, dates, signal_index, recipe):
    """Recipe v2: weighted average of sources; per source, seed members share the training window and the
    day-level ridge is fitted on the first seed's subsample (research engine fit_fold, verified parity)."""
    from sklearn.ensemble import HistGradientBoostingClassifier, HistGradientBoostingRegressor
    from sklearn.linear_model import Ridge
    h = recipe["horizon"]
    last_train = signal_index - h - 1
    p = panel[panel["hist_len"] >= recipe["min_hist"]]
    mf = recipe["market_features"]
    sources, infos = [], []
    for src in recipe["sources"]:
        first_train = max(0, last_train - int(src["train_window"]) + 1)
        tr = p[(p["di"] >= first_train) & (p["di"] <= last_train) & p["y1"].notna()]
        clfs, regs, first_sub = [], [], None
        for seed in src["seeds"]:
            rng = np.random.default_rng(int(seed))
            trs = tr.iloc[np.sort(rng.choice(len(tr), int(len(tr) * recipe["sample_frac"]), replace=False))]
            if first_sub is None:
                first_sub = trs
            X = trs[FEATURES].to_numpy("float32")
            up = (trs["y1"].to_numpy("float64") > 0).astype(int)
            w = None
            if src.get("recency_halflife"):
                w = np.power(0.5, (last_train - trs["di"].to_numpy("float64")) / float(src["recency_halflife"]))
            hp = {**recipe["hgb"], "random_state": int(seed)}
            clfs.append(HistGradientBoostingClassifier(**hp).fit(X, up, sample_weight=w))
            regs.append(HistGradientBoostingRegressor(loss=recipe["reg_loss"], **hp).fit(
                X, trs["yx1"].to_numpy("float64"), sample_weight=w))
        dtr = first_sub.groupby("di").agg({**{f: "first" for f in mf}, "y1": "mean"})
        sc = ZScaler().fit(dtr[mf].to_numpy("float64"))
        ridge = Ridge(alpha=recipe["market_alpha"]).fit(sc.transform(dtr[mf].to_numpy("float64")), dtr["y1"].to_numpy())
        sources.append({"clfs": clfs, "regs": regs, "market_scaler": sc, "market_ridge": ridge, "weight": float(src["weight"])})
        infos.append({"train_first_date": str(dates[first_train].date()), "train_rows": int(len(tr)),
                      "seeds": list(src["seeds"]), "train_window": int(src["train_window"]),
                      "recency_halflife": src.get("recency_halflife")})
    info = {"train_first_date": min(i["train_first_date"] for i in infos), "train_last_date": str(dates[last_train].date()),
            "train_rows": int(max(i["train_rows"] for i in infos)), "sources": infos,
            "first_signal_date": str(dates[signal_index].date()) if signal_index < len(dates) else None}
    return {"sources": sources}, info


def _market_part(model, rows, mf):
    drows = rows.groupby("di")[mf].first()
    mk = pd.Series(model["market_ridge"].predict(model["market_scaler"].transform(drows[mf].to_numpy("float64"))),
                   index=drows.index)
    return mk.reindex(rows["di"]).to_numpy()


def predict_raw(model, rows, recipe=None):
    X = rows[FEATURES].to_numpy("float32")
    if "sources" in model:
        mf = (recipe or RECIPE_V2)["market_features"]
        tot = sum(s["weight"] for s in model["sources"])
        p = np.zeros(len(rows))
        r = np.zeros(len(rows))
        for s in model["sources"]:
            ps = np.mean([c.predict_proba(X)[:, 1] for c in s["clfs"]], axis=0)
            rs = np.mean([g.predict(X) for g in s["regs"]], axis=0) + _market_part(s, rows, mf)
            p += s["weight"] / tot * ps
            r += s["weight"] / tot * rs
        return p, r
    p = model["clf"].predict_proba(X)[:, 1]
    r = model["reg"].predict(X)
    mf = (recipe or RECIPE_V1)["market_features"]
    drows = rows.groupby("di")[mf].first()
    mk = pd.Series(model["market_ridge"].predict(model["market_scaler"].transform(drows[mf].to_numpy("float64"))),
                   index=drows.index)
    return p, r + mk.reindex(rows["di"]).to_numpy()


def fit_calibrator(pool, window, min_dates, kind="platt_ladshrink"):
    """pool: DataFrame(signal_date, p_raw, r_raw, y) of out-of-sample predictions with matured labels."""
    from sklearn.linear_model import LogisticRegression
    pool = pool[pool["y"].notna()].copy()
    dates = sorted(pool["signal_date"].unique())[-window:]
    pool = pool[pool["signal_date"].isin(dates)]
    if len(dates) < min_dates:
        return {"kind": "identity", "reason": f"only {len(dates)} matured dates"}
    pc = np.clip(pool["p_raw"].to_numpy("float64"), 1e-6, 1 - 1e-6)
    z = np.log(pc / (1 - pc)).reshape(-1, 1)
    if kind == "stack2_ladshrink":
        r_scale = float(np.nanstd(pool["r_raw"].to_numpy("float64"))) or 1.0
        X2 = np.column_stack([z[:, 0], pool["r_raw"].to_numpy("float64") / r_scale])
        lr = LogisticRegression(C=1.0).fit(X2, (pool["y"].to_numpy() > 0).astype(int))
    else:
        lr = LogisticRegression(C=1.0).fit(z, (pool["y"].to_numpy() > 0).astype(int))
    x = pool["r_raw"].to_numpy("float64")
    yy = pool["y"].to_numpy("float64")
    best = None
    for b in np.arange(0.0, 1.55, 0.1):
        a = float(np.median(yy - b * x))
        m = float(np.mean(np.abs(yy - a - b * x)))
        if best is None or m < best[0]:
            best = (m, a, float(b))
    if kind == "stack2_ladshrink":
        return {"kind": "stack2_ladshrink", "stack_coef_z": float(lr.coef_[0, 0]), "stack_coef_r": float(lr.coef_[0, 1]),
                "stack_intercept": float(lr.intercept_[0]), "r_scale": r_scale,
                "lad_a": best[1], "lad_b": best[2], "window_first": str(dates[0])[:10], "window_last": str(dates[-1])[:10],
                "dates": len(dates), "rows": int(len(pool))}
    return {"kind": "platt_ladshrink", "platt_coef": float(lr.coef_[0, 0]), "platt_intercept": float(lr.intercept_[0]),
            "lad_a": best[1], "lad_b": best[2], "window_first": str(dates[0])[:10], "window_last": str(dates[-1])[:10],
            "dates": len(dates), "rows": int(len(pool))}


def apply_calibrator(cal, p_raw, r_raw):
    if cal.get("kind") == "stack2_ladshrink":
        pc = np.clip(np.asarray(p_raw, dtype="float64"), 1e-6, 1 - 1e-6)
        zz = np.log(pc / (1 - pc))
        rr = np.asarray(r_raw, dtype="float64")
        p = 1 / (1 + np.exp(-(cal["stack_intercept"] + cal["stack_coef_z"] * zz + cal["stack_coef_r"] * rr / cal["r_scale"])))
        return p, cal["lad_a"] + cal["lad_b"] * rr
    if cal.get("kind") != "platt_ladshrink":
        return np.asarray(p_raw, dtype="float64"), np.asarray(r_raw, dtype="float64")
    pc = np.clip(np.asarray(p_raw, dtype="float64"), 1e-6, 1 - 1e-6)
    z = np.log(pc / (1 - pc))
    p = 1 / (1 + np.exp(-(cal["platt_coef"] * z + cal["platt_intercept"])))
    r = cal["lad_a"] + cal["lad_b"] * np.asarray(r_raw, dtype="float64")
    return p, r


def save_artifact(root, model, meta, recipe=None):
    import joblib
    import sklearn
    adir = root / "models" / "artifacts"
    adir.mkdir(parents=True, exist_ok=True)
    tmp = adir / f"tmp-{uuid.uuid4().hex}.joblib"
    joblib.dump(model, tmp, compress=3)
    sha = sha256_file(tmp)
    final = adir / f"{sha}.joblib"
    if final.exists():
        tmp.unlink()
    else:
        os.replace(tmp, final)
    meta = {**meta, "schema": ARTIFACT_SCHEMA, "sha256": sha, "sklearn": sklearn.__version__,
            "numpy": np.__version__, "pandas": pd.__version__, "features": FEATURES, "recipe": recipe or RECIPE}
    meta_path = adir / f"{sha}.json"
    if not meta_path.exists():
        write_new(meta_path, json.dumps(meta, ensure_ascii=False, indent=2, default=str).encode("utf-8"))
    return sha, meta


def load_active(root):
    import joblib
    import sklearn
    pointer = root / "models" / "current.json"
    if not pointer.exists():
        raise SystemExit("no active pre-open model (models\\current.json); run train first")
    cur = json.loads(pointer.read_text(encoding="utf-8"))
    if cur.get("schema") != POINTER_SCHEMA:
        raise SystemExit("unexpected pointer schema")
    path = root / "models" / "artifacts" / f"{cur['model_sha256']}.joblib"
    if sha256_file(path) != cur["model_sha256"]:
        raise SystemExit("model artifact sha mismatch")
    meta = json.loads((root / "models" / "artifacts" / f"{cur['model_sha256']}.json").read_text(encoding="utf-8"))
    if meta["sklearn"] != sklearn.__version__:
        raise SystemExit(f"sklearn {sklearn.__version__} differs from training {meta['sklearn']}")
    known = RECIPES.get(meta["recipe"].get("name"))
    if meta["features"] != FEATURES or known is None or meta["recipe"]["frozen_sha256"] != known["frozen_sha256"]:
        raise SystemExit("artifact features/recipe differ from this code")
    return joblib.load(path), cur, meta


# ---------------------------------------------------------------- ledger
LEDGER_SQL = """
CREATE TABLE IF NOT EXISTS predictions (
  signal_date TEXT NOT NULL, target_date TEXT NOT NULL, stock_id TEXT NOT NULL, market TEXT,
  p_up REAL, p_up_raw REAL, exp_ret REAL, exp_ret_raw REAL, rank INTEGER, rank_tradable INTEGER, tradable INTEGER,
  gate_us_up INTEGER, us_status TEXT, model_sha256 TEXT NOT NULL, run_id TEXT NOT NULL, recorded_at TEXT NOT NULL,
  prospective INTEGER NOT NULL, signal_adj_close REAL,
  observed_return REAL, observed_at TEXT,
  sessions_ahead INTEGER, settlement_status TEXT, settlement_source TEXT,
  PRIMARY KEY (signal_date, stock_id, model_sha256, run_id));
CREATE TABLE IF NOT EXISTS calibration_pool (
  signal_date TEXT NOT NULL, stock_id TEXT NOT NULL, p_raw REAL, r_raw REAL, y REAL, source TEXT NOT NULL,
  PRIMARY KEY (signal_date, stock_id, source));
CREATE TABLE IF NOT EXISTS meta (key TEXT PRIMARY KEY, value TEXT);
"""
# 2026-10-31 新增欄位；既有帳冊以 ALTER TABLE … ADD COLUMN 補齊（舊列的值為 NULL，settle 會重新分類）。
LEDGER_COLUMNS_V2 = {"sessions_ahead": "INTEGER", "settlement_status": "TEXT", "settlement_source": "TEXT"}
PREDICTION_COLUMNS = ["signal_date", "target_date", "stock_id", "market", "p_up", "p_up_raw", "exp_ret", "exp_ret_raw",
                      "rank", "rank_tradable", "tradable", "gate_us_up", "us_status", "model_sha256", "run_id",
                      "recorded_at", "prospective", "signal_adj_close", "observed_return", "observed_at",
                      "sessions_ahead", "settlement_status", "settlement_source"]


def ledger(root):
    path = root / "state" / "forward.sqlite3"
    path.parent.mkdir(parents=True, exist_ok=True)
    con = sqlite3.connect(path)
    con.executescript(LEDGER_SQL)
    have = {row[1] for row in con.execute("PRAGMA table_info(predictions)")}
    for col, typ in LEDGER_COLUMNS_V2.items():
        if col not in have:
            con.execute(f"ALTER TABLE predictions ADD COLUMN {col} {typ}")
    con.commit()
    return con


def _prospective_publication(d, keep):
    """Select one eligible run per signal date without mixing stocks from other publications."""
    if d.empty:
        return d
    runs = (d.sort_values(["recorded_at", "run_id"], kind="stable")
            .drop_duplicates(["signal_date", "run_id"], keep="first")
            .drop_duplicates("signal_date", keep=keep))
    keys = pd.MultiIndex.from_frame(runs[["signal_date", "run_id"]])
    return d.loc[pd.MultiIndex.from_frame(d[["signal_date", "run_id"]]).isin(keys)]


def latest_prospective(d):
    """Keep the last eligible publication, ordered by recorded_at then run_id, for each signal date."""
    return _prospective_publication(d, "last")


def first_prospective(d):
    """Keep the first eligible publication for each signal date; first_seen_runs also includes void/pending runs."""
    return _prospective_publication(d, "first")


def first_seen_runs(con, rows=None):
    """signal_date → the earliest prospective run consumers could have seen (any settlement status)."""
    if rows is None:
        df = pd.read_sql("SELECT signal_date, run_id, target_date, recorded_at, COALESCE(settlement_status, 'unclassified') AS status, "
                         "COUNT(*) AS n FROM predictions WHERE prospective=1 GROUP BY signal_date, run_id, target_date, recorded_at, "
                         "settlement_status", con)
    else:
        df = rows.loc[rows["prospective"].eq(1), ["signal_date", "run_id", "target_date", "recorded_at", "settlement_status"]].copy()
        df["status"] = df.pop("settlement_status").fillna("unclassified")
        df = df.groupby(["signal_date", "run_id", "target_date", "recorded_at", "status"], as_index=False).size().rename(columns={"size": "n"})
    out = {}
    if df.empty:
        return out
    runs = df.sort_values("n", ascending=False).groupby(["signal_date", "run_id"], as_index=False).agg(
        target_date=("target_date", "first"), recorded_at=("recorded_at", "min"), status=("status", "first"))  # status＝該 run 最多列的狀態
    for row in runs.sort_values(["signal_date", "recorded_at", "run_id"], kind="stable").itertuples(index=False):
        out.setdefault(str(row.signal_date), {"first_seen_run_id": str(row.run_id), "first_seen_target_date": str(row.target_date),
                                              "first_seen_recorded_at": str(row.recorded_at), "first_seen_status": str(row.status)})
    return out


def _seed_source(recipe):
    return recipe["calib_seed"]["source"] if "calib_seed" in recipe else "research_walkforward_oos"


def seed_calibration_pool(con, recipe=None, root=None):
    """Bootstrap with the walk-forward out-of-sample predictions of the frozen recipe (research holdout run)."""
    recipe = recipe or RECIPE
    source = _seed_source(recipe)
    if con.execute("SELECT COUNT(*) FROM calibration_pool WHERE source=?", (source,)).fetchone()[0]:
        return 0
    path = RESEARCH_OOS
    if "calib_seed" in recipe:
        seed = recipe["calib_seed"]
        path = root_dir(root) / "models" / "seeds" / seed["file"]
        if not path.exists() or sha256_file(path) != seed["sha256"]:
            raise SystemExit(f"校準種子缺漏或雜湊不符：{path}（應為 sha256 {seed['sha256'][:16]}…）")
    pr = pd.read_parquet(path, columns=["date", "stock_id", "p_up", "exp_ret", "y"])
    pr = pr[pr["y"].notna()]
    rows = [(d.strftime("%Y-%m-%d"), str(s), float(p), float(r), float(y), source)
            for d, s, p, r, y in pr[["date", "stock_id", "p_up", "exp_ret", "y"]].itertuples(index=False)]
    con.executemany("INSERT OR IGNORE INTO calibration_pool VALUES (?,?,?,?,?,?)", rows)
    con.commit()
    return len(rows)


def recipe_model_shas(root, recipe):
    out = set()
    for f in (Path(root) / "models" / "artifacts").glob("*.json"):
        try:
            m = json.loads(f.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            continue
        if (m.get("recipe") or {}).get("name") == recipe["name"]:
            out.add(m.get("sha256"))
    return out


# 計分／校準只用「訊號日→目標日恰為 1 個已驗證 session」、由目前價格來源結算（settled_h1，不含 legacy_unverified）
# 且發布於目標日開盤前的列。
SCORABLE_SQL = f"settlement_status='{STATUS_SETTLED_H1}' AND observed_return IS NOT NULL AND prospective=1 AND sessions_ahead=1"


def calibration_pool(con, recipe=None, model_shas=None):
    """Seeded OOS rows of ``recipe`` plus settled live rows (one-session, prospective) of models trained with the same recipe."""
    recipe = recipe or RECIPE
    seeded = pd.read_sql("SELECT signal_date, stock_id, p_raw, r_raw, y FROM calibration_pool WHERE source=?", con,
                         params=(_seed_source(recipe),))
    live = pd.read_sql(f"SELECT * FROM predictions WHERE {SCORABLE_SQL}", con)
    if model_shas is not None:
        live = live[live["model_sha256"].isin(model_shas)]
    live = latest_prospective(live)
    live = live.rename(columns={"p_up_raw": "p_raw", "exp_ret_raw": "r_raw", "observed_return": "y"})[
        ["signal_date", "stock_id", "p_raw", "r_raw", "y"]]
    pool = pd.concat([seeded[~seeded["signal_date"].isin(set(live["signal_date"]))], live], ignore_index=True)
    return pool


# ---------------------------------------------------------------- commands
def cmd_train(args):
    root = root_dir(args.root)
    recipe, active = train_recipe(root, getattr(args, "recipe", None))
    if active is not None and recipe["name"] != active["name"]:
        if not args.force or not (args.authorization or "").strip():
            raise SystemExit(f"切換配方 {active['name']} → {recipe['name']} 需要同時指定 --force 與 --authorization（使用者核准內容）")
    pointer = root / "models" / "current.json"
    if pointer.exists() and not args.force:
        # cheap pre-check (weekdays) before loading 5 years of data; the exact session count follows
        cur = json.loads(pointer.read_text(encoding="utf-8"))
        _, routes, _ = pdata.read_head(args.head)
        latest = str(routes["finmind_price_adj"].get("actual_max_date") or "")[:10]
        if latest and np.busday_count(cur["first_signal_date"], latest) < recipe["refit_every"]:
            log(f"active model {cur['model_sha256'][:12]} serves since {cur['first_signal_date']}; "
                f"latest data {latest} is < {recipe['refit_every']} sessions later; skip (use --force to retrain)")
            return
    panel, dates, inputs = build_panel(root, head_sha=args.head)
    sig = len(dates) - 1 if args.signal_date is None else int(np.searchsorted(dates, pd.Timestamp(args.signal_date)))
    if pointer.exists() and not args.force:
        last = pd.Timestamp(cur["first_signal_date"])
        age = int((dates > last).sum()) if last in dates else recipe["refit_every"]
        if age < recipe["refit_every"]:
            log(f"active model is {age} sessions old (< {recipe['refit_every']}); skip (use --force to retrain)")
            return
    t0 = time.time()
    model, info = fit_model(panel, dates, sig, recipe)
    log(f"[{recipe['name']}] fitted on {info['train_rows']:,} rows {info['train_first_date']}..{info['train_last_date']} in {time.time() - t0:.0f}s")
    con = ledger(root)
    seeded = seed_calibration_pool(con, recipe, root)
    cal = fit_calibrator(calibration_pool(con, recipe, recipe_model_shas(root, recipe)), recipe["calib_window"],
                         recipe["calib_min_dates"], recipe.get("calibrator", "platt_ladshrink"))
    con.close()
    meta = {"model_id": f"{recipe.get('model_tag', 'preopen_h1')}_{info['first_signal_date'].replace('-', '')}", **info,
            "calibrator": cal, "data_provenance": inputs["provenance"], "trained_at": now_utc().isoformat(),
            "validation_report": recipe.get("validation", str(RESEARCH_DIR / "report" / "校正報告_20261018.md"))}
    sha, meta = save_artifact(root, model, meta, recipe)
    prev = json.loads(pointer.read_text(encoding="utf-8")) if pointer.exists() else None
    cur = {"schema": POINTER_SCHEMA, "model_sha256": sha, "model_id": meta["model_id"],
           "first_signal_date": info["first_signal_date"], "activated_at": now_utc().isoformat(),
           "calibrator": cal, "previous_model_sha256": prev["model_sha256"] if prev else None,
           "recipe": {"name": recipe["name"], "frozen_sha256": recipe["frozen_sha256"]},
           "authorization": args.authorization}
    write_json_atomic(root / "models" / "activations" / f"{now_utc().strftime('%Y%m%dT%H%M%SZ')}_{sha[:12]}.json", cur)
    write_json_atomic(pointer, cur)
    log(f"activated model {sha[:12]} [{recipe['name']}] (calibrator {cal['kind']}, seeded pool rows {seeded})")


def cmd_calibrate(args):
    root = root_dir(args.root)
    pointer = root / "models" / "current.json"
    if not pointer.exists():
        raise SystemExit("no active pre-open model (models\\current.json); run train first")
    cur = json.loads(pointer.read_text(encoding="utf-8"))
    con = ledger(root)
    meta = json.loads((root / "models" / "artifacts" / f"{cur['model_sha256']}.json").read_text(encoding="utf-8"))
    recipe = recipe_of(meta)
    seed_calibration_pool(con, recipe, root)
    cal = fit_calibrator(calibration_pool(con, recipe, recipe_model_shas(root, recipe)), recipe["calib_window"],
                         recipe["calib_min_dates"], recipe.get("calibrator", "platt_ladshrink"))
    con.close()
    cur = {**cur, "calibrator": cal, "calibrated_at": now_utc().isoformat()}
    write_json_atomic(pointer, cur)
    log(f"calibrator refit: {cal}")


def resolve_target(signal_date, explicit=None, calendar=None, closures=()):
    """目標日：明確指定 → 台北今日（晚於訊號日、是已驗證 session，或至少是平日且非固定假日／已宣告休市日）→
    下一個假設交易日。

    已驗證日曆沒有未來 session，所以除了 explicit 與「今日已在日曆內」之外都是假設值（target_date_source 如實記錄）。
    """
    if explicit:
        return dt.date.fromisoformat(explicit), "explicit"
    today = now_utc().astimezone(TAIPEI).date()
    if today > signal_date:
        if calendar and today.isoformat() in set(calendar.get("all_sessions") or ()):
            return today, "runtime_today_taipei_verified_session"
        if not is_known_closure(today, closures):
            return today, "runtime_today_taipei"
        return next_session_guess(today, closures), "next_weekday_assumed"  # 今日休市：下一個假設交易日（不回頭猜過去的日子）
    return next_session_guess(signal_date, closures), "next_weekday_assumed"


_DATE_RE = re.compile(r"^(\d{4})-?(\d{2})-?(\d{2})(?:$|[T ])")


def _as_date(value):
    """Central ``actual_max_date`` (YYYY-MM-DD or YYYYMMDD, optionally followed by a time) as a date.

    None when absent, not in that form or not a calendar date. Deliberately no free-form parsing, so
    relative words such as ``today`` can never pass as fresh.
    """
    m = _DATE_RE.match("" if value is None else str(value).strip())
    if not m:
        return None
    try:
        return dt.date(int(m.group(1)), int(m.group(2)), int(m.group(3)))
    except ValueError:
        return None


def central_price_max_date(routes):
    """中央 head 的 ``finmind_price_adj.actual_max_date``（YYYY-MM-DD 或 YYYYMMDD，``_as_date`` 嚴格解析）→ date 或 None。
    v3.1：缺欄位／無法解析回 None（calendar_gate 以 central_max_date_unknown 註記、訊號日以面板最後日為準），不再以
    1900-01-01 代入——那會讓守門誤標「中央價格落後」。"""
    entry = (routes or {}).get("finmind_price_adj")
    return _as_date(entry.get("actual_max_date")) if isinstance(entry, dict) else None


def chips_freshness(provenance, signal_date):
    """Compare the central ``actual_max_date`` of every t-day chip route with the snapshot signal date.

    A route whose latest date is earlier than ``signal_date`` (or unknown) was not updated to the signal
    session: its features then carry the previous session's values or NaN and accuracy drops. ``status`` is
    decided by the date only (user ruling 2026-10-18: no thresholds, data present is usable). Routes the
    central store marks PARTIAL (and that are not stale) go to ``partial_routes`` with their success/total
    counts in ``coverage`` — shown in the report, never blocking. Returns
    {status: ok|stale, signal_date, routes: {route: latest ISO date or None}, route_status: {route: status},
    coverage: {route: {success, total} or None}, stale_routes: [...], partial_routes: [...]}.
    """
    objects = (provenance or {}).get("objects") or {}
    routes, status, coverage, stale, partial = {}, {}, {}, [], []
    for route in CHIP_ROUTES:
        entry = objects.get(route) or {}
        latest = _as_date(entry.get("actual_max_date"))
        routes[route] = latest.isoformat() if latest else None
        status[route] = entry.get("status")
        coverage[route] = entry.get("coverage") if isinstance(entry.get("coverage"), dict) else None
        if latest is None or latest < signal_date:
            stale.append(route)
        elif str(entry.get("status") or "").upper() == "PARTIAL":
            partial.append(route)
    return {"status": "stale" if stale else "ok", "signal_date": signal_date.isoformat(), "routes": routes,
            "route_status": status, "coverage": coverage, "stale_routes": stale, "partial_routes": partial}


def chips_freshness_message(fresh):
    """繁體中文一行說明（日期 YYYYMMDD）：哪些籌碼路由最新到哪一天、哪些中央標示不完整（成功/應有），
    供主控台與拒絕發布訊息使用。"""
    def label(route):
        return f"{CHIP_ROUTE_LABELS_ZH.get(route, route)}（{route}）"

    def one(route):
        day = fresh["routes"].get(route)
        return label(route) + (f"最新到 {str(day)[:10].replace('-', '')}" if day else "日期未知")

    def counts(route):
        cov = (fresh.get("coverage") or {}).get(route) or {}
        ok = cov.get("success") is not None and cov.get("total") is not None
        return label(route) + (f"{cov['success']}/{cov['total']}" if ok else "數量未知")
    signal = str(fresh["signal_date"])[:10].replace("-", "")
    parts = []
    if fresh.get("stale_routes"):
        parts.append(f"未更新到訊號日 {signal}：" + "、".join(one(r) for r in fresh["stale_routes"]))
    if fresh.get("partial_routes"):
        parts.append("資料不完整（中央回報成功/應有）：" + "、".join(counts(r) for r in fresh["partial_routes"]))
    return "t 日籌碼" + "；".join(parts)


def _ymd(day):
    return str(day)[:10].replace("-", "")


GAP_EXPLAINED = ("verified_closure", "weekend", "fixed_holiday", "declared_closure")
# 守門狀態（manifest calendar_gate.status；全部成立的代碼列在 reasons）。依嚴重度排序：目標日問題（結算必 void）→ 日曆不可用
# → 日曆落後 → 中央價格落後 → 訊號日非交易日 → 缺口無法解釋 → 中央最新日未知 → 宣告檔無效 → --closed-day 格式錯。使用者 2026-11-01 裁示
# 「舊資料不會阻擋程式執行及報告發布」：任一不為 ok 只標示 degraded 並照常發布；只有 --strict-calendar（手動 QA）才拒發。
GATE_OK = "ok"
GATE_STATUS_ORDER = ("target_weekend", "target_fixed_closure", "target_declared_closure", "target_verified_closure",
                     "target_not_after_signal", "calendar_unavailable", "calendar_stale", "stale_signal", "signal_not_session",
                     "signal_gap_unverified", "central_max_date_unknown", "closures_file_invalid", "closed_day_cli_invalid")
GATE_TARGET_CODES = {"weekend": "target_weekend", "fixed_holiday": "target_fixed_closure",
                     "declared_closure": "target_declared_closure", "verified_closure": "target_verified_closure"}
GATE_TARGET_WHY_ZH = {"target_weekend": "週末", "target_fixed_closure": "固定假日／補假", "target_declared_closure": "已宣告休市日",
                      "target_verified_closure": "已驗證日曆內的非交易日", "target_not_after_signal": "不晚於訊號日"}
GATE_LEGACY_CODES = {  # v2 的 preopen_* 原因碼：strict 模式的拒發紀錄與結束訊息沿用
    "stale_signal": "preopen_central_price_stale", "calendar_stale": "preopen_calendar_stale", "signal_not_session": "preopen_signal_not_session",
    "signal_gap_unverified": "preopen_signal_gap_unverified", "closures_file_invalid": "preopen_closures_invalid",
    "central_max_date_unknown": "preopen_central_max_date_unknown", "closed_day_cli_invalid": "preopen_closed_day_invalid",
    **{code: "preopen_target_not_session" for code in GATE_TARGET_WHY_ZH},
}


def calendar_gate(signal, px_max, target, calendar, calendar_error=None, closures=(), strict=False, closures_error=None,
                  closed_day_invalid=()):
    """發布守門 v3（2026-11-01 裁示：只註記、不阻擋）。回傳 manifest 的 ``calendar_gate`` 區塊。``px_max`` 為 None 表示中央
    最新日未知（v3.1）。

    ``status``（GATE_STATUS_ORDER 中最嚴重的一項；全部成立者列在 ``reasons``）：
    - target_weekend／target_fixed_closure／target_declared_closure／target_verified_closure／target_not_after_signal：
      目標日為週末、規則可推得的固定假日／補假、已宣告休市日、已驗證日曆內的非交易日、不晚於訊號日（結算時該列標 void、不計分）。
    - calendar_unavailable：中央日曆缺檔／雜湊不符／格式錯（``calendar_error_code``）；目標日仍依 resolve_target 的規則假設。
    - calendar_stale：訊號日晚於已驗證日曆最後 session；只能註記未確認，不能當成已驗證交易日。
    - stale_signal：面板訊號日 ≠ 中央價格 actual_max_date，或訊號日 < 日曆最後一個 session（中央價格落後，舊訊號重發；
      結算時 sessions_ahead≥2 的列標 settled_multi_session、不計分）。注意 predict 預設取與價格同一個 head 的 sidecar 日曆，
      verified_asof 與價格最新日同步，中央落後時這條通常不會觸發（2026-10-29 07:16 的事故即如此），要靠下一條。
    - signal_not_session：訊號日在日曆範圍內但不是交易日。
    - signal_gap_unverified：訊號日與目標日之間有平日既不在已驗證日曆內、也不是固定假日／補假、也未被宣告休市
      （config\\announced_closures.json、--closed-day）：最可能是中央價格落後；颱風等臨時休市後的第一個交易日早上也會出現，
      宣告後即為 ok。
    - central_max_date_unknown（v3.1）：中央 head 缺 finmind_price_adj.actual_max_date 或無法解析（``px_max`` 為 None）：
      訊號日無法與中央最新日核對（不是「中央落後」；與日曆的比對照常）。
    - closures_file_invalid：宣告檔無法使用（已忽略，仍發布）。
    - closed_day_cli_invalid（v3.1）：--closed-day 的值不是 YYYY-MM-DD（已忽略，仍發布；``closed_day_invalid`` 列出原值）。
    任一不為 ok：manifest degraded=true、``reasons`` 併入 degraded_reasons；``refuse`` 只在 ``strict``（--strict-calendar）
    且不為 ok 時為 True。``sessions_ahead_expected``：已驗證時為實際 session 數，否則 1 + 缺口中已驗證交易日與無法解釋平日的
    數目（中央落後時這些平日很可能是交易日）。日曆沒有未來 session，目標日晚於日曆最後一個 session 時只能標示 unverified_future
    （屬 ok），無法事前證明它是下一個交易日。
    """
    note = []
    closures = frozenset(closures or ())
    reasons = []
    gate = {"status": GATE_OK, "reasons": reasons, "policy": "strict" if strict else "annotate",
            "calendar_status": None, "calendar_error_code": None, "calendar_error": None,
            "calendar_source": None, "calendar_vintage": None, "signal_date": signal.isoformat(),
            "central_price_max_date": None if px_max is None else px_max.isoformat(),
            "signal_is_verified_session": None, "signal_is_last_verified_session": None,
            "last_verified_session": None, "target_date": target.isoformat(), "target_calendar_status": None,
            "target_closure_kind": None, "sessions_ahead_verified": None, "sessions_ahead_expected": 1, "gap_days": [],
            "declared_closures": sorted(closures), "closures_file_error": closures_error,
            "closed_day_invalid": [str(v) for v in (closed_day_invalid or ())],
            "reason_code": None, "target_not_session": False, "refuse": False, "strict": bool(strict), "note_zh": ""}

    def add(code):
        if code not in reasons:
            reasons.append(code)

    n, status = None, None  # sessions_between 的結果（日曆不可用時沒有）
    if calendar is None:
        gate["calendar_status"] = "unavailable"
        gate["calendar_error_code"] = getattr(calendar_error, "reason", "preopen_calendar_unavailable")
        gate["calendar_error"] = None if calendar_error is None else str(calendar_error)
        add("calendar_unavailable")
        note.append(f"已驗證日曆不可用（{calendar_error}），無法核對訊號日與目標日；目標日依規則假設")
    else:
        gate["calendar_status"] = "ok"
        gate["calendar_source"] = {k: calendar.get(k) for k in ("path", "sha256", "verified_asof", "last_session", "source",
                                                                 "future_targets_verified", "schema")}
        same_head = calendar.get("source") == "head_sidecar"
        gate["calendar_vintage"] = {"source": calendar.get("source"), "verified_asof": calendar.get("verified_asof"),
                                    "last_session": calendar.get("last_session"), "same_head_as_prices": same_head,
                                    "sidecar_error": calendar.get("sidecar_error"),
                                    "note_zh": ("日曆與價格出自同一個 head，verified_asof 與價格最新日同步；『訊號日 = 日曆最後 session』"
                                                "不能證明中央沒有落後" if same_head else "日曆與價格不是同一個版本")}
        sessions = set(calendar["all_sessions"])
        last = calendar["last_session"]
        gate["last_verified_session"] = last
        gate["signal_is_verified_session"] = signal.isoformat() in sessions
        gate["signal_is_last_verified_session"] = signal.isoformat() == last
        if px_max is not None and signal != px_max:
            add("stale_signal")
            note.append(f"面板訊號日 {_ymd(signal)} 與中央價格最新日 {_ymd(px_max)} 不一致（中央價格落後，舊訊號重發）")
        elif last and signal.isoformat() < last:
            add("stale_signal")
            who = f"中央價格最新日 {_ymd(px_max)}" if px_max is not None else f"面板訊號日 {_ymd(signal)}"
            note.append(f"{who} 早於已驗證日曆最後交易日 {_ymd(last)}（中央價格落後，舊訊號重發）")
        if last and signal.isoformat() > last:
            add("calendar_stale")
            gate["calendar_status"] = "stale"
            note.append(f"訊號日 {_ymd(signal)} 晚於已驗證日曆最後交易日 {_ymd(last)}（日曆落後於價格，無法驗證訊號日）")
        elif calendar["all_sessions"] and calendar["all_sessions"][0] <= signal.isoformat() <= last and not gate["signal_is_verified_session"]:
            add("signal_not_session")
            note.append(f"訊號日 {_ymd(signal)} 不是已驗證交易日")
        n, status = sessions_between(calendar, signal, target)
        gate["sessions_ahead_verified"] = n
        if status == "verified":
            gate["target_calendar_status"] = "verified_session"
            if n != 1:
                note.append(f"目標日 {_ymd(target)} 距訊號日 {n} 個已驗證 session（預期 1；結算時標 settled_multi_session、不計分）")
        elif status == "unverified_future":
            gate["target_calendar_status"] = "unverified_future"
            note.append(f"目標日 {_ymd(target)} 晚於已驗證日曆範圍，無法事前證明它是下一個交易日（假設為 1 個 session）")
        else:
            gate["target_calendar_status"] = status
            if status == STATUS_VOID_SIGNAL and "signal_not_session" not in reasons:
                add("signal_not_session")
                note.append(f"訊號日 {_ymd(signal)} 不是已驗證交易日")
    if px_max is None:  # v3.1：中央最新日未知只註記，不當成「中央落後」（1900-01-01 代入會誤標 stale_signal）
        add("central_max_date_unknown")
        note.append("中央價格最新日未知（head 中繼資料缺 finmind_price_adj.actual_max_date 或無法解析），訊號日以面板最後日為準、未與中央核對")
    # 訊號日與目標日之間的平日：每一天都要能解釋（已驗證休市、固定假日／補假、已宣告休市），否則視為中央落後
    gap, unexplained, assumed_sessions = [], [], 0
    day = signal + dt.timedelta(days=1)
    while day < target:
        if day.weekday() < 5:
            kind = closure_kind(day, calendar, closures)
            gap.append({"date": day.isoformat(), "status": kind})
            if kind == "unverified":
                unexplained.append(day)
            if kind in ("unverified", "verified_session"):
                assumed_sessions += 1
        day += dt.timedelta(days=1)
    gate["gap_days"] = gap
    gate["sessions_ahead_expected"] = n if (calendar is not None and status == "verified") else 1 + assumed_sessions
    if unexplained:
        add("signal_gap_unverified")
        note.append("訊號日與目標日之間的平日 " + "、".join(_ymd(d) for d in unexplained)
                    + " 不在已驗證日曆內、也不是已知休市日：中央價格很可能落後（若為臨時休市請以 --closed-day 或 "
                      "config\\announced_closures.json 宣告）")
    tkind = closure_kind(target, calendar, closures)
    gate["target_closure_kind"] = tkind
    target_code = GATE_TARGET_CODES.get(tkind)
    if target_code is None and gate["target_calendar_status"] == STATUS_VOID_ORDER:
        target_code = "target_not_after_signal"
    if target_code:
        add(target_code)
        note.append(f"目標日 {_ymd(target)} 為{GATE_TARGET_WHY_ZH[target_code]}（結算時該列標 void、不計分）")
    if closures_error:
        add("closures_file_invalid")
        note.append(f"宣告休市設定檔無法使用，已忽略（{closures_error}）")
    if closed_day_invalid:  # v3.1：命令列宣告格式錯誤只忽略（排程參數打錯不得擋住發布）
        add("closed_day_cli_invalid")
        note.append("--closed-day 的值 " + "、".join(str(v) for v in closed_day_invalid) + " 不是 YYYY-MM-DD，已忽略")
    ordered = [code for code in GATE_STATUS_ORDER if code in reasons]
    reasons[:] = ordered
    gate["status"] = ordered[0] if ordered else GATE_OK
    gate["target_not_session"] = bool(target_code)
    if ordered:
        gate["reason_code"] = (gate["calendar_error_code"] if gate["status"] == "calendar_unavailable"
                               else GATE_LEGACY_CODES[gate["status"]])
        gate["refuse"] = bool(strict)
        note.append("--strict-calendar：拒絕發布" if strict else "依 2026-11-01 裁示照常發布並標示 degraded")
    gate["note_zh"] = "；".join(note)
    return gate


def write_refusal(root, gate, run_context):
    """predictions\\latest_refusal.json：--strict-calendar（手動 QA）拒發的可辨識紀錄（latest.json 不更新）；預設的每日
    流程一律發布、不寫此檔，下一次成功發布時刪除。消費端的 reader 不讀取此檔。"""
    body = {"schema": REFUSAL_SCHEMA, "refused_at": now_utc().isoformat(), "signal_date": gate["signal_date"],
            "target_date": gate["target_date"], "reason_code": gate["reason_code"], "note_zh": gate["note_zh"],
            "calendar_gate": gate, **run_context}
    write_json_atomic(Path(root) / "predictions" / "latest_refusal.json", body)
    return body


def cmd_predict(args):
    root = root_dir(args.root)
    model, cur, meta = load_active(root)
    recipe = recipe_of(meta)
    head_sha, routes, _ = pdata.read_head(args.head)
    px_max = central_price_max_date(routes)  # None：中央最新日未知（v3.1 註記 central_max_date_unknown，不當成落後）
    if px_max is None:
        log("中央價格最新日未知（head 中繼資料缺 finmind_price_adj.actual_max_date 或無法解析）：訊號日以面板最後日為準，"
            "守門註記 central_max_date_unknown（照常發布）")
    calendar, calendar_error = None, None
    try:
        calendar = load_verified_calendar(args.calendar, routes)
    except (OSError, ValueError, PreopenCalendarError) as exc:
        calendar_error = exc
        log(f"已驗證日曆讀取失敗：{exc}")
    announced, closures_path, closures_error = load_announced_closures(root)
    if closures_error:
        log(f"宣告休市設定檔無法使用，已忽略（照常發布）：{closures_error}")
    declared, closed_day_invalid = parse_closed_days(args.closed_day)
    if closed_day_invalid:
        log(f"--closed-day 的值 {'、'.join(closed_day_invalid)} 不是 YYYY-MM-DD，已忽略（照常發布，守門註記 closed_day_cli_invalid）")
    closures = announced | declared
    signal_guess = px_max if px_max is not None else dt.date(1900, 1, 1)  # 未知時先以極早日期猜，面板讀出訊號日後重算目標日
    target, target_src = resolve_target(signal_guess, args.target_date, calendar, closures)
    panel, dates, inputs = build_panel(root, head_sha=head_sha, next_session=pd.Timestamp(target))
    signal = dates[-1].date()
    if signal != signal_guess:
        target, target_src = resolve_target(signal, args.target_date, calendar, closures)
        panel, dates, inputs = build_panel(root, head_sha=head_sha, next_session=pd.Timestamp(target))
    if target <= signal:  # 只有明確給錯 --target-date 才會發生（resolve_target 的結果一定晚於訊號日）
        raise SystemExit(f"target {target} must be after signal {signal}")
    gate = calendar_gate(signal, px_max, target, calendar, calendar_error, closures=closures,
                         strict=args.strict_calendar, closures_error=closures_error, closed_day_invalid=closed_day_invalid)
    gate["closures_file"] = None if closures_path is None else str(closures_path)
    gate["declared_closures_cli"] = sorted(declared)
    gate["allow_stale_signal"] = bool(args.allow_stale_signal)  # 相容旗標：v3 起無作用，只記錄
    if gate["refuse"]:  # 只有 --strict-calendar（手動 QA）才拒發；預設一律發布
        if args.no_latest:  # QA 快照本來就不發布、不入帳：只記錄守門結果，不中止
            log(f"日曆守門（--strict-calendar、--no-latest：QA 快照仍寫出、不發布、不入帳；正式發布時將以 {gate['reason_code']} 擋下）："
                f"{gate['note_zh']}")
        else:
            write_refusal(root, gate, {"central_head_sha256": head_sha, "model_sha256": cur["model_sha256"],
                                       "target_date_source": target_src})
            raise SystemExit(f"{gate['reason_code']}: --strict-calendar 拒絕發布：{gate['note_zh']}")
    elif gate["status"] != GATE_OK:
        log(f"日曆守門（{gate['status']}；只註記、照常發布並標示 degraded）：{gate['note_zh']}")
    elif gate["note_zh"]:
        log("日曆守門：" + gate["note_zh"])
    us = pdata.us_overnight_status(inputs["g"], signal, target)
    rows = panel[(panel["di"] == len(dates) - 1) & (panel["hist_len"] >= recipe["min_hist"])].copy()
    if us["status"] == "missing":
        # 舊 --allow-missing-us 僅相容命令；缺漏一律沿用既有缺值推論與 degraded 標示。
        rows[ON_FEATURES] = np.nan
    fresh = chips_freshness(inputs["provenance"], signal)
    if fresh["status"] == "stale":
        detail = chips_freshness_message(fresh)
        log(f"{detail}；仍發布但標示 degraded（以舊值推論，準確度可能下降）")
    elif fresh["partial_routes"]:
        log(f"{chips_freshness_message(fresh)}；仍發布（只在說明標示，不阻擋）")
    on_sp = rows["on_us_sp500"].iloc[0] if len(rows) else np.nan
    gate_us = None if us["status"] == "missing" or pd.isna(on_sp) else bool(on_sp > 0)
    p_raw, r_raw = predict_raw(model, rows, recipe) if "sources" in recipe else predict_raw(model, rows)
    p, r = apply_calibrator(cur["calibrator"], p_raw, r_raw)
    tcfg = recipe["tradable"]
    snap = pd.DataFrame({
        "signal_date": signal.isoformat(), "target_date": target.isoformat(), "stock_id": rows["stock_id"].astype(str).to_numpy(),
        "market": rows["market"].astype(str).to_numpy(), "p_up": p, "p_up_raw": p_raw, "exp_ret": r, "exp_ret_raw": r_raw,
        "signal_close": rows["raw_close"].to_numpy("float64"), "adj_close": rows["adj_close"].to_numpy("float64"),
        "amt20": rows["amt20"].to_numpy("float64"),
        "feature_coverage": rows[FEATURES].notna().mean(axis=1).to_numpy("float64"),
        "limit_up": rows["limit_up"].to_numpy("float64"),
    })
    snap["tradable"] = (snap["amt20"] >= tcfg["min_amt20"]) & (snap["signal_close"] >= tcfg["min_price"]) & (snap["limit_up"] < 0.5)
    order = snap.sort_values(["p_up", "exp_ret", "market", "stock_id"], ascending=[False, False, True, True]).index
    snap.loc[order, "rank"] = np.arange(1, len(snap) + 1)
    snap["rank"] = snap["rank"].astype("int32")
    tr_order = snap[snap["tradable"]].sort_values(["p_up", "exp_ret", "market", "stock_id"], ascending=[False, False, True, True]).index
    snap["rank_tradable"] = np.nan
    snap.loc[tr_order, "rank_tradable"] = np.arange(1, len(tr_order) + 1)
    snap = snap.drop(columns=["limit_up"])
    created = now_utc()
    run_id = f"{created.strftime('%Y%m%dT%H%M%SZ')}_{uuid.uuid4().hex[:8]}"
    rel = Path("predictions") / signal.isoformat() / f"{run_id}.parquet"
    out = root / rel
    out.parent.mkdir(parents=True, exist_ok=True)
    tmp = out.with_suffix(".tmp.parquet")
    snap.to_parquet(tmp, index=False)
    os.replace(tmp, out)
    snap_sha = sha256_file(out)
    csv_out = out.with_suffix(".csv")  # companion for consumers whose environment has no parquet engine
    snap.to_csv(csv_out, index=False, encoding="utf-8")
    csv_sha = sha256_file(csv_out)
    manifest = {
        "schema": LATEST_SCHEMA, "snapshot_schema": SNAPSHOT_SCHEMA, "run_id": run_id,
        "signal_date": signal.isoformat(), "target_date": target.isoformat(), "target_date_source": target_src,
        "snapshot": rel.as_posix(), "snapshot_sha256": snap_sha, "rows": int(len(snap)),
        "snapshot_csv": rel.with_suffix(".csv").as_posix(), "snapshot_csv_sha256": csv_sha,
        "tradable_rows": int(snap["tradable"].sum()),
        "model_id": cur["model_id"], "model_sha256": cur["model_sha256"], "calibrator": cur["calibrator"],
        "recipe": {"name": recipe["name"], "frozen_sha256": recipe["frozen_sha256"]},
        "created_at": created.isoformat(), "us_overnight": {**us, "on_us_sp500": None if pd.isna(on_sp) else float(on_sp)},
        "gate_us_up": gate_us, "central_head_sha256": inputs["provenance"]["central_head_sha256"],
        "data_provenance": inputs["provenance"], "chips_freshness": fresh,
        "calendar_source": gate["calendar_source"],
        "calendar_vintage": gate["calendar_vintage"],
        "signal_is_last_verified_session": gate["signal_is_last_verified_session"],
        "sessions_ahead_expected": gate["sessions_ahead_expected"],
        "calendar_gate": gate,
        "degraded_reasons": ([f"us_overnight_{us['status']}"] if us["status"] == "missing" else [])
                            + (["chips_stale"] if fresh["status"] == "stale" else [])
                            + list(gate["reasons"]),
        "degraded": us["status"] == "missing" or fresh["status"] == "stale" or gate["status"] != GATE_OK,
    }
    target_open = dt.datetime.combine(target, dt.time(9, 0), TAIPEI)
    prospective = int(created < target_open)
    if args.no_latest:  # test / QA run: neither published nor recorded in the forward ledger
        manifest["ledger"] = {"status": "skipped", "error": None, "rows": 0, "path": LEDGER_REL}
        write_new(out.with_suffix(".json"), json.dumps(manifest, ensure_ascii=False, indent=2, default=str).encode("utf-8"))
        log(f"snapshot {rel} written without publication (--no-latest); ledger untouched; "
            f"rows={len(snap)} tradable={int(snap['tradable'].sum())} us={us['status']} chips={fresh['status']} gate={gate_us}")
        return
    recs = [(signal.isoformat(), target.isoformat(), s, m, float(pu), float(pr_), float(eu), float(er), int(rk),
             None if pd.isna(rt) else int(rt), int(tb), None if gate_us is None else int(gate_us), us["status"],
             cur["model_sha256"], run_id, created.isoformat(), prospective, float(ac), None, None,
             gate["sessions_ahead_verified"], STATUS_PENDING, None)
            for s, m, pu, pr_, eu, er, rk, rt, tb, ac in snap[["stock_id", "market", "p_up", "p_up_raw", "exp_ret",
                                                              "exp_ret_raw", "rank", "rank_tradable", "tradable", "adj_close"]].itertuples(index=False)]
    # v3.1：帳冊 state\forward.sqlite3 被鎖定／損毀／不可建立時仍發布——這是「舊狀態檔」，不得擋住報告（2026-11-01 裁示）；
    # manifest "ledger" 註記（status ok／error／skipped），結算端找不到該列就不計分、不進校準池。入帳在寫 manifest 與
    # latest.json 之前完成，兩者的 ledger 區塊一致；帳冊問題不影響預測品質，故不標 degraded。
    ledger_error = None
    try:
        con = ledger(root)
        try:
            con.executemany(f"INSERT OR IGNORE INTO predictions ({', '.join(PREDICTION_COLUMNS)}) "
                            f"VALUES ({', '.join('?' for _ in PREDICTION_COLUMNS)})", recs)
            con.commit()
        finally:
            con.close()
    except (sqlite3.Error, OSError) as exc:
        ledger_error = f"{type(exc).__name__}: {exc}"
        log(f"帳冊寫入失敗（照常發布；本次快照未入帳，結算時不計分、不進校準池；請檢修 {root / 'state' / 'forward.sqlite3'}）：{ledger_error}")
    manifest["ledger"] = {"status": "error" if ledger_error else "ok", "error": ledger_error,
                          "rows": 0 if ledger_error else len(recs), "path": LEDGER_REL}
    write_new(out.with_suffix(".json"), json.dumps(manifest, ensure_ascii=False, indent=2, default=str).encode("utf-8"))
    write_json_atomic(root / "predictions" / "latest.json", manifest)
    refusal = root / "predictions" / "latest_refusal.json"
    if refusal.exists():  # 先前的拒發紀錄已被本次發布取代
        refusal.unlink()
    top = snap[snap["tradable"]].sort_values("rank_tradable").head(10)
    log(f"snapshot {rel} rows={len(snap)} tradable={int(snap['tradable'].sum())} us={us['status']} chips={fresh['status']} gate={gate_us} "
        f"prospective={prospective} target_status={gate['target_calendar_status']}")
    log("top10: " + ", ".join(f"{s}({p:.3f})" for s, p in zip(top["stock_id"], top["p_up"])))


# ---------------------------------------------------------------- settlement
def _needed_dates(rows):
    return set(rows["signal_date"].astype(str)) | set(rows["target_date"].astype(str))


def _iso_dates(series):
    s = series.astype(str)
    return s.str.slice(0, 4) + "-" + s.str.slice(4, 6) + "-" + s.str.slice(6, 8)


def official_close_keys(paths, dates):
    """official_market_daily 物件快取 parquet 中「該檔該日有正的官方收盤價」的 (stock_id, ISO date) 集合。
    官方日報對無成交（或只有盤後零股）的日子收盤價為空，而 finmind 還原序列會沿用前一日收盤；
    沒有這個過濾，直讀 finmind 快取會把這些日子結算成 0% 報酬（帳冊副本試算：每日約 20 列）。"""
    import pyarrow.parquet as pq
    wanted = sorted({str(d)[:10].replace("-", "") for d in dates})
    keys = set()
    for path in paths:
        table = pq.read_table(Path(path), columns=["date", "stock_id", "market", "close"],
                              filters=[("date", "in", wanted)] if wanted else None)
        df = table.to_pandas()
        del table
        df["stock_id"] = df["stock_id"].astype(str)
        df = df[df["stock_id"].str.fullmatch(r"[1-9]\d{3}") & df["market"].isin(["TWSE", "TPEx"])]
        df["close"] = pd.to_numeric(df["close"], errors="coerce")
        df = df[df["close"] > 0].drop_duplicates(["stock_id", "date"], keep="last")
        keys |= set(zip(df["stock_id"], _iso_dates(df["date"])))
    return keys


def close_lookup_from_cache(paths, dates, official_paths=None):
    """直讀 finmind_price_adj 物件快取 parquet（只讀 date/stock_id/close；close 即還原收盤），回傳
    [(label, {(stock_id, ISO date): close})]（每個檔一個查表；同一筆報酬的兩端只取同一個檔，避免跨 head 的
    調整基準不一致）。``dates``：需要的 ISO 日期集合（只載入這些日期的列，記憶體極小）。
    ``official_paths``：official_market_daily 快取 parquet；給定時只保留官方收盤價 > 0 的 (檔, 日)，與預設
    panel 路徑（build_prices 的 official inner join 與 raw_close > 0）同義；未給定時不過濾並記錄警告。"""
    import pyarrow.parquet as pq
    wanted = pd.Series(sorted({str(d)[:10].replace("-", "") for d in dates}))  # 快取內日期為 YYYYMMDD
    official = official_close_keys(official_paths, dates) if official_paths else None
    out = []
    for path in paths:
        path = Path(path)
        table = pq.read_table(path, columns=["date", "stock_id", "close"],
                              filters=[("date", "in", wanted.tolist())] if len(wanted) else None)
        df = table.to_pandas()
        del table
        df["stock_id"] = df["stock_id"].astype(str)
        df = df[df["stock_id"].str.fullmatch(r"[1-9]\d{3}")]
        df["close"] = pd.to_numeric(df["close"], errors="coerce")
        df = df.dropna(subset=["close"]).drop_duplicates(["stock_id", "date"], keep="last")
        lookup = dict(zip(zip(df["stock_id"], _iso_dates(df["date"])), df["close"].astype("float64")))
        dropped = 0
        if official is not None:
            before = len(lookup)
            lookup = {k: v for k, v in lookup.items() if k in official}
            dropped = before - len(lookup)
        label = f"price_cache:{path.name[:12]}"
        out.append((label, lookup))
        log(f"price cache {path.name[:16]}… rows for {len(wanted)} dates: {len(lookup):,}"
            + (f"（官方收盤過濾移除 {dropped:,}）" if official is not None else "（未以官方收盤過濾：無成交日會被結算成 0% 報酬）"))
    return out


def close_lookup_from_panel(px, dates):
    """由 pdata.build_prices 的面板（adj_close，已拼接兩個 head）建查表；只保留需要的日期。"""
    sub = px[px["date"].dt.strftime("%Y-%m-%d").isin(set(dates))]
    lookup = dict(zip(zip(sub["stock_id"].astype(str), sub["date"].dt.strftime("%Y-%m-%d")), sub["adj_close"].astype("float64")))
    return [("central_panel", lookup)]


# 預設只處理未分類（舊版寫入、settlement_status 為 NULL）與 pending 的列；void／已結算／legacy 列只有 --resettle 才重算。
SETTLE_TODO_SQL = f"settlement_status IS NULL OR settlement_status='{STATUS_PENDING}'"


def settle_rows(con, calendar, lookups, *, resettle=False, source_note=""):
    """逐列結算。每列以 rowid 更新（不再以 signal_date/stock_id/model_sha256 鍵更新，避免同訊號日多個 target
    互相覆蓋）。sessions_ahead 以已驗證日曆計算；target 不是 session 的列標 void 且 observed_return 清空
    （settlement_source=calendar_only）；1 個 session 的列 settled_h1，多個 session 的列 settled_multi_session
    （保留報酬但不計分）；價格未到的列維持 pending；價格來源缺兩端收盤但舊值存在的列標 legacy_unverified
    （保留舊值，不計分、不進校準池）。只有狀態／值／sessions_ahead 改變的列才寫入（observed_at 保持首次結算時間；
    --resettle 重跑時未變的列也不動）。回傳各狀態的列數、值被改動的列數與未變動（未寫入）的列數。"""
    cols = "rowid, signal_date, target_date, stock_id, market, observed_return, sessions_ahead, settlement_status, settlement_source"
    where = "" if resettle else f" WHERE {SETTLE_TODO_SQL}"
    rows = pd.read_sql(f"SELECT {cols} FROM predictions{where}", con)
    counts = {"evaluated": int(len(rows)), "changed_value": 0, "cleared_value": 0, "unverified_future": 0,
              "unchanged": 0}
    for status in (STATUS_SETTLED_H1, STATUS_SETTLED_MULTI, STATUS_LEGACY, STATUS_PENDING, *VOID_STATUSES):
        counts[status] = 0
    if rows.empty:
        return counts
    stamp = now_utc().isoformat()
    updates, status_only = [], []

    def same(row, n, status):
        prev_n = None if pd.isna(row.sessions_ahead) else int(row.sessions_ahead)
        return prev_n == n and row.settlement_status == status

    for row in rows.itertuples(index=False):
        n, status = sessions_between(calendar, row.signal_date, row.target_date, row.market)
        prev = None if pd.isna(row.observed_return) else float(row.observed_return)
        if status in VOID_STATUSES:
            counts[status] += 1
            if prev is not None:
                counts["cleared_value"] += 1
                updates.append((None, stamp, None, status, SOURCE_CALENDAR_ONLY, int(row.rowid)))
            elif same(row, None, status):
                counts["unchanged"] += 1
            else:
                status_only.append((None, status, SOURCE_CALENDAR_ONLY, int(row.rowid)))
            continue
        if status == "unverified_future":
            # 目標日晚於日曆範圍：不能判定是不是交易日，保留既有值、不計分，等日曆涵蓋後再算
            counts["unverified_future"] += 1
            counts[STATUS_PENDING] += 1
            if same(row, None, STATUS_PENDING):
                counts["unchanged"] += 1
            else:
                status_only.append((None, STATUS_PENDING, row.settlement_source if prev is not None else None, int(row.rowid)))
            continue
        value, label = None, None
        for lbl, lookup in lookups:
            c0 = lookup.get((row.stock_id, row.signal_date))
            c1 = lookup.get((row.stock_id, row.target_date))
            if c0 is not None and c1 is not None and c0 > 0:
                value, label = float(c1 / c0 - 1), lbl
                break
        if value is None:
            if prev is None:
                counts[STATUS_PENDING] += 1
                if same(row, n, STATUS_PENDING):
                    counts["unchanged"] += 1
                else:
                    status_only.append((n, STATUS_PENDING, None, int(row.rowid)))
            else:
                # 價格來源沒有這檔的兩端收盤：舊值可能來自舊鍵覆蓋 bug，保留但不計分（summary.excluded 會列出）
                counts[STATUS_LEGACY] += 1
                if same(row, n, STATUS_LEGACY):
                    counts["unchanged"] += 1
                else:
                    status_only.append((n, STATUS_LEGACY, SOURCE_LEGACY, int(row.rowid)))
            continue
        if source_note:
            label = source_note if label == source_note or source_note.startswith(label + ":") else f"{label};{source_note}"
        new_status = STATUS_SETTLED_H1 if n == 1 else STATUS_SETTLED_MULTI
        counts[new_status] += 1
        if prev is None or abs(prev - value) > 1e-12:
            counts["changed_value"] += 1
            updates.append((value, stamp, n, new_status, label, int(row.rowid)))
        elif same(row, n, new_status) and row.settlement_source == label:
            counts["unchanged"] += 1
        else:  # 值相同、只有狀態／sessions_ahead／來源標記不同：不改 observed_at
            status_only.append((n, new_status, label, int(row.rowid)))
    con.executemany("UPDATE predictions SET observed_return=?, observed_at=?, sessions_ahead=?, settlement_status=?, "
                    "settlement_source=? WHERE rowid=?", updates)
    con.executemany("UPDATE predictions SET sessions_ahead=?, settlement_status=?, settlement_source=? WHERE rowid=?", status_only)
    con.commit()
    counts["written"] = len(updates) + len(status_only)
    return counts


def cmd_settle(args):
    root = root_dir(args.root)
    con = ledger(root)
    where = "" if args.resettle else f" WHERE {SETTLE_TODO_SQL}"
    todo = pd.read_sql(f"SELECT signal_date, target_date FROM predictions{where}", con)
    counts = None
    calendar = None
    skipped = None
    if not todo.empty:
        dates = _needed_dates(todo)
        try:
            if args.price_cache:
                calendar = load_verified_calendar(args.calendar)
                lookups = close_lookup_from_cache(args.price_cache, dates, args.official_cache)
                note = "price_cache_explicit"
            elif args.price_source == "cache":
                head_sha, routes, _ = pdata.read_head(args.head)
                calendar = load_verified_calendar(args.calendar, routes)
                caches = []
                for route, cols in (("finmind_price_adj", pdata.PRICE_COLS), ("official_market_daily", pdata.OFFICIAL_COLS)):
                    entry = pdata._route(routes, route, head_sha)
                    sig = hashlib.sha256(json.dumps(cols).encode()).hexdigest()[:10]
                    cache = root / "cache" / "objects" / f"{entry['sha256']}_{sig}.parquet"
                    if not cache.exists():
                        pdata.load_object(entry, cols, root / "cache")  # builds the parquet cache (same as predict)
                    caches.append(cache)
                lookups = close_lookup_from_cache([caches[0]], dates, [caches[1]])  # 官方收盤過濾＝panel 路徑語意
                note = f"price_cache_head:{head_sha[:12]}"
            else:
                head_sha, routes, _ = pdata.read_head(args.head)
                calendar = load_verified_calendar(args.calendar, routes)
                hist_sha, hist_routes, _ = pdata.read_head(pdata.HISTORIC_HEAD)
                px = pdata.build_prices(routes, hist_routes, root / "cache", head_sha, hist_sha)
                lookups = close_lookup_from_panel(px, dates)
                del px
                note = f"central_panel:{head_sha[:12]}"
        except (OSError, ValueError, PreopenCalendarError) as exc:
            # 日曆不可用（sidecar 被汰除且目錄無可驗證檔、proof 損毀…）：不結算、只重寫摘要；predict 的守門另行判定
            skipped = f"{type(exc).__name__}: {exc}"
            log(f"settle 略過（已驗證日曆不可用，{len(todo):,} 列維持原狀）：{skipped}")
        if skipped is None:
            log(f"calendar {calendar['source']} verified_asof={calendar['verified_asof']} last_session={calendar['last_session']} "
                f"sha={str(calendar['sha256'])[:12]}")
            counts = settle_rows(con, calendar, lookups, resettle=args.resettle, source_note=note)
            del lookups
    summary = forward_summary(con, root=root, calendar=calendar)
    con.close()
    if skipped:
        summary["settle_skipped"] = skipped
    write_json_atomic(root / "state" / "summary.json", summary)
    write_daily_csv(root / "state" / "forward_daily.csv", summary)
    log(f"settled {json.dumps(counts, ensure_ascii=False) if counts else 'nothing pending'}; "
        f"summary {json.dumps(summary.get('all', {}), ensure_ascii=False)}")


# ---------------------------------------------------------------- forward summary (v2)
def _auc(up, p):
    """Mann-Whitney AUC（平均秩處理同分）；單一類別時 None。"""
    y = np.asarray(up, dtype=int)
    npos, nneg = int(y.sum()), int(len(y) - y.sum())
    if npos == 0 or nneg == 0:
        return None
    r = pd.Series(np.asarray(p, dtype="float64")).rank(method="average").to_numpy()
    return float((r[y == 1].sum() - npos * (npos + 1) / 2) / (npos * nneg))


def _f(x):
    return None if x is None or (isinstance(x, float) and np.isnan(x)) else float(x)


def _block(d):
    """一組已結算列的整體指標（既有鍵名保留；新增 up_rate、mean_p_up、prob_bias、auc、top10_mean_ret、days_insufficient）。"""
    if d.empty:
        return {"settled_rows": 0, "settled_days": 0, "days_insufficient": True}
    up = (d["observed_return"] > 0).astype(int)
    call = (d["p_up"] >= 0.5).astype(int)
    top = d[d["rank_tradable"].notna() & (d["rank_tradable"] <= 10)]
    gated = top[top["gate_us_up"] == 1]
    days = int(d["signal_date"].nunique())
    return {
        "settled_rows": int(len(d)), "settled_days": days,
        "first_signal": str(d["signal_date"].min()), "last_signal": str(d["signal_date"].max()),
        "direction_accuracy": float((call == up).mean()), "always_down_baseline": float(1 - up.mean()),
        "up_rate": float(up.mean()), "mean_p_up": float(d["p_up"].mean()), "prob_bias": float(d["p_up"].mean() - up.mean()),
        "auc": _auc(up, d["p_up"]),
        "top10_hit": float((top["observed_return"] > 0).mean()) if len(top) else None,
        "top10_days": int(top["signal_date"].nunique()),
        "top10_mean_ret": float(top["observed_return"].mean()) if len(top) else None,
        "gated_top10_hit": float((gated["observed_return"] > 0).mean()) if len(gated) else None,
        "gated_top10_days": int(gated["signal_date"].nunique()),
        "mae": float(np.mean(np.abs(d["exp_ret"] - d["observed_return"]))),
        "mae_zero": float(np.mean(np.abs(d["observed_return"]))),
        "days_insufficient": days < MIN_SUMMARY_DAYS,
    }


def _manifest_degraded(root, signal_date, run_id):
    if root is None:
        return None
    p = Path(root) / "predictions" / str(signal_date) / f"{run_id}.json"
    if not p.exists():
        return None
    try:
        return bool(json.loads(p.read_text(encoding="utf-8")).get("degraded"))
    except (OSError, ValueError):
        return None


def _publication_audit(rows):
    """Counts for each published run, including rows excluded from scoring; built from the same ledger read."""
    d = rows.loc[rows["prospective"].eq(1), ["signal_date", "run_id", "rank_tradable", "settlement_status", "_scorable"]].copy()
    if d.empty:
        return {}
    d["_pending"] = d["settlement_status"].eq(STATUS_PENDING)
    d["_unclassified"] = d["settlement_status"].isna()
    d["_top10"] = d["rank_tradable"].notna() & d["rank_tradable"].between(1, 10)
    d["_top10_pending"] = d["_top10"] & d["_pending"]
    d["_top10_missing"] = d["_top10"] & d["_scorable"].eq(0)
    audit = d.groupby(["signal_date", "run_id"]).agg(
        published_rows=("_scorable", "size"), scorable_rows=("_scorable", "sum"),
        pending_rows=("_pending", "sum"), unclassified_rows=("_unclassified", "sum"),
        top10_published_n=("_top10", "sum"), top10_pending_n=("_top10_pending", "sum"),
        top10_missing_n=("_top10_missing", "sum"))
    audit["excluded_rows"] = audit["published_rows"] - audit["scorable_rows"]
    return {key: {name: int(value) for name, value in counts.items()} for key, counts in audit.to_dict("index").items()}


def _daily(d, root=None, first_seen=None, publication_audit=None, manifest_cache=None):
    """逐日表：n、acc、always_down、up_rate、mean_p_up、bias、auc、top10_hit、top10_mean_ret、gate、us_status、
    model_sha12、run_id、degraded（manifest；缺檔時以 us_status=missing 代替）、first_seen_*（消費端實際最先看到的
    run，不論其結算狀態）。"""
    rows = []
    first_seen = first_seen or {}
    publication_audit = publication_audit or {}
    manifest_cache = {} if manifest_cache is None else manifest_cache
    for sig, x in d.groupby("signal_date", sort=True):
        up = (x["observed_return"] > 0).astype(int)
        call = (x["p_up"] >= 0.5).astype(int)
        top = x[x["rank_tradable"].notna() & (x["rank_tradable"] <= 10)]
        run_id = x["run_id"].iloc[0]
        gate = x["gate_us_up"].dropna()
        manifest_key = (str(sig), str(run_id))
        if manifest_key not in manifest_cache:
            manifest_cache[manifest_key] = _manifest_degraded(root, sig, run_id)
        degraded = manifest_cache[manifest_key]
        rows.append({
            "signal_date": str(sig), "target_date": str(x["target_date"].mode().iloc[0]), "n": int(len(x)),
            "acc": float((call == up).mean()), "always_down": float(1 - up.mean()), "up_rate": float(up.mean()),
            "mean_p_up": float(x["p_up"].mean()), "bias": float(x["p_up"].mean() - up.mean()),
            "auc": _auc(up, x["p_up"]) if len(x) > 20 else None,
            "top10_n": int(len(top)), "top10_hits": int((top["observed_return"] > 0).sum()),
            "top10_hit": float((top["observed_return"] > 0).mean()) if len(top) else None,
            "top10_mean_ret": float(top["observed_return"].mean()) if len(top) else None,
            "gate": None if gate.empty else bool(gate.iloc[0] == 1),
            "us_status": str(x["us_status"].mode().iloc[0]) if x["us_status"].notna().any() else None,
            "model_sha12": str(x["model_sha256"].mode().iloc[0])[:12], "run_id": str(run_id),
            "runs": int(x["run_id"].nunique()), "recorded_at": str(x["recorded_at"].max()),
            "degraded": bool(x["us_status"].eq("missing").any()) if degraded is None else bool(degraded),
            **publication_audit.get((sig, run_id), {}),
            **{k: (first_seen.get(str(sig)) or {}).get(k) for k in ("first_seen_run_id", "first_seen_target_date",
                                                                    "first_seen_status", "first_seen_recorded_at")},
        })
    return rows


def _bootstrap_days(daily, seed=BOOTSTRAP_SEED, reps=BOOTSTRAP_REPS, level=BOOTSTRAP_LEVEL):
    """以日為抽樣單位的 bootstrap（固定 seed）：整體 acc（列數加權）與 top10_hit（Top10 列數加權）的區間。"""
    if not daily:
        return {"days": 0, "seed": seed, "reps": reps, "level": level, "acc": None, "top10_hit": None}
    n = np.array([r["n"] for r in daily], dtype="float64")
    hits = np.array([r["acc"] * r["n"] for r in daily], dtype="float64")
    tn = np.array([r["top10_n"] for r in daily], dtype="float64")
    th = np.array([r["top10_hits"] for r in daily], dtype="float64")
    rng = np.random.default_rng(seed)
    idx = rng.integers(0, len(daily), size=(reps, len(daily)))
    acc = hits[idx].sum(axis=1) / n[idx].sum(axis=1)
    tsum = tn[idx].sum(axis=1)
    with np.errstate(invalid="ignore", divide="ignore"):
        top = np.where(tsum > 0, th[idx].sum(axis=1) / tsum, np.nan)
    lo, hi = (1 - level) / 2 * 100, (1 + level) / 2 * 100
    top_ok = top[~np.isnan(top)]
    return {"days": int(len(daily)), "seed": int(seed), "reps": int(reps), "level": level,
            "acc": [float(np.percentile(acc, lo)), float(np.percentile(acc, hi))],
            "top10_hit": [float(np.percentile(top_ok, lo)), float(np.percentile(top_ok, hi))] if len(top_ok) else None,
            "days_insufficient": len(daily) < MIN_SUMMARY_DAYS}


def _model_labels(root):
    labels = {}
    if root is None:
        return labels
    for f in (Path(root) / "models" / "artifacts").glob("*.json"):
        try:
            m = json.loads(f.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            continue
        if m.get("sha256"):
            labels[m["sha256"]] = (m.get("recipe") or {}).get("name") or "unknown"
    return labels


def _view(d, root, first_seen=None, *, labels=None, publication_audit=None, manifest_cache=None):
    daily = _daily(d, root, first_seen, publication_audit, manifest_cache)
    labels = _model_labels(root) if labels is None else labels
    by_model = {}
    for sha, x in d.groupby("model_sha256"):
        by_model[sha[:12]] = {"recipe": labels.get(sha, "unknown"), **_block(x)}
    by_us = {str(k): _block(x) for k, x in d.groupby(d["us_status"].fillna("unknown"))}
    return {"selection_unit": "run", "overall": _block(d), "daily": daily, "by_model": by_model,
            "by_us_status": by_us, "bootstrap": _bootstrap_days(daily)}


VIEW_LAST = "last_publication"
VIEW_FIRST_SCORABLE = "first_scorable_publication"


def forward_summary(con, root=None, calendar=None):
    """state/summary.json（schema …-forward-summary-v2）。

    只計 settled_h1（sessions_ahead=1、由目前價格來源結算）、prospective=1 的列。``all`` 區塊保留 v1 的鍵（消費端相容；
    口徑＝最後可計分發布），``views.last_publication`` 與 ``views.first_scorable_publication``（每 signal_date
    依 recorded_at、run_id 選最後／最早有可計分列的單一 run，再取該 run 的有效原 rank；不跨 run 拼接。
    後者不等於消費端最先看到的發布，逐日表的 first_seen_* 才是）各含 overall、
    daily（逐日表）、by_model、by_us_status、bootstrap（以日為單位、固定 seed、90% 區間）；``excluded`` 列出 void／
    多 session／legacy／pending／未分類列數。樣本日數 < MIN_SUMMARY_DAYS 時 days_insufficient=true。
    """
    out = {"schema": SUMMARY_SCHEMA, "generated_at": now_utc().isoformat(),
           "scoring": {"rows": SCORABLE_SQL, "selection_unit": "run", "run_order": ["recorded_at", "run_id"],
                       "all_block_publication": VIEW_LAST, "views": [VIEW_LAST, VIEW_FIRST_SCORABLE], "min_days": MIN_SUMMARY_DAYS,
                       "calendar": None if not calendar else {k: calendar.get(k) for k in ("path", "sha256", "verified_asof", "last_session", "source")}}}
    rows = pd.read_sql(f"SELECT *, CASE WHEN {SCORABLE_SQL} THEN 1 ELSE 0 END AS _scorable FROM predictions", con)
    status_counts = rows["settlement_status"].fillna("unclassified").value_counts()
    out["excluded"] = {str(status): int(n) for status, n in status_counts.items() if status != STATUS_SETTLED_H1}
    out["excluded"]["not_prospective"] = int(rows["prospective"].eq(0).sum())
    scorable = rows.loc[rows["_scorable"].eq(1)]
    first_seen = first_seen_runs(con, rows)
    audit, labels, manifest_cache = _publication_audit(rows), _model_labels(root), {}
    last, first = latest_prospective(scorable), first_prospective(scorable)
    out["views"] = {}
    for name, data, label in ((VIEW_LAST, last, "最後可計分發布"), (VIEW_FIRST_SCORABLE, first, "最早可計分發布")):
        out["views"][name] = {"label_zh": label, **_view(data, root, first_seen, labels=labels,
                                                        publication_audit=audit, manifest_cache=manifest_cache)}
    out["all"] = {**out["views"][VIEW_LAST]["overall"], "publication_rule": VIEW_LAST, "sessions_ahead": 1,
                  "selection_unit": "run"}
    return _jsonable(out)


def _jsonable(value):
    """numpy 純量 → Python 純量（避免 json.dumps(default=str) 把 numpy bool 寫成字串）。"""
    if isinstance(value, dict):
        return {k: _jsonable(v) for k, v in value.items()}
    if isinstance(value, (list, tuple)):
        return [_jsonable(v) for v in value]
    if isinstance(value, np.bool_):
        return bool(value)
    if isinstance(value, np.integer):
        return int(value)
    if isinstance(value, np.floating):
        return None if np.isnan(value) else float(value)
    return value


def write_daily_csv(path, summary):
    rows = []
    for view, body in (summary.get("views") or {}).items():
        for r in body.get("daily") or []:
            rows.append({"publication": view, **r})
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(path.name + ".tmp-" + uuid.uuid4().hex)
    pd.DataFrame(rows).to_csv(tmp, index=False, encoding="utf-8")
    os.replace(tmp, path)  # 原子寫入：CSV 被其他程式鎖住時不留下半截檔


def cmd_status(args):
    root = root_dir(args.root)
    for name in ("models/current.json", "predictions/latest.json", "state/summary.json"):
        p = root / name
        print(f"== {name}")
        print(p.read_text(encoding="utf-8")[:2500] if p.exists() else "(missing)")


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("command", choices=["train", "calibrate", "predict", "settle", "status"])
    ap.add_argument("--root", default=None)
    ap.add_argument("--head", default=None, help="pin a central head sha (default: current)")
    ap.add_argument("--signal-date", default=None, help="train: first signal date the model will serve")
    ap.add_argument("--target-date", default=None, help="predict: next TW session (YYYY-MM-DD)")
    ap.add_argument("--allow-missing-us", action="store_true",
                    help="相容舊命令：美股缺漏已預設以缺值推論並標示 degraded；此旗標不改變發布行為")
    ap.add_argument("--require-fresh-chips", action="store_true",
                    help="相容舊命令：此旗標不再阻擋發布；t 日籌碼未更新時保留各路由日期，"
                         "並標示 chips_freshness=stale 與 degraded=true")
    ap.add_argument("--no-latest", action="store_true", help="predict: do not move predictions/latest.json")
    ap.add_argument("--force", action="store_true")
    ap.add_argument("--recipe", default=None, choices=sorted(RECIPES),
                    help="train: 指定配方（與現役不同時必須同時給 --force 與 --authorization）；預設沿用現役模型的配方")
    ap.add_argument("--authorization", default="")
    ap.add_argument("--calendar", default=None,
                    help="predict/settle: 已驗證日曆 v2 proof 檔路徑（預設：中央 head 的 official_calendar sidecar，"
                         "或 _calendar\\verified 中最新者）")
    ap.add_argument("--allow-stale-signal", action="store_true",
                    help="相容旗標，無作用：v3（使用者 2026-11-01 裁示）起日曆守門一律只註記不阻擋，predict 照常發布並在 manifest "
                         "標示 degraded；本旗標也不影響 --strict-calendar")
    ap.add_argument("--strict-calendar", action="store_true",
                    help="predict（手動 QA 用，預設關閉；每日排程不得使用）：日曆守門 status 不為 ok（中央價格落後、缺口無法解釋、"
                         "目標日為週末／假日／已驗證非交易日、日曆不可用…）時拒絕發布：exit 非 0、寫 predictions\\latest_refusal.json、"
                         "latest.json 不更新")
    ap.add_argument("--closed-day", nargs="+", default=None, metavar="YYYY-MM-DD",
                    help="predict: 宣告已知的臨時休市日（颱風假等），使訊號日與目標日之間的該平日不被視為中央落後（gate 變 ok）；"
                         "長期已公告的休市日請放在 <root>\\config\\announced_closures.json。格式 YYYY-MM-DD；無法解析的值會被忽略、"
                         "以 calendar_gate 的 closed_day_cli_invalid 註記並照常發布（不中止）")
    ap.add_argument("--resettle", action="store_true", help="settle: 重算帳冊全部列（含已結算列）")
    ap.add_argument("--price-source", default="panel", choices=["panel", "cache"],
                    help="settle: panel＝如舊，以中央兩個 head 拼接價格面板（預設）；cache＝直讀目前 head 的 finmind 價格快取 parquet，"
                         "並以同 head 的 official_market_daily 快取過濾官方收盤價 > 0 的 (檔, 日)（記憶體極小）")
    ap.add_argument("--price-cache", nargs="+", default=None,
                    help="settle: 直接指定 finmind_price_adj 快取 parquet（可多個；兩端收盤只取同一檔），不讀中央 head")
    ap.add_argument("--official-cache", nargs="+", default=None,
                    help="settle（與 --price-cache 併用）：official_market_daily 快取 parquet，只結算官方收盤價 > 0 的 (檔, 日)；"
                         "未給定時無成交日會被結算成 0%% 報酬")
    args = ap.parse_args(argv)
    {"train": cmd_train, "calibrate": cmd_calibrate, "predict": cmd_predict, "settle": cmd_settle,
     "status": cmd_status}[args.command](args)


if __name__ == "__main__":
    main()
