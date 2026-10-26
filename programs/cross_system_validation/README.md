# 跨系統唯讀驗收工具（v2，C0 現行架構）

本工具只讀取既有正式證據，不啟動 Central、MIX、個股分析、Telegram 或
三竹，不呼叫 API／provider，不註冊或修改排程，也不搬移、刪除或修復檔案。
排程只用 `Get-ScheduledTask`／`Export-ScheduledTask` 唯讀查詢。

## 唯一正式輸出與唯讀模式

`--reports` 只會把快照寫入：

```text
D:\TWStockData\services\acceptance\latest.json
```

`--structure-only` 與相容別名 `--central-mix-live` 會評估全部 component，
但不寫任何檔案（`main()` 只在 `--reports` 時呼叫 `_write_report`）。fixture
測試用 `--fixture-root`，輸出覆寫只能留在 fixture 根目錄；fixture 模式不讀
正式路徑，也不查詢 Task Scheduler（改讀 `scheduler\scheduled_tasks.json`，
不存在代表沒有註冊任何排程）。

```bat
RUN_ACCEPTANCE_CHECKS.cmd --structure-only
RUN_ACCEPTANCE_CHECKS.cmd --reports
```

退出碼：`0` 代表沒有任何 blocking component（可能仍有警告、`warning` 或
`not_applicable`），`1` 代表至少一個 component blocked，`2` 代表驗收器設定或
輸出錯誤。`--stale-hours`／`--legacy-write-hours` 只屬於已移除的舊路徑，
已一併移除。

## Component 狀態

每個 component 各自記錄 `status`：

- `pass`：該 component 應驗證的內容全部通過（可能附資料時間警告）。
- `blocked`：KEEP 門檻失敗或真實正式證據缺失，`blocking=true`。
- `warning`：無法驗證但依使用者決定不阻擋（例如排程未註冊、尚未寄出），
  `pass=null`，不視為通過。
- `not_applicable`：C0 架構不存在此項（三竹），`pass=null`，不視為通過。

只有 `blocking=true` 會讓 `overall_status=blocked`。`non_blocking_unverified`
列出 `warning`／`not_applicable` 的 component；`warnings` 列出所有標籤。

## 不阻擋與 KEEP

不擋（只記標籤並顯示實際讀到的資料時間）：資料非最新、報告或 Central head
超過 48 小時、route 為 `PARTIAL`、版本或綁定差異、registry route 組合與已知
C0 六條不同、`LATEST_C0_RUN_BY_RUNTIME.json` 指向較新 run、排程未註冊、
MIX 報告尚未寄出、個股報告未驗證或沒有 `data_time`。

KEEP（擋）：完整性與竄改（exact path/size/SHA-256、head 與 immutable 副本
bytes 相同、重複 key／NaN、個股 seal）、schema／status／horizons、同 run
manifest/payload 一致、ETF／非普通股排除、預測有效性、registry 內部不一致、
已註冊排程與定義不一致、舊排程仍為啟用、退役檔仍在原位置或仍被現行程式／
啟用排程引用、資料根目錄缺失、隔離清單含非程式檔。

## Active components

1. `central_publication`：`contracts\shared_market_data\requirements_v1.json`
   必須是 `shared-market-data-requirements-v3`（schema_version 3），
   `publication_contract.publication_scope`（profile `C0`）的
   `required_route_ids` 必須與 `shared_raw_requirements`、`route_contracts`、
   `interface.route_ids` 一致；head／archive／objects 相對路徑必須是
   `_metadata/stock_data_current.json`、`_manifests`、`_objects`。接著讀
   `D:\TWStockData\shared_market_data\_metadata\stock_data_current.json`
   （`stock-data-publication-v1`），其 SHA-256 對應的
   `_manifests\<sha>.json` 必須存在且 bytes 完全相同，每條 required route 的
   `_objects\<sha>.json` 必須存在且 size／SHA-256 相符。缺 required route、
   route 狀態不是 `SUCCESS`／`PARTIAL` 都會擋。資料時間（published_at、
   `official_market_daily` 的 actual_max_date、各 route 最大日、日曆驗證日）
   會顯示在 `data_time`，並以 `official_market_daily` 資料日作為 MIX 報告比對
   的 Central 資料日（不同只記警告）。
