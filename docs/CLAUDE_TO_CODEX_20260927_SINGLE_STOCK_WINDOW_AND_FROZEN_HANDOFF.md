# Claude → Codex：個股分析「歷史長度評估＋凍結模型初始化＋使用者選配方」交接（2026-09-26～27）

產出：Claude（session `51ab15cb-7b36-4847-b785-5ba122936b29`）
用途：給 codex 核對。**這是紀錄，不是要你執行的計畫。** 需要你決定的事集中在第 8 節。
前一份個股分析交接：`D:\stock\docs\CLAUDE_TO_CODEX_20260919_SINGLE_STOCK_HANDOFF.md`（09-19 的八輪修改）。
評估全文：`D:\stock\docs\SINGLE_STOCK_HISTORY_WINDOW_REVIEW_20260926.md`（第 0、11、12 節最新）。

---

## 0. 範圍與所有權（先看）

- **09-20～09-23 有另一個 session 改了個股分析 `src` 的 16 個檔案，沒有留交接文件。** 新增了
  `tw_single_stock_weekend.py`、`tw_single_stock_frozen.py`、`tw_single_stock_process_control.py`、
  `tw_single_stock_comparable.py`、`tw_single_stock_performance.py`、`published_single_stock_reference.py`、
  `tw_order21_support.py`，把架構改成「**週末訓練並提交凍結模型、每日只做推論**」。本次我是在這個新架構上工作。
- **我這次改的東西**（全部在第 3 節）：
  1. 基本面請求窗 2,355 → 1,955 天（封存模組內，但在週末校正**之前**改，不影響模型）。
  2. 新增啟動檔 `run_single_stock_entry.py`，修掉「分析器以 `__main__` 執行造成雙重載入」的錯誤。
  3. 兩個 CMD 包裝檔改走啟動檔。
  4. 新增 `tw_single_stock_recipe_choice.py`：讓使用者逐檔、逐模式指定配方。
  5. 以使用者指定的籃子（2330、2317）跑週末校正，初始化凍結模型庫，並套用使用者選擇。
- **我沒有改**：`tw_single_stock_frozen.source_seal()` 涵蓋的 7 個模組（3m_analyzer 除外，見 3.1）、
  TWStockUpdater、MIX ALL、共同資料區。

---

## 1. 現況快照（2026-09-27 01:35）

- 凍結模型庫 `D:\TWStockData\program_data\dstock_single\models\single_stock_frozen\current.json`
  （sha256 前 16 碼 `9171fd365bf9750e`）授權 4 個模型，**全部與目前原始碼雜湊相容**（逐一 `load_bundle` 通過）：

| 模型 | 配方 | sha256 前 16 碼 | 提交原因 | 決定者 |
| --- | --- | --- | --- | --- |
| 2330 three_month (h63) | core-v1（5 年） | e6bc40e9fff15a1f | initial_validated_recipe_after_120_session_replay | 使用者 |
| 2330 tomorrow_open | core-v1（5 年） | 85f3a2bcdd1aeac5 | 同上 | 使用者委託 Claude 判斷 |
| 2317 three_month (h63) | core-recent-v1（3 年） | 53d7cef0f0c2b9a4 | user_selected_recipe | 使用者 |
| 2317 tomorrow_open | core-recent-v1（3 年） | 5693f8cbf6618934 | user_selected_recipe | 使用者委託 Claude 判斷 |

  四個模型的 `training_cutoff` 都是 2026-09-24。
- comparable_t22：**未初始化**（資料缺口，見 4.3）。每日流程會把它標成 `pending_weekend_initialization`，不影響另外兩種模式。
- 使用者選擇紀錄：`models\single_stock_frozen\recipe_choices.json`（`9ff0166cf1728f5c`）。
- 其他股票：凍結模型庫只有 2330、2317。**其他股票在跑過週末校正前都出不了報告。**
- **端到端正式執行已通過**（第 6 節）：2317（01:37～02:10）、2330（02:10～02:42）皆結束碼 0；
  報告已發布，指標最後指向 2330，預測已存入歷史帳本。每檔約 33 分鐘，大部分花在保留來源（8.9）。

