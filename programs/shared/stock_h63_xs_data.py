"""Read-only input layer of the H63 U7 model series (ADR-0009): pinned central head, sha-verified objects, the
program-owned price supplement and the verified calendar.

* The central store D:\\TWStockData\\shared_market_data is never written; the head is pinned once per run under the
  shared catalog lock and a reader lease (stock_cache_lifecycle.CacheLease) keeps its objects alive while in use.
* Large JSON array objects are parsed by pyarrow in one streaming pass that also verifies sha256, size and record
  count; any parse problem stops the run (no whole-file json.loads fallback: a 1.6 GB object would not fit).
* Parsed columns are cached as <root>\\cache\\objects\\<object sha>_<column signature>.parquet (rebuildable; at most
  two versions per signature are kept, only inside our own root).
* The price supplement (a807 prices and boards, 20211004..20261008) is pinned by the sha256 of every file.
"""
from __future__ import annotations

import contextlib
import gc
import hashlib
import io
import json
import os
import platform
import re
import tempfile
import time
from pathlib import Path
from typing import Any, Dict, List, Optional, Sequence, Tuple

import numpy as np
import pandas as pd
import pyarrow as pa
import pyarrow.compute as pc
import pyarrow.json as pj
import pyarrow.parquet as pq

import stock_cache_lifecycle as cache_lifecycle
import stock_h63_xs_core as core

CENTRAL = Path(r"D:\TWStockData\shared_market_data")
HISTORIC_HEAD = "a807be3ea33b4acb2d076ba763755dc37b51ca029fd74ed2d7771f5d07cc8ffd"
REQUIRED_ROUTES = ("finmind_price_adj", "official_market_daily", "finmind_per", "finmind_month_revenue")
OPTIONAL_ROUTES = ("official_finance_reporting_deadline",)
CALENDAR_ROUTE = "official_calendar"
F, S = pa.float64(), pa.string()
COLUMNS: Dict[str, Dict[str, Any]] = {
    "finmind_price_adj": {"date": S, "stock_id": S, "open": F, "high": F, "low": F, "close": F,
                          "Trading_Volume": F, "Trading_money": F},
    "official_market_daily": {"date": S, "stock_id": S, "market": S, "close": F},
    "finmind_per": {"date": S, "stock_id": S, "per": F, "pbr": F},
}
SMALL_JSON_ROUTES = ("finmind_month_revenue", "official_finance_reporting_deadline")
CHUNK = 64 << 20
SUPPLEMENT_SCHEMA = "tw-stock-h63-xs-price-supplement-v1"
SUPPLEMENT_FILES = ("price_hist.parquet", "board_hist.parquet")
MIN_BOARD_ROWS = {"TWSE": 773, "TPEx": 643}
MIN_BOARD_RATIO = 0.79
MAX_MISSING_PRICE_SHARE = 0.01
CACHE_VERSIONS_KEPT = 2


class H63XsDataError(RuntimeError):
    """code: machine reason; exit_code: 3 data not ready, 4 integrity / gate, 5 IO."""

    def __init__(self, code: str, detail: str = "", exit_code: int = 3):
        super().__init__(code + (": " + detail if detail else ""))
        self.code = code
        self.detail = detail
        self.exit_code = exit_code


def sha256_bytes(raw: bytes) -> str:
    return hashlib.sha256(raw).hexdigest()


def sha256_file(path: Any) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as handle:
        for block in iter(lambda: handle.read(16 << 20), b""):
            h.update(block)
    return h.hexdigest()


def column_signature(columns: Sequence[str]) -> str:
    return hashlib.sha256(json.dumps(list(columns)).encode()).hexdigest()[:10]


# ------------------------------------------------------------------------------------------------------------------
# head pin + lease
# ------------------------------------------------------------------------------------------------------------------
class PinnedHead:
    """Pinned head: sha, publication fields, compact route entries and the reader lease (close() releases it)."""

    def __init__(self, sha: str, top: Dict[str, Any], routes: Dict[str, Dict[str, Any]],
                 calendar: Optional[Dict[str, Any]], lease: Any, central: Path):
        self.sha256, self.top, self.routes, self.calendar, self.central = sha, top, routes, calendar, Path(central)
        self._lease = lease

    @property
    def published_at(self) -> Optional[str]:
        return self.top.get("published_at")

    def close(self) -> None:
        if self._lease is not None:
            with contextlib.suppress(OSError):
                self._lease.close()
            self._lease = None

    def __enter__(self):
        return self

    def __exit__(self, *_):
        self.close()


