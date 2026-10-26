from __future__ import annotations

from datetime import datetime
import json
import os
from pathlib import Path
import sys
from types import SimpleNamespace

import pytest

SHARED_DIR = Path(__file__).resolve().parents[1]
if str(SHARED_DIR) not in sys.path:
    sys.path.insert(0, str(SHARED_DIR))
SINGLE_ROOT = SHARED_DIR.parent / "single_stock_analysis"
SINGLE_SRC = SINGLE_ROOT / "src"
if str(SINGLE_SRC) not in sys.path:
    sys.path.insert(0, str(SINGLE_SRC))

import market_update_client as client


# The bridge exports its session into the process environment and the single
# stock client refuses legacy or network opt-ins, so a test must neither start
# from the machine's settings nor leave its own behind for the next test.
_AMBIENT_ENVIRONMENT = (
    client.CENTRAL_READY_ENV,
    client.SHARED_RAW_NETWORK_DISABLED_ENV,
    client.EXPECTED_ASOF_ENV,
    "DStock_ALLOW_LEGACY_FINMIND_CACHE",
    "DStock_CENTRAL_PUBLICATION_MODE",
    "ENABLE_FINMIND_FETCH",
)


@pytest.fixture(autouse=True)
def hermetic_environment():
    saved = dict(os.environ)
    for name in _AMBIENT_ENVIRONMENT:
        os.environ.pop(name, None)
    try:
        yield
    finally:
        os.environ.clear()
        os.environ.update(saved)


def _fake_runner(responses, calls):
    def run(command, **kwargs):
        calls.append((command, kwargs))
        return responses.pop(0)

    return run


def _stub_receipt_validation(monkeypatch, tmp_path):
    receipt_path = tmp_path / "latest_manifest.json"
    receipt_path.write_text(
        json.dumps({"expected_asof": "2026-08-20"}),
        encoding="utf-8",
    )
    monkeypatch.setattr(client, "CENTRAL_LATEST_MANIFEST", receipt_path)
    monkeypatch.setattr(
        client,
        "_validate_central_receipt",
        lambda receipt, verify_payload, expected_asof: {
            "central_receipt_run_id": "central-run-1",
            "central_publication_run_ids": {},
            "registry_sha256": "a" * 64,
            "manifest_content_sha256": "b" * 64,
            "receipt_path": str(receipt_path),
        },
    )


_PINNED_SESSION_DOTENV_KEYS = (
    "DStock_CENTRAL_SESSION_DESCRIPTOR",
    "DStock_CENTRAL_SESSION_DESCRIPTOR_SHA256",
    "DStock_CENTRAL_SESSION_DESCRIPTOR_SCHEMA",
    "DStock_CENTRAL_SESSION_EXPECTED_ASOF",
    "DStock_MARKET_UPDATE_EXPECTED_ASOF",
    "DStock_CENTRAL_RECEIPT_PATH",
    "DStock_CENTRAL_RECEIPT_MANIFEST",
    "DStock_CENTRAL_RECEIPT_SHA256",
    "DStock_CENTRAL_RECEIPT_RUN_ID",
    "DStock_CENTRAL_ORCHESTRATION_RUN_ID",
    "DStock_CENTRAL_MANIFEST_SHA256",
    "DStock_CENTRAL_MANIFEST_CONTENT_SHA256",
    "DStock_CENTRAL_REGISTRY_SHA256",
    "DStock_CENTRAL_REGISTRY_RAW_SHA256",
    "DStock_CENTRAL_REGISTRY_CANONICAL_SHA256",
    "DStock_CENTRAL_ARTIFACT_MAP_SHA256",
    "DStock_CENTRAL_SESSION_RUN_ID",
    "DStock_CENTRAL_SINGLE_STOCK_DATASET_IDS",
    "DStock_CENTRAL_SINGLE_STOCK_PUBLICATION_RUN_IDS_JSON",
    "DStock_CENTRAL_SINGLE_STOCK_DATASET_HASHES_JSON",
    "DStock_CENTRAL_SINGLE_STOCK_CONTENT_SHA256S_JSON",
    "DStock_CENTRAL_SINGLE_STOCK_ARTIFACT_LIST_SHA256S_JSON",
    "DStock_CENTRAL_RECEIPT_SEAL_JSON",
)
_ROUTE_ENV_KEYS = (
    "DStock_CENTRAL_SESSION_RUN_ID",
    "DStock_CENTRAL_SINGLE_STOCK_DATASET_IDS",
    "DStock_CENTRAL_SINGLE_STOCK_PUBLICATION_RUN_IDS_JSON",
    "DStock_CENTRAL_SINGLE_STOCK_DATASET_HASHES_JSON",
    "DStock_CENTRAL_SINGLE_STOCK_CONTENT_SHA256S_JSON",
    "DStock_CENTRAL_SINGLE_STOCK_ARTIFACT_LIST_SHA256S_JSON",
)


