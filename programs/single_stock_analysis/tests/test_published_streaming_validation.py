from __future__ import annotations

import hashlib
import json
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch


SRC = Path(__file__).resolve().parents[1] / 'src'
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

import stock_data_sources as sources


class PublishedStreamingValidationTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)

    def reader(self, raw, **overrides):
        (self.root / 'payload.json').write_bytes(raw)
        ref = dict(route_id='finmind_price_adj', path='payload.json',
                   sha256=hashlib.sha256(raw).hexdigest(), size=len(raw),
                   status='SUCCESS')
        ref.update(overrides)
        manifest = dict(schema='stock-data-publication-v1', datasets=[ref])
        (self.root / 'manifest.json').write_bytes(json.dumps(manifest).encode('utf-8'))
        return sources.PublishedSources([sources.SourceSpec(
            'manifest_json', self.root, 'manifest.json', 'fixture')])

    def test_validation_and_iteration_use_two_payload_opens(self):
        raw = ('{"rows":[{"stock_id":"ignored"}],"data":[{"stock_id":"2330",'
               '"name":"\\u53f0"}],"fetched_at":"2026-09-20"} \n').encode('utf-8')
        reader = self.reader(raw)
        original_open = Path.open
        opens = []

        def counted_open(path, *args, **kwargs):
            if path == self.root / 'payload.json':
                opens.append(path)
            return original_open(path, *args, **kwargs)

        with patch.object(sources, '_verify_file', side_effect=AssertionError('extra scan')):
            with patch.object(Path, 'open', counted_open), patch.object(sources, '_CHUNK_SIZE', 7):
                artifacts = reader.iter_artifact_rows('finmind_price_adj')
                artifact, rows = next(artifacts)
                self.assertEqual(len(opens), 1)
                self.assertEqual(artifact.content_sha256, hashlib.sha256(raw).hexdigest())
                self.assertEqual(artifact.metadata['selected_ref']['size'], len(raw))
                self.assertEqual(artifact.metadata['payload_metadata'], {'fetched_at': '2026-09-20'})
                self.assertEqual(list(rows), [{'stock_id': '2330', 'name': '\u53f0'}])
                self.assertEqual(list(artifacts), [])
                self.assertEqual(len(opens), 2)
        self.assertEqual(reader.diagnostics, [])

    def test_bad_hash_or_size_exposes_no_artifacts(self):
        for overrides, message in [({'sha256': '0' * 64}, 'hash mismatch'),
                                   ({'size': 999}, 'size mismatch'),
                                   ({'size': True}, 'size mismatch')]:
            with self.subTest(overrides=overrides):
                reader = self.reader(b'[{"stock_id":"2330"}]', **overrides)
                self.assertEqual(list(reader.iter_artifact_rows('finmind_price_adj')), [])
                self.assertIn(message, reader.diagnostics[-1])
                self.assertEqual(reader.descriptor()['artifacts'], [])

    def test_invalid_json_exposes_no_artifacts(self):
        for raw in [b'[{"stock_id":"2330"},]', b'{"data":[],"data":[]}',
                    b'[{"x":1,"x":2}]', b'[NaN]', b'[] trailing', b'["\xff"]']:
            with self.subTest(raw=raw):
                reader = self.reader(raw)
                self.assertEqual(list(reader.iter_artifact_rows('finmind_price_adj')), [])
                self.assertTrue(reader.diagnostics)
                self.assertEqual(reader.descriptor()['artifacts'], [])

    def test_replacement_between_passes_rejected_before_first_row(self):
        reader = self.reader(b'[{"stock_id":"2330"}]')
        artifacts = reader.iter_artifact_rows('finmind_price_adj')
        _, rows = next(artifacts)
        replacement = self.root / 'replacement.json'
        replacement.write_bytes(b'[{"stock_id":"9999"},{"stock_id":"8888"}]')
        replacement.replace(self.root / 'payload.json')
        with self.assertRaisesRegex(ValueError, 'artifact changed before parsing'):
            next(rows)

    def test_optional_and_required_missing_hash_size(self):
        reader = self.reader(b'[]', sha256=None, size=None)
        artifact, rows = next(reader.iter_artifact_rows('finmind_price_adj'))
        self.assertEqual(list(rows), [])
        self.assertEqual(artifact.content_sha256, hashlib.sha256(b'[]').hexdigest())
        with self.assertRaisesRegex(ValueError, 'artifact hash/size missing'):
            sources._validate_rows(self.root, {'path': 'payload.json'}, set(), {})


if __name__ == '__main__':
    unittest.main()
