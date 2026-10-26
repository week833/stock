# 共用候選模型資料擴充 Implementation Plan

> **For agentic workers:** 依主代理已明定的 low_worker 分工執行；不再建立子代理、不 commit、不自行跑測試。一般計畫技能的額外確認與逐步測試由本次明確授權取代。

**Goal:** 在不切換舊正式模型的前提下，建立可選來源、18 月特徵、候選模型、runtime 與繁體中文詳細報告。
**Architecture:** 36 路只作可選 catalog；候選以新 schema 保存來源選擇、欄位順序與資料可用性證據，舊 C0 保持原樣。
**Tech Stack:** Python、pandas、numpy、現有 sklearn；不預設新增套件。
**Spec:** `docs/superpowers/specs/2026-10-17-shared-model-data-expansion-design.md`。

## Global Constraints

- 18 曆月按市場 own asof；精確市場日標籤，不補價、不造日期、不放寬 purge。
- 36 路不必全讀或全入模；not-selected 不等於 missing，來源／特徵規則須封存並維持訓練推論一致。
- 固定機率／零報酬只比較；新候選不自動切換舊 current，不改中央採集政策。
- 使用者可見標籤、原因、報告及分析依據均使用繁體中文；機器 ID 保留原文。
- 各 worker 只改分配檔案，保留其他工作者變更；產品測試由主代理集中交驗證 worker 執行。

## Review Focus

- 價格缺日、停牌及市場不同 asof：標籤仍指向精確第 H 個市場日。
- date-only／缺公告時間：不可回填歷史財報、海外或新聞訊號。
- 股／張與 raw／adjusted 價格：不猜單位、不混用分母。
- 未選用、合法無資料、PARTIAL、unknown：各自保留，不誤報完整。
- 126／252 日全缺：欄位與 coverage 保留，不用七特徵全有限作 readiness。

## Task 1: 可選來源與 18 月特徵（本 worker）

**Files:** `programs/shared/stock_daily_feature_catalog.py`；`programs/shared/tests/test_stock_daily_feature_catalog.py`。
**Interfaces:** `SOURCE_CATALOG` 共 36 路；`select_source_plan(horizons, industry=None)` 回傳 route_id/name_zh/usage/reason_zh。
`FEATURE_SCHEMA='tw-stock-selected-features-v1'`；`build_candidate_features(panel, market_sessions, *, asof_by_market, horizons, source_frames=None, source_manifest=None)` 回傳 features、labels、metadata、analysis_context。
metadata 固定含 feature_order、feature_order_by_horizon、feature_metadata、horizons、asof_by_market、source_manifest、coverage、limitations；原七特徵保留。
feature_order 固定為本版本全部 38 欄，feature_order_by_horizon 按事前每 H 規則固定，不受當次 union manifest 或資料缺值改變；未選來源欄位為 NaN。feature_metadata 同時提供 route 與 source_route。
feature_ready 為截至當日累積至少 60 筆實際有效價格且當日 close 有效；保留 adjusted_close／signal_close，日期為 ISO 字串。只有核心價量／官方比較／日曆／身分證據 required_for_analysis，選中的其他來源仍可缺值。
來源時間接受具時區的 available_time／published_at／announcement_time／source_close_at；日頻法人／融資融券／當沖／持股／估值 date-only 採至少延後一個已驗證市場交易日的研究代理，記 availability_assumption 與 historical_PIT_unverified，不冒充已證實公告。財報／營收／新聞／海外不使用任意 lag。
單位證據接受 row/manifest 的 units 欄位對照或 volume_unit 等專屬欄位；張轉股只接受明示 lot_size，不硬編 1000。來源原有 status／PARTIAL／證據均保留。
量比、金額比與法人淨額相對自身前 20 筆絕對量的標準化可在同序列單位一致假設下使用；跨來源分母仍須證據。非 USD 的總經序列須 selected_instrument 與 instrument_verified，避免混幣別／期限／商品；全球股指採固定 symbol 對照。財務 context 保留最新期間完整投影科目，新聞才限制最近六筆。
runtime 對齊官方同股同日 volume/amount/transactions 並提供 raw_open/raw_high/raw_low/raw_close；adjusted_close 與 open/high/low 保持 FinMind 還原價格。intraday_range_ratio 只用官方 raw_high/raw_low，不假冒未知 high/low 的來源。

- [x] 記錄已批准介面、分工與集中驗證限制。
- [x] 先新增測試：catalog／選用、18 月邊界、缺日標籤、七特徵缺值、時間與單位負向案例、context 與 manifest；本 worker 未自行執行，集中分批結果見下。
- [x] 實作純記憶體特徵建構，不讀檔、不 import 專案、不網路、不訓練；已交集中驗證。
- [x] 完成欄位／限制交接：固定 38 欄、逐 H 規則、source_route、核心 required、時間代理、單位限制、完整最新財報 context、正估值倍數及官方 raw 價格分離；研究報告最終內容驗收已通過，範圍見下。

## Task 2: 候選 Engine（其他 worker）

**狀態：候選程式與研究報告交付完成；非正式模型採用。**

