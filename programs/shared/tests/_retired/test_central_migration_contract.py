from __future__ import annotations

import copy
from dataclasses import dataclass
import hashlib
import json
from pathlib import Path
import sys
from types import MappingProxyType, SimpleNamespace
from typing import Any

import pytest


SHARED_DIR = Path(__file__).resolve().parents[1]
if str(SHARED_DIR) not in sys.path:
    sys.path.insert(0, str(SHARED_DIR))

import central_publication_reader as reader


ROUTE_ID = "finmind_price_adj"
DATASET = "TaiwanStockPriceAdj"
PROVIDER = "finmind"
ROUTE_SCHEMA = "fixture-route-schema-v1"
PIT_POLICY = "fixture_pit_policy"
COVERAGE_POLICY = "fixture_coverage_policy"
EXPECTED_ASOF = "2026-08-21"
RECEIPT_RUN_ID = "fixture-run-20260821"
PUBLICATION_RUN_ID = "fixture-publication-20260821"
SOURCE_RUN_ID = "fixture-source-20260821"
PUBLICATION_DIR = ("providers", "finmind", "json")

_ISOLATED_ENVIRONMENT = (
    reader.EXPECTED_ASOF_ENV,
    reader.CENTRAL_READY_ENV,
    reader.SHARED_RAW_NETWORK_DISABLED_ENV,
    reader.LEGACY_FINMIND_OPT_IN_ENV,
    "DStock_CENTRAL_PUBLICATION_MODE",
    "DStock_CENTRAL_REGISTRY_PATH",
)


@pytest.fixture(autouse=True)
def session_validation_calls(monkeypatch: pytest.MonkeyPatch) -> list[Any]:
    for name in _ISOLATED_ENVIRONMENT:
        monkeypatch.delenv(name, raising=False)
    monkeypatch.setattr(reader, "_PUBLICATION_CACHE", {})
    # Registry-wide session validation needs the full Central registry and is
    # outside what these isolated publication fixtures describe.  Replace it
    # with an identity function and record the calls so each test can still
    # prove the reader revalidates the frozen session before reading bytes.
    calls: list[Any] = []

    def validate_session(session: Any) -> Any:
        calls.append(session)
        return session

    monkeypatch.setattr(reader, "validate_central_evidence_session", validate_session)
    return calls


def _canonical_rows_hash(rows: list[dict[str, object]]) -> str:
    encoded = json.dumps(
        rows,
        ensure_ascii=True,
        sort_keys=True,
        separators=(",", ":"),
        allow_nan=False,
    ).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def _sha256(raw: bytes) -> str:
    return hashlib.sha256(raw).hexdigest()


def _artifact_list_sha256(records: list[dict[str, object]]) -> str:
    entries = sorted(
        (
            {
                "staging_relative_path": record["staging_relative_path"],
                "target_relative_path": record["target_relative_path"],
                "content_sha256": record["content_sha256"],
                "size": record["size"],
            }
            for record in records
        ),
        key=lambda entry: (
            str(entry["target_relative_path"]).casefold(),
            str(entry["staging_relative_path"]).casefold(),
        ),
    )
    encoded = json.dumps(
        entries,
        ensure_ascii=True,
        sort_keys=True,
        separators=(",", ":"),
        allow_nan=False,
    ).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


