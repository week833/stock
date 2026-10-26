"""Single adapter fixtures for the frozen shared current-only C0 interface.

Shared quality fixtures verify the 253/native/identity gates. These adapter fixtures
verify same-pin dispatch, report provenance, unknown targets, and propagation of
their unavailable reasons. They do not establish a live normal-entry result.
"""
from __future__ import annotations

import hashlib
import json
import sys
from types import ModuleType
from pathlib import Path

import pandas as pd
import pytest

import run_single_stock_entry as entry
import tw_single_stock_c0 as single


@pytest.fixture
def current_case(monkeypatch, tmp_path):
    # Isolate optional comparisons: these model/dispatch fixtures must never stream live Central.
    monkeypatch.setattr(single, 'attach_report_comparison', lambda payload: payload.setdefault(
        'report_comparison', {'schema':'stock-report-comparison-v1','metrics':[],
                              'notes':['離線夾具未提供比較資料，既有預測保留。']}))
    monkeypatch.setattr(single.selected, 'analysis_binding', lambda: None)
    stock = {'stock_id': '6488', 'stock_name': '環球晶', 'market': 'TPEx'}
    identity = {
        'records': [stock], 'current_positive_pairs': [['6488', 'TPEx']],
        'binding': {'sha256': 'c' * 64}, 'source_asof': '2026-10-08',
        'observed_date': '2026-10-08', 'historical_membership_verified': False,
    }
    scientific = {'profile_id': 'C0', 'requirements_version': 'original-v1',
                  'feature_version': 'tw-stock-c0-price-seven-v1',
                  'policy_sha256': 'a' * 64, 'requirements_raw_sha256': 'b' * 64}
    current = dict(scientific, requirements_version='2026-10-09.current-only.1',
                   policy_sha256='d' * 64, requirements_raw_sha256='e' * 64)
    active = {
        'model': {'metadata': {'source_evidence': {'eligible_stock_market_pairs': [['2330', 'TWSE']]},
                               'evaluation_role': 'comparison_only'}, 'metrics': {}},
        'policy_binding': scientific, 'current_policy_binding': current,
        'original_model_feature_contract': dict(scientific),
        'contract_adoption_ref': {'path': 'adoption.json', 'sha256': 'f' * 64, 'size': 100},
        'effective_C0_semantics_projection_sha256': '1' * 64,
        'original_effective_C0_semantics_projection_sha256': '1' * 64,
        'contract_adoption_verified': True,
        'model_activation_epoch': '2026-10-07T00:00:00+00:00',
        'artifact_binding': {'sha256': '2' * 64, 'feature_schema': 'tw-stock-c0-price-seven-v1',
                             'feature_order': ['f1', 'f2', 'f3', 'f4', 'f5', 'f6', 'f7']},
        'pointer_binding': {'sha256': '3' * 64},
    }
    descriptor = {'snapshot_fingerprint': '4' * 64, 'descriptor_sha256': '5' * 64,
                  'sources': [{'manifest_sha256': '9' * 64, 'published_at': '2026-10-08T07:00:00+00:00'}]}
    context = {'current_identity': identity, 'descriptor': descriptor, 'asof': '2026-10-08'}
    calendar_evidence = {'binding': {'sha256': '8' * 64}, 'calendar_verified_by_market': {'TPEx': True}}
    application_scope = {'binding': {'sha256': '7' * 64}, 'capture_binding': {'sha256': '6' * 64}}
    required_route_evidence = {'finmind_price_adj': {'status': 'PARTIAL'}}
    scope = {
        'calendar_evidence': calendar_evidence, 'application_scope': application_scope,
        'required_route_evidence': required_route_evidence,
        'panel': pd.DataFrame([{'stock_id': '6488', 'market': 'TPEx',
                                'session_date': '2026-10-08', 'adjusted_close': 100.0,
                                'Trading_Volume': 1000.0}]),
        'market_sessions': {'TPEx': ['2026-10-08']},
        'asof': '2026-10-08', 'evidence': {'fixture': True}, 'descriptor': descriptor,
        'policy_binding': scientific, 'current_policy_binding': current, 'current_identity': identity,
        'source_binding': {'route_statuses': {'finmind_price_adj': 'PARTIAL'}},
        'coverage': {'requested': 1, 'eligible': 1, 'excluded': []},
        'readiness': {
            'schema': 'tw-stock-profile-readiness-v1', 'schema_version': 1,
            'requirements_version': '2026-10-09.current-only.1',
            'feature_version': 'tw-stock-core-features-v1', 'profile_id': 'C0',
            'status': 'BLOCKED', 'ready': False, 'required_route_count': 6,
            'blocking_failures': [{'route_id': 'finmind_price_adj', 'reason': 'PARTIAL'}],
            'optional_warnings': [], 'training_eligible': False,
            'training_evidence_unknown': ['historical_pit', 'corporate_actions'],
            'snapshot_binding': descriptor,
            'verification': 'existing_reader_evidence_only', 'independently_verified': False,
        },
    }
    calls = []
    # The entry builds the runtime binding from the inputs it already pinned (same fields as
    # contract.c0_runtime_binding, without a second context load), so validation is bound to exactly these values.
    runtime = {'schema': 'stock-daily-c0-runtime-binding-v2', 'asof': '2026-10-08',
               'snapshot_fingerprint': descriptor['snapshot_fingerprint'], 'published_sources': descriptor['sources'],
               'identity_binding': identity['binding'], 'calendar_binding': calendar_evidence['binding'],
               'calendar_verified_by_market': calendar_evidence['calendar_verified_by_market'],
               'application_scope_binding': application_scope['binding'],
               'capture_scope_binding': application_scope['capture_binding'],
               'required_route_evidence': required_route_evidence, 'readiness': scope['readiness'],
               'model_binding': active['artifact_binding'], 'pointer_binding': active['pointer_binding'],
               'policy_binding': scientific,
               **{key: active[key] for key in ('current_policy_binding', 'original_model_feature_contract',
                                               'contract_adoption_ref', 'effective_C0_semantics_projection_sha256',
                                               'original_effective_C0_semantics_projection_sha256',
                                               'contract_adoption_verified', 'model_activation_epoch')}}

    def load_active(model_path=None, *, consumer_id=None, pointer_path=None):
        calls.append(('active', consumer_id))
        return active

    def load_context(context_or_sources=None, *, consumer_id=None, runtime_binding=None):
        assert runtime_binding is active
        calls.append(('context', consumer_id))
        return context

    def load_panel(stock_ids=None, *, context_or_sources=None, consumer_id=None, runtime_binding=None, **kwargs):
        assert context_or_sources is context and runtime_binding is active
        assert stock_ids == ['6488']
        calls.append(('panel', consumer_id))
        return scope

    def build_panel(panel, sessions, *, horizons, policy_binding, **kwargs):
        assert panel is scope['panel'] and sessions is scope['market_sessions']
        assert policy_binding is scientific
        calls.append(('prepare', list(horizons)))
        return list(horizons)

    def predict_baseline(model, horizons):
        assert model is active['model']
        return pd.DataFrame([
            {'stock_id': '6488', 'market': 'TPEx', 'horizon': horizon,
             'prediction_status': 'predicted', 'reason': None, 'prob_up': 0.5,
             'expected_return': 0.0, 'calibration_status': 'uncalibrated',
             'target_session_date': None, 'future_calendar_status': 'unverified',
             'selected_direction_kind': 'fit_up_rate_baseline',
             'selected_return_kind': 'zero_return_baseline'} for horizon in horizons
        ])

    def process_forward(predictions, loaded_scope, loaded_active, *, db_path=None):
        assert loaded_scope is scope and loaded_active is active
        calls.append(('forward', len(predictions)))
        return {'predictions': predictions.to_dict('records'), 'forward_binding': {'fixture': True}}

    def runtime_binding(*args, **kwargs):
        raise AssertionError('the entry must not load the published context a second time')

    def validate(payload, pdf_bytes, *, stock_id, horizon, expected_snapshot_fingerprint=None,
                 allow_qa_override=False, expected_runtime_binding=None):
        assert stock_id == '6488' and horizon == payload['horizon_sessions']
        assert expected_runtime_binding == runtime
        assert expected_snapshot_fingerprint == descriptor['snapshot_fingerprint']
        assert payload['pdf_sha256'] == hashlib.sha256(pdf_bytes).hexdigest()
        assert payload['original_effective_C0_semantics_projection_sha256'] == active['original_effective_C0_semantics_projection_sha256']
        assert payload['contract_adoption_verified'] is active['contract_adoption_verified']
        assert payload['readiness'] is scope['readiness']
        # The sealed JSON is written next to the PDF before validation (never an orphan PDF); validation's
        # result is written back afterwards.
        stored = json.loads(Path(payload['output_json']).read_text(encoding='utf-8'))
        assert stored['verification'] == {'status': 'pending'}
        assert stored['pdf_sha256'] == payload['pdf_sha256']
        calls.append(('validate', horizon))
        return {'verified': True, 'schema': 'fixture-verification', 'reproduction_status': 'reproduced',
                'prediction_reproduced': True}

    def pdf(path, payload, report_view=None):
        assert report_view is not None
        assert report_view['stock_id'] == payload['stock']['stock_id'] == '6488'
        assert report_view['dates'] == [payload['analysis_asof']] == ['2026-10-08']
        assert report_view['close'] == [100.0]
        assert report_view['volume'] == [1000.0]
        return path.write_bytes(b'%PDF-1.4\nfixture\n%%EOF\n')

    linear = ModuleType('stock_daily_linear')
    linear.build_panel = build_panel
    linear.predict_baseline = predict_baseline
    monkeypatch.setitem(sys.modules, 'stock_daily_linear', linear)
    monkeypatch.setattr(single.contract, 'load_active_c0_model', load_active)
    def selected_context(*args, **kwargs):
        calls.append(('selected-context', 'single'))
        return context
    monkeypatch.setattr(single.selected, 'load_selected_context', selected_context)
    monkeypatch.setattr(single.contract, 'load_c0_published_context', load_context)
    monkeypatch.setattr(single.contract, 'load_c0_published_panel', load_panel)
    monkeypatch.setattr(single.contract, 'process_c0_forward', process_forward)
    monkeypatch.setattr(single.contract, 'c0_runtime_binding', runtime_binding)
    monkeypatch.setattr(single.contract, 'validate_c0_estimate', validate)
    monkeypatch.setattr(single, '_pdf', pdf)
    return {'active': active, 'context': context, 'scope': scope, 'calls': calls,
            'output': tmp_path / 'reports', 'panel_loader': load_panel}


