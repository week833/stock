"""Read-only API of the H63 U7 snapshots for the single-stock report (ADR-0009).

Never writes, never computes U7 on the fly.  load_latest() verifies latest.json -> block header -> parquet sha ->
rows sha and the active model pointer; problems raise H63XsUnavailable(reason) so the report can show the reason
instead of numbers.  The fixed display texts of the H63 section live here (DISCLOSURE_VERSION).
"""
from __future__ import annotations

import datetime as dt
import hashlib
import json
import os
from pathlib import Path
from typing import Any, Dict, List, Optional

import numpy as np
import pandas as pd

import date_format as cd
import stock_h63_xs_core as core
import stock_h63_xs_ledger as ledger

DEFAULT_ROOT = Path(r"D:\TWStockData\program_data\stock_h63_xs")
ROOT_ENV = "STOCK_H63_XS_ROOT"
MAX_STALE_WEEKDAYS = 5
CURRENT_SCHEMA = "tw-stock-h63-xs-current-v1"
ARTIFACT_SCHEMA = "tw-stock-h63-xs-model-v1"
DISPLAY_MODES = ("auto", "off", "legacy")

REASON_TEXT_ZH = {
    "h63xs_no_latest": "尚未產生三個月相對強弱快照",
    "h63xs_schema_mismatch": "快照格式不符",
    "h63xs_snapshot_sha_mismatch": "快照檔案驗證失敗",
    "h63xs_snapshot_columns": "快照欄位不完整",
    "h63xs_snapshot_values": "快照數值不合理",
    "h63xs_future_signal": "快照日期晚於本次分析資料日",
    "h63xs_stale": "快照已超過 5 個平日未更新",
    "h63xs_model_integrity": "模型指標驗證失敗",
    "h63xs_stock_not_in_snapshot": "本股不在當日全市場快照內",
    "h63xs_market_mismatch": "快照的市場別與本報告不同",
    "h63xs_row_invalid": "本股快照列數值不合理",
    "h63xs_unexpected_error": "讀取快照時發生未預期的錯誤",
}
DISPLAY_LABELS_ZH = {
    "probability": "跑贏同期多數股票的機率",
    "relative_strength": "相對強弱",
    "rank": "合格母體排名",
    "band": "三個月報酬 80% 區間",
    "window": "訊號日至目標",
    "confidence": "信賴度",
    "model": "模型",
    "data": "資料",
    "forward": "前瞻紀錄",
    "notes": "註記",
}
TREND_TEXT_ZH = {
    "relatively_strong": "相對偏強",
    "neutral": "中性",
    "relatively_weak": "相對偏弱",
    "out_of_scope": "不在適用範圍",
}
TREND_SENTENCE_ZH = {
    "relatively_strong": "跑贏同期多數股票的機率達 55% 以上：模型判斷本股未來約三個月相對全市場偏強；這是相對排名訊號，"
                         "不代表大盤或本股一定上漲。",
    "neutral": "跑贏同期多數股票的機率介於 45% 至 55%：相對全市場的強弱不明顯。",
    "relatively_weak": "跑贏同期多數股票的機率在 45% 以下：模型判斷本股未來約三個月相對全市場偏弱；這是相對排名訊號，"
                       "不代表本股一定下跌。",
    "out_of_scope": "本股當日不屬上市櫃普通股，不在模型適用範圍，機率固定 50%，不提供方向判斷。",
}
FACTOR_LABELS_ZH = {
    "f_ivol60": "特異波動（60 日，越低越好）",
    "f_max5": "近一月最大 5 日漲幅平均（越低越好）",
    "f_hi52": "距 52 週高點（越接近越好）",
    "f_rev_yoy": "單月營收年增率（越高越好）",
    "f_rev_high12": "營收相對前 11 個月高點（越高越好）",
    "f_ep": "盈餘殖利率 E/P（前一交易日本益比換算）",
    "f_bm": "淨值市價比 B/M（前一交易日淨值比換算）",
}
DISCLOSURE_VERSION = "h63-xs-disclosure-v1"
DISCLOSURE_ROWS_ZH = (
    ("預測目標", "本股自訊號日收盤起第 63 個交易日收盤的報酬，高於同日合格上市櫃股票報酬中位數的機率（相對強弱）；"
                 "不是上漲機率，也不是可成交報酬。"),
    ("方法", "U7：7 個因子（特異波動、近月極端漲幅、52 週高點、單月營收年增、營收創高、E/P、B/M）對全市場的排名平均，"
             "經單一斜率換算成機率；不判斷大盤方向。"),
    ("回測（含設計期）", "2023 上半年至 2026 上半年七個半年區塊前推：漲跌方向準確率平均 54.7%（一律猜漲 47.5%）、"
                       "AUC 0.570、排名相關 IC 0.148、Brier 0.2465（常數為 0.25）；各區塊隨大盤擺動，單季可能低於 50%。"),
    ("與停用模型同期比較", "20260109 至 20260722（126 個訊號日）：漲跌方向準確率 54.3% 對 50.05%、AUC 0.572 對 0.502、"
                         "Brier 0.2467 對 0.2796（停用模型以相同配方在全母體重擬的代理版本計算）。"),
    ("設計後", "2026 年 7 月（18 個訊號日，設計後首次）：IC 0.094、漲跌方向 AUC 0.532；任何一季都可能沒有優勢。"),
    ("區間", "B3（60 日與 252 日波動混合）加 CQR252（以近一年已到期樣本校準）的 80% 名目區間；回測覆蓋 79.6%"
             "（各半年 75% 至 87%），平均寬度約 50 個百分點；不提供點預測。"),
    ("資料", "2025 年底以前的還原股價採 20261009 保存的歷史資料（補齊 2022 年 9 月至 2025 年 3 月的缺洞；接縫 20251231），之後採中央最新資料；中央資料標示不完整時只註記。"),
    ("前瞻紀錄", "自首個紀錄日起每日記錄全市場預測，63 個交易日後結算；正式檢討需 3 個 63 日區塊（約一年）。"),
    ("出處", "ADR-0009（H63 改用 U7 全市場排名模型）。"),
)
CAPTION_DISCLOSURE_ZH = ("揭露：只判斷相對全市場強弱、不判斷大盤；回測（含設計期）漲跌方向準確率約54.7%、AUC約0.57；"
                         "設計後AUC 0.53；機率只略優於50%；區間覆蓋約80%。")
