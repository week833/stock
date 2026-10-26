# 生產端修正設計：U2（證據被截斷）、D3（last_attempt 重複）、D10（head 封存檔重複）

日期：2026-10-05。範圍：TWStockUpdater（`D:/TWStockUpdater/src/twstock_updater`，下稱 live）。

全程唯讀：
- 沒有執行 updater，沒有寫入 D:。
- 所有量測都在 C: 的副本上做，D: 只用 `rb` 讀取。每支腳本都掛了 dguard（D: 寫入防護）。
- 測試副本中有一個既有測試會嘗試 `mkdir D:\TWStockUpdater\runs\test\order21`，被 dguard 擋下。base 與 patched 兩邊都一樣，沒有任何寫入真的到達 D:。

工作資料夾：`scratchpad/updater_inv_20261005/evidence/`（下稱 `E/`）。

---

## 0. 結論

### U2：證據被截斷（已確認，位元組完全相符）

**根因**：`shared_publication._bounded_evidence`（live L43-65）對每個證據欄位設了以下上限，超過的部分直接丟掉，沒有任何旗標：
- 清單最多 4096 項
- mapping 最多 64 個鍵
- 字串最多 1024 字
- 鍵名最多 128 字
- 深度最多 8 層
- 每個欄位最多 150,000 個節點

它由 `_publication_metadata`（L68-75）逐欄呼叫，所以節點預算是「每個欄位各 150,000」。

**實證**：拿 run `20261004T205159Z-ad5ea647` 自己留下的完整 metadata（`payloads/NN-*.canonical.json`），丟進副本的 `_publication_metadata`，輸出與 head `efbd4844` 內的值位元組完全相同：
- `finmind_cash_flows_statement.source_receipts`：6,104,912 B
- `finmind_cash_flows_statement.diagnostics`：484,319 B
- `official_finance_reporting_deadline.source_receipts`：222,050 B

**實際遺失量**：

| 欄位 | 完整值 | head 中的值 | 遺失 |
|---|---|---|---|
| 現金流 `source_receipts` | 7,972,609 B，196,178 個節點 | 第 14–16 筆整筆變 null；第 13 筆 `members[271]` 起變 null | 44,413 個節點，另有 14 個葉值被改成 null |
| 現金流 `diagnostics` | 78,081 項（9.22 MB） | 4,096 項 | 73,985 項（94.8%） |
| 財報期限 `company_period_keys` | 5,475 與 4,460 | 兩者都是 4,096 | 共 1,743 個 |

用無上限的 sanitizer 處理這三份完整值：0 個字串被遮罩、0 個私密鍵被移除。也就是說，舊函式在真實資料上唯一的作用就是截斷。

**官方行情 receipts 的「再約 151 個交易日會撞到 4096」需要修正**：
- receipts 依新到舊排列，視窗是 5 年（2021-10-02～2026-10-02，共 1,214 個交易日 × 3 個市場 = 3,642 筆）。
- 視窗固定時，筆數會穩定在約 3,650，不會持續增加。
- 只有當視窗超過約 1,365 個交易日（約 5.6 年）時才會被截斷，而且被截掉的會是**最舊**的 receipts。
- 截斷的影響不只是證據不完整：`published_coverage.legacy_native_source_proofs` 是從 receipts 產生 native unit proof，所以 receipts 少了，proof 也會跟著少。

### D3：last_attempt 重複（已確認）

`stock_shared_store._publication_attempt`（L1755-1770）會把 metadata 的 23 個欄位 `deepcopy` 一份到 `last_attempt`。

head 內兩邊位元組相同的部分共 15,032,316 B，占整個 head 的 49%：

| 欄位 | 重複位元組 |
|---|---|
| `source_receipts` | 14,183,660 |
| `diagnostics` | 535,567 |
| `coverage` | 308,277 |

有些欄位兩邊**不相同**，這些不是重複，必須保留：
- 位置：官方行情、TDCC 的 gaps；現金流的 coverage；Order21 的 coverage 與 gaps。
- 原因：retained 和 partial recovery 分支會在建立 last_attempt 之後改動 metadata。

**修法**：寫入 head 時，last_attempt 只保留與 metadata 不同的欄位，並加上 `same_as_metadata: [欄位名...]`。讀取端用 `attempt_view()` 還原成 v1 形狀。

### D10：每次發布都封存一份完整的 30 MB head（已確認）

**根因**：
- `publish()` 每次都把整個 head 正規化後寫出（L5289-5300）。
- `_archive_manifest` 再把整份位元組複製到 `_manifests/<sha>.json`（L5317-5338）。

大於 1 MB 的封存檔共 44 份，合計 664,578,608 B。其中 43 份寫於 10-04 07:08～10-05 07:52（約 25 小時），1 份寫於 09-19。

**修法（建議）**：v2 head 把大型證據外部化，加上 D3。
- `source_receipts`、`diagnostics` 中正規化後大於 64 KiB 的值，改存為內容定址的 `_evidence/<sha256>.json`，head 只放 5 個鍵的參照。
- 封存檔仍然是 head 的完整位元組，所以每份 pinned archive 依然能用 sha256 驗證。
- 沒有變動的證據，不論被幾份封存檔參照，都只存一份。

