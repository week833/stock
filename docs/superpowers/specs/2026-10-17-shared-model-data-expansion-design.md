# 共用模型與可選資料擴充設計

狀態：設計稿，供使用者審閱；本稿不代表已實作、訓練、測試或正式採用。
範圍：共用模型及個股、MIX、Top10 的候選介面；本輪只新增本文件，不改產品程式或中央資料。
最新決策：18 個月可接受；資料來源目錄不減少，但按 horizon、產業及分析目的選用，不要求每次全讀或全部入模。

## 1. 已確認現況與證據邊界

- 既有唯讀盤點 head：`D:\TWStockData\shared_market_data\_metadata\stock_data_current.json`，schema=`stock-data-publication-v1`，run=`20261017T061349Z-4f3bde84`，發布時間 `2026-10-17T14:14:33+08:00`。
- 該次 head SHA256：`5FA6F5B059DD41AA60B673C4E60BC16912A83BD07626AD4135752643B61904E9`；36 routes 為 29 SUCCESS、7 PARTIAL。此為既有觀察，不是本稿重掃後的最新完整性驗收。
- 官方行情與還原價格實際全體邊界為 `2025-04-14～2026-10-16`；不能由 route 邊界推定每股完整。
- Capture 引用：`_scope/capture/ce860a3209126642d2d75d36527949c1d03ffb17363a10e05820ac3d0f9d1f37.json`，status=`bounded`；retention 引用：`_scope/retention/f45921256235e5c5644fead5319ee0e6b74a3e7780519a34103fc93701816558.json`，scope certainty=`known`。引用皆位於上述共用根目錄。
- 既有 scope 的 18 月範圍為 `2025-04-17～2026-10-17`，兩市場已列日期軸各 368 sessions；C0 推論窗另為 253 sessions，`2025-09-30～2026-10-16`。範圍已知不等於所有來源齊全。
- 本輪直接查閱 `programs/shared/stock_daily_linear.py`、`stock_daily_contract.py`：現行七特徵為 `return_5/20/60/126/252`、`volatility_20/60`；舊訓練 readiness 要求 253 個有效價格且七特徵全有限值；預設 fit 為短期 252、長期 504 日。
- 現行 schema 為 `tw-stock-c0-price-seven-v1`／`tw-stock-daily-linear-c0-v1`；`fit_baseline` 可選固定 fit 上漲率及零報酬，且 evaluation 同時用於選頭，並非 untouched holdout。
- 主代理已核對 active artifact：training_asof=`2026-10-08`、44 股，H1/H63 選上述兩類基準；H63 實際 fit 462 日，`2023-01-11～2024-12-12`。此為主代理提供的 metadata 結果，本分工未重新讀取 artifact。
- 個股現行 README 與 `config/data_sources.json` 確認只讀中央發布來源；讀取層 `src/stock_data_sources.py` 有 FinMind dataset 對照及通用新聞來源清單，未直接確認股癌 podcast 的獨立接入。

## 2. 保留的 36 路可選來源目錄

下表用途是候選資格，不是每次必讀清單：H=歷史特徵候選、C=當前上下文、I=完整性／時間／股票資格證據。S/P 僅代表既有 head 的 SUCCESS/PARTIAL，不授予歷史 PIT 資格。

