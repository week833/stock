# Order21 四組重複自然鍵衝突交接（2026-10-04）

使用者要求將這類問題交由另一個對話確認根因後修正。本輪只撰寫交接文件，沒有修正 updater、補抓、發布資料、啟停服務或重訓；主題限於 Order21，待補 scope 作為次要待查項，不擴成全部 36 routes 的調查。

異常位置與處理責任已定位在 TWStockUpdater 收集／整理／合併端：四組不同 buy／sell 已存在於 producer 私人來源 parquet，Top10 沒有創造這四組差異。尚未證實 FinMind 原始回應、欄位整理或去重鍵誰有錯；處理責任不等於已判定供應商出錯。

## 1. 歷史觀測身分

以下為本對話 2026-10-04（Asia/Taipei）的唯讀觀測；接手必須重新核對最新 head、sealed manifest 與 live 工作。舊 run ID 只表示 lineage，不表示作業仍 active，也不能據此建議停止／重啟。

| 項目 | 觀測值 |
| --- | --- |
| head | D:\TWStockData\shared_market_data\_metadata\stock_data_current.json |
| schema／run_id | stock-data-publication-v1／20260929T041801Z-f5549d9e |
| published_at | 2026-10-03T23:26:11.418565+08:00 |
| head／sealed bytes、SHA-256 | 各 237641 bytes；均為 7ef67d34347facebfc10b274d9d4f73538ec3ef73cdafcd38a3e3f1e91cb6f5f，由另一 worker 核對 |
| sealed manifest | D:\TWStockData\shared_market_data\_manifests\7ef67d34347facebfc10b274d9d4f73538ec3ef73cdafcd38a3e3f1e91cb6f5f.json |
| route／status | finmind_trading_daily_report／PARTIAL |
| object | D:\TWStockData\shared_market_data\_objects\7840445c1e24268017783f7eacac4e7e47fa69eb6720b5e186b929cc32b4b0e1.json |
| entry.sha256／size | 7840445c1e24268017783f7eacac4e7e47fa69eb6720b5e186b929cc32b4b0e1／35951681289 bytes |
| object 檢查／actual_max_date | 檔案存在、Length 相符；未重算 body hash；actual_max_date=2026-09-29 |

正式 entry.path 為上列 object 的 shared-root 相對路徑；entry.sha256 是 manifest 宣告值。actual_max_date 不代表每股、每天及每個 requested scope 都完整。

## 2. 四鍵、八列已讀到的數值

每個 date／stock_id／securities_trader_id／price 精確鍵在指定 parquet 都命中 2 列、2 組不同 buy／sell；price 均為 0。以下保留原欄位數值，未核實 units／零價語義，不能稱為市價單、零交易、無效或可刪除資料，也不能自行換算。

| date | stock_id | securities_trader_id | price | buy | sell | 來源 |
| --- | --- | --- | ---: | ---: | ---: | --- |
| 2026-06-02 | 6938 | 779T | 0 | 4011 | 0 | S1 |
| 2026-06-02 | 6938 | 779T | 0 | 500000 | 500000 | S1 |
| 2026-06-09 | 6696 | 960T | 0 | 73309 | 70856 | S2 |
| 2026-06-09 | 6696 | 960T | 0 | 18000 | 18000 | S2 |
| 2026-06-11 | 6696 | 960T | 0 | 25000 | 25000 | S3 |
| 2026-06-11 | 6696 | 960T | 0 | 74179 | 68549 | S3 |
| 2026-09-18 | 3644 | 920T | 0 | 184412 | 215513 | S4 |
| 2026-09-18 | 3644 | 920T | 0 | 1000000 | 1000000 | S4 |

來源取自 entry.metadata.order21_reconciliation.source_states[date].path：

