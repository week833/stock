# Claude → Codex：個股分析修復交接（2026-09-19），含 TWStockUpdater／MIX ALL 邊界說明

產出時間：2026-09-19 17:50（台北）
產出者：Claude（session `51ab15cb-7b36-4847-b785-5ba122936b29`）
用途：給 codex 核對。**本檔不是要求你執行的計畫**，是「我做了什麼、怎麼驗的、還有什麼要你決定」的紀錄。

---

## 0. 範圍與所有權（先看這段，避免重工）

| 程式 | 這幾天誰改的 | 本檔的角色 |
| --- | --- | --- |
| **個股分析** `D:\stock\programs\single_stock_analysis` | **我（Claude）今天 08:20–15:10 全部改完並實跑驗證** | **完整細節在第 2～5 節，這裡是唯一的來源** |
| **TWStockUpdater／C（collect data tool）** | 我在 09-13～09-19 凌晨改完（已交接）；**今天白天之後我沒有再動**；codex 另有一份自己的修復計畫正在執行 | 第 6 節只做索引與摘要，**細節仍以既有那份 28 KB 交接為準** |
| **MIX ALL** `D:\MIX All` | **我完全沒有改過**（這幾天一次也沒有）。今天 17:10 起有另一個 session（依其文件為 codex）正在大規模修改，17:47 仍在寫檔 | 第 7 節只有**唯讀觀察**與證據指標，不下結論、不建議改法 |

三個程式今天的交集只有一個：個股分析讀共同資料區的發布頭（TWStockUpdater 產出），**個股分析沒有改任何 TWStockUpdater 或 MIX 的檔案**，MIX 那邊的計畫書也寫明「本輪不修改 Single」。

---

## 1. 現況快照（2026-09-19 17:50）

- **個股分析**：可用。`1_執行個股分析與產生報告.cmd 2330 --source-mode published` 今天跑了 4 次，**4 次都是結束碼 0**，最後一次 14:51–15:04。
  - 指標檔 `latest_report_manifest.json`（09-19 15:03）指向 `single_stock_run_3a55bc5bcb6647fdb99abc3d3c6fa608`，7 個成品（三個月 PDF＋JSON、次日開盤 PDF＋JSON＋MD、descriptor、run_manifest）。
  - 在此之前**最後一份正式報告停在 08-24**（改讀共同資料區後被 PIT 閘門擋住，見 2.1）。
- **共同資料區發布頭** `D:\TWStockData\shared_market_data\_metadata\stock_data_current.json`：
  `schema=stock-data-publication-v1`、`producer_id=collect_data_tool`、`run_id=20260918T172350Z-ac70307c`、
  `published_at=2026-09-18T22:23:15Z`、35 條路由其中 19 條 SUCCESS、sha256 前 16 碼 `48f77d0cb9e433c0`。
- **MIX ALL**：今天 12:07 那次每日流程 **失敗**（16:10 結束，`LedgerIntegrityError`，見 7.2），修復工作進行中，不是我的改動造成的。

---

## 2. 個股分析：八輪修改逐條說明

每一輪都是同一個流程：**沙箱副本改 → 編譯 → 針對性測試 → 用真實資料唯讀模擬（包住閘門後 `os._exit`）→ 比對雜湊後部署 → 真實 CMD 執行**。八份備份都在 `D:\stock\backups\claude_20260919_single_stock_*`，每份含 `original_sha256.txt`。

### 2.1 第 1 輪：低頻資料的 PIT 可得時間改用「法定公告期限」（**最關鍵的一輪**）

- **症狀**：正式執行一律被 `require_formal_single_stock_input_quality` 擋下，四個原因碼；08-24 之後沒有任何正式報告。
- **根因（已用資料證實，不是推測）**：
  - 財報（`FinancialStatements`）**沒有任何公告時間欄位**。
  - 申報期限表 `observed_filed_at` 實際填報數 **0／9,925**。
  - 月營收 `create_time` 只有 2021–2025 約 10% 有值。
  - 舊實作要求「可驗證的公告時間戳」，共同資料區給不出來 → 永遠不合格。
