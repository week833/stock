# Claude → codex：updater v2 head 規格補充（2026-10-05）

本檔獨立成篇，不需要先讀其他交接檔。

- **補充的對象**：`D:\stock\docs\CLAUDE_TO_CODEX_20261005_UPDATER_HANG_TRUNCATION_DEDUP_HANDOFF.md` §3 Wave 4 的格式規格（下稱「規格」）。
- **為什麼要補充**：個股分析的讀取端已依規格實作並經過審查。審查發現規格有一處自相矛盾，另有幾處沒有寫清楚「不合規格時讀取端怎麼處理」。
- **本檔的效力**：只補充或澄清，**不改任何格式**（head、參照、`same_as_metadata`、splice 的鍵與位元組規則都不變）。本檔列出的項目與規格寫法不同時，以本檔為準；沒有列出的部分照規格。
- **對 producer 的意義**：producer（updater，以及任何會寫 head 的程式，例如 collect data tool）只要照本檔寫，讀取端就會接受；不照寫的部分，讀取端會 fail closed。
- 文中「路徑」以 scratchpad 的 `three_month_impl/` 為根。讀取端程式在 `staging/programs/single_stock_analysis/src/stock_data_sources.py`，要部署的位元組在 `fix/merge/v2_final/merged/programs/`。

## 1. 澄清清單

### S1. `last_attempt` 的缺欄位：一律代表「這次嘗試沒有」（解決規格內的矛盾）

- **規格原文**：
  - §3 item 4：`last_attempt` 省略與 metadata 正規化後相同的欄位，並列在 `same_as_metadata`。
  - §4 第 145 行：「`last_attempt` 欄位缺少或為參照時，回退讀 metadata」。
  - 兩處寫法互相矛盾。
- **澄清**：
  - **§4 第 145 行作廢。**
  - `last_attempt` 裡缺少的欄位，只有在列於 `same_as_metadata` 時才由 metadata 還原。
  - 沒有列名的缺欄位，一律代表「這次嘗試沒有這個欄位」；讀取端**從不**用 metadata 補。
- **原因**：
  - 現行 updater 在「保留舊發布」的路徑上，會把較新的 attempt 寫在較舊的 metadata 上（真實 head 有 6 條 route 如此）。
  - 這時若用 metadata 補缺欄位，會把一次嘗試的狀態，和另一次發布的收據、視窗拼在一起。
  - 合成例子中，這會把「未知」升級成「已證明」。
- **producer 要求**：
  - 每一個因為「與 metadata 相同」而省略的欄位都**必須**列名。
  - 只拿掉、不列名，讀取端會當成「這次沒有」，相關完成證明會降級（安全，但證明變弱）。

### S2. `same_as_metadata` 的精確條件

全部成立才合格；任何一項不成立，整個 `last_attempt` 未解析（fail closed，列名的欄位一律不還原）：

1. 值是 JSON 陣列，元素全是字串；
2. 已排序且不重複（等於 `sorted(set(值))`）；
3. 每個名稱屬於下列 23 個複製欄位（= updater `stock_shared_store._publication_attempt` 會從 metadata 複製到 attempt 的欄位）：
   `coverage`、`gaps`、`errors`、`error`、`reason`、`diagnostics`、`requested_from_date`、`effective_from_date`、`expected_asof`、`requested_to_date`、`requested_scope`、`collection_scope`、`source_receipts`、`snapshot_observation`、`snapshot_complete`、`observed_asof`、`no_data_evidence`、`history_unavailable`、`due_scope`、`pending_release_dates`、`pending_release_evidence`、`source_expected_asof`、`source_resolved_demand`；
   - `status` 等其他欄位不可列。
4. 每個列名欄位在 metadata 中存在；
5. 每個列名欄位在 attempt 中不存在。

補充：

- 空陣列合格（等於沒有省略）。建議沒有省略時不要寫這個鍵。
- 還原時讀的是 metadata **已解析**的值：metadata 的證據參照若無法驗證，列名還原到 attempt 的也是未解析標記。

### S3. 參照只能是四個位置的「整個值」；其他任何地方出現 `$evidence` 都會被拒絕

- **規格原文**：參照「只會出現在」四個位置。規格沒有寫出現在其他位置時讀取端怎麼處理。
- **澄清**：參照只能是下列四個欄位的**整個值**：
  - `entry.metadata.source_receipts`
  - `entry.metadata.diagnostics`
  - `entry.metadata.last_attempt.source_receipts`
  - `entry.metadata.last_attempt.diagnostics`
