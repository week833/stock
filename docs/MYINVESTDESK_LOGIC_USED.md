# MyInvestDesk 分析邏輯採用紀錄
版本日期: 2026-08-10
網站: https://myinvestdesk.com/
本地來源規格: D:\\claude code\\twmix\\docs\\RULESET_SPEC.md
## 目的與範圍

本文件供其他 AI 單獨讀取，記錄本次 D-stock 與 MIX 實際借鏡 MyInvestDesk 的分析概念、採用邊界及研究狀態。
只整理網站公開前端可驗證的條件與本地實作映射，不重現、貼錄或推測網站 JavaScript 原始碼，也不把網站訊號視為現行模型輸出。

## 認知標籤

| 標籤 | 定義 |
|---|---|
| `[SITE]` | 可由網站公開前端推知的條件、門檻或顯示語意。 |
| `[ADOPTED]` | 已進入共用資料契約或正式可用欄位。 |
| `[RESEARCH_ONLY]` | 只存在於 challenger、ablation 或研究比較。 |
| `[NOT_ADOPTED]` | 經技術評估後刻意未納入正式預測或排序。 |
| `[MEASURED]` | 有固定資料切點與評估流程，可重現實測。 |
| `[OPEN]` | 證據或覆蓋仍不足，尚未形成正式結論。 |

## 三態語意

`True` 表示該筆資料完整且條件成立；`False` 表示資料完整但條件不成立；`None` 表示必要輸入缺失、歷史窗不足或在 as-of 時點尚不可得。`None` 不得轉成 `False`，也不得補成 0。

## [SITE] 趨勢訊號 (4)

| ID | 精確條件及網站門檻 | 原始方向/語意 |
|---|---|---|
| `MA_TREND_BULLISH` | `close > MA20`、`close > MA60`、`close > MA120`。 | bullish，`+1`。 |
| `TREND_POSITIVE_CONFIRMED` | `close > MA60`、`close > MA120`。 | bullish，`+1`。 |
| `ABOVE_MA120_CONFIRMED` | `close > MA120`。 | bullish，`+1`。 |
| `MA_TREND_BEARISH` | `close < MA20`、`close < MA60`、`close < MA120`。 | bearish，`-1`。 |

## [SITE] 量價訊號 (5)

| ID | 精確條件及網站門檻 | 原始方向/語意 |
|---|---|---|
| `VOLUME_PRICE_BREAKOUT` | `volume >= 1.5 * MA20(volume)` 且 daily return `> 0`。 | bullish，`+1`。 |
| `VOLUME_CONFIRMED` | `volume > 1.0 * MA20(volume)`。 | bullish，`+1`。 |
| `VOLUME_CONTRACTION` | `volume < 0.5 * MA20(volume)`。 | bearish，`-1`。 |
| `PRICE_LEVEL_OVERHEATED` | 60 日位置 `>= 80`。 | bearish，`-1`。 |
| `PRICE_LEVEL_OVERCOLD` | 60 日位置 `<= 20`。 | bullish，`+1`; mean-reversion prior。 |

## [SITE] 波動與回撤訊號 (2)

| ID | 精確條件及網站門檻 | 原始方向/語意 |
|---|---|---|
| `VOLATILITY_HIGH` | 近 20 日「日」報酬波動率 `>= 4.0 pct`。 | bearish，`-1`。 |
| `DRAWDOWN_HIGH` | `abs(近60日最大回撤) >= 20 pct`。 | bearish，`-1`。 |

注意: `VOLATILITY_HIGH` 使用日波動率；不可把年化波動率直接和 `4%` 日門檻比較，必須先轉成相同頻率與單位。

## [SITE] 籌碼訊號 (11)