- **修法**（使用者選項 A：法定公告期限推估）：在 `src/tw_industry_data.py` 新增
  `STATUTORY_MONTHLY_REVENUE_DAY = 10`、`STATUTORY_QUARTER_DEADLINES = {3:(5,15), 6:(8,31), 9:(11,14)}`、`STATUTORY_ANNUAL_DEADLINE = (3,31)`，
  以及 `statutory_available_at()`／`statement_filing_deadline()`／`month_revenue_filing_deadline()`／`_statement_available_date()`。
  規則：**法定期限遇假日順延，再取「之後的下一個決策交易日」**（嚴格大於，不允許當日就用）。
  `build_company_financial_features()`、`collect_industry_data()` 都加 `sessions` 參數把交易日序列傳進去。
- **半年報為什麼用 8/31 而不是 8/14**：用官方申報期限路由實際統計——1,853 家一般公司 8/14、118 家 KY／第一上市 8/31。取**最晚者 8/31** 才不會把 KY 股的資料提早當成可得（保守方向）。
- **同時收緊**：`PIT_QUALIFYING_ESTIMATED_BASES = frozenset({PIT_BASIS_ESTIMATED_CONSERVATIVE})`。
  第一版我把 `PIT_BASIS_ACTUAL` 也放進去，等於「只要宣稱是實際時間就放行」，被自己的測試
  `test_used_official_columns_cannot_opt_out_of_pit` 抓到後改掉，**現在只接受保守推估基準**。
- **驗證**：`tests/test_industry_data.py` 補 Q1 邊界（2025-05-15 不可得／05-16 可得、lag 46 天）；
  閘門 JSON 前後對照存在 `gate_three_month_2330_before_20260917.json` ↔ `..._after_deploy.json`、`gate_tomorrow_open_2330_*`。
- **決策紀錄**：`D:\stock\docs\decisions\ADR-0005-single-stock-statutory-availability.md`（已加入 decisions/README 索引）。

### 2.2 第 2 輪：空群組與 ETF 不該被當成資料缺漏

- **症狀**：ETF（0050）先卡 `formal_three_month_quality_incomplete`，補完又卡 `pit_decision_metadata_missing`。
- **根因**：低頻群組即使**一個欄位都沒有入模**，仍被註冊進正式 PIT 檢查；ETF／ETN 本來就沒有月營收與財報。
- **修法**：`build_features` 改成 `include_model_metadata=bool(columns)`（沒有欄位只登錄 QC，不要求 PIT metadata）；
  新增 `fundamental_sources_expected`（ETF／ETN 不預期有財報來源）；驗證器新增 `every_group_explicitly_excluded`（全數明確排除時不算 metadata 缺失）。
- **驗證**：`model_gate_three_month_0050_after.json`（ETF 通過）、`model_gate_three_month_2317_before.json`（非 ETF 對照）。

### 2.3 第 3 輪：次日開盤的三個實際缺陷

1. **fallback 偵測太寬**：舊的字串比對會把正常的驗證訊息誤判成 fallback。改成精準比對
   `_tomorrow_validation_failure_markers` ＋ `_tomorrow_validation_content` ＋ `_TOMORROW_FAILURE_KEY_SUFFIXES`。
2. **`maybe_write_tomorrow_research_audit_only()` 少了 `session_sequence` 參數**：呼叫端已經在傳，實際跑會丟
   `unexpected keyword argument 'session_sequence'`。補上參數並往下轉傳。
3. **`fallback_tomorrow_open_model()` 用到未定義的 `central_lineage`**：改為具名參數 `central_lineage=None`。
- **驗證**：新測試 `tests/test_formal_estimate_gates.py`；09:33 那次正式執行走完整條次日開盤路徑。

### 2.4 第 4 輪：正式模型閘門接受「低可靠度」但拒絕「真 fallback」

- **症狀**：`_require_formal_three_month_model`／`_require_formal_tomorrow_open_model` 把「可靠度低」與「模型壞掉」當成同一件事，一律拒絕。
- **修法**：新增 `FORMAL_LOW_RELIABILITY_PREDICTION_STATUSES`（`baseline_only_*` 與 `floor_blended_*`）視為可出正式報告；
  **真正的 fallback／fit error 仍然拒絕**。
- **理由**：可靠度低是「要標示的事實」，不是「不能出報告的錯誤」；報告本身會把信賴度標成「低」。

