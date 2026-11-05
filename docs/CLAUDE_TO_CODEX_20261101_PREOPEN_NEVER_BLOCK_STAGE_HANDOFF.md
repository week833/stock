# Claude → Codex 交接：Preopen 與 Updater 整合更正（2026-11-01；僅文件，尚未實作）

> **新版計畫優先**：[整合修改計畫](superpowers/plans/2026-11-01-preopen-and-updater-corrected-plan.md) 與本主文更正取代舊版部署方案。使用者本次只選擇「先修訂交接與修改計畫」；未授權程式實作、測試、部署、更新、帳冊寫入、服務重啟、外部發送或排程變更。
>
> **附錄 A～G 只作歷史證據**：來源逐字收錄與他人新增註記均保留；附錄中的部署／測試／settle／套用 diff／重啟／回復命令不得直接執行。舊 stage 通過數、早期 head、舊統計及指令不是本次完成證據；出入以本主文及新版計畫為準。
>
> **兩工作線、Updater 優先**：先處理 scope migration、route 恢復索引、同輪 witness／canonical／completion 共用及限定 clean code，再一次正常增量；preopen 只可並行準備暫存碼。normal 期間 D-stock／MIX 正式檔與 contracts 不改；取得實物結果後，才依另行授權單 writer 部署 preopen 及必要同輪 settle。

**目前狀態：文件已修訂，程式未修、未測、未部署。** 後續估計整體 2～3 小時（修碼約 60～90 分鐘＋一次正常增量／必要結果約 60～90 分鐘），僅規劃估計，不保證速度。本次文件工作預估 10～15 分鐘。

## 1. 裁示、目標與邊界

使用者原文：「我的要求就是舊資料不會阻擋程式執行及報告發布」。後續統一為**有可用資料就分析／發布並繁中註記**，不新增最低新鮮度門檻；模型／資料真正缺檔、損毀、內容 claim／hash／必要一致性或輸出 I/O 失敗，仍如實失敗，不能偽報成功。`--strict-calendar` 只保留手動 QA，排程不加。

v3.1 stage 尚不能直接部署：日曆 SHA 否決後同檔 fallback、日曆 stale／signal membership、跨 run 拼摘要、reader 錯鍵、三 consumer 日期硬拒及 caption 漏註記都尚待修正。約 35%=6 日21/60 是帳冊統計校正，不是模型改善；不採約40%研究替代口徑、不預告新命中率、不回補美股、不訓練、不改 H63 另一軌／排程／依賴版本。

## 2. 當前基線與歷史快照

- root 已核對：Updater `20261031T204521Z-1bde8339` 於 2026-11-01 08:48 完成，25 available／exit 10；有據 available-current switched 屬可用完成，不追 exit 0 重跑。結果薄檔與 normal 性能薄檔見新版計畫。
- price／global body 實際保留 `2021-10-01`～`2026-10-30`；floor=`2021-10-01`，training 1008 視窗=`2022-08-31`～`2026-10-30`。新股不強造更早歷史，下界刪除只限使用者手動 cleanup，不隨日常 scope migration 刪除。
- 舊版早期 head `20261031T151703Z-4bc854b6`／`2026-10-31T19:04:08Z`、物件 `_objects\46fd4011…`、10-29→10-30 latest 與當時美股缺列，均是**歷史快照**，不能代表 08:48 後狀態，也不是回補授權。
- 舊帳冊副本 evaluated=19,174、settled_h1=13,293、multi=1,898、legacy=34、pending=2,034、void=1,915，僅作歷史試算；舊 UPDATE 未分 target／run 曾互相覆寫，rowid 修法須保留，不預告新摘要數字。
- 2026-10-31 是**星期六**；週一至五排程當天未跑本身正常。`0xA`=十進位10，不能直接當故障；`0xC000013A` 具體停止原因未核對，不臆測，不在本案廣泛查 event log。
- 前一正常主輪 67m07.71 但 exit27，不是成功 benchmark；最後純本地續接21m59.73（prepare784.594s／availability291.891s／completion170.344s／localgen31.672s，collector／merge／sharedpub=0、ACK未改）不是每日正常時長。含失敗總時長2h38m42.5須揭露。舊成功78m01；正常前置37m22，其中migration22m29、recovery4m14；closed index 0 hit／40 fallback原因未逐筆全證實，不宣稱已消除。

## 3. Preopen 日曆與發布方案（待實作）

- 原 claim SHA 否決同檔不得經掃描重接受；備援只用既有可信不可變內容指紋、且可驗 v2 proof 的其他檔。單輪同檔只 read／parse 一次，不重抓日曆／全歷史；無可信日曆 predict 註記照發、settle pending。
- 加 `calendar_stale` 原因與繁中；已驗範圍內 `signal_not_session` 獨立判定，不被 stale elif／未來 target 短路吞掉。gap unverified 文字為「是否計分以結算時已驗證日曆為準」，不把估計說成確定不計分。
- 帳冊失敗但快照可寫仍發布並註記 ledger；真輸出 I/O 失敗不偽報成功。CMD 非零只要求核對 latest／log，不聲稱 latest 一定沒更新。continue／predict exit 是正式 CMD 既有能力，不當此次新增；每日 calibrate 不啟用。

## 4. 單一 run 摘要與口徑

保留 rowid 精確更新、void／multi-session／legacy 不混 H1／calibration。每 signal_date 先在具有 `SCORABLE_SQL` 列的合格 run 內，依 `recorded_at + run_id` 穩定選 first／last，再取該 run 有效列；保留原 rank，不能重排或跨 run 補 Top10 空位，每日最多一份真 Top10，missing／pending 數不可消失。

保留 v2 `all`、`views.last_publication`、`views.first_scorable_publication` 及 v1 讀取相容，新增 `selection_unit='run'`，中文明示「最後／最早可計分發布」。merged reader、單股 label 與既有 fixture 對齊 `first_scorable_publication`；不以錯鍵靜默換口徑、不採約40%研究替代法。

## 5. 三 consumer 與四種報告表面

Top10、單股、MIX 的 auto／preopen，對**較舊但可用**快照及原 signal／target 差異沿用原 H1，顯示原 signal／target、當次 report date／差異；不改寫舊預測日期、不假裝新預測、不因 freshness 自動換模型。若 snapshot 資料／發布時間晚於本次 analysis cutoff，屬真未來資料／PIT 衝突，保留既有處理，不能把所有日期差一律放行。

真正無可讀 H1 時 auto 原 fallback C0；強制 preopen 保留缺檔／損毀明確錯誤。日期不同沿用值不得記為當天新 H1 前瞻樣本。scores／ranking、ETF、hash／數值／股票市場等必要一致性仍保留。

PDF／Top10／MIX／Telegram caption 統一讀共用 reader formatter 的 `calendar_note_zh`＋`ledger_note_zh`；single_stock block 與 bot caption 接入，保留原欄位／原值，不在各 consumer 重算或重複貼長 describe。現行 PDF／Top10／MIX 有 describe 鏈，但 Bot caption 未用；日期硬拒殘留也不只 Top10，單股與 MIX 都需一起改。

## 6. 測試證據與本次文件驗收

stage64 passed／1 skipped 只是先前假資料；merged 實際整套67 passed／2 failed／1 skipped。短路徑單項通過、扣除假設或部署後自我比對跳過，都不能湊成全綠。後續更正既有 expected／fixture 與 reason-map「正式既有鍵值為 merged 子集」斷言，保留必要 columns 一致。

全部必要修改後，由 low_worker 集中一次最小既有受影響檢查；不新增測試案例／函數／檔案、不反覆TDD、不跑全套／benchmark／parity／重算歷史。失敗只修相關處並重驗受影響部分，通過停止。**本次不執行任何程式或測試**；文件驗收限 diff、Markdown、本機 link、UTF-8／原LF及附錄保留。

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

本次僅兩Markdown；不新增ADR或將ADR-0009骨架升格accepted，不改排程／治理檔。正式行為變更後須依既有ADR流程記錄舊「過期即C0」與美股拒發政策修訂。`D:\TWStockData\services\acceptance\latest.json`仍為2026-08-23歷史blocked，本案文件或局部可用完成不等於整體acceptance通過。附錄保留其原文與他人新增註記，皆不構成執行權限。

## 11. 執行角色與共同約束

主代理固定`gpt-6-astra`／`ultra`只規劃、委派、整合、唯讀驗收；真正載入的`low_worker`固定`gpt-6.1-sol`／`ultra`執行。不同檔可並行，同檔及正式資料writer不並行；保留他人變更，不替代executor、不commit／push、不啟用auto-review。`D:\stock\.venv`仍由Telegram服務使用，依賴／環境／服務／排程不動。可用性與來源完整性分列，不粉飾PARTIAL／unknown／coverage缺口。

## 12. 來源與交付索引

