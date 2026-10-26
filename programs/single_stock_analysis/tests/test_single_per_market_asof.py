"""個股 C0 逐市場 asof（2026-10-12 使用者指示 3b：市場最新資料日不同時，落後市場的股票不得被擋下）。

個股報告用「本股所屬市場」的分析資料日 build_panel／預測／記錄資料時間；
落後市場的股票照常估計並加標籤，不排除，也不使用其他市場較晚的日期。
端到端案例重用 shared 的合成兩市場資料（TWSE 最新 D、TPEX 落後到 T，且 TPEX 在 D 有一筆毒收盤）。
"""
from __future__ import annotations

import copy
import importlib.util
import sys
import types
from pathlib import Path

import pandas as pd
import pytest

import tw_single_stock_c0 as single

_SPEC = importlib.util.spec_from_file_location(
    'shared_per_market_fixture', Path(single.SHARED) / 'tests' / 'test_c0_per_market_asof.py')
pm = importlib.util.module_from_spec(_SPEC)
_SPEC.loader.exec_module(pm)
D, T = pm.D, pm.T


@pytest.fixture(scope='module')
def model():
    records = [{'stock_id': stock, 'market': market, 'session_date': pm.DATES[i], 'adjusted_close': float(close)}
               for k, (stock, market) in enumerate(pm.STOCKS) for i, close in enumerate(pm._closes(k)[:pm.TRAIN_INDEX + 1])]
    evidence = {'current_ordinary_verified': True, 'price_scope_verified': True, 'snapshot_verified': True,
                'calendar_verified_by_market': {'TWSE': True, 'TPEX': True},
                'eligible_stock_market_pairs': [list(pair) for pair in pm.STOCKS], 'known_future_information': False,
                'historical_membership_status': 'unknown', 'pit_status': 'unknown', 'corporate_actions_status': 'unknown',
                'source_binding': {'synthetic_fixture': True}}
    prepared = pm.linear.build_panel(pd.DataFrame(records), {'TWSE': pm.DATES, 'TPEX': pm.DATES},
                                     asof=pm.DATES[pm.TRAIN_INDEX], horizons=[1, 5], evidence=evidence,
                                     policy_binding=pm.BINDING)
    return pm.linear.fit_baseline(prepared, compare_windows=False)