ENTRY_FIELDS = ("route_id", "path", "sha256", "size", "record_count", "status", "actual_min_date", "actual_max_date")


def pin_head(central: Any = CENTRAL, sha: Optional[str] = None, *, lease: bool = True,
             routes: Sequence[str] = REQUIRED_ROUTES + OPTIONAL_ROUTES + (CALENDAR_ROUTE,)) -> PinnedHead:
    root = Path(central)
    registry = cache_lifecycle.source_registry(root)
    with cache_lifecycle.catalog_lock(registry) if lease else contextlib.nullcontext():
        if sha is None:
            path = root / "_metadata" / "stock_data_current.json"
            st0 = path.stat()
            raw = path.read_bytes()
            st1 = path.stat()
            if (st0.st_size, st0.st_mtime_ns) != (st1.st_size, st1.st_mtime_ns):
                raise H63XsDataError("h63xs_head_unavailable", "current head changed while reading")
            sha = sha256_bytes(raw)
        else:
            raw = (root / "_manifests" / f"{sha}.json").read_bytes()
            if sha256_bytes(raw) != sha:
                raise H63XsDataError("h63xs_head_unavailable", "manifest sha mismatch " + sha[:12], 3)
        size = len(raw)
        manifest = json.loads(raw)
        del raw
        if not isinstance(manifest, dict) or not isinstance(manifest.get("datasets"), list):
            raise H63XsDataError("h63xs_head_unavailable", "manifest shape unknown")
        top = {k: manifest.get(k) for k in ("schema", "published_at", "run_id", "producer_id", "previous_sha")}
        found: Dict[str, Dict[str, Any]] = {}
        calendar = None
        for entry in manifest["datasets"]:
            rid = entry.get("route_id") if isinstance(entry, dict) else None
            if rid not in routes:
                continue
            compact = {k: entry.get(k) for k in ENTRY_FIELDS}
            meta = entry.get("metadata") or {}
            compact["reason"] = meta.get("reason")
            found[rid] = compact
            if rid == CALENDAR_ROUTE and isinstance(meta.get("verified_market_calendar"), dict):
                calendar = dict(meta["verified_market_calendar"])
        del manifest
        gc.collect()
        missing = [r for r in REQUIRED_ROUTES if r in routes and r not in found]
        if missing:
            raise H63XsDataError("h63xs_head_unavailable", "required routes missing: " + ",".join(missing))
        holder = None
        if lease:
            refs = [dict(root=str(root), ref={"ref": e["path"], "sha256": e["sha256"], "size": e["size"]},
                         expand=False, route_ids=[rid]) for rid, e in found.items() if e.get("path")]
            archive = root / "_manifests" / (sha + ".json")
            if archive.is_file():
                refs.append(dict(root=str(root), ref={"ref": archive.relative_to(root).as_posix(), "sha256": sha,
                                                      "size": size}, expand=False))
            if calendar and calendar.get("ref"):
                refs.append(dict(root=str(root), ref={"ref": calendar["ref"], "sha256": calendar.get("sha256"),
                                                      "size": calendar.get("size")}, expand=False))
            holder = cache_lifecycle.CacheLease(registry, "h63xs-" + sha, physical_refs=refs, already_locked=True)
        top["manifest_size"] = size
        return PinnedHead(sha, top, found, calendar, holder, root)


# ------------------------------------------------------------------------------------------------------------------
# streaming object reader (pyarrow NDJSON split of compact JSON arrays)
# ------------------------------------------------------------------------------------------------------------------
def _parse_piece(piece: bytes, schema: pa.Schema) -> pa.Table:
    popts = pj.ParseOptions(explicit_schema=schema, unexpected_field_behavior="ignore")
    ropts = pj.ReadOptions(block_size=16 << 20, use_threads=True)
    return pj.read_json(io.BytesIO(piece.replace(b"},{", b"}\n{")), read_options=ropts, parse_options=popts)


