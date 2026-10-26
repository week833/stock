# updater 調查的對抗式驗證與統一實作計畫（verify，2026-10-05）

範圍：驗證 `hang_gap`（07:51 卡住、09-30 缺口）與 `evidence`（U2／D3／D10）兩份報告的根因與設計，檢查資料遺失、消費端破壞、封存可驗證性與遷移風險，整理成一份有順序的實作計畫（含與個股部署的先後），只列出使用者必須決定的問題。

## 0. 安全與方法

- D: 全程唯讀。沒有執行 updater、沒有寫入 D:、沒有刪除任何檔案。
- 所有腳本都掛 `scripts/dguard.py`（D: 寫入攔截），攔截 0 次。只讀了 D: 上的原始碼、head、封存檔、run 目錄的 receipts／canonical 與檔案時間。
- 程式只在副本上跑：`hang_gap/asrun_src`、`hang_gap/current_src`（與 live 位元組相同，已核 sha）、`evidence/data/run_ad5ea647`（已核 sha 與 D: 原檔相同）、`three_month_impl/_trash/hang_gap_quiet_root`（hang_gap 的重播輸出）、pd_final 合併樹副本。
- 安靜暫存根 `C:/Users/USER/claude_quiet_verify`（只放了 os.replace 測試用的兩個小檔），結束時移到 `three_month_impl/_trash/claude_quiet_verify_<時間>`。
- 腳本 `verify/scripts/`，輸出 `verify/out/`。

---

## 1. 結論

### 1.1 原有根因：全部成立（各自獨立重現）

| 項目 | 判定 | 我自己的證據 |
|---|---|---|
| HG1 二次方查詢 | 成立 | 從 as-run 原始碼文字抽出 SQL 與 DDL，在記憶體 DB 重跑：3k／6k／12k 列 = 0.25／1.00／4.27 秒（每倍增 ×4），計畫 `SCAN a USING COVERING INDEX sqlite_autoindex_rows_1`；current（含 rows_key）= 0.007／0.015／0.032 秒，`SEARCH a USING INDEX rows_key`（`out/v_hg1_query.txt`）。as-run 檔與 15:45 備份 sha 相同（`0c2d32c0…`），沒有 ANALYZE，所以不可能走 skip-scan |
| HG1 時間軸 | 成立 | `tmpvg8e7df6`：journal 08:05:51、merged.json 08:06:13、rows.sqlite 08:07:30；`_metadata`、`_manifests`、`_metadata/coverage`、`prepared_candidates`、`order21_resume_pins`、`updater_runtime/state|control|_metadata|logs`、run 的 receipts／payloads、`_objects` 頂層在 08:07:31 之後到現在**零寫入**；head 仍是 `efbd4844` |
| HG1 04:49 run 也卡 | 成立 | cash_flows merged 物件 `2a338066…` 05:22:26 → head `1b2f1e68` 05:30:54（8.5 分），86,542 列，與二次方模型同量級 |
| HG2 一個慢合併凍住全部 | 成立 | as-run `runner.py` 4875-4908：`if ready: publisher.drain()`；mutex 名稱固定（L41）；`read_head` 在鎖內讀 head 並算雜湊，3 次 × 50 ms |
| HG3 沒有進度紀錄 | 成立 | `menu.ps1:125` 只 `| Out-Host` |
| HG4 靜默沿用 | 成立 | `run_io._matching_incomplete_run`；updater 沒有任何「程序層級」的單例鎖（只有發布 mutex 與 ImmutableUpdateCore mutex） |
| HG5 舊候選重複合併 | **機制成立**（原報告標為未驗證） | 見 V4 |
| HG6 一個 route 層級警告擋住逐單位解除 | 成立 | 程式碼 + 用 pd_final 消費端的 `check_route` 實測（V1） |
| HG7 舊格式列無法替換 | 成立，但 G2 有資料遺失問題 | 見 V2 |
| HG8 收盤前抓當天 | 成立 | `calendar.py` mtime 09-30 18:21；`collect_market_daily.py` 只標 `no_data_reason`，狀態仍是 EMPTY／FAILED |
| U2 證據截斷 | 成立，位元組相同 | 用 live `_bounded_evidence`（sha `79b3284b`）處理 run ad5ea647 的完整值：現金流 receipts 7,972,609 B → 6,104,912 B，產生 1,765 個 null；diagnostics 9,223,616 B → 484,319 B；財報期限 receipts 268,816 B → 222,050 B；三者都與 head 位元組相同，last_attempt 也相同（`out/v_u2_repro.txt`） |
| D3 重複 | 成立 | 15,032,316 B（caps_dup/verify `head_probe.txt` 已獨立量過，與 evidence 報告一致） |
| D10 封存重複 | 成立 | `_manifests` 379 份 709,536,515 B；>1 MB 的 44 份 664,578,608 B |
| R1 16 MiB 上限 | 成立，**很可能會在下一次跨日發布財報類 route 時觸發**（見 V11） | |
| R2 housekeeping 全部解析並留在記憶體 | 成立 | `plan_object_prune` 把每份封存 `json.loads` 後放進 `documents` |

