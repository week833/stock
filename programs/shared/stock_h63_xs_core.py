"""U7 three-month (H63) cross-sectional rank model: pure computation, no IO (ADR-0009).

The arithmetic is the float64 path of the acceptance oracle u7_reference_oracle.py (oracle version
u7-h63-reference-oracle-v1, sha256 a94609fa...), itself copied from the sealed dates_v1 module
tw_single_stock_three_month_xs.py (1f70ed0a...) and the 2026-11-01 study (B3 band, CQR252).  Every function here
must stay row-for-row equal to the oracle (tolerance 1e-9); change RECIPE and re-run the acceptance otherwise.

Machine fields are datetime64 / ISO; people see YYYYMMDD through date_format.display_day.
"""
from __future__ import annotations

import hashlib
import json
import math
import re
from typing import Any, Dict, List, Optional, Sequence, Tuple

import numpy as np
import pandas as pd

HORIZON = 63
FEATURES: Tuple[str, ...] = ("f_ivol60", "f_max5", "f_hi52", "f_rev_yoy", "f_rev_high12", "f_ep", "f_bm")
SIGNS: Dict[str, int] = {"f_ivol60": -1, "f_max5": -1, "f_hi52": +1, "f_rev_yoy": +1, "f_rev_high12": +1,
                         "f_ep": +1, "f_bm": +1}
K = len(FEATURES)
CODE_PATTERN = r"[1-9]\d{3}"
ELIG_BOARDS = ("TWSE", "TPEx")
ELIG_MIN_HIST = 126
ELIG_MIN_VALUE = 1e7
LABEL_RANGE = (-0.95, 3.0)
SLOPE_DATE_STEP = 3
SLOPE_PHASE_ANCHOR = "2026-01-06"
SLOPE_C = 1e6
SLOPE_NEWTON_TOL = 1e-13
SLOPE_GATED = True
SLOPE_MIN, SLOPE_MAX = 0.5, 2.0
SIGMA_FLOOR = 0.03
PROBABILITY_EPSILON = 1e-6
STRONG_AT, WEAK_AT = 0.55, 0.45
REVENUE_DEADLINE_DAY = 10
REVENUE_LATE_DEADLINE_DAY = 15
REVENUE_LATE_FROM_YEAR = 2026
REVENUE_STALE_DAYS = 75
REVENUE_LATE_FALLBACK_CODES = (
    "2801", "2812", "2816", "2820", "2832", "2834", "2836", "2838", "2845", "2849", "2850", "2851", "2852", "2855",
    "2880", "2881", "2882", "2883", "2884", "2885", "2886", "2887", "2889", "2890", "2891", "2892", "2897", "2905",
    "5864", "5876", "5878", "5880", "6005", "6015", "6016", "6020", "6021", "6023", "6024", "6026", "6028", "6854",
    "6924", "7819", "7823", "7827", "9103", "9105", "9110", "9136")
REVENUE_LATE_ROUTE_CLASSES = ("monthly_revenue_insurance_115", "monthly_revenue_exception_unconfirmed",
                              "monthly_revenue_financial_unresolved", "secondary_foreign", "foreign_unresolved")
MIN_ELIGIBLE_COUNT = 696
MIN_COMPONENT_COVERAGE = {"f_ivol60": 0.80, "f_max5": 0.80, "f_hi52": 0.80, "f_rev_yoy": 0.76,
                          "f_rev_high12": 0.76, "f_ep": 0.79, "f_bm": 0.79}
MIN_REVENUE_CURRENT_SHARE = 0.79
REVENUE_FRESHNESS_GRACE_SESSIONS = 2
BAND_TAUS = (0.10, 0.50, 0.90)
BAND_VOL_BUCKETS = 5
BAND_MIN_POOL_ROWS = 5000
B3_WEIGHT_VOL60 = 0.5
VOL252_WINDOW, VOL252_MIN = 252, 126
CQR_WINDOW, CQR_LABEL_LAG, CQR_LEVEL, CQR_MIN_ROWS = 252, 63, 0.80, 2000
BAND_NOMINAL = 0.80
PRICE_FIRST = "2021-10-04"
SEAM_DATE = "2025-12-31"
STITCH_RULE_ID = "u7_supplement_seam_v1"
LATE_CODES_RULE_ID = "fallback50_or_28xx_or_route_classes_v1"
PROBABILITY_SEMANTICS = "beats_cross_sectional_median_63_session_return"
LABEL_RULE = "63rd_own_price_row"

# The frozen recipe: changing any value is a new recipe (new authorization, acceptance and pre-registration).
RECIPE: Dict[str, Any] = {
    "name": "u7_b3cqr252_v1",
    "horizon_sessions": HORIZON,
    "features": list(FEATURES), "signs": dict(SIGNS),
    "code_pattern": CODE_PATTERN, "eligible_boards": list(ELIG_BOARDS),
    "eligible_min_history_rows": ELIG_MIN_HIST, "eligible_min_money60": ELIG_MIN_VALUE,
    "label_range": list(LABEL_RANGE), "label_rule": LABEL_RULE,
    "probability_semantics": PROBABILITY_SEMANTICS,
    "slope": {"date_step": SLOPE_DATE_STEP, "phase_anchor": SLOPE_PHASE_ANCHOR, "C": SLOPE_C,
              "newton_tol": SLOPE_NEWTON_TOL, "gated": SLOPE_GATED, "b_min": SLOPE_MIN, "b_max": SLOPE_MAX,
              "refit_every_sessions": HORIZON},
    "probability_epsilon": PROBABILITY_EPSILON, "relative_strength": {"strong_at": STRONG_AT, "weak_at": WEAK_AT},
    "revenue": {"deadline_day": REVENUE_DEADLINE_DAY, "late_deadline_day": REVENUE_LATE_DEADLINE_DAY,
                "late_from_year": REVENUE_LATE_FROM_YEAR, "stale_days": REVENUE_STALE_DAYS,
                "late_codes_rule": LATE_CODES_RULE_ID, "late_fallback_codes": list(REVENUE_LATE_FALLBACK_CODES),
                "late_route_classes": list(REVENUE_LATE_ROUTE_CLASSES)},
    "daily_gate": {"min_eligible": MIN_ELIGIBLE_COUNT, "min_coverage": dict(MIN_COMPONENT_COVERAGE),
                   "min_revenue_current_share": MIN_REVENUE_CURRENT_SHARE,
                   "revenue_grace_sessions": REVENUE_FRESHNESS_GRACE_SESSIONS},
    "band": {"method": "B3+CQR252", "nominal": BAND_NOMINAL, "taus": list(BAND_TAUS), "buckets": BAND_VOL_BUCKETS,
             "min_pool_rows": BAND_MIN_POOL_ROWS, "sigma_floor": SIGMA_FLOOR, "b3_weight_vol60": B3_WEIGHT_VOL60,
             "vol252_window": VOL252_WINDOW, "vol252_min": VOL252_MIN,
             "cqr": {"window": CQR_WINDOW, "label_lag": CQR_LABEL_LAG, "level": CQR_LEVEL,
                     "min_rows": CQR_MIN_ROWS, "sigma": "B1"}},
    "prices": {"first_date": PRICE_FIRST, "seam_date": SEAM_DATE, "stitch_rule": STITCH_RULE_ID,
               "own_rows": "every board"},
}


