"""Point-in-time feature/label panel for the pre-open H1 model (stock_daily_preopen).

Ported line-for-line from the validated research builder
D:\\TWStockData\\program_data\\stock_daily_c0\\candidates\\calibration_20261018\\code\\features.py
(only I/O changed: inputs are DataFrames, the next TW session of the last row can be given, and
the us10y scale is frozen to the research value). A parity test compares both builders.

Time convention: a row (date t, stock s) holds features known after the close of t; on_* adds
the US move up to the last US session before the next TW session (valid before the next open).
Labels y_H = adj_close[t+H] / adj_close[t] - 1 on the exact H-th TW session (NaN if missing).
"""
from __future__ import annotations

import numpy as np
import pandas as pd

HORIZONS = (1, 5, 20, 60, 63)
US10Y_SCALE = 4.136000156402588  # median us10y level of the research snapshot (frozen for reproducibility)
INSTS_US = ["us_sp500", "us_nasdaq", "us_semiconductor", "us_dow", "us_technology", "us_financials", "us10y", "wti", "gold"]
INSTS_FX = ["usd_twd", "usd_jpy", "usd_krw"]
INSTS_ASIA = ["jp_nikkei225", "kr_kospi", "kr_kosdaq"]


def wide(df, col, dates, stocks):
    w = df.pivot(index="date", columns="stock_id", values=col)
    return w.reindex(index=dates, columns=stocks).astype("float64")


def rolling_mean(x, n, minp=None):
    return x.rolling(n, min_periods=minp or max(2, int(n * 0.8))).mean()


def rolling_std(x, n, minp=None):
    return x.rolling(n, min_periods=minp or max(2, int(n * 0.8))).std()