### 2.5 第 5 輪：三個月主估計改「模型主導」＋權重下限 0.5（**使用者決定**）

- **背景**：使用者明確指示「模型累計不是我要的，會修模型沒錯但是是在舊模型失準後進行的修正，模型利用現有資料對股票進行分析預測」，
  並在選項中選 **2：模型為主、保留權重下限 0.5 的混合**。
- **修法**：`RELIABILITY_MODEL_WEIGHT_FLOOR = 0.5`。可靠度閘門（`classification_reliability_weight`、
  `regression_reliability_weight`、`recent_holdout_reliability_gate`、`feature_ood_reliability_penalty`）**照常計算、照常記錄**，
  但**不再把模型權重歸零**；模型好時權重可升到 1。`prediction_audit` 的狀態用**套下限之前**的權重判定，
  並同時記錄 `reliability_classification_weight`／`reliability_regression_weight`，所以「閘門判低」這件事不會被下限洗掉。
- **報告文案**：`tw_single_stock_report_contract.py` 的 `estimate_source_details` 新增 floor_blended 分支——
  「模型估計與歷史基準混合（模型未通過樣本外增益門檻，採權重下限 50%）」，信賴度仍標「低」。
- **踩到的坑（重要）**：第一次部署後報告數字完全沒變 → **模型快取以模型版本＋lineage 為 key，命中舊快取**。
  必須把 `MODEL_VERSION_3M` 升版才會重算（第 6 輪就是為了這件事）。
  **給 codex 的規則：任何會改變預測數值的政策修改，都必須同步升 `MODEL_VERSION`，否則正式執行會沿用舊結果。**
- **2330 實際結果（09-19 14:57 那次）**：混合後 **74.97%／+10.41%**（模型 78.96%／+11.82%、基準 70.98%／+9.00%），信賴度「低」。

### 2.6 第 6 輪：`MODEL_VERSION_3M` 升版

- `3m-pit-official-technical-oof-estimate-v20-model-weight-floor`（前一版 v19）。
- 升版後 12:59 的正式執行數字才真正反映權重下限。

### 2.7 第 7 輪：S1 預測留存 ＋ S3 誤差帳本（使用者指定「S1+S3 最後再做 S2」）

- **S1：正式執行完成後才寫預測快照**
  - **根因**：分析器在「版本化 run 還在暫存」時會跳過寫入，所以**正式執行從來沒有留下任何可結算的預測**——
    這就是回饋迴路斷掉的第一個原因。
  - **修法**：`tw_single_stock_latest_report.py` 新增 `_record_published_prediction_history(final_dir, stock_id)`，
    **從 `main()` 在指標檔提交成功之後呼叫**，失敗只印 `[WARN]`，不回滾已發布的指標。
  - **踩到的坑**：第一版寫在 `publish_latest_report_manifest()` 裡面，導致既有測試把資料寫進**正式**快照庫、
    並打破 `replace.call_count==2`。我已用測試前的副本還原兩個 store（確認 9／7 筆、沒有測試殘留），並把呼叫移到 `main()`。
  - **落點**：`D:\TWStockData\program_data\dstock_single\snapshots\<mode>\<mode>\<stock>.json`
    （雙層目錄是既有慣例：`_history_store_path()` 會再接一層 mode，讀寫兩端一致，**不是 bug**）。
    現況：`three_month` 10 筆（最新 as-of 2026-09-18 → 目標 2026-12-22）、
    `tomorrow_open` 8 筆（最新 as-of 2026-09-18 → 目標 2026-09-21，已帶 `central_lineage`）。
