from __future__ import annotations

import argparse
import getpass
import json
import logging
import mimetypes
import os
import re
import sys
import time
import uuid
from datetime import datetime
from pathlib import Path
from typing import Any
from urllib.error import HTTPError, URLError
from urllib.parse import quote, urlencode
from urllib.request import Request, urlopen


SCRIPT_DIR = Path(__file__).resolve().parent
SHARED_DIR = SCRIPT_DIR.parent / "shared"
if str(SHARED_DIR) not in sys.path:
    sys.path.insert(0, str(SHARED_DIR))

import date_format as cd  # noqa: E402  (日期：顯示一律 YYYYMMDD，讀取兩種拼寫都收)

PATH_CONTRACT_PATH = Path(r"D:\TWStockData\config\path_contract.json")
# 已知版本不警告；主版本 1.x／2.x 的其他 X.Y.Z 版本照常載入並記 warning；
# 其他主版本或格式錯誤仍拒絕。與 shared/path_contract.py、
# CHECK_COMMAND_BOT_STATUS.ps1、INSTALL_COMMAND_BOT_SERVICE.ps1 同步。
SUPPORTED_CONTRACT_VERSIONS = ("1.1.0", "2.0.0")
SUPPORTED_CONTRACT_MAJOR_VERSIONS = (1, 2)
CONTRACT_VERSION_PATTERN = re.compile(r"^(\d+)\.(\d+)\.(\d+)$")
EXPECTED_CONTRACT_ROOT = Path(r"D:\TWStockData")
_WARNED_CONTRACT_VERSIONS: set[str] = set()


def path_contract_version_labels(version: object, path: Path = PATH_CONTRACT_PATH) -> list[dict[str, Any]]:
    """Return version labels; raise only for an unparsable or unsupported major version."""
    text = version.strip() if isinstance(version, str) else ""
    if text in SUPPORTED_CONTRACT_VERSIONS:
        return []
    match = CONTRACT_VERSION_PATTERN.fullmatch(text)
    if match is None or int(match.group(1)) not in SUPPORTED_CONTRACT_MAJOR_VERSIONS:
        majors = " 或 ".join(f"{major}.x" for major in SUPPORTED_CONTRACT_MAJOR_VERSIONS)
        raise RuntimeError(
            f"路徑契約版本必須是 {majors}（已知版本 {' 或 '.join(SUPPORTED_CONTRACT_VERSIONS)}）：{path}"
        )
    return [{
        "code": "path_contract_version_unknown",
        "recorded": list(SUPPORTED_CONTRACT_VERSIONS),
        "actual": text,
        "detail": "主版本受支援，照常載入；root 與 managed_paths 檢查仍適用",
    }]


