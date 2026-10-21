# Repository 結構

```text
stock/
├─ README.md
├─ .gitignore
├─ .env.example                           # .env 空白範本（金鑰欄位留空）
├─ requirements.txt
├─ requirements_tw_stock_ai.txt           # 舊檔名相容入口（-r requirements.txt）
├─ STOCK_SETUP_MANAGER.cmd                # 統一安裝 / 下載 / 修復 / 檢查選單
├─ AI_STOCK_ACTIONS.cmd                   # AI 語意 action/workflow 安全入口
├─ INSTALL_D_STOCK_ENV.cmd                # 安裝環境；/PREFLIGHT、/DRY-RUN 只預覽
├─ INSTALL_D_STOCK_ENV_FULL.cmd           # 安裝環境後下載 3 個 core 來源
├─ CREATE_OR_RECOVER_ENV.cmd              # 建立或復原 .env
├─ install_tw_stock_ai_env.cmd            # 舊根目錄安裝路徑相容入口
├─ DOWNLOAD_STOCK_SOURCES.cmd
├─ DOWNLOAD_LEGACY_SOURCES.cmd
├─ REPAIR_STOCK_PATHS.cmd
├─ VERIFY_STOCK_ENV.cmd
├─ CONFIGURE_WINDOWS_ENV.cmd
├─ OPEN_STOCK_TERMINAL.cmd
├─ RUN_STOCK_PYTHON.cmd
├─ RECOVER_MOVED_STOCK_PROGRAMS.cmd
├─ external_stock_repositories.md         # 舊文件路徑相容入口
│
├─ .github/workflows/
│  └─ validate-windows-installers.yml     # windows-latest 執行 tests/installer（-CiMode）
│
├─ docs/
│  ├─ ai_action_workflows.md
│  ├─ env_setup.md
│  ├─ external_stock_repositories.md
│  ├─ github-catalog.md
│  ├─ installer_troubleshooting.md
│  ├─ legacy_compatibility.md
│  ├─ repository_duplicate_matrix.md
│  ├─ repository_function_catalog.md
│  ├─ repository_structure.md
│  ├─ source-review.md
│  ├─ windows_installer_package.md
│  └─ mitake/
│     ├─ README.md
│     └─ capabilities.json
│
├─ scripts/
│  ├─ clone_stock_analysis_repos.cmd      # 舊 scripts 路徑相容入口
│  ├─ actions/
│  │  ├─ Invoke-StockActions.ps1
│  │  ├─ Test-StockActions.ps1
│  │  ├─ action-manifest.json
│  │  ├─ action-policy.json
│  │  ├─ action-schema.json
│  │  └─ workflows/
│  │     ├─ full-install.json
│  │     ├─ safe-setup.json
│  │     └─ verify-only.json
│  ├─ setup/
│  │  ├─ assert_fixed_install_root.ps1
│  │  ├─ configure_windows_environment.cmd
│  │  ├─ create_or_recover_env.ps1
│  │  ├─ install_d_stock_env.ps1
│  │  └─ install_tw_stock_ai_env.cmd
│  ├─ sources/
│  │  ├─ clone_stock_analysis_repos.cmd   # 轉呼叫 source_manager.ps1
│  │  ├─ clone_legacy_compat_repos.cmd    # 以 --legacy 轉呼叫上一支
│  │  ├─ source_manager.ps1               # 依 manifest 下載缺少來源；既有目錄略過
│  │  └─ source_manifest_v1.json          # 39 筆：37 primary＋2 legacy
│  └─ compat/
│     ├─ assert_managed_venv.ps1
│     ├─ recover_previous_stock_backups.ps1
│     ├─ repair_legacy_paths.cmd
│     ├─ repair_legacy_paths_guard.ps1    # 相容路徑對照表與 junction 修復
│     ├─ verify_python_packages.py
│     └─ verify_stock_environment.cmd
│
├─ tests/
│  └─ installer/
│     └─ Test-InstallerIntegration.ps1
│
├─ sources/
│  ├─ README.md
│  ├─ taiwan_market_data/                 # 7 個來源 README
│  ├─ global_market_data/                 # 2
│  ├─ machine_learning_forecasting/       # 12
│  ├─ backtesting_engines/                # 6
│  ├─ quant_portfolio_risk/               # 10
│  ├─ ai_agent_tools/                     # 只有 README.md，編目 10 個 AI／Agent 來源
│  └─ tw_data/ market_data/ ml/ backtests/ quant_tools/  # 舊分類名稱的說明副本
│
├─ repos/                        # 本機 clone 內容，不提交 GitHub
│  ├─ legacy_compat/
│  ├─ taiwan_market_data/
│  ├─ global_market_data/
│  ├─ machine_learning_forecasting/
│  ├─ backtesting_engines/
│  ├─ quant_portfolio_risk/
│  └─ ai_agent_tools/
│
├─ logs/sessions/                # AI action session 紀錄，不提交 GitHub
├─ stock_related/                # 股票相關集中檢視；10 個容器、11 個 Junction，不是額外 clone；不提交 GitHub
│
└─ github_sources/               # 早期路徑相容連結，不提交 GitHub
```

