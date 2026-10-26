# 現行程式與一鍵入口

`D:\stock\programs` 只放目前仍受支援的入口、程式、測試、設定與程式文件。正式
行情、模型、輸出與報告由各自的 canonical owner 管理，不因清理而在這裡建立複本。

## 一般使用者選單

雙擊：

```text
D:\stock\programs\STOCK_TOOLS.cmd
```

選單目前的責任如下：

| 選項 | 實際目標 | 性質 |
|---|---|---|
| `1` | `D:\MIX All\02_MIX_ALL正式報告.cmd` | MIX 全市場正式分析 |
| `2` | `single_stock_analysis\tools\RUN_SINGLE_STOCK_3M_ANALYSIS.cmd` | D-stock 單股 C0 H63 收盤估計（選單文字仍寫「三個月」） |
| `3` | `single_stock_analysis\tools\RUN_SINGLE_STOCK_TOMORROW_OPEN.cmd` | D-stock 單股 C0 H1 收盤估計（選單文字仍寫「次日開盤」） |
| `A` | `D:\TWStockData\services\market_update\一鍵更新.cmd` | Central owner 手動更新，會連網寫入 |
| `S` | `D:\TWStockData\services\workflow_scheduler\RUN_WORKFLOW_SCHEDULER.cmd` | Central→MIX→三竹工作流 |
| `T1`／`T2` | `telegram_report_delivery\RUN_COMMAND_BOT.cmd`／`CHECK_COMMAND_BOT_STATUS.cmd` | Bot 啟動／狀態 |
| `T3`／`T4` | `telegram_report_delivery\RUN_SEND_MIX_REPORT.cmd`／`SHOW_MIX_WEEKDAY_0800_TASK.cmd` | MIX 傳送／排程唯讀檢查 |
| `6`／`7`／`8` | `VERIFY_SHARED_DATA_RECEIPT.cmd`／`VERIFY_RUNTIME_DATA_PATHS.cmd`／`VERIFY_STABLE_PROGRAM_PATHS.cmd` | 唯讀驗證 |
| `9` | `RUN_ALL_TESTS.cmd` | 保留程式與支援契約測試 |
| `M1` | `environment_setup\STOCK_SETUP_MANAGER.cmd` | 環境、研究來源與修復選單 |

選單會先檢查目標與工作目錄；找不到目標時停止並顯示實際路徑。`A` 與 `S` 是外部
owner 的入口，D-stock 不複製它們。

## 目前資料夾

| 資料夾 | 責任 | 主要入口 |
|---|---|---|
| `single_stock_analysis` | 個股 C0 H1／H63 收盤估計報告與測試 | `1_執行個股分析與產生報告.cmd`、`tools\RUN_*.cmd` |
| `telegram_report_delivery` | Telegram 個股 Bot、MIX 報告傳送、服務與排程定義 | `RUN_COMMAND_BOT.cmd`、`RUN_SEND_MIX_REPORT.cmd` |
| `shared` | 共用路徑、中央資料讀取、來源介面與環境偵測 | `CHECK_ENV.cmd`、`find_python.cmd` |
| `shared_data_management` | Central receipt、owner seal 與共用資料完整性驗證 | `VERIFY_SHARED_DATA_RECEIPT.cmd` |
| `environment_setup` | Python、Windows 環境與外部 GitHub 研究來源 | `STOCK_SETUP_MANAGER.cmd` |
| `cross_system_validation` | 跨系統唯讀 acceptance source／contract checks | `RUN_ACCEPTANCE_CHECKS.cmd` |
| `maintenance` | 清理計畫、schema 與受控維護工具 | `PLAN_CLEANUP.ps1`、`APPLY_CLEANUP.ps1` |

根層驗證與工具入口為 `VERIFY_RUNTIME_DATA_PATHS.cmd`、
`VERIFY_STABLE_PROGRAM_PATHS.cmd`、`VALIDATE_MARKET_DATA_QUALITY.cmd` 與
`RUN_ALL_TESTS.cmd`。現行程式及測試路徑只登錄於 `STABLE_PROGRAM_PATHS.txt`。

## 資料與環境邊界

- 正式共用行情唯一位於 `D:\TWStockData\shared_market_data`；設定登錄表是
  `D:\stock\config\shared_data_paths.json`。
- 個股報告、快取與狀態位於 `D:\TWStockData\program_data\dstock_single`；MIX
  報告與模型位於 `D:\MIX All`／其中央 program-data。
- `environment_setup` 管理 `D:\stock\.venv` 與 `D:\stock\GitHub\repos`。根
  `.venv` 仍由執行中的 Telegram 服務直接使用，不能刪除或搬移。
- `GitHub`、`GPTlogic` 僅作外部研究或方法參考，不是正式推薦資料來源。
- `.ENV`、Token、快取、模型、輸出、報告與日誌不得提交 Git；不得為了清理而改動
  `D:\TWStockData`、`D:\MIX All` 或 `D:\stock\GitHub`。

## 退役與歷史內容

`D:\stock\programs\full_market_analysis` 與
`D:\stock\programs\daily_analysis_scheduler` 均已退役且不存在，不能再作為入口。
全市場改由 `D:\MIX All` 負責，現行工作流改由
`D:\TWStockData\services\workflow_scheduler` 負責。舊的
`programs\shared\UPDATE_GLOBAL_MARKETS.cmd` 已移出現行路徑，也不能重新加入選單或
固定路徑清單。

`D:\stock\logs\daily_analysis_scheduler` 已由 2026-08-30 live observation 確認目錄
不存在；清理前歷史摘要為 40 檔、552,595 bytes（32 log／8 json），過去刪除嘗試曾在
CreateProcess 前被執行政策拒絕。這些摘要不代表目前內容或待辦，也不是現行 scheduler。
`D:\stock\_DELETE_OLD_CONTENT_20260830` 只是歷史隔離區名稱；2026-08-30 live
observation 已確認該路徑不存在，不是入口、資料來源或復原目錄。

2026-08-30 live observation：User PATH 中舊的
`D:\stock\programs\full_market_analysis` segment 已移除 1 筆（35→34），target count
為 0；Machine PATH 未變更，其餘 segment 順序與文字保留。

## 驗證與完成條件

路徑變更後使用 `VERIFY_STABLE_PROGRAM_PATHS.cmd`；共用資料設定變更後使用
`VERIFY_RUNTIME_DATA_PATHS.cmd`。跨系統正式狀態以
`D:\TWStockData\services\acceptance\latest.json` 為準；`blocked`、`fail` 或
缺失時不得宣稱整體分析與發布已完成。
