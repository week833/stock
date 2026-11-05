import fs from 'node:fs/promises';
import path from 'node:path';
import { Workbook, SpreadsheetFile } from '@oai/artifact-tool';

const [inputFile, outputFile, ...flags] = process.argv.slice(2);
if (!inputFile || !outputFile || flags.some(flag => flag !== '--verify')) {
  throw new Error('用法：node excel_report.mjs INPUT_JSON OUTPUT_XLSX [--verify]');
}
const verify = flags.includes('--verify');
const data = JSON.parse(await fs.readFile(inputFile, 'utf8'));
const { predictions, coverage, meta, model } = data;
const context = data.report_context || {};
const DISPLAY_TEXT = {
    "unknown": "未確認", "none": "無", "null": "未提供",
    "not_applicable": "不適用", "not_available": "無可用資料",
    "unverified": "尚未驗證", "not_yet_verified": "尚未驗證",
    "verified": "已驗證", "verified_declared_session": "已驗證的下一交易日",
    "success": "成功", "success_current_switched": "已發布並切換目前資料",
    "complete": "完整", "partial": "部分完成", "failed": "失敗",
    "blocked": "受阻", "pending": "待確認", "pending_source": "待來源更新",
    "waiting_quota": "等待資料額度恢復", "empty": "無資料",
    "research_only": "僅供研究", "research_effect_unknown": "研究效果尚未確認",
    "eligible": "符合分析條件", "excluded": "未納入分析",
    "insufficient_history": "歷史資料不足", "insufficient_sessions": "交易日資料不足",
    "insufficient_253_sessions": "未滿253個已證交易日",
    "outside_current_market_domain": "不屬於目前市場範圍",
    "outside_current_ordinary_universe": "不屬於目前普通股範圍",
    "not_ordinary_stock": "非普通股", "etf_excluded": "已排除指數股票型基金",
    "missing_price": "缺少價格資料", "invalid_price": "價格資料無效",
    "fit_up_rate_baseline": "歷史收漲率基準", "zero_return_baseline": "零報酬基準",
    "constant_baseline": "固定基準", "logistic": "邏輯迴歸模型", "logistic_regression": "邏輯迴歸模型",
    "hist_gradient_boosting": "直方圖梯度提升模型",
    "hist_gradient_boosting_classifier": "直方圖梯度提升方向模型",
    "hist_gradient_boosting_regressor": "直方圖梯度提升報酬模型",
    "ridge": "嶺迴歸模型", "uncalibrated": "未校準", "calibrated": "已校準（可靠度未證）",
    "sigmoid": "邏輯校準（可靠度未證）", "platt": "邏輯校準（可靠度未證）",
    "platt_scaling": "邏輯校準（可靠度未證）", "sigmoid_calibrated": "已套用邏輯校準（可靠度未證）",
    "calibrated_sigmoid": "已套用邏輯校準（可靠度未證）", "isotonic": "保序校準（可靠度未證）",
    "not_calibrated": "未校準", "unqualified": "尚未具備資格",
    "all_tied_baseline": "基準同值，無個股排序訊號",
    "market_no_stock_signal": "市場基準，無個股排序訊號",
    "market_baseline_no_stock_discrimination": "市場基準，無個股區分能力",
    "all_tied_predictions": "預測同值，無個股排序訊號",
    "model_values_displayed_effect_unknown": "依模型數值展示，排序效果未驗",
    "requirements_version_differs": "資料需求版本標示不同",
    "requirements_source_changed": "資料需求來源已變更",
    "interface_revision_differs": "介面版本標示不同",
    "capture_requirements_source_changed": "採集需求標示已變更",
    "contract_changed_since_activation": "啟用後資料規格標示已變更",
    "contract_binding_differs_from_policy": "資料規格出處標示不同",
    "model_pointer_changed_since_activation": "啟用後模型指標已變更",
    "consumer_source_changed_since_activation": "分析程式來源已變更",
    "activation_model_differs": "模型啟用標示不同",
    // 共用 C0 契約與本程式的版本標籤（同 reporting.py；各市場資料日只陳述差異，不寫分析方式）。
    "market_asof_differs": "各市場資料日不同", "market_asof_differs_per_market_inference": "各市場資料日不同",
    "inference_code_differs_from_training": "分析程式與模型訓練時使用的程式不同",
    "mutable_ref_changed_since_adoption": "部分來源參照已更新",
    "contract_adoption_unverified": "目前契約採納狀態尚未驗證", "adoption_unverified": "採納紀錄尚未驗證",
    "contract_adoption_receipt_missing": "契約採納紀錄不存在", "policy_version_mismatch": "資料規格版本不同",
    "price_lags_scope": "價格資料日早於更新範圍截止日",
    "price_actual_max_date_unknown": "價格實際最新日期未知，以更新範圍日期分析",
    "price_series_vintage_stale": "部分股票價格資料版本較舊",
    "current_classification_observed_after_data_asof": "普通股分類觀測日較分析資料日晚",
    "stock_market_asof_lags_latest_market": "股票依所屬市場的資料日分析",
    "pinned_head_unavailable_used_current": "原指定快照已不可讀，改採目前已發布資料",
    "data_updated_since_caller_scope": "資料於指定範圍後重新發布，改用目前發布資料",
    "optional_route_unavailable": "部分補充資料來源未提供", "readiness_unavailable": "資料就緒狀態無法取得",
    "requested_asof_differs": "實際分析資料日與要求日期不同",
    "stock_info_publication_not_complete": "股票基本資料發布尚未完成",
    "explicit_model_differs_from_pointer": "指定模型與現役模型指標不同",
    "current_daily_policy_schema_differs": "現行每日資料規格格式不同",
    "current_C0_model_route_optional_in_policy": "現行規格把模型所需資料來源列為選用",
    "C0_runtime_binding_missing": "現役模型執行識別未提供",
    "C0_current_policy_drift_during_job": "執行期間現行資料規格已變更",
    "in_sample_lookahead_not_recorded": "部分股票屬歷史資料試算，未寫入前瞻紀錄",
    "skipped_data_lagged": "資料落後的股票未寫入前瞻紀錄", "forward_ledger_skipped": "本次未寫入前瞻紀錄",
    "current_policy_binding": "現行資料規格識別",
    "original_model_feature_contract": "模型原始特徵規格",
    "contract_adoption_ref": "規格採用紀錄",
    "effective_C0_semantics_projection_sha256": "現役模型語意摘要",
    "model_activation_epoch": "模型啟用批次", "data_time": "資料時間",
    "daily_requirements_binding": "資料需求識別", "actual_model_inputs": "實際模型輸入",
    "forward_binding": "共同前瞻紀錄", "active_model_binding": "現役模型識別",
    "preopen_model_ranking": "開盤前版可交易 Top10（依收漲機率→預估報酬排序）",
    "preopen_hgb_classifier_calibrated": "開盤前版直方圖梯度提升方向模型（校準後）",
    "preopen_hgb_regressor_calibrated": "開盤前版直方圖梯度提升報酬模型（校準後）",
    "preopen_predicted": "開盤前版已預測",
    "preopen_not_in_snapshot": "開盤前版快照未含此股（C0 狀態保留於原始資料）",
    "model_source_forced_c0": "依設定強制使用 C0",
    "preopen_no_latest": "開盤前版尚未產出（找不到 latest.json）",
    "preopen_stale": "開盤前版快照不是本次最新交易日",
    "preopen_target_mismatch": "開盤前版目標日與已驗證下一交易日不同",
    "preopen_parquet_engine_missing": "缺少 parquet 讀取元件",
    "preopen_snapshot_sha_mismatch": "開盤前版快照雜湊不符",
    "preopen_snapshot_missing": "開盤前版快照檔不存在",
    "preopen_reader_error": "開盤前版讀取錯誤",
    "preopen_reader_unavailable": "開盤前版讀取模組不存在",
    "preopen_schema_mismatch": "開盤前版格式版本不符",
    "preopen_snapshot_columns": "開盤前版快照缺少必要欄位",
    "preopen_snapshot_values": "開盤前版快照數值無效",
    "preopen_us_status_invalid": "開盤前版美股狀態無效",
    "preopen_no_tradable": "開盤前版快照沒有可交易股票",
    "preopen_duplicate_stock": "開盤前版快照股票代碼重複",
    "preopen_rank_inconsistent": "開盤前版可交易名次不一致",
};
function human(value, fallback = '其他狀態（原始紀錄保留）') {
  if (Array.isArray(value)) return value.map(item => human(item, fallback)).join('；') || '未提供';
  if (value === null || value === undefined || value === '') return '未提供';
  if (typeof value === 'boolean') return value ? '是' : '否';
  if (typeof value === 'object') return '詳見既有原始資料';
  const text = String(value).trim();
  const translated = DISPLAY_TEXT[text] ?? DISPLAY_TEXT[text.toLowerCase()];
  if (translated) return translated;
  if (text.startsWith('版本標籤：')) {
    return '版本提醒：' + human(text.slice('版本標籤：'.length).split('（', 1)[0].trim(), '版本標示有差異');
  }
  if (/[\u3400-\u9fff]/.test(text)) {
    return text.replace(/\b[A-Za-z][A-Za-z0-9_]*\b/g,
      code => DISPLAY_TEXT[code] ?? DISPLAY_TEXT[code.toLowerCase()] ?? (code.includes('_') ? fallback : code));
  }
  return /^[0-9.,%+ /:\-]+$/.test(text) ? text : fallback;
}
// 資料時間表（同 reporting.py 的 _display_time）：流程時間戳與缺漏欄位（見資料來源與限制）不列；
// 價格最新日、模型訓練資料日只在和分析資料日不同時列（模型訓練資料日未提供時也不列）；發布時間未宣告、沒有版本標籤時不列。
const TIME_ROWS_HIDDEN = ['價格收集時間（最新）', '本次讀取時間', '模型啟用時間', '缺漏欄位'];
const TIME_ROWS_IF_NOT_ANALYSIS_DAY = ['價格最新日', '模型訓練資料日'];
const TIME_ROWS_IF_PROVIDED = ['模型訓練資料日'];
const TIME_PUBLISHED_LABEL = 'head 發布時間（producer 宣告，未驗證）';
const UNKNOWN_VALUES = ['unknown', 'none', 'unverified', 'not_yet_verified'];
const provided = value => value !== null && value !== undefined && value !== '' && value !== '未提供';
// 發布時間是 unknown 等佔位值時視同未宣告。
const declared = value => provided(value) && !UNKNOWN_VALUES.includes(String(value).toLowerCase());
function displayTime(source) {
  const analysis = source.rows.find(([label]) => label === '分析資料日')?.[1];
  const rows = [];
  for (const [originalLabel, originalValue] of source.rows) {
    let label = String(originalLabel);
    let value = originalValue;
    if (TIME_ROWS_HIDDEN.includes(label) || (TIME_ROWS_IF_NOT_ANALYSIS_DAY.includes(label) && value === analysis)
      || (TIME_ROWS_IF_PROVIDED.includes(label) && !provided(value))) continue;
    if (label === '版本標籤') {
      const codes = [...new Set((data.version_labels ?? []).filter(item => item && typeof item === 'object').map(item => String(item.code)))].sort();
      if (!codes.length) continue;
      // 已知代碼寫中文（同義合併）；沒有中文的代碼只寫項數，原始代碼保留於 report.json。
      const known = [...new Set(codes.filter(code => Object.hasOwn(DISPLAY_TEXT, code)).map(code => DISPLAY_TEXT[code]))];
      const unknown = codes.filter(code => !Object.hasOwn(DISPLAY_TEXT, code)).length;
      const other = `${known.length ? '其他' : ''}版本標示有差異（${unknown} 項）`;
      [label, value] = ['版本提醒', [...known, ...(unknown ? [other] : [])].join('、')];
    } else if (label === TIME_PUBLISHED_LABEL) {
      if (!declared(value)) continue;
      label = '資料發布時間（提供者宣告，未驗證）';
    } else if (typeof value === 'string' && UNKNOWN_VALUES.includes(value.toLowerCase())) {
      value = human(value);
    }
    rows.push([label, value]);
  }
  return { ...source, rows };
}
function forwardCalibration() {
  const raw = context.forward_calibration_versions ?? [];
  const values = Array.isArray(raw) ? raw : [raw];
  const recorded = values.filter(value => value !== null && value !== undefined
    && !['', 'none', 'null'].includes(String(value).trim().toLowerCase()));
  if (!recorded.length) return '未套用';
  if (recorded.every(value => ['unknown', 'unverified'].includes(String(value).trim().toLowerCase()))) return '是否套用尚未確認';
  return '已記錄（不等於可靠性已驗證）';
}
if (!Array.isArray(predictions) || !predictions.length || !Array.isArray(coverage) || !meta || !model) {
  throw new Error('缺少預測、覆蓋、來源或模型資料。');
}
if (context.effective_profile !== 'C0' || context.horizon !== 1 || context.price_basis !== 'adjusted_close_return') {
  throw new Error('報告僅支援現役C0 H1調整後收盤方向與報酬。');
}
// H1 顯示模型：preopen（開盤前版）或 c0（含回退）；C0 契約綁定在兩種模式都照常核對。
const modelSource = data.model_source ?? context.model_source ?? 'c0';
if (!['c0', 'preopen'].includes(modelSource)) throw new Error(`H1 模型來源無效：${modelSource}`);
const preopen = modelSource === 'preopen';
const preopenMeta = data.preopen_meta ?? context.preopen_meta ?? null;
const preopenStatus = data.preopen_status ?? context.preopen_status ?? {};
const gate = data.preopen_gate ?? context.preopen_gate ?? null;
const disclosure = data.model_disclosure ?? context.model_disclosure ?? null;
// 開盤買進效果片語依快照配方（model_disclosure.trading_effect）；舊資料沒有此欄位時沿用配方 v1 原文（同 reporting.py）。
const tradingEffect = String(disclosure?.trading_effect || '開盤買進 Top10 保留期平均為負');
// 開盤前版排序說明的命中率子句；只在必要揭露與「使用限制」寫出（同 reporting.py 的 PREOPEN_HIT_RATE_NOTE）。
const HIT_RATE_NOTE = '命中率指收盤報價方向，不代表交易獲利';
const sourceNote = String(data.model_source_note ?? context.model_source_note ?? 'H1 模型來源：C0');
if (preopen) {
  if (!preopenMeta || !['run_id', 'model_sha256', 'snapshot_sha256'].every(key => preopenMeta[key])) {
    throw new Error('開盤前版識別缺漏（run_id／model_sha256／snapshot_sha256）。');
  }
  if (context.ranking_status !== 'preopen_model_ranking') throw new Error('開盤前版排序狀態不一致。');
  if (!disclosure || !Array.isArray(disclosure.lines) || !disclosure.lines.length) throw new Error('開盤前版必要揭露缺漏。');
}
const bindingFields = ['current_policy_binding', 'original_model_feature_contract', 'contract_adoption_ref',
  'effective_C0_semantics_projection_sha256', 'model_activation_epoch'];
