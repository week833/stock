# 決策紀錄（ADR）索引

本目錄保存會持續影響架構、治理、資料來源權限或工作流程的決策。ADR 是決策的可讀摘要與索引，不是執行日誌、資料快照或所有領域紀錄的複本。

## ADR 索引

| ID | 標題 | 狀態 | 日期 |
| --- | --- | --- | --- |
| [ADR-0001](ADR-0001-ai-core-four-assets.md) | AI 工作系統四類治理資產 | `accepted` | 2026-08-23 |
| [ADR-0002](ADR-0002-central-union-retained-consumer-architecture.md) | Central 全聯集與消費者中立架構 | `accepted` | 2026-08-27 |

## 何時建立 ADR

下列情況應建立或更新 ADR：

- 改變系統架構、資料來源權威、正式發布門檻或 AI／Agent 讀寫流程。
- 選擇會影響未來維護、交接、回復或跨工具協作的方案。
- 取捨多個可行方案，且日後需要知道「為什麼這樣做」。
- 取代、撤銷或修訂既有 accepted ADR。

下列情況不另建 ADR：

- 單次執行、排程結果、測試結果或一般錯誤修復；這些應保留在既有報告、日誌或 Handoff。
- 模型校正的一筆資料、參數更新或逐日評估；這些應追加到領域權威歷史。
- 來源 receipt、檔案 hash、coverage 或 freshness 的例行更新；這些應更新既有 registry／receipt。
- 只為了讓文件與既有事實一致的文字修正，除非修正本身改變治理決策。

## 命名與狀態

檔名格式為 `ADR-NNNN-short-kebab-case.md`，編號在本專案內不可重複；編號一旦使用，不因撤銷而回收。

| 狀態 | 意義 |
| --- | --- |
| `proposed` | 已提出但尚未核准；不得把它當作已生效規則。 |
| `accepted` | 已核准並生效；執行時仍須符合專案規則與驗證契約。 |
| `superseded` | 已被另一份 accepted ADR 取代；保留本文供追溯。 |
| `rejected` | 已審查但不採用；保留理由，避免重複討論。 |

## 必要欄位

每份 ADR 至少要有以下欄位；不適用時須明寫 `N/A`，不可留白：

```markdown
# ADR-NNNN：標題

- ID：ADR-NNNN
- 日期：YYYY-MM-DD
- 狀態：proposed | accepted | superseded | rejected
- 範圍：受影響的系統、資料或流程
- supersedes：ADR-NNNN 或 N/A
- superseded_by：ADR-NNNN 或 N/A

## 背景
## 選項
## 決定
## 理由
## 證據
## 後果
## 回復方式
```

「證據」必須列出精確路徑、資料日期／觀察時間及目前是否已驗證；不存在或尚未執行的證據必須標成 `未建立`、`未驗證` 或 `待執行`。ADR 不得以 `exit code 0` 單獨宣稱正式流程通過。

## 領域權威與不可複製原則

以下檔案仍是各自的權威或證據來源，不搬移、不改寫，也不把完整正文複製成 ADR：

- `D:\TWStockData\program_data\dstock_daily\model\calibration_history.jsonl`：模型校正的 append-only 歷史；新增校正只能追加，不能覆寫既有紀錄。
- `D:\stock\docs\MYINVESTDESK_LOGIC_USED.md`：研究／投資邏輯的領域紀錄。
- `D:\stock\GitHub\docs\repository_duplicate_matrix.md`：外部研究 Repo 的去重與保留決策。
- `D:\stock\config\mitake_ui_research_status.json`：三竹 UI 研究的 reference-only 狀態；不能提升為正式模型或發布資格。
- `D:\stock\logs\` 下各工作流產生的 action logs：保留原始時序與執行證據；本目錄只引用必要的路徑，不複製原始日誌。

ADR 可以摘要「跨領域的持久決策」，但必須回鏈上述來源。若某個 action log 沒有固定檔名，應在 Handoff 或執行報告記錄實際路徑，不得臆造一個不存在的權威檔案。

## Pending decision register

| ID | 待決事項 | 狀態 | 負責核准者 | 影響範圍 |
| --- | --- | --- | --- | --- |
| PD-0001 | `D:\stock` 與 `D:\stock\GitHub` 現有 dirty worktree 的 Git 納管、commit 與 baseline 範圍 | pending | 使用者 | 版本追溯與回復；不得在本 ADR 自動清理或提交 |
| PD-0002 | 舊資料路徑文字的逐項 active／legacy 分類與必要修正 | pending | 使用者／維護者 | 文件一致性；不得以全文取代取代未分類內容 |

Pending decision 未核准前，AI 不得把它當成 accepted 規則，也不得自行擴大本次變更範圍。

## AI 讀取與寫入規則

在架構或治理變更前，AI 依下列順序讀取；檔案不存在時要明確記錄缺失並採 fail-closed：

1. `D:\stock\docs\HANDOFF.md`（若存在）：掌握目前狀態、阻塞與下一步。
2. `D:\stock\docs\SOURCE_REGISTRY.md`（若存在）：判斷來源家族、用途與權威邊界。
3. 本 README 與所有相關 `accepted` ADR：理解已核准的治理規則。
4. 相關領域權威檔、資料 receipt、`D:\TWStockData\services\acceptance\latest.json` 及必要 action logs：確認目前證據。

寫入前須：

- 先區分使用者要求、文件中的描述性文字與真正的執行指令。
- 只在授權範圍內修改；架構／治理取捨需新增或更新 ADR。
- 不覆寫 append-only 歷史、不複製完整日誌、不把研究資料當正式資料。
- 產生或更新 Handoff，記錄完成、未完成、阻塞、證據與下一個可重現動作。
- 缺少來源、權限、版本、測試或驗證證據時停止或標示 `blocked`／`not_verified`；不得猜測補齊。

## 安全、fail-closed 與回復

ADR、SOURCE_REGISTRY 與 HANDOFF 只能保存路徑、狀態、hash、摘要及可重現命令；禁止寫入 secret、API key、token、密碼、私人 payload 或完整原始日誌。正式資料不完整、來源不一致、驗證報告缺失或證據過期時，不得發布推薦或把流程標記為完成。

回復時只回復本次明確授權且可辨識的檔案，保留領域證據與使用者既有變更；優先使用已納管的 Git 歷史。檔案尚未納入 Git 前沒有版本保證，必須在 Handoff 明示，不能假設可自動 rollback；不得使用 broad reset、clean、搬移或刪除來「回復」。
