"""Forward record of the H63 U7 model series (ADR-0009): immutable daily full-market blocks, a hash-chained index,
derived settlement (63rd own price row) and the pre-registered review v3.

* A block is the daily snapshot itself: predictions\\<YYYY>\\<YYYYMMDD>__<entry12>.parquet + .json header, written
  once (temporary file -> fsync -> rename that fails if the target exists).  Never updated or deleted.
* state\\forward_index.jsonl: one line per block, each carrying the sha256 of the previous line (hash chain).
* Settlement is recomputed every day from the current panel (same price vintage at both ends) and never rewrites a
  prediction row; the relative label of a block is frozen once 63 + 5 verified sessions have passed.
* The review uses only the earliest prospective block of each decision date (recorded before 09:00 Taipei of the
  next verified session) on gate-passing days; the verdict rules are PREREGISTERED_REVIEW_V3 (sha pinned by test).
"""
from __future__ import annotations

import contextlib
import datetime as dt
import hashlib
import json
import os
import tempfile
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional, Sequence, Tuple

import numpy as np
import pandas as pd

import stock_cache_lifecycle as cache_lifecycle
import stock_h63_xs_core as core

BLOCK_SCHEMA = "tw-stock-h63-xs-block-v1"
INDEX_SCHEMA = "tw-stock-h63-xs-forward-index-v1"
SUMMARY_SCHEMA = "tw-stock-h63-xs-forward-summary-v1"
LATEST_SCHEMA = "tw-stock-h63-xs-latest-v1"
TAIPEI = dt.timezone(dt.timedelta(hours=8), "Asia/Taipei")
GENESIS = "0" * 64
FREEZE_EXTRA_SESSIONS = 5
NO_LABEL_AFTER_SESSIONS = 2 * core.HORIZON
MIN_DAILY_ROWS = 30

PREREGISTERED_REVIEW_V3: Dict[str, Any] = {
    "version": "h63-xs-prereg-v3",
    "base": {"version": "v2", "sha256": "947cdbef1476f912dd550ebc789fc95c20ed9a420596ff56dbb87b955fe559b0"},
    "recipe": core.RECIPE["name"],
    "label": "r63 of the 63rd own price row beats the median of the block's eligible, label_ok, matured rows",
    "rows": "eligible, label_ok, gate-passing decision dates, earliest prospective block per decision date",
    "prospective": "recorded before 09:00 Asia/Taipei of the first verified session after the decision date",
    "data_status": "PARTIAL routes and the price supplement included; reported by data status",
    "model_merge": "blocks of every slope refit of the same recipe and band method are judged together",
    "band": {"method": "B3+CQR252 final band", "bucket": "b3_bucket", "nominal": core.BAND_NOMINAL},
    "block": {"decision_sessions": 63, "min_rows": 500, "min_decision_dates": 10},
    "min_matured_blocks": 3, "recent_blocks": 4,
    "expected": {"ic_gt": 0.0, "auc_gt": 0.5, "brier_lt": 0.25, "band_coverage_80": [0.72, 0.88]},
    "review_triggers": {"brier_ge": 0.252, "accuracy_le_always_beats": True, "bucket_coverage_lt": 0.70},
    "rollback": {"band_coverage_80_outside": [0.64, 0.96]},
    "verdicts": {"insufficient_data": "資料不足", "as_expected": "符合預期", "attention": "需要注意",
                 "rollback_suggested": "建議回滾"},
}
PREREGISTERED_REVIEW_V3_SHA256 = hashlib.sha256(core.canonical_json(PREREGISTERED_REVIEW_V3)).hexdigest()

SNAPSHOT_COLUMNS = (
    "stock_id", "decision_date", "market", "main", "in_scope", "eligible", "n_components",
    "f_ivol60", "f_max5", "f_hi52", "f_rev_yoy", "f_rev_high12", "f_ep", "f_bm",
    "f_vol60", "vol252", "f_money60", "f_nhist",
    "rk_f_ivol60", "rk_f_max5", "rk_f_hi52", "rk_f_rev_yoy", "rk_f_rev_high12", "rk_f_ep", "rk_f_bm",
    "score", "score_universe_mean", "score_centred",
    "p_raw", "p", "p_status", "relative_strength", "rank_eligible",
    "sigma63", "sigma63_b3", "own_vol60_missing", "b3_bucket", "b3_q10", "b3_q50", "b3_q90",
    "b3cqr_q10", "b3cqr_q90", "band_status",
    "revenue_period", "revenue_available_at", "revenue_stale", "per_date",
    "integrity_flags", "signal_adj_close", "signal_raw_close")


class LedgerError(RuntimeError):
    def __init__(self, code: str, detail: str = ""):
        super().__init__(code + (": " + detail if detail else ""))
        self.code = code


def sha256_file(path: Any) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as handle:
        for block in iter(lambda: handle.read(16 << 20), b""):
            h.update(block)
    return h.hexdigest()


def _iso(value: Any) -> Optional[str]:
    if value is None:
        return None
    stamp = pd.Timestamp(value)
    return None if pd.isna(stamp) else stamp.strftime("%Y-%m-%d")


def utc_now() -> dt.datetime:
    return dt.datetime.now(dt.timezone.utc)