@dataclass
class PublicationFixture:
    root: Path
    receipt_path: Path
    receipt: dict[str, Any]
    item: dict[str, Any]
    route_contract: dict[str, Any]
    rows: list[dict[str, object]]
    data_path: Path
    manifest_path: Path
    route_id: str
    dataset: str

    def session(self, *, artifact_map: dict[str, Any] | None = None) -> Any:
        raw = self.receipt_path.read_bytes()
        descriptors = tuple(
            reader.CentralArtifactDescriptor(
                route_id=self.route_id,
                dataset=self.dataset,
                dataset_id=self.route_id,
                target_relative_path=str(record["target_relative_path"]),
                size=int(record["size"]),
                content_sha256=str(record["content_sha256"]),
                receipt_run_id=RECEIPT_RUN_ID,
                publication_run_id=PUBLICATION_RUN_ID,
                source_publication_run_id=SOURCE_RUN_ID,
                expected_asof=EXPECTED_ASOF,
                schema=ROUTE_SCHEMA,
                publish_status="committed",
                coverage_ok=True,
                pit_ok=True,
                media="application/json",
                role="sidecar" if record["role"] == "manifest" else "payload",
                coverage=MappingProxyType(dict(self.item["coverage"])),
                pit=MappingProxyType(dict(self.item["pit"])),
            )
            for record in self.item["publish"]["artifacts"]
        )
        frozen_map = MappingProxyType(
            artifact_map if artifact_map is not None else {self.route_id: descriptors}
        )
        return reader.CentralEvidenceSession(
            receipt_path=self.receipt_path,
            receipt_sha256=_sha256(raw),
            status="complete",
            publishable=True,
            conditions_met=True,
            condition_status="met",
            expected_asof=EXPECTED_ASOF,
            run_id=RECEIPT_RUN_ID,
            registry_raw_sha256="a" * 64,
            registry_canonical_sha256="b" * 64,
            route_ids=(self.route_id,),
            artifact_map=frozen_map,
            artifact_map_sha256=reader._artifact_map_digest(frozen_map),
            receipt=MappingProxyType(copy.deepcopy(self.receipt)),
            route_contracts=MappingProxyType(
                {self.route_id: MappingProxyType(copy.deepcopy(self.route_contract))}
            ),
            receipt_bytes=raw,
            registry_bytes=b"{}",
        )

    def read(self, session: Any = None, **overrides: Any) -> Any:
        arguments: dict[str, Any] = {
            "root": self.root,
            "expected_asof": EXPECTED_ASOF,
            "receipt_path": self.receipt_path,
            "session": session if session is not None else self.session(),
        }
        arguments.update(overrides)
        return reader.read_central_publication(self.dataset, **arguments)


