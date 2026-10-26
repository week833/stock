"""Read-only consumer API for the pre-open H1 model (stock_daily_preopen).

The producer (stock_daily_preopen.py) writes one immutable prediction snapshot per signal date
before the next TW session opens and points to it from predictions/latest.json. Consumers
(TWNextDayTop10, MIX All, single-stock analysis) only read through this module; it has no
sklearn dependency and never writes.

Snapshot semantics: one row per TWSE/TPEx ordinary stock. p_up = calibrated probability that
adj_close[target_date] > adj_close[signal_date]; exp_ret = calibrated expected close-to-close
return. Both describe the quoted close direction, not a return achievable by buying at the open.

Manifest field ``chips_freshness`` (added 2026-10-18, absent in older snapshots) records whether the
t-day chip routes (CHIP_ROUTES) were updated to the signal date; ``describe`` appends a note when not.
"""
from __future__ import annotations

import hashlib
import json
import os
from dataclasses import dataclass
from pathlib import Path

import pandas as pd

DEFAULT_ROOT = Path(r"D:\TWStockData\program_data\stock_daily_preopen")
LATEST_SCHEMA = "tw-stock-preopen-latest-v1"
SNAPSHOT_SCHEMA = "tw-stock-preopen-snapshot-v1"
MODEL_LABEL = "開盤前版 H1（HGB，含前一晚美股）"
REQUIRED_COLUMNS = (
    "signal_date", "target_date", "stock_id", "market", "p_up", "p_up_raw", "exp_ret", "exp_ret_raw",
    "rank", "rank_tradable", "tradable", "signal_close", "adj_close", "amt20", "feature_coverage",
)
US_STATUSES = ("ok", "holiday", "missing")
# t 日籌碼路由（法人、融資券、當沖、本益比、外資持股）。產製端比對各路由的中央最新日期與訊號日，寫入 manifest 的
# ``chips_freshness``：{status: ok|stale, signal_date, routes: {route: 最新日期}, route_status, stale_routes: [...]}；
# stale 時 degraded=true。使用者裁示（2026-10-18）：不設覆蓋率門檻、有資料就可用；未更新時只在報告說明最新資料到幾號。
CHIP_ROUTES = ("finmind_institutional", "finmind_margin", "finmind_day_trading", "finmind_per", "finmind_shareholding")
CHIP_ROUTE_LABELS_ZH = {"finmind_institutional": "法人", "finmind_margin": "融資券", "finmind_day_trading": "當沖",
                        "finmind_per": "本益比", "finmind_shareholding": "外資持股"}
CHIPS_STATUSES = ("ok", "stale")
CHIPS_STALE_PREFIX_ZH = "t 日籌碼未更新"
CHIPS_STALE_SUFFIX_ZH = "（以舊值推論，準確度可能下降）"
CHIPS_STALE_TEXT_ZH = CHIPS_STALE_PREFIX_ZH + CHIPS_STALE_SUFFIX_ZH  # manifest 缺日期細節時的通用文字
# 中央標示 PARTIAL（資料不完整）的路由只在說明顯示成功／應有數量，不阻擋、不設門檻；數量單位依中央擷取範圍而異，故不寫「檔」。
CHIPS_PARTIAL_PREFIX_ZH = "t 日籌碼資料不完整（中央回報成功/應有）"
CHIPS_PARTIAL_SUFFIX_ZH = "（缺的以舊值推論，準確度可能下降）"

# 每個 ``PreopenUnavailable.reason`` 代碼對應的中文說明（面向使用者的報告與通知使用）。
REASON_TEXT_ZH = {
    "preopen_no_latest": "找不到最新快照指標（latest.json）",
    "preopen_schema_mismatch": "最新快照指標的 schema 不符",
    "preopen_stale": "快照訊號日與預期不符（快照已過期）",
    "preopen_snapshot_missing": "找不到快照檔",
    "preopen_snapshot_sha_mismatch": "快照 sha256 不符",
    "preopen_snapshot_columns": "快照缺少必要欄位",
    "preopen_snapshot_values": "快照預測值為空或含 NaN",
    "preopen_us_status_invalid": "前一晚美股狀態無效",
    "preopen_parquet_engine_missing": "缺少 parquet 讀取元件且無 CSV 伴隨檔",
}
REASON_TEXT_UNKNOWN_ZH = "開盤前版不可用"


def reason_text(reason):
    """``代碼：中文`` for a reason code; unknown codes get the generic ``開盤前版不可用``."""
    code = str(reason)
    return f"{code}：{REASON_TEXT_ZH.get(code, REASON_TEXT_UNKNOWN_ZH)}"


def _ymd(value):
    """Display form of an ISO date (YYYY-MM-DD → YYYYMMDD); anything else is shown as text, None as 未知."""
    s = "" if value is None else str(value)
    if len(s) >= 10 and s[4] == "-" and s[7] == "-":
        return s[:10].replace("-", "")
    return s or "未知"


