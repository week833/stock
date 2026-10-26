"""Bounded current C0 reader integrity; all rows and sidecars are synthetic."""
from __future__ import annotations

import copy
import importlib.util
from pathlib import Path
import sys
import types

import pandas as pd
import pytest


SHARED_TESTS = Path(__file__).resolve().parents[2] / "programs" / "shared" / "tests"
_spec = importlib.util.spec_from_file_location(
    "_top10_current_reader_fixtures", SHARED_TESTS / "test_c0_per_market_asof.py",
)
fixtures = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(fixtures)
contract = fixtures.contract


class _Selected(fixtures._Selected):
    def __init__(self, rows, tmp, route, failure=None):
        super().__init__(rows, tmp, route)
        self.failure = failure
        self.close_count = 0
        self.iter_calls = []

    def iter_rows(self, **kwargs):
        self.iter_calls.append(dict(kwargs))
        if kwargs.get("order") == "stock_shard":
            self.metrics.update(iteration_order="stock_shard", stock_sequence_preserved=True,
                                source_sequence_preserved=False)
        if self.failure == "iteration":
            raise RuntimeError("synthetic_iteration_failure")
        return super().iter_rows()

    def close(self):
        self.close_count += 1


@pytest.fixture()
def reader_case(tmp_path, monkeypatch):
    routes = fixtures._routes()
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
    context = fixtures._context(tmp_path, tpex_index=fixtures.D_INDEX)
    identity = context["current_identity"]
    identity.update(verified=True, classification_verified_asof=fixtures.D,
                    classification_observed_at=fixtures.COLLECTED)
    for record in identity["records"]:
        record.update(stock_name=record["stock_id"], source_industry="synthetic",
                      classification_source="synthetic_official",
                      classification_verified_asof=fixtures.D,
                      classification_observed_at=fixtures.COLLECTED)
    context["descriptor"]["snapshot_fingerprint"] = context["sources"].fingerprint
    case = types.SimpleNamespace(routes=routes, context=context, selected=[], failure=None)

    def query_rows(root, artifact, route, *, stock_ids, start_date, end_date, **_):
        selected_stocks = set(stock_ids)
        rows = [row for row in routes[route] if row["stock_id"] in selected_stocks
                and start_date <= row["date"] <= end_date]
        failure = case.failure[1] if case.failure and case.failure[0] == route else None
        selected = _Selected(rows, tmp_path, route, failure)
        case.selected.append(selected)
        return selected

    original_bytes = contract._stable_file_bytes

    def stable_bytes(path):
        for selected in reversed(case.selected):
            if Path(path) in (selected.completion_path, selected.index_manifest_path):
                assert selected.close_count == 0, "query closed before its evidence was materialized"
                break
        if case.failure:
            route, stage = case.failure
            suffix = {"completion": ".completion", "index": ".index"}.get(stage)
            if suffix and Path(path).name == route + suffix:
                raise OSError("synthetic_sidecar_failure")
        return original_bytes(path)

    monkeypatch.setitem(sys.modules, "stock_query_projection",
                        types.SimpleNamespace(query_published_rows=query_rows))
    monkeypatch.setattr(contract, "load_c0_published_context",
                        lambda *args, **kwargs: copy.deepcopy(context))
    monkeypatch.setattr(contract, "_stable_file_bytes", stable_bytes)

    def load():
        return contract.load_c0_published_panel(
            None, runtime_binding=fixtures._active(), cache_dir=tmp_path,
        )

    case.load = load
    return case


def _add_other_market_identity(case):
    record = copy.deepcopy(case.context["current_identity"]["records"][0])
    record["market"] = "TPEX"
    case.context["current_identity"]["records"].append(record)


