"""單股 H1「開盤前版」疊加層：只透過 stock_daily_preopen_reader 唯讀取用快照，不寫任何共用狀態。

H1 報告優先顯示開盤前版快照中本股那一列（p_up、exp_ret、全市場／可交易名次、前一晚美股、
describe(meta) 與必要揭露）。快照不可用、過期（signal_date 不等於本次單股分析的資料日）、
讀取失敗或沒有本股時，回退 C0 基準並在報告醒目標示原因代碼。H63 不受影響。

原本的 C0／選定模型機器欄位（prediction、prob_up、expected_return）一律照舊，仍由原驗證程式重現；
開盤前版內容另存於 payload['preopen_h1']，與報告一起封存（C0 路徑的 estimate_binding_sha256、
選定模型路徑的 runtime_payload_sha256）。開盤前版快照本身不在重現範圍內，只以 run_id、
snapshot_sha256 與 model_sha256 留下可稽核的身分。

來源開關（優先序：命令列 --h1-source ＞ 環境變數 DSTOCK_SINGLE_H1_SOURCE ＞ 預設 auto）：
- auto：快照可用且新鮮就用開盤前版，否則回退 C0 基準並標示原因；
- preopen：必須使用開盤前版，不可用即停止（PreopenRequiredError，rc=2）；
- c0：強制舊行為：不讀取開盤前版、不加入 preopen_h1 區塊，報告內容與改版前相同
  （也供尚未重啟、記憶體仍是舊版 stock_candidate_report 的常駐程式驗證使用）。
"""
from __future__ import annotations

import contextlib
import math
import os
from pathlib import Path

import date_format as cd  # 面向使用者的日期一律 YYYYMMDD；JSON 機器欄位維持 ISO

BLOCK_SCHEMA = "single-stock-preopen-h1-v1"
SOURCE_ENV = "DSTOCK_SINGLE_H1_SOURCE"
SOURCES = ("auto", "preopen", "c0")
DEFAULT_SOURCE = "auto"
C0_BASE_LABEL = "C0 基準"
SELECTED_BASE_LABEL = "C0 基準，單股已啟用選定模型"
MODEL_LABEL_FALLBACK = "開盤前版 H1（HGB，含前一晚美股）"
CALIBRATION_REPORT = Path(
    r"D:\TWStockData\program_data\stock_daily_c0\candidates\calibration_20261018\report\校正報告_20261018.md")
H1_IMPROVE_REPORT = Path(
    r"D:\TWStockData\program_data\stock_daily_c0\candidates\h1_improve_20261021\report\H1改良研究報告_20261021.md")
RECIPE_V1 = "h1pre_final_nogc_xs"  # manifest 沒有 recipe 欄位的舊快照一律視為此配方
RECIPE_V2 = "h1pre_v2_ens"

# 必要揭露依快照 manifest 的 recipe.name 選擇（DISCLOSURES／disclosure_for）；只換揭露文字，不改其他行為。
# 配方 v1（h1pre_final_nogc_xs）：資料出處為上列校正報告（504 日窗開盤前版，保留期 2026-01-02～2026-10-16），
# 內容與改版前逐字相同。
DISCLOSURE = {
    "target_zh": "下一交易日收盤相對訊號日收盤的方向（收盤對收盤）",
    "holdout_period": "2026-01～2026-10",
    "holdout_direction_accuracy": 0.590,
    "always_down_accuracy": 0.559,
    "holdout_auc": 0.608,
    "us_up_day_tradable_top10_hit_rate": 0.635,
    "all_days_tradable_top10_hit_rate": 0.556,
    "next_open_buy_top10_mean_daily_return": -0.0004,
    "source": str(CALIBRATION_REPORT),
}
DISCLOSURE_ROWS = (
    ("預測目標", "下一交易日收盤相對訊號日收盤的方向（收盤對收盤），不是開盤價、盤中價格或可成交報酬。"),
    ("保留期方向準確率", "2026-01～10 保留期 59.0%（一律猜跌 55.9%），AUC 0.608。"),
    ("可交易 Top10 命中率", "只在前一晚美股收漲日選出的可交易 Top10 命中 63.5%；不分日子為 55.6%。"),
    ("交易提醒", "若在隔日開盤買進，Top10 平均報酬為負（約 -0.04%／日）；命中率不代表交易獲利。"),
    ("資料出處", str(CALIBRATION_REPORT)),
)
CAPTION_DISCLOSURE = ("揭露：預測下一交易日收盤相對訊號日收盤的方向；保留期（2026-01～10）方向準確率59.0%"
                      "（一律猜跌55.9%）、AUC 0.608；美股收漲日可交易Top10命中63.5%，全部日子55.6%；"
                      "隔日開盤買進Top10平均約-0.04%／日，命中率不代表交易獲利。")

