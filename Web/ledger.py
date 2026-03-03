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


def verify_chain_integrity(db: Session) -> dict:
    """
    Verify the entire cryptographic audit ledger for tampering.
    Returns a dict with detailed results instead of a simple bool so the
    caller can report exactly which block failed.
    """
    entries = db.query(models.AuditLog).order_by(models.AuditLog.id.asc()).all()
    
    expected_prev_hash = "0" * 64
    
    for entry in entries:
        if entry.prev_hash != expected_prev_hash:
            print(f"Broken Link at ID {entry.id}! PrevHash mismatch.")
            return {"valid": False, "failed_id": entry.id, "reason": "prev_hash link broken"}
            
        calculated = calculate_hash(
            entry.prev_hash,
            str(entry.timestamp),
            entry.actor_id,
            entry.action,
            entry.details
        )
        
        if calculated != entry.record_hash:
            print(f"Data Tampering at ID {entry.id}! Hash mismatch.")
            return {"valid": False, "failed_id": entry.id, "reason": "record_hash mismatch"}
            
        expected_prev_hash = entry.record_hash
        
    return {"valid": True, "total_blocks": len(entries)}


def verify_all_blocks(db: Session) -> list:
    """
    Verify every block individually and return a list of per-block results.
    Unlike verify_chain_integrity (which stops at the first failure), this
    walks the entire chain so the UI can mark each block as valid/invalid.
    """
    entries = db.query(models.AuditLog).order_by(models.AuditLog.id.asc()).all()
    results = {}
    expected_prev_hash = "0" * 64

    for entry in entries:
        link_ok = (entry.prev_hash == expected_prev_hash)

        calculated = calculate_hash(
            entry.prev_hash,
            str(entry.timestamp),
            entry.actor_id,
            entry.action,
            entry.details
        )
        hash_ok = (calculated == entry.record_hash)

        valid = link_ok and hash_ok
        reason = "valid"
        if not link_ok:
            reason = "prev_hash link broken"
        elif not hash_ok:
            reason = "record_hash mismatch"

        results[entry.id] = {
            "valid": valid,
            "reason": reason,
        }

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