| 中文項目 | Route | 用途 | 發布 |
|---|---|---|---|
| 官方交易日曆 | `official_calendar` | I | S |
| 官方日行情 | `official_market_daily` | H、I | S |
| 股票基本資料 | `finmind_stock_info` | C、I；非歷史成分股證明 | S |
| 還原股價 | `finmind_price_adj` | H、I | S |
| 交易限制／處置 | `official_trading_restrictions` | C；歷史時點另驗 | S |
| 全球市場歷史 | `global_market_history` | H、C；海外收盤時點另驗 | P |
| 三大法人買賣超 | `finmind_institutional` | H、C | S |
| 融資融券 | `finmind_margin` | H、C | S |
| 財報申報期限 | `official_finance_reporting_deadline` | I、C；不等於實際公告時間 | S |
| 月營收 | `finmind_month_revenue` | H、C；公告時點另驗 | S |
| 損益／財務報表 | `finmind_financial_statements` | H、C；公告時點另驗 | P |
| 資產負債表 | `finmind_balance_sheet` | H、C；公告時點另驗 | P |
| 現金流量表 | `finmind_cash_flows_statement` | H、C；累計期與公告時點另驗 | P |
| 股利資訊 | `finmind_dividend` | H、C、I | P |
| 除權息結果 | `finmind_dividend_result` | H、C、I | P |
| 本益比／股價淨值比／殖利率 | `finmind_per` | H、C | S |
| 外資持股 | `finmind_shareholding` | H、C | S |
| 現股當沖 | `finmind_day_trading` | H、C | S |
| 期貨法人部位 | `finmind_futures_institutional` | H、C；市場因子 | S |
| 選擇權法人部位 | `finmind_options_institutional` | H、C；市場因子 | S |
| 官股銀行買賣 | `finmind_government_bank` | H、C | S |
| 券商分點交易 | `finmind_trading_daily_report` | H、C；依證據與成本選用 | P |
| 鉅額交易 | `finmind_block_trade` | H、C；事件型 | S |
| 借貸／擔保融通 | `finmind_loan_collateral` | H、C | S |
| 融資維持率 | `finmind_margin_maintenance` | H、C | S |
| 主動式 ETF 持股 | `official_active_etf_holding` | H、C；持有普通股的資金脈絡，非 ETF 推薦 | S |
| 主動式 ETF 持股變動 | `official_active_etf_change` | H、C；同上 | S |
| 產業鏈資金流 | `finmind_industry_chain_flow` | C；現況快照不當歷史特徵 | S |
| 官方產業分類／特徵 | `official_sector_features` | C、I；今日分類不回填過去 | S |
| 匯率 | `finmind_exchange_rate` | H、C | S |
| 公債殖利率 | `finmind_government_bonds_yield` | H、C | S |
| 原油價格 | `finmind_crude_oil_prices` | H、C；依產業相關性選用 | S |
| 黃金價格 | `finmind_gold_price` | H、C；依分析目的選用 | S |
| 集保持股分級 | `tdcc_share_distribution_raw` | H、C；週頻時點另驗 | S |
| 新聞／熱門題材原始資料 | `news_and_hot_topics_raw` | C；歷史公告證據足夠才可 H | S |
| 產業／總經觀測指標 | `official_sector_observations` | C；歷史 vintage 證據足夠才可 H | S |

政策原有 32 必要、4 選配之分屬中央 producer：選配為 options、券商分點、財報期限、產業觀測四路，不轉成消費端全來源讀取門檻。ETF 兩路既有抓取窗為 3 個月；部分基本／分類／限制路是最新快照，不宣稱 36 路都有 18 月歷史。

### 來源選擇與狀態

- 每次先依分析目的建立 metadata-only 選擇清單；只投影 `used`、必要 `context-only` 與完整性證據來源，其他記 `not-selected(reason)`，不讀其 payload、不當缺資料。
- 另以資料可用性軸記 `available`、`legitimate_empty`、`not_applicable`、`missing`、`unknown_availability`；「產業不適用」與「本次未選用」不同。`unavailable(reason)` 為選中來源無法供本次用途的彙總，須保留底層原因。
- 逐來源保留 route、欄位、用途、選擇理由、物件 hash、原發布狀態、實際日期／股票覆蓋、可用時間證據及缺口；S 不自動等於歷史 available，P 也不自動等於全部不可用。
- `legitimate_empty` 必須有對應來源／股票／期間的正向證據；空 API、檔案不存在或未選用均不能冒充合法無資料。來源未選用不阻止分析；選中項缺失依封存的缺值規則處理，必要價格／標籤／完整性缺失才阻止相應估計。
- 來源及特徵可依事前領域規則、fit 內時間切分的增益／冗餘證據及 selection 選擇；不得看 final holdout。各 horizon/head 的來源規則、`feature_order`、缺值與適用性規則隨 artifact 封存，訓練／推論一致，不得執行時任意換欄位。

### 既有 7 個 PARTIAL 原因摘要

| Route | 已知原因／限制 |
|---|---|
| `finmind_dividend` | reason=`success`、coverage 1/2；current native scope complete，但來源 P、歷史 recovery unresolved。 |
| `finmind_dividend_result` | 同上；不直接推論當前範圍缺資料。 |
| `finmind_balance_sheet` | `success; response contains no records`，coverage 2/6；未證明全部空回應合法。 |
| `finmind_financial_statements` | 同上，coverage 2/6。 |
| `finmind_cash_flows_statement` | 2025Q4、2026Q1、2026Q2 為 `deadline_company_period_unproved`，coverage 2/5。 |
| `finmind_trading_daily_report` | entitlement 不足、storage blocked/pending、既存來源需證明及事件空回應缺正向無資料證據；coverage 1,995/728,272。 |
| `global_market_history` | `global instrument collection: PARTIAL`，成功 22/26；未擴查個別 instrument。 |

### 舊版項目的窄範圍對照備註