- 新版計畫：`D:\stock\docs\superpowers\plans\2026-11-01-preopen-and-updater-corrected-plan.md`；文件證據：`D:\TWStockUpdater\artifacts\planning-review-20261101\`。
- 歷史候選：`D:\TWStockData\program_data\stock_daily_c0\candidates\h1_ops_review_20261031\deploy\stage\`、同層`merged_reader\`，同候選根`dryrun\`／總報告／單股另一軌。舊hash與stage.diff只供追溯，不是修正後部署成品。
- 正式preopen：`D:\stock\programs\shared\stock_daily_preopen.py`／`stock_daily_preopen_reader.py`／`RUN_PREOPEN_DAILY.cmd`；資料根`D:\TWStockData\program_data\stock_daily_preopen\`。三consumer／PDF／Bot確切路徑在新版計畫。
- 最新結果：`D:\TWStockUpdater\artifacts\delta-incremental-20261101\integration\local-finish-reference-fix\execution\final-result-thin.json`；normal性能：同integration下`industry-continuation\execution\final-performance-thin.v2.json`。不重讀正式summary／work，不用早期head取代結果。

---
# 附錄（來源文件逐字收錄；以主文為準）

## 附錄 A：暫存版變更說明——現行段（v3.1 審查處置、v3 裁示規則、manifest 欄位、reader 文字、完整行為矩陣、cmd、既有測試、測試結果、部署步驟、回復方式、已知差異）

來源：`D:\TWStockData\program_data\stock_daily_c0\candidates\h1_ops_review_20261031\deploy\CHANGES_stage.md`；收錄範圍：檔首至「v2 變更說明（歷史）」之前。

【註 1】「部署步驟（v3）」與「回復方式」較主文 §8／§9 簡略，以主文為準。其中第 2 點「`single_stock_analysis`、`telegram_report_delivery`、TWNextDayTop10 都會讀到新 `describe()`」應讀為主文 §3 第 4 點：`telegram_report_delivery` 不載入 reader、個股 caption 不印 describe 文字，會自動顯示的是 TWNextDayTop10、單股 PDF 與 `D:\MIX All`；第 2 點「本次未合併」已過期（三方合併已完成於 `deploy\merged_reader\`，附錄 F、主文 §7.4）；第 4 點「同 v2 步驟 4」指未收錄的 v2 部署步驟，其預期分類數已抄入主文 §8.7。

【註 2】本附錄的「審查發現 N」「審查第 N 項」指本附錄開頭 v3.1 的 6 項表；附錄 B、D 的「審查第 N 項」「審查者第 N 項」指附錄 B 的 20 項表。

【註 3】「v3 已知差異」第 3 點的「見 v2『消費端行為』」指未收錄的一節，其要點見附錄 B【註 3】。

【註 4】「v3 的規則」「行為矩陣（v3）」與主文 §3／§5 內容一致，主文 §5 另含 v3.1 補充列與結算備註，以主文 §5 為準。

### 開盤前版 H1 管線修正：暫存版（deploy/stage）變更說明 — v3.1（v3 審查修補；使用者 2026-11-01 裁示：一律發布、註明不阻擋）

日期：2026-11-01（週日）。本目錄仍是「暫存版」，**尚未部署**；正式檔 `D:\stock\programs\shared\stock_daily_preopen.py`、`stock_daily_preopen_reader.py`、`RUN_PREOPEN_DAILY.cmd` 與 `tests\test_stock_daily_preopen.py` 未被修改（sha256 仍為 `a56f6be7…a823`／`151c45c7…c69a`／`96478534…7ba7`／`ec6a48a8…27cd`，`git status` 乾淨）。v3 在 v2（兩位審查者修補後的版本，備份於 `deploy\v2\`）之上只改發布守門的「處置」：守門的判定邏輯、結算端、摘要 v2 完全不變。**v3.1（本次，2026-11-01）在 v3 之上處理審查發現 1、2、3、5**（v3 的三個檔、測試、文件備份於 `deploy\v3\`；v3 → v3.1 的 diff 為 `ops_patch_v3_1.diff`）。

#### v3.1：審查發現處置（逐條；證據在 `e2e_v31\`）

| # | 審查發現 | 處置 | 做了什麼 |
|---|---|---|---|
| 1 | `--closed-day` 日期格式錯誤 → 未捕捉的 `ValueError`、exit 1、不發布；cmd 以 `%*` 透傳 | **fixed** | 新增 `parse_closed_days()`：無法解析的值忽略、記 `calendar_gate.closed_day_invalid[]`、reason `closed_day_cli_invalid`（最低嚴重度；legacy 碼 `preopen_closed_day_invalid`）、照常發布 exit 0，與宣告檔的 `closures_file_invalid` 同一處置（不採 argparse `type=`：排程參數打錯仍不得擋住每日發布）。`--help` 註明格式與處置；reader 文字「命令列宣告的休市日格式無效（2026/10/28，已忽略）」；行為矩陣補列。`RUN_PREOPEN_DAILY.cmd` 未改（`%*` 透傳不再有害）。 |
| 2 | 行為矩陣「已驗證非交易日 10-22→10-23」與「補假 10-22→10-26」兩列與程式輸出不一致；目標日已判 void 時 stale_signal 文字仍說「預期 1 個交易日」 | **fixed** | 矩陣兩列改寫（10-22→10-23：`reasons=["target_verified_closure", "stale_signal"]`、兩段文字；10-26 列註明只在明確 `--target-date 2026-10-26` 時出現，每日流程 10-26 早上 `resolve_target` 跳過補假 → 目標 10-27、`signal_gap_unverified`）。reader `calendar_gate_text()`：reasons 含 `target_*` 或 `signal_not_session`（該列結算必不計分）時，stale_signal 一段只寫「中央價格落後：本快照以 … 訊號日重發，目標日 …」，不再補「（預期 1 個交易日）」或「（跨 N 個交易日），結算時不計入實盤統計」。 |
| 3 | 帳冊 `state\forward.sqlite3` 寫入先於 `latest.json`：被鎖定／損毀時快照已寫出但不發布、exit 1（pre-existing） | **fixed** | `cmd_predict` 把入帳包進 `try/except (sqlite3.Error, OSError)`：失敗時記日誌、manifest 新增頂層 `ledger={status: error, error, rows: 0, path}`、照常寫 manifest 與 `latest.json`、exit 0；成功時 `{status: ok, rows: N}`；`--no-latest` 為 `skipped`。manifest 改在入帳之後寫出，與 `latest.json` 內容一致。帳冊問題不影響預測品質，**不標 degraded**；reader `describe()` 附「帳冊寫入失敗（本快照未入帳，結算時不計分）」。以損毀檔、另一連線 `BEGIN EXCLUSIVE` 鎖定（等 5 秒 `database is locked`）、`state` 為檔案三種實際情況測試。 |
| 4 | 消費端殘餘阻擋路徑：TWNextDayTop10 `confirm_target`／`preopen_stale` 在 `--model-source preopen` 時中止；auto 只回退 C0 | **屬實；文件補述**（`D:\stock` 本次不可改） | 核實 `D:\stock\TWNextDayTop10\preopen_source.py` 第 397-409 行 `confirm_target()` 在 requested=="preopen" 時 raise `PreopenRequiredError`、auto 時 `replace(... model_source="c0")`；`choose()` 第 345／351 行 `load_latest(expected_signal_date=expected)`；第 362 行呼叫 `reader.describe()`；`RUN_TOP10.cmd` 預設 auto。補入「消費端行為」與 needs_user_decision。 |
| 5 | 中央 head 缺 `actual_max_date` 時以 1900-01-01 代入 → 誤標「中央價格落後」 | **fixed** | 新增 `central_price_max_date()`（`_as_date` 嚴格解析 YYYY-MM-DD／YYYYMMDD，與籌碼新鮮度同一規則；缺／無法解析 → None）；`calendar_gate(px_max=None)` 記 `central_max_date_unknown`（legacy 碼 `preopen_central_max_date_unknown`，`central_price_max_date=null`），與日曆的比對照常（訊號日 < 日曆最後 session 仍標 stale_signal，note 改為「面板訊號日 … 早於 …」）；reader 文字「中央價格最新日未知（head 中繼資料缺 actual_max_date），訊號日未與中央核對」。中央目前格式為 `YYYY-MM-DD`（唯讀查 `_metadata\stock_data_current.json`：finmind_price_adj actual_max_date=2026-10-30）。 |
| 6 | 核實紀錄（sha、測試、結算端未改、diff 可套用、cmd 證據） | 無需修改 | v3.1 重做同樣核實：`stage.diff` `git apply --check` 通過、套用後與 v3.1（LF 正規化）逐位元相同；結算端／摘要仍零 hunk。 |

v3 → v3.1 的 diff（`ops_patch_v3_1.diff`）：`stock_daily_preopen.py` +80／−21（hunk 只在 docstring、`LEDGER_REL`、`parse_closed_days`、`central_price_max_date`、`GATE_STATUS_ORDER`／`GATE_LEGACY_CODES`、`calendar_gate`、`cmd_predict`、argparse `--closed-day` help）、`stock_daily_preopen_reader.py` +34／−5（docstring、三個常數、`calendar_gate_text`、新 `ledger_text`、`describe`）、`tests\test_preopen_stage_ops.py` +133／−1（新增 `test_predict_publishes_on_invalid_closed_day_and_unknown_central_max_date`、`test_predict_publishes_when_ledger_is_locked_corrupt_or_uncreatable`；擴充 `test_calendar_gate_rules`、`test_announced_closures_file`、`test_help_lists_new_flags`、兩個 reader 測試）；`RUN_PREOPEN_DAILY.cmd` 與既有測試修補複本 **未改**（sha 同 v3，`cmd_run_v3\` 的 Run A–C 證據仍適用）。`cmd_settle`／`settle_rows`／`forward_summary` 零 hunk，`..\dryrun\` 不需重跑。

manifest 新增（v3.1）：`calendar_gate.status`／`reasons` 多兩個代碼 `central_max_date_unknown`、`closed_day_cli_invalid`；`calendar_gate.closed_day_invalid[]`；`calendar_gate.central_price_max_date` 可為 `null`；頂層 **`ledger`** `{status: ok|error|skipped, error, rows, path}`。`degraded` 的規則不變（美股缺漏 or 籌碼 stale or gate≠ok），`ledger.error` 不算 degraded。

測試（v3.1）：v3.1 暫存複本 **64 passed／1 skipped、0 failed**（38 既有 + 26 暫存版）；`deploy\stage\tests` 獨立模式 26 passed；未修補的正式既有測試對 v3.1 仍是同一個預期失敗（1 failed／37 passed／1 skipped，與 v3 相同原因）。守門情境表 23 例（`e2e_v31\scenarios_v31.txt`）與 predict 端到端 22 例（`e2e_v31\e2e_predict_v31.txt`；含帳冊損毀／鎖定／`state` 為檔案、`--closed-day` 格式錯、head 缺／不可解析 `actual_max_date`）全部 exit 0、`latest.json` 更新、manifest 與 `latest.json` 一致、reader 可讀；`--strict-calendar` 仍拒發。

#### v3：使用者 2026-11-01 裁示

> 「我的要求就是舊資料不會阻擋程式執行及報告發布」（使用者，2026-11-01，原文）

這推翻 v2 的 A3 預設拒發。v3 的規則：

1. **predict 一律發布、`latest.json` 一律更新、exit 0。** 中央價格落後（舊訊號重發）、訊號日與目標日之間有無法解釋的平日、目標日為週末／固定假日／已宣告休市／已驗證非交易日、中央日曆缺檔／雜湊不符／格式錯、宣告檔無效，全部只在 manifest 註記（`calendar_gate.status`≠`ok` → `degraded=true`、原因碼併入 `degraded_reasons`），不中止、不拒發、不寫 `latest_refusal.json`。
2. **拒發只保留在新的 `--strict-calendar` 旗標（手動 QA 用，預設關閉；cmd 不傳）**：strict 且 status≠ok 才 exit 非 0（SystemExit 訊息 → 結束碼 1）並寫 `predictions\latest_refusal.json`；下一次成功發布時刪除該檔。`--allow-stale-signal` 改為無作用的相容旗標（`--help` 註明；不覆蓋 strict），manifest 只記錄 `calendar_gate.allow_stale_signal`。
3. `--closed-day` 與 `<root>\config\announced_closures.json` 保留為選用：有宣告時 gate 變 `ok`，`gap_days[].status=declared_closure`、`declared_closures`／`declared_closures_cli`／`closures_file` 記錄依據。宣告檔格式錯誤 v2 會 `SystemExit`，v3 改為忽略並註記 `closures_file_invalid`（不得讓一個舊設定檔擋住每日流程）。
4. 日曆不可用時 `status=calendar_unavailable`（`calendar_error_code` 為 `preopen_calendar_unavailable|invalid|sha_mismatch`），目標日照原本 `resolve_target` 的規則（今日為平日且非已知休市 → 今日；否則下一個假設交易日）發布。
5. 帳冊寫入規則不變（`prospective` 仍依 `created_at` < 目標日 09:00 判定；新列 `settlement_status=pending`、`sessions_ahead` 為已驗證值或 NULL）。結算端 `cmd_settle`／`settle_rows`／`forward_summary`（void／multi-session／legacy 不計分、summary v2 只計 sessions_ahead=1）**一行未改**（v2→v3 diff 的 hunk 只在 docstring、`load_announced_closures`、`calendar_gate`、`write_refusal`、`cmd_predict`、argparse），因此 `..\dryrun\` 的帳冊副本試算**不需重跑**，結果仍適用。
6. 唯一仍會讓 predict 以非 0 結束的輸入是明確給錯的 `--target-date`（不晚於訊號日；既有測試 `test_predict_target_not_after_signal_still_rejected` 保留此行為）與真正的執行失敗（無現役模型、中央 head 不可讀、套件錯誤）——這些不是「舊資料」。v3.1 起 `--closed-day` 格式錯誤、中央 head 缺 `actual_max_date`、帳冊被鎖定／損毀也都只註記、照常發布。仍會非 0 的路徑盤點（審查第 3 項）：`load_active` 的 5 個 `SystemExit`（無 pointer、pointer schema、artifact sha、sklearn 版本、features/recipe）、`pdata.read_head`／`load_inputs` 的 `PreopenDataError`、明確 `--target-date` ≤ 訊號日、`--strict-calendar` 拒發、快照檔本身的寫入 I/O 錯誤。

| 檔案 | 來源 | v3.1 sha256 | v3 sha256 | v2 sha256 | 正式檔 sha256 |
|---|---|---|---|---|---|
| `stage\stock_daily_preopen.py`（CRLF 1,725 行） | v3 修改 | `218a05beb83b…3625` | `8a1c5cc40043…fee2` | `368d32685e32…a05b` | `a56f6be72858…a823` |
| `stage\stock_daily_preopen_reader.py`（LF 362 行） | v3 修改（正式檔複製後改 `describe()`） | `fa9228b57c22…2d73` | `ad535706773d…0264` | —（v2 未改） | `151c45c7293c…c69a` |
| `stage\RUN_PREOPEN_DAILY.cmd`（CRLF 91 行） | **v3.1 未改**（v2 改註解、訊息；流程不變） | `c94b962f6c7f…0d74` | `c94b962f6c7f…0d74` | `8b093bdca6de…a626` | `964785344482…7ba7` |
| `stage\tests\test_preopen_stage_ops.py`（LF 1,114 行、26 個測試） | v3 擴充 | `883003362c7d…34f0` | `64de433edd08…f2cf` | `d5a82a03c49e…955e` | — |
| `stage\tests\test_stock_daily_preopen.py`（CRLF 866 行） | **既有測試的修補複本**（`_fake_predict` 加 6 行假日曆；v3.1 未改） | `7c8efe40ca31…6246` | `7c8efe40ca31…6246` | — | `ec6a48a88fc4…27cd` |
| `stage.diff` | 正式檔 → v3.1（difflib unified，路徑 `programs/shared/…`，含 reader；`git apply --check` 通過） | py +971／−66、reader +101／−1、cmd +71／−7 | | | |
| `ops_patch_v3.diff` | v2 → v3.1（reader 與既有測試以正式檔為基準） | py +198／−83、reader +101／−1、cmd +11／−7、stage 測試 +497／−124、既有測試 +6／−0 | | | |
| `ops_patch_v3_1.diff` | v3 → v3.1 | py +80／−21、reader +34／−5、cmd 0、stage 測試 +133／−1、既有測試 0 | | | |
| `cmd_run_v3\runA…runC.txt` | 暫存區 cmd 複本實際執行的 stdout／日誌（cmd 未改，仍適用） | | | | |
| `e2e_v31\` | v3.1 證據：`scenarios_v31.txt`（守門 23 例）、`e2e_predict_v31.txt`（predict 端到端 22 例）、三份 pytest 日誌、兩支腳本 | | | | |
| `v2\`、`v3\` | v2／v3 的三個檔、測試、CHANGES、diff、TEST_RESULTS 備份 | | | | |

##### manifest `calendar_gate` 區塊（v3）

`status`（`GATE_STATUS_ORDER` 中最嚴重的一項）與 `reasons[]`（全部成立者，依嚴重度）：`target_weekend`、`target_fixed_closure`、`target_declared_closure`、`target_verified_closure`、`target_not_after_signal`、`calendar_unavailable`、`stale_signal`、`signal_not_session`、`signal_gap_unverified`、`central_max_date_unknown`（v3.1）、`closures_file_invalid`、`closed_day_cli_invalid`（v3.1）；`ok` 時 `reasons=[]`。其他欄位：`policy`（`annotate`／`strict`）、`refuse`（只在 strict 且≠ok 為 true）、`reason_code`（v2 的 `preopen_*` 代碼，strict 拒發紀錄沿用）、`calendar_status`／`calendar_error_code`／`calendar_error`、`calendar_source`、`calendar_vintage`（v2 原樣）、`signal_is_verified_session`／`signal_is_last_verified_session`／`last_verified_session`、`target_calendar_status`（`verified_session`／`unverified_future`／`void_*`）、`target_closure_kind`、`target_not_session`、`sessions_ahead_verified`、**`sessions_ahead_expected`**（已驗證時為實際 session 數，否則 1 + 缺口中已驗證交易日與無法解釋平日的數目；v2 恆為 1）、`gap_days[]`、`declared_closures`／`declared_closures_cli`／`closures_file`／`closures_file_error`、`closed_day_invalid[]`（v3.1）、`allow_stale_signal`、`note_zh`；`central_price_max_date` 在中央缺 `actual_max_date` 時為 `null`（v3.1）。頂層另有 **`ledger`**（v3.1：`{status: ok|error|skipped, error, rows, path}`）。v2 的 `overridden`／`target_refused`／`signal_reason_code` 移除。頂層 `calendar_source`、`calendar_vintage`、`signal_is_last_verified_session`、`sessions_ahead_expected`、`degraded_reasons` 照舊；`degraded = 美股缺漏 or 籌碼 stale or gate≠ok`。

`load_latest` 不受影響：`snapshot_sha256`／`snapshot_csv_sha256` 的計算對象是 parquet／CSV 檔（`_sha256(snap)`），manifest 新增鍵不在雜湊範圍內；`REQUIRED_COLUMNS`、`US_STATUSES` 檢查亦與 manifest 新鍵無關（測試 `test_predict_publishes_stale_central_prices_with_annotation` 以 reader 讀回 v3 發布的 latest.json 驗證）。

##### reader `describe()`（v3，跨程式共用模組）

`calendar_gate_text(gate, meta)`：meta 有 `calendar_gate` 且 `status`≠`ok` 時，在籌碼說明之後、美股漲跌之前加一段（多個原因以「；」相接、status 在前；日期一律 YYYYMMDD）；沒有 `calendar_gate` 鍵（舊快照）、`status=ok`、或型別異常時文字與現行**逐字相同**（測試以 importlib 載入正式 reader 對照）。文字：

- `stale_signal`：「中央價格落後：本快照以 20261027 訊號日重發，目標日 20261029（跨 2 個交易日），結算時不計入實盤統計」（expected=1 時「（預期 1 個交易日）」；v3.1：reasons 含 `target_*`／`signal_not_session` 時只寫「中央價格落後：本快照以 20261022 訊號日重發，目標日 20261023」）
- `signal_gap_unverified`：「中央價格可能落後（20261028 不在已驗證日曆中、也非已知休市日）：本快照以 20261027 訊號日重發，目標日 20261029（可能跨 2 個交易日，結算時不計入實盤統計）；若 20261028 為臨時休市請宣告」
- `target_verified_closure`：「目標日 20261023 為已驗證日曆中的非交易日（可能為臨時休市），結算時不計分」；`target_weekend`／`target_fixed_closure`／`target_declared_closure`／`target_not_after_signal`：「目標日 … 為週末／固定假日／補假／已宣告休市日／不晚於訊號日的日期，結算時不計分」
- `signal_not_session`：「訊號日 … 不是已驗證交易日，結算時不計分」；`calendar_unavailable`：「已驗證日曆不可用（preopen_calendar_…），訊號日與目標日未經核對；目標日 … 為假設值，結算時以日曆為準」；`closures_file_invalid`：「宣告休市設定檔無法使用（已忽略）」；未知 status：「日曆守門狀態 <status>」
- v3.1：`central_max_date_unknown`：「中央價格最新日未知（head 中繼資料缺 actual_max_date），訊號日未與中央核對」；`closed_day_cli_invalid`：「命令列宣告的休市日格式無效（2026/10/28，已忽略）」；manifest `ledger.status=error`：在守門說明之後、美股漲跌之前加「帳冊寫入失敗（本快照未入帳，結算時不計分）」（`ledger_text()`；沒有 `ledger` 鍵、ok、skipped 或型別異常時文字不變）
- 新增 `PreopenSnapshot.calendar_gate` 屬性（dict 或 None）；`REASON_TEXT_ZH`、`REQUIRED_COLUMNS` 不變。

消費端（本次不改）：`programs\single_stock_analysis\src\single_stock_preopen.py` 直接呼叫 `reader.describe(meta)`，新文字會自動出現；其 `_META_KEYS` 與 TWNextDayTop10 的 `preopen_meta` 白名單不含 `calendar_gate`（只透過 `degraded` 與 describe 文字得知）。`programs\telegram_report_delivery\mix_report_sender.py` 只讀 `preopen_meta` 的 `signal_date`／`target_date`／`gate_us_up`。TWNextDayTop10（`D:\stock\TWNextDayTop10\preopen_source.py`，v3.1 核實）：`choose()` 第 362 行呼叫 `reader.describe()`，文字進入 `note()`（第 310 行）與 `preopen_meta['describe']`（第 301 行），`reporting.py` 第 713 行以 `html.escape` 輸出，無長度限制；`preopen_meta` 白名單（第 297-299 行）不含 `calendar_gate`／`ledger`／`degraded`，新鍵被忽略；快照雜湊只比對 parquet／CSV。**殘餘阻擋路徑（本次不可改 `D:\stock`，審查第 4 項）**：`confirm_target()`（第 397-409 行）在 C0 有已驗證下一交易日（`app.py` 第 62-76 行 `market_sessions`）且與快照 `target_date` 不同時，auto → 回退 C0（報告仍發布，H1 改用 C0），`--model-source preopen` → `PreopenRequiredError` 中止；`choose()` 的 `load_latest(expected_signal_date=asof)` 在 preopen 模式遇 `preopen_stale` 亦中止。中央價格落後而日曆領先（C0 axis 含 10-28）時，v3 快照的目標日為執行當日，兩者不一致即觸發。`RUN_TOP10.cmd` 預設 auto（只在明確傳 `--model-source preopen` 時才中止），預設排程不受影響。若裁示也適用於 Top10 的 preopen 強制模式，需另案讓 `confirm_target`／`preopen_stale` 在快照 `calendar_gate.status≠ok` 時只註記不中止（needs_user_decision）。未驗證：C0 的 `market_sessions` axis 是否真會領先價格路由（需中央實際資料）。

##### 行為矩陣（v3；守門判定與 v2 相同，處置改為註記）

| 情境 | 輸入狀態 | predict | exit | manifest `calendar_gate.status`／`degraded_reasons` | `describe()` 附加文字 | 結算分類（不變） |
|---|---|---|---|---|---|---|
| 正常 | 訊號日 = 日曆最後 session，目標日為下一個平日（10-30 → 11-02） | 發布 | 0 | `ok`／`[]`（`target_calendar_status=unverified_future`） | 無（與現行逐字相同） | `settled_h1`（sessions_ahead=1）計分 |
| 中央價格落後（10-29 情境，真實：同 head sidecar 日曆，最後 session 10-27 = 訊號日，目標 10-29） | 10-28 不在日曆內、非已知休市 | 發布 | 0 | `signal_gap_unverified`／`["signal_gap_unverified"]`，`sessions_ahead_expected=2` | 「中央價格可能落後（20261028 不在已驗證日曆中、也非已知休市日）：本快照以 20261027 訊號日重發，目標日 20261029（可能跨 2 個交易日，結算時不計入實盤統計）；若 20261028 為臨時休市請宣告」 | sessions_ahead=2 → `settled_multi_session`，不計分、不進校準池 |
| 中央價格落後（日曆領先價格：目錄掃描到含 10-28 的日曆） | 訊號日 10-27 < 日曆最後 session | 發布 | 0 | `stale_signal`／`["stale_signal"]`，expected=2（已驗證） | 「中央價格落後：本快照以 20261027 訊號日重發，目標日 20261029（跨 2 個交易日），結算時不計入實盤統計」 | `settled_multi_session` |
| 颱風休市隔日，無宣告（10-27 情境：訊號 10-22、目標 10-27、日曆到 10-22） | 10-23 無法解釋、10-26 為補假 | 發布 | 0 | `signal_gap_unverified`／`["signal_gap_unverified"]`，expected=2 | 「中央價格可能落後（20261023 …）：本快照以 20261022 訊號日重發，目標日 20261027（可能跨 2 個交易日…）；若 20261023 為臨時休市請宣告」 | 事後日曆確認 10-23 休市 → sessions_ahead=1 → `settled_h1` 計分（註記為保守說法） |
| 颱風休市隔日，有宣告（`--closed-day 2026-10-23` 或 `announced_closures.json`） | 10-23 `declared_closure`、10-26 `fixed_holiday` | 發布 | 0 | `ok`／`[]`，expected=1，`declared_closures=["2026-10-23"]` | 無 | `settled_h1` |
| 目標日週末（10-30 → 10-31） | 週六 | 發布 | 0 | `target_weekend`／`["target_weekend"]` | 「目標日 20261031 為週末，結算時不計分」 | `void_target_not_session` |
| 固定假日／補假（12-24 → 12-25；或**明確 `--target-date 2026-10-26`** 的 10-22 → 10-26） | 規則可推得。注意每日流程不給 `--target-date` 時 `resolve_target` 會跳過補假：10-26 早上實際產生目標 10-27、`signal_gap_unverified`（上一列「颱風休市隔日」情境） | 發布 | 0 | `target_fixed_closure`／`["target_fixed_closure"]`（10-26 案為 `["target_fixed_closure", "signal_gap_unverified"]`，expected=2） | 「目標日 20261225 為固定假日／補假，結算時不計分」；10-26 案後接「；中央價格可能落後（20261023 不在已驗證日曆中、也非已知休市日）：本快照以 20261022 訊號日重發，目標日 20261026（可能跨 2 個交易日，結算時不計入實盤統計）；若 20261023 為臨時休市請宣告」 | `void_target_not_session` |
| 已宣告休市日為目標 | `--closed-day`／設定檔 | 發布 | 0 | `target_declared_closure` | 「目標日 … 為已宣告休市日，結算時不計分」 | void |
| 目標日為已驗證日曆內的非交易日（事後重跑 `--target-date 2026-10-23`，訊號 10-22） | 日曆已含 10-23 休市（最後交易日 10-30 > 訊號日，必然同時是舊訊號重發） | 發布 | 0 | `target_verified_closure`／`["target_verified_closure", "stale_signal"]`（`target_calendar_status=void_target_not_session`） | 「目標日 20261023 為已驗證日曆中的非交易日（可能為臨時休市），結算時不計分；中央價格落後：本快照以 20261022 訊號日重發，目標日 20261023」（v3.1：不再補「預期 1 個交易日」） | void |
| 日曆不可用（中央日曆缺檔／sha 不符／格式錯） | `load_verified_calendar` 例外 | 發布（目標日照 `resolve_target` 規則） | 0 | `calendar_unavailable`／`["calendar_unavailable"(+gap)]`，`calendar_error_code` | 「已驗證日曆不可用（preopen_calendar_invalid），訊號日與目標日未經核對；目標日 20261019 為假設值，結算時以日曆為準」 | settle 日曆不可用時略過、列維持 pending（v2 A1） |
| 訊號日非交易日（中央發布了非交易日的價格；10-23 → 10-27） | 訊號日在日曆內但非 session | 發布 | 0 | `stale_signal`／`["stale_signal", "signal_not_session"]`（`target_calendar_status=void_signal_not_session`） | 「中央價格落後：本快照以 20261023 訊號日重發，目標日 20261027；訊號日 20261023 不是已驗證交易日，結算時不計分」 | `void_signal_not_session` |
| 中央 head 缺／無法解析 `finmind_price_adj.actual_max_date`（v3.1） | `central_price_max_date()` 回 None | 發布（訊號日取面板最後日、目標日照常） | 0 | `central_max_date_unknown`／`["central_max_date_unknown"]`，`central_price_max_date=null`；訊號日 < 日曆最後 session 時另加 `stale_signal` 在前 | 「中央價格最新日未知（head 中繼資料缺 actual_max_date），訊號日未與中央核對」 | 不變（依日曆結算） |
| 美股缺漏 | `us_overnight.status=missing` | 發布 | 0 | gate `ok`；`degraded_reasons=["us_overnight_missing"]` | 現行文字（前一晚美股收盤缺漏…） | 不變 |
| 籌碼 stale | `chips_freshness.status=stale` | 發布 | 0 | gate `ok`；`["chips_stale"]` | 現行文字（t 日籌碼未更新…） | 不變 |
| 宣告檔格式錯誤 | `config\announced_closures.json` 無效 | 發布（忽略宣告） | 0 | `closures_file_invalid`（或較嚴重者在前） | 「宣告休市設定檔無法使用（已忽略）」 | 不變 |
| `--closed-day` 格式錯誤（v3.1；例 `2026/10/28`，cmd `%*` 透傳） | `parse_closed_days` 無法解析 | 發布（忽略該值；同列其他正確的值仍採用） | 0 | `closed_day_cli_invalid`（最低嚴重度，或較嚴重者在前）；`closed_day_invalid=["2026/10/28"]` | 「命令列宣告的休市日格式無效（2026/10/28，已忽略）」 | 不變 |
| 帳冊 `state\forward.sqlite3` 被鎖定／損毀／`state` 不是目錄（v3.1） | `ledger()`／`INSERT` 拋 `sqlite3.Error`／`OSError` | 發布（快照、manifest、`latest.json` 照常；本次未入帳） | 0 | gate 不變；頂層 `ledger={status: error, error: "OperationalError: database is locked", rows: 0}`；**不標 degraded** | 「帳冊寫入失敗（本快照未入帳，結算時不計分）」（在守門說明之後） | 該 run 不在帳冊 → 不結算、不計分、不進校準池 |
| `--strict-calendar`（手動 QA） | 任一 status≠ok | **拒發**：`latest.json` 不更新、寫 `latest_refusal.json` | 1 | （拒發紀錄內 `calendar_gate.refuse=true`、`policy=strict`） | — | — |
| `--strict-calendar` 且 status=ok | | 發布 | 0 | `ok`、`policy=strict` | 無 | `settled_h1` |
| `--no-latest`（QA 快照） | 任一 | 快照寫出、不發布、不入帳 | 0 | 如實記錄（strict 時 `refuse=true` 也不中止） | — | — |
| 明確給錯 `--target-date`（≤ 訊號日） | 操作者輸入 | `SystemExit`（既有行為） | 1 | — | — | — |
| cmd：settle／calibrate／train 任一失敗 | 非 0 | 只記日誌、繼續到 predict；最終 exit = predict 的 | predict 的 | | | |
| cmd：日誌目錄不可寫 | `LOG_OK=0` | 四步仍執行、輸出到主控台 | predict 的 | | | |

##### RUN_PREOPEN_DAILY.cmd（v3）

流程與 v2 相同（settle → [calibrate] → train → predict；每步 `call :Step`，`:Step` 以 `exit /b %STEP_RC%` 回傳但不中止流程；`RC` 取自 predict、`exit /b %RC%`；`LOG_OK=0` 時改主控台）。v3 只改註解與訊息：usage 註明 `--allow-stale-signal` 為 no-op、說明 v3 一律發布、predict 非 0 代表真正失敗或手動 strict。靜態檢查：`test_cmd_static_flow_contract`（四步順序、步驟間無 goto／exit、RC 緊接 predict、命令列不含 `--strict-calendar`）。實際執行（`deploy\cmd_run_v3\`，暫存區複本、絕對路徑、獨特檔名 `RUN_PREOPEN_DAILY_V3_STAGE.cmd`、`find_python.cmd` 樁指向 `D:\stock\.venv`，未執行 `D:\stock` 的 .cmd）：

- Run A：**真實 v3 producer**、空資料根 + 無效 `models\current.json`（`{}`）：settle「nothing pending」exit 0 → train 在 `active_recipe` 讀 pointer 時 `KeyError` exit 1（在 `pdata.read_head` 之前，不碰中央、不建租約）→ predict `load_active` 以「unexpected pointer schema」exit 1（同樣在讀 head 之前）→ `== end (predict exit 1)`、cmd 結束碼 1。證明任一步失敗仍走到 predict、結束碼為 predict 的。未以真實 producer 載入中央資料（train／predict 一旦讀 head 就會在中央 `_query` 建立租約，本任務禁止寫入中央）。
- Run B：樁 producer，settle／train 回傳 1、predict 回傳 0 → 三步皆執行、結束碼 0。
- Run C：樁 producer，predict 回傳 5（`--closed-day 2026-10-23` 透傳）→ 結束碼 5。

##### 既有測試（`tests\test_stock_daily_preopen.py`）

未修改的正式測試檔對 v3 producer：**37 passed、1 failed、1 skipped**。失敗的是 `test_predict_manifest_marks_chips_freshness_and_require_flag`（第 725 行 `assert m["degraded"] is False`）：其 `_fake_predict` 固定訊號日 2026-10-16、未假造日曆，predict（`--no-latest`）掃描中央 `_calendar\verified` 取得最後交易日 10-30，v3 如實標 `stale_signal`、`degraded=true`。這是 v3 的預期行為（v2 因 `--no-latest` 只記錄守門、`degraded` 不含守門而通過），且該測試原本就依賴中央日曆而非固定資料。修補複本 `stage\tests\test_stock_daily_preopen.py` 在 `_fake_predict` 加 6 行：以 `producer.calendar_from_sessions` 假造「最後交易日 = 2026-10-16」的日曆（不再掃描中央，各 predict 測試省 0.4 秒）→ **38 passed、1 skipped**（與基準相同）。部署時請用此複本覆蓋 `programs\shared\tests\test_stock_daily_preopen.py`（`ops_patch_v3.diff` 最後一個 hunk，+6／−0）。

##### 測試結果（詳 `TEST_RESULTS.txt`）

- 基準：正式複本 38 passed／1 skipped；v2 暫存複本 58 passed／1 skipped。
- v3 暫存複本（整個 `programs\shared` 複本 + 三個暫存檔 + 修補的既有測試 + 新測試）：62 passed／1 skipped（38 + 24）；**v3.1：64 passed／1 skipped、0 failed（38 + 26）**。
- `deploy\stage\tests` 獨立模式（`PREOPEN_STAGE_SHARED_DIR=<正式複本>`）：v3 24 passed；**v3.1 26 passed**。
- 新增／改寫的測試：`test_calendar_gate_rules`（status／reasons／strict／expected）、`test_predict_publishes_stale_central_prices_with_annotation`、`test_predict_strict_calendar_refuses_and_default_run_clears_refusal`（新）、`test_predict_annotates_same_head_lag_until_closure_declared`、`test_predict_publishes_when_signal_is_last_verified_session`（週末、日曆不可用、無 `--target-date`）、`test_announced_closures_file`（格式錯不中止）、`test_help_lists_new_flags`、`test_cmd_static_flow_contract`（新）、`test_cmd_daily_flow_order_logging_exit_code_and_fallbacks`（加「settle／calibrate／train 全失敗仍執行 predict、RC 0」與「train 失敗 + predict 失敗 → RC 4」）、`test_reader_describe_old_snapshot_and_ok_gate_are_verbatim`（新；含與正式 reader 逐字對照）、`test_reader_describe_calendar_gate_texts`（新；三種文字與其他狀態）。

##### 部署步驟（v3；需使用者核准，本任務未執行）

1. 確認排程 03（07:15）不在執行中；備份 `programs\shared\stock_daily_preopen.py`、`stock_daily_preopen_reader.py`、`RUN_PREOPEN_DAILY.cmd`、`tests\test_stock_daily_preopen.py` 與 `D:\TWStockData\program_data\stock_daily_preopen\state\forward.sqlite3`、`state\summary.json`。
2. 複製 `stage\stock_daily_preopen.py`、`stage\stock_daily_preopen_reader.py`、`stage\RUN_PREOPEN_DAILY.cmd` 到 `programs\shared\`；`stage\tests\test_stock_daily_preopen.py` 覆蓋 `programs\shared\tests\`；`stage\tests\test_preopen_stage_ops.py` 放進 `programs\shared\tests\`，在 `programs\STABLE_PROGRAM_PATHS.txt` 登錄並執行 `programs\VERIFY_STABLE_PROGRAM_PATHS.cmd`。reader 是跨程式共用模組：`single_stock_analysis`、`telegram_report_delivery`、TWNextDayTop10 都會讀到新 `describe()`；另一研究軌 `..\single_stock\stage\shared\stock_daily_preopen_reader.py`（17,773 bytes）也改了 reader，兩者需合併後才能同時部署（本次未合併）。
3. 跑 `pytest programs\shared\tests\test_stock_daily_preopen.py programs\shared\tests\test_preopen_stage_ops.py programs\single_stock_analysis\tests\test_single_stock_preopen.py`（後者驗證 `describe()` 文字進入單股報告；本任務未跑單股測試）。
4. 手動執行一次 `stock_daily_preopen.py settle`（同 v2 步驟 4；結算端未改）。
5. （可選）建立 `<root>\config\announced_closures.json`；臨時休市以 `--closed-day` 宣告。v3 下未宣告也只是 manifest 註記與 describe 文字，不會擋發布。
6. 下一個交易日由排程跑 `RUN_PREOPEN_DAILY.cmd`；檢查 `<root>\logs\preopen_daily_<日期>.log` 與 `latest.json` 的 `calendar_gate.status`。**不要**在排程加 `--strict-calendar`。
7. 文件：`programs\shared\README.md` 開盤前版段落補上 `calendar_gate` 狀態碼、`--strict-calendar`、describe 文字（本任務不可改 D:\stock）。

##### 回復方式

- 程式：把備份的四個檔案複製回 `programs\shared\`（含 reader 與既有測試）。
- 帳冊：同 v2（舊版 `predict` 以 20 個位置參數 `INSERT`，加欄後會失敗；回復舊版請一併還原 `forward.sqlite3`）。
- 快照：v3 發布的 `latest.json` 多了 `calendar_gate` 等鍵，舊版 reader 忽略未知鍵、可照常讀取（舊 `describe()` 不顯示守門文字）。`latest_refusal.json` 只在手動 strict 時產生，對舊版無影響。
- 若只想回到 v2 的「預設拒發」：`deploy\v2\` 有 v2 的三個檔。

##### v3 已知差異與注意事項

1. `degraded=true` 的語意擴大：中央落後、缺口無法解釋、目標日非交易日、日曆不可用都算 degraded。消費端若以 `degraded` 決定是否標示「準確度可能下降」，這些日子會一併標示（這正是裁示要的「註明」）。
2. 颱風後第一個交易日早上若未宣告，manifest 與 describe 會說「可能跨 2 個交易日」；事後結算以日曆為準（10-23 非交易日 → sessions_ahead=1、照常計分），註記只是保守說法。宣告（`--closed-day`）可讓當天的 gate 為 `ok`。
3. 中央落後重發的舊訊號仍會進帳冊（`pending`），結算時以 sessions_ahead≥2 標 `settled_multi_session`，不計分、不進校準池——與 v2 相同；消費端在中央落後時仍會沿用前一日快照（`preopen_stale` 不會觸發，見 v2「消費端行為」），v3 不改變這點。
4. 守門的判定邏輯（同 head 時「訊號日 = 日曆最後 session」不能證明中央沒落後、要靠缺口規則）與 v2 相同；v3 只把處置從拒發改為註記。
5. `SystemExit` 字串訊息的結束碼為 1；strict 拒發與真正失敗的結束碼相同，以 `latest_refusal.json` 是否存在區分。
6. （v3.1）帳冊寫入失敗的 run 不會進 `forward.sqlite3`，結算端與摘要自然不計它；`ledger.status=error` 只在 manifest／`latest.json` 與 describe 文字可見，不標 degraded。若要事後補入帳，需手動重跑 predict（新 run_id）或另寫補帳工具（本次未做）。
7. （v3.1）`closed_day_cli_invalid` 與 `central_max_date_unknown` 也算 gate≠ok → `degraded=true`（與 `closures_file_invalid` 一致）；`--closed-day` 一次給多個值時只忽略錯的那些。
8. （v3.1 觀察，未改）中央落後多日時 `signal_gap_unverified` 的 describe 文字會逐一列出全部無法解釋的平日（例：落後兩週 → 9 個日期、文字很長），屬 v3 既有行為；若要縮短需另案。

---

以下為 v2（2026-10-31，兩位審查者修補版）的變更說明，保留作為歷史與結算端／摘要／cmd 其他改動的依據；**A3「預設拒發」的處置已被上面的 v3 取代**（判定邏輯與欄位仍有效）。

## 附錄 B：暫存版變更說明——v2 逐項改動中仍現行的段落（A1 結算正確性、A2 摘要口徑、A4 cmd、A5 衛生、相容性、20 項審查處置、事故紀錄）

來源：`D:\TWStockData\program_data\stock_daily_c0\candidates\h1_ops_review_20261031\deploy\CHANGES_stage.md`；收錄範圍：「逐項改動（v2 現況；標 ★ 為 v2 修補時新增或改寫）」的 A1／A2／A4／A5 四節、「相容性」、「審查發現處置（20 項）」、「事故紀錄」。

【註 1】v2 的 A3（預設拒發）、v2 部署步驟、v2 回復方式、v2 已知差異與「消費端行為」一節**未收錄**，因已被 v3.1（附錄 A、主文 §3）取代。

【註 2】下列敘述為 v2 當時狀態，v3.1 已不同：A4 第 5 點「註解改寫：拒發時 `latest.json` 不更新、寫 `latest_refusal.json`」→ v3.1 預設一律發布、`latest_refusal.json` 只在手動 `--strict-calendar` 時產生，cmd 註解改為一律發布、`--allow-stale-signal` 為 no-op（附錄 A「RUN_PREOPEN_DAILY.cmd（v3）」）；「相容性」的「reader 未改」「新測試 20 passed」→ v3.1 reader +101／−1（主文 §4）、新測試 26 個、64 passed（附錄 E）；20 項表第 1、3、6、18 項的處置（`preopen_signal_gap_unverified` 拒絕規則、predict 拒發時寫 `latest_refusal.json`、`target_refused`、「可覆蓋」、reader／白名單未改）→ v3.1 改為只註記的 `signal_gap_unverified`、移除 `target_refused`／`overridden`／`signal_reason_code`、reader `describe()` 已加守門與帳冊文字（附錄 A manifest 區塊）；「相容性」所述既有 predict 測試仍掃描中央日曆 → 修補複本已改假日曆（附錄 A「既有測試」）；A2 的消費端盤點（只有 `cmd_status` 讀 `state\summary.json`）→ 單股軌部署後 `single_stock_preopen.py` 會經 reader `load_forward_summary()` 讀它（附錄 F、G；鍵名對齊見主文 §7.4）。A4 所述 calibrate 預設關閉是相對 v2 暫存版而言，正式 cmd 本來就沒有每日 calibrate（主文 §3 第 5 點）。

【註 3】未收錄的 v2「消費端行為」要點：`stock_daily_preopen_reader.load_latest` 只比對快照 `signal_date` 與消費端的 `expected_signal_date`；TWNextDayTop10 與單股分析的 expected 都來自同一個中央的最新交易日，中央落後時兩者同為舊日期，所以 `preopen_stale` 不會觸發、消費端沿用前一日快照；`confirm_target` 在日曆沒有未來 session 時不比對目標日。此即附錄 A「v3 已知差異」第 3 點的依據。

【註 4】未收錄的 v2 A3 中仍有效的說明：strict 拒發紀錄 `predictions\latest_refusal.json` 的 schema 為 `tw-stock-preopen-refusal-v1`（refused_at、signal_date、target_date、reason_code、note_zh、calendar_gate、central_head_sha256、model_sha256）；宣告檔格式見主文 §8.8。

#### A1 結算正確性（`cmd_settle`、`settle_rows`、帳冊 schema）

問題：原 `UPDATE … WHERE signal_date=? AND stock_id=? AND model_sha256=?` 鍵不含 `target_date`／`run_id`，同訊號日有多個 target 時後寫者覆蓋前者（10-22：target 10-23 颱風休市與 10-27；10-27：target 10-28 與 10-29 → 全部 1,919 列被寫成兩日報酬）。

- 以 rowid 逐列更新；帳冊新增 `sessions_ahead INTEGER`、`settlement_status TEXT`、`settlement_source TEXT`（既有帳冊 `ALTER TABLE … ADD COLUMN`）。
- 狀態：`pending`／`settled_h1`／`settled_multi_session`／★`legacy_unverified`／`void_target_not_session`／`void_signal_not_session`／`void_target_not_after_signal`。
- target 不是已驗證 session → void、`observed_return` 清空、★`settlement_source='calendar_only'`（未讀任何價格）；target 晚於日曆範圍 → 維持 pending。
- ★ 價格來源缺兩端收盤但舊值存在 → `legacy_unverified`（`settlement_source='legacy_kept'`，保留舊值但**不計分、不進校準池**；舊值可能來自舊鍵覆蓋 bug）。v1 把這種列標成 settled_h1 並計分（審查第 2 項）。
- ★ 重算範圍：預設只處理 `settlement_status IS NULL`（舊版未分類列）或 `pending`；void／已結算／legacy 列只有 `--resettle` 才重算（v1 的 `observed_return IS NULL` 條件讓 void 列每天被重寫）。
- ★ 冪等：只有值／狀態／sessions_ahead 改變的列才寫入；值未變的列不改 `observed_at`（保留首次結算時間），`--resettle` 重跑未變的列也不寫（`counts.unchanged`／`written`）。
- `predict` 入帳改為明確欄位清單，新列 `settlement_status='pending'`。
- `SCORABLE_SQL`（summary、校準池）：★`settlement_status='settled_h1' AND observed_return IS NOT NULL AND prospective=1 AND sessions_ahead=1`。
- ★ `cmd_settle` 在日曆不可用（sidecar 被汰除且目錄無可驗證檔、proof 損毀）時不中止：略過結算、只重寫 summary（`summary.settle_skipped` 記錄原因），exit 0。
- 價格來源：`--price-source panel`（預設，如舊：拼接兩個 head 的價格面板，記憶體與舊版相同）；`--price-source cache`（★ 直讀目前 head 的 finmind 快取 **並以同 head 的 official_market_daily 快取過濾官方收盤價 > 0 的 (檔, 日)**，與 panel 路徑同義、記憶體極小）；`--price-cache PATH…`（明確檔案，不讀中央 head）★ 搭配 `--official-cache PATH…` 做同樣過濾，未給則記錄警告（無成交日會被結算成 0% 報酬）。副本試算證實：過濾後 cache 與 panel 逐列相同；未過濾時每日多結算約 20 列、其中 132/148 列為 0% 報酬（`dryrun\DRYRUN_RESULTS.md`）。

#### A2 摘要口徑（`forward_summary` → schema `tw-stock-preopen-forward-summary-v2`）

- 只用 `settled_h1`、`prospective=1` 的列；`all` 區塊保留 v1 全部鍵（口徑＝最後一次發布）並加 `up_rate`、`mean_p_up`、`prob_bias`、`auc`、`top10_mean_ret`、`days_insufficient`、`publication_rule`、`sessions_ahead`。消費端盤點：`D:\stock` 與 `D:\MIX All` 只有 `stock_daily_preopen.py` 自己的 `cmd_status` 讀 `state\summary.json`。
- `views.last_publication` 與 ★`views.first_scorable_publication`（v1 名為 `first_publication`；它是每 (signal_date, stock_id) 最先的**可計分**列，不等於消費端最先看到的發布）各含 `overall`、`daily`、`by_model`、`by_us_status`、`bootstrap`（以日為單位、seed 20261031、2000 次、90%）。
- ★ 逐日表新增 `first_seen_run_id`／`first_seen_target_date`／`first_seen_status`／`first_seen_recorded_at`：該訊號日消費端實際最先看到的 run（不論結算狀態；status 取該 run 最多列的狀態）。
- `excluded`：各非計分狀態列數（含 ★`legacy_unverified`）。樣本日數 < 21 → `days_insufficient=true`。
- `state\forward_daily.csv`（兩口徑逐日表）★ 改為暫存檔 + `os.replace` 原子寫入。

#### A4 cmd（`RUN_PREOPEN_DAILY.cmd`）

- ★ 順序 settle → train → predict；**calibrate 不在預設每日流程**（校準器在 train 內每 21 個 session 重算，與研究引擎 `calibrate_oos` 逐 fold 校準的頻率一致，ADR-0008 亦未授權每日校準；審查第 13 項）。`STOCK_DAILY_PREOPEN_CALIBRATE_DAILY=1` 可加入每日 calibrate（未經驗證，需使用者決定）。
- 每步 stdout/stderr 追加到 `<root>\logs\preopen_daily_<YYYYMMDD>.log`；★ 日期戳的 Python 一行不含 `%`（v1 的 `%%Y%%m%%d` 在環境有 `Y` 變數時被子 cmd 展開成 `BADm31`）；★ 資料根由 `stock_daily_preopen_reader.root_dir()` 取得（單一定義處），Python 失敗時才退回 cmd 內建預設。
- ★ 日誌不可寫（目錄建不出、權限）時 `LOG_OK=0`：四步仍執行、輸出改到主控台（v1 會讓四步全部不執行）。
- 結束碼：predict 的結束碼；settle／calibrate 失敗只警告並繼續，train 失敗保留現役模型。
- ★ 註解改寫：拒發時 `latest.json` 不更新、寫 `latest_refusal.json`；不再宣稱「消費端依 preopen_stale 改用 C0」。
- ★ 煙霧測試自動化為 pytest（樁模組、獨立暫存樹、絕對路徑、獨特檔名 `RUN_PREOPEN_DAILY_STAGE_SMOKE.cmd`）：順序、含括號的 `--authorization`、`--closed-day` 透傳、日誌內容、結束碼、`Y=BAD`、日誌目錄不可用。

#### A5 衛生（`cmd_predict`）

- `min_hist` 與 `tradable` 門檻改用 `recipe_of(meta)`；★ 測試改為分別驗證（tradable 以 `min_amt20` 4e9 讓 6 檔只剩 1 檔可交易；min_hist 另測）。
- `cmd_calibrate` 補 `current.json` 缺檔錯誤。

### 相容性

- 既有帳冊：開啟時自動加欄，舊列 `settlement_status=NULL` 視為未分類，第一次 settle 重算；回復舊版程式時帳冊要一併還原備份（舊版 `predict` 以 20 個位置參數 `INSERT`）。
- `summary.json` v1 → v2；`all` 區塊鍵名保留。快照 manifest 只新增鍵；reader 未改。
- 既有測試 `tests\test_stock_daily_preopen.py` 38 passed／1 skipped 不變；新測試 20 passed（`TEST_RESULTS.txt`）。既有 predict 測試仍會以 `--no-latest` 掃描中央 `_calendar\verified`（唯讀，約 0.4 秒）；新測試全部 monkeypatch `load_verified_calendar`，真實日曆測試以 `PREOPEN_REAL_CALENDAR` 門檻（不再硬編碼研究目錄路徑）。
- ★ 測試檔匯入順序：暫存版目錄一律排在相依模組目錄之前，且若同名模組已被別處匯入會重新匯入（pytest 從複本根目錄執行時 v1 會誤匯入正式檔）。

### 審查發現處置（20 項）

| # | 發現 | 處置 | 說明 |
|---|---|---|---|
| 1 | A3 擋不住真實 10-29（日曆與價格同 head） | fixed | 確認屬實（head `599cb26dd718` sidecar `7f791410` 最後 session 10-27）；加 `preopen_signal_gap_unverified` 缺口規則＋`--closed-day`／`announced_closures.json`；以同 head 的事故 fixture 補回歸測試（gate 與 predict 各一）；CHANGES 如實改寫。 |
| 2 | panel 路徑 legacy_kept 以 settled_h1 計分（13 列） | fixed | 確認屬實（panel 模擬 13 檔完全相同）；新狀態 `legacy_unverified` 不計分、不進校準池；SCORABLE_SQL 改為 `settlement_status='settled_h1'`；副本以 panel 模擬重跑並納入 DRYRUN_RESULTS；部署預期值改用 panel 數字。 |
| 3 | 拒發後消費端不會改用 C0 | fixed（部分） | 確認屬實；cmd 註解與 CHANGES 改寫；predict 拒發時寫 `latest_refusal.json`；reader 未改（跨程式共用，列入 needs_user_decision）。 |
| 4 | sidecar 失效不退回目錄掃描；settle 直接失敗 | fixed | sidecar 失敗→記錄原因並掃描目錄；`cmd_settle` 捕捉日曆錯誤只寫摘要；測試 `test_load_verified_calendar_falls_back_from_broken_sidecar`、`test_settle_skips_without_calendar…`。 |
| 5／14 | void 列每日重寫 observed_at | fixed | 預設 WHERE 改為 `status IS NULL OR 'pending'`；未變列不寫；void 的 settlement_source=`calendar_only`、pending 為 NULL；`ledger_rows` 納入 observed_at 並斷言兩次 settle 與 `--resettle` 後完全相同。 |
| 6 | 訊號日非 session 被歸成目標日錯誤 | fixed | `target_refused` 只看 void_target／void_order；void_signal 歸訊號側（可覆蓋）；測試補案例。 |
| 7 | cmd `%%Y%%m%%d` 被展開 | fixed | 改用 `isoformat().replace('-','')`；煙霧測試以 `Y=BAD` 驗證。 |
| 8 | 日誌重導向失敗阻斷四步；根路徑重複定義；CSV 非原子 | fixed | `LOG_OK` 探測＋無日誌時改主控台；根由 `root_dir()` 取得；CSV temp+`os.replace`；煙霧測試以不可寫目錄驗證。 |
| 9 | 測試覆蓋缺口與命名高估 | fixed | (a) 同 head 事故 fixture；(b) tradable 以 min_amt20 獨立驗證；(c) `now_utc` monkeypatch；(d) panel／cache 路徑、legacy、cmd 日誌與結束碼皆有 pytest；(e) 新測試統一 monkeypatch 日曆，真實日曆改環境變數門檻。 |
| 10 | 文件不準：「目前 head」、首次 settle 預期值、docstring 研究路徑 | fixed | 改為 head `3a1a06e6`（到 10-29）；預期值改 panel 數字；docstring 改指向 README。 |
| 11 | 同 1（governance 版） | fixed | 同 1；另加 `calendar_vintage.same_head_as_prices` 與 `signal_is_last_verified_session` 語意說明。未採「要求中央提供 future_sessions 後再啟用」，因缺口規則不依賴未來 session。 |
| 12 | TW_FIXED_HOLIDAYS 不完整 | fixed | 補 9/28、10/25、12/25（2025 起）、清明、兒童節同日規則、週六／週日補假；以 1,232 個 session 核對零誤判；農曆／彈性／颱風改由宣告檔處理並在 CHANGES 寫明覆蓋範圍。 |
| 13 | 每日 calibrate 偏離研究驗證頻率 | fixed | cmd 預設不每日 calibrate（維持 train 內每 21 session）；`STOCK_DAILY_PREOPEN_CALIBRATE_DAILY=1` 可開；列入 needs_user_decision。 |
| 15 | 部署預期值只在 cache 模式驗證；快取非目前 head | fixed | panel 模擬重跑；cache 模式加官方收盤過濾後與 panel 相同；文件改寫 head 對應。 |
| 16 | 事故影響範圍描述不精確 | fixed（文件） | 事故紀錄補上租約／鎖檔副作用、06:24–06:28 時間窗與不可歸因的 06:35／06:45 租約活動；本次工作期間另有 07:39:14 `_objects\_query\…\leases` 目錄 mtime 變動，本任務程式路徑不呼叫 read_head／load_object（測試皆 mock、模擬直讀 parquet），無法由 mtime 歸因。 |
| 17 | 新測試未登錄固定路徑；依賴研究目錄日曆 | fixed | 部署步驟加登錄與 VERIFY；真實日曆測試改 `PREOPEN_REAL_CALENDAR`（未設則略過）。 |
| 18 | 覆蓋發布消費端看不到 | fixed（部分） | manifest 加 `degraded_reasons`；reader／消費端白名單未改（列入 needs_user_decision）。 |
| 19 | first_publication 語意 | fixed | 改名 `first_scorable_publication`；逐日表加 `first_seen_*`；CSV 的 publication 值同步改名。 |
| 20 | 核實紀錄（非問題） | acknowledged | 無修改；本次重算數字與其核實一致（未過濾 cache 口徑 0.6268／0.35 即其所核實者；修補後主口徑改為 0.6245／0.35）。 |

### 事故紀錄（製作者 v1，必讀；接手者補充）

- 06:24–06:28 本機：製作者為煙霧測試暫存版 cmd，以 `cmd /c RUN_PREOPEN_DAILY.cmd` 相對檔名呼叫，Bash 工具的工作目錄仍是 `D:\stock\programs\shared`，**誤執行正式 `RUN_PREOPEN_DAILY.cmd` 兩次**。當時 `STOCK_DAILY_PREOPEN_ROOT` 指向暫存目錄，所有資料寫入（空帳冊、v1 配方模型 `ff96896c…`、快照、563 MB 物件快取）都在暫存目錄且已刪除；正式資料根未變動（`forward.sqlite3`／`latest.json` mtime 10-30 07:17、`current.json` 10-22）。
- 接手者補充：誤執行的舊版 train／predict 透過 `read_head`、`load_object`、`prune_preopen_cache(apply=True)` 必然在中央 `_query` 建立過租約與鎖檔（協定內寫入，非資料變更）；中央 06:15 後的 mtime 變動只有 `_objects\_query\…\leases`（06:35:43）與 `_query\…\leases`（06:45:12），晚於製作者 06:31 的檢查，目前無殘留檔，無法歸因（同時段有其他研究子任務）。代價：訓練一個模型（15 秒）、predict 記憶體一度 5.9 GB。中央更新器 run `20261030T195351Z-eb363170` 於 06:13:27 結束，與事故無關。
- 工作規範（已落實到測試）：執行任何 .cmd 一律絕對路徑＋獨特檔名＋獨立暫存樹；本次接手者的 cmd 煙霧測試全部經 Python `subprocess` 以絕對路徑執行，未執行任何 D:\stock 的 .cmd。接手者一次以 Bash 直接呼叫 `cmd.exe /d /c <scratch>\stamp_probe.cmd` 的探測因引數解析開啟了互動式 cmd 等待 stdin（背景、閒置，未碰任何資料），由工具環境在工作結束時回收。

## 附錄 C：模型盤點與優化建議（總報告）——現況盤點、問題清單、候選清單、個股 H63 暫存版、建議順序

來源：`D:\TWStockData\program_data\stock_daily_c0\candidates\h1_ops_review_20261031\report\模型盤點與優化建議_20261031.md`；收錄範圍：§0 一句話結論、§1 現況盤點（1.1～1.5）、§2 問題清單、§3 候選清單、§4.3 個股 H63 誠實化暫存版、§5 建議的執行順序與待決事項。

【註 1】未收錄：報告檔首、§4 引言、§4.1（暫存版；內容已在主文與附錄 A）、§4.2（Top10 事後分析，純分析，原檔 `..\analysis\top10_posthoc.md`）、§4.4（事故紀錄，內容同附錄 B「事故紀錄」）、§4.5（模型端實驗，與部署無關）、§6（證據索引，同主文 §12）。§5 第 1 點的「第 1、2、7 項」指 §3 候選清單的編號。帳冊數字已於 2026-11-01 更正為過濾後口徑，與主文 §2 一致。

【註 2】§3 第 7 項「每日 calibrate＋漂移監控（本次已納入暫存版）」與 §5 第 1 點「部署第 1、2、7 項」：暫存版只以環境變數 `STOCK_DAILY_PREOPEN_CALIBRATE_DAILY=1` 提供 calibrate 步驟、預設關閉（主文 §3 第 5 點、附錄 B A4），部署不表示啟用每日校準，是否啟用為使用者待決。

【註 3】§3 第 6 項與 §5 第 4 點所稱「未提交的 `reporting.py`／`tw_single_stock_c0.py` 修改」已於 10-31 commit `afd28ab` 提交，目前工作樹除本交接檔外乾淨（主文 §6）；§5 第 1 點 (d) 的 reader 合併已完成（附錄 F）；(a)～(c) 列為主文 §7-B 第 11 點。

【註 4】§1.5 的環境狀態（更新器 PID 23652 佔 40 GB、「head 價格只到 10-29」、「writer lock 被手動 run 佔用」）為 10-31 當時：目前 head 價格到 10-30（主文 §2 及補記），10-30 23:00 任務中止原因主文列為 Not verified、由 §7.6(a) 以事件記錄核對。

【註 5】§1.2 表的「第一次前瞻發布 40.0%」即主文 §7-B.8 的替代口徑（結算端未實作），不是 summary v2 的 `views.first_scorable_publication`（35%）。

### 0. 一句話結論

- **H1 模型本身不是瓶頸**：開盤前版 v2（`h1pre_v2_ens`）已經過約 114 組變體的 walk-forward 研究，研究者自己的結論是「現有資料下已進入報酬遞減」；保留期 2026-01～10 方向 60.0%（一律猜跌 56.1%）、AUC 0.619、可交易 Top10 命中 57.1%。
- **真正扭曲「更新資料所得結果」的是管線與資料問題**：(1) 結算程式的 UPDATE 鍵寫錯，10-27 訊號日全部列被寫成兩日報酬，summary 的 Top10 26.7%／方向 0.600（低於猜跌 0.649）是錯的，修正後約 35%／0.625（猜跌 0.613）；(2) 沒有交易日曆與價格新鮮度守門，10-23 颱風休市日仍發布、10-29 中央價格落後時重發 10-27 舊訊號、10-28 訊號日完全沒有預測；(3) 前一晚美股資料 10 次發布缺 5 次，缺漏時等於換了一個模型（同訊號日兩份 Top10 重疊 0/10），而且 10-23、10-26 兩個美股交易日在中央永久缺漏，下次自動重訓（推算 2026-11-24）會靜默吃到錯的 `on_*` 特徵。
- **個股 H63（約三個月）沒有任何勝過基準的模型**（方向 58.2% < 一律猜跌 59.9%、報酬 MAE 劣於猜零；全母體回溯 AUC≈0.50），報告卻以「上漲機率 49%／預估報酬 +8.3%」當主表呈現；MIX 的 H5／H20／H60 同樣是 C0 常數基準。
- 乾淨（未被任何研究看過）的訊號日只剩 10-27、10-28、10-29 三天；任何 v2.1 候選都無法在現有資料上做乾淨驗證，只能靠前瞻帳冊累積（≥21 個結算日）。

### 1. 現況盤點

#### 1.1 次日 Top10 的 H1（開盤前版 v2）

| 項目 | 現況 |
|---|---|
| 模型 | sklearn HGB 集成：5 個分類器＋5 個迴歸器（3 種子 504 日窗、756 日窗、近期加權 504 日窗）＋3 個日層級 ridge 大盤估計；111 個特徵（價量 34、成交 9、籌碼 32、營收 3、相對 9、市場脈絡 14、前一晚美股 9、is_twse）；標籤 y1＝下一交易日調整後收盤報酬、yx1＝扣同日均值 |
| 校準 | Platt（機率）＋LAD 收縮（報酬），250 日窗；只在每 21 個交易日重訓時重擬，每日流程沒有呼叫 `calibrate` |
| 啟用 | 2026-10-22（模型 `3bc36b6c`，訓練列 1,380,221，2023-09-06～2026-10-19）；10-16～10-21 為 v1（`47573512`） |
| 研究證據 | `h1_improve_20261021`：dev 2023-07～2025-12 選模，保留期 2026-01-02～10-19 一次性評估：v2 對 v1 +1.04 pp [+0.20, +1.81]；6 項事前條件 5 項通過（Top10 MAE +0.19 pp 未過），使用者裁決部署 |
| 研究飽和 | v2 之後 44 組＋第七～九批實驗無一通過採用條件；已證無效：橫斷面標籤、海外前一日收盤、移除籌碼、小模型、不抽樣、每 10 日重訓、isotonic、stack2、依美股分組校準、日層級位移、美股缺值擴增、遮罩備援、無美股子模型 |
| 未試但有潛力 | 報酬頭新大盤模型＋lad4（唯一有正向區間的內部改動，Top10 MAE −0.02～−0.03 pp，不改方向）；`yo1`（開盤買進→收盤）作第二頭（研究者主動排除未試）；依美股方向分開訓練（待定）；lin_rank 線性成員（+0.18 pp，t 2.03） |
| 需要新資料才可能突破 | 台指期夜盤、台積電 ADR（AUC +0.002，研究者判定成本效益不足）；籌碼集中度（券商分點、TDCC）在中央已退役或無歷史 |

#### 1.2 實盤帳冊（2026-10-16～10-27，6 個結算訊號日）——「依更新資料所得的資料」

| 口徑 | Top10 命中 | 方向準確率 | 一律猜跌 | AUC | 說明 |
|---|---:|---:|---:|---:|---|
| summary.json（現況，錯誤） | 26.7% (16/60) | 60.0% | 64.9% | 0.618 | 10-27 全部列為兩日報酬；10-22 第一 run（target 10-23 休市）被寫成 10-22→10-27 |
| 修正 1 日報酬、最後一次發布 | 35.0% | 62.5% | 61.3% | 0.659 | 第一階段 live_ledger 代理重算 |
| 修正 1 日報酬、第一次前瞻發布 | 40.0% | 64.7% | — | — | 使用者實際看到的是第一份名單 |
| 保留期期望（研究） | 57.1% | 60.0% | 56.1% | 0.619 | 6 日視窗 Top10 命中 p5 約 40%、最低 28.3% |

- 逐日：10-16 acc 83.3%（猜跌 83.3%，大跌日）、10-19 55.3%／69.9%、10-20 52.1%／42.2%、10-21 66.2%／79.9%、10-22 第一 run 60.2%（第二 run 46.8%）、10-27 修正後 71.1%（Top10 10/10）。
- 本期 6 日平均上漲比例 35%，落在保留期最差 9%；保留期 Top10 命中與當日廣度相關 0.73（up_rate<0.35 的日子 Top10 平均只有 34%）。**6 日 bootstrap 區間太寬（Top10 [7%, 48%]），不能判定模型失效，也不能判定正常。**
- p_up 系統性高估約 +12 pp（分箱 0.3～0.6 實際低 6～19 pp，ECE 0.119 vs 保留期 0.014）：主因是弱勢盤＋Platt 全域單調映射無法做日層級位移；研究已證日層級位移無效，每日重擬校準只會改善監控與一致性，不會改排序。
- 美股缺漏：6 個結算日中 4 日 `us_status=missing`；只有 10-21 一天閘門為「美股收漲」（Top10 0/10）。
- C0 備援帳冊自 10-08 起沒有新訊號日（forward_write=skipped），備援沒有可比較的實盤紀錄。

#### 1.3 個股分析

| 期限 | 來源 | 證據 |
|---|---|---|
| H1 | 開盤前版快照本股列（主顯示）＋選定候選模型（備援對照） | 同 1.1／1.2；報告揭露只引用保留期數字，不顯示實盤 |
| H63 | `selected_current.json`（2026-10-17 啟用的凍結候選：50 檔抽樣、18 個月窗、擴充線性 Logistic／Ridge） | 唯一樣本外：20 個重疊訊號日×50 檔，方向 58.2% < 猜跌 59.9%，MAE 14.04 pp 劣於猜零 13.03 pp；全母體回溯（`window_comparison_20261025\h63`）AUC 0.50～0.52；選模規則 `baseline_eligible=false` 保證「真實模型」被選中；啟用後零前瞻結算 |
| C0 | 7 價格特徵線性，所有期限常數（H1 0.4359、H63 0.4938、報酬 0），`stock_ranking_signal=false`、`model_research_stopped=true` | MIX H5／H20／H60 仍讀 C0 |

報告層問題：`payload.prediction.research_only=true／deliverable=false` 與 `payload.research_only=false／promotion=true／deployment_status=active` 自相矛盾；第六章「本股市場方向準確率 60.00% vs 基準 40.19%」是單股樣本誤導列；同一份報告有兩組方向不同的 H1 數字。

#### 1.4 資料面

- 中央現行 head 實際發布 25 條路由（文件寫 48）；開盤前版用 10 條、個股選定模型用 15 條；已入庫但無模型使用：財報三表、股利／除權息、重大訊息、產業總經觀測、匯率、公債、TDCC 集保分級、處置／交易限制、產業鏈資金流、財報申報期限。
- `finmind_price_adj` 現行物件在 2022-08-30～2025-04-28 有空洞，5 年訓練窗完全依賴可被回收的歷史 head `a807be3e`（其 29 個其他物件已實體退役）。
- `global_market_history` 缺 2026-10-23、10-26 兩個美股交易日（wti／gold 另缺 10-20），更新器未登記為缺口；`build_panel` 的 `on_*` 以 `level_asof` 回退，不會變 NaN、無警示。
- `finmind_stock_info` 只有現況快照（產業、市場別），5 年訓練集存在存活者偏誤與產業欄輕微前視（每次 head 更新約 13.4% 股票產業標籤翻轉）。
- 10-23 颱風休市在中央只以「缺席」表示；已驗證日曆（`_calendar\verified`）沒有未來 session，守門只能擋「訊號錯」，不能事前證明 target 是下一交易日。

#### 1.5 環境與治理

- 機器 i7-13700K 16C/24T、64 GB；研究每個 walk-forward 槽約 4.5 GB。10-31 03:47 起 Codex 手動啟動的中央更新器（PID 23652）佔 40 GB，可用記憶體剩約 9 GB → 重運算實驗須等它結束。
- 10-30 23:00 中央完整更新被中止（writer lock 被手動 run 佔用），head 價格只到 10-29；10-29 訊號（1,916 列）尚未結算。
- 研究慣例：`candidates\<主題>_<日期>\`，以 `h1_refresh_20261029` 的「複製正式模組＋provenance」樣式最可重現；`h1_refresh` 快取面板（2,003,102 列×185 欄，到 10-27）與 `h1_improve` 引擎、174 組 OOS 預測可直接重用。
- 更新器契約 `application_capture_requirements_v1.json` 綁定 v2 配方與產製端程式雜湊：任何配方變更不同步契約，23:00 中央更新會整批擋下。
- AGENTS.md 指定的 `dstock_daily\model\calibration_history.jsonl` 路徑不存在；特徵一致性測試因研究快取目錄消失而永遠 skip；正式驗收 `acceptance\latest.json` 自 2026-08-23 為 `blocked`。

### 2. 問題清單（依嚴重度）

| # | 嚴重度 | 問題 | 影響 | 證據 |
|---|---|---|---|---|
| P1 | 高 | `cmd_settle` UPDATE 鍵缺 target_date／run_id，後寫者覆蓋 | 10-27 全部列兩日報酬、10-22 第一 run 寫成 10-22→10-27；summary 失真；錯誤列會進下次重訓的校準池 | `stock_daily_preopen.py` L736-762；帳冊 1,916/1,919 列 |
| P2 | 高 | `resolve_target` 不查交易日曆；predict 不檢查訊號日＝中央價格最大日 | 10-23 休市日發布並入帳；10-29 重發 10-27 舊訊號；10-28 訊號日缺失；Top10 照常產出名單 | 帳冊 run 清單、Top10 manifest |
| P3 | 高 | 前一晚美股缺漏頻繁（5/10），任一指數落後即 9 個 `on_*` 全設 NaN；10-23／10-26 缺口未回補 | 缺漏時名單與正常名單幾乎無關；下次重訓面板 `on_*` 靜默錯誤 | 10-22 兩 run 比較；`global_market_history` 物件 |
| P4 | 高 | 個股 H63 無有效模型卻以點估計呈現；旗標矛盾 | 使用者可能把 +8.3% 當預測 | acceptance／window_comparison 數字 |
| P5 | 中 | 校準不在每日流程；summary 混算 v1/v2、無拆解、無區間、無偏移監控 | 無法監控漂移；揭露只引用保留期 | `RUN_PREOPEN_DAILY.cmd` |
| P6 | 中 | 消費端（Top10／單股）不區分 degraded 快照 | 降級名單以同樣版面呈現 | `preopen_source.py` 未引用 degraded |
| P7 | 中 | 文件與程式不一致：ADR「美股缺漏拒絕發布」vs 程式降級發布；路由 48 vs 25；calibration_history 路徑不存在；parity test 永遠 skip | 治理規則無法執行 | ADR-0008、AGENTS.md |
| P8 | 中 | 價格物件空洞依賴 a807；stock_info 現況快照（存活者偏誤、產業欄前視） | 重訓可用性風險；輕微偏誤 | data_inventory |
| P9 | 低 | 效能與觀測性：每日重建 5 年面板；每日流程無日誌；10-26 是否執行無法回溯 | 可維護性 | — |

### 3. 還能提升什麼：候選清單（合併 18 項，依證據強度與成本排序）

| 序 | 項目 | 預期價值 | 成本 | 證據強度 | 性質 |
|---|---|---|---|---|---|
| 1 | 結算鍵修正＋void／sessions_ahead 標記＋summary 排除 horizon≠1、兩種口徑、逐日拆解與 bootstrap 區間 | 高 | 低 | 高 | 管線正確性（本次已做暫存版） |
| 2 | 交易日曆與價格新鮮度註記（訊號日＝最後已驗證 session＝head 價格最大日；休市／落後只在 manifest 與報告註明、照常發布——2026-11-01 裁示） | 高 | 中 | 高 | 管線正確性（本次已做暫存版 v3.1） |
| 3 | 美股隔夜資料：上游 07:15 前重試／DST 後時點、10-23／10-26 回補、逐工具遮罩取代 min 規則 | 高 | 中 | 高（美股資訊 dev 值 +2.1 pp；缺漏時掉 3 pp） | 資料端（需 TWStockUpdater 授權；遮罩需研究驗證） |
| 4 | H63 誠實化（無勝過基準的模型→揭露／基準分布；旗標一致；移除誤導列）或撤回 selected 只授權 H1 | 高 | 低 | 高 | 揭露修正（本次已做暫存版） |
| 5 | 乾淨前瞻驗證基礎：影子資料根＋排程、第一次發布口徑、逐日／逐模型／us_status 拆解，累積 ≥21 日 | 中 | 低 | 高 | 後續所有模型實驗的先決條件 |
| 6 | 消費端降級呈現＋揭露改用實盤（修正後）數字與廣度分層期望值 | 中 | 低 | 高 | 消費端（reporting.py 有您未提交的修改，需協調） |
| 7 | 每日 calibrate＋漂移監控 | 低 | 低 | 中 | 監控用，不改準確率（本次已納入暫存版） |
| 8 | 報酬頭升級：新大盤模型 on_mk_nl(_nofx)_a1000_wall（＋lad4） | 低 | 中 | 中 | 唯一有正向區間的模型內部改動；不改方向 |
| 9 | lb120 配方（878 日） | 低 | 中 | 弱～中 | 降低資料需求，準確度中性略負 |
| 10 | 產業欄清理（移除 4 個 ind_* 或 PIT 產業表）＋穩定排序 | 低 | 低 | 中 | 衛生修正，可與 8/9 併成一次 v2.1 |
| 11 | 多期限開盤前版面板（H5／H20 先、H63 後）以橫斷面超額報酬＋Rank IC 為主指標 | 中 | 高 | 中（Rank IC 0.08～0.11，t 2.5～5） | 取代單股／MIX 的常數 C0；方向命中目標不可達 |
| 12 | 美股下跌夜「超跌喊漲」缺口：依 gate 分開訓練 | 低 | 低 | 弱 | 待定未做 |
| 13 | Top10 選股風格檢驗（急跌後、產業上限、替代排序鍵）與發布規則 E1～E3 | 未知 | 低 | 弱 | 純事後分析（本次已做） |
| 14 | lin_rank 線性成員納入集成 | 低 | 低 | 弱 | 與 8/10 合併驗證 |
| 15 | 新資料：台指期夜盤、台積電 ADR | 低 | 高 | 弱～中 | 需上游新路由 |
| 16 | `yo1`（開盤買進→收盤）第二頭或 Top10 過濾 | 未知 | 中 | 未試 | 唯一直接對準交易報酬的標籤；需 walk-forward（等記憶體） |
| 17 | predict／settle 效能與觀測性（settle 直讀價格快取、predict 只建最近 262 日、日誌落地、parity test 改指向） | 低 | 中 | 高 | 不影響準確率 |
| 18 | 借貸／財報／官股等舊路由特徵（H63 IC +0.0075） | 未知 | 高 | 弱（PIT 未核實） | 需重新收集已退役路由 |

#### 4.3 個股 H63 誠實化暫存版（`single_stock\stage\`，審查修補後）

- H63 第一章主數字改為「模型估計｜尚無勝過基準的模型；以下為參考」＋「信賴度｜無」；附凍結為程式常數的最終測試 vs 基準（58.16% vs 一律猜跌 59.88%；MAE 14.04 vs 猜零 13.03 pp）與全母體回溯摘要（AUC 0.502～0.516）；本股 63 日報酬歷史分布（2376：中位 +6.94%、四分位 −5.07%～+18.95%、上漲 65%）；第六章移除「本股市場 vs 基準」誤導列；新增「預測效力」列與 `machine_evidence.prediction_disposition`；誠實模式門檻（方向 +1 pp、MAE −0.5 pp、成對不劣，且回溯穩健）寫入可見文字。
- H1 區塊「開盤前版實盤追蹤」改走 `stock_daily_preopen_reader.load_forward_summary`（符合 ADR-0008 消費端只經 reader），只採 v2 summary 口徑；現行 v1 summary 不印數字（避免印出失真的 26.7%）。
- Telegram Bot 的 H63 caption 改與 PDF 同一句判讀（否則主管道仍會顯示「上漲機率 49%／預估報酬 +8.3%」）；套用後需重啟 Bot 服務並重產當日報告（既有報告包無法用新程式重新驗證）。
- 測試：單股相關 5 檔 391 passed（基準 346）；完整套件 832 passed／1 failed（基準 787／1，唯一失敗為既有 `test_published_row_cache`）。審查 20 項發現（1 blocker、3 major）全部修補。
- 機器欄位（`prediction.prob_up`／`expected_return`）與三組旗標的 JSON 值刻意未改（穩定欄位重現與逐位元組測試），只在報告層一致化；是否對齊 JSON 旗標列為待決。

### 5. 建議的執行順序與需要您決定的事項

1. **立即（不需研究）**：部署第 1、2、7 項暫存版（`deploy\stage`，v3.1：已依 2026-11-01 裁示改為一律發布、只註記），重算帳冊；部署時一併覆蓋 `tests\test_stock_daily_preopen.py` 的 6 行修補、新增 `tests\test_preopen_stage_ops.py` 並登錄 `STABLE_PROGRAM_PATHS`；排程維持不帶 `--strict-calendar`。決定正式計分口徑：summary v2 內建的兩個 view（最後一次發布、第一次可計分發布）在目前帳冊相同（皆 35%）；「第一次發布對實際下一 session 計分」的 40% 只是研究試算、結算端尚未實作，若要採用需另案修改 `forward_summary()` 與摘要 schema。v3.1 另有四件小事待您確認：(a) 帳冊寫入失敗時「照常發布、exit 0、manifest `ledger.status=error`、報告附一句、不標 degraded」是否接受；(b) `--closed-day` 格式錯改為忽略＋註記（與宣告檔一致）是否接受；(c) reader `describe()` 新增文字的措辭（中央落後多日時會逐日列出無法解釋的平日）；(d) 本暫存版與 `single_stock\stage` 兩軌都改了 reader，需合併後同時部署。
2. **11-24 自動重訓前必做**：回補美股 10-23、10-26（TWStockUpdater 端）；或在 `build_panel` 加缺口偵測並以 `--force` 延後重訓。
3. **個股 H63**：決定「仍需要 H63 嗎」：若需要，部署第 4 項暫存版（誠實揭露）；若不需要，撤回 `selected_current` 只授權 H1。
4. **消費端**：等您的 `reporting.py`／`tw_single_stock_c0.py` 修改完成後，再做第 6 項（degraded 降級呈現、實盤數字揭露）。另（v3 審查發現）：`TWNextDayTop10\preopen_source.py` 的 `confirm_target`／`preopen_stale` 在明確 `--model-source preopen` 時仍會中止報告（預設 `auto` 只回退 C0、報告照發，排程用的是 `auto`）；若「不阻擋」也要涵蓋強制模式，需另案修改該檔（快照 `calendar_gate.status≠ok` 時只註記不中止）。
5. **模型研究**：第 16 項 `yo1` 第二頭與第 3 項逐工具遮罩已於 10-31 完成 walk-forward（見 4.5）——兩者皆未通過事前登記條件，不建議部署；剩下有正向證據的只有第 8＋10＋14（報酬頭新大盤模型、產業欄清理、lin_rank 成員）合併成一次 v2.1 配方變更，走影子資料根累積 ≥21 日再決定。模型端在現有資料下已無低垂果實，優先序應回到資料可得性（美股準時到位、缺口回補）與管線正確性。
6. **中長期**：第 11 項多期限面板取代 C0 常數（需 ADR）。

## 附錄 D：帳冊副本試算結果（dryrun，全文）

來源：`D:\TWStockData\program_data\stock_daily_c0\candidates\h1_ops_review_20261031\dryrun\DRYRUN_RESULTS.md`；收錄範圍：全文。

【註 1】「舊暫存版未過濾 cache 行為」一段的數字（settled_h1 13,419、方向 0.6268、AUC 0.6607 等）是修補前的舊口徑，正式口徑為「cache＋官方過濾＝panel 模擬」那組（與主文 §2 相同）；「替代口徑」一節只是研究試算、結算端尚未實作（主文 §7-B.8）。

【註 2】文中「目前 head `5f15a92b`」是 10-31 07:47 的狀態，之後中央已再發布（主文 §2 及補記），價格仍到 10-30，10-29 訊號 1,916 列在首次 settle 前仍 pending，試算結論不變。`settled_h1` 13,293 為帳冊列數（10-16 兩個 run 各 1,893 列皆計），摘要 `all` 的 settled_rows 11,400 為最後一次發布口徑（10-16 兩個 run 只取後者），相差 1,893 列。

【註 3】文中「CHANGES A4」「審查者第 2 項」分別指附錄 B 的 A4 與 20 項表第 2 項。

### 帳冊副本試算結果（修補版暫存模組 ops_patch，2026-10-31 07:47 重跑）

來源與方法（全部唯讀讀取正式資料，不經 `read_head`／`load_object`，不寫中央 `_query` 租約）：
- 帳冊：每次都從正式帳冊 `D:\TWStockData\program_data\stock_daily_preopen\state\forward.sqlite3`（mtime 10-30 07:17，19,174 列、10 個 run）以 `sqlite3` 唯讀 URI（`mode=ro&immutable=1`）`backup()` 重新複製到 `state\forward.sqlite3`（cache 模式）、`panel_mode\state\forward.sqlite3`（panel 模式模擬）、`cache_unfiltered\state\forward.sqlite3`（舊暫存版的未過濾 cache 行為，只為量化差異）。
- 已驗證日曆：`calendar\70d4a838….json`（verified_asof 2026-10-30，TWSE/TPEx 各 1,232 個 session，10-23、10-26 不在內，無未來 session）。
- 價格：正式快取 `cache\objects\57a849f2…_db24159024.parquet`（finmind_price_adj）與 `13c08ced…_cc8132dd37.parquet`（official_market_daily）。**這兩個物件屬於 head `3a1a06e6`（10-30 07:15 predict 所用、latest.json 記錄的 head），資料到 2026-10-29**，不是目前 head `5f15a92b`（10-30 21:59 UTC 發布、價格到 10-30，其物件 `9585901340e6…` 尚無快取 parquet；建立快取需 `load_object` 寫中央租約與正式快取目錄，本任務不做）。因此 10-29 訊號的 1,916 列仍 pending。
- panel 模式模擬：`rerun_settle_modes.py` 以四個快取 parquet（目前 head 與歷史 head `a807be3e` 的 finmind／official，只讀 date/stock_id/close(/market)）重現 `stock_daily_preopen_data.build_prices` 的拼接比例、presence 規則、official inner join 與 `adj_close>0 & raw_close>0`，得到與正式 settle 預設路徑相同的 `adj_close` 查表（2,245,174 列、295 MB），再呼叫暫存版 `settle_rows`。
- 指令：`python -B rerun_settle_modes.py <stage> <shared>`（日誌 `rerun_settle_modes_log.txt`）、`python -B compare_and_alt_view.py …`（`compare_and_alt_view_log.txt`）、`stock_daily_preopen.py calibrate --root <dryrun>`（`calibrate_log.txt`；執行前先把 `models\current.json` 還原為正式檔的 10-22 校準器，副本 `models\current_before_calibrate.json`）。
- 修正前摘要保留為 `state\summary_before.json`（正式 summary.json v1，10-29 23:15 UTC）。

#### 重要發現：直讀 finmind 快取必須以官方收盤過濾

舊暫存版 `--price-cache` 直讀 finmind 快取（不看 official）每日多結算約 20 列（8 個 run 共 148 列，`panel_mode\cache_only_rows.csv`）。核對後 **148 列的目標日官方收盤價全部為空**（`official_market_daily` 該檔該日 close 為 NaN：無成交或只有盤後零股；finmind 成交量 0 的有 55 列、其餘為極少量），finmind 還原序列沿用前一日收盤，其中 132 列被結算成恰好 0% 報酬。預設 panel 路徑（official inner join、`raw_close>0`）本來就排除這些列。修補版：`--price-source cache` 改為同 head 的 finmind 快取 + official 快取過濾（官方收盤價 > 0 才結算）、`--price-cache` 可搭配 `--official-cache`；過濾後 **cache 模式與 panel 模式逐列完全相同**（狀態、列數、值差異 0 列，`panel_vs_cache_summary.json`）。

#### 結算重算統計（`settle_rows` 回傳；cache＋官方過濾 ＝ panel 模擬）

evaluated 19,174；settled_h1 **13,293**；settled_multi_session 1,898；legacy_unverified **34**；pending 2,034（10-29 訊號 1,916 + 各 run 無官方收盤的 118 列）；void_target_not_session 1,915（10-22 訊號、target 10-23 的 run 16422e5a 全部）；changed_value 1,761；cleared_value 1,901。

舊暫存版未過濾 cache 行為（`cache_unfiltered\`）：settled_h1 13,419、legacy 3、pending 1,917、方向 0.6268／猜跌 0.6166／AUC 0.6607／Top10 0.35（製作者原回報數字即此）。

逐 run（`ledger_before_after_by_run.csv`，正式帳冊 vs 副本）：

| signal | target | run | 狀態 | 列數 | 舊已結算 | 新已結算 | 值改變 | 舊平均報酬 | 新平均報酬 |
|---|---|---|---|---|---|---|---|---|---|
| 10-16 | 10-19 | a05c7ef1 | settled_h1 | 1893 | 1893 | 1893 | 10 | −1.120% | −1.120% |
| 10-16 | 10-19 | a05c7ef1 | pending（無官方收盤） | 18 | 0 | 0 | — | — | — |
| 10-16 | 10-19 | c47957f3 | settled_h1 | 1893 | 1893 | 1893 | 10 | −1.120% | −1.120% |
| 10-19 | 10-20 | c9b507df | settled_h1 | 1897 | 1897 | 1897 | 11 | −0.301% | −0.299% |
| 10-20 | 10-21 | ad8cc543 | settled_h1 | 1905 | 1905 | 1905 | 8 | +0.863% | +0.863% |
| 10-21 | 10-22 | 30280797 | settled_h1 | 1898 | 1898 | 1898 | 9 | −1.424% | −1.421% |
| 10-22 | 10-23 | 16422e5a | **void_target_not_session** | 1915 | 1901 | 0 | — | −0.268% | —（清空） |
| 10-22 | 10-27 | 353b00b2 | settled_h1 | 1901 | 1901 | 1901 | 7 | −0.268% | −0.267% |
| 10-27 | 10-28 | 0add3db0 | settled_h1 | 1906 | 1906 | 1906 | **1706** | +0.329%（誤為兩日） | **+1.389%** |
| 10-27 | 10-28 | 0add3db0 | **legacy_unverified**（13 檔無 10-28 官方收盤，舊值為兩日報酬，保留但不計分） | 13 | 13 | 13 | 0 | +0.604% | +0.604%（不計分） |
| 10-27 | 10-29 | 5c54959d | settled_multi_session（sessions_ahead=2） | 1898 | 1898 | 1898 | 0 | +0.332% | +0.332% |
| 10-27 | 10-29 | 5c54959d | legacy_unverified（21 檔無 10-29 官方收盤） | 21 | 21 | 21 | 0 | +0.263% | +0.263%（不計分） |
| 10-29 | 10-30 | 139661ce | pending（快取尚無 10-30 收盤） | 1916 | 0 | 0 | — | — | — |

「值改變」每日約 10 列是目前 head 還原收盤重述造成（正式帳冊與副本同為 panel 語意，列數相同 1893/1897/…）。legacy_unverified 13 檔（0add3db0）：2064、2321、2751、2937、3158、3191、4402、5276、6195、6198、7713、7782、7820（與審查者第 2 項所列相同；`panel_mode\legacy_rows.csv`）。

#### 摘要：修正前 vs 修正後（`all` 區塊，最後一次發布口徑；schema v2）

| 指標 | 修正前（summary_before.json，正式 v1） | 修正後（settled_h1、sessions_ahead=1） |
|---|---|---|
| settled_rows / days | 11,413 / 6 | 11,400 / 6 |
| direction_accuracy | 0.5997 | **0.6245** |
| always_down_baseline | 0.6486 | 0.6130 |
| up_rate | — | 0.3870 |
| mean_p_up / prob_bias | — | 0.4707 / +0.0837 |
| auc | — | 0.6588 |
| top10_hit（Top10 列數 60） | 0.2667 | **0.3500** |
| top10_mean_ret | — | −0.41% |
| gated_top10_hit / days | 0.0 / 1 | 0.0 / 1 |
| mae / mae_zero | 0.01732 / 0.01746 | 0.01729 / 0.01755 |
| days_insufficient | — | true（6 < 21） |

bootstrap（以日為單位、seed 20261031、2000 次、90%）：acc [0.546, 0.709]、top10_hit [0.10, 0.63]。excluded：void 1,915、multi-session 1,898、legacy_unverified 34、pending 2,034、not_prospective 0。

#### 兩種口徑與消費端實際看到的 run

`views.last_publication` 與 `views.first_scorable_publication`（改名自 first_publication：每 (signal_date, stock_id) 取 recorded_at 最先的**可計分**列）在本帳冊上**完全相同**：10-16 兩個 run 預測值相同；10-22 只剩 353b00b2（16422e5a 為 void）；10-27 只剩 0add3db0（5c54959d 為多 session）。逐日表新增 `first_seen_run_id／first_seen_target_date／first_seen_status`：10-22 消費端最先看到的是 16422e5a（target 10-23，void_target_not_session），其餘各日即可計分的 run。

逐日表（兩口徑同；`state\forward_daily.csv` 12 列）：

| signal | target | n | acc | always_down | up_rate | mean_p_up | bias | auc | top10_hit | top10_mean_ret | gate | us_status | model | run | degraded | first_seen |
|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|
| 10-16 | 10-19 | 1893 | 0.833 | 0.833 | 0.167 | 0.371 | +0.204 | 0.520 | 0.1 | −1.44% | False | ok | v1 47573512 | c47957f3 | False | a05c7ef1（settled_h1） |
| 10-19 | 10-20 | 1897 | 0.553 | 0.699 | 0.302 | 0.485 | +0.183 | 0.550 | 0.0 | −1.60% | None | missing | v1 | c9b507df | True | 同 |
| 10-20 | 10-21 | 1905 | 0.521 | 0.422 | 0.579 | 0.467 | −0.112 | 0.656 | 0.7 | +1.67% | None | missing | v1 | ad8cc543 | True | 同 |
| 10-21 | 10-22 | 1898 | 0.662 | 0.799 | 0.201 | 0.449 | +0.248 | 0.417 | 0.0 | −3.10% | True | ok | v1 | 30280797 | False | 同 |
| 10-22 | 10-27 | 1901 | 0.468 | 0.601 | 0.399 | 0.532 | +0.133 | 0.513 | 0.3 | −1.94% | None | missing | v2 3bc36b6c | 353b00b2 | True | 16422e5a（target 10-23，void） |
| 10-27 | 10-28 | 1906 | 0.711 | 0.328 | 0.672 | 0.520 | −0.152 | 0.773 | 1.0 | +3.97% | None | missing | v2 | 0add3db0 | True | 同 |

按模型：v1（4 日、7,593 列）acc 0.642／always_down 0.688／auc 0.618／top10_hit 0.20；v2（2 日、3,807 列）acc 0.590／always_down 0.464／auc 0.620／top10_hit 0.65。
按美股狀態：ok（2 日、3,791 列）acc 0.748／always_down 0.816／auc 0.511／top10_hit 0.05；missing（4 日、7,609 列）acc 0.563／always_down 0.512／auc 0.602／top10_hit 0.50。（樣本日數 2～4，不具統計意義。）

#### 替代口徑（研究用，帳冊未寫入）：第一次發布對「訊號日後實際第一個 session」計分

`summary_alt_first_forecast_next_session.json`（`compare_and_alt_view.py`，價格同副本：finmind 快取 + 官方收盤過濾）：rows 11,400、days 6、direction_accuracy **0.6468**、always_down 0.6130、auc 0.6328、top10_hit **0.40**、top10_mean_ret +0.08%。差別只在 10-22：改用第一次發布 16422e5a（宣告 target 10-23、美股 ok）對 10-22→10-27 計分，該日 acc 0.602、top10_hit 0.6（353b00b2 為 0.468／0.3）。

#### calibrate（副本）

正式 10-22 校準器 → 修補版 calibrate（校準池只取 settled_h1 列）：platt_coef 0.82556 → 0.82822、platt_intercept 0.00313 → 0.00374、lad_a −0.001059 → −0.001016、lad_b 0.8 不變、window 2025-10-07～2026-10-19（250 日、470,498 列）→ 2025-10-09～2026-10-27（250 日、470,609 列；製作者原版未過濾為 470,640 列）。變動極小（6 個即時日對 250 日窗）。注意：修補版 cmd 預設**不**每日 calibrate（見 CHANGES A4）。

#### 記憶體與時間

cache 模式 settle --resettle 約 1 秒（finmind 快取過濾 10 個日期 15,598 列、official 過濾移除 248 鍵）；panel 模擬：四個 parquet 只讀 3～4 欄，面板 295 MB、全程 < 1 GB、約 8 秒；calibrate < 10 秒。

## 附錄 E：測試結果——v3.1 段

來源：`D:\TWStockData\program_data\stock_daily_c0\candidates\h1_ops_review_20261031\deploy\TEST_RESULTS.txt`；收錄範圍：檔首至「以下為 v3 的測試結果（歷史）」之前。

【註 1】純文字原樣收錄。

```text
=== v3.1（v3 審查修補：審查發現 1、2、3、5；2026-11-01 00:54～01:08）===

