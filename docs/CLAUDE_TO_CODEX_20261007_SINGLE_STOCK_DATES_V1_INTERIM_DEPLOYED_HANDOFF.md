# Claude → codex 交接：個股分析 dates_v1 暫時版本已部署、重訓、套用（20261007）

這是一份獨立交接檔，不接續任何舊交接檔。本檔記錄 20261007 21:20 重新驗證時的狀態。個股分析程式目前由 Claude 負責。

## 1. 結論

- 個股分析的 **dates_v1 暫時版本**已完成三件事：部署程式碼、用部署位元組重新訓練兩個模式、套用到正式環境。三個月暫定報告與 Telegram 都在新版運作。
- 這是**暫時版本**：資料端 head 的股價與本益比是「部分完成」，所以分析日固定在最後一個完整交易日 20261002，並排除 475 檔資料有缺陷的股票。
- 正式版需要等 updater 發布完整的 head，再做 A/B 比較與正式部署（§6）。

## 2. 目前在正式環境的內容

| 項目 | 值 |
|---|---|
| 程式 label | `dates_v1`（合併基準為已部署的 `interim_v1`） |
| 封印 `source_seal_id` | `bd54776e006526182f312747c9246e013225b31c3ae6b7245fe2b2cb7091a6a1` |
| 訓練來源 | 8 個，比 interim_v1 多了 `../../shared/date_format.py` |
| 套用的訓練 run | `D:\TWStockData\program_data\dstock_single\models\shared_stock\training\interim_20261006T201150Z\A_interim` |
| 核准的 summary sha256 | `0384f45a59e6d71f370b8398b14cb229d6f5e4a82dafe43382e616694d650fe6` |
| `models\shared_stock\current.json` | 20261007 05:14 寫入，sha256 `5d71f4a7678c6ba6…`，7.3 MB |
| 三個月模型 | `shared-xs-rank-v1-three_month-h63-598f927098150174`：全市場跨股排名，1,644 檔，W1–W5 通過 |
| 次日開盤模型 | `shared-native-v1-tomorrow_open-h1-0a1828146901f2c6`：45 檔，另有 5 檔因資料缺陷被拒（6212、6971、5601、7757、5878） |
| 訓練設定 | 設定 A：`order21_bounded_only`，樣本 seed 20261004。**沒有做 A/B 比較** |
| 暫時版本規則 | `dstock-interim-partial-v1`（由資料推導，不綁 head sha） |
| 分析日 D | 20261002（機器欄位 `2026-10-02`） |
| 排除 | 475 檔：ids sha256 `11e68841…`，exclusion identity `0fa1e7e2…`，interim identity `cdc85da2…` |
| 開關檔 | `D:\stock\programs\single_stock_analysis\config\interim_data_policy.json`：sha256 `078e4dd6…`，state active，到期 `2026-10-13T23:59:59+08:00` |
| 訓練時的 head | `82b06b8be32d1b8890bb39482875cd84dee00dc06fc0b9abf03e0d5df499ab08`（20261006 07:38，至 21:24 未變） |

## 3. 使用者目前看到的行為

- **三個月**：只出暫定報告（`--provisional --output-dir`，或用 Telegram）。
  - 報告開頭有「【暫時版本】分析日 20261002…」的揭露，並列出排除清單。
  - 例：2330 相對偏強，跑贏同期多數股票的機率 58.13%，三個月報酬 80% 區間 −15.79%～+25.60%，目標日（估計）20270105。
- **正式三個月**：擋下（`interim_formal_report_blocked`），提示改用暫定報告。
- **次日開盤**：暫停（`interim_data_incomplete_paused`），顯示「資料不完整，暫停：…需要最新一個交易日（20261005）的完整收盤資料…」。
- **被排除的股票**：例如 7631，回報 `xs_interim_stock_excluded` 並說明原因。
- **不寫入的紀錄**：暫時版本不寫歷史預測、v4 績效、結算與前瞻帳本。部署前後比對過，這些檔案都沒有變動。
- **日期**：使用者看得到的日期（PDF、Markdown、主控台、錯誤訊息、Telegram caption）一律是 YYYYMMDD。JSON 等機器欄位仍是 YYYY-MM-DD。讀取端兩種寫法都接受，以日期值比對。

## 4. 已完成的驗證

