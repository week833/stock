# D:\stock 台股分析與研究工具

## 目前用途

`D:\stock` 是台股單股分析、次日 Top10、開盤前版 H1 模型產製、Telegram 交付、
共用資料驗證、環境管理與研究資料的工作區。全市場正式分析不在本資料夾執行：
目前由外部的 `D:\MIX All` 負責；中央行情資料由 `D:\TWStockData` 統一管理。

一般使用者從這個選單開始：

```text
D:\stock\programs\STOCK_TOOLS.cmd
```

選單只顯示現行入口，會在執行前檢查實際目標路徑。需要直接操作時，使用下列固定
入口：

| 用途 | 入口 |
|---|---|
| MIX 全市場正式分析 | `D:\MIX All\02_MIX_ALL正式報告.cmd` |
| D-stock 單股三個月分析 | `D:\stock\programs\single_stock_analysis\tools\RUN_SINGLE_STOCK_3M_ANALYSIS.cmd` |
| D-stock 單股次日開盤分析 | `D:\stock\programs\single_stock_analysis\tools\RUN_SINGLE_STOCK_TOMORROW_OPEN.cmd` |
| 次日 Top10（H1 預設開盤前版，C0 備援） | `D:\stock\一鍵次日Top10.cmd`（轉呼叫 `D:\stock\TWNextDayTop10\RUN_TOP10.cmd`） |
| 開盤前版 H1 每日快照（排程入口） | `D:\stock\programs\shared\RUN_PREOPEN_DAILY.cmd` |
| Central owner 手動更新 | `D:\TWStockData\services\market_update\一鍵更新.cmd` |
| Telegram 個股查詢 Bot | `D:\stock\programs\telegram_report_delivery\RUN_COMMAND_BOT.cmd` |
| 手動傳送 MIX 正式報告 | `D:\stock\programs\telegram_report_delivery\RUN_SEND_MIX_REPORT.cmd` |
| 檢查共用資料路徑 | `D:\stock\programs\VERIFY_RUNTIME_DATA_PATHS.cmd` |
| 檢查固定程式路徑 | `D:\stock\programs\VERIFY_STABLE_PROGRAM_PATHS.cmd` |
| 環境安裝與修復 | `D:\stock\programs\environment_setup\STOCK_SETUP_MANAGER.cmd` |

## 資料與責任邊界

- `D:\TWStockData\shared_market_data` 是全體程式共用的中央行情資料根目錄；不要
  在 `D:\stock` 建立或保留第二份正式行情資料。
- `D:\MIX All` 負責全市場分析、正式報告與其模型流程；`D:\stock` 不提供舊的
  全市場分析入口。
- `programs\single_stock_analysis` 只負責個股三個月與次日開盤分析，報告寫入
  `D:\TWStockData\program_data\dstock_single`。
- `TWNextDayTop10` 產生次日 Top10 報告（H1：調整後收盤到同市場下一交易日收盤的
  方向與報酬），只推論共用已採納模型，不下載資料、不訓練模型；使用自己的
  `TWNextDayTop10\.venv`（由 `SETUP_ENV.cmd` 明確建立），報告產物寫入
  `D:\TWStockData\program_data\tw_nextday_top10`（Excel 另複製一份到
  `TWNextDayTop10\data\reports\<日期>.xlsx` 並開啟），另寫入共用 C0 前瞻帳冊
  `D:\TWStockData\program_data\stock_daily_c0\state\forward.sqlite3`。
- `programs\shared\stock_daily_preopen*.py` 是開盤前版 H1 模型（ADR-0008，與 C0
  並行），每日由 `programs\shared\RUN_PREOPEN_DAILY.cmd` 寫入
  `D:\TWStockData\program_data\stock_daily_preopen`；消費端只透過
  `stock_daily_preopen_reader.py` 唯讀取用，快照不可用或過期時回退 C0 並標示
  原因。排程定義在 `programs\shared\preopen_schedule`。
- `programs\telegram_report_delivery` 只傳送已驗證的既有報告，不改寫分析結果。
- `programs\environment_setup` 管理根目錄 Python 環境與 Windows 使用者環境變數；
  `sources` 只保存外部研究來源的分類說明，現行工具不 clone 或更新
  `D:\stock\GitHub\repos`。`D:\stock\GitHub`、`D:\stock\GPTlogic` 只作研究或
  安裝來源，不會自動成為正式推薦資料。
- 根目錄 `.venv` 仍是目前執行中 Telegram 服務所依賴的環境，不能因清理舊內容而刪除。

## 目錄導覽

| 目錄 | 現行角色 |
|---|---|
| `programs` | 現行入口、支援程式、測試與環境工具 |
| `TWNextDayTop10` | 次日 Top10 程式、測試與專案環境設定；根目錄 `一鍵次日Top10.cmd` 是其一鍵入口 |
| `config` | 共用資料路徑與執行設定 |
| `docs` | 治理、來源登錄與工作交接文件 |
| `logs\daily_analysis_scheduler` | 2026-08-30 live observation 已確認目錄不存在；清理前歷史摘要為 40 檔、552,595 bytes（32 log／8 json），不是現行排程 |
| `GitHub`、`GPTlogic` | 外部研究與方法資料，保留原狀 |
| `record`、`release_worktrees`、`releases` | 歷程、工作樹與發布證據，非一般執行入口 |

## 已退役與清理狀態

D-stock 舊的 `programs\full_market_analysis` 與
`programs\daily_analysis_scheduler` 已退役且不在現行目錄中；不要依歷史文件、舊
捷徑或舊環境變數重新啟動它們。`logs\daily_analysis_scheduler` 亦已由
2026-08-30 live observation 確認不存在，不是現行 scheduler，也不是可執行入口。

舊內容曾集中在下列歷史隔離區名稱；2026-08-30 live observation 已確認該路徑不存在：

```text
D:\stock\_DELETE_OLD_CONTENT_20260830
```

此名稱只供歷史紀錄，不是程式、資料或研究入口；不可依歷史文件重新建立或恢復舊入口。
清理不涉及 `D:\TWStockData`、`D:\MIX All`、
`D:\stock\GitHub`、根 `.venv` 或 Telegram 執行資料。

清理前的歷史摘要記錄 `D:\stock\logs\daily_analysis_scheduler` 曾有 40 檔、552,595
bytes（32 log／8 json）；過去的精確刪除嘗試曾在 CreateProcess 前被執行政策拒絕。
2026-08-30 live observation 已確認該目錄不存在；上述數字與執行摘要不代表目前內容或待辦。

2026-08-30 live observation 亦確認 User PATH 中舊的
`D:\stock\programs\full_market_analysis` segment 已移除 1 筆（35→34）；target count
為 0，Machine PATH 未變更，其餘 segment 的順序與文字保留。

正式驗收狀態仍以 `D:\TWStockData\services\acceptance\latest.json` 為準；若該檔案
為 `blocked`、`fail` 或缺失，不得把本導覽文件視為整體系統已通過正式驗收。

更多分類見 `PROGRAM_FILES.md` 與 `docs\CODEBASE_CLASSIFICATION.md`；工作交接先讀
`docs\HANDOFF.md`，資料來源先讀 `docs\SOURCE_REGISTRY.md`。
