# Claude → codex：開盤前版 V5 拼接修正複核後發現的錯誤與待修事項（20261102）

- 撰寫：Claude，20261102 23:00。
- 範圍：本輪用 codex 目前的版本確認 V5 時，發現需要修改的地方。每一項都在 20261102 22:44～23:00 重新查證過現況；檔中引用的路徑、行號、SHA 已用 `W\codex_compat\handoff_check\check_handoff_claims.py` 逐一核對（0 項不符）。
- 全程唯讀：
  - D: 沒有寫入；
  - 沒有部署、沒有提交；
  - 沒有執行 CMD、訓練、predict、settle，也沒有碰任何行程。
  - 測試都在 C: 副本跑，或用 C: 的 basetemp 跑；跑完都檢查過 D: 沒有新寫入。
- 請 codex：
  - 逐項回覆「同意／不同意，並附理由」；
  - 明確決定第 3 節的問題。
  - 需要改 `D:\stock` 或部署的部分，仍要使用者授權。
- 工作資料夾（下稱 `W`）：`C:\Users\USER\AppData\Local\Temp\claude\D--TWStockData\51ab15cb-7b36-4847-b785-5ba122936b29\scratchpad\preopen_stitch_seam\`
- 背景：
  - V5 的內容與部署經過：`W\deploy_v5\DEPLOY_RECORD_20261102.md`
  - 與 codex 版本的相容性複核：`W\codex_compat\V5_CODEX_COMPAT_20261102.md`

## 0. 版本對照

| 名稱 | 檔案 | SHA256 前 8 碼 | `build_prices` AST（Python 3.12.10，`ast.dump(include_attributes=False)`） |
|---|---|---|---|
| HEAD（codex 10-21 版） | `D:\stock` git HEAD 的 `programs\shared\stock_daily_preopen_data.py` | 3fff2f08（20,313 bytes） | f969d236 |
| V5（現行，20261102 00:04 部署） | `D:\stock\programs\shared\stock_daily_preopen_data.py` | c7bb65ff（27,455 bytes） | a58bb3c3 |
| V5.1（只在副本，未部署） | `W\codex_compat\v51\stock_daily_preopen_data.py` | 9971431c（28,229 bytes） | a58bb3c3 |

V5 的新測試是 `D:\stock\programs\shared\tests\test_stock_daily_preopen_stitch_repair.py`（ef7bbfb2）；V5.1 的版本是 `W\codex_compat\v51\test_stock_daily_preopen_stitch_repair.py`（a01034ea）。

## 1. 我這邊的錯誤與待修

### 1.1 V5 部署讓執行中的 updater 中止（codex 已在 00:31 解除）

經過：
1. 20261102 00:04:54（16:04:54Z）部署 V5。
2. 當時的 `current_model_price_view.py` 是 d76ddcf1，副本在 `D:\TWStockUpdater\artifacts\source-closure-20261101\consumer-observation-unbind\before\current_model_price_view.py`：
   - 第 23 行寫死 `_BUILD_PRICES_AST = 'f969d236…'`；
   - 第 248 行是 `_require(digest == _BUILD_PRICES_AST, 'model_price_view_consumer_algorithm_changed')`。
3. 2026-11-01T16:06:45Z，run 中止在 `resolve_price_view → _consumer_contract`，記錄在 `D:\TWStockData\updater_runtime\logs\update_errors.jsonl` 第 74 行（`CurrentAuthorityError`，`model_price_view_consumer_algorithm_changed`）。
4. codex 的 `consumer-observation-unbind`（00:20 暫存、00:31 部署）改成只觀察。現行版本：`current_model_price_view.py` a227643d、`reference_gc.py` a9c5edd4。
5. 同一個 run（20261101T133654Z-b83b23ba）續跑：
   - 10:06 的檢查點 `control\qualified_price_history.json` 記錄 `consumer_build_prices_ast_sha256` a58bb3c3、`consumer_post_transform.status=NOT_EVALUATED`，也記錄了 c7bb65ff；
   - 14:53 發布 head 47be2eb0。

我的錯：
- 部署前沒有查到 updater 會讀消費端原始碼，而且寫死了 AST。
- 部署紀錄寫「沒碰行程」，這不正確。

之後的做法：改 `stock_daily_preopen_data.py` 或 `stock_cache_lifecycle.py` 之前，我會：
- 先查 updater 現版有沒有寫死的檢查；
- 避開 updater 執行時段；
- 部署後查看 `update_errors.jsonl`。

請 codex 確認：這次中止除了第 74 行那筆紀錄，沒有在 run 20261101T133654Z-b83b23ba 留下需要處理的狀態。

### 1.2 V5 的兩個缺口 → V5.1（只在副本，未部署）

codex 在 `D:\TWStockUpdater\artifacts\source-closure-20261101\consumer-observation-unbind\HANDOFF.md` 第 5、13 行指出兩點：
1. `np.exp(M)` 之後沒有再驗證有限正值（V5 第 296～298 行）。
2. `provenance["objects"]` 沒有列 `finmind_dividend_result`，只在 `stitch_repair.dividend_object_sha256` 有記錄。

V5.1 的改法：只動 `repair_stitch_returns` 和 `load_inputs`，`build_prices` 沒動，所以 AST 仍是 a58bb3c3。
1. `repair_stitch_returns`：
   - 縮放係數必須全部是有限正數；
   - 每個價格欄位在縮放前後，「有限且為正」的位置必須完全相同。
   - 不符合就 raise `PreopenDataError`。`build_prices` 原有的 try 會接住：`status=skipped`，面板維持原本的拼接，並發出 UserWarning，不擋流程。
   - 成功時，報告多一個 `finite_positive_checked: true`。
2. `load_inputs`：只有在 head 有 `finmind_dividend_result` 時才把它加入 `used`。
   - 不能直接加入：`provenance["objects"]` 用 `cur_routes[r]` 取值，缺路由時會 KeyError。
   - 那樣原本「缺路由只略過修正」會變成 `load_inputs` 失敗。
3. 測試從 5 項增為 7 項，新增兩項：
   - 係數不是有限正數 → `skipped`，面板等於沒有除權息資料的版本；
   - head 沒有除權息路由 → 照常載入，只發警告。

驗證：
- 副本測試：副本由 git HEAD、codex 11-01 部署批次、codex 暫存區測試加上 V5.1 組成。
  - codex 各套件的結果和 V5 完全相同；
  - V5.1 測試 7/7 通過（`W\codex_compat\out\test_compare_v51.json`）。
- 真實資料（`W\deploy_v5\stage_check_v51.json`）：
  - 訓練時物件：和 V5 逐位元相同，修正 4 筆；
  - 現行物件：197 筆／95 檔，`detail_sha256` a5a56248，殘差 0，和 07:15 線上 V5 的結果相同；
  - 峰值記憶體 4.6 GB。
- 第 2 點只影響紀錄：
  - updater src 裡唯一讀 `data_provenance` 的地方是 `current_model_price_view.py:446`，只取 `historic_head_sha256` 和 `historic_objects`；
  - `D:\stock\programs` 也沒有程式讀開盤前版的 `data_provenance.objects`。

修補檔：
- 部署用（套在現行 V5 上）：`C:\Users\USER\AppData\Local\Temp\claude\D--TWStockData\51ab15cb-7b36-4847-b785-5ba122936b29\scratchpad\preopen_stitch_seam\codex_compat\V5_to_V51.patch`
- 提交用（套在 git HEAD 上）：`C:\Users\USER\AppData\Local\Temp\claude\D--TWStockData\51ab15cb-7b36-4847-b785-5ba122936b29\scratchpad\preopen_stitch_seam\codex_compat\V51_onto_codex.patch`
- 兩份都實際套用驗證過，換行正規化後和 `v51\` 的兩個檔逐位元相同。

請 codex 確認：
- 兩點改法是否符合你的意思，特別是「不符合就 skipped、不擋」。
- V5.1 部署後，updater 只會看到檔案 SHA 改變（AST 不變）；除了 2.4 的 QA pin，沒有其他地方寫死這個檔的 SHA。

### 1.3 V5 的新測試沒有登錄到 `programs\STABLE_PROGRAM_PATHS.txt`

- `programs\shared\tests\test_stock_daily_preopen_stitch_repair.py` 不在清單上：第 93 行是 `test_stock_daily_preopen.py`，後面沒有這一行。
- 因此 `VERIFY_STABLE_PROGRAM_PATHS.ps1` 會報錯；H63 交接檔 `D:\stock\docs\CLAUDE_TO_CODEX_20261102_H63_U7_SWITCH_HANDOFF.md` 也記了這件事。
- 計畫：提交 V5／V5.1 時，在第 93 行後面加這一行，而且只 stage 這一行，因為這個檔另有 H63 未提交的增列。提交方式要等使用者決定（PD-0001）。
- codex 不用動，知悉即可。

## 2. codex 這邊需要修改或決定的地方（都和 V5 無關）

### 2.1 updater 三支測試檔有 10 項失敗

重跑條件：
- 測試檔：`D:\TWStockUpdater\tests\` 下的 `test_current_model_history_gaps.py`、`test_model_price_view_single_pass.py`、`test_model_price_view_gc.py`，共 63 個節點。
- 方式：`sys.dont_write_bytecode`、`-p no:cacheprovider`、`--basetemp` 設在 C:、`--rootdir D:/TWStockUpdater -o addopts=`。
- 結果：10 failed／53 passed，輸出在 `W\codex_compat\handoff_check\updater_rerun_out.txt`。
- 改用 HEAD 的資料模組，結果逐項相同（`W\codex_compat\review\updater_baseline_out.txt`），所以和 V5 無關。
- 這三支測試最後一次修改是 10-22。codex 的 `D:\TWStockUpdater\artifacts\source-closure-20261101\integration\consumer-observation-check\run-001.execution-receipt.json` 記錄 `existing_nodes_rerun: 0`，所以這些舊節點一直沒有被重跑。

**A. 測試過時、程式是對的（建議改測試）**

| 失敗節點 | 現象 | 程式現況 |
|---|---|---|
| `test_current_model_history_gaps.py::test_effective_view_uses_both_prices_current_invalid_wins_and_keeps_gc_roots`（517～518 行） | `DID NOT RAISE CurrentAuthorityError` | 00:31 起 `_consumer_contract` 只回傳摘要（`current_model_price_view.py:240-242`）。建議把 517～518 行改成 `assert views._consumer_contract(item.consumer_raw.replace(b'.head(5)', b'.head(6)', 1)) != report['consumer_build_prices_ast_sha256']`，再加上 `assert report['consumer_post_transform']['status'] == 'NOT_EVALUATED'` |
| `test_current_model_history_gaps.py::test_current_756_fit_1008_history_keeps_exact253_and_legacy[756]`（579 行） | 斷言路由窗口裡沒有 `'finmind_dividend'` | `application_scope.py:455-457` 現在讓 `finmind_dividend`、`finmind_dividend_result` 沿用價格窗口 |
| `test_model_price_view_gc.py::test_unchanged_preflight_does_not_reuse_v4_edges`（154 行） | 查 `#edges-v5` 投影，結果是 None | `reference_gc.py:988、1070、1692` 現在用的是 `edges-v7` |

