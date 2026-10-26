# 已退役測試（不收集）

這個資料夾保留「受測模組或功能已被刻意移除」的測試，內容為原檔逐字搬移（2026-10-12），
只作紀錄，不再執行。`tests/conftest.py` 以 `collect_ignore = ["_retired"]` 讓 pytest 不收集。

共同證據：

- `D:\stock` 是 git repo，但 `programs/telegram_report_delivery/` 未納入版控（`git ls-files` 為 0 筆），
  所以沒有 git 歷史可查；以下用目前原始碼與兩份備份比對。
- 本次修改前的備份 `D:\cowork\backups\unpin_step2_9_20261012T023006\stock\programs\telegram_report_delivery\`
  已經沒有 `telegram_command_bot_e2e.py`、`INSTALL_WEEKDAY_0800_TASK.*`，三個 .py 也已是 C0／current-only 版本；
  也就是說，這些功能是更早的 current-only 清理移除的，不是本次 unbind 改動造成。
- 舊實作仍可在 `D:\stock\programs\single_stock_analysis\backups\20261006T201132Z_dates_v1\programs\telegram_report_delivery\`
  找到（下表簡稱「20261006 備份」）。
- 目前的入口都不 import 被移除的模組：在 `D:\stock\programs` 中 grep `telegram_command_bot_e2e`／
  `INSTALL_WEEKDAY_0800_TASK`（排除 backups），只有 `README.md` 與 tests 命中。

| 退役檔案 | 移除的模組／功能 | 證據 | 現行行為改由哪些測試涵蓋 |
|---|---|---|---|
| `test_telegram_report_sender_manifest_delivery.py`（`make_bundle`、`FakeTelegramClient` 與 15 個測試） | D-stock 全市場報告 manifest 傳送：`load_report_bundle`、`deliver_bundle`、`wait_for_stable_bundle`、`ReportBundle`、`ReportUnavailableError`、`ReportDeferredError`、`REPORT_MANIFEST_SCHEMA`、`DEFAULT_MANIFEST_PATH`、`_validate_versioned_manifest`、`_validate_manifest_payload`、`_canonical_sha256` | `telegram_report_sender.py:419`、`:433`「舊D-stock全市場報告CLI已退役，只接受 --configure 或 --check」；`RUN_SEND_LATEST_REPORT.cmd` 改轉呼 `RUN_SEND_MIX_REPORT.cmd`；20261006 備份 `telegram_report_sender.py:70`（`REPORT_MANIFEST_SCHEMA`）、`:640`（`load_report_bundle`）、`:742`、`:1000` | `tests/test_telegram_report_sender.py`（只剩 `--configure`／`--check`；path_contract 1.x／2.x；root KEEP）；報告傳送的完整性改由 `tests/test_mix_report_sender.py` 的 `test_c0_*` 測試涵蓋 |
| `test_mix_report_sender_formal_bundle.py`（`_receipt_digest`、`make_fixture` 與 23 個測試） | MIX 舊「正式 bundle」：`LATEST_PRODUCTION_RUN.json`、`current/last_run_status.json`、central receipt／registry、官方交易日曆 receipt、publication receipt、Mitake receipt（`_canonical_mitake_payload_digest`、`mitake_result_path`）、run-local evidence session、`_validate_central_calendar` | `mix_report_sender.py:1`「current MIX C0 report refs」、`:32` `MIX_POINTER_PATH = …/LATEST_C0_RUN.json`、`:99-106` 的 `MixSenderPaths` 已沒有 status／central／mitake／evidence 欄位；20261006 備份 `mix_report_sender.py:58`（`LATEST_PRODUCTION_RUN.json`）、`:283`、`:440` | `tests/test_mix_report_sender.py` 的 `test_c0_*`（含參數化共 42 個）：ref 位元組竄改、schema／status／H、run namespace、run root 外路徑、重複 JSON key、未知 artifact、ETF／非普通股排除、coverage、筆數與 H1/5/20/60、formal-model 宣稱、同 run manifest／payload 綁定、`--expected-asof`／`--target`、較新資料只記標籤、delivery 冪等、message_id 可重試、上傳前位元組變動 |
| `test_telegram_dates_legacy_formal_and_e2e.py`（18 個區塊） | bot 的 `_date_key`、`_valid_data_date`、`read_shared_raw_pin`、central `analysis_environment`、formal／provisional／tomorrow_open 報告與 caption；`telegram_report_sender.build_summary_text`／`wait_for_stable_bundle`；MIX `_validate_central_calendar`；live e2e harness `telegram_command_bot_e2e.py` | 目前模組沒有這些名稱（以 `hasattr` 逐一確認）；`telegram_command_bot_e2e.py` 不在樹中（20261006 備份 392 行，`:41-43` 驅動 `three_month`／`tomorrow_open`）；bot 現在只有 `MODE_H63`／`MODE_H1`／`MODE_BOTH`（`telegram_stock_command_bot.py:108-110`） | `tests/test_telegram_dates.py`：C0 caption 的訊號日／到期日／資料時間／「執行期間已有較新資料」、無估計 caption、MIX 日期兩種拼寫比值、YYYYMMDD 顯示 |
| `test_telegram_service_config_live_e2e.py`（`test_live_e2e_entrypoint_is_bounded_and_observational`） | live e2e harness `telegram_command_bot_e2e.py` 與 `RUN_TELEGRAM_COMMAND_BOT_E2E.cmd` | 兩個檔案都不在樹中，也不在本次修改前的備份中；harness 驗的是已移除的 `three_month`／`tomorrow_open` 流程 | 無直接替代（真實 Telegram 實機驗證本來就不在單元測試範圍） |
| `test_telegram_bot_legacy_provisional_central.py`（17 個測試；2026-10-12 搬入時原名 `test_telegram_stock_command_bot_legacy_provisional_central.py`，檔名主體 55 字元超過 `VERIFY_STABLE_PROGRAM_PATHS.ps1` 的 48 字元上限，同日改為現名，內容逐位元組不變） | bot 的 provisional／raw 降級來源與 central 正式閘門：`shared_current_head_exists`、`read_shared_raw_pin`、`provisional_environment`、`_validate_provisional_report`、`PROVISIONAL_*`、`FORMAL_SINGLE_REPORT_SCHEMA`、`CENTRAL_*` 環境變數與 receipt hash、`MODE_THREE_MONTH`／`MODE_TOMORROW_OPEN` 的 caption | 目前模組沒有這些名稱；20261006 備份 `telegram_stock_command_bot.py:129-130`、`:736`、`:1521`、`:1558`、`:1573`、`:1581` | `tests/test_telegram_stock_command_bot.py`：子程序環境去除秘密並關閉網路、job 開始 pin 與 `--pinned-head-sha256`、binding 失敗改用已發布 head 並標示、head 完全讀不到或竄改才失敗、工作期間較新資料／模型變更只記標籤、上傳前完整性失敗不上傳、C0 估計與無估計報告的完整性負向測試 |

在原檔就地改寫、沒有搬到這裡的測試（計畫刻意改變的行為，例如 C0 模式名稱、版本差異改為標籤），
請見各測試檔內的說明註解。
