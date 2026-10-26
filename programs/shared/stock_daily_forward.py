"""Small shared C0 forecast ledger; caller-verified inputs, no data fetching.

The SQLite ledger preserves the original forecast and its actual recording time.
Only exact market-session endpoints within an explicitly verified query scope
can settle a forecast. Calibration overlays never alter the base model weights.
"""
from __future__ import annotations

import copy
import hashlib
import json
import math
import os
import shutil
import sqlite3
from collections.abc import Mapping
from contextlib import contextmanager
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

DEFAULT_DB_PATH = Path(r"D:\TWStockData\program_data\stock_daily_c0\state\forward.sqlite3")
SCHEMA = "tw-stock-c0-forward-v1"
DB_VERSION = 2
RETENTION_REFS_SCHEMA = "tw-stock-c0-forward-retention-refs-v1"
RETENTION_PLAN_SCHEMA = "tw-stock-c0-forward-retention-plan-v1"
PENDING_EXPORT_SCHEMA = "tw-stock-c0-forward-settlement-requirements-v1"
QA_ROOT = Path(r"D:\TWStockData\artifacts\current-only-execution-20261009")
MAX_FORECAST_AGE_DAYS = 180
ENDPOINT_GRACE_SESSIONS = 20
MAX_EXACT_REFS = 50000
CALIBRATION_SCHEMA = "tw-stock-c0-forward-calibration-v1"
FEATURE_SCHEMA = "tw-stock-c0-price-seven-v1"
FEATURE_ORDER = ("return_5", "return_20", "return_60", "return_126", "return_252",
                 "volatility_20", "volatility_60")
CALIBRATE_DAYS = 20
RETRAIN_DAYS = 60
MAX_CALIBRATION_DAYS = 126
FLAT_TOLERANCE = 1e-12
PROSPECTIVE_POLICY = "before_target_session_open_v1"
_TAIPEI = timezone(timedelta(hours=8))
_SETTLEMENT_PENDING_SQL = """SELECT p.*,g.semantics_sha256,g.sealed_signal_date,g.profile_id
    FROM verified_scope AS s CROSS JOIN predictions AS p INDEXED BY idx_v2_pending_scope
    ON p.market=s.market AND p.stock_id=s.stock_id JOIN group_state AS g ON g.group_id=p.group_id
    WHERE p.observation_status IN ('pending','unobserved') AND p.price_basis=?"""
_SETTLEMENT_EXACT_RECORD_SQL = """SELECT p.*,g.semantics_sha256,g.sealed_signal_date,g.profile_id,
    g.base_model_version AS group_base_model_version,g.model_sha256 AS group_model_sha256,
    g.horizon AS group_horizon,g.market AS group_market,g.price_basis AS group_price_basis
    FROM verified_record_scope AS r CROSS JOIN predictions AS p ON p.record_id=r.record_id
    JOIN group_state AS g ON g.group_id=p.group_id"""
_CALIBRATION_SAMPLE_SQL = """SELECT record_id,stock_id,signal_date,raw_probability,observed_return,observed_up
    FROM (SELECT p.record_id,p.stock_id,p.signal_date,p.raw_probability,p.observed_return,p.observed_up,
          ROW_NUMBER() OVER(PARTITION BY p.stock_id,p.signal_date ORDER BY p.recorded_at,p.record_id) AS rn
          FROM predictions AS p JOIN mature_days AS d ON d.group_id=p.group_id AND d.signal_date=p.signal_date
          WHERE p.group_id=? AND p.observation_status='observed' AND p.prospective_eligible=1)
    WHERE rn=1 ORDER BY signal_date,stock_id,record_id"""


def _require(condition, message):
    if not condition:
        raise ValueError(message)


MAX_LABEL_RECORD_IDS = 50


def _label(labels, code, *, recorded=None, actual=None, detail=None):
    """Version/freshness drift is recorded, never raised (stock-daily version_labels)."""
    if labels is None:
        return
    item = {"code": code, "recorded": recorded, "actual": actual, "detail": detail}
    if item not in labels:
        labels.append(item)


def _row_marker(result, kind, record_id, reason=None):
    """Per-row marker: the row is left unchanged and excluded; other rows proceed."""
    markers = result.setdefault("row_markers", {})
    entry = markers.setdefault(kind, {"count": 0, "record_ids": []})
    entry["count"] += 1
    if record_id is not None and len(entry["record_ids"]) < MAX_LABEL_RECORD_IDS:
        entry["record_ids"].append(record_id if reason is None else [record_id, reason])


def _json(value):
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False)


def _hash(value):
    return hashlib.sha256(_json(value).encode("utf-8")).hexdigest()


def _sha(value, field):
    _require(isinstance(value, str) and len(value) == 64 and
             all(c in "0123456789abcdef" for c in value), "invalid_" + field)
    return value


def _day(value):
    _require(isinstance(value, str), "session_date_must_be_iso_string")
    parsed = date.fromisoformat(value)
    _require(parsed.isoformat() == value, "session_date_must_be_iso_string")
    return value


def _now():
    return datetime.now(timezone.utc)


def _rows(batch):
    if hasattr(batch, "to_dict") and not isinstance(batch, Mapping):
        batch = batch.to_dict("records")
    _require(not isinstance(batch, (str, bytes, Mapping)), "batch_must_be_rows")
    rows = [copy.deepcopy(dict(row)) for row in batch]
    return rows


def _number(value, field, *, nullable=False, probability=False, positive=False):
    if value is None and nullable:
        return None
    _require(not isinstance(value, bool), "invalid_" + field)
    try:
        number = float(value)
    except (TypeError, ValueError):
        raise ValueError("invalid_" + field) from None
    _require(math.isfinite(number) and (not probability or 0 <= number <= 1) and
             (not positive or number > 0), "invalid_" + field)
    return number


@contextmanager
def _transaction(db_path, retention_refs=None, labels=None):
    path = Path(db_path)
    path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(path), timeout=30, isolation_level=None)
    conn.row_factory = sqlite3.Row
    try:
        conn.execute("PRAGMA foreign_keys=ON")
        conn.execute("BEGIN IMMEDIATE")
        _prepare_schema(conn, retention_refs)
        if retention_refs is not None:
            _sync_refs(conn, retention_refs, _now(), labels)
        yield conn
        conn.commit()
    except BaseException:
        conn.rollback()
        raise
    finally:
        conn.close()


def _timestamp(value):
    _require(isinstance(value, str), "timestamp_must_be_aware_iso_string")
    parsed = datetime.fromisoformat(value)
    _require(parsed.tzinfo is not None and parsed.utcoffset() is not None, "timestamp_timezone_missing")
    return parsed.astimezone(timezone.utc)


def _meta(conn, key, default=None):
    row = conn.execute("SELECT value_json FROM meta WHERE key=?", (key,)).fetchone()
    return json.loads(row[0]) if row else default


def _put_meta(conn, key, value):
    payload = _json(value)
    previous = conn.execute("SELECT value_json FROM meta WHERE key=?", (key,)).fetchone()
    if previous is None or previous[0] != payload:
        conn.execute("INSERT INTO meta(key,value_json) VALUES (?,?) ON CONFLICT(key) DO UPDATE SET value_json=excluded.value_json",
                     (key, payload))


def _adoption_unverified_reason(ref, semantic):
    """None when the administrative contract adoption verifies; otherwise the reason code.

    A failing adoption is a model/policy version drift (stock-daily version_labels):
    the analysis continues, but the forecasts of that model are isolated from the
    current forward group (see ``_forward_isolation``)."""
    adoption = ref["contract_adoption_ref"]
    if not (isinstance(adoption, Mapping) and ref.get("contract_adoption_verified") is True and
            ref.get("original_effective_C0_semantics_projection_sha256") == semantic):
        return "administrative_contract_semantics_not_verified_equal"
    try:
        _sha(adoption.get("sha256"), "contract_adoption_ref_sha256")
    except ValueError as exc:
        return str(exc)
    receipt_size = adoption.get("byte_size", adoption.get("size"))
    if not (isinstance(adoption.get("path"), str) and bool(adoption["path"]) and type(receipt_size) is int and receipt_size > 0):
        return "contract_adoption_ref_incomplete"
    receipt = Path(adoption["path"])
    try:
        if not (receipt_size <= 1024 * 1024 and receipt.is_file() and receipt.stat().st_size == receipt_size and
                _file_sha(receipt) == adoption["sha256"]):
            return "contract_adoption_ref_bytes_mismatch"
        receipt_data = json.loads(receipt.read_text(encoding="utf-8-sig"))
    except (OSError, ValueError):
        return "contract_adoption_ref_bytes_mismatch"
    proof = receipt_data.get("effective_C0_semantics", {}) if isinstance(receipt_data, Mapping) else {}
    proof = proof if isinstance(proof, Mapping) else {}
    original_projection, current_projection = proof.get("original_projection"), proof.get("current_projection")
    try:
        original_digest = hashlib.sha256((_json(original_projection) + "\n").encode("utf-8")).hexdigest()
        current_digest = hashlib.sha256((_json(current_projection) + "\n").encode("utf-8")).hexdigest()
    except (TypeError, ValueError):
        return "contract_adoption_projection_not_equal"
    if not (isinstance(receipt_data, Mapping) and
            receipt_data.get("schema") == "tw-stock-c0-current-contract-adoption-v1" and
            receipt_data.get("stable_base_model_version") == ref["base_model_version"] and
            proof.get("projection_schema") == "tw-stock-c0-effective-semantics-v1" and proof.get("equal") is True and
            isinstance(original_projection, Mapping) and original_projection == current_projection and
            original_digest == current_digest == semantic == proof.get("original_projection_sha256") ==
            proof.get("current_projection_sha256")):
        return "contract_adoption_projection_not_equal"
    return None


def _ref_labels(refs, labels):
    """Copy each role's isolation reasons into version_labels."""
    for role in ("current", "rollback", "pending"):
        ref = refs.get(role) if isinstance(refs, Mapping) else None
        if not isinstance(ref, Mapping):
            continue
        for item in ref.get("forward_isolation") or []:
            code = item.get("code") if isinstance(item, Mapping) else str(item)
            _label(labels, code, recorded=item.get("recorded") if isinstance(item, Mapping) else None,
                   actual=item.get("actual") if isinstance(item, Mapping) else None,
                   detail={"role": role, "base_model_version": ref.get("base_model_version"),
                           "reason": item.get("detail") if isinstance(item, Mapping) else None,
                           "forward_statistics": "excluded"})


