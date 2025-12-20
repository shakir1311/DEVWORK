from fastapi import FastAPI, Request, Depends, HTTPException, status, Form
from fastapi.templating import Jinja2Templates
from fastapi.staticfiles import StaticFiles
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.security import OAuth2PasswordRequestForm
from sqlalchemy.orm import Session
import json
import datetime
from typing import List, Dict, Any

import models
import schemas
import auth
import ledger
from database import get_db, engine

# Create Tables (if not exist)
models.Base.metadata.create_all(bind=engine)

app = FastAPI(title="Doctor's Portal", description="Secure ECG Analysis Portal")

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

@app.get("/ecg/{record_id}", response_class=HTMLResponse)
async def view_ecg(record_id: int, request: Request, db: Session = Depends(get_db)):
    record = db.query(models.ECGRecord).filter(models.ECGRecord.id == record_id).first()
    if not record:
        raise HTTPException(status_code=404, detail="Record not found")
    
    # Parse processing results for display
    results = record.processing_results
    if isinstance(results, str):
        results = json.loads(results)
        
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
            name=f"Patient {p_id_ext}", # Placeholder name
            dob="Unknown"
        )
        db.add(patient)
        db.commit()
        db.refresh(patient)
    
    # 3. Extract Processing Results
    # Assuming standard EDGE format: results['results']['ml_inference']...
    ml_results = data.results.get('results', {}).get('ml_inference', {})
    hr_results = data.results.get('results', {}).get('heart_rate', {})
    
    classification = ml_results.get('classification', '?')
    confidence = ml_results.get('confidence', 0.0)
    bpm = hr_results.get('heart_rate_bpm', 0.0)
    
    # 4. Create ECG Record
    new_record = models.ECGRecord(
        patient_id=patient.id,
        timestamp=datetime.datetime.utcnow(),
        device_id=meta.get('device_id', 'EDGE_DEV'),
        heart_rate=bpm,
        classification=classification,
        confidence=confidence,
        ecg_data=data.ecg_values, # Stores as JSON
        processing_results=data.results
    )
    
    db.add(new_record)
    db.commit()
    db.refresh(new_record)
    
    # 5. Log to Cryptographic Audit Trail
    ledger.add_audit_entry(
        db, 
        actor_id=f"DEVICE_{meta.get('device_id')}", 
        action="INGEST_ECG", 
        details={"record_id": new_record.id, "patient": p_id_ext, "class": classification}
    )
    
    return {"status": "success", "record_id": new_record.id}

@app.get("/api/system/latest-record-id")
async def get_latest_record_id(db: Session = Depends(get_db)):
    """Public endpoint for dashboard polling"""
    last_record = db.query(models.ECGRecord.id).order_by(models.ECGRecord.id.desc()).first()
    return {"latest_id": last_record.id if last_record else 0}

@app.get("/api/audit/verify")
async def verify_audit_trail(db: Session = Depends(get_db)):
    """Check if the cryptographic audit ledger integrity is intact"""
    is_valid = ledger.verify_chain_integrity(db)
    return {"integrity_status": "Valid" if is_valid else "CORRUPTED"}

@app.get("/audit-ledger", response_class=HTMLResponse)
async def audit_ledger_view(request: Request, db: Session = Depends(get_db)):
    """View Cryptographic Audit Ledger"""
    # Get all audit entries
    entries = db.query(models.AuditLog).order_by(models.AuditLog.id.desc()).limit(100).all()
    
    # Verify chain integrity
    is_valid = ledger.verify_chain_integrity(db)
    
    return templates.TemplateResponse("audit_ledger.html", {
        "request": request,
        "entries": entries,
        "chain_valid": is_valid,
        "title": "Cryptographic Audit Ledger"
    })

# --- Patient Features ---

@app.get("/api/patients/search")
async def search_patients(q: str, db: Session = Depends(get_db)):
    """Search patients by ID or Name"""
    patients = db.query(models.Patient).filter(
        (models.Patient.patient_id_external.contains(q)) | 
        (models.Patient.name.contains(q))
    ).limit(10).all()
    
    return [{"id": p.id, "external_id": p.patient_id_external, "name": p.name} for p in patients]

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
