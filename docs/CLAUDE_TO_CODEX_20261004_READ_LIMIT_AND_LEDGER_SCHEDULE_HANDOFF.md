# Claude → codex 交接：移除 official_market_daily 的 512 MiB 讀取上限、前瞻帳本每日排程

- 日期：2026-10-04。本檔只涵蓋這一批（rl_investigate → rl_build／ld_build → rl_review → rl_final），是獨立的新交接檔，不延續先前的交接檔。先前三個月排名模型的交接見 `CLAUDE_TO_CODEX_20261004_THREE_MONTH_XS_RANK_HANDOFF.md`（其 §6「讀取政策例外」已標註由本檔取代）。
- 所有修改都在 `three_month_impl/staging/programs`；D:/stock、D:/TWStockData、D:/TWStockUpdater 與工作排程器全程唯讀（D: 寫入防護 0 次嘗試）。排程**尚未註冊**。
- 要部署的位元組：`fix/merge/rl_final/merged/programs`（D:/stock 唯讀快照與 staging 的三方合併），清單與 sha256 在 `patches/FILES.md`／`FILES.json`，步驟在 `DEPLOY_PLAN.md`（第 4 版）。

## 1. 使用者的決定（2026-10-04）

1. 部署已授權（等你暫停修改後再做，每個寫入 D: 的步驟仍逐項核准）。
2. **512 MiB consumer 讀取上限要「修掉」，不是給例外**：official_market_daily 必須能被 consumer 讀取，沒有位元組上限，記憶體也不能爆。使用者明說記憶體不是問題，但要求不能失控。
3. 為前瞻帳本建立**每日排程工作**：這批只寫好腳本，實際註冊在部署時、經使用者同意後才做。

這會推翻你 10-03 20:22Z 的設計「沒有 sealed projection 就明確不讀（consumer_read_limit／not_read），不做 fullscan」。理由與數字在 §2、§3。

## 2. 為什麼可以移除上限（調查結果）

- 上限只有一個常數 `_BATCH_OFFICIAL_MARKET_MAX_BYTES = 512 MiB`（stock_data_sources.py），用在兩處：`artifacts()`（batch prewarm 之後）與 `prewarm_stock_rows()`（多股 batch）。另有你 10-03 在 `published_single_stock_reference.py` 加的報告層規則：沒有 per-stock 快取也沒有 sealed projection 就不讀。
- 上限**真正擋住的只有** `artifacts()` 把整個物件載入記憶體：不設上限時 13.9 秒、峰值 7,535 MB，回傳後仍佔 3,877 MB（`_batch_artifacts` 整個 session 都留著）。
- 它同時擋住了**本來就有界**的串流路徑：

  | 路徑（1.30 GB 物件 c19c4ee7…，3,042,258 列，2,934 檔） | 修改前 | 修改後 |
  |---|---|---|
  | 單檔準備 2330（`single_stock_prepare`） | 32.8 秒、55 MB（本來就沒上限） | 22.1 秒、58.5 MB |
  | 報告 loader 冷讀（沒有 projection） | 0.06 秒、`consumer_read_limit`、0 列 | 21.9 秒、約 +38 MB；83 列（06-01 起），09-29 收盤 2,475.0 |
  | batch 50 檔 | 被上限擋下，0 列 | 23.6 秒、69.7 MB |
  | batch 全部 2,934 檔 | 不設上限的舊碼：100.9 秒、**6,801 MB** | 135.9 秒、**111 MB** |
  | `artifacts()` | 被上限擋下（回傳空） | 一律拒絕，33 MB（不設上限的舊碼：7,535 MB） |

  峰值為 Windows job object 量到的 process commit；每個情境在獨立子行程。所有路徑的 2330 列（1,217 列，列 sha256 `29fdcec9…`）彼此 byte 相同。
- 上限造成的實際影響：報告的「獨立原始收盤核對」顯示「本次讀取受限（consumer_read_limit）」、comparable 模型拿不到官方原始價、預測歷史結算拿不到官方 OHLC，而且訓練輸入取決於 Top10 有沒有先建好 projection（不具決定性）。

