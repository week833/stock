# 20261018 模型訓練與後續四批特徵研究：Claude 唯讀驗證交接

交接日期：20261019（臺北）；整理時點：20261019 07:59:36。本檔與證據清單均為新建，未改寫既有研究、正式程式或共享 notes。

## 1. 先讀結論與任務界線

「昨天」指臺北 20261018；研究延續至 20261019 凌晨，第三、四批仍納入本交接。20261018 較早已另經使用者授權啟用開盤前 H1；當晚四批新增特徵研究沒有替換正式模型。不能寫成昨天整天從未上線模型。

正式開盤前 H1 是 HGB 分類＋超額報酬 HGB 迴歸＋日層級 ridge 大盤估計，使用價量、法人／融資券／當沖／估值／外資持股、月營收、市場脈絡、前夜美股，凍結版本排除匯率欄。四批後續研究的 H1 僅取其分類元件、未做校準或報酬頭，不能視為完整正式模型對決。

報告新增的 **42 項歷史比較與中文解讀屬報告層**，不代表 42 項已進入正式模型特徵。四批研究共新增 182 個候選擬合窗口，另有 14 個唯一共同基準窗口；182 個 verified 全缺值等價重用不算有效新試驗。沒有足夠證據宣稱穩健提升或可交易獲利。

本輪只讀既有結果並製作文件；沒有執行 pytest、predict、train、重算模型實驗、安裝、下載、收集、服務或外部傳送。Claude 對原始資料、原研究輸入／結果、正式程式與模型皆唯讀；允許在獨立新 audit 目錄保存驗證腳本、差異表與報告，獨立重算既有預測與摘要。不得重訓、回填、修改 pipeline、收集設定或正式模型。

機器證據：[CLAUDE_MODEL_TRAINING_EVIDENCE_20261019.json](D:/stock/docs/CLAUDE_MODEL_TRAINING_EVIDENCE_20261019.json)。其中保留完整年度窗口指標、欄位名稱、378 個完成 cell 的路徑與既有 hash 綁定；關鍵檔另記現況絕對路徑、大小、SHA-256。

## 2. 前置研究與現行正式指標

前置校正研究請讀 [校正報告_20261018.md](D:/TWStockData/program_data/stock_daily_c0/candidates/calibration_20261018/report/校正報告_20261018.md) 與 [frozen_configs.json](D:/TWStockData/program_data/stock_daily_c0/candidates/calibration_20261018/results/frozen_configs.json)。17 個配方於臺北 20261018 06:15:30 凍結；名稱 h1pre_final_nogc_xs、配方 SHA e53c2416b63d8273162c3618564813cd13145052e5ac30f5c3c375fb48c3b28f。文件報告的 20260102～20261016 保留期結果為方向 59.0%、AUC 0.608、一律猜跌 55.9%；這是前置研究，與以下七個季度起始窗口的未校準元件指標不同。當時稱保留期，不代表在多次後續選組之後仍是未曝光資料。

| 現行系列 | pointer 絕對路徑 | 現況 pointer SHA-256 | 模型 SHA-256 |
| --- | --- | --- | --- |
| C0 | `D:\TWStockData\program_data\stock_daily_c0\models\current.json` | 260ee8b20243c2830b227f04884a6229cf01284c50e77a58f602b1ee9deb07cb | 2a1bc6a4ea360345f38815eedff49c5d9a44d00c4547bf3665bcfd3c45f07026 |
| preopen_H1 | `D:\TWStockData\program_data\stock_daily_preopen\models\current.json` | 28704c28678c1f30c6cf52fe771488ec3f8b9d8f9cd1ab64489eb2f9ea891b6d | 475735123e15774a2e440800bfafc1a19741b7a0794e718a72db2afd07b6ce6c |
| single_selected | `D:\TWStockData\program_data\dstock_single\models\selected_current.json` | 327e31cb23c3cd6d413577a8cc0dcd963d9ade1291a50fc592da3204cb2b2004 | 473bc21557be58e0266eb6e434dfcb4624a898efdd1f2c163f642310b04b448b |

模型絕對路徑、大小與綁定核對詳見 JSON 的 formal_models 及 files。這些 hash 是本次唯讀觀測，不是本輪訓練或換模紀錄。H63 正式 selected 兩頭各 19 有效欄：`return_5`, `return_20`, `return_60`, `return_126`, `volatility_20`, `volatility_60`, `return_1`, `volume_ratio_20`, `amount_ratio_20`, `transactions_ratio_20`, `intraday_range_ratio`, `foreign_net_relative_20`, `trust_net_relative_20`, `dealer_net_relative_20`, `margin_balance_change_ratio`, `short_balance_change_ratio`, `day_trade_relative_20`, `valuation_per`, `valuation_pbr`。

ADR-0008 的啟用與模型結構可作治理證據；其中「目前沒有自動排程」及 freshness／缺資料拒絕等舊敘述不得當成本次執行規則。最新使用者明確不加門檻，現行程式與 receipt 優先。本次沒有重新執行服務或排程驗收。治理 acceptance 是 20260823 的舊 blocked 觀測，不能因研究完成宣稱全系統正式驗收。

## 3. 四批主證據與實際執行量

| 批次 | 臺北交付時點 | 完成 cell | 候選新擬合窗口 | verified 全缺重用 | 新基準窗口 | 未執行／最終錯誤 |
| --- | --- | --- | --- | --- | --- | --- |
| batch1 | 20261018 23:33:52 | 210 | 98 | 98 | 14 | 0／None |
| batch2 | 20261018 23:58:09 | 56 | 28 | 28 | 0 | 0／None |
| batch3 | 20261019 01:12:08 | 84 | 42 | 42 | 0 | 0／None |
| batch4 | 20261019 01:33:49 | 28 | 14 | 14 | 0 | 0／None |

batch1 的 planned=224 是兩個時間模式各列 14 份 baseline；實際共用 14 份，故 completed=210，沒有漏掉 14 個窗口。後三批完成數為 56／84／28。四批合計 378 個完成 cell；候選窗口 98＋28＋42＋14＝182，基準 14，verified 等價重用 182。這是「模型窗口」計數：每個 H63 窗口內含分類與迴歸兩頭，不能稱為總 estimator fit 次數；亦未以失敗／續跑次數冒充新試驗。