def stamp_utc(moment: dt.datetime) -> str:
    return moment.astimezone(dt.timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.%fZ")


def parse_utc(text: str) -> dt.datetime:
    return dt.datetime.strptime(text, "%Y-%m-%dT%H:%M:%S.%fZ").replace(tzinfo=dt.timezone.utc)


# ------------------------------------------------------------------------------------------------------------------
# rows <-> canonical JSON
# ------------------------------------------------------------------------------------------------------------------
def snapshot_frame(rows: pd.DataFrame, flags: Optional[Dict[str, List[str]]] = None,
                   raw_close: Optional[Dict[str, float]] = None) -> pd.DataFrame:
    """Normalize infer_rows output into the stored block schema (ISO date text, plain dtypes)."""
    fr = rows.copy()
    fr["stock_id"] = fr["stock_id"].astype(str)
    fr["in_scope"] = fr["main"].astype(bool)
    for c in ("decision_date", "revenue_available_at", "per_date"):
        fr[c] = [_iso(v) for v in fr[c]]
    fr["revenue_period"] = [None if v is None or (isinstance(v, float) and np.isnan(v)) else str(v)
                            for v in fr["revenue_period"]]
    flags = flags or {}
    fr["integrity_flags"] = [",".join(sorted(set(flags.get(s, [])))) for s in fr["stock_id"]]
    raw_close = raw_close or {}
    fr["signal_raw_close"] = [raw_close.get(s, np.nan) for s in fr["stock_id"]]
    for c in ("main", "in_scope", "eligible", "own_vol60_missing", "revenue_stale"):
        fr[c] = fr[c].astype(bool)
    for c in ("n_components", "b3_bucket"):
        fr[c] = fr[c].astype(np.int64)
    fr = fr[list(SNAPSHOT_COLUMNS)].sort_values("stock_id", kind="mergesort").reset_index(drop=True)
    return fr


def _plain(value: Any) -> Any:
    if value is None:
        return None
    if isinstance(value, (np.bool_, bool)):
        return bool(value)
    if isinstance(value, (np.integer,)):
        return int(value)
    if isinstance(value, (float, np.floating)):
        return float(value) if np.isfinite(value) else None
    if isinstance(value, str):
        return value
    if value is pd.NaT:
        return None
    return value


def row_record(frame: pd.DataFrame, i: int) -> Dict[str, Any]:
    return {c: _plain(frame[c].iat[i]) for c in frame.columns}


def row_sha256(record: Dict[str, Any]) -> str:
    return hashlib.sha256(core.canonical_json(record)).hexdigest()


def rows_sha256(frame: pd.DataFrame) -> str:
    h = hashlib.sha256()
    for i in range(len(frame)):
        h.update(core.canonical_json(row_record(frame, i)))
        h.update(b"\n")
    return h.hexdigest()


# ------------------------------------------------------------------------------------------------------------------
# paths and low-level writes
# ------------------------------------------------------------------------------------------------------------------
class Paths:
    def __init__(self, root: Any):
        self.root = Path(root)
        self.predictions = self.root / "predictions"
        self.state = self.root / "state"
        self.settlement = self.state / "settlement"
        self.index = self.state / "forward_index.jsonl"
        self.events = self.state / "forward_events.jsonl"
        self.latest = self.predictions / "latest.json"
        self.refusal = self.predictions / "latest_refusal.json"
        self.summary = self.state / "summary.json"
        self.locks = self.state / "locks"

    def block(self, decision_date: str, entry_key: str) -> Tuple[Path, Path]:
        day = decision_date.replace("-", "")
        folder = self.predictions / day[:4]
        name = "%s__%s" % (day, entry_key[:12])
        return folder / (name + ".parquet"), folder / (name + ".json")


@contextlib.contextmanager
def ledger_lock(paths: Paths):
    paths.locks.mkdir(parents=True, exist_ok=True)
    with cache_lifecycle.file_lock(paths.locks / "ledger.lock"):
        yield


def _write_new(path: Path, writer) -> None:
    """Write once: temp file + fsync, then rename (fails when the target already exists)."""
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, temporary = tempfile.mkstemp(prefix=path.name + ".", suffix=".tmp", dir=path.parent)
    os.close(fd)
    try:
        writer(temporary)
        with open(temporary, "rb+") as handle:  # Windows FlushFileBuffers needs write access
            os.fsync(handle.fileno())
        os.rename(temporary, path)
    finally:
        if os.path.exists(temporary):
            os.unlink(temporary)


def write_new_json(path: Path, value: Any) -> None:
    def write(tmp):
        with open(tmp, "w", encoding="utf-8", newline="\n") as stream:
            json.dump(value, stream, ensure_ascii=False, sort_keys=True, indent=1, allow_nan=False)
    _write_new(path, write)


def replace_json(path: Path, value: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, temporary = tempfile.mkstemp(prefix=path.name + ".", suffix=".tmp", dir=path.parent)
    try:
        with os.fdopen(fd, "w", encoding="utf-8", newline="\n") as stream:
            json.dump(value, stream, ensure_ascii=False, sort_keys=True, indent=1, allow_nan=False)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, path)
    finally:
        if os.path.exists(temporary):
            os.unlink(temporary)


def append_line(path: Path, value: Dict[str, Any]) -> str:
    path.parent.mkdir(parents=True, exist_ok=True)
    line = json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False)
    with open(path, "a", encoding="utf-8", newline="\n") as stream:
        stream.write(line + "\n")
        stream.flush()
        os.fsync(stream.fileno())
    return line