def test_c0_writer_sends_same_comparison_block_to_pdf_html_and_json(monkeypatch, tmp_path):
    block = {'schema':'stock-report-comparison-v1','stock_id':'2330','signal_date':'2026-10-16',
             'metrics':[{'項目':'月營收','資料期間':'202609','本期值':'120','比較':'較去年同月增加20%',
                         '解讀':'僅描述當期比較。','資料日期':'20261009'}],'notes':[]}
    payload = {'stock':{'stock_id':'2330'},'horizon_sessions':63,'analysis_asof':'2026-10-16',
               'prediction':{'prob_up':.61,'expected_return':.02},'data_time':{}}
    seen = []
    monkeypatch.setattr(single, 'attach_report_comparison', lambda item: item.setdefault('report_comparison', block))
    def pdf(path, item, view):
        seen.append(item['report_comparison'])
        path.write_bytes(b'%PDF-1.4\nfixture\n%%EOF\n')
    monkeypatch.setattr(single, '_pdf', pdf)
    result = single._write_report(payload, tmp_path/'report.pdf', tmp_path/'report.json')
    stored = json.loads((tmp_path/'report.json').read_text(encoding='utf-8'))
    assert seen == [block] and stored['report_comparison'] == block
    assert '較去年同月增加20%' in (tmp_path/'report.html').read_text(encoding='utf-8')
    assert result['prediction'] == {'prob_up':.61,'expected_return':.02}