batch2、batch3 各只讀重用第一批 14 份基準；batch4 重用基準／財官／借貸各 14 份，共 42 份參考預測，參考模型重訓 0。這些引用會重複使用相同舊檔，不是 70 個額外新基準。批4的 reference_metric_audit.json 記錄 60 筆舊指標重算吻合，此為既有證據，本交接未再重算。

- batch1：[完整 run 目錄](D:/TWStockData/program_data/stock_daily_c0/candidates/report_feature_eval_20261018/results/batch1_d3b139c79247)；config [batch1.json](D:/TWStockData/program_data/stock_daily_c0/candidates/report_feature_eval_20261018/configs/batch1.json)；runner [run_ablation.py](D:/TWStockData/program_data/stock_daily_c0/candidates/report_feature_eval_20261018/code/run_ablation.py)。config SHA：`d3b139c79247bcf7e27efa90f20bdcc5d983b993c94f9cf321b45744c6cef27b`。
- batch2：[完整 run 目錄](D:/TWStockData/program_data/stock_daily_c0/candidates/report_feature_eval_20261018/batch2_combinations/results/batch2_7555279cbb35)；config [batch2.json](D:/TWStockData/program_data/stock_daily_c0/candidates/report_feature_eval_20261018/batch2_combinations/config/batch2.json)；runner [run_combinations.py](D:/TWStockData/program_data/stock_daily_c0/candidates/report_feature_eval_20261018/batch2_combinations/code/run_combinations.py)。config SHA：`7555279cbb351c78e3daea7023f17730391d3977fe9c09c291318ef3192c8165`。
- batch3：[完整 run 目錄](D:/TWStockData/program_data/stock_daily_c0/candidates/report_feature_eval_20261018/batch3_20261019/results/batch3_22f9601ddf80)；config [batch3.json](D:/TWStockData/program_data/stock_daily_c0/candidates/report_feature_eval_20261018/batch3_20261019/config/batch3.json)；runner [run_candidates.py](D:/TWStockData/program_data/stock_daily_c0/candidates/report_feature_eval_20261018/batch3_20261019/code/run_candidates.py)。config SHA：`22f9601ddf8074c4374c183ea56098cd64825d633d1b77b07871db7b8bbe6069`。
- batch4：[完整 run 目錄](D:/TWStockData/program_data/stock_daily_c0/candidates/report_feature_eval_20261018/batch4_20261019/results/batch4_ec82cacaed89)；config [batch4.json](D:/TWStockData/program_data/stock_daily_c0/candidates/report_feature_eval_20261018/batch4_20261019/config/batch4.json)；runner [run_combination.py](D:/TWStockData/program_data/stock_daily_c0/candidates/report_feature_eval_20261018/batch4_20261019/code/run_combination.py)。config SHA：`ec82cacaed8918928f39e93c087f0108678435b968fff495db1bd4fc76856d11`。

閱讀每批順序：研究結果摘要.md → research_report.md → metrics.json → paired_summary.json → run_manifest.json／execution_inputs.json → h1／h63 各 fold 的 parquet 與 manifest。只使用這四個完成 run；synthetic tests、首次 failure_attempt 不能代替研究主證據。

## 4. 後來加入的資料與確切欄位

H1 原始研究基準 111 欄，H63 原始研究基準 88 欄；完整有序欄名位於 JSON `baseline_features` 及 batch1.json:baseline_features。兩者保留原欄，不因新群組缺值刪股、刪日或加 coverage／freshness 篩選。原始欄數不是正式 selected 的有效欄數。

| 群組 | 來源 route_id | 每一時間版新增欄數 | 對應特徵檔／manifest |
| --- | --- | --- | --- |
| financial／財報 | `finmind_financial_statements`, `finmind_balance_sheet`, `finmind_cash_flows_statement` | 5 | `D:\TWStockData\program_data\stock_daily_c0\candidates\report_feature_eval_20261018\cache\financial_macro_features.parquet`；`D:\TWStockData\program_data\stock_daily_c0\candidates\report_feature_eval_20261018\cache\financial_macro_manifest.json` |
| fxbond／匯率公債 | `finmind_exchange_rate`, `finmind_government_bonds_yield` | 4 | `D:\TWStockData\program_data\stock_daily_c0\candidates\report_feature_eval_20261018\cache\financial_macro_features.parquet`；`D:\TWStockData\program_data\stock_daily_c0\candidates\report_feature_eval_20261018\cache\financial_macro_manifest.json` |
| futures／期貨法人 | `finmind_futures_institutional` | 9 | `D:\TWStockData\program_data\stock_daily_c0\candidates\report_feature_eval_20261018\cache\chips_features.parquet`；`D:\TWStockData\program_data\stock_daily_c0\candidates\report_feature_eval_20261018\cache\chips_manifest.json` |
| govbank／官股 | `finmind_government_bank` | 3 | `D:\TWStockData\program_data\stock_daily_c0\candidates\report_feature_eval_20261018\cache\chips_features.parquet`；`D:\TWStockData\program_data\stock_daily_c0\candidates\report_feature_eval_20261018\cache\chips_manifest.json` |
| maintenance／整體融資維持率 | `finmind_margin_maintenance` | 2 | `D:\TWStockData\program_data\stock_daily_c0\candidates\report_feature_eval_20261018\cache\chips_features.parquet`；`D:\TWStockData\program_data\stock_daily_c0\candidates\report_feature_eval_20261018\cache\chips_manifest.json` |
| tdcc／集保分布 | `tdcc_share_distribution_raw` | 6 | `D:\TWStockData\program_data\stock_daily_c0\candidates\report_feature_eval_20261018\cache\chips_features.parquet`；`D:\TWStockData\program_data\stock_daily_c0\candidates\report_feature_eval_20261018\cache\chips_manifest.json` |
| options／選擇權法人 | `finmind_options_institutional` | 6 | `D:\TWStockData\program_data\stock_daily_c0\candidates\report_feature_eval_20261018\batch3_20261019\adapters\options\cache\features.parquet`；`D:\TWStockData\program_data\stock_daily_c0\candidates\report_feature_eval_20261018\batch3_20261019\adapters\options\cache\manifest.json` |
| loan／借貸抵押 | `finmind_loan_collateral` | 12 | `D:\TWStockData\program_data\stock_daily_c0\candidates\report_feature_eval_20261018\batch3_20261019\adapters\loan\cache\features.parquet`；`D:\TWStockData\program_data\stock_daily_c0\candidates\report_feature_eval_20261018\batch3_20261019\adapters\loan\cache\manifest.json` |