def canonical_json(value: Any) -> bytes:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False).encode("utf-8")


RECIPE_SHA256 = hashlib.sha256(canonical_json(RECIPE)).hexdigest()


class H63XsComputationError(RuntimeError):
    def __init__(self, code: str, detail: str = ""):
        super().__init__(code + (": " + detail if detail else ""))
        self.code = code
        self.detail = detail


# ------------------------------------------------------------------------------------------------------------------
# small helpers
# ------------------------------------------------------------------------------------------------------------------
def four_mask(values: Any) -> np.ndarray:
    return pd.Series(values).astype(str).str.fullmatch(CODE_PATTERN).to_numpy(bool)


def to_date(values: pd.Series) -> pd.Series:
    """YYYYMMDD text (central objects) or date / datetime values -> datetime64[ns] at midnight."""
    s = pd.Series(values)
    if pd.api.types.is_datetime64_any_dtype(s):
        return pd.to_datetime(s).dt.normalize()
    return pd.to_datetime(s.astype(str), format="%Y%m%d", errors="coerce")


def day64(value: Any) -> np.datetime64:
    return np.datetime64(pd.Timestamp(value).normalize(), "ns")


def sigmoid(x: Any) -> np.ndarray:
    return 1.0 / (1.0 + np.exp(-np.asarray(x, float)))


# ------------------------------------------------------------------------------------------------------------------
# inputs -> normalized frames (oracle load_px / build_panel spelling)
# ------------------------------------------------------------------------------------------------------------------
PRICE_NUMERIC = ("open", "high", "low", "close", "Trading_Volume", "Trading_money")


def prepare_prices(df: pd.DataFrame) -> pd.DataFrame:
    df = df.copy()
    df["stock_id"] = df["stock_id"].astype(str)
    df = df[four_mask(df["stock_id"])].copy()
    df["date"] = to_date(df["date"])
    for c in PRICE_NUMERIC:
        df[c] = pd.to_numeric(df[c], errors="coerce").astype(float)
    return df.drop_duplicates(["stock_id", "date"], keep="last")


def stitch_prices(old: pd.DataFrame, new: pd.DataFrame, seam_date: Any = SEAM_DATE,
                  first_date: Any = PRICE_FIRST, log: Optional[Dict[str, Any]] = None) -> pd.DataFrame:
    """Supplement (old) rows up to seam_date scaled per stock by the median new/old close ratio of the last 5
    overlapping sessions <= seam_date, then the current head (new) rows after it (oracle stitch_research)."""
    seam = pd.Timestamp(seam_date)
    first = pd.Timestamp(first_date)
    ov = old[old["date"] <= seam].merge(new[["stock_id", "date", "close"]], on=["stock_id", "date"],
                                        suffixes=("", "_new"))
    ov = ov[(ov["close"] > 0) & (ov["close_new"] > 0)].sort_values(["stock_id", "date"])
    ov["ratio"] = ov["close_new"] / ov["close"]
    last5 = ov.groupby("stock_id").tail(5)
    scale = last5.groupby("stock_id")["ratio"].median()
    if log is not None:
        spread = last5.groupby("stock_id")["ratio"].agg(lambda r: float(r.max() / r.min() - 1.0))
        log["stitch_scale_ne1_stocks"] = int((scale.sub(1).abs() > 1e-9).sum())
        log["stitch_scale_range"] = [float(scale.min()), float(scale.max())] if len(scale) else [None, None]
        log["stitch_scale_quantiles"] = ([float(x) for x in scale.quantile([0.0, 0.01, 0.5, 0.99, 1.0])]
                                         if len(scale) else [])
        log["stitch_last5_not_constant"] = sorted(str(s) for s in spread.index[spread.to_numpy() >= 1e-6])
    pre = old[(old["date"] <= seam) & (old["date"] >= first)].merge(scale.rename("scale"), on="stock_id", how="left")
    pre["scale"] = pre["scale"].fillna(1.0)
    for c in ["open", "high", "low", "close"]:
        pre[c] = pre[c] * pre["scale"]
    pre = pre.drop(columns=["scale"])
    post = new[new["date"] > seam]
    px = pd.concat([pre, post], ignore_index=True).sort_values(["stock_id", "date"], kind="mergesort")
    return px.drop_duplicates(["stock_id", "date"], keep="last").reset_index(drop=True)


def prepare_board(frames: Sequence[pd.DataFrame]) -> pd.DataFrame:
    """official_market_daily rows (older frames first; later frames win on equal keys)."""
    offc = ["date", "stock_id", "market", "close"]
    parts = []
    for frame in frames:
        part = frame[offc].copy()
        part["date"] = to_date(part["date"])          # per frame: the supplement stores dates, the head text
        parts.append(part)
    off = pd.concat(parts, ignore_index=True)
    off["stock_id"] = off["stock_id"].astype(str)
    off = off[four_mask(off["stock_id"])].copy()
    return off.drop_duplicates(["stock_id", "date"], keep="last")


def prepare_revenue(rv: pd.DataFrame) -> pd.DataFrame:
    rv = rv.copy()
    rv["stock_id"] = rv["stock_id"].astype(str)
    rv = rv[four_mask(rv["stock_id"])].copy()
    for c in ["revenue", "revenue_year", "revenue_month"]:
        rv[c] = pd.to_numeric(rv[c], errors="coerce")
    rv = rv.dropna(subset=["revenue_year", "revenue_month"])
    rv = rv[rv["revenue_month"].between(1, 12)]
    rv["period"] = pd.PeriodIndex.from_fields(year=rv["revenue_year"].astype(int),
                                              month=rv["revenue_month"].astype(int), freq="M")
    return rv.drop_duplicates(["stock_id", "period"], keep="last")[["stock_id", "period", "revenue"]]


def prepare_per(per: pd.DataFrame) -> pd.DataFrame:
    per = per[["date", "stock_id", "per", "pbr"]].copy()
    per["stock_id"] = per["stock_id"].astype(str)
    per = per[four_mask(per["stock_id"])].copy()
    per["date"] = to_date(per["date"])
    for c in ["per", "pbr"]:
        per[c] = pd.to_numeric(per[c], errors="coerce")
    return per.drop_duplicates(["stock_id", "date"], keep="last").rename(columns={"per": "PER", "pbr": "PBR"})