## 3. 改了什麼

### 3.1 `stock_data_sources.py`（從你 10:04 的 live 版 `e0defea0…` 開始改；混合 CRLF／LF 照原樣保留）

- 刪除 512 MiB 常數與兩處檢查。新增：
  - `_STREAMED_ONLY_ROUTE_IDS = {'official_market_daily'}`；
  - `StreamedRouteRequiredError(RuntimeError)`：`artifacts('official_market_daily')` 不論大小一律在讀取前拒絕，不記錯誤、不污染狀態。刻意不繼承 ValueError／TypeError，避免被既有的 `except` 吞成診斷。沒有任何正式程式用 `artifacts()` 讀這個 route。
- batch 與單檔準備：只檢查宣告的 size／sha256 格式正確；manifest_json 來源另外檢查宣告的 `record_count` 與實際讀到的列數相同（準備路徑與冷 iterator 路徑都檢查）。sha256、大小、嚴格 JSON（重複鍵、NaN、無效 UTF-8、尾端垃圾）、讀取中檔案變動的檢查都照舊。
- **記憶體上限**：一次讀取（所有股票共用）有 64 MiB 的預算 `_PREWARM_MEMORY_BUDGET`（以 canonical JSON 位元組計）。超過時每檔股票把待寫的列一次附加到自己的暫存 spool 檔（每檔每次 flush 開一次檔），取代原本 32 個 handle 的檔案池。單文件或分塊（chunk）快取格式**只依該股票自己的大小決定**，所以快取位元組與預算大小無關（測試以 4 KiB、1 byte 預算驗證 byte 相同）。
- `_write_cache_bytes`（rl_final，RL-ROBUST-2）：Windows 上別的讀者開著目標檔時 `os.replace` 會 PermissionError。快取是驗證過的 payload 的純函數，所以目標檔已經是完全相同的位元組時就保留它；否則短暫重試（0.05–0.8 秒，共 5 次）後回報寫入失敗。修正前，訓練 batch 在報告剛好讀同一檔快取時，會讓 50 檔的官方 route 全部失敗。

### 3.2 `published_single_stock_reference.py`（封存檔；從你 09:27 的 live 版 `30cfcb75…` 開始改）

- 順序不變：先讀已驗證 pin 的 per-stock 快取，再讀 sealed projection（`allow_build=False`）。
- projection 不存在（`ProjectionUnavailableError`）或不支援（`UnsupportedProjectionError`，修正前沒有被接住）時，不再「不讀」，改為在 per-stock 鎖內：
  1. pin 已驗證時，先讀別的行程剛寫好的快取；
  2. 否則在一個 **worker**（用 `snapshot_descriptor([route])` + `from_descriptor` 從報告自己的釘選目錄還原，同 fingerprint、同快取目錄）上做一次嚴格的單檔串流準備，再從新快取讀回列（reader `published_consumer_stock_prepared`，metrics 含 `source_bytes_read`）。
  3. 成功後把驗證過的 artifact 交回報告的 sources（descriptor 的來源紀錄），並把這個 route 的檔案身分證據交給它，之後同一報告再讀這檔股票不必重新雜湊 1.3 GB。
- **失敗語意（rl_final，RL-ROBUST-1）**：
  - 完整性失敗（雜湊、大小、筆數、嚴格 JSON、讀取中變動）→ `ProjectionIntegrityError`，worker 的診斷複製到報告的 sources，報告失敗關閉（與 projection 路徑相同）。
  - 其他失敗（快取寫入失敗、I/O、pin 後物件不見、目錄無法還原）→ 不中止報告：改用報告自己的嚴格 stock-row iterator（再驗證一次 payload；快取寫不進去也會回傳列）。原因記在 `query_metrics.prepare_failure`。payload 本身讀不到時，iterator 記下 route 錯誤，狀態是 `producer_gap` 並附原因（10-03 之前的行為）。iterator 這一步若發現完整性失敗，一樣失敗關閉。
  - 因為準備在 worker 上做，失敗不會在報告的 sources 上記 route 錯誤，也不會把它切到 batch 模式（rl_build 版會把報告的 sources 設成 `_batch_prepared = True`，之後所有 `artifacts()` 的結果都會留在記憶體到 session 結束）。
