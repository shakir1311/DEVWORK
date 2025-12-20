"""
EDGE Layer GUI
PyQt6 GUI for displaying ECG data received from ESP32.
"""

import logging
import sys
from typing import Optional
import numpy as np
from PyQt6.QtWidgets import (
    QMainWindow, QWidget, QVBoxLayout, QHBoxLayout,
    QLabel, QPushButton, QTextEdit, QGroupBox, QStatusBar, QDoubleSpinBox, QComboBox
)
from PyQt6.QtCore import Qt, QTimer, pyqtSignal, QObject
from PyQt6.QtGui import QPalette, QColor
import pyqtgraph as pg

logger = logging.getLogger(__name__)


class ECGDataSignal(QObject):
    """Signal emitter for ECG data updates (thread-safe)."""
    ecg_data_received = pyqtSignal(np.ndarray, dict)  # ecg_data, metadata
    processing_results_updated = pyqtSignal(dict)  # processing_results
    connection_status_changed = pyqtSignal(bool)  # connected
    log_message_signal = pyqtSignal(str, str)  # message, level
    request_ecg_data = pyqtSignal()  # Request ECG data from ESP32
    model_changed = pyqtSignal(str)  # path to selected model


class EDGEGUI(QMainWindow):
    """Main GUI window for EDGE layer ECG visualization."""
    
    def __init__(self):
        """Initialize the EDGE GUI."""
        super().__init__()
        
        self.setWindowTitle("EDGE Layer - ECG Data Processor")
        self.setMinimumSize(1200, 800)
        
        # ECG data storage
        self.ecg_data = np.array([])
        self.time_data = np.array([])
        self.sampling_rate = 300  # Hz
        self.metadata = {}
        self.duration_seconds = 0.0
        
        # Time window navigation
        self.visible_time_window = 2.0  # Seconds visible in view (default: 2 seconds)
        self.current_time_position = 0.0  # Current time position (seconds from start)
        
        # Signal emitter for thread-safe updates
        self.signal_emitter = ECGDataSignal()
        self.signal_emitter.ecg_data_received.connect(self.update_ecg_plot)
        self.signal_emitter.processing_results_updated.connect(self.update_processing_results)
        self.signal_emitter.connection_status_changed.connect(self.set_connection_status)
        self.signal_emitter.log_message_signal.connect(self.log_message)
        
        # Setup UI
        self.init_ui()
        
        # Status update timer
        self.status_timer = QTimer()
        self.status_timer.timeout.connect(self.update_status)
        self.status_timer.start(1000)  # Update every second
    
    def init_ui(self):
        """Initialize the UI."""
        central_widget = QWidget()
        self.setCentralWidget(central_widget)
        layout = QVBoxLayout(central_widget)
        
        # Title
        title_label = QLabel("EDGE Layer - ECG Data Processor")
        title_label.setStyleSheet("font-size: 20pt; font-weight: bold; color: #0064c8; padding: 10px;")
        layout.addWidget(title_label)
        
        # Status bar
        self.status_bar = QStatusBar()
        self.setStatusBar(self.status_bar)
        self.status_bar.showMessage("Waiting for ECG data from ESP32...")
        
        # Main content area
        content_layout = QHBoxLayout()
        
        # Left panel: ECG plot
        plot_group = QGroupBox("ECG Waveform")
        plot_layout = QVBoxLayout()
        
        # Configure pyqtgraph
        pg.setConfigOptions(antialias=True)
        
        # Create plot widget
        self.plot_widget = pg.PlotWidget()
        self.plot_widget.setBackground('w')  # White background (ECG paper style)
        self.plot_widget.setLabel('left', 'Amplitude', units='mV')
        self.plot_widget.setLabel('bottom', 'Time', units='s')
        self.plot_widget.setTitle('ECG Waveform', size='14pt', bold=True)
        self.plot_widget.showGrid(x=True, y=True, alpha=0.3)
        self.plot_widget.setMouseEnabled(x=True, y=True)  # Enable pan/zoom
        self.plot_widget.showButtons()  # Show auto-range buttons
        
        # Create plot line (ECG-style: black line)
        pen = pg.mkPen(color=(0, 0, 0), width=1.5)
        self.plot_line = self.plot_widget.plot([], [], pen=pen)
        
        plot_layout.addWidget(self.plot_widget)
        
        # Plot controls
        plot_controls = QHBoxLayout()
        
        # Info labels
        self.info_label = QLabel("No ECG data received yet")
        self.info_label.setStyleSheet("font-weight: bold; color: #666; padding: 5px;")
        plot_controls.addWidget(self.info_label)
        
        plot_controls.addStretch()
        
        # Time window control (horizontal resolution)
        plot_controls.addWidget(QLabel("Time Window:"))
        self.time_window_spinbox = QDoubleSpinBox()
        self.time_window_spinbox.setRange(0.5, 60.0)
        self.time_window_spinbox.setValue(2.0)
        self.time_window_spinbox.setSingleStep(0.5)
        self.time_window_spinbox.setSuffix(" s")
        self.time_window_spinbox.setToolTip("Visible time window in seconds (horizontal resolution)")
        self.time_window_spinbox.valueChanged.connect(self.on_time_window_changed)
        plot_controls.addWidget(self.time_window_spinbox)
        
        plot_controls.addWidget(QLabel("|"))
        
        # Navigation controls
        self.scroll_left_btn = QPushButton("◄")
        self.scroll_left_btn.setToolTip("Scroll left (or drag with mouse)")
        self.scroll_left_btn.clicked.connect(self.scroll_left)
        plot_controls.addWidget(self.scroll_left_btn)
        
        self.scroll_right_btn = QPushButton("►")
        self.scroll_right_btn.setToolTip("Scroll right (or drag with mouse)")
        self.scroll_right_btn.clicked.connect(self.scroll_right)
        plot_controls.addWidget(self.scroll_right_btn)
        
        self.jump_start_btn = QPushButton("⏮")
        self.jump_start_btn.setToolTip("Jump to start of ECG")
        self.jump_start_btn.clicked.connect(self.jump_to_start)
        plot_controls.addWidget(self.jump_start_btn)
        
        self.jump_end_btn = QPushButton("⏭")
        self.jump_end_btn.setToolTip("Jump to end of ECG")
        self.jump_end_btn.clicked.connect(self.jump_to_end)
        plot_controls.addWidget(self.jump_end_btn)
        
        plot_controls.addWidget(QLabel("|"))
        
        # Zoom controls (vertical only - horizontal uses time window)
        plot_controls.addWidget(QLabel("Vertical Zoom:"))
        self.zoom_in_btn = QPushButton("🔍+")
        self.zoom_in_btn.setToolTip("Zoom in vertically (amplitude)")
        self.zoom_in_btn.clicked.connect(self.zoom_in_vertical)
        plot_controls.addWidget(self.zoom_in_btn)
        
        self.zoom_out_btn = QPushButton("🔍-")
        self.zoom_out_btn.setToolTip("Zoom out vertically (amplitude)")
        self.zoom_out_btn.clicked.connect(self.zoom_out_vertical)
        plot_controls.addWidget(self.zoom_out_btn)
        
        self.fit_btn = QPushButton("Fit All")
        self.fit_btn.setToolTip("Fit entire ECG record to view")
        self.fit_btn.clicked.connect(self.fit_to_view)
        plot_controls.addWidget(self.fit_btn)
        
        plot_controls.addWidget(QLabel("|"))
        
        # Clear button
        self.clear_btn = QPushButton("Clear Plot")
        self.clear_btn.clicked.connect(self.clear_plot)
        plot_controls.addWidget(self.clear_btn)
        
        # Auto-scale button (vertical only)
        self.autoscale_btn = QPushButton("Auto Scale")
        self.autoscale_btn.setToolTip("Auto-scale vertical axis (amplitude)")
        self.autoscale_btn.clicked.connect(self.auto_scale_plot)
        plot_controls.addWidget(self.autoscale_btn)
        
        plot_layout.addLayout(plot_controls)
        
        # Instructions label
        instructions = QLabel(
            "💡 <b>Controls:</b> "
            "Mouse drag = Pan | Mouse wheel = Zoom | "
            "Right-click = Context menu | "
            "Double-click = Auto-range"
        )
        instructions.setStyleSheet("color: #666; font-size: 10pt; padding: 5px;")
        plot_layout.addWidget(instructions)
        plot_group.setLayout(plot_layout)
        content_layout.addWidget(plot_group, stretch=2)
        
        # Right panel: Status and info
        info_group = QGroupBox("Status & Information")
        info_layout = QVBoxLayout()
        
        # Connection status
        status_label = QLabel("Connection Status:")
        status_label.setStyleSheet("font-weight: bold;")
        info_layout.addWidget(status_label)
        
        self.connection_status = QLabel("Disconnected")
        self.connection_status.setStyleSheet("color: red; font-size: 12pt;")
        info_layout.addWidget(self.connection_status)
        
        info_layout.addSpacing(10)
        
        # ============= ML Model Selector =============
        model_label = QLabel("ML Classification Model:")
        model_label.setStyleSheet("font-weight: bold;")
        info_layout.addWidget(model_label)
        
        model_controls = QHBoxLayout()
        
        self.model_combo = QComboBox()
        self.model_combo.setToolTip("Select a trained model for ECG classification")
        self.model_combo.addItem("(No models found)")
        self.model_combo.setEnabled(False)  # Will be enabled when models are loaded
        self.model_combo.currentIndexChanged.connect(self._on_model_changed)
        model_controls.addWidget(self.model_combo, stretch=1)
        
        self.refresh_models_btn = QPushButton("🔄")
        self.refresh_models_btn.setFixedWidth(40)
        self.refresh_models_btn.setToolTip("Refresh model list")
        self.refresh_models_btn.clicked.connect(self._on_refresh_models_clicked)
        model_controls.addWidget(self.refresh_models_btn)
        
        info_layout.addLayout(model_controls)
        
        # Model paths storage for combo data
        self._model_paths = []
        
        info_layout.addSpacing(10)
        
        # Fetch ECG Data button
        self.fetch_btn = QPushButton("📥 Fetch ECG Data")
        self.fetch_btn.setStyleSheet("""
            QPushButton {
                background-color: #0064c8;
                color: white;
                font-weight: bold;
                font-size: 12pt;
                padding: 10px;
                border-radius: 5px;
            }
            QPushButton:hover {
                background-color: #0052a3;
            }
            QPushButton:pressed {
                background-color: #003d7a;
            }
            QPushButton:disabled {
                background-color: #cccccc;
                color: #666666;
            }
        """)
        self.fetch_btn.setToolTip("Request ECG data transmission from ESP32")
        self.fetch_btn.clicked.connect(self.on_fetch_clicked)
        info_layout.addWidget(self.fetch_btn)
        
        info_layout.addSpacing(10)
        
        # Processing results (taller now that ECG Data Info is removed)
        results_label = QLabel("Processing Results:")
        results_label.setStyleSheet("font-weight: bold;")
        info_layout.addWidget(results_label)
        
        self.results_text = QTextEdit()
        self.results_text.setReadOnly(True)
        self.results_text.setPlainText("No processing results yet")
        info_layout.addWidget(self.results_text)
        
        info_layout.addStretch()
        
        info_group.setLayout(info_layout)
        content_layout.addWidget(info_group, stretch=1)
        
        layout.addLayout(content_layout)
        
        # Log area
        log_group = QGroupBox("Event Log")
        log_layout = QVBoxLayout()
        
        self.log_text = QTextEdit()
        self.log_text.setReadOnly(True)
        self.log_text.setMaximumHeight(150)
        log_layout.addWidget(self.log_text)
        
        log_group.setLayout(log_layout)
        layout.addWidget(log_group)
    
    def update_ecg_plot(self, ecg_data: np.ndarray, metadata: dict):
        """
        Update the ECG plot with new data (called from signal).
        
        Args:
            ecg_data: ECG signal array
            metadata: Metadata dictionary
        """
        try:
            self.ecg_data = ecg_data
            self.metadata = metadata
            self.sampling_rate = metadata.get('sampling_rate', 300)
            
            # Create time axis (in seconds)
            self.duration_seconds = len(ecg_data) / self.sampling_rate
            self.time_data = np.linspace(0, self.duration_seconds, len(ecg_data))
            
            # Update plot
            self.plot_line.setData(self.time_data, self.ecg_data)
            
            # Auto-scale Y-axis to fit data
            if len(ecg_data) > 0:
                y_min = np.min(ecg_data)
                y_max = np.max(ecg_data)
                y_margin = (y_max - y_min) * 0.1 if y_max != y_min else 1.0
                self.plot_widget.setYRange(y_min - y_margin, y_max + y_margin)
            
            # Reset time position to start
            self.current_time_position = 0.0
            
            # Apply current time window view
            self.apply_time_window()
            
            # Update info label
            samples = len(ecg_data)
            duration_str = f"{self.duration_seconds:.2f}"
            self.info_label.setText(
                f"ECG Data: {samples} samples, {duration_str}s @ {self.sampling_rate}Hz"
            )
            
            # Clear processing results to prevent showing stale inference
            self.results_text.setHtml("<p style='color: gray;'>Processing...</p>")
            self.results_text.setStyleSheet("")  # Reset background
            
            # Update status
            self.status_bar.showMessage(f"ECG data received: {samples} samples, {duration_str}s")
            self.log_message(f"ECG data updated: {samples} samples, {duration_str}s", "info")
            
        except Exception as e:
            logger.error(f"Error updating ECG plot: {e}", exc_info=True)
            self.log_message(f"Error updating plot: {str(e)}", "error")
    
    def update_processing_results(self, results: dict):
        """
        Update processing results display with enhanced formatting.
        
        Args:
            results: Processing results dictionary
        """
        try:
            if not results or 'results' not in results:
                self.results_text.setHtml("<p style='color: gray;'>No processing results yet</p>")
                self.results_text.setStyleSheet("")  # Reset background
                return
            
            # Check if Atrial Fibrillation is detected
            is_afib = False
            if 'results' in results and 'ml_inference' in results['results']:
                classification = results['results']['ml_inference'].get('classification', '')
                is_afib = (classification == 'A')
            
            # Set background color based on classification
            if is_afib:
                self.results_text.setStyleSheet("background-color: #fff3cd; border: 2px solid #ff9800;")
            else:
                self.results_text.setStyleSheet("")
            
            # Build HTML formatted results
            html = "<div style='font-family: Arial, sans-serif;'>"
            
            # Patient ID at the top (from metadata)
            if 'metadata' in results and 'patient_info' in results['metadata']:
                patient_id = results['metadata']['patient_info'].get('patient_id', 'Unknown')
                html += f"<h3 style='color: #0064c8; margin-top: 5px; margin-bottom: 10px;'>Patient: {patient_id}</h3>"
                html += "<hr style='border: none; border-top: 2px solid #0064c8; margin: 10px 0;'>"
            
            for processor_name, processor_results in results['results'].items():
                
                if 'error' in processor_results:
                    html += f"<h3 style='color: #0064c8; margin-top: 10px; margin-bottom: 5px;'>{processor_name.upper().replace('_', ' ')}</h3>"
                    html += f"<p style='color: red;'>❌ Error: {processor_results['error']}</p>"
                    html += "<hr style='border: none; border-top: 1px solid #ddd; margin: 10px 0;'>"
                    continue
                
                # HEART RATE - Show only BPM
                if processor_name == 'heart_rate':
                    heart_rate_bpm = processor_results.get('heart_rate_bpm', 0)
                    html += f"""
                    <div style='background-color: #f0f8ff; padding: 15px; margin: 10px 0; border-left: 4px solid #0064c8; border-radius: 4px;'>
                        <p style='margin: 0; font-size: 14pt;'><b>Heart Rate:</b> 
                            <span style='font-size: 18pt; color: #0064c8; font-weight: bold;'>{heart_rate_bpm:.1f} BPM</span>
                        </p>
                    </div>
                    """
                
                # ML INFERENCE - Show classification and confidence prominently
                elif processor_name == 'ml_inference':
                    model_loaded = processor_results.get('model_loaded', False)
                    
                    if not model_loaded:
                        html += "<h3 style='color: #0064c8; margin-top: 10px; margin-bottom: 5px;'>ML CLASSIFICATION</h3>"
                        html += "<p style='color: orange;'>⚠️ No model loaded</p>"
                    else:
                        # Classification result - BIG and prominent
                        classification = processor_results.get('classification', 'Unknown')
                        description = processor_results.get('classification_description', '')
                        confidence = processor_results.get('confidence', 0.0)
                        
                        html += f"""
                        <div style='background-color: #f0f8ff; padding: 15px; margin: 10px 0; border-left: 4px solid #0064c8; border-radius: 4px;'>
                            <p style='margin: 0; font-size: 14pt;'><b>Classification:</b> 
                                <span style='font-size: 18pt; color: #0064c8; font-weight: bold;'>{classification}</span>
                            </p>
                            {f"<p style='margin: 5px 0 0 0; color: #666;'>{description}</p>" if description else ""}
                        </div>
                        """
                        
                        # Probabilities table
                        probabilities = processor_results.get('probabilities', None)
                        if probabilities is not None and len(probabilities) > 0:
                            html += "<p style='margin-top: 10px; margin-bottom: 5px;'><b>Class Probabilities:</b></p>"
                            html += "<table style='width: 100%; border-collapse: collapse; font-size: 10pt;'>"
                            html += "<tr style='background-color: #e8e8e8;'><th style='text-align: left; padding: 5px;'>Class</th><th style='text-align: right; padding: 5px;'>Probability</th><th style='padding: 5px;'>Bar</th></tr>"
                            
                            class_names = ['N', 'A', 'O', '~']
                            for i, prob in enumerate(probabilities):
                                class_name = class_names[i] if i < len(class_names) else f"Class {i}"
                                bar_width = int(prob * 100)
                                # Highlight the predicted class
                                bar_color = '#0064c8' if class_name == classification else '#ccc'
                                
                                html += f"""
                                <tr>
                                    <td style='padding: 5px;'><b>{class_name}</b></td>
                                    <td style='text-align: right; padding: 5px;'>{prob*100:.1f}%</td>
                                    <td style='padding: 5px;'>
                                        <div style='background-color: #eee; width: 100%; height: 12px; border-radius: 3px;'>
                                            <div style='background-color: {bar_color}; width: {bar_width}%; height: 100%; border-radius: 3px;'></div>
                                        </div>
                                    </td>
                                </tr>
                                """
                            html += "</table>"
            
            html += "</div>"
            self.results_text.setHtml(html)
            
        except Exception as e:
            logger.error(f"Error updating processing results: {e}", exc_info=True)
            self.results_text.setHtml(f"<p style='color: red;'>Error displaying results: {str(e)}</p>")
    
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
    
    def clear_plot(self):
        """Clear the ECG plot."""
        self.ecg_data = np.array([])
        self.time_data = np.array([])
        self.duration_seconds = 0.0
        self.current_time_position = 0.0
        self.plot_line.setData([], [])
        self.info_label.setText("No ECG data received yet")
        self.status_bar.showMessage("Plot cleared")
        self.log_message("Plot cleared", "info")
    
    def auto_scale_plot(self):
        """Auto-scale the vertical axis (amplitude) to fit all data, preserving time window."""
        if len(self.ecg_data) > 0:
            y_min = np.min(self.ecg_data)
            y_max = np.max(self.ecg_data)
            y_margin = (y_max - y_min) * 0.1 if y_max != y_min else 1.0
            self.plot_widget.setYRange(y_min - y_margin, y_max + y_margin)
            # Preserve X range
            self.apply_time_window()
            self.log_message("Vertical axis auto-scaled", "info")
    
    def set_connection_status(self, connected: bool):
        """
        Update connection status display.
        
        Args:
            connected: True if connected, False otherwise
        """
        if connected:
            self.connection_status.setText("Connected")
            self.connection_status.setStyleSheet("color: green; font-size: 12pt;")
            # Enable fetch button when connected
            self.fetch_btn.setEnabled(True)
            # Enable fetch button when connected
            self.fetch_btn.setEnabled(True)
        else:
            self.connection_status.setText("Disconnected")
            self.connection_status.setStyleSheet("color: red; font-size: 12pt;")
            # Disable fetch button when disconnected
            self.fetch_btn.setEnabled(False)
    
    def on_fetch_clicked(self):
        """Handle fetch button click - request ECG data from ESP32."""
        self.log_message("Requesting ECG data from ESP32...", "info")
        # Emit signal to request ECG data (will be handled by main.py)
        self.signal_emitter.request_ecg_data.emit()
    
    def log_message(self, message: str, level: str = "info"):
        """
        Add a message to the log.
        
        Args:
            message: Message text
            level: Log level (info, warning, error)
        """
        from datetime import datetime
        timestamp = datetime.now().strftime("%H:%M:%S")
        
        color_map = {
            "info": "black",
            "warning": "orange",
            "error": "red",
            "success": "green"
        }
        
        color = color_map.get(level, "black")
        formatted_message = f'<span style="color: {color};">[{timestamp}] {message}</span>'
        
        self.log_text.append(formatted_message)
        
        # Auto-scroll to bottom
        scrollbar = self.log_text.verticalScrollBar()
        scrollbar.setValue(scrollbar.maximum())
    
    def update_status(self):
        """Update status bar periodically."""
        if len(self.ecg_data) > 0:
            duration = len(self.ecg_data) / self.sampling_rate
            self.status_bar.showMessage(
                f"ECG data: {len(self.ecg_data)} samples, {duration:.2f}s @ {self.sampling_rate}Hz"
            )
    
    def update_models_list(self, models: list):
        """
        Update the model dropdown with available models.
        
        Args:
            models: List of model dicts from MLInferenceProcessor.get_available_models()
        """
        self.model_combo.blockSignals(True)  # Prevent signal during update
        self.model_combo.clear()
        self._model_paths = []
        
        if models:
            for model in models:
                filename = model.get('filename', 'Unknown')
                model_type = model.get('type', 'unknown')
                display_name = f"{filename} [{model_type}]"
                self.model_combo.addItem(display_name)
                self._model_paths.append(model.get('path', ''))
            
            self.model_combo.setEnabled(True)
            self.log_message(f"Found {len(models)} model(s)", "success")
            
            # Auto-select ECG-DualNet as default (look for it in the list)
            dualnet_idx = -1
            for i, model in enumerate(models):
                filename = model.get('filename', '')
                if 'ECG-DualNet' in filename or 'dualnet' in filename.lower():
                    dualnet_idx = i
                    break
            
            if dualnet_idx >= 0:
                self.model_combo.setCurrentIndex(dualnet_idx)
                self.log_message("ECG-DualNet auto-selected as default", "info")
        else:
            self.model_combo.addItem("(No models found - copy to EDGE/models/)")
            self._model_paths = []
            self.model_combo.setEnabled(False)
            self.log_message("No models found in models directory", "warning")
        
        self.model_combo.blockSignals(False)
    
    def _on_model_changed(self, index: int):
        """Handle model selection change."""
        if index >= 0 and index < len(self._model_paths):
            model_path = self._model_paths[index]
            if model_path:
                self.signal_emitter.model_changed.emit(model_path)
                self.log_message(f"Model selected: {self.model_combo.currentText()}", "info")
    
    def _on_refresh_models_clicked(self):
        """Handle refresh models button click."""
        self.log_message("Refreshing model list...", "info")
        # Emit a special signal or just log - actual refresh done by main.py
        # For now we'll just log, main.py will need to call update_models_list
        self.signal_emitter.model_changed.emit("__REFRESH__")
    
    def get_signal_emitter(self) -> ECGDataSignal:
        """Get the signal emitter for thread-safe updates."""
        return self.signal_emitter

