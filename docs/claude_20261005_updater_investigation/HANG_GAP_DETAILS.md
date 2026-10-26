# 10-05 07:51 full-backfill 卡住，與 official_market_daily 09-30 缺口：producer 端調查（hang_gap，2026-10-05）

範圍與安全：

- D: 全程唯讀。沒有執行 updater、沒有刪除任何檔案。
- 只在副本上跑程式：
  - `asrun_src/`：07:51 那次 run 實際載入的原始碼。由 15:45 hotfix 的備份 `backups/source-only-…-698afd0e/src` 疊回 live 樹得到；8 個被換掉的檔案 mtime 都是 03:59:14。
  - `current_src/`：live 現況，含 15:45 部署的 `rows_key` 索引。
  - `patched_src/`：current 加上本報告的 G1+G2 原型。由 `scripts/make_patched_src.py` 產生，diff 在 `patch_G1_G2_stock_shared_store.diff`。
- 所有模擬都掛了 `scripts/dguard.py`，D: 寫入攔截 0 次。安靜暫存根是 `C:/Users/USER/claude_quiet_hanggap`。
- 行號除非另外註明，都指 **as-run** 版本。`stock_shared_store.py` 在 4112 行之後，current 的行號要 +1。

---

## 0. 結論

### 卡住的原因

不是無窮迴圈，也不是重試風暴、API 配額或等鎖，而是**一個二次方（O(N²)）的 SQL 查詢**。

- **主執行緒**：卡在 `publisher.drain()`（`runner.py:4908`）。
  - 呼叫鏈：`execute_phase` 對第一條優先路由 05 `finmind_price_adj` 呼叫 `recover_route_publication`（`runner.py:3337-3339`）。
- **發布執行緒**：在全域發布 mutex 內（`stock_shared_store.py:4916`）做 price_adj 的合併。
  - 07:53:20 開始，08:06:13 已寫出合併結果。
  - 之後進入 `_CoverageRows.result()` 的「bad」查詢（`stock_shared_store.py:3120-3127`）。
- **為什麼是 O(N²)**：
  - 子查詢 `rows a … where a.k=m.k` 只用 `k` 篩選。
  - 但 `rows` 的主鍵是 `(side,k)`，所以 SQLite 對每一筆 coverage member 都要從頭掃一次 rows 索引（`SCAN a USING COVERING INDEX sqlite_autoindex_rows_1`）。
- **規模估算**：
  - price_adj 兩側各約 3.07M 列。
  - 合成資料實測：5k 列 1.15 秒、10k 列 4.96 秒、20k 列 20.8 秒、40k 列 81.6 秒，每倍增 ×4。
  - 外推到 3.07M 列 ≈ **4.8×10⁵ 秒（約 5.6 天，這是下限）**。
  - 被停掉時只跑了 6.6 小時，約為下限的 5%。
- **這段程式的來源**：10-05 03:59 的部署才加入。04:49 那次 run 是第一次用它，也卡住了（推定卡在 finmind_per）。
- **修正狀態**：
  - 15:45 已部署 `rows_key` 索引（停掉之後才部署）。同一查詢在 3.07M 列降到 113 秒。
  - 已檢查其他 coverage 查詢，沒有二次方問題。
- **放大效果**：
  - 合併在整台機器共用的發布 mutex 裡執行；
  - 主執行緒在每條 route 收集前同步 drain。
  - 所以一個慢合併，就讓整個 run 和其他 head 讀取者一起停住（§1.1、§6.3）。

### 09-30 缺口

- **09-30 是交易日，來源也有給資料。**
  - 10-04 04:54 重抓時，TWSE、TPEx、興櫃三個市場都是 HTTP 200 SUCCESS。
  - 已發布物件 `6265b799…` 裡就有 09-30 的 2,757 列。
- **缺口來源**：09-30 01:50 啟動、expected_asof 2026-09-30 的 run `fa427aad`，在 09-30 11:24（收盤前）就抓了當天資料，記成 TWSE/TPEx `FAILED`、興櫃 `EMPTY`。
- **PARTIAL 一直解不開的原因**：
  - 已存物件裡有 **5,108 筆舊格式列**，無法被替換：
    - 09-09：2,717 筆。
    - 09-11：2,391 筆。
  - 因此每次全窗合併都加上警告 `incomplete_replacement_retained`。
  - 只要有任何警告，`current_scope_complete` 就不成立（4714-4717），於是根本不會呼叫「依收據解除缺口」的規則（2086-2107）。
  - 結果是 09-30 被重新宣告成缺口（4761），狀態維持 PARTIAL（4785）。
- **副本重播驗證（current 程式）**：

  | 候選視窗 | 對應的實際操作 | 結果 |
  |---|---|---|
  | 全窗 | resume / full-backfill | 仍是 PARTIAL，09-30 缺口還在 |
  | 增量窗（09-21 起） | `update --mode incremental` | **SUCCESS**，09-30 解除 |
  | 全窗 + G1/G2 原型 | resume／full-backfill（修正後） | **SUCCESS**，5,108 筆舊列被替換，09-30 解除（§6.3） |

