# System Architecture Documentation

This document provides comprehensive architecture diagrams for the ECG Remote Patient Monitoring system, including individual component architectures and a high-level system integration view.

---

## 1. DataSimulator Architecture

The DataSimulator is a PyQt6-based application that simulates ECG data from the PhysioNet CinC 2017 dataset and publishes it via MQTT for embedded device consumption.

### Component Diagram

```mermaid
graph TB
    subgraph "DataSimulator Application"
        subgraph "Presentation Layer"
            GUI[ecg_gui.py<br/>PyQt6 GUI]
        end
        
        subgraph "Control Layer"
            Controller[app_controller.py<br/>Application Controller]
            Worker[simulator_worker.py<br/>Background Worker Thread]
        end
        
        subgraph "Model Layer"
            Simulator[ecg_simulator.py<br/>ECG Dataset Manager]
            HEAParser[hea_parser.py<br/>Metadata Parser]
            Downloader[dataset_downloader.py<br/>PhysioNet Downloader]
        end
        
        subgraph "Communication Layer"
            Broker[mqtt_broker.py<br/>Embedded MQTT Broker<br/>Port: 1883]
            Discovery[UDP Discovery Responder<br/>Port: 1884]
        end
        
        subgraph "Data Storage"
            Dataset[(PhysioNet CinC 2017<br/>8,528 ECG Recordings<br/>300 Hz Sampling)]
            RefCSV[REFERENCE.csv<br/>Rhythm Classifications]
        end
        
        Config[config.py<br/>Configuration]
    end
    
    subgraph "External Systems"
        PhysioNet[PhysioNet Archive<br/>archive.physionet.org]
        ESP32[ESP32 Devices<br/>MQTT Clients]
    end
    
    GUI --> Controller
    Controller --> Simulator
    Controller --> Worker
    Worker --> Simulator
    Worker --> Broker
    
    Simulator --> HEAParser
    Simulator --> Dataset
    Simulator --> RefCSV
    
    Downloader --> PhysioNet
    Downloader --> Dataset
    Downloader --> RefCSV
    
    Broker --> ESP32
    Discovery -.-> ESP32
    
    Config -.-> GUI
    Config -.-> Controller
    Config -.-> Broker
    
    style Broker fill:#e1f5ff
    style Discovery fill:#e1f5ff
    style Dataset fill:#fff4e1
    style PhysioNet fill:#f0f0f0
```

### Data Flow Diagram

```mermaid
sequenceDiagram
    participant User
    participant GUI
    participant Controller
    participant Simulator
    participant Downloader
    participant Worker
    participant Broker
    participant ESP32
    
    User->>GUI: Select Patient & Start
    GUI->>Controller: Start Simulation Request
    Controller->>Simulator: Load Patient Data
    
    alt Patient Not Downloaded
        Simulator->>Downloader: Download Patient Files
        Downloader->>Downloader: Fetch .mat & .hea
        Downloader-->>Simulator: Files Ready
    end
    
    Simulator->>Simulator: Parse .hea Metadata
    Simulator->>Simulator: Load ECG Values (300 Hz)
    Simulator-->>Controller: Data Ready
    
    Controller->>Worker: Start Publishing Thread
    Worker->>Worker: Package Full Record<br/>(Binary Payload)
    Worker->>Broker: Publish to ecg/full_record
    Broker->>ESP32: MQTT Message (QoS 0)
    
    Worker-->>GUI: Update Status
    GUI-->>User: Show Completion
```

### Key Features

**Architecture Pattern**: Model-View-Controller (MVC) with threading

**Components**:
- **ecg_gui.py**: PyQt6-based user interface with live ECG preview
- **app_controller.py**: Orchestrates application state and coordinates components
- **simulator_worker.py**: Background thread for non-blocking MQTT publishing
- **ecg_simulator.py**: Manages dataset, patient selection, and data loading
- **hea_parser.py**: Parses PhysioNet .hea header files for metadata
- **dataset_downloader.py**: Handles on-demand and bulk dataset downloads
- **mqtt_broker.py**: Embedded pure-Python MQTT broker (no external dependencies)
- **config.py**: Centralized configuration constants

**MQTT Protocol**:
- **Topic**: `ecg/full_record`
- **QoS**: 0 (fire-and-forget)
- **Payload**: Binary format with header + float array
  - Header: format_version, sampling_rate (300 Hz), sample_count, start_timestamp, end_timestamp
  - Body: IEEE-754 float32 array representing ECG voltage in mV
- **Typical Size**: 36 KB for 30s recording (9,000 samples)

**Discovery Protocol**:
- **UDP Broadcast**: Port 1884
- **Response**: Broker IP and port information

---

## 2. IOT (ESP32) Architecture

The IOT layer consists of ESP32 firmware that discovers MQTT brokers, receives full ECG records, and forwards chunked data to the EDGE layer for processing.

