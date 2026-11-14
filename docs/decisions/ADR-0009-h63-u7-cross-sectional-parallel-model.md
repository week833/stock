# ADR-0009：H63（三個月）改用 U7 全市場排名模型系列，與選定模型並行並取代單股 H63 顯示

- ID：ADR-0009
- 日期：2026-11-02
- 狀態：`accepted`（使用者 2026-11-01 21:20 在對話中回覆「換」，同意下列提案；副本驗收全部通過後於 2026-11-02 部署）
- 範圍：D:\stock\programs\shared（新模組 stock_h63_xs*、RUN_H63_XS_DAILY.cmd、h63_xs_schedule\）；D:\TWStockData\program_data\stock_h63_xs（新資料根）；single_stock_analysis 的 H63 顯示（stock_candidate_report.py、tw_single_stock_c0.py、新 single_stock_h63_xs.py）；Telegram Bot 的 H63 caption 與說明文字
- supersedes：N/A（ADR-0008、C0 契約與 ADR-0002～0007 不變；2026-10-17 選定模型啟用沒有 ADR，其 receipts 不變）
- superseded_by：N/A

## 背景

- 現行 H63 是 2026-10-17 啟用的選定模型 expanded_linear（Logistic C=1＋Ridge α=10，未校準；50 檔、80 個訊號日）。以相同配方在全母體重擬的代理版本（10-25 研究）AUC 0.502，報告卻以「上漲機率／預估報酬」為主表。
- U7（2026-10-04 設計）曾在 10-07 以暫時版本上線，隨 C0 改版退役；舊帳本從未寫入任何紀錄。
- 現行 head 的 finmind_price_adj 在 20220830～20250418 缺 1,118,547 個適用鍵；歷史 head a807be3e 可補，但沒有保留承諾。

使用者核准的提案（2026-11-01）：
1. 現行 H63（選定模型 expanded_linear）改回 U7 全市場排名法。
2. 區間改為 B3＋CQR252。
3. 報告改稱「跑贏同期多數股票的機率」（相對強弱），不再稱「上漲機率」。
4. 建立每日前瞻紀錄，供日後評估。
5. 先補齊價格歷史缺洞。
6. 先在副本建置與驗證，再部署。
7. H1（開盤前版 v2 主顯示）不改。

## 選項

1. 維持 expanded_linear 的 H63 顯示。
2. 部署「H63 誠實化暫存版」：顯示「尚無勝過基準的模型」。
3. 撤回選定模型的 H63，只授權 H1。
4. 新增與選定模型並行的 U7 模型系列 stock_h63_xs（自有指標、快照、前瞻紀錄、歷史補充檔），單股以疊加層取代 H63 顯示。
5. 拆分：H63 改出新的正式報告 schema 與驗證器，不經選定路徑。

## 決定

採用選項 4。

- 模型：配方 u7_b3cqr252_v1（frozen_sha256 a2c4523c38b11315f35b58e96648e4d79e38f39f094acb50a09c338e5983b37e）。7 因子（IVOL60－、MAX5－、52 週高＋、單月營收年增＋、營收對前 11 月高＋、E/P＋、B/M＋；E/P 與 B/M 取前一自身交易日）對合格上市櫃母體排名平均、減當日合格平均，P＝sigmoid(b·Sc)。b 為無截距、每檔等權、每 3 個交易日取樣、套每日閘門的 logistic 斜率，每 63 個交易日例行重擬，須在 0.5～2.0。不給點預測。區間：B3（60／252 日波動 0.5／0.5 混合的 Mondrian 5 桶）＋CQR252 的 80% 名目區間。
- 資料：只讀中央不可變物件，每次 pin 當次 head 並持 reader lease。20211004～20251231 的價格與板別由 U7 自有、以 SHA 釘選的補充檔提供（來源 a807 物件 94712d7a…、27f8091d…；price_hist 0d08f47d…、board_hist aff81147…），接縫 20251231，每檔以接縫前最後 5 個重疊日的收盤比中位數縮放。不修改 stock_cache_lifecycle 或 stock_daily_preopen_data。
- 產製：programs\shared\stock_h63_xs.py（train／predict／settle／review／daily／status／rollback／supplement-build），寫入 D:\TWStockData\program_data\stock_h63_xs。每日快照同時是前瞻紀錄區塊，寫一次、不可覆寫。
- 消費：單股以 single_stock_h63_xs 疊加層在 H63 正式報告寫 PDF 前附加 h63_xs 區塊。選定模型的 H63 機器欄位保留，只供重現驗證，不再顯示。驗證鏈、/all 順序、比較快取交接不變。
- 顯示：主名稱「跑贏同期多數股票的機率」（相對強弱，不是上漲機率），附相對偏強／中性／偏弱、合格母體排名、80% 區間與固定揭露（h63-xs-disclosure-v1）；H63 報告的可見日期一律 YYYYMMDD。快照落後 ≤5 個平日沿用並註記；超過或不可用時只顯示原因、不顯示數字。
- 來源開關：命令列 --h63-source ＞ 環境變數 DSTOCK_SINGLE_H63_SOURCE ＞ 資料根 config\display.json ＞ 預設 auto；值為 auto、xs（QA）、off、legacy（舊行為）。
- 前瞻紀錄：每個決策日記錄全市場；以第 63 個自身價格列結算；以事先登記 v3（PREREGISTERED_REVIEW_V3，sha256 f0afc46b0f6ceb86f572e7d8a1179b1dd490a351bc03e857430dcfceca052356，在第一筆紀錄前凍結）的 63 日區塊判定。
- 排程：平日 07:20 單一用途任務（h63_xs_schedule\），另行經使用者同意才註冊；本 ADR 不註冊排程。
- 治理：首次啟用與換配方須 --authorization（使用者核准原文）；例行重擬依本 ADR。選定模型的 selected_current.json 與 receipts 不變。H1 不變。

