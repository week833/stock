"""開盤前版 H1 快照的 Top10 消費端（唯讀）；C0 保留為備援。

只透過共用唯讀 API ``stock_daily_preopen_reader`` 讀取快照（以 Top10 私有模組實例載入，
不改共用模組）。新鮮度以本次 C0 輸入的台股最新交易日（as-of）為 expected_signal_date；
快照可用且新鮮時 H1 顯示開盤前版，否則依 model source 回退 C0 或失敗。
必要揭露、閘門與「開盤買進」文字依快照 manifest 的 recipe.name 從揭露表選取（沒有 recipe 欄位視為配方 v1）。
"""
from __future__ import annotations

from dataclasses import dataclass, field, replace
import importlib.util
import math
import os
from pathlib import Path
import sys

import numpy as np
import pandas as pd

from date_contract import iso_day

DEFAULT_READER_MODULE = Path(r"D:\stock\programs\shared\stock_daily_preopen_reader.py")
READER_MODULE_NAME = "top10_shared_preopen_reader"
MODEL_SOURCES = ("auto", "preopen", "c0")
MODEL_SOURCE_ENV = "TOP10_MODEL_SOURCE"
PREOPEN_ROOT_ENV = "STOCK_DAILY_PREOPEN_ROOT"
PREOPEN_RANKING_STATUS = "preopen_model_ranking"
PREOPEN_RANKING_POLICY = "preopen_tradable_top10_p_up_then_exp_ret"
PREOPEN_RANKING_NOTE = "可交易母體依收漲機率→預估報酬排序取前十；命中率指收盤報價方向，不代表交易獲利"
PREOPEN_DIRECTION_KIND = "preopen_hgb_classifier_calibrated"
PREOPEN_RETURN_KIND = "preopen_hgb_regressor_calibrated"
PREOPEN_CALIBRATION_STATUS = "開盤前版校準後機率（保留期驗證資料見必要揭露；前瞻可靠度未證）"
PREOPEN_TARGET_DEFINITION = ("開盤前版：p_up = P(調整後收盤[目標日] > 調整後收盤[訊號日])（校準後）；"
                             "exp_ret = 校準後預估收盤對收盤報酬；兩者都不是開盤買進可得的報酬")
TOP_N = 10
CALIBRATION_REPORT = Path(r"D:\TWStockData\program_data\stock_daily_c0\candidates\calibration_20261018"
                          r"\report\校正報告_20261018.md")
DISCLOSURE_TITLE = "必要揭露（開盤前版 H1）"
DISCLOSURE = (
    "開盤前版預測的是「下一交易日收盤相對訊號日收盤」的方向（調整後收盤），不是開盤價、盤中價或可成交報酬的預測。",
    "保留期（2026-01～10，模型未看過的資料）方向準確率 59.0%（一律猜跌為 55.9%），AUC 0.608。",
    "只在前一晚美股收漲日選出的可交易 Top10 命中率 63.5%；不分日子全部計算為 55.6%。",
    "若在隔日開盤買進，Top10 平均報酬為負（約 −0.04%／日，未扣交易成本）；命中率不代表交易獲利。",
    "可交易母體與驗證時相同：20 日均成交金額 ≥ 2,000 萬元且訊號日收盤 ≥ 10 元。",
)
DISCLOSURE_SOURCE = "資料出處：" + str(CALIBRATION_REPORT)
GATE = {
    True: ("recommended_day", "建議日：前一晚美股收漲（保留期此條件下 Top10 命中 63.5%）"),
    False: ("not_recommended_day", "非建議日：歷史 Top10 命中僅約 55%（前一晚美股未收漲）"),
    None: ("unknown", "前一晚美股漲跌未知：無法判定是否為建議日"),
}
# 配方別揭露（2026-10-21）：共用產製端可由配方 v1「h1pre_final_nogc_xs」切換為 v2「h1pre_v2_ens」。
# 快照 latest.json 的 recipe.name 決定必要揭露、閘門文字與「開盤買進」效果片語（disclosure_entry）。
# 沒有 recipe 欄位（或為 null）的舊快照一律視為 v1：上方 DISCLOSURE／DISCLOSURE_SOURCE／GATE 就是 v1 原文，未更動。
# 不認得的名稱（含格式不符）改用中性文字並顯示名稱；閘門狀態碼不因配方改變。
RECIPE_V1 = "h1pre_final_nogc_xs"
RECIPE_V2 = "h1pre_v2_ens"
RECIPE_UNKNOWN = "unknown"
H1_IMPROVE_REPORT = Path(r"D:\TWStockData\program_data\stock_daily_c0\candidates\h1_improve_20261021"
                         r"\report\H1改良研究報告_20261021.md")
