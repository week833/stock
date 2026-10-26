# ADR-0001：AI 工作系統四類治理資產

- ID：ADR-0001
- 日期：2026-08-23
- 狀態：accepted
- 狀態語義：`accepted` 僅表示本架構決策已核准；不表示四類資產、驗證、報告或發布已通過。
- 範圍：`D:\stock` 的來源、決策、驗證與交接治理；不改變正式模型公式或外部資料內容
- supersedes：N/A
- superseded_by：N/A

## 背景

AI 會跨越 ChatGPT、Codex、外部研究 Repo、中央行情資料與既有分析程式工作。若只依賴單次對話，來源、取捨、驗證狀態與下一步容易遺失；若把所有資料複製到一個新知識庫，又會製造多個真實來源並增加版本漂移風險。

目前已有領域權威與證據：模型校正歷史、投資邏輯文件、外部 Repo 去重矩陣、三竹 UI reference-only 狀態及工作流 action logs。這些資產應繼續留在原位置，由治理文件引用，而不是搬移或複製正文。

## 選項

### 選項 A：把所有來源、日誌與決策全文複製到新的 AI 知識庫

不採用。這會形成重複真實來源，且無法保證複製內容與 append-only 歷史同步。

### 選項 B：四類資產各自建立第二套完整資料庫

不採用。尤其不新增第二個 verification latest，也不建立另一個與 acceptance 契約競爭的正式驗證摘要。

### 選項 C：薄型索引、中央 ADR、單一 Handoff，沿用既有驗證摘要

採用。索引只保存權威邊界與精確指標；領域紀錄保留在原處；驗證沿用既有 acceptance 契約。

## 決定

1. 建立薄型 `D:\stock\docs\SOURCE_REGISTRY.md`，只登錄來源家族、用途分類、系統記錄、更新入口、驗證入口及 fail-closed 規則；不複製中央資料集或外部 Repo 明細。
2. 以本目錄作為中央 ADR 索引，持久的架構與治理取捨由 `ADR-NNNN-*.md` 記錄。
3. `D:\TWStockData\services\acceptance\latest.json` 是正式驗證摘要的唯一 latest 契約；不新增第二個 `verification latest` 或平行正式摘要。正式 source log 採兩階段證據鏈：child 期間只寫 provisional context，最外層 launcher 在 writer 關閉後才可由 finalizer 以同一 run ID 原子寫入 final size、SHA-256、mtime 與 child exit code。
4. 建立單一 `D:\stock\docs\HANDOFF.md` 作為目前狀態與交接入口。歷史由 Git 保存；在檔案尚未納入 Git 前，明確視為「無版本保證」，不得假設可以自動回復。
5. AI 在架構／治理變更前依序讀取：

   ```text
   HANDOFF（若存在）
   → SOURCE_REGISTRY（若存在）
   → 本 README 與相關 accepted ADR
   → 領域權威、資料 receipt、acceptance/latest.json、必要 action logs
   → 規劃、執行、驗證
   → 更新 HANDOFF；只有持久取捨才新增 ADR
   ```

