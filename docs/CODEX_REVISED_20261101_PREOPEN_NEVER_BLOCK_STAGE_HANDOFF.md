# Codex 獨立修訂交接：Preopen 與 Updater 整合更正（2026-11-01；初稿為文件階段）

> **本輪執行結果**：[HANDOFF／實測與限制](../../TWStockUpdater/artifacts/delta-performance-20261101/integration/HANDOFF.md)。後續使用者已批准執行：e84正常增量實際exit10、25available、current pointer／ACK成立，含修正續接wall2h26m12.32；preopen正式部署與23個展開既有檢查PASS。後補7檔只經source／compile與唯一還原check、兩筆cache重解，未重跑normal或宣稱已測整輪加速。
> 原UNKNOWN／來源限制及未證完整度保留；官方缺碼／已證來源無資料不混為漏抓，未知不轉NoData。字面零重複merge未全達（FX／公債0.4919453s必要proof）；未正式settle／DB遷移、train、外傳或Bot重啟。下文保留原文件階段敘述與歷史基線，最新狀態以本節／HANDOFF為準。

> **新版計畫優先**：[整合修改計畫](superpowers/plans/2026-11-01-preopen-and-updater-corrected-plan.md) 與本主文更正取代舊版部署方案。當時使用者只選擇「先修訂交接與修改計畫」；當時尚未授權程式實作、測試、部署、更新、帳冊寫入、服務重啟、外部發送或排程變更。
>
> **附錄 A～G 只作歷史證據**：[原交接附錄](CLAUDE_TO_CODEX_20261101_PREOPEN_NEVER_BLOCK_STAGE_HANDOFF.md) 僅唯讀參照，本版不複製整份附錄，也不替其並行新增內容背書；其中部署／測試／settle／套用 diff／重啟／回復命令不得直接執行。舊 stage 通過數、早期 head、舊統計及指令不是本次完成證據；出入以本主文及新版計畫為準。
>
> **兩工作線、Updater 優先**：先處理 scope migration、route 恢復索引、同輪 witness／canonical／completion 共用及限定 clean code，再一次正常增量；preopen 只可並行準備暫存碼。normal 期間 D-stock／MIX 正式檔與 contracts 不改；取得實物結果後，才依另行授權單 writer 部署 preopen 及必要同輪 settle。

**原文件階段狀態（歷史）：文件已修訂，當時程式未修、未測、未部署。** 後續估計整體 2～3 小時（修碼約 60～90 分鐘＋一次正常增量／必要結果約 60～90 分鐘），僅規劃估計，不保證速度。本次文件工作原估 10～15 分鐘；原交接曾寫入更正主文，但遭外部 writer 覆寫，故改為本獨立版。原交接此後唯讀，其並行變更不能算成本次修正，最終僅以本獨立修訂版與整合計畫為自有交付。

## 1. 裁示、目標與邊界

使用者原文：「我的要求就是舊資料不會阻擋程式執行及報告發布」。後續統一為**有可用資料就分析／發布並繁中註記**，不新增最低新鮮度門檻；模型／資料真正缺檔、損毀、內容 claim／hash／必要一致性或輸出 I/O 失敗，仍如實失敗，不能偽報成功。`--strict-calendar` 只保留手動 QA，排程不加。

v3.1 stage 尚不能直接部署：日曆 SHA 否決後同檔 fallback、日曆 stale／signal membership、跨 run 拼摘要、reader 錯鍵、三 consumer 日期硬拒及 caption 漏註記都尚待修正。約 35%=6 日21/60 是帳冊統計校正，不是模型改善；不採約40%研究替代口徑、不預告新命中率、不回補美股、不訓練、不改 H63 另一軌／排程／依賴版本。

## 2. 當前基線與歷史快照

- root 已核對：Updater `20261031T204521Z-1bde8339` 於 2026-11-01 08:48 完成，25 available／exit 10；有據 available-current switched 屬可用完成，不追 exit 0 重跑。結果薄檔與 normal 性能薄檔見新版計畫。
- price／global body 實際保留 `2021-10-01`～`2026-10-30`；floor=`2021-10-01`；training 1008 視窗=`2022-08-31`～`2026-10-30` 為上輪已驗基準，實際執行按當輪已驗日曆推進終日與1008需求，維持既有必要歷史。新股不強造更早歷史，下界刪除只限使用者手動 cleanup，不隨日常 scope migration 刪除。
- 舊版早期 head `20261031T151703Z-4bc854b6`／`2026-10-31T19:04:08Z`、物件 `_objects\46fd4011…`、10-29→10-30 latest 與當時美股缺列，均是**歷史快照**，不能代表 08:48 後狀態，也不是回補授權。
- 舊帳冊副本 evaluated=19,174、settled_h1=13,293、multi=1,898、legacy=34、pending=2,034、void=1,915，僅作歷史試算；舊 UPDATE 未分 target／run 曾互相覆寫，rowid 修法須保留，不預告新摘要數字。
- 2026-10-31 是**星期六**；週一至五排程當天未跑本身正常。`0xA`=十進位10，不能直接當故障；`0xC000013A` 具體停止原因未核對，不臆測，不在本案廣泛查 event log。
- 前一正常主輪 67m07.71 但 exit27，不是成功 benchmark；最後純本地續接21m59.73（prepare784.594s／availability291.891s／completion170.344s／localgen31.672s，collector／merge／sharedpub=0、ACK未改）不是每日正常時長。含失敗總時長2h38m42.5須揭露。舊成功78m01；正常前置37m22，其中migration22m29、recovery4m14；closed index 0 hit／40 fallback原因未逐筆全證實，不宣稱已消除。

