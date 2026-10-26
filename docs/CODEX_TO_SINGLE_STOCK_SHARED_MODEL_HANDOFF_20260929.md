# 個股共用模型交接

> 2026-10-12 註記：現行 runtime 為 D:\stock\.venv；INSTALL_SINGLE_STOCK_3M_DEPS.cmd 已移至 single_stock_analysis\tools\_retired\

快照時間：2026-09-29 19:22:10.209 +08:00（2026-09-29 11:22:10.215 UTC）

## 設計與驗收

- 設計已核准：所有股票共用同一組 fitted weights，不為每檔股票各自訓練。共用模型有兩個 target head：three_month / horizon 63，以及 tomorrow_open / horizon 1。
- 不再把舊的四個逐股 frozen model 當正式模型或 fallback；舊檔保留、不覆寫、不清理。manifest 只提供 50 檔股票 ID 與 metadata（另有 25 檔 reserve），不得沿用研究模型權重。
- 來源凍結後必要的 31 個案例已全部通過；最後受影響 2 案例為 2 passed / 14.18 秒。不要重跑。
- 共用 source 模組：src/tw_single_stock_frozen.py、tw_single_stock_shared_comparable.py、tw_single_stock_shared_model.py、tw_single_stock_shared_training.py、tw_single_stock_train_shared.py。推論入口亦改接共用模型：src/tw_single_stock_3m_analyzer.py、tw_single_stock_tomorrow_open.py、tw_single_stock_daily.py。
- 正式啟動器：D:\stock\programs\single_stock_analysis\tools\TRAIN_SHARED_STOCK_MODEL.cmd。固定 runtime：D:\stock\GitHub\.venv\Scripts\python.exe。

source seal 由 tw_single_stock_frozen.source_seal() 對下列 source files 建立 SHA-256 map，再由 source_seal_id() 對 canonical map 建 ID：tw_single_stock_frozen.py、tw_single_stock_3m_analyzer.py、tw_single_stock_tomorrow_open.py、tw_single_stock_comparable.py、tw_single_stock_daily.py、tw_single_stock_weekend.py、published_single_stock_reference.py、tw_single_stock_cross_stock_calibration.py、tw_single_stock_shared_model.py、tw_single_stock_shared_training.py、tw_single_stock_train_shared.py、tw_single_stock_shared_comparable.py。這次快照時 run_summary 尚不存在，因此實際執行的 source_seal / source_fingerprint 尚未落盤；不要臆造其值。完成後以 run_summary.json 的 source.source_seal、source.source_seal_id、source.fingerprint 為準。

## 目前唯一正式執行

本次 run 以 Hidden 啟動，未修改全域環境變數。工作目錄為 D:\stock\programs\single_stock_analysis。命令為：

    D:\stock\GitHub\.venv\Scripts\python.exe -B -u D:\stock\programs\single_stock_analysis\src\tw_single_stock_train_shared.py --sample-manifest D:\TWStockData\program_data\dstock_single\research_random_50\run_20260927T182753_seed20260927_pooled50\sample_manifest.json --modes three_month tomorrow_open --horizon 63 --resume --run-dir D:\TWStockData\program_data\dstock_single\models\shared_stock\training\current

子程序環境：PYTHONUTF8=1、PYTHONIOENCODING=utf-8、MPLBACKEND=Agg；PYTHONPATH 前置 D:\stock\programs\single_stock_analysis\src 與 D:\stock\programs\shared，原有 PYTHONPATH（若有）附加保留。

19:22:10 +08:00 單次唯讀快照：

- redirector / parent PID 45744；實際 Python child PID 55256。child 的 parent 為 45744。啟動時間約 19:11:21.5 +08:00。
- stdout：D:\TWStockData\program_data\dstock_single\models\shared_stock\training\logs\shared_train_20260929_191121_509.stdout.log
- stderr：D:\TWStockData\program_data\dstock_single\models\shared_stock\training\logs\shared_train_20260929_191121_509.stderr.log（0 bytes）
- 最新 stdout 僅有：
  - [phase] load selected-stock manifest
  - [phase] pin published source context stocks=50
  - batch prewarm routes=17 stocks=50
- 當時 child 累計 CPU 約 638 秒、working set 約 1.075 GB、private memory 約 2.58 GB；系統 free memory 約 44.6 GB / total 約 66.9 GB。
- training/current 目錄當時沒有任何檔案或子目錄：沒有 packet checkpoint、candidate、run_summary 或 errors 檔。因此目前確認仍在 published-source batch prewarm，尚未到 packet preparation、fit、candidate commit 或 active publication；不宣稱已成功 resume。shared model current pointer 也尚未由本次 run 更新或驗證。

執行輸出位置：

- run directory：D:\TWStockData\program_data\dstock_single\models\shared_stock\training\current
- packet checkpoints：training\current\packets\<mode>\<sha256-stock-id>.pkl
- mode candidates：training\current\candidates\three_month.pkl 與 tomorrow_open.pkl
- summary / error ledger：training\current\run_summary.json 與 errors.jsonl
- 正式共用模型 pointer：D:\TWStockData\program_data\dstock_single\models\shared_stock\current.json
- immutable shared artifacts：D:\TWStockData\program_data\dstock_single\models\shared_stock\versions\<artifact-sha256>.pkl

這些是依 controller/storage source contract 的預期位置；本次快照中 run directory 是空的，pointer/artifact 尚未由此次訓練產生。source-seal runtime 值也未輸出。接手者先觀察既有 parent/child 與上述 stdout、checkpoint、summary；程序存在時不得再啟動。只有確認程序已不存在且需要續跑時才使用原命令的 --resume；保留所有 checkpoint，不刪檔、不降低 PARTIAL 或資格條件。

## 重複啟動事故紀錄

第一次 GO 檢查只比對了含 src 的完整文字，漏掉已存在程序命令列中的 tools\..\src 正規化路徑；因此不慎重複啟動 PID 47732 / child 33064。依 root 明確授權已核對 command line 和 parent-child 身分並停止這兩個本代理啟動的 PID。較早的 PID 54104 / parent 34408 是透過 TRAIN_SHARED_STOCK_MODEL.cmd 啟動，沒有 --resume、同用 training\current；本代理未停止它。之後唯讀核對時該較早程序已自行退出，沒有可確認的 log 或 checkpoint，退出原因未知，不能當成功。

目前 45744 / 55256 是其後按 root 恢復 GO 唯一重新啟動的 instance。程序偵測必須用 tw_single_stock_train_shared.py basename，並正規化 tools\..\src 路徑；不能只比對 \src\ 字串。不要再啟動第二份。

## 與 TWStockUpdater 的界線

Updater run 20260929T041801Z-f5549d9e 的 O1 receipt 與 canonical checkpoint 已回報 SUCCESS / receipt_complete=true，正式 payload 為 21,956,777,200 bytes；worker PID 54768 與 redirector PID 30680 已停止。Updater 的 O12 修正驗證與 O21 續跑由原 TWStockUpdater 對話處理，共用模型完成不是 updater 恢復前置。本交接不授權修改、啟停 Updater、Central、MIX 或 Top10；需要資源協調時通知 root。

此檔是交接資料，不代表已送達或已由另一個對話接收。