@pytest.fixture()
def synthetic(tmp_path, monkeypatch, model):
    """Real load_c0_published_panel + real build_panel/predict over the two-market synthetic data."""
    routes = pm._routes()

    def query_published_rows(root, artifact, route, *, stock_ids, start_date, end_date, **_):
        rows = [row for row in routes[route] if row['stock_id'] in set(stock_ids) and start_date <= row['date'] <= end_date]
        return pm._Selected(rows, tmp_path, route)

    monkeypatch.setitem(sys.modules, 'stock_query_projection',
                        types.SimpleNamespace(query_published_rows=query_published_rows))
    context = pm._context(tmp_path)
    context['descriptor']['snapshot_fingerprint'] = 'synthetic-fingerprint'
    context['current_identity']['records'] = [{'stock_id': s, 'stock_name': 'S' + s, 'market': m} for s, m in pm.STOCKS]
    context['data_time'] = single.contract.build_data_time(context, model=None)
    active = dict(pm._active(), model=model, pointer_binding={'sha256': '3' * 64})
    calls = {'forward': [], 'validate': []}
    monkeypatch.setattr(single.contract, 'load_active_c0_model', lambda *a, **k: active)
    monkeypatch.setattr(single.contract, 'load_c0_published_context', lambda *a, **k: copy.deepcopy(context))

    def process_forward(predictions, inputs, loaded, *, db_path=None):
        rows = predictions.to_dict('records')
        market_asof = single.contract._market_asof_map(inputs)
        # Same anti-lookahead rule as the real process_c0_forward (KEEP).
        for row in rows:
            if str(row['session_date']) > market_asof.get(str(row['market']), inputs['asof']):
                raise single.contract.C0IntegrityError('c0_forward_prediction_after_market_asof:' + str(row['market']))
        calls['forward'].append(rows)
        for row in rows:
            row['signal_close'] = float(inputs['panel'].loc[(inputs['panel'].stock_id == row['stock_id'])
                & (inputs['panel'].session_date == row['session_date']), 'adjusted_close'].iloc[0])
        return {'predictions': rows, 'forward_binding': {'fixture': True}}

    def validate(payload, pdf_bytes, *, stock_id, horizon, **kwargs):
        calls['validate'].append((stock_id, payload['analysis_asof'], payload['latest_data_date']))
        return {'verified': True, 'schema': 'fixture', 'reproduction_status': 'reproduced', 'prediction_reproduced': True}

    def pdf(path, payload, report_view=None):
        market = str(payload['stock']['market']).upper()
        market_asof = {'TWSE': D, 'TPEX': T}[market]
        assert payload['analysis_asof'] == market_asof
        if payload['estimate_status'] == 'no_estimate':
            assert report_view is None
        else:
            assert report_view is not None
            assert report_view['stock_id'] == payload['stock']['stock_id']
            assert report_view['dates'][-1] == market_asof
            assert all(day <= market_asof for day in report_view['dates'])
            assert pm.POISON not in report_view['close']
        return path.write_bytes(b'%PDF-1.4\nfixture\n%%EOF\n')

    monkeypatch.setattr(single.contract, 'process_c0_forward', process_forward)
    monkeypatch.setattr(single.contract, 'validate_c0_estimate', validate)
    monkeypatch.setattr(single, '_pdf', pdf)
    return {'calls': calls, 'context': context, 'output': tmp_path / 'reports'}


def test_lagging_market_stock_is_estimated_with_its_own_market_asof(synthetic):
    report = single.generate_estimate('H1', '3333', output_dir=synthetic['output'], cache_dir=synthetic['output'] / 'c')
    assert report['estimate_status'] == 'estimated'
    assert report['latest_data_date'] == T and report['analysis_asof'] == T
    [rows] = synthetic['calls']['forward']
    assert {row['session_date'] for row in rows} == {T} and {row['data_asof'] for row in rows} == {T}
    # The signal close is TPEX's own T close, never the poisoned later D row.
    assert report['prediction']['signal_close'] != pm.POISON
    assert report['prediction']['signal_close'] == pytest.approx(float(pm._closes(2)[pm.D_INDEX - 1]))
    data_time = report['data_time']
    assert data_time['analysis_asof'] == T and data_time['stock_market'] == 'TPEX'
    assert data_time['stock_market_asof'] == T and data_time['latest_market_asof'] == D
    assert data_time['stock_market_asof_lags'] is True
    assert data_time['asof_by_market'] == {'TWSE': D, 'TPEX': T}
    # A one-stock panel's own asof is already its market's day; the whole-data view is kept under all_markets.
    assert data_time['all_markets']['analysis_asof'] == T
    codes = [label['code'] for label in report['version_labels']]
    assert codes.count(single.STOCK_MARKET_ASOF_LAGS) == 1
    label = next(label for label in report['version_labels'] if label['code'] == single.STOCK_MARKET_ASOF_LAGS)
    assert label['recorded'] == D and label['actual'] == T and label['detail']['market'] == 'TPEX'
    assert single.STOCK_MARKET_ASOF_LAGS in [label['code'] for label in data_time['labels']]
    assert synthetic['calls']['validate'] == [('3333', T, T)]
    lines = single._data_time_lines(report)
    assert any(line.startswith('本股所屬上櫃市場資料日 ') and single._day(T) in line
               and single._day(D) in line and '本股依自己的市場資料日估計' in line for line in lines)