def late_revenue_codes(revenue_codes: Sequence[str], route_classes: Optional[Dict[str, str]] = None
                       ) -> List[str]:
    """Late-filer union: fixed fallback list, every 28xx code in the revenue data and the late route classes."""
    codes = set(REVENUE_LATE_FALLBACK_CODES) | {s for s in map(str, revenue_codes) if s.startswith("28")}
    for code, rule_class in (route_classes or {}).items():
        if str(rule_class) in REVENUE_LATE_ROUTE_CLASSES and re.fullmatch(CODE_PATTERN, str(code)):
            codes.add(str(code))
    return sorted(codes)


# ------------------------------------------------------------------------------------------------------------------
# U7 core (dates_v1 tw_single_stock_three_month_xs.py)
# ------------------------------------------------------------------------------------------------------------------
class StatutoryRevenueAvailability:
    """Deadline = 10th of M+1 (15th for late codes from 2026), postponed to the first session on/after it, usable
    from the next session; NaT when that session is not in the list."""

    def __init__(self, sessions: Sequence[Any], late_codes: Sequence[str] = (),
                 late_from_year: int = REVENUE_LATE_FROM_YEAR):
        self.sessions = pd.DatetimeIndex(pd.to_datetime(pd.Index(sessions))).normalize().unique().sort_values()
        self.late_codes = frozenset(str(code) for code in late_codes)
        self.late_from_year = int(late_from_year)

    def deadlines(self, stock_ids: np.ndarray, periods: Any) -> np.ndarray:
        per = pd.PeriodIndex(periods, freq="M")
        following = (per + 1).to_timestamp()
        late = np.isin(np.asarray(stock_ids, dtype=object), list(self.late_codes)) & (per.year >= self.late_from_year)
        day = np.where(late, REVENUE_LATE_DEADLINE_DAY, REVENUE_DEADLINE_DAY)
        return (following + pd.to_timedelta(day - 1, unit="D")).to_numpy("datetime64[ns]")

    def available_at(self, stock_ids: np.ndarray, periods: Any) -> np.ndarray:
        deadline = self.deadlines(stock_ids, periods)
        ses = self.sessions.to_numpy("datetime64[ns]")
        out = np.full(len(deadline), np.datetime64("NaT", "ns"), dtype="datetime64[ns]")
        if len(ses) == 0:
            return out
        pos = np.searchsorted(ses, deadline, side="left")
        ok = pos < len(ses)
        post = ses[np.minimum(pos, len(ses) - 1)]
        nxt = np.searchsorted(ses, post, side="right")
        ok &= nxt < len(ses)
        out[ok] = ses[nxt[ok]]
        return out


def slope_phase_for(sessions: pd.DatetimeIndex, anchor: str = SLOPE_PHASE_ANCHOR) -> np.ndarray:
    sessions = pd.DatetimeIndex(sessions)
    anchor_ts = pd.Timestamp(anchor)
    if anchor_ts not in sessions:
        raise H63XsComputationError("h63xs_phase_anchor_missing", "slope phase anchor 20260106 is not a session")
    return (np.arange(len(sessions)) - int(sessions.get_loc(anchor_ts))) % SLOPE_DATE_STEP


def market_return(px4: pd.DataFrame) -> pd.Series:
    """Equal-weight mean daily return over TWSE/TPEx 4-digit stocks (ffilled previous close, money > 0, +-30%)."""
    close = px4.pivot(index="date", columns="stock_id", values="close").astype(float)
    money = px4.pivot(index="date", columns="stock_id", values="money").astype(float)
    main = px4.assign(_m=px4["main"].astype("int8")).pivot(index="date", columns="stock_id", values="_m")
    main = main.reindex(index=close.index, columns=close.columns).eq(1)
    ret = close / close.ffill().shift(1) - 1.0
    ret = ret.where(close.notna() & (money > 0)).clip(-0.3, 0.3)
    ever = main.any(axis=0).to_numpy()
    cols = close.columns[ever]
    return ret[cols].where(main[cols]).mean(axis=1)


def _group_roll(values: np.ndarray, groups: np.ndarray, window: int, min_periods: int, how: str) -> np.ndarray:
    series = pd.Series(values)
    rolled = getattr(series.groupby(groups, sort=False).rolling(window, min_periods=min_periods), how)()
    return rolled.reset_index(level=0, drop=True).sort_index().to_numpy(float)


def _max5(r1: np.ndarray, rows: np.ndarray, chunk: int = 200000) -> np.ndarray:
    out = np.full(len(rows), np.nan)
    offsets = np.arange(-20, 1)
    for start in range(0, len(rows), chunk):
        idx = rows[start:start + chunk]
        w = r1[idx[:, None] + offsets[None, :]]
        fin = np.isfinite(w)
        valid = fin.sum(axis=1)
        srt = np.sort(np.where(fin, w, -np.inf), axis=1)
        top5 = srt[:, -5:]
        m5 = np.where(np.isfinite(top5).all(axis=1), top5.mean(axis=1), np.nan)
        out[start:start + chunk] = np.where(valid >= 15, m5, np.nan)
    return out


PRICE_FEATURE_NAMES = ("f_vol60", "f_hi52", "f_max5", "f_ivol60", "f_money60", "f_nhist", "r63")


