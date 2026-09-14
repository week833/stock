# D-stock 交接狀態同步頁

此頁是 `D:\stock\docs\HANDOFF.md` 的**去敏感化狀態摘要**，目的只有一個：讓沒有 `D:\` 存取權的環境（手機 app、雲端 session）也能讀到交接目前到哪裡、下一步要驗什麼。

## 定位與邊界

- 本頁**不是** system of record。正式資料與報告資格一律以本機中央 receipt、manifest 與 acceptance 為準。
- 本頁**不取代** `D:\stock\docs\HANDOFF.md`、`D:\stock\docs\SOURCE_REGISTRY.md` 與 ADR-0001；那三份仍是僅限本機的治理檔。
- 本頁內容是**特定時點的快照**。`snapshot_at` 以外的任何時間，都不得把本頁當成現況。
- 讀完本頁不等於通過驗收。要宣稱正式完成前，必須在本機重讀最新 acceptance。

## 本頁不放的內容

- 任何來源的 token、帳密、cookie、完整 API 回應
- raw dataset、signed URL、即時行情鏡像、可識別使用者資訊
- 中央資料的實際數值、逐檔內容或完整 hash

只放狀態欄位、阻塞原因與下一步動作。

## 狀態快照

```text
snapshot_at=未填寫
updated_by=未填寫
```

| 項目 | 狀態 | 備註 |
|---|---|---|
| 中央 receipt（market_update） | 未填寫 | |
| 資料覆蓋 coverage | 未填寫 | |
| PIT 判定 | 未填寫 | |
| 報告 manifest（dstock_daily） | 未填寫 | |
| artifact verifier | 未填寫 | |
| acceptance | 未填寫 | |

狀態值只用四種：`pass`／`pending`／`blocked`／`未填寫`。任一項為 `未填寫` 時，本頁都不足以支持任何正式宣稱。

## 看完交接後的驗證順序

依 `week833/finmind-twstock-research-skill` 的 `skills/finmind-twstock-research/references/dstock-boundary.md`：

1. 不把 `HANDOFF.md` 記載的 acceptance 當現況——那是特定觀察時點的證據。
2. 資料資格：讀中央 receipt `D:\TWStockData\services\market_update\receipts\latest_manifest.json`，檢查 status、publishability、expected_asof、coverage、PIT 與 hash。
3. 報告資格：讀 `D:\TWStockData\program_data\dstock_daily\reports\latest_report_manifest.json`、artifact verifier 與 `D:\TWStockData\services\acceptance\latest.json`。
4. `D:\stock\config\shared_data_paths.json` 只定義 routing，不是資料內容或 freshness 證據；不得以「路徑存在」推論資料有效。
5. 正式共用原始資料是 `D:\TWStockData\shared_market_data`；本頁與任何研究 skill 都不直接更新、修復、搬移、刪除或另建它。

## 判定規則

- receipt、manifest、source registry、資料日、coverage、PIT 或 schema 互相矛盾時，標 `pending` 或 `blocked`，fail-closed。
- 不得以另一份資料、或「現有路徑存在」來繞過缺少的證據。
- 阻塞項目要寫清楚卡在哪一步、需要誰處理。

### 目前阻塞

| 項目 | 卡在哪 | 需要的動作 |
|---|---|---|
| 未填寫 | | |

## 更新方式

本頁只能由**有 `D:\` 存取權的本機 session** 更新：

1. 在本機讀最新 receipt／manifest／acceptance。
2. 只把狀態值與阻塞原因寫回本頁，不複製資料內容。
3. 更新 `snapshot_at` 與 `updated_by`。
4. commit 並 push 到 GitHub，手機與雲端 session 才讀得到。

雲端與手機 session 對本頁**唯讀**：它們碰不到 `D:\`，無法驗證任何一項，也不得代填狀態。

## 已知限制

- 本頁的新鮮度取決於有沒有人在本機更新。長時間未更新時，`snapshot_at` 就是它唯一誠實的說明。
- 本機電腦離線時，Remote Control 的本機 session 無法接回，本頁也不會有任何更新。
