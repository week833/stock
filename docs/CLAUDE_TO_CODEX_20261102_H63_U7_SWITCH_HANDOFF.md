# Claude → Codex 交接：H63 改用 U7 三個月相對強弱（20261102）

獨立交接檔，不沿用舊檔。決策與理由見 `D:\stock\docs\decisions\ADR-0009-h63-u7-cross-sectional-parallel-model.md`。

## 1. 使用者決定

- 20261101 21:20 使用者回覆「換」：現行 H63（選定模型 expanded_linear）改回 U7 全市場排名法；區間改 B3＋CQR252；報告改稱「跑贏同期多數股票的機率」；建立每日前瞻紀錄；先補價格缺洞；副本驗證後部署；H1 不改。
- 使用者另外沒有回答、不得自行做：H1 營運修正（美股補齊、休市日守門、結算鍵）、次日開盤產品（S0／S1）。

## 2. 已完成

（部署結果見第 4 節。）

- 新程式（D:\stock\programs）：
  - `shared\stock_h63_xs_core.py`：U7 純計算（oracle float64 規格逐列移植）。
  - `shared\stock_h63_xs_data.py`：pin head＋reader lease、pyarrow 串流解析（同遍驗 SHA／大小／筆數）、補充檔（a807 價格與板別，SHA 釘選）、拼接（接縫 20251231）、價格覆蓋率／板別／日曆檢查。
  - `shared\stock_h63_xs_ledger.py`：前瞻區塊寫一次、index 雜湊鏈、結算（第 63 個自身價格列）、凍結、事先登記 v3 檢討。
  - `shared\stock_h63_xs.py`：CLI（supplement-build、train、predict、settle、review、daily、status、rollback）。
  - `shared\stock_h63_xs_reader.py`：唯讀 API 與固定顯示文字（揭露 h63-xs-disclosure-v1）。
  - `shared\stock_h63_xs_fixture.py`：測試夾具。
  - `shared\RUN_H63_XS_DAILY.cmd`、`shared\h63_xs_schedule\`（定義＋INSTALL／REMOVE，未註冊）。
  - `single_stock_analysis\src\single_stock_h63_xs.py`：單股疊加層（auto／xs／off／legacy）。
- 修改既有檔（以 11-01 16:24 開盤前版 session 的現檔為底）：
  - `stock_candidate_report.py`：h63_xs 掛勾、區塊驗證、H63 第一章與第六章新呈現、H63 可見日期 YYYYMMDD、machine evidence `h63_disposition`。H1 函式未動。
  - `tw_single_stock_c0.py`：`--h63-source`，選定路徑以 ExitStack 同時包 H1 與 H63 疊加層。
  - `telegram_stock_command_bot.py`：H63 區塊驗證與 caption 分支、HELP／READY 文字。MODE_LABELS 不改（既有錯誤訊息測試釘住）。
  - `single_stock_analysis\tests\conftest.py`：隔離 `STOCK_H63_XS_ROOT`。
  - `STABLE_PROGRAM_PATHS.txt`：只新增 18 行，既有行未動。
- 驗收（副本，D: 寫入以 audit hook 阻擋，只放行 reader lease 與選定快取鎖檔）：見 ADR-0009「證據」與驗收收據。

## 3. 重要數字（正式配方，接縫 20251231）

- 同期 20260109～20260722：漲跌方向準確率 54.28%（停用模型 50.05%）、AUC 0.572（0.502）、Brier 0.2467（0.2796）。
- 7 個半年區塊：準確率 54.7%、AUC 0.570、IC 0.148、Brier 0.2465；區間覆蓋 B3＋CQR252 79.6%（寬 49.5 pp）。
- 設計後 2026-07：AUC 0.532、IC 0.094。
- 弱勢期提醒：2025-10～2026-03 月 IC 約 −0.09～+0.01（A5 回放 20 日 AUC 0.47、IC −0.03），2026-04～06 轉強。前瞻判定需 3 個 63 日區塊。
- 20261030 快照：1,981 列、合格 1,060、b＝0.9689、CQR Q＝0.1095、區間寬度中位數 47.6 pp。

## 4. 部署與現況

- 部署時間：20261102 00:42（台北）。批次 SHA `5eb8f1d6b37c00c6a215c780568c33d6e30a0285b67711d6fba39eb3bafa42e7`，23 個檔案（5 改＋18 新），寫入後逐檔讀回 SHA 相符。
- 部署紀錄：`D:\TWStockData\program_data\stock_h63_xs\deploy\20261101T164200Z\`（batch.json、before\、stage\、journal.jsonl、receipt.json）。
- 修改檔 SHA（前 16 碼，部署前 → 部署後）。之後任何人再改這 5 個檔，都要以「部署後」為底三方合併：

| 檔案 | 部署前 | 部署後 |
| --- | --- | --- |
| single_stock_analysis\src\stock_candidate_report.py | 8af5cd9ae4579304 | f197caeb9bcb3f0b（第二批；第一批為 f80ebb37996b7e73） |
| single_stock_analysis\src\tw_single_stock_c0.py | 8a35cef8bf6188a8 | 6de98223c34e0255 |
| telegram_report_delivery\telegram_stock_command_bot.py | 7c028544b25e713b | 22661a438b3103f2 |
| single_stock_analysis\tests\conftest.py | 2dfc411f91c1e9a9 | ecd7bfc3828457bd |
| STABLE_PROGRAM_PATHS.txt | 672664308b074e6a | 9e95f7caeb857f81 |

- 資料根 `D:\TWStockData\program_data\stock_h63_xs\`：
  - `reference\price_supplement\95687e374776\`：補充檔（manifest 95687e3747760821…；price 0d08f47d…、board aff81147…），正式程式重建與副本位元組相同。
  - `models\`：模型 `2a12623a6dd09ee265711587a8806fec2494d41c94a12bb95b4d1f9e3b1dc5bd`（h63_xs_u7_20261030，b＝0.968851，328,649 列、1,755 檔），啟用 `h63xs-58ef69c13da345c38c01563248365ef6`，授權原文「換」（20261101 21:20）。
  - `predictions\2026\20261030__a773e2dbf35e.*`：第一個全市場快照＝第一筆前瞻紀錄（1,981 列、合格 1,060，CQR Q 0.1095），於 20261102 00:45 記錄（prospective）。
  - `acceptance\20261102_switch\`：驗收收據（sha256 64ac355c…，PASS）、oracle 與 10-30 輸入、比對結果、驗收工具。
  - `config\display.json`＝`legacy`：常駐 Bot 尚未重啟，舊模組無法驗證帶 h63_xs 區塊的報告。重啟並確認載入新原始碼後改為 `{"mode": "auto"}`（或刪除）。
- H1 與選定部署關鍵檔部署前後 SHA 不變：single_stock_preopen.py 1a24c2b8…、stock_daily_preopen.py 2de66ae5…、stock_daily_preopen_reader.py 43123d55…、RUN_PREOPEN_DAILY.cmd 88c4e89d…、selected_current.json 327e31cb…、stock_daily_feature_catalog.py 4bf52eca…。
- `VERIFY_STABLE_PROGRAM_PATHS.ps1` 只報另一 session 新增、未登錄的 `programs\shared\tests\test_stock_daily_preopen_stitch_repair.py`；本案新檔都已登錄。
- 第二批（20261102 01:27，批次 SHA 04eccdc87f95a8fe92040eda2afa1a3add6c03d1251c6500ffa643a9de999cdc，紀錄 `deploy\20261101T172700Z\`）：H63 報告的價格圖日期軸標籤與圖表註記也改為 YYYYMMDD（圖表只以日期當標籤、位置依序號）；`single_stock_analysis\tests\test_single_stock_h63_xs.py` 6386e22a… → 678dc0da…。
- P0（選定路徑在現行 head 能否出報告）已解除：以部署前的現行程式實跑 2376 H1＋H63，兩份都通過 validate_selected_report（rc 0；兩份共約 42 分鐘，屬既有效能）。
- 新程式端到端（副本模組、現行 head、輸出改到 C:）：2376 H1＋H63 兩份都通過 validate_selected_report（rc 0）；H63 帶 U7 區塊（p 0.586、相對偏強、排名 136／1,060、區間 −20.7%～＋32.5%），可見文字沒有 ISO 日期、「上漲機率」只出現在「不是上漲機率」。H1 逐位元組回歸（3 份 H1、2 份舊版 H63）章節、SHA、machine evidence、caption 全部相同。
- 部署後在 live 目錄跑：shared 新測試 50、單股相關 319、Telegram 404 全過；[INFO] log: D:\TWStockData\program_data\stock_h63_xs\logs\h63_xs_daily_20261102.log
[INFO] H63 U7 daily ok 以 cmd.exe 在正式資料根實跑 exit 0（同一 head 判定 already_recorded）。

## 5. 未完成與阻擋

1. Telegram Bot 重啟（需管理員，與開盤前版 session 欠的那次合併）：`RESTART_COMMAND_BOT_SERVICE.cmd -Deploy -ConfirmDeploy`。重啟前 `config\display.json` 必須維持 legacy；重啟並確認載入新原始碼 SHA 後才改為 auto（或刪除該檔）。
2. 每日排程註冊（需使用者同意）：`h63_xs_schedule\INSTALL_H63_XS_SCHEDULE.ps1 -Register -ConfirmRegister`，再 `-Verify`。未註冊前只能手動跑 `RUN_H63_XS_DAILY.cmd`；快照落後超過 5 個平日後 H63 不顯示數字，前瞻紀錄也會缺日。
3. Git commit：依 PD-0001 待決，未 commit。
4. 既有失敗（與本案無關，現行版本相同）：single_stock `test_published_row_cache.py::...unwritable_fall_back...`；shared 12 個（`test_stock_daily_preopen.py` 4、`test_stock_daily_selected_cache.py` 4、`test_selected_read_only_plan.py` 2、`test_c0_per_market_asof.py` 1、`test_stock_daily_candidate_runtime.py` 1）。
5. 另一 session 於 20261102 00:04 修改 `shared\stock_daily_preopen_data.py` 並新增 `tests\test_stock_daily_preopen_stitch_repair.py`（未登錄 STABLE_PROGRAM_PATHS）；與本案檔案無交集。

## 6. 回復

- 免重啟：`D:\TWStockData\program_data\stock_h63_xs\config\display.json` 設 `{"mode": "off"}`（只顯示已暫停）或 `{"mode": "legacy"}`（回到改版前）。
- 模型：`stock_h63_xs.py rollback --to <sha> --authorization "<原文>"`。
- 程式：以 `deploy\<UTC>\before\` 還原 5 個修改檔（核對 batch.json 的 before SHA），新檔移到 `_retired\`。

## 7. 擁有者

| 檔案或資源 | 擁有者 | 備註 |
| --- | --- | --- |
| stock_h63_xs*、RUN_H63_XS_DAILY.cmd、h63_xs_schedule、single_stock_h63_xs.py、資料根 stock_h63_xs | 本案（ADR-0009） | 新增 |
| stock_candidate_report.py、telegram_stock_command_bot.py | 開盤前版 session（11-01，未 commit）＋本案 | 之後再改需三方合併 |
| single_stock_preopen.py、stock_daily_preopen*.py、RUN_PREOPEN_DAILY.cmd | 開盤前版 session | 本案未碰 |
| selected_current.json、選定快取 | 選定部署 | 本案未碰；P0 已確認選定路徑在現行 head 可出報告 |
