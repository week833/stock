# Claude → codex 交接：head cccbca1f 的 PARTIAL 與 10-05 資料不完整（2026-10-06）

這是一份獨立交接檔。調查全程只讀 D:，沒有修改 updater 或資料。詳細證據放在 `D:\stock\docs\claude_20261006_head_partial_investigation\`：
- `HEAD_CCCBCA1F_PARTIAL_SEMANTICS.md`：updater 的語意與根因；
- `HEAD_PARTIAL_DATA_FINDINGS.md`：新舊物件的資料比對。

## 0. 結論

- **head 狀態：** 2026-10-05 22:05 啟動的 `resume`（續跑 run `20261004T205159Z-ad5ea647`）在 10-06 01:23 發布 head `cccbca1f…`，之後因 `SharedPublicationBusy` 中止，order-21 階段沒有執行。
- **10-05 資料不完整：**
  - 這個 head 的 finmind_price_adj 只有 540 檔有 10-05 的列，前一個交易日是 2,819 檔；2330、2317、0050 都沒有。
  - PER 沒有 10-05；official_market_daily 只到 10-02。
  - 但 price 的 `actual_max_date` 寫成 2026-10-05，而且沒有宣告缺口。
- **PARTIAL 範圍擴大：** PARTIAL 從 efbd4844 的 6 條增加到 23 條，其中 17 條是由 SUCCESS 降級。
- **個股分析的處理：** 部署維持失敗關閉，不放寬任何規則，也不會在 `cccbca1f` 上執行任何模式。等 updater 修好，並重新跑一次完整的 run 之後，再部署和訓練。

## 1. 根因（行號為 live 版本；證據見附件）

| 代號 | 問題 | 根因 |
|---|---|---|
| D5 | 10-05 在開盤前就被抓取 | run 在 10-05 04:51 啟動，`expected = taipei_now.date()`（runner 約 L3901），10-05 的請求在 04:53–05:17 全部回傳 EMPTY。resume 用同一 run 的 http cache 重播（`require_nonempty=False`，run_io 約 L929），不會重抓，只把早上的 payload 重新發布 |
| D1 | vintage repair 覆蓋了 price 的 metadata | repair candidate 的 metadata 只有 `{requested_from_date, expected_asof, coverage:{attempted, success}}`（price_vintage_repair.py L257-265），而 `lineage = dict(candidate.metadata)`（store 約 L4598）。結果沒有 coverage.total、沒有收據，永遠無法通過 PARTIAL 復原閘門（因此出現 `prior_partial_unresolved`），也抹掉了 price-axis 的證明（`_demand_source_evidence` 在 efbd4844 為 True，之後的 head 都是 False）。依賴 price axis 的 route 全部被 BLOCKED（`await_published_price_axis`） |
| D2 | 133 檔永遠無法修復 | 這 133 檔儲存的第一列是上市首日，用的是未調整基準，102 檔的 open=0，而且 provider 已不再提供這些列。所以 repair 的「候選須涵蓋儲存起日」規則每次都失敗，這 133 檔永遠拒收，也拿不到新日期；`control/price_vintage_repair.json` 每次都會重新注入 |
| D3 | 起日不一致造成大量衝突 | 主合併的請求起日是 2021-10-05，但 store 起日是 2021-10-04（PARTIAL 時不修剪）。700 檔拒收中有 567 檔只是因為這個差異 |
| D4 | resume 保護把舊 payload 以 PARTIAL 重新發布 | `run_finalize._should_protect_existing_route`（L680-688、L983-1010）：重試被 BLOCKED 時保留舊 payload 並標為 PARTIAL。global_market_history、finmind_government_bank 原本是 SUCCESS，也因此被降級 |
| D6 | run 中止 | 01:23:22 發生 `SharedPublicationBusy`，需檢查 0 ms 鎖等待，以及它和發布 worker 之間的競態 |
| — | 精確比對把雜訊當衝突 | vintage 比對對 OHLC 用精確 `!=`（store L4391-4421）。第一次合併 791 檔不符，其中 408 檔的差異小於 0.1%。repair 後 567 檔的逐股抓取與逐日抓取 674,912 列完全一致，代表 FinMind 會隨時間重算歷史，而且之後還會再發生 |

## 2. 資料面的事實

- **新舊 price 物件差異：** 367,797 列不同，只有 OHLC 與 spread 改變，成交量和金額沒變。四位數普通股的大差異有三種：
  - 約 10 檔因為 9 月下旬除權息或減資，整段歷史被重新縮放；
  - 26 檔共 707 個交易日列：舊資料的 9 月區段沒有調整（例如 2321 在 09-04..09-09 差 ×1.83，造成假的 −45% 報酬）；
  - 467 個無交易日的佔位列：舊資料常抄後一天的價格，等於偷看未來。
- **efbd4844 也不能拿來當替代：** 它的 9 月資料有上述錯誤，3356、6550、2542、1442 的三個月 P 會偏差 0.04～0.07。
- **133 檔拒收股：** 其中 21 檔在 D=10-02 屬於三個月模型的合格母體。有兩檔有實際錯誤：7631 缺 09-29 除權息調整；2254 的佔位列抄了後一天的收盤。

## 3. 請 codex 修正（建議順序）

1. **D2：** 對 finmind_price_adj，如果某檔「只在儲存端有」的列，全部早於 provider 目前提供的第一天（也就是上市首日的未調整列），就丟掉這些列，在 metadata 記錄被丟掉的鍵，改收新的完整序列。`control/price_vintage_repair.json` 只能經由這條路徑清除；其他情況維持原本的範圍規則。注意：這會改變約 299 檔四位數股的早期歷史（移除假的第二天報酬），這是修正，不是退步。
2. **D1：** vintage repair 發布時，必須保留 route 原本的 coverage total 與收據（合併 metadata，或讓 candidate 帶有真正的 route coverage），使它能通過 PARTIAL 復原閘門並保有 price-axis 證明。
3. **日期必須真實：** 決策日 D 的橫斷面不完整時，price entry 必須宣告有日期的缺口，或把 `actual_max_date` 限制在完整的日期。這次 10-05 只有 540/2,819 檔，卻沒有宣告缺口。
4. **D4：** 重試被 BLOCKED 時，應保留 head 上既有的 entry，不能把舊 payload 以 PARTIAL 重新發布。
5. **強烈建議一起做：**
   - **D3：** 候選視窗起日與 store 最小日期對齊，或把 store 修剪到 cutoff。
   - **D5：** 加防護，當天的交易日在 FinMind 發布前不排入計畫。
   - **D6：** 檢查鎖等待與發布 worker 的競態。
   - **OHLC 比對：** 改用容許誤差（例如相對 1e-6），或接受 provider 重算，並記錄差異摘要。
6. 這些都屬於 `CLAUDE_TO_CODEX_20261005_UPDATER_HANG_TRUNCATION_DEDUP_HANDOFF.md` Wave 1／2 的同一類問題，請依 `CODEX_TO_CLAUDE_20261005_UPDATER_REVIEW_CORRECTIONS.md` 的驗收方式處理。

## 4. 修好後的執行與交付條件

- **執行：** 跑一次新的 `update --mode incremental`，**不要 resume ad5ea647**。在台北時間約 22:00 之後啟動，抓當天的交易日（10-05 的資料 22:25 前就已提供）；或用 `--expected-asof` 指定 FinMind 已經發布的交易日。
  - 個股分析需要的 route 排序都在前面：official_market_daily #1、price #5、PER #15。三個月模式不需要 order-21。
- **個股分析開始部署前，會先核對新 head 符合下列條件：**
  - finmind_price_adj 為 SUCCESS，沒有 vintage 相關警告，`control/price_vintage_repair.json` 為空；
  - finmind_per 為 SUCCESS；
  - official_market_daily 的 `actual_max_date` ≥ D，只有 D 之前、有宣告日期的缺口（最多 3 天）；
  - D 的上市櫃別：TWSE ≥ 773、TPEx ≥ 643 列；
  - 價格在 D 的橫斷面完整（四位數普通股的列數與前一交易日相當）。
- **head 凍結：** 新 head 發布、個股分析的 A/B 訓練與套用期間（約 4 小時），請不要再發布新 head。這次的 A/B 就是被 22:37 的發布打斷的。
- **完成通知：** 完成後請把新 head 的 sha 與各 route 狀態寫成回覆檔放在 `D:\stock\docs\`。