// Key-order-independent comparison (same rule as reporting.py's dict equality).
const canonical = value => Array.isArray(value) ? `[${value.map(canonical).join(',')}]`
  : value !== null && typeof value === 'object'
    ? `{${Object.keys(value).sort().map(key => `${JSON.stringify(key)}:${canonical(value[key])}`).join(',')}}`
    : JSON.stringify(value ?? null);
const absent = value => value === undefined || value === null || value === ''
  || (typeof value === 'object' && !Array.isArray(value) && Object.keys(value).length === 0);
// A binding that was not provided is shown as 未提供 (same as reporting.py);
// two provided copies that disagree remain a report self-consistency error.
const fieldsMissing = Array.isArray(data.report_fields_missing) ? [...data.report_fields_missing] : [];
for (const key of bindingFields) {
  if (absent(context[key])) {
    if (!fieldsMissing.includes(key)) fieldsMissing.push(key);
    continue;
  }
  for (const container of [meta, model]) {
    if (Object.hasOwn(container, key) && !absent(container[key]) && canonical(container[key]) !== canonical(context[key])) {
      throw new Error(`契約身分不一致：${key}`);
    }
  }
}
const dataTime = displayTime(data.data_time_display && Array.isArray(data.data_time_display.rows) ? data.data_time_display
  : { rows: [['資料時間', '未提供']], summary: '資料時間未提供', not_latest: false, badge: null });
