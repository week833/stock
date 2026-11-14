"""Offline tests for the stitch repair of stock_daily_preopen_data.build_prices (synthetic objects, no central I/O)."""
from __future__ import annotations

import json
import sys
from pathlib import Path

import numpy as np
import pandas as pd
import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import stock_daily_preopen_data as pdata  # noqa: E402

DATES = pd.bdate_range("2025-01-06", periods=30)
BASE = 100 * 1.01 ** np.arange(30)  # every ordinary day returns +1%
EX_DAY, EX_FACTOR = 15, 0.97


def _px(stock, idx, close):
    return [{"date": DATES[i].strftime("%Y%m%d"), "stock_id": stock, "open": c, "high": c, "low": c, "close": c,
             "Trading_Volume": 1000, "Trading_money": 1000 * c} for i, c in zip(idx, close)]


def _objects(with_dividend=True):
    """歷史物件＝未調整價；1103 在第 15 日除息（歷史物件漏調整、現行物件已調整）。"""
    raw = {s: BASE.copy() for s in ("1101", "1102", "1103", "1104")}
    raw["1103"] = np.where(np.arange(30) >= EX_DAY, BASE * EX_FACTOR, BASE)
    old, off = [], []
    for s, r in raw.items():
        old += _px(s, range(30), r)
        off += [{"date": DATES[i].strftime("%Y%m%d"), "stock_id": s, "market": "TWSE", "open": r[i], "high": r[i],
                 "low": r[i], "close": r[i], "volume": 1000, "amount": 1e5, "transactions": 10, "name": s} for i in range(30)]
    new = []
    new += _px("1101", [3, 5, 8], 2 * BASE[[3, 5, 8]])  # scattered keys of another vintage inside the gap
    new += _px("1101", range(20, 30), BASE[20:30])  # main current segment
    bad = BASE.copy()
    bad[10] *= 10  # isolated bad current value
    new += _px("1102", range(30), bad)
    new += _px("1103", range(30), BASE * EX_FACTOR)  # correctly back-adjusted: +1% every day
    new += _px("1104", range(30), 1.5 * BASE)  # constant vintage ratio: nothing to repair
    frames = {"hist_price": pd.DataFrame(old), "hist_off": pd.DataFrame(off), "cur_price": pd.DataFrame(new),
              "cur_off": pd.DataFrame(off)}
    if with_dividend:
        frames["dividend"] = pd.DataFrame([{"date": DATES[EX_DAY].strftime("%Y%m%d"), "stock_id": "1103",
                                            "before_price": 100.0, "after_price": 100.0 * EX_FACTOR}])
    return frames


def _routes(frames):
    cur = {"finmind_price_adj": {"sha256": "cur_price"}, "official_market_daily": {"sha256": "cur_off"}}
    if "dividend" in frames:
        cur[pdata.DIVIDEND_ROUTE] = {"sha256": "dividend"}
    hist = {"finmind_price_adj": {"sha256": "hist_price"}, "official_market_daily": {"sha256": "hist_off"}}
    return cur, hist


def _build(monkeypatch, frames):
    monkeypatch.setattr(pdata, "load_object", lambda entry, cols, cache_dir, central=None: frames[entry["sha256"]][cols].copy())
    cur, hist = _routes(frames)
    return pdata.build_prices(cur, hist, None, "c" * 64, "h" * 64), pdata.LAST_STITCH_REPAIR_REPORT


def _r1(panel, stock):
    p = panel[panel["stock_id"] == stock].set_index("date")["adj_close"].reindex(DATES)
    return (p / p.ffill(limit=5).shift(1) - 1).dropna()


def test_repair_fixes_seam_internal_value_and_keeps_latest_level(monkeypatch):
    panel, rep = _build(monkeypatch, _objects())
    assert rep["status"] == "applied" and rep["residual_false"] == 0 and rep["finite_positive_checked"] is True
    got = {(d["stock_id"], d["date"], d["category"], d["confirmed_by"]) for d in rep["detail"]}
    assert got == {("1101", DATES[20].strftime("%Y%m%d"), "seam", "historic"),
                   ("1102", DATES[10].strftime("%Y%m%d"), "current_internal", "historic"),
                   ("1102", DATES[11].strftime("%Y%m%d"), "current_internal", "historic")}
    assert rep["repaired"] == 3 and rep["repaired_stocks"] == 2 and rep["by_category"] == {"current_internal": 2, "seam": 1}
    for s in ("1101", "1102", "1104"):
        assert np.allclose(_r1(panel, s), 0.01, atol=1e-12)
    for s in ("1101", "1102"):  # the latest rows keep the current object's level
        last = panel[panel["stock_id"] == s].iloc[-1]
        assert last["adj_close"] == pytest.approx(BASE[-1], rel=1e-12)
    assert np.allclose(panel[panel["stock_id"] == "1101"][["adj_open", "adj_high", "adj_low"]].to_numpy().T,
                       panel[panel["stock_id"] == "1101"]["adj_close"].to_numpy())
    json.dumps(rep)  # plain JSON types (written into model metadata and manifests)