def _write_publication_fixture(
    tmp_path: Path,
    *,
    route_id: str = ROUTE_ID,
    dataset: str = DATASET,
    rows: list[dict[str, object]] | None = None,
    fields: tuple[str, ...] = ("stock_id", "date", "close"),
    manifest_overrides: dict[str, object] | None = None,
    receipt_overrides: dict[str, object] | None = None,
    item_overrides: dict[str, object] | None = None,
    payload_overrides: dict[str, object] | None = None,
) -> PublicationFixture:
    root = tmp_path / "shared_market_data"
    directory = root.joinpath(*PUBLICATION_DIR)
    directory.mkdir(parents=True, exist_ok=True)
    if rows is None:
        rows = [
            {
                "stock_id": "2330",
                "date": EXPECTED_ASOF,
                "close": "123.4",
                "opaque": {"source": "fixture", "values": [1, 2]},
            }
        ]
    target = "/".join((*PUBLICATION_DIR, f"{route_id}.json"))
    manifest_target = "/".join((*PUBLICATION_DIR, f"{route_id}.manifest.json"))
    coverage = {"ok": True, "status": "complete", "observed": 1, "denominator": 1}
    pit = {"ok": True, "eligible": True, "expected_asof": EXPECTED_ASOF}
    content_hash = _canonical_rows_hash(rows)

    payload: dict[str, object] = {
        "dataset": dataset,
        "dataset_id": route_id,
        "schema": ROUTE_SCHEMA,
        "schema_version": 1,
        "provider": PROVIDER,
        "data": rows,
        "expected_asof": EXPECTED_ASOF,
        "publication_run_id": PUBLICATION_RUN_ID,
        "source_publication_run_id": SOURCE_RUN_ID,
        "coverage": dict(coverage),
        "pit": dict(pit),
    }
    if payload_overrides:
        payload.update(payload_overrides)
    manifest: dict[str, object] = {
        "schema": reader.REGISTRY_PUBLICATION_MANIFEST_SCHEMA,
        "dataset_identity": {
            "dataset_id": route_id,
            "dataset_name": dataset,
            "provider_dataset": dataset,
            "target_relative_path": target,
        },
        "dataset": dataset,
        "dataset_id": route_id,
        "provider": PROVIDER,
        "status": "complete",
        "rows": len(rows),
        "expected_asof": EXPECTED_ASOF,
        "publication_run_id": PUBLICATION_RUN_ID,
        "run_id": PUBLICATION_RUN_ID,
        "source_publication_run_id": SOURCE_RUN_ID,
        "coverage": {**coverage, "missing_units": [], "policy": COVERAGE_POLICY},
        "pit": {**pit, "policy": PIT_POLICY},
        "content_sha256": content_hash,
        "canonical_hash": {
            "algorithm": "sha256",
            "canonicalization": "central-json-v1",
        },
    }
    if manifest_overrides:
        manifest.update(manifest_overrides)
    data_path = root / Path(target)
    manifest_path = root / Path(manifest_target)
    data_bytes = json.dumps(payload, ensure_ascii=False).encode("utf-8")
    manifest_bytes = json.dumps(manifest, ensure_ascii=False).encode("utf-8")
    data_path.write_bytes(data_bytes)
    manifest_path.write_bytes(manifest_bytes)

    records: list[dict[str, object]] = [
        {
            "media": "application/json",
            "role": "payload",
            "target_relative_path": target,
            "staging_relative_path": f"staging/{route_id}.json",
            "content_sha256": _sha256(data_bytes),
            "size": len(data_bytes),
        },
        {
            "media": "application/json",
            "role": "manifest",
            "target_relative_path": manifest_target,
            "staging_relative_path": f"staging/{route_id}.manifest.json",
            "content_sha256": _sha256(manifest_bytes),
            "size": len(manifest_bytes),
        },
    ]
    artifact_list_sha256 = _artifact_list_sha256(records)
    item: dict[str, Any] = {
        "dataset": dataset,
        "dataset_id": route_id,
        "provider": PROVIDER,
        "status": "complete",
        "publishable": True,
        "expected_asof": EXPECTED_ASOF,
        "run_id": RECEIPT_RUN_ID,
        "publication_run_id": PUBLICATION_RUN_ID,
        "source_publication_run_id": SOURCE_RUN_ID,
        "coverage": dict(coverage),
        "pit": dict(pit),
        "schema": {"name": ROUTE_SCHEMA, "ok": True, "status": "complete"},
        "publish": {
            "status": "committed",
            "artifact_count": len(records),
            "target_relative_paths": [target, manifest_target],
            "artifacts": records,
            "artifact_list_sha256": artifact_list_sha256,
        },
        "publication_content_sha256": content_hash,
        "publication_payload_file_sha256": _sha256(data_bytes),
        "publication_sidecar_file_sha256": _sha256(manifest_bytes),
        "publication_artifact_list_sha256": artifact_list_sha256,
    }
    for key, value in (item_overrides or {}).items():
        if key == "publish" and isinstance(value, dict):
            item["publish"] = {**item["publish"], **value}
        else:
            item[key] = value
    receipt: dict[str, Any] = {
        "schema_version": 1,
        "status": "complete",
        "publishable": True,
        "fail_closed": False,
        "partial": False,
        "conditions_met": True,
        "condition_status": "met",
        "unmet_conditions": [],
        "expected_asof": EXPECTED_ASOF,
        "run_id": RECEIPT_RUN_ID,
        "orchestration_run_id": RECEIPT_RUN_ID,
        "datasets": [item],
    }
    if receipt_overrides:
        receipt.update(receipt_overrides)
    receipt_path = tmp_path / "latest_manifest.json"
    receipt_path.write_text(json.dumps(receipt, ensure_ascii=False), encoding="utf-8")
    route_contract = {
        "route_id": route_id,
        "dataset": dataset,
        "dataset_id": route_id,
        "provider": PROVIDER,
        "target_relative_path": target,
        "manifest_relative_path": manifest_target,
        "schema": ROUTE_SCHEMA,
        "fields": list(fields),
        "pit": {"policy": PIT_POLICY},
        "coverage": {"policy": COVERAGE_POLICY},
        "no_event": "",
    }
    return PublicationFixture(
        root=root,
        receipt_path=receipt_path,
        receipt=receipt,
        item=item,
        route_contract=route_contract,
        rows=rows,
        data_path=data_path,
        manifest_path=manifest_path,
        route_id=route_id,
        dataset=dataset,
    )


