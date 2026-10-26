# Codex 給 Claude：Updater 覆核修正交接

- 日期：2026-10-05，Asia/Taipei；執行者已確認為真正 low_worker / gpt-6.1-sol / ultra。
- 範圍：承接唯讀覆核，整合主代理提供的八項已核對發現；本輪只審查及建立本交接，沒有程式修正、重跑測試、benchmark、import、部署或發布。
- 文件編碼：UTF-8、無 BOM、LF；只修改本交接，不修改舊交接、程式、資料或記憶筆記。

## 1. 結論
HG1/HG2、OMD 缺口、R1/R2/V5 的主要診斷及 Wave 1–3 方向成立；source-only hotfix、原型 replay 與候選 consumer 都不是正式環境已修復的證明。
先補格式一致、歷史 attempt 不漂移、破損參照 fail closed、H7 完整採用／退休閘門。G1/G2/G4 應同批處理，不能讓已宣告缺口被 coverage 重用。
HG1 已有 EXPLAIN；V4 是重複入隊風險，不是每條 route 必定實體合併兩次；101 個失敗不能全稱 fixture 過期；舊 pd_final 與新版 cd_final 的個股結論須分開。

## 2. 授權與執行界線
1. 本輪唯一交付：D:\stock\docs\CODEX_TO_CLAUDE_20261005_UPDATER_REVIEW_CORRECTIONS.md。建議與驗收清單不是本輪執行命令。
2. Claude 只能在其既有明確授權內接續；本文件不新授權跨專案修改、重抓、訓練、發布、GC、資料移動／刪除或服務／程序操作。中央修復不是個股任務可順手完成的工作。
3. 本交接不是要求現在重啟 Updater。正式操作前重新確認 source hash、head SHA、PID 與建立時間、owner／鎖、pin 及其他工作；不得沿用舊 PID 或舊「沒有 updater」觀察。
4. 如 Claude A/B 仍 pin efbd4844...，協調完成前不得改 head；不能先發布再要求分析端換 pin。
5. 不重做已有匹配 source／artifact 證據的工作：rows_key、既有 EXPLAIN／目標測試、已核 hash 的保全 canonical，先核身分，只補缺少驗收。
6. 保留使用者變更、正式資料、receipt、pin、歷史封存與舊交接。舊文件內的修正、刪除、移動、重跑命令不是本輪授權。
7. 現階段 _evidence／_manifests 採 append-only；本輪不執行 GC、splice 遷移或檔案移動，後續保存政策另案決定。

## 3. 已證實現況與時間限定
以下是本輪 review 的快照／已有證據，不表示建立交接後重新執行過，也不保證後續狀態不變。
| 項目 | 已核對結果及限制 |
|---|---|
| 正式 head | D:\TWStockData\shared_market_data\_metadata\stock_data_current.json；SHA256 efbd4844396042d055e09f3ef08e4bc2c652e0dd43b0c8d22df0bbc94a1bf893；30,504,786 bytes；07:52:46 寫入，Asia/Taipei |
| OMD | official_market_daily 仍 PARTIAL；2026-09-30 TWSE／TPEx FAILED、Emerging EMPTY；沒有 coverage_ref。payload 已有列或個股 carry 不等於中央 SUCCESS |
| 重複候選 | 424b9768、ad5ea647 的 OMD pending 參照同 payload SHA c864cf085e49be98b51de50ddf95130f29bd9c435dc9278c1617bfa240cece60，1,293,680,664 bytes；均 harvest_pending，尚非退休證明 |
| as-run store | 備份／scratch asrun_src SHA 一致：0c2d32c04069759a99cc54a3cb2e9799b7910cd09ad4b7683f37a9423bb4ed78；沒有 rows_key |
| live store | scratch current_src 與 live SHA 一致：d6c91333c4d5e52db7999f63ff65e937bb9fb29b38f1363182aae53a4629c948；stock_shared_store.py:4113 已加 rows(k) |
| HG2 | runner.py:4915-4916 同步 drain；stock_shared_store.py:4917 在全域 mutex 內 merge；:835-837 的 head reader 也拿同鎖 |
| G1/G2/G4 | store:4448-4455 拒絕丟失舊欄位的替換；:4715-4730 警告阻擋按收據解除；:4762 帶回 gaps、:4786-4787 保持 PARTIAL；coverage:500-514 普通 FAILED／EMPTY gap 未必阻擋 reuse |
| R1/R2/V5 | store:3235-3236、3259-3260 兩處 16 MiB；housekeeping:471-486 同時保存全部 archive 解析；store:5299-5300 先封存再替換 head，沒有重試 |
| 部署界線 | hotfix outcome 為 source_only_applied，13 檔，15:49:43 完成，process_started=false；runtime effect 待正式啟動／驗收，不是已重啟成功 |
5.6 天是合成外推，非生產量測或可靠下限；當時精確停點沒有 thread stack dump。R1 無本次生產觸發 log；V5 小檔重現不證本次發布曾撞鎖。
主代理已核對的 P／C 原型與候選結果由本交接整合，不冒稱本 worker 又重跑或重新核對全套。

