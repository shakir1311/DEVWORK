// ============================================================================
// PAYLOAD HELPERS
// ============================================================================

uint16_t readUInt16(const byte* data) {
  return (uint16_t)data[0] | ((uint16_t)data[1] << 8);
}

uint32_t readUInt32(const byte* data) {
  return ((uint32_t)data[0]) |
         ((uint32_t)data[1] << 8) |
         ((uint32_t)data[2] << 16) |
         ((uint32_t)data[3] << 24);
}

uint64_t readUInt64(const byte* data) {
  uint64_t value = 0;
  for (int i = 0; i < 8; i++) {
    value |= ((uint64_t)data[i]) << (8 * i);
  }
  return value;
}

/*
 * ESP32 ECG MQTT Consumer
 * 
 * Receives ECG data from the DataSimulator via MQTT and processes/logs it.
 * 
 * Features:
 * - Resilient WiFi and MQTT reconnection
 * - Exception-proof error handling
 * - Simple float value parsing (no JSON overhead)
 * - Configurable WiFi and MQTT settings
 * - Receives ECG data in mV (millivolts) at 300 Hz
 * 
 * Required Libraries:
 * - WiFi (built-in)
 * - PubSubClient by Nick O'Leary
 * 
 * Installation:
 * 1. Install Arduino IDE with ESP32 board support
 * 2. Install PubSubClient library (Tools -> Manage Libraries -> "PubSubClient")
 * 3. Update WiFi credentials and MQTT broker IP below
 * 4. Select board: Tools -> Board -> ESP32 Dev Module
 * 5. Upload to ESP32
 */

#include <WiFi.h>
#include <WiFiUdp.h>

// IMPORTANT: Set MQTT_MAX_PACKET_SIZE BEFORE including PubSubClient
// PubSubClient default is 256 bytes, which is too small for our chunk messages
// This define must be set before the include for it to take effect
// Note: Must be >= MQTT_BUFFER_SIZE
// ESP32 has limited RAM (~200-300KB free), so keep this reasonable
#define MQTT_MAX_PACKET_SIZE 32768  // 32 KB - must be >= MQTT_BUFFER_SIZE

#include <PubSubClient.h>
#include <LittleFS.h>
// ArduinoJson no longer needed - we only receive a single float value

// ============================================================================
// CONFIGURATION - Update these values for your setup
// ============================================================================

// WiFi Credentials
#define WIFI_SSID "shakiriot"
#define WIFI_PASSWORD "shakir1311"

// MQTT Broker Configuration (Simulator -> ESP32)
#define MQTT_BROKER_PORT 1883
#define MQTT_CHUNK_TOPIC "ecg/chunk"    // 1-second ECG chunks (receiving from simulator)
#define MQTT_ACK_TOPIC "ecg/ack"        // Acknowledgment topic (ESP32 publishes here)
#define MQTT_CLIENT_ID "ESP32_ECG_Consumer"

// EDGE Layer MQTT Configuration (ESP32 -> Pi4)
#define EDGE_MQTT_BROKER_PORT 1885           // Different port from simulator broker (1883)
#define EDGE_MQTT_CHUNK_TOPIC "ecg/edge/chunk"    // ECG chunks (sending to Pi4) - different topic
#define EDGE_MQTT_ACK_TOPIC "ecg/edge/ack"        // Acknowledgments (receiving from Pi4) - different topic
#define EDGE_MQTT_COMMAND_TOPIC "ecg/edge/command"  // Commands from Pi4 (requesting ECG data)
#define EDGE_MQTT_CLIENT_ID "ESP32_ECG_Producer"
#define EDGE_BROKER_DISCOVERY_PORT 1886      // UDP port for EDGE broker discovery - different from simulator (1884)
#define EDGE_BROKER_DISCOVERY_TIMEOUT 5000   // ms to wait for EDGE broker discovery
// MQTT Buffer Size Calculation:
// - Header: 12 bytes
// - Each sample: ~10 bytes (e.g., "-0.012345,")
// - For 1000 samples: 12 + (1000 * 10) = ~10KB
// - For 2000 samples: 12 + (2000 * 10) = ~20KB
// - Adding 50% safety margin: 32KB should handle up to ~2000 samples per chunk
// WARNING: ESP32 has limited RAM. Values > 32KB may cause allocation failures.
#define MQTT_BUFFER_SIZE 32768  // 32 KB - supports up to ~2000 samples per chunk

// Broker Discovery Configuration
#define BROKER_DISCOVERY_PORT 1884      // UDP port for broker discovery
#define BROKER_DISCOVERY_TIMEOUT 5000   // ms to wait for broker discovery
#define BROKER_DISCOVERY_MAGIC "ECG_MQTT_BROKER"  // Magic string to identify discovery packets
#define BROKER_DISCOVERY_RESPONSE "ECG_MQTT_BROKER_RESPONSE"  // Response magic string

// Reconnection Settings
#define WIFI_RECONNECT_DELAY 5000      // ms between WiFi reconnect attempts
#define MQTT_RECONNECT_DELAY 5000      // ms between MQTT reconnect attempts
#define MQTT_KEEPALIVE 60              // seconds

// Serial Settings
#define SERIAL_BAUD 115200

// LED Settings
#define LED_PIN 2  // Onboard LED pin (GPIO 2 for most ESP32 DevKit boards)

// ECG Data Settings
#define ECG_SAMPLING_RATE 300      // Hz - dataset reference rate
#define ECG_UNITS_MV true          // Values are in millivolts (mV)
#define MAX_ECG_SAMPLES 18000      // Buffer up to 60 seconds @ 300 Hz (72 KB RAM)
                                     // Reduced from 36000 to fit ESP32 DRAM constraints

// Persistent Storage Settings
#define ECG_DATA_FILE "/ecg_data.bin"      // File to store ECG buffer
#define ECG_METADATA_FILE "/ecg_meta.bin"  // File to store metadata (sample count, sampling rate)

// Chunk payload header (little-endian)
#define CHUNK_HEADER_BYTES 12  // uint16 ver + uint16 fs + uint16 chunk_num + uint16 total_chunks + uint32 samples


// ============================================================================
// GLOBAL VARIABLES
// ============================================================================

WiFiClient wifiClient;
PubSubClient mqttClient(wifiClient);  // For receiving from simulator

WiFiClient edgeWifiClient;
PubSubClient edgeMqttClient(edgeWifiClient);  // For sending to Pi4

// Chunked ECG buffering + transmission state
static float ecgBuffer[MAX_ECG_SAMPLES];
static uint32_t bufferedSamples = 0;
static uint32_t transmissionChunkIndex = 0;  // Current chunk being transmitted to EDGE
static bool transmissionActive = false;
static bool transmissionCompleted = false;  // Track if transmission has been completed for current ECG data
static unsigned long nextTransmissionTimeMs = 0;
static unsigned long transmissionIntervalMs = 0;  // Interval between chunk transmissions (N seconds)
static uint16_t payloadSamplingRateHz = ECG_SAMPLING_RATE;
static uint16_t edgeChunkSize = 0;  // Chunk size for EDGE transmission

// Patient information (received from simulator, forwarded to EDGE)
#define MAX_PATIENT_ID_LEN 32
#define MAX_DATE_LEN 16
#define MAX_TIME_LEN 16
static char patientId[MAX_PATIENT_ID_LEN] = "";
static float patientDurationSeconds = 0.0f;
static uint32_t patientTotalSamples = 0;
static char patientRecordDate[MAX_DATE_LEN] = "";
static char patientRecordTime[MAX_TIME_LEN] = "";

// Chunk reception state
// We no longer impose a fixed upper limit on chunk size; instead, we rely on the
// total number of samples fitting into ecgBuffer (MAX_ECG_SAMPLES). The simulator
// can choose any chunk size; we derive the "nominal" chunk size from the first
// chunk's sample_count field.
#define MAX_CHUNKS 1000  // Maximum number of chunks we can track (independent of chunk size)
static bool chunksReceived[MAX_CHUNKS];  // Track which chunks we've received
static uint16_t totalChunksExpected = 0;
static uint16_t chunksReceivedCount = 0;
static bool allChunksReceived = false;

// Nominal chunk size (in samples), learned from the first chunk. All subsequent
// chunks except the last are expected to have this sample count so that
// bufferOffset = chunkNum * payloadChunkSize produces a contiguous record.
static uint32_t payloadChunkSize = 0;

// Last-chunk tracking for automatic fallback playback when some chunks are missing
static bool lastChunkSeen = false;
static unsigned long lastChunkSeenTimeMs = 0;
static const unsigned long AUTO_FORCE_PLAYBACK_TIMEOUT_MS = 5000;  // 5 seconds

// MQTT Broker IP (discovered fresh on every run - no storage)
static String mqttBrokerIP = "";

// EDGE layer connection state (discovered fresh on every run - no storage)
static String edgeBrokerIP = "";
static bool edgeMqttConnected = false;

// LED blink state for connection status indication
static unsigned long lastLedBlinkTime = 0;
static bool ledBlinkState = false;

// ============================================================================
// LED BLINK CONTROL
// ============================================================================

