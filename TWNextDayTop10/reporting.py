"""H1 次日報告（JSON/HTML/CSV/XLSX/manifest）。

H1 顯示模型由 report_context["model_source"] 決定：preopen（開盤前版，依可交易母體 p_up→exp_ret 取 Top10）
或 c0（現役 C0，含回退）；C0 契約綁定在兩種模式都保留並驗證。共同前瞻狀態由共用模組負責。
"""
import csv
import hashlib
import html
import json
import math
import os
import re
import shutil
import subprocess
import uuid
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path
from zoneinfo import ZoneInfo

import numpy as np
import pandas as pd
from date_contract import DATE_ENCODING, date8, iso_day, report_dates


COLUMNS = [
    ("display_order", "展示序號"), ("stock_id", "股票代碼"),
    ("name", "股票名稱"), ("market", "市場"),
    ("probability", "下一交易日收漲機率"), ("expected_return", "下一交易日調整後收盤報酬"),
]
NO_STOCK_RANKING_STATUSES = {
    "all_tied_baseline", "market_no_stock_signal",
    "market_baseline_no_stock_discrimination", "all_tied_predictions",
}
PREOPEN_RANKING_STATUS = "preopen_model_ranking"
PREOPEN_RANKING_POLICY = "preopen_tradable_top10_p_up_then_exp_ret"
PREOPEN_COLUMNS = [("tradable", "可交易"), ("rank_tradable", "可交易名次")]
DATA_COLUMNS = [("model_source_text", "模型來源"), ("data_date_text", "資料日"),
                ("target_date_text", "本股目標日"), ("data_reminder", "資料提醒")]
PREOPEN_META_REQUIRED = ("run_id", "model_sha256", "snapshot_sha256")
# 開盤前版「開盤買進」效果片語由 model_disclosure.trading_effect 提供（preopen_source 依快照配方選取）；
# 舊 payload／context 沒有此欄位時沿用配方 v1 原文。
PREOPEN_TRADING_EFFECT_V1 = "開盤買進 Top10 保留期平均為負"
# 開盤前版排序說明（preopen_source.PREOPEN_RANKING_NOTE）的命中率子句；報告只在必要揭露與「使用限制」寫出。
PREOPEN_HIT_RATE_NOTE = "命中率指收盤報價方向，不代表交易獲利"
# 同一排序說明的排序規則子句；HTML 的 Top10 標題已寫（依收漲機率→預估報酬），本次摘要不重複。
PREOPEN_RANKING_RULE = "可交易母體依收漲機率→預估報酬排序取前十"
# 資料時間表（HTML；excel_report.mjs 的 displayTime 同樣篩選）：流程時間戳與缺漏欄位（見資料來源與限制）不列；
# 價格最新日、模型訓練資料日只在和分析資料日不同時列（模型訓練資料日未提供時也不列）；發布時間未宣告、沒有版本標籤時不列。
TIME_ROWS_HIDDEN = ("價格收集時間（最新）", "本次讀取時間", "模型啟用時間", "缺漏欄位")
TIME_ROWS_IF_NOT_ANALYSIS_DAY = ("價格最新日", "模型訓練資料日")
TIME_ROWS_IF_PROVIDED = ("模型訓練資料日",)
TIME_PUBLISHED_LABEL = "head 發布時間（producer 宣告，未驗證）"
# 未確認類的佔位值；發布時間是這類值時視同未宣告。
UNKNOWN_VALUES = {"unknown", "none", "unverified", "not_yet_verified"}
# data_reader 寫入 meta.warnings 的固定 C0 限制句；內容已在資料來源與限制表（歷史成員、PIT、公司行動、使用限制）。
C0_LIMITATION_WARNING = "C0 僅調整後收盤方向與報酬；歷史身分、PIT、公司行動與交易效益未驗。"
MODEL_SOURCES = ("c0", "preopen")
MODEL_SOURCE_FIELDS = ("model_source", "requested_model_source", "model_source_origin", "preopen_status",
                       "preopen_meta", "preopen_gate", "model_source_note", "model_disclosure")
BINDING_FIELDS = (
    "current_policy_binding", "original_model_feature_contract",
    "contract_adoption_ref", "effective_C0_semantics_projection_sha256",
    "model_activation_epoch",
)
SEALED_FIELDS = (*BINDING_FIELDS, "daily_requirements_binding", "actual_model_inputs",
                 "forward_binding", "active_model_binding", "training_scope",
                 "training_by_horizon", "target_definition", "c0_source_binding",
                 "shared_input_descriptor", "ordinary_identity_binding",
                 "required_route_evidence", "readiness", "data_time", "version_labels", "preopen_meta")
TAIPEI = ZoneInfo("Asia/Taipei")
MARKET_TEXT = {"TWSE": "上市", "TPEX": "上櫃"}
FRESHNESS_TEXT = {
    "latest": "最新（依已驗證日曆）", "stale": "非最新資料",
    "calendar_unverified_after": "已驗證日曆之後的交易日未驗證，無法判定是否最新",
}
DISPLAY_TEXT = {
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
    # 共用 C0 契約與本程式的版本標籤（同 single_stock_analysis 的 _REPORT_LABEL_ZH；各市場資料日只陳述差異，
    # 不寫分析方式：開盤前版以快照訊號日為準，C0 才逐市場分析）。
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
}


def _human(value, fallback="其他狀態（原始紀錄保留）"):
    """Translate presentation values only; never rewrite the report payload."""
    if isinstance(value, (list, tuple)):
        return "；".join(_human(item, fallback) for item in value) or "未提供"
    if value is None or value == "":
        return "未提供"
    if isinstance(value, bool):
        return "是" if value else "否"
    if isinstance(value, (dict, set)):
        return "詳見既有原始資料"
    text = str(value).strip()
    translated = DISPLAY_TEXT.get(text) or DISPLAY_TEXT.get(text.lower())
    if translated:
        return translated
    if text.startswith("版本標籤："):
        code = text[len("版本標籤："):].split("（", 1)[0].strip()
        return "版本提醒：" + _human(code, "版本標示有差異")
    if re.search(r"[\u3400-\u9fff]", text):
        return re.sub(r"\b[A-Za-z][A-Za-z0-9_]*\b",
                      lambda match: DISPLAY_TEXT.get(match.group()) or DISPLAY_TEXT.get(match.group().lower())
                      or (fallback if "_" in match.group() else match.group()), text)
    if re.fullmatch(r"[0-9.,%+ /:\-]+", text):
        return text
    return fallback


def _display_time(payload):
    """A detached view keeps machine data_time_display and version codes intact."""
    source = payload.get("data_time_display") or data_time_display(None, payload.get("generated_at"), [], ["data_time"])
    display = dict(source)
    analysis = dict(source["rows"]).get("分析資料日")
    # 本次摘要已逐市場列出分析日（C0 各市場以自己的資料日分析）時，表內不再重複同一段各市場資料日。
    summary_markets = market_days_text((payload.get("report_context") or {}).get("base_session_by_market"))
    rows = []
    for label, value in source["rows"]:
        if (label in TIME_ROWS_HIDDEN or label in TIME_ROWS_IF_NOT_ANALYSIS_DAY and value == analysis
                or label in TIME_ROWS_IF_PROVIDED and value in (None, "", "未提供")):
            continue
        if label == "各市場資料日" and summary_markets and value == summary_markets:
            continue
        if label == "版本標籤":
            codes = sorted({str(item.get("code")) for item in payload.get("version_labels") or [] if isinstance(item, dict)})
            if not codes:
                continue
            # 已知代碼寫中文（同義合併）；沒有中文的代碼只寫項數，原始代碼保留於原始報告資料。
            known = list(dict.fromkeys(DISPLAY_TEXT[code] for code in codes if code in DISPLAY_TEXT))
            unknown = sum(code not in DISPLAY_TEXT for code in codes)
            other = ("其他" if known else "") + "版本標示有差異（" + str(unknown) + " 項）"
            label, value = "版本提醒", "、".join(known + ([other] if unknown else []))
        elif label == TIME_PUBLISHED_LABEL:
            if value in (None, "", "未提供") or str(value).lower() in UNKNOWN_VALUES:
                continue
            label = "資料發布時間（提供者宣告，未驗證）"
        elif isinstance(value, str) and value.lower() in UNKNOWN_VALUES:
            value = _human(value)
        rows.append([label, value])
    display["rows"] = rows
    return display