def load_path_contract(path: Path = PATH_CONTRACT_PATH) -> dict[str, Any]:
    try:
        payload = json.loads(path.read_text(encoding="utf-8-sig"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise RuntimeError(f"無法讀取路徑契約：{path}") from exc
    if not isinstance(payload, dict) or not isinstance(payload.get("managed_paths"), dict):
        raise RuntimeError(f"路徑契約格式錯誤：{path}")
    if path_contract_version_labels(payload.get("contract_version"), path):
        version_text = str(payload.get("contract_version"))
        if version_text not in _WARNED_CONTRACT_VERSIONS:
            _WARNED_CONTRACT_VERSIONS.add(version_text)
            logging.getLogger("telegram_report_sender").warning(
                "路徑契約版本 %s 不是已知版本（%s），依主版本照常載入：%s",
                version_text, " / ".join(SUPPORTED_CONTRACT_VERSIONS), path,
            )
    root_value = payload.get("root")
    if not isinstance(root_value, str) or not root_value.strip():
        raise RuntimeError(f"路徑契約根目錄缺失：{path}")
    if Path(root_value).resolve(strict=False) != EXPECTED_CONTRACT_ROOT.resolve(strict=False):
        raise RuntimeError(f"路徑契約根目錄必須是 {EXPECTED_CONTRACT_ROOT}：{path}")
    return payload


def get_managed_path(name: str) -> Path:
    managed_paths = load_path_contract().get("managed_paths", {})
    value = managed_paths.get(name)
    if not isinstance(value, str) or not value.strip():
        raise RuntimeError(f"路徑契約缺少 managed_paths.{name}")
    return Path(value).resolve(strict=False)


TELEGRAM_ROOT = get_managed_path("telegram")
DEFAULT_ENV_PATH = SCRIPT_DIR / ".ENV"
DEFAULT_LOG_DIR = TELEGRAM_ROOT / "logs"
MAX_TELEGRAM_DOCUMENT_BYTES = 49 * 1024 * 1024
TRUE_VALUES = {"1", "true", "yes", "on", "enabled"}
TOKEN_PATTERN = re.compile(r"^\d+:[A-Za-z0-9_-]{20,}$")
CHAT_ID_PATTERN = re.compile(r"^-?\d+$")
LOGGER = logging.getLogger("telegram_report_sender")


class ConfigurationError(RuntimeError):
    pass


class TelegramApiError(RuntimeError):
    def __init__(self, description: str, error_code: int | None = None) -> None:
        super().__init__(description)
        self.error_code = error_code


def _require_path_under(value: Path | str, root: Path, label: str) -> Path:
    candidate = Path(value).expanduser().resolve(strict=False)
    expected_root = Path(root).expanduser().resolve(strict=False)
    if not candidate.is_relative_to(expected_root):
        raise ConfigurationError(
            f"{label} 必須位於路徑契約根目錄 {expected_root}：{candidate}"
        )
    return candidate


def validate_cli_paths(args: argparse.Namespace) -> None:
    args.log_dir = _require_path_under(args.log_dir, TELEGRAM_ROOT, "Telegram 設定紀錄")


def setup_logging(log_dir: Path, *extra_loggers: logging.Logger) -> None:
    """Configure this module's logger, plus callers' own loggers, on the same stdout and file handlers."""
    log_dir.mkdir(parents=True, exist_ok=True)
    log_path = log_dir / f"telegram_delivery_{datetime.now():%Y%m}.log"
    formatter = logging.Formatter("%(asctime)s %(levelname)s %(message)s", "%Y%m%d %H:%M:%S")
    stream_handler = logging.StreamHandler(sys.stdout)
    stream_handler.setFormatter(formatter)
    file_handler = logging.FileHandler(log_path, encoding="utf-8")
    file_handler.setFormatter(formatter)
    for logger in dict.fromkeys((LOGGER, *extra_loggers)):
        logger.handlers.clear()
        logger.setLevel(logging.INFO)
        logger.addHandler(stream_handler)
        logger.addHandler(file_handler)
        logger.propagate = False


def load_env_file(path: Path) -> dict[str, str]:
    if not path.exists():
        return {}
    values: dict[str, str] = {}
    for raw_line in path.read_text(encoding="utf-8-sig").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#"):
            continue
        if line.lower().startswith("export "):
            line = line[7:].lstrip()
        if "=" not in line:
            continue
        key, value = line.split("=", 1)
        key = key.strip()
        value = value.strip()
        if not re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]*", key):
            continue
        if len(value) >= 2 and value[0] == value[-1] and value[0] in {"'", '"'}:
            value = value[1:-1]
        values[key] = value
    return values


def setting(env_file: dict[str, str], key: str) -> str:
    return str(os.environ.get(key, env_file.get(key, ""))).strip()


def update_env_file(path: Path, updates: dict[str, str]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    original = path.read_text(encoding="utf-8-sig") if path.exists() else ""
    lines = original.splitlines()
    emitted: set[str] = set()
    output: list[str] = []
    pattern = re.compile(r"^\s*(?:export\s+)?([A-Za-z_][A-Za-z0-9_]*)\s*=")
    for line in lines:
        match = pattern.match(line)
        key = match.group(1) if match else ""
        if key not in updates:
            output.append(line)
            continue
        if key in emitted:
            continue
        output.append(f"{key}={updates[key]}")
        emitted.add(key)
    missing = [key for key in updates if key not in emitted]
    if missing:
        if output and output[-1].strip():
            output.append("")
        output.append("# Telegram report delivery")
        output.extend(f"{key}={updates[key]}" for key in missing)
    content = "\n".join(output).rstrip() + "\n"
    temp_path = path.with_name(f"{path.name}.{os.getpid()}.tmp")
    temp_path.write_text(content, encoding="utf-8", newline="\n")
    os.replace(temp_path, path)


def validate_credentials(token: str, chat_id: str) -> None:
    if not TOKEN_PATTERN.fullmatch(token):
        raise ConfigurationError("TELEGRAM_BOT_TOKEN 缺少或格式不正確")
    if not CHAT_ID_PATTERN.fullmatch(chat_id):
        raise ConfigurationError("TELEGRAM_CHAT_ID 缺少或格式不正確")


def _manifest_day(value: Any) -> Any:
    """Manifest date field (YYYYMMDD, or YYYY-MM-DD) -> ``date``; None for JSON numbers and other values."""
    return cd.to_day(value.strip() if isinstance(value, str) else value)


def _format_yyyymmdd(value: Any) -> str:
    """Display: YYYYMMDD (either spelling in); other text as is; empty -> NA.

    Until 2026-10-06 this converted 8-digit dates TO YYYY-MM-DD for display; dates are now always shown
    as YYYYMMDD (user directive)."""
    text = value.strip() if isinstance(value, str) else value
    return cd.display_day(text or None, empty="NA")


def build_multipart_form(
    fields: dict[str, str],
    file_field: str,
    file_path: Path,
) -> tuple[bytes, str]:
    boundary = f"----StockReport{uuid.uuid4().hex}"
    chunks: list[bytes] = []

    def append(text: str) -> None:
        chunks.append(text.encode("utf-8"))

    for name, value in fields.items():
        append(f"--{boundary}\r\n")
        append(f'Content-Disposition: form-data; name="{name}"\r\n\r\n')
        append(str(value))
        append("\r\n")

    filename = file_path.name
    ascii_name = re.sub(r"[^A-Za-z0-9._-]", "_", filename) or "report"
    content_type = mimetypes.guess_type(filename)[0] or "application/octet-stream"
    append(f"--{boundary}\r\n")
    append(
        f'Content-Disposition: form-data; name="{file_field}"; filename="{ascii_name}"; '
        f"filename*=UTF-8''{quote(filename)}\r\n"
    )
    append(f"Content-Type: {content_type}\r\n\r\n")
    chunks.append(file_path.read_bytes())
    append("\r\n")
    append(f"--{boundary}--\r\n")
    return b"".join(chunks), f"multipart/form-data; boundary={boundary}"


class TelegramClient:
    def __init__(
        self,
        token: str,
        chat_id: str,
        timeout_seconds: float = 120.0,
        max_attempts: int = 4,
    ) -> None:
        self.token = token
        self.chat_id = chat_id
        self.timeout_seconds = timeout_seconds
        self.max_attempts = max(1, max_attempts)

    def _post(self, method: str, body: bytes, content_type: str) -> dict[str, Any]:
        endpoint = f"https://api.telegram.org/bot{self.token}/{method}"
        for attempt in range(1, self.max_attempts + 1):
            request = Request(
                endpoint,
                data=body,
                method="POST",
                headers={"Content-Type": content_type, "User-Agent": "D-stock-report-delivery/1.0"},
            )
            try:
                with urlopen(request, timeout=self.timeout_seconds) as response:
                    payload = json.loads(response.read().decode("utf-8"))
                if payload.get("ok"):
                    result = payload.get("result")
                    return result if isinstance(result, dict) else {"result": result}
                raise self._payload_error(payload)
            except HTTPError as exc:
                try:
                    payload = json.loads(exc.read().decode("utf-8", errors="replace"))
                except json.JSONDecodeError:
                    payload = {"error_code": exc.code, "description": "Telegram HTTP 錯誤"}
                error = self._payload_error(payload)
                retry_after = self._retry_after(payload, attempt)
                if self._is_transient(error.error_code) and attempt < self.max_attempts:
                    LOGGER.warning("Telegram 暫時失敗，%.0f 秒後重試（%d/%d）", retry_after, attempt, self.max_attempts)
                    time.sleep(retry_after)
                    continue
                raise error from None
            except (URLError, TimeoutError, OSError) as exc:
                if attempt < self.max_attempts:
                    delay = min(30.0, 2.0 ** (attempt - 1))
                    LOGGER.warning("Telegram 網路連線暫時失敗，%.0f 秒後重試（%d/%d）", delay, attempt, self.max_attempts)
                    time.sleep(delay)
                    continue
                raise TelegramApiError("Telegram 網路連線失敗；已達重試上限") from exc
        raise TelegramApiError("Telegram 傳送失敗")

    @staticmethod
    def _payload_error(payload: Any) -> TelegramApiError:
        if not isinstance(payload, dict):
            return TelegramApiError("Telegram API 回覆格式錯誤")
        code = payload.get("error_code")
        error_code = int(code) if isinstance(code, int | float | str) and str(code).isdigit() else None
        description = str(payload.get("description") or "Telegram API 拒絕請求")
        return TelegramApiError(description, error_code)

    @staticmethod
    def _retry_after(payload: Any, attempt: int) -> float:
        if isinstance(payload, dict):
            parameters = payload.get("parameters")
            if isinstance(parameters, dict):
                value = parameters.get("retry_after")
                try:
                    return max(1.0, min(300.0, float(value)))
                except (TypeError, ValueError):
                    pass
        return min(30.0, 2.0 ** (attempt - 1))

    @staticmethod
    def _is_transient(error_code: int | None) -> bool:
        return error_code == 429 or (error_code is not None and 500 <= error_code <= 599)

    def _post_fields(self, method: str, fields: dict[str, str]) -> dict[str, Any]:
        body = urlencode(fields).encode("utf-8")
        return self._post(method, body, "application/x-www-form-urlencoded; charset=utf-8")

    def get_me(self) -> dict[str, Any]:
        return self._post_fields("getMe", {})

    def get_chat(self) -> dict[str, Any]:
        return self._post_fields("getChat", {"chat_id": self.chat_id})

    def get_webhook_info(self) -> dict[str, Any]:
        return self._post_fields("getWebhookInfo", {})

    def get_updates(
        self,
        offset: int | None = None,
        timeout_seconds: int = 50,
        limit: int = 100,
    ) -> list[dict[str, Any]]:
        fields = {
            "timeout": str(max(0, min(50, timeout_seconds))),
            "limit": str(max(1, min(100, limit))),
            "allowed_updates": json.dumps(["message"], separators=(",", ":")),
        }
        if offset is not None:
            fields["offset"] = str(offset)
        payload = self._post_fields("getUpdates", fields)
        result = payload.get("result")
        if not isinstance(result, list):
            return []
        return [item for item in result if isinstance(item, dict)]

    def set_my_commands(self, commands: list[dict[str, str]]) -> bool:
        payload = self._post_fields(
            "setMyCommands",
            {"commands": json.dumps(commands, ensure_ascii=False, separators=(",", ":"))},
        )
        return bool(payload.get("result"))

    def send_chat_action(self, action: str) -> bool:
        payload = self._post_fields("sendChatAction", {"chat_id": self.chat_id, "action": action})
        return bool(payload.get("result"))

    def send_message(self, text: str) -> dict[str, Any]:
        return self._post_fields(
            "sendMessage",
            {"chat_id": self.chat_id, "text": text[:4096], "disable_notification": "false"},
        )

    def send_document(self, path: Path, caption: str) -> dict[str, Any]:
        body, content_type = build_multipart_form(
            {"chat_id": self.chat_id, "caption": caption[:1024], "disable_notification": "false"},
            "document",
            path,
        )
        return self._post("sendDocument", body, content_type)


def configure_delivery(env_path: Path) -> int:
    env_file = load_env_file(env_path)
    process_token = str(os.environ.get("TELEGRAM_BOT_TOKEN", "")).strip()
    process_chat_id = str(os.environ.get("TELEGRAM_CHAT_ID", "")).strip()
    current_token = env_file.get("TELEGRAM_BOT_TOKEN", "").strip()
    current_chat_id = env_file.get("TELEGRAM_CHAT_ID", "").strip()
    if process_token and process_chat_id:
        token = process_token
        chat_id = process_chat_id
    elif current_token and current_chat_id:
        token = current_token
        chat_id = current_chat_id
    else:
        entered_token = getpass.getpass("Telegram Bot Token（輸入不會顯示；直接 Enter 沿用既有值）：").strip()
        token = entered_token or current_token
        entered_chat_id = input("Telegram Chat ID（直接 Enter 沿用既有值）：").strip()
        chat_id = entered_chat_id or current_chat_id
    validate_credentials(token, chat_id)
    client = TelegramClient(token, chat_id)
    bot = client.get_me()
    chat = client.get_chat()
    update_env_file(
        env_path,
        {
            "TELEGRAM_DELIVERY_ENABLED": "true",
            "TELEGRAM_BOT_TOKEN": token,
            "TELEGRAM_CHAT_ID": chat_id,
        },
    )
    LOGGER.info(
        "Telegram 設定已驗證並寫入 %s：Bot=@%s，Chat 類型=%s",
        env_path,
        bot.get("username", "unknown"),
        chat.get("type", "unknown"),
    )
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Telegram 共用憑證設定工具；舊D-stock全市場報告CLI已退役")
    parser.add_argument("--env", type=Path, default=DEFAULT_ENV_PATH, help="環境設定檔")
    parser.add_argument("--log-dir", type=Path, default=DEFAULT_LOG_DIR, help="設定紀錄目錄")
    operation = parser.add_mutually_exclusive_group(required=True)
    operation.add_argument("--configure", action="store_true", help="互動設定並驗證 Bot Token 與 Chat ID")
    operation.add_argument("--check", action="store_true", help="檢查 Telegram 憑證，不傳送報告")
    return parser


def run(args: argparse.Namespace) -> int:
    setup_logging(args.log_dir)
    if args.configure:
        return configure_delivery(args.env)
    if not args.check:
        raise ConfigurationError("舊D-stock全市場報告CLI已退役，只接受 --configure 或 --check")
    env_file = load_env_file(args.env)
    token = setting(env_file, "TELEGRAM_BOT_TOKEN")
    chat_id = setting(env_file, "TELEGRAM_CHAT_ID")
    validate_credentials(token, chat_id)
    client = TelegramClient(token, chat_id)
    bot = client.get_me()
    chat = client.get_chat()
    LOGGER.info(
        "Telegram 憑證有效：Bot=@%s，Chat 類型=%s",
        bot.get("username", "unknown"), chat.get("type", "unknown"),
    )
    return 0


def main() -> int:
    args = build_parser().parse_args()
    try:
        validate_cli_paths(args)
        return run(args)
    except (ConfigurationError, TelegramApiError) as exc:
        if not LOGGER.handlers:
            print(f"[ERROR] {exc}", file=sys.stderr)
        else:
            LOGGER.error("FAILED: %s", exc)
        return 2
    except KeyboardInterrupt:
        LOGGER.warning("使用者取消設定")
        return 130


if __name__ == "__main__":
    raise SystemExit(main())