# 配方 v2（h1pre_v2_ens）：資料出處為 H1 改良研究報告第 6 節（保留期 2026-01-02～2026-10-19，191 個交易日）。
# 隔日開盤買進 Top10 約 +0.01%／日：約為零，不是負值（v1 的「為負」在 v2 不成立）。
DISCLOSURE_V2 = {
    "target_zh": "下一交易日收盤相對訊號日收盤的方向（收盤對收盤）",
    "recipe": RECIPE_V2,
    "holdout_period": "2026-01-02～2026-10-19",
    "holdout_sessions": 191,
    "holdout_direction_accuracy": 0.600,
    "always_down_accuracy": 0.561,
    "holdout_auc": 0.619,
    "us_up_day_tradable_top10_hit_rate": 0.653,
    "other_days_tradable_top10_hit_rate": 0.491,
    "all_days_tradable_top10_hit_rate": 0.571,
    "next_open_buy_top10_mean_daily_return": 0.0001,
    "tradable_top10_mae": 0.0271,
    "source": str(H1_IMPROVE_REPORT),
}
DISCLOSURE_ROWS_V2 = (
    ("預測目標", "下一交易日收盤相對訊號日收盤的方向（收盤對收盤），不是開盤價、盤中價格或可成交報酬。"),
    ("模型配方", "h1pre_v2_ens（2026-10-21 改良版：HGB 集成，校準窗 250 日）。"),
    ("保留期方向準確率", "2026-01-02～2026-10-19 保留期（191 個交易日）60.0%（一律猜跌 56.1%），AUC 0.619。"),
    ("可交易 Top10 命中率", "只在前一晚美股收漲日選出的可交易 Top10 命中 65.3%，其他日子 49.1%；不分日子為 57.1%。"),
    ("可交易 Top10 預估誤差", "可交易 Top10 預估收盤報酬的平均絕對誤差（MAE）2.71%。"),
    ("交易提醒", "若在隔日開盤買進，Top10 平均報酬約為零（約 +0.01%／日；不是負值，也不代表獲利）；命中率不代表交易獲利。"),
    ("資料出處", str(H1_IMPROVE_REPORT)),
)
CAPTION_DISCLOSURE_V2 = ("揭露：預測下一交易日收盤相對訊號日收盤的方向；保留期（2026-01-02～10-19，191日）方向準確率60.0%"
                         "（一律猜跌56.1%）、AUC 0.619；美股收漲日可交易Top10命中65.3%，其他日子49.1%，全部日子57.1%；"
                         "隔日開盤買進Top10平均約+0.01%／日（約為零），命中率不代表交易獲利。")

# 配方名稱 → 必要揭露（facts 進 block["disclosure"]、rows 進 block["disclosure_rows"]、caption 供短訊息使用）。
DISCLOSURES = {
    RECIPE_V1: {"facts": DISCLOSURE, "rows": DISCLOSURE_ROWS, "caption": CAPTION_DISCLOSURE},
    RECIPE_V2: {"facts": DISCLOSURE_V2, "rows": DISCLOSURE_ROWS_V2, "caption": CAPTION_DISCLOSURE_V2},
}


def _recipe_label(value):
    """未知配方的顯示名稱：只留可列印 ASCII（其餘以 ? 代替，避免字型或訊息格式問題），最長 64 字；空值為「未提供」。"""
    text = "" if value is None else str(value)
    text = "".join(ch if " " <= ch <= "~" else "?" for ch in text).strip()[:64]
    return text or "未提供"


