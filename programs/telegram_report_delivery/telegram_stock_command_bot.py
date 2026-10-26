from __future__ import annotations

import argparse
import errno
import hashlib
import importlib
import json
import logging
import math
import os
import queue
import re
import stat
import subprocess
import sys
import tempfile
import threading
import time
import uuid
from contextlib import contextmanager
from dataclasses import dataclass, replace as dataclass_replace
from datetime import datetime
from pathlib import Path
from typing import Any, Callable, Iterator, TextIO

import msvcrt

from telegram_report_sender import (
    CHAT_ID_PATTERN,
    MAX_TELEGRAM_DOCUMENT_BYTES,
    TOKEN_PATTERN,
    TRUE_VALUES,
    ConfigurationError,
    TelegramApiError,
    TelegramClient,
    get_managed_path,
    load_env_file,
)


SUITE_DIR = Path(__file__).resolve().parent
STOCK_ROOT = SUITE_DIR.parent.parent
SHARED_DIR = SUITE_DIR.parent / "shared"
if str(SHARED_DIR) not in sys.path:
    sys.path.insert(0, str(SHARED_DIR))



import date_format as cd  # noqa: E402  (日期：顯示一律 YYYYMMDD，讀取兩種拼寫都收)


LOCAL_ENV_PATH = SUITE_DIR / ".ENV"

TELEGRAM_ROOT = get_managed_path("telegram")
DSTOCK_SINGLE_ROOT = get_managed_path("dstock_single")
DATA_DIR = TELEGRAM_ROOT / "data"
LOG_DIR = TELEGRAM_ROOT / "logs"
JOBS_DIR = DSTOCK_SINGLE_ROOT / "telegram_jobs"
STATE_PATH = DATA_DIR / "stock_command_state.json"
LOCK_PATH = DATA_DIR / "stock_command_bot.lock"
OWNER_PATH = DATA_DIR / "stock_command_bot.owner.json"
HEARTBEAT_PATH = DATA_DIR / "stock_command_bot.heartbeat.json"
DELIVERY_AUDIT_PATH = DATA_DIR / "stock_command_bot.delivery.jsonl"
SERVICE_HEARTBEAT_STALE_SECONDS = 180
HEARTBEAT_WRITE_MAX_ATTEMPTS = 5
HEARTBEAT_WRITE_RETRY_SECONDS = 0.2
HEARTBEAT_ENOSPC_LOG_INTERVAL_SECONDS = 60 * 60
ANALYSIS_CLEANUP_TIMEOUT_SECONDS = 15
WORKER_JOIN_TIMEOUT_SECONDS = 15
ANALYSIS_ERROR_TAIL_MAX_BYTES = 8192
ANALYSIS_ERROR_TAIL_MAX_CHARS = 2000
DELIVERY_RECEIPT_NAME = "telegram_delivery.json"
DELIVERY_RECEIPT_MAX_BYTES = 32768
SINGLE_STOCK_PHASE_ENV = "DSTOCK_SINGLE_STOCK_PHASE_PROGRESS"
REPORT_COMPARISON_CACHE_PREFIX = "DSTOCK_REPORT_COMPARISON_CACHE_"
SINGLE_STOCK_PHASE_SCHEMA = "single-stock-progress-v1"
SINGLE_STOCK_PHASE_PREFIX = b"[SINGLE_STOCK_PHASE] "
SINGLE_STOCK_PHASE_MAX_LINE_BYTES = 8192
SINGLE_STOCK_PHASE_READ_MAX_BYTES = 65536
SINGLE_STOCK_PHASE_INTERVAL_SECONDS = 30
SINGLE_STOCK_PREPARATION_TIMEOUT_ENV = "TELEGRAM_COMMAND_PREPARATION_TIMEOUT_MINUTES"
SINGLE_STOCK_PHASES = ("data_check", "data_prepare", "model", "pdf")
SINGLE_STOCK_PHASE_LABELS = {
    "data_check": "資料檢查",
    "data_prepare": "資料準備",
    "model": "模型分析",
    "pdf": "報告產製",
    "delivery": "Telegram 傳送",
    "complete": "工作完成",
    "failed": "分析未完成",
}
SINGLE_STOCK_PHASE_RANK = {phase: rank for rank, phase in enumerate(SINGLE_STOCK_PHASES)}
# DStock_MARKET_UPDATE_EXPECTED_ASOF was removed: no child (single src, shared,
# telegram) ever read it, and analysis follows the data actually read.
CURRENT_NETWORK_DISABLED_ENV = "DStock_SHARED_RAW_NETWORK_DISABLED"

BLOCKED_PROVIDER_ENV_FRAGMENTS = (
    "finmind",
    "twse",
    "tpex",
    "gdelt",
    "yfinance",
)
SENSITIVE_ENV_NAME_PATTERN = re.compile(
    r"(?:token|secret|password|passwd|api[_-]?key|authorization|chat[_-]?id)",
    re.IGNORECASE,
)
SENSITIVE_ASSIGNMENT_PATTERN = re.compile(
    r"(?i)([\"']?\b[\w.-]*(?:token|secret|password|passwd|api[_-]?key|authorization|chat[_-]?id)[\w.-]*\b[\"']?\s*(?:=|:)\s*)([\"'][^\"']*[\"']|[^\s,;}]+)"
)
TELEGRAM_TOKEN_IN_TEXT_PATTERN = re.compile(r"\b\d{6,}:[A-Za-z0-9_-]{20,}\b")
ANALYSIS_DIR = STOCK_ROOT / "programs" / "single_stock_analysis" / "src"
C0_ANALYSIS_SCRIPT = ANALYSIS_DIR / "tw_single_stock_c0.py"
ANALYSIS_ENTRY_SCRIPT = ANALYSIS_DIR / "run_single_stock_entry.py"
ANALYSIS_PYTHON_RESOLVER = STOCK_ROOT / "programs" / "shared" / "find_python.cmd"
ANALYSIS_PYTHON_MARKER = "__DSTOCK_ANALYSIS_PYTHON__="
ACTIVE_STATUSES = {"queued", "running"}
MODE_H63 = "H63"
MODE_H1 = "H1"
MODE_BOTH = "both"
MODE_LABELS = {
    MODE_H63: "第63個市場交易日收盤估計",
    MODE_H1: "第1個市場交易日收盤估計",
    MODE_BOTH: "第63及第1個市場交易日收盤估計",
}
MODE_HORIZONS = {MODE_H63: 63, MODE_H1: 1}
C0_SINGLE_REPORT_SCHEMA = "single-stock-daily-c0-estimate-v1"
SELECTED_DEPLOYMENT_SCHEMA = "single-stock-selected-deployment-v1"
SELECTED_RUNTIME_BINDING_SCHEMA = "single-stock-selected-runtime-binding-v1"
SELECTED_SINGLE_REPORT_SCHEMA = "single-stock-selected-formal-report-v1"
SELECTED_PROJECTION_NOT_READY_PATTERN = re.compile(
    r"(?<![A-Za-z0-9_])selected_required_feature_projection_not_ready(?![A-Za-z0-9_])"
)
C0_ESTIMATE_REPORT_CLASS = "daily_estimate_effect_unknown"
# tw_single_stock_c0.py NO_ESTIMATE_CLASS: the stock was excluded (stale, unknown
# classification, calendar/253-window) and the report only states why and the data time.
C0_NO_ESTIMATE_REPORT_CLASS = "c0_no_estimate_data_time_only"
C0_PINNED_HEAD_UNAVAILABLE = "pinned_head_unavailable_used_current"
# Sealed H1 block written by single_stock_analysis\src\single_stock_preopen.py (開盤前版 H1 或回退原因).
PREOPEN_H1_SCHEMA = "single-stock-preopen-h1-v1"
# 開盤前版 H1 的必要揭露依快照 manifest 的 recipe.name 選擇（讀區塊 meta 摘要，其次 disclosure.recipe）；
# 舊快照沒有 recipe 欄位＝v1。v1 與改版前完全相同：數字取自封存區塊的 disclosure（與單股 PDF 同源）。
# v2 數字出自 PREOPEN_V2_SOURCE 第 6 節；未知配方只顯示中性說明與配方名稱，不顯示任何保留期數字。
PREOPEN_RECIPE_V1 = "h1pre_final_nogc_xs"
PREOPEN_RECIPE_V2 = "h1pre_v2_ens"
PREOPEN_V2_SOURCE = (r"D:\TWStockData\program_data\stock_daily_c0\candidates\h1_improve_20261021\report"
                     r"\H1改良研究報告_20261021.md")
PREOPEN_DISCLOSURE_BY_RECIPE: dict[str, dict[str, Any]] = {
    PREOPEN_RECIPE_V1: {
        "facts": None, "holdout_period": "2026-01～2026-10",
        "open_entry_value": -0.0004, "open_entry_note": "（為負）",
    },
    PREOPEN_RECIPE_V2: {
        "facts": {
            "holdout_start": "2026-01-02", "holdout_end": "2026-10-19", "holdout_sessions": 191,
            "holdout_direction_accuracy": 0.600, "always_down_accuracy": 0.561, "holdout_auc": 0.619,
            "us_up_day_tradable_top10_hit_rate": 0.653, "other_days_tradable_top10_hit_rate": 0.491,
            "all_days_tradable_top10_hit_rate": 0.571, "tradable_top10_mae": 0.0271,
            "next_open_buy_top10_mean_daily_return": 0.0001, "source": PREOPEN_V2_SOURCE,
        },
        "open_entry_value": 0.0001, "open_entry_note": "（約為零）",
    },
}
PREOPEN_RECIPE_NAME_DISPLAY_MAX = 64
SHA256_HEX_PATTERN = re.compile(r"[0-9a-f]{64}")

LOGGER = logging.getLogger("telegram_stock_command_bot")


def _source_sha256(path: Path) -> str:
    digest = hashlib.sha256()
    total_bytes = 0
    try:
        with path.open("rb") as handle:
            for chunk in iter(lambda: handle.read(1024 * 1024), b""):
                total_bytes += len(chunk)
                digest.update(chunk)
    except OSError as exc:
        raise ConfigurationError(f"Telegram source 無法讀取：{path}") from exc
    if total_bytes == 0:
        raise ConfigurationError(f"Telegram source 是空檔案：{path}")
    value = digest.hexdigest().lower()
    if not re.fullmatch(r"[0-9a-f]{64}", value):
        raise ConfigurationError("Telegram source SHA256 格式無效")
    return value


SOURCE_PATH = Path(__file__).resolve()
SOURCE_SHA256 = _source_sha256(SOURCE_PATH)
if SOURCE_SHA256 != _source_sha256(SOURCE_PATH):
    raise ConfigurationError("Telegram source SHA256 在初始化期間變動")
RUNTIME_REVISION = f"telegram_stock_command_bot@{SOURCE_SHA256[:16]}"


HELP_TEXT = """Telegram 個股收盤估計指令

直接輸入代碼或名稱：H63 收盤估計
2330
台積電
/h63 台積電

H1 收盤估計：
/h1 2330
明日 2330
/open 台積電
/tomorrow 台積電
明日／open／tomorrow／開盤均為 H1 收盤估計的相容指令。

兩份收盤報告：
全部 2330
/all 台積電
/both 2330

其他指令：
/status 查詢佇列
/help 顯示說明

H1 與 H63 的目標均為訊號收盤至同市場第 H 個官方交易日收盤。
H1 優先顯示開盤前版（含前一晚美股）；不可用時改用 C0 基準並註明原因。
名稱符合多檔時，Bot 會列出代碼供重新輸入。
歷史PIT、公司行動與交易效果未驗證。"""

READY_TEXT = "Telegram 個股服務已啟用。輸入代碼或名稱產生H63收盤估計；/h1、明日、/open、/tomorrow產生H1收盤估計；/all產生兩份收盤報告。歷史PIT與效果未驗證。"

class CommandBotError(RuntimeError):
    pass


class QueueFullError(CommandBotError):
    pass


class AnalysisProcessError(CommandBotError):
    pass


class C0BindingIntegrityError(AnalysisProcessError):
    """C0IntegrityError while building the runtime binding (KEEP: never a label)."""


class SelectedBindingIntegrityError(C0BindingIntegrityError):
    """Selected authority failures must not fall back to the legacy C0 model."""


def _analysis_failure_message(
    query: str, job_id: str, error: AnalysisProcessError, *, report_already_sent: bool = False,
) -> str:
    if SELECTED_PROJECTION_NOT_READY_PATTERN.search(str(error)):
        report_status = (
            "本份報告尚未產生或傳送；先前已傳送的報告不受影響。"
            if report_already_sent else "尚未產生或傳送報告。"
        )
        return (
            f"資料準備中｜{query}\n"
            "最新資料的個股查詢快取尚未備妥，請稍後重新查詢。\n"
            f"{report_status}\n工作編號：{job_id}"
        )
    return f"分析失敗｜{query}\n原因：{cd.display_text(str(error))}\n工作編號：{job_id}"


@dataclass(frozen=True)
class ParsedCommand:
    action: str
    mode: str = ""
    query: str = ""


@dataclass(frozen=True)
class StockMatch:
    code: str
    name: str
    match_type: str
    score: float


@dataclass(frozen=True)
class GeneratedReport:
    mode: str
    pdf_path: Path
    json_path: Path | None
    caption: str
    pdf_sha256: str = ""
    json_sha256: str = ""
    output_dir: Path | None = None
    declared_paths: tuple[Path, Path] | None = None



@dataclass(frozen=True)
class SingleStockPhase:
    phase: str
    completed: int | None = None
    total: int | None = None