- **注意**：只靠增量修好之後，下一次全窗發布會讓 route 再變回 PARTIAL，而且**沒有缺口日期**。這點已用重播證實。09-29 時就是這個狀態，會讓消費端以「缺口不明」失敗關閉。要長期穩定，必須修 G2。

---

## 1. 07:51 那次 run 做了什麼（時間軸）

時間都是台北時間；Z 表示 UTC。

| 時間 | 證據（唯讀觀察） | 程式位置與意義 |
|---|---|---|
| 約 07:51 | 使用者從選單啟動，PID 57376/10424 | `menu.ps1:292-294`（或 :214-217 留空日期）→ `update --mode full-backfill` |
| 07:52:06.6–07:52:42.3 | `updater_runtime/logs/housekeeping/20261004T235206Z.json` | 啟動前整理（`runner.py:3950`）。成功取得發布 mutex，刪了 11 個物件（832,617,716 B）和 1 個中斷合併留下的暫存（4,093,229,401 B，應是 04:49 那次 run 的殘留） |
| 07:52:24 | `updater_runtime/state/publication_recovery_pending.json` | 58 筆 `harvest_pending`：424b9768 有 32 筆、ad5ea647 有 26 筆 |
| 約 07:52:43 | 沒有新的 run 目錄 | `run_io.create_run_dirs` → `_matching_incomplete_run`（`run_io.py:571-593, 622-626`）：identity（full-backfill／1990-01-01／2026-10-05／registry b910e7b0）與 **04:49 的 run `20261004T205159Z-ad5ea647`** 相同，且該 run 沒有 `run_summary.json`，所以 07:51 的程序**直接沿用它的目錄** |
| 07:52:44–45 | run 目錄裡 00/04/08 的 receipts/payloads 被重寫 | 啟動路由依序執行 |
| 07:52:45.8 | head `efbd4844`（`published_at` 23:52:45.808922Z） | route_0 official_calendar 發布，這是和上一版 `f68bbd93` 唯一的差異。注意 head 的 `run_id` 寫的是 ad5ea647，雖然寫入者是 07:51 的程序 |
| 07:53:20–21 | `_objects/78eac9cc….json`（1,155,192,481 B） | 就是 ad5ea647 的 `payloads/05-finmind_price_adj.json`（sha 與 recovery 的 artifact_refs 一致）→ publisher-only recovery 正在發布 05 |
| 07:53:22 | `_objects/tmpvg8e7df6/rows.sqlite` 建立 | `_merge` 開始（`stock_shared_store.py:5165` → 4108） |
| 08:01:44 | `rows.sqlite-journal` 建立 | 讀入階段結束（約 8.4 分鐘），commit 後的第一個 DML（4390-4487） |
| 08:05:51 | `merged.json` 建立，journal 最後一次寫入 | stage「寫出合併結果」（4517） |
| 08:06:13 | `merged.json` 寫完；hardlink 成 `_objects/e35ef242….json`（879,466,862 B） | `_object()`（4883） |
| 08:07:30 | `rows.sqlite` 最後一次寫入 | 讀查詢時快取頁溢寫；位置在 `coverage_rows.result()`（4901 → 3105-3127） |
| 08:07:30 → 14:45 | `updater_runtime`、`_metadata`、`_manifests`、`_objects` **零寫入**；佔滿一顆核心、working set 1.18 GB | 正在執行 3120-3127 的相關子查詢 |
| 14:45 | Claude 停止程序 | 留下 `tmpvg8e7df6`：rows.sqlite 6,115,225,600 B、journal 2,399,550,960 B、merged.json |

補充說明：

- 合併結果已登記成物件之後，`publish()` 的下一步本來是寫 coverage sidecar，再寫 head。這兩者在 08:07 之後都沒有出現，所以程序停在 4883 和 sidecar 之間，也就是 `coverage_rows.result()`。
- 這段期間 console 一直顯示「核對合併結果雜湊 … 已耗時 N 秒」：
  - 最後一個 stage 標籤是 4859 行設定的；
  - coverage 查詢自己沒有 stage；
  - 所以畫面看起來像在算雜湊。
- 選單是 `| Out-Host`，沒有任何檔案 log；run 目錄裡也沒有進度檔。

### 1.1 執行緒為什麼全部停住

- `recover_route_publication`（`runner.py:4875-4908`）在收集每條 route **之前**：
  - 先把同一個 run 已保存的來源（或其他 run 的 harvest）送進 publisher；
  - 然後呼叫 `publisher.drain()` 同步等待。
- `_route_priority`（`runner.py:2053-2067`）把「required 的 FinMind route」排第一，所以啟動路由之後第一條就是 05 price_adj。
  - 05 的 receipt：required=True、PARTIAL、3,070,158 列。
