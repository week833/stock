# ADR-0005：個股分析低頻資料以法定公告期限作為可取得時間，與正式估計發布門檻

- ID：ADR-0005
- 日期：2026-09-19
- 狀態：accepted
- 狀態語義：使用者已核准並已實作、以真實共同資料驗證到正式資格檢查通過；模型擬合、報告產出與發布驗證尚未執行。
- 範圍：`D:\stock\programs\single_stock_analysis` 的低頻 PIT 契約與正式輸入資格檢查（三個月與次日開盤共用 `build_features`）。
- supersedes：ADR-0003「保留既有模型公式與特徵資格要求」中「正式模型只接受實際公告時間」的部分；其餘條款不變。
- superseded_by：N/A

## 背景

2026-09-13 兩個 consumer 改讀共同資料區後，個股分析的正式流程在
`require_formal_single_stock_input_quality` 永遠失敗，最後一份正式報告停在 2026-08-24。

2026-09-17 以真實共同資料（head `4d4a6099…`、資料日 2026-09-14）對 2330 做唯讀模擬，
三個月與次日開盤都取得同一組原因碼（證據：`D:\stock\logs\single_stock_pit_20260919\gate_*_before_20260917.json`）：

- `pit_metadata_no_available_rows:industry`
- `pit_legacy_availability_basis:monthly_revenue`
- `pit_publication_time_unverified:monthly_revenue`
- `pit_publication_time_unverified:company_financials`

共同資料區沒有實際公告時間：損益表與資產負債表只有 date/type/value；現金流量表 `create_time` 全空；
月營收 `create_time` 在 2021–2025 年只有約一成有值且有晚 50 天以上者；申報期限表 9,925 列的
`observed_filed_at` 全空。因此「只接受實際公告時間」等於永遠無法產生正式報告。

## 選項

- A：低頻資料以法定公告期限保守推估可取得時間，資格檢查接受此明確標示的推估。
- B：維持嚴格規則，月營收與財報退出正式模型，只作報告參考。
- C：由更新程式回補公開資訊觀測站五年的實際公告時間，期間個股分析維持阻塞。

使用者於 2026-09-19 選擇 A（「已補齊過去5年資料不過可依照建議執行」）。

## 決定

低頻資料的 `available_at` 改用法定公告期限，並以「順延假日後的下一個交易日」表達：

| 期別 | 法定期限 | 說明 |
| --- | --- | --- |
| 月營收 | 次月 10 日 | 全體適用 |
| Q1 | 5/15 | 季末後 45 日 |
| 半年報 | 8/31 | 取最晚者，涵蓋第一上市外國公司的 2 個月規定 |
| Q3 | 11/14 | 季末後 45 日 |
| 年報 | 3/31 | 會計年度終了後 3 個月 |

期限若落在非交易日，先順延到第一個交易日；由於公司可在期限當日收盤後才公告，
可取得時間再往後取「嚴格晚於該期限的下一個交易日」。沒有交易日曆時退回週末順延加一日。

- `availability_basis` 為 `estimated_conservative_lag`、`is_estimated=True`、
  `publication_time_verified` 維持 `False`：這是推估，不是實際公告時間。
- 正式資格檢查只接受這個 basis 的未驗證列；legacy／index／unknown basis，
  以及「宣稱是實際時間卻未驗證」的列仍然拒絕。
- 沒有任何欄位進入模型的群組（industry、月營收、財報在缺資料時）只登錄 QC，
  不再登錄為正式 PIT metadata；規則與官方群組一致。
- 報告的「低頻資料PIT/QC」來源欄位加註此推估規則與「不是實際公告時間」。

月營收原本以 FinMind `date`（次月 1 日）當可取得時間，比法定期限早約 9 天，屬前視；本決定同時修正。

## 理由

法定期限不早於合法公告時點，對準時申報的公司沒有前視；同時讓有效的基本面資料繼續進入模型，
並保留「非實際公告時間」的誠實標記。相較之下 B 會讓模型失去基本面，C 在資料回補完成前無法產生任何報告。

## 證據

