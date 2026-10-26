# Codex → Claude：單股 I/O QA 交接（2026-10-05）

## 停止狀態

- 使用者要求停止並交 Claude 後，Codex 已停止實作、測試、安裝及 QA 啟動；先前未執行授權全部取消。
- 2026-10-05T12:16:49.9047634+08:00 唯讀 Win32_Process 查詢：本輪 low_memory_qa / attempt008、009 driver / collect008、009 / attempt008 launcher、observer 匹配程序為空；008 PID 60760、63836、23792、44048 均不存在。未呼叫 terminate，未動其他 owner、中央或服務。
- 最後程式／候選 facts 讀回：12:17:43；權重及副本 byte 邊界讀回：12:18:32。本交接不是模型通過、正式發布或執行放行。
- 本 worker：Galileo，thread 01a10472-28c1-7332-92fb-e1694ac37f7b，實際 low_worker / gpt-6.1-sol / ultra。

## 精確位置

- Q = D:\stock\logs\single_stock_reference_completion\qa_runner
- L = Q\low_memory_qa_001
- O = L\released_qa_unbounded_io_20261004_1946_004
- M = Q\unbounded_io_001\report_metadata_io_001
- I = M\integration\C_metadata_001
- P8 = L\QA\focus_import_continuation008_preparation
- P9 = L\QA\focus_transport_continuation009_preparation
- 原失敗 training：D:\TWStockData\program_data\dstock_single\models\shared_stock\training\shared_20261004_raw_reference_completion
- 已恢復封存：同 training root 下 shared_20261004_raw_reference_recovered。原 MemoryError、所有 QA failure、checkpoint、preps、模型、cache 均保留。

## 已完成與未完成

- 007 three_month A 已完整完成，但 status=fail，唯一 failed check 為 three_month_target_contract_missing_official_2027_calendar；other_report_checks_passed=true、no_fit=true。不能稱正式可交付。
- O\three_month\baseline_report_receipt.json：288010 B，SHA 95f410dd48f523cf4a992e4a0777f12db6f8114960ab0d9c05376d8cb30e7553。
- 可重用的是該完整 A 證據，不是將 status 改 pass 或標成新 release 執行。其鏈為原 controller subprocess.wait、hash-bound receipt、下一 C request、parent afterguards、controller OS exit；不存在獨立 A 同 handle observer，不能借用 005 observer。
- 原 140 unique tests 已由主代理接受；rev005 新增 6 affected cases 均 pass，source-only native source_path/full SHA 48 項 pass，累計 146。此次停止前沒有重跑它們，也沒有執行任何 009 fixture。
- 原兩份 successful private prep 與 derived prep、同一批 artifact / weights / 50 training stocks / lineage / data / pin 沿用；不重 prep、不 fit、不重訓。
- 三月 weights SHA b633540565dc325ce149ad936dcf33766252102df7d42b29f5b2ca28849448fa；fit_id shared-fit-b4ab718af170ae36ca18e091。fresh 小型 prep 與 007 actual_loader_observations 相符；停止時未重新載入模型。
- immutable pin e492074a4246897dc0859514c49f5a684d342ae8f95cbc973df1c6dd798d1982；asof 2026-10-02。
- data fingerprint b1c763bc922c96f71f6457681e218e8ac9992bf4b14b8f7d9b1e5022e89a21e3；descriptor SHA 312558945f0feefbce42461931be91c53fe7037bdcb03267bf5e1de4383ef36e。
- training seal 4b3a9672245698fb3d6cfd20f413531a1ac19a1881d859a9373f675632d4b5c0。未 formal apply / deploy / approved_readback，未 Telegram / Central / service 寫入。

## 008 實際失敗與修正

- 008 C actual PID 23792，creation 11:42:44.121794+08:00，11:47:23.0926987 自然 exit1；driver actual PID 60760，creation 11:42:15.183998，11:47:24.6775958 自然 exit1。retained OS handles 證據均 terminate_called=false。
- 根因：I\src\tw_single_stock_shared_model.py:365 → io_compat.bind_loaded_bundle:288 → _authority:220 → _validate_entry:145；最內層 io_compatibility:B_to_C_not_exact_approved_IO，外層 shared_bundle_hash_or_compatibility_mismatch。
- actual entry changed 12 項，declared set 精確相同；舊 IO_CHANGED_FILES 唯一漏 tw_single_stock_report_focus.py。不是模型／weights／pin 預測失敗。
- P8\attempt008_execution_receipt.json：32277 B，SHA cfcbaa82c3235b21976aa83bac498df419ce3ea195c8ddcfc6fa680c176ca2bd。
- O\attempt008_driver_receipt.json：349555 B，SHA 2f923627cd1dc528dcf81797dfe2ef8adfd29c2d68ae4c210b81a259ec87e53b。
- O\attempt008_three_month_c_validation_report_request.json：961 B，SHA 0dc89a360de9c89d7e257f2f208e0a9d569259478781ac94cbadc3ff9028fac5。
- O\attempt008_three_month_assembly_proof.json：10907 B，SHA aae325aa40d29d5cb3d8fca6344bd61c6dd000b3317fccc40bb28468aabbe445。
- 008 source/authority afterguards 已完成、denied=[]；nextday 及三月等價尚未開始。完整 trace 位於 O\three_month\c_validation_report.log，SHA c6731e4c324e24b143361f0d69ccc6c87efc6f4260d6b0f7109cb7da9073c8b4。

