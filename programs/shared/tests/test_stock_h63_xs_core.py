"""Offline tests of the U7 pure computation (stock_h63_xs_core; ADR-0009).

Row-for-row parity with the acceptance oracle (u7_reference_oracle.py, tolerance 1e-9) is checked by the
acceptance receipt; these tests pin the rules on small synthetic data.
"""
from __future__ import annotations

import re
import sys
from pathlib import Path

import numpy as np
import pandas as pd
import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import stock_h63_xs_core as core  # noqa: E402

def test_recipe_is_frozen():
    # Changing any recipe constant is a new recipe: new authorization, acceptance and pre-registration.
    assert core.RECIPE["name"] == "u7_b3cqr252_v1"
    assert core.RECIPE_SHA256 == PINNED_RECIPE_SHA256
    assert core.RECIPE["slope"]["gated"] is True and core.RECIPE["prices"]["seam_date"] == "2025-12-31"
    assert core.FEATURES == ("f_ivol60", "f_max5", "f_hi52", "f_rev_yoy", "f_rev_high12", "f_ep", "f_bm")


PINNED_RECIPE_SHA256 = "a2c4523c38b11315f35b58e96648e4d79e38f39f094acb50a09c338e5983b37e"


def test_recipe_literal():
    assert re.fullmatch(r"[0-9a-f]{64}", core.RECIPE_SHA256)


def test_rank_against_members_and_inserted_rows():
    values = np.array([1.0, 2.0, 3.0, 2.5, np.nan])
    member = np.array([True, True, True, False, True])
    out = core.rank_against(values, member, [np.arange(5)])
    assert out[0] == -1.0 and out[2] == 1.0 and out[1] == 0.0
    # non-member inserted among n + 1 = 4 positions: rank 3 of 4 -> 2 * 2 / 3 - 1
    assert out[3] == pytest.approx(2.0 * 2 / 3 - 1.0)
    assert out[4] == 0.0  # missing -> 0


def test_centre_scores_uses_member_mean_only():
    score = np.array([0.5, -0.5, 0.9, 0.1])
    member = np.array([True, True, False, True])
    codes = np.array([0, 0, 0, 1])
    mean, centred = core.centre_scores(score, member, codes, 2)
    assert mean[0] == pytest.approx(0.0) and mean[1] == pytest.approx(0.1)
    assert centred[2] == pytest.approx(0.9) and centred[3] == pytest.approx(0.0)


def test_fit_slope_minimizes_the_penalized_objective_and_clips_negative():
    rng = np.random.default_rng(7)
    z = rng.normal(size=4000)
    y = (rng.random(4000) < core.sigmoid(1.3 * z)).astype(float)
    w = np.ones_like(z)
    fit = core.fit_slope(z, y, w)
    sw = w.sum()

    def objective(b):
        t = b * z
        return np.dot(w, np.logaddexp(0.0, t) - y * t) / sw + 0.5 / (core.SLOPE_C * sw) * b * b

    grid = np.linspace(fit["b"] - 0.05, fit["b"] + 0.05, 101)
    assert objective(fit["b"]) <= min(objective(g) for g in grid) + 1e-12
    assert 1.1 < fit["b"] < 1.5
    neg = core.fit_slope(z, 1.0 - y, w)
    assert neg["b"] == 0.0 and neg["b_raw"] < 0


def test_statutory_revenue_availability_postpones_and_uses_late_codes_from_2026():
    sessions = pd.bdate_range("2025-12-01", "2026-03-31")
    rule = core.StatutoryRevenueAvailability(sessions, late_codes=["2880"])
    # 2025-12 revenue: deadline 2026-01-10 (Saturday) -> first session 2026-01-12 -> usable from 2026-01-13
    got = rule.available_at(np.array(["2330"]), pd.PeriodIndex(["2025-12"], freq="M"))
    assert pd.Timestamp(got[0]) == pd.Timestamp("2026-01-13")
    # a late code in 2026: 2026-01 revenue deadline 2026-02-15 (Sunday) -> 2026-02-16 -> usable 2026-02-17
    got = rule.available_at(np.array(["2880"]), pd.PeriodIndex(["2026-01"], freq="M"))
    assert pd.Timestamp(got[0]) == pd.Timestamp("2026-02-17")
    # before 2026 the late code keeps the 10th
    got = rule.available_at(np.array(["2880"]), pd.PeriodIndex(["2025-12"], freq="M"))
    assert pd.Timestamp(got[0]) == pd.Timestamp("2026-01-13")


