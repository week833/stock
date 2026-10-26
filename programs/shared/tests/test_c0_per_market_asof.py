"""逐市場 asof 推論（2026-10-12 使用者指示 3b：市場最新資料日不同時不得排除落後市場的股票）。

合成兩市場 fixture：TWSE 最新日 D，TPEX 落後一個交易日（T = D 的前一交易日）。
價格 route 裡 TPEX 仍有一筆 D 的「毒」收盤（1e6），用來證明落後市場不會讀到比自己 asof 晚的資料。
"""
from __future__ import annotations

import copy
import sys
import types
from pathlib import Path

import numpy as np
import pandas as pd
import pytest

SHARED_DIR = Path(__file__).resolve().parents[1]
if str(SHARED_DIR) not in sys.path:
    sys.path.insert(0, str(SHARED_DIR))

import stock_daily_contract as contract  # noqa: E402
import stock_daily_linear as linear  # noqa: E402
import stock_daily_forward as forward  # noqa: E402

DATES = pd.bdate_range("2019-01-01", periods=1120).strftime("%Y-%m-%d").tolist()
D_INDEX = 1100
TRAIN_INDEX = 880
D, T = DATES[D_INDEX], DATES[D_INDEX - 1]
STOCKS = (("1111", "TWSE"), ("2222", "TWSE"), ("3333", "TPEX"), ("4444", "TPEX"))
POISON = 1.0e6
BINDING = {"profile_id": "C0", "requirements_version": "synthetic-test",
           "feature_version": "tw-stock-core-features-v1", "policy_sha256": "a" * 64,
           "requirements_raw_sha256": "b" * 64}
COLLECTED = "2019-06-01T00:00:00+00:00"


def _closes(k):
    t = np.arange(D_INDEX + 1)
    return 100 * np.exp(0.14 * np.sin(t / (23 + k * 3) + k) + 0.012 * np.sin(t / 2.5 + k))


def _routes():
    price, board = [], []
    for k, (stock, market) in enumerate(STOCKS):
        closes = _closes(k)
        previous = None
        for i, close in enumerate(closes):
            close = float(close)
            if market == "TPEX" and i == D_INDEX:
                close = POISON  # published later than TPEX's verified day: must never be read
            spread = 0.0 if previous is None else close - previous
            price.append({"stock_id": stock, "date": DATES[i], "close": close, "open": close, "spread": spread,
                          "Trading_Volume": 1000, "_shared_collected_at": COLLECTED})
            board.append({"stock_id": stock, "date": DATES[i], "close": close, "volume": 1000, "market": market})
            previous = close
    return {"finmind_price_adj": price, "official_market_daily": board}


class _Selected:
    def __init__(self, rows, tmp, route):
        self._rows = rows
        self.cache_key = "synthetic-" + route
        self.predicate = {"route": route}
        self.completion_path = tmp / (route + ".completion")
        self.index_manifest_path = tmp / (route + ".index")
        for path in (self.completion_path, self.index_manifest_path):
            path.write_bytes(route.encode("utf-8"))
        self.lineage = {"synthetic": True}
        self.metrics = {"rows": len(rows)}

    def iter_rows(self):
        return iter(copy.deepcopy(self._rows))


class _Sources:
    fingerprint = "synthetic-fingerprint"

    def __init__(self, tmp):
        self.tmp = tmp

    def projection_artifacts(self, route):
        return [{"root": str(self.tmp), "artifact_descriptor": {"path": route, "sha256": "c" * 64, "size": 1},
                 "source_identity": None, "parent_identity": None, "source_id": "synthetic"}]


def _scope_sessions(end_index):
    return DATES[max(0, end_index - 252):end_index + 1]