@pytest.mark.parametrize('horizon', ['H1', 'h1', 'H63', 'h63'])
def test_entry_dispatch_uses_current_horizon(horizon, monkeypatch):
    calls = []
    monkeypatch.setattr(single, 'main', lambda h, args: calls.append((h, args)) or 7)
    assert entry.main([horizon, '--stock', '6488']) == 7
    assert calls == [(63 if '63' in horizon else 1, ['--stock', '6488'])]


@pytest.mark.parametrize('horizon', ['tw_single_stock_3m_analyzer', 'tw_single_stock_tomorrow_open', 'H5', '1', '63'])
def test_entry_rejects_retired_module_dispatch(horizon):
    assert entry.main([horizon, '--stock', '6488']) == 2


@pytest.mark.parametrize('flag', ['--profile', '--source-mode', '--provisional', '--research-audit-only'])
def test_current_cli_rejects_retired_selectors(flag):
    with pytest.raises(SystemExit) as error:
        single.main('H1', ['--stock', '6488', flag, 'legacy'])
    assert error.value.code == 2


def test_both_horizons_share_one_pin_and_distinguish_current_policy(current_case):
    case = current_case
    reports = single.generate_estimate('H63', '6488', output_dir=case['output'], both_horizons=True)
    assert [report['horizon_sessions'] for report in reports] == [1, 63]
    assert case['calls'] == [('active', 'single'), ('context', 'single'), ('panel', 'single'),
                             ('prepare', [1, 63]), ('forward', 2),
                             ('validate', 1), ('validate', 63)]
    assert reports[0]['published_snapshot'] is reports[1]['published_snapshot']
    for report in reports:
        assert report['stock']['market'] == 'TPEx'
        assert report['policy_binding'] == case['active']['policy_binding']
        assert report['current_policy_binding'] == case['active']['current_policy_binding']
        assert report['requirements_version'] == '2026-10-09.current-only.1'
        for field in ('original_model_feature_contract', 'contract_adoption_ref',
                      'effective_C0_semantics_projection_sha256',
                      'original_effective_C0_semantics_projection_sha256',
                      'contract_adoption_verified', 'model_activation_epoch'):
            assert report[field] == case['active'][field]
        assert report['readiness'] is case['scope']['readiness']
        assert report['readiness']['ready'] is False
        assert report['readiness']['required_route_count'] == 6
        assert report['source_statuses']['finmind_price_adj'] == 'PARTIAL'
        assert report['target_session_date'] is None
        assert report['effectiveness_status'] == 'unknown'
        assert report['historical_membership_verified'] is False
        path = Path(report['output_json'])
        stored = json.loads(path.read_text(encoding='utf-8'))
        assert stored['pdf_sha256'] == hashlib.sha256(
            Path(report['output_pdf']).read_bytes()).hexdigest()
        unsigned = dict(stored)
        binding = unsigned.pop('estimate_binding_sha256')
        assert hashlib.sha256(single._canonical(unsigned)).hexdigest() == binding
        assert stored['verification']['status'] == 'verified'
        assert stored['data_time']['analysis_asof'] == '2026-10-08'