DISCLOSURE_V2 = (
    "開盤前版預測的是「下一交易日收盤相對訊號日收盤」的方向（調整後收盤），不是開盤價、盤中價或可成交報酬的預測。",
    "保留期（2026-01-02～2026-10-19，191 個交易日，模型未看過的資料）方向準確率 60.0%（一律猜跌為 56.1%），AUC 0.619。",
    "只在前一晚美股收漲日選出的可交易 Top10 命中率 65.3%；其他夜為 49.1%；不分日子全部計算為 57.1%。",
    "若在隔日開盤買進，Top10 平均報酬約為零（約 +0.01%／日，未扣交易成本），不是交易獲利的證據；命中率不代表交易獲利。",
    "Top10 預估報酬的平均絕對誤差（MAE）為 2.71%。",
    "可交易母體與驗證時相同：20 日均成交金額 ≥ 2,000 萬元且訊號日收盤 ≥ 10 元。",
)
DISCLOSURE_V2_SOURCE = "資料出處：" + str(H1_IMPROVE_REPORT)
GATE_V2 = {
    True: ("recommended_day", "建議日：前一晚美股收漲（保留期此條件下 Top10 命中 65.3%）"),
    False: ("not_recommended_day", "非建議日：保留期此條件下 Top10 命中約 49.1%（前一晚美股未收漲）"),
    None: ("unknown", "前一晚美股漲跌未知：無法判定是否為建議日"),
}
DISCLOSURE_UNKNOWN = (
    "開盤前版預測的是「下一交易日收盤相對訊號日收盤」的方向（調整後收盤），不是開盤價、盤中價或可成交報酬的預測。",
    "快照的模型配方為「{name}」，不在本程式已知的配方清單（h1pre_final_nogc_xs、h1pre_v2_ens）內："
    "本程式沒有此配方的保留期驗證數字，方向準確率、AUC 與 Top10 命中率皆未知。",
    "隔日開盤買進 Top10 的報酬未知；命中率不代表交易獲利。",
    "請先確認產製端配方與對應的驗證報告，再參考本報告的排序。",
)
DISCLOSURE_UNKNOWN_SOURCE = "資料出處：無（配方「{name}」沒有對應的驗證報告）"
GATE_UNKNOWN = {
    True: ("recommended_day", "建議日：前一晚美股收漲（配方未知：此條件下的保留期命中率未驗證）"),
    False: ("not_recommended_day", "非建議日：前一晚美股未收漲（配方未知：此條件下的保留期命中率未驗證）"),
    None: ("unknown", "前一晚美股漲跌未知：無法判定是否為建議日"),
}
RECIPE_DISCLOSURES = {
    RECIPE_V1: {"lines": DISCLOSURE, "source": DISCLOSURE_SOURCE, "gate": GATE,
                "trading_effect": "開盤買進 Top10 保留期平均為負"},
    RECIPE_V2: {"lines": DISCLOSURE_V2, "source": DISCLOSURE_V2_SOURCE, "gate": GATE_V2,
                "trading_effect": "開盤買進 Top10 保留期平均約為零（約 +0.01%／日）"},
}
TRADING_EFFECT_UNKNOWN = "開盤買進 Top10 保留期報酬未知（配方未知）"
RECIPE_NAME_SHOWN_MAX = 80
REASON_TEXT = {
    "model_source_forced_c0": "依設定強制使用 C0（model source = c0），未讀取開盤前版",
    "preopen_no_latest": "找不到開盤前版 latest.json（可能尚未產出）",
    "preopen_schema_mismatch": "開盤前版 latest.json 格式版本不符",
    "preopen_stale": "開盤前版快照的訊號日不是本次台股最新交易日",
    "preopen_snapshot_missing": "開盤前版快照檔不存在",
    "preopen_snapshot_sha_mismatch": "開盤前版快照檔雜湊與 latest.json 不符",
    "preopen_snapshot_columns": "開盤前版快照缺少必要欄位",
    "preopen_snapshot_values": "開盤前版快照數值無效",
    "preopen_us_status_invalid": "開盤前版的美股狀態欄位無效",
    "preopen_reader_unavailable": "開盤前版唯讀讀取模組不存在",
    "preopen_parquet_engine_missing": "本環境缺少可讀取 parquet 的元件",
    "preopen_reader_error": "讀取開盤前版快照時發生未預期錯誤",
    "preopen_no_tradable": "開盤前版快照沒有可交易股票",
    "preopen_duplicate_stock": "開盤前版快照的市場與股票代碼組合重複",
    "preopen_snapshot_market": "開盤前版快照的市場代碼無效",
    "preopen_snapshot_dates": "開盤前版快照逐列日期無效或與宣告日期不同",
    "preopen_rank_inconsistent": "開盤前版快照的可交易名次與排序不一致",
    "preopen_target_mismatch": "開盤前版快照的目標日與本次已驗證的下一交易日不同",
}
_SORT_KEYS = ["p_up", "exp_ret", "market", "stock_id"]
_SORT_ASCENDING = [False, False, True, True]
_READER_CACHE = {}