### Component Diagram

```mermaid
graph TB
    subgraph "ESP32 Firmware"
        subgraph "Network Layer"
            WiFi[WiFi Manager<br/>Auto-Reconnect]
            Discovery[UDP Discovery Client<br/>Broker Discovery]
        end
        
        subgraph "Communication Layer"
            SimMQTT[MQTT Client 1<br/>DataSimulator Connection<br/>Port: 1883]
            EdgeMQTT[MQTT Client 2<br/>EDGE Connection<br/>Port: 1885]
        end
        
        subgraph "Processing Layer"
            Parser[Binary Parser<br/>Full Record Decoder]
            Chunker[Data Chunker<br/>Splits into Chunks]
            Buffer[RAM Buffer<br/>~72 KB max]
        end
        
        subgraph "Hardware Layer"
            RAM[ESP32 RAM<br/>520 KB total]
            Flash[ESP32 Flash<br/>4 MB]
            Timer[Hardware Timer<br/>300 Hz Replay]
        end
        
        Config[Configuration<br/>WiFi Credentials<br/>Broker Settings]
    end
    
    subgraph "External Systems"
        SimBroker[DataSimulator Broker<br/>Port: 1883]
        EdgeBroker[EDGE Broker<br/>Port: 1885]
    end
    
    WiFi --> Discovery
    Discovery -.-> SimBroker
    Discovery -.-> EdgeBroker
    
    WiFi --> SimMQTT
    WiFi --> EdgeMQTT
    
    SimMQTT --> SimBroker
    EdgeMQTT --> EdgeBroker
    
    SimMQTT --> Parser
    Parser --> Buffer
    Buffer --> Chunker
    Chunker --> EdgeMQTT
    
    Buffer --> RAM
    Timer --> Buffer
    
    Config -.-> WiFi
    Config -.-> SimMQTT
    Config -.-> EdgeMQTT
    
    style SimMQTT fill:#e1f5ff
    style EdgeMQTT fill:#e1f5ff
    style Buffer fill:#fff4e1
    style RAM fill:#ffe1e1
```

### Data Flow Diagram

```mermaid
sequenceDiagram
    participant DataSim as DataSimulator Broker
    participant ESP32
    participant EdgeBroker as EDGE Broker
    
    Note over ESP32: Power On / Reset
    ESP32->>ESP32: Connect to WiFi
    ESP32->>ESP32: UDP Broadcast Discovery
    
    DataSim-->>ESP32: Broker Info (IP:1883)
    EdgeBroker-->>ESP32: Broker Info (IP:1885)
    
    ESP32->>DataSim: MQTT Connect (Port 1883)
    ESP32->>EdgeBroker: MQTT Connect (Port 1885)
    
    ESP32->>DataSim: Subscribe to ecg/full_record
    
    DataSim->>ESP32: Full ECG Record (Binary)<br/>~36 KB payload
    
    ESP32->>ESP32: Parse Binary Header<br/>(format, rate, timestamps)
    ESP32->>ESP32: Buffer Float Array<br/>(9,000 samples in RAM)
    
    loop For Each Chunk (e.g., 500 samples)
        ESP32->>ESP32: Extract Chunk from Buffer
        ESP32->>ESP32: Create Binary Chunk<br/>(header + CSV values)
        ESP32->>EdgeBroker: Publish to ecg/edge/chunk
        EdgeBroker-->>ESP32: ACK (optional)
    end
    
    Note over ESP32: Buffer cleared, ready for next record
```

### Key Features

**Hardware Platform**: ESP32 (any variant)
- **RAM**: 520 KB (sufficient for 60s ECG records)
- **Flash**: 4 MB
- **WiFi**: 802.11 b/g/n

**Firmware Components**:
- **WiFi Manager**: Automatic connection and reconnection
- **UDP Discovery**: Finds DataSimulator and EDGE brokers automatically
- **Dual MQTT Clients**: Separate connections to DataSimulator (1883) and EDGE (1885)
- **Binary Parser**: Decodes full-record binary payload
- **Data Chunker**: Splits large records into manageable chunks for EDGE
- **Error Handling**: Comprehensive exception handling and recovery

**Communication Protocols**:
1. **Receive from DataSimulator**:
   - Topic: `ecg/full_record`
   - Format: Binary (header + float32 array)
   - Size: ~36 KB typical

2. **Send to EDGE**:
   - Topic: `ecg/edge/chunk`
   - Format: Binary header + CSV body
   - Chunk Size: Configurable (e.g., 500 samples)

**Memory Management**:
- Full record buffered in RAM
- Chunked transmission to minimize EDGE memory requirements
- Automatic buffer cleanup after transmission

---

## 3. EDGE Architecture

The EDGE layer runs on a Raspberry Pi 4 and serves as the intelligent processing gateway between ESP32 devices and the Web Portal.

### Component Diagram

