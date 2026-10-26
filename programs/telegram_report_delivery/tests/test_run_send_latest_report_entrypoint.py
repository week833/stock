from __future__ import annotations

from pathlib import Path


SUITE_DIR = Path(__file__).resolve().parents[1]
ENTRYPOINT = SUITE_DIR / "RUN_SEND_LATEST_REPORT.cmd"
MIX_ENTRYPOINT = r"D:\stock\programs\telegram_report_delivery\RUN_SEND_MIX_REPORT.cmd"
DELEGATE_LINE = 'call "%MIX_ENTRYPOINT%" %*'


def _entrypoint_text() -> str:
    return ENTRYPOINT.read_text(encoding="utf-8")


def test_latest_report_entrypoint_is_fail_closed_on_missing_or_nonzero_mix_sender() -> None:
    source = _entrypoint_text()
    delegate_start = source.index(DELEGATE_LINE)

    assert f'set "MIX_ENTRYPOINT=%SUITE%RUN_SEND_MIX_REPORT.cmd"' in source
    missing_gate = 'if not exist "%MIX_ENTRYPOINT%" ('
    missing_start = source.index(missing_gate)
    assert missing_gate in source
    assert f'call "%MIX_ENTRYPOINT%" %*' in source
    assert 'set "RC=%ERRORLEVEL%"' in source
    assert source.index('set "NO_PAUSE=1"') < delegate_start
    assert 'goto :End' in source[missing_start:delegate_start]
    assert delegate_start < source.index('set "RC=%ERRORLEVEL%"')


def test_latest_report_entrypoint_passes_all_original_args_and_child_exit_code() -> None:
    source = _entrypoint_text()
    delegate_start = source.index(DELEGATE_LINE)

    assert source.count(DELEGATE_LINE) == 1
    assert source.index('if not exist "%MIX_ENTRYPOINT%" (') < delegate_start
    assert delegate_start < source.index('set "RC=%ERRORLEVEL%"')
    assert source.rstrip().endswith('exit /b %RC%')


def test_manual_sender_has_no_analysis_update_or_artifact_fallback_path() -> None:
    source = _entrypoint_text().casefold()

    for forbidden in (
        "full_market_analysis",
        "dstock_daily",
        "shared_market_data",
        "raw",
        "api",
        "fixture",
        "legacy",
        "for /f",
        "dir /b",
        "glob",
        "mtime",
        "*.pdf",
        "*.xlsx",
        "run_stock_analysis.cmd",
        "run_quick_update.cmd",
        "run_full_refresh.cmd",
        "finmind",
        "provider",
    ):
        assert forbidden not in source
