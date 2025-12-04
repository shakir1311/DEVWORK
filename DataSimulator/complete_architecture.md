# ECG Simulator GUI - Complete Architecture Specification for AI Agent

## Project Overview
Build a complete PyQt6 ECG dataset simulator application that:
1. Downloads ECG data from PhysioNet CinC 2017 Challenge
2. Simulates real-time ECG streaming via MQTT
3. Provides GUI controls for patient selection, sampling rate, and inter-window delay
4. Is error-resistant and non-blocking

**Technology Stack:**
- PyQt6 (GUI framework)
- Python 3.8+
- Threading (QThread for concurrent operations)
- queue.Queue (thread-safe communication)
- MQTT (paho-mqtt for publishing)
- scipy (ECG data loading)
- numpy (signal processing)

---

# PART 1: DATA SIMULATOR (Model Layer)

## File: `ecg_simulator.py`

### Purpose
Core data model that handles dataset download, patient management, and ECG data loading.

### Class: `ECGSimulator`

#### Attributes
```python
DATASET_URL = "https://physionet.org/files/challenge-2017/1.0.0/training.zip"
REFERENCE_URL = "https://physionet.org/files/challenge-2017/1.0.0/REFERENCE.csv"

DATASET_DIR = "./data/cinc2017"
TRAINING_DIR = os.path.join(DATASET_DIR, "training2017")
REFERENCE_FILE = os.path.join(DATASET_DIR, "REFERENCE-v3.csv")

ORIGINAL_FS = 300  # Original sampling rate from CinC 2017
TARGET_FS = 100    # Default wearable sampling rate (will be variable)
WINDOW_SIZE = 100  # 1 second @ 100 Hz (will be variable)

RHYTHM_CLASSES = {
    'N': 'Normal',
    'A': 'Atrial Fibrillation',
    'O': 'Other Rhythm',
    '~': 'Noisy'
}

Instance attributes:
- mqtt_broker: str (MQTT broker address)
- mqtt_port: int (MQTT broker port)
- mqtt_client: paho.mqtt.Client (connection object)
- patient_records: dict (loaded from REFERENCE file)
```

#### Methods

