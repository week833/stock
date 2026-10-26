# CLAUDE → codex 交接：個股分析讀取端的日期拼寫（YYYYMMDD）

- 日期：20261007（依使用者 20261006 的指示：所有日期統一為 8 位數字 `YYYYMMDD`，日期格式不同不可以讓資料讀不到；個股分析讀日期要跟上，訓練要遵守）。
- 對象：codex（updater／中央發布端）與使用者。本檔是獨立的新交接檔，不沿用、也不補充先前任何交接檔。
- 範圍：只講「個股分析程式（`D:\stock\programs\single_stock_analysis`、`shared`、`telegram_report_delivery`）的讀取端收什麼日期」，以及 updater 什麼時候可以改發布資料的日期拼寫。MIX、Top10、收集資料工具不在本檔範圍（見第 6 節）。

## 0. 結論（請先看這裡）

1. **個股分析的新版本（合併 label `dates_v1`）部署、重訓並套用之前，updater 發布的資料請維持 `YYYY-MM-DD`。** 目前 `D:\stock\programs` 上的個股分析（interim_v1，10-06 23:22 部署）遇到 `YYYYMMDD` 會：有些地方整個擋掉（例如 Telegram 每份報告都被擋），有些地方**不報錯、默默讀錯**（第 4 節）。
2. `dates_v1` 部署並套用之後，個股分析的讀取端在每一個日曆日期欄位都同時收 `YYYYMMDD` 與 `YYYY-MM-DD`，**同一個物件、同一個 head 裡兩種混用也可以**；比較、排序、最大／最小、去重、窗口邊界都比日期值，不比字串（第 2 節）。這時 updater 才可以切換。
3. 怎麼確認已部署並套用（唯讀即可，第 5 節）：`D:\stock\programs\shared\date_format.py` 存在；`frozen.source_seal_id()` 等於本版封印 `bd54776e…`；`current.json` 的模型是用本版程式訓練的（報告能出、不是 `shared_code_changed_pending_shared_retraining`）。**以使用者的通知為準。**
4. 有三件事**不要改**：時間戳欄位的格式、官方原生欄位（民國 7 位 `Date` 等）、`market_update.cmd` 收 `--expected-asof YYYY-MM-DD` 的能力（第 3 節）。

## 1. 個股分析這一版做了什麼（跟 updater 有關的部分）

- 新的共用日期模組 `D:\stock\programs\shared\date_format.py`（部署後才存在）。核心函式（`parse_day`、`to_day`、`format_day`、`format_iso`、`display_day`、`same_day`、`latest_day`、`day_before`）的接受／拒絕結果與收集資料工具 `D:\collect data tool\app\date_format.py` 逐值相同（測試直接拿收集資料工具的原始碼逐值比對）。
- **讀取端**：每個讀已發布資料、head、收據、快取、歷史／帳本、設定與命令列的地方，都改成兩種拼寫都收，讀進來就換成正規的 `YYYY-MM-DD` 再往下用。
- **已發布的資料一律不改寫**（連記憶體裡也不改）：sha256、size、筆數都對原始位元組驗證；列內容原樣保留。
- **機器欄位不變**：個股分析自己寫的 JSON 欄位、快取鍵、識別碼（例如暫時版本的 `identity_sha256`、模型 lineage、帳本鍵）都維持 `YYYY-MM-DD`。這些只有個股分析自己讀，不影響 updater。
- **使用者看得到的日期**（PDF、Markdown、主控台、Telegram 訊息與 caption、圖表座標）一律印 `YYYYMMDD`；日期＋時間印 `YYYYMMDD HH:MM:SS`（台北時間）。

## 2. 部署 `dates_v1` 之後，個股分析的讀取端收什麼

### 2.1 日曆日期（純日期）欄位