def test_late_revenue_codes_union():
    codes = core.late_revenue_codes(["2330", "2809", "2888"], {"2816": "monthly_revenue_insurance_115",
                                                               "2885": "monthly_revenue_exception_unconfirmed",
                                                               "1101": "monthly_revenue_general", "91234": "secondary_foreign"})
    assert {"2809", "2888", "2816", "2885", "9136"} <= set(codes)
    assert "1101" not in codes and "91234" not in codes and "2330" not in codes


def test_sigma63_floor_and_median_fill():
    vol = np.array([0.4, np.nan, 0.01, 0.2])
    member = np.array([True, True, True, True])
    codes = np.zeros(4, dtype=np.int64)
    sig = core.sigma63(vol, member, codes, 1)
    assert sig[0] == pytest.approx(0.4 * np.sqrt(63 / 252))
    assert sig[1] == pytest.approx(0.2 * np.sqrt(63 / 252))  # median of 0.4, 0.01, 0.2
    assert sig[2] == core.SIGMA_FLOOR


def _stitch_inputs():
    old = pd.DataFrame({"stock_id": ["1101"] * 8 + ["9999"] * 2,
                        "date": pd.to_datetime(["2025-12-22", "2025-12-23", "2025-12-24", "2025-12-25", "2025-12-26",
                                                "2025-12-29", "2025-12-30", "2025-12-31", "2025-12-30", "2025-12-31"]),
                        "open": 10.0, "high": 10.0, "low": 10.0, "close": 10.0, "Trading_Volume": 1.0,
                        "Trading_money": 1.0})
    new = pd.DataFrame({"stock_id": ["1101"] * 4 + ["2222"],
                        "date": pd.to_datetime(["2025-12-30", "2025-12-31", "2026-01-02", "2026-01-05", "2026-01-02"]),
                        "open": 20.0, "high": 20.0, "low": 20.0, "close": [20.0, 20.0, 21.0, 22.0, 5.0],
                        "Trading_Volume": 1.0, "Trading_money": 1.0})
    return old, new


def test_stitch_scales_the_supplement_and_switches_after_the_seam():
    old, new = _stitch_inputs()
    log = {}
    px = core.stitch_prices(old, new, "2025-12-31", "2021-10-04", log)
    a = px[px["stock_id"] == "1101"].set_index("date")["close"]
    assert a[pd.Timestamp("2025-12-22")] == 20.0  # supplement scaled by the overlap ratio 2.0
    assert a[pd.Timestamp("2026-01-02")] == 21.0 and a[pd.Timestamp("2026-01-05")] == 22.0
    assert (px[px["stock_id"] == "9999"]["close"] == 10.0).all()  # no overlap -> 1.0 (delisted stays)
    assert px[px["stock_id"] == "2222"]["date"].tolist() == [pd.Timestamp("2026-01-02")]  # new listing: head rows only
    assert log["stitch_scale_ne1_stocks"] == 1


def test_mondrian_tables_and_band_pool_prefix():
    rng = np.random.default_rng(3)
    n = 6000
    P = pd.DataFrame({"eligible": True, "label_ok": True, "r63": rng.normal(0, 0.1, n),
                      "label_end": pd.Timestamp("2025-01-01") + pd.to_timedelta(np.arange(n) % 300, unit="D")})
    sig = rng.uniform(0.05, 0.3, n)
    pool = core.BandPool(P, sig)
    early = pool.table_at("2025-01-02")
    assert early["status"] == "pool_insufficient"
    table = pool.table_at("2026-01-01")
    assert table["status"] == "ok" and table["pool"] == n and table["tab"].shape == (5, 3)
    q, bucket = core.apply_table(table, np.array([0.06, 0.29]))
    assert bucket.tolist() == [0, 4] and (q[:, 0] <= q[:, 1]).all() and (q[:, 1] <= q[:, 2]).all()


def test_relative_strength_thresholds():
    out = core.relative_strength(np.array([0.55, 0.5499, 0.45, 0.4501, 0.7]), np.array([True, True, True, True, False]))
    assert out.tolist() == ["relatively_strong", "neutral", "relatively_weak", "neutral", "out_of_scope"]


def test_slope_guard():
    assert core.slope_guard(0.5) == "ok" and core.slope_guard(2.0) == "ok"
    assert core.slope_guard(0.49) == "out_of_range" and core.slope_guard(2.01) == "out_of_range"


def test_no_timestamp_timedelta_arithmetic():
    # numpy 2.5 warns "will raise an error in the future" on Timestamp +/- pd.Timedelta; use np.timedelta64.
    for name in ("stock_h63_xs_core.py", "stock_h63_xs_data.py", "stock_h63_xs_ledger.py", "stock_h63_xs.py",
                 "stock_h63_xs_reader.py"):
        text = (Path(__file__).resolve().parents[1] / name).read_text(encoding="utf-8")
        assert not re.search(r"[+-]\s*pd\.Timedelta\(", text), name