def stream_json_array(path: Path, columns: Dict[str, Any], *, sha256: str, size: Optional[int],
                      record_count: Optional[int]) -> Tuple[pa.Table, Dict[str, Any]]:
    schema = pa.schema(list(columns.items()))
    st0 = os.stat(path)
    tables: List[pa.Table] = []
    records, nbytes = 0, 0
    hasher = hashlib.sha256()
    t0 = time.perf_counter()
    try:
        with open(path, "rb") as handle:
            carry, first = b"", True
            while True:
                raw = handle.read(CHUNK)
                if not raw:
                    break
                hasher.update(raw)
                nbytes += len(raw)
                buf = carry + raw
                cut = buf.rfind(b"},{")
                if cut < 0:
                    carry = buf
                    continue
                piece, carry = buf[:cut + 1], buf[cut + 2:]
                if first:
                    piece = piece[piece.index(b"{"):]
                    first = False
                table = _parse_piece(piece, schema)
                records += table.num_rows
                tables.append(table)
            tail = carry.rstrip()
            if not tail.endswith(b"]"):
                raise ValueError("json array not terminated")
            tail = tail[:-1].rstrip()
            if first and tail:
                tail = tail[tail.index(b"{"):]
            if tail:
                table = _parse_piece(tail, schema)
                records += table.num_rows
                tables.append(table)
    except (ValueError, pa.ArrowInvalid, pa.ArrowTypeError) as exc:
        raise H63XsDataError("h63xs_object_invalid", "%s: %s" % (path.name, exc), 3) from exc
    st1 = os.stat(path)
    if (st0.st_size, st0.st_mtime_ns) != (st1.st_size, st1.st_mtime_ns):
        raise H63XsDataError("h63xs_object_invalid", "object changed during read: " + path.name, 3)
    digest = hasher.hexdigest()
    if digest != sha256:
        raise H63XsDataError("h63xs_object_invalid", "sha256 mismatch: " + path.name, 3)
    if size is not None and nbytes != int(size):
        raise H63XsDataError("h63xs_object_invalid", "size mismatch: " + path.name, 3)
    if record_count is not None and records != int(record_count):
        raise H63XsDataError("h63xs_object_invalid", "record count %d != %s: %s" % (records, record_count, path.name), 3)
    table = pa.concat_tables(tables) if tables else schema.empty_table()
    return table, {"sha256": digest, "bytes": nbytes, "records": records,
                   "seconds": round(time.perf_counter() - t0, 2), "parser": "pyarrow_ndjson_split"}


def _object_path(central: Path, entry: Dict[str, Any]) -> Path:
    path = central / entry["path"]
    if not path.suffix:
        path = path.with_suffix(".json")
    if not path.exists():
        path = central / "_objects" / (entry["sha256"] + ".json")
    return path


def _atomic_write_bytes(path: Path, writer) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, temporary = tempfile.mkstemp(prefix=path.name + ".", suffix=".tmp", dir=path.parent)
    os.close(fd)
    try:
        writer(temporary)
        with open(temporary, "rb+") as handle:  # Windows FlushFileBuffers needs write access
            os.fsync(handle.fileno())
        os.replace(temporary, path)
    finally:
        if os.path.exists(temporary):
            os.unlink(temporary)


def atomic_json(path: Path, value: Any) -> None:
    def write(tmp):
        with open(tmp, "w", encoding="utf-8", newline="\n") as stream:
            json.dump(value, stream, ensure_ascii=False, sort_keys=True, indent=1, allow_nan=False)
    _atomic_write_bytes(Path(path), write)