void handleLEDBlink() {
  // LED blinking priority:
  // 1. If simulator MQTT not connected: blink 500ms ON, 500ms OFF (1s cycle)
  // 2. If EDGE MQTT not connected: blink 1000ms ON, 1000ms OFF (2s cycle)
  // 3. Otherwise: LED stays OFF
  
  unsigned long nowMs = millis();
  bool simulatorConnected = mqttClient.connected();
  bool edgeConnected = edgeMqttClient.connected();
  
  // If both connected, turn LED OFF
  if (simulatorConnected && edgeConnected) {
    digitalWrite(LED_PIN, LOW);
    ledBlinkState = false;
    lastLedBlinkTime = 0;
    return;
  }
  
  // Determine blink pattern based on which connection is missing
  unsigned long blinkPeriodMs;
  unsigned long blinkOnMs;
  
  if (!simulatorConnected) {
    // Simulator MQTT not connected: 500ms ON, 500ms OFF (1s cycle)
    blinkPeriodMs = 1000;
    blinkOnMs = 500;
  } else if (!edgeConnected) {
    // EDGE MQTT not connected: 1000ms ON, 1000ms OFF (2s cycle)
    blinkPeriodMs = 2000;
    blinkOnMs = 1000;
  } else {
    // Both connected (shouldn't reach here, but just in case)
    digitalWrite(LED_PIN, LOW);
    ledBlinkState = false;
    lastLedBlinkTime = 0;
    return;
  }
  
  // Initialize last blink time if needed
  if (lastLedBlinkTime == 0) {
    lastLedBlinkTime = nowMs;
  }
  
  // Calculate elapsed time since last blink cycle start
  unsigned long elapsed = nowMs - lastLedBlinkTime;
  
  // Handle overflow (if millis() wraps around)
  if (elapsed > blinkPeriodMs * 2) {
    lastLedBlinkTime = nowMs;
    elapsed = 0;
  }
  
  // Calculate position in blink cycle
  unsigned long cyclePosition = elapsed % blinkPeriodMs;
  
  // Update LED state based on cycle position
  bool shouldBeOn = (cyclePosition < blinkOnMs);
  
  if (shouldBeOn != ledBlinkState) {
    digitalWrite(LED_PIN, shouldBeOn ? HIGH : LOW);
    ledBlinkState = shouldBeOn;
  }
  
  // Reset cycle timer when period completes
  if (elapsed >= blinkPeriodMs) {
    lastLedBlinkTime = nowMs - (elapsed % blinkPeriodMs);
  }
}

// ============================================================================
// PATIENT INFO PARSING
// ============================================================================

void parsePatientInfo(const char* infoStr) {
  // Parse format: "PATIENT_INFO:patient_id|DURATION:duration|SAMPLES:samples|DATE:date|TIME:time"
  // Example: "PATIENT_INFO:A00001|DURATION:30.00|SAMPLES:9000|DATE:2024-01-01|TIME:12:00:00"
  
  // Clear previous patient info
  memset(patientId, 0, sizeof(patientId));
  patientDurationSeconds = 0.0f;
  patientTotalSamples = 0;
  memset(patientRecordDate, 0, sizeof(patientRecordDate));
  memset(patientRecordTime, 0, sizeof(patientRecordTime));
  
  // Check for "PATIENT_INFO:" prefix
  if (strncmp(infoStr, "PATIENT_INFO:", 13) != 0) {
    return;  // Not a patient info string
  }
  
  // Parse fields separated by |
  size_t len = strlen(infoStr);
  char* workStr = (char*)malloc(len + 1);
  if (!workStr) return;
  strcpy(workStr, infoStr + 13);  // Skip "PATIENT_INFO:"
  
  char* field = strtok(workStr, "|");
  bool isFirstField = true;
  
  while (field != NULL) {
    if (isFirstField) {
      // First field after "PATIENT_INFO:" is the patient ID (no prefix)
      strncpy(patientId, field, MAX_PATIENT_ID_LEN - 1);
      patientId[MAX_PATIENT_ID_LEN - 1] = '\0';
      isFirstField = false;
    } else if (strncmp(field, "DURATION:", 9) == 0) {
      // Extract duration
      patientDurationSeconds = atof(field + 9);
    } else if (strncmp(field, "SAMPLES:", 8) == 0) {
      // Extract total samples
      patientTotalSamples = (uint32_t)atoi(field + 8);
    } else if (strncmp(field, "DATE:", 5) == 0) {
      // Extract date
      strncpy(patientRecordDate, field + 5, MAX_DATE_LEN - 1);
      patientRecordDate[MAX_DATE_LEN - 1] = '\0';
    } else if (strncmp(field, "TIME:", 5) == 0) {
      // Extract time
      strncpy(patientRecordTime, field + 5, MAX_TIME_LEN - 1);
      patientRecordTime[MAX_TIME_LEN - 1] = '\0';
    }
    field = strtok(NULL, "|");
  }
  
  free(workStr);
  
  // Log parsed patient info
  Serial.println("========================================");
  Serial.println("[PATIENT INFO] Received patient information:");
  Serial.print("  Patient ID: ");
  Serial.println(patientId);
  Serial.print("  Duration: ");
  Serial.print(patientDurationSeconds, 2);
  Serial.println(" seconds");
  Serial.print("  Total Samples: ");
  Serial.println(patientTotalSamples);
  if (strlen(patientRecordDate) > 0) {
    Serial.print("  Date: ");
    Serial.println(patientRecordDate);
  }
  if (strlen(patientRecordTime) > 0) {
    Serial.print("  Time: ");
    Serial.println(patientRecordTime);
  }
  Serial.println("========================================");
}

// ============================================================================
// FORWARD DECLARATIONS
// ============================================================================

void discoverMQTTBroker();

// ============================================================================
// SETUP
// ============================================================================

void setup() {
  Serial.begin(SERIAL_BAUD);
  delay(1000);  // Give serial monitor time to connect
  
  // Initialize onboard LED
  pinMode(LED_PIN, OUTPUT);
  digitalWrite(LED_PIN, LOW);  // Start with LED OFF
  
  Serial.println("\n\n========================================");
  Serial.println("ESP32 ECG MQTT Consumer");
  Serial.println("========================================\n");
  
  // Print ECG data configuration
  Serial.print("ECG Configuration: ");
  Serial.print(ECG_SAMPLING_RATE);
  Serial.print(" Hz, ");
  if (ECG_UNITS_MV) {
    Serial.println("Units: mV (millivolts)");
  } else {
    Serial.println("Units: Unknown");
  }
  Serial.println("MQTT: QoS 1 (full-record payload = header + float array)");
  Serial.println();
  
  // Initialize LittleFS for persistent storage
  if (!LittleFS.begin(true)) {  // true = format if mount fails
    Serial.println("[ERROR] LittleFS mount failed! Data persistence disabled.");
  } else {
    Serial.println("✓ LittleFS initialized - ECG data can be saved/restored");
    // Try to load saved ECG data
    loadSavedECGData();
  }
  
  // Initialize WiFi
  initWiFi();
  
  // Clear any broker IPs to ensure fresh discovery (no storage or fallback)
  mqttBrokerIP = "";
  edgeBrokerIP = "";
  
  // Discover MQTT broker IP (always fresh discovery - no storage)
  // Simulator and Edge brokers are mutually exclusive - discover both independently
  discoverMQTTBroker();
  
  if (mqttBrokerIP.length() > 0) {
    Serial.print("Using Simulator MQTT broker at: ");
    Serial.println(mqttBrokerIP);
  
    // Initialize MQTT
    mqttClient.setServer(mqttBrokerIP.c_str(), MQTT_BROKER_PORT);
    mqttClient.setCallback(mqttCallback);
    mqttClient.setKeepAlive(MQTT_KEEPALIVE);
    mqttClient.setSocketTimeout(15);  // 15 second socket timeout
    
    // Set buffer size and check if allocation succeeded
    // Note: ESP32 has limited RAM (~200-300KB free), so very large buffers may fail
    Serial.print("Attempting to allocate MQTT buffer: ");
    Serial.print(MQTT_BUFFER_SIZE);
    Serial.println(" bytes");
    
    bool bufferSet = mqttClient.setBufferSize(MQTT_BUFFER_SIZE);
    if (!bufferSet) {
      Serial.println("========================================");
      Serial.println("[ERROR] Failed to allocate MQTT buffer!");
      Serial.print("Requested size: ");
      Serial.print(MQTT_BUFFER_SIZE);
      Serial.println(" bytes");
      Serial.println("This is likely too large for available RAM.");
      Serial.println("Try reducing MQTT_BUFFER_SIZE (e.g., 16384 for 16KB).");
      Serial.println("========================================");
      Serial.println("[ERROR] MQTT initialization failed - cannot receive data");
    } else {
      Serial.print("✓ MQTT buffer allocated successfully: ");
      Serial.print(MQTT_BUFFER_SIZE);
      Serial.println(" bytes");
      
      // Connect to MQTT
      connectToMQTT();
    }
  } else {
    Serial.println("[INFO] Simulator MQTT broker not discovered. Will retry in main loop.");
    Serial.println("ESP32 can still transmit to Edge if data is available onboard.");
  }
  
  // Discover and connect to EDGE MQTT broker (Pi4)
  // Edge broker discovery is independent - ESP32 can transmit to Edge even without simulator connection
  discoverEDGEBroker();
  if (edgeBrokerIP.length() > 0) {
    connectToEDGEMQTT();
  } else {
    Serial.println("[INFO] EDGE broker not discovered. Will retry in main loop.");
  }
  
  Serial.println("\nSetup complete.");
  Serial.println("Simulator and Edge MQTT brokers are mutually exclusive.");
  Serial.println("ESP32 can transmit to Edge even without simulator connection if data is available onboard.");
  Serial.println("Waiting for ECG chunks from simulator or ready to transmit to Edge...\n");
}

