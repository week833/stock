"""Current-market segments and lagged forward exclusion, with synthetic pinned rows."""
from __future__ import annotations

import copy

import pandas as pd
import pytest

from test_current_reader_integrity import reader_case, fixtures, contract  # noqa: F401


def _change_market(case, stock, current, previous, retained):
    start = fixtures.DATES[fixtures.D_INDEX - retained + 1]
    for row in case.routes["official_market_daily"]:
        if row["stock_id"] == stock:
            row["market"] = previous if row["date"] < start else current
    for row in case.routes["finmind_price_adj"]:
        if row["stock_id"] == stock:
            row["market"] = previous if row["date"] < start else current
    return start


def _excluded(scope):
    return {(row["stock_id"], row["market"]): set(row["reason"].split(","))
            for row in scope["coverage"]["excluded"]}


@pytest.mark.parametrize("stock,current,previous,retained", [
    ("1111", "TWSE", "TPEX", 5),
    ("2222", "TWSE", "TPEX", 68),
    ("3333", "TPEX", "TWSE", 180),
])
def test_one_market_change_keeps_only_current_segment_without_minimum_days(
        reader_case, stock, current, previous, retained):
    start = _change_market(reader_case, stock, current, previous, retained)
    identity = next(row for row in reader_case.context["current_identity"]["records"]
                    if row["stock_id"] == stock and row["market"] == current)
    # A current roster date is not historical lifecycle evidence and must not
    # determine the observed segment boundary.
    identity.update(source_listed_date=fixtures.D, source_listed_date_authoritative_effective_date=False)
    scope = reader_case.load()
    assert scope["coverage"]["eligible"] == len(fixtures.STOCKS)
    assert scope["coverage"]["excluded"] == []
    selected = scope["panel"].loc[lambda frame: frame.stock_id.eq(stock) & frame.market.eq(current)]
    assert len(selected) == retained
    assert selected["session_date"].min() == start and selected["session_date"].max() == fixtures.D
    expected = {row["date"]: row["close"] for row in reader_case.routes["finmind_price_adj"]
                if row["stock_id"] == stock and start <= row["date"] <= fixtures.D}
    assert dict(zip(selected["session_date"], selected["adjusted_close"])) == expected
    assessment = next(row for row in scope["data_quality_assessments"] if row["stock_id"] == stock)
    assert assessment["expected_sessions"] == 253
    assert assessment["observed_sessions"] == retained and assessment["missing_sessions"] == 253 - retained
    assert assessment["status"] == "partial"
    assert assessment["market_segment"] == {
        "status": "current_market_segment_only", "start_date": start, "end_date": fixtures.D,
        "previous_market": previous, "current_market": current,
        "truncated_price_rows": 253 - retained, "retained_price_rows": retained,
        "historical_issuer_continuity_verified": False, "historical_membership_verified": False,
    }
    assert any("未混接" in note and "歷史發行人連續性未驗證" in note for note in assessment["notes"])


@pytest.mark.parametrize("fault,reason", [
    ("same_day", "price_window_official_market_mismatch"),
    ("repeated", "price_window_official_market_mismatch"),
    ("unknown", "price_window_official_market_mismatch"),
    ("ends_other", "price_window_official_market_mismatch"),
    ("ends_before_asof", "price_window_official_market_mismatch"),
    ("quote_conflict", "conflicting_official_board_key"),
    ("price_market", "price_window_identity_market_mismatch"),
])
def test_market_segment_does_not_hide_market_or_quote_conflicts(reader_case, fault, reason):
    start = _change_market(reader_case, "1111", "TWSE", "TPEX", 5)
    board = reader_case.routes["official_market_daily"]
    own = [row for row in board if row["stock_id"] == "1111"]
    if fault == "same_day":
        board.append(dict(next(row for row in own if row["date"] == fixtures.D), market="TPEX"))
    elif fault == "repeated":
        next(row for row in own if row["date"] == fixtures.DATES[fixtures.D_INDEX - 10])["market"] = "TWSE"
    elif fault == "unknown":
        for row in own:
            if row["date"] < start:
                row["market"] = "UNKNOWN"
    elif fault == "ends_other":
        next(row for row in own if row["date"] == fixtures.D)["market"] = "TPEX"
    elif fault == "ends_before_asof":
        board[:] = [row for row in board if not (row["stock_id"] == "1111" and row["date"] == fixtures.D)]
    elif fault == "quote_conflict":
        original = next(row for row in own if row["date"] == fixtures.DATES[fixtures.D_INDEX - 6])
        board.append(dict(original, close=original["close"] + 1))
    else:
        next(row for row in reader_case.routes["finmind_price_adj"]
             if row["stock_id"] == "1111" and row["date"] == fixtures.D)["market"] = "TPEX"
    scope = reader_case.load()
    assert reason in _excluded(scope)[("1111", "TWSE")]
    assert "1111" not in set(scope["panel"]["stock_id"])
    assert scope["coverage"]["eligible"] == len(fixtures.STOCKS) - 1


