# Audit Ledger Architecture - Web Portal

## Overview

The Web Portal implements a **Hash-Chained Audit Ledger** for integrity verification and tamper detection. This is a cryptographically-linked immutable log that ensures all system actions are permanently recorded and verifiable.

## System Architecture

```mermaid
graph TB
    subgraph "Event Sources"
        A1[User Login]
        A2[ECG Data Ingestion]
        A3[Data Access Events]
        A4[System Operations]
    end
    
    subgraph "Web Portal Application Layer"
        B1[FastAPI Routes]
        B2[Authentication Module]
        B3[Data Ingestion API]
    end
    
    subgraph "Audit Ledger Module"
        C1[ledger.py]
        C2[add_audit_entry]
        C3[calculate_hash]
        C4[verify_chain_integrity]
    end
    
    subgraph "Data Persistence Layer"
        D1[(SQLite Database)]
        D2[AuditLog Table]
    end
    
    subgraph "Verification & Monitoring"
        E1[Chain Integrity Verification]
        E2[Audit Ledger Viewer UI]
        E3[API Verification Endpoint]
    end
    
    A1 --> B2
    A2 --> B3
    A3 --> B1
    A4 --> B1
    
    B1 --> C2
    B2 --> C2
    B3 --> C2
    
    C2 --> C3
    C2 --> D2
    
    D2 --> D1
    
    D1 --> C4
    C4 --> E1
    E1 --> E2
    E1 --> E3
    
    style C1 fill:#4a90e2,stroke:#2e5c8a,color:#fff
    style D2 fill:#e74c3c,stroke:#c0392b,color:#fff
    style E1 fill:#2ecc71,stroke:#27ae60,color:#fff
```

## Hash-Chain Structure

```mermaid
graph LR
    subgraph "Genesis Block"
        G[prev_hash: 0x000...000<br/>timestamp: T0<br/>actor: SYSTEM<br/>action: INIT<br/>hash: H0]
    end
    
    subgraph "Block 1"
        B1[prev_hash: H0<br/>timestamp: T1<br/>actor: dr_green<br/>action: LOGIN_SUCCESS<br/>hash: H1]
    end
    
    subgraph "Block 2"
        B2[prev_hash: H1<br/>timestamp: T2<br/>actor: DEVICE_ESP32<br/>action: INGEST_ECG<br/>hash: H2]
    end
    
    subgraph "Block N"
        BN[prev_hash: H_N-1<br/>timestamp: TN<br/>actor: dr_green<br/>action: VIEW_ECG<br/>hash: HN]
    end
    
    G -->|Links to| B1
    B1 -->|Links to| B2
    B2 -->|...| BN
    
    style G fill:#95a5a6,stroke:#7f8c8d,color:#fff
    style B1 fill:#3498db,stroke:#2980b9,color:#fff
    style B2 fill:#3498db,stroke:#2980b9,color:#fff
    style BN fill:#3498db,stroke:#2980b9,color:#fff
```

## Data Model

### AuditLog Table Schema

```mermaid
erDiagram
    AuditLog {
        int id PK "Auto-increment primary key"
        datetime timestamp "UTC timestamp of event"
        string actor_id "User ID or Device ID"
        string action "Event type (LOGIN, INGEST_ECG, etc.)"
        string details "JSON string with event details"
        string prev_hash "SHA-256 hash of previous record"
        string record_hash "SHA-256 hash of current record"
    }
```

**Field Descriptions:**
- **id**: Sequential identifier for database indexing
- **timestamp**: When the event occurred (UTC)
- **actor_id**: Who performed the action (user or device)
- **action**: Type of event (LOGIN_SUCCESS, INGEST_ECG, VIEW_ECG, etc.)
- **details**: JSON-encoded additional information
- **prev_hash**: Cryptographic link to previous record (64-char hex)
- **record_hash**: SHA-256 hash of this record's content (64-char hex)

## Hash Calculation Algorithm

```mermaid
flowchart TD
    A[Start: New Audit Event] --> B[Query Last Record]
    B --> C{First Record?}
    C -->|Yes| D[prev_hash = '0' × 64]
    C -->|No| E[prev_hash = last_record.record_hash]
    
    D --> F[Prepare Event Data]
    E --> F
    
    F --> G[Create Payload String:<br/>prev_hash + timestamp + actor_id + action + details]
    G --> H[Calculate SHA-256 Hash]
    H --> I[Create New AuditLog Entry]
    I --> J[Save to Database]
    J --> K[Commit Transaction]
    K --> L[End]
    
    style A fill:#2ecc71,stroke:#27ae60,color:#fff
    style H fill:#e74c3c,stroke:#c0392b,color:#fff
    style K fill:#f39c12,stroke:#d68910,color:#fff
    style L fill:#2ecc71,stroke:#27ae60,color:#fff
```