// ============================================================================
// MAIN LOOP
// ============================================================================

void loop() {
  // Maintain WiFi connection
  if (WiFi.status() != WL_CONNECTED) {
    handleWiFiDisconnect();
  }
  
  // Maintain MQTT connection (simulator -> ESP32)
  if (!mqttClient.connected()) {
    handleMQTTDisconnect();
  } else {
    // Call loop() multiple times to ensure messages are processed
    // Large messages may need multiple loop() calls to be fully received
    mqttClient.loop();  // Process incoming messages - CRITICAL: must be called regularly
    mqttClient.loop();  // Extra call for large messages
    mqttClient.loop();  // Extra call for large messages
  }
  
  // Maintain EDGE MQTT connection (ESP32 -> Pi4)
  // Simulator and Edge MQTT are mutually exclusive - Edge transmission works independently
  // ESP32 can transmit to Edge even without simulator connection if data is available onboard
  if (!edgeMqttClient.connected()) {
    handleEDGEMQTTDisconnect();
    edgeMqttConnected = false;
  } else {
    edgeMqttClient.loop();
    edgeMqttConnected = true;
    
    // If we have ECG data ready (from simulator or loaded from flash) and EDGE is connected,
    // automatically start transmission. Works independently of simulator MQTT connection.
    // Only start if transmission hasn't been completed yet for this ECG data
    if (bufferedSamples > 0 && allChunksReceived && !transmissionActive && !transmissionCompleted) {
      Serial.println("========================================");
      Serial.println("[EDGE] ECG data available - starting automatic transmission...");
      startTransmissionToEDGE();
    }
  }
  
  // Transmit chunks to EDGE layer periodically
  // Note: Only requires EDGE MQTT connection, NOT simulator MQTT connection
  // ESP32 can transmit saved data to Edge even if simulator is not connected
  if (transmissionActive) {
    transmitChunksToEDGE();
  } else {
    // Handle LED blinking for connection status (only when not transmitting)
    handleLEDBlink();
  }

  // If we've seen the last chunk but not all chunks, automatically start
  // playback after a timeout, using whatever data we have, and log which
  // chunks were missing. This prevents the device from getting "stuck"
  // waiting forever for a small number of missing chunks.
  if (!allChunksReceived && lastChunkSeen) {
    unsigned long nowMs = millis();
    if (nowMs - lastChunkSeenTimeMs > AUTO_FORCE_PLAYBACK_TIMEOUT_MS) {
      lastChunkSeen = false;  // Only trigger once
      Serial.println("========================================");
      Serial.println("⚠️ WARNING: Last chunk received but not all chunks present.");
      Serial.print("Received: ");
      Serial.print(chunksReceivedCount);
      Serial.print("/");
      Serial.print(totalChunksExpected);
      Serial.println(" chunks");
      Serial.println("Auto-starting transmission with incomplete data...");
      Serial.println("Missing chunks will appear as zeros in the waveform.");
      Serial.println("Use Serial command 'STATUS' to inspect missing chunks.");
      Serial.println("========================================");
      printMissingChunks();
      forceStartTransmission();
    }
  }
  
  // Handle serial commands for debugging
  if (Serial.available() > 0) {
    String cmd = Serial.readStringUntil('\n');
    cmd.trim();
    cmd.toUpperCase();
    
    if (cmd == "STATUS" || cmd == "S") {
      printMissingChunks();
    } else if (cmd == "FORCE" || cmd == "F") {
      forceStartTransmission();
    } else if (cmd == "DUMP" || cmd == "D") {
      dumpECGBuffer();
    } else if (cmd == "CLEAR" || cmd == "C") {
      clearSavedECGData();
    } else if (cmd == "HELP" || cmd == "H" || cmd == "?") {
      Serial.println("========================================");
      Serial.println("Available commands:");
      Serial.println("  STATUS (S) - Show chunk reception status");
      Serial.println("  FORCE (F)  - Force start playback with incomplete data");
      Serial.println("  DUMP  (D)  - Print full ECG buffer (index,value) once");
      Serial.println("  CLEAR (C)  - Clear saved ECG data from flash");
      Serial.println("  HELP  (H)  - Show this help message");
      Serial.println("========================================");
    }
  }
  
  // Transmission to EDGE is handled in transmitChunksToEDGE() called above
  
  // Small delay to yield to WiFi/MQTT stack, but keep it minimal
  delay(1);  // Yield to allow WiFi/MQTT stack to run
}

// ============================================================================
// WIFI FUNCTIONS
// ============================================================================

void initWiFi() {
  Serial.print("Connecting to WiFi: ");
  Serial.println(WIFI_SSID);
  
  WiFi.mode(WIFI_STA);
  WiFi.begin(WIFI_SSID, WIFI_PASSWORD);
  
  int attempts = 0;
  while (WiFi.status() != WL_CONNECTED && attempts < 20) {
    delay(500);
    Serial.print(".");
    attempts++;
  }
  
  if (WiFi.status() == WL_CONNECTED) {
    Serial.println("\nWiFi connected!");
    Serial.print("IP address: ");
    Serial.println(WiFi.localIP());
    Serial.print("Signal strength (RSSI): ");
    Serial.print(WiFi.RSSI());
    Serial.println(" dBm");
  } else {
    Serial.println("\n[ERROR] WiFi connection failed!");
    Serial.println("Will retry in main loop...");
  }
}

void handleWiFiDisconnect() {
  static unsigned long lastAttempt = 0;
  
  if (millis() - lastAttempt > WIFI_RECONNECT_DELAY) {
    Serial.println("[WARNING] WiFi disconnected. Attempting reconnect...");
    WiFi.disconnect();
    WiFi.reconnect();
    lastAttempt = millis();
  }
}

// ============================================================================
// MQTT FUNCTIONS
// ============================================================================

void discoverMQTTBroker() {
  Serial.println("========================================");
  Serial.println("Discovering MQTT broker...");
  
  // Always perform fresh discovery - no storage or fallback
  Serial.println("Attempting UDP broadcast discovery...");
  
  WiFiUDP udp;
  if (!udp.begin(BROKER_DISCOVERY_PORT)) {
    Serial.println("[ERROR] Failed to start UDP socket for discovery");
    return;
  }
  
  // Send broadcast discovery packet
  IPAddress broadcastIP = WiFi.broadcastIP();
  String discoveryMsg = BROKER_DISCOVERY_MAGIC;
  udp.beginPacket(broadcastIP, BROKER_DISCOVERY_PORT);
  udp.write((const uint8_t*)discoveryMsg.c_str(), discoveryMsg.length());
  udp.endPacket();
  
  Serial.print("Sent discovery broadcast to ");
  Serial.print(broadcastIP);
  Serial.print(":");
  Serial.println(BROKER_DISCOVERY_PORT);
  Serial.print("Waiting up to ");
  Serial.print(BROKER_DISCOVERY_TIMEOUT / 1000);
  Serial.println(" seconds for broker response...");
  
  // Wait for response
  unsigned long startTime = millis();
  while (millis() - startTime < BROKER_DISCOVERY_TIMEOUT) {
    int packetSize = udp.parsePacket();
    if (packetSize > 0) {
      char buffer[256];
      int len = udp.read(buffer, sizeof(buffer) - 1);
      if (len > 0) {
        buffer[len] = '\0';
        String response = String(buffer);
        
        // Check if this is a valid broker response
        if (response.startsWith(BROKER_DISCOVERY_RESPONSE)) {
          // Extract IP address from response (format: "ECG_MQTT_BROKER_RESPONSE:IP:PORT")
          int colonIndex = response.indexOf(':', strlen(BROKER_DISCOVERY_RESPONSE) + 1);
          if (colonIndex > 0) {
            String brokerIP = response.substring(strlen(BROKER_DISCOVERY_RESPONSE) + 1, colonIndex);
            IPAddress brokerAddr;
            if (brokerAddr.fromString(brokerIP)) {
              mqttBrokerIP = brokerIP;
              Serial.print("✓ Discovered MQTT broker at: ");
              Serial.println(mqttBrokerIP);
              
              // No storage - IP is only kept in memory for this session
              udp.stop();
              return;
            }
          }
        }
      }
    }
    delay(100);  // Small delay to avoid busy-waiting
  }
  
  udp.stop();
  Serial.println("✗ Broker discovery timeout - no response received");
}