- S1：D:\TWStockData\updater_runtime\runs\20260926T205508Z-aeaf018f\payloads\21-finmind_trading_daily_report\2026\2026-06-02-8b6ce0eeb8986a3a75a3dc7f79d3f18e846d2e3025ee5de367c6088cf159281e.parquet
- S2：D:\TWStockData\updater_runtime\runs\20260926T205508Z-aeaf018f\payloads\21-finmind_trading_daily_report\2026\2026-06-09-42d531155fad5b443da6746982c64c7187f706e23816564ce7ebfdb13af7dabd.parquet
- S3：D:\TWStockData\updater_runtime\runs\20260929T041801Z-f5549d9e\payloads\21-finmind_trading_daily_report\2026\2026-06-11-7ad235d2f1e25cdb8685f81e7a57424317ad28a33845de2f69c3698597408a1c.parquet
- S4：D:\TWStockData\updater_runtime\runs\20260919T104949Z-22a4ba2b\payloads\21-finmind_trading_daily_report\2026\2026-09-18-75ecf990a465597eb7b65aefa5b78f6ed405b858ee97091bf71cadb104911e19.parquet

原始 API response 與 parquet 映射尚未核對；這組證據只能確認差異已存在於 producer parquet。

## 3. 正式 gap 與 attempt 不可混用

metadata.gaps 與 metadata.prior_gaps 當時各有 6 筆：4 筆上述 duplicate_natural_key_conflict，加 2 筆 order21_backfill_pending；4 是衝突數，不是全部 gap 數。order21_reconciliation.deferred 對四個日期仍為 known_natural_key_conflict。

metadata.last_attempt.status=SUCCESS 不代表正式 route 已修復。last_attempt.warnings 原文為：

- order21_conflict_unit_quarantined
- prior_partial_unresolved
- source_timestamp_unknown_or_not_newer
- stored_duplicate_natural_key

兩筆 backfill pending scope 為 2026-09-25..2026-09-25 與 2026-09-25..2026-09-30。actual_max_date=2026-09-29 不能證明這些天全缺或全已補；須另外核對 requested／formal scope、coverage、來源證據及 gap 解除條件。本輪未補抓，不把待補與四鍵衝突合成同一修復結論。

## 4. Producer 程式責任與參考索引

程式根目錄 D:\TWStockUpdater。行號是本次靜態定位，可能漂移；接手先以 rg -n 找符號及所在函式，再核對前後分支，不以舊行號直接套 patch。

| 檔案／行號 | 責任及需保留的語義 |
| --- | --- |
| D:\TWStockUpdater\src\twstock_updater\stock_shared_store.py:1367 | natural key；查是否足以區分此次零價資料的真實交易語義 |
| stock_shared_store.py:2638 | side-key 區分 candidate／stored；同側重複不等於跨新舊版本 |
| stock_shared_store.py:2759 | incoming side=1 的同鍵不同業務值記 duplicate_natural_key_conflict |
| stock_shared_store.py:2778 | stored 側不同值保各版本，記 stored_duplicate_natural_key |
| stock_shared_store.py:2800 | 整個候選股日隔離，保留既有 rows，不任選一列覆蓋 |
| stock_shared_store.py:2859 | 跨新舊值依時間、完整取代、unit／schema 判定 |
| stock_shared_store.py:1789／:3119／:3222 | 歷史 gap／partial recovery 解除，必須有匹配的正向證據 |
| D:\TWStockUpdater\src\twstock_updater\shared_publication.py:651 | producer bridge／發布接線參考，與 shared store 一起核對 |

完全相同資料可去重。現行比較不把 _shared_collected_at／collected_at／retrieved_at 算業務衝突；其他 source_sha256／schema／未知欄位差異仍可能算差異，不能一概忽略 metadata。這是程式行為盤點，尚未證明本次零價自然鍵的語義正確。

必要參考：D:\TWStockUpdater\tests\test_order21_conflict_unit_retention.py；D:\TWStockUpdater\docs\CLAUDE_TO_CODEX_20261002_ORDER21_DAILY_BROKER_AGGREGATION_HANDOFF.md；D:\TWStockUpdater\docs\CODEX_TO_ANALYSIS_DATA_FORMAT_HANDOFF_20261003.md。10/02 聚合交接與 10/03 正式 array、狀態、PIT／私人引用契約均須核對，不能當作盲目聚合或清理 checkpoint 的理由。

## 5. 下一對話執行順序