def _forward_calibration(context):
    values = context.get("forward_calibration_versions") or []
    values = values if isinstance(values, (list, tuple)) else [values]
    recorded = [str(value).strip() for value in values if value is not None and str(value).strip().lower() not in {"", "none", "null"}]
    if not recorded:
        return "未套用"
    if all(value.lower() in {"unknown", "unverified"} for value in recorded):
        return "是否套用尚未確認"
    return "已記錄（不等於可靠性已驗證）"


def _preopen_trading_effect(container):
    """開盤前版的開盤買進效果片語（payload 或 context 的 model_disclosure.trading_effect；缺漏時為 v1 原文）。"""
    disclosure = container.get("model_disclosure") if isinstance(container, dict) else None
    phrase = disclosure.get("trading_effect") if isinstance(disclosure, dict) else None
    return str(phrase) if phrase else PREOPEN_TRADING_EFFECT_V1


def _limitation_rows(payload):
    """H1 模型來源只寫在模型來源區；沒有未提供欄位時不列該列。"""
    context, meta = payload["report_context"], payload["meta"]
    missing = payload.get("report_fields_missing") or []
    return [
        ["來源狀態", _human(meta.get("raw_status"))],
        ["下一交易日日曆", _human(context.get("future_calendar_status"))],
        ["歷史股票成員", _human(meta.get("historical_membership_status", "unknown"))],
        ["資料公開可用時間（PIT）", _human(meta.get("pit_status", "unknown"))],
        ["公司行動資料", _human(meta.get("corporate_actions_status", "unknown"))],
        *([["未提供欄位", "、".join(_human(field, "其他必要欄位") for field in missing)]] if missing else []),
        ["前瞻校準", _forward_calibration(context)],
        ["使用限制", "僅供研究；正式資格、排序效果與交易效益尚未確認。資料部分完成或未確認時，不表示限制已解除。"
                    + (PREOPEN_HIT_RATE_NOTE + "；隔日" + _preopen_trading_effect(payload) + "。"
                       if payload.get("model_source") == "preopen" else "")],
    ]


def _model_source_text(payload):
    """模型來源的一行說明（Excel 端讀 payload 的 model_source_note，取得同一文字）。"""
    note = payload.get("model_source_note")
    return str(note) if note else "H1 模型來源：C0"


def clean(value):
    if isinstance(value, dict):
        return {str(k): clean(v) for k, v in value.items()}
    if isinstance(value, (list, tuple, np.ndarray)):
        return [clean(v) for v in value]
    if isinstance(value, np.generic):
        return clean(value.item())
    if isinstance(value, float) and not math.isfinite(value):
        return None
    if isinstance(value, (datetime, pd.Timestamp, Path)):
        return str(value)
    return value


def write_json(path, value):
    path.write_text(json.dumps(clean(value), ensure_ascii=False, indent=2, allow_nan=False), encoding="utf-8")


def atomic_json(path, value):
    temp = path.with_name(path.name + "." + uuid.uuid4().hex + ".tmp")
    write_json(temp, value)
    temp.replace(path)


def excel_runtime(project):
    node = Path.home() / ".cache" / "codex-runtimes" / "codex-primary-runtime" / "dependencies" / "node" / "bin" / "node.exe"
    builder = project / "excel_report.mjs"
    artifact = project / "node_modules" / "@oai" / "artifact-tool"
    for path in (node, builder, artifact):
        if not path.exists():
            raise RuntimeError(f"Excel 報告元件缺少：{path}；請恢復既有 Node 與試算表元件，本程式不會自動下載或安裝。")
    return node, builder


def flat_report_path(project, target_session, *, analysis_asof=None, profile="C0"):
    if profile != "C0":
        raise ValueError("報告僅支援現役 C0")
    target = date8(target_session) if target_session else None
    filename = target + ".xlsx" if target else date8(analysis_asof) + "_H1_target_unverified.xlsx"
    override = os.environ.get("TOP10_VISIBLE_REPORT_ROOT")
    if override:
        root = Path(override)
        if not root.is_absolute():
            raise ValueError("TOP10_VISIBLE_REPORT_ROOT 必須為絕對路徑")
        return root / filename
    return Path(project) / "data" / "reports" / filename


def _copy_visible_report(source, destination, run_id):
    destination = Path(destination)
    destination.parent.mkdir(parents=True, exist_ok=True)
    temporary = destination.with_name(f".{destination.name}.{run_id}.tmp")
    created = False
    try:
        with Path(source).open("rb") as src:
            with temporary.open("xb") as dst:
                created = True
                shutil.copyfileobj(src, dst, length=1024 * 1024)
                dst.flush()
                os.fsync(dst.fileno())
        if temporary.stat().st_size != Path(source).stat().st_size:
            raise RuntimeError("使用者報告副本大小不符；保留既有日期報告。")
        temporary.replace(destination)
    finally:
        if created and temporary.exists():
            temporary.unlink()


def _utcnow():
    return datetime.now(timezone.utc)


def _canonical(value):
    return json.dumps(clean(value), ensure_ascii=False, sort_keys=True,
                      separators=(",", ":"), allow_nan=False).encode("utf-8")


def _hash_file(path):
    digest = hashlib.sha256()
    with Path(path).open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _write_new_json(path, value):
    with Path(path).open("xb") as handle:
        handle.write(_canonical(value))


def _text(value):
    if value is None or isinstance(value, (dict, list, tuple)):
        return "未提供"
    return str(value) if str(value).strip() not in ("", "None", "null", "[]") else "未提供"


def _display(row, key):
    value = row.get(key)
    if key == "market":
        return MARKET_TEXT.get(str(value).upper(), "未確認市場")
    if key in {"probability", "expected_return"}:
        try:
            number = float(value)
            return f"{number * 100:.2f}%" if math.isfinite(number) else "未提供"
        except (ValueError, TypeError):
            return "未提供"
    if key == "tradable":
        return "是" if value is True else "否" if value is False else "未提供"
    if key == "rank_tradable":
        try:
            number = float(value)
            return str(int(number)) if math.isfinite(number) else "—"
        except (ValueError, TypeError):
            return "—"
    return _text(value)


def _columns(payload):
    return COLUMNS + (PREOPEN_COLUMNS if payload.get("model_source") == "preopen" else []) + DATA_COLUMNS