- publisher 是單執行緒（`publication_worker.py:67-180`），卡在 merge 裡，所以：
  - 主執行緒永遠等在 drain；
  - 其他 route 都沒有收集，也沒有發布；
  - order21 階段沒有開始。
- 合併在全域具名 mutex 內執行（`stock_shared_store.py:4916` → 5165-5169；mutex 定義在 764-789，非阻塞）。
  - 07:53–14:45 之間，其他發布者和 housekeeping 都會得到 `SharedPublicationBusy`。

### 1.2 排除的其他可能

| 假設 | 判斷 | 依據 |
|---|---|---|
| 無窮迴圈 | 否 | 是有限的 O(M×R) 查詢；合成重現呈二次方成長（§3） |
| 重試風暴／API 配額 | 否 | 07:52:44 之後 run 的 `http_cache`、`raw` 都沒有新檔；主執行緒在 drain，不在收集 |
| 等鎖 | 否 | 這個程序就是 mutex 的持有者；07:52 housekeeping 和 07:52:46 發布都拿得到鎖，表示當時沒有其他持有者 |
| 路由太大 | 部分 | 3.07M 列 × 全歷史讓 M、R 都很大，但真正原因是查詢少了 `k` 索引；有索引時 113 秒 |

---

## 2. 同一個缺陷也讓 04:49 的 run（ad5ea647 原程序）卡住

### 時間軸

- **啟動**：PID 11536/43340，start 20:49:52Z（見 `stop_checkpoint_deploy_ad5ea647.ps1:30-31`）。
  - housekeeping 20:50:03Z–20:51:58Z，釋出 36 GB 物件；run 目錄 04:51:59 建立。
- **發布紀錄**：

  | 時間 | head | 內容 |
  |---|---|---|
  | 04:52:00 | `dc0463b7` | calendar |
  | 04:52:03 | `66e9c3e6` | finance deadline |
  | 05:03:11 | `6c6fd285` | futures |
  | 05:30:54 | `1b2f1e68` | cash_flows |
  | 05:32:14 | `f68bbd93` | block_trade |

- **cash_flows 的耗時符合二次方預測**（86,542 列）：
  - 候選物件 05:03:42 → `merged` 物件 `2a3380666` 05:22:26 → head 05:30:54；
  - 合併結果寫出後，還要 **8.5 分鐘**；
  - 合成模型預測 86.5k 列約 6.4 分鐘，量級一致。
- **block_trade**（52k 列）：merged 之後 48 秒。
- **05:32:14 之後再也沒有發布。**
  - 佇列中的下一條是 finmind_per（receipt 05:24:23，2.23M 列）。模型預測約 70 小時。
  - 收集持續到 05:55:44（route 01 market daily），然後主執行緒會停在 `regular_completed` 的 drain。
- **codex 在 05:56–06:40 已經診斷出來**：
  - `staging/formal-reuse-hotfix-20261005/verification/coverage-correlated-query-plan-f-20261005T062628.json`：EXPLAIN 顯示 `SCAN a`；
  - hotfix 測試用的也是 finmind_per 情境（`tests/test_coverage_rows_key_index_hotfix.py`）；
  - 準備了停止加部署工具，但當時 `stop_or_deploy_performed=false`。
- **何時結束**：07:52 之前這個程序已經不在：
  - housekeeping 拿到了 mutex；
  - 4.09 GB 的殘留合併暫存被刪掉。
  - 是誰、何時停掉的，**沒有任何檔案記錄**（見待討論問題）。

### 缺陷是什麼時候進來的

- 部署前的 `stock_shared_store.py`（`formal-reuse-20261005/baseline` 和 `rollback/…/atomic-backup`）裡，`coverage_members` 和 `_CoverageRows` 都出現 0 次。
- 這兩段是 10-05 03:59:11–15 部署的 34 個檔案之一（`IMPLEMENTATION.md`）。
- 所以 10-05 之前的 run 不受影響。04:49 和 07:51 兩次 run 都踩到。

---

## 3. 合成重現（離線，不讀 D:）

- 腳本：`scripts/sim_coverage_query.py`、`scripts/sim_other_queries.py`。
- 輸出：`sim_coverage_query.out`、`sim_other_queries.out`。
- 做法：用 as-run 的表結構和原始 SQL。資料形狀模擬全歷史 price_adj 合併：兩側同鍵，新側較新，舊側被 decisions 刪掉。

| 每側列數 | rows_key 索引 | 3120 bad 查詢秒數 | 查詢計畫 |
|---|---|---|---|
| 5,000 | 無 | 1.15 | `SCAN a USING COVERING INDEX sqlite_autoindex_rows_1` |
| 10,000 | 無 | 4.96 | 同上 |
| 20,000 | 無 | 20.8 | 同上 |
| 40,000 | 無 | 81.6 | 同上 |
| 40,000 | 有 | 0.19 | `SEARCH a USING INDEX rows_key (k=?)` |
| 400,000 | 有 | 9.3 | 同上 |
| 3,072,673 | 有 | 112.8 | 同上 |

