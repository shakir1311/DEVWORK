"""
ECG Simulator GUI
PyQt6 GUI for user interaction with the ECG simulator.
"""

import logging
import socket
from datetime import datetime
from typing import Optional
from collections import deque
import numpy as np
from PyQt6.QtWidgets import (
    QMainWindow, QWidget, QVBoxLayout, QHBoxLayout, QGridLayout, QScrollArea,
    QGroupBox, QLabel, QPushButton, QComboBox, QSlider, QSpinBox,
    QDoubleSpinBox, QTextEdit, QProgressBar, QCheckBox, QLineEdit,
    QMessageBox, QDialog
)
from PyQt6.QtCore import Qt, pyqtSlot
from PyQt6.QtGui import QPalette, QColor
import pyqtgraph as pg

from app_controller import SimulationController
from dataset_downloader import DatasetDownloadWorker
from mqtt_broker import EmbeddedMQTTBroker
from batch_simulator import BatchSimulatorWorker
import config

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)


class ECGPlotWindow(QDialog):
    """Separate window for full ECG record plotting with scroll/zoom."""
    
    def __init__(self, parent=None):
        """Initialize the ECG plot window."""
        super().__init__(parent)
        
        self.setWindowTitle("ECG Waveform - Full Record")
        self.setMinimumSize(1200, 700)
        
        # Full ECG record storage (not rolling buffer)
        self.ecg_data = np.array([])  # Full ECG voltage array
        self.time_data = np.array([])  # Full timestamp array (in seconds from start)
        self.start_timestamp_ms = 0  # Recording start timestamp (Unix ms)
        self.sampling_rate = 300  # Hz
        self.duration_seconds = 0.0
        
        # Time window navigation
        self.visible_time_window = 2.0  # Seconds visible in view (default: 2 seconds)
        self.current_time_position = 0.0  # Current time position (seconds from start)
        
        # Setup UI
        self.init_ui()
    
    def init_ui(self):
        """Initialize the plot window UI."""
        layout = QVBoxLayout(self)
        
        # Configure pyqtgraph for better performance
        pg.setConfigOptions(antialias=True)
        
        # Create plot widget with pan/zoom enabled
        self.plot_widget = pg.PlotWidget()
        self.plot_widget.setBackground('w')  # White background (ECG paper style)
        self.plot_widget.setLabel('left', 'Amplitude', units='mV')
        self.plot_widget.setLabel('bottom', 'Time', units='s')
        self.plot_widget.setTitle('ECG Waveform - Full Record', size='14pt', bold=True)
        self.plot_widget.showGrid(x=True, y=True, alpha=0.3)
        
        # Enable pan/zoom (default in pyqtgraph, but ensure it's enabled)
        self.plot_widget.setMouseEnabled(x=True, y=True)  # Enable pan
        self.plot_widget.showButtons()  # Show auto-range buttons
        
        # Create plot line (ECG-style: blue/black line)
        pen = pg.mkPen(color=(0, 0, 0), width=1.5)  # Black line (ECG paper style)
        self.plot_line = self.plot_widget.plot([], [], pen=pen)
        
        layout.addWidget(self.plot_widget)
        
        # Plot controls
        controls_layout = QHBoxLayout()
        
        # Patient info label
        self.patient_info_label = QLabel("No patient selected")
        self.patient_info_label.setStyleSheet("font-weight: bold; color: #0064c8;")
        controls_layout.addWidget(self.patient_info_label)
        
        # Duration/record info
        self.record_info_label = QLabel("")
        self.record_info_label.setStyleSheet("color: #666;")
        controls_layout.addWidget(self.record_info_label)
        
        controls_layout.addStretch()
        
        # Time window control (horizontal resolution)
        controls_layout.addWidget(QLabel("Time Window:"))
        self.time_window_spinbox = QDoubleSpinBox()
        self.time_window_spinbox.setRange(0.5, 60.0)
        self.time_window_spinbox.setValue(2.0)
        self.time_window_spinbox.setSingleStep(0.5)
        self.time_window_spinbox.setSuffix(" s")
        self.time_window_spinbox.setToolTip("Visible time window in seconds (horizontal resolution)")
        self.time_window_spinbox.valueChanged.connect(self.on_time_window_changed)
        controls_layout.addWidget(self.time_window_spinbox)
        
        controls_layout.addWidget(QLabel("|"))
        
        # Navigation controls
        self.scroll_left_btn = QPushButton("◄")
        self.scroll_left_btn.setToolTip("Scroll left (or drag with mouse)")
        self.scroll_left_btn.clicked.connect(self.scroll_left)
        controls_layout.addWidget(self.scroll_left_btn)
        
        self.scroll_right_btn = QPushButton("►")
        self.scroll_right_btn.setToolTip("Scroll right (or drag with mouse)")
        self.scroll_right_btn.clicked.connect(self.scroll_right)
        controls_layout.addWidget(self.scroll_right_btn)
        
        self.jump_start_btn = QPushButton("⏮")
        self.jump_start_btn.setToolTip("Jump to start of ECG")
        self.jump_start_btn.clicked.connect(self.jump_to_start)
        controls_layout.addWidget(self.jump_start_btn)
        
        self.jump_end_btn = QPushButton("⏭")
        self.jump_end_btn.setToolTip("Jump to end of ECG")
        self.jump_end_btn.clicked.connect(self.jump_to_end)
        controls_layout.addWidget(self.jump_end_btn)
        
        controls_layout.addWidget(QLabel("|"))
        
        # Zoom controls (vertical only - horizontal uses time window)
        controls_layout.addWidget(QLabel("Vertical Zoom:"))
        self.zoom_in_btn = QPushButton("🔍+")
        self.zoom_in_btn.setToolTip("Zoom in vertically (amplitude)")
        self.zoom_in_btn.clicked.connect(self.zoom_in_vertical)
        controls_layout.addWidget(self.zoom_in_btn)
        
        self.zoom_out_btn = QPushButton("🔍-")
        self.zoom_out_btn.setToolTip("Zoom out vertically (amplitude)")
        self.zoom_out_btn.clicked.connect(self.zoom_out_vertical)
        controls_layout.addWidget(self.zoom_out_btn)
        
        self.fit_btn = QPushButton("Fit All")
        self.fit_btn.setToolTip("Fit entire ECG record to view")
        self.fit_btn.clicked.connect(self.fit_to_view)
        controls_layout.addWidget(self.fit_btn)
        
        controls_layout.addWidget(QLabel("|"))
        
        self.clear_btn = QPushButton("Clear")
        self.clear_btn.clicked.connect(self.clear_plot)
        controls_layout.addWidget(self.clear_btn)
        
        layout.addLayout(controls_layout)
        
        # Instructions label
        instructions = QLabel(
            "💡 <b>Controls:</b> "
            "Mouse drag = Pan | Mouse wheel = Zoom | "
            "Right-click = Context menu | "
            "Double-click = Auto-range"
        )
        instructions.setStyleSheet("color: #666; font-size: 10pt; padding: 5px;")
        layout.addWidget(instructions)
    
    def set_patient_info(self, patient_id: str, rhythm: str = "", start_timestamp_ms: int = 0):
        """Update the patient information display."""
        info = f"Patient: {patient_id}"
        if rhythm:
            info += f" | Rhythm: {rhythm}"
        self.patient_info_label.setText(info)
        self.start_timestamp_ms = start_timestamp_ms
    
    def load_full_record(self, ecg_samples: list, sampling_rate: int = 300, 
                        start_timestamp_ms: int = 0, record_time: str = ""):
        """
        Load the full ECG record for display.
        
        Args:
            ecg_samples: List of all ECG voltage values (full record)
            sampling_rate: Sampling rate in Hz
            start_timestamp_ms: Recording start timestamp (Unix milliseconds)
            record_time: Recording time string from .hea file (optional)
        """
        try:
            if not ecg_samples or len(ecg_samples) == 0:
                return
            
            # Store full record
            self.ecg_data = np.array(ecg_samples, dtype=np.float32)
            self.sampling_rate = sampling_rate
            self.start_timestamp_ms = start_timestamp_ms
            
            # Calculate time array (seconds from start)
            num_samples = len(self.ecg_data)
            dt = 1.0 / sampling_rate
            self.time_data = np.arange(num_samples) * dt
            self.duration_seconds = num_samples * dt
            
            # Update record info
            if record_time:
                info = f"Duration: {self.duration_seconds:.1f}s | {num_samples} samples @ {sampling_rate}Hz | Time: {record_time}"
            else:
                info = f"Duration: {self.duration_seconds:.1f}s | {num_samples} samples @ {sampling_rate}Hz"
            self.record_info_label.setText(info)
            
            # Update plot
            self.plot_line.setData(self.time_data, self.ecg_data)
            
            # Set Y-axis range based on data (with some margin)
            if len(self.ecg_data) > 0:
                y_min = np.min(self.ecg_data)
                y_max = np.max(self.ecg_data)
                y_margin = (y_max - y_min) * 0.1 if (y_max - y_min) > 0 else 0.5
                self.plot_widget.setYRange(y_min - y_margin, y_max + y_margin)
            
            # Reset time position to start
            self.current_time_position = 0.0
            
            # Apply current time window view
            self.apply_time_window()
            
            # Auto-fit Y-axis based on data (with some margin)
            if len(self.ecg_data) > 0:
                y_min = np.min(self.ecg_data)
                y_max = np.max(self.ecg_data)
                y_margin = (y_max - y_min) * 0.1 if (y_max - y_min) > 0 else 0.5
                self.plot_widget.setYRange(y_min - y_margin, y_max + y_margin)
            
            logger.info(f"Loaded full ECG record: {num_samples} samples, {self.duration_seconds:.1f}s")
        
        except Exception as e:
            logger.error(f"Error loading full record: {str(e)}")
    
    def update_plot(self, ecg_samples: list, sampling_rate: int = 300):
        """
        Update the ECG plot with new data (replaces old data).
        This is called when a new full record is published.
        
        Args:
            ecg_samples: List of ECG voltage values (full record)
            sampling_rate: Sampling rate in Hz
        """
        # For now, treat as full record replacement
        # In the future, this could append if needed
        self.load_full_record(ecg_samples, sampling_rate, self.start_timestamp_ms)
    
    def clear_plot(self):
        """Clear the ECG plot."""
        self.ecg_data = np.array([])
        self.time_data = np.array([])
        self.start_timestamp_ms = 0
        self.duration_seconds = 0.0
        self.plot_line.setData([], [])
        self.record_info_label.setText("")
    
    def apply_time_window(self):
        """Apply the current time window setting to the view (horizontal only, preserves vertical scale)."""
        if len(self.time_data) == 0:
            return
        
        # Ensure time position is within bounds
        max_position = max(0, self.duration_seconds - self.visible_time_window)
        self.current_time_position = max(0, min(self.current_time_position, max_position))
        
        # Set X range based on time window (horizontal resolution)
        x_min = self.current_time_position
        x_max = min(self.current_time_position + self.visible_time_window, self.duration_seconds)
        
        # Get current Y range to preserve it
        current_y_range = self.plot_widget.viewRange()[1]
        
        # Update only X range, keep Y range unchanged
        self.plot_widget.setXRange(x_min, x_max)
        self.plot_widget.setYRange(current_y_range[0], current_y_range[1])
    
    def on_time_window_changed(self, value: float):
        """Handle time window spinbox change."""
        self.visible_time_window = value
        self.apply_time_window()
    
    def fit_to_view(self):
        """Fit the entire ECG record to the view."""
        if len(self.time_data) > 0:
            self.plot_widget.setXRange(self.time_data[0], self.time_data[-1])
            if len(self.ecg_data) > 0:
                y_min = np.min(self.ecg_data)
                y_max = np.max(self.ecg_data)
                y_margin = (y_max - y_min) * 0.1 if (y_max - y_min) > 0 else 0.5
                self.plot_widget.setYRange(y_min - y_margin, y_max + y_margin)
            # Reset time position
            self.current_time_position = 0.0
    
    def zoom_in_vertical(self):
        """Zoom in vertically only (amplitude), preserving horizontal time window."""
        view_range = self.plot_widget.viewRange()
        y_range = view_range[1]
        y_center = (y_range[0] + y_range[1]) / 2
        y_width = (y_range[1] - y_range[0]) / 2
        
        # Zoom in by 50% (vertical only)
        self.plot_widget.setYRange(y_center - y_width * 0.5, y_center + y_width * 0.5)
        # Preserve X range
        self.apply_time_window()
    
    def zoom_out_vertical(self):
        """Zoom out vertically only (amplitude), preserving horizontal time window."""
        view_range = self.plot_widget.viewRange()
        y_range = view_range[1]
        y_center = (y_range[0] + y_range[1]) / 2
        y_width = (y_range[1] - y_range[0]) / 2
        
        # Zoom out by 50% (vertical only)
        self.plot_widget.setYRange(y_center - y_width * 2, y_center + y_width * 2)
        # Preserve X range
        self.apply_time_window()
    
    def scroll_left(self):
        """Scroll the view left by one time window."""
        if len(self.time_data) == 0:
            return
        
        # Move back by the time window (or remaining if less)
        scroll_amount = min(self.visible_time_window, self.current_time_position)
        self.current_time_position = max(0, self.current_time_position - scroll_amount)
        self.apply_time_window()
    
    def scroll_right(self):
        """Scroll the view right by one time window."""
        if len(self.time_data) == 0:
            return
        
        # Move forward by the time window
        max_position = max(0, self.duration_seconds - self.visible_time_window)
        scroll_amount = min(self.visible_time_window, max_position - self.current_time_position)
        self.current_time_position = min(max_position, self.current_time_position + scroll_amount)
        self.apply_time_window()
    
    def jump_to_start(self):
        """Jump to the start of the ECG."""
        self.current_time_position = 0.0
        self.apply_time_window()
    
    def jump_to_end(self):
        """Jump to the end of the ECG."""
        if len(self.time_data) > 0:
            self.current_time_position = max(0, self.duration_seconds - self.visible_time_window)
            self.apply_time_window()


