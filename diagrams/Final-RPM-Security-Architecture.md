# Comprehensive Remote Patient Monitoring (RPM) Security Architecture
## Zero Trust Framework, ESP32 Native Implementation & Threat Mitigation

This document consolidates the end-to-end architecture for a secure Remote Patient Monitoring system. It integrates **Zero Trust** principles with **ESP32-S3/C3 native security peripherals** and includes a detailed **Threat Model and Mitigation** analysis for each domain.

---

## 1. SYSTEM OVERVIEW: ZERO TRUST PRINCIPLES

| **Principle** | **Implementation in RPM** |
|---|---|
| **Never Trust, Always Verify** | Every connection (Edge ↔ Cloud) requires mutual authentication (mTLS). No implicit trust based on network location. |
| **Least Privilege** | ESP32 devices publish only to specific topics. Clinicians access data via Attribute-Based Access Control (ABAC). |
| **Assume Breach** | Network segmentation at the edge; Encryption of data at rest (Flash/NVS) and in transit (TLS 1.3). |
| **Hardware Root of Trust** | **Edge:** ESP32 Digital Signature Peripheral (Internal HSM) & eFuse. <br> **Cloud:** Cloud HSM (AWS CloudHSM / Thales Luna). |

---

## 2. LOCAL WORKFLOW: EDGE DEVICE (ESP32-S3/C3)
This domain covers the patient-side device, from data acquisition to secure transmission.

### 2.1 Hardware Security Architecture (The "Internal HSM")
The system leverages the **ESP32-S3/C3 Digital Signature (DS) Peripheral** to protect device identity without an external security chip.

**A. Digital Signature (DS) Peripheral**
*   **Function:** Acts as a hardware accelerator for RSA/ECDSA signatures used in mTLS.
*   **Key Protection:** The Device Private Key is **never exposed to software**.
    *   The Private Key is encrypted using a hardware-derived key (HMAC) and stored in flash.
    *   During operation, the DS Peripheral decrypts the key internally to sign TLS packets.

**B. Device Hardening (Native Controls)**
*   **Secure Boot V2:**
    *   Verifies digital signature (RSA-3072) of the bootloader and firmware on every boot.
    *   Public Key hash burned into **eFuse BLOCK_KEY0** (Write-Protected).
*   **Flash Encryption:**
    *   **AES-256-XTS** encryption for all code and static data in SPI flash.
    *   Key stored in **eFuse BLOCK_KEY1** (Read/Write Protected).
    *   Mode: **Release Mode** (JTAG and UART Bootloader disabled).
*   **NVS Encryption:**
    *   Protects the Non-Volatile Storage (Database) partition using **AES-256-XTS**.
    *   Keys derived at runtime from the **HMAC Peripheral** (hardware secret).

### 2.2 Data Acquisition & Edge Processing
**A. Wearable Communication**
*   **Protocol:** Bluetooth Low Energy (BLE) 5.0.
*   **Security:** BLE Secure Connections with Out-of-Band (OOB) pairing.
*   **Data:** Single-lead ECG, PPG (HR/SpO2), Accelerometer.

**B. Edge Logic (On ESP32)**
1.  **Ingestion:** Receive raw BLE payload.
2.  **Quality Check:** Signal quality scoring (SQI).
3.  **Inference:** Quantized TensorFlow Lite model (AF detection) runs on ESP32.
4.  **Local Logging:** Events written to encrypted NVS partition.
5.  **Payload Formatting:** JSON payload created (Vitals + Risk Score + Device Status).

### 2.3 Secure Transmission: mTLS over MQTT
**Protocol:** MQTT v5.0 over TLS 1.3.

**Authentication Process (Handshake):**
1.  **Server Auth:** ESP32 verifies Cloud Server Certificate against **Embedded Root CA Bundle** (stored in encrypted flash).
2.  **Client Auth:** Cloud verifies ESP32 Device Certificate. ESP32 uses **DS Peripheral** to sign the handshake with the protected Private Key.
3.  **Channel:** Encrypted tunnel established (AES-256-GCM).

