# Telegram 報告傳送

程式與測試保留在 `D:\stock\programs\telegram_report_delivery`，獨立於全市場與單股分析程式。
專用 `.ENV`、Python/.venv、服務 executable 與程式碼仍保留原位；可變資料依
`D:\TWStockData\config\path_contract.json` 寫入中央 namespace（契約版本主版本 1.x／2.x 照常載入，
非已知版本只發出警告並記 `path_contract_version_unknown`；其他主版本或格式錯誤仍拒絕）。全市場報告只唯讀載入
MIX C0 pointer `D:\TWStockData\program_data\mix\reports\LATEST_C0_RUN.json` 及其 manifest 指定的
附件，不修改預測、報告或歷史快取。舊的 D-stock 全市場報告傳送（`latest_report_manifest.json`）已退役：
`telegram_report_sender.py` 只保留 `--configure`／`--check` 憑證工具，`RUN_SEND_LATEST_REPORT.cmd`
改為轉呼叫 `RUN_SEND_MIX_REPORT.cmd`。

中央 runtime 位置：`D:\TWStockData\services\telegram\data`（狀態與鎖定）、`jobs`（個股
工作輸出）、`logs`（傳送與 Bot 日誌）、`service_logs`（WinSW service 日誌）。

日期一律 `YYYYMMDD`：Telegram 訊息、caption、日誌時間與命令列說明顯示的日期都是 8 位數字；
`--expected-asof`／`--target` 與讀入的 manifest、receipt、報告 JSON 日期，`YYYYMMDD` 與舊的
`YYYY-MM-DD` 都收（用日期值比較，JSON 數字不算日期）。狀態檔與傳送證據仍記錄 `YYYY-MM-DD`
（機器欄位，不改寫）；Bot 不再把預期資料日以環境變數交給分析程式，分析以實際讀到的資料為準。

## 執行入口

| 用途 | 入口 |
|---|---|
| 設定並驗證 Token / Chat ID | `CONFIGURE_TELEGRAM_DELIVERY.cmd` |
| 檢查既有 Telegram 設定 | `CHECK_TELEGRAM_DELIVERY.cmd` |
| 手動傳送 current MIX C0 報告 | `RUN_SEND_MIX_REPORT.cmd`（`08_傳送MIX正式報告.cmd` 與相容名稱 `RUN_SEND_LATEST_REPORT.cmd` 都轉呼叫它） |
| 檢視 MIX 星期一至五 08:00 排程定義（唯讀） | `SHOW_MIX_WEEKDAY_0800_TASK.cmd` |
| 安裝／驗證 MIX 星期一至五 08:00 排程 | `INSTALL_MIX_WEEKDAY_0800_TASK.cmd` |
| 啟動個股指令 Bot | `RUN_COMMAND_BOT.cmd` |
| 安裝個股指令登入自啟 | `INSTALL_COMMAND_BOT_TASK.cmd` |
| 安裝可在登出後執行的 Windows Service | `INSTALL_COMMAND_BOT_SERVICE.cmd` |
| 唯讀檢查或精確重啟／換版 command Bot Service | `RESTART_COMMAND_BOT_SERVICE.cmd` |
| 查看個股指令狀態 | `CHECK_COMMAND_BOT_STATUS.cmd` |
| 移除個股指令登入自啟 | `REMOVE_COMMAND_BOT_TASK.cmd` |
| 移除 Windows Service | `REMOVE_COMMAND_BOT_SERVICE.cmd` |
| 執行本套件測試 | `RUN_TESTS.cmd` |

MIX 傳送內容是一則摘要訊息，加上 Excel 報告（`MIX_C0.xlsx`）一個附件。manifest、payload JSON 與
HTML 仍在傳送前逐一驗證 path／size／SHA-256，但只保留在本機、不上傳（全市場 payload 超過 100 MB、
HTML 超過 Telegram 49 MB 單檔上限）。manifest 未列出的報告記 `report_artifact_missing` 標籤；沒有
Excel 時只寄摘要。附件 caption 列出報告資料日與報告產生時間。

## MIX C0 傳送