class PreopenRequiredError(RuntimeError):
    """model source = preopen 但開盤前版不可用。"""

    def __init__(self, reason, detail=""):
        super().__init__("affected_output:preopen_required_unavailable:" + str(reason) + (": " + str(detail) if detail else ""))
        self.reason = reason
        self.detail = detail


def resolve_model_source(value=None, environ=None):
    """命令列優先，其次環境變數 TOP10_MODEL_SOURCE，預設 auto；回傳 (source, origin)。"""
    environ = os.environ if environ is None else environ
    if value is not None and str(value).strip():
        source, origin = str(value).strip().lower(), "cli"
    elif str(environ.get(MODEL_SOURCE_ENV) or "").strip():
        source, origin = str(environ[MODEL_SOURCE_ENV]).strip().lower(), "env"
    else:
        source, origin = "auto", "default"
    if source not in MODEL_SOURCES:
        raise ValueError("model source 必須是 auto、preopen 或 c0；收到：" + str(source))
    return source, origin


def reason_text(reason):
    return REASON_TEXT.get(str(reason), "其他原因（原始紀錄保留）")


def recipe_name(meta):
    """快照 manifest 宣告的配方名稱：沒有 recipe 欄位（或為 null）回傳 None；格式不符或沒有名稱回傳 ""。"""
    recipe = (meta or {}).get("recipe")
    if recipe is None:
        return None
    name = recipe.get("name") if isinstance(recipe, dict) else None
    return str(name).strip() if name is not None else ""


def disclosure_entry(meta=None):
    """揭露表項目：dict(recipe, entry, lines, source, gate, trading_effect)。

    recipe 為快照宣告的名稱（沒有 recipe 欄位時為 None）；entry 為採用的表項目（配方名稱或 "unknown"）。
    沒有 recipe 欄位 → v1（今日原文）；已知名稱 → 該配方；其他 → 中性文字並顯示名稱。
    """
    declared = recipe_name(meta)
    key = RECIPE_V1 if declared is None else declared
    if key in RECIPE_DISCLOSURES:
        return dict(RECIPE_DISCLOSURES[key], recipe=declared, entry=key)
    shown = (declared or "（未提供名稱）")[:RECIPE_NAME_SHOWN_MAX]
    return {"recipe": declared, "entry": RECIPE_UNKNOWN,
            "lines": tuple(line.format(name=shown) for line in DISCLOSURE_UNKNOWN),
            "source": DISCLOSURE_UNKNOWN_SOURCE.format(name=shown), "gate": GATE_UNKNOWN,
            "trading_effect": TRADING_EFFECT_UNKNOWN}


