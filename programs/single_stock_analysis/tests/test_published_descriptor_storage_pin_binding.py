"""Pinned descriptors without read caps or duplicate copies, with a stronger pin binding (2026-10-05).

C1: a pin file is verified whatever its size (one streamed pass: SHA-256, size, file identity, strict JSON) and a
manifest's datasets must equal the restored catalog entry by entry (binding check).  D2: the v2 storage form keeps a
manifest entry once instead of twice, with descriptor_sha256 and the session digest unchanged.  D4: session and
calibration descriptor files are compact JSON.  D5: from_descriptor keeps one private copy and never mutates its
input.  Old (v1, indented) session files stay readable.
"""
import copy
import hashlib
import io
import json
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import stock_data_sources as sources
import published_single_stock_projection as projection


def reseal(descriptor):
    descriptor.pop('descriptor_sha256', None)
    descriptor['descriptor_sha256'] = sources._sha(sources._canonical(descriptor))
    return descriptor


class PinFixture(unittest.TestCase):
    def setUp(self):
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        self.root = (Path(temp.name) / 'shared_market_data').resolve()
        self.current = self.root / '_metadata' / 'stock_data_current.json'
        self.current.parent.mkdir(parents=True)
        (self.root / '_manifests').mkdir()
        canonical_root = patch.object(sources, '_CANONICAL_SHARED_MARKET_DATA_ROOT', self.root)
        canonical_root.start()
        self.addCleanup(canonical_root.stop)
        datasets = []
        for route, day in (('finmind_price_adj', '2026-09-29'), ('finmind_government_bank', '2026-09-29')):
            payload = json.dumps({'data': [{'stock_id': '2330', 'date': day, 'close': 100}]}).encode()
            (self.root / f'{route}.json').write_bytes(payload)
            receipts = [{'date': f'2026-09-{index % 28 + 1:02d}', 'row_count': index} for index in range(200)]
            datasets.append({'route_id': route, 'path': f'{route}.json', 'sha256': sources._sha(payload),
                             'size': len(payload), 'status': 'SUCCESS', 'record_count': 1,
                             'metadata': {'source_receipts': receipts,
                                          'last_attempt': {'status': 'SUCCESS', 'source_receipts': receipts}}})
        self.manifest = {'schema': 'stock-data-publication-v1', 'producer_id': 'fixture-producer',
                         'run_id': 'run-1', 'published_at': '2026-09-29T20:00:00+08:00', 'datasets': datasets}
        self.manifest_bytes = sources._canonical(self.manifest)
        self.current.write_bytes(self.manifest_bytes)
        self.archive = self.root / '_manifests' / (sources._sha(self.manifest_bytes) + '.json')
        self.archive.write_bytes(self.manifest_bytes)
        self.reader = sources.PublishedSources([
            sources.SourceSpec('manifest_json', self.root, self.current, 'shared_market_data')])
        self.snapshot = self.reader.snapshot_descriptor()


