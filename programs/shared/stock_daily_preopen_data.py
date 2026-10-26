"""Read-only loaders for the pre-open model: central content-addressed objects -> DataFrames.

* Reads published D:\\TWStockData\\shared_market_data without changing its data/head; reader leases
  use the shared query lifecycle registry. The current head is pinned once
  per run (sha256 of _metadata\\stock_data_current.json); immutable objects are sha-verified.
* Prices: the 5-year price objects of the preserved head HISTORIC_HEAD are stitched with the
  current head (same rule as the validated research builder build_data.py, incl. the fix that
  keeps delisted stocks), because the current head only retains ~18 months of prices.
* Parsed objects are cached as parquet under <root>\\cache\\objects keyed by object sha.
* US session helpers decide whether last night's US close is present ('ok'), there was no US
  session ('holiday'), or the central data is missing it ('missing').
"""
from __future__ import annotations

import contextlib
import datetime as dt
import gc
import hashlib
import json
import os
import re
import tempfile
from pathlib import Path

import numpy as np
import pandas as pd

import stock_cache_lifecycle as cache_lifecycle

CENTRAL = Path(r"D:\TWStockData\shared_market_data")
HISTORIC_HEAD = cache_lifecycle.PREOPEN_HISTORIC_HEAD
PRICE_COLS = ["date", "stock_id", "open", "high", "low", "close", "Trading_Volume", "Trading_money"]
OFFICIAL_COLS = ["date", "stock_id", "market", "open", "high", "low", "close", "volume", "amount", "transactions", "name"]
US_GATE_INSTRUMENTS = ("us_sp500", "us_semiconductor", "us_nasdaq")

# NYSE full-day closures (source: NYSE holiday calendar as published for 2025-2027; Jan 9 2025 was the
# national day of mourning). Used only to tell a US holiday from missing central data.
NYSE_HOLIDAYS = {
    dt.date(2025, 1, 1), dt.date(2025, 1, 9), dt.date(2025, 1, 20), dt.date(2025, 2, 17), dt.date(2025, 4, 18),
    dt.date(2025, 5, 26), dt.date(2025, 6, 19), dt.date(2025, 7, 4), dt.date(2025, 9, 1), dt.date(2025, 11, 27),
    dt.date(2025, 12, 25),
    dt.date(2026, 1, 1), dt.date(2026, 1, 19), dt.date(2026, 2, 16), dt.date(2026, 4, 3), dt.date(2026, 5, 25),
    dt.date(2026, 6, 19), dt.date(2026, 7, 3), dt.date(2026, 9, 7), dt.date(2026, 11, 26), dt.date(2026, 12, 25),
    dt.date(2027, 1, 1), dt.date(2027, 1, 18), dt.date(2027, 2, 15), dt.date(2027, 3, 26), dt.date(2027, 5, 31),
    dt.date(2027, 6, 18), dt.date(2027, 7, 5), dt.date(2027, 9, 6), dt.date(2027, 11, 25), dt.date(2027, 12, 24),
}


class PreopenDataError(RuntimeError):
    pass


def _sha256_bytes(raw):
    return hashlib.sha256(raw).hexdigest()


class _LeasedRoutes(dict):
    """A dict-compatible pinned head whose source files stay live while in use."""
    def __init__(self, values, lease):
        super().__init__(values)
        self._reader_lease = lease

    def close(self):
        if self._reader_lease is not None:
            self._reader_lease.close()
            self._reader_lease = None

    def __del__(self):
        with contextlib.suppress(OSError, AttributeError):
            self.close()


def read_head(sha=None, central=CENTRAL):
    """Pin the selected head and its payloads before the GC can retire them."""
    root = Path(central)
    registry = cache_lifecycle.source_registry(root)
    with cache_lifecycle.catalog_lock(registry):
        if sha is None:
            raw = (root / "_metadata" / "stock_data_current.json").read_bytes()
            sha = _sha256_bytes(raw)
        else:
            raw = (root / "_manifests" / f"{sha}.json").read_bytes()
            if _sha256_bytes(raw) != sha:
                raise PreopenDataError(f"manifest sha mismatch for head {sha}")
        manifest = json.loads(raw)
        routes = {d.get("route_id"): d for d in manifest.get("datasets", [])}
        wanted = {"finmind_price_adj", "official_market_daily"} if sha == HISTORIC_HEAD else set(routes)
        refs = [dict(root=str(root), ref={"ref": entry["path"], "sha256": entry["sha256"],
                    "size": entry["size"]}, expand=False, route_ids=[route])
                for route, entry in routes.items() if route in wanted]
        archive = root / "_manifests" / (sha + ".json")
        if archive.is_file():
            refs.append(dict(root=str(root), ref={"ref": archive.relative_to(root).as_posix(),
                "sha256": sha, "size": len(raw)}, expand=False))
        lease = cache_lifecycle.CacheLease(registry, "source-" + sha,
            physical_refs=refs, already_locked=True)
        return sha, _LeasedRoutes(routes, lease), manifest