def price_features(px4: pd.DataFrame, mret: pd.Series, chunk_stocks: int = 400) -> Dict[str, np.ndarray]:
    """Own-row features of every row of px4 (sorted by stock_id, date): vol60, hi52, MAX5, IVOL60, money60, nhist,
    r63 and label_end (63rd own row ahead)."""
    sid = px4["stock_id"].to_numpy(dtype=object)
    n = len(sid)
    dates = px4["date"].to_numpy("datetime64[ns]")
    close = px4["close"].to_numpy(float)
    money = px4["money"].to_numpy(float)
    m_all = mret.reindex(pd.DatetimeIndex(dates)).to_numpy(float)
    out = {name: np.full(n, np.nan) for name in PRICE_FEATURE_NAMES}
    out["label_end"] = np.full(n, np.datetime64("NaT", "ns"), dtype="datetime64[ns]")
    starts = np.flatnonzero(np.r_[True, sid[1:] != sid[:-1]])
    ends = np.r_[starts[1:], n]
    for k in range(0, len(starts), chunk_stocks):
        k2 = min(k + chunk_stocks, len(starts))
        a, b = int(starts[k]), int(ends[k2 - 1])
        lengths = ends[k:k2] - starts[k:k2]
        g_sid = sid[a:b]
        local = np.arange(b - a)
        pos = local - np.repeat(starts[k:k2] - a, lengths)
        group_end = np.repeat(ends[k:k2] - a, lengths)
        cs = pd.Series(close[a:b])
        c = cs.where(cs > 0)
        grp = c.groupby(g_sid, sort=False)
        r1 = (c / grp.shift(1) - 1.0).to_numpy(float)
        cv = c.to_numpy(float)
        out["f_vol60"][a:b] = _group_roll(r1, g_sid, 60, 40, "std") * math.sqrt(252)
        out["f_hi52"][a:b] = cv / _group_roll(cv, g_sid, 252, 126, "max") - 1.0
        rows = np.flatnonzero(pos >= 20)
        if len(rows):
            out["f_max5"][a + rows] = _max5(r1, rows)
        m = m_all[a:b]
        with np.errstate(all="ignore"):
            xm = r1 + 0 * m
            ym = m + 0 * r1
            mean_xy = _group_roll(xm * ym, g_sid, 60, 40, "mean")
            mean_x = _group_roll(xm, g_sid, 60, 40, "mean")
            mean_y = _group_roll(ym, g_sid, 60, 40, "mean")
            cnt = _group_roll(pd.notna(xm + ym).astype(np.float64), g_sid, 60, 0, "sum")
            cov = (mean_xy - mean_x * mean_y) * (cnt / (cnt - 1))
            vm = _group_roll(m, g_sid, 60, 40, "var")
            vr = _group_roll(r1, g_sid, 60, 40, "var")
            resid = pd.Series(vr - cov ** 2 / vm).clip(lower=0).to_numpy(float)
            out["f_ivol60"][a:b] = np.sqrt(resid) * math.sqrt(252)
            mon = money[a:b]
            out["f_money60"][a:b] = _group_roll(np.where(mon > 0, mon, np.nan), g_sid, 60, 40, "mean")
            out["f_nhist"][a:b] = pos.astype(float)
            fwd = local + HORIZON
            ok = fwd < group_end
            c_fwd = np.full(b - a, np.nan)
            c_fwd[ok] = cv[fwd[ok]]
            out["r63"][a:b] = c_fwd / cv - 1.0
            le = np.full(b - a, np.datetime64("NaT", "ns"), dtype="datetime64[ns]")
            le[ok] = dates[a:b][fwd[ok]]
            out["label_end"][a:b] = le
    return out


def revenue_table(revenue: pd.DataFrame, rule: StatutoryRevenueAvailability) -> pd.DataFrame:
    """Single-month YoY and revenue vs the previous-11-month high per REPORTED (stock, month), with availability."""
    r = revenue[["stock_id", "period", "revenue"]].copy()
    r["revenue"] = pd.to_numeric(r["revenue"], errors="coerce").astype(float)
    wide = r.pivot(index="period", columns="stock_id", values="revenue")
    full = pd.period_range(wide.index.min(), wide.index.max(), freq="M")
    reported = r.assign(_one=1.0).pivot(index="period", columns="stock_id", values="_one").reindex(
        index=full, columns=wide.columns).notna()
    wide = wide.reindex(full).where(lambda x: x > 0)
    yoy = wide / wide.shift(12) - 1.0
    high = wide / wide.shift(1).rolling(11, min_periods=11).max() - 1.0
    codes = wide.columns.to_numpy()
    n_p, n_c = len(full), len(codes)
    frame = pd.DataFrame({"stock_id": np.tile(codes, n_p), "period": np.repeat(full.to_numpy(), n_c),
                          "f_rev_yoy": yoy.to_numpy(float).ravel(), "f_rev_high12": high.to_numpy(float).ravel()})
    frame = frame.loc[reported.to_numpy(bool).ravel()].reset_index(drop=True)
    frame["avail"] = rule.available_at(frame["stock_id"].to_numpy(object), pd.PeriodIndex(frame["period"], freq="M"))
    return frame


def attach_revenue(rows: pd.DataFrame, revtab: pd.DataFrame, stale_days: int) -> pd.DataFrame:
    left = pd.DataFrame({"stock_id": rows["stock_id"].to_numpy(object),
                         "decision_date": pd.to_datetime(rows["decision_date"]).to_numpy("datetime64[ns]"),
                         "_i": np.arange(len(rows))})
    right = revtab.dropna(subset=["avail"])[["stock_id", "avail", "period", "f_rev_yoy", "f_rev_high12"]].copy()
    right["avail"] = right["avail"].astype("datetime64[ns]")
    right = right.sort_values(["avail", "period"], kind="mergesort")
    m = pd.merge_asof(left.sort_values("decision_date", kind="mergesort"), right, left_on="decision_date",
                      right_on="avail", by="stock_id", direction="backward").sort_values("_i")
    yoy = m["f_rev_yoy"].to_numpy(float)
    high = m["f_rev_high12"].to_numpy(float)
    avail = m["avail"].to_numpy("datetime64[ns]")
    age = (m["decision_date"].to_numpy("datetime64[ns]") - avail) / np.timedelta64(1, "D")
    stale = np.isfinite(age) & (age > float(stale_days))
    out = pd.DataFrame(index=rows.index)
    out["f_rev_yoy"] = np.where(stale, np.nan, yoy)
    out["f_rev_high12"] = np.where(stale, np.nan, high)
    out["revenue_period"] = m["period"].astype(object).map(lambda p: None if pd.isna(p) else str(p)).to_numpy()
    out["revenue_available_at"] = avail
    out["revenue_stale"] = stale
    return out


def attach_value(rows: pd.DataFrame, per: pd.DataFrame) -> pd.DataFrame:
    """E/P and B/M of the PER/PBR published for the PREVIOUS decision row of the same stock.  PER > 0 -> 1/PER,
    PER <= 0 -> 0, missing -> NaN; PBR > 0 -> 1/PBR, else NaN."""
    prev = rows.groupby(rows["stock_id"].to_numpy(dtype=object), sort=False)["decision_date"].shift(1)
    left = pd.DataFrame({"stock_id": rows["stock_id"].to_numpy(dtype=object),
                         "decision_date": pd.to_datetime(prev).to_numpy("datetime64[ns]")})
    right = per.rename(columns={"date": "decision_date"})[["stock_id", "decision_date", "PER", "PBR"]].copy()
    right["decision_date"] = pd.to_datetime(right["decision_date"]).astype("datetime64[ns]")
    if right.duplicated(["stock_id", "decision_date"]).any():
        raise H63XsComputationError("h63xs_per_duplicate_keys")
    m = left.merge(right, on=["stock_id", "decision_date"], how="left")
    if len(m) != len(left):
        raise H63XsComputationError("h63xs_per_merge_rows")
    pe_l, pb_l = m["PER"], m["PBR"]
    with np.errstate(all="ignore"):
        ep = np.where(pe_l > 0, 1.0 / pe_l, np.where(pe_l.notna(), 0.0, np.nan))
        bm = np.where(pb_l > 0, 1.0 / pb_l, np.nan)
    out = pd.DataFrame({"f_ep": ep, "f_bm": bm, "per_date": left["decision_date"].to_numpy()})
    out.index = rows.index
    return out


def date_groups(dates: Any) -> Tuple[np.ndarray, int, List[np.ndarray]]:
    codes, uniques = pd.factorize(np.asarray(dates), sort=True)
    codes = codes.astype(np.int64)
    k = len(uniques)
    order = np.argsort(codes, kind="stable")
    cuts = np.cumsum(np.bincount(codes, minlength=k))[:-1]
    return codes, k, np.split(order, cuts)