def _top10_columns(payload):
    """Top10 表欄位與表上方共同說明（excel_report.mjs 同規則）。

    可交易／可交易名次只留在全部股票表（Top10 前段必為可交易、名次即展示序號）；全部同值的模型來源欄省略（來源見標題）；
    開盤前版的資料日／本股目標日／資料提醒各檔全部相同時改為表上方一行，C0 各市場日期可能不同，一律保留欄位。
    """
    rows = payload["top10"]
    uniform = [item for item in DATA_COLUMNS if rows and len({_display(row, item[0]) for row in rows}) == 1
               and (item[0] == "model_source_text" or payload.get("model_source") == "preopen")]
    parts = [("" if key == "data_reminder" else label + " ") + _display(rows[0], key)
             for key, label in uniform if key != "model_source_text"]
    return [item for item in COLUMNS + DATA_COLUMNS if item not in uniform], ("各檔共同：" + "；".join(parts) + "。" if parts else "")


def _row_data_display(row, context):
    """One display projection shared by JSON, HTML, CSV and the Excel builder."""
    source = row.get("model_source") or context.get("model_source", "c0")
    supplement = source == "c0" and context.get("model_source") == "preopen"
    analysis = row.get("analysis_asof") or context.get("analysis_asof") or context.get("base_session")
    feature = row.get("feature_data_date")
    if source != "preopen":
        feature = feature or row.get("stock_last_price_date") or row.get("data_asof") or row.get("date")
    reminder = []
    if source == "preopen" and not feature:
        signal = row.get("snapshot_signal_date") or row.get("date")
        data_text = _day(signal) + "（快照訊號日）" if signal else "未提供"
        reminder.append("快照未提供逐股行情日期")
    else:
        data_text = _day(feature) if feature else "未提供"
    lagged = row.get("data_lagged") is True
    if feature and analysis:
        lagged = lagged or iso_day(feature) < iso_day(analysis)
    if lagged:
        lag = row.get("data_lag_sessions")
        count = str(int(lag)) + "交易日" if isinstance(lag, (int, float)) and math.isfinite(lag) else "（交易日數未提供）"
        reminder.append("資料落後" + count + "，依既有資料分析")
    if row.get("in_sample_lookahead") is True:
        reminder.append("歷史資料試算／模型訓練晚於資料日")
    # 快照未收錄、改用 C0 補充分析已由模型來源欄（C0補充分析）表示，資料提醒不再重複。
    target = row.get("target_session_date")
    target_text = _day(target) if target else "未驗證"
    if target and analysis and iso_day(target) <= iso_day(analysis):
        target_text += "（歷史）"
    elif target and source == "preopen" and context.get("calendar_verified") is not True:
        target_text += "（快照宣告）"
    return {"model_source_text": "開盤前版" if source == "preopen" else "C0補充分析" if supplement else "C0",
            "data_date_text": data_text, "target_date_text": target_text,
            "data_reminder": "；".join(reminder) if reminder else "依本股資料日分析"}


def _missing(value):
    return value is None or value == {} or value == ""


def _current_context(context, meta, model):
    """H1 契約：C0 綁定（資料／政策 profile）一律驗證；H1 顯示模型可為 C0 或開盤前版。"""
    if context.get("effective_profile") != "C0" or context.get("horizon", 1) != 1:
        raise ValueError("current_report_requires_C0_H1")
    if context.get("price_basis") != "adjusted_close_return":
        raise ValueError("current_report_requires_adjusted_close_return")
    source = context.setdefault("model_source", "c0")
    if source not in MODEL_SOURCES:
        raise ValueError("current_report_model_source_invalid:" + str(source))
    preopen = source == "preopen"
    if preopen:
        info = context.get("preopen_meta")
        if not isinstance(info, dict) or any(not info.get(key) for key in PREOPEN_META_REQUIRED):
            raise ValueError("current_report_preopen_meta_missing")
        if context.get("ranking_status") != PREOPEN_RANKING_STATUS:
            raise ValueError("current_report_preopen_ranking_status_mismatch")
    elif context.get("ranking_status") == PREOPEN_RANKING_STATUS:
        raise ValueError("current_report_preopen_ranking_without_preopen_source")
    # A binding that was not provided is shown as「未提供」(report_fields_missing);
    # two provided copies that disagree are still a report self-consistency error.
    missing = list(context.get("report_fields_missing") or [])
    for key in BINDING_FIELDS:
        if _missing(context.get(key)):
            context[key] = None
            if key not in missing:
                missing.append(key)
            continue
        for container in (meta, model):
            if key in container and not _missing(container[key]) and clean(container[key]) != context[key]:
                raise ValueError("current_report_binding_mismatch:" + key)
    for key in ("current_policy_binding", "original_model_feature_contract", "contract_adoption_ref"):
        if context[key] is not None and not isinstance(context[key], dict):
            raise ValueError("current_report_binding_invalid:" + key)
    context["report_fields_missing"] = missing
    # Shared validation owns source/model/ref verification. Rendering preserves
    # the actual bindings, rather than inventing or recomputing adoption hashes.
    context.update(horizon=1, horizon_unit="market_trading_session", formal_qualified=False,
                   calibration_status=context.get("calibration_status", "未校準"),
                   effect_status="保留期驗證見必要揭露；正式前瞻效果未定" if preopen else "效果未定",
                   trading_effect_status=(_preopen_trading_effect(context) + "；交易效益未證") if preopen else "交易效益未驗")
    return context


def _comparison_module():
    """Load the shared report-only API lazily so predictions survive its absence."""
    from daily_contract import shared_module
    return shared_module(Path(r"D:\stock\programs\shared\stock_report_comparison.py"),
                         "top10_shared_report_comparison")


