# ADR-0004：共用實體資料與多 producer 發布

- ID：ADR-0004
- 日期：2026-09-13
- 狀態：accepted
- 狀態語義：使用者已核准本方向；共同資料遷移、兩 consumer live 切換與真實 reader 核對完成；資料／PIT 缺口及四 producer 新 API 更新未驗證限制保留，後續計畫仍待使用者確認。
- 範圍：四 producer 的必要共同資料發布與 MIX／個股分析 canonical 讀取。
- supersedes：ADR-0002 中 Central producer 壟斷及共同資料相關限定；ADR-0003 中兩 consumer 預設多來源選取改採共同 canonical 的部分。其餘條款及歷史證據保留。
- superseded_by：N/A

## 背景

使用者最新授權「同意執行，解除禁止改寫……用最新資料取代舊資料」。父代理將授權界定為 Central、D:\stock updater、D:\collect data tool、TWStockUpdater 的必要 source/config 與共用資料寫入。不同更新器撈取的資料應能經同一真實共同發布被兩個 consumer 使用，而不是只靠各 reader 串接各自資料夾。

## 選項

- 維持 Central 唯一 producer：不符最新明確授權。
- 維持 consumer 預設多根資料選取：保留為歷史方案，不能代替共同實體資料。
- 多 producer 寫同一明確協定、consumer 固定讀共同 canonical：採用。

## 決定

採 `stock-data-publication-v1`，head 為 `D:\TWStockData\shared_market_data\_metadata\stock_data_current.json`，payload 為共同 `_objects`。

各 producer 配置本地標準函式庫 helper，不 runtime import Central。共同 Windows mutex 序列化發布；先寫 immutable objects，再 atomic 更新 head。以 hash、日期與自然鍵合併，有效舊資料不因空／失敗結果被清掉。調整價格只在 vintage 一致時拼接；未知 timestamp 不盲覆蓋；採集時間不是 PIT 發布時間。

先導入現有共同 old global，再导入 TWStockUpdater 34 個可用候選 route。完成集中驗證，且真實 metadata/head 與引用物件建立核對後，才切換兩個 consumer。舊 v2 protocol/head 不偽造 final pin。原 TW root/run/raw/sealed 全保留、不搬程式、不刪原始資料。

本輪替代舊 Central raw/source 禁止只限必要共同資料整合。沒有授權新 API full run、order21、預測／訓練／UI、系統 env、安裝、排程或刪除原資料。ADR-0002／0003 原文與 acceptance 歷史不改寫為 PASS。

### 後續三項改善須再次核准

使用者要求共同整合完成後，依修改後 live 狀態重新確認 TWStockUpdater 的近期缺股、發布文件膨脹及記憶體改善計畫，再由使用者決定是否執行。未核准不得新啟 P1 API 補抓或 P2／P3 私有程序優化。U12 頂層 metadata 排除已隨本輪整合修改、待最終驗證；U17 streaming 僅為共同 publisher／遷移必要支援，不宣稱 TW 原 runner／reader 記憶體改善完成。

## 理由

讓不同更新器的有效資料進入同一可核對發布，避免 consumer 因 producer 名稱受阻；保留資料真實性與不同 vintage／未知時間限制，維持 producer 與分析責任分離。

## 證據

- 2026-09-13 使用者授權及父代理 U19 決策；完整執行交接：`D:\TWStockUpdater\docs\SHARED_DATA_MIGRATION_20260913.md`。
- `D:\TWStockUpdater\logs\shared_store_U12_changes.json`：validated=false。
- `D:\TWStockUpdater\logs\shared_store_U13_changes.json`：helper install pending，tests written not executed。
- `D:\TWStockUpdater\logs\shared_store_U14.json`：waiting_shared_store_module，tests_executed=false。
- `D:\TWStockUpdater\logs\shared_store_migrate_U16.plan.json`：PREPARED_NOT_EXECUTED_NOT_TESTED；來源 run `20260913T061320Z-c5ccb6b4`，僅計畫證據。
- 集中驗證、遷移、新 head 與 consumer live 切換：**待執行／未驗證**。U11 staged；B local-input 實作中，不宣稱可更新。

## 後果