def _cmd_label_block(source: str, label: str) -> str:
    lines = source.splitlines()
    marker = f":{label.strip().lower()}"
    start = next(
        index
        for index, line in enumerate(lines)
        if line.strip().lower() == marker
    )
    end = next(
        (
            index
            for index in range(start + 1, len(lines))
            if lines[index].strip().startswith(":")
        ),
        len(lines),
    )
    return "\n".join(lines[start:end])


def _dotenv_loader_block(source: str) -> str:
    return _cmd_label_block(source, "LoadFormalEnv")


def test_expected_asof_uses_latest_completed_session():
    def loader(year, *, cache_dir, allow_network):
        assert allow_network is False
        return {"year": year, "rows": [{"date": f"{year}-01-01"}], "closed_dates": []}

    before_open = datetime.fromisoformat("2026-01-05T08:00:00+08:00")
    after_cutoff_intraday = datetime.fromisoformat("2026-01-05T08:30:00+08:00")
    before_close = datetime.fromisoformat("2026-01-05T13:29:00+08:00")
    after_close = datetime.fromisoformat("2026-01-05T13:31:00+08:00")
    assert client.compute_expected_asof(before_open, calendar_loader=loader) == "2026-01-02"
    assert (
        client.compute_expected_asof(after_cutoff_intraday, calendar_loader=loader)
        == "2026-01-02"
    )
    assert client.compute_expected_asof(before_close, calendar_loader=loader) == "2026-01-02"
    assert (
        client.compute_expected_asof(
            after_close,
            calendar_loader=loader,
            session_close_checker=lambda day: False,
        )
        == "2026-01-02"
    )
    assert (
        client.compute_expected_asof(
            after_close,
            calendar_loader=loader,
            session_close_checker=lambda day: True,
        )
        == "2026-01-05"
    )


def test_expected_asof_skips_weekend_and_official_holiday():
    def loader(year, *, cache_dir, allow_network):
        assert allow_network is False
        return {
            "year": year,
            "rows": [{"date": f"{year}-01-01"}],
            "closed_dates": ["2026-01-05"],
        }

    weekend = datetime.fromisoformat("2026-01-03T14:00:00+08:00")
    holiday = datetime.fromisoformat("2026-01-05T14:00:00+08:00")
    assert (
        client.compute_expected_asof(
            weekend,
            calendar_loader=loader,
            session_close_checker=lambda day: True,
        )
        == "2026-01-02"
    )
    assert (
        client.compute_expected_asof(
            holiday,
            calendar_loader=loader,
            session_close_checker=lambda day: True,
        )
        == "2026-01-02"
    )


def test_expected_asof_defaults_closed_until_central_close_receipt(monkeypatch, tmp_path):
    def loader(year, *, cache_dir, allow_network):
        assert allow_network is False
        return {"year": year, "rows": [{"date": f"{year}-01-01"}], "closed_dates": []}

    monkeypatch.setattr(
        client,
        "CENTRAL_LATEST_MANIFEST",
        tmp_path / "missing-latest-manifest.json",
    )
    assert (
        client.compute_expected_asof(
            datetime.fromisoformat("2026-01-05T14:00:00+08:00"),
            calendar_loader=loader,
        )
        == "2026-01-02"
    )


def test_expected_asof_fails_closed_when_official_calendar_unavailable():
    def loader(year, *, cache_dir, allow_network):
        raise OSError("calendar unavailable")

    with pytest.raises(client.MarketUpdateError, match="official calendar unavailable"):
        client.compute_expected_asof(
            datetime.fromisoformat("2026-01-05T10:00:00+08:00"),
            calendar_loader=loader,
        )


