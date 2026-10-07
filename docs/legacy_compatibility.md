# D:\stock\GitHub 與舊路徑相容說明

本專案固定使用 `D:\stock\GitHub` 及其 `.venv`；與既有 `D:\stock\.venv` 不同，不替換其他專案的 Python 環境。
安裝器不搬移、刪除、改名或覆寫 `D:\stock` 其他內容。非空專案目標缺少本專案標記時會停止。

雙擊 `STOCK_SETUP_MANAGER.cmd` 使用六項選單：安裝環境、下載 core、修復舊路徑、重設 ENV/PATH、開 venv 終端機、基本檢查。
環境安裝沿用有效 venv；缺少 venv 才尋找 host Python 3.x >= 3.10，缺少可用 Python 時以 winget 安裝 3.12。
安裝 `requirements.txt` 後設定一次 User ENV/PATH；`CONFIGURE_WINDOWS_ENV.cmd` 只重新設定環境。
Git 只在新的 clone 需要；已有專案或研究目錄不以 Git identity 或 dirty 狀態阻擋，也不自動 pull。

## 研究來源與 legacy 路徑

外部 repository 都是可選研究來源。core 入口預設下載 3 個；`--all` 選全部 primary，legacy 入口選 2 個 legacy。
來源 target 已是目錄會提示略過，檔案 target 則停止。`--check`／`--dry-run` 是唯讀計畫。
既有分類的 legacy 路徑對照如下，`twstock` 共用 primary 目錄，不另作 alias 認證：

```text
mlouielu/twstock                    -> repos/taiwan_market_data/twstock
william911530-cmyk/python-stock-radar- -> repos/legacy_compat/python-stock-radar-
k66inthesky/TW-stock                -> repos/legacy_compat/TW-stock
```

## 修復舊路徑

`D:\Downloads\stock` 與相容 junction 是可選；安裝及基本檢查不會自動建立。
選單第 3 項就是明示修復。直接使用工具時可先預覽，再明示執行：

```cmd
REPAIR_STOCK_PATHS.cmd --check
REPAIR_STOCK_PATHS.cmd --apply --confirm
```

只建立尚不存在、且已有 target 的允許 junction；target 缺少時顯示 SKIP。
既有實體 endpoint、錯誤 junction 都不會被替換；修復會停止並保留現況。

## ENV、終端機與基本檢查

`CONFIGURE_WINDOWS_ENV.cmd` 重設 User ENV/PATH，不要求 host Python、winget 或 requirements 檔；設定後重新開啟應用程式。
`OPEN_STOCK_TERMINAL.cmd` 開啟已啟用本專案 venv 的 CMD；`RUN_STOCK_PYTHON.cmd` 用固定 Python 執行指定程式。
`VERIFY_STOCK_ENV.cmd` 只確認本專案標記、固定 Python 能啟動及版本；Git、PATH、研究來源、逐套件 import 和 pip check 不是基本門檻。
完整套件檢查保留為手動工具，在專案根目錄執行：

```cmd
.venv\Scripts\python.exe scripts\compat\verify_python_packages.py --requirements requirements.txt
```

基本檢查不代表正式分析或第三方策略驗收。AI／Agent 研究來源不進正式推薦。

## 舊入口

`install_tw_stock_ai_env.cmd`、`scripts\clone_stock_analysis_repos.cmd`、
`requirements_tw_stock_ai.txt` 與根目錄 `external_stock_repositories.md` 保留為相容入口或舊文件；
新操作請使用 `STOCK_SETUP_MANAGER.cmd` 與 `docs\` 下的說明。