def live_shared_data_references(shared_root=CENTRAL):
    return cache_lifecycle.live_shared_data_references(shared_root)


def prune_preopen_cache(cache_dir, *, central=CENTRAL, retained_objects=(), retained_cache_refs=(), apply=False):
    """Retire only superseded, rebuildable parquet files; dry-run is read-only."""
    root, objects = Path(cache_dir), Path(cache_dir) / "objects"
    report = dict(apply=bool(apply), removed=[], candidates=[], retained=[], unknown=[], stale_leases=[])
    with contextlib.ExitStack() as locks:
        if apply:
            locks.enter_context(cache_lifecycle.catalog_lock(cache_lifecycle.source_registry(central)))
            locks.enter_context(cache_lifecycle.catalog_lock(root))
        try:
            keep = set(retained_objects) | cache_lifecycle.publication_objects(central)
            historic, _ = cache_lifecycle.publication_metadata(central, HISTORIC_HEAD)
            keep.update(entry["sha256"] for entry in historic["datasets"]
                if entry.get("route_id") in {"finmind_price_adj", "official_market_daily"})
        except (OSError, ValueError, KeyError, TypeError) as exc:
            report["unknown"].append(dict(path=str(central), reason=str(exc)))
            return report
        active, unknown, stale = cache_lifecycle.active_leases(root, apply=apply)
        records, source_unknown, source_stale = cache_lifecycle.active_lease_records(
            cache_lifecycle.source_registry(central), apply=apply)
        for record in records:
            for reference in record.get("physical_refs", ()):
                claim = reference.get("ref") if isinstance(reference, dict) else None
                if (isinstance(claim, dict) and isinstance(claim.get("sha256"), str)
                        and os.path.normcase(str(Path(reference.get("root", "")).resolve()))
                            == os.path.normcase(str(Path(central).resolve()))):
                    keep.add(claim["sha256"])
        report["unknown"].extend(unknown + source_unknown)
        report["stale_leases"] = stale + source_stale
        retained_names = set()
        for reference in retained_cache_refs:
            raw = reference.get("path", reference.get("ref")) if isinstance(reference, dict) else reference
            if isinstance(raw, dict):
                raw = raw.get("path", raw.get("ref"))
            if isinstance(raw, str):
                retained_names.add(Path(raw).name)
        if not objects.exists():
            return report
        for path in objects.iterdir():
            match = re.fullmatch(r"([0-9a-f]{64})_[0-9a-f]{10}\.parquet", path.name)
            if not match or not path.is_file():
                report["unknown"].append(dict(path=str(path), reason="cache_item_not_owned"))
                continue
            if match[1] in keep or path.name in active or path.name in retained_names:
                report["retained"].append(str(path))
                continue
            report["candidates"].append(str(path))
            if apply:
                try:
                    cache_lifecycle.unlink_cache_file(root, path)
                    report["removed"].append(str(path))
                except OSError as exc:
                    report["unknown"].append(dict(path=str(path), reason=str(exc)))
    return report


LAST_CACHE_PRUNE_REPORT = None