rev005 已由另一 owner 完成 source-only 修正，fresh SHA 相符：
- I\evidence\runtime_namespace_C_metadata_001.revision005.json：a6f469e8f882ee459a06a72758d1e66c31817663c9d7e9deebfe22ec33507f2c。
- I\evidence\input_readback_revision005_receipt.json：d2ab026d81fee0c2ac1cc872d77e39570009445701f8c6a44b555dfeedb3edc4。
- I\evidence\main_C_metadata_IO_source_review_addendum_005.json：7d8cdc1bb43ac2fdf583a781fef7232f9850874b03bf5ac34e1dc63d64c3e916。
- M\evidence\focus_io_contract_005\final_receipt.json：16516 B，SHA 3b1400186ed0bd03c8e339cac4c81656967fa0a7f37cdf6d5a0666b70ff171ad；affected_validation_pass_source_only，6/6，非 actual report pass。
- I\src\tw_single_stock_io_compat.py：35665 B，SHA 1c492b4c0d55f2b9bacd5e1739b88444c8e1395222cd7e010e5eb84a70a147bf。只增加 focus 白名單一行。
- I\src\tw_single_stock_report_focus.py：48721 B，SHA 10d1fca6fa54fb173af78e729f74149267a278870280d789d8eda54ab8d28383；與 F 原 bytes 相同，僅 path 改 I。
- namespace 8931a2c10192a255e5a35b1f16b0ef2e31a6b204f4fb016f0fe7d20be02cfa69；sealed16 2c8e6b279bb6c5f07974d0253fa13fcb697ede0308be30bae1cc7f23300eb975。

## Active 與 009 Partial：不可直接執行

Active 停止時仍是 008，沒有安裝 009：
- L\c_runtime_qa.py：152596 B，SHA 4c50d11570f1f3efdc3c30e4f4360c0eb64205ac5d124150881b30ff4cc98a9a。
- L\release_bindings.py：27498 B，SHA a769d2fbc8a8995c68262a36e19227bd63ac7a67d9afc848167d1db4df91469f。
- L\low_memory_qa.py：49264 B，SHA 100b11a087805afc29daf90dceee5572165bd8a55bea9d2d500d96cfa0a6407a。
- Active metadata wrapper 仍 L\focus_attempt006_preparation\exact_runtime_wrapper_005.json，SHA d03e63fcb289fd1f56b7b9e9fecf3208771466514aeecc78a7d2201428c521a7，綁 rev004／舊 io_compat SHA 9736f9a7d7a326a9cab51e677be2c9768f6c1a9e3647c5c1d8b3c7df99c817f0，focus path 仍 F\src。
- I/core 已 rev005；因此 active bindings 與實體來源不一致，預期 exact-source/origin guard 拒絕。不可直接啟動 008 或宣稱來源已完全相容。

P9 目前只有下列 7 個 partial 檔，未 freeze、未測試：
| 檔名 | Bytes | SHA256 |
|---|---:|---|
| collect008_controls.py | 3190 | ba5b16d6c549d46b59367c10fa1e5da83c7bf4b2997ecf2feee825a0bb868875 |
| transport_008_bindings.json | 9434 | 5310967960942f39cd802c1c66e87a7ab9ba88cc6c9867562228008b1a7d83bc |
| collect009_controls.py | 3224 | ca7c67c218ab24d1a09bdc1da2fd98f64fbeff52ed39769586bdc8ff985c9dba |
| transport_008_bindings009.json | 9556 | b7a2fff3f7c1ae49752b8fecd8641fdd01c46517db3b4062209a198652fa9f1d |
| exact_runtime_wrapper_006.json | 21197 | 9148f6409df5503246ed391f3afc60c64e88c5aa23735b4a39cddeecbab53add |
| c_runtime_qa_original008.py | 152597 | d738c0333461ccc57fd712bc550309cc42465d209a3c2178d321b82a135da4a8 |
| release_bindings_original008.py | 27499 | fcadd2003d7613b8ff4aba7fb7980a5267a2b423ced0c988302275ff9d0e2eb8 |

