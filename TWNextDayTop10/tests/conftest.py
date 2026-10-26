"""Top10 測試共用設定。

- 每個測試都把開盤前版快照根目錄（STOCK_DAILY_PREOPEN_ROOT）指向空的暫存目錄，
  並清除 TOP10_MODEL_SOURCE，確保既有測試不會讀到正式快照或使用者設定。
- ``preopen_fixture`` 以共用 stock_daily_preopen_fixture.write_fixture 在暫存目錄寫出假快照；
  本專案 .venv 沒有 pyarrow／fastparquet 時，測試期間以 duckdb 代寫 parquet（只在測試中生效）。
"""
from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

import pandas as pd
import pytest

PROJECT = Path(__file__).resolve().parents[1]
SHARED = Path(r"D:\stock\programs\shared")
if str(PROJECT) not in sys.path:
    sys.path.insert(0, str(PROJECT))
if str(SHARED) not in sys.path:
    sys.path.append(str(SHARED))


def _native_parquet_engine():
    return any(importlib.util.find_spec(name) is not None for name in ("pyarrow", "fastparquet"))


def _duckdb_to_parquet(self, path, *args, index=None, **kwargs):
    import duckdb

    frame = self.reset_index() if index else self
    target = str(Path(path).as_posix()).replace("'", "''")
    connection = duckdb.connect()
    try:
        connection.register("frame", frame)
        connection.execute(f"COPY frame TO '{target}' (FORMAT PARQUET)")
    finally:
        connection.close()


@pytest.fixture(autouse=True)
def _isolated_preopen_root(tmp_path, monkeypatch):
    root = tmp_path / "preopen_absent"
    monkeypatch.setenv("STOCK_DAILY_PREOPEN_ROOT", str(root))
    monkeypatch.delenv("TOP10_MODEL_SOURCE", raising=False)
    return root


@pytest.fixture()
def preopen_fixture(tmp_path, monkeypatch):
    """回傳 write(**kwargs) → meta；快照寫在 tmp_path/preopen 並設為 STOCK_DAILY_PREOPEN_ROOT。"""
    import stock_daily_preopen_fixture as fixture_module

    root = tmp_path / "preopen"
    monkeypatch.setenv("STOCK_DAILY_PREOPEN_ROOT", str(root))
    if not _native_parquet_engine():
        monkeypatch.setattr(pd.DataFrame, "to_parquet", _duckdb_to_parquet)

    def write(**kwargs):
        return fixture_module.write_fixture(root, **kwargs)

    write.root = root
    return write

@pytest.fixture(autouse=True)
def _isolated_report_comparison(monkeypatch):
    """Existing synthetic report tests must never pin/read the live Central store."""
    import reporting

    def unavailable():
        raise ModuleNotFoundError("report_comparison_fixture_not_configured")

    monkeypatch.setattr(reporting, "_comparison_module", unavailable)