def record_event(paths: Paths, event_kind: str, **fields: Any) -> None:
    with contextlib.suppress(OSError, ValueError, TypeError):
        append_line(paths.events, {"event": event_kind, "at": stamp_utc(utc_now()), **core.jsonable(fields)})


def read_index(paths: Paths) -> Tuple[List[Dict[str, Any]], Dict[str, Any]]:
    """Index lines and chain status {'ok', 'lines', 'broken_at'}; a broken chain is reported, never repaired."""
    if not paths.index.exists():
        return [], {"ok": True, "lines": 0, "broken_at": None}
    entries, previous, broken = [], GENESIS, None
    with open(paths.index, "r", encoding="utf-8") as stream:
        for number, line in enumerate(stream, 1):
            line = line.rstrip("\n")
            if not line:
                continue
            try:
                value = json.loads(line)
            except ValueError:
                broken = broken or number
                continue
            if value.get("prev_line_sha256") != previous and broken is None:
                broken = number
            previous = hashlib.sha256(line.encode("utf-8")).hexdigest()
            entries.append(value)
    return entries, {"ok": broken is None, "lines": len(entries), "broken_at": broken}


def _last_line_sha(paths: Paths) -> str:
    if not paths.index.exists():
        return GENESIS
    last = None
    with open(paths.index, "r", encoding="utf-8") as stream:
        for line in stream:
            if line.strip():
                last = line.rstrip("\n")
    return GENESIS if last is None else hashlib.sha256(last.encode("utf-8")).hexdigest()


# ------------------------------------------------------------------------------------------------------------------
# record
# ------------------------------------------------------------------------------------------------------------------
def entry_key(key_material: Dict[str, Any]) -> str:
    return hashlib.sha256(core.canonical_json(core.jsonable(key_material))).hexdigest()


def record_block(root: Any, frame: pd.DataFrame, header: Dict[str, Any], *, update_latest: bool = True,
                 recorded_at: Optional[dt.datetime] = None) -> Dict[str, Any]:
    """Write one immutable block (parquet + header) for header['decision_date'] and header['key_material'].

    Returns {'status': recorded | already_recorded | conflicting_repeat, 'entry_key', 'parquet', 'header'}.  A repeat
    of an intact block completes the index line / latest pointer that an interrupted run left unwritten."""
    paths = Paths(root)
    key = entry_key(header["key_material"])
    decision = header["decision_date"]
    pq_path, js_path = paths.block(decision, key)
    digest = rows_sha256(frame)
    moment = recorded_at or utc_now()
    with ledger_lock(paths):
        if pq_path.exists() or js_path.exists():
            try:
                old = json.loads(js_path.read_text(encoding="utf-8"))
                same = old.get("rows_sha256") == digest and sha256_file(pq_path) == old.get("parquet_sha256") \
                    and old.get("entry_key") == key and old.get("decision_date") == decision  # 身分欄位也要相符
            except (FileNotFoundError, ValueError):  # 缺檔或無法解析才算不同；檔案被占用等讀取錯誤直接丟出
                same = False
            kind = "already_recorded" if same else "conflicting_repeat"
            record_event(paths, kind, decision_date=decision, entry_key=key, rows_sha256=digest)
            out = {"status": kind, "entry_key": key, "parquet": str(pq_path), "header": str(js_path)}
            if same:  # 前次寫完區塊後中斷：依既有 header 補完 index／latest（原 recorded_at，區塊檔不動）
                out["indexed"], out["completed"] = _publish(paths, old, pq_path, js_path, update_latest, resume=True)
            return out
        _write_new(pq_path, lambda tmp: frame.to_parquet(tmp, index=False, compression="zstd"))
        full = dict(header)
        full.update(schema=BLOCK_SCHEMA, entry_key=key, rows_sha256=digest, row_count=int(len(frame)),
                    fields=list(frame.columns), parquet_file=pq_path.name, parquet_sha256=sha256_file(pq_path),
                    recorded_at=stamp_utc(moment),
                    recorded_at_taipei=moment.astimezone(TAIPEI).strftime("%Y%m%d %H:%M:%S"),
                    prereg_review_sha256=PREREGISTERED_REVIEW_V3_SHA256)
        stored = core.jsonable(full)
        write_new_json(js_path, stored)
        indexed, _ = _publish(paths, stored, pq_path, js_path, update_latest)
    return {"status": "recorded", "entry_key": key, "parquet": str(pq_path), "header": str(js_path),
            "indexed": indexed}