def test_receipt_only_bridge_calls_central_verify_without_network(monkeypatch, tmp_path):
    monkeypatch.setattr(client, "CENTRAL_MARKET_UPDATE_CMD", Path(__file__))
    _stub_receipt_validation(monkeypatch, tmp_path)
    calls = []
    responses = [
        SimpleNamespace(
            returncode=0,
            stdout=json.dumps(
                {
                    "manifest_schema": "shared-market-data-update-manifest-v1",
                    "operation": "verify",
                    "status": "complete",
                    "expected_asof": "2026-08-20",
                }
            ),
            stderr="",
        ),
    ]
    monkeypatch.delenv(client.CENTRAL_READY_ENV, raising=False)
    result = client.ensure_market_update(
        "2026-08-20",
        runner=_fake_runner(responses, calls),
    )

    assert result["expected_asof"] == "2026-08-20"
    assert result["operation"] == "verify"
    assert result["ensure"]["operation"] == "verify"
    assert [call[0][4] for call in calls] == ["verify"]
    assert calls[0][0][-2:] == ["--expected-asof", "2026-08-20"]
    assert "--allow-network" not in calls[0][0]
    assert result["shared_raw_network_disabled"] is True


def test_same_expected_asof_second_call_reverifies_receipt(monkeypatch, tmp_path):
    monkeypatch.setattr(client, "CENTRAL_MARKET_UPDATE_CMD", Path(__file__))
    _stub_receipt_validation(monkeypatch, tmp_path)
    calls = []
    responses = [
        SimpleNamespace(
            returncode=0,
            stdout=json.dumps(
                {
                    "manifest_schema": "shared-market-data-update-manifest-v1",
                    "operation": "verify",
                    "status": "complete",
                    "expected_asof": "2026-08-20",
                }
            ),
            stderr="",
        ),
        SimpleNamespace(
            returncode=0,
            stdout=json.dumps(
                {
                    "manifest_schema": "shared-market-data-update-manifest-v1",
                    "operation": "verify",
                    "status": "complete",
                    "expected_asof": "2026-08-20",
                }
            ),
            stderr="",
        ),
    ]
    runner = _fake_runner(responses, calls)
    first = client.ensure_market_update("2026-08-20", runner=runner)
    second = client.ensure_market_update("2026-08-20", runner=runner)

    assert first["ensure"]["operation"] == "verify"
    assert second["verify"]["operation"] == "verify"
    assert [call[0][4] for call in calls] == ["verify", "verify"]
    assert all("--allow-network" not in call[0] for call in calls)


def test_partial_central_verify_fails_closed_with_dataset_and_manifest(
    monkeypatch, tmp_path
):
    monkeypatch.setattr(client, "CENTRAL_MARKET_UPDATE_CMD", Path(__file__))
    _stub_receipt_validation(monkeypatch, tmp_path)
    calls = []
    responses = [
        SimpleNamespace(
            returncode=2,
            stdout=json.dumps(
                {
                    "operation": "verify",
                    "expected_asof": "2026-08-20",
                    "status": "partial",
                    "datasets": [
                        {
                            "dataset_id": "TaiwanStockPriceAdj",
                            "status": "partial",
                            "error": "manifest_missing",
                        }
                    ],
                }
            ),
            stderr="",
        )
    ]

    with pytest.raises(client.MarketUpdateError) as raised:
        client.ensure_market_update(
            "2026-08-20",
            runner=_fake_runner(responses, calls),
        )

    message = str(raised.value)
    assert "TaiwanStockPriceAdj" in message
    assert "manifest_missing" in message
    assert [call[0][4] for call in calls] == ["verify"]
    assert "--allow-network" not in calls[0][0]


def test_shared_raw_path_is_read_only_after_gate(monkeypatch):
    monkeypatch.setenv(client.SHARED_RAW_NETWORK_DISABLED_ENV, "1")
    shared_path = client.SHARED_DATA_ROOT / "providers" / "finmind" / "json" / "row.json"
    derived_path = Path(r"D:\TWStockData\program_data\dstock_single\reports\row.json")

    assert client.is_shared_raw_path(shared_path)
    assert client.shared_raw_write_blocked(shared_path)
    assert not client.shared_raw_write_blocked(derived_path)