## 3. Preopen 日曆與發布方案（待實作）

- 原 claim SHA 否決同檔不得經掃描重接受；備援只用既有可信不可變內容指紋、且可驗 v2 proof 的其他檔。單輪同檔只 read／parse 一次，不重抓日曆／全歷史；無可信日曆 predict 註記照發、settle pending。
- 加 `calendar_stale` 原因與繁中；已驗範圍內 `signal_not_session` 獨立判定，不被 stale elif／未來 target 短路吞掉。gap unverified 文字為「是否計分以結算時已驗證日曆為準」，不把估計說成確定不計分。
- 帳冊失敗但快照可寫仍發布並註記 ledger；真輸出 I/O 失敗不偽報成功。CMD 非零只要求核對 latest／log，不聲稱 latest 一定沒更新。continue／predict exit 是正式 CMD 既有能力，不當此次新增；每日 calibrate 不啟用。

## 4. 單一 run 摘要與口徑

保留 rowid 精確更新、void／multi-session／legacy 不混 H1／calibration；把既有 source_note 的實際 HEAD 註記保存至 settlement_source，保留原價格來源標記，只接通既有參數，不額外讀帳冊／來源。每 signal_date 先在具有 `SCORABLE_SQL` 列的合格 run 內，依 `recorded_at + run_id` 穩定選 first／last，再取該 run 有效列；保留原 rank，不能重排或跨 run 補 Top10 空位，每日最多一份真 Top10，missing／pending 數不可消失。

保留 v2 `all`、`views.last_publication`、`views.first_scorable_publication` 及 v1 讀取相容，新增 `selection_unit='run'`，中文明示「最後／最早可計分發布」。merged reader、單股 label 與既有 fixture 對齊 `first_scorable_publication`；不以錯鍵靜默換口徑、不採約40%研究替代法。

## 5. 三 consumer 與四種報告表面

Top10、單股、MIX 的 auto／preopen，對**較舊但可用**快照及原 signal／target 差異沿用原 H1，顯示原 signal／target、當次 report date／差異；不改寫舊預測日期、不假裝新預測、不因 freshness 自動換模型。若 snapshot 資料／發布時間晚於本次 analysis cutoff，屬真未來資料／PIT 衝突，保留既有處理，不能把所有日期差一律放行。

真正無可讀 H1 時 auto 原 fallback C0；強制 preopen 保留缺檔／損毀明確錯誤。日期不同沿用值不得記為當天新 H1 前瞻樣本。scores／ranking、ETF、hash／數值／股票市場等必要一致性仍保留。

PDF／Top10／MIX／Telegram caption 統一讀共用 reader formatter 的 `calendar_note_zh`＋`ledger_note_zh`；single_stock block 與 bot caption 接入，保留原欄位／原值，不在各 consumer 重算或重複貼長 describe。現行 PDF／Top10／MIX 有 describe 鏈，但 Bot caption 未用；日期硬拒殘留也不只 Top10，單股與 MIX 都需一起改。

## 6. 測試證據與本次文件驗收

stage64 passed／1 skipped 只是先前假資料；merged 實際整套67 passed／2 failed／1 skipped。短路徑單項通過、扣除假設或部署後自我比對跳過，都不能湊成全綠。後續更正既有 expected／fixture 與 reason-map「正式既有鍵值為 merged 子集」斷言，保留必要 columns 一致。

全部必要修改後，由 low_worker 集中一次最小既有受影響檢查；不新增測試案例／函數／檔案、不反覆TDD、不跑全套／benchmark／parity／重算歷史。失敗只修相關處並重驗受影響部分，通過停止。**本次不執行任何程式或測試**；文件驗收限自有兩檔 diff、Markdown、本機 link、UTF-8／LF；原交接保留他人內容並說明並行變更，不宣稱最後附錄仍與早期 before 相同。

## 7. Updater 優先修正範圍