void connectToMQTT() {
  if (mqttBrokerIP.length() == 0) {
    Serial.println("[ERROR] No broker IP available for connection");
    Serial.println("  - Run broker discovery first");
    return;
  }
  
  // Verify WiFi is connected
  if (WiFi.status() != WL_CONNECTED) {
    Serial.println("[ERROR] WiFi not connected - cannot connect to MQTT broker");
    Serial.println("  - Wait for WiFi connection first");
    return;
  }
  
  Serial.print("Connecting to MQTT broker: ");
  Serial.print(mqttBrokerIP);
  Serial.print(":");
  Serial.println(MQTT_BROKER_PORT);
  Serial.print("ESP32 IP: ");
  Serial.println(WiFi.localIP());
  
  int attempts = 0;
  while (!mqttClient.connected() && attempts < 10) {  // Increased attempts
    if (mqttClient.connect(MQTT_CLIENT_ID)) {
      Serial.println("MQTT connected!");
      
      // Subscribe to ECG chunk topic (QoS 1 for reliable delivery)
      if (mqttClient.subscribe(MQTT_CHUNK_TOPIC, 1)) {
        Serial.print("✓ Subscribed to topic: ");
        Serial.print(MQTT_CHUNK_TOPIC);
        Serial.println(" (QoS 1)");
        Serial.println("Waiting for ECG chunks...");
      } else {
        Serial.print("[ERROR] Failed to subscribe to ECG chunk topic! Error code: ");
        Serial.println(mqttClient.state());
      }
      
      // Verify subscriptions by checking state
      Serial.print("MQTT client state: ");
      Serial.println(mqttClient.state());
      Serial.println("Ready to receive messages.");
      break;  // Successfully connected and subscribed
    } else {
      int state = mqttClient.state();
      Serial.print("Connection attempt ");
      Serial.print(attempts + 1);
      Serial.print(" failed. Error code: ");
      Serial.print(state);
      
      // Add brief error description
      if (state == -2) {
        Serial.print(" (Connection failed - broker unreachable)");
      } else if (state == -4) {
        Serial.print(" (Connection timeout)");
      }
      
      Serial.println(" - retrying...");
      attempts++;
      delay(2000);  // Longer delay between attempts
    }
  }
  
  if (!mqttClient.connected()) {
    Serial.println("\n[ERROR] MQTT connection failed after all attempts!");
    Serial.print("Final error code: ");
    int state = mqttClient.state();
    Serial.println(state);
    
    // Decode error code
    Serial.print("Error meaning: ");
    switch (state) {
      case -4:
        Serial.println("MQTT_CONNECTION_TIMEOUT - Connection timed out");
        break;
      case -3:
        Serial.println("MQTT_CONNECTION_LOST - Connection lost");
        break;
      case -2:
        Serial.println("MQTT_CONNECT_FAILED - Connection failed (broker not reachable)");
        Serial.println("  - Check if broker is running");
        Serial.println("  - Verify broker IP address is correct");
        Serial.println("  - Check network connectivity (ping broker IP)");
        Serial.println("  - Ensure broker is bound to network interface, not localhost");
        break;
      case -1:
        Serial.println("MQTT_DISCONNECTED - Disconnected");
        break;
      case 1:
        Serial.println("MQTT_CONNECT_BAD_PROTOCOL - Bad protocol version");
        break;
      case 2:
        Serial.println("MQTT_CONNECT_BAD_CLIENT_ID - Client ID rejected");
        break;
      case 3:
        Serial.println("MQTT_CONNECT_UNAVAILABLE - Server unavailable");
        break;
      case 4:
        Serial.println("MQTT_CONNECT_BAD_CREDENTIALS - Bad credentials");
        break;
      case 5:
        Serial.println("MQTT_CONNECT_UNAUTHORIZED - Unauthorized");
        break;
      default:
        Serial.println("Unknown error code");
        break;
    }
    
    Serial.println("\nTroubleshooting steps:");
    Serial.println("  1. Verify simulator MQTT broker is running");
    Serial.println("  2. Check broker is bound to network IP (not 127.0.0.1)");
    Serial.println("  3. Ensure broker is broadcasting discovery packets");
    Serial.println("  4. Check WiFi connection and IP address");
    Serial.print("     ESP32 IP: ");
    Serial.println(WiFi.localIP());
    Serial.print("     Broker IP: ");
    Serial.println(mqttBrokerIP);
  }
}

void handleMQTTDisconnect() {
  static unsigned long lastAttempt = 0;
  static unsigned long lastDiscoveryAttempt = 0;
  
  // Clear broker IP to force fresh discovery on every disconnect
  mqttBrokerIP = "";
  
  // Try to rediscover broker if disconnected (always fresh discovery)
  if (millis() - lastDiscoveryAttempt > 10000) {  // Try discovery every 10 seconds
    Serial.println("[INFO] MQTT disconnected - performing fresh broker discovery...");
    discoverMQTTBroker();  // Always fresh discovery - no storage
    lastDiscoveryAttempt = millis();
  }
  
  if (millis() - lastAttempt > MQTT_RECONNECT_DELAY) {
    if (mqttBrokerIP.length() > 0) {
      Serial.println("[WARNING] MQTT disconnected. Attempting reconnect...");
      connectToMQTT();
    }
    lastAttempt = millis();
  }
}

void sendAck(uint16_t chunkNum) {
  // Send acknowledgment: chunk number as string (QoS 0)
  // Use a small buffer to ensure proper formatting
  char ackBuffer[10];
  snprintf(ackBuffer, sizeof(ackBuffer), "%d", chunkNum);
  
  // Ensure MQTT client is connected and loop is called before publishing
  mqttClient.loop();
  
  if (mqttClient.publish(MQTT_ACK_TOPIC, (const uint8_t*)ackBuffer, strlen(ackBuffer), false)) {
    // Give the client time to send the message
    mqttClient.loop();
  } else {
    Serial.print("[ERROR] Failed to send ACK for chunk ");
    Serial.println(chunkNum);
  }
}

void printMissingChunks() {
  if (totalChunksExpected == 0) {
    Serial.println("[INFO] No chunks expected yet");
    return;
  }
  
  Serial.println("========================================");
  Serial.print("[INFO] Chunk status: ");
  Serial.print(chunksReceivedCount);
  Serial.print("/");
  Serial.print(totalChunksExpected);
  Serial.println(" received");
  
  if (chunksReceivedCount == totalChunksExpected) {
    Serial.println("[INFO] All chunks received!");
  } else {
    Serial.print("[INFO] Missing ");
    Serial.print(totalChunksExpected - chunksReceivedCount);
    Serial.println(" chunks:");
    
    int missingCount = 0;
    for (uint16_t i = 0; i < totalChunksExpected && missingCount < 50; i++) {
      if (!chunksReceived[i]) {
        Serial.print("  Chunk ");
        Serial.print(i + 1);
        Serial.print(" (index ");
        Serial.print(i);
        Serial.println(")");
        missingCount++;
      }
    }
    
    if (totalChunksExpected - chunksReceivedCount > 50) {
      Serial.println("  ... (showing first 50 missing chunks)");
    }
  }
  Serial.println("========================================");
}

// Print the full buffered ECG record: one line per sample:
//   index,ECG_value_mV
// This is only called on demand via a Serial command, not during playback.
void dumpECGBuffer() {
  if (bufferedSamples == 0) {
    Serial.println("[INFO] No ECG samples buffered yet.");
    return;
  }

  Serial.println("========================================");
  Serial.println("[DUMP] ECG buffer contents (index,value_mV):");
  Serial.print("Total samples: ");
  Serial.println(bufferedSamples);

  for (uint32_t i = 0; i < bufferedSamples; i++) {
    Serial.print(i);
    Serial.print(",");
    Serial.println(ecgBuffer[i], 6);
  }

  Serial.println("========================================");
}

void forceStartTransmission() {
  if (bufferedSamples == 0) {
    Serial.println("[ERROR] No samples buffered - cannot start transmission");
    return;
  }
  
  if (transmissionActive) {
    Serial.println("[INFO] Transmission is already active");
    return;
  }
  
  Serial.println("========================================");
  Serial.println("[FORCE] Starting transmission with incomplete data...");
  Serial.print("Received: ");
  Serial.print(chunksReceivedCount);
  Serial.print("/");
  Serial.print(totalChunksExpected);
  Serial.println(" chunks");
  
  allChunksReceived = true;
  startTransmissionToEDGE();
  
  Serial.println("⚠️ Warning: Missing chunks will appear as zeros in transmission");
  Serial.println("========================================");
}