兩份 original008 partial 副本各比 active 多一個尾端 LF（byte 10）；其原長度 prefix SHA 等於 active，但整檔 SHA 不等。不能作 exact-byte 原件或默認合格的 cross-release 基礎。transport_008_bindings009 亦引用這些 partial 副本，尚未驗證。
009 helper candidate、release candidate、固定 driver、fixtures、AST delta、最終 preparation receipt 均未落盤；未執行新測試。只有小型 control 收集腳本已 exit0，並非模型驗證。

## 008 私有 Transport：保留，不重造

位置 O\three_month\models\shared_stock：
- current.json：1418 B，SHA 31ed21005eea9fb5c93c3a31e6a3be37a97e1175f2f7bfb12b61a30f795739bb，小型 v2。
- original_A_current.json：6679309378 B。原 A v1 bytes 被 008 rename 保留；原 prep SHA d55a1e6826de45078b4062ed3b831878f84c919f6ba15045a82ce002d4062381。停止時僅 stat，不再讀大型檔 hash。
- evaluations\6a5b0deeb645673511f7f02414107539c681d34c2d1128dc48d4a5d33a82f41b.json：6030310180 B；預期完整 SHA 同檔名。停止時未重新全檔 SHA。
- io_compatibility\validation_entry.json：23808 B，SHA feab1f591c5b0a3af9711b11a46119bc3cc989ff5f5b32bf413b39948465cf66。
- io_compatibility\current.json：286 B，SHA a6e726d78211d98489a963dd84aa36eec725c00fc2a64e6b1b69a9391446fe26。
- evidence\deployment.json、fixture_compatibility.json、actual_usage.json 原檔保留。其 exact facts 在 P9 控制清單；後續必須重新核驗，不以 exists 作成功。
- validation_entry009.json、registry008_preserved.json fresh 均不存在；008 registry 尚未更新。

先前已決最小續接規格（目前仍待 Claude 實作／審核）：
1. 大型模型前，完成 rev005 actual entry、48 exact source path/SHA、registered imports、changed-local path、007 A 完整 provenance、008 自然 exit/request/release/helper/authority 鏈預檢，錯誤即停。
2. 僅 three_month 具名重用 008 transport；以 prep 完整控制資料重建預期 v2 package，完整語義及 exact serialized bytes/SHA 相等。original_A_current、evaluation、metadata artifact/archive/evidence 全 SHA/bytes 核驗且保持 leases；不重新複製 evaluation、不還原／覆寫 A。
3. 保留舊 validation_entry/evidence。新增具名 entry009；registry 更新前保留舊完整 bytes/SHA，只在 WriterGuard 中核對 expected-old SHA/identity 後 atomic 更新。路徑 exact private root、無 reparse、非 canonical；碰撞／identity/hash 錯誤拒絕。
4. tomorrow 未準備時保留原 create-only。不能擴通用 resume、借舊 parent leases、偽造 pass 或 proof。
5. cross-release 僅允許核准 C transport/registry helper與 runtime 綁定 AST 差異；A42/12/5、原 A 計算 AST、prep/artifact/data/weights/lineage/source/pin 全不變。完成新小型正反 affected fixtures後再審核，不能把 component/source-only pass 當 actual 模型 pass。
6. 固定剩餘序列：3m C → 3m 等價 → next A → next C → next 等價。具名引用 007 A calendar-only原 provenance；008 failed C 不能 reuse為pass。現無可直接執行的 009 入口／命令。

## 禁止範圍與尚存外部阻礙

- 不刪任何歷史／partial 檔，不搬 models/preps/cache，不重訓／fit／refetch／抽樣／截短 metadata，不假造 hash、日期、qualification 或 source proof。
- 不修改正式 source、A42 歷史 seal、模型數值、immutable pin、Central、canonical pointer、Telegram、Bot、服務、排程或其他 owner 程序。
- 不重跑既有 146 unique tests；新的受影響驗證與 actual QA 須由後續執行者依使用者授權安排。
- 2027 官方 calendar 缺口屬中央 owner 範圍外；calendar-only 可保留 fail 並續測其他 mode，但整體不得 pass/apply。
- 正式 rootvenv/Bot 的 ijson 部署相依尚未操作；分析 GitHub venv已裝 ijson3.5.1，不代表正式 Bot 相依已齊。
- 停止時沒有 actual C report pass／nextday pass／整體等價 pass。後續成功、formal acceptance、部署時程均未知。