`RUN_SEND_MIX_REPORT.cmd`（`mix_report_sender.py`）與工作日 08:00 排程讀取 `LATEST_C0_RUN.json`
指定的 run，驗證 pointer／manifest／payload 的 schema、run_id、資料日，以及每個附件的 exact
path／size／SHA-256。摘要列出報告資料日、報告產生時間、producer 宣告的資料發布時間、報告產生時
是否為最新資料，以及一行「目前」資料狀態。

不阻擋原則：報告資料日比目前最新資料舊（或較新）時照常寄出，摘要同時標出兩個日期並記
`newer_data_available`／`current_data_older_than_report`；綁定或版本差異、來源非 COMPLETE、
報告產生後普通股名單變動等只列入 `unmet_conditions` 與標籤。手動指定 `--expected-asof` 而與報告
資料日不符時，立即以 exit 4 結束、不輪詢，訊息會列出實際資料日。仍會停止（exit 2）的是完整性與內容
錯誤：schema／ref／hash 不符、附件超過 49 MB、明確 `--target` 找不到相符的 H1 目標日，以及 ETF／
非普通股排除無法證明或被違反。

## 設定

互動執行 `CONFIGURE_TELEGRAM_DELIVERY.cmd`，或先在同一個 PowerShell 工作階段設定：

```powershell
$env:TELEGRAM_BOT_TOKEN = $Token
$env:TELEGRAM_CHAT_ID = $ChatId
& 'D:\stock\programs\telegram_report_delivery\CONFIGURE_TELEGRAM_DELIVERY.cmd'
Remove-Item Env:TELEGRAM_BOT_TOKEN, Env:TELEGRAM_CHAT_ID -ErrorAction SilentlyContinue
```

驗證成功後會安全更新本資料夾的 `.ENV`：

```dotenv
TELEGRAM_DELIVERY_ENABLED=true
TELEGRAM_BOT_TOKEN=
TELEGRAM_CHAT_ID=
```

Token 不會出現在日誌中。

## 手動操作

```powershell
# 不連線，只在本機驗證 current MIX C0 refs 與附件
& 'D:\stock\programs\telegram_report_delivery\RUN_SEND_MIX_REPORT.cmd' --dry-run

# 傳送最新且尚未傳過的版本
& 'D:\stock\programs\telegram_report_delivery\RUN_SEND_MIX_REPORT.cmd'

# 強制重送相同版本
& 'D:\stock\programs\telegram_report_delivery\RUN_SEND_MIX_REPORT.cmd' --force

# 手動等待 MIX 報告完成（最多 2 小時）；不會建立或修改排程
& 'D:\stock\programs\telegram_report_delivery\RUN_SEND_MIX_REPORT.cmd' --max-wait-seconds 7200
```

## 防重複與失敗恢復

- 狀態：`D:\TWStockData\services\telegram\data\mix_report_delivery_state.json`（鎖定檔 `mix_report_delivery.lock`）
- 日誌：`D:\TWStockData\services\telegram\logs`
- 同一個 run 的同一組附件（run_id＋附件 ref）只會成功傳送一次；摘要中隨時間變動的「目前」行不影響去重。
- 若部分附件成功後網路中斷，下次只會續傳尚未成功的項目。
- 狀態為 complete 但收據損壞時會自動重開補送，不需要 `--force`：個別有效的收據保留、不重送，只補送
  無法證明已送出的項目，並記 `complete_receipt_corrupt_reopened` 標籤與 `reopen_history`。
- 報告尚未產生（pointer、manifest 或附件還不存在）時等待；附件 hash 不符、超過 49 MB 或不在該 run 的
  `reports\c0\<run_id>` 底下時不會傳送。
- Telegram 429 與伺服器暫時性錯誤會遵守等待時間並自動重試。

## 工作日 08:00 排程

目前正式 08:00 排程只允許 MIX 報告：