def disclosure_record(meta=None):
    """報告的 model_disclosure：title／lines／source 同前，另帶 recipe、recipe_entry 與 trading_effect（報告端共用片語）。"""
    entry = disclosure_entry(meta)
    return {"title": DISCLOSURE_TITLE, "lines": list(entry["lines"]), "source": entry["source"],
            "recipe": entry["recipe"], "recipe_entry": entry["entry"], "trading_effect": entry["trading_effect"]}


def gate_record(gate_us_up, meta=None):
    """前一晚美股閘門；狀態碼不因配方改變，文字依快照配方（meta 省略時為 v1 原文）。"""
    value = gate_us_up if gate_us_up in (True, False) else None
    code, label = disclosure_entry(meta)["gate"][value]
    return {"gate_us_up": value, "code": code, "label": label, "recommended": value is True}


# ---------------------------------------------------------------- reader loading


def _parquet_engine():
    for name in ("pyarrow", "fastparquet"):
        if importlib.util.find_spec(name) is not None:
            return name
    return "duckdb" if importlib.util.find_spec("duckdb") is not None else None


class _DuckdbParquetPandas:
    """只供 Top10 私有讀取模組使用的 pandas 代理：本 .venv 沒有 pyarrow／fastparquet 時，
    以既有相依 duckdb 解碼 parquet；其餘屬性一律轉交 pandas。雜湊、欄位與數值驗證仍由共用 reader 執行。"""

    engine = "duckdb"

    def __getattr__(self, name):
        return getattr(pd, name)

    @staticmethod
    def read_parquet(path, **_):
        import duckdb

        connection = duckdb.connect()
        try:
            return connection.execute("SELECT * FROM read_parquet(?)", [str(path)]).df()
        finally:
            connection.close()


def load_reader(path=None):
    """以私有名稱載入共用唯讀 reader（不影響其他程式的同名模組）。"""
    path = Path(path or DEFAULT_READER_MODULE).resolve()
    if not path.is_file():
        raise FileNotFoundError(str(path))
    info = path.stat()
    key = (str(path), info.st_size, info.st_mtime_ns)
    if key in _READER_CACHE:
        return _READER_CACHE[key]
    spec = importlib.util.spec_from_file_location(READER_MODULE_NAME, path)
    module = importlib.util.module_from_spec(spec)
    previous = sys.modules.get(READER_MODULE_NAME)
    sys.modules[READER_MODULE_NAME] = module
    try:
        spec.loader.exec_module(module)
    except BaseException:
        if previous is None:
            sys.modules.pop(READER_MODULE_NAME, None)
        else:
            sys.modules[READER_MODULE_NAME] = previous
        raise
    _READER_CACHE[key] = module
    return module


def _use_engine(reader, engine):
    if engine == "duckdb":
        reader.pd = _DuckdbParquetPandas()
    elif isinstance(getattr(reader, "pd", None), _DuckdbParquetPandas):
        reader.pd = pd


def prepared_reader(path=None):
    """回傳 (reader, engine)：已依本環境可用的 parquet 元件設定好的私有 reader；engine 為 None 表示無法讀 parquet。"""
    reader = load_reader(path)
    engine = _parquet_engine()
    if engine is not None:
        _use_engine(reader, engine)
    return reader, engine


# ---------------------------------------------------------------- choice


