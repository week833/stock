"""Offline tests for the pre-open H1 model family (stock_daily_preopen*)."""
from __future__ import annotations

import datetime as dt
import hashlib
import json
import os
import re
import sys
from pathlib import Path

import numpy as np
import pandas as pd
import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import stock_daily_preopen as producer  # noqa: E402
import stock_daily_preopen_data as pdata  # noqa: E402
from stock_daily_preopen_fixture import write_fixture  # noqa: E402
import stock_daily_preopen_reader as reader  # noqa: E402
from stock_daily_preopen_reader import (  # noqa: E402
    CHIP_ROUTES, CHIPS_PARTIAL_PREFIX_ZH, CHIPS_PARTIAL_SUFFIX_ZH, CHIPS_STALE_PREFIX_ZH, CHIPS_STALE_SUFFIX_ZH,
    CHIPS_STALE_TEXT_ZH, LATEST_SCHEMA, MODEL_LABEL, REASON_TEXT_UNKNOWN_ZH, REASON_TEXT_ZH, PreopenUnavailable,
    chips_freshness_text, describe, load_latest, reason_text,
)

RESEARCH_CACHE = Path(r"D:\TWStockData\program_data\stock_daily_c0\candidates\calibration_20261018\cache")

# 讀取器定義的全部原因代碼；面向使用者的文字必須對每一個都有繁體中文說明。
REASON_CODES = frozenset({
    "preopen_no_latest", "preopen_schema_mismatch", "preopen_stale", "preopen_snapshot_missing",
    "preopen_snapshot_sha_mismatch", "preopen_snapshot_columns", "preopen_snapshot_values",
    "preopen_us_status_invalid", "preopen_parquet_engine_missing",
})

# 常見簡體字（只收簡體獨有、繁體不使用的字形），以 \\u 轉義寫入避免本檔本身含簡體字。
SIMPLIFIED_CHARS = frozenset(
    "\u4e1c\u4e2a\u4e3a\u4e49\u4e60\u4e66\u4e70\u4ea7\u4ece\u4ef7\u4f17\u4f18\u4f1a\u4f20\u4f53\u4fa7\u503a\u5173\u517b\u5199\u519b\u51b3\u51cf\u51fb"
    "\u5219\u521b\u52a1\u52a8\u52bf\u533a\u533b\u534f\u5355\u5356\u5386\u538b\u53c2\u53cc\u53d1\u53d8\u542f\u5458\u54cd\u56e2\u56ed\u56f4\u56fd\u56fe"
    "\u5706\u573a\u574f\u5757\u575a\u5934\u5939\u593a\u5956\u5987\u5988\u5b59\u5b66\u5b9d\u5b9e\u5ba1\u5bbd\u5bf9\u5bfb\u5bfc\u5bff\u5c14\u5c18\u5c3d"
    "\u5c42\u5c5e\u5c81\u5c9b\u5e01\u5e08\u5e10\u5e26\u5e2e\u5e86\u5e94\u5e9f\u5f00\u5f20\u5f2f\u5f39\u5f52\u5f53\u5f55\u5fc6\u5fe7\u6000\u6001\u603b"
    "\u6076\u60ca\u60ef\u620f\u6218\u6267\u6269\u626b\u626c\u629a\u62a4\u62a5\u62df\u62e5\u62e9\u6323\u635f\u6362\u6444\u6446\u654c\u6570\u65ad\u65e0"
    "\u65e7\u65f6\u65f7\u663e\u6653\u6682\u672f\u6740\u6742\u6743\u6761\u6765\u6768\u6781\u6784\u6807\u680f\u6811\u6837\u6863\u68c0\u697c\u6b22\u6b8b"
    "\u6bd5\u6c14\u6c47\u6c49\u6c64\u6c9f\u6d45\u6d4b\u6d4e\u6d4f\u6d53\u6da8\u6e10\u6e7e\u6e7f\u6eda\u6ee1\u6ee4\u706d\u706f\u7075\u707e\u70b9\u70ed"
    "\u7231\u72b6\u72ec\u732e\u73af\u73b0\u7535\u7545\u7597\u76d1\u76d6\u76d8\u7801\u7840\u786e\u79bb\u79cd\u79ef\u79f0\u7a33\u7a77\u7ad6\u7ade\u7b3c"
    "\u7b51\u7b5b\u7b7e\u7b80\u7c7b\u7d27\u7ea2\u7ea6\u7ea7\u7eaa\u7eaf\u7eb2\u7eb3\u7eb5\u7eb7\u7eb8\u7ebf\u7ec3\u7ec4\u7ec6\u7ec7\u7ec8\u7ecd\u7ecf"
    "\u7ed3\u7ed5\u7ed9\u7edc\u7edd\u7edf\u7ee7\u7ee9\u7eed\u7ef4\u7ef5\u7efc\u7eff\u7f13\u7f16\u7f18\u7f29\u7f34\u7f51\u7f5a\u7f62\u83b7\u89c1\u89c2"
    "\u89c4\u89c6\u89c8\u89c9\u89e6\u8a89\u8ba1\u8ba2\u8ba4\u8ba8\u8ba9\u8bad\u8bae\u8baf\u8bb0\u8bb2\u8bb8\u8bba\u8bbe\u8bbf\u8bc1\u8bc4\u8bc6\u8bc9"
    "\u8bcd\u8bd1\u8bd5\u8bd7\u8bdd\u8be2\u8be5\u8be6\u8bed\u8bef\u8bf4\u8bf7\u8bf8\u8bfb\u8bfe\u8c01\u8c03\u8c08\u8c0a\u8c0b\u8c13\u8c22\u8c26\u8c28"
    "\u8c31\u8d1d\u8d1f\u8d21\u8d22\u8d23\u8d25\u8d26\u8d27\u8d28\u8d2a\u8d2b\u8d2c\u8d2d\u8d2e\u8d2f\u8d34\u8d35\u8d37\u8d38\u8d39\u8d44\u8d4b\u8d4c"
    "\u8d4f\u8d54\u8d56\u8d5a\u8d5b\u8d5e\u8d60\u8d62\u8d76\u8d8b\u8dc3\u8df5\u8e2a\u8f66\u8f68\u8f6c\u8f6e\u8f6f\u8f74\u8f7b\u8f7d\u8f83\u8f85\u8f86"
    "\u8f93\u8f96\u8f9e\u8fa9\u8fb9\u8fbe\u8fc1\u8fc7\u8fc8\u8fd0\u8fd8\u8fd9\u8fdb\u8fdc\u8fdd\u8fde\u8fdf\u9012\u903b\u9057\u9065\u90ae\u90bb\u90d1"
    "\u91ca\u9274\u9488\u94a2\u94a5\u94b1\u94bb\u94c1\u94dc\u94f6\u94f8\u94fe\u9500\u9501\u9505\u950b\u9519\u9521\u952e\u952f\u9547\u955c\u957f\u95e8"
    "\u95ea\u95ed\u95ee\u95f2\u95f4\u95f7\u95f8\u95f9\u95fb\u9600\u9605\u9614\u961f\u9633\u9634\u9635\u9636\u9645\u9646\u9648\u9669\u968f\u9690\u96be"
    "\u96fe\u97e9\u9875\u9876\u9879\u987a\u987b\u987e\u987f\u9884\u9886\u9891\u9898\u989c\u989d\u98ce\u98de\u996d\u996e\u9970\u9a6c\u9a71\u9a76\u9a8c"
    "\u9a91\u9c7c\u9c9c\u9e1f\u9e21\u9e23\u9f50\u9f7f\u9f84\u9f99"
)


def _assert_tw(text):
    """面向使用者的文字不得含簡體字。"""
    bad = sorted({ch for ch in str(text) if ch in SIMPLIFIED_CHARS})
    assert not bad, f"簡體字 {bad!r} 出現在：{text!r}"


