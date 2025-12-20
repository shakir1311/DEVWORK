# BIEIF-RPM: Comprehensive Security Architecture

> **Zero Trust Framework • PKI • mTLS • Hardware Security • Defense-in-Depth**

## Overview

This document describes the complete security architecture for the BIEIF-RPM (ECG Remote Patient Monitoring) system, covering all security zones, controls, threats, and mitigations.

---

## 1. Security Framework Principles

### 1.1 Zero Trust Architecture
- **Never Trust, Always Verify** - Every request must be authenticated regardless of origin
- **Assume Breach** - Design assumes adversary presence; minimize blast radius
- **Least Privilege** - Grant minimum necessary permissions
- **Continuous Verification** - Ongoing validation of identity and authorization

### 1.2 Defense-in-Depth Layers
1. Hardware Security (HSM, Secure Boot)
2. Device Hardening (Flash Encryption, Debug Disabled)
3. Transport Security (mTLS, TLS 1.3)
4. Application Security (JWT, RBAC)
5. Data Security (SQLCipher, Argon2id)
6. Audit & Compliance (Hash-Chain Ledger)

---

## 2. Security Zones

### 2.1 Zone 0: PKI Trust Infrastructure

| Component | Description |
|-----------|-------------|
| **Root CA** | Air-gapped offline server; self-signed root certificate; issues Device CA cert |
| **Device CA** | Guarded server; issues device certificates; maintains Certificate Revocation List (CRL) |
| **Device Registry** | Enrolled devices database; maps certificates to MQTT topic ACLs; tracks revocation status |

#### Factory Provisioning Flow
1. **eFuse Burn** - Permanent secure key storage in silicon
2. **DS Peripheral Signing** - Hardware-accelerated cryptographic signing
3. **CA Issues Certificate** - Device CA issues X.509 certificate
4. **Add to Broker ACL** - Device registered in MQTT broker access control

#### Certificate Lifecycle Management
- **90-day auto-rotation** - Automatic certificate renewal
- **CSR via DS Peripheral** - Hardware-generated Certificate Signing Requests
- **CRL/OCSP checking** - Real-time revocation status verification

---

### 2.2 Zone 1: IoT Device (ESP32-S3/C3)

#### Hardware Security Module (HSM)
| Feature | Implementation |
|---------|---------------|
| **Secure Element** | ATECC608A / ESP32 Digital Signature (DS) Peripheral |
| **Private Key Storage** | ECC P-256 keys, non-exportable |
| **TLS Acceleration** | Hardware-accelerated TLS signing |
| **Key Protection** | HMAC-derived key protection |
| **Device Certificate** | X.509 certificate with unique Common Name (CN) |

#### Device Hardening
| Control | Specification |
|---------|--------------|
| **Secure Boot** | V2 with RSA-3072 signature verification |
| **Flash Encryption** | AES-256-XTS for all flash storage |
| **NVS Encryption** | Encrypted Non-Volatile Storage for credentials |
| **Debug Disabled** | JTAG/Debug ports permanently disabled via eFuse |
| **Release Mode** | Production firmware with debug symbols stripped |

#### BLE 5.0 Security
| Feature | Implementation |
|---------|---------------|
| **Secure Connections** | Bluetooth LE Secure Connections mode |
| **Pairing** | Out-of-Band (OOB) or Numeric Comparison |
| **Key Exchange** | ECDH (Elliptic Curve Diffie-Hellman) |
| **Encryption** | AES-128 CCM for all BLE traffic |

#### Zone 1 Threats & Mitigations
| Threat | Mitigation |
|--------|-----------|
| Device theft/tampering | HSM non-exportable keys |
| Key extraction attempts | Secure Boot + Flash encryption |
| Firmware manipulation | Verified boot chain |
| BLE eavesdropping | Encrypted pairing + AES-128 |

---

### 2.3 Zone 2: Edge Processor (Raspberry Pi 4)

#### MQTT Broker Security
| Configuration | Value |
|--------------|-------|
| **Broker Software** | Mosquitto or EMQX |
| **Port** | 8883 (TLS/mTLS only) |
| **Authentication** | `require_certificate=true` |
| **CA Trust Store** | Device CA certificate loaded |
| **Protocol** | mTLS + AES-256-GCM encryption |

#### Certificate Validation Process
1. TLS handshake initiation
2. Client certificate verification against CA trust store
3. CRL/OCSP revocation check
4. ACL rule matching (certificate CN → topic permissions)

#### Trusted Execution Environment (TEE)
| Component | Function |
|-----------|----------|
| **ARM TrustZone** | Hardware isolation of secure world |
| **OP-TEE** | Trusted OS for secure key operations |
| **Secure Key Storage** | Private keys never leave TEE |
| **Isolated Execution** | AI inference in protected environment |

#### ACL Rules Example
```
device-ecg-001 → publish: data/ecg/001, subscribe: cmd/ecg/001
device-ecg-002 → publish: data/ecg/002, subscribe: cmd/ecg/002
```

#### Zone 2 Threats & Mitigations
| Threat | Mitigation |
|--------|-----------|
| Unauthorized device connection | mTLS with client cert validation |
| Topic hijacking | Certificate-to-topic ACL enforcement |
| MITM attacks | End-to-end TLS + certificate pinning |

---

### 2.4 Zone 3: Cloud Backend (FastAPI + SQLite)

#### Database Security
| Component | Implementation |
|-----------|---------------|
| **Encryption** | SQLCipher (AES-256 at rest) |
| **Password Storage** | Argon2id hashing (memory-hard) |
| **Connection** | Encrypted database file with key derivation |