def _unknown_disclosure(label):
    """表中沒有的配方：中性說明（配方未知＋名稱），不顯示也不沿用任何配方的保留期數字。"""
    return {
        "facts": {"target_zh": DISCLOSURE["target_zh"], "recipe": label, "recipe_known": False},
        "rows": (
            ("預測目標", "下一交易日收盤相對訊號日收盤的方向（收盤對收盤），不是開盤價、盤中價格或可成交報酬。"),
            ("模型配方", f"快照標示的配方「{label}」不在本程式的揭露表中（配方未知）；本報告不顯示此配方的保留期準確率、"
                         "命中率與開盤買進報酬，也不沿用其他配方的數字。"),
            ("交易提醒", "命中率不代表交易獲利；此配方的驗證結果請以產製端的研究報告為準。"),
        ),
        "caption": (f"揭露：預測下一交易日收盤相對訊號日收盤的方向；開盤前版配方「{label}」未知，"
                    "不顯示保留期驗證數字；命中率不代表交易獲利。"),
    }


def disclosure_for(meta):
    """依快照 meta["recipe"]["name"] 選必要揭露：{"recipe", "known", "facts", "rows", "caption"}。

    沒有 recipe 欄位（或為 null）的舊快照視為 v1，內容與改版前逐字相同；表中沒有的名稱、recipe 不是
    dict 或缺 name 時為未知配方，只給中性說明並顯示名稱。也可傳入封存區塊的 block["meta"]（保留 recipe）。
    回傳的 facts／rows 是模組常數，呼叫端需自行複製。
    """
    recipe = meta.get("recipe") if isinstance(meta, dict) else None
    if recipe is None:
        return {"recipe": RECIPE_V1, "known": True, **DISCLOSURES[RECIPE_V1]}
    name = recipe.get("name") if isinstance(recipe, dict) else None
    entry = DISCLOSURES.get(name) if isinstance(name, str) else None
    if entry is not None:
        return {"recipe": name, "known": True, **entry}
    label = _recipe_label(name if isinstance(recipe, dict) else recipe)
    return {"recipe": label, "known": False, **_unknown_disclosure(label)}


REASON_ZH = {
    "preopen_no_latest": "尚無開盤前版快照",
    "preopen_schema_mismatch": "開盤前版指標檔格式不符",
    "preopen_stale": "開盤前版快照的訊號日與本次分析資料日不同",
    "preopen_snapshot_missing": "開盤前版快照檔缺失",
    "preopen_snapshot_sha_mismatch": "開盤前版快照雜湊不符",
    "preopen_snapshot_columns": "開盤前版快照欄位不完整",
    "preopen_snapshot_values": "開盤前版快照數值無效",
    "preopen_us_status_invalid": "開盤前版的美股狀態無效",
    "preopen_stock_not_in_snapshot": "開盤前版快照沒有本股",
    "preopen_market_mismatch": "開盤前版快照中本股的市場別不同",
    "preopen_row_invalid": "開盤前版本股數值無效",
    "preopen_read_error": "開盤前版快照讀取失敗",
    "preopen_parquet_engine_missing": "開盤前版快照無法讀取（缺少 parquet 讀取元件，且 CSV 備援不可用）",
    "preopen_reader_unavailable": "開盤前版讀取程式無法載入",
    "preopen_asof_unknown": "本次分析資料日未知，無法確認快照新鮮度",
    "h1_source_forced_c0": "依設定強制使用 C0 基準",
}
US_STATUS_ZH = {"ok": "前一晚美股收盤已納入", "holiday": "前一晚美股休市",
                "missing": "前一晚美股收盤缺漏（以缺值推論，準確度可能下降）"}
_META_KEYS = ("schema", "snapshot_schema", "run_id", "signal_date", "target_date", "target_date_source", "snapshot",
              "snapshot_sha256", "rows", "model_id", "model_sha256", "recipe", "created_at", "us_overnight",
              "gate_us_up", "central_head_sha256", "fixture", "chips_freshness", "degraded")