6. `calibration_history.jsonl`、`MYINVESTDESK_LOGIC_USED.md`、`repository_duplicate_matrix.md`、`mitake_ui_research_status.json` 與 `D:\stock\logs\` 下 action logs 維持各自的領域權威／證據角色；不搬移、不複製成 ADR。三竹狀態仍是 reference-only，不能清除正式模型資格或發布門檻。
7. 沒有來源、版本、權限、完整性或驗證證據時採 fail-closed：停止發布、停止不可逆寫入，並把阻塞與缺失記錄於 Handoff 或驗證摘要；不得用推測填補。Shared-data verification 只有在 canonical root 為 `D:\TWStockData\shared_market_data`、hash algorithm 為 `SHA256`、`issues=[]` 且 `totals.checked_files > 0` 時才可視為 pass。
8. 治理文件不得含 secret、API key、token、密碼、私人 payload 或完整原始日誌。回復只限本次明確授權的檔案，優先使用已納管 Git；不得 broad reset、clean、搬移或刪除其他工作。

## 理由

- 薄型索引避免將 38 個中央資料集、外部研究 Repo 或巨大日誌複製成第二份真實來源。
- 中央 ADR 能保留跨專案的理由與替代方案，而不干擾模型校正等 append-only 領域歷史。
- 沿用既有 acceptance latest 可維持單一正式驗證契約，避免不同報告互相宣告不同狀態。
- 單一 Handoff 讓下一個 AI 能接續目前狀態；Git 未納管前明示限制，避免製造虛假的歷史保證。
- fail-closed 與秘密禁止規則將不確定性及敏感資訊排除在可發布治理資產之外。

## 證據

下列是目前觀察到的精確事實。四類治理資產均已建立，但檔案存在不等於驗證或發布通過；`accepted` 也不改變 acceptance 的阻塞狀態。

### 四類資產與驗證現況

- `D:\stock\docs\SOURCE_REGISTRY.md`：已建立；提供來源家族、用途分類與權威路徑索引。
- `D:\stock\docs\decisions\README.md` 與本 ADR：已建立；提供中央決策紀錄格式與本架構決策。
- `D:\stock\docs\HANDOFF.md`：已建立；提供目前狀態、證據、阻塞與下一步交接入口。
- `D:\TWStockData\services\acceptance\latest.json`：已存在；`generated_at=2026-08-23T13:27:01.519728+00:00`、`mode=reports`、`fixture=false`、selected 2／expected 6、`overall_status=blocked`，包含 1 個 blocking failure（`full_market_analysis:source_log_not_file`）及 2 個 non-blocking warnings（`full_market_analysis:mitake_watchlist_pending`、`full_market_analysis:shared_data_verification_missing`）。SHA-256 為 `5876A1BA42F76671D37299F02BFCF153E7D3239B030983AA68BA89EB95280359`；本 ADR 不宣稱 blocked 已修復。
- 最新 acceptance 的 program-level 結果為：`full_market_analysis` 的 canonical／alias structure 通過但 governance blocked；`single_stock_analysis` 因 `single_stock_latest_report_manifest_missing` 失敗。正式 pointer 僅接受 `D:\TWStockData\program_data\dstock_single\reports\latest_report_manifest.json`，不得以 state／data-refresh pointer 代替。
- `D:\TWStockData\program_data\dstock_daily\reports\audits\20260821\data_quality_audit_20260821.json`：已建立，SHA-256 為 `C1CC424360AC6113B51C264C2D35DF767116CC0D82693E7A46E7D4CCD8D22298`；payload `status=error`、2356 files、2352 healthy、3 warning、1 error、3 invalid OHLC、4 stale、4 PIT-safe superseded。acceptance 對此項的 `status=pass` 僅表示檔案存在且資料日相符，不表示原始資料品質通過；未執行 `--repair-invalid`。
- 本輪驗證摘要為 `115 passed`、`2 skipped`、`3 subtests`；stable path 登錄數為 296，structure-only 為全市場通過、個股 pointer 失敗。這些是契約／證據檢查結果，不取代正式分析或發布驗收。
- 上述治理檔案目前均未納入 Git 追蹤；因此沒有版本保證，不能假設可由 Git 自動回復。

### 已存在的領域證據

- `D:\TWStockData\program_data\dstock_daily\model\calibration_history.jsonl`：存在；為模型校正 append-only JSONL，最近觀察到 2026-08-23 12:12:28 +08:00。
- `D:\stock\docs\MYINVESTDESK_LOGIC_USED.md`：存在；投資／研究邏輯領域紀錄。
- `D:\stock\GitHub\docs\repository_duplicate_matrix.md`：存在；外部研究 Repo 去重紀錄。
- `D:\stock\config\mitake_ui_research_status.json`：存在；`reference_only` 的三竹 UI 研究狀態。
- `D:\stock\logs\`：存在；各工作流 action logs 的容器目錄；本回合未指定一個可取代全部 action logs 的單一 canonical 檔案。
- `D:\stock\config\shared_data_paths.json`：存在；共用資料路徑設定。
- `D:\stock\programs\cross_system_validation\README.md`：存在；既有 acceptance 驗證契約說明。

## 後果

### 正面

- 不同 AI session 可以沿著固定讀取順序接續工作。
- 來源、決策、驗證與交接各有單一入口，同時保留既有領域權威。
- 缺失或過期證據會被明確揭露，不會因文件存在而誤判正式可發布。

### 代價與限制

- 必須維護來源索引、ADR 與 Handoff 的指標；它們不是自動記憶，內容仍需在重大變更後更新。
- Git 尚未納管的檔案沒有歷史保證；若需真正可回溯，必須另行核准 Git baseline／納管範圍。
- acceptance latest 缺失或失效時，不能用另一個 latest 檔案繞過正式契約。

## 回復方式

本 ADR 只建立治理決策，不搬移或刪除領域資料。若日後要撤銷，新增一份 `superseded` 關係明確的 accepted ADR；保留本檔與原始證據供追溯。對尚未納入 Git 的治理檔案，先保存精確 diff 並取得明確授權，再逐檔回復；不得使用 broad reset、clean、覆寫或跨目錄刪除。若驗證摘要、來源索引或 Handoff 缺失，回復後狀態必須標示 `blocked`／`not_verified`，不能標示為完成。
