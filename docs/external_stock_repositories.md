# 外部股票分析 GitHub 來源清單

共 **36 個**來源，已依用途分為五類；每個來源在 `sources/` 下都有獨立資料夾。

> Scope note: this origin/main installer catalog remains 36 primary sources (6 + 2 + 12 + 6 + 10). The separate 2026-09-27 local workspace inventory has 124 physical external worktrees (49 canonical + 75 direct-root); it includes the local `stocktw` checkout and AI/reference repositories such as the FinMind research skill, neither of which is added to this 36-primary installer list. See the [function catalog](./repository_function_catalog.md) and [duplicate matrix](./repository_duplicate_matrix.md).

| 分類 | 數量 | Repository | 本機 clone 位置 |
|---|---:|---|---|
| 台股與既有股票分析來源 | 6 | `FinMind/FinMind` | `external_repos/01_taiwan_stock_data/FinMind/` |
|  |  | `ZhuLinsen/daily_stock_analysis` | `external_repos/01_taiwan_stock_data/daily_stock_analysis/` |
|  |  | `kevin801221/stock-strategies-only` | `external_repos/01_taiwan_stock_data/stock-strategies-only/` |
|  |  | `sacahan/CasualMarket` | `external_repos/01_taiwan_stock_data/CasualMarket/` |
|  |  | `mlouielu/twstock` | `external_repos/01_taiwan_stock_data/twstock/` |
|  |  | `victorgau/PyConTW2018Tutorial` | `external_repos/01_taiwan_stock_data/PyConTW2018Tutorial/` |
| 金融資料與市場研究平台 | 2 | `ranaroussi/yfinance` | `external_repos/02_market_data_platforms/yfinance/` |
|  |  | `OpenBB-finance/OpenBB` | `external_repos/02_market_data_platforms/OpenBB/` |
| AI、機器學習與時間序列預測 | 12 | `microsoft/qlib` | `external_repos/03_ai_ml_forecasting/qlib/` |
|  |  | `AI4Finance-Foundation/FinRL` | `external_repos/03_ai_ml_forecasting/FinRL/` |
|  |  | `unit8co/darts` | `external_repos/03_ai_ml_forecasting/darts/` |
|  |  | `Nixtla/mlforecast` | `external_repos/03_ai_ml_forecasting/mlforecast/` |
|  |  | `Nixtla/neuralforecast` | `external_repos/03_ai_ml_forecasting/neuralforecast/` |
|  |  | `Nixtla/statsforecast` | `external_repos/03_ai_ml_forecasting/statsforecast/` |
|  |  | `lightgbm-org/LightGBM` | `external_repos/03_ai_ml_forecasting/LightGBM/` |
|  |  | `dmlc/xgboost` | `external_repos/03_ai_ml_forecasting/xgboost/` |
|  |  | `catboost/catboost` | `external_repos/03_ai_ml_forecasting/catboost/` |
|  |  | `optuna/optuna` | `external_repos/03_ai_ml_forecasting/optuna/` |
|  |  | `shap/shap` | `external_repos/03_ai_ml_forecasting/shap/` |
|  |  | `freqtrade/freqtrade` | `external_repos/03_ai_ml_forecasting/freqtrade/` |
| 回測引擎與策略測試 | 6 | `polakowo/vectorbt` | `external_repos/04_backtesting_engines/vectorbt/` |
|  |  | `kernc/backtesting.py` | `external_repos/04_backtesting_engines/backtesting-py/` |
|  |  | `QuantConnect/Lean` | `external_repos/04_backtesting_engines/Lean/` |
|  |  | `pmorissette/bt` | `external_repos/04_backtesting_engines/bt/` |
|  |  | `stefan-jansen/zipline-reloaded` | `external_repos/04_backtesting_engines/zipline-reloaded/` |
|  |  | `mementum/backtrader` | `external_repos/04_backtesting_engines/backtrader/` |
| 技術指標、因子、績效與投組風控 | 10 | `TA-Lib/ta-lib-python` | `external_repos/05_indicators_factors_portfolio/ta-lib-python/` |
|  |  | `bukosabino/ta` | `external_repos/05_indicators_factors_portfolio/ta/` |
|  |  | `stefan-jansen/alphalens-reloaded` | `external_repos/05_indicators_factors_portfolio/alphalens-reloaded/` |
|  |  | `ranaroussi/quantstats` | `external_repos/05_indicators_factors_portfolio/quantstats/` |
|  |  | `stefan-jansen/pyfolio-reloaded` | `external_repos/05_indicators_factors_portfolio/pyfolio-reloaded/` |
|  |  | `stefan-jansen/empyrical-reloaded` | `external_repos/05_indicators_factors_portfolio/empyrical-reloaded/` |
|  |  | `pmorissette/ffn` | `external_repos/05_indicators_factors_portfolio/ffn/` |
|  |  | `PyPortfolio/PyPortfolioOpt` | `external_repos/05_indicators_factors_portfolio/PyPortfolioOpt/` |
|  |  | `dcajasn/Riskfolio-Lib` | `external_repos/05_indicators_factors_portfolio/Riskfolio-Lib/` |
|  |  | `skfolio/skfolio` | `external_repos/05_indicators_factors_portfolio/skfolio/` |

## 操作方式

```cmd
scripts\sources\clone_stock_analysis_repos.cmd
```

下載腳本會在來源已存在時執行 `git pull --ff-only`，不存在時執行 `git clone`。

> 第三方原始碼不會提交至本 repository；請依各來源授權條款使用。
