from __future__ import annotations

import hashlib
import json
import os
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch


SRC = Path(__file__).resolve().parents[1] / 'src'
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

import stock_data_sources as sources
import published_single_stock_projection as projection


class PublishedRowCacheTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        temp_root = Path(self.temp.name)
        self.root = temp_root / 'published'
        self.root.mkdir()
        self.cache = temp_root / 'cache'
        self.specs = [sources.SourceSpec('manifest_json', self.root, 'manifest.json', 'fixture')]

    def publish(self, raw=None, route_id='finmind_price_adj', **overrides):
        if raw is None:
            raw = json.dumps({'rows': [{'stock_id': '2330', 'date': '2099-01-01'}],
                'data': [{'stock_id': '2330', 'date': '2026-09-18', 'close': 100,
                          'name': '\u53f0\u7a4d\u96fb'},
                         {'data_id': '2330', 'date': '2026-09-17', 'close': 99},
                         {'stock_id': '9999', 'date': '2026-09-19', 'close': 10}, None],
                'fetched_at': '2026-09-20'}, ensure_ascii=False).encode('utf-8')
        (self.root / 'payload.json').write_bytes(raw)
        ref = dict(route_id=route_id, path='payload.json',
                   sha256=hashlib.sha256(raw).hexdigest(), size=len(raw), status='PARTIAL')
        ref.update(overrides)
        (self.root / 'manifest.json').write_bytes(json.dumps(dict(
            schema='stock-data-publication-v1', datasets=[ref])).encode('utf-8'))
        return raw

    def reader(self, cache=True, cache_dir=None):
        row_cache_dir = (cache_dir or self.cache) if cache else None
        return sources.PublishedSources(self.specs, row_cache_dir=row_cache_dir)

    def collect(self, reader, route_id='finmind_price_adj'):
        return [(artifact, list(rows)) for artifact, rows in
                reader.iter_stock_artifact_rows(route_id, '2330')]

    def select(self, reader, route_id='finmind_price_adj'):
        return projection._select(reader, route_id, '2330', None, '2026-09-20')

    def chunk_rows(self):
        repeated = {
            'stock_id': '2330', 'date': '2026-09-18', 'bank_name': 'fixture-bank',
            'payload': 'x' * 240,
        }
        return ([dict(repeated) for _ in range(96)] + [
            {'stock_id': '2330', 'date': 'invalid-date', 'bank_name': 'bad-date'},
            None,
            {'stock_id': '2330', 'date': '2026-09-19', 'marker': 'tail'},
        ])

    def prepare_chunk_cache(self, cache_dir):
        route_id = 'finmind_government_bank'
        rows = self.chunk_rows()
        raw = json.dumps({'data': rows}, separators=(',', ':')).encode('utf-8')
        self.publish(raw, route_id=route_id, status='SUCCESS')
        reader = self.reader(cache_dir=cache_dir)
        with patch.object(sources, '_ROW_CACHE_LIMIT', 8192):
            result = reader.prewarm_stock_rows(
                ['2330'], [route_id], single_stock_prepare=True)
        self.assertFalse(result['gaps'])
        manifests = [path for path in Path(cache_dir).glob('*.json')
                     if not path.name.endswith('.chunk.json')]
        self.assertEqual(len(manifests), 1)
        return reader, route_id, rows, raw, manifests[0]

    def reseal(self, document, digest_field):
        unsigned = dict(document)
        unsigned.pop(digest_field, None)
        document[digest_field] = sources._sha(sources._canonical(unsigned))
        return document

    def test_cold_hit_records_metadata_descriptor_and_single_parse(self):
        self.publish()
        old = self.reader(False)
        expected = self.select(old)
        cold = self.reader()
        with patch.object(sources, '_validate_rows', wraps=sources._validate_rows) as strict, \
                patch.object(sources, '_stream_rows', side_effect=AssertionError('second parse')), \
                patch.object(sources, '_CHUNK_SIZE', 7):
            self.assertEqual(self.select(cold), expected)
            self.assertEqual(strict.call_count, 1)
        warm = self.reader()
        with patch.object(sources, '_validate_rows', side_effect=AssertionError('strict parse')), \
                patch.object(sources, '_verify_file', wraps=sources._verify_file) as verify:
            self.assertEqual(self.select(warm), expected)
            self.assertEqual(verify.call_count, 1)
        self.assertEqual(cold.descriptor(), old.descriptor())
        self.assertEqual(warm.descriptor(), old.descriptor())
        self.assertIn('non_object_row', expected['gaps'])
        self.assertEqual(expected['unknown_publication_time_rows'], 2)

    def test_warm_hashes_entire_source_and_rejects_same_size_changed_bytes(self):
        raw = self.publish()
        self.collect(self.reader())
        reader = self.reader()
        (self.root / 'payload.json').write_bytes(raw.replace(b'9999', b'8888'))
        self.assertEqual(self.collect(reader), [])
        self.assertIn('hash mismatch', reader.diagnostics[-1])
        self.assertEqual(reader.descriptor()['artifacts'], [])

    def test_warm_rejects_size_change(self):
        raw = self.publish()
        self.collect(self.reader())
        reader = self.reader()
        (self.root / 'payload.json').write_bytes(raw + b' ')
        self.assertEqual(self.collect(reader), [])
        self.assertTrue(reader.diagnostics)

    def test_corrupt_cache_and_wrong_identity_rebuilt(self):
        self.publish()
        expected = self.collect(self.reader())
        path = next(self.cache.glob('*.json'))
        for corrupt in (b'{broken', b'{}'):
            path.write_bytes(corrupt)
            with patch.object(sources, '_validate_rows', wraps=sources._validate_rows) as strict:
                self.assertEqual(self.collect(self.reader()), expected)
                self.assertEqual(strict.call_count, 1)
        document = json.loads(path.read_bytes())
        document.pop('canonical_sha256')
        document['identity']['stock_id'] = '9999'
        document['canonical_sha256'] = sources._sha(sources._canonical(document))
        path.write_bytes(sources._canonical(document))
        self.assertEqual(self.collect(self.reader()), expected)

    def test_changed_descriptor_creates_new_cache_and_current_metadata(self):
        self.publish()
        first = self.reader()
        self.collect(first)
        snapshot = first.snapshot_descriptor()
        restored = sources.PublishedSources.from_descriptor(snapshot, row_cache_dir=self.cache)
        with patch.object(sources, '_validate_rows', side_effect=AssertionError('strict parse')):
            self.assertEqual(self.collect(restored), self.collect(self.reader()))
        self.publish(b'[{"stock_id":"2330","date":"2026-09-20","close":101}]', status='SUCCESS')
        with patch.object(sources, '_validate_rows', wraps=sources._validate_rows) as strict:
            artifact, rows = self.collect(self.reader())[0]
            self.assertEqual(strict.call_count, 1)
        self.assertEqual(rows[0]['close'], 101)
        self.assertEqual(artifact.status, 'SUCCESS')
        self.assertEqual(len(list(self.cache.glob('*.json'))), 2)

    def test_stock_cache_lock_keys_route_artifact_identity_not_whole_snapshot(self):
        raw = self.publish()

        def lock_names():
            with self.reader().stock_cache_lock(
                    'finmind_price_adj', '2330', lock_timeout_seconds=1):
                pass
            return {path.name for path in (self.cache / '.locks').glob('*.lock')}

        first = lock_names()
        self.assertEqual(len(first), 1)
        other = b'[{"stock_id":"2330","date":"2026-09-20"}]'
        (self.root / 'per.json').write_bytes(other)
        manifest_path = self.root / 'manifest.json'
        manifest = json.loads(manifest_path.read_bytes())
        manifest['datasets'].append(dict(route_id='finmind_per', path='per.json',
            sha256=hashlib.sha256(other).hexdigest(), size=len(other), status='SUCCESS'))
        manifest_path.write_bytes(json.dumps(manifest).encode('utf-8'))
        self.assertEqual(lock_names(), first)

        changed = raw.replace(b'100', b'101')
        (self.root / 'payload.json').write_bytes(changed)
        manifest['datasets'][0]['sha256'] = hashlib.sha256(changed).hexdigest()
        manifest['datasets'][0]['size'] = len(changed)
        manifest_path.write_bytes(json.dumps(manifest).encode('utf-8'))
        self.assertEqual(len(lock_names()), 2)

    def test_stock_cache_lock_keys_route_artifact_identity_not_whole_snapshot(self):
        raw = self.publish()

        def lock_names():
            with self.reader().stock_cache_lock(
                    'finmind_price_adj', '2330', lock_timeout_seconds=1):
                pass
            return {path.name for path in (self.cache / '.locks').glob('*.lock')}

        first = lock_names()
        self.assertEqual(len(first), 1)
        other = b'[{"stock_id":"2330","date":"2026-09-20"}]'
        (self.root / 'per.json').write_bytes(other)
        manifest_path = self.root / 'manifest.json'
        manifest = json.loads(manifest_path.read_bytes())
        manifest['datasets'].append(dict(route_id='finmind_per', path='per.json',
            sha256=hashlib.sha256(other).hexdigest(), size=len(other), status='SUCCESS'))
        manifest_path.write_bytes(json.dumps(manifest).encode('utf-8'))
        self.assertEqual(lock_names(), first)

        changed = raw.replace(b'100', b'101')
        (self.root / 'payload.json').write_bytes(changed)
        manifest['datasets'][0]['sha256'] = hashlib.sha256(changed).hexdigest()
        manifest['datasets'][0]['size'] = len(changed)
        manifest_path.write_bytes(json.dumps(manifest).encode('utf-8'))
        self.assertEqual(len(lock_names()), 2)

    def test_all_array_priorities_and_root(self):
        for raw in (b'{"rows":[1],"records":[2],"data":[3]}',
                    b'{"rows":[1],"records":[2]}', b'{"rows":[1]}', b'[4]'):
            self.publish(raw)
            old = [(a, list(rows)) for a, rows in self.reader(False).iter_artifact_rows('finmind_price_adj')]
            self.assertEqual([rows for _, rows in self.collect(self.reader())],
                             [rows for _, rows in old])
            self.assertEqual([rows for _, rows in self.collect(self.reader())],
                             [rows for _, rows in old])

    def test_invalid_tail_duplicates_numbers_and_utf8_never_cached(self):
        for raw in (b'[{"stock_id":"2330"}] trailing', b'{"data":[],"data":[]}',
                    b'[{"x":1,"x":2}]', b'[NaN]', b'["\xff"]', b'[{"stock_id":"2330"},]'):
            self.publish(raw)
            reader = self.reader()
            self.assertEqual(self.collect(reader), [])
            self.assertTrue(reader.diagnostics)
        self.assertFalse(self.cache.exists())

    def test_date_cache_warm_no_parse_and_remembers_price_ref(self):
        self.publish()
        cold = projection.pin_published_sources_context(source_specs=self.specs, row_cache_dir=self.cache)
        self.assertEqual(cold['data_date'], '2026-09-19')
        self.assertEqual(len(cold['sources'].descriptor()['artifacts']), 1)
        with patch.object(sources, '_validate_rows', side_effect=AssertionError('strict parse')), \
                patch.object(sources, '_verify_file', wraps=sources._verify_file) as verify:
            warm = projection.pin_published_sources_context(source_specs=self.specs, row_cache_dir=self.cache)
            self.assertEqual(warm['data_date'], cold['data_date'])
            self.assertEqual(verify.call_count, 0)
        self.assertEqual(warm['snapshot_descriptor'], cold['snapshot_descriptor'])
        document = json.loads(next(self.cache.glob('*.json')).read_bytes())
        self.assertEqual(document['records'], [])
        self.assertEqual(document['dates']['rows'], '2099-01-01')

    def test_price_date_uses_actual_max_summary_or_verified_date_cache(self):
        self.publish()
        manifest_path = self.root / 'manifest.json'
        manifest = json.loads(manifest_path.read_bytes())
        entry = manifest['datasets'][0]
        entry['actual_min_date'] = '2026-09-17'
        entry['record_count'] = 3
        manifest_path.write_bytes(json.dumps(manifest).encode('utf-8'))

        cold = projection.pin_published_sources_context(
            source_specs=self.specs, row_cache_dir=self.cache,
        )
        self.assertEqual(cold['data_date'], '2026-09-19')
        self.assertEqual(cold['pin_query_metrics']['mode'], 'published_consumer_date_scan')
        self.assertIsNone(cold['pin_query_metrics']['source_bytes_read'])
        with patch.object(sources, '_validate_rows', side_effect=AssertionError('date cache raw rehash')):
            warm = projection.pin_published_sources_context(
                source_specs=self.specs, row_cache_dir=self.cache,
            )
        self.assertEqual(warm['data_date'], cold['data_date'])
        self.assertIsNone(warm['pin_query_metrics']['source_bytes_read'])

        manifest['datasets'][0]['actual_max_date'] = '2026-09-18'
        manifest_path.write_bytes(json.dumps(manifest).encode('utf-8'))
        summarized = projection.pin_published_sources_context(
            source_specs=self.specs, row_cache_dir=self.cache,
        )
        self.assertEqual(summarized['data_date'], '2026-09-18')
        self.assertEqual(summarized['pin_query_metrics']['mode'],
                         'published_verified_artifact_summary')
        self.assertEqual(summarized['pin_query_metrics']['source_bytes_read'], 0)

    def test_malformed_or_conflicting_actual_max_summary_fails_closed(self):
        for summary, message in [
            ({'actual_max_date': '2026/09/19'}, 'maximum_invalid'),       # YYYYMMDD is valid (20261006)
            ({'actual_max_date': '2026-09-19',
              'metadata': {'actual_max_date': '2026-09-18'}}, 'summary_conflict'),
        ]:
            with self.subTest(summary=summary):
                self.publish()
                manifest_path = self.root / 'manifest.json'
                manifest = json.loads(manifest_path.read_bytes())
                manifest['datasets'][0].update(summary)
                manifest_path.write_bytes(json.dumps(manifest).encode('utf-8'))
                with self.assertRaisesRegex(ValueError, message):
                    projection.pin_published_sources_context(
                        source_specs=self.specs, row_cache_dir=self.cache,
                    )

    def test_price_date_uses_actual_max_summary_or_verified_date_cache(self):
        self.publish()
        manifest_path = self.root / 'manifest.json'
        manifest = json.loads(manifest_path.read_bytes())
        entry = manifest['datasets'][0]
        entry['actual_min_date'] = '2026-09-17'
        entry['record_count'] = 3
        manifest_path.write_bytes(json.dumps(manifest).encode('utf-8'))

        cold = projection.pin_published_sources_context(
            source_specs=self.specs, row_cache_dir=self.cache,
        )
        self.assertEqual(cold['data_date'], '2026-09-19')
        self.assertEqual(cold['pin_query_metrics']['mode'], 'published_consumer_date_scan')
        self.assertIsNone(cold['pin_query_metrics']['source_bytes_read'])
        with patch.object(sources, '_validate_rows', side_effect=AssertionError('date cache raw rehash')):
            warm = projection.pin_published_sources_context(
                source_specs=self.specs, row_cache_dir=self.cache,
            )
        self.assertEqual(warm['data_date'], cold['data_date'])
        self.assertIsNone(warm['pin_query_metrics']['source_bytes_read'])

        manifest['datasets'][0]['actual_max_date'] = '2026-09-18'
        manifest_path.write_bytes(json.dumps(manifest).encode('utf-8'))
        summarized = projection.pin_published_sources_context(
            source_specs=self.specs, row_cache_dir=self.cache,
        )
        self.assertEqual(summarized['data_date'], '2026-09-18')
        self.assertEqual(summarized['pin_query_metrics']['mode'],
                         'published_verified_artifact_summary')
        self.assertEqual(summarized['pin_query_metrics']['source_bytes_read'], 0)

    def test_malformed_or_conflicting_actual_max_summary_fails_closed(self):
        for summary, message in [
            ({'actual_max_date': '2026/09/19'}, 'maximum_invalid'),       # YYYYMMDD is valid (20261006)
            ({'actual_max_date': '2026-09-19',
              'metadata': {'actual_max_date': '2026-09-18'}}, 'summary_conflict'),
        ]:
            with self.subTest(summary=summary):
                self.publish()
                manifest_path = self.root / 'manifest.json'
                manifest = json.loads(manifest_path.read_bytes())
                manifest['datasets'][0].update(summary)
                manifest_path.write_bytes(json.dumps(manifest).encode('utf-8'))
                with self.assertRaisesRegex(ValueError, message):
                    projection.pin_published_sources_context(
                        source_specs=self.specs, row_cache_dir=self.cache,
                    )

    def test_date_multiple_artifacts_and_invalid_iso(self):
        # YYYYMMDD is a valid spelling (date directive 2026-10-06); YYYY/MM/DD and impossible days are not
        self.publish(b'[{"date":"2026/09/20"},{"date":"2026-02-30"},{"date":"20260917"}]')
        raw = b'{"records":[{"date":"2026-09-19"},{"date":true}]}'
        (self.root / 'second.json').write_bytes(raw)
        manifest_path = self.root / 'manifest.json'
        manifest = json.loads(manifest_path.read_bytes())
        manifest['datasets'].append(dict(route_id='finmind_price_adj', path='second.json',
            sha256=hashlib.sha256(raw).hexdigest(), size=len(raw)))
        manifest_path.write_bytes(json.dumps(manifest).encode('utf-8'))
        self.assertEqual(self.reader().latest_artifact_date('finmind_price_adj'), '2026-09-19')
        with patch.object(sources, '_validate_rows', side_effect=AssertionError('strict parse')):
            self.assertEqual(self.reader().latest_artifact_date('finmind_price_adj'), '2026-09-19')

    def test_official_market_daily_cache_is_per_stock_and_revalidates_source_hash(self):
        rows = [
            {'stock_id': '2330', 'date': '2026-09-18', 'market': 'TWSE',
             'open': 100, 'high': 110, 'low': 90, 'close': 105, 'volume': 1000},
            {'stock_id': '2317', 'date': '2026-09-18', 'market': 'TWSE',
             'open': 200, 'high': 210, 'low': 190, 'close': 205, 'volume': 2000},
        ]
        raw = json.dumps({'data': rows}, separators=(',', ':')).encode('utf-8')
        route_id = 'official_market_daily'
        self.publish(raw, route_id=route_id, status='PARTIAL')
        expected = self.select(self.reader(False), route_id)
        self.assertEqual([row['stock_id'] for row in expected['records']], ['2330'])

        cold = self.reader()
        with patch.object(sources, '_validate_rows', wraps=sources._validate_rows) as strict, \
                patch.object(sources, '_stream_rows', side_effect=AssertionError('second payload parse')):
            actual = self.select(cold, route_id)
            self.assertEqual(actual, expected)
            self.assertEqual(strict.call_count, 1)

        warm = self.reader()
        with patch.object(sources, '_validate_rows', side_effect=AssertionError('warm payload parse')), \
                patch.object(sources, '_stream_rows', side_effect=AssertionError('warm payload stream')), \
                patch.object(sources, '_verify_file', wraps=sources._verify_file) as verify:
            self.assertEqual(self.select(warm, route_id), expected)
            self.assertEqual(verify.call_count, 1)

        changed = raw.replace(b'2317', b'2318')
        self.assertEqual(len(changed), len(raw))
        (self.root / 'payload.json').write_bytes(changed)
        tampered = self.reader()
        self.assertFalse(self.select(tampered, route_id)['records'])
        self.assertIn('hash mismatch', tampered.diagnostics[-1])

    def test_batch_and_single_stock_both_stream_official_source_into_identical_caches(self):
        rows = [
            {'stock_id': '2330', 'date': '2026-09-18', 'close': 105},
            {'stock_id': '2317', 'date': '2026-09-18', 'close': 205},
        ]
        raw = json.dumps({'data': rows}, separators=(',', ':')).encode('utf-8')
        route_id = 'official_market_daily'
        self.publish(raw, route_id=route_id, status='PARTIAL')
        self.assertFalse(hasattr(sources, '_BATCH_OFFICIAL_MARKET_MAX_BYTES'))

        batch_dir = self.root.parent / 'batch-cache'
        batch = self.reader(cache_dir=batch_dir)
        with patch.object(sources, '_validate_rows', wraps=sources._validate_rows) as strict:
            batch_result = batch.prewarm_stock_rows(['2330'], [route_id])
        self.assertEqual(strict.call_count, 1)
        self.assertFalse(batch_result['gaps'])
        self.assertEqual(batch_result['source_bytes_read'], len(raw))
        self.assertEqual(batch_result['prepared_files'], 1)

        single_dir = self.root.parent / 'single-cache'
        single = self.reader(cache_dir=single_dir)
        with self.assertRaisesRegex(ValueError, 'exactly one stock id'):
            single.prewarm_stock_rows(['2330', '2317'], [route_id],
                                      single_stock_prepare=True)
        result = single.prewarm_stock_rows(['2330'], [route_id],
                                           single_stock_prepare=True)

        self.assertFalse(result['gaps'])
        self.assertEqual(result['source_bytes_read'], len(raw))
        self.assertEqual(result['prepared_files'], 1)
        batch_files = {path.name: path.read_bytes() for path in batch_dir.glob('*.json')}
        single_files = {path.name: path.read_bytes() for path in single_dir.glob('*.json')}
        self.assertEqual(len(batch_files), 1)
        self.assertEqual(batch_files, single_files)
        for reader in (batch, single):
            selected = self.select(reader, route_id)
            self.assertEqual([row['stock_id'] for row in selected['records']], ['2330'])

    def _spool_fixture(self, route_id='finmind_government_bank', stocks=('2330', '2317', '1101')):
        rows = []
        for index in range(60):
            for stock in stocks:
                rows.append({'stock_id': stock, 'date': f'2026-09-{1 + index % 28:02d}',
                             'note': '報告' * (index % 7) + 'x' * (index * 3 % 50), 'seq': index})
        rows.append(None)
        rows.append({'stock_id': '9999', 'date': 'not-a-date'})
        raw = json.dumps({'data': rows}, ensure_ascii=False, separators=(',', ':')).encode('utf-8')
        self.publish(raw, route_id=route_id, status='SUCCESS')
        return rows

    def test_memory_budget_spills_never_change_cache_bytes_or_format(self):
        route_id = 'finmind_government_bank'
        stocks = ['2330', '2317', '1101']
        self._spool_fixture(route_id, stocks)
        produced = {}
        for label, budget, limit in (('memory', None, None), ('spilled', 4096, None),
                                     ('memory-chunked', None, 4096), ('spilled-chunked', 1024, 4096)):
            cache_dir = self.root.parent / f'budget-{label}'
            reader = self.reader(cache_dir=cache_dir)
            flushes = []
            original = sources._RowCacheSpoolBudget.flush_all

            def counted(budget_object, _original=original, _flushes=flushes):
                _flushes.append(budget_object.pending)
                return _original(budget_object)

            with patch.object(sources._RowCacheSpoolBudget, 'flush_all', counted), \
                    patch.object(sources, '_PREWARM_MEMORY_BUDGET', budget or sources._PREWARM_MEMORY_BUDGET), \
                    patch.object(sources, '_ROW_CACHE_LIMIT', limit or sources._ROW_CACHE_LIMIT):
                result = reader.prewarm_stock_rows(stocks, [route_id])
                self.assertFalse(result['gaps'])
                self.assertEqual(result['prepared_files'], 1)
                cached = {stock: [row for _, rows in reader.cached_stock_artifact_rows(
                    route_id, stock, pinned_snapshot_fingerprint=reader.fingerprint)['artifacts']
                    for row in rows] for stock in stocks}
            self.assertEqual(bool(flushes), budget is not None, label)
            if budget is not None:
                self.assertTrue(all(pending > budget for pending in flushes))
            self.assertEqual(list(cache_dir.glob('.published-rows-stage-*')), [])
            produced[label] = ({path.name: path.read_bytes() for path in cache_dir.glob('*.json')}, cached)
        self.assertEqual(produced['memory'], produced['spilled'])
        self.assertEqual(produced['memory-chunked'], produced['spilled-chunked'])
        self.assertTrue(any(name.endswith('.chunk.json') for name in produced['memory-chunked'][0]))
        self.assertFalse(any(name.endswith('.chunk.json') for name in produced['memory'][0]))
        self.assertEqual(produced['memory'][1], produced['memory-chunked'][1])

    def test_each_flush_writes_each_pending_spool_with_one_open(self):
        route_id = 'finmind_government_bank'
        stocks = ['2330', '2317', '1101']
        self._spool_fixture(route_id, stocks)
        reader = self.reader(cache_dir=self.root.parent / 'flush-opens')
        events = []
        original_flush = sources._RowCacheSpool.flush
        original_open = Path.open
        original_temp = sources.tempfile.NamedTemporaryFile

        def flush(spool):
            if spool.lines:
                events.append(('flush', id(spool)))
            return original_flush(spool)

        def tracked_open(path, mode='r', *args, **kwargs):
            if path.name.startswith('.published-rows-stage-') and any(flag in mode for flag in 'wa+'):
                events.append(('open', path.name))
            return original_open(path, mode, *args, **kwargs)

        def tracked_temp(*args, **kwargs):
            if kwargs.get('prefix') == '.published-rows-stage-':
                events.append(('open', 'new'))
            return original_temp(*args, **kwargs)

        with patch.object(sources, '_PREWARM_MEMORY_BUDGET', 2048), \
                patch.object(sources._RowCacheSpool, 'flush', flush), \
                patch.object(Path, 'open', tracked_open), \
                patch.object(sources.tempfile, 'NamedTemporaryFile', tracked_temp):
            result = reader.prewarm_stock_rows(stocks, [route_id])
        self.assertFalse(result['gaps'])
        flush_count = sum(1 for kind, _ in events if kind == 'flush')
        open_count = sum(1 for kind, _ in events if kind == 'open')
        self.assertGreater(flush_count, len(stocks))
        self.assertEqual(open_count, flush_count)

    def test_spool_document_size_is_exact_at_the_single_document_boundary(self):
        route_id = 'finmind_government_bank'
        self._spool_fixture(route_id, ('2330',))
        probe_dir = self.root.parent / 'boundary-probe'
        self.reader(cache_dir=probe_dir).prewarm_stock_rows(['2330'], [route_id], single_stock_prepare=True)
        [document_path] = list(probe_dir.glob('*.json'))
        exact = document_path.stat().st_size
        for limit, chunked in ((exact, False), (exact - 1, True)):
            with self.subTest(limit=limit):
                cache_dir = self.root.parent / f'boundary-{limit}'
                reader = self.reader(cache_dir=cache_dir)
                with patch.object(sources, '_ROW_CACHE_LIMIT', limit):
                    self.assertFalse(reader.prewarm_stock_rows(
                        ['2330'], [route_id], single_stock_prepare=True)['gaps'])
                self.assertEqual(any(path.name.endswith('.chunk.json') for path in cache_dir.glob('*.json')),
                                 chunked)
                if not chunked:
                    self.assertEqual(next(cache_dir.glob('*.json')).read_bytes(), document_path.read_bytes())

    def test_cache_replace_keeps_an_identical_target_that_a_reader_holds_open(self):
        target = self.root.parent / 'held' / 'cache.json'
        target.parent.mkdir()
        target.write_bytes(b'{"same":1}')
        sleeps = []
        with open(target, 'rb') as held, \
                patch.object(sources, '_CACHE_REPLACE_RETRY_SECONDS', (0.0, 0.0)), \
                patch.object(sources.time, 'sleep', sleeps.append):
            # Windows: os.replace fails while this handle is open; identical bytes are the same cache
            self.assertTrue(sources._write_cache_bytes(target, b'{"same":1}'))
            self.assertEqual(sleeps, [])
            different = sources._write_cache_bytes(target, b'{"other":2}')
            self.assertEqual(held.read(), b'{"same":1}')
        if os.name == 'nt':
            self.assertFalse(different)                    # refused after the retries, target unchanged
            self.assertEqual(sleeps, [0.0, 0.0])
            self.assertEqual(target.read_bytes(), b'{"same":1}')
        else:
            self.assertTrue(different)
            self.assertEqual(target.read_bytes(), b'{"other":2}')
        self.assertEqual(list(target.parent.glob('*.tmp')), [])

    def test_batch_prewarm_while_a_reader_holds_a_stock_cache_open_keeps_the_route(self):
        rows = [{'stock_id': stock, 'date': '2026-09-18', 'close': close}
                for stock, close in (('2330', 105), ('2317', 205))]
        raw = json.dumps({'data': rows}, separators=(',', ':')).encode('utf-8')
        route_id = 'official_market_daily'
        self.publish(raw, route_id=route_id, status='PARTIAL')
        first = self.reader()
        self.assertFalse(first.prewarm_stock_rows(['2330'], [route_id], single_stock_prepare=True)['gaps'])
        [cache_file] = list(self.cache.glob('*.json'))
        batch = self.reader()
        with open(cache_file, 'rb'):
            result = batch.prewarm_stock_rows(['2330', '2317'], [route_id])
        self.assertFalse(result['gaps'])
        self.assertEqual(result['prepared_files'], 1)
        self.assertNotIn(route_id, batch._sources[0]['errors'])
        for stock, close in (('2330', 105), ('2317', 205)):
            cached = batch.cached_stock_artifact_rows(route_id, stock, pinned_snapshot_fingerprint=batch.fingerprint)
            self.assertEqual([row['close'] for _, rows in cached['artifacts'] for row in rows], [close])
        self.assertEqual(list(self.cache.glob('*.tmp')), [])

    def test_retained_routes_cache_matches_selector_and_warms_without_payload_parse(self):
        fixtures = {
            'finmind_trading_daily_report': [
                {'stock_id': '2330', 'date': '2026-09-18', 'securities_trader_id': 'A',
                 'price': 0, 'net': 11},
                {'stock_id': '2330', 'date': '2026-09-18', 'securities_trader_id': 'A',
                 'price': 0, 'net': 99},
                {'stock_id': '2330', 'date': '2026-09-18', 'securities_trader_id': 'A',
                 'price': 100, 'net': 12},
                {'stock_id': '2330', 'date': '2026-09-18', 'securities_trader_id': 'B',
                 'price': 0, 'net': 13},
                {'stock_id': '9999', 'date': '2026-09-18', 'securities_trader_id': 'Z',
                 'price': 80, 'net': 14},
                {'stock_id': '9999', 'date': '2026-09-18', 'price': 81, 'net': 15},
                {'data_id': '2330', 'date': '2026-09-18', 'securities_trader_id': 'C',
                 'price': 9, 'net': 16},
                {'date': '2026-09-18', 'market_total': 1},
                None,
            ],
            'finmind_government_bank': [
                {'stock_id': '2330', 'date': '2026-09-18', 'buy': 1},
                {'data_id': '2330', 'date': '2026-09-17', 'buy': 2},
                {'date': 'not-a-date', 'bank_name': 'market-total'},
                {'code': '2330', 'date': 'not-a-date', 'buy': 9},
                {'stock_id': '9999', 'data_id': '2330', 'date': '2026-09-16', 'buy': 3},
            ],
            'tdcc_share_distribution_raw': [
                {'stock_id': '2330', 'date': '2026-09-18', 'level': 'A'},
                {'data_id': '2330', 'date': '2026-09-17', 'level': 'B'},
                {'date': 'not-a-date', 'shareholders': 100},
                {'code': '2330', 'date': 'not-a-date', 'shareholders': 200},
                {'stock_id': '9999', 'data_id': '2330', 'date': '2026-09-16', 'level': 'C'},
            ],
        }
        for route_id, rows in fixtures.items():
            with self.subTest(route_id=route_id):
                raw = json.dumps({'data': rows}, ensure_ascii=False).encode('utf-8')
                self.publish(raw, route_id=route_id, status='SUCCESS')
                expected = self.select(self.reader(False), route_id)
                cold = self.reader()
                with patch.object(sources, '_validate_rows', wraps=sources._validate_rows) as strict, \
                        patch.object(sources, '_stream_rows',
                                     side_effect=AssertionError('second payload parse')):
                    actual = self.select(cold, route_id)
                    self.assertEqual(actual['records'], expected['records'])
                    if route_id == 'finmind_trading_daily_report':
                        self.assertEqual(actual['gaps'], expected['gaps'])
                    else:
                        self.assertEqual(set(actual['gaps']) - {'invalid_date'}, set(expected['gaps']))
                        self.assertIn('invalid_date', actual['gaps'])
                    self.assertEqual(strict.call_count, 1)
                warm = self.reader()
                with patch.object(sources, '_validate_rows',
                                  side_effect=AssertionError('warm payload parse')), \
                        patch.object(sources, '_stream_rows',
                                     side_effect=AssertionError('warm payload stream')), \
                        patch.object(sources, '_verify_file', wraps=sources._verify_file) as verify:
                    warm_result = self.select(warm, route_id)
                    self.assertEqual(warm_result['records'], expected['records'])
                    if route_id == 'finmind_trading_daily_report':
                        self.assertEqual(warm_result['gaps'], expected['gaps'])
                    else:
                        self.assertEqual(set(warm_result['gaps']) - {'invalid_date'}, set(expected['gaps']))
                        self.assertIn('invalid_date', warm_result['gaps'])
                    self.assertEqual(verify.call_count, 1)
                if route_id == 'finmind_trading_daily_report':
                    self.assertEqual([(row['securities_trader_id'], row['price'], row['net'])
                                      for row in actual['records']], [('A', 0, 11), ('A', 100, 12),
                                                                      ('B', 0, 13)])
                    self.assertIn('trading_daily_report_identity_missing', actual['gaps'])
                    self.assertIn('non_object_row', actual['gaps'])
                else:
                    self.assertEqual([row['date'] for row in actual['records']],
                                     ['2026-09-18', '2026-09-17'])
                    self.assertIn('invalid_date', actual['gaps'])

    def test_retained_order21_same_size_change_is_rejected(self):
        rows = [
            {'stock_id': '2330', 'date': '2026-09-18', 'securities_trader_id': 'A', 'price': 0},
            {'stock_id': '9999', 'date': '2026-09-18', 'securities_trader_id': 'Z', 'price': 1},
        ]
        raw = json.dumps({'data': rows}, separators=(',', ':')).encode('utf-8')
        self.publish(raw, route_id='finmind_trading_daily_report', status='SUCCESS')
        self.select(self.reader(), 'finmind_trading_daily_report')
        reader = self.reader()
        changed = raw.replace(b'9999', b'8888', 1)
        self.assertEqual(len(changed), len(raw))
        (self.root / 'payload.json').write_bytes(changed)
        result = self.select(reader, 'finmind_trading_daily_report')
        self.assertFalse(result['records'])
        self.assertIn('hash mismatch', reader.diagnostics[-1])

    def test_retained_routes_still_reject_strict_json_without_caching(self):
        for route_id in ('finmind_trading_daily_report', 'finmind_government_bank',
                         'tdcc_share_distribution_raw', 'official_market_daily'):
            with self.subTest(route_id=route_id):
                cache_dir = self.root / f'cache-strict-{route_id}'
                self.publish(b'{"data":[{"stock_id":"2330","date":"2026-09-18"}]} trailing',
                             route_id=route_id, status='SUCCESS')
                reader = self.reader(cache_dir=cache_dir)
                self.assertEqual(self.select(reader, route_id)['records'], ())
                self.assertTrue(reader.diagnostics)
                self.assertFalse(list(cache_dir.glob('*.json')))

    def test_no_cache_missing_declarations_and_unapproved_route_use_old_reader(self):
        self.publish()
        with patch.object(sources, '_stream_rows', wraps=sources._stream_rows) as stream:
            self.collect(self.reader(False))
            self.assertEqual(stream.call_count, 1)
        self.publish(sha256=None, size=None)
        self.collect(self.reader())
        self.assertFalse(self.cache.exists())
        # finmind_stock_info became a current C0 route (published_single_stock_projection.CURRENT_C0_ROUTE_IDS) and is
        # row-cached now; finmind_per is neither a C0 nor a retained route, so it still streams the whole artifact.
        self.publish(route_id='finmind_per', status='SUCCESS')
        reader = self.reader()
        with patch.object(sources, '_stream_rows', wraps=sources._stream_rows) as stream:
            rows = self.collect(reader, 'finmind_per')
            self.assertEqual(stream.call_count, 1)
        self.assertEqual(len(rows[0][1]), 4)
        self.assertFalse(self.cache.exists())
        self.assertIn('finmind_stock_info', projection.CURRENT_C0_ROUTE_IDS)
        self.assertNotIn('finmind_per', projection.CURRENT_C0_ROUTE_IDS)

    def test_retained_route_limit_and_unwritable_fall_back_without_temp_leaks(self):
        rows = [
            {'stock_id': '2330', 'date': '2026-09-18', 'securities_trader_id': 'A', 'price': 0},
            {'stock_id': '9999', 'date': '2026-09-18', 'price': 2},
        ]
        raw = json.dumps({'data': rows}, separators=(',', ':')).encode('utf-8')
        route_id = 'finmind_trading_daily_report'
        self.publish(raw, route_id=route_id, status='SUCCESS')
        expected = self.select(self.reader(False), route_id)
        with patch.object(sources, '_ROW_CACHE_LIMIT', 32), \
                patch.object(sources, '_stream_rows', wraps=sources._stream_rows) as stream:
            self.assertEqual(self.select(self.reader(), route_id), expected)
            self.assertEqual(stream.call_count, 1)
        with patch.object(sources.os, 'replace', side_effect=PermissionError('fixture')):
            self.assertEqual(self.select(self.reader(), route_id), expected)
        self.assertEqual(list(self.cache.glob('*')), [])

    def test_prepared_small_cache_keeps_v1_format(self):
        raw = self.publish()
        published_rows = json.loads(raw)['data']
        expected_rows = [row for row in published_rows
                         if not isinstance(row, dict)
                         or row.get('stock_id', row.get('data_id', '')) == '2330']
        reader = self.reader()
        result = reader.prewarm_stock_rows(
            ['2330'], ['finmind_price_adj'], single_stock_prepare=True)
        self.assertFalse(result['gaps'])
        cache_paths = list(self.cache.glob('*.json'))
        self.assertEqual(len(cache_paths), 1)
        document = json.loads(cache_paths[0].read_bytes())
        self.assertEqual(document['schema'], 'published-single-stock-rows-v1')
        warm = self.reader()
        self.assertEqual(warm.descriptor()['artifacts'], [])
        cached = warm.cached_stock_artifact_rows(
            'finmind_price_adj', '2330', pinned_snapshot_fingerprint=reader.fingerprint)
        self.assertIsNotNone(cached)
        cached_rows = [row for _, rows in cached['artifacts'] for row in rows]
        self.assertEqual(cached_rows, expected_rows)
        self.assertEqual(len(cached_rows), cached['metrics']['rows_loaded'])
        [artifact] = warm.descriptor()['artifacts']
        self.assertEqual(artifact['route_id'], 'finmind_price_adj')
        self.assertEqual(artifact['status'], 'PARTIAL')
        self.assertEqual(artifact['root'], str(self.root))
        self.assertEqual(artifact['path'], 'payload.json')
        self.assertEqual(artifact['sha256'], hashlib.sha256(raw).hexdigest())
        self.assertEqual(artifact['size'], len(raw))
        self.assertFalse(list(self.cache.glob('*.chunk.json')))

    def test_partial_cached_route_does_not_record_consumed_artifacts(self):
        route_id = 'finmind_price_adj'
        refs = []
        for index, close in enumerate((100, 101), start=1):
            raw = json.dumps({'data': [
                {'stock_id': '2330', 'date': f'2026-09-{18 + index}', 'close': close},
            ]}, separators=(',', ':')).encode('utf-8')
            name = f'payload-{index}.json'
            (self.root / name).write_bytes(raw)
            refs.append(dict(route_id=route_id, path=name,
                sha256=hashlib.sha256(raw).hexdigest(), size=len(raw), status='SUCCESS'))
        (self.root / 'manifest.json').write_bytes(json.dumps(dict(
            schema='stock-data-publication-v1', datasets=refs)).encode('utf-8'))

        producer = self.reader()
        result = producer.prewarm_stock_rows(
            ['2330'], [route_id], single_stock_prepare=True)
        self.assertFalse(result['gaps'])
        cache_paths = list(self.cache.glob('*.json'))
        self.assertEqual(len(cache_paths), 2)
        missing = next(path for path in cache_paths
            if json.loads(path.read_bytes())['identity']['path'].endswith('payload-2.json'))
        missing.unlink()

        warm = self.reader()
        cached = warm.cached_stock_artifact_rows(
            route_id, '2330', pinned_snapshot_fingerprint=warm.fingerprint)
        self.assertIsNone(cached)
        self.assertEqual(warm.descriptor()['artifacts'], [])

    def test_chunked_cache_matches_cold_rows_and_gaps_without_warm_source_reads(self):
        route_id = 'finmind_government_bank'
        rows = self.chunk_rows()
        raw = json.dumps({'data': rows}, separators=(',', ':')).encode('utf-8')
        self.publish(raw, route_id=route_id, status='SUCCESS')
        cold = self.select(self.reader(False), route_id)

        prepared = self.reader()
        with patch.object(sources, '_ROW_CACHE_LIMIT', 8192):
            result = prepared.prewarm_stock_rows(
                ['2330'], [route_id], single_stock_prepare=True)
        self.assertFalse(result['gaps'])
        manifest_paths = [path for path in self.cache.glob('*.json')
                          if not path.name.endswith('.chunk.json')]
        self.assertEqual(len(manifest_paths), 1)
        manifest = json.loads(manifest_paths[0].read_bytes())
        self.assertEqual(manifest['schema'], 'published-single-stock-row-chunks-v1')
        self.assertEqual(manifest['record_count'], len(rows))
        self.assertGreater(len(manifest['chunks']), 1)
        chunk_hashes = [chunk['sha256'] for chunk in manifest['chunks']]
        self.assertLess(len(set(chunk_hashes)), len(chunk_hashes))

        warm = self.reader()
        payload_path = (self.root / 'payload.json').resolve()
        original_open = Path.open
        payload_reads = []

        def tracked_open(path, *args, **kwargs):
            if path.resolve() == payload_path:
                payload_reads.append(str(path))
            return original_open(path, *args, **kwargs)

        with patch.object(Path, 'open', new=tracked_open):
            cached = warm.cached_stock_artifact_rows(
                route_id, '2330', pinned_snapshot_fingerprint=warm.fingerprint)
            self.assertIsNotNone(cached)
            warm_result = projection._select(
                warm, route_id, '2330', None, '2026-09-20',
                artifact_rows=cached['artifacts'])
            full_cache = warm.cached_stock_artifact_rows(
                route_id, '2330', pinned_snapshot_fingerprint=warm.fingerprint)
            cached_rows = [row for _, artifact_rows in full_cache['artifacts']
                           for row in artifact_rows]

        self.assertEqual(payload_reads, [])
        self.assertEqual(cached['metrics']['rows_loaded'], len(rows))
        self.assertEqual(len(cached_rows), len(rows))
        self.assertEqual(cached_rows[-1]['marker'], 'tail')
        self.assertEqual(warm_result['records'], cold['records'])
        self.assertEqual(warm_result['gaps'], cold['gaps'])
        self.assertEqual(warm_result['records'][-1]['marker'], 'tail')
        self.assertIn('invalid_date', warm_result['gaps'])
        self.assertIn('non_object_row', warm_result['gaps'])

    def test_rows_below_cap_chunk_when_full_cache_document_exceeds_cap(self):
        route_id = 'finmind_government_bank'
        rows = [
            {'stock_id': '2330', 'date': '2026-09-18', 'payload': 'z' * 420}
            for _ in range(32)
        ]
        raw = json.dumps({'data': rows}, separators=(',', ':')).encode('utf-8')
        self.publish(raw, route_id=route_id, status='SUCCESS')

        baseline_dir = self.root.parent / 'cache-boundary-baseline'
        baseline = self.reader(cache_dir=baseline_dir)
        baseline.prewarm_stock_rows(['2330'], [route_id], single_stock_prepare=True)
        baseline_path = next(baseline_dir.glob('*.json'))
        full_document_bytes = baseline_path.stat().st_size
        encoded_rows_bytes = len(sources._canonical(rows))
        cap = encoded_rows_bytes + 1
        self.assertGreaterEqual(cap, 4096)
        self.assertLess(encoded_rows_bytes, cap)
        self.assertLess(cap, full_document_bytes)

        boundary_dir = self.root.parent / 'cache-boundary-chunked'
        reader = self.reader(cache_dir=boundary_dir)
        with patch.object(sources, '_ROW_CACHE_LIMIT', cap):
            result = reader.prewarm_stock_rows(
                ['2330'], [route_id], single_stock_prepare=True)
        self.assertFalse(result['gaps'])
        manifest_paths = [path for path in boundary_dir.glob('*.json')
                          if not path.name.endswith('.chunk.json')]
        self.assertEqual(len(manifest_paths), 1)
        manifest = json.loads(manifest_paths[0].read_bytes())
        self.assertEqual(manifest['schema'], 'published-single-stock-row-chunks-v1')

        cached = reader.cached_stock_artifact_rows(
            route_id, '2330', pinned_snapshot_fingerprint=reader.fingerprint)
        self.assertIsNotNone(cached)
        cached_rows = [row for _, artifact_rows in cached['artifacts']
                       for row in artifact_rows]
        self.assertEqual(cached_rows, rows)

    def test_chunk_cache_missing_tampered_and_escaping_paths_fail_closed(self):
        for damage in ('missing', 'tampered', 'path_escape'):
            with self.subTest(damage=damage):
                cache_dir = self.root.parent / f'cache-{damage}'
                _, route_id, _, _, manifest_path = self.prepare_chunk_cache(cache_dir)
                manifest = json.loads(manifest_path.read_bytes())
                chunk_path = manifest_path.parent / manifest['chunks'][0]['path']
                if damage == 'missing':
                    chunk_path.unlink()
                elif damage == 'tampered':
                    chunk_path.write_bytes(b'{}')
                else:
                    manifest['chunks'][0]['path'] = '../' + manifest['chunks'][0]['path']
                    manifest = self.reseal(manifest, 'canonical_sha256')
                    manifest_path.write_bytes(sources._canonical(manifest))

                reader = self.reader(cache_dir=cache_dir)
                cached = reader.cached_stock_artifact_rows(
                    route_id, '2330', pinned_snapshot_fingerprint=reader.fingerprint)
                self.assertIsNone(cached)

    def test_runtime_prewarm_error_keeps_pin_valid_and_route_unavailable(self):
        raw = self.publish()
        reader = self.reader()
        pinned = reader.snapshot_descriptor()
        changed = raw.replace(b'2330', b'9999', 1)
        self.assertEqual(len(changed), len(raw))
        (self.root / 'payload.json').write_bytes(changed)

        result = reader.prewarm_stock_rows(
            ['2330'], ['finmind_price_adj'], single_stock_prepare=True)
        self.assertTrue(result['gaps'])
        self.assertTrue(any('finmind_price_adj' in gap and 'hash mismatch' in gap
                            for gap in result['gaps']))
        proof = reader.validate_pinned_context(pinned)
        self.assertEqual(proof['status'], 'verified')
        self.assertIsNone(reader.cached_stock_artifact_rows(
            'finmind_price_adj', '2330', pinned_snapshot_fingerprint=reader.fingerprint))
        self.assertEqual(reader.descriptor()['artifacts'], [])
        self.assertEqual(list(reader.iter_stock_artifact_rows('finmind_price_adj', '2330')), [])
        self.assertTrue(any('finmind_price_adj' in item and 'hash mismatch' in item
                            for item in reader.diagnostics))

    def test_pinned_catalog_tampering_is_rejected(self):
        self.publish()
        reader = self.reader()
        pinned = reader.snapshot_descriptor()
        self.assertEqual(reader.validate_pinned_context(pinned)['status'], 'verified')

        mutations = {
            'catalog_hash_changed': lambda doc: doc['pinned_sources'][0]['entries'][
                'finmind_price_adj'][0][0].__setitem__('sha256', '0' * 64),
            'catalog_path_changed': lambda doc: doc['pinned_sources'][0]['entries'][
                'finmind_price_adj'][0][0].__setitem__('path', 'other-payload.json'),
        }
        for name, mutate in mutations.items():
            with self.subTest(mutation=name):
                changed = json.loads(json.dumps(pinned))
                mutate(changed)
                changed = self.reseal(changed, 'descriptor_sha256')
                # The tampered catalog fails closed wherever it would be read: a reader restored from it is not
                # bound to the verified manifest bytes.
                with self.assertRaisesRegex(ValueError, 'published_context_pin_binding_mismatch:ref_changed:'):
                    sources.PublishedSources.from_descriptor(changed).validate_pinned_context(changed)
                # Converted gate (plan 2.4, catalog_mismatch -> label; same as test_published_manifest_archive_pin):
                # the honest reader verifies its own catalog, labels the difference and never adopts the edit.
                proof = reader.validate_pinned_context(changed)
                self.assertEqual(proof['status'], 'verified')
                self.assertEqual([label['code'] for label in proof['version_labels']], ['catalog_mismatch'])
                self.assertTrue(proof['descriptor_refreshed'])
                self.assertEqual(proof['snapshot_fingerprint'], reader.fingerprint)
                self.assertEqual(reader.snapshot_descriptor()['pinned_sources'],
                                 pinned['pinned_sources'])

    def test_identity_checked_before_and_after_cached_rows(self):
        raw = self.publish()
        self.collect(self.reader())
        for after in (False, True):
            (self.root / 'payload.json').write_bytes(raw)
            artifacts = self.reader().iter_stock_artifact_rows('finmind_price_adj', '2330')
            _, rows = next(artifacts)
            if after:
                next(rows)
            replacement = self.root / 'replacement.json'
            replacement.write_bytes(raw + b' ')
            replacement.replace(self.root / 'payload.json')
            with self.assertRaisesRegex(ValueError, 'artifact changed'):
                list(rows)

if __name__ == '__main__':
    unittest.main()