def test_reader_accepts_isolated_canonical_publication_and_receipt(
    tmp_path: Path,
    session_validation_calls: list[Any],
) -> None:
    fixture = _write_publication_fixture(tmp_path)
    session = fixture.session()
    expected_hash = _canonical_rows_hash(fixture.rows)

    publication = fixture.read(session)

    assert publication.rows == tuple(fixture.rows)
    assert publication.dataset == DATASET
    assert publication.expected_asof == EXPECTED_ASOF
    assert publication.run_id == PUBLICATION_RUN_ID
    assert publication.orchestration_run_id == RECEIPT_RUN_ID
    assert publication.source_publication_run_id == SOURCE_RUN_ID
    assert publication.schema == ROUTE_SCHEMA
    assert publication.content_sha256 == expected_hash
    assert publication.metadata()["content_sha256"] == expected_hash
    assert publication.receipt_sha256 == session.receipt_sha256
    assert publication.artifact_map_sha256 == session.artifact_map_sha256
    assert publication.data_path.parent == fixture.root.joinpath(*PUBLICATION_DIR)
    assert publication.manifest_path.name == f"{ROUTE_ID}.manifest.json"
    assert "D:\\TWStockData" not in str(publication.data_path)
    assert session_validation_calls == [session]


def test_receipt_item_of_the_fixture_satisfies_the_strict_artifact_contract(
    tmp_path: Path,
) -> None:
    fixture = _write_publication_fixture(tmp_path)

    records = reader._receipt_artifacts_strict(
        fixture.item,
        dataset=fixture.route_id,
        receipt_path=fixture.receipt_path,
    )

    assert {record["role"] for record in records} == {"payload", "manifest"}


def test_reader_resolves_industry_chain_alias_to_one_canonical_artifact(tmp_path: Path) -> None:
    canonical_dataset = "TaiwanStockIndustryChainMoneyFlow"
    dataset_id = "finmind_industry_chain_flow"
    rows = [{"date": EXPECTED_ASOF, "industry": "fixture", "trading_money_pct": "1.0"}]
    fixture = _write_publication_fixture(
        tmp_path,
        route_id=dataset_id,
        dataset=canonical_dataset,
        rows=rows,
        fields=("date", "industry", "trading_money_pct"),
    )

    publication = reader.read_central_publication(
        "TaiwanStockIndustryChain",
        root=fixture.root,
        expected_asof=EXPECTED_ASOF,
        receipt_path=fixture.receipt_path,
        session=fixture.session(),
    )

    assert publication.dataset == canonical_dataset
    assert publication.data_path.name == f"{dataset_id}.json"
    assert publication.manifest_path.name == f"{dataset_id}.manifest.json"
    assert publication.run_id == PUBLICATION_RUN_ID
    assert publication.rows == tuple(rows)


def test_reader_requires_a_pinned_evidence_session(
    tmp_path: Path,
    session_validation_calls: list[Any],
) -> None:
    fixture = _write_publication_fixture(tmp_path)

    for session in (None, SimpleNamespace(receipt_path=fixture.receipt_path), "session"):
        with pytest.raises(reader.CentralPublicationError) as raised:
            reader.read_central_publication(
                DATASET,
                root=fixture.root,
                expected_asof=EXPECTED_ASOF,
                receipt_path=fixture.receipt_path,
                session=session,
            )
        assert raised.value.reason == "evidence_session_required"

        with pytest.raises(reader.CentralPublicationError) as raised:
            reader.read_central_rows(
                DATASET,
                root=fixture.root,
                receipt_path=fixture.receipt_path,
                session=session,
            )
        assert raised.value.reason == "evidence_session_required"

        with pytest.raises(reader.CentralPublicationError) as raised:
            reader.validate_central_publications(
                [DATASET],
                root=fixture.root,
                receipt_path=fixture.receipt_path,
                session=session,
            )
        assert raised.value.reason == "evidence_session_required"

    assert session_validation_calls == []


def test_reader_rows_and_batch_helpers_forward_the_same_session(
    tmp_path: Path,
    session_validation_calls: list[Any],
) -> None:
    fixture = _write_publication_fixture(tmp_path)
    session = fixture.session()

    rows = reader.read_central_rows(
        DATASET,
        root=fixture.root,
        expected_asof=EXPECTED_ASOF,
        receipt_path=fixture.receipt_path,
        session=session,
    )
    publications = reader.validate_central_publications(
        [DATASET],
        root=fixture.root,
        expected_asof=EXPECTED_ASOF,
        receipt_path=fixture.receipt_path,
        session=session,
    )

    assert rows == tuple(fixture.rows)
    assert list(publications) == [DATASET]
    assert publications[DATASET].rows == tuple(fixture.rows)
    assert session_validation_calls == [session, session]


