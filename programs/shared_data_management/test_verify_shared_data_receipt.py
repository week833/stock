from __future__ import annotations

import hashlib
import json
from pathlib import Path
import sys
import tempfile
import unittest


MODULE_ROOT = Path(__file__).resolve().parent
if str(MODULE_ROOT) not in sys.path:
    sys.path.insert(0, str(MODULE_ROOT))

import VERIFY_SHARED_DATA_RECEIPT as verifier


FIXTURE_REGISTRY_SCHEMA = "shared-market-data-requirements-v1"
FIXTURE_REGISTRY_FILENAME = "requirements_v1.json"
FIXTURE_EXPECTED_ASOF = "2026-08-21"
FIXTURE_RUN_ID = "market-test-run"
FIXTURE_REQUIRED_ROUTE_IDS = [
    "route_required",
    *[f"required_{index:02d}" for index in range(2, 41)],
]
FIXTURE_OPTIONAL_ROUTE_IDS = [
    "route_optional",
    *[f"optional_{index:02d}" for index in range(2, 9)],
]
FIXTURE_ROUTE_IDS = FIXTURE_REQUIRED_ROUTE_IDS + FIXTURE_OPTIONAL_ROUTE_IDS


class CentralReceiptVerificationTests(unittest.TestCase):
    @staticmethod
    def _rewrite_receipt(receipt_path: Path, receipt: dict[str, object]) -> None:
        receipt.pop("manifest_content_sha256", None)
        receipt["manifest_content_sha256"] = verifier._sha256_bytes(
            verifier._canonical_json(receipt)
        )
        receipt_path.write_text(
            json.dumps(receipt, ensure_ascii=False),
            encoding="utf-8",
        )

    @staticmethod
    def _registry(target: str) -> dict[str, object]:
        requirements: list[dict[str, object]] = []
        contracts: dict[str, dict[str, object]] = {}
        for index, route_id in enumerate(FIXTURE_ROUTE_IDS, start=1):
            required = route_id in FIXTURE_REQUIRED_ROUTE_IDS
            dataset = route_id
            provider = "fixture_provider"
            payload_target = target if index == 1 else f"routes/{route_id}.json"
            sidecar_target = verifier._expected_sidecar_path(payload_target)
            requirement = {
                "id": route_id,
                "required": required,
                "classification": "shared_raw",
                "dataset": dataset,
                "provider": provider,
                "target_relative_paths": [f"routes/{route_id}"],
                "freshness_policy": "fixture_freshness_v1",
                "pit_policy": "fixture_pit_v1",
                "coverage_policy": "fixture_coverage_v1",
                "schema_policy": "fixture_schema_v1",
                "hash_policy": "sha256_payload",
                "adapter": {
                    "status": "ready",
                    "execute": True,
                    "owner": "market_update",
                    "command": f"adapter://{route_id}",
                },
            }
            requirements.append(requirement)
            if required:
                contracts[route_id] = {
                    "required": True,
                    "classification": "shared_raw",
                    "dataset": dataset,
                    "provider": provider,
                    "producer": {
                        "id": f"adapter://{route_id}",
                        "unique": True,
                        "status": "available",
                        "execute": True,
                        "central_owner": "market_update",
                    },
                    "publication": {
                        "target_relative_path": payload_target,
                        "manifest_relative_path": sidecar_target,
                    },
                    "update": {"strategy": "fixture_incremental"},
                    "pit": {
                        "policy": "fixture_pit_v1",
                        "expected_asof_rule": "fixture expected_asof",
                    },
                    "coverage": {
                        "policy": "fixture_coverage_v1",
                        "denominator": "fixture_universe",
                        "missing_unit_behavior": "fail_closed",
                    },
                    "fields": ["route_id", "date"],
                    "schema_policy": "fixture_schema_v1",
                    "hash_policy": "sha256_payload",
                }
        return {
            "schema": FIXTURE_REGISTRY_SCHEMA,
            "schema_version": 1,
            "shared_raw_requirements": requirements,
            "route_contracts": contracts,
            "route_order": FIXTURE_ROUTE_IDS,
            "route_contract_audit": {
                "required_route_count": len(FIXTURE_REQUIRED_ROUTE_IDS),
                "route_contract_count": len(contracts),
                "required_route_ids": FIXTURE_REQUIRED_ROUTE_IDS,
                "optional_route_ids": FIXTURE_OPTIONAL_ROUTE_IDS,
            },
        }

    def _fixture(self, root: Path, *, target: str = "providers/finmind/json/sample.json") -> tuple[Path, Path, Path]:
        shared_root = root / "shared"
        registry_path = root / FIXTURE_REGISTRY_FILENAME
        registry = self._registry(target)
        registry_path.write_text(json.dumps(registry, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        registry_raw_sha = hashlib.sha256(registry_path.read_bytes()).hexdigest()
        registry_canonical_sha = verifier._sha256_bytes(verifier._canonical_json(registry))
        datasets: list[dict[str, object]] = []
        for index, route_id in enumerate(FIXTURE_ROUTE_IDS, start=1):
            requirement = next(item for item in registry["shared_raw_requirements"] if item["id"] == route_id)
            contract = registry["route_contracts"].get(route_id)
            payload_target = (
                contract["publication"]["target_relative_path"]
                if contract is not None
                else f"routes/{route_id}.json"
            )
            sidecar_target = verifier._expected_sidecar_path(payload_target)
            payload_path = shared_root.joinpath(*payload_target.split("/"))
            sidecar_path = shared_root.joinpath(*sidecar_target.split("/"))
            payload_path.parent.mkdir(parents=True, exist_ok=True)
            sidecar_path.parent.mkdir(parents=True, exist_ok=True)
            payload_bytes = json.dumps({"data": [index]}, separators=(",", ":")).encode("utf-8") + b"\n"
            payload_path.write_bytes(payload_bytes)
            payload_sha = hashlib.sha256(payload_bytes).hexdigest()
            sidecar = {
                "schema": "shared-market-data-publication-sidecar-v1",
                "schema_version": 1,
                "dataset_id": route_id,
                "dataset": route_id,
                "target_relative_path": payload_target,
                "content_sha256": payload_sha,
                "size": len(payload_bytes),
                "run_id": FIXTURE_RUN_ID,
                "expected_asof": FIXTURE_EXPECTED_ASOF,
                "registry_raw_sha256": registry_raw_sha,
                "registry_canonical_sha256": registry_canonical_sha,
                "status": "complete",
            }
            sidecar_bytes = json.dumps(sidecar, sort_keys=True, separators=(",", ":")).encode("utf-8") + b"\n"
            sidecar_path.write_bytes(sidecar_bytes)
            sidecar_sha = hashlib.sha256(sidecar_bytes).hexdigest()
            payload_declaration = {
                "staging_relative_path": f"staging/{route_id}.json",
                "target_relative_path": payload_target,
                "content_sha256": payload_sha,
                "size": len(payload_bytes),
            }
            sidecar_declaration = {
                "staging_relative_path": f"staging/{route_id}.manifest.json",
                "target_relative_path": sidecar_target,
                "content_sha256": sidecar_sha,
                "size": len(sidecar_bytes),
            }
            declarations = [payload_declaration, sidecar_declaration]
            publish = {
                "status": "committed",
                "artifact_count": len(declarations),
                "artifact_list_sha256": verifier._artifact_list_hash(declarations),
                "target_relative_paths": [payload_target, sidecar_target],
                "content_sha256": payload_sha,
                "artifacts": declarations,
            }
            datasets.append(
                {
                    "dataset": requirement["dataset"],
                    "dataset_id": route_id,
                    "id": route_id,
                    "route_id": route_id,
                    "required": requirement["required"],
                    "provider": requirement["provider"],
                    "adapter": requirement["adapter"],
                    "schema_policy": requirement["schema_policy"],
                    "freshness_policy": requirement["freshness_policy"],
                    "pit_policy": requirement["pit_policy"],
                    "coverage_policy": requirement["coverage_policy"],
                    "hash_policy": requirement["hash_policy"],
                    "status": "complete",
                    "publishable": True,
                    "expected_asof": FIXTURE_EXPECTED_ASOF,
                    "run_id": FIXTURE_RUN_ID,
                    "coverage": {
                        "denominator": 1,
                        "observed": 1,
                        "ok": True,
                        "status": "complete",
                    },
                    "pit": {
                        "expected_asof": FIXTURE_EXPECTED_ASOF,
                        "eligible": True,
                        "ok": True,
                        "status": "eligible",
                    },
                    "freshness": {
                        "latest_asof": FIXTURE_EXPECTED_ASOF,
                        "ok": True,
                        "status": "fresh",
                    },
                    "hash": {
                        "algorithm": "sha256",
                        "ok": True,
                        "status": "verified",
                        "value": payload_sha,
                    },
                    "lineage": {
                        "run_id": FIXTURE_RUN_ID,
                        "expected_asof": FIXTURE_EXPECTED_ASOF,
                        "content_sha256": payload_sha,
                        "registry_raw_sha256": registry_raw_sha,
                        "registry_canonical_sha256": registry_canonical_sha,
                    },
                    "publish": publish,
                }
            )
        receipt: dict[str, object] = {
            "schema": {"manifest": verifier.RECEIPT_SCHEMA},
            "schema_version": 1,
            "status": "complete",
            "publishable": True,
            "partial": False,
            "fail_closed": False,
            "run_id": FIXTURE_RUN_ID,
            "expected_asof": FIXTURE_EXPECTED_ASOF,
            "registry_sha256": registry_canonical_sha,
            "registry_raw_sha256": registry_raw_sha,
            "registry_canonical_sha256": registry_canonical_sha,
            "coverage": {
                "required_total": len(FIXTURE_REQUIRED_ROUTE_IDS),
                "required_complete": len(FIXTURE_REQUIRED_ROUTE_IDS),
                "optional_total": len(FIXTURE_OPTIONAL_ROUTE_IDS),
                "optional_verified_datasets": len(FIXTURE_OPTIONAL_ROUTE_IDS),
                "all_verified": True,
            },
            "route_registry": {
                "total": len(FIXTURE_ROUTE_IDS),
                "sha256": registry_canonical_sha,
                "route_order_sha256": verifier._sha256_bytes(verifier._canonical_json(FIXTURE_ROUTE_IDS)),
                "routes": [
                    {"id": route_id, "required": route_id in FIXTURE_REQUIRED_ROUTE_IDS}
                    for route_id in FIXTURE_ROUTE_IDS
                ],
            },
            "datasets": datasets,
        }
        without_hash = dict(receipt)
        receipt["manifest_content_sha256"] = verifier._sha256_bytes(verifier._canonical_json(without_hash))
        receipt_path = root / "latest_manifest.json"
        receipt_path.write_text(json.dumps(receipt, ensure_ascii=False), encoding="utf-8")
        output_path = root / "state" / "shared_market_data_control" / "verification_latest.json"
        return shared_root, receipt_path, output_path

    def test_declared_artifact_is_hashed_and_published_atomically(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            shared_root, receipt_path, output_path = self._fixture(Path(temp))
            result = verifier.verify_central_receipt(
                shared_root=shared_root,
                receipt_path=receipt_path,
                output_path=output_path,
            )

            self.assertEqual(result["status"], "pass")
            self.assertEqual(result["schema"], verifier.VERIFICATION_SCHEMA)
            self.assertEqual(result["totals"]["checked_files"], len(FIXTURE_ROUTE_IDS) * 2)
            self.assertEqual(result["totals"]["datasets_without_published_artifacts"], 0)
            self.assertEqual(result["registry"]["route_total"], 48)
            self.assertEqual(result["registry"]["required_total"], 40)
            self.assertEqual(result["registry"]["optional_total"], 8)
            self.assertEqual(result["issues"], [])
            self.assertTrue(output_path.is_file())
            self.assertEqual(json.loads(output_path.read_text(encoding="utf-8"))["status"], "pass")

    def test_failed_hash_does_not_replace_previous_passing_output(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            shared_root, receipt_path, output_path = self._fixture(Path(temp))
            first = verifier.verify_central_receipt(
                shared_root=shared_root,
                receipt_path=receipt_path,
                output_path=output_path,
            )
            self.assertEqual(first["status"], "pass")
            previous = output_path.read_bytes()
            artifact_path = shared_root / "providers" / "finmind" / "json" / "sample.json"
            artifact_path.write_bytes(b'{"data":[2]}\n')

            failed = verifier.verify_central_receipt(
                shared_root=shared_root,
                receipt_path=receipt_path,
                output_path=output_path,
            )

            self.assertEqual(failed["status"], "fail")
            self.assertEqual(failed["issues"][0]["code"], "artifact_hash_mismatch")
            self.assertEqual(output_path.read_bytes(), previous)

    def test_registry_hash_drift_is_fail_closed(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            shared_root, receipt_path, output_path = self._fixture(Path(temp))
            receipt = json.loads(receipt_path.read_text(encoding="utf-8"))
            receipt["registry_sha256"] = "f" * 64
            self._rewrite_receipt(receipt_path, receipt)

            result = verifier.verify_central_receipt(
                shared_root=shared_root,
                receipt_path=receipt_path,
                output_path=output_path,
            )

            self.assertEqual(result["status"], "fail")
            self.assertEqual(result["issues"][0]["code"], "receipt_registry_sha_mismatch")

    def test_route_registry_without_routes_is_fail_closed(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            shared_root, receipt_path, output_path = self._fixture(Path(temp))
            receipt = json.loads(receipt_path.read_text(encoding="utf-8"))
            receipt["route_registry"] = {
                "sha256": receipt["registry_sha256"],
            }
            self._rewrite_receipt(receipt_path, receipt)

            result = verifier.verify_central_receipt(
                shared_root=shared_root,
                receipt_path=receipt_path,
                output_path=output_path,
            )

            self.assertEqual(result["status"], "fail")
            self.assertEqual(
                result["issues"][0]["code"],
                "receipt_route_registry_routes_missing",
            )

    def test_registry_route_addition_is_fail_closed_after_sha_rebind(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            shared_root, receipt_path, output_path = self._fixture(root)
            registry_path = root / FIXTURE_REGISTRY_FILENAME
            registry = json.loads(registry_path.read_text(encoding="utf-8"))
            registry["shared_raw_requirements"].append(
                {
                    "id": "route_added_optional",
                    "required": False,
                    "classification": "shared_raw",
                    "dataset": "route_added_optional",
                    "provider": "fixture_provider",
                    "target_relative_paths": ["routes/route_added_optional"],
                    "freshness_policy": "fixture_freshness_v1",
                    "pit_policy": "fixture_pit_v1",
                    "coverage_policy": "fixture_coverage_v1",
                    "schema_policy": "fixture_schema_v1",
                    "hash_policy": "sha256_payload",
                    "adapter": {
                        "status": "ready",
                        "execute": True,
                        "owner": "market_update",
                        "command": "adapter://route_added_optional",
                    },
                }
            )
            registry_path.write_text(
                json.dumps(registry, ensure_ascii=False, indent=2) + "\n",
                encoding="utf-8",
            )
            registry_sha = hashlib.sha256(registry_path.read_bytes()).hexdigest()
            receipt = json.loads(receipt_path.read_text(encoding="utf-8"))
            receipt["registry_sha256"] = hashlib.sha256(verifier._canonical_json(registry)).hexdigest()
            receipt["registry_canonical_sha256"] = receipt["registry_sha256"]
            receipt["registry_raw_sha256"] = registry_sha
            receipt["route_registry"]["sha256"] = receipt["registry_sha256"]
            receipt["route_registry"]["total"] = 49
            self._rewrite_receipt(receipt_path, receipt)

            result = verifier.verify_central_receipt(
                shared_root=shared_root,
                receipt_path=receipt_path,
                registry_path=registry_path,
                output_path=output_path,
            )

            self.assertEqual(result["status"], "fail")
            self.assertEqual(result["issues"][0]["code"], "registry_route_count_invalid")

    def test_registry_route_deletion_is_fail_closed_after_sha_rebind(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            shared_root, receipt_path, output_path = self._fixture(root)
            registry_path = root / FIXTURE_REGISTRY_FILENAME
            registry = json.loads(registry_path.read_text(encoding="utf-8"))
            registry["shared_raw_requirements"] = [
                item
                for item in registry["shared_raw_requirements"]
                if item["id"] != "route_optional"
            ]
            registry_path.write_text(
                json.dumps(registry, ensure_ascii=False, indent=2) + "\n",
                encoding="utf-8",
            )
            registry_sha = hashlib.sha256(registry_path.read_bytes()).hexdigest()
            registry_canonical_sha = hashlib.sha256(verifier._canonical_json(registry)).hexdigest()
            receipt = json.loads(receipt_path.read_text(encoding="utf-8"))
            receipt["registry_sha256"] = registry_canonical_sha
            receipt["registry_canonical_sha256"] = registry_canonical_sha
            receipt["registry_raw_sha256"] = registry_sha
            receipt["route_registry"]["sha256"] = registry_canonical_sha
            receipt["route_registry"]["total"] = 47
            self._rewrite_receipt(receipt_path, receipt)

            result = verifier.verify_central_receipt(
                shared_root=shared_root,
                receipt_path=receipt_path,
                registry_path=registry_path,
                output_path=output_path,
            )

            self.assertEqual(result["status"], "fail")
            self.assertEqual(result["issues"][0]["code"], "registry_route_count_invalid")

    def test_explicit_route_selection_does_not_change_union(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            shared_root, receipt_path, output_path = self._fixture(Path(temp))
            result = verifier.verify_central_receipt(
                shared_root=shared_root,
                receipt_path=receipt_path,
                output_path=output_path,
            )

            self.assertEqual(result["status"], "pass")
            selected_route_ids = verifier.select_explicit_route_ids(
                result["registry"]["route_ids"], ["route_required"]
            )
            self.assertEqual(selected_route_ids, (FIXTURE_REQUIRED_ROUTE_IDS[0],))
            with self.assertRaises(verifier.VerificationFailure) as context:
                verifier.select_explicit_route_ids(
                    result["registry"]["route_ids"], ["unknown_route"]
                )
            self.assertEqual(context.exception.code, "explicit_route_id_unknown")
            self.assertEqual(result["registry"]["route_total"], len(FIXTURE_ROUTE_IDS))
            self.assertEqual(result["registry"]["required_total"], len(FIXTURE_REQUIRED_ROUTE_IDS))
            self.assertEqual(result["registry"]["optional_total"], len(FIXTURE_OPTIONAL_ROUTE_IDS))

    def test_old_36_required_coverage_receipt_is_fail_closed(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            shared_root, receipt_path, output_path = self._fixture(Path(temp))
            receipt = json.loads(receipt_path.read_text(encoding="utf-8"))
            receipt["coverage"]["required_total"] = 36
            receipt["coverage"]["required_complete"] = 36
            self._rewrite_receipt(receipt_path, receipt)

            result = verifier.verify_central_receipt(
                shared_root=shared_root,
                receipt_path=receipt_path,
                output_path=output_path,
            )

            self.assertEqual(result["status"], "fail")
            self.assertEqual(result["issues"][0]["code"], "receipt_coverage_mismatch")

    def test_missing_registry_is_fail_closed(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            shared_root, receipt_path, output_path = self._fixture(root)

            result = verifier.verify_central_receipt(
                shared_root=shared_root,
                receipt_path=receipt_path,
                registry_path=root / "missing-requirements.json",
                output_path=output_path,
            )

            self.assertEqual(result["status"], "fail")
            self.assertEqual(result["issues"][0]["code"], "registry_missing")

    def test_invalid_registry_json_is_fail_closed(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            shared_root, receipt_path, output_path = self._fixture(root)
            registry_path = root / FIXTURE_REGISTRY_FILENAME
            registry_path.write_text("{invalid\n", encoding="utf-8")

            result = verifier.verify_central_receipt(
                shared_root=shared_root,
                receipt_path=receipt_path,
                registry_path=registry_path,
                output_path=output_path,
            )

            self.assertEqual(result["status"], "fail")
            self.assertEqual(result["issues"][0]["code"], "registry_parse_error")

    def test_duplicate_registry_route_is_fail_closed(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            shared_root, receipt_path, output_path = self._fixture(root)
            registry_path = root / FIXTURE_REGISTRY_FILENAME
            registry = json.loads(registry_path.read_text(encoding="utf-8"))
            appended = json.loads(json.dumps(registry))
            appended["shared_raw_requirements"].append(
                {"id": "route_required", "required": True}
            )
            registry_path.write_text(
                json.dumps(appended, ensure_ascii=False, indent=2) + "\n",
                encoding="utf-8",
            )

            result = verifier.verify_central_receipt(
                shared_root=shared_root,
                receipt_path=receipt_path,
                registry_path=registry_path,
                output_path=output_path,
            )

            self.assertEqual(result["status"], "fail")
            self.assertEqual(result["issues"][0]["code"], "registry_route_count_invalid")

            # The route count stays at 48 so the duplicate id itself is what is rejected.
            registry["shared_raw_requirements"][-1] = {
                "id": "route_required",
                "required": True,
            }
            registry_path.write_text(
                json.dumps(registry, ensure_ascii=False, indent=2) + "\n",
                encoding="utf-8",
            )

            result = verifier.verify_central_receipt(
                shared_root=shared_root,
                receipt_path=receipt_path,
                registry_path=registry_path,
                output_path=output_path,
            )

            self.assertEqual(result["status"], "fail")
            self.assertEqual(result["issues"][0]["code"], "registry_duplicate_route_id")
            self.assertFalse(output_path.exists())

    def test_path_escape_is_fail_closed_without_output(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            shared_root, receipt_path, output_path = self._fixture(Path(temp))
            # The fixture itself cannot create a traversal target; rewrite the
            # declaration while keeping its self-consistent receipt hash.
            receipt = json.loads(receipt_path.read_text(encoding="utf-8"))
            declaration = receipt["datasets"][0]["publish"]["artifacts"][0]
            declaration["target_relative_path"] = "../outside.json"
            receipt["datasets"][0]["publish"]["artifact_list_sha256"] = verifier._artifact_list_hash([declaration])
            receipt.pop("manifest_content_sha256", None)
            receipt["manifest_content_sha256"] = verifier._sha256_bytes(verifier._canonical_json(receipt))
            receipt_path.write_text(json.dumps(receipt), encoding="utf-8")

            result = verifier.verify_central_receipt(
                shared_root=shared_root,
                receipt_path=receipt_path,
                output_path=output_path,
            )

            self.assertEqual(result["status"], "fail")
            self.assertEqual(result["issues"][0]["code"], "artifact_target_path_escape")
            self.assertFalse(output_path.exists())

    def test_declared_target_classification_is_the_same_for_required_and_optional_routes(self) -> None:
        cases = (
            ("route_required", "../outside.json", "artifact_target_path_escape"),
            ("route_optional", "../outside.json", "artifact_target_path_escape"),
            ("route_required", "/absolute.json", "artifact_target_invalid"),
            ("route_optional", "/absolute.json", "artifact_target_invalid"),
        )
        for route_id, target, expected_code in cases:
            with self.subTest(route_id=route_id, target=target):
                with tempfile.TemporaryDirectory() as temp:
                    shared_root, receipt_path, output_path = self._fixture(Path(temp))
                    receipt = json.loads(receipt_path.read_text(encoding="utf-8"))
                    dataset = next(
                        item for item in receipt["datasets"] if item["dataset_id"] == route_id
                    )
                    declaration = dataset["publish"]["artifacts"][0]
                    declaration["target_relative_path"] = target
                    dataset["publish"]["artifact_list_sha256"] = verifier._artifact_list_hash(
                        dataset["publish"]["artifacts"]
                    )
                    self._rewrite_receipt(receipt_path, receipt)

                    result = verifier.verify_central_receipt(
                        shared_root=shared_root,
                        receipt_path=receipt_path,
                        output_path=output_path,
                    )

                    self.assertEqual(result["status"], "fail")
                    self.assertEqual(result["issues"][0]["code"], expected_code)
                    self.assertFalse(output_path.exists())

    def test_adapter_command_drift_is_fail_closed_for_required_and_optional_routes(self) -> None:
        for route_id in ("route_required", "route_optional"):
            with self.subTest(route_id=route_id):
                with tempfile.TemporaryDirectory() as temp:
                    shared_root, receipt_path, output_path = self._fixture(Path(temp))
                    receipt = json.loads(receipt_path.read_text(encoding="utf-8"))
                    dataset = next(
                        item for item in receipt["datasets"] if item["dataset_id"] == route_id
                    )
                    dataset["adapter"]["command"] = "adapter://some_other_route"
                    self._rewrite_receipt(receipt_path, receipt)

                    result = verifier.verify_central_receipt(
                        shared_root=shared_root,
                        receipt_path=receipt_path,
                        output_path=output_path,
                    )

                    self.assertEqual(result["status"], "fail")
                    self.assertEqual(
                        result["issues"][0]["code"], "receipt_producer_binding_mismatch"
                    )
                    self.assertEqual(result["issues"][0]["field"], route_id)
                    self.assertFalse(output_path.exists())

    def test_optional_route_adapter_owner_drift_is_fail_closed(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            shared_root, receipt_path, output_path = self._fixture(Path(temp))
            receipt = json.loads(receipt_path.read_text(encoding="utf-8"))
            dataset = next(
                item for item in receipt["datasets"] if item["dataset_id"] == "route_optional"
            )
            dataset["adapter"]["owner"] = "producer"
            self._rewrite_receipt(receipt_path, receipt)

            result = verifier.verify_central_receipt(
                shared_root=shared_root,
                receipt_path=receipt_path,
                output_path=output_path,
            )

            self.assertEqual(result["status"], "fail")
            self.assertEqual(result["issues"][0]["code"], "receipt_producer_owner_mismatch")
            self.assertFalse(output_path.exists())


if __name__ == "__main__":
    unittest.main()