### 1.2 新發現（會改變計畫的）

1. **V1（blocker）個股部署不能在 efbd4844 上訓練三個月模型。** 訓練的三個月 universe panel 會在 `T = 最新價格日（2026-10-02）` 呼叫 `route_checks`，official_market_daily 是 PARTIAL 且缺 09-30 → `xs_route_partial_gap_not_after_d` → `candidate_ready=false` → A/B 比較的 A_main 會以 exit 4 結束。DEPLOY_PLAN v5 的 P5 只寫「smoke 才需要補缺口」，這是錯的。**updater 必須先發布 omd 無缺口的 head，個股部署再 pin 那個 head。**
2. **V2（high）G2 原型會悄悄改值，而且它的 sidecar 會被 housekeeping 刪掉。** 5,108 列中有 345 列（全是興櫃 09-09）的 `open` 被換成不同的值；sidecar 放在 `_objects/`，不在 housekeeping 的保留集合裡。
3. **V3（high）「不改程式、只跑一次增量」並不能保證清掉 09-30。** 重播用的是手做的 09-21 之後候選，但真實 run 會先 harvest 已保存的全窗候選，再依 coverage 決定要不要重抓。
4. **V4～V12**：resume 會讓每條 route 合併兩次（omd 兩個候選是同一個 1.29 GB payload）；Windows 上有讀者開著 head 時換檔會失敗；H4／H5 原設計各有一個會造成資料錯亂的副作用；v2 的寫入開關是每個程序自己的環境變數；封存檔的資料物件其實早就被刪了；updater 測試基準本身是壞的；R1 很可能是活的；R2 在每次 run 的關鍵路徑上。

---

## 2. 新發現的細節

### V1 個股 A/B 訓練在 efbd4844 上一定失敗（三個月模式）

- pd_final 的 `tw_single_stock_train_shared.py`：`asof = context["expected_asof"]`（L1347），而 `_pin_published_context` 把它設成批次 prewarm 的最新價格日（native_calibration L637-668），efbd4844 是 2026-10-02。
- `prepare_training_panel(context, asof)` 第一步就是 `route_checks(refs, problems, T)`（xs_io L1485）→ `check_route(official_market_daily)`：狀態 PARTIAL，缺口日期 09-30 ≤ T → `XsUnavailable("xs_route_partial_gap_not_after_d:official_market_daily")`。
- 失敗會變成 `three_month_universe_panel_failed` → 三個月 evaluation 失敗 → `publication_skipped_until_all_requested_modes_are_ready` → `candidate_ready=false`。`pdc_common.check_run` 要求 A_main 兩個模式 `candidate_ready=True`，否則 `pdc_deploy_compare` exit 4。
- 實測（`out/v_xs_check_route.txt`，用 pd_final 的 `check_route` 原樣判定）：

  | head | omd 狀態 | D=09-29 | D=10-02 |
  |---|---|---|---|
  | efbd4844（現況） | PARTIAL | 接受 | **拒絕** not_after_d 09-30 |
  | 重播：增量窗 | SUCCESS | 接受 | 接受 |
  | 重播：全窗（current） | PARTIAL | 接受 | 拒絕 not_after_d 09-30 |
  | 重播：增量修好後再全窗 | PARTIAL、gaps [] | **拒絕** gap_unknown | 拒絕 gap_unknown |
  | 重播：全窗 + G1/G2 | SUCCESS | 接受 | 接受 |

- 結論：個股部署必須在 omd 修好之後的 head 上 pin。另外「增量修好後又跑全窗」會讓**所有** D 都失敗（不只 09-30 之後），所以第 2 波之前不能有任何全窗發布。

### V2 G2 原型的資料遺失