**在同樣 44 次發布上的反事實量測**（如果當時就用各種格式寫；其中 43 次發生在約 25 小時內）：


| 格式 | 合計 | 減少 |
|---|---|---|
| v1（現況） | 664.6 MB | — |
| 只做 D3 | 339.6 MB | −49% |
| 只外部化 | 50.5 MB | −92.4% |
| **v2（外部化 + D3）** | **43.0 MB**（head 20.0 MB + 20 個證據物件 23.0 MB） | **−93.5%** |
| 逐 route entry 內容定址（Model B）＋ v2 | 25.1 MB | −96.2%，但所有讀取 `datasets[]` 的程式都要改，不建議先做 |

### 不能分開修

只修 U2，把完整證據直接放進 v1 head，head 會從 30.5 MB 變成 **51.8 MB**：
- 對 live 消費端 64 MiB 的釘選上限（caps 報告的 C1、C2），餘裕只剩 1.3 倍。
- 每份封存檔也一樣大。

U2 必須和 v2 外部化同時上線。v2 head 為 0.876 MB，另有 25.25 MB 證據物件（17 個）。

### 消費端相容性（實測，P8 與 P8b）

**升級後的讀取端**：用 `attempt_view` 加 `resolve` 讀 v2，在下列所有情況與讀 v1 的結果完全相同：
- staging 與 live 的單股程式；
- 真實 head 36 條 route，共 72 次呼叫；
- consumer 自己的 8 組單日證明 fixture。

**未升級的讀取端**讀 v2 的結果：

| 情況 | 決策 | 其他輸出 |
|---|---|---|
| 真實 head | 0 個改變 | 70/72 次呼叫的欄位悄悄改變（`evidence_scope` 日期變 None，`source_receipts_count` 變成 5） |
| 單日 fixture（只要有 D3） | `source_unavailable`、`confirmed_no_data`、`pending_release` 全部退化成 `unknown_empty/missing` | — |
| 只外部化、不做 D3 | 0 個改變 | 只有 `source_receipts_count` 錯（32 次） |

**做法**：
1. 讀取端先上線，可同時讀 v1 與 v2；
2. head 的 schema 升為 `stock-data-publication-v2`，讓任何漏改的讀取端大聲失敗，而不是悄悄讀錯；
3. 生產端加一個寫入閘門 `TWSTOCK_UPDATER_HEAD_FORMAT`，預設 v1，等所有讀取端就緒才切換。

### 記憶體與時間（實測，C: 副本）

**每次發布**（模擬 `publish()` + `read_baseline()` 的「解析、深拷貝 3 次、再編碼」）：

| head | 時間 | 峰值 commit 增加 |
|---|---|---|
| v1，30.5 MB | 4.9 s | +416 MB |
| v2 | 0.16 s | +15.7 MB |
| 完整證據直接放 v1 | 9.5 s | +724 MB |

**housekeeping**：`plan_object_prune` 會把所有封存檔解析後同時保留在記憶體（L470-482）。
- 現在 379 份、709.5 MB，峰值 WS 2.24 GB。
- 以 10-04 的速度，每天再增加約 2 GB。
- collect data tool 的 `store_prune` 也是同樣寫法。

### 原型

- 位置：`trees/patched`。新增 `evidence_store.py`，修改 13 個模組；改動用 `out/producer_prototype.diff` 記錄。
- 新增 15 個測試，全部通過。
- 完整測試套件以 test id 和 base 比對，結果見 §6。

---

## 1. U2：`_bounded_evidence` 截斷證據

### 1.1 根因（live 行號）

**`shared_publication.py`**：
- L27-34：`_COMPLETION_METADATA_FIELDS`（25 個欄位，包括 `source_receipts`、`diagnostics`、`errors`、`requested_scope`、`no_data_evidence` 等）。
- L43-65：`_bounded_evidence`，截斷點如下：

| 行號 | 作法 | 結果 |
|---|---|---|
| L45 | `budget=[150000]` | 節點預算 |
| L47 | `budget<0 or depth>8` | 回傳 None |
| L51 | `items()[:64]` | 只留前 64 個鍵 |
| L52 | 非字串鍵、私密鍵 | 跳過 |
| L54 | `key[:128]` | 鍵名截短，可能讓兩個鍵撞名 |
| L57 | `value[:4096]` | 只留前 4096 項 |
| L64 | `value[:1024]` | 字串截短 |
| L65 | 非 JSON 純量 | 變成 None |

- L68-75：`_publication_metadata` 逐欄呼叫，每個欄位有自己的 150,000 預算。
- L767 處理 state metadata，L775 處理 receipt metadata（`publish_available`）。

**下游還有兩處相關**：
- `stock_shared_store._input_fingerprint`（L207-252）是對截斷後的 candidate metadata 算雜湊，所以 `accepted_input_fingerprint` 也只代表截斷後的證據。
- `published_coverage.legacy_native_source_proofs`（L1013 起，`publish()` L4975 呼叫）是從截斷後的 receipts 推出 native proof。

### 1.2 現行 head 的實測

資料：`out/p1_head_inventory.*`、`out/p2_truncation_root_cause.*`。