- 外推：無索引時 c ≈ 5.1×10⁻⁸ 秒／列²；3.07M 列 ≈ 4.8×10⁵ 秒，約 5.6 天。
  - 實際的鍵和列都比合成資料長，資料庫有 6 GB、快取只有 64 MB，所以這是下限。
  - 若用 cash_flows 的實測耗時推算，約數週。
- 其他 `result()` 查詢（3115、3131、3141、3167）在 3.07M 列沒有索引時：
  - 3115 是 0.5 秒；
  - 3131 不到 0.1 秒；
  - 3141、3167 各約 42 秒。
  - 都不是二次方。

---

## 4. 留下的東西與副作用（全部未動）

| 項目 | 大小 | 說明 |
|---|---|---|
| `shared_market_data/_objects/tmpvg8e7df6/` | 約 9.4 GB | rows.sqlite 6.1 GB＋journal 2.4 GB＋merged.json 0.88 GB（與 e35ef242 同一份資料塊） |
| `_objects/78eac9cc….json` | 1.155 GB | price_adj 候選複本，沒有被 manifest 參照 |
| `_objects/e35ef242….json` | 0.88 GB（hardlink） | 未發布的合併結果 |
| run `20261004T205159Z-ad5ea647` | payloads 25.3 GB、raw 20.5 GB | 沒有 `run_summary.json`，仍是「未完成」 |

- 下一次 run 的 housekeeping 會用 `plan_temp_leftovers` 和 `plan_object_prune` 清掉暫存和未參照物件。不需要手動刪。
- **今天（10-05）再按選單 4，會再沿用 ad5ea647**，因為 identity 相同：
  - 它會先把舊 payload 發布一次，再全部重新下載（keep_completed=False）；
  - 這是重工，請改用 R（§7）。
- head `efbd4844` 的 `run_id` 是 ad5ea647，但真正寫入的是 07:51 的程序。來源追溯會混淆（見 H6）。

---

## 5. official_market_daily 2026-09-30 缺口

### 5.1 09-30 是不是交易日：是

- 官方日曆物件的 2026 年 9 月休市只有 09-25、09-28（og_gap 的 `probe_gap_day`）。
- price_adj 在 09-30 有 2,336 筆，per 有 1,970 筆。
- 本次自己串流掃描發布物件 `6265b799…`（sha256 與大小都核對，共 3,036,629 列；`scripts/omd_legacy_rows.py`）：

  | 市場 | 09-30 列數 | source_id | `_shared_collected_at` |
  |---|---|---|---|
  | TWSE | 1,382 | `twse_daily_all` | 2026-10-03T20:54:40Z |
  | TPEx | 1,013 | `tpex_mainboard_daily` | 2026-10-03T20:54:40Z |
  | 興櫃 | 362 | `tpex_emerging_daily` | 2026-10-03T20:54:41Z |

### 5.2 來源有沒有給資料：有

head `9d04c031`（10-04 08:56:57，run `20261003T203425Z-9a26f992`）裡 09-30 的 receipts：

| 市場 | HTTP | 狀態 | 原始列數（過濾權證前） | retrieved_at |
|---|---|---|---|---|
| TWSE | 200 | SUCCESS | 35,794 | 10-03T20:54:59Z |
| TPEx | 200 | SUCCESS | 11,772 | 10-03T20:54:47Z |
| 興櫃 | 200 | SUCCESS | 362 | 10-03T20:54:43Z |

10-04 和 10-05 兩次 run（424b9768、ad5ea647）的候選是同一個 sha `c864cf08…`，也含這 2,757 列。

### 5.3 缺口怎麼來的

`scripts/omd_history.py` 逐份追 `_manifests`：

| 時間 | head | run | 內容 |
|---|---|---|---|
| 09-29 12:31 | `62e8db7e` | `aeaf018f` | 已經是 PARTIAL，`gaps []`，`warnings [incomplete_replacement_retained, prior_partial_unresolved]`。舊格式列的問題 09-30 之前就存在 |
| 09-30 11:24:05 | `66cefa82` | `20260929T175052Z-fa427aad` | 09-30 01:50 啟動、expected_asof 2026-09-30。coverage 3,639/3,642，units = 09-30 TWSE FAILED、TPEx FAILED、興櫃 EMPTY |
| 09-30 23:30:03 | `b0726d0d` | `f5549d9e` | 舊 run（expected 09-24）重新發布，把 09-30 帶進 `prior_gaps`／`unresolved_prior_gaps` |
| 10-04 08:56:57 | `9d04c031` | `9a26f992` | 已抓到 09-30（SUCCESS），但仍是 PARTIAL，09-30 被重新宣告成缺口 |

