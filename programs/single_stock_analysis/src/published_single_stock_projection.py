"""Consumer projection of pinned published sources; no network or Central receipt."""
from __future__ import annotations

import json
import argparse
import hashlib
import math
import os
import sys
from collections.abc import Mapping
from datetime import date
from pathlib import Path
try:
    import date_format as cd
except ModuleNotFoundError:  # shared/ is not on sys.path yet (module imported directly)
    sys.path.append(str(Path(__file__).resolve().parents[2] / "shared"))
    import date_format as cd
from stock_data_sources import (
    PublishedSources, SourceSpec, default_source_specs, published_completion,
    published_completion_fields, PUBLISHED_COMPLETION_FIELDS,
    order21_row_format, order21_summary_hint, ORDER21_MINIMAL_SUMMARY_SCHEMA,
)
from stock_data_sources import descriptor_from_storage, descriptor_storage, resolved_last_attempt
from stock_data_sources import C0IntegrityError


class PublishedPinUnavailableError(ValueError):
    """A pin file could not be opened or read (not tamper): distinct from ``C0IntegrityError`` (plan 2.4)."""

    def __init__(self, message, *, reason=None):
        super().__init__(message)
        self.reason = str(message) if reason is None else str(reason)

CURRENT_C0_ROUTE_IDS = (
    "official_market_daily", "official_calendar", "finmind_stock_info",
    "finmind_price_adj", "finmind_dividend", "finmind_dividend_result",
)
# These names classify rows in the generic immutable reader; they do not declare model demand.
_FINANCE = frozenset(("finmind_financial_statements", "finmind_balance_sheet", "finmind_cash_flows_statement"))
_ORDER21_ROUTE_ID = "finmind_trading_daily_report"
_PROJECTION_LOCK_TIMEOUT_ENV = "DStock_SINGLE_STOCK_PROJECTION_LOCK_TIMEOUT_SECONDS"
_DEFAULT_PROJECTION_LOCK_TIMEOUT_SECONDS = 3600.0


def _projection_lock_timeout_seconds():
    raw = os.environ.get(
        _PROJECTION_LOCK_TIMEOUT_ENV,
        str(_DEFAULT_PROJECTION_LOCK_TIMEOUT_SECONDS),
    )
    try:
        value = float(raw)
    except (TypeError, ValueError) as exc:
        raise ValueError(
            f"{_PROJECTION_LOCK_TIMEOUT_ENV} must be finite seconds in range 1..7200"
        ) from exc
    if not math.isfinite(value) or not 1.0 <= value <= 7200.0:
        raise ValueError(
            f"{_PROJECTION_LOCK_TIMEOUT_ENV} must be finite seconds in range 1..7200"
        )
    return value


def _projection_lock_wait_callback(route):
    def report_wait(elapsed_seconds, timeout_seconds):
        print(
            "[INFO] Waiting for projection lock "
            f"route={route} elapsed={elapsed_seconds:.0f}s "
            f"limit={timeout_seconds:g}s",
            file=sys.stderr, flush=True,
        )

    return report_wait

def _date(value):
    """A calendar day in either spelling (YYYYMMDD or YYYY-MM-DD, or a ``date``) -> canonical ``YYYY-MM-DD``.

    Timestamps, JSON numbers and every other format are not dates (None).  Callers use the RETURNED value, never the
    raw one, so every comparison, min/max and set downstream is on the canonical spelling (date directive 2026-10-06).
    """
    return cd.iso_day_or_none(value)


def _canonical(value):
    """A caller bound in either spelling -> canonical ISO; anything that is not a date is returned unchanged."""
    day = _date(value)
    return value if day is None else day


_DEDUP_DATE_FIELDS = frozenset((
    "CashExDividendTradingDate", "StockExDividendTradingDate",
    "cash_ex_dividend_trading_date", "stock_ex_dividend_trading_date",
))


def _dedup_value(field, value, date_field):
    """Dedup key part: a date field by its calendar day, so two spellings of one day are one row (rows unchanged)."""
    if field == date_field or field in _DEDUP_DATE_FIELDS:
        return _canonical(value)
    return value


def _row_by_date_value(row, date_field):
    """A row for the duplicate-content comparison: its date fields by calendar day, every other field as is."""
    return {field: _dedup_value(field, value, date_field) for field, value in row.items()}

def _rows(payload):
    if isinstance(payload, list):
        return payload
    if isinstance(payload, dict):
        for key in ("data", "records", "rows"):
            if isinstance(payload.get(key), list):
                return payload[key]
    return []

def _specs():
    path = Path(__file__).resolve().parents[1] / "config" / "data_sources.json"
    if not path.exists():
        return default_source_specs()
    document = json.loads(path.read_text(encoding="utf-8-sig"))
    return tuple(SourceSpec(**item) for item in document["sources"])

def _digest(document):
    return hashlib.sha256(json.dumps(document, sort_keys=True, ensure_ascii=False,
        separators=(",", ":"), allow_nan=False).encode("utf-8")).hexdigest()


def import_published_sources_context(path, expected_sha256, expected_asof=None, *, row_cache_dir=None):
    raw = Path(path).read_bytes()
    if not expected_sha256 or hashlib.sha256(raw).hexdigest() != expected_sha256:
        raise ValueError("published_descriptor_file_hash_mismatch")
    document = json.loads(raw.decode("utf-8"))
    if isinstance(document, dict) and "snapshot_descriptor" in document:
        # Session files store the snapshot descriptor in its v2 storage form (D2); the session digest and the
        # descriptor self-hash are both defined on the expanded v1 form, so v1 and v2 files verify alike.
        document = dict(document, snapshot_descriptor=descriptor_from_storage(document["snapshot_descriptor"]))
    unsigned = dict(document)
    digest = unsigned.pop("descriptor_sha256", None)
    if document.get("schema") != "single-stock-published-session-v1" or digest != _digest(unsigned):
        raise ValueError("published_session_descriptor_invalid")
    asof = _date(document.get("expected_asof"))
    if not asof:
        raise ValueError("published_context_asof_mismatch")
    # plan 2.4 / open question 11 (default): this import path is live (``--pin --descriptor-output`` writes the
    # session files it reads), so it is kept; a caller asof that differs from the session's is a label, and the
    # session's own (pinned) asof is analysed.
    stored_labels = document.get("version_labels")
    labels = ([dict(item) for item in stored_labels if isinstance(item, Mapping)]
              if isinstance(stored_labels, list) else [])
    if expected_asof is not None and _date(expected_asof) != asof:
        labels.append({"code": "requested_asof_differs", "recorded": _date(expected_asof) or str(expected_asof),
                       "actual": asof, "detail": {"source": "published_session_file"}})
    snapshot = document["snapshot_descriptor"]
    sources = PublishedSources.from_descriptor(snapshot, row_cache_dir=row_cache_dir)
    context = dict(source_mode="published", sources=sources, expected_asof=asof,
        data_date=_canonical(document["data_date"]), fingerprint=sources.fingerprint,
        input_sources=snapshot, snapshot_descriptor=snapshot,
        published_descriptor_sha256=digest, gaps=tuple(sources.diagnostics),
        evidence_session=None, receipt_path=None, receipt_sha256=None,
        receipt_run_id=None, orchestration_run_id=None, version_labels=labels)
    # If the session's head is gone from its mutable path, the reader now holds the latest head, so the price day
    # is that head's (never the session's recorded day for other bytes); the session's asof stays the analysis day.
    _settle_published_pin(context, sources, follow_expected_asof=False)
    return context