- 沒有快取目錄時用嚴格、有界的 stock-row iterator。
- `consumer_read_limit` 狀態移除（顯示端仍保留舊字串的處理，供舊 JSON 使用）。

### 3.3 其他程式

| 檔案 | 改動 | 原因 |
|---|---|---|
| `tw_single_stock_3m_analyzer.py`（封存） | 刪除 xs 揭露句中「讀取限制／整檔未讀」那一句；`build_three_month_settlement_evidence` 不再對每列複製 frame attrs | 官方 frame 的 attrs 現在含每個交易日一筆 publication observation（約 586 KB）；pandas 2.3.3 `iterrows()` 會把 attrs 深複製到每一列，每份報告約多 23 秒 |
| `tw_single_stock_performance.py`（非封存、非訓練來源；rl_final，RL-PERF-1） | `process_loaded` 迴圈改在 `frame.copy(deep=False)` 且 `attrs = {}` 的淺複本上 `iterrows()` | 同上：2330 實測此迴圈 22.85 秒 → 0.01 秒 |
| `tw_single_stock_native_recipe_research.py`（非封存；rl_final，RL-CAP-1） | 刪除 `MAX_BATCH_OFFICIAL_MARKET_BYTES` 與 `pinned_descriptor_contains_official_market_daily_over_512_mib` | 使用者要求移除上限；這個檢查目前對 09-27 基準不會觸發，但會擋掉任何用新訓練建立的基準。descriptor 的 payload 雜湊仍由 `_restore_source_context` 驗證 |

### 3.4 合併方式

- 上面兩個 live 版起始的檔案（加上 `test_published_single_stock_reference.py`）記在 `fix/rebased_on_live.json`，起始的 live 位元組存在 `fix/rebased_base/`。`fix/rebase_merge.py` 現在對這 3 個檔用「當時的 live 副本」當三方合併的 base（live 沒變 → 直接用 staging；live 變了 → `git merge-file`，base = 該副本）。用 pristine 當 base 會在 stock_data_sources.py 產生 4 個、reference 產生 1 個假衝突。
- `fix/make_patches.py` 對這 3 個檔另外輸出 `<檔>.vs_rebase_base.diff`（只含本批改動），對 live 做 `git apply --check` 用的是這個 diff。
- `fix/precheck_live.py` 新增本批 23 個檔的清單（FILES.json 少任何一個就 exit 2）與 rebase base 的檢查。**你若在部署前又改了 stock_data_sources.py 或 reference，precheck 會報 drift，要重跑合併**。

## 4. 測試變更

**改寫（原本斷言上限存在）**：

