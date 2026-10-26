# D:\stock Codex 工作規則

## 預設行為

- 使用繁體中文；回覆先給結果，再列出修改檔案、實際驗證與待決事項。
- 「先分析」只做唯讀盤點與計畫；只有明確要求修正或執行時才修改已授權範圍。
- 診斷只分析；修正才修改。保留使用者未提交的變更、無關檔案與外部工作樹。
- 不以改文件來改變正式程式行為；文件與程式不一致時，以實際程式、測試與 receipt
  核對後記錄事實。

## 開始工作前的治理資料

依序讀取必要內容：

1. `docs\HANDOFF.md`：目前狀態與阻塞。
2. `docs\SOURCE_REGISTRY.md`：來源資格與權威路徑。
3. `docs\decisions\README.md` 及對應 ADR：架構與流程決策。
4. `D:\TWStockData\services\acceptance\latest.json`：正式驗收狀態。

以上資料只提供治理與證據索引；中央 receipt、正式報告 manifest、模型校正歷史及
既有領域紀錄仍是各自的權威來源。Acceptance 缺失、`fail` 或 `blocked` 時不得宣稱
整體系統已完成或可發布。

## 現行固定入口

| 用途 | 入口 |
|---|---|
| 一般使用者選單 | `D:\stock\programs\STOCK_TOOLS.cmd` |
| MIX 全市場正式分析 | `D:\MIX All\02_MIX_ALL正式報告.cmd` |
| Central owner 手動更新 | `D:\TWStockData\services\market_update\一鍵更新.cmd` |
| Central receipt 唯讀驗證 | `D:\stock\programs\shared_data_management\VERIFY_SHARED_DATA_RECEIPT.cmd` |
| D-stock 單股三個月 | `D:\stock\programs\single_stock_analysis\tools\RUN_SINGLE_STOCK_3M_ANALYSIS.cmd` |
| D-stock 單股次日開盤 | `D:\stock\programs\single_stock_analysis\tools\RUN_SINGLE_STOCK_TOMORROW_OPEN.cmd` |
| Telegram 個股 Bot | `D:\stock\programs\telegram_report_delivery\RUN_COMMAND_BOT.cmd` |
| Telegram MIX 報告傳送 | `D:\stock\programs\telegram_report_delivery\RUN_SEND_MIX_REPORT.cmd` |
| Telegram 08:00 定義唯讀檢查 | `D:\stock\programs\telegram_report_delivery\SHOW_MIX_WEEKDAY_0800_TASK.cmd` |
| 共用資料路徑驗證 | `D:\stock\programs\VERIFY_RUNTIME_DATA_PATHS.cmd` |
| 固定程式路徑驗證 | `D:\stock\programs\VERIFY_STABLE_PROGRAM_PATHS.cmd` |
| 保留程式與支援契約測試 | `D:\stock\programs\RUN_ALL_TESTS.cmd` |
| 環境與研究來源管理 | `D:\stock\programs\environment_setup\STOCK_SETUP_MANAGER.cmd` |
| Central→MIX 工作流編排 | `D:\TWStockData\services\workflow_scheduler\RUN_WORKFLOW_SCHEDULER.cmd` |

前三個全市場／中央相關入口的責任不同：MIX 產生全市場分析，Central 只由 owner
管理共用資料，工作流排程位於 `D:\TWStockData`；D-stock 不再提供舊全市場 runner。

## 不可破壞的契約

- 現行程式、測試與設定不得任意搬移、改名或刪除；唯一固定路徑清單是
  `programs\STABLE_PROGRAM_PATHS.txt`。不要建立日期化程式副本或相容備份。
- 正式共用行情只使用 `D:\TWStockData\shared_market_data`。`config\shared_data_paths.json`
  是 D-stock 的路徑登錄表，不得把已移除的本機資料複本或其他副本當成正式來源。
- `D:\MIX All`、`D:\TWStockData` 與 `D:\cowork` 的專案及資料不屬於本次 D-stock
  清理範圍；`D:\stock\GitHub`、`D:\stock\GPTlogic` 僅作研究或安裝來源，不直接
  成為正式推薦輸入。
- 單股正式報告、Telegram 傳送與中央資料各有自己的 receipt／manifest；傳送程式只
  傳送已驗證成品，不修改分析結果。
- 公式校正只追加至
  `D:\TWStockData\program_data\dstock_daily\model\calibration_history.jsonl`，
  不覆寫歷史；輸出、報告、快取、Token、`.ENV` 與日誌不得提交 Git。
- `VALIDATE_MARKET_DATA_QUALITY.cmd` 預設只檢查；資料修復必須由使用者明確指定
  `--repair-invalid`。
- 報告即使退出碼為 0，仍須通過對應的 manifest、payload、成品與日期驗證，才能視為
  可交付；不要以檔案存在或單一成功旗標代替驗收。
- 根目錄 `D:\stock\.venv` 仍被執行中的 Telegram 服務直接使用，不得在清理舊內容時
  刪除、搬移或覆寫；環境工具也不得替換其他專案的 Python 環境。

## 退役、歷史與清理界線

- 舊 `D:\stock\programs\full_market_analysis` 已退役且不在現行目錄中；全市場分析
  入口一律使用 `D:\MIX All`。
- 舊 `D:\stock\programs\daily_analysis_scheduler` 已退役且不在現行目錄中；現行
  工作流由 `D:\TWStockData\services\workflow_scheduler` 管理。
- 舊 `programs\shared\UPDATE_GLOBAL_MARKETS.cmd` 已移出現行路徑，不得重新加入選單、
  manifest 或排程。
- `D:\stock\logs\daily_analysis_scheduler` 已由 2026-08-30 live observation 確認該
  目錄不存在；清理前歷史摘要為 40 檔、552,595 bytes（32 log／8 json），過去刪除嘗試
  曾在 CreateProcess 前被執行政策拒絕。這些摘要不代表目前內容或待辦，也不是排程入口。
- `D:\stock\_DELETE_OLD_CONTENT_20260830` 只是歷史隔離區名稱；2026-08-30 live
  observation 已確認該路徑不存在，不可作為入口或恢復來源。
- 2026-08-30 live observation：User PATH 中舊的
  `D:\stock\programs\full_market_analysis` segment 已移除 1 筆（35→34），target count
  為 0；Machine PATH 未變更，其餘 segment 順序與文字保留。

## 工作方式與驗證

- 只讀取完成任務必要的說明、程式與測試；不重抓資料、不重新訓練、不執行外部傳送，
  除非使用者明確要求。
- 文件／規則變更的最低驗證是確認路徑存在、Markdown 可讀，以及 Git 變更範圍只在
  授權檔案內。
- 程式路徑變更使用 `programs\VERIFY_STABLE_PROGRAM_PATHS.cmd`；共用資料設定變更
  使用 `programs\VERIFY_RUNTIME_DATA_PATHS.cmd`。全市場正式流程、完整刷新、服務、
  排程與外部網路操作都要另有明確授權。
- `programs\RUN_ALL_TESTS.cmd` 是測試入口，不代表本次文件清理必須執行全部測試。

詳細分類見 `PROGRAM_FILES.md`、`programs\README.md` 與
`docs\CODEBASE_CLASSIFICATION.md`；一行需求模板見
`docs\CODEX_QUICK_REQUESTS.md`。