def rank_against(values: np.ndarray, member: np.ndarray, groups: List[np.ndarray]) -> np.ndarray:
    """Per-date rank against the member rows in [-1, 1]; non-members inserted (n+1); missing / n < 2 -> 0."""
    out = np.zeros(len(values))
    for idx in groups:
        x = values[idx]
        fin = np.isfinite(x)
        mem = member[idx] & fin
        v = np.sort(x[mem])
        n = len(v)
        if n < 2 or not fin.any():
            continue
        xf = x[fin]
        less = np.searchsorted(v, xf, "left")
        le = np.searchsorted(v, xf, "right")
        im = mem[fin]
        r = np.where(im, (less + le + 1) / 2.0, (less + le + 2) / 2.0)
        nn = np.where(im, n, n + 1)
        out[idx[fin]] = 2.0 * (r - 1.0) / (nn - 1.0) - 1.0
    return out


def centre_scores(score: np.ndarray, member: np.ndarray, codes: np.ndarray, k: int) -> Tuple[np.ndarray, np.ndarray]:
    msum = np.bincount(codes, weights=score * member, minlength=k)
    mcnt = np.bincount(codes, weights=member.astype(float), minlength=k)
    with np.errstate(invalid="ignore", divide="ignore"):
        mmean = msum / mcnt
    return mmean, score - mmean[codes]


def beats_median(dates: Any, r63: Any) -> np.ndarray:
    frame = pd.DataFrame({"d": np.asarray(dates), "r": np.asarray(r63, float)})
    med = frame.groupby("d")["r"].transform("median")
    return (frame["r"] > med).to_numpy(int)


def equal_stock_weights(stock_ids: Any) -> np.ndarray:
    s = pd.Series(np.asarray(stock_ids, dtype=object))
    n = s.groupby(s).transform("count").to_numpy(float)
    w = 1.0 / n
    return w / w.mean()


def fit_slope(z: Any, y: Any, w: Any, C: float = SLOPE_C) -> Dict[str, Any]:
    """No-intercept weighted logistic regression, L2 1/(C sum w) (= sklearn lbfgs objective), Newton + backtracking.
    A non-positive b means no skill -> b = 0."""
    z = np.asarray(z, float)
    y = np.asarray(y, float)
    w = np.asarray(w, float)
    if len(z) == 0 or not (np.isfinite(z).all() and np.isfinite(w).all()):
        raise H63XsComputationError("h63xs_slope_rows_invalid")
    sw = float(w.sum())
    l2 = 1.0 / (C * sw)
    b, iterations = 0.0, 0

    def objective(value: float) -> float:
        t = value * z
        return float(np.dot(w, np.logaddexp(0.0, t) - y * t) / sw + 0.5 * l2 * value * value)

    current = objective(b)
    for iterations in range(1, 101):
        p = sigmoid(b * z)
        grad = float(np.dot(w, (p - y) * z) / sw + l2 * b)
        hess = float(np.dot(w, p * (1.0 - p) * z * z) / sw + l2)
        step = grad / hess
        trial, scale = b - step, 1.0
        value = objective(trial)
        while value > current + 1e-15 and scale > 1e-8:
            scale *= 0.5
            trial = b - scale * step
            value = objective(trial)
        converged = abs(trial - b) <= SLOPE_NEWTON_TOL * max(1.0, abs(trial))
        b, current = trial, value
        if converged:
            break
    return {"b": max(float(b), 0.0), "b_raw": float(b), "rows": int(len(z)), "iterations": iterations}


def sigma63(vol: np.ndarray, member: np.ndarray, codes: np.ndarray, k: int) -> np.ndarray:
    """max(vol * sqrt(63/252), 0.03); missing vol -> median vol of the member rows of the same date."""
    v = np.asarray(vol, float)
    ok = member & np.isfinite(v)
    med = pd.Series(v[ok]).groupby(codes[ok]).median().reindex(range(k)).to_numpy()
    v = np.where(np.isfinite(v), v, med[codes])
    return np.maximum(v * math.sqrt(HORIZON / 252.0), SIGMA_FLOOR)


# ------------------------------------------------------------------------------------------------------------------
# daily gate (tw_single_stock_three_month_xs_io.daily_gate_table)
# ------------------------------------------------------------------------------------------------------------------
def revenue_current_flags(stock_ids: np.ndarray, sess_pos: np.ndarray, revenue_periods: Sequence[Any],
                          rule: StatutoryRevenueAvailability, sessions: pd.DatetimeIndex,
                          grace: int = REVENUE_FRESHNESS_GRACE_SESSIONS) -> np.ndarray:
    sessions = pd.DatetimeIndex(sessions)
    pos = np.asarray(sess_pos, np.int64)
    ses = sessions.to_numpy("datetime64[ns]")
    ref = ses[np.maximum(pos - int(grace), 0)]
    base = sessions[pos].to_period("M")
    sid = np.asarray(stock_ids, dtype=object)
    low = np.iinfo(np.int64).min
    expected = np.full(len(pos), low, dtype=np.int64)
    for k in (4, 3, 2, 1):
        cand = base - k
        avail = np.asarray(rule.available_at(sid, cand), "datetime64[ns]")
        ok = ~np.isnat(avail) & (avail <= ref)
        expected = np.where(ok, cand.asi8, expected)
    stamp = pd.to_datetime(pd.Series(list(revenue_periods), dtype=object), format="%Y-%m", errors="coerce")
    ok_got = stamp.notna().to_numpy()
    got_ord = np.full(len(pos), low, dtype=np.int64)
    got_ord[ok_got] = ((stamp[ok_got].dt.year.to_numpy(np.int64) - 1970) * 12
                       + stamp[ok_got].dt.month.to_numpy(np.int64) - 1)
    return (expected > low) & (got_ord >= expected)


def gate_production(P: pd.DataFrame, rule: StatutoryRevenueAvailability, sessions: pd.DatetimeIndex
                    ) -> Tuple[np.ndarray, pd.DataFrame]:
    """Per-session gate: eligible count, 7-factor coverage, revenue freshness (statutory rule, grace 2)."""
    elig = P["eligible"].to_numpy(bool)
    sub = P.loc[elig, ["stock_id", "decision_date", "sess_pos", "revenue_period"] + list(FEATURES)]
    dates = sub["decision_date"].to_numpy()
    table = pd.DataFrame({"eligible": sub.groupby(dates).size()})
    cov = pd.DataFrame(np.isfinite(sub[list(FEATURES)].to_numpy(float)), columns=list(FEATURES)).groupby(dates).mean()
    table = table.join(cov)
    flags = revenue_current_flags(sub["stock_id"].to_numpy(dtype=object), sub["sess_pos"].to_numpy(np.int64),
                                  sub["revenue_period"].tolist(), rule, sessions)
    table["revenue_current_share"] = pd.Series(flags.astype(float)).groupby(dates).mean()
    ok = table["eligible"].to_numpy() >= MIN_ELIGIBLE_COUNT
    for name, minimum in MIN_COMPONENT_COVERAGE.items():
        ok &= table[name].to_numpy(float) >= minimum
    ok &= table["revenue_current_share"].to_numpy(float) >= MIN_REVENUE_CURRENT_SHARE
    table["ok"] = ok
    table.index = pd.DatetimeIndex(table.index)
    ok_dates = table.index[table["ok"].to_numpy(bool)]
    return P["decision_date"].isin(ok_dates).to_numpy(), table