class DescriptorStorageTests(PinFixture):
    def test_storage_form_keeps_each_entry_once_and_the_same_hash(self):
        stored = sources.descriptor_storage(self.snapshot)
        self.assertEqual(stored['descriptor_storage'], sources.DESCRIPTOR_STORAGE_V2)
        self.assertEqual(stored['descriptor_sha256'], self.snapshot['descriptor_sha256'])
        pairs = stored['pinned_sources'][0]['entries']['finmind_price_adj']
        self.assertEqual([len(pair) for pair in pairs], [1])
        v1, v2 = sources._canonical(self.snapshot), sources._canonical(stored)
        self.assertLess(len(v2), 0.6 * len(v1))
        loaded = json.loads(v2)
        expanded = sources.descriptor_from_storage(loaded)
        self.assertEqual(sources._canonical(expanded), v1)
        pair = expanded['pinned_sources'][0]['entries']['finmind_price_adj'][0]
        self.assertIs(pair[0], pair[1])                                  # one object, not a copy
        self.assertIs(sources.descriptor_storage(stored), stored)
        self.assertIs(sources.descriptor_from_storage(self.snapshot), self.snapshot)
        for value in (loaded, expanded, self.snapshot):
            restored = sources.PublishedSources.from_descriptor(value)
            self.assertEqual(restored.fingerprint, self.reader.fingerprint)
            self.assertEqual(sources.verify_descriptor(value)['status'], 'verified')
            self.assertEqual(sources.snapshot_descriptor_reference(value),
                             sources.snapshot_descriptor_reference(self.snapshot))
        self.assertEqual(restored.validate_pinned_context(loaded)['status'], 'verified')

    def test_storage_form_is_integrity_checked_on_the_expanded_form(self):
        stored = json.loads(sources._canonical(sources.descriptor_storage(self.snapshot)))
        changed = copy.deepcopy(stored)
        changed['pinned_sources'][0]['entries']['finmind_price_adj'][0][0]['status'] = 'PARTIAL'
        with self.assertRaisesRegex(ValueError, 'descriptor hash/schema mismatch'):
            sources.PublishedSources.from_descriptor(changed)
        for damage, message in ((dict(stored, descriptor_storage='storage-v3'), 'unknown descriptor storage'),):
            with self.assertRaisesRegex(ValueError, message):
                sources.PublishedSources.from_descriptor(damage)
        broken = copy.deepcopy(stored)
        broken['pinned_sources'][0]['entries']['finmind_price_adj'] = [[]]
        with self.assertRaisesRegex(ValueError, 'invalid stored pinned catalog pair'):
            sources.PublishedSources.from_descriptor(broken)
        # a pair whose ref is not its metadata (generation sources) is stored as a pair
        distinct = copy.deepcopy(self.snapshot)
        pair = distinct['pinned_sources'][0]['entries']['finmind_price_adj'][0]
        distinct['pinned_sources'][0]['entries']['finmind_price_adj'][0] = (dict(pair[0], extra=1), pair[1])
        folded = sources.descriptor_storage(reseal(distinct))
        self.assertEqual(len(folded['pinned_sources'][0]['entries']['finmind_price_adj'][0]), 2)
        self.assertEqual(sources._canonical(sources.descriptor_from_storage(folded)), sources._canonical(distinct))

    def test_from_descriptor_keeps_one_private_copy_and_never_mutates_its_input(self):
        before = copy.deepcopy(self.snapshot)
        restored = sources.PublishedSources.from_descriptor(self.snapshot)
        self.assertEqual(self.snapshot, before)
        state = restored._sources[0]
        supplied = self.snapshot['pinned_sources'][0]
        self.assertEqual(state['entries'], supplied['entries'])
        self.assertIsNot(state['entries'], supplied['entries'])
        state['entries']['finmind_price_adj'][0][1]['metadata']['source_receipts'].append({'id': 2})
        restored._record_route_runtime_error(state, 'finmind_price_adj', 'boom')
        self.assertEqual(self.snapshot, before)
        with self.assertRaisesRegex(ValueError, 'hash/schema mismatch'):
            sources.PublishedSources.from_descriptor(dict(before, diagnostics=['x']))


class SessionFileTests(PinFixture):
    def write_session(self, capsys_like=None):
        context = dict(source_mode='published', sources=self.reader, expected_asof='2026-09-29',
                       data_date='2026-09-29', fingerprint=self.reader.fingerprint,
                       snapshot_descriptor=self.snapshot)
        output = self.root.parent / 'published_session_descriptor.json'
        printed = io.StringIO()
        with patch.object(projection, 'pin_published_sources_context', lambda expected_asof=None: context), \
                patch.object(sys, 'argv', ['x', '--pin', '--descriptor-output', str(output)]), \
                patch('sys.stdout', printed):
            projection.main()
        return output, printed.getvalue().strip()

    def legacy_document(self):
        document = dict(schema='single-stock-published-session-v1', expected_asof='2026-09-29',
                        data_date='2026-09-29', snapshot_descriptor=self.snapshot)
        document['descriptor_sha256'] = projection._digest(document)
        return document

    def test_session_file_is_compact_v2_and_keeps_the_v1_digests(self):
        output, digest = self.write_session()
        raw = output.read_bytes()
        self.assertEqual(hashlib.sha256(raw).hexdigest(), digest)
        self.assertNotIn(b'\n  ', raw)
        self.assertTrue(raw.endswith(b'}\n'))
        stored = json.loads(raw)
        self.assertEqual(stored['snapshot_descriptor']['descriptor_storage'], sources.DESCRIPTOR_STORAGE_V2)
        legacy = self.legacy_document()
        self.assertEqual(stored['descriptor_sha256'], legacy['descriptor_sha256'])          # session digest unchanged
        self.assertEqual(stored['snapshot_descriptor']['descriptor_sha256'], self.snapshot['descriptor_sha256'])
        legacy_raw = (json.dumps(legacy, ensure_ascii=False, sort_keys=True, indent=2) + '\n').encode('utf-8')
        self.assertLess(len(raw), 0.5 * len(legacy_raw))
        context = projection.import_published_sources_context(output, digest, '2026-09-29')
        self.assertEqual(context['fingerprint'], self.reader.fingerprint)
        self.assertEqual(context['published_descriptor_sha256'], legacy['descriptor_sha256'])
        self.assertEqual(sources._canonical(context['snapshot_descriptor']), sources._canonical(self.snapshot))
        self.assertEqual(context['pin_validation']['status'], 'verified')

    def test_old_indented_v1_session_file_still_imports(self):
        legacy = self.legacy_document()
        raw = (json.dumps(legacy, ensure_ascii=False, sort_keys=True, indent=2) + '\n').encode('utf-8')
        path = self.root.parent / 'legacy_session.json'
        path.write_bytes(raw)
        context = projection.import_published_sources_context(path, hashlib.sha256(raw).hexdigest(), '2026-09-29')
        self.assertEqual(context['fingerprint'], self.reader.fingerprint)
        self.assertEqual(context['pin_validation']['status'], 'verified')

    def test_tampered_v2_session_is_rejected(self):
        output, _digest = self.write_session()
        stored = json.loads(output.read_bytes())
        stored['snapshot_descriptor']['pinned_sources'][0]['entries']['finmind_price_adj'][0][0]['status'] = 'X'
        raw = sources._canonical(stored)
        output.write_bytes(raw)
        with self.assertRaisesRegex(ValueError, 'published_session_descriptor_invalid'):
            projection.import_published_sources_context(output, hashlib.sha256(raw).hexdigest(), '2026-09-29')