def _context(tmp, *, tpex_index=D_INDEX - 1):
    tpex_asof = DATES[tpex_index]
    by_market = {"TWSE": D, "TPEX": tpex_asof}
    return {"version_labels": [], "sources": _Sources(tmp),
            "application_scope": {"body": {"scope_id": "synthetic-scope", "markets": {
                "TWSE": {"historical_sessions": _scope_sessions(D_INDEX)},
                "TPEx": {"historical_sessions": _scope_sessions(tpex_index)}}},
                "binding": {"scope": "retention"}, "capture_binding": {"scope": "capture"}},
            "price_max_date": D, "asof": D, "asof_by_market": dict(by_market),
            "analysis_asof_by_market": dict(by_market), "scope_asof": D, "scope_cutoff": D,
            "current_identity": {"records": [{"stock_id": s, "market": m} for s, m in STOCKS],
                                 "source_asof": D, "binding": {"identity": {"sha256": "d" * 64}}},
            "market_sessions": {"TWSE": list(DATES), "TPEX": list(DATES)},
            "calendar_evidence": {"calendar_verified_by_market": {"TWSE": True, "TPEX": True},
                                  "binding": {"verified_market_calendar": {"sha256": "e" * 64}},
                                  "verified_asof": DATES[-1]},
            "required_route_evidence": {"finmind_price_adj": {"status": "COMPLETE"}},
            "readiness": {"status": "READY"},
            "descriptor": {"sources": [{"manifest_sha256": "f" * 64}],
                           "artifacts": [{"route_id": "finmind_price_adj", "sha256": "c" * 64}]}}


def _active():
    return {"policy_binding": dict(BINDING), "current_policy_binding": dict(BINDING),
            "original_model_feature_contract": {"synthetic": True}, "contract_adoption_ref": {"synthetic": True},
            "effective_C0_semantics_projection_sha256": "1" * 64,
            "original_effective_C0_semantics_projection_sha256": "1" * 64,
            "contract_adoption_verified": True, "model_activation_epoch": "2019-01-01T00:00:00+00:00",
            "artifact_binding": {"sha256": "2" * 64, "feature_schema": linear.FEATURE_SCHEMA,
                                 "feature_order": list(linear.FEATURE_ORDER)}}


@pytest.fixture(scope="module")
def model():
    """A real C0 baseline trained on data strictly before the analysed window's end."""
    records = []
    for k, (stock, market) in enumerate(STOCKS):
        for i, close in enumerate(_closes(k)[:TRAIN_INDEX + 1]):
            records.append({"stock_id": stock, "market": market, "session_date": DATES[i], "adjusted_close": float(close)})
    evidence = {"current_ordinary_verified": True, "price_scope_verified": True, "snapshot_verified": True,
                "calendar_verified_by_market": {"TWSE": True, "TPEX": True},
                "eligible_stock_market_pairs": [list(pair) for pair in STOCKS], "known_future_information": False,
                "historical_membership_status": "unknown", "pit_status": "unknown", "corporate_actions_status": "unknown",
                "source_binding": {"synthetic_fixture": True}}
    prepared = linear.build_panel(pd.DataFrame(records), {"TWSE": DATES, "TPEX": DATES}, asof=DATES[TRAIN_INDEX],
                                  horizons=[1, 5], evidence=evidence, policy_binding=BINDING)
    return linear.fit_baseline(prepared, compare_windows=False)


@pytest.fixture()
def loaded_scope(tmp_path, monkeypatch):
    def load(**overrides):
        routes = _routes()

        def query_published_rows(root, artifact, route, *, stock_ids, start_date, end_date, **_):
            rows = [row for row in routes[route] if row["stock_id"] in set(stock_ids)
                    and start_date <= row["date"] <= end_date]
            return _Selected(rows, tmp_path, route)

        monkeypatch.setitem(sys.modules, "stock_query_projection",
                            types.SimpleNamespace(query_published_rows=query_published_rows))
        context = _context(tmp_path, **overrides)
        monkeypatch.setattr(contract, "load_c0_published_context", lambda *a, **k: copy.deepcopy(context))
        return contract.load_c0_published_panel(None, runtime_binding=_active(), cache_dir=tmp_path)
    return load


def test_lagging_market_stocks_are_kept_with_their_own_asof(loaded_scope):
    scope = loaded_scope()
    assert scope["coverage"]["excluded"] == []
    assert sorted(map(tuple, scope["evidence"]["eligible_stock_market_pairs"])) == sorted(STOCKS)
    assert scope["asof"] == D
    assert scope["asof_by_market"] == {"TWSE": D, "TPEX": T}
    assert scope["coverage"]["inference_asof_by_market"] == {"TWSE": D, "TPEX": T}
    assert scope["source_binding"]["scope"]["end_by_market"] == {"TWSE": D, "TPEX": T}
    panel = scope["panel"]
    for market, end in (("TWSE", D), ("TPEX", T)):
        rows = panel.loc[panel["market"] == market]
        # Exactly the market's own 253-session window, ending on its own day.
        for _, group in rows.groupby("stock_id"):
            assert group["session_date"].tolist() == _scope_sessions(DATES.index(end))
    assert not (panel["adjusted_close"] == POISON).any()
    assert panel.loc[panel["market"] == "TPEX", "session_date"].max() == T