def _publish(paths: Paths, stored: Dict[str, Any], pq_path: Path, js_path: Path, update_latest: bool, *,
             resume: bool = False) -> Tuple[bool, List[str]]:
    """Index line + latest pointer of a written block, both from its stored header.  resume (a repeat of an intact
    block) writes only the missing steps; its read errors raise and it never appends to a broken index
    (_index_missing).  Returns (indexed, steps written)."""
    header_sha = sha256_file(js_path)
    line = {"schema": INDEX_SCHEMA, "decision_date": stored["decision_date"], "entry_key": stored["entry_key"],
            "run_id": stored.get("run_id"), "recorded_at": stored["recorded_at"],
            "parquet": pq_path.relative_to(paths.root).as_posix(), "parquet_sha256": stored["parquet_sha256"],
            "header": js_path.relative_to(paths.root).as_posix(), "header_sha256": header_sha,
            "rows_sha256": stored["rows_sha256"], "row_count": int(stored["row_count"]),
            "model_sha256": (stored.get("model") or {}).get("model_sha256"),
            "date_gate_ok": bool((stored.get("gate") or {}).get("ok")), "trigger": stored.get("trigger"),
            "prev_line_sha256": None}
    steps: List[str] = []
    indexed = True
    if not resume or _index_missing(paths, line):
        error: Optional[str] = None
        try:
            line["prev_line_sha256"] = _last_line_sha(paths)
            append_line(paths.index, line)
        except OSError as exc:
            error = str(exc)
        if error is None and resume and read_index(paths)[0][-1:] != [line]:  # 重讀確認寫入的行可解析才算 indexed
            error = "appended index line is unreadable"
        if error is None:
            steps.append("index")
        else:
            indexed = False
            record_event(paths, "unindexed", decision_date=line["decision_date"], entry_key=line["entry_key"],
                         header_sha256=header_sha, error=error)  # 重跑補 index 前以此背書（_index_missing）
    if update_latest:
        pointer = {"schema": LATEST_SCHEMA, "decision_date": line["decision_date"], "entry_key": line["entry_key"],
                   "header": line["header"], "header_sha256": header_sha, "parquet_sha256": line["parquet_sha256"],
                   "rows_sha256": line["rows_sha256"], "recorded_at": line["recorded_at"], "indexed": indexed}
        if not resume or _latest_behind(paths, pointer):
            replace_json(paths.latest, pointer)
            with contextlib.suppress(FileNotFoundError):
                paths.refusal.unlink()
            steps.append("latest")
    if resume and steps:
        record_event(paths, "publish_completed", decision_date=line["decision_date"], entry_key=line["entry_key"],
                     steps=steps, recorded_at=line["recorded_at"])
    return indexed, steps


def _index_missing(paths: Paths, line: Dict[str, Any]) -> bool:
    """resume: True when the index has no line for this block yet and one may be appended.  The header must be the
    one its index line recorded, or without one the one latest.json (pointing at this block) and every unindexed
    event of this block recorded (h63xs_block_sha_mismatch); a header with no such record, a broken chain and a last
    line cut mid-write are left for a person, never indexed by appending (h63xs_block_unverified,
    h63xs_index_broken).  Read errors raise, like _latest_behind.  So a first write whose index append, unindexed
    event and latest.json all failed stays unverified on every repeat: no record can vouch for its header."""
    entries, chain = read_index(paths)
    pinned = {e.get("header_sha256") for e in entries if e.get("entry_key") == line["entry_key"]
              and e.get("header") == line["header"]}
    if pinned - {line["header_sha256"]}:  # header 已和 index 記錄的不同：不補發 latest
        raise LedgerError("h63xs_block_sha_mismatch", line["header"])
    if pinned:
        return False
    recorded: List[Any] = []  # index 缺行：改比對 latest 與 unindexed 事件記錄的 header sha
    with contextlib.suppress(FileNotFoundError, ValueError, AttributeError):
        current = json.loads(paths.latest.read_text(encoding="utf-8"))
        if current.get("schema") == LATEST_SCHEMA and current.get("entry_key") == line["entry_key"]:
            recorded.append(current.get("header_sha256"))
    recorded += _unindexed_header_shas(paths, line["entry_key"])
    if any(sha != line["header_sha256"] for sha in recorded):  # 任一記錄和現在的 header 不同：不補 index
        raise LedgerError("h63xs_block_sha_mismatch", line["header"])
    if not recorded:  # latest 已移到別的區塊、又沒有記下 header sha 的事件：無從確認 header 未被改過，交給人工
        raise LedgerError("h63xs_block_unverified", line["header"])
    tail = paths.index.read_bytes()[-1:] if paths.index.exists() else b""
    if not chain["ok"] or tail not in (b"", b"\n"):  # 損壞的鏈、或前次追加寫到一半的行：不自動補 index，交給人工
        raise LedgerError("h63xs_index_broken", "broken_at=%s" % (chain["broken_at"] or "end"))
    return True


def _unindexed_header_shas(paths: Paths, key: str) -> List[Any]:
    """header_sha256 recorded by each unindexed event of this block (events written before that field existed, and
    lines cut mid-write, record none).  A missing events file records none; other read errors raise."""
    try:
        raw = paths.events.read_bytes()
    except FileNotFoundError:
        return []
    shas = []
    for text in raw.splitlines():
        with contextlib.suppress(ValueError, AttributeError):
            event = json.loads(text)
            if event.get("event") == "unindexed" and event.get("entry_key") == key \
                    and event.get("header_sha256") is not None:
                shas.append(event["header_sha256"])
    return shas