用 hang_gap 重播留下的副本逐列比對（`scripts/v_g2_value_diff*.py`、`out/v_g2_value_diff_bymarket.txt`）：舊物件 `6265b799`（head 現行）對 G2 產生的 `f1283a5f`。

- 列數不變 3,036,629；09-09、09-11 以外的日期，逐日的列多重集合完全相同。
- 5,108 列被替換：amount 與 transactions 由無變有 2,726 列、volume 由無變有 9 列。
- **345 列的核心值被改掉：全部是興櫃 2026-09-09 的 `open`。** 例：7930 舊 314.12 → 新 282.14；7759 舊 30.12 → 新 29.97（`scripts/v_g2_open_detail.py`）。
  - 原因：興櫃沒有開盤價，`open` 是代理值。現行 collector 用「前一日均價」（`open_basis=previous_average`、`open_is_proxy=true`），舊 producer `market_tpex_emerging_daily` 在 09-09 存的是當日均價（等於 09-10 列的 previous_average），也沒有 `open_basis` 標記。
  - 新值和其他每一天的口徑一致，所以替換本身是對的；但原型**沒有記錄舊值**：`_market_daily_legacy_split` 只檢查「核心值不可由有變無」，不檢查值是否相同，舊值不進 sidecar。
- 別名欄位（code、session、ClosingPrice、Code、Date、Name…）全部等於該列的正規化值，丟掉不算遺失；provider 獨有欄位 2,391 列完整進了 sidecar，值逐一相同。
- **sidecar 會被刪掉**：
  - G2 用 `_object()` 寫進 `_objects/929b869b….json`，只在 `lineage['legacy_row_replacement']` 裡有參照。
  - `store_housekeeping.plan_object_prune` 的保留集合只有：被保留的 head 版本的 entry `path`（`_version`）、head entry 的 `candidate_object_sha256`／`previous_object_sha256`、resume／MIX pin、pending recovery、coverage sidecar。**metadata 裡的其他路徑不算**。
  - 所以 sidecar 在建立滿 1 小時後的第一次 housekeeping 就會被刪（同一次 run 結束時的 housekeeping 就可能刪掉，因為 run 通常跑好幾小時）。
  - 下一次 omd 發布的 lineage 也不會帶這個參照；再下一代，被替換前的物件 `6265b799` 也會被刪，舊值從此不存在。

### V3 不改程式、只跑一次增量，未必能清掉 09-30

- hang_gap 的增量重播是把 ad5ea647 候選中 ≥ 09-21 的列與 receipts 手動組成候選，直接呼叫 `publish`；沒有經過 harvest 與重抓規劃。
- 真實的增量 run 對每條 route 都會先跑 `recover_route_publication` → harvest。ad5ea647 的 omd canonical 沒有 `native_unit_proofs`，`_recovery_candidate_metadata` 會從 3,642 筆 receipts 推出 legacy proof，只要和增量需求有交集就選進來；`load_publication_recovery_candidate` 用的是**整個 payload**（`records_path`），store 端沒有依合格單位裁切列。所以會先做一次 1.29 GB 的全窗合併，碰到舊格式列 → 警告 → 仍是 PARTIAL。
- 這次發布會寫出 omd 的第一個 coverage sidecar（目前的 omd entry 沒有 `coverage_ref`，是 legacy entry）。之後的重抓規劃：
  - legacy entry 走 `_legacy_adoption_safe`，任何帶日期的 gap 都會讓該單位「不可重用」→ 重抓。
  - sidecar entry 走 `PublishedCoverage.proofs_for`，只有 `revoked／revision_required／requires_fetch／unresolved_conflict` 這類**有旗標**的 gap 才擋。09-30 的 gap 是 `{date, market, status: FAILED}`，沒有旗標 → 09-30 會被當成已覆蓋。
- 結果可能是 09-30 永遠不再被重抓，缺口卡住。這一點我沒有在真實 run 驗證（不能跑 updater），但兩條程式路徑都是讀過的。
- 修正 G4（新）：同一個 entry 宣告為 gap 的單位，不得在 coverage 中被採為完整，重抓規劃也必須把它當成需要重抓。

### V4 resume（選單 R）每條 route 會合併兩次