@pytest.mark.parametrize(
    ("label", "manifest_overrides", "receipt_overrides", "item_overrides", "payload_overrides", "reason"),
    [
        ("schema", {"schema": "wrong-schema"}, {}, {}, {}, "manifest_schema_mismatch"),
        (
            "canonical_hash",
            {"content_sha256": "0" * 64},
            {},
            {},
            {},
            "route_publication_hash_role_mismatch",
        ),
        (
            "canonical_hash_bound_in_receipt",
            {"content_sha256": "0" * 64},
            {},
            {"publication_content_sha256": "0" * 64},
            {},
            "content_hash_mismatch",
        ),
        (
            "canonical_hash_algorithm",
            {"canonical_hash": {"algorithm": "md5", "canonicalization": "central-json-v1"}},
            {},
            {},
            {},
            "manifest_canonical_hash_invalid",
        ),
        (
            "pit",
            {"pit": {"ok": False, "eligible": False, "policy": PIT_POLICY}},
            {},
            {},
            {},
            "manifest_pit_invalid",
        ),
        (
            "pit_policy",
            {"pit": {"ok": True, "eligible": True, "policy": "other_policy", "expected_asof": EXPECTED_ASOF}},
            {},
            {},
            {},
            "manifest_pit_policy_mismatch",
        ),
        (
            "coverage",
            {
                "coverage": {
                    "ok": False,
                    "observed": 0,
                    "denominator": 1,
                    "missing_units": [],
                    "policy": COVERAGE_POLICY,
                }
            },
            {},
            {},
            {},
            "manifest_coverage_invalid",
        ),
        (
            "coverage_policy",
            {
                "coverage": {
                    "ok": True,
                    "status": "complete",
                    "observed": 1,
                    "denominator": 1,
                    "missing_units": [],
                    "policy": "other_policy",
                }
            },
            {},
            {},
            {},
            "manifest_coverage_policy_mismatch",
        ),
        (
            "manifest_not_complete",
            {"status": "partial"},
            {},
            {},
            {},
            "manifest_not_complete",
        ),
        (
            "expected_asof",
            {"expected_asof": "2026-08-20"},
            {},
            {},
            {},
            "manifest_expected_asof_mismatch",
        ),
        (
            "payload_expected_asof",
            {},
            {},
            {},
            {"expected_asof": "2026-08-20"},
            "expected_asof_receipt_mismatch",
        ),
        (
            "payload_publication_run_id",
            {},
            {},
            {},
            {"publication_run_id": "different-run"},
            "publication_run_id_mismatch",
        ),
        (
            "manifest_publication_run_id",
            {"run_id": "different-run"},
            {},
            {},
            {},
            "manifest_lineage_invalid",
        ),
        ("receipt_run_id", {}, {"run_id": ""}, {}, {}, "receipt_run_id_missing"),
        (
            "receipt_dataset_run_id",
            {},
            {},
            {"run_id": "different-run"},
            {},
            "receipt_dataset_run_id_mismatch",
        ),
        (
            "receipt_conditions",
            {},
            {"conditions_met": False},
            {},
            {},
            "receipt_conditions_invalid",
        ),
        (
            "receipt_publish_not_committed",
            {},
            {},
            {"publish": {"status": "staged"}},
            {},
            "receipt_publish_not_committed",
        ),
        (
            "payload_schema",
            {},
            {},
            {},
            {"schema": "wrong-schema"},
            "schema_mismatch",
        ),
        (
            "payload_provider",
            {},
            {},
            {},
            {"provider": "other_provider"},
            "provider_mismatch",
        ),
        (
            "payload_rows_empty",
            {},
            {},
            {},
            {"data": []},
            "rows_empty",
        ),
    ],
)
def test_reader_fails_closed_on_publication_contract_drift(
    tmp_path: Path,
    label: str,
    manifest_overrides: dict[str, object],
    receipt_overrides: dict[str, object],
    item_overrides: dict[str, object],
    payload_overrides: dict[str, object],
    reason: str,
) -> None:
    fixture = _write_publication_fixture(
        tmp_path / label,
        manifest_overrides=manifest_overrides,
        receipt_overrides=receipt_overrides,
        item_overrides=item_overrides,
        payload_overrides=payload_overrides,
    )

    with pytest.raises(reader.CentralPublicationError) as raised:
        fixture.read()

    assert raised.value.reason == reason