def _latest_behind(paths: Paths, pointer: Dict[str, Any]) -> bool:
    """True when latest.json does not publish this block yet: missing or broken, pointing at a block recorded
    earlier, or this block still marked unindexed.  A newer block keeps the pointer; other read errors raise."""
    try:
        current = json.loads(paths.latest.read_text(encoding="utf-8"))
        if current.get("schema") != LATEST_SCHEMA:
            return True
        if current.get("entry_key") == pointer["entry_key"]:
            return current != pointer and dict(current, indexed=pointer["indexed"]) == pointer
        return parse_utc(current["recorded_at"]) < parse_utc(pointer["recorded_at"])
    except (FileNotFoundError, ValueError, KeyError, TypeError, AttributeError):
        return True


def record_refusal(root: Any, reason: str, detail: str, **fields: Any) -> None:
    paths = Paths(root)
    with contextlib.suppress(OSError, ValueError, TypeError):
        replace_json(paths.refusal, core.jsonable({"schema": "tw-stock-h63-xs-refusal-v1", "reason": reason,
                                                   "detail": detail, "at": stamp_utc(utc_now()), **fields}))
    record_event(paths, "skipped", reason=reason, detail=detail, **fields)


def load_block(root: Any, line: Dict[str, Any], *, verify: bool = True) -> Tuple[pd.DataFrame, Dict[str, Any]]:
    paths = Paths(root)
    pq_path, js_path = paths.root / line["parquet"], paths.root / line["header"]
    if verify and (sha256_file(js_path) != line["header_sha256"] or sha256_file(pq_path) != line["parquet_sha256"]):
        raise LedgerError("h63xs_block_sha_mismatch", line["parquet"])
    header = json.loads(js_path.read_text(encoding="utf-8"))
    frame = pd.read_parquet(pq_path)
    if verify and rows_sha256(frame) != header.get("rows_sha256"):
        raise LedgerError("h63xs_block_rows_mismatch", line["parquet"])
    return frame, header


# ------------------------------------------------------------------------------------------------------------------
# settlement
# ------------------------------------------------------------------------------------------------------------------
def _sessions_after(sessions: Optional[pd.DatetimeIndex], day: str) -> Optional[int]:
    if sessions is None:
        return None
    return int(len(sessions) - sessions.searchsorted(pd.Timestamp(day), side="right"))


def _next_session(sessions: Optional[pd.DatetimeIndex], day: str) -> Optional[pd.Timestamp]:
    if sessions is None:
        return None
    i = sessions.searchsorted(pd.Timestamp(day), side="right")
    return sessions[i] if i < len(sessions) else None


def prospective_status(recorded_at: str, decision_date: str, sessions: Optional[pd.DatetimeIndex]) -> str:
    nxt = _next_session(sessions, decision_date)
    if nxt is None:
        return "unknown"
    limit = dt.datetime(nxt.year, nxt.month, nxt.day, 9, 0, tzinfo=TAIPEI)
    return "prospective" if parse_utc(recorded_at) < limit else "late"


def settle(root: Any, P: pd.DataFrame, sessions: Optional[pd.DatetimeIndex], *, price_vintage: str
           ) -> Dict[str, Any]:
    """Derived settlement of every indexed block against the current panel P (r63 and label_end of the 63rd own
    price row, one vintage).  Writes state\\settlement\\settled_rows.parquet (replaced), freezes matured blocks
    once (frozen\\<block>.parquet + maturity_log.jsonl) and archives prices of pending stocks that left the head."""
    paths = Paths(root)
    lines, chain = read_index(paths)
    out: Dict[str, Any] = {"blocks": len(lines), "chain": chain, "frozen_now": 0, "errors": []}
    if not lines:
        return out
    key = P[["stock_id", "decision_date", "r63", "label_end", "close"]].copy()
    key["decision_date"] = key["decision_date"].dt.strftime("%Y-%m-%d")
    key["label_end"] = [_iso(v) for v in key["label_end"]]
    archive_dir = paths.settlement / "delisted_prices"
    archived = _load_archives(archive_dir)
    present = set(P["stock_id"].astype(str).unique())
    settled = []
    pending_codes: Dict[str, str] = {}
    for line in lines:
        try:
            frame, header = load_block(root, line)
        except (LedgerError, OSError, ValueError) as exc:
            out["errors"].append({"block": line.get("parquet"), "error": str(exc)})
            continue
        base = frame[["stock_id", "decision_date", "eligible", "main", "p", "score_centred", "b3_bucket",
                      "b3_q10", "b3_q90", "b3cqr_q10", "b3cqr_q90", "band_status", "signal_adj_close"]].copy()
        m = base.merge(key, on=["stock_id", "decision_date"], how="left", suffixes=("", "_now"))
        status = np.where(m["close"].isna(), "decision_row_missing",
                          np.where(m["r63"].notna(), "matured", "pending")).astype(object)
        after = _sessions_after(sessions, line["decision_date"])
        if after is not None and after >= NO_LABEL_AFTER_SESSIONS:
            status = np.where(status == "pending", "no_label", status)
        if sessions is not None and not (sessions == pd.Timestamp(line["decision_date"])).any() \
                and sessions.max() >= pd.Timestamp(line["decision_date"]):
            status[:] = "void_decision_not_session"
        # archived prices for stocks that disappeared from the head
        for i in np.flatnonzero((status == "decision_row_missing") | (status == "pending")):
            sid = m["stock_id"].iat[i]
            if sid not in present and sid in archived:
                r63, le = _archived_label(archived[sid], line["decision_date"])
                if r63 is not None:
                    m.at[m.index[i], "r63"], m.at[m.index[i], "label_end"] = r63, le
                    status[i] = "matured"
        m["status"] = status
        m["basis_changed"] = (m["close"].notna() & (np.abs(m["close"] / m["signal_adj_close"] - 1.0) > 1e-9))
        m["label_ok"] = m["r63"].between(*core.LABEL_RANGE)
        m["prospective"] = prospective_status(line["recorded_at"], line["decision_date"], sessions)
        m["entry_key"] = line["entry_key"]
        m["date_gate_ok"] = bool(line.get("date_gate_ok"))
        m["recorded_at"] = line["recorded_at"]
        for sid in m.loc[np.isin(status, ["pending"]), "stock_id"]:
            pending_codes[sid] = min(pending_codes.get(sid, line["decision_date"]), line["decision_date"])
        settled.append(m)
        if after is not None and after >= core.HORIZON + FREEZE_EXTRA_SESSIONS:
            out["frozen_now"] += _freeze(paths, line, m, price_vintage)
    rows = pd.concat(settled, ignore_index=True) if settled else pd.DataFrame()
    if len(rows):
        rows = rows.drop(columns=["close"])
        paths.settlement.mkdir(parents=True, exist_ok=True)
        fd, temporary = tempfile.mkstemp(prefix="settled_rows.", suffix=".tmp", dir=paths.settlement)
        os.close(fd)
        try:
            rows.to_parquet(temporary, index=False)
            os.replace(temporary, paths.settlement / "settled_rows.parquet")
        finally:
            if os.path.exists(temporary):
                os.unlink(temporary)
    out["rows"] = int(len(rows))
    out["status_counts"] = {str(k): int(v) for k, v in rows["status"].value_counts().items()} if len(rows) else {}
    out["archived_now"] = _archive_pending(paths, P, pending_codes, price_vintage)
    return out