@pytest.mark.parametrize("field", ["market", "close", "volume"])
@pytest.mark.parametrize("first", [False, True])
def test_official_conflict_excludes_only_affected_pair_in_either_order(reader_case, field, first):
    board = reader_case.routes["official_market_daily"]
    duplicate = copy.deepcopy(next(row for row in board
                                   if row["stock_id"] == "1111" and row["date"] == fixtures.D))
    duplicate[field] = "TPEX" if field == "market" else duplicate[field] + 1
    board.insert(0, duplicate) if first else board.append(duplicate)
    scope = reader_case.load()
    excluded = {(row["stock_id"], row["market"]): row["reason"]
                for row in scope["coverage"]["excluded"]}
    reason = "price_window_official_market_mismatch" if field == "market" else "conflicting_official_board_key"
    assert excluded == {("1111", "TWSE"): reason}
    assert scope["coverage"]["eligible"] == len(fixtures.STOCKS) - 1
    assert scope["source_binding"]["route_statuses"]["finmind_price_adj"] == "COMPLETE"


@pytest.mark.parametrize("first", [False, True])
def test_equal_official_duplicates_preserve_panel(reader_case, first):
    expected = reader_case.load()
    board = reader_case.routes["official_market_daily"]
    duplicate = copy.deepcopy(next(row for row in board
                                   if row["stock_id"] == "1111" and row["date"] == fixtures.D))
    board.insert(0, duplicate) if first else board.append(duplicate)
    actual = reader_case.load()
    pd.testing.assert_frame_equal(actual["panel"], expected["panel"], check_exact=True)
    assert actual["coverage"] == expected["coverage"]


def test_official_conflict_before_own_market_window_does_not_exclude_stock(reader_case):
    context = reader_case.context
    context["asof_by_market"]["TPEX"] = fixtures.T
    context["analysis_asof_by_market"]["TPEX"] = fixtures.T
    context["application_scope"]["body"]["markets"]["TPEx"]["historical_sessions"] = (
        fixtures._scope_sessions(fixtures.D_INDEX - 1)
    )
    expected = reader_case.load()
    board = reader_case.routes["official_market_daily"]
    # TPEX's earlier start admits this TWSE observation into the shared query,
    # but it precedes TWSE's own 253-session input window.
    before_twse_window = fixtures.DATES[fixtures.D_INDEX - 253]
    duplicate = copy.deepcopy(next(row for row in board
                                   if row["stock_id"] == "1111" and row["date"] == before_twse_window))
    duplicate["close"] += 1
    board.append(duplicate)
    actual = reader_case.load()
    assert actual["coverage"]["eligible"] == len(fixtures.STOCKS)
    assert actual["coverage"]["excluded"] == []
    pd.testing.assert_frame_equal(actual["panel"], expected["panel"], check_exact=True)
    assert actual["coverage"] == expected["coverage"]


def test_sparse_optional_price_market_preserves_baseline_pool_and_panel(reader_case):
    expected = reader_case.load()
    price = reader_case.routes["finmind_price_adj"]
    for stock, market in (("1111", "TWSE"), ("3333", "TPEx")):
        next(row for row in price if row["stock_id"] == stock and row["date"] == fixtures.D)["market"] = market
    for offset, value in ((1, None), (2, "")):
        day = fixtures.DATES[fixtures.D_INDEX - offset]
        next(row for row in price if row["stock_id"] == "1111" and row["date"] == day)["market"] = value
    actual = reader_case.load()
    assert actual["coverage"]["requested"] == actual["coverage"]["eligible"] == len(fixtures.STOCKS)
    assert len(actual["panel"]) == len(expected["panel"]) == len(fixtures.STOCKS) * 253
    assert actual["coverage"] == expected["coverage"]
    pd.testing.assert_frame_equal(actual["panel"], expected["panel"], check_exact=True)


@pytest.mark.parametrize("value", ["TPEX", "nan"])
def test_explicit_wrong_price_market_is_not_inherited_from_identity(reader_case, value):
    price = reader_case.routes["finmind_price_adj"]
    next(row for row in price if row["stock_id"] == "1111" and row["date"] == fixtures.D)["market"] = value
    scope = reader_case.load()
    assert {(row["stock_id"], row["market"], row["reason"]) for row in scope["coverage"]["excluded"]} == {
        ("1111", "TWSE", "price_window_identity_market_mismatch"),
    }
    assert scope["coverage"]["eligible"] == len(fixtures.STOCKS) - 1