def test_repair_keeps_current_ex_dividend_adjustment(monkeypatch):
    """現行物件已正確除息、歷史物件漏調整：不得把現行值改成歷史值。"""
    panel, rep = _build(monkeypatch, _objects())
    assert not any(d["stock_id"] == "1103" for d in rep["detail"])
    assert np.allclose(_r1(panel, "1103"), 0.01, atol=1e-12)
    assert rep["last_rescaled_date"] == DATES[19].strftime("%Y%m%d")


def test_repair_is_identity_without_false_returns(monkeypatch):
    frames = _objects()
    for key in ("hist_price", "cur_price", "hist_off", "cur_off"):
        frames[key] = frames[key][frames[key]["stock_id"].isin(["1103", "1104"])]
    panel, rep = _build(monkeypatch, frames)
    monkeypatch.setattr(pdata, "repair_stitch_returns", lambda p, *a, **k: (p, {"status": "applied", "repaired": 0}))
    plain, _ = _build(monkeypatch, frames)
    assert rep["repaired"] == 0 and rep["rescaled_rows"] == 0
    pd.testing.assert_frame_equal(panel, plain)


def test_repair_skipped_without_dividend_route_keeps_stitch(monkeypatch):
    """缺除權息路由只略過修正並警告，不擋流程；結果與原拼接規則相同。"""
    frames = _objects(with_dividend=False)
    with pytest.warns(UserWarning, match="stitch repair"):
        panel, rep = _build(monkeypatch, frames)
    assert rep["status"] == "skipped" and pdata.DIVIDEND_ROUTE in rep["reason"]
    assert _r1(panel, "1101").min() < -0.4  # the presence-wins seam is still there
    assert _r1(panel, "1102").max() > 8


class _NumpyWithBrokenExp:
    """numpy for the module under test, except that exp() returns +inf (a broken repair factor)."""

    def __getattr__(self, name):
        return getattr(np, name)

    @staticmethod
    def exp(x):
        return np.full(np.shape(x), np.inf)


def test_repair_skipped_when_factors_not_finite_positive(monkeypatch):
    """修正係數不是有限正數時只略過修正並警告，面板維持原拼接（與缺除權息路由時相同）。"""
    monkeypatch.setattr(pdata, "np", _NumpyWithBrokenExp())
    with pytest.warns(UserWarning, match="stitch repair"):
        panel, rep = _build(monkeypatch, _objects())
    monkeypatch.setattr(pdata, "np", np)
    with pytest.warns(UserWarning, match="stitch repair"):
        plain, _ = _build(monkeypatch, _objects(with_dividend=False))
    assert rep["status"] == "skipped" and "finite and positive" in rep["reason"]
    pd.testing.assert_frame_equal(panel, plain)


def _load_inputs(monkeypatch, frames):
    cur, hist = _routes(frames)
    for r in ("finmind_institutional", "finmind_margin", "finmind_day_trading", "finmind_per", "finmind_shareholding",
              "finmind_month_revenue", "finmind_stock_info", "global_market_history"):
        cur[r] = {"sha256": r, "status": "SUCCESS"}
    monkeypatch.setattr(pdata, "load_object", lambda entry, cols, cache_dir, central=None: frames[entry["sha256"]][cols].copy())
    monkeypatch.setattr(pdata, "read_head", lambda sha=None, central=None: (
        ("h" * 64, hist, {}) if sha == pdata.HISTORIC_HEAD else ("c" * 64, cur, {"published_at": "x", "run_id": "y"})))
    monkeypatch.setattr(pdata, "build_chips", lambda routes, cache_dir, sha: None)
    monkeypatch.setattr(pdata, "build_misc", lambda routes, cache_dir, sha: (None, None, None))
    return pdata.load_inputs(None)["provenance"]


def test_load_inputs_records_stitch_repair_in_provenance(monkeypatch):
    prov = _load_inputs(monkeypatch, _objects())
    assert prov["stitch_repair"]["repaired"] == 3 and prov["stitch_repair"]["dividend_object_sha256"] == "dividend"
    assert prov["objects"][pdata.DIVIDEND_ROUTE]["sha256"] == "dividend"  # listed with the other input objects
    json.dumps(prov)


def test_load_inputs_without_dividend_route_still_loads(monkeypatch):
    """缺除權息路由時 load_inputs 照常完成：物件清單不列它，修正狀態為 skipped。"""
    with pytest.warns(UserWarning, match="stitch repair"):
        prov = _load_inputs(monkeypatch, _objects(with_dividend=False))
    assert pdata.DIVIDEND_ROUTE not in prov["objects"] and prov["stitch_repair"]["status"] == "skipped"
    json.dumps(prov)