def test_reader_rejects_payload_bytes_changed_after_the_receipt_was_issued(
    tmp_path: Path,
) -> None:
    fixture = _write_publication_fixture(tmp_path)
    session = fixture.session()
    fixture.data_path.write_bytes(fixture.data_path.read_bytes() + b"\n")

    with pytest.raises(reader.CentralPublicationError) as raised:
        fixture.read(session)

    assert raised.value.reason == "artifact_size_mismatch"


def test_reader_rejects_payload_with_unchanged_size_but_different_hash(
    tmp_path: Path,
) -> None:
    fixture = _write_publication_fixture(tmp_path)
    session = fixture.session()
    raw = bytearray(fixture.data_path.read_bytes())
    index = raw.index(b"123.4")
    raw[index : index + 5] = b"999.9"
    fixture.data_path.write_bytes(bytes(raw))

    with pytest.raises(reader.CentralPublicationError) as raised:
        fixture.read(session)

    assert raised.value.reason == "artifact_hash_mismatch"


def test_reader_rejects_a_session_whose_descriptor_no_longer_matches_the_receipt(
    tmp_path: Path,
) -> None:
    fixture = _write_publication_fixture(tmp_path)
    honest = fixture.session()
    drifted = tuple(
        reader.CentralArtifactDescriptor(
            **{**descriptor.__dict__, "content_sha256": "f" * 64}
        )
        if descriptor.role == "payload"
        else descriptor
        for descriptor in honest.artifact_descriptors(ROUTE_ID)
    )
    session = fixture.session(artifact_map={ROUTE_ID: drifted})

    with pytest.raises(reader.CentralPublicationError) as raised:
        fixture.read(session)

    assert raised.value.reason == "session_artifact_identity_drift"


def test_reader_rejects_route_that_the_session_never_declared(tmp_path: Path) -> None:
    fixture = _write_publication_fixture(tmp_path)
    honest = fixture.session()
    session = reader.CentralEvidenceSession(
        **{**honest.__dict__, "route_contracts": MappingProxyType({})}
    )

    with pytest.raises(reader.CentralPublicationError) as raised:
        fixture.read(session)

    assert raised.value.reason == "session_route_not_declared"


@pytest.mark.parametrize(
    ("target", "reason"),
    [
        ("providers/finmind/json/../../../outside.json", "receipt_artifact_path_escape"),
        ("D:/outside.json", "receipt_artifact_path_escape"),
        ("providers/finmind/json/*.json", "receipt_artifact_path_escape"),
    ],
)
def test_reader_rejects_receipt_artifact_target_that_escapes_the_shared_root(
    tmp_path: Path,
    target: str,
    reason: str,
) -> None:
    fixture = _write_publication_fixture(tmp_path)
    fixture.item["publish"]["artifacts"][0]["target_relative_path"] = target
    fixture.receipt_path.write_text(json.dumps(fixture.receipt), encoding="utf-8")

    with pytest.raises(reader.CentralPublicationError) as raised:
        fixture.read()

    assert raised.value.reason == reason
    assert not (tmp_path / "outside.json").exists()


def test_reader_rejects_receipt_artifact_that_is_not_the_registry_target(
    tmp_path: Path,
) -> None:
    fixture = _write_publication_fixture(tmp_path)
    other = fixture.root.joinpath(*PUBLICATION_DIR, "other.json")
    other.write_bytes(fixture.data_path.read_bytes())
    fixture.item["publish"]["artifacts"][0]["target_relative_path"] = (
        "providers/finmind/json/other.json"
    )

    with pytest.raises(reader.CentralPublicationError) as raised:
        fixture.read()

    assert raised.value.reason == "receipt_artifact_missing"
