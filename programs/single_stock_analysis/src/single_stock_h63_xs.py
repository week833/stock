"""單股 H63「三個月相對強弱」疊加層（ADR-0009）：只透過 stock_h63_xs_reader 唯讀取用 U7 全市場快照，不寫任何檔案。

H63 正式報告改為顯示 U7 全市場排名法：跑贏同期多數股票的機率（相對強弱，不是上漲機率）、合格母體排名、
B3＋CQR252 的 80% 報酬區間與固定揭露。選定模型（expanded_linear）的 H63 機器欄位照舊保留，只供原驗證程式重現，
不再顯示。U7 內容另存於 payload['h63_xs']，與報告一起封存（runtime_payload_sha256 由本層重算）。

來源開關（優先序：命令列 --h63-source ＞ 環境變數 DSTOCK_SINGLE_H63_SOURCE ＞ 資料根 config\\display.json ＞ 預設 auto）：
- auto：快照可用就顯示 U7；不可用時只顯示原因，不顯示任何 H63 數字；
- xs：必須使用 U7，不可用即停止（H63XsRequiredError，rc=2；QA 用）；
- off：不讀快照，H63 只顯示「已暫停顯示」；
- legacy：不安裝掛勾，報告與改版前完全相同（Bot 重啟前的過渡與回復用）。
H1 不受影響。
"""
from __future__ import annotations

import contextlib
import math
import os

import date_format as cd  # 面向使用者的日期一律 YYYYMMDD；JSON 機器欄位維持 ISO

BLOCK_SCHEMA = "single-stock-h63-xs-v1"
SOURCE_ENV = "DSTOCK_SINGLE_H63_SOURCE"
SOURCES = ("auto", "xs", "off", "legacy")
DEFAULT_SOURCE = "auto"
MAX_STALE_WEEKDAYS = 5
DISPLAY_SOURCE = "u7_xs"
_BLOCKED_KEYS = ("p", "p_raw", "band", "factors", "rank_eligible", "relative_strength")


class H63XsRequiredError(ValueError):
    """--h63-source xs 時 U7 快照不可用：停止，不回退。"""

    def __init__(self, reason, detail=""):
        code = f"h63_xs_required_unavailable:{reason}" + (f":{detail}" if detail else "")
        super().__init__(f"已指定 --h63-source xs，但三個月相對強弱快照不可用：{code}；未產生任何報告")
        self.reason = reason
        self.detail = detail


def resolve_source(value=None, environ=None, root=None):
    """命令列值優先，其次環境變數，再其次 config\\display.json（auto／off／legacy），預設 auto；不合法即 ValueError。"""
    if value is None:
        value = (os.environ if environ is None else environ).get(SOURCE_ENV)
    if value is None or not str(value).strip():
        try:
            import stock_h63_xs_reader as reader
            config = reader.read_display_config(root)
            value = config["mode"]
        except Exception:  # noqa: BLE001 - 設定檔讀不到時沿用預設
            value = DEFAULT_SOURCE
    text = str(value).strip().lower() or DEFAULT_SOURCE
    if text not in SOURCES:
        raise ValueError(f"H63 來源設定無效：{text}（僅接受 {'、'.join(SOURCES)}）：h63_source_invalid:{text}")
    return text


def _clean(value):
    """JSON 原生值（numpy → python，NaN/NaT → None）。"""
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
        return str(value)
    return value


def _day(value):
    day = cd.calendar_day_of(value) if value is not None else None
    return day.isoformat() if day is not None else None


def _finite(value):
    if isinstance(value, bool):
        return None
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    return number if math.isfinite(number) else None


def _base_block(stock, expected, source):
    return {"schema": BLOCK_SCHEMA, "h63_source_mode": source, "stock_id": str((stock or {}).get("stock_id") or ""),
            "market": str((stock or {}).get("market") or "").upper() or None, "expected_signal_date": expected,
            "display_source": "none", "reason": None, "reason_zh": None, "reason_detail": None, "notice": None}


def _not_shown(block, status, reason, reason_zh, detail=""):
    if status == "disabled":
        notice = "H63 三個月相對強弱已暫停顯示（" + reason_zh + "）"
    else:
        notice = "三個月相對強弱暫不可用（" + reason_zh + "），本次不顯示 H63 數字"
    block.update(status=status, display_source="none", reason=reason, reason_zh=reason_zh,
                 reason_detail=str(detail or "")[:300], notice=notice)
    return _clean(block)


def _unavailable(block, source, reason, reason_zh, detail=""):
    if source == "xs":
        raise H63XsRequiredError(reason, str(detail or "")[:300])
    return _not_shown(block, "fallback", reason, reason_zh, detail)


