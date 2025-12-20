from pydantic import BaseModel
from typing import List, Optional, Any
from datetime import datetime

class Token(BaseModel):
    access_token: str
    token_type: str

class TokenData(BaseModel):
    username: Optional[str] = None

class ProcessingResults(BaseModel):
    heart_rate_bpm: float
    classification: str
    confidence: float
    probabilities: List[Any]
    
class PatientInfo(BaseModel):
    patient_id: str
    
class Metadata(BaseModel):
    patient_info: PatientInfo
    device_id: Optional[str] = "Unknown"

class ECGIngestRequest(BaseModel):
    """Data sent from EDGE"""
    ecg_data: List[float]
    metadata: Metadata
    results: ProcessingResults

class AuditLogEntry(BaseModel):
    id: int
    timestamp: datetime
    actor_id: str
    action: str
    details: str
    prev_hash: str
    record_hash: str

    class Config:
        from_attributes = True
