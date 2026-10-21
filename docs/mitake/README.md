# 三竹股市電腦版功能與程式介接目錄

## 結果

2026-07-28 以正在執行的三竹股市電腦版實機盤點（盤點當時執行檔位於 `D:\Downloads\MitakeGU\三竹股市.exe`；[capabilities.json](./capabilities.json) 的 `runtime_path` 已於 2026-10-07 更新為目前位置 `D:\MitakeGU\三竹股市.exe`；`sha256` 與 `observed_at` 欄位沿用 2026-07-28 盤點值，新位置未重新量測）：

- 點擊目前版面全部 **34 個可見頂端導覽／快捷頁籤**。
- 成功開啟並讀取 **24 個子視窗**，點擊錯誤 **0**。
- 未操作帳務、下單、警示、DDE設定或其他會改變帳號／交易狀態的功能。
- 原始截圖及 OCR 只留在本機 `D:\stock\tmp\mitake_ui_inventory_20260728`，不提交 Git；三個校正後的快捷頁證據映射記在同目錄 `inventory_corrections.json`。
- 機器可讀清單見 [capabilities.json](./capabilities.json)。

「全部」只代表本次 1400×900 版面上可見的 34 個頂端項目。被左右箭頭隱藏、需不同帳號／方案、盤中時段或券商登入才出現的子頁，均標為 `snapshot_only`、`schema_ready` 或 `pending`，不冒充已實機驗證。

## 狀態

| 狀態 | 意義 |
|---|---|
| `validated_ui_open` | 本次已點擊並開啟對應視窗，可確認主要可見欄位 |
| `clicked_no_separate_window` | 已點擊，但目前版面未開啟獨立子視窗；通常是導覽分類或選單 |
| `snapshot_only` | 有畫面／OCR 證據，但尚未完成可重播欄位驗證 |
| `schema_ready` | 已定義導覽或欄位，尚未完成本次逐頁蒐集 |
| `pending` | 受版本、權限、時段或隱藏頁影響，尚未驗證 |
| `unsupported` | 畫面明確顯示商品或功能不支援 |

## 本次已實機開啟

| 功能 | 可取得資訊 | 建議程式取得方式 |
|---|---|---|
| 綜合報價 | 即時價、買賣價、五檔、成交明細、個股報價／籌碼／財務／資料 | DDE 或 SDK |
| 權證專家 | 熱門權證、標的、比較、條件查詢、試算器、排行 | SDK |
| 主力追蹤 | 券商分點、交易週期、近五日進出、勝率、損益、報酬率 | SDK；UI 僅作授權證據 |
| 報價列表 | 商品、買賣價、成交、漲跌、幅度、單量、總量、分類 | DDE 或 SDK |
| 大盤走勢／大盤動態 | 指數、分時走勢、成交量值、市場廣度、漲跌停家數 | SDK 或交易所官方資料 |
| 即時走勢／最佳五檔／即時明細 | 分時價量、五檔價量、逐筆時間與成交 | DDE 或 SDK |
| 大單流向／分價統計 | 大小單流向、分價量、內外盤、買賣盤差 | SDK 或以授權逐筆資料重算 |
| 支撐壓力／技術分析／趨勢分析 | 關鍵價位、OHLCV、指標、均線、供應商技術摘要 | SDK 或自行以授權行情計算 |
| 相關商品 | 關聯標的、成交、漲跌與切換商品 | SDK |
| 個股財務 | 月營收、損益、資產負債、現金流、獲利／償債／經營能力 | SDK 或公開資訊觀測站等官方來源 |
| 個股新聞 | 來源、標題、觀看數、顯示時間、最後更新、相關商品 | SDK 或具授權新聞來源 |
| AI智選／選股專家 | 多空因子、AI評分、篩選條件與商品清單 | 供應商授權功能；不得推定未揭露公式 |
| 零股報價 | 盤中／盤後零股、價格、股數、買賣價、明細、內外盤 | DDE 或 SDK |
| 個股籌碼 | 券商分點、法人、主力、資券當沖、集保、董監與股權資料 | SDK、官方來源或經驗證 UI 證據 |
| 個股資料 | 公司資料、資本額、市值、產業、業務、股本、股利、行事曆、法說與轉投資 | SDK 或官方來源 |
| 個股診斷 | 個股概況、河流圖、同業比較、量價區間、估值、財務簡表與排名 | SDK 或自行計算；供應商結論權重預設 0 |
| 進出場指標 | 進出訊號、報酬率、勝率、指標排行與訊號歷史 | 建議以授權行情自行重算 |