def _load_archives(folder: Path) -> Dict[str, pd.DataFrame]:
    out: Dict[str, pd.DataFrame] = {}
    if folder.exists():
        for path in sorted(folder.glob("*.parquet")):
            with contextlib.suppress(OSError, ValueError):
                frame = pd.read_parquet(path)
                sid = str(frame["stock_id"].iat[0])
                out[sid] = frame
    return out


def _archived_label(frame: pd.DataFrame, decision_date: str) -> Tuple[Optional[float], Optional[str]]:
    dates = list(frame["date"])
    if decision_date not in dates:
        return None, None
    i = dates.index(decision_date)
    if i + core.HORIZON >= len(dates):
        return None, None
    c0, c1 = float(frame["close"].iat[i]), float(frame["close"].iat[i + core.HORIZON])
    return (c1 / c0 - 1.0 if c0 > 0 else None), dates[i + core.HORIZON]


def _archive_pending(paths: Paths, P: pd.DataFrame, pending: Dict[str, str], vintage: str) -> int:
    """Keep the latest own price rows (one vintage) of stocks with pending rows; when a stock later vanishes from
    the head the last kept copy becomes its archive (write once)."""
    keep_path = paths.settlement / "pending_prices.parquet"
    folder = paths.settlement / "delisted_prices"
    present = set(P["stock_id"].astype(str).unique())
    archived = 0
    if keep_path.exists():
        with contextlib.suppress(OSError, ValueError):
            previous = pd.read_parquet(keep_path)
            for sid, frame in previous.groupby("stock_id"):
                if sid not in present:  # pending at the previous run, gone from the head now
                    target = folder / ("%s_%s.parquet" % (sid, str(frame["vintage"].iat[0])[:12]))
                    if not target.exists():
                        _write_new(target, lambda tmp, f=frame: f.to_parquet(tmp, index=False))
                        archived += 1
    if pending:
        codes = pd.Series(list(pending))
        first = pd.Series(pending)
        sub = P[P["stock_id"].isin(set(codes))][["stock_id", "decision_date", "close"]].copy()
        sub = sub[sub["decision_date"] >= sub["stock_id"].map(first).map(pd.Timestamp)]
        sub = sub.rename(columns={"decision_date": "date"})
        sub["date"] = sub["date"].dt.strftime("%Y-%m-%d")
        sub["vintage"] = vintage
        paths.settlement.mkdir(parents=True, exist_ok=True)
        fd, temporary = tempfile.mkstemp(prefix="pending_prices.", suffix=".tmp", dir=paths.settlement)
        os.close(fd)
        try:
            sub.to_parquet(temporary, index=False)
            os.replace(temporary, keep_path)
        finally:
            if os.path.exists(temporary):
                os.unlink(temporary)
    return archived