@dataclass(frozen=True)
class PreopenChoice:
    requested: str
    origin: str
    model_source: str
    status: str
    reason: str | None = None
    detail: str | None = None
    expected_signal_date: str | None = None
    root: str | None = None
    engine: str | None = None
    snapshot: object = field(default=None, repr=False, compare=False)
    describe_text: str | None = None

    @property
    def active(self):
        return self.model_source == "preopen" and self.snapshot is not None

    @property
    def meta(self):
        return dict(self.snapshot.meta) if self.snapshot is not None else None

    def status_record(self):
        return {"requested_model_source": self.requested, "model_source_origin": self.origin,
                "model_source": self.model_source, "status": self.status, "reason": self.reason,
                "reason_text": reason_text(self.reason) if self.reason else None, "detail": self.detail,
                "expected_signal_date": self.expected_signal_date, "root": self.root,
                "root_override": os.environ.get(PREOPEN_ROOT_ENV) or None, "parquet_engine": self.engine,
                "snapshot_read_format": self.snapshot.frame.attrs.get("snapshot_read_format")
                if self.snapshot is not None else None}

    def preopen_meta(self):
        if not self.active:
            return None
        meta, frame = self.snapshot.meta, self.snapshot.frame
        keep = ("run_id", "model_sha256", "snapshot_sha256", "model_id", "signal_date", "target_date",
                "target_date_source", "snapshot", "snapshot_schema", "schema", "rows", "created_at",
                "us_overnight", "gate_us_up", "central_head_sha256", "fixture", "recipe")
        result = {key: meta.get(key) for key in keep if key in meta}
        result.update(describe=self.describe_text, root=self.root, parquet_engine=self.engine,
                      snapshot_read_format=frame.attrs.get("snapshot_read_format"),
                      tradable_rows=int(frame["tradable"].sum()), frame_rows=int(len(frame)),
                      model_label=_reader_label())
        return result

    def note(self):
        """報告醒目位置的一行說明。"""
        if self.active:
            return "H1 模型來源：開盤前版（C0 保留為備援）｜" + str(self.describe_text)
        if self.status == "not_requested":
            return "H1 模型來源：C0（" + reason_text(self.reason) + "）"
        return ("開盤前版不可用（" + str(self.reason) + "：" + reason_text(self.reason) +
                "），本次 H1 改用 C0 基準")


def _reader_label():
    module = sys.modules.get(READER_MODULE_NAME)
    return getattr(module, "MODEL_LABEL", None)


def _unavailable(requested, origin, expected, reason, detail, *, root=None, engine=None):
    detail = None if detail in (None, "") else str(detail)[:500]
    if requested == "preopen":
        raise PreopenRequiredError(reason, detail or "")
    return PreopenChoice(requested=requested, origin=origin, model_source="c0", status="unavailable",
                         reason=reason, detail=detail, expected_signal_date=expected, root=root, engine=engine)


def choose(requested, origin, expected_signal_date, *, root=None, reader_path=None):
    """依開關決定本次 H1 來源；auto 回退 C0，preopen 不可用即丟 PreopenRequiredError。"""
    if requested not in MODEL_SOURCES:
        raise ValueError("model source 無效：" + str(requested))
    expected = iso_day(expected_signal_date)
    if requested == "c0":
        return PreopenChoice(requested=requested, origin=origin, model_source="c0", status="not_requested",
                             reason="model_source_forced_c0", expected_signal_date=expected)
    try:
        reader, engine = prepared_reader(reader_path)
    except (OSError, ImportError, SyntaxError) as exc:
        return _unavailable(requested, origin, expected, "preopen_reader_unavailable", f"{type(exc).__name__}: {exc}")
    base = str(reader.root_dir(root))
    try:
        try:
            snapshot = reader.load_latest(root=root, expected_signal_date=expected)
        except ImportError as exc:
            if engine == "duckdb" or importlib.util.find_spec("duckdb") is None:
                raise reader.PreopenUnavailable("preopen_parquet_engine_missing", str(exc)) from exc
            engine = "duckdb"
            _use_engine(reader, engine)
            snapshot = reader.load_latest(root=root, expected_signal_date=expected)
        validate_snapshot(reader, snapshot)
        if snapshot.frame.attrs.get("snapshot_read_format") == "csv":
            engine = None
    except reader.PreopenUnavailable as exc:
        return _unavailable(requested, origin, expected, exc.reason, exc.detail, root=base, engine=engine)
    except Exception as exc:  # noqa: BLE001 - 任何讀取異常在 auto 下都回退 C0，原因保留
        return _unavailable(requested, origin, expected, "preopen_reader_error", f"{type(exc).__name__}: {exc}",
                            root=base, engine=engine)
    return PreopenChoice(requested=requested, origin=origin, model_source="preopen", status="available",
                         expected_signal_date=expected, root=base, engine=engine, snapshot=snapshot,
                         describe_text=reader.describe(snapshot.meta))