共同 canonical 成為兩 consumer 的預設輸入；資料缺口、失敗及未知 PIT 仍如實記錄，不因共同發布就成為正式可用。其他 consumer／舊協定／接受歷史只在本 ADR 指定範圍受影響，其餘規則不变。

## 回復方式

保留原始資料、舊 runs、sealed 與可辨識舊配置／head，依實際備份及當下 writer 狀態規劃精確回復；不刪原始物件、不 broad reset/clean、不自行覆蓋其他代理變更。U19 README 備份為 `D:\TWStockUpdater\backups\shared_store_U19\stock\docs\decisions\README.md`；其他變更及回復證據由各 owner 實際紀錄回填。本 ADR 不代表已執行回復或驗收。

## U27 實作進度註記（2026-09-13）

依父代理更新：U24 core 定稿（提供 SHA 前綴 `8331912e...`）；U12B TW、U13C Central、U14B collect、U18 B local contract/factory、U21 attestation、U22 B flow source 已完成，B local config 已啟用。U25 開始集中最小驗證，尚無 PASS；不是四 producer 已驗證可更新。

U26 migration script SHA256 為 `87254c24d332bb15e9469637a6a915e4ee439f8137ec224ab06bcd293985de14`，pin U24 helper；候選為 21,435,972,186 bytes、34 TW 加 4 legacy。遷移仍未執行，真共同 head 尚未完成本輪 migration；U11 的 7 個 consumer 檔案仍 staged，未 live，不宣稱資料正式互通。

小紀錄包含 `D:\TWStockUpdater\logs\shared_store_U18_changes.json`、`D:\TWStockUpdater\logs\shared_store_U21_changes.json`；先前證據段保留歷史，不改寫成驗收通過。後續計畫草稿 `D:\TWStockUpdater\docs\TWSTOCKUPDATER_FOLLOWUP_PLAN_20260913.md` 由 U23 建立、pending user；market API／新 run 等禁止維持。本註記不更改既有授權範圍。

## U50 目前進度（2026-09-13）

本節與文件開頭為父代理 U50 訊息提供的目前摘要。U19／U27 等前文狀態保留為當時歷史，不代表現在仍未開始遷移，也不能將部分驗證通過解讀為全數 ready。

- 最終 core U29 SHA256：`bef59584bda4cdbd4b31e5cd336f096d6d3da493fd73c9fa7e17b1581f554be0`；四個 producer 本地 copies 一致。
- 已通過：U33 core 14 項及 consumer 2 項 fixture；U25 TW 3 項、Central 6 項、C 5 項、migration parser 1 項。這些是各指定最小驗證結果，不是全流程驗收。
- B U18 真實 local 初始化尚未通過。已歷經 codec、core schema、cadence、scope、window 修復；U47 目前失敗於 `row_schema_variants` mapping，U49 修復中。U21／U22、real freeze、compile tail 尚未執行，不標 PASS。
- U44 實際 migration 已於 **2026-09-13 19:10:12 Asia/Taipei** 開始；attempt `20260913T111012Z-86f97007`，script SHA256 `816bf70e3d0e5f680ee1216be21121a3176fe70243aaa1eef02cb983b68128a3`。進度紀錄：`D:\TWStockUpdater\logs\shared_store_migrate_U16.progress.jsonl`。
- 目前處於約 2.4 GB manifest 的串流 preflight，**尚無本輪 common head**。後續待核對導入候選合計 **21,435,972,186 bytes**；此為候選量，不能稱已搬移／已導入量。
- U48 安全 apply tool 已準備。U11 七個 consumer staged 檔案仍 not live；尚未有最終 head／consumer 實讀結果。原 raw／sealed 全留原位置。
- U43 已更新後續計畫：U12 三個 tests 已綠，P2 private 巨 manifest 呼叫鏈等仍列改善計畫。`D:\TWStockUpdater\docs\TWSTOCKUPDATER_FOLLOWUP_PLAN_20260913.md` 的 P1／P2／P3 仍 **pending user**，不得因本輪整合或部分測試通過就啟動。

U50 僅更新這两份文件並保留原 bytes CreateNew 備份於 `D:\TWStockUpdater\backups\shared_store_U50`。未執行測試、API、source 或 shared data 寫入。最終 head、實際導入量、B 剩餘驗證及 consumer 實讀結果待後續指定紀錄代理補入。

