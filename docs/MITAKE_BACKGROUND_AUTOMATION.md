# Mitake 背景自動化目錄與操作矩陣

## 範圍與證據邊界

本文件與 `config/mitake_feature_catalog.json`、`config/mitake_responsive_profiles.json`、`config/mitake_operation_matrix.schema.json` 是三竹股市 UI 研究的 reference_only 骨架。它們只描述視窗可見功能、定位策略、後置條件與證據狀態，不是 DDE／SDK／CLI 資料介面，也不會控制正式預測、推薦或報告發布。

功能目錄以既有 `config/mitake_navigation_menu_catalog.json` 對帳：

- 10 groups、66 visible entries。
- 51 safe direct entries。
- 2 hover-cascade entries（客戶服務、看盤地圖），只完成入口發現，未展開子表面。
- 13 個人設定 entries，只做表面盤點；涉及帳戶、設定、檔案或工作階段者維持 blocked/manual。

既有證據必須原樣保留：

- 37 個 `validated_experimental_background_snapshot` 是舊流程快照，尚未用目前 `cursor_anchored_window_posted_pointer` 新法重播，不能改稱新法驗證。
- `國際新聞` 與 `ETF影音` 各只有共用 shell（`新聞匯流`、`影音頻道`）證據，狀態固定為 `validated_shell_only`，不得升級為葉項完整驗證。
- 目前新法重播是 12 個 full leaf、2 個 shell-only；仍屬 reference_only。Declared/known data fields 是預期欄位，不等於已取得資料。

## 兩階段 responsive 流程

1. **第一階段：maximized baseline。** 先在最大化視窗完成全功能、唯一 locator、window/OCR postcondition、before/after evidence 與清理復原。`maximized_baseline` 的 `resolution` 是 `null`，以 live capture 記錄當機實際 client size（`resolution_match_policy=record_actual_maximized`）；目前舊實機證據只可作為 `evidence_reference_resolution=1366x768`、DPI 96 的參考，不能當成每台主機的固定 expected size。
2. **第二階段：尺寸與啟動狀態。** 第一階段完成後才依序建立 P0、P1、1280x720、1366x768、1400x900、1600x900、1920x1080、minimized-start 的逐 entry/profile/DPI evidence。除 `minimized-start` 外，這些 phase-2 尺寸 profiles 使用 `window_state=normal` 與 `resolution_match_policy=exact_client_size`；目前仍為 `pending_environment`，不得解讀為支援。
3. DPI 只把 96 標為目前已驗證基準；120、144、192 尚無實機證據，一律 pending。沒有逐 profile evidence 時，不使用「支援所有解析度」等字樣。

所有 locator 優先序為 **text → UIA → OCR**；必須以目前 HWND／視窗相對區域作唯一匹配，不使用固定螢幕座標、桌面截圖或模糊匹配。視窗移動或暫時 restore 需要明確安全模式並留下 before/after evidence；UI/OCR 仍不能清除正式資料缺口。

### Nested locator 座標空間契約

Nested live payload 的 `coordinate_space` 只接受 `capture`、`client`、`logical`；既有 catalog 的 `root_hwnd_client` 僅是明確的 `client` 相容別名，其他值一律 fail-closed。`capture` 是目前 HWND 的 full-window `PrintWindow` 影像相對像素，`logical` 依 `GetDpiForWindow` 投影。`client` 是 client-area 影像／Win32 client 像素，必須先以同一 layout epoch 的 `ClientToScreen(hwnd, (0,0))` 取得 capture-relative client origin，再依 client 寬高投影到 full-window capture；不得把 client 值當成固定桌面座標。

只有在 client origin 為 `(0,0)` 且 client 寬高與目前 capture 完全相同時，才宣告 `client_capture_equivalent=true` 並直接等價使用。缺少 origin、尺寸不一致或 DPI／capture size／layout epoch 改變時維持 fail-closed；`client` 的 `reference_dpi` 不會再對已是實體 client 像素的座標重複套用 DPI 比例。

## 每 entry/profile/DPI 的操作契約

`mitake_operation_matrix.schema.json` 要求每一 profile/DPI 最多 5 個 workflow。每次 workflow 至少記錄：

- before/after evidence（視窗標題、OCR、screenshot/hash 參考）。
- live evidence 會寫入該次 audit output 下的 `evidence/<entry>/<profile>/<dpi>/<attempt>/`；每個 attempt 使用不可覆寫的唯一 leaf，保存 root before/after PNG、SHA256、OCR JSON 與 layout reference。缺少必要截圖、hash 或 OCR/layout 成品時，不能標成 validated。
- locator/dispatch（text/UIA/OCR 優先、目標 HWND、方法、禁止固定座標）。
- target evidence 必須保存導航目標的截圖/hash、視窗標題、OCR/text 與可辨識區域；只有從該證據實際觀察到的 `window_title`、`capture_ocr_text`、`visible_table_rows`、`selector`、`period` 才能進入 observed data fields，declared-only 不代表已取得資料。
- 五次背景重試各自記錄 canonical `strategy` enum（`targeted_hwnd_synchronous`、`queued_post_message`、`cursor_anchor_fraction_045`、`cursor_anchor_fraction_076`、`cursor_anchor_fraction_025`；實體 fallback 為 `physical_pointer_baseline`）；成功 attempt 會保留實際成功策略（不沿用前一次 queued/cursor strategy），細節放在 `attempt_evidence`/ledger，並同步保存 `target_evidence`、`feature_graph` 與 evidence references。
- cursor/foreground（前後 HWND／游標是否 unchanged）。
- postcondition（標題或 OCR marker 是否唯一且通過）。
- data fields（declared 與 observed 分離；未觀察不得填成資料）。
- cleanup（是否復原、錯誤、`closed_hwnds`、`launched_pid`、`main_process_preserved`）。
- physical fallback（是否使用實體滑鼠、前面失敗次數、ground-truth 證據）。

狀態列舉包括：`validated_maximized_background_strict/no_mouse`、`validated_at_size_background`、`validated_via_maximized_fallback`、`validated_physical_mouse_fallback`、`validated_shell_only`、`blocked_by_safety`、`pending_environment`、`unsupported_after_5`、`unreachable`。

schema 對 live validated 狀態會強制 before/after captured 與非空 references、target screenshots/hash、postcondition、cursor/foreground、cleanup 與 physical dispatch 契約；任何 evidence 不完整會降為 `pending_environment`。dry-run、shell-only、blocked 與其他 pending 狀態不會被這些 live 成品條件誤判為已驗證。

實體滑鼠只能在同一 entry/profile/DPI 的背景流程連續 5 次失敗後，且需明確 opt-in、單一項目、保留 ground-truth evidence；不得作為日常 crawler 或全量流程。任何安全阻擋、帳戶操作、檔案匯出、登出、設定變更都不能用 fallback 繞過。

## 目前可宣稱與限制

可宣稱的只有目錄對帳、已列 evidence 的 UI 操作與狀態；成果標籤為 `reference_only`。不可宣稱：

- 三竹 UI/OCR 已成為正式行情、模型或推薦資料來源。
- 37 舊快照已用新法驗證。
- 國際新聞或 ETF影音葉項已完整驗證。
- 非 1366x768／非 DPI96、未列 evidence 的 profile 已支援。
- 個人設定或級聯子表面已安全可自動操作。

這份文件只建立資料契約；crawler/broker/Python runner 尚未整合，後續若要實作必須沿用本目錄與 schema、先唯讀檢查，再由授權工作另行執行。