**B. 行為改了，我無法判斷是刻意還是回歸（請 codex 決定改測試或改程式）**

B1. 官方行情「算不算存在」的規則改了（5 項）

- 現行程式：`current_scope_runtime.py:786` 起的 `_price_body_presence_key`（此檔 20261102 08:25 修改）。`official_market_daily` 的列如果 close 不是有限正數就回 None，也就是不算存在。唯一例外是 `observation_status == 'no_quote'` 且 OHLC 全空的列。
- 測試期望 close=0 的官方列仍算存在；docstring 寫的是「Original catalogue presence semantics」。受影響的測試：
  - `test_model_price_view_single_pass.py::test_presence_preserves_supported_old_board_missing_board_and_observation_rules[False]`、`[True]`（153 行）
  - `test_model_price_view_single_pass.py::test_current_route_presence_is_not_combined_positive_or_native_coverage`（132 行）：`invalid_current`，也就是 ('2317','TWSE','2024-08-08')，不在官方的 presence 裡
  - `test_current_model_history_gaps.py::test_effective_view_normal_planners_reuse_only_extended_history_keep_recent_gap`（539 行）：官方規劃多出 2024-08-08
  - `test_current_model_history_gaps.py::test_1008_normal_planners_share_view_and_keep_recent_demand[756]`（631 行）：官方規劃多出 2023-11-30
  - 後兩項多出的日期，正好是 fixture 的 `invalid_current`（`('2317', case.axis[2])`，第 424 行）那天，推測是同一個原因。
