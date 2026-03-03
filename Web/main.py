from fastapi import FastAPI, Request, Depends, HTTPException, status, Form
from fastapi.templating import Jinja2Templates
from fastapi.staticfiles import StaticFiles
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.security import OAuth2PasswordRequestForm
from sqlalchemy.orm import Session
from pydantic import BaseModel
import json
import datetime
import os
from typing import List, Dict, Any

# Environment variable to enable/disable audit ledger (for experiments)
LEDGER_ENABLED = os.getenv("LEDGER_ENABLED", "true").lower() == "true"

import models
import schemas
import auth
import ledger
from database import get_db, engine

# Create Tables (if not exist)
models.Base.metadata.create_all(bind=engine)

app = FastAPI(title="Doctor's Portal", description="Secure ECG Analysis Portal")

# Log ledger status at startup
print(f"🔐 Audit Ledger: {'ENABLED' if LEDGER_ENABLED else 'DISABLED'}")

# Mount static files
app.mount("/static", StaticFiles(directory="static"), name="static")

# Setup templates
templates = Jinja2Templates(directory="templates")

# --- Authentication Routes ---

@app.post("/token", response_model=schemas.Token)
async def login_for_access_token(form_data: OAuth2PasswordRequestForm = Depends(), db: Session = Depends(get_db)):
    user = db.query(models.User).filter(models.User.username == form_data.username).first()
    if not user or not auth.verify_password(form_data.password, user.hashed_password):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Incorrect username or password",
            headers={"WWW-Authenticate": "Bearer"},
        )
    access_token_expires = datetime.timedelta(minutes=auth.ACCESS_TOKEN_EXPIRE_MINUTES)
    access_token = auth.create_access_token(
        data={"sub": user.username}, expires_delta=access_token_expires
    )
    
    # Log valid login
    ledger.add_audit_entry(db, user.username, "LOGIN_SUCCESS", {"ip": "unknown"})
    
    return {"access_token": access_token, "token_type": "bearer"}

# --- Web Routes (Frontend) ---

@app.get("/", response_class=HTMLResponse)
async def login_page(request: Request):
    return templates.TemplateResponse("login.html", {"request": request})

@app.get("/dashboard", response_class=HTMLResponse)
async def dashboard(request: Request, db: Session = Depends(get_db)):
    # In a real app, we'd check cookies/auth token here. 
    # For prototype, we assume if they can reach here they are authorized via the Login form JS.
    
    # Fetch recent ECG records
    records = db.query(models.ECGRecord).order_by(models.ECGRecord.timestamp.desc()).limit(20).all()
    

    return templates.TemplateResponse("dashboard.html", {
        "request": request, 
        "records": records,
        "title": "Doctor's Dashboard"
    })

@app.get("/dashboard/rows", response_class=HTMLResponse)
async def dashboard_rows(request: Request, db: Session = Depends(get_db)):
    """Returns just the table rows HTML for AJAX refresh"""
    records = db.query(models.ECGRecord).order_by(models.ECGRecord.timestamp.desc()).limit(20).all()
    return templates.TemplateResponse("partials/rows.html", {"request": request, "records": records})

@app.get("/history", response_class=HTMLResponse)
async def history_view(request: Request, page: int = 1, limit: int = 50, db: Session = Depends(get_db)):
    """View full history of ECG records with pagination"""
    skip = (page - 1) * limit
    
    # Get total count
    total_count = db.query(models.ECGRecord).count()
    total_pages = (total_count + limit - 1) // limit
    
    # Get paginated records
    records = db.query(models.ECGRecord).order_by(models.ECGRecord.timestamp.desc()).offset(skip).limit(limit).all()
    
    return templates.TemplateResponse("history.html", {
        "request": request,
        "records": records,
        "title": "ECG Record History",
        "current_page": page,
        "total_pages": total_pages,
        "limit": limit,
        "total_count": total_count
    })

