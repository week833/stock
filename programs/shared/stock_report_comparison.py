"""Read-only, batched Central comparisons. No model, network or formal output writes.

The supplied head belongs to the analysis read, not necessarily model training.
Current fundamentals are descriptive; missing publication evidence never becomes PIT.
"""
from __future__ import annotations

import codecs
import copy
import hashlib
import html
import json
import logging
import math
import re
import stat
import threading
from array import array
from collections import OrderedDict, defaultdict
from contextlib import closing
from datetime import date, datetime, time, timedelta, timezone
from pathlib import Path

SCHEMA = "stock-report-comparison-v1"
CENTRAL_ROOT = Path(r"D:\TWStockData\shared_market_data")
COLUMNS = ("項目", "資料期間", "本期值", "比較", "解讀", "資料日期")
_TZ = timezone(timedelta(hours=8))
_SHA = re.compile(r"[0-9a-f]{64}\Z")
_RESULT_CACHE = OrderedDict()
_CACHE_LOCK = threading.RLock()
_LOG = logging.getLogger(__name__)
_ROUTES = {
    "finmind_month_revenue": "月營收",
    "finmind_financial_statements": "損益表",
    "finmind_balance_sheet": "資產負債表",
    "finmind_cash_flows_statement": "現金流量表",
    "finmind_per": "估值",
    "finmind_institutional": "法人",
    "finmind_margin": "融資融券",
    "tdcc_share_distribution_raw": "集保持股分級",
}
_ACCOUNTS = {"Revenue": "營收", "GrossProfit": "毛利", "OperatingIncome": "營業利益",
             "NetIncome": "本期淨利", "IncomeAfterTaxes": "稅後淨利", "EPS": "每股盈餘",
             "TotalAssets": "資產總額", "TotalLiabilities": "負債總額", "Liabilities": "負債總額",
             "Equity": "權益總額", "CashAndCashEquivalents": "現金及約當現金",
             "ifrs-full:CashFlowsFromUsedInOperatingActivities": "營業活動現金流量",
             "CashFlowsFromUsedInOperatingActivities": "營業活動現金流量",
             "CashFlowsFromOperatingActivities": "營業活動現金流量"}


def _day(value):
    if isinstance(value, datetime):
        return value.date()
    if isinstance(value, date):
        return value
    text = str(value or "").strip()
    for pattern, length in (("%Y%m%d", 8), ("%Y-%m-%d", 10)):
        try:
            return datetime.strptime(text[:length], pattern).date()
        except ValueError:
            pass
    return None


def _d8(value):
    parsed = _day(value)
    return parsed.strftime("%Y%m%d") if parsed else "未提供"


def _number(value):
    if value is None or isinstance(value, bool):
        return None
    try:
        result = float(str(value).replace(",", ""))
    except (ValueError, TypeError):
        return None
    return result if math.isfinite(result) else None


def _num(row, *fields):
    for field in fields:
        value = _number(row.get(field))
        if value is not None:
            return value
    return None


def _fmt(value):
    return "未提供" if value is None else f"{value:,.4f}".rstrip("0").rstrip(".")


def _pct(value):
    return "無法計算" if value is None else f"{value:+.2%}"


def _rate(value, previous):
    return value / previous - 1 if value is not None and previous is not None and previous > 0 else None


def _unit(row, field, entry):
    for source in (row, entry.get("metadata") or {}, entry):
        units = source.get("units") or {}
        value = units.get(field) if isinstance(units, dict) else None
        value = value or source.get(field + "_unit") or source.get("currency_unit") or source.get("unit")
        if value:
            aliases = {"TWD": "新臺幣元", "NTD": "新臺幣元", "元": "新臺幣元",
                       "新台幣元": "新臺幣元", "新台幣千元": "新臺幣千元",
                       "shares": "股", "lots": "張", "times": "倍",
                       "percent": "%", "百分比": "%", "percentage": "%"}
            return aliases.get(str(value), str(value))
    return "單位未核實"


def _amount(row, field, entry):
    value, unit = _num(row, field), _unit(row, field, entry)
    scales = {"新臺幣千元": 1000, "新臺幣百萬元": 1000000, "新臺幣億元": 100000000}
    if unit in scales and value is not None:
        value, unit = value * scales[unit], "新臺幣元"
    return value, unit


def _value(value, unit):
    if value is None:
        return "未提供"
    if unit == "新臺幣元" and abs(value) >= 100000000:
        return _fmt(value / 100000000) + " 億元"
    return _fmt(value) + "（" + unit + "）"