def read_route(head: PinnedHead, route: str, cache_dir: Optional[Path], *, log: Optional[Dict[str, Any]] = None
               ) -> pd.DataFrame:
    """Columns of one pinned route as a DataFrame (cache: <cache_dir>/<sha>_<sig>.parquet + .json sidecar)."""
    entry = head.routes.get(route)
    if entry is None:
        raise H63XsDataError("h63xs_head_unavailable", "route missing: " + route)
    log = {} if log is None else log
    if route in SMALL_JSON_ROUTES:
        path = _object_path(head.central, entry)
        raw = path.read_bytes()
        if sha256_bytes(raw) != entry["sha256"] or (entry.get("size") is not None and len(raw) != int(entry["size"])):
            raise H63XsDataError("h63xs_object_invalid", "sha256/size mismatch: " + route, 3)
        rows = json.loads(raw)
        if entry.get("record_count") is not None and len(rows) != int(entry["record_count"]):
            raise H63XsDataError("h63xs_object_invalid", "record count mismatch: " + route, 3)
        log[route] = {"sha256": entry["sha256"], "records": len(rows), "parser": "json"}
        return pd.DataFrame.from_records(rows)
    columns = COLUMNS[route]
    sig = column_signature(list(columns))
    cache = None if cache_dir is None else Path(cache_dir) / f"{entry['sha256']}_{sig}.parquet"
    if cache is not None and cache.exists():
        side = cache.with_suffix(".json")
        try:
            meta = json.loads(side.read_text(encoding="utf-8"))
            if meta.get("parquet_sha256") == sha256_file(cache) and meta.get("object_sha256") == entry["sha256"]:
                log[route] = {"sha256": entry["sha256"], "cache": cache.name, "parser": "parquet_cache"}
                return pd.read_parquet(cache)
        except (OSError, ValueError):
            pass
    table, info = stream_json_array(_object_path(head.central, entry), columns, sha256=entry["sha256"],
                                    size=entry.get("size"), record_count=entry.get("record_count"))
    info["route_id"] = route
    log[route] = info
    if cache is not None:
        _atomic_write_bytes(cache, lambda tmp: pq.write_table(table, tmp, compression="zstd"))
        atomic_json(cache.with_suffix(".json"), {"object_sha256": entry["sha256"], "route_id": route,
                                                 "columns": list(columns), "rows": table.num_rows,
                                                 "parquet_sha256": sha256_file(cache)})
        prune_object_cache(Path(cache_dir), sig, keep={cache.name})
    frame = table.to_pandas()
    del table
    return frame


def prune_object_cache(cache_dir: Path, sig: str, keep: Sequence[str] = ()) -> List[str]:
    """Keep the newest CACHE_VERSIONS_KEPT parquet files of one column signature (own cache only)."""
    files = sorted((p for p in cache_dir.glob(f"*_{sig}.parquet") if re.fullmatch(r"[0-9a-f]{64}_[0-9a-f]{10}\.parquet", p.name)),
                   key=lambda p: p.stat().st_mtime_ns, reverse=True)
    removed = []
    for path in files[CACHE_VERSIONS_KEPT:]:
        if path.name in keep:
            continue
        for target in (path, path.with_suffix(".json")):
            with contextlib.suppress(FileNotFoundError):
                target.unlink()
        removed.append(path.name)
    return removed


# ------------------------------------------------------------------------------------------------------------------
# verified calendar
# ------------------------------------------------------------------------------------------------------------------
def verified_calendar(head: PinnedHead) -> Dict[str, Any]:
    """{'status', 'sessions' (DatetimeIndex, TWSE = TPEx), 'verified_asof', 'sha256'}; never raises."""
    ref = head.calendar or {}
    out: Dict[str, Any] = {"status": "calendar_unavailable", "sessions": None, "verified_asof": ref.get("verified_asof"),
                           "sha256": ref.get("sha256"), "ref": ref.get("ref")}
    try:
        raw = (head.central / ref["ref"]).read_bytes()
        if sha256_bytes(raw) != ref.get("sha256"):
            out["detail"] = "calendar sha mismatch"
            return out
        doc = json.loads(raw)
        sets = []
        for market in ("TWSE", "TPEx"):
            m = (doc.get("markets") or {}).get(market) or {}
            if m.get("calendar_verified") is not True:
                out["detail"] = market + " calendar not verified"
                return out
            sets.append(pd.DatetimeIndex(pd.to_datetime([str(x) for x in m.get("sessions") or []])).normalize())
        if not sets[0].equals(sets[1]):
            out["detail"] = "TWSE and TPEx sessions differ"
            return out
        out.update(status="ok", sessions=sets[0].sort_values(), verified_asof=doc.get("verified_asof"))
    except (OSError, KeyError, TypeError, ValueError) as exc:
        out["detail"] = type(exc).__name__ + ": " + str(exc)
    return out


