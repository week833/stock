"""Pinned, producer-neutral published JSON inputs (no network or model policy)."""
from __future__ import annotations

import codecs
import copy
import contextlib
import hashlib
import math
import os
import json
import re
import tempfile
import time
import functools
import threading
import weakref
from datetime import date, datetime, timedelta, timezone
from dataclasses import dataclass
from decimal import Decimal, localcontext
from numbers import Integral, Real
from pathlib import Path
from typing import Any, Mapping
from urllib.parse import urlencode
try:
    import date_format as cd
except ModuleNotFoundError:  # shared/ is not on sys.path yet (module imported directly)
    import sys as _sys
    _sys.path.append(str(Path(__file__).resolve().parents[2] / "shared"))
    import date_format as cd


_CANONICAL_SHARED_MARKET_DATA_ROOT = Path(r'D:\TWStockData\shared_market_data')


@dataclass(frozen=True)
class SourceSpec:
    kind: str
    root: str | Path
    manifest_path: str | Path | None = None
    source_id: str | None = None


@dataclass(frozen=True)
class PublishedArtifact:
    route_id: str
    payload: Any
    source_id: str
    snapshot_id: str
    content_sha256: str
    status: str
    metadata: Mapping[str, Any]


class PublishedArtifactValidationError(ValueError):
    """A pinned artifact failed strict JSON, physical identity, or digest validation."""


class StreamedRouteRequiredError(RuntimeError):
    """``artifacts()`` was asked to materialize a route that consumers read only as a bounded row stream.

    Deliberately not a ValueError/TypeError: the readers' route-error handlers must not turn this
    programming-contract violation into a recorded source diagnostic or a route error.
    """


class C0IntegrityError(ValueError):
    """Tamper, torn read or invalid content of pinned published inputs (plan 0.2): always raised, never a label.

    ``reason`` keeps the original verification message when the public message is a stable code."""

    def __init__(self, message, *, reason=None):
        super().__init__(message)
        self.reason = str(message) if reason is None else str(reason)


class C0VersionDrift:
    """Collects version/freshness differences that are reported instead of raised (plan 0.2).

    ``labels`` is the ``version_labels`` list: ``{code, recorded, actual, detail}`` per difference, in order."""

    def __init__(self):
        self.labels = []

    def note(self, code, *, recorded=None, actual=None, detail=None):
        label = {'code': str(code), 'recorded': recorded, 'actual': actual, 'detail': detail}
        if label not in self.labels:
            self.labels.append(label)
        return label


class _PinnedHeadMoved(ValueError):
    """Internal: the pinned head bytes are no longer at a *mutable* head path (it was rewritten by a newer
    publication) or the pinned snapshot no longer exists.  ``validate_pinned_context`` re-pins the latest head and
    labels ``code``; outside that path it is the original ValueError (same message)."""

    def __init__(self, message, *, code, state_index, path):
        super().__init__(message)
        self.code, self.state_index, self.path = code, state_index, str(path)


_DATASETS = {
    'TaiwanStockInfo': 'finmind_stock_info',
    'TaiwanStockPriceAdj': 'finmind_price_adj',
    'TaiwanStockInstitutionalInvestorsBuySellWide': 'finmind_institutional',
    'TaiwanStockMarginPurchaseShortSale': 'finmind_margin',
    'TaiwanStockMonthRevenue': 'finmind_month_revenue',
    'TaiwanStockFinancialStatements': 'finmind_financial_statements',
    'TaiwanStockBalanceSheet': 'finmind_balance_sheet',
    'TaiwanStockCashFlowsStatement': 'finmind_cash_flows_statement',
    'TaiwanStockDividend': 'finmind_dividend',
    'TaiwanStockDividendResult': 'finmind_dividend_result',
    'TaiwanStockPER': 'finmind_per',
    'TaiwanStockShareholding': 'finmind_shareholding',
    'TaiwanStockDayTrading': 'finmind_day_trading',
    'TaiwanFuturesInstitutionalInvestors': 'finmind_futures_institutional',
    'TaiwanOptionInstitutionalInvestors': 'finmind_options_institutional',
    'TaiwanStockGovernmentBankBuySell': 'finmind_government_bank',
    'TaiwanStockTradingDailyReport': 'finmind_trading_daily_report',
    'TaiwanStockBlockTrade': 'finmind_block_trade',
    'TaiwanStockLoanCollateralBalance': 'finmind_loan_collateral',
    'TaiwanStockMarginMaintenance': 'finmind_margin_maintenance',
    'TaiwanStockActiveETFHolding': 'official_active_etf_holding',
    'TaiwanStockActiveETFHoldingChange': 'official_active_etf_change',
    'TaiwanStockIndustryChainMoneyFlow': 'finmind_industry_chain_flow',
    'TaiwanExchangeRate': 'finmind_exchange_rate',
    'GovernmentBondsYield': 'finmind_government_bonds_yield',
    'CrudeOilPrices': 'finmind_crude_oil_prices',
    'GoldPrice': 'finmind_gold_price', 'TDCC': 'tdcc_share_distribution_raw',
    'official_trading_calendar': 'official_calendar',
}


_NO_EVENTS_SOURCES = {
    'official_trading_restrictions': frozenset((
        'twse_twtawu', 'twse_twt85u', 'twse_announcement_notice',
        'twse_announcement_punish', 'twse_twtbau2', 'tpex_cmode',
        'tpex_spendi_today', 'tpex_trading_warning_information',
        'tpex_disposal_information', 'tpex_intraday_trading_his')),
    'news_and_hot_topics_raw': frozenset((
        'topic_enrichment_ranked_feed', 'twse_news_list',
        'tpex_historical_announcement', 'twse_daily_major_information',
        'tpex_daily_major_information')),
}


def _event_snapshot_day(route_id, entry, *, row_count):
    """Snapshot day of a pinned, complete current-only event snapshot holding exactly ``row_count`` rows, else None.

    ``row_count == 0`` is the eventless snapshot that ``published_no_events`` accepts: every source receipt is empty
    and marked no_events.  A snapshot with rows must account for each of them: the declared record count is the
    number of rows read, the receipts' row counts add up to it, an empty receipt is marked no_events and a non-empty
    one is not, nothing reports an invalid row, and the snapshot as a whole is not marked eventless.  The checks on
    identity, completeness, observation day and unresolved flags are the same for both.  Never numeric or history.
    """
    expected = _NO_EVENTS_SOURCES.get(route_id) if isinstance(route_id, str) else None
    if (expected is None or type(row_count) is not int or row_count < 0 or not isinstance(entry, Mapping)
            or entry.get('route_id') != route_id or entry.get('status') != 'SUCCESS'
            or type(entry.get('record_count')) is not int or entry['record_count'] != row_count):
        return None
    selected = entry.get('selected_ref')
    if (not isinstance(entry.get('sha256'), str)
            or re.fullmatch('[0-9a-f]{64}', entry['sha256']) is None
            or type(entry.get('size')) is not int or entry['size'] < 0
            or not isinstance(entry.get('path'), str) or not entry['path']
            or not isinstance(selected, Mapping)
            or selected.get('sha256') != entry['sha256']
            or type(selected.get('size')) is not int or selected['size'] != entry['size']
            or selected.get('path') != entry['path']):
        return None
    # The entry as the producer recorded it (v1 / v2 heads alike, published_entry_view): an evidence reference or a
    # same_as_metadata marker that did not verify is an unresolved marker and refuses the proof (fail closed).
    entry = published_entry_view(entry)
    metadata = entry.get('metadata')
    if not isinstance(metadata, Mapping) or published_evidence_unresolved(metadata):
        return None
    coverage, observation = metadata.get('coverage'), metadata.get('snapshot_observation')
    receipts = metadata.get('source_receipts')
    if (not isinstance(coverage, Mapping) or not isinstance(observation, Mapping)
            or not isinstance(receipts, list) or len(receipts) != len(expected)
            or (metadata.get('no_events') is True) != (row_count == 0)
            or metadata.get('snapshot_complete') is not True
            or coverage.get('snapshot_complete') is not True
            or coverage.get('collection_scope') != 'current_only'
            or metadata.get('history_unavailable') is not True
            or coverage.get('history_complete') is not False
            or observation.get('all_sources_observed') is not True
            or observation.get('schema') != 'twstock-updater.snapshot-observation.v1'
            or observation.get('timezone') != 'Asia/Taipei'
            or any(type(coverage.get(key)) is not int or coverage[key] != len(expected)
                   for key in ('processed', 'success', 'total'))):
        return None
    # Either spelling of the observation day is read; ``day`` is canonical YYYY-MM-DD from here on and the other
    # published day fields are compared with it by date value (date directive 2026-10-06).
    day = cd.iso_day_or_none(observation.get('min_date'))
    if (day is None or not cd.same_day(observation.get('max_date'), day)
            or not cd.same_day(metadata.get('observed_asof'), day)):
        return None
    problems = ('error', 'errors', 'gaps', 'warnings', 'pending', 'not_yet_published',
                'refresh_error', 'stale', 'unknown', 'awaiting_source_release',
                'pending_dates', 'pending_release_dates', 'unresolved_prior_gaps',
                'stale_snapshot')

    def flags_clear(source):
        if not isinstance(source, Mapping) or any(source.get(key) for key in problems):
            return False
        for key in ('prior_gaps', 'unresolved_prior_reasons'):
            if key in source and (not isinstance(source[key], list) or source[key]):
                return False
        if (str(source.get('acquisition_status', '')).upper() in
                ('PARTIAL', 'FAILED', 'WAITING_QUOTA', 'UNKNOWN', 'SUCCESS_PENDING_RELEASE')
                or source.get('current_complete') is False
                or source.get('complete_for_publish') is False):
            return False
        recovery = source.get('partial_recovery')
        if recovery is None:
            return True
        if not isinstance(recovery, Mapping):
            return False
        status = str(recovery.get('status', '')).casefold()
        if status == 'resolved_for_scope':
            scope = recovery.get('requested_scope')
            if (not isinstance(scope, Mapping)
                    or scope.get('collection_scope') != 'current_only'
                    or not isinstance(recovery.get('unresolved_prior_gaps'), list)
                    or recovery['unresolved_prior_gaps']):
                return False
            first = cd.iso_day_or_none(scope.get('requested_from_date'))
            last = cd.iso_day_or_none(scope.get('expected_asof'))
            if first is None or last is None or first > day or last != day:
                return False
        elif status != 'resolved':
            return False
        return flags_clear(recovery)

    if not all(flags_clear(source) for source in (entry, metadata, coverage)):
        return None
    last_attempt = metadata.get('last_attempt')
    if last_attempt is not None and (not isinstance(last_attempt, Mapping)
            or last_attempt.get('status') not in ('SUCCESS', 'EMPTY')
            or not flags_clear(last_attempt)
            or ('coverage' in last_attempt and not flags_clear(last_attempt['coverage']))):
        return None
    diagnostics = metadata.get('diagnostics')
    if diagnostics and (not isinstance(diagnostics, Mapping) or any(diagnostics.values())):
        return None
    taipei = timezone(timedelta(hours=8))

    def aware(value):
        try:
            parsed = datetime.fromisoformat(value.replace('Z', '+00:00'))
            return parsed if parsed.tzinfo is not None and parsed.utcoffset() is not None else None
        except (AttributeError, TypeError, ValueError):
            return None

    seen, stamps, date_only, listed = set(), [], False, 0
    for source in receipts:
        if not isinstance(source, Mapping):
            return None
        source_id = source.get('source_id')
        if (not isinstance(source_id, str) or source_id not in expected or source_id in seen
                or source.get('processed') is not True or source.get('status') not in ('SUCCESS', 'EMPTY')
                or type(source.get('http_status')) is not int or source['http_status'] != 200
                or type(source.get('row_count')) is not int or source['row_count'] < 0
                or type(source.get('invalid_row_count')) is not int or source['invalid_row_count'] != 0
                or (source.get('no_events') is True) != (source['row_count'] == 0)
                or not flags_clear(source) or source.get('diagnostics')
                or not isinstance(source.get('source_sha256'), str)
                or re.fullmatch('[0-9a-f]{64}', source['source_sha256']) is None
                or type(source.get('source_size')) is not int or source['source_size'] <= 0
                or source.get('observation_timezone') != 'Asia/Taipei'
                or not cd.same_day(source.get('observation_date'), day)):
            return None
        seen.add(source_id)
        listed += source['row_count']
        if source.get('observation_precision') == 'datetime':
            stamp = aware(source.get('observed_at'))
            if (stamp is None or stamp.astimezone(taipei).date().isoformat() != day
                    or source.get('observation_basis') != 'response_retrieved_at'):
                return None
            stamps.append(stamp)
        elif (source.get('observation_precision') == 'date'
              and source.get('observation_basis') == 'cache_saved_date'
              and source.get('observed_at') is None):
            date_only = True
        else:
            return None
    if listed != row_count:
        return None
    if date_only:
        if (observation.get('precision') != 'date'
                or observation.get('observed_at_min') is not None
                or observation.get('observed_at_max') is not None):
            return None
    elif (observation.get('precision') != 'datetime'
          or aware(observation.get('observed_at_min')) != min(stamps)
          or aware(observation.get('observed_at_max')) != max(stamps)):
        return None
    return day


def published_no_events(route_id, entry, *, verified_empty=False, start_date=None,
                        end_date=None, before_count=0, formula_lookback_count=None):
    """Classify a verified empty current snapshot, never numeric or historical data."""
    if (verified_empty is not True or type(before_count) is not int or before_count != 0
            or (formula_lookback_count is not None and
                (type(formula_lookback_count) is not int or formula_lookback_count != 0))):
        return None
    day = _event_snapshot_day(route_id, entry, row_count=0)
    end_day = None if end_date is None else cd.iso_day_or_none(end_date)
    start_day = None if start_date is None else cd.iso_day_or_none(start_date)
    if (day is None or (end_date is not None and end_day != day)
            or (start_date is not None and (start_day is None or start_day > day))):
        return None
    return dict(status='available', reason='no_events', no_events=True, usable=False,
                completion_scope='current_snapshot', snapshot_asof=day,
                history_complete=False, model_usable=False)


# Per-stock reading of a complete event snapshot whose day is not the decision date: neither events nor their
# absence are proved for that date (reference status 'unverified', never a producer gap).  The third state is a
# snapshot of the decision date itself that lists, for the stock, an event announced to take effect only after that
# date (e.g. a disposition announced on D, effective from D+1): nothing is in effect on D, but 'no events' would
# hide an announcement already known on D, so it is 'unverified' too (never no_events, never a producer gap).
STOCK_SNAPSHOT_UNVERIFIED_STATES = ('current_snapshot_after_decision_date', 'current_snapshot_before_decision_date',
                                    'event_announced_effective_after_decision_date')
STOCK_EVENT_ANNOUNCED_AFTER_DECISION_STATE = STOCK_SNAPSHOT_UNVERIFIED_STATES[2]


def published_event_snapshot_day(route_id, entry, *, row_count):
    """Verified snapshot day of a pinned, complete current-only event snapshot holding exactly ``row_count`` rows
    (the same checks as the per-stock no_events proof), else None."""
    return _event_snapshot_day(route_id, entry, row_count=row_count)


def published_stock_no_events(route_id, entry, *, verified_object=False, row_count=None, stock_rows=None,
                              unknown_scope=True, start_date=None, end_date=None, announced_after_rows=0):
    """Per-stock completion of a pinned, complete current-only event snapshot (reference only).

    ``row_count`` is the number of rows read from the artifact (it must equal the declared and the receipt counts);
    ``stock_rows`` the rows of the requested stock in the requested scope.  The snapshot lists what is in effect on
    its own day, so a stock without a row proves ``no_events`` for that stock when the decision date ``end_date`` is
    the snapshot day (point in time: a current snapshot is only used on or after its day, and its absence of events
    only speaks for that day).  For another decision date it neither proves nor denies events: the result is
    ``unverified`` with the snapshot day, never a producer gap.  History and model use are never claimed.  None when
    the snapshot, the row accounting or the stock scope is not verified.
    ``announced_after_rows`` counts the stock's event rows of the same snapshot whose period starts after
    ``end_date``.  On the snapshot day they are announcements already known on the decision date, so no_events is not
    claimed: the result is ``unverified`` with state ``event_announced_effective_after_decision_date``.
    """
    if (verified_object is not True or unknown_scope is not False
            or type(stock_rows) is not int or stock_rows != 0
            or type(announced_after_rows) is not int or announced_after_rows < 0):
        return None
    entry = published_entry_view(entry)
    day = _event_snapshot_day(route_id, entry, row_count=row_count)
    # Caller bounds in either spelling; compared (and reported) as canonical YYYY-MM-DD.
    start_day = None if start_date is None else cd.iso_day_or_none(start_date)
    end_date = cd.iso_day_or_none(end_date)
    if (day is None or end_date is None
            or (start_date is not None and (start_day is None or start_day > end_date))):
        return None
    start_date = start_day
    result = dict(usable=False, model_usable=False, data_complete=False, history_complete=False,
                  completion_scope='current_snapshot_stock', snapshot_asof=day,
                  evidence_scope=dict(object_sha256=entry['sha256'], object_path=entry['path'],
                                      object_size=entry['size'], snapshot_asof=day, stock_scope='exact_stock',
                                      receipt_count=len(entry['metadata']['source_receipts'])))
    if end_date == day and announced_after_rows:
        state = STOCK_EVENT_ANNOUNCED_AFTER_DECISION_STATE
        result.update(status='unverified', reason=state, completion_state=state, no_events=False,
                      source_available_complete=False, completion_evidence_valid=False,
                      requested_scope_complete=False, completion_start_date=None, completion_end_date=None,
                      announced_after_decision_rows=announced_after_rows)
    elif end_date == day:
        result.update(status='available', reason='no_events', completion_state='no_events', no_events=True,
                      source_available_complete=True, completion_evidence_valid=True,
                      requested_scope_complete=start_date == day, completion_start_date=day,
                      completion_end_date=day)
    else:
        state = STOCK_SNAPSHOT_UNVERIFIED_STATES[0 if day > end_date else 1]
        result.update(status='unverified', reason=state, completion_state=state, no_events=False,
                      source_available_complete=False, completion_evidence_valid=False,
                      requested_scope_complete=False, completion_start_date=None, completion_end_date=None)
    return result


# ---------------------------------------------------------------------------------------------------------------
# Published head contract v1 / v2 - the binding spec is section 3 Wave 4 of
# D:/stock/docs/CLAUDE_TO_CODEX_20261005_UPDATER_HANG_TRUNCATION_DEDUP_HANDOFF.md, with the reader-side
# clarifications of three_month_impl/CLAUDE_TO_CODEX_20261005_UPDATER_SPEC_ADDENDUM.md; it is the single contract
# (it replaces the cd_final {sha256,size,ref} references and last_attempt.deduplicated_fields).  A head's ``schema``
# is ``stock-data-publication-v1`` or ``stock-data-publication-v2``; both are read.
#   * Evidence references appear only as the whole value of ``metadata.source_receipts``, ``metadata.diagnostics``,
#     ``metadata.last_attempt.source_receipts`` and ``metadata.last_attempt.diagnostics``:
#     ``{"$evidence": "stock-data-evidence-v1", "sha256", "size", "type": "list"|"object", "items"}`` names
#     ``<store>/_evidence/<sha256>.json`` whose bytes are exactly ``canonical(value)`` (``_canonical``: sort_keys,
#     compact separators, ensure_ascii=False, allow_nan=False, UTF-8).  A reader verifies size, sha256, strict JSON
#     (no duplicate key, no NaN/Infinity), that re-canonicalizing gives the same bytes, and type and items; every
#     reference is verified on its own (the same object named with another size, type or item count is refused).
#   * A ``$evidence`` key anywhere else in the entry is refused: another metadata / last_attempt field, the whole
#     ``last_attempt``, the whole ``metadata``, an entry-level field, or nested at any depth (also inside an inline
#     value of the four positions).  PublishedSources scans every pinned entry for nested keys once (skipped when
#     the pinned bytes cannot hold one: no ``$`` and no ``\u`` escape); the top-level positions are always checked.
#   * ``last_attempt`` may leave out the fields whose canonical bytes equal the metadata's and list them in
#     ``last_attempt.same_as_metadata`` (a sorted list of distinct names of ``LAST_ATTEMPT_COPY_FIELDS``, each
#     present in the metadata and absent from the attempt); ``attempt_view`` restores exactly those.  A field that
#     is absent and NOT listed means "this attempt had none" and is never filled from the metadata (today's producer
#     leaves fields out for other reasons: a retained publication whose last_attempt comes from a newer candidate,
#     the minimal Order21 attempts; filling them would combine one attempt's status with an older publication's
#     receipts and windows and could upgrade a proof - cd_final reviews A/F2).
#   * ``$evidence`` and ``same_as_metadata`` are v2 constructs: in a pinned head whose schema is not
#     ``stock-data-publication-v2`` (and in every other source kind) they are refused (``v2_construct_in_v1_head``),
#     so a v2 head rewritten as v1 by a producer that does not know the v2 format is visible instead of being read.
#     The pinned head's schema is recorded in the pinned identity (``publication_schema``, v2 heads only, so a v1
#     descriptor is byte-identical to before).
# Every entry a consumer reads goes through ``published_entry_view`` (PublishedSources hands out only views): the
# references are resolved and verified by the source's ``PublishedEvidenceStore``, ``last_attempt`` is restored, and
# whatever does not verify (missing or corrupt object, malformed reference, reference at another position, invalid
# same_as_metadata, v2 construct in a v1 head, no store) becomes an explicit ``EVIDENCE_UNRESOLVED_PREFIX`` marker in
# place of the value (``EVIDENCE_UNRESOLVED_KEY`` in the viewed metadata when the whole metadata or an entry-level
# field is concerned): every proof that reads the entry is refused (fail closed), the marker is disclosed in the
# completion diagnostics, and PublishedSources adds one source diagnostic per such entry when it pins the head (so
# every descriptor and report gap list carries it).  Payload rows are unaffected (verified by their own sha256).
# A v1 entry (no reference, no same_as_metadata) is returned unchanged (the same object), so a v1 head is read
# exactly as before; a v2 head proves exactly what the equivalent v1 head proves.
PUBLICATION_SCHEMA_V1 = 'stock-data-publication-v1'
PUBLICATION_SCHEMA_V2 = 'stock-data-publication-v2'
PUBLICATION_SCHEMAS = (PUBLICATION_SCHEMA_V1, PUBLICATION_SCHEMA_V2)
EVIDENCE_REFERENCE_KEY = '$evidence'
EVIDENCE_REFERENCE_SCHEMA = 'stock-data-evidence-v1'
EVIDENCE_REFERENCE_KEYS = frozenset((EVIDENCE_REFERENCE_KEY, 'sha256', 'size', 'type', 'items'))
EVIDENCE_FIELDS = ('source_receipts', 'diagnostics')
EVIDENCE_DIRNAME = '_evidence'
EVIDENCE_UNRESOLVED_PREFIX = 'published_evidence_unresolved:'
# Key of a viewed metadata that marks the whole entry unresolved (a reference as the whole metadata or outside it).
EVIDENCE_UNRESOLVED_KEY = 'published_evidence_unresolved'
EVIDENCE_REFERENCE_NOT_ALLOWED = 'evidence_reference_not_allowed'
V2_CONSTRUCT_IN_V1_HEAD = 'v2_construct_in_v1_head'
LAST_ATTEMPT_SAME_AS_KEY = 'same_as_metadata'
# Fields the producer copies from an entry's metadata into ``metadata.last_attempt`` when it records an attempt
# (twstock_updater stock_shared_store._publication_attempt; its Order21 branch copies coverage/gaps/errors/error);
# only these may be listed in ``same_as_metadata``.
LAST_ATTEMPT_COPY_FIELDS = (
    'coverage', 'gaps', 'errors', 'error', 'reason', 'diagnostics', 'requested_from_date',
    'effective_from_date', 'expected_asof', 'requested_to_date', 'requested_scope', 'collection_scope',
    'source_receipts', 'snapshot_observation', 'snapshot_complete', 'observed_asof', 'no_data_evidence',
    'history_unavailable', 'due_scope', 'pending_release_dates', 'pending_release_evidence',
    'source_expected_asof', 'source_resolved_demand')