def _source_dates(row):
    result = ["來源日 " + _d8(row.get("date", row.get("資料日期", row.get("period_end"))))]
    for field, label in (("available_time", "可得日"), ("available_at", "可得日"),
                         ("announcement_time", "公告日"), ("published_at", "來源發布日"),
                         ("create_time", "來源建立日（未核實為公告日）"), ("_shared_collected_at", "擷取日")):
        if _day(row.get(field)):
            result.append(label + " " + _d8(row[field]))
    return "；".join(result)


def _visible(row, signal):
    business_day = _day(row.get("date", row.get("資料日期", row.get("period_end"))))
    if business_day is None or business_day > signal:
        return False
    cutoff = datetime.combine(signal, time.max, tzinfo=_TZ)
    return _visible_before_cutoff(row, signal, cutoff)


def _visible_before_cutoff(row, signal, cutoff):
    for field in ("available_time", "available_at", "announcement_time", "published_at", "create_time"):
        value = row.get(field)
        if not value:
            continue
        try:
            stamp = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
            if stamp.tzinfo is None:
                stamp = stamp.replace(tzinfo=_TZ)
            if stamp > cutoff:
                return False
        except ValueError:
            parsed = _day(value)
            if parsed and parsed > signal:
                return False
    return True


def _contained(root, relative, prefix):
    value = str(relative or "").replace("\\", "/")
    if not value.startswith(prefix) or ".." in value.split("/") or Path(value).is_absolute():
        raise ValueError("來源路徑不是允許的不可變物件")
    path = root / value
    resolved = path.resolve(strict=True)
    if root not in resolved.parents:
        raise ValueError("來源路徑超出中央根目錄")
    for part in (path, *path.parents):
        if part == root:
            break
        if part.is_symlink() or getattr(part.stat(), "st_file_attributes", 0) & getattr(stat, "FILE_ATTRIBUTE_REPARSE_POINT", 1024):
            raise ValueError("來源路徑包含重新解析點")
    return resolved


def _head(root, supplied):
    if supplied is not None:
        digest = str(supplied).lower()
        if not _SHA.fullmatch(digest):
            raise ValueError("分析來源雜湊格式無效")
        path = _contained(root, "_manifests/" + digest + ".json", "_manifests/")
        raw = path.read_bytes()
    else:
        # One pointer read for the whole batch; never reread it for another stock.
        raw = (root / "_metadata" / "stock_data_current.json").read_bytes()
        digest = hashlib.sha256(raw).hexdigest()
    if hashlib.sha256(raw).hexdigest() != digest:
        raise ValueError("中央來源清單雜湊不符")
    document = json.loads(raw.decode("utf-8-sig"))
    if not isinstance(document, dict) or not isinstance(document.get("datasets"), list):
        raise ValueError("中央來源清單格式不符")
    return digest, document


def _verified_rows(root, entry):
    """Incrementally decode a JSON array while hashing its exact source bytes."""
    path = _contained(root, entry.get("path"), "_objects/")
    digest, size = str(entry.get("sha256", "")).lower(), entry.get("size")
    if not _SHA.fullmatch(digest) or type(size) is not int or size < 0:
        raise ValueError("來源缺少有效的雜湊與大小")
    before = path.stat()
    if before.st_size != size:
        raise ValueError("來源大小不符")
    hasher, count = hashlib.sha256(), 0
    decoder, utf8 = json.JSONDecoder(), codecs.getincrementaldecoder("utf-8-sig")("strict")
    buffer, position, eof = "", 0, False
    with path.open("rb") as handle:
        def more():
            nonlocal buffer, position, eof, count
            raw = handle.read(65536)
            hasher.update(raw)
            count += len(raw)
            buffer = buffer[position:] + utf8.decode(raw, final=not raw)
            position = 0
            eof = not raw

        def peek():
            nonlocal position
            while True:
                while position < len(buffer) and buffer[position].isspace():
                    position += 1
                if position < len(buffer):
                    return buffer[position]
                if eof:
                    return ""
                more()

        if peek() != "[":
            raise ValueError("來源不是已支援的中央陣列格式")
        position += 1
        empty_allowed = True
        while True:
            char = peek()
            if char == "]" and empty_allowed:
                position += 1
                break
            while True:
                try:
                    row, end = decoder.raw_decode(buffer, position)
                    position = end
                    break
                except json.JSONDecodeError:
                    if eof:
                        raise ValueError("來源 JSON 內容不完整") from None
                    more()
            if not isinstance(row, dict):
                raise ValueError("來源包含非資料列項目")
            yield row
            char = peek()
            if char == "]":
                position += 1
                break
            if char != ",":
                raise ValueError("來源 JSON 分隔格式不符")
            position += 1
            empty_allowed = False
        if peek():
            raise ValueError("來源 JSON 含額外內容")
    after = path.stat()
    if (count != size or (before.st_size, before.st_mtime_ns) != (after.st_size, after.st_mtime_ns)
            or hasher.hexdigest() != digest):
        raise ValueError("來源雜湊、大小或讀取期間身分不符")