def _assert_no_iso(text):
    """面向使用者的日期一律 YYYYMMDD，不得殘留 ISO（YYYY-MM-DD）；檔案路徑類 detail 不適用。"""
    assert not re.search(r"\d{4}-\d{2}-\d{2}", str(text)), f"ISO 日期殘留：{text!r}"


def _rewrite_snapshot(root, meta, frame):
    """以新的資料框覆寫 fixture 的 parquet 與 CSV，並同步 latest.json 的 sha256（模擬產製端寫出不同內容）。"""
    root = Path(root)
    pq = root / meta["snapshot"]
    frame.to_parquet(pq, index=False)
    csv = root / meta["snapshot_csv"]
    frame.to_csv(csv, index=False, encoding="utf-8")
    meta = dict(meta, snapshot_sha256=hashlib.sha256(pq.read_bytes()).hexdigest(),
                snapshot_csv_sha256=hashlib.sha256(csv.read_bytes()).hexdigest())
    (root / "predictions" / "latest.json").write_text(json.dumps(meta, ensure_ascii=False), encoding="utf-8")
    return meta


def test_reader_roundtrip_and_freshness(tmp_path):
    write_fixture(tmp_path, signal_date="2026-10-16", target_date="2026-10-19")
    snap = load_latest(tmp_path, expected_signal_date="2026-10-16")
    assert snap.target_date == "2026-10-19"
    assert len(snap.top(5)) == 5
    assert snap.top(5)["p_up"].is_monotonic_decreasing
    assert snap.row("2330")["stock_id"] == "2330"
    assert snap.row("9999") is None
    text = describe(snap.meta)
    assert "訊號日 20261016 → 目標日 20261019" in text
    assert text.startswith(MODEL_LABEL + "｜")
    assert "前一晚美股收盤已納入" in text and "美股收漲（建議日）" in text
    _assert_tw(text)
    _assert_no_iso(text)
    # meta 本身維持 ISO（機器欄位），只有顯示文字轉成 YYYYMMDD
    assert snap.meta["signal_date"] == "2026-10-16" and snap.signal_date == "2026-10-16"
    with pytest.raises(PreopenUnavailable) as e:
        load_latest(tmp_path, expected_signal_date="2026-10-15")
    assert e.value.reason == "preopen_stale"
    assert e.value.detail == "快照訊號日 20261016，預期 20261015"
    assert str(e.value) == "preopen_stale: 快照訊號日 20261016，預期 20261015"
    _assert_tw(e.value.detail)
    _assert_no_iso(e.value.detail)
    # expected_signal_date 也可給 date 物件；detail 一樣是 YYYYMMDD
    with pytest.raises(PreopenUnavailable) as e2:
        load_latest(tmp_path, expected_signal_date=dt.date(2026, 10, 14))
    assert e2.value.detail == "快照訊號日 20261016，預期 20261014"


def test_reader_rejects_tampered_snapshot(tmp_path):
    meta = write_fixture(tmp_path)
    p = tmp_path / meta["snapshot"]
    df = pd.read_parquet(p)
    df.loc[0, "p_up"] = 0.999
    df.to_parquet(p, index=False)
    with pytest.raises(PreopenUnavailable) as e:
        load_latest(tmp_path)
    assert e.value.reason == "preopen_snapshot_sha_mismatch"
    assert e.value.detail == f"快照 sha256 不符：{p}"
    _assert_tw(e.value.detail)


def test_reader_missing_root(tmp_path):
    with pytest.raises(PreopenUnavailable) as e:
        load_latest(tmp_path / "nothing")
    assert e.value.reason == "preopen_no_latest"
    assert e.value.detail == f"找不到 {tmp_path / 'nothing' / 'predictions' / 'latest.json'}"
    _assert_tw(e.value.detail)


def test_expected_us_session_holidays():
    assert pdata.expected_us_session(dt.date(2026, 11, 26)) == dt.date(2026, 11, 25)  # Thanksgiving
    assert pdata.expected_us_session(dt.date(2026, 10, 18)) == dt.date(2026, 10, 16)  # Sunday -> Friday
    assert pdata.expected_us_session(dt.date(2026, 7, 4)) == dt.date(2026, 7, 2)  # Sat; Jul 3 observed holiday


def _global(dates_by_inst):
    frames = {k: pd.Series(1.0 + np.arange(len(v)) * 0.01, index=pd.DatetimeIndex(v)) for k, v in dates_by_inst.items()}
    return pd.DataFrame(frames).sort_index()


def test_us_overnight_status_ok_missing_holiday():
    full = ["2026-10-14", "2026-10-15", "2026-10-16"]
    g = _global({i: full for i in pdata.US_GATE_INSTRUMENTS})
    ok = pdata.us_overnight_status(g, dt.date(2026, 10, 16), dt.date(2026, 10, 19))
    assert ok["status"] == "ok" and ok["expected_us_session"] == "2026-10-16"
    g2 = _global({i: full[:-1] for i in pdata.US_GATE_INSTRUMENTS})
    miss = pdata.us_overnight_status(g2, dt.date(2026, 10, 16), dt.date(2026, 10, 19))
    assert miss["status"] == "missing" and miss["latest_us_session"] == "2026-10-15"
    # TW session on Friday 2026-11-27 after US Thanksgiving (Nov 26): signal Thu 11-26 -> no new US session
    g3 = _global({i: ["2026-11-24", "2026-11-25"] for i in pdata.US_GATE_INSTRUMENTS})
    hol = pdata.us_overnight_status(g3, dt.date(2026, 11, 26), dt.date(2026, 11, 27))
    assert hol["status"] == "holiday"


def test_calibrator_identity_and_monotone():
    small = pd.DataFrame({"signal_date": ["2026-01-02"] * 5, "p_raw": [0.4] * 5, "r_raw": [0.0] * 5, "y": [0.01] * 5})
    assert producer.fit_calibrator(small, 120, 60)["kind"] == "identity"
    rng = np.random.default_rng(1)
    days = pd.bdate_range("2026-01-01", periods=130).strftime("%Y-%m-%d")
    rows = []
    for d in days:
        p = rng.uniform(0.2, 0.8, 50)
        y = (rng.uniform(size=50) < p).astype(float) * 0.02 - 0.01
        rows += list(zip([d] * 50, p, p * 0.01, y))
    pool = pd.DataFrame(rows, columns=["signal_date", "p_raw", "r_raw", "y"])
    cal = producer.fit_calibrator(pool, 120, 60)
    assert cal["kind"] == "platt_ladshrink" and cal["platt_coef"] > 0 and cal["dates"] == 120
    p, r = producer.apply_calibrator(cal, np.array([0.3, 0.5, 0.7]), np.array([0.0, 0.0, 0.0]))
    assert np.all(np.diff(p) > 0)


def test_resolve_target_explicit_and_weekday():
    assert producer.resolve_target(dt.date(2026, 10, 16), "2026-10-19") == (dt.date(2026, 10, 19), "explicit")
    tgt, src = producer.resolve_target(dt.date(2030, 1, 4))  # far-future Friday: falls back to next weekday
    assert tgt == dt.date(2030, 1, 7) and src == "next_weekday_assumed"


def test_feature_list_matches_frozen_recipe():
    assert len(producer.FEATURES) == len(set(producer.FEATURES))
    assert producer.FEATURES[-1] == "is_twse"
    assert not any(f.startswith("on_usd") for f in producer.FEATURES)
    assert producer.RECIPE_V1["frozen_sha256"].startswith("e53c2416")
    assert producer.RECIPES["h1pre_final_nogc_xs"] is producer.RECIPE_V1