# ------------------------------------------------------------------------------------------------------------------
# supplement (program-owned history from the historic head a807)
# ------------------------------------------------------------------------------------------------------------------
SUPPLEMENT_PRICE_COLUMNS = {"date": S, "stock_id": S, "open": F, "high": F, "low": F, "close": F,
                            "Trading_Volume": F, "Trading_money": F, "_shared_collected_at": S}
SUPPLEMENT_BOARD_COLUMNS = {"date": S, "stock_id": S, "market": S, "open": F, "high": F, "low": F, "close": F,
                            "volume": F, "amount": F, "transactions": F, "name": S}


def build_supplement(out_dir: Any, *, central: Any = CENTRAL, historic_head: str = HISTORIC_HEAD,
                     builder_sha256: Optional[str] = None) -> Dict[str, Any]:
    """Extract every 4-digit row of the historic head's prices and boards into two parquet files + manifest.json
    (deterministic: rebuilt bytes are identical) + build_receipt.json (timings)."""
    t0 = time.perf_counter()
    out = Path(out_dir)
    out.mkdir(parents=True, exist_ok=True)
    head = pin_head(central, historic_head, routes=("finmind_price_adj", "official_market_daily"))
    files, reads = {}, {}
    try:
        for route, cols, name in (("finmind_price_adj", SUPPLEMENT_PRICE_COLUMNS, "price_hist.parquet"),
                                  ("official_market_daily", SUPPLEMENT_BOARD_COLUMNS, "board_hist.parquet")):
            entry = head.routes[route]
            table, info = stream_json_array(_object_path(head.central, entry), cols, sha256=entry["sha256"],
                                            size=entry.get("size"), record_count=entry.get("record_count"))
            table = table.filter(pc.match_substring_regex(table["stock_id"], r"^[1-9][0-9]{3}$"))
            reads[route] = info
            df = table.to_pandas()
            del table
            if not df["date"].str.fullmatch(r"\d{8}").all():
                raise H63XsDataError("h63xs_object_invalid", "unexpected date spelling in " + route, 3)
            df["date"] = pd.to_datetime(df["date"], format="%Y%m%d").dt.date
            if df.duplicated(["stock_id", "date"]).any():
                raise H63XsDataError("h63xs_object_invalid", "duplicate keys in " + route, 3)
            df = df.sort_values(["stock_id", "date"], kind="mergesort").reset_index(drop=True)
            if route == "finmind_price_adj":
                df = df.rename(columns={"_shared_collected_at": "source_collected_at"})
                for c in ("Trading_Volume", "Trading_money"):
                    v = df[c].to_numpy()
                    if np.all(np.isfinite(v) & (v == np.round(v))):
                        df[c] = v.astype(np.int64)
            tab = pa.Table.from_pandas(df, schema=pa.Schema.from_pandas(df, preserve_index=False), preserve_index=False)
            path = out / name
            pq.write_table(tab, path, compression="zstd", compression_level=9, use_dictionary=True,
                           write_statistics=True, row_group_size=1 << 20)
            checks = {"rows": int(len(df)), "codes": int(df["stock_id"].nunique()),
                      "date_min": str(df["date"].min()), "date_max": str(df["date"].max())}
            for c in ("close", "Trading_money", "volume", "amount"):
                if c in df.columns:
                    checks["sum_" + c] = float(np.nansum(df[c].to_numpy(float)))
            if "market" in df.columns:
                checks["rows_by_market"] = {str(k): int(v) for k, v in df["market"].value_counts().sort_index().items()}
            files[name] = {"sha256": sha256_file(path), "bytes": path.stat().st_size, **checks}
            del df, tab
            gc.collect()
        objects = {r: {k: head.routes[r].get(k) for k in ("path", "sha256", "size", "record_count", "status",
                                                          "actual_min_date", "actual_max_date")}
                   for r in ("finmind_price_adj", "official_market_daily")}
    finally:
        head.close()
    manifest = {"schema": SUPPLEMENT_SCHEMA,
                "purpose": "H63 U7 price/board history (20211004..20261008) independent of archived central heads",
                "source": {"central_root": str(central), "historic_head_sha256": historic_head,
                           "historic_manifest": "_manifests/%s.json" % historic_head, "objects": objects},
                "filters": {"stock_id_pattern": "^[1-9][0-9]{3}$", "rows": "every row of the source objects"},
                "date_encoding": "parquet date32 (machine field); people see YYYYMMDD",
                "files": files,
                "stitch_policy": {"id": core.STITCH_RULE_ID, "seam_date": core.SEAM_DATE,
                                  "rule": "supplement rows with date <= seam scaled per stock by the median "
                                          "head/supplement close ratio of the last 5 overlapping sessions <= seam "
                                          "(1.0 without overlap); pinned head rows after the seam; codes absent from "
                                          "the supplement use head rows only",
                                  "board": "official_market_daily of the pinned head for its keys, else board_hist",
                                  "integrity_gate": "notes only (recipe v1); the run stops when more than 1% of the "
                                                    "TWSE/TPEx price keys since 20211004 have no price"},
                "build": {"builder_sha256": builder_sha256, "python": platform.python_version(),
                          "pandas": pd.__version__, "pyarrow": pa.__version__, "numpy": np.__version__}}
    atomic_json(out / "manifest.json", manifest)
    receipt = {"manifest_sha256": sha256_file(out / "manifest.json"), "reads": reads,
               "seconds_total": round(time.perf_counter() - t0, 2)}
    atomic_json(out / "build_receipt.json", receipt)
    return {"manifest": manifest, "manifest_sha256": receipt["manifest_sha256"], "receipt": receipt}