def _projection_rows(root, entry, stocks, head_sha, consumer_temp_root):
    """Use an existing lossless stock index, or verify the original full route."""
    import stock_query_projection as projection

    result = iterator = None
    consumed = fallback = False
    reason = None
    try:
        result = projection.query_published_rows(
            root, entry, entry["route_id"], parent_identity=head_sha,
            allow_build=False, stock_ids=stocks, start_date=None, end_date=None,
            before_count=0, columns=None, consumer_temp_root=consumer_temp_root)
        missing = result.manifest.get("missing_stock_rows")
        if (result.field_map.get("stock_field") != "stock_id"
                or type(missing) is not int or missing != 0):
            fallback = True
            reason = "stock_mapping_not_lossless"
        else:
            iterator = result.iter_rows()
            for row in iterator:
                consumed = True
                yield row
    except (projection.ProjectionUnavailableError, projection.UnsupportedProjectionError) as exc:
        if consumed:
            raise
        fallback = True
        reason = type(exc).__name__ + ":" + str(exc)
    finally:
        try:
            if iterator is not None:
                iterator.close()
        finally:
            if result is not None:
                result.close()
    if fallback:
        _LOG.warning("comparison_read route=%s mode=raw_fallback reason=%s", entry["route_id"], reason)
        yield from _verified_rows(root, entry)
    else:
        _LOG.info("comparison_read route=%s mode=verified_projection", entry["route_id"])


def _collect(root, entry, stocks, signal, *, rows=None):
    route, grouped = entry["route_id"], {stock: {} for stock in stocks}
    valuation = {stock: {} for stock in stocks} if route == "finmind_per" else None
    availability_cutoff = None
    if rows is None:
        rows = _verified_rows(root, entry)
    for row in rows:
        stock = str(row.get("stock_id", row.get("證券代號", row.get("data_id", "")))).strip()
        if stock not in grouped:
            continue
        day = _day(row.get("date", row.get("資料日期", row.get("period_end"))))
        if day is None or day > signal:
            continue
        if availability_cutoff is None:
            availability_cutoff = datetime.combine(signal, time.max, tzinfo=_TZ)
        if not _visible_before_cutoff(row, signal, availability_cutoff):
            continue
        if valuation is not None:
            for field, aliases in (("per", ("per", "PER")), ("pbr", ("pbr", "PBR"))):
                value = _num(row, *aliases)
                if value is None:
                    continue
                series = valuation[stock].setdefault(field, {"values": array("d"), "days": set(),
                    "first": day, "last": day, "current": value, "row": row, "duplicate": False})
                if day in series["days"]:
                    series["duplicate"] = True
                    continue
                series["days"].add(day)
                if value > 0:
                    series["values"].append(value)
                series["first"] = min(series["first"], day)
                if day >= series["last"]:
                    series.update(last=day, current=value, row=row)
            continue
        account = str(row.get("type", ""))
        if route in {"finmind_financial_statements", "finmind_balance_sheet", "finmind_cash_flows_statement"} and account not in _ACCOUNTS:
            continue
        if route == "finmind_month_revenue":
            key = (row.get("revenue_year", row.get("month_revenue_year")), row.get("revenue_month", row.get("month_revenue_month")))
        else:
            key = (day, account, str(row.get("持股分級", row.get("holding_level", ""))),
                   str(row.get("period_basis", "")), str(row.get("name", "")))
        previous = grouped[stock].get(key)
        if previous is not None and previous != row:
            # Only an explicit later revision timestamp chooses a revision. Otherwise keep a conflict.
            stamp = str(row.get("available_time", row.get("create_time", "")))
            old_stamp = str(previous.get("available_time", previous.get("create_time", "")))
            if stamp > old_stamp and stamp:
                grouped[stock][key] = row
            elif stamp == old_stamp:
                grouped[stock][key] = dict(previous, _comparison_conflict=True)
        else:
            grouped[stock][key] = row
        if route in {"finmind_institutional", "finmind_margin"} and len(grouped[stock]) > 100:
            days = sorted({value[0] for value in grouped[stock]})
            if len(days) > 40:
                cutoff = days[-40]
                grouped[stock] = {key: value for key, value in grouped[stock].items() if key[0] >= cutoff}
        if route == "tdcc_share_distribution_raw" and len(grouped[stock]) > 40:
            by_level = defaultdict(list)
            for old_key in grouped[stock]:
                by_level[old_key[2]].append(old_key)
            keep = {key for keys in by_level.values() for key in sorted(keys, key=lambda value: value[0])[-2:]}
            grouped[stock] = {key: value for key, value in grouped[stock].items() if key in keep}
    if valuation is not None:
        return {stock: [{"_valuation_stats": values}] if values else [] for stock, values in valuation.items()}
    return {stock: list(values.values()) for stock, values in grouped.items()}