09-30 11:24 那次：

- 當天 13:30 才收盤，這次在 11:24 前就向交易所要 09-30 的資料，所以拿不到。
- 當時的程式沒有 13:30 上限。`calendar.py` 是 09-30 18:21 才改的，現在的 `resolve_daily_effective_asof`（`calendar.py:54-88`）會在 13:30 前把日期退一天。
- 剩餘風險：13:30 之後、交易所實際公布之前，仍可能抓到「尚未公布」。`calendar.py:63-65` 的註解自己也承認這點。

### 5.4 為什麼一直是 PARTIAL（producer 端根因）

1. **舊格式列**：本次串流掃描結果，共 5,108 筆，都沒有 `_shared_collected_at`。
   - 2026-09-09：2,717 筆。
     - source_id 是 `market_twse_daily`、`market_tpex_daily`、`market_tpex_emerging_daily`（舊的 producer，現行 src 裡沒有）；
     - 多了 `code`、`session` 欄；`amount` 全是 null；沒有 `transactions`。
   - 2026-09-11：2,391 筆。
     - 沒有 source_id，帶著原始 provider 欄位；
     - TWSE 列：`Code`、`ClosingPrice`、`TradeValue`、`Change`…；
     - TPEx 列：`SecuritiesCompanyCode`、`TradingShares`、`NextLimitUp`、`LatestBidPrice`、`Capitals`…。
2. **替換規則**（4447-4454）：舊列沒有收集時間時，「舊列每個非空欄位，新列都要有值」才替換。
   - 新列沒有 `code`、`session` 和那些原始欄位，所以拒絕替換，加上 `incomplete_replacement_retained`。
   - 副作用：09-09 保留的是 `amount` 為 null 的舊值；候選裡其實有 amount 和 transactions。
3. **解除缺口的條件**（4710-4729）：
   - `current_scope_complete` 要求 `not warnings`。
   - 只有 tdcc 和 government_bank 例外會呼叫 `_resolve_prior_gaps`。
   - 所以 official_market_daily 專用、依 (date, market) 收據解除缺口的規則（2086-2107）**永遠不會被執行**。
4. **結果**：
   - `unresolved_prior_gaps` = 09-30 三筆，並加上 `prior_partial_unresolved`（4742-4743）；
   - `lineage['gaps'] = _prior_gap_values(lineage)`（4761），把 09-30 重新宣告成「現在的缺口」；
   - `(old and warnings)` 讓狀態變成 PARTIAL（4785）。
5. **10-04、10-05 的新候選都沒發布**：
   - 424b9768：`regular_publication.json` 記錄 official_market_daily 等 24 條是「shared publication did not acknowledge route」，codex ledger 的原因是 `resolved_demand_invalid`。它在 03:50 被停掉並部署新程式。
   - ad5ea647：卡住了（§1、§2）。
   - 兩者都還在 `publication_recovery_pending.json`，狀態 `harvest_pending`。
   - 就算發布了，也還是 PARTIAL（§6.2 全窗重播）。

### 5.5 哪種 run 能補好（副本重播，current 程式）

腳本：`scripts/sim_omd_publish.py`。方法：

- 複製 head 和 1.29 GB 物件到安靜暫存根；
- 用 ad5ea647 的真實候選列與 receipts 組候選；
- 呼叫真的 `stock_shared_store.publish`。

| 情境 | 候選列數 | 發布耗時 | 結果 |
|---|---|---|---|
| 增量窗（≥ 09-21，等同 `update --mode incremental`；official_market_daily 視窗 = asof − 14 日曆天，`route_window_policy.py:600-601`） | 22,051 | 241 秒 | **SUCCESS**，`gaps []`，`partial_recovery.status = resolved_for_scope`，09-30 三筆都在 `resolved_prior_gaps`，`last_attempt.warnings []`（`sim_omd_publish_incremental.json`） |
| 全窗（等同 resume／full-backfill／from-date ≤ 09-11） | 3,036,629 | 681 秒 | **PARTIAL**，09-30 缺口不變，`warnings [incomplete_replacement_retained, prior_partial_unresolved]`（`sim_omd_publish_full.json`） |

- 增量窗能修好，是因為不碰 09-09／09-11 的舊列，沒有警告，所以第 3 點的解除規則會被執行。
- **但不穩定，已用重播證實**：
  - 從增量修好後的 head（`efb02f14`，SUCCESS）出發，再發布一次全窗候選；
  - 結果是 **PARTIAL、`gaps []`、`warnings [incomplete_replacement_retained]`**（702 秒，`sim_omd_publish_full_after_incremental.json`）；
  - 原因：修好後 old 狀態是 SUCCESS，全窗候選再碰到舊列時，跳過恢復區塊（4653），直接被 `old and warnings` 判成 PARTIAL，回到 09-29 的狀態；
  - 消費端（例如三個月模型的 `check_route`）遇到這種「PARTIAL 沒有日期」會以 `xs_route_partial_gap_unknown` 失敗關閉。

