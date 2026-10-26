"""shared/stock_data_paths reads v1 and v2 published heads (binding v2 contract, v2_impl 2026-10-05).

``_validate_common_publication`` reads only entry-level path / sha256 / size, which a v2 head keeps unchanged; the
v2 evidence references in ``metadata`` are not its concern.  Any other schema is still refused.
"""
from __future__ import annotations

import hashlib
import json
from pathlib import Path
import sys
from types import SimpleNamespace

import pytest


SHARED_DIR = Path(__file__).resolve().parents[1]
if str(SHARED_DIR) not in sys.path:
    sys.path.insert(0, str(SHARED_DIR))

import stock_data_paths


def _publication(root: Path, schema: str) -> None:
    core = [route for route in stock_data_paths.CENTRAL_SINGLE_STOCK_SESSION_ROUTE_IDS
            if route not in ("finmind_stock_info", "official_finance_reporting_deadline", "official_calendar")]
    datasets = []
    for route in core:
        raw = json.dumps({"data": [{"stock_id": "2330", "date": "2026-09-30"}]}).encode("utf-8")
        (root / "_objects").mkdir(parents=True, exist_ok=True)
        (root / "_objects" / f"{route}.json").write_bytes(raw)
        reference = {"$evidence": "stock-data-evidence-v1", "sha256": "a" * 64, "size": 70000, "type": "list",
                     "items": 900}
        datasets.append({"route_id": route, "path": f"_objects/{route}.json", "status": "SUCCESS",
                         "sha256": hashlib.sha256(raw).hexdigest(), "size": len(raw),
                         "metadata": {"source_receipts": reference,
                                      "last_attempt": {"status": "SUCCESS", "same_as_metadata": ["source_receipts"]}}})
    (root / "_metadata").mkdir(parents=True, exist_ok=True)
    (root / "_metadata" / "stock_data_current.json").write_text(
        json.dumps({"schema": schema, "datasets": datasets}), encoding="utf-8")


@pytest.mark.parametrize("schema", ["stock-data-publication-v1", "stock-data-publication-v2"])
def test_common_publication_accepts_both_head_schemas(tmp_path, schema):
    _publication(tmp_path, schema)
    assert stock_data_paths._validate_common_publication(SimpleNamespace(root=tmp_path)) is True


def test_common_publication_refuses_an_unknown_schema(tmp_path):
    _publication(tmp_path, "stock-data-publication-v3")
    with pytest.raises(stock_data_paths.SharedDataPathError, match="schema"):
        stock_data_paths._validate_common_publication(SimpleNamespace(root=tmp_path))