# ------------------------------------------------------------------------------------------------------------------
# panel
# ------------------------------------------------------------------------------------------------------------------
def build_panel(px: pd.DataFrame, board: pd.DataFrame, per: pd.DataFrame, revenue: pd.DataFrame,
                late_codes: Sequence[str], log: Optional[Dict[str, Any]] = None
                ) -> Tuple[pd.DataFrame, Dict[str, Any]]:
    """Stitched prices + board + PER + revenue -> one row per (stock, session) with every U7 field.

    px: stitched price rows (prepare_prices spelling); board: prepare_board; per: prepare_per;
    revenue: prepare_revenue.  Own rows are every price row (every board); main = TWSE/TPEx on that day."""
    log = {} if log is None else log
    px = px.rename(columns={"Trading_money": "money", "Trading_Volume": "volume"})
    px = px.merge(board[["stock_id", "date", "market"]], on=["stock_id", "date"], how="left")
    px["main"] = px["market"].isin(list(ELIG_BOARDS)).to_numpy()
    px = px[px["close"].notna()].sort_values(["stock_id", "date"], kind="mergesort").reset_index(drop=True)
    log["price_rows"] = int(len(px))
    log["price_rows_without_board"] = int(px["market"].isna().sum())
    px4 = px[["stock_id", "date", "close", "money", "main"]].copy()
    mret = market_return(px4)
    sessions = pd.DatetimeIndex(np.sort(px.loc[px["main"].to_numpy(), "date"].unique()))
    feats = price_features(px4, mret)
    P = px[["stock_id", "date", "main", "close", "market"]].copy()
    for k_, v in feats.items():
        P[k_] = v
    del feats, px4, px
    P = P[P["date"].isin(sessions)].reset_index(drop=True).rename(columns={"date": "decision_date"})
    P["sess_pos"] = sessions.get_indexer(P["decision_date"]).astype(np.int64)
    P["slope_phase"] = slope_phase_for(sessions)[P["sess_pos"].to_numpy()].astype(np.int64)
    P["eligible"] = ((P["f_nhist"].to_numpy() >= ELIG_MIN_HIST) & (P["f_money60"].to_numpy() >= ELIG_MIN_VALUE)
                     & P["main"].to_numpy(bool))
    P["label_ok"] = P["r63"].between(*LABEL_RANGE).to_numpy(bool)
    rule = StatutoryRevenueAvailability(sessions, late_codes)
    log["revenue_periods"] = [str(revenue["period"].min()), str(revenue["period"].max())]
    log["revenue_late_codes"] = list(late_codes)
    rev = attach_revenue(P[["stock_id", "decision_date"]], revenue_table(revenue, rule), REVENUE_STALE_DAYS)
    for c in ("f_rev_yoy", "f_rev_high12", "revenue_period", "revenue_available_at", "revenue_stale"):
        P[c] = rev[c].to_numpy()
    del rev
    val = attach_value(P[["stock_id", "decision_date"]], per[["stock_id", "date", "PER", "PBR"]])
    for c in ("f_ep", "f_bm", "per_date"):
        P[c] = val[c].to_numpy()
    del val
    log["panel_rows"] = int(len(P))
    log["sessions"] = {"first": str(sessions[0].date()), "last": str(sessions[-1].date()), "count": int(len(sessions))}
    P["vol252"] = vol252_on_rows(P)
    return P, {"sessions": sessions, "rule": rule, "market_return": mret}


def vol252_on_rows(P: pd.DataFrame) -> np.ndarray:
    """std of r1 = close_t / close_{t-1} - 1 over the last 252 own panel rows (>= 126 valid), x sqrt(252)."""
    sid = P["stock_id"].to_numpy(object)
    close = P["close"].to_numpy(float)
    n = len(P)
    gs_flag = np.r_[True, sid[1:] != sid[:-1]]
    gstart = np.flatnonzero(gs_flag)[np.cumsum(gs_flag) - 1]
    pos_in = np.arange(n) - gstart
    r1 = np.full(n, np.nan)
    ok = pos_in >= 1
    with np.errstate(all="ignore"):
        r1[ok] = close[ok] / close[np.flatnonzero(ok) - 1] - 1.0
    valid = np.isfinite(r1)
    x0 = np.where(valid, r1, 0.0)
    cs, cs2, cn = np.cumsum(x0), np.cumsum(x0 * x0), np.cumsum(valid.astype(np.int64))
    i = np.arange(n)
    j = np.maximum(gstart, i - VOL252_WINDOW + 1)
    pj = np.maximum(j - 1, 0)
    has = j > 0
    s = cs - np.where(has, cs[pj], 0.0)
    s2 = cs2 - np.where(has, cs2[pj], 0.0)
    c = cn - np.where(has, cn[pj], 0)
    with np.errstate(invalid="ignore", divide="ignore"):
        var = (s2 - s * s / c) / (c - 1)
    return np.where(c >= VOL252_MIN, np.sqrt(np.maximum(var, 0)), np.nan) * math.sqrt(252)


def score_panel(P: pd.DataFrame) -> Dict[str, Any]:
    """Per-session ranks against the eligible universe, S, Sc and the B1 / B3 sigmas (float64)."""
    member = P["eligible"].to_numpy(bool)
    codes, k, groups = date_groups(P["decision_date"].to_numpy())
    ranks = {name: rank_against(P[name].to_numpy(float), member, groups) for name in FEATURES}
    S = np.zeros(len(P))
    for name in FEATURES:
        S += SIGNS[name] * ranks[name]
    S = S / K
    mmean, Sc = centre_scores(S, member, codes, k)
    vol60 = P["f_vol60"].to_numpy(float)
    sig = sigma63(vol60, member, codes, k)
    v252 = P["vol252"].to_numpy(float)
    v60f = np.where(np.isfinite(vol60), vol60, np.nan)
    mixv = np.sqrt(B3_WEIGHT_VOL60 * v60f ** 2 + (1 - B3_WEIGHT_VOL60) * np.where(np.isfinite(v252), v252, v60f) ** 2)
    sig_b3 = sigma63(mixv, member, codes, k)
    return {"ranks": ranks, "S": S, "Sc": Sc, "universe_mean": mmean[codes], "sigma63": sig, "sigma63_b3": sig_b3,
            "codes": codes, "k": k}


