"""Stable aliases retain one current dispatch and preserve the child's exit code."""
from pathlib import Path
SUITE = Path(__file__).resolve().parents[1]

def test_stable_tool_aliases_forward_to_the_fixed_current_entry():
    for name in ('RUN_SINGLE_STOCK_3M_ANALYSIS.cmd', 'RUN_SINGLE_STOCK_TOMORROW_OPEN.cmd'):
        text = (SUITE / 'tools' / name).read_text(encoding='utf-8').lower()
        assert 'target=%~dp0..\\' + name.lower() in text
        assert 'call "%target%" %*' in text
        assert 'exit /b %rc%' in text

def test_fixed_entries_have_only_the_current_close_horizon():
    paths = (
        ('1_執行個股分析與產生報告.cmd', 'h63', '--both-horizons'),
        ('RUN_SINGLE_STOCK_3M_ANALYSIS.cmd', 'h63', ''),
        ('RUN_SINGLE_STOCK_TOMORROW_OPEN.cmd', 'h1', ''),
    )
    for filename, horizon, extra in paths:
        text = (SUITE / filename).read_text(encoding='utf-8').lower()
        assert f'horizon={horizon}' in text
        assert f'horizon_args={extra}' in text
        assert 'find_python.cmd' in text
        assert 'run_single_stock_entry.py' in text
        for retired in ('--profile', '--source-mode', 'central_seal', 'loadformalenv', 'mitake-background'):
            assert retired not in text