def _metric(block, key, label, period, current, unit, comparison, interpretation, row, **details):
    item = {"id": key, "current_value": current, "unit": unit,
            "項目": label, "資料期間": period, "本期值": _value(current, unit),
            "比較": comparison, "解讀": interpretation, "資料日期": _source_dates(row), **details}
    block["metrics"].append(item)
    return item


def _growth_text(current, previous, label, unit=None):
    if previous is None:
        return label + "未提供"
    delta = current - previous if current is not None else None
    if previous <= 0:
        return label + " " + (_value(previous, unit) if unit else _fmt(previous)) + "；基期為" + ("零" if previous == 0 else "負值") + "，不列成長率；差額 " + _fmt(delta)
    return label + " " + (_value(previous, unit) if unit else _fmt(previous)) + "；變化 " + _pct(_rate(current, previous))


def _fact(label, current, previous):
    growth = _rate(current, previous)
    if growth is None:
        return "與" + label + "的成長率無法計算，原值與差額仍保留"
    direction = "增加" if growth > 0 else "減少" if growth < 0 else "持平"
    return "較" + label + direction + " " + f"{abs(growth):.2%}"


def _month_key(year, month):
    try:
        year, month = int(year), int(month)
        date(year, month, 1)
        return year * 12 + month - 1
    except (ValueError, TypeError, OverflowError):
        return None


def _month(key):
    return f"{key // 12:04}{key % 12 + 1:02}"