2. `workflow_scheduler`（id 沿用）：MIX 平日 08:00 Windows Task
   `MIX Telegram Report 08-00`。先驗證
   `D:\stock\programs\telegram_report_delivery\MIX_WEEKDAY_0800_TASK_DEFINITION.json`
   （與 `INSTALL/SHOW_MIX_WEEKDAY_0800_TASK.ps1` 相同契約）。未註冊 →
   `warning` + `schedule_not_registered`（註冊另行處理）；已註冊 → 以匯出的
   task XML 比對 action／arguments／working directory、平日 08:00 週觸發、
   `PT0S`、`IgnoreNew`、`StartWhenAvailable`、電池與閒置設定（規則同
   `INSTALL_MIX_WEEKDAY_0800_TASK.ps1 -Verify`），不一致才擋；已註冊但停用 →
   `warning` + `schedule_registered_but_disabled`（不算通過、不擋）。定義檔逐欄
   比對 `INSTALL_MIX_WEEKDAY_0800_TASK.ps1` 的 `Assert-Definition`（全部欄位與值，
   含 publication_contract，以及拒絕舊欄 `stale_or_integrity_failure_action`、
   `requires_feedback_publication_receipt`）；規則表是 `MIX_TASK_DEFINITION_RULES`，
   測試會從 ps1 抽出欄位清單比對以防漂移。owner SID：INSTALL 以「目前互動使用者」
   註冊（沒有固定 principal），所以 owner 不是驗證者本人或 SID 無法解析只記
   `warning`（`schedule_owner_not_current_user`／`schedule_owner_sid_unverified`）；
   只有設定固定 principal（`MIX_TASK_FIXED_PRINCIPAL_SID`）時不一致才擋。查詢失敗擋
   （`schedule_query_unavailable`）。
3. `mix_analysis_report`：只讀 `reports\LATEST_C0_RUN.json`
   （`mix-stock-daily-c0-current-report-v1`）→ `c0\<run_id>\manifest.json` →
   payload/html/input_sources/xlsx 的 exact path/size/SHA-256。已退役的
   `LATEST_PRODUCTION_RUN.json`／`LATEST_REPORT_RUN.json` 不讀，也不作備援。
4. `mix_mitake_persistence`：C0（`core_daily.py`）不做三竹同步，固定
   `not_applicable` + `mitake_not_applicable_c0`，不讀舊 v2 三竹證據，也不視為
   通過或阻擋。
5. `single_stock_analysis_report`：個股分析是按需執行，`tw_single_stock_c0.py`
   只輸出 `D:\TWStockData\program_data\dstock_single\reports\c0\c0_<代號>_h<H>_<時間>.json/.pdf`，
   沒有 latest pointer；因此驗證此固定目錄內全部符合檔名的報告：schema
   `single-stock-daily-c0-estimate-v1`、profile、H1/H63、output 路徑、
   `estimate_binding_sha256` seal、PDF size/SHA-256、有估計值時本股必須是已驗證
   普通股。正式附件契約同 `stock_daily_contract.validate_c0_estimate`／Bot 的
   `validate_c0_no_estimate_report`：report_class 只能是
   `daily_estimate_effect_unknown` 或 `c0_no_estimate_data_time_only`、
   `source_mode=published`、target_definition、`qa_override=false`、不得宣稱
   歷史 PIT／成分／除權息已驗證或效益已知；估計類報告必須帶估計值（不能藉由
   缺值略過 ETF 檢查），無估計類報告不得帶估計值。沒有任何報告 → `warning`
   `single_c0_no_report_yet`。
6. `telegram_service_publication`：command Bot 的 disk source SHA、owner、
   heartbeat（同 PID、狀態、180 秒內）＋ MIX 寄送狀態
   `D:\TWStockData\services\telegram\data\mix_report_delivery_state.json`
   （`mix_report_delivery_state_v1`，由 `mix_report_sender.py` 寫入）。尚未寄送
   （檔案不存在，或檔案存在但沒有目前 LATEST_C0_RUN fingerprint 的紀錄）→
   `warning` `mix_report_not_yet_delivered`（pass=null，不擋）。有目前報告的紀錄時
   沿用寄送證據邏輯：必須 complete、identity（manifest path／SHA）／資料日／
   message_id／sent_items 與目前附件一致；紀錄損壞或不一致、狀態檔無法解析或
   schema 錯誤 → 擋（完整性）。目前 run_id 的紀錄存在但 fingerprint 不同（寄送後
   manifest／附件 refs 已變）→ 擋 `mix_delivery_state_fingerprint_mismatch`。
