from __future__ import annotations

import hashlib
import json
from pathlib import Path
import re
import sys
from typing import Any

import pytest


PROGRAMS_ROOT = Path(__file__).resolve().parents[1]
if str(PROGRAMS_ROOT) not in sys.path:
    sys.path.insert(0, str(PROGRAMS_ROOT))

import VERIFY_RUNTIME_DATA_PATHS as verifier
import path_contract


PUBLICATION_CATEGORIES = {
    "providers_finmind_json": "finmind_stock_info",
    "providers_global_markets": "global_market_history",
    "official_calendar": "official_calendar",
    "official_trading_restrictions": "official_trading_restrictions",
    "official_market_daily": "official_market_daily",
    "official_sector_features": "official_sector_features",
}
LEGACY_SAMPLE_CATEGORIES = {"prices_history": "*.json"}


def _spec(key: str) -> Any:
    return next(spec for spec in verifier.CATEGORY_SPECS if spec.key == key)


def _write_publication(
    root: Path,
    *,
    routes: tuple[str, ...] = tuple(PUBLICATION_CATEGORIES.values()),
    schema: str | None = None,
    partial_alternate: bool = False,
) -> dict[str, Any]:
    objects = root / "_objects"
    objects.mkdir(parents=True, exist_ok=True)
    datasets: list[dict[str, Any]] = []
    for index, route_id in enumerate(routes):
        payload = json.dumps({"route_id": route_id, "rows": index}).encode("utf-8")
        digest = hashlib.sha256(payload).hexdigest()
        (objects / f"{digest}.json").write_bytes(payload)
        datasets.append(
            {
                "route_id": route_id,
                "path": f"_objects/{digest}.json",
                "sha256": digest,
                "size": len(payload),
                "status": "PARTIAL" if partial_alternate and index % 2 else "SUCCESS",
            }
        )
    document = {
        "schema": schema or verifier.PUBLICATION_SCHEMA,
        "producer_id": "twstock_updater",
        "run_id": "fixture-run",
        "published_at": "2026-10-05T23:38:07+00:00",
        "datasets": datasets,
    }
    _write_pointer(root, document)
    return document


def _write_pointer(root: Path, document: Any) -> Path:
    pointer = root / "_metadata" / "stock_data_current.json"
    pointer.parent.mkdir(parents=True, exist_ok=True)
    pointer.write_text(json.dumps(document), encoding="utf-8")
    return pointer


def _report(tmp_path: Path, *, strict_publication: bool = False) -> Any:
    return verifier.HealthReport(
        tmp_path / "stock",
        tmp_path / "mix",
        strict_publication=strict_publication,
    )


def _check(report: Any, root: Path, key: str) -> dict[str, Any]:
    category = root / "category" / key
    category.mkdir(parents=True, exist_ok=True)
    verifier._check_source_directory(
        report,
        role=f"stock:{key}",
        configured=key,
        path=category,
        spec=_spec(key),
        stock_root=root.parent / "stock",
        mix_root=root.parent / "mix",
        shared_root=root,
    )
    return report.payload["resolved_paths"][-1]


def _codes(report: Any) -> list[str]:
    return [issue["code"] for issue in report.payload["issues"]]


def test_published_categories_are_bound_to_their_updater_routes() -> None:
    bound = {
        spec.key: spec.publication_route
        for spec in verifier.CATEGORY_SPECS
        if spec.publication_route
    }
    assert bound == PUBLICATION_CATEGORIES
    for key in PUBLICATION_CATEGORIES:
        assert _spec(key).json_pattern is None
        assert _spec(key).required_directory is True
    for key, pattern in LEGACY_SAMPLE_CATEGORIES.items():
        assert _spec(key).json_pattern == pattern
        assert _spec(key).publication_route is None