def test_recipe_v2_registered_and_hash_matches_content():
    v2 = producer.RECIPE_V2
    assert producer.RECIPES[v2["name"]] is v2 and v2["name"] != producer.RECIPE_V1["name"]
    own = {k: v for k, v in producer.RECIPE_V2_OWN.items() if k != "frozen_sha256"}
    body = json.dumps(own, sort_keys=True, ensure_ascii=False)
    assert hashlib.sha256(body.encode("utf-8")).hexdigest() == v2["frozen_sha256"]
    assert all(s["weight"] > 0 and s["seeds"] and s["train_window"] >= 252 for s in v2["sources"])
    assert len(v2["calib_seed"]["sha256"]) == 64 and v2["calib_seed"]["file"].endswith(".parquet")
    # unchanged pieces are inherited from v1
    for key in ("horizon", "min_hist", "tradable", "market_features", "market_alpha", "reg_loss", "refit_every"):
        assert v2[key] == producer.RECIPE_V1[key]


def _tiny_panel(n_days=60, n_stocks=40, seed=3):
    rng = np.random.default_rng(seed)
    dates = pd.bdate_range("2025-01-01", periods=n_days)
    rows = []
    for di, d in enumerate(dates):
        for s in range(n_stocks):
            rows.append((d, str(1000 + s), di))
    panel = pd.DataFrame(rows, columns=["date", "stock_id", "di"])
    for f in producer.FEATURES:
        panel[f] = rng.normal(size=len(panel)).astype("float32")
    panel["y1"] = (0.01 * panel[producer.FEATURES[0]] + 0.02 * rng.normal(size=len(panel))).astype("float32")
    panel["yx1"] = panel["y1"] - panel.groupby("di")["y1"].transform("mean")
    panel["hist_len"] = 500
    return panel, pd.DatetimeIndex(dates)


def test_v2_ensemble_fit_predict_is_weighted_source_average():
    panel, dates = _tiny_panel()
    recipe = {**producer.RECIPE_V2, "hgb": {**producer.RECIPE_V2["hgb"], "max_iter": 5, "min_samples_leaf": 20},
              "sources": [{"seeds": [0, 1], "train_window": 30, "recency_halflife": None, "weight": 1.0},
                          {"seeds": [0], "train_window": 40, "recency_halflife": 10, "weight": 2.0}]}
    model, info = producer.fit_model(panel, dates, 50, recipe)
    assert len(model["sources"]) == 2 and len(model["sources"][0]["clfs"]) == 2 and len(info["sources"]) == 2
    rows = panel[panel["di"].between(50, 52)]
    p, r = producer.predict_raw(model, rows, recipe)
    X = rows[producer.FEATURES].to_numpy("float32")
    manual_p = np.zeros(len(rows))
    for s, w in zip(model["sources"], (1 / 3, 2 / 3)):
        manual_p += w * np.mean([c.predict_proba(X)[:, 1] for c in s["clfs"]], axis=0)
    assert np.allclose(p, manual_p) and np.all((p > 0) & (p < 1)) and np.isfinite(r).all()
    # training rows end h + 1 sessions before the signal (labels matured), as in v1
    assert info["train_last_date"] == str(dates[50 - 2].date())


def test_v1_fit_unchanged_by_recipe_argument():
    panel, dates = _tiny_panel()
    v1 = {**producer.RECIPE_V1, "hgb": {**producer.RECIPE_V1["hgb"], "max_iter": 5, "min_samples_leaf": 20}, "train_window": 30}
    m1, _ = producer.fit_model(panel, dates, 50, v1)
    assert set(m1) == {"clf", "reg", "market_scaler", "market_ridge"}
    rows = panel[panel["di"] == 50]
    p, r = producer.predict_raw(m1, rows, v1)
    assert p.shape == (len(rows),) and np.isfinite(r).all()


def test_calibration_pool_uses_only_rows_of_the_recipe(tmp_path):
    con = producer.ledger(tmp_path)
    con.executemany("INSERT INTO calibration_pool VALUES (?,?,?,?,?,?)", [
        ("2025-01-02", "1101", 0.4, 0.001, 0.01, "research_walkforward_oos"),
        ("2025-01-02", "1101", 0.6, 0.002, 0.01, producer.RECIPE_V2["calib_seed"]["source"]),
    ])
    con.commit()
    v1 = producer.calibration_pool(con, producer.RECIPE_V1, set())
    v2 = producer.calibration_pool(con, producer.RECIPE_V2, set())
    assert v1["p_raw"].tolist() == [0.4] and v2["p_raw"].tolist() == [0.6]
    con.close()


def test_recipe_of_defaults_to_v1_for_legacy_metadata():
    assert producer.recipe_of({}) is producer.RECIPE_V1
    assert producer.recipe_of({"recipe": {"frozen_sha256": "x"}}) is producer.RECIPE_V1
    assert producer.recipe_of({"recipe": {"name": producer.RECIPE_V2["name"]}}) is producer.RECIPE_V2


