# 外部股票分析 GitHub 來源清單

共 **37 個 primary 股票研究來源**，已依用途分為五類；其中 manifest 標記 **3 個 core
default_install**（FinMind、daily_stock_analysis、twstock），另有 **2 個 unique legacy
compatibility sources** 與 **9 個 AI／Agent 研究來源**。每個正式股票來源在
`sources/` 下都有獨立資料夾；legacy 與 AI 不增加 primary 計數。

| 分類 | 數量 | Repository | 本機 clone 位置 |
|---|---:|---|---|
| 台灣市場資料與股票分析來源 | 7 | `FinMind/FinMind` | `repos/taiwan_market_data/FinMind/` |
|  |  | `ZhuLinsen/daily_stock_analysis` | `repos/taiwan_market_data/daily_stock_analysis/` |
|  |  | `kevin801221/stock-strategies-only` | `repos/taiwan_market_data/stock-strategies-only/` |
|  |  | `sacahan/CasualMarket` | `repos/taiwan_market_data/CasualMarket/` |
|  |  | `mlouielu/twstock` | `repos/taiwan_market_data/twstock/` |
|  |  | `paullo0106/stocktw` | `repos/taiwan_market_data/stocktw/` |
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

## Legacy compatibility identities（3 identities）

其中 2 個是 unique legacy repository，由 `DOWNLOAD_LEGACY_SOURCES.cmd` 下載；第 3 個是
primary `twstock` 的共享 alias，只做 identity 檢查，不會二次 clone/pull。它們的目的只是
舊流程相容，不屬於 primary 研究清單：

| Repository | 本機 clone 位置 |
|---|---|
| `mlouielu/twstock` | `repos/taiwan_market_data/twstock/` |
| `william911530-cmyk/python-stock-radar-` | `repos/legacy_compat/python-stock-radar-/` |
| `k66inthesky/TW-stock` | `repos/legacy_compat/TW-stock/` |

## AI／Agent 與開發方法研究來源

| Repository | 本機 clone 位置 |
|---|---|
| `multica-ai/andrej-karpathy-skills` | `repos/ai_agent_tools/andrej-karpathy-skills/` |
| `AugustusW/audio-tldr-skill` | `repos/ai_agent_tools/audio-tldr-skill/` |
| `anthropics/claude-code` | `repos/ai_agent_tools/claude-code/` |
| `joshchaotang/claude-code-i18n` | `repos/ai_agent_tools/claude-code-i18n/` |
| `Raymondhou0917/claude-code-resources` | `repos/ai_agent_tools/claude-code-resources/` |
| `allenloves/de-ai-tone` | `repos/ai_agent_tools/de-ai-tone/` |
| `lopopolo/harness-engineering` | `repos/ai_agent_tools/harness-engineering/` |
| `mattpocock/skills` | `repos/ai_agent_tools/skills/` |
| `week833/stock-mitake-ui-reference` | `repos/ai_agent_tools/stock-mitake-ui-reference/` |

AI／Agent 來源只作方法研究，不直接匯入正式台股分析。

## 操作方式

下載器契約由 `scripts/sources/source_manifest_v1.json` 綁定 generation 與 SHA-256；每項來源
都有唯一 id、分類、相對 target、URL、預期 branch、estimated bytes、用途與授權提示。manifest
拒絕 duplicate id/target/normalized URL+target、型別錯誤與 path traversal。LightGBM 預期 branch
為 `main`；legacy 來源標為 historical/education optional，不是 runtime/formal 依賴。

主要與 legacy 來源正式位置固定為 `D:\stock\GitHub\repos`。下載與驗證是獨立階段；
完整安裝器只在核心環境成功且 fixed-root identity 通過後，才呼叫固定 root 的來源腳本。
從其他資料夾啟動的 package 不會讓來源腳本改寫呼叫端複本。

只檢查既有來源目錄，不建立目錄、不 clone、不 pull：

```cmd
DOWNLOAD_STOCK_SOURCES.cmd --check
DOWNLOAD_STOCK_SOURCES.cmd --check --all
DOWNLOAD_STOCK_SOURCES.cmd --dry-run
DOWNLOAD_STOCK_SOURCES.cmd --dry-run --all
DOWNLOAD_LEGACY_SOURCES.cmd --check
DOWNLOAD_LEGACY_SOURCES.cmd --dry-run
```

正式下載（明確選擇後才執行）使用：

```cmd
DOWNLOAD_STOCK_SOURCES.cmd
DOWNLOAD_LEGACY_SOURCES.cmd
```

`DOWNLOAD_STOCK_SOURCES.cmd` 無參數只處理 3 個 core；`--all` 處理 37 個 primary；`--check` 與
`--dry-run` 完全等價且只讀；`--verify`
為嚴格 missing/identity/branch gate（可加 `--all`）。`DOWNLOAD_LEGACY_SOURCES.cmd` 無參數只處理
2 個 unique legacy，並驗證共享的 primary `twstock` alias；不會二次 clone/pull。legacy `--check`
純唯讀，`--verify` 為嚴格 gate。

新 clone 一律使用 `--branch <expected> --single-branch --depth 1`，暫存於 canonical target 的
sibling，驗證 origin/branch/HEAD、real `.git` 與 non-reparse scope 後才 atomic rename；失敗保留
暫存目錄供 review，不覆蓋既有 target。既有 clean clone 僅 `pull --ff-only origin <expected>`，
dirty repository skip；origin/branch mismatch、非 Git 目標、reparse 或外部路徑 fail-closed。
下載前只對 missing 項估算 bytes 並加至少 30% margin；既有 clean source 不重複要求全量空間。
成功與既有驗證會寫入 `repos/.source_provenance.json`（relative target、URL、branch、HEAD、UTC
observed_at，不含 secrets），check/verify 零寫且 ledger 與 manifest generation/sha 綁定。

主要與 legacy 腳本在既有 repository 為 clean 且 normalized origin identity 符合時才允許
`git pull --ff-only`；dirty repository 會 skip，origin 不符或非 Git 目標會 fail-closed
並保留原資料。新 clone 先放在 canonical scope 內的暫存 sibling，完成後再驗證 origin，
最後以原子改名完成；clone 失敗或驗證失敗時不會覆蓋既有 target。

`--dry-run` 明確等價於 `--check`，兩者均為唯讀模式，不建立資料夾、不 clone、不 pull、不寫 config、
不修改 User env，也不建立 junction。legacy 路徑與 junction 不由來源下載器修復；請另外
執行 `REPAIR_STOCK_PATHS.cmd --check`，確認後才可明確使用 `--apply --confirm`。

## 來源與正式分析的邊界

這 37 個 primary、2 個 unique legacy（加上 1 個共享 alias）及 9 個 AI／Agent 來源只供
方法、資料與工具研究；AI／Agent 來源是 optional，不屬於 installer manifest。外部
repository 的推薦名單、模型或執行結果不會直接匯入正式台股分析。此來源驗證不等於
第三方策略的回測、風險或盈利證明。

> 第三方原始碼不會提交至本 repository；請依各來源授權條款使用。
