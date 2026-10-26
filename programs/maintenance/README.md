# 安全清理工具（股票維護範圍）

本目錄提供「預覽優先、精確清單、漂移即停止」的清理工具。工具只處理明列的 A 類候選，不會把未知 C 類、正式成品或目前中央更新當成可刪除資料。

## 允許掃描的根目錄

- `D:\TWStockData\services\market_update\staging`：只接受有失敗 evidence、沒有正式 receipt、且不是 active run 的完整 Central run staging。
- `D:\TWStockData\program_data\dstock_daily`：只接受路徑或檔名明確含 `preview`、`tmp`、`qa`、`candidate`、`smoke` 的非正式檔案。
- `D:\MIX All`：同上，只接受明確暫存／候選標記。
- `D:\cowork`：同上；`premarket_daytrade_top10` 不在範圍內。

工具永遠排除 `D:\TWStockData\shared_market_data`、`D:\claude code\twmix`、獨立 Premarket／當沖程式、active Central run/staging、正式 receipt/evidence/pointer/manifest/report、Git tracked／dirty／untracked 檔案、`.git`、`.venv`／`venv`、`__pycache__`、`node_modules` 及未知候選。

## 預覽與產生計畫

預設只讀盤點，不寫 plan、不刪除任何檔案：

```powershell
pwsh -NoProfile -File D:\stock\programs\maintenance\PLAN_CLEANUP.ps1
```

如需保存計畫，輸出路徑必須在 `D:\stock\programs\maintenance\plans`（不可輸出到資料根）：

```powershell
pwsh -NoProfile -File D:\stock\programs\maintenance\PLAN_CLEANUP.ps1 `
  -WritePlan `
  -OutputPath D:\stock\programs\maintenance\plans\cleanup_plan_YYYYMMDDTHHMMSSZ.json
```

每個 file target 都保存絕對路徑、大小、SHA-256、Git tracking 狀態；directory target 另保存完整檔案清單與完整子目錄清單。計畫 hash 是排除 `plan_sha256` 欄位後的 canonical JSON SHA-256，套用時必須完全相符。

## 套用計畫

套用不會自動重試，也不接受模糊路徑或 glob。必須明確給出計畫及 `-ConfirmApply`：

```powershell
pwsh -NoProfile -File D:\stock\programs\maintenance\APPLY_CLEANUP.ps1 `
  -PlanPath D:\stock\programs\maintenance\plans\cleanup_plan_YYYYMMDDTHHMMSSZ.json `
  -ConfirmApply
```

套用前會一次性檢查：

1. 計畫 schema、版本、計畫 hash、allowlist 與 target 類別。
2. 所有 target 的絕對路徑仍位於 allowlist、不是根目錄、不是 reparse point，且不屬於 Git tracked、dirty 或 untracked 檔案。
3. active run JSON 的檔案 hash、run id、PID、status 沒有漂移，且目前沒有 running Central run 或 owner process。
4. `D:\TWStockData\services\acceptance\latest.json` 檔案 hash 沒有漂移，且狀態為 `complete/pass`、沒有 blocking failure。
5. 每個檔案的大小與 SHA-256 沒有漂移；每個 directory target 的實際檔案／子目錄清單與計畫完全一致。

任何一項不符都整批 fail-closed，不刪除任何 target，並在 `D:\stock\programs\maintenance\receipts` 寫入 blocked receipt。實際刪除只呼叫 `Remove-Item -LiteralPath`，不使用 broad glob 或遞迴未知刪除。目錄只有在計畫列出完整檔案／子目錄清單且刪除檔案後確定為空時才會移除。

每次套用都寫入 `stock-cleanup-receipt-v1`，記錄 `deleted`、`skipped`、`bytes_deleted`、每個刪除檔案的 SHA-256、active/acceptance 快照與 recoverability。刪除是不可逆檔案刪除，receipt 是可供稽核的恢復線索，不是假裝可還原的備份。

## 精確 duplicate／superseded 證據

exact-hash duplicate 與 damaged superseded 副本不會靠檔名猜測。若日後要納入，先在 maintenance 根下建立符合 `cleanup_proof_map.schema.json` 的 proof map，再以 `-ProofMapPath` 產生新 plan。工具會重新驗證 target、authority、hash、正式 pointer/manifest 與 damage evidence；任何證據缺失就列為 skipped。

## 最窄契約測試

目前只新增靜態安全邊界測試，尚未執行：

```powershell
pwsh -NoProfile -File D:\stock\programs\maintenance\tests\test_cleanup_contract.ps1
```

正式套用前仍需在 Central run 結束、receipt/evidence 完整、正式報告與 acceptance 重新通過後，重新執行 preview、審閱 target 清單，再由使用者明確執行 apply。當前 `market-20260824T172419Z-11ef089a63` 必須先排除，不得納入 plan。
