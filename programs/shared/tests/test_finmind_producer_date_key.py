"""finmind_producer date keys accept both spellings and never take a JSON number.

Moved unchanged from the retired ``test_shared_date_readers.py`` (see
``_retired/README.md``): ``finmind_producer.py`` is still a live shared module,
so its date-key test stays collected.
"""
from __future__ import annotations

import sys
from pathlib import Path

SHARED_DIR = Path(__file__).resolve().parents[1]
if str(SHARED_DIR) not in sys.path:
    sys.path.insert(0, str(SHARED_DIR))

import finmind_producer  # noqa: E402


def test_finmind_producer_date_key_never_takes_a_json_number():
    assert finmind_producer._date_key("2026-10-02") == "2026-10-02"
    assert finmind_producer._date_key("20261002") == "2026-10-02"
    assert finmind_producer._date_key("2026-10-02T00:00:00") == "2026-10-02"
    for value in (20261002, 20261002.0, True):
        assert finmind_producer._date_key(value) is None
