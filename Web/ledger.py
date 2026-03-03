import hashlib
import hmac
import json
import datetime
import os
from pathlib import Path
from sqlalchemy.orm import Session
import models

# Load .env file from the Web/ directory so the key never needs to be
# hardcoded in source.  The .env file is excluded from git via .gitignore.
_env_path = Path(__file__).resolve().parent / ".env"
if _env_path.exists():
    for line in _env_path.read_text().splitlines():
        line = line.strip()
        if line and not line.startswith("#") and "=" in line:
            k, v = line.split("=", 1)
            os.environ.setdefault(k.strip(), v.strip())

_raw_key = os.getenv("LEDGER_HMAC_KEY")
if not _raw_key:
    raise RuntimeError(
        "LEDGER_HMAC_KEY is not set. "
        "Create Web/.env with: LEDGER_HMAC_KEY=<64-char hex secret>"
    )

LEDGER_HMAC_KEY = _raw_key.encode("utf-8")


def calculate_hash(prev_hash: str, timestamp: str, actor_id: str, action: str, details: str) -> str:
    """
    Calculate HMAC-SHA256 of a ledger block using a server-side secret key.
    An attacker with database-only access cannot forge valid hashes without
    possessing LEDGER_HMAC_KEY.
    """
    payload = f"{prev_hash}{timestamp}{actor_id}{action}{details}"
    return hmac.new(LEDGER_HMAC_KEY, payload.encode("utf-8"), hashlib.sha256).hexdigest()


def add_audit_entry(db: Session, actor_id: str, action: str, details: dict, auto_commit: bool = True):
    """
    Add a new entry to the immutable audit ledger.
    
    Args:
        db: Database session
        actor_id: Actor performing the action
        action: Action type
        details: Action details dict
        auto_commit: If True (default), commits immediately. Set False for batch operations.
    """
    last_entry = db.query(models.AuditLog).order_by(models.AuditLog.id.desc()).first()
    
    if last_entry:
        prev_hash = last_entry.record_hash
    else:
        prev_hash = "0" * 64
        
    timestamp = datetime.datetime.utcnow()
    details_str = json.dumps(details, sort_keys=True)
    
    record_hash = calculate_hash(
        prev_hash, 
        str(timestamp), 
        actor_id, 
        action, 
        details_str
    )
    
    new_entry = models.AuditLog(
        timestamp=timestamp,
        actor_id=actor_id,
        action=action,
        details=details_str,
        prev_hash=prev_hash,
        record_hash=record_hash
    )
    
    db.add(new_entry)
    if auto_commit:
        db.commit()
    return new_entry


def _build_lookups(db: Session) -> dict:
    """
    Pre-load all verifiable tables into fast lookup dicts.
    Only fetches lightweight columns — never loads ecg_data or processing_results.
    """
    from sqlalchemy.orm import load_only

    ecg_records = db.query(models.ECGRecord).options(
        load_only(
            models.ECGRecord.id,
            models.ECGRecord.classification,
            models.ECGRecord.data_hash,
            models.ECGRecord.model_hash,
            models.ECGRecord.model_version,
            models.ECGRecord.patient_id,
        )
    ).all()

    patients = db.query(models.Patient).all()
    users = db.query(models.User).all()

    return {
        "ecg": {r.id: r for r in ecg_records},
        "patient_by_id": {p.id: p for p in patients},
        "patient_by_ext": {p.patient_id_external: p for p in patients},
        "user_by_name": {u.username: u for u in users},
    }