**期貨法人 futures** 使用 finmind_futures_institutional；**選擇權法人 options** 使用 finmind_options_institutional，是兩條不同來源。後來停收的是後者，不能把第一批期貨來源也寫成已停收。

| 批次／組別 | 群組 | 新增欄 | H1 總欄 | H63 總欄 |
| --- | --- | --- | --- | --- |
| batch1/baseline | 無 | 0 | 111 | 88 |
| batch1/financial | financial | 5 | 116 | 93 |
| batch1/fxbond | fxbond | 4 | 115 | 92 |
| batch1/futures | futures | 9 | 120 | 97 |
| batch1/govbank | govbank | 3 | 114 | 91 |
| batch1/maintenance | maintenance | 2 | 113 | 90 |
| batch1/tdcc | tdcc | 6 | 117 | 94 |
| batch1/all | financial,fxbond,futures,govbank,maintenance,tdcc | 29 | 140 | 117 |
| batch2/financial_govbank | financial,govbank | 8 | 119 | 96 |
| batch2/financial_govbank_fxbond | financial,govbank,fxbond | 12 | 123 | 100 |
| batch3/options | options | 6 | 117 | 94 |
| batch3/loan | loan | 12 | 123 | 100 |
| batch3/all | options,loan | 18 | 129 | 106 |
| batch4/financial_govbank_loan | financial,govbank,loan | 20 | 131 | 108 |

第一批 all 僅指第一批六群組合計29欄；第三批 all 只指 options＋loan 共18欄，不是跨三批全部合併。第四批新組 financial_govbank_loan 加20欄；loan 舊單組只作參考，未重新訓練或額外搜尋。

新增特徵精確名稱如下列 estimated 版；verified 對應完整名單存於 JSON `feature_groups.<群組>.feature_names_by_mode`，不是以字首推測後補。
- financial（5）：`rf_fin_estimated_operating_margin_reported`, `rf_fin_estimated_operating_margin_quarter`, `rf_fin_estimated_debt_assets`, `rf_fin_estimated_ocf_yoy_same_period`, `rf_fin_estimated_ocf_quarter_yoy`。
- fxbond（4）：`rf_macro_estimated_usd_spot_return_1`, `rf_macro_estimated_usd_spot_return_5`, `rf_macro_estimated_us10y_relative_change_1`, `rf_macro_estimated_us10y_relative_change_5`。
- futures（9）：`rf_fut_estimated_foreign_net_oi_native`, `rf_fut_estimated_foreign_oi_imbalance`, `rf_fut_estimated_foreign_net_oi_delta5`, `rf_fut_estimated_trust_net_oi_native`, `rf_fut_estimated_trust_oi_imbalance`, `rf_fut_estimated_trust_net_oi_delta5`, `rf_fut_estimated_dealer_net_oi_native`, `rf_fut_estimated_dealer_oi_imbalance`, `rf_fut_estimated_dealer_net_oi_delta5`。
- govbank（3）：`rf_gov_estimated_net_amount_native`, `rf_gov_estimated_amount_imbalance`, `rf_gov_estimated_quantity_imbalance`。
- maintenance（2）：`rf_maint_estimated_raw`, `rf_maint_estimated_delta5`。
- tdcc（6）：`rf_tdcc_estimated_level_01_ratio`, `rf_tdcc_estimated_level_01_ratio_delta_release`, `rf_tdcc_estimated_level_08_ratio`, `rf_tdcc_estimated_level_08_ratio_delta_release`, `rf_tdcc_estimated_level_15_ratio`, `rf_tdcc_estimated_level_15_ratio_delta_release`。
- options（6）：`rf_b3_options_estimated_foreign_call_oi_imbalance`, `rf_b3_options_estimated_foreign_call_oi_imbalance_delta5`, `rf_b3_options_estimated_foreign_call_oi_imbalance_mean20`, `rf_b3_options_estimated_foreign_put_oi_imbalance`, `rf_b3_options_estimated_foreign_put_oi_imbalance_delta5`, `rf_b3_options_estimated_foreign_put_oi_imbalance_mean20`。
- loan（12）：`rf_b3_loan_estimated_unrestricted_change_1`, `rf_b3_loan_estimated_unrestricted_change_5`, `rf_b3_loan_estimated_unrestricted_change_20`, `rf_b3_loan_estimated_unrestricted_current_balance_is_zero`, `rf_b3_loan_estimated_finance_secured_change_1`, `rf_b3_loan_estimated_finance_secured_change_5`, `rf_b3_loan_estimated_finance_secured_change_20`, `rf_b3_loan_estimated_finance_secured_current_balance_is_zero`, `rf_b3_loan_estimated_securities_firm_change_1`, `rf_b3_loan_estimated_securities_firm_change_5`, `rf_b3_loan_estimated_securities_firm_change_20`, `rf_b3_loan_estimated_securities_firm_current_balance_is_zero`。

## 5. 演算法、時間拆分與指標定義

H1 實作為 sklearn.ensemble.HistGradientBoostingClassifier。凍結的顯式參數：`{"max_iter": 200, "learning_rate": 0.05, "max_leaf_nodes": 31, "min_samples_leaf": 500, "l2_regularization": 1.0, "max_features": 1.0, "early_stopping": false, "random_state": 0}`。原111欄加群組欄，float32 輸入，保留 NaN；分類標籤 y>0，p_up>=0.5 判漲。研究沒有機率校準，也沒有訓練報酬頭，故 H1 MAE／IC 一律為不適用，不填0。

H63 方向為 sklearn.linear_model.LogisticRegression，參數 `{"C": 1.0, "max_iter": 1000, "solver": "lbfgs", "tol": 0.0001, "random_state": 0}`；報酬為 sklearn.linear_model.Ridge，參數 `{"alpha": 10.0}`，目標為 raw y63。前處理僅以訓練集估計中位數（全缺欄補0）、均值／標準差（0或無效標準差設1），標準化後截在[-5,5]。研究未做機率或報酬校準；88欄並非正式 selected 兩頭各19有效欄的等價重訓。確切實作在 run_ablation.py:TrainScaler／fit_predict，後批複用凍結核心。