- `recover_route_publication` 先用 producer-owned 路徑選 ad5ea647 自己的候選，再呼叫 `_harvest_publication_recovery(..., active_run_ids=[run_abs.name])`。
- harvest 會跳過 active run，所以它的 proof 去重集合不含 owned 候選的 proof；424b9768 的同一批單位會再被選中。
- `handle_publication` 對每個 `publisher_only_recovery` 都直接 `publish_common`，中間沒有重新檢查。
- `publication_recovery_pending.json` 顯示：omd 在 424b9768 與 ad5ea647 的候選 payload 是**同一個** sha `c864cf08…`（1,293,680,664 B），會被完整合併兩次。
- 新開的 run（選單 2）不會這樣：harvest 依新到舊排序，同一個單位只取最新 run。
- 修正 H7：harvest 前先把 owned 候選的 proof identity 放進去重集合；payload sha 相同的舊候選直接退休。

### V5 Windows 上有讀者開著 head 時，換 head 會失敗

- `scripts/v_replace_sharing.py`：另一個 handle 用 Python 預設方式開著目標檔時，`os.replace` 回 `PermissionError [WinError 5]`；關掉之後才成功（`out/v_replace_sharing.txt`）。
- `stock_shared_store.publish()` 先 `_archive_manifest` 再 `os.replace(name, head)`，沒有重試。
- D:/stock 的消費端（`stock_data_paths`、`stock_data_sources`、telegram bot…）讀 head 時都不拿 mutex。
- 後果：撞上時，這次發布失敗，還會留下一份「沒有成為 head」的封存檔。30 MB 的 head 讀一次要上百毫秒，所以窗口不小；v2（0.9 MB）會縮小約 35 倍。
- H5 提議讓 `read_head` 不拿鎖，會讓 updater 自己的讀者也加入碰撞。修正 R3：換檔遇到 WinError 5／32 時有界重試；讀者一律「一次讀完位元組→關檔→雜湊→解析」。

### V6 H5（合併移出 mutex）原設計會造成資料錯亂

- housekeeping 的兩個前提都是「合併期間一定持有發布鎖」：
  - `plan_temp_leftovers`：鎖在手時，`_objects/tmp*` 只要 10 分鐘沒寫入就當成孤兒刪掉。07:53 那次合併在長查詢中 6.6 小時沒有寫入。
  - `plan_object_prune`：未被參照且滿 1 小時的物件就刪。合併中建立的候選物件與合併結果在提交前都不被參照。
- 合併移到鎖外之後，另一個程序的 housekeeping 會刪掉正在使用中的 rows.sqlite 與候選物件。
- 把 mutex 改成「依 shared root 雜湊命名」也有問題：collect data tool 的 `app/upstream_idle.py` 與 `app/preflight.py` 靠探測這兩個全域名稱判斷上游是否閒置；改名會讓新舊程式互相看不到，失去互斥。
- 修正：要做 H5，必須同時加「進行中合併」登記（例如 `_metadata/inflight/<pid>-<start>.json` 列出 temp 目錄與已建物件），housekeeping 與 collect tool 的 prune 都要尊重它；mutex 保留全域名稱，只給測試／重播一個環境覆寫。

### V7 H4（拿掉每條 route 的 drain）原設計會重抓或衝突

- drain 之後緊接著是 `_formal_coverage_for_route(run_dirs, route_id)`，收集範圍是用「剛發布完的 coverage」決定的。
- 不 drain 就收集，會用舊 coverage 重抓已保存的資料；兩個候選還可能在 CAS 時互相覆蓋。
- 修正：還有恢復發布在進行中的 route，這一輪先跳過，排到後面再處理（或 drain 加逾時後延後該 route）；不要在它的恢復發布完成前收集它。

### V8 v2 寫入開關與讀取點的設計風險

- `evidence_store.head_format()` 讀**每個程序自己的**環境變數 `TWSTOCK_UPDATER_HEAD_FORMAT`。選單、手動指令、collect data tool 內的 updater 副本都可能發布，設定不一致時 head 格式會在 v1／v2 之間來回切換。v1 會把參照展開成完整值（51.8 MB）。`INLINE_MAX` 也一樣。
- 原型的「邏輯一律看完整形」是逐處包 `evidence_view`（L1770、3603、4028、5068 等）。漏一處就會讀到參照。
- 有一處漏掉時會 **fail-open**：`_resolve_prior_gaps` 的 official_market_daily 分支在 `source_receipts` 不是 list 時，不做收據比對就直接 `resolved.append(gap)`。
- 修正：
  - 格式由 store 決定：持久化的 `_metadata/head_format.json`，或「沿用目前 head 的 schema，除非執行明確的遷移指令」。
  - producer 讀 head／封存的入口一次轉成完整形，只在寫入點轉成儲存形；entry digest 明確用儲存形計算。
  - 收據不是 list 時一律不解除缺口。

