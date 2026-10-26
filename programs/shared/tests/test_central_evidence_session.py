from __future__ import annotations

import json
from pathlib import Path
import sys

SHARED_DIR = Path(__file__).resolve().parents[1]
if str(SHARED_DIR) not in sys.path:
    sys.path.insert(0, str(SHARED_DIR))

from central_evidence_session import CentralEvidenceSession


def test_session_reuses_scoped_json_and_sha_then_invalidates_on_identity_change(
    tmp_path: Path,
) -> None:
    evidence = tmp_path / "latest_manifest.json"
    evidence.write_text(json.dumps({"run_id": "run-1", "value": 1}), encoding="utf-8")
    session = CentralEvidenceSession()
    session.bind(receipt_run_id="run-1", expected_asof="2026-08-21", target="2026-08-24")

    first = session.read_json(evidence, "receipt")
    first_sha = session.sha256(evidence)
    stable = session.stats()
    assert session.read_json(evidence, "receipt") == first
    assert session.sha256(evidence) == first_sha
    assert session.stats() == stable

    session.bind(receipt_run_id="run-2", expected_asof="2026-08-22", target="2026-08-25")
    assert session.read_json(evidence, "receipt")["run_id"] == "run-1"
    scoped = session.stats()
    assert scoped["json_entries"] == stable["json_entries"] + 1

    evidence.write_text(json.dumps({"run_id": "run-2", "value": 22}), encoding="utf-8")
    changed = session.read_json(evidence, "receipt")
    assert changed["run_id"] == "run-2"
    assert session.sha256(evidence) != first_sha
    assert session.stats()["json_loads"] == scoped["json_loads"] + 1