| 步驟 | 結果 |
|---|---|
| 部署前閘門 | head 為暫時版本形狀、D=20261002、排除清單相符。updater 的 resume（PID 60748/61128，20261006 04:50 起）仍在執行；依使用者決定放行，並記錄為 `updater_running_allowed_by_user_20261006` |
| 備份 | `D:\stock\programs\single_stock_analysis\backups\20261006T201132Z_dates_v1`（83 個檔，另 8 個新檔）；模型 `models\shared_stock\history\current_2026-10-06T232241.448557p0800_e4c14811.json`；`snapshots` 也已複製。`verify_backup` 通過 |
| 部署副本 | `D:\TWStockData\program_data\dstock_single\deploy_staging\20261006T201142Z\programs`，封印 `bd54776e…` |
| 訓練 | 20261007 04:12–05:13，結束碼 0。`interim_run_check` 通過，QA 套用到 run 內的 QA cache 後可正常載入 |
| 程式部署 | 寫入 87 個檔（79 個已與 interim_v1 相同而略過，8 個新檔），逐檔比對 sha256。當時 `precheck_live.py dates_v1 --deployed` 結果 drift 為空 |
| 套用 | 部署後的程式重驗封印與 8 個訓練來源雜湊後套用。`qa_load_check` 兩個模式都正常，暫時版本標記正確 |
| smoke | 2330 三個月暫定報告：結束碼 0，4.9 分鐘，數值與 interim_v1 相同，主控台沒有任何 YYYY-MM-DD；次日開盤、正式三個月的訊息都正確 |
| Telegram | 使用者以 `RESTART_COMMAND_BOT_SERVICE.cmd -Deploy -ConfirmDeploy` 重啟。狀態 `ListenerReadiness=ready`、`SourceStatus=current`、source SHA 相符 |
| 21:20 重驗 | 兩個模式在正式環境載入正常，封印 `bd54776e…`；Telegram 狀態仍為 ready／current |

## 5. 部署後被其他對話修改的檔案（需要知悉）

20261007 05:14 套用之後，`D:\stock\programs` 有檔案被 Claude 以外的對話修改：
- **程式檔 1 個**：`single_stock_analysis\src\tw_single_stock_mitake_background.py`（10:03 修改，不在封印或訓練來源內）。
- **測試檔約 50 個**：single_stock_analysis、shared、telegram 的 tests；另有 2 個測試檔被刪除：
  - `test_published_descriptor_storage_and_pin_binding.py`
  - `test_tomorrow_runtime_target_adaptive_fit_20260927.py`

影響與處理：
- 沒有改到封印檔或訓練來源檔，所以模型仍然有效，報告照常運作。
- `precheck_live.py dates_v1 --deployed` 目前會因這些檔案而 exit 1。下次部署前，Claude 會把這些修改三方合併進來，再重跑測試。
- **請 codex 不要再直接修改個股分析的程式。** 需要改的地方寫成交接檔給 Claude。也請不要終止不是自己啟動的 Python 程序：20261006 21:55 與 22:04，暫時版本的訓練兩次被外部強制終止（exit −1）。

## 6. 對 updater 的要求與交付條件

- **日期拼寫**：個股分析的讀取端已部署並套用，`YYYYMMDD` 與 `YYYY-MM-DD` 都接受，updater 現在可以依使用者指示切換發布資料的日期拼寫。限制：切換後，個股分析就不能再用程式回滾到 interim_v1，因為 interim_v1 讀不懂 YYYYMMDD。詳見 `CLAUDE_TO_CODEX_20261006_SINGLE_STOCK_YYYYMMDD_HANDOFF.md`。
- **完整 head 的條件**：同 `CLAUDE_TO_CODEX_20261006_HEAD_CCCBCA1F_PARTIAL_HANDOFF.md` §4。
  - finmind_price_adj 為 SUCCESS，沒有 vintage 相關警告；
  - finmind_per 為 SUCCESS；
  - official_market_daily 的 `actual_max_date` ≥ D，只有 D 之前、有宣告日期的缺口（最多 3 天）；
  - D 的上市櫃別：TWSE ≥ 773、TPEx ≥ 643 列；
  - 價格在 D 的橫斷面完整。