void checkAllChunksReceived() {
  if (allChunksReceived) return;
  
  if (chunksReceivedCount == totalChunksExpected && totalChunksExpected > 0) {
    allChunksReceived = true;
    
    // Save ECG data to flash for persistence across restarts
    saveECGDataToFlash();
    
    {
      Serial.println("========================================");
      Serial.println("✓ All chunks received! Starting automatic transmission to EDGE...");
      Serial.print("Total samples: ");
      Serial.println(bufferedSamples);
      Serial.print("Sampling rate: ");
      Serial.print(payloadSamplingRateHz);
      Serial.println(" Hz");
      Serial.print("Duration: ");
      Serial.print((double)bufferedSamples / payloadSamplingRateHz, 2);
      Serial.println(" s");
      Serial.println("Data saved to flash for persistence.");
      Serial.println("========================================");
    }
    
    // Automatically start transmission to EDGE when all chunks are received
    // Transmission will start as soon as EDGE MQTT connection is available
    // If EDGE is not connected yet, transmission will start when connection is established
  } else if (totalChunksExpected > 0 && chunksReceivedCount < totalChunksExpected) {
    // Provide status update every 10 chunks to help identify issues
    static uint16_t lastReportedCount = 0;
    if (chunksReceivedCount > lastReportedCount && chunksReceivedCount % 10 == 0) {
      lastReportedCount = chunksReceivedCount;
    }
    
    // If we're near the end but missing chunks, print a helpful message
    static bool warningShown = false;
    if (!warningShown && chunksReceivedCount >= totalChunksExpected - 5 && chunksReceivedCount < totalChunksExpected) {
      unsigned long now = millis();
      static unsigned long lastChunkTime = 0;
      static uint16_t lastChunkCount = 0;
      
      // Track when chunks stop arriving
      if (chunksReceivedCount != lastChunkCount) {
        lastChunkTime = now;
        lastChunkCount = chunksReceivedCount;
      } else if (now - lastChunkTime > 5000) {  // 5 seconds without new chunks
        warningShown = true;
        Serial.println("========================================");
        Serial.println("⚠️ WARNING: Chunk reception stalled!");
        Serial.print("Received: ");
        Serial.print(chunksReceivedCount);
        Serial.print("/");
        Serial.print(totalChunksExpected);
        Serial.println(" chunks");
        Serial.print("Missing: ");
        Serial.print(totalChunksExpected - chunksReceivedCount);
        Serial.println(" chunks");
        Serial.println();
        Serial.println("Type 'STATUS' to see missing chunks");
        Serial.println("Type 'FORCE' to start playback anyway");
        Serial.println("========================================");
      }
    }
  }
}

void mqttCallback(char* topic, byte* payload, unsigned int length) {
  // Process incoming MQTT message
  
  // Check if this is an ECG chunk message
  if (strcmp(topic, MQTT_CHUNK_TOPIC) != 0) {
    // Unexpected topic - ignore
    return;
  }
  
  // Validate payload size
  if (length < CHUNK_HEADER_BYTES) {
    Serial.print("[ERROR] Chunk payload too small: ");
    Serial.println(length);
    return;
  }
  
  // Parse chunk header (little-endian)
  uint16_t formatVersion = readUInt16(payload);
  uint16_t samplingRate = readUInt16(payload + 2);
  uint16_t chunkNum = readUInt16(payload + 4);
  uint16_t totalChunks = readUInt16(payload + 6);
  uint32_t sampleCount = readUInt32(payload + 8);
  
  if (formatVersion != 3) {
    Serial.print("[ERROR] Unsupported format version: ");
    Serial.print(formatVersion);
    Serial.println(" (expected 3)");
    return;
  }

  // Detect start of a NEW ECG record (chunk 0) while we still have state from
  // a previous record. In this case we immediately stop playback and clear
  // all chunk/buffer state so the device focuses solely on receiving the new
  // data.
  if (chunkNum == 0 && totalChunksExpected > 0) {
    transmissionActive = false;
    transmissionCompleted = false;  // Reset completion flag for new ECG data
    allChunksReceived = false;
    transmissionChunkIndex = 0;
    bufferedSamples = 0;
    payloadChunkSize = 0;
    lastChunkSeen = false;
    lastChunkSeenTimeMs = 0;

    for (int i = 0; i < MAX_CHUNKS; i++) {
      chunksReceived[i] = false;
    }

    // Optionally clear buffer to avoid any stale samples being reused
    for (uint32_t i = 0; i < MAX_ECG_SAMPLES; i++) {
      ecgBuffer[i] = 0.0f;
    }

    totalChunksExpected = 0;  // Force re-initialization below
    
    // Clear patient info for new ECG record
    memset(patientId, 0, sizeof(patientId));
    patientDurationSeconds = 0.0f;
    patientTotalSamples = 0;
    memset(patientRecordDate, 0, sizeof(patientRecordDate));
    memset(patientRecordTime, 0, sizeof(patientRecordTime));
    
    // Turn off LED - previous reception aborted
    digitalWrite(LED_PIN, LOW);

    Serial.println("========================================");
    Serial.println("[INFO] New ECG record detected (chunk 0).");
    Serial.println("Stopping playback and clearing previous data...");
    Serial.println("========================================");
  }
  
  // Initialize on first chunk
  if (totalChunksExpected == 0) {
    // Basic validation of chunk count against our tracking capacity
    if (totalChunks == 0 || totalChunks > MAX_CHUNKS) {
      Serial.print("[ERROR] total_chunks out of range: ");
      Serial.print(totalChunks);
      Serial.print(" (max tracked: ");
      Serial.print(MAX_CHUNKS);
      Serial.println(")");
      return;
    }

    // Derive nominal chunk size from the first chunk's sample_count
    payloadChunkSize = sampleCount;
    if (payloadChunkSize == 0) {
      Serial.println("[ERROR] First chunk has zero samples");
      return;
    }

    // Ensure the full record fits in our ECG buffer
    uint32_t estimatedTotalSamples = (uint32_t)totalChunks * payloadChunkSize;
    if (estimatedTotalSamples > MAX_ECG_SAMPLES) {
      Serial.print("[ERROR] Expected samples exceed buffer: ");
      Serial.print(estimatedTotalSamples);
      Serial.print(" (max ");
      Serial.print(MAX_ECG_SAMPLES);
      Serial.println(")");
      return;
    }

    totalChunksExpected = totalChunks;
    payloadSamplingRateHz = (samplingRate > 0) ? samplingRate : ECG_SAMPLING_RATE;
    // Transmission interval will be calculated in startTransmissionToEDGE()
    
    // Reset chunk tracking
    for (int i = 0; i < MAX_CHUNKS; i++) {
      chunksReceived[i] = false;
    }
    chunksReceivedCount = 0;
    allChunksReceived = false;
    bufferedSamples = 0;
    transmissionCompleted = false;  // Reset completion flag for new ECG data
    
    // Turn on LED - data reception started
    digitalWrite(LED_PIN, HIGH);
    lastChunkSeen = false;
    lastChunkSeenTimeMs = 0;
  
    Serial.println("========================================");
    Serial.println("Receiving ECG chunks...");
    Serial.print("Total chunks expected: ");
    Serial.println(totalChunksExpected);
    Serial.print("Sampling rate: ");
    Serial.print(payloadSamplingRateHz);
    Serial.println(" Hz");
    Serial.print("Nominal chunk size: ");
    Serial.print(payloadChunkSize);
    Serial.println(" samples");
    Serial.println("========================================");
  }
  
  // Validate chunk number
  if (chunkNum >= totalChunksExpected) {
    Serial.print("[ERROR] Invalid chunk number: ");
    Serial.print(chunkNum);
    Serial.print(" (expected < ");
    Serial.print(totalChunksExpected);
    Serial.println(")");
    return;
  }
  
  // Check if we already received this chunk
  if (chunksReceived[chunkNum]) {
    Serial.print("[WARNING] Duplicate chunk ");
    Serial.println(chunkNum);
    // Still send ACK for duplicate
    sendAck(chunkNum);
    return;
  }
  
  // Validate sample count
  if (sampleCount == 0) {
    Serial.println("[ERROR] Invalid sample count in chunk: 0");
    return;
  }

  // For all chunks after the first, enforce consistent nominal size for
  // non-final chunks to keep the record contiguous. The last chunk is
  // allowed to be shorter.
  if (payloadChunkSize > 0 && chunkNum < totalChunksExpected - 1 && sampleCount != payloadChunkSize) {
    Serial.print("[WARNING] Inconsistent sample count in chunk ");
    Serial.print(chunkNum);
    Serial.print(": got ");
    Serial.print(sampleCount);
    Serial.print(", expected ");
    Serial.println(payloadChunkSize);
  }
  
  // Calculate where to store this chunk in the buffer based on nominal chunk size
  uint32_t bufferOffset = (payloadChunkSize > 0)
                          ? (uint32_t)chunkNum * payloadChunkSize
                          : (uint32_t)chunkNum * sampleCount;
  
  if (bufferOffset + sampleCount > MAX_ECG_SAMPLES) {
    Serial.print("[ERROR] Chunk would exceed buffer size!");
    return;
  }
  
  // Parse string-based values for 100% precision
  // Format: "value1,value2,value3,..." (comma-separated float strings)
  // For chunk 0: "PATIENT_INFO:...\nvalue1,value2,..."
  const char* bodyStart = (const char*)(payload + CHUNK_HEADER_BYTES);
  size_t bodyLength = length - CHUNK_HEADER_BYTES;
  
  // Create null-terminated string for parsing
  char* bodyStr = (char*)malloc(bodyLength + 1);
  if (!bodyStr) {
    Serial.println("[ERROR] Out of memory for parsing chunk body");
    return;
  }
  memcpy(bodyStr, bodyStart, bodyLength);
  bodyStr[bodyLength] = '\0';
  
  // Parse patient info from chunk 0 (if present)
  char* ecgDataStart = bodyStr;
  if (chunkNum == 0 && strncmp(bodyStr, "PATIENT_INFO:", 13) == 0) {
    // Extract patient info line (ends with \n)
    char* newlinePos = strchr(bodyStr, '\n');
    if (newlinePos) {
      *newlinePos = '\0';  // Null-terminate patient info line
      ecgDataStart = newlinePos + 1;  // ECG data starts after newline
      
      // Parse patient info: "PATIENT_INFO:patient_id|DURATION:duration|SAMPLES:samples|DATE:date|TIME:time"
      parsePatientInfo(bodyStr);
    }
  }
  
  // Parse comma-separated float values (skip patient info line if present)
  uint32_t parsedCount = 0;
  char* token = strtok(ecgDataStart, ",");
  
  while (token != NULL && parsedCount < sampleCount) {
    // Convert string to float with full precision
    float value = strtof(token, NULL);
    
    // Store in buffer
    uint32_t idx = bufferOffset + parsedCount;
    ecgBuffer[idx] = value;
    
    parsedCount++;
    token = strtok(NULL, ",");
  }
  
  free(bodyStr);
  
  if (parsedCount != sampleCount) {
    Serial.print("[ERROR] Parsed ");
    Serial.print(parsedCount);
    Serial.print(" values, expected ");
    Serial.println(sampleCount);
    return;
  }
  
  // Mark chunk as received
  chunksReceived[chunkNum] = true;
  chunksReceivedCount++;
  bufferedSamples = bufferOffset + sampleCount;  // Update total samples
  
  Serial.print("[CHUNK] Received chunk ");
  Serial.print(chunkNum + 1);
  Serial.print("/");
  Serial.print(totalChunksExpected);
  Serial.print(" (");
  Serial.print(sampleCount);
  Serial.print(" samples, offset=");
  Serial.print(bufferOffset);
  Serial.print(", total=");
  Serial.print(chunksReceivedCount);
  Serial.print("/");
  Serial.print(totalChunksExpected);
  Serial.print(", buffered=");
  Serial.print(bufferedSamples);
  Serial.println(")");
  
  // Debug: Print first and last values of chunk for verification
  if (chunkNum == totalChunksExpected - 1) {  // Last chunk
    Serial.print("[DEBUG] Last chunk values: first=");
    Serial.print(ecgBuffer[bufferOffset], 6);
    Serial.print(", last=");
    Serial.print(ecgBuffer[bufferOffset + sampleCount - 1], 6);
    Serial.print(" (indices ");
    Serial.print(bufferOffset);
    Serial.print("-");
    Serial.print(bufferOffset + sampleCount - 1);
    Serial.println(")");
  }
  
  // Send acknowledgment
  sendAck(chunkNum);
  
  // Check if all chunks received
  checkAllChunksReceived();

  // If this was the last chunk, remember when we saw it so we can auto-start
  // playback after a timeout even if some chunks are missing.
  if (chunkNum == totalChunksExpected - 1) {
    lastChunkSeen = true;
    lastChunkSeenTimeMs = millis();
  }
  }
  
