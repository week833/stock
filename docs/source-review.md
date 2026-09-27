# 股票來源／歷史資料／待評估刪除清單

查證日：**2026-09-18**。

本文件集中記錄來源的用途、歷史背景與保留需求。**目前僅完成分類與評估記錄，尚未刪除來源、停用下載或搬動目錄。**「待評估」不等於已失效；是否保留仍需配合實際使用需求判斷。

相關索引：[GitHub 專案分類](./github-catalog.md) · [外部來源分類](../sources/README.md) · [完整來源清單](./external_stock_repositories.md)

## 盤點範圍與判讀方式

- 主來源清單、`sources/` 來源 README 與主下載腳本均為 **36 個來源**，彼此一致。
- legacy 下載另有 **2 個獨有來源**：`python-stock-radar-`、`TW-stock`；其中也列出的 `twstock` 與主腳本共用同一下載位置。因此合計為 **38 個獨有上游**，不含本文件另列的 `ai-trader` fork 與三竹 UI 參考庫。
- 本次查得上述 38 個上游均為 `archived: false`，沒有整庫已封存的證據。更新較少、用途重疊或名稱含 legacy，均不足以單獨判定專案失效。
- 下列日期如標示「最後 commit」，均來自已查詢的**預設分支 commit**，採 UTC 日期；不將 repository 的 `pushed_at` 或 `updated_at` 當成最後 commit 日期。

## 優先評估：歷史教材與文件