@app.get("/ecg/{record_id}", response_class=HTMLResponse)
async def view_ecg(record_id: int, request: Request, db: Session = Depends(get_db)):
    record = db.query(models.ECGRecord).filter(models.ECGRecord.id == record_id).first()
    if not record:
        raise HTTPException(status_code=404, detail="Record not found")
    
    # Parse processing results for display
    results = record.processing_results
    if isinstance(results, str):
        results = json.loads(results)
        
    if LEDGER_ENABLED:
        # Log this view action to the immutable ledger
        ledger.add_audit_entry(
            db,
            actor_id="CLINICIAN_VIEW", # User auth would populate actual ID
            action="VIEW_ECG",
            details={"record_id": record.id, "patient": record.patient.patient_id_external}
        )
        
    return templates.TemplateResponse("ecg_view.html", {
        "request": request,
        "record": record,
        "results": results,
        "title": f"ECG View - {record.patient.name}"
    })

# --- API Routes (For EDGE Device) ---

class ECGIngestSchema(schemas.BaseModel):
    ecg_values: List[float]
    results: Dict[str, Any]
    metadata: Dict[str, Any]

@app.post("/api/ingest")
async def ingest_ecg(data: ECGIngestSchema, db: Session = Depends(get_db)):
    """Receives data from EDGE device"""
    
    # 1. Extract Patient Info
    meta = data.metadata
    patient_info = meta.get('patient_info', {})
    p_id_ext = patient_info.get('patient_id', 'Unknown')
    
    # 2. Find or Create Patient
    patient = db.query(models.Patient).filter(models.Patient.patient_id_external == p_id_ext).first()
    if not patient:
        patient = models.Patient(
            patient_id_external=p_id_ext,
            name=f"Patient {p_id_ext}",
            dob="Unknown"
        )
        db.add(patient)
        db.commit()
        db.refresh(patient)
        
        if LEDGER_ENABLED:
            ledger.add_audit_entry(
                db,
                actor_id=f"DEVICE_{meta.get('device_id')}",
                action="PATIENT_CREATE",
                details={
                    "patient_db_id": patient.id,
                    "patient_external_id": p_id_ext,
                    "name": patient.name,
                }
            )
    
    # 3. Extract Processing Results
    # Assuming standard EDGE format: results['results']['ml_inference']...
    ml_results = data.results.get('results', {}).get('ml_inference', {})
    hr_results = data.results.get('results', {}).get('heart_rate', {})
    
    classification = ml_results.get('classification', '?')
    confidence = ml_results.get('confidence', 0.0)
    bpm = hr_results.get('heart_rate_bpm', 0.0)
    
    # Firmware / Provenance
    model_version = ml_results.get('model_version', None)
    model_hash = ml_results.get('model_hash', None)
    data_hash = ml_results.get('data_hash', None)
    
    # 4. Create ECG Record
    new_record = models.ECGRecord(
        patient_id=patient.id,
        timestamp=datetime.datetime.utcnow(),
        device_id=meta.get('device_id', 'EDGE_DEV'),
        heart_rate=bpm,
        classification=classification,
        confidence=confidence,
        model_version=model_version,
        model_hash=model_hash,
        data_hash=data_hash,
        ecg_data=data.ecg_values, # Stores as JSON
        processing_results=data.results
    )
    
    db.add(new_record)
    db.commit()
    db.refresh(new_record)
    
    # 5. Log to Cryptographic Audit Trail (if enabled)
    if LEDGER_ENABLED:
        ledger.add_audit_entry(
            db, 
            actor_id=f"DEVICE_{meta.get('device_id')}", 
            action="INGEST_ECG", 
            details={
                "record_id": new_record.id, 
                "patient": p_id_ext, 
                "class": classification,
                "model_version": model_version,
                "model_hash": model_hash,
                "data_hash": data_hash
            }
        )
    
    return {"status": "success", "record_id": new_record.id, "ledger_enabled": LEDGER_ENABLED}


