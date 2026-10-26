# 量化工具、因子、績效與投組風控

此分類共有 **10** 個外部來源。每個來源均有獨立資料夾，避免文件、程式與下載內容互相混淆。

| 資料夾 | 原始 repository | 分支 | 用途 |
|---|---|---:|---|
| [ta-lib-python](./ta-lib-python/) | `TA-Lib/ta-lib-python` | `master` | TA-Lib Python wrapper，涵蓋大量技術指標與 K 線型態。 |
| [ta](./ta/) | `bukosabino/ta` | `master` | 以 pandas/numpy 建立技術指標與特徵工程。 |
| [alphalens-reloaded](./alphalens-reloaded/) | `stefan-jansen/alphalens-reloaded` | `main` | Alpha 因子報酬、IC、換手率與分組分析。 |
| [quantstats](./quantstats/) | `ranaroussi/quantstats` | `main` | 勝率、Sharpe、回撤、績效圖與 HTML 報告。 |
| [pyfolio-reloaded](./pyfolio-reloaded/) | `stefan-jansen/pyfolio-reloaded` | `main` | 策略 tear sheet、風險圖與樣本外績效分析。 |
| [empyrical-reloaded](./empyrical-reloaded/) | `stefan-jansen/empyrical-reloaded` | `main` | Alpha、Beta、VaR、Sharpe、Sortino 與最大回撤計算。 |
| [ffn](./ffn/) | `pmorissette/ffn` | `master` | 報酬、權重與投組統計的輕量金融函式庫。 |
| [PyPortfolioOpt](./PyPortfolioOpt/) | `PyPortfolio/PyPortfolioOpt` | `main` | Efficient Frontier、Black-Litterman、HRP 等投組最佳化。 |
| [Riskfolio-Lib](./Riskfolio-Lib/) | `dcajasn/Riskfolio-Lib` | `master` | CVaR、回撤風險、風險平價、槓桿與換手率等進階風控。 |
| [skfolio](./skfolio/) | `skfolio/skfolio` | `main` | 投組最佳化、交叉驗證、HRP、Risk Budgeting 與壓力測試。 |

實際 clone 的原始碼會存放在：

```txt
repos/quant_portfolio_risk/
```

`repos/` 已由 `.gitignore` 排除，不會誤提交外部原始碼。