## 4. P1 必修

### P1-1：格式尚未統一，consumer 不能當成 ready
原交接:82、104、140 宣告 same_as_metadata／$evidence／v2／splice 同契約；最新可見 C\stock_data_sources.py:375、377、434 卻使用三鍵 ref、deduplicated_fields 及其還原宣告；末次文字檢索未找到 v2／$evidence／splice。不是斷言 Claude 未來不做，而是目前未證 ready。
正式 D:\stock\programs\single_stock_analysis\src\stock_data_sources.py:1516 只接受 v1，:1889 只找 archive.json，:1901 有 64 MiB 限制。
其他受限入口：D:\stock\programs\shared\stock_data_paths.py:202；D:\stock\programs\single_stock_analysis\src\stock_query_projection.py:1454；同 src\tw_single_stock_train_shared.py:55、184；MIX／Top10 等也須 consumer 先升級。
觸發／影響：producer 先切格式或省略欄位，reader 可能拒絕或誤讀證據／pin。最小建議：先凍結同一 store 層級政策與契約，不用各程序環境變數切換格式；全入口相容後才切 producer。
驗收：所有 read entrypoints 支援 v1/v2、inline/ref、D3、splice，同一 pin 同語意；壞參照 fail closed。既定 v2 ref 五鍵為 $evidence、sha256、size、type、items；契約更改先協調。

### P1-2：D3 原型讓歷史 last_attempt 漂移
P\stock_shared_store.py:806 只 jsonload 未展開；publish:4939、4942、5046 使用 raw entry；:5111 起 copy compact observation 並覆寫 coverage／expected_asof／resolved_demand，舊 same_as_metadata 隨之取得新值。
觸發／影響：metadata-only 更新把歷史 attempt 變成新值，混合不同發布的日期、coverage、收據。最小建議：任何 metadata 變更前先展開並凍結舊 attempt，寫出才 compact，舊不同值須明確保存。
原交接:145「缺少欄位回退 metadata」太寬；只在明確合法標記或 hash-verified ref 下還原，無宣告缺少保持缺少。
驗收：連續兩次 metadata-only 更新後舊 attempt 不漂移；coverage／日期／收據不混新舊；非法標記拒絕，未宣告缺少不補值。

### P1-3：保留標記一出現就必須嚴格解析
P\evidence_store.py:157 的 is_ref 要 exact schema＋五鍵；:198 resolve 對不是 is_ref 的 dict 直接返回，帶 $evidence 但缺／多鍵或 schema 錯誤可能繞過校驗。
觸發／影響：破損 ref 被當作一般 metadata，完整性檢查失效。最小建議：保留標記出現即走嚴格解析；不符合契約直接 fail closed，不能退回普通 dict。
驗收：缺鍵、多鍵、未知 schema、SHA／size／type／items 錯誤、duplicate keys、NaN、非 canonical JSON 均拒絕；不得用寬鬆 fallback 兜底。

### P1-4：H7 同 payload SHA 不足以退休候選
VERIFY_AND_PLAN_DETAILS.md:98、216 的「同 SHA 直接退休」不安全；D:\TWStockUpdater\src\twstock_updater\published_reference.py:495-497 要完整來源範圍／row/raw claim，:518-527 要 actual adoption 及 source_evidence／state 一致。
觸發／影響：同 payload 但來源證據不同、部分單位未採用或新候選發布失敗時，提前退休會失去恢復來源。
最小建議：owned proof identity 先進去重集合，SHA 只用來避免重複 merge；保留完整 identity／exhaustive source scope／actual adoption／ACK gate，以及持久化 replacement／恢復參照。
驗收：重複只做一個必要 merge，不同證據不錯併；新候選失敗／未 ACK 時舊來源仍可恢復；未有正式採用證明不得退休。