def slope_mask(P: pd.DataFrame, cutoff: Any, gate: Optional[np.ndarray]) -> np.ndarray:
    T = day64(cutoff)
    mask = (P["eligible"].to_numpy(bool) & P["label_ok"].to_numpy(bool)
            & (P["decision_date"].to_numpy("datetime64[ns]") < T) & (P["label_end"].to_numpy("datetime64[ns]") < T)
            & (P["slope_phase"].to_numpy() % SLOPE_DATE_STEP == 0))
    return mask if gate is None else mask & gate


def fit_b(P: pd.DataFrame, Sc: np.ndarray, cutoff: Any, gate: Optional[np.ndarray]) -> Dict[str, Any]:
    """Slope b with labels and decisions strictly before cutoff (exclusive)."""
    mk = slope_mask(P, cutoff, gate)
    if not mk.any():
        raise H63XsComputationError("h63xs_slope_rows_missing")
    y = beats_median(P["decision_date"].to_numpy("datetime64[ns]")[mk], P["r63"].to_numpy(float)[mk])
    w = equal_stock_weights(P["stock_id"].to_numpy(object)[mk])
    out = fit_slope(Sc[mk], y, w)
    out.update(cutoff=str(pd.Timestamp(cutoff).date()), gated=gate is not None,
               stocks=int(pd.Series(P["stock_id"].to_numpy(object)[mk]).nunique()),
               decision_sessions=int(pd.Series(P["decision_date"].to_numpy("datetime64[ns]")[mk]).nunique()))
    return out


def slope_guard(b: float) -> str:
    return "ok" if SLOPE_MIN <= float(b) <= SLOPE_MAX else "out_of_range"


# ------------------------------------------------------------------------------------------------------------------
# bands: B3 Mondrian tables and CQR252 (band.py / band_combo.py of the study)
# ------------------------------------------------------------------------------------------------------------------
def mondrian_tables(pool_u: np.ndarray, pool_s: np.ndarray, k_rows: int) -> Tuple[np.ndarray, np.ndarray, np.ndarray]:
    u = pool_u[:k_rows]
    s_ = pool_s[:k_rows]
    edges = np.quantile(s_, [0.2, 0.4, 0.6, 0.8])
    b = np.searchsorted(edges, s_, side="right")
    tab = np.array([np.quantile(u[b == j], BAND_TAUS) for j in range(BAND_VOL_BUCKETS)])
    counts = np.bincount(b, minlength=BAND_VOL_BUCKETS)
    return edges, tab, counts


class BandPool:
    """Matured pool rows (eligible, label_ok, label_end known) ordered by label_end; tables at any date D use the
    prefix with label_end < D (no daily gate)."""

    def __init__(self, P: pd.DataFrame, sigma_b3: np.ndarray):
        el = P["eligible"].to_numpy(bool)
        lok = P["label_ok"].to_numpy(bool)
        le = P["label_end"].to_numpy("datetime64[ns]")
        r63 = P["r63"].to_numpy(float)
        pool_ok = el & lok & ~np.isnat(le)
        PI = np.flatnonzero(pool_ok)
        order = PI[np.argsort(le[PI], kind="stable")]
        self.le_sorted = le[order]
        with np.errstate(invalid="ignore", divide="ignore"):
            self.u = r63[order] / sigma_b3[order]
        self.s = sigma_b3[order]
        self._tables: Dict[np.datetime64, Dict[str, Any]] = {}

    def table_at(self, D: Any) -> Dict[str, Any]:
        D = day64(D)
        if D not in self._tables:
            kk = int(np.searchsorted(self.le_sorted, D, side="left"))
            if kk < BAND_MIN_POOL_ROWS:
                self._tables[D] = {"pool": kk, "status": "pool_insufficient"}
            else:
                edges, tab, counts = mondrian_tables(self.u, self.s, kk)
                self._tables[D] = {"pool": kk, "status": "ok", "edges": edges, "tab": tab, "counts": counts}
        return self._tables[D]


def apply_table(table: Dict[str, Any], sigma_rows: np.ndarray) -> Tuple[np.ndarray, np.ndarray]:
    """(q[:, 3], bucket) of rows with the B3 sigma, NaN / -1 when the table is unavailable."""
    q = np.full((len(sigma_rows), 3), np.nan)
    bucket = np.full(len(sigma_rows), -1, dtype=np.int64)
    if table.get("status") == "ok":
        bucket = np.searchsorted(table["edges"], sigma_rows, side="right").astype(np.int64)
        q = sigma_rows[:, None] * table["tab"][bucket]
    return q, bucket


def cqr_at(D: Any, P: pd.DataFrame, sc: Dict[str, Any], gate: np.ndarray, pool: BandPool) -> Dict[str, Any]:
    """CQR252 shift at decision date D: conformity E = max((q10 - y)/sigma_B1, (y - q90)/sigma_B1) over gate-passing
    eligible label_ok rows with label_end < D and decision dates within the last 252 + 63 gate days; q10 / q90 are
    the B3 bands issued on each row's own decision date.  Q = 80% quantile when >= 2000 rows."""
    D = day64(D)
    d = P["decision_date"].to_numpy("datetime64[ns]")
    le = P["label_end"].to_numpy("datetime64[ns]")
    el = P["eligible"].to_numpy(bool)
    lok = P["label_ok"].to_numpy(bool)
    gate_days = np.unique(d[gate])
    if not (gate_days == D).any():
        gate_days = np.sort(np.append(gate_days, D))
    i = int(np.searchsorted(gate_days, D, side="left"))
    lo = gate_days[max(0, i - CQR_WINDOW - CQR_LABEL_LAG)]
    cal = gate & el & lok & ~np.isnat(le) & (le < D) & (d >= lo)
    idx = np.flatnonzero(cal)
    info: Dict[str, Any] = {"Q": None, "cal_rows": 0, "cal_from": str(pd.Timestamp(lo).date()),
                            "cal_dates": 0, "status": "ok_no_cqr"}
    if len(idx) == 0:
        return info
    y = P["r63"].to_numpy(float)[idx]
    sg = sc["sigma63"][idx]
    sb3 = sc["sigma63_b3"][idx]
    q10 = np.full(len(idx), np.nan)
    q90 = np.full(len(idx), np.nan)
    dd = d[idx]
    for day in np.unique(dd):
        rows = np.flatnonzero(dd == day)
        q, _ = apply_table(pool.table_at(day), sb3[rows])
        q10[rows], q90[rows] = q[:, 0], q[:, 2]
    with np.errstate(invalid="ignore"):
        E = np.maximum((q10 - y) / sg, (y - q90) / sg)
    finite = np.isfinite(E)
    n = int(finite.sum())
    info.update(cal_rows=n, cal_dates=int(len(np.unique(dd[finite]))) if n else 0)
    if n < CQR_MIN_ROWS:
        return info
    info.update(Q=float(np.quantile(E[finite], CQR_LEVEL)), status="ok")
    return info