class BatchIngestRecord(BaseModel):
    """Single record in a batch ingest request."""
    patient_id: str
    ecg_values: List[float]
    classification: str
    confidence: float
    heart_rate: float = 0.0
    metadata: Dict[str, Any] = {}
    results: Dict[str, Any] = {}


class BatchIngestSchema(BaseModel):
    """Schema for bulk ECG record ingestion."""
    records: List[BatchIngestRecord]


@app.post("/api/batch-ingest")
async def batch_ingest_ecg(data: BatchIngestSchema, db: Session = Depends(get_db)):
    """
    Bulk insert ECG records for experiment automation.
    Returns timing stats for ledger performance comparison.
    """
    import time
    
    start_time = time.time()
    inserted_count = 0
    record_ids = []
    
    for rec in data.records:
        try:
            # Find or Create Patient
            patient = db.query(models.Patient).filter(
                models.Patient.patient_id_external == rec.patient_id
            ).first()
            patient_is_new = False
            if not patient:
                patient = models.Patient(
                    patient_id_external=rec.patient_id,
                    name=f"Patient {rec.patient_id}",
                    dob="Unknown"
                )
                db.add(patient)
                db.flush()
                patient_is_new = True
            
            # Extract Provenance
            ml_results = rec.results.get('results', {}).get('ml_inference', {})
            model_version = ml_results.get('model_version', None)
            model_hash = ml_results.get('model_hash', None)
            data_hash = ml_results.get('data_hash', None)
            
            # Create ECG Record
            new_record = models.ECGRecord(
                patient_id=patient.id,
                timestamp=datetime.datetime.utcnow(),
                device_id="BATCH_INGEST",
                heart_rate=rec.heart_rate,
                classification=rec.classification,
                confidence=rec.confidence,
                model_version=model_version,
                model_hash=model_hash,
                data_hash=data_hash,
                ecg_data=rec.ecg_values,
                processing_results=rec.results
            )
            db.add(new_record)
            db.flush()  # Get ID
            record_ids.append(new_record.id)
            
            # Log to Audit Trail if enabled
            if LEDGER_ENABLED:
                if patient_is_new:
                    ledger.add_audit_entry(
                        db,
                        actor_id="BATCH_INGEST",
                        action="PATIENT_CREATE",
                        details={
                            "patient_db_id": patient.id,
                            "patient_external_id": rec.patient_id,
                            "name": patient.name,
                        },
                        auto_commit=False
                    )
                ledger.add_audit_entry(
                    db,
                    actor_id="BATCH_INGEST",
                    action="INGEST_ECG",
                    details={
                        "record_id": new_record.id, 
                        "patient": rec.patient_id, 
                        "class": rec.classification,
                        "model_version": model_version,
                        "model_hash": model_hash,
                        "data_hash": data_hash
                    },
                    auto_commit=False
                )
            
            inserted_count += 1
            
        except Exception as e:
            print(f"Error inserting {rec.patient_id}: {e}")
            continue
    
    # Commit all at once
    db.commit()
    
    total_time_ms = (time.time() - start_time) * 1000
    avg_time_per_record = total_time_ms / inserted_count if inserted_count > 0 else 0
    
    return {
        "status": "success",
        "inserted_count": inserted_count,
        "total_time_ms": total_time_ms,
        "avg_time_per_record_ms": avg_time_per_record,
        "ledger_enabled": LEDGER_ENABLED,
        "record_ids": record_ids
    }

@app.get("/api/system/latest-record-id")
async def get_latest_record_id(db: Session = Depends(get_db)):
    """Public endpoint for dashboard polling"""
    last_record = db.query(models.ECGRecord.id).order_by(models.ECGRecord.id.desc()).first()
    return {"latest_id": last_record.id if last_record else 0}

