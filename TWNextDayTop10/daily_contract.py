"""Bind only the live current C0 policy and verified shared runtime."""
from __future__ import annotations

import importlib
import importlib.util
import os
from pathlib import Path
import sys

DEFAULT_MODULE = Path(r"D:\TWStockData\services\market_update\daily_policy.py")
DEFAULT_CONTRACT_MODULE = Path(r"D:\stock\programs\shared\stock_daily_contract.py")
DEFAULT_C0_MODULE = Path(r"D:\stock\programs\shared\stock_daily_linear.py")
DEFAULT_READER_MODULE_DIR = Path(r"D:\stock\programs\single_stock_analysis\src")
# Only the routes the C0 H1 model actually consumes (app.py actual_model_inputs).
# Other policy routes (e.g. dividends) are recorded as version labels, not gates.
MODEL_ROUTES = ("finmind_price_adj", "official_market_daily", "official_calendar", "finmind_stock_info")
_REQUIRED_ROUTES = frozenset(MODEL_ROUTES)
_EXPECTED_REQUIREMENTS_SCHEMA = "tw-stock-daily-requirements-v2"
_RUNTIME_FIELDS = (
    "current_policy_binding", "original_model_feature_contract",
    "contract_adoption_ref", "effective_C0_semantics_projection_sha256", "model_activation_epoch",
)
_POLICY_KEYS = ("profile_id", "requirements_version", "feature_version",
                "policy_sha256", "requirements_raw_sha256")
_SHARED_MODULES = {}


def shared_module(path, name):
    path = Path(path).resolve()
    if not path.is_file():
        raise RuntimeError("affected_output:shared_module_unavailable:" + str(path))
    info = path.stat()
    key = (str(path), info.st_size, info.st_mtime_ns)
    if key in _SHARED_MODULES:
        return _SHARED_MODULES[key]
    if str(path.parent) not in sys.path:
        sys.path.append(str(path.parent))
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    previous = sys.modules.get(name)
    sys.modules[name] = module
    try:
        spec.loader.exec_module(module)
    except BaseException:
        if previous is None:
            sys.modules.pop(name, None)
        else:
            sys.modules[name] = previous
        raise
    _SHARED_MODULES[key] = module
    return module


def prepare_c0_reader():
    """A C0 process uses the canonical reader/query pair; conflicts fail closed."""
    directory = DEFAULT_READER_MODULE_DIR.resolve()
    for name in ("stock_data_sources", "stock_query_projection"):
        loaded = sys.modules.get(name)
        if loaded is not None and Path(loaded.__file__).resolve().parent != directory:
            raise RuntimeError("affected_output:C0_existing_reader_module_conflict:" + name)
    value = str(directory)
    if value in sys.path:
        sys.path.remove(value)
    sys.path.insert(0, value)
    for name in ("stock_data_sources", "stock_query_projection"):
        loaded = importlib.import_module(name)
        if Path(loaded.__file__).resolve().parent != directory:
            raise RuntimeError("affected_output:C0_reader_module_resolution_conflict:" + name)


def load_c0_model(path=None):
    prepare_c0_reader()
    contract = shared_module(DEFAULT_CONTRACT_MODULE, "top10_shared_daily_contract")
    active = contract.load_active_c0_model(path, consumer_id="top10")
    if not isinstance(active.get("version_labels"), list):
        active["version_labels"] = list(active.get("version_labels") or [])
    runtime_report_bindings(active, active["version_labels"])
    module = shared_module(DEFAULT_C0_MODULE, "top10_shared_daily_linear")
    return module, active["model"], Path(active["artifact_binding"]["path"]), active


def policy_module():
    path = Path(os.environ.get("STOCK_DAILY_POLICY_MODULE_PATH", DEFAULT_MODULE)).resolve()
    return shared_module(path, "top10_shared_daily_policy")


def note(labels, code, *, recorded=None, actual=None, detail=None):
    """Record a version/freshness difference instead of raising (plan 2.6)."""
    if labels is None:
        return None
    label = dict(code=code, recorded=recorded, actual=actual, detail=detail)
    if label not in labels:
        labels.append(label)
    return label


def version_warnings(labels):
    """Human-readable meta.warnings lines for version labels."""
    lines = []
    for label in labels or []:
        if not isinstance(label, dict):
            continue
        text = "版本標籤：" + str(label.get("code"))
        if label.get("recorded") is not None or label.get("actual") is not None:
            text += "（記錄：" + _short(label.get("recorded")) + "；實際：" + _short(label.get("actual")) + "）"
        if text not in lines:
            lines.append(text)
    return lines


def _short(value):
    text = "未提供" if value is None else str(value)
    return text if len(text) <= 120 else text[:117] + "..."


