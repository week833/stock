# 外部股票分析 GitHub 來源清單

共 **36 個**來源，已依用途分為五類；每個來源在 `sources/` 下都有獨立資料夾。

| 分類 | 數量 | Repository | 本機 clone 位置 |
|---|---:|---|---|
| 台灣市場資料與股票分析來源 | 6 | `FinMind/FinMind` | `repos/taiwan_market_data/FinMind/` |
|  |  | `ZhuLinsen/daily_stock_analysis` | `repos/taiwan_market_data/daily_stock_analysis/` |
|  |  | `kevin801221/stock-strategies-only` | `repos/taiwan_market_data/stock-strategies-only/` |
|  |  | `sacahan/CasualMarket` | `repos/taiwan_market_data/CasualMarket/` |
|  |  | `mlouielu/twstock` | `repos/taiwan_market_data/twstock/` |
|  |  | `victorgau/PyConTW2018Tutorial` | `repos/taiwan_market_data/PyConTW2018Tutorial/` |
| 全球市場資料與研究平台 | 2 | `ranaroussi/yfinance` | `repos/global_market_data/yfinance/` |
|  |  | `OpenBB-finance/OpenBB` | `repos/global_market_data/OpenBB/` |
| 機器學習與時間序列預測 | 12 | `microsoft/qlib` | `repos/machine_learning_forecasting/qlib/` |
|  |  | `AI4Finance-Foundation/FinRL` | `repos/machine_learning_forecasting/FinRL/` |
|  |  | `unit8co/darts` | `repos/machine_learning_forecasting/darts/` |
|  |  | `Nixtla/mlforecast` | `repos/machine_learning_forecasting/mlforecast/` |
|  |  | `Nixtla/neuralforecast` | `repos/machine_learning_forecasting/neuralforecast/` |
|  |  | `Nixtla/statsforecast` | `repos/machine_learning_forecasting/statsforecast/` |
|  |  | `lightgbm-org/LightGBM` | `repos/machine_learning_forecasting/LightGBM/` |
|  |  | `dmlc/xgboost` | `repos/machine_learning_forecasting/xgboost/` |
|  |  | `catboost/catboost` | `repos/machine_learning_forecasting/catboost/` |
|  |  | `optuna/optuna` | `repos/machine_learning_forecasting/optuna/` |
|  |  | `shap/shap` | `repos/machine_learning_forecasting/shap/` |
|  |  | `freqtrade/freqtrade` | `repos/machine_learning_forecasting/freqtrade/` |
| 回測引擎與策略測試 | 6 | `polakowo/vectorbt` | `repos/backtesting_engines/vectorbt/` |
|  |  | `kernc/backtesting.py` | `repos/backtesting_engines/backtesting-py/` |
|  |  | `QuantConnect/Lean` | `repos/backtesting_engines/Lean/` |
|  |  | `pmorissette/bt` | `repos/backtesting_engines/bt/` |
|  |  | `stefan-jansen/zipline-reloaded` | `repos/backtesting_engines/zipline-reloaded/` |
|  |  | `mementum/backtrader` | `repos/backtesting_engines/backtrader/` |
| 量化工具、因子、績效與投組風控 | 10 | `TA-Lib/ta-lib-python` | `repos/quant_portfolio_risk/ta-lib-python/` |
|  |  | `bukosabino/ta` | `repos/quant_portfolio_risk/ta/` |
|  |  | `stefan-jansen/alphalens-reloaded` | `repos/quant_portfolio_risk/alphalens-reloaded/` |
|  |  | `ranaroussi/quantstats` | `repos/quant_portfolio_risk/quantstats/` |
|  |  | `stefan-jansen/pyfolio-reloaded` | `repos/quant_portfolio_risk/pyfolio-reloaded/` |
|  |  | `stefan-jansen/empyrical-reloaded` | `repos/quant_portfolio_risk/empyrical-reloaded/` |
|  |  | `pmorissette/ffn` | `repos/quant_portfolio_risk/ffn/` |
|  |  | `PyPortfolio/PyPortfolioOpt` | `repos/quant_portfolio_risk/PyPortfolioOpt/` |
|  |  | `dcajasn/Riskfolio-Lib` | `repos/quant_portfolio_risk/Riskfolio-Lib/` |
|  |  | `skfolio/skfolio` | `repos/quant_portfolio_risk/skfolio/` |

## 操作方式

```cmd
scripts\sources\clone_stock_analysis_repos.cmd
```

下載腳本會在來源已存在時執行 `git pull --ff-only`，不存在時執行 `git clone`。

> 第三方原始碼不會提交至本 repository；請依各來源授權條款使用。
