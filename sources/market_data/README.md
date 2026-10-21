# 金融資料與市場研究平台

> 此資料夾是舊分類名稱 `market_data` 的說明副本；現行分類為 [`global_market_data`](../global_market_data/)（2 個來源）。`repos/market_data` 是可選的相容 junction，指向 `repos/global_market_data`：只有明示執行 `REPAIR_STOCK_PATHS.cmd --apply --confirm` 且當時 target 已存在才會建立，安裝與基本檢查不會自動建立；它不是實體下載位置。

此分類共有 **2** 個外部來源。每個來源均有獨立資料夾，避免文件、程式與下載內容互相混淆。

| 資料夾 | 原始 repository | 分支 | 用途 |
|---|---|---:|---|
| [yfinance](./yfinance/) | `ranaroussi/yfinance` | `main` | Yahoo Finance 價量、公司、產業與國際市場資料來源。 |
| [OpenBB](./OpenBB/) | `OpenBB-finance/OpenBB` | `develop` | 金融資料整合、研究終端、API 與 AI agent 金融分析架構。 |

實際 clone 的原始碼會存放在：

```txt
repos/global_market_data/
```

`repos/market_data/` 只用於可選的相容 junction；請勿直接 clone 到該路徑，否則實體資料夾會使相容路徑修復停止。

`repos/` 已由 `.gitignore` 排除，不會誤提交外部原始碼。
