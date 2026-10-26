from __future__ import annotations

import ast
import hashlib
import copy
import json
import re
import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path, PureWindowsPath
from unittest import mock
from xml.sax.saxutils import escape


SRC = Path(__file__).resolve().parents[1] / "src"
import sys

if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

import verify_stock_programs as verifier


C0_ROUTES = (
    "official_calendar",
    "official_market_daily",
    "finmind_stock_info",
    "finmind_price_adj",
    "finmind_dividend",
    "finmind_dividend_result",
)


def _write_json(path: Path, value: object) -> bytes:
    raw = json.dumps(value, ensure_ascii=False, indent=2).encode("utf-8")
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(raw)
    return raw


def _registry(route_ids: tuple[str, ...] = C0_ROUTES, **overrides: object) -> dict[str, object]:
    value: dict[str, object] = {
        "schema": "shared-market-data-requirements-v3",
        "schema_version": 3,
        "requirements_version": "2026-10-09.current-only.1",
        "purpose": "current_C0_six_routes_only",
        "interface": {"active_profile": "C0", "route_ids": list(route_ids)},
        "publication_contract": {
            "schema": "stock-data-publication-v1",
            "current_head_relative_path": "_metadata/stock_data_current.json",
            "archive_relative_root": "_manifests",
            "objects_relative_root": "_objects",
            "publication_scope": {"profile_id": "C0", "required_route_ids": list(route_ids)},
        },
        "shared_raw_requirements": [{"id": route_id, "required": True, "enabled": True} for route_id in route_ids],
        "route_contracts": {route_id: {"required": True} for route_id in route_ids},
        "formal_consumer_mappings": [{"consumer_id": "mix", "required_routes": list(route_ids)}],
    }
    value.update(overrides)
    return value


def build_central_fixture(
    fixture_root: Path,
    *,
    route_ids: tuple[str, ...] = C0_ROUTES,
    head_routes: tuple[str, ...] | None = None,
    statuses: dict[str, str] | None = None,
    published_at: str | None = None,
    registry: dict[str, object] | None = None,
) -> dict[str, Path]:
    central = fixture_root / "central"
    publication = central / "shared_market_data"
    _write_json(central / "requirements_v1.json", registry or _registry(route_ids))
    datasets = []
    objects: dict[str, Path] = {}
    for route_id in head_routes or route_ids:
        body = json.dumps({"route_id": route_id, "rows": [1, 2, 3]}).encode("utf-8")
        sha = hashlib.sha256(body).hexdigest()
        path = publication / "_objects" / f"{sha}.json"
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(body)
        objects[route_id] = path
        datasets.append(
            {
                "route_id": route_id,
                "path": f"_objects/{sha}.json",
                "sha256": sha,
                "size": len(body),
                "status": (statuses or {}).get(route_id, "SUCCESS"),
                "actual_min_date": "2025-09-22",
                "actual_max_date": "2026-10-08",
                "record_count": 3,
            }
        )
    head = {
        "schema": "stock-data-publication-v1",
        "producer_id": "twstock_updater",
        "run_id": "20261011T042103Z-5f5058f8",
        "published_at": published_at or datetime.now(timezone.utc).isoformat(),
        "previous_sha": "0" * 64,
        "datasets": datasets,
        "metadata": {
            "verified_market_calendar": {"verified_asof": "2026-10-10", "coverage_end": "2026-10-10", "future_targets_verified": False},
            "application_capture_scope": {"requirements_version": "2026-10-09.current-only.1"},
        },
    }
    head_path = publication / "_metadata" / "stock_data_current.json"
    raw = _write_json(head_path, head)
    archive = publication / "_manifests" / f"{hashlib.sha256(raw).hexdigest()}.json"
    archive.parent.mkdir(parents=True, exist_ok=True)
    archive.write_bytes(raw)
    return {"head": head_path, "archive": archive, "registry": central / "requirements_v1.json", **{f"object_{key}": value for key, value in objects.items()}}


def _definition(**action_overrides: object) -> dict[str, object]:
    action = {
        "executable": "C:\\Windows\\System32\\cmd.exe",
        "arguments": verifier.MIX_TASK_EXPECTED_ARGUMENTS,
        "working_directory": "D:\\stock\\programs\\telegram_report_delivery",
        "no_pause": True,
        "max_wait_seconds": 0,
        "poll_seconds": 60,
    }
    action.update(action_overrides)
    return {
        "schema_version": "mix_telegram_weekday_schedule_definition_v1",
        "task_name": "MIX Telegram Report 08-00",
        "timezone": "Taipei Standard Time",
        "trigger": {"days_of_week": ["Monday", "Tuesday", "Wednesday", "Thursday", "Friday"], "start_time": "08:00", "start_when_available": True},
        "action": action,
        "settings": {
            "multiple_instances": "IgnoreNew",
            "execution_time_limit_seconds": 0,
            "allow_start_if_on_batteries": True,
            "dont_stop_if_going_on_batteries": True,
            "dont_stop_on_idle_end": True,
        },
        "publication_contract": {
            "source": "D:\\TWStockData\\program_data\\mix\\reports\\LATEST_C0_RUN.json",
            "required_pointer_schema": "mix-stock-daily-c0-current-report-v1",
            "required_status": "limited_baseline_effect_unverified",
            "required_official": True,
            "required_publishable": False,
            "publishable_is_quality_indicator": True,
            "required_execution_success": True,
            "required_artifact_integrity": True,
            "requires_central_receipt_and_registry": True,
            "stale_report_action": "send_with_report_and_latest_data_days_labeled",
            "integrity_failure_action": "exit_nonzero_fail_closed_no_send",
            "no_action_action": "reject_no_action_report",
            "quality_conditions_action": "include_unmet_conditions_without_blocking",
            "selection_policy": "formal_only",
            "formal_unavailable_action": "exit_nonzero_no_fallback",
            "formal_send_failure_action": "exit_nonzero_no_fallback",
        },
    }


INSTALL_MIX_TASK_PS1 = verifier.MIX_TASK_SENDER_ROOT / "INSTALL_MIX_WEEKDAY_0800_TASK.ps1"


def _ps_single_quoted(text: str) -> str:
    return text[1:-1].replace("''", "'")


def _installer_assert_definition_rules() -> set[tuple[str, str, str, object]]:
    """Every field check in INSTALL_MIX_WEEKDAY_0800_TASK.ps1 Assert-Definition, as verifier rule tuples."""
    source = INSTALL_MIX_TASK_PS1.read_text(encoding="utf-8")
    start = source.index("function Assert-Definition")
    body = source[start : source.index("\nfunction ", start + 1)]
    # Script-level and in-function literal assignments the comparisons refer to.
    variables: dict[str, object] = {}
    for match in re.finditer(r"^\s*\$(\w+)\s*=\s*('(?:[^']|'')*')\s*$", source, re.M):
        variables[match.group(1)] = _ps_single_quoted(match.group(2))
    days = re.search(r"\$expectedDays\s*=\s*@\(([^)]*)\)", body)
    assert days is not None
    variables["expectedDays"] = [_ps_single_quoted(item.strip()) for item in days.group(1).split(",")]
    variables["suite"] = str(verifier.MIX_TASK_SENDER_ROOT)
    # Resolve the local aliases used inside the function.
    body = body.replace("$settings.", "$Definition.settings.").replace("$publication.", "$Definition.publication_contract.")
    body = re.sub(r"\$arguments\b(?!\s*=)", "[string]$Definition.action.arguments", body)
    rules: set[tuple[str, str, str, object]] = set()
    comparison = re.compile(
        r"(\[int\])?(?:\[string\])?\(?@?\(?\$Definition\.(?:(\w+)\.)?(\w+)\)?(?:\s*-join\s*','\))?\s+"
        r"(-cne|-ne|-notmatch|-match)\s+"
        r"('(?:[^']|'')*'|\(\$expectedDays -join ','\)|\$true|\$false|\$\w+|\d+)"
    )
    for match in comparison.finditer(body):
        as_int, section, field, operator, operand = match.groups()
        section = section or ""
        if operand == "$true":
            rule, expected = "is_true", True
        elif operand == "$false":
            rule, expected = "is_false", False
        elif operator == "-notmatch":
            rule, expected = "match", _ps_single_quoted(operand)
        elif operator == "-match":
            rule, expected = "not_match", _ps_single_quoted(operand)
        elif as_int:
            rule, expected = "int", int(operand)
        elif operand.startswith("(") :
            rule, expected = "eq", tuple(variables["expectedDays"])  # type: ignore[arg-type]
        elif operand.startswith("$"):
            name = operand[1:]
            rule, expected = ("path_ci" if operator == "-ne" else "eq"), variables[name]
        else:
            assert operator == "-cne", match.group(0)
            rule, expected = "eq", _ps_single_quoted(operand)
        rules.add((section, field, rule, expected))
    for match in re.finditer(r"@\(\$Definition\.(\w+)\.PSObject\.Properties\.Name\)\s+-contains\s+'(\w+)'", body):
        rules.add((match.group(1), match.group(2), "absent", None))
    # Completeness: every comparison operator in the function was turned into exactly one rule.
    operators = re.findall(r"\s-(?:cne|ne|notmatch|match|contains)\s", body)
    assert len(operators) == len(rules), (len(operators), len(rules))
    return rules


def _verifier_rules() -> set[tuple[str, str, str, object]]:
    return {(s, f, r, tuple(e) if isinstance(e, list) else e) for s, f, r, e in verifier.MIX_TASK_DEFINITION_RULES}


def _task_xml(*, arguments: str | None = None, start: str = "2026-10-12T08:00:00", limit: str = "PT0S", enabled: str = "true") -> str:
    args = escape(arguments if arguments is not None else verifier.MIX_TASK_EXPECTED_ARGUMENTS)
    return (
        '<?xml version="1.0" encoding="UTF-16"?>\n'
        '<Task version="1.2" xmlns="http://schemas.microsoft.com/windows/2004/02/mit/task">'
        f"<Triggers><CalendarTrigger><StartBoundary>{start}</StartBoundary><Enabled>true</Enabled>"
        "<ScheduleByWeek><DaysOfWeek><Monday /><Tuesday /><Wednesday /><Thursday /><Friday /></DaysOfWeek>"
        "<WeeksInterval>1</WeeksInterval></ScheduleByWeek></CalendarTrigger></Triggers>"
        "<Settings><MultipleInstancesPolicy>IgnoreNew</MultipleInstancesPolicy>"
        "<DisallowStartIfOnBatteries>false</DisallowStartIfOnBatteries><StopIfGoingOnBatteries>false</StopIfGoingOnBatteries>"
        "<StartWhenAvailable>true</StartWhenAvailable><IdleSettings><StopOnIdleEnd>false</StopOnIdleEnd></IdleSettings>"
        f"<Enabled>{enabled}</Enabled><ExecutionTimeLimit>{limit}</ExecutionTimeLimit></Settings>"
        '<Actions Context="Author"><Exec><Command>C:\\Windows\\System32\\cmd.exe</Command>'
        f"<Arguments>{args}</Arguments><WorkingDirectory>D:\\stock\\programs\\telegram_report_delivery</WorkingDirectory></Exec></Actions>"
        "</Task>"
    )


def build_scheduler_fixture(fixture_root: Path, *, tasks: list[dict[str, object]] | None = None, definition: dict[str, object] | None = None) -> None:
    _write_json(fixture_root / "scheduler" / "MIX_WEEKDAY_0800_TASK_DEFINITION.json", definition or _definition())
    if tasks is not None:
        _write_json(fixture_root / "scheduler" / "scheduled_tasks.json", {"tasks": tasks})


def build_telegram_fixture(fixture_root: Path, *, heartbeat_status: str = "polling", owner_sha: str | None = None) -> str:
    source = fixture_root / "telegram" / "telegram_stock_command_bot.py"
    source.parent.mkdir(parents=True, exist_ok=True)
    source.write_bytes(b"# fixture bot\n")
    sha = hashlib.sha256(source.read_bytes()).hexdigest()
    _write_json(fixture_root / "telegram" / "owner.json", {"processId": 3616, "sourceSha256": owner_sha or sha})
    _write_json(
        fixture_root / "telegram" / "heartbeat.json",
        {"pid": 3616, "status": heartbeat_status, "sourceSha256": sha, "serviceHeartbeatStaleSeconds": 180, "updatedAt": datetime.now(timezone.utc).isoformat()},
    )
    return sha