class PublishedEvidenceError(ValueError):
    """A published evidence reference, evidence object or same_as_metadata marker that does not verify."""


def is_evidence_reference(value):
    """True for a value marked as an evidence reference (``$evidence`` key), well formed or not."""
    return isinstance(value, Mapping) and EVIDENCE_REFERENCE_KEY in value


def holds_evidence_key(value):
    """True when ``value`` is, or holds at any depth, a mapping with a ``$evidence`` key (iterative, no recursion)."""
    stack = [value]
    while stack:
        item = stack.pop()
        if isinstance(item, Mapping):
            if EVIDENCE_REFERENCE_KEY in item:
                return True
            stack.extend(item.values())
        elif isinstance(item, (list, tuple)):
            stack.extend(item)
    return False


def _evidence_reference_fields(reference):
    """(sha256, size, type, items) of a well-formed evidence reference, else PublishedEvidenceError."""
    if (not isinstance(reference, Mapping) or set(reference) != EVIDENCE_REFERENCE_KEYS
            or reference[EVIDENCE_REFERENCE_KEY] != EVIDENCE_REFERENCE_SCHEMA
            or not isinstance(reference['sha256'], str) or re.fullmatch('[0-9a-f]{64}', reference['sha256']) is None
            or type(reference['size']) is not int or reference['size'] < 0
            or reference['type'] not in ('list', 'object')
            or type(reference['items']) is not int or reference['items'] < 0):
        raise PublishedEvidenceError('evidence_reference_invalid')
    return reference['sha256'], reference['size'], reference['type'], reference['items']


class PublishedEvidenceStore:
    """Read-only view of a published store's evidence objects ``<store>/_evidence/<sha256>.json`` (append-only).

    ``load`` returns a value only after verifying its file completely: unchanged file identity while read, exact
    size and sha256, strict JSON (no duplicate key, no NaN/Infinity), canonical bytes, and the reference's type and
    item count.  There is no byte cap.  A verified value is kept once per object with its verified byte size
    (memory: the distinct objects this reader resolved, which the v1 head held inline anyway) and is shared, read
    only, by every view naming it; every later reference to it is still checked for size, type and item count, so
    the outcome never depends on the order in which references are resolved.
    """

    def __init__(self, store_root):
        self.directory = Path(store_root) / EVIDENCE_DIRNAME
        self._values = {}

    def load(self, reference):
        digest, size, kind, items = _evidence_reference_fields(reference)
        cached = self._values.get(digest)
        if cached is None:
            path = self.directory / (digest + '.json')
            try:
                with path.open('rb') as handle:
                    before = _file_identity(os.fstat(handle.fileno()))
                    raw = handle.read()
                    after = _file_identity(os.fstat(handle.fileno()))
                current = _file_identity(path.stat())
            except FileNotFoundError:
                raise PublishedEvidenceError(f'evidence_object_missing:{digest}') from None
            except OSError as exc:
                raise PublishedEvidenceError(f'evidence_object_unreadable:{digest}:{type(exc).__name__}') from exc
            if before != after or before != current or len(raw) != before[2]:
                raise PublishedEvidenceError(f'evidence_object_changed_during_read:{digest}')
            if len(raw) != size or _sha(raw) != digest:
                raise PublishedEvidenceError(f'evidence_object_mismatch:{digest}')
            try:
                value = _json(raw)
                canonical = _canonical(value)
            except (ValueError, TypeError, UnicodeError, RecursionError) as exc:
                raise PublishedEvidenceError(f'evidence_object_json_invalid:{digest}') from exc
            if canonical != raw:
                raise PublishedEvidenceError(f'evidence_object_not_canonical:{digest}')
            cached = self._values[digest] = (len(raw), value)
            del raw, canonical
        elif cached[0] != size:
            raise PublishedEvidenceError(f'evidence_object_mismatch:{digest}')
        value = cached[1]
        if not isinstance(value, list if kind == 'list' else dict) or len(value) != items:
            raise PublishedEvidenceError(f'evidence_object_shape_mismatch:{digest}')
        return value


def _evidence_marker(field, reason):
    return f'{EVIDENCE_UNRESOLVED_PREFIX}{field}:{reason}'


def is_evidence_unresolved(value):
    """True for the marker that stands in place of a value that did not verify."""
    return isinstance(value, str) and value.startswith(EVIDENCE_UNRESOLVED_PREFIX)


def _resolved_evidence(store, field, value):
    """The verified value of the evidence reference ``value`` at ``field``, else the unresolved marker."""
    if field not in EVIDENCE_FIELDS:
        return _evidence_marker(field, EVIDENCE_REFERENCE_NOT_ALLOWED)
    if store is None:
        return _evidence_marker(field, 'evidence_store_unavailable')
    try:
        return store.load(value)
    except PublishedEvidenceError as exc:
        return _evidence_marker(field, str(exc))


def attempt_view(metadata):
    """``metadata['last_attempt']`` as the producer recorded it (the v1 shape), evidence references unresolved.

    The fields listed in ``same_as_metadata`` read ``metadata``; every other field is the attempt's own, and an
    absent, unlisted field stays absent.  An attempt without the marker is returned unchanged.  An invalid marker
    (not a sorted list of distinct ``LAST_ATTEMPT_COPY_FIELDS`` names, a listed field the metadata does not have or
    the attempt also holds) raises PublishedEvidenceError (callers fail closed).
    """
    attempt = metadata.get('last_attempt') if isinstance(metadata, Mapping) else None
    if not isinstance(attempt, Mapping) or LAST_ATTEMPT_SAME_AS_KEY not in attempt:
        return attempt
    same = attempt[LAST_ATTEMPT_SAME_AS_KEY]
    if (not isinstance(same, list) or any(not isinstance(field, str) for field in same)
            or same != sorted(set(same))
            or any(field not in LAST_ATTEMPT_COPY_FIELDS or field not in metadata or field in attempt
                   for field in same)):
        raise PublishedEvidenceError('last_attempt_same_as_metadata_invalid')
    view = {key: value for key, value in attempt.items() if key != LAST_ATTEMPT_SAME_AS_KEY}
    view.update((field, metadata[field]) for field in same)
    return view


def _construct_reason(constructs):
    return V2_CONSTRUCT_IN_V1_HEAD if constructs is False else EVIDENCE_REFERENCE_NOT_ALLOWED


def _viewed_value(field, value, store, constructs, deep):
    """(changed, value) for one top-level value of metadata or last_attempt (see ``published_entry_view``)."""
    if is_evidence_reference(value):
        if constructs is False:
            return True, _evidence_marker(field, V2_CONSTRUCT_IN_V1_HEAD)
        return True, _resolved_evidence(store, field, value)
    if deep and isinstance(value, (Mapping, list, tuple)) and holds_evidence_key(value):
        return True, _evidence_marker(field, _construct_reason(constructs))
    return False, value


def _attempt_view_of(attempt, metadata, store, constructs, deep):
    """The mapping ``attempt`` (= ``metadata['last_attempt']``) restored and resolved; the same object when there is
    nothing to do.  ``metadata`` is the metadata whose own references were already resolved."""
    view = None
    if (LAST_ATTEMPT_SAME_AS_KEY in attempt
            and not is_evidence_unresolved(attempt[LAST_ATTEMPT_SAME_AS_KEY])):     # a marker stays as it is
        marker = None
        if constructs is False:
            marker = _evidence_marker(LAST_ATTEMPT_SAME_AS_KEY, V2_CONSTRUCT_IN_V1_HEAD)
        else:
            try:
                view = dict(attempt_view(metadata))
            except PublishedEvidenceError as exc:
                marker = _evidence_marker(LAST_ATTEMPT_SAME_AS_KEY, str(exc))
        if marker is not None:
            # nothing listed is restored and the marker stands in place of same_as_metadata: fail closed
            view = dict(attempt)
            view[LAST_ATTEMPT_SAME_AS_KEY] = marker
    for field, value in attempt.items():
        if field == LAST_ATTEMPT_SAME_AS_KEY:
            continue
        changed, viewed = _viewed_value(field, value, store, constructs, deep)
        if changed:
            view = dict(attempt) if view is None else view
            view[field] = viewed
    return attempt if view is None else view


def _metadata_view(metadata, store, constructs=None, deep=False):
    """Metadata with its evidence references resolved and its last_attempt restored (the same mapping when there is
    nothing to do; never mutated, never deep-copied: unchanged values are shared)."""
    view = None
    for field, value in metadata.items():
        if field == 'last_attempt':
            continue
        changed, viewed = _viewed_value(field, value, store, constructs, deep)
        if changed:
            view = dict(metadata) if view is None else view
            view[field] = viewed
    base = metadata if view is None else view
    attempt = metadata.get('last_attempt')
    restored = attempt
    if is_evidence_reference(attempt) or (
            deep and isinstance(attempt, (list, tuple)) and holds_evidence_key(attempt)):
        # the whole last_attempt is never a reference (only its two evidence fields may be): fail closed
        restored = _evidence_marker('last_attempt', _construct_reason(constructs))
    elif isinstance(attempt, Mapping):
        restored = _attempt_view_of(attempt, base, store, constructs, deep)
    if restored is not attempt:
        view = dict(metadata) if view is None else view
        view['last_attempt'] = restored
    return metadata if view is None else view


def published_entry_view(entry, store=None, *, constructs=None, deep=False):
    """The published entry as the producer recorded it, for v1 and v2 heads alike (see PUBLICATION_SCHEMAS).

    ``store`` (a PublishedEvidenceStore) resolves evidence references; without one a reference is an unresolved
    marker (fail closed).  ``constructs``: True for an entry of a ``stock-data-publication-v2`` head, False for any
    other pinned entry (v2 constructs are refused), None when unknown (an entry handed to a proof directly: read by
    structure).  ``deep`` also scans every value for a nested ``$evidence`` key (PublishedSources, once per pinned
    entry); the top-level positions are always checked.  A v1 entry and an entry already viewed are returned
    unchanged (the same object).
    """
    if not isinstance(entry, Mapping):
        return entry
    reason = _construct_reason(constructs)
    changed = {}
    flagged = EVIDENCE_REFERENCE_KEY in entry
    for field, value in entry.items():
        if field != 'metadata' and (is_evidence_reference(value) or (
                deep and isinstance(value, (Mapping, list, tuple)) and holds_evidence_key(value))):
            changed[field] = _evidence_marker(f'entry.{field}', reason)
            flagged = True
    metadata = entry.get('metadata')
    if is_evidence_reference(metadata) or (
            deep and isinstance(metadata, (list, tuple)) and holds_evidence_key(metadata)):
        viewed = {EVIDENCE_UNRESOLVED_KEY: _evidence_marker('metadata', reason)}
    elif isinstance(metadata, Mapping):
        viewed = _metadata_view(metadata, store, constructs, deep)
    else:
        viewed = metadata
    if flagged and not (isinstance(viewed, Mapping) and is_evidence_unresolved(viewed.get(EVIDENCE_UNRESOLVED_KEY))):
        # a reference outside metadata: the whole entry is unresolved (every proof refused, disclosed)
        viewed = dict(viewed) if isinstance(viewed, Mapping) else {}
        viewed[EVIDENCE_UNRESOLVED_KEY] = _evidence_marker('entry', reason)
    if not changed and viewed is metadata:
        return entry
    view = dict(entry)
    view.update(changed)
    if viewed is not metadata:
        view['metadata'] = viewed
    return view


def last_attempt_unresolved(attempt):
    """Fields of a viewed last_attempt holding the unresolved marker (empty for any other value)."""
    if not isinstance(attempt, Mapping):
        return ()
    return tuple(sorted(str(field) for field, value in attempt.items() if is_evidence_unresolved(value)))


def published_evidence_unresolved(metadata):
    """Fields of a viewed metadata (``last_attempt.<field>`` for its attempt) holding the unresolved marker."""
    if not isinstance(metadata, Mapping):
        return ()
    fields = [str(field) for field, value in metadata.items() if is_evidence_unresolved(value)]
    fields.extend('last_attempt.' + field for field in last_attempt_unresolved(metadata.get('last_attempt')))
    return tuple(sorted(fields))


def published_evidence_unresolved_reasons(metadata):
    """The sorted distinct reasons of the markers ``published_evidence_unresolved`` reports (for disclosure)."""
    if not isinstance(metadata, Mapping):
        return ()
    attempt = metadata.get('last_attempt')
    values = list(metadata.values()) + (list(attempt.values()) if isinstance(attempt, Mapping) else [])
    return tuple(sorted({value[len(EVIDENCE_UNRESOLVED_PREFIX):].split(':', 1)[-1]
                         for value in values if is_evidence_unresolved(value)}))


def resolved_last_attempt(metadata, store=None):
    """The viewed ``last_attempt`` of ``metadata`` (``published_entry_view``): same_as_metadata restored, evidence
    references resolved with ``store`` (markers otherwise).  Unchanged (the same object) when there is nothing to
    do; a ``last_attempt`` that is not a mapping is returned as is (a reference becomes the unresolved marker), so
    callers keep rejecting it."""
    metadata = metadata if isinstance(metadata, Mapping) else {}
    return _metadata_view(metadata, store).get('last_attempt')


_COMPLETION_SUMMARY_KEYS = (
    'status', 'reason', 'warnings', 'gaps', 'errors', 'error', 'requested_from_date',
    'expected_asof', 'completion_scope', 'total', 'success', 'processed',
    'data_complete', 'history_complete', 'source_available_complete',
    'retrievable_history_complete', 'due_total', 'due_success', 'due_scope_complete',
    'pending_release_dates', 'source_unavailable_dates', 'missing_dates',
    'prior_gaps', 'unresolved_prior_gaps', 'unresolved_prior_reasons',
    'kind', 'date', 'source_id', 'market', 'dataset', 'rule_id', 'from_date', 'to_date',
    'dates', 'complete', 'collection_scope', 'snapshot_complete', 'no_data_reason')


def _completion_summary(value, depth=0, bounded=True):
    """Bound diagnostics without carrying raw receipts or partition state.

    ``bounded=False`` keeps the same keys but every item, every nesting level and whole strings: the evidence a
    completion decision reads (``published_completion`` decides conflicts on it); only the reported diagnostics
    are bounded.
    """
    if isinstance(value, Mapping):
        if bounded and depth > 3:
            return {}
        keys = _COMPLETION_SUMMARY_KEYS + ('coverage', 'partial_recovery', 'requested_scope', 'due_scope')
        result = {key: _completion_summary(value[key], depth + 1, bounded)
                  for key in keys if key in value}
        if depth == 0:
            for key in ('source_receipts', 'source_states', 'partition_fingerprints'):
                if isinstance(value.get(key), (list, tuple, Mapping)):
                    result[key + '_count'] = len(value[key])
            # A value that did not verify (evidence reference, same_as_metadata) is always disclosed, any field.
            result.update({key: item for key, item in value.items() if is_evidence_unresolved(item)})
        return result
    if isinstance(value, (list, tuple)):
        return [_completion_summary(item, depth + 1, bounded) for item in (value[:16] if bounded else value)]
    if isinstance(value, str):
        return value[:1024] if bounded else value
    return value if value is None or type(value) in (bool, int, float) else None


def _completion_day(value):
    """A published calendar day in either spelling -> canonical YYYY-MM-DD, else None (use the returned value)."""
    return cd.iso_day_or_none(value)


def _completion_bound(value):
    """A caller bound in either spelling -> canonical YYYY-MM-DD; a value that is not a date is kept as given."""
    day = cd.iso_day_or_none(value)
    return value if day is None else day


def _same_dated(left, right):
    """Equality of published evidence values in which calendar-day strings compare by date value (two spellings of
    one day are equal); every other value compares exactly as ``==`` does."""
    if isinstance(left, Mapping) and isinstance(right, Mapping):
        return set(left) == set(right) and all(_same_dated(left[key], right[key]) for key in left)
    if isinstance(left, (list, tuple)) and isinstance(right, (list, tuple)):
        return (type(left) is type(right) and len(left) == len(right)
                and all(_same_dated(a, b) for a, b in zip(left, right)))
    if isinstance(left, str) and isinstance(right, str) and left != right:
        return cd.same_day(left, right)
    return left == right


def _natural_key_value(field, value):
    """Natural-key part: the ``date`` field by its calendar day (two spellings of one day are one key)."""
    return _completion_bound(value) if field == 'date' else value


def _row_by_value(row):
    """A row for the duplicate-content comparison: its ``date`` by calendar day, every other field unchanged."""
    if isinstance(row, Mapping) and 'date' in row:
        return dict(row, date=_completion_bound(row['date']))
    return row


def _completion_time(value):
    try:
        stamp = datetime.fromisoformat(value.replace('Z', '+00:00'))
        return stamp if stamp.tzinfo is not None and stamp.utcoffset() is not None else None
    except (AttributeError, TypeError, ValueError):
        return None


def _completion_clear(value):
    return isinstance(value, Mapping) and not any(value.get(key) for key in (
        'gaps', 'errors', 'error', 'warnings', 'prior_gaps', 'unresolved_prior_gaps',
        'unresolved_prior_reasons', 'missing_dates', 'calendar_gaps', 'pending_dates',
        'unknown', 'stale', 'refresh_error', 'stale_snapshot', 'diagnostics'))


def _completion_conflict(value):
    known = {'duplicate_natural_key_conflict', 'source_timestamp_unknown_or_not_newer',
             'order21_conflict_unit_quarantined', 'unit_conflict', 'value_conflict', 'conflict'}
    if isinstance(value, str):
        return value.casefold() in known
    if isinstance(value, (list, tuple)):
        return any(_completion_conflict(item) for item in value)
    if not isinstance(value, Mapping):
        return False
    if value.get('resolved') is True or value.get('status') in ('resolved', 'resolved_for_scope'):
        return False
    return any(_completion_conflict(value[key]) for key in (
        'status', 'reason', 'kind', 'warnings', 'gaps', 'errors', 'error', 'prior_gaps',
        'unresolved_prior_gaps', 'unresolved_prior_reasons', 'coverage', 'last_attempt',
        'partial_recovery') if key in value)


def _government_empty_receipt(receipt, day, pending=False):
    if not isinstance(receipt, Mapping):
        return False
    expected = dict(schema='finmind-source-availability-v1', provider='finmind',
        route_id='finmind_government_bank', dataset='TaiwanStockGovernmentBankBuySell',
        kind='data_single_day', data_id=None, start=day, date=day, end=None,
        provider_message='success', status='EMPTY', data_complete=False,
        source_url='https://finmind.github.io/tutor/TaiwanMarket/Chip/')
    if (not _completion_day(day) or any(key not in receipt or not _same_dated(receipt[key], value)
            for key, value in expected.items()) or not _completion_clear(receipt)
            or any(type(receipt.get(key)) is not int or receipt[key] != value
                   for key, value in (('http_status', 200), ('provider_status', 200), ('row_count', 0)))
            or not isinstance(receipt.get('sha256'), str)
            or re.fullmatch('[0-9a-f]{64}', receipt['sha256']) is None
            or receipt.get('raw_sha256') != receipt['sha256']):
        return False
    if not pending:
        return (day == '2023-01-11'
                and receipt.get('rule_id') == 'finmind.government_bank.whole_day_unavailable.2023-01-11'
                and receipt.get('request_complete') is True
                and receipt.get('no_data_reason') == 'provider_declared_unavailable'
                and not receipt.get('pending_release'))
    observed, due = (_completion_time(receipt.get(key)) for key in ('observed_at', 'publication_due_at'))
    taipei = timezone(timedelta(hours=8))
    return (receipt.get('pending_release') is True and receipt.get('no_data_confirmed') is False
            and receipt.get('request_complete') is False and receipt.get('history_complete') is False
            and receipt.get('no_data_reason') == 'not_yet_published'
            and observed is not None and due is not None and observed < due
            and observed.astimezone(taipei).date().isoformat() == day
            and due.astimezone(taipei).isoformat() == day + 'T23:30:00+08:00')


def _official_empty_receipt(receipt, day):
    if not isinstance(receipt, Mapping) or not _completion_day(day):
        return False
    slash_day = day.replace('-', '/')
    requests = {
        'TWSE': ('twse_daily_all', 'GET', 'https://www.twse.com.tw/rwd/zh/afterTrading/MI_INDEX?'
                 + urlencode(dict(date=day.replace('-', ''), type='ALL', response='json')), None),
        'TPEx': ('tpex_mainboard_daily', 'GET', 'https://www.tpex.org.tw/www/zh-tw/afterTrading/dailyQuotes?'
                 + urlencode(dict(date=slash_day, response='json', id='')), None),
        'Emerging': ('tpex_emerging_daily', 'POST',
                     'https://www.tpex.org.tw/www/zh-tw/emerging/des010',
                     hashlib.sha256(urlencode(dict(date=slash_day, response='json')).encode('ascii')).hexdigest()),
    }
    request = requests.get(receipt.get('market')) if isinstance(receipt.get('market'), str) else None
    evidence = receipt.get('no_data_evidence')
    if request is None or not isinstance(evidence, Mapping):
        return False
    source, method, url, body_sha = request
    observed = _completion_time(evidence.get('observed_at'))
    taipei_day = observed.astimezone(timezone(timedelta(hours=8))).date().isoformat() if observed else None
    return (receipt.get('source_id') == source and cd.same_day(receipt.get('date'), day)
            and receipt.get('status') == 'EMPTY' and type(receipt.get('row_count')) is int
            and receipt['row_count'] == 0 and type(receipt.get('http_status')) is int
            and receipt['http_status'] == 200 and receipt.get('no_data_confirmed') is True
            and receipt.get('no_data_reason') == 'explicit_official_no_data'
            and receipt.get('not_yet_published') is False and _completion_clear(receipt)
            and evidence.get('scope') == 'single_market_day' and evidence.get('market') == receipt['market']
            and evidence.get('source_id') == source and cd.same_day(evidence.get('date'), day)
            and evidence.get('stat') in ('\u67e5\u7121\u8cc7\u6599', '\u5f88\u62b1\u6b49\uff0c\u6c92\u6709\u7b26\u5408\u689d\u4ef6\u7684\u8cc7\u6599!')
            and type(evidence.get('row_count')) is int and evidence['row_count'] == 0
            and evidence.get('not_yet_published') is False
            and evidence.get('publication_availability') == 'past_session_explicit_no_data'
            and observed is not None and taipei_day > day
            and cd.same_day(evidence.get('observed_taipei_date'), taipei_day)
            and _completion_time(receipt.get('retrieved_at')) == observed
            and isinstance(evidence.get('body_sha256'), str)
            and re.fullmatch('[0-9a-f]{64}', evidence['body_sha256']) is not None
            and (receipt.get('sha256') is None or receipt['sha256'] == evidence['body_sha256'])
            and receipt.get('method') == method and receipt.get('url') == url
            and evidence.get('request_identity') == dict(method=method, url=url, body_sha256=body_sha)
            and _completion_clear(evidence))


