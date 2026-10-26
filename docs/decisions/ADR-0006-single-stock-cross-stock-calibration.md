# ADR-0006：個股分析跨股票原模型校正

- ID：ADR-0006
- 日期：2026-09-27
- 狀態：`accepted`
- 狀態語義：使用者已核准目標與方案方向；不代表實作、測試、回測效益、正式資格或發布已完成／通過。
- 範圍：`D:\stock\programs\single_stock_analysis` 的原模型歷史預測校正、runtime bundle 與報告稽核。
- supersedes：ADR-0003「保留既有模型公式」僅就本 ADR 所定義的共用校正參數例外；其餘來源、PARTIAL、PIT 與正式資格條款不變。
- superseded_by：N/A

## 背景

使用者明確要求以多股票的原模型歷史預測校正現有模型／公式，而不是以 pooled predictor 取代各股票原模型。這是推論配方的持續變更，應與跨股票深度研究候選及同股票歷史額外修正分開治理。

## 選項

- pooled predictor 取代原模型：不採用，會改變模型核心及股票間預測責任。
- 各股票獨立重訓替換原模型：不採用，偏離以多股原模型歷史預測校正既有公式的目標。
- 保留 native 模型，以股票等權的跨股票歷史資料擬合校正參數，僅在獨立 holdout 門檻通過時套用：採用。

## 決定

1. 保留原 RF／GB／Logit／Ridge 模型與其內部 OOF；三個月校正受 bundle 既有 bounds 約束。native runner 以原 native 核心產生預測，不以 pooled predictor 擬合替代。輸入列須標示 `prediction_engine='native_existing_model'`，且帶有非空的 `native_model_version`、`fit_id`、`source_fingerprint`；pooled 簡化模型即使欄位形狀相同也拒絕。歷史截止日加 block refit 預設為 20 sessions；這不等同每日完全相同地重訓，該差異須明示。
2. 早期 calibration fit 與較晚、經 purge 的 holdout 分離；calibration target 必須早於 holdout 首個 decision date。holdout labels 只作 gate，不參與 fitted parameters。按股票等權，不能讓單一大樣本股票支配結果。至少 10 檔股票、calibration 40 dates、holdout 20 dates，且 selected-stock coverage 至少 80%。
3. 機率校正獨立 gate：macro Brier 至少改善 1%，accuracy 不得退步超過 1 個百分點。三個月報酬 gate：macro MAE 至少改善 1%，至少 55% 股票不變差，macro 95% tail error 退步不超過 2%。未通過者維持原公式。
4. 次日 OHLC 以整組判定，分別保留套規則前的 `original_OHLC` 與已套 TW tick rules 的 `published_original_OHLC`，並記錄 `market_type`、`security_type`、`reference_close`、`normalized_reference_close`、既有 `return_lower`／`return_upper` 與 `min_intraday_range`。holdout candidate 使用原 `base.conform_taiwan_stock_price` 及相同市場／參考價契約，與 `published_original_OHLC` 在同一 final stage 比較；須符合整組 OHLC coherence、bounds、range 與台灣價格規則。全部 gate 通過才可啟用整組，不能只啟用其中價格欄位；機率仍獨立判定。
5. 僅啟用通過 gate 的項目。此為 current-vintage retrospective 校正，不宣稱 prospective 效果；不得提升 PARTIAL、補造 PIT、放寬既有 formal eligibility 或 reliability gate。共用校正參數是模型配方更新，不是把 cross-stock deep-research artifact 宣稱為 live matured evidence；既有 same-stock history extra 維持 disabled。
6. runtime bundle 保留 `base_model_version`；active calibration ID 納入 effective `model_version`。報告 validation 記錄校正 ID、原 final 值與校正後 final 值。校正 artifact／模型維持 `dstock_single` consumer-owned scope；只向既有 canonical 校正 ledger append，不覆寫歷史，且單次推論不追加校正歷史。Central 與 MIX 不變。
7. 原模型跨股票研究準備可使用個股最早可得的有效歷史，不因未滿正式報告要求年限而整股排除；僅 native runner 設定的私有研究旗標，且同時為 `_weekend_prepare=True`、`source_mode='published'`、`provisional=True` 時生效。coverage 仍須有有效且不矛盾的首末日期及至少 20 筆資料，原始 coverage 隨 packet 與 checkpoint 留存。一般正式報告的歷史年限 gate 不變；逐 cutoff 的 160／120 樣本、purge 與 class gate 不變，不足的 block 略過並繼續後續 block。
8. Native research 的所有股票與 mode 共用 pinned published session calendar：由 `analysis_start_cut` 至受 pinned as-of 與首檔各 mode 最新有效收盤日限制的 replay limit，逐日以 `published_session_sequence` 驗證。Packet 只要求在其與 canonical calendar 的日期範圍交集逐日一致；回測窗內實際 price／feature 觀測必須是 canonical 官方 session 且受 packet calendar 涵蓋。未觀測前綴與 replay limit 之後的尾綴不裁短共同訓練窗；metadata 日期範圍不替代逐日驗證，未知官方日期仍 fail closed。