| 值 | 結果 |
|---|---|
| ASCII 字串 `YYYYMMDD`（例如 `"20261005"`） | 收；必須是真的日曆日（`"20260230"` 不收） |
| ASCII 字串 `YYYY-MM-DD`（例如 `"2026-10-05"`） | 收；同上 |
| 同一個物件或同一個 head 內兩種拼寫混用 | 收；同一天的兩種拼寫視為同一天 |
| JSON 數字（`20261005`）、`true`／`false` | **不是日期**：不會被當成 1970 年或西元 20261005 年。在必要欄位上是失敗關閉（缺口／報告擋下），在選填欄位上當成沒有日期 |
| `YYYY/MM/DD`、`YYYY-M-D`、`YYYYMM`（期間）、ISO 週（`2026W401`）、全形數字 | 不是日期（同上） |
| 時間戳字串（`2026-10-05T00:00:00+08:00`）放在純日期欄位 | 不是純日期（同上）。時間戳請放在時間戳欄位 |
| 前後有空白或換行 | 命令列與 head 的日期欄位不收。少數本來就會先去空白的列讀取（官方參考列、PIT registry、本機研究快照）維持原樣，與以前相同 |

適用的位置（個股分析會讀的；每一處都比日期值）：

| 位置 | 欄位 |
|---|---|
| head（`_metadata\stock_data_current.json`、`_manifests\<sha>.json`）的 `datasets[]`，含 `metadata`、`last_attempt`（v2 head 的 `same_as_metadata` 還原後） | `actual_min_date`、`actual_max_date`、`expected_asof`、`requested_from_date`、`effective_from_date`、`cutoff_date`、缺口項目的 `date`、`missing_dates` |
| 物件的資料列 | 各 FinMind route 的 `date`（還原股價、本益比、月營收、法人、融資融券、Order21 明細與彙總列、股利等）；股利的除權息交易日欄位（`CashExDividendTradingDate`、`StockExDividendTradingDate`、`cash_ex_dividend_trading_date`、`stock_ex_dividend_trading_date`）；`official_market_daily` 的 `date`；政府銀行與官方「無資料」收據的 `observation_date` 等日期欄位 |
| 中央 publication／收據（`central_publication_reader`） | `expected_asof`；財報截止規則的 `deadline`、`available_at`（純日期兩種拼寫，或 ISO 時間戳）；`pit.expected_asof_rule` 文字裡的日期（**有 ISO 日期就用第一個獨立的 ISO 日期**，沒有才用第一個獨立的 8 位數字日期；與英數字相連的數字，例如 `rev20260814`、`20250101-A`，不算） |
| 官方行事曆（中央讀取端 `tw_market_calendar`） | 行事曆列的 `date`、`expected_asof`、`next_session`、`next_trading_date`（`next_session` 早於或等於 `expected_asof` 會被擋，比日期值） |
| 全球市場歷史（`global_market_data`） | 各列 `date`、決策日（兩種拼寫都是台北 15:30） |
| Telegram 指令 bot | head 的資料日（`actual_max_date` 等）、`priceDate` 釘選讀取、收據的 `expected_asof`、暫時版本的分析日；MIX manifest 的 `dataDate`、`forecastTargetDate` |
| 時間戳欄位裡出現的純日期（`available_at`、`published_at`、`release` 等欄位有時只有日期） | 純日期兩種拼寫都收（換成 ISO 後照原本的時間戳解析）；時間戳本身照舊 |

### 2.2 Order21（`finmind_trading_daily_report`）

- 彙總列（`twstock-updater.order21-daily-broker-summary.v1`）的 `date` 兩種拼寫都收（以前只收 ISO）。
- 「同股同日」以日期值判斷：同一股同一天，明細列寫 `2026-09-24`、彙總列寫 `20260924`，仍算同一天的明細／彙總混用 → 該股該日排除並記缺口 `order21_mixed_detail_summary:<股票>|<YYYY-MM-DD>`（兩條讀取路徑、有無列快取都一樣）。
- 同一列用兩種拼寫各發布一次 → 視為同一列（去重），**不是**自然鍵衝突；同一自然鍵但內容不同 → 仍是 `trading_daily_report_natural_key_conflict`。
- 其他彙總統計檢查（整數量、金額字串格式、一致性）不變。

### 2.3 拼寫改變對個股分析的影響（不是錯誤，但要知道）