def published_completion(route_id, entry, *, verified_object=False, query_empty=False,
                         global_empty=False, start_date=None, end_date=None, before_count=0,
                         formula_lookback_count=None):
    """Interpret only pinned, scoped published proof; never manufacture numeric completion."""
    # Caller bounds in either spelling are compared as canonical YYYY-MM-DD (a non-date bound is kept as given).
    start_date, end_date = _completion_bound(start_date), _completion_bound(end_date)
    # v1 and v2 heads prove the same: the entry as the producer recorded it (published_entry_view: same_as_metadata
    # restored, evidence references resolved and verified by the PublishedSources that handed the entry out).
    entry = published_entry_view(entry if isinstance(entry, Mapping) else {})
    metadata = entry.get('metadata') if isinstance(entry.get('metadata'), Mapping) else {}
    attempt = metadata.get('last_attempt')
    proof = attempt if isinstance(attempt, Mapping) else metadata
    coverage = proof.get('coverage') if isinstance(proof.get('coverage'), Mapping) else {}
    producer = entry.get('status')
    producer = producer if isinstance(producer, str) else None
    gaps = []
    for source in (entry, metadata):
        for key in ('gaps', 'prior_gaps', 'unresolved_prior_gaps', 'unresolved_prior_reasons'):
            values = source.get(key)
            if isinstance(values, (list, tuple)):
                gaps.extend(_completion_summary(values))
    diagnostic = dict(published=_completion_summary(metadata), last_attempt=_completion_summary(attempt))
    # The conflict decision reads the complete pinned evidence (every item and nesting level of the same keys);
    # only the reported diagnostic above is bounded.
    state = ('conflict' if any(_completion_conflict(_completion_summary(value, bounded=False))
                               for value in (metadata, attempt))
             else 'unknown_empty' if query_empty else 'unknown')
    positive_status = str(producer).lower() in ('success', 'complete', 'available', 'committed', '200')
    result = dict(status='missing' if query_empty else (
        'available' if positive_status and state != 'conflict' else 'partial'),
        reason=state, producer_status=producer, completion_state=state,
        source_available_complete=False,
        data_complete=False if query_empty or coverage.get('data_complete') is False else None,
        history_complete=False if query_empty or coverage.get('history_complete') is False else None,
        usable=not query_empty,
        model_usable=False if query_empty or not positive_status or state == 'conflict' else None,
        last_attempt=_completion_summary(attempt), producer_gaps=gaps[:16],
        completion_reasons=diagnostic, completion_evidence_valid=False,
        completion_scope=(coverage.get('completion_scope')
                          if isinstance(coverage.get('completion_scope'), str) else None),
        completion_start_date=None, completion_end_date=None,
        evidence_scope=dict(route_id=route_id if isinstance(route_id, str) else None,
            requested_from_date=_completion_day(proof.get('requested_from_date')),
            expected_asof=_completion_day(proof.get('expected_asof'))),
        requested_scope_complete=False, due_scope_complete=False)
    selected = entry.get('selected_ref')
    if published_evidence_unresolved(metadata):
        # An evidence reference or same_as_metadata marker that did not verify: no proof (fail closed, disclosed).
        return result
    pinned = (verified_object is True and entry.get('route_id') == route_id
        and type(entry.get('record_count')) is int and entry['record_count'] >= 0
        and isinstance(entry.get('sha256'), str) and re.fullmatch('[0-9a-f]{64}', entry['sha256']) is not None
        and type(entry.get('size')) is int and entry['size'] >= 0
        and isinstance(entry.get('path'), str) and bool(entry['path']) and isinstance(selected, Mapping)
        and selected.get('path') == entry['path'] and selected.get('sha256') == entry['sha256']
        and type(selected.get('size')) is int and selected['size'] == entry['size']
        and (global_empty is not True or entry['record_count'] == 0))
    if not pinned or producer not in ('SUCCESS', 'PARTIAL') or type(before_count) is not int or before_count != 0 or (
            formula_lookback_count is not None and
            (type(formula_lookback_count) is not int or formula_lookback_count != 0)):
        return result
    no_events = published_no_events(route_id, entry, verified_empty=global_empty is True and query_empty is True,
        start_date=start_date, end_date=end_date, before_count=before_count,
        formula_lookback_count=formula_lookback_count)
    if no_events:
        result.update(no_events, completion_state='no_events', source_available_complete=True,
            data_complete=False, completion_evidence_valid=True,
            requested_scope_complete=start_date == no_events['snapshot_asof'] == end_date,
            completion_start_date=no_events['snapshot_asof'], completion_end_date=no_events['snapshot_asof'])
        result['evidence_scope'].update(object_sha256=entry['sha256'], object_path=entry['path'],
            object_size=entry['size'], snapshot_asof=no_events['snapshot_asof'],
            receipt_count=len(metadata['source_receipts']))
        return result
    if route_id not in ('finmind_government_bank', 'official_market_daily'):
        return result
    first, last = _completion_day(proof.get('requested_from_date')), _completion_day(proof.get('expected_asof'))
    receipts = proof.get('source_receipts')
    if (not first or not last or first > last
            or start_date != first or end_date != last or not isinstance(receipts, list) or not receipts
            or ('last_attempt' in metadata and (not isinstance(attempt, Mapping)
                or attempt.get('status') not in ('SUCCESS', 'EMPTY')))
            or not _completion_clear(proof) or not _completion_clear(coverage)):
        return result
    result.update(completion_start_date=first, completion_end_date=last)
    pending = False
    if route_id == 'finmind_government_bank':
        if (first != last or len(receipts) != 1 or coverage.get('completion_scope') != 'source_available'
                or coverage.get('data_complete') is not False or coverage.get('history_complete') is not False):
            return result
        receipt = receipts[0]
        pending = _government_empty_receipt(receipt, first, pending=True)
        if pending:
            if (not _same_dated(coverage.get('requested_scope'), dict(from_date=first, to_date=last, dates=[first]))
                    or not _same_dated(coverage.get('pending_release_dates'), [first])
                    or not _same_dated(coverage.get('pending_release_evidence'), receipts)
                    or coverage.get('due_scope') != dict(dates=[], total=0, success=0, complete=True)
                    or any(type(coverage['due_scope'].get(key)) is not int
                           for key in ('total', 'success'))
                    or coverage.get('due_scope_complete') is not True
                    or any(type(coverage.get(key)) is not int or coverage[key] != value for key, value
                           in (('total', 1), ('success', 0), ('due_total', 0), ('due_success', 0)))):
                return result
            result.update(status='missing' if query_empty else 'partial', reason='awaiting_source_release',
                completion_state='pending_release', completion_evidence_valid=True,
                completion_scope='source_available', due_scope_complete=True, usable=not query_empty,
                model_usable=False, data_complete=False, history_complete=False,
                pending_release_dates=[first])
        elif (_government_empty_receipt(receipt, first)
                and coverage.get('retrievable_history_complete') is True
                and _same_dated(coverage.get('source_unavailable_dates'), [first])
                and _same_dated(coverage.get('source_availability_evidence'), receipts)
                and all(type(coverage.get(key)) is int and coverage[key] == 1 for key in ('total', 'success'))):
            state = 'source_unavailable'
        else:
            return result
    else:
        if (coverage.get('calendar_verified') is not True or coverage.get('units')
                or first != last or len(receipts) != 3
                or not all(isinstance(r, Mapping) and isinstance(r.get('market'), str) for r in receipts)
                or {r.get('market') for r in receipts if isinstance(r, Mapping)} != {'TWSE', 'TPEx', 'Emerging'}
                or not all(_official_empty_receipt(receipt, first) for receipt in receipts)
                or any(type(coverage.get(key)) is not int or coverage[key] != 3 for key in ('total', 'success'))):
            return result
        state = 'confirmed_no_data'
    result['evidence_scope'].update(object_sha256=entry['sha256'], object_path=entry['path'],
        object_size=entry['size'], source_id=receipt.get('source_id') if len(receipts) == 1 else 'all_markets',
        receipt_count=len(receipts), receipt_sha256=(receipt.get('raw_sha256') if len(receipts) == 1
            else None))
    result['evidence_scope']['receipts'] = [dict(
        source_id=r.get('source_id', r.get('provider')), date=r.get('date'),
        market=r.get('market'), dataset=r.get('dataset'), rule_id=r.get('rule_id'),
        body_sha256=(r.get('raw_sha256') if route_id == 'finmind_government_bank'
                     else r['no_data_evidence'].get('body_sha256')),
        observed_at=r.get('observed_at', r.get('retrieved_at')),
        publication_due_at=r.get('publication_due_at')) for r in receipts]
    if pending:
        return result
    if (query_empty is not True or not _completion_clear(entry) or not _completion_clear(metadata)
            or any(source.get(key) for source in (entry, metadata, proof, coverage)
                   for key in ('pending', 'not_yet_published', 'pending_release',
                               'pending_release_dates', 'awaiting_source_release'))
            or (metadata.get('partial_recovery') is not None and
                (not isinstance(metadata['partial_recovery'], Mapping)
                 or metadata['partial_recovery'].get('status') != 'resolved'
                 or not _completion_clear(metadata['partial_recovery'])))):
        return result
    result.update(status='available' if producer == 'SUCCESS' else 'partial', reason=state,
        completion_state=state, source_available_complete=True, data_complete=False,
        history_complete=False, usable=False, model_usable=False, completion_evidence_valid=True,
        completion_scope='source_available', requested_scope_complete=True)
    return result


PUBLISHED_COMPLETION_FIELDS = (
    'reason', 'producer_status', 'completion_state', 'source_available_complete',
    'data_complete', 'history_complete', 'usable', 'model_usable', 'last_attempt',
    'producer_gaps', 'completion_reasons', 'completion_evidence_valid', 'completion_scope',
    'completion_start_date', 'completion_end_date', 'evidence_scope',
    'requested_scope_complete', 'due_scope_complete', 'pending_release_dates',
    'no_events', 'snapshot_asof', 'source_completions')


def published_completion_fields(proofs):
    """Combine bounded per-artifact diagnostics, without widening their proved scope."""
    proofs = list(proofs)
    if not proofs:
        return {}
    result = {key: proofs[0][key] for key in PUBLISHED_COMPLETION_FIELDS if key in proofs[0]}
    if len(proofs) == 1:
        return result
    for key in ('producer_status', 'completion_state', 'completion_scope',
                'completion_start_date', 'completion_end_date', 'snapshot_asof', 'reason'):
        values = [proof.get(key) for proof in proofs]
        if any(value != values[0] for value in values):
            result[key] = ('mixed' if key in ('completion_state', 'reason') else
                           values[:16] if key == 'producer_status' else None)
    if any(proof.get('completion_state') == 'conflict' for proof in proofs):
        result.update(completion_state='conflict', reason='conflict')
    for key in ('source_available_complete', 'completion_evidence_valid',
                'requested_scope_complete', 'due_scope_complete', 'no_events'):
        result[key] = all(proof.get(key) is True for proof in proofs)
    for key in ('data_complete', 'history_complete', 'model_usable'):
        values = [proof.get(key) for proof in proofs]
        result[key] = False if False in values else None
    if (result.get('completion_state') in ('mixed', 'conflict') or result.get('reason') == 'mixed'
            or any(any(proof.get(key) != proofs[0].get(key) for proof in proofs)
                   for key in ('completion_scope', 'completion_start_date',
                               'completion_end_date', 'snapshot_asof'))):
        result.update(source_available_complete=False, requested_scope_complete=False,
                      due_scope_complete=False, completion_evidence_valid=False,
                      no_events=False, data_complete=False, history_complete=False, model_usable=False)
    result['usable'] = any(proof.get('usable') is True for proof in proofs)
    result['last_attempt'] = [proof.get('last_attempt') for proof in proofs[:16]]
    result['evidence_scope'] = [proof.get('evidence_scope') for proof in proofs[:16]]
    result['completion_reasons'] = [proof.get('completion_reasons') for proof in proofs[:16]]
    result['producer_gaps'] = [gap for proof in proofs for gap in proof.get('producer_gaps', [])][:16]
    result['source_completions'] = [{key: proof.get(key) for key in (
        'source_id', 'producer_status', 'completion_state', 'reason', 'completion_scope',
        'source_available_complete', 'data_complete', 'history_complete', 'evidence_scope')}
        for proof in proofs[:16]]
    return result


def default_source_specs():
    return (SourceSpec('manifest_json', r'D:\TWStockData\shared_market_data',
                       r'D:\TWStockData\shared_market_data\_metadata\stock_data_current.json',
                       source_id='shared_market_data'),)

def _route(value):
    if not isinstance(value, str) or not value:
        raise ValueError('missing route_id')
    return _DATASETS.get(value, value)


def _sha(raw):
    return hashlib.sha256(raw).hexdigest()


def _canonical(value):
    return json.dumps(value, sort_keys=True, separators=(',', ':'),
                      ensure_ascii=False, allow_nan=False).encode('utf-8')


def verify_descriptor(descriptor):
    """Verify the selected bytes of a pinned input, independent of new publications."""
    errors = []
    try:
        document = dict(descriptor_from_storage(descriptor))
        digest = document.pop('descriptor_sha256', None)
        if document.get('schema') != 'stock-data-published-inputs-v1':
            raise ValueError('unknown descriptor schema')
        if digest != _sha(_canonical(document)):
            raise ValueError('descriptor hash mismatch')
        for ref in document.get('artifacts', []):
            try:
                _verify_file(Path(ref['root']).resolve(), ref)
            except (OSError, ValueError, TypeError, KeyError) as exc:
                errors.append(f'{ref.get("route_id", "unknown")}: {type(exc).__name__}: {exc}')
    except (ValueError, TypeError, KeyError) as exc:
        errors.append(f'{type(exc).__name__}: {exc}')
    return dict(status='invalid' if errors else 'verified', errors=tuple(errors))


# Compact reference of a pinned snapshot descriptor for report outputs.  The snapshot descriptor carries the whole
# pinned catalog with every manifest entry's metadata (61 MB of canonical JSON for the 30 MB head of 2026-10-05), and a
# report used to copy it into its lineage blocks, its history snapshots and the latest-report pointer.  Those only
# need its identity: the descriptor's own self-hash ``descriptor_sha256`` (recomputed whenever a context is pinned or
# restored, PublishedSources.from_descriptor) and its snapshot fingerprint.  The content stays in the pinned session
# file and in the immutable published manifests.
SNAPSHOT_DESCRIPTOR_REF_SCHEMA = 'single-stock-snapshot-descriptor-ref-v1'


def _price_actual_max_date(pairs):
    """Latest declared ``actual_max_date`` (canonical YYYY-MM-DD) of pinned ``finmind_price_adj`` catalog pairs, on
    the entry or its nested ``metadata``; None when none declares a valid day.  Never raises (display only)."""
    days = []
    for pair in pairs or ():
        entry = pair[-1] if isinstance(pair, (list, tuple)) and pair else None
        if not isinstance(entry, Mapping):
            continue
        nested = entry.get('metadata')
        for container in (entry, nested if isinstance(nested, Mapping) else {}):
            try:
                day = cd.iso_day_or_none(container.get('actual_max_date'))
            except (TypeError, ValueError, OverflowError):
                day = None
            if day:
                days.append(day)
    return max(days) if days else None


def _descriptor_data_time(descriptor):
    """``{published_at, price_actual_max_date}`` of a (expanded or stored) snapshot descriptor: its own data-time
    keys when it has them, else derived from its ``sources`` identities and pinned price catalog (older
    descriptors).  Never raises."""
    if 'published_at' in descriptor or 'price_actual_max_date' in descriptor:
        return {'published_at': descriptor.get('published_at'),
                'price_actual_max_date': descriptor.get('price_actual_max_date')}
    sources = descriptor.get('sources') if isinstance(descriptor.get('sources'), list) else []
    published = next((item['published_at'] for item in sources
                      if isinstance(item, Mapping) and isinstance(item.get('published_at'), str)), None)
    days = []
    pinned = descriptor.get('pinned_sources') if isinstance(descriptor.get('pinned_sources'), list) else []
    for item in pinned:
        entries = item.get('entries') if isinstance(item, Mapping) else None
        if isinstance(entries, Mapping):
            day = _price_actual_max_date(entries.get('finmind_price_adj'))
            if day is not None:
                days.append(day)
    return {'published_at': published, 'price_actual_max_date': max(days) if days else None}


def is_snapshot_descriptor_reference(value):
    return isinstance(value, Mapping) and value.get('schema') == SNAPSHOT_DESCRIPTOR_REF_SCHEMA


def snapshot_descriptor_reference(descriptor):
    """Reference of one snapshot descriptor (a reference is returned unchanged).

    The reference names the descriptor by its self-hash when it has a well-formed one (``descriptor_sha256``), else by
    the canonical sha256 of its content (``content_sha256``), so two references are equal exactly when they name the
    same descriptor content.

    The reference also carries the data time of the named snapshot (plan 4.1, for the report lineage): the head's
    producer-declared ``published_at`` and the price route's ``price_actual_max_date``.  Both are read from the
    descriptor content (``_descriptor_data_time``), so equal content still gives an equal reference; neither is
    part of, nor changes the definition of, ``descriptor_sha256``."""
    if is_snapshot_descriptor_reference(descriptor):
        return dict(descriptor)
    if not isinstance(descriptor, Mapping):
        raise ValueError('snapshot_descriptor_invalid')
    ref = {'schema': SNAPSHOT_DESCRIPTOR_REF_SCHEMA, 'descriptor_schema': descriptor.get('schema'),
           'snapshot_fingerprint': descriptor.get('snapshot_fingerprint'),
           **_descriptor_data_time(descriptor)}
    digest = descriptor.get('descriptor_sha256')
    if isinstance(digest, str) and re.fullmatch('[0-9a-f]{64}', digest):
        ref['descriptor_sha256'] = digest
    else:
        ref['content_sha256'] = _sha(_canonical(descriptor_from_storage(descriptor)))
    return ref


# Storage form v2 of a snapshot descriptor (caps_dup D2).  A manifest_json source pins each manifest entry as the
# pair (ref, metadata) with ref *being* the metadata, so the expanded (v1) descriptor holds every entry twice: the
# 61 MB canonical descriptor of the 30.5 MB head of 2026-10-05.  The storage form keeps such a pair once, as the
# one-item list ``[metadata]``, and is marked by ``descriptor_storage``.  Nothing else changes: ``descriptor_sha256``
# is still the self-hash of the expanded v1 form (``descriptor_from_storage`` restores it exactly, both pair slots
# sharing one object), so every recorded descriptor hash, snapshot reference and lineage identity is unchanged,
# and readers accept both forms.  A pair is folded only when ref and metadata have identical canonical bytes.
DESCRIPTOR_STORAGE_V2 = 'stock-data-published-inputs-storage-v2'


def _map_pinned_entries(descriptor, convert):
    pinned = descriptor.get('pinned_sources')
    if not isinstance(pinned, list):
        return descriptor
    sources = []
    for item in pinned:
        if isinstance(item, Mapping) and isinstance(item.get('entries'), Mapping):
            item = dict(item, entries={route: convert(pairs) for route, pairs in item['entries'].items()})
        sources.append(item)
    return dict(descriptor, pinned_sources=sources)


def descriptor_storage(descriptor):
    """The v2 storage form of an expanded snapshot descriptor (a stored form is returned unchanged)."""
    if not isinstance(descriptor, Mapping):
        raise ValueError('snapshot_descriptor_invalid')
    if 'descriptor_storage' in descriptor:
        if descriptor['descriptor_storage'] != DESCRIPTOR_STORAGE_V2:
            raise ValueError('unknown descriptor storage')
        return descriptor

    def fold(pairs):
        if not isinstance(pairs, (list, tuple)):
            return pairs
        folded = []
        for pair in pairs:
            if (isinstance(pair, (list, tuple)) and len(pair) == 2
                    and (pair[0] is pair[1] or _canonical(pair[0]) == _canonical(pair[1]))):
                folded.append([pair[1]])
            else:
                folded.append(pair)
        return folded

    stored = _map_pinned_entries(descriptor, fold)
    return dict(stored, descriptor_storage=DESCRIPTOR_STORAGE_V2)


def descriptor_from_storage(descriptor):
    """The expanded (v1) snapshot descriptor of a stored v2 form; any other value is returned unchanged.

    The expansion shares one object between the two slots of a folded pair (no copy).  Integrity is checked by
    the descriptor's own self-hash on the expanded form, exactly as for a v1 descriptor.
    """
    if not isinstance(descriptor, Mapping) or 'descriptor_storage' not in descriptor:
        return descriptor
    if descriptor['descriptor_storage'] != DESCRIPTOR_STORAGE_V2:
        raise ValueError('unknown descriptor storage')

    def expand(pairs):
        if not isinstance(pairs, (list, tuple)):
            raise ValueError('invalid stored pinned route catalog')
        expanded = []
        for pair in pairs:
            if not isinstance(pair, (list, tuple)) or len(pair) not in (1, 2):
                raise ValueError('invalid stored pinned catalog pair')
            expanded.append([pair[0], pair[0]] if len(pair) == 1 else pair)
        return expanded

    expanded = _map_pinned_entries(descriptor, expand)
    return {key: value for key, value in expanded.items() if key != 'descriptor_storage'}


def _json(raw):
    def bad_constant(value):
        raise ValueError('nonstandard JSON number')
    def unique(pairs):
        result = {}
        for key, value in pairs:
            if key in result:
                raise ValueError('duplicate JSON key')
            result[key] = value
        return result
    return json.loads(raw.decode('utf-8'), parse_constant=bad_constant,
                      object_pairs_hook=unique)


def _path(root, value):
    if not isinstance(value, (str, Path)) or not str(value):
        raise ValueError('missing artifact path')
    result = (root / value).resolve()
    if not result.is_relative_to(root):
        raise ValueError('artifact path escapes source root')
    return result


def _read(root, ref, *, required=True):
    raw = _path(root, ref.get('ref', ref.get('path'))).read_bytes()
    digest, size = ref.get('sha256'), ref.get('size')
    if required and (digest is None or size is None):
        raise ValueError('artifact hash/size missing')
    if digest is not None and (not isinstance(digest, str)
            or not re.fullmatch('[0-9a-f]{64}', digest) or _sha(raw) != digest):
        raise ValueError('artifact hash mismatch')
    if size is not None and (type(size) is not int or size != len(raw)):
        raise ValueError('artifact size mismatch')
    return raw



_CHUNK_SIZE = 1024 * 1024


def _file_identity(stat):
    return (stat.st_dev, stat.st_ino, stat.st_size, stat.st_mtime_ns)


def _rehash_open_handle(handle, expected):
    """Integrity retry (plan 2.4): hash an open artifact handle once more from its first byte.

    ``expected`` is the pinned ``(sha256, size)``.  Returns the handle's file identity, with the handle rewound to
    its start, when it still holds exactly those bytes and did not change while hashed; otherwise None (the caller
    raises).  A producer that only touched, or atomically re-wrote, the same bytes therefore no longer stops the
    analysis, while different bytes still do."""
    if not isinstance(expected, tuple) or len(expected) != 2:
        return None
    digest, size = expected
    if (not isinstance(digest, str) or re.fullmatch('[0-9a-f]{64}', digest) is None
            or type(size) is not int or size < 0):
        return None
    handle.seek(0)
    before = _file_identity(os.fstat(handle.fileno()))
    hasher, count = hashlib.sha256(), 0
    while True:
        block = handle.read(_CHUNK_SIZE)
        if not block:
            break
        hasher.update(block)
        count += len(block)
    after = _file_identity(os.fstat(handle.fileno()))
    handle.seek(0)
    if before != after or count != size or hasher.hexdigest() != digest:
        return None
    return after


def _rehashed_identity(path, expected):
    """``_rehash_open_handle`` on the file now at ``path``; its identity only when the path still names that same
    file after hashing (else None: the caller raises)."""
    try:
        with Path(path).open('rb') as handle:
            identity = _rehash_open_handle(handle, expected)
        if identity is None or identity != _file_identity(Path(path).stat()):
            return None
    except OSError:
        return None
    return identity


def _verify_file(root, ref, *, required=True):
    path = _path(root, ref.get('ref', ref.get('path')))
    digest, size = ref.get('sha256'), ref.get('size')
    if required and (digest is None or size is None):
        raise ValueError('artifact hash/size missing')
    hasher, count = hashlib.sha256(), 0
    with path.open('rb') as handle:
        identity = _file_identity(os.fstat(handle.fileno()))
        while True:
            block = handle.read(_CHUNK_SIZE)
            if not block:
                break
            hasher.update(block)
            count += len(block)
        if identity != _file_identity(os.fstat(handle.fileno())):
            raise ValueError('artifact changed during read')
    if identity != _file_identity(path.stat()):
        raise ValueError('artifact replaced during read')
    actual = hasher.hexdigest()
    if digest is not None and (not isinstance(digest, str)
            or not re.fullmatch('[0-9a-f]{64}', digest) or actual != digest):
        raise ValueError('artifact hash mismatch')
    if size is not None and (type(size) is not int or size != count):
        raise ValueError('artifact size mismatch')
    return actual, count, identity


class _HashingReader:
    def __init__(self, handle):
        self.handle = handle
        self.hasher = hashlib.sha256()
        self.count = 0

    def read(self, size):
        block = self.handle.read(size)
        self.hasher.update(block)
        self.count += len(block)
        return block


