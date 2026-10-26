# Claude → Codex：個股分析「準確度／誤差、執行速度、clean code」交接（2026-09-30～10-01）

產出：Claude（session `51ab15cb-7b36-4847-b785-5ba122936b29`）
用途：給 codex 核對。這是紀錄，不是要你執行的計畫；需要你決定的事集中在第 7 節。
決策紀錄：`D:\stock\docs\decisions\ADR-0007-single-stock-global-market-pre-open-availability.md`
證據：`D:\stock\logs\single_stock_overnight_global_20260930\`（含 `research\` 研究腳本與測試紀錄）

---

## 0. 範圍與所有權（先看）

- 個股分析在 09-27～09-30 仍有另一個 session 在大改：
  - 新增共用（50 檔合併）訓練：`tw_single_stock_train_shared.py`、`shared_model`、`shared_training`、`window_plan`、`native_calibration`、`shared_comparable`。
  - 保留路由單檔列快取與批次預熱（`stock_data_sources.py`，09-30 00:06）。
  - 權重下限改 0、「機率優先」政策。
- 我這次的修改都**建立在他們的最新版本上**。部署前逐檔確認正式檔與我沙箱的基準雜湊相同，期間沒有被別人改過。
- 使用者本次的要求依序是：
  1. 提升準確度、降低預估漲幅誤差（「執行」）。
  2. 部署時機選「現在部署並立刻重訓」。
  3. 追加 clean code 與提升執行速度，選「我做，先做加速再做 clean code」。
- **沒動**：TWStockUpdater、MIX、共同資料區、封存範圍以外的其他程式。

---

## 1. 準確度：找到的三個根因

1. **美股資料五年來從未進過模型**。共同資料區 `global_market_history` 的美股指數與類股 ETF 歷史列幾乎都沒有 `source_close_at`；
   `published_single_stock_reference.load_published_global_market_features()` 會丟棄沒有時區時間戳的列。
   實測 2330／2317 的特徵矩陣美股欄位 0 個、丟棄 20,013 列。
   日經／韓股的時間戳是 `…T00:00:00Z`（Yahoo 日 K 開盤時間），比實際收盤早 6 小時。
2. **決策時間實際是當天 00:00，不是註解說的 15:30**。`global_market_data._decision_datetime()` 遇到 `pd.Timestamp`（屬於 `datetime`）時不套 15:30。
   預測隔天開盤時，只看得到「大前天」晚上的美股。
3. **國際特徵閘門整組拒絕**（`evaluate_global_market_feature_set`）。方向模型要被採用，也要求漲跌幅誤差不能變差；
   方向與漲跌幅兩個模型共用一份 `feature_columns`。
   即使修了 1、2，50 檔合併模型在 29 個國際特徵下 AUC 0.625→0.670，但誤差 0.818→0.853pp，閘門就整組拒絕。

實證（單變數，前 70% 訓練、後 30% 樣本外，2025-04～2026-09）：
- 前一晚美股對隔天開盤跳空的相關：2330 與費半 0.650、2317 與科技 ETF 0.572。
- 三變數線性模型的開盤誤差：2330 少 23%、2317 少 20%；方向命中約 72%。
- 「前天」的美股幾乎沒有解釋力。

## 2. 準確度：修改（ADR-0007）

| 項目 | 檔案 | 內容 |
| --- | --- | --- |
| 可得時間 | `published_single_stock_reference.py` | 取「明確時區時間戳」與「交易所收盤規則」（美股紐約 16:15、日韓當地 15:40）較晚者；沒有時間戳的列用收盤規則。**推翻了既有規則「沒時間戳就不算訊號」**，原則同 ADR-0005 |
| 決策時點 | 同上＋`tw_single_stock_3m_analyzer.load_shared_global_market_features` | 新增 `decision_times` 參數逐日指定；只支援 published，Central 分支不變 |
| 次日開盤 | `tw_single_stock_tomorrow_open.py` | `tomorrow_pre_open_decision_times()`：每列用「下一個官方交易日 08:30」，推論列取 `min(08:30, 執行當下)`；國際特徵載入移到取得官方交易日序列之後 |
| 昨晚缺漏防護 | 同上 | `guard_overnight_global_features()`：推論列的 sp500／nasdaq／dow 最新交易日早於 D 時，把 `gm_us_*`、`gm_industry_*` 設缺值並標示；美股休市日也會觸發 |
| 閘門候選 | `tw_single_stock_3m_analyzer.py`（`GLOBAL_FEATURE_CANDIDATES_BY_MODE`、`evaluate_mode_global_feature_set`），三處訓練呼叫點改用它 | 次日開盤只給美股 1 日報酬＋`gm_industry_ret_1d`；三個月只給美股 20 日報酬；其餘國際欄位不進模型 |

驗證：
- **真實資料**：新載入器下 2330 費半 1 日報酬與隔天開盤跳空相關 +0.650、2317 +0.572。**偷看未來檢查 0 次**。
- **單檔模型**（09-26 輸入、只換國際欄位）：2330 AUC 0.544→0.792、開盤誤差 1.188→1.012pp；2317 被當時的全組閘門拒絕、不變。
- **閘門離線重播**（50 檔合併訓練資料，同一個閘門函式，精確重現訓練時的數字）：
  - 全部 29 個：拒絕。
  - 美股 1 日報酬 3 個：採用，AUC 0.625→0.678、Brier 0.2390→0.2264、誤差 0.818→0.795pp。
  - 三個月美股 20 日報酬：採用，誤差 14.90→14.68pp。
- **兩次重訓的保留期（最近 120 個決策日、50 檔合併、樣本外）**：見第 5 節。

## 3. Clean code

**第一批：非封存模組，不影響模型**
- 11 個檔、32 個沒有任何引用的定義，加上因此變多餘的 4 個 import，共約 708 行。
- 清單在 `research\dead_code_batch1_manifest.json`。
- 判定方式：以識別字計數掃描 src＋tests＋CMD＋tools＋shared；另查 MIX、TWStockUpdater、stock\programs，**沒有外部匯入**。
- 完整測試套件前後比對：差異只在既有的浮動測試組 `test_local_research_*`；多出的那條單獨重跑 3 次都通過。

**第二批：封存模組，需要重訓**
- 3m_analyzer 44 個、tomorrow_open 1 個、shared_training 1 個未被引用的定義，共約 592 行；清單在 `research\dead_code_batch2_manifest.json`。
- 移除 `fetch_market_factors` 空殼（永遠回傳空表），改為 `MARKET_FACTOR_SOURCES` 常數；報告的來源標記不變。
- 三個月的進度訊息「大盤/國際因子完成：0 筆」改為顯示實際國際特徵列數。
- 次日開盤報告「大盤/國際/總經因子」原本永遠 0 筆，而且在缺漏偵測名單裡；改成比照三個月報告的算法。
- `tests/test_single_stock_no_consumer_network.py` 原本斷言空殼存在；改為斷言空殼已移除、來源常數不變。「不連網」這個性質仍然成立。
- 完整測試套件前後比對：沒有新增失敗；新增 3 條包裝函式測試全部通過。

**沒做**：兩個超大檔（3m_analyzer 約 2 萬行、tomorrow_open 約 1.07 萬行）的結構性重構。另一個 session 正在同一批模組上重構，
而且封存模組每改一次就要重訓，這種規模的重構應該由負責重構的一方統一規劃。

## 4. 執行速度：量測與結論

**量測**：2330 正式執行的三個月子程序共 1,139 秒（09-30 23:23，冷快取）。

| 階段 | 秒 | 占比 |
| --- | --- | --- |
| 13 條模型路由準備 | 約 196 | 17% |
| **讀取 Central／Updater 保留來源** | **887** | **78%** |
| 官方 PIT | 31 | 3% |
| 模型推論＋報告 | 約 40 | 3% |

- 分析器載入只約 3 秒，不是瓶頸；clean code 帶來的是可維護性，不是速度。

**根因**：
- 保留路由 14 條、發布頭內合計 **27.4 GB**，其中 order 21（`finmind_trading_daily_report`）17.2 GB。
- 依設計（`test_published_row_cache.py`），**每次讀取都要把整份檔案重算 sha256**，快取只省 JSON 解析。
- 單檔列快取只涵蓋 3 條保留路由；2330 不在 50 檔批次預熱名單內，首次執行快取是空的；共同資料區每天發布，物件一變快取就失效。
- order 21 只用最近 60 個交易日（1／5／20／60 日視窗），但檔案保留 130 天，而且要整份讀完才挑得出一檔。

**本次沒有實作加速**：兩個有效方案都是架構或契約層級，需要 codex 決定（第 7 節 7.1、7.2）。
- 其一：兩個分析改在同一個程序內執行。現行 CMD 結構被測試鎖定。
- 其二：上游把 order 21 依股票分區或加索引。
- 另一個可能的做法是「驗證收據」，但它會違反「每次重算雜湊」這個刻意的不變式，所以不採用。

## 5. 部署、重訓與驗證結果

| 時間 | 動作 | 結果 |
| --- | --- | --- |
| 09-30 21:5x | 部署前一晚美股修正（3 個 src＋1 個新測試） | 基準雜湊比對 OK |
| 21:51 | 第一次重訓 | 我在註解寫了 ADR-0006，但該編號已被另一個 session 使用；**立刻停掉**，改為 ADR-0007（未發布任何東西） |
| 21:52～23:03 | 重訓（新程式碼） | 成功，但國際特徵在合併模型被閘門整組拒絕，模型指標幾乎不變 |
| 23:23～23:42 | 2330 正式驗證 | 三個月完成；**次日開盤因 `published_snapshot_pin_invalid` 失敗**：更新程式 resume 在 23:30 重新發布頭檔（同 run id），不是本次修改造成 |
| 23:4x | 部署第一批 clean code | 共用模型仍有效（非封存） |
| 23:5x | 部署第二批（clean code＋閘門候選） | 基準雜湊比對 OK |
| 23:54～10-01 00:56 | 第二次重訓 | 成功，國際特徵**被採用**（次日開盤 3 個美股 1 日、三個月 3 個美股 20 日） |

**第二次重訓的保留期指標（最近 120 個決策日、50 檔合併、樣本外）**：

| 次日開盤 | 前一次 | 本次 | 基準 |
| --- | --- | --- | --- |
| 方向命中 | 57.9% | **60.7%** | 51.1% |
| Brier | 0.2406 | **0.2321** | 0.2505 |
| 開盤誤差 | 0.988pp | **0.967pp** | 1.010pp |
| 最高／最低誤差 | 1.461／1.325pp | 1.456／1.314pp | 1.614／1.425pp |

| 三個月 | 前一次 | 本次 | 基準 |
| --- | --- | --- | --- |
| 方向命中 | 54.0% | 53.9% | 50.6% |
| 三個月誤差 | 16.58pp | **16.83pp（變差）** | 16.31pp |

三個月：閘門重播顯示改善、保留期卻略差，而且兩版都輸基準。
120 個 63 日期間彼此重疊，實際約 2 段獨立期間，差距在雜訊範圍內。列為 7.5 待決定。

**最終正式驗證（10-01 00:57，2330，177 秒後以結束碼 2 失敗、未發布）**：

- 三個月子程序：**成功**。
  - 共用模型 `shared-native-v1-three_month-h63-f1c034909da15206`，上漲機率 54.72%、預估 +0.63%。
  - 進度訊息已改為「大盤/國際因子完成：1214 列（partial）」（第二批 clean code 生效）。
- 次日開盤子程序：
  - 「海外市場（開盤前決策）：前一晚美股收盤尚未取得，本次不以較舊資料替代」——**防護如預期觸發**：01:00 執行，美股 04:15 才收盤。
  - 接著共用模型推論被 `formal_tomorrow_feature_qualification_incomplete` 擋下，原因碼 `formal_feature_session_not_in_official_sequence`。
- **根因：這不是本次修改造成的**。`qualify_tomorrow_formal_feature_inputs` 修改前後逐字相同。
  - 特徵矩陣第一天是 2021-09-29（模型窗往回 1,826 天），不在官方交易日序列裡。
  - 共同資料區的 `official_calendar`：`actual_min_date = 2021-09-30`，宣告的 `retention_filter.cutoff_date = 2021-09-24`，收集範圍 `current_only`。
    價格與官方行情則從 2021-09-24 開始。
  - 投影層 `_apply_calendar_retention_to_price` 依宣告起點 09-24 裁切，所以保留了 09-24～09-29。
    這是另一個 session 的刻意設計（測試 `test_weekend_retention_cutoff_keeps_rows_before_later_calendar_first_known`），
    但它與次日開盤正式資格檢查的「每一天都要在官方序列內」互相衝突。
- **影響**：目前所有股票的正式執行都會在次日開盤這一步失敗，整個報告不發布。行事曆只收今天往回 5 年，這個落差每天都會重現。
  我沒有自行修改，因為任何修法都會推翻另一個 session 的測試或設計，修法選項見 7.0。
- **速度（熱快取）**：
  - 三個月子程序約 2 分鐘：模型路由 0 秒、保留來源 49 秒、官方 PIT 32 秒、推論與報告約 40 秒。
  - 次日開盤子程序的保留來源 49 秒。
  - 對照冷快取時三個月子程序 1,139 秒。

另外 09-30 23:23 那次，次日開盤在更早的 `published_snapshot_pin_invalid` 就失敗了：更新程式 resume 於 23:30、00:25 重新發布頭檔（同 run id）。

## 6. 檔案、雜湊、備份

| 檔案 | 最終 sha256 前 16 碼 | 在封存內？ |
| --- | --- | --- |
| `src/published_single_stock_reference.py` | e621cab52675f701 | 是 |
| `src/tw_single_stock_3m_analyzer.py` | cc684ff84df95251 | 是 |
| `src/tw_single_stock_tomorrow_open.py` | a14601dfce52208b | 是 |
| `src/tw_single_stock_shared_training.py` | c6724ff6c6cb8138 | 是 |
| `tests/test_tomorrow_pre_open_global.py`（新） | 0240ee83df8776dc | 否 |
| `tests/test_global_feature_candidates.py`（新） | df1534152817fa22 | 否 |
| `tests/test_single_stock_no_consumer_network.py` | bca74a79a4f63126 | 否 |
| 第一批 11 個非封存 src | 見 `D:\stock\backups\claude_20260930_single_stock_dead_code_batch1\` | 否 |

備份：
- `D:\stock\backups\claude_20260930_single_stock_overnight_global\`：3 個 src 原檔，加上重訓前的 `shared_stock\current.json`。
- `D:\stock\backups\claude_20260930_single_stock_dead_code_batch1\`：11 個 src 原檔，加上死碼清單。
- `D:\stock\backups\claude_20260930_single_stock_sealed_batch2\`：3 個 src、1 個測試原檔、死碼清單，加上第二次重訓前的 `current.json`。

**還原注意**：還原任何封存檔都會讓共用模型失效，必須再跑 `tools\TRAIN_SHARED_STOCK_MODEL.cmd`。
參數：`--sample-manifest …\research_random_50\run_20260927T182753_seed20260927_pooled50\sample_manifest.json --modes three_month tomorrow_open --horizon 63`，約 60～70 分鐘。

殘留（可清理）：
- 第一次中斷的訓練目錄 `models\shared_stock\training\run_20260930T135111Z_df2907f2eff54079840e9afadc669252`。
- 第一次重訓完成、但模型已被取代的目錄 `models\shared_stock\training\run_20260930T135210Z_3e8444f5d4cc4b7a97acc9c10521aff1`（381 MB）。
  注意：第 5 節的前後比較與 `research\gate_experiment*.py` 會讀這個目錄。
- 兩次失敗的正式執行留下的暫存目錄：
  - `report_runs\.single_stock_staging_03e1bc05bb82424680222d9edb587f4d`（09-30 23:23）
  - `report_runs\.single_stock_staging_99fb59a14cca4ee78b6f226debe48af0`（10-01 00:57）

目前生效的共用模型：`models\shared_stock\current.json`，來自訓練目錄 `run_20260930T155409Z_a880de74258f4443bad8fdd9bb843e8a`
（三個月 10-01 00:35、次日開盤 00:56）。

## 7. 需要 codex 決定／處理

0. **阻斷中，最優先：所有正式報告在次日開盤資格檢查失敗**（見第 5 節最終驗證）。三種修法擇一：
   - (a) **上游**：`official_calendar` 的實際內容涵蓋到宣告的 `retention_filter.cutoff_date`；或宣告值改成實際第一天。這是根本解。
   - (b) **投影層**：`_apply_calendar_retention_to_price` 改以 `max(宣告起點, calendar_first_known_date)` 裁切。
     非封存、不用重訓，但要推翻 `test_weekend_retention_cutoff_keeps_rows_before_later_calendar_first_known`。
   - (c) **次日開盤正式路徑**：資格檢查前剔除行事曆涵蓋前的列。封存模組，要重訓。
   - 另外，三個月模型的起點是 1,826 天，`analysis_start_cut` 是 1,825 天（`round(365.25×5)` 與 `int(365×5)`），差一天，建議一併統一。
1. **速度，最優先**：上游把 order 21 依股票分區（或提供股票索引），或縮短保留。目前每個子程序都要把 17.2 GB 讀完、雜湊完，才挑出一檔股票約 60 天的資料。
2. **速度**：兩個分析改在同一個程序執行，保留路由與模型路由都只驗證一次，可省去第二個子程序約 37 GB 的雜湊。會改變正式交易的 CMD 結構與相關測試。
3. **上游每天台股開盤前更新 `global_market_history`**（約 8.5 MB）。否則推論列會觸發昨晚缺漏防護，**模型改善在實際預測時用不到**。
   可用 `update --successor-of … --keep-completed --retry-orders <國際資料路由>`，但要排程（常駐設定，需使用者同意）。
4. **正式執行期間上游重新發布會讓第二個子程序失敗**（`published_snapshot_pin_invalid`）。今晚更新程式 resume 約每小時重新發布一次頭檔。
   建議：正式執行期間暫停發布，或 pin 驗證只比對物件而非頭檔。
5. **三個月的國際特徵**：保留期略差、在雜訊內。要不要把三個月候選改回空（不加國際特徵），下次重訓時生效。
6. **ADR-0007 推翻既有測試規則**（沒時間戳就不算訊號 → 用交易所收盤規則）。請確認這個原則。
7. **補台積電 ADR（TSM）與台指期夜盤**。目前共同資料區都沒有；ADR 對 2330 理論上比費半更直接，未驗證。
8. **合併模型沒有產業對應的國際特徵**：`gm_industry_*` 不是 50 檔共有，取交集時被排除。可考慮對每檔映射到產業指數，或以寬基指數補值。
9. **三個月的決策時點 00:00**：是否改成 15:30（決策時點注釋與實作不一致）。
10. **使用者 09-19 的「方向機率下限 0.5」已被另一個 session 改成 0**，與使用者當時的選擇不同，請與使用者確認。
11. **09-27 的重新提交把 2330 次日開盤從 5 年換成 3 年**：那一格原本由使用者委託 Claude 判斷、選 5 年。