def test_central_owned_provider_urls_are_blocked_after_gate(monkeypatch):
    monkeypatch.setenv(client.SHARED_RAW_NETWORK_DISABLED_ENV, "1")
    for url in (
        "https://api.finmindtrade.com/api/v4/data",
        "https://query1.finance.yahoo.com/v8/finance/chart/2330.TW",
        "https://api.gdeltproject.org/api/v2/doc/doc",
        "https://news.google.com/rss/search?q=2330",
    ):
        assert client.central_network_blocked(url)
        with pytest.raises(client.MarketUpdateError):
            client.assert_network_allowed(url)


def test_single_finmind_client_does_not_network_after_gate(monkeypatch, tmp_path):
    import tw_single_stock_3m_analyzer as single

    monkeypatch.setenv(client.SHARED_RAW_NETWORK_DISABLED_ENV, "1")
    network_calls = []

    def fail_network(*args, **kwargs):
        network_calls.append((args, kwargs))
        raise AssertionError("central-owned FinMind network call was attempted")

    monkeypatch.setattr(single, "retry_get_json", fail_network)
    central_projection = SimpleNamespace(
        registry=SimpleNamespace(projection_route_ids=("finmind_price_adj",)),
        dataset_status=lambda _dataset: {
            "status": "available",
            "expected_asof": "2026-08-20",
            "publication": {
                "receipt_run_id": "central-run-1",
                "publication_run_id": "central-publication-1",
                "content_sha256": "a" * 64,
            },
        },
        payload_for=lambda *_args, **_kwargs: {
            "data": [
                {"stock_id": "2330", "date": "2026-08-20", "close": "1"}
            ]
        },
    )
    finmind = single.FinMindClient(
        finmind_cache_dir=tmp_path,
        refresh_cache=True,
        central_projection=central_projection,
    )
    frame = finmind.data(
        "finmind_price_adj",
        data_id="2330",
        start_date="2026-08-01",
        end_date="2026-08-20",
        force_refresh=True,
    )

    assert not frame.empty
    assert network_calls == []
    assert finmind.dataset_status["finmind_price_adj"]["status"] == "available"


def test_single_official_industry_client_does_not_network_or_create_shared_cache(
    monkeypatch, tmp_path
):
    from tw_industry_data import OfficialDataClient

    monkeypatch.setenv(client.SHARED_RAW_NETWORK_DISABLED_ENV, "1")
    cache_root = tmp_path / "missing-central-cache"
    official = OfficialDataClient(cache_root)
    payload = official.get_bytes("industry", "https://example.invalid/industry")

    assert payload == b""
    assert not cache_root.exists()
    assert official.sources["industry"]["status"] == "central_route_unavailable"


def test_legacy_global_refresh_is_maintenance_only(monkeypatch):
    monkeypatch.delenv(client.SHARED_RAW_NETWORK_DISABLED_ENV, raising=False)
    import global_market_data

    called = []
    with pytest.raises(global_market_data.CentralOwnerOnlyError) as raised:
        global_market_data.refresh_global_market_cache(
            download_fn=lambda *args, **kwargs: called.append((args, kwargs))
        )

    assert raised.value.payload["operation"] == "global_market_refresh"
    assert called == []
    assert global_market_data.main(["refresh"]) == 2


