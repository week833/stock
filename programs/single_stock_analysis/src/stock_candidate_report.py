"""Concise Traditional-Chinese reports; deployment authorization stays upstream."""

from __future__ import annotations

import calendar
import contextlib
import contextvars
import ctypes
import hashlib
import html
import importlib.util
import json
import math
import os
import re
import stat
import sys
import tempfile
import unicodedata
import uuid
from datetime import date
from functools import lru_cache
from pathlib import Path

from report_comparison_bridge import attach_report_comparison, report_comparison_rows
from stock_report_analysis import build_report_insights, normalize_news_title


SCHEMA = "single-stock-selected-candidate-report-v1"
FORMAL_SCHEMA = "single-stock-selected-formal-report-v1"
_BUNDLE_V1 = "single-stock-candidate-report-bundle-v1"
_BUNDLE_V2 = "single-stock-candidate-report-bundle-v2"
_REFERENCE_SCHEMA = "candidate-snapshot-descriptor-reference-v1"
_EVIDENCE_SCHEMA = "candidate-snapshot-descriptor-evidence-v1"
_MODEL_DESCRIPTOR_POINTER = "/metadata/snapshot_binding/descriptor"
_READ_ONLY_STORAGE_POLICY = "single-stock-read-only-inputs-v1"
_REPORT_CONTEXT_SCHEMA = "single-stock-report-context-v1"
_PUBLISHED_ROOT = Path(r"D:\TWStockData\shared_market_data")
_PUBLISHED_RECONSTRUCTION = "single-selected-published-snapshot-v1"
_PUBLISHED_SCHEMAS = {"stock-data-publication-v1", "stock-data-publication-v2"}
_MISSING = "無法計算（未提供足夠資料或證據）"
_HAN = re.compile(r"[\u3400-\u9fff\U00020000-\U0003134f]")
_SHA = re.compile(r"[0-9a-fA-F]{64}\Z")
_ROUTES = {
    "official_calendar": "官方交易日曆", "official_market_daily": "官方日行情",
    "finmind_stock_info": "股票基本資料", "finmind_price_adj": "還原股價",
    "official_trading_restrictions": "交易限制與處置", "global_market_history": "海外市場歷史",
    "finmind_institutional": "三大法人買賣超", "finmind_margin": "融資融券",
    "official_finance_reporting_deadline": "財報申報期限", "finmind_month_revenue": "月營收",
    "finmind_financial_statements": "損益與財務報表", "finmind_balance_sheet": "資產負債表",
    "finmind_cash_flows_statement": "現金流量表", "finmind_dividend": "股利資訊",
    "finmind_dividend_result": "除權息結果", "finmind_per": "本益比與股價淨值比",
    "finmind_shareholding": "外資持股", "finmind_day_trading": "現股當沖",
    "finmind_futures_institutional": "期貨法人部位", "finmind_options_institutional": "選擇權法人部位",
    "finmind_government_bank": "官股銀行買賣", "finmind_trading_daily_report": "券商分點交易",
    "finmind_block_trade": "鉅額交易", "finmind_loan_collateral": "借貸與擔保融通",
    "finmind_margin_maintenance": "融資維持率", "official_active_etf_holding": "主動式指數股票型基金持股",
    "official_active_etf_change": "主動式指數股票型基金持股變動", "finmind_industry_chain_flow": "產業鏈資金流",
    "official_sector_features": "官方產業分類", "finmind_exchange_rate": "匯率",
    "finmind_government_bonds_yield": "公債殖利率", "finmind_crude_oil_prices": "原油價格",
    "finmind_gold_price": "黃金價格", "tdcc_share_distribution_raw": "集保持股分級",
    "news_and_hot_topics_raw": "新聞與熱門題材", "official_sector_observations": "產業與總經觀測",
}
_STATUS = {
    "used": "已選為特徵或必要證據（不等於所有欄位實際入模）", "model_input": "模型輸入",
    "context-only": "僅作當期上下文", "context_only": "僅作當期上下文",
    "not-selected": "本次未選用（不是缺失）", "not_selected": "本次未選用（不是缺失）",
    "available": "可用", "legitimate_empty": "有正向證據的合法無資料", "not_applicable": "不適用",
    "missing": "資料缺失", "unknown_availability": "資料可得性未知", "unavailable": "本次用途不可用",
    "SUCCESS": "發布成功（不代表歷史時點已驗證）", "PARTIAL": "部分資料，限制保留",
    "complete": "完整", "partial": "部分完成", "trained": "已訓練", "not_trained": "未能訓練",
    "no_estimate": "無估計", "not_predicted": "未能預測", "predicted": "已有模型估計",
    "formal_estimate": "正式模型估計", "partial_formal_estimate": "部分正式模型估計",
    "research_estimate": "研究估計", "partial_research_estimate": "部分研究估計",
    "uncalibrated": "未校準", "calibrated": "已依封存規則校準", "evaluated": "已評估",
    "uncalibrated_insufficient_candidate_coverage": "未校準（候選校準覆蓋或方向類別不足）",
    "uncalibrated_calibration_failed": "未校準（校準執行失敗）",
    "calibrated_independent_20_days": "以獨立且足額的校準區間校準（依提供者封存紀錄）",
    "insufficient_effective_test_signal_days": "有效測試訊號日不足",
    "fit_all_missing": "訓練期間全缺，未學會數值或缺值訊號",
    "fit_constant": "訓練期間為常數，未學會數值變動訊號",
    "fit_informative": "訓練期間有變動，可參與學習，但未證明增益",
    "lagged-date": "延後交易日的日期代理", "lagged_date": "延後交易日的日期代理",
}
_KINDS = {
    "seven_linear": "七特徵線性參考模型", "original_seven_linear": "七特徵線性參考模型",
    "expanded_linear": "擴充特徵邏輯斯與嶺迴歸模型", "expanded_hgb": "擴充特徵直方圖梯度提升模型",
    "hist_gradient_boosting": "擴充特徵直方圖梯度提升模型",
    "fit_up_rate": "訓練期上漲率比較基準", "fit_up_rate_baseline": "訓練期上漲率比較基準",
    "zero_return": "零報酬比較基準", "zero_return_baseline": "零報酬比較基準",
}
_TASK = {"direction": "方向分支", "return": "報酬分支"}
_FUND = {"finmind_stock_info", "finmind_month_revenue", "finmind_per", "finmind_financial_statements",
         "finmind_balance_sheet", "finmind_cash_flows_statement", "finmind_dividend", "finmind_dividend_result",
         "official_finance_reporting_deadline", "official_sector_features"}
_GLOBAL = {"global_market_history", "finmind_exchange_rate", "finmind_government_bonds_yield",
           "finmind_crude_oil_prices", "finmind_gold_price", "news_and_hot_topics_raw", "official_sector_observations"}
_TECH = {"official_market_daily", "finmind_price_adj", "official_trading_restrictions", "official_calendar"}
_FIELDS = {
    "PER": "PER 本益比", "per": "PER 本益比", "PBR": "PBR 股價淨值比", "pbr": "PBR 股價淨值比",
    "dividend_yield": "現金殖利率", "revenue": "營收", "revenue_year": "營收年度",
    "revenue_month": "營收月份", "revenue_yoy": "營收年增率", "revenue_mom": "營收月增率",
    "Revenue": "營收", "OperatingIncome": "營業利益", "GrossProfit": "營業毛利",
    "NetIncome": "本期淨利", "IncomeAfterTaxes": "稅後淨利", "EPS": "EPS 每股盈餘",
    "TotalAssets": "資產總額", "TotalLiabilities": "負債總額", "Equity": "權益總額",
    "OperatingExpenses": "營業費用", "IncomeBeforeTax": "稅前淨利", "ProfitLoss": "本期損益",
    "TotalCurrentAssets": "流動資產合計", "TotalNoncurrentAssets": "非流動資產合計",
    "TotalCurrentLiabilities": "流動負債合計", "TotalNoncurrentLiabilities": "非流動負債合計",
    "EquityAttributableToOwnersOfParent": "歸屬母公司業主權益", "CapitalStock": "股本",
    "RetainedEarnings": "保留盈餘", "Inventories": "存貨", "AccountsReceivableNet": "應收帳款淨額",
    "CashFlowsFromOperatingActivities": "營業活動現金流量", "OperatingCashFlow": "營業現金流量",
    "CashFlowsFromInvestingActivities": "投資活動現金流量",
    "CashFlowsFromFinancingActivities": "籌資活動現金流量", "CashAndCashEquivalents": "現金及約當現金",
    "CashEarningsDistribution": "盈餘現金股利", "StockEarningsDistribution": "盈餘股票股利",
    "buy": "買進數量", "sell": "賣出數量", "net": "淨買賣數量", "net_buy": "淨買入數量",
    "buy_sell": "買賣差額", "buy_sell_diff": "買賣差額", "volume": "成交量",
    "foreign_investors_net": "外資淨買賣數量", "investment_trust_net": "投信淨買賣數量",
    "dealer_net": "自營商淨買賣數量", "total_net": "法人合計淨買賣數量",
    "Foreign_Investor_buy": "外資買進數量", "Foreign_Investor_sell": "外資賣出數量",
    "Investment_Trust_buy": "投信買進數量", "Investment_Trust_sell": "投信賣出數量",
    "Dealer_buy": "自營商買進數量", "Dealer_sell": "自營商賣出數量",
    "margin_purchase_balance": "融資餘額", "margin_purchase_change": "融資餘額變動",
    "short_sale_balance": "融券餘額", "short_sale_change": "融券餘額變動",
    "day_trade_volume": "當沖成交量", "Volume": "來源成交量", "BuyAmount": "買進金額",
    "SellAmount": "賣出金額", "holding_percentage": "持股比例", "NumberOfSharesIssued": "已發行股數",
    "Trading_Volume": "成交股數", "Trading_money": "成交金額", "Trading_turnover": "原始成交欄位（不逕稱換手率）",
    "MarginPurchaseTodayBalance": "融資今日餘額", "ShortSaleTodayBalance": "融券今日餘額",
    "MarginPurchaseBuy": "融資買進", "MarginPurchaseSell": "融資賣出",
    "ShortSaleBuy": "融券買進", "ShortSaleSell": "融券賣出",
    "ForeignInvestmentSharesRatio": "外資持股比例", "ForeignInvestmentShares": "外資持股數",
    "ForeignInvestmentRemainingShares": "外資尚可投資股數", "DayTradingVolume": "當沖成交量",
    "holding_shares": "持股數", "holding_ratio": "持股比例", "change": "變動值",
    "close": "收盤值", "price": "價格", "value": "觀測值", "yield": "殖利率",
    "exchange_rate": "匯率", "rate": "比率", "open": "開盤值", "high": "最高值", "low": "最低值",
    "number_of_people": "持有人數", "percent": "原始百分比", "shares": "股數",
    "operating_margin": "營業利益率", "debt_ratio": "負債資產比",
}
_METRIC = {
    "accuracy": "方向正確率", "holdout_accuracy": "保留區間方向正確率",
    "balanced_accuracy": "類別平衡正確率", "brier": "Brier 機率平方誤差", "log_loss": "對數損失",
    "auc": "AUC 區辨面積", "mae": "報酬平均絕對誤差", "rmse": "報酬均方根誤差",
    "coverage": "預測覆蓋率", "n_denominator": "原始分母筆數", "n_labeled": "已有成熟標籤筆數",
    "n_predicted": "成功預測筆數", "n_failed": "未能預測筆數", "n_paired": "成對比較筆數",
    "n_effective_signal_days": "有效訊號日數", "n_rows": "資料列數", "n_signal_days": "訊號日數",
    "n_stocks": "股票數", "n_excluded": "排除筆數", "n_positive": "上漲類別筆數",
    "n_evaluated": "可評分筆數", "n_unscored": "不可評分筆數", "evaluation_coverage": "評分覆蓋率",
    "n_effective_days": "有效訊號日數", "up": "上漲類別筆數", "not_up": "非上漲類別筆數",
    "next_signal_start": "下一區間訊號起日", "first_label_end": "最早標籤到期日", "last_label_end": "最晚標籤到期日",
    "n_negative": "非上漲類別筆數", "model_minus_baseline": "模型誤差減基準誤差",
    "worse_than_baseline": "是否劣於基準", "start": "區間開始", "end": "區間結束",
    "n_finite": "有限值筆數", "n_missing": "缺值筆數", "signal_days": "訊號日數",
    "stock_count": "股票數", "row_count": "資料列數", "status": "狀態", "reason_zh": "原因",
    "holdout_status": "保留測試資格", "value": "基準值", "kind": "方法", "metric": "比較指標",
}


class CandidateReportError(ValueError):
    def __init__(self, code, message, details=None):
        super().__init__(message)
        self.code, self.details = code, details or {}


# 單股入口（single_stock_preopen.selected_h1_overlay）包住 shared 的 generate_selected_reports 時才設定；
# 只對尚無開盤前版區塊的正式 H1 payload 呼叫一次，其他呼叫端（候選研究等）不受影響。
PREOPEN_H1_SCHEMA = "single-stock-preopen-h1-v1"
_PREOPEN_H1_ATTACHER = contextvars.ContextVar("single_stock_preopen_h1_attacher", default=None)


@contextlib.contextmanager
def preopen_h1_attacher(function):
    token = _PREOPEN_H1_ATTACHER.set(function)
    try:
        yield
    finally:
        _PREOPEN_H1_ATTACHER.reset(token)


def _attach_preopen_h1(payload):
    attach = _PREOPEN_H1_ATTACHER.get()
    if (attach is not None and isinstance(payload, dict) and payload.get("schema") == FORMAL_SCHEMA
            and payload.get("horizon_sessions") == 1 and "preopen_h1" not in payload):
        attach(payload)


@lru_cache(maxsize=1)
def _converter():
    if os.name != "nt":
        raise CandidateReportError("traditional_conversion_unavailable", "非 Windows 環境缺少已驗證的轉繁介面，停止產出。")
    api = ctypes.WinDLL("kernel32", use_last_error=True).LCMapStringEx
    api.argtypes = [ctypes.c_wchar_p, ctypes.c_uint, ctypes.c_wchar_p, ctypes.c_int,
                    ctypes.c_wchar_p, ctypes.c_int, ctypes.c_void_p, ctypes.c_void_p, ctypes.c_ssize_t]
    api.restype = ctypes.c_int

    def convert(text):
        if not text:
            return text
        count = api("zh-TW", 0x04000000, text, -1, None, 0, None, None, 0)
        if not count:
            raise CandidateReportError("traditional_conversion_failed", "Windows 繁體中文轉換失敗，停止產出。")
        buffer = ctypes.create_unicode_buffer(count)
        if not api("zh-TW", 0x04000000, text, -1, buffer, count, None, None, 0):
            raise CandidateReportError("traditional_conversion_failed", "Windows 繁體中文轉換失敗，停止產出。")
        return buffer.value

    if convert("汉语转换") != "漢語轉換":
        raise CandidateReportError("traditional_conversion_failed", "繁體中文轉換自我檢查未通過。")
    return convert


def _tc(text):
    text = unicodedata.normalize("NFC", str(text)).replace("\r\n", "\n").replace("\r", "\n").replace("\t", " ")
    if any(unicodedata.category(c) in {"Cc", "Cf", "Cs"} and c != "\n" for c in text):
        raise CandidateReportError("unsafe_visible_character", "可見內容含控制字元或無效文字，停止產出。")
    return _converter()(text)


def _zh(value, fallback="未提供繁體中文說明；原始資料保留於隨附資料檔"):
    if not isinstance(value, str) or not value.strip() or value.lstrip().startswith(("{", "[")):
        return fallback
    text = _tc(value).strip()
    if not _HAN.search(text):
        return fallback
    text = re.sub(r"[A-Za-z][A-Za-z0-9]*_[A-Za-z0-9_]+", "〔內部代碼見資料檔〕", text)
    return re.sub(r"\b(?:[A-Za-z]{2,}[ :]+){3,}[A-Za-z]{2,}\b", "〔外語原文見資料檔〕", text)


def _state(value):
    return _STATUS.get(str(value), _zh(value, "未確認（原始狀態見資料檔）"))


def _route(value, name=None):
    return _zh(name, _ROUTES.get(str(value), "補充資料來源（識別見資料檔）"))


def _kind(value):
    if value is None:
        return "未選取模型"
    known = _KINDS.get(str(value))
    if known is not None:
        return known
    return "研究模型（模型識別：" + _code(value) + "）"


def _code(value):
    text = str(value or "未提供")
    return text if re.fullmatch(r"[A-Za-z0-9_.:/\\+ -]+", text) else _zh(text)


def _num(value):
    if isinstance(value, bool):
        return None
    try:
        number = float(value)
        return number if math.isfinite(number) else None
    except (ValueError, TypeError, OverflowError):
        return None


def _number(value, percent=False):
    n = _num(value)
    if n is None:
        return _MISSING
    if percent:
        return f"{n * 100:,.2f}%"
    return f"{n:,.6f}".rstrip("0").rstrip(".") if n else "0"


def _day(value):
    try:
        return date.fromisoformat(str(value)[:10]).isoformat()
    except (TypeError, ValueError):
        return None


def _time(value):
    return str(value) if value and re.fullmatch(r"[0-9TZz:+. /-]+", str(value)) else "未提供有效時間"


def _unit(value):
    return {"ratio": "比例", "percent": "百分比", "%": "百分比", "TWD": "新臺幣元",
            "shares": "股", "share": "股", "lots": "張", "lot": "張", "times": "倍",
            "fraction": "比例", "points": "點", "USD": "美元", "JPY": "日圓"}.get(str(value), _zh(value, "單位未提供，不自行推定"))


def _field_unit(row, source, field):
    kind = "percentage" if field in {"holding_percentage", "ForeignInvestmentSharesRatio", "dividend_yield", "percent", "holding_ratio"} else (
        "currency" if field in {"BuyAmount", "SellAmount", "Trading_money", "amount", "revenue", "Revenue", "value"} else "volume")
    for container in (row, source):
        units = container.get("units")
        if isinstance(units, dict) and units.get(field):
            return _unit(units[field])
        for key in (field + "_unit", kind + "_unit", "unit"):
            if container.get(key):
                return _unit(container[key])
    return _unit(None)