@app.get("/api/ecg/{record_id}")
async def get_ecg_record(record_id: int, db: Session = Depends(get_db)):
    """Get ECG record details by ID (for batch simulator)"""
    record = db.query(models.ECGRecord).filter(models.ECGRecord.id == record_id).first()
    if not record:
        raise HTTPException(status_code=404, detail="Record not found")
    
    # Get patient external ID if available
    patient_ext_id = None
    if record.patient:
        patient_ext_id = record.patient.patient_id_external
    
    return {
        "id": record.id,
        "patient_id": record.patient_id,
        "patient_external_id": patient_ext_id,
        "classification": record.classification,
        "confidence": record.confidence,
        "timestamp": record.timestamp.isoformat() if record.timestamp else None
    }

@app.get("/api/ecg/{record_id}/provenance")
async def get_ecg_provenance(record_id: int, db: Session = Depends(get_db)):
    """API endpoint to fetch full tamper-evident cryptographic provenance for a record."""
    record = db.query(models.ECGRecord).filter(models.ECGRecord.id == record_id).first()
    if not record:
        raise HTTPException(status_code=404, detail="Record not found")
        
    # Find Ledger blocks
    audit_entries = db.query(models.AuditLog).filter(
        models.AuditLog.details.like(f'%"record_id": {record_id}%')
    ).order_by(models.AuditLog.timestamp.asc()).all()
    
    if not audit_entries:
        return {"status": "error", "message": "No ledger entry found for this transaction. The transaction was likely handled outside of the blockchain context.", "ledger_valid": False}
        
    history = []
    ledger_valid = True
    
    for entry in audit_entries:
        calculated_hash = ledger.calculate_hash(
            entry.prev_hash,
            str(entry.timestamp),
            entry.actor_id,
            entry.action,
            entry.details
        )
        is_val = (calculated_hash == entry.record_hash)
        if not is_val:
            ledger_valid = False
            
        history.append({
            "action": entry.action,
            "actor_id": entry.actor_id,
            "timestamp": entry.timestamp.isoformat() if entry.timestamp else None,
            "record_hash": entry.record_hash,
            "prev_hash": entry.prev_hash,
            "is_valid": is_val
        })
        
    ingest_entry = next((e for e in audit_entries if e.action == "INGEST_ECG"), audit_entries[0])
    
    # Hardening #2: verify stored ECG data against its original hash
    data_verification = ledger.verify_ecg_data_integrity(db, record_id)
    
    return {
        "status": "success",
        "ledger_valid": ledger_valid,
        "record_hash": ingest_entry.record_hash,
        "prev_hash": ingest_entry.prev_hash,
        "data_hash": record.data_hash,
        "model_hash": record.model_hash,
        "model_version": record.model_version,
        "timestamp": ingest_entry.timestamp.isoformat() if ingest_entry.timestamp else None,
        "history": history,
        "data_integrity": data_verification
    }

@app.get("/api/audit/verify")
async def verify_audit_trail(db: Session = Depends(get_db)):
    """Single-chain integrity: HMAC hash chain + ECG data cross-check.
    Fast enough for 3-second polling (field comparisons only, no SHA-256 recomputation)."""
    result = ledger.verify_chain_integrity(db)
    return {
        "integrity_status": "Valid" if result["valid"] else "CORRUPTED",
        "chain": result,
    }

@app.get("/api/audit/verify-all")
async def verify_all_blocks(db: Session = Depends(get_db)):
    """Return per-block verification results for every entry in the chain."""
    results = ledger.verify_all_blocks(db)
    invalid_ids = [bid for bid, r in results.items() if not r["valid"]]
    return {
        "total_blocks": len(results),
        "invalid_count": len(invalid_ids),
        "blocks": results,
    }

@app.get("/api/audit/block/{block_id}")
async def get_audit_block(block_id: int, db: Session = Depends(get_db)):
    """Return full details of a single audit block."""
    entry = db.query(models.AuditLog).filter(models.AuditLog.id == block_id).first()
    if not entry:
        raise HTTPException(status_code=404, detail="Block not found")
    return {
        "id": entry.id,
        "timestamp": str(entry.timestamp),
        "actor_id": entry.actor_id,
        "action": entry.action,
        "details": entry.details,
        "prev_hash": entry.prev_hash,
        "record_hash": entry.record_hash,
    }

