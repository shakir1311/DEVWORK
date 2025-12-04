# ECG Simulator - CinC 2017 Dataset

A PyQt6-based ECG dataset simulator that downloads data from the PhysioNet CinC 2017 Challenge and publishes each ECG strip as a compact binary payload over MQTT for accurate embedded replay.

## Features

- **🚀 Fully Self-Contained**: Embedded MQTT broker included - no external setup needed!
- **📦 Lossless Full-Record Export**: Entire ECG strips are packed (header + float array) into a single MQTT payload for jitter-free playback on embedded devices
- **📊 GUI Visualization**: Live preview plots of the selected ECG before publishing
- **Automatic Dataset Download**: Downloads PhysioNet CinC 2017 Challenge dataset automatically on first run
- **Fixed 300 Hz Sampling**: Always uses the dataset's original cadence and actual millivolt units derived from `.hea` metadata
- **Patient Selection**: Browse and select from 8,528 single-lead ECG recordings
- **Rhythm Filtering**: Filter patients by Normal, Atrial Fibrillation, Other Rhythm, or Noisy classifications
- **Temporal Accuracy**: Start/end timestamps come directly from `.hea` headers, so consumers can regenerate precise per-sample timing
- **AliveCor Device Data**: Based on recordings from AliveCor hand-held ECG device
- **Embedded MQTT Broker Control**: Start/stop/configure the broker directly inside the GUI
- **Thread-safe**: Non-blocking GUI with background worker threads
- **Error-resistant**: Comprehensive error handling and user-friendly messages

## Installation

### Prerequisites

- **Python 3.8 or higher** (ONLY system requirement!)
- **No MQTT broker setup needed** - embedded broker included! 🎉

### Quick Start (Recommended)

The application is **fully self-contained** with its own isolated Python environment!

```bash
cd DataSimulator
./run.sh
```

That's it! The `run.sh` script will:
- ✅ Check Python installation
- ✅ Create local virtual environment (if needed)
- ✅ Install all dependencies locally (if needed)
- ✅ Start embedded MQTT broker automatically
- ✅ Launch the application

**First run**: ~5 minutes (venv setup + dataset download)  
**Subsequent runs**: ~2 seconds (instant!)

**No external MQTT broker needed** - everything is self-contained!

### Manual Installation (Alternative)

If you prefer manual control:

```bash
cd DataSimulator
python3 -m venv venv
source venv/bin/activate  # macOS/Linux
# or: venv\Scripts\activate  # Windows
pip install -r requirements.txt
python main.py
```

### Install MQTT Broker (Optional)

The application can connect to any MQTT broker (local or remote).

**No external MQTT broker installation needed!** The application includes an embedded pure-Python MQTT broker that starts automatically.

**Just run the app and:**
1. The embedded broker starts on localhost:1883
2. Connect to "localhost" in the GUI
3. Start streaming immediately!

**Optional:** You can still use an external broker if preferred by entering a different address in the GUI.

## Usage

### Start the Application

```bash
python main.py
```

### Workflow

1. **Connect to MQTT Broker**
   - Enter broker address (default: localhost)
   - Enter port (default: 1883)
   - Click "Connect"

2. **Filter and Select Patient**
   - **Filter by Rhythm**: Use the dropdown to filter by classification:
     - ✅ **Normal (N)**: Normal sinus rhythm
     - ⚠️ **Atrial Fibrillation (A)**: AF detected
     - 🔶 **Other Rhythm (O)**: Other arrhythmias
     - ⚡ **Noisy (~)**: Too noisy to classify
   - **Select Patient**: Choose from filtered patients (rhythm indicator shown)
   - **View Details**: Patient info displays rhythm classification, duration, and sample count
   - **On-demand Download**: Patient files are auto-downloaded when selected if not already cached

3. **Sampling Rate**
   - The simulator now always uses the dataset’s native **300 Hz** cadence
   - `.hea` metadata supplies the exact start/end timestamps so consumers can recreate millisecond timing

4. **Publish Full Record**
   - Click "Start" to package the selected ECG into a single MQTT payload
   - The GUI shows payload size, duration, and voltage range before publishing
   - Once the message is delivered, the worker stops automatically (no continuous looping)

5. **Monitor Progress**
   - View status logs for connect/publish events and payload metrics
   - Click **"📊 Show ECG Plot"** to preview the waveform (first ~1 000 samples) inside the GUI
   - Event log highlights broker status, MQTT acknowledgements, and any errors

## Architecture

The application follows a Model-View-Controller (MVC) pattern with threading:

- **`ecg_simulator.py`**: Model layer - Dataset management and MQTT connectivity
- **`simulator_worker.py`**: Worker thread - Background ECG streaming
- **`app_controller.py`**: Controller - Orchestration and state management
- **`ecg_gui.py`**: View - PyQt6 GUI components
- **`config.py`**: Configuration constants
- **`main.py`**: Application entry point

## Dataset

The application uses the **PhysioNet Computing in Cardiology (CinC) Challenge 2017** dataset:

- **Source**: [PhysioNet/CinC Challenge 2017](https://archive.physionet.org/pn3/challenge/2017/)
- **Size**: ~167 MB (8,528 recordings organized in subdirectories)
- **Duration**: 9-60 seconds per recording
- **Original Sampling Rate**: 300 Hz (AliveCor hand-held device)
- **Simulator Sampling Rate**: 300 Hz (exactly matches dataset; no downsampling)
- **Sample Interval**: 3.33 ms between samples (regenerated on device from full-record payload)
- **Device**: AliveCor hand-held ECG with automatic mobile upload
- **Preprocessing**: Band-pass filtered by AliveCor device + anti-aliasing low-pass filter (50 Hz cutoff)

### Rhythm Classifications

Each ECG recording is classified into one of four categories (automatically loaded from `REFERENCE.csv`):

| Code | Classification | Description | Indicator |
|------|----------------|-------------|-----------|
| **N** | Normal | Normal sinus rhythm | ✅ |
| **A** | Atrial Fibrillation | AF detected | ⚠️ |
| **O** | Other Rhythm | Other arrhythmias (e.g., flutter, tachycardia) | 🔶 |
| **~** | Noisy | Too noisy to classify reliably | ⚡ |

**GUI Integration:**
- Filter patients by rhythm type using the dropdown
- Rhythm indicators appear next to patient IDs in the selection list
- Patient info panel shows rhythm classification with visual indicator

---

### Full-Record MQTT Payload (Binary)

To eliminate timing jitter and packet loss, the simulator now publishes each ECG strip **as a single binary MQTT message**. The ESP32 (or any consumer) buffers the payload and replays it at 300 Hz using the original start/end timestamps from the `.hea` file.

#### Topic
- `ecg/full_record` (QoS 0 - fire-and-forget, no acknowledgments)

#### Payload Layout (little-endian)

| Offset | Type    | Field                   | Description |
|--------|---------|-------------------------|-------------|
| 0      | uint16  | `format_version`        | Currently `1` |
| 2      | uint16  | `sampling_rate_hz`      | Always `300` (from `.hea`) |
| 4      | uint32  | `sample_count`          | Number of float samples |
| 8      | uint64  | `start_timestamp_ms`    | Recording start (from `.hea` date/time, ms since epoch, `0` if unknown) |
| 16     | uint64  | `end_timestamp_ms`      | `start + duration` (derived from `.hea` sample count) |
| 24     | float32 | `samples[sample_count]` | Little-endian IEEE-754 floats representing ECG voltage in **mV** |

Typical 30 s strip (9 000 samples) ≈ **36 KB**. Even 60 s strips (< 72 KB) fit comfortably within the ESP32’s RAM.

#### Benefits
- **Lossless delivery**: No per-sample packet loss or ordering issues.
- **Exact timing**: Consumers regenerate per-sample timestamps from the provided start/end metadata.
- **Minimal overhead**: Only floats are transmitted—no JSON strings or per-sample MQTT envelopes.
- **ESP32-friendly**: Buffer once, then replay via hardware timers/loop for perfect 300 Hz output.

---

### On-Demand Download Strategy

The application uses an intelligent download strategy to minimize startup time:

1. **Initial Download**: Only downloads `REFERENCE.csv` (~108 KB) containing metadata for all 8,528 patients
2. **On-Demand Download**: Individual `.mat` files are downloaded automatically when you select a patient
3. **Bulk Download Option**: Optionally download all patient files at once via the GUI
   - **Highly Parallel**: Uses up to 100 concurrent downloads (IO-bound) for maximum throughput
   - **Smart Caching**: Skips already downloaded files automatically
   - **Estimated Time**: Full dataset (~8,528 files) downloads in minutes on fast broadband

**Dataset Structure**:
```
data/cinc2017/
├── REFERENCE.csv          # Patient metadata (rhythm classification)
└── training/
    ├── A00/               # Subdirectory for A00xxx patients
    │   ├── A00001.mat
    │   ├── A00002.mat
    │   └── ...
    ├── A01/               # Subdirectory for A01xxx patients
    │   ├── A01001.mat
    │   └── ...
    └── ...                # More subdirectories (A02-A08)
```

The dataset will be downloaded to `./data/cinc2017/` on first run or via the GUI.

## MQTT Message Format

See [Full-Record MQTT Payload](#full-record-mqtt-payload-binary) for the latest protocol.

## Troubleshooting

### Dataset Download Fails

If automatic download fails:

1. **Download REFERENCE.csv**: 
   - URL: https://archive.physionet.org/pn3/challenge/2017/training/REFERENCE.csv
   - Save to: `./data/cinc2017/REFERENCE.csv`

2. **Individual Patient Files** (downloaded on-demand):
   - Base URL: https://archive.physionet.org/pn3/challenge/2017/training/
   - Example: https://archive.physionet.org/pn3/challenge/2017/training/A00/A00001.mat
   - Save to: `./data/cinc2017/training/A00/A00001.mat` (maintain subdirectory structure)

**Tip**: Use the "Bulk Download All Patient Files" button in the GUI to download all files at once.

### MQTT Connection Fails

- Ensure MQTT broker is running: `mosquitto -v`
- Check firewall settings
- Verify broker address and port

### GUI Freezes

The application uses background threads to prevent GUI freezing. If this occurs, please report as a bug.

## License

This software is provided as-is for research and educational purposes.

The PhysioNet CinC 2017 dataset is available under the Open Data Commons Open Database License v1.0.

## Citation

If you use this software or the dataset, please cite:

```
Clifford, G., Liu, C., Moody, B., Lehman, L., Silva, I., Li, Q., Johnson, A., & Mark, R. (2017).
AF Classification from a Short Single Lead ECG Recording: the PhysioNet/Computing in Cardiology Challenge 2017.
Computing in Cardiology, 44, 1-4.
```

## Support

For issues or questions, please check the event log in the application for detailed error messages.