- 請 codex 判斷：如果是刻意的，close≤0 但不是 no_quote 的官方列每次都會被排入抓取。來源如果一直回 0（例如停牌），會不會每次 run 都重抓同一批？

B2. repair unit 的形狀改了（2 項）

- `test_current_model_history_gaps.py::test_old_missing_body_reaches_normal_stock_range_and_resume`（185 行）：`runtime.reconstruct_resume_repair_units` 現在回傳用 `{'unit': {...}}` 包起來的 unit，測試期望的是沒包裝的 unit。
- `test_current_model_history_gaps.py::test_756_history_normal_transport_receipt_merge_resume_replan_reuses_body`（374 行）：pending 裡放沒包裝的 unit 時，`price_vintage_repair.apply_repair` 沒有發出任何請求，回傳 `status=FAILED`、`reason=price_interval_acquisition_incomplete`。原因我沒有查明，可能也和形狀改變有關。
- 請 codex 判斷：正式程式的所有呼叫端是否都已改用新形狀？如果還有路徑傳沒包裝的 unit，修補就會不抓資料、只回報 incomplete，那就是程式錯誤。

### 2.2 暫存區 `test_preopen_stage_ops.py` 的期望文字和部署中的 reader 不一致

- 測試：`D:\TWStockUpdater\artifacts\delta-performance-20261101\preopen-producer\stage\programs\shared\tests\test_preopen_stage_ops.py`（12d493f7，11-01 13:34），第 699～700 行，`test_predict_annotates_same_head_lag_until_closure_declared`。
- reader：`D:\stock\programs\shared\stock_daily_preopen_reader.py`（43123d55，11-01 16:24 部署），第 193～197 行的 `signal_gap_unverified` 分支。
- 差異。情境是訊號日 20261016、目標日 20261020、缺 20261019：

  | | 文字（「中央價格可能落後…」之後） |
  |---|---|
  | 測試期望 | …目標日 20261020（可能跨 2 個交易日，結算時不計入實盤統計）；若 20261019 為臨時休市請宣告 |
  | reader 實際 | …目標日 20261020（可能跨 2 個交易日）；是否計分以結算時已驗證日曆為準 |

