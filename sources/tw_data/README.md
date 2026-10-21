# 台股與既有股票分析來源

> 此資料夾是舊分類名稱 `tw_data` 的說明副本；現行分類為 [`taiwan_market_data`](../taiwan_market_data/)（7 個來源，含 2026-10-07 同步進本 repository 的 `stocktw`）。`repos/tw_data` 是可選的相容 junction，指向 `repos/taiwan_market_data`：只有明示執行 `REPAIR_STOCK_PATHS.cmd --apply --confirm` 且當時 target 已存在才會建立，安裝與基本檢查不會自動建立；它不是實體下載位置。

此副本列出 **6** 個外部來源（不含 `stocktw`）。每個來源均有獨立資料夾，避免文件、程式與下載內容互相混淆。

| 資料夾 | 原始 repository | 分支 | 用途 |
|---|---|---:|---|
| [FinMind](./FinMind/) | `FinMind/FinMind` | `master` | 台股價量、法人、基本面、新聞、期貨與選擇權等金融資料來源。 |
| [daily_stock_analysis](./daily_stock_analysis/) | `ZhuLinsen/daily_stock_analysis` | `main` | 每日股票分析流程、報告產生與資料整理參考。 |
| [stock-strategies-only](./stock-strategies-only/) | `kevin801221/stock-strategies-only` | `main` | 選股策略、交易規則與策略檔案組織參考。 |
| [CasualMarket](./CasualMarket/) | `sacahan/CasualMarket` | `main` | 市場分析工具與資料處理架構參考。 |
| [twstock](./twstock/) | `mlouielu/twstock` | `dev` | 台灣股市價格擷取工具，可作為 TWSE/TPEX 資料備援。 |
| [PyConTW2018Tutorial](./PyConTW2018Tutorial/) | `victorgau/PyConTW2018Tutorial` | `master` | Python 金融資料分析與教學範例。 |

實際 clone 的原始碼會存放在：

```txt
repos/taiwan_market_data/
```

`repos/tw_data/` 只用於可選的相容 junction；請勿直接 clone 到該路徑，否則實體資料夾會使相容路徑修復停止。

`repos/` 已由 `.gitignore` 排除，不會誤提交外部原始碼。