#### Authentication & Authorization
| Feature | Specification |
|---------|--------------|
| **Token Type** | JWT (JSON Web Token) |
| **Algorithm** | HS256 (HMAC-SHA256) |
| **Expiry** | 30 minutes |
| **Flow** | OAuth2 password flow |
| **Roles** | doctor, admin, device |
| **Authorization** | Role-Based Access Control (RBAC) |

#### Cryptographic Audit Ledger
| Feature | Implementation |
|---------|---------------|
| **Hash Algorithm** | SHA-256 |
| **Chain Structure** | Each entry contains hash of previous entry |
| **Tamper Evidence** | Breaking chain detectable via hash verification |
| **Logged Events** | All logins, data access, modifications, exports |
| **Timestamps** | ISO 8601 with timezone |

#### Zone 3 Threats & Mitigations
| Threat | Mitigation |
|--------|-----------|
| SQL injection | Parameterized queries + ORM |
| Authentication bypass | JWT validation + token expiry |
| Data breach | SQLCipher encryption + network isolation |
| Password cracking | Argon2id (memory-hard hashing) |

---

### 2.5 Zone 4: Clinical Web Portal

#### Portal Security Controls
| Control | Implementation |
|---------|---------------|
| **Transport** | HTTPS only (TLS 1.3) |
| **Session Management** | Secure cookies + timeout |
| **CSRF Protection** | Token-based protection |
| **XSS Prevention** | Content Security Policy + sanitization |
| **Action Logging** | All user actions recorded to audit trail |
| **Role-Based UI** | Interface restricted by user role |
| **Future Enhancement** | Multi-Factor Authentication (MFA) |

---

## 3. Trust Boundaries

### 3.1 IoT ↔ Edge Boundary
- **Protocol**: mTLS over MQTT
- **Port**: 8883
- **Encryption**: AES-256-GCM
- **Authentication**: Mutual TLS with device certificates
- **Validation**: Certificate chain + CRL/OCSP checking

### 3.2 Edge ↔ Cloud Boundary
- **Protocol**: HTTPS
- **TLS Version**: 1.3
- **Key Exchange**: ECDHE (Ephemeral Diffie-Hellman)
- **Perfect Forward Secrecy**: Yes (ephemeral keys)
- **Authentication**: mTLS device certificates or API keys

### 3.3 Cloud ↔ Clinical Boundary
- **Protocol**: HTTPS
- **TLS Version**: 1.3
- **Authentication**: JWT Bearer tokens
- **Session**: Secure cookies with HttpOnly + Secure flags

---

## 4. STRIDE Threat Model

| Category | Threat | Mitigation |
|----------|--------|-----------|
| **S - Spoofing** | Device/user impersonation | HSM keys + Device certs + mTLS |
| **T - Tampering** | Firmware/data modification | Secure Boot + Hash-chain audit |
| **R - Repudiation** | Denial of actions | Immutable audit ledger + timestamps |
| **I - Info Disclosure** | Data breach / eavesdropping | AES-256 + TLS 1.3 + SQLCipher |
| **D - Denial of Service** | Service disruption | Rate limiting + local fail-safe |
| **E - Elevation** | Privilege escalation | RBAC + Least privilege + TEE |

### Additional Threats
| Threat | Mitigation |
|--------|-----------|
| Key extraction | HSM/TEE hardware protection + non-exportable keys |
| Session hijacking | JWT 30-minute expiry + secure cookies |
| Past session decryption | Perfect Forward Secrecy (ephemeral keys) |
| Password cracking | Argon2id memory-hard hashing |

---

## 5. Regulatory Compliance Mapping

| Regulation | Requirement | Implementation |
|------------|-------------|---------------|
| **HIPAA** | Data protection at rest/transit | SQLCipher + TLS 1.3 + mTLS |
| **GDPR** | Data privacy and rights | Role-based access + audit logging |
| **FDA 21 CFR Part 11** | Electronic records/audit trail | Hash-chain audit ledger |
| **FDA Cybersecurity Guidance** | Medical device security | Defense-in-depth + secure boot |
| **NIST SP 800-207** | Zero Trust architecture | Never trust, always verify |

---

## 6. Technology Stack Summary

| Category | Technologies |
|----------|-------------|
| **Hardware Security** | ATECC608A, ESP32 DS Peripheral |
| **Secure Boot** | ESP32 Secure Boot V2 (RSA-3072) |
| **Storage Encryption** | Flash Encryption (AES-256-XTS), NVS Encryption |
| **Wireless Security** | BLE 5.0 Secure Connections (ECDH + AES-128) |
| **Transport Security** | mTLS, TLS 1.3, ECDHE, Perfect Forward Secrecy |
| **Certificate Management** | X.509, CRL, OCSP, 90-day auto-rotate |
| **Message Broker** | Mosquitto/EMQX (Port 8883) |
| **Trusted Execution** | ARM TrustZone, OP-TEE |
| **Database** | SQLCipher (AES-256) |
| **Authentication** | JWT (HS256, 30min), OAuth2, Argon2id |
| **Authorization** | Role-Based Access Control (RBAC) |
| **Audit** | SHA-256 Hash-Chain Ledger |

---

## 7. Data Flow Summary

```
Patient Wearable → [BLE 5.0 Encrypted] → ESP32 IoT Device
                                              ↓
                                    [mTLS + AES-256-GCM]
                                              ↓
                                    Raspberry Pi Edge + MQTT
                                              ↓
                                    [TLS 1.3 + PFS]
                                              ↓
                                    Cloud Backend (FastAPI + SQLite)
                                              ↓
                                    [HTTPS + JWT]
                                              ↓
                                    Clinician Web Portal
```

**All data encrypted:**
- In transit: mTLS, TLS 1.3
- At rest: SQLCipher (AES-256)
- In processing: TrustZone isolation

---

*Document Version: 1.0 | Date: 2024-12-12*
