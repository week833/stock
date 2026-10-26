"""Pinned shared manifests survive current-head advancement without weaker checks."""
from __future__ import annotations

import copy
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


class PublishedManifestArchivePinTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = (Path(self.temp.name) / 'shared_market_data').resolve()
        self.current = self.root / '_metadata' / 'stock_data_current.json'
        self.current.parent.mkdir(parents=True)
        self.archive_dir = self.root / '_manifests'
        self.archive_dir.mkdir()
        canonical_root = patch.object(sources, '_CANONICAL_SHARED_MARKET_DATA_ROOT', self.root)
        canonical_root.start()
        self.addCleanup(canonical_root.stop)
        self.payload = b'{"data":[{"stock_id":"2330","date":"2026-09-29","close":100}]}'
        self.payload_path = self.root / 'payload.json'
        self.payload_path.write_bytes(self.payload)
        self.manifest = {
            'schema': 'stock-data-publication-v1',
            'producer_id': 'fixture-producer',
            'run_id': 'run-old',
            'datasets': [{
                'route_id': 'finmind_price_adj', 'path': 'payload.json',
                'sha256': sources._sha(self.payload), 'size': len(self.payload),
                'status': 'SUCCESS',
            }],
        }
        self.manifest_bytes = sources._canonical(self.manifest)
        self.current.write_bytes(self.manifest_bytes)
        self.reader = sources.PublishedSources([
            sources.SourceSpec('manifest_json', self.root, self.current, 'shared_market_data'),
        ])
        self.snapshot = self.reader.snapshot_descriptor()
        self.archive = self.archive_dir / (sources._sha(self.manifest_bytes) + '.json')
        self.archive.write_bytes(self.manifest_bytes)

    def advance_current(self):
        advanced = dict(self.manifest, run_id='run-new')
        self.current.write_bytes(sources._canonical(advanced))

    def reseal(self, descriptor):
        descriptor.pop('descriptor_sha256', None)
        descriptor['descriptor_sha256'] = sources._sha(sources._canonical(descriptor))
        return descriptor

    def test_current_advances_and_restored_catalog_stays_pinned(self):
        self.advance_current()
        restored = sources.PublishedSources.from_descriptor(self.snapshot)
        proof = restored.validate_pinned_context(self.snapshot)
        self.assertEqual(proof['status'], 'verified')
        self.assertEqual(proof['snapshot_fingerprint'], self.reader.fingerprint)
        self.assertEqual(proof['verified_pin_paths'], (str(self.archive),))
        self.assertEqual(proof['pin_bytes_read'], len(self.manifest_bytes))
        self.assertFalse(proof['large_artifacts_verified'])
        self.assertEqual(restored.snapshot_descriptor(), self.snapshot)

    def test_session_import_uses_old_archive_after_current_advances(self):
        document = {
            'schema': 'single-stock-published-session-v1',
            'expected_asof': '2026-09-29', 'data_date': '2026-09-29',
            'snapshot_descriptor': self.snapshot,
        }
        self.reseal(document)
        raw = sources._canonical(document)
        session_path = self.root.parent / 'session.json'
        session_path.write_bytes(raw)
        self.advance_current()
        context = projection.import_published_sources_context(session_path, sources._sha(raw))
        self.assertEqual(sources._canonical(context['snapshot_descriptor']),
                         sources._canonical(self.snapshot))
        self.assertEqual(context['fingerprint'], self.reader.fingerprint)
        self.assertEqual(context['pin_validation']['verified_pin_paths'], (str(self.archive),))
        with self.assertRaisesRegex(ValueError, 'published_descriptor_file_hash_mismatch'):
            projection.import_published_sources_context(session_path, '0' * 64)

    def test_valid_archive_does_not_require_current_file(self):
        self.current.unlink()
        self.assertEqual(self.reader.validate_pinned_context(self.snapshot)['status'], 'verified')

    def test_missing_archive_falls_back_only_to_matching_current(self):
        self.archive.unlink()
        proof = self.reader.validate_pinned_context(self.snapshot)
        self.assertEqual(proof['verified_pin_paths'], (str(self.current),))
        self.assertFalse(proof['repinned'])
        self.assertEqual(proof['version_labels'], [])
        old_fingerprint = self.reader.fingerprint
        self.advance_current()
        # Converted gate (plan 2.4, unbind step 5): the mutable head was rewritten and no archive holds the pinned
        # bytes -> the reader re-pins the latest head (labelled), instead of raising pin_hash_mismatch.
        proof = self.reader.validate_pinned_context(self.snapshot)
        new_sha = sources._sha(self.current.read_bytes())
        self.assertTrue(proof['repinned'])
        self.assertTrue(proof['descriptor_refreshed'])
        self.assertEqual(proof['verified_pin_paths'], (str(self.current),))
        self.assertEqual(proof['snapshot_fingerprint'], self.reader.fingerprint)
        self.assertNotEqual(self.reader.fingerprint, old_fingerprint)
        (label,) = proof['version_labels']
        self.assertEqual(label['code'], 'head_updated_after_pin')
        self.assertEqual(label['recorded']['manifest_sha256'], sources._sha(self.manifest_bytes))
        self.assertEqual(label['actual']['manifest_sha256'], new_sha)
        self.assertEqual(self.reader.snapshot_descriptor()['sources'][0]['publication_run_id'], 'run-new')

    def test_corrupt_archive_never_falls_back_to_matching_current(self):
        changed = self.manifest_bytes.replace(b'run-old', b'run-new')
        self.assertEqual(len(changed), len(self.manifest_bytes))
        self.archive.write_bytes(changed)
        with self.assertRaisesRegex(ValueError, 'published_context_pin_hash_mismatch'):
            self.reader.validate_pinned_context(self.snapshot)

    def test_archive_directory_never_falls_back(self):
        self.archive.unlink()
        self.archive.mkdir()
        with self.assertRaises(OSError):
            self.reader.validate_pinned_context(self.snapshot)

    def test_archive_escape_rejected_even_if_current_matches(self):
        original_resolve = Path.resolve
        escaped = self.root.parent / 'outside.json'

        def resolve(path, *args, **kwargs):
            if path == self.archive:
                return escaped
            return original_resolve(path, *args, **kwargs)

        with patch.object(Path, 'resolve', autospec=True, side_effect=resolve):
            with self.assertRaisesRegex(ValueError, 'published_context_pin_path_invalid'):
                self.reader.validate_pinned_context(self.snapshot)

    def test_archive_changed_during_read_rejected(self):
        with patch.object(sources, '_file_identity', side_effect=[(1,), (2,)]):
            with self.assertRaisesRegex(ValueError, 'published_context_pin_changed_during_read'):
                self.reader.validate_pinned_context(self.snapshot)

    def test_noncanonical_source_identity_keeps_existing_current_check(self):
        for mismatch in ('source_id', 'root', 'manifest_path', 'kind'):
            with self.subTest(mismatch=mismatch):
                self.current.write_bytes(self.manifest_bytes)
                source_id = 'other-source' if mismatch == 'source_id' else 'shared_market_data'
                kind = 'central' if mismatch == 'kind' else 'manifest_json'
                manifest_path = self.current
                if mismatch == 'manifest_path':
                    manifest_path = self.root / '_metadata' / 'another_current.json'
                    manifest_path.write_bytes(self.manifest_bytes)
                reader = sources.PublishedSources([
                    sources.SourceSpec(kind, self.root, manifest_path, source_id),
                ])
                pinned = reader.snapshot_descriptor()
                advanced = sources._canonical(dict(self.manifest, run_id='run-new'))
                manifest_path.write_bytes(advanced)
                canonical = (self.root.parent / 'other-root' if mismatch == 'root' else self.root)
                with patch.object(sources, '_CANONICAL_SHARED_MARKET_DATA_ROOT', canonical):
                    if mismatch == 'kind':
                        # 'central' is a retired source kind (current-only redesign): it never pins, so its
                        # snapshot is refused before any head comparison (see test_retired_source_kinds_...).
                        with self.assertRaisesRegex(ValueError, 'invalid pinned source kind'):
                            reader.validate_pinned_context(pinned)
                        continue
                    # Converted gate (plan 2.4): a non-canonical source still never reads an archive, but its
                    # rewritten mutable head is now re-pinned and labelled instead of raising.
                    proof = reader.validate_pinned_context(pinned)
                    self.assertTrue(proof['repinned'])
                    self.assertEqual(proof['verified_pin_paths'], (str(manifest_path.resolve()),))
                    self.assertEqual([label['code'] for label in proof['version_labels']],
                                     ['head_updated_after_pin'])
                    self.assertEqual(proof['version_labels'][0]['actual']['manifest_sha256'],
                                     sources._sha(advanced))

    def test_retired_source_kinds_cannot_pin_or_restore(self):
        for kind in ('central', 'shared_publication_v2'):
            with self.subTest(kind=kind):
                reader = sources.PublishedSources([
                    sources.SourceSpec(kind, self.root, self.current, 'shared_market_data'),
                ])
                self.assertEqual(reader._sources[0]['entries'], {})
                self.assertIn('unknown source kind', reader._sources[0]['error'])
                with self.assertRaisesRegex(ValueError, 'invalid pinned source kind'):
                    sources.PublishedSources.from_descriptor(reader.snapshot_descriptor())

    def test_descriptor_hash_and_resealed_catalog_tampering_rejected(self):
        unsigned_change = copy.deepcopy(self.snapshot)
        unsigned_change['snapshot_fingerprint'] = '0' * 64
        with self.assertRaisesRegex(ValueError, 'descriptor hash/schema mismatch'):
            self.reader.validate_pinned_context(unsigned_change)

        for field, value in (('path', '../outside.json'), ('sha256', '0' * 64), ('size', -1)):
            with self.subTest(field=field):
                changed = copy.deepcopy(self.snapshot)
                ref = changed['pinned_sources'][0]['entries']['finmind_price_adj'][0][0]
                ref[field] = value
                self.reseal(changed)
                if field != 'sha256':
                    with self.assertRaises(ValueError):
                        self.reader.validate_pinned_context(changed)
                    continue
                # Converted gate (plan 2.4, catalog_mismatch -> label): an honest reader handed a resealed, edited
                # descriptor verifies its own catalog and labels the difference; the edited catalog is not used.
                proof = self.reader.validate_pinned_context(changed)
                self.assertEqual([label['code'] for label in proof['version_labels']], ['catalog_mismatch'])
                self.assertTrue(proof['descriptor_refreshed'])
                self.assertEqual(proof['snapshot_fingerprint'], self.reader.fingerprint)
                # The tamper itself still fails closed wherever the edited catalog would be read: a reader restored
                # from it is not bound to the verified manifest bytes.
                with self.assertRaisesRegex(ValueError, 'published_context_pin_binding_mismatch:'):
                    sources.PublishedSources.from_descriptor(changed).validate_pinned_context(changed)

    def test_context_catalog_tampering_rejected(self):
        changed = sources.PublishedSources.from_descriptor(self.snapshot)
        changed._sources[0]['entries']['finmind_price_adj'][0][0]['sha256'] = '0' * 64
        context = {
            'source_mode': 'published', 'snapshot_descriptor': self.snapshot, 'sources': changed,
        }
        with self.assertRaisesRegex(ValueError, 'published_snapshot_pin_invalid'):
            projection.validate_published_context_pin(context)

    def test_archive_pin_preserves_payload_hash_validation(self):
        self.reader.artifacts('finmind_price_adj')
        descriptor = self.reader.descriptor()
        self.payload_path.write_bytes(self.payload.replace(b'100', b'900'))
        proof = self.reader.validate_pinned_context(self.snapshot)
        self.assertFalse(proof['large_artifacts_verified'])
        self.assertEqual(sources.verify_descriptor(descriptor)['status'], 'invalid')
        self.assertEqual(list(self.reader.iter_stock_artifact_rows('finmind_price_adj', '2330')), [])
        self.assertIn('hash mismatch', self.reader.diagnostics[-1])

    # --- unbind step 5 (plan 2.4): version gates are labels, integrity gates still raise -------------------------

    def test_tampered_content_addressed_archive_still_raises_after_head_advances(self):
        self.advance_current()
        changed = self.manifest_bytes.replace(b'run-old', b'run-bad')
        self.assertEqual(len(changed), len(self.manifest_bytes))
        self.archive.write_bytes(changed)
        with self.assertRaisesRegex(ValueError, 'published_context_pin_hash_mismatch'):
            self.reader.validate_pinned_context(self.snapshot)
        context = {'source_mode': 'published', 'snapshot_descriptor': self.snapshot, 'sources': self.reader}
        with self.assertRaises(sources.C0IntegrityError) as caught:
            projection.validate_published_context_pin(context)
        self.assertEqual(str(caught.exception), 'published_snapshot_pin_invalid')
        self.assertIn('published_context_pin_hash_mismatch', caught.exception.reason)
        self.assertNotIn('pin_validation', context)

    def test_missing_content_addressed_pin_repins_the_current_head(self):
        reader = sources.PublishedSources([
            sources.SourceSpec('manifest_json', self.root, self.archive, 'shared_market_data'),
        ])
        pinned = reader.snapshot_descriptor()
        self.archive.unlink()
        self.advance_current()
        proof = reader.validate_pinned_context(pinned)
        self.assertTrue(proof['repinned'])
        self.assertEqual(proof['verified_pin_paths'], (str(self.current),))
        (label,) = proof['version_labels']
        self.assertEqual(label['code'], 'pinned_snapshot_missing')
        self.assertEqual(label['actual']['manifest_sha256'], sources._sha(self.current.read_bytes()))
        self.assertEqual(label['detail']['repinned_head_path'], str(self.current.resolve()))

    def test_repin_fails_closed_when_the_latest_head_is_not_valid(self):
        self.archive.unlink()
        self.current.write_bytes(b'{"schema": "not-a-publication"}')
        with self.assertRaisesRegex(ValueError, 'published_context_repin_failed'):
            self.reader.validate_pinned_context(self.snapshot)

    def test_session_import_repins_and_labels_when_its_head_is_gone(self):
        document = {
            'schema': 'single-stock-published-session-v1',
            'expected_asof': '2026-09-29', 'data_date': '2026-09-29',
            'snapshot_descriptor': self.snapshot,
        }
        self.reseal(document)
        raw = sources._canonical(document)
        session_path = self.root.parent / 'session.json'
        session_path.write_bytes(raw)
        self.archive.unlink()
        self.advance_current()
        context = projection.import_published_sources_context(session_path, sources._sha(raw), '2026-09-30')
        codes = [label['code'] for label in context['version_labels']]
        self.assertEqual(codes, ['requested_asof_differs', 'head_updated_after_pin'])
        self.assertEqual(context['expected_asof'], '2026-09-29')
        self.assertEqual(context['data_date'], '2026-09-29')
        self.assertEqual(context['fingerprint'], context['sources'].fingerprint)
        self.assertNotEqual(context['fingerprint'], self.reader.fingerprint)
        self.assertEqual(context['snapshot_descriptor']['sources'][0]['publication_run_id'], 'run-new')
        self.assertEqual(context['pin_validation']['status'], 'verified')

    def test_integrity_retry_rehashes_an_artifact_touched_with_the_same_bytes(self):
        artifacts = self.reader.iter_stock_artifact_rows('finmind_price_adj', '2330')
        _, rows = next(artifacts)
        stat = self.payload_path.stat()
        self.payload_path.write_bytes(self.payload)                         # same bytes, new identity
        os.utime(self.payload_path, ns=(stat.st_atime_ns, stat.st_mtime_ns + 5_000_000_000))
        self.assertEqual([row['close'] for row in rows], [100])
        artifacts = self.reader.iter_stock_artifact_rows('finmind_price_adj', '2330')
        _, rows = next(artifacts)
        self.payload_path.write_bytes(self.payload.replace(b'100', b'900'))  # other bytes, same length
        os.utime(self.payload_path, ns=(stat.st_atime_ns, stat.st_mtime_ns + 9_000_000_000))
        with self.assertRaisesRegex(ValueError, 'artifact changed'):
            list(rows)

    def test_descriptor_and_reference_carry_the_data_time(self):
        manifest = dict(self.manifest, published_at='2026-09-29T10:00:00+00:00')
        manifest['datasets'] = [dict(manifest['datasets'][0], actual_max_date='20260929')]
        self.current.write_bytes(sources._canonical(manifest))
        reader = sources.PublishedSources([
            sources.SourceSpec('manifest_json', self.root, self.current, 'shared_market_data'),
        ])
        for descriptor in (reader.descriptor(), reader.snapshot_descriptor()):
            self.assertEqual(descriptor['published_at'], '2026-09-29T10:00:00+00:00')
            self.assertEqual(descriptor['price_actual_max_date'], '2026-09-29')
            unsigned = dict(descriptor)
            digest = unsigned.pop('descriptor_sha256')
            self.assertEqual(digest, sources._sha(sources._canonical(unsigned)))      # definition unchanged
        snapshot = reader.snapshot_descriptor()
        self.assertEqual(sources.PublishedSources.from_descriptor(snapshot).snapshot_descriptor(), snapshot)
        ref = sources.snapshot_descriptor_reference(snapshot)
        self.assertEqual((ref['published_at'], ref['price_actual_max_date'], ref['descriptor_sha256']),
                         ('2026-09-29T10:00:00+00:00', '2026-09-29', snapshot['descriptor_sha256']))
        older = {key: value for key, value in snapshot.items()
                 if key not in ('published_at', 'price_actual_max_date')}
        self.assertEqual(sources.snapshot_descriptor_reference(older)['price_actual_max_date'], '2026-09-29')


if __name__ == '__main__':
    unittest.main()
