# 台股 AI 分析與量化研究工具庫

[GitHub專案分類總覽](docs/github-catalog.md) · [股票來源與歷史資料待評估清單](docs/source-review.md)

Windows 安全安裝位置固定為：

```text
D:\stock\GitHub
```

`D:\stock` 現在只視為共用資料夾。安裝器不會搬移、刪除、重新命名或覆蓋直接放在 `D:\stock` 內的其他程式。

## 一鍵完整安裝

直接執行：

```cmd
INSTALL_D_STOCK_ENV_FULL.cmd
```

此模式會：

1. 檢查或安裝 Git 與 Python 3.10–3.12。
2. 將 `week833/stock` 安裝或更新到 `D:\stock\GitHub`。
3. 建立 `D:\stock\GitHub\.venv`。
4. 安裝 `requirements.txt`。
5. 設定 Windows 使用者環境變數與 PATH。
6. 在舊路徑不存在時建立 `D:\Downloads\stock -> D:\stock\GitHub` junction。
7. 下載所有主要、大型與早期相容研究 repository。
8. 驗證核心 Python 套件。

外部來源存放於：

```text
D:\stock\GitHub\external_repos
```

## 安全原則

新版安裝器：

- 不會搬移整個 `D:\stock`。
- 不會刪除 `D:\stock` 內其他程式。
- 不會覆蓋非本 repository 的 `D:\stock\GitHub`。
- 若 `D:\stock\GitHub` 已存在且不是正確 repository，會安全停止並顯示錯誤。
- 若 `.venv` 不完整，會安全停止，不會自行刪除該資料夾。

## 復原前版安裝器搬走的程式

舊版安裝器可能曾把整個 `D:\stock` 移至：

```text
D:\stock_backup_YYYYMMDD_HHMMSS
```

可執行：

```cmd
RECOVER_MOVED_STOCK_PROGRAMS.cmd
```

復原工具只會將備份內容複製到新的資料夾：

```text
D:\stock\Recovered_from_previous_installer_YYYYMMDD_HHMMSS
```

它不會刪除原始備份，也不會直接覆蓋目前的 `D:\stock` 程式。

## Windows 應用程式設定

完成後會設定：

```text
STOCK_HOME=D:\stock\GitHub
STOCK_REPO=D:\stock\GitHub
STOCK_SHARED_ROOT=D:\stock
STOCK_VENV=D:\stock\GitHub\.venv
STOCK_PYTHON=D:\stock\GitHub\.venv\Scripts\python.exe
STOCK_EXTERNAL_REPOS=D:\stock\GitHub\external_repos
```

需要指定 Python 解譯器時使用：

```text
D:\stock\GitHub\.venv\Scripts\python.exe
```

安裝完成後請重新開啟 CMD、PowerShell、VS Code、排程器或其他應用程式。

## 可雙擊執行檔

| 執行檔 | 用途 |
|---|---|
| `INSTALL_D_STOCK_ENV_FULL.cmd` | 安裝環境並下載全部外部研究來源 |
| `INSTALL_D_STOCK_ENV.cmd` | 核心安裝；加 `/FULL` 可下載全部來源 |
| `RECOVER_MOVED_STOCK_PROGRAMS.cmd` | 安全複製前版搬走的程式 |
| `STOCK_SETUP_MANAGER.cmd` | 安裝、下載、修復與檢查選單 |
| `CONFIGURE_WINDOWS_ENV.cmd` | 重新設定環境變數與 PATH |
| `OPEN_STOCK_TERMINAL.cmd` | 開啟已啟用虛擬環境的 CMD |
| `RUN_STOCK_PYTHON.cmd` | 使用固定 Python 執行程式 |
| `DOWNLOAD_STOCK_SOURCES.cmd` | 下載或更新全部主要來源 |
| `DOWNLOAD_LEGACY_SOURCES.cmd` | 下載早期相容來源 |
| `REPAIR_STOCK_PATHS.cmd` | 建立安全的舊路徑連結 |
| `VERIFY_STOCK_ENV.cmd` | 驗證環境、PATH 與套件 |

## 來源分類

目前整理 36 個股票分析 GitHub 來源：

```text
sources/
├─ 01_taiwan_stock_data/
├─ 02_market_data_platforms/
├─ 03_ai_ml_forecasting/
├─ 04_backtesting_engines/
└─ 05_indicators_factors_portfolio/
```

第三方原始碼位於 `external_repos/`，並由 `.gitignore` 排除。

## 錯誤紀錄

```text
%TEMP%\install_d_stock_env.log
```
