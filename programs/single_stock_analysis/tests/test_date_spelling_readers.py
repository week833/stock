"""Current immutable readers compare canonical dates without guessing sessions."""
import pytest
import published_single_stock_projection as projection
import stock_data_sources as sources

@pytest.mark.parametrize('value', ['2026-10-08', '20261008'])
def test_reader_dates_have_one_canonical_value(value):
    assert projection._date(value) == '2026-10-08'
    assert sources._completion_bound(value) == '2026-10-08'

@pytest.mark.parametrize('value', [20261008, True, '2026-W41-4', '2026-10-08T08:00:00', 'invalid'])
def test_reader_dates_refuse_non_day_values(value):
    assert projection._date(value) is None


# ------------------------------------------------------------------------------------------------------------------
# Ported from tests\_retired\test_date_spelling_readers.py (2026-10-12 review): these tests only use live reader code
# (published_single_stock_projection, stock_data_sources, stock_query_projection).  The retired file could not be
# collected because of other imports (retired modules and test data modules), not because these rules were retired.
import re

import stock_query_projection as query
from test_published_no_events import government_completion_entry, official_no_data_entry, scope_recovered_entry

_ISO_DAY = re.compile(r'^\d{4}-\d{2}-\d{2}$')


def _compact(day):
    return day.replace('-', '')


def _spell_tree(value, spelling, _counter=None):
    """Every ISO day string rewritten as YYYYMMDD ('compact') or every other one ('mixed')."""
    counter = [0] if _counter is None else _counter
    if isinstance(value, dict):
        return {key: _spell_tree(item, spelling, counter) for key, item in value.items()}
    if isinstance(value, list):
        return [_spell_tree(item, spelling, counter) for item in value]
    if isinstance(value, str) and _ISO_DAY.match(value):
        counter[0] += 1
        return _compact(value) if spelling == 'compact' or counter[0] % 2 else value
    return value


def test_two_spellings_of_one_summary_day_are_not_a_conflict_but_two_days_are():
    def pin(*values):
        return [{'metadata': {'actual_max_date': values[0], 'metadata': {'actual_max_date': values[1]}}}]
    assert projection._trusted_price_summary_date(pin('2026-10-02', '20261002')) == ('2026-10-02', False)
    with pytest.raises(ValueError, match='conflict'):
        projection._trusted_price_summary_date(pin('2026-10-02', '20261001'))
    with pytest.raises(ValueError, match='maximum_invalid'):
        projection._trusted_price_summary_date(pin(20261002, 20261002))
    # max over mixed spellings is by value ('20261001' sorts after '2026-10-03' as text)
    assert projection._trusted_price_summary_date(pin('20261001', '20261001') + pin('2026-10-03', '2026-10-03')) == (
        '2026-10-03', False)


def test_empty_day_receipts_prove_the_same_completion_in_either_spelling():
    decisive = ('status', 'reason', 'completion_state', 'source_available_complete', 'completion_evidence_valid',
                'requested_scope_complete', 'due_scope_complete', 'completion_start_date', 'completion_end_date',
                'pending_release_dates', 'usable', 'model_usable', 'data_complete', 'history_complete',
                'no_events', 'snapshot_asof')
    cases = [('government_unavailable', government_completion_entry()),
             ('government_pending', government_completion_entry(pending=True)),
             ('official_no_data', official_no_data_entry())]
    for name, entry in cases:
        day = entry['metadata']['last_attempt']['expected_asof']
        iso = sources.published_completion(entry['route_id'], entry, verified_object=True, query_empty=True,
                                           global_empty=True, start_date=day, end_date=day)
        assert iso['completion_evidence_valid'] is True, name
        for spelling in ('compact', 'mixed'):
            spelled = _spell_tree(entry, spelling)
            assert spelled != entry, (name, spelling)            # the fixture really changed spellings
            for bounds in ((day, day), (_compact(day), _compact(day))):
                result = sources.published_completion(entry['route_id'], spelled, verified_object=True,
                                                      query_empty=True, global_empty=True,
                                                      start_date=bounds[0], end_date=bounds[1])
                for field in decisive:
                    assert result.get(field) == iso.get(field), (name, spelling, field)
                assert result['evidence_scope']['expected_asof'] == iso['evidence_scope']['expected_asof'] == day
    snapshot = scope_recovered_entry()
    base = sources.published_no_events(snapshot['route_id'], snapshot, verified_empty=True, end_date='2026-10-03')
    for spelling in ('compact', 'mixed'):
        spelled = _spell_tree(snapshot, spelling)
        assert spelled != snapshot
        assert sources.published_no_events(snapshot['route_id'], spelled, verified_empty=True,
                                           end_date='20261003') == base


def test_global_row_scope_and_cached_scope_read_both_spellings():
    scope = sources._GlobalRowScope('official_market_daily', 'date')
    for day in ('2026-09-30', '20260930', '20261001', '2026-10-01', '2026/10/01', 20261002):
        scope.add({'stock_id': '2330', 'date': day})
    document = scope.document()
    assert document['dates'] == ['2026-09-30', '2026-10-01'] and document['invalid_date_rows'] == 2
    cached = {'_projection_global_scope': dict(document, dates=['20260930', '2026-10-01'])}
    result = projection._cached_global_row_scope(cached, 'official_market_daily', 'date', '2026-10-01', '2026-10-01')
    assert result[2] == {'2026-09-30', '2026-10-01'} and result[5] == 1


def test_query_day_reader_refuses_numbers_and_other_formats():
    assert query._normalise_day('20261002') == query._normalise_day('2026-10-02T15:00:00+08:00') == '2026-10-02'
    assert query._normalise_day(20261002) is None and query._normalise_day('2026W401') is None
