"""Price identities need consecutive verified market sessions; no live data is read."""
from __future__ import annotations

import numpy as np
import pandas as pd
import pytest

import stock_daily_price_quality as quality


DAYS = pd.to_datetime(["2026-09-29", "2026-09-30", "2026-10-01", "2026-10-02"])


def _frames(dates, closes, spreads, volumes=None, official_closes=None, stock="1234"):
    dates = pd.to_datetime(dates)
    volumes = [100] * len(dates) if volumes is None else volumes
    price = pd.DataFrame({
        "stock_id": stock, "date": dates, "open": closes, "close": closes,
        "spread": spreads, "Trading_Volume": volumes,
        "_shared_collected_at": "2026-10-23T17:30:00+08:00",
    })
    official = pd.DataFrame({
        "stock_id": stock, "date": dates, "market": "TPEX",
        "close": closes if official_closes is None else official_closes,
        "volume": volumes,
    })
    return price, official


def _labels(result, code):
    return [label for label in result["version_labels"] if label["code"] == code]


@pytest.mark.parametrize("axis", [DAYS, DAYS.strftime("%Y-%m-%d").tolist()])
def test_missing_session_does_not_turn_retained_row_into_previous_close(axis):
    price, official = _frames(DAYS[[0, 1, 3]], [10, 10, 15], [0, 0, 1])

    result = quality.series_checks(price, official, DAYS[-1], expected_sessions=axis)

    assert result["excluded"] == set()
    label, = _labels(result, "price_series_comparison_gap")
    assert label["actual"] == 1
    assert label["detail"]["examples"] == [{
        "stock_id": "1234", "previous_date": "2026-09-30", "date": "2026-10-02",
        "missing_sessions": ["2026-10-01"],
    }]


def test_252_of_253_rows_remain_usable_with_gap_label():
    axis = pd.bdate_range("2025-10-01", periods=253)
    dates = axis.delete(120)
    price, official = _frames(dates, [10] * 252, [0] * 252)

    result = quality.series_checks(price, official, axis[-1], expected_sessions=axis)

    assert result["excluded"] == set()
    label, = _labels(result, "price_series_comparison_gap")
    assert label["detail"]["examples"][0]["missing_sessions"] == [axis[120].date().isoformat()]


@pytest.mark.parametrize("close,spread", [
    (np.nan, 0), (np.inf, 0), (0, 0), (-1, 0), (10, np.nan), (10, np.inf),
])
def test_gap_does_not_hide_invalid_current_price(close, spread):
    price, official = _frames(DAYS[[0, 1, 3]], [10, 10, close], [0, 0, spread])

    result = quality.series_checks(price, official, DAYS[-1], expected_sessions=DAYS)

    assert result["excluded"] == {"1234"}
    assert result["by_check"]["basis_break"] == {"1234"}
    assert ["1234", "2026-10-02"] in result["examples"]["basis_break"]


@pytest.mark.parametrize("stock,days,first_close,next_close,next_spread", [
    ("3718", ["2026-09-03", "2026-09-04"], 78.3, 75.5, -2.8),
    ("7823", ["2026-02-05", "2026-02-06"], 148, 131, -17),
    ("7835", ["2026-07-24", "2026-07-27"], 39.95, 36.4, -3.55),
])
def test_first_priced_row_with_unknown_spread_remains_usable(stock, days, first_close, next_close, next_spread):
    axis = pd.to_datetime(days)
    price, official = _frames(axis, [first_close, next_close], [np.nan, next_spread], stock=stock)

    result = quality.series_checks(price, official, axis[-1], expected_sessions=axis)

    assert result["excluded"] == set()
    assert result["by_check"]["basis_break"] == set()
    assert result["by_check"]["listing_day_raw_row"] == set()


def test_first_known_noquote_row_with_invalid_spread_remains_excluded():
    price, official = _frames(
        DAYS[:2], [10, 11], [np.nan, 1], volumes=[5, 100], official_closes=[np.nan, 11],
    )

    result = quality.series_checks(price, official, DAYS[1], expected_sessions=DAYS)

    assert result["by_check"]["basis_break"] == {"1234"}
    assert ["1234", "2026-09-29"] in result["examples"]["basis_break"]


def test_noquote_anchor_is_reset_across_missing_session_and_rebuilt_at_resume():
    price, official = _frames(
        DAYS[[0, 2, 3]], [10, 15.59, 16.2], [0, 0, .61],
        volumes=[100, 1855, 3403], official_closes=[10, np.nan, 16.2],
    )

    result = quality.series_checks(price, official, DAYS[-1], expected_sessions=DAYS)

    assert result["excluded"] == set()
    assert _labels(result, "price_series_comparison_gap")[0]["actual"] == 1
    label, = _labels(result, "price_series_noquote_anchor_unverified")
    assert label["actual"] == 2
    assert label["detail"]["examples"] == [["1234", "2026-10-01"], ["1234", "2026-10-02"]]


