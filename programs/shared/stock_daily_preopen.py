"""Producer for the pre-open H1 model (stock_daily_preopen), a model family parallel to C0.

Commands (run with D:\\stock\\.venv\\Scripts\\python.exe -B):
  train     refit the ACTIVE model's frozen recipe (v1 h1pre_final_nogc_xs: 504 sessions, 120-date calibration;
            v2 h1pre_v2_ens: hpC HGB ensemble, 250-date calibration); --recipe NAME --force --authorization TEXT
            switches recipes explicitly (routine monthly refits never switch)
  calibrate refit the Platt / LAD-shrink calibrator of the active recipe on its window of matured OOS dates
  predict   build features from the pinned central head and write the snapshot for the next session
  settle    fill observed returns in the forward ledger and refresh state/summary.json
  status    print the active model, latest snapshot and forward summary

Data root (override with STOCK_DAILY_PREOPEN_ROOT): D:\\TWStockData\\program_data\\stock_daily_preopen
  models\\current.json, models\\artifacts\\<sha>.joblib(+.json), models\\activations\\*.json
  predictions\\<signal_date>\\<run_id>.parquet(+.json), predictions\\latest.json
  state\\forward.sqlite3, state\\summary.json, cache\\
The recipe, validation and limits are documented in
D:\\TWStockData\\program_data\\stock_daily_c0\\candidates\\calibration_20261018\\report\\校正報告_20261018.md
"""
from __future__ import annotations

import argparse
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
RESEARCH_DIR = Path(r"D:\TWStockData\program_data\stock_daily_c0\candidates\calibration_20261018")
RESEARCH_OOS = RESEARCH_DIR / "results" / "holdout" / "h1pre_final_nogc_xs" / "preds.parquet"

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
  PRIMARY KEY (signal_date, stock_id, model_sha256, run_id));
CREATE TABLE IF NOT EXISTS calibration_pool (
  signal_date TEXT NOT NULL, stock_id TEXT NOT NULL, p_raw REAL, r_raw REAL, y REAL, source TEXT NOT NULL,
  PRIMARY KEY (signal_date, stock_id, source));