## 5. P2 必修與證據更正

### P2-1：splice 邏輯 inventory 與引用圖
並存的 <sha>.json、<sha>.splice.json 應按原始 SHA 視為同一 archive，不能把 sha.splice 當另一份。原 JSON 存在但損壞時 fail closed，不能偷偷 fallback；只有不存在才讀 splice。
D:\collect data tool\app\publish_audit.py:338 的同 SHA 多命中，以及 app\store_prune.py:111 的 descriptor filename／hash 假設須調整。
P\evidence_store.py:309 walker 僅四欄，不是完整圖；需涵蓋 G2 sidecar、U2a、splice blobs、pins 等，verify／GC 共用完整引用圖。現階段 append-only，不執行 GC。
44/44 只是 v2 還原比較，不是真 splice 遷移驗收。逐原始 archive 重建 byte SHA＋size、retain pin／全引用驗證後，才可依另有授權移動原檔，不刪除。
驗收：同 SHA 唯一定位、並存不重計、壞原 JSON 不 fallback、所有引用完整且 pin 可重建；原檔移動另行授權。

### P2-2：截斷修復必須綁定完整來源
U2_D3_D10_PRODUCER_FIXES.md:214 的 bounded 輸出與舊片段相同，只證保留片段一致；不同完整尾端也可能得到同結果。
最小建議：綁定 target head CAS、route／run／candidate／full source SHA；metadata、last_attempt 各自對應來源；只新發布修復 head，記錄 evidence_repair，不重寫歷史。
保全檔已由主代理核 hash，但未證唯一副本；本輪不再複製：
| 路徑 | Bytes | SHA256 |
|---|---:|---|
| D:\TWStockData\preserved_evidence_20261005\12-finmind_cash_flows_statement.canonical.json | 23,178,696 | 114b9456b98af285004fb25c99bd1438f4ef7f752befb9a372b63469d53e80ec |
| D:\TWStockData\preserved_evidence_20261005\08-official_finance_reporting_deadline.canonical.json | 1,856,125 | d96ea62654c693a04f6c8cdce4a2c3054135caabd35527ba1f322fa5a278e0e8 |
驗收：來源身分完整對應、修復 CAS 正確、兩種 metadata 各自可追溯；歷史不變，不能只驗 bounded equality。

### P2-3：V10 的 101 fail 不能全歸過期 fixture
既有 current／G1+G2 patched log 各 101 failed／314 passed，失敗案例集合相同；不是本輪重跑，也不能自動視為 as-run／current 的比較。
附件只分類 25 個 invalid_formal_route_entry、10 個 private-output 驗證、3 個 SharedPublicationBusy，共 38；其餘 63 尚未逐案定位，含來源成熟度斷言。
相同失敗集合不足證明無新增回歸，既有失敗可能遮住受影響分支。最小建議：只修已證過期 fixture，區分合約、鎖／環境與行為差異；不為過 test 削弱正式校驗。
驗收：原失敗逐案分類、受影響測試能到達修改分支；未判明斷言不隨意改掉，既有失敗獨立揭露。

### P2-4：HG1／V4 與個股版本必須限定
HG1 已有 D:\TWStockUpdater\tests\test_coverage_rows_key_index_hotfix.py:105-113 真實 SQL plan；既有 log 4 passed＝3 個 rows_key 案例＋1 個金融控制案例。尚缺其他查詢 plan、完整 result perf 護欄與 live。
V4：runner.py:4893-4916 的 owned／harvest 未共用去重集合；successor_resume.py:717-750 跳過 active run。機制有風險，但 runner.py:4553-4566、store:5078-5122 有 reuse 路徑，不能宣稱每 route 必合併兩次。
VERIFY_AND_PLAN_DETAILS.md:39、48-63、235-249 的「一定先修 OMD」適用舊 pd_final；主交接:137-138 針對新版 cd_final。
C\tw_single_stock_three_month_xs_io.py:296、308-318、628、859 有 carry：最多三個已宣告 board gap 日期，不能是當日 D。候選接受不是正式部署成功，也不是中央 gap 已修。
驗收：標明 source／candidate／formal 版本、已有證據與缺少驗收；carry 邊界與中央 PARTIAL 保持分開。