def _attach_report_comparisons(payload, meta, context):
    """One Top10 batch on the analysis day/head; never change model values/order."""
    stock_ids = list(dict.fromkeys(str(row["stock_id"]) for row in payload["top10"]))
    if not stock_ids:
        payload["report_comparison"] = payload["meta"]["report_comparison"] = {}
        return
    data_time = meta.get("data_time") or context.get("data_time") or {}
    descriptor = meta.get("shared_input_descriptor") or context.get("shared_input_descriptor") or {}
    sources = [source for source in descriptor.get("sources", []) if isinstance(source, dict)]
    head_sha = (data_time.get("head") or {}).get("sha256")
    if not isinstance(head_sha, str) or not re.fullmatch(r"[0-9a-fA-F]{64}", head_sha):
        head_sha = next((source["manifest_sha256"] for source in sources
                         if isinstance(source.get("manifest_sha256"), str)
                         and re.fullmatch(r"[0-9a-fA-F]{64}", source["manifest_sha256"])), None)
    head_sha = head_sha.lower() if head_sha else None
    central_root = next((source.get("root") for source in sources
                         if source.get("root") and (not head_sha
                         or str(source.get("manifest_sha256", "")).lower() == head_sha)), None)
    signal_date = meta.get("asof") or data_time.get("analysis_asof") or context.get("analysis_asof") or context.get("base_session")
    module, comparisons, error = None, {}, None
    try:
        signal_date = iso_day(signal_date)
        module = _comparison_module()
        comparisons = module.build_comparisons(stock_ids, signal_date, central_root=central_root, head_sha=head_sha)
        if not isinstance(comparisons, dict):
            raise ValueError("report_comparison_batch_not_a_mapping")
    except Exception as exc:
        error = "比較不可得（" + type(exc).__name__ + ": " + str(exc) + "）"

    blocks = {}
    for row in payload["top10"]:
        stock_id = str(row["stock_id"])
        value = comparisons.get(stock_id) if isinstance(comparisons, dict) else None
        if isinstance(value, dict) and str(value.get("stock_id")) == stock_id:
            block = clean(value)
            block.setdefault("schema", "stock_report_comparison_v1")
            block.setdefault("signal_date", signal_date)
            block.setdefault("source_head_sha256", None)
            block.setdefault("metrics", [])
            notes = block.get("notes") or []
            block["notes"] = [str(note) for note in notes] if isinstance(notes, list) else [str(notes)]
        else:
            block = {"schema": "top10_report_comparison_unavailable_v1", "stock_id": stock_id,
                     "signal_date": signal_date, "source_head_sha256": None, "metrics": [],
                     "status": "unavailable", "notes": [error or "比較不可得：本股未回傳有效歷史比較資料。"]}
        block["analysis_source_head_sha256"] = head_sha
        actual_head = block.get("source_head_sha256")
        block["analysis_head_match"] = str(actual_head).lower() == head_sha if head_sha and actual_head else None
        block["source_pin_mode"] = ("analysis_head_requested" if head_sha else
                                    "new_comparison_pin" if actual_head else "new_comparison_pin_unavailable")
        if not head_sha:
            block["notes"].append("本次分析未提供可用 head；" +
                ("比較資料另行釘選來源" if actual_head else "比較釘選來源尚不可得") + "，不宣稱與模型同 head。")
        elif block["analysis_head_match"] is not True:
            block["notes"].append("比較來源未證實與本次分析 head 相同；請以比較來源識別及資料日期為準。")
        unavailable = [{"項目": "歷史比較", "資料期間": "截至分析日 " + _day(signal_date),
                        "本期值": "未提供", "比較": "比較不可得", "解讀": "；".join(block["notes"]), "資料日期": "未提供"}]
        display_rows, summary = unavailable, ["比較不可得；原預測結果保留。"]
        if isinstance(value, dict) and block.get("status") != "unavailable":
            try:
                rendered = clean(module.comparison_rows(block))
                if not isinstance(rendered, list) or any(not isinstance(item, dict) for item in rendered):
                    raise ValueError("report_comparison_rows_invalid")
                display_rows = rendered or unavailable
            except Exception as exc:
                block["notes"].append("比較詳表不可得（" + type(exc).__name__ + ": " + str(exc) + "）；原始比較資料保留。")
            try:
                rendered = clean(module.comparison_summary(block, max_items=3))
                if not isinstance(rendered, list) or any(not isinstance(item, str) for item in rendered):
                    raise ValueError("report_comparison_summary_invalid")
                summary = rendered or ["請參閱歷史比較詳表及資料限制。"]
            except Exception as exc:
                summary = ["中文摘要不可得；原始比較資料保留。"]
                block["notes"].append("中文摘要不可得（" + type(exc).__name__ + ": " + str(exc) + "）；原始比較資料保留。")
        blocks[stock_id] = block
        row.update(report_comparison=block, report_comparison_rows=display_rows,
                   report_comparison_summary=summary)
    payload["report_comparison"] = payload["meta"]["report_comparison"] = blocks


def prepare_payload(predictions, coverage, meta, model, now, report_context=None, performance_state=None):
    """Order once for all formats without claiming stock selection advantage."""
    context = _current_context(clean(dict(report_context or {})), meta, model)
    required = {"stock_id", "market", "probability", "expected_return"}
    if not required.issubset(predictions.columns) or predictions.empty:
        raise ValueError("current_report_prediction_columns_or_rows_missing")
    values = predictions[["probability", "expected_return"]].apply(pd.to_numeric, errors="coerce")
    if not np.isfinite(values.to_numpy(dtype=float)).all() or not values.probability.between(0, 1).all():
        raise ValueError("current_report_prediction_values_invalid")
    preopen = context.get("model_source") == "preopen"
    no_signal = not preopen and (context.get("ranking_status") in NO_STOCK_RANKING_STATUSES
                                 or context.get("no_stock_specific_model_signal") is True)
    if preopen:
        rows, top10 = _preopen_rows(predictions)
    else:
        order = ["market", "stock_id"] if no_signal else ["probability", "expected_return", "market", "stock_id"]
        ascending = [True, True] if no_signal else [False, False, True, True]
        displayed = predictions.sort_values(order, ascending=ascending, kind="mergesort")
        rows = clean(displayed.to_dict("records"))
        for index, row in enumerate(rows, 1):
            row["display_order"] = index
        top10 = rows[:10]
    if no_signal and not context.get("ranking_note"):
        context["ranking_note"] = "無個股排序訊號；市場及代碼只用於穩定展示"
    for row in rows:
        row.update(_row_data_display(row, context))
    payload = clean({"schema": "tw_nextday_top10_report_payload_v1", "date_encoding": DATE_ENCODING,
        "engine_id": context.get("engine_id", "tw_nextday_top10"), "run_id": context.get("run_id"),
        "predictions": rows, "top10": top10, "coverage": coverage.to_dict("records"),
        "meta": meta, "model": model, "generated_at": now.isoformat(), "report_context": context,
        "research_only": True, "forward_state": {"status": "research_effect_unknown",
            "binding": context.get("forward_binding", {}), "effect_status": "unknown",
            "trading_effect_status": "unverified"},
        "ranking_policy": PREOPEN_RANKING_POLICY if preopen
            else "stable_market_code_display_only_no_stock_ranking_signal" if no_signal
            else "probability_return_display_order_effect_unknown",
        "return_unit": "decimal_adjusted_close_to_close_return",
        **{key: context.get(key) for key in MODEL_SOURCE_FIELDS}})
    data_time = payload["meta"].get("data_time") or context.get("data_time")
    labels = payload["meta"].get("version_labels") or context.get("version_labels") or []
    missing = list(context.get("report_fields_missing") or [])
    if not isinstance(data_time, dict) and "data_time" not in missing:
        missing.append("data_time")
    payload.update(data_time=data_time, version_labels=labels, report_fields_missing=missing,
                   data_time_display=data_time_display(data_time, now, labels, missing))
    result = report_dates(payload)
    # Pure-day display conversion must not alter sealed scientific or policy
    # containers that refer to independently verified source bytes.
    for key in ("report_context", "meta", "model"):
        for field in SEALED_FIELDS:
            if field in payload[key]:
                result[key][field] = payload[key][field]
    result["forward_state"]["binding"] = payload["forward_state"]["binding"]
    for key in ("data_time", "version_labels", "data_time_display", "preopen_meta"):
        result[key] = payload[key]
    _attach_report_comparisons(result, meta, context)
    return result


def _preopen_rows(predictions):
    """開盤前版：依 app 決定的 preopen_display_rank 排列；前段必為可交易 Top10（最多 10 檔）。"""
    if not {"preopen_display_rank", "tradable"}.issubset(predictions.columns):
        raise ValueError("current_report_preopen_columns_missing")
    ranks = pd.to_numeric(predictions["preopen_display_rank"], errors="coerce")
    if ranks.isna().any() or sorted(ranks.astype(int)) != list(range(1, len(predictions) + 1)):
        raise ValueError("current_report_preopen_display_rank_invalid")
    displayed = predictions.assign(_order=ranks.astype(int)).sort_values("_order", kind="mergesort").drop(columns="_order")
    tradable = [(value is True or isinstance(value, (int, float)) and value == 1)
                and source != "c0" for value, source in zip(clean(displayed["tradable"].tolist()),
                    displayed.get("model_source", pd.Series("preopen", index=displayed.index)).tolist())]
    expected = min(10, sum(tradable))
    if tradable[:expected] != [True] * expected:
        raise ValueError("current_report_preopen_top10_not_tradable_first")
    leading = displayed.iloc[:expected]
    keys = list(zip(-leading["probability"].astype(float), -leading["expected_return"].astype(float),
                    leading["market"].astype(str).str.upper(), leading["stock_id"].astype(str)))
    if keys != sorted(keys):
        raise ValueError("current_report_preopen_top10_order_invalid")
    rows = clean(displayed.to_dict("records"))
    for index, row in enumerate(rows, 1):
        row["display_order"] = index
    return rows, rows[:expected]