@pytest.mark.parametrize('horizon', [1, 63])
def test_direct_horizon_returns_its_own_report(current_case, horizon):
    report = single.generate_estimate(horizon, '6488', output_dir=current_case['output'])
    assert report['horizon_sessions'] == horizon
    assert current_case['calls'].count(('panel', 'single')) == 1


@pytest.mark.parametrize('reason', ['exact253_sessions_insufficient', 'native_price_quality_flagged'])
def test_shared_quality_failure_is_preserved_without_report(current_case, monkeypatch, capsys, reason):
    def unavailable(*args, **kwargs):
        raise ValueError(reason)
    monkeypatch.setattr(single.contract, 'load_c0_published_panel', unavailable)
    assert single.main('H1', ['--stock', '6488', '--output-dir', str(current_case['output'])]) == 2
    assert reason in capsys.readouterr().err
    assert not current_case['output'].exists()


def test_unknown_current_classification_cannot_fall_back(current_case, capsys):
    current_case['context']['current_identity']['records'] = []
    assert single.main('H63', ['--stock', '6488', '--output-dir', str(current_case['output'])]) == 2
    assert 'current_ordinary_stock' in capsys.readouterr().err
    assert ('panel', 'single') not in current_case['calls']


def test_resolver_keeps_the_bot_text_format(current_case, capsys):
    assert single.main('H63', ['--resolve-stock', '環球晶', '--max-matches', '8', '--no-progress']) == 0
    assert '1. 6488 環球晶｜exact｜score=1.0' in capsys.readouterr().out
    assert current_case['calls'] == [('selected-context', 'single')]