@app.get("/api/ecg/{record_id}/verify-data")
async def verify_ecg_data(record_id: int, db: Session = Depends(get_db)):
    """Re-derive SHA-256 of stored ecg_data and compare against the original data_hash."""
    result = ledger.verify_ecg_data_integrity(db, record_id)
    return {
        "record_id": record_id,
        "data_integrity": "Valid" if result["valid"] else "TAMPERED",
        "details": result
    }

@app.post("/api/ecg/verify-batch")
async def verify_ecg_batch(payload: dict, db: Session = Depends(get_db)):
    """Batch-verify ECG data integrity for a list of record IDs."""
    record_ids = payload.get("record_ids", [])
    results = {}
    for rid in record_ids:
        r = ledger.verify_ecg_data_integrity(db, int(rid))
        results[str(rid)] = r
    return {"results": results}

@app.get("/audit-ledger", response_class=HTMLResponse)
async def audit_ledger_view(request: Request, db: Session = Depends(get_db)):
    """View Cryptographic Audit Ledger — defrag-style block map."""
    total_count = db.query(models.AuditLog).count()
    return templates.TemplateResponse("audit_ledger.html", {
        "request": request,
        "title": "Cryptographic Audit Ledger",
        "total_count": total_count,
    })

# --- Patient Features ---

@app.get("/api/patients/search")
async def search_patients(q: str = "", db: Session = Depends(get_db)):
    """Search patients by ID or Name. Returns all patients (up to 100) when q is empty."""
    from sqlalchemy import func
    query = db.query(models.Patient)
    if q.strip():
        query = query.filter(
            (models.Patient.patient_id_external.contains(q)) |
            (models.Patient.name.contains(q))
        )
    patients = query.order_by(models.Patient.patient_id_external).limit(100).all()
    patient_ids = [p.id for p in patients]

    class_counts = {}
    integrity_flags = {}
    if patient_ids:
        rows = db.query(
            models.ECGRecord.patient_id,
            models.ECGRecord.classification,
            func.count(models.ECGRecord.id)
        ).filter(
            models.ECGRecord.patient_id.in_(patient_ids)
        ).group_by(
            models.ECGRecord.patient_id, models.ECGRecord.classification
        ).all()
        for pid, cls, cnt in rows:
            class_counts.setdefault(pid, {})[cls or "U"] = cnt

        missing_hash = db.query(models.ECGRecord.patient_id).filter(
            models.ECGRecord.patient_id.in_(patient_ids),
            (models.ECGRecord.data_hash == None) | (models.ECGRecord.data_hash == "")
        ).distinct().all()
        for (pid,) in missing_hash:
            integrity_flags[pid] = "unknown"

    results = []
    for p in patients:
        counts = class_counts.get(p.id, {})
        total = sum(counts.values())
        dominant = max(counts, key=counts.get) if counts else None
        results.append({
            "id": p.id,
            "external_id": p.patient_id_external,
            "name": p.name,
            "record_count": total,
            "classifications": counts,
            "dominant_class": dominant,
            "integrity": integrity_flags.get(p.id, "ok"),
        })
    return results

@app.get("/patient/{patient_id}", response_class=HTMLResponse)
async def patient_timeline(patient_id: int, request: Request, db: Session = Depends(get_db)):
    """View Patient Timeline"""
    patient = db.query(models.Patient).filter(models.Patient.id == patient_id).first()
    if not patient:
        raise HTTPException(status_code=404, detail="Patient not found")
        
    # Get all records sorted by time
    records = db.query(models.ECGRecord).filter(
        models.ECGRecord.patient_id == patient_id
    ).order_by(models.ECGRecord.timestamp.desc()).all()
    
    return templates.TemplateResponse("patient_timeline.html", {
        "request": request,
        "patient": patient,
        "records": records,
        "title": f"Timeline: {patient.name}"
    })