def load_supplement(folder: Any, expected_manifest_sha256: Optional[str] = None
                    ) -> Tuple[pd.DataFrame, pd.DataFrame, Dict[str, Any]]:
    """(price rows, board rows, manifest info) after verifying the manifest sha and the sha of every file."""
    folder = Path(folder)
    manifest_path = folder / "manifest.json"
    if not manifest_path.is_file():
        raise H63XsDataError("h63xs_supplement_sha_mismatch", "supplement manifest missing: " + str(folder), 4)
    msha = sha256_file(manifest_path)
    if expected_manifest_sha256 is not None and msha != expected_manifest_sha256:
        raise H63XsDataError("h63xs_supplement_sha_mismatch", "manifest sha " + msha[:12], 4)
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    for name in SUPPLEMENT_FILES:
        spec = (manifest.get("files") or {}).get(name) or {}
        path = folder / name
        if not path.is_file() or sha256_file(path) != spec.get("sha256"):
            raise H63XsDataError("h63xs_supplement_sha_mismatch", name, 4)
    price = pd.read_parquet(folder / "price_hist.parquet",
                            columns=["date", "stock_id", "open", "high", "low", "close", "Trading_Volume",
                                     "Trading_money"])
    price["date"] = pd.to_datetime(price["date"])
    board = pd.read_parquet(folder / "board_hist.parquet", columns=["date", "stock_id", "market", "close"])
    board["date"] = pd.to_datetime(board["date"])
    return price, board, {"manifest_sha256": msha, "price_sha256": manifest["files"]["price_hist.parquet"]["sha256"],
                          "board_sha256": manifest["files"]["board_hist.parquet"]["sha256"],
                          "historic_head_sha256": (manifest.get("source") or {}).get("historic_head_sha256")}


