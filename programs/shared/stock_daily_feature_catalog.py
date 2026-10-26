"""Selected-source, in-memory research features; never fetch or mutate data."""

from __future__ import annotations

import copy
import math
import re
from collections import defaultdict
from collections.abc import Mapping

import numpy as np
import pandas as pd

FEATURE_SCHEMA = "tw-stock-selected-features-v1"
_KEYS = ["stock_id", "market", "session_date"]
_HORIZONS = (1, 5, 20, 60, 63)
_LEGACY = ["return_5", "return_20", "return_60", "return_126",
           "return_252", "volatility_20", "volatility_60"]
_ROWS = [
    ("official_calendar", "官方交易日曆", "完整性"),
    ("official_market_daily", "官方日行情", "價量"),
    ("official_trading_restrictions", "交易限制與處置", "完整性"),
    ("global_market_history", "全球市場歷史", "海外"),
    ("finmind_stock_info", "股票基本資料", "完整性"),
    ("finmind_price_adj", "還原股價", "價量"),
    ("finmind_institutional", "三大法人買賣超", "籌碼"),
    ("finmind_margin", "融資融券", "籌碼"),
    ("official_finance_reporting_deadline", "財報申報期限", "財務"),
    ("finmind_month_revenue", "月營收", "財務"),
    ("finmind_financial_statements", "損益與財務報表", "財務"),
    ("finmind_balance_sheet", "資產負債表", "財務"),
    ("finmind_cash_flows_statement", "現金流量表", "財務"),
    ("finmind_dividend", "股利資訊", "事件"),
    ("finmind_dividend_result", "除權息結果", "事件"),
    ("finmind_per", "本益比、股價淨值比與殖利率", "估值"),
    ("finmind_shareholding", "外資持股", "籌碼"),
    ("finmind_day_trading", "現股當沖", "籌碼"),
    ("finmind_futures_institutional", "期貨法人部位", "衍生品"),
    ("finmind_options_institutional", "選擇權法人部位", "衍生品"),
    ("finmind_government_bank", "官股銀行買賣", "籌碼"),
    ("finmind_trading_daily_report", "券商分點交易", "籌碼"),
    ("finmind_block_trade", "鉅額交易", "籌碼"),
    ("finmind_loan_collateral", "借貸與擔保融通", "籌碼"),
    ("finmind_margin_maintenance", "融資維持率", "籌碼"),
    ("official_active_etf_holding", "主動式 ETF 持股", "資金"),
    ("official_active_etf_change", "主動式 ETF 持股變動", "資金"),
    ("finmind_industry_chain_flow", "產業鏈資金流", "產業"),
    ("official_sector_features", "官方產業分類與特徵", "產業"),
    ("finmind_exchange_rate", "匯率", "總經"),
    ("finmind_government_bonds_yield", "公債殖利率", "總經"),
    ("finmind_crude_oil_prices", "原油價格", "總經"),
    ("finmind_gold_price", "黃金價格", "總經"),
    ("tdcc_share_distribution_raw", "集保持股分級", "籌碼"),
    ("news_and_hot_topics_raw", "新聞與熱門題材", "新聞"),
    ("official_sector_observations", "產業與總經觀測指標", "產業"),
]
SOURCE_CATALOG = {route: {"name_zh": name, "category": category}
                  for route, name, category in _ROWS}
_DAILY_PROXY = {"finmind_institutional", "finmind_margin", "finmind_day_trading",
                "finmind_shareholding", "finmind_per"}
_BASE_USED = {"official_calendar", "official_market_daily", "finmind_price_adj"} | _DAILY_PROXY
_LONG_USED = {"finmind_month_revenue", "finmind_financial_statements",
              "finmind_balance_sheet", "finmind_cash_flows_statement",
              "global_market_history", "finmind_exchange_rate", "finmind_government_bonds_yield"}
_CONTEXT = {"finmind_stock_info", "official_sector_features", "official_trading_restrictions",
            "finmind_dividend", "finmind_dividend_result", "news_and_hot_topics_raw",
            "official_finance_reporting_deadline", "finmind_industry_chain_flow",
            "official_sector_observations"} | _LONG_USED
_MACRO = {"global_market_history", "finmind_exchange_rate", "finmind_government_bonds_yield",
          "finmind_crude_oil_prices", "finmind_gold_price"}
_CORE_REQUIRED = {"official_calendar", "official_market_daily", "finmind_price_adj", "finmind_stock_info"}
_FINANCIAL = {"finmind_month_revenue", "finmind_financial_statements",
              "finmind_balance_sheet", "finmind_cash_flows_statement"}


def _horizons(values):
    if not values or any(type(h) is not int or h not in _HORIZONS for h in values):
        raise ValueError("分析期限必須是 1、5、20、60、63 的整數清單。")
    return sorted(set(values))