- task name：`MIX Telegram Report 08-00`
- canonical entrypoint：`RUN_SEND_MIX_REPORT.cmd`
- definition：`MIX_WEEKDAY_0800_TASK_DEFINITION.json`
- central source pointer：`D:\TWStockData\program_data\mix\reports\LATEST_C0_RUN.json`
  （`mix_report_sender.py` 只讀這個 pointer；`LATEST_C0_RUN_BY_RUNTIME.json` 只用來在摘要註明是否有更新的執行；
  舊的 `LATEST_PRODUCTION_RUN.json` 已不再讀取，也不會被當成備援）
- 排程定義為 Taipei Standard Time、星期一至星期五 08:00，並以 `NO_PAUSE=1` 執行；
  `ExecutionTimeLimit` 為 `PT0S`、`MultipleInstancesPolicy` 為 `IgnoreNew`，允許電池啟動、
  不因切換電池或閒置結束而中止。

舊的 D-stock 08:00 shim `INSTALL_WEEKDAY_0800_TASK.cmd`／`.ps1`、`RUN_SCHEDULED_SEND.cmd` 與清理工具
`REMOVE_WEEKDAY_0800_TASK.cmd` 已從本資料夾移除；套件內唯一會註冊定時任務的是 MIX installer
（另一個 `INSTALL_COMMAND_BOT_TASK.ps1` 只註冊登入自啟的 Bot）。

MIX 08:00 的 canonical installer 是
`INSTALL_MIX_WEEKDAY_0800_TASK.cmd`／`.ps1`，以
`MIX_WEEKDAY_0800_TASK_DEFINITION.json` 為唯一權威。無參數或 `-Plan` 僅輸出計畫；
`-Verify` 只讀回根 TaskPath 的同名任務；正式註冊必須同時指定
`-Register -ConfirmRegister`，不使用 `-Force` 且不會立即啟動。若同名 action、owner、trigger
或 settings 不符即 fail-closed；若已完全符合則只回報 `already_present`。另一個
`D:\TWStockData\services\daily_orchestration\CUTOVER_SCHEDULER_TASKS.ps1` 仍可依同一 definition
執行授權 cutover；`SHOW_MIX_WEEKDAY_0800_TASK.cmd` 僅供唯讀檢查。

Task settings 固定為 Taipei Standard Time、星期一至星期五 08:00、`IgnoreNew`、
`StartWhenAvailable`、`PT0S`，允許電池啟動且不因電池或閒置停止。action 只呼叫
`RUN_SEND_MIX_REPORT.cmd --max-wait-seconds 0 --poll-seconds 60`；0 代表使用預設上限 30 分鐘
（`ZERO_WAIT_CAP_SECONDS`），期間報告仍未就緒就以 exit 4 結束，訊息列出報告資料日與最新一次執行資料日。
已存在但資料日較舊的報告不等待，照常寄出並在摘要標出兩個日期。排程不會呼叫 D-stock 報告、Central
ensure/refresh 或其他分析流程。手動執行未指定參數時採 4 小時 bounded wait。

## Telegram 個股分析指令

個股指令服務使用 Telegram `getUpdates` 長輪詢，只接受專用 `.ENV` 的私人
`TELEGRAM_CHAT_ID`。它不修改個股分析程式，而是以參數陣列及 `shell=False` 呼叫既有入口；
Telegram 輸入不會成為系統命令。

```text
2330            C0 H63 收盤估計（/stock、/h63 同義）
台積電          C0 H63 收盤估計
/h1 2330        C0 H1 收盤估計（明日、開盤、/open、/tomorrow 為相容指令）
/open 台積電    C0 H1 收盤估計
全部 2330       H63 與 H1 兩份報告（/all、/both 同義）
/all 台積電     H63 與 H1 兩份報告
/status         查詢佇列
/help           使用說明
```

H1 與 H63 的目標都是訊號收盤至同市場第 H 個官方交易日收盤；舊的「三個月完整分析」與「次日開盤分析」
模式已退役，舊工作會被要求以 H1／H63／both 重新送出。

