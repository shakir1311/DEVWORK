# ESP32 ECG MQTT Consumer

ESP32 program that receives ECG data from the DataSimulator via MQTT.

## Features

- ✅ **Resilient Connection Handling**: Automatic WiFi and MQTT reconnection
- ✅ **Exception-Proof**: Comprehensive error handling and validation
- ✅ **JSON Parsing**: Robust parsing of ECG MQTT messages
- ✅ **Statistics Tracking**: Monitor message reception and parsing success
- ✅ **Configurable**: Easy-to-update WiFi and MQTT settings
- ✅ **Low Overhead**: Efficient memory usage and minimal processing

## Hardware Requirements

- ESP32 development board (any variant)
- USB cable for programming and power
- WiFi network access

## Software Requirements

### Arduino IDE Setup

1. **Install Arduino IDE** (1.8.x or 2.x)
   - Download from: https://www.arduino.cc/en/software

2. **Add ESP32 Board Support**
   - File → Preferences → Additional Board Manager URLs
   - Add: `https://raw.githubusercontent.com/espressif/arduino-esp32/gh-pages/package_esp32_index.json`
   - Tools → Board → Boards Manager → Search "ESP32" → Install

3. **Install Required Libraries**
   - **PubSubClient** by Nick O'Leary
     - Tools → Manage Libraries → Search "PubSubClient" → Install
   - **ArduinoJson** by Benoit Blanchon (v6.x)
     - Tools → Manage Libraries → Search "ArduinoJson" → Install

## Configuration

Before uploading, update these settings in `esp32_ecg_mqtt_consumer.ino`:

```cpp
// WiFi Credentials
#define WIFI_SSID "YOUR_WIFI_SSID"
#define WIFI_PASSWORD "YOUR_WIFI_PASSWORD"

// MQTT Broker Configuration
#define MQTT_BROKER_IP "192.168.1.100"  // Your computer's IP (not "localhost")
#define MQTT_BROKER_PORT 1883
```

### Finding Your MQTT Broker IP

The DataSimulator runs on your computer. Find your computer's IP address:

**Windows:**
```cmd
ipconfig
```
Look for "IPv4 Address" under your active network adapter.

**macOS/Linux:**
```bash
ifconfig
# or
ip addr show
```
Look for your local network IP (usually 192.168.x.x or 10.0.x.x).

**Important:** Use the actual IP address, not "localhost" or "127.0.0.1"!

## Upload Instructions

1. **Connect ESP32** to your computer via USB
2. **Select Board**: Tools → Board → ESP32 Dev Module
3. **Select Port**: Tools → Port → (your ESP32 port)
4. **Open Serial Monitor**: Tools → Serial Monitor (set to 115200 baud)
5. **Upload**: Sketch → Upload
6. **Monitor**: Watch Serial Monitor for connection status and ECG data

## Usage

1. **Start DataSimulator** on your computer
2. **Connect to MQTT** in the simulator
3. **Start ECG simulation** for a patient
4. **ESP32 will automatically**:
   - Connect to WiFi
   - Connect to MQTT broker
   - Subscribe to `ecg/raw` topic
   - Receive and log ECG data

## Message Format

The ESP32 receives JSON messages with the following structure:

```json
{
  "timestamp": 1699876543210,
  "sample_time": 0.010,
  "patient_id": "A00/A00001",
  "sample_index": 1,
  "loop": 1,
  "ecg_value": -0.3456,
  "fs": 100,
  "units": "mV"
}
```

**Note:** Rhythm classification is intentionally excluded from MQTT messages. The ESP32 should perform inference using trained models to classify the ECG rhythm.

## Serial Output

The ESP32 logs:
- Connection status (WiFi, MQTT)
- First message confirmation (to verify data reception)
- Statistics every 10 seconds
- Error messages and warnings

### Serial Plotter Support

The ESP32 can output ECG data in a format compatible with Arduino IDE's Serial Plotter for real-time waveform visualization.

**To enable Serial Plotter:**
1. Set `ENABLE_SERIAL_PLOTTER` to `true` in the code
2. Adjust `PLOT_INTERVAL` to control plotting rate (default: every 10th sample = 10 Hz from 100 Hz input)
3. Open Arduino IDE → Tools → Serial Plotter (set to 115200 baud)
4. You'll see a real-time ECG waveform graph!

