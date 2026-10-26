# RETIRED TEST MODULE -- not collected (tests/conftest.py collect_ignore = ["_retired"]).
# Moved verbatim from tests/test_telegram_service_config.py on 2026-10-12; see tests/_retired/README.md
# for the removed module/feature each test exercised and the evidence that it was intentionally removed.
from __future__ import annotations

import hashlib
import json
import os
import subprocess
import xml.etree.ElementTree as ET
from pathlib import Path

import pytest


SUITE_DIR = Path(__file__).resolve().parents[1]
# 服務與排程定義寫的是正式安裝路徑；檔案內容仍從目前的套件目錄讀取。
FORMAL_SUITE_DIR = Path(r"D:\stock\programs\telegram_report_delivery")
SERVICE_DIR = SUITE_DIR / "service"
MIX_SCHEDULE_DEFINITION = SUITE_DIR / "MIX_WEEKDAY_0800_TASK_DEFINITION.json"
MIX_TASK_NAME = "MIX Telegram Report 08-00"
MIX_RUNNER_NAME = "RUN_SEND_MIX_REPORT.cmd"
MIX_INSTALLER_NAME = "INSTALL_MIX_WEEKDAY_0800_TASK.ps1"
MIX_PRODUCTION_POINTER = (
    r"D:\TWStockData\program_data\mix\reports\LATEST_PRODUCTION_RUN.json"
)
FORBIDDEN_0800_TOKENS = (
    "D-Stock",
    "RUN_SCHEDULED_SEND",
    "RUN_SEND_LATEST_REPORT",
)


def test_live_e2e_entrypoint_is_bounded_and_observational() -> None:
    harness = (SUITE_DIR / "telegram_command_bot_e2e.py").read_text(encoding="utf-8")
    wrapper = (SUITE_DIR / "RUN_TELEGRAM_COMMAND_BOT_E2E.cmd").read_text(encoding="utf-8")
    assert "--timeout-seconds" in harness
    assert "update_id" in harness
    assert "telegramMessageId" in harness
    assert "mocked" in harness
    assert "RUN_TELEGRAM_COMMAND_BOT_E2E.cmd" not in harness
    assert "telegram_command_bot_e2e.py" in wrapper
