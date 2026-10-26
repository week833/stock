@echo off
if /I "%~1"=="__UTF8_MENU" goto :utf8_menu
chcp 65001 >nul
cmd /d /c call "%~f0" __UTF8_MENU
exit /b %ERRORLEVEL%

:utf8_menu
chcp 65001 >nul
setlocal EnableExtensions DisableDelayedExpansion
set "PROGRAMS_ROOT=%~dp0"
set "CENTRAL_ROOT=D:\TWStockData\services\market_update"
set "MIX_ROOT=D:\MIX All"
set "SCHEDULER_ROOT=D:\TWStockData\services\workflow_scheduler"
set "EMPTY_INPUTS=0"

:menu
cls
echo(===============================================================
echo(             台股分析與研究工具：統一一鍵選單
echo(===============================================================
echo(
echo( [新版研究分析]
echo( N. 新版次日Top10研究分析
echo( R. 新版Top10立即修正模型（手動）
echo( W. 新版Top10週末校正
echo(
echo( [正式執行]
echo( 1. MIX ALL 每日正式分析（Excel/PDF及三竹再製同步）
echo( 2. D-Stock 單股 C0 H63 收盤估計（訊號收盤至第63個交易日收盤）
echo( 3. D-Stock 單股 C0 H1 收盤估計（訊號收盤至下一個交易日收盤）
echo( A. Central 中央市場資料手動更新（Central owner；僅手動，會連網寫入）
echo( S. 獨立 Workflow Scheduler（Central→MIX→三竹編排）
echo(
echo( [Telegram]
echo( T1. 啟動 Telegram 個股查詢 Bot
echo( T2. 查詢 Telegram 個股 Bot 狀態
echo( T3. 手動傳送 MIX 正式報告（與08:00同一入口）
echo( T4. 檢查 MIX 星期一至五 08:00 排程定義（唯讀）
echo(
echo( [唯讀驗證]
echo( 6. 驗證 Central 共用資料 receipt（唯讀）
echo( 7. 驗證共用資料路徑
echo( 8. 驗證固定程式路徑
echo( V1. 驗證 Central 目前正式資料（唯讀）
echo( V3. 驗證 Central runtime／quota／環境契約（唯讀）
echo(
echo( [進階維護]
echo( 9. 執行保留程式與支援契約測試
echo( M1. 環境安裝與修復選單
echo(
echo( P. 顯示固定入口、pointer 與報告路徑（唯讀）
echo( Q. 離開
echo(
set "OPTION="
set "TARGET_ROLE="
set /p "OPTION=請輸入選項："
rem Leave after 3 consecutive empty inputs so a closed stdin (EOF) cannot loop forever.
if not defined OPTION (set /a EMPTY_INPUTS+=1) else (set "EMPTY_INPUTS=0")
if %EMPTY_INPUTS% GEQ 3 goto :done

if /I "%OPTION%"=="Q" goto :done
if /I "%OPTION%"=="N" goto :select_nextday_top10
if /I "%OPTION%"=="R" goto :select_manual_calibration
if /I "%OPTION%"=="W" goto :select_weekend_calibration
if /I "%OPTION%"=="1" goto :select_mix_daily
if /I "%OPTION%"=="2" goto :select_single_3m
if /I "%OPTION%"=="3" goto :select_single_tomorrow
if /I "%OPTION%"=="S" goto :select_scheduler
if /I "%OPTION%"=="6" goto :select_shared_receipt
if /I "%OPTION%"=="7" goto :select_runtime_paths
if /I "%OPTION%"=="8" goto :select_stable_paths
if /I "%OPTION%"=="9" goto :select_all_tests
if /I "%OPTION%"=="A" goto :select_central_update
if /I "%OPTION%"=="T1" goto :select_telegram_bot
if /I "%OPTION%"=="T2" goto :select_telegram_status
if /I "%OPTION%"=="T3" goto :select_mix_send
if /I "%OPTION%"=="T4" goto :select_mix_schedule
if /I "%OPTION%"=="V1" goto :select_central_verify
if /I "%OPTION%"=="V3" goto :select_central_runtime_verify
if /I "%OPTION%"=="M1" goto :select_setup
if /I "%OPTION%"=="E" goto :select_setup
if /I "%OPTION%"=="P" goto :show_paths

echo(
echo([提示] 無效選項，請重新選擇。
pause
goto :menu

:select_mix_daily
set "TARGET=%MIX_ROOT%\02_MIX_ALL正式報告.cmd"
set "TARGET_CWD=%MIX_ROOT%"
set "TARGET_DISPLAY=%TARGET%"
set "TARGET_KIND=cmd"
goto :run_selected

:select_nextday_top10
set "TARGET=D:\stock\TWNextDayTop10\RUN_TOP10.cmd"
set "TARGET_CWD=D:\stock\TWNextDayTop10"
set "TARGET_DISPLAY=%TARGET%"
set "TARGET_KIND=cmd"
goto :run_selected

:select_manual_calibration
set "TARGET=D:\stock\TWNextDayTop10\RUN_MANUAL_CALIBRATION.cmd"
set "TARGET_CWD=D:\stock\TWNextDayTop10"
set "TARGET_DISPLAY=%TARGET%"
set "TARGET_KIND=cmd"
goto :run_selected

:select_weekend_calibration
set "TARGET=D:\stock\TWNextDayTop10\RUN_WEEKEND_CALIBRATION.cmd"
set "TARGET_CWD=D:\stock\TWNextDayTop10"
set "TARGET_DISPLAY=%TARGET%"
set "TARGET_KIND=cmd"
goto :run_selected

:select_single_3m
set "TARGET=%PROGRAMS_ROOT%single_stock_analysis\tools\RUN_SINGLE_STOCK_3M_ANALYSIS.cmd"
set "TARGET_CWD=%PROGRAMS_ROOT%single_stock_analysis"
set "TARGET_DISPLAY=%TARGET%"
set "TARGET_KIND=cmd"
goto :run_selected

:select_single_tomorrow
set "TARGET=%PROGRAMS_ROOT%single_stock_analysis\tools\RUN_SINGLE_STOCK_TOMORROW_OPEN.cmd"
set "TARGET_CWD=%PROGRAMS_ROOT%single_stock_analysis"
set "TARGET_DISPLAY=%TARGET%"
set "TARGET_KIND=cmd"
goto :run_selected

:select_shared_receipt
set "TARGET=%PROGRAMS_ROOT%shared_data_management\VERIFY_SHARED_DATA_RECEIPT.cmd"
set "TARGET_CWD=%PROGRAMS_ROOT%shared_data_management"
set "TARGET_DISPLAY=%TARGET%"
set "TARGET_KIND=cmd"
goto :run_selected

:select_runtime_paths
set "TARGET=%PROGRAMS_ROOT%VERIFY_RUNTIME_DATA_PATHS.cmd"
set "TARGET_CWD=%PROGRAMS_ROOT"
set "TARGET_DISPLAY=%TARGET%"
set "TARGET_KIND=cmd"
goto :run_selected

:select_stable_paths
set "TARGET=%PROGRAMS_ROOT%VERIFY_STABLE_PROGRAM_PATHS.cmd"
set "TARGET_CWD=%PROGRAMS_ROOT"
set "TARGET_DISPLAY=%TARGET%"
set "TARGET_KIND=cmd"
goto :run_selected

:select_all_tests
set "TARGET=%PROGRAMS_ROOT%RUN_ALL_TESTS.cmd"
set "TARGET_CWD=%PROGRAMS_ROOT"
set "TARGET_DISPLAY=%TARGET%"
set "TARGET_KIND=cmd"
goto :run_selected

:select_central_update
set "TARGET=%CENTRAL_ROOT%\一鍵更新.cmd"
set "TARGET_CWD=%CENTRAL_ROOT%"
set "TARGET_DISPLAY=%TARGET%"
set "TARGET_ROLE=central_owner_manual"
set "TARGET_KIND=cmd"
goto :run_selected

:select_scheduler
set "TARGET=%SCHEDULER_ROOT%\RUN_WORKFLOW_SCHEDULER.cmd"
set "TARGET_CWD=%SCHEDULER_ROOT%"
set "TARGET_DISPLAY=%TARGET%"
set "TARGET_ROLE=workflow_scheduler"
set "TARGET_KIND=cmd"
goto :run_selected

:select_telegram_bot
set "TARGET=%PROGRAMS_ROOT%telegram_report_delivery\RUN_COMMAND_BOT.cmd"
set "TARGET_CWD=%PROGRAMS_ROOT%telegram_report_delivery"
set "TARGET_DISPLAY=%TARGET%"
set "TARGET_KIND=cmd"
goto :run_selected

:select_telegram_status
set "TARGET=%PROGRAMS_ROOT%telegram_report_delivery\CHECK_COMMAND_BOT_STATUS.cmd"
set "TARGET_CWD=%PROGRAMS_ROOT%telegram_report_delivery"
set "TARGET_DISPLAY=%TARGET%"
set "TARGET_KIND=cmd"
goto :run_selected

:select_mix_send
set "TARGET=%PROGRAMS_ROOT%telegram_report_delivery\RUN_SEND_MIX_REPORT.cmd"
set "TARGET_CWD=%PROGRAMS_ROOT%telegram_report_delivery"
set "TARGET_DISPLAY=%TARGET%"
set "TARGET_KIND=cmd"
goto :run_selected

:select_mix_schedule
set "TARGET=%PROGRAMS_ROOT%telegram_report_delivery\SHOW_MIX_WEEKDAY_0800_TASK.cmd"
set "TARGET_CWD=%PROGRAMS_ROOT%telegram_report_delivery"
set "TARGET_DISPLAY=%TARGET%"
set "TARGET_KIND=cmd"
goto :run_selected

:select_central_verify
set "TARGET=%CENTRAL_ROOT%\market_update.cmd"
set "TARGET_CWD=%CENTRAL_ROOT%"
set "TARGET_ARGS=verify"
set "TARGET_DISPLAY=%TARGET% verify"
set "TARGET_KIND=cmd"
goto :run_selected

:select_central_runtime_verify
set "TARGET=%CENTRAL_ROOT%\VERIFY_MARKET_UPDATE.cmd"
set "TARGET_CWD=%CENTRAL_ROOT%"
set "TARGET_DISPLAY=%TARGET%"
set "TARGET_KIND=cmd"
goto :run_selected

:select_setup
set "TARGET=%PROGRAMS_ROOT%environment_setup\STOCK_SETUP_MANAGER.cmd"
set "TARGET_CWD=%PROGRAMS_ROOT%environment_setup"
set "TARGET_DISPLAY=%TARGET%"
set "TARGET_KIND=cmd"
goto :run_selected

:run_selected
if not defined TARGET (
    set "RC=2"
    set "TARGET_DISPLAY=未設定的入口"
    echo [錯誤] 選定入口未設定。
    goto :show_result
)
if /I "%TARGET_ROLE%"=="central_owner_manual" echo([隔離] 這是 Central owner 手動管理入口；正式分析流程不會自動呼叫。
if /I "%TARGET_KIND%"=="cmd" goto :run_cmd_target
if /I "%TARGET_KIND%"=="python" goto :run_python_target
set "RC=2"
echo [錯誤] 未知的入口類型：%TARGET_KIND%
goto :show_result

:run_cmd_target
if not exist "%TARGET%" goto :missing_target
if defined TARGET_CWD (
    if not exist "%TARGET_CWD%" goto :missing_cwd
    pushd "%TARGET_CWD%" >nul 2>&1
    if errorlevel 1 goto :missing_cwd
)
call "%TARGET%" %TARGET_ARGS%
set "RC=%ERRORLEVEL%"
if defined TARGET_CWD popd
goto :show_result

:run_python_target
if not exist "%TARGET%" goto :missing_target
if not exist "%TARGET_SCRIPT%" goto :missing_script
if defined TARGET_CWD (
    if not exist "%TARGET_CWD%" goto :missing_cwd
    pushd "%TARGET_CWD%" >nul 2>&1
    if errorlevel 1 goto :missing_cwd
)
"%TARGET%" -I "%TARGET_SCRIPT%" %TARGET_ARGS%
set "RC=%ERRORLEVEL%"
if defined TARGET_CWD popd
goto :show_result

:missing_target
set "RC=2"
echo [錯誤] 找不到選定入口：%TARGET_DISPLAY%
echo [錯誤] 實際檢查路徑：%TARGET%
goto :show_result

:missing_script
set "RC=2"
echo [錯誤] 找不到選定入口所需腳本：%TARGET_SCRIPT%
echo [錯誤] Python 執行檔：%TARGET%
goto :show_result

:missing_cwd
set "RC=2"
echo [錯誤] 找不到或無法進入選定入口工作目錄：%TARGET_CWD%
goto :show_result

:show_paths
cls
echo(===============================================================
echo(             固定入口、pointer 與報告路徑（唯讀）
echo(===============================================================
echo(
echo( [固定一鍵入口]
call :print_path "D-Stock 單股 C0 H63 收盤估計" "%PROGRAMS_ROOT%single_stock_analysis\tools\RUN_SINGLE_STOCK_3M_ANALYSIS.cmd"
call :print_path "D-Stock 單股 C0 H1 收盤估計" "%PROGRAMS_ROOT%single_stock_analysis\tools\RUN_SINGLE_STOCK_TOMORROW_OPEN.cmd"
call :print_path "Central owner 手動更新（正式流程不自動呼叫）" "%CENTRAL_ROOT%\一鍵更新.cmd"
call :print_path "MIX ALL 每日正式分析" "%MIX_ROOT%\02_MIX_ALL正式報告.cmd"
call :print_path "獨立 Workflow Scheduler" "%SCHEDULER_ROOT%\RUN_WORKFLOW_SCHEDULER.cmd"
echo(
echo( [Telegram 一鍵入口]
call :print_path "Telegram 個股 Bot" "%PROGRAMS_ROOT%telegram_report_delivery\RUN_COMMAND_BOT.cmd"
call :print_path "Telegram Bot 狀態" "%PROGRAMS_ROOT%telegram_report_delivery\CHECK_COMMAND_BOT_STATUS.cmd"
call :print_path "MIX 正式報告傳送" "%PROGRAMS_ROOT%telegram_report_delivery\RUN_SEND_MIX_REPORT.cmd"
call :print_path "MIX 08:00 排程唯讀檢查" "%PROGRAMS_ROOT%telegram_report_delivery\SHOW_MIX_WEEKDAY_0800_TASK.cmd"
echo(
echo( [正式 pointer／manifest 與報告根目錄]
call :print_path "Central latest manifest" "D:\TWStockData\services\market_update\receipts\latest_manifest.json"
call :print_path "D-Stock 個股 manifest" "D:\TWStockData\program_data\dstock_single\reports\latest_report_manifest.json"
call :print_path "MIX C0 report pointer" "D:\TWStockData\program_data\mix\reports\LATEST_C0_RUN.json"
call :print_path "Telegram Bot heartbeat" "D:\TWStockData\services\telegram\data\stock_command_bot.heartbeat.json"
call :print_path "D-Stock 個股報告根目錄" "D:\TWStockData\program_data\dstock_single\reports"
call :print_path "Telegram 個股工作輸出" "D:\TWStockData\program_data\dstock_single\telegram_jobs"
call :print_path "MIX 輸出根目錄" "D:\TWStockData\program_data\mix\outputs"
call :print_path "MIX PDF 報告根目錄" "D:\TWStockData\program_data\mix\reports\pdf"
echo(
echo( 動態最新檔名依 pointer／manifest 宣告；本選單不猜測日期化檔名。
pause
goto :menu

:print_path
if exist "%~2" (
    echo([存在] %~1：%~2
) else (
    echo([缺少] %~1：%~2
)
exit /b 0

:show_result
echo(
if "%RC%"=="0" goto :show_success
echo([結果] 入口結束，exit code=%RC%
goto :show_result_target

:show_success
echo([完成] 入口執行成功，exit code=0

:show_result_target
echo([入口] %TARGET_DISPLAY%
if defined TARGET_CWD echo([工作目錄] %TARGET_CWD%
if defined TARGET_ARGS echo([參數] %TARGET_ARGS%
pause
set "TARGET="
set "TARGET_ARGS="
set "TARGET_SCRIPT="
set "TARGET_DISPLAY="
set "TARGET_KIND="
set "TARGET_CWD="
set "TARGET_ROLE="
goto :menu

:done
endlocal
exit /b 0