def chips_freshness_text(chips):
    """籌碼狀態的一段說明（YYYYMMDD）。stale：``t 日籌碼未更新：融資券最新到 20261015（以舊值推論，準確度可能下降）``
    （多個日期由舊到新分組，日期未知另列）；中央標示 PARTIAL 的路由另列成功／應有數量：
    ``t 日籌碼資料不完整（中央回報成功/應有）：法人 100/1215（缺的以舊值推論，準確度可能下降）``。
    兩者同時出現時合成一段。新鮮且完整、欄位不存在或型別異常回 None（舊快照相容）。"""
    if not isinstance(chips, dict):
        return None
    routes = chips.get("routes") if isinstance(chips.get("routes"), dict) else {}
    stale = chips.get("stale_routes") if isinstance(chips.get("stale_routes"), (list, tuple)) else []
    partial = chips.get("partial_routes") if isinstance(chips.get("partial_routes"), (list, tuple)) else []
    coverage = chips.get("coverage") if isinstance(chips.get("coverage"), dict) else {}
    by_date = {}
    for route in stale:
        day = routes.get(route)
        by_date.setdefault(str(day)[:10] if day else None, []).append(CHIP_ROUTE_LABELS_ZH.get(route, str(route)))
    parts = [f"{'、'.join(by_date[day])}最新到 {_ymd(day)}" for day in sorted(d for d in by_date if d)]
    if None in by_date:
        parts.append(f"{'、'.join(by_date[None])}日期未知")
    partial_txt = "、".join(f"{CHIP_ROUTE_LABELS_ZH.get(r, str(r))} {_coverage_zh(coverage.get(r))}"
                           for r in partial if r not in stale)
    if chips.get("status") == "stale":
        if not parts and not partial_txt:
            return CHIPS_STALE_TEXT_ZH
        body = "，".join(parts) or "最新日期未知"
        if partial_txt:
            body += f"；資料不完整（中央回報成功/應有）：{partial_txt}"
        return f"{CHIPS_STALE_PREFIX_ZH}：{body}{CHIPS_STALE_SUFFIX_ZH}"
    if partial_txt:
        return f"{CHIPS_PARTIAL_PREFIX_ZH}：{partial_txt}{CHIPS_PARTIAL_SUFFIX_ZH}"
    return None


def _coverage_zh(cov):
    """``成功/應有``（例如 ``100/1215``）；缺數字時 ``數量未知``。"""
    if isinstance(cov, dict) and cov.get("success") is not None and cov.get("total") is not None:
        return f"{cov['success']}/{cov['total']}"
    return "數量未知"


class PreopenUnavailable(RuntimeError):
    """No usable pre-open snapshot; ``reason`` is a short machine-readable code."""

    def __init__(self, reason, detail=""):
        super().__init__(f"{reason}: {detail}" if detail else reason)
        self.reason = reason
        self.detail = detail


@dataclass(frozen=True)
class PreopenSnapshot:
    meta: dict
    frame: pd.DataFrame

    @property
    def signal_date(self):
        return self.meta["signal_date"]

    @property
    def target_date(self):
        return self.meta["target_date"]

    @property
    def gate_us_up(self):
        """True when the US market rose overnight (validated Top10 gate), None if unknown."""
        return self.meta.get("gate_us_up")

    @property
    def chips_freshness(self):
        """籌碼新鮮度（manifest 的 ``chips_freshness`` dict）；舊快照沒有此欄位時為 None。"""
        value = self.meta.get("chips_freshness")
        return value if isinstance(value, dict) else None

    def row(self, stock_id):
        hit = self.frame[self.frame["stock_id"] == str(stock_id)]
        return None if hit.empty else hit.iloc[0].to_dict()

    def top(self, n=10, tradable_only=True):
        f = self.frame[self.frame["tradable"]] if tradable_only else self.frame
        return f.sort_values(["p_up", "exp_ret", "market", "stock_id"],
                             ascending=[False, False, True, True]).head(n).reset_index(drop=True)


def root_dir(root=None):
    return Path(root or os.environ.get("STOCK_DAILY_PREOPEN_ROOT") or DEFAULT_ROOT)