def _settle_published_pin(context, sources, *, follow_expected_asof):
    """Validate the pin; after each re-pin of the reader (a newer head, labelled in ``version_labels``) re-read the
    price day of the head now pinned, refresh the context from the reader and validate once more.  A head that
    keeps changing under three consecutive pins fails closed (``published_head_unstable_during_pin``)."""
    proof = validate_published_context_pin(context)
    rounds = 0
    while proof.get("repinned"):
        rounds += 1
        if rounds > 2:
            raise C0IntegrityError("published_head_unstable_during_pin")
        latest, metrics = _pinned_latest_price(sources)
        if latest is None:
            raise ValueError("published_price_date_unavailable_or_invalid")
        snapshot = sources.snapshot_descriptor()
        context.update(data_date=latest, pin_query_metrics=metrics, snapshot_descriptor=snapshot,
                       input_sources=snapshot, fingerprint=sources.fingerprint, gaps=tuple(sources.diagnostics))
        if follow_expected_asof:
            context["expected_asof"] = latest
        proof = validate_published_context_pin(context)
    return proof


def validate_published_context_pin(context):
    """Validate the pinned descriptor and small head/manifest files only.

    Version-type differences never raise (plan 2.4): they are the proof's ``version_labels`` in
    ``context["pin_validation"]`` and are appended to ``context["version_labels"]``.  When the reader re-pinned a
    newer head or verified its own catalog instead of the supplied one (``descriptor_refreshed``), the context's
    descriptor, fingerprint and gaps are refreshed from the reader, so the context names what is read.
    Errors keep distinct classes (message ``published_snapshot_pin_invalid`` for both, original error as
    ``__cause__`` and ``.reason``): ``C0IntegrityError`` for tamper, torn reads and invalid pins, and
    ``PublishedPinUnavailableError`` for a pin file that cannot be read.  Both are ValueErrors.
    """
    if not isinstance(context, Mapping) or context.get("source_mode") != "published":
        raise ValueError("published_context_required")
    snapshot = context.get("snapshot_descriptor")
    sources = context.get("sources")
    if not isinstance(snapshot, Mapping) or not isinstance(sources, PublishedSources):
        raise ValueError("published_context_pin_invalid")
    try:
        proof = sources.validate_pinned_context(snapshot)
    except OSError as exc:
        raise PublishedPinUnavailableError(
            "published_snapshot_pin_invalid", reason=f"{type(exc).__name__}: {exc}") from exc
    except (ValueError, TypeError, KeyError) as exc:
        raise C0IntegrityError(
            "published_snapshot_pin_invalid", reason=f"{type(exc).__name__}: {exc}") from exc
    if isinstance(context, dict):
        if proof.get("descriptor_refreshed"):
            refreshed = sources.snapshot_descriptor()
            context.update(snapshot_descriptor=refreshed, input_sources=refreshed,
                           fingerprint=sources.fingerprint, gaps=tuple(sources.diagnostics))
        context["pin_validation"] = proof
        labels = list(context.get("version_labels") or ())
        for label in proof.get("version_labels") or ():
            if label not in labels:
                labels.append(label)
        context["version_labels"] = labels
    return proof


def _published_pin_proof(context, sources, fingerprint):
    proof = context.get('pin_validation') if isinstance(context, Mapping) else None
    if (isinstance(proof, Mapping) and proof.get('status') == 'verified'
            and proof.get('snapshot_fingerprint') == fingerprint):
        return proof
    return validate_published_context_pin(context) if isinstance(sources, PublishedSources) else None


def pin_published_sources_context(expected_asof=None, source_specs=None, row_cache_dir=None):
    specs = _specs() if source_specs is None else source_specs
    sources = (PublishedSources(specs) if row_cache_dir is None
               else PublishedSources(specs, row_cache_dir=row_cache_dir))
    _ensure_consumer_row_cache(sources)
    latest, price_query_metrics = _pinned_latest_price(sources)
    expected = _date(expected_asof) if expected_asof is not None else latest
    if expected is None:
        raise ValueError("published_price_date_unavailable_or_invalid")
    # Pin the whole available catalog so optional reference inputs use this head too.
    snapshot = sources.snapshot_descriptor()
    context = dict(source_mode="published", sources=sources, expected_asof=expected,
                data_date=latest, fingerprint=sources.fingerprint,
                input_sources=snapshot, snapshot_descriptor=snapshot, gaps=tuple(sources.diagnostics),
                pin_query_metrics=price_query_metrics,
                evidence_session=None, receipt_path=None, receipt_sha256=None,
                receipt_run_id=None, orchestration_run_id=None, version_labels=[])
    # A head rewritten between this pin and its validation is re-pinned (labelled); the price day then follows the
    # latest head, and so does the expected asof unless the caller named one.
    _settle_published_pin(context, sources, follow_expected_asof=expected_asof is None)
    if expected_asof is not None and context["data_date"] is not None and context["data_date"] != expected:
        context["version_labels"].append({"code": "requested_asof_differs", "recorded": expected,
                                          "actual": context["data_date"],
                                          "detail": {"source": "published_price_latest_date"}})
    return context


def _pinned_latest_price(sources):
    """The latest published price day of the pinned reader and its query metrics (verified summary, sealed index
    statistics, else a verifying date scan); the price payload objects are released afterwards.  A projection
    integrity failure is a reason to rescan the verified payload (``fallback_reason``), never to trust it."""
    from stock_query_projection import ProjectionIntegrityError, UnsupportedProjectionError
    try:
        latest, price_query_metrics = _latest_published_price_date(sources)
    except (UnsupportedProjectionError, ProjectionIntegrityError) as exc:
        latest = sources.latest_artifact_date("finmind_price_adj")
        price_query_metrics = {
            "mode": "published_fullscan_fallback",
            "fallback_reason": f"{type(exc).__name__}: {exc}",
            "source_bytes_read": None,
            "metrics_complete": False,
        }
    sources.release("finmind_price_adj")
    return latest, price_query_metrics


def _trusted_price_summary_date(pinned_items):
    dates = []
    missing_summary = False
    for pinned in pinned_items:
        entry = pinned.get('metadata')
        if not isinstance(entry, Mapping):
            missing_summary = True
            continue
        nested = entry.get('metadata')
        nested = nested if isinstance(nested, Mapping) else {}
        maximum_values = [container['actual_max_date'] for container in (entry, nested)
                          if 'actual_max_date' in container]
        if not maximum_values:
            missing_summary = True
            continue
        # Two spellings of one day are not a conflict; a different day (or a non-date) is.
        keys = [_date(value) or ('not_a_date', repr(value)) for value in maximum_values]
        if any(key != keys[0] for key in keys[1:]):
            raise ValueError('published_price_summary_conflict:actual_max_date')
        maximum = _date(maximum_values[0])
        if maximum is None:
            raise ValueError('published_price_summary_maximum_invalid')
        dates.append(maximum)
    return (max(dates) if dates else None), missing_summary