CONFIDENCE_ZH = "低：只判斷相對全市場強弱，不判斷大盤方向；大盤下跌期間，相對偏強的股票仍可能下跌"
TARGET_TEXT_ZH = "收盤至之後第 63 個交易日收盤（約三個月；確切日期待交易日曆）"


class H63XsUnavailable(Exception):
    def __init__(self, reason: str, detail: str = ""):
        super().__init__(reason + (": " + detail if detail else ""))
        self.reason = reason
        self.detail = detail

    @property
    def reason_zh(self) -> str:
        return REASON_TEXT_ZH.get(self.reason, REASON_TEXT_ZH["h63xs_unexpected_error"])


def root_dir(root: Any = None) -> Path:
    if root is not None:
        return Path(root)
    value = os.environ.get(ROOT_ENV)
    return Path(value) if value else DEFAULT_ROOT


def _sha_file(path: Path) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as handle:
        for block in iter(lambda: handle.read(16 << 20), b""):
            h.update(block)
    return h.hexdigest()


def pointer_integrity(pointer: Dict[str, Any]) -> str:
    unsigned = {k: v for k, v in pointer.items() if k != "integrity_sha256"}
    return hashlib.sha256(core.canonical_json(unsigned)).hexdigest()


def load_model(root: Any = None) -> Dict[str, Any]:
    """Active model pointer + artifact after schema, integrity, artifact sha, recipe sha and guard checks."""
    base = root_dir(root)
    try:
        pointer = json.loads((base / "models" / "current.json").read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        raise H63XsUnavailable("h63xs_model_integrity", "current.json unreadable") from exc
    if pointer.get("schema") != CURRENT_SCHEMA or pointer.get("integrity_sha256") != pointer_integrity(pointer):
        raise H63XsUnavailable("h63xs_model_integrity", "pointer schema or integrity")
    path = base / "models" / "artifacts" / (str(pointer.get("model_sha256")) + ".json")
    try:
        if _sha_file(path) != pointer["model_sha256"]:
            raise H63XsUnavailable("h63xs_model_integrity", "artifact sha")
        artifact = json.loads(path.read_text(encoding="utf-8"))
    except OSError as exc:
        raise H63XsUnavailable("h63xs_model_integrity", "artifact unreadable") from exc
    if artifact.get("schema") != ARTIFACT_SCHEMA or (artifact.get("recipe") or {}).get("frozen_sha256") != \
            core.RECIPE_SHA256 or (pointer.get("recipe") or {}).get("frozen_sha256") != core.RECIPE_SHA256:
        raise H63XsUnavailable("h63xs_model_integrity", "recipe")
    b = float((artifact.get("slope") or {}).get("b", -1))
    if core.slope_guard(b) != "ok":
        raise H63XsUnavailable("h63xs_model_integrity", "slope guard")
    return {"pointer": pointer, "artifact": artifact, "path": str(path)}


def weekdays_between(earlier: Any, later: Any) -> int:
    a = np.datetime64(pd.Timestamp(earlier).date(), "D")
    b = np.datetime64(pd.Timestamp(later).date(), "D")
    return int(np.busday_count(a, b))


class H63XsSnapshot:
    def __init__(self, header: Dict[str, Any], frame: pd.DataFrame, latest: Dict[str, Any], model: Dict[str, Any],
                 lag_weekdays: int):
        self.header, self.frame, self.latest, self.model = header, frame, latest, model
        self.lag_weekdays = lag_weekdays
        self.signal_date = header["decision_date"]
        self._by_id = {sid: i for i, sid in enumerate(frame["stock_id"].astype(str))}

    def row(self, stock_id: Any) -> Optional[Dict[str, Any]]:
        i = self._by_id.get(str(stock_id))
        if i is None:
            return None
        record = ledger.row_record(self.frame, i)
        record["row_sha256"] = ledger.row_sha256(ledger.row_record(self.frame, i))
        return record

    @property
    def eligible_count(self) -> int:
        return int(self.frame["eligible"].astype(bool).sum())

    @property
    def universe_rows(self) -> int:
        return int(len(self.frame))


REQUIRED_COLUMNS = set(ledger.SNAPSHOT_COLUMNS)


def load_latest(root: Any = None, expected_signal_date: Any = None, verify: bool = True) -> H63XsSnapshot:
    base = root_dir(root)
    paths = ledger.Paths(base)
    try:
        latest = json.loads(paths.latest.read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        raise H63XsUnavailable("h63xs_no_latest") from exc
    if latest.get("schema") != ledger.LATEST_SCHEMA:
        raise H63XsUnavailable("h63xs_schema_mismatch", "latest")
    header_path = base / latest["header"]
    try:
        if verify and _sha_file(header_path) != latest["header_sha256"]:
            raise H63XsUnavailable("h63xs_snapshot_sha_mismatch", "header")
        header = json.loads(header_path.read_text(encoding="utf-8"))
        parquet_path = header_path.with_name(header["parquet_file"])
        if verify and _sha_file(parquet_path) != header["parquet_sha256"]:
            raise H63XsUnavailable("h63xs_snapshot_sha_mismatch", "parquet")
        frame = pd.read_parquet(parquet_path)
    except (OSError, ValueError, KeyError) as exc:
        raise H63XsUnavailable("h63xs_snapshot_sha_mismatch", type(exc).__name__) from exc
    if header.get("schema") != ledger.BLOCK_SCHEMA:
        raise H63XsUnavailable("h63xs_schema_mismatch", "block")
    if not REQUIRED_COLUMNS <= set(frame.columns):
        raise H63XsUnavailable("h63xs_snapshot_columns")
    if verify and ledger.rows_sha256(frame) != header.get("rows_sha256"):
        raise H63XsUnavailable("h63xs_snapshot_sha_mismatch", "rows")
    main = frame["main"].astype(bool).to_numpy()
    p = frame["p"].to_numpy(float)
    if not (np.isfinite(p[main]).all() and ((p[main] >= 0) & (p[main] <= 1)).all()):
        raise H63XsUnavailable("h63xs_snapshot_values")
    model = load_model(base)
    if (header.get("model") or {}).get("model_sha256") != model["pointer"]["model_sha256"]:
        header.setdefault("notes", []).append("snapshot_model_not_active")
    lag = 0
    if expected_signal_date is not None:
        expected = cd.calendar_day_of(expected_signal_date)
        if expected is None:
            raise ValueError("expected_signal_date must be a date (YYYYMMDD or YYYY-MM-DD)")
        signal = cd.parse_day(header["decision_date"])
        if signal > expected:
            raise H63XsUnavailable("h63xs_future_signal", header["decision_date"])
        lag = weekdays_between(signal, expected)
        if lag > MAX_STALE_WEEKDAYS:
            raise H63XsUnavailable("h63xs_stale", "%d weekdays" % lag)
    return H63XsSnapshot(header, frame, latest, model, lag)


def load_forward_summary(root: Any = None) -> Optional[Dict[str, Any]]:
    path = ledger.Paths(root_dir(root)).summary
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    if value.get("schema") != ledger.SUMMARY_SCHEMA:
        return None
    return value


def read_display_config(root: Any = None) -> Dict[str, Any]:
    """{'mode': auto | off | legacy, 'reason_zh', 'valid'}; a missing file means auto, a broken one is ignored."""
    path = root_dir(root) / "config" / "display.json"
    if not path.exists():
        return {"mode": "auto", "reason_zh": None, "valid": True, "present": False}
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
        mode = value.get("mode")
        if mode not in DISPLAY_MODES:
            raise ValueError("mode")
        reason = value.get("reason_zh")
        return {"mode": mode, "reason_zh": reason if isinstance(reason, str) and reason.strip() else None,
                "valid": True, "present": True}
    except (OSError, ValueError, AttributeError):
        return {"mode": "auto", "reason_zh": None, "valid": False, "present": True}


def describe(snapshot: H63XsSnapshot) -> Dict[str, Any]:
    h = snapshot.header
    return {"signal_date": h["decision_date"], "entry_key": h["entry_key"], "rows": snapshot.universe_rows,
            "eligible": snapshot.eligible_count, "model_sha256": (h.get("model") or {}).get("model_sha256"),
            "central_head_sha256": (h.get("data_provenance") or {}).get("central_head_sha256")}