Python：D:\stock\.venv\Scripts\python.exe -B（3.12.10；PYTHONUTF8=1、PYTHONDONTWRITEBYTECODE=1、PYTHONIOENCODING=utf-8；
pytest -q -p no:cacheprovider；PREOPEN_REAL_CALENDAR=<dryrun>\calendar\70d4a838….json；PREOPEN_PARITY 未設）
測試樹（短路徑，cmd 煙霧測試需要）：C:\Users\USER\AppData\Local\Temp\claude\c6c88124_v31\
  shared_base            = D:\stock\programs\shared 未修改複本（獨立模式的相依模組來源）
  shared_stage           = 複本 + v3.1 三檔 + 修補的 tests\test_stock_daily_preopen.py + v3.1 tests\test_preopen_stage_ops.py
  shared_stage_unpatched = 複本 + v3.1 三檔 + 正式 tests\test_stock_daily_preopen.py 原樣
  tmp\run1～run3        = pytest basetemp；tmp\e2e = 端到端假資料根（研究目錄無 __pycache__／.pytest_cache 殘留）

[1] v3.1 暫存複本（shared_stage）tests/test_stock_daily_preopen.py tests/test_preopen_stage_ops.py -rs
      64 passed, 1 skipped, 37 warnings in 29.42s（38 既有 + 26 暫存版；skipped = parity）；failures: 無
      日誌：e2e_v31\pytest_v31_stage_copy.txt