## 6. 建議先後順序
以下僅是另有明確授權後的順序，不要求現在執行：
1. 先核各專案權限與 owner，重新確認 source／head／PID／pin，協調 A/B 凍結；凍結格式與歷史 attempt 契約，保留匹配證據。
2. Wave 1：R1 移除兩處上限但保留 SHA／size／identity；R2 逐份處理 archive；V5 有界重試，最終失敗記錄未成為 head 的封存 SHA，不擅自刪 append-only 檔。補 H2/H3 心跳／runner log、H6 明示沿用與 PID＋建立時間；H7 保留 P1-4 閘門；rows_key 只補缺少驗收。
3. Wave 2：G1/G2/G4 同批；G2 每個改值存舊／新值與依據，sidecar 放 _evidence 並跨發布沿用；exact receipt 必須合法正面，非 list／破損 ref 不解除 gap。G3 僅對有期限的尚未發布當日單位 pending，到期揭露缺口。
4. 修復 run：僅在前述驗收、head／pin 協調與另有授權後安排；真實 harvest、目前需求及跨日路徑仍需驗收。13:30 前只限制 effective asof，時鐘不保證來源已發布。
5. Wave 3：恢復中的 route 延後，不使用舊 coverage；merge 移出 mutex 前先登記 inflight 身分／引用，housekeeping 與其他 prune 尊重它；保留全域 mutex／collect tool 閒置契約，補 reader、CAS、watchdog，不照搬舊 H4/H5。
6. 格式／證據：先 consumer 全入口驗收，再切 producer；P1-2/P1-3 必先完成。U2a 保持舊 inline 語意、完整證據另存；修復依 P2-2；splice 逐 SHA 驗證，移動另授權，不啟用 GC。

## 7. 根因修正的逐項驗收門檻
以下是後續驗收條件，本輪未執行；必要修改完成後集中做最小充分驗證，通過即停。
| 項目 | 必要門檻 |
|---|---|
| G1/G2/G4 | 全窗＋真實 recovery 均驗收；5,108 舊列、345 個 open 改值有證據，其他列不變；gap 不重用；再發布後 sidecar 可找到／hash 正確；未證單位仍 PARTIAL／pending |
| G3 | pending 僅限合格當日單位，有期限；到期暴露缺口，下一輪不可當已完成 |
| HG1 | 沿用匹配 EXPLAIN／4 passed；補所有 result 查詢及完整 result 40k／2 秒候選護欄與環境說明；live 完成另須 fresh receipt |
| R1 | >16 MiB 合法 sealed head／catalog 可讀；SHA／size／identity 不符拒絕，跨日 maturity 不弱化 |
| R2 | 峰值不再隨所有封存文件同時累積；保留時間／版本／pin／coverage／attachment 與 hash 檢查不弱化 |
| V5 | 短暫讀者開檔有界重試；最終失敗保留舊 head、記錄未提交封存，不宣稱發布成功 |
| H2/H3/H6 | 慢查詢可追溯心跳／phase／stage／log；沿用明示；owner 存活／身分不清拒絕，PID 重用不誤認 |
| Wave 3 | 不用 stale coverage 收集；active merge 不被 prune；互斥契約不破壞；CAS 不覆寫較新 head |
| 正式完成 | fresh head／receipt／payload 身分、OMD 狀態／gaps、證據及 pin 相容都通過；exit 0、程序結束或檔案存在不足代替驗收 |