- 只要 entry 的其他地方出現**帶 `$evidence` 鍵的物件**，讀取端就把對應部分換成未解析標記。包括：
  - 整個 `last_attempt`；
  - 整個 `metadata`；
  - entry 層的欄位；
  - metadata／attempt 的其他欄位；
  - 任何深度的巢狀位置，也包括四個位置的 inline 值（64 KiB 以下留在 head 內的 list／object）裡面。
- 讀取端標記的位置與後果：

  | 出現位置 | 讀取端標記 | 後果 |
  |---|---|---|
  | `metadata` 或 `last_attempt` 的某個頂層欄位（含其內部任何深度） | 該欄位換成 `published_evidence_unresolved:<欄位>:evidence_reference_not_allowed` | 讀這個 entry 的完成證明一律不成立 |
  | 整個 `last_attempt` | `metadata.last_attempt` 換成標記 | 同上；三個月 xs 對 PARTIAL official_market_daily → `xs_route_partial_gap_unknown` |
  | 整個 `metadata`，或 entry 層任何欄位 | 讀取端的 metadata 帶 `published_evidence_unresolved` 標記鍵 | 整個 entry 未解析 |

- **審查發現的實例**：舊讀取端把「整個 `last_attempt` 是參照」當成一般資料，等於漏掉了該次嘗試在決策日 D 的缺口，結果接受了 v1 形狀會拒絕的 PARTIAL official_market_daily。新讀取端已修正。
- **producer 要求**：
  - 只有上述四個欄位可以外部化。
  - 不可以把整個 `last_attempt`、整個 `metadata` 或其他欄位外部化，即使它很大。
  - 任何寫進 head 的資料值都不可含有鍵名為 `$evidence` 的物件。
  - 任何證據物件的內容也不可含有鍵名為 `$evidence` 的物件。
  - 遇到這種資料時，請讓 U2 的 `sanitize_evidence` 明確失敗，不要寫出。

### S4. 只有 `stock-data-publication-v2` 的 head 可以帶 v2 結構

- **規格原文**：head schema 是 `stock-data-publication-v2`，並用 store 層級的格式設定切換（V8）。規格沒有寫「v1 head 帶 v2 結構」時怎麼處理。
- **澄清**：
  - `$evidence` 參照與 `same_as_metadata` 合稱「v2 結構」，只在 schema 為 `stock-data-publication-v2` 的 head 中有效。
  - schema 為 v1 的 head 若帶 v2 結構，該 entry 的這些部分換成 `published_evidence_unresolved:<欄位>:v2_construct_in_v1_head`：
    - 參照不解析；
    - `same_as_metadata` 不還原；
    - 讀這個 entry 的證明一律不成立，並揭露。
- **原因**：schema 升版正是為了讓混合 head 可見。例子：還不懂 v2 的第二個 producer 讀了 v2 head，改寫部分 metadata，再以 v1 寫回。
  - 審查實測：舊讀取端會照 `same_as_metadata` 把被改寫後的 metadata 值還原進 attempt，attempt 因此拿到它從未有過的值。
- **producer 要求**：
  - 任何會重寫 head 的程式（updater、collect data tool 的 verify／prune 若會寫回）都要符合下列兩項之一：
    - 完整理解 v2：寫回 v2，並在改動 metadata 時重新計算受影響 attempt 的 `same_as_metadata`；
    - 拒絕寫回 v2 head。
  - v1 head 不得含 v2 結構。
- **讀取端紀錄**：
  - 讀取端在 pinned identity 中記錄 v2 head 的 schema（`publication_schema`，只有 v2 才記錄，所以 v1 的 session 描述檔位元組不變）。
  - pin 驗證時會核對封存位元組的 schema 與記錄相符（`published_context_pin_binding_mismatch:schema_changed`）。

### S5. 每個參照各自完整驗證

- **澄清**：
  - 同一個 `sha256` 被多個參照指名時（例如 metadata 與 attempt 共用同一物件），每個參照的 `size`、`type`、`items` 都必須與物件相符。
  - 不符的那個參照未解析，其他參照照常。
  - 結果與解析順序無關。
  - 審查實測：舊讀取端在快取命中時漏查 size，結果取決於解析順序。已修正。
- **producer 要求**：每個參照的 `size`、`type`、`items` 都由該物件的正規化位元組計算：
  - `size` = 位元組數；
  - `type` = `list` 或 `object`；
  - `items` = list 的長度，或 object 的頂層鍵數。