- 改拼寫就是新的物件位元組 → 新的 sha256。個股分析依物件身分建的快取（每股列快取、截面快取、投影快取）會重建一次。**Order21 物件約 36 GB，第一次冷讀每檔報告約 16–40 分鐘**；建議同一次 run 一起切換，不要分很多次各改一點。
- 暫時版本（`dstock-interim-partial-v1`）：`identity_sha256` 含物件身分，會跟著變；但排除清單的 `exclusion_identity_sha256` 只由資料推導，**拼寫不同不會變**。所以在 head 形狀、警告名稱、資料都不變的情況下，換拼寫不會讓暫時版本模型需要重訓。
- 規則依賴的其他東西都不變：狀態值、警告名稱（`prior_partial_unresolved`、`vintage_history_conflict`）、每個 run 一個 `_shared_collected_at` 戳記。

## 3. 請不要改的地方

| 項目 | 原因 |
|---|---|
| 時間戳欄位（`published_at`、`collected_at`、`retrieved_at`、`source_close_at`、`_shared_collected_at`、`last_attempt` 的時間等）維持 ISO 8601 | 讀取端照原本的時間戳解析。`20261005T…` 這種 8 位數字開頭的時間戳**不會**被當成時間戳 |
| 官方原生欄位：`official_calendar`／`official_market_daily`／新聞的民國 7 位 `Date`、TWSE／TPEx 原生格式 | 這些走各來源自己的 parser（provider wire format），不是日曆日期入口。要改請先說 |
| `D:\TWStockData\services\market_update\market_update.cmd verify --expected-asof YYYY-MM-DD` | 個股分析（`shared\market_update_client.py`）交給這支命令的 `--expected-asof` 一律是 `YYYY-MM-DD`，環境變數 `DStock_MARKET_UPDATE_EXPECTED_ASOF`、`DStock_CENTRAL_SESSION_EXPECTED_ASOF` 也是。這支服務目前只收 `\d{4}-\d{2}-\d{2}`。若要改成收 `YYYYMMDD`，**必須繼續收 `YYYY-MM-DD`**（與收集資料工具判斷 updater CLI 的規則相同：舊版只收 YYYY-MM-DD 時一律傳 YYYY-MM-DD） |
| head 的結構、狀態值、警告名稱、`$evidence` 參照規則 | 與日期無關，維持 v2 head 規格 |

## 4. 為什麼一定要等部署之後：現在 live（interim_v1）的程式遇到 YYYYMMDD 會怎樣

以下是部署前的程式（目前在 `D:\stock\programs`）的行為，都已在 `dates_v1` 修正，而且有測試：

| 位置 | 現在的行為 |
|---|---|
| Telegram bot `_valid_data_date` | head 的資料日是 `YYYYMMDD` → **每一份 Telegram 報告都被擋** |
| `published_single_stock_projection._date`、`central_publication_reader`、`tw_market_calendar`、`market_update_client`、暫時版本規則的日期讀取、xs 的缺口日期 | 只收 ISO → 失敗關閉（報告擋下或資料當成缺口） |
| `published_single_stock_projection` 的 `data_date` | 對原始字串取最大值；混用時 `"20261001" > "2026-10-02"`（字串比較），會**選錯日子** |
| 分析程式的每個 route 資料框（`to_datetime_index`） | 混用拼寫時有些列變成 NaT 後被丟掉 → **默默少資料** |
| 共用模型的時間切分（`shared_model._as_date`） | `np.datetime64("20261002")` 變成西元 20261002 年 |
| 次日開盤的美股可用性（`tomorrow_open`） | 字串 `>=` 比較，8 位數字的日期永遠「比較新」→ 舊的美股收盤被當成可用 |
| 全球市場決策時間（`global_market_data`） | `20260909` 被當成 00:00 而不是 15:30 台北 → 少算一個美股交易日（實測 `20260904` 而不是 `20260908`） |
| xs 讀取（`three_month_xs_io._strict_table`） | JSON 數字日期變成 1970 年的列 |
| 前瞻帳本（`three_month_xs_ledger`） | 8 位數字的 header 日期變成 2112-04-25 |
| v4 績效帳本、可比較預測的官方價格規則 | 字串相等比較 → 默默「不合格」或「未確認」 |