| 檔案 | 舊 → 新 |
|---|---|
| test_single_stock_data_sources.py | `test_batch_limit_preserves_pinned_route_and_producer_status` → `test_batch_prewarm_reads_official_route_without_byte_cap_and_keeps_pinned_route`；`test_artifact_limit_does_not_poison_sealed_or_stock_cache_access` → `test_artifacts_contract_error_is_size_independent_and_reads_or_poisons_nothing`；新增 `test_declared_record_count_and_digest_are_enforced_on_the_streamed_route` |
| test_published_row_cache.py | `test_batch_guard_blocks_large_official_source_but_single_stock_streams_it` → `test_batch_and_single_stock_both_stream_official_source_into_identical_caches`；新增預算 spill 不改快取位元組、每次 flush 每檔只開一次、單文件邊界、（rl_final）讀者開著同一快取時 replace 保留相同位元組、batch 時讀者開著快取 route 不失敗 |
| test_single_stock_native_calibration.py | `test_official_market_payload_over_512_mib_is_not_opened` → `test_official_market_payload_has_no_byte_cap_and_a_missing_payload_fails_closed`；新增宣告大小必須等於串流位元組 |
| test_published_single_stock_reference.py | 3 個斷言「不讀」的測試改為「一次嚴格串流準備」；新增：第二次讀取用鎖內快取不讀 payload、未驗證 pin 不信任快取、不支援的 projection 改為準備、準備中完整性失敗不退回 iterator、無快取目錄用 iterator、settlement evidence 不逐列複製 attrs；rl_final 新增：快取寫入失敗退回 iterator 且不污染報告的 sources、pin 後物件不見記為 route 缺口、iterator 退回時完整性失敗仍失敗關閉、準備不把報告的 sources 切到 batch 模式並交回證據。rl_build 的 `test_non_integrity_preparation_gap_fails_closed_with_the_gap_text` 已依 RL-ROBUST-1 換成前者 |

**新增檔案**：

- `test_official_market_daily_followups.py`（rl_final）：`process_loaded` 不逐列複製 attrs（deepcopy 次數 ≤ 2，結算值不變）；recipe research 沒有 512 MiB 檢查。
- `test_official_market_daily_real_streaming.py`：真實資料測試，只在 `TW_SINGLE_STOCK_REAL_OFFICIAL_MARKET_DAILY` 指向 c19c4ee7… 物件時執行（先驗 sha256），每個情境在獨立子行程、有 Windows job object 記憶體上限。`TW_SINGLE_STOCK_REAL_OFFICIAL_MARKET_DAILY_SLOW=1` 加跑全部 2,934 檔的情境。
- `test_three_month_xs_ledger_daily.py`：每日排程工具與排程腳本 44 個測試。

顯示端的測試（`test_three_month_contract_integration`、`test_tomorrow_contract_integration` 的舊 `consumer_read_limit` 字串）不變：舊 JSON 仍要能正確顯示。

## 5. 前瞻帳本每日排程