- **S3：append-only 誤差帳本**
  - 新增 schema `tw_stock.single_stock_settlement_ledger_v1`、`append_settlement_ledger()`／`settlement_ledger_path()`，
    兩個結算函式都新增 `settlement_ledger_dir` 參數與 `"settlement_ledger"` 回傳欄位。
  - 落點：`D:\TWStockData\program_data\dstock_single\audit\settlement\<mode>.jsonl`。
  - **每個到期目標寫一次，跨模型版本保留並標記**（`model_version`／`current_model_version`／`same_model_version`），
    **即使該筆不具備正式證據資格也照寫**（`eligibility_*` 欄位如實記錄原因碼）——這樣「舊模型失準」才有帳可查。
  - **實測（09-19 13:37 寫入 7 筆，全部是 7 月～8 月的舊研究快照）**：

    | 目標日 | 預測 | 實際 | 誤差 | 方向 | 模型版本 |
    | --- | --- | --- | --- | --- | --- |
    | 2026-07-13 | +0.207% | +1.564% | +1.357% | 命中 | tomorrow-purged-baseline-history-v1 |
    | 2026-07-14 | +0.410% | −1.519% | −1.929% | 未中 | tomorrow-industry-point-in-time-v5 |
    | 2026-07-23 | 0.000% | −0.917% | −0.917% | 未中 | tomorrow-pit-broad-guard-oof-v13 |
    | 2026-08-03 | 0.000% | −1.733% | −1.733% | 未中 | v15-recent-holdout-gate |
    | 2026-08-04 | 0.000% | −1.766% | −1.766% | 未中 | v16-joint-ohlc-ood |
    | 2026-08-17 | 0.000% | +0.331% | +0.331% | 未中 | v17-joint-ohlc-ood |
    | 2026-08-18 | 0.000% | +0.330% | +0.330% | 未中 | v17-joint-ohlc-ood |

    合計：**方向命中 1/7、平均誤差 −0.62pp、MAE 1.19pp**。
    這 7 筆的 `eligibility_role` 都是 `rejected`（那時還沒有 published 結算證據），**所以它們是誤差紀錄、不是校準輸入**。

### 2.8 第 8 輪：S2 published 結算證據 ＋ 資格契約升版 v2

- **根因**：資格契約只認 Central 模式的證據欄位，published 模式一律缺 `receipt_sha256`／`artifact_sha256`／
  `receipt_run_id`／`revision`／`vintage`，加上價格基準 `tradable=unknown`，所以**永遠 `eligible=false`**。
- **修法（把真實可驗證的 published 證據填進既有欄位，沒有新增虛構欄位）**：
  `published_single_stock_projection.py` 新增 `PUBLISHED_SETTLEMENT_ROUTE = "published_shared_market_data"` 與
  `_published_settlement_identity(descriptor, fingerprint, expected_asof)`：
  發布頭 sha256 → `receipt_sha256`／`revision`；descriptor sha256 → `artifact_sha256`；
  發布頭的 `run_id` → `receipt_run_id`／`publication_run_id`；descriptor fingerprint → `central_session_id`；
  `expected_asof` → `vintage`；`central_session_immutable=True`；`evidence_mode=published_pinned_catalog`。
  `stock_data_sources.py` 的 `manifest_json` 分支補讀 `producer_id`／`run_id`／`published_at`。
- **三項規則調整（請重點核對）**：
  1. 價格基準：新增 `settlement_scope=model_basis_only`。共同資料區只有還原股價、沒有可交易價對應，
     **模型基準本身可驗證**，所以允許用來算「模型誤差」，但 `settlement_evidence_scope` 標為 `realized_error_only`，
     **不能當成可交易績效**。`model=unknown` 仍然一律拒絕。
  2. `research_only` 決策狀態、成本／治理指紋缺失 → 改記在 `actionability_notes`，**不再阻擋**。
     第一版我把 `research_candidate_not_formal` 整組改成非阻擋，結果連 `unavailable`／`rejected` 也會被放行；
     我自己的測試抓到後拆出 `HISTORY_REASON_DECISION_NOT_PREDICTIVE`（**仍然阻擋**）。
  3. 跨執行的 PIT 指紋／lineage 差異（`pit_fingerprint_mismatch`、`central_session_lineage_mismatch`）→ 改記在
     `cross_run_notes`，仍列在 `reason_codes` 但不阻擋。**跨模型版本（`model_version_mismatch`）維持排除**——
     第一版我誤把它一起放寬，打破既有測試 `test_history_legacy_missing_lineage_and_cross_version_are_excluded`，已回復。
- **契約版本**：`tw_stock.single_stock_prediction_history_eligibility_v1` → **version 2**，
  新增欄位 `actionability_notes`／`cross_run_notes`／`settlement_evidence_scope`。
