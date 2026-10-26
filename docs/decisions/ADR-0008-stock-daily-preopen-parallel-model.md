# ADR-0008：開盤前版 H1 模型（stock_daily_preopen）與 C0 並行，並取代三個消費端的 H1 顯示

- ID：ADR-0008
- 日期：2026-10-18
- 狀態：`accepted`（使用者 2026-10-18 在對話中指示「執行套用新・開盤前版模型」，並選擇「直接取代 C0 顯示」、授權修改 D:\TWStockUpdater、排程之後再註冊）
- 範圍：D:\stock\programs\shared（新模組 stock_daily_preopen*）、D:\TWStockData\program_data\stock_daily_preopen（新資料根）、TWNextDayTop10、D:\MIX All、single_stock_analysis 的 H1 顯示；D:\TWStockUpdater 的 global_market_history 收集
- supersedes：N/A（C0 契約與 ADR-0002～0007 不變；C0 仍是 MIX H5/H20/H60 與 H1 備援）
- superseded_by：N/A

## 背景

- 共用 C0（`models\current.json` → artifact 2a1bc6a4…）在所有期限都退回常數基準：每檔同機率（H1 0.4359）、預估報酬 0，`stock_ranking_signal=false`。Top10 實際依代號排序。
- 2026-10-18 的校正研究（`D:\TWStockData\program_data\stock_daily_c0\candidates\calibration_20261018\report\校正報告_20261018.md`）以 5 年、約 2,038 檔上市櫃普通股做每月重訓的 walk-forward，2026-01-02～10-16 為一次性保留期（17 個設定於 2026-10-17 22:15 UTC 以 sha256 凍結）。
- C0 原公式（7 價格特徵線性）以全市場重訓，保留期仍等於一律猜跌（55.8% vs 55.9%）。

## 選項

1. 維持 C0 常數基準。
2. 恢復舊 MIX v11（H1 與新模型同級，但 H5/H20 無排序力或反向、依賴已停用的資料層，恢復約 1～2 週）。
3. 把新模型塞進 C0 契約（`tw-stock-daily-linear-c0-v1` 只容許 7 特徵線性；HGB 不相容，且中央更新器依 C0 forward 帳冊決定保留範圍）。
4. 新增與 C0 並行的模型系列 `stock_daily_preopen`，有自己的指標、快照、前瞻帳冊，消費端以唯讀 API 取用 H1。

## 決定

採用選項 4。

- 模型：凍結設定 `h1pre_final_nogc_xs`（sha256 e53c2416…）。sklearn HGB 分類器＋超額報酬迴歸器＋日層級 ridge 大盤估計；特徵為價量、法人／融資券／當沖／估值／外資持股、月營收、市場脈絡、前一晚美股（on_*，不含匯率）；504 交易日訓練窗、每 21 個交易日重訓、50% 抽樣、seed 0。機率與報酬以最近 120 個已實現日的樣本外預測做 Platt／LAD 收縮校準。
- 產製：`programs\shared\stock_daily_preopen.py`（train／calibrate／predict／settle／status）。資料只讀中央不可變 object（釘選當次 head＋保留的 5 年歷史 head a807be3e…），寫入 `D:\TWStockData\program_data\stock_daily_preopen`。
- 消費：三個程式透過 `stock_daily_preopen_reader.load_latest(expected_signal_date=本次最新交易日)` 取用。快照新鮮時 H1 顯示開盤前版；不可用或過期時回退 C0，並標示原因。C0 契約、C0 forward 帳冊與 MIX 的 H5/H20/H60 不變。
- 美股資料：前一晚美股收盤缺漏時，快照標示 `us_overnight.status=missing`。預設拒絕發布；必要時以 `--allow-missing-us` 降級發布（on_* 設為缺值、Top10 閘門未知）。上游 TWStockUpdater 修正 FinMind 缺尾段時的 provider 補尾與 coverage 上界，並新增只更新國際資料的模式。
- 時程：產製應在美股收盤後、台股 08:30 前執行。排程註冊另行授權，本 ADR 不註冊排程。

## 理由

保留期（模型未見過）H1 結果（校準後）：

| 指標 | 開盤前版（本設定） | 一律猜跌 | 現行 C0 |
| --- | --- | --- | --- |
| 方向準確率 | 59.0%（Newey-West t 2.38，8/10 月勝出） | 55.9% | 55.9% |
| AUC | 0.608 | — | 0.501 |
| 前一晚美股收漲日 Top10 命中 | 63.5% | — | — |
| 全部日子 Top10 命中 | 55.6% | — | 45.1% |

與舊 MIX v11 凍結冠軍在 2026-08-17 之後同期相比：方向 58.8% vs 58.8%，AUC 0.614 vs 0.616，同級；但舊 v11 的 H5 Rank IC 0.001、H20 為 −0.025。

## 證據