def test_intact_publication_satisfies_every_published_category(tmp_path: Path) -> None:
    root = tmp_path / "shared"
    root.mkdir()
    document = _write_publication(root, partial_alternate=True)
    report = _report(tmp_path)

    entries = {key: _check(report, root, key) for key in PUBLICATION_CATEGORIES}

    # Odd-indexed fixture routes are PARTIAL: they pass, but never silently.
    partial = [
        item["route_id"] for item in document["datasets"] if item["status"] == "PARTIAL"
    ]
    assert len(partial) == 3
    assert [
        (issue["severity"], issue["code"]) for issue in report.payload["issues"]
    ] == [("info", "publication_route_partial")] * len(partial)
    assert [issue["message"].split("：")[0] for issue in report.payload["issues"]] == partial
    assert report.payload["publication"]["partial_routes"] == partial
    assert report.payload["totals"]["notices"] == len(partial)
    assert report.payload["totals"]["errors"] == 0
    assert report.payload["totals"]["warnings"] == 0
    assert report.finish() == verifier.EXIT_PASS
    by_route = {item["route_id"]: item for item in document["datasets"]}
    for key, route_id in PUBLICATION_CATEGORIES.items():
        entry = entries[key]
        assert entry["status"] == "pass"
        assert entry["publication_route"] == route_id
        assert entry["publication_status"] == by_route[route_id]["status"]
        assert entry.get("publication_partial", False) is (route_id in partial)
        assert entry["publication_object"] == str(
            (root / by_route[route_id]["path"]).resolve()
        )
    assert report.payload["publication"]["route_count"] == len(PUBLICATION_CATEGORIES)
    assert report.payload["publication"]["producer_id"] == "twstock_updater"
    assert report.payload["totals"]["json_samples_checked"] == 1


def test_pointer_is_read_once_and_its_failure_is_reported_once(tmp_path: Path) -> None:
    root = tmp_path / "shared"
    root.mkdir()
    report = _report(tmp_path)

    entries = [_check(report, root, key) for key in PUBLICATION_CATEGORIES]

    assert [entry["status"] for entry in entries] == ["fail"] * len(PUBLICATION_CATEGORIES)
    assert _codes(report) == ["publication_pointer_invalid"]
    assert report.finish() == verifier.EXIT_FAIL


def test_legacy_files_in_the_category_directory_are_not_publication_evidence(
    tmp_path: Path,
) -> None:
    root = tmp_path / "shared"
    root.mkdir()
    legacy = root / "category" / "providers_finmind_json"
    legacy.mkdir(parents=True)
    (legacy / "TaiwanStockInfo_20260913.json").write_text("{}", encoding="utf-8")
    report = _report(tmp_path)

    entry = _check(report, root, "providers_finmind_json")

    assert entry["status"] == "fail"
    assert entry["json_sample"] is None
    assert _codes(report) == ["publication_pointer_invalid"]


def _corrupt_pointer_text(root: Path, document: dict[str, Any]) -> None:
    (root / "_metadata" / "stock_data_current.json").write_text("{broken", encoding="utf-8")


def _corrupt_pointer_schema(root: Path, document: dict[str, Any]) -> None:
    _write_pointer(root, {**document, "schema": "stock-data-publication-v0"})


def _corrupt_pointer_not_an_object(root: Path, document: dict[str, Any]) -> None:
    _write_pointer(root, [document])


def _corrupt_datasets_not_a_list(root: Path, document: dict[str, Any]) -> None:
    _write_pointer(root, {**document, "datasets": {"official_calendar": {}}})


def _corrupt_duplicate_route(root: Path, document: dict[str, Any]) -> None:
    _write_pointer(
        root, {**document, "datasets": [*document["datasets"], document["datasets"][0]]}
    )


def _corrupt_route_id(root: Path, document: dict[str, Any]) -> None:
    _write_pointer(
        root, {**document, "datasets": [*document["datasets"], {"route_id": ""}]}
    )


@pytest.mark.parametrize(
    "corrupt",
    [
        _corrupt_pointer_text,
        _corrupt_pointer_schema,
        _corrupt_pointer_not_an_object,
        _corrupt_datasets_not_a_list,
        _corrupt_duplicate_route,
        _corrupt_route_id,
    ],
)
def test_invalid_pointer_fails_every_published_category(
    tmp_path: Path, corrupt: Any
) -> None:
    root = tmp_path / "shared"
    root.mkdir()
    corrupt(root, _write_publication(root))
    report = _report(tmp_path)

    statuses = [_check(report, root, key)["status"] for key in PUBLICATION_CATEGORIES]

    assert statuses == ["fail"] * len(PUBLICATION_CATEGORIES)
    assert set(_codes(report)) == {"publication_pointer_invalid"}
    assert len(_codes(report)) == 1
    assert report.finish() == verifier.EXIT_FAIL


