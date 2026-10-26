# D:\stock 程式碼責任分類

本分類以 2026-08-30 工作區實際存在的目錄為準。全市場分析與中央資料不是 D-stock
內部程式；它們的 owner 分別是 `D:\MIX All` 與 `D:\TWStockData`。分類文件不授權
搬移、刪除或重新啟用退役內容。

## 分類定義

| 類型 | 責任 | 不負責 |
|---|---|---|
| `analysis` | 個股特徵、模型、預測、校正與推薦排序 | 資料下載、報告排版、Telegram 傳送 |
| `reporting` | 讀取既有 payload，產生或驗證報告成品 | 偷改推薦、重新訓練或發布 |
| `data` | 路徑、receipt、來源資料介面、快取與品質檢查 | 取代 Central owner、直接決定推薦 |
| `delivery` | 傳送已驗證的個股或 MIX 報告 | 修改分析結果或建立第二份報告 |
| `runtime` | CMD、Python 尋找、環境設定與流程編排 | 模型公式或中央資料所有權 |
| `governance` | 固定路徑、文件、驗收與清理契約 | 執行市場計算 |
| `tests` | 單元、契約與跨系統驗證 | 正式資料、服務或報告發布 |
| `research` | GitHub、GPTlogic、研究快照與候選方法 | 正式推薦輸入 |
| `evidence` | 歷史 log、receipt、工作樹與發布證據 | 現行執行入口 |

## D-stock 現行路徑

### 分析與報告

| 路徑 | 分類 | 邊界 |
|---|---|---|
| `programs\single_stock_analysis\src` | `analysis`／`data`／`reporting` | 個股三個月與次日開盤；只讀中央資料契約 |
| `programs\single_stock_analysis\tools` | `runtime`／`research` | 個股執行、稽核與研究快取工具 |
| `programs\single_stock_analysis\tests` | `tests` | 個股模型、PIT、報告與來源契約測試 |
| `programs\telegram_report_delivery` | `delivery`／`runtime` | Bot、MIX 報告傳送、狀態與排程定義 |
| `programs\telegram_report_delivery\tests` | `tests` | 傳送與服務契約測試 |

個股固定入口為：

```text
programs\single_stock_analysis\1_執行個股分析與產生報告.cmd
programs\single_stock_analysis\tools\RUN_SINGLE_STOCK_3M_ANALYSIS.cmd
programs\single_stock_analysis\tools\RUN_SINGLE_STOCK_TOMORROW_OPEN.cmd
```

Telegram 只處理既有成品，主要入口為 `RUN_COMMAND_BOT.cmd`、
`CHECK_COMMAND_BOT_STATUS.cmd`、`RUN_SEND_LATEST_REPORT.cmd`、
`RUN_SEND_MIX_REPORT.cmd` 與 `SHOW_MIX_WEEKDAY_0800_TASK.cmd`。

### 共用資料與執行支援

| 路徑 | 分類 | 邊界 |
|---|---|---|
| `programs\shared` | `data`／`runtime` | 共用資料路徑、中央讀取介面、來源轉接與環境偵測 |
| `programs\shared_data_management` | `data`／`governance` | Central receipt、owner seal 與路徑完整性驗證 |
| `programs\environment_setup` | `runtime` | Python、Windows 環境及外部 GitHub 來源管理 |
| `programs\cross_system_validation` | `governance`／`tests` | 只讀跨系統 acceptance source 與 contract checks |
| `programs\maintenance` | `runtime`／`governance` | 清理計畫、schema 與受控維護工具 |
| `programs\STOCK_TOOLS.cmd` | `runtime` | 只作現行入口選單，不複製外部程式 |
| `programs\STABLE_PROGRAM_PATHS.txt` | `governance` | 現行程式／測試固定路徑唯一清單 |

## 外部 owner 與資料責任

```text
D:\TWStockData\shared_market_data
    ↓ Central owner 的共用原始行情與參考資料
D:\MIX All
    ↓ 全市場分析、正式報告與 MIX 模型
D:\stock\programs\single_stock_analysis
    ↓ 個股分析與個股報告
D:\stock\programs\telegram_report_delivery
    ↓ 已驗證成品的 Telegram 傳送
```

`D:\stock\config\shared_data_paths.json` 只登錄中央資料路徑；不得把根目錄本機
複本當成正式資料。`D:\stock\.venv` 是仍被 Telegram 服務使用的執行環境；
`D:\stock\GitHub`、`D:\stock\GPTlogic` 是研究／安裝來源；它們不直接成為正式
推薦模型的輸入。

## 根層資料、文件與證據

| 路徑 | 分類 | 說明 |
|---|---|---|
| `config` | `governance`／`data` | 共用資料路徑與執行設定 |
| `docs` | `governance` | HANDOFF、SOURCE_REGISTRY、ADR 與操作界線 |
| `logs\daily_analysis_scheduler` | `evidence` | 2026-08-30 live observation 已確認不存在；僅作歷史分類參考，不是目前排程或 live source |
| `record` | `evidence` | 歷程與保留證據 |
| `release_worktrees` | `evidence`／`research` | 發布工作樹；保留其現況與未提交變更 |
| `releases` | `evidence` | 發布與安裝包證據 |
| `_DELETE_OLD_CONTENT_20260830` | `evidence`／`retention` | 歷史隔離區名稱；2026-08-30 live observation 已確認不存在，不作入口 |

## 明確退役邊界

- `D:\stock\programs\full_market_analysis` 已退役且不存在；不屬於現行 D-stock 分類，
  全市場責任在 `D:\MIX All`。
- `D:\stock\programs\daily_analysis_scheduler` 已退役且不存在；現行工作流在
  `D:\TWStockData\services\workflow_scheduler`。
- `programs\shared\UPDATE_GLOBAL_MARKETS.cmd` 已移出現行路徑，不得恢復為共用核心
  或選單入口。
- `logs\daily_analysis_scheduler` 曾屬歷史 `evidence`；2026-08-30 live observation
  已確認不存在，不是現行 scheduler、恢復來源或清理待辦。
- `_DELETE_OLD_CONTENT_20260830` 只屬歷史隔離區名稱；2026-08-30 live observation
  已確認不存在，不得作為入口或恢復來源。

## 依賴方向與驗收

允許的主要方向為：

```text
中央來源／source adapter → data → analysis → reporting → delivery
                                  ↑
                         runtime 只負責編排
governance、tests、evidence 只做規則與證據，不成為推薦輸入
research 只供人工採納，不直接進入正式流程
```

固定程式路徑使用 `programs\VERIFY_STABLE_PROGRAM_PATHS.cmd` 驗證，共用資料設定
使用 `programs\VERIFY_RUNTIME_DATA_PATHS.cmd` 驗證。跨系統正式狀態以
`D:\TWStockData\services\acceptance\latest.json` 為準；`blocked`、`fail` 或缺失
時維持未完成，不以文件或 exit code=0 取代正式驗收。