def test_market_asof_label_records_both_dates(loaded_scope):
    scope = loaded_scope()
    labels = [label for label in scope["version_labels"] if label["code"] == "market_asof_differs_per_market_inference"]
    assert len(labels) == 1
    assert labels[0]["recorded"] == D and labels[0]["actual"] == {"TWSE": D, "TPEX": T}
    assert labels[0]["detail"]["stocks_by_market"] == {"TPEX": 2, "TWSE": 2}
    assert not any(label["code"] == "market_asof_differs_stocks_excluded" for label in scope["version_labels"])
    data_time = scope["data_time"]
    assert data_time["analysis_asof"] == D
    assert data_time["asof_by_market"] == {"TWSE": D, "TPEX": T}


def test_each_market_is_predicted_with_its_own_asof_and_no_later_feature(loaded_scope, model):
    scope = loaded_scope()
    parts = contract.panel_by_market(scope)
    assert {market: day for market, (_, day) in parts.items()} == {"TPEX": T, "TWSE": D}
    prepared, predictions = contract.predict_c0_by_market(model, scope, horizons=[1, 5])
    assert prepared["TPEX"]["metadata"]["asof"] == T and prepared["TWSE"]["metadata"]["asof"] == D
    assert prepared["TPEX"]["features"]["session_date"].max() == T
    tpex = predictions.loc[predictions["market"] == "TPEX"]
    twse = predictions.loc[predictions["market"] == "TWSE"]
    assert set(tpex["session_date"]) == {T} and set(tpex["data_asof"]) == {T}
    assert set(twse["session_date"]) == {D} and set(twse["data_asof"]) == {D}
    assert (predictions["prediction_status"] == "predicted").all()
    # Same values as an independent TPEX-only build at T from rows <= T (no later day used).
    subset, _ = parts["TPEX"]
    direct = linear.predict_baseline(model, linear.build_panel(subset, scope["market_sessions"], asof=T, horizons=[1, 5],
        evidence=scope["evidence"], policy_binding=BINDING))
    pd.testing.assert_frame_equal(tpex.reset_index(drop=True)[direct.columns], direct.reset_index(drop=True),
                                  check_dtype=False)
    # Deterministic (stock_id, market, horizon) order, as one single call would give.
    keys = list(zip(predictions["stock_id"], predictions["market"], predictions["horizon"]))
    assert keys == sorted(keys)
    # Batched build gives the same rows.
    _, batched = contract.predict_c0_by_market(model, scope, horizons=[1, 5], batch_pairs=1)
    pd.testing.assert_frame_equal(batched, predictions, check_dtype=False)


def test_one_global_asof_over_both_markets_is_rejected_by_forward(loaded_scope, model, monkeypatch, tmp_path):
    """A naive caller using scope["asof"] for TPEX would predict a day TPEX has not verified."""
    scope = loaded_scope()
    rows = [{"stock_id": "3333", "market": "TPEX", "session_date": D, "horizon": 1}]
    with pytest.raises(contract.C0IntegrityError, match="c0_forward_prediction_after_market_asof:TPEX"):
        contract.process_c0_forward(rows, scope, _active(), db_path=tmp_path / "forward.sqlite3")