_ROW_KEYS = ("signal_date", "target_date", "stock_id", "market", "p_up", "p_up_raw", "exp_ret", "exp_ret_raw",
             "rank", "rank_tradable", "tradable", "signal_close", "adj_close", "amt20", "feature_coverage")


class PreopenRequiredError(ValueError):
    """--h1-source preopen 時開盤前版不可用：停止，不回退。"""

    def __init__(self, reason, detail=""):
        code = f"preopen_required_unavailable:{reason}" + (f":{detail}" if detail else "")
        super().__init__(f"已指定 --h1-source preopen，但開盤前版不可用（{REASON_ZH.get(reason, '開盤前版不可用')}）："
                         f"{code}；未產生任何報告")
        self.reason = reason
        self.detail = detail


def resolve_source(value=None, environ=None):
    """命令列值優先，其次環境變數 DSTOCK_SINGLE_H1_SOURCE，預設 auto；不合法即 ValueError。"""
    if value is None:
        value = (os.environ if environ is None else environ).get(SOURCE_ENV)
    text = str(value if value is not None else DEFAULT_SOURCE).strip().lower() or DEFAULT_SOURCE
    if text not in SOURCES:
        raise ValueError(f"H1 來源設定無效：{text}（僅接受 {'、'.join(SOURCES)}）：h1_source_invalid:{text}")
    return text