**Hash Formula:**
```
record_hash = SHA256(prev_hash || timestamp || actor_id || action || details)
```

Where `||` represents string concatenation.

## Integrity Verification Process

```mermaid
flowchart TD
    A[Start Verification] --> B[Fetch All Audit Entries<br/>Ordered by ID ASC]
    B --> C[Initialize:<br/>expected_prev_hash = '0' × 64]
    C --> D{More Entries?}
    
    D -->|No| E[✓ Chain Valid]
    D -->|Yes| F[Get Next Entry]
    
    F --> G{entry.prev_hash ==<br/>expected_prev_hash?}
    G -->|No| H[✗ Broken Link Detected]
    G -->|Yes| I[Recalculate Hash:<br/>SHA256 of entry data]
    
    I --> J{calculated_hash ==<br/>entry.record_hash?}
    J -->|No| K[✗ Data Tampering Detected]
    J -->|Yes| L[Update:<br/>expected_prev_hash = entry.record_hash]
    
    L --> D
    
    style E fill:#2ecc71,stroke:#27ae60,color:#fff
    style H fill:#e74c3c,stroke:#c0392b,color:#fff
    style K fill:#e74c3c,stroke:#c0392b,color:#fff
    style I fill:#3498db,stroke:#2980b9,color:#fff
```

## Event Flow Examples

### Example 1: User Login Event

```mermaid
sequenceDiagram
    participant U as User (Browser)
    participant API as FastAPI /token
    participant Auth as auth.py
    participant Ledger as ledger.py
    participant DB as Database
    
    U->>API: POST /token<br/>(username, password)
    API->>Auth: verify_password()
    Auth-->>API: ✓ Valid
    API->>Auth: create_access_token()
    Auth-->>API: JWT Token
    
    API->>Ledger: add_audit_entry()<br/>actor="dr_green"<br/>action="LOGIN_SUCCESS"
    Ledger->>DB: Query last record
    DB-->>Ledger: prev_hash
    Ledger->>Ledger: calculate_hash()
    Ledger->>DB: INSERT AuditLog
    DB-->>Ledger: ✓ Committed
    
    API-->>U: Return JWT Token
```

### Example 2: ECG Data Ingestion Event

```mermaid
sequenceDiagram
    participant EDGE as EDGE Device
    participant API as FastAPI /api/ingest
    participant DB as Database
    participant Ledger as ledger.py
    
    EDGE->>API: POST /api/ingest<br/>(ECG data + metadata)
    API->>DB: Find/Create Patient
    DB-->>API: Patient Object
    API->>DB: Create ECGRecord
    DB-->>API: record_id
    
    API->>Ledger: add_audit_entry()<br/>actor="DEVICE_ESP32"<br/>action="INGEST_ECG"<br/>details={record_id, patient, class}
    
    Ledger->>DB: Query last audit record
    DB-->>Ledger: prev_hash
    Ledger->>Ledger: calculate_hash()
    Ledger->>DB: INSERT AuditLog
    DB-->>Ledger: ✓ Committed
    
    API-->>EDGE: {"status": "success", "record_id": 123}
```

### Example 3: Chain Integrity Verification

```mermaid
sequenceDiagram
    participant UI as Audit Ledger UI
    participant API as FastAPI /api/audit/verify
    participant Ledger as ledger.py
    participant DB as Database
    
    UI->>API: GET /api/audit/verify
    API->>Ledger: verify_chain_integrity()
    Ledger->>DB: Query all AuditLog entries
    DB-->>Ledger: All entries (ordered)
    
    loop For each entry
        Ledger->>Ledger: Verify prev_hash link
        Ledger->>Ledger: Recalculate hash
        Ledger->>Ledger: Compare with stored hash
    end
    
    Ledger-->>API: is_valid = True/False
    API-->>UI: {"integrity_status": "Valid"}
    
    Note over UI: Display status badge<br/>and chain visualization
```

## User Interface Components

### Audit Ledger Viewer

The Web Portal provides a dedicated UI at `/audit-ledger` that displays:

1. **Chain Integrity Status Badge**
   - Green: ✓ Valid - All hashes verified
   - Red: ✗ CORRUPTED - Tampering detected

