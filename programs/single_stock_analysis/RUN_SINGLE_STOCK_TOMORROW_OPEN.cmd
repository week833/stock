@echo off
setlocal EnableExtensions EnableDelayedExpansion
chcp 65001 >nul
set "SUITE=%~dp0"
for %%I in ("%SUITE%..\..") do set "ROOT=%%~fI"
set "ENTRY=%SUITE%src\run_single_stock_entry.py"
set "HORIZON=H1"
set "HORIZON_ARGS="
set "DStock_SHARED_RAW_NETWORK_DISABLED=1"
set "ENABLE_FINMIND_FETCH=0"
set "PYTHONUTF8=1"
set "PYTHONIOENCODING=utf-8"
set "PYTHONDONTWRITEBYTECODE=1"
set "PYTHONPATH=%SUITE%src;%ROOT%\programs\shared"
if not exist "%ENTRY%" goto MissingEntry
if not exist "%SUITE%src\tw_single_stock_c0.py" goto MissingEntry
call "%ROOT%\programs\shared\find_python.cmd" analysis
if not defined PY (
    echo [錯誤] 找不到具備目前分析所需套件的 Python。
    set "RC=2"
    goto End
)
cd /d "%ROOT%"
if errorlevel 1 (
    echo [錯誤] 無法進入固定的專案根目錄。
    set "RC=2"
    goto End
)
echo H1：訊號收盤至同市場下一個已驗證的交易日收盤；優先顯示開盤前版，不可用時改用 C0 基準並標示原因（--h1-source auto^|preopen^|c0）。
if "%~1"=="" (
    set /p "STOCK=請輸入台灣普通股代碼或名稱："
    if not defined STOCK (
        set "RC=2"
        goto End
    )
    "%PY%" -B "%ENTRY%" %HORIZON% %HORIZON_ARGS% --stock "!STOCK!"
    set "RC=!ERRORLEVEL!"
    goto End
)
set "FIRST=%~1"
if "!FIRST:~0,1!"=="-" (
    "%PY%" -B "%ENTRY%" %HORIZON% %HORIZON_ARGS% %*
    set "RC=!ERRORLEVEL!"
    goto End
)
set "STOCK=%~1"
set "EXTRA_ARGS="
:CollectExtraArgs
shift /1
if "%~1"=="" goto RunWithStock
set "EXTRA_ARGS=!EXTRA_ARGS! ^"%~1^""
goto CollectExtraArgs
:RunWithStock
"%PY%" -B "%ENTRY%" %HORIZON% %HORIZON_ARGS% --stock "!STOCK!" !EXTRA_ARGS!
set "RC=!ERRORLEVEL!"
goto End
:MissingEntry
echo [錯誤] 找不到目前的 C0 入口程式：%ENTRY%
set "RC=2"
:End
if not "%RC%"=="0" echo [錯誤] C0 分析失敗，結束碼：%RC%。
if not "%NO_PAUSE%"=="1" pause
exit /b %RC%