def _day(value):
    if value is None or value == "":
        return "未提供"
    try:
        return date8(str(value)[:10]) if isinstance(value, str) else date8(value)
    except (TypeError, ValueError):
        return _text(value)


def _clock(value):
    """Instants shown as Taipei clock time; a missing value is「未提供」."""
    if value is None or value == "":
        return "未提供"
    try:
        moment = value if isinstance(value, datetime) else datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except ValueError:
        return _text(value)
    if moment.tzinfo is None:
        return moment.strftime("%Y%m%d %H:%M:%S") + "（時區未提供）"
    return moment.astimezone(TAIPEI).strftime("%Y%m%d %H:%M:%S") + " 台北"


def data_time_display(data_time, generated_at, labels, missing):
    """One rendering of the data time for HTML and Excel (excel_report.mjs uses these rows)."""
    value = data_time if isinstance(data_time, dict) else {}
    by_market = value.get("asof_by_market") if isinstance(value.get("asof_by_market"), dict) else {}
    head = value.get("head") if isinstance(value.get("head"), dict) else {}
    model = value.get("model") if isinstance(value.get("model"), dict) else {}
    markets = "／".join(MARKET_TEXT.get(str(market).upper(), str(market)) + " " + _day(day)
                        for market, day in sorted(by_market.items())) or "未提供"
    stale, older = value.get("is_stale") is True, value.get("is_older_than_requested") is True
    freshness = FRESHNESS_TEXT.get(value.get("freshness_status"), "未提供")
    if stale:
        freshness += "（依已驗證日曆最近應有交易日 " + _day(value.get("latest_expected_session")) + "）"
    lagging = value.get("stale_markets") if isinstance(value.get("stale_markets"), dict) else {}
    if stale and lagging:
        # A lagging market is analysed with its own day (not dropped); say which one.
        freshness += "；落後市場：" + "／".join(
            MARKET_TEXT.get(str(market).upper(), str(market)) + " 資料日 " + _day(pair[0]) + "，應有 " + _day(pair[1])
            for market, pair in sorted(lagging.items()) if isinstance(pair, (list, tuple)) and len(pair) == 2)
    if older:
        freshness += "；早於要求日 " + _day(value.get("requested_asof"))
    codes = [str(label.get("code")) for label in labels or [] if isinstance(label, dict)]
    unique = sorted(set(codes))
    analysis, published = _day(value.get("analysis_asof")), _clock(head.get("published_at"))
    generated = _clock(generated_at)
    rows = [
        ["分析資料日", analysis], ["各市場資料日", markets],
        ["價格最新日", _day(value.get("price_max_date"))],
        ["價格收集時間（最新）", _clock(value.get("price_collected_at_max"))],
        ["head 發布時間（producer 宣告，未驗證）", published],
        ["本次讀取時間", _clock(value.get("read_at_utc"))], ["報告產生時間", generated],
        ["日曆已驗證至", _day(value.get("calendar_verified_asof"))],
        ["模型訓練資料日", _day(model.get("training_asof"))], ["模型啟用時間", _clock(model.get("activated_at_utc"))],
        ["資料新鮮度", freshness],
        ["版本標籤", str(len(unique)) + " 個" + ("：" + "、".join(unique) if unique else "")],
        ["缺漏欄位", "、".join(str(item) for item in missing) if missing else "無"],
    ]
    summary = ("資料日 " + analysis + "（" + markets + "）；head 發布 " + published +
               "（producer 宣告）；產生 " + generated)
    return {"rows": rows, "summary": summary, "not_latest": stale or older,
            "badge": "非最新資料" if stale or older else None, "freshness_status": value.get("freshness_status"),
            "label_count": len(unique)}


def market_days_text(mapping):
    """'上市 20261012／上櫃 20261011' for a {market: day} mapping; '' when absent."""
    if not isinstance(mapping, dict) or not mapping:
        return ""
    return "／".join(MARKET_TEXT.get(str(market).upper(), str(market)) + " " + _day(day)
                    for market, day in sorted(mapping.items()))


def _session_text(context, key, fallback):
    """The day, followed by each market's own day when markets were analysed on different days."""
    text = _text(context.get(key) or fallback)
    by_market = market_days_text(context.get(key + "_by_market"))
    return text + ("（各市場：" + by_market + "）" if by_market else "")


def _data_time_summary(payload):
    value = payload.get("data_time") if isinstance(payload.get("data_time"), dict) else {}
    head = value.get("head") if isinstance(value.get("head"), dict) else {}
    return {"analysis_asof": value.get("analysis_asof"), "asof_by_market": value.get("asof_by_market"),
            "head_published_at": head.get("published_at"), "is_stale": value.get("is_stale"),
            "freshness_status": value.get("freshness_status"),
            "version_label_count": len(payload.get("version_labels") or [])}


def _data_time_html(display):
    # 數值只在表內寫一次；表上只放「非最新資料」徽章。
    badge = '<p><span class="stale">' + html.escape(display["badge"]) + "</span></p>" if display.get("badge") else ""
    body = "".join("<tr><th>" + html.escape(str(label)) + "</th><td>" + html.escape(str(value)) + "</td></tr>"
                   for label, value in display["rows"])
    return "<section><h2>資料時間</h2>" + badge + '<table class="datatime"><tbody>' + body + "</tbody></table></section>"


def _shown_warnings(payload, display):
    """資料提醒：只列報告其他位置沒有的上游警告（excel_report.mjs 的來源提醒同規則）。

    資料過舊、落後市場、早於要求日與日曆未驗證已在資料時間表的徽章與資料新鮮度列，版本標籤已在版本提醒列，
    固定的 C0 限制句已在資料來源與限制表；這些重複的句子不列，其餘上游警告照列。
    """
    warnings = payload["meta"].get("warnings") or []
    freshness = str(dict(display["rows"]).get("資料新鮮度") or "")
    codes = {str(item.get("code")) for item in payload.get("version_labels") or [] if isinstance(item, dict)}
    shown = []
    for value in warnings if isinstance(warnings, list) else [warnings]:
        text = str(value)
        if (text == C0_LIMITATION_WARNING or text.startswith("非最新資料：") and display.get("badge")
                or text.startswith("落後市場 ") and "落後市場：" in freshness
                or text.startswith("資料日早於要求日：") and "早於要求日" in freshness
                or text.startswith("日曆已驗證至 ") and display.get("freshness_status") == "calendar_unverified_after"
                or text.startswith("版本標籤：") and text[len("版本標籤："):].split("（", 1)[0].strip() in codes):
            continue
        shown.append(value)
    return shown


def _describe_text(value, payload):
    """開盤前版說明（共用 reader 的 describe）的顯示版；原文保留於原始報告資料。

    去掉結尾「｜模型 <雜湊前 12 碼>」內部識別碼與 Top10 上方閘門已寫的美股漲跌段；訊號日→目標日與本次摘要的
    分析日、已驗證下一交易日相同時也不重複（沿用較舊快照等其他說明照列）。
    """
    text = re.sub(r"｜模型 [^｜]*$", "", _text(value))
    if (payload.get("preopen_gate") or {}).get("label"):
        text = re.sub(r"｜美股(?:收漲（建議日）|未收漲（非建議日）|漲跌未知)(?=｜|$)", "", text, count=1)
    context = payload.get("report_context") or {}
    dates = re.search(r"｜訊號日 (\d{8}) → 目標日 (\d{8})(?=｜|$)", text)
    if dates and (dates[1], dates[2]) == (_day(context.get("base_session")), _day(context.get("target_session"))):
        text = text.replace(dates[0], "", 1)
    return text


