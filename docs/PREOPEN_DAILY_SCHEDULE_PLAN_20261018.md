# 開盤前版每日排程規劃（2026-10-18 使用者確認並註冊；首次執行 2026-10-19 週一）

依據：ADR-0008。目標是每個交易日 08:30 前產出開盤前版 H1 快照，並讓 Top10、MIX、Telegram 都用到它。
目前這台電腦**沒有任何**每日自動排程（只剩一個已中斷的一次性 `TWStockUpdater_FullBackfill_20261012`）。

## 時間表（台北時間，週一至週五）

| 時間 | 任務 | 指令 | 預估耗時 | 依據 |
| --- | --- | --- | --- | --- |
| 23:00 | ① 中央完整更新（32項啟用路由：價格、籌碼、國際、營收…） | `D:\TWStockUpdater\runtime\.venv\Scripts\python.exe -B -m twstock_updater update --mode incremental --headless`（cwd `D:\TWStockUpdater`，環境變數 `PYTHONPATH=D:\TWStockUpdater\src`、`TWSTOCK_UPDATER_ROOT=D:\TWStockData\updater_runtime`） | 一般 6～13 分鐘（10/16 五次實測 349～804 秒）；補歷史缺口時可能到 2.4 小時 | 融資券約 21:30 公布後再抓；放在深夜，就算拖長也不影響早上 |
| 06:30 | ② 國際資料單獨更新（只抓前一晚美股等 26 個標的） | 同①，但加 `--routes global_market_history` | 約 3.5 分鐘（今日實測 210 秒） | 美股收盤台北 04:00（11/1 起冬令 05:00）；FinMind 若尚未發布會自動改用 Yahoo 補尾段 |
| 07:15 | ③ 開盤前版產製 | `D:\stock\programs\shared\RUN_PREOPEN_DAILY.cmd`（結算前一日 → 每 21 個交易日自動重訓 → 產生今日快照） | 1～3 分鐘（今日 49 秒；中央 head 更新後首次約 2.5 分鐘） | 前一晚美股缺漏仍以缺值推論並發布，標示 degraded 與美股最新資料日期 |
| 07:30 | ④ 次日 Top10 | `D:\stock\TWNextDayTop10\RUN_TOP10.cmd --no-pause --no-open`（預設 `--model-source auto`） | 約 6 分鐘（今日隔離實測 5 分 59 秒） | 需在③之後；寫入共用 C0 前瞻帳冊，與⑤錯開 |
| 07:40 | ⑤ MIX 全市場報告 | `powershell -NoProfile -ExecutionPolicy Bypass -File "D:\MIX All\tools\run_daily.ps1"`（預設 `auto`） | **未實測**全市場耗時；讀取方式與 Top10 相同，估 5～10 分鐘 | 需在④結束後（兩者都寫同一個 C0 前瞻帳冊 sqlite，不可同時） |
| 08:00 | ⑥ MIX 報告 Telegram 傳送 | 既有定義 `INSTALL_MIX_WEEKDAY_0800_TASK.ps1`（`RUN_SEND_MIX_REPORT.cmd --max-wait-seconds 0 --poll-seconds 60`） | 最多等 30 分鐘報告就緒 | 已有現成定義與測試，直接註冊 |
| 隨時 | Telegram 個股 Bot | Windows 服務 `DStockTelegramCommandBot`（已在跑） | 不排程 | 需重啟一次才會載入新程式（見下） |

- **不註冊**文件中規劃的「Central 03:00 current-only」與「04:00 串行鏈」：03:00 那條是 6 路由版，會把中央 head 的 capture scope 換成沒有國際資料視窗的版本，之後②會依設計拒絕執行（fail-closed）。手動更新中央時也請用①的 32項啟用路由指令，不要用 `一鍵更新.cmd`。
- 星期一：用的是星期五 23:00 抓的台股資料＋星期一 06:30 補進的上週五美股收盤。週六、週日不排。
- 台股平日休市（連假）：①②③照跑；③的目標日以「下一個平日」推算，可能推錯，Top10 會標示「目標日未驗證」，不影響其他日子。
- 電腦必須在 23:00 與 06:30～08:00 開機；註冊時勾選「喚醒電腦以執行」。

## 註冊方式（2026-10-18 17:12 已註冊，`-Verify` 與工作排程器查詢皆一致）

註冊身分是目前使用者的互動式登入（`InteractiveToken`、一般權限）：**電腦必須保持登入狀態**（鎖定螢幕可以，登出或重開機後未登入則不會執行）。已開啟「喚醒電腦以執行」。

