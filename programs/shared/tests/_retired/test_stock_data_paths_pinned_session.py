from __future__ import annotations

import hashlib
import json
from pathlib import Path
import sys
from types import SimpleNamespace
from typing import Any

import pytest


SHARED_DIR = Path(__file__).resolve().parents[1]
if str(SHARED_DIR) not in sys.path:
    sys.path.insert(0, str(SHARED_DIR))

import central_publication_reader as reader
import stock_data_paths


EXPECTED_ARTIFACTS = (
    ("finmind_stock_info", "providers/finmind/json/finmind_stock_info.json"),
    (
        "official_finance_reporting_deadline",
        "official/finance_reporting_deadline/latest.json",
    ),
    ("finmind_price_adj", "providers/finmind/json/finmind_price_adj.json"),
    ("finmind_per", "providers/finmind/json/finmind_per.json"),
    ("finmind_month_revenue", "providers/finmind/json/finmind_month_revenue.json"),
    ("finmind_institutional", "providers/finmind/json/finmind_institutional.json"),
    ("finmind_margin", "providers/finmind/json/finmind_margin.json"),
    ("finmind_day_trading", "providers/finmind/json/finmind_day_trading.json"),
    ("finmind_shareholding", "providers/finmind/json/finmind_shareholding.json"),
    (
        "finmind_loan_collateral",
        "providers/finmind/json/extended/finmind_loan_collateral.json",
    ),
    (
        "finmind_financial_statements",
        "providers/finmind/json/finmind_financial_statements.json",
    ),
    ("finmind_balance_sheet", "providers/finmind/json/finmind_balance_sheet.json"),
    (
        "finmind_cash_flows_statement",
        "providers/finmind/json/finmind_cash_flows_statement.json",
    ),
    ("finmind_dividend", "providers/finmind/json/finmind_dividend.json"),
    (
        "finmind_dividend_result",
        "providers/finmind/json/finmind_dividend_result.json",
    ),
    ("official_calendar", "official/calendar/latest.json"),
)
EXPECTED_ROUTES = tuple(dataset_id for dataset_id, _ in EXPECTED_ARTIFACTS)
COMMON_PUBLICATION_ROUTES = (
    "finmind_price_adj",
    "finmind_per",
    "finmind_month_revenue",
    "finmind_institutional",
    "finmind_margin",
    "finmind_day_trading",
    "finmind_shareholding",
    "finmind_loan_collateral",
    "finmind_financial_statements",
    "finmind_balance_sheet",
    "finmind_cash_flows_statement",
    "finmind_dividend",
    "finmind_dividend_result",
)
COMMON_PUBLICATION_SCHEMA = "stock-data-publication-v1"
ANALYZER_SOURCE = (
    Path(__file__).resolve().parents[2]
    / "single_stock_analysis"
    / "src"
    / "tw_single_stock_3m_analyzer.py"
)


def _paths_fixture(tmp_path: Path) -> stock_data_paths.StockDataPaths:
    root = tmp_path / "shared_market_data"
    root.mkdir(parents=True)
    return stock_data_paths.StockDataPaths(
        root=root,
        price_history=root / "prices" / "history",
        finmind_json=root / "providers" / "finmind" / "json",
        global_markets=root / "providers" / "yahoo" / "global_markets",
        official_calendar=root / "official" / "calendar",
        official_market_daily=root / "official" / "market_daily",
        official_sector_features=root / "official" / "sector_features",
        official_industry_operational=root / "industry_operational",
        official_trading_restrictions=root / "official" / "trading_restrictions",
        broker_normalized_cache=root / "brokers" / "normalized_cache",
        broker_imports=root / "brokers" / "imports",
        broker_provider_raw=root / "brokers" / "provider_raw",
        broker_finlab_cache=root / "brokers" / "finlab_cache",
    )


def _write_manifest(root: Path, document: Any) -> Path:
    metadata = root / "_metadata"
    metadata.mkdir(parents=True, exist_ok=True)
    manifest = metadata / "stock_data_current.json"
    manifest.write_text(json.dumps(document), encoding="utf-8")
    return manifest


