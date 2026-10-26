r"""日期格式：使用者看得到的日期一律 YYYYMMDD（8 位 ASCII 數字）；讀取端兩種拼寫都收。

與「收集資料工具」（D:\collect data tool\app\date_format.py）同一套語意與拒絕清單：

- 日期入口（:func:`parse_day`）接受 ``datetime.date``、ASCII ``YYYYMMDD``、ASCII ``YYYY-MM-DD``，並以 ``date()``
  驗證真實日曆日。拒絕 datetime（時間戳）、int、bool、None、前後有空白或換行、全形數字、``YYYY/MM/DD``、
  ``YYYY-M-D``、期間（``YYYYMM``）、7 位民國年、ISO 週（``2026W401``）。
- 讀已存資料（:func:`to_day`）寬鬆：不是上述日期就回 None，由呼叫端決定怎麼處理。JSON 數字（例如 20260105）
  一律不是日期，不可被當成 1970 年或西元 20260105 年之類的時間點。
- 比較、去重、窗口邊界一律比日期值，不比字串（``"20261001" >= "2026-10-02"`` 在字串比較下是 True）。
- 已發布的資料與原始證據不改寫；機器欄位、快取／識別鍵維持 ``YYYY-MM-DD``（:func:`format_iso`／:func:`iso_day`），
  只有顯示層（PDF、Markdown、主控台、Telegram、``*_zh`` 文字）用 :func:`format_day`／:func:`display_day`。
- 時間戳（含 ``T`` 或時間的字串）與各 provider 的 wire format 完全不碰。

匯入時只用標準函式庫；pandas／numpy 只在 pandas 相關函式內延遲匯入。建議寫法：``import date_format as cd``。

與收集資料工具的差異（只有增加，核心函式的接受／拒絕結果逐一相同，見 shared/tests/test_date_format.py）：
``display_day``／``display_clock`` 可指定空值顯示文字（``empty=``，預設同樣是 ``-``）；``day_before`` 把 NaT
當成 None 一樣拒絕（TypeError）；沒有 ``argv_day``／``cli_accepts_compact``：本程式交給中央更新程式 CLI 的
``--expected-asof`` 與環境變數交接一律維持 ``YYYY-MM-DD``（INVENTORY D5）。
"""
from __future__ import annotations

import argparse
import re
from datetime import date, datetime, timedelta, timezone

__all__ = [
    "parse_day", "to_day", "format_day", "format_iso", "display_day", "format_clock", "same_day", "latest_day",
    "day_before", "calendar_day_of", "iso_day", "iso_day_or_none", "iso_calendar_day", "cli_day", "display_clock",
    "display_text", "find_day_token", "to_timestamp_day", "to_datetime_days", "canonical_date_tokens",
    "DATE_HELP",
]

_ISO = re.compile(r"\A[0-9]{4}-[0-9]{2}-[0-9]{2}\Z")
_COMPACT = re.compile(r"\A[0-9]{8}\Z")
# 台灣 1979 年後沒有日光節約時間，固定 +08:00 與 Asia/Taipei 相同（不依賴 tzdata）。
_TAIPEI = timezone(timedelta(hours=8), "Asia/Taipei")

DATE_HELP = "YYYYMMDD（相容 YYYY-MM-DD）"


# ---------------------------------------------------------------------------------------------------------------
# 核心：與收集資料工具相同
# ---------------------------------------------------------------------------------------------------------------
def parse_day(value: date | str) -> date:
    """日期入口：date、YYYYMMDD、YYYY-MM-DD；其他一律拒絕（型別不對 TypeError，內容不對 ValueError）。"""
    if isinstance(value, datetime):
        raise TypeError("日期不可以是時間戳（datetime）")
    if type(value) is date:
        return value
    if not isinstance(value, str):
        raise TypeError("日期必須是 date 或字串")
    if _COMPACT.fullmatch(value):
        parts = (int(value[0:4]), int(value[4:6]), int(value[6:8]))
    elif _ISO.fullmatch(value):
        parts = (int(value[0:4]), int(value[5:7]), int(value[8:10]))
    else:
        raise ValueError(f"日期格式要是 YYYYMMDD（相容 YYYY-MM-DD），收到 {value!r}")
    try:
        return date(*parts)
    except ValueError:
        raise ValueError(f"不是有效的日曆日期：{value!r}") from None