執行版本依凍結記錄：`{"python": "3.12.10 (tags/v3.12.10:0cc8128, Apr  8 2025, 12:21:36) [MSC v.1943 64 bit (AMD64)]", "numpy": "2.5.1", "pandas": "2.3.3", "scipy": "1.18.0", "sklearn": "1.9.0", "pyarrow": "25.0.0"}`；未列在顯式參數內者沿用該版本預設，不以目前任意環境重設。每次擬合4執行緒、單研究程序；不把 H1/H63 不同期限的準確度直接拿來判斷演算法優劣。

固定設計：7季度起點×各21交易日；504交易日訓練、50%訓練列抽樣、seed0、每個 fold 重設種子且各組用相同訓練列。資料 panel 1,991,820 列，固定 head `4513ff48d6682ff3c65d26148edb776e5fb7559561ab15900e172e65fb81ab19`。Horizon h 的訓練尾端為 fold_start_di-h-1，標籤須在 fold 起點前成熟；評分再依指定年末／回溯截止日限制成熟標籤，不捏造未來目標日。

| fold | 訊號日起 | 訊號日迄 | 期間 |
| --- | --- | --- | --- |
| 2025Q1 | 20250102 | 20250210 | 2025_development |
| 2025Q2 | 20250401 | 20250502 | 2025_development |
| 2025Q3 | 20250701 | 20250729 | 2025_development |
| 2025Q4 | 20251001 | 20251103 | 2025_development |
| 2026Q1 | 20260102 | 20260130 | 2026_retrospective |
| 2026Q2 | 20260401 | 20260504 | 2026_retrospective |
| 2026Q3 | 20260701 | 20260730 | 2026_retrospective |

2025與2026僅是指定窗口按年度彙總，並非完整年度績效。H1可評84／63日；H63為63／53日，共116日。H63 2025Q4 無年末前成熟標籤、2026Q3只有11日可評；預測列全部保留，只對成熟標籤計分。

指標先逐日期、同股同市場同標籤計算，再等權平均日期：accuracy＝mean((p_up>=0.5)==(y>0))；Brier＝mean((p_up-(y>0))²)；AUC只有同日有兩類時可計，否則缺值並記有效日數；H63 MAE＝mean(abs(exp_ret-y))；rank IC為同日預測報酬與真實報酬的排名相關，常數預測依原實作記0。不能拿所有股票列直接 pooled mean 取代日期平均，也不能把股票列當獨立樣本。

時間版本：verified 只接受真正公開／可得 timestamp，收集時間與 head 發布時間不得替代；本次182組新增特徵均全缺，僅等價重用。estimated 財報採法定期限估計；日頻宏觀／籌碼採來源觀測後下一台股交易日；集保採觀測後7曆日再下一交易日；options／loan 也沿用下一交易日假設。這些是日期可得性假設，不是已證明歷史公告時點；資料修訂、隔夜與盤前切點仍待核實。雙倍延遲敏感度未在第一批執行，不能寫成通過。

## 6. 全部組合：指定窗口按年度彙總

以下直接取各完成 run 的 metrics.json，顯示6位小數。準確度與MAE乘100顯示百分比；AUC／Brier／IC是原分數。2025＝既有開發窗口，2026＝已曝光回溯窗口。batch4出現的基準／財官／loan是舊結果引用。JSON保留未四捨五入數值與來源 JSON index；H1／H63分表，避免不同期限混比。
### H1