**Plot Format:**
- `ECG_Value(mV),Sample_Index,Time(s)` - Three values per line
- Serial Plotter will graph all three values over time
- ECG value is the main waveform you want to see

Example output (Serial Monitor):
```
[INFO] First ECG message received!
  Patient: A00/A00001 | Sampling Rate: 100 Hz
[INFO] Serial Plotter enabled - plotting every 10th sample (10 Hz effective rate)
[PLOT] Format: ECG_Value(mV),Sample_Index,Time(s)

--- Statistics ---
Messages received: 1000
Messages parsed: 998
Messages failed: 2
Success rate: 99.80%
WiFi RSSI: -45 dBm
MQTT connected: Yes
```

Example Serial Plotter output (when enabled):
```
-0.3456,10,0.100
-0.3124,20,0.200
-0.2891,30,0.300
-0.2567,40,0.400
...
```

**Note:** 
- Serial Plotter shows real-time ECG waveform graphs
- Individual ECG values are plotted at a reduced rate (configurable) to avoid overwhelming the plotter
- Use the `processECGData()` function to add custom processing (ML inference, R-peak detection, etc.)

## Customization

### Configure Serial Plotter

The Serial Plotter feature is controlled by two settings:

```cpp
#define ENABLE_SERIAL_PLOTTER true   // Enable/disable plotting
#define PLOT_INTERVAL 10             // Plot every Nth sample
```

**Plot Interval Guidelines:**
- **PLOT_INTERVAL = 1**: Plot every sample (100 Hz) - Very detailed but may be too fast
- **PLOT_INTERVAL = 5**: Plot every 5th sample (20 Hz) - Good detail, smooth graph
- **PLOT_INTERVAL = 10**: Plot every 10th sample (10 Hz) - Balanced (default)
- **PLOT_INTERVAL = 20**: Plot every 20th sample (5 Hz) - Less detail, smoother

**Using Serial Plotter:**
1. Set `ENABLE_SERIAL_PLOTTER` to `true`
2. Upload code to ESP32
3. Open Arduino IDE → Tools → Serial Plotter
4. Set baud rate to 115200
5. Watch the real-time ECG waveform!

**Note:** Serial Plotter and Serial Monitor cannot be open simultaneously. Close Serial Monitor before opening Serial Plotter.

### Add Custom Processing

Edit the `processECGData()` function to add:
- R-peak detection
- Heart rate calculation
- Data storage (SD card, EEPROM, etc.)
- Display updates (OLED, LCD)
- Forward to other services

### Adjust Reconnection Delays

```cpp
#define WIFI_RECONNECT_DELAY 5000   // 5 seconds
#define MQTT_RECONNECT_DELAY 5000   // 5 seconds
```

## Troubleshooting

### WiFi Connection Fails
- Check SSID and password are correct
- Ensure ESP32 is within WiFi range
- Check router allows new devices
- Try restarting ESP32

### MQTT Connection Fails
- Verify MQTT broker IP is correct (not "localhost")
- Ensure DataSimulator MQTT broker is running
- Check firewall allows port 1883
- Verify ESP32 and computer are on same network

### No Messages Received
- Check ESP32 is subscribed to correct topic (`ecg/raw`)
- Verify DataSimulator is publishing messages
- Check Serial Monitor for error messages
- Verify MQTT connection status in statistics

### JSON Parse Errors
- Check message format matches expected structure
- Increase `StaticJsonDocument<512>` size if messages are larger
- Monitor Serial output for specific error messages

## Error Handling

The program includes comprehensive error handling:
- ✅ WiFi disconnection → Automatic reconnection
- ✅ MQTT disconnection → Automatic reconnection
- ✅ JSON parse errors → Logged, message skipped
- ✅ Buffer overflow protection → Large messages rejected
- ✅ Exception handling → Try-catch blocks for safety
- ✅ Timeout detection → Warns if no messages for 30 seconds

## Memory Usage

- **Flash**: ~1.2 MB (plenty of room on ESP32)
- **RAM**: ~50 KB (leaves room for processing)
- **JSON Buffer**: 512 bytes (adjustable if needed)

## Performance

- **Message Processing**: < 1ms per message
- **Reconnection Time**: ~5 seconds
- **Max Message Rate**: Handles 100+ Hz easily
- **Power Consumption**: ~80-100mA (can be optimized for battery)

## License

Part of the ECG Simulator project.