const noStockRanking = !preopen && (['all_tied_baseline', 'market_no_stock_signal',
  'market_baseline_no_stock_discrimination', 'all_tied_predictions'].includes(context.ranking_status)
  || context.no_stock_specific_model_signal === true);
const number = value => value === null || value === undefined || value === ''
  ? null : Number.isFinite(Number(value)) ? Number(value) : null;
const shown = value => value === null || value === undefined || value === '' ? '未提供'
  : ['unknown', 'none', 'unverified', 'not_yet_verified'].includes(String(value).toLowerCase()) ? human(value) : value;
const date = value => {
  if (!value) return null;
  const raw = String(value);
  const iso = /^[0-9]{8}$/.test(raw) ? `${raw.slice(0, 4)}-${raw.slice(4, 6)}-${raw.slice(6, 8)}` : raw;
  if (!/^[0-9]{4}-[0-9]{2}-[0-9]{2}$/.test(iso)) throw new Error('日期必須是YYYYMMDD或ISO純日期。');
  const parsed = new Date(`${iso}T00:00:00Z`);
  if (!Number.isFinite(parsed.getTime()) || parsed.toISOString().slice(0, 10) !== iso) throw new Error('無效日曆日期。');
  return parsed;
};
const markets = { TWSE: '上市', TPEX: '上櫃' };
const market = value => markets[String(value ?? '').toUpperCase()] ?? '未確認';
// Markets analysed on different days: each market's own day ('' when one common day).
const day8 = value => value ? String(value).slice(0, 10).replaceAll('-', '') : '未提供';
const marketDays = mapping => mapping && typeof mapping === 'object' && !Array.isArray(mapping)
  ? Object.keys(mapping).sort().map(key => `${markets[key.toUpperCase()] ?? key} ${day8(mapping[key])}`).join('／') : '';