### 2.4 Threats & Mitigations (Edge Domain)
| **Threat** | **Mitigation(s)** |
|---|---|
| **Physical Tampering / Device Theft** | **Flash Encryption** makes firmware unreadable. **NVS Encryption** protects stored credentials/logs. The **DS Peripheral** prevents private key extraction. **JTAG Disable eFuse** blocks hardware debugging. |
| **Malicious Firmware Flashing** | **Secure Boot V2** ensures only authentic, signed firmware can execute. **Release Mode** permanently disables flashing via UART. |
| **Eavesdropping on BLE Comms** | **BLE Secure Connections** with **ECDH** key exchange and **AES-128** encryption prevent sniffing of wearable data. |
| **Side-Channel Attacks on ESP32** | Burning `DISABLE_DL_CACHE` eFuse mitigates some cache-timing attacks. Private key operations are handled in the isolated **DS Peripheral**, not by the main CPU. |
| **Private Key Theft (Software Vulnerability)** | The private key is encrypted in flash and only handled in plaintext inside the isolated **DS Peripheral**, making it inaccessible to compromised application software. |

---

## 3. CLOUD WORKFLOW: BACKEND INFRASTRUCTURE
This domain covers the backend services that ingest, process, and serve patient data.

### 3.1 Secure Ingestion (Zero Trust Gateway)
**MQTT Broker (e.g., AWS IoT Core / VerneMQ):**
*   **mTLS Enforcement:** Connection refused if client certificate is missing or invalid.
*   **Certificate Pinning & Revocation:** Checks CRL/OCSP status before allowing connection.
*   **Topic Policies:** Device constrained to `devices/{client_id}/#`.

### 3.2 Cloud Hardware Security Module (Cloud HSM)
**Role:** The Cloud Root of Trust (FIPS 140-2 Level 3).

**Functions:**
*   **Master Key Storage:** Protects database encryption keys (KEK).
*   **CA Management:** Stores the Private Key of the Enterprise CA used to sign ESP32 device certificates.
*   **Audit Signing:** Cryptographically signs all central audit logs for non-repudiation.

### 3.3 Data Processing & Storage
**A. Real-Time Processing**
*   **Validation:** Verify timestamp and data schema.
*   **AI Inference:** Advanced arrhythmia detection models (Digital Twin analysis) run on cloud GPU instances.
*   **XAI Generation:** SHAP-based explanation generated for high-risk alerts.

**B. Storage (Encryption at Rest)**
*   **Time-Series DB (InfluxDB):** Stores raw vitals. Encrypted with keys managed by Cloud HSM.
*   **Relational DB (PostgreSQL):** Stores patient metadata. Column-level encryption for PII.
*   **Data Minimization:** PII is separated from health data (pseudonymization).

### 3.4 Clinical Integration (FHIR API)
**Interface:** REST API complying with HL7 FHIR standards.
**Security Controls:**
*   **Auth:** OAuth 2.0 with OIDC (OpenID Connect).
*   **MFA:** Mandatory Multi-Factor Authentication for clinicians.
*   **Access Control:** Attribute-Based Access Control (ABAC) based on role, shift, and patient consent.
*   **Continuous Access Evaluation (CAE):** Session tokens re-validated every 15 minutes against threat intel.

