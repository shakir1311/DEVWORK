"""
MQTT Broker Discovery Module
Implements UDP-based broker discovery similar to Simulator-ESP32 pattern.
"""

import socket
import logging
import time
from typing import Optional, Tuple

from config import (
    BROKER_DISCOVERY_PORT,
    BROKER_DISCOVERY_MAGIC,
    BROKER_DISCOVERY_RESPONSE_PREFIX,
    BROKER_DISCOVERY_TIMEOUT
)

logger = logging.getLogger(__name__)


class MQTTBrokerDiscovery:
    """Handles UDP-based MQTT broker discovery."""
    
    def __init__(self):
        """Initialize broker discovery."""
        self.discovered_broker: Optional[Tuple[str, int]] = None
    
    def discover_broker(self, timeout: float = BROKER_DISCOVERY_TIMEOUT) -> Optional[Tuple[str, int]]:
        """
        Discover MQTT broker via UDP broadcast.
        
        Args:
            timeout: Timeout in seconds to wait for broker response
            
        Returns:
            Tuple of (broker_ip, broker_port) if found, None otherwise
        """
        logger.info("Starting MQTT broker discovery...")
        
        try:
            # Create UDP socket
            udp_socket = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
            udp_socket.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
            udp_socket.setsockopt(socket.SOL_SOCKET, socket.SO_BROADCAST, 1)
            udp_socket.settimeout(1.0)  # 1 second timeout for recvfrom
            
            # Send broadcast discovery packet
            broadcast_ip = "255.255.255.255"
            udp_socket.sendto(BROKER_DISCOVERY_MAGIC, (broadcast_ip, BROKER_DISCOVERY_PORT))
            logger.info(f"Sent discovery broadcast to {broadcast_ip}:{BROKER_DISCOVERY_PORT}")
            
            # Wait for response
            start_time = time.time()
            while time.time() - start_time < timeout:
                try:
                    data, addr = udp_socket.recvfrom(1024)
                    response = data.decode('utf-8', errors='ignore')
                    
                    logger.debug(f"Received discovery response from {addr[0]}: {response}")
                    
                    # Check if this is a valid discovery response
                    if response.startswith(BROKER_DISCOVERY_RESPONSE_PREFIX):
                        # Parse response: "ECG_MQTT_BROKER_RESPONSE:IP:PORT"
                        parts = response.split(':')
                        if len(parts) >= 3:
                            broker_ip = parts[1]
                            broker_port = int(parts[2])
                            
                            logger.info(f"✓ Discovered MQTT broker at {broker_ip}:{broker_port}")
                            self.discovered_broker = (broker_ip, broker_port)
                            udp_socket.close()
                            return self.discovered_broker
                
                except socket.timeout:
                    # Continue waiting
                    continue
                except Exception as e:
                    logger.warning(f"Error receiving discovery response: {e}")
                    continue
            
            logger.warning(f"Broker discovery timeout after {timeout} seconds")
            udp_socket.close()
            return None
            
        except Exception as e:
            logger.error(f"Error during broker discovery: {e}", exc_info=True)
            return None
    
    def get_discovered_broker(self) -> Optional[Tuple[str, int]]:
        """
        Get the last discovered broker.
        
        Returns:
            Tuple of (broker_ip, broker_port) if previously discovered, None otherwise
        """
        return self.discovered_broker


class MQTTBrokerDiscoveryResponder:
    """Responds to UDP discovery requests (for Pi4 MQTT broker)."""
    
    def __init__(self, broker_ip: str, broker_port: int):
        """
        Initialize discovery responder.
        
        Args:
            broker_ip: IP address of the MQTT broker
            broker_port: Port of the MQTT broker
        """
        self.broker_ip = broker_ip
        self.broker_port = broker_port
        self.running = False
        self.socket: Optional[socket.socket] = None
    
    def start(self) -> bool:
        """
        Start the discovery responder.
        
        Returns:
            True if started successfully, False otherwise
        """
        try:
            self.socket = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
            self.socket.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
            self.socket.setsockopt(socket.SOL_SOCKET, socket.SO_BROADCAST, 1)
            self.socket.bind(('', BROKER_DISCOVERY_PORT))
            self.socket.settimeout(1.0)
            
            self.running = True
            logger.info(f"✓ UDP discovery responder started on port {BROKER_DISCOVERY_PORT}")
            logger.info(f"  Listening for discovery requests")
            logger.info(f"  Will respond with: {self.broker_ip}:{self.broker_port}")
            return True
            
        except Exception as e:
            logger.error(f"Failed to start discovery responder: {e}")
            return False
    
    def stop(self):
        """Stop the discovery responder."""
        self.running = False
        if self.socket:
            self.socket.close()
            self.socket = None
        logger.info("Discovery responder stopped")
    
    def process_requests(self):
        """Process discovery requests (call this in a loop or thread)."""
        if not self.running or not self.socket:
            return
        
        try:
            data, addr = self.socket.recvfrom(1024)
            
            if data == BROKER_DISCOVERY_MAGIC:
                logger.info(f"Discovery request received from {addr[0]}:{addr[1]}")
                
                # Send response: "ECG_MQTT_BROKER_RESPONSE:IP:PORT"
                response = f"{BROKER_DISCOVERY_RESPONSE_PREFIX}:{self.broker_ip}:{self.broker_port}"
                self.socket.sendto(response.encode('utf-8'), addr)
                logger.info(f"Sent discovery response to {addr[0]}: {response}")
        
        except socket.timeout:
            # Normal timeout, continue
            pass
        except Exception as e:
            if self.running:  # Only log if we're supposed to be running
                logger.warning(f"Error processing discovery request: {e}")

