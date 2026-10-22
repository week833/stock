# GitHub 專案分類總覽

查核日期：2026-10-21

week833 的完整 Skill 與專案分類索引已移到 **[skills-index](https://github.com/week833/skills-index)**。本頁列出相同的分類，方便在 stock 內查看。帳號共有 6 個公開儲存庫。

## 📈 股票與交易

### Skill

| Skill | 用途 | 來源 |
|---|---|---|
| `finmind-twstock-research` | **用 FinMind 資料研究台股**：選資料集、規劃查詢、檢查資料品質與時間點一致性（PIT）；非 FinMind 官方 Skill，只供研究 | [finmind-twstock-research-skill](https://github.com/week833/finmind-twstock-research-skill/tree/main/skills/finmind-twstock-research) |
| `ai-trader` | **交易訊號平台主 Skill**：發布交易訊號、追蹤交易者 | [HKUDS/AI-Trader/skills/ai4trade](https://github.com/HKUDS/AI-Trader/tree/main/skills/ai4trade) |
| `ai-trader-copytrade` | **跟單**：追蹤高手並自動複製其（模擬）部位 | [HKUDS/AI-Trader/skills/copytrade](https://github.com/HKUDS/AI-Trader/tree/main/skills/copytrade) |
| `ai-trader-tradesync` | **同步自己的部位與交易紀錄**給追蹤者 | [HKUDS/AI-Trader/skills/tradesync](https://github.com/HKUDS/AI-Trader/tree/main/skills/tradesync) |
| `ai-trader-heartbeat` | **定期輪詢平台通知與任務**（心跳機制） | [HKUDS/AI-Trader/skills/heartbeat](https://github.com/HKUDS/AI-Trader/tree/main/skills/heartbeat) |
| `market-intel` | **唯讀市場情報**：財經事件快照與分組新聞 | [HKUDS/AI-Trader/skills/market-intel](https://github.com/HKUDS/AI-Trader/tree/main/skills/market-intel) |
| `polymarket-public-data` | **讀取 Polymarket 預測市場**公開資料與訂單簿價格（直接查 Polymarket；發布模擬交易時才經 AI-Trader） | [HKUDS/AI-Trader/skills/polymarket](https://github.com/HKUDS/AI-Trader/tree/main/skills/polymarket) |

> ⚠️ 6 個 AI-Trader Skill 來自第三方專案 HKUDS/AI-Trader，配合第三方平台 [ai4trade.ai](https://ai4trade.ai) 使用：**只支援美股、加密貨幣與 Polymarket，不支援台股**，交易使用平台的**模擬資金**。其中 `ai-trader`、`ai-trader-copytrade`、`ai-trader-tradesync`、`ai-trader-heartbeat` 需註冊取得 token；`market-intel` 只唯讀讀取平台快照；`polymarket-public-data` 直接讀 Polymarket 公開 API，只有要把模擬交易發布到 AI-Trader 時才會用到平台。`ai-trader-tradesync` 上傳的部位與交易紀錄會分享給平台上的追蹤者。研究台股請用 `finmind-twstock-research`。

> ⚠️ `finmind-twstock-research` 是 week833 自行整理的**非官方** Skill，與 FinMind 沒有隸屬或背書關係；要連 FinMind API 必須先經使用者明確同意並安全提供 token；**只供研究，不下單**。

### 儲存庫

| 專案 | 用途 | 型態 |
|---|---|---|
| [stock](https://github.com/week833/stock) | **一鍵安裝／修復 Windows 股票研究環境**（`D:\stock\GitHub`），整理 39 個可選外部研究來源（37 primary＋2 legacy），預設下載 3 個 core；另附 AI 語意動作入口 `AI_STOCK_ACTIONS.cmd`（非 Skill）；不是完整股票預測應用 | 工具／安裝包 |
| [finmind-twstock-research-skill](https://github.com/week833/finmind-twstock-research-skill) | **FinMind 台股研究 Skill**（week833 自行整理，非 FinMind 官方）；不是正式行情或下單系統 | Skill |
| [stock-mitake-ui-reference](https://github.com/week833/stock-mitake-ui-reference) | **三竹股市介面導航與操作安全規範**（公開包 `2.1.0`，82 項功能語意投影）；不是 API、不下單 | 參考文件 |

6 個 AI-Trader Skill 位於外部上游 [HKUDS/AI-Trader](https://github.com/HKUDS/AI-Trader)（不屬於本帳號）；本帳號原有的 Fork 已於 2026-10-22 刪除，見下方「副本處理紀錄」。

股票外部來源、歷史文件與待評估刪除候選，集中見 **[股票來源與歷史資料待評估清單](source-review.md)**（該頁評估內容為 2026-09-18 查證；2026-10-21 已複查來源數量、路徑與部分上游分支 HEAD，2026-10-07 同步進本 repository 的 `stocktw` 尚未評估）。目前 `scripts/sources/source_manifest_v1.json` 列出 39 個可選外部研究來源（37 primary＋2 legacy），這是本庫維護的來源索引，不是帳號另外擁有的 39 個儲存庫。

## 📊 簡報製作

| 專案 | 用途 | 型態 |
|---|---|---|
| [ppt-master](https://github.com/week833/ppt-master)（Skill：[skills/ppt-master](https://github.com/week833/ppt-master/tree/main/skills/ppt-master)） | **`ppt-master` Skill：用 AI 生成可編輯 PPTX 簡報**，支援範本填入、重建與美化既有簡報；本 Fork 沒有自有修改，`main` 已於 2026-10-21 快轉同步至上游 `main`（`604ecde`，`SKILL.md` 版本 `6.7.2`；`skills/ppt-master` 共 13,025 個檔案、約 37 MiB，其中 `templates/` 12,450 個）；Fork 的 `v6` 分支也已同步到同一個提交 | Fork（上游 [hugohe3/ppt-master](https://github.com/hugohe3/ppt-master)） |

## 🖥️ Windows 桌面自動化

| 專案 | 用途 | 型態 |
|---|---|---|
| [windows-local-action-recorder](https://github.com/week833/windows-local-action-recorder) | **錄製 Windows 本機操作並安全重播**，整理成 Codex 可學習的工作流程 | 工具（非 Skill） |

## 🗂️ 索引

| 專案 | 用途 | 型態 |
|---|---|---|
| [skills-index](https://github.com/week833/skills-index) | **全部 Skill 與儲存庫的功能分類索引**（原 `week833/week833`） | 文件 |

## 副本處理紀錄

| 專案 | 處理結果 | 依據 |
|---|---|---|
| `week833/ai-trader`（Fork） | 2026-10-22 已刪除；6 個 AI-Trader Skill 的來源改為直接連到上游 [HKUDS/AI-Trader](https://github.com/HKUDS/AI-Trader)。 | 刪除前 Fork 只有 `main` 分支，與上游 `main` 同為 [`d03ff6c`](https://github.com/HKUDS/AI-Trader/commit/d03ff6c056b32ced735adf7c19ed8175adb1c8df)（2026-06-11），沒有自有提交、tag、PR、issue 或 Actions 紀錄；本 repository 的程式與安裝腳本都沒有引用它。 |

刪除的是重複副本，不代表 AI-Trader 已過時。