def _revenue(block, rows, entry):
    values, records, units = {}, {}, {}
    for row in rows:
        key = _month_key(row.get("revenue_year", row.get("month_revenue_year")), row.get("revenue_month", row.get("month_revenue_month")))
        if key is None:
            continue
        value, unit = _amount(row, "revenue", entry)
        values[key], records[key], units[key] = (None if row.get("_comparison_conflict") else value), row, unit
    if not values:
        block["notes"].append("月營收沒有訊號日前可列示的月份；其他資料仍保留。")
        return
    end, start = max(values), min(values)
    row, current, unit = records[end], values[end], units[end]
    comparable = lambda key: values.get(key) if units.get(key) == unit else None
    previous, year_ago = comparable(end - 1), comparable(end - 12)
    block["notes"].append("最新可見營收月份 " + _month(end) + "；實際來源歷史 " + _month(start) + "～" + _month(end) + "，共 " + str(len(values)) + " 個月份。")
    if unit == "單位未核實":
        block["notes"].append("營收保留原始值；同源同比採單位一致假設，未擅自換算億元。")
    if any(key in units and units[key] != unit for key in (end-1, end-12)):
        block["notes"].append("營收比較基期存在，但單位口徑不同或未核實，未跨單位計算成長率。")
    monthly = _metric(block, "revenue_monthly", "月營收", _month(end), current, unit,
        _growth_text(current, year_ago, "去年同月（" + _month(end-12) + "）", unit) + "；" +
        _growth_text(current, previous, "前月（" + _month(end-1) + "）", unit),
        _fact("去年同月", current, year_ago) + "；" + _fact("上月", current, previous) +
        "。月比可能受季節性、工作天與春節影響；營收變化不直接代表獲利或股價方向。", row,
        previous_value=previous, year_ago_value=year_ago, previous_period=_month(end-1), year_ago_period=_month(end-12),
        yoy=_rate(current, year_ago), mom=_rate(current, previous),
        delta_yoy=current-year_ago if current is not None and year_ago is not None else None,
        actual_history_start=_month(start), actual_history_end=_month(end), observed_months=len(values))
    if row.get("_comparison_conflict"):
        monthly["解讀"] = "同月份來源數值衝突，未任選；原有其他資料仍保留。"
    for key, label, keys in (
        ("revenue_ytd", "年初累計營收", list(range(end // 12 * 12, end + 1))),
        ("revenue_3m", "近三月營收", list(range(end - 2, end + 1))),
        ("revenue_12m", "近十二月營收", list(range(end - 11, end + 1)))):
        missing = [_month(k) for k in keys if comparable(k) is None]
        old_keys = [k - 12 for k in keys]
        old_missing = [_month(k) for k in old_keys if comparable(k) is None]
        known = sum(comparable(k) for k in keys if comparable(k) is not None)
        total = known if not missing else None
        old = sum(comparable(k) for k in old_keys) if not old_missing else None
        item = _metric(block, key, label, _month(keys[0]) + "～" + _month(keys[-1]), total, unit,
            _growth_text(total, old, "去年同期間", unit) + "（" + _month(old_keys[0]) + "～" + _month(old_keys[-1]) + "）",
            _fact("去年同期", total, old) + "。按同期間合計比較，不平均月成長率；不據此判定獲利好壞。", row,
            year_ago_value=old, yoy=_rate(total, old), complete=not missing, known_sum=known,
            missing_months=missing, prior_missing_months=old_missing)
        if missing:
            item["本期值"] = "已知 " + str(len(keys)-len(missing)) + "／" + str(len(keys)) + " 月合計 " + _value(known, unit)
            item["解讀"] = "缺少 " + "、".join(missing) + "；已知合計不是完整期間營收，不補零。"
        if old_missing:
            item["比較"] += "；同期缺月 " + "、".join(old_missing)
    same = [(k, value) for k, value in values.items() if k % 12 == end % 12 and units[k] == unit and value is not None]
    same.sort()
    years = [k // 12 for k, value in same]
    rank = 1 + sum(value > current for k, value in same) if current is not None else None
    _metric(block, "revenue_same_month", "歷年同月位置", "、".join(str(year) for year in years) + " 年 " + f"{end % 12 + 1:02}" + " 月",
        current, unit, "；".join(str(k // 12) + "年 " + _value(value, unit) for k, value in same),
        ("實際 " + str(len(same)) + " 次同月觀測（含本年），由高至低第 " + str(rank) + " 名；同值同名次，不宣稱未提供年份齊全。") if rank else "本月數值缺漏，仍列實際可比較年份。",
        row, historical_years=years, rank=rank, count=len(same))


def _financial(block, rows, entry):
    groups = defaultdict(dict)
    for row in rows:
        basis = str(row.get("period_basis") or ("期末存量" if entry["route_id"] == "finmind_balance_sheet" else "期間口徑未核實"))
        end = _day(row.get("period_end", row.get("date")))
        account = str(row.get("type", ""))
        value, unit = _amount(row, "value", entry)
        if end is not None:
            groups[(account, basis)][end] = (None if row.get("_comparison_conflict") else value, unit, row)
    for (account, basis), series in sorted(groups.items()):
        end = max(series)
        current, unit, row = series[end]
        prior = date(end.year-1, end.month, min(end.day, 28)) if end.month == 2 and end.day == 29 else end.replace(year=end.year-1)
        old = series.get(prior)
        # Unknown period spans remain visible, but must not masquerade as comparable quarters.
        comparable = old is not None and old[1] == unit and basis != "期間口徑未核實"
        if comparable and basis == "year_to_date":
            starts = (_day(row.get("period_start")), _day(old[2].get("period_start")))
            comparable = all(value is not None for value in starts) and starts[0].month == starts[1].month and starts[0].day == starts[1].day
            # Provider rows explicitly marked YTD with no start retain a qualified same-basis comparison.
            if not any(starts):
                comparable = True
        previous = old[0] if comparable else None
        _metric(block, "financial_" + account + "_" + basis, _ACCOUNTS.get(account, account),
            _d8(end) + "（" + {"year_to_date": "年初累計", "quarter": "單季", "single_quarter": "單季"}.get(basis, basis) + "）",
            current, unit, _growth_text(current, previous, "去年同口徑", unit) + ("；期間口徑或基期未核實" if not comparable else ""),
            "期末日不等於公告日；累計不當成單季，當期比較不宣稱回測時已公開。", row,
            year_ago_value=previous, yoy=_rate(current, previous), period_basis=basis)


def _valuation(block, rows, entry):
    if rows and "_valuation_stats" in rows[0]:
        for field, series in rows[0]["_valuation_stats"].items():
            current, values = series["current"], series["values"]
            position = (sum(value <= current for value in values) / len(values)
                        if values and current > 0 and not series["duplicate"] else None)
            _metric(block, "valuation_" + field, "本益比" if field == "per" else "股價淨值比",
                _d8(series["last"]), current, "倍",
                "自身歷史 " + _d8(series["first"]) + "～" + _d8(series["last"]) +
                "，正值觀測 " + str(len(values)) + " 筆；累積位置 " + (f"{position:.1%}" if position is not None else "不適用"),
                "歷史位置不是好壞評分或合理價；非正倍數不解讀成便宜。" +
                ("同日觀測重複，未計算歷史位置。" if series["duplicate"] else "盈餘與淨值期間仍須核對。"), series["row"],
                historical_start=_d8(series["first"]), historical_end=_d8(series["last"]), historical_count=len(values), percentile=position)
        return
    for field, aliases, label in (("per", ("per", "PER"), "本益比"), ("pbr", ("pbr", "PBR"), "股價淨值比")):
        series = {}
        for row in rows:
            value = _num(row, *aliases)
            if value is not None and not row.get("_comparison_conflict"):
                series[_day(row["date"])] = (value, row)
        if not series:
            continue
        end, start = max(series), min(series)
        current, row = series[end]
        values = [item[0] for item in series.values() if item[0] > 0]
        position = sum(value <= current for value in values) / len(values) if values and current > 0 else None
        _metric(block, "valuation_" + field, label, _d8(end), current, "倍",
            "自身歷史 " + _d8(start) + "～" + _d8(end) + "，正值觀測 " + str(len(values)) + " 筆；累積位置 " + (f"{position:.1%}" if position is not None else "不適用"),
            "歷史位置不是好壞評分或合理價；非正倍數不解讀成便宜，盈餘與淨值期間仍須核對。", row,
            historical_start=_d8(start), historical_end=_d8(end), historical_count=len(values), percentile=position)


def _flows(block, rows, entry):
    for key, field, label in (("foreign", "foreign_investors_net", "外資"), ("trust", "investment_trust_net", "投信"), ("dealer", "dealer_net", "自營商")):
        series, source_rows = {}, {}
        for row in rows:
            value = _num(row, field)
            if value is None or row.get("_comparison_conflict"):
                continue
            day = _day(row["date"])
            unit = _unit(row, field, entry)
            if day in series and series[day][1] != unit:
                series[day] = (None, "單位不一致")
            else:
                previous = series.get(day, (0, unit))[0]
                series[day] = (previous + value if previous is not None else None, unit)
            source_rows[day] = row
        days = sorted(series)
        if not days:
            continue
        for window in (5, 20):
            selected = days[-window:]
            units = {series[day][1] for day in selected}
            total = sum(series[day][0] for day in selected) if len(units) == 1 and all(series[d][0] is not None for d in selected) else None
            unit = next(iter(units)) if len(units) == 1 else "單位不一致"
            _metric(block, "institutional_" + key + "_" + str(window), label + "近" + str(window) + "有資料日淨買賣",
                _d8(selected[0]) + "～" + _d8(selected[-1]), total, unit,
                "實際 " + str(len(selected)) + "／" + str(window) + " 有資料日；最近單日 " + _fmt(series[days[-1]][0]),
                "正值為淨買、負值為淨賣；依有資料日排列，缺交易日未判定，不據此保證股價方向。", source_rows[days[-1]],
                observed_days=len(selected), requested_days=window)


def _margin(block, rows, entry):
    for field, aliases, label in (("margin_purchase_balance", ("margin_purchase_balance", "MarginPurchaseTodayBalance"), "融資餘額"),
                                  ("short_sale_balance", ("short_sale_balance", "ShortSaleTodayBalance"), "融券餘額")):
        series = {}
        for row in rows:
            value = _num(row, *aliases)
            if value is not None and not row.get("_comparison_conflict"):
                series[_day(row["date"])] = (value, _unit(row, field, entry), row)
        days = sorted(series)
        if not days:
            continue
        current, unit, row = series[days[-1]]
        previous = series[days[-2]][0] if len(days) > 1 and series[days[-2]][1] == unit else None
        delta = current - previous if previous is not None else None
        comparison = "前一有資料日 " + (_d8(days[-2]) if len(days)>1 else "未提供") + "：" + _fmt(previous) + "；增減 " + _fmt(delta)
        for n in (5, 20):
            if len(days)>n and series[days[-1-n]][1] == unit:
                comparison += "；較 " + str(n) + " 有資料日前增減 " + _fmt(current-series[days[-1-n]][0])
        _metric(block, field, label, _d8(days[-1]), current, unit, comparison,
            "餘額與增減分列；股、張未核實不換算，融資融券變化不直接等於股價多空。", row,
            previous_value=previous, delta=delta)


def _tdcc(block, rows, entry):
    groups = defaultdict(dict)
    for row in rows:
        level = str(row.get("持股分級", row.get("holding_level", "")))
        value = _num(row, "占集保庫存數比例%", "holding_percentage", "percent")
        if level and value is not None and not row.get("_comparison_conflict"):
            unit = "%" if "占集保庫存數比例%" in row else _unit(row, "holding_percentage", entry)
            groups[level][_day(row.get("資料日期", row.get("date")))] = (value, unit, row)
    for level, series in sorted(groups.items()):
        days = sorted(series)
        current, unit, row = series[days[-1]]
        weekly = len(days)>1 and (days[-1]-days[-2]).days == 7
        previous = series[days[-2]][0] if len(days)>1 and series[days[-2]][1] == unit else None
        delta = current-previous if previous is not None else None
        _metric(block, "tdcc_" + level, "集保持股分級 " + level, _d8(days[-1]), current, unit,
            ("前週" if weekly else "前次觀測（非完整週比較）") + " " + (_d8(days[-2]) if len(days)>1 else "未提供") + "：" + _fmt(previous) + "；變化 " + _fmt(delta) + (" 個百分點" if unit == "%" else "（原單位）"),
            "只比較相同持股級距；需留意股本與級距定義變更，不把大戶稱為主力或推論交易。", row,
            previous_value=previous, delta=delta, weekly_comparable=weekly)


def build_comparisons(stock_ids, signal_date, *, central_root=None, head_sha=None,
                      use_verified_projection=False, consumer_temp_root=None) -> dict[str, dict]:
    """Serialize cache misses; verified projection reads require explicit opt-in."""
    with _CACHE_LOCK:
        return _build_comparisons(stock_ids, signal_date, central_root=central_root, head_sha=head_sha,
                                  use_verified_projection=use_verified_projection,
                                  consumer_temp_root=consumer_temp_root)


def _build_comparisons(stock_ids, signal_date, *, central_root=None, head_sha=None,
                       use_verified_projection=False, consumer_temp_root=None):
    """Stream each selected immutable route once for the entire stock batch."""
    stocks = tuple(dict.fromkeys(str(stock).strip() for stock in stock_ids if str(stock).strip()))
    signal = _day(signal_date)
    if signal is None:
        raise ValueError("比較訊號日必須是有效日期，不能使用電腦今天代替")
    blocks = {stock: {"schema": SCHEMA, "stock_id": stock, "signal_date": signal.isoformat(),
        "source_head_sha256": None, "source_pin_mode": "analysis_pinned_head" if head_sha is not None else "newly_pinned_for_comparison",
        "historical_publication_verified": False, "metrics": [], "notes": [
            "當期基本面比較不代表回測時已公開；缺值、舊資料與原預測均保留，不改排序或評分。"]} for stock in stocks}
    if not stocks:
        return blocks
    try:
        root = Path(central_root or CENTRAL_ROOT).resolve(strict=True)
        digest, document = _head(root, head_sha)
    except Exception as exc:
        for block in blocks.values():
            block["notes"].append("歷史比較不可得：" + re.sub(r"[0-9a-fA-F]{64}", "（來源識別見資料檔）", str(exc))[:180])
        return blocks
    cache_key = (str(root), digest, signal.isoformat(), head_sha is not None, use_verified_projection)
    cached = _RESULT_CACHE.get(cache_key, {})
    remaining = tuple(stock for stock in stocks if stock not in cached)
    if not remaining:
        _RESULT_CACHE.move_to_end(cache_key)
        return {stock: copy.deepcopy(cached[stock]) for stock in stocks}
    all_stocks = stocks
    stocks = remaining
    blocks = {stock: blocks[stock] for stock in stocks}
    for block in blocks.values():
        block["source_head_sha256"] = digest
        if head_sha is None:
            block["notes"].append("比較另行讀取中央來源快照；分析來源未提供，不宣稱與分析或模型同一來源。")
    entries = defaultdict(list)
    for entry in document["datasets"]:
        if isinstance(entry, dict):
            entries[entry.get("route_id")].append(entry)
    handlers = {"finmind_month_revenue": _revenue, "finmind_per": _valuation,
                "finmind_institutional": _flows, "finmind_margin": _margin,
                "tdcc_share_distribution_raw": _tdcc}
    for route, name in _ROUTES.items():
        if len(entries[route]) != 1:
            for block in blocks.values():
                block["notes"].append(name + "比較不可得：來源未提供或物件不唯一。")
            continue
        entry = entries[route][0]
        try:
            if use_verified_projection:
                with closing(_projection_rows(root, entry, stocks, digest, consumer_temp_root)) as rows:
                    grouped = _collect(root, entry, stocks, signal, rows=rows)
            else:
                grouped = _collect(root, entry, stocks, signal)
            for stock, rows in grouped.items():
                block = blocks[stock]
                if rows:
                    # Compute into a private block so a route failure cannot discard earlier data.
                    addition = {"metrics": [], "notes": []}
                    handlers.get(route, _financial)(addition, rows, entry)
                    for metric in addition["metrics"]:
                        metric["source_route"] = route
                        metric["source_object_sha256"] = entry["sha256"]
                    block["metrics"].extend(addition["metrics"])
                    block["notes"].extend(addition["notes"])
                else:
                    block["notes"].append(name + "沒有訊號日前可見的本股資料，原預測與其他數值保留。")
                if str(entry.get("status", "")).upper() not in {"SUCCESS", "COMPLETE"}:
                    block["notes"].append(name + "來源狀態 " + str(entry.get("status", "UNKNOWN")) + "；可讀部分保留，不宣稱完整。")
        except Exception as exc:
            for block in blocks.values():
                block["notes"].append(name + "比較不可得：" + re.sub(r"[0-9a-fA-F]{64}", "（來源識別見資料檔）", str(exc))[:180])
    for block in blocks.values():
        block["notes"] = list(dict.fromkeys(block["notes"]))
    combined = dict(cached)
    # Newly built blocks are private; the cache owns them until the defensive return copy.
    combined.update(blocks)
    _RESULT_CACHE[cache_key] = combined
    _RESULT_CACHE.move_to_end(cache_key)
    while len(_RESULT_CACHE) > 3:
        _RESULT_CACHE.popitem(last=False)
    return {stock: copy.deepcopy(combined[stock]) for stock in all_stocks}


def comparison_rows(block) -> list[dict]:
    """Six stable Traditional-Chinese display columns; no source I/O."""
    if not isinstance(block, dict):
        return []
    return [{column: str(item.get(column, "未提供")) for column in COLUMNS}
            for item in block.get("metrics") or [] if isinstance(item, dict)]


def comparison_summary(block, max_items=3) -> list[str]:
    """Consume an already sealed block, never reload Central from a caption."""
    if not isinstance(block, dict) or max_items <= 0:
        return []
    rows = comparison_rows(block)
    return _summary_from_rows(block, rows, max_items)


def comparison_display(block, max_items=3):
    """Return full display rows and the existing summary with one row conversion."""
    rows = comparison_rows(block)
    summary = ([] if not isinstance(block, dict) or max_items <= 0
               else _summary_from_rows(block, rows, max_items))
    return rows, summary


def _summary_from_rows(block, rows, max_items):
    if not rows:
        return ["歷史比較不可得；原有分析保留。"][:max_items]
    metrics = [item for item in block.get("metrics") or [] if isinstance(item, dict)]
    summaries = []
    for item, row in zip(metrics[:max_items], rows[:max_items]):
        key = item.get("id")
        current = _number(item.get("current_value"))
        if key == "revenue_monthly":
            facts = _fact("去年同月", current, _number(item.get("year_ago_value"))) + "；" + _fact(
                "上月", current, _number(item.get("previous_value")))
        elif key in {"revenue_ytd", "revenue_3m", "revenue_12m"}:
            facts = _fact("去年同期", current, _number(item.get("year_ago_value")))
        else:
            # Older sealed blocks may only carry display fields; keep their facts.
            facts = row["比較"]
        facts = facts.replace("增加 ", "增加").replace("減少 ", "減少").replace("持平 ", "持平")
        found = re.search(r"(?<!\d)\d{8}(?!\d)", row["資料日期"])
        source_day = found.group(0) if found else "未提供"
        assumption = "；同比採同源單位一致假設" if item.get("unit") == "單位未核實" else ""
        summaries.append(row["項目"] + " " + row["資料期間"] + "：" + row["本期值"] +
                         "；" + facts + "；資料日" + source_day + assumption)
    return summaries


def comparison_html(block) -> str:
    """Pure preview API. Returns HTML only; never starts a bot or writes a file."""
    rows = comparison_rows(block)
    esc = lambda value: html.escape(str(value), quote=True)
    head = "".join("<th>" + esc(column) + "</th>" for column in COLUMNS)
    body = "".join("<tr>" + "".join("<td>" + esc(row[column]) + "</td>" for column in COLUMNS) + "</tr>" for row in rows)
    notes = "".join("<li>" + esc(note) + "</li>" for note in (block or {}).get("notes", []))
    summary = "".join("<li>" + esc(line) + "</li>" for line in comparison_summary(block))
    return ('<!doctype html><html lang="zh-Hant"><meta charset="utf-8"><title>歷史比較預覽</title>'
            '<style>body{font-family:"Microsoft JhengHei",sans-serif;margin:24px;line-height:1.6}'
            'table{border-collapse:collapse;width:100%}td,th{border:1px solid #ccd;padding:8px;text-align:left}</style>'
            '<h1>歷史比較與中文解讀</h1><p>股票 ' + esc((block or {}).get("stock_id", "未提供")) +
            '；分析日 ' + esc(_d8((block or {}).get("signal_date"))) + '</p><h2>歷史比較摘要</h2><ul>' + summary + '</ul><table><thead><tr>' + head +
            '</tr></thead><tbody>' + body + '</tbody></table><ul>' + notes + '</ul></html>')