## U63 最終資料切換交接（2026-09-13）

**本輪共同資料整合、遷移、兩 consumer live 切換與真實 reader 核對已完成。** 本節與文件開頭為目前狀態；U19／U27／U50 的未遷移、未通過與 staged 記載均是歷史快照，不代表目前仍停在該階段。這不等於完成 forecast、完整 PIT、35 項全齊或四 producer 新 API 更新驗證。

### 已完成與證據

- U59：核准的 40 項 behavior 驗證通過；34 個 source/stage 的 compile、UTF-8、BOM、EOL 核對通過。B 真實 local 35 factory／freeze／variant keys 通過。
- U44 migration：native exit 0，耗時 1314.453 秒；34 routes、44,678,571 rows，38 次發布成功（34 copied、4 merged）。不是以候選 bytes 推定搬移量。
- 共同 root：`D:\TWStockData\shared_market_data`；head：`_metadata/stock_data_current.json`；schema：`stock-data-publication-v1`；head size：30,343 bytes；SHA256：`54bd9fad6f46c02297468362fe968bdcf34abbb03aa5a76fbc00a3bf5d21bd25`。payload 位於共同 `_objects`。
- U60：七個 consumer live 檔案 applied，exit 0。
- U61：single → MIX 真實 reader 在 2026-09-13 19:38:04 Asia/Taipei 完成，exit 0；所有引用均在共同 root，diagnostics 0，head 讀取前後一致。
- 2330：7 個日頻資料各 1214 天，2021-09-13～2026-09-11；營收 60 月、三財報各 20 季。2026 H1 CFO `1,482,341,242,000` 為 YTD，不是 Q2 單季。
- MIX：2776 檔中 2492 檔最新至 2026-09-11；284 檔缺最新，其中 11 檔近期待查。calendar 1350、global 39186（48 列缺 `source_close_at`）、sector 947；不把未知時間說成 PIT verified。

精確證據：

- `D:\TWStockUpdater\logs\shared_store_migrate_U44.result.json`
- `D:\TWStockUpdater\logs\shared_store_migrate_U16.progress.jsonl`
- `D:\TWStockUpdater\logs\shared_consumer_apply_U60.execution.json`
- `D:\TWStockUpdater\logs\shared_store_U61_execution.json`
- `D:\TWStockUpdater\logs\shared_store_real_read_U20.json`

U59 驗證數量與 compile/編碼結果依父代理 U63 最終證據訊息記錄；本紀錄任務未重跑驗證。U44 退出後額外一次 poll 回報 UnknownProcess，是監看編排錯誤，不是 migration failure。

### 保留限制與警告

B 保留官方旗標 false；MOPS 現金流 handler 仍缺，相關路徑會 blocked／保留既有資料。四 producer 的新 API run 未驗證，不宣稱四套 live 更新全完成。

Migration 曾記錄 calendar／market daily／gold／TDCC 的 `unknown_natural_key`；global 時戳未知或不新時保留舊重疊資料，追加新日期。這些限制不隱藏，也不以資料切換成功消除。

原 TW run／raw／sealed 與 d8 current 均留原位置，沒有偽造 legacy v2 head/final pin。四 producer 必要 source/data 與兩 consumer 必要切換已依使用者本輪解除禁止改寫授權完成；這不是廣泛解除其他禁止。

FinMind 6000/hour、Yahoo 無回應先 skip 的既有要求保留。order21、新全量 run／API、forecast、training、UI、系統 env、安裝、scheduler 等無新增授權。主代理維持必要唯讀、規劃與彙整，實際執行由 registered low_worker；本文件不宣稱重新動態驗證模型設定。

### 下一步：僅提出待確認計畫

`D:\TWStockUpdater\docs\TWSTOCKUPDATER_FOLLOWUP_PLAN_20260913.md` 由 U62 獨佔維護；P1 近期缺股、P2 發布膨脹、P3 記憶體改善仍須使用者確認後才執行。U12 頂層 metadata filter 已實作且 3 tests 通過，不能再描述為完全未做。

U63 僅文件更新，原 bytes CreateNew 備份於 `D:\TWStockUpdater\backups\shared_store_U63`；未執行測試、API、source/shared data 寫入。
