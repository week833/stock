# ADR-0002：Central 全聯集與消費者中立架構

- ID：ADR-0002
- 日期：2026-08-27
- 狀態：accepted
- 狀態語義：`accepted` 表示本架構決策已獲使用者核准；不表示資料回補、正式分析、報告、三竹／Telegram 發布或 acceptance 已通過。
- 範圍：`D:\TWStockData` Central 資料蒐集與發布、`D:\stock` 消費程式、MIX／TWMIX／Cowork／Premarket 邊界、Telegram、scheduler 與後續隔離治理
- supersedes：N/A
- superseded_by：N/A

## 背景

系統同時包含 Central 資料蒐集、全市場分析、個股分析、MIX／TWMIX／Cowork／Premarket 工作流、三竹更新與 Telegram 發布。若由各程式自行決定要抓哪些資料，會造成重複 API 請求、資料缺口、不同資料日或不同來源版本；若以程式分類取代資料契約，也會讓未來新程式無法安全取得既有資料。

使用者已釐清：資料蒐集內容不得因程式整理或退役而減少；程式可以依自己的分析需要尋找資料，但不能改變 Central 的完整資料聯集。Telegram、環境安裝、資料驗證、維護與外部研究工具也不是股票分析程式，不能自動納入刪除範圍。

## 選項

### 選項 A：每個分析程式自行更新自己的資料

不採用。這會重複抓取、分散來源權威，並使 MIX ALL、個股分析或日後新程式可能取得不完整資料。

### 選項 B：依程式建立不同的 Central 資料分類或縮減聯集

不採用。程式分組不是資料保存權限；不得因 consumer mapping、程式隔離或退役而移除 route、降級 optional route 或縮減 acceptance coupling。

### 選項 C：Central 保留完整聯集，catalog 不含程式分組，consumer 以 explicit route IDs 選取

採用。Central 一次依單一來源契約更新完整聯集；每個 consumer 從同一個 immutable receipt／session 依自身明確 route IDs 讀取需要的子集。資料權威與程式維護邊界分離。

## 決定

### 1. Central 完整資料聯集

1. `D:\TWStockData\contracts\shared_market_data\requirements_v1.json` 是 Central route 契約的唯一落盤 authority。完整聯集固定為 48 routes：required 40、optional 8。
2. 48 routes 全部標記為 `CENTRAL_UNION_RETAINED`。任何 route 的 producer、adapter、fields、PIT、freshness、coverage、target、history 與 required／optional 語意，不得因程式狀態、consumer mapping、隔離或退役而刪除、縮減或降級；不得把 optional 擅自改成 required。
3. Central 一次更新全體程式可能需要的 union；consumer 不得各自呼叫 provider、API 或 legacy raw fallback。consumer 只可從 Central 正式發布資料選取自己的分析子集。
4. Central technical catalog 採 app-neutral schema：保留 route、provider、adapter、schema、fields、PIT、freshness、coverage、target、history、producer 與 hash lineage；不得包含 `formal_consumer_mappings`、applications、entrypoints、consumer subsets 或其他程式分組資訊。
5. consumer 必須提供明確、唯一、registry-declared 的 `route_ids`。不得以 catalog 的程式分組推導 generic selector，也不得以 glob、directory scan、latest fixture、legacy path 或 cross-run artifact 代替 receipt-declared target。
6. 所有正式 consumer 由同一個 immutable Central evidence session 綁定 receipt、registry raw／canonical hashes、payload／sidecar bytes、target、size、SHA、native identity、PIT、freshness、coverage、run lineage 與 TOCTOU 檢查；缺失或漂移一律 fail-closed。

### 2. 保留的正式程式與服務邊界

1. `D:\MIX All` 是唯一保留的全市場分析與報告維護主體，並由 MIX 自有三竹操作／更新群組功能負責其 Mitake 流程。
2. `D:\stock\programs\single_stock_analysis` 保留為個股分析程式；它從 Central 選取自己的 explicit route IDs，不自行抓取 provider 資料。
3. `D:\stock\programs\telegram_report_delivery` 與 Telegram 全部目錄保持 protected；Telegram 不是股票分析程式，不納入刪除或退役範圍。
4. Central、環境安裝、資料驗證、維護及外部研究工具保留其獨立治理角色，不自動列入程式刪除。
5. `D:\stock\programs\full_market_analysis`、`D:\cowork` 的分析流程、`D:\claude code\twmix` 的分析流程、Premarket analysis 與舊 scheduler 現階段只可精確隔離規劃；本 ADR 不授權現在移動或永久刪除。若日後隔離，僅可移動可辨識的程式檔案，不得移動任何資料、receipt、checkpoint、journal、報告、模型或環境；永久刪除必須另取得第二次明確授權。

### 3. Scheduler 邊界

1. scheduler code 的固定位置為 `D:\TWStockData\services\workflow_scheduler`，且只負責編排、鎖定、排程與狀態協調。
2. scheduler 不擁有分析公式、模型、provider 抓取邏輯或程式專屬資料；Central 的唯一 owner 與排程安全門檻仍由 Central 契約和 acceptance 定義。
3. 排程變更仍須查核 exact task identity、action、狀態與 owner；不得以猜測的新名稱取代現有 task。Central 02:00 永久維持 Disabled 的既有規則仍有效。

### 4. Protected 資產與工作樹

