# 共用資料路徑與執行前驗證

`D:\stock` 與 `D:\MIX All` 的正式原始／外部資料來源統一放在：

`D:\TWStockData\shared_market_data`

正式程式只從這個 canonical 根目錄讀取；MIX 的模型衍生快取、輸出與報表仍留在 MIX 專案內，不會混入原始資料。

## 正式資料分類

| 資料類型 | 絕對路徑 |
|---|---|
| 五年行情歷史（D-stock 衍生快取） | `D:\TWStockData\program_data\dstock_daily\prices\history` |
| FinMind JSON | `D:\TWStockData\shared_market_data\providers\finmind\json` |
| 美／日／韓大盤與海外產業代理（Yahoo/yfinance 共用快取） | `D:\TWStockData\shared_market_data\providers\yahoo\global_markets` |
| 交易日曆 | `D:\TWStockData\shared_market_data\official\calendar` |
| 每日市場資料 | `D:\TWStockData\shared_market_data\official\market_daily` |
| 產業特徵 | `D:\TWStockData\shared_market_data\official\sector_features` |
| 產業營運資料（D-stock 衍生快取） | `D:\TWStockData\program_data\dstock_daily\industry_operational` |
| 處置／交易限制 | `D:\TWStockData\shared_market_data\official\trading_restrictions` |
| 券商正規化快取（D-stock 衍生快取） | `D:\TWStockData\program_data\dstock_daily\brokers\normalized_cache` |
| 券商人工匯入 | `D:\TWStockData\shared_market_data\brokers\imports` |
| 券商供應商原始資料 | `D:\TWStockData\shared_market_data\brokers\provider_raw` |
| FinLab 券商快取 | `D:\TWStockData\shared_market_data\brokers\finlab_cache` |

完整註冊表：

`D:\stock\config\shared_data_paths.json`

如需覆寫整個根目錄，只可使用絕對路徑環境變數 `STOCK_SHARED_DATA_ROOT`；程式會優先讀取目前程序的環境變數，未設定時才讀取 `D:\stock\.ENV`。相對路徑或不存在的明確覆寫會直接失敗，不會偷偷建立空資料夾。

## 執行前快速確認

在檔案總管雙擊：

`D:\stock\programs\VERIFY_RUNTIME_DATA_PATHS.cmd`

或在 PowerShell 執行：

```powershell
& "D:\stock\programs\VERIFY_RUNTIME_DATA_PATHS.cmd"
```

檢查內容包括註冊路徑、代表性資料、MIX 設定，以及是否誤指向備份、舊複本、輸出或報表資料夾。共用原始資料的各分類（FinMind 股票資訊、全球市場、交易日曆、處置／交易限制、每日市場資料、產業特徵）改由 updater 發布指標 `_metadata\stock_data_current.json` 佐證：指標必須是 `stock-data-publication-v1`，各分類對應的路由必須存在，其物件必須位於 `_objects` 內、檔名即自身 SHA-256、大小與指標宣告相符。物件可達數 GB，所以只做大小檢查，不重算雜湊；分類資料夾內遺留的舊 JSON 不再當作證據。價格歷史衍生快取仍以資料夾內一份可讀 JSON 為樣本。

指標可為 `stock-data-publication-v1` 或 `-v2`（與 `shared\stock_data_paths.py` 相同的接受集合；兩者的路由層欄位一致），其他 schema 一律失敗。各路由的 `status` 規則（updater 只會把 `SUCCESS` 與 `PARTIAL` 帶進指標，其餘值視為指標損毀）：

| 指標中的 `status` | 本檢查的處理 |
|---|---|
| `SUCCESS` | 通過，不回報。 |
| `PARTIAL` | 物件存在、大小相符才算可讀，**預設通過但一律回報**：每條路由一則 `INFO`（`publication_route_partial`，不影響回傳碼）、結果 JSON 的 `publication.partial_routes` 列出路由、各路徑項目的 `publication_partial` 為 `true`、`totals.notices` 計數。加上 `--strict-publication` 時每條 PARTIAL 路由都是錯誤（回傳碼 1）。 |
| 缺少、其他字串或非字串 | 失敗（`publication_route_status_invalid`）。 |

這個檢查回答的是「消費端讀得到資料嗎」，不是「資料完整嗎」：與 `shared\stock_data_paths.py` 的發布指標驗證一致，PARTIAL 本身不擋讀取；覆蓋缺口的追查看 `VALIDATE_MARKET_DATA_QUALITY.cmd` 與各路由 sidecar 的 coverage 證據。物件損毀（缺檔、大小不符、不在 `_objects`、檔名不是自身 SHA-256）無論 `status` 為何一律失敗。機器可讀結果寫入：

`D:\TWStockData\program_data\dstock_daily\state\runtime_path_health_latest.json`

回傳碼：

- `0`：全部通過（可含 `INFO` 回報，例如 PARTIAL 路由）。
- `1`：資料路徑、檔案或設定失效（含 `--strict-publication` 下的 PARTIAL 路由）。
- `2`：只有警告（選用分類的問題）。

## 已退役的遷移入口

`RUN_MIGRATION.cmd` 與 `MIGRATE_SHARED_REFERENCE_DATA.ps1` 僅保留名稱相容性；現在只呼叫上述快速驗證，不再搬移、複製或覆寫任何資料。

`VERIFY_SHARED_REFERENCE_DATA.ps1` 的預設行為也只呼叫目前有效的 runtime path health-check，避免增量更新後把舊雜湊誤報為資料損毀。只有需要稽核首次切換時留下的歷史 manifest，才明確執行：

```powershell
& "D:\stock\programs\shared_data_management\VERIFY_SHARED_REFERENCE_DATA.ps1" -HistoricalBaseline
```

歷史基準可能隨正式資料持續增量更新而自然變舊，不能取代 runtime path health-check。

## Current owner-seal v2 驗證（正式 acceptance evidence）

目前中央資料的唯一 owner-seal 根目錄固定為：

`D:\TWStockData\shared_market_data`

owner manifest 固定為：

`D:\TWStockData\shared_market_data\_cowork_shared_data_manifest.json`

需要核對目前中央資料完整檔案雜湊時，明確使用：

```powershell
& "D:\stock\programs\shared_data_management\VERIFY_SHARED_REFERENCE_DATA.ps1" -CurrentOwnerSeal
```

這個模式只接受 `cowork-shared-data-owner-seal-v2` / `schema_version=2`，會核對 destination identity、exclusion policy、canonical inventory 的 file count／總 bytes／aggregate／per-file hashes、每個檔案的 size／mtime／SHA-256，以及 reparse、traversal、duplicate、unknown lock 與 owner manifest 自身的 SHA-256。驗證結果的契約 schema 為 `shared-data-owner-seal-verification-v1`，預計原子寫入：

`D:\TWStockData\program_data\dstock_daily\state\shared_market_data_control\verification_latest.json`

未執行 `-CurrentOwnerSeal` 前，不會產生或覆寫該 verification evidence；中央更新或 reseal 進行中也不得同時執行完整驗證。`-HistoricalBaseline` 仍只表示歷史稽核，預設模式仍是 runtime path health-check。