def test_fixed_entries_pin_the_16_route_session_before_single_publish():
    root_entry = (
        SINGLE_ROOT
        / "1_執行個股分析與產生報告.cmd"
    ).read_text(encoding="utf-8")
    three_month_entry = (
        SINGLE_ROOT
        / "RUN_SINGLE_STOCK_3M_ANALYSIS.cmd"
    ).read_text(encoding="utf-8")
    tomorrow_entry = (
        SINGLE_ROOT
        / "RUN_SINGLE_STOCK_TOMORROW_OPEN.cmd"
    ).read_text(encoding="utf-8")

    root_lower = root_entry.lower()
    assert root_lower.count("call :createpinnedsession") == 1
    assert root_lower.count("call :verifypinnedsession") == 1
    assert root_lower.index("call :createpinnedsession") < root_lower.index(
        "call :verifypinnedsession"
    )
    assert root_lower.index("call :verifypinnedsession") < root_lower.index(
        'tw_single_stock_latest_report.py" --publish'
    )

    for source in (root_entry, three_month_entry, tomorrow_entry):
        lowered = source.lower()
        assert "central_single_stock_projection.py" in lowered
        assert "import_central_evidence_session_descriptor" in lowered
        assert "dstock_central_session_descriptor" in lowered
        assert "dstock_central_single_stock_dataset_ids" in lowered
        assert "len(tuple(c.get('route_ids') or ())) == 16" in lowered
        assert "dstock_central_receipt_path" in lowered
        assert "dstock_central_receipt_sha256" in lowered
        assert "dstock_central_orchestration_run_id" in lowered
        assert "dstock_central_manifest_content_sha256" in lowered
        assert "dstock_central_registry_sha256" in lowered
        assert "dstock_central_single_stock_publication_run_ids_json" in lowered
        assert "dstock_central_single_stock_dataset_hashes_json" in lowered
        assert "dstock_shared_raw_network_disabled=1" in lowered
        assert "enable_finmind_fetch=0" in lowered
        assert "call :verifypinnedsession" in lowered
        assert "market_update_client.py" not in lowered
        assert "market_update.cmd" not in lowered
        assert "market_update.py" not in lowered
        assert "ensure_market_update" not in lowered
        assert "latest_manifest.json" not in lowered
        assert "api.finmindtrade.com" not in lowered

    for source in (three_month_entry, tomorrow_entry):
        assert "dstock_allow_legacy_finmind_cache=0" in source.lower()

    assert 'if not "!RC!"=="0" goto End' in root_entry
    assert "global_market_data.py\" refresh" not in three_month_entry
    assert "global_market_data.py\" refresh" not in tomorrow_entry


def test_single_cache_update_entries_use_central_gate_and_compat_adapters():
    history_entry = (
        SINGLE_ROOT / "tools"
        / "UPDATE_STOCK_HISTORY_CACHE.cmd"
    ).read_text(encoding="utf-8")
    research_entry = (
        SINGLE_ROOT / "tools"
        / "UPDATE_SINGLE_STOCK_RESEARCH_CACHE.cmd"
    ).read_text(encoding="utf-8")
    for entry in (history_entry, research_entry):
        lowered = entry.lower()
        assert "this provider-refresh wrapper is retired and blocked." in lowered
        assert "no provider, network, or cache updater is invoked" in lowered
        assert "market_update_client.py" not in lowered
        assert "market_update.cmd" not in lowered
        assert "api.finmindtrade.com" not in lowered
        assert "exit /b 2" in lowered


def test_single_formal_source_refresh_respects_central_network_gate():
    analyzer = (
        SINGLE_SRC
        / "tw_single_stock_3m_analyzer.py"
    ).read_text(encoding="utf-8")

    weekend = (SINGLE_SRC / "tw_single_stock_weekend.py").read_text(encoding="utf-8")

    projection_index = analyzer.index(
        "central_projection, central_lineage = require_central_single_stock_projection("
    )
    client_index = analyzer.index("client = FinMindClient(")
    assert projection_index < client_index
    client_call = analyzer[client_index:analyzer.index("\n    )\n", client_index)]
    assert "central_projection=central_projection," in client_call
    assert "refresh_cache=False" in client_call
    assert "central_mode = True" in analyzer
    assert "collect_candidate_features=False" in analyzer
    assert "refresh_cache=False" in analyzer
    assert "provider_tail_disabled" in analyzer
    assert "cache_fallback_disabled" in analyzer
    assert "and not shared_raw_network_disabled()" not in analyzer

    # The provider client can only be built on a verified projection, with
    # neither the legacy cache nor the network opt-in active.
    for reason in (
        "legacy_finmind_opt_in_rejected",
        "network_provider_opt_in_rejected",
        "central_projection_required_before_client",
    ):
        assert f'raise CentralPublicationError("{reason}")' in analyzer

    # The formal analyzer no longer passes ``central_only=True`` at its call
    # site: it rejects any fallback model result itself, and the remaining
    # training entry point still disables the fallback.
    assert 'if model_mode == "fallback" or fallback_reason:' in analyzer
    assert analyzer.count('raise _central_projection_error("formal_model_fallback_disabled")') >= 3
    assert "central_only=True" in weekend