def load_object(entry, cols, cache_dir, central=CENTRAL):
    """Reuse one object/column cache and retire obsolete versions after atomic commit."""
    global LAST_CACHE_PRUNE_REPORT
    sha = entry["sha256"]
    sig = hashlib.sha256(json.dumps(cols).encode()).hexdigest()[:10]
    cache_dir = Path(cache_dir)
    cache = cache_dir / "objects" / f"{sha}_{sig}.parquet"
    source_ref = dict(root=str(central), ref={"ref": entry["path"], "sha256": sha, "size": entry["size"]},
                      expand=False, route_ids=[entry.get("route_id")])
    with cache_lifecycle.CacheLease(cache_lifecycle.source_registry(central), "source-" + sha,
            physical_refs=[source_ref]), cache_lifecycle.CacheLease(cache_dir, cache.name):
        if cache.exists():
            return pd.read_parquet(cache)
        with cache_lifecycle.file_lock(cache_dir / "locks" / (cache.name + ".build.lock")):
            if cache.exists():
                return pd.read_parquet(cache)
            path = central / entry["path"]
            if not path.suffix:
                path = path.with_suffix(".json")
            if not path.exists():
                path = central / "_objects" / f"{sha}.json"
            raw = path.read_bytes()
            if _sha256_bytes(raw) != sha:
                raise PreopenDataError(f"object sha mismatch: {path}")
            data = json.loads(raw)
            del raw
            df = pd.DataFrame.from_records(data, columns=cols)
            del data
            gc.collect()
            cache.parent.mkdir(parents=True, exist_ok=True)
            fd, temporary = tempfile.mkstemp(prefix=cache.name + ".", suffix=".tmp", dir=cache.parent)
            os.close(fd)
            try:
                df.to_parquet(temporary, index=False)
                with cache_lifecycle.catalog_lock(cache_dir):
                    os.replace(temporary, cache)
            finally:
                if os.path.exists(temporary):
                    os.unlink(temporary)
        try:
            LAST_CACHE_PRUNE_REPORT = prune_preopen_cache(cache_dir, central=central, apply=True)
        except (OSError, ValueError, TypeError, KeyError) as exc:
            LAST_CACHE_PRUNE_REPORT = dict(apply=True, removed=[], candidates=[], retained=[],
                unknown=[dict(path=str(cache_dir), reason=str(exc))], stale_leases=[])
        return df


def _ordinary(ids):
    return ids.astype(str).str.fullmatch(r"[1-9]\d{3}")


def _to_date(s):
    return pd.to_datetime(s.astype(str), format="%Y%m%d")


def _route(routes, route_id, head):
    if route_id not in routes:
        raise PreopenDataError(f"route {route_id} missing in head {head[:12]}")
    return routes[route_id]


def build_prices(cur_routes, hist_routes, cache_dir, cur_sha, hist_sha):
    old = load_object(_route(hist_routes, "finmind_price_adj", hist_sha), PRICE_COLS, cache_dir)
    new = load_object(_route(cur_routes, "finmind_price_adj", cur_sha), PRICE_COLS, cache_dir)
    old, new = old.copy(), new.copy()
    for df in (old, new):
        df["stock_id"] = df["stock_id"].astype(str)
    old = old[_ordinary(old["stock_id"])].copy()
    new = new[_ordinary(new["stock_id"])].copy()
    old["date"] = _to_date(old["date"])
    new["date"] = _to_date(new["date"])
    for df in (old, new):
        for c in ["open", "high", "low", "close", "Trading_Volume", "Trading_money"]:
            df[c] = pd.to_numeric(df[c], errors="coerce")
        df.drop_duplicates(["stock_id", "date"], keep="last", inplace=True)
    ov = old.merge(new[["stock_id", "date", "close"]], on=["stock_id", "date"], suffixes=("", "_new"))
    ov = ov[(ov["close"] > 0) & (ov["close_new"] > 0)].sort_values(["stock_id", "date"])
    ov["ratio"] = ov["close_new"] / ov["close"]
    first = ov.groupby("stock_id").head(5).groupby("stock_id")["ratio"].median().rename("scale")
    old = old.merge(first, on="stock_id", how="left")
    old["scale"] = old["scale"].fillna(1.0)
    # Presence wins per key, including a current value rejected by the final guard.
    current_keys = pd.MultiIndex.from_frame(new[["stock_id", "date"]])
    pre = old[~pd.MultiIndex.from_frame(old[["stock_id", "date"]]).isin(current_keys)].copy()
    for c in ["open", "high", "low", "close"]:
        pre[c] = pre[c] * pre["scale"]
    pre.drop(columns=["scale"], inplace=True)
    adj = pd.concat([pre, new], ignore_index=True)
    adj = adj.drop_duplicates(["stock_id", "date"], keep="last").sort_values(["stock_id", "date"])
    adj = adj.rename(columns={"open": "adj_open", "high": "adj_high", "low": "adj_low", "close": "adj_close",
                              "Trading_Volume": "fm_volume", "Trading_money": "fm_amount"})
    o_old = load_object(_route(hist_routes, "official_market_daily", hist_sha), OFFICIAL_COLS, cache_dir).copy()
    o_new = load_object(_route(cur_routes, "official_market_daily", cur_sha), OFFICIAL_COLS, cache_dir).copy()
    o_old["stock_id"] = o_old["stock_id"].astype(str)
    o_new["stock_id"] = o_new["stock_id"].astype(str)
    # Select before value/market filtering so invalid current keys cannot revive old rows.
    old_keys = pd.MultiIndex.from_frame(o_old[["stock_id", "date"]].astype(str))
    new_keys = pd.MultiIndex.from_frame(o_new[["stock_id", "date"]].astype(str))
    off = pd.concat([o_old[~old_keys.isin(new_keys)], o_new], ignore_index=True)
    off = off[_ordinary(off["stock_id"]) & off["market"].isin(["TWSE", "TPEx"])].copy()
    off["date"] = _to_date(off["date"])
    for c in ["open", "high", "low", "close", "volume", "amount", "transactions"]:
        off[c] = pd.to_numeric(off[c], errors="coerce")
    off = off.drop_duplicates(["stock_id", "date"], keep="last")
    off = off.rename(columns={"open": "raw_open", "high": "raw_high", "low": "raw_low", "close": "raw_close"})
    panel = adj.merge(off, on=["stock_id", "date"], how="inner")
    panel = panel[(panel["adj_close"] > 0) & (panel["raw_close"] > 0)]
    return panel.sort_values(["stock_id", "date"]).reset_index(drop=True)