<!-- data-table:estimated_h1 -->
| 批次 | 組別ID | 中文組別 | 年度窗口 | 準確度% | AUC | Brier | MAE% | IC | 可評日 | 計分列 |
| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |
| batch1 | baseline | 共同基準 | 2025 | 59.775403 | 0.579418 | 0.230737 | 不適用 | 不適用 | 84 | 153415 |
| batch1 | baseline | 共同基準 | 2026 | 58.377595 | 0.575388 | 0.239672 | 不適用 | 不適用 | 63 | 118916 |
| batch1 | financial | 財報 | 2025 | 59.957599 | 0.580372 | 0.229771 | 不適用 | 不適用 | 84 | 153415 |
| batch1 | financial | 財報 | 2026 | 58.528106 | 0.578654 | 0.239353 | 不適用 | 不適用 | 63 | 118916 |
| batch1 | fxbond | 匯率公債 | 2025 | 59.698330 | 0.581210 | 0.230957 | 不適用 | 不適用 | 84 | 153415 |
| batch1 | fxbond | 匯率公債 | 2026 | 58.961894 | 0.578813 | 0.238139 | 不適用 | 不適用 | 63 | 118916 |
| batch1 | futures | 期貨法人 | 2025 | 59.483072 | 0.582018 | 0.232478 | 不適用 | 不適用 | 84 | 153415 |
| batch1 | futures | 期貨法人 | 2026 | 58.819977 | 0.578989 | 0.239719 | 不適用 | 不適用 | 63 | 118916 |
| batch1 | govbank | 官股 | 2025 | 59.934694 | 0.579354 | 0.230409 | 不適用 | 不適用 | 84 | 153415 |
| batch1 | govbank | 官股 | 2026 | 58.558658 | 0.576283 | 0.239383 | 不適用 | 不適用 | 63 | 118916 |
| batch1 | maintenance | 整體融資維持率 | 2025 | 59.666937 | 0.577322 | 0.231570 | 不適用 | 不適用 | 84 | 153415 |
| batch1 | maintenance | 整體融資維持率 | 2026 | 58.710170 | 0.577224 | 0.239770 | 不適用 | 不適用 | 63 | 118916 |
| batch1 | tdcc | 集保分布 | 2025 | 59.809916 | 0.578807 | 0.230905 | 不適用 | 不適用 | 84 | 153415 |
| batch1 | tdcc | 集保分布 | 2026 | 58.389742 | 0.577919 | 0.239387 | 不適用 | 不適用 | 63 | 118916 |
| batch1 | all | 該批全部群組 | 2025 | 59.746334 | 0.578881 | 0.233638 | 不適用 | 不適用 | 84 | 153415 |
| batch1 | all | 該批全部群組 | 2026 | 58.554983 | 0.580940 | 0.237944 | 不適用 | 不適用 | 63 | 118916 |
| batch2 | baseline | 共同基準 | 2025 | 59.775403 | 0.579418 | 0.230737 | 不適用 | 不適用 | 84 | 153415 |
| batch2 | baseline | 共同基準 | 2026 | 58.377595 | 0.575388 | 0.239672 | 不適用 | 不適用 | 63 | 118916 |
| batch2 | financial_govbank | 財報＋官股 | 2025 | 59.996441 | 0.581142 | 0.229527 | 不適用 | 不適用 | 84 | 153415 |
| batch2 | financial_govbank | 財報＋官股 | 2026 | 58.576051 | 0.578003 | 0.239420 | 不適用 | 不適用 | 63 | 118916 |
| batch2 | financial_govbank_fxbond | 財報＋官股＋匯率公債 | 2025 | 59.789318 | 0.581410 | 0.230545 | 不適用 | 不適用 | 84 | 153415 |
| batch2 | financial_govbank_fxbond | 財報＋官股＋匯率公債 | 2026 | 58.513084 | 0.577176 | 0.239132 | 不適用 | 不適用 | 63 | 118916 |
| batch3 | baseline | 共同基準 | 2025 | 59.775403 | 0.579418 | 0.230737 | 不適用 | 不適用 | 84 | 153415 |
| batch3 | baseline | 共同基準 | 2026 | 58.377595 | 0.575388 | 0.239672 | 不適用 | 不適用 | 63 | 118916 |
| batch3 | options | 選擇權法人 | 2025 | 59.490852 | 0.579078 | 0.232343 | 不適用 | 不適用 | 84 | 153415 |
| batch3 | options | 選擇權法人 | 2026 | 58.118469 | 0.575601 | 0.239052 | 不適用 | 不適用 | 63 | 118916 |
| batch3 | loan | 借貸抵押 | 2025 | 59.914616 | 0.579757 | 0.230379 | 不適用 | 不適用 | 84 | 153415 |
| batch3 | loan | 借貸抵押 | 2026 | 58.360888 | 0.576796 | 0.239223 | 不適用 | 不適用 | 63 | 118916 |
| batch3 | all | 該批全部群組 | 2025 | 59.498228 | 0.580803 | 0.232950 | 不適用 | 不適用 | 84 | 153415 |
| batch3 | all | 該批全部群組 | 2026 | 58.009161 | 0.575270 | 0.239804 | 不適用 | 不適用 | 63 | 118916 |
| batch4 | baseline | 共同基準 | 2025 | 59.775403 | 0.579418 | 0.230737 | 不適用 | 不適用 | 84 | 153415 |
| batch4 | baseline | 共同基準 | 2026 | 58.377595 | 0.575388 | 0.239672 | 不適用 | 不適用 | 63 | 118916 |
| batch4 | financial_govbank | 財報＋官股 | 2025 | 59.996441 | 0.581142 | 0.229527 | 不適用 | 不適用 | 84 | 153415 |
| batch4 | financial_govbank | 財報＋官股 | 2026 | 58.576051 | 0.578003 | 0.239420 | 不適用 | 不適用 | 63 | 118916 |
| batch4 | loan | 借貸抵押 | 2025 | 59.914616 | 0.579757 | 0.230379 | 不適用 | 不適用 | 84 | 153415 |
| batch4 | loan | 借貸抵押 | 2026 | 58.360888 | 0.576796 | 0.239223 | 不適用 | 不適用 | 63 | 118916 |
| batch4 | financial_govbank_loan | 財報＋官股＋借貸抵押 | 2025 | 59.946248 | 0.580641 | 0.229405 | 不適用 | 不適用 | 84 | 153415 |
| batch4 | financial_govbank_loan | 財報＋官股＋借貸抵押 | 2026 | 58.577397 | 0.578300 | 0.238847 | 不適用 | 不適用 | 63 | 118916 |
<!-- end-data-table:estimated_h1 -->

### H63

