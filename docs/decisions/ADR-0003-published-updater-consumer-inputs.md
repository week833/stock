# ADR-0003：MIX 與個股分析的跨更新器發布資料輸入

- ID：ADR-0003
- 日期：2026-09-13
- 狀態：accepted
- 狀態語義：使用者已授權此方向；實作、驗證、資料回補及正式分析仍須分別證明，不表示 ready。
- 範圍：`D:\MIX All` 與 `D:\stock\programs\single_stock_analysis` 的資料讀取邊界。
- supersedes：ADR-0002 的上述兩個 consumer 只能讀 Central 的來源限定；其餘條款不變。
- superseded_by：N/A

## 背景

使用者於 2026-09-13 指示：「先以修復mix all 及個股分析所短缺的資料為優先項目，我要這兩個程式不管是用哪個股票更新程式撈出的資料都能使用」。
Central-only 的入口使其他更新器已取得的有效資料無法被這兩個 consumer 使用。

## 選項

- 維持 Central-only：無法達成最新授權。
- Consumer 自行呼叫資料 API：不採用，會混合更新及分析責任。
- Consumer 讀取明確、已發布、可核對的多更新器輸入：採用。

## 決定

預設依序使用 `D:\TWStockUpdater` 的 current 發布、共同 `shared_publication_v2` head，再以 `D:\TWStockData\shared_market_data` 的 Central legacy receipt 已聲明 references 補缺。兩個 consumer 自有 reader，不偽裝 Central receipt/session。

初始化固定來源 manifest／pointer 身分；只讀選定的 payload，驗證路徑位於來源 root、SHA256、size 及 JSON。已選 bytes 的 descriptor 可驗證固定 snapshot，不要求稍後 current.pointer 仍指向舊版本。

依明確 route ID 選資料；自然鍵明確時優先來源保留，次要來源補缺，衝突明列。同日期不同 financial type 不可誤合併；調整價格不得跨 vintage 拼接。

有效 PARTIAL 記錄可保留；缺欄位、日期、coverage、未知 availability/PIT 照實表達，不能標成 verified，不能創造金融值或可用時間。保留既有模型公式與特徵資格要求。

Central 完整 48 routes（required 40、optional 8）及其他程式、原始資料、排程、UI、發布與權限規則不變。Consumer 不呼叫 provider API、不寫 Central raw、不讀未發布 run 充當正式來源。

### 配置與其他更新器

既有 `D:\stock updater` 與 `D:\collect data tool` 使用共同 v2 發布格式：`D:\TWStockData\services\shared_market_data_control\publication\current_head.json` 的 `shared-market-data-current-head-v2` 指向 `shared-market-data-publication-manifest-v2`，從 top-level artifacts 的 route_id／target_relative_path／sha256／size 選 payload。Reader 新增 `kind="shared_publication_v2"`，不限定 producer 身分；head 尚不存在時記錄缺失並繼續其他来源，不偽造發布。
本輪唯讀觀察共同 head 不存在；adapter 與 focused cases 已撰寫，待集中驗證，不能宣稱已驗證兩個 producer 的 live 發布。

MIX 使用 `D:\MIX All\config\mix_config.json` 的 `data_sources.published_sources`；個股分析使用 `D:\stock\programs\single_stock_analysis\config\data_sources.json` 的 `sources`。清單順序就是來源優先序。

其他更新器可發布以下明確協定，再加入 `SourceSpec(kind="manifest_json", root=..., manifest_path=..., source_id=...)` 對應設定。以下 hash/size 為示意，必須替換成 payload 真實 bytes 的值。

```json
{
  "schema": "stock-data-publication-v1",
  "datasets": [{
    "route_id": "finmind_price_adj",
    "path": "published/price_adj.json",
    "sha256": "<payload 的 64 位小寫 SHA256>",
    "size": 123,
    "status": "SUCCESS"
  }]
}
```

對應來源設定範例：

```json
{"kind":"manifest_json","root":"D:\\AnotherUpdater","manifest_path":"published/manifest.json","source_id":"another-updater"}
```

只承諾已支援 schema／consumer 欄位形狀可讀；不承諾任意未知格式自動識別。Nested 格式須有明確 adapter，不以檔名或資料夾掃描猜測。

## 理由

讓資料 producer 與分析模型解耦，同時保留實際來源、版本、日期及缺口證據；避免資料已取得卻被 Central-only 路徑限制擋住。

## 證據

觀察日期：2026-09-13；五年回補目標截至 2026-09-11，目標日期不代表 coverage 已完成。

- Reader：`D:\MIX All\src\mixstock\stock_data_sources.py`、`D:\stock\programs\single_stock_analysis\src\stock_data_sources.py`。
- 配置：上述兩個實際 config 檔，已讀確認預設 TWStockUpdater 優先、Central 補缺。
- Focused tests：`D:\MIX All\tests\test_stock_data_sources.py`、`D:\MIX All\tests\test_published_sector_sources.py`，已撰寫；consumer 正在集中驗證，不能以本文當作通過證據。
- Updater focused validation：`D:\TWStockUpdater\logs\consumer_data_repair_validation_20260913.json` 與同名 `.log`；父代理回報 CPython 3.12.10、23 項測試、0 failures／errors。此文件作者未重跑；這不是 consumer 驗證，也不是完整五年回補通過。
- 尚未執行 live forecast；資料回補與兩個 consumer 正式可用性仍未完成驗收。
- 舊 `D:\TWStockData\services\acceptance\latest.json` 若為 blocked，不能因本 ADR 改稱新的 PASS。

## 後果

多來源可以補足有效資料，但不能將來源成功等同模型或報告成功。未支援 payload、缺失 PIT、coverage 不足或 bytes 不一致必須在相應資料／分析結果保留狀態。

## 回復方式

可明確使用 `--source-mode central` 回到原 Central 入口；不能因此忽略原 Central acceptance 狀態。
必要時只還原本輪各專案 `backups/20260913_consumer_data_repair` 對應原 bytes，保留之後使用者變更並先核對差異。
本 README 原 bytes 備份：`D:\stock\docs\decisions\backups\20260913_consumer_data_repair\README.md`。
不得 broad reset/clean、刪除或搬移資料，不觸碰 live 進程、排程、UI、來源 raw 或其他未授權檔案。