### V9 封存檔只保證 head 位元組，不保證資料還在

- 抽樣 13 份封存（`out/v_archive_object_presence.txt`）：09-13～09-24 的 head 引用的 34～36 個資料物件**一個都不在**；09-29 的 17/36；10-04 之後才全在。
- 原因同 V2：housekeeping 只保留目前 head（`keep_versions=1`、`keep_hours=0`）、前一代物件與 pin。
- 影響：
  - efbd4844 的物件在下一次 omd 發布並經過一次 housekeeping（且沒有分析程式在跑）之後就會被刪。之後任何「重驗 efbd4844 資料」的步驟都會失敗。
  - 任何放在 `_objects/` 又不在保留規則裡的東西（G2 sidecar、未來若把證據放 `_objects`）都會消失。
- `_evidence/` 不在 housekeeping 與 collect tool prune 的掃描範圍（collect tool `store_prune` 只刪 `_objects`），適合放「必須跟著封存檔一起留下」的東西。

### V10 updater 的回歸測試基準本身是壞的

- hang_gap 跑的 19 個合併相關測試檔：current 與 patched 各 101 failed／314 passed。分類：
  - 25 個 `invalid_formal_route_entry`：測試 fixture 的 old entry 沒有 `route_id`，10-05 03:59 部署的 `_CoverageRows` → `load_coverage_reference` 要求它。這是 fixture 過時，不是副本佈局造成的。
  - 10 個 `verified_stream_source_is_not_private_store_output`。
  - 3 個 `SharedPublicationBusy`：同機另一個程序拿著全域 mutex（也是 H5 的佐證）。
- 所以「改前改後失敗清單相同」只能證明沒有變得更糟，不能證明 G1/G2/U2/D3/D10 正確。
- codex 在 10-05 只跑了 4 個與 41 個目標測試，沒有跑完整套件。

### V11 R1 很可能是活的

- `runner.publish_common` 每次都在候選 metadata 加 `publication_source_context`，裡面的 `deadline_catalog_identity.global_head_sha256` 是當時 baseline head（30.5 MB）。
- `_finish_current_native_scope`（每次 `_commit_published_coverage` 都會呼叫）在財報類 route 且 `source_expected_asof < expected_asof`、又帶 `formal_reuse.publication_native_units` 時，會讀 `_manifests/<sha>.json`，大於 16 MiB 就丟 `deadline_catalog_manifest_unbounded`。同函式對 catalog 本體還有一個 16 MiB 上限。
- publisher 恢復路徑會把舊候選的 `expected_asof` 改寫成當前目標日，所以日期一跨過 10-05，用已保存候選發布財報類 route 就可能踩到。是否帶 formal_reuse 宣告我沒有驗證。
- 修正很小，放第 1 波。

### V12 R2 在每次 run 的關鍵路徑上

- 每次 run 開始與結束都跑 housekeeping。v1 下每次 run 會再加約 30～40 份 30 MB 的封存。
- 把所有封存解析後同時留在記憶體，峰值現在是 2.24 GB，之後每次 run 再增加數 GB。
- housekeeping「永不拋錯」，記憶體不足時只會靜默跳過清理，物件就不再被刪（每次 run 數十 GB）。
- 必須在 v2 之前修（第 1 波）。

---

## 3. 設計審查（資料遺失／消費端／封存可驗證性／遷移）

