# 外部來源分類

完整的逐 repository 功能與索引狀態見
[../docs/repository_function_catalog.md](../docs/repository_function_catalog.md)。

- [taiwan_market_data — 台灣市場資料與股票分析來源](./taiwan_market_data/)（7 個來源；FinMind、daily_stock_analysis、twstock 為 core default）
- [global_market_data — 全球市場資料與研究平台](./global_market_data/)（2 個來源）
- [machine_learning_forecasting — 機器學習與時間序列預測](./machine_learning_forecasting/)（12 個來源）
- [backtesting_engines — 回測引擎與策略測試](./backtesting_engines/)（6 個來源）
- [quant_portfolio_risk — 量化工具、因子、績效與投組風控](./quant_portfolio_risk/)（10 個來源）
- [ai_agent_tools — AI、Agent 與開發方法研究](./ai_agent_tools/)（10 個來源：2 個股票相關、8 個一般 AI／Agent；不在 manifest）

股票研究來源均有自己的資料夾與 `README.md`；AI／Agent 來源集中由 `ai_agent_tools/README.md` 編目。實際原始碼由下載腳本或既有 clone 存放到相同分類的 `repos/` 路徑。所有外部來源均為 optional research，legacy/historical/education 來源不是 runtime 或 formal 依賴；來源 id、branch、bytes 與授權提示以 `../scripts/sources/source_manifest_v1.json` 為準（例外：`shap` 上游預設分支已是 `main`，manifest 仍記 `master`，見 [shap](./machine_learning_forecasting/shap/)）。