def test_forward_settles_and_records_each_market_with_its_own_asof(loaded_scope, model, monkeypatch, tmp_path):
    scope = loaded_scope()
    _, predictions = contract.predict_c0_by_market(model, scope, horizons=[1, 5])
    calls, recorded = [], []

    def settle(market_sessions, prices, *, asof, scope_pairs, evidence, db_path, retention_refs):
        prices = list(prices)
        calls.append({"asof": asof, "markets": sorted({p[1] for p in scope_pairs}),
                      "max_price_day": max(p["session_date"] for p in prices), "end": evidence["price_scope_end"]})
        assert all(p["session_date"] <= asof for p in prices)
        return {"observed": 1, "waiting": 0, "version_labels": [],
                "row_markers": {"basis_changed": {"count": 1, "record_ids": [asof]}}}

    monkeypatch.setattr(forward, "sync_retention_refs", lambda *a, **k: {"version_labels": []})
    monkeypatch.setattr(forward, "settle_predictions", settle)
    monkeypatch.setattr(forward, "process_maturity", lambda **k: {"version_labels": []})
    monkeypatch.setattr(forward, "latest_calibration", lambda *a, **k: None)
    monkeypatch.setattr(forward, "apply_forward_calibration", lambda rows, calibration, **k: [dict(r) for r in rows])
    monkeypatch.setattr(forward, "record_predictions",
                        lambda rows, bindings, **k: recorded.extend(rows) or {"version_labels": []})
    monkeypatch.setattr(forward, "export_pending_settlement_requirements",
                        lambda path, axes, **k: {"asof_by_market": k["asof_by_market"], "version_labels": []})
    result = contract.process_c0_forward(predictions, scope, _active(), db_path=tmp_path / "forward.sqlite3")
    assert sorted((c["markets"][0], c["asof"], c["end"], c["max_price_day"]) for c in calls) == [
        ("TPEX", T, T, T), ("TWSE", D, D, D)]
    settlement = result["forward_binding"]["settlement"]
    assert settlement["observed"] == 2 and settlement["asof_by_market"] == {"TPEX": T, "TWSE": D}
    assert settlement["row_markers"]["basis_changed"]["count"] == 2
    assert result["forward_binding"]["pending_settlement_export"]["asof_by_market"] == {"TWSE": D, "TPEX": T}
    tpex = [row for row in recorded if row["market"] == "TPEX"]
    assert tpex and {row["session_date"] for row in tpex} == {T}
    closes = {k: float(c[D_INDEX - 1]) for k, c in ((2, _closes(2)), (3, _closes(3)))}
    for row in tpex:
        expected = closes[2 if row["stock_id"] == "3333" else 3]
        assert row["signal_close"] == pytest.approx(expected) and row["signal_close"] != POISON


def test_each_market_settles_only_within_its_own_window(loaded_scope):
    """TPEX's window starts one session earlier; TWSE must not settle (as unobserved) a target
    on that earlier day, and an accepted TWSE target there is not treated as panel-covered."""
    scope = loaded_scope()
    tpex_start, twse_start = _scope_sessions(D_INDEX - 1)[0], _scope_sessions(D_INDEX)[0]
    assert tpex_start < twse_start
    calls = []

    def settle(market_sessions, prices, *, asof, scope_pairs, evidence, db_path, retention_refs):
        prices = list(prices)
        calls.append((sorted({p[1] for p in scope_pairs})[0], evidence["price_scope_start"], evidence["price_scope_end"]))
        assert all(evidence["price_scope_start"] <= p["session_date"] <= evidence["price_scope_end"] for p in prices)
        return {"observed": 0, "version_labels": []}

    fake = types.SimpleNamespace(settle_predictions=settle)
    prices = scope["panel"][["stock_id", "market", "session_date", "adjusted_close"]].to_dict("records")
    evidence = {"price_scope_start": tpex_start, "price_scope_end": D}
    contract._settle_by_market(fake, scope, prices, evidence, contract._market_asof_map(scope), None, None)
    assert sorted(calls) == [("TPEX", tpex_start, T), ("TWSE", twse_start, D)]
    accepted = [{"record_id": "older", "stock_id": "1111", "market": "TWSE", "target_date": tpex_start,
                 "purpose": "not_forward"},
                {"record_id": "covered", "stock_id": "1111", "market": "TWSE", "target_date": twse_start,
                 "purpose": "not_forward"}]
    inputs = dict(scope, application_scope={"body": {"forward_settlement": accepted}})
    _, binding = contract._forward_target_prices(inputs, {}, dict(evidence,
        calendar_verified_by_market={"TWSE": True, "TPEX": True}))
    # The older target goes through endpoint qualification; the covered one is skipped.
    assert [item["record_id"] for item in binding["blocked"]] == ["older"]


def test_validation_uses_the_validated_stocks_market_asof(loaded_scope):
    scope = loaded_scope()
    assert contract._stock_market_asof(scope, "3333") == T
    assert contract._stock_market_asof(scope, "1111") == D
    assert contract._stock_market_asof(scope, "9999") == D