class PinBindingTests(PinFixture):
    def resealed_session(self, change):
        """A self-consistent session whose catalog was edited after pinning (descriptor and session resealed)."""
        changed = copy.deepcopy(self.snapshot)
        change(changed)
        reseal(changed)
        document = dict(schema='single-stock-published-session-v1', expected_asof='2026-09-29',
                        data_date='2026-09-29', snapshot_descriptor=changed)
        document['descriptor_sha256'] = projection._digest(document)
        raw = sources._canonical(document)
        path = self.root.parent / 'resealed_session.json'
        path.write_bytes(raw)
        return changed, path, hashlib.sha256(raw).hexdigest()

    def test_resealed_catalog_edits_are_rejected_by_the_binding(self):
        def edit_metadata(descriptor):
            pair = descriptor['pinned_sources'][0]['entries']['finmind_government_bank'][0]
            pair[1]['metadata']['source_receipts'][0]['row_count'] = 9999      # pair shares one object

        def drop_route(descriptor):
            descriptor['pinned_sources'][0]['entries'].pop('finmind_government_bank')

        def add_entry(descriptor):
            entries = descriptor['pinned_sources'][0]['entries']['finmind_price_adj']
            entries.append(copy.deepcopy(entries[0]))

        def change_identity(descriptor):
            descriptor['pinned_sources'][0]['identity']['producer_id'] = 'other-producer'
            descriptor['sources'][0]['producer_id'] = 'other-producer'

        def change_ref_only(descriptor):
            pairs = descriptor['pinned_sources'][0]['entries']['finmind_price_adj']
            pairs[0] = (dict(pairs[0][0], status='PARTIAL'), pairs[0][1])

        for change, reason in ((edit_metadata, 'entry_changed'), (drop_route, 'unpinned_entry'),
                               (add_entry, 'pinned_entry_not_in_manifest'), (change_identity, 'identity_changed'),
                               (change_ref_only, 'ref_changed')):
            with self.subTest(change=change.__name__):
                changed, path, digest = self.resealed_session(change)
                restored = sources.PublishedSources.from_descriptor(changed)
                with self.assertRaisesRegex(ValueError, 'published_context_pin_binding_mismatch:' + reason):
                    restored.validate_pinned_context(changed)
                with self.assertRaises(ValueError) as caught:
                    projection.import_published_sources_context(path, digest, '2026-09-29')
                self.assertEqual(str(caught.exception), 'published_snapshot_pin_invalid')
                self.assertIn('published_context_pin_binding_mismatch', str(caught.exception.__cause__))

    def test_untouched_catalog_binds_on_the_archive_and_on_the_current_head(self):
        proof = self.reader.validate_pinned_context(self.snapshot)
        self.assertEqual(proof['verified_pin_paths'], (str(self.archive),))
        self.assertEqual(proof['pin_bytes_read'], len(self.manifest_bytes))
        self.archive.unlink()
        self.assertEqual(self.reader.validate_pinned_context(self.snapshot)['verified_pin_paths'],
                         (str(self.current),))

    def test_corrupt_json_with_a_matching_hash_is_rejected_after_the_hash_check(self):
        corrupt = self.manifest_bytes[:-1] + b',}'
        digest = sources._sha(corrupt)
        (self.root / '_manifests' / (digest + '.json')).write_bytes(corrupt)
        changed = copy.deepcopy(self.snapshot)
        state = changed['pinned_sources'][0]
        state['snapshot_id'] = state['identity']['manifest_sha256'] = digest
        changed['sources'][0].update(snapshot_id=digest, manifest_sha256=digest)
        changed['snapshot_fingerprint'] = sources._sha(json.dumps(
            [(state['source_id'], digest, state.get('error'))], ensure_ascii=True).encode())
        reseal(changed)
        restored = sources.PublishedSources.from_descriptor(changed)
        with self.assertRaisesRegex(ValueError, 'published_context_pin_json_invalid'):
            restored.validate_pinned_context(changed)

    def test_changed_file_still_reports_its_hash_mismatch_first(self):
        self.archive.write_bytes(self.manifest_bytes.replace(b'run-1', b'run-2'))
        with self.assertRaisesRegex(ValueError, 'published_context_pin_hash_mismatch'):
            self.reader.validate_pinned_context(self.snapshot)
        self.archive.write_bytes(b'not json at all')
        with self.assertRaisesRegex(ValueError, 'published_context_pin_hash_mismatch'):
            self.reader.validate_pinned_context(self.snapshot)