| 設計 | 資料遺失 | 消費端破壞 | 封存可驗證性 | 遷移風險 | 判定與修改 |
|---|---|---|---|---|---|
| H1 rows_key（已部署） | 無 | 無 | 無 | 無 | 保留；補 EXPLAIN 與 40k 效能測試 |
| H2 查詢預算 | 預算太低會讓大 route 永遠延後（資料變舊但看得見） | 無 | 無 | 無 | 改成 progress handler 心跳 + watchdog 告警；要中止的話只能用很高、隨列數調整的預算，並清楚記錄 |
| H3 心跳／log | 無 | 無 | 無 | 低 | 在 runner 端寫 log，不只靠 menu 的 Tee-Object（排程或直接 CLI 呼叫也要有紀錄） |
| H4 拿掉每條 drain | 會重抓或 CAS 衝突 | 無 | 無 | 中 | 改成「恢復中的 route 先跳過、之後再處理」（V7） |
| H5 合併移出鎖、mutex 依 root 命名、read_head 無鎖 | **housekeeping 會刪掉使用中的合併暫存與物件** | 無 | 無 | **高**（collect tool 探測舊名稱） | 加進行中合併登記；保留全域名稱；讀者一次讀完再雜湊；寫入端重試（V5、V6） |
| H6 沿用提示 + owner | 無 | 無 | 無 | 低 | owner 要記 pid + 程序建立時間（避免 PID 重用） |
| H7 harvest 去重 | 無 | 無 | 無 | 低 | 見 V4 |
| G1 逐單位解除 | 無 | retained 單位會變成帶日期的 gap → 三個月報告對那些日期失敗關閉（符合「看得見」原則） | 無 | 低 | 必須和 G2 一起上；只有 G1 時 09-09／09-11 會變成 ≤D 的缺口 |
| G2 舊列無損替換 | **有**：345 個 `open` 值無紀錄被改；sidecar 會被刪 | 無（替換後值與其他日期口徑一致） | sidecar 不在保留集合 | 中 | 記錄所有被改的核心值；sidecar 放 `_evidence/`（或登記進保留集合）並在之後每次合併沿用參照（V2） |
| G3 尚未公布 → pending | 無 | 無 | 無 | 低 | 只限 effective asof 當天，且要有期限，過期仍未公布就寫成缺口 |
| G4（新）gap 單位不可被當成已覆蓋 | 無 | 無 | 無 | 低 | 見 V3 |
| U2 完整證據 | 修正後無 | **v1 內嵌會讓 head 變 51.8 MB，最長清單 78,081 項；pd_final 仍有 C1/C2 64 MiB、C6 32 KiB、C7 10,000 項上限** | 無 | 中 | 只能與 v2 一起上，或先做 U2a（下列） |
| U2a（新，v1 相容）| 無：被截斷的欄位另存完整值到 `_evidence/<sha>.json`，metadata 加參照與截斷標記；原本的 inline 值不動 | 無（只多一個小鍵，消費端不讀） | `_evidence` 只增不刪 | 低：只有現金流、財報期限的指紋會變，各重新合併一次 | 讓 v2 推出前不再悄悄遺失 |
| D3 last_attempt 去重 | 無（不同的欄位保留） | 未升級的讀取端會讀錯（單日證明退化成 unknown_empty）→ 必須升 schema | 新封存仍是 head 位元組 | 中 | 讀取端先上；改用 store 層級的格式政策（V8） |
| D10 v2 外部化 | 無 | 同上；pin 驗證要包含證據物件 | 封存位元組可驗；完整解析需要 `_evidence` 存在（只增不刪即可） | 中：第一次 v2 發布時所有 entry digest 都會變（prepared-merge 快取失效、order35 延後一輪） | 用專門的 metadata-only 遷移發布；在沒有分析程式執行時做 |
| R1 移除 16 MiB | 無 | 無 | sha 檢查保留 | 低 | 第 1 波 |
| R2 逐份處理封存 | 無 | 無 | 無 | 低 | 第 1 波（collect tool `store_prune` 同步） |
| R3（新）換 head 重試 | 無 | 無 | 避免孤兒封存 | 低 | 第 1 波 |

---

## 4. 統一實作順序

### 階段 0（現在；不改程式）

- **0a. updater 保持閒置。** 不要按選單 4、R，也不要用起日 ≤ 09-11 的指定日期。理由：
  - 任何全窗發布都會讓 omd 維持 PARTIAL；在「增量修好之後」則會變成「PARTIAL、沒有日期」，三個月報告全部失敗關閉。
  - R 會把每條 route 合併兩次（V4）。
  - 目前沒有 updater 的排程工作（已查工作排程器），只要不手動執行即可。
- **0b. 保全唯一的完整證據（需核准，寫入 D:）。**
  - 把 `D:\TWStockData\updater_runtime\runs\20261004T205159Z-ad5ea647\payloads\12-finmind_cash_flows_statement.canonical.json`（sha `114b9456…`）與 `08-official_finance_reporting_deadline.canonical.json`（sha `d96ea626…`），約 25 MB，複製到 D: 上的持久位置，並附 sha 清單。
  - C: 的副本在 Temp 底下，不算持久。
  - 期限：這個 run 一旦被 harvest 完並退休，再有兩次新 run 就會被 housekeeping 刪掉。