def test_single_common_asof_is_unchanged(loaded_scope, model):
    scope = loaded_scope(tpex_index=D_INDEX)
    assert scope["asof_by_market"] == {"TWSE": D, "TPEX": D}
    assert "inference_asof_by_market" not in scope["coverage"]
    assert "end_by_market" not in scope["source_binding"]["scope"]
    assert "asof_by_market" not in scope["source_binding"]
    assert not any(label["code"].startswith("market_asof_differs") for label in scope["version_labels"])
    # Here D is TPEX's own verified day, so its D row is legitimately read.
    assert scope["coverage"]["excluded"] == []
    _, predictions = contract.predict_c0_by_market(model, scope, horizons=[1, 5])
    single = linear.predict_baseline(model, linear.build_panel(scope["panel"], scope["market_sessions"], asof=scope["asof"],
        horizons=[1, 5], evidence=scope["evidence"], policy_binding=BINDING))
    pd.testing.assert_frame_equal(predictions, single)


def test_lagging_market_without_an_exact_window_is_still_excluded(loaded_scope):
    """KEEP: the 253-session window gate still applies per market."""
    scope = loaded_scope(tpex_index=200)
    reasons = {item["stock_id"]: item["reason"] for item in scope["coverage"]["excluded"]}
    assert reasons == {"3333": "market_calendar_exact_253_sessions_unavailable",
                       "4444": "market_calendar_exact_253_sessions_unavailable"}


# --- 18-month acquisition axis / 253-session analysis view -------------------------

ACQUISITION_SESSIONS = 370  # ~18 calendar months of synthetic verified sessions (> 253)


def _retention_document(*, data):
    """Producer retention scope grammar: new (data_* + >=253) or legacy (exactly 253)."""
    import stock_daily_application_scope as application_scope

    ref = {"ref": "_fixture/ref.json", "sha256": "d" * 64, "size": 1}
    markets = {}
    for market in ("TWSE", "TPEx"):
        history = DATES[D_INDEX - (ACQUISITION_SESSIONS - 1 if data else 252):D_INDEX + 1]
        item = {"historical_sessions": history, "historical_session_sha256": application_scope._digest(history),
                "future_sessions": [], "calendar_ref": dict(ref), "future_calendar_ref": None}
        if data:
            item.update(data_start=history[0], data_end=history[-1], data_sessions=list(history))
        markets[market] = item
    body = {"schema": "twstock.application-retention-scope.v1", "schema_version": 1, "profile_id": "C0",
            "revision": 1, "asof": D, "capture_scope_id": "e" * 64, "scope_certainty": "known",
            "missing_proofs": [], "active_consumers": [],
            "allowed_stock_market_pairs": [{"stock_id": stock, "market": "TPEx" if market == "TPEX" else market}
                                           for stock, market in STOCKS],
            "markets": markets,
            "route_rules": {route: {"capture_allowed": True, "event_dates": [], "basis_anchor_keys": [],
                                    "missing_scope_proofs": []} for route in sorted(application_scope.ROUTES)},
            "forward_settlement": [], "raw_unit_exceptions": [], "historical_observations": [],
            "ordinary_universe_ref": dict(ref), "requirements_ref": dict(ref)}
    body["scope_id"] = application_scope._digest(application_scope.retention_scope_domain_projection(body))
    body["document_sha256"] = application_scope._digest(body)
    return body