def build_panel(px, ch, rv, info, g, next_session=None, us10y_scale=US10Y_SCALE, min_hist=120, log=None):
    """px: prices (date, stock_id, adj_open/high/low/close, raw_close, volume, amount, transactions, market);
    ch: chips; rv: month revenue; info: dated stock_info; g: wide global closes (index=date).
    next_session: the TW session after the last price date (default: next weekday)."""
    log = log or (lambda m: None)
    px = px.copy()
    px["stock_id"] = px["stock_id"].astype(str)
    dates = pd.DatetimeIndex(sorted(px["date"].unique()))
    stocks = sorted(px["stock_id"].unique())

    P = wide(px, "adj_close", dates, stocks)
    O = wide(px, "adj_open", dates, stocks)
    H = wide(px, "adj_high", dates, stocks)
    L = wide(px, "adj_low", dates, stocks)
    RC = wide(px, "raw_close", dates, stocks)
    V = wide(px, "volume", dates, stocks)
    A = wide(px, "amount", dates, stocks)
    T = wide(px, "transactions", dates, stocks)
    mkt = px.pivot(index="date", columns="stock_id", values="market").reindex(index=dates, columns=stocks)
    mkt = mkt.ffill().bfill()
    valid = P.notna() & (V > 0)

    Pf = P.ffill(limit=5)
    prev = Pf.shift(1)
    F = {}
    for k in (1, 2, 3, 5, 10, 20, 60, 120, 250):
        F[f"r{k}"] = P / Pf.shift(k) - 1
    d = np.log(P / prev)
    for k in (5, 20, 60):
        F[f"vol{k}"] = rolling_std(d, k)
    F["vol_ratio_5_60"] = F["vol5"] / F["vol60"]
    for k in (5, 10, 20, 60, 120):
        F[f"ma_dist{k}"] = P / rolling_mean(Pf, k) - 1
    F["ma_slope20"] = rolling_mean(Pf, 20) / rolling_mean(Pf, 20).shift(5) - 1
    gain = d.clip(lower=0)
    loss = (-d).clip(lower=0)
    rs = rolling_mean(gain, 14) / rolling_mean(loss, 14)
    F["rsi14"] = 1 - 1 / (1 + rs)
    F["hi250"] = P / Pf.rolling(250, min_periods=120).max() - 1
    F["lo250"] = P / Pf.rolling(250, min_periods=120).min() - 1
    F["dd60"] = P / Pf.rolling(60, min_periods=40).max() - 1
    F["gap"] = O / prev - 1
    F["intraday"] = P / O - 1
    F["range"] = (H - L) / prev
    F["clv"] = ((P - L) / (H - L)).where(H > L, 0.5)
    F["upper_shadow"] = (H - np.maximum(O, P)) / prev
    F["lower_shadow"] = (np.minimum(O, P) - L) / prev
    up = (F["r1"] > 0).astype(float).where(F["r1"].notna())
    F["up_ratio20"] = rolling_mean(up, 20)
    streak = np.zeros(P.shape)
    r1v = F["r1"].to_numpy()
    for i in range(1, len(dates)):
        s_prev = streak[i - 1]
        r = r1v[i]
        streak[i] = np.where(r > 0, np.where(s_prev > 0, s_prev + 1, 1), np.where(r < 0, np.where(s_prev < 0, s_prev - 1, -1), 0))
    F["streak"] = pd.DataFrame(np.clip(streak, -10, 10), index=dates, columns=stocks)
    F["limit_up"] = (F["r1"] >= 0.095).astype(float)
    F["limit_dn"] = (F["r1"] <= -0.095).astype(float)
    F["limit_up5"] = F["limit_up"].rolling(5, min_periods=1).sum()

    Vp = V.shift(1)
    F["vratio5"] = V / rolling_mean(Vp, 5)
    F["vratio20"] = V / rolling_mean(Vp, 20)
    amt20 = rolling_mean(A, 20)
    F["log_amt20"] = np.log1p(amt20)
    F["aratio5_20"] = rolling_mean(A, 5) / rolling_mean(A.shift(5), 20)
    F["tx_ratio20"] = T / rolling_mean(T.shift(1), 20)
    F["log_price"] = np.log(RC)
    F["avg_trade_size"] = np.log1p(A / T.replace(0, np.nan))
    log("price features done")

    ch = ch.copy()
    ch["stock_id"] = ch["stock_id"].astype(str)
    ch = ch[ch["stock_id"].isin(set(stocks))]

    def cw(col, ffill=0):
        w = wide(ch, col, dates, stocks)
        return w.ffill(limit=ffill) if ffill else w

    shares = cw("NumberOfSharesIssued", ffill=60)
    F["turnover20"] = rolling_mean(V / shares, 20)
    F["log_mcap"] = np.log(RC * shares)
    for col, tag in (("foreign_investors_net", "f"), ("investment_trust_net", "it"), ("dealer_net", "dl"), ("total_net", "tot")):
        x = cw(col).fillna(0.0).where(valid)
        for k in (1, 5, 20):
            num = x.rolling(k, min_periods=1).sum()
            den = V.rolling(k, min_periods=1).sum()
            F[f"{tag}_net{k}"] = (num / den).where(den > 0)
        F[f"{tag}_net20_sh"] = x.rolling(20, min_periods=5).sum() / shares
    F["it_buy_days10"] = (cw("investment_trust_net").fillna(0) > 0).astype(float).rolling(10, min_periods=1).sum()
    mb = cw("margin_purchase_balance", ffill=3)
    sb = cw("short_sale_balance", ffill=3)
    lim = cw("MarginPurchaseLimit", ffill=3)
    F["m_chg1"] = mb / mb.shift(1) - 1
    F["m_chg5"] = mb / mb.shift(5) - 1
    F["m_chg20"] = mb / mb.shift(20) - 1
    F["m_util"] = (mb / lim).where(lim > 0)
    F["m_sh"] = mb * 1000 / shares
    F["s_chg5"] = (sb - sb.shift(5)) / (mb.shift(5) + 1)
    F["s_m_ratio"] = sb / (mb + 1)
    dtv = cw("day_trade_volume")
    F["dt_ratio"] = (dtv / V).where(V > 0)
    F["dt_ratio5"] = rolling_mean(F["dt_ratio"], 5, 3)
    per = cw("per", ffill=5)
    F["ep"] = (1 / per).where(per > 0, 0.0).where(per.notna())
    F["pbr"] = cw("pbr", ffill=5)
    F["dy"] = cw("dividend_yield", ffill=5)
    fh = cw("holding_percentage", ffill=5)
    F["fh"] = fh
    F["fh_chg5"] = fh - fh.shift(5)
    F["fh_chg20"] = fh - fh.shift(20)
    del ch
    log("chip features done")

    rv = rv.copy()
    rv["stock_id"] = rv["stock_id"].astype(str)
    rv = rv[rv["stock_id"].isin(set(stocks))].sort_values(["stock_id", "revenue_year", "revenue_month"])
    rv["ym"] = rv["revenue_year"].astype(int) * 12 + rv["revenue_month"].astype(int) - 1
    rv = rv.set_index(["stock_id", "ym"])["revenue"]
    rv = rv[~rv.index.duplicated(keep="last")]
    rvd = rv.reset_index()
    lag12 = rvd.assign(ym=rvd["ym"] + 12).set_index(["stock_id", "ym"])["revenue"]
    lag1 = rvd.assign(ym=rvd["ym"] + 1).set_index(["stock_id", "ym"])["revenue"]
    rvd = rvd.set_index(["stock_id", "ym"])
    rvd["yoy"] = rvd["revenue"] / lag12.reindex(rvd.index) - 1
    rvd["mom"] = rvd["revenue"] / lag1.reindex(rvd.index) - 1
    roll3 = rvd["revenue"].groupby(level=0).rolling(3).sum().droplevel(0)
    roll3_l12 = roll3.reset_index().assign(ym=lambda x: x["ym"] + 12).set_index(["stock_id", "ym"])["revenue"]
    rvd["yoy3"] = roll3 / roll3_l12.reindex(rvd.index) - 1
    rvd = rvd.reset_index()
    avail_month = rvd["ym"] + 1
    deadline = pd.to_datetime(dict(year=avail_month // 12, month=avail_month % 12 + 1, day=10))
    pos = np.searchsorted(dates.values, deadline.values, side="left") + 1
    ext_dates = dates.append(pd.bdate_range(dates[-1] + pd.Timedelta(days=1), periods=40))
    rvd["avail"] = ext_dates[np.clip(pos, 0, len(ext_dates) - 1)]
    for col in ("yoy", "mom", "yoy3"):
        w = rvd.pivot_table(index="avail", columns="stock_id", values=col, aggfunc="last")
        w = w.reindex(columns=stocks)
        w = w.reindex(w.index.union(dates)).sort_index().ffill(limit=70).reindex(dates)
        F[f"rev_{col}"] = w.clip(-1, 5)
    log("revenue features done")

    r1 = F["r1"].where(valid)
    elig = valid & (rolling_mean(A, 20) > 0)
    ctx = pd.DataFrame(index=dates)
    ctx["mkt_r1"] = r1.where(elig).mean(axis=1)
    ctx["mkt_r5"] = F["r5"].where(elig).mean(axis=1)
    ctx["mkt_r20"] = F["r20"].where(elig).mean(axis=1)
    ctx["mkt_r60"] = F["r60"].where(elig).mean(axis=1)
    ctx["breadth"] = (r1 > 0).where(r1.notna()).mean(axis=1)
    ctx["breadth5"] = ctx["breadth"].rolling(5).mean()
    ctx["mkt_vol20"] = ctx["mkt_r1"].rolling(20).std()
    ctx["limit_up_frac"] = F["limit_up"].where(valid).mean(axis=1)
    ctx["limit_dn_frac"] = F["limit_dn"].where(valid).mean(axis=1)
    tot_amt = A.where(valid).sum(axis=1)
    ctx["mkt_amt_ratio"] = tot_amt / tot_amt.shift(1).rolling(20).mean()
    ctx["mkt_dispersion"] = r1.where(elig).std(axis=1)
    ctx["mkt_f_net"] = F["f_net1"].where(valid).mean(axis=1)
    for m in ("TWSE", "TPEx"):
        ctx[f"mkt_r1_{m}"] = r1.where(elig & (mkt == m)).mean(axis=1)
    mkt_d = np.log1p(ctx["mkt_r1"])
    cov = d.rolling(60, min_periods=40).cov(mkt_d)
    F["beta60"] = cov.div(mkt_d.rolling(60, min_periods=40).var(), axis=0)
    for k in (1, 5, 20, 60):
        F[f"rel_r{k}"] = F[f"r{k}"].sub(ctx[f"mkt_r{k}"], axis=0)
    info = info.copy()
    info["stock_id"] = info["stock_id"].astype(str)
    info = info[info["stock_id"].isin(set(stocks))].sort_values("date")
    codes_all, _ = pd.factorize(info["industry_category"])
    info["code"] = codes_all
    indw = info.pivot_table(index="date", columns="stock_id", values="code", aggfunc="last")
    indw = indw.reindex(indw.index.union(dates)).sort_index().ffill().bfill().reindex(dates).reindex(columns=stocks)
    first_ind = info.drop_duplicates("stock_id", keep="last").set_index("stock_id")["industry_category"]
    ind = first_ind.reindex(stocks).fillna("NA")
    for k in (5, 20):
        x = F[f"r{k}"].where(elig)
        long = x.stack(future_stack=True).rename("v").to_frame()
        long["code"] = indw.stack(future_stack=True).reindex(long.index)
        long = long.dropna(subset=["code"])
        means = long.groupby([long.index.get_level_values(0), "code"])["v"].transform("mean")
        ind_mean = means.unstack().reindex(index=dates, columns=stocks)
        F[f"ind_r{k}"] = ind_mean
        F[f"rel_ind_r{k}"] = F[f"r{k}"] - ind_mean
    log("context features done")

    g = g.sort_index()
    cal = pd.Series(dates, index=dates)
    next_date = cal.shift(-1)
    next_date.iloc[-1] = pd.Timestamp(next_session) if next_session is not None else dates[-1] + pd.offsets.BDay(1)

    def level_asof(series, cut):
        s = series.dropna()
        idx = s.index.searchsorted(cut.values, side="right") - 1
        out = np.where(idx >= 0, s.to_numpy()[np.clip(idx, 0, None)], np.nan)
        return pd.Series(out, index=cut.index)

    def nth_back(series, cut, n):
        s = series.dropna()
        idx = s.index.searchsorted(cut.values, side="right") - 1 - n
        return pd.Series(np.where(idx >= 0, s.to_numpy()[np.clip(idx, 0, None)], np.nan), index=cut.index)

    G = pd.DataFrame(index=dates)
    cut_close_us = pd.Series(dates - pd.Timedelta(days=1), index=dates)
    cut_close_asia = pd.Series(dates, index=dates)
    cut_pre = pd.Series(pd.DatetimeIndex(next_date.values) - pd.Timedelta(days=1), index=dates)
    for inst in INSTS_US + INSTS_FX + INSTS_ASIA:
        if inst not in g.columns:
            continue
        s = g[inst]
        cut = cut_close_asia if inst in INSTS_ASIA else cut_close_us
        lvl = level_asof(s, cut)
        G[f"g_{inst}_r1"] = lvl / nth_back(s, cut, 1) - 1
        G[f"g_{inst}_r5"] = lvl / nth_back(s, cut, 5) - 1
        G[f"g_{inst}_r20"] = lvl / nth_back(s, cut, 20) - 1
        if inst in INSTS_US + INSTS_FX:
            lvl_pre = level_asof(s, cut_pre)
            G[f"on_{inst}"] = lvl_pre / lvl - 1
    for c in [c for c in G.columns if c.startswith(("g_us10y", "on_us10y"))]:
        G[c] = G[c] * us10y_scale
    G = G.replace([np.inf, -np.inf], np.nan)
    log("global features done")

    Y = {}
    O_next = O.shift(-1)
    for h in HORIZONS:
        Y[f"y{h}"] = P.shift(-h) / P - 1
        Y[f"yo{h}"] = P.shift(-h) / O_next - 1
    Y["gap_next"] = O_next / P - 1
    Y["open_lock_next"] = ((O_next >= H.shift(-1) * 0.9999) & (Y["gap_next"] > 0.09)).astype(float).where(O_next.notna())
    hist = Pf.notna().cumsum()
    base_mask = valid & (hist >= min_hist)
    idx = np.where(base_mask.to_numpy())
    out = pd.DataFrame({
        "date": dates[idx[0]],
        "stock_id": np.asarray(stocks, dtype=object)[idx[1]],
        "market": mkt.to_numpy()[idx],
        "industry": ind.to_numpy()[idx[1]],
        "hist_len": hist.to_numpy()[idx].astype("int32"),
        "amt20": amt20.to_numpy()[idx].astype("float64"),
        "raw_close": RC.to_numpy()[idx],
        "adj_close": P.to_numpy()[idx],
    })
    cols = {}
    for name, w in {**F, **Y}.items():
        arr = w.to_numpy()[idx]
        arr = np.where(np.isfinite(arr), arr, np.nan)
        cols[name] = arr.astype("float32")
    out = pd.concat([out, pd.DataFrame(cols)], axis=1)
    ctxg = pd.concat([ctx, G], axis=1)
    ctxg = ctxg.replace([np.inf, -np.inf], np.nan).astype("float32")
    out = out.merge(ctxg, left_on="date", right_index=True, how="left")
    for h in HORIZONS:
        mean_y = out.groupby("date")[f"y{h}"].transform("mean")
        out[f"yx{h}"] = (out[f"y{h}"] - mean_y).astype("float32")
    out["stock_id"] = out["stock_id"].astype(str)
    return out, dates