<!-- data-table:estimated_h63 -->
| 批次 | 組別ID | 中文組別 | 年度窗口 | 準確度% | AUC | Brier | MAE% | IC | 可評日 | 計分列 |
| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |
| batch1 | baseline | 共同基準 | 2025 | 40.872079 | 0.533331 | 0.314043 | 16.967642 | -0.032836 | 63 | 114087 |
| batch1 | baseline | 共同基準 | 2026 | 55.599163 | 0.580399 | 0.244511 | 19.740593 | 0.106171 | 53 | 99428 |
| batch1 | financial | 財報 | 2025 | 40.857426 | 0.532230 | 0.314242 | 16.966409 | -0.036227 | 63 | 114087 |
| batch1 | financial | 財報 | 2026 | 55.590084 | 0.580904 | 0.244438 | 19.742910 | 0.104803 | 53 | 99428 |
| batch1 | fxbond | 匯率公債 | 2025 | 40.892195 | 0.533376 | 0.312634 | 16.916620 | -0.031629 | 63 | 114087 |
| batch1 | fxbond | 匯率公債 | 2026 | 55.798798 | 0.582145 | 0.244410 | 19.799234 | 0.109975 | 53 | 99428 |
| batch1 | futures | 期貨法人 | 2025 | 44.315101 | 0.538900 | 0.363638 | 23.858225 | -0.042247 | 63 | 114087 |
| batch1 | futures | 期貨法人 | 2026 | 50.593424 | 0.579332 | 0.278047 | 20.274809 | 0.110064 | 53 | 99428 |
| batch1 | govbank | 官股 | 2025 | 40.863375 | 0.533401 | 0.314025 | 16.968166 | -0.033057 | 63 | 114087 |
| batch1 | govbank | 官股 | 2026 | 55.647370 | 0.580509 | 0.244459 | 19.740172 | 0.106166 | 53 | 99428 |
| batch1 | maintenance | 整體融資維持率 | 2025 | 40.695202 | 0.532663 | 0.314092 | 16.970501 | -0.033775 | 63 | 114087 |
| batch1 | maintenance | 整體融資維持率 | 2026 | 55.564390 | 0.580991 | 0.244437 | 19.732211 | 0.107847 | 53 | 99428 |
| batch1 | tdcc | 集保分布 | 2025 | 40.872079 | 0.533331 | 0.314043 | 16.967642 | -0.032836 | 63 | 114087 |
| batch1 | tdcc | 集保分布 | 2026 | 55.150464 | 0.569612 | 0.246247 | 19.953839 | 0.093958 | 53 | 99428 |
| batch1 | all | 該批全部群組 | 2025 | 44.986577 | 0.538052 | 0.361552 | 23.809734 | -0.045170 | 63 | 114087 |
| batch1 | all | 該批全部群組 | 2026 | 50.196339 | 0.571078 | 0.279789 | 20.562819 | 0.094425 | 53 | 99428 |
| batch2 | baseline | 共同基準 | 2025 | 40.872079 | 0.533331 | 0.314043 | 16.967642 | -0.032836 | 63 | 114087 |
| batch2 | baseline | 共同基準 | 2026 | 55.599163 | 0.580399 | 0.244511 | 19.740593 | 0.106171 | 53 | 99428 |
| batch2 | financial_govbank | 財報＋官股 | 2025 | 40.836615 | 0.532197 | 0.314366 | 16.966872 | -0.036475 | 63 | 114087 |
| batch2 | financial_govbank | 財報＋官股 | 2026 | 55.569903 | 0.580896 | 0.244463 | 19.742524 | 0.104813 | 53 | 99428 |
| batch2 | financial_govbank_fxbond | 財報＋官股＋匯率公債 | 2025 | 40.846163 | 0.532317 | 0.312869 | 16.915153 | -0.035225 | 63 | 114087 |
| batch2 | financial_govbank_fxbond | 財報＋官股＋匯率公債 | 2026 | 55.759337 | 0.582143 | 0.244427 | 19.801236 | 0.108625 | 53 | 99428 |
| batch3 | baseline | 共同基準 | 2025 | 40.872079 | 0.533331 | 0.314043 | 16.967642 | -0.032836 | 63 | 114087 |
| batch3 | baseline | 共同基準 | 2026 | 55.599163 | 0.580399 | 0.244511 | 19.740593 | 0.106171 | 53 | 99428 |
| batch3 | options | 選擇權法人 | 2025 | 44.546207 | 0.535235 | 0.302222 | 16.187346 | -0.054805 | 63 | 114087 |
| batch3 | options | 選擇權法人 | 2026 | 53.938150 | 0.580560 | 0.256542 | 19.788951 | 0.099699 | 53 | 99428 |
| batch3 | loan | 借貸抵押 | 2025 | 41.231608 | 0.535177 | 0.312893 | 16.966996 | -0.025113 | 63 | 114087 |
| batch3 | loan | 借貸抵押 | 2026 | 55.768855 | 0.579604 | 0.244502 | 19.807195 | 0.113510 | 53 | 99428 |
| batch3 | all | 該批全部群組 | 2025 | 44.248992 | 0.535115 | 0.303226 | 16.242433 | -0.050491 | 63 | 114087 |
| batch3 | all | 該批全部群組 | 2026 | 54.427846 | 0.581597 | 0.254749 | 19.781716 | 0.107461 | 53 | 99428 |
| batch4 | baseline | 共同基準 | 2025 | 40.872079 | 0.533331 | 0.314043 | 16.967642 | -0.032836 | 63 | 114087 |
| batch4 | baseline | 共同基準 | 2026 | 55.599163 | 0.580399 | 0.244511 | 19.740593 | 0.106171 | 53 | 99428 |
| batch4 | financial_govbank | 財報＋官股 | 2025 | 40.836615 | 0.532197 | 0.314366 | 16.966872 | -0.036475 | 63 | 114087 |
| batch4 | financial_govbank | 財報＋官股 | 2026 | 55.569903 | 0.580896 | 0.244463 | 19.742524 | 0.104813 | 53 | 99428 |
| batch4 | loan | 借貸抵押 | 2025 | 41.231608 | 0.535177 | 0.312893 | 16.966996 | -0.025113 | 63 | 114087 |
| batch4 | loan | 借貸抵押 | 2026 | 55.768855 | 0.579604 | 0.244502 | 19.807195 | 0.113510 | 53 | 99428 |
| batch4 | financial_govbank_loan | 財報＋官股＋借貸抵押 | 2025 | 41.209469 | 0.534141 | 0.313110 | 16.964612 | -0.028810 | 63 | 114087 |
| batch4 | financial_govbank_loan | 財報＋官股＋借貸抵押 | 2026 | 55.704121 | 0.579772 | 0.244475 | 19.809172 | 0.112233 | 53 | 99428 |
<!-- end-data-table:estimated_h63 -->

verified 版所有群組的五項指標、計分日與列數均與同期限年度共同基準相同；它們的完整年度列也保存在證據 JSON `annual_metrics`。這是全缺值重用，不是新增特徵勝出；不再把相同數字列成額外有效試驗。

### 配對差與兩個對照不可混用

batch4 H63 新組相對財官的全部可評窗口 ΔIC＝+0.007553，5/6季改善，探索95%區間[+0.000220,+0.017828]；相對共同基準 ΔIC＝+0.004957，4/6季改善，區間[-0.000775,+0.015256]跨0。兩者不是同一結論。相對財官的2026 MAE惡化0.06665百分點，不能因IC訊號宣稱所有誤差均改善。原始配對記錄在 batch4 paired_summary.json，JSON另保留這兩筆完整記錄。

各批 paired_summary.json 保留年度／季度／全窗口差值、可評季度數與探索區間；bootstrap=2000、seed0，以日期／fold群組處理，H63用兩fold區塊。季度少、區塊與標籤重疊、多重比較及選組後回溯都限制推論；不能把區間不跨0包裝成獨立確認。

## 7. 已有失敗、修復與測試證據（本次未重跑）