---

## 6. 修正設計（程式）

### 6.1 卡住相關

| 編號 | 修改 | 位置 | 測試 |
|---|---|---|---|
| H1（**已部署** 15:45） | `create index rows_key on rows(k)` | `stock_shared_store.py:4113`（current） | 已有：`tests/test_coverage_rows_key_index_hotfix.py` 只檢查 bad 查詢的計畫。建議補兩項：(a) 對 3115/3131/3141/3167 也做 EXPLAIN，斷言 correlated 子查詢裡沒有 `SCAN a`；(b) 效能護欄：40k 列 `result()` 要 < 2 秒（無索引時 81 秒） |
| H2 | 長查詢預算與心跳：`database.set_progress_handler(cb, 1_000_000)`。cb 更新進度；單一查詢超過預算（例如 30 分鐘）就丟 `CandidateError('merge_query_budget_exceeded')`，該 route 標為 deferred、run 繼續。在 `coverage_rows.result()` 前加 `stage('核對覆蓋證明')` | `_merge` 4108 附近、4900 | 用 monkeypatch 把預算調成 0，斷言 route deferred、其他 route 照常發布 |
| H3 | 可追溯的執行紀錄：runner 在 run 目錄每 15 秒原子寫 `heartbeat.json`（pid、phase、route、stage、elapsed、最後寫入時間），並 append `progress.jsonl`。`menu.ps1:125` 改用 `Tee-Object` 寫到 `updater_runtime/logs/menu-<ts>.log` | `progress.py:78-81`、`runner._emit`、`menu.ps1:125` | 跑一個假 route，斷言 heartbeat 會前進 |
| H4 | 不讓發布擋住收集：`recover_route_publication` 不要每條 route 都同步 `publisher.drain()`（4907-4908），改在 `regular_completed` barrier 一次 drain；或 drain 加逾時後繼續收集 | `runner.py:4875-4908` | publisher 卡住時，其他 route 仍會被收集 |
| H5 | 合併移到全域 mutex 外：mutex 只包 CAS、head、manifest 寫入。合併先在 lock 外做，沿用 prepared-merge 快取（5125-5130、5250）；CAS 不符時重驗或重做 | `stock_shared_store.publish` 4916/5165 | 兩個 publisher 並行時，長合併期間另一個小 route 仍能發布 |
| H6 | 沿用未完成 run 時要明示：`create_run_dirs`（`run_io.py:622-626`）沿用同 identity 的 run 時，emit「續用未完成 run <id>（起於 …）」，並寫 `owner.json`（pid、start）。前一個 owner 還活著就拒絕。manifest 加上本程序的 attempt id，修正 head `run_id` 的追溯 | `run_io.py`、`stock_shared_store.publish` | 沿用時有訊息；owner 存活時拒絕 |
| H7 | harvest 去重：同一 route 有較新、同範圍的已保存候選（ad5ea647 取代 424b9768）時，舊的不要再做全歷史合併；新的被 ACK 之後，把舊的 `harvest_pending` 退休 | `successor_resume.select_publication_recovery_candidates` 704-759；`runner.recover_route_publication` | 兩個 run 都有同 route 候選時只合併一次 |

### 6.2 09-30／PARTIAL 相關（原型在 `patched_src`，diff 195 行）

**G1：缺口解除改成依單位判斷**（`stock_shared_store.py` 4710-4743、`_resolve_prior_gaps` 2013/2086-2107）

- 合併時記錄 `retained_units`：拒絕替換的 (date, market)。
- official_market_daily 的警告若只有 `incomplete_replacement_retained`，`current_scope_complete` 不再整體失效。
- `_resolve_prior_gaps` 多一個參數 `blocked_units=retained_units`：被保留的單位不解除，其他單位照收據規則解除。
- 只要還有 `retained_units`，就逐一寫進 `gaps`：`{date, market, status:'RETAINED_STORED_VERSION', reason:'incomplete_replacement_retained'}`。
- 目的：**PARTIAL 永遠帶日期**，消費端不會再遇到「缺口不明」。

**G2：official_market_daily 舊格式列的無損替換**（4447-4454，新增 `_market_daily_legacy_split`）

- 前提：舊列沒有收集時間、新列身分（stock_id/date/market）相同，而且新列不會讓任何核心值（open/high/low/close/volume/amount/transactions/name）由有變無。
- 舊列多出來的欄位分兩類：
  - **證明重複的別名**：`code`／`Code`／`SecuritiesCompanyCode` = stock_id；`session` = date；民國 `Date` = date；`Name`／`CompanyName` = name；數值別名（例如 `ClosingPrice`）等於該列自己的正規化值。
  - **provider 獨有欄位**：例如 `Change`、`NextLimitUp`／`NextLimitDown`、`NextReferencePrice`、`LatestBidPrice`／`LatesAskPrice`、`Average`、`Capitals`。
    - 這些不丟掉，原樣寫進一個 sidecar 物件；
    - lineage 記錄 `legacy_row_replacement`：替換列數、各單位列數、sidecar 的 path/sha/size/rows。