_COMPLETION_PROOF_LIMIT = 32 * 1024
_COMPLETION_CONTEXT_KEYS = (
    'status', 'reason', 'requested_from_date', 'expected_asof', 'coverage',
    'last_attempt', 'partial_recovery', 'source_receipts', 'snapshot_observation',
    'no_events', 'snapshot_complete', 'observed_asof', 'history_unavailable',
    'error', 'errors', 'gaps', 'warnings', 'pending', 'not_yet_published',
    'refresh_error', 'stale', 'unknown', 'awaiting_source_release',
    'pending_dates', 'pending_release_dates', 'unresolved_prior_gaps',
    'stale_snapshot', 'prior_gaps', 'unresolved_prior_reasons', 'missing_dates',
    'calendar_gaps', 'pending_release', 'diagnostics', 'acquisition_status',
    'current_complete', 'complete_for_publish',
)
def _small_completion_entry(entry):
    """Bounded copy of an entry's completion context for an output summary (``_bounded_producer_evidence``).

    Never used for a proof: private completion proofs hold ``_completion_entry_view`` references (C6)."""
    budget = _COMPLETION_PROOF_LIMIT

    def copy(value, depth=0):
        nonlocal budget
        budget -= 8
        if budget < 0 or depth > 10:
            raise ValueError('completion_proof_too_large')
        if isinstance(value, Mapping):
            if len(value) > 64:
                raise ValueError('completion_proof_too_large')
            return {key: copy(child, depth + 1) for key, child in value.items()
                    if key not in ('source_states', 'partition_fingerprints')}
        if isinstance(value, (list, tuple)):
            if len(value) > 16:
                raise ValueError('completion_proof_too_large')
            return [copy(child, depth + 1) for child in value]
        if isinstance(value, str):
            budget -= len(value.encode('utf-8'))
            if budget < 0:
                raise ValueError('completion_proof_too_large')
        return value

    metadata = entry.get('metadata')
    metadata = metadata if isinstance(metadata, Mapping) else {}
    selected = entry.get('selected_ref')
    compact = {key: entry[key] for key in (
        'route_id', 'status', 'path', 'sha256', 'size', 'record_count',
        *_COMPLETION_CONTEXT_KEYS) if key in entry and key != 'metadata'}
    if isinstance(selected, Mapping):
        compact['selected_ref'] = {key: selected[key] for key in ('path', 'sha256', 'size')
                                   if key in selected}
    compact['metadata'] = {key: metadata[key] for key in _COMPLETION_CONTEXT_KEYS if key in metadata}
    try:
        return copy(compact)
    except ValueError:
        return None


def _completion_entry_view(entry):
    """The pinned entry a private completion proof keeps, by reference (no copy, no size budget; C6/D11).

    Entry-level values are the keys ``_small_completion_entry`` selects (the read-path-only ``_projection_*`` keys of
    a row-cache or query read are left out, so every read path keeps the same proof) and ``metadata`` is the pinned
    metadata mapping itself.  Whatever the number of producer receipts or coverage keys, the proof re-scoped in
    ``_route_window`` sees exactly the evidence the full read proved (the entry PublishedSources handed out: v2
    evidence resolved, last_attempt restored).  It is held in memory only: ``_public_projection_metadata``
    drops every ``_completion_*`` key, so nothing serializes it.
    """
    entry = entry if isinstance(entry, Mapping) else {}
    metadata = entry.get('metadata')
    selected = entry.get('selected_ref')
    view = {key: entry[key] for key in (
        'route_id', 'status', 'path', 'sha256', 'size', 'record_count',
        *_COMPLETION_CONTEXT_KEYS) if key in entry and key != 'metadata'}
    if isinstance(selected, Mapping):
        view['selected_ref'] = {key: selected[key] for key in ('path', 'sha256', 'size')
                                if key in selected}
    view['metadata'] = metadata if isinstance(metadata, Mapping) else {}
    return view


def _completion_query_empty(scope, start, end, date_field):
    if not scope['complete'] or scope['date_field'] != date_field:
        return False
    if scope['dates'] is not None:
        return not any((not start or day >= start) and (not end or day <= end)
                       for day in scope['dates'])
    return bool((start and scope['maximum'] and start > scope['maximum'])
                or (end and scope['minimum'] and end < scope['minimum'])
                or (start == scope['start'] and end == scope['end'] and scope['query_empty']))


_COMPLETION_DATE_SCOPE_ROUTE_IDS = ('official_market_daily', 'finmind_government_bank')


def _cached_global_row_scope(metadata, route, date_field, start, end):
    """The all-stock scope a per-stock row cache recorded for this artifact, as a full read computes it in ``_select``.

    Returns ``(row_count, invalid_scope, distinct_dates_or_None, minimum, maximum, rows_in_window)`` or None when the
    rows did not come from such a cache, the cache predates the record, or it was recorded for another date field
    (the scope then stays the per-stock one, which never proves an all-stock empty window).
    """
    scope = metadata.get('_projection_global_scope') if isinstance(metadata, Mapping) else None
    date_scope = route in _COMPLETION_DATE_SCOPE_ROUTE_IDS
    if (not isinstance(scope, Mapping) or scope.get('schema') != 'published-global-row-scope-v1'
            or any(type(scope.get(key)) is not int or scope[key] < 0
                   for key in ('rows', 'non_object_rows', 'invalid_date_rows'))
            or scope['non_object_rows'] + scope['invalid_date_rows'] > scope['rows']):
        return None
    dates = scope.get('dates')
    if date_scope:
        if (scope.get('date_field') != date_field or not isinstance(dates, list)
                or any(_date(day) is None for day in dates)):
            return None
        dates = [_date(day) for day in dates]
        if dates != sorted(set(dates)):
            return None
    else:
        dates = []
    in_window = sum(1 for day in dates if (not start or day >= start) and (not end or day <= end))
    invalid = scope['non_object_rows'] > 0 or (date_scope and scope['invalid_date_rows'] > 0)
    # The complete date set (C14): a re-scoped window proof is decided on every recorded date, never on a summary.
    return (scope['rows'], invalid, set(dates),
            dates[0] if dates else None, dates[-1] if dates else None, in_window)


def _completion_result(proofs, records, gaps, *, source_errors=False):
    fields = published_completion_fields(proofs)
    scoped = fields.get('completion_evidence_valid') is True
    status = ('partial' if gaps else 'available') if records else 'missing'
    if source_errors:
        fields.update(source_available_complete=False, completion_evidence_valid=False,
                      requested_scope_complete=False, due_scope_complete=False,
                      data_complete=False, history_complete=False, model_usable=False)
        if records:
            status = 'partial'
    if scoped and not source_errors and not gaps:
        status = 'partial' if any(proof['status'] == 'partial' for proof in proofs) else proofs[0]['status']
    if records and (fields.get('completion_state') == 'conflict'
                    or any(proof['status'] == 'partial' for proof in proofs)):
        status = 'partial'
    if not records:
        fields['usable'] = False
        fields['model_usable'] = False
        if fields.get('completion_state') == 'unknown':
            fields.update(completion_state='unknown_empty', reason='unknown_empty',
                          data_complete=False, history_complete=False)
        if not scoped or source_errors or gaps:
            gaps.append('no_matching_published_rows')
    elif 'usable' not in fields:
        fields['usable'] = True
    return dict(fields, status=status)