- batch1：首次最小測試21 passed／30.70秒；相關修復後3 passed／23.62秒，24是案例執行總次數，含重複案例，不是24個唯一case。第一次真實estimated財報組在HGB全缺欄分箱觸發 ValueError: window shape cannot be larger than input array shape；失敗時完成1個基準及7個verified等價重用。只在研究process內修空threshold分支並還原，不改sklearn套件／欄／參數；原baseline安全重用，最終完整run210筆、error=null。證據：[full_traceback.txt](D:/TWStockData/program_data/stock_daily_c0/candidates/report_feature_eval_20261018/results/batch1_d3b139c79247/failure_attempt_01/full_traceback.txt)、[baseline_reuse_audit.json](D:/TWStockData/program_data/stock_daily_c0/candidates/report_feature_eval_20261018/results/batch1_d3b139c79247/failure_attempt_01/baseline_reuse_audit.json)及完成run delivery_manifest.json。
- batch2：一次8 passed／11.75秒，新增修復0、重驗0；成功研究351.156秒。證據：[minimal_tests.log](D:/TWStockData/program_data/stock_daily_c0/candidates/report_feature_eval_20261018/batch2_combinations/logs/minimal_tests.log)、[research_7555279cbb35.log](D:/TWStockData/program_data/stock_daily_c0/candidates/report_feature_eval_20261018/batch2_combinations/logs/research_7555279cbb35.log)。
- batch3：首次收集測試因 options import ijson 失敗，執行案例0；原owner修復依賴後，runner／loan 39 passed／19.39秒，options 28 passed／0.76秒，共67唯一通過，未重跑未受影響39項。證據：[concentrated_validation_attempt01.log](D:/TWStockData/program_data/stock_daily_c0/candidates/report_feature_eval_20261018/batch3_20261019/logs/concentrated_validation_attempt01.log)、[affected_runner_loan_attempt02.log](D:/TWStockData/program_data/stock_daily_c0/candidates/report_feature_eval_20261018/batch3_20261019/logs/affected_runner_loan_attempt02.log)、[affected_options_after_owner_fix.log](D:/TWStockData/program_data/stock_daily_c0/candidates/report_feature_eval_20261018/batch3_20261019/logs/affected_options_after_owner_fix.log)、[validation_status.json](D:/TWStockData/program_data/stock_daily_c0/candidates/report_feature_eval_20261018/batch3_20261019/logs/validation_status.json)。不把測試收集失敗說成研究擬合失敗。
- batch4：最小8 passed／10.92秒；首次真實新組在fit前 KeyError: resources，當時新擬合0。只修包裝層傳遞既定資源設定並強化原端到端斷言，受影響1 passed／10.09秒；8唯一case、9次執行。原失敗與備份保留，成功run28筆、error=null。證據：[research_attempt01.log](D:/TWStockData/program_data/stock_daily_c0/candidates/report_feature_eval_20261018/batch4_20261019/logs/research_attempt01.log)、[related_resources_regression.log](D:/TWStockData/program_data/stock_daily_c0/candidates/report_feature_eval_20261018/batch4_20261019/logs/related_resources_regression.log)及[delivery_receipt.json](D:/TWStockData/program_data/stock_daily_c0/candidates/report_feature_eval_20261018/batch4_20261019/logs/delivery_receipt.json)。

以上為既有receipt／log所載結果，不是本輪再驗證。未找到可完整證明全部原shell quoting、工作目錄與環境變數的統一命令收據，故不臆造「原樣重跑命令」。各runner入口與config絕對路徑已列；它們預設會訓練／寫結果，本次不得執行。下節提供可直接操作的唯讀命令。

## 8. Claude 唯讀驗證順序與精確操作

1. 先讀本檔與 JSON；核對四個主run的config SHA、status、error、unrun、完成數，勿把 failure_attempt 或synthetic結果替代。再讀前置校正資料及正式pointer，區分已授權啟用H1與後續研究未換模。
2. 只對 JSON `files` 所列存在的小型關鍵檔核對現況大小／SHA；缺件依 dependency_gaps 分類。正式pointer模型只算hash，不印大型單行JSON、不反序列化未知模型。歷史registry舊hash與現況active32的差異按授權變更解讀，不作自動篡改或freshness閘門。
3. 查run_manifest.completed_cells的train_key_sha256、test_key_sha256、train_label_sha256、first/last_train_di、label_cutoff_di，再選同horizon/fold的prediction_file核對stock_id/market/date、y、label_end_di、metric_eligible；不能只比列數。H1與H63標籤期限不同，不互相當paired。
4. 對verified與對應baseline做預測檔SHA核對與全欄逐值比對（NaN按NaN等價），核對reused_from與baseline_prediction_sha256。batch2/3的14份基準及batch4各14份參考都不可計入新訓練。
5. 以既有daily_metrics.parquet重算年度／全窗口均值與sum(row counts)，與metrics.json比對；必要時再從既有prediction parquet逐日重算五指標。這是重算既有結果摘要，不重新訓練。配對需同股、同日、同標籤，重算paired_summary前先核對日序、fold區塊、bootstrap 2000次、seed=0與比較方向，H63不足可估區間不可補0。未經另行授權不啟動runner main。
6. 審查adapter manifest的source_time_assumptions、feature_definitions、source_objects、原始觀測日期／可得日期與age欄；確認沒有用收集時間當公開時間、沒有future bfill、missing值沒有被偽造。這才是時序可得性審查，不能只看指標。

以下 PowerShell 命令僅讀取、輸出到主控台，不建立cache／報告，不啟動train／predict：
```powershell
$evidence = 'D:\stock\docs\CLAUDE_MODEL_TRAINING_EVIDENCE_20261019.json'
$e = Get-Content -LiteralPath $evidence -Raw -Encoding UTF8 | ConvertFrom-Json
$e.batches | Select-Object batch,status,completed_cells,unrun,error
$e.files | Where-Object { $_.exists } | ForEach-Object {
  $f = Get-Item -LiteralPath $_.path
  $s = (Get-FileHash -LiteralPath $_.path -Algorithm SHA256).Hash.ToLowerInvariant()
  [pscustomobject]@{ Path=$_.path; BytesMatch=($f.Length -eq $_.size_bytes); ShaMatch=($s -eq $_.sha256) }
}
$e.annual_metrics | Where-Object { $_.mode -eq "estimated" } | Format-Table batch,horizon,group,scope,accuracy,auc,brier,mae,rank_ic,scored_dates
```

逐列核對時用 `D:\stock\.venv\Scripts\python.exe -B`，只用 pandas.read_parquet／pyarrow 讀既有檔，不import或呼叫runner main。prediction欄位實查為 date,stock_id,market,fold_id,period,horizon,signal_di,label_end_di,y,p_up,metric_eligible；H63另有exp_ret。daily_metrics識別欄為horizon/mode/variant（batch2用combination），年度取period，五指標忽略NaN後按日期平均。

## 9. 重現缺口與授權清理的時間界線

當前registry仍有36份route契約項，但enabled只有32；停收四路為 `finmind_options_institutional`, `finmind_trading_daily_report`, `official_active_etf_holding`, `official_active_etf_change`。不能把「active32」誤寫成歷史config只用32路，亦不能因研究完成後36→32就判成未授權篡改。

