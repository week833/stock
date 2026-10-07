# 台股 AI 分析與量化研究工具庫

Windows 安全安裝位置固定為：

```text
D:\stock\GitHub
```

簡易安裝與基本檢查說明見
[`docs/windows_installer_package.md`](docs/windows_installer_package.md)。

外部 GitHub repository 的分類、功能與 runtime 邊界見
[docs/repository_function_catalog.md](docs/repository_function_catalog.md)。

`D:\stock` 現在只視為共用資料夾。安裝器不會搬移、刪除、重新命名或覆蓋直接放在 `D:\stock` 內的其他程式。

## 安裝環境與選單

第一次請下載完整 repository（ZIP 解壓或 Git clone），再雙擊：

```cmd
STOCK_SETUP_MANAGER.cmd
```

選單提供六項操作，`Q` 離開：

1. 安裝環境：沿用或建立本專案 `.venv`，安裝 `requirements.txt`，設定一次 User ENV/PATH。
2. 下載 core 研究來源：下載目前預設的 3 個 core；外部 repository 全部是可選研究資源。
3. 修復舊路徑：明確選擇此項才建立缺少的 junction；target 尚不存在時略過。
4. 重設 ENV/PATH：重新設定本專案的使用者環境與 PATH。
5. 開啟 venv 終端機：開啟已啟用本專案 `.venv` 的 CMD。
6. 基本檢查：確認本專案標記及固定 Python 能啟動，顯示版本與實際路徑。

固定環境是 `D:\stock\GitHub\.venv`，與既有 `D:\stock\.venv` 不同；安裝器不替換其他專案的環境。
已有本專案標記（`STOCK_SETUP_MANAGER.cmd` 與 `scripts\setup\install_d_stock_env.ps1`）時沿用現有專案，不以 origin、branch 或 dirty 狀態阻擋。
優先沿用有效的本專案 venv；需要新建時才尋找 host Python 3.x >= 3.10，缺少可用 Python 才以 winget 安裝 Python 3.12。
wrapper 不強制提升權限；個別系統工具安裝仍可能由 Windows 顯示 UAC。

Git 只在 clone 缺少的專案或新的外部來源時需要。已存在的外部來源目錄會提示略過，不自動 pull 或更新。
若 `D:\stock\GitHub` 已有非空內容卻缺少本專案標記，安裝會停止；不覆寫、不搬移或刪除 `D:\stock` 其他內容。

要先預覽或直接使用入口，可執行：

```cmd
INSTALL_D_STOCK_ENV.cmd /PREFLIGHT
INSTALL_D_STOCK_ENV.cmd
INSTALL_D_STOCK_ENV_FULL.cmd
DOWNLOAD_STOCK_SOURCES.cmd --check
REPAIR_STOCK_PATHS.cmd --check
VERIFY_STOCK_ENV.cmd
```

`INSTALL_D_STOCK_ENV.cmd` 只安裝環境；獨立的 FULL 入口接著下載預設 core 來源。
來源存放於 `D:\stock\GitHub\repos`；`DOWNLOAD_STOCK_SOURCES.cmd --all` 可另外下載全部 primary，legacy 入口另行使用。
來源的 `--check`／`--dry-run` 與安裝的 `/PREFLIGHT`／`/DRY-RUN` 是唯讀預覽。
選單修復會執行 `--apply --confirm`；直接使用修復工具時也必須明示這兩個參數。既有實體路徑或錯誤 junction 不會被替換。

## 復原前版安裝器搬走的程式

舊版安裝器可能曾把整個 `D:\stock` 移至：

```text
D:\stock_backup_YYYYMMDD_HHMMSS
```

可執行：

```cmd
RECOVER_MOVED_STOCK_PROGRAMS.cmd
```

復原工具目前僅列出並檢查舊 `D:\stock_backup_*` 來源；依專案不再建立額外備份的政策，不會複製成第二份 recovery 目錄，也不會覆蓋目前的 `D:\stock` 程式。

## Python、Windows 設定與基本檢查

固定 Python 路徑：

```text
D:\stock\GitHub\.venv\Scripts\python.exe
```

`CONFIGURE_WINDOWS_ENV.cmd` 重設 User ENV/PATH；設定後重新開啟 CMD、PowerShell 或 VS Code。
直接使用固定 Python 不需要先滿足 Git、User PATH 或研究來源完整性條件。

`VERIFY_STOCK_ENV.cmd` 只確認本專案標記、固定 Python 能啟動及版本；不是套件完整性或正式分析驗收。
需要額外檢查套件時，可在本專案根目錄手動執行既有工具：

```cmd
.venv\Scripts\python.exe scripts\compat\verify_python_packages.py --requirements requirements.txt
```

基本檢查不執行正式全市場分析，也不證明第三方策略的績效、盈利能力或資料品質。

| 入口 | 用途 |
|---|---|
| `STOCK_SETUP_MANAGER.cmd` | 六項環境、來源、路徑、終端機與檢查選單 |
| `INSTALL_D_STOCK_ENV.cmd` | 安裝環境；`/PREFLIGHT`、`/DRY-RUN` 只預覽 |
| `INSTALL_D_STOCK_ENV_FULL.cmd` | 安裝環境後下載預設 core 來源 |
| `CONFIGURE_WINDOWS_ENV.cmd` | 重設 User ENV/PATH |
| `OPEN_STOCK_TERMINAL.cmd` | 開啟已啟用本專案 venv 的 CMD |
| `RUN_STOCK_PYTHON.cmd` | 用固定 Python 執行指定程式 |
| `DOWNLOAD_STOCK_SOURCES.cmd` | 預設 core；`--all` 選全部 primary；既有目錄略過 |
| `DOWNLOAD_LEGACY_SOURCES.cmd` | 下載 legacy 研究來源；既有目錄略過 |
| `REPAIR_STOCK_PATHS.cmd` | `--check` 預覽；`--apply --confirm` 才建立缺少的 junction |
| `VERIFY_STOCK_ENV.cmd` | 唯讀基本 Python 檢查 |

## 來源分類

目前整理 37 個 primary 股票分析 GitHub 來源，另管理 3 個 legacy compatibility
identities，並收錄 8 個 AI／Agent 開發研究來源（optional）：

```text
sources/
├─ taiwan_market_data/
├─ global_market_data/
├─ machine_learning_forecasting/
├─ backtesting_engines/
├─ quant_portfolio_risk/
└─ ai_agent_tools/
```

第三方原始碼位於 `repos/`，並由 `.gitignore` 排除。

`repos/ai_agent_tools/` 只供 AI、Agent 與開發方法研究；正式台股分析不直接匯入這些 repository 的推薦名單、模型或執行結果。

