"""
MQTT Client Module
Handles MQTT connection, subscription, and message reception.
"""

import logging
import threading
import time
from typing import Optional, Callable
import paho.mqtt.client as mqtt

from config import (
    MQTT_BROKER_PORT,
    MQTT_CHUNK_TOPIC,
    MQTT_ACK_TOPIC,
    MQTT_COMMAND_TOPIC,
    MQTT_CLIENT_ID,
    BROKER_DISCOVERY_PORT
)
from mqtt_discovery import MQTTBrokerDiscovery

logger = logging.getLogger(__name__)


class MQTTClient:
    """MQTT client wrapper with automatic broker discovery."""
    
    def __init__(self, on_message_callback: Optional[Callable] = None):
        """
        Initialize MQTT client.
        
        Args:
            on_message_callback: Callback function for received messages
                                Signature: callback(topic: str, payload: bytes)
        """
        self.client: Optional[mqtt.Client] = None
        self.broker_ip: Optional[str] = None
        self.broker_port = MQTT_BROKER_PORT
        self.connected = False
        self.on_message_callback = on_message_callback
        self.discovery = MQTTBrokerDiscovery()
        self.reconnect_delay = 5.0  # seconds
        self.last_reconnect_attempt = 0.0
    
    def discover_and_connect(self) -> bool:
        """
        Discover broker and connect to MQTT.
        
        Returns:
            True if connected successfully, False otherwise
        """
        # Discover broker
        broker_info = self.discovery.discover_broker()
        if not broker_info:
            logger.error("Failed to discover MQTT broker")
            return False
        
        self.broker_ip, self.broker_port = broker_info
        return self.connect()
    
    def connect(self, broker_ip: Optional[str] = None, broker_port: Optional[int] = None) -> bool:
        """
        Connect to MQTT broker.
        
        Args:
            broker_ip: Broker IP address (uses discovered broker if None)
            broker_port: Broker port (uses default if None)
            
        Returns:
            True if connected successfully, False otherwise
        """
        if broker_ip:
            self.broker_ip = broker_ip
        if broker_port:
            self.broker_port = broker_port
        
        if not self.broker_ip:
            logger.error("No broker IP available. Use discover_and_connect() or provide broker_ip")
            return False
        
        try:
            # Create MQTT client
            self.client = mqtt.Client(client_id=MQTT_CLIENT_ID)
            self.client.on_connect = self._on_connect
            self.client.on_disconnect = self._on_disconnect
            self.client.on_message = self._on_message
            
            # Connect to broker
            logger.info(f"Connecting to MQTT broker at {self.broker_ip}:{self.broker_port}...")
            self.client.connect(self.broker_ip, self.broker_port, keepalive=60)
            
            # Start network loop in background thread
            self.client.loop_start()
            
            # Wait for connection (with timeout)
            timeout = 10.0
            start_time = time.time()
            while not self.connected and (time.time() - start_time) < timeout:
                time.sleep(0.1)
            
            if self.connected:
                logger.info(f"✓ Connected to MQTT broker at {self.broker_ip}:{self.broker_port}")
                return True
            else:
                logger.error("MQTT connection timeout")
                self.client.loop_stop()
                return False
                
        except Exception as e:
            logger.error(f"Failed to connect to MQTT broker: {e}", exc_info=True)
            if self.client:
                self.client.loop_stop()
            return False
    
    def _on_connect(self, client, userdata, flags, rc):
        """Callback when MQTT client connects."""
        if rc == 0:
            self.connected = True
            logger.info("MQTT client connected")
            
            # Subscribe to chunk topic
            result, mid = client.subscribe(MQTT_CHUNK_TOPIC, qos=1)
            if result == mqtt.MQTT_ERR_SUCCESS:
                logger.info(f"✓ Subscribed to topic: {MQTT_CHUNK_TOPIC} (QoS 1)")
            else:
                logger.error(f"Failed to subscribe to {MQTT_CHUNK_TOPIC}, error code: {result}")
        else:
            self.connected = False
            logger.error(f"MQTT connection failed with code {rc}")
    
    def _on_disconnect(self, client, userdata, rc):
        """Callback when MQTT client disconnects."""
        self.connected = False
        if rc != 0:
            logger.warning(f"MQTT disconnected unexpectedly (code {rc})")
        else:
            logger.info("MQTT disconnected")
    
    def _on_message(self, client, userdata, msg):
        """Callback when MQTT message is received."""
        if self.on_message_callback:
            try:
                self.on_message_callback(msg.topic, msg.payload)
            except Exception as e:
                logger.error(f"Error in message callback: {e}", exc_info=True)
    
    def send_ack(self, chunk_num: int) -> bool:
        """
        Send acknowledgment for a received chunk.
        
        Args:
            chunk_num: Chunk number to acknowledge
            
        Returns:
            True if ACK sent successfully, False otherwise
        """
        if not self.connected or not self.client:
            return False
        
        try:
            ack_payload = str(chunk_num).encode('utf-8')
            result = self.client.publish(MQTT_ACK_TOPIC, ack_payload, qos=0)
            return result.rc == mqtt.MQTT_ERR_SUCCESS
        except Exception as e:
            logger.error(f"Failed to send ACK: {e}")
            return False
    
    def send_command(self, command: str) -> bool:
        """
        Send command to ESP32 (e.g., request ECG data transmission).
        
        Args:
            command: Command string (e.g., "TRANSMIT", "SEND", "START")
            
        Returns:
            True if command sent successfully, False otherwise
        """
        if not self.connected or not self.client:
            logger.warning("Cannot send command: MQTT client not connected")
            return False
        
        try:
            command_payload = command.encode('utf-8')
            result = self.client.publish(MQTT_COMMAND_TOPIC, command_payload, qos=1)
            if result.rc == mqtt.MQTT_ERR_SUCCESS:
                logger.info(f"Sent command to ESP32: {command}")
                return True
            else:
                logger.error(f"Failed to send command, error code: {result.rc}")
                return False
        except Exception as e:
            logger.error(f"Failed to send command: {e}")
            return False
    
    def maintain_connection(self):
        """Maintain MQTT connection (call this periodically)."""
        if not self.connected:
            current_time = time.time()
            if current_time - self.last_reconnect_attempt >= self.reconnect_delay:
                self.last_reconnect_attempt = current_time
                logger.info("Attempting to reconnect to MQTT broker...")
                self.discover_and_connect()
    
    def disconnect(self):
        """Disconnect from MQTT broker."""
        if self.client:
            self.client.loop_stop()
            self.client.disconnect()
            self.connected = False
            logger.info("MQTT client disconnected")