| ID | 精確條件及網站門檻 | 原始方向/語意 |
|---|---|---|
| `INSTITUTIONAL_NET_BUY` | 近 5 日三大法人合計買賣超 `> 0`。 | bullish，`+1`。 |
| `INSTITUTIONAL_NET_SELL` | 近 5 日三大法人合計買賣超 `< 0`。 | bearish，`-1`。 |
| `INSTITUTIONAL_BUY_STREAK` | 外資連續買超日數 `>= 3`。 | bullish，`+1`。 |
| `INSTITUTIONAL_SELL_STREAK` | 外資連續賣超日數 `>= 3`。 | bearish，`-1`。 |
| `TURNOVER_HEALTHY` | 週轉率 `>= 0.5%` 且 `<= 8%`。 | bullish，`+1`。 |
| `TURNOVER_OVERHEATED` | 週轉率 `> 15%`。 | bearish，`-1`。 |
| `DAY_TRADE_RATIO_HIGH` | 當沖比率 `> 50%`。 | bearish，`-1`。 |
| `HOLDER_CONCENTRATION_SUPPORTIVE` | TDCC 大戶持股比率 `>= 40%`。 | bullish，`+1`。 |
| `HOLDER_CONCENTRATION_LOW` | TDCC 大戶持股比率 `< 15%`。 | bearish，`-1`。 |
| `HOLDER_CONCENTRATION_RISING` | TDCC 大戶持股比率期變化 `>= +0.5 pp`。 | bullish，`+1`。 |
| `HOLDER_CONCENTRATION_FALLING` | TDCC 大戶持股比率期變化 `<= -0.5 pp`。 | bearish，`-1`。 |

最後四條大戶訊號必須使用 TDCC 大戶持股資料；FinMind Shareholding 的外資持股不可替代或冒充 TDCC 大戶持股。

## [SITE] 基本面與估值訊號 (14)

| ID | 精確條件及網站門檻 | 原始方向語意 |
|---|---|---|
| `EPS_POSITIVE` | `EPS > 0`。 | bullish，`+1`。 |
| `EPS_NEGATIVE` | `EPS < 0`。 | bearish，`-1`。 |
| `EPS_GROWTH` | EPS 年增率 `>= 10%`。 | bullish，`+1`。 |
| `EPS_DECLINE` | EPS 年增率 `<= -20%`。 | bearish，`-1`。 |
| `REVENUE_GROWTH` | 月營收年增率 `>= 10%`。 | bullish，`+1`。 |
| `REVENUE_DECLINE` | 月營收年增率 `<= -10%`。 | bearish，`-1`。 |
| `REVENUE_MOMENTUM` | 月營收月增率 `>= 10%`。 | bullish，`+1`。 |
| `REVENUE_MOMENTUM_WEAK` | 月營收月增率 `<= -15%`。 | bearish，`-1`。 |
| `ROE_STRONG` | `ROE >= 15%`。 | bullish，`+1`。 |
| `ROE_WEAK` | `ROE < 5%`。 | bearish，`-1`。 |
| `PROFITABILITY_HEALTHY` | 毛利率 `> 0` 且營益率 `>= 10%`。 | bullish，`+1`。 |
| `OPERATING_MARGIN_NEGATIVE` | 營益率 `< 0`。 | bearish，`-1`。 |
| `VALUATION_REASONABLE` | `8 <= PE <= 25`。 | bullish，`+1`。 |
| `VALUATION_EXPENSIVE` | `PE >= 50`。 | bearish，`-1`。 |

`[OPEN]` 原網站面板若缺 EPS 絕對值而使用淨利率正負代理，不得將代理結果視為真正的 EPS 正負；本地程式不可沿用此代理。

## [NOT_ADOPTED] 網站原始總分與分級

網站原始公式為 `factor_score = bullish_count - bearish_count`，其中 `bullish_count` 與 `bearish_count` 只計入已評估且成立的對應方向訊號。

| 分級條件 | 網站狀態 |
|---|---|
| `evaluated < 6` | `DATA_UNAVAILABLE` |
| `factor_score <= -3` | `STRONG_REDUCE` |
| `factor_score <= -1` | `REDUCE` |
| `factor_score >= 3` | `ADD_SMALL` |
| 其餘，即 `0`、`+1`、`+2` | `NO_ACTION` |