def _json_bytes(value, *, sort_keys=True):
    try:
        return json.dumps(value, ensure_ascii=False, sort_keys=sort_keys, allow_nan=False, separators=(",", ":")).encode("utf-8")
    except (ValueError, TypeError) as exc:
        raise CandidateReportError("payload_not_json", "輸入必須可序列化為不含非有限數值的資料檔。") from exc


def _digest(value):
    return hashlib.sha256(_json_bytes(value)).hexdigest()


def _file_info(path):
    digest = hashlib.sha256()
    with Path(path).open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return {"sha256": digest.hexdigest(), "size": Path(path).stat().st_size}


def _reject_json_constant(value):
    raise CandidateReportError("evidence_json_invalid", "證據包含非有限數值，不能接受。")


_PARSED_EVIDENCE = contextvars.ContextVar("single_stock_report_parsed_evidence", default=None)
_SELECTED_CONTEXTS = contextvars.ContextVar("single_stock_report_selected_contexts", default=None)


@contextlib.contextmanager
def _parsed_evidence_operation(*, reuse=False):
    # Only private, read-only descriptor consumers share these mappings; none escape the operation.
    if reuse and _PARSED_EVIDENCE.get() is not None:
        yield
        return
    evidence, contexts, owners = {}, {}, contextlib.ExitStack()
    token = _PARSED_EVIDENCE.set(evidence)
    context_token = _SELECTED_CONTEXTS.set((contexts, owners))
    try:
        yield
    finally:
        try:
            owners.close()
        finally:
            contexts.clear()
            evidence.clear()
            _SELECTED_CONTEXTS.reset(context_token)
            _PARSED_EVIDENCE.reset(token)


def _stable_json(path, *, sha256=None, size=None, schema=None):
    """Bind parsed evidence to one unchanged file read, not a later path observation."""
    try:
        path = Path(path).resolve(strict=True)
        with path.open("rb") as stream:
            before = os.fstat(stream.fileno())
            if not stat.S_ISREG(before.st_mode):
                raise CandidateReportError("evidence_not_file", "證據路徑不是一般檔案。")
            raw = stream.read()
            after = os.fstat(stream.fileno())
            # Windows path stat and handle stat can expose different change times after linking.
            with path.open("rb") as current_stream:
                current = os.fstat(current_stream.fileno())
                final_original = os.fstat(stream.fileno())
    except (OSError, TypeError, ValueError) as exc:
        if isinstance(exc, CandidateReportError):
            raise
        raise CandidateReportError("evidence_read_failed", "完整證據檔缺失或無法穩定讀取。") from exc
    def identity(value):
        return value.st_dev, value.st_ino, value.st_size, value.st_mtime_ns, value.st_ctime_ns
    if (not stat.S_ISREG(current.st_mode) or identity(before) != identity(after)
            or identity(after) != identity(current) or identity(current) != identity(final_original)
            or len(raw) != before.st_size):
        raise CandidateReportError("evidence_changed_during_read", "證據檔在讀取期間改變，停止驗收。")
    digest = hashlib.sha256(raw).hexdigest()
    if sha256 is not None and (not _SHA.fullmatch(str(sha256)) or digest != str(sha256).lower()):
        raise CandidateReportError("evidence_sha256_mismatch", "完整證據檔雜湊與綁定不符。")
    if size is not None and (type(size) is not int or size != len(raw)):
        raise CandidateReportError("evidence_size_mismatch", "完整證據檔大小與綁定不符。")
    evidence = _PARSED_EVIDENCE.get()
    cached = evidence.get(str(path)) if evidence is not None else None
    if cached is not None and cached[:2] == (digest, len(raw)):
        value = cached[2]
    else:
        try:
            value = json.loads(raw.decode("utf-8-sig"), parse_constant=_reject_json_constant)
        except (UnicodeError, ValueError) as exc:
            if isinstance(exc, CandidateReportError):
                raise
            raise CandidateReportError("evidence_json_invalid", "完整證據檔不是有效的 UTF-8 資料檔。") from exc
    if not isinstance(value, dict) or schema is not None and value.get("schema") != schema:
        raise CandidateReportError("evidence_schema_mismatch", "完整證據檔格式版本與綁定不符。")
    if evidence is not None:
        evidence[str(path)] = (digest, len(raw), value)
    return value, {"path": str(path), "sha256": digest, "size": len(raw)}


def _pointer(document, pointer):
    current = document
    for token in pointer.split("/")[1:]:
        key = token.replace("~1", "/").replace("~0", "~")
        if not isinstance(current, dict) or key not in current:
            raise CandidateReportError("evidence_node_missing", "完整證據檔缺少指定節點。")
        current = current[key]
    return current


def _save_descriptor_evidence(descriptor, head_sha256, output_dir):
    root = Path(output_dir).resolve()
    directory = root / "evidence"
    if directory.is_symlink() or not directory.resolve().is_relative_to(root):
        raise CandidateReportError("evidence_output_escape", "證據保存位置超出指定輸出目錄。")
    directory.mkdir(parents=True, exist_ok=True)
    evidence = {"schema": _EVIDENCE_SCHEMA, "head_sha256": head_sha256,
                "descriptor_sha256": _digest(descriptor), "descriptor": descriptor}
    raw = _json_bytes(evidence)
    digest = hashlib.sha256(raw).hexdigest()
    target = directory / ("snapshot_descriptor_" + digest + ".json")
    if not target.exists():
        temporary = directory / (".descriptor-" + uuid.uuid4().hex + ".tmp")
        try:
            with temporary.open("xb") as stream:
                stream.write(raw)
                stream.flush()
                os.fsync(stream.fileno())
            try:
                # Create-only publication: never overwrite an existing evidence package.
                os.link(temporary, target)
            except FileExistsError:
                pass
        finally:
            temporary.unlink(missing_ok=True)
    _stable_json(target, sha256=digest, size=len(raw), schema=_EVIDENCE_SCHEMA)
    return {"schema": _REFERENCE_SCHEMA, "kind": "report_evidence_json", "path": str(target),
            "sha256": digest, "size": len(raw), "container_schema": _EVIDENCE_SCHEMA,
            "json_pointer": "/descriptor", "head_sha256": head_sha256,
            "descriptor_sha256": evidence["descriptor_sha256"], "output_dir": str(root)}


def _read_only_storage(payload):
    return payload.get("schema") == FORMAL_SCHEMA and payload.get("storage_policy") == _READ_ONLY_STORAGE_POLICY


def _published_deployment_digest(payload):
    binding = payload.get("deployment_binding")
    if (not _read_only_storage(payload) or not isinstance(binding, dict)
            or binding.get("schema") != "single-stock-selected-deployment-v1"
            or binding.get("consumer_id") != "single"
            or not isinstance(binding.get("activation_id"), str) or not binding["activation_id"].strip()
            or binding.get("model_sha256") != (payload.get("model_binding") or {}).get("sha256")):
        raise CandidateReportError("evidence_deployment_binding_mismatch", "重建引用的個股啟用身分與模型綁定不符。")
    return _digest(binding)


def _check_published_descriptor(descriptor, head, fingerprint):
    if (not isinstance(head, str) or not _SHA.fullmatch(head) or head != head.lower()
            or not isinstance(fingerprint, str) or not _SHA.fullmatch(fingerprint)):
        raise CandidateReportError("evidence_reference_invalid", "中央快照缺少有效 head 或快照身分指紋。")
    sources = descriptor.get("sources") if isinstance(descriptor, dict) else None
    pinned = descriptor.get("pinned_sources") if isinstance(descriptor, dict) else None
    root = _PUBLISHED_ROOT.resolve()
    path = root / "_manifests" / (head + ".json")
    if (not isinstance(descriptor, dict) or descriptor.get("schema") != "stock-data-published-inputs-v1"
            or descriptor.get("snapshot_fingerprint") != fingerprint
            or not isinstance(sources, list) or len(sources) != 1
            or not isinstance(pinned, list) or len(pinned) != 1):
        raise CandidateReportError("evidence_descriptor_mismatch", "重建快照的格式、來源數或快照身分不符。")
    source, catalog = sources[0], pinned[0]
    identity = catalog.get("identity") if isinstance(catalog, dict) else None
    if (not isinstance(source, dict) or not isinstance(identity, dict)
            or source.get("source_id") != "shared_market_data" or catalog.get("source_id") != source["source_id"]
            or source.get("kind") != "manifest_json" or catalog.get("kind") != "manifest_json"
            or source.get("root") != str(root) or catalog.get("root") != str(root)
            or source.get("snapshot_id") != head or catalog.get("snapshot_id") != head
            or source.get("manifest_path") != str(path) or identity.get("manifest_path") != str(path)
            or source.get("manifest_sha256") != head or identity.get("manifest_sha256") != head
            or source.get("publication_schema", "stock-data-publication-v1") not in _PUBLISHED_SCHEMAS
            or identity.get("publication_schema", "stock-data-publication-v1")
                != source.get("publication_schema", "stock-data-publication-v1")):
        raise CandidateReportError("evidence_descriptor_mismatch", "重建快照不是指定中央不可變 head 的單一來源。")
    unsigned = {key: value for key, value in descriptor.items() if key != "descriptor_sha256"}
    if descriptor.get("descriptor_sha256") != _digest(unsigned):
        raise CandidateReportError("evidence_descriptor_mismatch", "重建快照內部的完整內容指紋不符。")


def _selected_context_key(head, binding):
    if not isinstance(head, str) or not _SHA.fullmatch(head) or head != head.lower() or not isinstance(binding, dict):
        raise CandidateReportError("evidence_reference_invalid", "借用 context 的不可變 head 或啟用綁定不正確。")
    shared = str(Path(__file__).resolve().parents[2] / "shared")
    if shared not in sys.path:
        sys.path.insert(0, shared)
    import stock_daily_selected_deployment as deployment
    root = _PUBLISHED_ROOT.resolve()
    if deployment.runtime.PUBLISHED_ROOT.resolve() != root:
        raise CandidateReportError("evidence_output_escape", "重建來源的中央根目錄與固定允許位置不符。")
    construction = {"reconstruction": _PUBLISHED_RECONSTRUCTION, "consumer_id": "single",
        "selected_single_build": True, "policy_binding": deployment.contract.c0_policy_binding(consumer_id="single")}
    return str(root), head, _digest(binding), _digest(construction)


def _borrow_selected_context(head, binding):
    """Borrow only a resolver-verified context owned by this evidence operation."""
    scope = _SELECTED_CONTEXTS.get()
    if _PARSED_EVIDENCE.get() is None or scope is None or not scope[0]:
        return None
    key = _selected_context_key(head, binding)
    record = scope[0].get(key)
    if record is None:
        return None
    info = record["head"]
    path = _PUBLISHED_ROOT.resolve() / "_manifests" / (head + ".json")
    if info["path"] != str(path) or path.resolve() != path:
        raise CandidateReportError("evidence_output_escape", "借用 context 的 head 路徑不可重導到其他檔案。")
    _stable_json(path, sha256=head, size=info["size"], schema=record["head_schema"])
    if _selected_context_key(head, binding) != key:
        raise CandidateReportError("evidence_context_binding_changed", "借用期間的 context 建構綁定發生變更。")
    return record["context"]


def _resolve_published_descriptor(reference, payload):
    expected_keys = {"schema", "kind", "root", "path", "sha256", "size", "container_schema",
        "head_sha256", "snapshot_fingerprint", "descriptor_sha256", "reconstruction",
        "deployment_binding_sha256"}
    snapshot = payload.get("snapshot_binding") or {}
    head = reference.get("head_sha256")
    fingerprint = snapshot.get("snapshot_fingerprint")
    root = _PUBLISHED_ROOT.resolve()
    if (set(reference) != expected_keys or reference.get("schema") != _REFERENCE_SCHEMA
            or reference.get("kind") != "published_snapshot_descriptor"
            or not isinstance(head, str) or not _SHA.fullmatch(head) or head != head.lower()
            or head != snapshot.get("head_sha256") or reference.get("sha256") != head
            or not _SHA.fullmatch(str(fingerprint or ""))
            or reference.get("snapshot_fingerprint") != fingerprint
            or not _SHA.fullmatch(str(reference.get("descriptor_sha256", "")))
            or reference.get("container_schema") not in _PUBLISHED_SCHEMAS
            or reference.get("reconstruction") != _PUBLISHED_RECONSTRUCTION
            or reference.get("deployment_binding_sha256") != _published_deployment_digest(payload)
            or type(reference.get("size")) is not int or reference["size"] <= 0):
        raise CandidateReportError("evidence_reference_invalid", "中央快照重建引用的版本、大小或身分綁定不正確。")
    path = root / "_manifests" / (head + ".json")
    if (reference.get("root") != str(root) or reference.get("path") != str(path)
            or path.resolve() != path):
        raise CandidateReportError("evidence_output_escape", "中央快照引用不在唯一允許的不可變 manifest 路徑。")
    _, info = _stable_json(path, sha256=head, size=reference["size"], schema=reference["container_schema"])
    evidence = _PARSED_EVIDENCE.get()
    context_key = _selected_context_key(head, payload["deployment_binding"])
    cache_key = ("published_snapshot_descriptor", _digest(reference), context_key)
    descriptor = evidence.get(cache_key) if evidence is not None else None
    context = sources = None
    try:
        if descriptor is None:
            import stock_daily_selected_deployment as deployment
            try:
                context = deployment.load_selected_context(head, payload["deployment_binding"])
                sources = context["sources"]
                descriptor = sources.snapshot_descriptor()
            except Exception as exc:
                raise CandidateReportError("evidence_reconstruction_failed", "無法由指定不可變 head 精確重建快照。") from exc
            # Recheck exact head bytes after reconstruction; never trust a later path stat alone.
            _stable_json(path, sha256=info["sha256"], size=info["size"], schema=reference["container_schema"])
        _check_published_descriptor(descriptor, head, fingerprint)
        if _digest(descriptor) != reference["descriptor_sha256"]:
            raise CandidateReportError("evidence_descriptor_mismatch", "不可變 head 重建內容與原始完整快照指紋不同。")
        if context is not None:
            if _selected_context_key(head, payload["deployment_binding"]) != context_key:
                raise CandidateReportError("evidence_context_binding_changed", "重建期間的 context 建構綁定發生變更。")
            scope = _SELECTED_CONTEXTS.get()
            if evidence is not None and scope is not None and context_key not in scope[0]:
                scope[1].callback(sources.close)
                sources = None
                scope[0][context_key] = {"context": context, "head": info,
                    "head_schema": reference["container_schema"]}
        if evidence is not None:
            evidence[cache_key] = descriptor
        return descriptor
    finally:
        if sources is not None:
            sources.close()


def _make_published_descriptor_reference(payload, descriptor_digest):
    snapshot = payload["snapshot_binding"]
    head, fingerprint = snapshot["head_sha256"], snapshot.get("snapshot_fingerprint")
    _check_published_descriptor(snapshot["descriptor"], head, fingerprint)
    root = _PUBLISHED_ROOT.resolve()
    path = root / "_manifests" / (head + ".json")
    if path.resolve() != path:
        raise CandidateReportError("evidence_output_escape", "中央 head 路徑不可重導到其他檔案。")
    try:
        size = path.stat().st_size
    except OSError as exc:
        raise CandidateReportError("evidence_read_failed", "指定不可變 head 缺失或不可讀。") from exc
    reference = {"schema": _REFERENCE_SCHEMA, "kind": "published_snapshot_descriptor",
        "root": str(root), "path": str(path), "sha256": head, "size": size,
        "container_schema": snapshot["descriptor"]["sources"][0].get("publication_schema", "stock-data-publication-v1"),
        "head_sha256": head, "snapshot_fingerprint": fingerprint, "descriptor_sha256": descriptor_digest,
        "reconstruction": _PUBLISHED_RECONSTRUCTION, "deployment_binding_sha256": _published_deployment_digest(payload)}
    _resolve_published_descriptor(reference, payload)
    return reference


def _make_descriptor_reference(payload, output_dir):
    snapshot, binding = payload.get("snapshot_binding") or {}, payload.get("model_binding") or {}
    descriptor = snapshot["descriptor"]
    if not binding.get("path") or not binding.get("sha256") or not binding.get("schema"):
        raise CandidateReportError("evidence_model_binding_missing", "模型證據綁定不完整，不能改用未核對引用。")
    model, info = _stable_json(binding["path"], sha256=binding["sha256"], schema=binding["schema"])
    model_snapshot = _pointer(model, "/metadata/snapshot_binding")
    model_descriptor = _pointer(model, _MODEL_DESCRIPTOR_POINTER)
    descriptor_digest = _digest(descriptor)
    head = snapshot.get("head_sha256")
    if not _SHA.fullmatch(str(head or "")):
        raise CandidateReportError("evidence_head_missing", "當期快照缺少有效內容指紋，不能建立完整證據引用。")
    if _digest(model_descriptor) == descriptor_digest and model_snapshot.get("head_sha256") == head:
        return {"schema": _REFERENCE_SCHEMA, "kind": "model_json", **info,
                "container_schema": model["schema"], "json_pointer": _MODEL_DESCRIPTOR_POINTER,
                "head_sha256": head, "descriptor_sha256": descriptor_digest}
    if _read_only_storage(payload):
        return _make_published_descriptor_reference(payload, descriptor_digest)
    return _save_descriptor_evidence(descriptor, head, output_dir)