def _normalize_refs(refs, now, *, verify_files=True):
    _require(isinstance(refs, Mapping) and refs.get("schema") == RETENTION_REFS_SCHEMA,
             "verified_retention_refs_missing")
    result = {"schema": RETENTION_REFS_SCHEMA, "current": None, "rollback": None, "pending": None,
              "checkpoint": None, "protected_record_ids": [], "protected_calibration_versions": []}
    for role in ("current", "rollback", "pending"):
        ref = refs.get(role)
        if ref is None:
            continue
        _require(isinstance(ref, Mapping), "invalid_model_role_reference")
        ref = copy.deepcopy(dict(ref))
        for field in ("base_model_version", "model_activation_epoch"):
            _require(isinstance(ref.get(field), str) and bool(ref[field]), "retention_ref_missing_" + field)
        _timestamp(ref["model_activation_epoch"])
        _require(_timestamp(ref["model_activation_epoch"]) <= now, "model_activation_epoch_in_future")
        _sha(ref.get("model_sha256"), "retention_model_sha256")
        _require(ref.get("profile_id") == "C0", "retention_profile_not_supported")
        semantic = _sha(ref.get("effective_C0_semantics_projection_sha256"), "effective_C0_semantics_projection_sha256")
        if verify_files:
            # Recompute every time; an isolation stored by a previous sync never sticks.
            ref.pop("forward_isolation", None)
            reason = _adoption_unverified_reason(ref, semantic) if ref.get("contract_adoption_ref") is not None else None
            if reason is not None:
                adoption = ref.get("contract_adoption_ref")
                ref["forward_isolation"] = [{"code": "adoption_unverified",
                    "recorded": adoption.get("sha256") if isinstance(adoption, Mapping) else None,
                    "actual": None, "detail": reason}]
        if role == "pending":
            _require(isinstance(ref.get("owner"), str) and bool(ref["owner"]), "pending_owner_missing")
            created, expires = _timestamp(ref.get("created_at")), _timestamp(ref.get("expires_at"))
            _require(created <= now and created < expires <= created + timedelta(days=MAX_FORECAST_AGE_DAYS), "pending_reference_unbounded")
            _require(ref.get("status") in ("prepared", "running", "paused", "blocked"), "pending_status_invalid")
            if expires <= now:
                continue
        result[role] = ref
    _require(result["current"] is not None, "current_model_reference_missing")
    checkpoint = refs.get("checkpoint")
    if checkpoint is not None:
        _require(isinstance(checkpoint, Mapping) and isinstance(checkpoint.get("owner"), str) and bool(checkpoint["owner"]),
                 "checkpoint_owner_missing")
        created, expires = _timestamp(checkpoint.get("created_at")), _timestamp(checkpoint.get("expires_at"))
        _require(created <= now and created < expires <= created + timedelta(days=MAX_FORECAST_AGE_DAYS) and
                 checkpoint.get("status") in ("prepared", "running", "paused", "blocked"), "checkpoint_reference_unbounded")
        if expires > now:
            result["checkpoint"] = copy.deepcopy(dict(checkpoint))
    for field in ("protected_record_ids", "protected_calibration_versions"):
        values = refs.get(field, [])
        _require(isinstance(values, (list, tuple)) and all(isinstance(v, str) and bool(v) for v in values), "invalid_" + field)
        values = sorted(set(values))
        _require(len(values) <= MAX_EXACT_REFS, "unbounded_" + field)
        pending_source = refs.get("pending") or {}
        pending_source_values = pending_source.get(field, [])
        _require(not values or checkpoint is not None or set(values).issubset(set(pending_source_values)),
                 "exact_reference_requires_finite_checkpoint")
        if result["checkpoint"] is None:
            values = []
        if result["pending"] is not None:
            pending_values = result["pending"].get(field, [])
            _require(isinstance(pending_values, (list, tuple)) and all(isinstance(v, str) for v in pending_values), "invalid_pending_" + field)
            values = sorted(set(values).union(pending_values))
            _require(len(values) <= MAX_EXACT_REFS, "unbounded_" + field)
        result[field] = values
    return result


def _ref_for_base(refs, base):
    for role in ("current", "rollback", "pending"):
        ref = refs.get(role)
        if ref is not None and ref["base_model_version"] == base:
            return role, ref
    return "retired", None


def _role_for_group(group, refs):
    for role in ("current", "rollback", "pending"):
        ref = refs.get(role)
        if ref is not None and all(group[name] == ref[field] for name, field in (
                ("base_model_version", "base_model_version"), ("model_sha256", "model_sha256"),
                ("activation_epoch", "model_activation_epoch"), ("profile_id", "profile_id"),
                ("semantics_sha256", "effective_C0_semantics_projection_sha256"))):
            return role
    return "retired"


def _create_schema(conn):
    statements = (
        "CREATE TABLE meta(key TEXT PRIMARY KEY,value_json TEXT NOT NULL)",
        """CREATE TABLE group_state(group_id TEXT PRIMARY KEY,base_model_version TEXT NOT NULL,
            model_sha256 TEXT NOT NULL,activation_epoch TEXT NOT NULL,profile_id TEXT NOT NULL,
            semantics_sha256 TEXT NOT NULL,horizon INTEGER NOT NULL,market TEXT NOT NULL,price_basis TEXT NOT NULL,
            group_role TEXT NOT NULL,legacy_group_alias TEXT,record_id_scheme TEXT NOT NULL,
            lifetime_mature_count INTEGER NOT NULL DEFAULT 0,sealed_signal_date TEXT,
            calibration_cursor INTEGER NOT NULL DEFAULT 0,retrain_cursor INTEGER NOT NULL DEFAULT 0,
            seen_cursor INTEGER NOT NULL DEFAULT 0,last_issued_token_hash TEXT,last_issued_ordinal INTEGER,
            last_consumed_token_hash TEXT,last_consumed_ordinal INTEGER,last_revoked_token_hash TEXT,last_revoked_ordinal INTEGER,
            UNIQUE(base_model_version,activation_epoch,profile_id,semantics_sha256,horizon,market,price_basis))""",
        "CREATE TABLE group_aliases(alias_group_id TEXT PRIMARY KEY,group_id TEXT NOT NULL REFERENCES group_state(group_id) ON DELETE CASCADE)",
        "CREATE TABLE bindings(binding_id TEXT PRIMARY KEY,profile_id TEXT NOT NULL,semantics_sha256 TEXT NOT NULL,binding_json TEXT NOT NULL)",
        """CREATE TABLE predictions(record_id TEXT PRIMARY KEY,group_id TEXT NOT NULL REFERENCES group_state(group_id),
            base_model_version TEXT NOT NULL,model_sha256 TEXT NOT NULL,price_artifact_sha256 TEXT NOT NULL,stock_id TEXT NOT NULL,market TEXT NOT NULL,
            signal_date TEXT NOT NULL,horizon INTEGER NOT NULL,price_basis TEXT NOT NULL,calibration_version TEXT NOT NULL,
            raw_probability REAL,final_probability REAL,raw_return REAL,final_return REAL,direction_kind TEXT,
            signal_close REAL,original_ordinary INTEGER,target_date TEXT,recorded_at TEXT NOT NULL,recorded_date TEXT NOT NULL,record_kind TEXT NOT NULL,
            prospective_eligible INTEGER,prospective_reason TEXT NOT NULL,immutable_sha256 TEXT NOT NULL,
            scientific_comparison_sha256 TEXT NOT NULL,binding_id TEXT NOT NULL REFERENCES bindings(binding_id),
            observation_status TEXT NOT NULL,target_close REAL,observed_return REAL,observed_up INTEGER,
            settlement_binding_id TEXT REFERENCES bindings(binding_id),settled_at TEXT,
            UNIQUE(group_id,stock_id,signal_date,calibration_version))""",
        """CREATE TABLE mature_days(group_id TEXT NOT NULL REFERENCES group_state(group_id) ON DELETE CASCADE,
            signal_date TEXT NOT NULL,mature_ordinal INTEGER NOT NULL,PRIMARY KEY(group_id,signal_date),
            UNIQUE(group_id,mature_ordinal))""",
        """CREATE TABLE calibrations(calibration_version TEXT PRIMARY KEY,group_id TEXT NOT NULL REFERENCES group_state(group_id),
            mature_count INTEGER NOT NULL,payload_json TEXT NOT NULL)""",
        """CREATE TABLE retrain_tokens(token TEXT PRIMARY KEY,group_id TEXT NOT NULL REFERENCES group_state(group_id),
            mature_count INTEGER NOT NULL,created_at TEXT NOT NULL,consumed_at TEXT)""",
        "CREATE INDEX idx_v2_pending_scope ON predictions(market,stock_id,price_basis,target_date,signal_date) WHERE observation_status IN ('pending','unobserved')",
        "CREATE INDEX idx_v2_calibration_raw ON predictions(group_id,signal_date,stock_id,recorded_at,record_id) WHERE observation_status='observed' AND prospective_eligible=1",
        "CREATE INDEX idx_v2_prediction_binding ON predictions(binding_id)",
        "CREATE INDEX idx_v2_settlement_binding ON predictions(settlement_binding_id)",
        "CREATE INDEX idx_v2_calibration_latest ON calibrations(group_id,mature_count DESC)",
    )
    for statement in statements:
        conn.execute(statement)


def _compact_snapshot(value):
    _require(isinstance(value, Mapping) and bool(value), "source_snapshot_binding_missing")
    result = {"caller_verified_binding_sha256": _hash(value)}
    for key in ("snapshot_fingerprint", "asof", "producer_id", "run_id", "head_sha256", "head_size"):
        item = value.get(key)
        if isinstance(item, (str, int, float, bool)) or item is None:
            if key in value:
                result[key] = item
    calendar = value.get("calendar_evidence", value.get("calendar_binding"))
    if calendar is not None:
        result["calendar_binding_sha256"] = _hash(calendar)
    return result


def _store_binding(conn, binding, semantic, profile="C0"):
    payload = {"profile_id": profile, "effective_C0_semantics_projection_sha256": semantic,
        "model_sha256": binding.get("model_sha256"), "price_artifact_sha256": binding.get("price_artifact_sha256"),
        "price_basis": binding.get("price_basis", "adjusted_close"), "feature_schema": binding.get("feature_schema", FEATURE_SCHEMA),
        "feature_order": binding.get("feature_order", list(FEATURE_ORDER)),
        "original_training_anchor": binding.get("policy_binding", binding.get("original_training_anchor")),
        "current_policy_binding": binding.get("current_policy_binding"),
        "contract_adoption_ref": binding.get("contract_adoption_ref"),
        "snapshot_binding": _compact_snapshot(binding.get("snapshot_binding", binding.get("source_binding", {})))}
    for key in ("price_scope_start", "price_scope_end", "asof", "calendar_verified_by_market"):
        if key in binding:
            payload[key] = binding[key]
    bid = _hash(payload)
    conn.execute("INSERT OR IGNORE INTO bindings(binding_id,profile_id,semantics_sha256,binding_json) VALUES (?,?,?,?)",
                 (bid, profile, semantic, _json(payload)))
    return bid


def _scientific_prediction(row, group):
    return _hash({"group_id": group["group_id"], "stock_id": row["stock_id"], "signal_date": row["signal_date"],
        "calibration_version": row["calibration_version"], "model_sha256": row["model_sha256"],
        "price_artifact_sha256": row["price_artifact_sha256"],
        "raw_probability": row["raw_probability"], "final_probability": row["final_probability"],
        "raw_return": row["raw_return"], "final_return": row["final_return"],
        "direction_kind": row["direction_kind"], "signal_close": row["signal_close"]})


def _insert_prediction(conn, row):
    columns = ("record_id", "group_id", "base_model_version", "model_sha256", "price_artifact_sha256", "stock_id", "market", "signal_date",
        "horizon", "price_basis", "calibration_version", "raw_probability", "final_probability", "raw_return", "final_return",
        "direction_kind", "signal_close", "original_ordinary", "target_date", "recorded_at", "recorded_date", "record_kind", "prospective_eligible",
        "prospective_reason", "immutable_sha256", "scientific_comparison_sha256", "binding_id", "observation_status",
        "target_close", "observed_return", "observed_up", "settlement_binding_id", "settled_at")
    conn.execute("INSERT INTO predictions(" + ",".join(columns) + ") VALUES (" + ",".join("?" for _ in columns) + ")",
                 tuple(row.get(column) for column in columns))