1. 讀 updater 工作規則，重新 pin 最新 head／sealed manifest，確認來源檔與 live 工作身分；保留 checkpoint、receipts、引用及既有資料，不干擾原作業，不以舊 run 判 active。
2. 追四鍵現有原始 response、adapter／parquet 映射，確認 price=0 是否有不同交易類型、欄位遺失、去重鍵不足或來源真衝突；不預設答案。必要原始證據缺失時明示未證實，不猜測選列或單位。
3. 根因確認後直接對 TWStockUpdater 做最小、有證據的修正與針對性 fixtures。
4. 全部必要修改完成後集中跑一次最小相關 fixtures；失敗只修相關問題，僅重驗受影響部分，不擴跑全套分析／訓練／suite。
5. 對指定四鍵做有界驗證，確認身份、業務值、保留／隔離及 gap 恢復符合核實語義；避免全量掃約 36 GB 正式 JSON，只輸出少量可追溯對比。
6. 分開回報程式／fixtures 修正與正式資料是否透過 producer 正規重發。若未重發，明示 live 資料未更新；若重發，pin 新 head／manifest，重新核對四鍵與 PARTIAL／gaps。補抓、發布、服務操作須符合接手對話當時授權。

backfill pending 的 scope／coverage 作為次要待查項單列；四鍵修正不代表待補完成或其他 routes 全可用。

## 6. 修復驗收

- 不任取第一／末列、不直接加總、不改 SUCCESS 或清 gap／warning 掩蓋衝突。相同資料只去重；真衝突保 PARTIAL 與證據。只有已核實業務語義才可重定義鍵或聚合，並保留單位、身份、PIT 證據。
- 四鍵結果與歷史 gap 解除須依確定來源、scope／恢復條件重現；last_attempt=SUCCESS 不能代替驗收，不將零價自動視為可刪除。
- 正式資料只由 producer 正規發布，不手改 _objects／head／sealed manifest，不用 consumer 修改掩蓋 producer 衝突。
- 程式、fixtures、正式重發、consumer runtime 分別回報，不承諾模型準確率提升。

## 7. 已完成與未完成檢查

- 已完成四 parquet 有界唯讀：DuckDB :memory:、threads=1、memory_limit=512MB、temp_directory=''；精確四鍵，每鍵上限 10、實際 2 列，共 8 列；exit 0、耗時 0.79 秒，沒有建立腳本／cache／temp／資料檔。
- head／sealed 身分已核對，正式 object existence／Length 相符；四個 parquet 與正式 body 均未重算 hash，未掃讀約 36 GB body。
- Top10 限定 49 個 fixtures 已取得通過證據：首跑 47 passed／2 個 fixture 失敗，修正後只重驗 2 個並通過，另 9 subtests passed；不是第二次整套 49。首跑既有 mix_price_features.py:2 docstring \T SyntaxWarning 未擴改。
- 未核對原始 API／交易類型／units，未判定 FinMind 或去重鍵誰錯；未修 updater 此四鍵、未補抓／正式重發，PARTIAL 不因本文件解除。
- Top10 完整實際資料 reader runtime 未驗證；沒有重訓、完整分析／報告或準確率改善證據。49 fixtures 不能替代 live 完整讀取成功。

## 8. 可直接貼上的接手 prompt

沿用使用者分工：主代理 gpt-6-astra／ultra 只規劃與唯讀驗收；修改與測試交由已註冊 low_worker，以 gpt-6.1-sol／ultra 執行。

> 請依 D:\stock\docs\CODEX_TO_UPDATER_ORDER21_CONFLICT_HANDOFF_20261004.md 接手 Order21 四組 duplicate_natural_key_conflict。先唯讀核對最新 head、live 工作與四鍵原始 response／parquet 映射，確認零價資料語義及根因後，直接對 D:\TWStockUpdater 做最小有證據的修正、一次相關 fixtures 與四鍵有界驗證。不要任取列、直接加總、清 gap 或改 SUCCESS 掩蓋；保留真衝突、PIT、來源及既有工作。order21_backfill_pending 分列為次要待查項；分開回報程式修正與正式資料是否正規重發，不手改 objects／head，不擴查其他 routes，不承諾準確率提升。