def format_day(value: date | str) -> str:
    """畫面、報告、使用者看得到的日期：YYYYMMDD。"""
    day = parse_day(value)
    return f"{day.year:04d}{day.month:02d}{day.day:02d}"


def format_clock(value: datetime) -> str:
    """畫面與報告上的「日期 時間」：YYYYMMDD HH:MM:SS（只給人看；機器讀的時間戳不經過這裡）。"""
    if not isinstance(value, datetime):
        raise TypeError("需要 datetime")
    return f"{format_day(value.date())} {value:%H:%M:%S}"


def format_iso(value: date | str) -> str:
    """內部慣例（機器欄位、快取鍵、交給只收 YYYY-MM-DD 的 CLI）用的 YYYY-MM-DD。"""
    return parse_day(value).isoformat()


def to_day(value: object) -> date | None:
    """讀已存資料用：是一個純日期（date 或兩種拼寫的字串）就回 date，否則回 None。

    時間戳、JSON 數字、空值、期間、其他格式都回 None，不猜。
    """
    if isinstance(value, bool) or not isinstance(value, (date, str)):
        return None
    try:
        return parse_day(value)
    except (TypeError, ValueError):
        return None


def _is_empty(value: object) -> bool:
    # 只有 None 與空字串算空值（與收集資料工具相同）；不對 Series／ndarray 做逐元素比較。
    return value is None or (isinstance(value, str) and value == "")


def display_day(value: object, empty: str = "-") -> str:
    """畫面顯示：純日期轉成 YYYYMMDD；時間戳或其他文字原樣顯示。空值顯示 ``empty``。

    注意（與收集資料工具相同，刻意不改）：只有 ``date`` 與兩種拼寫的字串算純日期。``datetime``、``pd.Timestamp``、
    ``numpy.datetime64`` 都是時間戳，會原樣顯示（例如 ``2026-10-02 00:00:00``），不會變成 YYYYMMDD。手上是這類值時，
    先用 :func:`calendar_day_of` 取日曆日再顯示，或用 :func:`display_clock`（日期＋時間）；個股分析程式的
    ``tw_single_stock_3m_analyzer.display_date`` 已經先做這一步。"""
    if _is_empty(value):
        return empty
    day = to_day(value)
    return format_day(day) if day is not None else str(value)


def same_day(left: object, right: object) -> bool:
    """兩個值是不是同一天（兩種拼寫視為相同）；任一邊不是純日期就是 False。

    嚴格（與收集資料工具相同）：``datetime``／``pd.Timestamp``／``numpy.datetime64`` 不是純日期，一律 False，
    即使它落在同一天。要比較時間戳所在的日子，先用 :func:`calendar_day_of` 換成日曆日再比。"""
    first, second = to_day(left), to_day(right)
    return first is not None and first == second


def latest_day(values) -> date | None:
    """一組值（兩種拼寫都可能）中最新的一天；比日期值，不比字串。沒有任何純日期就回 None。"""
    days = [day for day in map(to_day, values) if day is not None]
    return max(days) if days else None


def _is_missing_datetime(value: object) -> bool:
    # pd.NaT 是 datetime 的子類別，而且不等於自己。
    try:
        return value != value  # noqa: PLR0124
    except Exception:  # pragma: no cover - exotic objects
        return False


def _is_numpy_datetime64(value: object) -> bool:
    kind = type(value)
    return kind.__name__ == "datetime64" and kind.__module__ == "numpy"


