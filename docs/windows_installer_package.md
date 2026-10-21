# Windows 簡易安裝與檢查

固定專案位置為 `D:\stock\GitHub`，Python 環境為其 `.venv`；不使用或替換 `D:\stock\.venv`。

下載完整 repository 後，雙擊 `STOCK_SETUP_MANAGER.cmd`。六項選單依序是安裝環境、下載 core 來源、修復舊路徑、重設 ENV/PATH、開啟 venv 終端機與基本檢查。

## 安裝環境

`INSTALL_D_STOCK_ENV.cmd` 執行環境安裝；`INSTALL_D_STOCK_ENV_FULL.cmd` 接著下載預設的 3 個 core 研究來源。
`/PREFLIGHT`、`/DRY-RUN`（及相容的 `/CHECK`）只顯示計畫。

已存在的專案以 `STOCK_SETUP_MANAGER.cmd` 和 `scripts\setup\install_d_stock_env.ps1` 為標記，沿用其有效 venv。
需要建立 venv 時才尋找 host Python 3.x >= 3.10；缺少可用 Python 才透過 winget 安裝 Python 3.12。
安裝會以本專案 Python 安裝 `requirements.txt`，再設定一次 User ENV/PATH；ConfigureOnly 只重設環境，不要求 requirements 檔。
wrapper 不強制 UAC；個別系統工具安裝仍可能要求 Windows 權限確認。

Git 只在 clone 缺少的專案或新外部來源時需要，找不到時以 winget 安裝 `Git.Git`。已有專案不檢查 origin、branch、dirty 或 worktree identity。
AI 動作入口 `AI_STOCK_ACTIONS.cmd` 以相同兩個標記辨識專案根目錄，同樣不檢查 origin、branch、dirty 或 worktree identity，辨識時也不需要 Git；session 記錄以 `origin_check: not_checked` 註明（見 `docs/ai_action_workflows.md`）。
非空目標若缺少本專案標記，會停止；不覆寫、不搬移或刪除 `D:\stock` 其他內容。

## 外部來源與舊路徑

外部 repository 全部是可選研究資源。core 下載預設選 3 個來源；`--all` 選 primary 清單，legacy 使用獨立入口。
來源 target 已是目錄時提示略過，不 pull、不更新；是檔案時停止。只有缺少的來源才 clone。
`--check`、`--dry-run`（及舊 `--verify`）是唯讀計畫，不認證既有 repository 或要求完整研究清單。

修復選單是明示操作；直接呼叫可先用 `REPAIR_STOCK_PATHS.cmd --check` 預覽，再以 `--apply --confirm` 執行。
只有 link 尚不存在且 target 已存在時才建立 junction；缺少 target 顯示 SKIP。既有實體 endpoint 或錯誤 junction 保留並停止，不被替換。

## 基本檢查

`VERIFY_STOCK_ENV.cmd` 只確認固定專案標記、`.venv\Scripts\python.exe` 能啟動，並顯示 Python 版本與實際路徑。
它不以 Git、User PATH、研究來源、逐套件 import 或 `pip check` 作為基本環境門檻。
若要自行檢查套件，在專案根目錄執行：

```cmd
.venv\Scripts\python.exe scripts\compat\verify_python_packages.py --requirements requirements.txt
```

基本檢查及 fixture 測試都不等於正式市場分析、資料品質或第三方策略驗收。