| route.欄位 | 完整（run canonical） | head | 截斷特徵 |
|---|---|---|---|
| `finmind_cash_flows_statement.source_receipts` | 17 筆，7,972,609 B，196,178 個節點 | 6,104,912 B | 第 150,000 個節點之後全為 null：[13].members[271] 的 4 個葉值；[13].members[272..2019] 共 1,748 項；[14]–[16] 整筆 |
| `finmind_cash_flows_statement.diagnostics` | 78,081 項，9,223,616 B | 4,096 項，484,319 B | `[:4096]` |
| `official_finance_reporting_deadline.source_receipts[].source_scope.company_period_keys` | 5,475、4,460 | 4,096、4,096 | `[:4096]` |
| 其餘 33 條 route | — | 沒有截斷特徵 | — |

**這些完整值目前只存在兩個地方**：
- D: 的 `updater_runtime/runs/20261004T205159Z-ad5ea647/payloads/{12,08}-*.canonical.json`。整個 run 目錄 43 GB，housekeeping 的 `keep_runs=2` 之後會把它刪掉。
- 我在 `E/data/run_ad5ea647/` 的副本，sha256：
  - `12-…cash_flows…canonical.json`：`114b9456…`
  - `08-…deadline…canonical.json`：`d96ea626…`
  - `01-…market_daily…canonical.json`：`afea3c4e…`

### 1.3 修法

**1. 改寫 `_bounded_evidence` 為 `_complete_evidence`**（實作在 `evidence_store.sanitize_evidence`）：
- 用迭代走訪整棵樹，不設任何清單、mapping、字串、深度或節點上限。
- 認證資訊的處理規則不變：同樣的私密鍵與 token／Bearer 遮罩。
- 每次遮罩或移除，都記到 `metadata.evidence_sanitization`：`{schema, redacted_strings, dropped_private_keys:[路徑…]}`。只在有發生時才寫。
- 遇到非 JSON 值或非字串鍵就拋 `EvidenceError`，屬於 ValueError。`publish_available` L817 會把這條 route 標成 `candidate_prepare_failed`，明確延後，不再悄悄變成 null。

**2. `publish_available`**：state metadata 與 receipt 兩份的 sanitization 紀錄要合併，不可以後者覆蓋前者。

**3. 必須和 D10 v2 一起上線**（見 §0）。只修 U2 會讓 head 變成 51.8 MB。

**4. 同類的悄悄移除（附帶發現，未量測）**：`run_finalize._safe_publication_value`（L1113-1144）會悄悄丟掉兩種東西：
- 鍵名符合 `token|secret|password|authorization|api_key|header|body` 的欄位；
- 看起來像絕對路徑的字串（`^[A-Za-z]:\|\\\\|//|/`）。

這些資料會進入 run 的 canonical metadata，之後再被發布。建議用同樣的記帳方式處理。本次沒有量測實際影響。

**5. 既有限制（不屬於 U2）**：Order21 的完整性欄位不會進入 head（L72 的 `route_id != 'finmind_trading_daily_report'`）。這是刻意的設計，Order21 用 partition fingerprint 與實體 proof。

### 1.4 對既有 head 的證據修復（需決定）

**選項 a：等下一次正常收集**
- 修正後的程式會發布完整證據。
- 舊封存檔保留截斷版。封存檔不可變；目前也沒有消費端用這些值做決策，只會出現在診斷輸出。

**選項 b：立即修補，只更新 metadata**
- 做法：用 run `ad5ea647` 的 canonical 檔，再做一次只改 metadata 的發布。
- 先驗證：用舊演算法處理 canonical，結果必須和 head 現值位元組相同（P2 已確認成立）。驗證通過才替換成完整值。
- 寫入 `evidence_repair: {field: {source_ref, source_sha256, bounded_v0_match: true}}`。
- 時效：必須在 housekeeping 刪掉該 run 之前完成，或先保留 canonical 檔（D: 只有那一份）。

---

## 2. D3：last_attempt 重複

### 2.1 根因

**`stock_shared_store._publication_attempt`（L1755-1770）**：
- 會對這 23 個欄位做 `deepcopy`：`coverage`、`gaps`、`errors`、`error`、`reason`、`diagnostics`、`requested_from_date`、`effective_from_date`、`expected_asof`、`requested_to_date`、`requested_scope`、`collection_scope`、`source_receipts`、`snapshot_observation`、`snapshot_complete`、`observed_asof`、`no_data_evidence`、`history_unavailable`、`due_scope`、`pending_release_dates`、`pending_release_evidence`、`source_expected_asof`、`source_resolved_demand`。
- 同一份 candidate metadata 也會整份成為 entry 的 metadata（`lineage = dict(metadata, …)`）。

寫入點：
- `_merge` 的 L3840、L3899-3900、L3915、L4002、L4039-4045、L4051、L4613-4622；
- `_finish_current_native_scope` 的 L3357；
- `_formal_claim_observation` 的 L3456；
- `finalize_order21_metadata` 的 L5403。

### 2.2 實測

head `efbd4844` 的 `last_attempt` 共 15,038,823 B，其中與 metadata 位元組相同的有 15,032,316 B（`out/p1_head_inventory.txt`）。