def test_first_quote_after_unverified_anchor_restarts_contiguous_basis_check():
    axis = pd.bdate_range("2026-09-28", periods=6)
    price, official = _frames(
        axis[[0, 2, 3, 4, 5]], [10, 15, 16, 17, 19], [0, 0, 1, 1, 1],
        volumes=[100, 5, 100, 100, 100], official_closes=[10, np.nan, 16, 17, 19],
    )

    result = quality.series_checks(price, official, axis[-1], expected_sessions=axis)

    assert result["by_check"]["basis_break"] == {"1234"}
    assert result["examples"]["basis_break"] == [["1234", axis[5].date().isoformat()]]


def test_contiguous_noquote_anchor_still_rejects_inconsistent_resume():
    price, official = _frames(
        DAYS[:3], [10, 15.59, 16.2], [0, 0, .61],
        volumes=[100, 1855, 3403], official_closes=[10, np.nan, 16.2],
    )

    result = quality.series_checks(price, official, DAYS[2], expected_sessions=DAYS)

    assert result["by_check"]["basis_break"] == {"1234"}
    assert result["examples"]["basis_break"] == [["1234", "2026-10-01"]]


def test_contiguous_carried_noquote_series_is_usable():
    price, official = _frames(
        DAYS[:3], [10, 10, 11], [0, 0, 1],
        volumes=[100, 0, 100], official_closes=[10, np.nan, 11],
    )

    result = quality.series_checks(price, official, DAYS[2], expected_sessions=DAYS)

    assert result["excluded"] == set()
    assert not _labels(result, "price_series_noquote_anchor_unverified")


def test_zero_volume_after_gap_is_unverified_instead_of_noncarry():
    price, official = _frames(
        DAYS[[0, 1, 3]], [10, 10, 15], [0, 0, 0],
        volumes=[100, 100, 0], official_closes=[10, 10, np.nan],
    )

    result = quality.series_checks(price, official, DAYS[-1], expected_sessions=DAYS)

    assert result["excluded"] == set()
    assert result["by_check"]["placeholder_noncarry"] == set()
    assert _labels(result, "price_series_comparison_gap")


@pytest.mark.parametrize("stock,previous,close", [
    ("2235", 28.3, 28.4), ("2926", 30, 30.1), ("3067", 17, 16.55),
    ("3629", 18.2, 17.3), ("4305", 42.15, 41.95), ("4413", 19.3, 19.85),
    ("5276", 17.9, 18.5), ("5276", 18.5, 18.45), ("5276", 18.5, 18.45),
    ("5520", 82, 82.1), ("5601", 30.65, 30), ("5878", 38.05, 38.1),
    ("6198", 19.5, 19.4), ("6212", 33.15, 32.6), ("6228", 17.85, 16.4),
    ("6236", 19.05, 17.5), ("6730", 31.9, 31.8), ("6762", 162, 159),
    ("7713", 73.5, 73.8), ("8047", 46.7, 45.1), ("8102", 59.5, 59),
    ("8917", 52.6, 54), ("8923", 18.65, 18), ("7732", 33.8, 34.45),
    ("7732", 34.3, 33.5),
])
def test_adjacent_noquote_noncarry_values_from_25_audited_events_remain_excluded(stock, previous, close):
    price, official = _frames(
        DAYS[:2], [previous, close], [0, 0], volumes=[100, 0],
        official_closes=[previous, np.nan], stock=stock,
    )

    result = quality.series_checks(price, official, DAYS[1], expected_sessions=DAYS)

    assert result["excluded"] == {stock}
    assert result["by_check"]["placeholder_noncarry"] == {stock}


@pytest.mark.parametrize("previous,close,spread,noquote", [
    (51.2, 47.85, -.35, False), (56.9, 55.5, -.5, True),
])
def test_contiguous_exdividend_basis_conflicts_remain_excluded(previous, close, spread, noquote):
    price, official = _frames(
        DAYS[:3], [previous, previous, close], [0, 0, spread],
        volumes=[100, 177, 100],
        official_closes=[previous, np.nan if noquote else previous, close],
    )

    result = quality.series_checks(price, official, DAYS[2], expected_sessions=DAYS)

    assert result["by_check"]["basis_break"] == {"1234"}
    assert result["examples"]["basis_break"] == [["1234", "2026-10-01"]]


def test_omitting_expected_sessions_preserves_existing_basis_behavior():
    price, official = _frames(DAYS[[0, 1, 3]], [10, 10, 15], [0, 0, 1])

    result = quality.series_checks(price, official, DAYS[-1])
    explicit_none = quality.series_checks(price, official, DAYS[-1], expected_sessions=None)

    assert result == explicit_none
    assert result["by_check"]["basis_break"] == {"1234"}
    assert not _labels(result, "price_series_comparison_gap")


def test_quote_anchor_does_not_cross_stock_boundary():
    price_a, official_a = _frames(DAYS[:2], [10, 10], [0, 0], stock="1111")
    price_b, official_b = _frames(
        DAYS[:2], [20, 21], [0, 1], official_closes=[np.nan, 21], stock="2222",
    )

    result = quality.series_checks(
        pd.concat([price_a, price_b], ignore_index=True),
        pd.concat([official_a, official_b], ignore_index=True), DAYS[1], expected_sessions=DAYS,
    )

    assert result["excluded"] == set()
    assert _labels(result, "price_series_noquote_anchor_unverified")[0]["detail"]["examples"] == [
        ["2222", "2026-09-30"],
    ]