def validate_snapshot(reader, snapshot):
    """消費端額外檢查：市場股票鍵、逐列日期、機率範圍、有可交易股票及 Top10 名次。"""
    frame = snapshot.frame
    identities = pd.DataFrame({"market": frame["market"].astype(str).str.upper(),
                               "stock_id": frame["stock_id"].astype(str)})
    unknown_markets = sorted(set(identities.loc[~identities["market"].isin(("TWSE", "TPEX")), "market"]))
    if unknown_markets:
        raise reader.PreopenUnavailable("preopen_snapshot_market", ",".join(unknown_markets)[:200])
    duplicates = identities.loc[identities.duplicated(["market", "stock_id"])]
    if not duplicates.empty:
        detail = ",".join(market + "/" + stock for market, stock in duplicates.itertuples(index=False, name=None))
        raise reader.PreopenUnavailable("preopen_duplicate_stock", detail[:200])
    for column in ("signal_date", "target_date"):
        try:
            expected = iso_day(snapshot.meta[column])
            dates = frame[column].map(iso_day)
        except (KeyError, TypeError, ValueError) as exc:
            raise reader.PreopenUnavailable("preopen_snapshot_dates", column + " 格式無效") from exc
        if not dates.eq(expected).all():
            raise reader.PreopenUnavailable("preopen_snapshot_dates",
                                            column + " 必須全部等於 manifest " + expected)
    values = frame[["p_up", "exp_ret"]].apply(pd.to_numeric, errors="coerce")
    if not np.isfinite(values.to_numpy(dtype=float)).all() or not values["p_up"].between(0, 1).all():
        raise reader.PreopenUnavailable("preopen_snapshot_values", "p_up 必須介於 0～1 且數值有限")
    if not bool(frame["tradable"].any()):
        raise reader.PreopenUnavailable("preopen_no_tradable", "tradable 全為 False")
    top = snapshot.top(TOP_N)
    ranks = pd.to_numeric(top["rank_tradable"], errors="coerce").tolist()
    if ranks != [float(index) for index in range(1, len(top) + 1)]:
        raise reader.PreopenUnavailable("preopen_rank_inconsistent", "top=" + ",".join(top["stock_id"].astype(str)))


def confirm_target(choice, c0_target):
    """已驗證的 C0 下一交易日存在時，快照目標日必須一致；否則依開關回退或失敗。"""
    if not choice.active or c0_target is None:
        return choice
    expected = iso_day(c0_target)
    actual = str(choice.snapshot.meta.get("target_date") or "")[:10]
    if actual == expected:
        return choice
    detail = "snapshot target_date " + actual + " != C0 verified next session " + expected
    if choice.requested == "preopen":
        raise PreopenRequiredError("preopen_target_mismatch", detail)
    return replace(choice, model_source="c0", status="unavailable", reason="preopen_target_mismatch",
                   detail=detail, snapshot=None, describe_text=None)


# ---------------------------------------------------------------- report rows