def select_source_plan(horizons: list[int], industry: str | None = None) -> list[dict]:
    """Return all catalog entries; selection never inspects outcomes or payloads."""
    horizons = _horizons(horizons)
    used = _BASE_USED | (_LONG_USED if max(horizons) >= 20 else set())
    industry = str(industry or "")
    if any(word in industry for word in ("石化", "塑化", "能源", "航運", "航空")):
        used = used | {"finmind_crude_oil_prices"}
    if any(word in industry for word in ("黃金", "貴金屬")):
        used = used | {"finmind_gold_price"}
    plan = []
    for route, item in SOURCE_CATALOG.items():
        usage = "used" if route in used else "context-only" if route in _CONTEXT else "not-selected"
        reason = {"used": "依分析期限與事前領域規則選為特徵或必要證據。",
                  "context-only": "只列當期背景或完整性資訊，不自動回填歷史特徵。",
                  "not-selected": "本次規則未選用；保留來源目錄，不視為資料缺失。"}[usage]
        applicability = "applicable"
        if route in ("finmind_crude_oil_prices", "finmind_gold_price"):
            applicability = "unknown" if not industry else "applicable" if route in used else "not_applicable"
        plan.append(dict(route_id=route, **copy.deepcopy(item), usage=usage, reason_zh=reason,
                         applicability=applicability))
    return plan


def _definition(label, unit, category, route, calculation):
    return dict(label_zh=label, unit=unit, category=category, route=route, source_route=route,
                calculation_zh=calculation)


def _definitions():
    result = {}
    for n in (5, 20, 60, 126, 252, 1):
        result[f"return_{n}"] = _definition(
            f"{n} 日還原收盤報酬", "比例", "價量", "finmind_price_adj",
            f"精確市場日還原收盤價除以 {n} 日前價格減一；不補缺日價格。")
    for n in (20, 60):
        result[f"volatility_{n}"] = _definition(
            f"{n} 日日報酬波動", "比例", "價量", "finmind_price_adj",
            f"{n} 筆有效日報酬樣本標準差，不補價。")
    for column, label in (("volume", "成交量"), ("amount", "成交金額"), ("transactions", "成交筆數")):
        result[f"{column}_ratio_20"] = _definition(
            f"{label}相對前 20 日比值", "倍", "價量", "official_market_daily",
            "同來源同欄位除以前 20 日平均；單位相消，假設序列單位一致。")
    result["intraday_range_ratio"] = _definition(
        "官方原價日內高低幅度", "比例", "價量", "official_market_daily",
        "官方 raw_high 除以官方 raw_low 減一；缺少原價欄位不以未明示來源的 high/low 代替，不混用還原收盤價。")
    definitions = [
        ("foreign_net_volume_ratio", "外資淨買賣占成交股數", "比例", "finmind_institutional"),
        ("trust_net_volume_ratio", "投信淨買賣占成交股數", "比例", "finmind_institutional"),
        ("dealer_net_volume_ratio", "自營商淨買賣占成交股數", "比例", "finmind_institutional"),
        ("foreign_net_relative_20", "外資淨額相對自身前 20 筆絕對量", "倍", "finmind_institutional"),
        ("trust_net_relative_20", "投信淨額相對自身前 20 筆絕對量", "倍", "finmind_institutional"),
        ("dealer_net_relative_20", "自營商淨額相對自身前 20 筆絕對量", "倍", "finmind_institutional"),
        ("margin_balance_change_ratio", "融資餘額變動率", "比例", "finmind_margin"),
        ("short_balance_change_ratio", "融券餘額變動率", "比例", "finmind_margin"),
        ("day_trade_volume_ratio", "當沖股數占成交股數", "比例", "finmind_day_trading"),
        ("day_trade_relative_20", "當沖量相對自身前 20 筆平均", "倍", "finmind_day_trading"),
        ("foreign_holding_ratio", "外資持股比例", "比例", "finmind_shareholding"),
        ("foreign_holding_change_5", "外資持股比例五日變化", "比例差", "finmind_shareholding"),
        ("valuation_per", "本益比", "倍", "finmind_per"),
        ("valuation_pbr", "股價淨值比", "倍", "finmind_per"),
        ("valuation_dividend_yield", "現金殖利率", "比例", "finmind_per"),
        ("revenue_yoy", "已公告月營收年增率", "比例", "finmind_month_revenue"),
        ("financial_operating_margin", "已公告營業利益率", "比例", "finmind_financial_statements"),
        ("balance_debt_ratio", "已公告負債資產比", "比例", "finmind_balance_sheet"),
        ("cashflow_operating_change_ratio", "已公告營業現金流同基礎變動率", "比例", "finmind_cash_flows_statement"),
        ("global_sp500_return_1", "美國標普前筆收盤報酬", "比例", "global_market_history"),
        ("global_nasdaq_return_1", "美國那斯達克前筆收盤報酬", "比例", "global_market_history"),
        ("global_sox_return_1", "美國半導體前筆收盤報酬", "比例", "global_market_history"),
        ("exchange_rate_change_1", "匯率同幣別前筆變動率", "比例", "finmind_exchange_rate"),
        ("bond_yield_change_1", "公債殖利率同序列前筆變化", "原欄位差", "finmind_government_bonds_yield"),
        ("oil_return_1", "原油同品項前筆價格變動率", "比例", "finmind_crude_oil_prices"),
        ("gold_return_1", "黃金同序列前筆價格變動率", "比例", "finmind_gold_price"),
    ]
    for name, label, unit, route in definitions:
        calculation = "僅用訊號截止前已可用資料；計算及單位證據另列來源限制。"
        if "relative_20" in name:
            calculation = "同來源同欄位除以前 20 筆絕對量平均；不使用跨來源分母，假設序列單位一致。"
        if name in ("valuation_per", "valuation_pbr"):
            calculation = "僅採有限且大於零的估值倍數；零與負值保留原始上下文，不作低估訊號。"
        if name == "valuation_dividend_yield":
            calculation = "百分比或比例單位須明示；有限且非負的殖利率可用，合法零值保留。"
        result[name] = _definition(label, unit, SOURCE_CATALOG[route]["category"], route, calculation)
    return result


