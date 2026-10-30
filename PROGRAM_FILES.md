# D:\stock 現行程式分類與入口

本文件只描述目前仍在 `D:\stock` 的程式與其責任。舊版本由 Git 或隔離區保存，不能
依歷史文件把已退役的 runner 當成現行入口。

## 固定路徑契約

`D:\stock\programs\STABLE_PROGRAM_PATHS.txt` 是現行 `.py`、`.cmd`、`.ps1`、`.mjs`
程式與測試路徑的唯一清單。固定路徑驗證入口是：

```text
D:\stock\programs\VERIFY_STABLE_PROGRAM_PATHS.cmd
```

新增、搬移或改名程式前，必須同步更新這份清單並保留既有相容入口；不要建立日期化
副本、備份副本或第二份 manifest。`_DELETE_OLD_CONTENT_20260830` 只是歷史隔離區名稱，
2026-08-30 live observation 已確認不存在，其歷史內容不屬於現行清單。

清單只登錄 `programs\` 之下的路徑：驗證器只接受 `programs\` 開頭的登錄，未登錄程式
掃描也只走 `programs` 資料夾。根目錄 `一鍵次日Top10.cmd` 與 `TWNextDayTop10` 不登錄（驗證器只接受 `programs\` 開頭的登錄路徑）。
但驗證器的命名與路徑長度規則（資料夾名稱與程式主檔名 ≤ 48 字元、路徑 ≤ 200 字元）
仍掃描整個 `D:\stock`（含 `TWNextDayTop10`；任何層級名為 `.venv`、快取等的資料夾都略過，
`GitHub`、`data`、`outputs`、`reports` 只排除 `D:\stock` 根層那一個，`TWNextDayTop10\data` 仍會掃描），也檢查根目錄退役路徑是否重新出現；`D:\MIX All` 存在時同樣套用
命名與路徑長度規則。`TWNextDayTop10\tests` 由 `programs\RUN_ALL_TESTS.cmd` 以根
`D:\stock\.venv` 另外執行。

## 根層入口

| 入口 | 用途 |
|---|---|
| `programs\STOCK_TOOLS.cmd` | 一般使用者的一鍵選單 |
| `programs\RUN_ALL_TESTS.cmd` | 保留程式與支援契約測試總入口 |
| `programs\VERIFY_RUNTIME_DATA_PATHS.cmd` | 驗證共用資料路徑設定 |
| `programs\VERIFY_STABLE_PROGRAM_PATHS.cmd` | 驗證固定程式路徑與退役路徑隔離 |
| `programs\VALIDATE_MARKET_DATA_QUALITY.cmd` | 共用行情品質檢查；預設不修復資料 |

全市場正式分析入口不在 D-stock：使用
`D:\MIX All\02_MIX_ALL正式報告.cmd`。Central owner 手動更新使用
`D:\TWStockData\services\market_update\一鍵更新.cmd`；兩者都不應由 D-stock
複製或包裝成另一個本機 runner。

## 現行程式資料夾

### `programs\single_stock_analysis`

負責 D-stock 個股三個月與次日開盤分析。一般入口為：

```text
programs\single_stock_analysis\1_執行個股分析與產生報告.cmd
programs\single_stock_analysis\tools\RUN_SINGLE_STOCK_3M_ANALYSIS.cmd
programs\single_stock_analysis\tools\RUN_SINGLE_STOCK_TOMORROW_OPEN.cmd
```

`src` 放分析與資料介面，`tests` 放測試，`tools` 放單股稽核、研究快取與操作工具。
正式共用行情只讀 `D:\TWStockData\shared_market_data`；個股報告與狀態由
`D:\TWStockData\program_data\dstock_single` 管理。

### `programs\telegram_report_delivery`

負責 Telegram 個股查詢 Bot 與 MIX 正式報告傳送，不產生全市場分析。主要入口為：

```text
programs\telegram_report_delivery\RUN_COMMAND_BOT.cmd
programs\telegram_report_delivery\CHECK_COMMAND_BOT_STATUS.cmd
programs\telegram_report_delivery\RUN_SEND_LATEST_REPORT.cmd
programs\telegram_report_delivery\RUN_SEND_MIX_REPORT.cmd
programs\telegram_report_delivery\SHOW_MIX_WEEKDAY_0800_TASK.cmd
programs\telegram_report_delivery\RUN_TESTS.cmd
```

08:00 排程只允許 MIX formal-only 報告；定義檔為
`programs\telegram_report_delivery\MIX_WEEKDAY_0800_TASK_DEFINITION.json`。Token
與 Chat ID 僅能留在專用 `.ENV`，不得寫入文件、日誌或 Git。

### `programs\shared`

放多套程式共同使用的路徑、中央資料讀取、來源轉接與環境偵測。一般檢查入口為
`programs\shared\CHECK_ENV.cmd`，Python 尋找器為
`programs\shared\find_python.cmd`。此資料夾不擁有中央市場資料，也不應自行新增
另一套全市場下載入口。

開盤前版 H1 模型（ADR-0008，與 C0 並行）也位於此資料夾：產製端
`stock_daily_preopen.py`（train、calibrate、predict、settle、status）只讀中央資料，
寫入 `D:\TWStockData\program_data\stock_daily_preopen`；Top10、單股與 MIX 只透過
唯讀 API `stock_daily_preopen_reader.py` 取用。每日入口與排程工具為：

```text
programs\shared\RUN_PREOPEN_DAILY.cmd
programs\shared\preopen_schedule\INSTALL_PREOPEN_SCHEDULE.ps1
programs\shared\preopen_schedule\REMOVE_PREOPEN_SCHEDULE.ps1
programs\shared\preopen_schedule\RUN_CENTRAL_UPDATE.ps1
programs\shared\preopen_schedule\PREOPEN_SCHEDULE_DEFINITION.json
```

`RUN_PREOPEN_DAILY.cmd` 依序執行 settle、月度 train（未滿 21 個交易日自動跳過）與
predict。`INSTALL_PREOPEN_SCHEDULE.ps1` 預設只列出計畫，註冊需
`-Register -ConfirmRegister`；`RUN_CENTRAL_UPDATE.ps1` 只供排程呼叫外部
`D:\TWStockUpdater` 更新中央資料（`-Mode full` 對所有啟用路由做增量更新：更新器結束碼為 10 表示部分路由完成，視為可接受、不重試；其他非 0 結束碼〔28 除外〕等 30 分鐘重試，最多 3 次。`-Mode global` 只更新國際資料路由，不重試）。
詳細說明見 `programs\shared\README.md` 與
`docs\decisions\ADR-0008-stock-daily-preopen-parallel-model.md`。

### `programs\shared_data_management`

負責中央共用資料 receipt、owner seal 與路徑完整性驗證。主要入口為：

```text
programs\shared_data_management\VERIFY_SHARED_DATA_RECEIPT.cmd
programs\shared_data_management\VERIFY_SHARED_REFERENCE_DATA.ps1
programs\shared_data_management\RUN_MIGRATION.cmd
```

正式原始資料唯一根目錄是 `D:\TWStockData\shared_market_data`；migration 名稱只
保留相容性用途時，仍須以目前驗證腳本的實際行為為準，不可當成日常搬移工具。

### `programs\environment_setup`

管理本機 Python 環境與 Windows 使用者環境設定，並保存外部 GitHub 研究來源的分類
說明：

```text
programs\environment_setup\STOCK_SETUP_MANAGER.cmd
programs\environment_setup\INSTALL_D_STOCK_ENV.cmd
programs\environment_setup\INSTALL_D_STOCK_ENV_FULL.cmd
programs\environment_setup\CONFIGURE_WINDOWS_ENV.cmd
programs\environment_setup\VERIFY_STOCK_ENV.cmd
programs\environment_setup\OPEN_STOCK_TERMINAL.cmd
```

來源分類位於 `programs\environment_setup\sources`（36 個來源各有 `README.md`），只作
研究或方法參考。現行環境工具沒有研究來源下載入口；`INSTALL_D_STOCK_ENV_FULL.cmd`
與 `INSTALL_D_STOCK_ENV.cmd` 執行相同的安裝核心，不會 clone 或更新
`D:\stock\GitHub\repos`。安裝器不得 clone、覆蓋、搬移或刪除正式程式。根目錄
`D:\stock\.venv` 仍由執行中的 Telegram 服務使用，不能在清理舊內容時刪除。

### `programs\cross_system_validation`

`RUN_ACCEPTANCE_CHECKS.cmd` 與 `src`／`tests` 是跨系統唯讀驗收工具。它只讀取
Central、MIX、單股、Telegram 與保留證據，不啟動分析、不呼叫 API，也不因某個 component
成功就掩蓋整體 `blocked` 狀態。

### `programs\maintenance`

包含 cleanup plan schema、規劃與套用工具：`PLAN_CLEANUP.ps1` 先產生計畫，
`APPLY_CLEANUP.ps1` 只應在目標、證據與授權都明確時使用。它不是日常分析入口。

## `TWNextDayTop10`（次日 Top10）

位於 `D:\stock\TWNextDayTop10`，不在 `programs` 之下。負責次日 Top10 報告：H1 是
調整後收盤到同市場下一交易日收盤的方向與報酬；預設 `--model-source auto`，開盤前版
快照可用且新鮮時使用開盤前版，否則以 C0 為基準並在報告標示原因。入口為：

```text
D:\stock\一鍵次日Top10.cmd
TWNextDayTop10\RUN_TOP10.cmd
TWNextDayTop10\CHECK_ENV.cmd
TWNextDayTop10\SETUP_ENV.cmd
```

`一鍵次日Top10.cmd` 只轉呼叫 `RUN_TOP10.cmd`；`STOCK_TOOLS.cmd` 的 `N` 也指向
`RUN_TOP10.cmd`。程式使用自己的 `TWNextDayTop10\.venv`，缺少時入口只提示執行
`SETUP_ENV.cmd`，不自動安裝，也不替換根 `D:\stock\.venv`。它只推論共用已採納模型，
不下載資料、不訓練模型；報告產物寫入 `D:\TWStockData\program_data\tw_nextday_top10`，
Excel 另複製一份到 `TWNextDayTop10\data\reports\<日期>.xlsx` 並由入口開啟；`predict` 另寫入共用 C0 前瞻帳冊
`D:\TWStockData\program_data\stock_daily_c0\state\forward.sqlite3`。
詳細說明見 `TWNextDayTop10\README.md`。

## 資料、研究與證據位置

| 位置 | 責任與界線 |
|---|---|
| `D:\TWStockData\shared_market_data` | Central owner 管理的共用原始行情與參考資料 |
| `D:\TWStockData\program_data\dstock_single` | D-stock 個股報告、快取與狀態 |
| `D:\TWStockData\program_data\tw_nextday_top10` | 次日 Top10 的 JSON、CSV、Excel、HTML 與 manifest |
| `D:\TWStockData\program_data\stock_daily_preopen` | 開盤前版 H1 的模型指標、快照與前瞻帳冊 |
| `D:\MIX All` | 全市場分析、正式報告與 MIX 模型流程 |
| `D:\stock\GitHub` | 外部 GitHub 安裝／研究工作區，保留原狀 |
| `D:\stock\GPTlogic` | 方法研究與快照，不是正式推薦輸入 |
| `D:\stock\logs\daily_analysis_scheduler` | 2026-08-30 live observation 已確認目錄不存在；清理前歷史摘要為 40 檔、552,595 bytes（32 log／8 json），不是現行排程 |
| `D:\stock\record`、`release_worktrees`、`releases` | 歷程、工作樹與發布證據，非一般入口 |

## 明確退役項目

- `D:\stock\programs\full_market_analysis`：已退役且不存在；全市場改由 MIX 負責。
- `D:\stock\programs\daily_analysis_scheduler`：已退役且不存在；現行工作流由
  `D:\TWStockData\services\workflow_scheduler` 管理。
- `programs\shared\UPDATE_GLOBAL_MARKETS.cmd`：已移出現行路徑，不得重新加入選單、
  stable manifest 或排程。
- `_DELETE_OLD_CONTENT_20260830`：只是歷史隔離區名稱；2026-08-30 live observation
  已確認不存在，不是現行程式、資料或恢復來源。

2026-08-30 live observation 亦確認 User PATH 中舊的
`D:\stock\programs\full_market_analysis` segment 已移除 1 筆（35→34），target count
為 0；Machine PATH 未變更，其餘 segment 順序與文字保留。

## 正式驗收界線

正式驗收以 `D:\TWStockData\services\acceptance\latest.json`、中央 receipt 與各
系統 manifest 為準。文件、入口存在或單次 exit code=0 都不足以宣稱已發布；資料不完整、
成品損毀、來源 lineage 漂移或 acceptance 為 `blocked` 時，必須維持未完成狀態。