def _fallback_detail(status):
    """回退的原因詳情（快照實際日期等，原因代碼只在紅字寫一次）；目標日不符改寫成中文日期句。"""
    detail = status.get("detail")
    if not detail:
        return ""
    match = re.fullmatch(r"snapshot target_date (\S*) != C0 verified next session (\S+)", str(detail))
    return "快照目標日 " + _day(match[1]) + "／已驗證下一交易日 " + _day(match[2]) if match else str(detail)


def _model_kinds(context):
    """方向模型／報酬模型的顯示子句；未提供的子句不列。"""
    kinds = (("方向模型", _human(context.get("selected_direction_kind"), "其他方向模型（識別見既有資料）")),
             ("報酬模型", _human(context.get("selected_return_kind"), "其他報酬模型（識別見既有資料）")))
    return [label + "：" + text for label, text in kinds if text != "未提供"]


def _model_source_html(payload):
    """模型來源區塊：開盤前版（說明、模型種類、必要揭露）、回退 C0（醒目原因與原因詳情）或強制 C0。

    美股閘門只在 Top10 上方顯示；run_id 與雜湊保留於原始報告資料。
    """
    source = payload.get("model_source")
    status = payload.get("preopen_status") if isinstance(payload.get("preopen_status"), dict) else {}
    note = html.escape(_model_source_text(payload))
    if source == "preopen":
        info = payload.get("preopen_meta") or {}
        disclosure = payload.get("model_disclosure") or {}
        # 開頭已寫「開盤前版」，模型種類不再重複這個字首。
        kinds = "；".join(kind.replace("：開盤前版", "：", 1) for kind in _model_kinds(payload.get("report_context") or {}))
        lines = "".join("<li>" + html.escape(str(line)) + "</li>" for line in disclosure.get("lines") or [])
        return ('<section class="model preopen"><p><strong>H1 模型來源：開盤前版</strong>' +
                ("；" + html.escape(kinds) if kinds else "") + "</p>"
                "<p>" + html.escape(_describe_text(info.get("describe"), payload)) + "</p>"
                '<div class="disclosure"><h2>' + html.escape(_text(disclosure.get("title"))) + "</h2><ul>" + lines + "</ul>"
                '<p class="ident">' + html.escape(_text(disclosure.get("source"))) + "</p></div></section>")
    if status.get("status") == "unavailable":
        detail = _fallback_detail(status)
        return ('<section class="model"><p class="fallback">' + note + "</p>" +
                ('<p class="ident">原因詳情：' + html.escape(detail) + "</p>" if detail else "") + "</section>")
    return '<section class="model"><p>' + note + "</p></section>"


def _comparison_display_text(value):
    """A detached Chinese display view; keep raw dates, diagnostics and hashes."""
    text = _text(value)
    if text.startswith("本次分析未提供可用 head") or text.startswith("分析來源未提供；比較另行釘選"):
        return "分析資料來源未能核對；比較資料另行讀取，無法確認與預測使用相同資料。"
    if text.startswith("比較來源未證實與本次分析 head 相同"):
        return "歷史比較與本次分析的資料來源尚未確認一致，請參閱各項資料日期。"
    if text.startswith("中文摘要不可得（"):
        return "中文摘要暫不可得；原始比較資料保留。"
    if text.startswith("比較詳表不可得（"):
        return "比較詳表暫不可得；原始比較資料保留。"
    unavailable = re.match(r"^([\u3400-\u9fff]{0,30}比較不可得)[：:（(]", text)
    if unavailable:
        return unavailable.group(1) + "；部分來源資料目前無法使用，原有分析及其他可用數值保留。"
    if re.search(r"(?:比較(?:詳表)?|中文摘要)不可得[：:（(]", text):
        return "部分比較資料暫不可得；原始比較資料及原預測保留。"
    text = re.sub(r"(?<!\d)(\d{4})\s*年\s*(\d{1,2})\s*月\s*(\d{1,2})\s*日",
                  lambda match: match[1] + match[2].zfill(2) + match[3].zfill(2), text)
    text = re.sub(r"(?<!\d)((?:\d{4}、)*\d{4})\s*年\s*(0?[1-9]|1[0-2])\s*月",
                  lambda match: "、".join(year + match[2].zfill(2) for year in match[1].split("、")), text)
    text = re.sub(r"(?<!\d)(\d{4})[-/](\d{2})[-/](\d{2})(?!\d)",
                  lambda match: "".join(match.groups()), text)
    text = re.sub(r"(?<!\d)(\d{4})[-/](0[1-9]|1[0-2])(?![\d/-])",
                  lambda match: "".join(match.groups()), text)
    text = re.sub(r"(?<![0-9a-fA-F])[0-9a-fA-F]{64}(?![0-9a-fA-F])", "", text)
    text = re.sub(r"\b(?:source(?:_[a-z0-9]+)*|head|sha(?:256)?)\b", "資料來源", text, flags=re.IGNORECASE)
    if re.fullmatch(r"[0-9.,%+ /:\-、～~]+", text):
        return text
    return _human(text, "資料說明暫不可得，原始紀錄保留。")


def _comparison_html(payload):
    """前十檔歷史比較：比較基準日與各檔相同的說明只在節首寫一次；比較不可得的股票只列代碼（全部不可得時只留一行）。"""
    blocks = [(row, row["report_comparison"]) for row in payload["top10"] if isinstance(row.get("report_comparison"), dict)]
    if not blocks:
        return ""
    available = [(row, block) for row, block in blocks if block.get("status") != "unavailable"]
    if not available:
        return "<section><h2>前十檔個股歷史比較與中文解讀</h2><p>本次歷史比較不可得；原預測結果保留。</p></section>"
    unavailable = [str(row["stock_id"]) for row, block in blocks if block.get("status") == "unavailable"]
    columns = ("項目", "資料期間", "本期值", "比較", "解讀", "資料日期")
    header = "".join("<th>" + html.escape(key) + "</th>" for key in columns)
    common_day = Counter(_day(block.get("signal_date")) for _, block in available).most_common(1)[0][0]
    notes = [tuple(dict.fromkeys(_comparison_display_text(note) for note in block.get("notes", []))) for _, block in available]
    # 各檔說明完全相同時移到節首寫一次；有任何一檔不同時保留在各檔。
    shared = len(set(notes)) == 1

    def note_list(items):
        return "<ul>" + "".join("<li>" + html.escape(note) + "</li>" for note in items) + "</ul>" if items else ""
    sections = []
    for (row, block), visible_notes in zip(available, notes):
        title = str(row["display_order"]) + ". " + str(row["stock_id"]) + " " + _text(row.get("name"))
        day = _day(block.get("signal_date"))
        lines = "".join("<li>" + html.escape(_comparison_display_text(line)) + "</li>" for line in row.get("report_comparison_summary", []))
        body = "".join("<tr>" + "".join("<td>" + html.escape(_comparison_display_text(item.get(key))) + "</td>" for key in columns) + "</tr>"
                       for item in row.get("report_comparison_rows", []))
        sections.append("<section><h3>" + html.escape(title) + "</h3>" +
                        ("<p>比較基準日：" + html.escape(day) + "（與其他股票不同）</p>" if day != common_day else "") +
                        "<ul>" + lines + "</ul><details><summary>歷史比較詳表</summary><table><thead><tr>" + header +
                        "</tr></thead><tbody>" + body + "</tbody></table>" + ("" if shared else note_list(visible_notes)) +
                        "</details></section>")
    return ("<section><h2>前十檔個股歷史比較與中文解讀</h2>"
            "<p>依本次分析日比較近期營收同比／月比及其他可算指標；各項資料期間與資料日期見詳表。</p>"
            "<p>比較基準日：" + html.escape(common_day) + "</p>" + (note_list(notes[0]) if shared else "") +
            ("<p>歷史比較不可得（原預測結果保留）：" + html.escape("、".join(unavailable)) + "</p>" if unavailable else "") +
            "".join(sections) + "</section>")


