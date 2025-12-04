#!/usr/bin/env python3
"""
Simple MQTT subscriber for testing ECG data streaming.
Run this in a separate terminal while the simulator is running.
"""

import json
import paho.mqtt.client as mqtt
from datetime import datetime


def on_connect(client, userdata, flags, rc):
    """Callback when connected to MQTT broker."""
    if rc == 0:
        print("✓ Connected to MQTT broker")
        print("  Subscribing to topic: ecg/raw")
        client.subscribe("ecg/raw")
        print("  Waiting for messages...\n")
    else:
        print(f"✗ Connection failed with code {rc}")


def on_disconnect(client, userdata, rc):
    """Callback when disconnected from MQTT broker."""
    if rc != 0:
        print(f"✗ Unexpected disconnection (code {rc})")


def on_message(client, userdata, msg):
    """Callback when message received."""
    try:
        # Parse JSON payload
        data = json.loads(msg.payload.decode())
        
        # Extract key information
        timestamp = datetime.fromtimestamp(data['timestamp'] / 1000.0)
        patient_id = data['patient_id']
        window = data['window']
        ecg_samples = data['ecg']
        sampling_rate = data['fs']
        rhythm = data.get('rhythm', 'Unknown')
        
        # Calculate statistics
        min_val = min(ecg_samples)
        max_val = max(ecg_samples)
        mean_val = sum(ecg_samples) / len(ecg_samples)
        
        # Print formatted output
        print(f"[{timestamp.strftime('%H:%M:%S')}] Window #{window:04d}")
        print(f"  Patient: {patient_id}")
        print(f"  Rhythm: {rhythm}")
        print(f"  Samples: {len(ecg_samples)} @ {sampling_rate} Hz")
        print(f"  Range: [{min_val:.3f}, {max_val:.3f}] mV")
        print(f"  Mean: {mean_val:.3f} mV")
        print()
        
    except json.JSONDecodeError as e:
        print(f"✗ JSON decode error: {e}")
    except KeyError as e:
        print(f"✗ Missing key in payload: {e}")
    except Exception as e:
        print(f"✗ Error processing message: {e}")


def main():
    """Main function to run MQTT subscriber."""
    print("=" * 60)
    print("ECG Data MQTT Subscriber - Test Tool")
    print("=" * 60)
    print()
    
    # Configuration
    broker = "localhost"
    port = 1883
    
    print(f"Configuration:")
    print(f"  Broker: {broker}")
    print(f"  Port: {port}")
    print(f"  Topic: ecg/raw")
    print()
    
    # Create MQTT client
    client = mqtt.Client(mqtt.CallbackAPIVersion.VERSION1)
    
    # Set callbacks
    client.on_connect = on_connect
    client.on_disconnect = on_disconnect
    client.on_message = on_message
    
    try:
        # Connect to broker
        print("Connecting to MQTT broker...")
        client.connect(broker, port, 60)
        
        # Start loop (blocking)
        client.loop_forever()
        
    except KeyboardInterrupt:
        print("\n\n✓ Subscriber stopped by user")
    except ConnectionRefusedError:
        print(f"\n✗ Connection refused. Is MQTT broker running on {broker}:{port}?")
        print("\nTo start Mosquitto:")
        print("  macOS:  brew services start mosquitto")
        print("  Linux:  sudo systemctl start mosquitto")
    except Exception as e:
        print(f"\n✗ Error: {e}")
    finally:
        client.disconnect()
        print("\n✓ Disconnected from broker")


if __name__ == "__main__":
    main()

