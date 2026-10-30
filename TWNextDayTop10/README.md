# 次日 Top10：開盤前版 H1（C0 備援）收盤方向與報酬

雙擊 RUN_TOP10.cmd，使用本專案 .venv 的 Python，從同一正式發布快照讀取動態 TWSE／TPEx 官方普通股池與每市場最近253個真交易日。程式只推論共用已採納 C0 模型，不下載、訓練或載入舊自有模型。

## H1 模型來源（2026-10-18 起）

H1 顯示改用「開盤前版」（HGB，含前一晚美股），C0 保留為備援：C0 推論與共用前瞻帳冊每次照常執行。開盤前版只透過共用唯讀 API `D:\stock\programs\shared\stock_daily_preopen_reader.py` 讀取產製端每天 08:30 前寫出的快照（預設 `D:\TWStockData\program_data\stock_daily_preopen`，可用環境變數 `STOCK_DAILY_PREOPEN_ROOT` 改寫，僅供 QA）。新鮮度以本次 C0 輸入的台股最新交易日為 expected_signal_date；已驗證的下一交易日存在時，快照目標日也必須一致。

開關 `--model-source`（或環境變數 `TOP10_MODEL_SOURCE`；命令列優先）：

- `auto`（預設）：快照可用且新鮮 → H1 顯示開盤前版；否則報告醒目標示「開盤前版不可用（原因代碼），本次 H1 改用 C0 基準」。
- `preopen`：必須使用開盤前版；不可用時在 C0 前瞻帳冊寫入前即失敗。
- `c0`：強制舊行為，不讀取快照。

開盤前版啟用時：每檔 probability／expected_return 改為 p_up／exp_ret；ranking_status 為 `preopen_model_ranking`；Top10 為可交易母體（20 日均成交金額 ≥ 2,000 萬且收盤 ≥ 10）依 p_up→exp_ret 前 10 名，其餘股票依同排序列在後面；每列另保留 C0 數值（c0_probability／c0_expected_return）。HTML、Excel、JSON payload 與 manifest 顯示模型來源、快照說明、前一晚美股閘門（收漲＝建議日；未收漲＝非建議日；未知）與必要揭露（文字依快照配方，見下節）；payload 與 manifest 含 `model_source`、`preopen_meta`（run_id、model_sha256、snapshot_sha256；快照有 `recipe` 時一併保留）。本 .venv 沒有 pyarrow／fastparquet 時，以既有相依 duckdb 解碼 parquet（仍由共用 reader 驗證雜湊、欄位與數值）。

### 必要揭露依快照配方（2026-10-21 起）

共用產製端快照的 `latest.json` 可能帶 `recipe`（`{"name": ..., "frozen_sha256": ...}`）。`preopen_source.py` 依 `recipe.name` 從揭露表（`RECIPE_DISCLOSURES`）取必要揭露、閘門文字與「開盤買進」效果片語；片語放在 `model_disclosure.trading_effect`，HTML、Excel 與 `trading_effect_status` 共用（舊報告資料沒有此欄位時沿用 v1 原文）。閘門狀態碼（recommended_day／not_recommended_day／unknown）、模型來源選擇與排序都不因配方改變。

- 沒有 `recipe` 欄位（舊快照）或 `h1pre_final_nogc_xs`（v1）：保留期 2026-01～10 方向準確率 59.0%（一律猜跌 55.9%）、AUC 0.608；美股收漲夜 Top10 命中 63.5%、全部日子 55.6%；未收漲＝非建議日：歷史 Top10 命中僅約 55%；隔日開盤買進 Top10 平均為負（約 −0.04%／日）。出處 `calibration_20261018\report\校正報告_20261018.md`。
- `h1pre_v2_ens`（v2）：保留期 2026-01-02～2026-10-19（191 個交易日）方向準確率 60.0%（一律猜跌 56.1%）、AUC 0.619；美股收漲夜 Top10 命中 65.3%、其他夜 49.1%、全部日子 57.1%；隔日開盤買進 Top10 約為零（約 +0.01%／日），不是交易獲利的證據；Top10 MAE 2.71%。出處 `h1_improve_20261021\report\H1改良研究報告_20261021.md`。
- 其他名稱（或 `recipe` 格式不符）：中性文字，顯示配方名稱並說明本程式沒有該配方的驗證數字（準確率、命中率與開盤買進效果皆未知）。

CHECK_ENV.cmd 另以唯讀方式檢查開盤前版快照是否可用且新鮮；`--model-source preopen` 時不可用即失敗。

CHECK_ENV.cmd 驗共用 C0 模型、現行契約採納與六核心來源 context，不建立 price panel、不推論或寫前瞻帳冊。缺少專案環境時只提示明確執行 SETUP_ENV.cmd；入口不自動安裝或替換其他專案環境。

輸出 JSON、CSV、Excel、HTML及manifest。H1 是調整後收盤到同市場下一交易日收盤的方向與報酬；沒有預測 OHLC／原始價格。future交易日未驗時日期維持空值，報告清楚標示H1，不猜工作日。

個股必須具253個真市場位置、完整正有限close、當前普通股身分及既有native品質。缺價、新股缺歷史、跨市場或基準異常只排除受影響股票並保存原因；全池無合格股票會失敗，不補0、不延長歷史湊資料。

目前所選基準可能所有股票同值，展示前10列不代表個股更優；報告保留「無個股排序訊號」、PARTIAL、歷史PIT／公司行動／校準可靠性及交易效益未知。原模型科學契約與現行操作契約並列，共用forward校準／成熟記錄保留原版本，不自動重訓。

正常資料為 D:\TWStockData\shared_market_data；active模型為 D:\TWStockData\program_data\stock_daily_c0\models\current.json；Top10產物為 D:\TWStockData\program_data\tw_nextday_top10。模型舊training refs只作不可變provenance，不讀舊training原文。

命令：app.py predict --no-open 或 app.py check。只有必要隔離QA允許 --c0-model、--c0-forward-db、--namespace-root；模型／帳冊／輸出override均標記QA，不是正式default資料來源。舊profile、校正／週末工作、舊input-manifest資料覆寫已退出入口。