def _cross_check_block(action: str, snapshot: dict, lookups: dict) -> list:
    """
    Compare a ledger block's snapshot against the live database row.
    Returns a list of tampered field names (empty = intact).
    Handles INGEST_ECG, PATIENT_CREATE, and USER_CREATE actions.
    """
    if action == "INGEST_ECG":
        rid = snapshot.get("record_id")
        if rid is None:
            return []
        record = lookups["ecg"].get(rid)
        if record is None:
            return ["record_deleted"]

        tampered = []
        if "class" in snapshot and snapshot["class"] != record.classification:
            tampered.append("classification")
        if "data_hash" in snapshot and snapshot["data_hash"] != record.data_hash:
            tampered.append("data_hash")
        if "model_hash" in snapshot and snapshot["model_hash"] != record.model_hash:
            tampered.append("model_hash")
        if "model_version" in snapshot and snapshot["model_version"] != record.model_version:
            tampered.append("model_version")
        if "patient" in snapshot and record.patient_id:
            patient = lookups["patient_by_id"].get(record.patient_id)
            if patient and snapshot["patient"] != patient.patient_id_external:
                tampered.append("patient")
        return tampered

    if action == "PATIENT_CREATE":
        ext_id = snapshot.get("patient_external_id")
        if not ext_id:
            return []
        patient = lookups["patient_by_ext"].get(ext_id)
        if patient is None:
            return ["patient_deleted"]

        tampered = []
        if "name" in snapshot and snapshot["name"] != patient.name:
            tampered.append("name")
        if "patient_db_id" in snapshot and snapshot["patient_db_id"] != patient.id:
            tampered.append("id")
        return tampered

    if action == "USER_CREATE":
        username = snapshot.get("username")
        if not username:
            return []
        user = lookups["user_by_name"].get(username)
        if user is None:
            return ["user_deleted"]

        tampered = []
        if "role" in snapshot and snapshot["role"] != user.role:
            tampered.append("role")
        return tampered

    return []


_CROSSCHECK_ACTIONS = {"INGEST_ECG", "PATIENT_CREATE", "USER_CREATE"}


def verify_chain_integrity(db: Session) -> dict:
    """
    Verify the entire cryptographic ledger:
      1. HMAC hash chain (detects audit_log tampering)
      2. Cross-check every state-creating block against live DB rows
         (detects ecg_records / patients / users tampering)
    Fast: field-level comparisons only, no SHA-256 recomputation.
    """
    entries = db.query(models.AuditLog).order_by(models.AuditLog.id.asc()).all()
    lookups = _build_lookups(db)

    expected_prev_hash = "0" * 64
    tampered_records = []

    for entry in entries:
        if entry.prev_hash != expected_prev_hash:
            return {
                "valid": False, "failed_id": entry.id,
                "reason": "prev_hash link broken",
                "total_blocks": len(entries),
                "tampered_records": tampered_records,
                "tampered_count": len(tampered_records),
                "chain_intact": False,
                "data_intact": len(tampered_records) == 0,
            }

        calculated = calculate_hash(
            entry.prev_hash, str(entry.timestamp),
            entry.actor_id, entry.action, entry.details
        )

        if calculated != entry.record_hash:
            return {
                "valid": False, "failed_id": entry.id,
                "reason": "record_hash mismatch",
                "total_blocks": len(entries),
                "tampered_records": tampered_records,
                "tampered_count": len(tampered_records),
                "chain_intact": False,
                "data_intact": len(tampered_records) == 0,
            }

        if entry.action in _CROSSCHECK_ACTIONS:
            try:
                snapshot = json.loads(entry.details)
            except (json.JSONDecodeError, TypeError):
                snapshot = {}
            fields = _cross_check_block(entry.action, snapshot, lookups)
            if fields:
                tampered_records.append({
                    "record_id": snapshot.get("record_id") or snapshot.get("patient_db_id") or snapshot.get("username"),
                    "block_id": entry.id,
                    "action": entry.action,
                    "tampered_fields": fields,
                })

        expected_prev_hash = entry.record_hash

    data_ok = len(tampered_records) == 0

    return {
        "valid": data_ok,
        "total_blocks": len(entries),
        "tampered_records": tampered_records,
        "tampered_count": len(tampered_records),
        "chain_intact": True,
        "data_intact": data_ok,
    }


