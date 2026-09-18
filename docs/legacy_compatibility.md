> [!WARNING]
> **歷史文件／待更新**
>
> 本頁記載的 `D:\stock` 舊安裝位置與資料夾備份／搬移描述，已不適用目前 README 指定的 `D:\stock\GitHub` 安裝流程。以下原文完整保留作歷史參考；目前操作請以[根 README](../README.md)為準。
>
> 待整理項目集中見[股票來源與歷史資料待評估清單](source-review.md)。

# D:\stock 與舊路徑相容說明

本 repository 的正式 Windows 安裝位置已改為：

```text
D:\stock
```

原本的 `D:\Downloads\stock` 已刪除時，不需要手動重建內容。執行根目錄的 `INSTALL_D_STOCK_ENV.cmd` 即可重新建立環境，並建立舊路徑到新路徑的相容連結。

## 建議執行方式

新電腦、環境遺失或舊資料已刪除時：

```cmd
INSTALL_D_STOCK_ENV.cmd
```

建議按右鍵選擇「以系統管理員身分執行」，以便建立 junction 與使用者環境設定。

安裝後可執行：

```cmd
VERIFY_STOCK_ENV.cmd
```

確認 `D:\stock`、`.venv`、PATH、使用者環境變數與核心套件皆正常。

## 正式路徑與舊路徑

正式路徑：

```text
D:\stock
```

舊程式可能使用：

```text
D:\Downloads\stock
```

若舊路徑不存在，修復程式會建立：

```text
D:\Downloads\stock -> D:\stock
```

這是 Windows directory junction，因此原本寫死 `D:\Downloads\stock` 的程式仍會讀取 `D:\stock` 中的內容。

## Windows 使用者環境變數

安裝後會設定：

```text
STOCK_HOME=D:\stock
STOCK_REPO=D:\stock
STOCK_VENV=D:\stock\.venv
STOCK_PYTHON=D:\stock\.venv\Scripts\python.exe
STOCK_EXTERNAL_REPOS=D:\stock\external_repos
PYTHONUTF8=1
PYTHONIOENCODING=utf-8
```

並將 `D:\stock`、虛擬環境與 scripts 子資料夾加入使用者 PATH。

新設定會由新開啟的 CMD、PowerShell、VS Code、排程器與其他應用程式讀取。

## 可直接雙擊的程式

| 檔案 | 功能 |
|---|---|
| `INSTALL_D_STOCK_ENV.cmd` | 從零 clone / 更新 repository、建立環境並設定 Windows |
| `STOCK_SETUP_MANAGER.cmd` | 統一選單：安裝、下載、修復、檢查或完整建置 |
| `CONFIGURE_WINDOWS_ENV.cmd` | 重新設定使用者環境變數與 PATH |
| `OPEN_STOCK_TERMINAL.cmd` | 開啟已啟用 `D:\stock\.venv` 的 CMD |
| `RUN_STOCK_PYTHON.cmd` | 使用固定虛擬環境 Python 執行程式 |
| `DOWNLOAD_STOCK_SOURCES.cmd` | 下載 / 更新全部分類來源 |
| `DOWNLOAD_LEGACY_SOURCES.cmd` | 下載舊版安裝程式曾使用的額外來源 |
| `REPAIR_STOCK_PATHS.cmd` | 建立舊資料夾到新資料夾的相容連結 |
| `VERIFY_STOCK_ENV.cmd` | 檢查核心檔案、環境變數、PATH、`.venv` 與套件匯入 |

## 保留的舊檔案入口

以下舊入口仍可繼續使用：

```text
install_tw_stock_ai_env.cmd
scripts\clone_stock_analysis_repos.cmd
requirements_tw_stock_ai.txt
external_stock_repositories.md
```

它們會自動轉交到新的分類位置。

## 既有 `.venv` 的處理

安裝程式不會主動刪除正常的 `D:\stock\.venv`。若虛擬環境已存在，會沿用並補裝 `requirements.txt` 中缺少的套件。

若 `D:\stock` 存在但不是 Git repository，獨立安裝程式會先將其備份為：

```text
D:\stock_backup_YYYYMMDD_HHMMSS
```

再重新 clone，避免直接刪除未知資料。

## 外部來源舊路徑

新版來源實際存放於：

```text
D:\stock\external_repos\<分類>\<來源>\
```

修復程式會建立舊版平面路徑，例如：

```text
D:\stock\external_repos\FinMind
D:\stock\external_repos\qlib
D:\stock\external_repos\vectorbt
```

並指向新的分類資料夾。

早期安裝程式使用的：

```text
D:\stock\github_sources\twstock
D:\stock\github_sources\tw_stocker
D:\stock\github_sources\python-stock-radar-
D:\stock\github_sources\TW-stock
```

也會建立相容連結。

## 安全原則

相容與安裝程式：

- 不覆蓋已存在的實體舊路徑。
- 非 Git 的 `D:\stock` 會先備份，而不是直接刪除。
- 尚未下載的外部來源只顯示 `SKIP`。
- 第三方原始碼由 `.gitignore` 排除。
- `INSTALL_D_STOCK_ENV.cmd` 預設只安裝核心環境，避免一次下載大量 repository。