def build_chips(routes, cache_dir, sha):
    def part(route_id, cols, agg=None):
        df = load_object(_route(routes, route_id, sha), cols, cache_dir).copy()
        df["stock_id"] = df["stock_id"].astype(str)
        df = df[_ordinary(df["stock_id"])]
        df["date"] = _to_date(df["date"])
        if agg == "sum":
            return df.groupby(["stock_id", "date"], as_index=False).sum(numeric_only=True)
        return df.drop_duplicates(["stock_id", "date"], keep="last")

    inst = part("finmind_institutional", ["date", "stock_id", "foreign_investors_net", "investment_trust_net", "dealer_net", "total_net"], "sum")
    mar = part("finmind_margin", ["date", "stock_id", "margin_purchase_balance", "short_sale_balance",
                                  "margin_purchase_change", "short_sale_change", "MarginPurchaseLimit"])
    dtr = part("finmind_day_trading", ["date", "stock_id", "day_trade_volume", "BuyAmount"])
    per = part("finmind_per", ["date", "stock_id", "per", "pbr", "dividend_yield"])
    sh = part("finmind_shareholding", ["date", "stock_id", "holding_percentage", "NumberOfSharesIssued"])
    chips = inst.merge(mar, on=["stock_id", "date"], how="outer")
    for other in (dtr, per, sh):
        chips = chips.merge(other, on=["stock_id", "date"], how="outer")
    for c in chips.columns:
        if c not in ("stock_id", "date"):
            chips[c] = pd.to_numeric(chips[c], errors="coerce").astype("float32")
    return chips


def build_misc(routes, cache_dir, sha):
    rev = load_object(_route(routes, "finmind_month_revenue", sha), ["stock_id", "revenue", "revenue_year", "revenue_month"], cache_dir).copy()
    rev["stock_id"] = rev["stock_id"].astype(str)
    rev = rev[_ordinary(rev["stock_id"])]
    for c in ["revenue", "revenue_year", "revenue_month"]:
        rev[c] = pd.to_numeric(rev[c], errors="coerce")
    rev = rev.dropna().drop_duplicates(["stock_id", "revenue_year", "revenue_month"], keep="last")
    info = load_object(_route(routes, "finmind_stock_info", sha), ["stock_id", "industry_category", "market", "date"], cache_dir).copy()
    info["stock_id"] = info["stock_id"].astype(str)
    info = info[_ordinary(info["stock_id"])].sort_values("date")
    info["date"] = _to_date(info["date"])
    g = load_object(_route(routes, "global_market_history", sha), ["date", "instrument", "adj_close", "close"], cache_dir).copy()
    g["date"] = _to_date(g["date"])
    g["px"] = pd.to_numeric(g["adj_close"], errors="coerce").fillna(pd.to_numeric(g["close"], errors="coerce"))
    g = g.dropna(subset=["px"]).drop_duplicates(["instrument", "date"], keep="last")
    gw = g.pivot(index="date", columns="instrument", values="px").sort_index()
    return rev, info[["stock_id", "date", "industry_category", "market"]], gw