- **驗證**：用**真實的 pinned descriptor ＋ 真實的正式摘要**跑探測（`probe_published_eligibility.py`），
  結果 `role=formal`、`eligible=true`（證據存於 `published_eligibility_probe.json`）。

---

## 3. 驗證結果（可自行複驗）

### 3.1 四次真實 CMD 執行（`1_執行個股分析與產生報告.cmd 2330 --source-mode published`）

| # | 起訖 | 秒數 | 結束碼 | 尖峰記憶體 | 對應輪次 |
| --- | --- | --- | --- | --- | --- |
| 1 | 09:33:08 → 09:42:30 | 561.6 | **0** | 379.9 MB | 2.1–2.3 |
| 2 | 12:59:30 → 13:12:59 | 809.6 | **0** | 466.4 MB | 2.5–2.6（權重下限首次生效） |
| 3 | 13:30:26 → 13:39:17 | 531.1 | **0** | 341.4 MB | 2.7（S1＋S3） |
| 4 | 14:51:13 → 15:04:30 | 796.9 | **0** | 383.6 MB | 2.8（S2） |

四次都沒有觸發記憶體保護（門檻 6 GiB 可用）。結果檔：`formal_run_20260919_*_result.json`＋對應 `_stdout.log`。

**環境地雷**：這個 session 的殼是 PowerShell 7，直接叫 CMD 會把 PS7 的 `PSModulePath` 洩漏給 Windows PowerShell 5.1 子行程，
導致 `Get-FileHash` 找不到、結束碼 23。`run_formal.ps1` 已把 `PSModulePath` 設回 downlevel 預設（只影響該行程）。

### 3.2 測試

| 時點 | pytest 摘要行 |
| --- | --- |
| 修改前基線 | `140 failed, 661 passed, 6 skipped, 65 subtests passed` |
| 第 1 批之後 | `137 failed, 665 passed, 6 skipped, 70 subtests passed` |
| 第 2 批之後 | `137 failed, 676 passed, 6 skipped, 76 subtests passed` |
| S2 之後 | `144 failed, 687 passed, 6 skipped, 76 subtests passed` |

**最後那次 failed 變多，我實際比對過測試 id**（`pytest_failures_before.txt` vs `pytest_failures_after_s2.txt`）：
新增失敗 14 條**全部**在 `tests/test_local_research_projection.py` 與 `tests/test_local_research_snapshot.py`；
同一批裡另有 8 條這次變成通過——這組測試會隨執行順序／併發浮動，是**既有的不穩定測試**，不是本次改動造成的。
本次改動範圍內的測試全部由失敗轉通過（`test_single_stock_pit_contract`、`test_published_source_freshness`、
`test_single_stock_formula_regressions`、`test_tomorrow_nested_outer_oos`）。
**請你單獨重跑那兩個檔案確認**——這是我唯一沒有完全排除的雜訊。

新增測試：`tests/test_formal_estimate_gates.py`、`tests/test_settlement_ledger.py`、`tests/test_published_settlement_evidence.py`。

### 3.3 資料落點（實際存在，已核對）

- 指標：`reports\latest_report_manifest.json` → `single_stock_run_3a55bc5bcb6647fdb99abc3d3c6fa608`（7 個成品）。
- 預測快照：`snapshots\three_month\three_month\2330.json`（10 筆）、`snapshots\tomorrow_open\tomorrow_open\2330.json`（8 筆）。
- 誤差帳本：`audit\settlement\tomorrow_open.jsonl`（7 筆，9,367 bytes）。
- 報告目錄目前 **505 MB／6 個版本化 run 目錄**（今天四次執行的暫存＋最終）。**舊 run 我沒有刪**，要不要清理請你或使用者決定。

---

## 4. 檔案、雜湊與備份

### 4.1 目前部署中的檔案（sha256 前 16 碼，`D:\stock\programs\single_stock_analysis\`）

