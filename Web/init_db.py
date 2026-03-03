from database import engine, SessionLocal
import models
from auth import get_password_hash
from ledger import add_audit_entry

def init_db():
    # Create Tables
    models.Base.metadata.create_all(bind=engine)
    
    db = SessionLocal()
    
    # Check if default user exists
    user = db.query(models.User).filter(models.User.username == "dr_green").first()
    if not user:
        print("Creating default Doctor user...")
        pwd = get_password_hash("medical_secure!")
        new_user = models.User(
            username="dr_green",
            hashed_password=pwd,
            full_name="Dr. John Green",
            role="doctor"
        )
        db.add(new_user)
        db.commit()
        
        # Log to cryptographic audit ledger
        add_audit_entry(db, "SYSTEM", "USER_CREATE", {"username": "dr_green", "role": "doctor"})
        
    # Create Admin
    admin = db.query(models.User).filter(models.User.username == "admin").first()
    if not admin:
        print("Creating default Admin user...")
        pwd = get_password_hash("admin_secret_key")
        new_admin = models.User(
            username="admin",
            hashed_password=pwd,
            full_name="System Administrator",
            role="admin"
        )
        db.add(new_admin)
        db.commit()
        
        add_audit_entry(db, "SYSTEM", "USER_CREATE", {"username": "admin", "role": "admin"})
        
    # Create Mock Patients


    db.close()
    print("Database initialization complete.")

if __name__ == "__main__":
    init_db()
