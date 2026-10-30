# 環境與研究來源管理

本資料夾是獨立的環境管理工具，不是全市場分析主程式。它只管理 `D:\stock\.venv` 與 Windows 使用者環境變數；`sources` 與 `docs\external_stock_repositories.md` 只保存外部 GitHub 研究來源的分類說明，本資料夾沒有下載或更新 `D:\stock\GitHub\repos` 的程式。

## 入口

- `STOCK_SETUP_MANAGER.cmd`：互動式管理器。
- `INSTALL_D_STOCK_ENV.cmd`：建立或更新 Python 環境。
- `INSTALL_D_STOCK_ENV_FULL.cmd`：以 `-Full` 呼叫同一安裝核心；目前 `-Full` 不另做下載或檢查，結果與 `INSTALL_D_STOCK_ENV.cmd` 相同。
- `CONFIGURE_WINDOWS_ENV.cmd`：只更新 Windows 使用者環境變數與 PATH。
- `VERIFY_STOCK_ENV.cmd`：檢查程式、虛擬環境與必要套件。

## 依賴

完整環境只使用本資料夾的 `requirements.txt`，根目錄不保留相容副本。安裝器完成套件安裝後
會執行 `python -m pip check`，若套件版本互相衝突則整體安裝失敗。單股工具（`programs\single_stock_analysis`）也只使用
`D:\stock\.venv`，依賴由本資料夾的安裝器安裝；舊的輕量清單 `requirements_single_stock_3m.txt` 與
`INSTALL_SINGLE_STOCK_3M_DEPS.cmd` 已於 2026-10-12 退役並移到 `programs\single_stock_analysis\tools\_retired\`，請勿使用。
`programs\single_stock_analysis` 於 2026-10-27 納入 Git（commit `41f2cbc`）時未包含 `tools\_retired` 與 `tests\_retired`，這些檔案不在本 repository 追蹤中，也不登錄於 `programs\STABLE_PROGRAM_PATHS.txt`；2026-10-30 本機 `VERIFY_STABLE_PROGRAM_PATHS.cmd`（exit 0）確認這兩個資料夾已不在 `D:\stock`。`programs\shared` 與 `programs\telegram_report_delivery` 的 `tests\_retired` 已納入 Git 並已登錄，不受影響。

## 安全界線

環境安裝器不會 clone、pull、搬移、刪除或覆蓋 `D:\stock` 的正式分析程式，也不會建立 `D:\Downloads\stock` 相容連結。本資料夾沒有研究來源下載器；舊的根目錄 `DOWNLOAD_STOCK_SOURCES.cmd`、`DOWNLOAD_LEGACY_SOURCES.cmd` 與 `scripts` 已退役，`programs\VERIFY_STABLE_PROGRAM_PATHS.cmd` 會把它們重新出現在 `D:\stock` 根目錄視為錯誤。正式預測程式不直接匯入外部推薦名單；`gh` CLI 未安裝不影響預測或本機 Git repository 使用。

外部來源清單見 `docs\external_stock_repositories.md`，分類說明見 `sources`。