def calendar_day_of(value: object) -> date | None:
    """純日期，或時間戳所在的那一天；其他回 None。

    - 純日期（date、``YYYYMMDD``、``YYYY-MM-DD``）-> 那一天；
    - ``datetime``／``pd.Timestamp`` -> ``.date()``（不換時區；需要台北日期的呼叫端先換成台北時間）；
      ``numpy.datetime64`` -> 它所在的那一天；
    - ``YYYY-MM-DD`` 開頭、接 ``T`` 或空白、整串是 ``datetime.fromisoformat`` 收得下的時間戳字串 -> 開頭那一天；
    - 其他（JSON 數字、bool、None、NaT、8 位數字開頭的時間戳、其他格式）-> None。
    """
    if isinstance(value, datetime):
        if _is_missing_datetime(value):
            return None
        return value.date()
    if _is_numpy_datetime64(value):
        import numpy as np

        if np.isnat(value):
            return None
        item = value.astype("datetime64[D]").item()
        return item if type(item) is date else None
    day = to_day(value)
    if day is not None or not isinstance(value, str) or len(value) <= 10 or value[10] not in "T ":
        return day
    if not _ISO.fullmatch(value[:10]):
        return None
    try:
        datetime.fromisoformat(value)
    except ValueError:
        return None
    return to_day(value[:10])


def day_before(day: date | str, cutoff: date | str) -> bool:
    """``day`` 是否早於 ``cutoff``。兩邊都能換成日曆日就比日期值（時間戳取它所在的那天），不比字串；
    其中一邊是什麼日期都不是的字串才退回原本的字串比較。None、NaT、數字等不是日期也不是字串的值一律 TypeError
    （NaT 是 datetime 的子類別，但它不是任何一天，不可拿 ``"NaT"`` 去比字串）。"""
    for value in (day, cutoff):
        if not isinstance(value, (date, str)) or (isinstance(value, datetime) and _is_missing_datetime(value)):
            raise TypeError(f"日期邊界必須是 date 或字串，收到 {type(value).__name__}")
    first, second = calendar_day_of(day), calendar_day_of(cutoff)
    if first is not None and second is not None:
        return first < second
    return str(day) < str(cutoff)


# ---------------------------------------------------------------------------------------------------------------
# 本程式的補充
# ---------------------------------------------------------------------------------------------------------------
def iso_day(value: date | str) -> str:
    """讀取端：兩種拼寫（或 date）-> 正規 ``YYYY-MM-DD``；其他拒絕（同 :func:`parse_day`）。"""
    return format_iso(value)


def iso_day_or_none(value: object) -> str | None:
    """讀取端（寬鬆）：純日期 -> ``YYYY-MM-DD``，否則 None。"""
    day = to_day(value)
    return None if day is None else day.isoformat()


def iso_calendar_day(value: object) -> str | None:
    """讀取端（欄位可能是日期也可能是時間戳）：:func:`calendar_day_of` -> ``YYYY-MM-DD``，否則 None。"""
    day = calendar_day_of(value)
    return None if day is None else day.isoformat()


def cli_day(text: str) -> str:
    """argparse ``type=``：收 YYYYMMDD（相容 YYYY-MM-DD），回傳正規 ``YYYY-MM-DD``（後續程式不用改）。"""
    try:
        return iso_day(text)
    except (TypeError, ValueError):
        raise argparse.ArgumentTypeError(f"日期格式要是 {DATE_HELP}：{text!r}") from None


def display_clock(value: object, empty: str = "-") -> str:
    """畫面顯示「日期 時間」：datetime 或 ISO 時間戳字串 -> ``YYYYMMDD HH:MM:SS``（有時區的換成台北時間）；
    純日期 -> ``YYYYMMDD``；其他文字原樣；空值（None、""、NaT）-> ``empty``。"""
    if _is_empty(value):
        return empty
    if isinstance(value, datetime):
        if _is_missing_datetime(value):
            return empty
        if value.tzinfo is not None and value.utcoffset() is not None:
            value = value.astimezone(_TAIPEI)
        return format_clock(value)
    day = to_day(value)
    if day is not None:
        return format_day(day)
    if isinstance(value, str) and calendar_day_of(value) is not None:
        stamp = datetime.fromisoformat(value)
        if stamp.tzinfo is not None and stamp.utcoffset() is not None:
            stamp = stamp.astimezone(_TAIPEI)
        return format_clock(stamp)
    return str(value)