def _route(document: dict[str, Any], route_id: str) -> dict[str, Any]:
    return next(item for item in document["datasets"] if item["route_id"] == route_id)


def _drop_route(root: Path, document: dict[str, Any]) -> None:
    document["datasets"] = [
        item for item in document["datasets"] if item["route_id"] != "official_calendar"
    ]
    _write_pointer(root, document)


def _bad_digest(root: Path, document: dict[str, Any]) -> None:
    _route(document, "official_calendar")["sha256"] = "G" * 64
    _write_pointer(root, document)


def _upper_digest(root: Path, document: dict[str, Any]) -> None:
    entry = _route(document, "official_calendar")
    entry["sha256"] = entry["sha256"].upper()
    _write_pointer(root, document)


def _bool_size(root: Path, document: dict[str, Any]) -> None:
    _route(document, "official_calendar")["size"] = True
    _write_pointer(root, document)


def _negative_size(root: Path, document: dict[str, Any]) -> None:
    _route(document, "official_calendar")["size"] = -1
    _write_pointer(root, document)


def _missing_path(root: Path, document: dict[str, Any]) -> None:
    del _route(document, "official_calendar")["path"]
    _write_pointer(root, document)


def _size_mismatch(root: Path, document: dict[str, Any]) -> None:
    _route(document, "official_calendar")["size"] += 1
    _write_pointer(root, document)


def _object_missing(root: Path, document: dict[str, Any]) -> None:
    (root / _route(document, "official_calendar")["path"]).unlink()


def _object_escapes_root(root: Path, document: dict[str, Any]) -> None:
    entry = _route(document, "official_calendar")
    outside = root.parent / f"{entry['sha256']}.json"
    outside.write_bytes((root / entry["path"]).read_bytes())
    entry["path"] = f"../{outside.name}"
    _write_pointer(root, document)


def _object_outside_object_directory(root: Path, document: dict[str, Any]) -> None:
    entry = _route(document, "official_calendar")
    elsewhere = root / "providers" / f"{entry['sha256']}.json"
    elsewhere.parent.mkdir(parents=True)
    elsewhere.write_bytes((root / entry["path"]).read_bytes())
    entry["path"] = f"providers/{elsewhere.name}"
    _write_pointer(root, document)


def _object_not_named_by_its_digest(root: Path, document: dict[str, Any]) -> None:
    entry = _route(document, "official_calendar")
    renamed = root / "_objects" / "latest.json"
    renamed.write_bytes((root / entry["path"]).read_bytes())
    entry["path"] = "_objects/latest.json"
    _write_pointer(root, document)


@pytest.mark.parametrize(
    ("break_route", "code"),
    [
        (_drop_route, "publication_route_missing"),
        (_bad_digest, "publication_object_invalid"),
        (_upper_digest, "publication_object_invalid"),
        (_bool_size, "publication_object_invalid"),
        (_negative_size, "publication_object_invalid"),
        (_missing_path, "publication_object_invalid"),
        (_size_mismatch, "publication_object_invalid"),
        (_object_missing, "publication_object_invalid"),
        (_object_escapes_root, "publication_object_invalid"),
        (_object_outside_object_directory, "publication_object_invalid"),
        (_object_not_named_by_its_digest, "publication_object_invalid"),
    ],
)
def test_broken_route_fails_only_its_own_category(
    tmp_path: Path, break_route: Any, code: str
) -> None:
    root = tmp_path / "shared"
    root.mkdir()
    break_route(root, _write_publication(root))
    report = _report(tmp_path)

    entries = {key: _check(report, root, key) for key in PUBLICATION_CATEGORIES}

    assert entries["official_calendar"]["status"] == "fail"
    assert [key for key, entry in entries.items() if entry["status"] != "pass"] == [
        "official_calendar"
    ]
    assert _codes(report) == [code]
    assert report.payload["issues"][0]["severity"] == "error"
    assert report.finish() == verifier.EXIT_FAIL


def test_missing_category_directory_is_still_an_error_without_reading_the_pointer(
    tmp_path: Path,
) -> None:
    root = tmp_path / "shared"
    root.mkdir()
    _write_publication(root)
    report = _report(tmp_path)

    verifier._check_source_directory(
        report,
        role="stock:official_calendar",
        configured="official/calendar",
        path=root / "official" / "calendar",
        spec=_spec("official_calendar"),
        stock_root=tmp_path / "stock",
        mix_root=tmp_path / "mix",
        shared_root=root,
    )

    assert _codes(report) == ["source_directory_missing"]
    assert report.publication_loaded is False