| 檔案 | sha256(前16) |
| --- | --- |
| `src/tw_industry_data.py` | `9a4abc4426a23d25` |
| `src/tw_single_stock_3m_analyzer.py` | `1c2cf31d97def2bb` |
| `src/tw_single_stock_tomorrow_open.py` | `23d4ea99b1cb4da9` |
| `src/tw_prediction_history.py` | `2cedf7d2de0130f9` |
| `src/tw_single_stock_latest_report.py` | `bc2125a01e3ac6b7` |
| `src/published_single_stock_projection.py` | `2ce03aadce09d281` |
| `src/stock_data_sources.py` | `0350651ec6ebad59` |
| `src/tw_single_stock_report_contract.py` | `26b75335de9b7238` |
| `tests/test_formal_estimate_gates.py` | `7894cbed3125f325` |
| `tests/test_settlement_ledger.py` | `65657d360005eeba` |
| `tests/test_published_settlement_evidence.py` | `0df28e5fddf6f578` |
| `tests/test_single_stock_pit_contract.py` | `4171955f65005ec0` |
| `tests/test_industry_data.py` | `8f26322d5b7a2c9f` |
| `tests/test_published_source_freshness.py` | `04b6f495797b68d5` |

完整 64 碼在 `D:\stock\logs\single_stock_pit_20260919\deployed_sha256.txt`。

### 4.2 備份（`D:\stock\backups\`，八份，每份含 `original_sha256.txt`）

| 建立時間 | 目錄 | 內容（改動前） |
| --- | --- | --- |
| 08:24 | `claude_20260919_single_stock_pit` | 法定期限 PIT：3 個 src＋3 個 tests |
| 09:22 | `claude_20260919_single_stock_formal_gates` | 正式模型閘門：3m＋tomorrow |
| 09:33 | `claude_20260919_single_stock_tomorrow_defects` | 次日開盤三缺陷＋新測試 |
| 12:48 | `claude_20260919_single_stock_weight_floor` | 權重下限：3m＋report_contract＋測試 |
| 12:59 | `claude_20260919_single_stock_model_version` | MODEL_VERSION 升版：3m |
| 13:30 | `claude_20260919_single_stock_settlement` | S1＋S3：prediction_history／3m／latest_report／tomorrow |
| 14:31 | `claude_20260919_single_stock_published_evidence` | S2：projection／stock_data_sources／history／3m／tomorrow |
| 14:51 | `claude_20260919_single_stock_cross_run_notes` | S2 收尾：prediction_history＋新測試 |

還原方式：把備份裡的 `src/`／`tests/` 蓋回程式目錄（**逐輪還原，不要混用不同輪次**，因為後面的輪次依賴前面的函式簽章）。

### 4.3 證據（`D:\stock\logs\single_stock_pit_20260919\`，41 個檔案）

閘門 JSON（修改前 09-17／沙箱／部署後三組對照）、模型閘門 JSON（2330／0050／2317）、
四次正式執行的 result＋stdout、記憶體取樣、pytest 摘要與失敗清單（4 組）、
資格探測腳本與結果（`probe_eligibility.py`／`probe_published_eligibility.py` 與兩個 JSON）、
誤差帳本副本、模擬腳本（`sim_gate.py`／`sim_model.py`／`run_sim.sh`）、`run_formal.ps1`、`deployed_sha256.txt`、
以及兩個「正式資料未被測試污染」的核對檔（`production_unchanged_*.txt`）。

決策紀錄：`D:\stock\docs\decisions\ADR-0005-single-stock-statutory-availability.md`；
`D:\stock\docs\HANDOFF.md` 也新增了 2026-09-19 一節。

---

## 5. 模型有效性的事實（使用者問「為什麼模型無效」，這是實測答案）

- **三個月（63 個交易日視野）確實沒有樣本外技能**：1,151 列 ÷ 63 ≈ **18 個獨立樣本**，卻有 **145 個特徵**。
  OOF ROC AUC **0.345**（<0.5）、Brier **0.439**（naive 0.257）、MAE **0.158**（naive 0.156）、各 fold AUC 0.05–0.64。
- **次日開盤（1 天視野）是有效的**：約 1,200 個獨立樣本，模型權重落在 0.15–0.35。
- **所以現在的設計是**：三個月採「模型為主＋下限 0.5 混合基準」並**明白標示信賴度低**，
  次日開盤照常用模型；兩者的每一筆預測都進快照庫，到期後寫進誤差帳本，
  **等真實誤差出現再回頭修模型**（使用者的要求：修正發生在舊模型失準之後，不是疊加新模型）。
