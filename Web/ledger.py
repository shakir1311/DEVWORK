import hashlib
import json
import datetime
from sqlalchemy.orm import Session
import models

def calculate_hash(prev_hash: str, timestamp: str, actor_id: str, action: str, details: str) -> str:
    """
    Calculate SHA-256 hash of a block.
    """
    payload = f"{prev_hash}{timestamp}{actor_id}{action}{details}"
    return hashlib.sha256(payload.encode('utf-8')).hexdigest()

def add_audit_entry(db: Session, actor_id: str, action: str, details: dict):
    """
    Add a new entry to the immutable audit ledger.
    """
    # 1. Get the last record's hash
    last_entry = db.query(models.AuditLog).order_by(models.AuditLog.id.desc()).first()
    
    if last_entry:
        prev_hash = last_entry.record_hash
    else:
        # Genesis Block Link
        prev_hash = "0" * 64
        
    # 2. Prepare data
    timestamp = datetime.datetime.utcnow()
    details_str = json.dumps(details, sort_keys=True) # Sort keys for consistent hashing
    
    # 3. Calculate Hash (The "Proof of work" equivalent, though trivial here)
    record_hash = calculate_hash(
        prev_hash, 
        str(timestamp), 
        actor_id, 
        action, 
        details_str
    )
    
    # 4. Create and Save Record
    new_entry = models.AuditLog(
        timestamp=timestamp,
        actor_id=actor_id,
        action=action,
        details=details_str,
        prev_hash=prev_hash,
        record_hash=record_hash
    )
    
    db.add(new_entry)
    db.commit()
    return new_entry

def verify_chain_integrity(db: Session) -> bool:
    """
    Verify the entire cryptographic audit ledger for tampering.
    Returns True if valid, False if tampering detected.
    """
    entries = db.query(models.AuditLog).order_by(models.AuditLog.id.asc()).all()
    
    expected_prev_hash = "0" * 64
    
    for entry in entries:
        # 1. Check if points to correct previous
        if entry.prev_hash != expected_prev_hash:
            print(f"Broken Link at ID {entry.id}! PrevHash mismatch.")
            return False
            
        # 2. specific hash check
        calculated = calculate_hash(
            entry.prev_hash,
            str(entry.timestamp),
            entry.actor_id,
            entry.action,
            entry.details
        )
        
        if calculated != entry.record_hash:
            print(f"Data Tampering at ID {entry.id}! Hash mismatch.")
            return False
            
        expected_prev_hash = entry.record_hash
        
    return True
