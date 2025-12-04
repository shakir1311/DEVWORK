# EDGE Layer - ECG Data Processor

The EDGE layer receives ECG data from ESP32 devices via MQTT and processes it using modular processing pipelines.

## Architecture

- **Modular Design**: Each component has a specific purpose and can be extended
- **MQTT Communication**: Receives chunked ECG data from ESP32
- **Broker Discovery**: Automatic UDP-based broker discovery
- **Processing Pipeline**: Pluggable ECG processing modules
- **Data Storage**: Saves received ECG data and processing results
- **GUI Visualization**: Real-time ECG waveform display with PyQt6

## Components

### Core Modules

- `config.py` - Centralized configuration
- `mqtt_broker.py` - **Embedded MQTT broker for Pi4** (receives from ESP32)
- `mqtt_discovery.py` - MQTT broker discovery (UDP-based)
- `mqtt_client.py` - MQTT client wrapper
- `chunk_receiver.py` - Receives and assembles ECG chunks
- `ecg_processor.py` - Base class for processing modules
- `data_storage.py` - Saves ECG data to disk
- `edge_gui.py` - **GUI for ECG visualization** (PyQt6)
- `main.py` - Main entry point

### Processing Modules

- `processors/heart_rate_processor.py` - Heart rate calculation

## Installation

The EDGE layer is fully self-contained with automatic setup. Simply run:

```bash
./run.sh
```

The script will:
- Check Python installation (requires 3.8+)
- Create virtual environment if needed
- Install all dependencies automatically
- Start the application

### Manual Setup (Optional)

If you prefer manual setup:

```bash
./setup.sh
```

Or manually:
```bash
python3 -m venv venv
source venv/bin/activate
pip install -r requirements.txt
```

## Usage

### Quick Start (Recommended)

Simply run the self-contained script:

```bash
./run.sh
```

This will automatically:
- Check and install all requirements
- Start the embedded MQTT broker
- Launch the GUI (if PyQt6 is available)
- Begin receiving ECG data from ESP32

The GUI will display:
- Real-time ECG waveform as data is received
- Connection status
- ECG data statistics
- Processing results
- Event log

**Note**: The EDGE broker runs on port **1885** (different from simulator's 1883) to avoid conflicts when both brokers run simultaneously.

### Advanced Usage

You can pass arguments to the script:

```bash
# Disable GUI (console mode)
./run.sh --no-gui

# Use external broker
./run.sh --no-broker --broker-ip 192.168.1.100

# Debug mode
./run.sh --log-level DEBUG

# See all options
./run.sh --help
```

### GUI Features

The GUI provides:
- **ECG Waveform Display**: Real-time visualization of received ECG data
- **Auto-refresh**: Updates automatically when new data is received from ESP32
- **Pan/Zoom**: Interactive plot controls for detailed analysis
- **Connection Status**: Visual indicator of MQTT connection state
- **Data Statistics**: Sample count, duration, min/max values
- **Processing Results**: Display results from processing modules
- **Event Log**: Real-time log of system events

**Note**: The GUI is optional. If PyQt6 is not installed, the application will run in console mode.

### Manual Run (After Setup)

If you've already run setup, you can run directly:

```bash
source venv/bin/activate
python main.py
```

This will:
1. Start an embedded MQTT broker on port **1885** (listening on all interfaces) - different from simulator (1883)
2. Start UDP discovery responder on port **1886** - different from simulator (1884)
3. Connect the MQTT client to the embedded broker
4. Begin receiving ECG chunks from ESP32 on topic `ecg/edge/chunk`

### Use External Broker

If you want to use an external MQTT broker (e.g., mosquitto):

```bash
python main.py --no-broker --broker-ip 192.168.1.100 --broker-port 1885
```

### Auto-discover External Broker

```bash
python main.py --no-broker
```

### Debug Mode

```bash
python main.py --log-level DEBUG
```

## Configuration

Edit `config.py` to customize:

- MQTT topics
- Processing modules
- Data storage settings
- Logging level

## Adding New Processors

1. Create a new processor class in `processors/`:

```python
from ecg_processor import ECGProcessor

class MyProcessor(ECGProcessor):
    def __init__(self):
        super().__init__("my_processor")
    
    def process(self, ecg_data, metadata):
        # Your processing logic
        return {'result': 'value'}
```

2. Add to `processors/__init__.py`
3. Add to `PROCESSING_MODULES` in `config.py`
4. Import and add in `main.py`

## MQTT Protocol

### Topics

- `ecg/edge/chunk` - ECG chunks from ESP32 (subscribe)
- `ecg/edge/ack` - Acknowledgments to ESP32 (publish)

**Note**: These topics are different from simulator topics (`ecg/chunk`, `ecg/ack`) to avoid conflicts when both brokers run simultaneously.

### Chunk Format

Same as Simulator→ESP32:
- Binary header (12 bytes): format_version, sampling_rate, chunk_num, total_chunks, sample_count
- Body: Comma-separated float values

## Data Storage

Received ECG data is saved to `./data/received_ecg/`:
- `ecg_TIMESTAMP.npz` - Compressed numpy array with ECG data and metadata
- `ecg_TIMESTAMP_metadata.json` - Human-readable metadata and processing results

## Broker Discovery

The EDGE layer can discover MQTT brokers via UDP broadcast:
- Listens on port **1886** (different from simulator's 1884)
- Responds to discovery requests with broker IP and port

To run as a broker responder:
```bash
python main.py --broker-ip YOUR_PI_IP
```

**Note**: The EDGE broker uses port 1885 for MQTT and port 1886 for discovery, which are different from the simulator broker (1883/1884) to allow both to run simultaneously.

## Testing

1. Start MQTT broker (e.g., mosquitto or DataSimulator's embedded broker)
2. Run EDGE layer: `python main.py`
3. ESP32 will send ECG chunks automatically

## Troubleshooting

- **No broker found**: Ensure broker is running and broadcasting discovery packets
- **Connection failed**: Check broker IP and port
- **No chunks received**: Verify ESP32 is connected and sending to correct topic
- **Processing errors**: Check log level (use DEBUG for more details)