def _coverage_counts(entry):
    """``metadata.coverage`` 的 success／total 計數（只取整數，不帶逐檔明細；單位依中央擷取範圍而異）；缺少時 None。"""
    cov = (entry.get("metadata") or {}).get("coverage")
    if not isinstance(cov, dict):
        return None
    out = {}
    for key in ("success", "total"):
        value = cov.get(key)
        out[key] = int(value) if isinstance(value, (int, float)) and not isinstance(value, bool) else None
    return out if any(v is not None for v in out.values()) else None


def load_inputs(cache_dir, head_sha=None, historic_sha=HISTORIC_HEAD, central=CENTRAL):
    cur_sha, cur_routes, cur_manifest = read_head(head_sha, central)
    hist_sha, hist_routes, _ = read_head(historic_sha, central)
    px = build_prices(cur_routes, hist_routes, cache_dir, cur_sha, hist_sha)
    ch = build_chips(cur_routes, cache_dir, cur_sha)
    rv, info, g = build_misc(cur_routes, cache_dir, cur_sha)
    used = ["finmind_price_adj", "official_market_daily", "finmind_institutional", "finmind_margin", "finmind_day_trading",
            "finmind_per", "finmind_shareholding", "finmind_month_revenue", "finmind_stock_info", "global_market_history"]
    provenance = {
        "central_head_sha256": cur_sha, "central_published_at": cur_manifest.get("published_at"),
        "central_run_id": cur_manifest.get("run_id"), "historic_head_sha256": hist_sha,
        "objects": {r: {"sha256": cur_routes[r]["sha256"], "status": cur_routes[r].get("status"),
                        "actual_max_date": cur_routes[r].get("actual_max_date"),
                        "coverage": _coverage_counts(cur_routes[r])} for r in used},
        "historic_objects": {r: hist_routes[r]["sha256"] for r in ("finmind_price_adj", "official_market_daily")},
    }
    return {"px": px, "ch": ch, "rv": rv, "info": info, "g": g, "provenance": provenance}


# ---------------------------------------------------------------- calendars
def is_us_session(day):
    return day.weekday() < 5 and day not in NYSE_HOLIDAYS


def expected_us_session(cutoff):
    """Last NYSE session dated <= cutoff (a date)."""
    day = cutoff
    for _ in range(15):
        if is_us_session(day):
            return day
        day -= dt.timedelta(days=1)
    raise PreopenDataError(f"no US session found before {cutoff}")


def next_weekday(day):
    nxt = day + dt.timedelta(days=1)
    while nxt.weekday() >= 5:
        nxt += dt.timedelta(days=1)
    return nxt


def us_overnight_status(g, signal_date, target_date):
    """Classify the US information between the TW close on signal_date and the open on target_date."""
    prev_cut = signal_date - dt.timedelta(days=1)
    cut = target_date - dt.timedelta(days=1)
    exp_prev = expected_us_session(prev_cut)
    exp_now = expected_us_session(cut)
    latest = {}
    for inst in US_GATE_INSTRUMENTS:
        s = g[inst].dropna() if inst in g.columns else pd.Series(dtype=float)
        s = s[s.index <= pd.Timestamp(cut)]
        latest[inst] = s.index.max().date() if len(s) else None
    have = [d for d in latest.values() if d is not None]
    latest_us = min(have) if len(have) == len(US_GATE_INSTRUMENTS) else None
    if exp_now <= exp_prev:
        status = "holiday"
    elif latest_us is not None and latest_us >= exp_now:
        status = "ok"
    else:
        status = "missing"
    return {"status": status, "expected_us_session": exp_now.isoformat(), "previous_us_session": exp_prev.isoformat(),
            "latest_us_session": latest_us.isoformat() if latest_us else None,
            "latest_by_instrument": {k: (v.isoformat() if v else None) for k, v in latest.items()}}