這是網站規則本質，不是目前 D-stock 或 MIX 的投資評分、預測報酬、上漲機率或排名。本次未建立 36 項等權投資評分，未用網站狀態取代原模型，也未把具名訊號硬編碼成目前系統的交易方向。未採用原因是等權二元訊號無法替代已依 PIT/OOS 流程驗證的連續模型特徵。

### [SITE] 已知限制

- `RULESET_SPEC.md` 逆讀網站面板後確認，面板的 `MA120` 實際以 `close/(1+ret_120)` 反推；所得是 120 日前收盤價，不是 120 日移動平均。本地契約已改用真實 `SMA120`。
- `MA_TREND_BULLISH`、`TREND_POSITIVE_CONFIRMED`、`ABOVE_MA120_CONFIRMED` 在數學上巢狀；最強條件成立時會同時得到 `+3`。無法確認網站是否刻意放大此方向，因此本地不照搬。
- `TURNOVER_OVERHEATED` 的觸發率僅 `0.2%`，顯示既有門檻近乎不作用。
- 四條 TDCC 大戶訊號在當時沒有可用資料，不能據此宣稱已完成大戶因子驗證。
- EPS 正負若由淨利率正負代理，會改變原始欄位本義；此問題已在 `[OPEN]` 段落說明。

## [ADOPTED] D-stock 技術欄位映射

下列定義是 D-stock 共用資料契約中的可重現技術欄位；所有窗口均依交易日排序，必要歷史不足或分母無效時回傳 `None`。

| 欄位 | 可重現定義 |
|---|---|
| `sma_20`、`sma_60`、`sma_120` | 分別為 `close` 的真實 20/60/120 日 rolling mean；各窗口歷史不足為 `None`，不得互相代理。 |
| `sma_20_slope_percent_per_day`、`sma_60_slope_percent_per_day`、`sma_120_slope_percent_per_day` | 各 SMA 序列取最後 `min(5,len(SMA序列))` 點，對 `log(SMA)` 以 `x=0..n-1` 做 OLS；`(exp(beta)-1)*100`，單位為每日百分比。 |
| `sma_alignment` | `sma_20>sma_60>sma_120` 為 `bullish`；反向為 `bearish`；其他為 `mixed`；必要 SMA 缺失則為 `None`。 |
| `position_60d_percent` | 近 60 日 `close` 的 `(close-low60)/(high60-low60)*100`；分母為 0 或歷史不足時為 `None`。 |
| `breakout_20d_percent`、`breakout_trigger` | `prior_high20=max(high)`，取排除當日的前 20 日；前者為 `(close/prior_high20-1)*100`，後者為 `close>prior_high20`。 |
| `breakout_volume_ratio_20d` | `當日 volume / 前20日正值 volume 的平均`；前 20 日排除當日，分母無效時為 `None`。 |
| `volume_contraction_ratio_5d_to_20d`、`volume_contraction_trigger` | `最近5日正值量均值/再前20日正值量均值`；兩段不重疊，比值 `<0.8` 時 trigger 為 `True`。 |
| `support_price_20d`、`support_price_60d` | 排除當日，分別取前 20/60 日 `close` 的最小值；不是 `low` 欄位。 |
| `resistance_price_20d`、`resistance_price_60d` | 排除當日，分別取前 20/60 日 `close` 的最大值；不是 `high` 欄位。 |
| `pullback_depth_20d_percent` | 近 20 日 `close` 窗口取 `peak=max(close)`，計算 `(close/peak-1)*100`。 |
| `pullback_recovery_20d_percent` | 峰值日起至當日取最低 `close` 為 trough，計算 `(close/trough-1)*100`；峰值若在當日、沒有後續區間時為 `None`。 |
| `volatility_20d_percent`、`volatility_60d_percent` | 近 20/60 日的日報酬樣本標準差乘 100，單位為每日百分比，不年化。 |
| `drawdown_20d_percent`、`drawdown_60d_percent` | `(close/max(close窗口)-1)*100`；窗口分別為 20/60 日。 |
| `rsi_14d` | 依 14 日上漲與下跌幅度計算 RSI，範圍 `0..100`；歷史不足為 `None`。 |