def _freeze(paths: Paths, line: Dict[str, Any], m: pd.DataFrame, vintage: str) -> int:
    day = line["decision_date"].replace("-", "")
    target = paths.settlement / "frozen" / ("%s__%s.parquet" % (day, line["entry_key"][:12]))
    if target.exists():
        return 0
    use = m["eligible"].astype(bool) & m["label_ok"].astype(bool) & (m["status"] == "matured")
    median = float(np.median(m.loc[use, "r63"])) if use.any() else None
    frozen = m[["stock_id", "decision_date", "status", "r63", "label_end", "label_ok"]].copy()
    frozen["block_median"] = median
    frozen["beats_median"] = (m["r63"] > median) if median is not None else False
    frozen["entry_key"] = line["entry_key"]
    frozen["price_vintage"] = vintage
    _write_new(target, lambda tmp: frozen.to_parquet(tmp, index=False))
    append_line(paths.settlement / "maturity_log.jsonl",
                {"decision_date": line["decision_date"], "entry_key": line["entry_key"], "frozen_at":
                 stamp_utc(utc_now()), "median": median, "matured_rows": int(use.sum()), "price_vintage": vintage,
                 "file": target.name, "file_sha256": sha256_file(target)})
    return 1


# ------------------------------------------------------------------------------------------------------------------
# review (pre-registered v3)
# ------------------------------------------------------------------------------------------------------------------
def _auc(y: np.ndarray, p: np.ndarray) -> float:
    n1 = y.sum()
    n0 = len(y) - n1
    if n1 == 0 or n0 == 0:
        return float("nan")
    r = pd.Series(p).rank(method="average").to_numpy()
    return float((r[y == 1].sum() - n1 * (n1 + 1) / 2.0) / (n1 * n0))


def review(root: Any) -> Dict[str, Any]:
    """Daily reference metrics and the 63-session block verdict from frozen labels (earliest prospective block per
    decision date, gate-passing days only).  Writes forward_daily.csv, forward_blocks.csv, summary.json and
    review_zh.txt in state\\."""
    paths = Paths(root)
    lines, chain = read_index(paths)
    settled_path = paths.settlement / "settled_rows.parquet"
    settled = pd.read_parquet(settled_path) if settled_path.exists() else pd.DataFrame()
    frozen_dir = paths.settlement / "frozen"
    frozen = pd.concat([pd.read_parquet(p) for p in sorted(frozen_dir.glob("*.parquet"))], ignore_index=True) \
        if frozen_dir.exists() and any(frozen_dir.glob("*.parquet")) else pd.DataFrame()
    summary: Dict[str, Any] = {"schema": SUMMARY_SCHEMA, "prereg_review_sha256": PREREGISTERED_REVIEW_V3_SHA256,
                               "generated_at": stamp_utc(utc_now()), "chain": chain,
                               "recorded_blocks": len(lines),
                               "first_decision_date": min((l["decision_date"] for l in lines), default=None),
                               "last_decision_date": max((l["decision_date"] for l in lines), default=None),
                               "decision_dates": len({l["decision_date"] for l in lines})}
    daily = pd.DataFrame()
    blocks = pd.DataFrame()
    if len(settled):
        prosp = settled[settled["prospective"] == "prospective"]
        summary["late_blocks"] = int(settled.loc[settled["prospective"] == "late", "entry_key"].nunique())
        chosen = (prosp.sort_values("recorded_at").groupby("decision_date")["entry_key"].first()
                  if len(prosp) else pd.Series(dtype=object))
        summary["matured_decision_dates"] = int(len(frozen["decision_date"].unique())) if len(frozen) else 0
        if len(frozen) and len(chosen):
            fz = frozen[frozen["entry_key"].isin(set(chosen.to_numpy()))]
            # 計分樣本、標籤與報酬都取凍結檔：凍結時仍 pending 的列日後成熟、或歷史價格修訂，都不改變已凍結的區塊；
            # 作廢是區塊層級的效力（日曆修正後決策日不是交易日），凍結不覆蓋，仍依每日重算的狀態排除
            label = ["status", "r63", "label_end", "label_ok"]
            valid = settled[settled["status"] != "void_decision_not_session"]
            use = valid.drop(columns=label).merge(fz[["entry_key", "stock_id", "beats_median"] + label],
                                                  on=["entry_key", "stock_id"])
            use = use[use["eligible"].astype(bool) & use["label_ok"].astype(bool) & use["date_gate_ok"].astype(bool)
                      & (use["status"] == "matured")]
            daily, blocks = _metrics(use)
    summary["daily_rows"] = int(len(daily))
    verdict, detail = _verdict(blocks)
    summary.update(verdict=verdict, verdict_zh=PREREGISTERED_REVIEW_V3["verdicts"][verdict], verdict_detail=detail,
                   valid_blocks=int(blocks["valid"].sum()) if len(blocks) else 0)
    if len(daily):
        summary["cumulative"] = {k: float(daily[k].mean()) for k in ("auc", "ic", "brier", "accuracy",
                                                                      "coverage_final") if k in daily}
    paths.state.mkdir(parents=True, exist_ok=True)
    daily.to_csv(paths.state / "forward_daily.csv", index=False)
    blocks.to_csv(paths.state / "forward_blocks.csv", index=False)
    replace_json(paths.summary, core.jsonable(summary))
    (paths.state / "review_zh.txt").write_text(review_text(summary), encoding="utf-8", newline="\n")
    return summary