**不同的欄位（不是重複，必須保留）**：
- 官方行情、TDCC 的 `gaps`（2 B）；
- 現金流的 `coverage`（184 B）；
- Order21 的 `coverage`（393 B）與 `gaps`（87 B）。

**為什麼會不同**：retained snapshot、partial recovery、metadata finalize 等分支，會在 last_attempt 建立之後改寫 entry 的 metadata。所以 last_attempt 記的是「最後一次嘗試」，metadata 記的是「目前發布的資料本體」。兩者只在嘗試被採用時才相同。

### 2.3 修法

**寫入**：`evidence_store.compact_attempt(metadata)`
- 只在寫入 head 時執行（`_head_entries`，v2）。
- `ATTEMPT_FIELDS` 中與 metadata 正規化位元組相同的欄位，從 last_attempt 拿掉，並列進 `last_attempt.same_as_metadata`（排序）。
- 不同的欄位原樣保留。
- 執行兩次結果不變（idempotent）。

**讀取**：`evidence_store.attempt_view(metadata)`
- 還原成 v1 的 last_attempt。
- 如果 `same_as_metadata` 指到 metadata 沒有的欄位，或與 last_attempt 本身的欄位衝突，就拋錯（fail closed）。
- v1 entry 沒有 `same_as_metadata`，直接原樣傳回。

**效果**：

| head | 大小 | last_attempt 總量 |
|---|---|---|
| v1 head + D3 | 15,472,941 B | — |
| v2 head（外部化 + D3） | 876,397 B | 6,978 B，另加 `same_as_metadata` 清單 4,156 B |

### 2.4 消費端：讀 `last_attempt.source_receipts` 當證明的地方

**live／staging 單股 `stock_data_sources.py`**：
- `published_completion` 用 `proof = attempt if Mapping else metadata`（live L402-403／staging L498-499），從 last_attempt 讀 `source_receipts`、`requested_from_date`、`expected_asof`、`coverage`（L462／L558）。
- `published_no_events` 檢查 last_attempt 的旗標（L188-192／L196-200）。
- `_completion_summary` 用 `len(value[key])` 算筆數（L273／L369）。如果值是參照，會算成 5。

**必須的改動**：
- `attempt = attempt_view(metadata)`；
- 對 `source_receipts`、`diagnostics` 呼叫 `resolve(root, …)`；
- 參照的筆數改讀 `ref['items']`。

**staging projection 的私有證明視圖**：
- 位置：`published_single_stock_projection._COMPLETION_CONTEXT_KEYS`（staging L223-232）。
- 問題：缺少 `ATTEMPT_FIELDS` 中的 9 個鍵：`effective_from_date`、`requested_to_date`、`requested_scope`、`collection_scope`、`no_data_evidence`、`due_scope`、`pending_release_evidence`、`source_expected_asof`、`source_resolved_demand`。
- 改法：在挑選鍵之前先套用 `attempt_view`。否則 `attempt_view` 會在私有視圖裡 fail closed。
- 這裡也要與 caps 報告 C6 的 `_completion_entry_view` 協調。

**其他消費端**：

| 程式 | 位置 | 判斷 |
|---|---|---|
| staging `tw_single_stock_three_month_xs_io.py` | L246-257，收集 gaps 時會讀 last_attempt | 改用 `attempt_view` |
| MIX `stock_data_sources.py` | L185（no_events receipts）、L202-206（last_attempt 旗標） | 改用 `attempt_view` 與 `resolve` |
| Top10 | 只讀 entry 的核心欄位 | 不受影響，只需接受 schema v2 |
| collect data tool `publish_audit.py` | L215 只讀 `last_attempt.warnings` | 不受影響 |

**生產端自己的讀取點**（原型已改用 `attempt_view`）：
- `collect_finmind._current_financial_due_reuse`（L511）
- `order21_cache_retirement`（L1191，讀 `coverage[ORDER21_FINGERPRINT_KEY]`）
- `successor_resume`（L196，讀 `coverage`、`gaps`）
- `published_coverage`（L965）

只讀 `warnings`、`retention_reason`、`status` 的地方不受影響，因為這些欄位只存在於 last_attempt，不會被列進 `same_as_metadata`。

**實證**：在 v2 模式下，既有測試有 7 個失敗，原因都是測試直接讀 `x['metadata']['last_attempt'][...]`，等於「未升級的讀取端」。兩個測試檔共 28 處讀取改走 `attempt_view` 之後，v1 與 v2 都通過（§6）。

---

## 3. D10：封存檔重複

### 3.1 根因

| 位置 | 作法 |
|---|---|
| `stock_shared_store.publish()` L5289-5300 | 每次都把整個 head 以 `canonical(document)` 寫出 |
| `_archive_manifest` L5317-5338 | 把整份 head 位元組複製到 `_manifests/<sha>.json` |
| L4923、L5299 | 呼叫 `_archive_manifest`（publish） |
| L5446、L5461-5462 | 呼叫 `_archive_manifest`（finalize） |

每次 route 層級的增量發布都會產生一份完整 head 的封存檔。