class ECGSimulatorApp(QMainWindow):
    """Main application window for ECG simulator."""
    
    def __init__(self):
        """Initialize the GUI application."""
        super().__init__()
        
        self.setWindowTitle("ECG Simulator - CinC 2017 Dataset")
        self.setMinimumSize(1200, 800)
        
        # Create controller
        self.controller: Optional[SimulationController] = None
        self.mqtt_connected = False
        self.dataset_worker = None
        
        # Embedded MQTT broker
        self.embedded_broker: Optional['EmbeddedMQTTBroker'] = None
        
        # ECG plot window (separate window)
        self.plot_window: Optional[ECGPlotWindow] = None
        
        # Setup UI
        self.init_ui()
        
        # Initial state
        self.update_button_states("disconnected")
        
        # Check dataset status and update UI
        self.update_dataset_status()
        # Batch simulator worker
        self.batch_worker: Optional[BatchSimulatorWorker] = None
        
        logger.info("GUI initialized")
    
    def init_ui(self):
        """Initialize the user interface."""
        # Menu bar (optional - download is in main GUI now)
        # Can add other menu items here if needed
        
        # Create scroll area for the main content
        scroll_area = QScrollArea()
        scroll_area.setWidgetResizable(True)
        scroll_area.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAsNeeded)
        scroll_area.setVerticalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAsNeeded)
        
        # Content widget that will be scrollable
        content_widget = QWidget()
        scroll_area.setWidget(content_widget)
        self.setCentralWidget(scroll_area)
        
        # Main layout for content widget
        main_layout = QVBoxLayout(content_widget)
        main_layout.setSpacing(15)  # Add spacing between panels
        main_layout.setContentsMargins(15, 15, 15, 15)  # Add margins
        
        # Top section: Dataset Status
        dataset_panel = self.create_dataset_panel()
        main_layout.addWidget(dataset_panel)
        
        # MQTT Broker Control
        broker_panel = self.create_broker_panel()
        main_layout.addWidget(broker_panel)
        
        # MQTT Connection
        mqtt_panel = self.create_mqtt_panel()
        main_layout.addWidget(mqtt_panel)
        
        # Middle section: Patient Selection and Parameters
        middle_layout = QHBoxLayout()
        middle_layout.setSpacing(15)
        
        patient_panel = self.create_patient_panel()
        middle_layout.addWidget(patient_panel, 1)
        
        params_panel = self.create_parameters_panel()
        middle_layout.addWidget(params_panel, 1)
        
        main_layout.addLayout(middle_layout)
        
        # Control buttons
        control_panel = self.create_control_panel()
        main_layout.addWidget(control_panel)
        
        # Batch Experiment panel
        batch_panel = self.create_batch_experiment_panel()
        main_layout.addWidget(batch_panel)
        
        # Status and progress
        status_panel = self.create_status_panel()
        main_layout.addWidget(status_panel)
        
        # Error log
        log_panel = self.create_log_panel()
        main_layout.addWidget(log_panel)
        
        # Add stretch at the end to push everything to the top
        main_layout.addStretch()
    
    def create_broker_panel(self) -> QGroupBox:
        """Create embedded MQTT broker control panel."""
        group = QGroupBox("Embedded MQTT Broker")
        layout = QHBoxLayout()
        
        # Broker IP address - Dropdown with all available IPs
        layout.addWidget(QLabel("Listen IP:"))
        self.broker_ip_combo = QComboBox()
        self.broker_ip_combo.setEditable(True)  # Allow manual entry as fallback
        self.broker_ip_combo.setMaximumWidth(180)
        self.broker_ip_combo.setToolTip("Select IP address to bind broker to. Click refresh button to update list.")
        # Populate with available IPs
        self.refresh_ip_list()
        layout.addWidget(self.broker_ip_combo)
        
        # Refresh button to update IP list
        refresh_ip_btn = QPushButton("🔄")
        refresh_ip_btn.setMaximumWidth(30)
        refresh_ip_btn.setToolTip("Refresh list of available IP addresses")
        refresh_ip_btn.clicked.connect(self.refresh_ip_list)
        layout.addWidget(refresh_ip_btn)
        
        # Broker port
        layout.addWidget(QLabel("Port:"))
        self.broker_port_input = QSpinBox()
        self.broker_port_input.setRange(1, 65535)
        self.broker_port_input.setValue(1883)
        self.broker_port_input.setMaximumWidth(80)
        layout.addWidget(self.broker_port_input)
        
        # Start/Stop broker button
        self.broker_start_btn = QPushButton("Start Broker")
        self.broker_start_btn.clicked.connect(self.on_start_broker_clicked)
        layout.addWidget(self.broker_start_btn)
        
        self.broker_stop_btn = QPushButton("Stop Broker")
        self.broker_stop_btn.clicked.connect(self.on_stop_broker_clicked)
        self.broker_stop_btn.setEnabled(False)
        layout.addWidget(self.broker_stop_btn)
        
        # Broker status indicator
        self.broker_status_indicator = QLabel("●")
        self.broker_status_indicator.setStyleSheet("color: red; font-size: 20px;")
        layout.addWidget(self.broker_status_indicator)
        
        self.broker_status_text = QLabel("Stopped")
        layout.addWidget(self.broker_status_text)
        
        layout.addStretch()
        
        group.setLayout(layout)
        return group
    
    def refresh_ip_list(self):
        """Refresh the list of available IP addresses."""
        try:
            # Get all IP addresses
            ip_addresses = self.get_all_ip_addresses()
            
            # Store current selection
            current_text = self.broker_ip_combo.currentText()
            
            # Clear and repopulate
            self.broker_ip_combo.clear()
            
            # Add all IPs
            for ip in ip_addresses:
                self.broker_ip_combo.addItem(ip)
            
            # Restore previous selection if it still exists, otherwise select first
            index = self.broker_ip_combo.findText(current_text)
            if index >= 0:
                self.broker_ip_combo.setCurrentIndex(index)
            elif self.broker_ip_combo.count() > 0:
                # Default to localhost if available, otherwise first item
                localhost_index = self.broker_ip_combo.findText("127.0.0.1")
                if localhost_index >= 0:
                    self.broker_ip_combo.setCurrentIndex(localhost_index)
                else:
                    self.broker_ip_combo.setCurrentIndex(0)
        except Exception as e:
            logger.error(f"Error refreshing IP list: {str(e)}")
            # Fallback: just add localhost
            if self.broker_ip_combo.count() == 0:
                self.broker_ip_combo.addItem("127.0.0.1")
    
    def get_all_ip_addresses(self):
        """Get all IP addresses assigned to this computer."""
        ip_addresses = []
        
        try:
            # Get hostname
            hostname = socket.gethostname()
            
            # Get all IPs associated with hostname
            try:
                host_ips = socket.gethostbyname_ex(hostname)[2]
                ip_addresses.extend(host_ips)
            except socket.gaierror:
                pass
            
            # Also get IPs from all network interfaces
            # This works on both Unix-like systems and Windows
            import platform
            system = platform.system()
            
            if system == "Windows":
                # Windows: use socket.getaddrinfo
                try:
                    # Get all interfaces
                    for interface in socket.if_nameindex() if hasattr(socket, 'if_nameindex') else []:
                        pass  # Windows doesn't have if_nameindex
                    
                    # Alternative: connect to external address to get local IP
                    s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
                    try:
                        # Connect to external address (doesn't actually connect)
                        s.connect(("8.8.8.8", 80))
                        local_ip = s.getsockname()[0]
                        if local_ip not in ip_addresses:
                            ip_addresses.append(local_ip)
                    except Exception:
                        pass
                    finally:
                        s.close()
                except Exception:
                    pass
            else:
                # Unix-like (Linux, macOS): use netifaces if available, otherwise socket
                try:
                    import netifaces
                    for interface in netifaces.interfaces():
                        addrs = netifaces.ifaddresses(interface)
                        if netifaces.AF_INET in addrs:
                            for addr_info in addrs[netifaces.AF_INET]:
                                ip = addr_info.get('addr')
                                if ip and ip not in ip_addresses:
                                    ip_addresses.append(ip)
                except ImportError:
                    # Fallback: use socket method
                    try:
                        # Connect to external address to get local IP
                        s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
                        try:
                            s.connect(("8.8.8.8", 80))
                            local_ip = s.getsockname()[0]
                            if local_ip not in ip_addresses:
                                ip_addresses.append(local_ip)
                        except Exception:
                            pass
                        finally:
                            s.close()
                    except Exception:
                        pass
            
            # Always include localhost
            if "127.0.0.1" not in ip_addresses:
                ip_addresses.insert(0, "127.0.0.1")
            
            # Add 0.0.0.0 option (listen on all interfaces)
            if "0.0.0.0" not in ip_addresses:
                ip_addresses.insert(0, "0.0.0.0")
            
            # Remove duplicates and sort (0.0.0.0 first, then local network IP, then localhost)
            seen = set()
            unique_ips = []
            
            # 1. 0.0.0.0 is best for discovery (listens on all interfaces)
            if "0.0.0.0" in ip_addresses:
                unique_ips.append("0.0.0.0")
                seen.add("0.0.0.0")
            
            # 2. Add other non-localhost IPs
            for ip in ip_addresses:
                if ip not in seen and not ip.startswith("127."):
                    unique_ips.append(ip)
                    seen.add(ip)
            
            # 3. Add localhost last
            if "127.0.0.1" in ip_addresses and "127.0.0.1" not in seen:
                unique_ips.append("127.0.0.1")
                seen.add("127.0.0.1")
            
            return unique_ips
            
        except Exception as e:
            logger.error(f"Error getting IP addresses: {str(e)}")
            # Fallback
            return ["0.0.0.0", "127.0.0.1"]
    
    def create_mqtt_panel(self) -> QGroupBox:
        """Create MQTT connection panel."""
        group = QGroupBox("MQTT Connection")
        layout = QHBoxLayout()
        
        # Broker address
        layout.addWidget(QLabel("Broker:"))
        self.broker_input = QLineEdit(config.MQTT_BROKER)
        self.broker_input.setMaximumWidth(150)
        layout.addWidget(self.broker_input)
        
        # Port
        layout.addWidget(QLabel("Port:"))
        self.port_input = QSpinBox()
        self.port_input.setRange(1, 65535)
        self.port_input.setValue(config.MQTT_PORT)
        self.port_input.setMaximumWidth(80)
        layout.addWidget(self.port_input)
        
        # Topic (read-only display)
        layout.addWidget(QLabel("Topic:"))
        topic_label = QLabel(config.MQTT_TOPIC)
        topic_label.setStyleSheet("color: #666; font-style: italic;")
        topic_label.setToolTip("ECG data topic (fixed)")
        layout.addWidget(topic_label)
        
        # Connect/Disconnect buttons
        self.connect_btn = QPushButton("Connect")
        self.connect_btn.clicked.connect(self.on_connect_clicked)
        layout.addWidget(self.connect_btn)
        
        self.disconnect_btn = QPushButton("Disconnect")
        self.disconnect_btn.clicked.connect(self.on_disconnect_clicked)
        self.disconnect_btn.setEnabled(False)
        layout.addWidget(self.disconnect_btn)
        
        # Status indicator
        self.status_indicator = QLabel("●")
        self.status_indicator.setStyleSheet("color: red; font-size: 20px;")
        layout.addWidget(self.status_indicator)
        
        self.status_text = QLabel("Disconnected")
        layout.addWidget(self.status_text)
        
        # Connection monitoring timer (check every 2 seconds)
        from PyQt6.QtCore import QTimer
        self.connection_check_timer = QTimer()
        self.connection_check_timer.timeout.connect(self.check_mqtt_connection)
        self.connection_check_timer.setInterval(2000)  # Check every 2 seconds
        
        layout.addStretch()
        
        group.setLayout(layout)
        return group
    
    def create_dataset_panel(self) -> QGroupBox:
        """Create dataset status and download panel."""
        group = QGroupBox("Dataset Status")
        layout = QVBoxLayout()
        
        # Status row
        status_layout = QHBoxLayout()
        self.dataset_status_label = QLabel("Checking...")
        status_layout.addWidget(self.dataset_status_label)
        
        self.dataset_download_btn = QPushButton("Download REFERENCE.csv")
        self.dataset_download_btn.clicked.connect(self.start_dataset_download)
        status_layout.addWidget(self.dataset_download_btn)
        
        self.dataset_bulk_download_btn = QPushButton("Bulk Download All Files")
        self.dataset_bulk_download_btn.clicked.connect(self.start_bulk_download)
        self.dataset_bulk_download_btn.setEnabled(False)
        status_layout.addWidget(self.dataset_bulk_download_btn)
        
        status_layout.addStretch()
        layout.addLayout(status_layout)
        
        # Progress bar (hidden initially)
        self.dataset_progress_bar = QProgressBar()
        self.dataset_progress_bar.setRange(0, 100)
        self.dataset_progress_bar.setValue(0)
        self.dataset_progress_bar.setVisible(False)
        layout.addWidget(self.dataset_progress_bar)
        
        # Progress text (hidden initially)
        self.dataset_progress_text = QLabel("")
        self.dataset_progress_text.setVisible(False)
        layout.addWidget(self.dataset_progress_text)
        
        group.setLayout(layout)
        return group
    
    def create_patient_panel(self) -> QGroupBox:
        """Create patient selection panel."""
        group = QGroupBox("Patient Selection")
        layout = QVBoxLayout()
        
        # Filter by rhythm classification
        filter_layout = QHBoxLayout()
        filter_layout.addWidget(QLabel("Filter by Rhythm:"))
        
        self.rhythm_filter_combo = QComboBox()
        self.rhythm_filter_combo.addItems([
            "All Rhythms",
            "Normal (N)",
            "Atrial Fibrillation (A)",
            "Other Rhythm (O)",
            "Noisy (~)"
        ])
        self.rhythm_filter_combo.setEnabled(False)
        self.rhythm_filter_combo.currentTextChanged.connect(self.on_rhythm_filter_changed)
        filter_layout.addWidget(self.rhythm_filter_combo, 1)
        
        self.patient_count_label = QLabel("0 patients")
        self.patient_count_label.setStyleSheet("color: gray; font-size: 10px;")
        filter_layout.addWidget(self.patient_count_label)
        
        layout.addLayout(filter_layout)
        
        # Patient selector
        selector_layout = QHBoxLayout()
        selector_layout.addWidget(QLabel("Select Patient:"))
        
        self.patient_combo = QComboBox()
        self.patient_combo.setEnabled(False)
        self.patient_combo.currentTextChanged.connect(self.on_patient_selected)
        selector_layout.addWidget(self.patient_combo, 1)
        
        self.refresh_btn = QPushButton("Refresh")
        self.refresh_btn.setEnabled(False)
        self.refresh_btn.clicked.connect(self.on_refresh_patients)
        selector_layout.addWidget(self.refresh_btn)
        
        layout.addLayout(selector_layout)
        
        # Patient info display
        self.patient_info_text = QTextEdit()
        self.patient_info_text.setReadOnly(True)
        self.patient_info_text.setMaximumHeight(100)
        self.patient_info_text.setPlainText("No patient selected")
        layout.addWidget(self.patient_info_text)
        
        group.setLayout(layout)
        return group
    
    def create_parameters_panel(self) -> QGroupBox:
        """Create simulation info panel."""
        group = QGroupBox("Real-Time Streaming")
        layout = QVBoxLayout()
        
        # Info label about ECG transmission
        info_label = QLabel("📡 ECG Data Transmission")
        info_label.setStyleSheet("font-weight: bold; color: #0064c8; font-size: 13px;")
        layout.addWidget(info_label)
        
        desc_label = QLabel(
            "ECG data is sent in chunks via MQTT at 300 Hz.\n"
            "You can adjust how many samples are packed into each MQTT chunk.\n"
            "Starting a new transmission will abort any ongoing transmission."
        )
        desc_label.setStyleSheet("color: gray; font-size: 10px; margin-bottom: 10px;")
        desc_label.setWordWrap(True)
        layout.addWidget(desc_label)
        
        # Sampling Rate Info (Fixed at 300 Hz)
        rate_layout = QHBoxLayout()
        rate_layout.addWidget(QLabel("Sampling Rate:"))
        self.sampling_rate_label = QLabel("300 Hz (Fixed)")
        self.sampling_rate_label.setStyleSheet("font-weight: bold; color: #0064c8;")
        rate_layout.addWidget(self.sampling_rate_label)
        rate_layout.addStretch()
        layout.addLayout(rate_layout)
        
        # Sample interval display
        self.interval_info = QLabel("Sample Interval: 3.33 ms (300 Hz)")
        self.interval_info.setStyleSheet("color: #666; font-size: 10px;")
        layout.addWidget(self.interval_info)
        
        # Chunk size configuration
        chunk_layout = QHBoxLayout()
        chunk_layout.addWidget(QLabel("Chunk Size:"))
        from PyQt6.QtWidgets import QSpinBox
        self.chunk_size_spinbox = QSpinBox()
        # Allow chunk size up to a large value so we can test ESP32/MQTT limits.
        # The effective upper bound in practice is the total number of samples
        # in the record (e.g., 9,000 for a 30s record @ 300 Hz).
        self.chunk_size_spinbox.setRange(1, 10000)
        self.chunk_size_spinbox.setValue(600)  # Default to 600 samples (2 seconds @ 300Hz)
        self.chunk_size_spinbox.setSuffix(" samples")
        self.chunk_size_spinbox.setToolTip(
            "Number of samples per MQTT message (>=1).\n"
            "Use larger values to test how big a chunk the ESP32 can handle.\n"
            "Practical upper bound is the total samples in the record."
        )
        self.chunk_size_spinbox.valueChanged.connect(self.on_chunk_size_changed)
        chunk_layout.addWidget(self.chunk_size_spinbox)
        chunk_layout.addStretch()
        layout.addLayout(chunk_layout)
        
        # Info about fixed rate
        original_info = QLabel("Fixed at 300 Hz (original dataset rate) | Values in mV")
        original_info.setStyleSheet("color: #888; font-size: 9px; font-style: italic;")
        layout.addWidget(original_info)
        
        layout.addStretch()
        
        group.setLayout(layout)
        return group
    
    def create_control_panel(self) -> QGroupBox:
        """Create control buttons panel."""
        group = QGroupBox("Controls")
        layout = QHBoxLayout()
        
        self.start_btn = QPushButton("Start")
        self.start_btn.setEnabled(False)
        self.start_btn.clicked.connect(self.on_start_clicked)
        layout.addWidget(self.start_btn)
        
        self.stop_btn = QPushButton("Stop")
        self.stop_btn.setEnabled(False)
        self.stop_btn.clicked.connect(self.on_stop_clicked)
        layout.addWidget(self.stop_btn)
        
        layout.addStretch()
        
        # ECG Plot Window button
        self.show_plot_btn = QPushButton("📊 Show ECG Plot")
        self.show_plot_btn.setStyleSheet("background-color: #0064c8; color: white; font-weight: bold;")
        self.show_plot_btn.clicked.connect(self.on_show_plot_window)
        layout.addWidget(self.show_plot_btn)
        
        group.setLayout(layout)
        return group
    
    def create_batch_experiment_panel(self) -> QGroupBox:
        """Create batch experiment automation panel."""
        group = QGroupBox("🔬 Batch Experiment (Automation)")
        group.setStyleSheet("QGroupBox { font-weight: bold; }")
        layout = QVBoxLayout()
        
        # Description
        desc_label = QLabel(
            "Run all 8,528 ECG records through the pipeline automatically.\n"
            "Results are saved to experiment_results/ folder."
        )
        desc_label.setStyleSheet("color: #666; font-size: 10px;")
        desc_label.setWordWrap(True)
        layout.addWidget(desc_label)
        
        # Configuration row 1
        config_row1 = QHBoxLayout()
        
        config_row1.addWidget(QLabel("Experiment Name:"))
        self.experiment_name_input = QLineEdit("ledger_on")
        self.experiment_name_input.setMaximumWidth(150)
        self.experiment_name_input.setToolTip("Folder name for results (ledger_on or ledger_off)")
        config_row1.addWidget(self.experiment_name_input)
        
        config_row1.addWidget(QLabel("Min Delay:"))
        self.batch_delay_spinbox = QDoubleSpinBox()
        self.batch_delay_spinbox.setRange(0.0, 10.0)
        self.batch_delay_spinbox.setValue(0.0)
        self.batch_delay_spinbox.setSuffix(" s")
        self.batch_delay_spinbox.setToolTip("Minimum delay between records (0 = as fast as possible)")
        self.batch_delay_spinbox.setMaximumWidth(80)
        config_row1.addWidget(self.batch_delay_spinbox)
        
        config_row1.addStretch()
        layout.addLayout(config_row1)
        
        # Configuration row 2 - Portal URL
        config_row2 = QHBoxLayout()
        config_row2.addWidget(QLabel("Portal URL:"))
        self.portal_url_input = QLineEdit("http://localhost:8000")
        self.portal_url_input.setMaximumWidth(250)
        self.portal_url_input.setToolTip("Web Portal URL for polling record completion")
        config_row2.addWidget(self.portal_url_input)
        config_row2.addStretch()
        layout.addLayout(config_row2)
        
        # Batch progress
        progress_layout = QHBoxLayout()
        self.batch_progress_label = QLabel("Ready")
        self.batch_progress_label.setStyleSheet("font-weight: bold;")
        progress_layout.addWidget(self.batch_progress_label)
        progress_layout.addStretch()
        
        self.batch_current_patient_label = QLabel("")
        self.batch_current_patient_label.setStyleSheet("color: #0064c8;")
        progress_layout.addWidget(self.batch_current_patient_label)
        
        layout.addLayout(progress_layout)
        
        # Progress bar
        self.batch_progress_bar = QProgressBar()
        self.batch_progress_bar.setRange(0, 100)
        self.batch_progress_bar.setValue(0)
        layout.addWidget(self.batch_progress_bar)
        
        # Stats row
        stats_layout = QHBoxLayout()
        self.batch_elapsed_label = QLabel("Elapsed: --")
        stats_layout.addWidget(self.batch_elapsed_label)
        self.batch_remaining_label = QLabel("Remaining: --")
        stats_layout.addWidget(self.batch_remaining_label)
        self.batch_accuracy_label = QLabel("Accuracy: --")
        stats_layout.addWidget(self.batch_accuracy_label)
        stats_layout.addStretch()
        layout.addLayout(stats_layout)
        
        # Control buttons
        button_layout = QHBoxLayout()
        
        self.batch_start_btn = QPushButton("🚀 Simulate All Records")
        self.batch_start_btn.setStyleSheet("background-color: #28a745; color: white; font-weight: bold; padding: 8px;")
        self.batch_start_btn.clicked.connect(self.on_batch_start_clicked)
        button_layout.addWidget(self.batch_start_btn)
        
        self.batch_stop_btn = QPushButton("⏹ Stop")
        self.batch_stop_btn.setEnabled(False)
        self.batch_stop_btn.setStyleSheet("padding: 8px;")
        self.batch_stop_btn.clicked.connect(self.on_batch_stop_clicked)
        button_layout.addWidget(self.batch_stop_btn)
        
        button_layout.addStretch()
        layout.addLayout(button_layout)
        
        group.setLayout(layout)
        return group
    
    def create_status_panel(self) -> QGroupBox:
        """Create live status panel."""
        group = QGroupBox("Live Status")
        layout = QGridLayout()
        
        row = 0
        layout.addWidget(QLabel("Patient:"), row, 0)
        self.patient_label = QLabel("None")
        layout.addWidget(self.patient_label, row, 1)
        
        layout.addWidget(QLabel("Status:"), row, 2)
        self.status_label = QLabel("Idle")
        layout.addWidget(self.status_label, row, 3)
        
        row += 1
        layout.addWidget(QLabel("Progress:"), row, 0)
        self.progress_label = QLabel("0 chunks sent")
        layout.addWidget(self.progress_label, row, 1)
        
        layout.addWidget(QLabel("Elapsed:"), row, 2)
        self.elapsed_label = QLabel("0s")
        layout.addWidget(self.elapsed_label, row, 3)
        
        row += 1
        layout.addWidget(QLabel("Samples:"), row, 0)
        self.samples_label = QLabel("0 samples sent")
        layout.addWidget(self.samples_label, row, 1, 1, 3)
        
        row += 1
        self.progress_bar = QProgressBar()
        self.progress_bar.setRange(0, 100)
        self.progress_bar.setValue(0)
        layout.addWidget(self.progress_bar, row, 0, 1, 4)
        
        group.setLayout(layout)
        return group
    
    def create_log_panel(self) -> QGroupBox:
        """Create error/event log panel."""
        group = QGroupBox("Event Log")
        layout = QVBoxLayout()
        
        # Toolbar
        toolbar = QHBoxLayout()
        
        self.clear_log_btn = QPushButton("Clear")
        self.clear_log_btn.clicked.connect(self.on_clear_log)
        toolbar.addWidget(self.clear_log_btn)
        
        self.autoscroll_check = QCheckBox("Auto-scroll")
        self.autoscroll_check.setChecked(True)
        toolbar.addWidget(self.autoscroll_check)
        
        toolbar.addStretch()
        
        layout.addLayout(toolbar)
        
        # Log text
        self.error_log = QTextEdit()
        self.error_log.setReadOnly(True)
        self.error_log.setMaximumHeight(150)
        layout.addWidget(self.error_log)
        
        group.setLayout(layout)
        return group
    
    def update_button_states(self, state: str):
        """
        Update button enabled/disabled states based on application state.
        
        Args:
            state: Application state ("disconnected", "connected", "running", "paused", "stopped")
        """
        if state == "disconnected":
            self.connect_btn.setEnabled(True)
            self.rhythm_filter_combo.setEnabled(False)
            self.patient_combo.setEnabled(False)
            self.refresh_btn.setEnabled(False)
            self.start_btn.setEnabled(False)
            self.stop_btn.setEnabled(False)
            
        elif state == "connected":
            self.connect_btn.setEnabled(False)
            self.rhythm_filter_combo.setEnabled(True)
            self.patient_combo.setEnabled(True)
            self.refresh_btn.setEnabled(True)
            # Enable start if patient is selected and MQTT is connected
            if self.controller and self.controller.current_patient and self.mqtt_connected:
                self.start_btn.setEnabled(True)
            else:
                self.start_btn.setEnabled(False)
            self.stop_btn.setEnabled(False)
            
        elif state == "running":
            self.connect_btn.setEnabled(False)
            self.rhythm_filter_combo.setEnabled(False)
            self.patient_combo.setEnabled(False)
            self.refresh_btn.setEnabled(False)
            self.start_btn.setEnabled(False)
            self.stop_btn.setEnabled(True)
            
        elif state == "stopped":
            self.connect_btn.setEnabled(False)
            self.rhythm_filter_combo.setEnabled(True)
            self.patient_combo.setEnabled(True)
            self.refresh_btn.setEnabled(True)
            # Enable start if patient is selected and connected
            if self.controller and self.controller.current_patient and self.mqtt_connected:
                self.start_btn.setEnabled(True)
            else:
                self.start_btn.setEnabled(False)
            self.stop_btn.setEnabled(False)
    
    @pyqtSlot()
    def on_start_broker_clicked(self):
        """Handle start broker button click."""
        broker_ip = self.broker_ip_combo.currentText().strip()
        broker_port = self.broker_port_input.value()
        
        if not broker_ip:
            QMessageBox.warning(self, "Invalid IP", "Please enter a valid IP address.")
            return
        
        # Validate IP address format (basic check)
        try:
            parts = broker_ip.split('.')
            if len(parts) != 4:
                raise ValueError("Invalid IP format")
            for part in parts:
                if not (0 <= int(part) <= 255):
                    raise ValueError("Invalid IP range")
        except (ValueError, AttributeError):
            if broker_ip not in ["0.0.0.0", "localhost", "127.0.0.1"]:
                QMessageBox.warning(self, "Invalid IP", 
                                  "Please enter a valid IP address (e.g., 0.0.0.0, 127.0.0.1, or your network IP).")
                return
        
        self.log_message(f"Starting embedded MQTT broker on {broker_ip}:{broker_port}...")
        
        try:
            # Create and start broker
            self.embedded_broker = EmbeddedMQTTBroker(host=broker_ip, port=broker_port)
            broker_started = self.embedded_broker.start()
            
            if broker_started:
                self.broker_status_indicator.setStyleSheet("color: green; font-size: 20px;")
                self.broker_status_text.setText(f"Running on {broker_ip}:{broker_port}")
                self.broker_start_btn.setEnabled(False)
                self.broker_stop_btn.setEnabled(True)
                self.broker_ip_combo.setEnabled(False)
                self.broker_port_input.setEnabled(False)
                self.log_message(f"✓ Embedded MQTT broker started on {broker_ip}:{broker_port}", "success")
                
                # Update MQTT connection broker input to match
                if broker_ip == "0.0.0.0":
                    # If listening on all interfaces, suggest localhost for connection
                    self.broker_input.setText("127.0.0.1")
                else:
                    self.broker_input.setText(broker_ip)
                self.port_input.setValue(broker_port)
            else:
                self.broker_status_indicator.setStyleSheet("color: red; font-size: 20px;")
                self.broker_status_text.setText("Failed to start")
                self.log_message("✗ Failed to start embedded MQTT broker", "error")
                QMessageBox.critical(self, "Broker Error", 
                                   f"Failed to start MQTT broker on {broker_ip}:{broker_port}.\n"
                                   "Check if the port is already in use.")
                self.embedded_broker = None
                
        except Exception as e:
            self.log_message(f"✗ Broker error: {str(e)}", "error")
            QMessageBox.critical(self, "Broker Error", f"Error starting broker: {str(e)}")
            self.embedded_broker = None
    
    def on_stop_broker_clicked(self):
        """Handle stop broker button click."""
        if self.embedded_broker:
            self.log_message("Stopping embedded MQTT broker...")
            try:
                # Disable buttons during stop
                self.broker_stop_btn.setEnabled(False)
                
                # Stop the broker (this will properly release the port)
                self.embedded_broker.stop()
                
                # Wait a moment to ensure port is released, then update UI
                from PyQt6.QtCore import QTimer
                QTimer.singleShot(500, lambda: self._after_broker_stopped())
                
            except Exception as e:
                self.log_message(f"✗ Error stopping broker: {str(e)}", "error")
                QMessageBox.warning(self, "Broker Error", f"Error stopping broker: {str(e)}")
                # Re-enable stop button if error occurred
                self.broker_stop_btn.setEnabled(True)
    
    def _after_broker_stopped(self):
        """Update UI after broker has been stopped and port is released."""
        self.broker_status_indicator.setStyleSheet("color: red; font-size: 20px;")
        self.broker_status_text.setText("Stopped")
        self.broker_start_btn.setEnabled(True)
        self.broker_stop_btn.setEnabled(False)
        self.broker_ip_combo.setEnabled(True)
        self.broker_port_input.setEnabled(True)
        self.log_message("✓ Embedded MQTT broker stopped and port released", "success")
        self.embedded_broker = None
    
    def on_connect_clicked(self):
        """Handle MQTT connect button click."""
        broker = self.broker_input.text()
        port = self.port_input.value()
        
        self.log_message(f"Connecting to MQTT broker {broker}:{port}...")
        
        try:
            # Create controller with MQTT settings
            self.controller = SimulationController(mqtt_broker=broker, mqtt_port=port)
            
            # Connect controller signals
            self.controller.sig_status_changed.connect(self.on_status_changed)
            self.controller.sig_mqtt_connected.connect(self.on_mqtt_connection_changed)
            
            # Initialize MQTT
            if self.controller.initialize_mqtt():
                self.mqtt_connected = True
                self.status_indicator.setStyleSheet("color: green; font-size: 20px;")
                self.status_text.setText("Connected")
                self.log_message("✓ MQTT connected successfully", "success")
                
                # Enable/disable buttons
                self.connect_btn.setEnabled(False)
                self.disconnect_btn.setEnabled(True)
                
                # Start connection monitoring
                self.connection_check_timer.start()
                
                # Load patient list
                self.on_refresh_patients()
                
                # Sync chunk size from GUI to controller
                self.controller.set_chunk_size(self.chunk_size_spinbox.value())
                
                # Update button states
                self.update_button_states("connected")
            else:
                self.status_indicator.setStyleSheet("color: red; font-size: 20px;")
                self.status_text.setText("Connection Failed")
                self.log_message("✗ MQTT connection failed", "error")
                QMessageBox.critical(self, "Connection Error", 
                                   "Failed to connect to MQTT broker.\n"
                                   "Please check the broker address and ensure it's running.")
                
        except Exception as e:
            self.log_message(f"✗ Error: {str(e)}", "error")
            QMessageBox.critical(self, "Error", f"Failed to initialize: {str(e)}")
    
    def on_disconnect_clicked(self):
        """Handle MQTT disconnect button click."""
        if self.controller:
            try:
                # Stop connection monitoring
                self.connection_check_timer.stop()
                
                # Disconnect MQTT
                self.controller.disconnect_mqtt()
                
                # Update UI
                self.mqtt_connected = False
                self.status_indicator.setStyleSheet("color: red; font-size: 20px;")
                self.status_text.setText("Disconnected")
                self.log_message("MQTT disconnected", "info")
                
                # Enable/disable buttons
                self.connect_btn.setEnabled(True)
                self.disconnect_btn.setEnabled(False)
                
                # Update button states
                self.update_button_states("disconnected")
                
            except Exception as e:
                self.log_message(f"Error disconnecting: {str(e)}", "error")
    
    def check_mqtt_connection(self):
        """Periodically check MQTT connection status."""
        if not self.controller or not self.controller.simulator:
            return
        
        if not self.controller.simulator.mqtt_client:
            if self.mqtt_connected:
                self.on_mqtt_connection_lost()
            return
        
        # Check if client is actually connected
        try:
            is_connected = self.controller.simulator.mqtt_client.is_connected()
            
            if not is_connected and self.mqtt_connected:
                # Connection lost
                self.on_mqtt_connection_lost()
            elif is_connected and not self.mqtt_connected:
                # Connection restored
                self.on_mqtt_connection_restored()
        except Exception as e:
            # Error checking connection - assume disconnected
            if self.mqtt_connected:
                logger.warning(f"Error checking MQTT connection: {str(e)}")
                self.on_mqtt_connection_lost()
    
    def on_mqtt_connection_lost(self):
        """Handle MQTT connection loss."""
        if self.mqtt_connected:  # Only log if we thought we were connected
            self.mqtt_connected = False
            self.status_indicator.setStyleSheet("color: red; font-size: 20px;")
            self.status_text.setText("Connection Lost")
            self.log_message("⚠ MQTT connection lost - broker may be offline", "error")
            
            # Enable/disable buttons
            self.connect_btn.setEnabled(True)
            self.disconnect_btn.setEnabled(False)
            
            # Update button states
            self.update_button_states("disconnected")
            
            # Stop any running simulation
            if self.controller and self.controller.worker and self.controller.worker.isRunning():
                self.log_message("Stopping simulation due to connection loss", "warning")
                self.controller.stop_simulation()
    
    def on_mqtt_connection_restored(self):
        """Handle MQTT connection restoration."""
        self.mqtt_connected = True
        self.status_indicator.setStyleSheet("color: green; font-size: 20px;")
        self.status_text.setText("Connected")
        self.log_message("✓ MQTT connection restored", "success")
        
        # Enable/disable buttons
        self.connect_btn.setEnabled(False)
        self.disconnect_btn.setEnabled(True)
        
        # Update button states
        self.update_button_states("connected")
    
    @pyqtSlot()
    def on_refresh_patients(self):
        """Refresh the patient list based on current filter."""
        if not self.controller:
            return
        
        try:
            # Get all patients
            all_patients = self.controller.get_patient_list()
            
            # Apply rhythm filter
            filtered_patients = self._filter_patients_by_rhythm(all_patients)
            
            # Update combo box
            self.patient_combo.clear()
            self.patient_combo.addItems(filtered_patients)
            
            # Update count label
            self.patient_count_label.setText(f"{len(filtered_patients)} patients")
            
            self.log_message(f"Loaded {len(filtered_patients)} patients (filtered from {len(all_patients)})")
        except Exception as e:
            self.log_message(f"✗ Error loading patients: {str(e)}", "error")
    
    def _filter_patients_by_rhythm(self, patients: list) -> list:
        """
        Filter patients by selected rhythm classification.
        
        Args:
            patients: List of all patient IDs
            
        Returns:
            Filtered list of patient display strings based on rhythm selection
        """
        if not self.controller:
            return patients
        
        filter_text = self.rhythm_filter_combo.currentText()
        
        # Rhythm indicators for display
        rhythm_indicators = {
            'N': '✅',  # Normal
            'A': '⚠️',   # Atrial Fibrillation
            'O': '🔶',  # Other
            '~': '⚡'   # Noisy
        }
        
        # No filter - return all with rhythm indicators
        if filter_text == "All Rhythms":
            formatted = []
            for patient_id in patients:
                patient_info = self.controller.simulator.patient_records.get(patient_id)
                if patient_info:
                    rhythm = patient_info['rhythm']
                    indicator = rhythm_indicators.get(rhythm, '❓')
                    formatted.append(f"{patient_id} {indicator} {rhythm}")
                else:
                    formatted.append(patient_id)
            return formatted
        
        # Extract rhythm code from filter text (e.g., "Normal (N)" -> "N")
        rhythm_code = filter_text.split('(')[-1].strip(')')
        
        # Filter patients by rhythm
        filtered = []
        for patient_id in patients:
            patient_info = self.controller.simulator.patient_records.get(patient_id)
            if patient_info and patient_info['rhythm'] == rhythm_code:
                indicator = rhythm_indicators.get(rhythm_code, '❓')
                filtered.append(f"{patient_id} {indicator} {rhythm_code}")
        
        return filtered
    
    @pyqtSlot(str)
    def on_rhythm_filter_changed(self, filter_text: str):
        """Handle rhythm filter change."""
        self.on_refresh_patients()  # Refresh with new filter
    
    @pyqtSlot(int)
    def on_chunk_size_changed(self, value: int):
        """Handle chunk size spinbox change."""
        if self.controller:
            try:
                self.controller.set_chunk_size(value)
                self.log_message(f"Chunk size set to {value} samples per MQTT message")
            except Exception as e:
                self.log_message(f"Error setting chunk size: {str(e)}", "error")
    
    @pyqtSlot(str)
    def on_patient_selected(self, patient_display: str):
        """Handle patient selection change."""
        if not patient_display or not self.controller:
            return
        
        # Extract patient ID from display string (e.g., "A00/A00001 ✅ N" -> "A00/A00001")
        patient_id = patient_display.split()[0] if ' ' in patient_display else patient_display
        
        # Check if patient file needs to be downloaded
        from dataset_downloader import DatasetDownloadWorker
        import os
        mat_file = os.path.join(DatasetDownloadWorker.TRAINING_DIR, f"{patient_id}.mat")
        
        if not os.path.exists(mat_file):
            self.patient_info_text.setPlainText(f"Downloading {patient_id}.mat...\nPlease wait...")
            self.log_message(f"Downloading patient file: {patient_id}.mat")
            # Force GUI update
            from PyQt6.QtWidgets import QApplication
            QApplication.processEvents()
        
        info = self.controller.get_patient_info(patient_id)
        if info:
            # Map rhythm codes to emoji indicators
            rhythm_indicators = {
                'N': '✅',  # Normal
                'A': '⚠️',   # Atrial Fibrillation
                'O': '🔶',  # Other
                '~': '⚡'   # Noisy
            }
            indicator = rhythm_indicators.get(info['rhythm'], '❓')
            
            info_text = (
                f"{indicator} Patient ID: {info['patient_id']}\n"
                f"Rhythm Classification: {info['rhythm_name']} ({info['rhythm']})\n"
                f"Duration: {info['duration_seconds']:.1f} seconds\n"
                f"Samples: {info['samples']} @ {info['sampling_rate']} Hz"
            )
            self.patient_info_text.setPlainText(info_text)
            
            # Update Start button state - enable if MQTT is connected
            if self.mqtt_connected and not (self.controller.worker and self.controller.worker.isRunning()):
                self.start_btn.setEnabled(True)
            else:
                self.start_btn.setEnabled(False)
            
            # Update plot window ONLY if it's already visible
            # Don't auto-open the window, just update it if user has it open
            if self.plot_window and self.plot_window.isVisible():
                try:
                    # Load full record for the new patient
                    ecg_data, metadata = self.controller.simulator.load_ecg(patient_id)
                    start_timestamp_ms = metadata.get('start_timestamp_ms', 0)
                    record_time = metadata.get('record_time', '')
                    
                    self.plot_window.set_patient_info(
                        patient_id,
                        info['rhythm_name'],
                        start_timestamp_ms
                    )
                    
                    # Load full record
                    self.plot_window.load_full_record(
                        ecg_data.tolist(),
                        metadata['sampling_rate'],
                        start_timestamp_ms,
                        record_time
                    )
                    
                    logger.info(f"ECG plot updated for patient {patient_id}")
                except Exception as e:
                    logger.error(f"Error updating ECG plot for patient {patient_id}: {str(e)}")
                    # Clear plot if loading fails
                    if self.plot_window:
                        self.plot_window.clear_plot()
                        self.plot_window.set_patient_info(patient_id, info['rhythm_name'])
        else:
            self.patient_info_text.setPlainText("Patient information not available")
            
            # Clear plot window if it exists and no patient info available
            if self.plot_window:
                self.plot_window.clear_plot()
                self.plot_window.set_patient_info("", "")
    
    @pyqtSlot(int)
    @pyqtSlot(int)
    @pyqtSlot()
    def on_start_clicked(self):
        """Handle start button click."""
        if not self.controller:
            return
        
        patient_display = self.patient_combo.currentText()
        if not patient_display:
            QMessageBox.warning(self, "No Patient", "Please select a patient first.")
            return
        
        # Extract patient ID from display string (e.g., "A00/A00001 ✅ N" -> "A00/A00001")
        patient_id = patient_display.split()[0] if ' ' in patient_display else patient_display
        # Sampling rate is fixed at 300 Hz
        self.log_message(f"Starting real-time simulation: {patient_id} @ 300 Hz (fixed)")
        
        # Step 1: Create worker (but don't start thread yet)
        if not self.controller.start_simulation(patient_id):
            self.log_message("✗ Failed to create simulation", "error")
            QMessageBox.critical(self, "Start Error", "Failed to create simulation.")
            return
        
        # Step 2: Connect GUI signals to worker (before thread starts!)
        if self.controller.worker:
            self.controller.worker.sig_status.connect(self.on_worker_status)
            self.controller.worker.sig_window_sent.connect(self.on_worker_window_sent)
            self.controller.worker.sig_progress.connect(self.on_worker_progress)
            self.controller.worker.sig_error.connect(self.on_worker_error)
            self.controller.worker.sig_finished.connect(self.on_worker_finished)
            self.log_message("✓ Worker signals connected", "success")
        
        # Step 3: Now start the worker thread
        if self.controller.start_worker():
            self.patient_label.setText(patient_id)
            self.status_label.setText("Starting...")
            self.status_label.setStyleSheet("color: blue;")
            # Immediately update button states - Start disabled, Stop enabled
            self.start_btn.setEnabled(False)
            self.stop_btn.setEnabled(True)
            self.update_button_states("running")
            self.log_message("✓ Continuous publishing started", "success")
        else:
            self.log_message("✗ Failed to start worker thread", "error")
            QMessageBox.critical(self, "Start Error", "Failed to start worker thread.")
    
    @pyqtSlot()
    def on_stop_clicked(self):
        """Handle stop button click."""
        if self.controller:
            self.log_message("Stopping continuous publishing...")
            self.controller.stop_simulation()
            self.log_message("✓ Publishing stopped", "success")
            
            # Immediately update button states - Stop disabled, Start enabled
            self.stop_btn.setEnabled(False)
            if self.controller.current_patient and self.mqtt_connected:
                self.start_btn.setEnabled(True)
            else:
                self.start_btn.setEnabled(False)
            
            self.update_button_states("stopped")
            
            # Reset progress
            self.progress_bar.setValue(0)
            self.progress_label.setText("0 / 0 windows")
            self.status_label.setText("Stopped")
            
            # Clear plot window if open
            if self.plot_window:
                self.plot_window.clear_plot()
    
    @pyqtSlot(str)
    def on_worker_status(self, msg: str):
        """Handle worker status update."""
        # Update status label with worker messages
        self.status_label.setText(msg)
        self.log_message(msg)
        
        # Update status styling based on message content
        if "sending" in msg.lower() or "chunk" in msg.lower() or "transmission" in msg.lower() or "connected" in msg.lower():
            self.status_label.setStyleSheet("color: green; font-weight: bold;")
        elif "error" in msg.lower() or "failed" in msg.lower() or "aborted" in msg.lower():
            self.status_label.setStyleSheet("color: red; font-weight: bold;")
        else:
            self.status_label.setStyleSheet("")
    
    @pyqtSlot(dict)
    def on_worker_window_sent(self, data: dict):
        """Handle worker window sent signal (full record preview)."""
        # Update plot window if it's open - load full record
        if self.plot_window and self.plot_window.isVisible():
            if 'ecg_data' in data and 'sampling_rate' in data:
                # Get full ECG data from controller if available
                if self.controller and self.controller.current_patient:
                    try:
                        # Load full record from simulator
                        ecg_data, metadata = self.controller.simulator.load_ecg(
                            self.controller.current_patient
                        )
                        start_timestamp_ms = metadata.get('start_timestamp_ms', 0)
                        record_time = metadata.get('record_time', '')
                        
                        # Load full record into plot window
                        self.plot_window.load_full_record(
                            ecg_data.tolist(),
                            metadata['sampling_rate'],
                            start_timestamp_ms,
                            record_time
                        )
                    except Exception as e:
                        logger.error(f"Error loading full record for plot: {str(e)}")
                        # Fallback to preview data
                        self.plot_window.update_plot(data['ecg_data'], data['sampling_rate'])
                else:
                    # Fallback to preview data
                    self.plot_window.update_plot(data['ecg_data'], data['sampling_rate'])
        
        # Update occasionally (not every chunk to avoid log spam)
        if data.get("window_num", 0) % 10 == 0:
            self.log_message(
                f"📤 Chunk {data.get('window_num', 0)} sent: {data['samples']} samples @ {data.get('timestamp', 'N/A')}"
            )
    
    @pyqtSlot(dict)
    def on_worker_progress(self, data: dict):
        """Handle worker progress update."""
        # Support both old format (windows_sent) and new format (chunks_sent)
        chunks_sent = data.get("chunks_sent", data.get("windows_sent", 0))
        elapsed = data.get("elapsed_time", 0)
        samples = data.get("samples_total", 0)
        
        # Update labels - show chunk progress
        self.progress_label.setText(f"{chunks_sent} chunks sent")
        self.elapsed_label.setText(f"{elapsed:.1f}s")
        self.samples_label.setText(f"{samples} samples sent")
        
        # Progress bar shows activity (pulse animation)
        # Show chunks sent % 100 for visual feedback during transmission
        percent = (chunks_sent % 100)
        self.progress_bar.setValue(percent)
    
    @pyqtSlot(str)
    def on_worker_error(self, msg: str):
        """Handle worker error."""
        self.log_message(f"✗ ERROR: {msg}", "error")
    
    @pyqtSlot()
    def on_worker_finished(self):
        """Handle worker finished signal."""
        self.log_message("✓ Publishing stopped", "success")
        self.status_label.setText("Stopped")
        # Update button states - Stop disabled, Start enabled if conditions met
        self.stop_btn.setEnabled(False)
        if self.controller and self.controller.current_patient and self.mqtt_connected:
            self.start_btn.setEnabled(True)
        else:
            self.start_btn.setEnabled(False)
        self.update_button_states("stopped")
    
    @pyqtSlot(str)
    def on_status_changed(self, status: str):
        """Handle controller status change."""
        logger.info(f"Status changed: {status}")
    
    @pyqtSlot(bool)
    def on_mqtt_connection_changed(self, connected: bool):
        """Handle MQTT connection state change from controller."""
        self.mqtt_connected = connected
        if connected:
            self.status_indicator.setStyleSheet("color: green; font-size: 20px;")
            self.status_text.setText("Connected")
            self.connect_btn.setEnabled(False)
            self.disconnect_btn.setEnabled(True)
            if not self.connection_check_timer.isActive():
                self.connection_check_timer.start()
        else:
            self.status_indicator.setStyleSheet("color: red; font-size: 20px;")
            self.status_text.setText("Disconnected")
            self.connect_btn.setEnabled(True)
            self.disconnect_btn.setEnabled(False)
            if self.connection_check_timer.isActive():
                self.connection_check_timer.stop()
    
    @pyqtSlot()
    def on_clear_log(self):
        """Clear the event log."""
        self.error_log.clear()
    
    def log_message(self, msg: str, level: str = "info"):
        """
        Add message to event log.
        
        Args:
            msg: Message to log
            level: Message level ("info", "error", "success")
        """
        timestamp = datetime.now().strftime("%H:%M:%S")
        
        if level == "error":
            color = "red"
        elif level == "success":
            color = "green"
        else:
            color = "black"
        
        formatted_msg = f'<span style="color: gray;">[{timestamp}]</span> <span style="color: {color};">{msg}</span>'
        self.error_log.append(formatted_msg)
        
        # Auto-scroll if enabled
        if self.autoscroll_check.isChecked():
            scrollbar = self.error_log.verticalScrollBar()
            scrollbar.setValue(scrollbar.maximum())
    
    def update_dataset_status(self):
        """Update dataset status in the GUI."""
        from dataset_downloader import DatasetDownloadWorker
        
        if DatasetDownloadWorker.is_dataset_downloaded():
            self.dataset_status_label.setText("✅ REFERENCE.csv ready (files download on demand)")
            self.dataset_status_label.setStyleSheet("color: green;")
            self.dataset_download_btn.setEnabled(False)
            self.dataset_download_btn.setText("REFERENCE Downloaded")
            self.dataset_bulk_download_btn.setEnabled(True)
        else:
            self.dataset_status_label.setText("⚠️ REFERENCE.csv not downloaded (required for patient list)")
            self.dataset_status_label.setStyleSheet("color: orange;")
            self.dataset_download_btn.setEnabled(True)
            self.dataset_download_btn.setText("Download REFERENCE.csv")
            self.dataset_bulk_download_btn.setEnabled(False)
    
    def start_dataset_download(self, bulk_mode=False):
        """Start dataset download with progress shown in main GUI."""
        from dataset_downloader import DatasetDownloadWorker
        
        # Disable download buttons
        self.dataset_download_btn.setEnabled(False)
        self.dataset_bulk_download_btn.setEnabled(False)
        
        if bulk_mode:
            self.dataset_bulk_download_btn.setText("Downloading...")
            mode = DatasetDownloadWorker.MODE_BULK_DOWNLOAD
        else:
            self.dataset_download_btn.setText("Downloading...")
            mode = DatasetDownloadWorker.MODE_REFERENCE_ONLY
        
        # Show progress bar and text
        self.dataset_progress_bar.setVisible(True)
        self.dataset_progress_text.setVisible(True)
        self.dataset_progress_bar.setValue(0)
        self.dataset_progress_text.setText("Preparing download...")
        
        # Update status
        self.dataset_status_label.setText("📥 Downloading dataset...")
        self.dataset_status_label.setStyleSheet("color: blue;")
        
        # Create download worker
        self.dataset_worker = DatasetDownloadWorker(mode=mode)
        
        # Connect signals
        def on_progress(msg, percent):
            self.dataset_progress_bar.setValue(percent)
            self.dataset_progress_text.setText(f"{msg} ({percent}%)")
            self.log_message(f"Dataset download: {msg} - {percent}%")
        
        def on_status(msg):
            self.dataset_status_label.setText(f"📥 {msg}")
            self.log_message(f"Dataset: {msg}")
        
        def on_finished(success, message):
            if success:
                self.dataset_progress_bar.setValue(100)
                self.dataset_progress_text.setText("Download complete!")
                self.dataset_status_label.setText("✅ Dataset ready")
                self.dataset_status_label.setStyleSheet("color: green;")
                self.dataset_download_btn.setText("REFERENCE Downloaded")
                self.dataset_download_btn.setEnabled(False)
                self.dataset_bulk_download_btn.setText("Bulk Download All Files")
                self.dataset_bulk_download_btn.setEnabled(True)
                
                # Hide progress after a delay
                from PyQt6.QtCore import QTimer
                QTimer.singleShot(3000, lambda: (
                    self.dataset_progress_bar.setVisible(False),
                    self.dataset_progress_text.setVisible(False)
                ))
                
                # Reload dataset in controller if it exists
                if self.controller:
                    self.controller.simulator.reload_dataset()
                    self.on_refresh_patients()
                
                self.log_message(f"✅ {message}", "success")
                QMessageBox.information(self, "Download Complete", message)
            else:
                self.dataset_status_label.setText("❌ Download failed")
                self.dataset_status_label.setStyleSheet("color: red;")
                self.dataset_download_btn.setText("Download REFERENCE.csv")
                self.dataset_download_btn.setEnabled(True)
                self.dataset_bulk_download_btn.setEnabled(False)
                self.dataset_progress_bar.setVisible(False)
                self.dataset_progress_text.setVisible(False)
                
                self.log_message(f"✗ {message}", "error")
                QMessageBox.critical(self, "Download Failed", message)
        
        self.dataset_worker.sig_progress.connect(on_progress)
        self.dataset_worker.sig_status.connect(on_status)
        self.dataset_worker.sig_finished.connect(on_finished)
        
        # Start download
        self.dataset_worker.start()
    
    def start_bulk_download(self):
        """Start bulk download of all patient files."""
        reply = QMessageBox.question(
            self,
            "Bulk Download",
            "This will download all 8,528 patient files (~800 MB total).\n\n"
            "This may take 15-30 minutes depending on your connection.\n\n"
            "Files are downloaded on demand by default, so bulk download is optional.\n\n"
            "Continue with bulk download?",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
            QMessageBox.StandardButton.No
        )
        
        if reply == QMessageBox.StandardButton.Yes:
            self.start_dataset_download(bulk_mode=True)
    
    @pyqtSlot()
    def on_show_plot_window(self):
        """Show or bring to front the ECG plot window and load current patient's ECG."""
        # Create plot window if it doesn't exist
        if self.plot_window is None:
            self.plot_window = ECGPlotWindow(self)
            self.log_message("ECG plot window opened", "success")
        
        # Load current patient's ECG data (if a patient is selected)
        if self.controller:
            # Get currently selected patient from the combo box
            patient_display = self.patient_combo.currentText()
            if patient_display:
                # Extract patient ID from display string (e.g., "A00/A00001 ✅ N" -> "A00/A00001")
                patient_id = patient_display.split()[0] if ' ' in patient_display else patient_display
                patient_info = self.controller.get_patient_info(patient_id)
                
                if patient_info:
                    try:
                        # Load full record for the current patient
                        ecg_data, metadata = self.controller.simulator.load_ecg(patient_id)
                        start_timestamp_ms = metadata.get('start_timestamp_ms', 0)
                        record_time = metadata.get('record_time', '')
                        
                        self.plot_window.set_patient_info(
                            patient_id,
                            patient_info.get('rhythm_name', ''),
                            start_timestamp_ms
                        )
                        
                        # Load full record
                        self.plot_window.load_full_record(
                            ecg_data.tolist(),
                            metadata['sampling_rate'],
                            start_timestamp_ms,
                            record_time
                        )
                        logger.info(f"ECG plot loaded for patient {patient_id}")
                    except Exception as e:
                        logger.error(f"Error loading ECG for plot: {str(e)}")
                        self.plot_window.set_patient_info(
                            patient_id,
                            patient_info.get('rhythm_name', '')
                        )
                else:
                    # Patient info not available - clear the plot
                    self.plot_window.clear_plot()
                    self.plot_window.set_patient_info("", "")
            else:
                # No patient selected - clear the plot
                self.plot_window.clear_plot()
                self.plot_window.set_patient_info("", "")
        else:
            # No controller - clear the plot
            if self.plot_window:
                self.plot_window.clear_plot()
                self.plot_window.set_patient_info("", "")
        
        # Show and bring to front
        self.plot_window.show()
        self.plot_window.raise_()
        self.plot_window.activateWindow()
    
    # === Batch Experiment Event Handlers ===
    
    @pyqtSlot()
    def on_batch_start_clicked(self):
        """Start batch simulation of all records."""
        # Validate MQTT connection
        if not self.controller or not self.mqtt_connected:
            QMessageBox.warning(
                self, "MQTT Not Connected",
                "Please connect to MQTT broker first before starting batch simulation."
            )
            return
        
        # Check if dataset is available
        if not self.controller.simulator.check_dataset_available():
            QMessageBox.warning(
                self, "Dataset Not Available",
                "Please download the dataset first."
            )
            return
        
        # Confirm with user
        patient_count = len(self.controller.simulator.list_patients())
        reply = QMessageBox.question(
            self, "Start Batch Experiment",
            f"This will process all {patient_count} ECG records through the pipeline.\n\n"
            f"Experiment: {self.experiment_name_input.text()}\n"
            f"Portal URL: {self.portal_url_input.text()}\n"
            f"Min Delay: {self.batch_delay_spinbox.value()}s\n\n"
            "Continue?",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
            QMessageBox.StandardButton.No
        )
        
        if reply != QMessageBox.StandardButton.Yes:
            return
        
        # Create and configure batch worker
        self.batch_worker = BatchSimulatorWorker(
            self.controller.simulator,
            self.portal_url_input.text(),
            edge_url="http://localhost:5001"
        )
        self.batch_worker.configure(
            min_delay_seconds=self.batch_delay_spinbox.value(),
            experiment_name=self.experiment_name_input.text(),
            portal_url=self.portal_url_input.text(),
            edge_url="http://localhost:5001"
        )
        
        # Connect signals
        self.batch_worker.sig_progress.connect(self.on_batch_progress)
        self.batch_worker.sig_status.connect(self.on_batch_status)
        self.batch_worker.sig_record_complete.connect(self.on_batch_record_complete)
        self.batch_worker.sig_finished.connect(self.on_batch_finished)
        self.batch_worker.sig_error.connect(self.on_batch_error)
        
        # Update UI
        self.batch_start_btn.setEnabled(False)
        self.batch_stop_btn.setEnabled(True)
        self.batch_progress_label.setText("Starting...")
        self.batch_progress_bar.setValue(0)
        
        # Start
        self.batch_worker.start()
        self.log_message(f"🚀 Batch experiment started: {self.experiment_name_input.text()}")
    
    @pyqtSlot()
    def on_batch_stop_clicked(self):
        """Stop batch simulation."""
        if self.batch_worker and self.batch_worker.isRunning():
            self.batch_worker.stop()
            self.batch_stop_btn.setEnabled(False)
            self.batch_progress_label.setText("Stopping...")
            self.log_message("⏹ Batch stop requested")
    
    @pyqtSlot(int, int, str)
    def on_batch_progress(self, current: int, total: int, patient_id: str):
        """Handle batch progress update."""
        percent = int((current / total) * 100) if total > 0 else 0
        self.batch_progress_bar.setValue(percent)
        self.batch_progress_label.setText(f"Processing {current}/{total}")
        self.batch_current_patient_label.setText(f"Patient: {patient_id}")
        
        # Estimate remaining time based on elapsed
        if hasattr(self, '_batch_start_time') and current > 1:
            import time
            elapsed = time.time() - self._batch_start_time
            rate = current / elapsed  # records per second
            remaining = (total - current) / rate if rate > 0 else 0
            self.batch_elapsed_label.setText(f"Elapsed: {int(elapsed)}s")
            self.batch_remaining_label.setText(f"Remaining: ~{int(remaining)}s")
        elif current == 1:
            import time
            self._batch_start_time = time.time()
    
    @pyqtSlot(str)
    def on_batch_status(self, message: str):
        """Handle batch status message."""
        self.log_message(f"📊 {message}")
    
    @pyqtSlot(dict)
    def on_batch_record_complete(self, result: dict):
        """Handle single record completion."""
        patient_id = result.get('patient_id', '?')
        success = result.get('success', False)
        predicted = result.get('predicted_class', '?')
        ground_truth = result.get('ground_truth', '?')
        latency = result.get('latency_ms', 0)
        
        if success:
            match = "✓" if predicted == ground_truth else "✗"
            self.log_message(f"  {patient_id}: {ground_truth}→{predicted} {match} ({latency:.0f}ms)")
    
    @pyqtSlot(dict)
    def on_batch_finished(self, summary: dict):
        """Handle batch completion."""
        self.batch_start_btn.setEnabled(True)
        self.batch_stop_btn.setEnabled(False)
        
        total = summary.get('total_records', 0)
        successful = summary.get('successful', 0)
        accuracy = summary.get('accuracy', 0) * 100
        elapsed = summary.get('elapsed_seconds', 0)
        
        self.batch_progress_label.setText("Complete!")
        self.batch_progress_bar.setValue(100)
        self.batch_accuracy_label.setText(f"Accuracy: {accuracy:.1f}%")
        self.batch_elapsed_label.setText(f"Elapsed: {int(elapsed)}s")
        self.batch_remaining_label.setText(f"Done: {successful}/{total}")
        
        self.log_message(f"✅ Batch complete: {successful}/{total} records, {accuracy:.1f}% accuracy, {elapsed:.0f}s")
        
        QMessageBox.information(
            self, "Batch Complete",
            f"Processed {successful}/{total} records\n"
            f"Accuracy: {accuracy:.1f}%\n"
            f"Time: {elapsed:.0f} seconds\n\n"
            f"Results saved to experiment_results/{summary.get('experiment_folder', '')}/"
        )
    
    @pyqtSlot(str)
    def on_batch_error(self, message: str):
        """Handle batch error."""
        self.batch_start_btn.setEnabled(True)
        self.batch_stop_btn.setEnabled(False)
        self.batch_progress_label.setText("Error")
        self.log_message(f"❌ Batch error: {message}", "error")
        QMessageBox.critical(self, "Batch Error", message)
    
    def closeEvent(self, event):
        """Handle window close event."""
        if self.controller:
            # Stop simulation if running
            if self.controller.worker and self.controller.worker.isRunning():
                reply = QMessageBox.question(
                    self, "Confirm Exit",
                    "Simulation is running. Stop and exit?",
                    QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
                    QMessageBox.StandardButton.No
                )
                
                if reply == QMessageBox.StandardButton.Yes:
                    self.controller.stop_simulation()
                    self.controller.cleanup()
                else:
                    event.ignore()
                    return
            else:
                self.controller.cleanup()
        
        # Stop embedded broker if running
        if self.embedded_broker:
            try:
                self.embedded_broker.stop()
                logger.info("Embedded MQTT broker stopped on window close")
            except Exception as e:
                logger.error(f"Error stopping broker on close: {e}")
        
        event.accept()

