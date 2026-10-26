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
function displayTime(source) {
  const rows = source.rows.map(([originalLabel, originalValue]) => {
    let value = originalValue;
    if (originalLabel === '版本標籤') {
      const codes = [...new Set((data.version_labels ?? []).filter(item => item && typeof item === 'object').map(item => String(item.code)))].sort();
      value = `${codes.length} 個${codes.length ? '：' + codes.map(code => human(code, '版本標示有差異')).join('、') : ''}`;
    } else if (originalLabel === '缺漏欄位') {
      value = fieldsMissing.length ? fieldsMissing.map(field => human(field, '其他必要欄位')).join('、') : '無';
    } else if (typeof value === 'string' && ['unknown', 'none', 'unverified', 'not_yet_verified'].includes(value.toLowerCase())) {
      value = human(value);
    }
    const label = String(originalLabel).replace('head 發布時間（producer 宣告，未驗證）', '資料發布時間（提供者宣告，未驗證）');
    return [label, value];
  });
  return { ...source, rows,
    summary: String(source.summary).replace('head 發布', '資料發布').replace('producer 宣告', '提供者宣告')
      .replace(/\b(?:unknown|none|unverified|not_yet_verified)\b/gi, code => human(code)) };
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
const headers = ['展示序號', '股票代碼', '股票名稱', '市場', '下一交易日收漲機率', '下一交易日調整後收盤報酬',
  ...(preopen ? ['可交易', '可交易名次'] : []), '模型來源', '資料日', '本股目標日', '資料提醒'];
const col = index => String.fromCharCode(64 + index);
function predictionRow(row) {
  const probability = number(row.probability);
  const expectedReturn = number(row.expected_return);
  if (probability === null || probability < 0 || probability > 1 || expectedReturn === null) {
    throw new Error(`${preopen ? '開盤前版' : 'C0'}機率或報酬無效：${row.market}:${row.stock_id}`);
  }
  // Both values are decimals. Excel's percentage format alone performs the
  // display conversion; the original return must never be divided by 100.
  const values = [row.display_order, String(row.stock_id), row.name ?? '未提供', market(row.market), probability, expectedReturn];
  if (preopen) values.push(row.tradable === true ? '是' : row.tradable === false ? '否' : '未提供', number(row.rank_tradable) ?? '—');
  values.push(row.model_source_text ?? (row.model_source === 'c0' ? 'C0補充分析' : preopen ? '開盤前版' : 'C0'),
    row.data_date_text ?? '未提供', row.target_date_text ?? '未驗證', row.data_reminder ?? '未提供');
  return values;
}
const ranked = predictions.map(predictionRow);
const ids = new Set(predictions.map(row => `${row.market}:${row.stock_id}`));
const missing = coverage.filter(row => !ids.has(`${row.market}:${row.stock_id}`));
const allRows = [...ranked.map((row, index) => [...row, predictions[index].model_source === 'c0' && preopen ? 'C0補充分析' : preopen ? '開盤前版已預測' : '已分析']), ...missing.map(row => [
  '未提供', String(row.stock_id), row.name ?? '未提供', market(row.market), '未提供', '未提供',
  ...(preopen ? ['未提供', '—'] : []), '未提供', '未提供', '未驗證', '未提供', status(row),
])];
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
function writeRows(sheet, firstRow, rows, full) {
  const labels = full ? [...headers, '分析狀態／排除原因'] : headers;
  const lastCol = col(labels.length);
  const lastRow = firstRow + rows.length;
  sheet.getRange(`A${firstRow}:${lastCol}${lastRow}`).values = [labels, ...rows];
  header(sheet, `A${firstRow}:${lastCol}${firstRow}`);
  const widths = [13, 16, 24, 12, 24, 30, ...(preopen ? [12, 14] : []), 22, 28, 28, 65, 62];
  for (let index = 0; index < labels.length; index++) {
    sheet.getRangeByIndexes(0, index, lastRow, 1).format.columnWidth = widths[index];
  }
  if (rows.length) {
    sheet.getRange(`B${firstRow + 1}:B${lastRow}`).setNumberFormat('@');
    sheet.getRange(`E${firstRow + 1}:E${lastRow}`).setNumberFormat('0.00%');
    sheet.getRange(`F${firstRow + 1}:F${lastRow}`).setNumberFormat('+0.00%;-0.00%;0.00%');
    sheet.getRange(`E${firstRow + 1}:F${lastRow}`).format.horizontalAlignment = 'right';
    if (preopen) {
      sheet.getRange(`G${firstRow + 1}:G${lastRow}`).format.horizontalAlignment = 'center';
      sheet.getRange(`H${firstRow + 1}:H${lastRow}`).setNumberFormat('0');
      sheet.getRange(`H${firstRow + 1}:H${lastRow}`).format.horizontalAlignment = 'right';
    }
    if (full) sheet.getRange(`${lastCol}${firstRow + 1}:${lastCol}${lastRow}`).format.wrapText = true;
    sheet.getRange(`${col(headers.length)}${firstRow + 1}:${col(headers.length)}${lastRow}`).format.wrapText = true;
    sheet.getRange(`A${firstRow + 1}:${lastCol}${lastRow}`).format.rowHeight = 42;
  }
}
// 研究展示頁：標題、日期、排序說明、資料時間，接著模型來源（開盤前版說明／美股閘門／必要揭露，或回退原因），最後是 Top10 表。
const topLast = col(headers.length);
const gateStyle = { recommended_day: { color: '#1E7E34', fill: '#E6F4EA' },
  not_recommended_day: { color: '#C00000', fill: '#FDECEA' }, unknown: { color: '#555555', fill: '#F1F1F1' } };
const banners = [];
if (preopen) {
  banners.push({ text: sourceNote, bold: true, color: '#000000', height: 48 });
  const style = gateStyle[gate?.code] ?? gateStyle.unknown;
  banners.push({ text: String(gate?.label ?? '前一晚美股漲跌未知'), bold: true, color: style.color, fill: style.fill, height: 36 });
  banners.push({ text: `【${disclosure.title}】`, bold: true, color: '#7A5C00', fill: '#FFFBEA', height: 30 });
  for (const line of disclosure.lines) banners.push({ text: `・${line}`, color: '#000000', fill: '#FFFBEA', height: 36 });
  if (disclosure.source) banners.push({ text: String(disclosure.source), color: '#555555', fill: '#FFFBEA', height: 36 });
} else if (preopenStatus.status === 'unavailable') {
  banners.push({ text: sourceNote, bold: true, color: '#FFFFFF', fill: '#C00000', height: 48 });
} else {
  banners.push({ text: sourceNote, color: '#000000', height: 30 });
}
const tableRow = 6 + banners.length;
const topRows = (data.top10 ?? predictions.slice(0, 10)).map(predictionRow);
base(top, `A1:${topLast}${tableRow + Math.max(topRows.length, 1)}`);
top.getRange(`A1:${topLast}1`).merge();
top.getRange('A1').values = [[preopen ? `開盤前版 H1 可交易 Top10：${human(context.ranking_note ?? '')}`
  : `下一交易日收盤研究展示：${human(context.ranking_note ?? '效果未定；交易效益未驗')}`]];
top.getRange(`A1:${topLast}1`).format.font = { name: font, size: 14, bold: true, color: '#000000' };
top.getRange(`A1:${topLast}1`).format.wrapText = true;
top.getRange(`A1:${topLast}1`).format.rowHeight = 64;
top.getRange('A2:C3').values = [['分析日', null, date(context.analysis_asof ?? context.base_session ?? meta.asof)],
  [context.target_session ? '已證下一交易日' : '下一交易日未驗證', null, date(context.target_session)]];
top.getRange('C2:C3').setNumberFormat('yyyymmdd');
for (const [row, text] of [[2, baseByMarket], [3, targetByMarket]]) {
  if (!text) continue;
  top.getRange(`D${row}:${topLast}${row}`).merge();
  top.getRange(`D${row}`).values = [[`各市場：${text}`]];
  top.getRange(`D${row}:${topLast}${row}`).format.wrapText = true;
}
top.getRange(`A4:${topLast}4`).merge();
top.getRange('A4').values = [[preopen ? '展示順序：開盤前版可交易Top10與其餘快照股票保留原排序；快照未收錄但有可用資料者，另列C0補充分析，不混入Top10。'
  : noStockRanking ? '無個股排序訊號；展示序號與前十檔不表示較佳。'
  : '展示順序依模型數值；排序效果與交易效益未驗。']];
top.getRange(`A4:${topLast}4`).format.wrapText = true;
top.getRange(`A4:${topLast}4`).format.rowHeight = 42;
top.getRange(`A5:${topLast}5`).merge();
top.getRange('A5').values = [[`${dataTime.badge ? `【${dataTime.badge}】` : ''}${dataTime.summary ?? '資料時間未提供'}`]];
top.getRange(`A5:${topLast}5`).format.wrapText = true;
top.getRange(`A5:${topLast}5`).format.rowHeight = 60;
top.getRange(`A5:${topLast}5`).format.font = { name: font, size: 11, bold: Boolean(dataTime.not_latest),
  color: dataTime.not_latest ? '#C00000' : '#000000' };
banners.forEach((banner, index) => {
  const address = `A${6 + index}:${topLast}${6 + index}`;
  top.getRange(address).merge();
  top.getRange(`A${6 + index}`).values = [[banner.text]];
  top.getRange(address).format.wrapText = true;
  top.getRange(address).format.rowHeight = banner.height;
  top.getRange(address).format.font = { name: font, size: 11, bold: Boolean(banner.bold), color: banner.color };
  if (banner.fill) top.getRange(address).format.fill = banner.fill;
});
writeRows(top, tableRow, topRows, false);
base(all, `A1:${col(headers.length + 1)}${allRows.length + 1}`);
writeRows(all, 1, allRows, true);
all.freezePanes.freezeRows(1);
const warnings = Array.isArray(meta.warnings) ? meta.warnings : meta.warnings ? [meta.warnings] : [];
const gateText = preopen ? String(gate?.label ?? '前一晚美股漲跌未知') : '不適用（本次 H1 未使用開盤前版）';
const modelRows = preopen ? [
  ['H1 顯示模型', String(preopenMeta.describe ?? sourceNote)],
  ['H1 模型來源', `開盤前版（設定：${preopenStatus.requested_model_source ?? data.requested_model_source ?? 'auto'}；C0 保留為備援）`],
  ['前一晚美股（Top10 閘門）', gateText],
  ['開盤前版訊號日／目標日', `${day8(preopenMeta.signal_date)} → ${day8(preopenMeta.target_date)}`],
  ['開盤前版 run_id', String(preopenMeta.run_id)],
  ['開盤前版模型 SHA-256', String(preopenMeta.model_sha256)],
  ['開盤前版快照 SHA-256', String(preopenMeta.snapshot_sha256)],
  [String(disclosure.title), disclosure.lines.join('\n')],
  ['揭露資料出處', String(disclosure.source ?? '未提供')],
  ['C0 備援排序狀態', human(context.c0_ranking_note, 'C0 排序狀態（原始紀錄保留）')],
] : [
  ['H1 模型來源', sourceNote],
  ...(preopenStatus.status === 'unavailable'
    ? [['開盤前版不可用原因代碼', `${preopenStatus.reason ?? '未提供'}${preopenStatus.detail ? `（${preopenStatus.detail}）` : ''}`]] : []),
];
const detail = [
  [preopen ? 'H1 模型與資料' : '現役C0版本與資料', '內容'],
  ...modelRows,
  ...dataTime.rows.map(([label, value]) => [String(label), value === null || value === undefined || value === '' ? '未提供' : String(value)]),
  ['行情基準日', date(context.base_session ?? meta.asof)], ['下一交易日', date(context.target_session)],
  ...(baseByMarket ? [['各市場行情基準日（各市場以自己的資料日分析）', baseByMarket]] : []),
  ...(targetByMarket ? [['各市場下一交易日', targetByMarket]] : []),
  ['未來日曆狀態', human(context.future_calendar_status)], ['來源狀態', human(meta.raw_status)],
  ['目標與報酬單位', 'H1為下一個市場交易日收盤；預測前一交易日收盤至該日收盤的調整後報酬。原始資料及CSV保留小數，百分比僅為顯示換算。'],
  ['資料窗', '各市場固定253個已證交易日；停牌、缺價或無效值不往更早日期移位補足。'],
  ['已分析股票數', predictions.length], ['未分析／排除股票數', missing.length],
  [preopen ? 'C0 模型版本（備援）' : '現役模型版本', shown(context.model_version ?? model.model_version)],
  ['方向模型', human(context.selected_direction_kind, '其他方向模型（識別見既有資料）')], ['報酬模型', human(context.selected_return_kind, '其他報酬模型（識別見既有資料）')],
  ['排序狀態', human(context.ranking_note)], ['機率校準', human(context.calibration_status)],
  ['前瞻校準', forwardCalibration()],
  ['正式與研究資格', '正式資格未確認；歷史成員與PIT未知不得升格。'],
  ['模型與交易效果', preopen ? `保留期驗證見必要揭露；命中率不代表交易獲利；隔日${tradingEffect}。` : '效果未定；交易效益未驗。'],
  ['當前身分核對日期', shown(context.ordinary_identity_verified_asof)],
  ['當前身分觀測時間', shown(context.ordinary_identity_observed_at)],
  ['歷史成員狀態', human(meta.historical_membership_status ?? 'unknown')],
  ['資料公開可用時間（PIT）狀態', human(meta.pit_status ?? 'unknown')],
  ['公司行動狀態', human(meta.corporate_actions_status ?? 'unknown')],
  ['來源提醒', warnings.map(value => human(value, '資料提醒（原始紀錄保留）')).join('；') || '無新增提醒'],
];
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
const lineageRows = [['資料來源與限制', '說明'],
  ['H1 模型來源', preopen ? `開盤前版（${String(preopenMeta.describe ?? '')}）` : sourceNote],
  ['來源狀態', human(meta.raw_status)],
  ['下一交易日日曆', human(context.future_calendar_status)],
  ['歷史股票成員', human(meta.historical_membership_status ?? 'unknown')],
  ['資料公開可用時間（PIT）', human(meta.pit_status ?? 'unknown')],
  ['公司行動資料', human(meta.corporate_actions_status ?? 'unknown')],
  ['未提供欄位', fieldsMissing.length ? fieldsMissing.map(field => human(field, '其他必要欄位')).join('、') : '無'],
  ['模型與資料識別', '完整紀錄保留於原始報告資料檔（report.json）。'],
  ['前瞻校準', forwardCalibration()],
  ['使用限制', '僅供研究；正式資格、排序效果與交易效益尚未確認。資料部分完成或未確認時，不表示限制已解除。'
    + (preopen ? `命中率指收盤報價方向，不代表交易獲利；隔日${tradingEffect}。` : '')],
  ['原始報告資料', '完整紀錄保留於原始報告資料檔（report.json）；此處不重複展開技術內容。']];
writeDetails(lineage, lineageRows);
wb.recalculate();
if (verify) {
  const checkDir = process.env.TOP10_EXCEL_CHECK_DIR;
  if (!checkDir || !(await fs.stat(checkDir)).isDirectory()) throw new Error('驗證需要TOP10_EXCEL_CHECK_DIR指向既有資料目錄。');
  const inspection = { prediction_rows: predictions.length, coverage_rows: coverage.length,
    workbook_rows: allRows.length, headers: [...headers, '分析狀態／排除原因'],
    return_unit: data.return_unit, ranking_status: context.ranking_status, model_source: modelSource, ranges: [] };
  const views = [['研究展示', `A1:${topLast}${tableRow + topRows.length}`],
    ['全股票預測', `A1:${col(headers.length + 1)}${Math.min(allRows.length + 1, 12)}`],
    ['版本與資料', `A1:B${detail.length}`], ['資料來源與限制', `A1:B${Math.min(lineageRows.length, 12)}`]];
  for (let index = 0; index < views.length; index++) {
    const [sheetName, range] = views[index];
    const inspected = await wb.inspect({ kind: 'table', range: `'${sheetName}'!${range}`, include: 'values,formulas',
      tableMaxRows: 8, tableMaxCols: headers.length + 1, maxChars: 2400 });
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