def _public_projection_metadata(item):
    return {key: value for key, value in item.items()
            if key != 'records' and not key.startswith('_completion_')}


def _bounded_producer_evidence(entry, *, source_id=None, snapshot_id=None, root=None, ref=None):
    """Small status summary with exact artifact identity, not a producer receipt."""
    budget = 4096

    def scalar(value):
        nonlocal budget
        if value is None or isinstance(value, (bool, int, float)):
            return value
        if not isinstance(value, str):
            return None
        raw = value[:256].encode("utf-8")[:min(256, max(0, budget))]
        budget -= len(raw)
        return raw.decode("utf-8", errors="ignore")

    def summary(container):
        if not isinstance(container, Mapping):
            return {}
        result = {key: scalar(container[key]) for key in (
            "status", "reason", "acquisition_status", "expected_asof",
            "requested_from_date", "published_at", "retrieved_at", "source_run_id",
        ) if key in container}
        for key in ("gaps", "warnings"):
            values = container.get(key)
            if isinstance(values, (list, tuple)):
                result[key] = [({name: scalar(value[name]) for name in
                    ("route_id", "status", "reason", "date", "code") if name in value}
                    if isinstance(value, Mapping) else scalar(value)) for value in values[:4]]
                result[key + "_count"] = len(values)
                result[key + "_truncated"] = len(values) > 4
        return result

    compact = dict(route_id=entry.get("route_id", _ORDER21_ROUTE_ID), status=entry.get("status"),
                   metadata=summary(entry.get("metadata")))
    compact.update(summary(entry))
    for container, target in ((entry, compact), (entry.get("metadata"), compact["metadata"])):
        if isinstance(container, Mapping) and isinstance(container.get("last_attempt"), Mapping):
            # The last attempt as recorded (v2 heads: same_as_metadata restored; see resolved_last_attempt).
            target["last_attempt"] = summary(resolved_last_attempt(container))
    bounded = _small_completion_entry(compact) or {"route_id": compact["route_id"], "status": compact["status"]}
    for container, target in ((compact, bounded), (compact["metadata"], bounded.setdefault("metadata", {}))):
        target.update({key: container[key] for key in
            ("gaps_count", "gaps_truncated", "warnings_count", "warnings_truncated") if key in container})
    identity = ref if isinstance(ref, Mapping) else entry
    bounded.update(source_id=source_id, snapshot_id=snapshot_id, summary_only=True,
        selected_ref=dict(root=str(root) if root is not None else None,
            path=identity.get("ref", identity.get("path")), sha256=identity.get("sha256"), size=identity.get("size")))
    return bounded


