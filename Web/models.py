from sqlalchemy import Column, Integer, String, Float, DateTime, ForeignKey, Text, JSON, Boolean
from sqlalchemy.orm import relationship
from database import Base
import datetime

class User(Base):
    __tablename__ = "users"

    id = Column(Integer, primary_key=True, index=True)
    username = Column(String, unique=True, index=True)
    hashed_password = Column(String)
    full_name = Column(String)
    role = Column(String, default="doctor") # doctor, admin, device
    is_active = Column(Boolean, default=True)

class Patient(Base):
    __tablename__ = "patients"

    id = Column(Integer, primary_key=True, index=True)
    patient_id_external = Column(String, unique=True, index=True) # e.g. "A0001"
    name = Column(String)
    dob = Column(String) # Date of Birth
    
    ecg_records = relationship("ECGRecord", back_populates="patient")

class ECGRecord(Base):
    __tablename__ = "ecg_records"

    id = Column(Integer, primary_key=True, index=True)
    patient_id = Column(Integer, ForeignKey("patients.id"))
    timestamp = Column(DateTime, default=datetime.datetime.utcnow)
    
    # Metadata
    device_id = Column(String)
    firmware_version = Column(String)
    
    # Processing Results
    heart_rate = Column(Float)
    classification = Column(String) # N, A, O, ~
    confidence = Column(Float)
    
    # Tamper-Evident Provenance
    model_version = Column(String, nullable=True)
    model_hash = Column(String, nullable=True)
    data_hash = Column(String, nullable=True)
    
    # Data Storage (JSON blobs)
    ecg_data = Column(JSON) # The raw/filtered samples
    processing_results = Column(JSON) # Full results dict
    
    patient = relationship("Patient", back_populates="ecg_records")

class AuditLog(Base):
    """
    Immutable Audit Ledger (Hash-chained cryptographic implementation).
    Each record is cryptographically linked to the previous one via 'prev_hash'.
    """
    __tablename__ = "audit_log"

    id = Column(Integer, primary_key=True, index=True)
    timestamp = Column(DateTime, default=datetime.datetime.utcnow)
    actor_id = Column(String) # User ID or Device ID who performed the action
    action = Column(String) # e.g. "LOGIN", "VIEW_ECG", "INGEST_DATA"
    details = Column(String) # JSON string of details
    
    # Cryptographic Linkage
    prev_hash = Column(String) # Hash of the previous record
    record_hash = Column(String) # Hash of (prev_hash + timestamp + actor + action + details)

    # To verify integrity, we re-calculate hash and match with record_hash