def _clean(value):
    """JSON 原生值（numpy → python，NaN/NaT → None）；封存摘要要求原生 JSON 樹。"""
    if isinstance(value, dict):
        return {str(key): _clean(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [_clean(item) for item in value]
    if hasattr(value, "item") and not isinstance(value, (str, bytes)):
        try:
            value = value.item()
        except (TypeError, ValueError):
            return str(value)
    if isinstance(value, float) and not math.isfinite(value):
        return None
    if hasattr(value, "isoformat") and not isinstance(value, str):
        return value.isoformat()
    if value is not None and not isinstance(value, (str, bool, int, float)):
        try:
            if value != value:
                return None
        except (TypeError, ValueError):
            pass
        return str(value)
    return value


def _day(value):
    text = str(value or "").strip()[:10]
    if len(text) == 8 and text.isdigit():
        text = f"{text[:4]}-{text[4:6]}-{text[6:]}"
    try:
        from datetime import date
        return date.fromisoformat(text).isoformat()
    except ValueError:
        return None


def _finite(value):
    if isinstance(value, bool):
        return None
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    return number if math.isfinite(number) else None


def _base_block(stock, expected, source, base_label, consumer):
    return {"schema": BLOCK_SCHEMA, "consumer": consumer, "h1_source_mode": source,
            "stock_id": str((stock or {}).get("stock_id") or ""),
            "market": str((stock or {}).get("market") or "").upper() or None,
            "expected_signal_date": expected, "fallback_label": base_label,
            "model_label": MODEL_LABEL_FALLBACK, "target_definition": DISCLOSURE["target_zh"]}


def _fallback(block, reason, detail="", *, status="fallback", meta=None):
    detail = str(detail or "")[:300]
    if status == "disabled":
        notice = f"依設定不使用開盤前版（{reason}），本次 H1 改用 {block['fallback_label']}"
    else:
        notice = f"開盤前版不可用（{reason}），本次 H1 改用 {block['fallback_label']}"
    block.update(status=status, display_source="c0", reason=reason,
                 reason_zh=REASON_ZH.get(reason, "開盤前版不可用"), reason_detail=detail, notice=notice)
    if meta:
        block["observed_meta"] = {key: _clean(meta.get(key)) for key in _META_KEYS if key in meta}
    return block


def _unavailable(block, source, reason, detail="", meta=None):
    if source == "preopen":
        raise PreopenRequiredError(reason, str(detail or "")[:300])
    return _fallback(block, reason, detail, meta=meta)


def _observed_meta(reader, root):
    """過期等情形下，記錄實際看到的快照身分（只讀指標檔；失敗就略過）。"""
    try:
        return reader.read_latest_meta(root)
    except Exception:  # noqa: BLE001 - 僅供說明，不影響回退
        return None


def build_h1_block(stock, expected_signal_date, *, source=None, root=None, base_label=C0_BASE_LABEL,
                   consumer="single_c0", base_target_date=None):
    """回傳本股的開盤前版 H1 區塊（status=used／fallback／disabled）。

    expected_signal_date 為本次單股分析本股所屬市場的資料日；快照 signal_date 必須相同。
    source=None 時讀 DSTOCK_SINGLE_H1_SOURCE（預設 auto）。source=preopen 且不可用時丟
    PreopenRequiredError；auto 時任何讀取錯誤（Exception）都只回退，不中斷 C0 報告。
    """
    source = resolve_source(source)
    expected = _day(expected_signal_date)
    block = _base_block(stock, expected, source, base_label, consumer)
    if source == "c0":
        return _fallback(block, "h1_source_forced_c0", "h1_source=c0", status="disabled")
    if expected is None:
        return _unavailable(block, source, "preopen_asof_unknown", str(expected_signal_date))
    try:
        import stock_daily_preopen_reader as reader
    except Exception as exc:  # noqa: BLE001 - 讀取層無法載入時只回退
        return _unavailable(block, source, "preopen_reader_unavailable", type(exc).__name__ + ": " + str(exc))
    block["model_label"] = getattr(reader, "MODEL_LABEL", MODEL_LABEL_FALLBACK)
    try:
        block["root"] = str(reader.root_dir(root))
        snapshot = reader.load_latest(root, expected_signal_date=expected)
    except reader.PreopenUnavailable as exc:
        meta = _observed_meta(reader, root) if exc.reason == "preopen_stale" else None
        return _unavailable(block, source, exc.reason, exc.detail, meta=meta)
    except Exception as exc:  # noqa: BLE001 - 損壞的快照不可讓 C0 報告失敗
        return _unavailable(block, source, "preopen_read_error", type(exc).__name__ + ": " + str(exc))
    meta = snapshot.meta
    meta_summary = {key: _clean(meta.get(key)) for key in _META_KEYS if key in meta}
    chosen = disclosure_for(meta)  # 依快照配方選必要揭露（舊快照＝v1）
    try:
        row = snapshot.row(block["stock_id"])
    except Exception as exc:  # noqa: BLE001
        return _unavailable(block, source, "preopen_read_error", type(exc).__name__ + ": " + str(exc), meta=meta)
    if row is None:
        return _unavailable(block, source, "preopen_stock_not_in_snapshot",
                            f"stock {block['stock_id']} not in snapshot {meta.get('run_id')}", meta=meta)
    row = {key: _clean(row.get(key)) for key in _ROW_KEYS}
    if block["market"] and str(row.get("market") or "").upper() != block["market"]:
        return _unavailable(block, source, "preopen_market_mismatch",
                            f"snapshot market {row.get('market')} != {block['market']}", meta=meta)
    p_up, exp_ret = _finite(row.get("p_up")), _finite(row.get("exp_ret"))
    if p_up is None or not 0 <= p_up <= 1 or exp_ret is None or _day(row.get("signal_date")) != expected:
        return _unavailable(block, source, "preopen_row_invalid",
                            f"p_up={row.get('p_up')} exp_ret={row.get('exp_ret')} signal_date={row.get('signal_date')}",
                            meta=meta)
    frame = snapshot.frame
    rank = _finite(row.get("rank"))
    rank_tradable = _finite(row.get("rank_tradable"))
    tradable = bool(row.get("tradable"))
    target = _day(row.get("target_date") or meta.get("target_date"))
    base_target = _day(base_target_date)
    us = meta.get("us_overnight") if isinstance(meta.get("us_overnight"), dict) else {}
    gate = meta.get("gate_us_up")
    block.update(
        status="used", display_source="preopen", reason=None, notice=None,
        p_up=p_up, exp_ret=exp_ret, p_up_raw=_finite(row.get("p_up_raw")), exp_ret_raw=_finite(row.get("exp_ret_raw")),
        rank=int(rank) if rank is not None else None,
        rank_tradable=int(rank_tradable) if tradable and rank_tradable is not None else None,
        tradable=tradable, universe_rows=int(len(frame)), tradable_rows=int(frame["tradable"].sum()),
        in_tradable_top10=bool(tradable and rank_tradable is not None and rank_tradable <= 10),
        signal_date=_day(row.get("signal_date")), target_date=target,
        base_target_date=base_target,
        target_date_consistent=None if base_target is None or target is None else base_target == target,
        signal_close=_finite(row.get("signal_close")), feature_coverage=_finite(row.get("feature_coverage")),
        us_overnight_status=us.get("status"), us_overnight_status_zh=US_STATUS_ZH.get(us.get("status"), "美股狀態未知"),
        gate_us_up=gate if isinstance(gate, bool) else None,
        gate_us_up_zh=("前一晚美股收漲（建議日）" if gate is True else
                       "前一晚美股未收漲（非建議日）" if gate is False else "前一晚美股漲跌未知"),
        fixture=meta.get("fixture") is True,
        chips_note_zh=_chips_note(reader, meta),
        describe=reader.describe(meta), row=row, meta=meta_summary,
        disclosure=dict(chosen["facts"]), disclosure_rows=[list(item) for item in chosen["rows"]],
    )
    return _clean(block)


def _chips_note(reader, meta):
    """t 日籌碼未更新或不完整時的一段說明（讀取器 chips_freshness_text；舊讀取器或舊快照為 None）。"""
    text_fn = getattr(reader, "chips_freshness_text", None)
    if not callable(text_fn):
        return None
    try:
        note = text_fn(meta.get("chips_freshness"))
    except Exception:  # noqa: BLE001 - 說明文字不得讓 H1 區塊失敗
        return None
    return str(note) if isinstance(note, str) and note.strip() else None


def display_lines(block):
    """主控台一行說明（不含「｜」與 PDF／JSON 前綴，不干擾 Bot 的路徑解析）。"""
    if not isinstance(block, dict):
        return []
    if block.get("status") == "used":
        return [f"H1 來源：開盤前版（訊號日 {cd.display_day(block.get('signal_date'), empty='未知')}，"
                f"目標日 {cd.display_day(block.get('target_date'), empty='未知')}，"
                f"上漲機率 {100 * float(block['p_up']):.2f}%，預估收盤報酬 {100 * float(block['exp_ret']):+.2f}%）"]
    line = "H1 來源：" + str(block.get("notice") or "C0 基準")
    reason_zh = block.get("reason_zh")
    return [line + f"。原因：{reason_zh}" if reason_zh else line]


def attach_selected_h1(payload, *, source=None, root=None):
    """選定模型正式 H1 payload：加入開盤前版區塊並重算 runtime_payload_sha256（同一共用摘要演算法）。

    只在單股入口包住 generate_selected_reports 時、由 stock_candidate_report.write_candidate_report
    在產製 PDF 之前呼叫一次；之後的驗證（validate_selected_report）以同一 payload 核對摘要與章節，
    預測重現只比對原本的穩定欄位，不含本區塊。
    """
    import stock_daily_selected_deployment as selected

    block = build_h1_block(payload.get("stock") or {}, payload.get("asof"), source=source, root=root,
                           base_label=SELECTED_BASE_LABEL, consumer="single_selected",
                           base_target_date=payload.get("target_session_date"))
    payload["preopen_h1"] = block
    payload["runtime_payload_sha256"] = selected._digest(
        {key: value for key, value in payload.items() if key != "runtime_payload_sha256"})
    for line in display_lines(block):
        print(line, flush=True)
    return block


@contextlib.contextmanager
def selected_h1_overlay(source=None, *, root=None):
    """在單股層包住 shared 的 generate_selected_reports：只對正式 H1 報告加入開盤前版區塊。

    source=c0（強制舊行為）時不安裝任何掛勾，選定模型報告與改版前完全相同。
    """
    import stock_candidate_report as report

    source = resolve_source(source)
    if source == "c0":
        yield
        return
    with report.preopen_h1_attacher(lambda payload: attach_selected_h1(payload, source=source, root=root)):
        yield