def _write_html(path, payload):
    top_columns, top_common = _top10_columns(payload)

    def table(rows, columns=_columns(payload)):
        head = "".join("<th>" + html.escape(label) + "</th>" for _, label in columns)
        body = "".join("<tr>" + "".join("<td>" + html.escape(_display(row, key)) + "</td>" for key, _ in columns) + "</tr>" for row in rows)
        return "<table><thead><tr>" + head + "</tr></thead><tbody>" + body + "</tbody></table>"
    preopen = payload.get("model_source") == "preopen"
    gate = payload.get("preopen_gate") or {}
    gate_css = {"recommended_day": "gate gate-up", "not_recommended_day": "gate gate-down"}.get(gate.get("code"), "gate gate-unknown")
    context = payload["report_context"]
    target = context.get("target_session")
    target_text = _session_text(context, "target_session", None) if target else "未驗證；保留空值"
    declared = (payload.get("preopen_meta") or {}).get("target_date") if preopen else None
    if not target and declared:
        # 已驗證日曆尚未涵蓋時不猜日期；只標示開盤前版快照自己宣告的目標日。
        target_text += "（開盤前版快照宣告目標日 " + _day(declared) + "，未經本程式日曆驗證）"
    elif declared and _day(declared) != _day(target):
        target_text += "（本報告日曆；H1 沿用開盤前版原目標日 " + _day(declared) + "）"
    predicted = {(str(row.get("market")), str(row.get("stock_id"))) for row in payload["predictions"]}
    excluded = [row for row in payload["coverage"] if (str(row.get("market")), str(row.get("stock_id"))) not in predicted]
    ranking = _human(context.get("ranking_note"))
    if preopen:
        # 命中率子句只在必要揭露與「使用限制」寫出；排序規則已在 Top10 標題。
        ranking = ranking.replace("；" + PREOPEN_HIT_RATE_NOTE, "")
        ranking = "未提供" if ranking == PREOPEN_RANKING_RULE else ranking
    # 校準狀態一律保留；未提供的子句不列。開盤前版的模型種類固定，已併入模型來源區。
    status = "；".join(text for text in (_human(context.get("calibration_status")), ranking) if text != "未提供")
    kinds = [] if preopen else _model_kinds(context)
    # 開盤前版時 C0 為備援（全部股票表另列 C0 補充分析）；C0 排序狀態只在本次摘要寫一次。
    c0_ranking = _human(context.get("c0_ranking_note"), "C0 排序狀態（原始紀錄保留）") if preopen else "未提供"
    display = _display_time(payload)
    parts = ['<!doctype html><html lang="zh-Hant"><meta charset="utf-8"><title>台股下一交易日收盤研究報告</title>',
        "<style>body{font-family:'Microsoft JhengHei','Noto Sans TC',sans-serif;margin:24px;color:#17202a;line-height:1.6}main{max-width:1280px;margin:auto}table{border-collapse:collapse;width:100%;margin:12px 0}td,th{border:1px solid #bbb;padding:8px;text-align:right;overflow-wrap:anywhere}th{background:#edf2f7}td:nth-child(-n+4){text-align:left}pre{white-space:pre-wrap;overflow-wrap:anywhere}details{margin:16px 0}"
        ".datatime td,.datatime th{text-align:left}.datatime{width:auto;margin:8px 0}.stale{color:#fff;background:#c00;padding:2px 8px;border-radius:4px;font-weight:bold}"
        ".model{margin:12px 0}.fallback{color:#fff;background:#c00;padding:8px 12px;border-radius:4px;font-weight:bold}"
        ".gate{padding:8px 12px;border-radius:4px;font-weight:bold}.gate-up{background:#e6f4ea;border-left:6px solid #1e7e34}"
        ".gate-down{background:#fdecea;border-left:6px solid #c00}.gate-unknown{background:#f1f1f1;border-left:6px solid #777}"
        ".disclosure{border:2px solid #b8860b;background:#fffbea;padding:4px 16px;margin:12px 0}.ident{color:#555;font-size:90%;overflow-wrap:anywhere}</style><body><main>",
        "<h1>台股下一交易日收盤方向與報酬研究</h1>",
        "<p>H1 顯示模型：開盤前版（C0 保留為備援）；H1 指下一個市場交易日的收盤相對訊號日收盤的方向，不是開盤預測。</p>"
            if preopen else "<p>現役模型 C0；H1 指下一個市場交易日的收盤，不是開盤預測。</p>",
        # 排序（Top10）放最前、全部股票分析次之，模型來源、資料時間與摘要移到後面。
        "<h2>開盤前版可交易 Top10（依收漲機率→預估報酬）</h2>" if preopen else "<h2>研究展示（最多十檔）</h2>",
        '<p class="' + gate_css + '">' + html.escape(_text(gate.get("label"))) + "</p>" if preopen else "<p>展示序號不表示交易優勢。</p>",
        "<p>" + html.escape(top_common) + "</p>" if top_common else "",
        table(payload["top10"], top_columns), _comparison_html(payload),
        "<details><summary>" + ("全部股票（開盤前版依原排序；C0補充分析另列於後）" if preopen else "全部已分析股票") + "</summary>",
        table(payload["predictions"]), "</details>",
        _model_source_html(payload),
        _data_time_html(display),
        "<h2>本次摘要</h2><p>分析日：" +
            html.escape(_session_text(context, "base_session", payload["meta"].get("asof"))) +
            "；下一交易日：" + html.escape(target_text) + "。</p>",
        "<p>已分析 " + str(len(payload["predictions"])) + " 檔；未分析／排除 " + str(len(excluded)) + " 檔。</p>",
        "<p>" + html.escape(status) + "。</p>" if status else "",
        "<p>" + html.escape("；".join(kinds)) + "。</p>" if kinds else "",
        "<p>C0 備援排序狀態：" + html.escape(c0_ranking) + "。</p>" if c0_ranking != "未提供" else "",
        # 正式資格、交易效益與命中率的說明只在「使用限制」與必要揭露寫一次。
        "<p>報酬以調整後收盤為基礎，百分比僅為小數換算。</p>"]
    if excluded:
        body = "".join("<tr>" + "".join("<td>" + html.escape(value) + "</td>" for value in (
            _text(row.get("stock_id")), _text(row.get("name")), _display(row, "market"),
            _human(row.get("reason") or row.get("exclusion_reason") or row.get("status"), "其他排除原因（原始紀錄保留）"))) + "</tr>" for row in excluded)
        parts.append("<details><summary>未分析／排除原因</summary><table><thead><tr><th>股票代碼</th><th>股票名稱</th><th>市場</th><th>原因</th></tr></thead><tbody>" + body + "</tbody></table></details>")
    warnings = _shown_warnings(payload, display)
    if warnings:
        parts.append("<h2>資料提醒</h2><ul>" + "".join("<li>" + html.escape(_human(value, "資料提醒（原始紀錄保留）")) + "</li>" for value in warnings) + "</ul>")
    parts.append("<h2>資料來源與限制</h2><table class=\"datatime\"><tbody>" + "".join(
        "<tr><th>" + html.escape(label) + "</th><td>" + html.escape(value) + "</td></tr>"
        for label, value in _limitation_rows(payload)) + "</tbody></table>")
    parts.append('<p><a href="report.json">原始報告資料（完整數值、來源及模型識別）</a></p>')
    parts.append("</main></body></html>")
    with Path(path).open("x", encoding="utf-8", newline="") as handle:
        handle.write("\n".join(parts))