名稱精準命中唯一股票時直接分析；多檔或只有模糊結果時只列出候選代碼，不會自行猜測。
工作採單工佇列，狀態保存在 `D:\TWStockData\services\telegram\data\stock_command_state.json`；
分析輸出、行程日誌與 PDF 位於 `D:\TWStockData\services\telegram\jobs\tg-update_id`，完成後
自動傳回 Telegram。登入自啟排程名稱是
`D-Stock Telegram Stock Command Bot`，使用隱藏的 `pythonw.exe` 執行，不需要保持 CMD 視窗開啟，
同時只允許一個監聽行程。服務 heartbeat 保存在
`D:\TWStockData\services\telegram\data\stock_command_bot.heartbeat.json`，錯誤與 traceback 會寫入
`D:\TWStockData\services\telegram\logs\stock_command_bot_YYYYMM.log`；WinSW service 日誌位於
`D:\TWStockData\services\telegram\service_logs`。狀態檢查會顯示 `ListenerHealth`、`ListenerReadiness`、
heartbeat 年齡、實際 PID、listener executable/command line 的比對與存取狀態及目前 `nextUpdateId`；若可讀的
`D:\stock\.venv\Scripts\python.exe`（登入排程則為同一 venv 的 `pythonw.exe`）或 listener 路徑不一致，
會標記 `ListenerHealth=owner_identity_mismatch`，不視為可用。LocalSystem 服務在一般權限檢查時若無法讀取
process command line 或 executable path，會標記為 `unavailable_due_to_access`；只要服務、owner PID、
PID 建立時間、heartbeat 仍一致且 heartbeat 新鮮，會以 `ListenerHealth=running` 表示存活，
不把不可讀欄位誤判為 stopped。`DiskSourceSHA256` 與 heartbeat 的 `LoadedSourceSHA256` 不一致時，
會標記 `SourceStatus=stale_source`、`ListenerReadiness=restart_required` 並以非零退出，
必須重啟服務後才算 `ListenerReadiness=ready`。

Bot 只接受 current C0 H1／H63 報告（`report_schema_version` 與期限必須相符）。估計報告的 caption
顯示訊號收盤日與到期日、資料時間（緊接在訊號日之後，不會被 caption 長度截掉）、上漲機率與預估報酬、
方法與校準狀態、比較方向率（低於 60% 時加註不可視為可靠預測）、模型與需求版本及來源狀態；來源 PARTIAL
時另行標示。「無法估計」報告（`c0_no_estimate_data_time_only`）也會傳送，caption 列出原因、該股最後價格日、
分析資料日與資料時間，不含估計值。工作執行期間若已有較新資料，caption 會註明本報告仍使用工作開始時的資料。

H1 報告（C0 與單股選定模型兩種格式）帶有封存的 `preopen_h1` 區塊時（`single-stock-preopen-h1-v1`，由
`single_stock_analysis\src\single_stock_preopen.py` 產生）：使用開盤前版時，caption 標題加註「開盤前版」，
先列開盤前版上漲機率、預估收盤報酬、全市場／可交易名次、前一晚美股（t 日籌碼未更新或不完整時多一行，寫最新到幾號或成功／應有數量，只說明、不阻擋）與必要揭露，再列「C0 基準（備援）」數值；
回退時第二行即為「開盤前版不可用（原因代碼），本次 H1 改用 C0 基準」，其餘照舊。區塊格式或數值無效時拒絕傳送。
必要揭露依開盤前版快照 manifest 的配方名稱選擇（`PREOPEN_DISCLOSURE_BY_RECIPE`；讀區塊 `meta.recipe.name`，沒有時讀
`disclosure.recipe`）：沒有 `recipe` 欄位（或為 null）的舊快照與 `h1pre_final_nogc_xs`（v1）沿用原本的文字與封存區塊的數字；
`h1pre_v2_ens`（v2）顯示 v2 保留期數字（20260102～20261019，191 個交易日；出處 `H1改良研究報告_20261021.md` 第 6 節）；
其他名稱或格式不符（`meta.recipe` 不是 `{"name": ...}`）只顯示「開盤前版配方「名稱」未知，不顯示保留期驗證數字」。
「隔日開盤買進 Top10」的括號說明依配方與數字正負（為負／約為零／為正），不再固定寫「為負」。
H63 與不帶區塊的報告 caption 不變。注意：常駐服務在行程內第一次驗證選定模型報告時載入
`stock_candidate_report` 後即沿用該版本；更新單股報告程式後必須重啟服務（`RESTART_COMMAND_BOT_SERVICE.cmd`），
否則帶區塊的 H1 正式報告會因章節比對不符而無法通過驗證。

