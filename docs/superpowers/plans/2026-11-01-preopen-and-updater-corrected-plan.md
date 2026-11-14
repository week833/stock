# Preopen 與 Updater 整合更正 Implementation Plan

> **本輪執行結果（2026-11-01）**：[HANDOFF與實物限制](../../../../TWStockUpdater/artifacts/delta-performance-20261101/integration/HANDOFF.md)。使用者後續「執行計劃」授權已取代當時文件-only階段。正常run e84實際exit10／25available／真current pointer＋ACK；含中斷續接wall2h26m12.32，非末段66m37速度benchmark。
> 後補7檔已compile／部署與唯一還原check、兩cache重解PASS；只證該範圍，不宣稱整輪加速。Preopen 20既有selectors→23PASS；未做正式settle／DB遷移、訓練、外傳或Bot重啟；reader forward-summary v1/v2僅source審，無直接既有節點。
> 原字面零重複merge未全達：FX／公債0.4919453s是必要source/scope確認；Gold跨段同bytes重取已證且由後補Yahoo空chart修正。原exit10／UNKNOWN、coverage false保留；759官方缺碼不當confirmed missing。未達原文字面或未執行項的checkbox保留。

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking. 此句記錄當時文件規劃階段；執行方式固定為主代理 `gpt-6-astra`／`ultra` 規劃、委派、整合及唯讀驗收，真正載入的 `low_worker`／`gpt-6.1-sol`／`ultra` 執行，不能替換 executor。

**Goal:** 優先消除 Updater 無意義的歷史重做與重讀，並讓可讀舊 H1 繼續分析／發布、以一致繁中註記揭露日期與日曆限制，保持完整性及真實單一 run 計分。

**Architecture:** 同一份計畫分 Updater、preopen 兩條工作線。Updater 擴充現有快路、可信 witness 與精確依賴失效，不建立第二套 scope／多層 cache；preopen 在既有檔修正日曆信任、run 選擇、reader 與三 consumer。暫存實作可依不同檔並行，正式 writer 與部署依序。

**Tech Stack:** 現有 Windows PowerShell／CMD、Python／SQLite／pandas、既有 Updater runtime 與 D-stock venv；不安裝、升級或改依賴。

**Spec:** [已更正交接主文](../../CODEX_REVISED_20261101_PREOPEN_NEVER_BLOCK_STAGE_HANDOFF.md)。原交接附錄 A～G 僅歷史來源逐字證據，維持原檔唯讀參照，不複製或替並行新增內容背書，不是執行指令；與本計畫衝突時以本計畫及使用者裁示為準。

## Global Constraints

- 原文件階段狀態（歷史）：當時僅修訂文件。現已完成本輪授權執行，詳頂端結果與HANDOFF；checkbox按已證範圍更新，未達成／未執行者保留。
- 「有可用資料就分析／發布並繁中註記」，不新增最低新鮮度門檻；缺檔／真損毀、內容 claim／hash、ETF、必要一致性與輸出 I/O 仍如實失敗，不偽報成功。
- Updater 優先 source 修改／限定 clean code／一次集中最小既有檢查，再只啟動一次 normal；normal 執行時 D-stock／MIX 正式 producer、consumer 及 contracts 不改，preopen 僅暫存準備。
- normal 結束取得實物結果後，才處理另行授權的 preopen 正式部署與必要同輪 settle；不藉此另開 Updater、重訓、回補美股、重算歷史或外部發送。
- source、scope、coverage、allocation、歷史引用、Order21 外部索引權威、鎖內 current HEAD 與 CAS 全保留；PARTIAL／unknown 不轉完整或 NoData，未完成 HTTP 不當完成。
- retained floor=`2021-10-01`；training 1008 視窗=`2022-08-31`～`2026-10-30` 為上輪已驗基準；實際執行按當輪已驗日曆推進終日與1008需求、維持既有必要歷史，新股不強造更早歷史；下界刪除 manual cleanup only，不隨日常 scope migration 刪除。
- 確有全來源無資料證據者保留註記並停止重複追補；官方缺代號／unknown／PARTIAL 不等於全來源無資料。日常只處理新 session 與有新變更證據的缺口，不因歷史 unknown 重抓全歷史。
- 不新增測試檔／案例，不反覆 TDD、不跑全套／benchmark／安全掃描，不 commit／push、不啟用 auto-review、不拆新 module／改名或無關重構。
- 不改 H63 另一軌、模型配方、排程／任務／服務、正式 venv／依賴版本及系統環境；不刪搬其他 `D:\stock` 內容，不覆寫既有 dirty 變更。
- 每個 worker 明列單一目標、必要輸入、授權路徑、禁止範圍、輸出與驗收；不單獨作業、不回復他人變更。同檔單一 owner，必要共同函式由該檔 owner 合入。
- 文件保留實測 UTF-8 無 BOM／LF；程式實作另逐檔保留既有 encoding／BOM／換行。before／diff／manifest 依既有慣例，不複製資料庫或全樹來做文件驗收。
- 後續總時估計 2～3 小時＝修碼約 60～90 分鐘＋一次正常增量／必要結果約 60～90 分鐘；不保證 N 分鐘或百分比節省。