@pytest.mark.parametrize("outside", ["before", "after"])
def test_price_market_outside_own_window_does_not_exclude_stock(reader_case, outside):
    context = reader_case.context
    context["asof_by_market"]["TPEX"] = fixtures.T
    context["analysis_asof_by_market"]["TPEX"] = fixtures.T
    context["application_scope"]["body"]["markets"]["TPEx"]["historical_sessions"] = (
        fixtures._scope_sessions(fixtures.D_INDEX - 1)
    )
    expected = reader_case.load()
    stock, day, wrong_market = (("1111", fixtures.DATES[fixtures.D_INDEX - 253], "TPEX") if outside == "before"
                                else ("3333", fixtures.D, "TWSE"))
    price = reader_case.routes["finmind_price_adj"]
    next(row for row in price if row["stock_id"] == stock and row["date"] == day)["market"] = wrong_market
    actual = reader_case.load()
    assert actual["coverage"] == expected["coverage"]
    pd.testing.assert_frame_equal(actual["panel"], expected["panel"], check_exact=True)


def test_sparse_price_market_cannot_disambiguate_same_code_pairs(reader_case):
    _add_other_market_identity(reader_case)
    price = reader_case.routes["finmind_price_adj"]
    next(row for row in price if row["stock_id"] == "1111" and row["date"] == fixtures.D)["market"] = "TWSE"
    scope = reader_case.load()
    coverage = scope["coverage"]
    assert coverage["requested"] == len(fixtures.STOCKS) + 1
    assert coverage["eligible"] == len(fixtures.STOCKS) - 1
    assert coverage["eligible"] + len(coverage["excluded"]) == coverage["requested"]
    assert {(row["stock_id"], row["market"], row["reason"]) for row in coverage["excluded"]} == {
        ("1111", "TWSE", "price_stock_market_ambiguous"),
        ("1111", "TPEX", "price_stock_market_ambiguous"),
    }


def test_marketless_price_ambiguity_preserves_pair_accounting(reader_case):
    _add_other_market_identity(reader_case)
    scope = reader_case.load()
    coverage = scope["coverage"]
    assert coverage["requested"] == len(fixtures.STOCKS) + 1
    assert coverage["eligible"] + len(coverage["excluded"]) == coverage["requested"]
    assert {(row["stock_id"], row["market"], row["reason"]) for row in coverage["excluded"]} == {
        ("1111", "TWSE", "price_stock_market_ambiguous"),
        ("1111", "TPEX", "price_stock_market_ambiguous"),
    }
    assert "1111" not in set(scope["panel"]["stock_id"])


def test_market_price_rows_keep_both_verified_pairs(reader_case):
    _add_other_market_identity(reader_case)
    markets = dict(fixtures.STOCKS)
    price = reader_case.routes["finmind_price_adj"]
    for row in price:
        row["market"] = markets[row["stock_id"]]
    for route in ("finmind_price_adj", "official_market_daily"):
        extra = []
        for row in reader_case.routes[route]:
            if row["stock_id"] == "1111":
                changed = dict(row, market="TPEX", close=row["close"] + 20)
                if route == "finmind_price_adj":
                    changed["open"] += 20
                extra.append(changed)
        reader_case.routes[route].extend(extra)
    scope = reader_case.load()
    assert scope["coverage"]["requested"] == scope["coverage"]["eligible"] == len(fixtures.STOCKS) + 1
    assert scope["coverage"]["excluded"] == []
    original = scope["panel"].loc[lambda frame: frame.stock_id.eq("1111")]
    assert set(original["market"]) == {"TWSE", "TPEX"}
    by_market = {market: frame["adjusted_close"].to_numpy()
                 for market, frame in original.groupby("market")}
    assert (by_market["TPEX"] == by_market["TWSE"] + 20).all()


