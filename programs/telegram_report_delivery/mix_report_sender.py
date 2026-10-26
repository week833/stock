"""Local validation and Telegram transport for the current MIX C0 report refs."""
from __future__ import annotations

import argparse
import hashlib
import json
import logging
import math
import os
import re
import sys
import time
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any, Mapping, Protocol

SUITE_DIR = Path(__file__).resolve().parent
SHARED_DIR = SUITE_DIR.parent / "shared"
for directory in (SUITE_DIR, SHARED_DIR):
    if str(directory) not in sys.path:
        sys.path.insert(0, str(directory))
import date_format as cd
import stock_daily_contract as contract
from telegram_report_sender import (
    MAX_TELEGRAM_DOCUMENT_BYTES, TelegramApiError, TelegramClient, TRUE_VALUES,
    ConfigurationError, load_env_file, setting, setup_logging, validate_credentials,
)

MIX_NAMESPACE_ROOT = Path(r"D:\TWStockData\program_data\mix")
MIX_REPORTS_ROOT = MIX_NAMESPACE_ROOT / "reports"
MIX_POINTER_PATH = MIX_REPORTS_ROOT / "LATEST_C0_RUN.json"
TELEGRAM_ROOT = Path(r"D:\TWStockData\services\telegram")
DEFAULT_ENV_PATH = SUITE_DIR / ".ENV"
DEFAULT_STATE_PATH = TELEGRAM_ROOT / "data" / "mix_report_delivery_state.json"
DEFAULT_LOCK_PATH = TELEGRAM_ROOT / "data" / "mix_report_delivery.lock"
DEFAULT_LOG_DIR = TELEGRAM_ROOT / "logs"
MIX_POINTER_SCHEMA = "mix-stock-daily-c0-current-report-v1"
MIX_MANIFEST_SCHEMA = "mix-stock-daily-c0-manifest-v1"
MIX_PAYLOAD_SCHEMA = "mix-stock-daily-c0-report-v1"
MIX_STATUS = "limited_baseline_effect_unverified"
HORIZONS = [1, 5, 20, 60]
# H1 model source recorded by MIX (pre-open model or the C0 fallback); H5/H20/H60 are always C0.
H1_MODEL_SOURCES = ("preopen", "c0")
ADOPTION_FIELDS = (
    "original_model_feature_contract", "current_policy_binding", "contract_adoption_ref",
    "effective_C0_semantics_projection_sha256", "model_activation_epoch",
)
RUNTIME_FIELDS = (
    "asof", "snapshot_fingerprint", "published_sources", "identity_binding", "calendar_binding",
    "calendar_verified_by_market", "application_scope_binding", "capture_scope_binding",
    "required_route_evidence", "readiness", "model_binding", "pointer_binding", "policy_binding",
    *ADOPTION_FIELDS, "original_effective_C0_semantics_projection_sha256", "contract_adoption_verified",
)
ARTIFACT_NAMES = {
    "payload": "report_payload.json", "html": "report.html",
    "input_sources": "published_inputs.json", "excel": "MIX_C0.xlsx",
}
# Telegram receives the summary and the Excel report only. The manifest, the full-market payload
# JSON (>100 MB) and the HTML (>49 MB) stay on disk; their refs are still verified before sending.
DELIVERED_ARTIFACT_KEYS = ("excel",)
SHA256_RE = re.compile(r"^[0-9a-f]{64}$")
# Plan default #6: ``--max-wait-seconds 0`` (the 08:00 schedule) waits at most 30 minutes, then exit 4.
ZERO_WAIT_CAP_SECONDS = 30 * 60
MIX_RUNTIME_POINTER_NAME = "LATEST_C0_RUN_BY_RUNTIME.json"
LOGGER = logging.getLogger("mix_report_sender")
_SENSITIVE_ASSIGNMENT_RE = re.compile(
    r"(?ix)(?P<key>['\"]?\b(?:telegram[_ -]?bot[_ -]?token|bot[_ -]?token|token|secret|password|api[_ -]?key|authorization|chat[_ -]?id|query(?:[_ -]?param)?)\b['\"]?\s*[:=]\s*)(?P<quote>['\"]?)(?P<value>[^\s,;&}\"']+)(?P=quote)"
)
_TELEGRAM_ENDPOINT_RE = re.compile(r"(?i)(?P<prefix>https?://api\.telegram\.org/bot)(?P<token>[^/\s]+)")
_URL_QUERY_RE = re.compile(r"(?P<base>https?://[^\s?#]+)\?[^\s#]+")


def _redact_sensitive(value: object) -> str:
    text = _TELEGRAM_ENDPOINT_RE.sub(r"\g<prefix>[REDACTED]", str(value))
    text = _URL_QUERY_RE.sub(r"\g<base>?[REDACTED]", text)
    return _SENSITIVE_ASSIGNMENT_RE.sub(lambda match: f"{match.group('key')}[REDACTED]", text)


class MixReportError(RuntimeError):
    pass


class MixReportDeferredError(MixReportError):
    """The MIX report (or the delivery environment) is not available yet; retried until the wait cap."""
    retryable = True


class MixReportRequestMismatchError(MixReportDeferredError):
    """An explicit operator request (``--expected-asof``) differs from the report; reported at once, never polled."""
    retryable = False


class MixReportIntegrityError(MixReportError):
    """A schema, ref or same-job binding is invalid."""


class MixTelegramSender(Protocol):
    def send_message(self, text: str) -> dict[str, Any]: ...
    def send_document(self, path: Path, caption: str) -> dict[str, Any]: ...


@dataclass(frozen=True)
class MixSenderPaths:
    namespace_root: Path = MIX_NAMESPACE_ROOT
    reports_root: Path = MIX_REPORTS_ROOT
    pointer_path: Path = MIX_POINTER_PATH
    telegram_root: Path = TELEGRAM_ROOT
    state_path: Path = DEFAULT_STATE_PATH
    lock_path: Path = DEFAULT_LOCK_PATH
    log_dir: Path = DEFAULT_LOG_DIR


@dataclass(frozen=True)
class MixDeliveryFile:
    path: Path
    label: str
    key: str
    size: int
    sha256: str


@dataclass(frozen=True)
class MixReportBundle:
    paths: MixSenderPaths
    pointer_path: Path
    manifest_path: Path
    payload_path: Path
    run_id: str
    expected_asof: str
    target: str
    status: str
    attachments: tuple[MixDeliveryFile, ...]
    verified_files: tuple[MixDeliveryFile, ...]
    runtime_binding: dict[str, Any]
    current_positive_pairs: tuple[tuple[str, str], ...]
    summary_text: str
    unmet_conditions: tuple[str, ...]
    fingerprint: str
    # Report-only part of the summary (no "目前" line); the dedup summary key is derived from it.
    summary_key_text: str = ""
    report_created_at: str = ""
    current_asof: str = ""
    version_labels: tuple[dict[str, Any], ...] = ()


def _identity(path: Path):
    stat = path.stat()
    return stat.st_dev, stat.st_ino, stat.st_size, stat.st_mtime_ns, stat.st_ctime_ns


def _sha256_file(path: Path) -> str:
    try:
        before = _identity(path)
        digest = hashlib.sha256()
        with path.open("rb") as handle:
            for chunk in iter(lambda: handle.read(4 * 1024 * 1024), b""):
                digest.update(chunk)
        if before != _identity(path):
            raise MixReportIntegrityError(f"檔案讀取中改變：{path}")
        return digest.hexdigest()
    except OSError as exc:
        raise MixReportIntegrityError(f"無法讀取檔案 SHA-256：{path}") from exc