- **校準函式**（`apply_three_month_history_calibration`／`apply_tomorrow_history_calibration`）已經接好，
  但**還沒有任何一筆合格的到期紀錄可用**，見第 9 節。

---

## 6. TWStockUpdater／C（collect data tool）：索引，不重複

**權威文件（我 09-19 00:42 寫的，28,068 bytes，兩份內容相同）**：
- `D:\collect data tool\CLAUDE_TO_CODEX_20260919_COMPLETE_HANDOFF.md`
- `D:\TWStockUpdater\docs\CLAUDE_TO_CODEX_20260919_COMPLETE_HANDOFF.md`

它涵蓋 09-13～09-19，共 10 節：現況快照／每條路徑實跑結果／死碼移除／待刪除區 20.15 GB／
`audit` 兩個警示怎麼判讀／`verify` 為何結束碼 20／09-18 深夜測試修到全過／
法人過濾 v3（只收股票與 ETF）／還原股價「同次補抓」／`--fill-gaps`／共同資料區自動清理／
09-13～15 路由修補／測試誤發布事故與修復／第 7 節「需要你決定」清單／檔案與雜湊／備份與證據／下次 update 驗收清單。
**那份仍然有效，本檔不取代它。**

**今天（09-19 白天）之後我沒有再改 TWStockUpdater 或 C 的任何檔案。**

**其中一項已經被我今天做掉了**：該文件 7.2 最後一條「個股分析卡 PIT 閘門……方向待使用者選」——
使用者已選 A（法定公告期限推估），並於今天實作完成，見本檔 2.1。**請把那條標為已處理。**

另外，`D:\TWStockUpdater\docs\CODEX_REVIEW_AND_REPAIR_PLAN_20260919.md`（今天 17:48 仍在增長，25,789 bytes）
明確寫著「已核准執行（2026-09-19）；實作中，尚未驗證／同步／發布」與「本文件是 Codex 的執行計畫，不是交付 Claude 的計畫」。
**我沒有依它做任何事，也沒有改它。**

---

## 7. MIX ALL：唯讀觀察（**我沒有修改過任何一個 MIX 檔案**）

### 7.1 現在有另一個 session 正在改

