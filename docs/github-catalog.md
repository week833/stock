# GitHub 專案分類總覽

查核日期：2026-10-04

week833 的完整 Skill 與專案分類索引已移到 **[skills-index](https://github.com/week833/skills-index)**。本頁列出相同的分類，方便在 stock 內查看。帳號共有 7 個公開儲存庫。

## 📈 股票與交易

### Skill

| Skill | 用途 | 來源 |
|---|---|---|
| `finmind-twstock-research` | **用 FinMind 資料研究台股**：選資料集、規劃查詢、檢查資料品質與時間點一致性（PIT） | [finmind-twstock-research-skill](https://github.com/week833/finmind-twstock-research-skill/tree/main/skills/finmind-twstock-research) |
| `ai-trader` | **交易訊號平台主 Skill**：發布交易訊號、追蹤交易者 | [ai-trader/skills/ai4trade](https://github.com/week833/ai-trader/tree/main/skills/ai4trade) |
| `ai-trader-copytrade` | **跟單**：追蹤高手並自動複製其部位 | [ai-trader/skills/copytrade](https://github.com/week833/ai-trader/tree/main/skills/copytrade) |
| `ai-trader-tradesync` | **同步自己的部位與交易紀錄**給追蹤者 | [ai-trader/skills/tradesync](https://github.com/week833/ai-trader/tree/main/skills/tradesync) |
| `ai-trader-heartbeat` | **定期輪詢平台通知與任務**（心跳機制） | [ai-trader/skills/heartbeat](https://github.com/week833/ai-trader/tree/main/skills/heartbeat) |
| `market-intel` | **唯讀市場情報**：財經事件快照與分組新聞 | [ai-trader/skills/market-intel](https://github.com/week833/ai-trader/tree/main/skills/market-intel) |
| `polymarket-public-data` | **讀取 Polymarket 預測市場**公開資料與訂單簿價格 | [ai-trader/skills/polymarket](https://github.com/week833/ai-trader/tree/main/skills/polymarket) |

### 儲存庫

| 專案 | 用途 | 型態 |
|---|---|---|
| [stock](https://github.com/week833/stock) | **一鍵安裝／修復 Windows 股票研究環境**，並整理外部股票分析來源索引；不是完整股票預測應用 | 工具／安裝包 |
| [finmind-twstock-research-skill](https://github.com/week833/finmind-twstock-research-skill) | **FinMind 台股研究 Skill**；不是正式行情或下單系統 | Skill |
| [ai-trader](https://github.com/week833/ai-trader) | **AI 代理交易平台**（內含上述 6 個交易 Skill）；不是台股專用 | Fork（上游 [HKUDS/AI-Trader](https://github.com/HKUDS/AI-Trader)） |
| [stock-mitake-ui-reference](https://github.com/week833/stock-mitake-ui-reference) | **三竹股市介面導航與操作安全規範**；不是 API、不下單 | 參考文件 |

股票外部來源、歷史文件與待評估刪除候選，集中見 **[股票來源與歷史資料待評估清單](source-review.md)**。`stock` 內的 36 個外部來源是本庫維護的來源索引，不是帳號另外擁有的 36 個儲存庫。

## 📊 簡報製作

| 專案 | 用途 | 型態 |
|---|---|---|
| [ppt-master](https://github.com/week833/ppt-master) | **`ppt-master` Skill：用 AI 生成可編輯 PPTX 簡報**，支援範本填入、重建與美化既有簡報 | Fork（上游 [hugohe3/ppt-master](https://github.com/hugohe3/ppt-master)） |

## 🖥️ Windows 桌面自動化

| 專案 | 用途 | 型態 |
|---|---|---|
| [windows-local-action-recorder](https://github.com/week833/windows-local-action-recorder) | **錄製 Windows 本機操作並安全重播**，整理成 Codex 可學習的工作流程 | 工具（非 Skill） |

## 🗂️ 索引

| 專案 | 用途 | 型態 |
|---|---|---|
| [skills-index](https://github.com/week833/skills-index) | **全部 Skill 與儲存庫的功能分類索引**（原 `week833/week833`） | 文件 |

## 副本保留待評估

| 專案 | 已確認的證據（查核日期 2026-09-18） | 待評估事項 |
|---|---|---|
| [ai-trader](https://github.com/week833/ai-trader) | 查核時 Fork 僅有 `main` 分支，與上游 `main` 同為 `d03ff6c056b32ced735adf7c19ed8175adb1c8df`，最後提交為 2026-06-11。 | 評估是否仍需保留 Fork，或以直接連結上游作研究參考；尚未確認部署或其他工作流程是否依賴此副本。 |

證據：[Fork 分支清單](https://api.github.com/repos/week833/ai-trader/branches?per_page=100)、[Fork 提交](https://github.com/week833/ai-trader/commit/d03ff6c056b32ced735adf7c19ed8175adb1c8df)、[上游提交](https://github.com/HKUDS/AI-Trader/commit/d03ff6c056b32ced735adf7c19ed8175adb1c8df)。

此處評估的是重複副本的保留需求，不代表 AI-Trader 已過時，也不是刪除決定。未僅因提交日期較早、Fork 身分或分支名稱，把專案判為可刪除。
