# 單股分析

本資料夾是 D-stock 個股 C0 收盤估計工具。根目錄主要入口 `1_執行個股分析與產生報告.cmd` 以同一份已發布快照產生 H1 與 H63 兩份收盤估計報告（`--both-horizons`），並顯示實際產生的 PDF／JSON 路徑。

- 雙擊入口：`1_執行個股分析與產生報告.cmd`（H1＋H63）。`RUN_SINGLE_STOCK_3M_ANALYSIS.cmd` 只產生 H63、`RUN_SINGLE_STOCK_TOMORROW_OPEN.cmd` 只產生 H1；檔名中的「3M」「TOMORROW_OPEN」是相容名稱，現在代表「訊號收盤至同市場第 63／第 1 個交易日收盤」，不是舊的三個月模型或次日開盤模型。`07_D-stock個股報告.cmd` 只轉呼叫唯一的 `1_*.cmd`。
- 生產程式：`src\run_single_stock_entry.py`（只分派 H1／H63）→ `src\tw_single_stock_c0.py`；讀取層為 `src\stock_data_sources.py`、`src\published_single_stock_projection.py`、`src\stock_query_projection.py`；模型、輸入驗證與 forward 帳本在 `D:\stock\programs\shared`（`stock_daily_contract.py`、`stock_daily_linear.py`、`stock_daily_forward.py`）。
- 工具與維護入口：`tools\*.cmd`（`tools\RUN_SINGLE_STOCK_*.cmd` 轉呼叫根目錄同名入口，`tools\RUN_TESTS.cmd` 執行測試；已退役的工具不在本工作區）
- 測試：`tests`（已退役的測試不在本工作區；`tests\conftest.py` 仍忽略 `_retired` 資料夾名稱）

入口固定設定 `DStock_SHARED_RAW_NETWORK_DISABLED=1`、`ENABLE_FINMIND_FETCH=0`：分析不連網、不抓 FinMind，只讀中央已發布資料。根目錄不依賴目前工作目錄。

## 日期格式

日期一律 YYYYMMDD（8 位數字，例如 20261002）：PDF、主控台與「資料時間」行顯示的日期都是 YYYYMMDD，日期時間顯示為 `YYYYMMDD HH:MM:SS`（台北時間），共用輔助模組是 `shared\date_format.py`。報告 JSON 的機器欄位（例如 `latest_data_date`、`target_session_date`、`data_time`）維持 YYYY-MM-DD；讀取時兩種寫法都接受，並以日期值比較。

## Python 環境

入口以 `shared\find_python.cmd analysis` 尋找 Python，只接受 `D:\stock\.venv\Scripts\python.exe`，並確認 `numpy`、`pandas`、`reportlab` 可用。若設定 `STOCK_ANALYSIS_PYTHON`，它必須正好指向同一路徑，否則視為找不到 Python，入口以 exit 2 停止；不搜尋 PATH、Anaconda 或其他 venv。舊說明中的 `STOCK_PYTHON` 優先序、`D:\stock\GitHub\.venv` canonical 環境與 `STOCK_ALLOW_LEGACY_PYTHON` 旗標，現行入口都不再讀取。

