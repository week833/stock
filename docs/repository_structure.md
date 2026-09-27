# Repository 結構

```text
stock/
├─ README.md
├─ .gitignore
├─ requirements.txt
├─ requirements_tw_stock_ai.txt          # 舊檔名相容入口
├─ STOCK_SETUP_MANAGER.cmd                # 統一安裝 / 下載 / 修復 / 檢查選單
├─ install_tw_stock_ai_env.cmd            # 舊根目錄安裝路徑相容入口
├─ DOWNLOAD_STOCK_SOURCES.cmd
├─ DOWNLOAD_LEGACY_SOURCES.cmd
├─ REPAIR_STOCK_PATHS.cmd
├─ VERIFY_STOCK_ENV.cmd
├─ external_stock_repositories.md         # 舊文件路徑相容入口
│
├─ docs/
│  ├─ repository_structure.md
│  ├─ external_stock_repositories.md
│  └─ legacy_compatibility.md
│
├─ scripts/
│  ├─ clone_stock_analysis_repos.cmd      # 舊 scripts 路徑相容入口
│  ├─ setup/
│  │  └─ install_tw_stock_ai_env.cmd
│  ├─ sources/
│  │  ├─ clone_stock_analysis_repos.cmd
│  │  └─ clone_legacy_compat_repos.cmd
│  └─ compat/
│     ├─ repair_legacy_paths.cmd
│     └─ verify_stock_environment.cmd
│
├─ sources/
│  ├─ 01_taiwan_stock_data/
│  ├─ 02_market_data_platforms/
│  ├─ 03_ai_ml_forecasting/
│  ├─ 04_backtesting_engines/
│  └─ 05_indicators_factors_portfolio/
│
├─ external_repos/                        # 本機 clone 內容，不提交 GitHub
│  ├─ 00_legacy_compat/
│  ├─ 01_taiwan_stock_data/
│  ├─ 02_market_data_platforms/
│  ├─ 03_ai_ml_forecasting/
│  ├─ 04_backtesting_engines/
│  └─ 05_indicators_factors_portfolio/
│
└─ github_sources/                        # 早期路徑相容連結，不提交 GitHub
```

## 整理原則

- 根目錄保留 GitHub 入口檔與可直接雙擊的 Windows 啟動器。
- 真正執行邏輯集中於 `scripts/`，根目錄檔案主要負責相容轉接。
- 說明文件集中於 `docs/`，但保留舊文件路徑轉接。
- 每個外部來源在 `sources/<分類>/<來源>/README.md` 有獨立說明。
- 真正的外部原始碼 clone 到 `external_repos/<分類>/<來源>/`，並由 `.gitignore` 排除。
- 舊的 `external_repos/<來源>/` 與 `github_sources/<來源>/` 透過 junction 指向新分類位置。
- 不覆蓋既有實體資料夾，不刪除既有 `.venv`，也不把不同第三方專案混在同一資料夾。

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

## 2026-09-27 外部 repository inventory

- [本機 repository 功能目錄](./repository_function_catalog.md)
- [本機 repository duplicate matrix](./repository_duplicate_matrix.md)

這份 124-worktree 清冊（49 canonical + 75 direct-root）是本機 snapshot，與本文件所列的 36-primary installer catalog 分屬不同範圍；分類不代表 source-tree migration 或正式 runtime 依賴。