```mermaid
graph TB
    subgraph "EDGE Application (Raspberry Pi 4)"
        subgraph "Presentation Layer"
            GUI[edge_gui.py<br/>PyQt6 Real-time Visualization]
        end
        
        subgraph "Communication Layer"
            Broker[mqtt_broker.py<br/>Embedded MQTT Broker<br/>Port: 1885]
            Discovery[mqtt_discovery.py<br/>UDP Discovery Responder<br/>Port: 1886]
            Client[mqtt_client.py<br/>MQTT Client Wrapper]
        end
        
        subgraph "Processing Layer"
            Receiver[chunk_receiver.py<br/>ECG Chunk Assembler]
            BaseProcessor[ecg_processor.py<br/>Base Processor Class]
            
            subgraph "Processing Modules"
                HRProc[heart_rate_processor.py<br/>Heart Rate Calculation]
                MLProc[ml_inference_processor.py<br/>Rhythm Classification]
                OtherProc[... Other Processors<br/>Extensible Pipeline]
            end
        end
        
        subgraph "Storage Layer"
            Storage[data_storage.py<br/>NPZ + JSON Storage]
            DataDir[(data/received_ecg/<br/>ECG Records)]
        end
        
        subgraph "API Layer"
            WebAPI[HTTP Client<br/>Web Portal API]
        end
        
        Config[config.py<br/>Configuration<br/>Processing Modules]
        Main[main.py<br/>Application Entry Point]
    end
    
    subgraph "External Systems"
        ESP32[ESP32 Devices]
        WebPortal[Web Portal<br/>FastAPI Backend]
    end
    
    ESP32 --> Broker
    Discovery -.-> ESP32
    
    Broker --> Client
    Client --> Receiver
    
    Receiver --> BaseProcessor
    BaseProcessor --> HRProc
    BaseProcessor --> MLProc
    BaseProcessor --> OtherProc
    
    HRProc --> Storage
    MLProc --> Storage
    OtherProc --> Storage
    
    Storage --> DataDir
    Storage --> WebAPI
    WebAPI --> WebPortal
    
    Receiver --> GUI
    HRProc --> GUI
    MLProc --> GUI
    
    Main --> Broker
    Main --> Client
    Main --> Receiver
    Main --> GUI
    
    Config -.-> Main
    Config -.-> Broker
    Config -.-> BaseProcessor
    
    style Broker fill:#e1f5ff
    style Discovery fill:#e1f5ff
    style DataDir fill:#fff4e1
    style MLProc fill:#e1ffe1
```

### Data Flow Diagram

```mermaid
sequenceDiagram
    participant ESP32
    participant Broker as EDGE Broker
    participant Receiver as Chunk Receiver
    participant Processors as Processing Pipeline
    participant Storage
    participant WebPortal as Web Portal API
    participant GUI
    
    ESP32->>Broker: Connect via UDP Discovery
    ESP32->>Broker: Subscribe to ecg/edge/ack
    
    loop For Each Chunk
        ESP32->>Broker: Publish ecg/edge/chunk<br/>(Binary: header + CSV)
        Broker->>Receiver: Forward Chunk
        
        Receiver->>Receiver: Parse Binary Header
        Receiver->>Receiver: Parse CSV Body
        Receiver->>Receiver: Assemble Chunks
        
        alt All Chunks Received
            Receiver->>Processors: Full ECG Record Ready
            
            par Parallel Processing
                Processors->>Processors: Heart Rate Calculation
                Processors->>Processors: ML Inference (Rhythm)
                Processors->>Processors: Other Analyses
            end
            
            Processors->>Storage: Save Results + ECG Data
            Storage->>Storage: Write NPZ (compressed)
            Storage->>Storage: Write JSON (metadata)
            
            Storage->>WebPortal: POST /api/ingest<br/>(ECG + Results + Metadata)
            WebPortal-->>Storage: ACK (record_id)
            
            Processors->>GUI: Update Visualization
            GUI->>GUI: Plot ECG Waveform
            GUI->>GUI: Display Results
        end
        
        Broker->>ESP32: Publish ecg/edge/ack
    end
```

### Key Features

**Platform**: Raspberry Pi 4 (or similar Linux SBC)

**Architecture Pattern**: Modular Processing Pipeline

**Core Components**:
- **mqtt_broker.py**: Embedded MQTT broker for receiving from ESP32 (Port 1885)
- **mqtt_discovery.py**: UDP responder for broker discovery (Port 1886)
- **mqtt_client.py**: MQTT client wrapper for internal communication
- **chunk_receiver.py**: Assembles chunked ECG data from ESP32
- **ecg_processor.py**: Base class for extensible processing modules
- **data_storage.py**: Saves ECG data and results to disk
- **edge_gui.py**: Real-time ECG visualization with PyQt6
- **main.py**: Application orchestration and lifecycle management

