# Codex 一行需求

根目錄 `AGENTS.md`、`PROGRAM_FILES.md` 與本文件保存固定入口、安全邊界及驗證規則。
通常只要說明目標與限制，不必重述整套流程。

## 最短寫法

```text
<動作> <對象>；<真正重要的限制>；<完成條件>。
```

| 需求 | 直接輸入 |
|---|---|
| 先分析 | `分析 <問題>，列出原因與計畫，等我說可以再修改。` |
| 直接修正 | `直接修正 <問題>；保留現有入口；只做必要驗證。` |
| 執行 MIX 全市場分析 | `執行 D:\MIX All\02_MIX_ALL正式報告.cmd；核對正式成品；失敗才附錯誤摘要。` |
| 更新 Central 共用資料 | `執行 D:\TWStockData\services\market_update\一鍵更新.cmd；確認 Central owner 與 receipt；明確說明網路與寫入結果。` |
| 驗證最新個股報告 | `驗證 D-stock 最新個股報告，不重跑分析，只回報錯誤與警告。` |
| 單股報告 | `分析 <股票代號>，產生三個月與次日開盤報告。` |
| Telegram 傳送 | `只傳送已驗證的 MIX 正式報告；不重跑分析；回報 manifest 與傳送狀態。` |
| 固定路徑盤點 | `盤點 programs 固定路徑；不修改、不暫存、不提交。` |
| Git 盤點 | `分類目前 Git 變更；不修改、不暫存、不提交。` |
| 清理舊內容 | `盤點 D:\stock 舊內容；逐檔分類；只把明確可刪除項目集中到隔離區；保留 Central、MIX、GitHub、.venv 與歷史證據。` |
| 提交發布 | `只提交本次已驗證變更並建立草稿 PR，排除其他變更。` |

## 常用限制

可直接接在句尾：`不要連網`、`不要重抓歷史`、`只改文件`、`不改模型`、
`只驗證不發布`、`先給計畫`、`保留未提交變更`、`只回覆結論`。

## 固定邊界

- 全市場分析由 `D:\MIX All` 負責；Central 更新由
  `D:\TWStockData\services\market_update` 的 owner 入口負責，兩者不可在 D-stock
  內另建副本。
- D-stock 現行分析範圍是 `programs\single_stock_analysis` 與
  `programs\telegram_report_delivery`；共用資料一律讀取
  `D:\TWStockData\shared_market_data`。
- 舊 `programs\full_market_analysis`、舊 `programs\daily_analysis_scheduler` 與舊
  更新入口均已退役，不得當成執行目標；`logs\daily_analysis_scheduler` 僅屬歷史
  分類，且 2026-08-30 live observation 已確認該路徑不存在，不是現行 scheduler 或執行目標。
- `D:\stock\_DELETE_OLD_CONTENT_20260830` 只是歷史隔離區名稱；2026-08-30 live
  observation 已確認該路徑不存在，不是入口或恢復來源。
- 2026-08-30 live observation：User PATH 中舊的
  `D:\stock\programs\full_market_analysis` segment 已移除 1 筆（35→34），target count
  為 0；Machine PATH 未變更，其餘 segment 順序與文字保留。
- 正式驗收以 `D:\TWStockData\services\acceptance\latest.json`、Central receipt
  與各系統 manifest 為準；`blocked`、`fail` 或缺失時必須照實回報。

預設回覆只有：結果、修改檔案、實際驗證、待決事項。