### 3.5 Threats & Mitigations (Cloud & API Domain)
| **Threat** | **Mitigation(s)** |
|---|---|
| **Man-in-the-Middle (MITM) Attack** | **Mutual TLS (mTLS)** ensures the device only connects to the authentic cloud server (by validating the server's cert against an embedded Root CA) and the cloud only accepts data from an authentic device. |
| **Unauthorized Data Access (Insider)** | **Cloud HSM** manages database encryption keys, preventing even DBAs from reading plaintext. **Data Minimization** and **ABAC** on the FHIR API enforce strict "need-to-know" access. |
| **Denial of Service (DoS/DDoS)** | Cloud provider's native DDoS protection (e.g., AWS Shield). **MQTT Broker throttling** and connection limits per IP. **Micro-segmentation** contains the blast radius of a compromised device fleet. |
| **Data Exfiltration from Database** | Encryption at rest using **Cloud HSM**. Database access is restricted to specific IAM roles/services. **SIEM monitoring** for anomalous data access patterns. |
| **Credential Theft / Account Takeover** | **Mandatory MFA** for all clinician accounts. **CAE** revokes suspicious sessions automatically. Strong password policies enforced. |
| **Privilege Escalation** | **ABAC** policies provide granular permissions beyond simple roles. API Gateway enforces strict OAuth 2.0 scopes, preventing services from calling unauthorized functions. |
| **Unauthorized Patient Data Access** | **ABAC** policies factor in patient consent status, clinician role, and treatment relationship. All access is logged in a tamper-evident **Audit Trail** signed by the Cloud HSM. |

---

## 4. PROVISIONING & LIFECYCLE MANAGEMENT

### 4.1 Factory Provisioning (The "Pre-Loaded Keys" Step)
Before deployment, the ESP32 undergoes a secure factory process:
1.  **Key Gen:** Secure Server generates unique Device Private Key and X.509 Certificate.
2.  **Encryption:** Server requests ESP32 to generate an HMAC key (burned to eFuse). Server encrypts the Private Key using this HMAC key.
3.  **Flashing:**
    *   Encrypted Private Key + Certificate → Written to `esp_secure_cert` partition.
    *   Bootloader + App Firmware → Written to Flash.
4.  **Locking:**
    *   Burn `BLOCK_KEY0` (Secure Boot Key).
    *   Burn `BLOCK_KEY1` (Flash Encryption Key).
    *   Burn `JTAG_DISABLE` to 1.
    *   Enable **Secure Boot** and **Flash Encryption (Release Mode)**.

### 4.2 Certificate Lifecycle
*   **Rotation:** Automated job checks certificate expiry (e.g., 90 days). ESP32 generates CSR (signed by DS Peripheral), sends to Cloud, receives new Cert, stores in NVS.
*   **Revocation:** If device is lost/stolen, its Certificate Serial is added to the Cloud CRL. Broker immediately rejects future connections.

---

## 5. COMPLIANCE & SECURITY MATRIX

| Domain | Feature | Regulation / Standard |
| :--- | :--- | :--- |
| **Identity** | ESP32 DS Peripheral (Internal HSM) | NIST SP 800-207 (Zero Trust) |
| **Code Integrity** | Secure Boot V2 (RSA-3072) | FDA Cybersecurity Guidelines |
| **Confidentiality** | Flash Encryption (AES-256) | HIPAA (Data at Rest) |
| **Transmission** | TLS 1.3 + mTLS | HIPAA (Transmission Security) |
| **Audit** | Signed Logs (Cloud HSM) | FDA 21 CFR Part 11 |
| **Database** | NVS Encryption (AES-256) | GDPR (Data Protection) |

---

## 6. DATA FLOW DIAGRAM SUMMARY

1.  **Sensor** (BLE Encrypted) → **ESP32** (RAM).
2.  **ESP32 Processing:**
    *   App reads NVS (Decrypted via HMAC).
    *   App runs Inference.
    *   App formats JSON.
3.  **ESP32 Signing:**
    *   App sends hash to **DS Peripheral**.
    *   DS Peripheral decrypts Private Key (HW), signs hash, returns Signature.
4.  **Network Transmission:**
    *   ESP32 sends Signed TLS Packet → **Cloud Gateway**.
5.  **Cloud Ingestion:**
    *   Gateway verifies Cert against CRL.
    *   Data Decrypted → Sent to **Processing Pipeline**.
6.  **Storage:**
    *   Data Encrypted (Cloud HSM Key) → **Database**.