const baseByMarket = marketDays(context.base_session_by_market);
const targetByMarket = marketDays(context.target_session_by_market);
const status = row => {
  const reason = row.reason ?? row.exclusion_reason ?? row.status ?? '未提供原因';
  return human(reason, '其他排除原因（原始紀錄保留）');
};
// 欄位：[鍵, 標題, 欄寬]。前六欄固定（E/F 為機率與報酬），Top10 與全股票預測各自取用（同 reporting.py）。
const COLUMNS = [['display_order', '展示序號', 13], ['stock_id', '股票代碼', 16], ['name', '股票名稱', 24], ['market', '市場', 12],
  ['probability', '下一交易日收漲機率', 24], ['expected_return', '下一交易日調整後收盤報酬', 30],
  ...(preopen ? [['tradable', '可交易', 12], ['rank_tradable', '可交易名次', 14]] : []),
  ['model_source_text', '模型來源', 22], ['data_date_text', '資料日', 28], ['target_date_text', '本股目標日', 28], ['data_reminder', '資料提醒', 65]];
const STATUS_COLUMN = ['status', '分析狀態／排除原因', 62];
const col = index => String.fromCharCode(64 + index);
function predictionCells(row) {
  const probability = number(row.probability);
  const expectedReturn = number(row.expected_return);
  if (probability === null || probability < 0 || probability > 1 || expectedReturn === null) {
    throw new Error(`${preopen ? '開盤前版' : 'C0'}機率或報酬無效：${row.market}:${row.stock_id}`);
  }
  // Both values are decimals. Excel's percentage format alone performs the
  // display conversion; the original return must never be divided by 100.
  return { display_order: row.display_order, stock_id: String(row.stock_id), name: row.name ?? '未提供', market: market(row.market),
    probability, expected_return: expectedReturn,
    tradable: row.tradable === true ? '是' : row.tradable === false ? '否' : '未提供', rank_tradable: number(row.rank_tradable) ?? '—',
    model_source_text: row.model_source_text ?? (row.model_source === 'c0' ? 'C0補充分析' : preopen ? '開盤前版' : 'C0'),
    data_date_text: row.data_date_text ?? '未提供', target_date_text: row.target_date_text ?? '未驗證',
    data_reminder: row.data_reminder ?? '未提供', status: null };
}
const cellsOf = (cells, columns) => columns.map(([key]) => cells[key]);
const ids = new Set(predictions.map(row => `${row.market}:${row.stock_id}`));
const missing = coverage.filter(row => !ids.has(`${row.market}:${row.stock_id}`));
// 已預測列的分析狀態可由模型來源欄得知（留空）；只有未分析列的排除原因是新資訊，沒有未分析列時整欄不輸出。
const allColumns = missing.length ? [...COLUMNS, STATUS_COLUMN] : COLUMNS;
const allRows = [...predictions.map(predictionCells), ...missing.map(row => ({
  display_order: '未提供', stock_id: String(row.stock_id), name: row.name ?? '未提供', market: market(row.market),
  probability: '未提供', expected_return: '未提供', tradable: '未提供', rank_tradable: '—', model_source_text: '未提供',
  data_date_text: '未提供', target_date_text: '未驗證', data_reminder: '未提供', status: status(row),
}))].map(cells => cellsOf(cells, allColumns));
// Top10：可交易／可交易名次只留在全股票預測（前段必為可交易、名次即展示序號）；全部同值的模型來源欄省略（來源見標題）；
// 開盤前版的資料日／本股目標日／資料提醒各檔全部相同時改為表上方一行（C0 各市場日期可能不同，一律保留欄位）。
const topCells = (data.top10 ?? predictions.slice(0, 10)).map(predictionCells);
const uniformKeys = ['model_source_text', ...(preopen ? ['data_date_text', 'target_date_text', 'data_reminder'] : [])]
  .filter(key => topCells.length && new Set(topCells.map(cells => cells[key])).size === 1);