44 份封存檔的實測（`out/p5_archives.txt`）：
- 除了第一份，每份都只變動 1 條 route；
- 最後 11 份中，與前一份相同的 entry 位元組占 94.7%（verify `archive_delta_probe`）。

**附帶後果（實測）**：

| 位置 | 問題 | 影響 |
|---|---|---|
| housekeeping `plan_object_prune`（L470-482）；collect tool `store_prune.py` L111-117 | 把所有封存檔解析後同時保留在記憶體 | 2.24 GB，而且每天增加 |
| `_manifest_route_entry`（L378-391） | 每往前一跳就 `json.loads` 一份 30 MB 封存檔，最多 8 跳（L375） | 每條 route 最多約 1.5 s、125 MB 暫存 |
| `_publication_maturity_context`（L3234-3236） | 讀封存的 deadline catalog head 時，超過 16 MiB 直接拋 `deadline_catalog_manifest_unbounded` | 現在 head 已有 30.5 MB。只要財報類 route（9、10、11、12）走 `current_native_scope` 路徑就會失敗。目前 head 沒有這種 entry，所以是潛伏問題 |

### 3.2 方案比較

所有讀取 `_manifests/<sha>.json` 的程式，都依賴「檔案位元組的 sha256 等於檔名」，包括：
- updater 的 9 個模組；
- collect tool 的 6 個模組；
- 單股的 `validate_pinned_context`（live L1890／staging L2161）與 `train_shared` L146／L188。

所以去重複只能有兩種做法：讓 head 本身變小，或在檔案系統層透明地做。

| 方案 | 44 份封存檔合計 | 讀取端要改什麼 | 判定 |
|---|---|---|---|
| A. v2：大型證據外部化 + D3 | 43.0 MB | 只有讀證據的程式（`attempt_view`、`resolve`），加上接受 schema v2 | **建議** |
| B. 逐 route entry 內容定址（head 只放 index） | 47.7 MB（B）；25.1 MB（A + B） | 所有讀 `datasets[]` 的程式，數十處 | 之後再考慮 |
| C. 對 `_manifests` 開 NTFS 壓縮 | 30.5 MB 的封存檔 → 12.8 MB（2.4:1），sha 不變 | 不用改 | 舊封存檔的空間處理（需使用者同意） |
| D. 舊封存檔重新打包（v2 部件 + 讀取時重組） | 已證明 44/44 份可位元組完全重組 | 所有 `_manifests` 讀取端都要改用 resolver | 不建議先做 |

### 3.3 v2 格式與規則（原型 `evidence_store.py`）

**格式**：
- head 頂層 `schema` 改為 `stock-data-publication-v2`。
- 參照只會出現在兩個位置，其他地方都不會有：
  - `entry.metadata.{source_receipts, diagnostics}`
  - `entry.metadata.last_attempt.{source_receipts, diagnostics}`
- 參照的格式是 `{"$evidence":"stock-data-evidence-v1","sha256","size","type":"list|object","items"}`。
- 參照指向 `_evidence/<sha256>.json`，內容正好是 `canonical(value)`。

**門檻 64 KiB**：
- 只外部化這兩個欄位，實測大小與外部化 7 個欄位相同。
- 單日證明（1–10 筆 receipts，約 0.3–4 KB）會留在 head 內，所以未升級讀取端的單日判斷不會因為外部化而改變（P8b）。

**讀取端驗證 `resolve`**（任何一項不符就 fail closed）：
- size 與 sha256；
- 不得有重複鍵，不得有 NaN；
- 重新正規化後必須與原位元組相同；
- type 與 items 必須相符；
- 未傳入 root 時直接拋錯。

**生產端的核心規則**：
- 所有邏輯與指紋都用「邏輯形」，也就是 v1 等價的完整值。
- entry 的 digest（ack 的 `entry_sha256`、published reference 的 digest、`_route_ack` 的 `committed_entry`）只對「儲存形」計算。

這樣的結果：
- 沒有截斷問題的 route，`accepted_input_fingerprint` 不會變，不會因為格式轉換而全部重新合併。
- 舊的 published reference 與 resume pin 都綁在 v1 封存檔上，仍然能驗證。

**寫入點**：`_head_entries(root, entries)`，在 `publish()` 建 document 前（L5289），以及 `finalize_order21_metadata`（L5451）。
- v2：`stored_entry`，也就是外部化加 `compact_attempt`。
- v1：把 v2 的參照展開回完整值。這是回滾路徑，原型有測試。

**生產端要解析證據參照的讀取點**（原型已改用 `evidence_view(root, metadata)`）：

| 位置 | 用途 | 如果不解析 |
|---|---|---|
| `stock_shared_store` L4009 | `_snapshot_sources_strictly_newer(prior)` | 新快照會被誤判為「不是較新」，舊資料被保留 |
| `stock_shared_store` L3585 | `_proved_empty_history_entry` | — |
| `stock_shared_store` L5050 | formal reuse 改用 old entry 的 metadata | 指紋改變 |
| `published_coverage.adapt_legacy_entry` | legacy proof 從 receipts 推導 | — |
| `runner._demand_source_evidence` | 4 個呼叫點 | 因為 `isinstance(receipts, list)` 不成立，receipt 帳本被當成「不存在」，造成多餘的下載。這一處是審查時找到的，既有測試沒有覆蓋；原型已新增測試 |

