# 環境與研究來源管理

本資料夾是獨立的環境管理工具，不是全市場分析主程式。它只管理 `D:\stock\.venv`、Windows 使用者環境變數，以及 `D:\stock\GitHub\repos` 的外部 GitHub 研究來源。

## 入口

- `STOCK_SETUP_MANAGER.cmd`：互動式管理器。
- `INSTALL_D_STOCK_ENV.cmd`：建立或更新 Python 環境。
- `INSTALL_D_STOCK_ENV_FULL.cmd`：更新環境並下載所有分類研究來源。
- `CONFIGURE_WINDOWS_ENV.cmd`：只更新 Windows 使用者環境變數與 PATH。
- `VERIFY_STOCK_ENV.cmd`：檢查程式、虛擬環境與必要套件。
- `DOWNLOAD_STOCK_SOURCES.cmd`：下載或更新分類研究來源。
- `DOWNLOAD_LEGACY_SOURCES.cmd`：下載選用的舊研究來源。

## 依賴

完整環境只使用本資料夾的 `requirements.txt`，根目錄不保留相容副本。安裝器完成套件安裝後
會執行 `python -m pip check`，若套件版本互相衝突則整體安裝失敗。單股工具（`programs\single_stock_analysis`）也只使用
`D:\stock\.venv`，依賴由本資料夾的安裝器安裝；舊的輕量清單 `requirements_single_stock_3m.txt` 與
`INSTALL_SINGLE_STOCK_3M_DEPS.cmd` 已於 2026-10-12 退役並移到 `programs\single_stock_analysis\tools\_retired\`，請勿使用。

## 安全界線

環境安裝器不會 clone、pull、搬移、刪除或覆蓋 `D:\stock` 的正式分析程式，也不會建立 `D:\Downloads\stock` 相容連結。若 `D:\stock\GitHub` 已安裝，下載器會直接沿用其 `repos`，不在根目錄重複下載。正式預測程式不直接匯入外部推薦名單；`gh` CLI 未安裝不影響預測或本機 Git repository 使用。

外部來源清單見 `docs\external_stock_repositories.md`，分類說明見 `sources`。