| 候選標記 | 必須同時成立的條件 |
|---|---|
| `oversold_mean_reversion_candidate` | `rsi_14d<=30`、`position_60d_percent<=25`、`drawdown_20d_percent<=-5` 必須同時成立。 |
| `weak_trend_continuation_candidate` | `close<sma_20`、`sma_20_slope_percent_per_day<0`、`drawdown_20d_percent<=-3` 必須同時成立。 |

這些欄位用於診斷、研究候選與連續模型特徵，不合併成網站式投資總分。任何 `None` 均保留缺失語意，不補 0。

## [ADOPTED] MIX 技術特徵映射

以下名稱與公式依 `D:\MIX All\src\mixstock\features.py`；比例欄位以小數保存，未額外乘 100。

| 欄位 | 程式中的實際定義 |
|---|---|
| `ema_distance_{5,10,20,60,120}` | `close/EMA(span=n, adjust=False)-1`；`min_periods=max(3,n//2)`。 |
| `sma_distance_{20,60,120}` | `close/SMA_n-1`；SMA 必須滿足完整 `n` 日窗口。 |
| `sma_slope_{20,60,120}` | 對 SMA 做 rolling OLS slope，再除以 `close`；斜率窗口分別為 `10/30/60` 日且須完整。 |
| `sma_alignment_bullish`、`sma_alignment_bearish` | 分別為 `SMA20>SMA60>SMA120` 與反向排列的三態 trigger。 |
| `macd_pct`、`macd_signal_pct`、`macd_hist_pct` | `MACD=(EMA12-EMA26)`，signal 為 MACD 的 EMA9；三者分別以 `close` 正規化，hist 為 `(MACD-signal)/close`。 |
| `rsi_14`、`bollinger_z_20` | Wilder-style RSI14 再除以 100；Bollinger z 為 `(close-SMA20)/population_std20`，20 日欄位 `min_periods=10`。 |
| `range_position_20`、`range_position_60` | `(close-rolling_low)/(rolling_high-rolling_low)`；高低取 `high/low`，`min_periods` 分別為 `10/30`。 |
| `breakout_above_{20,60}d_high` | `close > rolling_high.shift(1)` 的三態 trigger，即比較前一日已知的 20/60 日高點。 |
| `support_distance_{20,60}d` | `close/prior_low-1`，其中 `prior_low=rolling_low.shift(1)`。 |
| `resistance_distance_{20,60}d` | `prior_high/close-1`，其中 `prior_high=rolling_high.shift(1)`。 |
| `volume_contraction_20_vs_60` | `avg20d_volume/avg60d_volume-1`；60 日均量 `min_periods=30`。 |
| `breakout_volume_contraction_60` | `breakout_above_60d_high * clip(-volume_contraction_20_vs_60, lower=0)`。 |
| `drawdown_{20,60,120}` | `close/rolling_max(close,n)-1`；`min_periods=max(10,n//2)`。 |
| `trend_strength_20`、`trend_strength_60` | 對 `log(close)` 做 rolling OLS slope；窗口為 20/60，`min_periods` 為 15/30。 |
| `recovery_from_{20,60}d_low` | `close/rolling_low(high-low price window)-1`；實際分母為對應 `low20/low60`。 |
| `oversold_state_14`、`weak_trend_state_20` | 三態 trigger 分別為 `rsi_14<=0.30`、`trend_strength_20<0`。 |
| `interaction_oversold_mean_reversion` | `oversold_state_14 * clip(recovery_from_20d_low, lower=0)`。 |
| `interaction_weak_trend_continuation` | `weak_trend_state_20 * clip(-sma_distance_20, lower=0)`，即弱勢狀態乘低於 SMA20 的距離。 |
| `return_skew_20`、`return_kurt_20` | 日報酬的 20 日 rolling skew/kurt，`min_periods=10`。 |
| `downside_vol_20`、`overnight_vol_20` | 負日報酬的 20 日 population std (`min_periods=5`)；隔夜 `open/prev_close-1` 的 20 日 population std (`min_periods=10`)。 |
| `liquidity_shock` | `volume_ratio_20 * abs(intraday_return)`，其中 `intraday_return=close/open-1`。 |