def _band(row, header):
    status = str(row.get("band_status") or "")
    out = {"status": status, "method": "B3+CQR252", "nominal": 0.80}
    cqr = ((header.get("band") or {}).get("cqr") or {})
    out.update(cqr_q=_finite(cqr.get("Q")), cal_rows=cqr.get("cal_rows"),
               pool_rows=((header.get("band") or {}).get("b3_table") or {}).get("pool"))
    if status in ("ok", "ok_no_cqr"):
        out.update(q10=_finite(row.get("b3cqr_q10")), q90=_finite(row.get("b3cqr_q90")),
                   q10_raw=_finite(row.get("b3_q10")), q50_raw=_finite(row.get("b3_q50")),
                   q90_raw=_finite(row.get("b3_q90")), sigma63=_finite(row.get("sigma63")),
                   sigma_eff=_finite(row.get("sigma63_b3")), bucket=row.get("b3_bucket"))
    return out


def _notes(row, lag, signal, expected):
    notes = []
    if lag:
        notes.append("沿用較舊快照：訊號日 " + cd.display_day(signal) + "；本次分析資料日 " + cd.display_day(expected)
                     + "（落後 " + str(lag) + " 個平日）")
    if row.get("main") and not row.get("eligible"):
        notes.append("未達排名母體門檻（60 日均成交值 1,000 萬元、上市櫃、126 個交易日歷史），以插入方式排名，可信度較低")
    if row.get("main") and int(row.get("n_components") or 0) < 4:
        notes.append("可用排名因子少於 4 項，機率會靠近 50%，可信度較低")
    status = row.get("band_status")
    if status == "own_vol60_missing":
        notes.append("近 60 日波動度資料不足，無法換算本股三個月報酬區間")
    elif status == "ok_no_cqr":
        notes.append("近一年校準樣本不足，區間未經 CQR 校準")
    elif status in ("pool_insufficient", "invalid_not_containing_zero"):
        notes.append("本次無法提供有效的三個月報酬區間")
    if row.get("revenue_stale"):
        notes.append("最新月營收超過 75 天未更新，營收因子以缺值計")
    flags = [f for f in str(row.get("integrity_flags") or "").split(",") if f]
    if flags:
        names = {"seam_jump": "拼接接縫", "adj_raw_mismatch": "還原價與原價不一致"}
        notes.append("本股價格歷史有" + "、".join(names.get(f, f) for f in flags) + "註記，排名可信度較低")
    if not row.get("main"):
        notes.append("區間僅以上市櫃普通股樣本校準，供參考")
    return notes


def _forward_line(reader, root):
    summary = reader.load_forward_summary(root)
    if not isinstance(summary, dict):
        return {"status": "unavailable"}
    return _clean({"status": "recorded" if summary.get("recorded_blocks") else "empty",
                   "first_decision_date": summary.get("first_decision_date"),
                   "decision_dates": summary.get("decision_dates"),
                   "matured_decision_dates": summary.get("matured_decision_dates") or 0,
                   "verdict": summary.get("verdict"), "verdict_zh": summary.get("verdict_zh"),
                   "prereg_review_sha256": summary.get("prereg_review_sha256")})