---

## 2. 使用者的問題與結論

**問題**：個股分析需要 5 年／2,355 天嗎？不影響準確度的話，抓那麼多是浪費時間。

**結論**：
1. 2,355 天是 `--lookback-years 5.0` 推導出來的「5 年＋130 天暖身＋400 天基本面緩衝」。
   共同資料區實際只有 5.03 年（2021-09-13 起），多要的部分資料本來就不存在 → 已收斂（3.1）。
2. **砍年數省不到時間**：每日執行已經不訓練，約 80% 時間花在把 13 條全市場路由（9,367 MB）讀完並逐檔 sha256。
   2330 自己五年只用到 4.91 MB，讀取放大約 1,900 倍。縮短年數不會少讀任何位元組。
3. 保存天數由 MIX 決定（`maximum_sessions=1260`＋`feature_lookback_sessions=120`），砍個股分析的年數也不會少抓。
4. **準確度面**：程式本來就內建答案——週末校正每週比較 `core-v1`（完整約 5 年）與 `core-recent-v1`（`price.tail(756)`，約 3 年），
   用 120 個已到期決策日做配對回放。這次的實證在第 5 節：兩種模式自動規則都維持 5 年，但各股差異大，最後由使用者逐檔選擇。

---

## 3. 本次修改逐項

### 3.1 基本面請求窗 2,355 → 1,955 天（09-26 約 20:15 部署）

- 檔案：`src/tw_single_stock_3m_analyzer.py`（`cb0c7197252ba9af` → **`fc35303f12e4d837`**）。
- 新增 `SHARED_STORE_RETENTION_DAYS = 1890`；`analysis_date_ranges()` 的基本面窗改為
  `min(max(1100, calendar_days + 400), max(calendar_days, SHARED_STORE_RETENTION_DAYS))`，並回傳 `fundamental_days`。
  5 年設定 2,355 → 1,955；3 年（1,625）與 1 年（1,100）不變；6 年時收斂到每日類窗。每日類窗與模型窗沒動。
- **數值中立的證明**：掃描全市場 artifact，月營收、綜合損益、資產負債、現金流、股利、除權息在新起點 2021-05-18 之前的列數**全部為 0**。
  `fundamental_start` 只當 `fetch_month_revenue` 的起點，`_select` 只拿它篩列，不進 lineage／指紋。→ 不需升模型版本。
- 測試：7 個相關測試檔在「未修改副本」與「修改後副本」各跑一次，**失敗清單完全相同（41 個既有失敗）**。
  `tests/test_single_stock_data_sources.py`（`f21132dc9d6f3d86` → **`fefffccbdc821e7a`**）：刻意更新穩定性測試的
  `fundamental_start` 期望值（2020-02-01 → 2021-03-07），並新增 `test_fundamental_window_is_bounded_by_shared_store_retention`。