## Review Focus

- scope 日期移動但 route 有效保留內容未變：走現有 metadata rebinder，原 body／coverage／source-scope 不動；真正 route／集合／軸／來源 claim 改變才局部失效。
- 跨 run 恢復與同輪重用：不得以 mtime／stat 跨 run 宣稱驗過 bytes；精確 witness／依賴 token 不符才回原檢查，PARTIAL 與未完 HTTP 不抹去。
- 日曆 claim 已否決同檔、過舊 proof 或無未來 session：不得 fallback 洗白／短路 signal 判定；無可信日曆照發註記、settle pending。
- 同 signal 有多 run、Top10 缺列／pending：選一個合格 run 後保留原 rank，不能拼接、重排或以其他 run 補滿，摘要不預告命中率。
- consumer 較舊可用資料／原 target 不一致與真正損毀：前者沿用原 H1 並揭露，後者依 auto／preopen 契約處理；晚於 analysis cutoff 的真未來資料／PIT 衝突不放行，沿用值不得冒充當日新前瞻樣本。

## Files 與責任邊界

- Updater 正式 source 根：`D:\TWStockUpdater\src\twstock_updater\`。修改候選：`runner.py`、`retention_migration.py`、`current_scope_runtime.py`、`incremental_recovery_index.py`、`targeted_route_retirement.py`、`shared_publication.py`、`run_finalize.py`、`stock_shared_store.py`；每檔限下述函式鏈。
- Preopen 現有候選根：`D:\TWStockData\program_data\stock_daily_c0\candidates\h1_ops_review_20261031\deploy\stage\`；合併 reader 在同層 `merged_reader\`。先在候選中修既有檔及 consumer 對應鏡像，不在 normal 執行期間修改正式檔或 contracts。
- Preopen 正式部署目標：`D:\stock\programs\shared\stock_daily_preopen.py`、`stock_daily_preopen_reader.py`、`RUN_PREOPEN_DAILY.cmd`；`D:\stock\TWNextDayTop10\preopen_source.py`／`reporting.py`；`D:\stock\programs\single_stock_analysis\src\single_stock_preopen.py`／`stock_candidate_report.py`；`D:\stock\programs\telegram_report_delivery\telegram_stock_command_bot.py`；`D:\MIX All\src\mixstock\preopen.py`／`core_daily.py`。renderer 僅改共用註記接線所需處。
- 既有測試候選與執行邊界列 Task 5／8。正式部署的檔案取本次修正後差異與指紋，不能直接複製舊 v3.1／merged 成品；不套 H63 另一軌。

### Task 1：U1 scope 與既有保留內容分開（Updater 優先）

**Files:** Modify `runner.py`、`retention_migration.py`、`current_scope_runtime.py`、`targeted_route_retirement.py` 的下列既有函式；不新建第二個 migration 系統。

**Interfaces:**
- Consumes：同一份已驗 scope／calendar／universe／demand、原 source-scope／body／coverage、真 route／股票市場集合／有效 session 軸／來源 claim 差異。
- Produces：route 有效範圍差異與現有可信 rebinder／body-writer 決策；只有真正改變的 route／partition 失效，新 acquisition metadata 可綁原 retained body。

- [x] 重用 `runner._typed_migration`（約 2528）與 no-op（2573–2584）；擴充 `retention_migration._prepare_scope_migration`（1286–1337 已有 metadata rebinder 不讀 history，1338 才 body writer），不只放寬 scope_id。
- [x] 沿用 `retention_row_predicate_projection`（1000）／`_policy2_row_filter_semantics_equal_validated`（1005），分辨 acquisition 日期／範圍變動與 retained 有效內容差異；未變保留原 body／coverage／source-scope，有新交易日只增量處理新增日／變更分區。
- [x] `current_scope_runtime._resolve_current_scope`（1768）沿用 `_ScopePreparationMemo`（115）與 membership（891）；global axes（623）／key（637）共用完整已驗軸，再按 cutoff 裁切，fresh clock／lease／取得 unit 仍合法更新。
- [x] `targeted_route_retirement._load_history`（406）／`audit_content_claims`（590）已有 memo，保留；`_current_routes`（336）整 HEAD key（343）改為按精確 bindings／routes／seals 重用純投影，current HEAD 權威照驗。
- [x] 日常遷移不刪 floor／1008 所需歷史，不因 asof／整 HEAD 變動讓全部歷史失效；`runner._incremental_binding`（2824）本來未直接綁 asof，不能把「移除 asof」寫成完成修法。

### Task 2：U2 獨立 route 恢復與依賴閉包

**Files:** Modify `runner.py::_incremental_recover`、`incremental_recovery_index.py::RecoveryIndex`；與 Task 1 的 `runner.py` 同 owner 依序合入。

**Interfaces:**
- Consumes：Task 1 真 route 差異、已驗 immutable 引用／來源結果、progress 指向、byte claim／route 依賴。
- Produces：route／依賴閉包的命中或有固定原因的 fallback；成功來源結果可沿用，不把未完請求標完成。

- [x] 改 `_incremental_recover`（3499、3622 closed）與 `RecoveryIndex._route_digest`（56）／`closed`（94），由整 run all-or-nothing 改為獨立 route／依賴閉包命中。
- [x] 只因 progress 指向／bytes claim／依賴變動重建該 route 及反向依賴；沒有證據才回原必要檢查，不為單一缺口整掃 40 run。
- [x] 跨 run 以可信不可變內容與 claims／來源依賴證據判定，不能只靠 mtime／stat；保留 PARTIAL／unknown／失敗 retry 狀態與缺口 denominator。
- [x] 沿用可用索引／原 fallback；未完成 HTTP、來源缺代號或 unknown 不轉 NoData，不因歷史 unknown 觸發全歷史 download。

### Task 3：U3／U4 單輪 witness、canonical 與 completion 共用

**Files:** Modify `shared_publication.py`、`run_finalize.py`、`stock_shared_store.py` 的指定函式鏈；不新增版本綁定／全量 cache 副本。

**Interfaces:**
- Consumes：已有真 witness、不可變 HEAD bytes／route→entry／digest，以及精確 result／receipt／payload／entry／scope／calendar／coverage／來源依賴 token。
- Produces：同一 buffer 的驗證結果／parsed canonical／receipt／claims／layout，及可精確失效的 route completion view；鎖內 HEAD、snapshot checks、CAS 不變。

- [x] 將 `shared_publication.read_baseline`（384–438）／`run_finalize._completion_head` 已有 witness 傳入 publisher／migration；移除 `stock_shared_store.publish`（8744–8747）hash 後再 parse、migration（10389）再 read_head 的相同內容重讀。
- [x] 在 owned immutable HEAD 建一次 route→entry／digest 索引，只新 entry 重算；`_route_ack`（1309）、`_verified_industry_reference`（1394／1401）、shared_publication CAS retry（454／511）共用，仍在鎖內核對 current HEAD 與 CAS。
- [x] `run_finalize._verified_route_pair`（2441–2447）首次驗證同 buffer 產 parsed canonical／receipt／claims／layout；`_selected_canonical_path`（2488）與 loader 共用，不再 verify 後 read／hash／parse 同檔。
- [x] closure 邊去重＋反向 route 索引只抽取一次；changed claim 及依賴 route 局部重建，寫入邊界原 snapshot checks 與 Order21 外部索引權威保留。
- [x] `collect_available_data`／`evaluate_completion` 共用已驗 `_current_completion_view`；精確 token 不變沿用，scope 真影響 assessment 才重算，不把先前 unavailable 硬改 available。

### Task 4：U5／U6 capacity 與限定 clean code

**Files:** Modify Task 1～3 同一檔案／函式範圍，重點 `stock_shared_store.py::_validate_current_capacity` 及既有 merge／索引呼叫點；不順手重構其他模組。

**Interfaces:**
- Consumes：Task 3 已驗 immutable entry／claims／反向 route 索引、既有 capacity memo、真新增／訂正 partition。
- Produces：角色敏感薄引用邊的局部更新＋完整最終全域驗證；既有 metrics 可區分合法重驗與無意義重讀。

- [x] 保留 `_validate_current_capacity` 既有 memo，不把它宣稱為新功能；immutable entry 建角色敏感薄引用邊，只更新 changed route／claim 及反向依賴，再合併既有邊。
- [x] 保留最終全域 claim 一致性、仍被引用歷史、coverage／allocation／CAS；不能為速度略過，或拿 scope 比較結果代替內容驗證。
- [ ] 未改 body 不完整 merge／copy，不重建相同 price 日期索引或全歷史 NEWS；真新增／內容訂正僅合併受影響 partition，已完成相同 unit 不重抓。
- [x] 只移除上述鏈重複 wrapper／不可達 memo／重複 canonical 化，合併為一個既有共用函式／索引；不拆新 module，不堆多層 cache。
- [x] 沿用 `head_document_reads/reuses`、`canonical_document_reads/reuses`、`receipt_*`、`available_* reuses/fallbacks`、`completion_evaluations`、migration replay／commit-capacity 指標；必要時只加固定 reason-count，不加掃描／benchmark／大 log，不承諾未量到的速度。

### Task 5：Updater 一次集中最小檢查與唯一 normal

**Files:** Existing checks only：`D:\TWStockUpdater\tests\test_resume_metadata_reuse.py`、`test_lifecycle_scope_validation_cache.py`、`test_partial_window_scope_reuse.py`、`test_runner_incremental_publication.py`；不是執行整 file／suite。

**Interfaces:**
- Consumes：Task 1～4 全部 source／clean code 差異、既有 prepare／receipt／cache 與 immutable 結果。
- Produces：一次集中相關既有檢查結果、唯一 normal 的 run ID／全時長／25 availability／current pointer／ACK／來源限制與 metrics；真錯誤續接仍屬同 run。

- [ ] 全部 Updater 必要修改完成後，集中一次執行適用既有節點：`test_keep_completed_metadata_mode_skips_payload_hydration_and_preserves_status`；`test_same_saved_scope_is_fully_validated_once`；`test_multiple_claims_prepare_scope_once_and_reuse_genuine_handle`；同檔 `test_no_replacements_do_not_prepare_scope`；`test_order21_resume_is_queued_and_final_flush_uses_unacknowledged_partitions`。失敗只修相關處、重驗受影響部分；不新增測試。
- [x] 通過後只啟動一次正常增量；cwd=`D:\TWStockUpdater`，唯一 CLI 如下。若背景執行，沿用既有 hidden supervisor，不另建一套 runner。

```powershell
& 'D:\TWStockUpdater\runtime\.venv\Scripts\python.exe' -B -m twstock_updater update --mode incremental --headless --workers 8
```

- [x] 僅 child env 設 `PYTHONPATH=D:\TWStockUpdater\src`、`TWSTOCK_UPDATER_ROOT=D:\TWStockData\updater_runtime`、`PYTHONUTF8=1`、`PYTHONIOENCODING=utf-8`、`PYTHONDONTWRITEBYTECODE=1`；僅清 child 的 `TWSTOCKUPDATER_ROOT`／`TWSTOCK_ROOT`，不帶上次專用 `TWSTOCK_UPDATER_PUBLICATION_ONLY_RESUME`；不改系統 env。
- [x] 不帶 force／日期覆寫／routes／cleanup。保留 receipt／prepare，真錯誤以已存同 run 續接並揭露含失敗全時長，不另啟 normal 假作 benchmark。此期間 D-stock／MIX 正式檔及 contracts 不變。
- [ ] 以真實 25 availability、current pointer／ACK 及來源證據確認；有據 available-current switched／exit 10 可用完成，不追 exit 0。主輪真 source change 可 HTTP>0，新增／失敗 retry 分列；相同已成功 unit 不重抓，未改內容重複 merge=0，不阻止真訂正分區 merge。
- [ ] 可驗效果：相同資料／依賴無變動不重建／重算，真變更僅局部；逐項保留合法重驗原因。可用性／來源完整性分列，759 日常鍵與 253 視窗 1656 等既有缺口不是相加，不在本次另查。

### Task 6：Preopen 可信日曆與單一 run 摘要（只在候選準備）

**Files:** Modify 既有候選 `stock_daily_preopen.py`；對應正式檔在 Task 8 才部署。保留 rowid 修正與現有 schema，不採 H63／40% 研究替代法。

**Interfaces:**
- Consumes：`load_verified_calendar(path=None, routes=None, central=None)`、`calendar_gate(...)`、`settle_rows(con, calendar, lookups, *, resettle=False, source_note='')`、`forward_summary(con, root=None, calendar=None)` 現有介面與可信不可變指紋／v2 proof。
- Produces：`calendar_stale`／獨立 signal 狀態、不可洗白的可信日曆結果；v2 原鍵＋`selection_unit='run'` 的單一合格 run 摘要，v1 reader 相容。

- [x] SHA claim 否決同檔不得掃描重接受；備援只用可信內容指紋且通過 v2 proof 的其他檔，單輪同檔只 read／parse 一次，不重抓日曆／全歷史；無可信日曆 predict 註記照發，settle pending。
- [x] 加 `calendar_stale`；已驗範圍內 `signal_not_session` 獨立檢查，不能被 stale elif／未來 target 短路吞掉。`signal_gap_unverified` 用「是否計分以結算時已驗證日曆為準」。strict-calendar 只手動 QA。
- [x] 將 settle_rows 已有 source_note 中實際 HEAD 來源註記保存至 settlement_source，保留原價格來源標記；只接通既有參數，不額外讀 DB／來源。
- [x] 保留 rowid 更新；void／multi-session／legacy 不進 H1／calibration。每 signal_date 在有 `SCORABLE_SQL` 列的合格 run 中依 `recorded_at + run_id` 穩定選 first／last，再取該 run 有效列。
- [x] 保留原 rank／Top10 身分，不重排名、不跨 run 補缺列；每日最多一份真 Top10，missing／pending 數保留。保留 `all`／`last_publication`／`first_scorable_publication`，新增 run 選擇單位與「最後／最早可計分發布」文字，不預告新命中率。

### Task 7：Reader 與三 consumer 日期沿用／同一中文註記

**Files:** Modify 既有候選 reader／CMD、merged reader 必要共同差異；Top10／單股／MIX／Telegram 對應鏡像。正式目標見 Files 表；只取本案必要共用差異，不部署 H63 另一軌。

**Interfaces:**
- Consumes：Task 6 manifest／summary、可讀且完整的原 H1、原 signal／target 與當次 report date；reader `load_latest(root=None, expected_signal_date=None, verify_sha=True)` 保留完整性，consumer 不再把 freshness 當拒用理由。
- Produces：共用 `calendar_gate_text(gate, meta=None)`／`ledger_text(ledger)` 格式化的 `calendar_note_zh`／`ledger_note_zh`；payload／block／renderer／caption 沿用同文字，原值／原日期與 scores／ranking 不動。

- [x] reader／merged 的 view 常數、`load_forward_summary(root=None)` 讀取鍵及單股 label／既有 fixture 一起對齊 `first_scorable_publication`；v1 `all` 相容，不對錯鍵靜默退回其他口徑。
- [x] Top10 `choose(...)`／`confirm_target(...)`、單股 `build_h1_block(...)`／row 日期檢查、MIX 快照載入路徑（約 382）／`_validate`／`check_target` 的 auto／preopen 對較舊但可用 H1 及原 target 差異改為沿用可讀 H1＋原 signal／target、report date／差異註記；不改寫舊預測日期、不因 freshness 自動換模型。snapshot 資料／發布時間若晚於本次 analysis cutoff，屬真正未來資料／PIT 衝突，保留既有處理，不能把所有日期差一律放行。
- [x] 真正無可讀 H1 時 auto 原 fallback C0，強制 preopen 明確錯誤；保持 hash／欄位／股票市場／ETF／數值／rank 等必要一致性。日期不同的沿用值不能記為當天新 H1 prospective 樣本。
- [x] PDF／Top10／MIX／Telegram caption 統一消費 `calendar_note_zh`／`ledger_note_zh`；single_stock block 保存，bot `_preopen_caption_lines` 追加必要短註記，formatter 不在各端重算，describe 不重複貼長段。
- [x] CMD 保留既有 continue／predict exit、env root／log-dir／初始不可寫 console fallback；非零只說核對 latest／log，不聲稱 latest 一定沒更新，不啟用每日 calibrate／strict。

### Task 8：Preopen 一次既有檢查、依序部署與必要同輪結果

**Files:** Existing check candidates：候選 `tests\test_stock_daily_preopen.py`／`test_preopen_stage_ops.py`；`D:\stock\TWNextDayTop10\tests\test_preopen_model_source.py`／`test_preopen_read_integrity.py`；`D:\stock\programs\single_stock_analysis\tests\test_single_stock_preopen.py`；`D:\stock\programs\telegram_report_delivery\tests\test_telegram_preopen_h1_caption.py`；`D:\MIX All\tests\test_h1_preopen.py`。不新增案例／函數／檔案或跑完整 file／suite。

**Interfaces:**
- Consumes：Task 6～7 全部候選修改、Task 5 normal 真實結果、保留的現行正式 before／hash。
- Produces：一次集中既有受影響檢查結果；另獲授權後的單 writer 正式部署及必要同輪 settle／summary／報告註記結果，不重算歷史。

- [x] 更正既有 expected／calendar／日期／summary fixture；既有 reason-map 相等斷言改為正式既有鍵值是 merged 子集，必要 columns 保持完全一致。不要以部署後自我比對跳過取代修正。
- [x] 全部 preopen 候選修改後，集中一次執行既有受影響節點（含 reader 日曆／summary、CMD flow、三端 model source 與 caption），不跑全套／長測／parity／benchmark；失敗只修相關處並重驗受影響部分。候選可先準備，normal 期間不得改正式 producer／consumer／contracts。
- [x] Task 5 normal 結束且實物結果齊備後，才依另行授權先保存精確 before／hash、單 writer 部署必要差異；不得直接照附錄複製原 v3.1、套單股整軌 diff 或更動服務／排程。既有測試新檔不因本案複製到正式樹。
- [ ] 若正式帳冊寫入另已獲授權，只做必要同輪 settle／summary，核對 rowid／run 選擇、分類／pending／缺列及四種報告表面的同一註記；不 `--resettle` 重算全歷史、不訓練、不另開 Updater、不外部傳送。所需服務重啟不在本案自動執行。
- [x] 分別回報 source／候選檢查、正式部署、帳冊／成品與 consumer 可見註記的真實證據；只完成文件或候選時不宣稱 runtime 完成，不使用新命中率暗示模型提升。

## 證據與成功判準

- 本次文件 before／diff／manifest：`D:\TWStockUpdater\artifacts\planning-review-20261101\`；只驗自有兩檔編碼／Markdown／本機連結／授權差異；原交接保留原文並記錄並行變更，不宣稱最後附錄等同早期 before，**不執行本計畫 CLI 或 checks**。
- 最新已核結果 run=`20261031T204521Z-1bde8339`，11-01 08:48 完成、25 available／exit 10；結果：`D:\TWStockUpdater\artifacts\delta-incremental-20261101\integration\local-finish-reference-fix\execution\final-result-thin.json`。
- 原 normal 性能薄檔：`D:\TWStockUpdater\artifacts\delta-incremental-20261101\integration\industry-continuation\execution\final-performance-thin.v2.json`；不重讀正式 summary／work 或另跑 benchmark。
- 歷史正常主輪 67m07.71 但 exit 27；純本地續接 21m59.73（prepare 784.594s、availability 291.891s、completion 170.344s、local generation 31.672s；collector／merge／shared publication=0、ACK 未變）；含失敗總時長 2h38m42.5，不當每日預測時長。
- 舊成功基線 78m01；正常前置 37m22（migration 22m29、recovery 4m14）；closed index 0 hit／40 fallback 尚無逐筆全原因，不能宣稱已完全消除或承諾特定加速。
- Preopen stage 64 passed／1 skipped 僅先前假資料；merged 實際整套 67 passed／2 failed／1 skipped。短路徑單項通過／部署自比跳過不能湊全綠；新修改必須有本輪真檢查證據。
- 35%=6 日21/60 是帳冊統計校正，不是模型改善；保留 v2 合法鍵與 v1 相容，不採約40%研究替代口徑、不預告新命中率。
- 最終判定：必要 source 修改與限定 clean code、一次集中既有檢查、唯一 normal 及其真實 receipts／25 availability／pointer／ACK、之後獲授權 preopen 部署與必要結果，逐層分列；不把可用性當來源完整性、也不把 blocked 整體 acceptance 說成通過。