def _single_doc(stock_id: str, horizon: int, *, ordinary: bool = True, verification: dict[str, object] | None = None) -> dict[str, object]:
    record = {
        "stock_id": stock_id,
        "board": "TWSE",
        "is_ordinary_share": ordinary,
        "security_type": "ordinary_share" if ordinary else "etf",
        "current_ordinary_classification_verified": True,
    }
    doc: dict[str, object] = {
        "report_schema_version": "single-stock-daily-c0-estimate-v1",
        "report_class": "daily_estimate_effect_unknown",
        "profile": "C0",
        "source_mode": "published",
        "target_definition": "signal_close_to_same_market_H_session_close",
        "qa_override": False,
        "historical_pit_verified": False,
        "historical_membership_verified": False,
        "corporate_actions_verified": False,
        "effectiveness_status": "unknown",
        "trading_effect": "unverified",
        "stock": dict(record),
        "latest_data_date": "2026-10-08",
        "analysis_asof": "2026-10-08",
        "horizon_sessions": horizon,
        "prediction": {"stock_id": stock_id, "horizon": horizon, "prob_up": 0.43},
        "prob_up": 0.43,
        "expected_return": 0.0,
        "coverage": {"requested": 1, "eligible": 1, "excluded": [], "records": [record]},
        "source_statuses": {"official_market_daily": "PARTIAL"},
        "data_time": {"freshness_status": "latest", "analysis_asof": "2026-10-08"},
    }
    if verification is not None:
        doc["verification"] = verification
    return doc


def write_single_report(reports_root: Path, doc: dict[str, object], *, stamp: str = "20261009T041003508086") -> dict[str, Path]:
    stock_id = str(doc["stock"]["stock_id"])  # type: ignore[index]
    stem = f"c0_{stock_id}_h{doc['horizon_sessions']}_{stamp}"
    reports_root.mkdir(parents=True, exist_ok=True)
    pdf = reports_root / f"{stem}.pdf"
    pdf.write_bytes(b"%PDF-1.4 fixture " + stem.encode("ascii"))
    json_path = reports_root / f"{stem}.json"
    body = dict(doc)
    body.update({"output_pdf": str(pdf), "output_json": str(json_path), "pdf_sha256": hashlib.sha256(pdf.read_bytes()).hexdigest(), "pdf_size": len(pdf.read_bytes())})
    body.pop("estimate_binding_sha256", None)
    canonical = json.dumps(body, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False).encode("utf-8")
    body["estimate_binding_sha256"] = hashlib.sha256(canonical).hexdigest()
    _write_json(json_path, body)
    return {"json": json_path, "pdf": pdf}


GOVERNANCE_ENTRIES: tuple[tuple[str, str, str], ...] = (
    ("full_market_analysis", "RUN_QUICK_UPDATE.cmd", "retired_wrapper"),
    ("full_market_analysis", "src\\stock_master.py", "retired_analysis"),
    ("cowork", "tools\\run_oneclick.py", "retired_analysis"),
    ("cowork", "src\\cowork\\pipeline.py", "retired_analysis"),
    ("premarket_daytrade_top10", "src\\premarket_top10\\cli.py", "retired_analysis"),
    ("twmix", "src\\twmix\\pipeline.py", "retired_analysis"),
    ("old_scheduler", "RUN_SCHEDULED_ANALYSIS.ps1", "retired_wrapper"),
)


def governance_config(fixture_root: Path) -> dict[str, object]:
    root = fixture_root.resolve()
    return verifier._retained_governance_config(verifier._retained_paths(root), root)


def allowlist_entries(fixture_root: Path, entries: tuple[tuple[str, str, str], ...] = GOVERNANCE_ENTRIES) -> list[dict[str, object]]:
    retired = governance_config(fixture_root)["retired_roots"]
    return [
        {
            "entry_id": f"{root_id}-{index:03d}",
            "source_root_id": root_id,
            "source_path": str(retired[root_id].joinpath(*PureWindowsPath(relative).parts)),  # type: ignore[index]
            "relative_path": relative,
            "category": category,
        }
        for index, (root_id, relative, category) in enumerate(entries, 1)
    ]


def write_allowlist(fixture_root: Path, entries: list[dict[str, object]], **overrides: object) -> Path:
    document: dict[str, object] = {
        "schema": "retained-program-quarantine-allowlist-v1",
        "destination_root": "fixture-not-used",
        "authorization_scope": "fixture",
        "entries": entries,
    }
    document.update(overrides)
    path = fixture_root.resolve() / "governance" / "program_quarantine_allowlist_v1.json"
    _write_json(path, document)
    return path


def build_governance_fixture(
    fixture_root: Path,
    *,
    union: dict[str, int] | None = None,
    entries: tuple[tuple[str, str, str], ...] = GOVERNANCE_ENTRIES,
    legacy: bool = False,
) -> dict[str, object]:
    """Observational governance fixture: allowlist, absent retired files, clean current trees, program data."""
    root = fixture_root.resolve()
    config = governance_config(root)
    write_allowlist(root, allowlist_entries(root, entries))
    for scope in config["scan_scopes"]:  # type: ignore[union-attr]
        path = Path(scope["path"])
        path.mkdir(parents=True, exist_ok=True)
        (path / "entry.py").write_text("import json\nprint(json.dumps({'current': True}))\n", encoding="utf-8")
    # The shared retired root (D:\cowork) keeps non-retired directories.
    (config["retired_roots"]["cowork"] / "logs").mkdir(parents=True, exist_ok=True)  # type: ignore[index]
    (root / "program_data" / "mix").mkdir(parents=True, exist_ok=True)
    if legacy:
        quarantine = root / "quarantine" / "manifest.json"
        raw = _write_json(quarantine, {"schema": "retained-program-quarantine-manifest-v1"})
        record = {"manifest_path": str(quarantine), "sha256": hashlib.sha256(raw).hexdigest(), "size": len(raw), "zero_touch": True}
        central_union = union or {"total": 6, "required": 6, "optional": 0}
        _write_json(
            root / "retention" / "data_retention_guard.json",
            {"schema": "cross-system-data-retention-guard-v1", "status": "verified", "data_untouched": True, "deleted_count": 0, "moved_count": 0, "quarantine_manifest": record, "central_union": central_union},
        )
        _write_json(
            root / "retirement" / "retirement_isolation.json",
            {"schema": "cross-system-retirement-isolation-v1", "status": "verified", "data_untouched": True, "telegram_protected": True, "central_union": central_union, "isolated_consumers": ["cowork", "twmix"]},
        )
    return config