[2] 未修補的正式既有測試檔 vs v3.1（shared_stage_unpatched）tests/test_stock_daily_preopen.py
      1 failed, 37 passed, 1 skipped, 36 warnings in 22.12s
      failed = test_predict_manifest_marks_chips_freshness_and_require_flag（第 725 行 assert m["degraded"] is False）：與 v3 同一原因
      （固定假訊號日 2026-10-16 vs 中央真實日曆最後交易日 10-30 → stale_signal／degraded=true，預期行為；修補複本的 6 行假日曆解決）
      日誌：e2e_v31\pytest_v31_official_test_unpatched.txt

[3] deploy\stage\tests 獨立模式（PREOPEN_STAGE_SHARED_DIR=<shared_base>）test_preopen_stage_ops.py -rs
      26 passed, 1 warning in 11.27s（含帳冊鎖定測試等 5 秒）
      日誌：e2e_v31\pytest_v31_standalone.txt

[4] v3.1 新增／擴充的測試（相對 v3 的 24 個）
  ★ test_predict_publishes_on_invalid_closed_day_and_unknown_central_max_date（新）：--closed-day 2026/10/19 2026-10-19 → 發布、
        status=closed_day_cli_invalid、正確的值仍採用、describe 文字、日誌；全部格式錯 → [signal_gap_unverified, closed_day_cli_invalid]；
        head 缺 actual_max_date → central_max_date_unknown（非 stale_signal）、訊號日取面板、describe 文字；strict 仍拒
  ★ test_predict_publishes_when_ledger_is_locked_corrupt_or_uncreatable（新）：損毀檔（DatabaseError）、另一連線 BEGIN EXCLUSIVE
        （OperationalError: database is locked）、state 為檔案（FileExistsError）三種 → 發布、exit 0、manifest==latest.json、
        ledger.status=error、不標 degraded、describe 附文字、reader.load_latest 可讀；正常 ledger.status=ok rows=6；--no-latest skipped
    test_calendar_gate_rules（加：10-22→10-23 的 reasons 含 stale_signal；px_max=None 三案；closed_day_invalid 兩案）
    test_announced_closures_file（加：parse_closed_days、central_price_max_date 解析規則）
    test_help_lists_new_flags（加：closed_day_cli_invalid）
    test_reader_describe_old_snapshot_and_ok_gate_are_verbatim（加：ledger ok／skipped／舊快照／型別異常 → 逐字不變）
    test_reader_describe_calendar_gate_texts（加：兩個新 status、--closed-day 原值、void 時 stale_signal 文字、帳冊文字位置）