## 本機結構快照（2026-08-30）

以下數量是 2026-08-30 在本機 `D:\stock\GitHub` 實測的快照（`repos/`、root external 與 Junction 都不在
本 repository 內，2026-10-21 未重新實測）。本基準以實體、非 Junction 的 Git worktree 計算；Junction 是
既有相容入口，不另計為 repository。分類依主要功能判定，不以目前是否被 D-stock 程式引用為條件。

| 結構或分類 | 數量 | 定義 |
|---|---:|---|
| 固定 root | 1 | `D:\stock\GitHub` 主專案 |
| canonical external repository | 49 | `repos/<分類>/<repository>` 下的 49 個實體 clone |
| root external repository | 43 | `D:\stock\GitHub/<repository>` 下的 43 個實體 clone |
| stock-related external | 44 | 主要功能屬台股、全球市場、量化、因子、回測、投組風控、股票 AI／交易研究或 Legacy 相容的全部外部 clone |
| non-stock external | 48 | 其餘 AI／Agent、開發、影音、資料、隱私、安全、遊戲與其他研究工具 |
| Junction（全工作區實測） | 63 | 原有 root／`repos/` 相容別名 52 個，加上 `stock_related/` 的 11 個分類／來源 Junction；不代表第二份 clone |

外部 repository 合計為 92（49 canonical + 43 root external），其中股票相關 44、非股票
相關 48。`stock_related/` 已建立為股票相關集中檢視入口：頂層有 10 個分類容器，其中
`01`–`05` 與 `10` 是 6 個分類 Junction，`06`–`09` 是 4 個普通分組容器；容器內另有
5 個來源 Junction，合計 11 個。這些 Junction 全部指向既有 canonical 或 root external
repository，不複製 Git metadata，也不改變上述實體 clone 數量；全工作區 Junction 實測總數
為 63。

## 整理原則

- 根目錄保留 GitHub 入口檔與可直接雙擊的 Windows 啟動器。
- 真正執行邏輯集中於 `scripts/`，根目錄檔案主要負責相容轉接。
- 說明文件集中於 `docs/`，但保留舊文件路徑轉接。
- 每個外部來源在 `sources/<分類>/<來源>/README.md` 有獨立說明。
- 真正的外部原始碼 clone 到 `repos/<分類>/<來源>/`，並由 `.gitignore` 排除。
- AI／Agent 研究來源集中於 `repos/ai_agent_tools/`；其中 FinMind research skill 與
  三竹 UI reference 依功能歸入股票相關集中檢視，其餘 8 個為一般 AI／Agent 支援；
  10 個來源全數仍是 reference-only，不屬於正式股票推薦輸入。
- `STOCK_EXTERNAL_REPOS` 固定為 `D:\stock\GitHub\repos`；canonical 實體路徑仍為
  `repos/<分類>/<來源>/`，不得為了集中檢視改成另一份 clone 或改寫 source manifest。
- `stock_related/` 只提供股票相關 repository 的單一查找入口；`01`–`05`、`10` 直接是
  分類 Junction，`06`–`09` 是普通分組容器，容器內的 5 個來源項目是 Junction；每個
  Junction target 都指向既有 canonical 或已確認的 root external 實體路徑。
- 舊的 `repos/<來源>/` 與 `github_sources/<來源>/` 透過 junction 指向新分類位置。
- 既有頂層 AI 工具與 `stocktw` 路徑也保留 junction，指向新的分類位置。
- 不覆蓋既有實體資料夾，不刪除既有 `.venv`，也不把不同第三方專案混在同一資料夾。
- package 可從任意解壓位置啟動；來源下載、修復與檢查 wrapper 以固定路徑呼叫
  `D:\stock\GitHub` 內的腳本，避免從呼叫端複本寫入錯誤目錄。
- 安裝器拒絕 reparse point 形式的 `D:\stock`、`D:\stock\GitHub`、`.venv`、`.venv\Scripts`、
  `python.exe` 與 `requirements.txt`；來源下載在寫入缺少的 target 前檢查其上層目錄不是 reparse
  point；路徑修復要求 root、target 與 link 上層目錄（連同祖先）都是實體目錄。
- `AI_STOCK_ACTIONS.cmd`、`CREATE_OR_RECOVER_ENV.cmd`、`RECOVER_MOVED_STOCK_PROGRAMS.cmd` 執行
  `scripts\` 下的 PowerShell 腳本前，會確認腳本本身及所有上層目錄（含 `scripts`）都不是 reparse
  point；AI action 處理每個 action 時也對其 wrapper 做相同檢查。`.git` 沒有 reparse point 檢查。

## 主要相容入口

以下路徑整理前後都能使用：

```text
install_tw_stock_ai_env.cmd
scripts/clone_stock_analysis_repos.cmd
requirements_tw_stock_ai.txt
external_stock_repositories.md
```

推薦新的統一入口：

```text
STOCK_SETUP_MANAGER.cmd
```