const topColumns = COLUMNS.filter(([key]) => !['tradable', 'rank_tradable', ...uniformKeys].includes(key));
const commonParts = COLUMNS.filter(([key]) => uniformKeys.includes(key) && key !== 'model_source_text')
  .map(([key, label]) => `${key === 'data_reminder' ? '' : `${label} `}${topCells[0][key]}`);
const topCommon = commonParts.length ? `各檔共同：${commonParts.join('；')}。` : '';
const topRows = topCells.map(cells => cellsOf(cells, topColumns));
const wb = Workbook.create();
const top = wb.worksheets.add('研究展示');
const all = wb.worksheets.add('全股票預測');
const notes = wb.worksheets.add('版本與資料');
const lineage = wb.worksheets.add('資料來源與限制');
const font = 'Microsoft JhengHei';
function base(sheet, address) {
  sheet.showGridLines = true;
  sheet.getRange(address).format.font = { name: font, size: 11, color: '#000000' };
  sheet.getRange(address).format.rowHeight = 28;
  sheet.getRange(address).format.verticalAlignment = 'center';
}
function header(sheet, address) {
  sheet.getRange(address).format = { font: { name: font, size: 11, bold: true, color: '#000000' },
    horizontalAlignment: 'center', verticalAlignment: 'center', wrapText: true, rowHeight: 42 };
}
function writeRows(sheet, firstRow, rows, columns) {
  const lastCol = col(columns.length);
  const lastRow = firstRow + rows.length;
  const at = key => col(columns.findIndex(([name]) => name === key) + 1);
  sheet.getRange(`A${firstRow}:${lastCol}${lastRow}`).values = [columns.map(([, label]) => label), ...rows];
  header(sheet, `A${firstRow}:${lastCol}${firstRow}`);
  columns.forEach(([, , width], index) => { sheet.getRangeByIndexes(0, index, lastRow, 1).format.columnWidth = width; });
  if (rows.length) {
    sheet.getRange(`B${firstRow + 1}:B${lastRow}`).setNumberFormat('@');
    sheet.getRange(`E${firstRow + 1}:E${lastRow}`).setNumberFormat('0.00%');
    sheet.getRange(`F${firstRow + 1}:F${lastRow}`).setNumberFormat('+0.00%;-0.00%;0.00%');
    sheet.getRange(`E${firstRow + 1}:F${lastRow}`).format.horizontalAlignment = 'right';
    if (columns.some(([key]) => key === 'tradable')) {
      sheet.getRange(`${at('tradable')}${firstRow + 1}:${at('tradable')}${lastRow}`).format.horizontalAlignment = 'center';
      sheet.getRange(`${at('rank_tradable')}${firstRow + 1}:${at('rank_tradable')}${lastRow}`).setNumberFormat('0');
      sheet.getRange(`${at('rank_tradable')}${firstRow + 1}:${at('rank_tradable')}${lastRow}`).format.horizontalAlignment = 'right';
    }
    for (const key of ['data_reminder', 'status']) {
      if (columns.some(([name]) => name === key)) sheet.getRange(`${at(key)}${firstRow + 1}:${at(key)}${lastRow}`).format.wrapText = true;
    }
    sheet.getRange(`A${firstRow + 1}:${lastCol}${lastRow}`).format.rowHeight = 42;
  }
}
// 研究展示頁：標題、日期、排序說明、資料時間提示，接著模型來源（開盤前版說明／美股閘門／必要揭露，或回退原因與原因詳情），
// Top10 各檔共同說明，最後是 Top10 表。
const topLast = col(topColumns.length);
const topWidth = topColumns.reduce((sum, [, , width]) => sum + width, 0);
// 合併列的高度依文字長度估算（中日韓字元約兩個字寬），Top10 欄位變少、合併寬度變窄時文字不被截斷。
const textUnits = text => [...String(text)].reduce((sum, char) => sum + (/[\u2E80-\uFFEF]/.test(char) ? 2 : 1), 0);
const fitHeight = (text, minimum, line = 16) => Math.max(minimum, line * Math.ceil(textUnits(text) / Math.max(topWidth - 4, 20)) + 12);
const gateStyle = { recommended_day: { color: '#1E7E34', fill: '#E6F4EA' },
  not_recommended_day: { color: '#C00000', fill: '#FDECEA' }, unknown: { color: '#555555', fill: '#F1F1F1' } };