[5] 守門情境表（純函式）e2e_v31\scenarios_v31.txt：23 例，含審查發現 2 的兩列與 px_max=None、closed_day_invalid、strict；
    predict 端到端（假模型／假 head／假面板，不碰中央）e2e_v31\e2e_predict_v31.txt：17 個每日情境 + 4 個帳冊情境 + 1 個 strict，
    全部 exit 0（strict 除外，如預期拒發）、latest.json 更新、manifest == latest.json、無 latest_refusal.json、reader 可讀。

[6] stage.diff（正式檔 → v3.1）：git apply --check 通過（暫存 repo、core.autocrlf=false、LF 正規化），套用後三檔與 v3.1 逐位元相同。

[7] cmd：RUN_PREOPEN_DAILY.cmd v3.1 未改（sha c94b962f6c7f…0d74 同 v3），cmd_run_v3\ 的 Run A–C 與 test_cmd_* 仍適用；
    帳冊副本試算（dryrun）：未重跑——結算端／摘要零 hunk。
```

## 附錄 F：兩軌 reader 合併結果（merged_reader README，全文）

來源：`D:\TWStockData\program_data\stock_daily_c0\candidates\h1_ops_review_20261031\deploy\merged_reader\README.md`；收錄範圍：全文。

【註 1】由「Codex 視角」審查者產生；主文 §7.4 已摘錄其結論。README 內「交接檔 §7.4 只提到常數與兩個函式，漏了 3 個新代碼」指修補前的交接檔版本，現行主文 §7.4 (a) 已補上；「單股軌 CHANGES §8.8」指附錄 G 未收錄的該軌第 8 節，其結論已在主文 §7.4 鍵名段。

### merged_reader：v3.1 reader ＋ 單股軌 reader 的合併結果（Codex 視角審查者，2026-11-01）

本目錄由「Codex 視角與完整性」審查者產生，供 codex 核對交接檔 §7.4「reader 兩軌合併」用。
**未修改 `D:\stock` 任何檔案、未寫正式資料根與中央。**

#### 檔案

| 檔案 | 行尾／行數 | sha256 | 來源與合併方式 |
|---|---|---|---|
| `stock_daily_preopen_reader.py` | LF／422 | `3e3fce07b259d2729e53c1d5bb22ea6485a4fa7228a1756ccfafa77986442540` | 以 `deploy\stage\stock_daily_preopen_reader.py`（v3.1，sha `fa9228b57c22…2d73`，LF 362 行）為底，把 `single_stock\stage\shared\stock_daily_preopen_reader.py`（sha `88b544f4794211ee…c7bf`，LF 322 行）相對正式 reader（sha `151c45c7293c…c69a`，LF 262 行）的純新增內容三方合併進去 |
| `tests\test_stock_daily_preopen.py` | CRLF／868 | `aca1df4b1458f0e39aa0951b709466019b81fa3e6cdc8c10e40b6769bd975666` | 以 `deploy\stage\tests\test_stock_daily_preopen.py`（v3.1 修補複本，sha `7c8efe40ca31…6246`，CRLF 866 行）為底，加入 `single_stock\stage\shared_tests\test_stock_daily_preopen.py`（sha `2f3e2ff0ed3c…4bf6`）的 `REASON_CODES` +2 行（第 34-35 行） |
| `pytest_merged_run1.txt` | — | — | 合併複本的 pytest 完整輸出（見下） |

#### 合併方法（Python `difflib.SequenceMatcher`，逐行、保留原行尾）

- 單股軌 reader 相對正式 reader 的差異只有三段**純插入**（正式檔第 58 行後 +3、第 60 行後 +6、第 248 行後 +51，合計 +60／−0）：
  `REASON_TEXT_ZH` 新增 `preopen_summary_missing`／`preopen_summary_invalid`／`preopen_summary_schema_mismatch` 三個代碼、常數 `FORWARD_SUMMARY_SCHEMAS`／`FORWARD_SUMMARY_VIEWS`、函式 `forward_summary_path()`／`load_forward_summary()`。
  （交接檔 §7.4 只提到常數與兩個函式，漏了 `REASON_TEXT_ZH` 的 3 個新代碼。）
- v3.1 reader 相對正式 reader 的差異在正式檔第 13、115、146、258 行後插入與第 262 行取代，與單股軌的插入點不重疊；插入位置以前後文行內容定位，對應到 v3.1 的第 70、72、345 行之前。
- 結果核對：merged − v3.1 ＝ +60／−0（恰為單股軌的新增）；merged − 單股軌 ＝ +101／−1（恰為 v3.1 的變更）；`FORWARD_SUMMARY_SCHEMAS`、`FORWARD_SUMMARY_VIEWS`、`forward_summary_path`、`load_forward_summary`、`calendar_gate_text`、`ledger_text`、`describe`、`load_latest` 各只定義一次；`ast.parse` 通過。
- 測試檔：merged − v3.1 ＝ +2／−0；merged − 單股軌 ＝ +6／−0；merged − 正式檔 ＝ +8／−0。

#### 測試結果（`D:\stock\.venv\Scripts\python.exe -B -m pytest -q -p no:cacheprovider -rs`，`PYTHONDONTWRITEBYTECODE=1`／`PYTHONUTF8=1`／`PYTHONIOENCODING=utf-8`，`PREOPEN_REAL_CALENDAR=<dryrun>\calendar\70d4a838b33e06ddd6f673b1e2e5502a30362f5b7ae158aa6ae0ba77d527298d.json`，`PREOPEN_PARITY`／`PREOPEN_STAGE_SHARED_DIR` 未設）

整個 `programs\shared` 複本 ＋ v3.1 的 `stock_daily_preopen.py`／`RUN_PREOPEN_DAILY.cmd`／`tests\test_preopen_stage_ops.py` ＋ 本目錄的 merged reader 與合併後既有測試 ＋ 單股軌的 `tests\test_stock_daily_preopen_reader_summary_stage.py`：

`tests\test_stock_daily_preopen.py tests\test_preopen_stage_ops.py tests\test_stock_daily_preopen_reader_summary_stage.py` → **67 passed、2 failed、1 skipped**（38＋24＋5 通過；skipped 為 `PREOPEN_PARITY` 未設的 parity 測試）。兩個失敗：

1. `test_preopen_stage_ops.py::test_cmd_daily_flow_order_logging_exit_code_and_fallbacks`：`NotADirectoryError: [WinError 267]`——審查者的 pytest `--basetemp` 位於超過 260 字元的暫存路徑，`cmd.exe` 無法以該目錄為工作目錄啟動。**與合併無關**；改以短路徑（`subst` 磁碟 `W:`）直接呼叫該測試函式 → PASSED（`cmd_test_driver.txt`）。codex 重跑時請把複本與 `--basetemp` 放在短路徑（例如 `C:\Users\USER\AppData\Local\Temp\claude\<短名>\`）。
2. `test_preopen_stage_ops.py::test_reader_describe_old_snapshot_and_ok_gate_are_verbatim`（第 1039 行）：`assert reader.REQUIRED_COLUMNS == mod.REQUIRED_COLUMNS and reader.REASON_TEXT_ZH == mod.REASON_TEXT_ZH`——`mod` 是以 importlib 載入的 `D:\stock\programs\shared\stock_daily_preopen_reader.py`（正式檔，`PREOPEN_STAGE_SHARED_DIR` 未設時的預設）；merged reader 的 `REASON_TEXT_ZH` 多了單股軌的 3 個代碼，所以不相等。`describe()` 逐字對照（第 1036-1038 行）全部通過，**失敗的只有這一個斷言**，是 v3.1 新測試與單股軌「新增原因代碼」在設計上的衝突，不是合併錯誤。該測試在 `official.resolve() == reader.__file__`（部署後 `programs\shared` 內自我比對）時會跳過這段對照：以 `PREOPEN_STAGE_SHARED_DIR=<合併複本>` 重跑 → 2 passed（`pytest_verbatim_selfcompare_run3.txt`）。

因此扣除環境因素後為 **68 passed／1 failed／1 skipped**；要在部署前的複本中全綠，codex 需決定：(a) 把第 1039 行的 `REASON_TEXT_ZH` 相等改為「正式檔的鍵值是 merged 的子集且共有鍵文字相同」（例如 `all(reader.REASON_TEXT_ZH[k] == v for k, v in mod.REASON_TEXT_ZH.items())`），或 (b) 接受部署前複本有這 1 個已知失敗、部署後自我比對通過。

#### 其他需 codex 注意的事

- 單股軌 reader 的 `FORWARD_SUMMARY_VIEWS = ("first_publication", "last_publication", "all")` 與 `load_forward_summary()` 讀 `views.first_publication`，但 v3.1 結算端 `forward_summary()` 寫出的是 `views.last_publication` 與 `views.first_scorable_publication`（`VIEW_FIRST_SCORABLE`，deploy 審查第 19 項改名）。以 merged reader 讀 `..\dryrun\state\summary.json`（v2）：`first_publication=None`、`last_publication` 有值、`all` 有值——不會壞，但單股消費端會退回 `last_publication`，且其標籤「第一次發布口徑（views.first_publication）」永遠不會出現。兩軌同時部署前需對齊名稱（改 reader 常數或結算端鍵名，並改對應測試）。
- 正式 `state\summary.json` 目前為 v1：merged reader `load_forward_summary` 回傳 `version=1`、只有 `all` 區塊（與單股軌 CHANGES §8.8 一致）。

## 附錄 G：單股 H63 誠實化暫存版（另一研究軌）——摘要、檔案佈局與套用步驟、需使用者決定

來源：`D:\TWStockData\program_data\stock_daily_c0\candidates\h1_ops_review_20261031\single_stock\CHANGES_stage.md`；收錄範圍：檔首、§0 一句話摘要、§1 檔案佈局與對應（含套用步驟）、§9 需使用者決定。

【註 1】此軌與本軌同時改了 `stock_daily_preopen_reader.py` 與 `tests\test_stock_daily_preopen.py`；同時部署時以附錄 F 的合併檔取代，並先對齊 `views.first_publication`／`first_scorable_publication` 鍵名（主文 §7.4）。本附錄的 `stage.diff` 指 `single_stock\stage.diff`，與本軌 `deploy\stage.diff` 無關；同時部署時套用該 diff 須排除 reader 與 shared 既有測試兩個 hunk（主文 §7.4 的 `--exclude` 例）。套用步驟第 1 點「一律用 diff、不要複製 stage 檔」只適用於該軌（其 stage 檔除 shared 測試副本維持 CRLF 外已轉 LF）；本軌 stage 檔行尾與正式檔一致，以複製為準（主文 §8.4）。

【註 2】檔首「`git status` 仍只有原有的兩個 M」為 10-31 狀態：兩檔已於 commit `afd28ab` 提交；§1 的 8 個原始副本 sha256 於 2026-11-01 仍與工作樹相符。套用步驟第 3 點與 §9 第 6 點的「重啟 Telegram 服務、重產當日報告」只在套用此軌（改 `stock_candidate_report`／bot）時需要；只部署本軌不需重啟（主文 §7.4）。

【註 3】文中「§2～§8」「見 §7」「§8.8」「§8.9」「第 10 節」指該軌 CHANGES 未收錄的章節，非本檔主文：「見 §7」實指其 §8.2／§8.3（`source_bindings` 實測 SAME 5／DIFF 23／MISSING 0，若要收據反映現況需另開 revision_04；既有報告包以新程式驗證得 `pdf_sections_binding_mismatch`，須先重啟 Telegram 服務再重產當日報告）；「§8.9」為行尾說明；「第 10 節」為該軌 20 條審查處置。

### 單股報告 H63 誠實化暫存版（任務 C → h63_patch 修補版）— CHANGES_stage

- 日期：2026-10-31（週六）；隔離研究，**未修改 D:\stock 任何檔案、未寫任何正式資料根、未 commit**（`git status` 仍只有原有的兩個 M：`TWNextDayTop10/reporting.py`、`programs/single_stock_analysis/src/tw_single_stock_c0.py`）。
- 暫存版位置：`D:\TWStockData\program_data\stock_daily_c0\candidates\h1_ops_review_20261031\single_stock\stage\`
- 差異檔：`..\single_stock\stage.diff`（`git diff --no-index` 格式、路徑為 `programs\…` 正式相對路徑、全部 LF；`cd D:\stock && git apply --check stage.diff` 通過；11 個檔案：4 個正式程式、4 個既有測試修改、3 個新增測試）。
- 本版（h63_patch）是對製作者原版的修補：兩位審查者 20 條發現的逐條處置見第 10 節。與原版最大的差別：(1) 全母體回溯數字改為**程式內凍結值**，正式報告的產出與驗證完全不讀研究目錄；(2) 誠實模式開關加入最小邊際並把凍結回溯的「穩健」判定納入 AND 條件；(3) C3 實盤追蹤改走 `stock_daily_preopen_reader.load_forward_summary`（shared 新增 API）、只採 v2 口徑並加 asof 守門，v1 摘要只顯示狀態列；(4) Telegram bot 的正式 H63 caption 與 PDF 第一章同一份判讀；(5) 啟用狀態缺漏不再寫成「已啟用」。

#### 0. 一句話摘要

正式 H63 報告的第一章不以「上漲機率 49.08%／預估報酬 +8.33%」作主表，改為「模型估計｜尚無勝過基準的模型；以下為參考」＋信賴度，附 (a) 候選模型封存指標 vs 基準（58.16% vs 一律猜跌 59.88%；MAE 14.04 vs 猜零 13.03 個百分點，讀 `payload.metrics`）、勝過基準的門檻、全母體回溯（程式內凍結：AUC 0.502～0.516、MAE 不優於猜零；來源 sha256 前 12 碼 7ce704d435fb）與 (b) 本股 63 日報酬歷史分布；Telegram 的 H63 caption 印同一句話；所有正式報告新增「預測效力」列；第六章只列全體樣本；H1 開盤前版區塊的「實盤追蹤」表經讀取層 API 取 v2 結算摘要（v1 舊口徑只顯示狀態、不印數字）。機器欄位（`prediction.prob_up`／`expected_return`、三組旗標）完全不變，`validate_selected_report` 的穩定欄位重現不受影響。

#### 1. 檔案佈局與對應

| stage 內路徑 | 對應正式路徑 | 狀態（`git apply --numstat`） | sha256（前 16 碼） |
|---|---|---|---|
| `stage\src\stock_candidate_report.py` | `programs\single_stock_analysis\src\stock_candidate_report.py` | 修改 +526／-68 | `c2367be7a1c611c0` |
| `stage\src\single_stock_preopen.py` | `programs\single_stock_analysis\src\single_stock_preopen.py` | 修改 +163／-0 | `a1e759d084ff63c5` |
| `stage\shared\stock_daily_preopen_reader.py` | `programs\shared\stock_daily_preopen_reader.py` | 修改 +60／-0（只新增） | `88b544f4794211ee` |
| `stage\telegram\telegram_stock_command_bot.py` | `programs\telegram_report_delivery\telegram_stock_command_bot.py` | 修改 +24／-1 | `73f9acc646620261` |
| `stage\tests\test_h63_honest_stage.py` | `programs\single_stock_analysis\tests\test_h63_honest_stage.py` | 新增（33 個函式、45 個 item） | `48c77fd3601522e4` |
| `stage\tests\test_stock_candidate_report.py` | `programs\single_stock_analysis\tests\test_stock_candidate_report.py` | 修改 1 個測試的期望（+7／-5）；**已轉為 LF** | `a7ef82ef3181c8b9` |
| `stage\shared_tests\test_stock_daily_preopen.py` | `programs\shared\tests\test_stock_daily_preopen.py` | 修改 `REASON_CODES`（+2）；維持工作樹的 CRLF（index 為 LF，見 §8.9） | `2f3e2ff0ed3cc380` |
| `stage\shared_tests\test_stock_daily_preopen_reader_summary_stage.py` | `programs\shared\tests\test_stock_daily_preopen_reader_summary_stage.py` | 新增（5 個） | `1ba8064520c15afe` |
| `stage\telegram_tests\test_telegram_stock_command_bot.py` | `programs\telegram_report_delivery\tests\test_telegram_stock_command_bot.py` | 修改 1 個測試的期望（+6／-1） | `a0578ce24a0b4371` |
| `stage\telegram_tests\test_telegram_selected_deployment.py` | `programs\telegram_report_delivery\tests\test_telegram_selected_deployment.py` | 修改 1 個參數化測試的 H63 期望（+5／-1） | `1c11c3525b6be336` |
| `stage\telegram_tests\test_telegram_h63_caption_stage.py` | `programs\telegram_report_delivery\tests\test_telegram_h63_caption_stage.py` | 新增（6 個） | `7a39bf64333586ad` |
| `stage\original\*` | 8 個正式檔的原始副本（與 D:\stock 工作樹逐位元組相同） | 比對用 | `stock_candidate_report.py 7fdc45faf34a1b45`、`single_stock_preopen.py 38e263c7967a70bb`、`test_stock_candidate_report.py 031b4d6911838875`（原版 CHANGES 誤寫 f134f1b0…）、`stock_daily_preopen_reader.py 151c45c7293cf2b6`、`test_stock_daily_preopen.py ec6a48a88fc48142`、`telegram_stock_command_bot.py 89b793aa6d8a8e7b`、`test_telegram_stock_command_bot.py a5ff265378e3f7c7`、`test_telegram_selected_deployment.py 525ba91959e8dab3` |
| `stage\logs\*` | 測試輸出與煙霧測試 | 證據 | `patch_*`＝本修補版；`base_*`／`stage_*`＝原版製作者的紀錄（基準仍有效） |

##### 套用步驟（本次未做；需使用者核准）

1. 確認常駐 Telegram 服務與排程不在送件中；`cd D:\stock && git apply --check stage.diff`（本次已通過）→ `git apply stage.diff`（**建議一律用 diff，不要複製 stage 檔**，避免行尾差異）。
2. 跑測試：`programs\single_stock_analysis`（`pytest tests`）、`programs\shared`（至少 `tests\test_stock_daily_preopen.py tests\test_stock_daily_preopen_reader_summary_stage.py tests\test_selected_report_parsed_evidence_context.py tests\test_selected_read_only_plan.py tests\test_stock_daily_selected_deployment.py`）、`programs\telegram_report_delivery`（`pytest tests`）。
3. **重啟常駐 Telegram 服務**：`RESTART_COMMAND_BOT_SERVICE.cmd -Restart -ConfirmRestart`（需系統管理員）。README（`programs\telegram_report_delivery\README.md` L193-195）明載常駐服務第一次驗證後沿用載入的 `stock_candidate_report` 版本；不重啟時，分析子行程以新程式產出的包會被舊模組驗證成 `report_binding_mismatch`／`pdf_sections_binding_mismatch`，H1 與 H63 全部無法送出；舊行程內 `telegram_jobs` 快取的 bundle 重送也會驗證失敗。
4. 重產當日正式報告（既有正式報告包無法用新程式重新驗證，見 §7）；Telegram 只送新產生的包。
5. 若保留 `selected_current.json`，另開 revision_04 驗收收據讓 `source_bindings` 反映現況（§7）。

#### 9. 需使用者決定

1. **是否仍要在正式報告產生 H63？** (a) 採本暫存版（保留 H63 但誠實呈現，PDF 與 Telegram 同一句話）；(b) 直接停產 H63（需改入口／deployment 的 `horizons=(1, 63)` 預設，本次未動 `tw_single_stock_c0.py`）。
2. **是否改為撤回 `selected_current.json`、只授權 H1？**（撤回後單股正式報告退回 C0 路徑；須走 `activate_selected_model` 行政流程；若保留，建議補 revision_04 驗收收據。）
3. **JSON 旗標是否也要對齊？**（需改 `_formal_payload` 與 `validate_selected_report` 的穩定欄位轉換並更新其逐位元組測試。）
4. **H1 備援對照表是否也要誠實化？**（本版只加預測效力列。）
5. **邊際與門檻的數值**：方向 1 個百分點／MAE 0.5 個百分點／回溯 AUC 0.55 或 MAE 低猜零 1 個百分點是本版設定，可調；調整只改常數與測試期望。
6. **套用時機**：套用 → 重啟 Telegram 服務 → 重產當日報告；C3 的實盤數字要等 deploy 子任務的 v2 settle 落地。