# 自由文字裡「獨立」的 ISO 日期：前面不是英數、底線、斜線、反斜線、減號、單一個點（``a..b`` 區間的兩個點可以）；
# 後面不是英數、底線、斜線、反斜線、減號，不是副檔名（.xxx），也不是時間（T08、 08:00、:00）。
_TEXT_ISO = re.compile(
    r"(?<![0-9A-Za-z_/\\\-])(?<![^.]\.)(?<!^\.)([0-9]{4})-([0-9]{2})-([0-9]{2})"
    r"(?![0-9A-Za-z_/\\\-])(?!\.[0-9A-Za-z])(?!:[0-9])(?! [0-9]{1,2}:[0-9]{2})"
)
_URL_OR_PATH = re.compile(r"(?:[A-Za-z][A-Za-z0-9+.\-]*://|\A[A-Za-z]:[\\/]|\\)")


def display_text(text: object) -> object:
    """顯示用（D7）：把自由文字裡獨立、有效的 ISO 日曆日期改成 YYYYMMDD；時間戳、與數字相連的字、網址、
    檔案路徑都不動。非字串原樣回傳。只用在給人看的地方，機器欄位不經過這裡。"""
    if not isinstance(text, str) or "-" not in text:
        return text

    def _replace(match: re.Match) -> str:
        start = match.start()
        word_start = max(text.rfind(" ", 0, start), text.rfind("\n", 0, start), text.rfind("\t", 0, start)) + 1
        word_end_candidates = [pos for pos in (text.find(" ", match.end()), text.find("\n", match.end()),
                                               text.find("\t", match.end())) if pos >= 0]
        word_end = min(word_end_candidates) if word_end_candidates else len(text)
        if _URL_OR_PATH.search(text[word_start:word_end]):
            return match.group(0)
        day = to_day(match.group(0))
        return match.group(0) if day is None else format_day(day)

    return _TEXT_ISO.sub(_replace, text)


# 契約文字裡「獨立」的日期記號：前後都不可以是英數或底線（``rev20260814``、``20250101A`` 不算）。
# ISO 日期後面可以直接接 ``T時:分``（時間戳的日期部分，例如 ``2026-08-14T23:59:59+08:00``）。
# 8 位數字前後另外不可以是減號（``20250101-A`` 這類編號不算日期）。
_TOKEN_ISO = re.compile(r"(?<![0-9A-Za-z_])([0-9]{4}-[0-9]{2}-[0-9]{2})(?:(?=T[0-9]{2}:[0-9]{2})|(?![0-9A-Za-z_]))")
_TOKEN_COMPACT = re.compile(r"(?<![0-9A-Za-z_\-])([0-9]{8})(?![0-9A-Za-z_\-])")


def find_day_token(text: object) -> date | None:
    """契約文字（例如 ``expected_asof_rule``）裡的日期：先找第一個獨立、有效的 ISO 日期；整段都沒有 ISO 日期時，
    才取第一個獨立、有效的 8 位數字日期。都沒有就回 None。

    ISO 優先：改版前只認 ISO（``(\\d{4})-(\\d{2})-(\\d{2})`` 的第一個符合處），所以一般的契約文字（裡面有一個
    獨立的 ISO 日期）結果與以前相同；文字裡另外出現的編號或版本號（``rule 20260814 rev``）不會搶走它（日期審查 C4）。"""
    if not isinstance(text, str):
        return None
    for pattern in (_TOKEN_ISO, _TOKEN_COMPACT):
        for match in pattern.finditer(text):
            day = to_day(match.group(1))
            if day is not None:
                return day
    return None