def _calendar_proof(retention, *, data):
    """Verified calendar v2 grammar bound to that retention scope (no I/O)."""
    import hashlib
    import stock_daily_calendar as calendar

    def axis_ref(sessions):
        raw = calendar.axis_bytes(sessions)
        digest = hashlib.sha256(raw).hexdigest()
        return {"ref": "_calendar/axes/" + digest + ".json", "sha256": digest, "size": len(raw)}

    content = {"ref": "_calendar/receipts/" + "f" * 64 + ".json", "sha256": "f" * 64, "size": 1}
    markets, digests = {}, {}
    for market, item in retention["markets"].items():
        history = list(item["historical_sessions"])
        markets[market] = {"calendar_verified": True, "future_axis_verified": False,
                           "historical_sessions": history, "future_sessions": [], "sessions": history,
                           "axis_ref": axis_ref(history), "historical_axis_ref": axis_ref(history),
                           "future_axis_ref": None, "axis_sha256": axis_ref(history)["sha256"]}
        if data:
            markets[market].update(data_start=item["data_start"], data_end=item["data_end"], data_sessions=history)
        digests[market] = calendar._digest(history)
    urls = {"TWSE": "https://www.twse.com.tw/holidaySchedule", "TPEx": "https://www.tpex.org.tw/holiday"}
    body = {"schema": calendar.SCHEMA, "schema_version": 2, "verified_asof": D,
            "scope_id": retention["scope_id"],
            "retention_scope_binding": {"scope_id": retention["scope_id"],
                                        "capture_scope_id": retention["capture_scope_id"], "asof": D,
                                        "market_session_sha256": digests},
            "historical_axis_verified": True, "future_targets_verified": False, "markets": markets,
            "source_receipt_ref": dict(content),
            "sources": [{"market": market, "source_url": urls[market],
                         "native_unit": {"kind": "calendar_year", "market": market, "year": 2023},
                         "observed_at": COLLECTED, "raw_ref": dict(content), "http_receipt_ref": dict(content)}
                        for market in calendar.MARKETS],
            "retired_historical_observations": [],
            "derivation": {"parent_proof_sha256": "a" * 64, "retained_values_unchanged": True,
                           "market_session_sha256": digests}}
    body["proof_sha256"] = calendar._digest(body)
    return body


def _load_panel(tmp_path, monkeypatch, retention, *, tpex_index, drop=()):
    routes = _routes()
    routes["finmind_price_adj"] = [row for row in routes["finmind_price_adj"]
                                   if (row["stock_id"], row["date"]) not in set(drop)]

    def query_published_rows(root, artifact, route, *, stock_ids, start_date, end_date, **_):
        rows = [row for row in routes[route] if row["stock_id"] in set(stock_ids)
                and start_date <= row["date"] <= end_date]
        return _Selected(rows, tmp_path, route)

    monkeypatch.setitem(sys.modules, "stock_query_projection",
                        types.SimpleNamespace(query_published_rows=query_published_rows))
    context = _context(tmp_path, tpex_index=tpex_index)
    context["application_scope"]["body"] = copy.deepcopy(retention)
    monkeypatch.setattr(contract, "load_c0_published_context", lambda *a, **k: copy.deepcopy(context))
    return contract.load_c0_published_panel(None, runtime_binding=_active(), cache_dir=tmp_path)


def _windows(scope):
    panel = scope["panel"]
    return {(stock, market): group["session_date"].tolist()
            for (stock, market), group in panel.groupby(["stock_id", "market"])}


def test_18month_scope_and_calendar_read_with_last_253_session_view(tmp_path, monkeypatch):
    import stock_daily_application_scope as application_scope
    import stock_daily_calendar as calendar

    new, legacy = _retention_document(data=True), _retention_document(data=False)
    # Both the new 18-month and the legacy exact-253 grammar are readable.
    for document, length in ((new, ACQUISITION_SESSIONS), (legacy, 253)):
        labels = []
        body = application_scope.validate_application_retention_scope(document, labels)
        assert all(len(item["historical_sessions"]) == length for item in body["markets"].values())
        assert not any(label["code"] == "scope_identity_not_recomputable" for label in labels)
        proof = calendar.validate_verified_market_calendar_v2(_calendar_proof(document, data=length > 253))
        assert all(len(item["historical_sessions"]) == length for item in proof["markets"].values())
    # Same-day branch: each market's last 253 official sessions, not the 18-month axis.
    same_day = _load_panel(tmp_path, monkeypatch, new, tpex_index=D_INDEX)
    assert same_day["coverage"]["excluded"] == []
    assert all(days == _scope_sessions(D_INDEX) for days in _windows(same_day).values())
    legacy_same_day = _load_panel(tmp_path, monkeypatch, legacy, tpex_index=D_INDEX)
    assert _windows(legacy_same_day) == _windows(same_day)
    # Lagging branch: TPEX ends at its own analysed day, still exactly 253 sessions.
    lagging = _load_panel(tmp_path, monkeypatch, new, tpex_index=D_INDEX - 1)
    for (stock, market), days in _windows(lagging).items():
        assert days == _scope_sessions(D_INDEX if market == "TWSE" else D_INDEX - 1)
    assert not (lagging["panel"]["adjusted_close"] == POISON).any()
    # Insufficiency is per stock: a hole inside the last 253 sessions excludes only
    # that stock; a hole only in the earlier acquisition months does not.
    early_hole = DATES[D_INDEX - ACQUISITION_SESSIONS + 5]
    late_hole = DATES[D_INDEX - 10]
    partial = _load_panel(tmp_path, monkeypatch, new, tpex_index=D_INDEX - 1,
                          drop=[("2222", early_hole), ("4444", late_hole)])
    assert [item["stock_id"] for item in partial["coverage"]["excluded"]] == ["4444"]
    assert sorted(stock for stock, _ in _windows(partial)) == ["1111", "2222", "3333"]
    assert _windows(partial)[("2222", "TWSE")] == _scope_sessions(D_INDEX)