**schema 檢查點**：
- updater 的 12 處（原型已全部改成接受 v1 或 v2）：
  - `read_manifest` L813
  - `_cas_baseline_snapshot` L550
  - `published_reference` L164、L231
  - `published_coverage` L882
  - `runner` L2763
  - `order21_gc_pins` L138
  - `order21_cache_retirement` L261、L336
  - `order21_lifecycle` L190
  - `order21_physical_reuse` L179
  - `_publication_maturity_context` L3245
- consumers 見 §4。

**GC 與保存**：
- `_evidence/` 與 `_manifests/` 一樣只增不刪。
- 舊的 GC 程式只掃 `_objects/`，所以不會刪到 `_evidence/`；collect tool 舊版也一樣。
- `plan_temp_leftovers` 要把 `_evidence` 納入 `.tmp` 清理（原型已改）。
- 如果之後訂定封存檔保存政策，`_evidence` 也照「head、保留中的封存檔、resume pin 所參照者都保留」的規則做 mark/sweep。可直接用原型的 `referenced_evidence()`。
- `verify` 指令（updater 與 collect tool）要核對 head 參照的每一個證據物件。

**其他要一起移除的讀取上限**：
- L3235 的 16 MiB deadline catalog 上限：原型已移除，sha 檢查保留。
- housekeeping 與 collect tool prune 改成逐份處理封存檔，只保留物件名集合，不要把所有解析結果同時留在記憶體。

### 3.4 實測效果

量測檔：`out/p3_head_variants.txt`、`p4_memory.txt`、`p5_archives.txt`、`p6_archive_reconstruct.txt`、`p7_housekeeping_memory.txt`。

**head 大小**：

| head | 大小 |
|---|---|
| v1（現況） | 30,504,786 |
| 只做 D3 | 15,472,941 |
| v2 | 876,397，另有 17 個物件共 14,599,176 |
| 完整證據 v1 | 51,812,306 |
| 完整證據 v1 + D3 | 26,126,701 |
| 完整證據 v2 | 876,399，另有 17 個物件共 25,252,936 |

**v2 head 還剩什麼**（876 KB）：
- `coverage` 320 KB，其中 block_trade 的 `native_planned_units` 占 264 KB；
- `derivative_filter` 157 KB；
- `resolved_demand` 98 KB。

全部都是完整值，不需要再動。

**round trip**：v2 經 `resolve` 與 `attempt_view` 還原，結果等於完整 v1。36/36 條 route 通過。

**每次發布的記憶體**：v1 為 +416 MB 與 4.9 s；v2 為 +15.7 MB 與 0.16 s。

**消費端的連帶效果**：
- descriptor 是 head 的兩倍，從 61.0 MB 降到約 1.75 MB。
- 64 MiB 釘選上限（C1、C2）的餘裕從 2.2 倍變成 76 倍。
- C6 的 32 KiB 私有證明溢出，從 24 條 route 降到 12 條（`out/p9_c6_overflow.txt`），剩下的仍需要 caps 報告的 C6 修正。

**每天的成長**（依 10-04 的發布頻率）：
- v1 約 660 MB 封存檔（43 份約在 25 小時內寫成），外加 housekeeping 約 2 GB 記憶體。
- v2 約 20 MB head，加上當天有變動的證據物件。每日型 route 的 receipts 每天都會換新，估計約 7.7 MB；現金流有變動時再加約 6 MB。
- 如果要再降，可以把 receipts 按月分塊做內容定址，使每天只新增最新一塊。這是選項，本次沒有原型。

---

## 4. 與既有消費端的相容（讀取端先上線）

| 程式 | 必改 | 行號（live／staging） |
|---|---|---|
| 單股 `stock_data_sources.py` | 接受 v2；`attempt_view`；`resolve(root)`；參照筆數讀 `items` | schema L1516／L1794；completion L402-403、L457、L462、L512／L498-499、L553、L558、L608；no_events L119、L188／L130、L196；summary L273／L369 |
| 單股 projection | 私有證明視圖先套 `attempt_view`；`_COMPLETION_CONTEXT_KEYS` 缺 9 個 `ATTEMPT_FIELDS` 鍵 | live L226、L361-362、L490、L534／staging L223-232、L391-392、L522、L566 |
| 單股 `shared/stock_data_paths.py` | 接受 v2 | L202 |
| 單股 `stock_query_projection.py` | 接受 v2 | L1454 |
| 單股 `tw_single_stock_train_shared.py` | 接受 v2；封存檔讀取不變 | L146、L184／L188、L226 |
| telegram bot | 接受 v2 | L1375／L1517 |
| staging `three_month_xs_io` | `attempt_view` | L246-257 |
| MIX | 接受 v2；`attempt_view`；`resolve` | `stock_data_sources` L185、L202-206、L713；`stock_query_projection` L1394 |
| Top10 | 接受 v2；不讀證據 | `data_reader` L128；`stock_query_projection` L1382 |
| collect data tool | 同步 updater 分支（`updater/`）與 `app/` 複本；`store_status` 與 `verify` 加 `_evidence`；`store_prune` 改成逐份處理 | `app/shared_publication.py` L40-72（同樣的 `_bounded_evidence`）；`app/stock_shared_store.py` L32、L1496；`store_status.py` L15、L89；`order21_gc_pins.py` L123；`store_prune.py` L111-117；`publish_audit.py` L338-356；`official_history_backfill.py` L415 |

