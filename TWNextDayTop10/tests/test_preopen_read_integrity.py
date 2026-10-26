"""Pre-open identity, date and CSV decoding integrity; all inputs are synthetic."""
from __future__ import annotations

import hashlib
import json

import numpy as np
import pandas as pd
import pytest

import preopen_source


SIGNAL = "2026-10-16"
TARGET = "2026-10-19"


def _frame(*pairs):
    rows = []
    for index, (market, stock) in enumerate(pairs):
        rows.append(dict(signal_date=SIGNAL, target_date=TARGET, market=market, stock_id=stock,
                         p_up=0.8 - index * 0.1, p_up_raw=0.8 - index * 0.1,
                         exp_ret=0.02 - index * 0.001, exp_ret_raw=0.02 - index * 0.001,
                         rank=index + 1, rank_tradable=index + 1, tradable=True,
                         signal_close=100.0, adj_close=100.0, amt20=30_000_000.0,
                         feature_coverage=1.0))
    return pd.DataFrame(rows)


def _snapshot(frame):
    reader = preopen_source.load_reader()
    meta = dict(signal_date=SIGNAL, target_date=TARGET, model_sha256="f" * 64,
                snapshot_sha256="e" * 64, us_overnight={"status": "ok"}, gate_us_up=True)
    return reader, reader.PreopenSnapshot(meta=meta, frame=frame)


def test_pair_identity_keeps_snapshot_pool_and_c0_coverage_union():
    reader, snapshot = _snapshot(_frame(("TWSE", "2330"), ("TPEx", "2330")))
    preopen_source.validate_snapshot(reader, snapshot)
    universe = pd.DataFrame([
        dict(market="TWSE", stock_id="2330", name="Listed name"),
        dict(market="TPEX", stock_id="9999", name="C0 only"),
    ])
    c0 = pd.DataFrame([dict(market="TWSE", stock_id="2330", name="Listed name",
                            probability=0.55, expected_return=0.01, prediction_status="predicted")])
    coverage = universe.assign(status=["predicted", "not_enough_history"])

    predictions, actual_coverage = preopen_source.build_view(snapshot, c0, universe, coverage, engine_id="test")
    rows = {(row.market, row.stock_id): row for row in predictions.itertuples()}
    assert set(rows) == {("TWSE", "2330"), ("TPEX", "2330")}
    assert rows[("TWSE", "2330")].name == "Listed name"
    assert rows[("TWSE", "2330")].c0_probability == pytest.approx(0.55)
    assert bool(rows[("TWSE", "2330")].in_c0_universe)
    assert rows[("TPEX", "2330")].name is None
    assert pd.isna(rows[("TPEX", "2330")].c0_probability)
    assert not bool(rows[("TPEX", "2330")].in_c0_universe)
    actual = {(row.market, row.stock_id): row.status for row in actual_coverage.itertuples()}
    assert actual == {("TWSE", "2330"): "preopen_predicted", ("TPEX", "2330"): "preopen_predicted",
                      ("TPEX", "9999"): "preopen_not_in_snapshot"}


def test_pair_identity_matches_each_market_c0_values():
    reader, snapshot = _snapshot(_frame(("TWSE", "2330"), ("TPEx", "2330")))
    preopen_source.validate_snapshot(reader, snapshot)
    universe = pd.DataFrame([dict(market="TWSE", stock_id="2330", name="Listed"),
                             dict(market="TPEX", stock_id="2330", name="OTC")])
    c0 = universe.assign(probability=[0.51, 0.61], expected_return=[0.01, 0.02], prediction_status="predicted")
    predictions, coverage = preopen_source.build_view(snapshot, c0, universe,
                                                     universe.assign(status="predicted"), engine_id="test")
    rows = {(row.market, row.stock_id): row for row in predictions.itertuples()}
    assert rows[("TWSE", "2330")].name == "Listed"
    assert rows[("TPEX", "2330")].name == "OTC"
    assert rows[("TWSE", "2330")].c0_probability == pytest.approx(0.51)
    assert rows[("TPEX", "2330")].c0_probability == pytest.approx(0.61)
    assert len(coverage) == 2


@pytest.mark.parametrize("pairs,reason", [
    ((("TPEx", "2330"), ("TPEX", "2330")), "preopen_duplicate_stock"),
    ((("UNKNOWN", "2330"),), "preopen_snapshot_market"),
])
def test_duplicate_pair_and_unknown_market_are_rejected(pairs, reason):
    reader, snapshot = _snapshot(_frame(*pairs))
    with pytest.raises(reader.PreopenUnavailable) as error:
        preopen_source.validate_snapshot(reader, snapshot)
    assert error.value.reason == reason