def build_view(snapshot, c0_predictions, universe, c0_coverage, *, engine_id):
    """開盤前版的展示列與覆蓋表。

    順序：可交易母體依 p_up→exp_ret（同分再依市場、代碼）前 10 名，其餘股票依同排序列在後面。
    每列保留 C0 數值作為追溯；快照外可用 C0 列另附於末尾，不參與開盤前 Top10。
    """
    meta, frame = snapshot.meta, snapshot.frame.copy()
    frame["stock_id"] = frame["stock_id"].astype(str)
    frame["market"] = frame["market"].astype(str).str.upper()
    top = snapshot.top(TOP_N)
    top_keys = list(zip(top["market"].astype(str).str.upper(), top["stock_id"].astype(str)))
    frame_keys = pd.MultiIndex.from_frame(frame[["market", "stock_id"]])
    rest = frame.loc[~frame_keys.isin(top_keys)].sort_values(_SORT_KEYS, ascending=_SORT_ASCENDING, kind="mergesort")
    ordered = pd.concat([frame.set_index(["market", "stock_id"]).loc[top_keys].reset_index(), rest], ignore_index=True)
    names, c0_markets = {}, {}
    for row in universe.itertuples():
        key = (str(row.market).upper(), str(row.stock_id))
        names.setdefault(key, row.name)
        c0_markets.setdefault(key, key[0])
    c0_rows = {}
    for row in c0_predictions.to_dict("records"):
        c0_rows.setdefault((str(row["market"]).upper(), str(row["stock_id"])), row)
    c0_status = {}
    for row in c0_coverage.to_dict("records"):
        c0_status.setdefault((str(row["market"]).upper(), str(row["stock_id"])), row.get("status"))
    records = []
    for index, row in enumerate(ordered.to_dict("records"), 1):
        stock = str(row["stock_id"])
        key = (str(row["market"]).upper(), stock)
        c0 = c0_rows.get(key) or {}
        rank_tradable = row.get("rank_tradable")
        records.append({
            "stock_id": stock, "market": str(row["market"]).upper(),
            "name": names.get(key) if names.get(key) is not None else c0.get("name"),
            "date": str(row.get("signal_date") or meta["signal_date"])[:10],
            "snapshot_signal_date": str(row.get("signal_date") or meta["signal_date"])[:10],
            "analysis_asof": c0.get("analysis_asof") or meta["signal_date"],
            "feature_data_date": row.get("feature_data_date"),
            "stock_last_price_date": row.get("stock_last_price_date"),
            "target_session_date": str(row.get("target_date") or meta["target_date"])[:10],
            "probability": float(row["p_up"]), "expected_return": float(row["exp_ret"]),
            "probability_raw": _float(row.get("p_up_raw")), "expected_return_raw": _float(row.get("exp_ret_raw")),
            "tradable": _tradable(row["tradable"]),
            "rank_tradable": int(rank_tradable) if _finite(rank_tradable) else None,
            "preopen_rank": int(row["rank"]) if _finite(row.get("rank")) else None,
            "preopen_display_rank": index,
            "signal_close": _float(row.get("signal_close")), "adj_close": _float(row.get("adj_close")),
            "amt20": _float(row.get("amt20")), "feature_coverage": _float(row.get("feature_coverage")),
            "prediction_status": "predicted", "model_source": "preopen",
            "engine_id": engine_id, "model_schema": meta.get("snapshot_schema"),
            "base_model_version": meta.get("model_sha256"), "data_snapshot": meta.get("snapshot_sha256"),
            "preopen_run_id": meta.get("run_id"), "contribution_mode": "report_only",
            "in_c0_universe": key in names, "c0_market": c0_markets.get(key),
            "c0_probability": _float(c0.get("probability")), "c0_expected_return": _float(c0.get("expected_return")),
            "c0_prediction_status": c0.get("prediction_status") or c0_status.get(key),
            "c0_feature_data_date": c0.get("feature_data_date"),
            "c0_stock_last_price_date": c0.get("stock_last_price_date"),
            "in_preopen_snapshot": True,
        })
    in_snapshot = set(zip(ordered["market"], ordered["stock_id"]))
    for key, c0 in sorted(c0_rows.items()):
        probability, expected_return = _float(c0.get("probability")), _float(c0.get("expected_return"))
        if (key in in_snapshot or c0.get("prediction_status", "predicted") != "predicted"
                or probability is None or not 0 <= probability <= 1 or expected_return is None):
            continue
        records.append({**c0, "stock_id": key[1], "market": key[0],
            "name": c0.get("name") or names.get(key), "model_source": "c0", "c0_supplement": True,
            "in_preopen_snapshot": False, "in_c0_universe": key in names, "c0_market": key[0],
            "tradable": None, "rank_tradable": None, "preopen_rank": None,
            "preopen_display_rank": len(records) + 1,
            "c0_probability": probability, "c0_expected_return": expected_return,
            "c0_prediction_status": c0.get("prediction_status", "predicted")})
    predictions = pd.DataFrame.from_records(records)
    displayed_pairs = set(zip(predictions["market"], predictions["stock_id"]))
    coverage_rows = []
    for row in predictions.itertuples():
        coverage_rows.append({"stock_id": row.stock_id, "market": row.market, "name": row.name,
                              "status": "preopen_predicted" if row.model_source == "preopen" else "c0_supplement_predicted",
                              "c0_status": c0_status.get((row.market, row.stock_id)),
                              "in_preopen_snapshot": row.model_source == "preopen", "in_c0_universe": bool(row.in_c0_universe)})
    for row in c0_coverage.to_dict("records"):
        stock = str(row["stock_id"])
        if (str(row["market"]).upper(), stock) in displayed_pairs:
            continue
        coverage_rows.append({"stock_id": stock, "market": str(row.get("market")).upper(), "name": row.get("name"),
                              "status": "preopen_not_in_snapshot", "c0_status": row.get("status"),
                              "in_preopen_snapshot": False, "in_c0_universe": True})
    return predictions, pd.DataFrame.from_records(coverage_rows)