def _get(mapping, *keys):
    for key in keys:
        if not isinstance(mapping, dict):
            return None
        mapping = mapping.get(key)
    return mapping


def c0_policy_binding(*, requirements=None, labels=None):
    if requirements is None:
        module = policy_module()
        requirements = module.requirements_for(module.load_policy(), "C0", consumer_id="top10")
    if requirements.get("profile_id", "C0") != "C0":
        raise ValueError("affected_output:C0_current_policy_profile_mismatch")
    if not isinstance(requirements.get("schema"), str) or not requirements.get("schema"):
        # Plan gate 53: only an unparseable requirements schema blocks.
        raise ValueError("affected_output:current_daily_policy_schema_unparseable")
    if requirements.get("schema") != _EXPECTED_REQUIREMENTS_SCHEMA:
        note(labels, "current_daily_policy_schema_differs", recorded=_EXPECTED_REQUIREMENTS_SCHEMA,
             actual=requirements.get("schema"))
    required = list(requirements.get("required_routes") or [])
    optional = list(requirements.get("optional_routes") or [])
    missing = sorted(_REQUIRED_ROUTES - set(required) - set(optional))
    if missing:
        # The model cannot be fed without these routes; this is content, not version.
        raise ValueError("affected_output:current_C0_model_route_missing:" + ",".join(missing))
    # Extra policy routes (dividends) are not used by the model and are not labelled;
    # a model route the policy demotes to optional is shown.
    demoted = sorted(_REQUIRED_ROUTES - set(required))
    if demoted:
        note(labels, "current_C0_model_route_optional_in_policy", recorded=sorted(_REQUIRED_ROUTES),
             actual=dict(required=sorted(required), optional=sorted(optional)),
             detail=dict(model_routes=list(MODEL_ROUTES), demoted=demoted, analysed="model_routes_only"))
    return dict(
        profile_id="C0", requirements_version=requirements.get("requirements_version"),
        feature_version=requirements.get("feature_version"), policy_sha256=_get(requirements, "policy_binding", "sha256"),
        requirements_raw_sha256=_get(requirements, "requirements_binding", "raw_sha256"),
        policy_binding=requirements.get("policy_binding"), requirements_binding=requirements.get("requirements_binding"),
        consumer_id="top10", required_routes=required,
    )


def runtime_report_bindings(runtime, labels=None):
    """Carry the shared loader's actual new and original bindings without rewriting.

    A missing binding is shown as not provided (labelled); only a non-C0 profile raises.
    """
    result = {}
    for name in _RUNTIME_FIELDS:
        value = runtime.get(name)
        if value is None or value == "" or value == {}:
            note(labels, "C0_runtime_binding_missing", actual=None, detail=dict(field=name))
            value = None
        result[name] = value
    current = result["current_policy_binding"]
    if not isinstance(current, dict) or current.get("profile_id") != "C0":
        raise ValueError("affected_output:C0_current_policy_profile_mismatch")
    original = result["original_model_feature_contract"]
    if original is not None and (not isinstance(original, dict) or original.get("profile_id") != "C0"):
        raise ValueError("affected_output:C0_original_feature_contract_profile_mismatch")
    return result


def bind_daily_snapshot(checked, demand_context, asof, *, asof_by_market=None):
    """``asof_by_market`` is given only when markets were analysed on different days."""
    module = policy_module()
    policy = module.load_policy()
    requirements = module.requirements_for(policy, "C0", consumer_id="top10")
    labels = checked.setdefault("version_labels", [])
    before = len(labels)
    current = c0_policy_binding(requirements=requirements, labels=labels)
    expected = checked.get("current_policy_binding") or {}
    differing = [key for key in _POLICY_KEYS if current.get(key) != expected.get(key)]
    if differing:
        # The job continues with the data it read; the policy change is shown, not fatal.
        note(labels, "C0_current_policy_drift_during_job",
             recorded={key: expected.get(key) for key in differing},
             actual={key: current.get(key) for key in differing})
    warnings = checked.setdefault("warnings", [])
    for line in version_warnings(labels[before:]):
        if line not in warnings:
            warnings.append(line)
    bindings = runtime_report_bindings(checked, labels)
    descriptor = dict(checked["shared_input_descriptor"], analysis_asof=asof,
        model_identity=dict(demand_context["model_identity"]), **bindings)
    if asof_by_market:
        descriptor["analysis_asof_by_market"] = dict(asof_by_market)
    return dict(requirements=requirements, snapshot=module.bind_snapshot(policy, "C0", descriptor),
        effective_profile="C0", requirements_profile_id="C0", requirements_profile_scope="actual_model_input_policy",
        c0_model_compatible=True, version_labels=list(labels), **bindings)