@pytest.mark.parametrize("column,value", [
    ("signal_date", "2026-10-15"),
    ("signal_date", "2026-10-19"),
    ("target_date", "2026-10-20"),
    ("signal_date", np.nan),
    ("target_date", ""),
    ("target_date", "2026/10/19"),
])
def test_row_dates_are_validated_in_all_source_modes(monkeypatch, column, value):
    frame = _frame(("TWSE", "2330"))
    frame.loc[0, column] = value
    reader, snapshot = _snapshot(frame)
    reads = []

    def load(**kwargs):
        reads.append(kwargs)
        return snapshot

    monkeypatch.setattr(reader, "load_latest", load)
    monkeypatch.setattr(preopen_source, "prepared_reader", lambda *_: (reader, "fixture"))
    choice = preopen_source.choose("auto", "cli", SIGNAL)
    assert not choice.active and choice.reason == "preopen_snapshot_dates"
    with pytest.raises(preopen_source.PreopenRequiredError, match="preopen_snapshot_dates"):
        preopen_source.choose("preopen", "cli", SIGNAL)
    assert preopen_source.choose("c0", "cli", SIGNAL).status == "not_requested"
    assert len(reads) == 2


class _NoParquetPandas:
    def __getattr__(self, name):
        return getattr(pd, name)

    @staticmethod
    def read_parquet(*args, **kwargs):
        raise ImportError("synthetic parquet engine missing")


def _force_csv(monkeypatch):
    reader = preopen_source.load_reader()
    monkeypatch.setattr(reader, "pd", _NoParquetPandas())
    monkeypatch.setattr(preopen_source, "_parquet_engine", lambda: None)
    return reader


def _csv_values(preopen_fixture, meta, values):
    path = preopen_fixture.root / meta["snapshot_csv"]
    frame = pd.read_csv(path, dtype={"stock_id": str})
    frame["tradable"] = pd.Series(values, dtype=object)
    frame.to_csv(path, index=False, encoding="utf-8")
    meta = dict(meta, snapshot_csv_sha256=hashlib.sha256(path.read_bytes()).hexdigest())
    latest = preopen_fixture.root / "predictions" / "latest.json"
    latest.write_text(json.dumps(meta, ensure_ascii=False), encoding="utf-8")
    return path


def test_csv_without_parquet_engine_is_available_and_tampering_is_rejected(preopen_fixture, monkeypatch):
    meta = preopen_fixture(signal_date=SIGNAL, target_date=TARGET, stock_ids=["2330", "2317"],
                           markets=["TWSE", "TWSE"], seed=7)
    _force_csv(monkeypatch)
    choice = preopen_source.choose("auto", "cli", SIGNAL)
    assert choice.active
    assert choice.engine is None
    assert choice.status_record()["snapshot_read_format"] == "csv"
    assert choice.preopen_meta()["snapshot_read_format"] == "csv"
    path = preopen_fixture.root / meta["snapshot_csv"]
    path.write_bytes(path.read_bytes() + b"\n")
    assert preopen_source.choose("auto", "cli", SIGNAL).reason == "preopen_snapshot_sha_mismatch"
    with pytest.raises(preopen_source.PreopenRequiredError, match="preopen_snapshot_sha_mismatch"):
        preopen_source.choose("preopen", "cli", SIGNAL)


@pytest.mark.parametrize("values", [(True, False), ("true", "false"), ("1", "0")])
def test_csv_tradable_false_remains_false(preopen_fixture, monkeypatch, values):
    meta = preopen_fixture(signal_date=SIGNAL, target_date=TARGET, stock_ids=["2330", "2317"],
                           markets=["TWSE", "TWSE"], seed=7)
    _csv_values(preopen_fixture, meta, values)
    reader = _force_csv(monkeypatch)
    frame = reader.load_latest(expected_signal_date=SIGNAL).frame
    assert frame["tradable"].tolist() == [True, False]
    assert frame["tradable"].dtype == bool


@pytest.mark.parametrize("value", [np.nan, "not-a-boolean"])
def test_csv_invalid_tradable_is_rejected(preopen_fixture, monkeypatch, value):
    meta = preopen_fixture(signal_date=SIGNAL, target_date=TARGET, stock_ids=["2330", "2317"],
                           markets=["TWSE", "TWSE"], seed=7)
    _csv_values(preopen_fixture, meta, [True, value])
    _force_csv(monkeypatch)
    choice = preopen_source.choose("auto", "cli", SIGNAL)
    assert not choice.active and choice.reason == "preopen_snapshot_values"


def test_parquet_tradable_requires_actual_boolean(preopen_fixture, monkeypatch):
    preopen_fixture(signal_date=SIGNAL, target_date=TARGET, stock_ids=["2330"], markets=["TWSE"], seed=7)
    reader = preopen_source.load_reader()
    frame = _frame(("TWSE", "2330"))

    class SyntheticParquetPandas(_NoParquetPandas):
        @staticmethod
        def read_parquet(*args, **kwargs):
            return frame.copy()

    monkeypatch.setattr(reader, "pd", SyntheticParquetPandas())
    assert reader.load_latest(expected_signal_date=SIGNAL).frame["tradable"].tolist() == [True]
    for value in (False,):
        frame["tradable"] = value
        assert reader.load_latest(expected_signal_date=SIGNAL).frame["tradable"].tolist() == [value]
    for value in (np.nan, "False", "invalid", 1):
        frame["tradable"] = value
        with pytest.raises(reader.PreopenUnavailable) as error:
            reader.load_latest(expected_signal_date=SIGNAL)
        assert error.value.reason == "preopen_snapshot_values"