def _day(value):
    try:
        day = pd.Timestamp(str(value))
        if pd.isna(day):
            raise ValueError
        if day.tzinfo is not None:
            day = day.tz_convert("Asia/Taipei").tz_localize(None)
        return day.normalize()
    except (TypeError, ValueError, OverflowError) as exc:
        raise ValueError("資料日期無法解析。") from exc


def _number(value):
    try:
        result = float(value)
        return result if math.isfinite(result) else np.nan
    except (TypeError, ValueError):
        return np.nan


def _value(row, *names):
    for name in names:
        value = _number(row.get(name))
        if np.isfinite(value):
            return value
    return np.nan


def _ratio(numerator, denominator):
    return numerator / denominator if np.isfinite(numerator) and np.isfinite(denominator) and denominator != 0 else np.nan


def _json(value):
    if isinstance(value, Mapping):
        return {str(k): _json(v) for k, v in value.items()}
    if isinstance(value, (list, tuple, np.ndarray)):
        return [_json(item) for item in value]
    if isinstance(value, (pd.Timestamp,)):
        return value.isoformat()
    if isinstance(value, np.generic):
        return _json(value.item())
    if isinstance(value, float) and not math.isfinite(value):
        return None
    if value is pd.NA or value is pd.NaT:
        return None
    if value is None or isinstance(value, (str, int, float, bool)):
        return value
    return str(value)


def _timestamp(row):
    for field in ("available_time", "published_at", "announcement_time", "source_close_at"):
        value = row.get(field)
        if value is None or not re.search(r"[T ]\d{2}:\d{2}", str(value)):
            continue
        try:
            stamp = pd.Timestamp(value)
            if stamp.tzinfo is not None and not pd.isna(stamp):
                return stamp.tz_convert("Asia/Taipei")
        except (TypeError, ValueError):
            pass
    return None


def _unit(row, manifest, field, kind="volume"):
    for container in (row, manifest):
        units = container.get("units")
        if isinstance(units, Mapping) and units.get(field):
            return str(units[field])
        for key in (field + "_unit", kind + "_unit"):
            value = container.get(key)
            if isinstance(value, str) and value.strip():
                return value.strip()
    return None


def _quantity_scale(source_unit, target_unit, row, manifest):
    names = {"股": "shares", "share": "shares", "shares": "shares",
             "張": "lots", "lot": "lots", "lots": "lots"}
    source, target = names.get(source_unit), names.get(target_unit)
    if source and source == target:
        return 1.0
    size = _value(row, "lot_size")
    if not np.isfinite(size):
        size = _value(manifest, "lot_size")
    if size > 0 and source == "lots" and target == "shares":
        return size
    if size > 0 and source == "shares" and target == "lots":
        return 1 / size
    return np.nan


def _fraction(row, manifest, *fields):
    for field in fields:
        value = _number(row.get(field))
        if not np.isfinite(value):
            continue
        unit = _unit(row, manifest, field, "percentage")
        if unit in ("%", "percent", "百分比", "百分點"):
            return value / 100
        if unit in ("fraction", "ratio", "比例"):
            return value
    return np.nan


def _empty_proven(item):
    evidence = item.get("no_data_evidence")
    return (item.get("legitimate_empty") is True and isinstance(evidence, Mapping)
            and evidence.get("source_scope_complete") is True
            and bool(re.fullmatch(r"[0-9a-fA-F]{64}", str(evidence.get("sha256", "")))))