// ============================================================================
// VALUE PARSING (Now handled directly in mqttCallback)
// ============================================================================
// Note: Parsing is now done directly in mqttCallback for the full-record
// binary payload format (header + float array).

// ============================================================================
// ECG DATA PROCESSING
// ============================================================================

void processECGData(float ecgValue) {
  // TODO: Add your custom processing here:
  // - Heart rate calculation
  // - ML model inference (rhythm classification)
  // - Data storage (SD card, EEPROM, etc.)
  // - Display updates (OLED, LCD)
  // - Forward to other services
  // 
  // Note: ECG voltage value is available in ecgValue parameter (float, in mV)
}

// ============================================================================
// EDGE LAYER FUNCTIONS
// ============================================================================

void discoverEDGEBroker() {
  Serial.println("========================================");
  Serial.println("Discovering EDGE MQTT broker (Pi4)...");
  
  WiFiUDP udp;
  if (!udp.begin(EDGE_BROKER_DISCOVERY_PORT)) {
    Serial.println("[ERROR] Failed to start UDP socket for EDGE broker discovery");
    return;
  }
  
  // Send broadcast discovery packet
  IPAddress broadcastIP = WiFi.broadcastIP();
  String discoveryMsg = BROKER_DISCOVERY_MAGIC;
  udp.beginPacket(broadcastIP, EDGE_BROKER_DISCOVERY_PORT);
  udp.write((const uint8_t*)discoveryMsg.c_str(), discoveryMsg.length());
  udp.endPacket();
  
  Serial.print("Sent EDGE discovery broadcast to ");
  Serial.print(broadcastIP);
  Serial.print(":");
  Serial.println(EDGE_BROKER_DISCOVERY_PORT);
  Serial.print("Waiting up to ");
  Serial.print(EDGE_BROKER_DISCOVERY_TIMEOUT / 1000);
  Serial.println(" seconds for EDGE broker response...");
  
  // Wait for response
  unsigned long startTime = millis();
  while (millis() - startTime < EDGE_BROKER_DISCOVERY_TIMEOUT) {
    int packetSize = udp.parsePacket();
    if (packetSize > 0) {
      char buffer[256];
      int len = udp.read(buffer, sizeof(buffer) - 1);
      buffer[len] = '\0';
      String response = String(buffer);
      
      IPAddress remoteIP = udp.remoteIP();
      Serial.print("Received EDGE discovery response from ");
      Serial.print(remoteIP);
      Serial.print(": ");
      Serial.println(response);
      
      // Parse response: "ECG_MQTT_BROKER_RESPONSE:IP:PORT"
      if (response.startsWith(BROKER_DISCOVERY_RESPONSE)) {
        int colon1 = response.indexOf(':', 0);
        int colon2 = response.indexOf(':', colon1 + 1);
        if (colon1 > 0 && colon2 > colon1) {
          String brokerIP = response.substring(colon1 + 1, colon2);
          int brokerPort = response.substring(colon2 + 1).toInt();
          
          edgeBrokerIP = brokerIP;
          Serial.print("✓ Discovered EDGE broker at: ");
          Serial.print(edgeBrokerIP);
          Serial.print(":");
          Serial.println(brokerPort);
          udp.stop();
          return;
        }
      }
    }
    delay(100);
  }
  
  Serial.println("[WARNING] EDGE broker discovery timeout");
  udp.stop();
}

void connectToEDGEMQTT() {
  if (edgeBrokerIP.length() == 0) {
    Serial.println("[ERROR] No EDGE broker IP available");
    return;
  }
  
  Serial.print("Connecting to EDGE MQTT broker at ");
  Serial.print(edgeBrokerIP);
  Serial.print(":");
  Serial.println(EDGE_MQTT_BROKER_PORT);
  
  edgeMqttClient.setServer(edgeBrokerIP.c_str(), EDGE_MQTT_BROKER_PORT);
  edgeMqttClient.setKeepAlive(60);
  
  // Set buffer size for EDGE MQTT client (same as main client)
  // This is critical for large payloads - default is only 256 bytes!
  Serial.print("Attempting to allocate EDGE MQTT buffer: ");
  Serial.print(MQTT_BUFFER_SIZE);
  Serial.println(" bytes");
  
  bool bufferSet = edgeMqttClient.setBufferSize(MQTT_BUFFER_SIZE);
  if (!bufferSet) {
    Serial.println("[WARNING] Failed to allocate EDGE MQTT buffer!");
    Serial.print("Requested size: ");
    Serial.print(MQTT_BUFFER_SIZE);
    Serial.println(" bytes");
    Serial.println("Transmission may fail with large chunks.");
  } else {
    Serial.print("✓ EDGE MQTT buffer allocated successfully: ");
    Serial.print(MQTT_BUFFER_SIZE);
    Serial.println(" bytes");
  }
  
  if (edgeMqttClient.connect(EDGE_MQTT_CLIENT_ID)) {
    edgeMqttConnected = true;
    Serial.println("✓ Connected to EDGE MQTT broker");
    
    // Set callback for receiving commands from EDGE
    edgeMqttClient.setCallback(edgeMqttCallback);
    
    // Subscribe to command topic to receive transmission requests (Fetch button)
    if (edgeMqttClient.subscribe(EDGE_MQTT_COMMAND_TOPIC, 1)) {
      Serial.print("✓ Subscribed to EDGE command topic: ");
      Serial.println(EDGE_MQTT_COMMAND_TOPIC);
    } else {
      Serial.println("[WARNING] Failed to subscribe to EDGE command topic");
    }
    
    // If we have ECG data ready, automatically start transmission
    // Only start if transmission hasn't been completed yet for this ECG data
    if (bufferedSamples > 0 && allChunksReceived && !transmissionActive && !transmissionCompleted) {
      Serial.println("========================================");
      Serial.println("[EDGE] ECG data available - starting automatic transmission...");
      startTransmissionToEDGE();
    }
    
    // Do NOT start transmission automatically - wait for command from EDGE
  } else {
    edgeMqttConnected = false;
    Serial.print("[ERROR] Failed to connect to EDGE broker, rc=");
    Serial.println(edgeMqttClient.state());
  }
}

