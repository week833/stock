"""pd_final M4: reports carry the pinned snapshot descriptor by reference, not by value.

The published snapshot descriptor (the whole pinned catalog with every manifest entry's metadata; 61 MB of canonical
JSON on the 2026-10-05 head) used to be copied into six places of a three-month summary (367 MB JSON) and twice into
every compact history snapshot.  Reports now carry ``{"schema": "single-stock-snapshot-descriptor-ref-v1",
"snapshot_fingerprint", "descriptor_sha256"}``; the descriptor's self-hash is verified whenever a context is pinned
or restored, so equality of two references still means the same descriptor.  Training packets keep the full
descriptor (the shared trainer stores it once per run).  Every write goes below tmp_path.
"""
from __future__ import annotations

import copy
import hashlib
import json
from pathlib import Path
from types import SimpleNamespace

import pytest

from published_single_stock_projection import (CURRENT_C0_ROUTE_IDS, _digest, import_published_sources_context,
                                               pin_published_sources_context)
from stock_data_sources import (SNAPSHOT_DESCRIPTOR_REF_SCHEMA, SourceSpec, is_snapshot_descriptor_reference,
                                snapshot_descriptor_reference)

ASOF = "2026-09-29"
STOCK = "2330"


def _published_root(root: Path, close: float = 100.0):
    root.mkdir(parents=True, exist_ok=True)
    raw = json.dumps([dict(stock_id=STOCK, date=ASOF, close=close)]).encode()
    (root / "price.json").write_bytes(raw)
    (root / "head.json").write_text(json.dumps(dict(schema="stock-data-publication-v1", datasets=[
        dict(route_id="finmind_price_adj", path="price.json", sha256=hashlib.sha256(raw).hexdigest(), size=len(raw),
             status="SUCCESS", metadata=dict(note="x" * 2048))])), encoding="utf-8")
    return [SourceSpec("manifest_json", root, root / "head.json")]


def _session(tmp_path: Path, name="pub", close=100.0):
    context = pin_published_sources_context(source_specs=_published_root(tmp_path / name, close))
    document = dict(schema="single-stock-published-session-v1", expected_asof=ASOF, data_date=ASOF,
                    snapshot_descriptor=context["snapshot_descriptor"])
    document["descriptor_sha256"] = _digest(document)
    path = tmp_path / f"{name}_session.json"
    path.write_text(json.dumps(document), encoding="utf-8")
    session = import_published_sources_context(path, hashlib.sha256(path.read_bytes()).hexdigest(), ASOF)
    return context, session


# ----------------------------------------------------------------------------------------------- the reference
def test_reference_names_the_descriptor_by_its_verified_self_hash(tmp_path):
    context, session = _session(tmp_path)
    descriptor = session["snapshot_descriptor"]
    assert "pinned_sources" in descriptor and isinstance(descriptor.get("descriptor_sha256"), str)
    ref = snapshot_descriptor_reference(descriptor)
    assert ref == {"schema": SNAPSHOT_DESCRIPTOR_REF_SCHEMA, "descriptor_schema": descriptor["schema"],
                   "snapshot_fingerprint": session["fingerprint"],
                   "descriptor_sha256": descriptor["descriptor_sha256"],
                   # unbind step 5 (plan 2.4): the reference also names the snapshot's data time
                   "published_at": descriptor["published_at"],
                   "price_actual_max_date": descriptor["price_actual_max_date"]}
    assert is_snapshot_descriptor_reference(ref) and not is_snapshot_descriptor_reference(descriptor)
    assert snapshot_descriptor_reference(ref) == ref                         # idempotent
    assert snapshot_descriptor_reference(copy.deepcopy(descriptor)) == ref    # same content -> same reference
    assert len(json.dumps(ref)) < 400 < len(json.dumps(descriptor))
    # another snapshot (different payload) -> another reference
    other = snapshot_descriptor_reference(_session(tmp_path, "pub2", close=101.0)[1]["snapshot_descriptor"])
    assert other["descriptor_sha256"] != ref["descriptor_sha256"]
    assert other["snapshot_fingerprint"] != ref["snapshot_fingerprint"]
    # no self-hash: named by its canonical content hash
    bare = {"fixture": True, "snapshot_fingerprint": "f" * 64}
    bare_ref = snapshot_descriptor_reference(bare)
    assert "descriptor_sha256" not in bare_ref and len(bare_ref["content_sha256"]) == 64
    assert snapshot_descriptor_reference(dict(bare, fixture=False))["content_sha256"] != bare_ref["content_sha256"]
    with pytest.raises(ValueError, match="snapshot_descriptor_invalid"):
        snapshot_descriptor_reference(["not", "a", "mapping"])


