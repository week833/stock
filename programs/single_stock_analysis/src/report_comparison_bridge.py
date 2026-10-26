"""Optional report metadata bridge. Never changes predictions or authorizes models."""
from __future__ import annotations

import copy
import hashlib
import html
import importlib
import json
import os
import re
import stat
import sys
from datetime import date
from functools import lru_cache
from pathlib import Path

COLUMNS = ("項目", "資料期間", "本期值", "比較", "解讀", "資料日期")
_SHA = re.compile(r"[0-9a-fA-F]{64}\Z")
CACHE_SCHEMA = "stock-report-comparison-job-cache-v1"
CACHE_CONTEXT = "telegram-single-stock-both"
CACHE_ENV_PREFIX = "DSTOCK_REPORT_COMPARISON_CACHE_"
CACHE_FILE_NAME = "comparison_block.json"
CACHE_TEMP_NAME = CACHE_FILE_NAME + ".tmp"
_NONCE = re.compile(r"[0-9a-f]{32}\Z")
try:
    _BRIDGE_SOURCE_SHA256 = hashlib.sha256(Path(__file__).read_bytes()).hexdigest()
except OSError:
    _BRIDGE_SOURCE_SHA256 = None  # source identity is optional; the original report path remains usable
_ENGINE_SOURCE_BINDINGS = {}


def _engine():
    shared = Path(__file__).resolve().parents[2] / "shared"
    if str(shared) not in sys.path:
        sys.path.insert(0, str(shared))
    return importlib.import_module("stock_report_comparison")


def _canonical(value):
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False).encode("utf-8")


def _signal_date(value):
    text = value.isoformat() if isinstance(value, date) else str(value)
    if re.fullmatch(r"[0-9]{8}", text):
        text = text[:4] + "-" + text[4:6] + "-" + text[6:]
    return date.fromisoformat(text).isoformat()


def _regular(info):
    return stat.S_ISREG(info.st_mode) and not (
        getattr(info, "st_file_attributes", 0) & getattr(stat, "FILE_ATTRIBUTE_REPARSE_POINT", 0x400))


def _cache_directory(directory, nonce):
    path = Path(directory)
    if not _NONCE.fullmatch(str(nonce)) or not path.is_absolute() or path.name != ".report_comparison_" + nonce:
        raise ValueError("job comparison cache context differs")
    info = path.lstat()
    if (not stat.S_ISDIR(info.st_mode) or path.is_symlink()
            or getattr(info, "st_file_attributes", 0) & getattr(stat, "FILE_ATTRIBUTE_REPARSE_POINT", 0x400)
            or path.resolve(strict=True) != path.absolute()):
        raise ValueError("job comparison cache directory is not owned plain storage")
    return path


def _cache_identity(payload, nonce):
    head = analysis_head(payload)
    stock = str((payload.get("stock") or {}).get("stock_id", ""))
    signal = payload.get("asof") or payload.get("analysis_asof") or payload.get("latest_data_date")
    if not head or not stock or not signal:
        raise ValueError("job comparison cache requires actual report identity")
    engine = _engine()
    engine_sha = hashlib.sha256(Path(engine.__file__).read_bytes()).hexdigest()
    loaded = _ENGINE_SOURCE_BINDINGS.setdefault(id(engine), (engine, engine_sha))
    if (loaded is None or loaded[0] is not engine or loaded[1] != engine_sha
            or hashlib.sha256(Path(__file__).read_bytes()).hexdigest() != _BRIDGE_SOURCE_SHA256):
        raise ValueError("comparison source changed after import")
    return {"context": CACHE_CONTEXT, "job_nonce": nonce, "cache_schema": CACHE_SCHEMA,
            "central_root": os.path.normcase(os.path.abspath(engine.CENTRAL_ROOT)),
            "head_sha256": head, "stock_id": stock, "asof": _signal_date(signal),
            "block_schema": engine.SCHEMA, "engine_sha256": engine_sha,
            "bridge_sha256": _BRIDGE_SOURCE_SHA256}