def _write_common_publication(root: Path) -> dict[str, Any]:
    objects = root / "_objects"
    objects.mkdir(parents=True, exist_ok=True)
    datasets: list[dict[str, Any]] = []
    for index, route_id in enumerate((*COMMON_PUBLICATION_ROUTES, "finmind_stock_info", "official_calendar")):
        payload = json.dumps({"route_id": route_id, "rows": index}).encode("utf-8")
        digest = hashlib.sha256(payload).hexdigest()
        (objects / f"{digest}.json").write_bytes(payload)
        datasets.append(
            {
                "route_id": route_id,
                "path": f"_objects/{digest}.json",
                "sha256": digest,
                "size": len(payload),
                "status": "SUCCESS",
            }
        )
    document = {
        "schema": COMMON_PUBLICATION_SCHEMA,
        "producer_id": "twstock_updater",
        "run_id": "fixture-run",
        "datasets": datasets,
    }
    _write_manifest(root, document)
    return document


def _forbid_pinned_session(monkeypatch: pytest.MonkeyPatch) -> None:
    def unexpected_create(*args: object, **kwargs: object) -> object:
        raise AssertionError("the common publication check must not create a pinned session")

    def unexpected_read(*args: object, **kwargs: object) -> object:
        raise AssertionError("the common publication check must not read a Central artifact")

    monkeypatch.setattr(reader, "create_central_evidence_session", unexpected_create)
    monkeypatch.setattr(reader, "read_central_artifact", unexpected_read)
    monkeypatch.setattr(stock_data_paths, "read_central_artifact", unexpected_read)


def test_single_stock_session_routes_and_artifacts_are_the_sixteen_pinned_targets() -> None:
    assert stock_data_paths.CENTRAL_SINGLE_STOCK_SESSION_ROUTE_IDS == EXPECTED_ROUTES
    assert stock_data_paths.CENTRAL_RECEIPT_ARTIFACTS == EXPECTED_ARTIFACTS
    assert len(EXPECTED_ROUTES) == len(set(EXPECTED_ROUTES)) == 16