def test_pinned_session_wrappers_bind_after_export_without_dotenv_contamination():
    root_entry = (
        SINGLE_ROOT
        / "1_執行個股分析與產生報告.cmd"
    ).read_text(encoding="utf-8")
    three_month_entry = (
        SINGLE_ROOT
        / "RUN_SINGLE_STOCK_3M_ANALYSIS.cmd"
    ).read_text(encoding="utf-8")
    tomorrow_entry = (
        SINGLE_ROOT
        / "RUN_SINGLE_STOCK_TOMORROW_OPEN.cmd"
    ).read_text(encoding="utf-8")

    root_lines = root_entry.splitlines()
    lowered_lines = [line.lower() for line in root_lines]
    create_label_index = next(
        index
        for index, line in enumerate(lowered_lines)
        if line.strip() == ":createpinnedsession"
    )
    main_lines = lowered_lines[:create_label_index]

    def _exact_call_index(lines, target):
        marker = f"call :{target.lower()}"
        return next(
            index
            for index, line in enumerate(lines)
            if line.strip() == marker
        )

    create_index = _exact_call_index(main_lines, "CreatePinnedSession")
    formal_index = _exact_call_index(main_lines, "ValidateFormalBinding")
    verify_index = _exact_call_index(main_lines, "VerifyPinnedSession")
    analysis_index = next(
        index
        for index, line in enumerate(main_lines)
        if "run_single_stock_3m_analysis.cmd" in line
    )
    assert create_index < formal_index < verify_index < analysis_index

    create_block_lines = _cmd_label_block(
        root_entry, "CreatePinnedSession"
    ).splitlines()
    success_indices = [
        index
        for index, line in enumerate(create_block_lines)
        if line.strip().lower().startswith("exit /b 0")
    ]
    assert success_indices
    success_index = success_indices[-1]
    for key in _ROUTE_ENV_KEYS:
        commit_marker = f'set "{key.lower()}='
        assert any(
            line.strip().lower().startswith(commit_marker)
            for line in create_block_lines[:success_index]
        ), key
    formal_block = _cmd_label_block(root_entry, "ValidateFormalBinding").lower()
    expected_route_ids = (
        "finmind_stock_info",
        "official_finance_reporting_deadline",
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
        "official_calendar",
    )
    route_positions = [
        formal_block.index(f"'{route_id}'") for route_id in expected_route_ids
    ]
    assert len(route_positions) == 16
    assert route_positions == sorted(route_positions)
    assert "$routes.count -ne 16" in formal_block
    assert (
        "[string]::join(',', $routes) -cne [string]::join(',', $expected)"
        in formal_block
    )

    verify_block = _cmd_label_block(root_entry, "VerifyPinnedSession").lower()
    for key in (
        "dstock_central_session_descriptor",
        "dstock_central_session_descriptor_sha256",
        "dstock_central_receipt_path",
        "dstock_central_receipt_sha256",
        "dstock_central_receipt_run_id",
        "dstock_central_orchestration_run_id",
        "dstock_central_artifact_map_sha256",
        "dstock_central_session_run_id",
        "dstock_central_single_stock_dataset_ids",
        "dstock_central_single_stock_publication_run_ids_json",
        "dstock_central_single_stock_dataset_hashes_json",
        "dstock_central_single_stock_content_sha256s_json",
        "dstock_central_single_stock_artifact_list_sha256s_json",
        "dstock_central_manifest_sha256",
        "dstock_central_manifest_content_sha256",
        "dstock_central_registry_sha256",
        "dstock_central_registry_canonical_sha256",
        "dstock_central_registry_raw_sha256",
    ):
        assert key in verify_block

    pre_export_calls = [
        line.strip()
        for line in lowered_lines[:create_index]
        if line.strip().startswith("call :") and "validate" in line
    ]
    static_calls = [
        line
        for line in pre_export_calls
        if line.strip() != "call :validateformalbinding"
    ]
    assert static_calls
    static_label = static_calls[0].split(":", 1)[1].split()[0]
    static_block = _cmd_label_block(root_entry, static_label).lower()
    for key in _ROUTE_ENV_KEYS:
        assert key.lower() not in static_block

    root_loader = _dotenv_loader_block(root_entry).lower()
    for key in _PINNED_SESSION_DOTENV_KEYS:
        assert f'not "%%a"=="{key.lower()}"' in root_loader

    for source in (three_month_entry, tomorrow_entry):
        loader = _dotenv_loader_block(source).lower()
        for key in (
            "DStock_CENTRAL_RECEIPT_SEAL_JSON",
            "DStock_CENTRAL_SESSION_EXPECTED_ASOF",
            "DStock_MARKET_UPDATE_EXPECTED_ASOF",
        ):
            assert f'not "%%a"=="{key.lower()}"' in loader