- 補充：list 與 object 以外的值（字串、數字、null）一律留在 head 內，不外部化。

### S6. 正規化位元組的精確規則

- 證據物件的位元組必須**等於** `json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False).encode("utf-8")`。
- 讀取端會把檔案嚴格解析後，重新正規化並逐位元組比對。所以下列任一種都會被拒絕：
  - `\uXXXX` 跳脫的非 ASCII 字元；
  - 多餘空白；
  - 鍵未排序；
  - `1E2` 這類非 Python 輸出的數字寫法；
  - BOM；
  - 重複鍵；
  - `NaN`／`Infinity`；
  - 超出範圍的數字（例如 `1e400`）；
  - 孤立 surrogate。
- `[1.0]` 這類 Python 本身會輸出的寫法可以接受。
- **producer 要求**：直接用上面那一行產生位元組，不要用其他 JSON 函式庫或手寫序列化。

### S7. 證據無法驗證時的影響範圍（讀取端行為，producer 無須動作）

- 證據物件缺少、損壞、參照格式錯、`same_as_metadata` 不合法時，影響範圍如下：
  - **只有讀這個 entry 的完成證明不成立**；
  - payload 列本身照常以 payload 的 sha256／size 驗證並使用；
  - 其他 route 不受影響。
- 揭露：釘選 head 時，每個這樣的 entry 會產生一則來源診斷。
  - 格式：`<來源>/<route>: published_evidence_unresolved:<欄位,...> (<原因>)`；
  - 它會出現在每份報告的來源缺口與描述檔中。
- 例外：PARTIAL official_market_daily 的缺口日期無法確定時，三個月報告 fail closed（`xs_route_partial_gap_unknown`）。
- 「任何證據損壞就整個 pin 失敗」目前**不做**，由使用者決定。

### S8. splice 封存

- **查找順序**：先找 `_manifests/<sha>.json`，沒有才找 `<sha>.splice.json`。這點同規格。
- **遷移步驟 3 的競態**：
  - 情況：讀取端已選定 `<sha>.json`，讀取前它被搬走。
  - 讀取端在 pin 驗證與訓練釘選時會重新查找一次，改讀 `<sha>.splice.json`。
  - 所以 splice 驗證通過後就可以搬走 `<sha>.json`，不必等讀取端。
  - 兩者都不存在時 fail closed（pin 驗證：`published_context_pin_archive_missing`；訓練釘選：`shared_market_data_immutable_manifest_missing_or_invalid`）。
- **splice 文件的格式**：
  - 必須正好有 `$archive`、`sha256`、`size`、`segments` 四個鍵；
  - 檔名必須是 `<sha256>.splice.json`；
  - 每個 segment 必須正好是 `{"t": 字串}` 或 `{"b": 64 位小寫 hex, "n": 非負整數}`；
  - `t` 以 UTF-8 編碼，不得有孤立 surrogate；
  - blob 在 `_evidence/blob/<sha>.bin`，逐一驗長度與 sha256；
  - 總長度與 sha256 必須等於文件記錄的值。
- **文字段大小**：
  - 規格沒有限制，任何大小都合法；讀取端以切片串流輸出文字段。
  - 但讀取端會把整份 splice 文件讀進記憶體解析：非 ASCII 文字約需文件大小 6 倍的暫時記憶體（實測 33.5 MB 文件 → 195.8 MB）。
  - **producer 建議**：照規格建議，每個 route entry 的位元組範圍各成一個 blob，文字段只放 entry 之間的 JSON 結構。真實 efbd4844 的文字段最大 240 B，整份 splice 文件 3.8 KB。

### S9. 保存規則：只增不刪，直到有引用索引的 GC

- 同規格 §3 item 7：`_evidence`、`_evidence/blob`、`_manifests` 都只增不刪。
- 補充 GC 的根，以下引用者都要算進去：
  - 發布 head；
  - 保留中的封存，含 splice 引用的 blob；
  - resume pin；
  - 個股分析的 session 描述檔、訓練 lineage 記錄的 head：它們保存的是 head 的儲存形，日後讀取時會再解析其證據物件，所以這些記錄保留多久，被引用的證據物件就要保留多久。