def _select(sources, route, stock_id, start, end, *, stock_field="stock_id", date_field="date",
            artifact_rows=None):
    candidates, gaps = [], []
    publication_metadata = []
    completion_proofs, completion_inputs, empty_sources = [], [], []
    date_scope_proof = route in ('official_market_daily', 'finmind_government_bank')
    seen = set()
    order21_formats, order21_seen = {}, {}
    order21_blocked_v2_stockdays = set()
    artifacts = artifact_rows if artifact_rows is not None else (
        sources.iter_stock_artifact_rows(route, stock_id)
        if isinstance(sources, PublishedSources) and
        callable(getattr(sources, 'iter_stock_artifact_rows', None))
        else sources.iter_artifact_rows(route))
    for priority, (artifact, rows) in enumerate(artifacts):
        entry = dict(getattr(artifact, "metadata", None) or {})
        entry.setdefault('status', artifact.status)
        details = dict(entry.get("metadata") or {})
        details.update({key: entry[key] for key in (
            "actual_min_date", "actual_max_date", "record_count", "status",
            "requested_from_date", "effective_from_date", "expected_asof",
            "coverage", "gaps", "acquisition_status", "collected_at",
            "retrieved_at", "published_at", "source_timestamp",
        ) if key in entry})
        details["status"] = artifact.status
        publication_metadata.append(details)
        artifact_metadata = getattr(artifact, "metadata", None)
        if isinstance(artifact_metadata, Mapping) and artifact_metadata.get(
                "_projection_missing_date_rows", 0):
            gaps.append("invalid_date")
        if (route == _ORDER21_ROUTE_ID and isinstance(artifact_metadata, Mapping)
                and artifact_metadata.get("_projection_missing_stock_rows", 0)):
            gaps.append("trading_daily_report_identity_missing")
        if details.get("gaps"):
            gaps.append(f"{artifact.source_id}:publication_gaps")
        if str(details.get("acquisition_status", "")).upper() == "WAITING_QUOTA":
            gaps.append(f"{artifact.source_id}:WAITING_QUOTA")
        valid = []
        raw_count = 0
        scope_count, invalid_scope = 0, False
        scope_dates = set()
        scope_minimum = scope_maximum = None
        for row in rows:
            raw_count += 1
            if not isinstance(row, dict):
                gaps.append("non_object_row")
                invalid_scope = True
                continue
            day = _date(row.get(date_field)) if date_scope_proof else None
            if date_scope_proof and day is None:
                invalid_scope = True
            elif date_scope_proof:
                scope_minimum = min(scope_minimum, day) if scope_minimum else day
                scope_maximum = max(scope_maximum, day) if scope_maximum else day
                scope_dates.add(day)            # the complete date set, never summarized (C14)
                if (not start or day >= start) and (not end or day <= end):
                    scope_count += 1
            if route == _ORDER21_ROUTE_ID:
                try:
                    row_format = order21_row_format(row, metadata=artifact_metadata)
                except ValueError as error:
                    gaps.append(str(error) if 'summary_invalid' in str(error)
                                else "trading_daily_report_identity_missing")
                    if row.get('row_schema') == ORDER21_MINIMAL_SUMMARY_SCHEMA:
                        order21_blocked_v2_stockdays.add((str(row.get('stock_id', '')), _date(row.get('date'))))
                    continue
            if stock_id is not None and str(row.get(stock_field, row.get("stock_id", row.get("data_id", "")))) != str(stock_id):
                continue
            if not date_scope_proof:
                day = _date(row.get(date_field))
            if day is None and route != "finmind_stock_info":
                gaps.append("invalid_date")
                continue
            if day and ((start and day < start) or (end and day > end)):
                continue
            if route in _FINANCE and not row.get("type"):
                gaps.append("financial_type_missing")
                continue
            if route == "finmind_price_adj":
                value = row.get("close")
                try:
                    usable = not isinstance(value, bool) and math.isfinite(float(value)) and float(value) > 0
                except (TypeError, ValueError):
                    usable = False
                if not usable:
                    gaps.append("price_close_invalid")
                    continue
            valid.append(dict(row))
            if route == _ORDER21_ROUTE_ID:
                # stock-day by calendar-day VALUE: an ISO detail row and a YYYYMMDD summary row of one day are
                # the same stock-day (F2 of the date review; rows themselves are never rewritten)
                order21_formats.setdefault((str(row['stock_id']), _date(row['date'])), set()).add(row_format)
        verified = artifact_rows is None and isinstance(sources, PublishedSources)
        cached_scope = _cached_global_row_scope(artifact_metadata, route, date_field, start, end)
        if cached_scope is not None:
            # Stock rows from a per-stock row cache: the all-stock scope it recorded while streaming the
            # whole payload is exactly what a full read of the payload counts here.
            raw_count, invalid_scope, scope_dates, scope_minimum, scope_maximum, scope_count = cached_scope
        global_rows = ((artifact_rows is None and (stock_id is None
            or not isinstance(sources, PublishedSources)
            or getattr(sources, 'row_cache_dir', None) is None))
            or (verified and type(entry.get('record_count')) is int
                and entry['record_count'] == raw_count))
        global_rows = global_rows or cached_scope is not None
        scope = dict(complete=global_rows and not invalid_scope and (date_scope_proof or raw_count == 0),
            date_field=date_field,
            dates=tuple(sorted(scope_dates)) if scope_dates is not None else None,
            minimum=scope_minimum, maximum=scope_maximum, start=start, end=end,
            query_empty=scope_count == 0)
        global_empty = verified and global_rows and raw_count == 0
        proof = published_completion(route, entry, verified_object=verified,
            query_empty=_completion_query_empty(scope, start, end, date_field),
            global_empty=global_empty, start_date=start, end_date=end)
        proof['source_id'] = artifact.source_id
        if not valid and proof['completion_state'] == 'unknown' and not proof['completion_evidence_valid']:
            proof.update(status='missing', completion_state='unknown_empty', reason='unknown_empty',
                         usable=False, model_usable=False, data_complete=False, history_complete=False)
        completion_proofs.append(proof)
        # The private proof keeps references to the very entry this full read proved (C6/D11: never a copy and
        # never a size-bounded summary, so neither a proof budget nor a proof count applies), so the proof
        # re-scoped in _route_window has the same evidence whatever the number of producer receipts.
        private_proof = dict(route_id=route, entry=_completion_entry_view(entry), verified_object=verified,
            global_empty=global_empty, scope=scope, source_id=artifact.source_id, priority=priority)
        completion_inputs.append(private_proof)
        empty_sources.append(dict(source_id=artifact.source_id, snapshot_id=artifact.snapshot_id,
                                  content_sha256=artifact.content_sha256, status=artifact.status))
        if valid:
            candidates.append((priority, artifact, valid, scope))
    if not completion_proofs:
        scope = dict(complete=False, date_field=date_field, dates=(), minimum=None,
                     maximum=None, start=start, end=end, query_empty=False)
        completion_proofs.append(published_completion(route, {}, start_date=start, end_date=end))
        completion_inputs.append(dict(route_id=route, entry={}, verified_object=False,
                                      global_empty=False, scope=scope))
    if artifact_rows is None:
        sources.release(route)
    if route == "finmind_price_adj" and candidates:
        # A source's adjusted history is indivisible across adjustment vintages.
        def vintage_key(item):
            artifact_metadata = getattr(item[1], "metadata", None)
            statistics = (artifact_metadata.get("_projection_stock_statistics")
                          if isinstance(artifact_metadata, Mapping) else {}) or {}
            row_dates = [_date(row.get(date_field)) for row in item[2]
                         if _date(row.get(date_field))]
            return (_canonical(statistics.get("maximum_date")) or max(row_dates),
                    statistics.get("distinct_observation_dates") or len(set(row_dates)),
                    -item[0])
        candidates = [max(candidates, key=lambda item: vintage_key(item))]
        priority, selected_artifact, _, selected_scope = candidates[0]
        completion_proofs = [completion_proofs[priority]]
        publication_metadata = [publication_metadata[priority]]
        selected_entry = dict(getattr(selected_artifact, 'metadata', None) or {})
        selected_entry.setdefault('status', selected_artifact.status)
        verified = artifact_rows is None and isinstance(sources, PublishedSources)
        private_proof = dict(route_id=route, entry=_completion_entry_view(selected_entry), verified_object=verified,
            global_empty=False, scope=selected_scope, source_id=selected_artifact.source_id)
        completion_inputs = [private_proof]
    records, used = [], []
    mixed_stockdays = {stockday for stockday, formats in order21_formats.items() if len(formats) > 1}
    if mixed_stockdays:
        gaps.append('order21_mixed_detail_summary:' + ','.join('|'.join(day) for day in sorted(mixed_stockdays)))
    for _, artifact, valid, _ in candidates:
        used.append(dict(source_id=artifact.source_id, snapshot_id=artifact.snapshot_id,
                         content_sha256=artifact.content_sha256, status=artifact.status))
        if str(artifact.status).lower() not in ("success", "complete", "available", "committed", "200"):
            gaps.append(f"{artifact.source_id}:{artifact.status}")
        for row in valid:
            if route == _ORDER21_ROUTE_ID and (str(row['stock_id']), _date(row['date'])) in mixed_stockdays:
                continue
            fields = (stock_field, date_field, "type") if route in _FINANCE else (stock_field, date_field)
            if stock_field is None:
                fields = tuple(sorted(row))
            if route in ("finmind_dividend", "finmind_dividend_result"):
                fields = ("stock_id", "date", "type", "year", "CashExDividendTradingDate",
                          "StockExDividendTradingDate", "cash_ex_dividend_trading_date",
                          "stock_ex_dividend_trading_date", "stock_and_cache_dividend")
            if route == "finmind_institutional":
                fields = ("stock_id", "date", "name")
            if route == "finmind_trading_daily_report":
                fields = ("stock_id", "date", "securities_trader_id")
                if not order21_summary_hint(row):
                    fields += ("price",)
            key = json.dumps([_dedup_value(field, row.get(field), date_field) for field in fields],
                             sort_keys=True, ensure_ascii=False)
            if route == _ORDER21_ROUTE_ID:
                # Same natural key: identical content by calendar-day VALUE is one row, not a conflict
                # (two spellings of one day; date directive 2026-10-06).
                if (key in order21_seen and _row_by_date_value(order21_seen[key], date_field)
                        != _row_by_date_value(row, date_field)):
                    gaps.append('trading_daily_report_natural_key_conflict')
                    if row.get('row_schema') == ORDER21_MINIMAL_SUMMARY_SCHEMA:
                        order21_blocked_v2_stockdays.add((str(row['stock_id']), _date(row['date'])))
                else:
                    order21_seen[key] = row
            if key not in seen:
                seen.add(key)
                records.append(row)
    if order21_blocked_v2_stockdays:
        records = [row for row in records
                   if (str(row['stock_id']), _date(row['date'])) not in order21_blocked_v2_stockdays]
    source_errors = any(state.get('error') or route in state['errors']
                        for state in getattr(sources, '_sources', ()))
    completion = _completion_result(completion_proofs, records, gaps, source_errors=source_errors)
    unknown = sum(not any(row.get(key) for key in ("available_at", "published_at", "source_close_at")) for row in records)
    dates = sorted({day for row in records if (day := _date(row.get(date_field)))})
    acquisition = list(dict.fromkeys(str(item["acquisition_status"])
        for item in publication_metadata if item.get("acquisition_status")))
    return dict(completion, records=tuple(records), sources=tuple(used or empty_sources),
                gaps=tuple(dict.fromkeys(gaps)), publication_metadata=publication_metadata,
                _completion_inputs=tuple(completion_inputs), _completion_source_errors=source_errors,
                acquisition_status=acquisition[0] if len(acquisition) == 1 else (acquisition or "unknown"),
                coverage=[item["coverage"] for item in publication_metadata if "coverage" in item] or "unknown",
                requested_start_date=start, requested_end_date=end,
                first_date=dates[0] if dates else None, latest_date=dates[-1] if dates else None,
                distinct_dates=len(dates), stock_day_coverage_status="unknown",
                unknown_publication_time_rows=unknown, stock_field=stock_field,
                date_field=date_field)