- **已知不可用的價格物件**：`fbb9fb50…`（只改 metadata 重新發布仍會被擋）、`a5fe7c55…`（efbd4844 的價格物件，九月調整有錯）。
- **一次 run 只有一個收集時間戳**：暫時版本規則假設每次 run 只有一個 `_shared_collected_at`；超過 3 個值時，暫時版本會全部擋下。
- **發布暫停窗口**：完整 head 發布後，個股分析要做 A/B 訓練與套用（約 4 小時）。這段期間請不要再發布新 head，Claude 開工前會通知。

## 7. 完整 head 發布後的處理（Claude）

依 `three_month_impl\DEPLOY_PLAN.md` 第 8 版 §13 的路徑 A，以及 `INTERIM_DEPLOY_PLAN.md` §6、§7：
1. **暫時版本自動停用**：股價與本益比都是 SUCCESS 時規則不再適用。在 A/B 套用之前：
   - 三個月暫定報告照出，揭露「模型為暫時版本」；
   - 次日開盤恢復暫定報告；
   - 正式報告仍擋（`interim_model_formal_report_blocked`）。
2. **合併**：先把 §5 的修改合併進來，再加入一項變更：把 `shared/date_format.py` 列入 `frozen.source_seal()`。
3. **A/B 比較**：設定 A 與設定 B，事先登記的判定規則 v2（sha `1c8e3325…`）。結束碼 6 由使用者決定。
4. **QA、套用、smoke、Telegram 重啟**：Telegram 重啟需系統管理員權限，由使用者執行。
5. **收尾**：登記前瞻帳本每日排程，並把暫時版本開關設為 `withdrawn`。

## 8. 延長、撤回與回滾

- **延長暫時版本**：在 20261013 到期前，如果 updater 還沒修好，執行 `set_interim_policy.py` 加 `--expires-at`，不需重訓（`INTERIM_DEPLOY_PLAN.md` §5）。
- **撤回**：`--state withdrawn`，暫時版本形狀的 head 上所有模式都會暫停。
- **回滾（DEPLOY_PLAN v8 §13.9）**：
  - R1：模型有問題時，重新訓練後再套用。
  - R2：程式有問題時，從 `backups\20261006T201132Z_dates_v1\programs\` 還原 83 個檔，並用 `history\current_2026-10-06T232241…_e4c14811.json` 覆蓋 `current.json`，然後重啟 Telegram。**updater 已經把發布資料改成 YYYYMMDD 之後，不要做 R2。**
- **不要還原的檔**：`tw_prediction_history.py`、`tw_single_stock_performance.py`、`tw_single_stock_report_contract.py`、`tw_single_stock_report_focus.py`。
- **不要刪除**：`models\shared_stock\lineage_objects\`、`snapshots\lineage_objects\`（含 `_damaged\`）、`versions\`。

## 9. 相關檔案

| 檔案 | 位置 |
|---|---|
| 部署計畫（第 8 版，§13 為 dates_v1） | `C:\Users\USER\AppData\Local\Temp\claude\D--TWStockData\51ab15cb-7b36-4847-b785-5ba122936b29\scratchpad\three_month_impl\DEPLOY_PLAN.md` |
| 暫時版本計畫 | 同資料夾 `INTERIM_DEPLOY_PLAN.md` |
| 部署工具 | 同資料夾 `interim\final\deploy\`（`deploy_gate.py`、`make_deploy_copy.py`、`start_interim_training.ps1`、`interim_run_check.py`、`qa_load_check.py`、`deploy_code.py`、`set_interim_policy.py`）、`fix\precheck_live.py` |
| 訓練與 smoke 紀錄 | 同資料夾 `interim\final\deploy\train_logs\`、`smoke_2330_3m_dates.log` |
| 日期格式交接 | `D:\stock\docs\CLAUDE_TO_CODEX_20261006_SINGLE_STOCK_YYYYMMDD_HANDOFF.md` |
| head 問題交接 | `D:\stock\docs\CLAUDE_TO_CODEX_20261006_HEAD_CCCBCA1F_PARTIAL_HANDOFF.md` |
| updater 卡住、截斷、重複交接與規格補充 | `D:\stock\docs\CLAUDE_TO_CODEX_20261005_UPDATER_HANG_TRUNCATION_DEDUP_HANDOFF.md`、`CLAUDE_TO_CODEX_20261005_UPDATER_SPEC_ADDENDUM.md` |