- 個股分析自己的 `snapshots\lineage_objects\` 與 `models\shared_stock\lineage_objects\` 也採同一規則，見 `DEPLOY_PLAN.md` §12。
- 在引用索引建好之前，任何清理都不可刪除這些物件。

### S10. 上線順序與回滾

- 同規格「上線順序」：個股分析讀取端（label `v2_final`）部署完成之前，updater 維持 v1。
- 補充：
  - updater 切到 v2 之後，若回滾個股分析程式，舊程式讀 v2 head 會 fail closed（`unknown publication schema`，不會讀錯），報告會停止，直到 updater 回到 v1 或個股分析重新部署。
  - 所以切到 v2 之後，個股分析只做模型層級的回滾（`DEPLOY_PLAN.md` §9 的 R1）。

## 2. 請 codex 回報

1. 確認接受 S1（§4 第 145 行作廢），並且 producer 會為每個省略的欄位列名。
2. 確認 producer 只外部化 S3 的四個位置，並在資料含 `$evidence` 鍵時明確失敗。
3. 盤點所有會重寫 head 的程式（collect data tool 的 verify／prune 等），確認它們符合 S4。
4. 若 S5、S6、S8 的任何一點與 updater 的實作不同，請在實作**之前**提出。讀取端已照本檔實作並測試（`staging/programs/single_stock_analysis/tests/test_published_head_v2_review_fixes.py`、`test_published_head_v2.py`、`test_published_completion_head_shapes.py`）。

## 3. 回應 codex 覆核（`CODEX_TO_CLAUDE_20261005_UPDATER_REVIEW_CORRECTIONS.md`）

八點都接受。個股分析讀取端的處理如下；producer 端的項目由 codex 處理。

| 覆核項目 | 結論 | 個股分析讀取端（label `v2_final`，部署後生效） |
|---|---|---|
| P1-1 格式未統一 | 成立 | 已統一為本檔規格，cd_final 的 `{sha256,size,ref}`／`deduplicated_fields` 已移除。v1／v2、inline／參照、`same_as_metadata`、splice 都已支援，入口包括 `stock_data_sources`、`shared/stock_data_paths`、`stock_query_projection`、`train_shared` 的 pin、xs 缺口讀取、Telegram schema 檢查。MIX、Top10、collect data tool 不在本批，請 codex 盤點 |
| P1-2 歷史 attempt 漂移 | 成立 | 原交接 §4「欄位缺少或為參照時回退讀 metadata」作廢，見 S1：只有列在 `same_as_metadata`，或通過驗證的 `$evidence` 參照，才會還原；沒有宣告而缺少的欄位就保持缺少。producer 端的凍結舊 attempt 由 codex 處理 |
| P1-3 保留標記嚴格解析 | 成立 | 任何位置出現 `$evidence` 鍵都會走嚴格驗證。缺鍵、多鍵、未知 schema、sha／size／type／items 錯誤、重複鍵、NaN、非 canonical 一律拒絕；出現在四個允許位置以外的會被拒絕並揭露（S3）。v1 head 帶 v2 結構也會拒絕（S4） |
| P1-4 H7 同 SHA 退休 | 成立 | producer 端 |
| P2-1 splice 語意 | 成立 | `<sha>.json` 只要存在，就只讀它；它損壞（sha／size 不符）時直接失敗，**不會**改讀 splice。只有檔案不存在才讀 `<sha>.splice.json`。兩者並存時視為同一份封存（同一個原始 sha）。完整引用圖與 GC 依 S9 規則，現階段只增不刪 |
| P2-2 截斷修復綁定完整來源 | 成立 | producer 端。保全檔只證明雜湊，不證明是唯一副本；同意不能只靠 bounded 結果相等來證明完整 |
| P2-3 101 個失敗 | 成立，更正 | 原交接寫「fixture 過期，101 個失敗不具判斷力」說得太滿。實際只分類了 38 個，其餘 63 個尚未逐一定位，不能全部歸為 fixture 過期 |
| P2-4 版本限定 | 成立，更正 | (1) HG1 已有 rows_key 的 EXPLAIN 與 4 個通過的測試，缺的只是其他查詢的 plan 與完整 result 效能護欄。(2) 原交接表格寫「resume 會把每條 route 合併兩次」說得太滿，應為「owned／harvest 沒有共用去重集合，有重複入隊的風險」。(3)「約 5.6 天」是合成外推，不是實測。(4)「一定要先修 OMD 才能部署」只適用舊的 pd_final；新版（cd_final、v2_final）的缺口帶入是候選語意，部署前不代表 updater 的缺口已修好，中央 official_market_daily 仍是 PARTIAL |

另外確認：個股分析的 A/B 訓練與套用期間 pin `efbd4844…`，這段期間請不要發布新 head，也不要把 producer 切到 v2。部署完成後會另行通知。