# ------------------------------------------------------------------------------------------------------------------
# inference rows at the decision date
# ------------------------------------------------------------------------------------------------------------------
def relative_strength(p: np.ndarray, main: np.ndarray) -> np.ndarray:
    p = np.asarray(p, float)
    out = np.where(p >= STRONG_AT, "relatively_strong", np.where(p <= WEAK_AT, "relatively_weak", "neutral"))
    return np.where(np.asarray(main, bool), out, "out_of_scope").astype(object)


def infer_rows(P: pd.DataFrame, sc: Dict[str, Any], b: float, D: Any, pool: BandPool, gate: np.ndarray
               ) -> Tuple[pd.DataFrame, Dict[str, Any]]:
    """Every panel row of session D with P = sigmoid(b * Sc) and the B3 + CQR252 80% band."""
    D = day64(D)
    d = P["decision_date"].to_numpy("datetime64[ns]")
    L = np.flatnonzero(d == D)
    if len(L) == 0:
        raise H63XsComputationError("h63xs_decision_rows_missing")
    main = P["main"].to_numpy(bool)[L]
    el = P["eligible"].to_numpy(bool)
    Sc = sc["Sc"]
    fr = P.iloc[L][["stock_id", "decision_date", "market", "main", "eligible", "label_ok", "f_nhist", "f_money60",
                    "f_vol60", "vol252"] + list(FEATURES) +
                   ["revenue_period", "revenue_available_at", "revenue_stale", "per_date", "close"]
                   ].reset_index(drop=True)
    fr = fr.rename(columns={"close": "signal_adj_close"})
    for f in FEATURES:
        fr["rk_" + f] = sc["ranks"][f][L]
    fr["n_components"] = np.isfinite(P[list(FEATURES)].to_numpy(float)[L]).sum(axis=1).astype(np.int64)
    fr["score"] = sc["S"][L]
    fr["score_universe_mean"] = sc["universe_mean"][L]
    fr["score_centred"] = Sc[L]
    fr["sigma63"] = sc["sigma63"][L]
    fr["sigma63_b3"] = sc["sigma63_b3"][L]
    p_raw = sigmoid(float(b) * Sc[L])
    fr["p_raw"] = p_raw
    fr["p"] = np.where(main, np.clip(p_raw, PROBABILITY_EPSILON, 1 - PROBABILITY_EPSILON), 0.5)
    status = np.where(main, "ok", "out_of_scope").astype(object)
    if float(b) <= 0:
        fr["p"] = 0.5
        status = np.where(main, "no_skill", "out_of_scope").astype(object)
    fr["p_status"] = status
    fr["relative_strength"] = relative_strength(fr["p"].to_numpy(float), main)
    rank = np.full(len(L), np.nan)
    elig_L = el[L]
    if elig_L.any():
        sub = pd.DataFrame({"i": np.flatnonzero(elig_L), "sc": Sc[L][elig_L],
                            "sid": fr["stock_id"].to_numpy(object)[elig_L]})
        sub = sub.sort_values(["sc", "sid"], ascending=[False, True], kind="mergesort")
        rank[sub["i"].to_numpy()] = np.arange(1, len(sub) + 1)
    fr["rank_eligible"] = rank
    fr["own_vol60_missing"] = ~np.isfinite(P["f_vol60"].to_numpy(float)[L])
    table = pool.table_at(D)
    q, bucket = apply_table(table, sc["sigma63_b3"][L])
    fr["b3_bucket"] = bucket
    fr["b3_q10"], fr["b3_q50"], fr["b3_q90"] = q[:, 0], q[:, 1], q[:, 2]
    cq = cqr_at(D, P, sc, gate, pool)
    if cq["Q"] is not None and table.get("status") == "ok":
        fr["b3cqr_q10"] = fr["b3_q10"] - cq["Q"] * sc["sigma63"][L]
        fr["b3cqr_q90"] = fr["b3_q90"] + cq["Q"] * sc["sigma63"][L]
    else:
        fr["b3cqr_q10"] = fr["b3_q10"]
        fr["b3cqr_q90"] = fr["b3_q90"]
    band_status = np.full(len(L), "ok" if cq["Q"] is not None else "ok_no_cqr", dtype=object)
    if table.get("status") != "ok":
        band_status[:] = "pool_insufficient"
    own_missing = fr["own_vol60_missing"].to_numpy(bool)
    band_status[own_missing & (band_status != "pool_insufficient")] = "own_vol60_missing"
    lo, hi = fr["b3cqr_q10"].to_numpy(float), fr["b3cqr_q90"].to_numpy(float)
    bad = np.isin(band_status, ["ok", "ok_no_cqr"]) & ~((lo <= 0) & (hi >= 0) & np.isfinite(lo) & np.isfinite(hi))
    band_status[bad] = "invalid_not_containing_zero"
    fr["band_status"] = band_status
    meta = {"decision_date": str(pd.Timestamp(D).date()), "rows": int(len(L)), "main": int(main.sum()),
            "eligible": int(elig_L.sum()), "b": float(b),
            "b3_table": {"pool": table.get("pool"), "status": table.get("status"),
                         "edges": table.get("edges"), "q10_q50_q90_by_bucket": table.get("tab"),
                         "counts": table.get("counts")},
            "cqr": cq}
    return fr, meta


def gate_table_row(table: pd.DataFrame, D: Any) -> Dict[str, Any]:
    D = pd.Timestamp(D)
    if D not in table.index:
        return {"date": str(D.date()), "ok": False, "eligible": 0}
    row = table.loc[D]
    out = {"date": str(D.date()), "ok": bool(row["ok"]), "eligible": int(row["eligible"]),
           "revenue_current_share": float(row["revenue_current_share"])}
    for f in FEATURES:
        out["cov_" + f] = float(row[f])
    return out


def jsonable(value: Any) -> Any:
    if value is None or value is pd.NaT:
        return None
    if isinstance(value, dict):
        return {str(k): jsonable(v) for k, v in value.items()}
    if isinstance(value, (list, tuple)):
        return [jsonable(v) for v in value]
    if isinstance(value, np.ndarray):
        if value.dtype.kind == "M":
            return [None if np.isnat(v) else str(pd.Timestamp(v).date()) for v in value]
        return jsonable(value.tolist())
    if isinstance(value, (np.bool_, bool)):
        return bool(value)
    if isinstance(value, np.integer):
        return int(value)
    if isinstance(value, (float, np.floating)):
        return float(value) if np.isfinite(value) else None
    if isinstance(value, (pd.Timestamp, np.datetime64)):
        stamp = pd.Timestamp(value)
        return None if pd.isna(stamp) else str(stamp.date())
    return value