- **時機**：這個檔案在 `source_seal()` 內，所以刻意在週末校正**開始之前**部署，模型是用新雜湊訓練的。
- 備份：`D:\stock\backups\claude_20260926_single_stock_fundamental_window\`。

### 3.2 修正「分析器以 `__main__` 執行造成雙重載入」——**不修的話任何股票都出不了報告**

- **症狀**：凍結模型提交後，三個月分析一律以 `formal_model_result_invalid` 失敗。
- **根因（已重現）**：CMD 用 `"%PY%" "%SCRIPT%"` 直接執行 `tw_single_stock_3m_analyzer.py`，它在 Python 內的名字是 `__main__`；
  而 `tw_single_stock_frozen.infer_native()` 用 `import tw_single_stock_3m_analyzer as base` 建立結果，
  於是記憶體裡有兩份模組、兩個不同的 `ModelResult` 類別，
  `_require_formal_three_month_model()` 的 `isinstance(model, ModelResult)` 判定失敗。
  - 用 `runpy.run_path(..., run_name="__main_sim__")` 模擬腳本那一份：`ModelResult is base.ModelResult` 為 `False`；
    同一個推論結果，腳本那份閘門擋下、模組那份閘門通過。
  - 次日開盤（`TomorrowOpenModelResult`）是同一個機制。
  - 週末校正以模組名稱匯入分析器，所以訓練正常；**每日推論路徑從未端到端跑通過**。
- **為什麼不改分析器本身**：`validate_bundle()` 在 `bundle["source_seal"] != source_seal()` 時丟
  `frozen_code_changed_pending_weekend_revalidation`。改任何封存模組都會讓已提交的模型全部失效，而且會卡住週末校正（見 8.1）。
- **修法（不在封存範圍內）**：
  - 新增 `src/run_single_stock_entry.py`（**`d1667ca97bb55b71`**）：用 `importlib.import_module()` 以模組名稱載入分析器再呼叫 `main()`。
    只接受 `tw_single_stock_3m_analyzer`、`tw_single_stock_tomorrow_open`。
  - `RUN_SINGLE_STOCK_3M_ANALYSIS.cmd`（`2dce36dcd8b8ff0b` → **`74d0e38899cb1aaa`**）與
    `RUN_SINGLE_STOCK_TOMORROW_OPEN.cmd`（`e8c48966fe452ca1` → **`864bd9b745e27447`**）：在 `:RunAnalyzer` 後設定 `ENTRY`／`MODULE`，
    4 處 `"%PY%" "%SCRIPT%"` 改為 `"%PY%" "%ENTRY%" %MODULE%`。這兩個檔是 CRLF／LF 混用，以位元組精確修改，保留原行尾。
    部署前確認「正式檔套上同一個修改後」與沙箱內容逐位元組相同。
- **驗證**：用啟動檔跑 2330 三個月（`--provisional`，輸出到 `dstock_single\_verify_frozen_20260927\3m_entry`），結束碼 0，
  訊息為「凍結模型當期推論完成；沒有重新訓練」，上漲機率 76.34%、預估三個月報酬 10.20%，PDF 與 JSON 皆產出。
  修正前同樣的指令：`formal_model_result_invalid`。
- 備份：`D:\stock\backups\claude_20260927_single_stock_entry_and_recipe_choice\`（兩個 CMD 原檔＋`original_sha256.txt`）。
- **我自己造成的錯誤與修正（01:34～01:40）**：
  - **原因**：第一版包裝檔寫入時，路徑 `src\run_single_stock_entry.py` 的 `\r` 被轉成 CR 位元組（0x0D），
    實際內容變成 `src<CR>un_single_stock_entry.py`，`if not exist "%ENTRY%" goto EndFail` 因此直接失敗。
    原因是這個環境的 Bash heredoc 會把 `\\` 轉成 `\`，Python 原始碼裡的 `\\r` 變成了 `\r`。
  - **為什麼沒提早發現**：沙箱驗證時我是直接執行啟動檔、沒經過 CMD。直到 01:34 第一次正式執行（2317、2330），兩檔都在 26 秒內以結束碼 1 失敗才發現。
  - **修正**：以十六進位位元組把 `src 0x0D un_…` 換回 `src 0x5C run_…`（沙箱與正式各兩檔）。
  - **煙霧測試**：兩個包裝檔加 `--source-mode published --help` 都結束碼 0，並印出分析器的說明。
  - **紀錄與殘留**：第一次失敗的紀錄保留為 `formal_*_stdout_attempt1_cr_bug.log` 與 `formal_pair_result_attempt1_cr_bug.json`。
    那次失敗在 `dstock_single\report_runs\` 留下兩個隱藏暫存目錄
    `.single_stock_staging_08160734db1540149c32f25ec2b3faf0`、`.single_stock_staging_58c59b1dfd7746cd8d6e8b17e0d00556`
    （CMD 自己保留供診斷），可清理。
  - **教訓**：修改 CMD 後要**經由 CMD 本身**驗證，不能只驗證它呼叫的程式。

### 3.3 使用者指定配方：`src/tw_single_stock_recipe_choice.py`（新增，**`f0e7eb454b28128e`**）

- **需求**：使用者要求「用哪個模型由我選，但你要分析利弊」。
- **做法**：完全照 `tw_single_stock_weekend.worker()` 的最後一步，用本週存下的輸入 packet
  （`weekend\<週>\inputs\<mode>\<stock>.pkl`，並檢查 source_seal），以 `weekend._packet_at(packet, 最後日期, recipe)` 與 `weekend.build()`
  在目前截止點重建選定配方，再經 `frozen.stage_bundle()`＋`frozen.commit_package()` 提交。週末時段、原始碼雜湊、檔案雜湊、提交歷史等閘門都沒有繞過。
- bundle 會多兩個欄位：`weekend_evaluation`（本週配對回放證據）與 `recipe_selection`
  （basis=`user_selected_recipe`、配方、決定者、時間、備註、自動規則當時的結果、證據檔路徑）。
  提交閘門 `reason=user_selected_recipe`。
- 同時寫 `models\single_stock_frozen\recipe_choices.json`（目前選擇＋歷史）與
  `weekend\<週>\user_recipe_choice_<stock>_<mode>.json`（每次套用的收據）。
- 若現任模型已經是該配方 → 回傳 `already_committed`，只記錄不重複提交。
- 僅支援 three_month／tomorrow_open（comparable_t22 尚無模型）。
- 在 `source_seal()` 之外，套用選擇**不會讓其他模型失效**。
- **沙箱測試**（模型庫複本 `scratchpad\choice_test`）：2317 兩個模式提交成功（三個月 16 秒、次日開盤 56 秒）；新模型可載入、可推論，
  三個月通過正式閘門；重跑回傳 `already_committed`；正式庫 `current.json` 雜湊不變，正式快取沒有新檔。
- **正式套用**（2026-09-27 01:32～01:33）：

| 股票 | 模式 | 選擇 | 結果 | 決定者 |
| --- | --- | --- | --- | --- |
| 2317 | three_month | core-recent-v1 | committed `53d7cef0f0c2` | 使用者 |
| 2317 | tomorrow_open | core-recent-v1 | committed `5693f8cbf661` | 使用者委託 Claude 判斷 |
| 2330 | three_month | core-v1 | already_committed | 使用者 |
| 2330 | tomorrow_open | core-v1 | already_committed | 使用者委託 Claude 判斷 |

  套用前的 `current.json` 備份在 `claude_20260927_single_stock_entry_and_recipe_choice\current.json.before_choice`（`40bb4a6106b36023`）。
- **重要語意**：週末校正的 worker **不讀** `recipe_choices.json`。下週末時，使用者選的配方會成為「現任」，
  只有另一個配方在配對回放中依自動規則勝出才會被換掉。要「鎖定」選擇需改 `weekend.py`（見 8.3）。

---

## 4. 週末校正（凍結模型初始化）

### 4.1 執行

- 指令：`RUN_SINGLE_STOCK_WEEKEND_CALIBRATION.cmd --stocks 2330 2317`（籃子由使用者指定）。
- 啟動腳本：`D:\stock\logs\single_stock_weekend_20260926\run_weekend.ps1`。只在該程序把 `PSModulePath` 設回 Windows PowerShell 5.1 預設，
  避免 PS7 模組路徑洩漏到 `find_python.cmd` 的 5.1 子程序（09-19 踩過）。
- 時間：09-26 20:24:20 → 09-27 00:18:29，14,049 秒，**結束碼 2**（comparable_t22 未完成；另兩個模式成功並提交）。
- 過程：
  - 每個（股票、模式）讀一次輸入約 15 分鐘（因為自行 pin 全部路由，含保留來源）。
  - 三個月回放 104 個歷史週末模型，每個約 14 秒。
  - 次日開盤回放約 104 個，每個約 77 秒。
- 輸出：`models\single_stock_frozen\weekend\2026-09-25\`，內含 `checkpoint.json`、`registered_evaluation_basket.json`、
  `<mode>_incumbent_registration.json`、`<mode>_paired_oos.json`、`inputs\`、`replay\`。模型庫目前共 1.1 GB。

### 4.2 結果

| 模式 | 狀態 | 自動規則 | 提交 |
| --- | --- | --- | --- |
| three_month | 完成 | 兩檔皆 core-v1（`keep_original_core`） | 是（00:18） |
| tomorrow_open | 完成 | 兩檔皆 core-v1（`keep_original_core`） | 是（00:18） |
| comparable_t22 | pending | — | 否 |

### 4.3 comparable_t22 為什麼被擋（資料缺口，不是程式錯）

1. `reason=projection_not_exposed`：價格序列沒有「官方實際公布時間」欄位，1,216 個交易日全部列在 `missing_sessions`，
   `publication_field_mapping` 為空 → 無法證明 22:00 前可得 → `comparable_historical_close_availability_unknown`。
2. packet 內的價格是 `split_dividend_adjusted`，這個模式規定必須是 raw／unadjusted。就算補了公布時間，也會在
   `comparable_raw_price_basis_or_source_unknown` 被擋。

---

## 5. 5 年 vs 3 年：配對回放結果與使用者選擇

評估規則：
- 取最近 120 個已到期的決策日，只在歷史週末邊界重訓（不用測試標籤），兩配方逐筆配對。
- 3 年配方要被採用，必須在近 60 筆「命中率 +1pp 且 MAE 不變差」或「MAE −0.10pp 且命中率不變差」或「Brier 更好且兩者都不變差」，**而且** 120 筆命中率與 MAE 都不輸。
- 注意：**自動規則是以兩檔合併評估**，不是逐檔。

### 5.1 三個月（決策期 2025-12-23～2026-06-26）

| 近 120 筆 | 5 年 | 3 年 | 一律猜漲／猜 0 |
| --- | --- | --- | --- |
| 方向命中 | 77.5% | 79.2% | **81.7%** |
| 下跌時猜對 | **7／44** | 0／44 | 0／44 |
| 平均誤差 | **13.42pp** | 13.83pp | 猜 0：15.88pp |

- 分股：2317 用 3 年兩項都較好（命中 63.3% vs 59.2%、誤差 13.37 vs 14.34pp）；2330 用 3 年誤差較大（14.30 vs 12.50pp）。
- **證據很弱**：兩者都輸一律猜漲；各股預測與實際的相關多為負；回放期是大多頭（2330 有 95.8% 期間上漲）；
  63 日期間重疊，每檔實際只有 2～3 個獨立樣本。

### 5.2 次日開盤（決策期 2026-04-02～2026-09-23；1 日視野，樣本不重疊）

| 項目 | 5 年 | 3 年 | 一律猜漲／猜 0 |
| --- | --- | --- | --- |
| 命中（近 120） | 55.0% | 57.1% | **58.3%** |
| 命中（近 60） | **52.5%** | 50.8% | 53.3% |
| 平均誤差（近 120） | 1.241pp | **1.201pp** | 猜 0：1.239pp |
| 上漲日猜對／下跌日猜對 | 54%／**56%** | **92%**／8% | — |
| 預測漲幅波動（實際 1.69pp） | 0.03pp | 0.13pp | — |

- 5 年偏空（2330 下跌日 83% 猜對、上漲日 15%）；3 年偏多。
- 唯一看得到微弱優勢的是 **2317 × 3 年**：誤差 1.154 vs 猜 0 的 1.231pp。
- **更正 09-19 的說法**：當時依單次驗證 AUC 0.54 說「次日開盤模型有效」。每週重訓的回放顯示兩個配方都沒有超越簡單基準。

### 5.3 使用者的選擇與理由

- 2330 三個月：**5 年**（使用者選）。
- 2317 三個月：**3 年**（使用者選）。
- 2330 次日開盤：**5 年**（使用者委託 Claude 判斷）。兩配方皆無優於基準證據，近 60 筆 3 年反而較差（45.0% vs 48.3%），
  維持現任；5 年保有下跌日多半示警的特性。
- 2317 次日開盤：**3 年**（使用者委託 Claude 判斷）。近 60 筆命中打平（56.7%），誤差較小（1.004 vs 1.046pp）且優於猜 0（1.042pp）；
  近 120 筆命中（65.0% vs 62.5%）與誤差（1.154 vs 1.233pp）皆較好；和 2317 三個月的選擇一致。代價是下跌日幾乎不示警。

---

## 6. 端到端正式執行驗證（`1_執行個股分析與產生報告.cmd <股票> --source-mode published`）

啟動腳本：`D:\stock\logs\single_stock_weekend_20260926\run_formal_pair.ps1`；結果：`formal_pair_result.json`、`formal_<股票>_stdout.log`。
第一次（01:34）因 3.2 的包裝檔錯誤失敗，修正後第二次：

| 股票 | 起訖 | 秒數 | 結束碼 | 三個月（凍結推論） | 次日開盤（凍結推論） | comparable_t22 |
| --- | --- | --- | --- | --- | --- | --- |
| 2317 | 01:37:08 → 02:10:00 | 1,971.9 | **0** | 上漲機率 46.10%、預估報酬 +7.52%（3 年配方） | 開高機率 47.47%、開盤 +0.40%、高 +1.00%、低 −0.80% | pending（報告明寫不以開盤機率替代） |
| 2330 | 02:10:00 → 02:42:06 | 1,925.8 | **0** | 上漲機率 76.34%、預估報酬 +10.20%（5 年配方） | 開高機率 51.65%、開盤 0.00%、高 +0.81%、低 −0.61% | pending |

- 兩檔皆「凍結模型當期推論完成；沒有重新訓練」，三個月數字與沙箱驗證一致。
- 發布：`latest_report_manifest.json` 最後指向 2330（`single_stock_run_74b9cd53e43d44719e12bc1938512393`）；
  2317 的版本化 run 是 `single_stock_run_78b2565951444b8fb7901af4794cabec`。
- 兩檔的 three_month／tomorrow_open 預測都已存入歷史帳本（`snapshots\<mode>\<mode>\<股票>.json`）；
  v4 performance registration 各 inserted 4。**09-19 的 S1（發布後留存）在凍結架構下仍正常運作。**

**新發現：每檔 33 分鐘，約 28 分鐘花在「保留來源」**（09-19 同步驟 21 秒）：
- 2330 兩個分析的「讀取 Central／Updater 保留來源」各 842 秒、829 秒；可用來源由 09-19 的 5/14 變成 7/14。
- 機制（已從程式碼確認）：`stock_data_sources.iter_stock_artifact_rows()` 只對 `MODEL_ROUTE_IDS`（13 條）使用單檔列快取，
  其他路由一律 `iter_artifact_rows()` 全檔解析。
- 09-22 新增的 `tw_order21_support.py` 會讀 order 21（`finmind_trading_daily_report`，發布頭中 **16.91 GB**）；
  報告中出現分點 1／5／20／60 日視窗文字，證實它有被讀。另有 `finmind_government_bank` 2.53 GB、`tdcc_share_distribution_raw` 2.24 GB 等大型保留路由。
- 逐路由耗時沒有單獨量測；但「16.9 GB 無快取、每個分析各解析一次」和 14 分鐘相符。
- **這是目前最大的時間浪費，和歷史年數無關**（見 8.9）。

---

## 7. 沒有做的事與原因

1. **兩個分析共用一次投影**：原提案「第二個程序看收據就略過雜湊」違反 `tests/test_published_row_cache.py` 刻意設下的不變式：
   熱快取也必須重算整份 sha256，同大小的竄改要被拒。唯一不破壞的做法是同一個程序依序跑兩個分析，屬架構決定。
   列快取（09-21 起）之後實際只省約 100 秒／次，不是原先估的 40%。
2. **comparable_t22 的資料缺口**（4.3）：需要決定原始價來源與公布時間的認定方式。
3. **窗長掃描（1.5／2／3／5 年）沒做完**：每日路徑改成凍結推論後，`--lookback-years` 的掃描沒有意義；已由週末配對回放取代（只比 5 年 vs 3 年）。

---

## 8. 需要 codex 決定／處理的事

1. **source_seal 死結（高）**：改任何封存模組 → `validate_bundle()` 丟 `frozen_code_changed_pending_weekend_revalidation`
   → 每日推論全部失敗；而週末 worker 對已有提交的股票會先 `frozen.load_bundle()` 現任模型，同樣丟例外，整個模式 pending，**無法自動重訓**。
   需要一條「程式碼更新後的重新驗證」路徑（例如 seal 不符時把現任當成未初始化、配方改從 `current.json` 歷史或 `recipe_choices.json` 取得）。
2. **雙重載入的根治**：目前靠啟動檔繞過。下次本來就要改封存模組時，可在兩個分析器頂端加 `__main__` 別名，或讓所有入口一律以模組名稱載入。
   另外 `07_D-stock個股報告.cmd` 等其他入口是否也直接執行腳本，請一併檢查。
3. **使用者選擇要不要「鎖定」**：worker 目前不讀 `recipe_choices.json`。若要鎖定，改 `weekend.py` 讓它優先採用使用者選擇
   （仍計算並保存配對證據）。這會改 seal，須在週末校正開始前部署，並先處理第 1 點。
4. **自動規則是兩檔合併評估**：這次兩檔方向相反（2317 偏好 3 年、2330 偏好 5 年）。是否改成逐檔評估？
5. **comparable_t22 資料**：原始價來源（例如 `official_market_daily`）與公布時間證據（比照 ADR-0005 的政策推估，或上游記錄實際時間）。
6. **次日開盤的 `--output-dir` 沒有 `ExplicitOutputDirAction`**，導致 `--provisional` 永遠回報「必須明確指定 --output-dir」，無法做不發布驗證。
7. **模型能力**：兩種模式、兩個配方都沒有超越「一律猜漲／猜 0」。145～149 個特徵對每檔約 1,200 列（三個月只有約 18 個獨立窗）明顯過多，
   建議減特徵或改面板（`tw_single_stock_panel_challenger`）。
8. **既有測試失敗 41 個**（data_sources 36、deep_audit 3、entrypoints 2），修改前後相同，本次沒處理。
9. **讀取成本（最優先）**：正式執行每檔約 33 分鐘，約 28 分鐘在保留來源（第 6 節）。
   - **最大項**：order 21（16.91 GB）等非 `MODEL_ROUTE_IDS` 路由沒有單檔列快取，每個分析都全檔解析。
     讓保留路由也走單檔列快取（不需放寬雜湊不變式），預估可把每檔時間從 33 分鐘拉回 10 分鐘以內。
   - **其次**：`finmind_loan_collateral` 一條 3,070 MB，佔 13 條模型路由讀取量 33%。
   - **最後**：「同一程序跑兩個分析」約可再省 100 秒／次。
10. **跨程式一致性**：共同資料區保存 1,890 天 ≈ 1,296 個交易日，小於 MIX 需要的 1,380～1,445 個交易日；
    個股分析每日類請求窗 1,955 天也大於 1,890。
11. **`--provisional` 很慢**：自行 pin 全部路由，「保留來源」步驟 CPU-bound 約 14 分鐘／次。週末校正讀輸入也是同一原因（每個股票×模式約 15 分鐘）。
12. **驗證輸出與殘留待清理**：
    - `D:\TWStockData\program_data\dstock_single\_verify_frozen_20260927\`（驗證用 PDF／JSON／log）。
    - `dstock_single\report_runs\` 下的隱藏暫存目錄：
      - 本次失敗留下 2 個：`.single_stock_staging_08160734…`、`.single_stock_staging_58c59b1d…`。
      - 更早就存在 2 個：`.single_stock_staging_9dd2b726…`（09-19）、`.single_stock_staging_c7964cfb…`（09-23）。
13. **正式執行跑 1_執行… 時，comparable_t22 的 pending 會一直出現在報告裡**。在 8.5 解決前這是預期行為。

---

## 9. 檔案、雜湊、備份、證據

### 9.1 部署的檔案（`D:\stock\programs\single_stock_analysis\`）

| 檔案 | 之前 | 之後 | 在 source_seal 內？ |
| --- | --- | --- | --- |
| `src/tw_single_stock_3m_analyzer.py` | cb0c7197252ba9af | fc35303f12e4d837 | 是（週末校正前部署） |
| `tests/test_single_stock_data_sources.py` | f21132dc9d6f3d86 | fefffccbdc821e7a | 否 |
| `RUN_SINGLE_STOCK_3M_ANALYSIS.cmd` | 2dce36dcd8b8ff0b | **ce1665a0ba616756**（第一版 74d0e38899cb1aaa 有錯，見 3.2） | 否 |
| `RUN_SINGLE_STOCK_TOMORROW_OPEN.cmd` | e8c48966fe452ca1 | **0bf3d5dabc5d3bb1**（第一版 864bd9b745e27447 有錯，見 3.2） | 否 |
| `src/run_single_stock_entry.py` | （新增） | d1667ca97bb55b71 | 否 |
| `src/tw_single_stock_recipe_choice.py` | （新增） | f0e7eb454b28128e | 否 |

### 9.2 資料狀態

- `models\single_stock_frozen\current.json`：00:18 週末提交 `40bb4a6106b36023` → 01:33 套用選擇後 `9171fd365bf9750e`（`committed_history` 3 筆）。
- `models\single_stock_frozen\recipe_choices.json`：`9ff0166cf1728f5c`。

### 9.3 備份

- `D:\stock\backups\claude_20260926_single_stock_fundamental_window\`（3m_analyzer、測試原檔）
- `D:\stock\backups\claude_20260927_single_stock_entry_and_recipe_choice\`（兩個 CMD 原檔、`current.json.before_choice`）

**還原注意**：還原 `tw_single_stock_3m_analyzer.py` 會改變 source_seal，使 4 個已提交模型全部失效（見 8.1）。

### 9.4 證據

- `D:\stock\logs\single_stock_weekend_20260926\`：週末校正與正式驗證的啟動腳本、結果 JSON、stdout／stderr。
- `models\single_stock_frozen\weekend\2026-09-25\`：checkpoint、兩份配對回放、輸入 packet、回放模型、使用者選擇收據。
- `D:\TWStockData\program_data\dstock_single\_verify_frozen_20260927\`：修正前（失敗）與修正後（成功）的三個月驗證輸出。
- 評估文件：`D:\stock\docs\SINGLE_STOCK_HISTORY_WINDOW_REVIEW_20260926.md`。

---

## 10. 之後的驗收點

- **週一（09-28）盤前**：正常每日執行應以凍結模型推論產出 2330／2317 報告；
  comparable_t22 顯示 pending；其他股票顯示 `pending_weekend_initialization`（預期）。
- **下週末（10-02 22:00 起）**：週末校正會以本週的配方為現任重新比較；若要鎖定使用者選擇，要在那之前處理 8.1 與 8.3。
- 預測留存與到期結算（09-19 的 S1／S3）在凍結架構下是否仍照常寫入 `snapshots` 與 `audit\settlement`，請一併確認（本次未驗證）。