## 理由

正式配方（接縫 20251231）在同一組 20261030 輸入上的驗收數字（oracle 與實作逐列一致）：

| 指標（同期 20260109～20260722，126 個訊號日） | U7 | 停用模型（10-25 全母體重擬代理） |
| --- | --- | --- |
| 漲跌方向準確率 | 54.28% | 50.05% |
| AUC（逐日平均） | 0.572 | 0.502 |
| Brier | 0.2467 | 0.2796 |

- 前推 7 個半年區塊（2023H1～2026H1，漲跌方向、依股票平均）：準確率 54.7%（一律猜漲 47.5%）、Brier 0.2465、AUC 0.570、IC 0.148。
- 區間覆蓋：B1 75.7%、B3 76.2%、B3＋CQR252 79.6%（寬 49.5 pp，各半年 75%～87%）。
- 限制：上列數字都在 U7 設計期內；設計後（2026-07，18 個訊號日）AUC 0.532、IC 0.094；漲跌方向準確率隨大盤擺動，任何一季都可能沒有優勢；Brier 約等於常數 0.25。

## 證據

- 可執行規格：u7_reference_oracle.py（sha256 a94609fad5a6851e…）；研究接縫 research32 模式與研究成品逐位元一致。
- 副本驗收（tolerance 1e-9，oracle compare 全部 PASS）：
  - C1 研究接縫、C2 正式接縫：1,230 日總和、3 日全欄、最新日機率與區間、6 個歷史日 B3＋CQR 區間、斜率。
  - A3 現行 head（67a2f93b，資料到 20261030）以正式讀取路徑：1,231 日總和、4 日全欄、最新日、歷史區間、斜率。
  - A4 正式快照對 oracle 最新日 38 欄。
  - 物件串流解析與獨立 json.loads 解析逐欄相同；補充檔由正式程式重建後與驗證版位元組相同。
  - 既有測試：single_stock 787 通過／1 既有失敗（現行版本相同），telegram 393 通過，shared 相關套件通過；新測試 75 個通過。
- 驗收收據與部署收據：D:\TWStockData\program_data\stock_h63_xs\acceptance\、deploy\。

## 後果

- H63 報告不再顯示上漲機率與預估報酬；Telegram 說明文字一併改名（MODE_LABELS 不變，以免改動既有錯誤訊息）。
- H63 仍依賴選定路徑才能產生報告；H1 也是如此。
- 每日需要一次產製（約 1 分鐘、峰值約 3 GB）；沒有排程時，快照落後 5 個平日後 H63 不顯示數字，前瞻紀錄也會缺日。
- 補充檔成為 U7 的必要輸入；a807 物件之後被回收也不影響。
- 常駐 Bot 重啟前，config\display.json 必須維持 legacy（舊模組無法驗證帶 h63_xs 區塊的報告）；重啟後改為 auto。

## 回復方式

- 立即（免重啟）：config\display.json 設為 {"mode": "off"}，H63 只顯示「已暫停」；設為 {"mode": "legacy"}，報告回到改版前（顯示 expanded_linear）。
- 模型：stock_h63_xs.py rollback --to <前一版 sha> --authorization "<原文>"。
- 程式：依 deploy\<UTC>\batch.json 以 before 副本還原（核對 SHA），再重啟 Bot；新檔移到 _retired（不刪除）。
- 排程：REMOVE_H63_XS_SCHEDULE.ps1 -Remove -ConfirmRemove。
- 資料根與帳本保留，不刪除。