def _unique_pairs(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise ValueError("duplicate JSON key: " + key)
        result[key] = value
    return result


def _read_json(path: Path, label: str) -> dict[str, Any]:
    try:
        before = _identity(path)
        raw = path.read_bytes()
        if before != _identity(path):
            raise MixReportIntegrityError(f"{label} 讀取中改變：{path}")
        value = json.loads(raw.decode("utf-8-sig"), object_pairs_hook=_unique_pairs,
            parse_constant=lambda value: (_ for _ in ()).throw(ValueError("nonfinite JSON: " + value)))
    except FileNotFoundError as exc:
        raise MixReportDeferredError(f"{label} 尚未產生：{path}") from exc
    except (OSError, UnicodeError, ValueError) as exc:
        raise MixReportIntegrityError(f"{label} 無法讀取或解析：{path}") from exc
    if not isinstance(value, dict):
        raise MixReportIntegrityError(f"{label} 頂層必須是 JSON object")
    return value


def _require_date(value: object, label: str) -> str:
    try:
        return cd.iso_day(value.strip() if isinstance(value, str) else value)
    except (TypeError, ValueError) as exc:
        raise MixReportIntegrityError(f"{label} 必須是有效 {cd.DATE_HELP}：{value!r}") from exc


def _same_date(value: object, expected: str) -> bool:
    return cd.same_day(value.strip() if isinstance(value, str) else value, expected)


def _require_sha(value: object, label: str) -> str:
    if not isinstance(value, str) or not SHA256_RE.fullmatch(value):
        raise MixReportIntegrityError(f"{label} 缺少有效 SHA-256")
    return value


def _inside(path: Path, root: Path) -> bool:
    try:
        path.resolve(strict=False).relative_to(root.resolve(strict=False))
        return True
    except ValueError:
        return False


def _file_record(path: Path, label: str) -> MixDeliveryFile:
    try:
        size = path.stat().st_size
        if not path.is_file() or size <= 0:
            raise MixReportIntegrityError(f"{label} 不是非空檔案")
    except FileNotFoundError as exc:
        raise MixReportDeferredError(f"{label} 尚未產生：{path}") from exc
    except OSError as exc:
        raise MixReportIntegrityError(f"{label} 無法取得檔案資訊：{path}") from exc
    digest = _sha256_file(path)
    return MixDeliveryFile(path, label, f"{label}:{digest}", size, digest)


def _verify_ref(value: object, *, label: str, root: Path, expected_name: str) -> MixDeliveryFile:
    if not isinstance(value, Mapping) or set(value) != {"path", "size", "sha256"}:
        raise MixReportIntegrityError(f"{label} ref 必須是 exact path/size/sha256")
    if not isinstance(value["path"], str) or not value["path"]:
        raise MixReportIntegrityError(f"{label}.path 缺失")
    path = Path(value["path"])
    if (not path.is_absolute() or not _inside(path, root)
            or path.resolve(strict=False) != (root / expected_name).resolve(strict=False)):
        raise MixReportIntegrityError(f"{label} 路徑不符 current run ref")
    if type(value["size"]) is not int or value["size"] <= 0:
        raise MixReportIntegrityError(f"{label}.size 必須是正整數")
    record = _file_record(path.resolve(strict=False), label)
    if record.size != value["size"] or record.sha256 != _require_sha(value["sha256"], label):
        raise MixReportIntegrityError(f"{label} 原始 bytes size/SHA-256 不一致")
    return record


def _canonical_digest(value) -> str:
    return hashlib.sha256(json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"),
        allow_nan=False, default=str).encode("utf-8")).hexdigest()


def _brief(value):
    """Small label value: scalars as-is, structures by canonical SHA-256."""
    if value is None or isinstance(value, (str, int, float, bool)):
        return value
    return {"sha256": _canonical_digest(value)}


def _label(labels, code, *, recorded=None, actual=None, detail=None):
    labels.append({"code": code, "recorded": recorded, "actual": actual, "detail": detail})


def _pair(stock_id, market):
    return str(stock_id or ""), str(market or "").upper()


def _capture_runtime():
    """The current published state, for display and labels only.

    The sender delivers a report the producer already wrote; the current
    model/head/identity never gates delivery (plan 2.6).  Any failure here is
    recorded as a label and the report is still sent.
    """
    labels: list[dict[str, Any]] = []
    current = {"available": False, "asof": None, "binding": None, "pairs": None, "readiness": None,
               "data_time": None, "version_labels": [], "labels": labels}
    try:
        active = contract.load_active_c0_model(consumer_id="mix")
        context = contract.load_c0_published_context(consumer_id="mix", runtime_binding=active)
        binding = contract.c0_runtime_binding(context, active, consumer_id="mix")
    except (OSError, ValueError, KeyError, TypeError) as exc:
        integrity = isinstance(exc, getattr(contract, "C0IntegrityError", ()))
        _label(labels, "current_runtime_integrity_error" if integrity else "current_runtime_unavailable",
            actual=_redact_sensitive(f"{type(exc).__name__}: {exc}")[:500],
            detail={"effect": "目前已發布資料狀態無法取得；照常寄出報告本身"})
        return current
    current.update(available=True, binding=binding, asof=binding.get("asof"), readiness=binding.get("readiness"),
        data_time=context.get("data_time"), version_labels=list(context.get("version_labels") or []))
    missing = [key for key in RUNTIME_FIELDS if key not in binding]
    if binding.get("schema") != "stock-daily-c0-runtime-binding-v2" or missing:
        _label(labels, "current_runtime_binding_incomplete", recorded="stock-daily-c0-runtime-binding-v2",
            actual=binding.get("schema"), detail={"missing_fields": missing})
    if (binding.get("contract_adoption_verified") is not True
            or binding.get("original_effective_C0_semantics_projection_sha256")
                != binding.get("effective_C0_semantics_projection_sha256")):
        _label(labels, "current_contract_adoption_unverified",
            recorded=binding.get("original_effective_C0_semantics_projection_sha256"),
            actual=binding.get("effective_C0_semantics_projection_sha256"),
            detail={"status": active.get("contract_adoption_status")})
    try:
        records = context["current_identity"]["records"]
        pairs = tuple(sorted(_pair(item["stock_id"], item["market"]) for item in records))
    except (KeyError, TypeError):
        pairs = ()
    if not pairs or any(market not in {"TWSE", "TPEX"} for _, market in pairs):
        _label(labels, "current_ordinary_identity_unavailable", actual=len(pairs),
            detail={"effect": "改用報告本身的普通股 coverage 核對"})
    else:
        current["pairs"] = pairs
    return current


def _require_equal(actual, expected, label):
    if actual != expected:
        raise MixReportIntegrityError(label + " 與同一 run 的紀錄不一致")


def _present(document, field):
    return isinstance(document, Mapping) and field in document and document[field] not in (None, "", {}, [])


def _report_field(document, field, label, labels):
    """A report field used for display/comparison only: missing -> warning label, never a gate."""
    if _present(document, field):
        return document[field]
    _label(labels, "report_field_missing", actual=None, detail={"field": label + "." + field})
    return None


def _header(document, schema, label, *, run_id=None, asof=None):
    if (document.get("schema") != schema or document.get("profile") != "C0"
            or document.get("status") != MIX_STATUS or document.get("horizons") != HORIZONS
            or any(type(h) is not int for h in document.get("horizons", []))):
        raise MixReportIntegrityError(label + " 不是 current MIX C0 schema/status/H")
    if run_id is not None:
        _require_equal(document.get("run_id"), run_id, label + ".run_id")
    if asof is not None and not _same_date(document.get("as_of_date"), asof):
        raise MixReportIntegrityError(label + ".as_of_date 不一致")