腳本位置：`D:\stock\programs\shared\preopen_schedule\`

| 檔案 | 用途 |
| --- | --- |
| `PREOPEN_SCHEDULE_DEFINITION.json` | 5 個任務的名稱、時間、指令、工作目錄、時間上限與說明（UTF-8，人類可讀文字都在這裡） |
| `INSTALL_PREOPEN_SCHEDULE.ps1` | 預設或 `-Plan`：只列出會註冊什麼；`-Register -ConfirmRegister`：註冊；`-Verify`：核對已註冊任務是否與定義一致。同名任務存在時絕不覆寫（一致→回報 already_present，不一致→失敗） |
| `REMOVE_PREOPEN_SCHEDULE.ps1` | 預設：列出定義中哪些任務目前存在；`-Remove -ConfirmRemove`：只移除這 5 個任務 |
| `RUN_CENTRAL_UPDATE.ps1` | ①②共用的中央更新包裝：`-Mode full`（32項啟用路由；結束碼不在 0、10 時等 30 分鐘重試，最多 3 次）、`-Mode global`（只更新國際資料，不重試）；日誌寫到 `D:\TWStockData\program_data\stock_daily_preopen\logs\central_<mode>_<時間>.log`；`-DryRun` 只印指令 |

- 任務以目前使用者註冊（互動式登入、一般權限），設定：`StartWhenAvailable`、`WakeToRun`（喚醒電腦）、`IgnoreNew`、電池不阻擋、閒置不中止；時間上限 ① 6 小時、② 40 分、③ 30 分、④ 45 分、⑤ 1 小時。
- ⑥ MIX Telegram 08:00 用既有的 `telegram_report_delivery\INSTALL_MIX_WEEKDAY_0800_TASK.ps1 -Register -ConfirmRegister` 另外註冊。
- 從 PowerShell 7 執行這些 `.ps1` 時，請先設 `PSModulePath` 為 Windows PowerShell 的模組路徑（或直接用 `C:\Windows\System32\WindowsPowerShell\v1.0\powershell.exe -File …`），否則 `ScheduledTasks` 模組載入會出錯。

```powershell
# 預覽（不改變系統）
C:\Windows\System32\WindowsPowerShell\v1.0\powershell.exe -NoProfile -ExecutionPolicy Bypass -File "D:\stock\programs\shared\preopen_schedule\INSTALL_PREOPEN_SCHEDULE.ps1" -Plan
# 註冊（需明確確認）
C:\Windows\System32\WindowsPowerShell\v1.0\powershell.exe -NoProfile -ExecutionPolicy Bypass -File "D:\stock\programs\shared\preopen_schedule\INSTALL_PREOPEN_SCHEDULE.ps1" -Register -ConfirmRegister
```

## 失敗時的行為

| 情況 | 結果 |
| --- | --- |
| ①部分失敗（價格已更新、籌碼路由未更新）、②成功 | ③仍會產出並發布，但 predict 會比對五條 t 日籌碼路由（法人、融資券、當沖、本益比、外資持股）的中央最新日期與訊號日：任一早於訊號日或無法取得，即在快照 manifest（`latest.json` 與 `<run>.json`）寫入 `chips_freshness.status=stale`（含 `stale_routes`、各路由日期與中央狀態 `route_status`）並設 `degraded=true`；④⑤與單股的開盤前版說明文字加註最新資料到幾號，例如「t 日籌碼未更新：融資券最新到 20261015（以舊值推論，準確度可能下降）」。使用者裁示（20261018）：不設覆蓋率門檻、有資料就可用；中央標示不完整（PARTIAL）的路由只在說明顯示成功／應有數量、不阻擋；Top10／MIX／單股報告與 Telegram 個股訊息都會顯示這段說明；`--require-fresh-chips` 保留接受以相容舊命令，不再因 stale 阻擋③。資料新鮮度僅用於說明與 degraded 標示，不另設發布門檻 |
| ①整體失敗（價格也未更新）、②成功 | 本次籌碼檢查不會觸發：訊號日（價格面板最後一個交易日）與籌碼同樣停在前一個交易日，`chips_freshness` 為 ok。③會以該舊訊號日產出快照（目標日為當天）；④⑤與單股的預期訊號日同樣取自中央 head 的價格資料日，不會因訊號日不符而退回 C0，整條鏈以前一個交易日的台股資料運作（報告上的訊號日可辨識）；Top10 另有快照目標日與 C0 下一交易日的一致性檢查 |
| ②失敗（美股缺漏） | ③沿用原缺值推論流程並發布，`degraded=true`、`gate_us_up=null`；`describe()` 顯示美股最新資料日期（YYYYMMDD，無資料為「未提供」）。`--allow-missing-us` 仍可接受，但不改變發布行為；缺漏本身不再觸發④⑤回退 C0 |
| ③失敗 | ④⑤自動退回 C0 |
| ④或⑤失敗 | 各自失敗，不影響另一個；⑥等不到 MIX 報告 30 分鐘後以退出碼 4 結束，不傳送 |

## 尚未確認的數字

- MIX 全市場一次要跑多久（10/08 那次只跑 44 檔，約 30 秒）。
- FinMind 當日還原價與籌碼的實際發布時間（10/16 的紀錄是更新器執行時間，不是 FinMind 發布時間）。若實測發現 23:00 仍不完整，①往後移到 23:30 或 00:30 即可，不影響其他任務。