def test_quality_receives_full_verified_market_axis_after_segment_selection(reader_case, monkeypatch):
    import stock_daily_price_quality as quality

    _change_market(reader_case, "1111", "TWSE", "TPEX", 5)
    original = quality.series_checks
    axes = {}

    def checks(price, board, day, *, expected_sessions=None):
        axes[str(price["stock_id"].iloc[0])] = list(expected_sessions)
        return original(price, board, day, expected_sessions=expected_sessions)

    monkeypatch.setattr(quality, "series_checks", checks)
    reader_case.load()
    assert set(axes) == {stock for stock, _ in fixtures.STOCKS}
    assert all(axis == fixtures._scope_sessions(fixtures.D_INDEX) for axis in axes.values())


def test_data_lagged_prediction_is_shown_but_never_written_to_forward(reader_case, monkeypatch, tmp_path):
    forward = fixtures.forward
    scope = reader_case.load()
    next_day = fixtures.DATES[fixtures.D_INDEX + 1]
    rows = [
        dict(stock_id="1111", market="TWSE", session_date=fixtures.T, data_asof=fixtures.T,
             feature_data_date=fixtures.T, analysis_asof=fixtures.D, horizon=1, target_session_date=fixtures.D,
             prob_up=0.7, expected_return=0.02, prediction_status="predicted", in_sample_lookahead=False,
             data_lagged=True, data_lag_sessions=1),
        dict(stock_id="2222", market="TWSE", session_date=fixtures.D, data_asof=fixtures.D,
             feature_data_date=fixtures.D, analysis_asof=fixtures.D, horizon=1, target_session_date=next_day,
             prob_up=0.6, expected_return=0.01, prediction_status="predicted", in_sample_lookahead=False,
             data_lagged=False, data_lag_sessions=0),
        dict(stock_id="3333", market="TPEX", session_date=fixtures.D, data_asof=fixtures.D,
             feature_data_date=fixtures.D, analysis_asof=fixtures.D, horizon=1, target_session_date=next_day,
             prob_up=0.55, expected_return=0.005, prediction_status="predicted", in_sample_lookahead=True,
             data_lagged=False, data_lag_sessions=0),
    ]
    recorded = []
    monkeypatch.setattr(forward, "sync_retention_refs", lambda *a, **k: {"version_labels": []})
    monkeypatch.setattr(forward, "settle_predictions", lambda *a, **k: {"version_labels": []})
    monkeypatch.setattr(forward, "process_maturity", lambda **k: {"version_labels": []})
    monkeypatch.setattr(forward, "latest_calibration", lambda *a, **k: None)
    monkeypatch.setattr(forward, "apply_forward_calibration", lambda batch, *a, **k: copy.deepcopy(batch))
    monkeypatch.setattr(forward, "record_predictions",
                        lambda batch, *a, **k: recorded.extend(copy.deepcopy(batch)) or {"version_labels": []})
    monkeypatch.setattr(forward, "export_pending_settlement_requirements", lambda *a, **k: {"version_labels": []})
    monkeypatch.setattr(contract, "_forward_target_prices", lambda *a, **k: (None, {"status": "fixture"}))
    result = contract.process_c0_forward(pd.DataFrame(rows), scope, fixtures._active(),
                                        db_path=tmp_path / "forward.sqlite3")
    assert [row["stock_id"] for row in recorded] == ["2222"]
    displayed = {row["stock_id"]: row for row in result["predictions"]}
    assert set(displayed) == {"1111", "2222", "3333"}
    lagged = displayed["1111"]
    assert lagged["prediction_status"] == "predicted" and lagged["data_lagged"] is True
    assert lagged["session_date"] == lagged["feature_data_date"] == fixtures.T
    assert lagged["target_session_date"] == fixtures.D and lagged["analysis_asof"] == fixtures.D
    signal = scope["panel"].loc[lambda frame: frame.stock_id.eq("1111") & frame.session_date.eq(fixtures.T)]
    assert lagged["signal_close"] == pytest.approx(float(signal["adjusted_close"].iloc[0]))
    assert lagged["prob_up"] == 0.7 and lagged["expected_return"] == 0.02
    binding = result["forward_binding"]
    assert binding["skipped_data_lagged"] == 1
    label = next(item for item in binding["version_labels"] if item["code"] == "skipped_data_lagged")
    assert label["actual"] == 1 and label["detail"]["forward_write"] == "skipped"
    assert any(item["code"] == "in_sample_lookahead_not_recorded" for item in binding["version_labels"])