class _JSONStream:
    """Strict incremental JSON reader, retaining at most one row/value and a chunk."""

    def __init__(self, handle):
        self.handle = handle
        self.buffer, self.pos, self.eof = '', 0, False
        self.utf8 = codecs.getincrementaldecoder('utf-8')('strict')
        def bad_constant(value):
            raise ValueError('nonstandard JSON number')
        def unique(pairs):
            result = {}
            for key, value in pairs:
                if key in result:
                    raise ValueError('duplicate JSON key')
                result[key] = value
            return result
        self.decoder = json.JSONDecoder(parse_constant=bad_constant, object_pairs_hook=unique)

    def fill(self):
        if self.eof:
            return False
        self.buffer = self.buffer[self.pos:]
        self.pos = 0
        block = self.handle.read(_CHUNK_SIZE)
        self.eof = not block
        self.buffer += self.utf8.decode(block, final=self.eof)
        return bool(block)

    def peek(self):
        while True:
            while self.pos < len(self.buffer) and self.buffer[self.pos] in ' \t\r\n':
                self.pos += 1
            if self.pos < len(self.buffer):
                return self.buffer[self.pos]
            if self.eof:
                return ''
            self.fill()

    def take(self, expected):
        if self.peek() != expected:
            raise ValueError('invalid or truncated JSON delimiter')
        self.pos += 1

    def value(self):
        self.peek()
        while True:
            try:
                value, end = self.decoder.raw_decode(self.buffer, self.pos)
            except json.JSONDecodeError:
                if self.eof:
                    raise ValueError('invalid or truncated JSON value') from None
                # Grow the pending text to at least twice its size before decoding again, so a value of n
                # bytes is decoded O(log n) times rather than once per chunk (memory stays within 2n + chunk).
                target = 2 * (len(self.buffer) - self.pos)
                while self.fill() and len(self.buffer) - self.pos < target:
                    pass
                continue
            # A numeric token may be split immediately after a valid prefix.
            if end == len(self.buffer) and not self.eof:
                self.fill()
                continue
            if end < len(self.buffer) and self.buffer[end] not in ' \t\r\n,]}:':
                if not self.eof:
                    self.fill()
                    continue
                raise ValueError('invalid JSON value suffix')
            self.pos = end
            return value

    def array(self):
        self.take('[')
        if self.peek() == ']':
            self.take(']')
            return
        while True:
            yield self.value()
            if self.peek() == ']':
                self.take(']')
                return
            self.take(',')

    def rows(self, selected, found, payload_metadata, row_callback=None):
        if self.peek() == '[':
            found.add('root')
            for row in self.array():
                if row_callback is not None:
                    row_callback('root', row)
                if selected == 'root':
                    yield row
        elif self.peek() == '{':
            self.take('{')
            keys = set()
            if self.peek() != '}':
                while True:
                    key = self.value()
                    if not isinstance(key, str) or key in keys:
                        raise ValueError('duplicate or invalid JSON key')
                    keys.add(key)
                    self.take(':')
                    if self.peek() == '[':
                        if key in ('data', 'records', 'rows'):
                            found.add(key)
                        for row in self.array():
                            if row_callback is not None and key in ('data', 'records', 'rows'):
                                row_callback(key, row)
                            if key == selected:
                                yield row
                    else:
                        value = self.value()
                        if key == 'fetched_at' and (value is None or isinstance(value, (str, int, float, bool))):
                            if not isinstance(value, str) or len(value) <= 4096:
                                payload_metadata[key] = value
                    if self.peek() == '}':
                        break
                    self.take(',')
            self.take('}')
        else:
            self.value()
        if self.peek():
            raise ValueError('trailing JSON content')


def _validate_rows_impl(root, ref, found, payload_metadata, *, required=True, row_callback=None):
    path = _path(root, ref.get('ref', ref.get('path')))
    digest, size = ref.get('sha256'), ref.get('size')
    if required and (digest is None or size is None):
        raise ValueError('artifact hash/size missing')
    with path.open('rb') as handle:
        identity = _file_identity(os.fstat(handle.fileno()))
        reader = _HashingReader(handle)
        # Exhaust strict parsing before exposing any rows or verified metadata.
        for _ in _JSONStream(reader).rows(None, found, payload_metadata, row_callback):
            pass
        if identity != _file_identity(os.fstat(handle.fileno())):
            raise ValueError('artifact changed during parsing')
    if identity != _file_identity(path.stat()):
        raise ValueError('artifact replaced during parsing')
    actual = reader.hasher.hexdigest()
    if digest is not None and (not isinstance(digest, str)
            or not re.fullmatch('[0-9a-f]{64}', digest) or actual != digest):
        raise ValueError('artifact hash mismatch')
    if size is not None and (type(size) is not int or size != reader.count):
        raise ValueError('artifact size mismatch')
    return actual, reader.count, identity


def _validate_rows(root, ref, found, payload_metadata, *, required=True, row_callback=None):
    try:
        return _validate_rows_impl(root, ref, found, payload_metadata,
                                   required=required, row_callback=row_callback)
    except PublishedArtifactValidationError:
        raise
    except (ValueError, TypeError, UnicodeError) as exc:
        raise PublishedArtifactValidationError(str(exc)) from exc


def _check_declared_record_count(route, state, entry, count):
    """A streamed-only manifest route must hold exactly its declared number of rows in the row array."""
    declared = entry.get('record_count') if isinstance(entry, Mapping) else None
    if (route in _STREAMED_ONLY_ROUTE_IDS and state['kind'] == 'manifest_json' and declared is not None
            and (type(declared) is not int or declared != count)):
        raise PublishedArtifactValidationError(
            f'{route} record count mismatch: declared {declared!r}, read {count}')


def _stream_rows(path, identity, selected, found, payload_metadata=None, expected=None):
    # ``expected`` = the pinned (sha256, size): an identity change is re-hashed once (_rehash_open_handle) before
    # it fails; bytes changed while rows were being yielded (a torn read) always fail.
    with path.open('rb') as handle:
        if identity != _file_identity(os.fstat(handle.fileno())):
            identity = _rehash_open_handle(handle, expected)
            if identity is None:
                raise ValueError('artifact changed before parsing')
        yield from _JSONStream(handle).rows(selected, found, payload_metadata if payload_metadata is not None else {})
        if identity != _file_identity(os.fstat(handle.fileno())):
            raise ValueError('artifact changed during parsing')
    if identity != _file_identity(path.stat()) and _rehashed_identity(path, expected) is None:
        raise ValueError('artifact replaced during parsing')


_ROW_CACHE_SCHEMA = 'published-single-stock-rows-v1'
_ROW_CACHE_CHUNK_MANIFEST_SCHEMA = 'published-single-stock-row-chunks-v1'
_ROW_CACHE_LIMIT = 32 * 1024 * 1024
# Rows held in memory by one consumer row-cache preparation (every stock of one artifact read), counted in
# canonical JSON bytes.  Above it each stock's pending rows are appended to that stock's private spool file
# (one write per stock per flush), so peak memory depends on neither the artifact size nor the number of
# stocks.  The cache bytes written do not depend on it.
_PREWARM_MEMORY_BUDGET = 64 * 1024 * 1024
# Routes whose payload is never materialized as one Python object.  Consumers read them only through the
# strict streaming readers (iter_stock_artifact_rows, iter_artifact_rows, rows, prewarm_stock_rows) or a
# sealed query projection.  There is no byte cap on them.
_STREAMED_ONLY_ROUTE_IDS = frozenset(('official_market_daily',))
_ROW_CACHE_FILTER_SEMANTICS = 'published-stock-row-filter-v2'
_ORDER21_ROW_CACHE_FILTER_SEMANTICS = 'published-stock-row-filter-v4-order21-minimal-summary'
_ARRAY_KEYS = ('root', 'data', 'records', 'rows')
_RETAINED_ROW_CACHE_ROUTE_IDS = frozenset((
    'finmind_trading_daily_report', 'finmind_government_bank',
    'tdcc_share_distribution_raw', 'official_market_daily',
))
_ORDER21_ROUTE_ID = 'finmind_trading_daily_report'
_ORDER21_REQUIRED_IDENTITY_FIELDS = ('stock_id', 'date', 'securities_trader_id', 'price')
ORDER21_SUMMARY_SCHEMA = 'twstock-updater.order21-daily-broker-summary.v1'
ORDER21_MINIMAL_SUMMARY_SCHEMA = 'twstock-updater.order21-daily-broker-summary.v2'
_ORDER21_SUMMARY_VOLUMES = (
    'buy', 'sell', 'price_level_count', 'source_row_count',
    'positive_net_volume', 'negative_net_volume',
)
_ORDER21_SUMMARY_AMOUNTS = (
    'buy_amount', 'sell_amount', 'positive_net_price_amount', 'negative_net_price_amount',
)
_ORDER21_SIDE_STATS = ('buy_price_weight', 'sell_price_weight', 'buy_price_amount', 'sell_price_amount')
_ORDER21_CANONICAL_AMOUNT = re.compile(r'(?:0|[1-9][0-9]*)(?:\.[0-9]*[1-9])?\Z')


def _order21_missing(value, *, dataframe=False):
    return value is None or (isinstance(value, str) and value == '') or (
        dataframe and isinstance(value, Real) and math.isnan(value))


def order21_summary_hint(row, *, dataframe=False):
    """A statistical marker, not merely the absence of a price column."""
    return row.get('row_schema') == ORDER21_MINIMAL_SUMMARY_SCHEMA or any(not _order21_missing(row.get(field), dataframe=dataframe)
               for field in _ORDER21_SUMMARY_VOLUMES[2:] + _ORDER21_SUMMARY_AMOUNTS[2:] + _ORDER21_SIDE_STATS)


def _order21_metadata_contexts(metadata):
    contexts = [metadata] if isinstance(metadata, Mapping) else []
    for context in tuple(contexts):
        contexts.extend(context[key] for key in ('metadata', 'payload_metadata')
                        if isinstance(context.get(key), Mapping))
    return contexts


def order21_requires_minimal_tag(metadata):
    """Use the row validator's object/envelope contexts, not price absence."""
    return any(context.get('order21_summary_schema') == ORDER21_MINIMAL_SUMMARY_SCHEMA
               and context.get('order21_data_granularity') != 'mixed_detail_summary_v2'
               for context in _order21_metadata_contexts(metadata))


def order21_row_format(row, *, dataframe=False, metadata=None):
    """Validate the version-one sufficient statistics without inventing detail.

    Raw JSON volumes must be integers.  DataFrame-only integral floats account
    for pandas coercion when separate stock-days use the two supported formats.
    """
    if not isinstance(row, Mapping):
        raise ValueError('order21_identity_missing')
    for field in _ORDER21_REQUIRED_IDENTITY_FIELDS[:3]:
        if _order21_missing(row.get(field), dataframe=dataframe):
            raise ValueError('order21_identity_missing:' + field)
    marker = row.get('row_schema')
    if not _order21_missing(marker, dataframe=dataframe) and marker not in (
            ORDER21_SUMMARY_SCHEMA, ORDER21_MINIMAL_SUMMARY_SCHEMA):
        raise ValueError('order21_summary_invalid:unsupported_row_schema')
    for context in _order21_metadata_contexts(metadata):
        summary_schema = context.get('order21_summary_schema')
        if summary_schema is not None and summary_schema not in (ORDER21_SUMMARY_SCHEMA, ORDER21_MINIMAL_SUMMARY_SCHEMA):
            raise ValueError('order21_summary_invalid:unsupported_object_schema')
    if order21_requires_minimal_tag(metadata) and marker != ORDER21_MINIMAL_SUMMARY_SCHEMA:
        raise ValueError('order21_summary_invalid:v2_row_schema_missing')
    if marker == ORDER21_MINIMAL_SUMMARY_SCHEMA:
        for field in _ORDER21_REQUIRED_IDENTITY_FIELDS[:3]:
            if not isinstance(row[field], str) or not row[field].strip():
                raise ValueError('order21_summary_invalid:' + field)
        if cd.iso_day_or_none(row['date']) is None:
            raise ValueError('order21_summary_invalid:date')
        forbidden = ('price', 'securities_trader', 'average_price', 'avg_price', 'buy_price',
                     'sell_price', 'net') + _ORDER21_SUMMARY_VOLUMES[2:] + _ORDER21_SUMMARY_AMOUNTS + _ORDER21_SIDE_STATS
        for field in forbidden:
            if not _order21_missing(row.get(field), dataframe=dataframe):
                raise ValueError('order21_summary_invalid:v2_has_legacy_field:' + field)
        for field in ('buy', 'sell'):
            value = row.get(field)
            integer = isinstance(value, Integral) and not isinstance(value, bool)
            coerced = (dataframe and isinstance(value, Real) and not isinstance(value, bool)
                       and math.isfinite(value) and float(value).is_integer() and abs(value) <= 2 ** 53)
            if not (integer or coerced) or value < 0:
                raise ValueError('order21_summary_invalid:' + field)
        return 'summary_v2'
    if not order21_summary_hint(row, dataframe=dataframe):
        if _order21_missing(row.get('price'), dataframe=dataframe):
            raise ValueError('order21_identity_missing:price')
        return 'detail'
    if not _order21_missing(row.get('price'), dataframe=dataframe):
        raise ValueError('order21_summary_invalid:summary_has_price')
    try:
        if cd.iso_day_or_none(row['date']) is None:
            raise ValueError('date')

        def volume(field):
            value = row.get(field)
            integer = isinstance(value, Integral) and not isinstance(value, bool)
            coerced = (dataframe and isinstance(value, Real) and not isinstance(value, bool)
                       and math.isfinite(value) and float(value).is_integer() and abs(value) <= 2 ** 53)
            if not (integer or coerced) or value < 0:
                raise ValueError(field)
            return int(value)

        def amount(field):
            value = row.get(field)
            if not isinstance(value, str) or not _ORDER21_CANONICAL_AMOUNT.fullmatch(value):
                raise ValueError(field)
            return Decimal(value)

        volumes = {field: volume(field) for field in _ORDER21_SUMMARY_VOLUMES}
        amounts = {field: amount(field) for field in _ORDER21_SUMMARY_AMOUNTS}
        if (volumes['price_level_count'] < 1 or volumes['source_row_count'] < volumes['price_level_count']
                or volumes['buy'] - volumes['sell'] != volumes['positive_net_volume'] - volumes['negative_net_volume']
                or volumes['positive_net_volume'] > volumes['buy']
                or volumes['negative_net_volume'] > volumes['sell']):
            raise ValueError('volume_or_count_consistency')
        with localcontext() as context:
            context.prec = max(28, sum(len(value) for value in (row[field] for field in _ORDER21_SUMMARY_AMOUNTS)) + 2)
            if (amounts['buy_amount'] - amounts['sell_amount']
                    != amounts['positive_net_price_amount'] - amounts['negative_net_price_amount']
                    or amounts['positive_net_price_amount'] > amounts['buy_amount']
                    or amounts['negative_net_price_amount'] > amounts['sell_amount']):
                raise ValueError('amount_consistency')
        for volume_field, amount_field in (('buy', 'buy_amount'), ('sell', 'sell_amount'),
                ('positive_net_volume', 'positive_net_price_amount'), ('negative_net_volume', 'negative_net_price_amount')):
            if volumes[volume_field] == 0 and amounts[amount_field] != 0:
                raise ValueError('zero_volume_amount')
        if any(not _order21_missing(row.get(field), dataframe=dataframe) for field in _ORDER21_SIDE_STATS):
            if not all(field in row for field in _ORDER21_SIDE_STATS):
                raise ValueError('side_statistics_incomplete')
            for side, net_field in (('buy', 'positive_net_volume'), ('sell', 'negative_net_volume')):
                weight = volume(side + '_price_weight')
                if weight > volumes[net_field]:
                    raise ValueError('side_weight')
                value = row.get(side + '_price_amount')
                if weight == 0:
                    if value is not None and not (dataframe and _order21_missing(value, dataframe=True)):
                        raise ValueError('zero_side_weight_amount')
                else:
                    amount(side + '_price_amount')
        return 'summary'
    except (KeyError, TypeError, ValueError) as error:
        raise ValueError('order21_summary_invalid:' + str(error)) from error


def _order21_invalid_identity(row):
    try:
        order21_row_format(row)
    except ValueError:
        return True
    return False


def _stock_cache_filter_semantics(stock_field='stock_id', date_field='date', *, route_id=None):
    return dict(schema=(_ORDER21_ROW_CACHE_FILTER_SEMANTICS if route_id == _ORDER21_ROUTE_ID
                        else _ROW_CACHE_FILTER_SEMANTICS),
                stock_field=stock_field, date_field=date_field)


def _row_selected_for_stock_cache(row, stock_id, route_id, stock_field='stock_id'):
    if stock_field is None:
        return True
    row_stock_id = row.get(stock_field, row.get('stock_id', row.get('data_id', '')))
    if route_id == 'official_market_daily' and stock_field == 'stock_id':
        row_stock_id = row.get('code') or row.get('stock_id') or row.get('Code') or row.get('SecuritiesCompanyCode') or ''
    if str(row_stock_id) == str(stock_id):
        return True
    return route_id == _ORDER21_ROUTE_ID and _order21_invalid_identity(row)


_SPOOL_LINE_PREFIX = {key: ('["' + key + '",').encode('ascii') for key in _ARRAY_KEYS}


class _RowCacheSpoolBudget:
    """Memory budget shared by every stock spool of one artifact read (canonical JSON bytes)."""

    def __init__(self, limit=None):
        self.limit = _PREWARM_MEMORY_BUDGET if limit is None else limit
        self.pending = 0
        self.flushes = 0
        self.spools = []

    def charge(self, byte_count):
        self.pending += byte_count
        if self.pending > self.limit:
            self.flush_all()

    def flush_all(self):
        for spool in self.spools:
            spool.flush()
        self.pending = 0
        self.flushes += 1


class _RowCacheSpool:
    """One stock's rows while an artifact is streamed: canonical lines in memory, a private file on demand.

    Each staged line is the canonical JSON of ``[array_key, row]``.  ``flush`` appends every pending line
    with one write (one file open) when the shared budget is exceeded, so memory stays bounded by the
    budget whatever the number of stocks or the artifact size.
    """

    def __init__(self, cache_dir, budget=None):
        self.cache_dir = Path(cache_dir)
        self.budget = budget if budget is not None else _RowCacheSpoolBudget()
        self.budget.spools.append(self)
        self.lines = []
        self.pending_bytes = 0
        self.sizes = {key: 0 for key in _ARRAY_KEYS}
        self.counts = {key: 0 for key in _ARRAY_KEYS}
        self.size = 0
        self.path = None

    @property
    def spilled(self):
        return self.path is not None

    def add(self, key, row):
        raw = _canonical(row)
        line = _SPOOL_LINE_PREFIX[key] + raw + b']\n'
        self.sizes[key] += len(raw) + 1
        self.counts[key] += 1
        self.size += len(raw) + 1
        self.lines.append(line)
        self.pending_bytes += len(line)
        self.budget.charge(len(line))

    def flush(self):
        if not self.lines:
            return
        data = b''.join(self.lines)
        if self.path is None:
            self.cache_dir.mkdir(parents=True, exist_ok=True)
            with tempfile.NamedTemporaryFile(mode='wb', dir=self.cache_dir,
                                             prefix='.published-rows-stage-', suffix='.tmp',
                                             delete=False) as handle:
                self.path = Path(handle.name)
                handle.write(data)
        else:
            with self.path.open('ab') as handle:
                handle.write(data)
        self._release()

    def _release(self):
        self.lines = []
        self.budget.pending -= self.pending_bytes
        self.pending_bytes = 0

    def document_bytes(self, empty_document_bytes, selected):
        """Exact size of the single cache document holding this spool's ``selected`` rows."""
        if not self.counts[selected]:
            return empty_document_bytes
        # n rows add their canonical bytes plus n - 1 separators inside the empty '[]'.
        return empty_document_bytes + self.sizes[selected] - 1

    @staticmethod
    def _records(lines, selected):
        for line in lines:
            value = _json(line)
            if (not isinstance(value, list) or len(value) != 2
                    or value[0] not in _ARRAY_KEYS):
                raise ValueError('consumer row cache staging record invalid')
            if value[0] == selected:
                yield value[1]

    def iter_records(self, selected):
        if self.path is None:
            yield from self._records(self.lines, selected)
            return
        self.flush()
        with self.path.open('rb') as handle:
            yield from self._records(handle, selected)

    def close(self):
        self._release()
        if self.path is not None:
            try:
                self.path.unlink(missing_ok=True)
            except OSError:
                pass


def _cached_rows(path, identity, records, expected=None):
    # Integrity retry: a changed identity is re-hashed once against the pinned (sha256, size) before it fails.
    if identity != _file_identity(path.stat()):
        identity = _rehashed_identity(path, expected)
        if identity is None:
            raise ValueError('artifact changed before parsing')
    yield from records
    if identity != _file_identity(path.stat()) and _rehashed_identity(path, expected) is None:
        raise ValueError('artifact changed during parsing')


# Routes whose completion proof (published_single_stock_projection._select) reads the all-stock date scope of an
# artifact.  A per-stock row cache records the all-stock scope while the whole payload is streamed, so a stock read
# from the cache proves exactly what a full read of the payload proves, and nothing more.
_GLOBAL_DATE_SCOPE_ROUTE_IDS = frozenset(('official_market_daily', 'finmind_government_bank'))
_GLOBAL_SCOPE_SCHEMA = 'published-global-row-scope-v1'
# Size of the date-string parse memo only (a speed-up for repeated date strings); the recorded date set is always
# complete, whatever the number of distinct dates.
_GLOBAL_SCOPE_PARSE_MEMO = 8192


class _GlobalRowScope:
    """All-stock rows, non-object rows and (date-scope routes) the complete set of distinct calendar dates (either
    spelling read, recorded canonical YYYY-MM-DD) of one artifact array.  The date set is never summarized: the window
    proof built from it is exact (C14)."""

    def __init__(self, route_id, date_field):
        from published_single_stock_projection import _date
        self.parse = _date
        self.date_field = date_field if route_id in _GLOBAL_DATE_SCOPE_ROUTE_IDS and date_field else None
        self.rows = self.non_object_rows = self.invalid_date_rows = 0
        self.dates = set()
        self.checked = {}                                   # date string -> strict ISO day or None

    def add(self, row):
        self.rows += 1
        if not isinstance(row, dict):
            self.non_object_rows += 1
        elif self.date_field is not None:
            value = row.get(self.date_field)
            if isinstance(value, str) and len(self.checked) <= _GLOBAL_SCOPE_PARSE_MEMO:
                day = self.checked.get(value, self.checked)
                if day is self.checked:
                    day = self.checked[value] = self.parse(value)
            else:
                day = self.parse(value)
            if day is None:
                self.invalid_date_rows += 1
            else:
                self.dates.add(day)

    def document(self):
        return dict(schema=_GLOBAL_SCOPE_SCHEMA, rows=self.rows, non_object_rows=self.non_object_rows,
                    date_field=self.date_field, invalid_date_rows=self.invalid_date_rows,
                    dates=None if self.date_field is None else sorted(self.dates))


def _quality_projection_fields(quality):
    """Artifact metadata a per-stock cache read exposes: quality counts and, when recorded, the all-stock scope."""
    fields = {'_projection_missing_date_rows': quality['missing_date_rows'],
              '_projection_missing_stock_rows': quality['missing_stock_rows']}
    if isinstance(quality.get('global_scope'), dict):
        fields['_projection_global_scope'] = quality['global_scope']
    return fields