**為什麼要升 schema 而不是加欄位**：
- 未升級的證據讀取端會「悄悄」讀錯（`source_receipts_count=5`、單日證明消失，P8 已實測）。
- collect tool 舊程式是第二個 producer。如果讀到含參照的 head，會寫出混合格式的 v1 head。

schema 升為 v2 之後，以上情況都會變成 `invalid_store_manifest`／`schema` 錯誤，可以被看見。

**pinned 封存檔與 lineage**：
- 所有既有封存檔都不改，379 份已逐一驗證 sha 等於檔名（P7）。
- 新的 v2 封存檔一樣是 head 的完整位元組。
- 證據物件以 sha 綁定，只增不刪。
- 所以舊模型 lineage 記錄的 head sha，以及消費端的 pin 驗證流程，都不需要改。

---

## 5. 遷移順序

**M0：保全完整證據（需使用者決定）**
- run `ad5ea647` 的 `payloads/12-…canonical.json` 與 `08-…canonical.json`，是現金流與財報期限完整證據唯一的來源。
- 我已在 C: 留了副本（§1.2 有 sha）。

**M1：讀取端先上線**
- 範圍：單股 live 與 staging、MIX、Top10、telegram、collect tool。
- 內容：同時接受 v1 與 v2，並加上 `attempt_view` 與 `resolve`。
- 這一步不改變 head，沒有風險。可以拿本報告 P8 的升級版比對作為驗收：72 次呼叫加 8 組 fixture 都必須與 v1 結果相同。

**M2：生產端版本上線**
- 內容：U2 的 `_complete_evidence`、`evidence_store`、`_head_entries`、各讀取點、12 處 schema 檢查、移除 16 MiB 上限、housekeeping 逐份處理封存檔。
- 寫入閘門：等 M1 完成後直接設為 v2，與 U2 同時生效。
- 回滾：設回 v1 時，生產端會把參照展開成完整值。此時 head 約 51.8 MB，所以需要 caps 報告的 C1、C2 已上線。

**M3：第一次 v2 發布**
- 這次發布會把 36 個 entry 全部轉成儲存形，內容不變（36/36 round trip）。
- 前一版 v1 head 照常封存，`previous_sha` 可以追溯。
- 建議另外提供 `migrate-head-format` 指令：在 lock 內、以 CAS 做一次只改 metadata 的發布，讓格式轉換與資料更新分開，方便稽核。
- 預期的一次性成本：
  - prepared-merge 快取因為 key 含 old entry 而全部失效；
  - `_accepted_unchanged_ancestor` 跨越 v1、v2 時，簽章不同而退回一般判斷；
  - order35 的 `expected_formal_entry_sha256` 對不上，延後一輪；
  - 現金流與財報期限因為證據變完整，指紋改變，各重新合併一次。
  - 其他 route 的 `accepted_input_fingerprint` 不變，`_can_reuse` 仍然有效。

**M4：證據修補（§1.4 的選項 a 或 b）**

**M5：舊封存檔（664.6 MB 加其餘）**
- 保持原樣，不做任何改寫。
- 如果需要空間，可以對 `_manifests` 開 NTFS 壓縮（2.4 倍、透明，需使用者同意）。
- 封存檔刪除或保存政策另行決定。

---

## 6. 原型與測試（副本）

**改動範圍**：
- `out/producer_prototype.diff`：新增 `evidence_store.py`，修改 13 個模組。
- `out/producer_tests.diff`：
  - 新增 `tests/test_evidence_u2_d3_d10.py`，15 個測試；
  - 更新 1 個斷言了截斷行為的既有測試：`test_publication_bridge_preserves_bounded_proof_not_secrets` 原本是 `len(...) <= 1024`；
  - 兩個測試檔共 28 處 last_attempt 讀取改走 `attempt_view`。
- 套用方式：`scripts/patch_producer.py`。逐段比對、每段只能命中一次，並保留 CRLF。

**新增測試**（v1 與 v2 都通過）：
- **U2**：
  - 超過 4096 項；
  - 沒有 150,000 節點預算；
  - 長字串、寬 mapping、長鍵、40 層深度；
  - 遮罩照舊而且有記錄；
  - 非 JSON 值大聲失敗；
  - bridge 端到端：完整 diagnostics 加上 sanitization 紀錄。
- **D3**：
  - 只保留差異，`attempt_view` 能完全還原，執行兩次結果不變；
  - 壞掉的 `same_as` 會 fail closed。
- **D10**：
  - v2 head 的參照與證據物件 sha 正確，封存檔等於 head 位元組，ack 的 `entry_sha256` 是儲存形的雜湊；
  - 沒變的證據只存一份；
  - 前一版的證據參照會被解析，「是否較新」的判斷正確；
  - 證據物件被竄改或遺失會 fail closed；
  - v2 → v1 回滾後是完整的 inline 值；
  - 預設 v1 時，完整證據直接 inline；
  - `runner._demand_source_evidence` 能解析參照，沒有 root 時拋錯。

