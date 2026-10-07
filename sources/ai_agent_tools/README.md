# AI、Agent 與開發方法研究來源

本索引目前對應 `repos/ai_agent_tools/` 下的 10 個實體 canonical clone。分類依
主要功能判定，不以目前是否被 D-stock 程式引用為條件；其中 2 個屬股票相關，8 個
屬一般 AI／Agent 支援。

## 股票相關 AI／交易介面研究（2）

這兩個來源因功能直接涉及台股資料研究或股票操作介面，歸入整體
`stock_related` 檢視；仍然是 reference-only，不是正式 runtime 依賴。

| Repository | 功能標籤 | 本機 clone 位置 |
|---|---|---|
| `week833/finmind-twstock-research-skill` | FinMind 台股資料與研究流程 skill；方法研究，不取代正式資料來源 | `repos/ai_agent_tools/finmind-twstock-research-skill/` |
| `week833/stock-mitake-ui-reference` | 去識別化三竹股市 UI 導航與操作研究參考；不是 API、交易或推薦功能 | `repos/ai_agent_tools/stock-mitake-ui-reference/` |

## 一般 AI／Agent／開發方法研究（8）

| Repository | 功能標籤 | 本機 clone 位置 |
|---|---|---|
| `multica-ai/andrej-karpathy-skills` | Claude Code 思考、簡潔、精準修改與目標驅動指南 | `repos/ai_agent_tools/andrej-karpathy-skills/` |
| `AugustusW/audio-tldr-skill` | 本機影音轉錄、內容快取與摘要 Agent skill | `repos/ai_agent_tools/audio-tldr-skill/` |
| `anthropics/claude-code` | 終端與 IDE 的 agentic coding 工具及 Git 工作流程 | `repos/ai_agent_tools/claude-code/` |
| `joshchaotang/claude-code-i18n` | Claude Code 介面多語翻譯與更新後修復 | `repos/ai_agent_tools/claude-code-i18n/` |
| `Raymondhou0917/claude-code-resources` | Claude Code／Codex 中文學習資源、Starter Kit 與環境安裝包 | `repos/ai_agent_tools/claude-code-resources/` |
| `allenloves/de-ai-tone` | 繁體中文寫作與翻譯的去 AI 味規範 skill | `repos/ai_agent_tools/de-ai-tone/` |
| `lopopolo/harness-engineering` | 以 context、工具與約束改善 Agent 輸出品質 | `repos/ai_agent_tools/harness-engineering/` |
| `mattpocock/skills` | 可組合、可調整的通用 Agent skills | `repos/ai_agent_tools/skills/` |

## 共同來源邊界

- 10 個 clone 全數為 `reference-only`，只供 AI、Agent、Skill、股票介面與軟體工程方法研究。
- 不得把任何內容當成正式行情、正式模型輸入、正式推薦、交易下單或績效證據；股票相關標籤只代表功能分類。
- `scripts/sources/source_manifest_v1.json` 維持 37 個 primary + 2 個 legacy（39 筆）；以上 AI／Agent 來源刻意不加入該 manifest，也不進入 primary／legacy 下載與正式驗證流程。
- canonical 路徑固定為 `repos/ai_agent_tools/<來源>/`。既有頂層舊路徑 Junction 只作相容入口；後續 `stock_related` 集中檢視也只應使用 Junction，不建立第二份 clone。
- `stock-mitake-ui-reference` 目前有未提交變更，整理時必須保留，不得以其他 clone 覆寫。