class _RowCollector:
    def __init__(self, stock_id, date_only, route_id, stock_field='stock_id', date_field='date',
                 cache_dir=None):
        self.stock_id, self.date_only, self.route_id = stock_id, date_only, route_id
        self.stock_field, self.date_field = stock_field, date_field
        self.cache_dir = Path(cache_dir) if cache_dir is not None else Path(tempfile.gettempdir())
        self.spool = None
        self.records = {key: [] for key in _ARRAY_KEYS}
        self.dates = {key: None for key in _ARRAY_KEYS}
        self.quality = {key: dict(missing_date_rows=0, missing_stock_rows=0) for key in _ARRAY_KEYS}
        self.scopes = ({} if date_only else
                       {key: _GlobalRowScope(route_id, date_field) for key in _ARRAY_KEYS})
        self.size, self.overflow = 0, False

    @property
    def spilled(self):
        return self.spool is not None

    def finish(self):
        """Record each array's all-stock scope in its quality metadata once the payload is exhausted."""
        for key, scope in self.scopes.items():
            self.quality[key]['global_scope'] = scope.document()

    def __call__(self, key, row):
        if self.date_only:
            from published_single_stock_projection import _date
            day = _date(row.get('date')) if isinstance(row, dict) else None
            if day and (self.dates[key] is None or day > self.dates[key]):
                self.dates[key] = day
        else:
            self.scopes[key].add(row)
            if isinstance(row, dict):
                from stock_query_projection import _normalise_day, _normalise_stock
                quality = self.quality[key]
                if self.date_field and _normalise_day(row.get(self.date_field)) is None:
                    quality['missing_date_rows'] += 1
                stock_value = (row.get(self.stock_field, row.get('stock_id', row.get('data_id', '')))
                               if self.stock_field else None)
                if self.stock_field and _normalise_stock(stock_value) is None:
                    quality['missing_stock_rows'] += 1
            if (not isinstance(row, dict) or
                    _row_selected_for_stock_cache(row, self.stock_id, self.route_id, self.stock_field)):
                self.size += len(_canonical(row)) + 1
                if not self.overflow and self.size > _ROW_CACHE_LIMIT:
                    self.overflow = True
                    self.spool = _RowCacheSpool(self.cache_dir)
                    for array_key in _ARRAY_KEYS:
                        for prior in self.records[array_key]:
                            self.spool.add(array_key, prior)
                        self.records[array_key].clear()
                if self.overflow:
                    self.spool.add(key, row)
                else:
                    self.records[key].append(row)


def _load_row_cache(path, identity, io_metrics=None):
    try:
        with path.open('rb') as handle:
            before = _file_identity(os.fstat(handle.fileno()))
            raw = handle.read(_ROW_CACHE_LIMIT + 1)
            after = _file_identity(os.fstat(handle.fileno()))
        if io_metrics is not None:
            io_metrics['row_cache_bytes_read'] = io_metrics.get('row_cache_bytes_read', 0) + len(raw)
        if (len(raw) > _ROW_CACHE_LIMIT or before != after
                or before != _file_identity(path.stat())):
            return None
        document = _decode_cache_document(raw, identity)
        if document is None:
            return None
        if document.get('schema') == _ROW_CACHE_CHUNK_MANIFEST_SCHEMA:
            return _load_chunked_row_cache(path, document, identity, io_metrics, before)
        return document
    except (OSError, ValueError, TypeError, KeyError, AttributeError):
        return None


def _decode_cache_document(raw, identity):
    document = _json(raw)
    if not isinstance(document, dict):
        return None
    checksum = document.pop('canonical_sha256', None)
    if checksum != _sha(_canonical(document)) or document.get('identity') != identity:
        return None
    if document.get('schema') == _ROW_CACHE_CHUNK_MANIFEST_SCHEMA:
        return document if identity.get('mode') == 'stock' else None
    if (document.get('schema') != _ROW_CACHE_SCHEMA
            or document.get('selected_array') not in _ARRAY_KEYS
            or not isinstance(document.get('payload_metadata'), dict)
            or not isinstance(document.get('records'), list)):
        return None
    if identity.get('mode') == 'stock':
        quality = document.get('quality_metadata')
        if (not isinstance(quality, dict)
                or any(type(quality.get(name)) is not int or quality[name] < 0
                       for name in ('missing_date_rows', 'missing_stock_rows'))):
            return None
    if identity.get('mode') == 'date':
        from published_single_stock_projection import _date
        dates = document.get('dates')
        if (not isinstance(dates, dict) or set(dates) != set(_ARRAY_KEYS)
                or any(value is not None and _date(value) is None for value in dates.values())
                or document['records']):
            return None
    elif identity.get('mode') == 'stock':
        semantics = identity.get('filter_semantics')
        stock_field = (semantics.get('stock_field', 'stock_id')
                       if isinstance(semantics, dict)
                       and semantics.get('schema') == _stock_cache_filter_semantics(route_id=identity['route'])['schema']
                       else 'stock_id')
        if any(isinstance(row, dict) and not _row_selected_for_stock_cache(
                row, identity['stock_id'], identity['route'], stock_field)
                for row in document['records']):
            return None
    return document


def _valid_row_cache_quality(quality):
    return (isinstance(quality, dict)
            and all(type(quality.get(name)) is int and quality[name] >= 0
                    for name in ('missing_date_rows', 'missing_stock_rows')))


def _read_chunk_cache_file(manifest_path, chunk, identity, manifest, io_metrics=None):
    digest, byte_count, row_count = chunk.get('sha256'), chunk.get('bytes'), chunk.get('rows')
    if (not isinstance(digest, str) or not re.fullmatch('[0-9a-f]{64}', digest)
            or type(byte_count) is not int or byte_count < 0 or byte_count > _ROW_CACHE_LIMIT
            or type(row_count) is not int or row_count <= 0):
        return None, None
    name = chunk.get('path')
    expected_name = f'{manifest_path.stem}.{digest}.chunk.json'
    if not isinstance(name, str) or name != expected_name or Path(name).name != name:
        return None, None
    root = manifest_path.parent.resolve()
    path = root / name
    try:
        resolved = path.resolve(strict=True)
        if resolved != path or resolved.parent != root:
            return None, None
        with resolved.open('rb') as handle:
            before = _file_identity(os.fstat(handle.fileno()))
            raw = handle.read(_ROW_CACHE_LIMIT + 1)
            after = _file_identity(os.fstat(handle.fileno()))
        if io_metrics is not None:
            io_metrics['row_cache_bytes_read'] = io_metrics.get('row_cache_bytes_read', 0) + len(raw)
        if (len(raw) != byte_count or len(raw) > _ROW_CACHE_LIMIT
                or before != after or before != _file_identity(resolved.stat())
                or _sha(raw) != digest):
            return None, None
        document = _decode_cache_document(raw, identity)
        if (document is None or document.get('schema') != _ROW_CACHE_SCHEMA
                or document.get('selected_array') != manifest.get('selected_array')
                or document.get('payload_metadata') != manifest.get('payload_metadata')
                or document.get('quality_metadata') != manifest.get('quality_metadata')
                or len(document['records']) != row_count):
            return None, None
        return document, _file_identity(resolved.stat())
    except (OSError, ValueError, TypeError, KeyError, AttributeError):
        return None, None


class _ChunkedCachedRows:
    def __init__(self, manifest_path, identity, manifest, chunks, manifest_identity, io_metrics):
        self.manifest_path = manifest_path
        self.identity = identity
        self.manifest = manifest
        self.chunks = chunks
        self.manifest_identity = manifest_identity
        self.io_metrics = io_metrics

    def __len__(self):
        return self.manifest['record_count']

    def __iter__(self):
        if _file_identity(self.manifest_path.stat()) != self.manifest_identity:
            raise ValueError('consumer row cache manifest changed after validation')
        for chunk, chunk_identity in self.chunks:
            path = self.manifest_path.parent.resolve() / chunk['path']
            if _file_identity(path.stat()) != chunk_identity:
                raise ValueError('consumer row cache chunk changed after validation')
            document, current_identity = _read_chunk_cache_file(
                self.manifest_path, chunk, self.identity, self.manifest, self.io_metrics)
            if document is None or current_identity != chunk_identity:
                raise ValueError('consumer row cache chunk changed after validation')
            yield from document['records']


def _load_chunked_row_cache(path, document, identity, io_metrics, manifest_identity):
    if (document.get('identity') != identity
            or document.get('selected_array') not in _ARRAY_KEYS
            or not isinstance(document.get('payload_metadata'), dict)
            or not _valid_row_cache_quality(document.get('quality_metadata'))
            or type(document.get('record_count')) is not int or document['record_count'] < 0
            or not isinstance(document.get('chunks'), list)):
        return None
    expected_dates = {key: None for key in _ARRAY_KEYS}
    if document.get('dates') != expected_dates:
        return None
    chunks, total_rows = [], 0
    for chunk in document['chunks']:
        if not isinstance(chunk, dict) or set(chunk) != {'path', 'sha256', 'bytes', 'rows'}:
            return None
        chunk_document, chunk_identity = _read_chunk_cache_file(
            path, chunk, identity, document, io_metrics)
        if chunk_document is None:
            return None
        chunks.append((chunk, chunk_identity))
        total_rows += chunk['rows']
    if total_rows != document['record_count'] or bool(total_rows) != bool(chunks):
        return None
    return dict(schema=_ROW_CACHE_SCHEMA, identity=identity,
        selected_array=document['selected_array'], payload_metadata=document['payload_metadata'],
        quality_metadata=document['quality_metadata'], dates=expected_dates,
        record_count=total_rows,
        records=_ChunkedCachedRows(path, identity, document, chunks, manifest_identity, io_metrics))


def _cache_document_bytes(document):
    return _canonical(dict(document, canonical_sha256=_sha(_canonical(document))))


# Waits (seconds) between attempts to replace a consumer row cache that another reader holds open.
_CACHE_REPLACE_RETRY_SECONDS = (0.05, 0.1, 0.2, 0.4, 0.8)


def _replace_cache_file(temporary, path, raw):
    """``os.replace`` that tolerates a reader holding the target open.

    On Windows the replace fails with PermissionError while another handle on the target is open (for example a
    report reading the same stock cache during a batch preparation).  Cache bytes are a pure function of the
    verified payload, so a target that already holds exactly ``raw`` is the same cache and is kept; otherwise the
    replace is retried briefly and then reported as a failed write.
    """
    for delay in _CACHE_REPLACE_RETRY_SECONDS + (None,):
        try:
            os.replace(temporary, path)
            return True
        except PermissionError:
            try:
                if path.is_file() and path.stat().st_size == len(raw) and path.read_bytes() == raw:
                    return True
            except OSError:
                pass
            if delay is None:
                return False
            time.sleep(delay)
    return False


def _write_cache_bytes(path, raw):
    temporary = None
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        with tempfile.NamedTemporaryFile(mode='wb', dir=path.parent,
                                         prefix=path.name + '.', suffix='.tmp', delete=False) as handle:
            temporary = Path(handle.name)
            handle.write(raw)
        return _replace_cache_file(temporary, path, raw)
    except (OSError, ValueError, TypeError):
        return False
    finally:
        if temporary is not None:
            try:
                temporary.unlink(missing_ok=True)
            except OSError:
                pass


def _write_row_cache(path, document):
    try:
        raw = _cache_document_bytes(document)
        if len(raw) > _ROW_CACHE_LIMIT:
            return False
        return _write_cache_bytes(path, raw)
    except (OSError, ValueError, TypeError):
        return False


def _row_cache_document_exceeds_limit(document):
    return len(_cache_document_bytes(document)) > _ROW_CACHE_LIMIT


def _write_chunked_row_cache(path, identity, selected, payload_metadata, quality, records):
    empty_chunk = dict(schema=_ROW_CACHE_SCHEMA, identity=identity,
        selected_array=selected, payload_metadata=payload_metadata, records=[],
        dates={key: None for key in _ARRAY_KEYS}, quality_metadata=quality)
    empty_bytes = _cache_document_bytes(empty_chunk)
    if len(empty_bytes) > _ROW_CACHE_LIMIT:
        return False
    chunk_rows, chunk_payload_bytes, chunks = [], 0, []

    def flush_chunk():
        nonlocal chunk_rows, chunk_payload_bytes
        if not chunk_rows:
            return True
        document = dict(empty_chunk, records=chunk_rows)
        raw = _cache_document_bytes(document)
        if len(raw) > _ROW_CACHE_LIMIT:
            return False
        digest = _sha(raw)
        chunk_path = path.with_name(f'{path.stem}.{digest}.chunk.json')
        if not _write_cache_bytes(chunk_path, raw):
            return False
        chunks.append(dict(path=chunk_path.name, sha256=digest,
                           bytes=len(raw), rows=len(chunk_rows)))
        chunk_rows, chunk_payload_bytes = [], 0
        return True

    record_count = 0
    for row in records:
        row_bytes = len(_canonical(row))
        separator_bytes = 1 if chunk_rows else 0
        if len(empty_bytes) + chunk_payload_bytes + separator_bytes + row_bytes > _ROW_CACHE_LIMIT:
            if not flush_chunk():
                return False
            separator_bytes = 0
        if len(empty_bytes) + row_bytes > _ROW_CACHE_LIMIT:
            return False
        chunk_rows.append(row)
        chunk_payload_bytes += separator_bytes + row_bytes
        record_count += 1
    if not flush_chunk():
        return False
    manifest = dict(schema=_ROW_CACHE_CHUNK_MANIFEST_SCHEMA, identity=identity,
        selected_array=selected, payload_metadata=payload_metadata,
        quality_metadata=quality, dates={key: None for key in _ARRAY_KEYS},
        record_count=record_count, chunks=chunks)
    raw = _cache_document_bytes(manifest)
    if len(raw) > _ROW_CACHE_LIMIT:
        return False
    return _write_cache_bytes(path, raw)


def _write_spooled_row_cache(path, identity, selected, payload_metadata, quality, spool):
    """Write one stock's consumer cache from its spool.

    The format depends only on the stock's rows: one document when it fits ``_ROW_CACHE_LIMIT``, chunks
    otherwise, whether or not the shared memory budget moved some rows to the spool file.  The cache bytes
    are therefore the same with any budget.
    """
    empty = dict(schema=_ROW_CACHE_SCHEMA, identity=identity, selected_array=selected,
                 payload_metadata=payload_metadata, records=[],
                 dates={key: None for key in _ARRAY_KEYS}, quality_metadata=quality)
    if spool.document_bytes(len(_cache_document_bytes(empty)), selected) > _ROW_CACHE_LIMIT:
        return _write_chunked_row_cache(path, identity, selected, payload_metadata, quality,
                                        spool.iter_records(selected))
    return _write_row_cache(path, dict(empty, records=list(spool.iter_records(selected))))


def _manifest_dataset_refs(source_id, entry):
    """Derive payload refs from a published ``datasets`` entry for pin and catalog validation."""
    if source_id == 'shared_market_data':
        if (not isinstance(entry.get('sha256'), str)
                or not re.fullmatch('[0-9a-f]{64}', entry['sha256'])
                or type(entry.get('size')) is not int or entry['size'] < 0):
            raise ValueError('shared_market_data requires payload sha256 and size')
    return [entry]


_PIN_CONTAINER = object()
_PIN_DATASETS_STREAMED = object()


def _stream_pin_object(stream):
    """Yield each key of the JSON object at the stream position (its value is left for the caller); keys unique."""
    stream.take('{')
    if stream.peek() == '}':
        stream.take('}')
        return
    keys = set()
    while True:
        if stream.peek() != '"':
            raise ValueError('invalid JSON object key')
        key = stream.value()
        if not isinstance(key, str) or key in keys:
            raise ValueError('duplicate or invalid JSON key')
        keys.add(key)
        stream.take(':')
        yield key
        if stream.peek() == '}':
            stream.take('}')
            return
        stream.take(',')


def _stream_pin_value(stream):
    """Strictly walk one JSON value, materializing only scalars (memory: the largest scalar plus a chunk)."""
    token = stream.peek()
    if token == '{':
        for _key in _stream_pin_object(stream):
            _stream_pin_value(stream)
        return _PIN_CONTAINER
    if token == '[':
        stream.take('[')
        if stream.peek() == ']':
            stream.take(']')
            return _PIN_CONTAINER
        while True:
            _stream_pin_value(stream)
            if stream.peek() == ']':
                stream.take(']')
                return _PIN_CONTAINER
            stream.take(',')
    if token == '':
        raise ValueError('invalid or truncated JSON value')
    return stream.value()


def _stream_pin_document(stream, on_dataset=None):
    """Strict one-pass walk of a pin file; returns its top-level scalar members.

    With ``on_dataset``, each item of a top-level ``datasets`` array is materialized alone and handed to it (the
    manifest binding check), so memory is bounded by the largest dataset entry, never by the file.
    """
    top = {}
    if stream.peek() == '{':
        for key in _stream_pin_object(stream):
            if key == 'datasets' and on_dataset is not None and stream.peek() == '[':
                for item in stream.array():
                    on_dataset(item)
                top[key] = _PIN_DATASETS_STREAMED
            else:
                top[key] = _stream_pin_value(stream)
    else:
        _stream_pin_value(stream)
    if stream.peek():
        raise ValueError('trailing JSON content')
    return top


class _ManifestPinBinding:
    """Binds a restored published catalog to the bytes of its pinned manifest.

    Every ``datasets`` entry of the hashed manifest is derived into catalog pairs exactly as ``_pin`` derives them
    and must equal, in order and by canonical bytes, the pair the descriptor restored; nothing may be left over
    and the derived route errors and head identity must be the restored ones.  A self-consistent descriptor whose
    catalog was edited and resealed therefore fails even though its manifest hash is right.  The first mismatch is
    recorded (not raised) so a changed or corrupt file still reports its hash mismatch first.
    """

    def __init__(self, state, pinned_errors):
        self.state, self.pinned_errors = state, pinned_errors
        self.cursor, self.errors, self.mismatch = {}, {}, None

    def fail(self, reason):
        if self.mismatch is None:
            self.mismatch = reason

    def add(self, entry):
        if self.mismatch is not None:
            return
        if not isinstance(entry, dict):
            return self.fail('dataset_entry_invalid')
        try:
            route = _route(entry.get('route_id') or entry.get('dataset_id') or entry.get('dataset'))
            refs = _manifest_dataset_refs(self.state['source_id'], entry)
        except (OSError, ValueError, TypeError, KeyError, AttributeError) as exc:
            return self.fail(f'dataset_entry_invalid:{type(exc).__name__}')
        pinned = self.state['entries'].get(route, [])
        entry_bytes = None
        for ref in refs:
            index = self.cursor.get(route, 0)
            if index >= len(pinned):
                return self.fail(f'unpinned_entry:{route}')
            pinned_ref, pinned_entry = pinned[index]
            entry_bytes = _canonical(entry) if entry_bytes is None else entry_bytes
            if _canonical(pinned_entry) != entry_bytes:
                return self.fail(f'entry_changed:{route}')
            if pinned_ref is not pinned_entry and _canonical(pinned_ref) != entry_bytes:
                return self.fail(f'ref_changed:{route}')
            self.cursor[route] = index + 1

    def finish(self, top):
        if self.mismatch is None:
            if top.get('datasets') is not _PIN_DATASETS_STREAMED:
                self.fail('datasets_missing')
            elif any(self.cursor.get(route, 0) != len(pairs) for route, pairs in self.state['entries'].items()):
                self.fail('pinned_entry_not_in_manifest')
            elif self.errors != self.pinned_errors:
                self.fail('route_errors_changed')
            elif self.state['kind'] == 'manifest_json':
                # the catalog records a v2 head's schema (v2 constructs are read only then); absent = v1
                if top.get('schema') != self.state.get('identity', {}).get('publication_schema',
                                                                         PUBLICATION_SCHEMA_V1):
                    self.fail('schema_changed')
                identity = self.state.get('identity', {})
                for name, key in (('producer_id', 'producer_id'), ('publication_run_id', 'run_id'),
                                  ('published_at', 'published_at')):
                    if identity.get(name) is None:
                        # Not recorded by the descriptor (sessions written before the head identity was recorded,
                        # e.g. 2026-09-18): nothing to compare; the pin sha256 and the catalog binding above still
                        # bind every byte of the head (cd_final F1).
                        continue
                    value = top.get(key)
                    expected = value.strip() if isinstance(value, str) and value.strip() else None
                    if identity.get(name) != expected:
                        self.fail(f'identity_changed:{name}')
        if self.mismatch is not None:
            raise ValueError('published_context_pin_binding_mismatch:' + self.mismatch)


def _verify_pin_file(path, expected, binding=None, store=None):
    """Hash, strictly parse and (optionally) bind one pin file in a single bounded-memory pass (no byte cap).

    Returns the bytes read.  Checks, in order: unchanged file identity and full length, SHA-256, strict JSON, then
    the binding.  Memory is one read block plus the largest JSON scalar or bound dataset entry.  A deduplicated
    archive ``<sha>.splice.json`` is verified on its streamed reconstruction (``_verify_pin_splice``).
    """
    if is_publication_splice_path(path):
        return _verify_pin_splice(path, expected, binding, store)
    parse_error = None
    with path.open('rb') as handle:
        before = _file_identity(os.fstat(handle.fileno()))
        reader = _HashingReader(handle)
        top = {}
        try:
            top = _stream_pin_document(_JSONStream(reader), binding.add if binding is not None else None)
        except (ValueError, TypeError, UnicodeError, RecursionError) as exc:
            parse_error = exc
        while reader.read(_CHUNK_SIZE):          # hash whatever a parse error left unread
            pass
        after = _file_identity(os.fstat(handle.fileno()))
    count = reader.count
    if before != after or before != _file_identity(path.stat()) or count != before[2]:
        raise ValueError('published_context_pin_changed_during_read')
    if reader.hasher.hexdigest() != expected:
        raise ValueError('published_context_pin_hash_mismatch')
    if parse_error is not None:
        raise ValueError(f'published_context_pin_json_invalid:{parse_error}') from parse_error
    if binding is not None:
        binding.finish(top)
    return count


# Legacy (v1) head archives may be stored deduplicated (spec section 3 item 6):
# ``<store>/_manifests/<sha>.splice.json`` = {"$archive": "stock-data-archive-splice-v1", "sha256": <archive sha>,
#    "size": <archive bytes>, "segments": [{"t": <text>} | {"b": <blob sha256>, "n": <bytes>}, ...]}, the blobs at
# ``<store>/_evidence/blob/<sha>.bin``.  The archive bytes are the concatenation of the segments (text as UTF-8, a
# blob's raw bytes).  Readers look for ``<sha>.json`` first, then ``<sha>.splice.json``; both forms coexist during
# a migration.  Nothing here writes or deletes an archive, a blob or an evidence object (append-only stores; a
# retention policy would need a mark/sweep over every head, kept archive and resume pin, DEPLOY_PLAN section 12).
PUBLICATION_ARCHIVE_DIRNAME = '_manifests'
PUBLICATION_ARCHIVE_SPLICE_SCHEMA = 'stock-data-archive-splice-v1'
PUBLICATION_ARCHIVE_SPLICE_SUFFIX = '.splice.json'
EVIDENCE_BLOB_DIRNAME = 'blob'
_SPLICE_KEYS = frozenset(('$archive', 'sha256', 'size', 'segments'))


def _publication_store(state):
    """The published store of a pinned source: the directory holding its ``_evidence`` objects and ``_manifests``
    archives, i.e. the source root (the updater writes both next to ``_metadata``)."""
    return Path(state['root'])


def is_publication_splice_path(path):
    return Path(path).name.endswith(PUBLICATION_ARCHIVE_SPLICE_SUFFIX)


def publication_archive_path(store, digest):
    """The archive of head ``digest`` in ``<store>/_manifests``: ``<sha>.json`` (the head bytes) when it exists,
    else the deduplicated ``<sha>.splice.json``; None when neither exists."""
    if not isinstance(digest, str) or re.fullmatch('[0-9a-f]{64}', digest) is None:
        raise ValueError('publication_archive_digest_invalid')
    directory = Path(store) / PUBLICATION_ARCHIVE_DIRNAME
    for name in (digest + '.json', digest + PUBLICATION_ARCHIVE_SPLICE_SUFFIX):
        path = directory / name
        try:
            path.lstat()
        except FileNotFoundError:
            continue
        return path
    return None


def _content_addressed_pin(path):
    """True for a pin file named by its content (``<sha>.json`` / ``<sha>.splice.json``) or kept in an archive
    directory ``_manifests``: its bytes can never legitimately change, so a mismatch there is tamper (raised),
    while any other pin path (e.g. ``_metadata/stock_data_current.json``) is a mutable head (plan 2.4)."""
    path = Path(path)
    return (path.parent.name == PUBLICATION_ARCHIVE_DIRNAME
            or re.fullmatch('[0-9a-f]{64}(?:[.]splice)?[.]json', path.name) is not None)


