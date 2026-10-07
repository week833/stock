# `.env` 安全建立與復原

正式位置：

```text
D:\stock\GitHub\.env
```

這個檔案只屬於固定安裝 root；第一次使用必須先從 GitHub 取得並完整解壓整套
package，不能只下載單一 CMD。安裝器不會搬移或刪除 `D:\stock` 其他項目，也不會
因 `.env` 缺失而猜測或產生 API 金鑰。

## 執行工具

雙擊 repository 根目錄的：

```cmd
CREATE_OR_RECOVER_ENV.cmd
```

工具提供三種操作：

1. 從先前備份中復原 `.env`。
2. 使用隱藏輸入重新建立 `.env`。
3. 保留目前 `.env`，不做修改。

## 自動搜尋範圍

復原模式會搜尋：

```text
D:\stock\.env
D:\Downloads\stock\.env
D:\stock_backup_*\...\.env
D:\stock\Recovered_from_previous_installer_*\...\.env
```

只會列出路徑與修改時間，不會顯示 `.env` 內容。

## 預設環境變數

重新建立模式會處理：

```text
STOCK_HOME=D:\stock\GitHub
STOCK_EXTERNAL_REPOS=D:\stock\GitHub\repos
PYTHONUTF8=1
PYTHONIOENCODING=utf-8
TZ=Asia/Taipei
FINMIND_AUTH_MODE=header
FINMIND_TOKEN=
ANTHROPIC_API_KEY=
OPENAI_API_KEY=
```

金鑰使用 PowerShell 隱藏輸入，不會顯示在畫面或寫入 log。也可以加入其他自訂環境變數。

## 覆蓋規則

若目前 `.env` 已存在，工具不會在未選擇操作時自動覆蓋。選擇建立或復原後會先驗證來源格式，再更新 `.env`；依專案政策不再建立日期化 `.env.backup_*` 副本。

`.gitignore` 已排除 `.env` 與 `.env.*`，避免秘密資料被提交到 GitHub。

單股分析程式的 canonical Python runtime 是 `D:\stock\GitHub\.venv`；請使用該環境的
`Scripts\python.exe` 執行分析與唯讀驗證。安裝環境的 `requirements.txt` 目前有 29 個
direct requirements；`.env` 建立工具
只管理設定檔與秘密輸入，不代表第三方套件或正式市場分析已驗證成功。請用
`VERIFY_STOCK_ENV.cmd` 做唯讀環境檢查。

## 無法復原舊金鑰時

API Token 無法由空白範本推算。找不到備份時，必須到原服務重新取得或重新產生：

- FinMind：重新取得 `FINMIND_TOKEN`
- Anthropic：重新取得 `ANTHROPIC_API_KEY`
- OpenAI：重新取得 `OPENAI_API_KEY`

不要把實際金鑰貼到 GitHub issue、commit、README 或聊天截圖中。