def build_h63_block(stock, expected_signal_date, *, source=None, root=None):
    """回傳本股的 H63 U7 區塊（status=used／fallback／disabled）；fallback／disabled 不帶任何數字。"""
    source = resolve_source(source, root=root)
    expected = _day(expected_signal_date)
    block = _base_block(stock, expected, source)
    try:
        import stock_h63_xs_reader as reader
    except Exception as exc:  # noqa: BLE001 - 讀取層無法載入時只回退
        return _unavailable(block, source, "h63xs_reader_unavailable", "讀取模組無法載入", type(exc).__name__)
    if source == "off":
        config = reader.read_display_config(root)
        return _not_shown(block, "disabled", "h63_display_off", config.get("reason_zh") or "依設定停用")
    if expected is None:
        return _unavailable(block, source, "h63xs_asof_unknown", "本次分析資料日不明", str(expected_signal_date))
    try:
        block["root"] = str(reader.root_dir(root))
        snapshot = reader.load_latest(root, expected_signal_date=expected)
    except reader.H63XsUnavailable as exc:
        return _unavailable(block, source, exc.reason, exc.reason_zh, exc.detail)
    except Exception as exc:  # noqa: BLE001 - 損壞的快照不可讓報告失敗
        return _unavailable(block, source, "h63xs_unexpected_error", reader.REASON_TEXT_ZH["h63xs_unexpected_error"],
                            type(exc).__name__ + ": " + str(exc))
    header = snapshot.header
    row = snapshot.row(block["stock_id"])
    if row is None:
        return _unavailable(block, source, "h63xs_stock_not_in_snapshot",
                            reader.REASON_TEXT_ZH["h63xs_stock_not_in_snapshot"], block["stock_id"])
    if block["market"] and str(row.get("market") or "").upper() != block["market"]:
        return _unavailable(block, source, "h63xs_market_mismatch", reader.REASON_TEXT_ZH["h63xs_market_mismatch"],
                            f"snapshot {row.get('market')} != {block['market']}")
    p = _finite(row.get("p"))
    if p is None or not 0 <= p <= 1:
        return _unavailable(block, source, "h63xs_row_invalid", reader.REASON_TEXT_ZH["h63xs_row_invalid"],
                            f"p={row.get('p')}")
    signal = _day(row.get("decision_date"))
    model = header.get("model") or {}
    factors = []
    import stock_h63_xs_core as core
    for name in core.FEATURES:
        rank = _finite(row.get("rk_" + name))
        factors.append({"name": name, "label_zh": reader.FACTOR_LABELS_ZH[name], "sign": core.SIGNS[name],
                        "value": _finite(row.get(name)), "rank": rank,
                        "signed_rank": None if rank is None else core.SIGNS[name] * rank})
    rank = _finite(row.get("rank_eligible"))
    strength = str(row.get("relative_strength"))
    block.update(
        status="used", display_source=DISPLAY_SOURCE, signal_date=signal, snapshot_lag_weekdays=snapshot.lag_weekdays,
        p=p, p_raw=_finite(row.get("p_raw")), p_status=row.get("p_status"),
        relative_strength=strength, relative_strength_zh=reader.TREND_TEXT_ZH.get(strength, "未知"),
        trend_sentence_zh=reader.TREND_SENTENCE_ZH.get(strength), in_scope=bool(row.get("in_scope")),
        eligible=bool(row.get("eligible")), n_components=int(row.get("n_components") or 0),
        rank_eligible=int(rank) if rank is not None else None, eligible_count=snapshot.eligible_count,
        universe_rows=snapshot.universe_rows, score_centred=_finite(row.get("score_centred")),
        factors=factors, band=_band(row, header),
        target={"definition": "63rd_own_price_row_after_signal", "date_status": "unverified_future_calendar"},
        revenue_period=row.get("revenue_period"), revenue_available_at=row.get("revenue_available_at"),
        per_date=row.get("per_date"),
        integrity_flags=[f for f in str(row.get("integrity_flags") or "").split(",") if f],
        notes_zh=_notes(row, snapshot.lag_weekdays, signal, expected),
        forward_record=_forward_line(reader, root),
        model={"model_id": model.get("model_id"), "b": _finite(model.get("b")),
               "fit_cutoff_exclusive": model.get("fit_cutoff_exclusive")},
        identity={"run_id": header.get("run_id"), "entry_key": header.get("entry_key"),
                  "block_sha256": header.get("parquet_sha256"), "rows_sha256": header.get("rows_sha256"),
                  "row_sha256": row.get("row_sha256"), "model_sha256": model.get("model_sha256"),
                  "activation_id": model.get("activation_id"), "recipe": model.get("recipe"),
                  "central_head_sha256": (header.get("data_provenance") or {}).get("central_head_sha256"),
                  "supplement_manifest_sha256": ((header.get("data_provenance") or {}).get("supplement") or {})
                  .get("manifest_sha256")},
        disclosure_version=reader.DISCLOSURE_VERSION,
        disclosure_rows=[list(item) for item in reader.DISCLOSURE_ROWS_ZH],
        caption_disclosure=reader.CAPTION_DISCLOSURE_ZH,
    )
    return _clean(block)


def display_lines(block):
    """主控台一行說明（不含「｜」與 PDF／JSON 前綴，不干擾 Bot 的路徑解析）。"""
    if not isinstance(block, dict):
        return []
    if block.get("status") == "used":
        band = block.get("band") or {}
        text = (f"H63 來源：U7 三個月相對強弱（訊號日 {cd.display_day(block.get('signal_date'), empty='未知')}，"
                f"跑贏同期多數股票的機率 {100 * float(block['p']):.1f}%，{block.get('relative_strength_zh')}")
        if band.get("q10") is not None and band.get("q90") is not None:
            text += f"，80% 區間 {100 * band['q10']:+.1f}% 至 {100 * band['q90']:+.1f}%"
        return [text + "）"]
    return ["H63 來源：" + str(block.get("notice") or "不顯示 H63 數字")]


def attach_selected_h63(payload, *, source=None, root=None):
    """選定模型正式 H63 payload：加入 U7 區塊並重算 runtime_payload_sha256（與 H1 疊加層同一摘要演算法）。"""
    import stock_daily_selected_deployment as selected

    block = build_h63_block(payload.get("stock") or {}, payload.get("asof"), source=source, root=root)
    payload["h63_xs"] = block
    payload["runtime_payload_sha256"] = selected._digest(
        {key: value for key, value in payload.items() if key != "runtime_payload_sha256"})
    for line in display_lines(block):
        print(line, flush=True)
    return block


@contextlib.contextmanager
def selected_h63_overlay(source=None, *, root=None):
    """在單股層包住 shared 的 generate_selected_reports：只對正式 H63 報告加入 U7 區塊。

    source=legacy 時不安裝任何掛勾，選定模型報告與改版前完全相同。
    """
    import stock_candidate_report as report

    source = resolve_source(source, root=root)
    if source == "legacy":
        yield
        return
    with report.h63_xs_attacher(lambda payload: attach_selected_h63(payload, source=source, root=root)):
        yield