def _migrated_archive(path, store):
    """``<store>/_manifests/<sha>.splice.json`` for a missing ``<store>/_manifests/<sha>.json`` (else None)."""
    path = Path(path)
    if (store is None or path.parent != Path(store) / PUBLICATION_ARCHIVE_DIRNAME
            or re.fullmatch('[0-9a-f]{64}[.]json', path.name) is None):
        return None
    splice = path.parent / (path.name[:-len('.json')] + PUBLICATION_ARCHIVE_SPLICE_SUFFIX)
    return splice if splice.is_file() else None


class _SpliceArchiveReader:
    """File-like streaming reconstruction of a ``<sha>.splice.json`` archive (``read(n)``).

    The splice document (text segments and blob names only) is read once and strictly parsed; ``read`` then yields
    the archive bytes in order, a blob streamed in blocks.  Each blob's sha256 and length are verified when it ends,
    and the stream ends only after the total length and sha256 equal the document's ``size`` and ``sha256`` and the
    splice file was unchanged; any mismatch raises ValueError (fail closed).  ``verified`` is True only then.
    Memory: the splice document (read and strictly parsed once, so briefly about three times its size) plus about
    one block: a blob is streamed in ``_CHUNK_SIZE`` blocks and a text segment is encoded and emitted in slices of
    ``_CHUNK_SIZE`` characters (at most four times that in bytes), whatever the segment sizes.
    """

    def __init__(self, store, path):
        self.path = Path(path)
        with self.path.open('rb') as handle:
            self.identity = _file_identity(os.fstat(handle.fileno()))
            raw = handle.read()
            if _file_identity(os.fstat(handle.fileno())) != self.identity or len(raw) != self.identity[2]:
                raise ValueError('publication_splice_changed_during_read')
        self.physical_sha256 = _sha(raw)
        document = _json(raw)
        if (not isinstance(document, dict) or set(document) != _SPLICE_KEYS
                or document['$archive'] != PUBLICATION_ARCHIVE_SPLICE_SCHEMA
                or not isinstance(document['sha256'], str)
                or re.fullmatch('[0-9a-f]{64}', document['sha256']) is None
                or type(document['size']) is not int or document['size'] < 0
                or not isinstance(document['segments'], list)):
            raise ValueError('publication_splice_invalid')
        for segment in document['segments']:
            if not isinstance(segment, dict) or not (
                    (set(segment) == {'t'} and isinstance(segment['t'], str))
                    or (set(segment) == {'b', 'n'} and isinstance(segment['b'], str)
                        and re.fullmatch('[0-9a-f]{64}', segment['b']) is not None
                        and type(segment['n']) is int and segment['n'] >= 0)):
                raise ValueError('publication_splice_segment_invalid')
        if self.path.name != document['sha256'] + PUBLICATION_ARCHIVE_SPLICE_SUFFIX:
            raise ValueError('publication_splice_name_mismatch')
        self.sha256, self.size, self.segments = document['sha256'], document['size'], document['segments']
        self.blob_directory = Path(store) / EVIDENCE_DIRNAME / EVIDENCE_BLOB_DIRNAME
        self.verified = False
        self.count = 0
        self._hasher = hashlib.sha256()
        self._chunks = self._generate()
        self._buffer = b''

    def _emit(self, data):
        self.count += len(data)
        if self.count > self.size:
            raise ValueError('publication_splice_reconstruction_exceeds_size')
        self._hasher.update(data)
        return data

    def _generate(self):
        for segment in self.segments:
            if 't' in segment:
                text = segment['t']
                for start in range(0, len(text), _CHUNK_SIZE):      # a large text segment: one slice at a time
                    yield self._emit(text[start:start + _CHUNK_SIZE].encode('utf-8'))
                continue
            digest, length = segment['b'], segment['n']
            path = self.blob_directory / (digest + '.bin')
            hasher, count = hashlib.sha256(), 0
            try:
                handle = path.open('rb')
            except FileNotFoundError:
                raise ValueError(f'publication_splice_blob_missing:{digest}') from None
            with handle:
                before = _file_identity(os.fstat(handle.fileno()))
                while True:
                    block = handle.read(_CHUNK_SIZE)
                    if not block:
                        break
                    count += len(block)
                    if count > length:
                        raise ValueError(f'publication_splice_blob_mismatch:{digest}')
                    hasher.update(block)
                    yield self._emit(block)
                after = _file_identity(os.fstat(handle.fileno()))
            if before != after or count != length or hasher.hexdigest() != digest:
                raise ValueError(f'publication_splice_blob_mismatch:{digest}')
        if self.count != self.size or self._hasher.hexdigest() != self.sha256:
            raise ValueError('publication_splice_reconstruction_mismatch')
        if _file_identity(self.path.stat()) != self.identity:
            raise ValueError('publication_splice_changed_during_read')
        self.verified = True

    def read(self, size=-1):
        if size is None or size < 0:
            data = self._buffer + b''.join(self._chunks)
            self._buffer = b''
            return data
        while len(self._buffer) < size:
            chunk = next(self._chunks, None)
            if chunk is None:
                break
            self._buffer += chunk
        data, self._buffer = self._buffer[:size], self._buffer[size:]
        return data


def read_publication_manifest(path, store=None, *, _physical_refs=None):
    """The bytes of a pinned head or archive.  A ``<sha>.splice.json`` archive is reconstructed and verified, and an
    archive ``<store>/_manifests/<sha>.json`` that was migrated to ``<sha>.splice.json`` is read from the latter."""
    path = Path(path)
    if not is_publication_splice_path(path):
        try:
            raw = path.read_bytes()
            if _physical_refs is not None:
                _physical_refs.append(_reader_physical_ref(store or path.parent.parent, path,
                                                          _sha(raw), len(raw), expand=True))
            return raw
        except FileNotFoundError:
            splice = _migrated_archive(path, store)
            if splice is None:
                raise
            path = splice
    reader = _SpliceArchiveReader(store if store is not None else path.parent.parent, path)
    data = reader.read()
    if not reader.verified:
        raise ValueError('publication_splice_reconstruction_mismatch')
    if _physical_refs is not None:
        _physical_refs.extend(_splice_reader_refs(store or path.parent.parent, reader))
    return data


def _verify_pin_splice(path, expected, binding, store):
    """``_verify_pin_file`` for a splice archive: the reconstructed stream is hashed, strictly parsed and bound in
    one pass; every blob, the total size and sha256 and the splice file identity are verified (fail closed)."""
    try:
        source = _SpliceArchiveReader(store if store is not None else Path(path).parent.parent, path)
    except (OSError, ValueError, TypeError, UnicodeError, RecursionError) as exc:
        raise ValueError(f'published_context_pin_splice_invalid:{exc}') from exc
    if source.sha256 != expected:
        raise ValueError('published_context_pin_hash_mismatch')
    reader = _HashingReader(source)
    top, parse_error = {}, None
    try:
        top = _stream_pin_document(_JSONStream(reader), binding.add if binding is not None else None)
    except (ValueError, TypeError, UnicodeError, RecursionError) as exc:
        parse_error = exc
    try:
        while reader.read(_CHUNK_SIZE):          # reconstruct (and verify) whatever a parse error left unread
            pass
    except (OSError, ValueError) as exc:
        raise ValueError(f'published_context_pin_splice_invalid:{exc}') from exc
    if not source.verified:
        raise ValueError('published_context_pin_splice_invalid:'
                         + (str(parse_error) if parse_error is not None else 'incomplete'))
    if reader.hasher.hexdigest() != expected or reader.count != source.size:
        raise ValueError('published_context_pin_hash_mismatch')
    if parse_error is not None:
        raise ValueError(f'published_context_pin_json_invalid:{parse_error}') from parse_error
    if binding is not None:
        binding.finish(top)
    return reader.count


class _ReaderLeaseOwner:
    def __init__(self):
        self.leases, self.users, self.closing = [], 0, False
        self.lock = threading.RLock()

    def add(self, lease):
        with self.lock:
            if self.closing:
                lease.close()
                raise ValueError('published_reader_closed')
            self.leases.append(lease)

    def acquire(self, *, allow_closing=False):
        with self.lock:
            if self.closing and not (allow_closing and self.users):
                raise ValueError('published_reader_closed')
            self.users += 1

    def release(self):
        with self.lock:
            self.users -= 1
            self._finish()

    def close(self):
        with self.lock:
            self.closing = True
            self._finish()

    def _finish(self):
        if self.closing and self.users == 0:
            pending, self.leases = self.leases, []
            first_error = None
            for lease in pending:
                try:
                    lease.close()
                except OSError as exc:
                    self.leases.append(lease)
                    first_error = first_error or exc
            if first_error is not None:
                raise first_error

    @contextlib.contextmanager
    def reading(self):
        self.acquire()
        try:
            yield
        finally:
            self.release()


def _close_reader_owner(owner):
    with contextlib.suppress(OSError):
        owner.close()


def _reader_operation(method):
    @functools.wraps(method)
    def guarded(self, *args, **kwargs):
        with self._reader_owner.reading():
            return method(self, *args, **kwargs)
    return guarded


class _ReaderLeaseIterator:
    def __init__(self, iterator, owner, *, artifact_rows=False, _allow_closing=False):
        owner.acquire(allow_closing=_allow_closing)
        self.iterator, self.owner, self.artifact_rows = iter(iterator), owner, artifact_rows
        self.finalizer = weakref.finalize(self, owner.release)

    def __iter__(self):
        return self

    def __next__(self):
        if not self.finalizer.alive:
            raise StopIteration
        try:
            value = next(self.iterator)
            if self.artifact_rows:
                artifact, rows = value
                return artifact, _ReaderLeaseIterator(rows, self.owner, _allow_closing=True)
            return value
        except BaseException:
            self.close()
            raise

    def close(self):
        try:
            close = getattr(self.iterator, 'close', None)
            if close is not None:
                close()
        finally:
            self.finalizer()


def _reader_physical_ref(root, path, digest, size, *, expand=False):
    root, path = Path(root).resolve(), Path(path).resolve()
    if not path.is_relative_to(root):
        root = path.parent
    return dict(root=str(root), ref=dict(ref=path.relative_to(root).as_posix(),
        sha256=digest, size=size), expand=expand)


def _splice_reader_refs(root, reader):
    result = [_reader_physical_ref(root, reader.path, reader.physical_sha256,
                                   reader.identity[2])]
    result.extend(_reader_physical_ref(root, reader.blob_directory / (item['b'] + '.bin'),
                                       item['b'], item['n'])
                  for item in reader.segments if 'b' in item)
    return result