class SingleStockPhaseReader:
    def __init__(self, stock_id: str) -> None:
        self.stock_id = stock_id
        self.offset = 0
        self.pending = bytearray()
        self.discarding_long_line = False
        self.phase = ""

    @staticmethod
    def _strict_object(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
        result: dict[str, Any] = {}
        for key, value in pairs:
            if key in result:
                raise ValueError("duplicate JSON key")
            result[key] = value
        return result

    @classmethod
    def _parse_line(cls, line: bytes, stock_id: str, current_phase: str) -> SingleStockPhase | None:
        if not line.startswith(SINGLE_STOCK_PHASE_PREFIX):
            return None
        try:
            text = line[len(SINGLE_STOCK_PHASE_PREFIX):].decode("utf-8", errors="strict")
            payload = json.loads(
                text,
                object_pairs_hook=cls._strict_object,
                parse_constant=lambda _: (_ for _ in ()).throw(ValueError("invalid JSON constant")),
            )
        except (UnicodeError, ValueError, json.JSONDecodeError):
            return None
        if not isinstance(payload, dict):
            return None
        if set(payload) - {"schema", "phase", "stock_id", "detail", "completed", "total"}:
            return None
        if payload.get("schema") != SINGLE_STOCK_PHASE_SCHEMA or payload.get("stock_id") != stock_id:
            return None
        phase = payload.get("phase")
        if not isinstance(phase, str) or phase not in SINGLE_STOCK_PHASE_RANK:
            return None
        if not isinstance(payload.get("stock_id"), str) or len(payload["stock_id"]) > 64:
            return None
        if current_phase and SINGLE_STOCK_PHASE_RANK[phase] < SINGLE_STOCK_PHASE_RANK[current_phase]:
            return None
        detail = payload.get("detail")
        if "detail" in payload and (
            not isinstance(detail, str)
            or len(detail) > 160
            or any(ord(char) < 32 or ord(char) == 127 for char in detail)
        ):
            return None
        completed = payload.get("completed")
        total = payload.get("total")
        for key, value in (("completed", completed), ("total", total)):
            if key in payload and (type(value) is not int or value < 0 or value > 2_147_483_647):
                return None
        if total is not None and total == 0:
            return None
        if completed is not None and total is not None and completed > total:
            return None
        return SingleStockPhase(phase, completed, total)

    def _consume_line(self, line: bytes) -> SingleStockPhase | None:
        event = self._parse_line(line.rstrip(b"\r"), self.stock_id, self.phase)
        if event is not None:
            self.phase = event.phase
        return event

    def read_available(self, path: Path) -> list[SingleStockPhase]:
        try:
            size = path.stat().st_size
            if size < self.offset:
                self.offset = 0
                self.pending.clear()
                self.discarding_long_line = False
                self.phase = ""
            with path.open("rb") as handle:
                handle.seek(self.offset)
                chunk = handle.read(SINGLE_STOCK_PHASE_READ_MAX_BYTES)
                self.offset += len(chunk)
        except OSError:
            return []
        events: list[SingleStockPhase] = []
        cursor = 0
        while cursor < len(chunk):
            if self.discarding_long_line:
                newline = chunk.find(b"\n", cursor)
                if newline < 0:
                    return events
                cursor = newline + 1
                self.discarding_long_line = False
                continue
            newline = chunk.find(b"\n", cursor)
            if newline < 0:
                tail = chunk[cursor:]
                if len(self.pending) + len(tail) > SINGLE_STOCK_PHASE_MAX_LINE_BYTES:
                    self.pending.clear()
                    self.discarding_long_line = True
                else:
                    self.pending.extend(tail)
                break
            segment = chunk[cursor:newline]
            if len(self.pending) + len(segment) > SINGLE_STOCK_PHASE_MAX_LINE_BYTES:
                self.pending.clear()
            else:
                line = bytes(self.pending) + segment
                event = self._consume_line(line)
                if event is not None:
                    events.append(event)
                self.pending.clear()
            cursor = newline + 1
        return events


def now_iso() -> str:
    return datetime.now().astimezone().isoformat(timespec="seconds")


def setup_logging() -> None:
    formatter = logging.Formatter("%(asctime)s %(levelname)s %(message)s", "%Y%m%d %H:%M:%S")
    for handler in LOGGER.handlers:
        handler.close()
    LOGGER.handlers.clear()
    LOGGER.setLevel(logging.INFO)
    if sys.stdout is not None and sys.stdout.isatty():
        stream_handler = logging.StreamHandler(sys.stdout)
        stream_handler.setFormatter(formatter)
        LOGGER.addHandler(stream_handler)
    else:
        LOGGER.addHandler(logging.NullHandler())
    LOGGER.propagate = False


def int_setting(values: dict[str, str], key: str, default: int, minimum: int, maximum: int) -> int:
    try:
        value = int(str(values.get(key, default)).strip())
    except (TypeError, ValueError):
        value = default
    return max(minimum, min(maximum, value))


def parse_command(text: str) -> ParsedCommand:
    value = str(text or "").replace("\u3000", " ").strip()
    if not value:
        return ParsedCommand("ignore")
    lowered = value.casefold()
    if lowered in {"/start", "/help", "help", "說明", "使用說明"}:
        return ParsedCommand("help")
    if lowered in {"/status", "status", "狀態", "佇列"}:
        return ParsedCommand("status")

    parts = value.split(maxsplit=1)
    command = re.sub(r"@[A-Za-z0-9_]+$", "", parts[0]).casefold()

    aliases = {
        "/stock": MODE_H63,
        "/h63": MODE_H63,
        "h63": MODE_H63,
        "個股": MODE_H63,
        "/h1": MODE_H1,
        "h1": MODE_H1,
        "/open": MODE_H1,
        "/tomorrow": MODE_H1,
        "明日": MODE_H1,
        "開盤": MODE_H1,
        "/all": MODE_BOTH,
        "/both": MODE_BOTH,
        "both": MODE_BOTH,
        "全部": MODE_BOTH,
    }
    if command in aliases:
        if len(parts) < 2:
            return ParsedCommand("invalid")
        query = parts[1].strip()
        return ParsedCommand("job", aliases[command], query) if valid_stock_query(query) else ParsedCommand("invalid")
    if value.startswith("/"):
        return ParsedCommand("invalid")
    return ParsedCommand("job", MODE_H63, value) if valid_stock_query(value) else ParsedCommand("invalid")


def valid_stock_query(value: str) -> bool:
    query = str(value or "").strip()
    return bool(re.fullmatch(r"[0-9A-Za-z.\-_\u3400-\u9fff]{1,30}", query))


def parse_resolver_output(output: str) -> list[StockMatch]:
    matches: list[StockMatch] = []
    for raw_line in str(output or "").splitlines():
        parts = raw_line.strip().split("｜")
        if len(parts) != 3 or not parts[2].startswith("score="):
            continue
        left = re.match(r"^\d+\.\s+(\d{4,6})\s+(.+?)\s*$", parts[0])
        if not left:
            continue
        try:
            score = float(parts[2].split("=", 1)[1])
        except (IndexError, ValueError):
            score = 0.0
        matches.append(StockMatch(left.group(1), left.group(2).strip(), parts[1].strip(), score))
    return matches


def choose_stock_match(query: str, matches: list[StockMatch]) -> StockMatch | None:
    if not matches:
        return None
    query_text = query.strip().casefold()
    if query_text.isdigit():
        return next((item for item in matches if item.code == query_text), None)
    exact_names = [item for item in matches if item.name.casefold() == query_text]
    if len(exact_names) == 1:
        return exact_names[0]
    if len(matches) == 1 and matches[0].match_type != "fuzzy_closest":
        return matches[0]
    return None


def ambiguity_message(query: str, matches: list[StockMatch]) -> str:
    if not matches:
        return f"找不到「{query}」對應的台股，請改輸入 4 至 6 位股票代碼。"
    lines = [f"「{query}」符合多檔或只有模糊結果，請改輸入代碼："]
    lines.extend(f"{index}. {item.code} {item.name}" for index, item in enumerate(matches[:8], start=1))
    return "\n".join(lines)


def atomic_write_json(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temp_path = path.with_name(f"{path.name}.{os.getpid()}.{threading.get_ident()}.{uuid.uuid4().hex}.tmp")
    try:
        temp_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8", newline="\n")
        os.replace(temp_path, path)
    finally:
        try:
            temp_path.unlink(missing_ok=True)
        except OSError:
            pass  # Cleanup must not replace the original write/replace error.


class CommandStateStore:
    def __init__(self, path: Path, max_active_jobs: int) -> None:
        self.path = path
        self.max_active_jobs = max_active_jobs
        self.lock = threading.RLock()
        self.payload = self._load()

    def _load(self) -> dict[str, Any]:
        if not self.path.exists():
            return {"version": 1, "nextUpdateId": None, "readyAnnounced": False, "jobs": []}
        try:
            payload = json.loads(self.path.read_text(encoding="utf-8-sig"))
        except (UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise ConfigurationError(f"指令狀態檔損壞，為避免重複分析已停止：{self.path}") from exc
        if not isinstance(payload, dict):
            raise ConfigurationError(f"指令狀態檔格式錯誤：{self.path}")
        payload.setdefault("version", 1)
        payload.setdefault("nextUpdateId", None)
        payload.setdefault("readyAnnounced", False)
        if not isinstance(payload.get("jobs"), list):
            raise ConfigurationError(f"指令狀態檔 jobs 欄位格式錯誤，為避免重複分析已停止：{self.path}")
        return payload

    def _save(self) -> None:
        atomic_write_json(self.path, self.payload)

    @staticmethod
    def _copy(value: Any) -> Any:
        return json.loads(json.dumps(value, ensure_ascii=False))

    def next_update_id(self) -> int | None:
        with self.lock:
            value = self.payload.get("nextUpdateId")
            return int(value) if isinstance(value, int | float) else None

    def set_next_update_id(self, value: int) -> None:
        with self.lock:
            self.payload["nextUpdateId"] = int(value)
            self._save()

    def ready_announced(self) -> bool:
        with self.lock:
            return bool(self.payload.get("readyAnnounced"))

    def mark_ready_announced(self) -> None:
        with self.lock:
            self.payload["readyAnnounced"] = True
            self._save()

    def add_job(
        self, update_id: int, query: str, mode: str, command: str = ""
    ) -> tuple[dict[str, Any], bool, int]:
        normalized = query.strip().casefold()
        with self.lock:
            jobs = self.payload["jobs"]
            for job in jobs:
                if int(job.get("updateId", -1)) == update_id:
                    return self._copy(job), False, self._queue_position(job)
                if (
                    job.get("status") in ACTIVE_STATUSES
                    and str(job.get("normalizedQuery")) == normalized
                    and str(job.get("mode")) == mode
                ):
                    return self._copy(job), False, self._queue_position(job)
            active_count = sum(1 for job in jobs if job.get("status") in ACTIVE_STATUSES)
            if active_count >= self.max_active_jobs:
                raise QueueFullError(f"目前已有 {active_count} 個工作，請稍後再試")
            job = {
                "jobId": f"tg-{update_id}",
                "updateId": int(update_id),
                "query": query.strip(),
                "normalizedQuery": normalized,
                "mode": mode,
                "command": str(command or ""),
                "status": "queued",
                "requestedAt": now_iso(),
                "reports": [],
            }
            jobs.append(job)
            self._trim_jobs()
            self._save()
            return self._copy(job), True, self._queue_position(job)

    def _queue_position(self, target: dict[str, Any]) -> int:
        queued = [job for job in self.payload["jobs"] if job.get("status") == "queued"]
        for index, job in enumerate(queued, start=1):
            if job.get("jobId") == target.get("jobId"):
                return index
        return 0

    def _trim_jobs(self) -> None:
        jobs = self.payload["jobs"]
        if len(jobs) <= 200:
            return
        active = [job for job in jobs if job.get("status") in ACTIVE_STATUSES]
        terminal = [job for job in jobs if job.get("status") not in ACTIVE_STATUSES]
        self.payload["jobs"] = terminal[-160:] + active

    def queued_job_ids(self) -> list[str]:
        with self.lock:
            return [str(job.get("jobId")) for job in self.payload["jobs"] if job.get("status") == "queued"]

    def recover_interrupted_jobs(self) -> list[dict[str, Any]]:
        interrupted: list[dict[str, Any]] = []
        with self.lock:
            for job in self.payload["jobs"]:
                if job.get("status") == "running":
                    job["status"] = "failed"
                    job["completedAt"] = now_iso()
                    job["error"] = "指令服務在分析期間中斷，請重新送出指令"
                    interrupted.append(self._copy(job))
            if interrupted:
                self._save()
        return interrupted

    def get_job(self, job_id: str) -> dict[str, Any] | None:
        with self.lock:
            for job in self.payload["jobs"]:
                if job.get("jobId") == job_id:
                    return self._copy(job)
        return None

    def update_job(self, job_id: str, **fields: Any) -> dict[str, Any]:
        with self.lock:
            for job in self.payload["jobs"]:
                if job.get("jobId") == job_id:
                    job.update(fields)
                    job["updatedAt"] = now_iso()
                    self._save()
                    return self._copy(job)
        raise KeyError(job_id)

    def append_report(self, job_id: str, report: GeneratedReport) -> None:
        with self.lock:
            for job in self.payload["jobs"]:
                if job.get("jobId") == job_id:
                    job.setdefault("reports", []).append(
                        {
                            "mode": report.mode,
                            "pdfPath": str(report.pdf_path),
                            "jsonPath": str(report.json_path) if report.json_path else "",
                            "pdfSha256": report.pdf_sha256,
                            "jsonSha256": report.json_sha256,
                            "pdfSize": report.pdf_path.stat().st_size if report.pdf_path.is_file() else 0,
                            "sentAt": now_iso(),
                        }
                    )
                    job["updatedAt"] = now_iso()
                    self._save()
                    return
        raise KeyError(job_id)

    def status_text(self) -> str:
        with self.lock:
            active = [job for job in self.payload["jobs"] if job.get("status") in ACTIVE_STATUSES]
            if not active:
                return "目前沒有執行中或排隊中的個股分析。"
            lines = [f"目前工作數：{len(active)}"]
            for job in active[:10]:
                status = "分析中" if job.get("status") == "running" else "排隊"
                lines.append(
                    f"{status}｜{job.get('query')}｜{MODE_LABELS.get(str(job.get('mode')), str(job.get('mode')))}"
                )
            return "\n".join(lines)


class SingleInstanceLock:
    def __init__(self, path: Path, owner_path: Path | None = None) -> None:
        self.path = path
        self.owner_path = owner_path or path.with_suffix(".owner.json")
        self.handle: Any = None

    def __enter__(self) -> "SingleInstanceLock":
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.path.touch(exist_ok=True)
        self.handle = self.path.open("r+b")
        self.handle.seek(0, os.SEEK_END)
        if self.handle.tell() < 1:
            self.handle.write(b" ")
            self.handle.flush()
        self.handle.seek(0)
        try:
            msvcrt.locking(self.handle.fileno(), msvcrt.LK_NBLCK, 1)
        except OSError as exc:
            self.handle.close()
            self.handle = None
            raise ConfigurationError("Telegram 個股指令服務已在執行") from exc
        atomic_write_json(
            self.owner_path,
            {
                "processId": os.getpid(),
                "startedAt": now_iso(),
                "sourceSha256": SOURCE_SHA256,
                "runtimeRevision": RUNTIME_REVISION,
            },
        )
        return self

    def __exit__(self, exc_type: Any, exc: Any, traceback: Any) -> None:
        if self.handle is None:
            return
        try:
            self.owner_path.unlink(missing_ok=True)
            self.handle.seek(0)
            msvcrt.locking(self.handle.fileno(), msvcrt.LK_UNLCK, 1)
        finally:
            self.handle.close()
            self.handle = None


C0_BINDING_UNAVAILABLE_SCHEMA = "stock-daily-c0-runtime-binding-unavailable-v1"
# Fields whose change means "the producer published newer data"; any other
# changed field is a model/policy/contract change.  Neither stops a job.
C0_BINDING_DATA_FIELDS = frozenset({"asof", "snapshot_fingerprint", "published_sources"})


def _is_c0_integrity_error(exc: BaseException) -> bool:
    """True for any C0IntegrityError (shared adoption or single-stock source class)."""
    return any(cls.__name__ == "C0IntegrityError" for cls in type(exc).__mro__)


def _published_head_binding(binding_error: BaseException) -> dict[str, Any]:
    """Describe the current published head when the full C0 binding is unavailable.

    Only when even the head cannot be read is the data "completely unreadable";
    that is the one case that still raises.
    """
    try:
        from stock_daily_contract import _current_c0_sources

        descriptor = _current_c0_sources().descriptor()
        fingerprint = descriptor["snapshot_fingerprint"]
        sources = descriptor.get("sources") or []
    except Exception as exc:
        error_type = C0BindingIntegrityError if _is_c0_integrity_error(exc) else AnalysisProcessError
        raise error_type(
            "完全讀不到已發布資料：" + str(binding_error)[:300] + "；head：" + str(exc)[:300]
        ) from exc
    return {
        "schema": C0_BINDING_UNAVAILABLE_SCHEMA,
        "asof": descriptor.get("price_actual_max_date"),
        "snapshot_fingerprint": fingerprint,
        "published_sources": sources,
        "binding_error": f"{type(binding_error).__name__}: {binding_error}"[:500],
    }


def c0_binding_data_time(binding: dict[str, Any] | None) -> dict[str, Any]:
    """The data-time part of a runtime binding (asof, fingerprint, producer published_at)."""
    if not isinstance(binding, dict):
        return {"asof": None, "snapshot_fingerprint": None, "published_at": None}
    sources = binding.get("published_sources") or []
    first = sources[0] if isinstance(sources, list) and sources and isinstance(sources[0], dict) else {}
    return {
        "asof": binding.get("asof"),
        "snapshot_fingerprint": binding.get("snapshot_fingerprint"),
        "published_at": first.get("published_at"),
    }


def _binding_model_sha(binding: dict[str, Any]) -> Any:
    model = binding.get("model_binding")
    if isinstance(model, dict):
        return model.get("sha256") or model.get("model_sha256")
    deployment = binding.get("deployment_binding")
    return deployment.get("model_sha256") if isinstance(deployment, dict) else None


def _is_selected_binding(binding: Any) -> bool:
    return isinstance(binding, dict) and isinstance(binding.get("deployment_binding"), dict)


def _check_selected_deployment(binding: dict[str, Any]) -> list[dict[str, Any]]:
    """Recheck only deployment identity; keep the job's expensive context and environment."""
    try:
        from stock_daily_selected_deployment import active_selected_binding

        current = active_selected_binding()
        if current is not None and not isinstance(current, dict):
            raise ValueError("selected_active_binding_invalid")
    except Exception as exc:
        raise SelectedBindingIntegrityError("正式單股 deployment 漂移或完整性檢查失敗：" + str(exc)[:500]) from exc
    recorded = binding["deployment_binding"]
    if current == recorded:
        return []
    return [{
        "code": "runtime_contract_changed_during_job",
        "recorded": {"activation_id": recorded.get("activation_id"), "model_sha256": recorded.get("model_sha256")},
        "actual": {"activation_id": current.get("activation_id"), "model_sha256": current.get("model_sha256")}
        if isinstance(current, dict) else None,
        "detail": {"authority": SELECTED_DEPLOYMENT_SCHEMA},
    }]


def c0_binding_version_labels(
    baseline: dict[str, Any] | None, current: dict[str, Any] | None,
) -> list[dict[str, Any]]:
    """Compare two runtime bindings and return version labels (never raises)."""
    if not isinstance(baseline, dict) or not isinstance(current, dict) or not current:
        return []
    fields = sorted(name for name in set(baseline) | set(current) if baseline.get(name) != current.get(name))
    if not fields:
        return []
    labels: list[dict[str, Any]] = []
    data_fields = [name for name in fields if name in C0_BINDING_DATA_FIELDS]
    other_fields = [name for name in fields if name not in C0_BINDING_DATA_FIELDS]
    if data_fields:
        labels.append({
            "code": "newer_data_during_job",
            "recorded": c0_binding_data_time(baseline),
            "actual": c0_binding_data_time(current),
            "detail": {"fields": data_fields},
        })
    if other_fields:
        labels.append({
            "code": "runtime_contract_changed_during_job",
            "recorded": _binding_model_sha(baseline),
            "actual": _binding_model_sha(current),
            "detail": {"fields": other_fields},
        })
    return labels


def c0_analysis_environment() -> tuple[dict[str, str], dict[str, Any]]:
    """Child environment plus the current C0 runtime binding.

    A version/binding failure is logged as a warning and replaced by a head-only
    binding, so the job still runs on the data actually read; the child keeps
    its own integrity/content checks.  A completely unreadable head raises, and so
    does C0IntegrityError (tamper, torn read, invalid content: plan 0.2 KEEP).
    """
    try:
        from stock_daily_contract import c0_runtime_binding

        binding = c0_runtime_binding(consumer_id="single")
    except Exception as exc:
        if _is_c0_integrity_error(exc):
            raise C0BindingIntegrityError("C0 已發布資料或模型完整性驗證失敗：" + str(exc)[:500]) from exc
        binding = _published_head_binding(exc)
        LOGGER.warning("C0 runtime binding 無法完整建立，改以已發布 head 繼續：%s", binding["binding_error"])
    return _child_analysis_environment(), binding


def analysis_environment() -> tuple[dict[str, str], dict[str, Any]]:
    """Select the independent formal authority before touching any legacy C0 gate."""
    try:
        from stock_daily_selected_deployment import selected_analysis_environment

        selected = selected_analysis_environment()
        if selected is not None and (not isinstance(selected, tuple) or len(selected) != 2):
            raise ValueError("selected_analysis_environment_result_invalid")
        environment, binding = selected if selected is not None else (None, None)
        if selected is not None and (
            not isinstance(environment, dict)
            or not all(isinstance(key, str) and isinstance(value, str) for key, value in environment.items())
            or not _is_selected_binding(binding)
        ):
            raise ValueError("selected_analysis_environment_binding_invalid")
        if selected is not None and (
            binding.get("schema") != SELECTED_RUNTIME_BINDING_SCHEMA
            or binding["deployment_binding"].get("schema") != SELECTED_DEPLOYMENT_SCHEMA
            or binding["deployment_binding"].get("consumer_id") != "single"
        ):
            raise ValueError("selected_analysis_environment_deployment_invalid")
    except C0BindingIntegrityError:
        raise
    except Exception as exc:
        raise SelectedBindingIntegrityError("正式單股 authority 驗證失敗：" + str(exc)[:500]) from exc
    if selected is None:
        return c0_analysis_environment()
    return _child_analysis_environment(environment), binding


def _child_analysis_environment(base_environment: dict[str, str] | None = None) -> dict[str, str]:
    environment = dict(os.environ if base_environment is None else base_environment)
    for key in list(environment):
        lowered = key.casefold()
        if (
            lowered.startswith(("telegram_", "dstock_central_", "dstock_report_comparison_cache_"))
            or lowered in {"dstock_single_stock_profile", "dstock_single_stock_formal_transaction"}
            or SENSITIVE_ENV_NAME_PATTERN.search(key)
            or any(fragment in lowered for fragment in BLOCKED_PROVIDER_ENV_FRAGMENTS)
        ):
            environment.pop(key, None)
    environment.update(
        PYTHONUTF8="1",
        PYTHONIOENCODING="utf-8",
        PYTHONDONTWRITEBYTECODE="1",
        PYTHONPATH=os.pathsep.join((str(ANALYSIS_DIR), str(SHARED_DIR))),
        MPLBACKEND="Agg",
        NO_PAUSE="1",
        ENABLE_FINMIND_FETCH="0",
    )
    environment[CURRENT_NETWORK_DISABLED_ENV] = "1"
    return environment

def run_non_queue_self_check() -> dict[str, Any]:
    """Validate the resident contract without opening Telegram state or queue.

    Version differences (the resident runs an older source, data or model
    updated during the check) are reported as warnings, not failures.
    """
    warnings: list[dict[str, Any]] = []
    try:
        heartbeat = json.loads(HEARTBEAT_PATH.read_text(encoding="utf-8"))
        owner = json.loads(OWNER_PATH.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise ConfigurationError("Telegram resident heartbeat/owner 不可讀取") from exc
    if not isinstance(heartbeat, dict) or not isinstance(owner, dict):
        raise ConfigurationError("Telegram resident heartbeat/owner schema invalid")
    if str(heartbeat.get("pid")) != str(owner.get("processId")):
        raise ConfigurationError("Telegram resident owner/heartbeat PID 不一致")
    current_source_sha256 = _source_sha256(SOURCE_PATH).lower()
    if not re.fullmatch(r"[0-9a-fA-F]{64}", SOURCE_SHA256):
        raise ConfigurationError("Telegram runtime source SHA 無效")
    heartbeat_source_sha256 = str(heartbeat.get("sourceSha256") or "").strip().lower()
    owner_source_sha256 = str(owner.get("sourceSha256") or "").strip().lower()
    if not re.fullmatch(r"[0-9a-f]{64}", heartbeat_source_sha256) or not re.fullmatch(r"[0-9a-f]{64}", owner_source_sha256):
        raise ConfigurationError("Telegram resident heartbeat/owner source SHA 格式無效")
    if SOURCE_SHA256.lower() != current_source_sha256:
        warnings.append({"code": "self_check_source_changed", "recorded": SOURCE_SHA256.lower(),
                         "actual": current_source_sha256, "detail": "source 在 self-check 載入後被更新"})
    if heartbeat_source_sha256 != current_source_sha256 or owner_source_sha256 != current_source_sha256:
        warnings.append({"code": "resident_listener_source_stale",
                         "recorded": {"heartbeat": heartbeat_source_sha256, "owner": owner_source_sha256},
                         "actual": current_source_sha256, "detail": "常駐程式為舊版，需重啟"})
        LOGGER.warning("常駐程式為舊版，需重啟：resident=%s disk=%s",
                       heartbeat_source_sha256[:16], current_source_sha256[:16])
    environment, binding = analysis_environment()
    if binding.get("schema") == C0_BINDING_UNAVAILABLE_SCHEMA:
        warnings.append({"code": "c0_runtime_binding_unavailable", "recorded": None,
                         "actual": c0_binding_data_time(binding), "detail": binding.get("binding_error")})
    try:
        result = subprocess.run(
            build_resolver_command(
                "2303", resolve_analysis_python(environment),
                c0_binding_head_sha256(binding) if _is_selected_binding(binding) else None,
            ),
            cwd=STOCK_ROOT,
            env=environment,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=180,
            creationflags=subprocess_creation_flags(),
            shell=False,
            check=False,
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        raise ConfigurationError("C0 non-queue resolver self-check 無法完成") from exc
    selected = choose_stock_match("2303", parse_resolver_output(result.stdout))
    if result.returncode != 0 or selected is None or selected.code != "2303":
        raise ConfigurationError("C0 non-queue resolver self-check 失敗")
    try:
        if _is_selected_binding(binding):
            warnings.extend(_check_selected_deployment(binding))
            after = binding
        else:
            _, after = analysis_environment()
    except C0BindingIntegrityError:
        raise  # integrity is KEEP: tamper/torn read fails the self-check
    except AnalysisProcessError as exc:
        after = None
        warnings.append({"code": "current_binding_unreadable_after_self_check",
                         "recorded": c0_binding_data_time(binding), "actual": None, "detail": str(exc)[:300]})
    changed = c0_binding_version_labels(binding, after)
    if changed:
        # 期間已更新：回報，不算失敗。
        LOGGER.warning("C0 資料或版本在 self-check 期間已更新（不視為失敗）：%s",
                       json.dumps(changed, ensure_ascii=False, sort_keys=True, default=str)[:2000])
        warnings.extend(changed)
    data_time = c0_binding_data_time(after if isinstance(after, dict) else binding)
    summary = {
        "status": "ok_with_warnings" if warnings else "ok",
        "residentPid": heartbeat.get("pid"),
        "dataAsof": data_time["asof"],
        "publishedAt": data_time["published_at"],
        "snapshotFingerprint": data_time["snapshot_fingerprint"],
        "modelSha256": _binding_model_sha(binding),
        "checkedAt": now_iso(),
        "warnings": warnings,
    }
    LOGGER.info(
        "C0 non-queue self-check 通過：residentPid=%s 資料=%s published=%s model=%s warnings=%s",
        heartbeat.get("pid"), cd.display_day(data_time["asof"], empty="未知"), data_time["snapshot_fingerprint"],
        summary["modelSha256"], [item["code"] for item in warnings],
    )
    return summary

def subprocess_creation_flags() -> int:
    return int(getattr(subprocess, "CREATE_NO_WINDOW", 0))


def resolve_path_under(root: Path, candidate: Path, label: str) -> Path:
    resolved_root = root.expanduser().resolve(strict=False)
    resolved_candidate = candidate.expanduser().resolve(strict=False)
    try:
        resolved_candidate.relative_to(resolved_root)
    except ValueError as exc:
        raise AnalysisProcessError(f"{label}超出允許的契約資料根目錄") from exc
    return resolved_candidate


def build_analysis_output_dir(job_id: str, mode: str, stamp: str) -> Path:
    if mode not in MODE_HORIZONS:
        raise AnalysisProcessError("個股分析期限只接受 H1 或 H63")
    return resolve_path_under(
        DSTOCK_SINGLE_ROOT, JOBS_DIR / job_id / f"{mode}_{stamp}", "個股分析輸出路徑",
    )


def _path_identity(info: os.stat_result) -> tuple[int, int]:
    return info.st_dev, info.st_ino


def _require_owned_directory(path: Path, identity: tuple[int, int]) -> None:
    info = path.lstat()
    if (path.resolve() != path or _path_identity(info) != identity
            or not stat.S_ISDIR(info.st_mode) or path.is_symlink()
            or getattr(info, "st_file_attributes", 0) & getattr(stat, "FILE_ATTRIBUTE_REPARSE_POINT", 0x400)):
        raise AnalysisProcessError("本次輸出目錄身分或位置已變，拒絕寫入或清理")


def _unlink_owned_file(path: Path, identity: tuple[int, int]) -> None:
    info = path.lstat()
    if (_path_identity(info) != identity or not stat.S_ISREG(info.st_mode)
            or getattr(info, "st_file_attributes", 0) & getattr(stat, "FILE_ATTRIBUTE_REPARSE_POINT", 0x400)):
        raise AnalysisProcessError("本次暫存檔身分已變，拒絕清理")
    path.unlink()


@contextmanager
def _owned_analysis_log(output_dir: Path) -> Iterator[tuple[Path, TextIO]]:
    output_identity = _path_identity(output_dir.lstat())
    _require_owned_directory(output_dir, output_identity)
    directory = Path(tempfile.mkdtemp(prefix=".analysis-", dir=output_dir))
    directory_identity = _path_identity(directory.lstat())
    log_path = directory / "analysis.log"
    log_identity = None
    try:
        _require_owned_directory(directory, directory_identity)
        if directory.parent != output_dir:
            raise AnalysisProcessError("分析暫存超出本次輸出目錄")
        with log_path.open("x", encoding="utf-8", newline="\n") as stream:
            log_identity = _path_identity(os.fstat(stream.fileno()))
            yield log_path, stream
    finally:
        primary_error = sys.exc_info()[1]
        try:
            _require_owned_directory(output_dir, output_identity)
            _require_owned_directory(directory, directory_identity)
            if log_identity is not None:
                _unlink_owned_file(log_path, log_identity)
            directory.rmdir()  # Unknown files are never removed recursively.
        except BaseException as exc:
            if primary_error is None:
                raise
            add_note = getattr(primary_error, "add_note", None)
            if callable(add_note):
                add_note(f"analysis temporary cleanup: {type(exc).__name__}")


def sensitive_values(values: dict[str, str]) -> tuple[str, ...]:
    return tuple(
        sorted(
            {
                str(value).strip()
                for key, value in values.items()
                if SENSITIVE_ENV_NAME_PATTERN.search(str(key)) and len(str(value).strip()) >= 4
            },
            key=len,
            reverse=True,
        )
    )


def redact_sensitive_text(value: str, secrets: tuple[str, ...] = ()) -> str:
    text = str(value or "")
    for secret in secrets:
        text = text.replace(secret, "[REDACTED]")
    text = SENSITIVE_ASSIGNMENT_PATTERN.sub(r"\1[REDACTED]", text)
    text = re.sub(r"(?i)\bBearer\s+\S+", "Bearer [REDACTED]", text)
    return TELEGRAM_TOKEN_IN_TEXT_PATTERN.sub("[REDACTED_TELEGRAM_TOKEN]", text)


def read_safe_log_tail(path: Path, secrets: tuple[str, ...] = ()) -> str:
    try:
        with path.open("rb") as handle:
            handle.seek(0, os.SEEK_END)
            total_bytes = handle.tell()
            handle.seek(max(0, total_bytes - ANALYSIS_ERROR_TAIL_MAX_BYTES), os.SEEK_SET)
            raw = handle.read(ANALYSIS_ERROR_TAIL_MAX_BYTES)
    except (OSError, ValueError):
        return ""
    if not raw:
        return ""
    truncated = total_bytes > ANALYSIS_ERROR_TAIL_MAX_BYTES
    decoded = raw.decode("utf-8", errors="replace")
    if truncated:
        decoded = decoded.partition("\n")[2]
    phase_prefix = SINGLE_STOCK_PHASE_PREFIX.decode("ascii")
    safe_lines = [line for line in decoded.splitlines() if not line.startswith(phase_prefix)]
    text = redact_sensitive_text("\n".join(safe_lines), secrets).strip()
    if len(text) > ANALYSIS_ERROR_TAIL_MAX_CHARS:
        text = text[-ANALYSIS_ERROR_TAIL_MAX_CHARS:]
        truncated = True
    if truncated:
        text = "[分析紀錄末段已截斷]\n" + text
    return text.strip()


def resolve_analysis_python(environment: dict[str, str]) -> str:
    if not ANALYSIS_PYTHON_RESOLVER.is_file():
        raise ConfigurationError("固定 analysis Python resolver 不存在")
    system_root_values = {
        str(value).strip()
        for key, value in environment.items()
        if key.casefold() == "systemroot"
    }
    if len(system_root_values) != 1 or not next(iter(system_root_values), ""):
        raise ConfigurationError("固定 analysis Python resolver 的 SystemRoot 缺少或衝突")
    system_root = next(iter(system_root_values))
    command_processor = Path(system_root) / "System32" / "cmd.exe"
    if not command_processor.is_file():
        raise ConfigurationError("固定 analysis Python command processor 不存在")
    try:
        resolver_argument = str(ANALYSIS_PYTHON_RESOLVER.relative_to(STOCK_ROOT))
    except ValueError as exc:
        raise ConfigurationError("固定 analysis Python resolver 不在程式根目錄內") from exc
    if any(char.isspace() or char in '&|<>^()%!"' for char in resolver_argument):
        raise ConfigurationError("固定 analysis Python resolver 路徑含 cmd 特殊字元")
    command = (
        f"call {resolver_argument} analysis "
        f"&& echo {ANALYSIS_PYTHON_MARKER}!PY!"
    )
    try:
        result = subprocess.run(
            [str(command_processor), "/d", "/s", "/v:on", "/c", command],
            cwd=STOCK_ROOT,
            env=environment,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=60,
            creationflags=subprocess_creation_flags(),
            shell=False,
            check=False,
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        raise ConfigurationError("固定 analysis Python resolver 執行失敗") from exc
    if result.returncode != 0:
        raise ConfigurationError("find_python.cmd analysis 未選出可用 Python")
    candidates = [
        line[len(ANALYSIS_PYTHON_MARKER):].strip()
        for line in str(result.stdout or "").splitlines()
        if line.startswith(ANALYSIS_PYTHON_MARKER)
    ]
    if len(candidates) != 1:
        raise ConfigurationError("find_python.cmd analysis 輸出缺少唯一 Python 路徑")
    executable = candidates[0]
    if not Path(executable).is_absolute() or Path(executable).name.casefold() != "python.exe":
        raise ConfigurationError("find_python.cmd analysis 輸出的 Python 路徑無效")
    return executable


def build_resolver_command(
    query: str, python_executable: str, pinned_head_sha256: str | None = None,
) -> list[str]:
    command = [
        python_executable, str(ANALYSIS_ENTRY_SCRIPT), MODE_H63,
        "--resolve-stock", query, "--max-matches", "8", "--no-progress",
    ]
    if pinned_head_sha256 is not None:
        if not SHA256_HEX_PATTERN.fullmatch(str(pinned_head_sha256)):
            raise AnalysisProcessError("股票解析工作 pin 的 head sha256 格式無效")
        command += ["--pinned-head-sha256", pinned_head_sha256]
    return command


def c0_binding_head_sha256(binding: dict[str, Any] | None) -> str | None:
    """The immutable head (_manifests/<sha>.json) a runtime binding was built from, or None."""
    if not isinstance(binding, dict):
        return None
    if _is_selected_binding(binding):
        snapshot = binding.get("snapshot_binding")
        snapshot_head = snapshot.get("head_sha256") if isinstance(snapshot, dict) else None
        direct_head = binding.get("head_sha256")
        if snapshot_head is not None and direct_head is not None and snapshot_head != direct_head:
            raise SelectedBindingIntegrityError("正式單股 runtime binding head 身分衝突")
        value = snapshot_head if snapshot_head is not None else direct_head
        if value is not None:
            if not isinstance(value, str) or not SHA256_HEX_PATTERN.fullmatch(value):
                raise SelectedBindingIntegrityError("正式單股 runtime binding head 格式無效")
            sources = binding.get("published_sources")
            if sources is not None:
                if (
                    not isinstance(sources, list) or len(sources) != 1 or not isinstance(sources[0], dict)
                    or sources[0].get("manifest_sha256") != value
                ):
                    raise SelectedBindingIntegrityError("正式單股 runtime binding head 身分衝突")
            return value
    sources = binding.get("published_sources")
    if not isinstance(sources, list) or len(sources) != 1 or not isinstance(sources[0], dict):
        return None
    value = sources[0].get("manifest_sha256")
    return value if isinstance(value, str) and SHA256_HEX_PATTERN.fullmatch(value) else None


def build_analysis_command(
    mode: str, code: str, output_dir: Path, python_executable: str,
    pinned_head_sha256: str | None = None,
) -> list[str]:
    if mode not in MODE_HORIZONS:
        raise AnalysisProcessError("個股分析期限只接受 H1 或 H63")
    command = [
        python_executable, "-u", str(ANALYSIS_ENTRY_SCRIPT), mode,
        "--stock", code, "--output-dir", str(output_dir), "--max-matches", "1",
    ]
    if pinned_head_sha256 is not None:
        if not SHA256_HEX_PATTERN.fullmatch(str(pinned_head_sha256)):
            raise AnalysisProcessError("C0 工作 pin 的 head sha256 格式無效")
        # The child analyses the job-start head while it exists (else the current head, labelled).
        command += ["--pinned-head-sha256", str(pinned_head_sha256)]
    return command

REPORT_CAPTION_DISCLAIMER = "模型結果僅作資訊整理，不保證報酬。"
TELEGRAM_CAPTION_BUDGET = 1000


def safe_number(value: Any) -> float | None:
    if isinstance(value, bool):
        return None
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    return number if math.isfinite(number) else None


def _taipei_datetime(value: Any) -> datetime | None:
    if isinstance(value, datetime):
        stamp = value
    elif isinstance(value, str) and value.strip():
        text = value.strip()
        if text.endswith("Z"):
            text = text[:-1] + "+00:00"
        try:
            stamp = datetime.fromisoformat(text)
        except ValueError:
            return None
    else:
        return None
    if stamp.tzinfo is None:
        stamp = stamp.astimezone()  # naive timestamps are this machine's local time
    return stamp.astimezone(cd._TAIPEI)


def _caption_clock(value: Any, reference: datetime | None) -> str:
    """hh:mm (Taipei); prefix YYYYMMDD when it is not the reference day."""
    stamp = _taipei_datetime(value)
    if stamp is None:
        return "未知"
    if reference is not None and stamp.date() == reference.date():
        return f"{stamp:%H:%M}"
    return f"{cd.format_day(stamp.date())} {stamp:%H:%M}"


def report_data_time(payload: dict[str, Any], read_at: Any = None) -> dict[str, Any]:
    """Data time of the report actually generated: data day, producer publish time, read time.

    Prefers the report's own ``data_time`` block; falls back to the pinned
    snapshot descriptor recorded in the report and the job's read time.
    """
    data_time = payload.get("data_time") if isinstance(payload.get("data_time"), dict) else {}
    snapshot = payload.get("published_snapshot") if isinstance(payload.get("published_snapshot"), dict) else {}
    if payload.get("schema") == SELECTED_SINGLE_REPORT_SCHEMA:
        selected_snapshot = payload.get("snapshot_binding")
        if isinstance(selected_snapshot, dict):
            descriptor = selected_snapshot.get("descriptor")
            snapshot = dict(descriptor) if isinstance(descriptor, dict) else {}
            snapshot["snapshot_fingerprint"] = selected_snapshot.get("snapshot_fingerprint")
    sources = snapshot.get("sources") if isinstance(snapshot.get("sources"), list) else []
    first = sources[0] if sources and isinstance(sources[0], dict) else {}
    head = data_time.get("head") if isinstance(data_time.get("head"), dict) else {}
    return {
        "asof": data_time.get("analysis_asof") or payload.get("analysis_asof") or payload.get("asof") or payload.get("latest_data_date"),
        "published_at": head.get("published_at") or snapshot.get("published_at") or first.get("published_at"),
        "read_at": data_time.get("read_at_utc") or payload.get("generated_at") or read_at,
        "snapshot_fingerprint": snapshot.get("snapshot_fingerprint"),
        "not_latest": bool(data_time.get("is_stale") or data_time.get("is_older_than_requested")),
    }


def report_data_time_lines(payload: dict[str, Any], job_context: dict[str, Any] | None = None) -> list[str]:
    """「資料：YYYYMMDD（發布 hh:mm，讀取 hh:mm）」 plus 「執行期間已有較新資料 Y」 when applicable."""
    context = job_context if isinstance(job_context, dict) else {}
    current = report_data_time(payload, context.get("read_at"))
    read_stamp = _taipei_datetime(current["read_at"])
    now = datetime.now(cd._TAIPEI)
    data_line = (
        f"資料：{cd.display_day(current['asof'], empty='未知')}"
        f"（發布 {_caption_clock(current['published_at'], read_stamp)}，讀取 {_caption_clock(current['read_at'], now)}）"
    )
    if current["not_latest"]:
        data_line += "［非最新資料］"
    lines = [data_line]
    if any(isinstance(label, dict) and label.get("code") == "runtime_contract_changed_during_job"
           for label in context.get("labels") or []):
        lines.append("執行期間模型版本已更新；本報告仍使用工作開始時的封存模型。")
    report_labels = payload.get("version_labels") if isinstance(payload.get("version_labels"), list) else []
    if any(isinstance(label, dict) and label.get("code") == C0_PINNED_HEAD_UNAVAILABLE
           for label in [*report_labels, *(context.get("labels") or [])]):
        lines.append("工作開始時的資料版本已不存在，本報告改用分析當下的資料。")
    newer = context.get("current_binding")
    newer_time = c0_binding_data_time(newer) if isinstance(newer, dict) and newer else None
    if not (newer_time and newer_time["snapshot_fingerprint"]):
        # Fall back to a newer-head label recorded by validation.
        for label in context.get("labels") or []:
            if isinstance(label, dict) and label.get("code") in {"newer_data_available", "newer_data_during_job"}:
                actual = label.get("actual") if isinstance(label.get("actual"), dict) else {}
                newer_time = {"asof": actual.get("asof"), "snapshot_fingerprint": actual.get("snapshot_fingerprint"),
                              "published_at": actual.get("published_at")}
    if (
        newer_time
        and newer_time.get("snapshot_fingerprint")
        and current["snapshot_fingerprint"]
        and newer_time["snapshot_fingerprint"] != current["snapshot_fingerprint"]
    ):
        newer_day = newer_time.get("asof")
        if newer_day and not cd.same_day(newer_day, current["asof"]):
            text = " " + cd.display_day(newer_day)
        elif newer_day:
            text = f" {cd.display_day(newer_day)} 重新發布"
        else:
            text = ""
        if newer_time.get("published_at"):
            text += f"（發布 {_caption_clock(newer_time['published_at'], now)}）"
        lines.append(f"執行期間已有較新資料{text}；本報告仍使用上列資料。")
    return lines


def _preopen_h1_block(payload: dict[str, Any], horizon: int) -> dict[str, Any] | None:
    """The report's sealed pre-open H1 block (None for H63 or reports without one); malformed blocks never deliver."""
    if horizon != 1 or "preopen_h1" not in payload:
        return None
    block = payload.get("preopen_h1")
    if (
        not isinstance(block, dict) or block.get("schema") != PREOPEN_H1_SCHEMA
        or block.get("status") not in {"used", "fallback", "disabled"}
    ):
        raise AnalysisProcessError("H1 開盤前版欄位格式無效，拒絕傳送")
    if block["status"] == "used":
        probability = safe_number(block.get("p_up"))
        if probability is None or not 0 <= probability <= 1 or safe_number(block.get("exp_ret")) is None:
            raise AnalysisProcessError("H1 開盤前版數值無效，拒絕傳送")
    elif not isinstance(block.get("notice"), str) or not block["notice"].strip():
        raise AnalysisProcessError("H1 開盤前版回退說明缺漏，拒絕傳送")
    return block


def _caption_percent(value: Any, signed: bool = False, digits: int = 1) -> str:
    number = safe_number(value)
    return "未提供" if number is None else f"{100 * number:{'+' if signed else ''}.{digits}f}%"


def _preopen_caption_lines(block: dict[str, Any]) -> list[str]:
    """H1 primary lines from the pre-open row, followed by the required disclosure."""
    probability, expected = safe_number(block.get("p_up")), safe_number(block.get("exp_ret"))
    rank, universe = block.get("rank"), block.get("universe_rows")
    tradable_rank, tradable_rows = block.get("rank_tradable"), block.get("tradable_rows")
    if block.get("tradable") is True and isinstance(tradable_rank, int):
        tradable = f"可交易 {tradable_rank}/{tradable_rows}" + ("（Top10）" if tradable_rank <= 10 else "")
    else:
        tradable = "不在可交易母體"
    lines = [
        f"開盤前版：上漲機率 {100*probability:.2f}%；預估收盤報酬 {100*expected:+.2f}%",
        f"名次：全市場 {rank if isinstance(rank, int) else '未提供'}/{universe if isinstance(universe, int) else '未提供'}；{tradable}",
        f"{block.get('gate_us_up_zh') or '前一晚美股漲跌未知'}；{block.get('us_overnight_status_zh') or '美股狀態未知'}",
    ]
    chips_note = block.get("chips_note_zh")  # t 日籌碼未更新／不完整時才有：寫最新到幾號或成功/應有數量，不阻擋
    if isinstance(chips_note, str) and chips_note.strip():
        lines.append(chips_note.strip())
    if block.get("fixture") is True:
        lines.append("【測試用假快照，不可作為正式資訊】")
    lines.append(_preopen_disclosure_line(block))
    return lines


def _preopen_recipe(block: dict[str, Any]) -> tuple[str | None, str]:
    """(查表用配方名稱, 顯示用名稱)。

    先看區塊 meta 摘要的 recipe（快照 manifest 原樣 {"name": ..., "frozen_sha256": ...}），沒有時看
    disclosure.recipe（名稱字串或同樣的 dict）；兩處都沒有或為 null＝舊快照＝v1。meta 的 recipe 不是 dict、
    或 name 不是字串時查表名稱為 None（未知配方，只顯示其文字）。
    """
    meta, facts = block.get("meta"), block.get("disclosure")
    if isinstance(meta, dict) and meta.get("recipe") is not None:
        recipe, strict = meta["recipe"], isinstance(meta["recipe"], dict)
    elif isinstance(facts, dict) and facts.get("recipe") is not None:
        recipe, strict = facts["recipe"], True
    else:
        return PREOPEN_RECIPE_V1, PREOPEN_RECIPE_V1
    value = recipe.get("name") if isinstance(recipe, dict) else recipe
    return (value if strict and isinstance(value, str) else None), _preopen_recipe_label(value)


def _preopen_recipe_label(value: Any) -> str:
    """未知配方的顯示名稱：只留可列印 ASCII（其餘以 ? 代替），最長 64 字；空值為「未提供」（與單股報告同規則）。"""
    text = "" if value is None else str(value)
    text = "".join(ch if " " <= ch <= "~" else "?" for ch in text).strip()[:PREOPEN_RECIPE_NAME_DISPLAY_MAX]
    return text or "未提供"


def _preopen_open_entry_note(value: Any, entry: dict[str, Any]) -> str:
    """開盤買進 Top10 日均報酬後的括號：未提供或與該配方保留期紀錄相同時用配方的說法，否則依顯示數字的正負。"""
    number = safe_number(value)
    if number is None or round(100 * number, 2) == round(100 * entry["open_entry_value"], 2):
        return entry["open_entry_note"]
    shown = round(100 * number, 2)
    return "（為負）" if shown < 0 else "（約為零）" if shown == 0 else "（為正）"


def _preopen_disclosure_line(block: dict[str, Any]) -> str:
    """必要揭露（一行），依快照配方選擇；v1（含沒有 recipe 欄位的舊快照）文字與改版前相同。"""
    name, label = _preopen_recipe(block)
    entry = PREOPEN_DISCLOSURE_BY_RECIPE.get(name) if name is not None else None
    if entry is None:
        return ("揭露：預測下一交易日收盤相對訊號日收盤的方向；"
                f"開盤前版配方「{label}」未知，不顯示保留期驗證數字；命中率不代表交易獲利。")
    if entry["facts"] is None:  # v1：數字取自封存區塊，與改版前相同
        facts = block.get("disclosure") if isinstance(block.get("disclosure"), dict) else {}
        open_entry = facts.get("next_open_buy_top10_mean_daily_return")
        return (
            "揭露：預測下一交易日收盤相對訊號日收盤的方向；"
            f"保留期（{facts.get('holdout_period') or entry['holdout_period']}）方向準確率{_caption_percent(facts.get('holdout_direction_accuracy'))}"
            f"（一律猜跌{_caption_percent(facts.get('always_down_accuracy'))}）、AUC {facts.get('holdout_auc', '未提供')}；"
            f"美股收漲日可交易Top10命中{_caption_percent(facts.get('us_up_day_tradable_top10_hit_rate'))}，"
            f"全部日子{_caption_percent(facts.get('all_days_tradable_top10_hit_rate'))}；"
            f"隔日開盤買進Top10平均約{_caption_percent(open_entry, digits=2)}"
            f"／日{_preopen_open_entry_note(open_entry, entry)}，命中率不代表交易獲利。"
        )
    facts = entry["facts"]
    open_entry = facts["next_open_buy_top10_mean_daily_return"]
    return (
        "揭露：預測下一交易日收盤相對訊號日收盤的方向；"
        f"保留期（{cd.display_day(facts['holdout_start'])}～{cd.display_day(facts['holdout_end'])}，"
        f"{facts['holdout_sessions']}個交易日）方向準確率{_caption_percent(facts['holdout_direction_accuracy'])}"
        f"（一律猜跌{_caption_percent(facts['always_down_accuracy'])}）、AUC {facts['holdout_auc']}；"
        f"美股收漲日可交易Top10命中{_caption_percent(facts['us_up_day_tradable_top10_hit_rate'])}，"
        f"其他日子{_caption_percent(facts['other_days_tradable_top10_hit_rate'])}，"
        f"全部日子{_caption_percent(facts['all_days_tradable_top10_hit_rate'])}，"
        f"Top10預估報酬誤差（MAE）{_caption_percent(facts['tradable_top10_mae'], digits=2)}；"
        f"隔日開盤買進Top10平均約{_caption_percent(open_entry, signed=True, digits=2)}"
        f"／日{_preopen_open_entry_note(open_entry, entry)}，命中率不代表交易獲利。"
    )


def _comparison_caption_lines(payload: dict[str, Any]) -> list[str]:
    """Consume sealed report metadata only; never build or fetch comparisons here."""
    block = payload.get("report_comparison")
    if not isinstance(block, dict):
        return []
    try:
        from stock_report_comparison import comparison_summary
        return [str(line) for line in comparison_summary(block, max_items=2)]
    except Exception:
        return ["歷史比較不可得；既有預測保留，比較資料詳見附件。"]


def report_caption(
    mode: str, stock: StockMatch, payload: dict[str, Any], job_context: dict[str, Any] | None = None,
) -> str:
    if payload.get("schema") == SELECTED_SINGLE_REPORT_SCHEMA:
        return selected_report_caption(mode, stock, payload, job_context)
    if mode not in MODE_HORIZONS or payload.get("report_schema_version") != C0_SINGLE_REPORT_SCHEMA:
        raise AnalysisProcessError("只接受 current C0 H1／H63 收盤報告")
    h = MODE_HORIZONS[mode]
    if payload.get("horizon_sessions") != h:
        raise AnalysisProcessError("C0 caption 期限與請求不一致")
    if payload.get("report_class") == C0_NO_ESTIMATE_REPORT_CLASS:
        return no_estimate_caption(mode, stock, payload, job_context)
    prediction = payload["prediction"]
    metrics = payload.get("comparison_metrics") or {}
    accuracy = safe_number(metrics.get("direction_accuracy"))
    preopen = _preopen_h1_block(payload, h)
    used = preopen is not None and preopen["status"] == "used"
    c0_values = f"上漲機率：{100*prediction['prob_up']:.2f}%；預估報酬：{100*prediction['expected_return']:+.2f}%"
    lines = [
        f"{stock.code} {stock.name}｜" + ("H1 收盤估計（開盤前版）" if used else f"C0 H{h} 收盤估計"),
        f"訊號收盤日：{cd.display_day(payload['latest_data_date'])}；到期日："
        f"{cd.display_day((preopen or {}).get('target_date') if used else payload.get('target_session_date'), empty='尚未驗證日期')}",
        f"目標：訊號收盤至同市場第{h}交易日收盤",
        *(_preopen_caption_lines(preopen) if used else []),
        ("C0 基準（備援）：" + c0_values.replace("：", " ", 2)) if used else c0_values,
        *_comparison_caption_lines(payload),
        "歷史PIT／公司行動／歷史普通股資格未驗證；效果未知；交易效益未驗證。",
    ]
    if accuracy is not None and accuracy < .60:
        lines.append(("C0 基準" if used else "") + "比較方向率低於60%，不能視為可靠預測。")
    if prediction.get("probability_constant_across_stocks") or prediction.get("expected_return_zero_baseline"):
        lines.append("C0 基準屬簡單基準，可能產生相同結果；不具個股排序差異。" if used
                     else "簡單基準可能產生相同結果；不具個股排序差異。")
    # Data time right under the signal date so the caption budget never truncates it.
    lines[2:2] = report_data_time_lines(payload, job_context)
    if preopen is not None and not used:
        # The fallback notice sits right under the title (never truncated by the caption budget).
        lines.insert(1, _preopen_fallback_notice(preopen))
    lines.extend(_report_caution_lines(payload))
    body_budget = TELEGRAM_CAPTION_BUDGET - len(REPORT_CAPTION_DISCLAIMER) - 1
    return "\n".join(lines)[:body_budget] + "\n" + REPORT_CAPTION_DISCLAIMER


def _report_caution_lines(payload: dict[str, Any]) -> list[str]:
    statuses: list[Any] = []
    for key in ("readiness", "data_quality_assessment"):
        value = payload.get(key)
        if isinstance(value, dict):
            statuses.extend(value.get(name) for name in ("quality", "source_status", "status"))
    for key in ("source_statuses", "source_manifest"):
        value = payload.get(key)
        entries = value.values() if isinstance(value, dict) else value if isinstance(value, list) else []
        for entry in entries:
            if isinstance(entry, dict):
                statuses.extend(entry.get(name) for name in ("status", "availability"))
            else:
                statuses.append(entry)
    incomplete = {"partial", "usable_partial", "missing", "unavailable", "unknown_availability", "core_data_incomplete"}
    if any(isinstance(value, str) and value.casefold() in incomplete for value in statuses):
        return ["來源資料不完整（PARTIAL）；缺口與限制保留於 JSON／PDF。"]
    return []


def selected_report_caption(
    mode: str, stock: StockMatch, payload: dict[str, Any], job_context: dict[str, Any] | None = None,
) -> str:
    if mode not in MODE_HORIZONS or payload.get("horizon_sessions") != MODE_HORIZONS[mode]:
        raise AnalysisProcessError("正式單股 caption 期限與請求不一致")
    identity = payload.get("stock")
    if (
        payload.get("research_only") is not False or payload.get("promotion") is not True
        or not isinstance(identity, dict) or str(identity.get("stock_id")) != stock.code
    ):
        raise AnalysisProcessError("正式單股 caption 身分或資格不符")
    h = MODE_HORIZONS[mode]
    prediction = payload.get("prediction") or {}
    probability = safe_number(prediction.get("prob_up"))
    expected_return = safe_number(prediction.get("expected_return"))
    preopen = _preopen_h1_block(payload, h)
    used = preopen is not None and preopen["status"] == "used"
    selected_values = (f"上漲機率：{100*probability:.2f}%；預估報酬：{100*expected_return:+.2f}%"
                       if probability is not None and expected_return is not None else "本次無可交付估計值，詳見附件原因。")
    if used:
        selected_values = (f"C0 基準備援（選定模型）：上漲機率 {100*probability:.2f}%；預估報酬 {100*expected_return:+.2f}%"
                           if probability is not None and expected_return is not None
                           else "C0 基準備援（選定模型）：本次無可交付估計值。")
    target = preopen.get("target_date") if used else payload.get("target_session_date")
    lines = [
        f"{stock.code} {stock.name}｜第{h}交易日收盤估計" + ("（開盤前版）" if used else ""),
        *([_preopen_fallback_notice(preopen)] if preopen is not None and not used else []),
        f"資料日：{cd.display_day(payload.get('asof'), empty='未知')}；目標日：{cd.display_day(target, empty='尚未驗證日期')}",
        *report_data_time_lines(payload, job_context),
        *(_preopen_caption_lines(preopen) if used else []),
        selected_values,
        *_comparison_caption_lines(payload),
        "歷史時點完整性與交易效益未驗證。",
        *_report_caution_lines(payload),
    ]
    if prediction.get("probability_constant_across_stocks") or prediction.get("expected_return_zero_baseline"):
        lines.append(("備援的" if used else "") + "簡單基準可能產生相同結果；不具個股排序差異。")
    if h == 63:
        lines.append("長期限樣本重疊，訊號數不等於獨立樣本數。")
    body_budget = TELEGRAM_CAPTION_BUDGET - len(REPORT_CAPTION_DISCLAIMER) - 1
    return "\n".join(lines)[:body_budget] + "\n" + REPORT_CAPTION_DISCLAIMER


def _preopen_fallback_notice(block: dict[str, Any]) -> str:
    """回退／停用說明行：原因代碼後補中文，例「開盤前版不可用（preopen_stale：中文），本次 H1 改用 C0 基準」。"""
    notice = str(block["notice"])
    reason, reason_zh = block.get("reason"), block.get("reason_zh")
    if not (isinstance(reason, str) and reason and isinstance(reason_zh, str) and reason_zh.strip()):
        return notice
    marker = f"（{reason}）"
    if marker in notice:
        return notice.replace(marker, f"（{reason}：{reason_zh.strip()}）", 1)
    return f"{notice}（{reason}：{reason_zh.strip()}）"


# 分析程式對照表沒有的原因代碼：補上中文；查不到時以「資料或模型條件未滿足」顯示（呼叫端會附上代碼）。
_NO_ESTIMATE_REASON_ZH_SUPPLEMENT = {"prediction_unavailable": "現有模型未產生有效估計"}
_NO_ESTIMATE_REASON_ZH_UNKNOWN = "資料或模型條件未滿足"


def _no_estimate_reason_text(reason: str) -> str:
    """Chinese reason text from the analyzer's own table; unknown codes read 「資料或模型條件未滿足」 (the caller appends the code)."""
    try:
        from stock_daily_contract import _single_support_path

        _single_support_path()
        from tw_single_stock_c0 import _REASON_ZH
    except Exception:  # informational only; the codes are shown either way
        _REASON_ZH = {}
    table = {**_NO_ESTIMATE_REASON_ZH_SUPPLEMENT, **_REASON_ZH}
    return "、".join(table.get(part) or _NO_ESTIMATE_REASON_ZH_UNKNOWN for part in reason.split(",") if part)


def no_estimate_caption(
    mode: str, stock: StockMatch, payload: dict[str, Any], job_context: dict[str, Any] | None = None,
) -> str:
    """Caption for a valid "no estimate" result: reason, the stock's last data day and the data time."""
    h = MODE_HORIZONS[mode]
    reason = str(payload.get("no_estimate_reason") or "unknown")
    preopen = _preopen_h1_block(payload, h)
    used = preopen is not None and preopen["status"] == "used"
    lines = [
        f"{stock.code} {stock.name}｜" + ("H1 收盤估計（開盤前版；C0 基準無法估計）" if used else f"C0 H{h} 收盤估計：無法估計"),
        *([_preopen_fallback_notice(preopen)] if preopen is not None and not used else []),
        ("C0 基準無法估計，原因：" if used else "原因：") + f"{_no_estimate_reason_text(reason)}（{reason}）",
        f"該股最後價格日：{cd.display_day(payload.get('stock_last_price_date'), empty='未知')}；"
        f"分析資料日：{cd.display_day(payload.get('latest_data_date'), empty='未知')}",
        *report_data_time_lines(payload, job_context),
        *(_preopen_caption_lines(preopen) if used else ["本報告只說明資料狀態，不含任何估計值。"]),
        *_comparison_caption_lines(payload),
        "歷史PIT／公司行動／歷史普通股資格未驗證。",
    ]
    body_budget = TELEGRAM_CAPTION_BUDGET - len(REPORT_CAPTION_DISCLAIMER) - 1
    return "\n".join(lines)[:body_budget] + "\n" + REPORT_CAPTION_DISCLAIMER

def _declared_report_paths(log_path: Path, output_dir: Path) -> tuple[Path, Path]:
    """Read the child process' two exact formal output declarations.

    The child analyzers print the paths they actually wrote.  Accept exactly
    one PDF and one JSON declaration from that output; never scan the work
    directory or select an artifact by glob/name/mtime.
    """
    try:
        lines = log_path.read_text(encoding="utf-8", errors="replace").splitlines()
    except OSError as exc:
        raise AnalysisProcessError("個股分析紀錄不可讀取，拒絕尋找報告備援") from exc
    declared: dict[str, list[Path]] = {"PDF": [], "JSON": []}
    for raw_line in lines:
        line = raw_line.strip()
        for label in declared:
            value = ""
            for prefix in (f"{label}：", f"{label}:"):
                if line.startswith(prefix):
                    value = line[len(prefix) :].strip()
                    break
            if not value:
                continue
            if any(char in value for char in "*?[]"):
                raise AnalysisProcessError(f"分析行程 {label} 路徑含萬用字元，拒絕傳送")
            declared[label].append(resolve_path_under(output_dir, Path(value), f"{label} 報告路徑"))
            break
    if len(declared["PDF"]) != 1 or len(declared["JSON"]) != 1:
        raise AnalysisProcessError(
            "分析行程未宣告唯一正式 PDF/JSON 路徑，拒絕任何 latest/candidate 備援"
        )
    return _validate_declared_paths(declared["PDF"][0], declared["JSON"][0], output_dir)


def _validate_declared_paths(pdf_path: Path, json_path: Path, output_dir: Path) -> tuple[Path, Path]:
    for label, path in (("PDF", pdf_path), ("JSON", json_path)):
        if any(char in str(path) for char in "*?[]"):
            raise AnalysisProcessError(f"分析行程 {label} 路徑含萬用字元，拒絕傳送")
    pdf_path = resolve_path_under(output_dir, pdf_path, "PDF 報告路徑")
    json_path = resolve_path_under(output_dir, json_path, "JSON 報告路徑")
    if pdf_path.suffix.lower() != ".pdf" or json_path.suffix.lower() != ".json":
        raise AnalysisProcessError("分析行程正式輸出副檔名不符合 PDF/JSON 契約")
    for path in (pdf_path, json_path):
        _require_formal_report_path(path)
    return pdf_path, json_path


def _require_formal_report_path(path: Path) -> None:
    lowered_parts = {part.casefold() for part in path.parts}
    lowered_name = path.name.casefold()
    if lowered_parts & {"candidate", "candidates", "smoke", "staging", "stage"} or any(
        marker in lowered_name for marker in ("candidate", "smoke", "staging")
    ):
        raise AnalysisProcessError("分析行程輸出位於 candidate/smoke/staging，拒絕傳送")


def _selected_report_payload(document: dict[str, Any]) -> dict[str, Any] | None:
    if document.get("schema") == SELECTED_SINGLE_REPORT_SCHEMA:
        if document.get("report_schema_version") is not None:
            raise ValueError("selected_report_schema_dispatch_ambiguous")
        return document
    payload = document.get("payload")
    if isinstance(payload, dict) and payload.get("schema") == SELECTED_SINGLE_REPORT_SCHEMA:
        if document.get("report_schema_version") is not None:
            raise ValueError("selected_report_schema_dispatch_ambiguous")
        return payload
    return None


def _validate_selected_attachments(
    document: dict[str, Any], payload: dict[str, Any], pdf_bytes: bytes,
    *, stock: StockMatch, horizon: int, expected_runtime_binding: dict[str, Any],
    pdf_path: Path, json_path: Path,
) -> dict[str, Any]:
    from stock_daily_selected_deployment import validate_selected_report

    if not _is_selected_binding(expected_runtime_binding):
        raise ValueError("selected_report_requires_selected_job_binding")
    if payload.get("research_only") is not False or payload.get("promotion") is not True:
        raise ValueError("selected_report_formal_qualification_invalid")
    snapshot = payload.get("snapshot_binding")
    expected_head = c0_binding_head_sha256(expected_runtime_binding)
    if not isinstance(snapshot, dict) or expected_head is None or snapshot.get("head_sha256") != expected_head:
        raise ValueError("selected_report_job_head_mismatch")
    expected_fingerprint = expected_runtime_binding.get("snapshot_fingerprint")
    if not expected_fingerprint or snapshot.get("snapshot_fingerprint") != expected_fingerprint:
        raise ValueError("selected_report_job_snapshot_fingerprint_mismatch")
    verification = validate_selected_report(
        document, pdf_bytes, stock_id=stock.code, horizon=horizon,
        expected_runtime_binding=expected_runtime_binding, pdf_path=pdf_path, require_active=True,
    )
    if not isinstance(verification, dict) or any(
        verification.get(key) is not True for key in ("valid", "verified", "prediction_reproduced", "activation_verified")
    ):
        errors = verification.get("errors") if isinstance(verification, dict) else None
        raise ValueError("selected_report_validation_not_verified:" + str(errors)[:500])
    # The shared validator owns bundle seals, model/sidecar/head and prediction
    # reproduction; any paths the producer records must still name these exact bytes.
    for record in (document, payload) if document is not payload else (payload,):
        if "output_pdf" not in record and "output_json" not in record:
            continue
        if (
            Path(str(record.get("output_pdf") or "")).resolve() != pdf_path
            or Path(str(record.get("output_json") or "")).resolve() != json_path
        ):
            raise ValueError("selected_attachment_declared_path_mismatch")
    return verification


def validate_c0_no_estimate_report(
    payload: dict[str, Any],
    pdf_bytes: bytes,
    *,
    stock_id: str,
    horizon: int,
    expected_snapshot_fingerprint: Any = None,
) -> dict[str, Any]:
    """Validate a ``c0_no_estimate_data_time_only`` report (no estimate; reason + data time).

    Integrity/content still raise C0IntegrityError: identity/schema/target,
    formal (non-QA) attachment, no claimed estimate, seal digest, PDF digest,
    reason and data time present, and the report's own pinned head must be
    byte-identical to its sha and carry the recorded snapshot fingerprint.
    Newer data, a job pin different from the report, or a pinned head that no
    longer exists are version labels (they never block).
    """
    from stock_daily_contract import (
        C0_REPORT_SCHEMA, C0IntegrityError, _canonical, _current_c0_sources, _published_sources,
    )

    stock = payload.get("stock") if isinstance(payload.get("stock"), dict) else {}
    if (
        payload.get("report_schema_version") != C0_REPORT_SCHEMA
        or payload.get("report_class") != C0_NO_ESTIMATE_REPORT_CLASS
        or payload.get("estimate_status") != "no_estimate"
        or payload.get("profile") != "C0"
        or payload.get("source_mode") != "published"
        or payload.get("horizon_sessions") != horizon
        or payload.get("target_definition") != "signal_close_to_same_market_H_session_close"
        or str(stock.get("stock_id")) != str(stock_id)
    ):
        raise C0IntegrityError("c0_no_estimate_report_identity_schema_or_target_invalid")
    if payload.get("qa_override") is not False:
        raise C0IntegrityError("c0_qa_model_override_cannot_be_formal_attachment")
    if (payload.get("historical_pit_verified") is not False or payload.get("historical_membership_verified") is not False
            or payload.get("corporate_actions_verified") is not False or payload.get("effectiveness_status") != "unknown"
            or payload.get("trading_effect") != "unverified"):
        raise C0IntegrityError("c0_report_claims_unverified_historical_or_effectiveness_status")
    prediction = payload.get("prediction")
    if (
        "prob_up" in payload or "expected_return" in payload
        or (prediction is not None and (not isinstance(prediction, dict) or prediction.get("prediction_status") == "predicted"))
    ):
        raise C0IntegrityError("c0_no_estimate_report_carries_estimate")
    reason = payload.get("no_estimate_reason")
    if not isinstance(reason, str) or not reason.strip():
        raise C0IntegrityError("c0_no_estimate_reason_missing")
    data_time = payload.get("data_time")
    if not isinstance(data_time, dict) or not data_time.get("analysis_asof"):
        raise C0IntegrityError("c0_no_estimate_data_time_missing")
    verification = payload.get("verification")
    if not isinstance(verification, dict) or verification.get("status") != "not_applicable_no_estimate":
        raise C0IntegrityError("c0_no_estimate_verification_status_invalid")
    unsigned = dict(payload)
    digest = unsigned.pop("estimate_binding_sha256", None)
    if hashlib.sha256(_canonical(unsigned)).hexdigest() != digest:
        raise C0IntegrityError("c0_report_binding_digest_mismatch")
    if len(pdf_bytes) != payload.get("pdf_size") or hashlib.sha256(pdf_bytes).hexdigest() != payload.get("pdf_sha256"):
        raise C0IntegrityError("c0_report_pdf_digest_or_size_mismatch")
    snapshot = payload.get("published_snapshot") if isinstance(payload.get("published_snapshot"), dict) else {}
    heads = snapshot.get("sources") if isinstance(snapshot.get("sources"), list) else []
    if len(heads) != 1 or not isinstance(heads[0], dict) or not snapshot.get("snapshot_fingerprint"):
        raise C0IntegrityError("c0_no_estimate_published_snapshot_invalid")
    report_head = heads[0]
    labels: list[dict[str, Any]] = []
    head_status = "verified"
    # The report's own pinned head: content-addressed, so different bytes or a
    # different fingerprint are tamper (raise); an absent head is only a label.
    try:
        pinned = _published_sources({
            "root": report_head.get("root"), "manifest_path": report_head.get("manifest_path"),
            "manifest_sha256": report_head.get("manifest_sha256"),
        })
    except FileNotFoundError as exc:
        head_status = "report_head_gone"
        labels.append({"code": "reproduction_inputs_gone", "recorded": report_head.get("manifest_sha256"),
                       "actual": None, "detail": {"reason": str(exc)[:200]}})
    else:
        if pinned.snapshot_descriptor().get("snapshot_fingerprint") != snapshot.get("snapshot_fingerprint"):
            raise C0IntegrityError("c0_no_estimate_snapshot_fingerprint_mismatch")
    # Current head: information only (newer data never invalidates an older report).
    try:
        current_descriptor = _current_c0_sources().descriptor()
        current_head = ((current_descriptor.get("sources") or [{}]) or [{}])[0]
        if current_descriptor.get("snapshot_fingerprint") != snapshot.get("snapshot_fingerprint"):
            labels.append({"code": "newer_data_available",
                           "recorded": {"snapshot_fingerprint": snapshot.get("snapshot_fingerprint"),
                                        "published_at": report_head.get("published_at"),
                                        "head_sha256": report_head.get("manifest_sha256")},
                           "actual": {"snapshot_fingerprint": current_descriptor.get("snapshot_fingerprint"),
                                      "published_at": current_head.get("published_at"),
                                      "head_sha256": current_head.get("manifest_sha256"),
                                      "asof": current_descriptor.get("price_actual_max_date")},
                           "detail": None})
    except (OSError, ValueError) as exc:
        labels.append({"code": "current_head_unreadable", "recorded": None,
                       "actual": type(exc).__name__ + ":" + str(exc)[:200], "detail": None})
    if expected_snapshot_fingerprint and snapshot.get("snapshot_fingerprint") != expected_snapshot_fingerprint:
        labels.append({"code": "job_published_snapshot_changed", "recorded": expected_snapshot_fingerprint,
                       "actual": snapshot.get("snapshot_fingerprint"), "detail": None})
    return {
        "schema": "telegram-c0-no-estimate-report-verification-v1", "verified": True,
        "estimate_status": "no_estimate", "no_estimate_reason": reason,
        "snapshot_fingerprint": snapshot.get("snapshot_fingerprint"), "report_head_status": head_status,
        "prediction_reproduced": False, "reproduction_status": "not_applicable_no_estimate",
        "version_labels": labels,
    }


def _validate_generated_report(
    mode: str,
    stock: StockMatch,
    output_dir: Path,
    pdf_path: Path,
    json_path: Path,
    expected_runtime_binding: dict[str, Any],
    version_labels: list[dict[str, Any]] | None = None,
) -> tuple[Path, dict[str, Any], str, str]:
    """Read the two exact attachments and validate them.

    Integrity/content (digest, schema, PDF markers, declared paths, prediction
    reproduction) still raises.  Differences between the report's pinned data or
    model and the job/current binding come back from ``validate_c0_estimate`` as
    version labels and are appended to ``version_labels``; they never block.
    """
    if mode not in MODE_HORIZONS:
        raise AnalysisProcessError("個股分析期限只接受 H1 或 H63")
    if not isinstance(expected_runtime_binding, dict) or not expected_runtime_binding:
        raise AnalysisProcessError("C0 報告缺少本次工作 runtime binding")
    resolved_pdf = resolve_path_under(output_dir, pdf_path, "PDF 報告路徑")
    selected_path = resolve_path_under(output_dir, json_path, "JSON 報告路徑")
    if resolved_pdf.suffix.lower() != ".pdf" or selected_path.suffix.lower() != ".json":
        raise AnalysisProcessError("報告副檔名不符合 PDF/JSON 契約")
    _require_formal_report_path(resolved_pdf)
    _require_formal_report_path(selected_path)
    pdf_bytes, json_bytes = _read_report_attachments(resolved_pdf, selected_path)
    try:
        from stock_daily_contract import _identity_json, validate_c0_estimate

        document = dict(_identity_json(json_bytes))
        selected_payload = _selected_report_payload(document)
        if selected_payload is not None:
            verification = _validate_selected_attachments(
                document, selected_payload, pdf_bytes, stock=stock, horizon=MODE_HORIZONS[mode],
                expected_runtime_binding=expected_runtime_binding, pdf_path=resolved_pdf, json_path=selected_path,
            )
        elif _is_selected_binding(expected_runtime_binding):
            raise ValueError("selected_job_requires_selected_formal_report_schema")
        elif document.get("report_schema_version") != C0_SINGLE_REPORT_SCHEMA:
            raise ValueError("current_c0_report_schema_required")
        elif document.get("report_class") == C0_NO_ESTIMATE_REPORT_CLASS:
            selected_payload = document
            # A valid "no estimate" result (reason + data time), not an analysis failure.
            verification = validate_c0_no_estimate_report(
                selected_payload,
                pdf_bytes,
                stock_id=stock.code,
                horizon=MODE_HORIZONS[mode],
                expected_snapshot_fingerprint=expected_runtime_binding.get("snapshot_fingerprint"),
            )
        else:
            selected_payload = document
            # Real estimates: unchanged full validation (integrity/reproduction still raise).
            verification = validate_c0_estimate(
                selected_payload,
                pdf_bytes,
                stock_id=stock.code,
                horizon=MODE_HORIZONS[mode],
                expected_snapshot_fingerprint=expected_runtime_binding.get("snapshot_fingerprint"),
                expected_runtime_binding=expected_runtime_binding,
            )
        if selected_payload.get("report_schema_version") == C0_SINGLE_REPORT_SCHEMA and (
            Path(str(selected_payload.get("output_pdf") or "")).resolve() != resolved_pdf
            or Path(str(selected_payload.get("output_json") or "")).resolve() != selected_path
        ):
            raise ValueError("c0_attachment_declared_path_mismatch")
    except (OSError, UnicodeError, ValueError, TypeError, KeyError, ImportError) as exc:
        raise AnalysisProcessError("正式 PDF/JSON 完整性或內容驗證失敗：" + str(exc)) from exc
    labels = verification.get("version_labels") if isinstance(verification, dict) else None
    if isinstance(labels, list) and labels:
        LOGGER.warning(
            "C0 報告版本標籤（不阻擋）：stock=%s mode=%s codes=%s",
            stock.code, mode, [item.get("code") for item in labels if isinstance(item, dict)],
        )
        if version_labels is not None:
            version_labels.extend(item for item in labels if isinstance(item, dict))
    return (
        selected_path, selected_payload,
        hashlib.sha256(pdf_bytes).hexdigest(), hashlib.sha256(json_bytes).hexdigest(),
    )


def _read_report_attachments(pdf_path: Path, json_path: Path) -> tuple[bytes, bytes]:
    try:
        pdf_bytes = pdf_path.read_bytes()
        json_bytes = json_path.read_bytes()
    except OSError as exc:
        raise AnalysisProcessError("本次工作 PDF/JSON 不可讀取，拒絕傳送") from exc
    if not 0 < len(pdf_bytes) <= MAX_TELEGRAM_DOCUMENT_BYTES:
        raise AnalysisProcessError("PDF 是空檔或超過 Telegram 上傳限制")
    if not pdf_bytes.startswith(b"%PDF-") or b"%%EOF" not in pdf_bytes[-4096:]:
        raise AnalysisProcessError("PDF 格式標記或 EOF 不完整，拒絕傳送")
    return pdf_bytes, json_bytes


def _publish_delivery_receipt(directory: Path, evidence: dict[str, Any]) -> None:
    identity = _path_identity(directory.lstat())
    _require_owned_directory(directory, identity)
    raw = (json.dumps(evidence, ensure_ascii=False, sort_keys=True, allow_nan=False) + "\n").encode("utf-8")
    if len(raw) > DELIVERY_RECEIPT_MAX_BYTES:
        raise TelegramApiError("Telegram delivery receipt 超出小型證據限制")
    target = directory / DELIVERY_RECEIPT_NAME
    if not target.exists():
        temporary = directory / (".telegram-delivery-" + uuid.uuid4().hex + ".tmp")
        temporary_identity = None
        try:
            with temporary.open("xb") as handle:
                temporary_identity = _path_identity(os.fstat(handle.fileno()))
                handle.write(raw)
                handle.flush()
                os.fsync(handle.fileno())
            _require_owned_directory(directory, identity)
            try:
                os.link(temporary, target)
            except FileExistsError:
                pass
        finally:
            if temporary_identity is not None:
                _require_owned_directory(directory, identity)
                _unlink_owned_file(temporary, temporary_identity)
    _require_owned_directory(directory, identity)
    info = target.lstat()
    if (not stat.S_ISREG(info.st_mode) or info.st_nlink != 1
            or getattr(info, "st_file_attributes", 0) & getattr(stat, "FILE_ATTRIBUTE_REPARSE_POINT", 0x400)
            or not 0 < info.st_size <= DELIVERY_RECEIPT_MAX_BYTES):
        raise TelegramApiError("Telegram delivery receipt 檔案身分無效")
    with target.open("rb") as handle:
        existing_raw = handle.read(DELIVERY_RECEIPT_MAX_BYTES + 1)
        after = os.fstat(handle.fileno())
    _require_owned_directory(directory, identity)
    final = target.lstat()
    if any((_path_identity(item), item.st_size, item.st_mtime_ns) != (
        _path_identity(info), info.st_size, info.st_mtime_ns,
    ) for item in (after, final)) or len(existing_raw) != info.st_size:
        raise TelegramApiError("Telegram delivery receipt 讀取期間變動")
    try:
        existing = json.loads(existing_raw.decode("utf-8"), object_pairs_hook=SingleStockPhaseReader._strict_object)
        recorded = datetime.fromisoformat(existing["recordedAt"])
        if recorded.tzinfo is None:
            raise ValueError("receipt time requires timezone")
    except (UnicodeError, ValueError, TypeError, KeyError) as exc:
        raise TelegramApiError("Telegram delivery receipt 格式無效") from exc
    expected = dict(evidence)
    expected["recordedAt"] = existing["recordedAt"]
    if existing != expected:
        raise TelegramApiError("Telegram delivery receipt 已存在且不一致，拒絕覆寫")


class TelegramStockCommandBot:
    def __init__(self, env_values: dict[str, str]) -> None:
        token = str(env_values.get("TELEGRAM_BOT_TOKEN", "")).strip()
        chat_id = str(env_values.get("TELEGRAM_CHAT_ID", "")).strip()
        if not TOKEN_PATTERN.fullmatch(token) or not CHAT_ID_PATTERN.fullmatch(chat_id):
            raise ConfigurationError("Telegram Token 或 Chat ID 缺少／格式錯誤")
        self.chat_id = chat_id
        self.client = TelegramClient(token, chat_id, timeout_seconds=120, max_attempts=4)
        self.progress_client = TelegramClient(token, chat_id, timeout_seconds=5, max_attempts=1)
        self.max_queue = int_setting(env_values, "TELEGRAM_COMMAND_MAX_QUEUE", 10, 1, 50)
        self.analysis_timeout_seconds = int_setting(
            env_values, "TELEGRAM_COMMAND_ANALYSIS_TIMEOUT_MINUTES", 90, 10, 240
        ) * 60
        self.preparation_timeout_seconds = min(
            self.analysis_timeout_seconds,
            int_setting(env_values, SINGLE_STOCK_PREPARATION_TIMEOUT_ENV, 45, 1, 240) * 60,
        )
        self.heartbeat_seconds = int_setting(env_values, "TELEGRAM_COMMAND_HEARTBEAT_SECONDS", 300, 60, 1800)
        self.resolve_timeout_seconds = int_setting(env_values, "TELEGRAM_COMMAND_RESOLVE_TIMEOUT_SECONDS", 180, 30, 600)
        self.store = CommandStateStore(STATE_PATH, self.max_queue)
        self.work_queue: queue.Queue[str] = queue.Queue()
        self.stop_event = threading.Event()
        self.current_process_lock = threading.Lock()
        self.current_process: subprocess.Popen[Any] | None = None
        self.worker: threading.Thread | None = None
        # Each job records the data/model binding it started on.  Later changes
        # (newer data, model/policy) are version labels: the job finishes and the
        # caption names the newer data instead of stopping.
        self.analysis_env: dict[str, str] = {}
        self.c0_binding: dict[str, Any] = {}
        self._job_c0_binding: dict[str, Any] | None = None
        self._job_analysis_env: dict[str, str] | None = None
        self._job_comparison_cache: dict[str, Any] | None = None
        self._verified_selected_reports: dict[tuple[str, ...], tuple[dict[str, Any], dict[str, Any]]] = {}
        self._job_read_at: str | None = None
        self._job_version_labels: list[dict[str, Any]] = []
        self.c0_startup_warning = ""

        self.analysis_secret_values = sensitive_values(env_values)
        self.heartbeat_lock = threading.Lock()
        self.heartbeat_failure: BaseException | None = None
        self.heartbeat_data: dict[str, Any] = {
            "pid": os.getpid(),
            "startedAt": now_iso(),
            "status": "created",
            "sourceSha256": SOURCE_SHA256,
            "runtimeRevision": RUNTIME_REVISION,
            "serviceHeartbeatStaleSeconds": SERVICE_HEARTBEAT_STALE_SECONDS,
            "nextUpdateId": self.store.next_update_id(),
            "currentJobId": None,
            "currentProcessId": None,
            "currentMode": "",
            "lastError": "",
        }

    @staticmethod
    def _close_source_binding(binding) -> None:
        close = getattr(binding, "close_source_lease", None)
        if close is not None:
            close()

    def refresh_analysis_environment(self, *, retain_source_lease: bool = False) -> None:
        """Resolve authority; only an active job retains the source-reader owner."""
        environment, binding = analysis_environment()
        prior = getattr(self, "c0_binding", None)
        if prior is not getattr(self, "_job_c0_binding", None):
            self._close_source_binding(prior)
        if not retain_source_lease:
            self._close_source_binding(binding)
        self.analysis_env = environment
        self.c0_binding = binding
        self.analysis_secret_values = tuple(sorted(
            set(getattr(self, "analysis_secret_values", ())) | set(sensitive_values(environment)),
            key=len, reverse=True,
        ))

    def _analysis_python_for_child(self) -> str:
        environment = getattr(self, "analysis_env", None)
        if not isinstance(environment, dict) or not environment:
            raise AnalysisProcessError("C0 工作環境尚未完成驗證")
        try:
            return resolve_analysis_python(environment)
        except (ConfigurationError, OSError) as exc:
            raise AnalysisProcessError("固定 analysis Python runtime 無法解析") from exc

    def _note_job_labels(self, labels: list[dict[str, Any]]) -> None:
        existing = getattr(self, "_job_version_labels", None)
        if not isinstance(existing, list):
            existing = []
            self._job_version_labels = existing
        seen = {json.dumps(item, ensure_ascii=False, sort_keys=True, default=str) for item in existing}
        for item in labels:
            key = json.dumps(item, ensure_ascii=False, sort_keys=True, default=str)
            if key not in seen:
                seen.add(key)
                existing.append(json.loads(key))  # JSON-safe copy for state/audit files

    def _job_caption_context(self) -> dict[str, Any]:
        return {
            "read_at": getattr(self, "_job_read_at", None),
            "current_binding": getattr(self, "c0_binding", None),
            "labels": list(getattr(self, "_job_version_labels", None) or []),
        }

    def refresh_analysis_environment_for_mode(self, job_id: str, mode: str) -> None:
        """Re-read the current binding and label (never stop on) changes since the job pin."""
        baseline = getattr(self, "_job_c0_binding", None)
        if not isinstance(baseline, dict) or not baseline:
            raise AnalysisProcessError("C0 工作尚未建立 pin，已停止")
        if _is_selected_binding(baseline):
            self._note_job_labels(_check_selected_deployment(baseline))
            pinned_environment = getattr(self, "_job_analysis_env", None)
            if not isinstance(pinned_environment, dict) or not pinned_environment:
                raise SelectedBindingIntegrityError("正式單股工作缺少已 pin 的環境")
            self.analysis_env = dict(pinned_environment)
            return
        try:
            self.refresh_analysis_environment()
        except C0BindingIntegrityError:
            raise  # integrity is KEEP: tamper/torn read still stops the job
        except AnalysisProcessError as exc:
            # The job keeps its start pin; the child or the report validation
            # still fails on its own if the data it needs is really unreadable.
            LOGGER.warning("C0 目前資料暫時無法重新讀取，工作沿用開始時資料：job=%s mode=%s：%s", job_id, mode, exc)
            self._note_job_labels([{
                "code": "current_binding_unreadable_during_job",
                "recorded": c0_binding_data_time(baseline), "actual": None, "detail": str(exc)[:300],
            }])
            return
        if _is_selected_binding(self.c0_binding):
            self._note_job_labels(c0_binding_version_labels(baseline, self.c0_binding))
            pinned_environment = getattr(self, "_job_analysis_env", None)
            if isinstance(pinned_environment, dict) and pinned_environment:
                self.analysis_env = dict(pinned_environment)
            return
        labels = c0_binding_version_labels(baseline, self.c0_binding)
        if labels:
            LOGGER.warning(
                "C0 工作期間資料或版本已更新，沿用開始時資料完成：job=%s mode=%s codes=%s",
                job_id, mode, [item["code"] for item in labels],
            )
            self._note_job_labels(labels)

    def validate_report_before_upload(
        self, job_id: str, stock: StockMatch, report: GeneratedReport,
    ) -> GeneratedReport:
        """Re-read the declared attachments before any upload; refresh the caption's data-time notes."""
        self.refresh_analysis_environment_for_mode(job_id, report.mode)
        if report.json_path is None:
            raise AnalysisProcessError("C0 上傳附件缺少 JSON")
        output_dir = report.output_dir if report.output_dir is not None else report.pdf_path.parent
        if report.declared_paths is None:
            raise AnalysisProcessError("C0 上傳附件缺少已核驗的正式輸出宣告")
        declared_pdf, declared_json = _validate_declared_paths(*report.declared_paths, output_dir)
        if declared_pdf != report.pdf_path.resolve() or declared_json != report.json_path.resolve():
            raise AnalysisProcessError("C0 上傳附件宣告路徑變動")
        cache_key = self._selected_report_cache_key(stock, report)
        cached = getattr(self, "_verified_selected_reports", {}).get(cache_key)
        if _is_selected_binding(self._job_c0_binding) and cached is not None and cached[0] == self._job_c0_binding:
            pdf_bytes, json_bytes = _read_report_attachments(declared_pdf, declared_json)
            if (hashlib.sha256(pdf_bytes).hexdigest(), hashlib.sha256(json_bytes).hexdigest()) != (
                report.pdf_sha256, report.json_sha256,
            ):
                raise AnalysisProcessError("正式 PDF/JSON附件在上傳前變動")
            return dataclass_replace(
                report, caption=report_caption(report.mode, stock, cached[1], self._job_caption_context()),
            )
        labels: list[dict[str, Any]] = []
        _, payload, pdf_sha256, json_sha256 = _validate_generated_report(
            report.mode, stock, output_dir, declared_pdf, declared_json,
            self._job_c0_binding, labels,
        )
        if (pdf_sha256, json_sha256) != (report.pdf_sha256, report.json_sha256):
            raise AnalysisProcessError("C0 PDF/JSON附件在上傳前變動")
        self._note_job_labels(labels)
        return dataclass_replace(
            report, caption=report_caption(report.mode, stock, payload, self._job_caption_context()),
        )

    @staticmethod
    def _selected_report_cache_key(stock: StockMatch, report: GeneratedReport) -> tuple[str, ...]:
        return (
            stock.code, report.mode, str(report.pdf_path.resolve()),
            str(report.json_path.resolve()) if report.json_path is not None else "",
            report.pdf_sha256, report.json_sha256,
        )

    def _record_delivery_evidence(
        self,
        job_id: str,
        code: str,
        mode: str,
        kind: str,
        api_result: object,
        *,
        text: str = "",
        path: Path | None = None,
        json_path: Path | None = None,
        report: GeneratedReport | None = None,
    ) -> None:
        """Keep a small create-only ACK beside the exact verified attachments.

        The real client must return Telegram's message object.  Test doubles
        are intentionally ignored so existing deterministic unit fixtures do
        not become a network simulation.  No token, chat id, caption, or raw
        response body is written; only hashes, sizes, lineage, and message id
        are retained.
        """
        if not isinstance(self.client, TelegramClient):
            return
        if (not isinstance(api_result, dict) or type(api_result.get("message_id")) is not int
                or api_result["message_id"] <= 0):
            raise TelegramApiError("Telegram 回覆缺少可核驗 message_id")
        binding = getattr(self, "_job_c0_binding", None)
        if not isinstance(binding, dict) or not binding:
            raise TelegramApiError("Telegram delivery evidence 缺少本次 C0 binding")
        if kind != "document" or report is None or path is None or json_path is None or text:
            raise TelegramApiError("Telegram delivery receipt 必須對應已驗證的報告附件")
        if report.declared_paths is None or report.json_path is None or mode != report.mode:
            raise TelegramApiError("Telegram delivery receipt 缺少本次正式輸出宣告")
        output_dir = report.output_dir if report.output_dir is not None else report.pdf_path.parent
        declared_pdf, declared_json = _validate_declared_paths(*report.declared_paths, output_dir)
        if (declared_pdf != path.resolve() or declared_pdf != report.pdf_path.resolve()
                or declared_json != json_path.resolve() or declared_json != report.json_path.resolve()
                or declared_pdf.parent != declared_json.parent):
            raise TelegramApiError("Telegram delivery receipt 附件路徑不一致")
        pdf_bytes, json_bytes = _read_report_attachments(declared_pdf, declared_json)
        pdf_sha = hashlib.sha256(pdf_bytes).hexdigest()
        json_sha = hashlib.sha256(json_bytes).hexdigest()
        if (pdf_sha, json_sha) != (report.pdf_sha256, report.json_sha256):
            raise TelegramApiError("Telegram delivery receipt 附件 SHA256 與已驗證成品不一致")
        head_sha = c0_binding_head_sha256(binding)
        model_sha = _binding_model_sha(binding)
        if (any(value is not None and not SHA256_HEX_PATTERN.fullmatch(str(value))
                for value in (head_sha, model_sha))
                or (_is_selected_binding(binding) and (head_sha is None or model_sha is None))):
            raise TelegramApiError("Telegram delivery receipt 缺少已驗證 head/model SHA256")
        secrets = getattr(self, "analysis_secret_values", ())
        labels = [
            {key: redact_sensitive_text(str(label[key]), secrets) if label[key] is not None else None
             for key in ("code", "recorded", "actual", "detail") if key in label}
            for label in (getattr(self, "_job_version_labels", None) or []) if isinstance(label, dict)
        ]
        evidence: dict[str, Any] = {
            "schemaVersion": "telegram-command-bot-delivery-evidence-v2",
            "recordedAt": now_iso(),
            "requestId": str(job_id),
            "code": str(code),
            "mode": str(mode),
            "kind": str(kind),
            "reportClass": "C0",
            "headSha256": head_sha,
            "modelSha256": model_sha,
            "snapshotFingerprint": binding.get("snapshot_fingerprint"),
            "dataAsOf": binding.get("asof"),
            "c0JobReadAt": getattr(self, "_job_read_at", None),
            "versionLabels": labels,
            "historicalPITVerified": False,
            "effectivenessStatus": "unknown",
            "sourceSha256": SOURCE_SHA256,
            "runtimeRevision": RUNTIME_REVISION,
            "telegramMessageId": str(api_result.get("message_id")),
            "fileName": declared_pdf.name,
            "fileSha256": pdf_sha,
            "fileSize": len(pdf_bytes),
            "jsonName": declared_json.name,
            "jsonSha256": json_sha,
            "jsonSize": len(json_bytes),
        }
        if _is_selected_binding(binding):
            evidence["reportClass"] = SELECTED_SINGLE_REPORT_SCHEMA
            evidence["activationId"] = binding["deployment_binding"].get("activation_id")
        try:
            _publish_delivery_receipt(declared_pdf.parent, evidence)
        except (OSError, AnalysisProcessError) as exc:
            raise TelegramApiError("Telegram delivery receipt 無法安全保存") from exc

    def write_heartbeat(self, status: str | None = None, **fields: Any) -> None:
        with self.heartbeat_lock:
            if status:
                self.heartbeat_data["status"] = status
            self.heartbeat_data.update(fields)
            self.heartbeat_data["pid"] = os.getpid()
            self.heartbeat_data["updatedAt"] = now_iso()
            payload = dict(self.heartbeat_data)
        try:
            for attempt in range(1, HEARTBEAT_WRITE_MAX_ATTEMPTS + 1):
                try:
                    atomic_write_json(HEARTBEAT_PATH, payload)
                    return
                except PermissionError:
                    if attempt >= HEARTBEAT_WRITE_MAX_ATTEMPTS:
                        raise
                    LOGGER.warning(
                        "Heartbeat 取代檔案被鎖定，%.1f 秒後重試（%d/%d）",
                        HEARTBEAT_WRITE_RETRY_SECONDS,
                        attempt,
                        HEARTBEAT_WRITE_MAX_ATTEMPTS,
                    )
                    time.sleep(HEARTBEAT_WRITE_RETRY_SECONDS)
        except Exception as exc:
            if isinstance(exc, OSError) and exc.errno == errno.ENOSPC:
                observed = time.monotonic()
                with self.heartbeat_lock:
                    last_logged = getattr(self, "_heartbeat_enospc_last_logged_at", None)
                    log_due = last_logged is None or observed - last_logged >= HEARTBEAT_ENOSPC_LOG_INTERVAL_SECONDS
                    if log_due:
                        self._heartbeat_enospc_last_logged_at = observed
                if log_due:
                    try:
                        LOGGER.warning("磁碟空間不足，heartbeat 暫未寫入；服務繼續執行，工作狀態保存仍嚴格處理")
                    except OSError as log_error:
                        if log_error.errno != errno.ENOSPC:
                            raise
                return
            self.stop_event.set()
            with self.heartbeat_lock:
                if self.heartbeat_failure is None:
                    self.heartbeat_failure = exc
            LOGGER.exception("無法寫入 Telegram 服務 heartbeat；服務主循環將停止")
            raise

    @staticmethod
    def _attach_cleanup_error(
        primary_error: BaseException,
        context: str,
        cleanup_error: BaseException,
    ) -> None:
        if primary_error is cleanup_error:
            LOGGER.error("%s: primary and cleanup exceptions are identical; skip self-attachment", context)
            return
        detail = f"{context}: {type(cleanup_error).__name__}: {cleanup_error}"
        LOGGER.error("%s", detail)
        add_note = getattr(primary_error, "add_note", None)
        if callable(add_note):
            note = f"cleanup error: {detail}"
            try:
                existing_notes = getattr(primary_error, "__notes__", ())
                if isinstance(existing_notes, (list, tuple)) and note in existing_notes:
                    return
                add_note(note)
            except Exception:
                LOGGER.exception("無法將 cleanup error 附加至主要例外：%s", context)

    def _promote_heartbeat_failure(
        self,
        primary_error: BaseException | None,
        heartbeat_exc: BaseException,
        context: str,
    ) -> BaseException:
        with self.heartbeat_lock:
            heartbeat_primary = self.heartbeat_failure
            if heartbeat_primary is None:
                heartbeat_primary = heartbeat_exc
                self.heartbeat_failure = heartbeat_primary
        if primary_error is not None and primary_error is not heartbeat_primary:
            self._attach_cleanup_error(heartbeat_primary, "previous primary error", primary_error)
        if heartbeat_exc is not heartbeat_primary and heartbeat_exc is not primary_error:
            self._attach_cleanup_error(heartbeat_primary, context, heartbeat_exc)
        return heartbeat_primary

    def _terminate_analysis_process(
        self,
        process: subprocess.Popen[Any] | None,
    ) -> list[BaseException]:
        errors: list[BaseException] = []
        if process is None:
            return errors
        try:
            running = process.poll() is None
        except BaseException as exc:
            errors.append(exc)
            running = True
        if not running:
            return errors
        try:
            process.terminate()
        except BaseException as exc:
            errors.append(exc)
        try:
            process.wait(timeout=ANALYSIS_CLEANUP_TIMEOUT_SECONDS)
        except subprocess.TimeoutExpired:
            try:
                process.kill()
            except BaseException as exc:
                errors.append(exc)
            try:
                process.wait(timeout=ANALYSIS_CLEANUP_TIMEOUT_SECONDS)
            except BaseException as exc:
                errors.append(exc)
        except BaseException as exc:
            errors.append(exc)
            try:
                process.kill()
            except BaseException as kill_exc:
                errors.append(kill_exc)
            try:
                process.wait(timeout=ANALYSIS_CLEANUP_TIMEOUT_SECONDS)
            except BaseException as wait_exc:
                errors.append(wait_exc)
        try:
            if process.poll() is None:
                errors.append(RuntimeError(f"analysis child remained alive: pid={process.pid}"))
        except BaseException as exc:
            errors.append(exc)
        return errors

    def _cleanup_runtime(self) -> list[BaseException]:
        """Stop polling and boundedly clean every resident worker/analysis child."""
        cleanup_errors: list[BaseException] = []
        self.stop_event.set()
        with self.current_process_lock:
            process = self.current_process
        cleanup_errors.extend(self._terminate_analysis_process(process))
        if process is not None:
            with self.current_process_lock:
                if self.current_process is process:
                    self.current_process = None

        worker = self.worker
        if worker is not None and worker is not threading.current_thread():
            try:
                worker.join(timeout=WORKER_JOIN_TIMEOUT_SECONDS)
            except BaseException as exc:
                cleanup_errors.append(exc)
            if worker.is_alive():
                cleanup_errors.append(RuntimeError("Telegram worker did not stop before cleanup deadline"))

        # A worker can cross Popen() while the first snapshot is being taken;
        # take one last locked snapshot so no child survives cleanup.
        with self.current_process_lock:
            late_process = self.current_process
        if late_process is not None and late_process is not process:
            cleanup_errors.extend(self._terminate_analysis_process(late_process))
            with self.current_process_lock:
                if self.current_process is late_process:
                    self.current_process = None
        return cleanup_errors

    def record_fatal_error(self, exc: BaseException) -> None:
        try:
            self.write_heartbeat(
                status="fatal",
                stoppedAt=now_iso(),
                lastError=f"{type(exc).__name__}: {exc}"[:1000],
                currentJobId=None,
                currentProcessId=None,
                currentMode="",
            )
        except Exception as heartbeat_exc:
            LOGGER.exception("無法寫入 fatal heartbeat；保留原始服務例外：%s", type(exc).__name__)
            raise exc from heartbeat_exc

    def verify(self) -> None:
        missing = [
            str(path)
            for path in (
                ANALYSIS_ENTRY_SCRIPT,
                C0_ANALYSIS_SCRIPT,
                ANALYSIS_PYTHON_RESOLVER,
            )
            if not path.is_file()
        ]
        if missing:
            raise ConfigurationError("找不到個股分析程式：" + " | ".join(missing))
        # Service start does not depend on the data/model binding: when even the
        # published head is unreadable, log a warning and keep listening; each job
        # re-reads the data when it starts and reports its own failure.
        try:
            self.refresh_analysis_environment()
            self.c0_startup_warning = ""
            if self.c0_binding.get("schema") == C0_BINDING_UNAVAILABLE_SCHEMA:
                self.c0_startup_warning = "C0 runtime binding 不完整：" + str(self.c0_binding.get("binding_error"))[:500]
        except AnalysisProcessError as exc:
            self.analysis_env = _child_analysis_environment()
            self.c0_binding = {}
            self.c0_startup_warning = str(exc)[:1000]
            LOGGER.warning("啟動時讀不到 C0 已發布資料；服務照常監聽，工作開始時再讀取：%s", exc)
        heartbeat_lock = getattr(self, "heartbeat_lock", None)
        heartbeat_data = getattr(self, "heartbeat_data", None)
        if heartbeat_lock is not None and isinstance(heartbeat_data, dict):
            with heartbeat_lock:
                heartbeat_data["c0StartupWarning"] = self.c0_startup_warning
                heartbeat_data["c0StartupDataTime"] = c0_binding_data_time(self.c0_binding or None)
        webhook = self.client.get_webhook_info()
        if str(webhook.get("url") or "").strip():
            raise ConfigurationError("Telegram Bot 已設定 webhook，無法同時使用本機 getUpdates 長輪詢")
        self.client.get_me()
        chat = self.client.get_chat()
        if str(chat.get("type")) != "private":
            raise ConfigurationError("TELEGRAM_CHAT_ID 必須是私人對話")

    def bootstrap(self) -> None:
        self.verify()
        self.client.set_my_commands(
            [
                {"command": "stock", "description": "H63 收盤估計，例如 /stock 2330"},
                {"command": "h63", "description": "第63市場交易日收盤估計，例如 /h63 2330"},
                {"command": "h1", "description": "下一市場交易日收盤估計，例如 /h1 2330"},
                {"command": "open", "description": "H1 收盤估計相容指令，例如 /open 2330"},
                {"command": "tomorrow", "description": "H1 收盤估計相容指令，例如 /tomorrow 2330"},
                {"command": "all", "description": "產生 H63 與 H1 收盤報告，例如 /all 2330"},
                {"command": "status", "description": "查詢分析佇列"},
                {"command": "help", "description": "顯示使用說明"},
            ]
        )
        if self.store.next_update_id() is None:
            offset: int | None = None
            for _ in range(50):
                updates = self.client.get_updates(offset=offset, timeout_seconds=0, limit=100)
                if not updates:
                    break
                update_ids = [int(item["update_id"]) for item in updates if isinstance(item.get("update_id"), int)]
                if not update_ids:
                    break
                offset = max(update_ids) + 1
            self.store.set_next_update_id(offset or 0)
            LOGGER.info("首次啟動已略過既有 Telegram 歷史訊息，nextUpdateId=%s", offset or 0)
        if not self.store.ready_announced():
            self.client.send_message(READY_TEXT)
            self.store.mark_ready_announced()

    def start_worker(self) -> None:
        interrupted = self.store.recover_interrupted_jobs()
        for job in interrupted:
            try:
                self.client.send_message(f"工作 {job.get('jobId')} 因服務中斷而停止，請重新送出股票指令。")
            except TelegramApiError:
                LOGGER.exception("無法通知中斷工作：%s", job.get("jobId"))
        for job_id in self.store.queued_job_ids():
            self.work_queue.put(job_id)
        self.worker = threading.Thread(target=self._worker_loop, name="telegram-stock-worker", daemon=False)
        self.worker.start()

    def run_forever(self) -> None:
        primary_error: BaseException | None = None
        try:
            self.write_heartbeat(status="booting", lastPollStatus="not_started", lastError="")
            self.bootstrap()
            self.start_worker()
            LOGGER.info("Telegram 個股指令服務已開始長輪詢")
            self.write_heartbeat(status="running", lastPollStatus="bootstrap_ok", lastError="")
            while not self.stop_event.is_set():
                try:
                    offset = self.store.next_update_id() or 0
                    self.write_heartbeat(
                        status="polling",
                        lastPollStartedAt=now_iso(),
                        nextUpdateId=offset,
                    )
                    updates = self.client.get_updates(offset=offset, timeout_seconds=50, limit=100)
                    if self.stop_event.is_set():
                        break
                    self.write_heartbeat(
                        status="running",
                        lastPollCompletedAt=now_iso(),
                        lastPollStatus=f"ok updates={len(updates)}",
                        lastError="",
                    )
                    for update in updates:
                        if self.stop_event.is_set():
                            break
                        update_id = update.get("update_id")
                        if not isinstance(update_id, int):
                            continue
                        try:
                            self.handle_update(update)
                        except Exception:
                            LOGGER.exception("處理 Telegram update 失敗：%s", update_id)
                        finally:
                            self.store.set_next_update_id(max(offset, update_id + 1))
                            offset = max(offset, update_id + 1)
                    self.write_heartbeat(nextUpdateId=self.store.next_update_id() or offset)
                except TelegramApiError as exc:
                    if self.stop_event.is_set():
                        break
                    LOGGER.error("Telegram 長輪詢暫時失敗：%s", exc)
                    self.write_heartbeat(
                        status="degraded",
                        lastPollCompletedAt=now_iso(),
                        lastPollStatus="telegram_error",
                        lastError=str(exc)[:1000],
                    )
                    self.stop_event.wait(10)
                except Exception as exc:
                    if self.stop_event.is_set() and self.heartbeat_failure is not None:
                        raise self.heartbeat_failure
                    if self.stop_event.is_set():
                        break
                    if self.heartbeat_failure is exc:
                        raise
                    LOGGER.exception("Telegram 長輪詢發生未預期錯誤")
                    self.write_heartbeat(
                        status="degraded",
                        lastPollCompletedAt=now_iso(),
                        lastPollStatus="unexpected_error",
                        lastError=f"{type(exc).__name__}: {exc}"[:1000],
                    )
                    self.stop_event.wait(10)
            if self.heartbeat_failure is not None:
                raise self.heartbeat_failure
        except BaseException as exc:
            primary_error = exc
        finally:
            try:
                self.write_heartbeat(
                    status="stopped",
                    stoppedAt=now_iso(),
                    currentJobId=None,
                    currentProcessId=None,
                    currentMode="",
                )
            except BaseException as heartbeat_exc:
                self.stop_event.set()
                primary_error = self._promote_heartbeat_failure(
                    primary_error,
                    heartbeat_exc,
                    "final heartbeat cleanup",
                )
            try:
                cleanup_errors = self._cleanup_runtime()
            except BaseException as cleanup_exc:
                cleanup_errors = [cleanup_exc]
            if primary_error is None and cleanup_errors:
                primary_error = RuntimeError("Telegram runtime cleanup failed")
            if primary_error is not None:
                for cleanup_error in cleanup_errors:
                    self._attach_cleanup_error(primary_error, "runtime cleanup", cleanup_error)
        if primary_error is not None:
            raise primary_error

    def handle_update(self, update: dict[str, Any]) -> None:
        message = update.get("message")
        if not isinstance(message, dict):
            return
        chat = message.get("chat")
        if not isinstance(chat, dict) or str(chat.get("id")) != self.chat_id or str(chat.get("type")) != "private":
            LOGGER.warning("忽略非授權 Telegram 對話訊息")
            return
        text = message.get("text")
        if not isinstance(text, str):
            self.client.send_message("目前只接受股票代碼或名稱文字。輸入 /help 查看說明。")
            return
        parsed = parse_command(text)
        if parsed.action == "ignore":
            return
        if parsed.action == "help":
            self.client.send_message(HELP_TEXT)
            return
        if parsed.action == "status":
            self.client.send_message(self.store.status_text())
            return


        if parsed.action != "job":
            self.client.send_message("無法辨識指令。\n\n" + HELP_TEXT)
            return
        update_id = int(update["update_id"])
        requested_command = re.sub(
            r"@[A-Za-z0-9_]+$", "", str(text).split(maxsplit=1)[0]
        ).casefold()
        try:
            job, created, position = self.store.add_job(
                update_id, parsed.query, parsed.mode, requested_command
            )
        except QueueFullError as exc:
            self.client.send_message(str(exc))
            return
        if created:
            self.work_queue.put(str(job["jobId"]))
            LOGGER.info("工作加入佇列：%s query=%s mode=%s position=%s", job["jobId"], parsed.query, parsed.mode, position)
            self.client.send_message(
                f"已加入分析佇列｜{parsed.query}\n"
                f"類型：{MODE_LABELS[parsed.mode]}\n"
                f"排隊位置：{position or 1}\n"
                f"工作編號：{job['jobId']}"
            )
        else:
            self.client.send_message(f"相同工作已在排隊或執行中：{job.get('jobId')}")

    def _worker_loop(self) -> None:
        while not self.stop_event.is_set():
            try:
                job_id = self.work_queue.get(timeout=1)
            except queue.Empty:
                continue
            primary_error: BaseException | None = None
            try:
                self.write_heartbeat(status="working", currentJobId=job_id)
                self.process_job(job_id)
            except BaseException as exc:
                if self.heartbeat_failure is exc:
                    primary_error = exc
                elif isinstance(exc, Exception):
                    LOGGER.exception("個股工作未預期失敗：%s", job_id)
                    try:
                        self.write_heartbeat(status="degraded", lastError=f"job {job_id} 未預期失敗")
                    except BaseException as heartbeat_exc:
                        self.stop_event.set()
                        primary_error = heartbeat_exc
                    try:
                        self.store.update_job(job_id, status="failed", completedAt=now_iso(), error="未預期錯誤")
                        self.client.send_message(f"工作 {job_id} 發生未預期錯誤，已停止。")
                    except BaseException as cleanup_exc:
                        LOGGER.exception("無法記錄個股工作錯誤：%s", job_id)
                        if primary_error is not None:
                            self._attach_cleanup_error(primary_error, "worker error cleanup", cleanup_exc)
                        else:
                            primary_error = cleanup_exc
                else:
                    primary_error = exc
            finally:
                try:
                    self.write_heartbeat(status="running", currentJobId=None, currentProcessId=None, currentMode="")
                except BaseException as heartbeat_exc:
                    self.stop_event.set()
                    primary_error = self._promote_heartbeat_failure(
                        primary_error,
                        heartbeat_exc,
                        "final worker heartbeat cleanup",
                    )
                try:
                    self.work_queue.task_done()
                except BaseException as cleanup_exc:
                    if primary_error is None:
                        primary_error = cleanup_exc
                    else:
                        self._attach_cleanup_error(primary_error, "worker cleanup", cleanup_exc)
            if primary_error is not None:
                raise primary_error

    def select_job_source(self) -> None:
        """Record the binding this job starts on; only completely unreadable data fails the job."""
        self._close_source_binding(getattr(self, "_job_c0_binding", None))
        self.refresh_analysis_environment(retain_source_lease=True)
        self._job_c0_binding = self.c0_binding
        self._job_analysis_env = dict(self.analysis_env)
        self._verified_selected_reports = {}
        if _is_selected_binding(self._job_c0_binding) and c0_binding_head_sha256(self._job_c0_binding) is None:
            raise SelectedBindingIntegrityError("正式單股工作缺少可驗證 head pin")
        self._job_read_at = now_iso()
        if self._job_c0_binding.get("schema") == C0_BINDING_UNAVAILABLE_SCHEMA:
            self._note_job_labels([{
                "code": "c0_runtime_binding_unavailable", "recorded": None,
                "actual": c0_binding_data_time(self._job_c0_binding),
                "detail": self._job_c0_binding.get("binding_error"),
            }])

    @staticmethod
    def _comparison_bridge():
        if str(ANALYSIS_DIR) not in sys.path:
            sys.path.insert(0, str(ANALYSIS_DIR))
        return importlib.import_module("report_comparison_bridge")

    def _begin_comparison_cache(self, job_id: str) -> None:
        # Older/incompletely initialized callers retain the original uncached path.
        if not hasattr(self, "_job_comparison_cache"):
            return
        self._job_comparison_cache = None
        try:
            parent = resolve_path_under(DSTOCK_SINGLE_ROOT, JOBS_DIR / job_id, "比較暫存工作路徑")
            parent.mkdir(parents=True, exist_ok=True)
            nonce = uuid.uuid4().hex
            directory = parent / (".report_comparison_" + nonce)
            directory.mkdir(exist_ok=False)
            info = directory.lstat()
            self._job_comparison_cache = {
                "directory": directory, "nonce": nonce, "directory_identity": (info.st_dev, info.st_ino),
                "expected_sha256": None,
            }
        except Exception as exc:
            LOGGER.warning("比較暫存無法建立，沿用原計算路徑：%s", type(exc).__name__)

    def _comparison_child_environment(self, environment: dict[str, str], mode: str) -> dict[str, str]:
        child = {key: value for key, value in environment.items()
                 if not key.casefold().startswith(REPORT_COMPARISON_CACHE_PREFIX.casefold())}
        context = getattr(self, "_job_comparison_cache", None)
        if not isinstance(context, dict) or not context or mode not in {MODE_H63, MODE_H1}:
            return child
        if mode == MODE_H1 and not context.get("expected_sha256"):
            return child  # no handoff until H63 was validated and delivered
        if (not isinstance(context.get("nonce"), str)
                or not re.fullmatch(r"[0-9a-f]{32}", context["nonce"])
                or not isinstance(context.get("directory"), Path)
                or (mode == MODE_H1 and (
                    not isinstance(context.get("expected_sha256"), str)
                    or not SHA256_HEX_PATTERN.fullmatch(context["expected_sha256"])))):
            return child
        child.update({
            REPORT_COMPARISON_CACHE_PREFIX + "DIR": str(context["directory"]),
            REPORT_COMPARISON_CACHE_PREFIX + "NONCE": context["nonce"],
            REPORT_COMPARISON_CACHE_PREFIX + "PHASE": "write" if mode == MODE_H63 else "read",
        })
        if mode == MODE_H1:
            child[REPORT_COMPARISON_CACHE_PREFIX + "READ_SHA256"] = context["expected_sha256"]
        return child

    def _promote_comparison_cache(self, report: GeneratedReport) -> None:
        context = getattr(self, "_job_comparison_cache", None)
        if not context or report.mode != MODE_H63:
            return
        context["expected_sha256"] = None
        if report.json_path is None or not SHA256_HEX_PATTERN.fullmatch(report.json_sha256):
            return
        try:
            raw = report.json_path.read_bytes()
            if hashlib.sha256(raw).hexdigest() != report.json_sha256:
                return
            document = json.loads(raw.decode("utf-8-sig"))
            if not isinstance(document, dict):
                return
            payload = _selected_report_payload(document)
            if payload is None:
                payload = document
            context["expected_sha256"] = self._comparison_bridge().cache_handoff_digest(
                context["directory"], context["nonce"], payload)
        except Exception as exc:
            LOGGER.warning("比較暫存未交接，後續期限沿用原計算路徑：%s", type(exc).__name__)

    def _finish_comparison_cache(self) -> None:
        context = getattr(self, "_job_comparison_cache", None)
        if context is None:
            return
        self._job_comparison_cache = None  # no environment may escape into the next job
        try:
            directory = context["directory"]
            info = directory.lstat()
            if ((info.st_dev, info.st_ino) != context["directory_identity"]
                    or not stat.S_ISDIR(info.st_mode) or directory.is_symlink()
                    or getattr(info, "st_file_attributes", 0) & getattr(stat, "FILE_ATTRIBUTE_REPARSE_POINT", 0x400)):
                LOGGER.warning("比較暫存目錄身分已變，保留目錄不清理")
                return
            # Only these exact two child-owned names are ever removed. Never enumerate
            # another job or recursively delete a directory containing unknown files.
            for name in ("comparison_block.json", "comparison_block.json.tmp"):
                path = directory / name
                try:
                    file_info = path.lstat()
                except FileNotFoundError:
                    continue
                if (stat.S_ISREG(file_info.st_mode)
                        and not getattr(file_info, "st_file_attributes", 0) & getattr(stat, "FILE_ATTRIBUTE_REPARSE_POINT", 0x400)):
                    path.unlink()
            directory.rmdir()  # fails safely if an unexpected file remains
        except FileNotFoundError:
            pass
        except Exception as exc:
            LOGGER.warning("比較暫存精確清理未完成，保留原工作結果：%s", type(exc).__name__)

    def process_job(self, job_id: str) -> None:
        job = self.store.get_job(job_id)
        if not job or job.get("status") != "queued":
            return
        self.store.update_job(job_id, status="running", startedAt=now_iso())
        query = str(job["query"])
        mode = str(job["mode"])
        if mode not in MODE_LABELS:
            self.store.update_job(
                job_id, status="failed", completedAt=now_iso(),
                error="工作期限已退役，請使用 H1／H63／both 重新送出指令",
            )
            return
        LOGGER.info("開始個股工作：%s query=%s mode=%s", job_id, query, mode)
        start_text = f"開始處理｜{query}\n類型：{MODE_LABELS[mode]}\n工作編號：{job_id}"
        start_response = self.client.send_message(start_text)
        progress_message_id = self._telegram_message_id(start_response)
        last_progress_attempt: float | None = None
        last_progress_text = ""
        terminal_progress_attempted = False
        active_analysis_mode = ""
        last_preparation_elapsed: int | None = None
        last_model_elapsed: int | None = None

        def publish_progress(snapshot: dict[str, Any]) -> None:
            nonlocal last_progress_attempt, last_progress_text, terminal_progress_attempted
            nonlocal active_analysis_mode, last_preparation_elapsed, last_model_elapsed
            phase = snapshot.get("phase")
            analysis_mode = snapshot.get("mode", "")
            if not isinstance(phase, str) or phase not in SINGLE_STOCK_PHASE_LABELS:
                return
            if not isinstance(analysis_mode, str):
                analysis_mode = ""
            if phase in SINGLE_STOCK_PHASE_RANK and analysis_mode != active_analysis_mode:
                active_analysis_mode = analysis_mode
                last_preparation_elapsed = None
                last_model_elapsed = None
            completed = snapshot.get("completed")
            total = snapshot.get("total")
            if type(completed) is not int or completed < 0:
                completed = None
            if type(total) is not int or total <= 0:
                total = None
            if completed is None or total is None or completed > total:
                completed = total = None
            preparation_elapsed = snapshot.get("preparation_elapsed_seconds")
            model_elapsed = snapshot.get("model_elapsed_seconds")
            if type(preparation_elapsed) is int and preparation_elapsed >= 0:
                last_preparation_elapsed = preparation_elapsed
            if type(model_elapsed) is int and model_elapsed >= 0:
                last_model_elapsed = model_elapsed
            preparation_elapsed = last_preparation_elapsed
            model_elapsed = last_model_elapsed
            fields: dict[str, Any] = {
                "progressPhase": phase,
                "progressMode": analysis_mode,
                "progressCompleted": completed,
                "progressTotal": total,
                "preparationElapsedSeconds": preparation_elapsed,
                "modelElapsedSeconds": model_elapsed,
                "progressUpdatedAt": now_iso(),
            }
            try:
                self.store.update_job(job_id, **fields)
            except Exception as exc:
                LOGGER.warning("個股進度狀態寫入失敗：%s", type(exc).__name__)
            lines = [start_text, f"階段：{SINGLE_STOCK_PHASE_LABELS[phase]}"]
            mode_label = MODE_LABELS.get(analysis_mode)
            if mode_label:
                lines.append(f"報告：{mode_label}")
            if completed is not None and total is not None:
                lines.append(f"已完成項目：{completed}/{total}")
            if preparation_elapsed is not None:
                lines.append(f"資料準備時間：{preparation_elapsed // 60} 分鐘")
            if model_elapsed is not None:
                lines.append(f"模型分析時間：{model_elapsed // 60} 分鐘")
            progress_text = "\n".join(lines)[:4096]
            now = time.monotonic()
            terminal = phase in {"complete", "failed"}
            if progress_message_id is None or progress_text == last_progress_text:
                return
            if terminal and terminal_progress_attempted:
                return
            if (
                not terminal
                and last_progress_attempt is not None
                and now - last_progress_attempt < SINGLE_STOCK_PHASE_INTERVAL_SECONDS
            ):
                return
            last_progress_attempt = now
            if terminal:
                terminal_progress_attempted = True
            last_progress_text = progress_text
            self._edit_progress_message(progress_message_id, progress_text)

        report_already_sent = False
        try:
            self._close_source_binding(getattr(self, "_job_c0_binding", None))
            self._job_c0_binding = None
            self._job_analysis_env = None
            self._verified_selected_reports = {}
            self._job_read_at = None
            self._job_version_labels = []
            self.select_job_source()
            source_fields = {
                "reportClass": "C0", "c0PublishedModelBinding": self._job_c0_binding,
                "c0JobReadAt": self._job_read_at,
                "c0JobDataTime": c0_binding_data_time(self._job_c0_binding),
            }
            if _is_selected_binding(self._job_c0_binding):
                source_fields["reportClass"] = SELECTED_SINGLE_REPORT_SCHEMA
                source_fields["selectedPublishedModelBinding"] = source_fields.pop("c0PublishedModelBinding")
            self.store.update_job(job_id, **source_fields)

            matches = self.resolve_stock(query)
            selected = choose_stock_match(query, matches)
            if selected is None:
                self.store.update_job(job_id, status="needs_input", completedAt=now_iso(), matches=[item.__dict__ for item in matches])
                LOGGER.info("個股工作需要重新選擇代碼：%s matches=%d", job_id, len(matches))
                self.client.send_message(ambiguity_message(query, matches))
                return
            LOGGER.info("股票解析完成：%s -> %s %s", job_id, selected.code, selected.name)
            self.store.update_job(
                job_id,
                resolvedStock={"code": selected.code, "name": selected.name, "matchType": selected.match_type},
            )
            modes = (
                [MODE_H63, MODE_H1]
                if mode == MODE_BOTH
                else [mode]
            )
            if mode == MODE_BOTH:
                self._begin_comparison_cache(job_id)
            for mode_index, analysis_mode in enumerate(modes):

                try:
                    report = self.run_analysis_mode(
                        job_id, selected, analysis_mode, publish_progress
                    )
                    checked = self.validate_report_before_upload(job_id, selected, report)
                    if isinstance(checked, GeneratedReport):
                        report = checked
                    if self._job_version_labels:
                        self.store.update_job(job_id, versionLabels=list(self._job_version_labels))

                except (AnalysisProcessError, TelegramApiError):
                    unsent_modes = list(modes[mode_index:])
                    LOGGER.error(
                        "個股工作模式未送出：job=%s mode=%s unsent_modes=%s",
                        job_id,
                        analysis_mode,
                        unsent_modes,
                    )
                    self.store.update_job(job_id, unsentModes=unsent_modes)
                    raise
                publish_progress({"phase": "delivery", "mode": analysis_mode})
                self.client.send_chat_action("upload_document")
                document_result = self.client.send_document(report.pdf_path, report.caption)
                report_already_sent = True
                self._record_delivery_evidence(
                    job_id,
                    selected.code,
                    analysis_mode,
                    "document",
                    document_result,
                    path=report.pdf_path,
                    json_path=report.json_path,
                    report=report,
                )
                self.store.append_report(job_id, report)
                LOGGER.info("PDF 已回傳：%s mode=%s file=%s", job_id, analysis_mode, report.pdf_path.name)
                if analysis_mode == MODE_H63:
                    self._promote_comparison_cache(report)
            self.store.update_job(
                job_id,
                status="complete",
                completedAt=now_iso(),
                processId=None,
                currentMode="",
            )
            publish_progress({"phase": "complete", "mode": mode})
            LOGGER.info("個股工作完成：%s reports=%d", job_id, len(modes))
        except AnalysisProcessError as exc:
            self.store.update_job(
                job_id,
                status="failed",
                completedAt=now_iso(),
                error=getattr(exc, "analysis_error_summary", redact_sensitive_text(
                    str(exc), getattr(self, "analysis_secret_values", ()),
                )[:ANALYSIS_ERROR_TAIL_MAX_CHARS]),
                processId=None,
                currentMode="",
            )
            publish_progress({"phase": "failed", "mode": mode})
            LOGGER.error("個股分析失敗：%s error=%s", job_id, exc)
            self.client.send_message(_analysis_failure_message(
                query, job_id, exc, report_already_sent=report_already_sent,
            ))
        except TelegramApiError as exc:
            self.store.update_job(
                job_id,
                status="failed",
                completedAt=now_iso(),
                error="Telegram 回傳失敗",
                processId=None,
                currentMode="",
            )
            publish_progress({"phase": "failed", "mode": mode})
            LOGGER.error("報告已產生但 Telegram 回傳失敗：%s：%s", job_id, exc)
        finally:
            try:
                self._finish_comparison_cache()
            finally:
                self._close_source_binding(getattr(self, "_job_c0_binding", None))
                self._job_c0_binding = None

    @staticmethod
    def _telegram_message_id(response: Any) -> int | None:
        if not isinstance(response, dict):
            return None
        result = response.get("result")
        payload = result if isinstance(result, dict) else response
        message_id = payload.get("message_id") if isinstance(payload, dict) else None
        return message_id if type(message_id) is int and message_id > 0 else None

    def _edit_progress_message(self, message_id: int, text: str) -> None:
        client = getattr(self, "progress_client", None)
        if client is None:
            return
        try:
            client._post_fields(
                "editMessageText",
                {"chat_id": self.chat_id, "message_id": str(message_id), "text": text[:4096]},
            )
        except Exception as exc:
            LOGGER.warning("個股進度訊息更新失敗：%s", type(exc).__name__)

    def resolve_stock(self, query: str) -> list[StockMatch]:
        self.refresh_analysis_environment_for_mode("", "resolver")
        command = build_resolver_command(
            query, self._analysis_python_for_child(),
            c0_binding_head_sha256(self._job_c0_binding) if _is_selected_binding(self._job_c0_binding) else None,
        )

        try:
            result = subprocess.run(
                command,
                cwd=STOCK_ROOT,
                env=self.analysis_env,
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,
                text=True,
                encoding="utf-8",
                errors="replace",
                timeout=self.resolve_timeout_seconds,
                creationflags=subprocess_creation_flags(),
                shell=False,
                check=False,
            )
        except subprocess.TimeoutExpired as exc:
            raise AnalysisProcessError("股票名稱解析逾時") from exc
        if result.returncode != 0:
            safe_output = redact_sensitive_text(
                str(result.stdout or "")[-ANALYSIS_ERROR_TAIL_MAX_CHARS:],
                getattr(self, "analysis_secret_values", ()),
            )
            LOGGER.error("股票解析失敗 rc=%d output=%s", result.returncode, safe_output)
            raise AnalysisProcessError("股票代碼或名稱解析失敗")
        return parse_resolver_output(result.stdout)

    def run_analysis_mode(
        self,
        job_id: str,
        stock: StockMatch,
        mode: str,
        progress_callback: Callable[[dict[str, Any]], None] | None = None,
    ) -> GeneratedReport:
        stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        output_dir = build_analysis_output_dir(job_id, mode, stamp)
        job_directory = output_dir.parent
        try:
            job_directory.mkdir(parents=True, exist_ok=False)
            job_identity = _path_identity(job_directory.lstat())
        except FileExistsError:
            job_identity = None
        output_identity = None
        try:
            output_dir.mkdir(exist_ok=False)
            output_identity = _path_identity(output_dir.lstat())
            with _owned_analysis_log(output_dir) as (log_path, log_file):
                try:
                    return self._run_analysis_in_output(
                        job_id, stock, mode, output_dir, log_path, log_file, progress_callback,
                    )
                except BaseException as exc:
                    secrets = getattr(self, "analysis_secret_values", ())
                    tail = read_safe_log_tail(log_path, secrets)
                    summary = redact_sensitive_text(str(exc), secrets)
                    if tail and tail not in summary:
                        summary += "\n" + tail
                    if len(summary) > ANALYSIS_ERROR_TAIL_MAX_CHARS:
                        summary = summary[:256] + "\n" + summary[-(ANALYSIS_ERROR_TAIL_MAX_CHARS - 257):]
                    exc.analysis_error_summary = summary
                    try:
                        self.store.update_job(job_id, error=exc.analysis_error_summary, analysisLog=None)
                    except BaseException as state_exc:
                        self._attach_cleanup_error(exc, "analysis error state", state_exc)
                    raise
        finally:
            for directory, identity in ((output_dir, output_identity), (job_directory, job_identity)):
                if identity is None:
                    continue
                try:
                    _require_owned_directory(directory, identity)
                    directory.rmdir()  # Only this run's empty directories may disappear.
                except FileNotFoundError:
                    pass
                except OSError as exc:
                    if exc.errno not in (errno.ENOTEMPTY, errno.EEXIST):
                        LOGGER.warning("分析空目錄未清理：%s", type(exc).__name__)
                except AnalysisProcessError:
                    LOGGER.warning("分析輸出目錄身分已變，保留不清理")

    def _run_analysis_in_output(
        self, job_id: str, stock: StockMatch, mode: str, output_dir: Path,
        log_path: Path, log_file: TextIO,
        progress_callback: Callable[[dict[str, Any]], None] | None,
    ) -> GeneratedReport:
        self.store.update_job(
            job_id,
            currentMode=mode,
            processId=None,
            workDir=str(output_dir),
            analysisLog=None,
        )
        started = time.monotonic()
        overall_timeout = self.analysis_timeout_seconds
        overall_deadline = started + overall_timeout
        preparation_timeout = min(
            overall_timeout,
            getattr(self, "preparation_timeout_seconds", min(45 * 60, overall_timeout)),
        )
        phase_reader = SingleStockPhaseReader(stock.code)
        current_phase = ""
        current_progress: SingleStockPhase | None = None
        preparation_started: float | None = None
        model_started: float | None = None
        model_ended: float | None = None
        preparation_deadline: float | None = None
        model_deadline: float | None = None
        last_progress_notification: float | None = None

        def notify_progress(now: float, force: bool = False) -> None:
            nonlocal last_progress_notification
            if progress_callback is None or current_progress is None:
                return
            if not force and last_progress_notification is not None and (
                now - last_progress_notification < SINGLE_STOCK_PHASE_INTERVAL_SECONDS
            ):
                return
            preparation_elapsed: int | None = None
            if preparation_started is not None:
                preparation_end = model_started if model_started is not None else now
                preparation_elapsed = max(0, int(preparation_end - preparation_started))
            model_end = model_ended if model_ended is not None else now
            model_elapsed = max(0, int(model_end - model_started)) if model_started is not None else None
            snapshot = {
                "phase": current_phase,
                "mode": mode,
                "completed": current_progress.completed,
                "total": current_progress.total,
                "preparation_elapsed_seconds": preparation_elapsed,
                "model_elapsed_seconds": model_elapsed,
            }
            try:
                progress_callback(snapshot)
            except Exception as exc:
                LOGGER.warning("個股進度回呼失敗：%s", type(exc).__name__)
            last_progress_notification = now

        def accept_phase_events(events: list[SingleStockPhase]) -> None:
            nonlocal current_phase, current_progress, preparation_started, model_started, model_ended
            nonlocal preparation_deadline, model_deadline
            for event in events:
                changed = event.phase != current_phase
                current_phase = event.phase
                current_progress = event
                now = time.monotonic()
                if preparation_started is None:
                    preparation_started = started
                    preparation_deadline = min(overall_deadline, started + preparation_timeout)
                if event.phase == "model" and model_started is None:
                    model_started = now
                    model_deadline = min(overall_deadline, now + overall_timeout)
                if event.phase == "pdf" and model_started is not None and model_ended is None:
                    model_ended = now
                if changed:
                    notify_progress(now, force=True)

        process: subprocess.Popen[Any] | None = None
        primary_error: BaseException | None = None
        cleanup_errors: list[BaseException] = []
        with log_file:
            try:
                self.refresh_analysis_environment_for_mode(job_id, mode)
                child_environment = self._comparison_child_environment(self.analysis_env, mode)
                child_environment[SINGLE_STOCK_PHASE_ENV] = "1"
                command = build_analysis_command(
                    mode, stock.code, output_dir, self._analysis_python_for_child(),
                    c0_binding_head_sha256(self._job_c0_binding),
                )

                process = subprocess.Popen(
                    command,
                    cwd=STOCK_ROOT,
                    env=child_environment,
                    stdout=log_file,
                    stderr=subprocess.STDOUT,
                    creationflags=subprocess_creation_flags(),
                    shell=False,
                )
                with self.current_process_lock:
                    self.current_process = process
                # The heartbeat can fail immediately after Popen(); keep this
                # write inside the cleanup envelope so the child cannot leak.
                self.write_heartbeat(
                    status="working",
                    currentJobId=job_id,
                    currentProcessId=process.pid,
                    currentMode=mode,
                )
                self.store.update_job(job_id, processId=process.pid)
                while process.poll() is None:
                    accept_phase_events(phase_reader.read_available(log_path))
                    now = time.monotonic()
                    if now > overall_deadline:
                        process.terminate()
                        try:
                            process.wait(timeout=ANALYSIS_CLEANUP_TIMEOUT_SECONDS)
                        except subprocess.TimeoutExpired:
                            process.kill()
                        raise AnalysisProcessError(f"{MODE_LABELS[mode]}超過允許時間，已停止")
                    if (
                        current_phase
                        and model_started is None
                        and preparation_deadline is not None
                        and now > preparation_deadline
                    ):
                        process.terminate()
                        try:
                            process.wait(timeout=ANALYSIS_CLEANUP_TIMEOUT_SECONDS)
                        except subprocess.TimeoutExpired:
                            process.kill()
                        raise AnalysisProcessError("資料檢查或資料準備超過允許時間，已停止")
                    if model_deadline is not None and now > model_deadline:
                        process.terminate()
                        try:
                            process.wait(timeout=ANALYSIS_CLEANUP_TIMEOUT_SECONDS)
                        except subprocess.TimeoutExpired:
                            process.kill()
                        raise AnalysisProcessError(f"{MODE_LABELS[mode]}模型階段超過允許時間，已停止")
                    if self.stop_event.is_set():
                        process.terminate()
                        raise AnalysisProcessError("指令服務正在停止")
                    notify_progress(now)
                    time.sleep(2)
                accept_phase_events(phase_reader.read_available(log_path))
                notify_progress(time.monotonic())
            except BaseException as exc:
                primary_error = exc
            finally:
                cleanup_errors.extend(self._terminate_analysis_process(process))
                if process is not None:
                    with self.current_process_lock:
                        if self.current_process is process:
                            self.current_process = None
                    try:
                        self.write_heartbeat(currentProcessId=None, currentMode="", currentJobId=job_id)
                    except BaseException as heartbeat_exc:
                        self.stop_event.set()
                        primary_error = self._promote_heartbeat_failure(
                            primary_error,
                            heartbeat_exc,
                            "final heartbeat cleanup",
                        )
                if primary_error is not None:
                    for cleanup_error in cleanup_errors:
                        self._attach_cleanup_error(primary_error, "analysis child cleanup", cleanup_error)
                elif cleanup_errors:
                    primary_error = RuntimeError("analysis child cleanup failed")
                    for cleanup_error in cleanup_errors:
                        self._attach_cleanup_error(primary_error, "analysis child cleanup", cleanup_error)
        if primary_error is not None:
            raise primary_error
        if process is None:
            raise AnalysisProcessError("分析行程未啟動")
        if process.returncode != 0:
            tail = read_safe_log_tail(log_path, getattr(self, "analysis_secret_values", ()))
            safe_tail = tail or "分析程式未提供可用錯誤訊息"
            LOGGER.error("分析行程失敗 job=%s mode=%s rc=%s tail=%s", job_id, mode, process.returncode, safe_tail)
            raise AnalysisProcessError(f"{MODE_LABELS[mode]}執行失敗；末段訊息：{safe_tail}")

        pdf_path, json_path = _declared_report_paths(log_path, output_dir)
        self.refresh_analysis_environment_for_mode(job_id, mode)
        labels: list[dict[str, Any]] = []
        json_path, payload, pdf_sha256, json_sha256 = _validate_generated_report(
            mode, stock, output_dir, pdf_path, json_path, self._job_c0_binding, labels,
        )
        self._note_job_labels(labels)
        report = GeneratedReport(
            mode, pdf_path, json_path, report_caption(mode, stock, payload, self._job_caption_context()),
            pdf_sha256, json_sha256, output_dir, (pdf_path, json_path),
        )
        if _is_selected_binding(self._job_c0_binding):
            cache = getattr(self, "_verified_selected_reports", None)
            if not isinstance(cache, dict):
                cache = {}
                self._verified_selected_reports = cache
            cache[self._selected_report_cache_key(stock, report)] = (
                CommandStateStore._copy(self._job_c0_binding), payload,
            )
        return report

    def stop(self) -> None:
        self.stop_event.set()
        primary_error: BaseException | None = None
        try:
            self.write_heartbeat(status="stopping")
        except BaseException as exc:
            primary_error = exc
            self.stop_event.set()
        try:
            cleanup_errors = self._cleanup_runtime()
        except BaseException as cleanup_exc:
            cleanup_errors = [cleanup_exc]
        if primary_error is not None:
            for cleanup_error in cleanup_errors:
                self._attach_cleanup_error(primary_error, "stop cleanup", cleanup_error)
            raise primary_error
        if cleanup_errors:
            cleanup_error = RuntimeError("Telegram stop cleanup failed")
            for error in cleanup_errors:
                self._attach_cleanup_error(cleanup_error, "stop cleanup", error)
            raise cleanup_error


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Telegram 股票代碼／名稱個股分析指令服務")
    parser.add_argument("--check", action="store_true", help="只驗證設定、Telegram 與分析入口")
    parser.add_argument("--self-check", action="store_true", help="不讀 Telegram queue，驗證 resident 與 2303 resolver")
    parser.add_argument("--bootstrap-only", action="store_true", help="初始化 update offset 與 Bot 指令後離開")
    return parser


def main() -> int:
    setup_logging()
    args = build_parser().parse_args()
    env_values = load_env_file(LOCAL_ENV_PATH)
    if str(env_values.get("TELEGRAM_STOCK_COMMANDS_ENABLED", "")).strip().lower() not in TRUE_VALUES:
        LOGGER.info("Telegram 個股指令目前停用")
        return 0
    bot: TelegramStockCommandBot | None = None
    try:
        if args.self_check:
            run_non_queue_self_check()
            return 0
        bot = TelegramStockCommandBot(env_values)
        if args.check:
            bot.verify()
            LOGGER.info("Telegram 個股指令設定有效；兩個分析入口均存在")
            return 0
        if args.bootstrap_only:
            bot.bootstrap()
            LOGGER.info("Telegram 個股指令初始化完成")
            return 0
        with SingleInstanceLock(LOCK_PATH, OWNER_PATH):
            bot.run_forever()
        return 0
    except (ConfigurationError, CommandBotError, TelegramApiError) as exc:
        LOGGER.error("%s", exc)
        if bot is not None and not args.check and not args.bootstrap_only and not args.self_check:
            bot.record_fatal_error(exc)
        return 2
    except KeyboardInterrupt:
        LOGGER.info("使用者停止 Telegram 個股指令服務")
        if bot is not None:
            bot.stop()
        return 0
    except Exception as exc:
        LOGGER.exception("Telegram 個股指令服務未預期終止")
        if bot is not None and not args.check and not args.bootstrap_only:
            bot.record_fatal_error(exc)
        return 3


if __name__ == "__main__":
    raise SystemExit(main())