def _resolve_descriptor_reference(reference, payload, report_path=None):
    snapshot, binding = payload.get("snapshot_binding") or {}, payload.get("model_binding") or {}
    if not isinstance(reference, dict) or reference.get("schema") != _REFERENCE_SCHEMA:
        raise CandidateReportError("evidence_reference_invalid", "完整證據引用格式不正確。")
    for key in ("sha256", "descriptor_sha256", "head_sha256"):
        if not _SHA.fullmatch(str(reference.get(key, ""))):
            raise CandidateReportError("evidence_reference_invalid", "完整證據引用缺少有效指紋。")
    if reference["head_sha256"] != snapshot.get("head_sha256"):
        raise CandidateReportError("evidence_head_mismatch", "完整證據引用與當期快照指紋不符。")
    kind = reference.get("kind")
    if kind == "published_snapshot_descriptor":
        if not binding.get("path") or not binding.get("sha256") or not binding.get("schema"):
            raise CandidateReportError("evidence_model_binding_missing", "模型證據綁定不完整，不能接受引用。")
        _stable_json(binding["path"], sha256=binding["sha256"], schema=binding["schema"])
        return _resolve_published_descriptor(reference, payload)
    elif kind == "model_json":
        if (reference.get("json_pointer") != _MODEL_DESCRIPTOR_POINTER
                or reference.get("container_schema") != binding.get("schema")
                or reference.get("sha256") != str(binding.get("sha256", "")).lower()
                or Path(reference.get("path", "")).resolve() != Path(binding.get("path", "")).resolve()):
            raise CandidateReportError("evidence_model_binding_mismatch", "完整證據引用與模型綁定不符。")
    elif kind == "report_evidence_json":
        root = Path(reference.get("output_dir", "")).resolve()
        target = Path(reference.get("path", "")).resolve()
        if (not reference.get("output_dir") or not target.is_relative_to(root / "evidence")
                or report_path is not None and not Path(report_path).resolve().is_relative_to(root)
                or reference.get("container_schema") != _EVIDENCE_SCHEMA
                or reference.get("json_pointer") != "/descriptor"):
            raise CandidateReportError("evidence_output_escape", "完整證據引用不在指定輸出目錄，或節點不正確。")
        if not binding.get("path") or not binding.get("sha256") or not binding.get("schema"):
            raise CandidateReportError("evidence_model_binding_missing", "模型證據綁定不完整，不能接受引用。")
        _stable_json(binding["path"], sha256=binding["sha256"], schema=binding["schema"])
    else:
        raise CandidateReportError("evidence_reference_invalid", "不支援此種完整證據引用。")
    if type(reference.get("size")) is not int or reference["size"] <= 0:
        raise CandidateReportError("evidence_reference_invalid", "完整證據引用缺少有效檔案大小。")
    document, _ = _stable_json(reference.get("path"), sha256=reference["sha256"], size=reference["size"],
                               schema=reference.get("container_schema"))
    node = _pointer(document, reference["json_pointer"])
    container_head = (_pointer(document, "/metadata/snapshot_binding").get("head_sha256")
                      if kind == "model_json" else document.get("head_sha256"))
    if container_head != reference["head_sha256"]:
        raise CandidateReportError("evidence_head_mismatch", "完整證據容器與引用的快照指紋不符。")
    if _digest(node) != reference["descriptor_sha256"]:
        raise CandidateReportError("evidence_descriptor_mismatch", "完整證據節點內容與宣告指紋不符。")
    if kind == "report_evidence_json" and document.get("descriptor_sha256") != reference["descriptor_sha256"]:
        raise CandidateReportError("evidence_descriptor_mismatch", "完整證據包內的節點指紋不符。")
    return node


def _restore_descriptor(payload, descriptor):
    snapshot = dict(payload.get("snapshot_binding") or {})
    snapshot.pop("descriptor_reference", None)
    snapshot["descriptor"] = descriptor
    return {**payload, "snapshot_binding": snapshot}


def _prepare_report_payload(payload, output_dir):
    snapshot = payload.get("snapshot_binding") or {}
    if "descriptor" in snapshot and "descriptor_reference" in snapshot:
        raise CandidateReportError("evidence_reference_ambiguous", "完整證據與引用同時出現，無法確定原始綁定。")
    if "descriptor" not in snapshot:
        reference = snapshot.get("descriptor_reference")
        if reference is not None:
            descriptor = _resolve_descriptor_reference(reference, payload)
            return payload, _digest(_restore_descriptor(payload, descriptor)), reference
        return payload, _digest(payload), None
    reference = _make_descriptor_reference(payload, output_dir)
    compact_snapshot = {key: value for key, value in snapshot.items() if key != "descriptor"}
    compact_snapshot["descriptor_reference"] = reference
    return {**payload, "snapshot_binding": compact_snapshot}, _digest(payload), reference


def _evidence_summary(payload, reference=None, descriptor_digest=None):
    snapshot = payload.get("snapshot_binding") or {}
    summary = {key: snapshot[key] for key in ("head_sha256", "snapshot_fingerprint", "published_at", "read_at_utc", "read_at")
               if key in snapshot and isinstance(snapshot[key], (str, int, float, bool, type(None)))}
    return {"model_binding": payload.get("model_binding"), "deployment_binding": payload.get("deployment_binding"),
            "deployment_authorization_reverified": False, "snapshot_summary": summary,
            "descriptor_reference": reference, "descriptor_sha256": descriptor_digest,
            "source_count": len(payload.get("source_manifest") or []), "upstream_evidence_reverified": False,
            "descriptor_reference_verified": reference is not None}


def _check_payload(payload):
    if not isinstance(payload, dict) or payload.get("schema") not in {SCHEMA, FORMAL_SCHEMA}:
        raise CandidateReportError("schema_invalid", "輸入不是指定的個股分析報告格式。")
    formal = payload["schema"] == FORMAL_SCHEMA
    if (payload.get("language") != "zh-TW" or payload.get("research_only") is not (not formal)
            or payload.get("promotion") is not formal):
        raise CandidateReportError("report_boundary_invalid", "報告語言、格式與正式／研究用途不一致。")
    if formal:
        deployment, model = payload.get("deployment_binding"), payload.get("model_binding")
        if (not isinstance(deployment, dict) or not isinstance(model, dict)
                or deployment.get("schema") != "single-stock-selected-deployment-v1"
                or deployment.get("consumer_id") != "single"
                or not isinstance(deployment.get("activation_id"), str) or not deployment["activation_id"].strip()
                or not _SHA.fullmatch(str(model.get("sha256", "")))
                or deployment.get("model_sha256") != model.get("sha256")):
            raise CandidateReportError("deployment_binding_invalid", "正式報告的啟用身分或模型綁定不一致。")
    if "preopen_h1" in payload:
        _check_preopen_h1(payload)
    context = _report_context(payload)
    if context is not None and (str(context.get("stock_id")) != str((payload.get("stock") or {}).get("stock_id"))
            or not _day(context.get("asof")) or _day(context.get("asof")) != _day(payload.get("asof"))
            or not isinstance(context.get("sources"), list)):
        raise CandidateReportError("report_context_binding_invalid", "正文上下文的股票、資料日或來源格式與本次報告不一致。")
    _json_bytes(payload)


def _check_preopen_h1(payload):
    block = payload.get("preopen_h1")
    valid = (isinstance(block, dict) and block.get("schema") == PREOPEN_H1_SCHEMA
             and payload.get("horizon_sessions") == 1 and block.get("status") in {"used", "fallback", "disabled"})
    if valid and block["status"] == "used":
        p_up, exp_ret = _num(block.get("p_up")), _num(block.get("exp_ret"))
        valid = (p_up is not None and 0 <= p_up <= 1 and exp_ret is not None
                 and str(block.get("stock_id")) == str((payload.get("stock") or {}).get("stock_id")))
    elif valid:
        valid = isinstance(block.get("notice"), str) and bool(block["notice"].strip())
    if not valid:
        raise CandidateReportError("preopen_block_invalid", "開盤前版 H1 區塊格式或身分不正確，停止產出。")


def _report_title(payload):
    if payload["schema"] == FORMAL_SCHEMA and _deployment_status(payload) == "preactivation_audit":
        return "正式個股分析報告（啟用前驗收）"
    return "正式個股分析報告" if payload["schema"] == FORMAL_SCHEMA else "個股研究分析報告"


def _deployment_status(payload):
    binding = payload.get("deployment_binding") or {}
    binding = binding if isinstance(binding, dict) else {}
    statuses = (payload.get("deployment_status"), binding.get("deployment_status"), binding.get("status"))
    if "preactivation_audit" in statuses:
        return "preactivation_audit"
    return next((status for status in statuses if status), None)


def _check_output_path(payload, path):
    if payload["schema"] == FORMAL_SCHEMA and any(
            word in str(Path(path).resolve()).lower() for word in ("candidate", "research", "staging")):
        raise CandidateReportError("formal_output_path_invalid", "正式報告路徑與檔名不得含候選、研究或暫存標記。")


def _table(title, columns, rows, widths=None):
    return {"title": title, "columns": columns, "rows": rows, "widths": widths}


def _section(key, title, paragraphs=(), tables=(), chart=None):
    result = {"id": key, "title": title, "paragraphs": list(paragraphs), "tables": list(tables)}
    if chart is not None:
        result["chart"] = chart
    return result