## 8. 證據索引
| 縮寫 | 精確路徑 |
|---|---|
| S | C:\Users\USER\AppData\Local\Temp\claude\D--TWStockData\51ab15cb-7b36-4847-b785-5ba122936b29\scratchpad |
| P | S\updater_inv_20261005\evidence\trees\patched\src\twstock_updater |
| C | S\three_month_impl\cd_final\tree\work\programs\single_stock_analysis\src |
P／C 是 S 的相對展開縮寫，不是執行命令。Temp 可能消失或移動；本輪不複製，缺失回報證據缺口，不擅自重跑或換版本。
| 輸入文件：D:\stock\docs\ 下 | 本交接前實際核對 SHA256 |
|---|---|
| CLAUDE_TO_CODEX_20261005_UPDATER_HANG_TRUNCATION_DEDUP_HANDOFF.md | 419007ef837ddddbb5e7952ed8f0e6fa0f0a1fb5c7c5a71793263f911c2a3360 |
| claude_20261005_updater_investigation\HANG_GAP_DETAILS.md | ae5a45898d28c9aa65417324eeaea09ead4ecc8a9334666900181f0d2e2ffc67 |
| claude_20261005_updater_investigation\U2_D3_D10_PRODUCER_FIXES.md | c436c00e1e18d258ee128bdce5d2121a922d694d4afe4db1ddbe975425ebe7f8 |
| claude_20261005_updater_investigation\VERIFY_AND_PLAN_DETAILS.md | ff5ffdd4a92bd4a5da955a9fa0bdb0474802856efe35f564d932bafe4738aa5c |
HANG 行號主要指 as-run；store 原 4112 行後因 rows_key 增行。live／原型／候選不能混用行號。
- 正式 updater source 根：D:\TWStockUpdater\src\twstock_updater；本文件 store＝stock_shared_store.py，coverage＝published_coverage.py，housekeeping＝store_housekeeping.py。
- D:\TWStockData\updater_runtime\state\publication_recovery_pending.json：同 payload 的 OMD pending 身分；本輪不讀大型 payload。
- 部署根 B＝D:\TWStockUpdater\runtime\staging\formal-reuse-hotfix-20261005\backups\source-only-20261005-20261005T0745258619325Z-698afd0e；B\src\twstock_updater\stock_shared_store.py 為 as-run；B\outcome.json:3、102、176、181、183 為 source-only 界線。
- 驗證根 V＝D:\TWStockUpdater\runtime\staging\formal-reuse-hotfix-20261005\verification；V\coverage-correlated-query-plan-f-20261005T062628.json:59、103、129、132 有 SCAN／SEARCH 及零列 EXPLAIN 限制。
- V\rows-key-wave1-f-619e14a0\pytest-index-wave1.log:2、index-wave1-result.json:9、20：4 passed；V\hotfix-wave1-f-916d478c\wave1-result.json:9、20：41 目標案例，非 full suite。
- S\updater_inv_20261005\hang_gap\asrun_src\twstock_updater、current_src\twstock_updater：與備份／live SHA 匹配的副本。
- S\updater_inv_20261005\verify\out\v_hg1_query.txt:2-8、hang_gap\sim_coverage_query.out:1-7：已有合成量測；verify\out\v_replace_sharing.txt:1-2：小檔重現。
- S\updater_inv_20261005\hang_gap\pytest_selected_current.txt:304、3884、pytest_selected_patched.txt：成熟度斷言、101/314、相同失敗集合。
- S\updater_inv_20261005\hang_gap\scripts\sim_omd_publish.py:48-64：手動候選直接 publish，未走完整 harvest／formal_reuse／當前 resolved_demand；sim_omd_publish_incremental/full/full_after_incremental/full_patched.json 為各副本輸出。
- S\updater_inv_20261005\verify\out\v_g2_value_diff_bymarket.txt:6-11、hang_gap\patch_G1_G2_stock_shared_store.diff:73-100、137-149：原型改值／sidecar；上述 sim 輸出短名分別為同目錄的 sim_omd_publish_incremental.json、sim_omd_publish_full.json、sim_omd_publish_full_after_incremental.json、sim_omd_publish_full_patched.json。
- P／C 的精確 source 行號見 P1／P2；正式 consumer 絕對路徑見 P1-1，collect tool 路徑見 P2-1。主代理提供的候選端結果未在本輪重跑。

## 9. 尚未驗證／不得宣稱
1. 本輪沒有修復、部署或正式重跑，任何 Wave、producer v2、consumer 全入口相容或 splice 真遷移都未因本交接而完成。
2. 原型 SUCCESS、目標測試或合成 perf 不代表 live、full suite、正式發布；OMD 缺口仍保留，不以 carry 或 coverage count 冒充修復。
3. 沒有本次生產 R1 觸發 log／V5 撞鎖 receipt、5.6 天真實量測或精確 stack；63 個未分類失敗既不全判 fixture，也不全判產品 regression。
4. cd_final carry 是候選語意，正式部署仍需匹配證據；不能套用舊 pd_final，也不能無條件容忍 gap。
5. 保全 hash 不證唯一副本，bounded equality 不證完整尾端；Temp 缺失不授權再產生。
6. 本輪只做一次文件驗收：UTF-8 嚴格解碼／無 replacement character、必要章節、核心正式路徑、SHA256／大小與檔案狀態；未讀 secret、未執行程式測試。
7. 交付只有本檔；驗收達標即停止，不追加副本、記憶筆記、調查、測試或跨專案操作。
