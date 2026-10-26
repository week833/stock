# D-stock AI 工作系統 Handoff

## 2026-09-19 個股分析正式 PIT 資格修復（目前狀態）

- **Fact｜原本的阻塞**：個股分析自改讀共同資料區後，正式三個月與次日開盤都在
  `require_formal_single_stock_input_quality` 失敗（`formal_*_pit_qualification_incomplete`），
  最後一份正式報告停在 2026-08-24。2026-09-17 以真實資料模擬 2330 取得四個原因碼：
  `pit_metadata_no_available_rows:industry`、`pit_legacy_availability_basis:monthly_revenue`、
  `pit_publication_time_unverified:monthly_revenue`、`pit_publication_time_unverified:company_financials`。
  證據：`D:\stock\logs\single_stock_pit_20260919\gate_*_before_20260917.json`。
- **Fact｜決定與實作**：依 [ADR-0005](decisions/ADR-0005-single-stock-statutory-availability.md)，
  低頻資料改以法定公告期限（月營收次月10日、Q1 5/15、半年報 8/31、Q3 11/14、年報 3/31）
  順延假日後的下一個交易日作為 `available_at`，basis 維持 `estimated_conservative_lag`、
  `publication_time_verified=False`；資格檢查只接受這個 basis，legacy/index/unknown 仍拒絕；
  沒有欄位進入模型的群組只登錄 QC。修改檔：`src\tw_industry_data.py`、
  `src\tw_single_stock_3m_analyzer.py`、`src\tw_single_stock_tomorrow_open.py` 與三個對應測試。
