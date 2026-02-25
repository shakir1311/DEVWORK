import sqlite3
import os

DB_PATH = os.path.join(os.path.dirname(__file__), "portal.db")

def migrate():
    print(f"Migrating {DB_PATH} to add provenance columns...")
    
    if not os.path.exists(DB_PATH):
        print("Database not found. Nothing to migrate.")
        return
        
    conn = sqlite3.connect(DB_PATH)
    cursor = conn.cursor()
    
    try:
        # Check if columns already exist
        cursor.execute("PRAGMA table_info(ecg_records)")
        columns = [info[1] for info in cursor.fetchall()]
        
        if "model_version" not in columns:
            cursor.execute("ALTER TABLE ecg_records ADD COLUMN model_version VARCHAR;")
            print("Added column model_version")
            
        if "model_hash" not in columns:
            cursor.execute("ALTER TABLE ecg_records ADD COLUMN model_hash VARCHAR;")
            print("Added column model_hash")
            
        if "data_hash" not in columns:
            cursor.execute("ALTER TABLE ecg_records ADD COLUMN data_hash VARCHAR;")
            print("Added column data_hash")
            
        conn.commit()
        print("Migration complete!")
        
    except Exception as e:
        print(f"Error during migration: {e}")
    finally:
        conn.close()

if __name__ == "__main__":
    migrate()