- 校正報告、凍結設定與逐筆預測：`D:\TWStockData\program_data\stock_daily_c0\candidates\calibration_20261018\`（`report\校正報告_20261018.md`、`results\frozen_configs.json`、`results\holdout\h1pre_final_nogc_xs\`）。已驗證（2026-10-18）。
- 特徵一致性：正式模組與研究版在 1,991,820 列 × 178 欄完全一致（`tests\test_stock_daily_preopen.py::test_feature_parity_with_research_panel`，需 `PREOPEN_PARITY=1`）。已驗證（2026-10-18）。
- 前瞻帳冊 `D:\TWStockData\program_data\stock_daily_preopen\state\forward.sqlite3`：自 2026-10-16 訊號日起累積，實盤命中率**待結算**。
- 2026-10-18：中央 global_market_history 補錄完成（head 5fa6f5b0… → 4513ff48…，run 20261018T073236Z-c9d90076，只有該路由變動）；第一份正式快照 run 20261018T073748Z_a05c7ef1（訊號日 2026-10-16 → 目標日 2026-10-19，1,911 檔，可交易 836 檔，美股狀態 ok，閘門為非建議日）。已驗證。
- 排程：2026-10-18 17:12 使用者授權後以 `D:\stock\programs\shared\preopen_schedule\INSTALL_PREOPEN_SCHEDULE.ps1 -Register -ConfirmRegister` 註冊 5 個任務（週一至週五 23:00 中央完整更新、06:30 國際更新、07:15 快照、07:30 Top10、07:40 MIX），並以既有 `INSTALL_MIX_WEEKDAY_0800_TASK.ps1` 註冊 MIX Telegram 08:00；`-Verify` 與 `Get-ScheduledTaskInfo` 核對一致，首次執行 2026-10-19。已驗證。時間表與失敗行為見 `D:\stock\docs\PREOPEN_DAILY_SCHEDULE_PLAN_20261018.md`。

## 後果

- 命中率只代表「預測收盤報價方向」。研究顯示若在隔日開盤買進 Top10，平均報酬為負（約 −0.04%～−0.20%／日），不得當作交易建議。
- 模型依賴 t 日晚間公布的籌碼資料與前一晚美股，中央資料必須在 08:30 前更新；目前沒有自動排程。
- 504 日訓練窗需要 5 年價格物件（保留在 head a807be3e）；若中央回收該 head，重訓會失敗並明確報錯。
- 新增檔案需登錄 `programs\STABLE_PROGRAM_PATHS.txt`；消費端改動的原檔快照保存在 `D:\TWStockData\program_data\stock_daily_c0\candidates\calibration_20261018\deploy_snapshots\`。

## 回復方式

- 立即回復：三個消費端以 model source `c0` 開關（或移除 `D:\TWStockData\program_data\stock_daily_preopen\predictions\latest.json`）即回到 C0 顯示。
- 完整回復：依 `deploy_snapshots\CHANGES_*.md` 把原檔還原；`stock_daily_preopen` 資料根與模組可保留作研究或刪除。

## 修訂（2026-10-22）：配方 v2 `h1pre_v2_ens`

- 狀態：`accepted`（使用者 2026-10-21 對話「可以部屬」；2026-10-22「如果前面模型確實有提升能力可先套用」、「我人不在電腦前 可直接幫我執行」）。
- 研究：`D:\TWStockData\program_data\stock_daily_c0\candidates\h1_improve_20261021\`（`report\H1改良研究報告_20261021.md`、`report\研究日誌.md`）。只用 2023-07～2025-12 的 dev 期挑選，保留期在事前登記後只評估一次。
- 配方：模型容量改為 63 葉、min_samples_leaf 1000、L2 5、max_features 0.5、300 輪；3 個種子 bagging，再與 756 日訓練窗、近期加權（半衰期 252 日）兩個成員等權集成。校準仍為 Platt＋LAD 收縮，窗由 120 日改為 250 日，並以研究的樣本外預測初始化校準池（`models\seeds\`，驗證 sha256）。特徵、標籤、504 日主窗、每 21 個交易日重訓、快照格式不變；frozen_sha256 `84343f31…`。
- 保留期（2026-01-02～2026-10-19，191 個交易日）對 v1：

| 指標 | v1 `h1pre_final_nogc_xs` | v2 `h1pre_v2_ens` |
| --- | --- | --- |
| 方向準確率 | 58.95% | 59.99%（逐日配對 +1.04 pp，95% 區間 +0.20～+1.81） |
| AUC | 0.607 | 0.619 |
| 整體 MAE | 1.948% | 1.943% |
| 可交易 Top10 命中 | 55.4% | 57.1% |
| 前一晚美股收漲日 Top10 命中 | 62.8% | 65.3% |
| 可交易 Top10 MAE | 2.52% | 2.71% |

- 事前登記的 6 項條件中，「Top10 MAE 增加不超過 0.15 pp」未通過（+0.19 pp）。原因是 v2 的 Top10 選到漲跌幅較大的股票；相對誤差反而較小。依規則交由使用者決定，使用者核准部署。
- 前一晚美股缺漏時（研究 30 個 dev fold、校準後、單種子核心模型）：v1 掉 1.65 pp、v2 核心模型掉 2.10 pp，v2 的準確度優勢縮小到不顯著（+0.10 pp），Top10 命中仍較高（+2.6 pp）。TWStockUpdater 已於 2026-10-21 修正收盤後日線被當成盤中報價的問題。
- 若在隔日開盤買進 Top10，v2 保留期平均約為零（約 +0.01%／日，未扣成本）；命中率不代表交易獲利。
- 啟用：2026-10-22 09:52（台北）於正式資料根執行 `train --recipe h1pre_v2_ens --force --authorization "<核准原文>"`，模型 `3bc36b6ce54c`（訓練列 1,380,221，2023-09-06～2026-10-19）。每月例行重訓沿用 v2。消費端（MIX、TWNextDayTop10、MIX 08:00 Telegram 傳送程式）已改為依快照 `recipe` 選擇揭露；單股與 Telegram Bot 的揭露修改需重啟 Bot 服務，待使用者在電腦前時一起部署，在此之前這兩處對 v2 快照仍顯示 v1 揭露文字。紀錄：`deploy_snapshots\CHANGES_host_preopen.md`、`CHANGES_MIX_All.md`、`CHANGES_TWNextDayTop10.md`。
- 回復 v1：`train --recipe h1pre_final_nogc_xs --force --authorization "回復 v1"`（約 40 秒，之後例行重訓維持 v1）；程式回復見 CHANGES。
