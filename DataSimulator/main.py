"""
ECG Simulator Application Entry Point
Includes embedded MQTT broker - no external setup needed!
"""

import sys
from PyQt6.QtWidgets import QApplication
from ecg_gui import ECGSimulatorApp
from mqtt_broker import EmbeddedMQTTBroker


if __name__ == "__main__":
    print("╔══════════════════════════════════════════════════════════╗")
    print("║          ECG Simulator - Starting Application           ║")
    print("╚══════════════════════════════════════════════════════════╝")
    print()
    
    # Start GUI application
    print("🚀 Launching GUI application...")
    print("ℹ️  Use the 'Embedded MQTT Broker' panel to start/stop the broker")
    print()
    
    app = QApplication(sys.argv)
    window = ECGSimulatorApp()
    window.show()
    
    # Run application (broker is controlled via GUI)
    exit_code = app.exec()
    
    sys.exit(exit_code)