def verify_all_blocks(db: Session) -> dict:
    """
    Verify every block individually and return per-block results.
    For state-creating blocks (INGEST_ECG, PATIENT_CREATE, USER_CREATE),
    also cross-checks against the live database.
    """
    entries = db.query(models.AuditLog).order_by(models.AuditLog.id.asc()).all()
    lookups = _build_lookups(db)
    results = {}
    expected_prev_hash = "0" * 64

    for entry in entries:
        link_ok = (entry.prev_hash == expected_prev_hash)

        calculated = calculate_hash(
            entry.prev_hash, str(entry.timestamp),
            entry.actor_id, entry.action, entry.details
        )
        hash_ok = (calculated == entry.record_hash)

        data_tampered = []
        if entry.action in _CROSSCHECK_ACTIONS:
            try:
                snapshot = json.loads(entry.details)
            except (json.JSONDecodeError, TypeError):
                snapshot = {}
            data_tampered = _cross_check_block(entry.action, snapshot, lookups)

        valid = link_ok and hash_ok and len(data_tampered) == 0
        reason = "valid"
        if not link_ok:
            reason = "prev_hash link broken"
        elif not hash_ok:
            reason = "record_hash mismatch"
        elif data_tampered:
            reason = "data_tampered"

        result = {
            "valid": valid,
            "reason": reason,
            "action": entry.action,
            "timestamp": str(entry.timestamp),
            "actor_id": entry.actor_id,
        }
        if data_tampered:
            result["tampered_fields"] = data_tampered
            try:
                det = json.loads(entry.details)
                result["ecg_record_id"] = det.get("record_id")
                result["entity_label"] = (
                    det.get("record_id")
                    or det.get("patient_external_id")
                    or det.get("username")
                )
            except Exception:
                pass

        results[entry.id] = result
        expected_prev_hash = entry.record_hash

    return results


def verify_ecg_data_integrity(db: Session, record_id: int) -> dict:
    """
    Field-level tamper detection for an ECG record.

    1. Re-derive SHA-256 of ecg_data and compare to the stored data_hash.
    2. Look up the INGEST_ECG audit entry and compare the current DB field
       values against the snapshot captured at ingest time, reporting every
       field that differs.
    """
    record = db.query(models.ECGRecord).filter(models.ECGRecord.id == record_id).first()
    if not record:
        return {"valid": False, "reason": "record_not_found", "tampered_fields": []}

    tampered_fields = []

    # --- 1. ECG raw-data hash check ---
    data_hash_ok = True
    stored_hash = record.data_hash
    recomputed_hash = None

    if not stored_hash:
        data_hash_ok = False
    else:
        ecg_data = record.ecg_data
        if ecg_data is None:
            data_hash_ok = False
        else:
            ecg_list = json.loads(ecg_data) if isinstance(ecg_data, str) else list(ecg_data)
            recomputed_hash = hashlib.sha256(json.dumps(ecg_list).encode("utf-8")).hexdigest()
            if recomputed_hash != stored_hash:
                data_hash_ok = False
                tampered_fields.append("ecg_data")

    # --- 2. Cross-reference against ledger snapshot ---
    ingest_entry = db.query(models.AuditLog).filter(
        models.AuditLog.action == "INGEST_ECG",
        models.AuditLog.details.like(f'%"record_id": {record_id}%')
    ).first()

    if ingest_entry:
        try:
            snapshot = json.loads(ingest_entry.details)
        except (json.JSONDecodeError, TypeError):
            snapshot = {}

        if "class" in snapshot and snapshot["class"] != record.classification:
            tampered_fields.append("classification")

        if "data_hash" in snapshot and snapshot["data_hash"] != record.data_hash:
            tampered_fields.append("data_hash")

        if "model_hash" in snapshot and snapshot["model_hash"] != record.model_hash:
            tampered_fields.append("model_hash")

        if "model_version" in snapshot and snapshot["model_version"] != record.model_version:
            tampered_fields.append("model_version")

        if "patient" in snapshot and record.patient:
            if snapshot["patient"] != record.patient.patient_id_external:
                tampered_fields.append("patient")

    no_hash = (stored_hash is None)
    valid = data_hash_ok and len(tampered_fields) == 0

    reason = "match"
    if no_hash and len(tampered_fields) == 0:
        reason = "no_data_hash_stored"
        valid = False
    elif not valid:
        reason = "fields_tampered"

    return {
        "valid": valid,
        "stored_hash": stored_hash,
        "recomputed_hash": recomputed_hash,
        "reason": reason,
        "tampered_fields": tampered_fields,
    }


