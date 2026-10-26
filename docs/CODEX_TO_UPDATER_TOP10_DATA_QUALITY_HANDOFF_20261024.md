# TWNextDayTop10 來源資料問題交接（2026-10-24）

狀態：**consumer 必要驗證通過；25 股來源值問題待 producer 接手，9 股跨減資基底仍待證、4 股合法停牌待補 lifecycle 標記**。本文件只整理已存在的同 pin 證據與後續驗收方向，未修上游、未重抓資料、未更新 canonical/head、未重訓、未執行正式分析或發布。

## 摘要與結論邊界

原案共 **41 股＝37 股 C0 排除＋4 股 C0 unavailable**。不重複分類為：3 股合法轉市場的 consumer 誤判、9 股跨缺交易日比較失效、22 股零量無報價非延續、1 股獨立除息基礎不一致（8936）、2 股 IPO 首兩筆基礎不一致、4 股合法停牌、無分析日交易價格。**8102 已算在 22 股中**，另有一個除息事件，不另增加股數；22 股共有 25 個 placeholder 事件，除息邊界另有 8102/8936 兩事件。

3 股轉市場與 9 股跨缺日比較由目前對話修 consumer。來源交接分為：**25 股已發布序列不一致；9 股已證合法減資停買段，跨事件 adjusted 基底待上游證明；4 股已證合法停牌。** 9 股缺 session 與 4 股尾端無報價都不能稱為行情漏抓。最初調查清單的股數不能當作來源錯誤數量。25 股的 published 差異已可量測，但責任仍未證實落在 provider 原回應、adapter、歷史 vintage 合併或發布哪一層。

使用者已要求「有可用資料仍分析並可發布」。consumer 已改以真實最後 feature 日推論，揭露分析日、個股資料日、個股目標日與落後提醒；這項 consumer 行為不代表上游補齊，也不把舊資料標成 2026-10-22。

37 股 C0 原排除中，**31 股已存在 preopen snapshot 與 view**。另 6 股（3085、4305、5520、6212、6236、8047）在兩者都不存在；4 股 unavailable 也都不存在。因此不能稱這 41 股「全都沒有報告」。C0-only 顯示整合已通過必要驗證，不能把歷史目標列混成當期 preopen Top10。

## 固定觀測與授權邊界

- 分析 asof：**2026-10-22**；市場已驗證日期軸均為 253 sessions，2025-10-07～2026-10-22。
- 固定 head/pin：`18e48d4f5e2f74ad5d68c26d28ded68756adb0e272f33146736028c17771bc2e`。
- producer：`twstock_updater`；run：`20261023T064039Z-2c950abe`；head published_at：2026-10-23T15:02:51.983173+08:00。
- 證據釘在上述不可變 pin；日後若 live head 已變，應另外記錄新 head，不能把新數值回填為本 pin 當時已正確。
- source receipt 記錄各大型來源只串流抽取一次，stream hash 等於 expected hash、stat 前後不變；本文件沒有再次掃大型 source。價格與 ordinal 結論使用這份抽取與 findings；2026-10-24 的官方 lifecycle 補證另列，沒有改寫原 pin。
- 本文件只交接，不授權另一對話執行修復。不得直接改 canonical object、head、manifest、receipt、正式模型、報告、ledger、服務或排程。

## 證據索引與來源身分

以下 P/O/I/C 是逐股表中的固定 source 參照；每次出現 P/O 都指向此表的完整 immutable path 與 SHA-256。`ordinal0` 是原來源物件中 **0-based 原始 row ordinal**，不是抽取 JSONL 的行號。抽取保存 `raw_row` 與 `raw_json`，可核對原欄位型別、明確 null 及缺 key。


| 代號 | route | 來源路徑與完整 SHA-256 | 抽取與 SHA-256 |
| --- | --- | --- | --- |
| P | finmind_price_adj | [immutable source](<D:/TWStockData/shared_market_data/_objects/e9f46d290cf796fc93c34c04b2715705336692c18eef985fe59e1d235d33bbdf.json>)<br>SHA-256 `e9f46d290cf796fc93c34c04b2715705336692c18eef985fe59e1d235d33bbdf` | [抽取 JSONL](<D:/stock/TWNextDayTop10/data/exclusion_audit_20261024/extract/finmind_price_adj.jsonl>)<br>SHA-256 `9265fc3d45eceb5d4e2d1a029bf443f654e06c71d955ae0b3c345041c0ec14cb` |
| O | official_market_daily | [immutable source](<D:/TWStockData/shared_market_data/_objects/dbe66c1bd2465c4c0a335d1975c842487fbf262d07aeb6a103e2363ba1c92807.json>)<br>SHA-256 `dbe66c1bd2465c4c0a335d1975c842487fbf262d07aeb6a103e2363ba1c92807` | [抽取 JSONL](<D:/stock/TWNextDayTop10/data/exclusion_audit_20261024/extract/official_market_daily.jsonl>)<br>SHA-256 `244847d7d76c35d7627fd0b50cf53294ce15e0a176671926127ca58be78e6c3c` |
| I | finmind_stock_info | [immutable source](<D:/TWStockData/shared_market_data/_objects/7fa16f32cb7b6e645675b31fa947e9a7382e0ae3687673f6d8b66643949641c6.json>)<br>SHA-256 `7fa16f32cb7b6e645675b31fa947e9a7382e0ae3687673f6d8b66643949641c6` | [抽取 JSONL](<D:/stock/TWNextDayTop10/data/exclusion_audit_20261024/extract/finmind_stock_info.jsonl>)<br>SHA-256 `832d1e45661a3f9899843cd19074c87870a710efb2581f16b733e1494aab9bd7` |
| C | current_ordinary_identity | [immutable source](<D:/TWStockData/shared_market_data/_identity/current_ordinary/79cd8736bfb9a85fa709928e7b0254ade19705be1153e2624df772d753b6e5c5.json>)<br>SHA-256 `79cd8736bfb9a85fa709928e7b0254ade19705be1153e2624df772d753b6e5c5` | 41 股 current identity 由 receipt/lifecycle findings 留證；沒有另存 JSONL |

| 參照 | 檔案 | 內容／限制 |
| --- | --- | --- |
| T | [targets.json](<D:/stock/TWNextDayTop10/data/exclusion_audit_20261024/targets.json>) | 41 股原 reason、原 case_kind、preopen/view presence 與各市場日期軸 |
| X | [extract_receipt.json](<D:/stock/TWNextDayTop10/data/exclusion_audit_20261024/extract_receipt.json>) | head、來源雜湊、stream/stat 驗證及逐股 rows_by_route |
| R | [reader_findings.json](<D:/stock/TWNextDayTop10/data/exclusion_audit_20261024/reader_findings.json>)；[說明](<D:/stock/TWNextDayTop10/data/exclusion_audit_20261024/reader_findings.md>) | 32 股、36 事件；事件日期、原價、缺日 presence、ordinal0、門檻與判定 |
| L | [lifecycle_findings.json](<D:/stock/TWNextDayTop10/data/exclusion_audit_20261024/lifecycle_findings.json>)；[說明](<D:/stock/TWNextDayTop10/data/exclusion_audit_20261024/lifecycle_findings.md>) | 3 股轉市場與 2 股 IPO；官方事件、raw 市場序列、價格樣本與 ordinal0 |
| F | [forecast_findings.json](<D:/stock/TWNextDayTop10/data/exclusion_audit_20261024/forecast_findings.json>)；[說明](<D:/stock/TWNextDayTop10/data/exclusion_audit_20261024/forecast_findings.md>) | 4 股尾端報價、原生 feature/last_price、來源 ordinal0 與未證事項 |
| V | [display_findings.json](<D:/stock/TWNextDayTop10/data/exclusion_audit_20261024/display_findings.json>)；[說明](<D:/stock/TWNextDayTop10/data/exclusion_audit_20261024/display_findings.md>) | 日期與 C0-only 顯示契約；原快照/成品未被改寫 |
| G | [gap_lifecycle_findings.json](<D:/stock/TWNextDayTop10/data/exclusion_audit_20261024/gap_lifecycle_findings.json>)；[說明](<D:/stock/TWNextDayTop10/data/exclusion_audit_20261024/gap_lifecycle_findings.md>) | 9/9 官方減資停復買、原欄位／換股率／退款／股利、4 份 HTTP receipt 與本文 hash；跨事件 adjusted 基底仍未證 |
| PIN | [固定 manifest](<D:/TWStockData/shared_market_data/_manifests/18e48d4f5e2f74ad5d68c26d28ded68756adb0e272f33146736028c17771bc2e.json>) | manifest 大小 80,627,466 bytes；pin 依本次 source receipt，未由本文件重掃 |
| 原 C0 audit | [post_recheck](<D:/stock/TWNextDayTop10/data/read_audit_20261023/run_20261023_230457/post_recheck>) | 保存的 panel/forecast 與原驗證成品；不得用重跑覆寫 |