- reader 整個檔已經沒有「若…為臨時休市請宣告」這類提示。
- 請 codex 決定：照 reader 改測試，或在 reader 補回「若 YYYYMMDD 為臨時休市請宣告」。目前使用者看不到該怎麼處理。
- 這支測試只在暫存區，`D:\stock` 沒有部署。

### 2.3 `D:\stock` 現行的 `test_stock_daily_preopen.py` 沒跟上 11-01 的部署（4 項失敗）

- 現行測試：`D:\stock\programs\shared\tests\test_stock_daily_preopen.py`（ec6a48a8，10-22）。
- 在 C: 副本跑：副本是 git HEAD、codex 11-01 部署批次加上 V5，換上這支現行測試。
  - 結果：4 failed／34 passed／1 skipped，和在 D: 原地跑的結果相同。
  - 輸出：`W\codex_compat\handoff_check\inplace_preopen_test_out.txt`。
- 四項失敗都是測試沒跟上新行為：

  | 測試 | 行 | 現象 |
  |---|---|---|
  | `test_reader_roundtrip_and_freshness` | 102 | reader 回 `preopen_future_signal`，測試期望 `preopen_stale` |
  | `test_latest_prospective_keeps_last_publication` | 359 | `KeyError: 'run_id'`：`_prospective_publication`（`stock_daily_preopen.py:703`）依 `recorded_at`、`run_id` 排序，測試自己組的表沒有 `run_id`。正式帳冊 `predictions` 表的 `run_id` 是 NOT NULL，所以不影響實際資料 |
  | `test_reason_text_covers_every_reason_code` | 446 | reader 的原因代碼多了 `preopen_future_signal`、`preopen_summary_schema_mismatch`、`preopen_summary_invalid`、`preopen_summary_missing`，少了 `preopen_stale` |
  | `test_predict_manifest_marks_chips_freshness_and_require_flag` | 725 | `m["degraded"]` 為 True：日曆守門 stale_signal 現在照常發布並標示 degraded（11-01 裁示） |

- codex 暫存區的新版（`D:\TWStockUpdater\artifacts\delta-performance-20261101\preopen-producer\stage\programs\shared\tests\test_stock_daily_preopen.py`，116aa837）在同樣的程式下是 38 passed／1 skipped。
- 建議部署暫存區的新版（需要使用者授權）。請 codex 確認那一版就是要部署的版本。

### 2.4 10-21 QA 的 `SOURCE-PINS.json` 釘住舊 SHA

- `D:\TWStockUpdater\artifacts\current-model-gap-repair-20261021\consumer\SOURCE-PINS.json` 的 `after_sha256` 是 3fff2f08…、`after_size` 是 20313。
- `consumer\tests\test_preopen_keyed_fallback.py:25` 斷言檔案 SHA 等於這個 pin。現行檔案是 V5，所以重跑時 4 個案例都會在 fixture 階段 ERROR。
- 要重跑的話，pin 改成：
  - V5：`c7bb65ff7fc0889befef3db3a1ef6d899d67efd1eec4ed9474edee9e7b52cc5d`、27455
  - V5.1：`9971431cf5397923b21849483788c4c481d60fe581e150bd8ca0e2071f7fdcb8`、28229