**完整套件以 test id 比對**：
- base 在這個環境有 503 failed 與 7 errors，都是既有失敗，大多是 registry 視窗政策測試；兩邊相同。
- 結果檔：`out/compare_*.json`、`junit/`、`logs/`。

| 執行 | passed | failed | error | 與 base 比對 |
|---|---|---|---|---|
| base | 1668 | 503 | 7 | — |
| patched，v1（預設） | 1681 | 505 | 7 | 新增的 15 個全部通過；由通過變失敗的只有 2 個環境因素（見下） |
| patched，v2（門檻 64 KiB） | 1681 | 505 | 7 | 同上 |
| patched，v2，`INLINE_MAX=0`（強迫所有證據都變參照） | 1675 | 511 | 7 | 2 個環境因素，加 6 個「測試直接拿 head 的 `source_receipts` 當 list 比對」 |

`INLINE_MAX=0` 多出的 6 個失敗，都是測試本身直接讀儲存形，等同於未升級的讀取端：
- `test_publication_completion_semantics` 5 個；
- `test_formal_source_reuse_lifecycle` 1 個（該測試前面的資料與 coverage 斷言都通過）。

生產端邏輯本身沒有任何失敗。

**修改過程中的中間結果**：
- 第一輪 v2 有 25 個失敗：漏改了 Order21 的 4 處 schema 檢查，以及測試直接讀 last_attempt。之後補了 round 2、round 3 的修改。
- 中間的紀錄保留在 `logs/pytest_full_patched_*`，不作為結論依據。

**環境因素**：`test_shared_collection_time_and_price_repair` 的兩個測試使用真實的 `Global\…SingleProducer.v1` mutex。執行期間有另一個 session 的 pytest 在跑，所以兩個測試都回報 `SharedPublicationBusy`。在 v1 模式重跑也同樣失敗（`logs/pytest_mutex_v1.txt`），與本修改無關。

---

## 7. 需要使用者決定

1. **schema 與閘門**：是否接受把 head schema 升為 `stock-data-publication-v2`，並依「讀取端先上線 → 生產端寫入閘門切到 v2」的順序部署？
   - 替代做法：先只做外部化、不做 D3（P8b：未升級讀取端的決策 0 改變，只有筆數顯示錯）。下一版再做 D3。
2. **保全完整證據**：run `ad5ea647` 的現金流與財報期限 canonical 檔，是否要在 D: 上保留或另存？之後是立即修補（§1.4 b），還是等下一次收集（a）？
3. **門檻**：64 KiB 是否可以？
   - 更低：head 更小（1 KiB 時為 0.76 MB）。
   - 但會讓單日證明也變成參照，未升級讀取端在單日情況下會受影響。
4. **舊封存檔**：是否對 `_manifests` 開 NTFS 壓縮（2.4 倍）？要不要訂定封存檔與 `_evidence` 的保存政策？目前兩者都只增不刪。
5. **附帶項目**：是否一起處理 `run_finalize._safe_publication_value` 的悄悄移除，改為有記帳？以及 housekeeping 與 collect tool prune 同時載入全部封存檔的記憶體問題（2.24 GB 且每天增加）？

---

## 8. 檔案（`E/` = `scratchpad/updater_inv_20261005/evidence/`）

**量測腳本**（`scripts/`）：

| 腳本 | 內容 |
|---|---|
| `p1_head_inventory.py` | 截斷特徵、重複位元組 |
| `p2_truncation_root_cause.py` | 用 run canonical 重現截斷 |
| `p3_head_variants.py` | 各格式的大小與 round trip |
| `p4_memory.py` | 每次發布的記憶體 |
| `p5_archives.py` | 44 份封存檔的反事實量測 |
| `p6_archive_reconstruct.py` | 44/44 位元組重組 |
| `p7_housekeeping_memory.py` | 全部封存檔同時解析的記憶體 |
| `p8_consumer_compat.py`、`p8b_ext_only.py` | 消費端相容性 |

輔助：`evidence_store.py`（量測用模組）、`memutil.py`。

**原型與測試**：
- `scripts/patch_producer.py`
- `scripts/test_evidence_u2_d3_d10.py`
- `scripts/run_tests.sh`、`scripts/compare_junit.py`
- `trees/base`、`trees/patched`

**輸出**：`out/*.txt`、`out/*.json`、`out/variants/`（各格式的 head 與證據物件）、`out/producer_prototype.diff`、`out/producer_tests.diff`、`logs/`、`junit/`。

**資料副本**：`data/head_current.json`（efbd4844）、`data/run_ad5ea647/`（三個 canonical 檔）、`data/ntfs_test/`（NTFS 壓縮實驗，已設定壓縮屬性的副本）。

**未使用的中間產物**：`_trash_staging/`（扁平佈局的測試樹，以及 7 鍵草稿版的證據物件；保留，沒有刪除）。

**安靜暫存根**：`C:/Users/USER/claude_quiet_evidence`，已整個移到 `three_month_impl/_trash/claude_quiet_evidence_172323`，沒有刪除。