- **0c. codex 先修好測試基準**（V10），讓之後每一波都有可信的回歸結果。

### 第 1 波：updater 小修（v1 格式不變、消費端無感；在下一次 run 之前部署）

| 項目 | 修改 | 測試 |
|---|---|---|
| R1 | 移除 `_publication_maturity_context` 的 sealed head 與 catalog 本體兩個 16 MiB 上限，保留 sha 與大小比對 | 超過 16 MiB 的 sealed head 在 sha 相符時可讀；sha 不符仍拒絕 |
| R2 | housekeeping 與 collect tool `store_prune` 逐份處理封存，只保留 `_version` 物件名集合，完整文件只留被保留的版本與 pin 用到的 | 封存份數加倍時峰值記憶體不隨之成長 |
| R3 | `os.replace(head)` 遇到 WinError 5／32 時有界重試；最後仍失敗時要把「已封存但未成為 head」的 sha 記錄下來 | 另一個 handle 開著 head 時，發布在讀者關檔後成功 |
| H1 測試 | 所有 `_CoverageRows.result()` 查詢做 EXPLAIN；40k 列 < 2 秒 | — |
| H3 + H2（只做心跳） | run 目錄每 15 秒原子寫 `heartbeat.json`、追加 `progress.jsonl`；runner 端寫 console log；`coverage_rows.result()` 前加 stage「核對覆蓋證明」；SQLite progress handler 只更新心跳，不中止 | 假的慢 route 讓心跳前進 |
| H6 | 沿用未完成 run 時明確提示；寫 `owner.json`（pid + 程序建立時間），owner 還活著就拒絕 | — |
| H7 | owned 候選的 proof 先放進去重集合；payload sha 相同的舊候選退休 | 兩個 run 有同一 route 的候選時只合併一次 |

### 第 2 波：official_market_daily 正確性（在修復 run 之前部署）

- **G2（修正版）**
  - 除了 provider 獨有欄位，**每個被改掉的核心值**（目前是 345 個興櫃 09-09 `open`）也要把舊值、新值、依據寫進 sidecar；lineage 記錄各單位、各欄位的筆數。
  - sidecar 寫到只增不刪的 `_evidence/<sha>.json`（或正式登記進 housekeeping 保留集合），參照在之後每一次合併都要沿用。
- **G1**：照原型。
- **G4（新）**：同一個 entry 宣告為 gap 的單位，不得在 coverage 中被採為完整；重抓規劃一定要重抓它。
- **G3**：effective asof 當天若「尚未公布」，列為 pending_release，有期限；過期仍未公布就寫成缺口。
- **驗收**：
  - 全窗副本重播 SUCCESS，替換 5,108 列，345 個值變更有紀錄。
  - 「發布 → 再發布一次 omd → housekeeping」之後，sidecar 仍在，而且能從 head 找到。
  - 修好的測試基準全綠。

### 修復 run（第 2 波部署之後；需核准）

- 只跑一次 `update --mode incremental`（選單 2），**13:30 前啟動**。這樣 effective asof 是前一個交易日，三個市場都已公布。同時不要跑任何分析程式或其他 producer。
- 預期：harvest ad5ea647 已保存的 10-05 候選（有 rows_key 不會卡；有 H7 不會重複合併）；omd 全窗候選經 G2 替換 → SUCCESS。
- **驗收閘門（唯讀；任何一項不過就不要開始個股部署）**：
  - omd：`status=SUCCESS`、`gaps=[]`、`partial_recovery.status=resolved_for_scope`、`last_attempt.warnings=[]`；`legacy_row_replacement.replaced_rows=5108`；sidecar 存在且 sha 相符。
  - 用 `verify/scripts/v_xs_check_route.py`（指向新 head）確認 `check_route(official_market_daily, T=最新價格日)` 接受。
  - price_adj、per、month_revenue 都是 SUCCESS。
  - head 小於 64 MiB，所有 list 不超過 10,000 項。
  - 心跳與 log 完整。

### 個股部署（pd_final／DEPLOY_PLAN v5）