def _sha256(path):
    h = hashlib.sha256()
    with open(path, "rb") as fh:
        for chunk in iter(lambda: fh.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def read_latest_meta(root=None):
    base = root_dir(root)
    pointer = base / "predictions" / "latest.json"
    if not pointer.exists():
        raise PreopenUnavailable("preopen_no_latest", f"找不到 {pointer}")
    meta = json.loads(pointer.read_text(encoding="utf-8"))
    if meta.get("schema") != LATEST_SCHEMA:
        raise PreopenUnavailable("preopen_schema_mismatch",
                                 f"latest.json 的 schema 為 {meta.get('schema')}，預期 {LATEST_SCHEMA}")
    return meta


def _read_csv_fallback(base, meta, exc):
    """Companion CSV (same rows, sha-verified, full float precision) for consumers without a parquet engine."""
    rel = meta.get("snapshot_csv")
    if not rel:
        raise PreopenUnavailable("preopen_parquet_engine_missing",
                                 f"缺少 parquet 讀取元件且無 CSV 伴隨檔：{type(exc).__name__}: {exc}") from exc
    path = base / rel
    if not path.exists():
        raise PreopenUnavailable("preopen_snapshot_missing", f"找不到 {path}") from exc
    if _sha256(path) != meta.get("snapshot_csv_sha256"):
        raise PreopenUnavailable("preopen_snapshot_sha_mismatch", f"快照 sha256 不符：{path}") from exc
    return pd.read_csv(path, dtype={"stock_id": str, "market": str, "signal_date": str, "target_date": str,
                                    "tradable": str},
                       float_precision="round_trip")


def _tradable_values(values, read_format):
    """Reject missing/non-boolean parquet values; decode only explicit CSV boolean tokens."""
    if values.isna().any():
        raise PreopenUnavailable("preopen_snapshot_values", "tradable 含空值")
    if read_format == "csv":
        allowed = {"true": True, "false": False, "1": True, "0": False}
        parsed = values.map(lambda value: allowed.get(value.lower()) if isinstance(value, str) else None)
        if parsed.isna().any():
            raise PreopenUnavailable("preopen_snapshot_values", "tradable 必須為 true/false 或 0/1")
        return parsed.astype(bool)
    if not pd.api.types.is_bool_dtype(values.dtype) and not all(isinstance(value, bool) for value in values.tolist()):
        raise PreopenUnavailable("preopen_snapshot_values", "tradable 必須為真正布林值")
    return values.astype(bool)


def load_latest(root=None, expected_signal_date=None, verify_sha=True):
    """Load the newest snapshot. ``expected_signal_date`` (YYYY-MM-DD) enforces freshness:
    the snapshot must be for the consumer's own latest TW session."""
    base = root_dir(root)
    meta = read_latest_meta(base)
    if expected_signal_date is not None and meta.get("signal_date") != str(expected_signal_date)[:10]:
        raise PreopenUnavailable("preopen_stale",
                                 f"快照訊號日 {_ymd(meta.get('signal_date'))}，預期 {_ymd(expected_signal_date)}")
    snap = base / meta["snapshot"]
    if not snap.exists():
        raise PreopenUnavailable("preopen_snapshot_missing", f"找不到 {snap}")
    if verify_sha and _sha256(snap) != meta.get("snapshot_sha256"):
        raise PreopenUnavailable("preopen_snapshot_sha_mismatch", f"快照 sha256 不符：{snap}")
    read_format = "parquet"
    try:
        frame = pd.read_parquet(snap)
    except ImportError as exc:  # no pyarrow/fastparquet in this consumer's environment
        frame = _read_csv_fallback(base, meta, exc)
        read_format = "csv"
    missing = [c for c in REQUIRED_COLUMNS if c not in frame.columns]
    if missing:
        raise PreopenUnavailable("preopen_snapshot_columns", f"缺少欄位：{'、'.join(missing)}")
    if frame.empty or frame["p_up"].isna().any() or frame["exp_ret"].isna().any():
        raise PreopenUnavailable("preopen_snapshot_values", "預測值為空或含 NaN")
    frame = frame.copy()
    frame["stock_id"] = frame["stock_id"].astype(str)
    frame["tradable"] = _tradable_values(frame["tradable"], read_format)
    frame.attrs["snapshot_read_format"] = read_format
    us_status = meta.get("us_overnight", {}).get("status")
    if us_status not in US_STATUSES:
        raise PreopenUnavailable("preopen_us_status_invalid",
                                 f"美股狀態無效：{us_status}（允許 {'／'.join(US_STATUSES)}）")
    return PreopenSnapshot(meta=meta, frame=frame)


def describe(meta):
    """One-line human description used in consumer reports (Traditional Chinese)."""
    us = meta.get("us_overnight", {})
    status = {"ok": "前一晚美股收盤已納入", "holiday": "前一晚美股休市",
              "missing": "前一晚美股收盤缺漏（以缺值推論，準確度可能下降）"}.get(us.get("status"), "美股狀態未知")
    if us.get("status") == "missing":
        latest = us.get("latest_us_session")
        status += f"；美股資料最新日期 {_ymd(latest) if latest else '未提供'}"
    chips_note = chips_freshness_text(meta.get("chips_freshness"))
    chips_txt = f"｜{chips_note}" if chips_note else ""
    gate = meta.get("gate_us_up")
    gate_txt = "美股收漲（建議日）" if gate is True else ("美股未收漲（非建議日）" if gate is False else "美股漲跌未知")
    return (f"{MODEL_LABEL}｜訊號日 {_ymd(meta.get('signal_date'))} → 目標日 {_ymd(meta.get('target_date'))}"
            f"｜{status}{chips_txt}｜{gate_txt}｜模型 {str(meta.get('model_sha256', ''))[:12]}")