**Processing Modules** (Extensible):
- **heart_rate_processor.py**: R-peak detection and heart rate calculation
- **ml_inference_processor.py**: Trained ML model for rhythm classification
- **Custom processors**: Easily add new processing modules

**MQTT Protocol**:
- **Receive Topic**: `ecg/edge/chunk`
- **ACK Topic**: `ecg/edge/ack`
- **Chunk Format**: Binary header (12 bytes) + CSV body
  - Header: format_version, sampling_rate, chunk_num, total_chunks, sample_count
  - Body: Comma-separated float values

**Data Storage**:
- **Format**: NPZ (compressed numpy) + JSON (metadata)
- **Location**: `./data/received_ecg/`
- **Naming**: `ecg_TIMESTAMP.npz` and `ecg_TIMESTAMP_metadata.json`

**Web Portal Integration**:
- **Endpoint**: `POST /api/ingest`
- **Payload**: ECG values, processing results, metadata
- **Response**: Record ID for tracking

**Discovery Protocol**:
- **UDP Port**: 1886 (different from DataSimulator's 1884)
- **Response**: EDGE broker IP and port (1885)

---

## 4. Web Portal Architecture

The Web Portal is a FastAPI-based secure web application for doctors to view ECG data, patient timelines, and audit trails.

### Component Diagram

```mermaid
graph TB
    subgraph "Web Portal Application"
        subgraph "Frontend (Templates)"
            Login[login.html<br/>Authentication Page]
            Dashboard[dashboard.html<br/>ECG Records Dashboard]
            ECGView[ecg_view.html<br/>Detailed ECG Visualization]
            PatientTimeline[patient_timeline.html<br/>Patient History]
            AuditLedger[audit_ledger.html<br/>Cryptographic Audit Trail]
        end
        
        subgraph "Backend (FastAPI)"
            Main[main.py<br/>API Routes & Web Routes]
            Auth[auth.py<br/>JWT Authentication]
            Ledger[ledger.py<br/>Cryptographic Audit Ledger]
            Models[models.py<br/>SQLAlchemy ORM Models]
            Schemas[schemas.py<br/>Pydantic Schemas]
        end
        
        subgraph "Database Layer"
            DB[(portal.db<br/>SQLite Database)]
            
            subgraph "Tables"
                Users[users<br/>Doctor Accounts]
                Patients[patients<br/>Patient Records]
                ECGRecords[ecg_records<br/>ECG Data & Results]
                AuditLog[audit_log<br/>Hash-Chained Ledger]
            end
        end
        
        subgraph "Static Assets"
            CSS[CSS Stylesheets<br/>Modern UI Design]
            JS[JavaScript<br/>AJAX Polling & Interactivity]
        end
    end
    
    subgraph "External Systems"
        EdgeDevice[EDGE Device<br/>Data Ingestion]
        Doctor[Doctor<br/>Web Browser]
    end
    
    Doctor --> Login
    Login --> Auth
    Auth --> Dashboard
    Dashboard --> ECGView
    Dashboard --> PatientTimeline
    Dashboard --> AuditLedger
    
    Main --> Auth
    Main --> Ledger
    Main --> Models
    Main --> Schemas
    
    Models --> DB
    DB --> Users
    DB --> Patients
    DB --> ECGRecords
    DB --> AuditLog
    
    Ledger --> AuditLog
    
    EdgeDevice --> Main
    Main --> ECGRecords
    Main --> Patients
    Main --> Ledger
    
    Dashboard --> CSS
    Dashboard --> JS
    ECGView --> CSS
    ECGView --> JS
    
    style Auth fill:#ffe1e1
    style Ledger fill:#e1ffe1
    style DB fill:#fff4e1
    style AuditLog fill:#e1ffe1
```

### Data Flow Diagram

```mermaid
sequenceDiagram
    participant Doctor
    participant Frontend
    participant FastAPI
    participant Auth
    participant Ledger
    participant Database
    participant EDGE
    
    Note over Doctor,Database: Authentication Flow
    Doctor->>Frontend: Enter Credentials
    Frontend->>FastAPI: POST /token
    FastAPI->>Auth: Verify Credentials
    Auth->>Database: Query users table
    Database-->>Auth: User Record
    Auth->>Auth: Generate JWT Token
    Auth->>Ledger: Log LOGIN_SUCCESS
    Ledger->>Database: Add Audit Entry (hash-chained)
    Auth-->>Frontend: JWT Token
    Frontend->>Frontend: Store Token (localStorage)
    
    Note over Doctor,Database: Data Ingestion Flow (from EDGE)
    EDGE->>FastAPI: POST /api/ingest<br/>(ECG + Results + Metadata)
    FastAPI->>Database: Find/Create Patient
    FastAPI->>Database: Create ECG Record
    FastAPI->>Ledger: Log INGEST_ECG
    Ledger->>Ledger: Calculate Hash Chain
    Ledger->>Database: Add Audit Entry
    FastAPI-->>EDGE: Success (record_id)
    
    Note over Doctor,Database: Dashboard View Flow
    Doctor->>Frontend: Access Dashboard
    Frontend->>FastAPI: GET /dashboard (with JWT)
    FastAPI->>Database: Query Recent ECG Records
    Database-->>FastAPI: Records (limit 20)
    FastAPI-->>Frontend: Render dashboard.html
    Frontend->>Frontend: Auto-refresh (AJAX polling)
    
    Note over Doctor,Database: ECG Detail View Flow
    Doctor->>Frontend: Click ECG Record
    Frontend->>FastAPI: GET /ecg/{record_id}
    FastAPI->>Database: Query ECG Record + Patient
    Database-->>FastAPI: Full Record Data
    FastAPI-->>Frontend: Render ecg_view.html<br/>(with Chart.js visualization)
    
    Note over Doctor,Database: Audit Ledger Verification
    Doctor->>Frontend: Access Audit Ledger
    Frontend->>FastAPI: GET /audit-ledger
    FastAPI->>Ledger: Verify Chain Integrity
    Ledger->>Database: Query All Audit Entries
    Ledger->>Ledger: Validate Hash Chain
    Ledger-->>FastAPI: Integrity Status
    FastAPI->>Database: Query Recent Entries
    FastAPI-->>Frontend: Render audit_ledger.html<br/>(with integrity badge)
```

### Key Features

**Framework**: FastAPI (Python)
- **Async Support**: High-performance async request handling
- **Auto Documentation**: OpenAPI/Swagger UI
- **Type Safety**: Pydantic schema validation

**Frontend**:
- **Template Engine**: Jinja2
- **Styling**: Modern CSS with responsive design
- **Interactivity**: Vanilla JavaScript with AJAX polling
- **Visualization**: Chart.js for ECG waveform rendering

**Authentication**:
- **Method**: JWT (JSON Web Tokens)
- **Storage**: localStorage (client-side)
- **Expiration**: Configurable token lifetime
- **Security**: Bcrypt password hashing

**Database** (SQLite):
- **users**: Doctor accounts (username, hashed_password, role)
- **patients**: Patient information (patient_id_external, name, dob)
- **ecg_records**: ECG data, processing results, metadata
- **audit_log**: Hash-chained cryptographic audit trail

**Cryptographic Audit Ledger**:
- **Hash Chain**: Each entry contains hash of previous entry
- **Immutability**: Any tampering breaks the chain
- **Verification**: `/api/audit/verify` endpoint checks integrity
- **Logged Actions**: LOGIN_SUCCESS, INGEST_ECG, VIEW_RECORD, etc.

**API Endpoints**:
- **POST /token**: Authentication (returns JWT)
- **GET /dashboard**: Main dashboard view
- **GET /ecg/{record_id}**: Detailed ECG view
- **GET /patient/{patient_id}**: Patient timeline
- **GET /audit-ledger**: Cryptographic audit trail view
- **POST /api/ingest**: Data ingestion from EDGE (public)
- **GET /api/audit/verify**: Verify audit chain integrity
- **GET /api/patients/search**: Patient search

**Security Features**:
- JWT-based authentication
- Password hashing (Bcrypt)
- Cryptographic audit trail (hash-chained)
- HTTPS ready (production deployment)
- Role-based access control (extensible)

---

## 5. High-Level System Integration Architecture

This diagram shows how all components interact to form the complete Remote Patient Monitoring system.

```mermaid
graph TB
    subgraph "Data Source Layer"
        PhysioNet[PhysioNet Archive<br/>CinC 2017 Dataset<br/>8,528 ECG Recordings]
    end
    
    subgraph "Simulation Layer"
        DataSim[DataSimulator<br/>PyQt6 Application<br/>macOS/Linux/Windows]
        SimBroker[MQTT Broker<br/>Port: 1883]
        SimDiscovery[UDP Discovery<br/>Port: 1884]
        
        DataSim --> SimBroker
        DataSim --> SimDiscovery
    end
    
    subgraph "IoT Device Layer"
        ESP32_1[ESP32 Device 1<br/>WiFi Connected]
        ESP32_2[ESP32 Device 2<br/>WiFi Connected]
        ESP32_N[ESP32 Device N<br/>WiFi Connected]
        
        ESP32_1 -.-> SimDiscovery
        ESP32_2 -.-> SimDiscovery
        ESP32_N -.-> SimDiscovery
    end
    
    subgraph "Edge Processing Layer (Raspberry Pi 4)"
        EdgeBroker[MQTT Broker<br/>Port: 1885]
        EdgeDiscovery[UDP Discovery<br/>Port: 1886]
        EdgeProcessor[Processing Pipeline<br/>Heart Rate + ML Inference]
        EdgeStorage[(Local Storage<br/>NPZ + JSON)]
        EdgeGUI[Real-time GUI<br/>PyQt6 Visualization]
        
        EdgeBroker --> EdgeProcessor
        EdgeProcessor --> EdgeStorage
        EdgeProcessor --> EdgeGUI
    end
    
    subgraph "Cloud/Server Layer"
        WebPortal[Web Portal<br/>FastAPI Backend]
        WebDB[(SQLite Database<br/>Patients + ECG + Audit)]
        WebFrontend[Web Frontend<br/>Jinja2 Templates + JS]
        AuditLedger[Cryptographic Audit Ledger<br/>Hash-Chained Immutable Log]
        
        WebPortal --> WebDB
        WebPortal --> AuditLedger
        WebPortal --> WebFrontend
        AuditLedger --> WebDB
    end
    
    subgraph "User Layer"
        Doctor[Doctor<br/>Web Browser<br/>Authenticated Access]
    end
    
    PhysioNet -.->|Download Dataset| DataSim
    
    SimBroker -->|MQTT: ecg/full_record<br/>Binary Payload ~36KB| ESP32_1
    SimBroker -->|MQTT: ecg/full_record<br/>Binary Payload ~36KB| ESP32_2
    SimBroker -->|MQTT: ecg/full_record<br/>Binary Payload ~36KB| ESP32_N
    
    ESP32_1 -.->|UDP Discovery| EdgeDiscovery
    ESP32_2 -.->|UDP Discovery| EdgeDiscovery
    ESP32_N -.->|UDP Discovery| EdgeDiscovery
    
    ESP32_1 -->|MQTT: ecg/edge/chunk<br/>Chunked Binary| EdgeBroker
    ESP32_2 -->|MQTT: ecg/edge/chunk<br/>Chunked Binary| EdgeBroker
    ESP32_N -->|MQTT: ecg/edge/chunk<br/>Chunked Binary| EdgeBroker
    
    EdgeStorage -->|HTTP POST /api/ingest<br/>JSON: ECG + Results| WebPortal
    
    Doctor -->|HTTPS<br/>JWT Authentication| WebFrontend
    WebFrontend <-->|REST API| WebPortal
    
    style PhysioNet fill:#f0f0f0
    style SimBroker fill:#e1f5ff
    style EdgeBroker fill:#e1f5ff
    style ESP32_1 fill:#ffe1e1
    style ESP32_2 fill:#ffe1e1
    style ESP32_N fill:#ffe1e1
    style EdgeProcessor fill:#e1ffe1
    style WebPortal fill:#fff4e1
    style AuditLedger fill:#e1ffe1
    style Doctor fill:#f0f0f0
```

### System-Wide Data Flow

```mermaid
sequenceDiagram
    participant PhysioNet
    participant DataSim as DataSimulator
    participant ESP32
    participant EDGE
    participant WebPortal
    participant Doctor
    
    Note over PhysioNet,Doctor: System Initialization
    DataSim->>PhysioNet: Download Dataset (First Run)
    PhysioNet-->>DataSim: CinC 2017 Dataset (~167 MB)
    DataSim->>DataSim: Start MQTT Broker (Port 1883)
    DataSim->>DataSim: Start UDP Discovery (Port 1884)
    
    EDGE->>EDGE: Start MQTT Broker (Port 1885)
    EDGE->>EDGE: Start UDP Discovery (Port 1886)
    EDGE->>EDGE: Start Processing Pipeline
    
    WebPortal->>WebPortal: Start FastAPI Server
    WebPortal->>WebPortal: Initialize Database
    WebPortal->>WebPortal: Initialize Audit Ledger
    
    Note over PhysioNet,Doctor: Device Discovery & Connection
    ESP32->>ESP32: Power On, Connect WiFi
    ESP32->>DataSim: UDP Discovery Request
    DataSim-->>ESP32: Broker Info (IP:1883)
    ESP32->>EDGE: UDP Discovery Request
    EDGE-->>ESP32: Broker Info (IP:1885)
    
    ESP32->>DataSim: MQTT Connect (Port 1883)
    ESP32->>EDGE: MQTT Connect (Port 1885)
    ESP32->>DataSim: Subscribe: ecg/full_record
    ESP32->>EDGE: Subscribe: ecg/edge/ack
    
    Note over PhysioNet,Doctor: ECG Data Simulation & Transmission
    DataSim->>DataSim: User Selects Patient A00001
    DataSim->>DataSim: Load ECG (9,000 samples @ 300 Hz)
    DataSim->>DataSim: Package Binary Payload (~36 KB)
    DataSim->>ESP32: Publish ecg/full_record (Binary)
    
    ESP32->>ESP32: Receive & Buffer Full Record
    ESP32->>ESP32: Parse Header (rate, timestamps)
    
    loop For Each Chunk (e.g., 18 chunks of 500 samples)
        ESP32->>ESP32: Extract Chunk from Buffer
        ESP32->>EDGE: Publish ecg/edge/chunk (Binary)
        EDGE-->>ESP32: Publish ecg/edge/ack
    end
    
    Note over PhysioNet,Doctor: Edge Processing
    EDGE->>EDGE: Assemble All Chunks
    EDGE->>EDGE: Full ECG Record Reconstructed
    
    par Parallel Processing
        EDGE->>EDGE: Heart Rate Calculation<br/>(R-peak detection)
        EDGE->>EDGE: ML Inference<br/>(Rhythm Classification)
        EDGE->>EDGE: Other Analyses
    end
    
    EDGE->>EDGE: Save to Local Storage (NPZ + JSON)
    EDGE->>EDGE: Update Real-time GUI
    
    Note over PhysioNet,Doctor: Cloud Ingestion
    EDGE->>WebPortal: POST /api/ingest<br/>(ECG values + Results + Metadata)
    WebPortal->>WebPortal: Find/Create Patient
    WebPortal->>WebPortal: Create ECG Record
    WebPortal->>WebPortal: Log to Audit Ledger<br/>(hash-chained)
    WebPortal-->>EDGE: Success (record_id)
    
    Note over PhysioNet,Doctor: Doctor Access
    Doctor->>WebPortal: Login (username/password)
    WebPortal->>WebPortal: Verify Credentials
    WebPortal->>WebPortal: Generate JWT Token
    WebPortal->>WebPortal: Log LOGIN_SUCCESS to Audit
    WebPortal-->>Doctor: JWT Token
    
    Doctor->>WebPortal: GET /dashboard (with JWT)
    WebPortal->>WebPortal: Query Recent ECG Records
    WebPortal-->>Doctor: Dashboard (20 recent records)
    
    Doctor->>WebPortal: GET /ecg/{record_id}
    WebPortal->>WebPortal: Query ECG Record + Patient
    WebPortal-->>Doctor: ECG Visualization Page<br/>(Chart.js waveform + results)
    
    Doctor->>WebPortal: GET /audit-ledger
    WebPortal->>WebPortal: Verify Hash Chain Integrity
    WebPortal->>WebPortal: Query Audit Entries
    WebPortal-->>Doctor: Audit Ledger View<br/>(with integrity status)
```

### Communication Protocols Summary

| Source | Destination | Protocol | Port | Topic/Endpoint | Format | Purpose |
|--------|-------------|----------|------|----------------|--------|---------|
| DataSimulator | ESP32 | MQTT | 1883 | `ecg/full_record` | Binary (header + float32[]) | Full ECG record transmission |
| DataSimulator | ESP32 | UDP | 1884 | Broadcast | JSON | Broker discovery |
| ESP32 | EDGE | MQTT | 1885 | `ecg/edge/chunk` | Binary (header + CSV) | Chunked ECG data |
| ESP32 | EDGE | UDP | 1886 | Broadcast | JSON | Broker discovery |
| EDGE | ESP32 | MQTT | 1885 | `ecg/edge/ack` | JSON | Chunk acknowledgment |
| EDGE | Web Portal | HTTP | 8000 | `POST /api/ingest` | JSON | ECG data + results ingestion |
| Doctor | Web Portal | HTTPS | 8000 | Various REST endpoints | JSON/HTML | Web interface access |

### Technology Stack Summary

| Layer | Platform | Language | Framework | Database | Key Libraries |
|-------|----------|----------|-----------|----------|---------------|
| **DataSimulator** | macOS/Linux/Windows | Python 3.8+ | PyQt6 | File-based (PhysioNet dataset) | scipy, numpy, paho-mqtt |
| **IOT** | ESP32 | C++ | Arduino/PlatformIO | N/A (RAM-based) | PubSubClient, ArduinoJson, WiFi |
| **EDGE** | Raspberry Pi 4 | Python 3.8+ | PyQt6 | File-based (NPZ + JSON) | numpy, scipy, paho-mqtt, scikit-learn |
| **Web Portal** | Linux/Cloud | Python 3.8+ | FastAPI | SQLite | SQLAlchemy, Jinja2, Pydantic, JWT |

### System Characteristics

**Scalability**:
- Multiple ESP32 devices can connect to single EDGE instance
- Multiple EDGE instances can send data to single Web Portal
- Web Portal can be scaled horizontally with load balancer

**Reliability**:
- Automatic reconnection at all layers (WiFi, MQTT)
- Comprehensive error handling and logging
- Data persistence at EDGE and Web Portal layers
- Cryptographic audit trail for data integrity

**Security**:
- JWT authentication for Web Portal
- Bcrypt password hashing
- Hash-chained audit ledger (immutable)
- HTTPS ready for production deployment
- Network segmentation (separate MQTT brokers)

**Performance**:
- Real-time ECG transmission (300 Hz sampling rate)
- Efficient binary protocols (minimal overhead)
- Parallel processing at EDGE layer
- Async request handling at Web Portal
- AJAX polling for live dashboard updates

**Extensibility**:
- Modular processing pipeline (EDGE)
- Pluggable processors (easy to add new analyses)
- RESTful API (Web Portal)
- Configurable via config files
- Open architecture for integration

---

## Deployment Architecture

```mermaid
graph TB
    subgraph "Development Environment"
        DevMachine[Developer Laptop<br/>macOS/Linux/Windows]
        DataSimDev[DataSimulator<br/>Development & Testing]
    end
    
    subgraph "IoT Device Network"
        Patient1[Patient 1<br/>ESP32 Wearable]
        Patient2[Patient 2<br/>ESP32 Wearable]
        PatientN[Patient N<br/>ESP32 Wearable]
    end
    
    subgraph "Edge Gateway (On-Premises)"
        RaspberryPi[Raspberry Pi 4<br/>EDGE Application<br/>Local Network]
        LocalStorage[(Local Storage<br/>Backup & Cache)]
    end
    
    subgraph "Cloud Infrastructure (Optional)"
        LoadBalancer[Load Balancer<br/>HTTPS Termination]
        
        subgraph "Application Tier"
            WebServer1[Web Portal Instance 1<br/>FastAPI]
            WebServer2[Web Portal Instance 2<br/>FastAPI]
        end
        
        subgraph "Data Tier"
            PrimaryDB[(Primary Database<br/>PostgreSQL/MySQL)]
            ReplicaDB[(Replica Database<br/>Read-Only)]
        end
        
        subgraph "Storage Tier"
            ObjectStorage[(Object Storage<br/>S3/MinIO<br/>ECG Archives)]
        end
    end
    
    subgraph "User Access"
        DoctorBrowser[Doctor<br/>Web Browser<br/>Hospital/Remote]
    end
    
    DevMachine --> DataSimDev
    DataSimDev -.->|WiFi/Network| Patient1
    DataSimDev -.->|WiFi/Network| Patient2
    DataSimDev -.->|WiFi/Network| PatientN
    
    Patient1 -->|WiFi| RaspberryPi
    Patient2 -->|WiFi| RaspberryPi
    PatientN -->|WiFi| RaspberryPi
    
    RaspberryPi --> LocalStorage
    RaspberryPi -->|HTTPS/VPN| LoadBalancer
    
    LoadBalancer --> WebServer1
    LoadBalancer --> WebServer2
    
    WebServer1 --> PrimaryDB
    WebServer2 --> PrimaryDB
    WebServer1 --> ReplicaDB
    WebServer2 --> ReplicaDB
    
    WebServer1 --> ObjectStorage
    WebServer2 --> ObjectStorage
    
    DoctorBrowser -->|HTTPS| LoadBalancer
    
    style DevMachine fill:#f0f0f0
    style RaspberryPi fill:#e1ffe1
    style Patient1 fill:#ffe1e1
    style Patient2 fill:#ffe1e1
    style PatientN fill:#ffe1e1
    style LoadBalancer fill:#e1f5ff
    style PrimaryDB fill:#fff4e1
    style DoctorBrowser fill:#f0f0f0
```

### Deployment Scenarios

**Scenario 1: Development/Testing**
- DataSimulator on developer laptop
- ESP32 on local WiFi network
- EDGE on Raspberry Pi (local network)
- Web Portal on localhost or local server

**Scenario 2: Clinical Pilot**
- DataSimulator replaced by real ECG sensors (future)
- ESP32 devices on patients (wearable)
- EDGE on Raspberry Pi (hospital network)
- Web Portal on hospital server (internal network)

**Scenario 3: Production Deployment**
- Real ECG sensors integrated with ESP32
- Multiple EDGE gateways (per ward/clinic)
- Web Portal on cloud infrastructure (AWS/Azure/GCP)
- Load balancing and database replication
- Object storage for long-term ECG archives
- HTTPS with SSL/TLS certificates
- VPN for EDGE-to-Cloud communication

---

## Summary

This architecture documentation provides comprehensive views of:

1. **DataSimulator**: Simulation layer for ECG data generation and MQTT publishing
2. **IOT (ESP32)**: Embedded device layer for data reception and forwarding
3. **EDGE**: Intelligent processing gateway with ML inference and data aggregation
4. **Web Portal**: Secure web application for clinical access and audit trails
5. **System Integration**: End-to-end data flow and component interactions

The system demonstrates a modern IoT architecture with:
- **Edge Intelligence**: Processing at the edge reduces latency and bandwidth
- **Security**: Multi-layer security with authentication, encryption, and audit trails
- **Scalability**: Modular design supports horizontal scaling
- **Reliability**: Automatic reconnection and error handling at all layers
- **Extensibility**: Plugin architecture for new processing modules and features

This architecture is suitable for research, clinical pilots, and production deployment in remote patient monitoring scenarios.