class RetainedArchitectureContractTests(unittest.TestCase):
    def test_schema_and_active_components_are_app_neutral(self) -> None:
        self.assertEqual(verifier.SCHEMA, "cross_system_acceptance_v2")
        self.assertEqual(
            verifier.RETAINED_COMPONENT_IDS,
            (
                "central_publication",
                "workflow_scheduler",
                "mix_analysis_report",
                "mix_mitake_persistence",
                "single_stock_analysis_report",
                "telegram_service_publication",
                "data_retention_guard",
                "retirement_isolation",
                "schedule_reconciliation",
            ),
        )
        self.assertTrue(set(verifier.RETIRED_CONSUMER_IDS).isdisjoint(verifier.RETAINED_COMPONENT_IDS))

    def test_central_contract_is_current_c0_v3(self) -> None:
        self.assertEqual(verifier.CENTRAL_REQUIREMENTS_REGISTRY_SCHEMA, "shared-market-data-requirements-v3")
        self.assertEqual(set(verifier.CENTRAL_C0_ROUTE_IDS), set(C0_ROUTES))
        paths = verifier._retained_paths()
        self.assertTrue(paths["central_registry"].as_posix().lower().endswith("contracts/shared_market_data/requirements_v1.json"))
        self.assertEqual(paths["central_publication_root"], Path(r"D:\TWStockData\shared_market_data"))
        self.assertNotIn("central_receipt", paths)
        self.assertNotIn("scheduler_final", paths)
        self.assertNotIn("telegram_message_receipt", paths)
        self.assertNotIn("single_manifest", paths)

    def test_retired_live_path_is_removed_and_alias_is_read_only(self) -> None:
        for name in (
            "_central_mix_live_payload",
            "_central_mix_live_publication_evidence",
            "_central_mix_live_mitake_evidence",
            "_central_mix_live_paths",
            "_reports_check",
            "_program_result",
            "ProgramSpec",
        ):
            self.assertFalse(hasattr(verifier, name), name)
        with tempfile.TemporaryDirectory() as temp:
            args = verifier._parse_args(["--central-mix-live", "--fixture-root", temp])
            self.assertFalse(args.reports)
            payload = verifier._build_payload(args)
            self.assertEqual(payload["mode"], verifier.RETAINED_MODE)
            self.assertEqual(verifier.main(["--central-mix-live", "--fixture-root", temp]), 1)
            self.assertFalse((Path(temp) / "acceptance.latest.json").exists())

    def test_fixture_paths_are_isolated_and_do_not_rewrite_production_latest(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            args = verifier._parse_args(["--structure-only", "--fixture-root", str(root)])
            payload = verifier._build_payload(args)
            self.assertEqual(payload["schema"], "cross_system_acceptance_v2")
            self.assertEqual(payload["mode"], verifier.RETAINED_MODE)
            self.assertEqual(payload["overall_status"], "blocked")
            self.assertFalse((root / "acceptance.latest.json").exists())
            self.assertEqual(payload["read_only_scope"]["real_systems_are_never_written"], True)
            self.assertFalse(any(item["pass"] is True for name, item in payload["components"].items() if name != "schedule_reconciliation"))
            self.assertEqual(payload["components"]["mix_mitake_persistence"]["status"], "not_applicable")
            self.assertEqual(payload["components"]["single_stock_analysis_report"]["status"], "warning")

    def test_old_consumer_id_cannot_be_selected_as_retained_component(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            args = verifier._parse_args(["--structure-only", "--fixture-root", str(root), "--only", "full_market_analysis"])
            with self.assertRaises(ValueError):
                verifier._build_payload(args)

    def test_component_statuses_remain_separate(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            args = verifier._parse_args(["--structure-only", "--fixture-root", temp])
            payload = verifier._build_payload(args)
            self.assertEqual(set(payload["components"]), set(verifier.RETAINED_COMPONENT_IDS))
            self.assertEqual(set(payload["checks"]), set(verifier.RETAINED_COMPONENT_IDS))
            self.assertEqual(
                {item["component_id"] for item in payload["blocking_failures"]},
                {
                    "central_publication",
                    "workflow_scheduler",
                    "mix_analysis_report",
                    "telegram_service_publication",
                    "data_retention_guard",
                    "retirement_isolation",
                },
            )
            self.assertEqual(payload["overall_status"], "blocked")

    def test_artifact_reader_rejects_glob_and_traversal(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            for value in (str(root / "*.json"), str(root / ".." / "outside.json"), "relative.json"):
                record, check = verifier._retained_artifact_record(value, root, "fixture_artifact")
                self.assertIsNone(record)
                self.assertFalse(check["pass"])
                self.assertEqual(check["reason"], "fixture_artifact_path_invalid")
            for value in ("../x.json", "/abs.json", "C:/x.json", "_objects/*.json", "_objects/./a.json"):
                self.assertIsNone(verifier._retained_safe_relative(value), value)

    def test_full_fixture_passes_with_non_blocking_labels(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            build_central_fixture(root)
            build_scheduler_fixture(root)
            build_c0_fixture(root / "mix" / "reports")
            write_single_report(root / "single" / "reports" / "c0", _single_doc("2330", 1, verification={"status": "verified"}))
            build_telegram_fixture(root)
            write_delivery_state(root / "mix" / "reports", root / "telegram" / "mix_report_delivery_state.json")
            build_governance_fixture(root)
            payload = verifier._build_payload(verifier._parse_args(["--structure-only", "--fixture-root", str(root)]))
            statuses = {name: item["status"] for name, item in payload["components"].items()}
            self.assertEqual(payload["overall_status"], "pass", payload["blocking_failures"])
            self.assertEqual(statuses["workflow_scheduler"], "warning")
            self.assertEqual(statuses["mix_mitake_persistence"], "not_applicable")
            for name in ("central_publication", "mix_analysis_report", "single_stock_analysis_report", "telegram_service_publication", "data_retention_guard", "retirement_isolation", "schedule_reconciliation"):
                self.assertEqual(statuses[name], "pass", (name, payload["components"][name].get("reason")))
            codes = {item["code"] for item in payload["warnings"]}
            self.assertIn("schedule_not_registered", codes)
            self.assertIn("mitake_not_applicable_c0", codes)
            self.assertEqual({item["component_id"] for item in payload["non_blocking_unverified"]}, {"workflow_scheduler", "mix_mitake_persistence"})
            self.assertEqual(verifier.main(["--structure-only", "--fixture-root", str(root)]), 0)
            self.assertFalse((root / "acceptance.latest.json").exists())


class CentralPublicationTests(unittest.TestCase):
    def setUp(self) -> None:
        self._temp = tempfile.TemporaryDirectory()
        self.root = Path(self._temp.name)
        self.paths = verifier._retained_paths(self.root)

    def tearDown(self) -> None:
        self._temp.cleanup()

    def component(self) -> dict[str, object]:
        return verifier._retained_central_component(self.paths, datetime.now(timezone.utc))

    def test_current_head_passes_and_shows_data_time(self) -> None:
        build_central_fixture(self.root)
        component = self.component()
        self.assertTrue(component["pass"], component.get("reason"))
        self.assertEqual(component["route_union"]["total"], 6)
        self.assertEqual(component["market_asof"], "2026-10-08")
        self.assertEqual(component["data_time"]["calendar_verified_asof"], "2026-10-10")
        self.assertEqual(component["warnings"], [])

    def test_retired_receipt_is_never_a_fallback(self) -> None:
        _write_json(self.root / "central" / "latest_manifest.json", {"manifest_schema": "shared-market-data-update-manifest-v1", "status": "complete"})
        component = self.component()
        self.assertFalse(component["pass"])
        self.assertEqual(component["reason"], "central_registry_missing")

    def test_v1_registry_schema_is_blocked(self) -> None:
        build_central_fixture(self.root, registry=_registry(schema="shared-market-data-requirements-v1", schema_version=1))
        self.assertEqual(self.component()["reason"], "central_registry_schema_invalid")

    def test_registry_scope_inconsistency_is_blocked(self) -> None:
        registry = _registry()
        registry["shared_raw_requirements"] = registry["shared_raw_requirements"][:-1]  # type: ignore[index]
        build_central_fixture(self.root, registry=registry)
        self.assertEqual(self.component()["reason"], "central_registry_route_set_inconsistent")

    def test_different_route_set_is_a_label(self) -> None:
        routes = C0_ROUTES[:5]
        build_central_fixture(self.root, route_ids=routes)
        component = self.component()
        self.assertTrue(component["pass"], component.get("reason"))
        self.assertIn("central_registry_route_set_differs_from_known_c0", {item["code"] for item in component["warnings"]})

    def test_archive_mismatch_is_blocked(self) -> None:
        paths = build_central_fixture(self.root)
        paths["archive"].write_bytes(paths["archive"].read_bytes() + b" ")
        self.assertEqual(self.component()["reason"], "central_head_archive_mismatch")
        paths["archive"].unlink()
        self.assertEqual(self.component()["reason"], "central_head_archive_missing")

    def test_tampered_object_is_blocked(self) -> None:
        paths = build_central_fixture(self.root)
        target = paths["object_finmind_price_adj"]
        target.write_bytes(target.read_bytes().replace(b"1", b"9", 1))
        self.assertEqual(self.component()["reason"], "central_head_object_hash_mismatch")
        target.unlink()
        self.assertEqual(self.component()["reason"], "central_object_finmind_price_adj_missing")

    def test_missing_required_route_is_blocked(self) -> None:
        build_central_fixture(self.root, head_routes=C0_ROUTES[:5])
        component = self.component()
        self.assertEqual(component["reason"], "central_head_required_route_missing")
        self.assertEqual(component["checks"]["published_head"]["missing_routes"], ["finmind_dividend_result"])

    def test_partial_route_and_old_head_are_labels(self) -> None:
        old = (datetime.now(timezone.utc) - timedelta(days=5)).isoformat()
        build_central_fixture(self.root, statuses={"official_market_daily": "PARTIAL"}, published_at=old)
        component = self.component()
        self.assertTrue(component["pass"], component.get("reason"))
        codes = {item["code"] for item in component["warnings"]}
        self.assertIn("central_route_partial", codes)
        self.assertIn("central_head_older_than_window", codes)

    def test_failed_route_status_is_blocked(self) -> None:
        build_central_fixture(self.root, statuses={"official_calendar": "FAILED"})
        self.assertEqual(self.component()["reason"], "central_head_route_status_invalid")

    def test_duplicate_key_head_is_blocked(self) -> None:
        paths = build_central_fixture(self.root)
        raw = paths["head"].read_bytes().replace(b'"schema": "stock-data-publication-v1",', b'"schema": "stock-data-publication-v1", "schema": "x",', 1)
        paths["head"].write_bytes(raw)
        archive = paths["archive"].parent / f"{hashlib.sha256(raw).hexdigest()}.json"
        archive.write_bytes(raw)
        self.assertEqual(self.component()["reason"], "central_head_invalid_json")


class SchedulerTests(unittest.TestCase):
    def setUp(self) -> None:
        self._temp = tempfile.TemporaryDirectory()
        self.root = Path(self._temp.name)
        self.paths = verifier._retained_paths(self.root)

    def tearDown(self) -> None:
        self._temp.cleanup()

    def query(self) -> dict[str, object]:
        return verifier._fixture_task_query(self.paths["scheduled_tasks"])

    def test_production_definition_matches_the_verifier_contract(self) -> None:
        definition, check = verifier._mix_task_definition_evidence(verifier.MIX_TASK_DEFINITION_PATH)
        self.assertIsNotNone(definition, check)
        self.assertTrue(check["pass"])
        self.assertEqual(check["rules_checked"], len(verifier.MIX_TASK_DEFINITION_RULES))

    def test_verifier_rules_equal_installer_assert_definition(self) -> None:
        """Same field set and values as INSTALL_MIX_WEEKDAY_0800_TASK.ps1 Assert-Definition (drift guard)."""
        extracted = _installer_assert_definition_rules()
        # Sanity: the extraction really saw the whole function (every section, both refused legacy fields).
        self.assertGreaterEqual(len(extracted), 30)
        self.assertEqual({item[0] for item in extracted}, {"", "trigger", "action", "settings", "publication_contract"})
        self.assertIn(("publication_contract", "requires_feedback_publication_receipt", "absent", None), extracted)
        self.assertIn(("publication_contract", "stale_or_integrity_failure_action", "absent", None), extracted)
        self.assertEqual(extracted, _verifier_rules())

    def test_fixture_definition_passes_every_rule(self) -> None:
        build_scheduler_fixture(self.root)
        definition, check = verifier._mix_task_definition_evidence(self.paths["mix_task_definition"])
        self.assertIsNotNone(definition, check)

    def test_each_assert_definition_field_violation_is_blocked(self) -> None:
        for section, field, rule, expected in verifier.MIX_TASK_DEFINITION_RULES:
            value = _definition()
            target = value if not section else value[section]
            assert isinstance(target, dict)
            if rule == "absent":
                target[field] = True
            elif rule == "not_match":
                target[field] = str(target[field]) + " --allow-network"
            elif rule in ("is_true", "is_false"):
                target[field] = "true" if expected is True else 0  # wrong type is refused, not coerced
            elif rule == "int":
                target[field] = 1
            elif rule == "match":
                target[field] = str(target[field]).replace(".cmd", ".bat").replace(".exe", ".com")
            else:
                target[field] = copy.deepcopy(expected)[:-1] if isinstance(expected, list) else str(expected) + "x"
            build_scheduler_fixture(self.root, definition=value)
            label = f"{section}.{field}" if section else field
            _, check = verifier._mix_task_definition_evidence(self.paths["mix_task_definition"])
            self.assertEqual(check["reason"], "mix_task_definition_invalid", label)
            self.assertIn(label, check["invalid_fields"], (label, rule))
            self.assertTrue(check["blocking"], label)

    def test_missing_publication_field_is_blocked(self) -> None:
        value = _definition()
        del value["publication_contract"]["requires_central_receipt_and_registry"]  # type: ignore[union-attr]
        build_scheduler_fixture(self.root, definition=value)
        _, check = verifier._mix_task_definition_evidence(self.paths["mix_task_definition"])
        self.assertEqual(check["invalid_fields"], ["publication_contract.requires_central_receipt_and_registry"])
        self.assertEqual(check["invalid_sections"], ["publication_contract"])

    def test_legacy_feedback_receipt_field_is_refused(self) -> None:
        value = _definition()
        value["publication_contract"]["requires_feedback_publication_receipt"] = True  # type: ignore[index]
        build_scheduler_fixture(self.root, definition=value)
        component = verifier._retained_scheduler_component(self.paths, self.query())
        self.assertEqual(component["reason"], "mix_task_definition_invalid")
        self.assertEqual(component["checks"]["definition"]["invalid_fields"], ["publication_contract.requires_feedback_publication_receipt"])
        production = json.loads(verifier.MIX_TASK_DEFINITION_PATH.read_text(encoding="utf-8"))
        self.assertNotIn("requires_feedback_publication_receipt", production["publication_contract"])

    def test_installer_registers_current_user_so_no_fixed_principal(self) -> None:
        source = INSTALL_MIX_TASK_PS1.read_text(encoding="utf-8")
        principals = re.findall(r"New-ScheduledTaskPrincipal\s+-UserId\s+(\S+)", source)
        self.assertEqual(principals, ["$identity.name"])
        self.assertIn("$identity = Get-CurrentIdentity", source)
        self.assertIsNone(verifier.MIX_TASK_FIXED_PRINCIPAL_SID)

    def _registered(self, **owner: object) -> dict[str, object]:
        task = {"task_name": verifier.MIX_TASK_NAME, "task_path": "\\", "state": "Ready", "xml": _task_xml(), **owner}
        build_scheduler_fixture(self.root, tasks=[task])
        return verifier._retained_scheduler_component(self.paths, self.query())

    def test_owner_same_as_current_user_passes_without_label(self) -> None:
        sid = "S-1-5-21-1-2-3-1001"
        component = self._registered(principal_user_id="PC\\me", principal_sid=sid, current_user_sid=sid.lower())
        self.assertEqual(component["status"], "pass", component.get("reason"))
        self.assertTrue(component["owner"]["matches_current_user"])
        self.assertEqual(component["warnings"], [])

    def test_owner_other_user_or_unresolved_is_a_label_without_fixed_principal(self) -> None:
        other = self._registered(principal_user_id="PC\\other", principal_sid="S-1-5-21-1-2-3-1002", current_user_sid="S-1-5-21-1-2-3-1001")
        self.assertEqual(other["status"], "pass")
        self.assertFalse(other["blocking"])
        self.assertIn("schedule_owner_not_current_user", {item["code"] for item in other["warnings"]})
        unresolved = self._registered(principal_user_id="ghost")
        self.assertEqual(unresolved["status"], "pass")
        self.assertIn("schedule_owner_sid_unverified", {item["code"] for item in unresolved["warnings"]})

    def test_fixed_principal_mismatch_blocks(self) -> None:
        original = verifier.MIX_TASK_FIXED_PRINCIPAL_SID
        verifier.MIX_TASK_FIXED_PRINCIPAL_SID = "S-1-5-21-1-2-3-1001"
        try:
            mismatch = self._registered(principal_sid="S-1-5-21-1-2-3-1002", current_user_sid="S-1-5-21-1-2-3-1002")
            self.assertEqual((mismatch["status"], mismatch["reason"]), ("blocked", "schedule_task_owner_sid_mismatch"))
            match = self._registered(principal_sid="s-1-5-21-1-2-3-1001", current_user_sid="S-1-5-21-1-2-3-1002")
            self.assertEqual(match["status"], "pass", match.get("reason"))
        finally:
            verifier.MIX_TASK_FIXED_PRINCIPAL_SID = original

    def test_not_registered_is_a_warning_not_a_block(self) -> None:
        build_scheduler_fixture(self.root)
        component = verifier._retained_scheduler_component(self.paths, self.query())
        self.assertEqual(component["status"], "warning")
        self.assertFalse(component["blocking"])
        self.assertIsNone(component["pass"])
        self.assertEqual(component["reason"], "schedule_not_registered")
        self.assertIn("schedule_not_registered", {item["code"] for item in component["warnings"]})

    def test_registered_task_matching_definition_passes(self) -> None:
        build_scheduler_fixture(self.root, tasks=[{"task_name": verifier.MIX_TASK_NAME, "task_path": "\\", "state": "Ready", "xml": _task_xml()}])
        component = verifier._retained_scheduler_component(self.paths, self.query())
        self.assertEqual(component["status"], "pass", component.get("reason"))
        self.assertTrue(component["registered"])

    def test_registered_task_mismatch_is_blocked(self) -> None:
        build_scheduler_fixture(
            self.root,
            tasks=[{"task_name": verifier.MIX_TASK_NAME, "task_path": "\\", "state": "Ready", "xml": _task_xml(arguments="/c RUN_SEND_LATEST_REPORT.cmd", start="2026-10-12T07:00:00", limit="PT72H")}],
        )
        component = verifier._retained_scheduler_component(self.paths, self.query())
        self.assertEqual(component["status"], "blocked")
        self.assertEqual(component["reason"], "schedule_task_definition_mismatch")
        self.assertEqual(set(component["mismatches"]), {"action_arguments", "trigger_start_time", "execution_time_limit"})

    def test_registered_but_disabled_is_a_label(self) -> None:
        build_scheduler_fixture(self.root, tasks=[{"task_name": verifier.MIX_TASK_NAME, "task_path": "\\", "state": "Disabled", "xml": _task_xml(enabled="false")}])
        component = verifier._retained_scheduler_component(self.paths, self.query())
        self.assertEqual((component["status"], component["reason"]), ("warning", "schedule_registered_but_disabled"))
        self.assertIsNone(component["pass"])
        self.assertFalse(component["blocking"])
        self.assertIn("schedule_registered_but_disabled", {item["code"] for item in component["warnings"]})

    def test_invalid_definition_and_unavailable_query_are_blocked(self) -> None:
        build_scheduler_fixture(self.root, definition=_definition(arguments="/c RUN_SEND_LATEST_REPORT.cmd"))
        component = verifier._retained_scheduler_component(self.paths, self.query())
        self.assertEqual(component["reason"], "mix_task_definition_invalid")
        build_scheduler_fixture(self.root)
        component = verifier._retained_scheduler_component(self.paths, {"status": "unavailable", "method": "x"})
        self.assertEqual(component["reason"], "schedule_query_unavailable")

    def test_legacy_task_enabled_is_blocked_and_disabled_is_a_label(self) -> None:
        legacy = "D-Stock Telegram Report 08-00"
        enabled = verifier._retained_schedule_component({"status": "ok", "tasks": [{"task_name": legacy, "task_path": "\\", "state": "Ready", "xml": _task_xml()}]})
        self.assertEqual(enabled["reason"], "schedule_legacy_task_enabled")
        self.assertEqual(enabled["enabled_legacy_tasks"], [legacy])
        disabled = verifier._retained_schedule_component({"status": "ok", "tasks": [{"task_name": legacy, "task_path": "\\", "state": "Disabled", "xml": _task_xml(enabled="false")}]})
        self.assertEqual(disabled["status"], "pass")
        self.assertIn("legacy_task_present_disabled", {item["code"] for item in disabled["warnings"]})
        none = verifier._retained_schedule_component({"status": "ok", "tasks": []})
        self.assertEqual(none["status"], "pass")
        self.assertEqual(verifier._retained_schedule_component({"status": "unavailable"})["reason"], "schedule_query_unavailable")

    def test_payload_uses_injected_read_only_query(self) -> None:
        calls: list[tuple[str, ...]] = []

        def fake(names: object) -> dict[str, object]:
            calls.append(tuple(names))  # type: ignore[arg-type]
            return {"status": "ok", "method": "fake", "tasks": []}

        build_scheduler_fixture(self.root)
        payload = verifier._retained_payload(verifier._parse_args(["--structure-only", "--fixture-root", str(self.root)]), task_query=fake)
        self.assertEqual(len(calls), 1)
        self.assertIn(verifier.MIX_TASK_NAME, calls[0])
        self.assertEqual(payload["components"]["workflow_scheduler"]["reason"], "schedule_not_registered")


class MitakeAndSingleStockTests(unittest.TestCase):
    def setUp(self) -> None:
        self._temp = tempfile.TemporaryDirectory()
        self.root = Path(self._temp.name)
        self.paths = verifier._retained_paths(self.root)
        self.reports = self.paths["single_c0_reports_root"]

    def tearDown(self) -> None:
        self._temp.cleanup()

    def test_mitake_is_not_applicable_never_a_pass_and_never_blocks(self) -> None:
        component = verifier._retained_mitake_component({"report_run_id": RUN_ID})
        self.assertEqual(component["status"], "not_applicable")
        self.assertIsNone(component["pass"])
        self.assertFalse(component["blocking"])
        self.assertEqual(component["reason"], "not_applicable_c0")
        self.assertIn("mitake_not_applicable_c0", {item["code"] for item in component["warnings"]})

    def test_no_single_report_is_a_warning(self) -> None:
        component = verifier._retained_single_component(self.paths)
        self.assertEqual((component["status"], component["reason"]), ("warning", "single_c0_no_report_yet"))
        self.assertFalse(component["blocking"])

    def test_valid_reports_pass_and_unverified_is_a_label(self) -> None:
        write_single_report(self.reports, _single_doc("2330", 1, verification={"status": "verified"}))
        write_single_report(self.reports, _single_doc("2330", 63), stamp="20261009T041003670405")
        component = verifier._retained_single_component(self.paths)
        self.assertEqual(component["status"], "pass", component.get("reason"))
        self.assertEqual(component["report_count"], 2)
        self.assertEqual(component["latest_report"]["horizon"], 63)
        self.assertIn("single_c0_report_not_verified", {item["code"] for item in component["warnings"]})

    def test_retired_latest_manifest_is_not_read(self) -> None:
        _write_json(self.reports.parent / "latest_report_manifest.json", {"schema": "tw_stock.single_stock.latest_report_manifest_v3"})
        self.assertEqual(verifier._retained_single_component(self.paths)["reason"], "single_c0_no_report_yet")

    def test_pdf_tamper_is_blocked(self) -> None:
        files = write_single_report(self.reports, _single_doc("2330", 1))
        files["pdf"].write_bytes(files["pdf"].read_bytes() + b"x")
        self.assertEqual(verifier._retained_single_component(self.paths)["reason"], "single_c0_report_pdf_hash_mismatch")

    def test_json_seal_tamper_is_blocked(self) -> None:
        files = write_single_report(self.reports, _single_doc("2330", 1))
        document = json.loads(files["json"].read_bytes())
        document["prob_up"] = 0.99
        _write_json(files["json"], document)
        self.assertEqual(verifier._retained_single_component(self.paths)["reason"], "single_c0_report_seal_mismatch")

    def test_etf_estimate_is_blocked(self) -> None:
        write_single_report(self.reports, _single_doc("0050", 1, ordinary=False))
        self.assertEqual(verifier._retained_single_component(self.paths)["reason"], "single_c0_report_non_ordinary_or_etf_estimate")

    def test_qa_override_report_is_not_a_formal_attachment(self) -> None:
        doc = _single_doc("2330", 1)
        doc["qa_override"] = True
        write_single_report(self.reports, doc)
        self.assertEqual(verifier._retained_single_component(self.paths)["reason"], "single_c0_report_qa_override_not_formal")

    def test_effectiveness_or_pit_claim_is_blocked(self) -> None:
        for field, value in (("historical_pit_verified", True), ("effectiveness_status", "verified"), ("trading_effect", "positive")):
            with self.subTest(field=field), tempfile.TemporaryDirectory() as temp:
                paths = verifier._retained_paths(Path(temp))
                doc = _single_doc("2330", 1)
                doc[field] = value
                write_single_report(paths["single_c0_reports_root"], doc)
                self.assertEqual(verifier._retained_single_component(paths)["reason"], "single_c0_report_claims_unverified_historical_or_effectiveness")

    def test_wrong_source_mode_or_class_is_blocked(self) -> None:
        for field, value in (("source_mode", "local_raw"), ("report_class", "full_market"), ("target_definition", "other")):
            with self.subTest(field=field), tempfile.TemporaryDirectory() as temp:
                paths = verifier._retained_paths(Path(temp))
                doc = _single_doc("2330", 1)
                doc[field] = value
                write_single_report(paths["single_c0_reports_root"], doc)
                self.assertEqual(verifier._retained_single_component(paths)["reason"], "single_c0_report_class_source_or_target_invalid")

    def test_estimate_report_without_estimate_cannot_skip_etf_check(self) -> None:
        doc = _single_doc("0050", 1, ordinary=False)
        for key in ("prediction", "prob_up", "expected_return"):
            doc.pop(key)
        write_single_report(self.reports, doc)
        self.assertEqual(verifier._retained_single_component(self.paths)["reason"], "single_c0_estimate_report_estimate_missing")

    def test_no_estimate_report_passes_and_must_not_carry_estimate(self) -> None:
        doc = _single_doc("2330", 1)
        for key in ("prediction", "prob_up", "expected_return"):
            doc.pop(key)
        doc.update({"report_class": "c0_no_estimate_data_time_only", "estimate_status": "no_estimate", "verification": {"status": "not_applicable_no_estimate"}})
        files = write_single_report(self.reports, doc)
        component = verifier._retained_single_component(self.paths)
        self.assertEqual(component["status"], "pass", component.get("reason"))
        files["json"].unlink()
        files["pdf"].unlink()
        doc["prob_up"] = 0.5
        write_single_report(self.reports, doc)
        self.assertEqual(verifier._retained_single_component(self.paths)["reason"], "single_c0_no_estimate_report_carries_estimate")

    def test_verification_integrity_failure_is_blocked(self) -> None:
        write_single_report(self.reports, _single_doc("2330", 1, verification={"status": "failed", "integrity_error": True}))
        self.assertEqual(verifier._retained_single_component(self.paths)["reason"], "single_c0_report_verification_integrity_failed")


class TelegramComponentTests(unittest.TestCase):
    def setUp(self) -> None:
        self._temp = tempfile.TemporaryDirectory()
        self.root = Path(self._temp.name)
        self.paths = verifier._retained_paths(self.root)
        build_c0_fixture(self.paths["mix_reports_root"])

    def tearDown(self) -> None:
        self._temp.cleanup()

    def component(self) -> dict[str, object]:
        return verifier._retained_telegram_component(self.paths, {"expected_asof": "2026-10-08"}, datetime.now(timezone.utc))

    def test_never_delivered_is_a_warning(self) -> None:
        build_telegram_fixture(self.root)
        component = self.component()
        self.assertEqual(component["status"], "warning")
        self.assertFalse(component["blocking"])
        self.assertEqual(component["reason"], "mix_report_not_yet_delivered")
        self.assertTrue(component["checks"]["service"]["pass"])
        self.assertIn("mix_report_not_yet_delivered", {item["code"] for item in component["warnings"]})

    def test_state_present_but_current_report_not_sent_is_the_same_warning(self) -> None:
        build_telegram_fixture(self.root)
        _write_json(self.paths["mix_delivery_state"], {"schema_version": "mix_report_delivery_state_v1", "deliveries": {}})
        component = self.component()
        self.assertEqual((component["status"], component["blocking"], component["pass"]), ("warning", False, None))
        self.assertEqual(component["reason"], "mix_report_not_yet_delivered")
        self.assertEqual(component["checks"]["mix_delivery"]["status"], "warning")

    def test_delivered_current_report_passes(self) -> None:
        build_telegram_fixture(self.root)
        fingerprint = write_delivery_state(self.paths["mix_reports_root"], self.paths["mix_delivery_state"])
        component = self.component()
        self.assertEqual(component["status"], "pass", component.get("reason"))
        self.assertEqual(component["message_fingerprint"], fingerprint)

    def test_source_identity_mismatch_is_blocked(self) -> None:
        build_telegram_fixture(self.root, owner_sha="0" * 64)
        component = self.component()
        self.assertEqual((component["status"], component["reason"]), ("blocked", "telegram_source_or_pid_identity_invalid"))

    def test_stopped_service_is_blocked(self) -> None:
        build_telegram_fixture(self.root, heartbeat_status="stopped")
        self.assertEqual(self.component()["reason"], "telegram_service_not_running")

    def test_retired_bot_delivery_state_is_not_read(self) -> None:
        build_telegram_fixture(self.root)
        _write_json(self.root / "telegram" / "message_receipt.json", {"version": 1, "deliveries": {}})
        self.assertEqual(self.component()["reason"], "mix_report_not_yet_delivered")


RUN_ID = "c0_20261008T201516005980Z_f7ee0ba9c4"
OTHER_RUN_ID = "c0_20261009T201516005980Z_0123456789"


def _ref(path: Path) -> dict[str, object]:
    raw = path.read_bytes()
    return {"path": str(path), "size": len(raw), "sha256": hashlib.sha256(raw).hexdigest()}


def _json_bytes(value: object) -> bytes:
    return json.dumps(value, ensure_ascii=False, indent=2).encode("utf-8")


def _coverage_record(stock_id: str, market: str = "TWSE", *, ordinary: bool = True) -> dict[str, object]:
    return {
        "stock_id": stock_id,
        "market": market,
        "board": market,
        "is_ordinary_share": ordinary,
        "security_type": "ordinary_share" if ordinary else "etf",
        "current_ordinary_classification_verified": True,
    }


def _prediction(stock_id: str, horizon: int, market: str = "TWSE") -> dict[str, object]:
    return {
        "stock_id": stock_id,
        "market": market,
        "horizon": horizon,
        "target_session_date": None,
        "prob_up": 0.43,
        "expected_return": 0.0,
        "prediction_status": "predicted",
    }


def _data_time(freshness: str = "latest", **extra: object) -> dict[str, object]:
    value: dict[str, object] = {
        "schema": "mix-c0-data-time-v1",
        "analysis_asof": "2026-10-08",
        "asof_by_market": {"TWSE": "2026-10-08"},
        "price_panel_max_date": "2026-10-08",
        "head": {"published_at": "2026-10-08T18:00:00+00:00"},
        "read_at_utc": "2026-10-08T20:10:00+00:00",
        "created_at_utc": "2026-10-08T20:15:16+00:00",
        "is_older_than_requested": False,
        "is_stale": freshness == "stale",
        "stale_markets": {"TWSE": ["2026-10-08", "2026-10-09"]} if freshness == "stale" else {},
        "calendar_verified_through": "2026-10-08",
        "weekdays_after_calendar": [],
        "freshness_status": freshness,
    }
    value.update(extra)
    return value


def build_c0_fixture(
    reports_root: Path,
    *,
    run_id: str = RUN_ID,
    data_time: dict[str, object] | None = None,
    include_data_time: bool = True,
    coverage_records: list[dict[str, object]] | None = None,
    predictions: list[dict[str, object]] | None = None,
    payload_overrides: dict[str, object] | None = None,
    manifest_overrides: dict[str, object] | None = None,
    extra_artifacts: dict[str, object] | None = None,
    created_at: str | None = None,
) -> dict[str, Path]:
    """Write a C0 run laid out exactly like core_daily.run_core_daily."""
    run_dir = reports_root / "c0" / run_id
    run_dir.mkdir(parents=True)
    horizons = [1, 5, 20, 60]
    created = created_at or datetime.now(timezone.utc).isoformat()
    inputs = run_dir / "published_inputs.json"
    inputs.write_bytes(_json_bytes({"bound": True}))
    records = coverage_records if coverage_records is not None else [_coverage_record("1108"), _coverage_record("6152", "TPEX")]
    rows = predictions if predictions is not None else [
        _prediction(stock_id, horizon, market) for stock_id, market in (("1108", "TWSE"), ("6152", "TPEX")) for horizon in horizons
    ]
    binding = {
        "original_model_feature_contract": {"sha256": "a" * 64},
        "current_policy_binding": {"requirements_version": "c0-v1"},
        "contract_adoption_ref": {"sha256": "b" * 64},
        "effective_C0_semantics_projection_sha256": "c" * 64,
        "model_activation_epoch": "2026-10-01T00:00:00+00:00",
    }
    model_binding = {"artifact_binding": {"sha256": "d" * 64}, "pointer_binding": {"sha256": "e" * 64}}
    forward = {"schema": "stock-daily-c0-forward-binding-v1", "base_model_version": "f" * 64}
    payload: dict[str, object] = {
        "schema": "mix-stock-daily-c0-report-v1",
        "run_id": run_id,
        "status": "limited_baseline_effect_unverified",
        **binding,
        "profile": "C0",
        "as_of_date": "20261008",
        "created_at": created,
        "version_labels": [],
        "horizons": horizons,
        "model": {**model_binding, "formal_model_qualified": False},
        "daily_requirements": {"requirements_version": "c0-v1"},
        "forward_binding": forward,
        "input_sources": _ref(inputs),
        "source_binding": {"route_statuses": {"price": "COMPLETE"}, "readiness": {"ready": True}},
        "published_reader_binding": {"snapshot_fingerprint": "x", "sources": []},
        "coverage": {"records": records, "excluded": []},
        "runtime_evidence": {"ok": True},
        "universe_policy": {"selection": "current_verified_ordinary_twse_tpex", "training_scope_is_inference_allowlist": False},
        "predictions": rows,
        "prediction_count": len(rows),
        "usable_prediction_count": sum(1 for row in rows if row.get("prediction_status") == "predicted"),
        "stock_order_is_ranking": False,
        "fit_performed": False,
        "backtests_performed": False,
        "delivery_status": "not_requested",
    }
    if include_data_time:
        payload["data_time"] = data_time or _data_time()
    payload.update(payload_overrides or {})
    payload_path = run_dir / "report_payload.json"
    payload_path.write_bytes(_json_bytes(payload))
    html = run_dir / "report.html"
    html.write_bytes(b"<!doctype html><title>MIX C0</title>")
    excel = run_dir / "MIX_C0.xlsx"
    excel.write_bytes(b"PK\x03\x04fixture-xlsx")
    artifacts: dict[str, object] = {"payload": _ref(payload_path), "html": _ref(html), "input_sources": _ref(inputs), "excel": _ref(excel)}
    artifacts.update(extra_artifacts or {})
    manifest: dict[str, object] = {
        "schema": "mix-stock-daily-c0-manifest-v1",
        "run_id": run_id,
        "status": "limited_baseline_effect_unverified",
        **binding,
        "profile": "C0",
        "as_of_date": "20261008",
        "created_at": created,
        "version_label_count": 0,
        "horizons": horizons,
        "prediction_count": payload["prediction_count"],
        "usable_prediction_count": payload["usable_prediction_count"],
        "artifacts": artifacts,
        "model": model_binding,
        "forward_binding": forward,
        "requirements_version": "c0-v1",
        "formal_model_qualified": False,
        "delivery_status": "not_requested",
    }
    if include_data_time:
        manifest["data_time"] = {key: (data_time or _data_time())[key] for key in ("analysis_asof", "freshness_status", "is_stale")}
    manifest.update(manifest_overrides or {})
    manifest_path = run_dir / "manifest.json"
    manifest_path.write_bytes(_json_bytes(manifest))
    pointer = {
        "schema": "mix-stock-daily-c0-current-report-v1",
        "run_id": run_id,
        "status": "limited_baseline_effect_unverified",
        "profile": "C0",
        "manifest": _ref(manifest_path),
        "as_of_date": "20261008",
        "created_at": created,
        "horizons": horizons,
    }
    pointer_path = reports_root / "LATEST_C0_RUN.json"
    pointer_path.write_bytes(_json_bytes(pointer))
    return {"pointer": pointer_path, "manifest": manifest_path, "payload": payload_path, "html": html, "excel": excel, "run_dir": run_dir}


def _codes(evidence: dict[str, object]) -> set[str]:
    return {str(item.get("code")) for item in evidence.get("warnings") or []}


class MixC0PointerTests(unittest.TestCase):
    def setUp(self) -> None:
        self._temp = tempfile.TemporaryDirectory()
        self.root = Path(self._temp.name)
        self.reports = self.root / "mix" / "reports"
        self.reports.mkdir(parents=True)

    def tearDown(self) -> None:
        self._temp.cleanup()

    def evidence(self, **kwargs: object) -> dict[str, object]:
        kwargs.setdefault("central_expected_asof", "2026-10-08")
        return verifier._mix_c0_report_evidence(self.reports, **kwargs)  # type: ignore[arg-type]

    def test_pointer_constant_is_c0_and_retired_names_are_gone(self) -> None:
        self.assertEqual(verifier.MIX_C0_POINTER_NAME, "LATEST_C0_RUN.json")
        self.assertFalse(hasattr(verifier, "MIX_LATEST_PRODUCTION_POINTER_NAME"))
        self.assertFalse(hasattr(verifier, "MIX_LATEST_REPORT_POINTER_NAME"))
        self.assertEqual(verifier._retained_paths()["mix_c0_pointer"], Path(r"D:\TWStockData\program_data\mix\reports\LATEST_C0_RUN.json"))

    def test_valid_current_report_passes_and_shows_data_time(self) -> None:
        build_c0_fixture(self.reports)
        evidence = self.evidence()
        self.assertTrue(evidence["pass"], evidence.get("reason"))
        self.assertEqual(evidence["run_id"], RUN_ID)
        self.assertEqual(evidence["as_of_date"], "20261008")
        self.assertEqual(evidence["data_time"]["freshness_status"], "latest")
        self.assertEqual(evidence["data_time"]["analysis_asof"], "2026-10-08")
        self.assertTrue(evidence["predictions"]["etf_non_ordinary_excluded_verified"])
        self.assertFalse(evidence["legacy_pointers_read"])
        self.assertEqual(set(evidence["artifacts"]), {"payload", "html", "input_sources", "excel"})
        self.assertNotIn("mix_c0_data_not_latest", _codes(evidence))

    def test_legacy_production_pointer_is_never_a_fallback(self) -> None:
        legacy = {"schema_version": "mix_latest_pointer_v1", "run_id": RUN_ID, "status": "final-before-open"}
        (self.reports / "LATEST_PRODUCTION_RUN.json").write_text(json.dumps(legacy), encoding="utf-8")
        (self.reports / "LATEST_REPORT_RUN.json").write_text(json.dumps(legacy), encoding="utf-8")
        evidence = self.evidence()
        self.assertFalse(evidence["pass"])
        self.assertEqual(evidence["reason"], "mix_c0_pointer_missing")
        build_c0_fixture(self.reports)
        (self.reports / "LATEST_PRODUCTION_RUN.json").write_text("{not json", encoding="utf-8")
        self.assertTrue(self.evidence()["pass"])

    def test_stale_data_is_a_warning_not_a_failure(self) -> None:
        build_c0_fixture(self.reports, data_time=_data_time("stale"))
        evidence = self.evidence()
        self.assertTrue(evidence["pass"], evidence.get("reason"))
        self.assertIn("mix_c0_data_not_latest", _codes(evidence))
        self.assertEqual(evidence["data_time"]["freshness_status"], "stale")

    def test_calendar_unverified_and_missing_data_time_are_warnings(self) -> None:
        build_c0_fixture(self.reports, data_time=_data_time("calendar_unverified_after", weekdays_after_calendar=["2026-10-09"]))
        self.assertIn("mix_c0_data_not_latest", _codes(self.evidence()))
        other = self.root / "other" / "reports"
        other.mkdir(parents=True)
        build_c0_fixture(other, include_data_time=False)
        evidence = verifier._mix_c0_report_evidence(other, central_expected_asof="2026-10-08")
        self.assertTrue(evidence["pass"], evidence.get("reason"))
        self.assertIn("mix_c0_data_time_missing", _codes(evidence))
        self.assertEqual(evidence["data_time"]["report_as_of_date"], "20261008")

    def test_old_report_and_central_asof_difference_are_warnings(self) -> None:
        build_c0_fixture(self.reports, created_at="2026-09-01T00:00:00+00:00")
        evidence = self.evidence(central_expected_asof="2026-10-09")
        self.assertTrue(evidence["pass"], evidence.get("reason"))
        self.assertIn("mix_c0_report_older_than_window", _codes(evidence))
        self.assertIn("mix_c0_asof_differs_from_central", _codes(evidence))
        self.assertIn("mix_c0_central_expected_asof_unavailable", _codes(self.evidence(central_expected_asof=None)))

    def test_newer_runtime_run_is_a_warning(self) -> None:
        paths = build_c0_fixture(self.reports)
        runtime = json.loads(paths["pointer"].read_text(encoding="utf-8"))
        runtime["run_id"] = OTHER_RUN_ID
        (self.reports / "LATEST_C0_RUN_BY_RUNTIME.json").write_text(json.dumps(runtime), encoding="utf-8")
        evidence = self.evidence()
        self.assertTrue(evidence["pass"], evidence.get("reason"))
        self.assertIn("mix_c0_newer_run_not_selected", _codes(evidence))

    def test_etf_prediction_is_blocked(self) -> None:
        records = [_coverage_record("1108"), _coverage_record("0050", ordinary=False)]
        rows = [_prediction(stock_id, horizon) for stock_id in ("1108", "0050") for horizon in (1, 5, 20, 60)]
        build_c0_fixture(self.reports, coverage_records=records, predictions=rows)
        evidence = self.evidence()
        self.assertFalse(evidence["pass"])
        self.assertEqual(evidence["reason"], "mix_c0_prediction_non_ordinary_or_etf_present")

    def test_missing_coverage_records_fail_closed(self) -> None:
        build_c0_fixture(self.reports, coverage_records=[])
        evidence = self.evidence()
        self.assertFalse(evidence["pass"])
        self.assertEqual(evidence["reason"], "mix_c0_coverage_records_missing_etf_exclusion_unverifiable")

    def test_prediction_outside_coverage_is_blocked(self) -> None:
        rows = [_prediction(stock_id, horizon) for stock_id in ("1108", "9999") for horizon in (1, 5, 20, 60)]
        build_c0_fixture(self.reports, coverage_records=[_coverage_record("1108")], predictions=rows)
        self.assertEqual(self.evidence()["reason"], "mix_c0_prediction_outside_ordinary_coverage")

    def test_incomplete_horizons_are_blocked(self) -> None:
        rows = [_prediction("1108", horizon) for horizon in (1, 5, 20)]
        build_c0_fixture(self.reports, coverage_records=[_coverage_record("1108")], predictions=rows)
        self.assertEqual(self.evidence()["reason"], "mix_c0_prediction_horizons_incomplete")

    def test_tampered_payload_bytes_are_blocked(self) -> None:
        paths = build_c0_fixture(self.reports)
        paths["payload"].write_bytes(paths["payload"].read_bytes() + b" ")
        evidence = self.evidence()
        self.assertFalse(evidence["pass"])
        self.assertEqual(evidence["reason"], "mix_c0_payload_size_or_sha256_mismatch")

    def test_tampered_manifest_bytes_are_blocked(self) -> None:
        paths = build_c0_fixture(self.reports)
        paths["manifest"].write_bytes(paths["manifest"].read_bytes().replace(b"not_requested", b"not_requestex", 1))
        self.assertEqual(self.evidence()["reason"], "mix_c0_manifest_size_or_sha256_mismatch")

    def test_duplicate_json_key_pointer_is_blocked(self) -> None:
        paths = build_c0_fixture(self.reports)
        text = paths["pointer"].read_text(encoding="utf-8")
        paths["pointer"].write_text(text.replace('"profile": "C0"', '"profile": "C0", "profile": "C0"', 1), encoding="utf-8")
        self.assertEqual(self.evidence()["reason"], "mix_c0_pointer_invalid_json")

    def test_ref_outside_run_namespace_is_blocked(self) -> None:
        outside = self.root / "outside.html"
        outside.write_bytes(b"x")
        build_c0_fixture(self.reports, extra_artifacts={"html": _ref(outside)})
        self.assertEqual(self.evidence()["reason"], "mix_c0_html_path_not_current_run_ref")

    def test_unknown_artifact_and_wrong_schema_are_blocked(self) -> None:
        paths = build_c0_fixture(self.reports, extra_artifacts={"summary_pdf": {"path": "x", "size": 1, "sha256": "0" * 64}})
        self.assertEqual(self.evidence()["reason"], "mix_c0_manifest_unknown_artifact_ref")
        pointer = json.loads(paths["pointer"].read_text(encoding="utf-8"))
        pointer["schema"] = "mix_latest_pointer_v1"
        paths["pointer"].write_text(json.dumps(pointer), encoding="utf-8")
        self.assertEqual(self.evidence()["reason"], "mix_c0_pointer_schema_invalid")

    def test_same_run_binding_mismatch_is_blocked(self) -> None:
        build_c0_fixture(self.reports, payload_overrides={"forward_binding": {"base_model_version": "1" * 64}})
        self.assertEqual(self.evidence()["reason"], "mix_c0_manifest_payload_forward_binding_mismatch")

    def test_version_labels_are_warnings(self) -> None:
        build_c0_fixture(self.reports, payload_overrides={"version_labels": [{"code": "policy_drift"}]})
        evidence = self.evidence()
        self.assertTrue(evidence["pass"], evidence.get("reason"))
        self.assertIn("mix_c0_report_version_labels", _codes(evidence))

    def test_forward_binding_version_labels_are_warnings(self) -> None:
        forward = {"schema": "stock-daily-c0-forward-binding-v1", "base_model_version": "f" * 64, "version_labels": [{"code": "model_drift"}]}
        build_c0_fixture(self.reports, payload_overrides={"forward_binding": forward}, manifest_overrides={"forward_binding": forward})
        evidence = self.evidence()
        self.assertTrue(evidence["pass"], evidence.get("reason"))
        labels = [item for item in evidence["warnings"] if item["code"] == "mix_c0_report_version_labels"]
        self.assertEqual(labels[0]["codes"], ["model_drift"])

    def test_created_at_falls_back_to_pointer_like_sender(self) -> None:
        paths = build_c0_fixture(self.reports, include_data_time=False)
        for key in ("payload", "manifest"):
            document = json.loads(paths[key].read_bytes())
            document.pop("created_at")
            paths[key].write_bytes(_json_bytes(document))
        manifest = json.loads(paths["manifest"].read_bytes())
        manifest["artifacts"]["payload"] = _ref(paths["payload"])
        paths["manifest"].write_bytes(_json_bytes(manifest))
        pointer = json.loads(paths["pointer"].read_bytes())
        pointer["manifest"] = _ref(paths["manifest"])
        pointer["created_at"] = "2026-10-08T20:15:16+00:00"
        paths["pointer"].write_bytes(_json_bytes(pointer))
        evidence = self.evidence()
        self.assertTrue(evidence["pass"], evidence.get("reason"))
        self.assertEqual(evidence["data_time"]["created_at"], "2026-10-08T20:15:16+00:00")
        self.assertNotIn("mix_c0_created_at_missing", _codes(evidence))

    def test_delivery_fingerprint_matches_sender_algorithm(self) -> None:
        paths = build_c0_fixture(self.reports)
        evidence = self.evidence()
        self.assertTrue(evidence["pass"], evidence.get("reason"))
        # Independent re-implementation of mix_report_sender.load_mix_bundle's fingerprint:
        # manifest ref, then artifact refs in ARTIFACT_NAMES order, resolved path + size + sha256.
        def record(path: Path) -> tuple[str, int, str]:
            raw = path.read_bytes()
            return str(path.resolve(strict=False)), len(raw), hashlib.sha256(raw).hexdigest()

        refs = [record(paths["manifest"])] + [
            record(paths["run_dir"] / name)
            for name in ("report_payload.json", "report.html", "published_inputs.json", "MIX_C0.xlsx")
        ]
        body = {"schema": "mix-stock-daily-c0-report-v1", "run_id": RUN_ID, "refs": refs}
        expected = hashlib.sha256(
            json.dumps(body, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False, default=str).encode("utf-8")
        ).hexdigest()
        self.assertEqual(verifier._mix_c0_delivery_fingerprint(RUN_ID, evidence["manifest"], evidence["artifacts"]), expected)

    def test_formal_model_claim_is_blocked(self) -> None:
        build_c0_fixture(self.reports, manifest_overrides={"formal_model_qualified": True})
        self.assertEqual(self.evidence()["reason"], "mix_c0_formal_model_qualified_claimed")

    def test_retained_component_reads_c0_pointer_and_exposes_manifest(self) -> None:
        build_c0_fixture(self.reports)
        paths = verifier._retained_paths(self.root)
        self.assertEqual(paths["mix_c0_pointer"], (self.root / "mix" / "reports" / "LATEST_C0_RUN.json").resolve())
        component = verifier._retained_mix_component(paths, {"expected_asof": "2026-10-08"}, datetime.now(timezone.utc))
        self.assertTrue(component["pass"], component.get("reason"))
        self.assertEqual(component["report_run_id"], RUN_ID)
        self.assertEqual(component["expected_asof"], "2026-10-08")
        self.assertTrue(component["artifacts"]["artifact_manifest"]["path"].endswith("manifest.json"))

    def test_retained_payload_passes_mix_and_surfaces_warnings(self) -> None:
        build_c0_fixture(self.reports, data_time=_data_time("stale"))
        args = verifier._parse_args(["--structure-only", "--fixture-root", str(self.root)])
        payload = verifier._build_payload(args)
        self.assertTrue(payload["components"]["mix_analysis_report"]["pass"])
        self.assertNotIn("mix_analysis_report", {item["component_id"] for item in payload["blocking_failures"]})
        self.assertIn("mix_c0_data_not_latest", {item["code"] for item in payload["warnings"] if item["component_id"] == "mix_analysis_report"})


def write_delivery_state(reports_root: Path, state_path: Path, *, run_id: str = RUN_ID) -> str:
    """mix_report_delivery_state_v1 exactly as mix_report_sender.deliver_mix_bundle records a complete send."""
    evidence = verifier._mix_c0_report_evidence(reports_root, central_expected_asof="2026-10-08")
    assert evidence["pass"], evidence.get("reason")
    fingerprint = verifier._mix_c0_delivery_fingerprint(run_id, evidence["manifest"], evidence["artifacts"])
    keys = ["summary:" + "1" * 64] + [f"MIX C0 {name}:{evidence[name]['sha256'] if name == 'manifest' else evidence['artifacts'][name]['sha256']}" for name in ("manifest", "payload", "html", "excel")]
    state = {
        "schema_version": "mix_report_delivery_state_v1",
        "deliveries": {
            fingerprint: {
                "status": "complete",
                "report_delivery_status": "complete",
                "profile": "C0",
                "run_id": run_id,
                "manifest_path": str(Path(evidence["manifest"]["path"]).resolve()),
                "manifest_sha256": evidence["manifest"]["sha256"],
                "expected_asof": "2026-10-08",
                "sent_items": keys,
                "messages": [{"key": key, "message_id": index + 10} for index, key in enumerate(keys)],
                "completed_at": "2026-10-09T08:00:30+08:00",
            }
        },
        "last_complete_fingerprint": fingerprint,
        "last_report_complete_fingerprint": fingerprint,
    }
    _write_json(state_path, state)
    return fingerprint


class MixC0DeliveryEvidenceTests(unittest.TestCase):
    def setUp(self) -> None:
        self._temp = tempfile.TemporaryDirectory()
        self.root = Path(self._temp.name)
        self.reports = self.root / "mix" / "reports"
        self.reports.mkdir(parents=True)
        self.state_path = self.root / "telegram" / "mix_report_delivery_state.json"

    def tearDown(self) -> None:
        self._temp.cleanup()

    def evidence(self) -> dict[str, object]:
        return verifier._mix_c0_delivery_evidence(self.reports, self.state_path, central_expected_asof="2026-10-08")

    def test_current_report_delivery_passes(self) -> None:
        build_c0_fixture(self.reports)
        fingerprint = write_delivery_state(self.reports, self.state_path)
        evidence = self.evidence()
        self.assertTrue(evidence["pass"], evidence.get("reason"))
        self.assertEqual(evidence["fingerprint"], fingerprint)
        self.assertEqual(evidence["message_counts"], {"summary": 1, "document": 4})

    def test_missing_state_is_not_yet_delivered_warning(self) -> None:
        build_c0_fixture(self.reports)
        evidence = self.evidence()
        self.assertEqual((evidence["status"], evidence["reason"]), ("warning", "mix_report_not_yet_delivered"))
        self.assertFalse(evidence["blocking"])
        self.assertIsNone(evidence["pass"])

    def test_state_without_current_report_record_is_not_yet_delivered_warning(self) -> None:
        """Same meaning as a missing state file (not sent yet): label, never a pass, never a block."""
        build_c0_fixture(self.reports)
        _write_json(self.state_path, {"schema_version": "mix_report_delivery_state_v1", "deliveries": {}})
        evidence = self.evidence()
        self.assertEqual((evidence["status"], evidence["reason"]), ("warning", "mix_report_not_yet_delivered"))
        self.assertFalse(evidence["blocking"])
        self.assertIsNone(evidence["pass"])
        self.assertTrue(evidence["delivery_state_present"])
        codes = {item["code"] for item in evidence["warnings"]}
        self.assertIn("mix_report_not_yet_delivered", codes)
        self.assertNotIn("mix_delivery_current_report_not_delivered", {evidence.get("reason"), *codes})

    def test_only_other_report_delivered_is_not_yet_delivered_warning(self) -> None:
        build_c0_fixture(self.reports)
        write_delivery_state(self.reports, self.state_path)
        state = json.loads(self.state_path.read_text(encoding="utf-8"))
        entry = state["deliveries"].pop(next(iter(state["deliveries"])))
        entry["run_id"] = "C0_OTHER_EARLIER_RUN"
        state["deliveries"]["0" * 64] = entry
        state["last_complete_fingerprint"] = state["last_report_complete_fingerprint"] = "0" * 64
        _write_json(self.state_path, state)
        evidence = self.evidence()
        self.assertEqual((evidence["status"], evidence["reason"], evidence["pass"]), ("warning", "mix_report_not_yet_delivered", None))
        self.assertEqual(evidence["warnings"][0]["last_complete_fingerprint"], "0" * 64)

    def test_current_run_recorded_under_other_fingerprint_blocks(self) -> None:
        """Record for the current run_id exists but its fingerprint differs: integrity, still blocked."""
        build_c0_fixture(self.reports)
        write_delivery_state(self.reports, self.state_path)
        state = json.loads(self.state_path.read_text(encoding="utf-8"))
        entry = state["deliveries"].pop(next(iter(state["deliveries"])))
        state["deliveries"]["0" * 64] = entry
        _write_json(self.state_path, state)
        evidence = self.evidence()
        self.assertEqual(evidence["reason"], "mix_delivery_state_fingerprint_mismatch")
        self.assertTrue(evidence["blocking"])
        self.assertFalse(evidence["pass"])
        self.assertEqual(evidence["recorded_fingerprints"], ["0" * 64])

    def _mutate_current_entry(self, mutate: object) -> dict[str, object]:
        if not (self.reports / "LATEST_C0_RUN.json").exists():
            build_c0_fixture(self.reports)
        write_delivery_state(self.reports, self.state_path)
        state = json.loads(self.state_path.read_text(encoding="utf-8"))
        key = next(iter(state["deliveries"]))
        result = mutate(state["deliveries"][key])  # type: ignore[operator]
        if result is not None:
            state["deliveries"][key] = result
        _write_json(self.state_path, state)
        return self.evidence()

    def test_current_record_inconsistent_or_corrupt_still_blocks(self) -> None:
        cases = {
            "mix_delivery_state_identity_mismatch": lambda entry: entry.update(manifest_sha256="f" * 64),
            "mix_delivery_state_not_complete": lambda entry: entry.update(report_delivery_status="in_progress"),
            "mix_delivery_message_id_missing": lambda entry: entry["messages"][1].update(message_id=None),
            "mix_delivery_message_receipt_keys_inconsistent": lambda entry: entry.update(sent_items=entry["sent_items"][:-1]),
            "mix_delivery_state_entry_invalid": lambda entry: "corrupt",
        }
        for reason, mutate in cases.items():
            evidence = self._mutate_current_entry(mutate)
            self.assertEqual(evidence["reason"], reason)
            self.assertTrue(evidence["blocking"], reason)
            self.assertFalse(evidence["pass"], reason)

    def test_unreadable_state_file_still_blocks(self) -> None:
        build_c0_fixture(self.reports)
        self.state_path.parent.mkdir(parents=True, exist_ok=True)
        self.state_path.write_bytes(b"{not json")
        evidence = self.evidence()
        self.assertEqual(evidence["reason"], "mix_delivery_state_missing_or_invalid")
        self.assertTrue(evidence["blocking"])

    def test_invalid_message_id_is_blocked(self) -> None:
        build_c0_fixture(self.reports)
        write_delivery_state(self.reports, self.state_path)
        state = json.loads(self.state_path.read_text(encoding="utf-8"))
        entry = next(iter(state["deliveries"].values()))
        entry["messages"][0]["message_id"] = 0
        _write_json(self.state_path, state)
        self.assertEqual(self.evidence()["reason"], "mix_delivery_message_id_missing")

    def test_wrong_state_schema_is_blocked(self) -> None:
        build_c0_fixture(self.reports)
        _write_json(self.state_path, {"version": 1, "deliveries": {}})
        self.assertEqual(self.evidence()["reason"], "mix_delivery_state_schema_invalid")

    def test_invalid_current_report_blocks_delivery_check(self) -> None:
        build_c0_fixture(self.reports)
        write_delivery_state(self.reports, self.state_path)
        (self.reports / "LATEST_C0_RUN.json").unlink()
        self.assertEqual(self.evidence()["reason"], "mix_c0_pointer_missing")


def _quarantine_program_constants() -> dict[str, object]:
    """ALLOWED_ROOT_IDS / ALLOWED_SUFFIXES / PROTECTED_ROOTS / ALLOWLIST_SCHEMA of program_quarantine.py (AST, never imported)."""
    tree = ast.parse(verifier.QUARANTINE_PROGRAM_PATH.read_text(encoding="utf-8"))
    values: dict[str, ast.expr] = {}
    for node in tree.body:
        if isinstance(node, ast.Assign) and len(node.targets) == 1 and isinstance(node.targets[0], ast.Name):
            values[node.targets[0].id] = node.value

    def strings(node: ast.AST) -> list[str]:
        return [item.value for item in ast.walk(node) if isinstance(item, ast.Constant) and isinstance(item.value, str)]

    roots = values["ALLOWED_ROOT_IDS"]
    assert isinstance(roots, ast.Dict)
    return {
        "allowed_root_ids": {key.value: strings(value)[0] for key, value in zip(roots.keys, roots.values) if isinstance(key, ast.Constant)},
        "allowed_suffixes": set(strings(values["ALLOWED_SUFFIXES"])),
        "protected_roots": strings(values["PROTECTED_ROOTS"]),
        "allowlist_schema": strings(values["ALLOWLIST_SCHEMA"])[0],
    }


class GovernanceObservationTests(unittest.TestCase):
    """data_retention_guard / retirement_isolation: read-only direct observation."""

    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name).resolve()
        build_central_fixture(self.root)
        self.config = build_governance_fixture(self.root)
        self.retired = self.config["retired_roots"]
        self.scopes = {scope["scope_id"]: Path(scope["path"]) for scope in self.config["scan_scopes"]}  # type: ignore[union-attr]

    def tearDown(self) -> None:
        self.temp.cleanup()

    def components(self, query: dict[str, object] | None = None) -> tuple[dict[str, object], dict[str, object]]:
        args = verifier._parse_args(["--structure-only", "--fixture-root", str(self.root)])
        payload = verifier._retained_payload(args, task_query=(lambda names: query) if query is not None else None)
        return payload["components"]["data_retention_guard"], payload["components"]["retirement_isolation"]

    def retirement(self, query: dict[str, object] | None = None) -> dict[str, object]:
        return self.components(query)[1]

    def retention(self) -> dict[str, object]:
        return self.components()[0]

    def retired_path(self, root_id: str, *parts: str) -> Path:
        return self.retired[root_id].joinpath(*parts)  # type: ignore[index]

    # -- mirror of program_quarantine.py ------------------------------------

    def test_constants_mirror_program_quarantine(self) -> None:
        constants = _quarantine_program_constants()
        self.assertEqual(constants["allowlist_schema"], verifier.QUARANTINE_ALLOWLIST_SCHEMA)
        self.assertEqual(constants["allowed_root_ids"], {key: str(value) for key, value in verifier.RETIRED_PROGRAM_ROOTS.items()})
        self.assertEqual(constants["allowed_suffixes"], set(verifier.RETIRED_PROGRAM_SUFFIXES))
        self.assertEqual(constants["protected_roots"], [str(item) for item in verifier.QUARANTINE_PROTECTED_ROOTS])
        for root in verifier.RETAINED_DATA_AUTHORITY_ROOTS.values():
            self.assertIn(root, verifier.QUARANTINE_PROTECTED_ROOTS)
        for root in verifier.RETIRED_LEGACY_DATA_ROOTS.values():
            self.assertIn(root, verifier.QUARANTINE_PROTECTED_ROOTS)

    def test_production_scope_is_fixed_and_documented(self) -> None:
        config = verifier._retained_governance_config(verifier._retained_paths())
        self.assertEqual(verifier._retained_paths()["quarantine_allowlist"], Path(r"D:\TWStockData\services\acceptance\program_quarantine_allowlist_v1.json"))
        blocking = {scope["scope_id"] for scope in config["scan_scopes"] if scope["blocking"]}  # type: ignore[union-attr]
        self.assertEqual(blocking, {"mix_src", "mix_tools", "mix_entrypoints", "single_stock_analysis", "telegram_report_delivery", "shared", "shared_data_management", "central_market_update", "central_control"})
        self.assertIn("external_entrypoints", verifier.REFERENCE_SCAN_EXCLUDED_DIR_NAMES)
        self.assertIn("tests", verifier.REFERENCE_SCAN_EXCLUDED_DIR_NAMES)

    def test_production_allowlist_satisfies_entry_rules(self) -> None:
        entries, check = verifier._governance_allowlist(verifier.QUARANTINE_ALLOWLIST_PATH, verifier.RETIRED_PROGRAM_ROOTS)
        self.assertEqual(check["status"], "pass", check.get("reason"))
        assert entries is not None
        self.assertTrue(entries)
        self.assertTrue(all(item["suffix"] in verifier.RETIRED_PROGRAM_SUFFIXES for item in entries))

    # -- passing fixture -----------------------------------------------------

    def test_observational_fixture_passes_both_without_legacy_files(self) -> None:
        retention, retirement = self.components()
        self.assertEqual(retention["status"], "pass", retention.get("failures"))
        self.assertEqual(retirement["status"], "pass", retirement.get("failures"))
        for component in (retention, retirement):
            self.assertEqual(component["observation_basis"], "read_only_current_state_observation_not_event_audit")
            self.assertFalse(component["checks"]["legacy_evidence"]["present"])
            self.assertNotIn("zero_touch", component)
            self.assertNotIn("data_untouched", component)
        self.assertEqual(retirement["checks"]["retired_files_absent"]["entry_total"], len(GOVERNANCE_ENTRIES))
        self.assertEqual(retention["checks"]["central_publication"]["route_objects_verified"], 6)
        self.assertEqual({item["code"] for item in retention["warnings"]}, {"legacy_data_root_absent"})
        rules = retirement["checks"]["reference_scan"]["rules"]
        self.assertIn("twmix", rules["retired_python_modules"])
        self.assertIn("stock_master", rules["retired_python_modules"])
        self.assertTrue(all(scope["files_scanned"] >= 1 for scope in retirement["checks"]["reference_scan"]["scopes"]))

    def test_valid_legacy_files_are_still_accepted(self) -> None:
        build_governance_fixture(self.root, legacy=True)
        retention, retirement = self.components()
        self.assertEqual(retention["status"], "pass", retention.get("failures"))
        self.assertEqual(retirement["status"], "pass", retirement.get("failures"))
        self.assertTrue(retention["checks"]["legacy_evidence"]["pass"])
        self.assertTrue(retirement["checks"]["legacy_evidence"]["pass"])

    def test_present_legacy_files_are_validated_with_the_original_rules(self) -> None:
        build_governance_fixture(self.root, legacy=True)
        for name, label in (("retention/data_retention_guard.json", "data_retention_guard"), ("retirement/retirement_isolation.json", "retirement_isolation")):
            path = self.root.joinpath(*name.split("/"))
            document = json.loads(path.read_text(encoding="utf-8"))
            document["status"] = "pending"
            _write_json(path, document)
        retention, retirement = self.components()
        self.assertEqual(retention["reason"], "data_retention_guard_status_invalid")
        self.assertEqual(retirement["reason"], "retirement_isolation_status_invalid")
        _write_json(self.root / "retirement" / "retirement_isolation.json", {"schema": "cross-system-retirement-isolation-v1", "status": "verified", "data_untouched": True, "telegram_protected": True, "central_union": {"total": 5, "required": 5, "optional": 0}, "isolated_consumers": ["cowork"]})
        self.assertEqual(self.retirement()["reason"], "retirement_isolation_central_union_invalid")
        (self.root / "retention" / "data_retention_guard.json").write_bytes(b"{not json")
        self.assertEqual(self.retention()["reason"], "data_retention_guard_invalid_json")

    # -- allowlist -----------------------------------------------------------

    def test_missing_or_invalid_allowlist_blocks_both(self) -> None:
        (self.root / "governance" / "program_quarantine_allowlist_v1.json").unlink()
        retention, retirement = self.components()
        self.assertEqual(retention["reason"], "quarantine_allowlist_missing")
        self.assertEqual(retirement["reason"], "quarantine_allowlist_missing")
        write_allowlist(self.root, allowlist_entries(self.root), schema="other")
        self.assertEqual(self.retirement()["reason"], "quarantine_allowlist_schema_invalid")
        write_allowlist(self.root, [])
        self.assertEqual(self.retention()["reason"], "quarantine_allowlist_entries_required")

    def test_allowlist_entry_rules_block(self) -> None:
        roots = self.retired
        base = allowlist_entries(self.root)
        cases: list[tuple[str, object]] = []
        extra = dict(base[0], note="x")
        cases.append(("quarantine_allowlist_entry_invalid", [extra]))
        cases.append(("quarantine_allowlist_unknown_root", [dict(base[0], source_root_id="mix")]))
        cases.append(("quarantine_allowlist_relative_path_invalid", [dict(base[0], relative_path="..\\escape.cmd")]))
        cases.append(("quarantine_allowlist_source_path_mismatch", [dict(base[0], source_path=str(self.root / "elsewhere.cmd"))]))
        cases.append(("quarantine_allowlist_category_invalid", [dict(base[0], category="retained")]))
        cases.append(("quarantine_allowlist_duplicate_entry", [base[0], dict(base[0])]))
        overlap = {"entry_id": "cowork-x", "source_root_id": "cowork", "relative_path": "premarket_daytrade_top10\\x.py", "source_path": str(self.retired_path("cowork", "premarket_daytrade_top10", "x.py")), "category": "retired_analysis"}
        cases.append(("quarantine_allowlist_root_overlap", [overlap]))
        for reason, entries in cases:
            path = write_allowlist(self.root, entries)  # type: ignore[arg-type]
            normalized, check = verifier._governance_allowlist(path, roots)  # type: ignore[arg-type]
            self.assertIsNone(normalized, reason)
            self.assertEqual(check["reason"], reason)
        write_allowlist(self.root, [extra])
        retention, retirement = self.components()
        self.assertEqual(retention["reason"], "quarantine_allowlist_entry_invalid")
        self.assertEqual(retirement["reason"], "quarantine_allowlist_entry_invalid")

    # -- retirement_isolation ------------------------------------------------

    def test_retired_file_still_present_blocks(self) -> None:
        path = self.retired_path("twmix", "src", "twmix", "pipeline.py")
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text("# still here\n", encoding="utf-8")
        component = self.retirement()
        self.assertEqual(component["reason"], "retired_program_file_present")
        self.assertEqual(component["checks"]["retired_files_absent"]["present"][0]["path"], str(path))

    def test_absolute_reference_in_current_code_blocks(self) -> None:
        target = self.retired_path("twmix", "src", "twmix", "pipeline.py")
        for scope_id, text in (
            ("central", f"TARGET = {str(target)!r}\n"),
            ("mix", f"CMD = {json.dumps(str(target.parent))}\n"),
            ("single", "call \"" + str(target).replace("\\", "/") + "\"\n"),
        ):
            with self.subTest(scope_id=scope_id):
                build_governance_fixture(self.root)
                for leftover in self.root.joinpath("programs").rglob("ref.*"):
                    leftover.unlink()
                (self.scopes[scope_id] / ("ref.py" if scope_id != "single" else "ref.cmd")).write_text(text, encoding="utf-8")
                component = self.retirement()
                self.assertEqual(component["reason"], "retired_program_referenced_by_current_code")
                self.assertEqual(component["failures"][0]["scope_id"], scope_id)

    def test_relative_reference_to_retired_directory_blocks(self) -> None:
        (self.scopes["mix"] / "run.cmd").write_text('call "%~dp0..\\full_market_analysis\\RUN_QUICK_UPDATE.cmd"\n', encoding="utf-8")
        component = self.retirement()
        self.assertEqual(component["reason"], "retired_program_referenced_by_current_code")
        scope = next(item for item in component["checks"]["reference_scan"]["scopes"] if item["scope_id"] == "mix")
        self.assertEqual(scope["files_with_references"][0]["hits"][0]["match"], "\\full_market_analysis")

    def test_piecewise_assembled_retired_path_blocks(self) -> None:
        for name, text in (
            ("join.py", 'ROOT = Path(r"D:\\stock\\programs") / "full_market_analysis"\n'),
            ("join2.py", "ROOT = os.path.join(BASE, 'twmix', 'src')\n"),
            ("join.ps1", '$root = Join-Path $base "daily_analysis_scheduler"\n'),
        ):
            with self.subTest(name=name):
                for leftover in self.scopes["shared"].glob("join*"):
                    leftover.unlink()
                (self.scopes["shared"] / name).write_text(text, encoding="utf-8")
                component = self.retirement()
                self.assertEqual(component["reason"], "retired_program_referenced_by_current_code")
                self.assertEqual(component["failures"][0]["scope_id"], "shared")
        for leftover in self.scopes["shared"].glob("join*"):
            leftover.unlink()
        (self.scopes["shared"] / "ok.py").write_text("ENV = 'TWMIX_HOME'\nNAME = 'twmix_report'\n", encoding="utf-8")
        self.assertEqual(self.retirement()["status"], "pass")

    def test_reappeared_retired_root_without_listed_files_is_a_label(self) -> None:
        root = Path(self.retired["full_market_analysis"])  # type: ignore[index]
        self.assertNotIn("retired_program_root_present", {item["code"] for item in self.retirement()["warnings"]})
        root.mkdir(parents=True, exist_ok=True)
        (root / "unrelated.txt").write_text("x\n", encoding="utf-8")
        component = self.retirement()
        self.assertEqual(component["status"], "pass", component.get("failures"))
        codes = [item for item in component["warnings"] if item["code"] == "retired_program_root_present"]
        self.assertEqual([item["root_id"] for item in codes], ["full_market_analysis"])

    def test_retired_python_import_and_dynamic_import_block(self) -> None:
        for text in ("from twmix.pipeline import run\n", "import cowork\n", "import importlib\nimportlib.import_module('premarket_top10.cli')\n", "stock_master = __import__('stock_master')\n"):
            with self.subTest(text=text):
                (self.scopes["telegram"] / "bot.py").write_text(text, encoding="utf-8")
                component = self.retirement()
                self.assertEqual(component["reason"], "retired_program_referenced_by_current_code")
                scope = next(item for item in component["checks"]["reference_scan"]["scopes"] if item["scope_id"] == "telegram")
                self.assertEqual(scope["files_with_references"][0]["hits"][0]["kind"], "python_import")

    def test_module_shadowed_by_current_module_is_not_a_retired_import(self) -> None:
        (self.scopes["shared"] / "stock_master.py").write_text("VALUE = 1\n", encoding="utf-8")
        (self.scopes["mix"] / "use.py").write_text("import stock_master\n", encoding="utf-8")
        component = self.retirement()
        self.assertEqual(component["status"], "pass", component.get("failures"))
        self.assertIn("stock_master", component["checks"]["reference_scan"]["rules"]["retired_python_modules_shadowed_by_current_module"])

    def test_shared_root_itself_and_data_names_are_not_references(self) -> None:
        cowork_logs = self.retired_path("cowork", "logs")
        (self.scopes["mix"] / "paths.py").write_text(f"LOGS = {str(cowork_logs)!r}\nMANIFEST = '_cowork_shared_data_manifest.json'\nENV = 'TWMIX_HOME'\n", encoding="utf-8")
        self.assertEqual(self.retirement()["status"], "pass")

    def test_excluded_trees_and_non_retained_tooling_do_not_block(self) -> None:
        target = str(self.retired_path("full_market_analysis", "RUN_QUICK_UPDATE.cmd"))
        for relative in ("tests/test_old.py", "backups/old.py", "external_entrypoints/old.cmd"):
            path = self.scopes["central"].joinpath(*relative.split("/"))
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(f"X = {target!r}\n", encoding="utf-8")
        (self.scopes["mix"] / "test_retired.py").write_text(f"X = {target!r}\n", encoding="utf-8")
        (self.scopes["tooling"] / "cleanup.ps1").write_text(f"$excluded = '{target}'\n", encoding="utf-8")
        component = self.retirement()
        self.assertEqual(component["status"], "pass", component.get("failures"))
        warnings = [item for item in component["warnings"] if item["code"] == "retired_program_referenced_by_non_retained_tree"]
        self.assertEqual([item["path"] for item in warnings], [str(self.scopes["tooling"] / "cleanup.ps1")])

    def test_missing_blocking_scan_scope_blocks(self) -> None:
        for item in sorted(self.scopes["central"].rglob("*"), reverse=True):
            item.unlink() if item.is_file() else item.rmdir()
        self.scopes["central"].rmdir()
        component = self.retirement()
        self.assertEqual(component["reason"], "retirement_reference_scan_scope_missing")

    def test_unreadable_current_file_blocks(self) -> None:
        (self.scopes["single"] / "locked.py").write_text("X = 1\n", encoding="utf-8")
        original = Path.open

        def guarded(path: Path, *args: object, **kwargs: object):  # type: ignore[no-untyped-def]
            if path.name == "locked.py":
                raise PermissionError("locked")
            return original(path, *args, **kwargs)  # type: ignore[arg-type]

        with mock.patch.object(Path, "open", guarded):
            component = self.retirement()
        self.assertEqual(component["reason"], "retirement_reference_scan_file_unreadable")

    def test_scheduled_task_pointing_at_retired_program(self) -> None:
        target = str(self.retired_path("full_market_analysis", "RUN_QUICK_UPDATE.cmd"))
        enabled = {"status": "ok", "method": "fake", "tasks": [{"task_name": verifier.MIX_TASK_NAME, "task_path": "\\", "state": "Ready", "xml": _task_xml(arguments=f'/c "{target}"')}]}
        component = self.retirement(enabled)
        self.assertEqual(component["reason"], "retired_program_referenced_by_enabled_task")
        disabled = {"status": "ok", "method": "fake", "tasks": [{"task_name": "MIX ALL Daily Analysis", "task_path": "\\", "state": "Disabled", "xml": _task_xml(arguments=f'/c "{target}"', enabled="false")}]}
        component = self.retirement(disabled)
        self.assertEqual(component["status"], "pass", component.get("failures"))
        self.assertIn("retired_program_referenced_by_disabled_task", {item["code"] for item in component["warnings"]})
        clean = {"status": "ok", "method": "fake", "tasks": [{"task_name": verifier.MIX_TASK_NAME, "task_path": "\\", "state": "Ready", "xml": _task_xml()}]}
        self.assertEqual(self.retirement(clean)["status"], "pass")

    def test_schedule_query_unavailable_blocks(self) -> None:
        component = self.retirement({"status": "unavailable", "method": "fake"})
        self.assertEqual(component["reason"], "retirement_schedule_query_unavailable")

    # -- data_retention_guard ------------------------------------------------

    def test_non_program_allowlist_entry_blocks(self) -> None:
        build_governance_fixture(self.root, entries=(*GOVERNANCE_ENTRIES, ("cowork", "tools\\settings.json", "retired_analysis")))
        component = self.retention()
        self.assertEqual(component["reason"], "data_retention_allowlist_non_program_entry")
        self.assertEqual(component["checks"]["allowlist_programs_only"]["non_program_entries"][0]["entry_id"], f"cowork-{len(GOVERNANCE_ENTRIES) + 1:03d}")

    def test_allowlist_entry_inside_protected_data_root_blocks(self) -> None:
        build_governance_fixture(self.root, entries=(*GOVERNANCE_ENTRIES, ("cowork", "data\\prices.py", "retired_analysis")))
        self.assertEqual(self.retention()["reason"], "data_retention_allowlist_entry_in_protected_root")

    def test_missing_or_empty_data_authority_root_blocks(self) -> None:
        (self.root / "program_data" / "mix").rmdir()
        self.assertEqual(self.retention()["reason"], "data_retention_data_root_empty_or_unreadable")
        (self.root / "program_data").rmdir()
        component = self.retention()
        self.assertEqual(component["reason"], "data_retention_data_root_missing")
        self.assertEqual(component["failures"][0]["root"], "program_data")

    def test_central_publication_not_intact_blocks(self) -> None:
        objects = sorted((self.root / "central" / "shared_market_data" / "_objects").iterdir())
        objects[0].write_bytes(b"tampered")
        component = self.retention()
        self.assertEqual(component["reason"], "data_retention_central_publication_not_intact")
        self.assertFalse(component["checks"]["central_publication"]["pass"])

    def test_legacy_data_roots_are_observed_labels_only(self) -> None:
        component = self.retention()
        self.assertEqual(component["status"], "pass")
        self.assertEqual(len([item for item in component["warnings"] if item["code"] == "legacy_data_root_absent"]), len(verifier.RETIRED_LEGACY_DATA_ROOTS))
        for root in self.config["legacy_data_roots"].values():  # type: ignore[union-attr]
            Path(root).mkdir(parents=True, exist_ok=True)
        component = self.retention()
        self.assertEqual(component["status"], "pass")
        self.assertEqual(component["warnings"], [])
        self.assertTrue(all(item["exists"] for item in component["checks"]["legacy_data_roots"].values()))


if __name__ == "__main__":
    unittest.main()