def test_lagging_market_estimate_equals_an_independent_build_at_its_own_day(synthetic, model):
    report = single.generate_estimate('H1', '3333', output_dir=synthetic['output'], cache_dir=synthetic['output'] / 'c')
    inputs = single.contract.load_c0_published_panel(['3333'], context_or_sources=synthetic['context'], consumer_id='single',
        cache_dir=synthetic['output'] / 'c2', runtime_binding=dict(pm._active(), model=model))
    assert inputs['asof'] == T and inputs['asof_by_market']['TPEX'] == T
    assert inputs['panel']['session_date'].max() == T
    prepared = pm.linear.build_panel(inputs['panel'], inputs['market_sessions'], asof=T, horizons=[1],
                                     evidence=inputs['evidence'], policy_binding=pm.BINDING)
    expected = pm.linear.predict_baseline(model, prepared).iloc[0]
    assert report['prob_up'] == pytest.approx(float(expected['prob_up']), rel=0, abs=0)
    assert report['expected_return'] == pytest.approx(float(expected['expected_return']), rel=0, abs=0)


def test_build_uses_the_stock_market_day_even_when_scope_asof_is_later(synthetic, monkeypatch):
    real = single.contract.load_c0_published_panel

    def later_scope_asof(*args, **kwargs):
        inputs = real(*args, **kwargs)
        inputs['asof'] = D  # e.g. a multi-market scope: its asof is the latest market's day
        return inputs
    monkeypatch.setattr(single.contract, 'load_c0_published_panel', later_scope_asof)
    report = single.generate_estimate('H1', '3333', output_dir=synthetic['output'], cache_dir=synthetic['output'] / 'c')
    [rows] = synthetic['calls']['forward']
    assert {row['data_asof'] for row in rows} == {T}
    assert report['analysis_asof'] == T and report['data_time']['analysis_asof'] == T
    assert synthetic['calls']['validate'] == [('3333', T, T)]


def test_current_market_stock_keeps_latest_day_and_is_not_stale_for_the_other_market(synthetic):
    report = single.generate_estimate('H1', '1111', output_dir=synthetic['output'], cache_dir=synthetic['output'] / 'c')
    assert report['estimate_status'] == 'estimated'
    assert report['analysis_asof'] == D and report['data_time']['analysis_asof'] == D
    assert report['data_time']['stock_market_asof_lags'] is False
    assert single.STOCK_MARKET_ASOF_LAGS not in [label['code'] for label in report['version_labels']]


def test_stock_data_time_freshness_is_per_stock_market():
    data_time = {'analysis_asof': D, 'asof_by_market': {'TWSE': D, 'TPEX': T}, 'is_stale': True,
                 'freshness_status': 'stale', 'latest_expected_session': D,
                 'latest_expected_session_by_market': {'TWSE': D, 'TPEX': D},
                 'stale_markets': {'TPEX': [T, D]}, 'weekdays_after_calendar': [], 'requested_asof': None,
                 'is_older_than_requested': False, 'labels': []}
    twse = single._stock_data_time(data_time, 'TWSE', D)
    assert twse['is_stale'] is False and twse['freshness_status'] == 'latest'
    assert twse['all_markets']['is_stale'] is True and twse['stale_markets'] == {'TPEX': [T, D]}
    lines = single._data_time_lines({'data_time': twse})
    assert any(line.startswith('其他市場資料較舊（不影響本股）：上櫃') for line in lines)
    assert not any('【非最新資料】' in line for line in lines)
    lag = single._stock_market_lag_label(data_time, 'TPEx', T)
    tpex = single._stock_data_time(data_time, 'TPEx', T, lag)
    assert tpex['is_stale'] is True and tpex['analysis_asof'] == T and tpex['latest_expected_session'] == D
    assert tpex['stock_market'] == 'TPEX' and tpex['stock_market_asof_lags'] is True
    lines = single._data_time_lines({'data_time': tpex})
    assert any('【非最新資料】' in line for line in lines)
    assert not any('其他市場資料較舊' in line for line in lines)
    assert data_time['analysis_asof'] == D  # input not mutated
    unknown = single._stock_data_time(data_time, None, D)
    assert unknown['stock_market'] is None and unknown['stock_market_asof_lags'] is False
    assert not any('其他市場資料較舊' in line for line in single._data_time_lines({'data_time': unknown}))