def _migrate_v1(conn, refs):
    refs = _normalize_refs(refs, _now())
    source_counts = {table: conn.execute("SELECT COUNT(*) FROM " + table).fetchone()[0]
                     for table in ("predictions", "group_state", "calibrations", "retrain_tokens")}
    verified_mature_days = 0
    for table in ("predictions", "group_state", "calibrations", "retrain_tokens"):
        conn.execute("ALTER TABLE " + table + " RENAME TO old_v1_" + table)
    _create_schema(conn)
    group_rows = conn.execute("""SELECT base_model_version,horizon,market,price_basis,MAX(model_sha256) AS model_sha256,MIN(model_sha256) AS first_model_sha256
        FROM old_v1_predictions GROUP BY base_model_version,horizon,market,price_basis""").fetchall()
    original_states = {r["group_id"]: dict(r) for r in conn.execute("SELECT * FROM old_v1_group_state")}
    for row in group_rows:
        _require(row["model_sha256"] == row["first_model_sha256"], "legacy_group_contains_multiple_model_artifacts")
        gid = _group_id(row["base_model_version"], row["horizon"], row["market"], row["price_basis"])
        role, ref = _ref_for_base(refs, row["base_model_version"])
        _require(ref is None or row["model_sha256"] == ref["model_sha256"], "legacy_model_reference_sha_mismatch")
        epoch = ref["model_activation_epoch"] if ref else "legacy-retired-" + gid
        semantic = ref["effective_C0_semantics_projection_sha256"] if ref else "unknown"
        dates = [r[0] for r in conn.execute("""SELECT DISTINCT signal_date FROM old_v1_predictions
            WHERE base_model_version=? AND horizon=? AND market=? AND price_basis=?
            AND observation_status='observed' AND prospective_eligible=1 ORDER BY signal_date""",
            (row["base_model_version"], row["horizon"], row["market"], row["price_basis"]))]
        state = original_states.get(gid, {})
        count = len(dates)
        verified_mature_days += count
        for field in ("calibration_cursor", "retrain_cursor", "seen_cursor"):
            value = state.get(field, 0)
            _require(type(value) is int and 0 <= value <= count, "legacy_cursor_exceeds_verified_maturity_" + field)
        sealed = dates[-MAX_CALIBRATION_DAYS - 1] if count > MAX_CALIBRATION_DAYS else None
        conn.execute("""INSERT INTO group_state(group_id,base_model_version,model_sha256,activation_epoch,profile_id,
            semantics_sha256,horizon,market,price_basis,group_role,legacy_group_alias,record_id_scheme,
            lifetime_mature_count,sealed_signal_date,calibration_cursor,retrain_cursor,seen_cursor)
            VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
            (gid, row["base_model_version"], row["model_sha256"], epoch, "C0", semantic, row["horizon"], row["market"],
             row["price_basis"], role, gid, "legacy_v1", count, sealed, state.get("calibration_cursor", 0),
             state.get("retrain_cursor", 0), state.get("seen_cursor", 0)))
        conn.execute("INSERT INTO group_aliases(alias_group_id,group_id) VALUES (?,?)", (gid, gid))
        for ordinal, day in enumerate(dates, 1):
            if ordinal > count - MAX_CALIBRATION_DAYS:
                conn.execute("INSERT INTO mature_days(group_id,signal_date,mature_ordinal) VALUES (?,?,?)", (gid, day, ordinal))
    for old in conn.execute("SELECT * FROM old_v1_predictions").fetchall():
        row = dict(old)
        gid = _group_id(row["base_model_version"], row["horizon"], row["market"], row["price_basis"])
        group = conn.execute("SELECT * FROM group_state WHERE group_id=?", (gid,)).fetchone()
        original = json.loads(row.pop("original_json"))
        legacy_binding = original["bindings"]
        _require(legacy_binding.get("base_model_version") == row["base_model_version"] and
                 legacy_binding.get("model_sha256") == row["model_sha256"] and
                 legacy_binding.get("price_basis") == row["price_basis"] and
                 legacy_binding.get("feature_schema") == FEATURE_SCHEMA and
                 list(legacy_binding.get("feature_order", [])) == list(FEATURE_ORDER) and
                 legacy_binding.get("policy_binding", {}).get("profile_id") == "C0", "legacy_scientific_binding_mismatch")
        _sha(legacy_binding.get("price_artifact_sha256"), "legacy_price_artifact_sha256")
        row["original_ordinary"] = 1 if original["bindings"].get("original_ordinary_verified") is True else None
        row["price_artifact_sha256"] = original["bindings"]["price_artifact_sha256"]
        row["group_id"] = gid
        row["binding_id"] = _store_binding(conn, original["bindings"], group["semantics_sha256"])
        settlement = row.pop("settlement_json")
        row["settlement_binding_id"] = _store_binding(conn, json.loads(settlement), group["semantics_sha256"]) if settlement else None
        row["scientific_comparison_sha256"] = _scientific_prediction(row, group)
        _insert_prediction(conn, row)
    for table, columns in (("calibrations", "calibration_version,group_id,mature_count,payload_json"),
                           ("retrain_tokens", "token,group_id,mature_count,created_at,consumed_at")):
        for row in conn.execute("SELECT " + columns + " FROM old_v1_" + table).fetchall():
            exists = conn.execute("SELECT 1 FROM group_state WHERE group_id=?", (row[1],)).fetchone()
            _require(exists is not None, "orphan_legacy_" + table)
            conn.execute("INSERT INTO " + table + "(" + columns + ") VALUES (" + ",".join("?" for _ in row) + ")", tuple(row))
    for group in conn.execute("SELECT group_id FROM group_state").fetchall():
        issued = conn.execute("SELECT token,mature_count FROM retrain_tokens WHERE group_id=? ORDER BY mature_count DESC LIMIT 1", (group[0],)).fetchone()
        consumed = conn.execute("SELECT token,mature_count FROM retrain_tokens WHERE group_id=? AND consumed_at IS NOT NULL ORDER BY mature_count DESC LIMIT 1", (group[0],)).fetchone()
        if issued:
            conn.execute("UPDATE group_state SET last_issued_token_hash=?,last_issued_ordinal=? WHERE group_id=?", (issued[0], issued[1], group[0]))
        if consumed:
            conn.execute("UPDATE group_state SET last_consumed_token_hash=?,last_consumed_ordinal=? WHERE group_id=?", (consumed[0], consumed[1], group[0]))
    for table in ("predictions", "calibrations", "retrain_tokens", "group_state"):
        conn.execute("DROP TABLE old_v1_" + table)
    for table in ("predictions", "calibrations", "retrain_tokens"):
        _require(conn.execute("SELECT COUNT(*) FROM " + table).fetchone()[0] == source_counts[table],
                 "migration_row_count_mismatch_" + table)
    _require(not conn.execute("PRAGMA foreign_key_check").fetchone(), "migration_foreign_key_violation")
    conn.execute("PRAGMA user_version=2")
    _put_meta(conn, "migration", {"from": 1, "to": 2, "completed_at": _now().isoformat(), "cursor_reset": False,
        "source_counts": source_counts, "derived_group_count": len(group_rows),
        "verified_mature_signal_days_across_groups": verified_mature_days, "consistent_snapshot_counts_verified": True})


def _prepare_schema(conn, refs):
    version = conn.execute("PRAGMA user_version").fetchone()[0]
    _require(version in (0, 1, DB_VERSION), "foreign_or_unsupported_forward_database")
    if version == 0:
        _require(not conn.execute("SELECT 1 FROM sqlite_master WHERE type='table'").fetchone(), "foreign_forward_database")
        _create_schema(conn)
        conn.execute("PRAGMA user_version=2")
    elif version == 1:
        _require(refs is not None, "v1_migration_requires_verified_retention_refs")
        _migrate_v1(conn, refs)


def _sync_refs(conn, refs, now, labels=None):
    refs = _normalize_refs(refs, now)
    floor = _meta(conn, "retired_activation_epoch_floor")
    if floor is not None:
        for role in ("current", "rollback", "pending"):
            ref = refs[role]
            if ref is None:
                continue
            exists = conn.execute("""SELECT 1 FROM group_state WHERE base_model_version=? AND activation_epoch=?
                AND semantics_sha256=? LIMIT 1""", (ref["base_model_version"], ref["model_activation_epoch"],
                                                   ref["effective_C0_semantics_projection_sha256"])).fetchone()
            if exists is None and _timestamp(ref["model_activation_epoch"]) <= _timestamp(floor):
                # A fully retired (pruned) model is used again, e.g. only an old
                # model is available. Analysis continues; its forecasts are not
                # written to the ledger and never count toward forward statistics.
                ref.setdefault("forward_isolation", []).append({"code": "fully_retired_model_reused",
                    "recorded": floor, "actual": ref["model_activation_epoch"],
                    "detail": "fully_retired_model_requires_new_activation_epoch"})
    _ref_labels(refs, labels)
    for group in conn.execute("SELECT * FROM group_state").fetchall():
        role = _role_for_group(group, refs)
        if group["group_role"] != role:
            conn.execute("UPDATE group_state SET group_role=? WHERE group_id=?", (role, group["group_id"]))
    _put_meta(conn, "retention_refs", refs)
    _put_meta(conn, "retention_refs_sha256", _hash(refs))
    return refs


def sync_retention_refs(retention_refs, *, db_path=DEFAULT_DB_PATH):
    """Verified caller refs only; migrate once, never fit or reset identities."""
    labels = []
    with _transaction(db_path, retention_refs, labels) as conn:
        result = {"schema_version": DB_VERSION, "retention_refs_sha256": _meta(conn, "retention_refs_sha256"),
                  "administrative_migration_resets_identity": False,
                  "forward_write_isolated": bool((_meta(conn, "retention_refs") or {}).get("current", {}).get("forward_isolation"))}
    result["version_labels"] = labels
    return result


@contextmanager
def _readonly(db_path):
    conn = sqlite3.connect(Path(db_path).resolve().as_uri() + "?mode=ro", uri=True, timeout=30)
    conn.row_factory = sqlite3.Row
    try:
        _require(conn.execute("PRAGMA user_version").fetchone()[0] in (1, 2), "foreign_or_unsupported_forward_database")
        conn.execute("BEGIN")
        yield conn
    finally:
        conn.close()


def _write_isolation(conn, binding, labels):
    """Reasons why these forecasts must not enter the current group (empty: write).

    The calibration sample reads only the group, so an unverified, retired or
    non-current model would contaminate it. Such forecasts are labelled, not
    written, and are excluded from forward statistics; the analysis continues."""
    refs = _meta(conn, "retention_refs")
    _require(refs is not None, "verified_retention_refs_missing")
    current = refs["current"]
    reasons = []
    for field in ("base_model_version", "model_sha256", "model_activation_epoch", "effective_C0_semantics_projection_sha256"):
        if binding[field] != current[field]:
            reasons.append("forecast_not_current_model_" + field)
            _label(labels, "forecast_not_current_model_" + field, recorded=current[field], actual=binding[field],
                   detail={"forward_write": "skipped", "forward_statistics": "excluded"})
    if not reasons:
        for item in current.get("forward_isolation") or []:
            reasons.append(item.get("code") if isinstance(item, Mapping) else str(item))
    return reasons


def _ensure_group(conn, binding, horizon, market):
    refs = _meta(conn, "retention_refs")
    _require(refs is not None, "verified_retention_refs_missing")
    current = refs["current"]
    for field in ("base_model_version", "model_sha256", "model_activation_epoch", "effective_C0_semantics_projection_sha256"):
        # Callers check _write_isolation first; this is an internal invariant.
        _require(binding[field] == current[field], "forecast_not_current_model_" + field)
    key = (binding["base_model_version"], binding["model_activation_epoch"], "C0",
           binding["effective_C0_semantics_projection_sha256"], horizon, market, binding["price_basis"])
    row = conn.execute("""SELECT * FROM group_state WHERE base_model_version=? AND activation_epoch=?
        AND profile_id=? AND semantics_sha256=? AND horizon=? AND market=? AND price_basis=?""", key).fetchone()
    if row:
        return row
    gid = _hash(list(key))
    conn.execute("""INSERT INTO group_state(group_id,base_model_version,model_sha256,activation_epoch,profile_id,
        semantics_sha256,horizon,market,price_basis,group_role,record_id_scheme) VALUES (?,?,?,?,?,?,?,?,?,?,?)""",
        (gid, key[0], binding["model_sha256"], key[1], key[2], key[3], horizon, market, key[-1], "current", "scientific_v2"))
    return conn.execute("SELECT * FROM group_state WHERE group_id=?", (gid,)).fetchone()


def _mature_day(conn, group_id, day):
    group = conn.execute("SELECT * FROM group_state WHERE group_id=?", (group_id,)).fetchone()
    if group["sealed_signal_date"] is not None and day <= group["sealed_signal_date"]:
        return 0
    exists = conn.execute("SELECT 1 FROM mature_days WHERE group_id=? AND signal_date=?", (group_id, day)).fetchone()
    if exists:
        return 0
    ordinal = group["lifetime_mature_count"] + 1
    conn.execute("INSERT INTO mature_days(group_id,signal_date,mature_ordinal) VALUES (?,?,?)", (group_id, day, ordinal))
    conn.execute("UPDATE group_state SET lifetime_mature_count=? WHERE group_id=?", (ordinal, group_id))
    old = conn.execute("SELECT signal_date FROM mature_days WHERE group_id=? ORDER BY signal_date DESC LIMIT -1 OFFSET ?",
                       (group_id, MAX_CALIBRATION_DAYS)).fetchall()
    if old:
        sealed = max(row[0] for row in old)
        conn.execute("DELETE FROM mature_days WHERE group_id=? AND signal_date<=?", (group_id, sealed))
        conn.execute("UPDATE group_state SET sealed_signal_date=CASE WHEN sealed_signal_date IS NULL OR sealed_signal_date<? THEN ? ELSE sealed_signal_date END WHERE group_id=?",
                     (sealed, sealed, group_id))
    return 1


def _bindings(bindings):
    result = copy.deepcopy(dict(bindings))
    for field in ("base_model_version", "price_basis"):
        _require(isinstance(result.get(field), str) and bool(result[field]), "missing_" + field)
    for field in ("model_sha256", "price_artifact_sha256"):
        _sha(result.get(field), field)
    _require(result.get("price_basis") == "adjusted_close", "unsupported_price_basis")
    _require(result.get("feature_schema") == FEATURE_SCHEMA and
             list(result.get("feature_order", [])) == list(FEATURE_ORDER), "feature_contract_mismatch")
    for field in ("snapshot_binding", "policy_binding"):
        _require(isinstance(result.get(field), Mapping) and bool(result[field]), "missing_" + field)
    policy = result["policy_binding"]
    _require(policy.get("profile_id") == "C0", "model_profile_must_be_C0")
    for field in ("feature_version", "requirements_version"):
        _require(isinstance(policy.get(field), str) and bool(policy[field]), "missing_policy_" + field)
    for field in ("policy_sha256", "requirements_raw_sha256"):
        _sha(policy.get(field), "policy_" + field)
    result.setdefault("record_kind", "current_forecast")
    _require(result["record_kind"] in ("current_forecast", "legacy_forecast"), "invalid_record_kind")
    _sha(result.get("effective_C0_semantics_projection_sha256"), "effective_C0_semantics_projection_sha256")
    _require(isinstance(result.get("model_activation_epoch"), str) and bool(result["model_activation_epoch"]),
             "model_activation_epoch_missing")
    _json(result)
    return result


def _prospective(record_kind, recorded_at, target_date, has_prediction=True, *, market):
    if record_kind != "current_forecast":
        return 0, "legacy_record_not_forward"
    if not has_prediction:
        return 0, "prediction_unavailable"
    if target_date is None:
        return None, "target_session_not_yet_verified"
    _require(market in ("TWSE", "TPEX"), "prospective_market_open_unverified")
    actual = datetime.fromisoformat(recorded_at)
    _require(actual.tzinfo is not None and actual.utcoffset() is not None,
             "actual_recorded_timestamp_timezone_missing")
    # Ordinary TWSE and TPEx sessions start at 09:00 Asia/Taipei. The target
    # session date itself still comes only from the caller-verified market axis.
    # An overnight forecast based on the preceding close is genuinely forward
    # when its immutable actual timestamp precedes that target session's open.
    target_open = datetime.fromisoformat(target_date + "T09:00:00").replace(tzinfo=_TAIPEI)
    if actual < target_open:
        return 1, PROSPECTIVE_POLICY
    return 0, "recorded_at_or_after_target_session_open"


def record_predictions(batch, bindings, *, db_path=DEFAULT_DB_PATH, retention_refs=None):
    """Retain the actual displayed forecasts, including unavailable rows.

    Call after settling old rows and selecting an overlay. No backdated recording
    timestamp is accepted. Repeated identical forecasts do not create new rows.
    """
    binding = _bindings(bindings)
    now = _now().astimezone(timezone.utc)
    recorded_at, recorded_date = now.isoformat(), now.astimezone(_TAIPEI).date().isoformat()
    prepared, in_sample, labels = [], 0, []
    for row in _rows(batch):
        stock, market = str(row.get("stock_id", "")), str(row.get("market", ""))
        _require(bool(stock) and market in ("TWSE", "TPEX"), "invalid_stock_market")
        signal = _day(row.get("session_date"))
        _require(signal <= recorded_date, "future_signal_date")
        horizon = row.get("horizon", row.get("horizon_sessions"))
        _require(type(horizon) is int and horizon in (1, 5, 20, 60, 63), "invalid_horizon")
        target = row.get("target_session_date")
        if target is not None:
            target = _day(target)
            _require(target > signal, "target_not_after_signal")
        cal = str(row.get("calibration_version") or "none")
        _validate_row_base(row, binding["base_model_version"])
        probability = _number(row.get("prob_up"), "prob_up", nullable=True, probability=True)
        raw = _number(row.get("prob_up_uncalibrated", probability), "raw_prob_up", nullable=True, probability=True)
        expected = _number(row.get("expected_return"), "expected_return", nullable=True)
        raw_return = _number(row.get("expected_return_uncalibrated", expected), "raw_expected_return", nullable=True)
        _require((probability is None) == (raw is None), "incomplete_probability_pair")
        signal_close = _number(row.get("signal_close", row.get("adjusted_close")), "signal_close", nullable=True, positive=True)
        _require(probability is None or binding["record_kind"] != "current_forecast" or signal_close is not None,
                 "forward_signal_close_scalar_required")
        eligible, reason = _prospective(binding["record_kind"], recorded_at, target, probability is not None,
                                        market=market)
        flag = row.get("in_sample_lookahead")
        # numpy.bool_ (rows not built by DataFrame.to_dict) must not slip through.
        if flag is True or (type(flag).__module__ == "numpy" and type(flag).__name__ in ("bool", "bool_") and bool(flag)):
            # Data older than the model's training asof: an in-sample display
            # forecast, never a forward observation (plan open question 1).
            in_sample += 1
            continue
        prepared.append({"base_model_version": binding["base_model_version"], "model_sha256": binding["model_sha256"],
            "price_artifact_sha256": binding["price_artifact_sha256"],
            "stock_id": stock, "market": market, "signal_date": signal, "horizon": horizon,
            "price_basis": binding["price_basis"], "calibration_version": cal, "raw_probability": raw,
            "final_probability": probability, "raw_return": raw_return, "final_return": expected,
            "direction_kind": row.get("selected_direction_kind"), "signal_close": signal_close,
            "original_ordinary": 1 if binding.get("original_ordinary_verified") is True else None, "target_date": target,
            "recorded_at": recorded_at, "recorded_date": recorded_date, "record_kind": binding["record_kind"],
            "prospective_eligible": eligible, "prospective_reason": reason,
            "observation_status": "pending" if probability is not None else "prediction_unavailable"})
    result = {"schema": SCHEMA, "schema_version": DB_VERSION, "inserted": 0, "duplicates": 0,
              "outside_retention": 0, "recorded_at": recorded_at, "record_ids": [],
              "forward_write": "written", "skipped_isolated": 0, "skipped_in_sample_lookahead": in_sample,
              "conflicting_repeat": 0, "version_labels": labels}
    if in_sample:
        _label(labels, "in_sample_lookahead_not_recorded", actual=in_sample,
               detail={"forward_write": "skipped", "forward_statistics": "excluded"})
    if not prepared:
        if in_sample:
            result["forward_write"] = "skipped"
        return result
    with _transaction(db_path, retention_refs, labels) as conn:
        isolation = _write_isolation(conn, binding, labels)
        if isolation:
            result.update(forward_write="skipped", skipped_isolated=len(prepared), forward_isolation=sorted(set(isolation)))
            return result
        bid = None
        groups = {}
        for row in prepared:
            key = (row["horizon"], row["market"])
            if key not in groups:
                groups[key] = _ensure_group(conn, binding, *key)
            group = groups[key]
            row["group_id"] = group["group_id"]
            identity = ([row["base_model_version"], row["stock_id"], row["market"], row["signal_date"],
                         row["horizon"], row["price_basis"], row["calibration_version"]]
                        if group["record_id_scheme"] == "legacy_v1" else
                        [group["group_id"], row["stock_id"], row["signal_date"], row["calibration_version"]])
            rid = _hash(identity)
            row["record_id"] = rid
            digest = _scientific_prediction(row, group)
            previous = conn.execute("SELECT scientific_comparison_sha256,target_date FROM predictions WHERE record_id=?", (rid,)).fetchone()
            if previous:
                conflict = None
                if previous["scientific_comparison_sha256"] != digest:
                    conflict = "conflicting_repeat_prediction"
                elif not (previous["target_date"] is None or row["target_date"] is None or
                          previous["target_date"] == row["target_date"]):
                    conflict = "conflicting_repeat_target_session"
                if conflict is not None:
                    # Data was revised since the original recording (adjusted
                    # prices, calendar). The original immutable forecast is kept.
                    result["conflicting_repeat"] += 1
                    _row_marker(result, "conflicting_repeat", rid, conflict)
                    continue
                result["duplicates"] += 1
            elif group["sealed_signal_date"] is not None and row["signal_date"] <= group["sealed_signal_date"]:
                result["outside_retention"] += 1
                continue
            else:
                if bid is None:
                    bid = _store_binding(conn, binding, binding["effective_C0_semantics_projection_sha256"])
                row.update(binding_id=bid, scientific_comparison_sha256=digest, immutable_sha256=digest)
                _insert_prediction(conn, row)
                result["inserted"] += 1
            result["record_ids"].append(rid)
    if result["conflicting_repeat"]:
        _label(labels, "conflicting_repeat", actual=result["conflicting_repeat"],
               detail={"original_forecast_kept": True, "row_markers": "conflicting_repeat"})
    return result


def _expiry(row, axis, now, positions=None):
    hard = _timestamp(row["recorded_at"]) + timedelta(days=MAX_FORECAST_AGE_DAYS)
    positions = positions if positions is not None else {day: i for i, day in enumerate(axis)}
    target = row["target_date"]
    if target is None and row["signal_date"] in positions:
        pos = positions[row["signal_date"]] + row["horizon"]
        if pos < len(axis):
            target = axis[pos]
    expiry = hard
    if target is not None and target in positions:
        pos = positions[target] + ENDPOINT_GRACE_SESSIONS
        if pos < len(axis):
            grace = datetime.fromisoformat(axis[pos] + "T00:00:00").replace(tzinfo=_TAIPEI) + timedelta(days=1)
            expiry = min(expiry, grace.astimezone(timezone.utc))
    reason = "expired_target_unverified" if target is None else "expired_endpoint_unobserved"
    return now >= expiry, expiry.isoformat(), reason, target


def _axes(market_sessions):
    _require(isinstance(market_sessions, Mapping), "verified_market_axes_missing")
    output = {}
    for market, values in market_sessions.items():
        _require(market in ("TWSE", "TPEX"), "unsupported_market_axis")
        axis = [_day(value) for value in values]
        _require(axis == sorted(set(axis)), "invalid_market_session_axis")
        output[market] = axis
    return output


def settle_predictions(market_sessions, prices, *, asof, scope_pairs, evidence, db_path=DEFAULT_DB_PATH, retention_refs=None):
    """Settle exact Hth sessions only for explicitly verified queried pairs."""
    asof = _day(asof)
    _require(isinstance(evidence, Mapping) and evidence.get("snapshot_verified") is True and
             evidence.get("price_scope_verified") is True, "settlement_scope_unverified")
    _require(isinstance(evidence.get("source_binding"), Mapping) and bool(evidence["source_binding"]),
             "settlement_source_binding_missing")
    artifact = _sha(evidence.get("price_artifact_sha256"), "price_artifact_sha256")
    exact_ids = evidence.get("forward_settlement_record_ids")
    if exact_ids is not None:
        _require(isinstance(exact_ids, (list, tuple)), "exact_settlement_record_ids_must_be_list")
        _require(len(exact_ids) <= MAX_EXACT_REFS, "unbounded_exact_settlement_record_ids")
        exact_ids = sorted({_sha(value, "exact_settlement_record_id") for value in exact_ids})
    basis = evidence.get("price_basis")
    _require(basis == "adjusted_close", "unsupported_price_basis")
    start, end = _day(evidence.get("price_scope_start")), _day(evidence.get("price_scope_end"))
    _require(start <= end <= asof, "invalid_settlement_price_window")
    pairs = {(str(p[0]), str(p[1])) for p in scope_pairs}
    _require(all(stock and market in ("TWSE", "TPEX") for stock, market in pairs), "invalid_scope_pairs")
    verified = evidence.get("calendar_verified_by_market", {})
    axes = _axes(market_sessions)
    positions = {market: {day: i for i, day in enumerate(axis)} for market, axis in axes.items()}
    for market in {p[1] for p in pairs}:
        _require(verified.get(market) is True and market in market_sessions, "settlement_market_calendar_unverified")
    lookup = {}
    for row in _rows(prices):
        pair = (str(row.get("stock_id", "")), str(row.get("market", "")))
        _require(pair in pairs, "price_row_outside_verified_query_scope")
        day = _day(row.get("session_date"))
        _require(start <= day <= end, "price_row_outside_verified_query_window")
        value = _number(row.get("adjusted_close"), "adjusted_close", positive=True)
        key = pair + (day,)
        _require(key not in lookup or lookup[key] == value, "conflicting_price_endpoint")
        lookup[key] = value
    labels = []
    result = {"observed": 0, "unobserved": 0, "waiting": 0, "out_of_scope": 0,
              "expired": 0, "outside_retention": 0, "new_mature_signal_days": 0,
              "calendar_changed": 0, "basis_changed": 0, "exact_excluded": 0, "version_labels": labels}
    if exact_ids == []:
        # Explicitly no accepted targets must never fall back to pair/window
        # scope. It also need not acquire a writer or initialize a schema.
        return result
    if not Path(db_path).exists():
        _require(exact_ids is None, "exact_settlement_records_database_missing")
        return result
    now = _now().astimezone(timezone.utc)
    _require(asof <= now.astimezone(_TAIPEI).date().isoformat(), "settlement_asof_in_future")
    with _transaction(db_path, retention_refs, labels) as conn:
        conn.execute("CREATE TEMP TABLE IF NOT EXISTS verified_scope(market TEXT NOT NULL,stock_id TEXT NOT NULL,PRIMARY KEY(market,stock_id))")
        conn.execute("DELETE FROM verified_scope")
        conn.executemany("INSERT INTO verified_scope(market,stock_id) VALUES (?,?)", ((market, stock) for stock, market in pairs))
        if exact_ids is None:
            pending = conn.execute(_SETTLEMENT_PENDING_SQL, (basis,)).fetchall()
        else:
            conn.execute("CREATE TEMP TABLE verified_record_scope(record_id TEXT PRIMARY KEY)")
            conn.executemany("INSERT INTO verified_record_scope VALUES (?)", ((rid,) for rid in exact_ids))
            selected = conn.execute(_SETTLEMENT_EXACT_RECORD_SQL).fetchall()
            _require({row["record_id"] for row in selected} == set(exact_ids), "exact_settlement_record_ids_unknown")
            targets, accepted = set(), []
            # Validate the complete selected scope before writing any label.
            # A consumer may have settled a selected record first; that race
            # remains idempotent, but it does not authorize another pending ID.
            # A single non-conforming record is excluded (row marker); the DB's
            # own group-binding consistency still fails the whole call.
            for row in selected:
                pair = (row["stock_id"], row["market"])
                _require(row["profile_id"] == "C0" and all(row[field] == row["group_" + field] for field in
                    ("base_model_version", "model_sha256", "horizon", "market", "price_basis")),
                    "exact_settlement_group_binding_mismatch")
                excluded = None
                target = None
                if not (pair in pairs and row["price_basis"] == basis):
                    excluded = "exact_settlement_pair_or_basis_mismatch"
                elif not (row["record_kind"] == "current_forecast" and row["original_ordinary"] == 1 and
                          row["raw_probability"] is not None and row["signal_close"] is not None):
                    excluded = "exact_settlement_original_forecast_unverified"
                else:
                    index, axis = positions[row["market"]], axes[row["market"]]
                    pos = index.get(row["signal_date"])
                    if pos is None or pos + row["horizon"] >= len(axis):
                        excluded = "exact_settlement_target_calendar_unverified"
                    else:
                        target = axis[pos + row["horizon"]]
                        if not ((row["target_date"] is None or row["target_date"] == target) and start <= target <= end <= asof):
                            excluded = "exact_settlement_target_mismatch"
                        elif _prospective(row["record_kind"], row["recorded_at"], target, market=row["market"])[0] != 1:
                            excluded = "exact_settlement_forecast_not_forward"
                if excluded is not None:
                    result["exact_excluded"] += 1
                    _row_marker(result, "exact_excluded", row["record_id"], excluded)
                    continue
                targets.add(pair + (target,))
                accepted.append(row)
            outside = sorted(set(lookup).difference(targets))
            if outside:
                # Price endpoints not belonging to an accepted record are ignored.
                for key in outside:
                    lookup.pop(key)
                _label(labels, "exact_settlement_price_outside_accepted_target", actual=len(outside),
                       detail={"ignored_price_endpoints": [list(key) for key in outside[:MAX_LABEL_RECORD_IDS]]})
            if result["exact_excluded"]:
                _label(labels, "exact_settlement_record_excluded", actual=result["exact_excluded"],
                       detail={"row_markers": "exact_excluded"})
            pending = [row for row in accepted if row["observation_status"] in ("pending", "unobserved")]
        binding_cache = {}
        for row in pending:
            pair = (row["stock_id"], row["market"])
            axis = axes[row["market"]]
            expired, _, _, _ = _expiry(row, axis, now, positions[row["market"]])
            if expired:
                result["expired"] += 1
                continue
            if row["sealed_signal_date"] is not None and row["signal_date"] <= row["sealed_signal_date"]:
                result["outside_retention"] += 1
                continue
            pos = positions[row["market"]].get(row["signal_date"])
            if pos is None or pos + row["horizon"] >= len(axis):
                result["waiting"] += 1
                continue
            target = axis[pos + row["horizon"]]
            if not (row["target_date"] is None or row["target_date"] == target):
                # Calendar revised since recording: leave this row untouched
                # (not settled, not matured, not calibrated); others proceed.
                result["calendar_changed"] += 1
                _row_marker(result, "calendar_changed", row["record_id"], [row["target_date"], target])
                continue
            eligible, reason = _prospective(row["record_kind"], row["recorded_at"], target, market=row["market"])
            if target > asof or target < start or target > end:
                if (row["target_date"], row["prospective_eligible"], row["prospective_reason"]) != (target, eligible, reason):
                    conn.execute("UPDATE predictions SET target_date=?,prospective_eligible=?,prospective_reason=? WHERE record_id=?",
                                 (target, eligible, reason, row["record_id"]))
                result["waiting"] += 1
                continue
            signal = row["signal_close"]
            close = lookup.get(pair + (target,))
            current_signal = lookup.get(pair + (row["signal_date"],))
            if (current_signal is not None and signal is not None and
                    not math.isclose(current_signal, signal, rel_tol=1e-10, abs_tol=1e-10)):
                # Adjusted-price basis changed (e.g. ex-dividend back-adjustment):
                # target/recorded-signal would mix bases. Leave this row pending
                # (never observed, matured or calibrated); others settle.
                result["basis_changed"] += 1
                _row_marker(result, "basis_changed", row["record_id"], [signal, current_signal])
                continue
            observed = signal is not None and close is not None
            outcome = close / signal - 1 if observed else None
            status = "observed" if observed else "unobserved"
            if (row["target_date"], row["prospective_eligible"], row["observation_status"], row["target_close"]) != (target, eligible, status, close):
                cache_key = (row["semantics_sha256"], row["profile_id"])
                if cache_key not in binding_cache:
                    binding_cache[cache_key] = _store_binding(conn, evidence, *cache_key)
                conn.execute("""UPDATE predictions SET target_date=?,prospective_eligible=?,prospective_reason=?,
                    observation_status=?,target_close=?,observed_return=?,observed_up=?,settlement_binding_id=?,settled_at=?
                    WHERE record_id=?""", (target, eligible, reason, status, close, outcome,
                    int(outcome > FLAT_TOLERANCE) if observed else None, binding_cache[cache_key], now.isoformat(), row["record_id"]))
            if observed and eligible == 1:
                result["new_mature_signal_days"] += _mature_day(conn, row["group_id"], row["signal_date"])
            result["observed" if observed else "unobserved"] += 1
    for kind, code in (("calendar_changed", "settlement_calendar_changed"), ("basis_changed", "settlement_basis_changed")):
        if result[kind]:
            _label(labels, code, actual=result[kind],
                   detail={"row_markers": kind, "settled": False, "forward_statistics": "excluded"})
    return result


def _group_id(base, horizon, market, basis):
    return _hash([base, horizon, market, basis])


def _sigmoid(value):
    if value >= 0:
        return 1 / (1 + math.exp(-min(value, 700)))
    e = math.exp(max(value, -700))
    return e / (1 + e)


def _logit(probability):
    probability = min(max(probability, 1e-6), 1 - 1e-6)
    return math.log(probability / (1 - probability))


def _platt(samples):
    xs = [_logit(r["raw_probability"]) for r in samples]
    ys = [r["observed_up"] for r in samples]
    slope, intercept = 0.0, _logit(sum(ys) / len(ys))
    # Fixed bounded Newton iterations, two coefficients, no base-model refit.
    for _ in range(25):
        ga, gb, haa, hab, hbb = 1e-4 * slope, 0.0, 1e-4, 0.0, 1e-8
        for x, y in zip(xs, ys):
            p = _sigmoid(slope * x + intercept)
            residual, weight = p - y, max(p * (1 - p), 1e-8)
            ga += residual * x
            gb += residual
            haa += weight * x * x
            hab += weight * x
            hbb += weight
        determinant = haa * hbb - hab * hab
        if determinant <= 1e-12:
            break
        da, db = (ga * hbb - gb * hab) / determinant, (gb * haa - ga * hab) / determinant
        step = max(1.0, abs(da) / 5, abs(db) / 5)
        slope -= da / step
        intercept -= db / step
        if max(abs(da), abs(db)) < 1e-7:
            break
    return {"slope": slope, "intercept": intercept}


def process_maturity(*, db_path=DEFAULT_DB_PATH, retention_refs=None):
    """Advance per-base/H/market cursors; issue no automatic training work."""
    labels = []
    result = {"new_mature_signal_days": 0, "calibration_events": [], "retrain_tokens": [], "no_work": True,
              "version_labels": labels}
    if not Path(db_path).exists():
        return result
    with _transaction(db_path, retention_refs, labels) as conn:
        groups = conn.execute("""SELECT * FROM group_state WHERE group_role='current'
            AND lifetime_mature_count>seen_cursor""").fetchall()
        for group in groups:
            base, horizon, market, basis, count = (group[name] for name in
                ("base_model_version", "horizon", "market", "price_basis", "lifetime_mature_count"))
            gid, state = group["group_id"], group
            added = count - state["seen_cursor"]
            result["new_mature_signal_days"] += added
            if added <= 0:
                continue
            result["no_work"] = False
            if count - state["calibration_cursor"] >= CALIBRATE_DAYS:
                dates = [r[0] for r in conn.execute("SELECT signal_date FROM mature_days WHERE group_id=? ORDER BY signal_date DESC LIMIT ?",
                                                   (gid, MAX_CALIBRATION_DAYS))]
                # The same base forecast under subsequent overlays is one raw
                # forecast per stock/signal. Keep its first actual recording.
                _require(bool(dates), "maturity_counter_without_retained_dates")
                sample = [dict(row) for row in conn.execute(_CALIBRATION_SAMPLE_SQL, (gid,))]
                positives = sum(r["observed_up"] for r in sample)
                negatives = len(sample) - positives
                raw_values = {r["raw_probability"] for r in sample}
                payload = {"schema": CALIBRATION_SCHEMA, "base_model_version": base, "horizon": horizon,
                    "market": market, "price_basis": basis, "mature_signal_days": count, "group_id": gid,
                    "activation_epoch": group["activation_epoch"],
                    "effective_C0_semantics_projection_sha256": group["semantics_sha256"],
                    "sample_signal_days": len(dates), "sample_count": len(sample), "positive_count": positives,
                    "negative_count": negatives, "sample_first_signal_date": min(dates),
                    "sample_last_signal_date": max(dates), "sample_scope": "original_prospective_raw_predictions",
                    "sample_sha256": _hash([{key: r[key] for key in ("record_id", "stock_id", "signal_date",
                        "raw_probability", "observed_up", "observed_return")} for r in sample]),
                    "base_model_refitted": False, "reliability_verified": False, "parameters": {}}
                if len(sample) < 20 or not positives or not negatives:
                    payload.update(kind="uncalibrated", status="uncalibrated_insufficient_classes_or_samples")
                elif len(raw_values) == 1:
                    payload.update(kind="forward_frequency_calibration", status="reliability_unverified",
                                   parameters={"probability": positives / len(sample)})
                elif min(positives, negatives) >= 5:
                    payload.update(kind="forward_platt_calibration", status="reliability_unverified", parameters=_platt(sample))
                else:
                    payload.update(kind="uncalibrated", status="uncalibrated_insufficient_classes_or_samples")
                digest = _hash(payload)
                payload.update(calibration_sha256=digest, calibration_version="forward-" + digest[:24])
                conn.execute("INSERT INTO calibrations(calibration_version,group_id,mature_count,payload_json) VALUES (?,?,?,?)",
                             (payload["calibration_version"], gid, count, _json(payload)))
                conn.execute("UPDATE group_state SET calibration_cursor=? WHERE group_id=?", (count, gid))
                result["calibration_events"].append(payload)
            if count - state["retrain_cursor"] >= RETRAIN_DAYS:
                token = _hash({"group_id": gid, "mature_count": count, "kind": "manual_retrain_allow_once"})
                _revoke_old_tokens(conn, gid)
                conn.execute("INSERT INTO retrain_tokens(token,group_id,mature_count,created_at) VALUES (?,?,?,?)",
                             (token, gid, count, _now().isoformat()))
                conn.execute("UPDATE group_state SET retrain_cursor=?,last_issued_token_hash=?,last_issued_ordinal=? WHERE group_id=?",
                             (count, token, count, gid))
                result["retrain_tokens"].append({"token": token, "base_model_version": base, "horizon": horizon,
                    "market": market, "price_basis": basis, "mature_signal_days": count,
                    "retrain_eligible": True, "allow_once": True, "automatic_retraining": False})
            conn.execute("UPDATE group_state SET seen_cursor=? WHERE group_id=?", (count, gid))
    return result


def _revoke_old_tokens(conn, group_id, keep=None):
    rows = conn.execute("SELECT token,mature_count,consumed_at FROM retrain_tokens WHERE group_id=? ORDER BY mature_count DESC", (group_id,)).fetchall()
    revoked = False
    for row in rows:
        if row["token"] == keep:
            continue
        if row["consumed_at"] is None and not revoked:
            conn.execute("UPDATE group_state SET last_revoked_token_hash=?,last_revoked_ordinal=? WHERE group_id=?",
                         (row["token"], row["mature_count"], group_id))
            revoked = True
        conn.execute("DELETE FROM retrain_tokens WHERE token=?", (row["token"],))


def latest_calibration(base_model_version, horizon, market, price_basis, *, db_path=DEFAULT_DB_PATH):
    path = Path(db_path)
    if not path.exists():
        return None
    # This lookup is also used by read-only report verification. Never create
    # a database, initialize a schema, acquire a writer lock or advance cursors.
    with _readonly(path) as conn:
        version = conn.execute("PRAGMA user_version").fetchone()[0]
        if version == 1:
            row = conn.execute("SELECT payload_json FROM calibrations WHERE group_id=? ORDER BY mature_count DESC LIMIT 1",
                               (_group_id(base_model_version, horizon, market, price_basis),)).fetchone()
        else:
            row = conn.execute("""SELECT c.payload_json FROM calibrations AS c JOIN group_state AS g ON g.group_id=c.group_id
                WHERE g.base_model_version=? AND g.horizon=? AND g.market=? AND g.price_basis=? AND g.group_role='current'
                ORDER BY c.mature_count DESC LIMIT 1""", (base_model_version, horizon, market, price_basis)).fetchone()
    return json.loads(row[0]) if row else None


def _validate_row_base(row, base):
    for field in ("base_model_version", "raw_base_model_version", "model_sha256", "raw_model_sha256"):
        _require(row.get(field) is None or row[field] == base, "prediction_base_model_mismatch_" + field)
    for field in ("model_version", "raw_model_version"):
        _require(row.get(field) is None or (isinstance(row[field], str) and row[field].split("+", 1)[0] == base),
                 "prediction_base_model_mismatch_" + field)


def apply_forward_calibration(batch, calibration, *, base_model_version=None):
    """Apply one matching group overlay while preserving original raw values."""
    rows = _rows(batch)
    if calibration is not None:
        payload = copy.deepcopy(dict(calibration))
        digest, version = payload.pop("calibration_sha256", None), payload.pop("calibration_version", None)
        _require(payload.get("schema") == CALIBRATION_SCHEMA and _hash(payload) == digest and
                 version == "forward-" + digest[:24], "calibration_content_hash_mismatch")
        if base_model_version is None:
            base_model_version = payload["base_model_version"]
        _require(base_model_version == payload["base_model_version"], "calibration_base_model_mismatch")
    for row in rows:
        base = base_model_version or row.get("base_model_version")
        if base:
            # Caller metadata cannot relabel a forecast made by another base
            # model. Raw provenance is immutable even when a newer calibration
            # overlay replaces a previous overlay of the same base model.
            _validate_row_base(row, base)
            row["base_model_version"] = base
        row.setdefault("prob_up_uncalibrated", row.get("prob_up"))
        row.setdefault("expected_return_uncalibrated", row.get("expected_return"))
        row["calibration_version"] = "none"
        row["forward_calibration_status"] = "uncalibrated_no_forward_overlay"
        if base:
            row["model_version"] = base
        if calibration is None:
            continue
        _require(row.get("market") == payload["market"] and row.get("horizon", row.get("horizon_sessions")) == payload["horizon"],
                 "calibration_horizon_market_mismatch")
        row["calibration_version"] = version
        row["forward_calibration_status"] = payload["status"]
        row["forward_calibration_kind"] = payload["kind"]
        row["calibration_reliability_verified"] = False
        row["model_version"] = base + "+" + version
        probability = row.get("prob_up_uncalibrated")
        if probability is not None and payload["kind"] != "uncalibrated":
            probability = _number(probability, "raw_prob_up", probability=True)
            row["prob_up_before_forward_calibration"] = row.get("prob_up")
            if payload["kind"] == "forward_frequency_calibration":
                row["prob_up"] = payload["parameters"]["probability"]
            elif payload["kind"] == "forward_platt_calibration":
                row["prob_up"] = _sigmoid(payload["parameters"]["slope"] * _logit(probability) + payload["parameters"]["intercept"])
            else:
                raise ValueError("unsupported_calibration_kind")
    return rows


def consume_retrain_token(token, *, db_path=DEFAULT_DB_PATH, retention_refs=None):
    """Consume an existing manual eligibility token exactly once; never train."""
    _sha(token, "retrain_token")
    if not Path(db_path).exists():
        return {"token": token, "allow_once": False, "reason": "token_not_found"}
    with _transaction(db_path, retention_refs) as conn:
        row = conn.execute("""SELECT t.*,g.group_role,g.last_issued_token_hash FROM retrain_tokens AS t
            JOIN group_state AS g ON g.group_id=t.group_id WHERE t.token=?""", (token,)).fetchone()
        if row is None or row["consumed_at"] is not None or row["group_role"] != "current" or row["last_issued_token_hash"] != token:
            return {"token": token, "allow_once": False, "reason": "token_not_found_or_consumed"}
        conn.execute("UPDATE group_state SET last_consumed_token_hash=?,last_consumed_ordinal=? WHERE group_id=?",
                     (token, row["mature_count"], row["group_id"]))
        conn.execute("DELETE FROM retrain_tokens WHERE token=?", (token,))
    return {"token": token, "allow_once": True, "automatic_retraining": False}


def _owned_database(db_path, qa_override):
    path = Path(db_path).resolve()
    canonical = DEFAULT_DB_PATH.resolve()
    _require(path == canonical or (qa_override is True and path.is_relative_to(QA_ROOT.resolve())),
             "maintenance_database_outside_owned_path")
    return path


def _verified_axes(market_sessions, calendar_verified_by_market, labels=None):
    """Verified market axes. Maintenance (labels None) still fails on any
    unverified market; the read-only export drops that market with a label."""
    axes = _axes(market_sessions or {})
    flags = calendar_verified_by_market or {}
    if labels is None:
        _require(all(flags.get(market) is True for market in axes), "retention_market_calendar_unverified")
        return axes
    unverified = sorted(market for market in axes if flags.get(market) is not True)
    for market in unverified:
        _label(labels, "calendar_unverified", actual=flags.get(market),
               detail={"market": market, "pending_candidates": "omitted"})
        axes.pop(market)
    return axes


def _state_digest(conn, refs):
    # One bounded scalar scan per explicit maintenance plan. Daily maturity
    # work never uses this scan or hashes source files / repeated binding JSON.
    digest = hashlib.sha256(_json(refs).encode("utf-8"))
    queries = (
        "SELECT * FROM group_state ORDER BY group_id",
        "SELECT record_id,scientific_comparison_sha256,target_date,prospective_eligible,observation_status,target_close,settled_at FROM predictions ORDER BY record_id",
        "SELECT group_id,signal_date,mature_ordinal FROM mature_days ORDER BY group_id,signal_date",
        "SELECT calibration_version,group_id,mature_count FROM calibrations ORDER BY calibration_version",
        "SELECT token,group_id,mature_count,consumed_at FROM retrain_tokens ORDER BY token",
    )
    for query in queries:
        for row in conn.execute(query):
            digest.update(_json(list(row)).encode("utf-8"))
            digest.update(b"\n")
    return digest.hexdigest()


def _retention_plan(conn, axes, refs, now):
    refs = _normalize_refs(refs, now)
    groups = {row["group_id"]: dict(row) for row in conn.execute("SELECT * FROM group_state")}
    dates = {}
    for row in conn.execute("SELECT group_id,signal_date FROM mature_days ORDER BY group_id,signal_date"):
        dates.setdefault(row[0], set()).add(row[1])
    positions = {market: {day: i for i, day in enumerate(axis)} for market, axis in axes.items()}
    protected = set(refs["protected_record_ids"])
    retained, deleted, reasons, earliest, latest_day = set(), [], {}, {}, {}
    rows = [dict(row) for row in conn.execute("""SELECT record_id,group_id,stock_id,market,signal_date,horizon,
        recorded_at,record_kind,original_ordinary,target_date,signal_close,raw_probability,prospective_eligible,
        observation_status,calibration_version FROM predictions ORDER BY group_id,signal_date,stock_id,recorded_at,record_id""")]
    for row in rows:
        group = groups[row["group_id"]]
        role = _role_for_group(group, refs)
        if role != "retired":
            latest_day[row["group_id"]] = max(latest_day.get(row["group_id"], ""), row["signal_date"])
        key = (row["group_id"], row["stock_id"], row["signal_date"])
        if (role != "retired" and row["observation_status"] == "observed" and row["prospective_eligible"] == 1
                and row["signal_date"] in dates.get(row["group_id"], set())):
            earliest.setdefault(key, row["record_id"])
    for row in rows:
        group = groups[row["group_id"]]
        role = _role_for_group(group, refs)
        axis = axes.get(row["market"], [])
        expired, _, expiry_reason, _ = _expiry(row, axis, now, positions.get(row["market"], {}))
        key = (row["group_id"], row["stock_id"], row["signal_date"])
        sealed = group["sealed_signal_date"]
        if row["record_id"] in protected:
            keep = "exact_pending_or_checkpoint_reference"
        elif earliest.get(key) == row["record_id"]:
            keep = "latest_126_mature_signal_dates_earliest_raw"
        elif (not expired and row["record_kind"] == "current_forecast" and row["raw_probability"] is not None
              and row["signal_close"] is not None and row["prospective_eligible"] in (None, 1)
              and row["observation_status"] in ("pending", "unobserved") and (sealed is None or row["signal_date"] > sealed)):
            keep = "finite_true_forecast_pending"
        elif (not expired and role != "retired" and row["signal_date"] == latest_day.get(row["group_id"])
              and (row["observation_status"] != "observed" or row["prospective_eligible"] != 1)
              and (sealed is None or row["signal_date"] > sealed)):
            keep = "current_latest_signal_diagnostic"
        else:
            keep = None
        if keep:
            retained.add(row["record_id"])
        else:
            reason = expiry_reason if expired and row["observation_status"] in ("pending", "unobserved") else "outside_finite_current_purpose"
            deleted.append(row["record_id"])
            reasons[reason] = reasons.get(reason, 0) + 1
    keep_cal = set(refs["protected_calibration_versions"])
    _require(protected.issubset({row["record_id"] for row in rows}), "protected_forecast_reference_missing")
    for row in rows:
        if row["record_id"] in retained and row["calibration_version"] != "none":
            keep_cal.add(row["calibration_version"])
    all_cal = [dict(row) for row in conn.execute("SELECT calibration_version,group_id,mature_count FROM calibrations ORDER BY group_id,mature_count DESC,calibration_version")]
    _require(set(refs["protected_calibration_versions"]).issubset({row["calibration_version"] for row in all_cal}),
             "protected_calibration_reference_missing")
    cal_rank = {}
    for row in all_cal:
        if _role_for_group(groups[row["group_id"]], refs) in ("current", "rollback", "pending"):
            rank = cal_rank.get(row["group_id"], 0)
            if rank < 2:
                keep_cal.add(row["calibration_version"])
            cal_rank[row["group_id"]] = rank + 1
    delete_cal = [row["calibration_version"] for row in all_cal if row["calibration_version"] not in keep_cal]
    keep_groups = {groups[row["group_id"]]["group_id"] for row in rows if row["record_id"] in retained}
    keep_groups.update(row["group_id"] for row in all_cal if row["calibration_version"] in keep_cal)
    keep_groups.update(gid for gid, group in groups.items() if _role_for_group(group, refs) != "retired")
    delete_groups = sorted(set(groups).difference(keep_groups))
    delete_tokens = []
    for row in conn.execute("SELECT * FROM retrain_tokens ORDER BY group_id,mature_count DESC"):
        group = groups[row["group_id"]]
        if not (_role_for_group(group, refs) == "current" and row["consumed_at"] is None
                and row["token"] == group["last_issued_token_hash"]):
            delete_tokens.append(row["token"])
    plan = {"schema": RETENTION_PLAN_SCHEMA, "schema_version": DB_VERSION,
        "evaluated_at": now.isoformat(), "retention_refs_sha256": _hash(refs),
        "source_state_sha256": _state_digest(conn, refs), "calendar_axes_sha256": _hash(axes),
        "counts": {"predictions_before": len(rows), "predictions_keep": len(retained), "predictions_delete": len(deleted),
                   "calibrations_delete": len(delete_cal), "tokens_delete": len(delete_tokens), "groups_delete": len(delete_groups)},
        "delete_reason_counts": reasons, "delete_record_ids": sorted(deleted),
        "delete_calibration_versions": sorted(delete_cal), "delete_tokens": sorted(delete_tokens),
        "delete_group_ids": delete_groups, "physical_reclaim_performed": False,
        "unknown_expiry_creates_label": False, "lifetime_cursors_reset": False}
    plan["plan_sha256"] = _hash(plan)
    return plan


def retention_plan(market_sessions=None, *, calendar_verified_by_market=None, retention_refs=None, db_path=DEFAULT_DB_PATH):
    """Read-only, coherent deletion proposal; never migrate, fit or prune."""
    if not Path(db_path).exists():
        return {"schema": RETENTION_PLAN_SCHEMA, "database_exists": False, "migration_required": False, "no_work": True}
    axes = _verified_axes(market_sessions, calendar_verified_by_market)
    with _readonly(db_path) as conn:
        if conn.execute("PRAGMA user_version").fetchone()[0] == 1:
            return {"schema": RETENTION_PLAN_SCHEMA, "database_exists": True, "migration_required": True, "no_work": False}
        refs = retention_refs if retention_refs is not None else _meta(conn, "retention_refs")
        return _retention_plan(conn, axes, refs, _now())


def prune_retention(market_sessions=None, *, calendar_verified_by_market=None, retention_refs=None,
                    expected_plan=None, db_path=DEFAULT_DB_PATH, dry_run=True, qa_override=False):
    """Apply one reviewed finite plan in one transaction; any failure rolls back."""
    path = _owned_database(db_path, qa_override)
    if dry_run:
        return retention_plan(market_sessions, calendar_verified_by_market=calendar_verified_by_market,
                              retention_refs=retention_refs, db_path=path)
    _require(isinstance(expected_plan, Mapping), "reviewed_retention_plan_missing")
    payload = dict(expected_plan)
    digest = payload.pop("plan_sha256", None)
    _require(_hash(payload) == digest and payload.get("schema") == RETENTION_PLAN_SCHEMA, "retention_plan_hash_mismatch")
    axes = _verified_axes(market_sessions, calendar_verified_by_market)
    _require(_hash(axes) == payload["calendar_axes_sha256"], "retention_plan_calendar_changed")
    evaluated = _timestamp(payload["evaluated_at"])
    now = _now()
    _require(evaluated <= now <= evaluated + timedelta(minutes=10), "retention_plan_stale")
    with _transaction(path, retention_refs) as conn:
        refs = _normalize_refs(_meta(conn, "retention_refs"), now)
        _require(_hash(refs) == payload["retention_refs_sha256"] and
                 _state_digest(conn, refs) == payload["source_state_sha256"], "retention_plan_state_changed")
        # Keep the proposal's cutoff throughout the transaction. A target that
        # expires during these minutes waits for the next explicit plan.
        actual = _retention_plan(conn, axes, refs, evaluated)
        _require(actual["plan_sha256"] == digest, "retention_plan_decision_changed")
        conn.execute("CREATE TEMP TABLE prune_records(record_id TEXT PRIMARY KEY)")
        conn.executemany("INSERT INTO prune_records VALUES (?)", ((rid,) for rid in actual["delete_record_ids"]))
        conn.execute("DELETE FROM predictions WHERE record_id IN (SELECT record_id FROM prune_records)")
        for version in actual["delete_calibration_versions"]:
            conn.execute("DELETE FROM calibrations WHERE calibration_version=?", (version,))
        for token in actual["delete_tokens"]:
            row = conn.execute("SELECT group_id,mature_count,consumed_at FROM retrain_tokens WHERE token=?", (token,)).fetchone()
            if row and row["consumed_at"] is None:
                conn.execute("""UPDATE group_state SET last_revoked_token_hash=?,last_revoked_ordinal=?
                    WHERE group_id=? AND (last_revoked_ordinal IS NULL OR last_revoked_ordinal<?)""",
                    (token, row["mature_count"], row["group_id"], row["mature_count"]))
            conn.execute("DELETE FROM retrain_tokens WHERE token=?", (token,))
        for gid in actual["delete_group_ids"]:
            group = conn.execute("SELECT activation_epoch FROM group_state WHERE group_id=?", (gid,)).fetchone()
            if group and not group[0].startswith("legacy-retired-"):
                floor = _meta(conn, "retired_activation_epoch_floor")
                if floor is None or _timestamp(group[0]) > _timestamp(floor):
                    _put_meta(conn, "retired_activation_epoch_floor", group[0])
            conn.execute("DELETE FROM group_state WHERE group_id=?", (gid,))
        conn.execute("""DELETE FROM bindings WHERE NOT EXISTS(SELECT 1 FROM predictions WHERE binding_id=bindings.binding_id)
            AND NOT EXISTS(SELECT 1 FROM predictions WHERE settlement_binding_id=bindings.binding_id)""")
        _require(not conn.execute("PRAGMA foreign_key_check").fetchone(), "pruned_forward_foreign_key_violation")
        totals = _meta(conn, "retention_totals", {"predictions_deleted": 0, "expired_unknown_deleted": 0, "runs_with_deletions": 0})
        if actual["counts"]["predictions_delete"] or actual["delete_calibration_versions"] or actual["delete_tokens"] or actual["delete_group_ids"]:
            totals["predictions_deleted"] += actual["counts"]["predictions_delete"]
            totals["expired_unknown_deleted"] += sum(actual["delete_reason_counts"].get(key, 0) for key in
                                                       ("expired_target_unverified", "expired_endpoint_unobserved"))
            totals["runs_with_deletions"] += 1
            _put_meta(conn, "retention_totals", totals)
            _put_meta(conn, "last_retention", {"plan_sha256": digest, "completed_at": now.isoformat(),
                                             "counts": actual["counts"], "delete_reason_counts": actual["delete_reason_counts"]})
    return {"schema": RETENTION_PLAN_SCHEMA, "plan_sha256": digest, "applied": True,
            "counts": actual["counts"], "physical_reclaim_performed": False, "cursor_reset": False}


def _file_sha(path):
    digest = hashlib.sha256()
    with Path(path).open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def reclaim_storage(*, db_path=DEFAULT_DB_PATH, expected_db_sha256=None, quiescence=None,
                    dry_run=True, incremental_pages=None, qa_override=False):
    """Explicit quiescent maintenance only; logical pruning is a separate API."""
    path = _owned_database(db_path, qa_override)
    _require(path.is_file(), "maintenance_database_missing")
    _require(isinstance(quiescence, Mapping) and quiescence.get("all_consumers_stopped") is True and
             quiescence.get("checkpoint_clear") is True and quiescence.get("database_path") == str(path),
             "fresh_quiescence_proof_missing")
    observed = _timestamp(quiescence.get("observed_at"))
    _require(observed <= _now() <= observed + timedelta(minutes=5), "quiescence_proof_stale")
    _sha(expected_db_sha256, "expected_db_sha256")
    _require(_file_sha(path) == expected_db_sha256, "maintenance_database_changed")
    for suffix in ("-wal", "-journal"):
        sidecar = Path(str(path) + suffix)
        _require(not sidecar.exists() or sidecar.stat().st_size == 0, "maintenance_active_journal_or_checkpoint")
    size = path.stat().st_size
    free = shutil.disk_usage(path.parent).free
    _require(free >= max(64 * 1024 * 1024, size * 2), "maintenance_insufficient_free_space")
    with _readonly(path) as reader:
        _require(reader.execute("PRAGMA user_version").fetchone()[0] == DB_VERSION, "maintenance_requires_migrated_schema")
        mode = reader.execute("PRAGMA auto_vacuum").fetchone()[0]
        freelist = reader.execute("PRAGMA freelist_count").fetchone()[0]
    if incremental_pages is not None:
        _require(type(incremental_pages) is int and 1 <= incremental_pages <= 4096, "invalid_incremental_vacuum_budget")
        _require(mode == 2, "incremental_vacuum_not_initialized")
    result = {"database_path": str(path), "dry_run": dry_run, "size_before": size,
              "free_space_bytes": free, "freelist_pages_before": freelist, "auto_vacuum_before": mode,
              "operation": "incremental_vacuum" if incremental_pages is not None else "vacuum_then_incremental_mode"}
    if dry_run:
        return result
    conn = sqlite3.connect(str(path), timeout=0, isolation_level=None)
    try:
        conn.execute("PRAGMA locking_mode=EXCLUSIVE")
        conn.execute("BEGIN EXCLUSIVE")
        _require(conn.execute("PRAGMA user_version").fetchone()[0] == DB_VERSION, "maintenance_schema_changed")
        _require(_file_sha(path) == expected_db_sha256, "maintenance_database_changed")
        conn.commit()
        if incremental_pages is None:
            conn.execute("PRAGMA auto_vacuum=INCREMENTAL")
            conn.execute("VACUUM")
        else:
            conn.execute("PRAGMA incremental_vacuum(" + str(incremental_pages) + ")")
        _require(conn.execute("PRAGMA quick_check").fetchone()[0] == "ok", "maintenance_integrity_failed")
        _require(not conn.execute("PRAGMA foreign_key_check").fetchone(), "maintenance_foreign_key_failed")
        result.update(size_after=path.stat().st_size, auto_vacuum_after=conn.execute("PRAGMA auto_vacuum").fetchone()[0],
                      freelist_pages_after=conn.execute("PRAGMA freelist_count").fetchone()[0], physical_reclaim_performed=True)
    finally:
        conn.close()
    return result


def _content_ref(value, field):
    _require(isinstance(value, Mapping), field + "_missing")
    result = {"path": value.get("path"), "sha256": value.get("sha256"),
              "byte_size": value.get("byte_size", value.get("size"))}
    _require(isinstance(result["path"], str) and bool(result["path"]) and
             type(result["byte_size"]) is int and result["byte_size"] > 0, field + "_incomplete")
    _sha(result["sha256"], field + "_sha256")
    return result


def pending_settlement_requirements(market_sessions, *, calendar_verified_by_market, calendar_ref_by_market,
                                    asof_by_market, db_path=DEFAULT_DB_PATH, export_ttl_hours=24):
    """Read-only finite DATA candidates. This document never authorizes HTTP.

    An independent producer must revalidate its own market/calendar/UTC expiry
    and then request only the single target endpoint. It must not import this
    module or borrow the analysis interpreter. Unknown targets are omitted.
    """
    _require(type(export_ttl_hours) is int and 1 <= export_ttl_hours <= 24, "invalid_pending_export_ttl")
    labels = []
    axes = _verified_axes(market_sessions, calendar_verified_by_market, labels)
    _require(isinstance(calendar_ref_by_market, Mapping) and isinstance(asof_by_market, Mapping),
             "pending_export_calendar_proof_missing")
    calendar_refs = {market: _content_ref(calendar_ref_by_market.get(market), "calendar_ref_" + market) for market in axes}
    market_asof = {market: _day(asof_by_market.get(market)) for market in axes}
    positions = {market: {day: i for i, day in enumerate(axis)} for market, axis in axes.items()}
    now = _now()
    _require(all(day <= now.astimezone(_TAIPEI).date().isoformat() for day in market_asof.values()),
             "pending_export_market_asof_in_future")
    result = {"schema": PENDING_EXPORT_SCHEMA, "schema_version": 1, "purpose": "forward_settlement", "profile_id": "C0",
        "candidate_authorizes_http": False, "generated_at": now.isoformat(),
        "expires_at": (now + timedelta(hours=export_ttl_hours)).isoformat(),
        "asof_by_market": market_asof, "calendar_verified_by_market": {market: True for market in axes},
        "calendar_ref_by_market": calendar_refs, "pending_settlement_candidates": [],
        "unknown_or_unproven_candidates_omitted": True, "producer_maturity_gate": "own_verified_market_expected_asof_gte_target",
        "prospective_policy": PROSPECTIVE_POLICY, "expiry_policy": "target_plus_20_verified_sessions_or_actual_recorded_plus_180_days_first",
        "version_labels": labels}
    if not Path(db_path).exists():
        result["database_exists"] = False
        return result
    witnesses, calendar_mismatch = {}, 0
    with _readonly(db_path) as conn:
        _require(conn.execute("PRAGMA user_version").fetchone()[0] == DB_VERSION, "pending_export_requires_migrated_schema")
        # Read-only candidate list: re-reading the adoption receipt file is not
        # required here (the stored refs carry the last sync's isolation labels).
        refs = _normalize_refs(_meta(conn, "retention_refs"), now, verify_files=False)
        _ref_labels(refs, labels)
        result["retention_refs_sha256"] = _hash(refs)
        rows = conn.execute("""SELECT p.*,g.sealed_signal_date,g.activation_epoch,g.semantics_sha256
            FROM predictions AS p JOIN group_state AS g ON g.group_id=p.group_id
            WHERE p.observation_status IN ('pending','unobserved') AND p.record_kind='current_forecast'
            AND p.raw_probability IS NOT NULL AND p.signal_close>0 AND p.original_ordinary=1""")
        for row in rows:
            market = row["market"]
            if market not in axes or (row["sealed_signal_date"] is not None and row["signal_date"] <= row["sealed_signal_date"]):
                continue
            axis, index = axes[market], positions[market]
            # A stored target alone is insufficient: this exact signal/H pair
            # must also be witnessed by the supplied, verified calendar axis.
            start = index.get(row["signal_date"])
            if start is None or start + row["horizon"] >= len(axis):
                continue
            target = axis[start + row["horizon"]]
            if not (row["target_date"] is None or row["target_date"] == target):
                calendar_mismatch += 1
                continue
            eligible, _ = _prospective(row["record_kind"], row["recorded_at"], target, market=market)
            expired, expires, _, _ = _expiry(row, axis, now, index)
            if eligible != 1 or expired:
                continue
            grace_pos = index[target] + ENDPOINT_GRACE_SESSIONS
            grace_day = axis[grace_pos] if grace_pos < len(axis) else None
            candidate = {"record_id": row["record_id"], "stock_id": row["stock_id"], "market": market,
                "profile_id": "C0", "record_kind": "current_forecast", "original_ordinary": True,
                "signal_scalar_stored": True, "signal_close": row["signal_close"], "forward_eligible": True,
                "recorded_at": row["recorded_at"], "signal_date": row["signal_date"], "horizon": row["horizon"],
                "target_date": target, "not_before_session_date": target, "expires_at": expires,
                "hard_expires_at": (_timestamp(row["recorded_at"]) + timedelta(days=MAX_FORECAST_AGE_DAYS)).isoformat(),
                "grace_sessions": ENDPOINT_GRACE_SESSIONS, "target_grace_session_date": grace_day,
                "forecast_ref": {"record_id": row["record_id"], "immutable_sha256": row["immutable_sha256"],
                                 "model_sha256": row["model_sha256"], "price_artifact_sha256": row["price_artifact_sha256"]},
                "target_session_ref": {"verified": True, "market": market, "signal_date": row["signal_date"],
                    "horizon": row["horizon"], "target_date": target, "calendar_ref": calendar_refs[market]},
                "grace_calendar_ref": calendar_refs[market], "matured_at_export": market_asof[market] >= target,
                "base_model_version": row["base_model_version"], "model_activation_epoch": row["activation_epoch"],
                "effective_C0_semantics_projection_sha256": row["semantics_sha256"], "price_basis": row["price_basis"]}
            key = (row["stock_id"], market, target)
            previous = witnesses.get(key)
            if previous is None or (_timestamp(candidate["expires_at"]), candidate["record_id"]) > (
                    _timestamp(previous["expires_at"]), previous["record_id"]):
                witnesses[key] = candidate
    result["pending_settlement_candidates"] = [witnesses[key] for key in sorted(witnesses)]
    if calendar_mismatch:
        _label(labels, "pending_target_calendar_mismatch", actual=calendar_mismatch,
               detail={"pending_candidates": "omitted", "reason": "calendar_unverified"})
    return result


def export_pending_settlement_requirements(output_path, market_sessions, *, calendar_verified_by_market,
                                          calendar_ref_by_market, asof_by_market, db_path=DEFAULT_DB_PATH,
                                          export_ttl_hours=24, qa_override=False):
    """Seal JSON DATA for independent producers; no source-code dependency."""
    path = Path(output_path).resolve()
    canonical = DEFAULT_DB_PATH.parent / "pending_settlement_requirements.json"
    _require(path == canonical.resolve() or (qa_override is True and path.is_relative_to(QA_ROOT.resolve())),
             "pending_export_outside_owned_path")
    data = pending_settlement_requirements(market_sessions, calendar_verified_by_market=calendar_verified_by_market,
        calendar_ref_by_market=calendar_ref_by_market, asof_by_market=asof_by_market,
        db_path=db_path, export_ttl_hours=export_ttl_hours)
    encoded = (_json(data) + "\n").encode("utf-8")
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(path.name + ".tmp-" + str(os.getpid()))
    try:
        with temporary.open("xb") as handle:
            handle.write(encoded)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, path)
    finally:
        if temporary.exists():
            temporary.unlink()
    return {"schema": PENDING_EXPORT_SCHEMA, "content_ref": {"path": str(path),
            "sha256": hashlib.sha256(encoded).hexdigest(), "byte_size": len(encoded)},
            "candidate_count": len(data["pending_settlement_candidates"]),
            "generated_at": data["generated_at"], "expires_at": data["expires_at"], "candidate_authorizes_http": False,
            "version_labels": data["version_labels"]}
