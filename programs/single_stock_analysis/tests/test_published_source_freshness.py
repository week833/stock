"""Single adjusted-price source freshness, without provider or forecast calls."""
import unittest
from types import SimpleNamespace
from published_single_stock_projection import _select


def source(name, days):
    return SimpleNamespace(source_id=name, snapshot_id=name+'-snapshot',
        content_sha256='a'*64, status='SUCCESS', payload=[
            dict(stock_id='2330', date=day, close=100+i)
            for i, day in enumerate(days)])


def select(*artifacts):
    # The projection streams rows per artifact and releases the route afterwards.
    sources = SimpleNamespace(
        iter_artifact_rows=lambda route: ((item, iter(item.payload)) for item in artifacts),
        release=lambda route=None: None,
    )
    return _select(sources, 'finmind_price_adj', '2330', '2026-09-01', '2026-09-11')


class PublishedSourceFreshnessTests(unittest.TestCase):
    def test_new_short_source_wins_without_splicing_old_history(self):
        result = select(source('twstock_updater', ['2026-09-07','2026-09-08','2026-09-09']),
                        source('shared_publication_v2', ['2026-09-10','2026-09-11']))
        self.assertEqual([r['date'] for r in result['records']], ['2026-09-10','2026-09-11'])
        self.assertEqual([r['close'] for r in result['records']], [100,101])
        self.assertEqual([s['source_id'] for s in result['sources']], ['shared_publication_v2'])

    def test_same_latest_uses_length_then_priority_and_excludes_future(self):
        with self.subTest(case='longer_same_latest'):
            result = select(source('first', ['2026-09-11','2026-09-12']),
                            source('second', ['2026-09-10','2026-09-11']))
            self.assertEqual([s['source_id'] for s in result['sources']], ['second'])
            self.assertEqual([r['date'] for r in result['records']], ['2026-09-10','2026-09-11'])
        with self.subTest(case='same_length_priority'):
            result = select(source('first', ['2026-09-10','2026-09-11']),
                            source('second', ['2026-09-10','2026-09-11','2026-09-12']))
            self.assertEqual([s['source_id'] for s in result['sources']], ['first'])
            self.assertTrue(all(r['date'] <= '2026-09-11' for r in result['records']))


if __name__ == '__main__':
    unittest.main()