def _block_matches(block, identity):
    return (isinstance(block, dict) and block.get("schema") == identity["block_schema"]
            and block.get("stock_id") == identity["stock_id"]
            and _signal_date(block.get("signal_date")) == identity["asof"]
            and block.get("source_head_sha256") == identity["head_sha256"]
            and isinstance(block.get("metrics"), list) and isinstance(block.get("notes"), list)
            and all(isinstance(note, str) for note in block["notes"]))


def _read_cache(directory, nonce):
    directory = _cache_directory(directory, nonce)
    path = directory / CACHE_FILE_NAME
    before = path.lstat()
    if not _regular(before):
        raise ValueError("job comparison cache is not a regular file")
    raw = path.read_bytes()
    after = path.lstat()
    if not _regular(after) or (before.st_dev, before.st_ino, before.st_size, before.st_mtime_ns) != (
            after.st_dev, after.st_ino, after.st_size, after.st_mtime_ns):
        raise ValueError("job comparison cache changed while reading")
    return raw, json.loads(raw.decode("utf-8"))


def _read_matching_cache(directory, nonce, identity, expected_sha=None):
    raw, envelope = _read_cache(directory, nonce)
    if expected_sha is not None and (
            not _SHA.fullmatch(str(expected_sha)) or hashlib.sha256(raw).hexdigest() != expected_sha.lower()):
        raise ValueError("job comparison cache bytes differ from parent handoff")
    if (not isinstance(envelope, dict) or envelope.get("schema") != CACHE_SCHEMA
            or envelope.get("identity") != identity):
        raise ValueError("job comparison cache identity differs")
    block = envelope.get("block")
    if not _block_matches(block, identity) or hashlib.sha256(_canonical(block)).hexdigest() != envelope.get("block_sha256"):
        raise ValueError("job comparison block integrity differs")
    return raw, block


def cache_handoff_digest(directory, nonce, payload):
    """Promote only a block equal to the parent's already validated/delivered report."""
    try:
        identity = _cache_identity(payload, nonce)
        raw, block = _read_matching_cache(directory, nonce, identity)
        if _canonical(block) != _canonical(payload.get("report_comparison")):
            return None
        return hashlib.sha256(raw).hexdigest()
    except Exception:
        return None


def _cache_environment():
    directory = os.environ.get(CACHE_ENV_PREFIX + "DIR")
    nonce = os.environ.get(CACHE_ENV_PREFIX + "NONCE")
    phase = os.environ.get(CACHE_ENV_PREFIX + "PHASE")
    if not directory or not nonce or phase not in {"write", "read"}:
        return None
    try:
        return _cache_directory(directory, nonce), nonce, phase
    except Exception:
        return None


def _cached_block(payload, context):
    if context is None or context[2] != "read":
        return None
    expected = os.environ.get(CACHE_ENV_PREFIX + "READ_SHA256")
    if not expected:
        return None
    try:
        identity = _cache_identity(payload, context[1])
        _, block = _read_matching_cache(context[0], context[1], identity, expected)
        return copy.deepcopy(block)
    except Exception:
        return None


def _write_cache(payload, block, context, identity):
    if context is None or context[2] != "write" or identity is None:
        return
    try:
        if identity != _cache_identity(payload, context[1]) or not _block_matches(block, identity):
            return
        directory = _cache_directory(context[0], context[1])
        envelope = {"schema": CACHE_SCHEMA, "identity": identity, "block": block,
                    "block_sha256": hashlib.sha256(_canonical(block)).hexdigest()}
        temporary, target = directory / CACHE_TEMP_NAME, directory / CACHE_FILE_NAME
        if target.exists() or target.is_symlink():
            return  # one write per fresh job; never replace another completed handoff
        with temporary.open("xb") as handle:
            handle.write(_canonical(envelope) + b"\n")
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, target)
    except Exception:
        pass  # optional cache failures must not discard existing report information