- 工具 `tools/three_month_xs_ledger_daily.py`，啟動檔 `RUN_THREE_MONTH_XS_LEDGER_DAILY.cmd`；安裝／移除／查看各有 `.cmd` + `.ps1`，共用 `THREE_MONTH_XS_LEDGER_TASK_COMMON.ps1`；定義檔 `THREE_MONTH_XS_LEDGER_DAILY_TASK_DEFINITION.json`；README 新增一節。
- 一次執行：釘選與報告相同的發布快照並驗證 pin → D = 快照股價的最後日期 → 先載入正式共用三個月模型（模型不對就在建置前結束）→ 讀取或建置 D 的全市場截面 → 用帳本模組記錄一次。只記錄快照自己的最新 D，不回補舊日期（較新的快照會帶入事後的還原價）。
- 交易日：用快照內的官方行事曆，沒有資料的日子用週一到週五規則。交易日若快照落後於「今天以前最近的交易日」，仍記錄快照的 D（若未記錄），exit 3；非交易日若 D 已記錄就立即結束 exit 0，否則補記。
- 結束碼：0 已記錄／已記錄過／略過／被 `TW_SINGLE_STOCK_XS_LEDGER=off` 關閉；1 程式錯誤；2 參數或路徑錯誤；3 資料尚未就緒；4 失敗關閉；5 快取或帳本寫入失敗。`--check`、`--status` 不寫入任何東西。
- **排程（rl_final 改為每天，LD-SCHED-1）**：工作名稱 `D-Stock Three-Month XS Ledger Daily 22-00`，路徑 `\`；兩個 weekly 觸發、**週一到週日**（DaysOfWeek 遮罩 127），22:00 與同晚 23:30 重試（Asia/Taipei）；動作 `C:\Windows\System32\cmd.exe /d /c "set NO_PAUSE=1&&call D:\stock\programs\single_stock_analysis\RUN_THREE_MONTH_XS_LEDGER_DAILY.cmd"`，工作目錄 suite；目前使用者、Interactive、RunLevel Limited（不需系統管理員、不存密碼）；IgnoreNew、StartWhenAvailable、執行上限 1 小時、不喚醒。
  - 為什麼含週末：10-02（週五）的 price／board／PER 到 10-04 週日 08:56 才齊，而 price route 曾在當天就發布（09-18 週五 18:54）。只排平日時，若週一 22:00 前 updater 又發布新 head，10-02 就永遠記不到。週末執行若 D 已記錄，約 0.04 秒就結束。
  - 22:00 的依據：09-13～10-04 的 364 個 head 中，平日資料最晚在隔天 20:51 齊備。
- 安裝腳本不帶參數只是預演（印出確切的 `Register-ScheduledTask` 命令）；註冊需要 `-Register -ConfirmRegister`，只能從 `D:\stock\programs\single_stock_analysis` 執行，絕不覆寫既有工作、不啟動工作；`-Verify` 唯讀比對。所有 CMD 在呼叫 Windows PowerShell 5.1 之前先把 PSModulePath 設回 5.1 的模組路徑（PowerShell 7 的路徑外洩會讓 `Get-FileHash` 無法辨識，已重現）。
- **部署前不能註冊**：在新模型 `--apply-run` 之前，工具一定是 exit 4（`shared_code_changed_pending_shared_retraining`，已對正式 head 唯讀驗證）。
- 帳本涵蓋率受限於 updater 手動執行的頻率（觀察期 13 個交易日中有 5 個成為某個 head 的最新日期），仍高於事先登記的每區塊 10 個決策日。
- 選做（你的範圍）：把 `RUN_THREE_MONTH_XS_LEDGER_DAILY.cmd` 加進 `D:\TWStockData\services\acceptance\verify_all_consumers_central_only.py` 的維護入口清單（那份清單不會自動探索）。

## 6. 對你現有流程的影響

- **重訓**：`published_single_stock_reference.py` 與 `tw_single_stock_3m_analyzer.py` 都在 `source_seal()` 內，所以封印改變，凍結／共用模型都要在合併後重訓（本來就在部署計畫內，順序是：合併 → 部署副本訓練 → 換檔 → `--apply-run`）。訓練來源檔的雜湊不變（本批沒改任何訓練來源檔）。
- **訓練輸入變成決定性的**：batch 訓練一定會準備 50 檔的 official_market_daily（每個 pin 約 +23 秒、約 70 MB），不再取決於 Top10 是否先建 projection。order21 的 `not_read_by_training_policy` 是另一個政策，本批沒動。
- **報告時間**：沒有快取也沒有 projection 時，每檔股票每個快照第一次 +22–25 秒；之後暖讀約 0.02–0.05 秒。performance 迴圈修正後，2330 的報告比修改前多約 3 秒（rl_build 實測 19.4 → 22.3 秒；最終數字見 §8）。
- **磁碟**：每檔股票每個快照約 0.5 MB 的 consumer 快取。
- **你 10:04 之後若又改了 stock_data_sources.py 或 reference**：重跑 `fix/rebase_merge.py`，它會以 `fix/rebased_base/` 為 base 合併；有衝突就照 diff 解。

## 7. 仍待決定（不影響部署）

- **D3**：其他超過 0.5 GB、`artifacts()` 會整個載入的 route（loan_collateral 3.06 GB、government_bank 2.52 GB、tdcc 2.29 GB、margin、shareholding、institutional、price_adj、balance_sheet）目前沒有呼叫者；要不要也列入 `_STREAMED_ONLY_ROUTE_IDS`。
- **D4**：order21 的訓練不讀政策維持不變（解除要每個訓練 pin 約 10 分鐘）。
- **D5**：xs IO 的 pyarrow 快速路徑比 `_JSONStream` 寬鬆：所選浮點欄的 NaN、未選欄位的 NaN／Infinity、重複鍵、無效 UTF-8 會被接受（sha256、大小、筆數仍強制）。這是前一批的設計，本批沒改。
- **recipe research 的可比性**：09-27 的 native 基準是在上限下訓練的（official_market_daily 沒讀），之後用新程式跑的候選會讀到它；兩者比較時要注意輸入不同。
- 你 10-04 11:37 的 `shared_20261004_raw_reference_recovered` 已在 14:34 寫出 `run_summary.json`（狀態 `awaiting_user_decision`），但檔案有 **26,401,548,369 bytes（26.4 GB）**。部署會重訓，所以不需要 apply 它；它佔用的空間與那麼大的原因請你確認（之前同一系列的 run 在驗證 candidate 時曾 MemoryError）。

## 8. 本批最終驗證（rl_final）

| 檢查 | 結果 |
|---|---|
| 三方合併（`fix/merge/rl_final`，D:/stock 快照 17:44:03） | 0 衝突；3 個從 live 起始的檔 = `ours_rebased_on_live`（你 09:27／09:29／10:04 之後沒有再改）；analyzer 以 pristine 為 base 乾淨合併 |
| 補丁（`patches/`，60 個檔） | ALL.diff 套到 pristine = staging；每檔 diff 都能套到現在的 live；DEPLOY_ALL.diff 套到 live 副本 = 合併樹 |
| `precheck_live.py rl_final` | drift 空（18 個封存／訓練來源、60 個部署檔、本批 23 個、3 個 rebase base） |
| 完整測試 theirs → merged（ssa） | 1,428 → 1,657 個 id；新增 236（231 過、5 個真實資料測試預設略過）；移除 7（改寫的上限測試）；**0 個新失敗**；共同失敗 122 個與修改前相同 |
| 前批 merged → 本批 merged（ssa） | 新增 75（70 過、5 略過）；移除 7；0 個新失敗 |
| shared、telegram | 與修改前相同（telegram 新增的 22 個是前批的 caption 測試） |
| 真實資料測試（合併樹、凍結 1.30 GB 物件、慢速情境也跑） | 5 個全過：單檔 35.6 秒／58.8 MB；loader 冷讀 35.3 秒／195.9 MB（含 pandas），83 列、09-29 收盤 2,475.0；batch 50 檔 38.0 秒／71.5 MB；全部 2,934 檔 223.7 秒／110.5 MB；`artifacts()` 拒絕／33.2 MB（本機當時另有程式在跑，時間比 rl_build 慢約 1.5 倍） |
| 正式 head 唯讀（物件 `6265b799…`，D = 10-02，沒有 sealed projection） | 2330 準備 39 秒、1,214 列、10-02 收盤 2,500.0；第二次 0.55 秒、新 sources 0.61 秒（快取）；報告的 sources 沒有 route 錯誤、沒有切到 batch 模式；讀取本身只增加約 14 MB commit；D: 寫入 0 |
| 報告路徑（2330，同一時段比較） | 三個月：修改前 28.4 秒「本次讀取受限」；修改後第一次 65.2 秒、之後 29.5–29.7 秒，「2026-09-29 原始收盤 2,475.00；模型收盤 2,475.00」，P 相同（0.5789137320742336），峰值 commit 都約 1.25 GB。tomorrow：1,217 列、comparable 用官方原始價、結算 1,217 列、Markdown 沒有「讀取受限」 |
| 每日帳本工具對正式 head 唯讀 `--check` | 週日、週一都是 exit 4（模型待重訓，預期）；`--status` exit 0；正式 cache 不變 |
| A1 oracle 嚴格比對 | PASS，1,038,944 列 bit-identical，parquet 與前批 byte 相同 |
| D: 寫入防護 | 所有測試、合併、量測：0 次嘗試 |

所有輸出在 `three_month_impl/rl_final/`（`out/`、`logs/`、`junit/`、`demo/`、`a1/`）。總結與部署步驟：`FINAL_REPORT.md`、`DEPLOY_PLAN.md`（第 4 版）。