# ---------------------------------------------------------------------------------------------------------------
# pandas 相關（延遲匯入）
# ---------------------------------------------------------------------------------------------------------------
def _is_missing_scalar(value: object) -> bool:
    if value is None:
        return True
    if isinstance(value, str):
        return value == ""
    if isinstance(value, bool):
        return False
    if isinstance(value, float):
        return value != value
    if isinstance(value, datetime):
        return _is_missing_datetime(value)
    kind = type(value)
    if kind.__name__ in ("NaTType", "NAType"):
        return True
    if _is_numpy_datetime64(value):
        import numpy as np

        return bool(np.isnat(value))
    if kind.__module__ == "numpy" and kind.__name__.startswith("float"):
        return bool(value != value)  # float16／float32 的 NaN（float64 已是 float 的子類別）
    return False


def _is_number(value: object) -> bool:
    if isinstance(value, (bool, int, float)):
        return True
    kind = type(value)
    return kind.__module__ == "numpy" and kind.__name__.startswith(("int", "uint", "float", "bool"))


def _timestamp_of(value: object, pd):
    """一個值 -> 午夜的 naive Timestamp（日曆日）。不是日期就 ValueError。"""
    day = calendar_day_of(value)
    if day is None:
        raise ValueError(f"日期格式要是 YYYYMMDD（相容 YYYY-MM-DD），收到 {value!r}")
    return pd.Timestamp(day)


# datetime64[ns] 能表示的日曆日（午夜）範圍；超出時 numpy 的單位換算會靜默溢位成別的日子（例如 00010101 變成
# 1754-08-30），所以寫進 datetime64[ns] 陣列之前一律先檢查。
_NS_FIRST_DAY = date(1677, 9, 22)
_NS_LAST_DAY = date(2262, 4, 11)


def _ns_value(value: object, pd):
    stamp = _timestamp_of(value, pd)
    if not _NS_FIRST_DAY <= stamp.date() <= _NS_LAST_DAY:
        raise ValueError(f"日期超出 datetime64[ns] 可表示的範圍（16770922～22620411），收到 {value!r}")
    return stamp.as_unit("ns").to_datetime64()


def to_timestamp_day(value: object):
    """一個值 -> 它所在日曆日午夜的 naive ``pd.Timestamp``；None／""／NaN／NaT -> ``pd.NaT``；
    JSON 數字、bool、其他格式 -> ValueError（不會變成 1970 年）。"""
    import pandas as pd

    if _is_missing_scalar(value):
        return pd.NaT
    return _timestamp_of(value, pd)


def _day_values(values, errors: str):
    """物件陣列 -> datetime64[ns] 陣列（逐一日曆日；以唯一值對照，百萬列也只解析一次）。"""
    import numpy as np
    import pandas as pd

    codes, uniques = pd.factorize(np.asarray(values, dtype=object), use_na_sentinel=True)
    parsed = np.empty(len(uniques), dtype="datetime64[ns]")
    for position, value in enumerate(uniques):
        if _is_missing_scalar(value):
            parsed[position] = np.datetime64("NaT", "ns")
            continue
        try:
            parsed[position] = _ns_value(value, pd)
        except (TypeError, ValueError, OverflowError):
            if errors == "coerce":
                parsed[position] = np.datetime64("NaT", "ns")
                continue
            raise ValueError(f"日期格式要是 YYYYMMDD（相容 YYYY-MM-DD），收到 {value!r}") from None
    out = np.full(len(codes), np.datetime64("NaT", "ns"), dtype="datetime64[ns]")
    present = codes >= 0
    if len(parsed):
        out[present] = parsed[codes[present]]
    return out