## 理由

分開原模型、擬合期與 holdout 可把校正限制在既有公式的參數修正，並避免時間洩漏、股票樣本量失衡、OHLC 價格規則 stage 不一致，以及研究證據被誤認為正式成熟證據。明確保留 PIT、PARTIAL 與 reliability 門檻，避免校正 gate 變成資料資格或發布資格的替代品。

## 證據

- 基線決策：`D:\stock\docs\decisions\ADR-0003-published-updater-consumer-inputs.md`、`D:\stock\docs\decisions\ADR-0005-single-stock-statutory-availability.md`；本次唯讀核對日期為 2026-09-27。
- 實作證據：core/native calibration、frozen runtime hook、daily summary helper 與 batch prewarm source 已存在於工作樹。2026-09-27 首次集中執行五個指定測試檔為 83 passed、7 failed、7 subtests passed（17.220 秒）；修正授權測試 fixture 後，7 個原失敗項均以窄範圍重驗通過，未重跑其餘 83 個通過項。
- focused tests：`D:\stock\programs\single_stock_analysis\tests\test_single_stock_cross_stock_calibration.py`、`D:\stock\programs\single_stock_analysis\tests\test_single_stock_native_calibration.py` 已建立並有局部驗證證據；五檔集中執行的首輪未全數通過，修正後未重跑整組。
- 真實 50 股票回測及 runtime 啟用尚未確認；結果與校正效益未驗證，證據未建立。
- 日曆 focused test 首輪 35 passed／1 failed；第一次窄重驗仍因 test fixture 缺少 `pd` 而 NameError，第二次窄重驗 1 passed（1.91 秒）。36 個獨立案例皆有 pass 證據，未重跑整個 testfile。真資料 bounded evaluate-only run：6959、`--max-blocks 4`，run `native_20260927T140458Z`，elapsed 00:14:11.796、exit code 1；終態 `status=partial`、`requested_scope_complete=true`，160/160 rows（兩 mode 各 80，8 個 block checkpoint 各 20）、skip=0、error=0。Checkpoint 使用 `native_existing_model`；history coverage 原值為 2023-07-13 至 2026-09-24、780 rows、3.201/5.0 years、`shorter_than_requested=true`、`used_all_available=true`。Packet calendar diagnostics：three_month 2023-01-03 至 2026-12-31（966 sessions），tomorrow_open 2021-09-10 至 2026-12-31（1,291）；共用 canonical replay calendar 2021-09-27 至 2026-09-24（1,217）。本 run skip=0，未以真資料驗證早期 block skip；不是完整 50 股票 run。`publish_requested=false` 且 evaluate-only 未進入發布呼叫；事後檢查兩個 active pointer 與 calibration ledger 均不存在，但未留事前 hash，故不宣稱已作前後內容比對。

## 後果

即使文件狀態為 `accepted`，active 校正仍須由實作與 holdout gates 決定；未通過或 invalid calibration 保持原公式。現有 canonical ledger 為 `D:\TWStockData\program_data\dstock_daily\model\calibration_history.jsonl`，僅追加，不覆寫。此 ADR 本身不改變 runtime 行為，也不代表可正式發布。

## 回復方式

停用 active cross-stock calibration 或使用 `suspend_cross_stock_calibration()` 走原公式，並保留 bundle 版本、校正 ID 與既有 ledger。不得藉回復刪除或改寫歷史；任何程式回復須另有明確授權。本 ADR 不授權修改 Central、MIX 或其他文件。
