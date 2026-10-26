@echo off
setlocal EnableExtensions DisableDelayedExpansion
chcp 65001 >nul
for %%I in ("%~dp0..\..\..") do set "ROOT=%%~fI"
set "ENTRY=%ROOT%\programs\shared\stock_daily_candidate_runtime.py"
set "PYTHONUTF8=1"
set "PYTHONIOENCODING=utf-8"
set "PYTHONDONTWRITEBYTECODE=1"
set "DStock_SHARED_RAW_NETWORK_DISABLED=1"
set "ENABLE_FINMIND_FETCH=0"
set "PYTHONPATH=%ROOT%\programs\shared;%ROOT%\programs\single_stock_analysis\src"
call "%ROOT%\programs\shared\find_python.cmd" analysis
if not defined PY (
    echo 候選研究受阻：找不到目前專案的分析 Python 環境。
    exit /b 2
)
if not exist "%ENTRY%" (
    echo 候選研究受阻：找不到固定候選程式入口。
    exit /b 2
)
if "%~1"=="" (
    "%PY%" -B "%ENTRY%" --help
    exit /b 0
)
"%PY%" -B "%ENTRY%" %*
exit /b %ERRORLEVEL%