def test_snapshot_drift_cannot_produce_a_report(current_case):
    current_case['scope']['descriptor'] = {'snapshot_fingerprint': '6' * 64}
    with pytest.raises(ValueError, match='c0_analysis_snapshot_changed'):
        single.generate_estimate('H1', '6488', output_dir=current_case['output'])
    assert not current_case['output'].exists()


def test_current_policy_difference_is_recorded_not_fatal(current_case):
    # Plan (unbind inventory, tw_single_stock_c0.py:101-102 REMOVE): a policy version difference no longer aborts;
    # the report records the active model's policy bindings it was analysed with.
    current_case['scope']['current_policy_binding'] = {'policy_sha256': '7' * 64}
    report = single.generate_estimate('H1', '6488', output_dir=current_case['output'])
    assert report['current_policy_binding'] == current_case['active']['current_policy_binding']
    assert report['policy_binding'] == current_case['active']['policy_binding']
    assert report['verification']['status'] == 'verified'


def test_report_verification_failure_cannot_publish_json(current_case, monkeypatch, capsys):
    def invalid(*args, **kwargs):
        raise ValueError('c0_estimate_binding_invalid')
    monkeypatch.setattr(single.contract, 'validate_c0_estimate', invalid)
    assert single.main('H1', ['--stock', '6488', '--output-dir', str(current_case['output'])]) == 2
    assert 'c0_estimate_binding_invalid' in capsys.readouterr().err
    # The run still fails (rc=2).  The JSON written beside the PDF records the failed verification, so no
    # report can be taken for a verified one and no PDF is left without its JSON.
    [stored_path] = list(current_case['output'].glob('*.json'))
    stored = json.loads(stored_path.read_text(encoding='utf-8'))
    assert stored['verification']['status'] == 'failed'
    assert 'c0_estimate_binding_invalid' in stored['verification']['error']
    assert Path(stored['output_pdf']).is_file()
    unsigned = dict(stored)
    binding = unsigned.pop('estimate_binding_sha256')
    assert hashlib.sha256(single._canonical(unsigned)).hexdigest() == binding