R 中 32 股 14,120 筆原 price dict 沒有 `adjustment_factor`、`_shared_raw_spread` 或其他 factor/adjust 欄。本文與 findings 的 `derived_adjusted_to_official_close_ratio` 僅為「adjusted close ÷ 同日 official close」的**衍生比例**，絕非 provider 原始 adjustment_factor。

## A．3 股合法轉市場：consumer 整股排除誤判

原 reason 均為 `price_window_official_market_mismatch`。官方事件與同 pin official 列共同支持非重疊市場切換；253 軸內沒有量到超過既有 0.1% basis 容差的差異。本輪採「結束在本市場 asof 的最新單一 current-market 連續觀測段」，保留 253 日期軸並揭露截除舊段；剩餘 observations 數不是新增最低天數門檻。

consumer 狀態：**已修、必要驗證通過**。本類沒有已證實的上游價格錯誤；若要將完整跨市場歷史接回，仍需可驗證的 issuer/ISIN、from/to、effective date 與公告 hash，不可只憑代碼、名稱或 current table 日期。


| 股票／現市場 | 切換與事件 | 轉板日 adjusted 算術 | 來源 ordinal0／證據 | current 段 | 處理狀態 |
| --- | --- | --- | --- | --- | --- |
| TPEX/6423 億而得 | TWSE→TPEX；2026-01-22<br>[官方事件](<https://www.twse.com.tw/staticFiles/news/news/tsecnews/8a8216d69a3d6cf9019bc625f80f075e.pdf>) | 84.94343 → 93.348775 − (8.405345)＝84.94343 | P#56489；O#1063826<br>[L：6423](<D:/stock/TWNextDayTop10/data/exclusion_audit_20261024/lifecycle_findings.json>) | 2026-01-22～2026-10-22；可用 180、截除 73 | consumer 已修、必要驗證通過；historical unknown 保留 |
| TWSE/5236 凌陽創新 | TPEX→TWSE；2026-07-16<br>[官方事件](<https://www.twse.com.tw/rwd/zh/company/newlisting?response=html>) | 156.5 → 152.5 − (-4)＝156.5 | P#715450；O#1280629<br>[L：5236](<D:/stock/TWNextDayTop10/data/exclusion_audit_20261024/lifecycle_findings.json>) | 2026-07-16～2026-10-22；可用 68、截除 185 | consumer 已修、必要驗證通過；historical unknown 保留 |
| TWSE/8908 欣雄 | TPEX→TWSE；2026-10-16<br>[官方事件](<https://www.twse.com.tw/rwd/zh/company/newlisting?response=html>) | 39.8 → 40.2 − (0.4)＝39.8 | P#708859；O#1405498<br>[L：8908](<D:/stock/TWNextDayTop10/data/exclusion_audit_20261024/lifecycle_findings.json>) | 2026-10-16～2026-10-22；可用 5、截除 248 | consumer 已修、必要驗證通過；historical unknown 保留 |

current identity 的 scope 是 `current_inference_only`，`historical_classification_verified=false`、`historical_universe_verified=false`；corroboration 的 historical effective dates 亦未驗證。現行 ISIN 或 source_listed_date 不能自行成為歷史 issuer interval。保留同日雙市場、最後段不單一、明確 price market 錯誤與多 positive identity 等完整性拒絕。本 audit 未重掃完整 manifest，不把「此次所讀 identity 沒有 typed lifecycle」擴張成「全部 route 都沒有」。

## B．9 股合法減資停買段：consumer 跨事件比較失效，adjusted 基底仍待上游證明

原 reason 均為 `basis_break`。2026-10-24 官方窄查已證明 **9/9 股的原缺 session 全部落在合法減資停止買賣區間**，不能交接成已證實漏抓。原 basis 前值逐股等於官方舊股最後收盤，觸發列 close−spread 則等於官方新股恢復買賣參考價（浮點差 ≤1e-10）；它們分屬減資前後股數與價格尺度，不能當相鄰普通交易日直接比較。

**合法停買段已證，跨事件 adjusted 基底待上游證明。** consumer 狀態為**已修、必要驗證通過**：只停用跨缺日而前提失效的前收／quote anchor 比較，保留 PARTIAL 與未驗證提示；不改 basis 門檻 0.001，不 hardcode 9 股豁免，不放寬連續交易日品質檢查，也不造停止期間價格。

下表保留原觸發算術、全部缺日與 source ordinal0；P/O 無列是該 pin 的事實，官方事件另見下一表。
| 股票 | 觸發日 | 原 anchor | 觸發算術 | 缺交易日（P/O 均無列） | 來源 ordinal0／證據 |
| --- | --- | --- | --- | --- | --- |
| TPEX/2067 嘉鋼 | 2026-10-19 | 2026-10-06：4.8<br>P#173936 | 5.9 − (-0.5)＝6.4<br>相對差 33.333333%；門檻 0.1% | 7 日：2026-10-07, 2026-10-08, 2026-10-12, 2026-10-13, 2026-10-14, 2026-10-15, 2026-10-16 | P#173937；O#1406391<br>[R：2067](<D:/stock/TWNextDayTop10/data/exclusion_audit_20261024/reader_findings.json>) |
| TPEX/3625 西勝 | 2026-10-19 | 2026-10-07：10.1<br>P#595505 | 20 − (0.14)＝19.86<br>相對差 96.633663%；門檻 0.1% | 6 日：2026-10-08, 2026-10-12, 2026-10-13, 2026-10-14, 2026-10-15, 2026-10-16 | P#595506；O#1407630<br>[R：3625](<D:/stock/TWNextDayTop10/data/exclusion_audit_20261024/reader_findings.json>) |
| TPEX/3632 研勤 | 2026-10-19 | 2026-10-06：7.1<br>P#120365 | 12.3 − (-0.9)＝13.2<br>相對差 85.915493%；門檻 0.1% | 7 日：2026-10-07, 2026-10-08, 2026-10-12, 2026-10-13, 2026-10-14, 2026-10-15, 2026-10-16 | P#120366；O#1406600<br>[R：3632](<D:/stock/TWNextDayTop10/data/exclusion_audit_20261024/reader_findings.json>) |
| TPEX/4527 方土霖 | 2026-10-07 | 2026-09-29：40.9<br>P#530403 | 45 − (0.67)＝44.33<br>相對差 8.3863081%；門檻 0.1% | 5 日：2026-09-30, 2026-10-01, 2026-10-02, 2026-10-05, 2026-10-06 | P#530404；O#1392884<br>[R：4527](<D:/stock/TWNextDayTop10/data/exclusion_audit_20261024/reader_findings.json>) |
| TPEX/5301 寶得利 | 2026-10-07 | 2026-09-29：4.99<br>P#182191 | 6 − (-0.1)＝6.1<br>相對差 22.244489%；門檻 0.1% | 5 日：2026-09-30, 2026-10-01, 2026-10-02, 2026-10-05, 2026-10-06 | P#182192；O#1392894<br>[R：5301](<D:/stock/TWNextDayTop10/data/exclusion_audit_20261024/reader_findings.json>) |
| TPEX/6173 信昌電 | 2026-10-19 | 2026-10-06：284<br>P#472861 | 271.5 − (-29.99)＝301.49<br>相對差 6.1584507%；門檻 0.1% | 7 日：2026-10-07, 2026-10-08, 2026-10-12, 2026-10-13, 2026-10-14, 2026-10-15, 2026-10-16 | P#472862；O#1406319<br>[R：6173](<D:/stock/TWNextDayTop10/data/exclusion_audit_20261024/reader_findings.json>) |
| TWSE/2323 中環 | 2026-10-12 | 2026-09-30：11<br>P#417491 | 10.9 − (0.02)＝10.88<br>相對差 1.0909091%；門檻 0.1% | 6 日：2026-10-01, 2026-10-02, 2026-10-05, 2026-10-06, 2026-10-07, 2026-10-08 | P#417492；O#1396515<br>[R：2323](<D:/stock/TWNextDayTop10/data/exclusion_audit_20261024/reader_findings.json>) |
| TWSE/2601 益航 | 2026-10-05 | 2026-09-22：5.91<br>P#763210 | 6.45 − (-0.61)＝7.06<br>相對差 19.458545%；門檻 0.1% | 6 日：2026-09-23, 2026-09-24, 2026-09-29, 2026-09-30, 2026-10-01, 2026-10-02 | P#763211；O#1388676<br>[R：2601](<D:/stock/TWNextDayTop10/data/exclusion_audit_20261024/reader_findings.json>) |
| TPEX/3085 新零售 | 2026-10-13 | 2026-09-30：12.1<br>P#5277 | 16.2 − (0.61)＝15.59<br>相對差 28.842975%；門檻 0.1% | 6 日：2026-10-01, 2026-10-02, 2026-10-05, 2026-10-06, 2026-10-07, 2026-10-08 | P#5279；O#1398693<br>[R：3085](<D:/stock/TWNextDayTop10/data/exclusion_audit_20261024/reader_findings.json>) |

| 股票 | 官方停止／恢復 | 官方舊股收盤→新股參考 | 缺日結論 | 官方來源／逐案證據 |
| --- | --- | --- | --- | --- |
| TPEX/2067 嘉鋼 | 2026-10-07 起停買；2026-10-19 復買 | 4.8 → 6.4；彌補虧損 | 7 個原缺日全在官方停買區間 | [tpex_reduction_reference](<https://www.tpex.org.tw/www/zh-tw/bulletin/revivt?startDate=2026/09/23&endDate=2026/10/22>)<br>[G：2067](<D:/stock/TWNextDayTop10/data/exclusion_audit_20261024/gap_lifecycle_findings.json>) |
| TPEX/3085 新零售 | 2026-10-01 起停買；2026-10-12 復買 | 12.1 → 15.59；彌補虧損 | 6 個原缺日全在官方停買區間 | [tpex_reduction_reference](<https://www.tpex.org.tw/www/zh-tw/bulletin/revivt?startDate=2026/09/23&endDate=2026/10/22>)<br>[G：3085](<D:/stock/TWNextDayTop10/data/exclusion_audit_20261024/gap_lifecycle_findings.json>) |
| TPEX/3625 西勝 | 2026-10-08 起停買；2026-10-19 復買 | 10.1 → 19.86；彌補虧損 | 6 個原缺日全在官方停買區間 | [tpex_reduction_reference](<https://www.tpex.org.tw/www/zh-tw/bulletin/revivt?startDate=2026/09/23&endDate=2026/10/22>)<br>[G：3625](<D:/stock/TWNextDayTop10/data/exclusion_audit_20261024/gap_lifecycle_findings.json>) |
| TPEX/3632 研勤 | 2026-10-07 起停買；2026-10-19 復買 | 7.1 → 13.2；彌補虧損 | 7 個原缺日全在官方停買區間 | [tpex_reduction_reference](<https://www.tpex.org.tw/www/zh-tw/bulletin/revivt?startDate=2026/09/23&endDate=2026/10/22>)<br>[G：3632](<D:/stock/TWNextDayTop10/data/exclusion_audit_20261024/gap_lifecycle_findings.json>) |
| TPEX/4527 方土霖 | 2026-09-30 起停買；2026-10-07 復買 | 40.9 → 44.33；現金減資 | 5 個原缺日全在官方停買區間 | [tpex_reduction_reference](<https://www.tpex.org.tw/www/zh-tw/bulletin/revivt?startDate=2026/09/23&endDate=2026/10/22>)<br>[G：4527](<D:/stock/TWNextDayTop10/data/exclusion_audit_20261024/gap_lifecycle_findings.json>) |
| TPEX/5301 寶得利 | 2026-09-30 起停買；2026-10-07 復買 | 4.99 → 6.1；彌補虧損 | 5 個原缺日全在官方停買區間 | [tpex_reduction_reference](<https://www.tpex.org.tw/www/zh-tw/bulletin/revivt?startDate=2026/09/23&endDate=2026/10/22>)<br>[G：5301](<D:/stock/TWNextDayTop10/data/exclusion_audit_20261024/gap_lifecycle_findings.json>) |
| TPEX/6173 信昌電 | 2026-10-07 起停買；2026-10-19 復買 | 284 → 301.49；現金減資 | 7 個原缺日全在官方停買區間 | [tpex_reduction_reference](<https://www.tpex.org.tw/www/zh-tw/bulletin/revivt?startDate=2026/09/23&endDate=2026/10/22>)<br>[G：6173](<D:/stock/TWNextDayTop10/data/exclusion_audit_20261024/gap_lifecycle_findings.json>) |
| TWSE/2323 中環 | 2026-10-01 起停買；2026-10-12 復買 | 11 → 10.88；退還股款 | 6 個原缺日全在官方停買區間 | [twse_reduction_reference](<https://www.twse.com.tw/exchangeReport/TWTAUU?response=json&startDate=20260923&endDate=20261022>)<br>[twse_2323_detail](<https://www.twse.com.tw/rwd/zh/reducation/TWTAVUDetail?STK_NO=2323&FILE_DATE=20260930&response=json>)<br>[G：2323](<D:/stock/TWNextDayTop10/data/exclusion_audit_20261024/gap_lifecycle_findings.json>) |
| TWSE/2601 益航 | 2026-09-23 起停買；2026-10-05 復買 | 5.91 → 7.06；彌補虧損 | 6 個原缺日全在官方停買區間 | [twse_reduction_reference](<https://www.twse.com.tw/exchangeReport/TWTAUU?response=json&startDate=20260923&endDate=20261022>)<br>[twse_2601_detail](<https://www.twse.com.tw/rwd/zh/reducation/TWTAVUDetail?STK_NO=2601&FILE_DATE=20260922&response=json>)<br>[G：2601](<D:/stock/TWNextDayTop10/data/exclusion_audit_20261024/gap_lifecycle_findings.json>) |

3085 官方 2026-10-12 復買參考價為 15.59；pin 當日 official no_quote、volume=1855，P close=15.59。10/13 的 16.2−0.61=15.59，原檢查仍拿 9/30 quote=12.1 當 anchor。參考價相符能解釋比較失效，不能證 provider 補值流程或完整 adjusted 歷史已正確。

2323 官方減資並除息：每舊股退還 1 元、現金股利 0.2 元、每仟股換 900 股；恢復參考為 10.88、開盤競價基準為 10.90。close−spread 應核對 10.88，不能混用競價基準。G 保存各股原欄位、換股率、退款、股利及 raw source ordinal/hash。

外部官方取證 receipt 如下；SHA 是 UTF-8 無 BOM **已解碼本文**，不是 HTTP wire bytes。它們是本次稽核註記，不屬原 pin，不增加 runtime network 依賴。


| 來源 | 網址 | HTTP／取證時間 UTC | response_text_sha256 |
| --- | --- | --- | --- |
| tpex_reduction_reference | [官方 API](<https://www.tpex.org.tw/www/zh-tw/bulletin/revivt?startDate=2026/09/23&endDate=2026/10/22>) | 200；2026-10-23T17:21:29.7111501Z | `a8dd6e7083a14604f19b423c1ebe91c377772e7910172c24e89e35f06a136dfe` |
| twse_reduction_reference | [官方 API](<https://www.twse.com.tw/exchangeReport/TWTAUU?response=json&startDate=20260923&endDate=20261022>) | 200；2026-10-23T17:21:30.0452998Z | `af532fefad2941257382411fbd9374e945677ed28d254920418a131444aced7f` |
| twse_2601_detail | [官方 API](<https://www.twse.com.tw/rwd/zh/reducation/TWTAVUDetail?STK_NO=2601&FILE_DATE=20260922&response=json>) | 200；2026-10-23T17:21:30.0829861Z | `ffac2f5ccc09be16704286b21f44c74bb4ebcef497245d5898d047e905a971f0` |
| twse_2323_detail | [官方 API](<https://www.twse.com.tw/rwd/zh/reducation/TWTAVUDetail?STK_NO=2323&FILE_DATE=20260930&response=json>) | 200；2026-10-23T17:21:30.1225388Z | `2c7708acc0bfd885ce52a005f341044c162a228afe76e941e4c923da16197c81` |

上游待補：將已查證停買／復買與公司行動適用區間變成可機器讀取證據；另追跨事件舊／新 adjusted 序列、原始因子及 provider/adapter/publication vintage 轉換。跳過失效比較不代表跨減資還原已驗證，不能猜補缺日或直接改 canonical。


## C．22 股、25 次零量無報價非延續：published 序列不一致

下列每個事件的 O 列都有明確 `close=null`、`volume=0`、`observation_status=no_quote`；P 的 `Trading_Volume`、`Trading_money`、`Trading_turnover` 也都是 0，卻未延續相鄰前交易日 adjusted close。carry 容差為 `1e-9 * max(1, abs(previous_close))`；25 事件都相鄰市場 session，B 的 gap 修正不會使它們通過。這不是股／張換算可以解釋的差異。

原 reason 為 `placeholder_noncarry`；8102 的完整原 reason 為 `basis_break,placeholder_noncarry`。consumer 對此類維持原品質判定；**資料尚未修復**，沒有 provider/adapter/merge 層歸因證據。


| 股票 | 事件日、前 close→當日 close、carry 容差 | P/O ordinal0／逐案證據 | 狀態 |
| --- | --- | --- | --- |
| TPEX/2235 謚源 | 2026-09-18：28.3→28.4；容差 2.83e-08；同 collected stamp 是 | 2026-09-18 P#118465／O#1371131<br>[R：2235](<D:/stock/TWNextDayTop10/data/exclusion_audit_20261024/reader_findings.json>) | 維持品質排除；上游 lineage 待查 |
| TPEX/2926 誠品生活 | 2026-09-21：30→30.1；容差 3e-08；同 collected stamp 是 | 2026-09-21 P#167874／O#1374184<br>[R：2926](<D:/stock/TWNextDayTop10/data/exclusion_audit_20261024/reader_findings.json>) | 維持品質排除；上游 lineage 待查 |
| TPEX/3067 全域 | 2026-09-17：17→16.55；容差 1.7e-08；同 collected stamp 是 | 2026-09-17 P#169020／O#1369500<br>[R：3067](<D:/stock/TWNextDayTop10/data/exclusion_audit_20261024/reader_findings.json>) | 維持品質排除；上游 lineage 待查 |
| TPEX/3629 地心引力 | 2026-09-17：18.2→17.3；容差 1.82e-08；同 collected stamp 是 | 2026-09-17 P#84072／O#1370079<br>[R：3629](<D:/stock/TWNextDayTop10/data/exclusion_audit_20261024/reader_findings.json>) | 維持品質排除；上游 lineage 待查 |
| TPEX/4305 世坤 | 2026-09-22：42.15→41.95；容差 4.215e-08；同 collected stamp 是 | 2026-09-22 P#133637／O#1375950<br>[R：4305](<D:/stock/TWNextDayTop10/data/exclusion_audit_20261024/reader_findings.json>) | 維持品質排除；上游 lineage 待查 |
| TPEX/4413 飛寶企業 | 2026-09-18：19.3→19.85；容差 1.93e-08；同 collected stamp 是 | 2026-09-18 P#136627／O#1371945<br>[R：4413](<D:/stock/TWNextDayTop10/data/exclusion_audit_20261024/reader_findings.json>) | 維持品質排除；上游 lineage 待查 |
| TPEX/5276 達輝-KY | 2026-09-17：17.9→18.5；容差 1.79e-08；同 collected stamp 是; 2026-09-21：18.5→18.45；容差 1.85e-08；同 collected stamp 是; 2026-09-23：18.5→18.45；容差 1.85e-08；同 collected stamp 是 | 2026-09-17 P#105590／O#1369658; 2026-09-21 P#105592／O#1373046; 2026-09-23 P#105594／O#1378148<br>[R：5276](<D:/stock/TWNextDayTop10/data/exclusion_audit_20261024/reader_findings.json>) | 維持品質排除；上游 lineage 待查 |
| TPEX/5520 力泰 | 2026-09-17：82→82.1；容差 8.2e-08；同 collected stamp 是 | 2026-09-17 P#203244／O#1370302<br>[R：5520](<D:/stock/TWNextDayTop10/data/exclusion_audit_20261024/reader_findings.json>) | 維持品質排除；上游 lineage 待查 |
| TPEX/5601 台聯櫃 | 2026-09-17：30.65→30；容差 3.065e-08；同 collected stamp 是 | 2026-09-17 P#75481／O#1370024<br>[R：5601](<D:/stock/TWNextDayTop10/data/exclusion_audit_20261024/reader_findings.json>) | 維持品質排除；上游 lineage 待查 |
| TPEX/5878 台名 | 2026-09-17：38.05→38.1；容差 3.805e-08；同 collected stamp 是 | 2026-09-17 P#73798／O#1369334<br>[R：5878](<D:/stock/TWNextDayTop10/data/exclusion_audit_20261024/reader_findings.json>) | 維持品質排除；上游 lineage 待查 |
| TPEX/6198 瑞築 | 2026-09-17：19.5→19.4；容差 1.95e-08；同 collected stamp 是 | 2026-09-17 P#223227／O#1370535<br>[R：6198](<D:/stock/TWNextDayTop10/data/exclusion_audit_20261024/reader_findings.json>) | 維持品質排除；上游 lineage 待查 |
| TPEX/6212 理銘 | 2026-09-16：33.15→32.6；容差 3.315e-08；同 collected stamp 是 | 2026-09-16 P#121545／O#1367721<br>[R：6212](<D:/stock/TWNextDayTop10/data/exclusion_audit_20261024/reader_findings.json>) | 維持品質排除；上游 lineage 待查 |
| TPEX/6228 全譜 | 2026-09-17：17.85→16.4；容差 1.785e-08；同 collected stamp 是 | 2026-09-17 P#660535／O#1369475<br>[R：6228](<D:/stock/TWNextDayTop10/data/exclusion_audit_20261024/reader_findings.json>) | 維持品質排除；上游 lineage 待查 |
| TPEX/6236 中湛 | 2026-09-16：19.05→17.5；容差 1.905e-08；同 collected stamp 是 | 2026-09-16 P#113874／O#1368806<br>[R：6236](<D:/stock/TWNextDayTop10/data/exclusion_audit_20261024/reader_findings.json>) | 維持品質排除；上游 lineage 待查 |
| TPEX/6730 常廣 | 2026-09-16：31.9→31.8；容差 3.19e-08；同 collected stamp 是 | 2026-09-16 P#61047／O#1368767<br>[R：6730](<D:/stock/TWNextDayTop10/data/exclusion_audit_20261024/reader_findings.json>) | 維持品質排除；上游 lineage 待查 |
| TPEX/6762 達亞 | 2026-09-21：162→159；容差 1.62e-07；同 collected stamp 是 | 2026-09-21 P#81310／O#1374702<br>[R：6762](<D:/stock/TWNextDayTop10/data/exclusion_audit_20261024/reader_findings.json>) | 維持品質排除；上游 lineage 待查 |
| TPEX/7713 威力德生醫 | 2026-09-16：73.5→73.8；容差 7.35e-08；同 collected stamp 是 | 2026-09-16 P#157115／O#1368398<br>[R：7713](<D:/stock/TWNextDayTop10/data/exclusion_audit_20261024/reader_findings.json>) | 維持品質排除；上游 lineage 待查 |
| TPEX/8047 星雲 | 2026-09-16：46.7→45.1；容差 4.67e-08；同 collected stamp 是 | 2026-09-16 P#104420／O#1368774<br>[R：8047](<D:/stock/TWNextDayTop10/data/exclusion_audit_20261024/reader_findings.json>) | 維持品質排除；上游 lineage 待查 |
| TPEX/8102 傑霖科技 | 2026-09-29：59.5→59；容差 5.95e-08；同 collected stamp 是 | 2026-09-29 P#136245／O#1382538<br>[R：8102](<D:/stock/TWNextDayTop10/data/exclusion_audit_20261024/reader_findings.json>) | 維持品質排除；上游 lineage 待查 |
| TPEX/8917 欣泰 | 2026-09-17：52.6→54；容差 5.26e-08；同 collected stamp 是 | 2026-09-17 P#101795／O#1368842<br>[R：8917](<D:/stock/TWNextDayTop10/data/exclusion_audit_20261024/reader_findings.json>) | 維持品質排除；上游 lineage 待查 |
| TPEX/8923 時報 | 2026-09-23：18.65→18；容差 1.865e-08；同 collected stamp 否 | 2026-09-23 P#77407／O#1378021<br>[R：8923](<D:/stock/TWNextDayTop10/data/exclusion_audit_20261024/reader_findings.json>) | 維持品質排除；上游 lineage 待查 |
| TWSE/7732 金興精密 | 2026-09-16：33.8→34.45；容差 3.38e-08；同 collected stamp 是; 2026-09-23：34.3→33.5；容差 3.43e-08；同 collected stamp 是 | 2026-09-16 P#170965／O#1368771; 2026-09-23 P#170970／O#1377759<br>[R：7732](<D:/stock/TWNextDayTop10/data/exclusion_audit_20261024/reader_findings.json>) | 維持品質排除；上游 lineage 待查 |

同 collected stamp 不等於同一 provider 回應；部分 close 恰等於次日價僅是觀測，尚未證前視或搬值。上游需追同一事件／vintage 的原回應、adapter、staged rows、合併前後輸出，才能決定修正層。不得直接把表列 close 改成前 close、刪列或補零。

## D．除息邊界兩事件：8102 已算於 C，8936 為獨立一股

官方 2026-10-22 的 `Change` 為「除息」。P 當日 close−spread 精確等於 O 前日 `NextReferencePrice`，但 P 前日 adjusted close 尚在另一基礎；前後 collected stamp 不同。已證 published 序列跨事件基礎不一致，不能一律以「除息正常」豁免；provider 原始因子沒有留在此 pin，上游歸因仍 unknown。


| 股票 | 原 reason | 觸發數值 | 官方參考與門檻 | P/O ordinal0／證據 | 狀態 |
| --- | --- | --- | --- | --- | --- |
| TPEX/8102 傑霖科技 | basis_break,placeholder_noncarry | 2026-10-22：前 P 56.9；當日 55.5 − (-0.5)＝56 | 2026-10-21 O.NextReferencePrice=56.00<br>相對差 1.5817223%；門檻 0.1% | 前 P#136260／O#1410585；當日 P#136261／O#1413082<br>[R：8102](<D:/stock/TWNextDayTop10/data/exclusion_audit_20261024/reader_findings.json>) | 維持品質排除；來源事件/vintage 待查 |
| TPEX/8936 國統 | basis_break | 2026-10-22：前 P 51.2；當日 47.85 − (-0.35)＝48.2 | 2026-10-21 O.NextReferencePrice=48.20<br>相對差 5.859375%；門檻 0.1% | 前 P#406405／O#1411890；當日 P#406406／O#1413593<br>[R：8936](<D:/stock/TWNextDayTop10/data/exclusion_audit_20261024/reader_findings.json>) | 維持品質排除；來源事件/vintage 待查 |

## E．2 股 IPO 首兩筆基礎差：不能因 IPO 身分自動豁免

原 reason 均為 `listing_day_raw_row`。兩股首筆 open 為正且與同日 official 一致；原因是第二筆與第一筆的 adjusted 基礎差，quality 再把窗口第一筆標成 listing_day_raw_row。first/second 指傳入窗口的第 1/2 列，本身不能證明 IPO。

6907 的官方公告支持 2026-01-29 初上櫃；7717 採官方週報預告及同日實際 official 成交列交叉佐證，尚未另取得 security-specific 原公告。不得用 FinMind「興櫃首日 open=0」說明豁免這兩筆正 open 的上櫃資料。


| 股票 | 首筆 | 第二筆觸發數值（門檻 0.1%） | P/O ordinal0／證據 | 狀態 |
| --- | --- | --- | --- | --- |
| TPEX/6907 雅特力-KY | 2026-01-29：open=49.05、close=49；P#772657 | 2026-01-30：42.203697 − (-6.454683)＝48.65838<br>絕對差 0.34162；相對差 0.69718367% | P#772658；O#1074705<br>[L：6907](<D:/stock/TWNextDayTop10/data/exclusion_audit_20261024/lifecycle_findings.json>) | 維持品質排除；來源基礎/vintage 待查 |
| TPEX/7717 萊德光電-KY | 2025-11-17：open=160、close=142；P#772231 | 2025-11-18：168.876908 − (27.81502)＝141.061888<br>絕對差 0.938112；相對差 0.66064225% | P#772232；O#977149<br>[L：7717](<D:/stock/TWNextDayTop10/data/exclusion_audit_20261024/lifecycle_findings.json>) | 維持品質排除；來源基礎/vintage 待查 |

兩股首日的衍生 adjusted/official close 比都為 1，第二日分別約 0.99302816、0.99339358；這只證基礎 identity 不一致，不能證 provider 因子或是哪一層混歷史。上游應以統一的上市初段／corporate-action vintage 處理查證，不在 consumer 猜改第一筆價格，也不 hardcode 6907/7717。

## F．4 股 unavailable：合法停牌，不能稱為行情漏抓

原 reason 均為 `stock_price_older_than_asof`。P/O 最後有效日期、保存 panel、原生 last_price 和保存 forecast 一致，兩來源均無 2026-10-22 列；沒有 reader 丟失現存當日價格或 last_price 算錯證據。原直接日期拒絕發生在 feature/model 計算前。2026-10-24 補查的官方正證已確認四股分析日皆合法停止買賣，raw 最後價與停牌前最後交易日吻合，不能把停止期間沒有報價說成漏抓。

consumer 狀態：**已修、必要驗證通過**。已改以真正最後可用 feature 日的原生 feature row 推論，保留既有 imputer/inf/完整性處理；不能拿分析日全 NaN 向量冒充當日資料。stale 列可顯示並可發布，但保留各自歷史 target，不寫成當期 prospective observation。復買日期不是提前造出新價格或改寫 target 的授權。

| 股票 | 最後有效資料 | 原始覆蓋 | 最後 feature 日缺值 | P/O 最後列 ordinal0／證據 | 狀態 |
| --- | --- | --- | --- | --- | --- |
| TWSE/1516 川飛 | 2026-10-14；close 18.55；落後 6 交易日 | P 364 筆／窗口 247；O 有效 750 日 | return_252 | P#402706；O#1401057<br>[F：1516](<D:/stock/TWNextDayTop10/data/exclusion_audit_20261024/forecast_findings.json>) | 合法停牌；consumer 已修、必要驗證通過 |
| TWSE/1589 永冠-KY | 2026-04-02；close 5.54；落後 137 交易日 | P 233 筆／窗口 116；O 有效 620 日 | return_126, return_252 | P#680763；O#1145331<br>[F：1589](<D:/stock/TWNextDayTop10/data/exclusion_audit_20261024/forecast_findings.json>) | 合法停牌；consumer 已修、必要驗證通過 |
| TPEX/4804 大略-KY | 2026-04-13；close 3.27；落後 132 交易日 | P 238 筆／窗口 121；O 有效 505 日 | return_126, return_252 | P#505286；O#1154404<br>[F：4804](<D:/stock/TWNextDayTop10/data/exclusion_audit_20261024/forecast_findings.json>) | 合法停牌；consumer 已修、必要驗證通過 |
| TWSE/6142 友勁 | 2026-10-14；close 8.71；落後 6 交易日 | P 364 筆／窗口 247；O 有效 751 日 | return_252 | P#309521；O#1402254<br>[F：6142](<D:/stock/TWNextDayTop10/data/exclusion_audit_20261024/forecast_findings.json>) | 合法停牌；consumer 已修、必要驗證通過 |

| 股票 | 官方適用期間與結論 | 官方正證 | 取證方式與限制 |
| --- | --- | --- | --- |
| TWSE/1516 川飛 | 2026-10-15～2026-10-23 舊股停止買賣；2026-10-27 新股恢復。 | [2026-09-30 臺證上一字第1150019619號](<https://www.twse.com.tw/rwd/zh/announcement/announcement_detail?id=28B05C06BCA711F19A80005056BE3760&response=json>) | official announcement JSON fetched directly; stat=ok |
| TWSE/1589 永冠-KY | 2026-04-07 起停止，2026-10-12 公告仍維持停止；2026-11-18 是未來預定終止上市日，分析日尚未下市。 | [2026-10-08 臺證上二字第1151702936號](<https://accessibility.twse.com.tw/zh/announcement/announcement/detail.html?id=C4DFB886C2F211F19A80005056BE3760>)<br>[2026-10-12 臺證上二字第1151703003號](<https://accessibility.twse.com.tw/zh/announcement/announcement/detail.html?id=2D69A2C2C62211F19A80005056BE3760>) | official indexed announcement text; JavaScript detail HTML itself has no body in web reader |
| TPEX/4804 大略-KY | 2026-04-14 起停止；2026-09-03 因其他事由繼續停止；10/22 官方表 ! 標記表示停止買賣。 | [2026-04-10 證櫃監字第11502007761號](<https://www.tpex.org.tw/storage/eb_data/11504/11502007761.html>)<br>[2026-09-03 證櫃監字第11502024351號](<https://www.tpex.org.tw/storage/eb_data/11509/11502024351.html>)<br>[10/22官方狀態表](<https://www.tpex.org.tw/web/stock/margin_trading/margin_sbl/margin_sbl_result.php?l=zh-tw&o=htm>) | 4/10 official HTML; 9/3 official indexed text (direct web reader 403); 10/22 official status table and legend |
| TWSE/6142 友勁 | 2026-10-15～2026-10-23 舊股停止買賣；2026-10-27 新股恢復。 | [2026-09-30 臺證上一字第1150019573號](<https://www.twse.com.tw/rwd/zh/announcement/announcement_detail?id=5E8AEB36BC8211F19A80005056BE3760&response=json>) | official announcement JSON fetched directly; stat=ok |

1516/6142 的官方 announcement JSON 直接讀取 stat=ok；1589 依官方索引文本，動態 HTML 本身無正文；4804 的 9/3 頁直接讀取 403，官方索引與 10/22 狀態表交叉佐證。這些是本次外部官方補證，詳見 F 的 `official_lifecycle_evidence`／`official_lifecycle_checks`，沒有回寫原 immutable pin；以上公告未由本文件作者另行抓取。

**producer 待補的是可機器讀取的停牌／公司行動 lifecycle 證據，不是停止期間行情。** 四股 official 均有 2023-09-08 起有效價格，不能稱為本窗口新上市；P 在本 pin 都自 2025-04-21 開始。1589/4804 全部 P 歷史只有 233/238 日，這個較早歷史覆蓋限制是另一問題，原因尚未確認，不能推定上市日或混稱停牌漏抓。保留 unknown/PARTIAL、原 null/token 與 vintage，不補造價格、補零或新增 freshness 發布門檻。


## 41 股原案完整對帳

A/B/C/D/E/F 是不重複主分類；D 的 8102 附加事件仍歸 C。下表原 reason 與 preopen/view presence 來自 T，代表原保存成品，不代表 consumer 修正後已重新發布。


| 股票 | 原案類型 | 原 reason | 分類 | 原 snapshot/view | 程式／來源狀態 | 逐股證據 |
| --- | --- | --- | --- | --- | --- | --- |
| TPEX/2067 嘉鋼 | excluded | basis_break | B | 有/有 | C0 已恢復；合法停買／跨事件基底待證 | [R：2067](<D:/stock/TWNextDayTop10/data/exclusion_audit_20261024/reader_findings.json>) |
| TPEX/2235 謚源 | excluded | placeholder_noncarry | C | 有/有 | C0 品質排除保留；來源待 producer | [R：2235](<D:/stock/TWNextDayTop10/data/exclusion_audit_20261024/reader_findings.json>) |
| TPEX/2926 誠品生活 | excluded | placeholder_noncarry | C | 有/有 | C0 品質排除保留；來源待 producer | [R：2926](<D:/stock/TWNextDayTop10/data/exclusion_audit_20261024/reader_findings.json>) |
| TPEX/3067 全域 | excluded | placeholder_noncarry | C | 有/有 | C0 品質排除保留；來源待 producer | [R：3067](<D:/stock/TWNextDayTop10/data/exclusion_audit_20261024/reader_findings.json>) |
| TPEX/3085 新零售 | excluded | basis_break | B | 無/無 | C0 已恢復；合法停買／跨事件基底待證 | [R：3085](<D:/stock/TWNextDayTop10/data/exclusion_audit_20261024/reader_findings.json>) |
| TPEX/3625 西勝 | excluded | basis_break | B | 有/有 | C0 已恢復；合法停買／跨事件基底待證 | [R：3625](<D:/stock/TWNextDayTop10/data/exclusion_audit_20261024/reader_findings.json>) |
| TPEX/3629 地心引力 | excluded | placeholder_noncarry | C | 有/有 | C0 品質排除保留；來源待 producer | [R：3629](<D:/stock/TWNextDayTop10/data/exclusion_audit_20261024/reader_findings.json>) |
| TPEX/3632 研勤 | excluded | basis_break | B | 有/有 | C0 已恢復；合法停買／跨事件基底待證 | [R：3632](<D:/stock/TWNextDayTop10/data/exclusion_audit_20261024/reader_findings.json>) |
| TPEX/4305 世坤 | excluded | placeholder_noncarry | C | 無/無 | C0 品質排除保留；來源待 producer | [R：4305](<D:/stock/TWNextDayTop10/data/exclusion_audit_20261024/reader_findings.json>) |
| TPEX/4413 飛寶企業 | excluded | placeholder_noncarry | C | 有/有 | C0 品質排除保留；來源待 producer | [R：4413](<D:/stock/TWNextDayTop10/data/exclusion_audit_20261024/reader_findings.json>) |
| TPEX/4527 方土霖 | excluded | basis_break | B | 有/有 | C0 已恢復；合法停買／跨事件基底待證 | [R：4527](<D:/stock/TWNextDayTop10/data/exclusion_audit_20261024/reader_findings.json>) |
| TPEX/5276 達輝-KY | excluded | placeholder_noncarry | C | 有/有 | C0 品質排除保留；來源待 producer | [R：5276](<D:/stock/TWNextDayTop10/data/exclusion_audit_20261024/reader_findings.json>) |
| TPEX/5301 寶得利 | excluded | basis_break | B | 有/有 | C0 已恢復；合法停買／跨事件基底待證 | [R：5301](<D:/stock/TWNextDayTop10/data/exclusion_audit_20261024/reader_findings.json>) |
| TPEX/5520 力泰 | excluded | placeholder_noncarry | C | 無/無 | C0 品質排除保留；來源待 producer | [R：5520](<D:/stock/TWNextDayTop10/data/exclusion_audit_20261024/reader_findings.json>) |
| TPEX/5601 台聯櫃 | excluded | placeholder_noncarry | C | 有/有 | C0 品質排除保留；來源待 producer | [R：5601](<D:/stock/TWNextDayTop10/data/exclusion_audit_20261024/reader_findings.json>) |
| TPEX/5878 台名 | excluded | placeholder_noncarry | C | 有/有 | C0 品質排除保留；來源待 producer | [R：5878](<D:/stock/TWNextDayTop10/data/exclusion_audit_20261024/reader_findings.json>) |
| TPEX/6173 信昌電 | excluded | basis_break | B | 有/有 | C0 已恢復；合法停買／跨事件基底待證 | [R：6173](<D:/stock/TWNextDayTop10/data/exclusion_audit_20261024/reader_findings.json>) |
| TPEX/6198 瑞築 | excluded | placeholder_noncarry | C | 有/有 | C0 品質排除保留；來源待 producer | [R：6198](<D:/stock/TWNextDayTop10/data/exclusion_audit_20261024/reader_findings.json>) |
| TPEX/6212 理銘 | excluded | placeholder_noncarry | C | 無/無 | C0 品質排除保留；來源待 producer | [R：6212](<D:/stock/TWNextDayTop10/data/exclusion_audit_20261024/reader_findings.json>) |
| TPEX/6228 全譜 | excluded | placeholder_noncarry | C | 有/有 | C0 品質排除保留；來源待 producer | [R：6228](<D:/stock/TWNextDayTop10/data/exclusion_audit_20261024/reader_findings.json>) |
| TPEX/6236 中湛 | excluded | placeholder_noncarry | C | 無/無 | C0 品質排除保留；來源待 producer | [R：6236](<D:/stock/TWNextDayTop10/data/exclusion_audit_20261024/reader_findings.json>) |
| TPEX/6423 億而得 | excluded | price_window_official_market_mismatch | A | 有/有 | C0 已恢復；current 段驗證通過 | [L：6423](<D:/stock/TWNextDayTop10/data/exclusion_audit_20261024/lifecycle_findings.json>) |
| TPEX/6730 常廣 | excluded | placeholder_noncarry | C | 有/有 | C0 品質排除保留；來源待 producer | [R：6730](<D:/stock/TWNextDayTop10/data/exclusion_audit_20261024/reader_findings.json>) |
| TPEX/6762 達亞 | excluded | placeholder_noncarry | C | 有/有 | C0 品質排除保留；來源待 producer | [R：6762](<D:/stock/TWNextDayTop10/data/exclusion_audit_20261024/reader_findings.json>) |
| TPEX/6907 雅特力-KY | excluded | listing_day_raw_row | E | 有/有 | C0 品質排除保留；來源待 producer | [L：6907](<D:/stock/TWNextDayTop10/data/exclusion_audit_20261024/lifecycle_findings.json>) |
| TPEX/7713 威力德生醫 | excluded | placeholder_noncarry | C | 有/有 | C0 品質排除保留；來源待 producer | [R：7713](<D:/stock/TWNextDayTop10/data/exclusion_audit_20261024/reader_findings.json>) |
| TPEX/7717 萊德光電-KY | excluded | listing_day_raw_row | E | 有/有 | C0 品質排除保留；來源待 producer | [L：7717](<D:/stock/TWNextDayTop10/data/exclusion_audit_20261024/lifecycle_findings.json>) |
| TPEX/8047 星雲 | excluded | placeholder_noncarry | C | 無/無 | C0 品質排除保留；來源待 producer | [R：8047](<D:/stock/TWNextDayTop10/data/exclusion_audit_20261024/reader_findings.json>) |
| TPEX/8102 傑霖科技 | excluded | basis_break,placeholder_noncarry | C | 有/有 | C0 品質排除保留；來源待 producer | [R：8102](<D:/stock/TWNextDayTop10/data/exclusion_audit_20261024/reader_findings.json>) |
| TPEX/8917 欣泰 | excluded | placeholder_noncarry | C | 有/有 | C0 品質排除保留；來源待 producer | [R：8917](<D:/stock/TWNextDayTop10/data/exclusion_audit_20261024/reader_findings.json>) |
| TPEX/8923 時報 | excluded | placeholder_noncarry | C | 有/有 | C0 品質排除保留；來源待 producer | [R：8923](<D:/stock/TWNextDayTop10/data/exclusion_audit_20261024/reader_findings.json>) |
| TPEX/8936 國統 | excluded | basis_break | D | 有/有 | C0 品質排除保留；來源待 producer | [R：8936](<D:/stock/TWNextDayTop10/data/exclusion_audit_20261024/reader_findings.json>) |
| TWSE/2323 中環 | excluded | basis_break | B | 有/有 | C0 已恢復；合法停買／跨事件基底待證 | [R：2323](<D:/stock/TWNextDayTop10/data/exclusion_audit_20261024/reader_findings.json>) |
| TWSE/2601 益航 | excluded | basis_break | B | 有/有 | C0 已恢復；合法停買／跨事件基底待證 | [R：2601](<D:/stock/TWNextDayTop10/data/exclusion_audit_20261024/reader_findings.json>) |
| TWSE/5236 凌陽創新 | excluded | price_window_official_market_mismatch | A | 有/有 | C0 已恢復；current 段驗證通過 | [L：5236](<D:/stock/TWNextDayTop10/data/exclusion_audit_20261024/lifecycle_findings.json>) |
| TWSE/7732 金興精密 | excluded | placeholder_noncarry | C | 有/有 | C0 品質排除保留；來源待 producer | [R：7732](<D:/stock/TWNextDayTop10/data/exclusion_audit_20261024/reader_findings.json>) |
| TWSE/8908 欣雄 | excluded | price_window_official_market_mismatch | A | 有/有 | C0 已恢復；current 段驗證通過 | [L：8908](<D:/stock/TWNextDayTop10/data/exclusion_audit_20261024/lifecycle_findings.json>) |
| TWSE/1516 川飛 | unavailable | stock_price_older_than_asof | F | 無/無 | C0 已可依舊資料試算；合法停牌／歷史 target | [F：1516](<D:/stock/TWNextDayTop10/data/exclusion_audit_20261024/forecast_findings.json>) |
| TWSE/1589 永冠-KY | unavailable | stock_price_older_than_asof | F | 無/無 | C0 已可依舊資料試算；合法停牌／歷史 target | [F：1589](<D:/stock/TWNextDayTop10/data/exclusion_audit_20261024/forecast_findings.json>) |
| TPEX/4804 大略-KY | unavailable | stock_price_older_than_asof | F | 無/無 | C0 已可依舊資料試算；合法停牌／歷史 target | [F：4804](<D:/stock/TWNextDayTop10/data/exclusion_audit_20261024/forecast_findings.json>) |
| TWSE/6142 友勁 | unavailable | stock_price_older_than_asof | F | 無/無 | C0 已可依舊資料試算；合法停牌／歷史 target | [F：6142](<D:/stock/TWNextDayTop10/data/exclusion_audit_20261024/forecast_findings.json>) |

## 給另一對話的統一修復方向與建議驗收

1. **先固定 lineage，再決定修復層。** 用本文件 pin 與 P/O ordinal 定位 raw response → adapter → staging → 合併前候選 → 合併後 immutable object → published route。每層保留 source hash、request/日期範圍、vintage/collected_at、轉換規則及 event evidence。沒有該層證據就明寫未證，不把同 collected stamp 當同一 provider 回應。
2. **13 股合法停止買賣不補造行情。** B 九股減資停買及 F 四股停牌均已有官方正證；producer 應補機器可讀 lifecycle/公司行動證據與日期適用範圍，不能將合法缺交易日當漏抓。current ordinary identity 不等於歷史狀態；原 null/token 保留，不猜補零。
3. **用統一價格/vintage 管線修來源。** C 的 carry、D 的除息、E 的 IPO 初段，以及 B 尚未證明的跨減資 adjusted 歷史，都由共同 adapter／merge／發布規則處理，不 per-stock 特判。不混接不同調整基礎，不覆寫舊 vintage；若 provider 真有原始 factor 欄，須以原欄名、原回應與 hash 留證，不能把 derived ratio 改名代替。
4. **consumer 與資料修復分開驗收。** A 的 current-market 段、B 的相鄰 session 比較、F 的真實最後 feature 日與 C0-only 顯示屬本對話 consumer 工作；其通過不代表跨公司行動 adjusted 基底或 published 矛盾已修。現有可用資料依使用者要求可分析／發布，freshness/coverage 僅揭露，不新增阻擋。
5. **另行授權後執行 source-only 修正與最小驗證。** 建議 fixtures 涵蓋連續與跨缺日 basis/carry、no_quote anchor 重置、零量非 carry、除息 vintage、IPO 初兩筆、跨減資基底、不同股邊界與合法轉市場；資料改正後針對本表事件核對前後差異。這是下一對話的建議，本交接作者未自行執行測試；runtime 集中結果另見本文件驗收節。
6. **新發布須留完整證據。** 若後續獲准更新，建立新 producer run、來源 receipt、candidate/merge 證據與新 immutable head；保留本 pin/舊物件不變。逐股說明「確認合法停買／補 lifecycle／已修值／基底仍未證／較早歷史仍受限」，附舊新 hash、原因與日期；不得只移除 reason/gap、改 SUCCESS、手改 canonical/head，或以一個 exit 0 代替 readback。
7. **最終檢查預期。** 41 股 disposition 不重複；22 股 25 placeholder 事件與 8102/8936 除息事件都交代；9 股合法減資停買、4 股合法停牌須綁官方證據，較早歷史與跨事件基底未證部分仍明示 PARTIAL/unknown。consumer 的分析日、真資料日、target 與提醒一致；舊 target 不冒充明日，stale 不寫當期 prospective ledger。

## 必要唯讀復現方式（僅供接手者使用）

先讀 X/R/L/F/G/T，不重新呼叫 full-pool reader，也不重掃大型 source。PowerShell 解析 X 或原始 official dict 時用 `-AsHashtable`，因為資料可同時有 `date/Date`、`open/Open` 等大小寫不同 keys。

下列只列出已抽取的指定原始 ordinal，並保留 raw_json；本文件作者沒有以它執行修復：

```powershell
$audit = 'D:\stock\TWNextDayTop10\data\exclusion_audit_20261024'
Get-Content -LiteralPath "$audit\extract\finmind_price_adj.jsonl" -Encoding UTF8 |
    ForEach-Object {
        $row = $_ | ConvertFrom-Json -AsHashtable
        if ($row.source_row_ordinal0 -in @(136245,136261,406406)) {
            $row | ConvertTo-Json -Depth 12
        }
    }
```

其餘股票使用表中 P/O ordinal0 及相應抽取檔，確認 `source_sha256` 和來源索引一致；ordinal 是來源物件位置，不能拿來當 JSONL 行號。對缺列則看 R 的逐日 presence 與 X 的 rows_by_route，不從不存在的列推定合法狀態。

## Consumer 修正與本交接驗收結果

本節是 runtime worker 的集中結果回填；本交接作者未再執行程式、資料或模型驗收。權威 evidence：
[acceptance.json](<D:/stock/TWNextDayTop10/data/exclusion_audit_20261024/post_fix/acceptance.json>)、[comparison.json](<D:/stock/TWNextDayTop10/data/exclusion_audit_20261024/post_fix/comparison.json>)、[pytest.xml](<D:/stock/TWNextDayTop10/data/exclusion_audit_20261024/validation/run_01/pytest.xml>)。


- **集中 focused：122 tests passed，exit 0，failures/errors/skipped 均 0**，耗時 22.278 秒。
- **單次 native replay：1,981 股池、1,956 eligible/predicted、25 股品質排除。** 原 37 排除精確恢復下列 12 股，沒有新排除；4 股原 unavailable 也已依真實舊 feature 日得到有限試算，合計原 41 案為 **12 恢復＋4 舊資料試算＋25 保留排除**。
- 12 股：2067, 3085, 3625, 3632, 4527, 5301, 6173, 6423, 2323, 2601, 5236, 8908。
- 原共同 panel 的 490,336 個 common keys 與既有欄位值未變；原 1,940 筆 predicted 的機率／報酬／原始機率數值未變。完整逐列新增／欄位差異留於 [all_row_changes.jsonl.gz](<D:/stock/TWNextDayTop10/data/exclusion_audit_20261024/post_fix/all_row_changes.jsonl.gz>)，不是把新增日期／揭露欄位說成零差異。
- 原 preopen snapshot 1,915 股及 Top10 的值與順序未變；新 view 為 **1,976 列，含 61 列 C0-only 補充**。補充列標來源與真日期，不改 preopen Top10 排名。
- 同 head/model/preopen pointer；input/source hashes 不變；沒有新的 projection build 或 spool、沒有 blocked writes。這是本次固定輸入的重播驗證，不是重新下載、重訓或正式發布。

四股真實 feature/target/lag 與 in-sample 已核對：
| 股票 | 真實資料／feature 日 | 本股 target | 落後交易日 | in_sample_lookahead | 樣本提醒 |
| --- | --- | --- | --- | --- | --- |
| TWSE/1516 | 2026-10-14 | 2026-10-15（歷史） | 6 | 否 | 資料落後6交易日，依既有資料分析；開盤前快照未收錄；C0補充分析 |
| TWSE/1589 | 2026-04-02 | 2026-04-07（歷史） | 137 | 是：歷史試算，模型訓練晚於資料日 | 資料落後137交易日，依既有資料分析；歷史資料試算／模型訓練晚於資料日；開盤前快照未收錄；C0補充分析 |
| TPEX/4804 | 2026-04-13 | 2026-04-14（歷史） | 132 | 是：歷史試算，模型訓練晚於資料日 | 資料落後132交易日，依既有資料分析；歷史資料試算／模型訓練晚於資料日；開盤前快照未收錄；C0補充分析 |
| TWSE/6142 | 2026-10-14 | 2026-10-15（歷史） | 6 | 否 | 資料落後6交易日，依既有資料分析；開盤前快照未收錄；C0補充分析 |

上述 target 是原生市場 calendar 的歷史標籤，不代表停牌股票當日有成交、可交易，或已成為本次分析日的明日預測。1589/4804 明確保留歷史試算／模型訓練晚於資料日提醒。

真 Node Excel 產生的 [nonformal_sample.xlsx](<D:/stock/TWNextDayTop10/data/exclusion_audit_20261024/post_fix/nonformal_sample.xlsx>)：**node exit 0，7 項 ZIP/XML checks 全通過**；沒有 Excel Tables，分析日期是真日期值及日期格式，四股來源／資料日／歷史 target／落後提醒與 payload 一致，機率與報酬的數值／百分比格式正確，Top10 值及順序保持。
樣本 SHA-256：`0515d76a2f87b1e33275d89a8570dd74bf2d2256c0f85f941d83c3feb8d55244`；187,077 bytes。

**樣本歷史比較功能明確未執行**（optional provider unavailable），不能宣稱該功能已驗證。未呼叫正式 prediction entrypoint、未寫正式 forward ledger、未發布正式報告、未重訓，也未改上游。25 股 published 值問題仍待 producer；9 股合法減資停買不等於漏抓，跨事件 adjusted 基底仍待證；4 股合法停牌也非漏抓。

文件最低核對：UTF-8 無 BOM、LF、繁中可讀；41 股原案與 A/B/C/D/E/F 不重複對帳、22 股 25 placeholder 事件與 8102 附加除息不重複計股；HTTP(S) 連結保留完整 URL、沒有反斜線；本機連結路徑均存在。本機路徑／連結只查存在，未重新開啟已查官方網頁。

本文件僅寫入授權的這一份 Markdown 與 [handoff_accounting.json](<D:/stock/TWNextDayTop10/data/exclusion_audit_20261024/handoff_accounting.json>)。來源資料仍未修復、未重抓、未重新發布；provider/adapter/merge 歸因未證。實作、測試、模型、中央、Updater、MIX、正式報告、服務與 Git 不在本交接作者的寫入範圍。

