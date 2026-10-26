"""Producer-side fixtures of the binding v2 published-head contract (tests only; never imported by src).

Mirrors the updater handoff 2026-10-05 section 3 Wave 4 (and the scratch prototype evidence_store.compact_entry):
  * evidence fields ``source_receipts`` / ``diagnostics`` of ``metadata`` and ``metadata.last_attempt`` whose
    canonical bytes exceed ``inline_max`` become ``{"$evidence": "stock-data-evidence-v1", "sha256", "size",
    "type", "items"}`` references to ``<store>/_evidence/<sha256>.json`` (bytes = canonical(value));
  * ``last_attempt`` fields canonical-equal to the metadata's are left out and listed (sorted) in
    ``last_attempt.same_as_metadata``;
  * legacy archives are stored as ``<store>/_manifests/<sha>.splice.json`` with one blob per route entry
    (``<store>/_evidence/blob/<sha>.bin``).
"""
from __future__ import annotations

import copy
import hashlib
import json
from pathlib import Path

EVIDENCE_FIELDS = ("source_receipts", "diagnostics")
ATTEMPT_FIELDS = (
    "coverage", "gaps", "errors", "error", "reason", "diagnostics", "requested_from_date",
    "effective_from_date", "expected_asof", "requested_to_date", "requested_scope", "collection_scope",
    "source_receipts", "snapshot_observation", "snapshot_complete", "observed_asof", "no_data_evidence",
    "history_unavailable", "due_scope", "pending_release_dates", "pending_release_evidence",
    "source_expected_asof", "source_resolved_demand")
V1, V2 = "stock-data-publication-v1", "stock-data-publication-v2"


def canonical(value) -> bytes:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"),
                      allow_nan=False).encode("utf-8")


def sha(raw: bytes) -> str:
    return hashlib.sha256(raw).hexdigest()


def evidence_reference(value, raw=None):
    raw = canonical(value) if raw is None else raw
    return {"$evidence": "stock-data-evidence-v1", "sha256": sha(raw), "size": len(raw),
            "type": "list" if isinstance(value, list) else "object", "items": len(value)}


def _externalize(container, objects, inline_max):
    result = dict(container)
    for field in EVIDENCE_FIELDS:
        value = result.get(field)
        if isinstance(value, (list, dict)) and "$evidence" not in (value if isinstance(value, dict) else {}):
            raw = canonical(value)
            if len(raw) > inline_max:
                objects[sha(raw)] = raw
                result[field] = evidence_reference(value, raw)
    return result


def compact_metadata(metadata, objects, *, inline_max=64 * 1024):
    """The v2 storage form of one entry's metadata (pure; evidence object bytes go to ``objects``)."""
    result = _externalize(metadata, objects, inline_max)
    attempt = result.get("last_attempt")
    if isinstance(attempt, dict):
        attempt = _externalize(attempt, objects, inline_max)
        same = sorted(field for field in attempt if field in ATTEMPT_FIELDS and field in result
                      and canonical(attempt[field]) == canonical(result[field]))
        for field in same:
            del attempt[field]
        if same:
            attempt["same_as_metadata"] = same
        result["last_attempt"] = attempt
    return result


def compact_entry(entry, objects, *, inline_max=64 * 1024):
    if not isinstance(entry.get("metadata"), dict):
        return copy.deepcopy(entry)
    return dict(copy.deepcopy(entry), metadata=compact_metadata(copy.deepcopy(entry["metadata"]), objects,
                                                                 inline_max=inline_max))


def v2_document(document, objects, *, inline_max=64 * 1024):
    return dict(document, schema=V2, datasets=[compact_entry(entry, objects, inline_max=inline_max)
                                               for entry in document["datasets"]])


def write_evidence(store: Path, objects) -> None:
    folder = Path(store) / "_evidence"
    folder.mkdir(parents=True, exist_ok=True)
    for digest, raw in objects.items():
        (folder / (digest + ".json")).write_bytes(raw)


def splice_segments(raw: bytes, document):
    """Segments of a canonical archive: each dataset entry's bytes become a blob (others text).
    Returns (segments, blobs {sha: bytes})."""
    marker = b'"datasets":['
    start = raw.index(marker) + len(marker)
    cursor, segments, blobs = start, [], {}
    text_start = 0
    for index, entry in enumerate(document["datasets"]):
        if index:
            assert raw[cursor:cursor + 1] == b","
            cursor += 1
        piece = canonical(entry)
        assert raw[cursor:cursor + len(piece)] == piece, "archive is not canonical"
        if cursor > text_start:
            segments.append({"t": raw[text_start:cursor].decode("utf-8")})
        digest = sha(piece)
        blobs[digest] = piece
        segments.append({"b": digest, "n": len(piece)})
        cursor += len(piece)
        text_start = cursor
    if text_start < len(raw):
        segments.append({"t": raw[text_start:].decode("utf-8")})
    return segments, blobs


def write_splice_archive(store: Path, raw: bytes, document=None):
    """Store archive bytes ``raw`` as ``<store>/_manifests/<sha>.splice.json`` + blobs; returns the splice path."""
    document = json.loads(raw) if document is None else document
    segments, blobs = splice_segments(raw, document)
    blob_dir = Path(store) / "_evidence" / "blob"
    blob_dir.mkdir(parents=True, exist_ok=True)
    for digest, piece in blobs.items():
        path = blob_dir / (digest + ".bin")
        if not path.exists():
            path.write_bytes(piece)
    splice = {"$archive": "stock-data-archive-splice-v1", "sha256": sha(raw), "size": len(raw),
              "segments": segments}
    manifests = Path(store) / "_manifests"
    manifests.mkdir(parents=True, exist_ok=True)
    path = manifests / (sha(raw) + ".splice.json")
    path.write_bytes(canonical(splice))
    return path