def test_legacy_sample_category_still_needs_a_readable_json_file(tmp_path: Path) -> None:
    root = tmp_path / "shared"
    root.mkdir()
    report = _report(tmp_path)

    entry = _check(report, root, "prices_history")
    assert entry["status"] == "fail"
    assert _codes(report) == ["required_json_sample_missing"]

    report = _report(tmp_path)
    (root / "category" / "prices_history" / "TWSE_2330.json").write_text(
        "{broken", encoding="utf-8"
    )
    entry = _check(report, root, "prices_history")
    assert entry["status"] == "fail"
    assert _codes(report) == ["invalid_runtime_json_sample"]


def test_reference_data_wrapper_accepts_the_contract_versions_of_the_python_loader() -> None:
    script = (Path(__file__).resolve().parent / "VERIFY_SHARED_REFERENCE_DATA.ps1").read_text(
        encoding="utf-8"
    )
    declared = re.search(r"\$supportedContractVersions = @\((?P<values>[^)]*)\)", script)

    assert declared is not None
    versions = frozenset(re.findall(r'"([^"]+)"', declared.group("values")))
    assert versions == path_contract._SUPPORTED_CONTRACT_VERSIONS
    assert path_contract.EXPECTED_CONTRACT_VERSION in versions
    assert "-notcontains [string]$pathContract.contract_version" in script


_ABSENT = object()


def _set_status(document: dict[str, Any], route_id: str, status: Any) -> None:
    entry = _route(document, route_id)
    if status is _ABSENT:
        del entry["status"]
    else:
        entry["status"] = status


def test_complete_publication_reports_nothing(tmp_path: Path) -> None:
    root = tmp_path / "shared"
    root.mkdir()
    document = _write_publication(root)
    for item in document["datasets"]:
        item["status"] = "SUCCESS"
    _write_pointer(root, document)
    report = _report(tmp_path)

    entries = [_check(report, root, key) for key in PUBLICATION_CATEGORIES]

    assert [entry["status"] for entry in entries] == ["pass"] * len(entries)
    assert report.payload["issues"] == []
    assert report.payload["publication"]["partial_routes"] == []
    assert report.payload["totals"]["notices"] == 0
    assert report.finish() == verifier.EXIT_PASS


def test_partial_route_passes_by_default_but_is_always_reported(tmp_path: Path) -> None:
    root = tmp_path / "shared"
    root.mkdir()
    document = _write_publication(root)
    for item in document["datasets"]:
        item["status"] = "SUCCESS"
    _set_status(document, "official_market_daily", "PARTIAL")
    _write_pointer(root, document)
    report = _report(tmp_path)

    entries = {key: _check(report, root, key) for key in PUBLICATION_CATEGORIES}

    assert entries["official_market_daily"]["status"] == "pass"
    assert entries["official_market_daily"]["publication_partial"] is True
    assert [key for key, entry in entries.items() if entry.get("publication_partial")] == [
        "official_market_daily"
    ]
    assert len(report.payload["issues"]) == 1
    issue = report.payload["issues"][0]
    assert issue["severity"] == "info"
    assert issue["code"] == "publication_route_partial"
    assert issue["context"] == "stock:official_market_daily"
    assert report.payload["publication"]["partial_routes"] == ["official_market_daily"]
    assert report.payload["totals"]["notices"] == 1
    assert report.finish() == verifier.EXIT_PASS


def test_strict_publication_turns_every_partial_route_into_an_error(
    tmp_path: Path,
) -> None:
    root = tmp_path / "shared"
    root.mkdir()
    document = _write_publication(root, partial_alternate=True)
    partial = [
        item["route_id"] for item in document["datasets"] if item["status"] == "PARTIAL"
    ]
    report = _report(tmp_path, strict_publication=True)

    entries = {key: _check(report, root, key) for key in PUBLICATION_CATEGORIES}

    failed = [
        PUBLICATION_CATEGORIES[key] for key, entry in entries.items() if entry["status"] == "fail"
    ]
    assert failed == partial
    assert {(issue["severity"], issue["code"]) for issue in report.payload["issues"]} == {
        ("error", "publication_route_partial")
    }
    assert len(report.payload["issues"]) == len(partial)
    assert report.payload["totals"]["notices"] == 0
    assert report.finish() == verifier.EXIT_FAIL