class PublishedSources:
    """Snapshot descriptors at construction; read each selected payload once."""

    def __init__(self, specs, row_cache_dir=None):
        import stock_cache_lifecycle as lifecycle
        self._reader_owner = _ReaderLeaseOwner()
        self._reader_finalizer = weakref.finalize(self, _close_reader_owner, self._reader_owner)
        self._reader_manifest_refs = {}
        self._reader_extra_refs = {}
        self.row_cache_dir = Path(row_cache_dir) if row_cache_dir is not None else None
        self.diagnostics = []
        self._sources = []
        self._cache = {}
        self._verified = {}
        self._validation_failures = {}
        self._batch_verified = {}
        self._batch_artifacts = {}
        self._batch_prepared = False
        self._pinned_catalog_errors = []
        self._runtime_route_errors = {}
        # v2 heads: one viewed entry per pinned entry and one evidence store per source (published_entry_view);
        # per source index, whether its pinned bytes can hold a "$evidence" key (absent = unknown: scan)
        self._entry_views = {}
        self._evidence_stores = {}
        self._evidence_scan = {}
        identities = []
        try:
            for index, spec in enumerate(specs):
                if isinstance(spec, Mapping):
                    spec = SourceSpec(**spec)
                root = Path(spec.root).resolve()
                source_id = spec.source_id or f'{spec.kind}:{root}'
                state = dict(root=root, source_id=source_id, index=index, entries={},
                             errors={}, snapshot_id='missing', kind=spec.kind, identity={})
                try:
                    with lifecycle.catalog_lock(lifecycle.source_registry(root)):
                        self._pin(spec, state)
                        self._register_reader_lease(state, already_locked=True)
                except (OSError, ValueError, TypeError, KeyError) as exc:
                    state['error'] = f'{type(exc).__name__}: {exc}'
                    self.diagnostics.append(f'{source_id}: {state["error"]}')
                identities.append((source_id, state['snapshot_id'], state.get('error')))
                self._sources.append(state)
            for state in self._sources:
                if not state.get('error'):
                    self._disclose_unresolved_entries(state)
            self._pinned_catalog_errors = [copy.deepcopy(state['errors']) for state in self._sources]
            self.fingerprint = _sha(json.dumps(identities, ensure_ascii=True).encode())
        except BaseException:
            self.close()
            raise

    def _reader_source_refs(self, state):
        root, identity = state['root'], state.get('identity', {})
        refs = list(self._reader_manifest_refs.get(state['index'], ()))
        if not refs and identity.get('manifest_path'):
            path = Path(identity['manifest_path'])
            digest = identity.get('manifest_sha256')
            archive = root / PUBLICATION_ARCHIVE_DIRNAME / (str(digest) + '.json')
            if archive.is_file():
                path = archive
            if path.is_file():
                refs.append(_reader_physical_ref(root, path, digest, path.stat().st_size, expand=True))
            else:
                splice = _migrated_archive(path, root)
                if splice is not None:
                    refs.extend(_splice_reader_refs(root, _SpliceArchiveReader(root, splice)))
        for item in refs:
            if item['expand']:
                item['route_ids'] = list(state['entries'])

        def proof_refs(metadata):
            if not isinstance(metadata, Mapping):
                return
            for key in ('application_retention_scope', 'application_capture_scope',
                        'verified_market_calendar', 'current_ordinary_identity', 'coverage_ref'):
                reference = metadata.get(key)
                if (isinstance(reference, Mapping) and reference.get('sha256')
                        and type(reference.get('size')) is int
                        and isinstance(reference.get('ref', reference.get('path')), str)):
                    refs.append(_reader_physical_ref(root,
                        _path(root, reference.get('ref', reference.get('path'))),
                        reference['sha256'], reference['size'], expand=True))

        for metadata in self._reader_extra_refs.get(state['index'], ()):
            proof_refs(metadata)
        for entries in state['entries'].values():
            for reference, entry in entries:
                refs.append(_reader_physical_ref(root,
                    _path(root, reference.get('ref', reference.get('path'))),
                    reference.get('sha256'), reference.get('size')))
                proof_refs(entry)
                proof_refs(entry.get('metadata'))
        unique = {}
        for item in refs:
            reference = item['ref']
            unique[(item['root'], reference['ref'], reference['sha256'],
                    reference['size'], item['expand'])] = item
        return list(unique.values())

    def _register_reader_lease(self, state, *, already_locked=False):
        import stock_cache_lifecycle as lifecycle
        if state.get('error'):
            return
        digest = state.get('identity', {}).get('manifest_sha256') or state['snapshot_id']
        registry = lifecycle.source_registry(state['root'])
        with contextlib.nullcontext() if already_locked else lifecycle.catalog_lock(registry):
            refs = self._reader_source_refs(state)
            if refs:
                self._reader_owner.add(lifecycle.CacheLease(registry, 'source-' + digest,
                    physical_refs=refs, already_locked=True))

    def close(self):
        """End this reader; in-flight row iterators keep their source leases until they finish."""
        self._reader_owner.close()
        if not self._reader_owner.leases:
            self._reader_finalizer.detach()

    def __enter__(self):
        return self

    def __exit__(self, *_):
        self.close()

    def _pin(self, spec, state):
        root = state['root']
        if spec.kind == 'twstock_updater':
            pointer = _path(root, 'current.pointer').read_bytes()
            digest = pointer.decode('ascii').strip()
            state['snapshot_id'] = _sha(pointer)
            if not re.fullmatch('[0-9a-f]{64}', digest):
                raise ValueError('invalid current pointer')
            seals = []
            for path in (root / 'generations').glob('*/seal.json'):
                raw = _path(root, path).read_bytes()
                if _sha(raw) == digest:
                    seals.append((path, _json(raw)))
            if len(seals) != 1:
                raise ValueError('pointer must match exactly one seal')
            path, seal = seals[0]
            state['identity'].update(seal_path=str(path), seal_sha256=digest,
                                     generation_id=seal.get('generation_id'))
            if (seal.get('schema') != 1 or seal.get('kind') != 'generation-seal'
                    or seal.get('sealed') is not True
                    or seal.get('generation_id') != path.parent.name):
                raise ValueError('invalid generation seal')
            document = _json(_read(root, seal['manifest']))
            state['identity'].update(manifest_path=str(_path(root, seal['manifest']['ref'])),
                                     manifest_sha256=seal['manifest']['sha256'])
            if (document.get('schema') != 1
                    or document.get('kind') != 'generation-manifest'
                    or document.get('generation_id') != seal['generation_id']):
                raise ValueError('invalid generation manifest')
            for entry in document.get('metadata', {}).get('routes', []):
                route = _route(entry.get('route_id'))
                if entry.get('available') is not True:
                    continue
                ref = entry.get('payload')
                if not isinstance(ref, dict) or ref != document.get('refs', {}).get('route_' + route):
                    state['errors'][route] = 'route payload reference mismatch'
                    continue
                state['entries'].setdefault(route, []).append((ref, entry))
        elif spec.kind == 'manifest_json':
            if spec.manifest_path is None:
                raise ValueError('manifest_json requires manifest_path')
            manifest = Path(spec.manifest_path)
            if not manifest.is_absolute():
                manifest = _path(root, manifest)
            # A pinned archive may be a reconstructed splice or a migrated <sha>.json.
            physical_refs = []
            raw = read_publication_manifest(manifest, _publication_store(state), _physical_refs=physical_refs)
            manifest_digest = (physical_refs[0]['ref']['sha256'] if len(physical_refs) == 1
                               and physical_refs[0]['expand'] else _sha(raw))
            archive = root / PUBLICATION_ARCHIVE_DIRNAME / (manifest_digest + '.json')
            if not _content_addressed_pin(manifest) and archive.is_file():
                physical_refs = [_reader_physical_ref(root, archive, manifest_digest, len(raw), expand=True)]
            self._reader_manifest_refs[state['index']] = physical_refs
            state['snapshot_id'] = manifest_digest
            state['identity'].update(manifest_path=str(manifest.resolve()), manifest_sha256=manifest_digest)
            document = _json(raw)
            self._reader_extra_refs[state['index']] = [document.get('metadata') or {}]
            if document.get('schema') not in PUBLICATION_SCHEMAS:
                raise ValueError('unknown publication schema')
            # A "$evidence" key needs a literal "$" or a \u escape in the pinned bytes.
            self._evidence_scan[state['index']] = b'$' in raw or b'\\u' in raw
            if document.get('schema') == PUBLICATION_SCHEMA_V2:
                state['identity']['publication_schema'] = PUBLICATION_SCHEMA_V2
            # Preserve the producer run and publication time pinned by the head.
            for name, key in (('producer_id', 'producer_id'),
                              ('publication_run_id', 'run_id'),
                              ('published_at', 'published_at')):
                value = document.get(key)
                if isinstance(value, str) and value.strip():
                    state['identity'][name] = value.strip()
            for entry in document['datasets']:
                route = _route(entry.get('route_id') or entry.get('dataset_id') or entry.get('dataset'))
                for ref in _manifest_dataset_refs(spec.source_id, entry):
                    state['entries'].setdefault(route, []).append((ref, entry))
        else:
            raise ValueError('unknown source kind')

    @_reader_operation
    def artifacts(self, route_id):
        route = _route(route_id)
        if route in _STREAMED_ONLY_ROUTE_IDS:
            raise StreamedRouteRequiredError(
                f'{route} is never materialized by artifacts(); read it with iter_stock_artifact_rows, '
                'iter_artifact_rows, rows or prewarm_stock_rows (strict streaming) or a sealed projection')
        if self._batch_prepared and route in self._batch_artifacts:
            return self._batch_artifacts[route]
        if route in self._cache:
            return self._cache[route]
        result = []
        for state in self._sources:
            if state.get('error') or route in state['errors']:
                continue
            selected = []
            try:
                for ref, entry in state['entries'].get(route, []):
                    entry = self._published_entry(state, entry)
                    raw = _read(state['root'], ref, required=state['kind'] != 'manifest_json')
                    payload = _json(raw)
                    selected_ref = dict(root=str(state['root']), path=str(ref.get('ref', ref.get('path'))),
                                        sha256=_sha(raw), size=len(raw))
                    selected.append(PublishedArtifact(route, payload, state['source_id'],
                        state['snapshot_id'], _sha(raw), str(entry.get('status', 'unknown')),
                        dict(entry, source_kind=state['kind'], selected_ref=selected_ref)))
            except (OSError, ValueError, TypeError, KeyError) as exc:
                message = f'{type(exc).__name__}: {exc}'
                self._record_route_runtime_error(state, route, message)
                self.diagnostics.append(f'{state["source_id"]}/{route}: {message}')
                continue
            result.extend(selected)
        if self._batch_prepared:
            self._batch_artifacts[route] = tuple(result)
            return self._batch_artifacts[route]
        self.release()
        self._cache[route] = tuple(result)
        for item in result:
            self._remember(item)
        return self._cache[route]

    def _published_entry(self, state, entry):
        """The view of one pinned entry (``published_entry_view`` with this source's evidence store), computed once.

        The pinned catalog keeps the entry exactly as published (descriptors, the pin binding and the catalog
        comparison read those bytes); every consumer gets the view.  A v1 entry's view is the entry itself."""
        key = (state['index'], id(entry))
        cached = self._entry_views.get(key)
        if cached is not None and cached[0] is entry:
            return cached[1]
        store = self._evidence_stores.get(state['index'])
        if store is None:
            store = self._evidence_stores[state['index']] = PublishedEvidenceStore(_publication_store(state))
        constructs = (state['kind'] == 'manifest_json'
                      and state.get('identity', {}).get('publication_schema') == PUBLICATION_SCHEMA_V2)
        view = published_entry_view(entry, store, constructs=constructs,
                                    deep=self._evidence_scan.get(state['index'], True))
        self._entry_views[key] = (entry, view)
        return view

    def _disclose_unresolved_entries(self, state):
        """Pin time: view every pinned entry of ``state`` once (the views are kept) and add one diagnostic per entry
        holding an unresolved marker, so evidence that does not verify is disclosed in every descriptor and report
        gap list from the start.  The markers themselves refuse every proof that reads the entry; payload rows are
        unaffected.  A catalog restored from a descriptor carries the pinning process's diagnostics instead."""
        for route, pairs in state['entries'].items():
            for _ref, entry in pairs:
                view = self._published_entry(state, entry)
                metadata = view.get('metadata') if isinstance(view, Mapping) else None
                fields = published_evidence_unresolved(metadata)
                if fields:
                    message = (f'{state["source_id"]}/{route}: {EVIDENCE_UNRESOLVED_PREFIX}{",".join(fields)} '
                               f'({"; ".join(published_evidence_unresolved_reasons(metadata))})')
                    if message not in self.diagnostics:
                        self.diagnostics.append(message)

    def descriptor(self):
        sources = [dict(source_id=state['source_id'], kind=state['kind'],
                        root=str(state['root']), snapshot_id=state['snapshot_id'],
                        **state['identity']) for state in self._sources]
        artifacts = [dict(route_id=item.route_id, source_id=item.source_id,
                          status=item.status, **item.metadata['selected_ref'])
                     for _, item in sorted(self._verified.items())]
        document = dict(schema='stock-data-published-inputs-v1',
                        snapshot_fingerprint=self.fingerprint, sources=sources,
                        artifacts=artifacts, diagnostics=list(self.diagnostics),
                        **self.data_time())
        # Same definition as always: the sha256 of the canonical document without this key (the data-time keys
        # above are ordinary content, derived only from the pinned catalog, so one head gives one hash).
        document['descriptor_sha256'] = _sha(_canonical(document))
        return document

    def data_time(self):
        """Data time of this pin (plan 4.1): the head's producer-declared ``published_at`` (first source that
        declares one; not independently verified) and the latest ``actual_max_date`` declared by the pinned
        ``finmind_price_adj`` entries.  Informational: no reader decision depends on it."""
        published = next((state['identity']['published_at'] for state in self._sources
                          if isinstance(state.get('identity', {}).get('published_at'), str)), None)
        days = [day for state in self._sources
                if (day := _price_actual_max_date(state['entries'].get('finmind_price_adj'))) is not None]
        return {'published_at': published, 'price_actual_max_date': max(days) if days else None}


    def release(self, route_id=None):
        """Release payload objects; verified source provenance remains pinned."""
        if route_id is None:
            self._cache.clear()
        else:
            self._cache.pop(_route(route_id), None)

    def _remember(self, artifact):
        # D11: the record holds the artifact's own metadata mapping (built fresh for each read; its nested values
        # are the pinned catalog's, which nothing mutates) instead of a deep copy per read.  descriptor() reads
        # only its selected_ref.
        ref = artifact.metadata['selected_ref']
        key = (artifact.route_id, artifact.source_id, ref['root'], ref['path'])
        self._verified[key] = PublishedArtifact(artifact.route_id, None,
            artifact.source_id, artifact.snapshot_id, artifact.content_sha256,
            artifact.status, artifact.metadata)

    def _record_route_runtime_error(self, state, route, message):
        route = _route(route)
        index = state['index']
        pinned_errors = self._pinned_catalog_errors[index]
        if route in pinned_errors:
            return pinned_errors[route]
        key = (index, route)
        existing = self._runtime_route_errors.get(key)
        if existing is None:
            existing = state['errors'].get(route)
            if existing is None:
                existing = str(message)
                self._runtime_route_errors[key] = existing
        state['errors'][route] = existing
        return existing

    def snapshot_descriptor(self, route_ids=None):
        """Export the pinned catalog, without claiming unread payloads were verified."""
        selected = None if route_ids is None else {_route(route) for route in route_ids}
        pinned = []
        for state in self._sources:
            catalog = copy.deepcopy({key: value for key, value in state.items() if key != 'root'})
            catalog['errors'] = copy.deepcopy(self._pinned_catalog_errors[state['index']])
            catalog['root'] = str(state['root'])
            catalog['entries'] = {route: entries for route, entries in catalog['entries'].items()
                                  if selected is None or route in selected}
            pinned.append(catalog)
        document = self.descriptor()
        document.pop('descriptor_sha256')
        if selected is not None:
            document['artifacts'] = [ref for ref in document['artifacts'] if ref['route_id'] in selected]
        document['pinned_sources'] = pinned
        document['runtime_route_errors'] = [
            dict(source_index=index, source_id=self._sources[index]['source_id'],
                 route_id=route, error=message)
            for (index, route), message in sorted(self._runtime_route_errors.items())
            if selected is None or route in selected
        ]
        document['payload_validation'] = 'artifacts_only'
        document['descriptor_sha256'] = _sha(_canonical(document))
        return document

    @_reader_operation
    def projection_artifacts(self, route_id):
        """Return query descriptors derived only from this object's pinned catalog.

        This method deliberately does not open, hash, or parse artifact payloads.
        The query projection verifies the pinned artifact bytes when it builds a
        cold index and verifies its sealed shards when it serves a query.
        ``metadata`` is the pinned manifest entry itself (D11: a direct, read-only
        reference, never a per-call deep copy of up to 13 MB per entry); callers
        that need a private copy make it.
        """
        route = _route(route_id)
        result = []
        for state in self._sources:
            if state.get('error') or route in state['errors']:
                continue
            parent_identity = state.get('identity', {}).get('manifest_sha256')
            source_identity = {
                'source_id': state['source_id'],
                'kind': state['kind'],
                'snapshot_id': state['snapshot_id'],
                'producer_id': state.get('identity', {}).get('producer_id'),
                'publication_run_id': state.get('identity', {}).get('publication_run_id'),
                'run_id': state.get('identity', {}).get('run_id'),
            }
            for ref, entry in state['entries'].get(route, []):
                entry = self._published_entry(state, entry)
                relative = ref.get('ref', ref.get('path'))
                _path(state['root'], relative)
                descriptor = {
                    'route_id': route,
                    'path': str(relative),
                    'sha256': ref.get('sha256'),
                    'size': ref.get('size'),
                    'status': entry.get('status', 'unknown'),
                }
                result.append({
                    'root': str(state['root']),
                    'artifact_descriptor': descriptor,
                    'metadata': entry,
                    'source_id': state['source_id'],
                    'snapshot_id': state['snapshot_id'],
                    'source_kind': state['kind'],
                    'source_identity': source_identity,
                    'parent_identity': parent_identity,
                })
        return tuple(result)

    @_reader_operation
    def cached_stock_artifact_rows(self, route_id, stock_id, *, pinned_snapshot_fingerprint,
                                   stock_field='stock_id', date_field='date'):
        """Read a stock cache after the caller reverified this pinned snapshot.

        This is an explicit fallback for a huge retained route whose full-market
        projection is not already sealed.  It never reads the source payload and
        never changes the stronger validation in ``iter_artifact_rows``.
        """
        route = _route(route_id)
        if not isinstance(stock_id, str) or not stock_id:
            raise ValueError('pinned_stock_cache_context_invalid')
        if pinned_snapshot_fingerprint != self.fingerprint:
            # The caller verified another pin (e.g. before this reader re-pinned a newer head): a cache miss, so the
            # caller reads through the verifying readers of the current pin (plan 2.4), never an error.
            return None
        if self.row_cache_dir is None:
            return None
        prepared, metrics = [], {
            'row_cache_bytes_read': 0,
            'cache_entries_read': 0,
            'rows_loaded': 0,
        }
        for state in self._sources:
            if state.get('error') or route in state['errors']:
                continue
            entries = state['entries'].get(route, [])
            if not entries:
                continue
            for ref, entry in entries:
                entry = self._published_entry(state, entry)
                digest, size = ref.get('sha256'), ref.get('size')
                path = _path(state['root'], ref.get('ref', ref.get('path')))
                evidence = next((key for key in self._batch_verified
                    if key[:4] == (route, state['source_id'], str(state['root']), str(path))), None)
                if state['kind'] != 'manifest_json' and evidence is None:
                    return None
                if evidence is not None:
                    digest, size = evidence[-2:]
                if (not isinstance(digest, str) or not re.fullmatch('[0-9a-f]{64}', digest)
                        or type(size) is not int or size < 0):
                    return None
                identity = dict(schema=_ROW_CACHE_SCHEMA, root=str(state['root']),
                    path=str(path), sha256=digest, size=size, route=route,
                    stock_id=stock_id, mode='stock',
                    filter_semantics=_stock_cache_filter_semantics(stock_field, date_field, route_id=route))
                cache_path = self.row_cache_dir / (_sha(_canonical(identity)) + '.json')
                cached = _load_row_cache(cache_path, identity, io_metrics=metrics)
                if cached is None:
                    return None
                metrics['cache_entries_read'] += 1
                metrics['rows_loaded'] += cached.get('record_count', len(cached['records']))
                artifact = PublishedArtifact(route, None, state['source_id'], state['snapshot_id'],
                    digest, str(entry.get('status', 'unknown')),
                    dict(entry, source_kind=state['kind'],
                         selected_ref=dict(root=str(state['root']),
                             path=str(ref.get('ref', ref.get('path'))), sha256=digest, size=size),
                         payload_metadata=dict(cached.get('payload_metadata') or {}),
                         **_quality_projection_fields(cached['quality_metadata']),
                         row_cache_verified=True))
                prepared.append((artifact, iter(cached['records'])))
        if not prepared:
            return None
        for artifact, _rows in prepared:
            self._remember(artifact)
        return {'artifacts': tuple(prepared), 'metrics': metrics}

    @contextlib.contextmanager
    def stock_cache_lock(self, route_id, stock_id, *, lock_timeout_seconds=120,
                         on_lock_wait=None):
        """Serialize route-version cache preparation with a process-owned lock."""
        if self.row_cache_dir is None:
            yield
            return
        route = _route(route_id)
        artifacts = []
        for state in self._sources:
            if state.get('error') or route in state['errors']:
                continue
            for ref, _entry in state['entries'].get(route, []):
                path = _path(state['root'], ref.get('ref', ref.get('path')))
                artifacts.append(dict(root=str(state['root']), path=str(path),
                    sha256=ref.get('sha256'), size=ref.get('size')))
        key = _sha(_canonical(dict(schema=_ROW_CACHE_SCHEMA,
            filter_semantics=_stock_cache_filter_semantics(route_id=route), route=route,
            stock_id=str(stock_id), artifacts=artifacts)))
        lock_dir = self.row_cache_dir / '.locks'
        lock_dir.mkdir(parents=True, exist_ok=True)
        lock_path = lock_dir / (key + '.lock')
        from stock_query_projection import _writer_lock
        with _writer_lock(lock_path, timeout_seconds=lock_timeout_seconds,
                          on_lock_wait=on_lock_wait):
            yield

    @_reader_operation
    def validate_pinned_context(self, snapshot_descriptor):
        """Verify descriptor/catalog identity and only its small pin files.

        Large route payloads are intentionally excluded; they are proven by the
        query index's source binding and the selected sealed shard hashes.

        Version-type differences are reported, not raised (plan 2.4), in the proof's ``version_labels``:
        - ``head_updated_after_pin``: the pinned bytes were only at a mutable head path (``_metadata/...``, no
          content-addressed archive) and a newer publication rewrote it.  This reader is re-pinned in place to
          the latest head (the whole catalog, every source) and that pin is verified instead; the label carries
          the recorded and the new head sha256 and ``published_at``.
        - ``pinned_snapshot_missing``: the pinned snapshot no longer exists anywhere; re-pinned likewise.
        - ``catalog_mismatch``: the supplied descriptor names another catalog than this reader holds; the proof is
          then about this reader's own catalog (``descriptor_refreshed``: callers re-export the descriptor).
        - ``pointer_advanced_after_pin`` (twstock_updater): the pointer moved on; the pinned seal stays verified.
        Still raised (integrity): a content-addressed ``<sha>.json`` / ``<sha>.splice.json`` whose bytes differ,
        a file changed while read, invalid JSON, a catalog not bound to the verified manifest bytes, invalid pin
        identities or paths, and a re-pin whose latest head itself does not verify.
        """
        drift = C0VersionDrift()
        repinned = False
        try:
            proof = self._validate_pinned_context_once(snapshot_descriptor, drift, allow_repin=True)
        except _PinnedHeadMoved as moved:
            self._repin_latest_head(drift, moved)
            repinned = True
            proof = self._validate_pinned_context_once(self.snapshot_descriptor(), drift, allow_repin=False)
        proof['repinned'] = repinned
        proof['descriptor_refreshed'] = repinned or proof.get('descriptor_refreshed', False)
        proof['data_time'] = self.data_time()
        proof['head_identity'] = tuple(
            {'source_id': state['source_id'], 'snapshot_id': state['snapshot_id'],
             'manifest_sha256': state.get('identity', {}).get('manifest_sha256'),
             'published_at': state.get('identity', {}).get('published_at'),
             'publication_run_id': state.get('identity', {}).get('publication_run_id')}
            for state in self._sources)
        proof['version_labels'] = list(drift.labels)
        return proof

    def _repin_latest_head(self, drift, moved):
        """Re-pin every source of this reader to its latest mutable head, in place (plan 2.4, step 5).

        Only ``manifest_json`` sources can be re-pinned (any other kind re-raises ``moved``).  A source pinned
        through a mutable head path is re-pinned on that path; one pinned through a content-addressed archive on
        ``<root>/_metadata/stock_data_current.json``.  The fresh pin is built exactly like a new reader
        (``PublishedSources(specs)``) and must pin without a source error, else the re-pin fails closed.  Payload
        objects, row-evidence and diagnostics of the old pin are dropped with it; the number of payload reads
        served before the re-pin is recorded in the label."""
        specs = []
        for state in self._sources:
            if state['kind'] != 'manifest_json':
                raise moved
            recorded = state.get('identity', {}).get('manifest_path')
            head = Path(state['root']) / '_metadata' / 'stock_data_current.json'
            if isinstance(recorded, str) and recorded and not _content_addressed_pin(Path(recorded)):
                head = Path(recorded)
            specs.append(SourceSpec('manifest_json', state['root'], head, state['source_id']))
        fresh = type(self)(specs, row_cache_dir=self.row_cache_dir)
        failed = [f'{state["source_id"]}: {state["error"]}' for state in fresh._sources if state.get('error')]
        if failed:
            fresh.close()
            raise ValueError('published_context_repin_failed:' + '; '.join(failed)) from moved
        reads = len(self._verified) + len(self._batch_verified) + len(self._cache)
        for old, new, spec in zip(self._sources, fresh._sources, specs):
            old_identity, new_identity = old.get('identity', {}), new.get('identity', {})
            drift.note(moved.code,
                recorded={'manifest_sha256': old_identity.get('manifest_sha256'),
                          'published_at': old_identity.get('published_at'),
                          'price_actual_max_date': _price_actual_max_date(old['entries'].get('finmind_price_adj'))},
                actual={'manifest_sha256': new_identity.get('manifest_sha256'),
                        'published_at': new_identity.get('published_at'),
                        'price_actual_max_date': _price_actual_max_date(new['entries'].get('finmind_price_adj'))},
                detail={'source_id': old['source_id'], 'reason': str(moved), 'checked_path': moved.path,
                        'repinned_head_path': str(Path(spec.manifest_path).resolve()),
                        'previous_snapshot_fingerprint': self.fingerprint,
                        'payload_reads_before_repin': reads})
        previous_owner = self._reader_owner
        self._reader_finalizer.detach()
        fresh._reader_finalizer.detach()
        self.__dict__.update(fresh.__dict__)
        self._reader_finalizer = weakref.finalize(self, _close_reader_owner, self._reader_owner)
        previous_owner.close()

    def _validate_pinned_context_once(self, snapshot_descriptor, drift, *, allow_repin):
        reconstructed = type(self).from_descriptor(
            snapshot_descriptor, row_cache_dir=self.row_cache_dir, _lease_sources=False
        )
        mismatch = self._catalog_difference(reconstructed)
        descriptor_refreshed = False
        if mismatch is not None:
            # plan 2.4 (catalog_mismatch -> label): the supplied descriptor is not this reader's catalog (a stale
            # or edited copy).  Reads go through this reader, so its own catalog is what is verified below; a
            # catalog of this reader that is not bound to the verified manifest bytes still fails there.
            drift.note('catalog_mismatch', recorded=reconstructed.fingerprint, actual=self.fingerprint,
                       detail={'reason': mismatch})
            reconstructed = type(self).from_descriptor(self.snapshot_descriptor(), row_cache_dir=self.row_cache_dir,
                                                        _lease_sources=False)
            if self._catalog_difference(reconstructed) is not None:
                raise ValueError('published_context_catalog_mismatch')
            descriptor_refreshed = True
        proof = self._verify_pin_files(reconstructed, drift, allow_repin=allow_repin)
        proof['descriptor_refreshed'] = descriptor_refreshed
        return proof

    def _catalog_difference(self, reconstructed):
        """None when ``reconstructed`` (a reader restored from a descriptor) holds exactly this reader's catalog,
        else the first difference.  This reader's own runtime-error bookkeeping being inconsistent still raises."""
        def catalog(states, pinned_errors):
            return [{
                'source_id': state['source_id'],
                'kind': state['kind'],
                'root': str(state['root']),
                'snapshot_id': state['snapshot_id'],
                'index': state['index'],
                'identity': state['identity'],
                'entries': state['entries'],
                'errors': pinned_errors[state['index']],
                'error': state.get('error'),
            } for state in states]
        def runtime_errors_difference(source):
            expected = [copy.deepcopy(errors) for errors in source._pinned_catalog_errors]
            for (index, route), message in source._runtime_route_errors.items():
                if (type(index) is not int or not 0 <= index < len(expected)
                        or route in expected[index] or not isinstance(message, str) or not message):
                    return 'runtime_route_error_invalid'
                expected[index][route] = message
            if [state['errors'] for state in source._sources] != expected:
                return 'route_errors_inconsistent'
            return None

        # This reader's own bookkeeping must be consistent (integrity of the reader, never a label).
        if runtime_errors_difference(self) is not None:
            raise ValueError('published_context_catalog_mismatch')
        if reconstructed._pinned_catalog_errors != self._pinned_catalog_errors:
            return 'pinned_route_errors_differ'
        reason = runtime_errors_difference(reconstructed)
        if reason is not None:
            return 'descriptor_' + reason
        if any(self._runtime_route_errors.get(key) != message
               for key, message in reconstructed._runtime_route_errors.items()):
            return 'runtime_route_errors_differ'
        if reconstructed.fingerprint != self.fingerprint:
            return 'snapshot_fingerprint_differs'
        # The two catalogs are compared by the SHA-256 of their canonical bytes, serialized one after the other, so
        # only one serialization is alive at a time (the head of 2026-10-05 serializes to 61 MB per catalog).
        if (_sha(_canonical(catalog(reconstructed._sources, reconstructed._pinned_catalog_errors)))
                != _sha(_canonical(catalog(self._sources, self._pinned_catalog_errors)))):
            return 'catalog_content_differs'
        return None

    def _verify_pin_files(self, reconstructed, drift, *, allow_repin):
        """Hash, parse and bind the small pin files of ``reconstructed`` (see ``validate_pinned_context``)."""
        verified_paths = []
        pin_bytes_read = 0
        for state in reconstructed._sources:
            identity = state.get('identity', {})
            for path_key, digest_key in (
                ('head_path', 'head_sha256'),
                ('seal_path', 'seal_sha256'),
                ('manifest_path', 'manifest_sha256'),
            ):
                path_value, expected = identity.get(path_key), identity.get(digest_key)
                if path_value is None and expected is None:
                    continue
                if not isinstance(path_value, str) or not isinstance(expected, str):
                    raise ValueError('published_context_pin_identity_invalid')
                if not re.fullmatch('[0-9a-f]{64}', expected):
                    raise ValueError('published_context_pin_hash_invalid')
                path = Path(path_value)
                if not path.is_absolute() or str(path.resolve()) != path_value:
                    raise ValueError('published_context_pin_path_invalid')
                store = _publication_store(state)
                archived = (path_key == 'manifest_path'
                        and state['kind'] == 'manifest_json'
                        and state['source_id'] == 'shared_market_data'
                        and state['root'] == _CANONICAL_SHARED_MARKET_DATA_ROOT
                        and (path == store / '_metadata' / 'stock_data_current.json'
                             or (path.parent == store / PUBLICATION_ARCHIVE_DIRNAME
                                 and path.name in (expected + '.json', expected + PUBLICATION_ARCHIVE_SPLICE_SUFFIX))))

                def archive_path():
                    # Verify the old publication bytes, not the mutable current catalog: its archive <sha>.json, else
                    # the deduplicated <sha>.splice.json (spec section 3 item 6; a migration moves the former away).
                    archive = publication_archive_path(store, expected)
                    if archive is not None and (archive.resolve() != archive or not archive.is_relative_to(store)):
                        raise ValueError('published_context_pin_path_invalid')
                    return archive

                if archived:
                    path = archive_path() or path
                # No byte cap: the pin is hashed and strictly parsed in one streamed pass, and a manifest's
                # datasets are bound to the restored catalog entry by entry (memory: one block plus the largest
                # entry, whatever the file size).
                def binding():
                    return (_ManifestPinBinding(state, reconstructed._pinned_catalog_errors[state['index']])
                            if (path_key == 'manifest_path' and not state.get('error')
                                and state['kind'] == 'manifest_json') else None)
                # A re-pin is possible only for a manifest_json head (plan 2.4); every other pin keeps failing.
                repinnable = (allow_repin and path_key == 'manifest_path' and state['kind'] == 'manifest_json'
                              and not state.get('error'))

                def moved(message, code, checked):
                    return _PinnedHeadMoved(message, code=code, state_index=state['index'], path=checked)

                try:
                    pin_bytes_read += _verify_pin_file(path, expected, binding(), store)
                except FileNotFoundError:
                    if not archived:
                        if repinnable:
                            # The pinned snapshot no longer exists: use the current head, labelled.
                            raise moved('published_context_pin_missing', 'pinned_snapshot_missing', path) from None
                        raise
                    # The migration's step 3 may move <sha>.json away right after its splice verified: look the
                    # archive up once more (a fresh binding); an archive that is still missing is no longer a
                    # failure but a re-pin of the current head, labelled (plan 2.4, :3061-3066).
                    retry = archive_path()
                    if retry is None or retry == path:
                        if repinnable:
                            raise moved('published_context_pin_archive_missing', 'pinned_snapshot_missing',
                                        path) from None
                        raise ValueError('published_context_pin_archive_missing') from None
                    path = retry
                    pin_bytes_read += _verify_pin_file(path, expected, binding(), store)
                except ValueError as exc:
                    # Only the mutable head may legitimately hold other bytes (a newer publication).  A
                    # content-addressed <sha>.json / <sha>.splice.json with other bytes is tamper: re-raised.
                    if (repinnable and str(exc) == 'published_context_pin_hash_mismatch'
                            and not _content_addressed_pin(path)):
                        raise moved(str(exc), 'head_updated_after_pin', path) from exc
                    raise
                verified_paths.append(str(path))

            if state['kind'] == 'twstock_updater':
                pointer_path = state['root'] / 'current.pointer'
                with pointer_path.open('rb') as handle:
                    before = _file_identity(os.fstat(handle.fileno()))
                    raw = handle.read(65537)
                    after = _file_identity(os.fstat(handle.fileno()))
                pin_bytes_read += len(raw)
                if (len(raw) > 65536 or before != after
                        or before != _file_identity(pointer_path.stat())):
                    raise ValueError('published_context_pointer_mismatch')
                if _sha(raw) != state['snapshot_id']:
                    # The pointer moved on to a newer generation (plan 2.4, :3074-3083): the pinned seal above is
                    # content-verified, so this is a label; a torn pointer read above still raises.
                    drift.note('pointer_advanced_after_pin', recorded=state['snapshot_id'], actual=_sha(raw),
                               detail={'source_id': state['source_id'],
                                       'pinned_seal_sha256': identity.get('seal_sha256')})
                elif raw.decode('ascii').strip() != identity.get('seal_sha256'):
                    raise ValueError('published_context_pointer_target_mismatch')
                verified_paths.append(str(pointer_path.resolve()))
        return {
            'status': 'verified',
            'snapshot_fingerprint': reconstructed.fingerprint,
            'verified_pin_paths': tuple(verified_paths),
            'pin_bytes_read': pin_bytes_read,
            'large_artifacts_verified': False,
        }

    @classmethod
    def from_descriptor(cls, descriptor, row_cache_dir=None, *, _lease_sources=True):
        """Restore only the supplied catalog; never consult a mutable current head.

        Accepts the expanded (v1) descriptor and its v2 storage form (``descriptor_from_storage``); the self-hash is
        always checked on the expanded form.  A shallow copy suffices (D5): only the top-level self-hash is popped,
        nested values are only read, and each pinned source state below is deep-copied once into the restored
        object, so it never shares (or mutates) the supplied descriptor.
        """
        document = dict(descriptor_from_storage(descriptor))
        digest = document.pop('descriptor_sha256', None)
        if (document.get('schema') != 'stock-data-published-inputs-v1'
                or digest != _sha(_canonical(document))):
            raise ValueError('descriptor hash/schema mismatch')
        pinned = document.get('pinned_sources')
        if not isinstance(pinned, list) or not pinned:
            raise ValueError('descriptor has no reconstructable pinned catalog')
        result = cls([], row_cache_dir=row_cache_dir)
        identities, sources, catalog_refs = [], [], set()
        for index, item in enumerate(pinned):
            state = copy.deepcopy(item)
            root = Path(state['root'])
            if not root.is_absolute() or str(root.resolve()) != str(root):
                raise ValueError('pinned root must be canonical and absolute')
            if (state['index'] != index or not isinstance(state['errors'], dict)
                    or any(_route(route) != route or not isinstance(message, str) or not message
                           for route, message in state['errors'].items())):
                raise ValueError('invalid pinned source state')
            if state['kind'] not in ('manifest_json', 'twstock_updater'):
                raise ValueError('invalid pinned source kind')
            if not isinstance(state['source_id'], str) or not state['source_id']:
                raise ValueError('invalid pinned source id')
            if not state.get('error') and not re.fullmatch('[0-9a-f]{64}', state['snapshot_id']):
                raise ValueError('invalid pinned snapshot id')
            for route, entries in state['entries'].items():
                if _route(route) != route or not isinstance(entries, list):
                    raise ValueError('invalid pinned route catalog')
                for ref, metadata in entries:
                    _path(root, ref.get('ref', ref.get('path')))
                    catalog_refs.add((route, state['source_id'], str(root),
                        str(ref.get('ref', ref.get('path'))), ref.get('sha256'), ref.get('size')))
                    if not isinstance(metadata, dict):
                        raise ValueError('invalid pinned metadata')
                    if (not isinstance(ref.get('sha256'), str)
                            or not re.fullmatch('[0-9a-f]{64}', ref['sha256'])
                            or type(ref.get('size')) is not int or ref['size'] < 0):
                        raise ValueError('pinned catalog requires payload hash/size')
            sources.append(dict(source_id=state['source_id'], kind=state['kind'],
                root=str(root), snapshot_id=state['snapshot_id'], **state['identity']))
            state['root'] = root
            identities.append((state['source_id'], state['snapshot_id'], state.get('error')))
            result._sources.append(state)
        result._pinned_catalog_errors = [copy.deepcopy(state['errors']) for state in result._sources]
        runtime_errors = document.get('runtime_route_errors', [])
        if not isinstance(runtime_errors, list):
            raise ValueError('invalid runtime route errors')
        for item in runtime_errors:
            if (not isinstance(item, dict)
                    or set(item) != {'source_index', 'source_id', 'route_id', 'error'}
                    or type(item['source_index']) is not int
                    or not 0 <= item['source_index'] < len(result._sources)
                    or not isinstance(item['source_id'], str)
                    or not isinstance(item['route_id'], str)
                    or _route(item['route_id']) != item['route_id']
                    or not isinstance(item['error'], str) or not item['error']):
                raise ValueError('invalid runtime route error entry')
            index, route, message = item['source_index'], item['route_id'], item['error']
            state = result._sources[index]
            key = (index, route)
            if (state['source_id'] != item['source_id']
                    or route in result._pinned_catalog_errors[index]
                    or key in result._runtime_route_errors):
                raise ValueError('runtime route error conflicts with pinned catalog')
            result._runtime_route_errors[key] = message
            state['errors'][route] = message
        result.fingerprint = _sha(json.dumps(identities, ensure_ascii=True).encode())
        if result.fingerprint != document.get('snapshot_fingerprint') or sources != document.get('sources'):
            raise ValueError('pinned catalog identity mismatch')
        for ref in document.get('artifacts', []):
            if (ref['route_id'], ref['source_id'], ref['root'], ref['path'], ref['sha256'], ref['size']) not in catalog_refs:
                raise ValueError('verified reference does not match pinned catalog')
        result.diagnostics = list(document.get('diagnostics', []))
        # Historical verified refs are not carried forward as current verification.
        if _lease_sources:
            try:
                for state in result._sources:
                    result._register_reader_lease(state)
            except BaseException:
                result.close()
                raise
        return result

    def iter_artifact_rows(self, route_id):
        """Yield metadata and bounded row iterators; callers must exhaust each iterator.

        Hash and strict JSON validation precede exposure. A second streaming parse
        yields rows and checks file identity again on exhaustion.
        """
        yield from self._iter_artifact_rows(route_id)

    def iter_stock_artifact_rows(self, route_id, stock_id):
        from published_single_stock_projection import CURRENT_C0_ROUTE_IDS
        route = _route(route_id)
        if (self.row_cache_dir is None or stock_id is None or str(stock_id) == '' or
                (route not in CURRENT_C0_ROUTE_IDS and route not in _RETAINED_ROW_CACHE_ROUTE_IDS)):
            yield from self.iter_artifact_rows(route)
        else:
            yield from self._iter_artifact_rows(route, stock_id=str(stock_id))

    @_reader_operation
    def prewarm_stock_rows(self, stock_ids, route_ids, *, single_stock_prepare=False,
                           stock_field='stock_id', date_field='date'):
        """Validate selected routes and write consumer-owned per-stock caches."""
        if self.row_cache_dir is None:
            raise ValueError('consumer row preparation requires a consumer-owned cache directory')
        stocks = tuple(dict.fromkeys(str(value) for value in stock_ids if value is not None and str(value)))
        if not stocks:
            raise ValueError('consumer row preparation requires stock ids')
        if type(single_stock_prepare) is not bool:
            raise TypeError('single_stock_prepare must be bool')
        if single_stock_prepare and len(stocks) != 1:
            raise ValueError('single_stock_prepare requires exactly one stock id')
        if stock_field is not None and (not isinstance(stock_field, str) or not stock_field):
            raise ValueError('stock_field must be a non-empty string or None')
        if date_field is not None and (not isinstance(date_field, str) or not date_field):
            raise ValueError('date_field must be a non-empty string or None')
        routes = tuple(dict.fromkeys(_route(value) for value in route_ids))
        gaps = []
        latest_price_date = None
        prepared_files = 0
        prepared_source_bytes_read = 0
        from published_single_stock_projection import _date
        from stock_query_projection import _normalise_day, _normalise_stock
        stock_set = set(stocks)

        for route in routes:
            for state in self._sources:
                if state.get('error') or route in state['errors']:
                    continue
                entries = state['entries'].get(route, [])
                if not entries:
                    continue
                source_route_failed = False
                source_route_latest = None
                for ref, entry in entries:
                    spools = {}
                    try:
                        path = _path(state['root'], ref.get('ref', ref.get('path')))
                        if route in _STREAMED_ONLY_ROUTE_IDS:
                            # No byte cap: the declared identity must be well formed, and the strict
                            # stream below proves sha256, size, record count and file identity.
                            if type(ref.get('size')) is not int or ref['size'] < 0:
                                raise PublishedArtifactValidationError(f'{route} artifact size invalid')
                            if (not isinstance(ref.get('sha256'), str)
                                    or not re.fullmatch('[0-9a-f]{64}', ref['sha256'])):
                                raise PublishedArtifactValidationError(f'{route} artifact sha256 invalid')

                        budget = _RowCacheSpoolBudget()
                        spools = {stock: _RowCacheSpool(self.row_cache_dir, budget) for stock in stocks}
                        found, payload_metadata = set(), {}
                        row_counts = {key: 0 for key in _ARRAY_KEYS}
                        dates_by_array = {key: None for key in _ARRAY_KEYS}
                        quality_by_array = {key: dict(missing_date_rows=0, missing_stock_rows=0)
                                            for key in _ARRAY_KEYS}
                        scopes = {key: _GlobalRowScope(route, date_field) for key in _ARRAY_KEYS}

                        def collect(key, row):
                            row_counts[key] += 1
                            scopes[key].add(row)
                            if route == 'finmind_price_adj' and isinstance(row, dict):
                                day = _date(row.get('date'))
                                if day and (dates_by_array[key] is None or day > dates_by_array[key]):
                                    dates_by_array[key] = day
                            if not isinstance(row, dict):
                                selected_stocks = stocks
                            else:
                                quality = quality_by_array[key]
                                if date_field and _normalise_day(row.get(date_field)) is None:
                                    quality['missing_date_rows'] += 1
                                stock_value = (row.get(stock_field, row.get('stock_id', row.get('data_id', '')))
                                               if stock_field else None)
                                if stock_field and _normalise_stock(stock_value) is None:
                                    quality['missing_stock_rows'] += 1
                                row_stock_id = row.get(stock_field, row.get('stock_id', row.get('data_id', '')))
                                if route == 'official_market_daily' and stock_field == 'stock_id':
                                    row_stock_id = (row.get('code') or row.get('stock_id') or row.get('Code')
                                                    or row.get('SecuritiesCompanyCode') or '')
                                selected_stocks = ((str(row_stock_id),)
                                                   if str(row_stock_id) in stock_set else ())
                                if route == _ORDER21_ROUTE_ID and _order21_invalid_identity(row):
                                    selected_stocks = stocks
                            for stock in selected_stocks:
                                spools[stock].add(key, row)

                        digest, size, identity = _validate_rows(
                            state['root'], ref, found, payload_metadata,
                            required=state['kind'] != 'manifest_json', row_callback=collect)
                        prepared_source_bytes_read += size
                        selected = next((key for key in _ARRAY_KEYS if key in found), None)
                        if selected is None:
                            raise ValueError('unsupported row payload')
                        _check_declared_record_count(route, state, entry, row_counts[selected])
                        for key, scope in scopes.items():
                            quality_by_array[key]['global_scope'] = scope.document()
                        selected_latest = dates_by_array[selected]

                        for stock, spool in spools.items():
                            cache_identity = dict(schema=_ROW_CACHE_SCHEMA, root=str(state['root']),
                                path=str(path), sha256=digest, size=size, route=route,
                                stock_id=stock, mode='stock',
                                filter_semantics=_stock_cache_filter_semantics(stock_field, date_field, route_id=route))
                            cache_path = self.row_cache_dir / (_sha(_canonical(cache_identity)) + '.json')
                            written = _write_spooled_row_cache(cache_path, cache_identity, selected,
                                payload_metadata, quality_by_array[selected], spool)
                            spool.close()
                            if not written:
                                raise ValueError(f'consumer row cache write failed for stock {stock}')

                        artifact = PublishedArtifact(route, None, state['source_id'], state['snapshot_id'],
                            digest, str(entry.get('status', 'unknown')),
                            dict(self._published_entry(state, entry), source_kind=state['kind'],
                                 selected_ref=dict(root=str(state['root']),
                                                   path=str(ref.get('ref', ref.get('path'))),
                                                   sha256=digest, size=size),
                                 payload_metadata=payload_metadata,
                                 **_quality_projection_fields(quality_by_array[selected])))
                        self._remember(artifact)
                        evidence_key = (route, state['source_id'], str(state['root']), str(path), digest, size)
                        self._batch_verified[evidence_key] = identity
                        prepared_files += 1
                        if selected_latest and (source_route_latest is None or selected_latest > source_route_latest):
                            source_route_latest = selected_latest
                    except (OSError, ValueError, TypeError, KeyError) as exc:
                        message = f'{type(exc).__name__}: {exc}'
                        self._record_route_runtime_error(state, route, message)
                        if isinstance(exc, PublishedArtifactValidationError):
                            self._validation_failures[(state['source_id'], route)] = True
                        diagnostic = f'{state["source_id"]}/{route}: batch prewarm failed: {message}'
                        self.diagnostics.append(diagnostic)
                        gaps.append(diagnostic)
                        source_route_failed = True
                        break
                    finally:
                        for spool in spools.values():
                            spool.close()
                if not source_route_failed and source_route_latest and (
                        latest_price_date is None or source_route_latest > latest_price_date):
                    latest_price_date = source_route_latest
        self._batch_prepared = True
        return dict(latest_price_date=latest_price_date, prepared_files=prepared_files,
                    source_bytes_read=(None if gaps else prepared_source_bytes_read),
                    gaps=tuple(gaps), source_fingerprint=self.fingerprint)

    @_reader_operation
    def latest_artifact_date(self, route_id):
        from published_single_stock_projection import _date
        latest = None
        for _, rows in self._iter_artifact_rows(route_id, date_only=True):
            for row in rows:
                day = _date(row.get('date')) if isinstance(row, dict) else None
                if day and (latest is None or day > latest):
                    latest = day
        return latest

    def _iter_artifact_rows(self, route_id, stock_id=None, date_only=False):
        return _ReaderLeaseIterator(self._iter_artifact_rows_unleased(route_id, stock_id, date_only),
                                    self._reader_owner, artifact_rows=True)

    def _iter_artifact_rows_unleased(self, route_id, stock_id=None, date_only=False):
        route = _route(route_id)
        for state in self._sources:
            if state.get('error') or route in state['errors']:
                continue
            prepared = []
            for ref, entry in state['entries'].get(route, []):
                collector = None
                try:
                    path = _path(state['root'], ref.get('ref', ref.get('path')))
                    found = set()
                    payload_metadata = {}
                    cache_path, cache_identity, cached = None, None, None
                    records = None
                    quality_metadata = None
                    if (self.row_cache_dir is not None and (stock_id is not None or date_only)
                            and isinstance(ref.get('sha256'), str)
                            and re.fullmatch('[0-9a-f]{64}', ref['sha256'])
                            and type(ref.get('size')) is int and ref['size'] >= 0):
                        cache_identity = dict(schema=_ROW_CACHE_SCHEMA, root=str(state['root']),
                            path=str(path), sha256=ref['sha256'], size=ref['size'], route=route,
                            stock_id=stock_id, mode='date' if date_only else 'stock',
                            filter_semantics=('valid-iso-date-maximum-v1' if date_only
                                              else _stock_cache_filter_semantics(route_id=route)))
                        cache_path = self.row_cache_dir / (_sha(_canonical(cache_identity)) + '.json')
                        evidence_key = (route, state['source_id'], str(state['root']), str(path),
                                        ref['sha256'], ref['size'])
                        if (self._batch_prepared and evidence_key in self._batch_verified
                                and not cache_path.is_file()):
                            raise ValueError('batch row cache missing after prewarm')
                    if cache_path is not None and cache_path.is_file():
                        if evidence_key in self._batch_verified:
                            identity = self._batch_verified[evidence_key]
                            if identity != _file_identity(path.stat()):
                                raise ValueError('batch-pinned artifact identity changed')
                            cached = _load_row_cache(cache_path, cache_identity)
                            if cached is None:
                                raise ValueError('batch row cache missing or invalid')
                            if identity != _file_identity(path.stat()):
                                raise ValueError('batch-pinned artifact changed while reading row cache')
                            digest, size = ref['sha256'], ref['size']
                        elif date_only:
                            cached = _load_row_cache(cache_path, cache_identity)
                            if cached is not None:
                                digest, size = ref['sha256'], ref['size']
                                identity = _file_identity(path.stat())
                            else:
                                digest, size, identity = _verify_file(state['root'], ref)
                        else:
                            digest, size, identity = _verify_file(state['root'], ref)
                            cached = _load_row_cache(cache_path, cache_identity)
                    if cached is not None:
                        selected = cached['selected_array']
                        payload_metadata = cached['payload_metadata']
                        records = cached['records']
                        if date_only:
                            records = [{'date': cached['dates'][selected]}]
                        else:
                            quality_metadata = cached['quality_metadata']
                    else:
                        collector = (_RowCollector(stock_id, date_only, route,
                                                   cache_dir=self.row_cache_dir)
                                     if cache_path is not None else None)
                        row_counts = {key: 0 for key in _ARRAY_KEYS}
                        callback = collector
                        if route in _STREAMED_ONLY_ROUTE_IDS:
                            def callback(key, row, _collector=collector, _counts=row_counts):
                                _counts[key] += 1
                                if _collector is not None:
                                    _collector(key, row)
                        digest, size, identity = _validate_rows(state['root'], ref,
                            found, payload_metadata, required=state['kind'] != 'manifest_json',
                            row_callback=callback)
                        selected = next((key for key in _ARRAY_KEYS if key in found), None)
                        if selected is not None:
                            _check_declared_record_count(route, state, entry, row_counts[selected])
                        if selected is not None and collector is not None and not date_only:
                            collector.finish()
                            quality_metadata = collector.quality[selected]
                        if selected is not None and collector is not None:
                            document = dict(schema=_ROW_CACHE_SCHEMA, identity=cache_identity,
                                selected_array=selected, payload_metadata=payload_metadata,
                                records=collector.records[selected], dates=collector.dates)
                            if not date_only:
                                document['quality_metadata'] = collector.quality[selected]
                            if (collector.spilled or (not date_only
                                    and _row_cache_document_exceeds_limit(document))):
                                cache_rows = (collector.spool.iter_records(selected)
                                              if collector.spilled else iter(collector.records[selected]))
                                written = _write_chunked_row_cache(cache_path, cache_identity,
                                    selected, payload_metadata, collector.quality[selected],
                                    cache_rows)
                                if written:
                                    cached = _load_row_cache(cache_path, cache_identity)
                                    if cached is None:
                                        raise ValueError('consumer row cache missing or invalid after write')
                                    records = cached['records']
                            else:
                                written = _write_row_cache(cache_path, document)
                            if not collector.spilled and written:
                                records = ([{'date': collector.dates[selected]}] if date_only
                                           else collector.records[selected])
                        if collector is not None and collector.spool is not None:
                            collector.spool.close()
                    if selected is None:
                        raise ValueError('unsupported row payload')
                    selected_ref = dict(root=str(state['root']), path=str(ref.get('ref', ref.get('path'))),
                                        sha256=digest, size=size)
                    artifact = PublishedArtifact(route, None, state['source_id'], state['snapshot_id'],
                        digest, str(entry.get('status', 'unknown')),
                        dict(self._published_entry(state, entry), source_kind=state['kind'],
                             selected_ref=selected_ref, payload_metadata=payload_metadata,
                             **(_quality_projection_fields(quality_metadata)
                                if quality_metadata is not None else {})))
                    prepared.append((artifact, path, identity, selected, records))
                except (OSError, ValueError, TypeError, KeyError) as exc:
                    message = f'{type(exc).__name__}: {exc}'
                    if collector is not None and collector.spool is not None:
                        collector.spool.close()
                    self._record_route_runtime_error(state, route, message)
                    if isinstance(exc, PublishedArtifactValidationError):
                        self._validation_failures[(state['source_id'], route)] = True
                    self.diagnostics.append(f'{state["source_id"]}/{route}: {message}')
                    prepared = []
                    break
            for artifact, path, identity, selected, records in prepared:
                expected = (artifact.content_sha256, artifact.metadata['selected_ref']['size'])
                if identity != _file_identity(path.stat()):
                    # Integrity retry (plan 2.4): same bytes re-hashed -> continue; different bytes -> raise.
                    identity = _rehashed_identity(path, expected)
                    if identity is None:
                        raise ValueError('artifact changed before parsing')
                self._remember(artifact)
                yield artifact, (_cached_rows(path, identity, records, expected) if records is not None
                                 else _stream_rows(path, identity, selected, set(), expected=expected))


    @_reader_operation
    def rows(self, route_id, *, stock_id=None, start_date=None, end_date=None, key_fields=None):
        route = _route(route_id)
        date_scope_proof = route in ('official_market_daily', 'finmind_government_bank')
        # Caller bounds in either spelling (or a date) are compared as canonical YYYY-MM-DD; rows are never rewritten.
        start_key = None if start_date is None else cd.iso_calendar_day(start_date)
        end_key = None if end_date is None else cd.iso_calendar_day(end_date)
        if (start_date is not None and start_key is None) or (end_date is not None and end_key is None):
            raise ValueError('published_rows_date_bound_invalid')
        start_date, end_date = start_key, end_key
        artifacts = self.iter_artifact_rows(route)
        records, gaps, sources, seen = [], [], [], {}
        order21_formats = {}
        order21_blocked_v2_stockdays = set()
        completion_proofs = []
        conflicts, consumer_errors = 0, False
        for artifact, rows in artifacts:
            raw_count, date_scope_count, unknown_scope = 0, 0, False
            sources.append(dict(source_id=artifact.source_id, snapshot_id=artifact.snapshot_id,
                                content_sha256=artifact.content_sha256, status=artifact.status))
            if artifact.status.lower() not in ('success', 'complete', 'available', 'committed'):
                gaps.append(f'{artifact.source_id}: published status {artifact.status}')
            for row in rows:
                raw_count += 1
                if not isinstance(row, dict):
                    gaps.append(f'{artifact.source_id}: non-object row')
                    unknown_scope = True
                    consumer_errors = True
                    continue
                if date_scope_proof:
                    row_day = _completion_day(row.get('date'))
                    if row_day is None:
                        unknown_scope = True
                    elif ((start_date is None or row_day >= start_date)
                            and (end_date is None or row_day <= end_date)):
                        date_scope_count += 1
                if stock_id is not None and str(row.get('stock_id', row.get('data_id', ''))) != str(stock_id):
                    continue
                if start_date is not None or end_date is not None:
                    row_date = cd.iso_day_or_none(row.get('date'))
                    if row_date is None:
                        gaps.append(f'{artifact.source_id}: row date unavailable for filtering')
                        consumer_errors = True
                        continue
                    if start_date is not None and row_date < start_date:
                        continue
                    if end_date is not None and row_date > end_date:
                        continue
                natural_fields = key_fields
                if route == _ORDER21_ROUTE_ID:
                    try:
                        row_format = order21_row_format(row, metadata=artifact.metadata)
                    except ValueError as error:
                        gaps.append(f'{artifact.source_id}: {error}')
                        consumer_errors = True
                        if row.get('row_schema') == ORDER21_MINIMAL_SUMMARY_SCHEMA:
                            order21_blocked_v2_stockdays.add((str(row.get('stock_id', '')), _completion_bound(row.get('date'))))
                        continue
                    stockday = (str(row['stock_id']), _completion_bound(row['date']))
                    order21_formats.setdefault(stockday, set()).add(row_format)
                    natural_fields = _ORDER21_REQUIRED_IDENTITY_FIELDS[:3] if row_format in ('summary', 'summary_v2') else _ORDER21_REQUIRED_IDENTITY_FIELDS
                if natural_fields:
                    if any(row.get(field) is None for field in natural_fields):
                        gaps.append(f'{artifact.source_id}: missing natural key')
                        consumer_errors = True
                    else:
                        key = json.dumps([_natural_key_value(field, row[field]) for field in natural_fields],
                                         sort_keys=True)
                        if key in seen:
                            conflicts += _row_by_value(seen[key]) != _row_by_value(row)
                            if (route == _ORDER21_ROUTE_ID and row_format == 'summary_v2'
                                    and _row_by_value(seen[key]) != _row_by_value(row)):
                                order21_blocked_v2_stockdays.add(stockday)
                            continue
                        seen[key] = row
                records.append(dict(row))
            completion = published_completion(route, artifact.metadata, verified_object=True,
                query_empty=(not unknown_scope and date_scope_count == 0) if date_scope_proof else raw_count == 0,
                global_empty=raw_count == 0, start_date=start_date, end_date=end_date)
            completion['source_id'] = artifact.source_id
            if (not records and not completion['completion_evidence_valid']
                    and completion['completion_state'] != 'conflict'):
                completion.update(status='missing', reason='unknown_empty', completion_state='unknown_empty',
                                  usable=False, model_usable=False)
            completion_proofs.append(completion)
        mixed = {stockday for stockday, formats in order21_formats.items() if len(formats) > 1}
        if mixed:
            gaps.append('order21_mixed_detail_summary:' + ','.join('|'.join(day) for day in sorted(mixed)))
            records = [row for row in records
                       if (str(row['stock_id']), _completion_bound(row['date'])) not in mixed]
            consumer_errors = True
        if order21_blocked_v2_stockdays:
            records = [row for row in records
                       if (str(row['stock_id']), _completion_bound(row['date'])) not in order21_blocked_v2_stockdays]
            consumer_errors = True
        for state in self._sources:
            problem = state.get('error') or state['errors'].get(route)
            if problem:
                gaps.append(f'{state["source_id"]}: {problem}')
                consumer_errors = True
        if conflicts:
            gaps.append(f'natural key conflicts: {conflicts}; first source retained')
        completion = published_completion_fields(completion_proofs)
        if consumer_errors:
            completion.update(source_available_complete=False, requested_scope_complete=False,
                completion_evidence_valid=False, data_complete=False, model_usable=False)
        if conflicts:
            completion.update(reason='duplicate_natural_key_conflict', completion_state='conflict',
                source_available_complete=False, data_complete=False, model_usable=False)
        if not records:
            if (completion_proofs and completion.get('source_available_complete') is True
                    and all(proof['completion_evidence_valid'] for proof in completion_proofs)):
                return dict(completion, status='partial' if gaps else 'available',
                            records=(), gaps=tuple(dict.fromkeys(gaps)), sources=tuple(sources))
            gaps.append(f'{route}: no matching published rows')
        incomplete = any(proof['completion_state'] in ('pending_release', 'conflict')
                         for proof in completion_proofs)
        return dict(completion, records=tuple(records), usable=bool(records),
                    status=('partial' if gaps or incomplete else 'available') if records else 'missing',
                    gaps=tuple(dict.fromkeys(gaps)), sources=tuple(sources))