| 項目 | 現有程式直接確認／本稿處置 |
|---|---|
| FinMind 既有資料項目 | `stock_data_sources.py:_DATASETS` 對照涵蓋價量、法人、融資融券、財務、股利、估值、籌碼及總經等現行 route；保留登錄，不代表原模型全部使用。 |
| 股癌 podcast | 使用者指出的舊項目；36 路無獨立同名 route，現行 README／設定／對照表未直接證實通用新聞路已涵蓋。記「未驗證、待實作階段核對；中央是否發布及可用時間未知」，不能宣稱已接入，也不在本輪恢復工程。 |
| 三竹背景參考及其他舊項目 | README 將三竹背景參考列為退役；其他歷來來源集合未完整核對，記「待實作階段核對」。不掃備份／外部專案，不宣稱 36 等於歷來全部，不自動恢復退役功能。 |
| 小朋友學投資 | 使用者明確要求移除，記 `excluded_by_user`；不恢復，不算本設計誤刪來源。 |

## 3. 候選資料與特徵設計

- 每市場以同一封存中央快照中該市場已發布 asof 回看 18 個曆月；來源 hash、calendar、capture/retention、股票資格與時區證據一起封存。不同市場不強拉同一最新日；本次不補抓、不改中央採集政策。
- 候選基礎為普通股的真實市場日價量；原七特徵保留計算及對照。短期主要使用 60 日內訊號，126/252 日特徵照算並允許缺值、記 coverage，不能用 `all7finite` 排掉大部分 18 月樣本，不能捏造長期歷史。
- 可選擴充：成交量／金額與波動、法人淨買賣及短期累積、融資融券變化、當沖占比、外資持股變化、估值、公告後營收／財務／現金流、具證據的產業／海外／總經訊號。比率需核對單位與分母；`Trading_turnover` 不逕稱換手率。
- 財報、營收、新聞、海外及事件以 `available_time <= signal_time` 連接；缺歷史公告／收盤時點證據時不回填到過去，可保留當前上下文或缺值。財報期末日／期限不等於實際公告日；現金流年初累計不冒充單季。
- 今日產業分類沒有歷史資格證據時，只作 current context；不回填今日分類到過去選股或來源適用性判定。`latest_history_seed` 不當歷史 PIT 已驗證。
- 缺值轉換、標準化、增益／冗餘篩選只在 fit 資料學習；selection 僅依預先固定規則選候選。訓練期全缺／常數／無資訊特徵記有效性、原因及實際採用欄位，來源仍留 catalog；不默默刪來源。禁止補價格、補標籤或偽造公告時間。

## 4. 模型與基準

- 候選比較：七特徵 Logistic/Ridge 參考組、擴充特徵 Logistic/Ridge、擴充特徵 sklearn HistGradientBoosting 方向／報酬模型；不預設新增套件。參數、seed 與模型內部驗證規則事前封存，禁止隨機內部分割破壞時間邊界。
- 固定 fit 上漲率與零報酬只列比較基準，不能進入新正式 head 的自動選取或在失敗時頂替模型。真實模型自然預測 0 可接受，不加噪、不硬推非零，不承諾準確度改善。
- 即使真實模型劣於基準，也保存完整分數與風險標記交使用者決定；若不可訓練／推論，回傳明確原因與無估計狀態，不捏造值。

## 5. 18 月時間切分與公平比較

- 每 horizon 事前固定最低門檻：fit 60、selection 20、final holdout 20 個有效訊號日；不是股票列數，須另報列數、股票數、類別分布及所有排除分母。門檻是驗證設計，不保證能訓練。
- 先按各市場實際日期軸及已成熟標籤建立共同切分；同日所有股票屬同一區塊。左區塊僅保留 `label_end < 下一區塊起日` 的列，最終標籤必須已於市場 asof 成熟；逐邊界嚴格 purge，不以固定少隔幾日或放寬門檻湊數。
- H60/H63 預設順序為 fit → selection → final holdout，不另切機率校準區，明示 `uncalibrated`。selection 不能同時當校準又當獨立測試。
- H1/H5/H20 僅在額外、獨立且合格的校準區塊可容納時採 fit → calibration → selection → final holdout，所有邊界同樣 purge。是否校準及合格規則在看成績前固定，不能依分數臨時開關。
- 任一區塊不足、方向 fit 只有單一類別、標籤未成熟或特徵不足，逐 horizon 記 `not_trained` 原因與計數；不縮 purge、不造長期特徵。H63 重疊標籤多，20 個訊號日不代表 20 個獨立樣本，須明示估計不穩定。
- 比較 A：原七特徵全有限值／253 warmup 流程先報 18 月下可行性；原 active artifact 的歷史分數另列 `historical_non_independent`。它已用 evaluation 選頭，重用截至 10/8 的該區間不能宣稱獨立改善。
- 比較 B：在新共同切分、缺值與股票／日期規則下，七特徵 Logistic/Ridge 對擴充 linear/HGB；標明「受控對照」，不是原封不動舊 artifact 重驗。不得因候選失敗而暗改共同分母。
- 每輪普通股隨機抽樣事前封存 seed、抽樣數量、完整母體及排除理由；同輪受控組使用相同股票／日期／快照，資料合法性排除先定規則。錯誤、劣化及無法預測個案全部保留；未預測個案不虛構誤差，但必須進 coverage／失敗計數。
- 所有新候選共用未參與來源選擇、特徵選擇或選模的 final holdout；selection 先鎖候選 head。看 holdout 後再換頭／改規則即失去本輪獨立性，需新保留資料或前瞻驗證，不能重稱獨立測試。
- 報告方向 accuracy／balanced accuracy／Brier／log loss／適用時 AUC，報酬 MAE／RMSE，並列基準、成對差、coverage、樣本量、各市場及 horizon 限制；無法計算指標記 null 與原因，不承諾立即得到完整且無洩漏的 H63 新舊提升數字。