- 修改檔案與原檔備份：`D:\stock\backups\claude_20260919_single_stock_pit\`（含 `original_sha256.txt`）。
- 修改後以真實資料模擬 2330（head `4d4a6099…` 之後的 09-18 head，資料日 2026-09-18）：
  三個月與次日開盤皆 `eligible=true`、`reason_codes=[]`，特徵 1214 列 x 178 欄，
  正式程式碼／共同資料區 head／報告 manifest 前後雜湊一致，無網路連線。
  證據：`D:\stock\logs\single_stock_pit_20260919\gate_*_sandbox.json`、`run_sim.sh`、`sim_gate.py`。
- 部署後在正式位置重跑同一組模擬，結果相同（`gate_*_after_deploy.json`）；
  部署的三個 src 檔 SHA256 記錄於 `deployed_sha256.txt`。
- 測試：修改前 140 失敗／661 通過，修改後 137 失敗／665 通過
  （`pytest_summary_before.txt`、`pytest_failures_before.txt` 與 `_after` 對應檔）。
  差異中的 `test_local_research_*` 為既有的併發性不穩定測試：未修改的正式程式單獨重跑同樣失敗，
  且失敗組合每次不同，錯誤為 `sealed_sidecar_ancestor_changed`。
- **未驗證**：模型擬合、報告產出、`tw_single_stock_latest_report.py --publish` 的發布驗證與
  Telegram 傳送都未執行；通過資格檢查不代表正式報告已可交付。

## 追加決定（同日，實跑後發現的後續門檻）

修好 PIT 資格後實際執行 2330，暴露三個既有門檻，使用者同意一併處理：

1. **基準估計可發布**：可靠度閘門把模型權重降為 0（2330：ROC AUC 0.345、Brier 0.439 對基準 0.257、
   報酬 MAE 0.158 對基準 0.156）時，估計退回歷史基準，`prediction_audit` 狀態為
   `baseline_only_model_*_unsupported`，`_require_formal_three_month_model` 原本一律拒絕，
   與 README 的 `single-stock-formal-estimate-v3`「仍發布基準估計並標示低信賴」相矛盾。
   決定：這兩個狀態列為正式可發布（常數 `FORMAL_BASELINE_ONLY_PREDICTION_STATUSES`）；
   真正的 fallback 模型、擬合錯誤與其他 unsupported 狀態仍然拒絕。
2. **ETF／ETN 沒有月營收與財報不算資料缺漏**：`build_features` 新增 `fundamental_sources_expected`，
   由呼叫端以 `is_etf_or_etn_security` 判斷；為 False 時這兩個群組的 unavailable 只記錄
   `expected_for_instrument=False`，不列入 QC 阻擋。低頻群組全部明確排除時，空的
   `pit_decision_metadata` 也不再判為 `pit_decision_metadata_missing`（普通股若有群組宣稱入模卻缺
   metadata，仍然拒絕）。
3. **次日開盤的 fallback 偵測過寬**：`_tomorrow_validation_failure_markers` 原本只要驗證資料任何一層
   出現 `fallback` 字樣就判定 fallback，實際命中的是校正描述 `calibration.probability.fallback="identity"`，
   使正式次日開盤永遠無法通過（2330 的四個預測目標其實都是 model_supported）。
   改為只認 `model_mode="fallback"`、非空的 `fallback_reason`、非空的 `*_fit_error`／`*_training_error`／
   `*_model_error`，以及以 `fallback` 開頭的字串值。

驗證（真實資料、只讀模擬到模型檢查點）：2330 三個月與次日開盤、0050 三個月的模型閘門都通過；
證據 `D:\stock\logs\single_stock_pit_20260919\model_gate_*.json`。備份：
`D:\stock\backups\claude_20260919_single_stock_formal_gates\`。

## 追加修正：次日開盤的兩個程式缺陷（2026-09-19）

實跑再暴露兩個與決策無關、純粹的既有缺陷，已一併修正（備份
`D:\stock\backups\claude_20260919_single_stock_tomorrow_defects\`）：

- `maybe_write_tomorrow_research_audit_only` 沒有 `session_sequence` 參數，但正式流程一定會呼叫它
  並傳入該參數，導致每次正式次日開盤都 `TypeError`。已補上參數並轉傳給
  `write_tomorrow_research_audit_artifact`（該函式本來就接受）。
- `fallback_tomorrow_open_model` 內部引用未定義的 `central_lineage`，非正式 fallback 會 `NameError`
  （既有測試 `test_tomorrow_fallback_uses_product_rules_instead_of_fixed_35_percent` 長期失敗）。
  已加上 `central_lineage` 具名參數（預設 None），行為不變、測試恢復通過。

## 追加決定：正式估計不再被可靠度閘門歸零（2026-09-19，使用者選定）

使用者要求「模型用現有資料直接給出分析預測，等實際失準再修正」，因此三個月的正式估計改為：

- 新增 `RELIABILITY_MODEL_WEIGHT_FLOOR = 0.5`：可靠度閘門（OOF 凸組合、近期留出、特徵外推懲罰）的結果照舊計算並完整記錄，
  但實際發布用的權重取 `max(閘門權重, 0.5)`；閘門給出更高權重時仍沿用較高者。
- `prediction_audit` 的 `status` 依**閘門權重（未套下限）**判定：閘門為 0 時記為
  `floor_blended_model_*_reliability_unsupported`，並新增 `reliability_model_weight`、`model_weight_floor` 欄位；
  報告顯示「模型估計與歷史基準混合（模型未通過樣本外增益門檻，採權重下限 50%）」、信賴度「低」。
- 正式模型閘門接受這兩個新狀態（`FORMAL_LOW_RELIABILITY_PREDICTION_STATUSES`），真 fallback 與擬合錯誤仍拒絕。
- 模型版本升為 `3m-pit-official-technical-oof-estimate-v20-model-weight-floor`：混合政策改變必須讓模型快取失效，
  否則正式執行會沿用舊版快取結果（09-19 12:54 那次即因此仍輸出舊的純基準數字）。
- 次日開盤未套下限：它的權重（0.15–0.35）是樣本外最佳凸組合、閘門本來就有效，改動會降低其預期準確度。

2330 實測（真實資料）：閘門權重 0／0 → 下限 0.5；機率 模型 78.96%、基準 70.98% → 發布 74.97%；
報酬 模型 +11.82%、基準 +9.00% → 發布 +10.41%。備份：`D:\stock\backups\claude_20260919_single_stock_weight_floor\`、
`…_model_version\`。

## 追加修正：預測留存與實際誤差帳本（S1＋S3，2026-09-19）

使用者的工作方式是「模型先用現有資料判斷，等實際失準再修正」。既有的
`apply_three_month_history_calibration`／`apply_tomorrow_history_calibration` 本來就是這個機制，
但一直沒有樣本，原因與修法：

- **S1：正式執行從不留存預測。** `record_prediction_snapshot` 寫在兩支分析程式的 `if not batch_transaction:`
  之後，而正式 CMD 一律是 versioned-run（batch）模式，所以正式預測完全沒有被保存，日後無從結算。
  改為由發布端 CLI（`tw_single_stock_latest_report.py --publish`）在 pointer 原子更新**成功之後**，
  讀取已提交的兩份 summary 寫入歷史帳本；寫入失敗只回報警告，絕不回捲已發布的 pointer。
  發布函式本身維持單純（不寫歷史），避免既有測試在真實快照區留下資料。
- **S3：新增 append-only 實際誤差帳本** `tw_stock.single_stock_settlement_ledger_v1`，
  位置 `D:\TWStockData\program_data\dstock_single\audit\settlement\<mode>.jsonl`，
  每個已到期目標只寫一次（去重鍵：mode／stock／目標日／模型版本／資料日）。
  每列含預測值、實際值、誤差、方向是否命中、機率、價格基準、資格 role 與 reason codes、是否同版本。
  **跨版本紀錄照樣保留並標記**，模型改版不再讓實際成績歸零。

實跑驗證（2026-09-19 13:30–13:39，退出碼 0）：發布後兩個模式的預測都已存入歷史帳本
（三個月目標日 2026-12-22、次日開盤目標日 2026-09-21）；次日開盤帳本寫入 7 筆已到期紀錄——
方向命中 1/7、平均誤差 −0.62 個百分點（模型偏高）、MAE 1.20 個百分點。
證據：`D:\stock\logs\single_stock_pit_20260919\settlement_ledger_tomorrow_open_20260919.jsonl`、
`formal_run_20260919_1330_*`。備份：`D:\stock\backups\claude_20260919_single_stock_settlement\`。

## 追加修正：published 模式的結算證據（S2，2026-09-19）

目標：讓 published 模式的預測能成為「可用於校正」的結算證據，既有的
`apply_three_month_history_calibration`／`apply_tomorrow_history_calibration` 才會有樣本。
資格契約本身不檢查「是不是 Central」，只要求那組欄位非空且為合法雜湊，因此修法是**把 published 模式的
等價證據填進同一組欄位**，而不是放寬契約：

| 契約欄位 | published 對應（全部可驗證） |
|---|---|
| central_route | `published_shared_market_data` |
| central_session_id | 固定版本 catalog 的 fingerprint |
| central_session_immutable | true（物件以內容雜湊固定） |
| receipt_sha256 / revision | 共同資料區 head 的位元組雜湊 |
| artifact_sha256 | 已固定 artifact catalog 的 descriptor 雜湊 |
| receipt_run_id / publication_run_id | head 的 producer run id（新增：reader 從 head 讀 `producer_id`／`run_id`／`published_at`） |
| vintage | expected_asof |

另外三項規則調整，全部保留原紀錄、只改「是否阻擋」：

1. **價格基準**：共同資料區只有還原價，`tradable` 永遠 unknown。新增明確的
   `settlement_scope = model_basis_only`：預測與實際值以同一基準比較時，未驗證的「還原價→可交易價」
   對應限制的是可執行性宣稱，不是誤差衡量。model 基準未知仍然拒絕。
2. **可執行性註記**：`research_only` 決策狀態、缺少成本／治理指紋改列 `actionability_notes`，不再阻擋
   結算證據；`not_actionable`／`unavailable`／`rejected`（沒有可用預測）新增
   `decision_status_not_predictive` 並維持阻擋。
3. **跨執行差異**：過去的預測必然來自較早的資料，PIT 指紋與資料 lineage 與當次執行不同是必然結果，
   改列 `cross_run_notes`；**模型版本不同仍然排除**（校正證據必須描述被校正的模型，既有測試
   `test_history_legacy_missing_lineage_and_cross_version_are_excluded` 的保證不變）。

資格契約版本升為 2（`PREDICTION_HISTORY_ELIGIBILITY_VERSION`），新增 `actionability_notes`、
`cross_run_notes`、`settlement_evidence_scope` 三個輸出欄位。

驗證：以真實 pinned descriptor 與真實報告做探測，published 模式的已結算預測取得
`role=formal`、`eligible=true`，三項 actionability 註記照實保留
（`D:\stock\logs\single_stock_pit_20260919\published_eligibility_probe.json`）；
沙箱全套測試相對基準（140 失敗／661 通過）沒有新增失敗，另修好 6 項既有失敗。
備份：`D:\stock\backups\claude_20260919_single_stock_published_evidence\`、`…_cross_run_notes\`。

**仍待真實到期驗證**：今天（09-19）存下的次日開盤預測目標日為 09-21，屆時才會出現第一筆
「published 模式、同版本、已到期」的正式證據並觸發自動校正；三個月的第一批在 12-22 到期。

## 實際正式執行（2026-09-19 09:33–09:42）

`1_執行個股分析與產生報告.cmd 2330 --source-mode published` 退出碼 0，耗時 561.6 秒，
單股程序樹峰值 380 MB（同時有 MIX 在跑，記憶體守門未觸發）：

- 三個月：偏多、上漲機率 70.98%、預估三個月報酬 9.00%；`estimate_basis` 明確標示
  「歷史樣本基準（預測模型未通過樣本外增益門檻）」、信賴度「低」。
- 次日開盤：平盤附近、信賴度低、開高機率 49.15%，預估開盤 2460.0、最高 2480.0、最低 2445.0。
- `latest_report_manifest.json` 原子更新為 `single_stock_run_8dd7819661a14243a8e1c97754c7dd4a`。
- 證據：`D:\stock\logs\single_stock_pit_20260919\formal_run_20260919_*`。

## 後果

- 正式三個月與次日開盤可以進入模型階段；能否產出報告仍待一次完整正式執行確認。
- 模型無技能時報告會顯示歷史基準估計並標示低信賴：閱讀報告時必須看 `estimate_basis` 與信賴等級，
  不能把基準數字當成模型預測（2330 目前即是此情況）。
- 可取得時間變動會改變低頻特徵的對齊，模型快取因 PIT 指紋改變而重新訓練。
- 殘餘風險：公司晚於法定期限申報、財報重編（來源沒有版本歷史），兩者都無法由推估涵蓋；
  取得實際公告時間（選項 C）仍是後續可選的改善。

## 回復方式

把 `D:\stock\backups\claude_20260919_single_stock_pit\` 的 6 個原檔複製回
`D:\stock\programs\single_stock_analysis` 對應路徑即可完整回復；回復後個股分析會回到
「正式資格檢查必擋、無法產生報告」的狀態。不使用 git reset、clean 或刪除其他內容。