| 項目 | 具體證據 | 建議處置 |
|---|---|---|
| `victorgau/PyConTW2018Tutorial` | [官方 README](https://github.com/victorgau/PyConTW2018Tutorial/blob/master/README.md) 明確為 PyCon TW 2018 股票分析課程，含當時的 pandas-datareader 相容修補與舊安裝指引；預設分支[最後 commit](https://github.com/victorgau/PyConTW2018Tutorial/commit/445e660f4f1ae693826c7a53f72d67d665e0b132) 為 **2018-05-25**。現有索引位於 `sources/01_taiwan_stock_data/PyConTW2018Tutorial/`。 | **歷史教材／待評估刪除**。優先確認是否仍需下載整份教材；可保留索引作教學與研究參考。未據此宣稱教材全數無法運作。 |
| [`docs/legacy_compatibility.md`](./legacy_compatibility.md) | 舊文件以 `D:\stock` 為正式位置，與[目前 README](../README.md) 的 `D:\stock\GitHub` 配置及不搬整個目錄的說明不同。 | **歷史文件／待更新**。保留舊路徑相容背景並標註歷史版本，以目前 README 為操作入口；這是文件需更新，不是整個 `stock` 過時。 |

## 保留需求待確認

| 項目 | 具體證據 | 建議處置 |
|---|---|---|
| `william911530-cmyk/python-stock-radar-` | [官方 repository](https://github.com/william911530-cmyk/python-stock-radar-) 描述為手動刷新的台股雷達；根目錄有五個應用檔案、沒有 README。預設分支[最後 commit](https://github.com/william911530-cmyk/python-stock-radar-/commit/d512bbcf8014e355c1be6f0dfb7cf659474a17eb) 為 **2026-05-17**。legacy 下載位置為 `external_repos/00_legacy_compat/python-stock-radar-/`。 | 確認是否仍供舊程式或本機介面使用，再評估保留。它是本機相容清單中的來源，沒有上游已過時或停維護的明確證據。 |
| `k66inthesky/TW-stock` | [官方 README](https://github.com/k66inthesky/TW-stock/blob/main/README.md) 提供十年以上台股上市股資料與爬蟲，明示不支援上櫃股，並記載 2025 年持續更新；預設分支[最後 commit](https://github.com/k66inthesky/TW-stock/commit/52b8f467b4b4e298f8009161dbc7e7acff6cfa41) 為 **2025-03-02**。legacy 下載位置為 `external_repos/00_legacy_compat/TW-stock/`。 | 與其他台股資料工具有部分用途重疊，但歷史資料可能仍有研究價值。先確認資料及舊路徑需求，再評估保留。 |
| [`week833/ai-trader`](https://github.com/week833/ai-trader) fork | 查證時僅有 `main` 分支，與[上游 HKUDS/AI-Trader](https://github.com/HKUDS/AI-Trader) 的 `main` 同為 SHA `d03ff6c056b32ced735adf7c19ed8175adb1c8df`；可對照 [fork commit](https://github.com/week833/ai-trader/commit/d03ff6c056b32ced735adf7c19ed8175adb1c8df) 與[上游 commit](https://github.com/HKUDS/AI-Trader/commit/d03ff6c056b32ced735adf7c19ed8175adb1c8df)。 | **重複 fork／保留需求待確認**。已知分支沒有相對該上游提交的差異，但使用者本機用途尚未確認；不能把重複 fork 解讀成上游過時。 |

## 保留研究參考

| 項目 | 官方證據與保留理由 |
|---|---|
| `mementum/backtrader` | [官方 README](https://github.com/mementum/backtrader/blob/master/README.rst) 中的 deprecated **只指 pyfolio integration**，不適用於整個回測框架；整庫未封存。預設分支[最後 commit](https://github.com/mementum/backtrader/commit/b853d7c90b6721476eb5a5ea3135224e33db1f14) 為 **2023-04-19**。可保留作經典回測參考，若需精簡下載再依實際使用評估。 |
| `AI4Finance-Foundation/FinRL` | [官方 README](https://github.com/AI4Finance-Foundation/FinRL/blob/master/README.md) 將新架構與 production 開發指向 FinRL-X／FinRL-Trading，同時保留 FinRL 作教育、benchmark 與研究原型，並仍稱由基金會維護。標示新世代指引即可，不將 FinRL 列為已停維護。 |
| [`week833/stock-mitake-ui-reference`](https://github.com/week833/stock-mitake-ui-reference) | [專案 README](https://github.com/week833/stock-mitake-ui-reference/blob/codex/mitake-ui-reference-initial/README.md) 是三竹 UI 參考資料；目前沒有已被另一專案完整取代的證據。保留作介面研究參考，並可搭配本庫的[三竹功能目錄](./mitake/README.md)。 |

## 正確用途補註

以下項目主要需要更精確的用途標籤，不列為過時刪除候選。

| 來源 | 建議標示 |
|---|---|
| [Freqtrade](https://github.com/freqtrade/freqtrade/blob/develop/README.md) | **加密貨幣交易系統／FreqAI 架構參考**。含回測、資金管理及機器學習最佳化；作為股票專案的跨市場架構參考，並非股票資料來源。 |
| [CasualMarket](https://github.com/sacahan/CasualMarket/blob/main/README.md) | **台股 MCP／資料查詢與模擬交易**。官方描述涵蓋行情、財務、市場資訊，交易功能為模擬用途。 |
| [stock-strategies-only](https://github.com/kevin801221/stock-strategies-only/blob/main/README.md) | **台股策略／自動選股應用**。含 FinMind、Telegram、Google Sheets 與策略介面；README 仍列嚴謹回測機制為 TODO，不宜標成成熟通用回測引擎。 |
| [daily_stock_analysis](https://github.com/ZhuLinsen/daily_stock_analysis/blob/main/README.md) | **多市場 AI 股票分析應用**。現行 README 明示支援 A 股、港股、美股、日股、韓股及台股；保留於現有寬分類合理。 |
| [Optuna](https://github.com/optuna/optuna/blob/master/README.md)、[SHAP](https://github.com/shap/shap/blob/main/README.md) | **機器學習支援工具**：Optuna 用於調參，SHAP 用於解釋模型輸出；可保留在 AI／ML 分類。 |
| [LightGBM](https://github.com/lightgbm-org/LightGBM/blob/main/README.md)、[XGBoost](https://github.com/dmlc/xgboost/blob/master/README.md)、[CatBoost](https://github.com/catboost/catboost/blob/master/README.md) | **通用機器學習模型庫**。可供股票研究使用，但不是現成股票分析系統。 |

## legacy 下載與重複來源說明

[主下載腳本](../scripts/sources/clone_stock_analysis_repos.cmd) 使用 `stefan-jansen/zipline-reloaded` 與 `stefan-jansen/pyfolio-reloaded`；[legacy 下載腳本](../scripts/sources/clone_legacy_compat_repos.cmd)沒有另外下載原 Quantopian zipline／pyfolio。舊平面目錄由[相容路徑腳本](../scripts/compat/repair_legacy_paths.cmd)連到分類目錄，不能把路徑別名當成另一份來源。

[Zipline Reloaded](https://github.com/stefan-jansen/zipline-reloaded/blob/main/README.md) 明示延續 Quantopian 關閉後的維護；`alphalens-reloaded`、`pyfolio-reloaded`、`empyrical-reloaded` 亦未封存，應保留現有研究用途。`voidful/tw_stocker` 則已被 legacy 下載腳本明確排除，僅保留可能既存目錄的相容路徑，不是目前自動下載的第 39 個上游。

+> 範圍註記：本文件的來源審查範圍是 36 個 primary 與 2 個 legacy identities，不是完整的本機外部 Git worktree 清冊。2026-09-27 snapshot 共 124 rows（49 canonical + 75 direct-root），詳見 [功能目錄](./repository_function_catalog.md) 與 [duplicate matrix](./repository_duplicate_matrix.md)。