def _write_csv(path, payload):
    """每檔一列（前十檔以欄位標示：開盤前版叫「Top10」，C0 沿用 HTML／Excel 的「研究展示」，不另寫研究展示列）；
    模型來源與必要揭露、報告脈絡各一列原始 JSON，其餘原始資料（來源、模型、覆蓋、資料時間）見同資料夾的 report.json。"""
    columns = _columns(payload)
    labels = {key: label + ("（小數）" if key in {"probability", "expected_return"} else "") for key, label in columns}
    # C0（含無個股排序訊號）的前十列只是研究展示，欄名不用 Top10，避免暗示排序優勢。
    top_label = "Top10" if payload.get("model_source") == "preopen" else "研究展示"
    headers = ["資料類型", *labels.values(), top_label, "原始脈絡資料"]
    preopen_info = payload.get("preopen_meta") or {}
    # 依實際展示的 top10 列標示：開盤前版可交易股不足十檔時，展示序號 ≤ 10 不等於 Top10。
    top10 = {(str(row.get("market")), str(row.get("stock_id"))) for row in payload["top10"]}
    row_context = json.dumps({
        "model": payload["report_context"].get("model_version"),
        "model_source": payload.get("model_source"),
        "preopen_run_id": preopen_info.get("run_id"),
        "preopen_model_sha256": preopen_info.get("model_sha256"),
        "target_session": payload["report_context"].get("target_session"),
        "return_unit": payload["return_unit"],
        "ranking_policy": payload["ranking_policy"]}, ensure_ascii=False)
    with Path(path).open("x", encoding="utf-8-sig", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=headers)
        writer.writeheader()
        for row in payload["predictions"]:
            item = {labels[key]: row.get(key) if row.get(key) is not None else "未提供" for key, _ in columns}
            item[labels["market"]] = _display(row, "market")
            if "tradable" in labels:
                item[labels["tradable"]] = _display(row, "tradable")
                item[labels["rank_tradable"]] = _display(row, "rank_tradable")
            for key, value in item.items():
                if isinstance(value, str) and value.startswith(("=", "+", "-", "@")):
                    item[key] = "'" + value
            writer.writerow({"資料類型": "全部已分析股票", **item,
                             top_label: "是" if (str(row.get("market")), str(row.get("stock_id"))) in top10 else "否"})
        writer.writerow({"資料類型": "模型來源與必要揭露", "原始脈絡資料": json.dumps(
            {key: payload.get(key) for key in MODEL_SOURCE_FIELDS}, ensure_ascii=False)})
        writer.writerow({"資料類型": "報告脈絡", "原始脈絡資料": row_context})


def report(project, predictions, coverage, meta, model, now, *,
           namespace_root=None, report_context=None, performance_state=None):
    project = Path(project)
    node, builder = excel_runtime(project)
    root = Path(namespace_root) if namespace_root is not None else Path(r"D:\TWStockData\program_data\tw_nextday_top10")
    context = dict(report_context or {})
    target_raw = context.get("target_session")
    target = date8(target_raw) if target_raw else None
    partition = target if target else date8(meta.get("asof"))
    run_id = str(context.get("run_id") or ("run_" + _utcnow().strftime("%Y%m%dT%H%M%S%fZ") + "_" + uuid.uuid4().hex[:8]))
    if not run_id or any(c not in "abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789_-" for c in run_id):
        raise ValueError("報告 run_id 格式無效")
    context.update(run_id=run_id, target_session=target)
    if target is None:
        context.update(target_date=None, future_calendar_status="not_yet_verified",
            output_partition_date=partition, output_partition_role="analysis_asof_file_organization_only")
    payload = prepare_payload(predictions, coverage, meta, model, now, context, performance_state)
    folder = root / "reports" / partition / run_id
    folder.mkdir(parents=True, exist_ok=False)
    payload_path, output = folder / "report.json", folder / "report.xlsx"
    html_path, csv_path = folder / "report.html", folder / "report.csv"
    _write_new_json(payload_path, payload)
    _write_html(html_path, payload)
    _write_csv(csv_path, payload)
    command = [str(node), str(builder), str(payload_path), str(output)]
    if os.environ.get("TOP10_EXCEL_VERIFY") == "1":
        command.append("--verify")
    try:
        subprocess.run(command, check=True, encoding="utf-8", errors="strict", capture_output=True)
    except subprocess.CalledProcessError as exc:
        detail = (exc.stderr or exc.stdout or "未提供錯誤訊息").strip()
        raise RuntimeError(f"Excel 匯出失敗（退出碼 {exc.returncode}）：{detail}") from exc
    artifacts = {}
    for kind, path in (("payload", payload_path), ("xlsx", output), ("html", html_path), ("csv", csv_path)):
        size = path.stat().st_size
        if size <= 0:
            raise RuntimeError("報告產物為空，未更新最新報告。")
        artifacts[kind] = {"path": str(path), "size": size, "sha256": _hash_file(path)}
    manifest = {"schema": "tw_nextday_top10_report_manifest_v1", "date_encoding": DATE_ENCODING, "status": "complete",
        "run_id": run_id, "target_date": target, "generated_at": now.isoformat(),
        "output_partition_date": partition,
        "output_partition_role": "analysis_asof_file_organization_only" if target is None else "target_session",
        "research_only": True, "report_context": payload["report_context"],
        **{key: payload.get(key) for key in MODEL_SOURCE_FIELDS},
        "data_time": _data_time_summary(payload),
        "report_payload_sha256": artifacts["payload"]["sha256"], "artifact_hashes": artifacts}
    manifest_path = folder / "manifest.json"
    _write_new_json(manifest_path, manifest)
    manifest_hash = _hash_file(manifest_path)
    latest_path = root / "reports" / "latest.json"
    _copy_visible_report(output, flat_report_path(project, target, analysis_asof=meta.get("asof")), run_id)
    atomic_json(latest_path, {"schema": "tw_nextday_top10_latest_v1", "date_encoding": DATE_ENCODING, "run_id": run_id,
        "target_session": target, "report": str(output), "html": str(html_path), "csv": str(csv_path),
        "payload": str(payload_path), "manifest": str(manifest_path), "manifest_sha256": manifest_hash,
        "model_source": payload.get("model_source"),
        "preopen_run_id": (payload.get("preopen_meta") or {}).get("run_id"),
        "preopen_status": (payload.get("preopen_status") or {}).get("status"),
        "preopen_reason": (payload.get("preopen_status") or {}).get("reason"),
        "generated_at": now.isoformat(), "data_time": _data_time_summary(payload)})
    return output