7. `data_retention_guard`、8. `retirement_isolation`：改為**唯讀的直接觀察**
   （2026-10-12）。原本要求的
   `D:\TWStockData\services\acceptance\data_retention_guard.json`／
   `retirement_isolation.json`（`cross-system-data-retention-guard-v1`／
   `cross-system-retirement-isolation-v1`）沒有任何 producer：
   `program_quarantine.py apply` 只寫 `migration_snapshots\...\pending_delete\programs\_run_evidence\<run_id>\`
   底下不同 schema 的檔案，且需輸入確認字串；allowlist 的 107 個退役檔已用其他
   方式移除，quarantine 已無對象（apply 會 `plan_snapshot_missing`）。所以這兩項
   不再依賴那兩個檔案：檔案**不存在**時不要求；檔案**存在**時仍照原規則驗證
   （schema／status／data_untouched／deleted、moved=0／quarantine manifest
   path+SHA／union 與 registry route 數），不符就擋。兩項結果都帶
   `observation_basis=read_only_current_state_observation_not_event_audit`：
   這是現況觀察，**不是**退役事件稽核，無法證明「過去從未刪過資料」。

   共同輸入：唯讀讀取
   `services\acceptance\program_quarantine_allowlist_v1.json`
   （`retained-program-quarantine-allowlist-v1`），逐項套用
   `program_quarantine.py` 的 entry 規則：欄位集合固定、`source_root_id` 必須是
   `ALLOWED_ROOT_IDS` 之一、`relative_path` 不可為絕對路徑／`..`／萬用字元、
   `source_path` 必須等於 root + relative、category 只能是
   `retired_analysis`／`retired_wrapper`、`cowork` 不得與 premarket 重疊、不得重複。
   讀不到或任一規則不符 → 兩項都擋（`quarantine_allowlist_*`）。
   `ALLOWED_ROOT_IDS`、`ALLOWED_SUFFIXES`、`PROTECTED_ROOTS` 在驗收器內有一份鏡像，
   測試以 AST 解析 `program_quarantine.py`（不 import、不執行）比對，避免漂移。

   `retirement_isolation`（任一項不符就擋，原因列在 `failures`）：
   - 每個 allowlist 項目在 `ALLOWED_ROOT_IDS` 對應的原位置都不存在
     （`os.path.lexists`）；仍存在 → `retired_program_file_present`。非共用的退役
     根目錄本身重新出現（但 allowlist 檔案不在）只記標籤 `retired_program_root_present`。
   - 現行程式樹沒有指向退役程式（`retired_program_referenced_by_current_code`）。
     掃描範圍（blocking）：`D:\MIX All\src`、`D:\MIX All\tools`、`D:\MIX All`
     頂層、`D:\stock\programs\single_stock_analysis`、`telegram_report_delivery`、
     `shared`、`shared_data_management`、`D:\TWStockData\services\market_update`、
     `services\shared_market_data_control`。副檔名 `.py .pyw .cmd .bat .ps1 .psm1 .mjs .js`；
     排除目錄名 `tests test fixtures backup backups __pycache__ .venv venv site-packages node_modules .git .pytest_cache external_entrypoints`、
     排除檔名 `test_*.py`／`*_test.py`／`conftest.py`；不跟隨 symlink；註解與字串
     都算引用（保守）。比對方式：(a) 文字比對退役檔完整路徑、其上層子目錄與
     非共用退役根目錄（`/` 與跳脫的 `\\` 先正規化、不分大小寫）；(b) 路徑片段
     `\full_market_analysis`、`\premarket_daytrade_top10`、`\twmix`、
     `\daily_analysis_scheduler`（抓相對路徑），以及等於這四個名稱的引號字串
     （`"twmix"`、`'full_market_analysis'` 等，抓 `Path(...) / "..."`、
     `os.path.join`、`Join-Path` 分段組出的路徑）；(c) Python AST 的
     `import`／`from ... import`／`importlib.import_module`／`__import__` 的頂層模組
     名稱等於退役 `src\` 下的模組（`cowork`、`twmix`、`premarket_top10`、
     full_market_analysis 的 `src\*.py`），但現行樹內有同名模組時視為現行模組
     （列在 `retired_python_modules_shadowed_by_current_module`）。`D:\cowork`
     本身仍有 logs／backups，所以只把退役檔與其子目錄當成引用，`D:\cowork`
     根目錄不算。blocking 範圍不存在 → `retirement_reference_scan_scope_missing`；
     檔案讀不到 → `retirement_reference_scan_file_unreadable`。
   - 非 retained 的工具／舊樹只記標籤 `retired_program_referenced_by_non_retained_tree`
     （不擋）：`D:\stock\programs` 頂層、`maintenance`、`environment_setup`、
     `services\market_update\external_entrypoints`（Central 測試用的保險副本）、
     `services\daily_orchestration`、`services\workflow_scheduler`、
     `services\acceptance`。完全不掃：`cross_system_validation`（本身持有退役名稱
     denylist）。
   - 排程：沿用 `schedule_reconciliation` 同一次唯讀查詢（MIX 08:00 與舊排程名稱）
     的 task XML；啟用中的工作指向退役程式 → `retired_program_referenced_by_enabled_task`，
     停用的只記標籤；查詢失敗 → `retirement_schedule_query_unavailable`。其他名稱
     的工作不在觀察範圍（結果中 `scope_note` 已註明）。

   `data_retention_guard`（任一項不符就擋）：
   - allowlist 每項副檔名都在 `ALLOWED_SUFFIXES`（`.py .cmd .bat .ps1 .mjs`）→
     否則 `data_retention_allowlist_non_program_entry`；沒有任何項目位於
     `PROTECTED_ROOTS` 之內 → 否則 `data_retention_allowlist_entry_in_protected_root`。
   - 現行資料權威根目錄 `D:\TWStockData\shared_market_data`、
     `D:\TWStockData\program_data` 存在且非空 → 否則
     `data_retention_data_root_missing`／`data_retention_data_root_empty_or_unreadable`。
   - 直接沿用 `central_publication.checks.published_head` 的結果（不重算）：head、
     同 SHA 的 `_manifests` 副本與每條 route 的 `_objects` 完整 → 否則
     `data_retention_central_publication_not_intact`。
   - 退役程式的舊資料根目錄（`D:\stock\data`、`D:\cowork\data`、
     `D:\claude code\twmix\data`）只觀察：不存在記 `legacy_data_root_absent`
     標籤（不擋），因為現況無法判斷是否於退役時刪除或搬移。`D:\stock\data`
     已不是資料權威（`shared\price_adj_commit_rebuild.py` 把它當禁用根目錄）。
9. `schedule_reconciliation`：不再讀從未有 producer 的
   `schedule_reconciliation.json`，改為唯讀查詢舊 v1/v2 排程名稱（Central
   03-00/02-00、Premarket Top10、MIX ALL Daily、cowork、twmix、D-Stock 07-00、
   D-Stock Telegram 08-00）：不存在或停用 → pass（停用記標籤），仍啟用 → 擋。

## 已退役、不再讀取的證據

| 路徑 | 原 component | 退役原因 |
|---|---|---|
| `services\market_update\receipts\latest_manifest.json`、`source_seal.json`、`tools\market_update_scheduler\CENTRAL_SOURCE_EXPECTED_MANIFEST.json` | central_publication | Central 已改為 requirements v3 C0 六條 route 與 `stock_data_current.json` head；這些檔案已不存在 |
| `services\scheduler\state\workflow_scheduler_v1\latest_final_receipt.json` | workflow_scheduler | 舊 Central→MIX v1→三竹 workflow；MIX 三竹入口已不存在，現行排程是 Windows Task |
| MIX v1 三竹 `mix-mitake-remake-close-reopen-v2` | mix_mitake_persistence | C0 不做三竹 |
| `program_data\dstock_single\reports\latest_report_manifest.json` | single_stock_analysis_report | 舊個股流程（v3 manifest，最後寫入 2026-09-30）；現行 C0 不寫 |
| `services\telegram\data\telegram_delivery_state.json` | telegram_service_publication | 舊 D-stock 全市場報告傳送（`telegram_report_sender.py` 已只剩憑證工具）；單股 Bot 的寄送紀錄是 `stock_command_bot.delivery.jsonl`，屬按需寄送，不列入驗收 |
| `services\acceptance\schedule_reconciliation.json` | schedule_reconciliation | 從未有 producer，改為唯讀查詢 |

## 2026-10-12 移除的死碼

`main()` 一律只呼叫 `_retained_payload`。以下整段從
`verify_stock_programs.py` 移除（已確認 `D:\stock`、`D:\MIX All`、
`D:\TWStockData\services` 底下 .py/.ps1/.cmd/.json 沒有任何呼叫端）：

- `_central_mix_live_payload` 與全部 `_central_mix_live_*`（含要求 MIX v1
  publication receipt、target_date、三竹 receipt 的
  `_central_mix_live_publication_evidence`／`_central_mix_live_mitake_evidence`；
  C0 不產生這些，該路徑必定 fail closed）。
- 舊六程式相容驗證 `_reports_check`、`_program_result`、`_structure_check`、
  `ProgramSpec` 與只被它們使用的 pointer／metadata／source-log／
  shared-data／governance helper。

`--central-mix-live` 保留為唯讀別名，避免既有命令列失效。原檔備份：
`D:\cowork\backups\nonblock_20261012\stock\programs\cross_system_validation\src\verify_stock_programs.py.pre_align_1`。

## 測試

```bat
set PYTHONDONTWRITEBYTECODE=1
D:\stock\.venv\Scripts\python.exe -B -m pytest -p no:cacheprovider tests
```

測試全部使用 fixture，不查詢真實 Task Scheduler；讀取正式檔的只有（皆唯讀）
`MIX_WEEKDAY_0800_TASK_DEFINITION.json` 的契約比對、以 AST 解析
`services\acceptance\program_quarantine.py` 比對鏡像常數，以及用 entry 規則讀
`program_quarantine_allowlist_v1.json`。