### 安全換版與重啟

`RESTART_COMMAND_BOT_SERVICE.cmd` 無參數只做唯讀驗證；正式操作必須明確指定
`-Restart -ConfirmRestart` 或 `-Deploy -ConfirmDeploy`，且需系統管理員權限。入口會核對
WinSW service identity、XML executable/arguments/working directory、LocalSystem、wrapper SHA
與 listener source SHA；停止時只透過 SCM 停止服務，並以 owner PID 加 creation identity 等待舊
listener 完整退出，禁止依行程名稱廣泛終止。啟動後必須看到不同的 owner PID/creation identity、
owner/heartbeat PID 一致、新鮮 heartbeat、目前 loaded source SHA 與磁碟 source SHA 一致，才回報
成功；`Running` 單獨不算成功。`-Deploy` 是已部署原位檔案的精確驗證加安全重啟，不會複製未知檔案。

```powershell
# 只讀驗證，不會停止服務
& 'D:\stock\programs\telegram_report_delivery\RESTART_COMMAND_BOT_SERVICE.cmd' -VerifyOnly

# 明確核准才會透過 SCM 重啟
& 'D:\stock\programs\telegram_report_delivery\RESTART_COMMAND_BOT_SERVICE.cmd' -Restart -ConfirmRestart
```

### Telegram 個股命令真實 E2E（已退役）

`RUN_TELEGRAM_COMMAND_BOT_E2E.cmd` 與 `telegram_command_bot_e2e.py` 驗的是舊的 `three_month`／
`tomorrow_open` 流程與 central receipt lineage，已隨 C0 改版移除，不再提供。對應的舊測試保留在
`tests\_retired\`（不執行）。delivery ledger
`D:\TWStockData\services\telegram\data\stock_command_bot.delivery.jsonl` 仍會記錄每次傳送的
message id、檔案 hash／size、C0 binding、讀取時間與版本標籤，不保存 Token、Chat ID、原始回覆或 caption。

## 登出後持續執行

需要在使用者登出後繼續接受 Telegram 查詢時，請使用
`INSTALL_COMMAND_BOT_SERVICE.cmd`。安裝器會顯示一次 Windows UAC，核准後建立
`DStockTelegramCommandBot` 服務，使用 `LocalSystem`、自動延遲啟動及失敗後一分鐘重啟。
服務直接執行原位的 `D:\stock\.venv\Scripts\python.exe`，不會在桌面顯示 CMD 或 PowerShell 視窗；
服務設定檔仍在本套件的 `service` 子資料夾，但 WinSW 日誌寫入中央 `service_logs`。

服務安裝成功且 heartbeat 驗證完成後，舊的登入排程才會被移除；若服務安裝或啟動失敗，
安裝器會移除失敗服務並恢復登入排程。WinSW 2.12.0 x64 包裝器及其 MIT License 位於
`service` 子資料夾，安裝前會核對固定 SHA-256。Token 與 Chat ID 不會寫入服務設定，
仍只從本資料夾的 `.ENV` 讀取。每個個股工作開始時 pin 一次目前的 C0 binding，並以
`--pinned-head-sha256` 交給分析子行程，讓同一工作的每份報告都用同一份資料。子行程完成後及每次
Telegram PDF 上傳前，Bot 會重新讀取目前 binding 並重新驗證宣告的 PDF／JSON：工作期間資料或版本更新只記
`newer_data_during_job`／`runtime_contract_changed_during_job` 標籤（目前資料暫時讀不到則記
`current_binding_unreadable_during_job`），報告照常傳送；竄改或讀取途中改變（`C0BindingIntegrityError`）、
附件在上傳前變動或宣告路徑變動仍會停止，`unsentModes` 會保留未傳送模式證據。
