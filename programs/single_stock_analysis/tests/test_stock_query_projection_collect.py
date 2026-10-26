"""C8: ProjectionQueryResult.collect() has no default row cap; a caller-chosen max_rows still guards."""
from __future__ import annotations

import pytest

import stock_query_projection as query_projection


def _result(rows):
    result = object.__new__(query_projection.ProjectionQueryResult)
    result.iter_rows = lambda: iter(rows)            # collect() materializes exactly what iter_rows() yields
    return result


def test_collect_returns_every_row_beyond_the_former_100k_default():
    assert not hasattr(query_projection, "_MAX_COLLECT_ROWS")
    rows = [{"sequence": index} for index in range(100_007)]
    collected = _result(rows).collect()
    assert collected == rows


def test_collect_keeps_an_explicit_caller_guard():
    rows = [{"sequence": index} for index in range(4)]
    assert _result(rows).collect(max_rows=4) == rows
    with pytest.raises(query_projection.ProjectionResultLimitError, match="collect_row_limit_exceeded"):
        _result(rows).collect(max_rows=3)
    for value in (True, -1, 1.5, "3"):
        with pytest.raises(ValueError, match="max_rows"):
            _result(rows).collect(max_rows=value)