// 研究展示唯一的資料時間提示：非最新徽章；開盤前版另列各市場資料日（市場落後只在此可見，C0 見 D2）；
// 提供者有宣告時列資料發布時間。分析日見 A2，報告產生時間見版本與資料。
const timeRow = label => (data.data_time_display?.rows ?? []).find(([name]) => name === label)?.[1];
const timeHint = `${dataTime.badge ? `【${dataTime.badge}】` : ''}${[
  preopen && provided(timeRow('各市場資料日')) ? `各市場資料日 ${timeRow('各市場資料日')}` : '',
  declared(timeRow(TIME_PUBLISHED_LABEL)) ? `資料發布 ${timeRow(TIME_PUBLISHED_LABEL)}（提供者宣告）` : '',
].filter(Boolean).join('；')}`;
// 開盤前版說明的顯示版（同 reporting.py 的 _describe_text；原文保留於 report.json）：去掉「｜模型 <雜湊前 12 碼>」
// 內部識別碼與閘門列已寫的美股漲跌段；訊號日→目標日與 A2／A3 的分析日、已證下一交易日相同時也不重複。
// 雜湊之後以「；」接上的日期提醒保留。
function shownNote(text) {
  let shown = text.replace(/｜模型 [0-9A-Fa-f]*(?=；|｜|$)/, '');
  if (gate?.label) shown = shown.replace(/｜美股(?:收漲（建議日）|未收漲（非建議日）|漲跌未知)(?=；|｜|$)/, '');
  const dates = /｜訊號日 (\d{8}) → 目標日 (\d{8})(?=；|｜|$)/.exec(shown);
  if (dates && dates[1] === day8(context.base_session) && dates[2] === day8(context.target_session)) shown = shown.replace(dates[0], '');
  return shown;
}
const shownSourceNote = preopen ? shownNote(sourceNote) : sourceNote;
// 回退的原因詳情（快照實際日期等，同 reporting.py 的 _fallback_detail）；原因代碼只在紅字寫一次。
const targetMismatch = /^snapshot target_date (\S*) != C0 verified next session (\S+)$/.exec(String(preopenStatus.detail ?? ''));
const fallbackDetail = !preopenStatus.detail ? ''
  : targetMismatch ? `快照目標日 ${day8(targetMismatch[1])}／已驗證下一交易日 ${day8(targetMismatch[2])}` : String(preopenStatus.detail);
const banners = [{ text: preopen ? '展示順序：開盤前版可交易Top10與其餘快照股票保留原排序；快照未收錄但有可用資料者，另列C0補充分析，不混入Top10。'
  : noStockRanking ? '無個股排序訊號；展示序號與前十檔不表示較佳。'
  : '展示順序依模型數值；排序效果與交易效益未驗。', color: '#000000', height: 42 }];
if (timeHint) banners.push({ text: timeHint, bold: Boolean(dataTime.not_latest), color: dataTime.not_latest ? '#C00000' : '#000000', height: 30 });
if (preopen) {
  banners.push({ text: shownSourceNote, bold: true, color: '#000000', height: 48 });
  const style = gateStyle[gate?.code] ?? gateStyle.unknown;
  banners.push({ text: String(gate?.label ?? '前一晚美股漲跌未知'), bold: true, color: style.color, fill: style.fill, height: 36 });
  banners.push({ text: `【${disclosure.title}】`, bold: true, color: '#7A5C00', fill: '#FFFBEA', height: 30 });
  for (const line of disclosure.lines) banners.push({ text: `・${line}`, color: '#000000', fill: '#FFFBEA', height: 36 });
  if (disclosure.source) banners.push({ text: String(disclosure.source), color: '#555555', fill: '#FFFBEA', height: 36 });
} else if (preopenStatus.status === 'unavailable') {
  banners.push({ text: shownSourceNote, bold: true, color: '#FFFFFF', fill: '#C00000', height: 48 });
  if (fallbackDetail) banners.push({ text: `原因詳情：${fallbackDetail}`, color: '#555555', height: 30 });
} else {
  banners.push({ text: shownSourceNote, color: '#000000', height: 30 });
}
if (topCommon) banners.push({ text: topCommon, color: '#000000', height: 30 });
const tableRow = 4 + banners.length;
base(top, `A1:${topLast}${tableRow + Math.max(topRows.length, 1)}`);
top.getRange(`A1:${topLast}1`).merge();
// 標題只寫名稱；排序說明在 A4，命中率與交易獲利的說明在必要揭露。
const title = preopen ? '開盤前版 H1 可交易 Top10' : '下一交易日收盤研究展示';
top.getRange('A1').values = [[title]];
top.getRange(`A1:${topLast}1`).format.font = { name: font, size: 14, bold: true, color: '#000000' };
top.getRange(`A1:${topLast}1`).format.wrapText = true;
top.getRange(`A1:${topLast}1`).format.rowHeight = fitHeight(title, 40, 22);
top.getRange('A2:C3').values = [['分析日', null, date(context.analysis_asof ?? context.base_session ?? meta.asof)],
  [context.target_session ? '已證下一交易日' : '下一交易日未驗證', null, date(context.target_session)]];
