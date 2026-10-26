# Claude → codex 交接：三個月跨股排名模型（U7）部署（2026-10-04）

這是一份獨立的交接檔，不接續任何舊交接檔。所有路徑的根目錄是
`C:\Users\USER\AppData\Local\Temp\claude\D--TWStockData\51ab15cb-7b36-4847-b785-5ba122936b29\scratchpad\three_month_impl\`（下稱 `impl\`）。
Claude 這邊全程沒有寫入 D:/stock、D:/TWStockData、D:/TWStockUpdater；所有修改都在 `impl\staging\programs`，要部署的位元組在 `impl\fix\merge\fu_final\merged\programs`。

## 1. 一句話

把個股三個月預測換成「全市場跨股排名」模型（共用模型 recipe `shared-xs-rank-v1`），並同步更新 Telegram caption、前瞻帳本、`find_python.cmd`／`CHECK_ENV.cmd`、weekend 預設模式、L8369 published 依據。已和你 12:43 的 D:/stock 三方合併，**0 衝突**；部署需要使用者逐步核准，請你在部署窗口內暫停相關檔案的修改（§5）。

## 2. 改了什麼

| 元件 | 內容 | 檔案（single_stock_analysis 以外另註） |
|---|---|---|
| 排名模型 | 7 個因子的跨股排名分數 S、置中 Sc、P = sigmoid(b·Sc)；P 是「跑贏同期中位數 63 日報酬的機率」；Mondrian（σ 五分位）80% 區間 | 新增 `src/tw_single_stock_three_month_xs.py`（**封存**）、`src/tw_single_stock_three_month_xs_io.py`（**訓練來源**，CRLF） |
| 報告整合 | 不顯示點報酬（內部 0）；標籤 相對偏強／相對偏弱／相對中性／不在模型適用範圍；信賴度固定「低」附理由；PDF 兩張表以標籤替換舊列（你加的「獨立原始收盤核對」會保留） | `tw_single_stock_3m_analyzer.py`（封存，已與你的 09:29 版合併）、`tw_single_stock_report_contract.py`、`tw_prediction_history.py`、`tw_single_stock_performance.py` |
| 共用模型與訓練 | 封印清單加 xs；`TRAINING_SOURCE_FILES` 加 IO；三個月改由全市場面板訓練；`--apply-run` 顯示 W1–W5；印出的套用命令帶正確 `--cache-dir`（明確帶 `--cache-dir` 訓練時，另印一行正式 cache 版） | `tw_single_stock_frozen.py`、`tw_single_stock_shared_model.py`、`tw_single_stock_train_shared.py`（皆封存） |
| 退役個股三個月 | `recipe_choice.SUPPORTED_MODES = ("tomorrow_open",)`；weekend 預設 `("tomorrow_open","comparable_t22")`，明確要求 three_month 在任何工作前以 `weekend_mode_retired:three_month` 失敗關閉 | `tw_single_stock_recipe_choice.py`、`tw_single_stock_weekend.py`（封存） |
| L8369 | `_validate_three_month_official_target_contract` 依合約自己的來源接受依據：published → `published_official_calendar_63rd_session`，其他 → Central；列日期合約、meta-feature 檢查、歷史快照同一規則 | analyzer；測試 `test_three_month_published_target_basis.py` |
| 前瞻帳本（REVIEW #4） | 每份三個月報告記錄當天全市場的 P／區間（不可改寫區塊）；結算與事先登記的檢討規則 v2；唯讀檢討工具 | 新增 `src/tw_single_stock_three_month_xs_ledger.py`（不封存、不是訓練來源）、`tools/three_month_xs_ledger_review.py` |
| Telegram | 三個月排名摘要的 caption 改成相對強弱、「跑贏同期多數股票的機率」、80% 區間與參考價格、信賴度低；舊摘要、tomorrow、provisional caption 逐字不變 | `telegram_report_delivery/telegram_stock_command_bot.py`、`README.md`、新測試 |
| CMD | `find_python.cmd` 的 analysis／analysis-test 檢查加 pyarrow（行尾原樣）；`CHECK_ENV.cmd` 加 pyarrow 並改為全 CRLF | `shared/find_python.cmd`、`shared/CHECK_ENV.cmd` |
| 其他 | `requirements_single_stock_3m.txt` 加 pyarrow；README；local_research orchestrator 不再蓋新的 MODEL_VERSION_3M | — |

## 3. 為什麼（驗證過的數字）

- **準確度**（正式 PIT 設定，7 個半年區塊，oracle 計分）：全市場準確率 0.5472（永遠看漲 0.4749）、Brier 0.2478、AUC 0.5701；50 檔準確率 0.5467、Brier 0.2487、AUC 0.5703，現行正式模型是 0.4991／0.2593／0.5083。訓練器 walk-forward W1–W5 全過；保留期 Brier 0.2452、AUC 0.579。誠實限制：Brier 只比 0.25 好約 1–1.5%，模型不判斷大盤方向，所以信賴度固定「低」。
- **區間**：Mondrian 讓最差波動度五分位的 |覆蓋 − 0.80| 從 0.094 降到 0.055，整體覆蓋約 0.76，報告寫「歷史實際覆蓋率約 76%（名目 80%）」。
- **實作正確性**：A1（本批重跑）與 oracle 逐列比對 1,038,944 列 bit-identical、G1–G7 相同；A4／A4b 候選與冷建置 xs 對面板差 0；帳本的 P 與區間與真實報告 bit-identical；帳本結算的 63 日標籤與封存面板在 272,695 列完全相同。
- **報告**：10-03 快照 2330 → P 0.5789「相對偏強」、80% 區間 −17.5%～+28.7%（約 2,040～3,185 元）；0050 → 不在模型適用範圍。
- **Telegram**：舊 bot 對同一份摘要會印「上漲機率：+57.89%」「預估三個月報酬：+0.00%」，新 bot 不會。
- **速度**：報告冷建置 23.9 秒、峰值 1.06 GB；暖讀 0.03–0.05 秒；帳本記錄 0.17 秒。
- **測試**：見 §4.3。

## 4. 檔案與補丁

### 4.1 位置

- `impl\patches\`：40 個檔的 diff（staging vs pristine）、`ALL.diff`；`deploy_vs_live\` 是合併樹 vs 你 12:43 的現況（部署實際會改的內容）與 `DEPLOY_ALL.diff`；`FILES.md`／`FILES.json` 有 pristine、base、current、merged 的 sha256、合併結果與 CR 數；`verify_apply.json` 有三種套用驗證（全部乾淨、byte 相同）。
- `impl\fix\merge\fu_final\merge_report.json`：合併明細，`sealed_and_training_sources` 是 18 個封存／訓練來源檔的 live 與 merged sha256。
- `impl\DEPLOY_PLAN.md`：完整部署步驟；`impl\FINAL_REPORT.md`：完整結果。

### 4.2 主要檔案的 merged sha256

| 檔案 | sha256 | 備註 |
|---|---|---|
| src/tw_single_stock_3m_analyzer.py | 95ab8439e40dc92bd3e0afc005dd1ae6d626d1a94bf6d6f217a91fed26a45e09 | 封存；你的 `5bdb7254…` + staging |
| src/tw_single_stock_three_month_xs.py | 1974f158073927990811d28c99fbdb5510b4cfabcd7bc73537f3d18c6d6b4ac0 | 封存，新檔，CRLF |
| src/tw_single_stock_three_month_xs_io.py | 331ce4b6134451c19056945135c31635388b1c9d19ec5fba5e2eae5b4be65e7d | 訓練來源，新檔，CRLF |
| src/tw_single_stock_three_month_xs_ledger.py | a6e14d6c40cd18741ce3a1730f14df75a72dc90f931268b55578c4852e71170d | 新檔 |
| src/tw_single_stock_train_shared.py | f0bf586a8cb97de1c354530d4c8f06a298187f984705f720d8518c027aca5911 | 封存＋訓練來源 |
| src/tw_single_stock_weekend.py | 50e3e57a189ac28feec82c5ccfdafd641cb9d5d58e2479971ff7a795b4b34799 | 封存 |
| src/tw_single_stock_frozen.py | 48a70b5ebbf31caf68b267ad51fdcd2fed84aa16116b7e40c3be0246c61e66b6 | 封存 |
| src/tw_single_stock_shared_model.py | 3d79b6588042b4e367d73d00131325449280acc77a1c8d6b18b4d2ce4238da04 | 封存 |
| tools/three_month_xs_ledger_review.py | 9bb8b2b9eb30d9f55cefd3b099c999084613dbb522a17c79da6a0461e27bb493 | 新檔 |
| telegram_report_delivery/telegram_stock_command_bot.py | feb39119d388e2b9358cd50a1106c94413bba5bba897c238064c403afa66c4cb | |
| shared/CHECK_ENV.cmd | 92dc2a01289361cdc54c320f497b50cfa0f1b9a698b1beac0da284ccd5a388de | 全 CRLF |
| shared/find_python.cmd | c8e98593f74f5fb0ad56cd8d1468564af0d46d2d6b7b3a7c10268d372a88fa73 | 混合行尾，原樣 |

其餘 28 個檔（測試、README、requirements、history／performance／report_contract／recipe_choice／local_research_orchestrator）見 `FILES.md`。

### 4.3 測試

完整測試，同一環境依序執行，逐 test id 比較（`impl\fu_final\out\compare_full_*.json`）：

| 套件 | 你的現況（theirs）→ 合併樹（merged） | 新增 | 只在合併樹失敗 | 只在 theirs 失敗 |
|---|---|---|---|---|
| single_stock_analysis | 1,428 → 1,589 個 id | 161 個全過（含帳本 50） | 0 | 0 |
| shared | 31 → 31 | 0 | 0 | 0 |
| telegram_report_delivery | 199 → 221 | 22 個全過 | 0 | 0 |

- 共同失敗（ssa 122、shared 6、telegram 32）在 pristine 就有；訊息不同的只差樹名、標籤或 tmp 路徑。
- pristine → staging 的比較結論相同（只在 pristine 有 1 個 local_research 暫時性失敗）。
- A1 oracle 嚴格比對重跑：1,038,944 列 bit-identical，G1–G7 相同。
- 12 次完整執行都沒有寫入 D: 的嘗試。

## 5. 部署順序，以及你需要暫停／重新檢查的事

**前提**：你的兩模式訓練（PID 50524）已結束，並告訴我們那個 run 要不要 apply；updater 回補閒置；記憶體 ≥ 6 GB。14:08 唯讀檢查時 PID 50524 已不存在，但最新的 run `models\shared_stock\training\shared_20261004_raw_reference_recovered`（11:43）沒有 `run_summary.json`：請說明它是中斷、仍在執行，還是要丟棄。在這之前 DEPLOY_PLAN 的 P2 不成立。

1. **請暫停修改**（直到下面第 7 步套用完成）：
   - 13 個封存檔：frozen、3m_analyzer、tomorrow_open、comparable、daily、weekend、published_single_stock_reference、cross_stock_calibration、shared_model、shared_training、train_shared、shared_comparable、three_month_xs；
   - 其他 5 個訓練來源檔：native_calibration、published_single_stock_projection、feature_sources、50_research、three_month_xs_io；
   - `FILES.json` 列出的 40 個部署檔（含 telegram bot、README、shared 兩個 CMD）。
   - 原因：訓練後若任何封存或訓練來源檔改變，`--apply-run` 會失敗關閉（封印不符或 `training_sources_changed_before_shared_publication`，train_shared L993），而此時程式碼已換掉，三個月與 tomorrow 報告會停擺到重訓為止。你在 11:23 之後改過 `published_single_stock_projection.py`、`tw_single_stock_feature_sources.py`（都是訓練來源），所以這次才把檢查擴大到 18 個檔。
2. **重新合併**（如果你在 12:43 之後又改了任何上述檔）：`python -B impl\fix\rebase_merge.py <label>`、`python -B impl\fix\make_patches.py <label>`，再在合併樹跑完整測試。
3. **每一步之前的檢查**：`python -B impl\fix\precheck_live.py <label>`（唯讀）在訓練前、換檔前、套用前各跑一次，`drift` 必須是空的；換檔後用 `--deployed`，確認 40 個檔與 18 個封存／訓練來源檔都等於 merged sha256。
4. **備份**（核准後）：改動的 live 檔、18 個封存／訓練來源檔、`models\shared_stock\current.json`。
5. **在部署副本上訓練兩個模式**（核准後）：把 merged 樹位元組複製到 `D:\TWStockData\program_data\dstock_single\deploy_staging\<UTC>\programs\`，用副本的 `tw_single_stock_train_shared.py --modes three_month tomorrow_open --run-dir ...`（不帶 `--cache-dir`）。QA 依 DEPLOY_PLAN §3.4，預期警告：selection 的 Brier（0.2502）與 selection／holdout 的 MAE（點估計固定 0）。
6. **換檔**（核准後）：位元組複製 40 個檔，不要用 `git apply`（這台 `core.autocrlf=true` 會改 CRLF 檔，例如 IO 模組，一變 sha 就是訓練來源不符）。跑 `RUN_TESTS.cmd`（single_stock_analysis、telegram）。
7. **套用**（核准後）：用訓練印出的 `[apply command]`（已帶 `--cache-dir "D:\TWStockData\program_data\dstock_single\cache"`）。
8. **重啟 Telegram 指令 bot**（核准後）：`RESTART_COMMAND_BOT_SERVICE.cmd`，再 `CHECK_COMMAND_BOT_STATUS.cmd`。新 bot 對舊摘要逐字不變，可以先於模型上線。
9. **套用後 smoke**：2330、一檔上櫃、0050；確認帳本在 `D:\TWStockData\program_data\dstock_single\cache\universe_xs_ledger\` 寫入第一個區塊，檢討工具 `--print-only` 回「資料不足，尚未判定」。
10. **退役個股三個月凍結模型**（核准後）：DEPLOY_PLAN §8 的資料層級步驟（搬移，不刪除）。

**其他要注意的**：
- **不要執行 live 的 `shared\CHECK_ENV.cmd`**，直到 staging 版部署：現行檔混用 LF／CRLF，在 `chcp 65001` 下從第 29 行起每行晚 8 bytes 讀取，會開啟 `D:\TWStockData\contracts\shared_market_data\requirements_v1.json` 並顯示錯誤結果。只有 README 列它為手動入口。
- 回滾時**不要**還原 `tw_prediction_history.py`、`tw_single_stock_performance.py`、`tw_single_stock_report_contract.py`（舊版會把排名模型紀錄全部算成未命中），也不要還原 `CHECK_ENV.cmd`。
- 改 analyzer 時若改名舊預測列（三個月預測方向、上漲機率、三個月預估報酬、信賴度、最新收盤價、三個月情境價格、歷史三個月報酬中位數／10%～90% 區間、偏多／基準／偏空情境、歷史壓力區間），要同步改 `_XS_LEGACY_PRICE_ROW_LABELS`／`_XS_LEGACY_SCENARIO_ROW_LABELS`。在「價格基準」之後加列不受影響。

## 6. 讀取政策例外請求（REVIEW #7）

- 三個月報告的冷建置（每個發布快照第一次）會**串流讀完 1.30 GB 的 `official_market_daily`**：只解析 3 欄、每塊 64 MB、驗證 sha256／大小／筆數，程序峰值約 1.06 GB，約 24 秒；之後同一快照的報告只讀 `universe_xs\<key>\` 的 2.2 MB 結果（0.03–0.05 秒）。
- 這超過 consumer 每檔 512 MiB 的讀取上限（雖然記憶體受限於區塊大小），所以是讀取政策的例外。請你與使用者確認，並寫進讀取政策文件。PDF 已主動揭露排名模型讀了哪些輸入（route、sha256、大小，以及官方每日行情是串流讀取），避免和 provisional 區塊「整檔未讀」的診斷互相矛盾。
- 若不同意這個例外，替代方案是由 updater 端產生一個只含 3 欄的小型衍生物件，但那需要改 updater 與 IO 模組，並重訓。

## 7. 前瞻帳本與事先登記的檢討門檻（v2）

- **記錄**：每份三個月報告（任何一檔）在取得截面後記錄當天全市場；同一 entry key 只寫一次；失敗只記原因碼，不影響報告。位置 `<cache_root>\universe_xs_ledger`；`TW_SINGLE_STOCK_XS_LEDGER_DIR` 改位置，`TW_SINGLE_STOCK_XS_LEDGER=off` 關閉；每交易日約 0.32 MB，永久保留。
- **工具**（唯讀，建議每季執行）：`python D:\stock\programs\single_stock_analysis\tools\three_month_xs_ledger_review.py [--print-only | --out-dir DIR]`。是否要包一個 CMD 由你決定（決策 10）。
- **規則 v2**（sha256 `947cdbef1476f912dd550ebc789fc95c20ed9a420596ff56dbb87b955fe559b0`，2026-10-04 登記，取代同日的 v1 `f40e123d…`；兩者都在任何前瞻紀錄之前）：
  - 有效區塊 = 63 個交易日的決策窗、已到期、合格股結算樣本 ≥ 500 筆、**決策日 ≥ 10 個**；至少 3 個有效區塊才判定，否則「資料不足」。
  - 兩個視窗：全部有效區塊；最近 4 個有效區塊。
  - 門檻（區塊平均）：排名相關 > 0、AUC > 0.5、Brier < 0.25、80% 區間覆蓋率 70%–86%。
  - **建議啟動回滾決策**：任一視窗排名相關與 AUC 都未過、或三項以上未過、或覆蓋率 < 60% / > 92%；全部區塊 Brier ≥ 0.252、「跑贏同期多數」正確率不高於「一律判斷會跑贏」、任一波動度分組覆蓋率 < 70%（REVIEW #4 三項）。
  - **需要注意**：任一視窗有門檻未過；或最近兩個有效區塊排名相關與 AUC 都未過。
  - 校準（樣本內，只檢查誤報與偵測力）：每日記錄時 11 個季區塊沒有誤報 rollback；每區塊 ≥ 10 個報告日時 0／900 次誤報；模型完全失效後約 4 個區塊內達到 rollback（每日記錄）。
- **需要你協助的營運前提**：帳本只在有人跑三個月報告的日子記錄。要讓每個區塊有 ≥ 10 個決策日，最簡單的是**每個交易日跑一份任一股票的三個月報告**（例如排程）；要不要加排程由你與使用者決定。

## 8. 你可以順手處理、但不阻擋部署的事

1. 歷史快照仍不是正式資格的另外兩個原因（REVIEW #4，不在本案範圍）：`run_analysis` 用 `build_three_month_execution_envelope(None, …)` 建的 cost 預設 `actionable = False`；價格基準 mapping unsupported 也會擋。前瞻帳本不依賴歷史快照。
2. telegram 測試中 13 個 `test_telegram_report_sender` 失敗是測試資料過時（缺 `tomorrowTop10`、錯誤訊息不同），pristine 也一樣失敗，與本案無關。
3. 測試環境：`test_local_research_projection`／`test_local_research_snapshot` 會比對 tmp 的所有祖先目錄 mtime，basetemp 在 `%TEMP%` 底下就會隨機失敗。用 `C:\Users\USER\<資料夾>` 當 basetemp 就穩定；**不要**用 `C:\Users\USER\AppData\Local\<資料夾>`：從 Claude 桌面程式啟動的行程寫到那裡會被重新導向到 `D:\WpSystem\...\Packages\Claude_pzs8sxrjxfjjc\LocalCache\Local\`。
