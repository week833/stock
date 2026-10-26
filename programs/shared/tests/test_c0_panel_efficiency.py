"""Synthetic scope equivalence and bounded selection work for C0 panel loading."""
from __future__ import annotations

import copy
from datetime import datetime, timedelta, timezone
from pathlib import Path
import sys
import types

import numpy as np
import pandas as pd
import pytest

TEST_DIR = Path(__file__).resolve().parent
if str(TEST_DIR) not in sys.path:
    sys.path.insert(0, str(TEST_DIR))

import test_c0_per_market_asof as fixtures  # noqa: E402

contract = fixtures.contract
FIXED_NOW = datetime.fromisoformat(fixtures.D + "T08:00:00+00:00")


def _routes(same_day, scenario):
    routes = fixtures._routes()
    if same_day:
        for index, (stock, market) in enumerate(fixtures.STOCKS):
            if market != "TPEX":
                continue
            closes = fixtures._closes(index)
            close = float(closes[fixtures.D_INDEX])
            for row in routes["finmind_price_adj"]:
                if row["stock_id"] == stock and row["date"] == fixtures.D:
                    row.update(close=close, open=close,
                               spread=close - float(closes[fixtures.D_INDEX - 1]))
            for row in routes["official_market_daily"]:
                if row["stock_id"] == stock and row["date"] == fixtures.D:
                    row["close"] = close
    price, board = routes["finmind_price_adj"], routes["official_market_daily"]
    if scenario == "duplicate_price":
        price.append(copy.deepcopy(next(row for row in price
                                        if row["stock_id"] == "1111" and row["date"] == fixtures.D)))
    elif scenario == "wrong_market":
        duplicate = copy.deepcopy(next(row for row in board
                                       if row["stock_id"] == "1111" and row["date"] == fixtures.D))
        duplicate["market"] = "TPEX"
        board.append(duplicate)
    elif scenario == "missing_stock":
        routes["finmind_price_adj"] = [row for row in price if row["stock_id"] != "1111"]
    elif scenario == "empty_board":
        routes["official_market_daily"] = []
    elif scenario == "empty_price":
        routes = {route: [] for route in routes}
    return routes


def _loader(tmp_path, monkeypatch, *, same_day=False, scenario="clean"):
    routes = _routes(same_day, scenario)

    def query_rows(root, artifact, route, *, stock_ids, start_date, end_date, **_):
        selected = set(stock_ids)
        rows = [row for row in routes[route]
                if row["stock_id"] in selected and start_date <= row["date"] <= end_date]
        return fixtures._Selected(rows, tmp_path, route)

    context = fixtures._context(
        tmp_path, tpex_index=fixtures.D_INDEX if same_day else fixtures.D_INDEX - 1,
    )
    context["read_at_utc"] = FIXED_NOW.isoformat()
    monkeypatch.setitem(sys.modules, "stock_query_projection",
                        types.SimpleNamespace(query_published_rows=query_rows))
    monkeypatch.setattr(contract, "load_c0_published_context",
                        lambda *args, **kwargs: copy.deepcopy(context))
    monkeypatch.setattr(contract, "_taipei_now",
                        lambda now=None: FIXED_NOW.astimezone(timezone(timedelta(hours=8))))

    def load():
        return contract.load_c0_published_panel(
            None, runtime_binding=fixtures._active(), cache_dir=tmp_path,
        )

    return load


def _outcome(load):
    try:
        return {"scope": load()}
    except contract.C0IntegrityError as error:
        return {"error": str(error),
                "coverage": copy.deepcopy(getattr(error, "coverage", None)),
                "assessments": copy.deepcopy(getattr(error, "data_quality_assessments", None))}


def _assert_scope_equal(actual, expected):
    assert actual.keys() == expected.keys()
    pd.testing.assert_frame_equal(actual["panel"], expected["panel"],
                                  check_dtype=True, check_exact=True)
    assert vars(actual["sources"]) == vars(expected["sources"])
    assert actual["sources"].fingerprint == expected["sources"].fingerprint
    for key in actual.keys() - {"panel", "sources"}:
        assert actual[key] == expected[key], key


@pytest.mark.parametrize(("same_day", "scenario"), [
    (True, "clean"), (False, "clean"), (False, "duplicate_price"),
    (False, "wrong_market"), (False, "missing_stock"),
    (False, "empty_board"), (False, "empty_price"),
])
def test_panel_stock_partitions_preserve_scope(tmp_path, monkeypatch, same_day, scenario):
    load = _loader(tmp_path, monkeypatch, same_day=same_day, scenario=scenario)
    real_groupby = pd.DataFrame.groupby

    def reference_groupby(frame, by=None, *args, **kwargs):
        grouped = real_groupby(frame, by, *args, **kwargs)
        if isinstance(by, str) and by == "stock_id" and kwargs.get("sort") is False and frame["stock_id"].nunique() > 1:
            # Reference membership uses the former whole-column equality predicate.
            indices = {stock: np.flatnonzero((frame["stock_id"] == stock).to_numpy())
                       for stock in frame["stock_id"].unique()}
            return types.SimpleNamespace(indices=indices)
        return grouped

    with monkeypatch.context() as reference_patch:
        reference_patch.setattr(pd.DataFrame, "groupby", reference_groupby)
        expected = _outcome(load)
    actual = _outcome(load)
    assert actual.keys() == expected.keys()
    if "error" in expected:
        assert actual == expected
        assert scenario in {"empty_board", "empty_price"}
    else:
        _assert_scope_equal(actual["scope"], expected["scope"])
        reasons = {row["stock_id"]: row["reason"]
                   for row in actual["scope"]["coverage"]["excluded"]}
        if scenario == "duplicate_price":
            assert reasons["1111"] == "duplicate_price_key"
        elif scenario == "wrong_market":
            assert reasons["1111"] == "price_window_official_market_mismatch"
        elif scenario == "missing_stock":
            assert reasons["1111"] == "price_data_unavailable"
        else:
            assert reasons == {}


def test_panel_selection_never_compares_each_stock_with_whole_route(tmp_path, monkeypatch):
    load = _loader(tmp_path, monkeypatch)
    real_equal = pd.Series.__eq__
    real_groupby = pd.DataFrame.groupby
    examined_lengths, partitions = [], []

    def counted_equal(series, other):
        if series.name == "stock_id" and isinstance(other, str):
            examined_lengths.append(len(series))
        return real_equal(series, other)

    def counted_groupby(frame, by=None, *args, **kwargs):
        if isinstance(by, str) and by == "stock_id" and kwargs.get("sort") is False and frame["stock_id"].nunique() > 1:
            partitions.append(len(frame))
        return real_groupby(frame, by, *args, **kwargs)

    monkeypatch.setattr(pd.Series, "__eq__", counted_equal)
    monkeypatch.setattr(pd.DataFrame, "groupby", counted_groupby)
    scope = load()
    assert scope["coverage"]["eligible"] == len(fixtures.STOCKS)
    assert len(partitions) == 2 and all(length > 253 for length in partitions)
    # The earlier TPEX start admits one extra TWSE board day before its own window.
    assert max(examined_lengths, default=0) <= 254