- 證明完成後才替換。替換前的物件仍由 `previous_object_sha256` 指向。
- **正式版還差一步**：sidecar 要登記進 housekeeping 的保留集合（例如 coverage `attachments`），否則會被當成未參照物件清掉。原型還沒做這一步。

**和 U2（證據截斷）的交互**

- G1 依賴 `metadata.source_receipts` 裡剛好一筆 (date, market) 收據（2099-2105）。
- 這個欄位經過 `shared_publication._bounded_evidence`：
  - 清單上限 4096；
  - 每欄位約 150k 節點。
- route 01 的收據是日期由新到舊排列，所以超過上限時，被截掉的是**最舊**的收據。
- 結果：舊日期的缺口會靜默地變成「無法解除」，而且不會有截斷標記。
- 所以 U2 的修正（不截斷，或至少加上截斷旗標）也是 G1 長期正確的前提。

**G3：「尚未公布」不要記成缺口**

- `collect_market_daily.py:270-284` 已經會標 `not_yet_published`。
- 要再補：effective asof 當天若是 not_yet_published，route 要把那天列為 `pending_release`（比照 government_bank 的 `is_complete_pending_release_scope`），不列入範圍、下次重抓，不寫成 FAILED 或 EMPTY 缺口。

**驗證**

- §6.3 的全窗重播。
- 19 個與合併相關的測試檔，current 與 patched 結果完全相同：各 101 failed、314 passed；只出現在其中一邊的失敗數為 0；DGUARD 0 次。
  - 這 101 個失敗是複製環境下的既有失敗，修改前後一樣；只有差異有意義。
  - 原因沒有追（待討論）。
  - 輸出：`pytest_selected_current.txt`、`pytest_selected_patched.txt`。

### 6.3 G1+G2 原型的全窗重播

輸出：`sim_omd_publish_full_patched.json`。重播的是和 §5.5 同一個全窗候選（3,036,629 列）。

| 項目 | current（無修正） | patched（G1+G2） |
|---|---|---|
| status | PARTIAL | **SUCCESS** |
| gaps | 09-30 三筆 | `[]` |
| partial_recovery | unresolved | resolved_for_scope（09-30 三筆都已解除） |
| last_attempt.warnings | incomplete_replacement_retained、prior_partial_unresolved | `[]` |
| 舊格式列被替換 | 0 | **5,108** |
| 列數 | 3,036,629 | 3,036,629（不變） |
| 全程耗時 | 681 秒發布 | 929.5 秒（與另一個重播同時跑） |

- 替換明細：
  - TWSE/09-09：1,374
  - TPEx/09-09：989
  - Emerging/09-09：354
  - TWSE/09-11：1,379
  - TPEx/09-11：1,012
- 09-11 的 2,391 列 provider 獨有欄位寫進 sidecar `_objects/929b869b….json`（587,378 B，2,391 列），沒有丟失。
- 09-09 的列只有證明重複的別名（`code`、`session`），所以沒有 sidecar 列。
- DGUARD 攔截 0 次。

**模擬時順帶發現：發布 mutex 是整台機器共用的，`read_head` 也會去拿它。**

- mutex 名稱固定為 `Global\TWStockData.SharedMarketData.SingleProducer.v1`（`stock_shared_store.py:41`），和 shared root 無關。
- 所以兩個在不同副本上的重播也會互搶：第二個 `read_head` 直接得到 `SharedPublicationBusy`。
- `read_head`（824-838）讀 head 也要拿這個 mutex，只重試 3 次、每次 50 ms。

影響：

- 07:53–14:45 卡住期間，任何經過 updater 程式讀 head 的程序都會失敗或被擋。包括 `shared_publication.read_baseline`、housekeeping，以及同步版 `D:/collect data tool`。
- `D:/stock` 的消費端沒有使用這個 mutex（grep 0 筆）。
- 測試或副本模擬在 live updater 執行時也會互相干擾。

建議（H5 的一部分）：

- mutex 名稱加上 shared root 的雜湊；
- 讀 head 不要拿寫入鎖，改成讀到的 head 再用雜湊驗證。

---

## 7. 修復已發布資料的操作步驟（本次都沒有執行）

**步驟 0：前提（唯讀檢查）**

1. live `D:/TWStockUpdater/src/twstock_updater/stock_shared_store.py:4113` 有 `create index rows_key`。**已確認**。
2. 沒有 updater 程序在跑。14:40 之後 `updater_runtime`、`_metadata`、`_manifests`、`_objects` 零寫入。**已確認**，16:5x 檢查。
3. 建議先部署 G1+G2（或至少 G2），這樣步驟 1 一次就能讓 official_market_daily 變成 SUCCESS，並換掉 5,108 筆舊列。
   - 這一點已用副本全窗重播驗證（§6.3）。
   - 正式版要先補 sidecar 的保留登記，並由 codex 在正式 staging 跑完整測試。
   - 如果暫時不部署，就要另做步驟 2。