def analysis_head(payload):
    for container in (payload.get("snapshot_binding"), payload.get("published_snapshot")):
        if not isinstance(container, dict):
            continue
        value = container.get("head_sha256") or container.get("manifest_sha256")
        if isinstance(value, str) and _SHA.fullmatch(value):
            return value.lower()
        sources = container.get("sources") or []
        values = {item.get("manifest_sha256") for item in sources if isinstance(item, dict)
                  and isinstance(item.get("manifest_sha256"), str) and _SHA.fullmatch(item["manifest_sha256"])}
        if len(values) == 1:
            return next(iter(values)).lower()
    return None


def _comparison_temp_root():
    from stock_daily_candidate_runtime import _consumer_path
    from stock_daily_selected_deployment import SINGLE_ROOT

    return _consumer_path(SINGLE_ROOT / "cache" / "report_comparison")


@lru_cache(maxsize=64)
def _pinned_block(stock, signal, head):
    return _engine().build_comparisons(
        [stock], signal, head_sha=head, use_verified_projection=True,
        consumer_temp_root=_comparison_temp_root())[stock]


def attach_report_comparison(payload):
    """Attach once; the same stock/head/day is reused for H1 and H63."""
    if isinstance(payload.get("report_comparison"), dict):
        return payload["report_comparison"]
    stock = str((payload.get("stock") or {}).get("stock_id", ""))
    signal = payload.get("asof") or payload.get("analysis_asof") or payload.get("latest_data_date")
    head = analysis_head(payload)
    context = _cache_environment()
    identity = None
    if context is not None and context[2] == "write":
        try:
            identity = _cache_identity(payload, context[1])
        except Exception:
            pass
    try:
        if not stock or not signal:
            raise ValueError("缺少本股身分或實際分析日")
        block = _cached_block(payload, context)
        if block is None:
            block = copy.deepcopy(_pinned_block(stock, str(signal), head) if head else
                                  _engine().build_comparisons(
                                      [stock], signal, head_sha=None, use_verified_projection=True,
                                      consumer_temp_root=_comparison_temp_root())[stock])
    except Exception as exc:
        block = {"schema": "stock-report-comparison-v1", "stock_id": stock,
                 "signal_date": (str(signal)[:4] + "-" + str(signal)[4:6] + "-" + str(signal)[6:8])
                     if signal and len(str(signal)) == 8 and str(signal).isdigit() else str(signal or "未提供")[:10],
                 "source_head_sha256": None, "requested_head_sha256": head, "metrics": [],
                 "notes": ["歷史比較不可得：" + type(exc).__name__ + "；原有預測與來源資料保留。"]}
    _write_cache(payload, block, context, identity)
    payload["report_comparison"] = block
    if "runtime_payload_sha256" in payload:
        unsigned = {key: value for key, value in payload.items() if key != "runtime_payload_sha256"}
        payload["runtime_payload_sha256"] = hashlib.sha256(json.dumps(
            unsigned, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False).encode("utf-8")).hexdigest()
    return block


def report_comparison_rows(payload):
    block = payload.get("report_comparison") or {}
    try:
        return _engine().comparison_rows(block)
    except Exception:
        return [{key: str(item.get(key, "未提供")) for key in COLUMNS}
                for item in block.get("metrics") or [] if isinstance(item, dict)]


def comparison_preview_html(payload):
    """Pure read-only preview from the block already present in a JSON payload."""
    block = payload.get("report_comparison") or {}
    esc = lambda value: html.escape(str(value), quote=True)
    rows = report_comparison_rows(payload)
    return ('<!doctype html><html lang="zh-Hant"><meta charset="utf-8"><title>單股歷史比較預覽</title>'
            '<h1>單股歷史比較</h1><table border="1"><tr>' +
            ''.join('<th>' + esc(key) + '</th>' for key in COLUMNS) + '</tr>' +
            ''.join('<tr>' + ''.join('<td>' + esc(row[key]) + '</td>' for key in COLUMNS) + '</tr>' for row in rows) +
            '</table><ul>' + ''.join('<li>' + esc(note) + '</li>' for note in block.get("notes", [])) + '</ul></html>')
