"""Pure current-price consistency checks; no interim switch, transport or model policy."""
from __future__ import annotations
from typing import Any, Dict, List

IDENTITY_REL_TOL = 1e-3
MAX_STAMP_VALUES = 3
CHECKS = ("vintage_stale", "basis_break", "placeholder_noncarry", "listing_day_raw_row", "missing_rows")
# Reported in by_check/examples/version_labels but never a reason for exclusion.
LABEL_CHECKS = frozenset({"vintage_stale"})

class PriceSeriesQualityError(ValueError):
    """Current price provenance cannot be checked without inventing a vintage."""

def series_checks(price: Any, board4: Any, D: Any, *, expected_sessions: Any = None) -> Dict[str, Any]:
    """Per-series consistency checks on the current published price rows <= D.

    ``price``: stock_id, date, open, close, spread, Trading_Volume, _shared_collected_at (all securities, any
    dates); ``board4``: official board rows of 4-digit codes (stock_id, date, market, close, volume), deduplicated.
    ``expected_sessions``: optional verified market-session axis. A missing session makes a
    previous-close/quote-anchor comparison unavailable, without adding a completeness gate.
    Returns {"excluded": set, "by_check": {check: set}, "reasons": {stock: [checks]}, "examples": {...},
    "stamps": {...}}."""
    import numpy as np
    import pandas as pd

    D = pd.Timestamp(D).normalize()
    px = price.loc[price["date"] <= D, ["stock_id", "date", "open", "close", "spread", "Trading_Volume",
                                        "_shared_collected_at"]]
    px = px.drop_duplicates(["stock_id", "date"], keep="last").sort_values(["stock_id", "date"], kind="stable")
    px = px.reset_index(drop=True)
    by_check: Dict[str, set] = {name: set() for name in CHECKS}
    examples: Dict[str, List[Any]] = {name: [] for name in CHECKS}

    def flag(name: str, mask: Any) -> None:
        idx = np.flatnonzero(np.asarray(mask, bool))
        if not len(idx):
            return
        sids = px["stock_id"].to_numpy(dtype=object)[idx]
        by_check[name].update(str(s) for s in sids)
        for i in idx[:12]:
            if len(examples[name]) < 12:
                examples[name].append([str(px.at[i, "stock_id"]), px.at[i, "date"].date().isoformat()])

    # vintage: one stamp per producing run (fbb9fb50: two values, no null)
    stamps = px["_shared_collected_at"]
    if stamps.isna().any():
        raise PriceSeriesQualityError("price_series_stamps_unrecognized:stamp_null")
    values = stamps.astype(str)
    distinct = sorted(values.unique())
    version_labels: List[Dict[str, Any]] = []
    if len(distinct) > MAX_STAMP_VALUES:
        # Many collection batches (accumulated partial updates) are not a data
        # error: record the count and the latest stamp, analyse the rows read.
        version_labels.append({"code": "price_series_stamp_values_exceed_expected",
                               "recorded": MAX_STAMP_VALUES, "actual": len(distinct),
                               "detail": {"latest": max(distinct), "earliest": min(distinct)}})
    latest_stamp = max(distinct)
    per_stock_max = values.groupby(px["stock_id"]).max()
    stale = per_stock_max.index[per_stock_max.to_numpy() < latest_stamp]
    # vintage_stale is a freshness label (the security's own latest stamp is
    # older than the frame's): it is recorded with that stamp, never excluded.
    by_check["vintage_stale"].update(str(s) for s in stale)
    examples["vintage_stale"] = [[str(s), str(per_stock_max[s])] for s in list(stale)[:12]]
    if len(stale):
        version_labels.append({"code": "price_series_vintage_stale", "recorded": latest_stamp,
                               "actual": len(stale), "detail": {"examples": examples["vintage_stale"]}})
    sid = px["stock_id"].to_numpy(dtype=object)
    same = np.zeros(len(px), bool)
    if len(px) > 1:
        same[1:] = sid[1:] == sid[:-1]
    first = ~same
    adjacent = same.copy()
    if expected_sessions is not None:
        sessions = pd.DatetimeIndex(pd.to_datetime(expected_sessions)).normalize()
        positions = sessions.get_indexer(pd.DatetimeIndex(px["date"]))
        previous_positions = np.r_[-1, positions[:-1]]
        adjacent &= (positions >= 0) & (previous_positions >= 0) & (positions == previous_positions + 1)
        gaps = np.flatnonzero(same & ~adjacent)
        if len(gaps):
            gap_examples = []
            for i in gaps[:12]:
                start, end = previous_positions[i], positions[i]
                missing = sessions[start + 1:end] if start >= 0 and end > start else []
                gap_examples.append({"stock_id": str(sid[i]),
                                     "previous_date": px.at[i - 1, "date"].date().isoformat(),
                                     "date": px.at[i, "date"].date().isoformat(),
                                     "missing_sessions": [day.date().isoformat() for day in missing]})
            version_labels.append({"code": "price_series_comparison_gap",
                                   "recorded": "adjacent_expected_market_sessions", "actual": int(len(gaps)),
                                   "detail": {"examples": gap_examples}})
    close = px["close"].to_numpy(float)
    spread = px["spread"].to_numpy(float)
    opened = px["open"].to_numpy(float)
    volume = px["Trading_Volume"].to_numpy(float)
    prev = np.r_[np.nan, close[:-1]]
    # official board row of the same (stock, date): a traded day has close > 0 and volume > 0
    key = pd.MultiIndex.from_arrays([px["stock_id"].astype(str), px["date"]])
    official = board4.set_index(["stock_id", "date"])
    off_close = official["close"].reindex(key).to_numpy(float)
    off_volume = official["volume"].reindex(key).to_numpy(float)
    off_traded = np.isfinite(off_close) & (off_close > 0) & np.isfinite(off_volume) & (off_volume > 0)
    # A present null quote is different from an absent official observation.
    known_no_quote = np.asarray(key.isin(official.index), bool) & np.isnan(off_close)
    basis_prev = prev.copy()
    basis_comparable = adjacent.copy()
    anchored_no_quote = np.zeros(len(px), bool)
    unverified_anchor = np.zeros(len(px), bool)
    if known_no_quote.any():
        quoted_close = np.nan
        crossing_no_quote = False
        for i in range(len(px)):
            if first[i] or (expected_sessions is not None and not adjacent[i]):
                quoted_close = np.nan
                crossing_no_quote = False
            if known_no_quote[i]:
                anchored_no_quote[i] = np.isfinite(quoted_close) and quoted_close > 0
                if expected_sessions is not None and not anchored_no_quote[i]:
                    basis_comparable[i] = False
                    unverified_anchor[i] = same[i]
                crossing_no_quote = anchored_no_quote[i] if expected_sessions is None else True
                continue
            if off_traded[i] and crossing_no_quote:
                # Use the same adjusted series, never the unadjusted board close.
                if expected_sessions is None or (np.isfinite(quoted_close) and quoted_close > 0):
                    basis_prev[i] = quoted_close
                else:
                    basis_comparable[i] = False
                    unverified_anchor[i] = same[i]
            quoted_close = close[i] if off_traded[i] and np.isfinite(close[i]) and close[i] > 0 else np.nan
            crossing_no_quote = False
    if unverified_anchor.any():
        version_labels.append({"code": "price_series_noquote_anchor_unverified",
                               "recorded": "continuous_observed_quote_anchor",
                               "actual": int(unverified_anchor.sum()),
                               "detail": {"examples": [[str(sid[i]), px.at[i, "date"].date().isoformat()]
                                                       for i in np.flatnonzero(unverified_anchor)[:12]]}})
    with np.errstate(divide="ignore", invalid="ignore"):
        basis_rel = np.abs(basis_prev - (close - spread)) / np.abs(basis_prev)
    invalid = same & (~np.isfinite(close) | ~np.isfinite(spread) | ~np.isfinite(prev) | (prev == 0))
    basis_invalid = invalid | (same & (~np.isfinite(basis_prev) | (basis_prev == 0)))
    # An unavailable comparison cannot hide an invalid value on the observed row.
    current_invalid = (~np.isfinite(close) | (close <= 0) | ((same | known_no_quote) & ~np.isfinite(spread))
                       if expected_sessions is not None else np.zeros(len(px), bool))
    basis_violation = current_invalid | (basis_comparable & (basis_invalid | (basis_rel > IDENTITY_REL_TOL)))
    # Spread has no quoted-close identity on a verified no-quote day. Invalid
    # values and an unknown previous quote cannot acquire this exemption.
    basis_violation &= ~(anchored_no_quote & ~basis_invalid & (close > 0))
    no_quote_invalid = known_no_quote & (~np.isfinite(close) | ~np.isfinite(spread) | (close <= 0))
    carry = adjacent & np.isfinite(close) & np.isfinite(prev) & (np.abs(close - prev) <= 1e-9 * np.maximum(1.0, np.abs(prev)))
    # a carried no-trade day (no official traded row) may carry a meaningless spread: not a series defect
    basis_ignorable = basis_violation & carry & ~off_traded & ~known_no_quote & ~current_invalid
    second = same & np.r_[False, first[:-1]]                 # the second own row of a security
    listing = first & (opened == 0)
    listing_break = second & basis_violation & ~basis_ignorable
    listing_rows = listing.copy()
    listing_rows[np.flatnonzero(listing_break) - 1] = True
    flag("listing_day_raw_row", listing_rows)
    flag("basis_break", (basis_violation & ~basis_ignorable & ~second) | no_quote_invalid | current_invalid)
    flag("placeholder_noncarry", adjacent & (volume == 0) & ~carry)
    # missing rows: official traded sessions between the first own price row and D without a price row
    first_dates = px.loc[first, ["stock_id", "date"]].set_index("stock_id")["date"]
    traded = board4.loc[(board4["date"] <= D) & (board4["volume"] > 0) & (board4["close"] > 0),
                        ["stock_id", "date"]]
    traded = traded.loc[traded["stock_id"].isin(first_dates.index)]
    traded = traded.loc[traded["date"].to_numpy() >= first_dates.reindex(traded["stock_id"]).to_numpy()]
    price_sessions = pd.DatetimeIndex(np.sort(px["date"].unique()))
    traded = traded.loc[traded["date"].isin(price_sessions)]
    have = pd.MultiIndex.from_arrays([px["stock_id"].astype(str), px["date"]])
    want = pd.MultiIndex.from_arrays([traded["stock_id"].astype(str), traded["date"]])
    missing = traded.loc[~want.isin(have)]
    by_check["missing_rows"].update(missing["stock_id"].astype(str))
    examples["missing_rows"] = [[str(s), d.date().isoformat()] for s, d in
                                zip(missing["stock_id"].head(12), missing["date"].head(12))]
    reasons: Dict[str, List[str]] = {}
    for name in CHECKS:
        if name in LABEL_CHECKS:
            continue
        for stock in by_check[name]:
            reasons.setdefault(stock, []).append(name)
    excluded = set(reasons)
    stock_latest = {str(k): str(v) for k, v in per_stock_max.items()}
    return {"excluded": excluded, "by_check": by_check, "reasons": {k: sorted(v) for k, v in sorted(reasons.items())},
            "examples": examples,
            "stamps": {"values": distinct, "count": len(distinct), "latest": latest_stamp,
                       "stale_securities": int(len(stale)), "stock_latest": stock_latest},
            # Latest collection stamp actually read; contract passes it to data_time.price_collected_at_max.
            "price_collected_at_max": latest_stamp,
            "version_labels": version_labels}