def test_top10_keeps_ambiguous_universe_in_coverage_accounting(reader_case, monkeypatch):
    import data_reader

    _add_other_market_identity(reader_case)
    loaded = reader_case.load()
    active = fixtures._active()
    active["original_model_feature_contract"]["profile_id"] = "C0"
    loaded["original_model_feature_contract"] = copy.deepcopy(active["original_model_feature_contract"])
    calls = []

    def load_panel(*args, **kwargs):
        calls.append(kwargs)
        return loaded

    monkeypatch.setattr(contract, "load_c0_published_panel", load_panel)
    monkeypatch.setattr(data_reader, "prepare_c0_reader", lambda: None)
    monkeypatch.setattr(data_reader, "shared_module", lambda *args: contract)
    inputs = data_reader.load_c0_inputs(runtime_binding=active)
    assert calls[0]["top10_single_build"] is True
    assert len(inputs["universe"]) == inputs["coverage"]["requested"] == len(fixtures.STOCKS) + 1
    assert inputs["coverage"]["eligible"] + len(inputs["coverage"]["excluded"]) == len(inputs["universe"])


def test_quality_version_labels_are_carried_without_exclusion(reader_case, monkeypatch):
    import stock_daily_price_quality as quality

    original_checks = quality.series_checks

    def checks(price, board, day, **kwargs):
        result = original_checks(price, board, day, **kwargs)
        result["version_labels"].append({"code": "synthetic_quality_version", "recorded": 1, "actual": 2})
        return result

    monkeypatch.setattr(quality, "series_checks", checks)
    scope = reader_case.load()
    assert any(label["code"] == "synthetic_quality_version" for label in scope["version_labels"])
    assert scope["coverage"]["excluded"] == []


def test_old_and_multibatch_stamps_are_labels_not_gates(reader_case):
    old = "2019-05-01T00:00:00+00:00"
    for index, row in enumerate(reader_case.routes["finmind_price_adj"]):
        if row["stock_id"] == "1111":
            row["_shared_collected_at"] = old
        elif row["stock_id"] == "2222":
            row["_shared_collected_at"] = f"2019-06-0{index % 5 + 1}T00:00:00+00:00"
    scope = reader_case.load()
    labels = scope["version_labels"]
    assert any(label["code"] == "price_series_stamp_values_exceed_expected" for label in labels)
    stale = [label for label in labels if label["code"] == "price_series_vintage_stale"]
    assert stale and any(example[0] == "1111" for label in stale for example in label["detail"]["examples"])
    assert scope["coverage"]["excluded"] == []


def test_query_closes_after_rows_and_evidence_are_materialized(reader_case):
    scope = reader_case.load()
    assert len(reader_case.selected) == 2
    assert all(selected.close_count == 1 for selected in reader_case.selected)
    assert len(scope["source_binding"]["query_bindings"]) == 2


@pytest.mark.parametrize("stock_shard", [False, True])
def test_private_top10_mode_requests_stock_shard_and_carries_order_metrics(reader_case, stock_shard):
    if stock_shard:
        scope = contract._load_c0_published_panel(
            None, consumer_id="top10", runtime_binding=fixtures._active(),
            _prepared_context=copy.deepcopy(reader_case.context), _stock_shard_order=True,
        )
    else:
        scope = reader_case.load()
    expected_call = {"order": "stock_shard"} if stock_shard else {}
    assert all(selected.iter_calls == [expected_call] for selected in reader_case.selected)
    for binding in scope["source_binding"]["query_bindings"].values():
        metrics = binding["query_metrics"]
        if stock_shard:
            assert metrics["iteration_order"] == "stock_shard"
            assert metrics["stock_sequence_preserved"] is True
            assert metrics["source_sequence_preserved"] is False
        else:
            assert "iteration_order" not in metrics


@pytest.mark.parametrize("route", ["finmind_price_adj", "official_market_daily"])
@pytest.mark.parametrize("stage", ["iteration", "completion", "index"])
def test_query_closes_when_row_or_evidence_loading_fails(reader_case, route, stage):
    reader_case.failure = (route, stage)
    with pytest.raises((RuntimeError, OSError), match="synthetic_"):
        reader_case.load()
    assert reader_case.selected
    assert all(selected.close_count == 1 for selected in reader_case.selected)