def _verify_bindings(manifest, payload, runtime, report_labels, current_labels):
    """Same-run consistency is KEEP (raise); differences from the current runtime are labels.

    ``runtime`` is the current binding, or None when it could not be read.
    """
    def differs(code, field, recorded, actual):
        if runtime is not None and recorded is not None and actual is not None and recorded != actual:
            _label(current_labels, code, recorded=_brief(recorded), actual=_brief(actual), detail={"field": field})

    for field in ADOPTION_FIELDS:
        bound = _report_field(manifest, field, "manifest", report_labels)
        reported = _report_field(payload, field, "payload", report_labels)
        if bound is not None and reported is not None:
            _require_equal(bound, reported, "manifest/payload." + field)
        differs("report_binding_differs_from_current", field, reported if reported is not None else bound,
            None if runtime is None else runtime.get(field))
    models = {}
    for document, label in ((manifest, "manifest"), (payload, "payload")):
        model = _report_field(document, "model", label, report_labels)
        models[label] = model if isinstance(model, Mapping) else {}
    for field, runtime_field in (("artifact_binding", "model_binding"), ("pointer_binding", "pointer_binding")):
        bound = _report_field(models["manifest"], field, "manifest.model", report_labels) if models["manifest"] else None
        reported = _report_field(models["payload"], field, "payload.model", report_labels) if models["payload"] else None
        if bound is not None and reported is not None:
            _require_equal(bound, reported, "manifest/payload.model." + field)
        differs("model_differs_from_current", "model." + field, reported if reported is not None else bound,
            None if runtime is None else runtime.get(runtime_field))
    differs("model_policy_differs_from_current", "model_policy_binding",
        _report_field(payload, "model_policy_binding", "payload", report_labels),
        None if runtime is None else runtime.get("policy_binding"))
    published = _report_field(payload, "published_reader_binding", "payload", report_labels)
    if isinstance(published, Mapping):
        differs("published_snapshot_differs_from_current", "published_reader_binding.snapshot_fingerprint",
            _report_field(published, "snapshot_fingerprint", "payload.published_reader_binding", report_labels),
            None if runtime is None else runtime.get("snapshot_fingerprint"))
        differs("published_sources_differ_from_current", "published_reader_binding.sources",
            _report_field(published, "sources", "payload.published_reader_binding", report_labels),
            None if runtime is None else runtime.get("published_sources"))
    requirements = _report_field(payload, "daily_requirements", "payload", report_labels)
    bound_policy = (_report_field(requirements, "policy_binding", "payload.daily_requirements", report_labels)
        if isinstance(requirements, Mapping) else None)
    current_policy = (runtime or {}).get("current_policy_binding") or {}
    if isinstance(bound_policy, Mapping):
        for field in ("profile_id", "requirements_version", "feature_version", "policy_sha256", "requirements_raw_sha256"):
            differs("requirements_policy_differs_from_current", "daily_requirements.policy_binding." + field,
                bound_policy.get(field), current_policy.get(field))
    if (_present(manifest, "requirements_version") and isinstance(requirements, Mapping)
            and _present(requirements, "requirements_version")):
        _require_equal(manifest["requirements_version"], requirements["requirements_version"], "manifest.requirements_version")
    if _present(manifest, "requirements_version"):
        differs("requirements_version_differs_from_current", "manifest.requirements_version",
            manifest["requirements_version"], current_policy.get("requirements_version"))
    # KEEP: the same run's manifest and payload must record the same forward binding.
    _require_equal(manifest.get("forward_binding"), payload.get("forward_binding"), "forward_binding")
    if not isinstance(payload.get("forward_binding"), Mapping) or not payload["forward_binding"].get("base_model_version"):
        _label(report_labels, "report_field_missing", detail={"field": "payload.forward_binding.base_model_version"})


def _report_ordinary_identity(payload, report_labels):
    """The report's own verified identity (coverage): ordinary pairs, and pairs it excluded or marked non-ordinary."""
    coverage = payload.get("coverage")
    records = coverage.get("records") if isinstance(coverage, Mapping) else None
    if not isinstance(records, list) or not records:
        _label(report_labels, "report_field_missing", detail={"field": "payload.coverage.records"})
        return None, set(), set()
    ordinary, non_ordinary = set(), set()
    for item in records:
        if not isinstance(item, Mapping):
            raise MixReportIntegrityError("coverage.records 必須是 object")
        pair = _pair(item.get("stock_id"), item.get("market") or item.get("board"))
        # KEEP (ETF / non-ordinary exclusion): ordinary only with positive classification evidence.
        if (item.get("is_ordinary_share") is True and item.get("security_type") == "ordinary_share"
                and item.get("current_ordinary_classification_verified") is not False
                and pair[1] in {"TWSE", "TPEX"} and pair[0]):
            ordinary.add(pair)
        else:
            non_ordinary.add(pair)
    excluded = coverage.get("excluded") if isinstance(coverage.get("excluded"), list) else []
    excluded_pairs = {_pair(item.get("stock_id"), item.get("market")) for item in excluded if isinstance(item, Mapping)}
    return ordinary, non_ordinary, excluded_pairs


def _verify_predictions(manifest, payload, current_pairs, target, report_labels, current_labels):
    records = payload.get("predictions")
    if not isinstance(records, list) or not records:
        raise MixReportDeferredError("MIX current report 沒有 prediction rows")
    seen, usable = set(), 0
    universe = payload.get("universe_policy")
    if (not isinstance(universe, Mapping)
            or universe.get("selection") not in {"current_verified_ordinary_twse_tpex", "explicit_current_ordinary_filter"}
            or universe.get("training_scope_is_inference_allowlist") is not False):
        raise MixReportIntegrityError("MIX current ordinary universe contract 不符")
    ordinary, non_ordinary, excluded_pairs = _report_ordinary_identity(payload, report_labels)
    allowed_now = set(current_pairs) if current_pairs is not None else None
    not_current = []
    for record in records:
        if not isinstance(record, Mapping):
            raise MixReportIntegrityError("prediction 必須是 object")
        pair = _pair(record.get("stock_id", ""), record.get("market", ""))
        horizon = record.get("horizon")
        key = (*pair, horizon)
        if type(horizon) is not int or horizon not in HORIZONS or key in seen or not pair[0]:
            raise MixReportIntegrityError("prediction pair/H 重複或不合法")
        # KEEP (ETF / non-ordinary exclusion): checked against the report's own verified identity.
        if pair in non_ordinary:
            raise MixReportIntegrityError("prediction 含報告自身標為非普通股的標的：" + "/".join(pair))
        if ordinary is not None and pair not in ordinary and not (
                pair in excluded_pairs and record.get("prediction_status") != "predicted"):
            raise MixReportIntegrityError("prediction pair 不在報告自身的普通股 coverage：" + "/".join(pair))
        if ordinary is None:
            # KEEP: without the report's own classification evidence, fall back to the current
            # verified ordinary identity; with neither, ETF exclusion cannot be shown -> fail closed.
            if allowed_now is None:
                raise MixReportIntegrityError(
                    "報告缺 coverage.records 且目前普通股名單無法取得，無法確認 ETF／非普通股已排除")
            if pair not in allowed_now:
                raise MixReportIntegrityError("prediction pair 無報告自身普通股證據且不在目前普通股名單：" + "/".join(pair))
        if allowed_now is not None and pair not in allowed_now and horizon == HORIZONS[0]:
            not_current.append("/".join(pair))
        seen.add(key)
        if not isinstance(record.get("prediction_status"), str) or not record["prediction_status"]:
            raise MixReportIntegrityError("prediction_status 缺失")
        if record["prediction_status"] == "predicted":
            usable += 1
            for field in ("prob_up", "expected_return"):
                value = record.get(field)
                if type(value) not in (int, float) or not math.isfinite(value):
                    raise MixReportIntegrityError("predicted row 缺少有限值：" + field)
            if not 0 <= record["prob_up"] <= 1:
                raise MixReportIntegrityError("prob_up 不在 0..1")
        if target and horizon == 1 and not _same_date(record.get("target_session_date"), target):
            raise MixReportIntegrityError("explicit --target 缺少 matching verified H1 endpoint")
    if not_current:
        _label(current_labels, "prediction_pair_not_in_current_identity", actual=len(not_current),
            detail={"examples": sorted(not_current)[:20], "effect": "報告產生後普通股名單已變動；照常寄出"})
    if {key[2] for key in seen} != set(HORIZONS):
        raise MixReportIntegrityError("MIX report rows 沒有 exact H1/5/20/60")
    for document, label in ((manifest, "manifest"), (payload, "payload")):
        if (type(document.get("prediction_count")) is not int or document["prediction_count"] != len(records)
                or type(document.get("usable_prediction_count")) is not int
                or document["usable_prediction_count"] != usable):
            raise MixReportIntegrityError(label + " prediction counts 不一致")
    if usable < 1:
        raise MixReportDeferredError("MIX current report 沒有 usable prediction")
    for field in ("fit_performed", "backtests_performed", "stock_order_is_ranking"):
        if payload.get(field) is not False:
            raise MixReportIntegrityError("MIX current limitations 不符：" + field)
    model = payload.get("model") if isinstance(payload.get("model"), Mapping) else {}
    if model.get("formal_model_qualified") is not False or manifest.get("formal_model_qualified") is not False:
        raise MixReportIntegrityError("MIX C0 不得宣稱 formal model qualified")
    if any(document.get("delivery_status") != "not_requested" for document in (manifest, payload)):
        raise MixReportIntegrityError("producer delivery_status 不符")