CREATE TABLE IF NOT EXISTS meta (key TEXT PRIMARY KEY, value TEXT);
"""


def ledger(root):
    path = root / "state" / "forward.sqlite3"
    path.parent.mkdir(parents=True, exist_ok=True)
    con = sqlite3.connect(path)
    con.executescript(LEDGER_SQL)
    return con


def latest_prospective(d):
    """Keep, per (signal_date, stock_id), the last publication recorded before the target session opened."""
    if d.empty:
        return d
    return d.sort_values("recorded_at").drop_duplicates(["signal_date", "stock_id"], keep="last")


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


def calibration_pool(con, recipe=None, model_shas=None):
    """Seeded OOS rows of ``recipe`` plus settled live rows of models trained with the same recipe."""
    recipe = recipe or RECIPE
    seeded = pd.read_sql("SELECT signal_date, stock_id, p_raw, r_raw, y FROM calibration_pool WHERE source=?", con,
                         params=(_seed_source(recipe),))
    live = pd.read_sql("SELECT * FROM predictions WHERE observed_return IS NOT NULL AND prospective=1", con)
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


def resolve_target(signal_date, explicit=None):
    if explicit:
        return dt.date.fromisoformat(explicit), "explicit"
    today = now_utc().astimezone(TAIPEI).date()
    if today > signal_date and today.weekday() < 5:
        return today, "runtime_today_taipei"
    return pdata.next_weekday(signal_date), "next_weekday_assumed"


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


def cmd_predict(args):
    root = root_dir(args.root)
    model, cur, meta = load_active(root)
    head_sha, routes, _ = pdata.read_head(args.head)
    px_max = pd.Timestamp(str(routes["finmind_price_adj"].get("actual_max_date") or "1900-01-01")).date()
    signal_guess = px_max
    target, target_src = resolve_target(signal_guess, args.target_date)
    panel, dates, inputs = build_panel(root, head_sha=head_sha, next_session=pd.Timestamp(target))
    signal = dates[-1].date()
    if signal != signal_guess:
        target, target_src = resolve_target(signal, args.target_date)
        panel, dates, inputs = build_panel(root, head_sha=head_sha, next_session=pd.Timestamp(target))
    if target <= signal:
        raise SystemExit(f"target {target} must be after signal {signal}")
    us = pdata.us_overnight_status(inputs["g"], signal, target)
    rows = panel[(panel["di"] == len(dates) - 1) & (panel["hist_len"] >= RECIPE["min_hist"])].copy()
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
    gate = None if us["status"] == "missing" or pd.isna(on_sp) else bool(on_sp > 0)
    recipe = recipe_of(meta)
    p_raw, r_raw = predict_raw(model, rows, recipe) if "sources" in recipe else predict_raw(model, rows)
    p, r = apply_calibrator(cur["calibrator"], p_raw, r_raw)
    tcfg = RECIPE["tradable"]
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
        "gate_us_up": gate, "central_head_sha256": inputs["provenance"]["central_head_sha256"],
        "data_provenance": inputs["provenance"], "chips_freshness": fresh,
        "degraded": us["status"] == "missing" or fresh["status"] == "stale",
    }
    write_new(out.with_suffix(".json"), json.dumps(manifest, ensure_ascii=False, indent=2, default=str).encode("utf-8"))
    target_open = dt.datetime.combine(target, dt.time(9, 0), TAIPEI)
    prospective = int(created < target_open)
    if args.no_latest:  # test / QA run: neither published nor recorded in the forward ledger
        log(f"snapshot {rel} written without publication (--no-latest); ledger untouched; "
            f"rows={len(snap)} tradable={int(snap['tradable'].sum())} us={us['status']} chips={fresh['status']} gate={gate}")
        return
    con = ledger(root)
    recs = [(signal.isoformat(), target.isoformat(), s, m, float(pu), float(pr_), float(eu), float(er), int(rk),
             None if pd.isna(rt) else int(rt), int(tb), None if gate is None else int(gate), us["status"],
             cur["model_sha256"], run_id, created.isoformat(), prospective, float(ac), None, None)
            for s, m, pu, pr_, eu, er, rk, rt, tb, ac in snap[["stock_id", "market", "p_up", "p_up_raw", "exp_ret",
                                                              "exp_ret_raw", "rank", "rank_tradable", "tradable", "adj_close"]].itertuples(index=False)]
    con.executemany("INSERT OR IGNORE INTO predictions VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)", recs)
    con.commit()
    con.close()
    write_json_atomic(root / "predictions" / "latest.json", manifest)
    top = snap[snap["tradable"]].sort_values("rank_tradable").head(10)
    log(f"snapshot {rel} rows={len(snap)} tradable={int(snap['tradable'].sum())} us={us['status']} chips={fresh['status']} gate={gate} "
        f"prospective={prospective}")
    log("top10: " + ", ".join(f"{s}({p:.3f})" for s, p in zip(top["stock_id"], top["p_up"])))


def cmd_settle(args):
    root = root_dir(args.root)
    con = ledger(root)
    pending = pd.read_sql("SELECT DISTINCT signal_date, target_date, stock_id, model_sha256 FROM predictions "
                          "WHERE observed_return IS NULL", con)
    settled = 0
    if not pending.empty:
        head_sha, routes, _ = pdata.read_head(args.head)
        hist_sha, hist_routes, _ = pdata.read_head(pdata.HISTORIC_HEAD)
        px = pdata.build_prices(routes, hist_routes, root / "cache", head_sha, hist_sha)
        px["date"] = px["date"].dt.strftime("%Y-%m-%d")
        close = px.set_index(["stock_id", "date"])["adj_close"]
        stamp = now_utc().isoformat()
        for row in pending.itertuples(index=False):
            c0 = close.get((row.stock_id, row.signal_date))
            c1 = close.get((row.stock_id, row.target_date))
            if c0 is None or c1 is None or not (c0 > 0):
                continue
            # same-head adjusted closes for both ends (corporate-action safe)
            con.execute("UPDATE predictions SET observed_return=?, observed_at=? WHERE signal_date=? AND stock_id=? AND model_sha256=?",
                        (float(c1 / c0 - 1), stamp, row.signal_date, row.stock_id, row.model_sha256))
            settled += 1
        con.commit()
    summary = forward_summary(con)
    con.close()
    write_json_atomic(root / "state" / "summary.json", summary)
    log(f"settled {settled} rows; summary {json.dumps(summary.get('all', {}), ensure_ascii=False)}")


def forward_summary(con):
    d = latest_prospective(pd.read_sql("SELECT * FROM predictions WHERE observed_return IS NOT NULL AND prospective=1", con))
    out = {"schema": "tw-stock-preopen-forward-summary-v1", "generated_at": now_utc().isoformat()}
    if d.empty:
        out["all"] = {"settled_rows": 0, "settled_days": 0}
        return out
    up = (d["observed_return"] > 0).astype(int)
    call = (d["p_up"] >= 0.5).astype(int)
    top = d[d["rank_tradable"].notna() & (d["rank_tradable"] <= 10)]
    gated = top[top["gate_us_up"] == 1]
    out["all"] = {
        "settled_rows": int(len(d)), "settled_days": int(d["signal_date"].nunique()),
        "first_signal": d["signal_date"].min(), "last_signal": d["signal_date"].max(),
        "direction_accuracy": float((call == up).mean()), "always_down_baseline": float(1 - up.mean()),
        "top10_hit": float((top["observed_return"] > 0).mean()) if len(top) else None,
        "top10_days": int(top["signal_date"].nunique()),
        "gated_top10_hit": float((gated["observed_return"] > 0).mean()) if len(gated) else None,
        "gated_top10_days": int(gated["signal_date"].nunique()),
        "mae": float(np.mean(np.abs(d["exp_ret"] - d["observed_return"]))),
        "mae_zero": float(np.mean(np.abs(d["observed_return"]))),
    }
    return out


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
    args = ap.parse_args(argv)
    {"train": cmd_train, "calibrate": cmd_calibrate, "predict": cmd_predict, "settle": cmd_settle,
     "status": cmd_status}[args.command](args)


if __name__ == "__main__":
    main()