def test_current_stock_history_selector_preserves_legacy_pair_selectors():
    import stock_daily_application_scope as application_scope

    retention = _retention_document(data=True)
    retention.update(source_unit_domain_version="compact-necessary-keys-v1", necessary_keys_basis={})
    legacy = application_scope._retention_capture_context(retention)
    assert "history_membership" not in legacy["route_categories"]["official_market_daily"]
    membership = "current_stock_ids_any_supported_market"
    retention["route_rules"]["official_market_daily"]["history_membership"] = membership
    scope = application_scope._retention_capture_context(retention)
    assert scope["route_categories"]["official_market_daily"]["history_membership"] == membership
    pairs = scope["allowed_stock_market_pairs"]
    axes = {}
    for market, window in scope["market_windows"].items():
        axes[market] = {name: copy.deepcopy(window[name]) for name in
                       ("asof", "start", "end", "exact_window_sessions", "calendar_ref", "future_calendar_ref")}
        axes[market].update(sessions_sha256=application_scope._digest(window["sessions"]),
                            future_sessions_sha256=application_scope._digest(window["future_sessions"]),
                            data_start=window["data_start"], data_end=window["data_end"],
                            data_sessions_sha256=application_scope._digest(window["data_sessions"]))
    basis = {"schema": "twstock-updater.necessary-keys-basis.v1", "schema_version": 1,
             "positive_ordinary": True, "ordinary_universe_ref": scope["ordinary_universe_ref"],
             "ordered_pairs": pairs, "market_axes": axes}
    domain = {key: value for key, value in basis.items() if key != "ordinary_universe_ref"}
    domain["market_axes"] = {market: {key: value for key, value in axis.items() if not key.endswith("_ref")}
                             for market, axis in axes.items()}
    basis["basis_sha256"] = application_scope._digest(domain)
    scope.update(source_unit_domain_version="compact-necessary-keys-v1", necessary_keys_basis=basis)
    day = scope["market_windows"]["TWSE"]["data_sessions"][0]
    markets = ["TWSE", "TPEx"]
    expected = {
        "ordinary_stock_day": [{"stock_id": stock, "market": market, "date": day}
                               for market in markets for stock in ("1111", "2222", "3333", "4444")],
        "ordinary_day": [dict(pair, date=day) for market in markets for pair in pairs if pair["market"] == market],
        "ordinary_identity": [dict(pair) for pair in pairs],
    }
    declarations = []
    for kind, values in expected.items():
        selector = {"schema": "twstock-updater.necessary-keys-selector.v1", "schema_version": 1,
                    "kind": kind, "basis_sha256": basis["basis_sha256"], "markets": markets,
                    "expanded_count": len(values), "expanded_sha256": application_scope._digest(values)}
        if kind != "ordinary_identity":
            selector["date"] = day
        declarations.append({"route_id": "finmind_stock_info" if kind == "ordinary_identity" else "official_market_daily",
                             "unit_kind": "identity_snapshot" if kind == "ordinary_identity" else "market_day",
                             "unit_key": kind, "unit_scope": {} if kind == "ordinary_identity" else {"date": day},
                             "necessary_keys": selector})
    labels = []
    application_scope._validate_compact_necessary_keys(scope, declarations, labels)
    assert labels == []
    projection = application_scope.scope_domain_projection({"route_categories": scope["route_categories"]})
    assert projection["route_categories"]["official_market_daily"]["history_membership"] == membership