def _h1_summary_lines(manifest, payload, asof) -> tuple[str, ...]:
    """H1 model-source lines for the summary (report-only, so part of the dedup text).

    Reports written before MIX recorded ``h1_model_source`` get no line, so their summary
    text -- and therefore an existing delivery's dedup key -- is unchanged.  Same-run
    consistency (manifest/payload source, pre-open rows only on H1, fresh signal day) is KEEP.
    """
    if "h1_model_source" not in payload and "h1_model_source" not in manifest:
        return ()
    source = payload.get("h1_model_source")
    if source not in H1_MODEL_SOURCES or manifest.get("h1_model_source") != source:
        raise MixReportIntegrityError("MIX H1 模型來源不合法或 manifest/payload 不一致")
    preopen_rows = [record for record in payload.get("predictions") or []
                    if isinstance(record, Mapping) and record.get("model_source") == "preopen"]
    if any(record.get("horizon") != 1 for record in preopen_rows) or (source == "c0" and preopen_rows):
        raise MixReportIntegrityError("MIX 只有 H1 可使用開盤前版")
    info = payload.get("h1_preopen") if isinstance(payload.get("h1_preopen"), Mapping) else {}
    if source == "c0":
        if payload.get("h1_model_source_requested") == "c0":
            return ("H1：C0 有限基準（依設定使用 C0）",)
        # 「代碼：中文」(the same wording MIX prints); changing this text changes the summary's dedup key.
        return (f"H1：開盤前版不可用（{_h1_reason_text(info.get('fallback_reason'))}），本次 H1 改用 C0 基準",)
    meta = payload.get("preopen_meta") if isinstance(payload.get("preopen_meta"), Mapping) else {}
    if not preopen_rows or not _same_date(meta.get("signal_date"), asof):
        raise MixReportIntegrityError("MIX 開盤前版訊號日與報告資料日不一致")
    gate = meta.get("gate_us_up")
    gate_text = "前一晚美股收漲" if gate is True else "前一晚美股未收漲，非建議日" if gate is False else "前一晚美股漲跌未知"
    top = [item for item in info.get("top10") or [] if isinstance(item, Mapping)][:10]
    names = "、".join(
        str(item.get("stock_id")) + (" " + item["stock_name"].strip()
                                     if isinstance(item.get("stock_name"), str) and item["stock_name"].strip() else "")
        for item in top) or "無"
    lines = [f"H1：開盤前版（訊號日 {cd.display_day(meta.get('signal_date'))} → 目標日 "
             f"{cd.display_day(meta.get('target_date'))}；{gate_text}）",
             f"H1 可交易 Top10（依上漲機率→預估報酬）：{names}",
             "H5／H20／H60：C0 有限基準"]
    disclosure = info.get("disclosure") if isinstance(info.get("disclosure"), Mapping) else {}
    if isinstance(disclosure.get("summary"), str) and disclosure["summary"].strip():
        lines.append(_display_disclosure(disclosure["summary"].strip())[:400])
    return tuple(lines)


_HOLDOUT_MONTHS_RE = re.compile(r"(?<!\d)(\d{4})-(\d{2})～(?:\1-)?(\d{2})(?!\d)")


def _display_disclosure(text: str) -> str:
    """Disclosure sentence for people: ISO dates -> YYYYMMDD and「2026-01～10」->「2026 年 1～10 月」(as MIX writes now)."""
    text = _HOLDOUT_MONTHS_RE.sub(lambda match: f"{int(match.group(1))} 年 {int(match.group(2))}～{int(match.group(3))} 月", text)
    return str(cd.display_text(text))


def _h1_reason_text(code: object) -> str:
    """「代碼：中文」for an H1 fallback reason (mirrors mixstock.preopen.reason_text without importing MIX)."""
    if not isinstance(code, str) or not code:
        return "原因未記錄"
    return f"{code}：{_HUMAN_LABELS.get(code, '其他原因，詳見原始資料')}"


def load_mix_bundle(paths: MixSenderPaths = MixSenderPaths(), *, expected_asof="", target="") -> MixReportBundle:
    pointer_path = paths.pointer_path.resolve(strict=False)
    if not _inside(pointer_path, paths.reports_root):
        raise MixReportIntegrityError("MIX current pointer 超出 reports namespace")
    pointer_record = _file_record(pointer_path, "MIX C0 current pointer")
    pointer = _read_json(pointer_path, "MIX C0 current pointer")
    _header(pointer, MIX_POINTER_SCHEMA, "pointer")
    run_id = pointer.get("run_id")
    if not isinstance(run_id, str) or not re.fullmatch(r"c0_[0-9]{8}T[0-9]{12}Z_[0-9a-f]{10}", run_id):
        raise MixReportIntegrityError("MIX C0 run_id 不符 immutable run namespace")
    asof = _require_date(pointer.get("as_of_date"), "pointer.as_of_date")
    if expected_asof and not _same_date(expected_asof, asof):
        # KEEP (manual opt-in), but report at once with the actual data day instead of polling.
        raise MixReportRequestMismatchError(
            f"--expected-asof {cd.display_day(expected_asof)} 與 MIX 報告資料日 {cd.display_day(asof)} 不符；"
            "未寄出（不等待）。不指定 --expected-asof 時會寄出此報告並標示資料日。")
    requested_target = _require_date(target, "--target") if target else ""
    root = paths.reports_root.resolve(strict=False) / "c0" / run_id
    manifest_record = _verify_ref(pointer.get("manifest"), label="MIX C0 manifest", root=root, expected_name="manifest.json")
    manifest = _read_json(manifest_record.path, "MIX C0 manifest")
    _header(manifest, MIX_MANIFEST_SCHEMA, "manifest", run_id=run_id, asof=asof)
    report_labels: list[dict[str, Any]] = []
    refs = manifest.get("artifacts")
    if not isinstance(refs, Mapping) or not isinstance(refs.get("payload"), Mapping):
        raise MixReportDeferredError("MIX C0 manifest 尚無 payload ref")
    unknown = set(refs) - set(ARTIFACT_NAMES)
    if unknown:
        raise MixReportIntegrityError("MIX C0 manifest 含未知 artifact ref：" + ",".join(sorted(unknown)))
    for key in ARTIFACT_NAMES:
        if key not in refs:
            _label(report_labels, "report_artifact_missing", detail={"artifact": key, "effect": "照常寄出摘要與其他報告"})
    artifacts = {key: _verify_ref(refs[key], label="MIX C0 " + key, root=root, expected_name=name)
        for key, name in ARTIFACT_NAMES.items() if key in refs}
    payload = _read_json(artifacts["payload"].path, "MIX C0 payload")
    _header(payload, MIX_PAYLOAD_SCHEMA, "payload", run_id=run_id, asof=asof)
    current = _capture_runtime()
    current_labels = current["labels"]
    runtime, current_pairs = current["binding"], current["pairs"]
    current_asof = ""
    if current["available"] and current["asof"]:
        try:
            current_asof = cd.iso_day(current["asof"])
        except (TypeError, ValueError):
            _label(current_labels, "current_asof_invalid", actual=str(current["asof"]))
        if current_asof and current_asof != asof:
            # The report is still sent; the summary shows both days (plan 2.6, default #6).
            _label(current_labels, "newer_data_available" if current_asof > asof else "current_data_older_than_report",
                recorded=asof, actual=current_asof)
    _runtime_pointer_note(paths, run_id, current_labels)
    _verify_bindings(manifest, payload, runtime, report_labels, current_labels)
    if "input_sources" in refs:
        _require_equal(payload.get("input_sources"), refs["input_sources"], "payload.input_sources ref")
    for field in ("source_binding", "coverage", "runtime_evidence"):
        if not isinstance(payload.get(field), Mapping) or not payload[field]:
            _label(report_labels, "report_field_missing", detail={"field": "payload." + field})
    _verify_predictions(manifest, payload, current_pairs, requested_target, report_labels, current_labels)
    h1_lines = _h1_summary_lines(manifest, payload, asof)
    # Reports without h1_model_source keep the original title (and so their dedup text).
    title = ("MIX 台股每日估計（H1 開盤前版＋H5／H20／H60 C0 有限基準）"
             if payload.get("h1_model_source") == "preopen" else "MIX C0 有限基準")
    created_at = payload.get("created_at") or manifest.get("created_at") or pointer.get("created_at") or ""
    data_time = payload.get("data_time") if isinstance(payload.get("data_time"), Mapping) else None
    if data_time is None:
        _label(report_labels, "report_field_missing", detail={"field": "payload.data_time"})
    if not created_at:
        _label(report_labels, "report_field_missing", detail={"field": "payload.created_at"})
    warnings = ["effectiveness_unknown", "historical_PIT_unverified", "historical_membership_unverified",
        "corporate_actions_unverified", "trading_effect_unverified"]
    source_binding = payload.get("source_binding") if isinstance(payload.get("source_binding"), Mapping) else {}
    statuses = source_binding.get("route_statuses", {})
    if isinstance(statuses, Mapping):
        warnings.extend(f"{key}={value}" for key, value in statuses.items() if str(value).upper() != "COMPLETE")
    # Readiness as recorded when the report was produced (the current state is a separate "目前" line).
    report_readiness = source_binding.get("readiness")
    if isinstance(report_readiness, Mapping) and report_readiness.get("ready") is not True:
        warnings.append("six_route_readiness=" + str(report_readiness.get("status", "unknown")) + "（報告產生時）")
    produced_codes = _label_codes(payload.get("version_labels"),
        (payload.get("forward_binding") or {}).get("version_labels") if isinstance(payload.get("forward_binding"), Mapping) else None)
    if produced_codes:
        warnings.append("報告版本標示=" + "、".join(produced_codes))
    sender_codes = _label_codes(report_labels)
    if sender_codes:
        warnings.append("報告欄位標示=" + "、".join(sender_codes))
    warnings = tuple(_redact_sensitive(item) for item in warnings)
    head = (data_time or {}).get("head") if isinstance((data_time or {}).get("head"), Mapping) else {}
    stable = "\n".join((
        title,
        f"報告資料日：{cd.display_day(asof)}",
        f"報告產生時間：{cd.display_clock(created_at, empty='未提供')}",
        f"資料發布時間（producer 宣告，未驗證）：{cd.display_clock(head.get('published_at'), empty='未提供')}",
        f"報告產生時是否為最新資料：{_report_freshness_text(data_time, localized=False)}",
        "H=1/5/20/60 同市場交易日收盤",
        f"可用列：{payload.get('usable_prediction_count')}/{payload.get('prediction_count')}；依代號列示，非排序",
        *h1_lines,
        "效果、歷史PIT、公司行動與交易效益未驗證；目標日期空值與缺值狀態維持原報告。",
        cd.display_text("；".join(warnings))))
    display_stable = "\n".join((
        title,
        f"報告資料日：{cd.display_day(asof)}",
        f"報告產生時間：{cd.display_clock(created_at, empty='未提供')}",
        f"資料發布時間（來源宣告，未驗證）：{cd.display_clock(head.get('published_at'), empty='未提供')}",
        f"報告產生時是否為最新資料：{_report_freshness_text(data_time)}",
        "估計期間：1／5／20／60 個同市場交易日收盤",
        f"可用估計：{payload.get('usable_prediction_count')}/{payload.get('prediction_count')} 列；依代號列示，並非推薦排名",
        *h1_lines,
        "效果、歷史時點資料、公司行動與交易效益未驗證；目標日期空值、缺值及校準未確認狀態維持原報告。",
        "；".join(dict.fromkeys(_human_warning(item) for item in warnings))))
    # The historical summary text remains the dedup identity; display translations never resend it.
    summary = display_stable + "\n" + _current_summary_line(asof, current, current_asof)
    # Every ref above is still verified locally; only the readable report itself is uploaded.
    attachments = tuple(artifacts[key] for key in DELIVERED_ARTIFACT_KEYS if key in artifacts)
    if any(item.size > MAX_TELEGRAM_DOCUMENT_BYTES for item in attachments):
        raise MixReportIntegrityError("MIX C0 attachment 超過 Telegram document size limit")
    files = (pointer_record, manifest_record, *artifacts.values())
    # Dedup identity: run_id + the run's artifact refs only (no runtime binding, no pointer bytes).
    fingerprint = _canonical_digest({"schema": MIX_PAYLOAD_SCHEMA, "run_id": run_id,
        "refs": [(str(item.path), item.size, item.sha256) for item in (manifest_record, *artifacts.values())]})
    unmet = warnings + tuple(_redact_sensitive("目前:" + code) for code in _label_codes(current_labels))
    bundle = MixReportBundle(paths, pointer_path, manifest_record.path, artifacts["payload"].path,
        run_id, asof, requested_target, MIX_STATUS, attachments, files, runtime, tuple(current_pairs or ()),
        summary, unmet, fingerprint, summary_key_text=stable, report_created_at=str(created_at),
        current_asof=current_asof, version_labels=tuple(report_labels + current_labels))
    _verify_file_readbacks(bundle)
    return bundle