##### `__init__(mqtt_broker: str = "localhost", mqtt_port: int = 1883)`
- Initialize simulator
- Call `_load_dataset()` to download if needed and load patient records
- Initialize MQTT client (but don't connect yet)

##### `_load_dataset() -> None`
- Check if REFERENCE_FILE exists
- If not: call `_download_dataset()`
- Then: call `_load_references()`

##### `_download_dataset() -> None`
- Create DATASET_DIR if not exists
- Download training.zip from DATASET_URL with progress callback
- Extract zip to DATASET_DIR/training2017/
- Download REFERENCE.csv from REFERENCE_URL
- Print download status

**Error Handling:**
- If download fails: raise exception with helpful message including manual download URL
- If extraction fails: raise exception

##### `_load_references() -> None`
- Open REFERENCE_FILE (CSV format: patient_id, rhythm_class)
- Parse CSV and populate patient_records dict
- Each record: `{patient_id: {'rhythm': 'A', 'rhythm_name': 'Atrial Fibrillation'}}`
- Print loaded patient count

**Error Handling:**
- If file not found: raise FileNotFoundError
- If CSV parsing fails: skip malformed rows, log warning

##### `list_patients() -> List[str]`
- Return sorted list of all patient IDs from patient_records
- Example: ['A00001', 'A00002', 'A00003', ...]

##### `get_patient_info(patient_id: str) -> Optional[Dict]`
- Input: patient_id (e.g., 'A00001')
- Check if patient_id in patient_records
- Check if corresponding .mat file exists in TRAINING_DIR
- Load .mat file using scipy.io.loadmat
- Extract ECG signal (key 'val', index [0])
- Calculate original duration: len(ecg_raw) / ORIGINAL_FS
- Return dict:
  ```
  {
    'patient_id': str,
    'rhythm': str (single char: N/A/O/~),
    'rhythm_name': str (e.g., 'Atrial Fibrillation'),
    'duration_seconds': float,
    'sampling_rate': int (ORIGINAL_FS = 300),
    'samples': int (total samples in recording)
  }
  ```

**Error Handling:**
- If patient not found: return None
- If .mat file not found: return None
- If scipy.io.loadmat fails: catch exception, print error, return None

##### `load_ecg(patient_id: str, target_fs: int = 100) -> Tuple[np.ndarray, Dict]`
- Input: patient_id, target_fs (desired sampling rate in Hz)
- Check if patient exists in dataset
- Load .mat file: `mat_data = scipy.io.loadmat(filepath)`
- Extract ECG: `ecg_raw = mat_data['val'][0]`
- Downsample from ORIGINAL_FS (300) to target_fs:
  - `downsample_factor = ORIGINAL_FS // target_fs`
  - `ecg_downsampled = ecg_raw[::downsample_factor]`
- Normalize to zero-mean, unit variance:
  - `ecg_normalized = (ecg_downsampled - mean) / (std + 1e-8)`
- Add realistic sensor noise (±0.5% of signal):
  - `noise = np.random.normal(0, 0.005, len(ecg_normalized))`
  - `ecg_with_noise = ecg_normalized + noise`
- Return tuple: (ecg_array, metadata_dict)
  - ecg_array: np.ndarray of shape (N,) with float32 values
  - metadata_dict:
    ```
    {
      'patient_id': str,
      'rhythm': str,
      'rhythm_name': str,
      'original_samples': int,
      'downsampled_samples': int,
      'sampling_rate': int (= target_fs),
      'duration_seconds': float
    }
    ```

**Error Handling:**
- If patient not found: raise FileNotFoundError
- If scipy.io.loadmat fails: catch, print error, re-raise with context
- If malformed data: catch, print error, re-raise

##### `connect_mqtt() -> None`
- Create paho.mqtt.Client instance
- Define on_connect callback:
  - If rc == 0: print success message
  - Else: print failure with code
- Define on_disconnect callback:
  - If unexpected: print warning
- Call client.connect(self.mqtt_broker, self.mqtt_port, 60)
- Call client.loop_start()
- Sleep 1 second to allow connection to establish

**Error Handling:**
- If connection fails: catch exception, print helpful message (check broker running/address), re-raise

##### `disconnect_mqtt() -> None`
- If mqtt_client exists:
  - Call client.loop_stop()
  - Call client.disconnect()

#### Usage Example (for reference)
```python
# Create simulator
sim = ECGSimulator(mqtt_broker="192.168.1.100", mqtt_port=1883)

# List all patients
patients = sim.list_patients()  # ['A00001', 'A00002', ...]

# Get patient info
info = sim.get_patient_info('A00001')
# {'patient_id': 'A00001', 'rhythm': 'N', 'rhythm_name': 'Normal', ...}

# Load ECG data at custom sampling rate
ecg, metadata = sim.load_ecg('A00001', target_fs=100)
# ecg shape: (duration_sec * target_fs,)
# metadata contains rhythm info and sampling details
```

---

# PART 2: WORKER THREAD (Simulation Engine)

## File: `simulator_worker.py`

### Purpose
Runs streaming simulation in background thread, respects pause/stop signals, publishes via MQTT.

### Class: `SimulatorWorker(QThread)`

Inherits: `PyQt6.QtCore.QThread`

#### Attributes
```python
Instance attributes:
- simulator: ECGSimulator (reference to Model)
- patient_id: str or None
- delay_per_window: float (seconds, 0.1-2.0)
- target_sampling_rate: int (Hz, 50-300)
- is_paused: bool (thread-safe via Lock)
- stop_requested: bool (thread-safe via Lock)
- pause_lock: threading.Lock()
```

#### Signals (PyQt6)
```python
# Define as class attributes using pyqtSignal()

sig_connected = pyqtSignal(str)
  # Emitted: MQTT connected successfully
  # Data: "Connected to 192.168.1.100:1883"

sig_disconnected = pyqtSignal()
  # Emitted: MQTT disconnected

sig_window_sent = pyqtSignal(dict)
  # Emitted: ECG window published to MQTT
  # Data: {"window_num": int, "timestamp": str, "samples": int, "status": "OK"}

sig_progress = pyqtSignal(dict)
  # Emitted: Progress update (every N windows)
  # Data: {"elapsed_time": float, "windows_sent": int, "total_windows": int, "samples_total": int}

sig_error = pyqtSignal(str)
  # Emitted: Non-fatal error occurred
  # Data: "MQTT publish failed: Connection lost"
  # Note: Worker continues running after error

sig_status = pyqtSignal(str)
  # Emitted: Status update message
  # Data: "Loading patient A00001", "Streaming started", "Patient loaded (60 sec)", etc.

sig_finished = pyqtSignal()
  # Emitted: Simulation completed or stopped gracefully
```

#### Methods

##### `__init__(simulator: ECGSimulator)`
- Store simulator reference
- Initialize patient_id = None
- Initialize delay_per_window = 1.0 (default)
- Initialize target_sampling_rate = 100 (default)
- Initialize is_paused = False
- Initialize stop_requested = False
- Create pause_lock = threading.Lock()
- NOTE: Do NOT call connect_mqtt() here (will be called later from controller)

##### `set_parameters(patient_id: str, delay: float, sampling_rate: int) -> None`
- Input validation:
  - patient_id: must be non-empty string
  - delay: 0.1 ≤ delay ≤ 2.0 (seconds)
  - sampling_rate: 50 ≤ sampling_rate ≤ 300 (Hz)
- Store parameters as instance attributes
- Emit sig_status("Parameters set: patient={}, delay={:.1f}s, fs={}Hz".format(...))

**Error Handling:**
- If invalid inputs: emit sig_error() with reason, don't update parameters

##### `pause() -> None`
- Acquire pause_lock
- Set is_paused = True
- Release pause_lock

##### `resume() -> None`
- Acquire pause_lock
- Set is_paused = False
- Release pause_lock

##### `stop() -> None`
- Acquire pause_lock
- Set stop_requested = True
- Release pause_lock
- Note: Worker thread will exit main loop on next iteration

##### `run() -> None` (Main worker loop - overrides QThread.run())

**Pseudocode:**
```
try:
    emit sig_status("Connecting to MQTT broker...")
    simulator.connect_mqtt()
    emit sig_connected(f"Connected to {simulator.mqtt_broker}:{simulator.mqtt_port}")
    
    emit sig_status(f"Loading patient {self.patient_id}...")
    ecg_data, metadata = simulator.load_ecg(
        self.patient_id, 
        target_fs=self.target_sampling_rate
    )
    emit sig_status(f"Patient loaded: {metadata['rhythm_name']}, {metadata['duration_seconds']}s")
    
    # Calculate windows
    window_size = self.target_sampling_rate  # 1 second @ target_fs
    num_windows = len(ecg_data) // window_size
    emit sig_status(f"Ready to stream: {num_windows} windows")
    
    window_count = 0
    samples_sent = 0
    start_time = time.time()
    
    for i in range(0, len(ecg_data) - window_size, window_size):
        # Check stop flag
        if self.stop_requested:
            emit sig_status("Simulation stopped by user")
            break
        
        # Check pause flag (block until resumed)
        while True:
            with pause_lock:
                if not is_paused:
                    break
            time.sleep(0.1)  # Poll pause state every 100ms
        
        # Extract window
        ecg_window = ecg_data[i:i + window_size]
        
        # Create MQTT payload (JSON)
        payload = {
            'timestamp': int(time.time() * 1000),
            'patient_id': self.patient_id,
            'window': window_count,
            'ecg': ecg_window.tolist(),
            'fs': self.target_sampling_rate,
            'units': 'mV',
            'rhythm': metadata['rhythm_name']
        }
        
        # Publish via MQTT
        try:
            msg = json.dumps(payload)
            result = simulator.mqtt_client.publish("ecg/raw", msg)
            
            window_count += 1
            samples_sent += window_size
            
            emit sig_window_sent({
                "window_num": window_count,
                "timestamp": str(datetime.now()),
                "samples": window_size,
                "status": "OK"
            })
            
            # Emit progress every 10 windows
            if window_count % 10 == 0:
                elapsed = time.time() - start_time
                emit sig_progress({
                    "elapsed_time": elapsed,
                    "windows_sent": window_count,
                    "total_windows": num_windows,
                    "samples_total": samples_sent
                })
        
        except Exception as e:
            emit sig_error(f"MQTT publish failed: {str(e)}")
            # Continue streaming despite error
        
        # Apply delay
        time.sleep(self.delay_per_window)
    
    emit sig_status(f"Streaming complete: {window_count} windows sent")
    emit sig_finished()

except Exception as e:
    emit sig_error(f"Fatal error: {str(e)}")
    emit sig_finished()

finally:
    simulator.disconnect_mqtt()
    emit sig_disconnected()
```

**Error Handling:**
- MQTT connection failure: emit sig_error(), don't crash
- Patient loading failure: emit sig_error(), emit sig_finished()
- MQTT publish failure: emit sig_error(), continue streaming next window
- Any other exception: emit sig_error(), emit sig_finished(), cleanup MQTT
- Always disconnect MQTT in finally block

---

# PART 3: CONTROLLER

## File: `app_controller.py`

### Purpose
Orchestrates Model + Worker Thread, validates inputs, manages state transitions.

### Class: `SimulationController(QObject)`

Inherits: `PyQt6.QtCore.QObject`

#### Attributes
```python
Instance attributes:
- simulator: ECGSimulator
- worker: SimulatorWorker
- worker_thread: QThread (separate from worker)
- current_patient: str or None
- state: str (enum: "idle", "loading", "running", "paused", "error", "stopped")
```

#### Signals (PyQt6)
```python
# For GUI to listen to controller state changes

sig_status_changed = pyqtSignal(str)  # "idle", "running", etc.
sig_patient_list_updated = pyqtSignal(list)  # List of patient IDs
sig_mqtt_connected = pyqtSignal(bool)  # True/False
```

#### Methods

##### `__init__(mqtt_broker: str = "localhost", mqtt_port: int = 1883)`
- Create ECGSimulator instance with given broker/port
- Create SimulatorWorker instance
- Create QThread instance (for worker)
- Initialize state = "idle"
- Initialize current_patient = None
- Connect worker signals to controller slots

##### `initialize_mqtt() -> bool`
- Try: simulator.connect_mqtt()
- On success: 
  - Emit sig_mqtt_connected(True)
  - Emit sig_status_changed("ready")
  - Return True
- On error: 
  - Emit sig_mqtt_connected(False)
  - Set state = "error"
  - Return False

##### `validate_patient(patient_id: str) -> bool`
- Check if patient_id in simulator.patient_records
- Return True/False

##### `validate_parameters(delay: float, sampling_rate: int) -> Tuple[bool, str]`
- Check delay: 0.1 ≤ delay ≤ 2.0
- Check sampling_rate: 50 ≤ sampling_rate ≤ 300
- If invalid: return (False, "delay must be between 0.1 and 2.0 seconds")
- If valid: return (True, "")

##### `start_simulation(patient_id: str, delay: float, sampling_rate: int) -> bool`
- Validate patient: if invalid, emit sig_error("Patient not found"), return False
- Validate parameters: if invalid, emit sig_error(error_msg), return False
- Set state = "loading"
- Emit sig_status_changed("loading")
- Set worker parameters: worker.set_parameters(patient_id, delay, sampling_rate)
- Move worker to thread: worker.moveToThread(worker_thread)
- Connect worker.finished() → worker_thread.quit()
- Connect worker.finished() → on_worker_finished()
- Start thread: worker_thread.start()
- Start worker: worker.run() (actually: worker_thread calls worker.run())
- Wait briefly for worker to report success
- If worker emits error within timeout: state = "error", return False
- Else: state = "running", current_patient = patient_id, return True

**Error Handling:**
- Patient validation: check simulator.patient_records
- Parameter validation: range checks
- Thread start errors: catch, emit error, return False

##### `pause_simulation() -> None`
- Call worker.pause()
- Set state = "paused"
- Emit sig_status_changed("paused")

##### `resume_simulation() -> None`
- Call worker.resume()
- Set state = "running"
- Emit sig_status_changed("running")

##### `stop_simulation() -> None`
- Call worker.stop()
- Wait for worker_thread to finish (with timeout): worker_thread.wait(5000)
- Set state = "stopped"
- Emit sig_status_changed("stopped")

##### `set_delay(delay: float) -> None`
- Validate: 0.1 ≤ delay ≤ 2.0
- If valid: worker.delay_per_window = delay
- If invalid: emit sig_error("Invalid delay")

##### `set_sampling_rate(rate: int) -> None`
- Validate: 50 ≤ rate ≤ 300
- If valid: worker.target_sampling_rate = rate
- If invalid: emit sig_error("Invalid sampling rate")

##### `get_patient_list() -> List[str]`
- Return simulator.list_patients()

##### `get_patient_info(patient_id: str) -> Optional[Dict]`
- Return simulator.get_patient_info(patient_id)

##### `on_worker_connected(msg: str) -> None` (Slot)
- Emit sig_mqtt_connected(True)
- Set state = "ready_to_stream"

##### `on_worker_error(msg: str) -> None` (Slot)
- Log error message
- If state was "loading": state = "error"
- Otherwise: state remains as is (don't interrupt running stream)

##### `on_worker_finished() -> None` (Slot)
- Worker thread finished
- Set state = "stopped"
- Emit sig_status_changed("stopped")
- Disconnect all worker signals

---

# PART 4: GUI VIEW

## File: `ecg_gui.py`

### Purpose
PyQt6 GUI for user interaction.

### Class: `ECGSimulatorApp(QMainWindow)`

#### Constructor: `__init__()`
- Set window title: "ECG Simulator - CinC 2017 Dataset"
- Set window size: 1200x800 minimum
- Create controller: `self.controller = SimulationController()`
- Create all UI panels (see below)
- Connect controller signals to GUI slots
- Connect button/slider signals to controller methods
- Initialize connection status indicator (red/disconnected)

#### UI Panels (implement as QGroupBox or QWidget)

**1. MQTT Connection Panel**
```
Widgets:
- QLineEdit: broker_input (default "localhost")
- QSpinBox: port_input (default 1883, range 1-65535)
- QPushButton: connect_btn (text "Connect")
- QLabel: status_indicator (red circle initially)
- QLabel: status_text (text "Disconnected")
- QLineEdit: topic_input (default "ecg/raw")

Layout: Horizontal

Signals/Slots:
- connect_btn.clicked() → on_connect_clicked()
```

**2. Patient Selection Panel**
```
Widgets:
- QLabel: "Select Patient:"
- QComboBox: patient_combo (populated from controller.get_patient_list())
- QPushButton: refresh_btn (text "Refresh")
- QLabel: patient_info_text (multiline text showing rhythm, duration, etc.)
- QLabel: patient_status (shows selected patient ID)

Layout: Vertical with combobox and info display

Signals/Slots:
- patient_combo.currentIndexChanged(str) → on_patient_selected(str)
- refresh_btn.clicked() → on_refresh_patients()
```

**3. Simulation Parameters Panel**
```
Widgets:
- QLabel: "Inter-Window Delay (sec):"
- QSlider: delay_slider (min 1, max 20, tick 1, representing 0.1-2.0 sec)
  - Convert: slider_value / 10.0 = delay_seconds
- QSpinBox: delay_spinbox (min 0.1, max 2.0, step 0.1, decimals 1)
- QLabel: delay_display ("0.5 sec")

- QLabel: "Sampling Rate (Hz):"
- QSlider: sampling_rate_slider (min 50, max 300, tick 10)
- QSpinBox: sampling_rate_spinbox (min 50, max 300, step 10)
- QLabel: sampling_rate_display ("100 Hz")

- QLabel: calculated_info ("X windows, Y total samples")

Layout: Vertical, two rows

Signals/Slots:
- delay_slider.valueChanged(int) → on_delay_changed(int)
  - Update spinbox, label, controller
- sampling_rate_slider.valueChanged(int) → on_sampling_rate_changed(int)
  - Update spinbox, label, controller
```

**4. Control Panel**
```
Widgets:
- QPushButton: start_btn (text "Start", disabled until patient selected + connected)
- QPushButton: pause_btn (text "Pause", disabled until running)
- QPushButton: resume_btn (text "Resume", disabled until paused)
- QPushButton: stop_btn (text "Stop", disabled until running)

Layout: Horizontal

Signals/Slots:
- start_btn.clicked() → on_start_clicked()
- pause_btn.clicked() → on_pause_clicked()
- resume_btn.clicked() → on_resume_clicked()
- stop_btn.clicked() → on_stop_clicked()
```

**5. Live Status Panel**
```
Widgets:
- QLabel: patient_label (text "Patient: None")
- QLabel: status_label (text "Idle")
- QLabel: progress_label (text "0 / 0 windows")
- QLabel: elapsed_label (text "Elapsed: 0s")
- QLabel: samples_label (text "Samples sent: 0")
- QProgressBar: progress_bar (0-100%)

Layout: Vertical

Updated by slots:
- on_worker_progress()
- on_worker_finished()
```

**6. Error Log Panel**
```
Widgets:
- QTextEdit: error_log (read-only, rich text)
- QPushButton: clear_log_btn (text "Clear")
- QCheckBox: autoscroll_check (checked by default)

Layout: Vertical with toolbar

Signals/Slots:
- clear_log_btn.clicked() → on_clear_log()
```

#### Key Slot Methods

##### `on_connect_clicked()`
- Get broker address from broker_input
- Get port from port_input
- Try: controller.initialize_mqtt()
- If success: 
  - Enable patient_combo, start_btn
  - Update status_indicator to green
  - Update status_text to "Connected"
- If error: 
  - Update status_indicator to red
  - Update status_text to error message
  - Log error

##### `on_patient_selected(patient_id: str)`
- Get patient info: controller.get_patient_info(patient_id)
- Display in patient_info_text (rhythm, duration, samples)
- Enable start_btn if connected

##### `on_delay_changed(slider_value: int)`
- Convert slider value: delay = slider_value / 10.0
- Update delay_spinbox
- Update delay_display label
- Call controller.set_delay(delay)

##### `on_sampling_rate_changed(slider_value: int)`
- Update sampling_rate_spinbox
- Update sampling_rate_display label
- Call controller.set_sampling_rate(slider_value)

##### `on_start_clicked()`
- Get patient_id from patient_combo
- Get delay from delay_spinbox
- Get sampling_rate from sampling_rate_spinbox
- Disable patient_combo, parameter sliders
- Enable pause_btn, stop_btn
- Disable start_btn
- Call controller.start_simulation(patient_id, delay, sampling_rate)

##### `on_pause_clicked()`
- Disable pause_btn
- Enable resume_btn
- Call controller.pause_simulation()

##### `on_resume_clicked()`
- Disable resume_btn
- Enable pause_btn
- Call controller.resume_simulation()

##### `on_stop_clicked()`
- Call controller.stop_simulation()
- Reset all buttons
- Enable patient_combo, parameter sliders

##### `on_worker_progress(data: dict)`
- Update progress_label: "{windows_sent} / {total_windows} windows"
- Update elapsed_label: "{elapsed_time:.1f}s"
- Update samples_label: "{samples_total} samples"
- Update progress_bar: (windows_sent / total_windows) * 100

##### `on_worker_status(msg: str)`
- Update status_label with message

##### `on_worker_error(msg: str)`
- Append to error_log with timestamp and "ERROR" prefix
- Play warning sound (optional)
- If autoscroll_check checked: scroll to bottom

##### `on_worker_finished()`
- Reset controls: enable start_btn, disable pause/resume/stop
- Update status_label: "Ready"
- Append completion message to error_log

##### `on_clear_log()`
- Clear error_log text

#### Closevent

Override `closeEvent()`:
- If worker thread running: call controller.stop_simulation()
- Wait for thread to finish
- Disconnect MQTT
- Close window

---

# PART 5: APPLICATION ENTRY POINT

## File: `main.py`

```python
import sys
from PyQt6.QtWidgets import QApplication
from ecg_gui import ECGSimulatorApp

if __name__ == "__main__":
    app = QApplication(sys.argv)
    window = ECGSimulatorApp()
    window.show()
    sys.exit(app.exec())
```

---

# PART 6: CONFIGURATION

## File: `config.py`

```python
# Default application configuration

MQTT_BROKER = "localhost"
MQTT_PORT = 1883
MQTT_TOPIC = "ecg/raw"

DELAY_MIN = 0.1
DELAY_MAX = 2.0
DELAY_DEFAULT = 1.0

SAMPLING_RATE_MIN = 50
SAMPLING_RATE_MAX = 300
SAMPLING_RATE_DEFAULT = 100

WINDOW_UPDATE_INTERVAL = 10  # Emit progress every N windows
```

---

# PART 7: DEPENDENCIES

## File: `requirements.txt`

```
PyQt6==6.6.1
PyQt6-sip==13.6.0
scipy>=1.11.0
numpy>=1.24.0
paho-mqtt>=1.6.0
```

---

# PART 8: DATA FLOW EXAMPLES

### Scenario 1: User Starts Application

1. User runs: `python main.py`
2. QApplication creates window
3. ECGSimulatorApp.__init__() called
4. SimulationController created
5. ECGSimulator created:
   - Checks if dataset exists
   - If not: downloads CinC 2017 (~167 MB)
   - Loads patient records from REFERENCE.csv
6. Patient list loaded in combo box
7. GUI shows "Disconnected" status
8. Start button disabled (awaiting connection)

### Scenario 2: User Connects MQTT

1. User enters broker address "192.168.1.100"
2. User clicks "Connect"
3. on_connect_clicked() called
4. controller.initialize_mqtt() called
5. simulator.connect_mqtt() called
6. MQTT client connects to broker
7. SimulatorWorker.sig_connected emitted
8. GUI slot: status indicator turns green, "Connected"
9. Start button enabled

### Scenario 3: User Starts Streaming

1. User selects patient "A00001"
2. User sets delay to 0.5 sec
3. User sets sampling rate to 100 Hz
4. User clicks "Start"
5. on_start_clicked() called
6. controller.start_simulation("A00001", 0.5, 100) called
7. SimulatorWorker.run() starts:
   - Loads ECG: load_ecg("A00001", 100)
   - Calculates 600 windows total
   - Enters main loop
   - For each window:
     - Creates MQTT JSON payload
     - Publishes to "ecg/raw"
     - Emits sig_window_sent
     - Sleeps 0.5 sec
8. GUI updates progress every 10 windows
9. User can adjust delay/sampling rate (takes effect next window)
10. User can pause/resume/stop at any time

### Scenario 4: Error During Streaming

1. MQTT broker disconnects while streaming
2. mqtt_client.publish() raises exception
3. Worker catches exception
4. Worker emits sig_error("MQTT publish failed: Connection lost")
5. GUI slot appends error to error_log with timestamp
6. Worker continues: tries next window
7. GUI remains responsive
8. User can stop, troubleshoot, and restart

---

# PART 9: TESTING CHECKLIST

- [ ] Dataset downloads correctly on first run
- [ ] Patient list populates from reference file
- [ ] MQTT connection succeeds/fails with appropriate messages
- [ ] Patient info displays correctly (rhythm, duration)
- [ ] Delay slider changes take effect in-flight (next window)
- [ ] Sampling rate slider changes take effect in-flight
- [ ] Pause button stops window sending
- [ ] Resume button resumes from pause
- [ ] Stop button terminates simulation gracefully
- [ ] MQTT payload contains correct JSON structure
- [ ] Error messages are user-friendly and logged with timestamp
- [ ] Application doesn't crash on MQTT failure
- [ ] Application doesn't freeze GUI during file I/O
- [ ] Close window while running: worker stops cleanly
- [ ] Multiple start/stop/start cycles work correctly

---

# PART 10: INSTRUCTIONS FOR AI AGENT

**Create the following files:**

1. **ecg_simulator.py** - Dataset model (ECGSimulator class only, no GUI)
2. **simulator_worker.py** - Worker thread (SimulatorWorker extends QThread)
3. **app_controller.py** - Controller orchestrating model+worker
4. **ecg_gui.py** - PyQt6 GUI (ECGSimulatorApp extends QMainWindow)
5. **main.py** - Application entry point
6. **config.py** - Configuration constants
7. **requirements.txt** - Dependencies

**Implementation Notes:**
- Use type hints throughout
- Follow PEP 8 style
- Use logging module for debug messages
- All blocking I/O in worker thread (never block GUI)
- All MQTT/file operations in try-except blocks
- Emit signals (not call methods) from worker to GUI
- Thread-safe parameter updates in worker (atomic assignments OK for primitives)
- Docstrings for all public methods

**Do NOT include:**
- Matplotlib/plotting code (marked "optional" in spec)
- Database storage
- Configuration file save/load (use defaults)