def to_datetime_days(values, *, errors: str = "raise"):
    """逐元素的日曆日（兩種拼寫都收），取代 ``pd.to_datetime(<日期欄>, errors="coerce")``。

    - Series 進 -> Series 出（同 index、同 name）；其他（list、Index、ndarray）-> ``DatetimeIndex``（Index 保留 name）。
    - 已經是 datetime64（含有時區）的輸入原樣傳回（Series 原物件；其他包成 DatetimeIndex），呼叫端自己處理時區。
    - 文字／物件：缺值（None、NaN、NaT、""）-> NaT；有效日期 -> 午夜；無效值（含 JSON 數字、bool、其他格式）
      -> ValueError，``errors="coerce"`` 時才變 NaT（只用在原本就把日期當選填的地方）。
    """
    if errors not in ("raise", "coerce"):
        raise ValueError("errors 只能是 'raise' 或 'coerce'")
    import pandas as pd

    if isinstance(values, pd.Series):
        if pd.api.types.is_datetime64_any_dtype(values.dtype):
            return values
        return pd.Series(_day_values(values.to_numpy(dtype=object), errors), index=values.index, name=values.name)
    if isinstance(values, pd.Index):
        if pd.api.types.is_datetime64_any_dtype(values.dtype):
            return values if isinstance(values, pd.DatetimeIndex) else pd.DatetimeIndex(values)
        return pd.DatetimeIndex(_day_values(values.to_numpy(dtype=object), errors), name=values.name)
    if hasattr(values, "dtype") and pd.api.types.is_datetime64_any_dtype(getattr(values, "dtype", None)):
        return pd.DatetimeIndex(values)
    if isinstance(values, (str, bytes)) or not hasattr(values, "__iter__"):
        raise TypeError("to_datetime_days 需要一串值（list／Index／Series／ndarray）；單一值用 to_timestamp_day")
    import numpy as np

    if isinstance(values, np.ndarray):
        if values.ndim != 1:
            raise TypeError("to_datetime_days 只收一維的值")
        return pd.DatetimeIndex(_day_values(values.astype(object, copy=False), errors))
    return pd.DatetimeIndex(_day_values(pd.Series(list(values), dtype=object).to_numpy(), errors))


def canonical_date_tokens(values):
    """時間戳型欄位（``available_at``、``published_at``…）裡的純日期字串（兩種拼寫）改寫成 ``YYYY-MM-DD``，
    其他值（時間戳、空值、其他文字）原樣保留，之後照原本的 ``pd.to_datetime`` 呼叫解析。

    JSON 數字（int、float、bool、numpy 數字）不是日期也不是時間戳：換成一段一定解析不了的文字
    （``<not a date: 20261002>``），讓原本的 ``pd.to_datetime`` 依它自己的 ``errors=`` 報錯或得到 NaT，
    不會被當成 1970 年的時間點（D11）。

    Series 進 -> Series 出（同 index、同 name）；Index 進 -> Index 出；其他 -> list。只改寫拿去解析的副本，
    不改原物件。"""
    import numpy as np
    import pandas as pd

    def _token(item):
        if isinstance(item, str):
            return iso_day_or_none(item)
        if _is_number(item):
            return f"<not a date: {item!r}>"
        return None

    def _map(items):
        array = np.asarray(items, dtype=object)
        codes, uniques = pd.factorize(array, use_na_sentinel=True)
        replaced = np.array([_token(item) for item in uniques], dtype=object)
        out = array.copy()
        if len(uniques):
            changed = np.array([item is not None for item in replaced], dtype=bool)
            rows = (codes >= 0) & changed[codes]  # codes == -1（缺值）會讀到最後一個，先用 codes >= 0 排除
            out[rows] = replaced[codes[rows]]
        return out

    if isinstance(values, pd.Series):
        if pd.api.types.is_datetime64_any_dtype(values.dtype):
            return values
        return pd.Series(_map(values.to_numpy(dtype=object)), index=values.index, name=values.name, dtype=object)
    if isinstance(values, pd.Index):
        if pd.api.types.is_datetime64_any_dtype(values.dtype):
            return values
        return pd.Index(_map(values.to_numpy(dtype=object)), name=values.name, dtype=object)
    return list(_map(list(values)))