batch4於臺北20261019 01:30:07研究完成後明確釋放registry／head／四路payload清理相依：[registry_head_dependency_release.json](D:/TWStockData/program_data/stock_daily_c0/candidates/report_feature_eval_20261018/batch4_20261019/results/batch4_ec82cacaed89/registry_head_dependency_release.json)；新組沒有options輸入依賴。停收套用收據與後續已授權payload刪除子集收據列於JSON；該子集收據保留其他名字，不代表所有歷史payload均已刪光。

- 現況缺件：`D:\TWStockData\shared_market_data\_objects\17fc7d34b7e820d8964a1356213f391b990b008f34c8de29535303a89a90925e.json`；原綁定SHA `17fc7d34b7e820d8964a1356213f391b990b008f34c8de29535303a89a90925e`。
- 現況缺件：`D:\TWStockData\program_data\stock_daily_c0\candidates\report_feature_eval_20261018\batch3_20261019\adapters\options\cache\features.parquet`；原綁定SHA `079e78c48cbc21ebb2055c645cc5ad680ef162876a19e99bdffe7ff6a9a61a7e`。
options manifest仍在，保存原object與feature cache hash；第三批options原路徑目前不能直接重訓。不可擅自重新抓取、重建或從別的快照替代原綁定。財報／匯率公債、chips、loan feature cache仍存在；本次未重做這些大檔、panel、全部預測檔或所有Central objects的SHA／逐列核對，只記存在性、大小及原freeze綁定。既有預測、run manifest、metrics與paired summary仍能獨立檢查；完整從來源重訓的重現性目前不成立。

四批runner的交付以逐fold預測、metadata、固定recipe為主，不把它視為每個fold完整模型權重的已封存交付。若未來另授權重現訓練，須先確認所有原source/cache、完整panel/calendar、版本與取樣鍵俱在；缺件先回報，不調資料門檻、不換模型或偷偷補資料。

## 10. 研究結論與待查問題分開

### 已有結果能支持的結論

- 第一批財報、官股可保留作H1有限研究候選；官股2025 AUC微降，不能稱所有指標跨年皆改善。FXBond的H1 2026準確度增加約0.5843百分點，但2025反向。第一批全加在H63 2026準確度下降約5.4028百分點，不支持直接全部合併。
- 第二批財官、財官匯債沒有跨年逐季穩定增益；增加匯債後H1年度準確度反而低於財官。第三批loan有部分區辨／排名訊號，options與合併組不支持本輪採用；資料不支持增益不等於已證明來源永遠無用。
- 第四批財官loan沒有穩定勝過財官；H63相對財官IC有探索訊號，但2026 MAE較差，相對共同基準IC區間仍跨0。四批都不支持直接替換正式模型；未做完整算法搜尋或正式selected等價對決。

### 仍待Claude核對／尚未解決

- 逐fold抽樣鍵、同股同日同標籤與重用hash是否完全吻合；既有summary能否由預測與daily_metrics重現。
- estimated可得時間與真實歷史公告時間／修訂版本有何差異；日期假設、季度少、多重比較與事後選組對結果的影響。
- H1官股AUC等細微反向、Brier／MAE／IC取捨是否在結論完整揭露，是否誤將局部改善變成全面改善。
- options兩項缺件使從來源重訓無法依原路徑完成；四路後續授權退役與歷史研究證據如何保留。缺件不應以重抓／重建解決本次只讀交接。
- 2026早已曝光，後批又依前批結果選組；沒有新的未曝光forward驗證，不能稱穩健提升、可交易獲利或已完成全系統正式驗收。

本檔不是投資建議。達成此次只讀查驗後即回報差異與缺證據，不執行正式predict/train、換模、收集、服務或自動審查。

## 11. 可直接貼給 Claude 的驗證提示詞

以下提示詞授權的隔離 audit 僅供後續 Claude 查核；本輪沒有建立該目錄、重算研究指標或執行模型實驗。原驗證計畫仍依第8節執行。

```text
請先閱讀 D:\stock\docs\CLAUDE_MODEL_TRAINING_HANDOFF_20261019.md 與同目錄 CLAUDE_MODEL_TRAINING_EVIDENCE_20261019.json，唯讀驗證四批完整研究；不要重訓、predict、回填、重抓、改收集或正式檔案，不加門檻、不換正式模型。原始資料、原研究與正式程式／模型均唯讀；只可在 report_feature_eval_20261018\claude_audit 下新建唯一隔離目錄，保存驗證腳本、差異表與報告。不預設新資料全部有效入模或沒有前視，必須逐項用證據判定。

1. 追蹤來源→adapter→特徵→X_train／X_predict→feature order，檢查實際非缺值、有效欄、擬合／預測使用證據。不能因有欄名或讀過來源就宣稱進入模型；verified 全缺值等價重用須分列，不算有效新增試驗。
2. 以真正決策時點檢查未來資料：前夜美股在台股翌日開盤前已可得，不應誤判偷看未來；另核對公告／available_time、歷史 PIT 與修訂、rolling 只用當時及之前已可得資料；rolling所需估計參數與scaler／imputer只以訓練資料估計；若有calibrator，只用預測決策時點以前標籤已實現、與當期測試隔離的校準資料，不能使用當期測試標籤。核對各H標籤成熟、purge／時間隔離與train/test交疊；2026已曝光，不可稱獨立holdout。
3. 比較須同股、同日、同成熟標籤、同分母，保留缺值與未成熟預測，核對固定seed、抽樣與參數。確認方向label是y>0、判漲閾值0.5，依日期先算再平均，不以股票列混平均替代。
4. 從現存預測獨立重算accuracy、AUC、Brier、H63 MAE／IC及paired CI，核對單位、有效日／季度數、區塊與比較方向；H1 MAE／IC為N/A，不能補0。核對重用hash與全欄值；區分H63相對財官和相對共同基準，不混用區間。
5. options原object／feature cache缺失，或只有估計公告時點時，對受影響項目列「無法證明／無法完整重現」，不能宣告通過，也不能自行重建。歷史36路與授權後active32差異不直接視為篡改。

逐項回報「通過／不通過／證據不足」，並標明已證明、僅假設、無法證明或無法完整重現；附具體檔案、欄位、程式位置、差異與影響，清楚分開已確認結論及待查問題。不要把回溯改善稱穩健提升或可交易獲利；查核結束即停，不修改模型、資料或門檻。
```
