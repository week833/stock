# Claude → codex 交接：開盤前版 H1 管線修正暫存版 v3.1（一律發布、只註記）請確認（20261101）

這是一份獨立交接檔，不接續任何舊交接檔，記錄 2026-11-01（週日）的狀態。開盤前版（`programs\shared\stock_daily_preopen*.py`、`RUN_PREOPEN_DAILY.cmd`）的研究與暫存版由 Claude 製作；**暫存版尚未部署，正式檔未改**。本檔請 codex 核對 §7 的事項後回覆；部署需使用者核准，核准後由 codex 或 Claude 執行皆可，但同一時間只能有一方修改 `programs\shared`。本版已依 2026-11-01 三位審查者（事實對磁碟、治理、Codex 視角）的逐項核對修補。

標記：**Fact** = 已以唯讀方式核對；**Not verified** = 未核對或無法在研究目錄內核對。

## 1. 結論

- 使用者 2026-11-01 裁示（原文）：**「我的要求就是舊資料不會阻擋程式執行及報告發布」**。暫存版 v2 原本在中央價格落後、目標日非交易日等情況預設拒發，已依裁示改為 v3.1：**所有日曆／新鮮度／帳冊問題只寫進 manifest 並由 reader `describe()` 自動在報告註明，predict 一律發布、`latest.json` 一律更新、exit 0**；拒發只保留為手動 QA 旗標 `--strict-calendar`（排程不帶）。
- 暫存版位置：`D:\TWStockData\program_data\stock_daily_c0\candidates\h1_ops_review_20261031\deploy\stage\`（3 個程式檔、1 個既有測試的修補複本、1 個新測試檔）。內容分五部分：A1 結算鍵修正與結算分類、A2 摘要口徑 v2、A3 日曆／新鮮度註記、A4 cmd 日誌與不中斷、A5 predict 衛生。
- 版本代號：v2＝2026-10-31 審查修補後的「預設拒發」版（`deploy\v2\`）；v3＝2026-11-01 依裁示改為一律發布的第一版（`deploy\v3\`）；v3.1＝v3 再經審查修補的現行版（`deploy\stage\`）。本文「summary v2」指摘要 schema `tw-stock-preopen-forward-summary-v2`，與暫存版版本無關。
- **Fact**：`D:\stock` 四個正式檔 sha256 未變（§4）且與 HEAD 無差異，`git status` 除本交接檔（未追蹤）外乾淨；正式資料根 `D:\TWStockData\program_data\stock_daily_preopen\` 與中央 `shared_market_data` 未被寫入（研究只唯讀）。
- 修正的動機是正式帳冊的三個事實（§2）：結算鍵 bug 讓 summary 的 Top10 命中 26.7% 是錯的（修正後約 35%）；沒有日曆守門讓颱風休市日與中央落後日的快照被當成正常結算；美股 10-23、10-26 在中央目前 head 缺漏。

## 2. 正式環境現況（2026-11-01 唯讀核對）

- **Fact｜結算鍵 bug**：正式 `cmd_settle` 的 `UPDATE … WHERE signal_date=? AND stock_id=? AND model_sha256=?` 沒有 `target_date`／`run_id`，同訊號日的兩個 run 互相覆蓋。10-27 訊號的 run `0add3db0`（目標 10-28）1,919 個已結算值全部被 10-29 的結算覆寫為 10-27→10-29 的兩日報酬（`observed_at` 與 run `5c54959d` 逐列相同）；重算後其中 1,706 列的值改變、13 列（無 10-28 官方收盤）歸 `legacy_unverified`。正式 `state\summary.json`（v1）的 Top10 26.7%／方向 0.600 因此是錯的。帳冊副本重算（`..\dryrun\DRYRUN_RESULTS.md`，結算端修補版、同 head 官方收盤過濾）：evaluated 19,174、`settled_h1` 13,293、`settled_multi_session` 1,898、`legacy_unverified` 34、`pending` 2,034、`void_target_not_session` 1,915；Top10 約 35%（21/60）。
- **Fact｜無日曆守門的後果**：10-22 訊號→10-23（颱風休市）的 run `16422e5a` 1,915 列被結算成 10-27 報酬（重算後 void）；10-29 中央價格落後時重發 10-27 舊訊號（run `5c54959d`，跨 2 個 session，重算後不計分）；10-28 訊號日沒有任何預測。
- **Fact｜帳冊與快照**：`state\forward.sqlite3`（mtime 10-30 07:17）`predictions` 表 19,174 列、10 個 run、`observed_return` 非空 17,126 列；最後一個 run `139661ce`（10-29→10-30，1,916 列）仍 pending。`predictions\latest.json` 停在 10-29→10-30（10-30 07:17，`central_head_sha256` `3a1a06e6…`，`degraded=false`，美股 ok、籌碼 ok）；`predictions\` 最後一個日期目錄是 `2026-10-29`；沒有 `latest_refusal.json`。`models\current.json` 為 10-22 09:52 的 `h1pre_v2_ens`（每 21 個 session 自動重訓，推算下次 2026-11-24）。
- **Fact｜排程**：六個工作排程（`TWStock Preopen 01 Central Full Update 23-00`、`02 Central Global Update 06-30`、`03 Snapshot 07-15`、`04 Top10 07-30`、`05 MIX 07-40`、`MIX Telegram Report 08-00`）的 LastRunTime **全部停在 10-30**，10-31（週五）沒有任何一個執行，NextRunTime 皆為 11-02。10-30 23:00 的 01 任務 LastTaskResult `0xC000013A`（被中止）、10-30 06:30 的 02 任務 `0xA`；03／04／05／08:00 皆 `0x0`。**Not verified**：10-31 未執行與 01／02 非 0 結束碼的原因（Claude 未查事件記錄；請 codex 確認，見 §7.6）。
- **Fact｜中央 head**：`_metadata\stock_data_current.json`（`producer_id` `twstock_updater`；**Not verified**：這次發布是否由 codex 手動觸發，磁碟上無紀錄）目前 `run_id` `20261031T151703Z-4bc854b6`、`published_at` `2026-10-31T19:04:08Z`（台北 11-01 03:04）、`previous_sha` `516b17ef…`；`finmind_price_adj.actual_max_date` 與 `global_market_history.actual_max_date` 皆為 `2026-10-30`（格式 `YYYY-MM-DD`；後者 `record_count` 33,604、物件 `_objects\46fd4011…`）。已驗證日曆最新檔 `_calendar\verified\8715826242947e6f….json`（10-31 23:26 寫入）`verified_asof=2026-10-30`，TWSE／TPEx 各 1,232 個 session、最後一個 10-30、沒有未來 session。
- **Fact｜美股**：前一晚美股資料在 10 次發布中缺 5 次（缺漏時同訊號日兩份 Top10 重疊 0/10）；目前 head 物件 `_objects\46fd4011…` 中 16 個 `us_*` 工具在 10-23、10-26 兩個美股交易日皆 0 列（同日其他 10 個非美股工具有列；2026-11-01 直接讀物件 JSON 核對，未經 `read_head`）。11-24 自動重訓前若 updater 不回補，`on_*` 特徵會以缺值訓練（§7.5）。

## 3. v3.1 的規則（裁示的落實方式）

1. **predict 一律發布、`latest.json` 一律更新、exit 0**。中央價格落後（舊訊號重發）、訊號日與目標日之間有無法解釋的平日、目標日為週末／固定假日／已宣告休市／已驗證非交易日、訊號日非交易日、中央日曆缺檔／雜湊不符／格式錯、宣告檔無效、`--closed-day` 格式錯、中央 head 缺 `actual_max_date`、帳冊 `forward.sqlite3` 被鎖定／損毀——全部只在 manifest 註記：`calendar_gate.status`／`reasons[]`（最嚴重者在前）、`degraded=true`（gate≠ok 時）、原因碼併入 `degraded_reasons`、頂層 `ledger={status, error, rows, path}`。不中止、不拒發、不寫 `latest_refusal.json`。
2. 拒發只在新旗標 **`--strict-calendar`**（手動 QA，預設關閉，cmd 不傳）：strict 且 status≠ok 才 exit 1 並寫 `predictions\latest_refusal.json`，下一次成功發布時刪除。`--allow-stale-signal` 改為無作用的相容旗標。
3. `--closed-day` 與 `<root>\config\announced_closures.json` 保留為選用：有宣告時 gate 為 `ok`；格式錯誤只註記 `closures_file_invalid`／`closed_day_cli_invalid`。
4. reader `describe()` 在 status≠ok 或 `ledger.status=error` 時，於籌碼說明之後、美股漲跌之前附加繁中說明（§5 的「報告文字」欄）；沒有 `calendar_gate`／`ledger` 鍵（舊快照）、`status=ok` 或型別異常時文字**逐字不變**（測試以 importlib 載入正式 reader 對照）。文字會自動出現在三個以檔案路徑載入 reader 的消費端：TWNextDayTop10（`preopen_source.py` 第 362 行 → `reporting.py` 第 713 行）、單股 PDF 的「模型說明」列（`single_stock_preopen.py` 第 344 行 → `stock_candidate_report.py` 第 1942 行）、`D:\MIX All`（`src\mixstock\preopen.py` 第 317／551 行 → `core_daily.py` 第 513 行；排程 05 與 08:00 的 MIX Telegram 報告都會帶出這段文字，MIX 不在本次可改範圍）。**Telegram 個股 Bot 的 H1 caption 不印 describe 文字**（`telegram_stock_command_bot.py` `_preopen_caption_lines` 第 1401–1421 行只用 `gate_us_up_zh`／`us_overnight_status_zh`／`chips_note_zh`／fixture／揭露；整個 `programs\telegram_report_delivery` 無 `describe` 與 `stock_daily_preopen_reader` 字樣），新守門文字不會出現在 caption；是否要顯示見 §7-B.10。
5. `RUN_PREOPEN_DAILY.cmd` 流程與現行相同（settle → train → predict；任一步失敗仍執行 predict、結束碼取自 predict；現行亦無每日 calibrate），v3.1 只新增：每步 stdout／stderr 落地 `<root>\logs\preopen_daily_<日期>.log`、日誌目錄不可寫時退回主控台、`STOCK_DAILY_PREOPEN_CALIBRATE_DAILY=1` 才啟用的選用 calibrate 步驟。
6. 結算端（A1）負責保護帳冊與校準池：target 非已驗證 session → `void`（不寫報酬）、≥2 個 session → `settled_multi_session`、舊鍵覆蓋留下的值 → `legacy_unverified`，三者都不計分、不進校準池；`summary.json` 升為 `…-forward-summary-v2`（保留 `all` 區塊舊鍵、只計 `sessions_ahead=1`、另寫 `state\forward_daily.csv`）。
7. 仍會讓 predict 以非 0 結束的只剩**非舊資料**的錯誤（既有行為保留）：`load_active` 的 5 個 `SystemExit`（無 pointer、pointer schema、artifact sha、sklearn 版本、features/recipe）、`pdata.read_head`／`load_inputs` 的 `PreopenDataError`（中央 manifest／object sha 不符、route 缺）、明確給錯 `--target-date`（不晚於訊號日，或格式不是 `YYYY-MM-DD`／`YYYYMMDD`：`resolve_target` 第 854 行的 `date.fromisoformat` 以 `ValueError` 中止）、快照檔本身的寫入 I/O 錯誤。極端情況：`latest.json` 已寫出後刪除舊 `latest_refusal.json` 失敗（第 1263 行 `unlink` 的 `OSError`）會以非 0 結束，但發布已完成。

## 4. 暫存版檔案與雜湊（Fact，2026-11-01 核對）

| stage 內路徑 | 對應正式路徑 | 行尾／行數 | 暫存版 sha256 | 正式檔 sha256 |
|---|---|---|---|---|
| `stage\stock_daily_preopen.py` | `programs\shared\stock_daily_preopen.py` | CRLF／1,725 | `218a05beb83b…3625` | `a56f6be72858…a823` |
| `stage\stock_daily_preopen_reader.py` | `programs\shared\stock_daily_preopen_reader.py` | LF／362 | `fa9228b57c22…2d73` | `151c45c7293c…c69a` |
| `stage\RUN_PREOPEN_DAILY.cmd` | `programs\shared\RUN_PREOPEN_DAILY.cmd` | CRLF／91 | `c94b962f6c7f…0d74` | `964785344482…7ba7` |
| `stage\tests\test_stock_daily_preopen.py` | `programs\shared\tests\test_stock_daily_preopen.py`（既有測試的修補複本：`_fake_predict` 加 6 行假日曆） | CRLF／866 | `7c8efe40ca31…6246` | `ec6a48a88fc4…27cd` |
| `stage\tests\test_preopen_stage_ops.py` | `programs\shared\tests\test_preopen_stage_ops.py`（新增，26 個測試；需登錄 `STABLE_PROGRAM_PATHS.txt`） | LF／1,114 | `883003362c7d…34f0` | —（新檔） |

差異檔（審閱用）：`deploy\stage.diff`（正式檔 → v3.1；`stock_daily_preopen.py` +971／−66、reader +101／−1、cmd +71／−7）、`deploy\ops_patch_v3.diff`（v2 → v3.1）、`deploy\ops_patch_v3_1.diff`（v3 → v3.1：py +80／−21、reader +34／−5、cmd 0、stage 測試 +133／−1）。歷史版本備份：`deploy\v2\`（預設拒發版）、`deploy\v3\`。結算端（`cmd_settle`／`settle_rows`／`forward_summary`）自 v2 之後零 hunk，`..\dryrun\` 的帳冊副本試算仍適用。與單股軌同時部署時，reader 與既有測試改用 `deploy\merged_reader\` 的合併檔（§7.4）。

v3.1 相對 v2 新增的程式元素：`parse_closed_days()`、`central_price_max_date()`、`calendar_gate()` 改為回傳 `status`／`reasons`／`refuse`／`sessions_ahead_expected` 等、`GATE_STATUS_ORDER`／`GATE_LEGACY_CODES`、`LEDGER_REL`、`cmd_predict` 的帳冊 `try/except (sqlite3.Error, OSError)`、argparse `--strict-calendar`；reader 新增 `calendar_gate_text()`、`ledger_text()`、`PreopenSnapshot.calendar_gate` 屬性，`REASON_TEXT_ZH`／`REQUIRED_COLUMNS` 不變；`load_latest` 不受影響（`snapshot_sha256` 只涵蓋 parquet／CSV，manifest 新鍵不在雜湊內）。

## 5. 行為矩陣（v3.1；每日排程、不帶 `--strict-calendar`）

| 情境 | 發布／exit | manifest 註記 | 報告文字（`describe()` 附加） | 結算 |
|---|---|---|---|---|
| 正常（訊號日＝日曆最後 session，目標日為下一個平日） | 發布／0 | `calendar_gate.status=ok`、`reasons=[]`、`degraded=false`、`ledger={status: ok, rows: N}` | 無（與現行逐字相同） | `settled_h1` 計分 |
| 中央價格落後、同 head（10-29 情境：訊號 10-27＝日曆最後 session，目標 10-29，10-28 不在日曆內） | 發布／0 | `signal_gap_unverified`、`sessions_ahead_expected=2`、`degraded=true` | 「中央價格可能落後（20261028 不在已驗證日曆中、也非已知休市日）：本快照以 20261027 訊號日重發，目標日 20261029（可能跨 2 個交易日，結算時不計入實盤統計）；若 20261028 為臨時休市請宣告」 | `settled_multi_session` 不計分 |
| 中央價格落後、日曆領先（訊號日 < 日曆最後 session，或面板訊號日 ≠ 中央 `actual_max_date`） | 發布／0 | `stale_signal`、`degraded=true` | 「中央價格落後：本快照以 20261027 訊號日重發，目標日 20261029（跨 2 個交易日），結算時不計入實盤統計」 | `settled_multi_session` |
| 颱風休市隔日，無宣告（訊號 10-22、目標 10-27、日曆到 10-22） | 發布／0 | `signal_gap_unverified`（10-23 unverified、10-26 fixed_holiday）、expected=2 | 「中央價格可能落後（20261023 …）：本快照以 20261022 訊號日重發，目標日 20261027（可能跨 2 個交易日…）；若 20261023 為臨時休市請宣告」 | 事後日曆確認 10-23 休市 → `sessions_ahead=1` → 計分。describe 的「結算時不計入實盤統計」是保守說法，是否計分以 settle 依已驗證日曆的分類為準；要改措辭需另案改 reader `calendar_gate_text()` |
| 颱風休市隔日，有宣告（`--closed-day 2026-10-23` 或 `announced_closures.json`） | 發布／0 | `ok`、`declared_closures=["2026-10-23"]`、expected=1 | 無 | `settled_h1` |
| 目標日為週末／固定假日／已宣告休市／已驗證非交易日 | 發布／0 | `target_weekend`／`target_fixed_closure`／`target_declared_closure`／`target_verified_closure`、`degraded=true`（事後重跑 10-22→10-23 時 reasons 另含 `stale_signal`） | 「目標日 20261031 為週末，結算時不計分」等 | `void_target_not_session` |
| 訊號日非交易日（中央發布了非交易日的價格） | 發布／0 | `stale_signal`＋`signal_not_session` | 「中央價格落後：本快照以 20261023 訊號日重發，目標日 20261027；訊號日 20261023 不是已驗證交易日，結算時不計分」 | `void_signal_not_session` |
| 日曆不可用（中央日曆缺檔／雜湊不符／格式錯） | 發布／0（目標日照 `resolve_target` 規則） | `calendar_unavailable`、`calendar_error_code`（可再加 `signal_gap_unverified`） | 「已驗證日曆不可用（preopen_calendar_invalid），訊號日與目標日未經核對；目標日 20261019 為假設值，結算時以日曆為準」 | settle 略過、維持 pending |
| 中央 head 缺／無法解析 `actual_max_date` | 發布／0（訊號日取面板最後日） | `central_max_date_unknown`、`central_price_max_date=null`（訊號日 < 日曆最後 session 時另加 `stale_signal`） | 「中央價格最新日未知（head 中繼資料缺 actual_max_date），訊號日未與中央核對」 | 不變（依日曆結算） |
| 宣告檔格式錯／`--closed-day` 格式錯（例 `2026/10/28`） | 發布／0（忽略錯的值，同列其他正確值仍採用） | `closures_file_invalid`／`closed_day_cli_invalid`、`closed_day_invalid=["2026/10/28"]` | 「宣告休市設定檔無法使用（已忽略）」／「命令列宣告的休市日格式無效（2026/10/28，已忽略）」 | 不變 |
| 美股缺漏／籌碼 stale（既有） | 發布／0 | gate `ok`；`degraded_reasons` 含 `us_overnight_missing`／`chips_stale` | 現行文字 | 不變 |
| 帳冊 `state\forward.sqlite3` 被鎖定／損毀／`state` 不是目錄 | 發布／0（快照、manifest、`latest.json` 照常；本次未入帳） | 頂層 `ledger={status: error, error: "OperationalError: database is locked", rows: 0}`；**不標 degraded** | 「帳冊寫入失敗（本快照未入帳，結算時不計分）」 | 該 run 不在帳冊 → 不結算、不計分、不進校準池 |
| `--no-latest`（QA 快照） | 快照寫出、不發布、不入帳／0 | 如實記錄，`ledger.status=skipped` | — | — |
| cmd：settle／calibrate／train 任一步失敗；日誌目錄不可寫 | predict 仍執行／結束碼取自 predict | — | — | — |
| `--strict-calendar`（手動 QA）且 status≠ok | **不發布**／1，寫 `latest_refusal.json` | `calendar_gate.refuse=true`、`policy=strict` | — | — |
| 明確給錯 `--target-date`（≤ 訊號日或格式無效）；模型指標缺失／雜湊不符；中央 manifest 雜湊不符 | 不發布／1（操作或完整性錯誤，非舊資料；既有行為） | — | — | — |

## 6. 已驗證（Fact；全部在暫存複本或研究目錄，未碰 `D:\stock` 的 .cmd、未寫正式資料根與中央）

- 測試（`D:\stock\.venv\Scripts\python.exe -B -m pytest -q -p no:cacheprovider`；`PREOPEN_REAL_CALENDAR` 指向 `..\dryrun\calendar\70d4a838….json`）：
  - 基準：正式複本 `tests\test_stock_daily_preopen.py` 38 passed／1 skipped；v2 暫存複本 58 passed／1 skipped。
  - **v3.1 暫存複本**（整個 `programs\shared` 複本＋v3.1 三檔＋修補的既有測試＋新測試）：**64 passed／1 skipped、0 failed**（38 既有＋26 新；skipped 為 `PREOPEN_PARITY` 未設的 parity 測試）。
  - `deploy\stage\tests` 獨立模式（`PREOPEN_STAGE_SHARED_DIR=<正式複本>`）：26 passed（含帳冊鎖定測試，約 5～7 秒）。
  - **未修補**的正式既有測試檔對 v3.1：1 failed／37 passed／1 skipped——失敗的是 `test_predict_manifest_marks_chips_freshness_and_require_flag`（固定假訊號日 2026-10-16、未假造日曆 → 掃描中央真實日曆最後交易日 10-30 → 如實標 `stale_signal`／`degraded=true`）。這是預期行為，修補複本加 6 行假日曆即通過；**部署時必須一併覆蓋該測試檔**。
- 端到端（`deploy\e2e_v31\`）：`calendar_gate` 純函式 23 例（`scenarios_v31.txt`）；predict 端到端 22 例（`e2e_predict_v31.txt`：17 個每日情境＋4 個帳冊情境＋1 個 strict，假模型／假 head／假面板，不碰中央）——除 strict 如預期拒發外，全部 exit 0、`latest.json` 更新、manifest 與 `latest.json` 一致、無 `latest_refusal.json`、`reader.load_latest` 可讀。
- cmd（`deploy\cmd_run_v3\`，暫存區複本、絕對路徑、獨特檔名 `RUN_PREOPEN_DAILY_V3_STAGE.cmd`、`find_python.cmd` 樁指向 `D:\stock\.venv`）：Run A 真實 producer＋空資料根：settle 0 → train 1 → predict 1 → 結束碼 1（證明任一步失敗仍走到 predict、結束碼為 predict 的）；Run B 樁 settle／train 失敗、predict 0 → 三步皆執行、結束碼 0；Run C 樁 predict 5 → 結束碼 5。v3.1 的 cmd 與 v3 相同（sha 相同）。
- `stage.diff` 在暫存 git repo（`core.autocrlf=false`、正式檔 LF 正規化）`git apply --check` 通過、套用後與 v3.1 逐位元相同。**Not verified**：直接在 `D:\stock` 工作樹（正式檔為 CRLF）`git apply stage.diff`——因此 §8 的部署以**複製 stage 檔**為準（行尾已與正式檔一致），diff 只作審閱。
- 審查：v2 的 20 項審查發現（`deploy\CHANGES_stage.md`「審查發現處置（20 項）」表）全部處置；v3→v3.1 的 6 項發現（同檔 v3.1 表）：`--closed-day` 格式錯仍 exit 1、帳冊鎖定／損毀時不發布、head 缺 `actual_max_date` 誤標中央落後、行為矩陣兩列與程式不符——四項已修補；TWNextDayTop10 `--model-source preopen` 強制模式仍會中止——列為 §7.3／§7-B.9；核實紀錄無需修改。**Not verified**：審查者人數與各項嚴重度（major／minor）未記錄在磁碟上。
- 衛生：研究目錄無 `__pycache__`／`.pytest_cache`；`D:\stock` `git status` 除本交接檔（未追蹤）外乾淨；正式四檔 sha 未變。
- 未做：以真實 producer 載入中央資料跑完整 cmd（train／predict／settle 一讀 head 就會在中央 `_query` 建立租約，研究任務禁止寫中央）；`..\dryrun\` 未因 v3.1 重跑（結算端零 hunk）；單股與 Telegram 的消費端測試未在本軌跑（reader 的既有測試在 64 passed 內；合併 reader 的三檔測試結果見 §7.4）。

## 7. 請 codex 確認的事項

請逐項回覆 **confirmed／needs_change／blocked** 並附證據路徑；7-B 的項目只需補事實，不替使用者決定。

### 7-A 請 codex 核對

1. **核對暫存版**：§4 的 sha256 與行尾；`deploy\stage.diff` 的審閱；`deploy\CHANGES_stage.md`（v3.1 段置頂，含逐條審查處置、manifest 欄位、reader 文字、完整行為矩陣、部署與回復）與 `deploy\TEST_RESULTS.txt`。
2. **重跑測試**（建議在複本而非 `D:\stock`）：在短路徑（例如 `C:\Users\USER\AppData\Local\Temp\claude\<短名>\shared`）複製 `programs\shared`（不含 `__pycache__`），覆蓋 §4 五個檔後執行
   `set PYTHONDONTWRITEBYTECODE=1 & set PYTHONUTF8=1 & set PYTHONIOENCODING=utf-8 & set PREOPEN_REAL_CALENDAR=D:\TWStockData\program_data\stock_daily_c0\candidates\h1_ops_review_20261031\dryrun\calendar\70d4a838b33e06ddd6f673b1e2e5502a30362f5b7ae158aa6ae0ba77d527298d.json & D:\stock\.venv\Scripts\python.exe -B -m pytest -q -p no:cacheprovider --basetemp <短路徑>\tmp tests\test_stock_daily_preopen.py tests\test_preopen_stage_ops.py -rs`，預期 64 passed／1 skipped；未設 `PREOPEN_REAL_CALENDAR` 為 63 passed／2 skipped（第 983 行的真實日曆測試跳過）。複本與 `--basetemp` 必須是短路徑，否則 cmd 煙霧測試（第 937 行以 `tmp_path` 為 cmd.exe 工作目錄）會以 `WinError 267` 失敗；新測試預設以 `D:\stock\programs\shared` 的正式 reader 作唯讀對照（`PREOPEN_STAGE_SHARED_DIR` 可改）。若 reader 已依 §7.4 合併，預期改為 68 passed／1 failed／1 skipped（見 §7.4）。
3. **消費端（事實核對）**：`D:\stock\TWNextDayTop10\preopen_source.py` 第 397-409 行 `confirm_target()` 在 C0 有已驗證下一交易日且與快照 `target_date` 不同時，`auto` → 回退 C0（報告照發，H1 改用 C0），`--model-source preopen` → `PreopenRequiredError` 中止；`choose()` 第 345／351 行 `load_latest(expected_signal_date=…)` 遇 `preopen_stale` 在 preopen 模式也會中止。`RUN_TOP10.cmd` 不帶 `--model-source` 時由 `app.py`／`preopen_source.resolve_model_source()`（第 130–141 行：命令列 → 環境變數 `TOP10_MODEL_SOURCE` → `auto`）決定；目前 User／Machine 皆未設該環境變數、排程 04 的參數為 `--no-pause --no-open`，所以預設排程為 auto、不受影響。是否要讓裁示涵蓋強制模式見 §7-B.9。另：`preopen_meta` 白名單（第 297-299 行）不含 `calendar_gate`／`ledger`／`degraded`，新鍵被忽略；`describe()` 文字經 `note()`（第 310 行）與 `reporting.py` 第 713 行 `html.escape` 輸出。
4. **reader 兩軌合併**：另一研究軌「單股 H63 誠實化」（`..\single_stock\stage\shared\stock_daily_preopen_reader.py`，sha `88b544f4794211ee…`，相對正式檔 +60／−0）只新增 (a) `REASON_TEXT_ZH` 的 3 個代碼 `preopen_summary_missing`／`preopen_summary_invalid`／`preopen_summary_schema_mismatch`、(b) 常數 `FORWARD_SUMMARY_SCHEMAS`／`FORWARD_SUMMARY_VIEWS`、(c) `forward_summary_path()`／`load_forward_summary()`（hunk 在正式檔第 56、246 行附近）；本軌 hunk 在第 11、113、146、256 行附近，兩者**不重疊**（`patch --dry-run` 兩種順序皆成功且結果相同）。既有測試 `REASON_CODES` 須同步加這 3 個代碼（單股軌 `shared_tests` 的 +2 行）。
   - **審查者已完成三方合併**，放在 `deploy\merged_reader\`（README 記錄來源 sha、合併方式與測試）：`stock_daily_preopen_reader.py`（LF 422 行，sha256 `3e3fce07b259d2729e53c1d5bb22ea6485a4fa7228a1756ccfafa77986442540`；與 v3.1 差 +60／−0、與單股軌差 +101／−1、無重複定義、`ast.parse` 通過）、`tests\test_stock_daily_preopen.py`（CRLF 868 行，sha256 `aca1df4b1458f0e39aa0951b709466019b81fa3e6cdc8c10e40b6769bd975666`；v3.1 修補複本＋`REASON_CODES` +2 行）。兩軌同時部署時以這兩個檔取代 §4 的 reader 與既有測試複本；單股軌其餘檔案依其 CHANGES §套用步驟以 `git apply stage.diff` 套用，但需排除已合併的 reader 與 shared 既有測試兩個 hunk（例如 `--exclude=programs/shared/stock_daily_preopen_reader.py --exclude=programs/shared/tests/test_stock_daily_preopen.py`；**Not verified**：未實測）。
   - 合併複本測試（`merged_reader\pytest_merged_run1.txt`；`programs\shared` 複本＋v3.1 三檔＋merged 兩檔＋單股軌 `tests\test_stock_daily_preopen_reader_summary_stage.py`）：三個測試檔 **67 passed／2 failed／1 skipped**。失敗 1 `test_cmd_daily_flow_order_logging_exit_code_and_fallbacks` 是暫存路徑超過 260 字元的環境因素（短路徑直接呼叫 → PASSED，`cmd_test_driver.txt`）；失敗 2 `test_preopen_stage_ops.py::test_reader_describe_old_snapshot_and_ok_gate_are_verbatim` 第 1039 行 `reader.REASON_TEXT_ZH == mod.REASON_TEXT_ZH` 對正式 reader 的相等斷言（merged 多 3 個代碼；第 1036–1038 行 `describe()` 逐字對照全部通過；部署後 `official.resolve() == reader.__file__` 自我比對會跳過這段，`PREOPEN_STAGE_SHARED_DIR=<合併複本>` 重跑 2 passed）。扣除環境因素為 **68 passed／1 failed／1 skipped**。請 codex 擇一回覆：(a) 把第 1039 行改為「正式檔鍵值為 merged 子集且共有鍵文字相同」，例如 `assert reader.REQUIRED_COLUMNS == mod.REQUIRED_COLUMNS and all(reader.REASON_TEXT_ZH[k] == v for k, v in mod.REASON_TEXT_ZH.items())`；(b) 接受部署前複本有這 1 個已知失敗。
   - **鍵名不一致（兩軌同時部署前必須對齊）**：單股軌 `FORWARD_SUMMARY_VIEWS = ("first_publication", "last_publication", "all")`、`load_forward_summary()` 讀 `views.first_publication`，但 v3.1 結算端 `forward_summary()`（`stock_daily_preopen.py` 第 1616 行 `VIEW_FIRST_SCORABLE`）寫的是 `views.last_publication` 與 `views.first_scorable_publication`。以 merged reader 唯讀載入 `..\dryrun\state\summary.json`（v2）：`first_publication=None`、`last_publication` 與 `all` 有值——不會壞，但單股軌 stage 版 `single_stock_preopen.py`（`..\single_stock\stage\src\`，第 120 行）的「第一次發布口徑（views.first_publication）」標籤永遠不會出現、只會退回 `last_publication`；正式 `state\summary.json` 目前 v1 → `version=1`、只有 `all`。請 codex 擇一：改 reader 常數與單股消費端標籤為 `first_scorable_publication`，或改結算端鍵名；兩者都要更新對應測試（單股軌 `test_stock_daily_preopen_reader_summary_stage.py` 夾具用 `first_publication`）。
   - Telegram 常駐服務：已核對 bot 行程不載入 reader（`programs\telegram_report_delivery` 無 `stock_daily_preopen_reader` 字樣），單股分析由 bot 以子行程執行（`telegram_stock_command_bot.py` 第 965／1170／3124 行 `subprocess.run`、第 3291 行 `Popen`；第 139 行註解：H1 區塊由 `single_stock_preopen.py` 在分析端寫入），子行程每次重新匯入 reader，**只部署本軌不需重啟 Telegram 服務**。同時部署單股軌（改 `stock_candidate_report`／bot）時，依其 CHANGES §套用步驟 3 由使用者以系統管理員執行 `RESTART_COMMAND_BOT_SERVICE.cmd -Restart -ConfirmRestart`。
5. **updater 端（codex 負責）**：(a) 在 11-24 自動重訓前回補 `global_market_history` 的美股 10-23、10-26（需連網抓 FinMind／Yahoo，屬外部網路操作，依 AGENTS.md 需使用者明確授權；回補後請回覆兩日的 `us_*` 列數）；(b) `finmind_price_adj.actual_max_date` 維持 `YYYY-MM-DD`（v3.1 的 `central_price_max_date()` 接受 `YYYY-MM-DD`／`YYYYMMDD`，其他格式會被註記 `central_max_date_unknown` 而不是當成新鮮）；(c) 已驗證日曆 sidecar 的 route 與 sha 宣告維持現狀（head sidecar 失效時 producer 會退回掃描 `_calendar\verified`）。
6. **排程**：(a) 確認 10-31 六個任務未執行的原因與 10-30 23:00 任務的 `0xC000013A`、06:30 任務的 `0xA`（Task Scheduler 事件記錄與 `D:\TWStockData\updater_runtime` 日誌屬 codex 範圍）；(b) 部署後 03 任務仍只 `call RUN_PREOPEN_DAILY.cmd`，**不得加 `--strict-calendar`**；(c) 請回覆 updater 持有 writer lock 期間 `pdata.read_head` 會 (i) 讀到前一 head、(ii) 等待、或 (iii) 拋 `PreopenDataError`，附程式依據即可、不需實測；(d) 部署不修改 `preopen_schedule\PREOPEN_SCHEDULE_DEFINITION.json` 與六個已註冊任務；日後 `-Verify`／重新註冊時 03 任務的 arguments 必須維持 `/d /c call D:\stock\programs\shared\RUN_PREOPEN_DAILY.cmd`（`INSTALL_PREOPEN_SCHEDULE.ps1` 第 109 行以 Ordinal 比對 arguments，不一致即 `mismatch_refusing_to_overwrite`）。JSON 第 42 行 03 任務描述「前一晚美股缺漏時拒絕發布，消費端自動改用 C0」自 2026-10-18 裁示起已過期，於 §8 文件步驟更新（`-Verify` 不比對 description，已註冊任務的描述不會跟著變）。
7. **治理**：(a) 新測試 `programs\shared\tests\test_preopen_stage_ops.py` 登錄 `programs\STABLE_PROGRAM_PATHS.txt`（目前 shared 測試只有第 83 行 `test_stock_daily_preopen.py`）；若依 §7.4 同時部署單股軌 reader，也登錄 `programs\shared\tests\test_stock_daily_preopen_reader_summary_stage.py`（單股軌其餘新測試依其 CHANGES §1 表登錄）；跑 `VERIFY_STABLE_PROGRAM_PATHS.cmd`；(b) `programs\shared\README.md` 的「開盤前版 H1（ADR-0008）」段落補上 `calendar_gate` 狀態碼、`--strict-calendar`、describe 文字與 `logs\`；(c) 本次改變正式發布門檻並修訂 ADR-0008，依 `docs\decisions\README.md` 應建 ADR：請 codex 以 **ADR-0009**（現有 0001～0008，編號不回收）建立 `proposed` 草案（內容採 §10 骨架），部署前至少為 proposed；改 `accepted` 需使用者核准並記錄核准原文與日期；(d) 部署後在 `docs\HANDOFF.md` **新增**「開盤前版 H1（ADR-0008／0009）」段落（目前全檔無任何開盤前版字樣）：入口 `RUN_PREOPEN_DAILY.cmd`、資料根、`latest.json` 的 `calendar_gate`／`ledger` 鍵、`--strict-calendar` 僅手動、summary v2 口徑、阻塞事項；(e) 正式驗收 `D:\TWStockData\services\acceptance\latest.json` 自 2026-08-23 為 blocked，本次不改變其狀態。

### 7-B 使用者待決（codex 只補事實，不替使用者決定）

8. **計分口徑**：summary v2 內建的兩個口徑目前都約 35%：`all`／`views.last_publication`（最後一次發布）與 `views.first_scorable_publication`（每 (signal_date, stock_id) 最先的可計分列；在目前帳冊上與前者相同，`..\dryrun\state\summary.json` 兩者 `top10_hit` 皆 0.35、settled_days 6）。「第一次發布對實際下一 session 計分、void 列也計」的替代口徑（約 40%）**尚未實作在結算端**，只在 `..\dryrun\summary_alt_first_forecast_next_session.json`（schema `research-alt-view-first-forecast-vs-next-verified-session`，由 `compare_and_alt_view.py` 另算、帳冊未寫入）；若使用者選此口徑，需另案修改 `forward_summary()` 與摘要 schema 並重跑測試，不是部署後選一個 view 就有。報告 `..\report\模型盤點與優化建議_20261031.md` §5.1 的建議未說明這點，待一併更正。
9. **強制模式殘餘阻擋**：§7.3 的 `--model-source preopen` 中止路徑是否也要納入裁示（快照 `calendar_gate.status≠ok` 時只註記不中止）——需另案修改 `TWNextDayTop10\preopen_source.py`；預設排程（auto）不受影響。
10. **Telegram 個股 caption 是否顯示守門文字**：目前不顯示（§3.4）。若要顯示，需另案在 `single_stock_preopen.py` 的區塊加入例如 `calendar_note_zh`（比照 `_chips_note`）並在 `_preopen_caption_lines` 附加（或直接附加 `block['describe']`），同步更新 `tests\test_telegram_preopen_h1_caption.py`。

## 8. 部署步驟（需使用者核准；本次未執行）

1. **時窗**：在 11-01（週日）完成，或 11-02 的 06:30 前／08:00 後；避開 07:15–08:00（03／04／05 會連續以檔案路徑載入 reader 與快照，08:00 傳送 MIX 報告）。若 11-02 07:15 已先以舊版執行：當日快照無守門註記、帳冊以舊 20 欄入帳，部署後第一次 settle 會重新分類，不需重跑 predict。
2. 先建立 ADR-0009（`proposed`，依 §10 骨架）；使用者核准部署時改為 `accepted` 並記錄核准原文與日期。
3. 確認排程 03／04／05 不在執行中、Telegram 常駐服務不在送件中；備份到研究目錄 `D:\TWStockData\program_data\stock_daily_c0\candidates\h1_ops_review_20261031\deploy\formal_backup_<yyyymmddThhmmss>\`（比照 ADR-0008 的 `deploy_snapshots\` 慣例：依原路徑鏡像存放 `programs\shared` 四個正式檔與 `D:\TWStockData\program_data\stock_daily_preopen\state\forward.sqlite3`、`state\summary.json`，並寫 `original_sha256.txt`）；**不在 `D:\stock` 內建立任何副本**（AGENTS.md 禁止日期化程式副本；`.gitignore` 只忽略 `backup/`，`backups/` 會污染 `git status`）。
4. **複製** `stage\` 的三個程式檔到 `programs\shared\`、`stage\tests\test_stock_daily_preopen.py` 覆蓋既有測試、`stage\tests\test_preopen_stage_ops.py` 新增到 `programs\shared\tests\`；逐檔比對 sha256 與 §4 相同。與單股軌同時部署時，reader 與既有測試改用 `deploy\merged_reader\` 的兩個檔（sha 見 §7.4），且先完成 §7.4 的鍵名對齊。
5. 登錄 `STABLE_PROGRAM_PATHS.txt`（§7.7(a)），跑 `programs\VERIFY_STABLE_PROGRAM_PATHS.cmd`。
6. 測試：於 `D:\stock` 設 `PYTHONDONTWRITEBYTECODE=1` 後執行 `D:\stock\.venv\Scripts\python.exe -B -m pytest -q -p no:cacheprovider programs\shared\tests\test_stock_daily_preopen.py programs\shared\tests\test_preopen_stage_ops.py`（預期 64 passed／1 skipped；未設 `PREOPEN_REAL_CALENDAR` 為 63 passed／2 skipped），再以 `programs\single_stock_analysis\tools\RUN_TESTS.cmd` 與 `programs\telegram_report_delivery\RUN_TESTS.cmd` 跑消費端測試（或至少 `test_single_stock_preopen.py`、`test_telegram_preopen_h1_caption.py`；前者驗證 describe 進入單股區塊，後者只驗證 caption 既有欄位、不含 describe。從 `D:\stock` 根直接跑是否需額外 `PYTHONPATH`：**Not verified**）。
7. 手動執行一次 settle：`D:\stock\.venv\Scripts\python.exe -B D:\stock\programs\shared\stock_daily_preopen.py settle`（cwd `D:\stock\programs\shared`，不帶 `--root` 即正式資料根）。這是對正式帳冊的寫入，需使用者於對話中明確核准；不得與排程 03（07:15）同時進行（帳冊只有 `stock_daily_preopen.py` 自己讀寫；鎖定時 predict 會以 `ledger.status=error` 照常發布、該 run 不入帳）。`ALTER TABLE` 加 `sessions_ahead`／`settlement_status`／`settlement_source` 由任何子命令第一次開啟帳冊時執行（`ledger()` 第 679–690 行，不只 settle）；首次全量重分類 19,174 列在副本耗時約 1～8 秒（`--resettle` 可全部重算）。兩種 `--price-source`（預設 `panel`、或 `cache` 直讀同 head 官方收盤快取）都會 `pdata.read_head`（第 1444／1457 行，會在中央 `_query` 建立租約），結果相同。預期分類數（`deploy\CHANGES_stage.md` 第 231 行）：void 1,915、multi-session ≈1,898、`legacy_unverified` ≈34、`settled_h1` ≈13,293＋10-29 訊號約 1,900、pending ≈120；核對 `state\summary.json` schema `…-forward-summary-v2`（含 `excluded`）與 `state\forward_daily.csv`。
8. （可選）建立 `<root>\config\announced_closures.json`；臨時休市以 `--closed-day` 宣告。未宣告只是註記，不會擋發布。
9. 下一個交易日由排程 03 跑 `RUN_PREOPEN_DAILY.cmd`；檢查 `<root>\logs\preopen_daily_<日期>.log`、`latest.json` 的 `calendar_gate.status`／`ledger.status`，以及 Top10／單股 PDF／MIX「開盤前版說明」是否出現 describe 文字。
10. 文件：`programs\shared\README.md`（§7.7(b)）、`PROGRAM_FILES.md` 第 95 行的 cmd 流程（加日誌、任一步失敗仍續跑、可選 calibrate）、`PREOPEN_SCHEDULE_DEFINITION.json` 第 42 行 03 任務描述與 `docs\PREOPEN_DAILY_SCHEDULE_PLAN_20261018.md` 失敗行為表（加日曆／帳冊註記）、`docs\decisions\README.md` 索引加 ADR-0009、ADR-0008 末尾加「修訂（<日期>）：發布政策改依 ADR-0009」短段、`docs\HANDOFF.md` 新段（§7.7(d)）。
11. Git：部署後 `git status` 應只含上述授權檔案；是否 commit、在哪個分支（目前 `report/ranking-first-layout`，main 為 `main`）由使用者決定，codex 不自行 commit／push。

## 9. 回復方式

- 程式：把備份的四個檔案複製回 `programs\shared\`（含 reader 與既有測試），刪除新測試並還原 `STABLE_PROGRAM_PATHS.txt`；若已 commit，優先以 `git revert` 該 commit 回復程式與 STABLE 清單（`docs\decisions\README.md`：優先使用已納管的 Git 歷史），帳冊仍以備份還原。
- 帳冊：舊版 `predict` 以 20 個位置參數 `INSERT`，加欄後會失敗；回復舊版請一併還原備份的 `forward.sqlite3`／`summary.json`。
- 快照：v3.1 發布的 `latest.json` 多了 `calendar_gate`、`ledger` 等鍵，舊版 reader 忽略未知鍵、可照常讀取（只是不顯示守門文字）。`latest_refusal.json` 只在手動 strict 時產生。
- 若只想回到 v2 的「預設拒發」行為：`deploy\v2\` 有 v2 的三個檔（但這違反裁示，不建議）。

## 10. ADR-0009 草案（proposed；骨架依 `docs\decisions\README.md` 必要欄位，請 codex 落地）

- `# ADR-0009：開盤前版 H1 的發布政策——舊資料、日曆與帳冊問題只註記、不阻擋發布`
- ID：ADR-0009；日期：<部署日>；狀態：`proposed`（使用者核准部署後改 `accepted`，並記錄 2026-11-01 裁示原文「我的要求就是舊資料不會阻擋程式執行及報告發布」與核准日期）。
- 範圍：`programs\shared\stock_daily_preopen.py`（`cmd_predict`、`calendar_gate`、`cmd_settle`）、`stock_daily_preopen_reader.py`（`describe()`）、`RUN_PREOPEN_DAILY.cmd`、TWNextDayTop10／單股／MIX 的 H1 顯示；supersedes：N/A（不取代 ADR-0008，但修訂其「決定」段兩句，見「決定」）；superseded_by：N/A。
- 背景：10-23 颱風休市與 10-29 中央價格落後兩個事件顯示，沒有日曆守門時帳冊會把跨 session 的報酬計入實盤統計（§2）；使用者 2026-11-01 裁示「舊資料不會阻擋程式執行及報告發布」。
- 選項：(A) 預設拒發（v2）；(B) 一律發布、manifest 與報告註記、結算端分類不計分（v3.1）；(C) 中央落後時改由消費端回退 C0（只在 TWNextDayTop10 的 auto 模式存在）。
- 決定：B。發布品質問題只能以 `degraded`／`calendar_gate`／`ledger` 註記與 `describe()` 文字呈現；拒發僅限手動 `--strict-calendar`；帳冊與校準池由結算分類（void／multi-session／legacy 不計分）保護；每日流程任一步失敗不得阻止後續步驟。**本 ADR 修訂 ADR-0008「決定」中「前一晚美股缺漏預設拒絕發布」（第 30 行）與「消費端於快照不可用或過期時回退 C0」（第 29 行）兩句**：產製端一律發布並以 `degraded`／`calendar_gate`／`ledger` 註記（含 2026-10-18 裁示的美股與籌碼新鮮度，該次裁示已落實於程式與 README 但未記入 ADR-0008）；消費端 auto 模式的回退條件不變（`preopen_source.py` 第 397–409 行 `confirm_target`）。ADR-0008 末尾加「修訂（<日期>）：發布政策改依 ADR-0009」短段（比照其 2026-10-22 修訂段），`docs\decisions\README.md` 索引加 ADR-0009 一列。
- 理由：帳冊三個事實（§2）與裁示；拒發不能改善資料，只會讓當日沒有 H1。
- 證據：列 `deploy\TEST_RESULTS.txt`、`deploy\e2e_v31\`、`deploy\cmd_run_v3\`、`deploy\merged_reader\`、`..\dryrun\DRYRUN_RESULTS.md` 的精確路徑與 2026-11-01 核對狀態；正式環境首次 settle 與首日排程結果標「待執行」。
- 後果：`degraded=true` 的語意擴大到所有 gate≠ok 的日子；跨 session 的舊訊號重發仍會進帳冊但不計分；颱風後第一個交易日未宣告時註記為保守說法、事後以日曆為準；strict 拒發與真正失敗的結束碼皆為 1，以 `latest_refusal.json` 區分。
- 回復方式：見本檔 §9（備份複製回、帳冊還原，或 `git revert`）。

## 11. 共同約束（codex 與 Claude）

- 同一時間只有一方修改 `programs\shared`；對方的修改以交接檔溝通，不直接改對方負責的程式。
- 不在研究目錄以外執行研究用的 .cmd；不以相對路徑 `cmd /c RUN_PREOPEN_DAILY.cmd` 之類方式誤觸正式入口；不終止不是自己啟動的 Python 程序。
- 研究目錄 `D:\TWStockData\program_data\stock_daily_c0\candidates\` 的內容不得提交 Git；正式資料根與中央只由正式流程寫入。
- 根目錄 `D:\stock\.venv` 由 Telegram 服務使用，不得改動。

## 12. 證據索引

- 暫存版：`D:\TWStockData\program_data\stock_daily_c0\candidates\h1_ops_review_20261031\deploy\stage\`；說明 `deploy\CHANGES_stage.md`、`deploy\TEST_RESULTS.txt`；差異 `deploy\stage.diff`、`ops_patch_v3.diff`、`ops_patch_v3_1.diff`；證據 `deploy\e2e_v31\`（情境表、端到端、三份 pytest 日誌、腳本）、`deploy\cmd_run_v3\`；備份 `deploy\v2\`、`deploy\v3\`；兩軌合併 reader `deploy\merged_reader\`（README、merged reader、合併後既有測試、`pytest_merged_run1.txt`、`cmd_test_driver.txt`、`pytest_verbatim_selfcompare_run3.txt`）。
- 帳冊副本試算：`..\dryrun\DRYRUN_RESULTS.md`、`dryrun\state\{forward.sqlite3, summary.json, forward_daily.csv}`、`dryrun\summary_alt_first_forecast_next_session.json`（替代口徑研究試算，未實作）。
- 總報告：`..\report\模型盤點與優化建議_20261031.md`（§1 現況、§2 問題 P1～P9、§4.1 暫存版、§5 待決；注意 §4.1 的帳冊試算數字 1,923／13,419／1,917／62.7%／0.661 是修補前舊口徑，以本檔 §2 為準，§5.1 的 40% 口徑尚未實作，見 §7-B.8）；Top10 事後分析 `..\analysis\top10_posthoc.md`；單股軌 `..\single_stock\CHANGES_stage.md`、`..\single_stock\stage.diff`。
- 模型端實驗（已結案，不採用）：`candidates\h1_yo1_usmask_20261031\report\研究報告_20261031.md`（`yo1`＝以「次日開盤買進→收盤」報酬作第二個分類頭的實驗，與美股逐工具遮罩皆未通過事前登記條件）。
- 正式環境：`D:\TWStockData\program_data\stock_daily_preopen\{models\current.json, predictions\latest.json, state\forward.sqlite3, state\summary.json}`；中央 `D:\TWStockData\shared_market_data\_metadata\stock_data_current.json`、`_objects\46fd4011…`（global_market_history）、`_calendar\verified\`；排程定義 `programs\shared\preopen_schedule\PREOPEN_SCHEDULE_DEFINITION.json`、計畫 `docs\PREOPEN_DAILY_SCHEDULE_PLAN_20261018.md`；ADR-0008；消費端 `TWNextDayTop10\preopen_source.py`、`programs\single_stock_analysis\src\single_stock_preopen.py`、`D:\MIX All\src\mixstock\preopen.py`、`programs\telegram_report_delivery\telegram_stock_command_bot.py`。
