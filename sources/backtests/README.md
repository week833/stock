# 回測引擎與策略測試

> 此資料夾是舊分類名稱 `backtests` 的說明副本；現行分類為 [`backtesting_engines`](../backtesting_engines/)（6 個來源）。`repos/backtests` 是可選的相容 junction，指向 `repos/backtesting_engines`：只有明示執行 `REPAIR_STOCK_PATHS.cmd --apply --confirm` 且當時 target 已存在才會建立，安裝與基本檢查不會自動建立；它不是實體下載位置。

此分類共有 **6** 個外部來源。每個來源均有獨立資料夾，避免文件、程式與下載內容互相混淆。

| 資料夾 | 原始 repository | 分支 | 用途 |
|---|---|---:|---|
| [vectorbt](./vectorbt/) | `polakowo/vectorbt` | `master` | 高速向量化多標的、多參數策略回測。 |
| [backtesting-py](./backtesting-py/) | `kernc/backtesting.py` | `master` | 輕量策略回測、進出場驗證與參數最佳化。 |
| [Lean](./Lean/) | `QuantConnect/Lean` | `master` | 專業事件驅動回測、最佳化與實盤交易引擎。 |
| [bt](./bt/) | `pmorissette/bt` | `master` | 多資產配置、輪動與模組化組合策略回測。 |
| [zipline-reloaded](./zipline-reloaded/) | `stefan-jansen/zipline-reloaded` | `main` | Zipline 維護版與事件驅動回測流程。 |
| [backtrader](./backtrader/) | `mementum/backtrader` | `master` | 經典 Python 回測框架；整合時需注意 GPL-3.0 授權。 |

實際 clone 的原始碼會存放在：

```txt
repos/backtesting_engines/
```

`repos/backtests/` 只用於可選的相容 junction；請勿直接 clone 到該路徑，否則實體資料夾會使相容路徑修復停止。

`repos/` 已由 `.gitignore` 排除，不會誤提交外部原始碼。