@pytest.mark.parametrize(
    "status",
    [
        _ABSENT,
        None,
        "",
        "FAILED",
        "BLOCKED",
        "WAITING_QUOTA",
        "EMPTY",
        "success",
        "Partial",
        1,
        True,
        ["SUCCESS"],
    ],
)
def test_any_other_status_fails_only_its_own_category(tmp_path: Path, status: Any) -> None:
    root = tmp_path / "shared"
    root.mkdir()
    document = _write_publication(root)
    for item in document["datasets"]:
        item["status"] = "SUCCESS"
    _set_status(document, "official_calendar", status)
    _write_pointer(root, document)
    report = _report(tmp_path)

    entries = {key: _check(report, root, key) for key in PUBLICATION_CATEGORIES}

    assert entries["official_calendar"]["status"] == "fail"
    assert [key for key, entry in entries.items() if entry["status"] != "pass"] == [
        "official_calendar"
    ]
    assert _codes(report) == ["publication_route_status_invalid"]
    assert report.payload["issues"][0]["severity"] == "error"
    assert report.finish() == verifier.EXIT_FAIL


def test_a_broken_object_is_reported_as_such_even_when_the_route_is_partial(
    tmp_path: Path,
) -> None:
    root = tmp_path / "shared"
    root.mkdir()
    document = _write_publication(root)
    _set_status(document, "official_calendar", "PARTIAL")
    _write_pointer(root, document)
    _size_mismatch(root, document)
    report = _report(tmp_path)

    entry = _check(report, root, "official_calendar")

    assert entry["status"] == "fail"
    assert _codes(report) == ["publication_object_invalid"]
    assert report.payload["publication"]["partial_routes"] == []


@pytest.mark.parametrize(
    ("schema", "accepted"),
    [
        ("stock-data-publication-v1", True),
        ("stock-data-publication-v2", True),
        ("stock-data-publication-v3", False),
        ("stock-data-publication", False),
    ],
)
def test_pointer_schema_accepts_exactly_the_versions_the_shared_reader_accepts(
    tmp_path: Path, schema: str, accepted: bool
) -> None:
    root = tmp_path / "shared"
    root.mkdir()
    _write_publication(root, schema=schema)
    report = _report(tmp_path)

    statuses = {_check(report, root, key)["status"] for key in PUBLICATION_CATEGORIES}

    if accepted:
        assert statuses == {"pass"}
        assert report.payload["publication"]["schema"] == schema
        assert report.finish() == verifier.EXIT_PASS
    else:
        assert statuses == {"fail"}
        assert _codes(report) == ["publication_pointer_invalid"]
        assert report.finish() == verifier.EXIT_FAIL


def test_pointer_schema_set_matches_the_shared_reader() -> None:
    # The current shared head reader (stock_data_paths.py was retired in the current-only cleanup).
    source = (PROGRAMS_ROOT / "single_stock_analysis" / "src" / "stock_data_sources.py").read_text(encoding="utf-8")
    declared = set(re.findall(r"'(stock-data-publication-v[0-9]+)'", source))
    assert declared == set(verifier.PUBLICATION_SCHEMAS)


def test_strict_publication_flag_reaches_the_health_check(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    seen: list[bool] = []

    def fake_check(
        *, stock_root: Path, mix_root: Path, strict_publication: bool = False, **_: Any
    ) -> tuple[dict[str, Any], int]:
        seen.append(strict_publication)
        totals = dict.fromkeys(
            ("paths_checked", "json_samples_checked", "mix_configs_checked",
             "errors", "warnings", "notices"),
            0,
        )
        return {"totals": totals, "issues": []}, verifier.EXIT_PASS

    monkeypatch.setattr(verifier, "run_health_check", fake_check)

    assert verifier.main(["--no-write", "--quiet"]) == verifier.EXIT_PASS
    assert verifier.main(["--no-write", "--quiet", "--strict-publication"]) == verifier.EXIT_PASS
    assert seen == [False, True]
