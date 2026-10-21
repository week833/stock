# 外部股票分析 GitHub 來源清單

共 **37 個 primary 股票研究來源**，已依用途分為五類；其中 manifest 標記 **3 個 core
default_install**（FinMind、daily_stock_analysis、twstock），另有 **2 個 unique legacy
compatibility sources** 與 **10 個 AI／Agent 研究來源**（不在 manifest）。每個正式股票來源在
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

## Legacy compatibility 來源（2 個 legacy＋1 筆 alias 紀錄）

2 個 unique legacy repository 由 `DOWNLOAD_LEGACY_SOURCES.cmd` 下載；manifest 另以 `legacy_aliases`
記錄 primary `twstock` 為舊流程的共用位置，但下載器不讀取此紀錄，不會二次 clone/pull，也不做
identity 檢查。2 個 unique legacy 的目的只是舊流程相容，不屬於 primary 研究清單；表中 `twstock`
本身是 primary core 來源，只為對照舊流程的共用位置而列出：

| Repository | 本機 clone 位置 |
|---|---|
| `mlouielu/twstock` | `repos/taiwan_market_data/twstock/` |
| `william911530-cmyk/python-stock-radar-` | `repos/legacy_compat/python-stock-radar-/` |
| `k66inthesky/TW-stock` | `repos/legacy_compat/TW-stock/` |

## AI／Agent 與開發方法研究來源（10）

| Repository | 本機 clone 位置 |
|---|---|
| `week833/finmind-twstock-research-skill` | `repos/ai_agent_tools/finmind-twstock-research-skill/` |
| `week833/stock-mitake-ui-reference` | `repos/ai_agent_tools/stock-mitake-ui-reference/` |
| `multica-ai/andrej-karpathy-skills` | `repos/ai_agent_tools/andrej-karpathy-skills/` |
| `AugustusW/audio-tldr-skill` | `repos/ai_agent_tools/audio-tldr-skill/` |
| `anthropics/claude-code` | `repos/ai_agent_tools/claude-code/` |
| `joshchaotang/claude-code-i18n` | `repos/ai_agent_tools/claude-code-i18n/` |
| `Raymondhou0917/claude-code-resources` | `repos/ai_agent_tools/claude-code-resources/` |
| `allenloves/de-ai-tone` | `repos/ai_agent_tools/de-ai-tone/` |
| `lopopolo/harness-engineering` | `repos/ai_agent_tools/harness-engineering/` |
| `mattpocock/skills` | `repos/ai_agent_tools/skills/` |

AI／Agent 來源只作方法研究，不直接匯入正式台股分析；它們不在 `source_manifest_v1.json`，
`DOWNLOAD_STOCK_SOURCES.cmd`／`DOWNLOAD_LEGACY_SOURCES.cmd` 都不會 clone，清單以
[`sources/ai_agent_tools/README.md`](../sources/ai_agent_tools/README.md) 為準。

## 操作方式

來源清單是 `scripts/sources/source_manifest_v1.json`（39 筆：37 primary＋2 legacy，另有 1 筆
`legacy_aliases`）。每項來源記錄 id、scope、default_install、分類、相對 target、URL、expected_branch、
estimated_bytes、用途與授權提示。下載器 `scripts/sources/source_manager.ps1` 目前只使用 scope、
default_install、target 與 URL；`generation`、`expected_branch`、`estimated_bytes` 與 `legacy_aliases`
只是紀錄，不會被讀取或檢查，也沒有 SHA-256 綁定或 duplicate id／target／URL 檢查。target 必須是
相對路徑且解析後仍在 `repos\` 內，否則停止。legacy 來源標為 historical/education optional，不是
runtime/formal 依賴。

主要與 legacy 來源正式位置固定為 `D:\stock\GitHub\repos`（`clone_stock_analysis_repos.cmd` 以固定路徑
呼叫 `source_manager.ps1 -Root "D:\stock\GitHub"`）。`INSTALL_D_STOCK_ENV_FULL.cmd` 只在環境安裝成功
（含本專案標記檢查）後，才呼叫固定路徑的 `D:\stock\GitHub\DOWNLOAD_STOCK_SOURCES.cmd`（只處理 3 個 core）。
從其他資料夾啟動的 package 不會讓來源腳本改寫呼叫端複本。

唯讀預覽（列出缺少的 target 與既有目錄），不建立目錄、不 clone、不 pull：

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

`DOWNLOAD_STOCK_SOURCES.cmd` 無參數只處理 3 個 core；`--all` 處理 37 個 primary。`--check`、
`--dry-run` 與舊參數 `--verify` 目前行為相同且只讀：缺少的 target 顯示 `[PLAN] Missing`，既有目錄
顯示 `[SKIP]`，缺少來源不會使結果失敗，也不檢查 origin、branch 或 identity。`DOWNLOAD_LEGACY_SOURCES.cmd`
無參數只處理 2 個 unique legacy；manifest 的 `twstock` alias 紀錄不被讀取，不會二次 clone/pull。

下載只針對缺少的 target：以 `git clone --depth 1`（上游預設分支，不指定 `--branch`）先 clone 到同一
分類目錄下的暫存資料夾 `.stock-source-pending-<GUID>`，成功後再改名為正式 target；失敗時保留暫存
資料夾供檢查，不覆蓋既有 target。clone 後不另檢查 origin、branch 或 HEAD。寫入前確認 target 留在
`repos\` 內，且既有上層目錄不是 reparse point（junction）。找不到 Git 時會嘗試以 winget 安裝 Git。

target 已是目錄時一律顯示 `[SKIP] 既有路徑，未下載/未更新`，不 pull、不更新，也不檢查 Git 狀態；
target 是檔案時停止。下載器不估算磁碟空間，也不寫入 provenance 紀錄檔。

`--dry-run` 明確等價於 `--check`，兩者均為唯讀模式，不建立資料夾、不 clone、不 pull、不寫 config、
不修改 User env，也不建立 junction。legacy 路徑與 junction 不由來源下載器修復；請另外
執行 `REPAIR_STOCK_PATHS.cmd --check`，確認後才可明確使用 `--apply --confirm`。

## 來源與正式分析的邊界

這 37 個 primary、2 個 unique legacy（manifest 另記錄 1 個共享 alias）及 10 個 AI／Agent 來源只供
方法、資料與工具研究；AI／Agent 來源是 optional，不屬於 installer manifest。外部
repository 的推薦名單、模型或執行結果不會直接匯入正式台股分析。此來源清單與下載不等於
第三方策略的回測、風險或盈利證明。

> 第三方原始碼不會提交至本 repository；請依各來源授權條款使用。