void edgeMqttCallback(char* topic, byte* payload, unsigned int length) {
  // Handle commands from EDGE layer
  String topicStr = String(topic);
  
  if (topicStr == EDGE_MQTT_COMMAND_TOPIC) {
    // Parse command payload
    String command = "";
    for (unsigned int i = 0; i < length && i < 50; i++) {
      command += (char)payload[i];
    }
    command.trim();
    command.toUpperCase();
    
    Serial.print("[EDGE] Received command: ");
    Serial.println(command);
    
    if (command == "TRANSMIT" || command == "SEND" || command == "START") {
      // EDGE is requesting ECG data transmission
      if (bufferedSamples == 0) {
        Serial.println("[EDGE] No ECG data available to transmit");
        return;
      }
      
      if (transmissionActive) {
        Serial.println("[EDGE] Transmission already in progress");
        return;
      }
      
      // Reset completion flag to allow retransmission on manual request
      transmissionCompleted = false;
      
      Serial.println("========================================");
      Serial.println("[EDGE] Starting transmission on demand...");
      startTransmissionToEDGE();
    } else {
      Serial.print("[EDGE] Unknown command: ");
      Serial.println(command);
    }
  } else if (topicStr == EDGE_MQTT_ACK_TOPIC) {
    // Handle ACK from EDGE (optional - for future use)
    // Currently not used, but kept for compatibility
  }
}

void handleEDGEMQTTDisconnect() {
  static unsigned long lastAttempt = 0;
  static unsigned long lastDiscoveryAttempt = 0;
  
  // Clear broker IP to force fresh discovery on every disconnect
  edgeBrokerIP = "";
  
  // Always perform fresh discovery when disconnected (no storage or fallback)
  if (millis() - lastDiscoveryAttempt > 10000) {  // Try discovery every 10 seconds
    Serial.println("[INFO] EDGE MQTT disconnected - performing fresh broker discovery...");
    discoverEDGEBroker();
    lastDiscoveryAttempt = millis();
  }
  
  // Attempt reconnection only if broker was discovered
  if (millis() - lastAttempt > MQTT_RECONNECT_DELAY) {
    if (edgeBrokerIP.length() > 0) {
      Serial.println("[WARNING] EDGE MQTT disconnected. Attempting reconnect...");
      connectToEDGEMQTT();
    }
    lastAttempt = millis();
  }
}

void startTransmissionToEDGE() {
  if (bufferedSamples == 0) {
    Serial.println("[ERROR] No samples buffered - cannot start transmission");
    // Turn off LED - no data to transmit
    digitalWrite(LED_PIN, LOW);
    return;
  }
  
  if (transmissionActive) {
    Serial.println("[INFO] Transmission already active");
    // LED already ON - keep it ON
    return;
  }
  
  // Calculate transmission parameters
  // For on-demand transmission, we send chunks as fast as possible (with small delay)
  // No need to spread over N seconds - just send all chunks sequentially
  
  // Calculate chunk size for EDGE transmission
  // Use same chunk size as received from simulator (preserved in payloadChunkSize)
  // This ensures EDGE receives data in the same chunk size as ESP32 received it
  edgeChunkSize = payloadChunkSize > 0 ? payloadChunkSize : 30;
  
  Serial.print("[EDGE] Using chunk size: ");
  Serial.print(edgeChunkSize);
  Serial.println(" samples (same as received from simulator)");
  
  // Calculate total chunks for EDGE transmission
  uint16_t totalEdgeChunks = (bufferedSamples + edgeChunkSize - 1) / edgeChunkSize;
  
  transmissionActive = true;
  transmissionChunkIndex = 0;
  nextTransmissionTimeMs = millis();  // Start immediately
  
  // Turn on LED - EDGE transmission started
  digitalWrite(LED_PIN, HIGH);
  
  Serial.println("========================================");
  Serial.println("Starting transmission to EDGE layer (on-demand)...");
  Serial.print("Total samples: ");
  Serial.println(bufferedSamples);
  Serial.print("Duration: ");
  Serial.print((double)bufferedSamples / payloadSamplingRateHz, 2);
  Serial.println(" seconds");
  Serial.print("Chunk size: ");
  Serial.print(edgeChunkSize);
  Serial.println(" samples");
  Serial.print("Total chunks: ");
  Serial.println(totalEdgeChunks);
  Serial.println("========================================");
}

void transmitChunksToEDGE() {
  // Only check EDGE MQTT connection - simulator MQTT is NOT required for transmission
  if (!transmissionActive || bufferedSamples == 0) {
    // Turn off LED - transmission stopped or no data
    if (!transmissionActive) {
      digitalWrite(LED_PIN, LOW);
    }
    return;
  }
  
  // Check EDGE MQTT connection status
  if (!edgeMqttClient.connected()) {
    edgeMqttConnected = false;
    // Turn off LED - connection lost, transmission stopped
    digitalWrite(LED_PIN, LOW);
    // Don't return - let handleEDGEMQTTDisconnect() handle reconnection
    return;
  }
  
  // Update connection status
  edgeMqttConnected = true;
  
  unsigned long nowMs = millis();
  if (nowMs < nextTransmissionTimeMs) {
    return;  // Not time yet
  }
  
  // Calculate total chunks
  uint16_t totalEdgeChunks = (bufferedSamples + edgeChunkSize - 1) / edgeChunkSize;
  
  if (transmissionChunkIndex >= totalEdgeChunks) {
    // Completed transmission - stop completely
    // Mark transmission as completed so it won't restart automatically
    transmissionActive = false;
    transmissionCompleted = true;  // Mark as completed - won't restart until new ECG data
    transmissionChunkIndex = 0;
    
    // Turn off LED - EDGE transmission completed
    digitalWrite(LED_PIN, LOW);
    
    Serial.println("========================================");
    Serial.println("[EDGE] Transmission completed successfully!");
    Serial.print("Sent ");
    Serial.print(totalEdgeChunks);
    Serial.println(" chunks to EDGE layer.");
    Serial.println("Waiting for new ECG data from simulator...");
    Serial.println("(Transmission will start automatically when new ECG is received)");
    Serial.println("========================================");
    return;
  }
  
  // Build and send chunk
  uint32_t startIdx = transmissionChunkIndex * edgeChunkSize;
  uint32_t endIdx = min(startIdx + edgeChunkSize, bufferedSamples);
  uint32_t chunkSampleCount = endIdx - startIdx;
  
  // Build payload (same format as simulator)
  // Header: format_version(2) + sampling_rate(2) + chunk_num(2) + total_chunks(2) + sample_count(4)
  // Estimate payload size: header (12) + samples (each ~15 bytes: "-123.456789,")
  uint32_t estimatedPayloadSize = 12 + (chunkSampleCount * 15);
  if (estimatedPayloadSize > 32768) estimatedPayloadSize = 32768;  // Cap at max
  
  // Allocate payload buffer on heap to avoid stack overflow
  // Use a reasonable size that won't cause heap fragmentation
  uint8_t* payload = (uint8_t*)malloc(estimatedPayloadSize);
  if (!payload) {
    Serial.println("[ERROR] Failed to allocate memory for chunk payload");
    return;
  }
  
  uint32_t offset = 0;
  
  // Format version
  payload[offset++] = 3 & 0xFF;
  payload[offset++] = (3 >> 8) & 0xFF;
  
  // Sampling rate
  payload[offset++] = payloadSamplingRateHz & 0xFF;
  payload[offset++] = (payloadSamplingRateHz >> 8) & 0xFF;
  
  // Chunk number
  payload[offset++] = transmissionChunkIndex & 0xFF;
  payload[offset++] = (transmissionChunkIndex >> 8) & 0xFF;
  
  // Total chunks
  payload[offset++] = totalEdgeChunks & 0xFF;
  payload[offset++] = (totalEdgeChunks >> 8) & 0xFF;
  
  // Sample count
  payload[offset++] = chunkSampleCount & 0xFF;
  payload[offset++] = (chunkSampleCount >> 8) & 0xFF;
  payload[offset++] = (chunkSampleCount >> 16) & 0xFF;
  payload[offset++] = (chunkSampleCount >> 24) & 0xFF;
  
  // Build comma-separated float values directly in buffer (avoid String concatenation)
  // Use snprintf to write directly to buffer - much more memory efficient
  char* bodyPtr = (char*)(payload + offset);
  uint32_t remainingSpace = estimatedPayloadSize - offset;
  
  // Add patient info to chunk 0 (first chunk) - before ECG values
  if (transmissionChunkIndex == 0 && strlen(patientId) > 0) {
    // Format: "PATIENT_INFO:patient_id|DURATION:duration|SAMPLES:samples|DATE:date|TIME:time\n"
    int written = snprintf(bodyPtr, remainingSpace,
      "PATIENT_INFO:%s|DURATION:%.2f|SAMPLES:%lu|DATE:%s|TIME:%s\n",
      patientId, patientDurationSeconds, (unsigned long)patientTotalSamples,
      patientRecordDate, patientRecordTime);
    
    if (written > 0 && written < (int)remainingSpace) {
      bodyPtr += written;
      remainingSpace -= written;
    } else {
      Serial.println("[WARNING] Patient info too long, skipping");
    }
  }
  
  for (uint32_t i = startIdx; i < endIdx; i++) {
    if (i > startIdx) {
      if (remainingSpace > 1) {
        *bodyPtr++ = ',';
        remainingSpace--;
      } else {
        Serial.println("[ERROR] Payload buffer too small");
        free(payload);
        return;
      }
    }
    
    // Format float value directly into buffer
    int written = snprintf(bodyPtr, remainingSpace, "%.6f", ecgBuffer[i]);
    if (written < 0 || written >= (int)remainingSpace) {
      Serial.println("[ERROR] Payload buffer overflow");
      free(payload);
      return;
    }
    bodyPtr += written;
    remainingSpace -= written;
  }
  
  // Calculate actual payload size
  offset = (uint32_t)(bodyPtr - (char*)payload);
  
  // Ensure MQTT client is ready before publishing
  edgeMqttClient.loop();
  
  // Publish chunk (must be done before freeing payload)
  // Note: PubSubClient copies the payload internally, so we can free immediately after
  bool published = edgeMqttClient.publish(EDGE_MQTT_CHUNK_TOPIC, payload, offset, false);
  
  // Give MQTT client multiple chances to queue the message
  edgeMqttClient.loop();
  edgeMqttClient.loop();
  
  // Check if connection is still alive
  if (!edgeMqttClient.connected()) {
    Serial.print("[ERROR] EDGE MQTT connection lost during chunk ");
    Serial.println(transmissionChunkIndex);
    edgeMqttConnected = false;
    free(payload);
    return;
  }
  
  // Log result
  if (published) {
    Serial.print("[EDGE] Sent chunk ");
    Serial.print(transmissionChunkIndex + 1);
    Serial.print("/");
    Serial.print(totalEdgeChunks);
    Serial.print(" (");
    Serial.print(chunkSampleCount);
    Serial.print(" samples, ");
    Serial.print(offset);
    Serial.println(" bytes)");
  } else {
    Serial.print("[ERROR] Failed to send chunk ");
    Serial.print(transmissionChunkIndex);
    Serial.print(" (state=");
    Serial.print(edgeMqttClient.state());
    Serial.println(")");
  }
  
  // Free allocated memory (only once!)
  free(payload);
  
  transmissionChunkIndex++;
  
  // If this was the last chunk, stop transmission completely
  if (transmissionChunkIndex >= totalEdgeChunks) {
    transmissionActive = false;
    transmissionCompleted = true;  // Mark as completed - won't restart until new ECG data
    
    // Turn off LED - EDGE transmission completed
    digitalWrite(LED_PIN, LOW);
    
    Serial.println("========================================");
    Serial.println("[EDGE] Transmission completed successfully!");
    Serial.print("Sent ");
    Serial.print(totalEdgeChunks);
    Serial.println(" chunks to EDGE layer.");
    Serial.println("Waiting for next transmission request...");
    Serial.println("========================================");
  } else {
    // Send next chunk with a small delay to prevent overwhelming the system
    nextTransmissionTimeMs = nowMs + 50;  // 50ms delay between chunks (allows WiFi/MQTT stack to process)
  }
}

