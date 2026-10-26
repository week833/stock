# 共用核心

本資料夾只放會被多套現行程式共同引用的檔案；不在此保留第二份行情、
模型或全市場執行器。

## 資料 owner 與 refresh 邊界

- 正式共用行情唯一由中央 owner `D:\TWStockData` 維護，canonical root 為
  `D:\TWStockData\shared_market_data`，完整契約保留 48 routes。此資料夾的
  reader／adapter 只能讀取中央 receipt 宣告的內容，不能自行建立或發布另一份資料。
- `stock_daily_contract.py`：單股、MIX、Top10 與 Telegram 共用的 C0 已發布輸入讀取、模型綁定與
  forward 橋接。版本／綁定差異只記 `version_labels`，報告帶 `data_time`（以實際讀到的資料時間為準）；
  各市場以自己的資料日與 253 日窗推論（`panel_by_market`、`predict_c0_by_market`），不排除落後市場。
  完整性與竄改仍 raise `C0IntegrityError`；寫入 forward 時訊號日晚於該市場分析資料日會 raise
  `c0_forward_prediction_after_market_asof`（防前視）。
- `stock_daily_linear.py`（C0 收盤至收盤基準模型）、`stock_daily_forward.py`（forward 帳本）、
  `stock_daily_price_quality.py`、`stock_daily_calendar*.py`、`stock_daily_application_scope.py`、
  `stock_daily_contract_adoption.py`、`stock_daily_active_evidence.py`：C0 的模型、帳本、價格品質、
  日曆、scope 與 adoption 驗證。
- 開盤前版 H1（ADR-0008，與 C0 並行）：
  - `stock_daily_preopen.py` 是產製端，指令有 train、calibrate、predict、settle、status。它只讀中央不可變 object，寫入 `D:\TWStockData\program_data\stock_daily_preopen`（模型指標、快照、`state\forward.sqlite3`）。
  - 配方：v1 `h1pre_final_nogc_xs` 與 v2 `h1pre_v2_ens` 並存，模組預設仍是 v1；2026-10-22 起正式資料根啟用 v2（研究 `D:\TWStockData\program_data\stock_daily_c0\candidates\h1_improve_20261021\`，使用者核准）。每月例行重訓沿用「目前啟用模型」的配方；換配方必須 `train --recipe <名稱> --force --authorization "<核准內容>"`，缺任一項即拒絕。v2 的校準種子在 `models\seeds\`，使用前驗證 sha256。快照 manifest 與 `models\current.json` 記錄 `recipe`（`name`、`frozen_sha256`），消費端依此選擇揭露文字。回復 v1：`train --recipe h1pre_final_nogc_xs --force --authorization "回復 v1"`（約 40 秒）。
  - `stock_daily_preopen_data.py` 負責讀中央資料、把 5 年歷史 head 與最新 head 拼接，並判斷美股休市或缺漏。
  - `stock_daily_preopen_features.py` 是逐行移植自校正研究的特徵程式，`tests\test_stock_daily_preopen.py` 有一致性測試（需設 `PREOPEN_PARITY=1`）。
  - `stock_daily_preopen_reader.py` 是消費端唯一可用的唯讀 API，不依賴 sklearn；`stock_daily_preopen_fixture.py` 只供測試。
  - `RUN_PREOPEN_DAILY.cmd` 是每日入口，依序執行 settle、月度 train（未滿 21 個交易日自動跳過）、predict；安排在中央更新之後、08:30 之前執行；前一晚美股若仍缺漏，照常以缺值推論並標示。
  - 前一晚美股缺漏時，predict 預設沿用缺值推論並發布，manifest 保留 `us_overnight` 日期與 `degraded=true`；`describe()` 顯示美股資料最新日期（YYYYMMDD，無資料為「未提供」）。`--allow-missing-us` 保留接受以相容舊命令，不改變發布行為。
  - t 日籌碼（法人、融資券、當沖、本益比、外資持股）任一路由的中央最新日期早於訊號日或無法取得時，predict 仍會發布，但在快照 manifest（`latest.json` 與 `<run>.json`）寫入 `chips_freshness`（`status=stale`、`stale_routes`、各路由日期與中央狀態 `route_status`）並設 `degraded=true`，讀取器 `describe()` 會加註最新資料到幾號，例如「t 日籌碼未更新：融資券最新到 20261015（以舊值推論，準確度可能下降）」。使用者裁示（20261018）：不設覆蓋率門檻、有資料就可用，不是最新就在報告顯示最新資料到幾號；`--require-fresh-chips` 保留接受以相容舊命令，不再阻擋 stale 發布。中央標示 PARTIAL（資料不完整）的路由不算 stale、不阻擋，只在說明文字顯示成功／應有數量（例如「t 日籌碼資料不完整（中央回報成功/應有）：法人 100/1215」）；manifest 的 `chips_freshness` 另有 `coverage` 與 `partial_routes`。單股 H1 區塊（`chips_note_zh`）與 Telegram 個股訊息會顯示同一段說明。
- `path_contract.py`：中央路徑契約；主版本 1.x／2.x 照常載入，非已知版本只發
  `PathContractVersionWarning` 並記 `path_contract_version_unknown`，其他主版本或格式錯誤仍拒絕。
- 舊的 `tw_market_data_analysis_core.py` 與 `global_market_data.py` 已不在本資料夾。
- 本地全市場批次 updater `UPDATE_GLOBAL_MARKETS.cmd` 已退役並隔離；refresh
  現在採 `central_owner_only`／fail-closed，禁止以舊入口繞過中央 owner。

## 日期格式

- 日期一律 `YYYYMMDD`（8 位數字）：命令列參數、提示、主控台、報告與 Telegram 顯示的日期都是
  8 位數字；舊的 `YYYY-MM-DD` 仍可輸入。共用輔助模組是 `date_format.py`（`import date_format as cd`，
  語意與收集資料工具 `app\date_format.py` 相同，測試在 `tests\test_date_format.py`）。
- 讀取端兩種拼寫都收、比較用日期值；JSON 數字（例如 `20260105`）不是日期。已發布資料不改寫；
  機器欄位、快取／識別鍵與交給中央更新程式的 `--expected-asof`、環境變數交接維持 `YYYY-MM-DD`。

## Runtime 邊界

- `find_python.cmd` 依模式尋找依賴：`default` 只檢查 Python，
  `analysis` 檢查分析依賴，`test` 檢查 pytest，`analysis-test`
  同時檢查兩者。`analysis`／`analysis-test` 只接受根 `D:\stock\.venv\Scripts\python.exe`
  （`STOCK_ANALYSIS_PYTHON` 若有設定也必須指向同一路徑）；現行 Telegram service 與單股入口都使用這個環境。
- `CHECK_ENV.cmd`：檢查共用依賴與現行程式分類是否可載入；它不會下載資料、
  執行退役的 D-stock full-market 或舊 daily scheduler。

業務模型與個別測試應放回各自程式資料夾，不放入共用核心。