這些是方向中性的連續候選欄位與獨立 interaction，由模型自行學習權重，並非網站 36 條訊號的等權加總。特徵已可產生不代表每個欄位都已被正式模型選用；正式採用仍須通過 PIT/OOS promotion gate。

## [ADOPTED] 資料時點與缺值契約

所有決策列以 `08:30 Asia/Taipei` 為可得資料切點；交易日 T 只能使用在 T 日 08:30 前已公開且可取得的資料，否則最多使用 T-1 或更早資料。

| 契約項目 | 必須遵守的語意 |
|---|---|
| 月營收與財報 | 依實際公布時間或資料源可得時間進入特徵，不得按營收月份或財報所屬期間向前倒灌。 |
| 缺值 | `NaN`、正負 `inf`、缺欄位或當時不可得均保留為 `None`，不得補 0。 |
| PER | FinMind `PER=0` 視為比率未定義或缺失，不視為零倍本益比。 |
| 殖利率 | `dividend_yield=0` 可以是真實有效值，不得比照 PER 改成缺失。 |
| 持股資料 | `TaiwanStockShareholding` 是外資持股資料，不得替代或冒充 TDCC 大戶持股。 |
| 交易限制 | 停牌、停止交易、處置等限制依各決策日的 as-of 狀態判斷，符合阻擋條件時可排除交易。 |
| 當沖限制 | 僅限制當沖不等於禁止一般買進或持有，不得因此直接阻擋持有候選。 |

此契約防止 look-ahead: 每一列特徵只能反映當時真實可知資訊，不能讓後續公告、修訂值或今日限制狀態污染歷史決策。

## [OPEN] 外部資料覆蓋限制

指定證據 `D:\stock\data\expected_return_model\research\pit_families_20260809\pit_feature_families.json` 記錄如下；覆蓋不足表示無法公平做同池比較，不表示缺失值等於中性。

| 系統 | 實測覆蓋與閘門 | 本次處理 |
|---|---|---|
| D-stock | 全市場 `2239` 檔、`30` 個 origin days；外部家族可用股票 `30/2239=1.34%`，低於全市場股票覆蓋 `50%` 門檻。 | `institutional_activity`、`fundamental`、`full` 均因 `stock_coverage_below_50_percent_of_full_universe` 跳過；`technical` 與技術 ablation 保留。 |
| MIX | 外部研究欄位採每檔 `>=80%` 覆蓋門檻；當次法人與基本面欄位約 `0%-1.5%`。 | 法人/基本面研究家族跳過或停用且不補 0；技術欄位仍可進研究候選。 |

外部家族在資料回補並重新通過 PIT 覆蓋閘門前維持 `[OPEN]`，不得因少數股票有值就推廣到全市場模型。

## [NOT_ADOPTED] 預測與排序責任邊界

- 網站規則只提供可解釋 feature 與 reason code，不直接提供目前系統的預測 OHLC、最終收盤漲幅、上漲機率或正式排名。
- D-stock 與 MIX 的既有模型仍是預測及排序的唯一責任線；網站規則不得覆寫其預測值或候選順序。
- 交易成本只用於回測的淨績效診斷，不用來扣減使用者看到的推薦分數或排名。
- 網站單日名單及任何帶日期的推薦名單不得作為模型輸入，以免形成標籤洩漏、循環引用或不可重現依賴。

## [MEASURED] D-stock 隔離回測結果

來源: `D:\stock\data\expected_return_model\research\pit_families_20260809\pit_feature_families.json`。

| 實測範圍 | 原檔紀錄 |
|---|---|
| 股票與日期 | 全市場 `2239` 檔，`30` 個 origin trading days。 |
| 評估列 | `65536` eligible decision rows。 |
| PIT 稽核 | `cutoff_violations=0`，且逐列傳入 `08:30 Asia/Taipei` cutoff。 |
| 隔離切分 | fit `15` 日、inner selection `5` 日、untouched outer evaluation `10` 日；outer 未參與候選選擇。 |