4. 執行期間不要在同一台機器跑任何會呼叫 updater `read_head` 或 `publish` 的工具、測試或模擬，因為 mutex 是全機共用的（§6.3）。

**步驟 1：把 10-05 已下載的資料發布出去**

- 選單選 `R`，等於 `python -m twstock_updater resume`，會選到最新的未完成 run ad5ea647。
- 建議明確指定：`resume --run-id 20261004T205159Z-ad5ea647`。
- 效果：
  - 沿用已保存的 32 條結果，不重新下載；
  - `retry_incomplete` 會重試 PARTIAL 的 route（例如缺 10-05 的 block_trade、futures）；
  - 逐條發布，最後做 Order21。
- 預估時間（推估，不是實測）：
  - **約 3–4 小時的合併**。依據：price_adj 2.03 GB 讀入 8.4 分鐘，約 0.24 GB/分；已保存 payload 23.6 GB＋已存物件約 15 GB，約 39 GB，讀入約 2.7 小時，再加 coverage 和寫出約 0.5–1 小時。
  - 如果 harvest 又把 424b9768 的舊候選先合併一遍（H7 未修），**最多約 7 小時**。
  - 另加 Order21 新交易日的時間，沒有估。
  - 每次發布會多一份約 30 MB 的 manifest 封存（D10），約 30 份，約 0.9 GB。
- 沒有 G2 時，這一步之後 official_market_daily 仍是 PARTIAL（09-30）。這已在全窗重播中證實。

**步驟 2：只在沒部署 G1/G2 時做**

- 步驟 1 完成後，在 TWSE、TPEx、興櫃都已公布最後一個交易日之後，選單選 `2`（`update --mode incremental`）。
  - 例如 10-06 18:00 之後，或 10-07 13:30 之前。
- 預估：
  - official_market_daily 本身約 4 分鐘（副本實測 241 秒）；
  - 整個 run 由其他 route 的合併主導（每次合併都要重讀完整已存物件，約 15 GB），約 1–1.5 小時，加上 Order21。
- 限制：之後任何全窗或 from-date（起日 ≤ 09-11）的 run，都會讓 route 回到「PARTIAL、沒有日期」。要等 G2。

**步驟 3：驗收（唯讀）**

- head 的 official_market_daily：`status SUCCESS`、`gaps []`、`partial_recovery.status resolved_for_scope`、`last_attempt.warnings []`。
- 有 G2 時，另外確認：`legacy_row_replacement.replaced_rows` = 5,108；sidecar 列數與檔案存在。
- `_objects/tmpvg8e7df6` 已被 housekeeping 清掉。
- 不再有超過 1 小時的 `tmp*` 目錄。

**今天請不要再按選單 4**：會再沿用 ad5ea647，先發布舊 payload、再全部重新下載。

---

## 8. 本次自己的操作與清理

- D:、staging、`D:/stock` 都沒有寫入，DGUARD 攔截 0 次。
- 只刪過本批自己幾分鐘前建立的暫存，沒有刪任何既有檔案：
  - 重建前的 `patched_src`、`variants/*`；
  - 第一次 layout 錯誤時的 `pytest_current` 暫存；
  - 合成模擬的 sqlite 檔；
  - 一次因 mutex 被自己另一個重播佔住而失敗的 `sim_omd_full_after_incremental` 半成品。
- 安靜暫存根 `C:/Users/USER/claude_quiet_hanggap`（含 1.29 GB 物件副本等）在結束時移到 `three_month_impl/_trash/hang_gap_quiet_root/`。

## 9. 檔案

- 報告：`HANG_GAP_DETAILS.md`（本檔）。
- 腳本 `scripts/`：
  - 調查：`omd_history.py`、`omd_legacy_rows.py`、`omd_tail_keys.py`；
  - 重現與重播：`sim_coverage_query.py`、`sim_other_queries.py`、`sim_omd_publish.py`；
  - 原型與測試：`make_patched_src.py`、`run_selected_tests.sh`、`dguard.py`、`dguard_plugin.py`。
- 輸出：
  - `sim_coverage_query.out`、`sim_other_queries.out`；
  - `sim_omd_publish_incremental.json`、`sim_omd_publish_full.json`、`sim_omd_publish_full_patched.json`；
  - `pytest_selected_*.txt`；
  - `patch_G1_G2_stock_shared_store.diff`。
- 副本：
  - `copies/`：head、recovery pending、housekeeping log、route 01 的 receipt/canonical；
  - `asrun_src/`、`current_src/`、`patched_src/`、`variants/`、`tests_copy/`。
  - **沒有**複製 `.ENV`。
