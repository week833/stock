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

Manifest field ``calendar_gate`` (added 2026-11-01, absent in older snapshots) records the producer's
verified-calendar check. Per the user ruling of 2026-11-01 the producer never refuses to publish on stale
data: ``calendar_gate.status`` (ok / calendar_stale / stale_signal / signal_gap_unverified / signal_not_session /
target_weekend / target_fixed_closure / target_declared_closure / target_verified_closure /
calendar_unavailable / central_max_date_unknown / closures_file_invalid / closed_day_cli_invalid) only
annotates, and ``describe`` appends a Traditional Chinese note when the status is not ok. Settlement
uses the verified calendar available then; an unresolved calendar remains pending. Manifest field ``ledger`` (v3.1, absent in older snapshots) records
whether the snapshot was written to the forward ledger; ``describe`` appends a note when that write failed
(the producer still publishes; such rows are never scored).
``load_latest`` preserves snapshot integrity and annotates older usable signals; a signal later than
the consumer's analysis date is still refused. ``snapshot_sha256`` covers the parquet file, not the manifest.
"""
from __future__ import annotations

import hashlib
import json
import os
from dataclasses import dataclass
from datetime import date
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
    "preopen_future_signal": "快照訊號日晚於本次分析資料日，不能用未來資料推論",
    "preopen_snapshot_missing": "找不到快照檔",
    "preopen_snapshot_sha_mismatch": "快照 sha256 不符",
    "preopen_snapshot_columns": "快照缺少必要欄位",
    "preopen_snapshot_values": "快照預測值為空或含 NaN",
    "preopen_us_status_invalid": "前一晚美股狀態無效",
    "preopen_parquet_engine_missing": "缺少 parquet 讀取元件且無 CSV 伴隨檔",
    "preopen_summary_missing": "找不到前瞻帳冊結算摘要（state/summary.json）",
    "preopen_summary_invalid": "前瞻帳冊結算摘要不是有效的 JSON 物件或沒有統計區塊",
    "preopen_summary_schema_mismatch": "前瞻帳冊結算摘要的 schema 不符",
}
REASON_TEXT_UNKNOWN_ZH = "開盤前版不可用"
# Settled forward-ledger summary (state/summary.json, written atomically by the producer's settle command).
# v1 (schema ...-v1) has only the top-level ``all`` block (latest-row scoring, no calendar check); v2 adds
# ``views.first_scorable_publication`` / ``views.last_publication`` (each with ``overall``), ``scoring.min_days`` and
# ``excluded`` counts while keeping ``all`` for compatibility. Consumers read it only through load_forward_summary.
FORWARD_SUMMARY_SCHEMAS = {"tw-stock-preopen-forward-summary-v1": 1, "tw-stock-preopen-forward-summary-v2": 2}
FORWARD_SUMMARY_VIEWS = ("first_scorable_publication", "last_publication", "all")


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


# 日曆守門（manifest ``calendar_gate``，2026-11-01 起）：產製端只註記、不阻擋發布；status 不為 ok 時 describe() 附加一段說明，
# 舊快照（沒有 calendar_gate）與 status=ok 的文字逐字不變。日期一律 YYYYMMDD。
CALENDAR_GATE_OK = "ok"
CALENDAR_GATE_TARGET_WHY_ZH = {
    "target_weekend": "週末", "target_fixed_closure": "固定假日／補假", "target_declared_closure": "已宣告休市日",
    "target_verified_closure": "已驗證日曆中的非交易日（可能為臨時休市）", "target_not_after_signal": "不晚於訊號日的日期",
}
CALENDAR_STALE_PREFIX_ZH = "中央價格落後"
CALENDAR_PROOF_STALE_PREFIX_ZH = "已驗證日曆落後"
CALENDAR_GAP_PREFIX_ZH = "中央價格可能落後"
CALENDAR_UNAVAILABLE_PREFIX_ZH = "已驗證日曆不可用"
CALENDAR_CLOSURES_FILE_TEXT_ZH = "宣告休市設定檔無法使用（已忽略）"
CALENDAR_MAX_DATE_UNKNOWN_TEXT_ZH = "中央價格最新日未知（head 中繼資料缺 actual_max_date），訊號日未與中央核對"  # v3.1
CALENDAR_CLOSED_DAY_CLI_PREFIX_ZH = "命令列宣告的休市日格式無效"  # v3.1：--closed-day 的值無法解析（已忽略）
LEDGER_ERROR_TEXT_ZH = "帳冊寫入失敗（本快照未入帳，結算時不計分）"  # v3.1：manifest ledger.status=error


def calendar_gate_text(gate, meta=None):
    """日曆守門的一段說明（status 不為 ok 時），例如
    ``中央價格落後：本快照以 20261027 訊號日重發，目標日 20261029（跨 2 個交易日），結算時不計入實盤統計``、
    ``目標日 20261023 為已驗證日曆中的非交易日（可能為臨時休市），結算時不計分``。多個原因以「；」相接（status 在前）；
    目標日／訊號日已判不計分（target_*／signal_not_session）時，stale_signal 一段不再補「預期 1 個交易日」（v3.1）。
    gate 不是 dict、沒有 status、status 為 ok 或型別異常時回 None（舊快照相容）。"""
    if not isinstance(gate, dict):
        return None
    status = gate.get("status")
    if not isinstance(status, str) or status == CALENDAR_GATE_OK:
        return None
    meta = meta if isinstance(meta, dict) else {}
    signal = _ymd(gate.get("signal_date") or meta.get("signal_date"))
    target = _ymd(gate.get("target_date") or meta.get("target_date"))
    reasons = gate.get("reasons") if isinstance(gate.get("reasons"), (list, tuple)) else []
    codes = list(dict.fromkeys([status] + [c for c in reasons if isinstance(c, str)]))
    n = gate.get("sessions_ahead_expected")
    n = n if isinstance(n, int) and not isinstance(n, bool) and n >= 1 else None
    gap = gate.get("gap_days") if isinstance(gate.get("gap_days"), (list, tuple)) else []
    unexplained = "、".join(_ymd(d.get("date")) for d in gap if isinstance(d, dict) and d.get("status") == "unverified")
    void = any(code in CALENDAR_GATE_TARGET_WHY_ZH or code == "signal_not_session" for code in codes)  # 該列結算必不計分
    parts = []
    for code in codes:
        if code in CALENDAR_GATE_TARGET_WHY_ZH:
            parts.append(f"目標日 {target} 為{CALENDAR_GATE_TARGET_WHY_ZH[code]}，結算時不計分")
        elif code == "stale_signal":
            if void:  # 目標日／訊號日已判不計分：不再補「預期 1 個交易日」或「跨 N 個交易日」（v3.1）
                span = ""
            elif n and n >= 2:
                span = f"（跨 {n} 個交易日），結算時不計入實盤統計"
            else:
                span = "（預期 1 個交易日）"
            parts.append(f"{CALENDAR_STALE_PREFIX_ZH}：本快照以 {signal} 訊號日重發，目標日 {target}{span}")
        elif code == "calendar_stale":
            last = _ymd(gate.get("last_verified_session"))
            parts.append(f"{CALENDAR_PROOF_STALE_PREFIX_ZH}：最後交易日 {last}，訊號日 {signal}；"
                         "訊號日與目標日尚未完成日曆核對，是否計分以結算時已驗證日曆為準")
        elif code == "signal_gap_unverified":
            span = f"（可能跨 {n} 個交易日）" if n and n >= 2 else ""
            parts.append(f"{CALENDAR_GAP_PREFIX_ZH}（{unexplained or '部分平日'} 不在已驗證日曆中、也非已知休市日）："
                         f"本快照以 {signal} 訊號日重發，目標日 {target}{span}；"
                         "是否計分以結算時已驗證日曆為準")
        elif code == "signal_not_session":
            parts.append(f"訊號日 {signal} 不是已驗證交易日，結算時不計分")
        elif code == "calendar_unavailable":
            parts.append(f"{CALENDAR_UNAVAILABLE_PREFIX_ZH}（{gate.get('calendar_error_code') or '原因未知'}），訊號日與目標日未經核對；"
                         f"目標日 {target} 為假設值，結算時以日曆為準")
        elif code == "closures_file_invalid":
            parts.append(CALENDAR_CLOSURES_FILE_TEXT_ZH)
        elif code == "central_max_date_unknown":
            parts.append(CALENDAR_MAX_DATE_UNKNOWN_TEXT_ZH)
        elif code == "closed_day_cli_invalid":
            bad = gate.get("closed_day_invalid") if isinstance(gate.get("closed_day_invalid"), (list, tuple)) else []
            bad_txt = "、".join(str(v) for v in bad)
            parts.append(f"{CALENDAR_CLOSED_DAY_CLI_PREFIX_ZH}（{bad_txt + '，' if bad_txt else ''}已忽略）")
        else:
            parts.append(f"日曆守門狀態 {code}")
    return "；".join(parts)


def ledger_text(ledger):
    """帳冊寫入結果的一段說明（v3.1）：manifest ``ledger.status`` 為 error 時「帳冊寫入失敗（本快照未入帳，結算時不計分）」；
    ok／skipped（--no-latest）、欄位不存在（v3.1 之前的快照）或型別異常回 None。"""
    if not isinstance(ledger, dict) or ledger.get("status") != "error":
        return None
    return LEDGER_ERROR_TEXT_ZH


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

    @property
    def calendar_gate(self):
        """日曆守門結果（manifest 的 ``calendar_gate`` dict，2026-11-01 起只註記不阻擋）；舊快照沒有此欄位時為 None。"""
        value = self.meta.get("calendar_gate")
        return value if isinstance(value, dict) else None

    @property
    def calendar_note_zh(self):
        """共用日曆註記；load_latest 已格式化時直接沿用。"""
        return (self.meta["calendar_note_zh"] if "calendar_note_zh" in self.meta
                else calendar_gate_text(self.meta.get("calendar_gate"), self.meta))

    @property
    def ledger_note_zh(self):
        """共用帳冊註記；舊快照或正常帳冊為 None。"""
        return (self.meta["ledger_note_zh"] if "ledger_note_zh" in self.meta
                else ledger_text(self.meta.get("ledger")))

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
    meta["calendar_note_zh"] = calendar_gate_text(meta.get("calendar_gate"), meta)
    meta["ledger_note_zh"] = ledger_text(meta.get("ledger"))
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
    """Load an integrity-checked snapshot; older signals are annotated, future signals refused.

    ``expected_signal_date`` is the consumer's analysis data date, not the prediction target date.
    The snapshot's original signal/target and model values are retained.
    """
    base = root_dir(root)
    meta = read_latest_meta(base)
    if expected_signal_date is not None:
        expected = str(expected_signal_date)[:10]
        signal = meta.get("signal_date")
        try:
            valid_signal = (isinstance(signal, str) and len(signal) == 10
                            and date.fromisoformat(signal).isoformat() == signal)
        except ValueError:
            valid_signal = False
        if not valid_signal:
            raise PreopenUnavailable("preopen_snapshot_values", "快照訊號日必須為有效的 YYYY-MM-DD 日期")
        if signal > expected:
            raise PreopenUnavailable("preopen_future_signal",
                                     f"快照訊號日 {_ymd(signal)}，本次分析資料日 {_ymd(expected)}")
        if signal != expected:
            meta["snapshot_freshness"] = {"status": "stale", "signal_date": signal,
                                          "expected_signal_date": expected}
            meta["snapshot_note_zh"] = (f"沿用較舊快照：訊號日 {_ymd(signal)}；"
                                         f"本次分析資料日 {_ymd(expected)}；"
                                         f"保留原目標日 {_ymd(meta.get('target_date'))}")
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


def forward_summary_path(root=None):
    """``state/summary.json`` under the data root (the producer's settle command writes it with os.replace)."""
    return root_dir(root) / "state" / "summary.json"


def load_forward_summary(root=None):
    """Read the settled forward-ledger summary through the consumer API (consumers never open state/ themselves).

    Returns ``{"path", "schema", "version", "generated_at", "min_days", "selection_unit", "excluded", "views"}``; ``views`` maps
    ``first_scorable_publication`` and ``last_publication`` to the v2 ``views.<name>.overall`` block and ``all`` to the
    top-level block (v1 and v2). Named views are None for v1; v2 requires its two named overall blocks.
    Values are returned as stored (no scoring here).
    Raises PreopenUnavailable with ``preopen_summary_missing`` (no file), ``preopen_summary_invalid`` (unreadable,
    not a JSON object, or no statistics block) or ``preopen_summary_schema_mismatch`` (unknown schema).
    """
    path = forward_summary_path(root)
    try:
        raw = path.read_text(encoding="utf-8-sig")
    except FileNotFoundError as exc:
        raise PreopenUnavailable("preopen_summary_missing", f"找不到 {path}") from exc
    except (OSError, ValueError) as exc:
        raise PreopenUnavailable("preopen_summary_invalid", f"無法讀取 {path}：{type(exc).__name__}") from exc
    try:
        document = json.loads(raw)
    except ValueError as exc:
        raise PreopenUnavailable("preopen_summary_invalid", f"{path} 不是有效的 JSON") from exc
    if not isinstance(document, dict):
        raise PreopenUnavailable("preopen_summary_invalid", f"{path} 不是 JSON 物件")
    schema = document.get("schema")
    version = FORWARD_SUMMARY_SCHEMAS.get(schema) if isinstance(schema, str) else None
    if version is None:
        raise PreopenUnavailable("preopen_summary_schema_mismatch",
                                 f"summary.json 的 schema 為 {schema}，預期 {'／'.join(FORWARD_SUMMARY_SCHEMAS)}")
    raw_views = document.get("views") if isinstance(document.get("views"), dict) else {}
    views = {}
    for name in FORWARD_SUMMARY_VIEWS[:-1]:
        view = raw_views.get(name)
        overall = view.get("overall") if isinstance(view, dict) else None
        views[name] = dict(overall) if isinstance(overall, dict) else None
    views["all"] = dict(document["all"]) if isinstance(document.get("all"), dict) else None
    if version == 2 and any(views[name] is None for name in FORWARD_SUMMARY_VIEWS[:-1]):
        raise PreopenUnavailable("preopen_summary_invalid",
                                 f"{path} 缺少 v2 可計分發布統計區塊（first_scorable_publication／last_publication）")
    if all(block is None for block in views.values()):
        raise PreopenUnavailable("preopen_summary_invalid", f"{path} 沒有統計區塊（views.*.overall 或 all）")
    scoring = document.get("scoring") if isinstance(document.get("scoring"), dict) else {}
    min_days = scoring.get("min_days")
    excluded = document.get("excluded") if isinstance(document.get("excluded"), dict) else {}
    generated_at = document.get("generated_at")
    return {"path": str(path), "schema": schema, "version": version,
            "generated_at": generated_at if isinstance(generated_at, str) else None,
            "min_days": min_days if isinstance(min_days, int) and not isinstance(min_days, bool) else None,
            "selection_unit": scoring.get("selection_unit"), "excluded": dict(excluded), "views": views}


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
    calendar_note = (meta["calendar_note_zh"] if "calendar_note_zh" in meta
                     else calendar_gate_text(meta.get("calendar_gate"), meta))
    calendar_txt = f"｜{calendar_note}" if calendar_note else ""
    ledger_note = meta["ledger_note_zh"] if "ledger_note_zh" in meta else ledger_text(meta.get("ledger"))
    ledger_txt = f"｜{ledger_note}" if ledger_note else ""
    snapshot_note = meta.get("snapshot_note_zh")
    snapshot_txt = f"｜{snapshot_note}" if snapshot_note else ""
    gate = meta.get("gate_us_up")
    gate_txt = "美股收漲（建議日）" if gate is True else ("美股未收漲（非建議日）" if gate is False else "美股漲跌未知")
    return (f"{MODEL_LABEL}｜訊號日 {_ymd(meta.get('signal_date'))} → 目標日 {_ymd(meta.get('target_date'))}"
            f"｜{status}{chips_txt}{calendar_txt}{ledger_txt}{snapshot_txt}｜{gate_txt}｜模型 {str(meta.get('model_sha256', ''))[:12]}")