- **Fact｜驗證**：修改後對 2330 以真實共同資料（資料日 2026-09-18）模擬到資格檢查點，
  三個月與次日開盤皆 `eligible=true`、`reason_codes=[]`，過程無網路，正式程式碼／共同資料區 head／
  報告 manifest 前後雜湊一致。測試由 140 失敗／661 通過變成 137 失敗／665 通過。
  證據：`D:\stock\logs\single_stock_pit_20260919\`。
- **Fact｜備份**：修改前原檔在 `D:\stock\backups\claude_20260919_single_stock_pit\`（含 `original_sha256.txt`）。
- **Fact｜實跑後再修的三類既有門檻**（使用者同意，詳見 ADR-0005 追加段落）：
  正式閘門原本拒絕「模型未勝過基準」的基準估計（與 v3 契約矛盾）；ETF／ETN 沒有月營收財報被當成資料缺漏；
  次日開盤的 fallback 偵測過寬（命中校正描述 `calibration.probability.fallback="identity"`）。
  另修兩個純缺陷：`maybe_write_tomorrow_research_audit_only` 缺 `session_sequence` 參數（每次正式執行必炸）、
  `fallback_tomorrow_open_model` 引用未定義的 `central_lineage`。
  備份：`D:\stock\backups\claude_20260919_single_stock_formal_gates\`、`…_tomorrow_defects\`。
- **Fact｜正式執行成功**：2026-09-19 09:33–09:42 以 `1_執行個股分析與產生報告.cmd 2330 --source-mode published`
  實跑，退出碼 0，三個月與次日開盤報告均產出，`latest_report_manifest.json` 更新為
  `single_stock_run_8dd7819661a14243a8e1c97754c7dd4a`。三個月估計為歷史基準（模型未過樣本外門檻），
  報告已標示「歷史樣本基準…」與信賴度「低」。
- **Fact｜正式估計改為模型主導（使用者決定）**：新增權重下限 `RELIABILITY_MODEL_WEIGHT_FLOOR = 0.5`，
  可靠度閘門結果照舊完整記錄但不再把模型權重歸零；狀態依閘門權重標為
  `floor_blended_model_*_reliability_unsupported`，報告顯示「模型估計與歷史基準混合（…採權重下限 50%）」、信賴度「低」。
  模型版本升為 `v20-model-weight-floor`（否則正式執行會沿用舊快取；09-19 12:54 那次即如此）。
  2330 重跑：上漲機率 74.97%、預估報酬 +10.41%（模型 78.96%／+11.82%，基準 70.98%／+9.00%），
  run=`single_stock_run_92d000c7a365401db5318c19d4343b75`。次日開盤未套下限（其權重為樣本外最佳凸組合）。
- **Fact｜S1＋S3 已完成（2026-09-19）**：發布端 CLI 在 pointer 原子更新成功後，把兩個模式的預測寫入歷史帳本
  （寫入失敗只警告，不回捲已發布 pointer）；新增 append-only 實際誤差帳本
  `program_data\dstock_single\audit\settlement\<mode>.jsonl`（schema `tw_stock.single_stock_settlement_ledger_v1`，
  每個到期目標只寫一次，跨版本紀錄保留並標記）。實跑驗證：次日開盤已寫入 7 筆到期紀錄——
  方向命中 1/7、平均誤差 −0.62 個百分點、MAE 1.20 個百分點；今天的預測已留存（三個月目標日 2026-12-22、
  次日開盤 2026-09-21）。備份：`D:\stock\backups\claude_20260919_single_stock_settlement\`。
- **Fact｜S2 已完成（2026-09-19）**：published 模式的預測現在可成為結算證據。做法是把可驗證的 published
  證據填進資格契約既有的欄位（head 雜湊＝receipt/revision、catalog 雜湊＝artifact、head 的 producer run id＝
  receipt_run_id、catalog fingerprint＝session id、expected_asof＝vintage），並新增三項規則調整：
  價格基準 `settlement_scope=model_basis_only`、`research_only` 與成本／治理指紋改列
  `actionability_notes`、跨執行的 PIT 與 lineage 差異改列 `cross_run_notes`（**模型版本不同仍然排除**）。
  資格契約版本升為 2。探測證據：`D:\stock\logs\single_stock_pit_20260919\published_eligibility_probe.json`
  取得 `role=formal`、`eligible=true`。備份：`…_published_evidence\`、`…_cross_run_notes\`。
- **Not verified｜第一筆真實到期證據**：09-19 存下的次日開盤預測目標日為 2026-09-21，屆時才會產生第一筆
  「published、同版本、已到期」的正式證據並觸發 `apply_tomorrow_history_calibration`；三個月的第一批 12-22 到期。
- **Not verified**：Telegram 傳送未執行；其他股票（僅另以唯讀模擬檢查 2317、0050 的閘門）未做完整正式執行。

## 2026-08-30 舊內容整理現況（目前狀態）

- **Fact｜退役程式**：D-stock 的 `programs\full_market_analysis` 與 `programs\daily_analysis_scheduler` 已退役且不在現行目錄；`programs\shared\UPDATE_GLOBAL_MARKETS.cmd` 也已移出現行路徑，沒有現行 caller。
- **Fact｜歷史隔離區名稱**：`D:\stock\_DELETE_OLD_CONTENT_20260830` 是本輪曾使用的歷史隔離區名稱；2026-08-30 live observation 已確認該路徑不存在。此前三次由 agent 嘗試永久刪除均在 PowerShell 啟動前收到 executor policy `CreateProcess rejected: blocked by policy`；這是歷史執行紀錄，不可宣稱本代理的刪除動作成功。
- **Fact｜資料邊界**：舊 `D:\stock\data` 已不存在於 live tree；其中 27/27 JSON 與 canonical `D:\TWStockData\shared_market_data` 的 SHA-256 一致。中央 owner 維持 `D:\TWStockData` 的 48 routes。
- **Fact｜歷史日誌 live absence**：2026-08-30 live observation 已確認 `D:\stock\logs\daily_analysis_scheduler` 不存在；該舊 D-stock scheduler 日誌不再是 live source 或現行 scheduler。
- **Fact｜User PATH**：2026-08-30 live observation 確認舊的 `D:\stock\programs\full_market_analysis` segment 已移除 1 筆（35→34），target count 為 0；Machine PATH 未變更，其餘 segment 順序與文字保留。
- **Fact｜仍在使用的 runtime**：根 `D:\stock\.venv` 與 `programs\environment_setup` 因 Running 的 `DStockTelegramCommandBot` 直接依賴而保留；single-stock canonical runtime 是 `D:\stock\GitHub\.venv`。`programs\shared` 的 refresh 維持 `central_owner_only`／fail-closed。
  - 2026-10-12 註記：現行 runtime 為 D:\stock\.venv；INSTALL_SINGLE_STOCK_3M_DEPS.cmd 已移至 single_stock_analysis\tools\_retired\
- **Fact｜runtime verifier FAIL（2026-08-30）**：`D:\stock\programs\VERIFY_RUNTIME_DATA_PATHS.cmd` 退出碼 1；`D:\TWStockData\program_data\dstock_daily\state\runtime_path_health_latest.json` 為 `status=fail`、`exit_code=1`、2 errors、0 warnings。錯誤為 `source_directory_missing`（`D:\TWStockData\program_data\dstock_daily\industry_operational`）與 `mix_data_source_not_relative`（`mix_config_investor_sector_v1_4.json:data_sources.history_cache`）。本次舊內容清理不修復 Central／MIX，該 FAIL 維持未決。

> 這是可持續更新的目前交接文件，不建立日期化副本。大型 JSON、log 與報告只引用路徑，
> 不在此複製內容。除非另有明確授權，本文件不代表已執行正式分析、下載、發布或 Git 提交。

## 2026-08-29 Central live 修正交接（已停止的 point-in-time handoff）

- **Fact｜觀測點與停止狀態**：觀測快照為 `2026-08-29T19:36:49+08:00`（Asia/Taipei）；Claude transcript 於 19:38 明確記錄「已停止。這是重新規劃。」Central/Claude 程序鏈已全部退出；`tasks/byzz41r6s.output` 記錄 `exit=-1`（`[exited with code 255]`），本輪為失敗中止。這是停止時的證據交接，不是完成或發布證明。
- **Fact｜本次 run**：`run_id=market-20260829T044023Z-b54433ec40`、`run_token=5b89cf901227467ca4f4d5c65348552b`、`expected_asof=2026-08-27`。停止前最後 `D:\TWStockData\services\market_update\state\heartbeat.json` 與 `ensure_live.txt` 記錄為 `dataset_id=finmind_price_unadjusted_fallback`、`10281/24997`、`detail=unit_start`、`state=running`；`active_run.json` 仍為 `status=running`、`pid=34684`，但 owner PID 已死，故屬 stale marker。
- **Inference**：heartbeat 的 running 與程序全退並存，代表本 run 沒有可接受的正常終態；不得把最後 progress 當作成功、完整更新或可 resume 的授權。

### Live 路由與資源證據

- **Fact｜immutable checkpoint 最終分類（36/48）**：已觀測的 36 個 dataset 中，23 `complete`、6 `fail_closed`、4 `partial`、2 `blocked_adapter`、1 `blocked`；第 37 條中斷，剩餘路由沒有 final checkpoint，故不可視為 48 路由完成。
- **Fact｜checkpoint route 詳情**：`finmind_dividend` 與 `finmind_dividend_result` 均 `fail_closed`、`error=finmind_sparse_event_counts_invalid`、coverage `1948/1948`；`official_active_etf_change` 為 `fail_closed`、`official_active_etf_producer_input_conflict_same_run`，holding 伴隨 `TypeError`；`finmind_active_etf_holding` 與 `finmind_active_etf_change` 均 `blocked_adapter`、`adapter_execution_disabled_until_app_integration`；`official_sector_features` 為 `blocked`、zero denominator `0/0`。
- **Fact｜checkpoint 未接受與自相矛盾**：`finmind_cash_flows_statement` 為 `1941/1948`、`content_conflict`；`finmind_day_trading` 為 `391/2071`、`content_conflict`，而 isolated 對照記錄為 `2071/2071`，兩者不一致；`finmind_kbar` 與 `finmind_trading_daily_report` 各為 `2070/2071`、`quota_reservation_error`。`official_trading_restrictions` 為 `fail_closed`、`canonical_publish_skip_reason=coverage_missing_unit_fail_closed`，但 source receipt verified、coverage `9/9`、208 records、schema/hash/freshness 均通過且允許合法 0-row，顯示完成判定自相矛盾。上述數字以 immutable checkpoint 為準，停止後須先驗證 chain；不可用中途 log 或 isolated 數字湊成成功。
- **Fact｜中斷位置**：Claude profiler/停止前記錄顯示 48 個 dataset 中 36 個有 checkpoint，第 37 個在 `finmind_price_unadjusted_fallback` 中斷；`D:\TWStockData\services\market_update\state\checkpoints\market-20260829T044023Z-b54433ec40\00000037-361efe7a1207cd93.json` SHA-256 為 `C4761924130CEFF927B3C05CA095314B4181C816EB34E129378AA7831A6A8429`；最後 `10281/24997` 仍是 `unit_start`，不是 dataset complete。
- **Fact｜資源風險**：worker working-set 峰值約 `52.77 GB`、Private Bytes 約 `64 GB` 且大量換頁；長單元／aggregation 曾出現約 13–15 分鐘 heartbeat 空窗。Claude profiler 的 8/8 樣本均命中 `adapters/finmind.py` 的 `_write_checkpoint` → `_atomic_stage_json` → `json.dumps(indent=2)`／SHA-256／`_stage_destination_matches`（`adapters/finmind.py:969` `_sha256_bytes`、`3316` `_stage_destination_matches`、`3430` `_atomic_stage_json`、`8145` `_write_checkpoint`，呼叫 checkpoint `8270`），估算每 unit 重寫約 4.3 MB、累積 checkpoint 形成 O(n²) 成本，CPU 約 96%、API quota 0%、約 47.6 units/min。
- **Not verified**：上述 profiler 數字未在本交接中重跑；快取、跳過 stage comparison、移除 indent 只是候選方案，未核准。移除 indent 會改變 JSON/hash，必須先做契約稽核。

### Source／Git provenance（必須分離）

- **Fact**：separate worktree `D:\TWStockData\services\market_update` branch `cleanup/central-clean-code` 的 HEAD 為 `77275685723fc40713aa13d8c3264c467a6cddc1`；`market_update.py` SHA-256 為 `5AB5C966B481CD38230286913A17F4278E0CBEA76D4CFEA960A9F2858400D85A`。
- **Fact｜啟動時 source 狀態**：本 run 於 12:40 啟動時觀察到 `market_update.py` 與 `workflow_lock.py` 皆 dirty；Claude 於 12:46 才以 `7727568` commit 收錄 `market_update.py`，而執行程序已先載入來源，因此事後 HEAD 不能證明本 run 的 source identity。
- **Fact｜停止後 source 狀態**：停止後 current status 唯一未提交檔為 ` M workflow_lock.py`，38 行差異，當下 SHA-256 為 `0A3A65E2F9BCB42DAC61B85A8661FDCC105F59F1DEADC1A364B6D9FB0538B0AA`；此變更屬另一 concurrent writer。Git 無法證明 agent 歸屬，不得歸給 Claude 或與 `market_update.py` 合併判定。
- **Fact**：相關 committed history 包含 `2d96d80`（hardlink 部分修復）、`a6d79a8`（progress heartbeat 的 schema-aware owner identity）與 `7727568`（clean-start 依實際 schema fallback）。Co-Authored-By trailer 不是 agent 歸屬的獨立證明；後續須以固定 source SHA 與 writer provenance 分開驗收。
- **Static contract warning**：`market_update.py:2048` 在 `os.link`（`2055`）之前以 `allow_nlinks=(1,2)` 讀 temporary，會接受 pre-link 已為 nlink=2 的外部 hardlink；post-link `2494`、`2501`、`2510-2515` 才嚴格要求 nlink=2。契約應為 pre-link nlink=1、post-link nlink=2；目前 patch 是部分修復且弱化 pre-link ownership，不是根治。
- **Static recovery warning**：`central_publication_recovery_v2.py:828`、`855-856` 對 `progress_heartbeat` 採 schema-aware 的 owner identity optional 規則，但 `tests/test_central_publication_recovery_v2.py:993-1008` 仍直接期待缺少 heartbeat owner identity 產生錯誤，測試與現行契約漂移。
- **Fact｜歷史 FullUnion 背景結果**：三次背景測試為 `1 pass/2 fail`，但外層 shell 錯誤顯示 0；這是外層 exit evidence，須與測試內容分開追查。
- **Static wrapper warning**：`tests/test_central_migration_recovery_v3.py:274-292` 在程序內呼叫 `recovery.main(args)` 並斷言 `exit_code=0`，不足以驗證 Windows wrapper/child exit；須補真正 wrapper exit 傳遞驗收，不把此 in-process test 當成外層假 0 的根因。

### Publication／receipt 狀態

- **Fact｜前一 transaction 已合法收束**：`market-20260828T074953Z-bbcbbbaa87/journal.json` 顯示 `external_mutation_started=true`、22/22 entries `committed=true`，且 successor handoff 已合法轉為 `status=rolled_back`／`phase=rolled_back`；同時 `latest_committed=false`、`pointer_promotion=null`、`committed_terminal_proof=null`，terminal record path 尚無 proof。不得把它寫成仍待決的 active transaction。
- **Fact｜本次未終結 transaction**：`market-20260829T044023Z-b54433ec40/journal.json` 為 schema v1、`status=active`、`phase=external_mutation_started`、`target_count=55`；immutable event sequence `151`，最新 `event-00000151` progress 為 `55/55`；entries `55/55 committed=true`、`backup_ready=55/55`、`had_original=35`、`content_identity_decision=created` 54、`reused` 1，但 `latest_committed=false`、`pointer_promotion=null`、`committed_terminal_proof=null`，terminal 宣告路徑不存在。journal SHA-256 為 `725FE0315E43C0388498CAC44A4B1F292FBF6535F89961492F776D688BE2785C`。這是最優先 recovery 邊界；其 `active_run/heartbeat` 仍標 running，而 owner PID 已死。
- **Fact｜舊 receipt**：`D:\TWStockData\services\market_update\receipts\latest_manifest.json` 仍為舊 run、`expected_asof=2026-08-21`、`conditions_met=false`。它不能替代本次 `2026-08-27` run 的 receipt、pointer 或 terminal proof；`D:\TWStockData\services\acceptance\latest.json` 仍是舊的 `overall_status=blocked`。

### Claude 後續完整修正順序與最窄驗收

1. **保存證據／建立 no-writer 邊界**：保留 live log、heartbeat、active_run、current journal、immutable event/checkpoint chain 與 dirty worktree；確認 writer/owner 全退，建立兩次一致的 source/Git snapshot。驗收是證據 hash 可重現、無程序競爭，不清除 marker。
2. **分離 source／Git provenance**：固定 branch/HEAD、`market_update.py` SHA 與 `workflow_lock.py` dirty SHA，讓 concurrent writer 先獨立交接；不覆寫、stage、commit 或把 ownership 猜給 Claude。驗收是授權 diff 與來源歸屬逐檔可追溯。
3. **合法處理本次 transaction**：先驗證 `market-20260829T044023Z-b54433ec40` 的 immutable journal/event/checkpoint chain、owner proof、backup 與 native identity，再由專用 recovery 依現存備份做 rollback/compensation；禁止直接刪 `active_run` 或 resume。驗收是產生可驗證 terminal/rollback proof，且每個 target identity 有結果。
4. **逐路由修正但保留完整 union**：處理上述 error、未接受計數與 formal/isolated 漂移；永遠保留 48 routes（40 required + 8 optional），不以 retire、跳過或放寬條件湊數。驗收是 48 路由各有 explicit outcome，40 required 全部合格，8 optional 仍有明確狀態。
5. **有界記憶體／增量 checkpoint／串流 lineage**：針對 O(n²) checkpoint、4.3 MB/unit、換頁與 long aggregation 設計 bounded chunk、增量 checkpoint、streaming lineage；profiler 候選不得直接採用。驗收是 peak/resource budget、checkpoint hash/lineage 與 unit progress 可逐步重建。
6. **週期 liveness**：長單元內週期寫 heartbeat，owner identity 依實際 schema 分層，dead owner fail-closed；消除 13–15 分鐘空窗而不偽造 progress。驗收是 liveness 間隔受界、stale/dead owner 可辨識、heartbeat 不再被當成終態。
7. **修 hardlink／recovery／wrapper**：恢復 pre-link nlink=1、post-link nlink=2 的 ownership contract；更新 schema-aware recovery 測試，並讓 Windows wrapper 傳遞真實 child exit。驗收是 pre-linked temporary 被拒、合法 1→2 transition 有專門測試、wrapper 失敗不假報 0。
8. **固定 source SHA 後做最窄測試**：先封存/核對 source SHA，再只執行 hardlink、recovery、wrapper、route/receipt 直接相關的最窄測試；本次交接未執行測試。驗收是 targeted tests 與 static diff 均通過，且不以 fixture/in-process 假綠代替 runtime 證據。
9. **受控 successor／resume／incremental**：只有 recovery terminal proof、source provenance 與路由修正簽核後，才依正式契約選擇受控 successor run；僅在契約明確支援且 identity／chain 可延續時 resume，否則建立新的 incremental run；不得把失敗 run 直接續成成功，也不得重抓或覆蓋未定案 targets。驗收是 checkpoint continuation 與 owner/lease、route union、source/registry hash 一致。
10. **同一成功 successor 的 receipt／pointer／terminal proof／acceptance 全通過才可完成**：receipt、latest pointer、committed terminal proof 與 acceptance 必須全部屬於同一個成功 successor run，不得把 rolled-back/current failed run 拼成成功；並確認 `conditions_met=true`、expected_asof 正確、48 routes 與 required/optional outcome 完整，最後 `acceptance/latest.json` 不得為 `fail`／`blocked`。在此之前一律標記 incomplete/not verified，不得宣稱完成或發布。

## 2026-08-23 既有治理基線（歷史快照，不代表目前路徑）

- `as_of`: 2026-08-23T21:27:01.519728+08:00 (Asia/Taipei)
- `overall_status`: `governance_optimizations_applied`
- `verification_status`: `blocked`
- `release_status`: `blocked`（程式契約已強化，但既有正式產物與外部狀態仍未通過 acceptance）
- `scope`: 四類治理資產，以及 source-log、shared-data、data-quality、stable-path、structure 與 Mitake 安全契約優化。
- `objective`: 讓後續 AI／Agent 能依固定順序取得現況、合格來源、既有決策與驗收證據，並在證據不足時停止。

## Git 基線（Fact）

- 根目錄：`D:\stock`
  - branch：`main`
  - HEAD：`966ca23bc0d1595fd1713d93d8117c3c1377c9a1`
  - `git status --porcelain=v1 --untracked-files=all`：530 個狀態項；modified 13、deleted 78、untracked 439、unmerged 0。
- 研究來源子庫：`D:\stock\GitHub`
  - branch：`codex/mitake-ui-catalog`
  - HEAD：`2b03bff44ae7b2dd9644755dc3fb8220da574b59`
  - `git status --porcelain=v1 --untracked-files=all`：215 個狀態項；modified 33、deleted 41、untracked 141、unmerged 0。
- 本階段未 stage、commit、push、clean、reset，也未保證目前內容已由 Git history 保存。
- 外層 `D:\stock\.gitignore:31` 仍忽略 `GitHub/`；nested `D:\stock\GitHub\.gitignore` 已取消 `repos/.source_provenance.json` 的 nested ignore，目前 nested worktree 為 `.gitignore` modified、ledger untracked。仍未 stage／commit，Git durability 尚未獲授權。

## 權威證據與邊界（Fact）

| 用途 | 權威路徑 | 本次觀察 |
|---|---|---|
| 中央市場資料 receipt | `D:\TWStockData\services\market_update\receipts\latest_manifest.json` | `status=complete`、`publishable=true`、`conditions_met=false`、`fail_closed=false`、`partial=false`、`expected_asof=2026-08-21`、`coverage.all_verified=true` |
| 正式報告 manifest | `D:\TWStockData\program_data\dstock_daily\reports\latest_report_manifest.json` | `status=publishable`、`publishable=true`、`fail_closed=false`；三竹同步狀態為 `pending` |
| runtime path health | `D:\TWStockData\program_data\dstock_daily\state\runtime_path_health_latest.json` | 2026-08-30 最新觀察為 `status=fail`、`exit_code=1`；2 errors、0 warnings。包含 `source_directory_missing` 與 `mix_data_source_not_relative`；本次清理不修復 |
| 共用資料路徑契約 | `D:\stock\config\shared_data_paths.json` | 正式共用資料根目錄為 `D:\TWStockData\shared_market_data` |
| 舊 shared data（legacy，已隔離） | `D:\stock\data` | live tree 已無此資料；27/27 JSON 與 `D:\TWStockData\shared_market_data` 對應檔案 SHA-256 一致；不可作正式來源 |
| 研究來源登錄 | `D:\stock\docs\SOURCE_REGISTRY.md` | 已建立；定義 formal-data、research-only、reference-only 邊界 |
| 研究來源清單／provenance | `D:\stock\GitHub\scripts\sources\source_manifest_v1.json`、`D:\stock\GitHub\repos\.source_provenance.json` | 39 個 canonical research Repo；39/39 valid；另有 reference-only AI／Agent 家族 |
| 決策索引／ADR | `D:\stock\docs\decisions\README.md`、`D:\stock\docs\decisions\ADR-0001-ai-core-four-assets.md` | 已建立；既有領域 JSONL／矩陣仍為各自紀錄 |
| 統一 acceptance 成品 | `D:\TWStockData\services\acceptance\latest.json` | schema `cross_system_acceptance_v1`；`generated_at=2026-08-23T13:27:01.519728+00:00`（台北 21:27:01.519728）；`mode=reports`、`fixture=false`、selected 2／expected 6、`overall_status=blocked`、2 warnings、1 blocking failure；SHA-256 `5876A1BA42F76671D37299F02BFCF153E7D3239B030983AA68BA89EB95280359` |

## 四類資產進度

### 1. 來源登錄

- `D:\stock\docs\SOURCE_REGISTRY.md` 已建立，提供 formal-data、research-only、reference-only 的權威邊界。
- SOURCE_REGISTRY 的 stale shared-root 清單已更新；原列六份文件的舊 shared root active 命中數為 0，歷史／legacy 文字已與現行 routing 邊界分開。
- `D:\stock\GitHub\repos\.source_provenance.json` 已建立；39 個 canonical research Repo snapshot 為 39/39 `valid`，研究 manifest SHA 已記錄；snapshot 不等於 nested Git 歷史基線。
- AI／Agent reference-only 家族實際 9 個，README 已登錄 9 個；其中 `stock-mitake-ui-reference` 的 dirty worktree 不得整理或覆寫。
- 邊界：SOURCE_REGISTRY 不重複中央 dataset 或外部 Repo 完整清單；正式模型只能使用中央 receipt 宣告合格的資料。

### 2. 決策紀錄

- 已有領域紀錄，例如模型校正歷史、研究 Repo 去重矩陣與邏輯採用文件。
- `D:\stock\docs\decisions\README.md` 與 `D:\stock\docs\decisions\ADR-0001-ai-core-four-assets.md` 已建立。
- ADR 狀態 `accepted` 僅代表四資產治理決策已核准，不代表驗證、報告或發布通過；既有 JSONL、領域紀錄及研究矩陣只引用，不搬移、不覆寫、不改成第二套模型決策來源。

### 3. 驗證報告

- 既有正式報告 manifest 與 runtime health 皆可引用。
- `D:\TWStockData\services\acceptance\latest.json` 已重新產生；四資產的驗證報告成品存在，但 `overall_status=blocked`。
- 最新 acceptance 明確分開 1 個 blocking failure（`source_log_not_file`）與 2 個 non-blocking warnings（`mitake_watchlist_pending`、`shared_data_verification_missing`）；warnings 不得稱為已通過。
- 歷史快照（路徑已退役並隔離）：`D:\stock\programs\full_market_analysis\VERIFY_LATEST_REPORT.cmd` 的根層 verifier 結果為 0；此歷史結果不覆蓋 acceptance blocked。

### 4. Handoff

- 本文件已建立並完成本輪最終刷新，供後續 Agent 續作。
- Handoff 不取代來源登錄、決策紀錄或 acceptance；只記錄目前狀態、證據指標、限制與下一步。

## 本階段已完成

- 四類資產實作與本輪安全優化完成：SOURCE_REGISTRY、39 Repo provenance、decisions／ADR、acceptance latest、Handoff 均有目前成品。
- 針對本輪範圍的驗證摘要為 `115 passed`、`2 skipped`、`3 subtests`；未執行完整測試套件。
- stable path 歷史證據為 296 個登錄項；目前唯一 manifest 為 195 個現行路徑。structure-only 的全市場／個股結果均屬當時快照，不能當作目前路徑驗收。
- 取得並記錄兩個 Git worktree 的 branch、完整 HEAD，以及 `--untracked-files=all` dirty 統計。
- 建立本文件，明確區分 `Fact`、`Inference` 與 `Not verified`。
- 本輪新增至 `D:\TWStockData` 的資料／驗收證據成品只有 `D:\TWStockData\program_data\dstock_daily\reports\audits\20260821\data_quality_audit_20260821.json` 與 `D:\TWStockData\services\acceptance\latest.json`；未修改原始行情、模型、正式報告或外部服務，未取得 Git durability 授權。

## 本輪安全優化（Fact）

- 歷史 source-log 契約（相關 D-stock full-market／old daily scheduler 路徑均已退役並隔離）：`programs\full_market_analysis\tools\internal\finalize_formal_run_evidence.py` 與舊 launcher／scheduler 已採兩階段契約；child 期間只寫 provisional context，最外層 writer 關閉後才以同一 run ID 原子寫入 final size、SHA-256、mtime 與 child exit code。既有正式 manifest 指向的舊 log 仍不存在，不能以程式修正回填舊證據。
- Shared data：`programs\cross_system_validation\src\verify_stock_programs.py` 只接受 canonical root `D:\TWStockData\shared_market_data`、`SHA256`、空 `issues` 與大於 0 的 `totals.checked_files`；目前 verification 成品仍缺失。
- Data quality：PIT-safe 同股票跨 market newer sibling 才分類為 `superseded_market_path`；真 stale 與 invalid OHLC 仍保留。audit 的詳細統計列於下方，未執行 `--repair-invalid`。
- Stable／structure（2026-08-23 歷史快照）：canonical entry 必須存在，已登錄 alias 可共存；當時 stable path 登錄數為 296。2026-08-30 目前清單為 195，structure-only 尚未在本次清理中執行。
- Mitake：retry 預設唯讀 preflight，`--execute` 與 `--auto-start` 均需明確指定並受 postconditions 約束；本輪未執行 GUI 或 `--execute`。

## 驗證結果（Fact）

證據主檔：`D:\TWStockData\services\acceptance\latest.json`（schema `cross_system_acceptance_v1`、`mode=reports`、`fixture=false`、`generated_at=2026-08-23T13:27:01.519728+00:00`、selected 2／expected 6、SHA-256 `5876A1BA42F76671D37299F02BFCF153E7D3239B030983AA68BA89EB95280359`）。

- 最新驗收基線：`overall_status=blocked`；程式契約、fixture、root/source/diff 基線已完成，但既有正式 source-log evidence、Mitake 外部狀態與 shared-data verification 仍阻止正式發布。
- Fixture verifier：3 passed；真實系統未啟動、未寫入。
- 歷史（路徑已退役並隔離）根層 `D:\stock\programs\full_market_analysis\VERIFY_LATEST_REPORT.cmd`：退出碼 0。
- Source checks：退出碼 0。
- Diff check：退出碼 0。
- 錯誤參數傳遞檢查：退出碼 2，為預期的參數錯誤結果。
- Acceptance（2026-08-23 歷史快照）：`overall_status=blocked`；1 blocking failure（`full_market_analysis:source_log_not_file`）、2 warnings（`full_market_analysis:mitake_watchlist_pending`、`full_market_analysis:shared_data_verification_missing`）。其中 `full_market_analysis` consumer 現已退役。
- Stable paths（2026-08-23 歷史快照）：296 個登錄項；目前唯一 manifest 為 195 個現行路徑。
- Structure-only：全市場通過；個股正式 pointer 失敗，原因為 `single_stock_latest_report_manifest_missing`。兩套程式均保留 canonical entry 與 alias，未發現 unexpected entry。

## Acceptance 阻塞與警示（Fact）

### Blocking failure

- `source_log_not_file`：既有正式報告 manifest 所指的舊 source log 不存在：
  `D:\TWStockData\program_data\dstock_daily\logs\formal_runs\formal_run_dstock-formal-20260823T122147+0800.stdout.log`。兩階段 finalizer 契約已修正未來流程，但本輪沒有重跑正式分析。

### Non-blocking warnings（不是通過）

- `mitake_watchlist_pending`：三竹自選商品組合視窗無法唯一辨識；本輪未 `--execute`。
- `shared_data_verification_missing`：完整 shared-data SHA 驗證成品缺失；acceptance 列出的 reports、outputs、state 三個候選 `verification_latest.json` 路徑均未形成目前證據。

### Data-quality audit（已建立但資料仍有錯誤）

- 路徑：`D:\TWStockData\program_data\dstock_daily\reports\audits\20260821\data_quality_audit_20260821.json`；SHA-256 `C1CC424360AC6113B51C264C2D35DF767116CC0D82693E7A46E7D4CCD8D22298`。
- audit payload：`status=error`、`files.total=2356`、`healthy=2352`、`warning=3`、`error=1`、`invalid_ohlc=3`、`stale_history=4`、`superseded_market_path=4`。
- acceptance 的 data-quality evidence 已由「缺失」改善為 `status=pass`，其語義只代表 audit 檔案存在且 `dataDate=20260821` 相符，不代表原始資料品質已通過。
- 未執行 `--repair-invalid`；invalid OHLC 與真 stale 仍待資料權威／使用者決定。

### Program-level not passed

- `single_stock_analysis` 的正式 pointer `D:\TWStockData\program_data\dstock_single\reports\latest_report_manifest.json` 缺失；不可用 state／data-refresh pointer 代替。

### 限制與未驗證（Fact／Boundary）

- Acceptance blocker／warnings 仍指向既有 source log、Mitake pending 與 shared-data SHA 驗證；個股 pointer 是另一個 program-level fail；本文件不把契約修正誤標為外部證據已通過。
- `.source_provenance.json` 的 nested ignore 已取消，但外層 `GitHub/` 仍忽略；仍未 stage／commit，Git durability 尚未獲授權。
- 本次授權範圍不包含正式分析、資料刷新、模型訓練、Telegram 傳送、GUI `--execute`、資料修復或完整測試套件；這些操作未執行。
- 沒有在本輪重新計算大型資料集；中央 receipt、正式報告 manifest 與 runtime health 均為既有成品引用。

## 實際修改檔案

- 本次 Handoff 最終刷新：修改 `D:\stock\docs\HANDOFF.md`。
- 治理資產群組：`D:\stock\docs\SOURCE_REGISTRY.md`、
  `D:\stock\docs\decisions\README.md`、
  `D:\stock\docs\decisions\ADR-0001-ai-core-four-assets.md`、
  `D:\stock\GitHub\repos\.source_provenance.json`。
- 六份既有文件同步群組（歷史／已退役路徑，僅供 provenance）：
  `programs\full_market_analysis\README.md`（已退役）、
  `programs\full_market_analysis\FINMIND_FEATURE_SEED_IMPORT.md`（已退役）、
  `programs\full_market_analysis\FINMIND_FEATURE_UPDATER.md`（已退役）、
  `programs\shared\README.md`、
  `programs\shared_data_management\README.md`、
  `programs\telegram_report_delivery\README.md`。
- 同步修正已完成（歷史／已退役文件）：`programs\full_market_analysis\README.md`、
  `programs\shared_data_management\README.md` 與 `PROGRAM_FILES.md` 均以
   `D:\TWStockData\shared_market_data` 為 centralized canonical path；舊根目錄只保留歷史／legacy 邊界。
- 驗證與入口群組：`programs\cross_system_validation\README.md`、
  `programs\cross_system_validation\RUN_ACCEPTANCE_CHECKS.cmd`、
  `programs\cross_system_validation\src\verify_stock_programs.py`、
  `programs\cross_system_validation\tests\test_verify_stock_programs.py`、
   `programs\full_market_analysis\VERIFY_LATEST_REPORT.cmd`（已退役）。
- 本輪 source-log 契約（歷史／已退役並隔離）：`programs\full_market_analysis\1_執行每日股票分析與產生報告.cmd`（歷史／已退役）、
  `programs\daily_analysis_scheduler\RUN_SCHEDULED_ANALYSIS.ps1`（歷史／已退役）、
  `programs\full_market_analysis\tools\internal\finalize_formal_run_evidence.py`（歷史／已退役）、
  `programs\full_market_analysis\tools\internal\repackage_latest_report.py`（歷史／已退役）。
- 本輪 data-quality／Mitake 契約（歷史／已退役並隔離的 D-stock full-market 路徑）：`programs\VALIDATE_MARKET_DATA_QUALITY.py`、
  `programs\full_market_analysis\src\tw_mitake_daily_editor.py`（歷史／已退役）、
  `programs\full_market_analysis\src\tw_mitake_daily_watchlist.py`（歷史／已退役）、
  `programs\full_market_analysis\tools\internal\retry_mitake_daily_sync.py`（歷史／已退役）。
- nested provenance 可見性：`D:\stock\GitHub\.gitignore` 與 untracked
  `D:\stock\GitHub\repos\.source_provenance.json`。
- 前一階段已加入的治理指標仍在 `D:\stock\AGENTS.md` 與
  `D:\stock\PROGRAM_FILES.md`；本次未修改這兩個檔案。

## 下一個精確動作

以下均需新授權或另立子任務，本文件不自行執行：

1. 於獲授權的正式流程中產生同一 run 的 source log，待 writer 關閉後由 finalizer 完成 evidence，再重跑 acceptance。
2. 取得完整 shared-data SHA verification 成品；缺失前維持 warning。
3. 若要清除 Mitake pending，須另行明確授權 `RUN_MITAKE_DAILY_SYNC_RETRY.cmd --execute`；目前不得自行操作 GUI。
4. 針對指定股票完成正式個股分析，產生 `D:\TWStockData\program_data\dstock_single\reports\latest_report_manifest.json`；不得手工建立 pointer。
5. 對 invalid OHLC／真 stale 取得資料權威決定；`--repair-invalid` 需另行明確授權。
6. `PD-0001`、`PD-0002` 與 Git baseline 仍不視為核准；不得自行 stage／commit。
7. 所有外部證據補齊後重新刷新本 Handoff；在 acceptance 不再 `blocked` 前不得宣稱正式完成或發布。

## 阻塞與停止條件

- acceptance 成品為 `blocked` 或出現 blocking failure：停止，不宣稱完成或發布。
- source log 缺失是目前 acceptance blocking failure；不得用三項 warning 或根層 verifier 0 掩蓋。
- source registry 與中央 receipt／路徑契約互相矛盾：停止，交回來源權威決策。
- 發現目標檔案被其他 Agent 同時修改、未授權檔案內容衝突或 Git worktree 狀態異常：停止並先保留現況。
- 任何步驟要求刪除、搬移、重命名、stage、commit、push、完整刷新、模型訓練或外部傳送：停止並取得額外授權。
- 不得以退出碼 0 單獨判定正式報告完成。

## 回復方式

- 本階段沒有自動回復或日期化備份。
- 如需回復，只能由獲授權 Agent 依本次 `docs\HANDOFF.md` 精確 diff 做 reviewed reverse patch；不得使用 `git reset --hard`、`git clean` 或廣泛刪除。
- 回復前須重新確認其他未提交及未追蹤內容仍保持原狀。

## Fact／Inference／Not verified 標記規則

- `Fact`：由本文件列出的檔案、命令輸出或既有 manifest 直接支持。
- `Inference`：依多個證據推導的工作判斷，必須保留來源路徑，不能當作驗證通過。
- `Not verified`：本階段未執行、成品缺失或無法由現有證據確認的事項；不得在對外回覆中省略。
