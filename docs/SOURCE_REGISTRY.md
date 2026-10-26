# D-stock 來源登錄與權威邊界

本文件是給人與 AI Agent 使用的「薄型索引」，不是第二份資料集清單，也不是外部 Repo 的完整 mirror。詳細資料仍以各來源的 system of record 為準；若本文件與權威成品不一致，應先停止發布並重新驗證，不可依本文件猜測。

盤點日期：2026-08-30

## 2026-08-30 清理後邊界（Fact）

- D-stock 的 `programs\full_market_analysis` 與 `programs\daily_analysis_scheduler` 已退役；`programs\shared\UPDATE_GLOBAL_MARKETS.cmd` 亦已移出現行路徑，這些名稱不代表目前可執行入口。
- `D:\stock\_DELETE_OLD_CONTENT_20260830` 只是歷史隔離區名稱；2026-08-30 live observation 已確認該路徑不存在。此前三次由 agent 嘗試永久刪除均在 PowerShell 啟動前遭 executor policy `CreateProcess rejected: blocked by policy` 攔截；這是歷史執行紀錄，不可宣稱本代理的刪除動作成功，也不可把該歷史名稱當作現行來源。
- 舊 `D:\stock\data` 已不存在於 live tree；其中 27/27 JSON 與 `D:\TWStockData\shared_market_data` 對應檔案 SHA-256 一致，因此不再是 live routing source。
- 中央 owner 維持 `D:\TWStockData` 的 48 routes。`D:\stock\logs\daily_analysis_scheduler` 已於 2026-08-30 live observation 確認不存在；它只屬歷史分類，不是 live source 或現行 scheduler。
- 根 `D:\stock\.venv`／`programs\environment_setup` 因 Running 的 `DStockTelegramCommandBot` 直接依賴而保留；single-stock canonical runtime 為 `D:\stock\.venv`（2026-10-12 更正：`programs\shared\find_python.cmd analysis`／`analysis-test` 只接受 `D:\stock\.venv\Scripts\python.exe`，`STOCK_ANALYSIS_PYTHON` 若指向其他路徑即 fail-closed；Telegram bot 也經由同一 resolver）。共用 refresh 僅接受 `central_owner_only` 且 fail-closed。
- 2026-10-12 註記（同日更正）：D-Stock 的單股、Telegram、shared 程式已不使用 `D:\stock\GitHub\.venv`，舊工具 `single_stock_analysis\tools\INSTALL_SINGLE_STOCK_3M_DEPS.cmd` 已移到 `single_stock_analysis\tools\_retired\`。但 `D:\stock\GitHub` 工具箱仍在使用這個 venv，所以不可刪除。依據如下：
  - 該目錄的 `RUN_STOCK_PYTHON.cmd`、`OPEN_STOCK_TERMINAL.cmd` 以及 README 第 34 行（「固定環境是 `D:\stock\GitHub\.venv`」）。
  - 使用者環境變數 `STOCK_VENV`、`STOCK_PYTHON` 指向它。
  - 使用者 PATH 含 `D:\stock\GitHub\.venv\Scripts`。
- **Fact｜runtime verifier FAIL（2026-08-30）**：`D:\stock\programs\VERIFY_RUNTIME_DATA_PATHS.cmd` 退出碼 1；`D:\TWStockData\program_data\dstock_daily\state\runtime_path_health_latest.json` 為 `status=fail`、`exit_code=1`、2 errors、0 warnings。錯誤為 `source_directory_missing`（`D:\TWStockData\program_data\dstock_daily\industry_operational`）與 `mix_data_source_not_relative`（`mix_config_investor_sector_v1_4.json:data_sources.history_cache`）；本次清理不修復 Central／MIX。
- 2026-08-30 live observation：User PATH 中舊的 `D:\stock\programs\full_market_analysis` segment 已移除 1 筆（35→34），target count 為 0；Machine PATH 未變更，其餘 segment 順序與文字保留。

## 來源分類

| 來源家族 | source class | 唯一真實來源 | 正式模型資格 | PIT／freshness | 更新入口 | 驗證入口 | 目前狀態與信任理由 |
|---|---|---|---|---|---|---|---|
| 中央正式行情與共用原始資料 | `formal-data` | `D:\TWStockData\shared_market_data`；最新 receipt 為 `D:\TWStockData\services\market_update\receipts\latest_manifest.json` | 可以。只限 receipt 宣告可發布、必要資料集與 hash/coverage/PIT 門檻通過時 | 以 receipt 的 `expected_asof`、`freshness`、`coverage`、`pit` 與 `hash` 欄位為準；缺資料必須留在 denominator | `D:\TWStockData\services\market_update\market_update.cmd` | `D:\stock\programs\VALIDATE_MARKET_DATA_QUALITY.cmd`、`D:\stock\programs\VERIFY_RUNTIME_DATA_PATHS.cmd`、中央 market receipt | 目前 receipt `status=complete`、`publishable=true`、`conditions_met=false`、`partial=false`、`fail_closed=false`；runtime verifier 另為 FAIL，正式模型只能信任 receipt 明確合格的資料 |
| D-stock 路徑與資料所有權 | `routing-control` | `D:\stock\config\shared_data_paths.json` | 不提供資料內容；只決定正式程式應讀寫哪個 system of record | 不可用路徑存在取代 freshness 或 PIT 證據 | 由專案維護；不得由下游程式自行改寫 | `D:\stock\programs\VERIFY_RUNTIME_DATA_PATHS.cmd` | 這是路由契約，不是資料來源；模型、報告、state 等 project data 實際集中於 `D:\TWStockData\program_data\dstock_daily` |
| 正式報告與發布成品 | `formal-output` | `D:\TWStockData\program_data\dstock_daily\reports\latest_report_manifest.json` 及其列出的 artifacts（歷史 D-stock 成品證據） | 只代表成品是否可進入發布流程；不會把外部研究 Repo 升格為正式資料 | 以 manifest 的資料日、預測日、來源 receipt hash、artifact hash 與 publishability 為準 | 現行全市場分析使用者入口為 `D:\MIX All\02_MIX_ALL正式報告.cmd`；該入口驗證並轉交唯一 `1_執行MIX每日分析與產生報告.cmd`，再由內部 `D:\MIX All\tools\internal\maintenance\RUN_DAILY.cmd` 呼叫 `D:\MIX All\tools\run_daily.ps1`。D-stock `full_market_analysis` 已退役，沒有現行 D-stock 更新入口 | `D:\stock\programs\cross_system_validation\RUN_ACCEPTANCE_CHECKS.cmd`；舊 D-stock verifier 僅屬歷史／已隔離路徑 | 舊 D-stock manifest 僅作歷史證據；目前 acceptance 仍為 `blocked`，不可由 exit code 0 單獨宣稱可發布 |
| 台股／全球／ML／回測／投資組合研究 Repo | `research-only` | `D:\stock\GitHub\scripts\sources\source_manifest_v1.json`；本機狀態快照為 `D:\stock\GitHub\repos\.source_provenance.json` | 不可以作為正式推薦、正式行情或正式模型的直接來源 | 以 manifest 的 branch 與 provenance 的 observed HEAD 為研究重現性界線；不得以目前 upstream 狀態替代當時快照 | `D:\stock\GitHub\scripts\sources\clone_stock_analysis_repos.cmd`、`source_manager.ps1 -Check`／`-Snapshot` | manifest 目前維持 37 個 primary、2 個 legacy；provenance 於 2026-08-23 觀察 39 個 canonical Repo，39/39 `status=valid`、0 dirty；snapshot 不等於 Git 歷史基線 |
| AI／Agent／Skill 與三竹 UI 研究資料 | `reference-only` | `D:\stock\GitHub\sources\ai_agent_tools\README.md`、對應 `D:\stock\GitHub\repos\ai_agent_tools\` canonical Repo，以及 `D:\stock\GitHub\docs\repository_duplicate_matrix.md` | 不可以作為台股正式推薦、交易下單、正式行情或模型輸入 | 依各 Repo 本機 branch／HEAD 及其公開包的 status；不得由 reference-only 推論能力或交易支援 | 讀取 Repo README、公開 provenance/status，必要時使用其自帶唯讀 checker；不得自動下載或更新 | 目前實際 9 個、README 已登錄 9 個；`source_manifest_v1.json` 的 39-source clone 清單刻意不含此 reference-only 家族，避免改變 source manager 的 37+2 研究來源契約 |
| 歷史相容 Repo | `legacy-reference` | `source_manifest_v1.json` 中 `scope=legacy` 的 2 筆來源 | 不可以；僅限歷史相容與教育研究 | 不具正式 freshness 或 PIT 保證 | `D:\stock\GitHub\scripts\sources\clone_legacy_compat_repos.cmd` | `source_manager.ps1 -Legacy -Check` | 保留作相容性證據；不得把 legacy 內容回填正式資料或推薦流程 |

## 權威層級

使用 AI 或 Agent 接續工作時，依下列順序判斷：

1. 正式行情內容、資料日、coverage、PIT、hash 與發布資格：以中央 market receipt 及其實體 `D:\TWStockData\shared_market_data` 為準。
2. 程式應讀寫哪裡：以 `config\shared_data_paths.json` 為準；它不能取代資料內容驗證。
3. 正式報告是否可交付：以 `latest_report_manifest.json`、artifact validator 及 acceptance 報告為準。
4. 外部研究 Repo 的來源身分與觀察版本：以 `source_manifest_v1.json` 加 `.source_provenance.json` 為準。
5. AI／Agent／UI 參考內容：只以 reference-only 索引與各 Repo 自帶公開證據為準；不得轉為正式資料來源。

## 研究 Repo provenance 契約

`D:\stock\GitHub\repos\.source_provenance.json` 是由既有安全 source manager 的 `-Snapshot` 模式生成。`-Snapshot` 只讀取本機 Git metadata，但會以原子方式寫入／覆寫授權範圍內的 `D:\stock\GitHub\repos\.source_provenance.json`；不會 clone、fetch、pull、下載，也不會修改任何 Repo working tree。它必須具備：

- `schema_version`、`generation`、`manifest_sha256` 與整批 `observed_at`。
- 每筆 source 的 `id`、`relative_target`、`origin`、`branch`、完整 `head`、`status`、`dirty`、`reason` 及 `observed_at`。
- `manifest_sha256` 必須等於當次讀取的 `source_manifest_v1.json` SHA-256；不一致時不得沿用舊 provenance。
- `-Snapshot` 只讀取本機 Git metadata，並只寫入／覆寫上述授權的 provenance 檔；不得 clone、fetch、pull、下載或修改任何 Repo。既有更新模式與檢查模式仍維持原本用途。

本次 provenance 觀察摘要：

- manifest SHA-256：`2bdf3f75277e49b8af9c294e31422439bb24f84a1e73db4c72282709ece17810`
- entries：39
- valid：39
- dirty：0
- AI／Agent Repo `stock-mitake-ui-reference` 本身仍有未提交變更；這不影響本次 39-source provenance，但任何後續整理必須保留該 dirty worktree。
- nested Git 可見性：`D:\stock\GitHub\.gitignore` 已取消 `repos/.source_provenance.json` 的 nested ignore，目前 ledger 在 nested worktree 為 untracked；外層 `D:\stock\.gitignore` 仍忽略 `GitHub/`。
- 本輪未對 ledger 或 nested `.gitignore` 執行 stage／commit；Git durability 仍未建立，39/39 snapshot 不得被解讀為 Git 歷史保證。

## AI／Agent 來源差異處理

本次由本機 README、Repo README、origin、branch、HEAD 與 repository matrix 唯一確認 `stock-mitake-ui-reference` 的 canonical 身分、用途與路徑，已將它補入 `sources\ai_agent_tools\README.md`。因此現況為「實際 9、README 9」。

`source_manifest_v1.json` 仍維持 39 筆台股研究／相容來源，不把 AI／Agent Repo 塞進 `sources` 陣列，原因是既有 source manager 明確驗證 37 primary + 2 legacy、限定 category，且 AI／Agent 來源是 reference-only，不是可安裝的台股研究來源。若未來要建立跨家族統一 manifest，必須另立 schema／決策紀錄，不可直接把 reference-only 來源混入現有 clone 清單。

## 舊 shared_market_data 路徑核對結果（歷史／已隔離）

原列六份文件已完成現行語意核對；六檔的舊 shared root 精確 active 命中數為 `0`。歷史／legacy 說明若仍提到舊根，只能標明為相容性或歷史證據，不得當作目前 routing contract：

- `D:\stock\programs\shared_data_management\README.md`（歷史核對項）
- `D:\stock\programs\full_market_analysis\README.md`（已退役／已隔離）
- `D:\stock\programs\full_market_analysis\PRICE_ADJ_UPDATER.md`（已退役／已隔離）
- `D:\stock\programs\full_market_analysis\FINMIND_FEATURE_SEED_IMPORT.md`（已退役／已隔離）
- `D:\stock\programs\full_market_analysis\FINMIND_FEATURE_UPDATER.md`（已退役／已隔離）
- `D:\stock\programs\shared\README.md`（現行說明，僅指向中央 owner）

`D:\stock\data` 是已不存在的 legacy tree，不再是現行 routing source；其中 27/27 JSON 與 `D:\TWStockData\shared_market_data` 對應檔案 SHA-256 一致。不得以歷史內容建立新的正式資料副本。現行正式共用資料仍以 `D:\TWStockData\shared_market_data`、中央 receipt 及 `config\shared_data_paths.json` 為準。

## AI 使用規則

新任務開始時，先讀本文件，再依 source class 選來源：

```text
formal-data       → 只讀中央 receipt / shared_market_data
routing-control   → 只讀 path contract，不當成內容證據
formal-output     → 讀 report manifest / acceptance 判定可交付性
research-only     → 只作方法研究，固定 branch + HEAD，不作正式輸入
reference-only    → 只作 AI／Agent／UI 方法參考，不作交易或推薦依據
legacy-reference  → 只作相容性研究，不回填正式流程
```

遇到來源缺失、manifest SHA 不一致、資料日不符、PIT/freshness 未驗證、Repo dirty 或 reference-only 邊界不清時，應回報 `pending`／`blocked`，停止推論與發布。