1. `D:\TWStockData\shared_market_data`、program data、state、receipts、staging、acceptance、migration evidence、checkpoint／journal、正式報告、模型、虛擬環境與設定均為 protected；本 ADR 不授權刪除、覆寫、重抓或重新分類資料。
2. `D:\stock` 及相關 dirty worktree 保留使用者既有變更；不得 reset、clean、revert、stage、commit 或 push。回復只能針對本次明確辨識且獲授權的檔案。

### 5. 決策與執行狀態分離

本 ADR 的 accepted 只核准架構方向。每次實作仍需依既有 source freeze、Central publishable receipt、consumer formal runtime、MIX／Mitake、Telegram 與 `latest.json` acceptance gate 分別驗證；任何靜態 inspection、audit 或 exit code 0 都不能單獨宣稱正式完成。

## 理由

- 完整 Central union 讓一次更新即可服務 MIX ALL、個股分析、既有 consumer 與日後新程式，避免各自抓取造成 quota、PIT、日期與版本不一致。
- app-neutral catalog 將資料權威與程式生命週期解耦；新增或隔離程式不會改變 48-route 資料保存契約。
- explicit route IDs 讓 consumer 可以讀取自己的資料子集，同時保留未知、重複、空值與 cross-run 選取的 fail-closed 邊界。
- MIX 自有 Mitake 操作可使分析、報告與群組更新責任集中；Telegram 則維持獨立發布服務，不被分析程式整理誤刪。
- scheduler 獨立後可避免把排程與任一股票分析程式的子目錄生命週期綁定，並維持單一 Central owner。

## 證據

以下是本決策使用的精確證據位置；它們描述目前可核對的基線，不把未完成 runtime 當成通過：

- `D:\TWStockData\contracts\shared_market_data\requirements_v1.json`：觀察日 2026-08-27；schema／version 為目前 Central 契約，48 routes、40 required、8 optional，route contracts 40；原始檔 SHA-256 `712C90E4940FF8ABF79870C0208FFF7750F14A4A9A7D68606452842116D9CC68`。
- `D:\TWStockData\services\market_update\market_update.py`：Central producer／union 實作入口；僅作來源與 owner 邊界證據，本 ADR 不改寫其資料內容。
- `D:\stock\programs\shared\central_publication_reader.py`：shared Central publication authority；目前已具 app-neutral catalog 與 explicit route selector 的 source 變更，仍待窄測試與正式 runtime 驗證。
- `D:\stock\programs\full_market_analysis\src\central_publication_reader.py`：full-market 相容 shim；session、formal gate 與 publication read 委派 shared authority，仍待正式 consumer 驗收。
- `D:\stock\programs\single_stock_analysis`、`D:\MIX All`、`D:\stock\programs\telegram_report_delivery`：保留範圍與 consumer／發布邊界；本 ADR 不刪除或移動其內容。
- `D:\TWStockData\services\workflow_scheduler`：scheduler 獨立目標根目錄；需由後續受限實作與排程驗收確認實際入口、task action 與 owner。
- `D:\TWStockData\services\acceptance\latest.json`：目前檔案 `generated_at=2026-08-23T21:27:01.519728+08:00`、`overall_status=blocked`、`mode=reports`、`fixture=false`；不是本次架構 accepted 的 runtime PASS。
- `D:\stock\docs\HANDOFF.md`、`D:\stock\docs\SOURCE_REGISTRY.md` 與本目錄 README：既有交接、來源權威與 ADR 治理入口；缺少 current receipt、process 或 acceptance 證據時仍須 fail-closed。

## 後果

### 正面

- Central producer 可集中處理完整資料 union，降低重複 API 與資料缺漏風險。
- MIX ALL 與個股分析可各自維護分析邏輯，但共用同一份 immutable、可追溯的 Central publication。
- 程式隔離或日後新增 consumer 不會自動改變資料保存契約；Telegram、scheduler 與工具的生命週期清楚分離。
- receipt、sidecar、hash、PIT、coverage 與 lineage 成為跨程式一致的正式門檻。

### 代價與限制

- Central union 更新成本由 Central 承擔；即使目前沒有 consumer 使用某個 optional route，也必須保留其契約與證據。
- 每個 consumer 必須維護自己的 explicit route IDs 與對應驗收；catalog 不會替程式自動推導子集。
- 隔離前必須建立精確檔案清單、引用／lineage proof、rollback marker 與影響驗證；永久刪除需要額外授權。
- runtime acceptance 尚未完成時，任何程式可執行或 source 靜態通過都不能視為正式發布資格。

## 回復方式

1. 本 ADR 不執行資料移動或刪除。若只需撤銷架構方向，應新增一份關係明確的 accepted ADR 取代本 ADR，不覆寫歷史決策。
2. 若經另一個明確授權的隔離任務建立 quarantine，回復只可依該任務產生的 exact manifest／lineage／rollback marker 將程式檔案移回原位置；Central data、receipt、checkpoint、報告、模型與 Telegram 檔案不得隨之移動。
3. quarantine 或刪除候選在中央資料、MIX ALL、個股分析、Telegram 與 scheduler 影響驗證完成前保持可恢復；永久刪除需第二次明確授權，且不得以 broad reset、clean、revert 或跨目錄刪除代替。
4. 若 source seal、receipt、acceptance 或 owner 證據缺失，回復／隔離流程維持 `blocked` 或 `not_verified`，不得以文件狀態推定正式成功。