`stock_daily_candidate_model.py`：`fit_candidate(prepared, *, seed, min_fit_days=60, selection_days=20, test_days=20)`、`predict_candidate(model, prepared)`、`save_candidate(model, path)`、`load_candidate(path)`。
先依 label_end purge 再確認 60/20/20 有效訊號日；H60/H63 預設 uncalibrated；所有新候選共用未參與選模的 final holdout。舊 artifact 歷史比較另標非獨立。
候選 artifact 獨立保存，不自動 promotion、不切換舊正式模型或 current pointer；尚未套用正式模型，分批驗證不宣稱完整 suite 全數通過。

## Task 3: Runtime、報告與整合（其他 worker）

**狀態：程式完成，最終研究報告內容驗收通過；非正式模型採用。**

`stock_daily_candidate_runtime.py` 負責 query_published_rows、候選 CLI、報告 payload；只投影所選來源。
固定候選 CMD 與明確 `--candidate-model` 入口已落地；使用 consumer cache，僅 train 模式允許 fullscan，日常分析不另作來源全量掃描。
`single_stock_analysis/src/stock_candidate_report.py`：`write_candidate_report(payload, output_dir)->dict`，報告為繁體中文並按實際分析詳細列依據、資料時間、限制及無估計原因。
繁體中文報告固定 11 章，已納入字型／內容預檢；最終 PDF 已完成下列抽文、render 與目視驗收。
候選整合保留明確選用，不自動 promotion；舊正式入口／模型不預設切換。個股 H1/H63、MIX H1/H5/H20/H60、Top10 H1 保留。

## 集中驗證交接

以下為主代理提供的已驗證事實；本文件 worker 未另跑測試或重讀正式資料。證據根：`D:\TWStockData\program_data\stock_daily_c0\candidates\acceptance_20261017T182819`。

- 初次集中測試：174 pass／15 fail；相關修復後，受影響 4 檔為 113 pass。兩輪分開記錄，不相加、不宣稱完整 suite 重跑全綠。
- Report 重構測試：57 pass／1 fail；後續 21 個 ref／stable-json 定向案例全 pass。這些是不同輪次與範圍，不合計為新的通過總數。
- Stable paths：161 files 檢查通過。
- 真實 run：`20261017T103954Z-dc5bb0a82a`，50 股、seed=`20261017`，同一 head=`5fa6f5b059dd41aa60b673c4e60bc16912a83bd07626ad4135752643b61904e9`；H1／H5／H20／H60／H63 均為 trained。
- 實際有效數值特徵：H1／H5／H20 各 18 欄，H60／H63 各 19 欄；`return_252` 與未知歷史 PIT 的財報／海外資料未冒充有效入模。
- 該 run 的 H1 方向準確率 53.94%、MAE 1.394%；H63 方向準確率 58.16%、MAE 14.035%。部分誤差劣於基準，不宣稱全面改善或準確度提升已獲保證。
- 該次 train 共 262.36 秒，其中 fit 19.1 秒；先前 report 的 consumer cache 為 22／22 命中，資料讀取約 1.1～1.2 秒，不把讀取耗時當完整 PDF 產製時間。此命中率屬先前紀錄，不額外宣稱 final receipt 的 cache 命中率。
- 兩個候選 PDF 由含空白的 133 頁版本先收斂為 compact 44 頁中間版本，已消除空白頁；最終版本各 45 頁，見下。大型 descriptor 改引用完整 model 節點，sidecar 由約 186 MB 降至約 1 MB，receipt 由約 185 MB 降至約 13 KB，完整證據未刪除。

### 最終研究報告驗收

依主代理最終通知，`final_acceptance_summary.json` 結論為 `passed_research_reports_only`，`can_deliver_research_report=true`。本文件 worker 未另跑測試或重做 PDF 驗收。

- H1／H63 最終 PDF 各 45 頁，共 90 頁；完整抽文與 render 已通過。主代理另完成 8 張 contact 圖及 H1 p1／p22、H63 p36 目視驗收，無空白頁、缺字、重疊或裁切，11 章齊全；海外代碼與 `source_close_at` 已補全。
- 最後 identity 相關既有測試 log 為 12 passed／66 deselected，本次重用、未重跑；其當時 exit code 與 source hash 未獨立記錄，不能冒充具有獨立來源綁定的本輪重跑證據。真實 final PDF 另有上述完整驗收，不以此既有 log 取代。
- Final CLI exit code 0，總耗時 65.193 秒；讀 snapshot 23.6 秒、讀 22 來源 1.1 秒、特徵 2.0 秒、推論 1.1 秒、報告 23.5 秒。未另核對 final receipt 所列 cache 命中率，因此不新增該項通過宣稱。
- 最後報告修正與驗收期間，模型與預測數值未變；正式 current、artifact、central head 的 hash 前後相同，無重訓、重抽樣、正式套用或外傳。
- 既有 holdout 結果與「部分誤差劣於基準、未全面改善」說明保留，不因報告驗收通過而提高模型成效宣稱。

最終 PDF 固定於上述證據根下：

- H1：`reports_final\1203\H1\candidate_1203_H1_3b0d6a2677b7449a925dbb3d40ab3b6a\candidate_1203_H1.pdf`。
- H63：`reports_final\1203\H63\candidate_1203_H63_118e5962427347fab50c4f18185ba3e9\candidate_1203_H63.pdf`。

- [x] 本次候選實作／研究報告交付完成：僅限已驗收研究報告，不代表正式採用、全來源完整、歷史 PIT 完備或模型全面優於基準。