def test_pinned_head_is_content_addressed_and_a_missing_head_is_labelled(monkeypatch, tmp_path):
    monkeypatch.setattr(single, 'PUBLISHED_ROOT', tmp_path)
    (tmp_path / '_manifests').mkdir()
    raw = b'{"schema":"stock-data-publication-v1","datasets":[]}'
    sha = hashlib.sha256(raw).hexdigest()
    (tmp_path / '_manifests' / (sha + '.json')).write_bytes(raw)
    assert single._pinned_head_reference(None) == (None, [])
    reference, labels = single._pinned_head_reference(sha.upper())
    assert labels == [] and reference == {'root': str(tmp_path),
                                          'manifest_path': str(tmp_path / '_manifests' / (sha + '.json')),
                                          'manifest_sha256': sha}
    missing = '0' * 64
    reference, labels = single._pinned_head_reference(missing)
    assert reference is None
    assert [label['code'] for label in labels] == [single.PINNED_HEAD_UNAVAILABLE]
    assert labels[0]['recorded'] == {'head_sha256': missing}
    # KEEP: bytes that do not hash to the head's name are tamper, never a fallback to the current head.
    tampered = 'a' * 64
    (tmp_path / '_manifests' / (tampered + '.json')).write_bytes(raw)
    with pytest.raises(single.contract.C0IntegrityError, match='daily_immutable_head_digest_mismatch'):
        single._pinned_head_reference(tampered)
    for invalid in ('xyz', 'g' * 64, '0' * 63):
        with pytest.raises(ValueError, match='c0_pinned_head_sha256_invalid'):
            single._pinned_head_reference(invalid)


def test_invalid_pinned_head_is_a_caller_error(current_case, capsys):
    assert single.main('H1', ['--stock', '6488', '--output-dir', str(current_case['output']),
                              '--pinned-head-sha256', 'xyz']) == 2
    assert 'c0_pinned_head_sha256_invalid' in capsys.readouterr().err
    assert not current_case['output'].exists()


def test_missing_pinned_head_uses_the_current_head_and_labels_the_report(current_case, monkeypatch, tmp_path):
    monkeypatch.setattr(single, 'PUBLISHED_ROOT', tmp_path / 'published')
    report = single.generate_estimate('H1', '6488', output_dir=current_case['output'], pinned_head_sha256='0' * 64)
    codes = [label['code'] for label in report['version_labels']]
    assert codes.count(single.PINNED_HEAD_UNAVAILABLE) == 1
    assert current_case['calls'][:3] == [('active', 'single'), ('context', 'single'), ('panel', 'single')]


def test_runtime_binding_has_the_same_fields_and_values_as_the_shared_one(monkeypatch):
    # The entry builds the runtime binding itself (no second context load); it must stay identical to
    # contract.c0_runtime_binding for the same pinned inputs, or validation reports a contract drift.
    active = {'artifact_binding': {'sha256': '2' * 64}, 'pointer_binding': {'sha256': '3' * 64},
              'policy_binding': {'policy_sha256': 'a' * 64}, 'current_policy_binding': {'policy_sha256': 'd' * 64},
              'original_model_feature_contract': {'feature_version': 'f'},
              'contract_adoption_ref': {'sha256': 'f' * 64}, 'effective_C0_semantics_projection_sha256': '1' * 64,
              'original_effective_C0_semantics_projection_sha256': '1' * 64, 'contract_adoption_verified': True,
              'model_activation_epoch': '2026-10-07T00:00:00+00:00'}
    inputs = {'asof': '2026-10-08',
              'descriptor': {'snapshot_fingerprint': '4' * 64, 'sources': [{'manifest_sha256': '9' * 64}]},
              'current_identity': {'binding': {'sha256': 'c' * 64}},
              'calendar_evidence': {'binding': {'sha256': '8' * 64}, 'calendar_verified_by_market': {'TPEx': True}},
              'application_scope': {'binding': {'sha256': '7' * 64}, 'capture_binding': {'sha256': '6' * 64}},
              'required_route_evidence': {'finmind_price_adj': {'status': 'PARTIAL'}},
              'readiness': {'status': 'BLOCKED'}}
    monkeypatch.setattr(single.contract, 'load_c0_published_context', lambda *args, **kwargs: inputs)
    shared = single.contract.c0_runtime_binding(None, active)
    assert single._runtime_binding(inputs, active) == shared
    assert set(single.contract._DUAL_BINDING_KEYS) <= set(shared)