依賴安裝與修復：`D:\stock\.venv` 由 `D:\stock\programs\environment_setup` 管理，執行 `environment_setup\INSTALL_D_STOCK_ENV.cmd`（以 `environment_setup\requirements.txt` 安裝並 `pip check`；加 `/CHECK` 只自我檢查、不安裝），或從 `STOCK_TOOLS.cmd` 的 `M1` 進入。舊的 `INSTALL_SINGLE_STOCK_3M_DEPS.cmd`（目標固定為 `D:\stock\GitHub\.venv`）與它專用的 `requirements_single_stock_3m.txt` 已於 2026-10-12 退役並移到 `tools\_retired\`；2026-10-27 納入 Git 時未包含 `_retired`，2026-10-30 本機 `VERIFY_STABLE_PROGRAM_PATHS.cmd` 也確認 `tests\_retired`、`tools\_retired` 已不在本工作區，請勿重新使用。

## 資料與輸出

- 模型：載入共用 active C0 模型（`stock_daily_contract.load_active_c0_model`，`consumer_id='single'`）。`--c0-model`、`--forward-db` 只供 QA，報告會標記 `qa_override`。
- 資料：一次執行只 pin 一次已發布 head（`D:\TWStockData\shared_market_data`），解析股票與讀取面板都使用同一個 context；讀取途中快照改變（`c0_analysis_snapshot_changed`）會停止。
- `--pinned-head-sha256 <sha>`：使用呼叫端（例如 Telegram Bot 工作開始時）pin 的 immutable head `_manifests\<sha>.json`。該 head 已不存在時改用目前 head，並記 `pinned_head_unavailable_used_current` 標籤；檔案存在但內容 hash 不符仍視為竄改並停止。
- 只分析目前已驗證的上市櫃普通股（current identity 的正向紀錄）；ETF、ETN 與分類未知的代號不會被解析成可分析標的，會以「需要恰好一檔目前普通股」停止。
- 輸出：`D:\TWStockData\program_data\dstock_single\reports\c0\c0_<代號>_h<H>_<時間>.pdf` 與同名 `.json`（`--output-dir` 可改）。先寫 PDF，再寫封存的 JSON，之後執行 `validate_c0_estimate`，驗證結果寫回 JSON 的 `verification`。
- 主控台最後印出一行「資料時間」與 `PDF：`、`JSON：` 兩行實際路徑（Telegram Bot 只依這兩行取檔）。
- `--resolve-stock`／`--resolve-only` 只解析股票，不產生報告；`--output-json` 寫出解析結果。

## H1 開盤前版（2026-10-18 起）

- H1 報告優先顯示「開盤前版」（`stock_daily_preopen`，HGB，含前一晚美股）快照中本股那一列：校準後上漲機率 `p_up`、預估收盤報酬 `exp_ret`、全市場名次與可交易母體名次、前一晚美股是否收漲、`describe(meta)` 與必要揭露；t 日籌碼未更新或中央標示不完整時另有一段說明 `chips_note_zh`（例如「t 日籌碼未更新：融資券最新到 20261015（以舊值推論，準確度可能下降）」，只說明、不阻擋；20261018 使用者裁示）。H63 維持原本來源。
- 只透過唯讀 API `D:\stock\programs\shared\stock_daily_preopen_reader.py` 讀取（預設根目錄 `D:\TWStockData\program_data\stock_daily_preopen`，測試可用 `STOCK_DAILY_PREOPEN_ROOT` 改寫）。實作在 `src\single_stock_preopen.py`。
- 新鮮度：快照 `signal_date` 必須等於本次單股分析本股所屬市場的資料日。快照缺檔、過期、雜湊不符、讀取失敗、沒有本股或市場別不同時，回退 C0 基準，報告醒目標示「開盤前版不可用（原因代碼），本次 H1 改用 C0 基準」。
- 來源開關：`--h1-source auto|preopen|c0`，未指定時讀環境變數 `DSTOCK_SINGLE_H1_SOURCE`，預設 `auto`。`preopen` 不可用即停止（rc=2，不寫任何報告）；`c0` 為舊行為（不讀快照、不加入 `preopen_h1` 區塊，報告與改版前相同）。候選研究模式（`--candidate-model`）不接受此參數。
- 兩條路徑：C0 路徑（`--c0-model` 或沒有單股啟用指標時）在 `tw_single_stock_c0.py` 內加入 `preopen_h1` 區塊後才封存（`estimate_binding_sha256`）；選定模型路徑由單股層 `single_stock_preopen.selected_h1_overlay` 包住 shared 的 `generate_selected_reports`，只在 `stock_candidate_report.write_candidate_report` 產製正式 H1 PDF 前加入同一區塊並重算 `runtime_payload_sha256`。原本的 C0／選定模型機器欄位（`prediction`、`prob_up`、`expected_return`）不變，仍由原驗證程式重現；開盤前版只以 `run_id`、`snapshot_sha256`、`model_sha256` 留下身分，不在重現範圍內。
- 必要揭露依快照 manifest（`latest.json`）的 `recipe.name` 選擇（`single_stock_preopen.DISCLOSURES`／`disclosure_for(meta)`；只換揭露文字，其他行為不變）。區塊的 `meta.recipe` 保留快照原值（舊快照沒有此鍵）。
  - `h1pre_final_nogc_xs`（v1；manifest 沒有 `recipe` 欄位的舊快照也視為 v1，內容與改版前逐字相同；資料出處 `D:\TWStockData\program_data\stock_daily_c0\candidates\calibration_20261018\report\校正報告_20261018.md`）：預測下一交易日收盤相對訊號日收盤的方向；保留期（2026-01～10）方向準確率 59.0%（一律猜跌 55.9%）、AUC 0.608；只在前一晚美股收漲日選出的可交易 Top10 命中 63.5%，全部日子 55.6%；隔日開盤買進 Top10 平均約 -0.04%／日，命中率不代表交易獲利。
  - `h1pre_v2_ens`（v2；資料出處 `D:\TWStockData\program_data\stock_daily_c0\candidates\h1_improve_20261021\report\H1改良研究報告_20261021.md` 第 6 節）：預測下一交易日收盤相對訊號日收盤的方向；保留期 2026-01-02～2026-10-19（191 個交易日）方向準確率 60.0%（一律猜跌 56.1%）、AUC 0.619；前一晚美股收漲日選出的可交易 Top10 命中 65.3%，其他日子 49.1%，全部日子 57.1%；可交易 Top10 預估收盤報酬 MAE 2.71%；隔日開盤買進 Top10 平均約 +0.01%／日（約為零，不是負值），命中率不代表交易獲利。
  - 其他名稱，或 `recipe` 不是物件、缺 `name`：只顯示中性說明「配方未知」與配方名稱（只留可列印 ASCII、最長 64 字），不顯示也不沿用任何配方的保留期數字；H1 仍照常使用開盤前版該列。

## 版本標籤與資料時間（不阻擋）

- 版本或綁定差異（模型、policy、adoption、head 在 pin 之後更新等）只記在 JSON 的 `version_labels`；PDF 顯示標籤數量與代碼，不停止分析。
- 每份報告都有 `data_time`：分析資料日（各市場）、價格最新日、本股最後價格日、發布者宣告的發布時間（未驗證）、讀取時間、報告產生時間、模型訓練資料日與啟用時間。資料非最新，或已驗證日曆之後的平日無法確認是否為交易日時，PDF 會明確標示。
- 逐市場資料日：本股以自己市場的資料日（`asof_by_market`）建面板與估計。本股市場落後其他市場時，記 `stock_market_asof_lags_latest_market` 標籤並照常估計，不排除；其他市場非最新不會讓本股被標成非最新。
- 分析資料日不晚於模型訓練資料日時標示「樣本內」（`in_sample_lookahead`），此估計不計入 forward 統計。
- 無法估計（例如本股價格停在分析資料日之前、最近 253 個交易日價格不完整、日曆未驗證、模型未訓練此期限）時，照樣輸出 PDF／JSON（`report_class=c0_no_estimate_data_time_only`），列出原因與該股最後價格日，不含任何估計值。

## 結束碼與保留的檢查

- 0：已輸出報告（包含「無法估計」報告）。
- 2：完整性或內容錯誤（`C0IntegrityError`、竄改、快照在讀取途中改變、查詢不是恰好一檔目前普通股、預測值非有限或超出範圍）；入口找不到 Python 或主程式時也是 2。
- 3：資料或執行環境暫時無法讀取（檔案、匯入或讀取層 `ProjectionError` 等），不是內容判定。
- 其他未預期的 `RuntimeError` 保留 traceback（rc 1）。

完整性與竄改、ETF／非普通股排除、交易日曆、253 日窗與防前視（`build_panel` 拒絕分析日之後的列）等檢查不因不阻擋原則放寬。

## 已退役的說明

本 README 先前描述的三個月橫斷面排名模型（`shared-xs-rank-v1`）、明日開盤模型、三個月前瞻帳本每日排程（`RUN_THREE_MONTH_XS_LEDGER_DAILY.cmd`）、三竹背景參考、候選特徵消融、模型 A/B 稽核（`RUN_SINGLE_STOCK_MODEL_AUDIT.cmd`）、`tw_industry_data.py`／`tw_prediction_history.py` 等模組，以及 ETF／ETN 單代號分析與 tick 合法化，都已隨 C0 改版退役，現行 `src`、`tools` 中已不存在。程式原檔只留在備份（`backups\20261005T135417Z_v2_final\`、`backups\20261006T201132Z_dates_v1\`）；修改前的 README 另存於 `D:\cowork\backups\nonblock_20261012\stock\programs\single_stock_analysis\README.md`。