# ------------------------------------------------------------------------------------------------------------------
# checks on the assembled inputs
# ------------------------------------------------------------------------------------------------------------------
def price_coverage(px: pd.DataFrame, board: pd.DataFrame, first: Any, last: Any) -> Dict[str, Any]:
    """Share of TWSE/TPEx board keys with a traded close (first..last) that have no stitched price."""
    first, last = pd.Timestamp(first), pd.Timestamp(last)
    b = board[(board["date"] >= first) & (board["date"] <= last) & board["market"].isin(list(core.ELIG_BOARDS))
              & (pd.to_numeric(board["close"], errors="coerce") > 0)][["stock_id", "date"]]
    have = px[(px["date"] >= first) & (px["date"] <= last) & (px["close"] > 0)][["stock_id", "date"]]
    merged = b.merge(have.assign(_p=1), on=["stock_id", "date"], how="left")
    missing = merged["_p"].isna()
    by_year = merged.loc[missing, "date"].dt.year.value_counts().sort_index()
    return {"applicable_keys": int(len(merged)), "missing_keys": int(missing.sum()),
            "missing_share": float(missing.mean()) if len(merged) else 0.0,
            "missing_by_year": {str(k): int(v) for k, v in by_year.items()}}


def board_gate(board: pd.DataFrame, px: pd.DataFrame, D: Any, previous: Any) -> Dict[str, Any]:
    """Decision-day board completeness: every 4-digit price row of D has a board row, minimum TWSE/TPEx rows,
    ratio to the previous session >= 0.79."""
    D, previous = pd.Timestamp(D), pd.Timestamp(previous) if previous is not None else None
    bd = board[board["date"] == D]
    counts = bd["market"].value_counts().to_dict()
    out: Dict[str, Any] = {"date": str(D.date()), "rows_by_market": {k: int(v) for k, v in counts.items()},
                           "ok": True, "problems": []}
    keys = set(bd["stock_id"].astype(str))
    price_codes = set(px.loc[px["date"] == D, "stock_id"].astype(str))
    no_board = sorted(price_codes - keys)
    out["price_rows_without_board"] = len(no_board)
    if no_board:
        out["problems"].append("price rows without board: " + ",".join(no_board[:10]))
    for market, minimum in MIN_BOARD_ROWS.items():
        if int(counts.get(market, 0)) < minimum:
            out["problems"].append("%s rows %d < %d" % (market, int(counts.get(market, 0)), minimum))
    if previous is not None:
        prev_counts = board[board["date"] == previous]["market"].value_counts().to_dict()
        for market in MIN_BOARD_ROWS:
            before = int(prev_counts.get(market, 0))
            now = int(counts.get(market, 0))
            if before > 0 and now / before < MIN_BOARD_RATIO:
                out["problems"].append("%s rows %d / previous %d < %.2f" % (market, now, before, MIN_BOARD_RATIO))
    out["ok"] = not out["problems"]
    return out


def integrity_flags(px: pd.DataFrame, board: pd.DataFrame, D: Any, seam: Any, codes: Sequence[str]
                    ) -> Dict[str, List[str]]:
    """Notes per stock (recipe v1 notes only): seam_jump (adjusted vs raw return differ > 0.5% on the first row
    after the seam) and adj_raw_mismatch (|r_adj| > 10.5% while |r_raw| <= 10.5% in the last 252 own rows)."""
    D, seam = pd.Timestamp(D), pd.Timestamp(seam)
    wanted = set(map(str, codes))
    p = px[px["stock_id"].isin(wanted) & (px["date"] <= D)][["stock_id", "date", "close"]]
    raw = board[board["stock_id"].isin(wanted) & (board["date"] <= D)][["stock_id", "date", "close"]]
    m = p.merge(raw.rename(columns={"close": "raw"}), on=["stock_id", "date"], how="left")
    m = m.sort_values(["stock_id", "date"], kind="mergesort")
    m["rank_back"] = m.groupby("stock_id").cumcount(ascending=False)
    g = m.groupby("stock_id")
    with np.errstate(all="ignore"):
        m["r_adj"] = m["close"] / g["close"].shift(1) - 1.0
        m["r_raw"] = pd.to_numeric(m["raw"], errors="coerce") / pd.to_numeric(g["raw"].shift(1), errors="coerce") - 1.0
    flags: Dict[str, List[str]] = {}
    seam_rows = m[(m["date"] > seam)].groupby("stock_id").head(1)
    bad = seam_rows[(seam_rows["r_adj"] - seam_rows["r_raw"]).abs() > 0.005]
    for sid in bad["stock_id"]:
        flags.setdefault(str(sid), []).append("seam_jump")
    recent = m[m["rank_back"] < 252]
    bad = recent[(recent["r_raw"].abs() <= 0.105) & (recent["r_adj"].abs() > 0.105)]
    for sid in bad["stock_id"].unique():
        flags.setdefault(str(sid), []).append("adj_raw_mismatch")
    return flags