def _tradable(value):
    """Unknown eligibility is never converted using Python's truthiness."""
    if value is True or value is False:
        return value
    try:
        if pd.isna(value):
            return None
        if value == 1:
            return True
        if value == 0:
            return False
    except (TypeError, ValueError):
        pass
    return None


def _finite(value):
    try:
        return value is not None and math.isfinite(float(value))
    except (TypeError, ValueError):
        return False


def _float(value):
    return float(value) if _finite(value) else None


def report_fields(choice):
    """報告（payload、context、manifest）共用的模型來源欄位；揭露與閘門文字依快照配方（disclosure_entry）。"""
    active = choice.active
    meta = choice.snapshot.meta if active else None
    return {
        "model_source": "preopen" if active else "c0",
        "requested_model_source": choice.requested,
        "model_source_origin": choice.origin,
        "preopen_status": choice.status_record(),
        "preopen_meta": choice.preopen_meta(),
        "preopen_gate": gate_record(meta.get("gate_us_up"), meta) if active else None,
        "model_source_note": choice.note(),
        "model_disclosure": disclosure_record(meta) if active else None,
    }


def check_summary(requested, origin, expected_signal_date, *, root=None):
    """CHECK_ENV 用：唯讀檢查快照可用性（一律實際讀取一次；不寫任何檔案）。

    ``required_unavailable`` 為 True 表示 model source = preopen 但快照不可用，呼叫端應以失敗結束。
    """
    choice = choose("auto", origin, expected_signal_date, root=root)
    summary = choice.status_record()
    summary.update(requested_model_source=requested,
                   model_source=("c0" if requested == "c0" else choice.model_source),
                   required_unavailable=requested == "preopen" and not choice.active,
                   preopen_meta=choice.preopen_meta(),
                   gate=gate_record(choice.snapshot.meta.get("gate_us_up"), choice.snapshot.meta)
                   if choice.active else None)
    if choice.active:
        line = "開盤前版快照可用：" + str(choice.describe_text)
        if requested == "c0":
            line += "（目前 model source = c0，執行時仍強制使用 C0）"
    else:
        line = ("開盤前版快照不可用（" + str(choice.reason) + "：" + reason_text(choice.reason) + "）；" +
                {"c0": "目前 model source = c0，執行時強制使用 C0",
                 "preopen": "目前 model source = preopen，執行將失敗"}.get(requested, "auto 模式執行時 H1 會改用 C0 基準"))
    summary["line"] = line
    return summary
