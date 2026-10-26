# Claude → codex 交接：TWStockUpdater 卡住、證據截斷、重複資料（2026-10-05）

這是一份獨立交接檔，不接續任何舊交接檔。

## 0. 先讀這裡

- 使用者 10-05 的決定：
  1. **更新程式的修正由 codex 做。** Claude 只做唯讀調查，沒有修改 D:\TWStockUpdater 的任何檔案。
  2. **不得有讀取上限。** 證據不得被靜默截斷。
  3. **確定是重複的資料一律移除。** 包含舊版 head 封存（`_manifests`），採**去重儲存**，不是只做壓縮。
- 詳細證據與行號在附件資料夾 `D:\stock\docs\claude_20261005_updater_investigation\`：
  - `HANG_GAP_DETAILS.md`：卡住與 09-30 缺口；
  - `U2_D3_D10_PRODUCER_FIXES.md`：截斷與重複的設計與原型；
  - `VERIFY_AND_PLAN_DETAILS.md`：反向驗證與排序後的計畫。
  - 原型程式與量測在 Claude 的 scratchpad：`...\scratchpad\updater_inv_20261005\`（evidence\src_copy、evidence\scripts、hang_gap\scripts）。
- 行號以 10-05 15:48 部署後的 live 檔為準，附件裡標註了 as-run 與 live 的差異。

## 1. 目前狀態

- **更新程式：** 10-05 07:51 從選單啟動的 full-backfill，07:52 發布 head `efbd4844` 後就不再有進展。14:45 由 Claude 依使用者指示關閉（PID 57376/10424/27124/10036）。目前沒有任何 updater 程序。
- **15:45～15:48：** 有人部署了 hotfix（`runtime\staging\formal-reuse-hotfix-20261005`），內含 rows_key 索引。
- **完整證據已保全：** 目前只有 run `20261004T205159Z-ad5ea647` 存著完整的現金流與申報期限證據，已複製到 `D:\TWStockData\preserved_evidence_20261005\`：
  - `12-finmind_cash_flows_statement.canonical.json`，sha `114b9456…`；
  - `08-official_finance_reporting_deadline.canonical.json`，sha `d96ea626…`。
- **在修正部署前，請不要做下列事情：**
  - 從選單執行 4（full-backfill）或 R（resume）；
  - 用 09-11 或更早的起始日執行。

  原因：resume 會把每條 route 合併兩次（V4）；full window 會把 09-30 的缺口標記重新寫回去（V3）。

## 2. 根因

| 代號 | 問題 | 根因 | 證據 |
|---|---|---|---|
| HG1 | 10-05 兩次 full-backfill 都卡住 | `_CoverageRows.result()` 的 NOT EXISTS 子查詢只用 `a.k=m.k`，但主鍵是 (side,k)，沒有 k 的索引，所以變成 O(M×R)。估算 price_adj 的 3.07M 列需要約 5.6 天 | 15:45 部署的 rows_key 索引已解決這一項，但還沒有 EXPLAIN 與效能測試，見 §3 Wave 1 |
| HG2 | 一條慢合併凍結整個 run 與所有讀 head 的人 | `recover_route_publication` 每條 route 都同步 `drain()`；合併在全機唯一的 mutex 內進行；`read_head` 也拿同一個 mutex，而且只重試 3 次 | 附件 HG2 |
| HG5／V4 | resume 會把每條 route 合併兩次 | 附件 V4 | official_market_daily 兩次儲存了同一份 1.29 GB payload（c864cf08） |
| G | official_market_daily 一直是 PARTIAL，帶著 09-30 缺口 | 09-30 的資料其實已在物件裡（2,757 列）。但 09-09 有 2,717 列、09-11 有 2,391 列舊格式資料（沒有收集時間，有 code/session 等欄位），每次合併都觸發 `incomplete_replacement_retained`，使 `current_scope_complete=false`，09-30 的缺口因此永遠不會被清除（`stock_shared_store` 4447-4454、4711-4743、4761、4785） | 附件 G；10-04 的完整候選 c864cf08 卡在 `harvest_pending` |
| U2 | 證據被靜默截斷 | `shared_publication._bounded_evidence` 把清單截到 4096 項、mapping 截到 64 鍵、字串截到 1024 字元、每欄最多 15 萬節點；超過的部分直接變成 null，也沒有標記 | head 的現金流 source_receipts 第 14～16 筆是 null，總共 1,765 個 null；diagnostics 只留下 5.2%；deadline 的 company_period_keys 也被截斷。用完整值重跑舊函式，結果與 head 位元組完全相同 |
| D3 | last_attempt 重複 metadata | `_publication_attempt` 用 deepcopy 複製 | 15,032,316 B，佔 head 的 49.3%，位元組完全相同；gaps 與 coverage 有少數真的不同，要保留 |
| D10 | 每次發布都封存一份 28～30 MB 的完整 head | 每次只有一條 route 改變 | 10-04 起 44 份共 664.6 MB；最近 11 份中 94.7% 與前一份相同 |
| R1 | 16 MiB 讀取上限 | `_publication_maturity_context`（約 L3235、L3245）讀取封存的 deadline catalog／head 時有 16 MiB 上限 | 換日後很可能觸發 |
| R2 | housekeeping 與 collect tool prune 記憶體隨封存份數增加 | 所有封存同時解析並留在記憶體 | 附件 V12 |
| V5 | Windows 上有讀者開著 head 時，替換 head 會失敗（WinError 5） | 發布沒有重試，並留下孤兒封存 | 附件 V5 |
| V10 | updater 測試基準已失效 | 10-05 部署後 fixture 過期，101 個失敗不具判斷力 | 先修基準，再做下面的修正 |

## 3. 要做的修正（依序）

### Wave 1：hotfix，不改 v1 格式，consumers 不受影響

- **R1：** 移除 16 MiB 上限，保留 sha 檢查。
- **R2：** 封存檔逐份串流處理，只保留物件名集合。
- **R3（V5）：** head 被鎖時重試，不留孤兒封存。
- **HG1 測試：**
  - `_CoverageRows.result()` 的每個查詢做 EXPLAIN QUERY PLAN，斷言關聯子查詢內不會 SCAN rows；
  - 40k 列需在 2 秒內完成（修正前是 81 秒）。
- **H3：** 心跳加選單 log 檔。H2 先只做心跳，不要做會讓 route 延後的時間上限。
- **H6：** 接手未完成的 run 資料夾時，必須明確確認，不能靜默沿用（07:51 那次就是靜默接手了 04:49 的 run）。
- **H7：** 跳過已被取代的已存候選。
- **V10：** 先修好測試基準，之後才用「改前與改後失敗相同」來判斷有沒有退步。

### Wave 2：缺口與舊格式列

- **G2（修正版）：** 原型 G2 有資料遺失（V2）：09-09 興櫃有 345 個 open 值被改掉卻沒有紀錄；sidecar 放在 `_objects` 會被 housekeeping 刪掉。修正版要求：
  - 每一個被改動的值都要記錄；
  - sidecar 放在只增不刪的 `_evidence`，並沿用其參照。
- **G1：** 依市場或單位逐一清除缺口。
- **G3：** 尚未發布的視為 pending，不算缺口。
- **G4（新）：** 被宣告為缺口的單位，不能被 coverage 重用當成已涵蓋。V3 指出，否則 09-30 可能永遠不會被重抓。
- **修復執行：** 部署後在 13:30 前跑一次 incremental，再用唯讀方式確認 official_market_daily 回到 SUCCESS、09-30 缺口已清除。
  - 注意 V3：真實執行會先收割已存的 full-window 候選；收割前要確認不會再寫回 PARTIAL。

### Wave 3：重設計 H4、H5、H2

- 不要照原設計做 H4、H5（V6、V7）：
  - 把合併移到 mutex 外，會讓 housekeeping 刪掉正在進行的合併；
  - 把 mutex 改名，會破壞 collect tool 的閒置檢查；
  - 拿掉 drain，會用到 recovery 發布前的 coverage。
- 請重新設計，使單一慢合併不會凍結整個 run 與讀者。

### Wave 4：U2、D3、D10，以及 v2 head

**格式規格（請照此實作。個股分析的讀取端已依同一規格開發，格式一改兩邊就不相容）：**

1. **head schema：** `stock-data-publication-v2`。用 store 層級的格式設定切換，不要用每個程序的環境變數（V8）。
2. **證據參照**只會出現在下列位置：
   - `entry.metadata.source_receipts`
   - `entry.metadata.diagnostics`
   - `entry.metadata.last_attempt.source_receipts`
   - `entry.metadata.last_attempt.diagnostics`

   參照的格式：
   ```json
   {"$evidence": "stock-data-evidence-v1", "sha256": "<hex>", "size": <int>, "type": "list|object", "items": <int>}
   ```
   - 指向 `_evidence/<sha256>.json`，檔案內容正好是 `canonical(value)`，也就是 `json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False)` 的 UTF-8 位元組。
   - 64 KiB 以下的值留在 head 內。
   - 讀取端驗證 size、sha256、嚴格 JSON（不得有重複鍵與 NaN）、重新正規化後位元組相同、type 與 items；任何一項不符就 fail closed。
3. **U2：**
   - 以不設上限的 `sanitize_evidence` 取代 `_bounded_evidence`。保留憑證遮蔽，但每一次遮蔽或丟棄都記錄在 `metadata.evidence_sanitization`。
   - 非 JSON 值要明確失敗，不能變成 null。
   - `run_finalize._safe_publication_value`（L1113-1144）比照辦理。
4. **D3：**
   - `last_attempt` 省略與 metadata 正規化後相同的欄位，並列在 `last_attempt.same_as_metadata`（排序過的鍵名陣列）。
   - 確實不同的欄位保留。
   - 讀取端用 `attempt_view` 還原；marker 不合法就 fail closed。
5. **D10：** 新的封存一律是該 head 的原始位元組，檔名 = sha256。v2 head 約 0.9 MB，大型證據只存一份在 `_evidence`。
6. **既有的舊 v1 封存，依使用者決定改成去重儲存。** 格式 `stock-data-archive-splice-v1`：
   - **路徑：** `_manifests/<sha256>.splice.json`，內容：
     ```json
     {"$archive": "stock-data-archive-splice-v1", "sha256": "<原封存 sha>", "size": <原位元組數>,
      "segments": [{"t": "<原文字片段>"}, {"b": "<blob sha256>", "n": <位元組數>}, ...]}
     ```
   - **還原：** 依序串接所有 segment 的原始位元組。`t` 用 UTF-8 編碼；`b` 讀取 `_evidence/blob/<sha>.bin` 的原始位元組，並驗證該 blob 的 sha 與長度。還原結果的總長度與 sha256 必須等於 `size` 與 `sha256`。
   - **切分方式（建議）：** 每個 route entry 的 JSON 值位元組範圍各自成為一個 blob。每次發布通常只改一條 route，所以重複的 entry 只會存一份。
   - **遷移步驟：**
     1. 寫入 splice 與 blob；
     2. 串流還原並驗證 sha；
     3. 通過後把原始 `<sha>.json` **搬到 `D:\_待刪除_<日期>\`，不要刪除**。
     - 遷移期間兩種形式並存。讀取端先找 `<sha>.json`，找不到才找 `<sha>.splice.json`。
   - **需要支援 splice 的讀取端：**
     - updater：pin、housekeeping、maturity context、`order21_gc_pins` 等所有讀 `_manifests` 的地方；
     - collect data tool：verify、prune；
     - 個股分析：Claude 這次部署會一併支援；
     - MIX、Top10：請 codex 盤點並修改。
7. **保存規則：** `_evidence` 與 `_manifests` 都只增不刪。若日後訂定保存政策，要依「head、保留中的封存、resume pin 所參照的物件都保留」做 mark/sweep。`verify` 指令必須核對每個參照。

**上線順序：**
1. 所有讀取端先支援 v1 與 v2、splice（個股分析這次部署就會支援）。
2. producer 寫出完整證據到 `_evidence`，但 v1 head 內的值先維持不變（U2a）。
3. 切換到 v2，並用一次只改 metadata 的遷移發布完成轉換。
4. 遷移舊封存（splice）。
5. 用 §1 的保全檔修復現金流與 deadline 的證據，並記錄 `evidence_repair` 來源。

## 4. 與個股分析部署的關係

- Claude 今晚會部署個股分析的新版（三個月跨股排名模型、讀取上限全部移除、報告去重等），pin 的是目前的 head `efbd4844`。
  - 新版個股分析已能正確處理 09-30 的缺口宣告：決策日前最多 3 天，從前一交易日帶入上市櫃別並揭露。所以不必等 updater 修復。
- **部署與 A/B 訓練期間（約 4 小時），請不要發布新 head。** Claude 開工前會再通知。
- 新版個股分析讀取端會支援：
  - v1 與 v2 head；
  - `$evidence` 參照解析與驗證；
  - `same_as_metadata`；
  - splice 封存；
  - `last_attempt` 欄位缺少或為參照時，回退讀 metadata。
- 個股分析不再有 64 MiB 的 pin 上限，所以 head 變大或內嵌完整證據，都不會讓它失敗。

## 5. 需要 codex 回報

- 每個 Wave 的變更檔清單、測試結果（先修好基準）、部署時間。
- 若 §3 的格式規格必須調整，請在實作**之前**提出，因為讀取端是照這份規格寫的。
- 修復執行後的唯讀檢查結果：official_market_daily 的狀態、09-30 缺口、現金流證據沒有 null。