// ============================================================================
// PERSISTENT STORAGE FUNCTIONS
// ============================================================================

void saveECGDataToFlash() {
  if (bufferedSamples == 0) {
    return;  // Nothing to save
  }
  
  Serial.println("[STORAGE] Saving ECG data to flash...");
  
  // Save metadata first (sample count and sampling rate)
  File metaFile = LittleFS.open(ECG_METADATA_FILE, "w");
  if (!metaFile) {
    Serial.println("[ERROR] Failed to open metadata file for writing");
    return;
  }
  
  // Write metadata: bufferedSamples (uint32_t) + payloadSamplingRateHz (uint16_t) + payloadChunkSize (uint32_t)
  metaFile.write((uint8_t*)&bufferedSamples, sizeof(uint32_t));
  metaFile.write((uint8_t*)&payloadSamplingRateHz, sizeof(uint16_t));
  metaFile.write((uint8_t*)&payloadChunkSize, sizeof(uint32_t));
  metaFile.close();
  
  // Save ECG buffer data
  File dataFile = LittleFS.open(ECG_DATA_FILE, "w");
  if (!dataFile) {
    Serial.println("[ERROR] Failed to open ECG data file for writing");
    return;
  }
  
  // Write all ECG samples as binary float array
  size_t bytesWritten = dataFile.write((uint8_t*)ecgBuffer, bufferedSamples * sizeof(float));
  dataFile.close();
  
  if (bytesWritten == bufferedSamples * sizeof(float)) {
    Serial.print("[STORAGE] ✓ Saved ");
    Serial.print(bufferedSamples);
    Serial.print(" samples (");
    Serial.print(bytesWritten);
    Serial.println(" bytes) to flash");
  } else {
    Serial.print("[ERROR] Failed to save all data. Wrote ");
    Serial.print(bytesWritten);
    Serial.print(" bytes, expected ");
    Serial.println(bufferedSamples * sizeof(float));
  }
}

void loadSavedECGData() {
  // Check if metadata file exists
  if (!LittleFS.exists(ECG_METADATA_FILE)) {
    Serial.println("[STORAGE] No saved ECG data found");
    return;
  }
  
  Serial.println("[STORAGE] Found saved ECG data, loading...");
  
  // Load metadata
  File metaFile = LittleFS.open(ECG_METADATA_FILE, "r");
  if (!metaFile) {
    Serial.println("[ERROR] Failed to open metadata file for reading");
    return;
  }
  
  uint32_t savedSamples = 0;
  uint16_t savedSamplingRate = 0;
  uint32_t savedChunkSize = 0;
  
  // Try to read chunk size (for backward compatibility, check file size)
  size_t fileSize = metaFile.size();
  bool hasChunkSize = (fileSize >= sizeof(uint32_t) + sizeof(uint16_t) + sizeof(uint32_t));
  
  if (metaFile.read((uint8_t*)&savedSamples, sizeof(uint32_t)) != sizeof(uint32_t) ||
      metaFile.read((uint8_t*)&savedSamplingRate, sizeof(uint16_t)) != sizeof(uint16_t)) {
    Serial.println("[ERROR] Failed to read metadata");
    metaFile.close();
    return;
  }
  
  // Read chunk size if available (for backward compatibility)
  if (hasChunkSize) {
    if (metaFile.read((uint8_t*)&savedChunkSize, sizeof(uint32_t)) != sizeof(uint32_t)) {
      savedChunkSize = 0;  // Default if read fails
    }
  } else {
    savedChunkSize = 0;  // Old format - will use default
  }
  metaFile.close();
  
  // Validate metadata
  if (savedSamples == 0 || savedSamples > MAX_ECG_SAMPLES) {
    Serial.print("[ERROR] Invalid sample count in saved data: ");
    Serial.println(savedSamples);
    return;
  }
  
  // Load ECG buffer data
  File dataFile = LittleFS.open(ECG_DATA_FILE, "r");
  if (!dataFile) {
    Serial.println("[ERROR] Failed to open ECG data file for reading");
    return;
  }
  
  size_t bytesRead = dataFile.read((uint8_t*)ecgBuffer, savedSamples * sizeof(float));
  dataFile.close();
  
  if (bytesRead != savedSamples * sizeof(float)) {
    Serial.print("[ERROR] Failed to read all data. Read ");
    Serial.print(bytesRead);
    Serial.print(" bytes, expected ");
    Serial.println(savedSamples * sizeof(float));
    return;
  }
  
  // Restore state
  bufferedSamples = savedSamples;
  payloadSamplingRateHz = (savedSamplingRate > 0) ? savedSamplingRate : ECG_SAMPLING_RATE;
  payloadChunkSize = savedChunkSize;  // Restore chunk size from flash
  transmissionChunkIndex = 0;
  transmissionActive = false;  // Will be started automatically after EDGE connection is established
  allChunksReceived = true;
  
  Serial.println("========================================");
  Serial.println("✓ Loaded saved ECG data from flash!");
  Serial.print("Total samples: ");
  Serial.println(bufferedSamples);
  Serial.print("Sampling rate: ");
  Serial.print(payloadSamplingRateHz);
  Serial.println(" Hz");
  Serial.print("Chunk size: ");
  Serial.print(payloadChunkSize);
  Serial.println(" samples");
  Serial.print("Duration: ");
  Serial.print((double)bufferedSamples / payloadSamplingRateHz, 2);
  Serial.println(" s");
  Serial.println("Transmission will start automatically after EDGE connection is established.");
  Serial.println("========================================");
}

void clearSavedECGData() {
  Serial.println("[STORAGE] Clearing saved ECG data from flash...");
  
  bool success = true;
  if (LittleFS.exists(ECG_METADATA_FILE)) {
    if (!LittleFS.remove(ECG_METADATA_FILE)) {
      Serial.println("[ERROR] Failed to remove metadata file");
      success = false;
    }
  }
  
  if (LittleFS.exists(ECG_DATA_FILE)) {
    if (!LittleFS.remove(ECG_DATA_FILE)) {
      Serial.println("[ERROR] Failed to remove ECG data file");
      success = false;
    }
  }
  
  if (success) {
    Serial.println("✓ Saved ECG data cleared from flash");
  } else {
    Serial.println("✗ Some files could not be removed");
  }
}