def _price_view(payload):
    asof = _day(payload.get("asof"))
    raw = payload.get("price_history") or []
    if not asof or not isinstance(raw, list):
        return {"dates": [], "notes": ["分析資料日或價量歷史未提供，無法繪圖。"]}
    end = date.fromisoformat(asof)
    month = end.year * 12 + end.month - 1 - 18
    start = date(month // 12, month % 12 + 1, min(end.day, calendar.monthrange(month // 12, month % 12 + 1)[1]))
    field = "adjusted_close" if any(isinstance(r, dict) and "adjusted_close" in r for r in raw) else "close"
    indexed, conflicts, rejected = {}, set(), 0
    for row in raw:
        if not isinstance(row, dict) or not (day := _day(row.get("date", row.get("session_date")))):
            rejected += 1
            continue
        if not (start.isoformat() <= day <= asof):
            continue
        close, volume = _num(row.get(field)), _num(row.get("volume", row.get("Trading_Volume")))
        values = (close if close is not None and close > 0 else None, volume if volume is not None and volume >= 0 else None)
        if day in indexed and indexed[day] != values:
            conflicts.add(day)
        indexed[day] = values
    for day in conflicts:
        indexed[day] = (None, None)
    days = sorted(indexed)
    close, volume = [indexed[d][0] for d in days], [indexed[d][1] for d in days]
    averages = {}
    for period in (5, 20, 60):
        averages[str(period)] = [sum(part) / period if len(part := close[max(0, i - period + 1):i + 1]) == period
                                and all(v is not None for v in part) else None for i in range(len(close))]
    adjusted = field == "adjusted_close" or bool(raw) and all(not isinstance(r, dict) or r.get("price_basis") == "adjusted" for r in raw)
    volume_unit = payload.get("price_volume_unit") or (payload.get("analysis_context") or {}).get("volume_unit")
    notes = [f"目標顯示窗為 {start.isoformat()} 至 {asof}，只呈現實際提供的歷史；不足十八個月不補造。",
             "五、二十、六十日均線為連續提供且有效的收盤觀測值算術平均；未另取得完整交易日軸，不能保證缺日均已辨識。",
             "圖中斷線表示已提供列的價格缺值；沒有列出的日期不視為已證明非交易日。",
             "價量僅描述歷史；不構成買賣建議或未來獲利承諾。"]
    if rejected or conflicts:
        notes.append(f"無效日期列 {rejected} 筆；日期衝突 {len(conflicts)} 日已留缺值，不任選其中價格。")
    unit = _unit(volume_unit)
    return {"dates": days, "close": close, "volume": volume, "averages": averages, "notes": notes,
            "price_label": "還原收盤價" if adjusted else "收盤價（還原基準未提供）",
            "volume_unit": unit, "volume_label": "成交量" + ("（" + unit + "）" if unit != _unit(None) else "")}


def _report_context(payload):
    context = payload.get("report_context")
    return context if isinstance(context, dict) and context.get("schema") == _REPORT_CONTEXT_SCHEMA else None


def _context_sources(payload):
    context = _report_context(payload)
    if context is None:
        context = payload.get("analysis_context") or {}
    sources = list(context.get("sources") or [])
    known = {str(s.get("route_id")) for s in sources if isinstance(s, dict)}
    for category in ("fundamentals", "flows", "news", "global", "technical"):
        value = context.get(category)
        for item in value if isinstance(value, list) else [value] if isinstance(value, dict) else []:
            if isinstance(item, dict) and item.get("route_id") not in known:
                sources.append(item)
                known.add(item.get("route_id"))
    manifests = {s.get("route_id"): s for s in payload.get("source_manifest") or [] if isinstance(s, dict)}
    return [{**manifests.get(s.get("route_id"), {}), **s} for s in sources if isinstance(s, dict)]


def _record_period(row):
    values = []
    for key, label in (("date", "資料日"), ("session_date", "交易日"), ("period_end", "期末日"),
                       ("period", "期間"), ("year", "年度"), ("quarter", "季度"),
                       ("revenue_year", "營收年度"), ("revenue_month", "營收月份")):
        value = row.get(key)
        if value is not None:
            values.append(label + " " + (_time(value) if key in {"date", "session_date", "period_end"} else _code(value)))
    return "；".join(values) or "期間未提供"


def _context_identity(record, route):
    names = {"Foreign_Investor": "外資", "Investment_Trust": "投信", "Dealer_self": "自營商自行買賣",
             "Dealer_Hedging": "自營商避險", "Foreign_Dealer_Self": "外資自營商",
             "SP500": "美國標普指數", "NASDAQ": "美國那斯達克指數", "SOX": "美國半導體指數"}
    if route == "tdcc_share_distribution_raw":
        level = record.get("holding_level", record.get("持股分級", record.get("level")))
        if level is not None and str(level).strip():
            return "持股級距 " + _code(level)
    identities = [record[key].strip() for key in ("name_zh", "name", "institutional_investor", "institution",
                  "indicator_name_zh", "indicator_name", "series_name_zh", "instrument_name_zh",
                  "label", "sector_label") if isinstance(record.get(key), str) and record[key].strip()]
    prefix = next((names[value] if value in names else _zh(value) for value in identities
                   if value in names or _HAN.search(value)), "")
    codes = []
    for key in ("symbol", "instrument", "series_id", "indicator_id", "indicator", "feature_name", "entity_id", "sector_key", "currency"):
        value = record.get(key)
        if isinstance(value, (str, int)) and not isinstance(value, bool) and str(value).strip():
            code = _tc(str(value).strip())
            if code not in codes:
                codes.append(code)
    if codes:
        label = "標的／指標代碼：" + "、".join(codes)
        return prefix + "（" + label + "）" if prefix else label + "（中文名稱未核對）"
    requires_identity = route in _GLOBAL or route in {"official_sector_features", "finmind_industry_chain_flow"}
    if prefix or not requires_identity:
        return prefix
    if identities:
        return "標的／指標識別：" + _tc(identities[0]) + "（中文名稱未核對）"
    return "標的未識別"


def _context_tables(sources):
    tables, notes = [], []
    for source in sources:
        route = source.get("route_id")
        name = _route(route, source.get("name_zh"))
        usage = _state(source.get("usage"))
        state = _state(source.get("availability", source.get("status")))
        records = source.get("records") or []
        notes.append(f"{name}：{usage}；{state}。" + _zh(source.get("reason_zh"), "可得時間與適用性依來源證據，不由發布成功推定。"))
        if source.get("context_scope_zh"):
            notes.append(_zh(source["context_scope_zh"]))
        if source.get("usage") in {"not-selected", "not_selected"}:
            continue
        rows, unsupported, undated_news = [], set(), 0
        for record in records:
            if not isinstance(record, dict):
                continue
            period = _record_period(record)
            available = record.get("available_at", record.get("available_time"))
            published = record.get("published_at", record.get("publication_time"))
            timing = "宣告可得時間 " + _time(available) if available else "可得時間未提供"
            timing += "；宣告發布時間 " + _time(published) if published else "；歷史公告時點未驗證"
            if record.get("source_close_at"):
                timing += "；來源市場收盤時間 " + _time(record["source_close_at"])
            prefix = _context_identity(record, route)
            if route == "news_and_hot_topics_raw":
                stamp = record.get("published_at", record.get("publication_time", record.get("date")))
                origin = record.get("source", record.get("url", source.get("source")))
                if not stamp or not origin:
                    undated_news += 1
                    continue
                title = _zh(record.get("title_zh", record.get("title", record.get("headline"))), "此則新聞無可用中文標題，原文保留於資料檔")
                summary = _zh(record.get("summary_zh", record.get("summary")), "未提供中文摘要，不自行推論事件影響。")
                rows.append([period, title, summary, "來源 " + _zh(origin, "來源網址保留於資料檔") + "；發布 " + _time(stamp) + "；" + timing])
                continue
            topic = record.get("type", record.get("account", record.get("account_name")))
            topic_zh = _FIELDS.get(str(topic), _zh(record.get("label_zh", record.get("origin_name", topic)), "未映射科目（原名見資料檔）"))
            for key, value in record.items():
                label = _FIELDS.get(key)
                if label is None:
                    if _num(value) is not None and key not in {"stock_id", "data_id", "year", "quarter", "revenue_year", "revenue_month"}:
                        unsupported.add(key)
                    continue
                if key == "value" and topic is not None:
                    label = topic_zh
                if _num(value) is not None or value is None:
                    metric_unit = "倍" if key in {"PER", "per", "PBR", "pbr"} else _field_unit(record, source, key)
                    rows.append([period, (prefix + "／" if prefix else "") + label,
                                 _number(value) + "（" + metric_unit + "）", timing])
            for key, label in (("stock_name", "公司名稱"), ("market", "市場"), ("industry", "產業"),
                               ("industry_category", "產業分類"), ("description_zh", "來源說明")):
                if record.get(key):
                    value = {"TWSE": "上市市場", "TPEX": "上櫃市場"}.get(str(record[key]), _zh(record[key]))
                    rows.append([period, label, value, timing])
        if rows:
            tables.append(_table(name, ["日期與期間", "科目／項目", "實際值／內容", "時間證據"], rows, [0.20, 0.25, 0.29, 0.26]))
        elif records:
            notes.append(name + "有投影資料，但未找到可解讀的已映射欄位；不將原始英文資料直接當成分析。")
        if unsupported:
            notes.append(f"{name}另有 {len(unsupported)} 種未映射數值欄位，保留於機器資料附件；本報告不猜測其定義或單位。")
        if undated_news:
            notes.append(f"另有 {undated_news} 則新聞缺來源或發布日期，未採為有證據的事件敘述。")
    return tables, notes


def _feature_rows(payload):
    adopted, other = [], []
    for item in payload.get("feature_values") or []:
        if not isinstance(item, dict):
            continue
        validity = item.get("feature_validity", item.get("validity")) or {}
        if not isinstance(validity, dict):
            validity = {}
        if "state" in validity:
            validity = {"direction": validity}
        tasks = item.get("selected_by_task") or {}
        coverage = item.get("coverage") or {}
        descriptions = []
        task_groups = []
        effective = False
        for task in ("direction", "return"):
            info, selected = validity.get(task) or {}, tasks.get(task) or {}
            state = info.get("state")
            numeric = selected.get("numeric") is True and info.get("numeric_effective") is True and state == "fit_informative"
            indicator = selected.get("missing_indicator") is True and info.get("missing_indicator_effective") is True
            if not tasks:
                numeric = item.get("selected") is True and info.get("numeric_effective") is True and state == "fit_informative"
                indicator = item.get("selected") is True and info.get("missing_indicator_effective") is True
            effective |= numeric or indicator
            if info or selected:
                role = "有效數值輸入" if numeric else "僅有效缺值指示輸入" if indicator else "不是有效數值輸入"
                description = role + "；" + _state(state)
                if info:
                    description += f"；訓練有效 {_number(info.get('n_fit_finite'))}／{_number(info.get('n_fit_rows'))} 筆"
                blocks = (coverage.get("training_by_task") or {}).get(task) or {}
                for block, counts in blocks.items():
                    if block not in {"fit", "selection", "test", "calibration"} or not isinstance(counts, dict):
                        continue
                    stage = {"fit": "訓練", "selection": "選模", "test": "保留測試", "calibration": "校準"}[block]
                    description += "\n" + stage + "有限值 " + _number(counts.get("n_finite")) + "／" + _number(counts.get("n_denominator"))
                    description += " 筆；覆蓋 " + _number(counts.get("coverage"), True)
                signature = (info, numeric, indicator, selected.get("selected"), blocks)
                match = next((group for group in task_groups if group[0] == signature), None)
                if match is None:
                    task_groups.append([signature, [_TASK[task]], description])
                else:
                    match[1].append(_TASK[task])
        for _, labels, description in task_groups:
            descriptions.append("／".join(labels) + "：" + description)
        if not descriptions:
            descriptions.append("訓練有效性或實際分支採用證據未提供，不宣稱已參與學習。")
        if any((v or {}).get("state") in {"fit_all_missing", "fit_constant"} for v in validity.values() if isinstance(v, dict)):
            descriptions.append("當期即使可計算，也不能當成訓練時已學會此數值訊號；凍結處理依模型封存規則。")
        inference = coverage.get("inference")
        if isinstance(inference, dict):
            for key in ("n_denominator", "n_finite", "n_missing", "coverage"):
                if key in inference:
                    descriptions.append("特徵建構範圍" + _METRIC[key] + "：" + _number(inference[key], key == "coverage"))
        row = [_zh(item.get("label_zh"), "特徵名稱未提供中文對照"),
               _number(item.get("value")) + "（" + _unit(item.get("unit")) + "）",
               _zh(item.get("calculation_zh"), "未提供計算定義，不自行補寫公式。") + "；來源：" + _route(item.get("source_route")),
               "\n".join(descriptions)]
        (adopted if effective else other).append(row)
    return adopted, other


def _metric_tables(metrics):
    """One row per model/partition/market, not one page-sized dump per scalar."""
    tables = []
    market_names = {"TWSE": "上市市場", "TPEX": "上櫃市場"}
    partitions = {"fit": "訓練", "calibration": "校準", "selection": "選模", "test": "保留測試", "holdout": "保留測試"}

    def value(stats, key, percent=False):
        number = _num(stats.get(key))
        return "不可計算" if number is None else _number(number, percent)

    def block(stats, keys, percent=False):
        return "／".join(value(stats, key, percent) for key in keys)

    def rows_for(entries, task):
        rows = []
        for name, partition, branches, parent in entries:
            stats = branches.get(task)
            if not isinstance(stats, dict):
                continue
            markets = [("全體", stats)] + [(market_names.get(str(m), _zh(m, "其他市場")), s)
                                             for m, s in (stats.get("by_market") or {}).items() if isinstance(s, dict)]
            for market, current in markets:
                label = name + "\n" + partition + "／" + market
                counts = block(current, ("n_denominator", "n_labeled", "n_evaluated"))
                failures = block(current, ("n_predicted", "n_failed", "n_unscored"))
                coverage = block(current, ("coverage", "evaluation_coverage"), True)
                days = block(current, ("n_signal_days", "n_effective_signal_days", "n_stocks"))
                classes = current.get("class_counts") or {}
                qualification = []
                if parent.get("status"):
                    qualification.append(_state(parent["status"]))
                if current.get("holdout_status"):
                    qualification.append(_state(current["holdout_status"]))
                for source in (parent, current):
                    if source.get("reason_zh"):
                        qualification.append(_zh(source["reason_zh"]))
                if task == "direction":
                    accuracy = current.get("accuracy", current.get("holdout_accuracy"))
                    accuracy_pair = value({"accuracy": accuracy}, "accuracy", True) + "／" + value(current, "balanced_accuracy", True)
                    scores = value(current, "brier") + "／" + value(current, "log_loss") + "／" + value(current, "auc")
                    class_text = "上漲類別筆數／非上漲類別筆數：" + block(classes, ("up", "not_up"))
                    rows.append([label, accuracy_pair, scores, counts, failures, coverage, days,
                                 class_text + "\n" + "；".join(dict.fromkeys(qualification))])
                else:
                    rows.append([label, block(current, ("mae", "rmse")), counts, failures, coverage, days,
                                 "；".join(dict.fromkeys(qualification)) or "資格與原因未提供"])
        return rows

    def add_scores(entries, prefix):
        direction = rows_for(entries, "direction")
        returns = rows_for(entries, "return")
        if direction:
            tables.append(_table(prefix + "：方向指標", ["候選／區間／市場", "正確率／平衡正確率", "Brier／對數損失／AUC",
                "原分母／成熟／評分", "預測／失敗／未評分", "預測／評分覆蓋率", "訊號日／有效日／股票", "類別、資格與原因"],
                direction, [0.17, 0.10, 0.135, 0.105, 0.105, 0.10, 0.105, 0.18]))
        if returns:
            tables.append(_table(prefix + "：報酬指標", ["候選／區間／市場", "MAE 平均絕對誤差／RMSE 均方根誤差",
                "原分母／成熟／評分", "預測／失敗／未評分", "預測／評分覆蓋率", "訊號日／有效日／股票", "資格與原因"],
                returns, [0.19, 0.15, 0.12, 0.12, 0.11, 0.12, 0.19]))

    comparison, holdout = metrics.get("comparison") or {}, metrics.get("holdout") or {}
    entries, candidates = [], []
    for name in dict.fromkeys([*comparison, *holdout]):
        candidate = comparison.get(name) or {}
        candidates.append([_kind(name), _state(candidate.get("status")), _zh(candidate.get("reason_zh"), "未提供候選狀態原因"),
                           value(candidate, "n_denominator")])
        if isinstance(candidate.get("selection"), dict):
            entries.append((_kind(name), "選模", candidate["selection"], candidate))
        test, final = candidate.get("test"), holdout.get(name)
        if isinstance(test, dict) and isinstance(final, dict) and test != final:
            entries.extend([(_kind(name), "保留測試（候選紀錄）", test, candidate),
                            (_kind(name), "保留測試（彙整紀錄，與候選不同）", final, candidate)])
        elif isinstance(final if final is not None else test, dict):
            entries.append((_kind(name), "保留測試", final if final is not None else test, candidate))
    if candidates:
        tables.append(_table("候選訓練狀態（失敗保留）", ["候選", "狀態", "原因", "原始分母"], candidates, [0.26, 0.16, 0.43, 0.15]))
    add_scores(entries, "候選比較（相同保留測試只列一次）")
    selection = metrics.get("selection") or {}
    selected_rows = []
    for task, kind in (selection.get("selected") or {}).items():
        reason = (selection.get("reasons") or {}).get(task)
        selected_rows.append([_TASK.get(task, "模型分支"), _kind(kind),
                              _zh(reason.get("reason_zh") if isinstance(reason, dict) else reason, "未提供中文選模原因")])
    if selected_rows:
        tables.append(_table("選定模型與選模原因", ["分支", "選定候選", "原因"], selected_rows, [0.15, 0.30, 0.55]))
    baseline = metrics.get("baselines") or {}
    baseline_methods = [[_TASK[task], _kind(info.get("kind")), value(info, "value")]
                        for task in _TASK if isinstance(info := baseline.get(task), dict)]
    if baseline_methods:
        tables.append(_table("比較基準定義（不作真實模型）", ["分支", "方法", "基準值"], baseline_methods, [0.15, 0.55, 0.30]))
    baseline_entries = [("比較基準", partitions.get(stage, "補充區間"), branches, {})
                        for stage, branches in baseline.items() if stage in partitions and isinstance(branches, dict)]
    add_scores(baseline_entries, "基準結果")
    paired = []
    for stage, branches in (metrics.get("paired_differences") or {}).items():
        for task, stats in branches.items():
            if not isinstance(stats, dict):
                continue
            worse = stats.get("worse_than_baseline")
            paired.append([partitions.get(stage, "補充區間") + "／" + _TASK.get(task, "模型分支"),
                           _METRIC.get(str(stats.get("metric")), "指標定義未提供"), value(stats, "model_minus_baseline"),
                           block(stats, ("n_paired", "n_denominator")),
                           "是（模型劣化保留）" if worse is True else "否" if worse is False else "不可計算"])
    if paired:
        tables.append(_table("模型減基準的成對誤差差異", ["區間／分支", "指標", "模型減基準", "成對／原分母", "是否劣化"],
                             paired, [0.20, 0.25, 0.16, 0.17, 0.22]))
    split_rows = []
    for stage, stats in (metrics.get("splits") or {}).items():
        if not isinstance(stats, dict):
            continue
        for market, current in [("全體", stats), *((market_names.get(str(m), _zh(m, "其他市場")), s)
                                                   for m, s in (stats.get("by_market") or {}).items() if isinstance(s, dict))]:
            split_rows.append([partitions.get(stage, "補充區間") + "／" + market,
                               _time(current.get("start", stats.get("start"))) + " 至 " + _time(current.get("end", stats.get("end"))),
                               _time(stats.get("next_signal_start")), block(current, ("n_denominator", "n_rows", "n_excluded")),
                               block(current, ("n_signal_days", "n_effective_days", "n_stocks")),
                               block(current.get("class_counts") or {}, ("up", "not_up")),
                               _time(current.get("first_label_end")) + " 至 " + _time(current.get("last_label_end"))])
    if split_rows:
        tables.append(_table("時間切分與樣本分母", ["區間／市場", "訊號日期起訖", "下一區間起日", "原分母／有效列／排除",
                     "訊號日／有效日／股票", "上漲／非上漲", "標籤到期起訖"], split_rows,
                     [0.12, 0.17, 0.13, 0.15, 0.15, 0.11, 0.17]))
    denominator = metrics.get("denominator") or {}
    if denominator:
        tables.append(_table("整體研究樣本分母", ["原始資料列", "合格成熟標籤", "特徵就緒列", "股票數", "訊號日數"],
                             [[value(denominator, key) for key in ("n_rows", "n_label_eligible", "n_feature_ready", "n_stocks", "n_signal_days")]]))
    return tables


def _source_tables(payload):
    groups = {"selected": [], "not_selected": [], "missing": [], "empty": []}
    for item in payload.get("source_manifest") or []:
        if not isinstance(item, dict):
            continue
        usage, availability = item.get("usage"), item.get("availability", item.get("status"))
        group = "not_selected" if usage in {"not-selected", "not_selected"} else "empty" if availability in {"legitimate_empty", "not_applicable"} else "missing" if availability in {"missing", "unknown_availability", "unavailable"} else "selected"
        coverage = item.get("coverage")
        if isinstance(coverage, dict):
            detail = "；".join(_METRIC[k] + " " + _number(v, k == "coverage") for k, v in coverage.items() if k in _METRIC)
            detail = detail or "覆蓋分母定義見資料檔，未推定完整率"
        else:
            detail = _number(coverage) if _num(coverage) is not None else "覆蓋未提供"
        fields = item.get("fields") or []
        labels = [_FIELDS.get(str(f), _zh(f, "未映射欄位")) for f in fields] if isinstance(fields, list) else []
        reason = _zh(item.get("reason_zh"), "未提供中文原因；原始原因保留於資料檔。")
        if availability == "legitimate_empty" and not any(item.get(k) for k in ("empty_evidence", "availability_evidence", "evidence", "legitimate_empty_evidence")):
            reason += "；提供者標記合法無資料，但本報告未取得正向證據細節，不視為已驗證。"
        timing = item.get("available_time_basis", item.get("availability_basis"))
        if ("lag" in str(timing).lower() or item.get("lag_sessions") or item.get("availability_lag_sessions")
                or item.get("historical_PIT_unverified") is True):
            lag_value = item.get("availability_lag_sessions", item.get("lag_sessions"))
            lag = "宣告延後 " + _number(lag_value) + " 個交易日" if lag_value is not None else "實際延後規則見隨附資料檔"
            reason += "；使用延後交易日的日期代理（" + lag + "），歷史公告時點未驗證，不是歷史時點可得性已驗證。"
            if item.get("availability_assumption"):
                reason += "；" + _zh(item["availability_assumption"])
        groups[group].append([_route(item.get("route_id"), item.get("name_zh")),
                              _state(usage) + "；" + _state(availability) + ("；原發布狀態：" + _state(item["status"]) if item.get("status") else ""), reason,
                              detail + "；最新資料日 " + (_day(item.get("latest_data_date")) or "未提供") +
                              ("；欄位：" + "、".join(labels) if labels else "")])
    names = {"selected": "已選用與當期上下文來源", "not_selected": "未選用來源（不算缺失）",
             "missing": "選中但缺失或可得性未知", "empty": "合法無資料與不適用（保留證據限制）"}
    return [_table(names[k], ["來源", "用途與狀態", "原因與限制", "覆蓋與資料時間"], rows, [0.19, 0.24, 0.32, 0.25])
            for k, rows in groups.items() if rows]


_BRIEF_ACCOUNTS = {
    "Revenue", "GrossProfit", "OperatingIncome", "IncomeAfterTaxes", "NetIncome", "EPS",
    "TotalAssets", "TotalLiabilities", "Liabilities", "Equity", "CashAndCashEquivalents",
    "CashFlowsFromOperatingActivities", "OperatingCashFlow", "CashFlowsFromInvestingActivities",
    "CashFlowsFromFinancingActivities", "ifrs-full:CashFlowsFromUsedInOperatingActivities",
    "ifrs-full:IncreaseDecreaseInCashAndCashEquivalents",
}
_BRIEF_LABELS = {**_FIELDS, "Liabilities": "負債總額",
                 "ifrs-full:CashFlowsFromUsedInOperatingActivities": "營業活動現金流量",
                 "ifrs-full:IncreaseDecreaseInCashAndCashEquivalents": "現金及約當現金增減",
                 "cash_earnings_distribution": "盈餘現金股利", "source_industry": "產業分類",
                 "spot_buy": "即期買入匯率", "spot_sell": "即期賣出匯率",
                 "adj_close": "還原收盤值", "Price": "價格", "占集保庫存數比例%": "持股比例",
                 "人數": "持有人數", "股數": "持股股數"}
_BRIEF_ALIASES = {"per": "PER", "pbr": "PBR", "CashEarningsDistribution": "cash_earnings_distribution",
                  "ForeignInvestmentSharesRatio": "holding_percentage", "MarginPurchaseTodayBalance": "margin_purchase_balance",
                  "ShortSaleTodayBalance": "short_sale_balance", "DayTradingVolume": "day_trade_volume",
                  "占集保庫存數比例%": "holding_percentage", "人數": "number_of_people", "股數": "shares"}
_BRIEF_FLOW = {"foreign_investors_net", "investment_trust_net", "dealer_net", "total_net",
               "margin_purchase_balance", "margin_purchase_change", "short_sale_balance", "short_sale_change",
               "day_trade_volume", "holding_percentage", "ForeignInvestmentSharesRatio",
               "MarginPurchaseTodayBalance", "ShortSaleTodayBalance", "DayTradingVolume",
               "buy", "sell", "net", "net_buy", "buy_sell", "buy_sell_diff", "BuyAmount", "SellAmount",
               "NumberOfSharesIssued", "number_of_people", "percent", "shares", "占集保庫存數比例%", "人數", "股數"}
_RAW_VALUE_NOTE = "金額與數量保留來源原始值；不同來源不作直接換算比較。"
_PERIOD_VALUE_NOTE = "期間口徑：財報期末不是公告時間，累計不當單季；比較保留來源單位。"
_GLOBAL_CLOSE_LABELS = {
    "^TNX": "美國10年期公債殖利率", "GC=F": "黃金期貨", "CL=F": "WTI 原油期貨",
    "JPY=X": "美元兌日圓", "KRW=X": "美元兌韓元", "TWD=X": "美元兌新臺幣",
    "^GSPC": "美國標普500指數", "^IXIC": "美國那斯達克指數", "^SOX": "美國費城半導體指數",
    "^DJI": "美國道瓊工業指數",
    "us_sp500": "美國標普500指數", "us_nasdaq": "美國那斯達克指數", "us_semiconductor": "美國費城半導體指數",
}


def _brief_context_label(row, route, field):
    if route == "global_market_history":
        symbol = row.get("symbol") or row.get("instrument") or row.get("indicator")
        if field in {"close", "adj_close"} and symbol in _GLOBAL_CLOSE_LABELS:
            display = {"us_sp500": "^GSPC", "us_nasdaq": "^IXIC", "us_semiconductor": "^SOX"}.get(symbol, symbol)
            return _GLOBAL_CLOSE_LABELS[symbol] + "（" + display + "）" + ("／還原收盤值" if field == "adj_close" else "")
        legacy = {"SOX": "美國費城半導體指數", "NASDAQ": "美國那斯達克指數", "SP500": "美國標普500指數"}
        if not symbol and field == "close" and row.get("name") in legacy:
            return legacy[row["name"]]
        return None
    if route == "finmind_exchange_rate":
        currency = {"USD": "美元", "JPY": "日圓", "KRW": "韓元"}.get(row.get("currency"))
        if currency and field in {"spot_buy", "spot_sell"}:
            return currency + _BRIEF_LABELS[field]
        return None
    if route in {"finmind_government_bonds_yield", "finmind_crude_oil_prices", "finmind_gold_price"}:
        fields = {"finmind_government_bonds_yield": {"value", "yield"},
                  "finmind_crude_oil_prices": {"price"}, "finmind_gold_price": {"Price", "price"}}[route]
        if field not in fields:
            return None
        symbol = row.get("symbol") or row.get("instrument") or row.get("indicator")
        if symbol in {"^TNX", "CL=F", "GC=F"}:
            return _GLOBAL_CLOSE_LABELS[symbol] + "（" + symbol + "）"
    elif route in _GLOBAL and route not in {"official_sector_observations", "news_and_hot_topics_raw"}:
        return None
    identity = _context_identity(row, route)
    if "未核對" in identity or "未識別" in identity:
        return None
    label = _BRIEF_LABELS[field]
    return identity + "／" + label if identity else label


def _company_profile(payload):
    stock = payload.get("stock") or {}
    profile = {key: stock.get(key) for key in ("stock_name", "industry") if stock.get(key)}
    context = _report_context(payload)
    candidate = context.get("stock_profile") if context is not None else None
    asof = _day(payload.get("asof"))
    observed = _day(candidate.get("classification_observed_date")) if isinstance(candidate, dict) else None
    market = str(stock.get("market") or "").strip().upper()
    if (context is not None and str(context.get("stock_id")) == str(stock.get("stock_id"))
            and asof and _day(context.get("asof")) == asof and isinstance(context.get("sources"), list)
            and isinstance(candidate, dict) and str(candidate.get("stock_id")) == str(stock.get("stock_id"))
            and market and str(candidate.get("market") or "").strip().upper() == market
            and candidate.get("classification_verified") is True and candidate.get("source_route") == "official_sector_features"
            and observed and observed <= asof):
        for target in ("stock_name", "industry"):
            value = candidate.get(target)
            if not profile.get(target) and isinstance(value, str) and _HAN.search(value):
                profile[target] = value.strip()
    for source in _context_sources(payload):
        if source.get("route_id") != "finmind_stock_info":
            continue
        for row in source.get("records") or []:
            if not isinstance(row, dict) or row.get("stock_id") is not None and str(row["stock_id"]) != str(stock.get("stock_id")):
                continue
            for target, fields in (("stock_name", ("stock_name",)), ("industry", ("industry", "industry_category", "source_industry"))):
                if not profile.get(target):
                    value = next((row[key] for key in fields if isinstance(row.get(key), str) and _HAN.search(row[key])), None)
                    if value:
                        profile[target] = value
    return profile


def _related_to_stock(row, stock):
    code = row.get("stock_id", row.get("data_id", row.get("公司代號")))
    if code is not None:
        return str(code).strip() == str(stock.get("stock_id"))
    text = " ".join(str(row.get(k) or "") for k in (
        "title_zh", "title", "headline", "industry", "industry_category", "sector_label", "stock_name", "company_name"))
    industry = str(stock.get("industry") or "").removesuffix("業")
    return any(value and str(value) in text for value in (stock.get("stock_id"), stock.get("stock_name"), industry))


def _brief_context(payload, sources, group, comparison_coverage=()):
    """Keep latest meaningful observations, not the complete accounting/source ledger."""
    latest, conflicts, notes, raw_values, news = {}, {}, [], set(), {}
    stock, asof = {**(payload.get("stock") or {}), **_company_profile(payload)}, _day(payload.get("asof"))
    for source in sources:
        route = source.get("route_id")
        unmapped = set()
        if source.get("usage") in {"not-selected", "not_selected"}:
            continue
        missing = source.get("availability", source.get("status"))
        if missing in {"missing", "unknown_availability", "unavailable", "partial", "PARTIAL"}:
            notes.append(_route(route) + "：" + _state(missing) + "；不視為完整資料。")
        for row in source.get("records") or []:
            if not isinstance(row, dict):
                continue
            # Compact points carry the original field explicitly; restore only that declared field.
            if row.get("observation_role") in {"latest", "previous"} and isinstance(row.get("field"), str) and "value" in row:
                row = dict(row)
                field = row["field"]
                if route in {"finmind_financial_statements", "finmind_balance_sheet", "finmind_cash_flows_statement"} and row.get("type"):
                    field = "value"
                if field != "value":
                    row[field] = row.pop("value")
                if not row.get("type"):
                    row.pop("type", None)
                elif route not in {"finmind_financial_statements", "finmind_balance_sheet", "finmind_cash_flows_statement"}:
                    row.setdefault("name", row["type"])
                if route not in _GLOBAL | {"official_sector_features", "finmind_industry_chain_flow"} and str(row.get("entity_id")) == str(stock.get("stock_id")):
                    row.pop("entity_id", None)
            if route == "finmind_stock_info":
                continue
            own_code = row.get("stock_id", row.get("data_id"))
            if own_code is not None and str(own_code) != str(stock.get("stock_id")):
                continue
            day = _day(row.get("period_end", row.get("date", row.get("session_date"))))
            if asof and day and day > asof:
                notes.append(_route(route) + "有晚於資料日的觀測，未混入當期摘要。")
                continue
            if route in {"official_sector_features", "official_sector_observations", "finmind_industry_chain_flow",
                         "news_and_hot_topics_raw"} and not _related_to_stock(row, stock):
                continue
            identity = _context_identity(row, route)
            period = day or ""
            sort_period = day or ""
            if row.get("revenue_year") and row.get("revenue_month"):
                try:
                    month = date(int(row["revenue_year"]), int(row["revenue_month"]), 1)
                except (TypeError, ValueError, OverflowError):
                    notes.append("月營收的原始年度或月份無效，未用公告日替代營收月份。")
                    continue
                period = _code(row["revenue_year"]) + " 年 " + _code(row["revenue_month"]) + " 月"
                sort_period = month.strftime("%Y%m")
            elif row.get("period_start") and row.get("period_end"):
                period = (_day(row["period_start"]) or _time(row["period_start"])) + " 至 " + (
                    _day(row["period_end"]) or _time(row["period_end"]))
            if row.get("period_basis") == "year_to_date":
                period += "（年初累計）"
            elif row.get("period_basis") in {"quarter", "single_quarter", "annual", "instant", "unknown"}:
                label = {"quarter": "單季", "single_quarter": "單季", "annual": "年度", "instant": "期末存量", "unknown": "期間口徑未核實"}
                period += "（" + label[row["period_basis"]] + "）"
            basis_name, span = str(row.get("period_basis") or ""), str(row.get("period_span") or "")
            if basis_name == "year_to_date" and not span:
                start = _day(row.get("period_start"))
                span = (start[5:] if start else "") + ":" + (day[5:7] if day else "")
            basis = (basis_name, span)

            def add(key, label, value, covered=False):
                token = (route, identity, key, basis)
                current = (sort_period, period, label, value, covered)
                previous = latest.get(token)
                if previous is None or current[0] > previous[0]:
                    latest[token], conflicts[token] = current, False
                elif current[0] == previous[0] and current[3] != previous[3]:
                    conflicts[token] = True

            if route == "news_and_hot_topics_raw":
                title = row.get("title_zh", row.get("title", row.get("headline")))
                stamp = row.get("published_at", row.get("publication_time"))
                origin = row.get("source") or row.get("url") or row.get("publisher") or source.get("source")
                if not title or not _HAN.search(str(title)) or not _day(stamp) or not origin:
                    notes.append("本股相關新聞缺中文標題、來源或發布日期，未作事件解讀。")
                    continue
                if asof and _day(stamp) > asof:
                    notes.append("本股相關新聞發布日晚於資料日，未混入當期事件。")
                    continue
                period = day or _day(stamp)
                publisher = row.get("publisher") or row.get("source") or source.get("publisher") or source.get("source")
                title = _zh(normalize_news_title(title, publisher), "")
                if not title:
                    continue
                summary = row.get("summary_zh", row.get("summary"))
                summaries = news.setdefault((route, period, title), [])
                if summary and _HAN.search(str(summary)):
                    value = _zh(summary)
                    if value not in summaries:
                        summaries.append(value)
                continue
            if not period:
                notes.append(_route(route) + "缺少資料期間，數值留在原始附件。")
                continue
            topic = row.get("type", row.get("account", row.get("account_name")))
            for key, value in row.items():
                if _num(value) is None:
                    continue
                effective = str(topic) if key == "value" and topic is not None else key
                if group == "fundamentals":
                    allowed = (_BRIEF_ACCOUNTS if topic is not None else
                               {"PER", "per", "PBR", "pbr", "revenue", "revenue_yoy", "revenue_mom",
                                "dividend_yield", "CashEarningsDistribution", "cash_earnings_distribution", *_BRIEF_ACCOUNTS})
                elif group == "flows":
                    allowed = _BRIEF_FLOW
                else:
                    allowed = {"close", "adj_close", "value", "price", "Price", "yield", "exchange_rate", "rate", "spot_buy", "spot_sell"}
                if effective not in allowed or effective not in _BRIEF_LABELS:
                    if key not in {"stock_id", "data_id", "year", "quarter", "revenue_year", "revenue_month", "level", "holding_level", "持股分級", "period_span"}:
                        unmapped.add(effective)
                    continue
                canonical = _BRIEF_ALIASES.get(effective, effective)
                covered = _covered_context_field(comparison_coverage, route, canonical, row, day, value, source)
                unit = "倍" if effective in {"PER", "per", "PBR", "pbr"} else _field_unit(row, source, effective)
                if unit == _unit(None) and key != effective:
                    unit = _field_unit(row, source, key)
                if (canonical != effective and _num(row.get(canonical)) == _num(value)
                        and (unit == "倍" or _field_unit(row, source, canonical) == unit)):
                    continue
                if unit == _unit(None) and row.get("measure_unit"):
                    unit = _unit(row["measure_unit"])
                label = _brief_context_label(row, route, effective)
                if label is None:
                    notes.append(_route(route) + "的標的或欄位名稱無明確對照，保留於原始附件。")
                    continue
                if unit == _unit(None):
                    raw_values.add((route, identity, canonical, basis))
                    notes.append(_route(route) + "／" + label + "的單位未提供，不自行推定。")
                add(canonical, label, _number(value) + ("（" + unit + "）" if unit != _unit(None) else ""), covered)
        if unmapped:
            scope = "財務科目" if group == "fundamentals" else "數值項目"
            notes.append(_route(route) + "另有 " + str(len(unmapped)) + " 個" + scope
                         + "缺少中文或口徑對照，不能納入本章解讀；原值保留於資料附件。")
    for (route, period, title), summaries in news.items():
        token = (route, "", (period, title), ())
        latest[token] = (period, period, "事件", "；".join([title, *summaries]), False)
        conflicts[token] = False
    # Choose the actual latest observation before hiding values already shown in comparisons.
    visible = [(token, item) for token, item in latest.items() if not item[4] or conflicts[token]]
    suppressed = len(latest) - len(visible)
    rows = [[item[1], item[2], "同日數值衝突，未任選；詳見附件" if conflicts[token] else item[3]]
            for token, item in visible]
    tables = [_table("最新重點資料", ["資料期間", "項目", "數值／內容"], rows, [0.20, 0.36, 0.44])] if rows else []
    if tables and any(token in raw_values for token, _ in visible):
        tables[0]["note"] = _RAW_VALUE_NOTE
    if not rows and not suppressed:
        notes.append("未提供可核對且適用本股的重點資料；不補造數值或事件。")
    return tables, list(dict.fromkeys(notes))


def _covered_context_field(coverage, route, field, row, day, value, source):
    period = day
    if route == "tdcc_share_distribution_raw" and field in {"holding_percentage", "percent"}:
        level = row.get("holding_level", row.get("持股分級", row.get("level")))
        field = "holding_percentage|" + str(level)
    if route == "finmind_month_revenue" and row.get("revenue_year") and row.get("revenue_month"):
        try:
            period = f"{int(row['revenue_year']):04}{int(row['revenue_month']):02}"
        except (TypeError, ValueError, OverflowError):
            return False
    aliases = {"single_quarter": "quarter", "期末存量": "instant", "期間口徑未核實": "unknown"}
    default = "instant" if route == "finmind_balance_sheet" else "unknown"
    basis = str(row.get("period_basis") or default)
    basis = aliases.get(basis, basis)
    unit = "倍" if field in {"PER", "PBR"} else _field_unit(row, source, field.split("|", 1)[0])
    if route == "tdcc_share_distribution_raw" and ("占集保庫存數比例%" in row or row.get("field") == "占集保庫存數比例%"):
        unit = _unit("%")
    if unit == _unit(None) and "value" in row:
        unit = _field_unit(row, source, "value")
    unit = None if unit == _unit(None) else unit
    return any(origin == route and key == field and stamp == period
               and (number is None or number == _num(value)) and (span is None or span == basis)
               and supplied_unit == unit for origin, key, stamp, number, span, supplied_unit in coverage)


def _comparison_group(item, row):
    key, route, label = str(item.get("id") or ""), item.get("source_route"), row["項目"]
    if key.startswith("revenue_") or route == "finmind_month_revenue" or label == "月營收":
        return "fundamentals", "營收比較"
    if key.startswith("valuation_") or route == "finmind_per":
        return "fundamentals", "估值比較"
    if key.startswith("institutional_") or route == "finmind_institutional":
        return "flows", "法人買賣比較"
    if key in {"margin_purchase_balance", "short_sale_balance"} or route == "finmind_margin":
        return "flows", "融資融券比較"
    if key.startswith("tdcc_") or route == "tdcc_share_distribution_raw":
        return "flows", "集保持股分級比較"
    if key.startswith("financial_") or route in {"finmind_financial_statements", "finmind_balance_sheet", "finmind_cash_flows_statement"}:
        return "fundamentals", "財務比較"
    return "fundamentals", "其他比較資料"


def _comparison_coverage(item, row):
    key = str(item.get("id") or "")
    matches = re.findall(r"\d{4}-\d{2}-\d{2}|(?<!\d)\d{8}(?!\d)", row["資料期間"])
    day = _day(matches[-1]) if matches else None
    value = _num(item.get("current_value"))
    unit = _unit(item.get("unit"))
    if item.get("unit") in {None, "unknown", "單位未核實"} or unit == _unit(None):
        unit = None

    def entry(route, field, period, number, basis=None):
        return route, field, period, number, basis, unit

    if key in {"valuation_per", "valuation_pbr"} and day and value is not None:
        return [entry("finmind_per", "PER" if key == "valuation_per" else "PBR", day, value)]
    if key == "revenue_monthly" and value is not None:
        month = re.search(r"(?<!\d)\d{6}(?!\d)", row["資料期間"])
        if month:
            return [entry("finmind_month_revenue", "revenue", month[0], value)]
    if key.startswith("financial_") and day and value is not None:
        account = key.removeprefix("financial_")
        matched_basis = None
        for basis in (str(item.get("period_basis") or ""), "single_quarter", "year_to_date", "quarter", "期末存量", "期間口徑未核實"):
            if basis and account.endswith("_" + basis):
                account = account[:-(len(basis) + 1)]
                matched_basis = {"single_quarter": "quarter", "期末存量": "instant", "期間口徑未核實": "unknown"}.get(basis, basis)
                break
        return [entry(item.get("source_route", "finmind_financial_statements"), account, day, value, matched_basis)]
    if key in {"margin_purchase_balance", "short_sale_balance"} and day and value is not None:
        return [entry("finmind_margin", key, day, value)]
    if key.startswith("tdcc_") and day and value is not None:
        return [entry("tdcc_share_distribution_raw", "holding_percentage|" + key.removeprefix("tdcc_"), day, value)]
    if key.startswith("institutional_") and day and "最近單日 " in row["比較"]:
        fields = {"foreign": "foreign_investors_net", "trust": "investment_trust_net", "dealer": "dealer_net"}
        field = next((field for name, field in fields.items() if key.startswith("institutional_" + name + "_")), None)
        if field:
            return [entry("finmind_institutional", field, day, None)]
    return []


def _body_note(value):
    if not isinstance(value, str) or not value.strip():
        return ""
    text = re.sub(r"\b[0-9a-fA-F]{32,64}\b", "", value)
    for code, label in (("PARTIAL", "來源發布範圍未完整"), ("not-selected", "本次未選用"),
                        ("unknown_availability", "可得時間證據不足"), ("unverified_future_calendar", "未來交易日曆未核實")):
        text = text.replace(code, label)
    return _zh(text, "").replace("〔內部代碼見資料檔〕", "").strip("；： ")


def _comparison_views(payload):
    groups, coverage = {}, []
    notes = {"fundamentals": [], "flows": [], "context": []}
    metrics = [item for item in (payload.get("report_comparison") or {}).get("metrics") or [] if isinstance(item, dict)]
    seen = set()
    for index, (item, row) in enumerate(zip(metrics, report_comparison_rows(payload))):
        chapter, title = _comparison_group(item, row)
        label = row["項目"]
        if not _HAN.search(label):
            account = str(item.get("id") or "").removeprefix("financial_")
            for basis in ("single_quarter", "year_to_date", "quarter", "期末存量", "期間口徑未核實"):
                account = account.removesuffix("_" + basis)
            label = _BRIEF_LABELS.get(account, "財務科目" + str(index + 1) if title == "財務比較" else "比較項目" + str(index + 1))
        comparison = row["比較"]
        # Classify this known definition by content, even when attached to a rank metric.
        detail = row["解讀"].replace(
            "期末日不等於公告日；累計不當成單季，當期比較不宣稱回測時已公開。", "").strip("； \n")
        key = str(item.get("id") or "")
        # Keep data-specific rank, missing-month and conflict facts, not repeated definitions.
        if detail and (key == "revenue_same_month" or any(word in detail for word in ("數值衝突", "缺少 ", "缺漏", "不是完整期間"))
                or key not in {"revenue_monthly", "revenue_ytd", "revenue_3m", "revenue_12m"}
                and re.search(r"\d", detail)) and detail not in comparison:
            comparison += "；" + detail
        visible = [label, row["資料期間"], row["本期值"], comparison, row["資料日期"]]
        signature = (chapter, title, *visible)
        if signature not in seen:
            groups.setdefault((chapter, title), []).append(visible)
            seen.add(signature)
        coverage.extend(_comparison_coverage(item, row))
    order = ("營收比較", "財務比較", "估值比較", "其他比較資料", "法人買賣比較", "融資融券比較", "集保持股分級比較")
    tables = {key: [] for key in notes}
    for (chapter, title), rows in sorted(groups.items(), key=lambda pair: order.index(pair[0][1])):
        tables[chapter].append(_table(title, ["項目", "資料期間", "本期值", "比較", "資料日期"], rows,
                                      [0.16, 0.16, 0.18, 0.35, 0.15]))
    for value in (payload.get("report_comparison") or {}).get("notes") or []:
        if not isinstance(value, str) or "來源狀態 " in value or value.startswith("當期基本面比較不代表回測時已公開"):
            continue
        if value.startswith("比較另行讀取中央來源快照"):
            value = "歷史比較使用另一來源快照，不能視為與預測同一批資料。"
        if "比較不可得：" in value:
            prefix, reason = value.split("比較不可得：", 1)
            if not _HAN.search(reason):
                value = prefix + "比較不可得：本次未能完成來源讀取或核對。"
        chapter = "flows" if value.startswith(("法人", "融資融券", "集保持股")) else "fundamentals"
        if text := _body_note(value):
            notes[chapter].append(text)
    return tables, coverage, notes


def _context_body_notes(notes):
    result = []
    for note in notes:
        if ("單位未提供，不自行推定" in note or "：資料可得性未知；不視為完整資料" in note
                or "：部分資料，限制保留；不視為完整資料" in note or "：部分完成；不視為完整資料" in note
                or "：資料缺失；不視為完整資料" in note or "：本次用途不可用；不視為完整資料" in note
                or re.search(r"另有 \d+ 個(?:財務科目|數值項目)缺少中文或口徑對照", note)
                or note.endswith("的標的或欄位名稱無明確對照，保留於原始附件。")
                or note.startswith("未提供可核對且適用本股的重點資料")):
            continue
        if text := _body_note(note):
            result.append(text)
    return result


def _report_insights(payload):
    keys = ("summary", "technical", "fundamentals", "flows", "context", "data_notes")
    if _report_context(payload) is None:
        return {key: [] for key in keys}
    result = build_report_insights(payload)
    if not isinstance(result, dict) or any(not isinstance(result.get(key), list)
            or any(not isinstance(value, str) for value in result[key]) for key in keys):
        raise CandidateReportError("report_insights_invalid", "中文解讀未提供約定的章節內容，停止產出。")
    return {key: list(dict.fromkeys(text for value in result[key] if (text := _body_note(value)))) for key in keys}


def _without_summary_disclosures(note, *, calibration, calendar):
    heading, separator, body = note.partition("：")
    prefix = heading + separator if separator and len(heading) <= 24 else ""
    if not prefix:
        body = note
    sentences = []
    for sentence in re.split(r"[。；]", body):
        parts = []
        for value in sentence.split("，"):
            value = value.strip()
            if calibration and (re.fullmatch(r"(?:目前)?(?:上漲)?機率(?:尚未|未經|未)校準", value)
                    or value in {"不能直接視為實際命中率", "不能視為實際命中率"}):
                continue
            if calendar and (re.fullmatch(r"(?:目標交易日|目標日期|未來交易日曆)(?:尚未驗證|未驗證|未核實|尚未確認|未知)", value)
                    or value in {"未以平日推算日期", "不以平日推算", "無法確認日期", "無法列出目標日期"}):
                continue
            if value:
                parts.append(value)
        if parts:
            sentences.append("，".join(parts))
    return prefix + "；".join(sentences) + "。" if sentences else ""


def _display_data_notes(payload, notes, *, raw_units, comparisons, calibration, calendar):
    modern = _report_context(payload) is not None
    result = [text for note in notes if (text := _without_summary_disclosures(
        note, calibration=calibration, calendar=calendar))] if modern else list(notes)
    fallback = []
    if raw_units and not (modern and any("單位" in note or "原始值" in note for note in result)):
        fallback.append(_RAW_VALUE_NOTE)
    if comparisons and not (modern and any(
            "期間" in note and "口徑" in note or "累計" in note and "單季" in note or "期末" in note and "公告" in note
            for note in result)):
        fallback.append(_PERIOD_VALUE_NOTE)
    if fallback and modern:
        extra = "；".join(note.removeprefix("期間口徑：").rstrip("。") for note in fallback)
        index = next((i for i, note in enumerate(result) if "：" in note
                      and any(word in note.split("：", 1)[0] for word in ("單位", "口徑"))), None)
        if index is None:
            result.append("單位與比較口徑：" + extra + "。")
        else:
            result[index] = result[index].rstrip("。") + "；" + extra + "。"
    else:
        result.extend(fallback)
    history_covered = modern and any("回測" in note and any(
        word in note for word in ("未核實", "未驗證", "未確認", "未知", "偏樂觀")) for note in result)
    if _historical_timing_unconfirmed(payload) and not history_covered:
        index = next((i for i, note in enumerate(result) if "：" in note
                      and any(word in note.split("：", 1)[0] for word in ("時點", "回測"))), None) if modern else None
        if index is None:
            result.append(_HISTORICAL_TIMING_RISK)
        else:
            result[index] = result[index].rstrip("。") + "；" + _HISTORICAL_TIMING_RISK
    return list(dict.fromkeys(result))


def _selected_metric_tables(payload):
    metrics, prediction = payload.get("metrics") or {}, payload.get("prediction") or {}
    selected = (metrics.get("selection") or {}).get("selected") or {}
    market = (payload.get("stock") or {}).get("market")
    rows, notes = [], []
    final = metrics.get("finaltest", metrics.get("holdout")) or {}
    baseline = (metrics.get("baselines") or {}).get("finaltest", (metrics.get("baselines") or {}).get("test")) or {}
    paired = (metrics.get("paired_differences") or {}).get("finaltest", (metrics.get("paired_differences") or {}).get("test")) or {}

    def merge(left, right):
        result = dict(left)
        conflict = False
        for key, value in right.items():
            old = result.get(key)
            if isinstance(old, dict) and isinstance(value, dict):
                result[key], different = merge(old, value)
                conflict |= different
            elif old is not None and value is not None and old != value:
                conflict = True
            elif old is None:
                result[key] = value
        return result, conflict

    for task in ("direction", "return"):
        kind = prediction.get("selected_" + task + "_kind")
        selection_mismatch = selected.get(task) and selected[task] != kind
        if selection_mismatch:
            notes.append(_TASK[task] + "的推論模型與選模紀錄不一致，不能將兩者績效混用。")
        candidate = (metrics.get("comparison") or {}).get(kind) or {}
        if candidate.get("status") not in {None, "trained", "complete", "evaluated"}:
            notes.append(_TASK[task] + "的模型紀錄狀態：" + _state(candidate["status"]) + "。")
        stored = candidate.get("finaltest", candidate.get("test")) or {}
        first, second = stored.get(task) or {}, (final.get(kind) or {}).get(task) or {}
        combined, conflict = merge(first, second)
        variants = [("候選紀錄", first), ("彙整紀錄", second)] if conflict else [("最終保留測試", combined)]
        if conflict:
            notes.append(_TASK[task] + "的最終保留測試紀錄不一致，兩份並列，不擇優採用。")
        for label, stats in variants:
            if not stats:
                notes.append(_TASK[task] + "未提供選中模型的最終保留測試結果，無法確認準確度。")
                continue
            task_paired = {} if selection_mismatch else paired.get(task) or {}
            scopes = [("全體樣本", stats, baseline.get(task) or {}, task_paired)]
            market_stats = (stats.get("by_market") or {}).get(market)
            if isinstance(market_stats, dict):
                scopes.append(({"TWSE": "上市樣本", "TPEX": "上櫃樣本"}.get(market, "本股市場樣本"), market_stats,
                               ((baseline.get(task) or {}).get("by_market") or {}).get(market) or {},
                               (task_paired.get("by_market") or {}).get(market) or {}))
            for scope, current, base, difference in scopes:
                key = "accuracy" if task == "direction" else "mae"
                value, base_value = _num(current.get(key)), _num(base.get(key))
                if task == "direction":
                    value = value if value is not None else _num(current.get("holdout_accuracy"))
                    base_value = base_value if base_value is not None else _num(base.get("holdout_accuracy"))

                def score(number):
                    if number is None:
                        return "未提供"
                    return (_number(number, True).removesuffix("%") + " 個百分點" if task == "return"
                            else _number(number, True))

                def count_pair(first_key, second_key):
                    return "／".join(_number(current[name]) if _num(current.get(name)) is not None else "未提供"
                                    for name in (first_key, second_key))

                comparison = []
                if value is not None and base_value is not None:
                    if task == "direction":
                        comparison.append("準確率" + ("低於基準" if value < base_value else "高於基準" if value > base_value else "與基準相同"))
                    else:
                        comparison.append("MAE " + ("劣於基準" if value > base_value else "低於基準" if value < base_value else "與基準相同"))
                delta = _num(difference.get("model_minus_baseline"))
                metric = difference.get("metric")
                worse = difference.get("worse_than_baseline") is True or delta is not None and (
                    delta > 0 if metric in {"mae", "brier", "rmse", "log_loss"} else delta < 0 if metric == "accuracy" else False)
                if worse and not any("劣於基準" in text or "準確率低於基準" in text for text in comparison):
                    comparison.append("機率誤差劣於基準" if metric in {"brier", "log_loss"} else "模型劣於基準")
                if current.get("holdout_status") not in {None, "evaluated"}:
                    comparison.append(_state(current["holdout_status"]))
                scope_name = "全體" if scope == "全體樣本" else "本股市場（" + str(market) + "）"
                indicator = "方向準確率" if task == "direction" else "報酬 MAE"
                rows.append([scope_name + "／" + indicator + ("\n" + label if conflict else ""), score(value), score(base_value),
                             count_pair("n_evaluated", "n_denominator"), count_pair("n_failed", "n_unscored"),
                             "；".join(comparison) or "僅列已提供結果"])
    tables = [_table("選中模型最終測試", ["樣本／指標", "模型", "基準", "可評分／原始樣本", "失敗／未評分", "基準對照"],
                     rows, [0.23, 0.14, 0.14, 0.17, 0.14, 0.18])] if rows else []
    return tables, list(dict.fromkeys(notes))


def _brief_reminders(payload, prediction_notes):
    reminders = []
    failures = [note for note in prediction_notes if note.startswith(("方向分支", "報酬分支"))]
    prediction = payload.get("prediction") or {}
    calibration = prediction.get("calibration_status", (payload.get("metrics") or {}).get("calibration_status"))
    if failures:
        reminders.append("估計限制：" + "；".join(dict.fromkeys(failures)) + "。")
    elif not calibration or str(calibration).startswith("uncalibrated"):
        reminders.append("機率尚未校準，不能直接視為實際命中率。")
    limitations = [str(value) for value in [*(payload.get("limitations") or []), *((payload.get("metrics") or {}).get("limitations") or [])]]
    specific = [value for value in limitations if any(key in value for key in ("本股", "本公司"))
                and any(key in value for key in ("停牌", "處置", "下市", "減資"))
                and not any(key in value for key in ("未確認", "未驗證", "缺少", "未知"))]
    if specific:
        reminders.append("本股提醒：" + "；".join(dict.fromkeys(_zh(value) for value in specific)))
    return reminders


_HISTORICAL_TIMING_RISK = "部分歷史資料的公告時點未能確認，回測表現可能偏樂觀。"


def _historical_timing_unconfirmed(payload):
    sources = [source for source in [*(payload.get("source_manifest") or []), *_context_sources(payload)] if isinstance(source, dict)]
    for source in sources:
        if (source.get("historical_PIT_unverified") is True or source.get("historical_availability_unverified") is True
                or source.get("historical_availability_verified") is False):
            return True
        basis = source.get("available_time_basis", source.get("availability_basis"))
        if source.get("publication_time_verified") is False and "lag" in str(basis).lower():
            return True
    notes = [*(payload.get("limitations") or []), *((payload.get("metrics") or {}).get("limitations") or []),
             *(source.get("reason_zh") for source in sources)]
    return any(isinstance(note, str) and "歷史" in note and any(word in note for word in ("公告", "公開", "可得", "時點"))
               and any(word in note for word in ("未確認", "未驗證", "未證實", "缺少", "缺乏", "未知", "未能確認", "未核實"))
               for note in notes)


_MACHINE_INTERPRETATION_NOTES = {
    "technical": "均線按已提供的有效收盤觀測計算，未另核實缺交易日；不補缺日價格或推算未知換手率。",
    "fundamentals": "財報期末日不等於公告日，年初累計不當成單季。未知金額單位不猜測，不由估計報酬反推合理價。",
    "flows": "同日同單位的分母未核實時不計算占比；歷史公告時點未驗證，不宣稱已通過歷史時點檢查。",
    "context": "背景與本股事件不等於選中模型已使用，不據此宣稱股價因果。",
    "evaluation": "封存最終保留樣本不是本股未來準確率，不用最終測試重新選模；訊號日數不等於獨立樣本數。",
}


def _report_machine_evidence(payload):
    sources = _context_sources(payload)
    context_routes = _GLOBAL | {"official_sector_features", "finmind_industry_chain_flow"}
    groups = {"fundamentals": [s for s in sources if s.get("route_id") in _FUND - context_routes],
              "flows": [s for s in sources if s.get("route_id") not in _FUND | context_routes | _TECH],
              "context": [s for s in sources if s.get("route_id") in context_routes]}
    prediction = payload.get("prediction") or {}
    return {"schema": "single-stock-report-machine-evidence-v1",
            "interpretation_notes": dict(_MACHINE_INTERPRETATION_NOTES),
            "prediction_reason_zh": prediction.get("reason_zh"),
            "prediction_status": prediction.get("prediction_status", prediction.get("status")),
            "deployment_status": _deployment_status(payload),
            "historical_timing_unconfirmed": _historical_timing_unconfirmed(payload),
            "technical_diagnostics": _price_view(payload).get("notes", []),
            "technical_units": {item["name"]: item.get("unit") for item in payload.get("feature_values") or []
                                if isinstance(item, dict) and item.get("name") in {"return_5", "return_20", "return_60", "volume_ratio_20"}},
            "context_diagnostics": {key: _brief_context(payload, items, key)[1] for key, items in groups.items()},
            "evaluation_diagnostics": _selected_metric_tables(payload)[1]}


def _prediction_blocker(prediction):
    reason = prediction.get("reason_zh")
    if not isinstance(reason, str) or not _HAN.search(reason):
        return None
    boilerplate = ("候選研究", "候選模型研究估計", "未正式採用", "尚未正式採用", "可比較有效估計", "排序")
    clauses = [text.strip() for text in re.split(r"[。！？；，\n]", reason) if text.strip()]
    relevant = [text for text in clauses if not any(term in text for term in boilerplate)]
    return "；".join(_zh(text) for text in relevant) or None


def _technical_summary(payload, view):
    rows, sentences = [], []
    features, conflicts = {}, set()
    names = {"return_5", "return_20", "return_60", "volume_ratio_20"}
    stock_id = str((payload.get("stock") or {}).get("stock_id"))
    asof = _day(payload.get("asof"))
    for item in payload.get("feature_values") or []:
        if not isinstance(item, dict) or item.get("name") not in names or _num(item.get("value")) is None:
            continue
        if item.get("stock_id") is not None and str(item["stock_id"]) != stock_id:
            continue
        item_day = _day(item.get("asof", item.get("date")))
        if item_day and item_day != asof:
            continue
        name, value = item["name"], (_num(item["value"]), item.get("unit"))
        if name in features and features[name] != value:
            conflicts.add(name)
        features[name] = value
    for name in conflicts:
        features.pop(name, None)
    returns = []
    for n in (5, 20, 60):
        if "return_" + str(n) not in features:
            continue
        value, unit = features["return_" + str(n)]
        if unit in {"ratio", "fraction", "比例"}:
            display = _number(value, True)
        elif unit in {"percent", "%", "百分比"}:
            display = _number(value) + "%"
        else:
            display = _number(value)
        rows.append(["近 " + str(n) + " 日報酬", display])
        returns.append("近 " + str(n) + " 日報酬 " + display)
    if returns:
        sentences.append("、".join(returns))
    if view["dates"]:
        rows.append(["圖表期間", view["dates"][0] + " 至 " + view["dates"][-1]])
        close = view["close"][-1]
        if close is not None:
            rows.append([view["price_label"], _number(close)])
        if view["volume"][-1] is not None:
            rows.append([view["volume_label"], _number(view["volume"][-1])])
        relations = {"高於": [], "低於": [], "等於": []}
        for n in (5, 20, 60):
            average = view["averages"][str(n)][-1]
            if average is not None:
                rows.append([str(n) + " 日均線", _number(average)])
                if close is not None:
                    relations["高於" if close > average else "低於" if close < average else "等於"].append(str(n))
        relative = [relation + " " + "／".join(periods) + " 日均線" for relation, periods in relations.items() if periods]
        if relative:
            sentences.append(view["dates"][-1] + " 收盤" + "、".join(relative))
    if "volume_ratio_20" in features:
        value, unit = features["volume_ratio_20"]
        if value >= 0:
            if unit in {"times", "倍", "ratio", "fraction", "比例"}:
                display = f"{value:,.2f} 倍"
                sentences.append("20 日量比 " + display + "（" + ("高於" if value > 1 else "低於" if value < 1 else "等於") + " 1 倍）")
            else:
                display = _number(value)
            rows.append(["20 日量比", display])
    return rows, ["；".join(sentences) + "。"] if sentences else []


def _technical_body_notes(notes, insights):
    if not insights:
        return list(notes)
    supplied = [re.sub(r"\s+", "", text) for text in insights]
    number = r"[-+]?\d[\d,.]*%?"
    returns = r"近(?:5|20|60)日報酬" + number
    relation = r"(?:高於|低於|等於)[\d／]+日均線"

    def covered(clause):
        text = re.sub(r"\s+", "", clause)
        if re.fullmatch(returns + r"(?:、" + returns + r")*", text):
            periods = re.findall(r"近(5|20|60)日報酬", text)
            return any("報酬" in item and all(period + "日" in item for period in periods) for item in supplied)
        if re.fullmatch(r"\d{4}-\d{2}-\d{2}收盤" + relation + r"(?:、" + relation + r")*", text):
            periods = re.findall(r"(?:高於|低於|等於)([\d／]+)日均線", text)
            return any("收盤" in item and all(any(period + label in item for label in ("日均線", "日均價", "筆均價", "筆均線"))
                       for group in periods for period in group.split("／")) for item in supplied)
        if re.fullmatch(r"20日量比\d[\d,.]*倍（(?:高於|低於|等於)1倍）", text):
            return any("20日量比" in item for item in supplied)
        return False

    return [note for note in notes if not all(covered(clause) for clause in note.rstrip("。").split("；"))]


def _preopen_percent(value, signed=False):
    number = _num(value)
    return _MISSING if number is None else f"{number * 100:{'+' if signed else ''},.2f}%"


def _preopen_h1_view(payload):
    """(段落, 主要表格, 原預測表標題, 揭露表格)；無開盤前版區塊時與原版輸出完全相同。"""
    block = payload.get("preopen_h1") if payload.get("horizon_sessions") == 1 else None
    if not isinstance(block, dict):
        return [], [], "本股預測", []
    label = str(block.get("fallback_label") or "C0 基準")
    if block.get("status") != "used":
        notice = "【注意】" + str(block.get("notice")) + "。"
        if block.get("status") == "fallback":
            notice += "原因：" + str(block.get("reason_zh") or "開盤前版不可用") + "。"
        return [notice], [], "本股預測（" + label + "）", []
    rank, universe = block.get("rank"), block.get("universe_rows")
    rank_text = (f"第 {rank} 名／共 {universe} 檔" if type(rank) is int and type(universe) is int else _MISSING)
    tradable_rank, tradable_rows = block.get("rank_tradable"), block.get("tradable_rows")
    if block.get("tradable") is True and type(tradable_rank) is int:
        tradable_text = f"第 {tradable_rank} 名／共 {tradable_rows} 檔" + ("（可交易 Top10）" if tradable_rank <= 10 else "")
    else:
        tradable_text = "不在可交易母體（20 日均成交金額未達 2,000 萬元或收盤低於 10 元）"
    rows = [["上漲機率（校準後）", _preopen_percent(block.get("p_up"))],
            ["預估收盤報酬（校準後）", _preopen_percent(block.get("exp_ret"), signed=True)],
            ["全市場名次", rank_text],
            ["可交易母體名次", tradable_text],
            ["前一晚美股", str(block.get("gate_us_up_zh") or "前一晚美股漲跌未知") + "；"
             + str(block.get("us_overnight_status_zh") or "美股狀態未知")],
            ["訊號日至目標日", str(_day(block.get("signal_date")) or "未提供") + " 收盤至 "
             + str(_day(block.get("target_date")) or "未提供") + " 收盤"],
            ["模型說明", str(block.get("describe") or block.get("model_label") or "開盤前版")]]
    if block.get("fixture") is True:
        rows.append(["資料性質", "測試用假快照，不可作為正式資訊"])
    if block.get("target_date_consistent") is False:
        rows.append(["日期提醒", "開盤前版目標日與本報告日曆推得的目標交易日不同，請以各自來源核對"])
    paragraphs = ["H1 主要預測來源：開盤前版（HGB，含前一晚美股）。「備援對照」表為" + label + "，只供對照，不作為 H1 主要顯示。"]
    disclosure = [[str(name), str(text)] for name, text in block.get("disclosure_rows") or []
                  if isinstance(name, str) and isinstance(text, str)]
    return (paragraphs, [_table("H1 開盤前版預測（主要顯示）", ["項目", "內容"], rows)], "備援對照：" + label,
            [_table("開盤前版必要揭露", ["項目", "內容"], disclosure)] if disclosure else [])


def build_report_sections(payload: dict) -> list[dict]:
    """Build six concise chapters; complete input evidence remains in the sidecar."""
    _check_payload(payload)
    insights = _report_insights(payload)
    stock, pred = payload.get("stock") or {}, payload.get("prediction") or {}
    model, metrics = payload.get("model_binding") or {}, payload.get("metrics") or {}
    horizon = payload.get("horizon_sessions")
    duration = str(horizon) if type(horizon) is int and horizon > 0 else "未提供"
    market = {"TWSE": "上市市場", "TPEX": "上櫃市場"}.get(str(stock.get("market")).upper(), "市場未提供")
    model_complete = all(model.get(k) for k in ("path", "schema", "feature_schema")) and bool(_SHA.fullmatch(str(model.get("sha256", ""))))
    prediction_notes = []

    def estimate(field):
        task = "direction" if field == "prob_up" else "return"
        kind = pred.get("selected_" + task + "_kind")
        if not model_complete or not kind or "baseline" in str(kind) or kind in {"fit_up_rate", "zero_return"}:
            prediction_notes.append(_TASK[task] + "模型綁定或分支未確認，或僅為比較基準")
            return None
        value = _num(pred.get(field))
        if pred.get("prediction_status", pred.get("status")) in {"no_estimate", "not_predicted"}:
            value = None
        if field == "prob_up" and value is not None and not 0 <= value <= 1:
            value = None
        if value is None:
            prediction_notes.append(_TASK[task] + "沒有有效估計，不填零")
        return _number(value, True) if value is not None else None

    profile = _company_profile(payload)
    summary_rows = [["股票", _code(stock.get("stock_id")) + " " + _zh(profile.get("stock_name"), "股票名稱未提供")],
                    ["市場與產業", market + ("／" + _zh(profile["industry"]) if profile.get("industry") else "")]]
    asof = _day(payload.get("asof"))
    if asof:
        summary_rows.append(["資料日", asof])
    else:
        prediction_notes.append("資料日未提供")
    target = _day(payload.get("target_session_date", pred.get("target_session_date")))
    summary_rows.append(["預測目標", "訊號日收盤至同市場第 " + duration + " 個交易日收盤"])
    if target:
        summary_rows.append(["目標交易日收盤", target])
    else:
        summary_rows.append(["目標交易日收盤", "未來交易日曆未核實，無法確認日期；不以平日推算。"])
    for key, title in (("prob_up", "上漲機率"), ("expected_return", "預估報酬")):
        value = estimate(key)
        if value is not None:
            summary_rows.append([title, value])
    calibration = pred.get("calibration_status", metrics.get("calibration_status"))
    if calibration:
        summary_rows.append(["機率校準", _state(calibration)])
    for task in ("direction", "return"):
        if pred.get("selected_" + task + "_kind"):
            summary_rows.append([_TASK[task] + "模型", _kind(pred["selected_" + task + "_kind"])])
    if not calibration or str(calibration).startswith("uncalibrated"):
        prediction_notes.append("機率未校準，不視為可靠頻率")
    if pred.get("prediction_status", pred.get("status")) in {"no_estimate", "not_predicted"}:
        blocker = _prediction_blocker(pred)
        if blocker:
            summary_rows.append(["無法估計原因", blocker])
    if payload["schema"] != FORMAL_SCHEMA:
        boundary = "本報告為候選研究，尚未正式採用。"
    elif _deployment_status(payload) == "preactivation_audit":
        boundary = "本報告為選定模型的啟用前驗收，尚未啟用。"
    else:
        boundary = "本報告使用已啟用模型。"
    if any(note.startswith(("方向分支", "報酬分支")) for note in prediction_notes):
        insights["summary"] = []
    preopen_notes, preopen_tables, summary_title, preopen_disclosure = _preopen_h1_view(payload)
    sections = [_section("summary", "一、預測摘要", [*preopen_notes, *insights["summary"], boundary + "機率與報酬皆有誤差。",
                         "預測為第 " + duration + " 個交易日的收盤對收盤報酬，非實際成交報酬。",
                         *_brief_reminders(payload, prediction_notes)],
                         [*preopen_tables, _table(summary_title, ["項目", "內容"], summary_rows), *preopen_disclosure])]
    view = _price_view(payload)
    technical, technical_notes = _technical_summary(payload, view)
    technical_notes = _technical_body_notes(technical_notes, insights["technical"])
    technical_tables = [_table("歷史價格摘要", ["項目", "內容"], technical)] if technical else []
    raw_features = any(isinstance(item, dict) and item.get("name") in {"return_5", "return_20", "return_60", "volume_ratio_20"}
                       and _num(item.get("value")) is not None
                       and item.get("unit") not in {"ratio", "fraction", "比例", "percent", "%", "百分比", "times", "倍"}
                       for item in payload.get("feature_values") or [])
    raw_units = bool(technical_tables and (view.get("volume_unit") == _unit(None) or raw_features))
    sections.append(_section("technical", "二、價量與 5／20／60 日均線", [*insights["technical"], *technical_notes], technical_tables, view))
    sources = _context_sources(payload)
    context_routes = _GLOBAL | {"official_sector_features", "finmind_industry_chain_flow"}
    comparisons, comparison_coverage, comparison_notes = _comparison_views(payload)
    for key, title, selected_sources in (
        ("fundamentals", "三、營收、財務與估值", [s for s in sources if s.get("route_id") in _FUND - context_routes]),
        ("flows", "四、法人、融資融券、當沖與集保", [s for s in sources if s.get("route_id") not in _FUND | context_routes | _TECH]),
        ("context", "五、產業、海外與重要事件", [s for s in sources if s.get("route_id") in context_routes]),
    ):
        tables, diagnostics = _brief_context(payload, selected_sources, key, comparison_coverage)
        for table in tables:
            if table.get("note") == _RAW_VALUE_NOTE:
                table.pop("note")
                raw_units = True
        paragraphs = [*insights[key], *comparison_notes[key], *_context_body_notes(diagnostics)]
        sections.append(_section(key, title, paragraphs=paragraphs, tables=[*comparisons[key], *tables]))
    calibration_disclosed = any("未校準" in text for text in [
        *sections[0]["paragraphs"], *(str(row[1]) for row in summary_rows)])
    data_notes = _display_data_notes(payload, insights["data_notes"], raw_units=raw_units,
        comparisons=any(comparisons.values()), calibration=calibration_disclosed, calendar=not target)
    tables, notes = _selected_metric_tables(payload)
    split = (metrics.get("splits") or {}).get("finaltest", (metrics.get("splits") or {}).get("test")) or {}
    start, end = _day(split.get("start")), _day(split.get("end"))
    period_note = ["最終測試訊號期間：" + start + " 至 " + end + "。"] if start and end else []
    sections.append(_section("evaluation", "六、選中模型的最終測試表現", [
        *data_notes,
        *period_note,
        "報酬 MAE 為平均絕對誤差，以百分點表示，越低越好。",
        *notes], tables))
    # Every visible string, including chart legends and table cells, crosses this boundary.
    def convert(value):
        if isinstance(value, str):
            return _tc(value)
        if isinstance(value, list):
            return [convert(v) for v in value]
        if isinstance(value, dict):
            return {k: v if k == "id" else convert(v) for k, v in value.items()}
        return value
    converted = convert(sections)
    seen = set()
    for section in converted:
        paragraphs = []
        for text in section["paragraphs"]:
            if text and text not in seen:
                paragraphs.append(text)
                seen.add(text)
        section["paragraphs"] = paragraphs
    return converted


def report_preview_html(payload, sections=None):
    """Pure preview API using the exact same sections as PDF and JSON."""
    sections = build_report_sections(payload) if sections is None else sections
    escape = lambda value: html.escape(str(value), quote=True)
    parts = ['<!doctype html><html lang="zh-Hant"><meta charset="utf-8"><title>單股分析報告</title>',
             '<style>body{font-family:"Microsoft JhengHei",sans-serif;margin:24px;line-height:1.6}'
             'table{border-collapse:collapse;width:100%}td,th{border:1px solid #ccd;padding:7px;text-align:left}</style>']
    for section in sections:
        parts.append('<h2>' + escape(section.get("title", "")) + '</h2>')
        parts.extend('<p>' + escape(text) + '</p>' for text in section.get("paragraphs", []))
        for table in section.get("tables", []):
            parts.append('<h3>' + escape(table.get("title", "")) + '</h3><table><tr>' +
                         ''.join('<th>' + escape(value) + '</th>' for value in table.get("columns", [])) + '</tr>')
            parts.extend('<tr>' + ''.join('<td>' + escape(value) + '</td>' for value in row) + '</tr>'
                         for row in table.get("rows", []))
            parts.append('</table>')
    return ''.join(parts) + '</html>'


def _visible_strings(sections):
    for section in sections:
        yield section["title"]
        yield from section["paragraphs"]
        for table in section["tables"]:
            if table["title"]:
                yield table["title"]
            if table.get("note"):
                yield table["note"]
            yield from table["columns"]
            for row in table["rows"]:
                yield from row
        if section.get("chart"):
            chart = section["chart"]
            yield chart.get("price_label", "")
            yield chart.get("volume_label", "")
    yield "個股分析報告 第 0123456789 頁 收盤價 五日均線 二十日均線 六十日均線 日期 有效價格未提供"
    yield "0123456789.,%/-：至"


class _Fonts:
    def __init__(self, faces):
        self.faces = faces
        self.primary = faces[0][0]

    def font_for(self, char, bold=False):
        preferred = sorted(self.faces, key=lambda face: face[2] != bold)
        for name, font, _ in preferred:
            if font.face.charToGlyph.get(ord(char), 0):
                return name
        return None

    def check(self, texts):
        missing = sorted({ord(c) for text in texts for c in text if c != "\n" and self.font_for(c) is None})
        if missing:
            raise CandidateReportError("unsupported_glyph", "可見內容含字型不支援字元，停止產出，避免方框或亂碼。",
                                       {"codepoints": [f"U+{c:04X}" for c in missing]})

    def runs(self, text, bold=False):
        name, buffer = None, ""
        for char in str(text):
            current = self.font_for(char, bold) if char != "\n" else self.primary
            if current is None:
                self.check([char])
            if current != name and buffer:
                yield name, buffer
                buffer = ""
            name, buffer = current, buffer + char
        if buffer:
            yield name, buffer


@lru_cache(maxsize=1)
def _load_fonts():
    try:
        from reportlab.pdfbase import pdfmetrics
        from reportlab.pdfbase.ttfonts import TTFont
    except ImportError as exc:
        raise CandidateReportError("reportlab_unavailable", "缺少既有 PDF 排版套件，停止產出；本工具不安裝套件。") from exc
    root = Path(os.environ.get("WINDIR", r"C:\Windows")) / "Fonts"
    faces = []
    for index, (file, bold) in enumerate((("msjh.ttc", False), ("msjhbd.ttc", True), ("mingliu.ttc", False))):
        path = root / file
        if not path.is_file():
            continue
        try:
            font = TTFont("CandidateTC" + str(index), str(path), subfontIndex=0)
            pdfmetrics.registerFont(font)
            faces.append((font.fontName, font, bold))
        except Exception:
            continue
    if not faces:
        raise CandidateReportError("cjk_font_unavailable", "找不到可嵌入且可解析的 Windows 中文字型，停止產出。")
    return _Fonts(faces)


def _markup(text, fonts, bold=False):
    return "".join('<font name="' + name + '">' + html.escape(value).replace("\n", "<br/>") + "</font>"
                   for name, value in fonts.runs(text, bold))


def _chart(view, fonts):
    from reportlab.graphics.shapes import Circle, Drawing, Line, PolyLine, Rect, String
    from reportlab.lib.colors import HexColor
    from reportlab.pdfbase import pdfmetrics
    drawing = Drawing(499, 282)
    left, right, bottom, top = 53, 490, 100, 233
    def label(x, y, text, size=8, anchor="start"):
        runs = list(fonts.runs(_tc(text)))
        width = sum(pdfmetrics.stringWidth(t, n, size) for n, t in runs)
        x -= width if anchor == "end" else width / 2 if anchor == "middle" else 0
        for name, text in runs:
            drawing.add(String(x, y, text, fontName=name, fontSize=size, fillColor=HexColor("#334155")))
            x += pdfmetrics.stringWidth(text, name, size)
    label(left, 269, view.get("price_label", "收盤價"), 9)
    palette = {"close": "#2365A2", "5": "#B24C31", "20": "#16725D", "60": "#77538C"}
    for i, (key, title) in enumerate((("close", "收盤價"), ("5", "五日均線"), ("20", "二十日均線"), ("60", "六十日均線"))):
        x = left + i * 108
        drawing.add(Line(x, 250, x + 14, 250, strokeColor=HexColor(palette[key])))
        label(x + 18, 247, title)
    series = {"close": view["close"], **view["averages"]}
    numbers = [v for values in series.values() for v in values if v is not None]
    if not numbers:
        label(250, 150, "有效價格未提供", anchor="middle")
        return drawing
    low, high = min(numbers), max(numbers)
    pad = max((high - low) * 0.06, high * 0.01, 0.1)
    low, high = low - pad, high + pad
    count = len(view["dates"])
    x_at = lambda i: left + (right - left) * i / max(count - 1, 1)
    y_at = lambda v: bottom + (top - bottom) * (v - low) / (high - low)
    for step in range(5):
        value = low + (high - low) * step / 4
        y = y_at(value)
        drawing.add(Line(left, y, right, y, strokeColor=HexColor("#DFE5EB"), strokeWidth=0.5))
        label(left - 6, y - 3, f"{value:,.1f}", anchor="end")
    for key, values in series.items():
        points = []
        for i, value in enumerate([*values, None]):
            if value is not None:
                points.extend((x_at(i), y_at(value)))
                continue
            if len(points) > 2:
                drawing.add(PolyLine(points, strokeColor=HexColor(palette[key]), strokeWidth=1.2, fillColor=None))
            elif points:
                drawing.add(Circle(*points, 1.5, fillColor=HexColor(palette[key]), strokeColor=None))
            points = []
    for i in sorted({0, (count - 1) // 3, 2 * (count - 1) // 3, count - 1}):
        label(x_at(i), 86, view["dates"][i], 7, "middle")
    label(left, 68, view["volume_label"])
    maximum = max((v for v in view["volume"] if v is not None), default=0)
    if maximum > 0:
        for i, value in enumerate(view["volume"]):
            if value is not None:
                drawing.add(Rect(x_at(i) - 0.3, 22, max(0.6, (right - left) / max(count, 1) * 0.7),
                                 36 * value / maximum, fillColor=HexColor("#82969C"), strokeColor=None))
    drawing.add(Line(left, 22, right, 22, strokeColor=HexColor("#BAC7CE")))
    return drawing


def _render_pdf(path, payload, sections, fonts):
    from reportlab.lib import colors
    from reportlab.lib.styles import ParagraphStyle
    from reportlab.platypus import CondPageBreak, LongTable, Paragraph, SimpleDocTemplate, TableStyle
    width = 499
    styles = {key: ParagraphStyle(key, fontName=fonts.primary, fontSize=size, leading=leading, wordWrap="CJK",
                                  splitLongWords=True, spaceAfter=4, keepWithNext=False,
                                  textColor=colors.HexColor("#193247"))
              for key, size, leading in (("title", 19, 27), ("heading", 12, 19), ("subheading", 9, 15),
                                         ("body", 9, 15), ("cell", 8, 12), ("metric_cell", 7, 10.5))}
    def paragraph(text, style="body", bold=False):
        return Paragraph(_markup(text, fonts, bold), styles[style])

    def height(items):
        return sum(item.wrap(width - 12, 10000)[1] + item.getSpaceAfter() for item in items)

    def prepare_table(table, section_id):
        prefix = [paragraph(table["title"], "subheading", True)] if table["title"] else []
        if table.get("note"):
            prefix.append(paragraph(table["note"], "cell"))
        cell_style = "metric_cell" if section_id == "evaluation" else "cell"
        rows = [[paragraph(c, cell_style, True) for c in table["columns"]]]
        rows.extend([[paragraph(c, cell_style) for c in row] for row in table["rows"]])
        ratios = table["widths"] or ([0.28, 0.72] if len(rows[0]) == 2 else [1 / len(rows[0])] * len(rows[0]))
        widths = [width * ratio for ratio in ratios]
        item = LongTable(rows, colWidths=widths, repeatRows=1, hAlign="LEFT", splitByRow=1, splitInRow=1)
        item.setStyle(TableStyle([("BACKGROUND", (0, 0), (-1, 0), colors.HexColor("#E6EEF2")),
                                  ("ROWBACKGROUNDS", (0, 1), (-1, -1), [colors.white, colors.HexColor("#F6F8F9")]),
                                  ("GRID", (0, 0), (-1, -1), 0.3, colors.HexColor("#CCD5DB")),
                                  ("VALIGN", (0, 0), (-1, -1), "TOP"), ("LEFTPADDING", (0, 0), (-1, -1), 6),
                                  ("RIGHTPADDING", (0, 0), (-1, -1), 6), ("TOPPADDING", (0, 0), (-1, -1), 3),
                                  ("BOTTOMPADDING", (0, 0), (-1, -1), 3)]))
        item.spaceAfter = 7
        first_rows = sum(max(cell.wrap(cell_width - 12, 10000)[1] for cell, cell_width in zip(row, widths)) + 6
                         for row in rows[:2])
        return prefix, item, height(prefix) + first_rows

    title = _report_title(payload)
    story = [paragraph(title, "title", True)]
    for section in sections:
        lead = [paragraph(section["title"], "heading", True), *(paragraph(text) for text in section["paragraphs"])]
        view = section.get("chart")
        chart = _chart(view, fonts) if view and view.get("dates") else None
        tables = [prepare_table(table, section["id"]) for table in section["tables"]]
        # Reserve the heading, its explanation and the first data row, never an entire long table.
        minimum = height(lead) + (chart.height if chart is not None else tables[0][2] if tables else 0)
        story.append(CondPageBreak(min(minimum + 12, 744)))
        story.extend(lead)
        if chart is not None:
            story.append(chart)
        for index, (prefix, item, minimum) in enumerate(tables):
            if index or chart is not None:
                story.append(CondPageBreak(min(minimum + 12, 744)))
            story.extend(prefix)
            story.append(item)
    def footer(canvas, document):
        from reportlab.pdfbase import pdfmetrics
        canvas.saveState()
        for x, text, right in ((48, _tc(title), False), (547, _tc(f"第 {document.page} 頁"), True)):
            runs = list(fonts.runs(text))
            if right:
                x -= sum(pdfmetrics.stringWidth(t, n, 8) for n, t in runs)
            for name, value in runs:
                canvas.setFont(name, 8)
                canvas.drawString(x, 23, value)
                x += pdfmetrics.stringWidth(value, name, 8)
        canvas.restoreState()
    document = SimpleDocTemplate(str(path), pagesize=(595, 842), leftMargin=48, rightMargin=48,
                                 topMargin=38, bottomMargin=48, pageCompression=0,
                                 title=title, author="個股分析",
                                 subject="candidate-sections-sha256=" + _digest(sections))
    document.build(story, onFirstPage=footer, onLaterPages=footer)


def _inspect_structure(path, sections):
    """Validate the classic xref format emitted above, without a PDF parser dependency."""
    data = Path(path).read_bytes()
    match = re.search(rb"startxref\s+(\d+)\s+%%EOF\s*\Z", data)
    if not data.startswith(b"%PDF-") or not match:
        raise CandidateReportError("pdf_incomplete", "PDF 標頭或結尾不完整。")
    offset = int(match[1])
    tail = data[offset:]
    header = re.match(rb"xref\s+0\s+(\d+)\s*\n", tail)
    if not header:
        raise CandidateReportError("pdf_xref_invalid", "PDF 交叉索引不是本工具可驗證的格式。")
    entries = tail[header.end():].splitlines()
    count = int(header[1])
    if len(entries) < count:
        raise CandidateReportError("pdf_xref_incomplete", "PDF 物件索引不完整。")
    offsets = {}
    for index, entry in enumerate(entries[:count]):
        item = re.fullmatch(rb"(\d{10}) (\d{5}) ([nf])\s*", entry)
        if not item:
            raise CandidateReportError("pdf_xref_invalid", "PDF 物件索引格式不正確。")
        if item[3] == b"n":
            pos = int(item[1])
            if not data[pos:].startswith(f"{index} {int(item[2])} obj".encode("ascii")):
                raise CandidateReportError("pdf_object_invalid", "PDF 物件位置與索引不一致。")
            offsets[index] = pos
    ordered = sorted(offsets.items(), key=lambda item: item[1])
    objects = {index: data[pos:(ordered[i + 1][1] if i + 1 < len(ordered) else offset)]
               for i, (index, pos) in enumerate(ordered)}
    if any(not obj.rstrip().endswith(b"endobj") for obj in objects.values()):
        raise CandidateReportError("pdf_object_incomplete", "PDF 物件未完整終結。")
    pages = [obj for obj in objects.values() if re.search(rb"/Type\s*/Page\b", obj)]
    font_objects = [obj for obj in objects.values() if b"/Subtype /TrueType" in obj]
    if not pages or not font_objects:
        raise CandidateReportError("pdf_content_missing", "PDF 缺少頁面或嵌入中文字型。")
    for obj in font_objects:
        descriptor = re.search(rb"/FontDescriptor\s+(\d+)\s+0\s+R", obj)
        cmap = re.search(rb"/ToUnicode\s+(\d+)\s+0\s+R", obj)
        description = objects.get(int(descriptor[1]), b"") if descriptor else b""
        font_file = re.search(rb"/FontFile2\s+(\d+)\s+0\s+R", description)
        if not cmap or int(cmap[1]) not in objects or not font_file or int(font_file[1]) not in objects:
            raise CandidateReportError("pdf_font_not_embedded", "PDF 字型未完整嵌入，或缺少中文文字對照。")
    if ("candidate-sections-sha256=" + _digest(sections)).encode("ascii") not in data:
        raise CandidateReportError("pdf_sections_binding_mismatch", "PDF 與可見章節內容指紋不一致。")
    return {"pages": len(pages), "embedded_truetype_subsets": len(font_objects), "classic_xref_verified": True}


def _extract_if_available(path, sections):
    # Production has ReportLab only; independent extraction is an explicit separate gate.
    if importlib.util.find_spec("pypdf") is None:
        return {"status": "pending_external", "reason_zh": "目前環境沒有獨立抽文套件，待集中驗收工具抽文。"}
    try:
        from pypdf import PdfReader
        reader = PdfReader(str(path), strict=True)
        actual = re.sub(r"\s+", "", "\n".join(page.extract_text() or "" for page in reader.pages))
        missing = []
        for text in _visible_strings(sections):
            if text.startswith("個股分析報告 第") or text == "0123456789.,%/-：至":
                continue
            clean = re.sub(r"\s+", "", text)
            # A split cell can contain repeated headers and footer text between characters.
            position = 0
            for character in clean:
                position = actual.find(character, position)
                if position < 0:
                    missing.append(clean[:48])
                    break
                position += 1
        if missing:
            raise CandidateReportError("pdf_text_missing", "獨立抽文發現章節文字缺漏。", {"missing_chunks": missing[:20]})
        return {"status": "passed", "pages": len(reader.pages), "extracted_characters": len(actual)}
    except CandidateReportError:
        raise
    except Exception as exc:
        raise CandidateReportError("pdf_text_extraction_failed", "獨立 PDF 抽文失敗，停止驗收。") from exc


def _validate_sidecar_payload(payload, sidecar, path):
    stored = sidecar.get("payload")
    if not isinstance(stored, dict):
        raise CandidateReportError("report_binding_mismatch", "報告資料附件缺少完整分析內容。")
    stored_digest = _digest(stored)
    schema = sidecar.get("schema")
    reference, descriptor_digest = None, None
    if schema == _BUNDLE_V1:
        original_digest = stored_digest
        snapshot = stored.get("snapshot_binding") or {}
        if "descriptor_reference" in snapshot:
            raise CandidateReportError("report_binding_mismatch", "舊版附件不能冒充完整證據引用格式。")
        if "descriptor" in snapshot:
            descriptor_digest = _digest(snapshot["descriptor"])
    elif schema == _BUNDLE_V2:
        snapshot = stored.get("snapshot_binding") or {}
        if "descriptor" in snapshot or not isinstance(snapshot.get("descriptor_reference"), dict):
            raise CandidateReportError("report_binding_mismatch", "引用版附件未正確保存完整證據引用。")
        reference = snapshot["descriptor_reference"]
        descriptor = _resolve_descriptor_reference(reference, stored, path)
        descriptor_digest = reference["descriptor_sha256"]
        original_digest = _digest(_restore_descriptor(stored, descriptor))
        if (sidecar.get("stored_payload_sha256") != stored_digest
                or sidecar.get("original_payload_sha256") != original_digest
                or sidecar.get("runtime_payload_sha256") != stored.get("runtime_payload_sha256")):
            raise CandidateReportError("report_binding_mismatch", "原始或精簡分析內容的指紋綁定不符。")
    else:
        raise CandidateReportError("report_binding_mismatch", "不支援此報告資料附件版本。")
    if sidecar.get("payload_sha256") != original_digest or _digest(payload) not in {original_digest, stored_digest}:
        raise CandidateReportError("report_binding_mismatch", "報告與提供的原始或精簡分析內容不一致。")
    return original_digest, stored_digest, reference, descriptor_digest


@_parsed_evidence_operation(reuse=True)
def validate_candidate_report(payload, pdf_path: Path) -> dict:
    """Return valid=True only for content/font/structure/binding preflight, never visual delivery."""
    return _validate_candidate_report(payload, pdf_path)


def _validate_candidate_report(payload, pdf_path):
    try:
        sections = build_report_sections(payload)
        path = Path(pdf_path)
        _check_output_path(payload, path)
        sidecar_path = path.with_suffix(".json")
        try:
            sidecar = json.loads(sidecar_path.read_text(encoding="utf-8"))
        except (OSError, ValueError) as exc:
            raise CandidateReportError("report_json_missing", "報告資料附件缺失或不可讀，不能通過驗收。") from exc
        layout = "concise_six_chapters"
        if sidecar.get("schema") == _BUNDLE_V1 and sidecar.get("sections") != sections:
            raise CandidateReportError("legacy_render_requires_revalidation", "舊版報告章節與目前模板不同，須重新產出及驗收；舊檔保持原樣。")
        fonts = _load_fonts()
        fonts.check([_report_title(payload), *_visible_strings(sections)])
        structure = _inspect_structure(path, sections)
        info = _file_info(path)
        original_digest, stored_digest, reference, descriptor_digest = _validate_sidecar_payload(payload, sidecar, path)
        if (sidecar.get("sections_sha256") != _digest(sections)
                or sidecar.get("sections") != sections or sidecar.get("pdf") != info):
            raise CandidateReportError("report_binding_mismatch", "報告、輸入、章節或檔案雜湊綁定不一致。")
        extracted = _extract_if_available(path, sections)
        return {"valid": True, "errors": [], "scope": "language_font_sections_pdf_integrity_preflight",
                "language": "zh-TW", "sections": [s["id"] for s in sections], "sections_sha256": _digest(sections),
                "sections_layout": layout,
                "payload_sha256": original_digest, "original_payload_sha256": original_digest,
                "stored_payload_sha256": stored_digest, "runtime_payload_sha256": payload.get("runtime_payload_sha256"),
                "pdf": info, "pdf_structure": structure,
                "language_preflight": "passed", "glyph_coverage": "passed", "text_extraction": extracted,
                "render_status": "pending_external", "deliverable": False,
                "research_only": payload["research_only"], "promotion": payload["promotion"],
                "evidence": _evidence_summary(payload, reference, descriptor_digest)}
    except Exception as exc:
        code = exc.code if isinstance(exc, CandidateReportError) else "report_validation_failed"
        message = str(exc) if isinstance(exc, CandidateReportError) else "個股報告驗收失敗，詳見機器診斷。"
        return {"valid": False, "deliverable": False, "errors": [{"code": code, "message_zh": message,
                "details": getattr(exc, "details", {})}], "render_status": "not_accepted"}


@_parsed_evidence_operation()
def write_candidate_report(payload: dict, output_dir: Path) -> dict:
    """Publish a unique PDF/JSON bundle by one directory rename, only after preflight."""
    _attach_preopen_h1(payload)
    attach_report_comparison(payload)
    sections = build_report_sections(payload)
    output_dir = Path(output_dir)
    _check_output_path(payload, output_dir)
    stock = re.sub(r"[^A-Za-z0-9]", "", str((payload.get("stock") or {}).get("stock_id", "unknown"))) or "unknown"
    horizon = payload.get("horizon_sessions")
    prefix = "single_stock_" if payload["schema"] == FORMAL_SCHEMA else "candidate_"
    stem = prefix + stock + "_H" + (str(horizon) if type(horizon) is int and horizon > 0 else "unknown")
    bundle = output_dir / (stem + "_" + uuid.uuid4().hex)
    _check_output_path(payload, bundle / (stem + ".pdf"))
    fonts = _load_fonts()
    fonts.check([_report_title(payload), *_visible_strings(sections)])
    stored_payload, original_digest, reference = _prepare_report_payload(payload, output_dir)
    if build_report_sections(stored_payload) != sections:
        raise CandidateReportError("report_compaction_content_changed", "證據引用改變了可見報告內容，停止產出。")
    output_dir.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix=".single-stock-report-", dir=output_dir) as temporary:
        stage = Path(temporary) / "bundle"
        stage.mkdir()
        pdf, data_path = stage / (stem + ".pdf"), stage / (stem + ".json")
        _render_pdf(pdf, payload, sections, fonts)
        html_path = None
        if not _read_only_storage(payload):
            html_path = stage / (stem + ".html")
            html_path.write_text(report_preview_html(payload, sections), encoding="utf-8", newline="\n")
        data = {"schema": _BUNDLE_V2 if reference is not None else _BUNDLE_V1,
                "payload": stored_payload, "sections": sections, "machine_evidence": _report_machine_evidence(payload),
                "payload_sha256": original_digest, "sections_sha256": _digest(sections), "pdf": _file_info(pdf),
                "acceptance": {"render_status": "pending_external", "deliverable": False}}
        if reference is not None:
            data.update(original_payload_sha256=original_digest, stored_payload_sha256=_digest(stored_payload),
                        runtime_payload_sha256=payload.get("runtime_payload_sha256"))
        # Record field order affects visible table order; canonical digests still sort keys.
        data_path.write_bytes(_json_bytes(data, sort_keys=not _read_only_storage(payload)))
        validation = _validate_candidate_report(payload, pdf)
        if validation.get("valid") is not True:
            raise CandidateReportError("report_preflight_failed", "個股報告預檢未通過，未發布檔案組。", {"validation": validation})
        data["acceptance"] = validation
        data_path.write_bytes(_json_bytes(data, sort_keys=not _read_only_storage(payload)))
        json_info, pdf_info = _file_info(data_path), _file_info(pdf)
        os.replace(stage, bundle)
    return {"pdf_path": str(bundle / pdf.name), "json_path": str(bundle / data_path.name),
            "html_path": str(bundle / html_path.name) if html_path is not None else None, "bundle_dir": str(bundle),
            "pdf": pdf_info, "json": json_info, "sections": [s["id"] for s in sections], "language": "zh-TW",
            "validation": validation, "valid": True, "deliverable": False, "render_status": "pending_external",
            "research_only": payload["research_only"], "promotion": payload["promotion"]}