- 在修復 run 產生的 head 上 pin，**不是 efbd4844**（V1）。DEPLOY_PLAN P5 要改寫：缺口擋的是 A/B 的三個月訓練，不只是 smoke。
- A/B 的 3～3.5 小時內 updater 不得執行。housekeeping 會因為偵測到 single_stock_analysis 行程而不刪物件。
- 部署後 producer 維持 v1，而且**不能把完整證據直接 inline**，因為 pd_final 仍有 C1/C2 64 MiB、C6 32 KiB、C7 10,000 項的上限。

### 第 3 波：updater 強韌性（部署穩定後）

- H4 依 V7 重設計。
- H5 依 V6：進行中合併登記、保留全域 mutex 名稱、讀者一次讀完、寫入端重試。
- H2 watchdog。

### 第 4 波：證據完整與去重（U2／D3／D10）

1. **4a U2a（建議盡早，可與第 1、2 波一起）**：被截斷的欄位另存完整值到 `_evidence/`，metadata 加參照與截斷標記；inline 值不變，所以消費端完全不受影響。完成後可以用 0b 保全的 canonical 做一次只改 metadata 的修補發布，並記錄 `evidence_repair` 來源。
2. **4b 消費端先上線**：
   - 個股：pd_final 之後的下一版，包含 C1、C2、C6、C7 的移除，`attempt_view` 與 `resolve`，接受 v1 或 v2。
   - MIX：`src/mixstock` 三個檔。
   - Top10：`data_reader`、`stock_query_projection`。
   - telegram bot。
   - collect data tool：`app/` 與 `updater/` 兩份副本、`store_status`、`verify`、`store_prune`；`upstream_idle` 與 `preflight` 的 mutex 名稱不改。
   - 驗收：evidence 報告 P8 的升級版比對（72 次呼叫 + 8 組 fixture 與 v1 相同）。
3. **4c producer v2**：
   - U2 `_complete_evidence`、D3、D10 外部化。
   - store 層級的格式政策；入口一次轉成完整形；收據不是 list 時一律不解除缺口；12 處 schema 檢查；`verify` 核對所有參照。
   - 遷移：在沒有任何分析程式執行時，做一次 metadata-only 的 `migrate-head-format` CAS 發布。
   - 預期一次性成本：prepared-merge 快取失效、order35 延後一輪、現金流與財報期限各重新合併一次。
   - 回滾：把政策設回 v1（head 展開成約 51.8 MB；這時消費端的上限已在 4b 移除，所以可以承受）。
4. **4d 舊封存檔**：依使用者決定（§5 問題 2）。

---

## 5. 使用者必須決定的問題

1. **個股部署的時程**：個股部署要等 updater 修好 official_market_daily 之後，改在新的 head 上 pin（不再是 efbd4844）。要選哪一條？
   - **A（建議）**：先完成第 1、2 波的程式，再跑一次 13:30 前的增量 run。
   - **B**：現在就跑一次增量 run（只有 hotfix 層級的保障）。比較快，但未必成功（V3）。而且會先用掉 ad5ea647 已保存的全窗候選，之後若要用 G2 修好舊格式列，就要重新下載全窗資料。
2. **44 份大型舊封存檔**（664.6 MB，與前一份有 94.7% 位元組相同）。保持「每份都是完整 head 位元組」，pin 驗證才能維持現在的做法。要選哪一種？
   - 原樣保留，可選擇開 NTFS 壓縮（2.4:1，sha 不變）。
   - 重新打包去重。這樣所有讀封存的程式（updater、collect tool、個股）都要改成重組後再驗證。
   - 另外，封存檔與 `_evidence` 是否允許有刪除（保存期限）政策？
3. **核准把唯一的完整現金流與財報期限證據複製到 D: 的持久位置**（約 25 MB，§4 的 0b）。這必須在 ad5ea647 被清理之前完成。

---

## 6. 檔案

- 本報告：`verify/VERIFY_AND_PLAN_DETAILS.md`
- 腳本：`verify/scripts/`
  - `v_hg1_query.py`：HG1 重現
  - `v_u2_repro.py`：U2 重現
  - `v_g2_value_diff.py`、`v_g2_value_diff_bymarket.py`、`v_g2_open_detail.py`：G2 逐列比對
  - `v_xs_check_route.py`：消費端 `check_route` 判定
  - `v_replace_sharing.py`：Windows 換檔測試
  - `dguard.py`
- 輸出：`verify/out/*.txt`
- 封存物件存在性的抽樣：`verify/out/v_archive_object_presence.txt`
