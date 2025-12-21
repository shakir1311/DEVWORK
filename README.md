# BIEIF-RPM

**Blockchain-Inspired Edge Intelligence Framework for Remote Patient Monitoring**

A lightweight IoT-to-Cloud architecture for secure cardiac arrhythmia monitoring, combining edge-based deep learning classification with tamper-evident audit logging.

![Python](https://img.shields.io/badge/Python-3.11+-blue)
![PyTorch](https://img.shields.io/badge/PyTorch-2.1+-red)
![FastAPI](https://img.shields.io/badge/FastAPI-0.100+-green)
![License](https://img.shields.io/badge/License-MIT-yellow)

---

## Overview

BIEIF-RPM addresses the security-efficiency tradeoff in healthcare IoT by:

1. **Edge-Based Deep Learning** — Deploys ECG-DualNet on Raspberry Pi 4 for real-time 4-class arrhythmia classification (Normal, AF, Other, Noisy)
2. **Hash-Chained Audit Ledger** — Provides blockchain-equivalent tamper-evidence without distributed consensus overhead
3. **End-to-End IoT Architecture** — Integrates ESP32 acquisition, edge inference, and cloud storage via MQTT

### Key Results

| Metric | Value |
|--------|-------|
| Classification Accuracy | 86.34% |
| Macro F1-Score | 0.811 |
| Audit Ledger Overhead | 0.69 ms/record (+30%) |
| Throughput | 259 records/sec |

Evaluated on the [PhysioNet/CinC 2017](https://physionet.org/content/challenge-2017/) dataset (8,528 ECG recordings).

---

## Architecture

```
┌─────────────────┐     MQTT      ┌─────────────────┐     MQTT      ┌─────────────────┐
│  Data Simulator │ ──────────▶  │   ESP32 IoT     │ ──────────▶  │  Raspberry Pi   │
│  (ECG Playback) │   Port 1883  │   (Gateway)     │   Port 1885  │  (Edge AI)      │
└─────────────────┘              └─────────────────┘              └────────┬────────┘
                                                                          │ HTTPS
                                                                          ▼
                                                                 ┌─────────────────┐
                                                                 │  Cloud Portal   │
                                                                 │  (FastAPI)      │
                                                                 │  + Hash-Chain   │
                                                                 └─────────────────┘
```

---

## Project Structure

```
BIEIF-RPM/
├── DataSimulator/      # ECG data simulation (PyQt6 GUI)
├── IOT/                # ESP32 firmware (C++/ESP-IDF)
├── EDGE/               # Edge inference pipeline (Python)
└── Web/                # FastAPI cloud portal + audit ledger
```

---

## Quick Start

### Prerequisites

- Python 3.11+
- Raspberry Pi 4 (8GB recommended) for edge deployment
- ESP32-WROOM-32 DevKit (optional, for IoT layer)

### Installation

```bash
# Clone the repository
git clone https://github.com/YOUR_USERNAME/BIEIF-RPM.git
cd BIEIF-RPM

# Install dependencies for each component
pip install -r DataSimulator/requirements.txt
pip install -r EDGE/requirements.txt
pip install -r Web/requirements.txt
```

### Running the System

**1. Start the Cloud Portal (Web Server):**
```bash
cd Web
pip install uvicorn  # ASGI server for FastAPI
python -m uvicorn main:app --reload --port 8000
```
Portal available at http://localhost:8000

**2. Start the Edge Processor:**
```bash
cd EDGE
python main.py
```

**3. Start the Data Simulator:**
```bash
cd DataSimulator
python ecg_gui.py
```

**4. Flash the ESP32 (IoT Gateway):**
```bash
cd IOT
# Requires PlatformIO CLI
pio run --target upload --upload-port /dev/ttyUSB0
```
See `IOT/README.md` for detailed setup and library installation.

---

## Components

### DataSimulator
PyQt6-based GUI for replaying PhysioNet ECG recordings via MQTT. Simulates wearable sensor data acquisition.

### EDGE
Edge intelligence layer running on Raspberry Pi 4:
- Receives ECG data via MQTT
- Runs ECG-DualNet inference (PyTorch)
- Classifies into 4 rhythm classes
- Forwards results to cloud with hash-chain audit

**Pre-trained model included:** The EDGE folder contains a pre-trained ECG-DualNet model (`ecg_dualnet_xl.pt`) trained on the CinC 2017 dataset. No additional training required.

### Web
FastAPI-based cloud portal:
- REST API for ECG records
- SQLite database with hash-chained audit ledger
- Clinical dashboard (Jinja2 templates)
- JWT authentication

---

## Hash-Chain Audit Ledger

Each database operation creates an audit entry:

```python
entry_hash = SHA256(payload || previous_hash)
```

This provides:
- ✅ **Tamper Evidence** — Modification invalidates subsequent hashes
- ✅ **Append-Only** — Entries can only be added, not inserted
- ✅ **Verification** — Any party can verify chain integrity

Unlike blockchain, this construction:
- ❌ Does not require distributed consensus
- ❌ Assumes trusted single writer (edge device)

---

## Dataset

This project uses the [PhysioNet/Computing in Cardiology Challenge 2017](https://physionet.org/content/challenge-2017/) dataset:

- 8,528 single-lead ECG recordings
- 4 classes: Normal (N), AF (A), Other (O), Noisy (~)
- Duration: 9-61 seconds each
- Sampling rate: 300 Hz

Download the dataset from [PhysioNet](https://physionet.org/content/challenge-2017/).

---

## Citation

If you use this code in your research, please cite:

```bibtex
@article{bieif-rpm2024,
  title={BIEIF-RPM: Blockchain-Inspired Edge Intelligence Framework for Secure Remote Patient Monitoring},
  author={[Authors]},
  journal={[Journal]},
  year={2024}
}
```

---

## License

This project is licensed under the MIT License — see [LICENSE](LICENSE) for details.

---

## Acknowledgments

- [ECG-DualNet](https://github.com/Bsingstad/ECG-DualNet-PyTorch) by Rohr et al.
- [PhysioNet](https://physionet.org/) for the CinC 2017 dataset
- Schneier & Kelsey for hash-chain audit log construction