| Untouched outer 指標 | incumbent | technical challenger |
|---|---:|---:|
| Direction accuracy | `55.35%` | `52.16%` |
| Brier | `0.249910` | `0.253811` |
| RankIC | `0.219709` | `0.020297` |
| Top10 gross | `2.1889%` | `0.0462%` |
| Top10 excess | `2.2795%` | `0.1368%` |
| MAE | `2.5338%` | `2.6034%` |

`technical_nested_sma_ablation` 的 untouched outer direction accuracy 為 `52.61%`，Top10 gross 為 `-0.4505%`。

技術候選雖在 inner 勝出，卻未在 untouched outer 勝過 incumbent；因此 `formal_model_unchanged=true`、`upgrade_eligible=false`，本次不 promotion。

## [MEASURED] MIX 隔離回測結果

來源: `D:\MIX All\research\factor_eval_20260809\factor_eval_20260809T014500Z\summary.json`。設計為 `3 groups x 2 periods x 4 horizons = 24` 組；每個 outer block 為 `252` sessions，inner embargo 為 `60` sessions。`listed_equity` 與 `emerging_equity` 分開評估，`etf_etn` 僅供 diagnostic。

| Full panel group | Rows | Codes | 使用邊界 |
|---|---:|---:|---|
| `listed_equity` | `301586` | `1960` | 正式股票研究池之一 |
| `emerging_equity` | `172872` | `270` | 與上市分開評估 |
| `etf_etn` | `7852` | `8` | diagnostic-only |

| Primary listed horizon | 該研究分組 winner |
|---:|---|
| `1` | `incumbent_price_volume_baseline` |
| `5` | `incumbent_price_volume_baseline` |
| `20` | `new_technical` |
| `60` | `incumbent_price_volume_baseline` |

| Primary listed h20 `new_technical` outer 指標 | Direction | Brier | RankIC | Top10 gross | Top10 excess | Top10 net | Top10 hit |
|---|---:|---:|---:|---:|---:|---:|---:|
| 實測值 | `47.64%` | `0.251293` | `0.009164` | `0.3258%` | `1.0623%` | `-0.4592%` | `43.77%` |

paired live sessions 為 `0 < 60`，且 outer `252 < 504` sessions promotion gate；h20 `new_technical` 即使被該研究分組選為 winner，也不等於正式升級。結論為 Champion 未升級、`formal_registry_modified=false`，正式 registry 保持不變。

## 採用狀態總表

| 狀態 | 內容 | 理由 |
|---|---|---|
| `[ADOPTED]` | D-stock 技術資料契約 | SMA、slope、排列、位置、突破、量比、支撐阻力、回檔、波動、drawdown、RSI 與候選旗標已有可重現公式及缺值語意。 |
| `[ADOPTED]` | MIX 技術特徵產生器 | 已能產生方向中性的連續特徵與 interaction，權重交由模型學習。 |
| `[ADOPTED]` | PIT、as-of 與 `None` 契約 | 固定 08:30 切點，公告依可得時間進場，缺失不補 0，可防止 look-ahead。 |
| `[RESEARCH_ONLY]` | D-stock 技術 family 與各項 ablation | inner 曾選中候選，但 untouched outer 未勝，未達 promotion。 |
| `[RESEARCH_ONLY]` | MIX `new_technical` | 可在個別研究分組成為 winner，但 paired live 與驗證期門檻未通過。 |
| `[RESEARCH_ONLY]` | 法人、籌碼與基本面外部家族 | 當次覆蓋過低，只能保留研究定義或停用狀態，不得進正式候選比較。 |
| `[NOT_ADOPTED]` | 以網站 36 條等權總分與分級取代模型 | 二元等權規則不能取代經 PIT/OOS 評估的連續預測與排名模型。 |
| `[NOT_ADOPTED]` | 以淨利率正負代理 EPS 正負 | 代理量不是 EPS 絕對值，會改變欄位本義。 |
| `[NOT_ADOPTED]` | 以外資持股冒充 TDCC 大戶持股 | 資料對象與經濟語意不同。 |
| `[NOT_ADOPTED]` | 對低覆蓋或不可得欄位補 0 | 0 可能是真值，且補值會把未知錯寫成中性或無活動。 |
| `[NOT_ADOPTED]` | 將網站單日名單或日期化推薦名單當模型輸入 | 會造成不可重現依賴、循環引用或標籤洩漏。 |