def _metrics(use: pd.DataFrame) -> Tuple[pd.DataFrame, pd.DataFrame]:
    recs = []
    for day, g in use.groupby("decision_date", sort=True):
        if len(g) < MIN_DAILY_ROWS:
            continue
        y = g["beats_median"].astype(int).to_numpy()
        p = g["p"].to_numpy(float)
        r = g["r63"].to_numpy(float)
        lo, hi = g["b3cqr_q10"].to_numpy(float), g["b3cqr_q90"].to_numpy(float)
        okb = g["band_status"].isin(["ok", "ok_no_cqr"]).to_numpy() & np.isfinite(lo) & np.isfinite(hi)
        cover = float(((r >= lo) & (r <= hi))[okb].mean()) if okb.any() else float("nan")
        bucket_cov = {}
        for k in range(core.BAND_VOL_BUCKETS):
            sel = okb & (g["b3_bucket"].to_numpy() == k)
            if sel.any():
                bucket_cov[k] = float(((r >= lo) & (r <= hi))[sel].mean())
        recs.append({"decision_date": day, "rows": int(len(g)), "auc": _auc(y, p),
                     "ic": float(np.corrcoef(pd.Series(g["score_centred"]).rank(), pd.Series(r).rank())[0, 1]),
                     "brier": float(np.mean((p - y) ** 2)), "accuracy": float(np.mean((p > 0.5) == (y == 1))),
                     "always_beats": float(np.mean(y == 1)), "coverage_final": cover,
                     "min_bucket_coverage": min(bucket_cov.values()) if bucket_cov else float("nan")})
    daily = pd.DataFrame(recs)
    if daily.empty:
        return daily, pd.DataFrame()
    size = PREREGISTERED_REVIEW_V3["block"]["decision_sessions"]
    daily["block"] = np.arange(len(daily)) // size
    agg = daily.groupby("block").agg(first=("decision_date", "min"), last=("decision_date", "max"),
                                     dates=("decision_date", "size"), rows=("rows", "sum"), auc=("auc", "mean"),
                                     ic=("ic", "mean"), brier=("brier", "mean"), accuracy=("accuracy", "mean"),
                                     always_beats=("always_beats", "mean"), coverage=("coverage_final", "mean"),
                                     min_bucket_coverage=("min_bucket_coverage", "min")).reset_index()
    rules = PREREGISTERED_REVIEW_V3["block"]
    agg["valid"] = (agg["rows"] >= rules["min_rows"]) & (agg["dates"] >= rules["min_decision_dates"])
    return daily, agg


def _verdict(blocks: pd.DataFrame) -> Tuple[str, Dict[str, Any]]:
    rules = PREREGISTERED_REVIEW_V3
    valid = blocks[blocks["valid"]] if len(blocks) else blocks
    if len(valid) < rules["min_matured_blocks"]:
        return "insufficient_data", {"valid_blocks": int(len(valid))}
    recent = valid.tail(rules["recent_blocks"])
    lo, hi = rules["rollback"]["band_coverage_80_outside"]
    if ((recent["coverage"] < lo) | (recent["coverage"] > hi)).any():
        return "rollback_suggested", {"coverage": [float(x) for x in recent["coverage"]]}
    exp = rules["expected"]
    means = {k: float(recent[k].mean()) for k in ("ic", "auc", "brier", "coverage", "accuracy", "always_beats")}
    problems = []
    if not means["ic"] > exp["ic_gt"]:
        problems.append("ic")
    if not means["auc"] > exp["auc_gt"]:
        problems.append("auc")
    if not means["brier"] < exp["brier_lt"]:
        problems.append("brier")
    if not exp["band_coverage_80"][0] <= means["coverage"] <= exp["band_coverage_80"][1]:
        problems.append("coverage")
    last = valid.iloc[-1]
    trig = rules["review_triggers"]
    if last["brier"] >= trig["brier_ge"]:
        problems.append("trigger_brier")
    if last["accuracy"] <= last["always_beats"]:
        problems.append("trigger_accuracy")
    if last["min_bucket_coverage"] < trig["bucket_coverage_lt"]:
        problems.append("trigger_bucket_coverage")
    return ("attention" if problems else "as_expected"), {"recent_means": means, "problems": problems}


def review_text(summary: Dict[str, Any]) -> str:
    first = summary.get("first_decision_date")
    lines = ["H63 三個月相對強弱 前瞻紀錄檢討（事先登記 v3，SHA %s）" % PREREGISTERED_REVIEW_V3_SHA256[:12],
             "已記錄區塊：%d；決策日：%d；首日：%s；最新：%s" % (
                 summary.get("recorded_blocks", 0), summary.get("decision_dates", 0),
                 first.replace("-", "") if first else "-",
                 (summary.get("last_decision_date") or "-").replace("-", "")),
             "已到期決策日：%d；有效區塊：%d" % (summary.get("matured_decision_dates", 0) or 0,
                                         summary.get("valid_blocks", 0) or 0),
             "判定：%s" % summary.get("verdict_zh")]
    if not (summary.get("chain") or {}).get("ok", True):
        lines.append("注意：前瞻紀錄索引的雜湊鏈在第 %s 行中斷，請檢查。" % summary["chain"].get("broken_at"))
    return "\n".join(lines) + "\n"