- 我在 C: 副本改 pin 後重跑，V5、V5.1 都是 4 passed（`W\codex_compat\review\keyed_v5\`、`keyed_v51\`）。這組合成資料只有兩條路由、沒有 `finmind_dividend_result`，所以修正會略過並發出 UserWarning，面板和 HEAD 相同。
- 只有要重跑這組 QA 時才需要處理。

### 2.5 `CURRENT-REQUIRED-RANGE-CORRECTION.md` 第 30 行的行號過時

- `D:\TWStockUpdater\artifacts\current-model-gap-repair-20261021\CURRENT-REQUIRED-RANGE-CORRECTION.md:30` 引用 `stock_daily_preopen_data.py:223–269`，並寫最後一步是「兩價格正值」。
- 那是 HEAD 的 `build_prices`。各版本的位置：
  - V5：`build_prices` 在 322～383 行，選取之後多了 `repair_stitch_returns`（248～319 行）；
  - V5.1：`build_prices` 在 331～392 行，`repair_stitch_returns` 在 249～328 行。
- 這是資訊性的落差，補註即可。

### 2.6 updater 的兩個檔不要回退

`D:\TWStockUpdater\src\twstock_updater\current_model_price_view.py`（a227643d）和 `reference_gc.py`（a9c5edd4）不要回退。如果回到 00:31 以前的版本（d76ddcf1 等），V5／V5.1 會再讓 updater 以 `model_price_view_consumer_algorithm_changed` 中止。

## 3. 請 codex 回覆

1. 2.1 B1：官方行情 close≤0 不算存在，是刻意的嗎？如果是，會不會反覆重抓？
2. 2.1 B2：repair unit 改成用 `{'unit': …}` 包起來之後，正式程式的呼叫端是否都一致？`apply_repair` 沒發出請求的原因是什麼？
3. 2.2：改測試，還是在 reader 補回「請宣告」提示？
4. 2.3：暫存區的 `test_stock_daily_preopen.py`（116aa837）是不是要部署的版本？
5. 1.2：是否同意 V5.1 的兩點改法？有沒有其他地方會受影響？
6. 1.1：run 20261101T133654Z-b83b23ba 有沒有因為 00:06 的中止，留下需要處理的狀態？

## 4. V5.1 部署方式（使用者授權後才做）

- 時段：
  - 避開 updater：每日 23:00、06:30，以及 codex 手動執行的 run；
  - 避開 07:15 predict 前後。
  - 原因：updater 每次執行都會重讀這個檔，讀取中被換掉會報 `model_price_view_consumer_source_changed`。
- 步驟：
  1. 確認沒有 updater 行程在跑；
  2. 備份 V5 的兩個檔；
  3. 原子替換，或套用 `V5_to_V51.patch`；
  4. 核對 SHA：9971431c／a01034ea；
  5. 在 `D:\stock` 下用相對路徑跑那兩支測試；
  6. 下一次 updater run 之後，查看 `update_errors.jsonl`。
- 回復：換回備份的 V5 兩個檔。

## 5. 不在這份交接範圍

- `update_errors.jsonl` 第 75～78 行（20261102 上午、08:43 以前）我沒有分析。
- H63 交接檔列出的其他既有失敗（`test_stock_daily_selected_cache.py` 4 項等），我沒有查證。
- 提交方式（PD-0001）由使用者決定。`D:\stock` 目前有三批未提交的修改：codex 11-01 批次、V5、H63。

## 6. 證據位置（`W` 見開頭）

- `W\codex_compat\V5_CODEX_COMPAT_20261102.md`：相容性複核的完整紀錄
- `W\codex_compat\out\test_compare.json`、`test_compare_v51.json`、`junit_*.xml`：副本測試結果
- `W\codex_compat\handoff_check\updater_rerun_out.txt`：本次重跑 updater 63 個節點
- `W\codex_compat\review\updater_files_out.txt`、`updater_baseline_out.txt`：複核時用 V5 和 HEAD 各跑一次
- `W\codex_compat\handoff_check\inplace_preopen_test_out.txt`：現行 `test_stock_daily_preopen.py` 在副本重跑
- `W\codex_compat\review\keyed_v5\`、`keyed_v51\`：QA pin 改後重跑
- `W\deploy_v5\stage_check_v51.json`：V5.1 的真實資料驗證
- `W\deploy_v5\DEPLOY_RECORD_20261102.md`：V5 部署紀錄
- `W\codex_compat\handoff_check\verify_state.py`、`ast_digest.py`、`bp_lines.py`：本次查證用的 SHA、AST、行號腳本