def _route_window(item, request):
    start, end = request["start_date"], request["end_date"]
    before_count = request["before_count"]
    date_field = request["field_map"]["date_field"]
    before_dates = sorted({
        _date(row.get(date_field)) for row in item["records"]
        if start and (day := _date(row.get(date_field))) is not None and day < start
    })[-before_count:] if before_count else []
    carry = set(before_dates)
    records = tuple(row for row in item["records"] if (
        (day := _date(row.get(date_field))) is not None
        and (not start or day >= start or day in carry)
        and (not end or day <= end)))
    dates = sorted({day for row in records if (day := _date(row.get(date_field)))})
    in_window_dates = sorted({day for row in records
        if (day := _date(row.get(date_field))) and (not start or day >= start)})
    gaps = [gap for gap in item['gaps'] if gap != 'no_matching_published_rows']
    allowed_request_keys = {'start_date', 'end_date', 'before_count', 'field_map',
        'all_symbols', 'metadata', 'unit', 'formula_lookback_count',
        'request_plan_digest'}
    request_metadata = request.get('metadata')
    represented_scope = (not set(request) - allowed_request_keys
        and (request_metadata is None or (isinstance(request_metadata, Mapping)
            and not set(request_metadata) - {'request_plan_digest', 'field_map', 'all_symbols'})))
    proofs = [published_completion(proof['route_id'], proof['entry'],
        verified_object=proof['verified_object'] and represented_scope
            and proof['scope']['date_field'] == date_field,
        query_empty=_completion_query_empty(proof['scope'], start, end, date_field),
        global_empty=proof['global_empty'], start_date=start, end_date=end,
        before_count=before_count, formula_lookback_count=request.get('formula_lookback_count'))
        for proof in item.get('_completion_inputs', ())]
    for interpreted, private in zip(proofs, item.get('_completion_inputs', ())):
        interpreted['source_id'] = private.get('source_id')
    completion = _completion_result(proofs, records, gaps,
        source_errors=item.get('_completion_source_errors', False))
    unknown = sum(not any(row.get(key) for key in ("available_at", "published_at", "source_close_at"))
                  for row in records)
    result = dict(item, records=records, requested_start_date=start, requested_end_date=end,
        first_date=dates[0] if dates else None, latest_date=dates[-1] if dates else None,
        distinct_dates=len(dates), before_count=before_count,
        gaps=tuple(dict.fromkeys(gaps)), unknown_publication_time_rows=unknown,
        carry_in_dates=before_dates, in_window_distinct_dates=len(in_window_dates),
        unit=request.get("unit"),
        formula_lookback_count=request.get("formula_lookback_count"))
    for key in PUBLISHED_COMPLETION_FIELDS:
        result.pop(key, None)
    result.update(completion)
    return result


def _query_row_matches(wrapper, predicate):
    stock, day = wrapper.get("__stock"), wrapper.get("__date")
    requested = predicate.get("stock_ids")
    if requested is not None and stock not in requested:
        return False
    start, end = predicate.get("start_date"), predicate.get("end_date")
    if isinstance(start, Mapping):
        start = start.get(stock)
    if isinstance(end, Mapping):
        end = end.get(stock)
    if start is None and end is None and not predicate.get("before_count"):
        return True
    if day is None:
        return False
    if (start is None or day >= start) and (end is None or day <= end):
        return True
    carry = predicate.get("carry_dates_missing_stock", []) if stock is None else (
        predicate.get("carry_dates_by_stock", {}).get(stock, []))
    return day in carry


