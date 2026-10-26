# 已退役的共用測試

這裡的測試檔測的是 current-only 清理時已移除的共用模組。檔案保留供參考，
pytest 不會收集（`tests\conftest.py` 的 `collect_ignore = ["_retired"]`）。
不要刪除這些檔案；若日後恢復模組，請把對應測試搬回 `tests\`。

| 測試檔 | 缺少的模組 |
| --- | --- |
| `test_central_migration_contract.py` | `central_publication_reader` |
| `test_market_update_client.py` | `market_update_client`（另外也 import `global_market_data`、single_stock 的 `tw_single_stock_3m_analyzer`、`tw_industry_data`） |
| `test_shared_date_readers.py` | `central_publication_reader`、`global_market_data`、`market_price_adj_store`、`market_update_client`、`tw_market_calendar`、`central_single_stock_projection` |
| `test_stock_data_paths_head_v2.py` | `stock_data_paths` |
| `test_stock_data_paths_pinned_session.py` | `central_publication_reader`、`stock_data_paths` |

## 證據（20261012 查核）

1. 這些模組已不在 `D:\stock\programs\shared`。
2. 刻意退役的紀錄：
   `D:\TWStockData\artifacts\current-only-execution-20261009\execution\github\stable_current_source_retirement_gate.json`
   的 `input_retired_live_paths` 與 `removed_exact_lines` 列出
   `programs\shared\central_publication_reader.py`、`central_single_stock_projection.py`、
   `global_market_data.py`、`market_price_adj_store.py`、`market_update_client.py`、
   `stock_data_paths.py`、`tw_market_calendar.py`。
3. 現行程式沒有 import：在 `D:\stock`（排除 backups、.venv）、`D:\MIX All`、
   `D:\TWStockData\services` 以 grep 搜尋 `import`／`from` 這三個模組名，
   只命中本資料夾的這幾個測試檔與 backups 內的舊副本。其他只出現在字串或註解中
   （`VERIFY_RUNTIME_DATA_PATHS.py:50` 註解、single_stock 的
   `test_legacy_wrapper_contract.py:18` 禁用字詞清單）。
   例外：`D:\TWStockData\services\market_update\tests\test_required_routes_full_union_acceptance.py:47,1570`
   仍以路徑載入 `central_publication_reader`，那是 market_update（codex 負責）的測試，不在本資料夾處理。
4. `D:\stock` 是 git repo，但 `programs\shared` 未被追蹤（`git status` 顯示 `??`），沒有 git 歷史可查；
   舊版模組副本在
   `D:\stock\programs\single_stock_analysis\backups\20261006T201132Z_dates_v1\programs\shared\`
   （`central_publication_reader.py`、`market_update_client.py`）與
   `...\backups\20261005T135417Z_v2_final\programs\shared\stock_data_paths.py`。
5. 修改前的 baseline（`baseline_tests\v2_shared.txt`）與整合後（`unbind_qa\integration\int_shared.txt`）
   這五個檔案都是同樣的 collection error（ModuleNotFoundError），與本次 unbind 改動無關。

`test_shared_date_readers.py` 中唯一測現行模組的
`test_finmind_producer_date_key_never_takes_a_json_number`（`finmind_producer.py` 仍存在）
已原樣搬到 `tests\test_finmind_producer_date_key.py`，繼續收集。