def _label_codes(*groups) -> list[str]:
    codes = []
    for group in groups:
        for item in group if isinstance(group, (list, tuple)) else []:
            code = item.get("code") if isinstance(item, Mapping) else None
            if isinstance(code, str) and code and code not in codes:
                codes.append(code)
    return codes


_HUMAN_LABELS = {
    "effectiveness_unknown": "效果未驗證", "historical_PIT_unverified": "歷史時點資料未驗證",
    "historical_membership_unverified": "歷史股票池未驗證", "corporate_actions_unverified": "公司行動未驗證",
    "trading_effect_unverified": "交易效果未驗證", "unknown": "未確認",
    "COMPLETE": "完整", "SUCCESS": "完成", "PARTIAL": "部分完成", "BLOCKED": "受阻",
    "FAILED": "失敗", "ERROR": "錯誤", "WAITING_QUOTA": "等待來源額度",
    "PENDING_SOURCE": "等待來源確認", "READY": "已就緒", "NOT_READY": "尚未就緒",
    "MISSING": "資料缺漏", "NO_DATA": "尚無資料", "INCOMPLETE": "資料未齊",
    "limited_baseline_effect_unverified": "有限基準，效果未驗證",
    "binding_missing": "部分來源或模型資訊未提供", "policy_drift": "資料需求與模型紀錄不同",
    "requested_asof_differs": "實際資料日與要求不同", "requested_target_differs": "實際目標日與要求不同",
    "report_field_missing": "報告缺少部分資訊", "report_artifact_missing": "部分報告附件未提供",
    "report_binding_differs_from_current": "報告與目前資料或模型資訊不同",
    "inference_code_differs_from_training": "推論程式與訓練版本不同",
    "mutable_ref_changed_since_adoption": "資料需求宣告自採用後已變更",
    "contract_adoption_unverified": "需求採用狀態未驗證",
    "current_contract_adoption_unverified": "目前需求採用狀態未驗證",
    "current_runtime_integrity_error": "目前已發布資料的完整性狀態異常",
    "current_runtime_unavailable": "目前已發布資料暫時無法讀取",
    "current_runtime_binding_incomplete": "目前資料與模型資訊不完整",
    "current_ordinary_identity_unavailable": "目前普通股名冊無法確認",
    "current_asof_invalid": "目前資料日無法辨識", "newer_data_available": "已有較新資料",
    "current_data_older_than_report": "目前已發布資料比本報告舊",
    "prediction_pair_not_in_current_identity": "部分報告股票已不在目前普通股名冊",
    "runtime_pointer_unreadable": "目前報告索引無法讀取", "newer_run_not_selected": "另有較新報告尚未採用",
    "current_runtime_changed_before_upload": "傳送前資料或模型資訊已更新",
    "current_identity_changed_before_upload": "傳送前普通股名冊已更新",
    "no_usable_predictions": "沒有可用估計", "report_model_differs_from_current": "報告與目前模型不同",
    "source_current_keys_missing": "來源尚缺必要資料", "current_price_sessions_incomplete": "當前價格交易日資料未齊",
    "complete_receipt_corrupt_reopened": "先前交付紀錄不完整，已恢復待交付狀態",
    # preopen_*／h1_* codes: copied from D:\MIX All\src\mixstock\preopen.py DISPLAY_TEXT (2026-10-18 繁體中文修正版);
    # kept as a copy on purpose (no MIX import here) -- update both places together.
    "preopen_no_latest": "找不到開盤前版最新快照",
    "preopen_schema_mismatch": "開盤前版快照格式不符",
    "preopen_stale": "開盤前版快照不是本次資料日，已過期",
    "preopen_snapshot_missing": "開盤前版快照檔缺漏",
    "preopen_snapshot_sha_mismatch": "開盤前版快照雜湊不符",
    "preopen_snapshot_columns": "開盤前版快照欄位不足",
    "preopen_snapshot_values": "開盤前版快照數值無效",
    "preopen_snapshot_duplicate_rows": "開盤前版快照股票重複",
    "preopen_us_status_invalid": "開盤前版美股狀態無效",
    "preopen_meta_invalid": "開盤前版中繼資料無效",
    "preopen_parquet_engine_missing": "缺少讀取開盤前版快照的套件 pyarrow",
    "preopen_reader_unavailable": "開盤前版讀取程式不可用",
    "preopen_reader_error": "開盤前版讀取失敗",
    "preopen_source_not_allowed": "開盤前版來源路徑不允許",
    "preopen_no_matching_rows": "本次股票都不在開盤前版快照",
    "preopen_target_mismatch": "開盤前版目標日與已驗證交易日曆的下一交易日不符",
    "h1_model_source_c0_requested": "依設定強制使用 C0",
    "h1_preopen_unavailable": "開盤前版不可用，H1 改用 C0 基準",
    "h1_preopen_rows_missing": "部分股票無開盤前版估計，該股 H1 改用 C0",
    "h1_preopen_us_overnight_missing": "前一晚美股收盤缺漏，開盤前版準確度可能下降",
    "h1_preopen_market_asof_differs": "各市場資料日不同，開盤前版以訊號日為準",
    "h1_preopen_root_override": "開盤前版讀取非正式路徑（測試或 QA）",
    "h1_preopen_rank_inconsistent": "開盤前版可交易名次與排序不一致",
    "preopen_calibrated": "開盤前版校準後機率",
    "preopen_holdout_evaluated": "開盤前版保留期已評估（2026 年 1～10 月）",
    "preopen_open_entry_negative": "隔日開盤買進 Top10 平均報酬為負",
    # 2026-10-21 配方 v2（h1pre_v2_ens）與未知配方的代碼，文字同 MIX DISPLAY_TEXT；v1 的代碼與文字不變。
    "preopen_open_entry_near_zero": "隔日開盤買進 Top10 平均報酬約為零（保留期約 +0.01%／日）",
    "preopen_open_entry_unknown_recipe": "開盤前版配方未知，隔日開盤買進報酬未評估",
    "preopen_holdout_unknown_recipe": "開盤前版配方未知，沒有保留期評估",
    "preopen_hgb": "開盤前版 HGB",
    "preopen_declared": "開盤前版宣告目標日",
    "preopen_row_missing": "開盤前版快照無此股票",
}
_HUMAN_ROUTES = {
    "official_calendar": "官方交易日曆", "official_market_daily": "官方日行情",
    "finmind_stock_info": "股票基本資料", "finmind_price_adj": "調整股價",
    "finmind_dividend": "股利政策", "finmind_dividend_result": "除權息結果",
}