def _unique_json_pairs(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise ValueError("projection_shard_duplicate_json_key")
        result[key] = value
    return result


def _query_rows(result):
    from stock_query_projection import ProjectionStreamingRequiredError
    before_rows = result.metrics.get("rows_scanned", 0)
    before_bytes = result.metrics.get("projection_bytes_read", 0)
    try:
        rows = list(result.iter_rows())
        return rows, {"consumer_rows_scanned": result.metrics.get("rows_scanned", 0) - before_rows,
            "consumer_projection_bytes_read": result.metrics.get("projection_bytes_read", 0) - before_bytes}
    except ProjectionStreamingRequiredError:
        rows_by_ordinal = []
        rows_scanned, bytes_read = 0, 0
        for shard in result.iter_verified_shards():
            digest = hashlib.sha256()
            size = 0
            shard_rows = 0
            with Path(shard["path"]).open("rb") as handle:
                for line in handle:
                    digest.update(line)
                    size += len(line)
                    bytes_read += len(line)
                    wrapper = json.loads(line.decode("utf-8"),
                        object_pairs_hook=lambda pairs: _unique_json_pairs(pairs),
                        parse_constant=lambda value: (_ for _ in ()).throw(ValueError("nonstandard JSON number")))
                    if not isinstance(wrapper, dict) or not isinstance(wrapper.get("row"), dict):
                        raise ValueError("published_projection_row_shape_invalid")
                    sequence = wrapper.get("__sequence")
                    if isinstance(sequence, bool) or not isinstance(sequence, int):
                        raise ValueError("published_projection_sequence_invalid")
                    shard_rows += 1
                    rows_scanned += 1
                    if _query_row_matches(wrapper, shard["predicate"]):
                        rows_by_ordinal.append((sequence, wrapper["row"]))
            if (digest.hexdigest() != shard["sha256"] or size != shard["size_bytes"]
                    or shard_rows != shard["rows"]):
                from stock_query_projection import ProjectionIntegrityError
                raise ProjectionIntegrityError("projection_shard_changed_during_adapter_read")
        result.record_consumer_scan(rows_scanned=rows_scanned, projection_bytes_read=bytes_read)
        rows_by_ordinal.sort(key=lambda item: item[0])
        return [row for _, row in rows_by_ordinal], {
            "consumer_rows_scanned": rows_scanned,
            "consumer_projection_bytes_read": bytes_read,
            "streamed_verified_shards": True,
        }


def _ensure_consumer_row_cache(sources):
    configured = getattr(sources, 'row_cache_dir', None)
    if configured is None:
        return
    cache_dir = Path(configured).resolve()
    for state in getattr(sources, '_sources', ()):
        root = Path(state['root']).resolve()
        if cache_dir == root or root in cache_dir.parents:
            raise ValueError('consumer_row_cache_inside_published_source')


def _consumer_spool_root(sources):
    cache_dir = getattr(sources, 'row_cache_dir', None)
    if cache_dir is None:
        from path_contract import CONTRACT
        cache_dir = CONTRACT.single('cache') / 'published_rows'
    root = (Path(cache_dir) / 'query_spool').resolve()
    for state in getattr(sources, '_sources', ()):
        source_root = Path(state['root']).resolve()
        if root == source_root or source_root in root.parents:
            raise ValueError('consumer_spool_root_inside_published_source')
    return root


def _cached_stock_rows(sources, route, stock_id, pin_fingerprint, *,
                       stock_field='stock_id', date_field='date'):
    if (stock_id is None or pin_fingerprint is None
            or not callable(getattr(sources, 'cached_stock_artifact_rows', None))):
        return None
    return sources.cached_stock_artifact_rows(
        route, str(stock_id), pinned_snapshot_fingerprint=pin_fingerprint,
        stock_field=stock_field, date_field=date_field,
    )


def _row_cache_metrics(cached, pin_proof=None, fallback_reason=None):
    metrics = _query_metrics([], mode='published_pinned_stock_row_cache',
                             fallback_reason=fallback_reason)
    metrics.update(
        source_bytes_read=0,
        projection_bytes_read=0,
        row_cache_bytes_read=cached['metrics'].get('row_cache_bytes_read', 0),
        pin_bytes_read=(pin_proof or {}).get('pin_bytes_read', 0),
        rows_scanned=cached['metrics'].get('rows_loaded', 0),
        shards_read=0,
        cache_hit=True,
        metrics_complete=True,
        cache_entries_read=cached['metrics'].get('cache_entries_read', 0),
    )
    return metrics


def _prepared_stock_route_item(sources, route, stock_id, *, start, end,
                               stock_field, date_field, pin_fingerprint,
                               pin_proof=None, request=None, fallback_reason=None):
    if isinstance(sources, PublishedSources):
        _ensure_consumer_row_cache(sources)

    def select_from_cache(cached):
        item = _select(sources, route, stock_id, None if request else start,
                       None if request else end, stock_field=stock_field,
                       date_field=date_field, artifact_rows=cached['artifacts'])
        if request:
            item = _route_window(item, request)
        item['query_metrics'] = _row_cache_metrics(cached, pin_proof, fallback_reason)
        return item

    cached = _cached_stock_rows(sources, route, stock_id, pin_fingerprint,
                                stock_field=stock_field, date_field=date_field)
    if cached is not None:
        return select_from_cache(cached)

    lock_factory = getattr(sources, 'stock_cache_lock', None)
    if callable(lock_factory) and stock_id is not None:
        prepared = None
        with lock_factory(route, stock_id,
                lock_timeout_seconds=_projection_lock_timeout_seconds(),
                on_lock_wait=_projection_lock_wait_callback(route)):
            cached = _cached_stock_rows(sources, route, stock_id, pin_fingerprint,
                                        stock_field=stock_field, date_field=date_field)
            if cached is not None:
                return select_from_cache(cached)
            prewarm = getattr(sources, 'prewarm_stock_rows', None)
            if callable(prewarm) and getattr(sources, 'row_cache_dir', None) is not None:
                prepared = prewarm([str(stock_id)], [route], single_stock_prepare=True,
                                   stock_field=stock_field, date_field=date_field)
                _raise_if_source_integrity_gap(sources, route)
                cached = _cached_stock_rows(sources, route, stock_id, pin_fingerprint,
                                            stock_field=stock_field, date_field=date_field)
                if cached is not None:
                    item = select_from_cache(cached)
                    metrics = item['query_metrics']
                    metrics.update(
                        mode='published_consumer_stock_prepared',
                        source_bytes_read=prepared.get('source_bytes_read'),
                        prepared_files=prepared.get('prepared_files', 0),
                        cache_hit=False,
                        metrics_complete=prepared.get('source_bytes_read') is not None,
                        fallback_reason=fallback_reason or 'consumer_stock_stream_preparation',
                    )
                    if prepared.get('gaps'):
                        item['gaps'] = tuple(dict.fromkeys((*item['gaps'], *prepared['gaps'])))
                        item['status'] = 'partial' if item['records'] else 'missing'
                    return item
            item = _select(sources, route, stock_id, None if request else start,
                           None if request else end, stock_field=stock_field,
                           date_field=date_field)
            _raise_if_source_integrity_gap(sources, route)
            if prepared and prepared.get('gaps'):
                item['gaps'] = tuple(dict.fromkeys((*item['gaps'], *prepared['gaps'])))
                item['status'] = 'partial' if item['records'] else 'missing'
    else:
        item = _select(sources, route, stock_id, None if request else start,
                       None if request else end, stock_field=stock_field,
                       date_field=date_field)
    _raise_if_source_integrity_gap(sources, route)
    if request:
        item = _route_window(item, request)
    item['query_metrics'] = _query_metrics([], mode='published_fullscan_fallback',
        fallback_reason=fallback_reason or 'consumer_stock_stream_preparation')
    item['query_metrics'].update(source_bytes_read=None, cache_hit=False, metrics_complete=False,
                                 pin_bytes_read=(pin_proof or {}).get('pin_bytes_read', 0))
    return item


def _raise_if_source_integrity_gap(sources, route):
    failures = getattr(sources, '_validation_failures', {})
    if any(failed for (source_id, failed_route), failed in failures.items()
           if failed_route == route):
        from stock_query_projection import ProjectionIntegrityError
        raise ProjectionIntegrityError('published_source_artifact_integrity_mismatch')


def _query_artifact_rows(sources, route, stock_id, request, *, allow_build=False):
    from stock_query_projection import UnsupportedProjectionError, query_published_rows
    if not callable(getattr(sources, "projection_artifacts", None)):
        raise UnsupportedProjectionError("pinned_source_descriptors_unavailable")
    lock_timeout_seconds = _projection_lock_timeout_seconds()
    descriptors = sources.projection_artifacts(route)
    artifacts, queries = [], []
    for pinned in descriptors:
        if pinned.get("source_kind") != "manifest_json":
            raise UnsupportedProjectionError("published_source_kind_not_projection_compatible")
        field_map = dict(request["field_map"])
        result = query_published_rows(
            pinned["root"], pinned["artifact_descriptor"], route,
            stock_ids=None if request.get("all_symbols") else [str(stock_id)],
            start_date=request["start_date"],
            end_date=request["end_date"], before_count=request["before_count"],
            source_identity=pinned["source_identity"], parent_identity=pinned["parent_identity"],
            allow_build=False, field_map=field_map,
            lock_timeout_seconds=lock_timeout_seconds,
            on_lock_wait=_projection_lock_wait_callback(route),
            consumer_temp_root=_consumer_spool_root(sources),
        )
        rows, consumer_metrics = _query_rows(result)
        entry = pinned["metadata"]
        full_stock_stats = [item for item in result.source_stock_statistics
            if str(item.get("stock_key")) == str(stock_id)]
        artifact_metadata = dict(entry, _projection_stock_statistics=(full_stock_stats[0]
            if full_stock_stats else {}),
            _projection_missing_date_rows=result.manifest.get("missing_date_rows", 0),
            _projection_missing_stock_rows=result.manifest.get("missing_stock_rows", 0))
        artifact = SimpleNamespace(metadata=artifact_metadata, status=entry.get("status", "unknown"),
            source_id=pinned["source_id"], snapshot_id=pinned["snapshot_id"],
            content_sha256=pinned["artifact_descriptor"]["sha256"])
        artifacts.append((artifact, rows))
        queries.append({"status": result.status, "cache_key": result.cache_key,
            "parent_identity": result.lineage["requested_parent_identity_sha256"],
            "source_identity": result.lineage["requested_source_identity_sha256"],
            "metrics": dict(result.metrics), "consumer_metrics": consumer_metrics,
            "source_stock_statistics": full_stock_stats,
            "query_stock_statistics": list(result.query_stock_statistics)})
    return artifacts, queries


def _query_metrics(queries, *, mode, fallback_reason=None):
    result = {
        "mode": mode,
        "fallback_reason": fallback_reason,
        "source_bytes_read": (sum(item["metrics"].get("source_bytes_read", 0) for item in queries)
                               if mode != "published_fullscan_fallback" else None),
        "projection_bytes_read": (sum(item["metrics"].get("projection_bytes_read", 0) for item in queries)
                                   if mode != "published_fullscan_fallback" else None),
        "rows_scanned": (sum(item["metrics"].get("rows_scanned", 0) for item in queries)
                         if mode != "published_fullscan_fallback" else None),
        "shards_read": (sum(item["metrics"].get("shards_read", 0) for item in queries)
                        if mode != "published_fullscan_fallback" else None),
        "cache_hit": bool(queries) and all(item["metrics"].get("cache_hit") for item in queries),
        "metrics_complete": mode != "published_fullscan_fallback",
        "queries": queries,
    }
    return result


def _latest_published_price_date(sources):
    from stock_query_projection import (
        ProjectionIntegrityError, ProjectionUnavailableError, UnsupportedProjectionError, query_published_rows,
    )
    if not callable(getattr(sources, "projection_artifacts", None)):
        raise UnsupportedProjectionError("pinned_source_descriptors_unavailable")
    pinned_items = sources.projection_artifacts("finmind_price_adj")
    summary_date, missing_summary = _trusted_price_summary_date(pinned_items)
    if summary_date is not None and not missing_summary:
        return summary_date, {
            'mode': 'published_verified_artifact_summary',
            'fallback_reason': None,
            'source_bytes_read': 0,
            'projection_bytes_read': 0,
            'rows_scanned': 0,
            'shards_read': 0,
            'cache_hit': False,
            'metrics_complete': True,
            'queries': [],
        }
    lock_timeout_seconds = _projection_lock_timeout_seconds()
    queries, dates = [], []
    try:
        for pinned in pinned_items:
            if pinned.get("source_kind") != "manifest_json":
                raise UnsupportedProjectionError("published_source_kind_not_projection_compatible")
            result = query_published_rows(
                pinned["root"], pinned["artifact_descriptor"], "finmind_price_adj",
                stock_ids=None, start_date=None, end_date=None, before_count=0,
                source_identity=pinned["source_identity"], parent_identity=pinned["parent_identity"],
                allow_build=False, field_map={"stock_field": "stock_id", "date_field": "date"},
                lock_timeout_seconds=lock_timeout_seconds,
                on_lock_wait=_projection_lock_wait_callback("finmind_price_adj"),
            )
            dates.extend(day for item in result.source_stock_statistics
                         if (day := _date(item.get("maximum_date"))))
            queries.append({"status": result.status, "cache_key": result.cache_key,
                "metrics": dict(result.metrics), "parent_identity": result.lineage["requested_parent_identity_sha256"],
                "source_identity": result.lineage["requested_source_identity_sha256"]})
    except (ProjectionUnavailableError, UnsupportedProjectionError, ProjectionIntegrityError) as exc:
        # A projection integrity failure (plan 2.4, :1010) only disqualifies the derived index: the date is read
        # again by the verifying scan of the pinned payload, and the reason is recorded.
        latest = sources.latest_artifact_date("finmind_price_adj")
        return latest, {
            'mode': 'published_consumer_date_scan',
            'fallback_reason': f'{type(exc).__name__}: {exc}',
            'source_bytes_read': None,
            'projection_bytes_read': 0,
            'rows_scanned': 0,
            'shards_read': 0,
            'cache_hit': False,
            'metrics_complete': False,
            'queries': queries,
        }
    if dates:
        return max(dates), _query_metrics(queries, mode="published_index_statistics")
    latest = sources.latest_artifact_date("finmind_price_adj")
    return latest, {
        'mode': 'published_consumer_date_scan',
        'fallback_reason': 'completed_index_has_no_price_date',
        'source_bytes_read': None,
        'projection_bytes_read': 0,
        'rows_scanned': 0,
        'shards_read': 0,
        'cache_hit': False,
        'metrics_complete': False,
        'queries': queries,
    }

def main():
    parser = argparse.ArgumentParser(description="Pin one published single-stock session")
    parser.add_argument("--pin", action="store_true", required=True)
    parser.add_argument("--expected-asof", type=cd.cli_day, help="YYYYMMDD（相容 YYYY-MM-DD）")
    parser.add_argument("--descriptor-output", required=True)
    args = parser.parse_args()
    context = pin_published_sources_context(args.expected_asof)
    document = dict(schema="single-stock-published-session-v1",
        expected_asof=context["expected_asof"], data_date=context["data_date"],
        snapshot_descriptor=context["snapshot_descriptor"])
    if context.get("version_labels"):
        # plan 0.2: version differences seen while pinning reach the session file (only when present, so a
        # label-free session keeps its digest); import_published_sources_context carries them forward.
        document["version_labels"] = list(context["version_labels"])
    # The session digest is defined on the expanded descriptor, as before (D2: hashes unchanged).
    document["descriptor_sha256"] = _digest(document)
    # Stored compact (D4: no indentation) with the snapshot descriptor in its v2 storage form (D2: each pinned
    # manifest entry once, not twice).  Readers bind the file by the sha256 printed below, then expand and verify
    # both self-hashes (import_published_sources_context); 129.6 MB -> about 30.5 MB for the head of 2026-10-05.
    stored = dict(document, snapshot_descriptor=descriptor_storage(document["snapshot_descriptor"]))
    raw = (json.dumps(stored, ensure_ascii=False, sort_keys=True, separators=(",", ":")) + "\n").encode("utf-8")
    with Path(args.descriptor_output).open("xb") as handle:
        handle.write(raw)
    print(hashlib.sha256(raw).hexdigest())


if __name__ == "__main__":
    main()