## 提供其他 AI 的研究提案格式

每次改進提案必須逐項交代下列內容，缺一不可:

| 必填項目 | 最低可接受內容 |
|---|---|
| Source 與欄位 | 資料提供者、dataset/API/檔案、原始欄位、轉換欄位及版本。 |
| 時點 | `event_time`、`available_time`、決策 `as_of` 與時區，並說明何時才可供模型使用。 |
| Universe | 市場、證券類型、納入排除規則、股票數與是否使用完整同池。 |
| 定義 | 頻率、單位、rolling window、分母、年化與否，以及所有門檻公式。 |
| 三態與缺值 | `True/False/None` 的個別語意、歷史不足處理、0 是否有效值及禁止零填的範圍。 |
| Coverage | 逐欄位、逐股票、逐日期的有效覆蓋，以及進入研究前必須達到的 gate。 |
| PIT audit | cutoff violations、公告延遲、修訂值、跨期 join 與 leakage 檢查結果。 |
| 比較設計 | baseline、challenger、單一 family 的獨立 ablation，以及固定資料池和固定模型條件。 |
| 隔離評估 | inner selection、purge/embargo 與 untouched outer；outer 不得回流調參。 |
| 指標 | 至少報告 RankIC、Brier、MAE、Top10 hit、gross、excess、net，並附樣本列數與交易日數。 |
| Promotion | 逐條列 promotion gate 是否通過，及 formal model/registry unchanged 或正式升級的可驗證證據。 |

拒絕只有股票名單、只觀察單日觸發率、未聲明資料時點與缺值語意，或把回測交易成本扣進使用者可見正式排名的提案。

## 可追溯來源與執行入口

| 用途 | 本地路徑 |
|---|---|
| 網站規則整理規格 | `D:\claude code\twmix\docs\RULESET_SPEC.md` |
| D-stock PIT 原始資料契約（歷史／已退役） | `D:\stock\programs\full_market_analysis\tw_pit_raw_data_contract.py`（已隔離） |
| D-stock challenger 研究程式（歷史／已退役） | `D:\stock\programs\full_market_analysis\RESEARCH_CHALLENGER.py`（已隔離） |
| D-stock PIT family 實測證據 | `D:\stock\data\expected_return_model\research\pit_families_20260809\pit_feature_families.json` |
| D-stock 正式執行入口（歷史／已退役） | `D:\stock\programs\full_market_analysis\RUN_STOCK_ANALYSIS.cmd`（已隔離；現行全市場入口為 MIX） |
| MIX 技術特徵程式 | `D:\MIX All\src\mixstock\features.py` |
| MIX FinMind 特徵與 PIT 處理 | `D:\MIX All\src\mixstock\finmind_features.py` |
| MIX sector research 設定 | `D:\MIX All\config\mix_config_sector_research.json` |
| MIX factor evaluation 實測摘要 | `D:\MIX All\research\factor_eval_20260809\factor_eval_20260809T014500Z\summary.json` |
| MIX 正式執行入口 | `D:\MIX All\RUN_DAILY.cmd` |

上述項目是規格、程式、研究證據或正式執行入口；任何帶日期的推薦結果、網站單日名單或 dated recommendation name 都不是模型輸入。

## 給下一個 AI 的一句話結論

本次真正採用的是可重現、PIT-safe、保留缺值的技術與資料契約；網站總分與交易方向未合併，研究候選未通過 untouched outer/promotion gate，因此正式模型不變。