所以部署前切換拼寫，結果不是「全部擋下」就是「默默讀錯」，兩種都不可接受。

## 5. 時序與確認方式

1. **現在**：updater 維持 `YYYY-MM-DD`。
2. 使用者核准後，個股分析依 `three_month_impl\DEPLOY_PLAN.md`（第 8 版）部署 `dates_v1`：複製程式、用部署位元組重訓、`--apply-run` 套用、重啟 Telegram 服務。**複製程式到套用之間所有報告都會被擋**（封存檔改了，舊模型對新程式無效），這是預期的。
3. **部署並套用之後**，updater 可以切換。確認方式（全部唯讀）：
   - `D:\stock\programs\shared\date_format.py` 存在；
   - `python -B three_month_impl\fix\precheck_live.py dates_v1 --deployed` → exit 0；
   - 用 `D:\stock\programs\single_stock_analysis\src` 執行 `import tw_single_stock_frozen as f; print(f.source_seal_id())` → `bd54776e006526182f312747c9246e013225b31c3ae6b7245fe2b2cb7091a6a1`；
   - 一份個股三個月報告能正常產生（不是 `shared_code_changed_pending_shared_retraining`）。
   以使用者的通知為準。
4. 切換方式：可以一次全切，也可以 head 與物件分開切；混用可以。建議同一次 run 把同一個物件整個換掉（避免 Order21 多次冷讀）。
5. **回滾的約定**：如果之後個股分析回滾到 interim_v1，舊程式不能讀 `YYYYMMDD`（第 4 節）。所以在使用者確認不會回滾之前，請保留「重新發布為 `YYYY-MM-DD`」的能力。
6. 若 updater 想用別的拼寫（例如時間戳改成 8 位數字開頭、民國年、`YYYY/MM/DD`），個股分析一律不收，請先說。

## 6. 不在本檔範圍、但切換前要另外確認的消費端

- **MIX**（`D:\MIX All`）：不 import 個股分析的程式。共用資料夾裡的 `shared\tw_market_data_analysis_core.py` 屬於 MIX，本版沒有改；已知它在 2486–2487 行用字串比較日期（混用時會排錯），`normalize_yyyymmdd` 與幾個列讀取會去掉分隔符號。MIX 自己的讀取端要由 MIX 的負責人確認。
- **Top10**（`D:\stock\TWNextDayTop10`）：有自己的一份讀取程式，本版沒有改。
- **收集資料工具**：已採用相同慣例（README「日期一律 YYYYMMDD」）。
- 所以「個股分析已部署」只代表個股分析可以讀；updater 真正切換前，其他消費端也要各自確認。

## 7. 證據（`three_month_impl\dates\`）

- 盤點與決策：`inventory\INVENTORY.md`（D1–D14）。
- 三組實作：`readers\READERS_REPORT.md`（讀取端、訓練端）、`outputs\SET2_OUTPUTS.md`（報告、歷史、帳本、CLI）、`shared-telegram\DETAILS.md`（共用模組、共用讀取端、Telegram）。
- 兩份審查：`review_correctness\REVIEW_CORRECTNESS.md`、`review_compatibility\REVIEW_COMPATIBILITY.md`。
- 最終合併、修正與驗證：`final\FINAL_DATES_REPORT.md`（全套測試 live 副本對合併樹、真實資料的零差異與拼寫檢查、2330 三個月暫定報告、A1 嚴格 oracle、合併與補丁）。
- 測試（部署後也在 `D:\stock\programs` 裡）：`single_stock_analysis\tests\test_date_spelling_readers.py`（head 與物件全用 YYYYMMDD、同一物件混用，結果與 ISO 完全相同；無效值失敗關閉）、`test_dates_v1_review_fixes.py`（Order21 混用拼寫）、`shared\tests\test_date_format.py`、`shared\tests\test_shared_date_readers.py`、`telegram_report_delivery\tests\test_telegram_dates.py`。