top.getRange('C2:C3').setNumberFormat('yyyymmdd');
for (const [row, text] of [[2, baseByMarket], [3, targetByMarket]]) {
  if (!text) continue;
  top.getRange(`D${row}:${topLast}${row}`).merge();
  top.getRange(`D${row}`).values = [[`各市場：${text}`]];
  top.getRange(`D${row}:${topLast}${row}`).format.wrapText = true;
}
banners.forEach((banner, index) => {
  const address = `A${4 + index}:${topLast}${4 + index}`;
  top.getRange(address).merge();
  top.getRange(`A${4 + index}`).values = [[banner.text]];
  top.getRange(address).format.wrapText = true;
  top.getRange(address).format.rowHeight = fitHeight(banner.text, banner.height);
  top.getRange(address).format.font = { name: font, size: 11, bold: Boolean(banner.bold), color: banner.color };
  if (banner.fill) top.getRange(address).format.fill = banner.fill;
});
writeRows(top, tableRow, topRows, topColumns);
base(all, `A1:${col(allColumns.length)}${allRows.length + 1}`);
writeRows(all, 1, allRows, allColumns);
all.freezePanes.freezeRows(1);
// 來源提醒（同 reporting.py 的 _shown_warnings）：只列其他位置沒有的上游警告。資料過舊、落後市場、早於要求日與
// 日曆未驗證已在資料新鮮度列（研究展示另有非最新徽章），版本標籤已在版本提醒列，固定的 C0 限制句已在資料來源與限制。
const C0_LIMITATION_WARNING = 'C0 僅調整後收盤方向與報酬；歷史身分、PIT、公司行動與交易效益未驗。';
const freshness = String(dataTime.rows.find(([label]) => label === '資料新鮮度')?.[1] ?? '');
const labelCodes = new Set((data.version_labels ?? []).filter(item => item && typeof item === 'object').map(item => String(item.code)));
const warnings = (Array.isArray(meta.warnings) ? meta.warnings : meta.warnings ? [meta.warnings] : []).filter(value => {
  const text = String(value);
  return !(text === C0_LIMITATION_WARNING || (text.startsWith('非最新資料：') && dataTime.badge)
    || (text.startsWith('落後市場 ') && freshness.includes('落後市場：'))
    || (text.startsWith('資料日早於要求日：') && freshness.includes('早於要求日'))
    || (text.startsWith('日曆已驗證至 ') && dataTime.freshness_status === 'calendar_unverified_after')
    || (text.startsWith('版本標籤：') && labelCodes.has(text.slice('版本標籤：'.length).split('（', 1)[0].trim())));
});
const requested = preopenStatus.requested_model_source ?? data.requested_model_source ?? 'auto';
// 版本與資料只寫來源與設定：開盤前版說明、美股閘門、必要揭露全文與資料出處見研究展示；回退原因（含代碼）與原因詳情只在研究展示。
const modelRows = preopen ? [
  ['H1 模型來源', `開盤前版（設定：${requested}；C0 保留為備援）`],
  ['開盤前版 run_id', String(preopenMeta.run_id)],
  [String(disclosure.title), '見研究展示'],
  ['C0 備援排序狀態', human(context.c0_ranking_note, 'C0 排序狀態（原始紀錄保留）')],
] : [
  ['H1 模型來源', `C0（設定：${requested}${preopenStatus.status === 'unavailable' ? '；開盤前版不可用，見研究展示' : ''}）`],
];
const identityDay = context.ordinary_identity_verified_asof;
// 資料缺漏時，模型種類、排序狀態與當前身分核對日期的「未提供」列不列（同 HTML 本次摘要）。
const OPTIONAL_DETAIL = ['C0 備援排序狀態', '方向模型', '報酬模型', '排序狀態', '當前身分核對日期'];
const detail = [
  [preopen ? 'H1 模型與資料' : '現役C0版本與資料', '內容'],
  ...modelRows,
  ...dataTime.rows.map(([label, value]) => [String(label), value === null || value === undefined || value === '' ? '未提供' : String(value)]),
  // 行情基準日與分析資料日相同、各市場行情基準日與各市場資料日相同，不另列；狀態列見資料來源與限制。
  ['下一交易日', date(context.target_session)],
  ...(targetByMarket ? [['各市場下一交易日', targetByMarket]] : []),
  ['已分析股票數', predictions.length], ['未分析／排除股票數', missing.length],
  // 不列模型版本（雜湊碼對判讀無用；完整識別見 report.json）。
  ['方向模型', human(context.selected_direction_kind, '其他方向模型（識別見既有資料）')], ['報酬模型', human(context.selected_return_kind, '其他報酬模型（識別見既有資料）')],
  // C0 依模型數值展示時，排序狀態與研究展示的展示順序說明（A4）相同；無個股排序訊號時保留（含原因）。
  ...(preopen ? [['排序狀態', human(context.ranking_note).replace(`；${HIT_RATE_NOTE}`, '')]]
    : noStockRanking ? [['排序狀態', human(context.ranking_note)]] : []),
  ['機率校準', human(context.calibration_status)],
  // C0 的「效果未定；交易效益未驗」與資料來源與限制的「使用限制」相同，不另列。
  ...(preopen ? [['模型與交易效果', `保留期驗證見必要揭露；隔日${tradingEffect}。`]] : []),
  // 當前身分核對日期只在和分析日不同時列出。
  ...(day8(identityDay) !== day8(context.analysis_asof ?? context.base_session ?? meta.asof)
    ? [['當前身分核對日期', shown(identityDay)]] : []),
  ...(warnings.length ? [['來源提醒', warnings.map(value => human(value, '資料提醒（原始紀錄保留）')).join('；')]] : []),
].filter(([label, value]) => !(OPTIONAL_DETAIL.includes(label) && value === '未提供'));
function writeDetails(sheet, rows) {
  base(sheet, `A1:B${rows.length}`);
  sheet.getRange(`A1:B${rows.length}`).values = rows;
  header(sheet, 'A1:B1');
  sheet.getRange('A:A').format.columnWidth = 38;
  sheet.getRange('B:B').format.columnWidth = 110;
  sheet.getRange(`A2:B${rows.length}`).format.wrapText = true;
  sheet.getRange(`A2:B${rows.length}`).format.rowHeight = 48;
  for (let index = 1; index < rows.length; index++) {
    const lines = typeof rows[index][1] === 'string' ? rows[index][1].split('\n').length : 1;
    if (lines > 2) sheet.getRange(`A${index + 1}:B${index + 1}`).format.rowHeight = 22 * lines + 10;
    if (rows[index][1] instanceof Date) sheet.getRange(`B${index + 1}`).setNumberFormat('yyyymmdd');
    else if (typeof rows[index][1] === 'number') sheet.getRange(`B${index + 1}`).setNumberFormat('#,##0');
  }
}
writeDetails(notes, detail);
// H1 模型來源只在研究展示；沒有未提供欄位時不列該列。
const lineageRows = [['資料來源與限制', '說明'],
  ['來源狀態', human(meta.raw_status)],
  ['下一交易日日曆', human(context.future_calendar_status)],
  ['歷史股票成員', human(meta.historical_membership_status ?? 'unknown')],
  ['資料公開可用時間（PIT）', human(meta.pit_status ?? 'unknown')],
  ['公司行動資料', human(meta.corporate_actions_status ?? 'unknown')],
  ...(fieldsMissing.length ? [['未提供欄位', fieldsMissing.map(field => human(field, '其他必要欄位')).join('、')]] : []),
  ['前瞻校準', forwardCalibration()],
  ['使用限制', '僅供研究；正式資格、排序效果與交易效益尚未確認。資料部分完成或未確認時，不表示限制已解除。'
    + (preopen ? `${HIT_RATE_NOTE}；隔日${tradingEffect}。` : '')],
  ['原始報告資料', '完整紀錄保留於原始報告資料檔（report.json）。']];