def test_calibrators_platt_and_stack2_roundtrip():
    rng = np.random.default_rng(0)
    n = 4000
    pool = pd.DataFrame({"signal_date": np.repeat(pd.bdate_range("2025-01-01", periods=80).strftime("%Y-%m-%d"), n // 80),
                         "stock_id": np.tile(np.arange(n // 80).astype(str), 80),
                         "p_raw": rng.uniform(0.2, 0.8, n), "r_raw": rng.normal(0, 0.01, n)})
    pool["y"] = np.where(rng.uniform(size=n) < pool["p_raw"], 0.01, -0.01)
    for kind in ("platt_ladshrink", "stack2_ladshrink"):
        cal = producer.fit_calibrator(pool, 60, 30, kind)
        assert cal["kind"] == kind and cal["dates"] == 60
        p, r = producer.apply_calibrator(cal, pool["p_raw"].to_numpy(), pool["r_raw"].to_numpy())
        assert np.all((p > 0) & (p < 1)) and np.isfinite(r).all()


def _root_with_active(tmp_path, recipe_name):
    root = tmp_path / "root"
    (root / "models" / "artifacts").mkdir(parents=True)
    sha = "c" * 64
    meta = {"sha256": sha, "recipe": {"name": recipe_name}} if recipe_name else {"sha256": sha}
    (root / "models" / "artifacts" / f"{sha}.json").write_text(json.dumps(meta), encoding="utf-8")
    (root / "models" / "current.json").write_text(json.dumps({"schema": producer.POINTER_SCHEMA, "model_sha256": sha,
                                                              "first_signal_date": "2026-10-16"}), encoding="utf-8")
    return root


def test_train_recipe_follows_the_active_model(tmp_path):
    assert producer.train_recipe(tmp_path / "empty")[0] is producer.RECIPE_V1
    r1 = _root_with_active(tmp_path / "a", producer.RECIPE_V1["name"])
    assert producer.train_recipe(r1)[0] is producer.RECIPE_V1
    r2 = _root_with_active(tmp_path / "b", producer.RECIPE_V2["name"])
    assert producer.train_recipe(r2)[0] is producer.RECIPE_V2
    legacy = _root_with_active(tmp_path / "c", None)
    assert producer.train_recipe(legacy)[0] is producer.RECIPE_V1
    assert producer.train_recipe(r1, producer.RECIPE_V2["name"])[0] is producer.RECIPE_V2
    assert producer.RECIPE is producer.RECIPE_V1


def test_train_recipe_switch_needs_force_and_authorization(tmp_path, monkeypatch):
    root = _root_with_active(tmp_path, producer.RECIPE_V1["name"])

    def no_data(*a, **k):
        raise AssertionError("must not load data")
    monkeypatch.setattr(producer, "build_panel", no_data)
    for extra in ([], ["--force"], ["--authorization", "x"]):
        with pytest.raises(SystemExit) as exc:
            producer.main(["train", "--root", str(root), "--recipe", producer.RECIPE_V2["name"]] + extra)
        assert "切換配方" in str(exc.value)


def test_v2_seed_requires_file_with_matching_sha(tmp_path):
    con = producer.ledger(tmp_path)
    with pytest.raises(SystemExit):
        producer.seed_calibration_pool(con, producer.RECIPE_V2, tmp_path)
    seeds = tmp_path / "models" / "seeds"
    seeds.mkdir(parents=True)
    (seeds / producer.RECIPE_V2["calib_seed"]["file"]).write_bytes(b"not the seed")
    with pytest.raises(SystemExit):
        producer.seed_calibration_pool(con, producer.RECIPE_V2, tmp_path)
    con.close()


def test_predict_manifest_records_recipe(tmp_path, monkeypatch):
    root = _fake_predict(monkeypatch, tmp_path / "r")
    producer.main(["predict", "--no-latest", "--target-date", "2026-10-19", "--root", str(root)])
    m = _only_manifest(root)
    assert m["recipe"] == {"name": producer.RECIPE_V1["name"], "frozen_sha256": producer.RECIPE_V1["frozen_sha256"]}


@pytest.mark.skipif(os.environ.get("PREOPEN_PARITY") != "1" or not RESEARCH_CACHE.exists(),
                    reason="slow parity check against the research panel; set PREOPEN_PARITY=1")
def test_feature_parity_with_research_panel():
    import stock_daily_preopen_features as pfeat
    rd = lambda n: pd.read_parquet(RESEARCH_CACHE / n)  # noqa: E731
    out, _ = pfeat.build_panel(rd("prices.parquet"), rd("chips.parquet"), rd("month_revenue.parquet"),
                               rd("stock_info.parquet"), rd("global.parquet"))
    ref = rd("panel.parquet")
    ref["stock_id"] = ref["stock_id"].astype(str)
    m = out.merge(ref, on=["date", "stock_id"], suffixes=("", "_ref"))
    assert len(m) == len(ref)
    for c in producer.FEATURES:
        if c == "is_twse":
            continue
        a = m[c].astype("float64").to_numpy()
        b = m[c + "_ref"].astype("float64").to_numpy()
        assert (np.isnan(a) == np.isnan(b)).all(), c
        both = ~np.isnan(a)
        assert np.nanmax(np.abs(a[both] - b[both])) < 1e-5, c


def test_latest_prospective_keeps_last_publication():
    d = pd.DataFrame({"signal_date": ["2026-10-16"] * 3, "stock_id": ["2330", "2330", "2317"],
                      "recorded_at": ["2026-10-18T22:00:00+00:00", "2026-10-18T23:30:00+00:00", "2026-10-18T22:00:00+00:00"],
                      "p_up": [0.4, 0.6, 0.5]})
    out = producer.latest_prospective(d)
    assert len(out) == 2
    assert float(out[out["stock_id"] == "2330"]["p_up"].iloc[0]) == 0.6


def test_reader_falls_back_to_csv_without_parquet_engine(tmp_path, monkeypatch):
    meta = write_fixture(tmp_path, signal_date="2026-10-16")
    ref = load_latest(tmp_path, expected_signal_date="2026-10-16")

    def no_engine(*_a, **_k):
        raise ImportError("Unable to find a usable engine; tried using: 'pyarrow', 'fastparquet'.")

    monkeypatch.setattr(pd, "read_parquet", no_engine)
    got = load_latest(tmp_path, expected_signal_date="2026-10-16")
    assert list(got.frame["stock_id"]) == list(ref.frame["stock_id"])
    assert got.frame["stock_id"].map(type).eq(str).all()
    assert got.frame["tradable"].dtype == bool
    for c in ("p_up", "p_up_raw", "exp_ret", "exp_ret_raw", "signal_close", "amt20"):
        assert (got.frame[c].to_numpy() == ref.frame[c].to_numpy()).all(), c
    assert got.frame["rank_tradable"].isna().equals(ref.frame["rank_tradable"].isna())
    # tampered csv is rejected, missing csv metadata gives the engine-missing code
    csv = tmp_path / meta["snapshot_csv"]
    csv.write_text(csv.read_text(encoding="utf-8").replace("2330", "9999"), encoding="utf-8")
    with pytest.raises(PreopenUnavailable) as e:
        load_latest(tmp_path)
    assert e.value.reason == "preopen_snapshot_sha_mismatch"
    assert e.value.detail == f"快照 sha256 不符：{csv}"
    _assert_tw(e.value.detail)
    # CSV 伴隨檔不存在
    csv.unlink()
    with pytest.raises(PreopenUnavailable) as e:
        load_latest(tmp_path)
    assert e.value.reason == "preopen_snapshot_missing"
    assert e.value.detail == f"找不到 {csv}"
    latest = tmp_path / "predictions" / "latest.json"
    m = json.loads(latest.read_text(encoding="utf-8"))
    m.pop("snapshot_csv")
    latest.write_text(json.dumps(m), encoding="utf-8")
    with pytest.raises(PreopenUnavailable) as e:
        load_latest(tmp_path)
    assert e.value.reason == "preopen_parquet_engine_missing"
    assert e.value.detail.startswith("缺少 parquet 讀取元件且無 CSV 伴隨檔：ImportError: Unable to find a usable engine")
    _assert_tw(e.value.detail)


def test_reader_detail_texts_are_traditional_chinese_with_values(tmp_path):
    meta = write_fixture(tmp_path, signal_date="2026-10-16")
    latest = tmp_path / "predictions" / "latest.json"
    base = pd.read_parquet(tmp_path / meta["snapshot"])

    def set_latest(**changes):
        latest.write_text(json.dumps(dict(meta, **changes), ensure_ascii=False), encoding="utf-8")

    def fails(expected_reason):
        with pytest.raises(PreopenUnavailable) as e:
            load_latest(tmp_path)
        assert e.value.reason == expected_reason
        _assert_tw(e.value.detail)
        assert str(e.value) == f"{expected_reason}: {e.value.detail}"
        return e.value.detail

    set_latest(schema="bogus-schema")
    assert fails("preopen_schema_mismatch") == f"latest.json 的 schema 為 bogus-schema，預期 {LATEST_SCHEMA}"

    set_latest(snapshot="predictions/2026-10-16/absent.parquet")
    assert fails("preopen_snapshot_missing") == f"找不到 {tmp_path / 'predictions' / '2026-10-16' / 'absent.parquet'}"

    meta = _rewrite_snapshot(tmp_path, meta, base.drop(columns=["amt20", "feature_coverage"]))
    assert fails("preopen_snapshot_columns") == "缺少欄位：amt20、feature_coverage"

    nan_frame = base.copy()
    nan_frame.loc[0, "p_up"] = np.nan
    meta = _rewrite_snapshot(tmp_path, meta, nan_frame)
    assert fails("preopen_snapshot_values") == "預測值為空或含 NaN"
    meta = _rewrite_snapshot(tmp_path, meta, base.iloc[0:0])
    assert fails("preopen_snapshot_values") == "預測值為空或含 NaN"

    meta = _rewrite_snapshot(tmp_path, meta, base)
    set_latest(us_overnight=dict(meta["us_overnight"], status="weird"))
    assert fails("preopen_us_status_invalid") == "美股狀態無效：weird（允許 ok／holiday／missing）"
    set_latest(us_overnight={})
    assert fails("preopen_us_status_invalid") == "美股狀態無效：None（允許 ok／holiday／missing）"


def test_reason_text_covers_every_reason_code():
    source = Path(reader.__file__).read_text(encoding="utf-8")
    raised = set(re.findall(r'PreopenUnavailable\("(\w+)"', source))
    assert raised == REASON_CODES, "讀取器拋出的原因代碼與測試清單不一致"
    assert set(REASON_TEXT_ZH) == REASON_CODES, "REASON_TEXT_ZH 必須涵蓋全部原因代碼"
    for code in sorted(REASON_CODES):
        zh = REASON_TEXT_ZH[code]
        assert zh and re.search(r"[一-鿿]", zh), code
        assert reason_text(code) == f"{code}：{zh}"
        _assert_tw(reason_text(code))
    assert REASON_TEXT_UNKNOWN_ZH == "開盤前版不可用"
    assert reason_text("preopen_reader_error") == "preopen_reader_error：開盤前版不可用"
    assert reason_text(None) == "None：開盤前版不可用"
    _assert_tw(reason_text("whatever"))
    # 整個模組原始碼不得含簡體字（涵蓋所有字串常數與註解）
    bad = sorted({ch for ch in source if ch in SIMPLIFIED_CHARS})
    assert not bad, f"讀取器原始碼含簡體字：{bad!r}"


def test_describe_uses_yyyymmdd_and_traditional_chinese():
    base = {"signal_date": "2026-10-16", "target_date": "2026-10-19", "model_sha256": "abcdef1234567890" + "0" * 48}
    cases = {
        ("ok", True): ("前一晚美股收盤已納入", "美股收漲（建議日）"),
        ("holiday", False): ("前一晚美股休市", "美股未收漲（非建議日）"),
        ("missing", None): ("前一晚美股收盤缺漏（以缺值推論，準確度可能下降）；美股資料最新日期 未提供", "美股漲跌未知"),
        ("weird", None): ("美股狀態未知", "美股漲跌未知"),
    }
    for (status, gate), (status_txt, gate_txt) in cases.items():
        text = describe(dict(base, us_overnight={"status": status}, gate_us_up=gate))
        assert text == f"{MODEL_LABEL}｜訊號日 20261016 → 目標日 20261019｜{status_txt}｜{gate_txt}｜模型 abcdef123456"
        _assert_tw(text)
        _assert_no_iso(text)
    # 缺日期或非 ISO 字串：不得當掉，顯示「未知」或原文
    text = describe({})
    assert "訊號日 未知 → 目標日 未知" in text and "美股狀態未知" in text and "美股漲跌未知" in text
    _assert_tw(text)
    _assert_no_iso(text)
    assert "訊號日 20261016 → 目標日 20261019" in describe(dict(base, target_date="2026-10-19T00:00:00"))
    assert "訊號日 x → 目標日 20261019" in describe(dict(base, signal_date="x"))


# ---------------------------------------------------------------- 籌碼新鮮度（chips_freshness，2026-10-18）
def _objects(**overrides):
    """假的 provenance objects：五條籌碼路由預設都到 2026-10-16；overrides 以路由名給 actual_max_date（None 表示缺）。"""
    objects = {r: {"sha256": "0" * 64, "status": "SUCCESS", "actual_max_date": "2026-10-16"} for r in CHIP_ROUTES}
    objects["finmind_price_adj"] = {"sha256": "1" * 64, "status": "SUCCESS", "actual_max_date": "2026-10-16"}
    for route, value in overrides.items():
        objects[route]["actual_max_date"] = value
    return objects


def test_chips_freshness_ok_stale_and_unknown():
    sig = dt.date(2026, 10, 16)
    ok = producer.chips_freshness({"objects": _objects()}, sig)
    assert ok == {"status": "ok", "signal_date": "2026-10-16", "routes": {r: "2026-10-16" for r in CHIP_ROUTES},
                  "route_status": {r: "SUCCESS" for r in CHIP_ROUTES}, "coverage": {r: None for r in CHIP_ROUTES},
                  "stale_routes": [], "partial_routes": []}
    assert producer.chips_freshness_message(ok) == "t 日籌碼"  # 沒有任何問題時不會被呼叫；回傳只有前綴
    # 只比對日期：路由狀態 PARTIAL 但日期已到訊號日仍為 ok（不阻擋），只列入 partial_routes 並記錄成功/應有數量
    partial = _objects()
    partial["finmind_institutional"].update(status="PARTIAL", coverage={"success": 100, "total": 1215})
    partial["finmind_per"].update(status="PARTIAL")  # 沒有 coverage 數字
    res = producer.chips_freshness({"objects": partial}, sig)
    assert res["status"] == "ok" and res["stale_routes"] == [] and res["partial_routes"] == ["finmind_institutional", "finmind_per"]
    assert res["route_status"]["finmind_institutional"] == "PARTIAL"
    assert res["coverage"]["finmind_institutional"] == {"success": 100, "total": 1215} and res["coverage"]["finmind_per"] is None
    assert producer.chips_freshness_message(res) == ("t 日籌碼資料不完整（中央回報成功/應有）：法人（finmind_institutional）100/1215、"
                                                     "本益比（finmind_per）數量未知")
    # 過期的路由不重複列入 partial_routes
    partial["finmind_institutional"]["actual_max_date"] = "2026-10-15"
    res = producer.chips_freshness({"objects": partial}, sig)
    assert res["status"] == "stale" and res["stale_routes"] == ["finmind_institutional"] and res["partial_routes"] == ["finmind_per"]
    assert producer.chips_freshness_message(res) == ("t 日籌碼未更新到訊號日 20261016：法人（finmind_institutional）最新到 20261015；"
                                                     "資料不完整（中央回報成功/應有）：本益比（finmind_per）數量未知")
    _assert_tw(producer.chips_freshness_message(res))
    _assert_no_iso(producer.chips_freshness_message(res))
    # 一條路由停在前一個交易日 → stale，只列出該路由；其他路由日期照錄
    stale = producer.chips_freshness({"objects": _objects(finmind_margin="2026-10-15")}, sig)
    assert stale["status"] == "stale" and stale["stale_routes"] == ["finmind_margin"]
    assert stale["routes"]["finmind_margin"] == "2026-10-15" and stale["routes"]["finmind_per"] == "2026-10-16"
    # 晚於訊號日不算過期；YYYYMMDD 寫法、帶時間的 ISO 都接受
    later = producer.chips_freshness({"objects": _objects(finmind_margin="2026-10-19", finmind_per="20261016",
                                                          finmind_day_trading="2026-10-16T00:00:00")}, sig)
    assert later["status"] == "ok" and later["routes"]["finmind_per"] == "2026-10-16"
    # JSON 數字型日期不得被當成 epoch 奈秒
    assert producer.chips_freshness({"objects": _objects(finmind_margin=20261016)}, sig)["status"] == "ok"
    # _as_date 只接受 YYYY-MM-DD／YYYYMMDD（可帶時間），不做自由解析（today、now 之類不得算新鮮）
    assert producer._as_date("2026-10-16 20:36:00") == dt.date(2026, 10, 16)
    assert producer._as_date(" 20261016 ") == dt.date(2026, 10, 16)
    for bad_value in (None, "", "n/a", "today", "now", "12:00", "2026", "2026-13-01", "20261016x", 1.5e9, "NaT"):
        assert producer._as_date(bad_value) is None, bad_value
    # 日期缺漏、無法解析（含相對時間字串）或路由不存在 → 保守視為未更新；stale_routes 依 CHIP_ROUTES 順序
    objects = _objects(finmind_institutional="today", finmind_per=None, finmind_day_trading="n/a")
    del objects["finmind_shareholding"]
    bad = producer.chips_freshness({"objects": objects}, sig)
    assert bad["status"] == "stale"
    assert bad["stale_routes"] == ["finmind_institutional", "finmind_day_trading", "finmind_per", "finmind_shareholding"]
    assert bad["routes"]["finmind_institutional"] is None and bad["routes"]["finmind_day_trading"] is None
    assert bad["routes"]["finmind_per"] is None and bad["routes"]["finmind_shareholding"] is None
    assert bad["route_status"]["finmind_shareholding"] is None and bad["route_status"]["finmind_per"] == "SUCCESS"
    assert producer.chips_freshness({}, sig)["stale_routes"] == list(CHIP_ROUTES)
    assert producer.chips_freshness(None, sig)["status"] == "stale"
    assert producer.chips_freshness({"objects": None}, sig)["status"] == "stale"
    # 主控台／拒絕訊息：繁體中文、YYYYMMDD、列出每條過期路由的日期（缺漏顯示「未知」）
    msg = producer.chips_freshness_message(stale)
    assert msg == "t 日籌碼未更新到訊號日 20261016：融資券（finmind_margin）最新到 20261015"
    msg2 = producer.chips_freshness_message(bad)
    assert msg2 == ("t 日籌碼未更新到訊號日 20261016：法人（finmind_institutional）日期未知、當沖（finmind_day_trading）日期未知、"
                    "本益比（finmind_per）日期未知、外資持股（finmind_shareholding）日期未知")
    for text in (msg, msg2):
        _assert_tw(text)
        _assert_no_iso(text)


def test_describe_adds_chips_note_only_when_stale():
    base = {"signal_date": "2026-10-16", "target_date": "2026-10-19", "model_sha256": "abcdef1234567890" + "0" * 48,
            "us_overnight": {"status": "ok"}, "gate_us_up": True}
    plain = describe(base)
    assert "籌碼" not in plain
    fresh = {"status": "ok", "signal_date": "2026-10-16", "routes": {r: "2026-10-16" for r in CHIP_ROUTES},
             "stale_routes": []}
    assert describe(dict(base, chips_freshness=fresh)) == plain
    stale = dict(fresh, status="stale", stale_routes=["finmind_margin"],
                 routes=dict(fresh["routes"], finmind_margin="2026-10-15"))
    text = describe(dict(base, chips_freshness=stale))
    assert text == (f"{MODEL_LABEL}｜訊號日 20261016 → 目標日 20261019｜前一晚美股收盤已納入"
                    f"｜t 日籌碼未更新：融資券最新到 20261015（以舊值推論，準確度可能下降）｜美股收漲（建議日）｜模型 abcdef123456")
    assert chips_freshness_text(stale) == "t 日籌碼未更新：融資券最新到 20261015（以舊值推論，準確度可能下降）"
    # 多條路由、不同日期：依日期由舊到新分組；日期未知另列；非 stale 回 None；缺細節用通用文字
    multi = dict(stale, stale_routes=["finmind_institutional", "finmind_margin", "finmind_shareholding", "finmind_per"],
                 routes=dict(stale["routes"], finmind_institutional="2026-10-14", finmind_shareholding="2026-10-15",
                             finmind_per=None))
    assert chips_freshness_text(multi) == ("t 日籌碼未更新：法人最新到 20261014，融資券、外資持股最新到 20261015，本益比日期未知"
                                           "（以舊值推論，準確度可能下降）")
    _assert_tw(chips_freshness_text(multi))
    _assert_no_iso(chips_freshness_text(multi))
    assert chips_freshness_text(fresh) is None and chips_freshness_text(None) is None and chips_freshness_text("stale") is None
    assert chips_freshness_text({"status": "stale"}) == CHIPS_STALE_TEXT_ZH == CHIPS_STALE_PREFIX_ZH + CHIPS_STALE_SUFFIX_ZH
    # 中央標示不完整（PARTIAL）：只顯示成功/應有數量，不算 stale；缺數字顯示「數量未知」
    partial = dict(fresh, partial_routes=["finmind_institutional", "finmind_per"],
                   route_status={**{r: "SUCCESS" for r in CHIP_ROUTES}, "finmind_institutional": "PARTIAL", "finmind_per": "PARTIAL"},
                   coverage={**{r: {"success": 1215, "total": 1215} for r in CHIP_ROUTES},
                             "finmind_institutional": {"success": 100, "total": 1215}, "finmind_per": None})
    partial_text = chips_freshness_text(partial)
    assert partial_text == (f"{CHIPS_PARTIAL_PREFIX_ZH}：法人 100/1215、本益比 數量未知{CHIPS_PARTIAL_SUFFIX_ZH}")
    assert partial_text == "t 日籌碼資料不完整（中央回報成功/應有）：法人 100/1215、本益比 數量未知（缺的以舊值推論，準確度可能下降）"
    assert describe(dict(base, chips_freshness=partial)).count("｜") == plain.count("｜") + 1
    assert partial_text in describe(dict(base, chips_freshness=partial))
    _assert_tw(partial_text)
    _assert_no_iso(partial_text)
    # 同時過期與不完整：合成一段；過期的路由不再列入不完整
    both_dict = dict(partial, status="stale", stale_routes=["finmind_margin"],
                     routes=dict(fresh["routes"], finmind_margin="2026-10-15"))
    assert chips_freshness_text(both_dict) == ("t 日籌碼未更新：融資券最新到 20261015；資料不完整（中央回報成功/應有）："
                                              "法人 100/1215、本益比 數量未知（以舊值推論，準確度可能下降）")
    overlap = dict(both_dict, partial_routes=["finmind_margin", "finmind_institutional"])
    assert chips_freshness_text(overlap) == ("t 日籌碼未更新：融資券最新到 20261015；資料不完整（中央回報成功/應有）："
                                            "法人 100/1215（以舊值推論，準確度可能下降）")
    assert chips_freshness_text(dict(fresh, partial_routes=[])) is None
    assert CHIPS_STALE_TEXT_ZH == "t 日籌碼未更新（以舊值推論，準確度可能下降）"
    _assert_tw(text)
    _assert_no_iso(text)
    # 美股缺漏＋籌碼過期同時標示
    both = describe(dict(base, us_overnight={"status": "missing"}, gate_us_up=None, chips_freshness=stale))
    assert ("前一晚美股收盤缺漏（以缺值推論，準確度可能下降）；美股資料最新日期 未提供｜t 日籌碼未更新：融資券最新到 20261015（以舊值推論，準確度可能下降）"
            "｜美股漲跌未知" in both)
    # 欄位缺失或型別異常：不當掉、不加註
    for odd in (None, "stale", ["stale"], {"status": "weird"}, {}):
        assert describe(dict(base, chips_freshness=odd)) == plain


def test_fixture_chips_freshness_and_reader_tolerates_old_manifest(tmp_path):
    meta = write_fixture(tmp_path)
    assert meta["chips_freshness"] == {"status": "ok", "signal_date": "2026-10-16",
                                       "routes": {r: "2026-10-16" for r in CHIP_ROUTES},
                                       "route_status": {r: "SUCCESS" for r in CHIP_ROUTES},
                                       "coverage": {r: {"success": 1215, "total": 1215} for r in CHIP_ROUTES},
                                       "stale_routes": [], "partial_routes": []}
    # 資料不完整（PARTIAL）：status 仍 ok、degraded 仍 False，只有說明文字多一段
    meta = write_fixture(tmp_path, partial_routes=["finmind_institutional"])
    assert meta["chips_freshness"]["status"] == "ok" and meta["degraded"] is False
    assert meta["chips_freshness"]["partial_routes"] == ["finmind_institutional"]
    assert meta["chips_freshness"]["route_status"]["finmind_institutional"] == "PARTIAL"
    assert meta["chips_freshness"]["coverage"]["finmind_institutional"] == {"success": 100, "total": 1215}
    snap = load_latest(tmp_path, expected_signal_date="2026-10-16")
    assert "t 日籌碼資料不完整（中央回報成功/應有）：法人 100/1215（缺的以舊值推論，準確度可能下降）" in describe(snap.meta)
    # 同一路由同時給 stale 與 partial：以 stale 為準
    meta = write_fixture(tmp_path, stale_routes=["finmind_margin"], partial_routes=["finmind_margin", "finmind_per"],
                         partial_coverage=(5, 368))
    assert meta["chips_freshness"]["stale_routes"] == ["finmind_margin"] and meta["chips_freshness"]["partial_routes"] == ["finmind_per"]
    assert meta["chips_freshness"]["coverage"]["finmind_per"] == {"success": 5, "total": 368}
    with pytest.raises(ValueError):
        write_fixture(tmp_path, partial_routes=["finmind_price_adj"])
    meta = write_fixture(tmp_path)  # 回到預設（新鮮且完整）
    assert meta["degraded"] is False
    snap = load_latest(tmp_path, expected_signal_date="2026-10-16")
    assert snap.chips_freshness["status"] == "ok"
    assert "籌碼" not in describe(snap.meta)
    # 全部過期：各路由停在前一個平日（2026-10-16 週五 → 10-15 週四）
    meta = write_fixture(tmp_path, chips_status="stale")
    assert meta["chips_freshness"]["status"] == "stale"
    assert meta["chips_freshness"]["stale_routes"] == list(CHIP_ROUTES)
    assert set(meta["chips_freshness"]["routes"].values()) == {"2026-10-15"}
    assert meta["degraded"] is True
    snap = load_latest(tmp_path, expected_signal_date="2026-10-16")
    assert snap.chips_freshness["stale_routes"] == list(CHIP_ROUTES)
    assert "t 日籌碼未更新：法人、融資券、當沖、本益比、外資持股最新到 20261015（以舊值推論，準確度可能下降）" in describe(snap.meta)
    # 只指定部分路由；週一訊號日的前一個平日是上週五
    meta = write_fixture(tmp_path, signal_date="2026-10-19", target_date="2026-10-20", stale_routes=["finmind_margin"])
    assert meta["chips_freshness"]["status"] == "stale" and meta["chips_freshness"]["stale_routes"] == ["finmind_margin"]
    assert meta["chips_freshness"]["routes"]["finmind_margin"] == "2026-10-16"
    assert meta["chips_freshness"]["routes"]["finmind_per"] == "2026-10-19"
    with pytest.raises(ValueError):
        write_fixture(tmp_path, stale_routes=["finmind_price_adj"])
    with pytest.raises(ValueError):
        write_fixture(tmp_path, chips_status="weird")
    # 美股缺漏也算 degraded（籌碼仍 ok）
    meta = write_fixture(tmp_path, us_status="missing", gate_us_up=None)
    assert meta["degraded"] is True and meta["chips_freshness"]["status"] == "ok"
    # 舊 manifest（沒有 chips_freshness／degraded 欄位）照常可讀、不加註
    meta = write_fixture(tmp_path)
    latest = tmp_path / "predictions" / "latest.json"
    old = json.loads(latest.read_text(encoding="utf-8"))
    old.pop("chips_freshness")
    old.pop("degraded")
    latest.write_text(json.dumps(old, ensure_ascii=False), encoding="utf-8")
    snap = load_latest(tmp_path, expected_signal_date="2026-10-16")
    assert snap.chips_freshness is None
    text = describe(snap.meta)
    assert "籌碼" not in text and text.startswith(MODEL_LABEL)
    _assert_tw(text)


def _fake_predict(monkeypatch, root, chip_dates=None, us_status="ok", partial=None):
    """讓 cmd_predict 不碰中央與正式資料根：假模型、假 head、6 檔假面板、假美股狀態、假推論；回傳 root。"""
    dates = pd.bdate_range("2026-09-01", "2026-10-16")
    ids = ["2330", "2317", "2454", "6488", "3008", "1101"]
    panel = pd.DataFrame({f: 0.1 for f in producer.FEATURES}, index=range(len(ids)))
    panel["stock_id"] = ids
    panel["market"] = ["TWSE", "TWSE", "TWSE", "TPEx", "TWSE", "TWSE"]
    panel["date"] = pd.Timestamp("2026-10-16")
    panel["di"] = len(dates) - 1
    panel["hist_len"] = 500
    panel["raw_close"] = [900.0, 150.0, 1200.0, 80.0, 50.0, 30.0]
    panel["adj_close"] = panel["raw_close"]
    panel["amt20"] = [5e9, 3e9, 2e9, 1e8, 1e7, 5e7]
    panel["limit_up"] = 0.0
    panel["on_us_sp500"] = 0.004
    objects = _objects(**(chip_dates or {}))
    for route, cov in (partial or {}).items():  # 中央標示 PARTIAL 的路由與其 coverage（None = 沒有數字）
        objects[route].update(status="PARTIAL", coverage=cov)
    inputs = {"g": None, "provenance": {"central_head_sha256": "a" * 64, "objects": objects}}
    cur = {"model_id": "preopen_fake", "model_sha256": "b" * 64, "calibrator": {"kind": "identity"}}
    us = {"status": us_status, "expected_us_session": "2026-10-16", "previous_us_session": "2026-10-15",
          "latest_us_session": "2026-10-16" if us_status == "ok" else "2026-10-15", "latest_by_instrument": {}}
    monkeypatch.setattr(producer, "load_active", lambda root_: (object(), cur, {}))
    monkeypatch.setattr(producer.pdata, "read_head",
                        lambda sha=None, central=None: ("a" * 64, {"finmind_price_adj": {"actual_max_date": "2026-10-16"}}, {}))
    monkeypatch.setattr(producer, "build_panel",
                        lambda root_, head_sha=None, next_session=None: (panel.copy(), dates, inputs))
    monkeypatch.setattr(producer.pdata, "us_overnight_status", lambda g, s, t: dict(us))
    monkeypatch.setattr(producer, "predict_raw",
                        lambda model, rows: (np.linspace(0.4, 0.7, len(rows)), np.linspace(-0.01, 0.01, len(rows))))
    return Path(root)


def _only_manifest(root):
    files = sorted((root / "predictions" / "2026-10-16").glob("*.json"))
    assert len(files) == 1
    return json.loads(files[0].read_text(encoding="utf-8"))


def test_predict_manifest_marks_chips_freshness_and_require_flag(tmp_path, monkeypatch, capsys):
    common = ["predict", "--no-latest", "--target-date", "2026-10-19"]
    # 新鮮：chips_freshness=ok、degraded=false；--no-latest 不寫 latest.json
    root = _fake_predict(monkeypatch, tmp_path / "ok")
    producer.main(common + ["--root", str(root)])
    m = _only_manifest(root)
    assert m["chips_freshness"] == {"status": "ok", "signal_date": "2026-10-16",
                                    "routes": {r: "2026-10-16" for r in CHIP_ROUTES},
                                    "route_status": {r: "SUCCESS" for r in CHIP_ROUTES}, "coverage": {r: None for r in CHIP_ROUTES},
                                    "stale_routes": [], "partial_routes": []}
    assert m["degraded"] is False and m["us_overnight"]["status"] == "ok"
    assert m["signal_date"] == "2026-10-16" and m["target_date"] == "2026-10-19" and m["rows"] == 6
    assert not (root / "predictions" / "latest.json").exists()
    assert "chips=ok" in capsys.readouterr().out
    # 過期：預設仍發布，但標示 stale 與 degraded=true，主控台有繁中說明
    root = _fake_predict(monkeypatch, tmp_path / "stale", {"finmind_margin": "2026-10-15", "finmind_shareholding": None})
    producer.main(common + ["--root", str(root)])
    m = _only_manifest(root)
    assert m["chips_freshness"]["status"] == "stale"
    assert m["chips_freshness"]["stale_routes"] == ["finmind_margin", "finmind_shareholding"]
    assert m["chips_freshness"]["routes"] == {**{r: "2026-10-16" for r in CHIP_ROUTES},
                                              "finmind_margin": "2026-10-15", "finmind_shareholding": None}
    assert m["degraded"] is True and m["us_overnight"]["status"] == "ok"
    assert "t 日籌碼未更新：融資券最新到 20261015，外資持股日期未知（以舊值推論，準確度可能下降）" in describe(m)
    out = capsys.readouterr().out
    assert ("t 日籌碼未更新到訊號日 20261016：融資券（finmind_margin）最新到 20261015、外資持股（finmind_shareholding）日期未知；"
            "仍發布但標示 degraded（以舊值推論，準確度可能下降）") in out
    assert "chips=stale" in out
    # 舊 --require-fresh-chips 仍可接受，但不再因 stale 阻擋；日期、degraded 與原預測保留。
    root = _fake_predict(monkeypatch, tmp_path / "stale_legacy_flag", {"finmind_per": "2026-10-15"})
    producer.main(common + ["--root", str(root), "--require-fresh-chips"])
    m = _only_manifest(root)
    assert m["degraded"] is True and m["chips_freshness"]["status"] == "stale"
    assert m["chips_freshness"]["stale_routes"] == ["finmind_per"]
    assert m["chips_freshness"]["routes"]["finmind_per"] == "2026-10-15"
    assert "本益比最新到 20261015" in describe(m)
    frame = pd.read_parquet(root / m["snapshot"])
    np.testing.assert_allclose(frame["p_up_raw"], np.linspace(0.4, 0.7, 6))
    assert not (root / "predictions" / "latest.json").exists()
    assert not (root / "state").exists()
    out = capsys.readouterr().out
    assert "仍發布但標示 degraded" in out and "拒絕發布" not in out
    _assert_tw(describe(m))
    _assert_no_iso(describe(m))
    # --require-fresh-chips 在新鮮時照常發布
    root = _fake_predict(monkeypatch, tmp_path / "fresh_required")
    producer.main(common + ["--root", str(root), "--require-fresh-chips"])
    assert _only_manifest(root)["chips_freshness"]["status"] == "ok"
    # 中央標示不完整（PARTIAL）：照常發布、不算 degraded，manifest 記錄數量，說明文字顯示；--require-fresh-chips 也不阻擋
    root = _fake_predict(monkeypatch, tmp_path / "partial", partial={"finmind_institutional": {"success": 100, "total": 1215}})
    producer.main(common + ["--root", str(root), "--require-fresh-chips"])
    m = _only_manifest(root)
    assert m["chips_freshness"]["status"] == "ok" and m["degraded"] is False
    assert m["chips_freshness"]["partial_routes"] == ["finmind_institutional"]
    assert m["chips_freshness"]["coverage"]["finmind_institutional"] == {"success": 100, "total": 1215}
    assert "t 日籌碼資料不完整（中央回報成功/應有）：法人 100/1215（缺的以舊值推論，準確度可能下降）" in describe(m)
    out = capsys.readouterr().out
    assert "t 日籌碼資料不完整（中央回報成功/應有）：法人（finmind_institutional）100/1215；仍發布（只在說明標示，不阻擋）" in out
    # 美股缺漏＋--allow-missing-us：degraded 來自美股，籌碼仍 ok
    root = _fake_predict(monkeypatch, tmp_path / "us_missing", us_status="missing")
    producer.main(common + ["--root", str(root), "--allow-missing-us"])
    m = _only_manifest(root)
    assert m["degraded"] is True and m["us_overnight"]["status"] == "missing"
    assert m["chips_freshness"]["status"] == "ok" and m["gate_us_up"] is None


@pytest.mark.parametrize("legacy_flags", [(), ("--allow-missing-us",), ("--require-fresh-chips",),
                                         ("--allow-missing-us", "--require-fresh-chips")])
@pytest.mark.parametrize("latest_us_session", ["2026-10-15", None])
def test_predict_missing_us_default_and_legacy_flags_keep_missing_value_flow(
        tmp_path, monkeypatch, legacy_flags, latest_us_session):
    root = _fake_predict(monkeypatch, tmp_path / "isolated", us_status="missing")
    us = {"status": "missing", "expected_us_session": "2026-10-16", "previous_us_session": "2026-10-15",
          "latest_us_session": latest_us_session,
          "latest_by_instrument": {inst: latest_us_session for inst in pdata.US_GATE_INSTRUMENTS}}
    monkeypatch.setattr(producer.pdata, "us_overnight_status", lambda g, s, t: dict(us))
    seen = []
    fake_predict = producer.predict_raw

    def capture_missing_features(model, rows):
        seen.append(rows.copy())
        return fake_predict(model, rows)

    monkeypatch.setattr(producer, "predict_raw", capture_missing_features)
    producer.main(["predict", "--root", str(root), "--no-latest", "--target-date", "2026-10-19", *legacy_flags])
    m = _only_manifest(root)
    assert m["degraded"] is True and m["us_overnight"]["status"] == "missing"
    assert m["us_overnight"]["latest_us_session"] == latest_us_session  # ISO／None 機器語意不變
    assert m["us_overnight"]["expected_us_session"] == "2026-10-16"
    assert m["us_overnight"]["on_us_sp500"] is None and m["gate_us_up"] is None
    assert m["chips_freshness"]["status"] == "ok" and m["rows"] == 6
    assert m["signal_date"] == "2026-10-16" and m["target_date"] == "2026-10-19"
    assert len(seen) == 1 and len(seen[0]) == 6
    assert seen[0][producer.ON_FEATURES].isna().all().all()  # 沿用原 allow-missing-us 缺值路徑
    other = [f for f in producer.FEATURES if f not in producer.ON_FEATURES]
    assert seen[0][other].notna().all().all()
    frame = pd.read_parquet(root / m["snapshot"])
    np.testing.assert_allclose(frame["p_up_raw"], np.linspace(0.4, 0.7, 6))
    np.testing.assert_allclose(frame["exp_ret_raw"], np.linspace(-0.01, 0.01, 6))
    assert list(frame["stock_id"]) == ["2330", "2317", "2454", "6488", "3008", "1101"]
    assert frame["rank"].tolist() == [6, 5, 4, 3, 2, 1]
    assert hashlib.sha256((root / m["snapshot"]).read_bytes()).hexdigest() == m["snapshot_sha256"]
    assert hashlib.sha256((root / m["snapshot_csv"]).read_bytes()).hexdigest() == m["snapshot_csv_sha256"]
    text = describe(m)
    assert "前一晚美股收盤缺漏（以缺值推論，準確度可能下降）" in text
    assert f"美股資料最新日期 {latest_us_session.replace('-', '') if latest_us_session else '未提供'}" in text
    _assert_tw(text)
    _assert_no_iso(text)
    assert not (root / "predictions" / "latest.json").exists()
    assert not (root / "state").exists()  # --no-latest 不碰前瞻帳冊


def test_reader_missing_us_old_manifest_without_optional_dates(tmp_path):
    m = write_fixture(tmp_path, us_status="missing", gate_us_up=None)
    m.pop("chips_freshness")
    m.pop("degraded")
    m["us_overnight"].pop("latest_us_session")
    (tmp_path / "predictions" / "latest.json").write_text(json.dumps(m, ensure_ascii=False), encoding="utf-8")
    snap = load_latest(tmp_path, expected_signal_date="2026-10-16")
    assert len(snap.frame) == m["rows"] and snap.chips_freshness is None
    assert snap.meta["signal_date"] == "2026-10-16"
    text = describe(snap.meta)
    assert "美股資料最新日期 未提供" in text and "籌碼" not in text
    _assert_tw(text)
    _assert_no_iso(text)


def test_legacy_freshness_flags_help_is_nonblocking_chinese(capsys):
    with pytest.raises(SystemExit) as exc:
        producer.main(["--help"])
    assert exc.value.code == 0
    text = capsys.readouterr().out
    assert "--allow-missing-us" in text and "--require-fresh-chips" in text
    assert "相容舊命令" in text and "此旗標不改變發布行為" in text
    assert "此旗標不再阻擋發布" in text
    _assert_tw(text)


def test_predict_target_not_after_signal_still_rejected(tmp_path, monkeypatch):
    root = _fake_predict(monkeypatch, tmp_path / "invalid_target", us_status="missing")
    with pytest.raises(SystemExit) as exc:
        producer.main(["predict", "--root", str(root), "--no-latest", "--target-date", "2026-10-16",
                       "--allow-missing-us", "--require-fresh-chips"])
    assert exc.value.code == "target 2026-10-16 must be after signal 2026-10-16"
    assert not (root / "predictions").exists()
    assert not (root / "state").exists()