def test_stock_market_asof_lookup_prefers_analysed_days():
    context = {'asof': D, 'asof_by_market': {'TWSE': D, 'TPEX': D}, 'analysis_asof_by_market': {'TWSE': D, 'TPEX': T}}
    assert single._stock_market_asof(context, 'TPEx') == T
    assert single._stock_market_asof({'asof': D, 'asof_by_market': {'TWSE': D, 'TPEX': T}}, 'tpex') == T
    assert single._stock_market_asof({'asof': D}, 'TPEX') == D
    assert single._stock_market_lag_label({'asof_by_market': {'TWSE': D, 'TPEX': D}}, 'TPEX', D) is None


def test_excluded_stock_no_estimate_reports_its_own_market_day(synthetic, monkeypatch):
    def excluded(*args, **kwargs):
        raise single.contract.C0IntegrityError('c0_no_stock_passed_current_price_scope_quality:'
            '[{"stock_id":"3333","market":"TPEX","reason":"latest_exact_253_market_sessions_incomplete"}]')
    monkeypatch.setattr(single.contract, 'load_c0_published_panel', excluded)
    monkeypatch.setattr(single, '_stock_last_price_date', lambda *a, **k: T)
    report = single.generate_estimate('H1', '3333', output_dir=synthetic['output'])
    assert report['estimate_status'] == 'no_estimate'
    assert report['latest_data_date'] == T and report['data_time']['analysis_asof'] == T
    assert single.STOCK_MARKET_ASOF_LAGS in [label['code'] for label in report['version_labels']]


def test_keep_lagging_market_without_253_sessions_is_still_a_no_estimate(tmp_path, monkeypatch, model):
    """KEEP：落後市場湊不滿 253 個已驗證交易日時，仍以 market_calendar_exact_253_sessions_unavailable 排除。"""
    routes = pm._routes()
    monkeypatch.setitem(sys.modules, 'stock_query_projection', types.SimpleNamespace(
        query_published_rows=lambda root, artifact, route, *, stock_ids, start_date, end_date, **_: pm._Selected(
            [row for row in routes[route] if row['stock_id'] in set(stock_ids) and start_date <= row['date'] <= end_date],
            tmp_path, route)))
    context = pm._context(tmp_path, tpex_index=200)
    context['descriptor']['snapshot_fingerprint'] = 'synthetic-fingerprint'
    context['current_identity']['records'] = [{'stock_id': s, 'stock_name': 'S' + s, 'market': m} for s, m in pm.STOCKS]
    active = dict(pm._active(), model=model, pointer_binding={'sha256': '3' * 64})
    monkeypatch.setattr(single.contract, 'load_active_c0_model', lambda *a, **k: active)
    monkeypatch.setattr(single.contract, 'load_c0_published_context', lambda *a, **k: copy.deepcopy(context))
    monkeypatch.setattr(single, '_stock_last_price_date', lambda *a, **k: None)

    def pdf(path, payload, report_view=None):
        assert payload['stock']['stock_id'] == '3333'
        assert payload['estimate_status'] == 'no_estimate'
        assert report_view is None
        return path.write_bytes(b'%PDF-1.4\nfixture\n%%EOF\n')

    monkeypatch.setattr(single, '_pdf', pdf)
    report = single.generate_estimate('H1', '3333', output_dir=tmp_path / 'reports')
    assert report['estimate_status'] == 'no_estimate'
    assert report['no_estimate_reason'] == 'market_calendar_exact_253_sessions_unavailable'
    assert report['latest_data_date'] == pm.DATES[200]
