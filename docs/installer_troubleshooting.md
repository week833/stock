# 安裝程式閃退與舊安裝器說明

## 已修正的閃退問題

舊版 `INSTALL_D_STOCK_ENV.cmd` 在處理已存在且非 Git repository 的 `D:\stock` 時，包含一個不完整的批次判斷：

```cmd
if !ERRORLEVEL! NEQ (
```

缺少比較值 `0`，會觸發 CMD 語法錯誤，雙擊執行時可能只看到視窗短暫出現後關閉。

## 目前使用方式

下載完整專案後雙擊 `STOCK_SETUP_MANAGER.cmd`，選項 1 安裝環境，選項 6 做基本檢查。
也可直接使用 `INSTALL_D_STOCK_ENV.cmd`；FULL 入口會在環境安裝後接著下載 core 來源。
`/PREFLIGHT`、`/DRY-RUN`（及相容的 `/CHECK`）只顯示計畫。

安裝使用 `D:\stock\GitHub\.venv`。優先沿用有效 venv；需要新建時才找 Python 3.x >= 3.10，缺少時以 winget 安裝 Python 3.12。
wrapper 不強制 UAC；Git 只在 clone 缺少的專案或新來源時需要。已有來源目錄提示略過，不自動更新。
基本檢查只確認專案標記與固定 Python 啟動／版本；User PATH、研究來源與完整套件檢查不是其門檻。

## 電腦中找到的舊版安裝器

舊版 `install_tw_stock_ai_env.cmd` 類型的安裝器（以下是歷史行為，不是目前 fixed-root
契約）可以：

- 建立 `D:\stock`。
- 建立 `D:\stock\.venv`。
- 安裝 FinMind、twstock、pandas、yfinance 等核心套件。
- 下載少量早期參考 repository。

當時它未提供以下功能：

- clone 或更新 `week833/stock` 到 `D:\stock`。
- 設定 `STOCK_HOME`、`STOCK_PYTHON`、`STOCK_VENV` 等 Windows 使用者環境變數。
- 將 `D:\stock\.venv\Scripts` 加入使用者 PATH。
- 建立 `D:\Downloads\stock -> D:\stock` 相容 junction。
- 依目前分類結構下載及管理完整外部來源。
- 驗證 CMD、PowerShell、VS Code、排程器等應用程式使用環境。

目前請使用 `D:\stock\GitHub` 的選單與固定 venv；既有 `D:\stock\.venv` 屬於其他入口，不要替換。

## 常見問題

- 目標已有非空內容但缺少 `STOCK_SETUP_MANAGER.cmd` 或 `scripts\setup\install_d_stock_env.ps1`：停止並確認資料夾內容，不搬移、刪除或覆寫。
- 固定 Python 無法啟動：用選單 1 安裝環境；既有損壞的 venv 不會被自動刪除或重建。
- 新開的應用程式看不到環境：選單 4 重設 ENV/PATH，再重新開啟 CMD、PowerShell 或 VS Code。
- 舊路徑 target 尚未下載：修復會顯示 SKIP；外部研究來源為可選，不阻擋基本 Python 檢查。

需要查看安裝訊息，可先開 CMD，再執行完整專案內的 `INSTALL_D_STOCK_ENV.cmd`。新版不再建立舊的 TEMP 安裝 log。