2. **Audit Entry Table**
   - Timestamp (UTC)
   - Actor ID (User or Device)
   - Action Type
   - Details (JSON formatted)
   - Previous Hash (truncated)
   - Record Hash (truncated)

3. **Visual Chain Representation**
   - Shows cryptographic linkage between entries
   - Highlights any broken links or hash mismatches

## Security Properties

### Immutability Guarantees

```mermaid
graph TD
    A[Audit Entry Created] --> B[Hash Calculated]
    B --> C[Linked to Previous Hash]
    C --> D[Committed to Database]
    D --> E{Attempt to Modify?}
    
    E -->|Modify timestamp| F[Hash Mismatch Detected]
    E -->|Modify action| F
    E -->|Modify details| F
    E -->|Modify prev_hash| G[Broken Link Detected]
    
    F --> H[verify_chain_integrity = FALSE]
    G --> H
    H --> I[Tampering Alert]
    
    style D fill:#2ecc71,stroke:#27ae60,color:#fff
    style F fill:#e74c3c,stroke:#c0392b,color:#fff
    style G fill:#e74c3c,stroke:#c0392b,color:#fff
    style I fill:#e74c3c,stroke:#c0392b,color:#fff
```

### Tamper Detection Mechanisms

1. **Hash Verification**: Each record's hash is recalculated and compared
2. **Chain Linkage**: Each record must point to the correct previous hash
3. **Sequential Integrity**: Any modification breaks the chain from that point forward
4. **Append-Only**: New entries can only be added to the end of the chain

## API Endpoints

### Audit-Related Endpoints

| Endpoint | Method | Purpose | Response |
|----------|--------|---------|----------|
| `/api/audit/verify` | GET | Verify chain integrity | `{"integrity_status": "Valid\|CORRUPTED"}` |
| `/audit-ledger` | GET | View audit ledger UI | HTML page with entries and status |

### Events Logged to Audit Ledger

| Event Type | Actor | Details |
|------------|-------|---------|
| `LOGIN_SUCCESS` | Username | IP address |
| `INGEST_ECG` | Device ID | Record ID, Patient ID, Classification |
| `USER_CREATE` | SYSTEM | Username, Role |
| `VIEW_ECG` | Username | Record ID (future) |
| `DATA_EXPORT` | Username | Export parameters (future) |

## Key Differences from Traditional Blockchain

> [!IMPORTANT]
> This system is a **Hash-Chained Audit Ledger**, not a traditional blockchain:

| Feature | Traditional Blockchain | This Audit Ledger |
|---------|----------------------|-------------------|
| **Consensus** | Distributed (PoW, PoS, etc.) | Single authority (Web Portal) |
| **Network** | Peer-to-peer | Centralized database |
| **Validation** | Multiple nodes | Single server verification |
| **Purpose** | Decentralized trust | Tamper detection & audit trail |
| **Performance** | Slower (consensus overhead) | Fast (local database) |
| **Use Case** | Cryptocurrency, DApps | Clinical audit logging |

**Why This Design?**

- ✓ **Regulatory Compliance**: Immutable audit trail for medical data
- ✓ **Tamper Detection**: Any modification is immediately detectable
- ✓ **Performance**: No consensus overhead, suitable for high-frequency events
- ✓ **Simplicity**: Easier to implement and maintain than distributed blockchain
- ✓ **Sufficient Security**: For permissioned medical system with trusted operators

## Implementation Files

| File | Purpose | Key Functions |
|------|---------|---------------|
| [ledger.py](file:///Volumes/Stuff/GDrive2026/Abertay/research/DEVWORK/WEB/ledger.py) | Core audit ledger logic | `add_audit_entry()`, `calculate_hash()`, `verify_chain_integrity()` |
| [models.py](file:///Volumes/Stuff/GDrive2026/Abertay/research/DEVWORK/WEB/models.py#L48-L66) | Database schema | `AuditLog` model definition |
| [main.py](file:///Volumes/Stuff/GDrive2026/Abertay/research/DEVWORK/WEB/main.py) | API endpoints & event triggers | Login, ingestion, verification endpoints |

## Future Enhancements

> [!TIP]
> Potential improvements for enhanced security and functionality:

1. **Digital Signatures**: Add cryptographic signatures from actors
2. **Merkle Trees**: Implement Merkle tree for efficient batch verification
3. **Periodic Anchoring**: Publish hash checkpoints to external immutable storage
4. **Real-time Monitoring**: Alert system for integrity violations
5. **Audit Search**: Advanced filtering and search capabilities
6. **Export Compliance**: Generate audit reports for regulatory submissions