def test_validate_stock_data_paths_without_session_checks_common_publication_only(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    paths = _paths_fixture(tmp_path)
    _write_common_publication(paths.root)
    _forbid_pinned_session(monkeypatch)

    assert stock_data_paths.validate_stock_data_paths(paths) is True


def test_validate_stock_data_paths_bootstrap_and_root_errors(tmp_path: Path) -> None:
    missing = _paths_fixture(tmp_path / "missing")
    missing.root.rmdir()

    assert stock_data_paths.validate_stock_data_paths(missing, allow_bootstrap=True) is False
    with pytest.raises(stock_data_paths.SharedDataPathError, match="共用資料根目錄不存在"):
        stock_data_paths.validate_stock_data_paths(missing)

    not_a_directory = _paths_fixture(tmp_path / "file")
    not_a_directory.root.rmdir()
    not_a_directory.root.write_text("not a directory", encoding="utf-8")
    with pytest.raises(stock_data_paths.SharedDataPathError, match="不是資料夾"):
        stock_data_paths.validate_stock_data_paths(not_a_directory)


def _entry(root: Path, route_id: str) -> tuple[dict[str, Any], dict[str, Any]]:
    manifest = root / "_metadata" / "stock_data_current.json"
    document = json.loads(manifest.read_text(encoding="utf-8"))
    entry = next(item for item in document["datasets"] if item["route_id"] == route_id)
    return document, entry


def _break_schema(root: Path) -> None:
    document, _ = _entry(root, "finmind_per")
    document["schema"] = "stock-data-publication-v0"
    _write_manifest(root, document)


def _break_missing_core_route(root: Path) -> None:
    document, _ = _entry(root, "finmind_dividend")
    document["datasets"] = [
        item for item in document["datasets"] if item["route_id"] != "finmind_dividend"
    ]
    _write_manifest(root, document)


def _break_digest(root: Path) -> None:
    document, entry = _entry(root, "finmind_per")
    entry["sha256"] = "z" * 64
    _write_manifest(root, document)


def _break_digest_missing(root: Path) -> None:
    document, entry = _entry(root, "finmind_per")
    del entry["sha256"]
    _write_manifest(root, document)


def _break_size_type(root: Path) -> None:
    document, entry = _entry(root, "finmind_per")
    entry["size"] = True
    _write_manifest(root, document)


def _break_size_mismatch(root: Path) -> None:
    document, entry = _entry(root, "finmind_per")
    entry["size"] = entry["size"] + 1
    _write_manifest(root, document)


def _break_object_missing(root: Path) -> None:
    _, entry = _entry(root, "finmind_per")
    (root / entry["path"]).unlink()


def _break_path_escape(root: Path) -> None:
    document, entry = _entry(root, "finmind_per")
    outside = root.parent / "outside.json"
    outside.write_bytes((root / entry["path"]).read_bytes())
    entry["path"] = "../outside.json"
    _write_manifest(root, document)


def _break_path_absolute(root: Path) -> None:
    document, entry = _entry(root, "finmind_per")
    outside = root.parent / "outside_absolute.json"
    outside.write_bytes((root / entry["path"]).read_bytes())
    entry["path"] = str(outside)
    _write_manifest(root, document)


@pytest.mark.parametrize(
    ("break_publication", "message"),
    [
        (_break_schema, "shared publication schema invalid"),
        (_break_missing_core_route, "common publication missing core routes: finmind_dividend"),
        (_break_digest, "common payload hash/size missing: finmind_per"),
        (_break_digest_missing, "common payload hash/size missing: finmind_per"),
        (_break_size_type, "common payload hash/size missing: finmind_per"),
        (_break_size_mismatch, "common payload path/size invalid: finmind_per"),
        (_break_object_missing, "common payload path/size invalid: finmind_per"),
        (_break_path_escape, "common payload path/size invalid: finmind_per"),
        (_break_path_absolute, "common payload path/size invalid: finmind_per"),
    ],
)
def test_validate_stock_data_paths_without_session_fails_closed_on_publication_drift(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    break_publication: Any,
    message: str,
) -> None:
    paths = _paths_fixture(tmp_path)
    _write_common_publication(paths.root)
    break_publication(paths.root)
    _forbid_pinned_session(monkeypatch)

    with pytest.raises(stock_data_paths.SharedDataPathError, match=message):
        stock_data_paths.validate_stock_data_paths(paths)


def test_validate_stock_data_paths_without_session_requires_the_publication_pointer(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    paths = _paths_fixture(tmp_path)
    _forbid_pinned_session(monkeypatch)

    with pytest.raises((stock_data_paths.SharedDataPathError, OSError)):
        stock_data_paths.validate_stock_data_paths(paths)


def _validate_with_supplied_session(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> tuple[SimpleNamespace, list[tuple[str, Path, object]]]:
    paths = _paths_fixture(tmp_path)
    session = SimpleNamespace(
        route_ids=EXPECTED_ROUTES,
        receipt_path=tmp_path / "latest_manifest.json",
    )
    reader_calls: list[tuple[str, Path, object]] = []

    def unexpected_create(*, receipt_path: Path, route_ids: tuple[str, ...]) -> object:
        raise AssertionError("an existing pinned session must not create another session")

    def read_artifact(
        dataset_id: str,
        *,
        receipt_path: Path,
        session: object,
    ) -> object:
        reader_calls.append((dataset_id, receipt_path, session))
        target = dict(EXPECTED_ARTIFACTS)[dataset_id]
        return SimpleNamespace(
            data_path=paths.root / Path(*target.split("/")),
        )

    real_path = Path

    def path_factory(*parts: str) -> Path:
        if parts == (r"D:\TWStockData\shared_market_data",):
            return paths.root
        return real_path(*parts)

    monkeypatch.setattr(reader, "create_central_evidence_session", unexpected_create)
    monkeypatch.setattr(stock_data_paths, "read_central_artifact", read_artifact)
    monkeypatch.setattr(stock_data_paths, "Path", path_factory)

    assert stock_data_paths.validate_stock_data_paths(paths, session=session) is True
    return session, reader_calls


def test_validate_stock_data_paths_reuses_supplied_session_without_creation(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    session, reader_calls = _validate_with_supplied_session(monkeypatch, tmp_path)

    assert [dataset_id for dataset_id, _path, _session in reader_calls] == list(
        EXPECTED_ROUTES
    )
    assert all(received_path == session.receipt_path for _, received_path, _ in reader_calls)
    assert all(received_session is session for *_, received_session in reader_calls)
    assert len(reader_calls) == len(EXPECTED_ROUTES) == 16


def test_validate_stock_data_paths_uses_one_pinned_16_route_session(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    """Successor of the original test of the same name.

    The original asserted that a session-less call creates the pinned 16-route session.  Production
    now answers a session-less call from the common publication pointer alone (see
    ``validate_stock_data_paths``), so that call must create no session and read no artifact; the
    pinned 16-route contract is exercised through the one session a caller supplies: all sixteen
    artifacts are read through exactly that object, in registry order, and no second session exists.
    """

    assert stock_data_paths.CENTRAL_SINGLE_STOCK_SESSION_ROUTE_IDS == EXPECTED_ROUTES
    assert stock_data_paths.CENTRAL_RECEIPT_ARTIFACTS == EXPECTED_ARTIFACTS

    session_less_paths = _paths_fixture(tmp_path / "session_less")
    _write_common_publication(session_less_paths.root)
    with monkeypatch.context() as sessionless:
        _forbid_pinned_session(sessionless)
        assert stock_data_paths.validate_stock_data_paths(session_less_paths) is True

    session, reader_calls = _validate_with_supplied_session(monkeypatch, tmp_path / "supplied")

    assert [dataset_id for dataset_id, _path, _session in reader_calls] == [
        dataset_id for dataset_id, _target in EXPECTED_ARTIFACTS
    ]
    assert all(received_path == session.receipt_path for _, received_path, _ in reader_calls)
    assert all(received_session is session for *_, received_session in reader_calls)
    assert len({id(received_session) for *_, received_session in reader_calls}) == 1
    assert len(reader_calls) == 16


@pytest.mark.parametrize(
    ("label", "route_ids"),
    [
        ("missing", EXPECTED_ROUTES[:-1]),
        ("reordered", EXPECTED_ROUTES[1:] + EXPECTED_ROUTES[:1]),
    ],
)
def test_validate_stock_data_paths_rejects_session_route_drift_before_artifacts(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    label: str,
    route_ids: tuple[str, ...],
) -> None:
    paths = _paths_fixture(tmp_path / label)
    session = SimpleNamespace(
        route_ids=route_ids,
        receipt_path=tmp_path / label / "latest_manifest.json",
    )

    def unexpected_create(*, receipt_path: Path, route_ids: tuple[str, ...]) -> object:
        raise AssertionError("route drift must not be repaired by creating another session")

    def unexpected_read(*args: object, **kwargs: object) -> object:
        raise AssertionError("route drift must fail before reading any artifact")

    monkeypatch.setattr(reader, "create_central_evidence_session", unexpected_create)
    monkeypatch.setattr(stock_data_paths, "read_central_artifact", unexpected_read)

    with pytest.raises(
        stock_data_paths.SharedDataPathError,
        match="route set mismatch",
    ):
        stock_data_paths.validate_stock_data_paths(paths, session=session)


def test_three_month_formal_preflight_forwards_session_and_keys_cache_by_identity() -> None:
    source = ANALYZER_SOURCE.read_text(encoding="utf-8")
    prepare_start = source.index("def prepare_single_stock_runtime_paths(")
    prepare_end = source.index("\n\n_CENTRAL_SHA256_RE", prepare_start)
    prepare_source = source[prepare_start:prepare_end]
    assert "session: Any = None" in prepare_source
    assert "id(session) if session is not None else None" in prepare_source
    assert "validate_stock_data_paths(\n            SHARED_DATA_PATHS," in prepare_source
    assert "session=session," in prepare_source
    assert prepare_source.count("_shared_data_preflight_signature") >= 2

    def _function_source(function_name: str, next_function_name: str) -> str:
        start = source.index(f"def {function_name}(")
        end = source.index(f"\ndef {next_function_name}(", start)
        return source[start:end]

    for function_name, next_function_name in (
        ("run_analysis", "run_requested_stock"),
        ("run_requested_stock", "main"),
    ):
        function_source = _function_source(function_name, next_function_name)
        assert 'session=central_context["evidence_session"]' in function_source