## 6. 共用介面、版本與正式採用邊界

- 共用層以版本派送維持 panel、訓練、預測及 artifact load/save 的公共 API 角色；個股 H1/H63、MIX H1/H5/H20/H60、Top10 H1 使用同一候選語義。固定程式檔名、報告入口不改名；本稿未讀改 MIX／Top10 程式。
- 新 feature/model schema 與舊 C0 schema 分離；原 JSON artifact／sidecar 保持原樣可讀，既有 `build_panel`、`fit_baseline`、`predict_baseline` 的舊版本行為不冒稱擴充版。新候選採明確 schema/模型選擇器，不改舊預設。
- 候選封存 source manifest、feature manifest、availability manifest、切分／抽樣／評估 manifest，及每 head 的 feature_order、來源規則、適用性、轉換器、模型種類、calibration_status、target 定義、版本與 hash。推論只用封存規則；資料變動不允許暗換來源或欄位。
- 三消費端 payload 均保留 horizon、market/asof、target session、prob_up、expected_return、model/schema、資料時間、來源使用狀態及無估計原因；同一語義由共用驗證器按版本驗證。目標市場日未驗證時不造日期，不把 QA override 當正式成品。
- 候選訓練／QA 可獨立驗收，不改 `D:\TWStockData\program_data\stock_daily_c0\models\current.json` 或既有 active artifact。不得直接改中央七特徵契約，也不得宣稱新模型仍符合原封存 C0 adoption。
- 正式採用須另有新 contract/adoption 版本、三消費端相容驗證及使用者明確選擇；跨專案變更是正式採用依賴，不在本次偷偷修改。不重啟 Telegram、不傳送、不發布、不切換模型。

## 7. 效能與後續驗收設計

- 按所選股票／日期／欄位批次投影，依 source hash＋schema＋選擇規則快取，僅對新快照增量更新；分析不掃全市場巨檔、不每股重訓。進度分讀取、特徵、推論、報告，記命中率／耗時；本稿不設定未量測的速度承諾。
- 後續實作全部必要修改完成後，由 low_worker 集中一次最小充分驗證：catalog／既有登錄不退步；not-selected 不誤判 missing；訓練推論規則與 feature_order 一致；同快照及未來資料負向案例；18 月 H63 purge／缺值合法且不填造。
- 同批必要驗收還含三消費端 payload 一致、基準不冒充模型、保留成對差結果與錯誤、final holdout 不參與選頭、舊 artifact 可讀且 current hash 不變；失敗只修相關部分。此為未執行的驗收條件，本輪不跑產品測試。

## 8. 尚未完成與具體介面決策

- 待使用者審閱後才進入實作；尚未建候選、訓練、驗證效能／準確度、啟用模型或確認外部來源完整。18 月與舊 462 日 fit 不再作期間不縮水硬門檻，來源目錄及既有項目登錄仍保留。
- 實作前需固定新 schema/profile 與公共 API 選擇器的命名、候選 artifact 放置位置、HGB 的受信任序列化／載入格式及套件版本相容規則；現有線性係數 JSON 格式不能直接假裝容納 HGB。
- 需固定各 horizon/head 的領域來源規則、候選參數、短期校準資格與抽樣規模；歷史公告時點不足保持 unknown，不為填滿特徵而猜測。股癌等舊來源只列待核對備註，是否未來接入中央另行決定，不成為每次分析或本輪設計完成的全來源門檻。
- 本次交付僅本設計文件；驗證僅限文件存在、UTF-8 可讀與自身差異，不安裝、不網路、不訓練、不測試、不 commit，不寫入其他路徑。