def _human_label(value) -> str:
    text = str(value or "unknown")
    return _HUMAN_LABELS.get(text, _HUMAN_LABELS.get(text.upper(), "其他資料或版本狀態（詳見原始資料）"))


def _human_warning(value) -> str:
    """Translate only the human summary; raw warnings and receipt identities stay unchanged."""
    text = str(value)
    key, separator, detail = text.partition("=")
    if key in _HUMAN_ROUTES and separator:
        return _HUMAN_ROUTES[key] + "：" + _human_label(detail)
    if key == "six_route_readiness" and separator:
        return "六項資料可用狀態：" + _human_label(detail.removesuffix("（報告產生時）")) + "（報告產生時）"
    if key in {"報告版本標示", "報告欄位標示"} and separator:
        return key + "：" + "、".join(dict.fromkeys(_human_label(code) for code in detail.split("、")))
    return _human_label(text)


def _human_market(value) -> str:
    return {"TWSE": "上市", "TPEX": "上櫃", "TPEx": "上櫃"}.get(str(value), "市場未確認")


def _attachment_caption(attachment: MixDeliveryFile, bundle: MixReportBundle) -> str:
    titles = {"manifest.json": "MIX 報告索引", "report_payload.json": "MIX 原始分析資料（JSON）",
              "report.html": "MIX 繁體中文網頁報告", "MIX_C0.xlsx": "MIX 繁體中文試算表"}
    return (titles.get(attachment.path.name, "MIX 報告附件") +
        f"\n報告資料日：{cd.display_day(bundle.expected_asof)}"
        f"\n報告產生時間：{cd.display_clock(bundle.report_created_at, empty='未提供')}"
        "\n估計期間：1／5／20／60 個同市場交易日收盤"
        "\n效益與歷史時點資料未驗證；部分完成、缺值、校準未確認狀態維持原報告。"
        "\n完整來源識別保留於本機報告索引。")


def _report_freshness_text(data_time, *, localized=True) -> str:
    """The report's own data-time verdict (core_daily ``data_time``), as recorded when it was produced."""
    if not isinstance(data_time, Mapping) or not data_time.get("freshness_status"):
        return "未提供"
    status = data_time["freshness_status"]
    if status == "stale":
        stale = data_time.get("stale_markets") if isinstance(data_time.get("stale_markets"), Mapping) else {}
        parts = [f"{_human_market(market) if localized else market} {cd.display_day(days[1])}" for market, days in sorted(stale.items())
            if isinstance(days, (list, tuple)) and len(days) > 1]
        return "否：已有較新交易日 " + ("、".join(parts) or "（未列出）")
    if status == "calendar_unverified_after":
        weekdays = data_time.get("weekdays_after_calendar")
        weekdays = weekdays if isinstance(weekdays, (list, tuple)) else []
        through = data_time.get("calendar_verified_through") or data_time.get("calendar_axis_end")
        return (f"無法確認：已驗證交易日曆只到 {cd.display_day(through)}，之後有 {len(weekdays)} 個平日"
                "尚未納入（可能含休市日）")
    if status == "latest":
        return "是（依已驗證交易日曆）"
    return _human_label(status) if localized else str(status)


def _current_summary_line(asof: str, current: Mapping[str, Any], current_asof: str) -> str:
    """The "目前" part of the summary; excluded from the dedup key because it changes over time."""
    if not current.get("available"):
        codes = "、".join(dict.fromkeys(_human_label(code) for code in _label_codes(current.get("labels")))) or "原因未確認"
        return f"目前：無法取得目前已發布資料狀態（{codes}）；報告資料日 {cd.display_day(asof)}"
    lines = []
    if current_asof and current_asof != asof:
        lines.append(f"目前：報告資料日 {cd.display_day(asof)}；目前最新資料日 {cd.display_day(current_asof)}"
            + ("（本報告不是最新資料）" if current_asof > asof else ""))
    else:
        lines.append(f"目前：最新資料日與報告相同（{cd.display_day(asof)}）")
    readiness = current.get("readiness")
    if isinstance(readiness, Mapping) and readiness.get("ready") is not True:
        lines.append("目前六項資料可用狀態：" + _human_label(readiness.get("status")))
    codes = [code for code in _label_codes(current.get("labels")) if code not in {"newer_data_available"}]
    if codes:
        lines.append("目前與報告的差異標示：" + "、".join(dict.fromkeys(_human_label(code) for code in codes)))
    return "\n".join(lines)


def _runtime_pointer_note(paths: MixSenderPaths, run_id: str, labels: list) -> None:
    """LATEST_C0_RUN_BY_RUNTIME.json names the newest run; when it is not the report sent, say so."""
    path = (paths.reports_root / MIX_RUNTIME_POINTER_NAME).resolve(strict=False)
    if not _inside(path, paths.reports_root) or not path.exists():
        return
    try:
        value = _read_json(path, "MIX C0 runtime pointer")
    except MixReportError as exc:
        _label(labels, "runtime_pointer_unreadable", actual=_redact_sensitive(exc)[:300])
        return
    other = value.get("run_id")
    if isinstance(other, str) and other and other != run_id:
        _label(labels, "newer_run_not_selected", recorded=run_id, actual=other,
            detail={"as_of_date": value.get("as_of_date"), "created_at": value.get("created_at"),
                    "reason": "最新一次執行的資料日沒有比較新；LATEST_C0_RUN 保留資料日較新的報告"})


def _verify_file_readbacks(bundle):
    for record in bundle.verified_files:
        actual = _file_record(record.path, record.label)
        if actual.size != record.size or actual.sha256 != record.sha256:
            raise MixReportIntegrityError("MIX same-job artifact changed：" + record.label)