- U1：分 acquisition scope變動與 retained有效內容差異，重用現有 metadata rebinder／row predicate 快路；未變保留原body／coverage／source-scope，只建必要新scope reference／metadata；真新增session／變動partition才處理。不能只鬆綁scope_id，也不能因asof／整HEAD變動讓全部歷史失效。
- U2：恢復從整run all-or-nothing改為獨立route／依賴閉包命中，只因progress指向／byte claim／依賴變更局部重建，無證據才回原必要檢查，不整掃40run。跨run不靠mtime／stat宣稱驗過內容，PARTIAL／unknown／未完HTTP如實保留。
- U3／U4：同輪傳真witness，publisher／migration／ACK／industry reference／CAS retry共用已驗HEAD route→entry／digest；canonical／receipt初次驗證同buffer產parsed／claims／layout，後續loader共用；closure去重／反向route索引一次，availability／completion共用精確依賴token，真正影響assessment才重算。
- U5／U6：保留capacity及retirement既有memo；角色敏感薄引用邊只更新changed route／claim及反向依賴，保留最終全域claim／歷史引用／coverage／allocation／CAS。未改body不完整merge／copy，不重建price日期索引或全歷史NEWS；真訂正只merge受影響partition。限定clean code去重wrapper／不可達memo／canonical化，不拆新module、不堆多層cache或全量副本。
- 確有全來源無資料證據者保留註記並停止重複追補；官方缺代號／unknown／PARTIAL不等於全來源無資料。日常僅處理新session與有變更證據缺口，不因歷史unknown重抓全歷史。沿用既有metrics，必要只加固定reason-count，不額外掃描／大log／benchmark，不承諾未量到的速度。

精確 Files、Interfaces、函式行號、唯一normal CLI／child env與既有測試候選以新版整合計畫為準；本次未改Updater。

## 8. 後續依賴與正式執行界線

Updater全部必要修改／限定clean code→一次最小既有相關檢查→只啟動一次正常增量。normal執行時preopen只可暫存，D-stock／MIX正式producer／consumer與contracts不改。使用既有cache／receipt／prepare；真來源變動可以HTTP>0，新增／失敗retry分列，已成功相同unit不重抓；真正錯誤以已存同run續接、全時長揭露，不另開normal充當benchmark。

查核25 availability／current pointer／ACK及來源限制；有據exit10／available-current switched可用完成，不追exit0。未改內容／已完成部分重複merge=0，不阻止真訂正partition merge。取得normal實物結果後，才依另行授權單writer正式部署preopen及必要同輪settle，不 `--resettle` 重算歷史、不另開Updater／訓練／回補／外部發送。附錄舊部署、重啟、測試指令一律不直接採用。

## 9. 回復與保存

本次before／diff／manifest只放 `D:\TWStockUpdater\artifacts\planning-review-20261101\`。後續正式寫入另獲授權時保存精確before／hash，只回復本次可辨識差異；舊producer與新帳冊schema不相容，不能只回程式。不得broad reset／clean、刪資料、搬其他D-stock內容或覆寫dirty變更，不在D-stock建立日期化程式副本。本次不做資料庫／程式全樹備份。

## 10. 治理狀態

本次僅兩Markdown；不新增ADR或將ADR-0009骨架升格accepted，不改排程／治理檔。正式行為變更後須依既有ADR流程記錄舊「過期即C0」與美股拒發政策修訂。`D:\TWStockData\services\acceptance\latest.json`仍為2026-08-23歷史blocked，本案文件或局部可用完成不等於整體acceptance通過。原交接原文與他人新增註記留在原檔，並行變動不由本版背書，亦不構成執行權限。

## 11. 執行角色與共同約束

主代理固定`gpt-6-astra`／`ultra`只規劃、委派、整合、唯讀驗收；真正載入的`low_worker`固定`gpt-6.1-sol`／`ultra`執行。不同檔可並行，同檔及正式資料writer不並行；保留他人變更，不替代executor、不commit／push、不啟用auto-review。`D:\stock\.venv`仍由Telegram服務使用，依賴／環境／服務／排程不動。可用性與來源完整性分列，不粉飾PARTIAL／unknown／coverage缺口。

## 12. 來源與交付索引

- 原交接及來源附錄：`D:\stock\docs\CLAUDE_TO_CODEX_20261101_PREOPEN_NEVER_BLOCK_STAGE_HANDOFF.md`，此後唯讀參照；本獨立版不控制其最新內容。
- 新版計畫：`D:\stock\docs\superpowers\plans\2026-11-01-preopen-and-updater-corrected-plan.md`；文件證據：`D:\TWStockUpdater\artifacts\planning-review-20261101\`。
- 歷史候選：`D:\TWStockData\program_data\stock_daily_c0\candidates\h1_ops_review_20261031\deploy\stage\`、同層`merged_reader\`，同候選根`dryrun\`／總報告／單股另一軌。舊hash與stage.diff只供追溯，不是修正後部署成品。
- 正式preopen：`D:\stock\programs\shared\stock_daily_preopen.py`／`stock_daily_preopen_reader.py`／`RUN_PREOPEN_DAILY.cmd`；資料根`D:\TWStockData\program_data\stock_daily_preopen\`。三consumer／PDF／Bot確切路徑在新版計畫。
- 最新結果：`D:\TWStockUpdater\artifacts\delta-incremental-20261101\integration\local-finish-reference-fix\execution\final-result-thin.json`；normal性能：同integration下`industry-continuation\execution\final-performance-thin.v2.json`。不重讀正式summary／work，不用早期head取代結果。

---