def build_candidate_features(panel: pd.DataFrame, market_sessions: dict, *,
                             asof_by_market: dict[str, str], horizons: list[int],
                             source_frames: dict[str, pd.DataFrame] | None = None,
                             source_manifest: list[dict] | None = None) -> dict:
    """Build an 18-calendar-month panel and exact endpoint labels without I/O."""
    horizons = _horizons(horizons)
    if not isinstance(panel, pd.DataFrame) or panel.empty or not set(_KEYS + ["adjusted_close"]).issubset(panel):
        raise ValueError("價量面板必須包含股票、市場、日期與還原收盤價。")
    asofs = {str(m).upper(): _day(day) for m, day in asof_by_market.items()}
    axes = {}
    for market, days in market_sessions.items():
        axis = pd.DatetimeIndex([_day(day) for day in days])
        if axis.empty or axis.has_duplicates or not axis.is_monotonic_increasing:
            raise ValueError("市場交易日曆必須非空、唯一且依日期遞增。")
        axes[str(market).upper()] = axis
    data = panel.copy(deep=True)
    data["stock_id"] = data["stock_id"].astype(str).str.strip()
    data["market"] = data["market"].astype(str).str.upper()
    data["session_date"] = data["session_date"].map(_day)
    if data.duplicated(_KEYS).any() or data.stock_id.eq("").any():
        raise ValueError("價量面板存在重複股票市場日期或空白股票代號。")
    data["adjusted_close"] = pd.to_numeric(data["adjusted_close"], errors="coerce")
    known = data.adjusted_close.notna()
    if not (np.isfinite(data.loc[known, "adjusted_close"]) & (data.loc[known, "adjusted_close"] > 0)).all():
        raise ValueError("實際還原收盤價必須為有限正數；缺價不得補值。")
    windows = {}
    for market, part in data.groupby("market"):
        if market not in asofs or market not in axes or asofs[market] not in axes[market]:
            raise ValueError("市場缺少對應資料日或已驗證交易日曆。")
        if (part.session_date > asofs[market]).any():
            raise ValueError("價量面板含分析資料日之後的價格。")
        windows[market] = asofs[market] - pd.DateOffset(months=18)
        relevant = part.loc[part.session_date >= windows[market]]
        if not relevant.session_date.isin(axes[market]).all():
            raise ValueError("價量日期不在對應市場的已驗證交易日曆中。")
    data = data.loc[data.session_date >= data.market.map(windows)].copy()
    if data.empty:
        raise ValueError("18 個月範圍內沒有實際價量資料。")
    source_frames = source_frames or {}
    supplied = {}
    for item in source_manifest or []:
        if not isinstance(item, Mapping) or not item.get("route_id") or item["route_id"] in supplied:
            raise ValueError("來源清單必須有唯一的來源代號。")
        supplied[item["route_id"]] = copy.deepcopy(dict(item))
    manifests = []
    for selection in select_source_plan(horizons):
        old = supplied.pop(selection["route_id"], {})
        item = dict(selection, **{k: v for k, v in old.items() if k not in ("name_zh", "category")})
        if item["usage"] not in ("used", "context-only", "not-selected"):
            raise ValueError("來源用途必須是已選用、僅上下文或未選用。")
        item.setdefault("availability", "unknown_availability")
        item["required_for_analysis"] = item["route_id"] in _CORE_REQUIRED
        manifests.append(item)
    for route, old in supplied.items():
        manifests.append(dict(old, route_id=route, name_zh=old.get("name_zh") or "其他已登錄來源", usage="not-selected",
                              reason_zh="保留原登錄項目；尚無本候選的特徵規則，不自動讀取。"))
    manifest_map = {item["route_id"]: item for item in manifests}
    definitions = _definitions()
    order = list(_LEGACY) + [name for name in definitions if name not in _LEGACY]
    by_horizon = {}
    for h in horizons:
        long_routes = _LONG_USED | {"finmind_crude_oil_prices", "finmind_gold_price"}
        by_horizon[str(h)] = [name for name in order
                             if (h >= 20 or definitions[name]["route"] not in long_routes)
                             and (h >= 60 or name not in ("return_126", "return_252"))]
    limits = {}

    def blocked(route, code, reason):
        key = (route, code)
        entry = limits.setdefault(key, dict(route_id=route, code=code, reason_zh=reason, count=0))
        entry["count"] += 1

    def prior_values(entries, day, at):
        # Revisions known later must never enter an earlier signal's normalization.
        latest = {}
        for old_day, value, known_at in entries:
            if old_day < day and known_at <= at.value:
                latest[old_day] = value
        return [latest[key] for key in sorted(latest)]

    groups, prices, stock_markets = {}, {}, defaultdict(set)
    feature_parts, label_parts = [], []
    for (stock, market), group in data.groupby(["stock_id", "market"], sort=True):
        stock_markets[stock].add(market)
        group = group.set_index("session_date").sort_index()
        axis = axes[market]
        history = axis[(axis >= max(windows[market], group.index.min())) & (axis <= asofs[market])]
        close = group.adjusted_close.reindex(history)
        out = pd.DataFrame(index=history)
        out["adjusted_close"] = close
        out["signal_close"] = close
        out["priced_observations"] = close.notna().cumsum().astype(int)
        out["feature_ready"] = (out.priced_observations >= 60) & close.notna()
        for n in (1, 5, 20, 60, 126, 252):
            out[f"return_{n}"] = close.div(close.shift(n)).sub(1).where(
                close.rolling(n + 1, min_periods=n + 1).count() == n + 1)
        daily = close.pct_change(fill_method=None)
        for n in (20, 60):
            out[f"volatility_{n}"] = daily.rolling(n, min_periods=n).std(ddof=1)
        for field in ("volume", "amount", "transactions"):
            values = pd.to_numeric(group[field], errors="coerce").reindex(history) if field in group else pd.Series(np.nan, index=history)
            values = values.where(np.isfinite(values) & (values >= 0))
            denominator = values.shift(1).rolling(20, min_periods=20).mean().replace(0, np.nan)
            out[f"{field}_ratio_20"] = values / denominator
        for field in ("open", "high", "low", "raw_open", "raw_high", "raw_low", "raw_close"):
            out[field] = pd.to_numeric(group[field], errors="coerce").reindex(history) if field in group else np.nan
        if "raw_high" in group and "raw_low" in group:
            high, low = out["raw_high"], out["raw_low"]
            out["intraday_range_ratio"] = (high / low - 1).where((high >= low) & (low > 0))
        else:
            out["intraday_range_ratio"] = np.nan
            blocked("official_market_daily", "official_raw_price_missing", "缺少官方 raw_high/raw_low，不以還原或未知來源 high/low 冒充官方原價。")
        out["stock_id"], out["market"], out["session_date"] = stock, market, history
        feature_parts.append(out.reset_index(drop=True))
        for day, row in group.iterrows():
            unit = row.get("volume_unit", panel.attrs.get("volume_unit"))
            prices[(stock, market, day)] = (_number(row.get("volume")), unit)
        groups[(stock, market)] = history
        for h in horizons:
            positions = axis.get_indexer(history) + h
            rows = []
            for i, day in enumerate(history):
                endpoint = axis[positions[i]] if positions[i] < len(axis) else None
                signal = _number(close.iloc[i])
                target = _number(close.get(endpoint)) if endpoint is not None else np.nan
                state, reason, value = "observed", "精確期限端點與訊號日皆有有效價格。", np.nan
                if not np.isfinite(signal):
                    state, reason = "missing_signal_price", "訊號日缺少實際價格。"
                elif endpoint is None:
                    state, reason = "unknown_target_session", "已驗證日曆尚無精確期限端點。"
                elif endpoint > asofs[market]:
                    state, reason = "unmatured", "精確期限端點尚未成熟。"
                elif not np.isfinite(target):
                    state, reason = "missing_target_price", "精確期限端點缺價，不改用較晚價格。"
                else:
                    value = target / signal - 1
                rows.append(dict(stock_id=stock, market=market, session_date=day.strftime("%Y-%m-%d"),
                                 horizon=int(h), label_end=endpoint.strftime("%Y-%m-%d") if endpoint is not None else None,
                                 target_return=value, label_state=state, reason_zh=reason))
            label_parts.append(pd.DataFrame(rows))
    features = pd.concat(feature_parts, ignore_index=True).sort_values(_KEYS).reset_index(drop=True)
    events = defaultdict(list)
    contexts = {stock: {"stock_id": stock, "sources": []} for stock in stock_markets}

    def emit(feature, row, value, at, day, stock="", market="", missing_event=False):
        if feature in order and (np.isfinite(value) or missing_event) and at is not None and day is not None:
            events[feature].append((stock, market, at.value, value, day))

    for item in manifests:
        route = item["route_id"]
        if item["usage"] == "not-selected":
            continue
        if route in ("official_calendar", "official_market_daily", "finmind_price_adj"):
            item.setdefault("availability_basis_zh", "由呼叫端提供已驗證市場日曆與價量面板。")
            continue
        source = source_frames.get(route)
        if source is not None and not isinstance(source, pd.DataFrame):
            raise ValueError("來源投影必須是資料表。")
        if source is None or source.empty:
            proven = _empty_proven(item)
            item["availability"] = "legitimate_empty" if proven else "missing"
            if proven:
                item["availability_reason_zh"] = "有對應範圍的正向無資料證據。"
            else:
                item["availability_reason_zh"] = "選用來源未提供可用投影；不以空回應推定合法無資料。"
            continue
        records = []
        for raw in source.to_dict("records"):
            row = dict(raw)
            stock = "" if route in _MACRO else str(row.get("stock_id", row.get("data_id", ""))).strip()
            market = str(row.get("market", "")).upper()
            if market in ("NAN", "NONE", "<NA>"):
                market = ""
            if stock and not market and len(stock_markets.get(stock, ())) == 1:
                market = next(iter(stock_markets[stock]))
            if route in _DAILY_PROXY | _FINANCIAL and (stock, market) not in groups:
                blocked(route, "security_market_unmatched", "來源無法唯一對應本次股票與市場，不把個別公司資料廣播給其他股票。")
                continue
            try:
                day = _day(row.get("date", row.get("session_date")))
            except ValueError:
                day = None
            at = _timestamp(row)
            records.append((row, stock, market, day, at))
        for stock in contexts:
            matches = []
            for row, sid, market, day, at in records:
                if sid and sid != stock:
                    continue
                relevant = [m for m in stock_markets[stock] if not market or market == m]
                if not relevant:
                    continue
                cutoff = max(asofs[m] for m in relevant)
                if day is not None and day > cutoff:
                    continue
                if at is not None and at > cutoff.tz_localize("Asia/Taipei") + pd.Timedelta(days=1):
                    continue
                if route == "news_and_hot_topics_raw" and not (row.get("title") or row.get("headline")):
                    blocked(route, "news_title_missing", "新聞缺少可核對標題，不產生情緒或交易訊號。")
                    continue
                if route == "news_and_hot_topics_raw" and at is None:
                    blocked(route, "news_time_unknown", "新聞缺少具時區的可用時間；僅列未核實時點的背景，不形成歷史訊號。")
                matches.append((at.value if at is not None else day.value if day is not None else -1, row))
            if matches:
                matches.sort(key=lambda entry: entry[0], reverse=True)
                dated = []
                for _, row in matches:
                    try:
                        dated.append((_day(row.get("period_end", row.get("date", row.get("session_date")))), row))
                    except ValueError:
                        pass
                latest_date = max((day for day, _ in dated), default=None)
                if route in _FINANCIAL:
                    retained = [row for day, row in dated if day == latest_date] if latest_date is not None else [row for _, row in matches]
                else:
                    retained = [row for _, row in matches[:6]]
                contexts[stock]["sources"].append(dict(
                    route_id=route, name_zh=item["name_zh"], usage=item["usage"],
                    availability="available" if all(_timestamp(row) is not None for row in retained) else "unknown_availability",
                    reason_zh="當期來源背景；沒有歷史可用時間者不回填為歷史特徵。",
                    latest_data_date=latest_date.strftime("%Y-%m-%d") if latest_date is not None else None,
                    context_scope_zh="最新期間完整投影科目，未裁為六列。" if route in _FINANCIAL else "最近至多六筆可核對資訊。",
                    records=[_json(row) for row in retained]))
                if route == "finmind_per":
                    latest_rows = [row for row_day, row in dated if row_day == latest_date]
                    if latest_rows and any(np.isfinite(value) and value <= 0 for value in (
                            _value(latest_rows[0], "per", "PER"), _value(latest_rows[0], "pbr", "PBR"))):
                        contexts[stock]["sources"][-1]["notes_zh"] = ["本期無可用正估值倍數，不作低估訊號。"]
        item["projected_rows"] = len(records)
        item["availability"] = "available" if records and all(at is not None for _, _, _, _, at in records) else "unknown_availability"
        if item["usage"] != "used":
            continue
        prepared = []
        for row, stock, market, day, at in records:
            if stock and (stock, market) not in groups:
                continue
            if at is None and route in _DAILY_PROXY and day is not None and market in axes:
                requested_lag = item.get("availability_lag_sessions", 1)
                lag = requested_lag if type(requested_lag) is int and requested_lag >= 1 else 1
                position = axes[market].get_indexer([day])[0]
                if position >= 0 and position + lag < len(axes[market]):
                    at = (axes[market][position + lag] + pd.Timedelta(hours=13, minutes=30)).tz_localize("Asia/Taipei")
                    item["historical_PIT_unverified"] = True
                    item["availability_assumption"] = "交易日期至少延後一個已驗證市場交易日，僅為研究代理而非實際公告證據。"
                    item["availability"] = "unknown_availability"
            if at is None:
                blocked(route, "availability_time_unknown", "缺少具時區的實際可用時間；不以任意延遲猜測財報、營收或海外公告。")
                continue
            if day is not None and day > at.tz_localize(None).normalize():
                blocked(route, "availability_precedes_business_date", "可用時間早於來源業務日期，不能作為已知歷史特徵。")
                continue
            prepared.append((row, stock, market, day, at))
        prepared.sort(key=lambda entry: (entry[1], entry[2], entry[4].value, entry[3] or pd.Timestamp.min))
        series = defaultdict(list)
        financial = defaultdict(dict)
        macro_instrument = item.get("selected_instrument")
        if route in _MACRO - {"global_market_history"}:
            if not macro_instrument and route == "finmind_exchange_rate":
                macro_instrument = "USD"
            if macro_instrument and not (route == "finmind_exchange_rate" and macro_instrument == "USD") and item.get("instrument_verified") is not True:
                macro_instrument = None
            if macro_instrument is None:
                blocked(route, "instrument_selection_required", "未封存已核實的幣別、殖利率期限或商品品項，不猜測欄位定義、不混合序列。")
            if macro_instrument is not None:
                item["selected_instrument"] = macro_instrument
        for row, stock, market, day, at in prepared:
            if day is None:
                blocked(route, "business_date_unknown", "來源缺少可核對的業務日期。")
                continue
            if stock and day < windows[market]:
                continue
            values = {}
            if route == "finmind_institutional":
                for prefix, normalized, buy, sell in (
                    ("foreign", "foreign_investors_net", "Foreign_Investor_buy", "Foreign_Investor_sell"),
                    ("trust", "investment_trust_net", "Investment_Trust_buy", "Investment_Trust_sell"),
                    ("dealer", "dealer_net", "Dealer_buy", "Dealer_sell")):
                    net = _value(row, normalized)
                    if not np.isfinite(net):
                        net = _value(row, buy) - _value(row, sell)
                    volume, unit = prices.get((stock, market, day), (np.nan, None))
                    scale = _quantity_scale(_unit(row, item, normalized), unit, row, item)
                    values[prefix + "_net_volume_ratio"] = _ratio(net * scale, volume)
                    if np.isfinite(net):
                        prior = series[(stock, market, prefix, _unit(row, item, normalized))]
                        previous = prior_values(prior, day, at)
                        values[prefix + "_net_relative_20"] = _ratio(net, np.mean(np.abs(previous[-20:]))) if len(previous) >= 20 else np.nan
                        prior.append((day, net, at.value))
                        item["same_series_unit_assumption"] = "同來源同欄位序列單位一致；自身標準化單位相消，不猜股或張。"
                    if np.isfinite(net) and not np.isfinite(scale):
                        blocked(route, "quantity_unit_unproved", "跨來源淨買賣與成交量單位未證實，只保留同序列標準化特徵。")
            elif route == "finmind_margin":
                for prefix, today, yesterday, normalized, change in (
                    ("margin", "MarginPurchaseTodayBalance", "MarginPurchaseYesterdayBalance", "margin_purchase_balance", "margin_purchase_change"),
                    ("short", "ShortSaleTodayBalance", "ShortSaleYesterdayBalance", "short_sale_balance", "short_sale_change")):
                    current = _value(row, today, normalized)
                    previous = _value(row, yesterday)
                    if not np.isfinite(previous):
                        previous = current - _value(row, change)
                    values[prefix + "_balance_change_ratio"] = _ratio(current - previous, previous)
            elif route == "finmind_day_trading":
                field = "day_trade_volume" if "day_trade_volume" in row else "Volume"
                quantity = _value(row, field)
                volume, unit = prices.get((stock, market, day), (np.nan, None))
                scale = _quantity_scale(_unit(row, item, field), unit, row, item)
                values["day_trade_volume_ratio"] = _ratio(quantity * scale, volume)
                prior = series[(stock, market, "daytrade", _unit(row, item, field))]
                previous = prior_values(prior, day, at)
                values["day_trade_relative_20"] = _ratio(quantity, np.mean(previous[-20:])) if len(previous) >= 20 else np.nan
                if np.isfinite(quantity):
                    prior.append((day, quantity, at.value))
                    item["same_series_unit_assumption"] = "同來源當沖量欄位序列單位一致；相對量的單位相消。"
                if np.isfinite(quantity) and not np.isfinite(scale):
                    blocked(route, "quantity_unit_unproved", "當沖量與成交量單位未證實，不計算跨來源比例；保留同欄位相對量。")
            elif route == "finmind_shareholding":
                values["foreign_holding_ratio"] = _fraction(row, item, "holding_percentage", "ForeignInvestmentSharesRatio")
                if not np.isfinite(values["foreign_holding_ratio"]):
                    blocked(route, "percentage_unit_unknown", "持股欄位未明示百分比或比例單位，不猜測縮放。")
            elif route == "finmind_per":
                values = {"valuation_per": _value(row, "per", "PER"), "valuation_pbr": _value(row, "pbr", "PBR"),
                          "valuation_dividend_yield": _fraction(row, item, "dividend_yield")}
                for key in ("valuation_per", "valuation_pbr"):
                    if not values[key] > 0:
                        values[key] = np.nan
                        blocked(route, "positive_valuation_unavailable", "本期無可用正估值倍數，不作低估訊號。")
                if values["valuation_dividend_yield"] < 0:
                    values["valuation_dividend_yield"] = np.nan
            elif route == "finmind_month_revenue":
                year, month = _value(row, "revenue_year", "month_revenue_year"), _value(row, "revenue_month", "month_revenue_month")
                revenue = _value(row, "revenue")
                key = (stock, market, int(year), int(month)) if np.isfinite(year) and np.isfinite(month) else None
                values["revenue_yoy"] = _fraction(row, item, "revenue_yoy")
                if key and np.isfinite(revenue):
                    previous = financial.get((stock, market, int(year) - 1, int(month)), {})
                    if previous and previous["at"] <= at.value:
                        values["revenue_yoy"] = _ratio(revenue - previous["value"], abs(previous["value"]))
                        item["same_series_unit_assumption"] = "同來源營收欄位與同月份前一年單位一致；年增率單位相消。"
                    financial[key] = dict(value=revenue, at=at.value)
            elif route in ("finmind_financial_statements", "finmind_balance_sheet"):
                state = financial[(stock, market, day)]
                field, value = str(row.get("type", "")), _value(row, "value")
                if np.isfinite(value):
                    state[field] = (value, _unit(row, item, "value", "currency"), at)
                pairs = (("Revenue", "OperatingIncome", "financial_operating_margin"),) if route == "finmind_financial_statements" else (
                    ("TotalAssets", "TotalLiabilities", "balance_debt_ratio"), ("Assets", "Liabilities", "balance_debt_ratio"))
                for denominator_key, numerator_key, feature in pairs:
                    if numerator_key in state and denominator_key in state:
                        numerator, denominator = state[numerator_key], state[denominator_key]
                        if numerator[1] and numerator[1] == denominator[1] and numerator[1] not in ("%", "percent"):
                            at = max(numerator[2], denominator[2])
                            values[feature] = _ratio(numerator[0], denominator[0])
                        else:
                            blocked(route, "financial_unit_unproved", "財務分子分母單位未證實一致，不混合金額與百分比。")
            elif route == "finmind_cash_flows_statement":
                if str(row.get("type", "")) in ("ifrs-full:CashFlowsFromUsedInOperatingActivities", "CashFlowsFromUsedInOperatingActivities"):
                    basis = str(row.get("period_basis", ""))
                    prior = series[(stock, market, basis)]
                    current = _value(row, "cash_flow_value", "value")
                    # Match the same calendar reporting span across years, not adjacent YTD quarters.
                    start, end = row.get("period_start"), row.get("period_end")
                    try:
                        span = (_day(start).strftime("%m-%d"), _day(end).strftime("%m-%d")) if start is not None and end is not None else None
                    except ValueError:
                        span = None
                        blocked(route, "financial_period_unknown", "現金流期間起訖不明，不混比累計期與單季。")
                    eligible = [entry for entry in prior if entry[1] == span and entry[2] <= at.value
                                and entry[3] == day.year - 1]
                    if basis and span is not None and eligible:
                        values["cashflow_operating_change_ratio"] = _ratio(current - eligible[-1][0], abs(eligible[-1][0]))
                    if np.isfinite(current) and basis and span is not None:
                        prior.append((current, span, at.value, day.year))
            elif route in ("global_market_history", "finmind_exchange_rate", "finmind_government_bonds_yield", "finmind_crude_oil_prices", "finmind_gold_price"):
                symbol = str(row.get("symbol", row.get("instrument", row.get("currency", row.get("name", "")))))
                if route == "global_market_history":
                    mapping = {"^GSPC": "global_sp500_return_1", "us_sp500": "global_sp500_return_1",
                               "^IXIC": "global_nasdaq_return_1", "us_nasdaq": "global_nasdaq_return_1",
                               "^SOX": "global_sox_return_1", "us_semiconductor": "global_sox_return_1"}
                    feature = mapping.get(symbol)
                    if feature is None:
                        blocked(route, "global_instrument_unmapped", "海外品項不在本版本固定代號對照中，只保留上下文，不猜測指數定義。")
                    field = "adj_close" if "adj_close" in source.columns else "close"
                else:
                    if macro_instrument is None or symbol != macro_instrument:
                        continue
                    feature, field = {"finmind_exchange_rate": ("exchange_rate_change_1", "spot_buy"),
                                      "finmind_government_bonds_yield": ("bond_yield_change_1", "value"),
                                      "finmind_crude_oil_prices": ("oil_return_1", "price"),
                                      "finmind_gold_price": ("gold_return_1", "Price")}[route]
                current = _value(row, field)
                prior = series[(symbol, field)]
                for target_market in windows:
                    if day < windows[target_market]:
                        continue
                    previous = prior_values([entry for entry in prior if entry[0] >= windows[target_market]], day, at)
                    if feature and np.isfinite(current) and previous:
                        value = current - previous[-1] if route == "finmind_government_bonds_yield" else _ratio(current, previous[-1]) - 1
                        emit(feature, row, value, at, day, market=target_market)
                if np.isfinite(current):
                    prior.append((day, current, at.value))
            else:
                blocked(route, "context_adapter_only", "此來源保留登錄與上下文，尚無本版本的歷史數值特徵。")
            for feature, value in values.items():
                emit(feature, row, value, at, day, stock, market, missing_event=route == "finmind_per")
        item["historical_feature_rows"] = sum(sum(np.isfinite(row[3]) for row in rows) for name, rows in events.items()
                                               if definitions[name]["route"] == route)

    for name in order:
        if name not in features:
            features[name] = np.nan
        if not events.get(name):
            continue
        route = definitions[name]["route"]
        for (stock, market), history in groups.items():
            selected = [entry for entry in events[name] if (not entry[0] or entry[0] == stock)
                        and (not entry[1] or entry[1] == market) and entry[4] >= windows[market]]
            if not selected:
                continue
            selected.sort(key=lambda entry: entry[2])
            times = np.array([entry[2] for entry in selected], dtype=np.int64)
            signal_times = (history + pd.Timedelta(hours=13, minutes=30)).tz_localize("Asia/Taipei").asi8
            positions = np.searchsorted(times, signal_times, side="right") - 1
            mask = (features.stock_id == stock) & (features.market == market)
            joined = np.full(len(history), np.nan)
            for i, position in enumerate(positions):
                if position < 0:
                    continue
                entry = selected[position]
                if route in _DAILY_PROXY:
                    lag = manifest_map[route].get("availability_lag_sessions", 1)
                    age = axes[market].get_indexer([history[i]])[0] - axes[market].get_indexer([entry[4]])[0]
                    if age > max(1, lag if type(lag) is int else 1):
                        continue
                joined[i] = entry[3]
            features.loc[mask, name] = joined
    if "foreign_holding_change_5" in order:
        features["foreign_holding_change_5"] = features.groupby(["stock_id", "market"])["foreign_holding_ratio"].diff(5)
    features["session_date"] = features.session_date.dt.strftime("%Y-%m-%d")
    features[order] = features[order].replace([np.inf, -np.inf], np.nan)
    labels = pd.concat(label_parts, ignore_index=True).sort_values(_KEYS + ["horizon"]).reset_index(drop=True)
    coverage = {name: {"available_rows": int(features[name].notna().sum()), "total_rows": len(features),
                       "missing_rows": int(features[name].isna().sum())} for name in order}
    for name, item in coverage.items():
        if not item["available_rows"]:
            item["blocked_reason_zh"] = "本次範圍沒有具備所需歷史、單位或可用時間的計算值；欄位保留，不虛構數值。"
            item["source_blocked_reasons_zh"] = sorted({entry["reason_zh"] for entry in limits.values()
                                                        if entry["route_id"] == definitions[name]["route"]})
    metadata = dict(schema=FEATURE_SCHEMA, feature_schema=FEATURE_SCHEMA, feature_order=order,
                    feature_order_by_horizon=by_horizon, legacy_feature_order=list(_LEGACY),
                    horizons=horizons, asof_by_market={m: day.strftime("%Y-%m-%d") for m, day in asofs.items()},
                    source_manifest=manifests, feature_metadata={name: definitions[name] for name in order},
                    coverage={"features": coverage, "price_rows": len(data), "feature_rows": len(features),
                              "ready_rows": int(features.feature_ready.sum()),
                              "label_states": {str(k): int(v) for k, v in labels.label_state.value_counts().items()}},
                    limitations=list(limits.values()),
                    date_rule="ISO-8601 YYYY-MM-DD；市場代號大寫；期限為精確市場交易日，時間使用台北時區。",
                    window_calendar_months=18, minimum_price_observations=60,
                    readiness_rule_zh="截至訊號日累積至少 60 筆實際有效價格且當日收盤有效；不要求長期特徵全有。",
                    signal_semantics_zh="收盤資料完成後的研究預測；收盤價只是報酬比較基準，不代表已知資訊後仍可成交於該收盤價。",
                    price_basis_zh="adjusted_close 與 open/high/low 保持還原價格；raw_open/raw_high/raw_low/raw_close 與成交量金額筆數由呼叫端對齊官方來源。",
                    selection_rule_zh="依事前期限及領域規則選來源；未使用標籤、selection 或最終保留區的成績挑選欄位。",
                    availability_assumptions=[dict(route_id=item["route_id"], reason_zh=item["availability_assumption"])
                                              for item in manifests if item.get("availability_assumption")],
                    historical_PIT_unverified=True)
    return dict(features=features, labels=labels, metadata=_json(metadata), analysis_context=_json(contexts))