writeDetails(lineage, lineageRows);
wb.recalculate();
if (verify) {
  const checkDir = process.env.TOP10_EXCEL_CHECK_DIR;
  if (!checkDir || !(await fs.stat(checkDir)).isDirectory()) throw new Error('驗證需要TOP10_EXCEL_CHECK_DIR指向既有資料目錄。');
  const inspection = { prediction_rows: predictions.length, coverage_rows: coverage.length,
    workbook_rows: allRows.length, headers: allColumns.map(([, label]) => label),
    return_unit: data.return_unit, ranking_status: context.ranking_status, model_source: modelSource, ranges: [] };
  const views = [['研究展示', `A1:${topLast}${tableRow + topRows.length}`],
    ['全股票預測', `A1:${col(allColumns.length)}${Math.min(allRows.length + 1, 12)}`],
    ['版本與資料', `A1:B${detail.length}`], ['資料來源與限制', `A1:B${Math.min(lineageRows.length, 12)}`]];
  for (let index = 0; index < views.length; index++) {
    const [sheetName, range] = views[index];
    const inspected = await wb.inspect({ kind: 'table', range: `'${sheetName}'!${range}`, include: 'values,formulas',
      tableMaxRows: 8, tableMaxCols: allColumns.length, maxChars: 2400 });
    inspection.ranges.push({ sheet: sheetName, result: inspected.ndjson });
    const preview = await wb.render({ sheetName, range, scale: 1, format: 'png' });
    await fs.writeFile(path.join(checkDir, `excel_preview_${index + 1}.png`), new Uint8Array(await preview.arrayBuffer()));
  }
  const errors = await wb.inspect({ kind: 'match', searchTerm: '#REF!|#DIV/0!|#VALUE!|#NAME\\?|#N/A|#NUM!|#NULL!|#SPILL!|#CALC!',
    options: { useRegex: true, maxResults: 30 }, summary: '公式錯誤檢查', maxChars: 2000 });
  inspection.formula_errors = errors.ndjson;
  await fs.writeFile(path.join(checkDir, 'excel_inspection.json'), JSON.stringify(inspection, null, 2), 'utf8');
}
const xlsx = await SpreadsheetFile.exportXlsx(wb);
await xlsx.save(outputFile);
console.log(`已產生${preopen ? '開盤前版 H1' : 'C0'}繁體中文Excel：${outputFile}`);