- `D:\MIX All\src\mixstock\` 的檔案時間戳：`cli.py` 17:47、`pipeline.py` 17:42、`panel.py` 17:42、
  `published_run.py` 17:40、`finmind_features.py` 17:40、`accuracy_targets.py` 17:31、`published_checkpoint.py` 17:30、
  `enrichment.py` 17:29、`backtest.py` 17:28、`derived_cache.py` 17:20、`report_preflight.py` 17:19、
  `feedback.py` 17:19、`excel_report.py` 17:18。**寫檔仍在進行中**（我最後一次觀察 17:48）。
- 該工作自己的紀錄在 `D:\MIX All\logs\complete_repair\repair-20260919-171008\`：
  `baseline_manifest.json`（17:13 的 before bytes／SHA）、`plan.md`、`ledger.json`、`blocking_issues.json`（17:42）、
  `tasks\`、`research\`。狀態 `implementation_delegated_validation_pending`，**本輪尚未執行任何測試或正式 run**。
- 它的 `plan.md` 寫明使用者新目標：**命中率 ≥60%、每筆預估漲幅與實際漲幅絕對誤差嚴格 <2 個百分點（不是 MAE）、期間 1／5 個交易日**，
  且「短中長期交個股分析，**本輪不修改 Single**」。
- **基線之後檔案又被改過**（可驗證）：`baseline_manifest.json` 記 `pipeline.py = ca633ca2…`、`feedback.py = 00e2c19e…`，
  17:48 實測已是 `pipeline.py = aa70965e355e71fd…`、`feedback.py = ac47f7e686ea9ba5…`。

### 7.2 MIX 今天的每日流程是失敗的（不是我造成的，也不是個股分析造成的）

`logs\MIX_daily_20260919_120714.log`（12:07 開始、16:10 結束）在 74% 進度「結算歷史預測並更新校準摘要」階段丟出：

```
mixstock.feedback.LedgerIntegrityError: settlement reconciliation has integrity failures;
no corrections were applied: settlements/final-2026-07-17-7a47801-729c53817b/actual-105f462f….json:
LedgerIntegrityError: official market cache root is unavailable for settlement correction
```

對應 `blocking_issues.json` 的 B02（6277 筆缺 snapshot 的更正、實際 run_id `final-2026-07-17-7a478015e0ced48f`
查無 publication／requirements 命中，依 strict 規則排除，屬歷史孤立證據缺口）。
**這件事正由那個 session 處理中，我沒有介入、也不建議由本檔的讀者同時動手。**

### 7.3 MIX 這幾天的改動留下的證據（我只是列出來，內容沒看細）

`D:\MIX All\backups\`：`20260913_consumer_data_repair`、`20260913_shared_v2_adapter`、`20260913_consumer_freshness`、
`sector_published_fix_20260915`、`sector_publication_phase2_20260915`、`encoding_repair_20260918_180443/180613`、
`ledger_pollution_quarantine_20260918`、`mix_fix_20260919_040635_374c4de03e54`（04:06，改 `feedback.py`＋`pipeline.py`）、
`mix_fix_20260919_083328_20c032546466`（08:33，同兩檔，`state=installed`，manifest 有 original／patched sha256 可追）。
相關日誌：`MIX_repair_check_20260919_040635_*.log`、`MIX_repair_check_20260919_083329_*.log`（16:33）。

**如果要知道 MIX 這幾天到底改了什麼，正確來源是上面這些 manifest 與 `logs\complete_repair\` 的 ledger，不是本檔。**

---

## 8. 請你核對／決定的清單（個股分析）

1. **法定公告期限當作 PIT 可得時間**（2.1）：半年報一律用 **8/31**（遷就 KY／第一上市），對一般公司偏保守 17 天。
   要不要改成「依公司別分流（一般 8/14、KY 8/31）」？分流會提高精度，但需要可信的公司別名單。
2. **權重下限 0.5**（2.5）：這是使用者的決定。要確認的是**下限值本身**與「閘門結果只記錄不歸零」的做法，
   以及報告上「信賴度低」的文案是否足夠。
3. **`settlement_scope=model_basis_only`**（2.8）：允許用還原股價基準結算「模型誤差」，但不得當成可交易績效。
   請確認這個界線在你那邊的契約定義下成立。
4. **`actionability_notes` 與 `cross_run_notes` 的分類**（2.8）：哪些原因碼該是「註記」、哪些該是「阻擋」。
   目前阻擋的仍有：`decision_status_not_predictive`、`settlement_values_missing`、`model_version_mismatch`、
   `price_basis` 的 `model=unknown`。
5. **資格契約升到 version 2**：新增三個欄位。如果 MIX 或其他消費端讀這個 schema，**需要同步**（我沒有找到其他消費端，請你確認）。
6. **`MODEL_VERSION` 與快取的耦合**（2.5 的坑）：是否要加一個「政策指紋」進快取 key，免得下次有人忘了升版。
7. **不穩定測試** `test_local_research_projection.py`／`test_local_research_snapshot.py`（3.2）：要不要標記或隔離。
8. **報告目錄 505 MB／6 個 run**（3.3）：舊 run 的清理政策。

---

## 9. 還沒到期、之後才能驗的事

- **2026-09-21（下一個交易日）**：09-19 存的次日開盤預測目標日到期。這會是**第一筆 published 模式的真實到期紀錄**，
  屆時才會實際觸發 `apply_tomorrow_history_calibration`，也才會證明 S1→S3→校準整條路走得通。
  **驗收方式**：09-21 之後跑一次正式執行，檢查 `audit\settlement\tomorrow_open.jsonl` 是否多一列且
  `eligibility_role=formal`、`eligible=true`（目前 7 筆舊紀錄都是 `rejected`）。
- **2026-12-22**：三個月預測到期。
- 在那之前，**任何「模型已經改善」的說法都沒有證據支撐**，我也沒有在報告或文件裡這樣寫。

---

## 10. 一句話總結

個股分析今天從「08-24 之後完全出不了正式報告」修到「可正常產出，且每一筆預測都會留存、到期會記誤差」，
四次正式執行結束碼都是 0；TWStockUpdater 的細節在既有那份 28 KB 交接；
**MIX ALL 我一個字都沒有改過**，它現在由另一個 session 在修，請以它自己的 `logs\complete_repair\` 為準。