class PinWithoutByteCapTests(unittest.TestCase):
    """The pinned head is verified whatever its size, by one streamed pass (C1; formerly 64 MiB)."""

    def setUp(self):
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        self.root = (Path(temp.name) / 'shared_market_data').resolve()
        self.current = self.root / '_metadata' / 'stock_data_current.json'
        self.current.parent.mkdir(parents=True)
        (self.root / '_manifests').mkdir()
        canonical_root = patch.object(sources, '_CANONICAL_SHARED_MARKET_DATA_ROOT', self.root)
        canonical_root.start()
        self.addCleanup(canonical_root.stop)
        payload = b'{"data":[{"stock_id":"2330","date":"2026-09-29","close":100}]}'
        (self.root / 'payload.json').write_bytes(payload)
        # one dataset entry larger than the former 64 MiB cap (its receipts), plus insignificant whitespace
        receipts = 'r' * (65 * 1024 * 1024)
        manifest = sources._canonical({
            'schema': 'stock-data-publication-v1', 'producer_id': 'fixture-producer', 'run_id': 'run-big',
            'datasets': [{'route_id': 'finmind_price_adj', 'path': 'payload.json', 'sha256': sources._sha(payload),
                          'size': len(payload), 'status': 'SUCCESS',
                          'metadata': {'source_receipts': [receipts]}}]})
        self.manifest_bytes = manifest + b' ' * 4096
        self.current.write_bytes(self.manifest_bytes)
        self.archive = self.root / '_manifests' / (sources._sha(self.manifest_bytes) + '.json')
        self.archive.write_bytes(self.manifest_bytes)
        self.reader = sources.PublishedSources([
            sources.SourceSpec('manifest_json', self.root, self.current, 'shared_market_data')])
        self.snapshot = self.reader.snapshot_descriptor()

    def test_pin_larger_than_64_mib_is_verified_in_bounded_blocks(self):
        requests = []
        real_open = Path.open

        def recording_open(path, *args, **kwargs):
            handle = real_open(path, *args, **kwargs)
            if path.name.endswith('.json') and path.parent.name == '_manifests':
                real_read = handle.read

                def read(size=-1):
                    requests.append(size)
                    return real_read(size)
                handle.read = read
            return handle

        with patch.object(Path, 'open', recording_open):
            proof = self.reader.validate_pinned_context(self.snapshot)
        self.assertEqual(proof['status'], 'verified')
        self.assertEqual(proof['pin_bytes_read'], len(self.manifest_bytes))
        self.assertTrue(requests)
        self.assertTrue(all(0 < size <= sources._CHUNK_SIZE for size in requests), requests[:5])

    def test_changed_large_pin_is_still_rejected(self):
        self.archive.write_bytes(self.manifest_bytes[:-1] + b'\t')
        with self.assertRaisesRegex(ValueError, 'published_context_pin_hash_mismatch'):
            self.reader.validate_pinned_context(self.snapshot)


class StreamGrowthTests(unittest.TestCase):
    def test_a_large_value_is_decoded_a_logarithmic_number_of_times(self):
        value = 'x' * (24 * sources._CHUNK_SIZE)
        raw = json.dumps([value, 1, {'k': [value]}]).encode()
        stream = sources._JSONStream(io.BytesIO(raw))
        calls = []
        decode = stream.decoder.raw_decode

        def counting(text, index=0):
            calls.append(index)
            return decode(text, index)
        stream.decoder.raw_decode = counting
        self.assertEqual(list(stream.array()), [value, 1, {'k': [value]}])
        self.assertLess(len(calls), 30)                     # about 2 x log2(24) + 3, formerly about 2 x 24


if __name__ == '__main__':
    unittest.main()