# ------------------------------------------------------------------------------------------------------------------
# assembled inputs
# ------------------------------------------------------------------------------------------------------------------
def deadline_classes(frame: Optional[pd.DataFrame]) -> Dict[str, str]:
    if frame is None or frame.empty or "rule_class" not in frame.columns:
        return {}
    sub = frame[frame["rule_class"].astype(str).str.startswith("monthly_revenue")
                | frame["rule_class"].isin(core.REVENUE_LATE_ROUTE_CLASSES)]
    return {str(s): str(c) for s, c in zip(sub["stock_id"], sub["rule_class"])}


def load_inputs(head: PinnedHead, supplement_dir: Any, supplement_sha256: Optional[str], cache_dir: Optional[Path],
                *, seam: Any = core.SEAM_DATE) -> Dict[str, Any]:
    """Everything build_panel needs from the pinned head and the supplement, plus provenance and checks."""
    log: Dict[str, Any] = {"reads": {}}
    t0 = time.perf_counter()
    supp_px, supp_board, supp_info = load_supplement(supplement_dir, supplement_sha256)
    head_px = read_route(head, "finmind_price_adj", cache_dir, log=log["reads"])
    old = core.prepare_prices(supp_px)
    new = core.prepare_prices(head_px)
    del supp_px, head_px
    stitch_log: Dict[str, Any] = {}
    px = core.stitch_prices(old, new, seam, core.PRICE_FIRST, stitch_log)
    del old, new
    head_board = read_route(head, "official_market_daily", cache_dir, log=log["reads"])
    board = core.prepare_board([supp_board, head_board])
    del supp_board, head_board
    per = core.prepare_per(read_route(head, "finmind_per", cache_dir, log=log["reads"]))
    revenue = core.prepare_revenue(read_route(head, "finmind_month_revenue", cache_dir, log=log["reads"]))
    classes: Dict[str, str] = {}
    deadline_status = "missing"
    if "official_finance_reporting_deadline" in head.routes:
        try:
            classes = deadline_classes(read_route(head, "official_finance_reporting_deadline", cache_dir,
                                                  log=log["reads"]))
            deadline_status = "ok"
        except H63XsDataError as exc:
            deadline_status = "unreadable:" + exc.code
    late = core.late_revenue_codes(revenue["stock_id"].unique(), classes)
    gc.collect()
    log["stitch"] = stitch_log
    log["seconds"] = round(time.perf_counter() - t0, 2)
    return {"px": px, "board": board, "per": per, "revenue": revenue, "late_codes": late,
            "late_codes_sha256": hashlib.sha256(core.canonical_json(late)).hexdigest(),
            "deadline_status": deadline_status, "supplement": supp_info, "log": log}


def provenance(head: PinnedHead, inputs: Dict[str, Any], calendar: Dict[str, Any], seam: Any) -> Dict[str, Any]:
    objects = {}
    for rid in REQUIRED_ROUTES + OPTIONAL_ROUTES:
        e = head.routes.get(rid)
        if e:
            objects[rid] = {k: e.get(k) for k in ("sha256", "status", "actual_min_date", "actual_max_date",
                                                  "record_count", "reason")}
    return {"central_head_sha256": head.sha256, "central_published_at": head.published_at,
            "central_run_id": head.top.get("run_id"), "objects": objects,
            "supplement": inputs["supplement"], "seam_date": str(pd.Timestamp(seam).date()),
            "stitch_rule": core.STITCH_RULE_ID, "stitch": inputs["log"].get("stitch"),
            "late_revenue_codes_sha256": inputs["late_codes_sha256"],
            "late_revenue_codes_count": len(inputs["late_codes"]), "deadline_route": inputs["deadline_status"],
            "calendar": {k: calendar.get(k) for k in ("status", "sha256", "ref", "verified_asof", "detail")}}