頂端另已點擊 `大盤`、`國際金融`、`證券行情`、`期權行情`、`新聞影音`、`申購競拍`、`市場動態`、`ETF專區`、`個人設定`、`更多`；在目前版面未開啟獨立子視窗，故只記為導覽分類，不延伸猜測欄位。

## 子頁覆蓋與既有程式

現有 D-stock 不是通用三竹 API，只自動化經驗證的有限頁面：

| 能力 | 現況 | 模型政策 |
|---|---|---|
| 5日券商分點買方排行 | `validated_ui_open` | 只作觀察清單補充證據 |
| 5日賣方排行 | `snapshot_only` | 不直接進正式模型 |
| 1日買／賣排行 | `schema_ready` | 尚未蒐集 |
| 券商分點買賣超／焦點／明細 | `snapshot_only` | 只作觀察證據 |
| 三大法人進出／持股、主力進出／集中度／股本比 | `snapshot_only` | 不得冒充券商分點條件 |
| 主力追蹤的焦點主力、交易高手、外資券商、官股行庫、常用、搜尋，以及五日進出、投資喜好、喜好標的 | `schema_ready` | 完成 point-in-time 回測前封鎖 |
| 個股新聞、趨勢分析、個股診斷 | `validated_ui_open` | 只進報告與校正歷史，直接預測權重固定為 0 |

可重用介面（模組位於本機 `D:\stock\programs\full_market_analysis\`，見 [capabilities.json](./capabilities.json)；不在本 repository 內）：

```python
from tw_mitake_broker_screen import (
    capability_manifest,
    collect_mitake_screen_results,
)
from tw_mitake_daily_watchlist import synchronize_ranked_daily_group

# 純讀取能力定義，不操作 UI。
catalog = capability_manifest()

# collect_mitake_screen_results(...) 只查詢已通過前三項技術條件的候選股，
# 且必須提供 expected_date、cache_dir 與明確 enabled 的 config。
#
# synchronize_ranked_daily_group(...) 只用於正式報告順序的「每日」群組同步；
# 它不是行情資料 API，呼叫前仍須通過正式成品驗證。
```

不要讓其他程式依賴 `_MitakeUi` 等私有座標方法。正式機器介接優先順序為：

1. 三竹 SDK：需另行申請方案、憑證與交易資訊授權。
2. DDE：在 `個人設定 → DDE設定` 由介面複製指令或輸出 Excel；上限以目前授權版本手冊為準。
3. 交易所／櫃買／公開資訊觀測站等官方資料：可重建的欄位優先使用正式來源。
4. UI／OCR：只保留看得見且可追溯的補充證據，失敗時維持 `pending`、`unsupported` 或 `verified_no_hit`。

## 不可跨越的界線

- 三竹桌面畫面不是無授權公開 HTTP API；不抓包、不攔截傳輸、不反向工程私人端點。
- Git 只保存本文件、欄位字典、介面定義與 mock；不保存真實行情截圖、OCR 原文、憑證、帳號、Token 或 DDE／SDK 密鑰。
- 不操作帳務與下單；券商下單 API 與行情 SDK 是不同權限。
- 新資料或公式要進正式推薦，仍須完成授權／安全審查、隔離測試、point-in-time、walk-forward／OOS 效益驗證。

## 來源

- [三竹股市電腦版官方功能頁](https://marketing.mitake.com.tw/apstock/)
- [三竹股市電腦版官方手冊 v1.4](https://www.mitake.com.tw/files/Mitake_Stock_App_User_Manual_v1.4.pdf)
- [三竹 SDK 文件](https://sdk.mitake.com.tw/docs.html)
- [致和證券 2026-05 三竹股市 AP 操作手冊（DDE 章節）](https://www.wintan.com.tw/downloads/SunWin_1.0_202605.pdf)
- [臺灣證券交易所交易資訊使用規範](https://www.twse.com.tw/zh/products/information/use.html)

本文件只描述功能、欄位、可重用介面與驗證狀態，不授予任何資料再散布或商業使用權。