def _validate_preupload(bundle):
    """KEEP the same-run file readback; a current-state change since loading is only labelled."""
    fresh = _capture_runtime()
    labels = list(fresh["labels"])
    if fresh["binding"] != bundle.runtime_binding:
        _label(labels, "current_runtime_changed_before_upload", recorded=_brief(bundle.runtime_binding),
            actual=_brief(fresh["binding"]), detail={"asof": fresh.get("asof")})
    if tuple(fresh["pairs"] or ()) != tuple(bundle.current_positive_pairs):
        _label(labels, "current_identity_changed_before_upload", recorded=len(bundle.current_positive_pairs),
            actual=len(fresh["pairs"] or ()))
    _verify_file_readbacks(bundle)
    for code in _label_codes(labels):
        LOGGER.warning("MIX C0 上傳前目前狀態標示（照常寄出）：%s", code)
    return labels


def _atomic_json(path: Path, value: Mapping[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temp = path.with_name(f".{path.name}.{os.getpid()}.tmp")
    temp.write_text(json.dumps(value, ensure_ascii=False, indent=2, allow_nan=False) + "\n", encoding="utf-8", newline="\n")
    os.replace(temp, path)


def _load_state(path: Path) -> dict[str, Any]:
    if not path.exists():
        return {"schema_version": "mix_report_delivery_state_v1", "deliveries": {}}
    value = _read_json(path, "MIX Telegram delivery state")
    if value.get("schema_version") != "mix_report_delivery_state_v1" or not isinstance(value.get("deliveries"), dict):
        raise MixReportIntegrityError("MIX Telegram delivery state schema 不符，為避免重送已停止")
    return value


def _message_id(result: Mapping[str, Any]) -> int:
    if not isinstance(result, Mapping) or type(result.get("message_id")) is not int or result["message_id"] <= 0:
        raise MixReportIntegrityError("Telegram API response 缺少有效正整數 message_id")
    return result["message_id"]


def _validate_delivery_message_receipts(messages: object, expected_keys=None) -> None:
    if not isinstance(messages, list) or not messages:
        raise MixReportIntegrityError("MIX delivery messages 必須是非空 list")
    for item in messages:
        _message_id(item)
    if expected_keys is not None:
        keys = [item.get("key") for item in messages]
        if any(not isinstance(key, str) for key in keys) or len(keys) != len(set(keys)) or set(keys) != set(expected_keys):
            raise MixReportIntegrityError("MIX complete receipt keys 不符 verified bundle")


def _complete_receipt_problem(existing: Mapping[str, Any], expected_keys) -> str:
    """Empty string when a complete entry's receipts prove delivery of exactly this bundle."""
    try:
        _validate_delivery_message_receipts(existing.get("messages"), expected_keys)
    except MixReportIntegrityError as exc:
        return str(exc)
    sent_items = existing.get("sent_items")
    if (not isinstance(sent_items, list) or any(not isinstance(item, str) for item in sent_items)
            or len(sent_items) != len(set(sent_items)) or set(sent_items) != set(expected_keys)):
        return "MIX complete sent_items 不符 verified bundle"
    return ""


def _salvage_receipts(messages: object, expected_keys) -> tuple[list[dict[str, Any]], int]:
    """Keep only receipts that individually prove a send (expected key, unique, positive message_id)."""
    kept: list[dict[str, Any]] = []
    seen: set[str] = set()
    dropped = 0
    for item in messages if isinstance(messages, list) else []:
        key = item.get("key") if isinstance(item, Mapping) else None
        message_id = item.get("message_id") if isinstance(item, Mapping) else None
        if (isinstance(key, str) and key in expected_keys and key not in seen
                and type(message_id) is int and message_id > 0):
            seen.add(key)
            kept.append({"key": key, "message_id": message_id,
                         **({"file": item["file"]} if isinstance(item.get("file"), str) else {})})
        else:
            dropped += 1
    return kept, dropped


def _reopen_corrupt_complete(existing: Mapping[str, Any], expected_keys, problem: str):
    """Reopen a complete entry whose receipts are corrupt so it is retried without --force.

    Items with a valid individual receipt stay sent (never re-sent); only unproven items are retried.
    """
    kept, dropped = _salvage_receipts(existing.get("messages"), expected_keys)
    entry = dict(existing)
    entry["messages"] = kept
    entry["sent_items"] = [item["key"] for item in kept]
    reopened_at = datetime.now().astimezone().isoformat(timespec="seconds")
    label = {"code": "complete_receipt_corrupt_reopened", "recorded": "complete", "actual": "in_progress",
             "detail": {"reason": _redact_sensitive(problem), "kept_receipts": len(kept),
                        "dropped_receipts": dropped, "previous_completed_at": existing.get("completed_at"),
                        "reopened_at": reopened_at}}
    history = existing.get("reopen_history")
    entry["reopen_history"] = (list(history) if isinstance(history, list) else []) + [label["detail"]]
    entry.pop("completed_at", None)
    LOGGER.warning("MIX complete receipt 損壞，自動重開補送（已驗證 receipt 不重送）：%s；保留 %d、丟棄 %d",
                   _redact_sensitive(problem), len(kept), dropped)
    return entry, label


def _claim_lock(path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    try:
        descriptor = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        with os.fdopen(descriptor, "w", encoding="utf-8") as handle:
            handle.write(json.dumps({"pid": os.getpid(), "started_at": datetime.now().astimezone().isoformat()}) + "\n")
    except FileExistsError as exc:
        raise MixReportDeferredError(f"MIX Telegram sender 已有另一個執行個體：{path}") from exc
    except OSError as exc:
        raise MixReportIntegrityError(f"無法建立 MIX Telegram single-instance lock：{path}") from exc


def _release_lock(path: Path) -> None:
    try:
        path.unlink(missing_ok=True)
    except OSError:
        LOGGER.warning("無法移除 MIX Telegram lock，保留 fail-closed：%s", path)


def deliver_mix_bundle(bundle, client: MixTelegramSender, state_path: Path, *, force=False):
    preupload_labels = _validate_preupload(bundle)
    state = _load_state(state_path)
    existing = state["deliveries"].get(bundle.fingerprint)
    # The key covers the report-only text; the time-varying "目前" line never changes it.
    summary_key = "summary:" + hashlib.sha256((bundle.summary_key_text or bundle.summary_text).encode("utf-8")).hexdigest()
    expected_keys = {summary_key, *(item.key for item in bundle.attachments)}
    reopen_labels: list[dict[str, Any]] = []
    if isinstance(existing, Mapping) and existing.get("report_delivery_status") == "complete" and not force:
        problem = _complete_receipt_problem(existing, expected_keys)
        if not problem:
            return False, 0  # a valid complete receipt means already delivered
        existing, reopen_label = _reopen_corrupt_complete(existing, expected_keys, problem)
        reopen_labels.append(reopen_label)
    entry = dict(existing) if isinstance(existing, Mapping) and not force else {}
    entry.update({"status": "in_progress", "report_delivery_status": "in_progress", "profile": "C0",
        "run_id": bundle.run_id, "manifest_path": str(bundle.manifest_path),
        "manifest_sha256": next(item.sha256 for item in bundle.verified_files if item.path == bundle.manifest_path),
        "expected_asof": bundle.expected_asof, "horizons": HORIZONS, "runtime_binding": bundle.runtime_binding,
        "formal_model_qualified": False, "unmet_conditions": list(bundle.unmet_conditions),
        "report_asof": bundle.expected_asof, "report_created_at": bundle.report_created_at,
        "current_asof_at_send": bundle.current_asof or None,
        "version_labels": [dict(item) for item in bundle.version_labels] + preupload_labels + reopen_labels,
        "updated_at": datetime.now().astimezone().isoformat(timespec="seconds")})
    entry.setdefault("sent_items", [])
    entry.setdefault("messages", [])
    if (not isinstance(entry["sent_items"], list) or not isinstance(entry["messages"], list)
            or any(not isinstance(item, str) for item in entry["sent_items"])):
        raise MixReportIntegrityError("MIX retry state sent_items/messages 不符")
    for message in entry["messages"]:
        _message_id(message)
    receipt_keys = [item.get("key") for item in entry["messages"]]
    if (any(not isinstance(key, str) for key in receipt_keys)
            or len(receipt_keys) != len(set(receipt_keys)) or set(receipt_keys) != set(entry["sent_items"])
            or not set(receipt_keys).issubset(expected_keys)):
        raise MixReportIntegrityError("MIX retry receipt keys 不符 verified bundle")
    state["deliveries"][bundle.fingerprint] = entry
    _atomic_json(state_path, state)
    sent_items, sent_count = set(entry["sent_items"]), 0

    def record_sent(key, result, file=None):
        nonlocal sent_count
        entry["messages"].append({"key": key, "message_id": _message_id(result), **({"file": str(file)} if file else {})})
        entry["sent_items"].append(key)
        sent_items.add(key)
        sent_count += 1
        entry["updated_at"] = datetime.now().astimezone().isoformat(timespec="seconds")
        _atomic_json(state_path, state)

    if summary_key not in sent_items:
        record_sent(summary_key, client.send_message(bundle.summary_text))
    for attachment in bundle.attachments:
        if attachment.key in sent_items:
            continue
        _verify_file_readbacks(bundle)
        caption = _attachment_caption(attachment, bundle)
        record_sent(attachment.key, client.send_document(attachment.path, caption), attachment.path)
    _validate_delivery_message_receipts(entry["messages"], expected_keys)
    entry["status"] = entry["report_delivery_status"] = "complete"
    entry["completed_at"] = datetime.now().astimezone().isoformat(timespec="seconds")
    entry["updated_at"] = entry["completed_at"]
    state["last_report_complete_fingerprint"] = state["last_complete_fingerprint"] = bundle.fingerprint
    state["last_report_complete_at"] = state["last_complete_at"] = entry["completed_at"]
    _atomic_json(state_path, state)
    return True, sent_count


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="傳送 MIX C0 current 報告，不讀取 Telegram updates。", allow_abbrev=False)
    parser.add_argument("--env", type=Path, default=DEFAULT_ENV_PATH)
    parser.add_argument("--state", type=Path, default=DEFAULT_STATE_PATH)
    parser.add_argument("--lock", type=Path, default=DEFAULT_LOCK_PATH)
    parser.add_argument("--log-dir", type=Path, default=DEFAULT_LOG_DIR)
    parser.add_argument("--expected-asof", default="", help=f"資料日 {cd.DATE_HELP}")
    parser.add_argument("--target", default="", help=f"顯式核 H1 目標日期 {cd.DATE_HELP}；預設允許空值")
    parser.add_argument("--max-wait-seconds", type=float, default=4 * 60 * 60,
        help=f"報告尚未產生時最多等待秒數；0 表示使用預設上限 {ZERO_WAIT_CAP_SECONDS} 秒（30 分鐘），逾時 exit 4")
    parser.add_argument("--poll-seconds", type=float, default=60.0)
    parser.add_argument("--dry-run", action="store_true", help="本地驗證 current MIX C0 refs，不連 Telegram")
    parser.add_argument("--verify-only", action="store_true", help="本地驗證，不建立 delivery state")
    parser.add_argument("--check-telegram", action="store_true", help="只呼叫 getMe/getWebhookInfo，不傳送報告")
    parser.add_argument("--force", action="store_true", help="驗證相同 current fingerprint 後明確重送")
    return parser


def _validate_cli_paths(args):
    for value, label in ((args.state, "state"), (args.lock, "lock"), (args.log_dir, "log"), (args.env, "env")):
        if not _inside(Path(value), TELEGRAM_ROOT) and Path(value).resolve(strict=False) != DEFAULT_ENV_PATH.resolve(strict=False):
            raise ConfigurationError(f"MIX Telegram {label} 必須位於 Telegram namespace：{value}")


def _peek_report_days(paths) -> str:
    """Best-effort data days for the timeout message (never raises)."""
    if paths is None:
        return "報告資料日：未知"
    parts = []
    for label, path in (("報告資料日", paths.pointer_path), ("最新一次執行資料日", paths.reports_root / MIX_RUNTIME_POINTER_NAME)):
        try:
            value = json.loads(Path(path).read_bytes().decode("utf-8-sig"))
            day = cd.display_day(cd.iso_day(str(value.get("as_of_date"))))
        except (OSError, UnicodeError, ValueError, TypeError, AttributeError):
            day = "未知"
        parts.append(f"{label}：{day}")
    return "；".join(parts)


def _load_bundle_with_wait(args, paths):
    # ``--max-wait-seconds 0`` (the 08:00 schedule) used to poll forever; it is capped at 30 minutes.
    wait_seconds = args.max_wait_seconds if args.max_wait_seconds > 0 else ZERO_WAIT_CAP_SECONDS
    deadline = time.monotonic() + wait_seconds
    while True:
        try:
            return load_mix_bundle(paths, expected_asof=args.expected_asof, target=args.target)
        except MixReportDeferredError as exc:
            if not getattr(exc, "retryable", True):
                raise
            if time.monotonic() >= deadline:
                raise MixReportDeferredError(
                    f"MIX C0 報告等待已達上限 {wait_seconds:.0f} 秒"
                    + ("（--max-wait-seconds 0 的預設上限 30 分鐘）" if args.max_wait_seconds <= 0 else "")
                    + f"，未寄出；{_peek_report_days(paths)}；最後狀態：{_redact_sensitive(exc)}") from exc
            LOGGER.info("MIX C0 報告等待 %.0f 秒：%s", args.poll_seconds, _redact_sensitive(exc))
            time.sleep(args.poll_seconds)


def run(args):
    setup_logging(args.log_dir, LOGGER)
    paths = MixSenderPaths(state_path=args.state.resolve(strict=False), lock_path=args.lock.resolve(strict=False), log_dir=args.log_dir.resolve(strict=False))
    if args.check_telegram:
        env = load_env_file(args.env)
        token, chat_id = setting(env, "TELEGRAM_BOT_TOKEN"), setting(env, "TELEGRAM_CHAT_ID")
        validate_credentials(token, chat_id)
        client = TelegramClient(token, chat_id)
        bot, webhook = client.get_me(), client.get_webhook_info()
        LOGGER.info("Telegram check OK：Bot=@%s，webhook_pending=%s", bot.get("username", "unknown"), webhook.get("pending_update_count", 0))
        return 0
    bundle = _load_bundle_with_wait(args, paths)
    LOGGER.info("MIX current C0 refs verified：run_id=%s artifacts=%d", bundle.run_id, len(bundle.attachments))
    if args.dry_run or args.verify_only:
        return 0
    _claim_lock(paths.lock_path)
    try:
        env = load_env_file(args.env)
        if setting(env, "TELEGRAM_DELIVERY_ENABLED").lower() not in TRUE_VALUES:
            raise MixReportDeferredError("Telegram delivery 未啟用")
        token, chat_id = setting(env, "TELEGRAM_BOT_TOKEN"), setting(env, "TELEGRAM_CHAT_ID")
        validate_credentials(token, chat_id)
        client = TelegramClient(token, chat_id)
        delivered, count = deliver_mix_bundle(bundle, client, paths.state_path, force=args.force)
        LOGGER.info("MIX C0 delivery：run_id=%s delivered=%s messages=%d", bundle.run_id, delivered, count)
        return 0
    finally:
        _release_lock(paths.lock_path)


def main() -> int:
    parser = _parser()
    args = parser.parse_args()
    if (not math.isfinite(args.max_wait_seconds) or not math.isfinite(args.poll_seconds)
            or args.max_wait_seconds < 0 or args.poll_seconds <= 0):
        parser.error("--max-wait-seconds 不可小於 0，--poll-seconds 必須大於 0")
    try:
        _validate_cli_paths(args)
        return run(args)
    except MixReportDeferredError as exc:
        if not LOGGER.handlers:
            setup_logging(args.log_dir, LOGGER)
        LOGGER.warning("DEFERRED: %s", _redact_sensitive(exc))
        return 4
    except (MixReportIntegrityError, ConfigurationError, TelegramApiError) as exc:
        if not LOGGER.handlers:
            setup_logging(args.log_dir, LOGGER)
        LOGGER.error("FAILED: %s", _redact_sensitive(exc))
        return 2
    except KeyboardInterrupt:
        LOGGER.warning("使用者取消 MIX Telegram 傳送")
        return 130
    except Exception:
        # Unexpected errors (e.g. a locked state file) keep their traceback and exit 1, but also reach the log file.
        if not LOGGER.handlers:
            setup_logging(args.log_dir, LOGGER)
        LOGGER.exception("FAILED（未預期錯誤）")
        raise


if __name__ == "__main__":
    raise SystemExit(main())
