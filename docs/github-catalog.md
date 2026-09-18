# GitHub 專案分類總覽

查核日期：2026-09-18

依主要用途集中列出 week833 的 6 個公開儲存庫，並區分研究工具、Skill、Fork 與參考文件。Fork 表示來源關係；是否過時或是否保留副本，另依具體證據評估。

## 金融與量化研究

| 專案 | 用途與型態 | 整理狀態 |
|---|---|---|
| [stock](https://github.com/week833/stock) | Windows 股票研究環境的安裝、設定、修復與外部來源整理工具；不是完整股票預測應用。 | 保留使用；歷史文件與外部來源另列待評估。 |
| [finmind-twstock-research-skill](https://github.com/week833/finmind-twstock-research-skill) | Codex 台股研究 Skill，提供 FinMind 資料路由、查詢規劃、資料品質與時間點一致性（PIT）研究規則。 | 保留 Skill／研究參考；不是正式行情或下單系統。 |
| [ai-trader](https://github.com/week833/ai-trader) | [HKUDS/AI-Trader](https://github.com/HKUDS/AI-Trader) 的研究 Fork，涵蓋 AI agent 交易平台與模擬交易；不是台股專用。 | 研究參考；交叉列入下方「副本保留待評估」。 |
| [stock-mitake-ui-reference](https://github.com/week833/stock-mitake-ui-reference) | 三竹股市介面導航、操作合約與去識別化觀察參考包。 | 保留參考；未見被替代的證據，不列為已過時。 |

用途依據：[stock README](../README.md)、[FinMind Skill README](https://github.com/week833/finmind-twstock-research-skill/blob/main/README.md)、[AI-Trader README](https://github.com/week833/ai-trader/blob/main/README.md)、[三竹參考包 README](https://github.com/week833/stock-mitake-ui-reference/blob/codex/mitake-ui-reference-initial/README.md)。

股票外部來源、歷史文件與待評估刪除候選，集中見 **[股票來源與歷史資料待評估清單](source-review.md)**。`stock` 內的 36 個外部來源是本庫維護的來源索引，不是帳號另外擁有的 36 個儲存庫。

## AI 簡報與文件製作

| 專案 | 用途與型態 | 整理狀態 |
|---|---|---|
| [ppt-master](https://github.com/week833/ppt-master) | [hugohe3/ppt-master](https://github.com/hugohe3/ppt-master) 的 Agent Skill 工具包 Fork；由文件或主題生成原生可編輯 PPTX，並支援範本填入與簡報修改。 | 保留 Skill／研究參考。 |

用途依據：[PPT Master README](https://github.com/week833/ppt-master/blob/main/README.md)、[SKILL.md](https://github.com/week833/ppt-master/blob/main/skills/ppt-master/SKILL.md)。

## Windows 桌面自動化

| 專案 | 用途與型態 | 整理狀態 |
|---|---|---|
| [windows-local-action-recorder](https://github.com/week833/windows-local-action-recorder) | Windows 本機操作錄製、Codex 工作流程交接、審核與受控重播示範工具。 | 保留工具；按 README 的示範範圍使用。 |

用途依據：[Windows Local Action Recorder README](https://github.com/week833/windows-local-action-recorder/blob/main/README.md)。

## 副本保留待評估

| 專案 | 已確認的證據 | 待評估事項 |
|---|---|---|
| [ai-trader](https://github.com/week833/ai-trader) | 查核時 Fork 僅有 `main` 分支，與上游 `main` 同為 `d03ff6c056b32ced735adf7c19ed8175adb1c8df`，最後提交為 2026-06-11。 | 評估是否仍需保留 Fork，或以直接連結上游作研究參考；尚未確認部署或其他工作流程是否依賴此副本。 |

證據：[Fork 分支清單](https://api.github.com/repos/week833/ai-trader/branches?per_page=100)、[Fork 提交](https://github.com/week833/ai-trader/commit/d03ff6c056b32ced735adf7c19ed8175adb1c8df)、[上游提交](https://github.com/HKUDS/AI-Trader/commit/d03ff6c056b32ced735adf7c19ed8175adb1c8df)。

此處評估的是重複副本的保留需求，不代表 AI-Trader 已過時，也不是刪除決定。未僅因提交日期較早、Fork 身分或分支名稱，把專案判為可刪除。
