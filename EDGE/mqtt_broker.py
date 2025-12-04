"""
Embedded MQTT Broker for EDGE Layer (Pi4)
Runs a lightweight MQTT broker on Pi4 to receive ECG data from ESP32.
"""

import asyncio
import logging
import socket
import netifaces
from threading import Thread
from amqtt.broker import Broker

from config import (
    MQTT_BROKER_PORT,
    BROKER_DISCOVERY_PORT,
    BROKER_DISCOVERY_MAGIC,
    BROKER_DISCOVERY_RESPONSE_PREFIX
)

logger = logging.getLogger(__name__)


class EDGEMQTTBroker:
    """Embedded MQTT broker for EDGE layer using aMQTT."""
    
    def __init__(self, host: str = "0.0.0.0", port: int = MQTT_BROKER_PORT):
        """
        Initialize embedded MQTT broker.
        
        Args:
            host: Bind address (default: 0.0.0.0 for all interfaces)
            port: Port to listen on (default: 1883)
        """
        self.host = host
        self.port = port
        self.broker = None
        self.thread = None
        self.loop = None
        self.running = False
        self.discovery_thread = None
        self.discovery_socket = None
        self.discovery_running = False
        
        # Broker configuration
        self.config = {
            'listeners': {
                'default': {
                    'type': 'tcp',
                    'bind': f'{host}:{port}',
                }
            },
            'sys_interval': 0,  # Disable system messages
            'auth': {
                'allow-anonymous': True,
                'password-file': None
            },
            'topic-check': {
                'enabled': False
            }
        }
    
    def _get_broker_ip(self, sender_ip: str = None) -> str:
        """
        Get the broker IP address.
        
        Args:
            sender_ip: IP address of the discovery request sender (for network matching)
            
        Returns:
            Broker IP address string
        """
        if self.host != "0.0.0.0":
            return self.host
        
        # If listening on all interfaces, determine which IP to use
        try:
            # Try using netifaces to find matching interface
            if sender_ip:
                sender_network = '.'.join(sender_ip.split('.')[:-1])  # First 3 octets
                
                for interface in netifaces.interfaces():
                    addrs = netifaces.ifaddresses(interface)
                    if netifaces.AF_INET in addrs:
                        for addr_info in addrs[netifaces.AF_INET]:
                            ip = addr_info.get('addr')
                            if ip and ip.startswith(sender_network):
                                return ip
            
            # Fallback: find first non-loopback IPv4 address
            for interface in netifaces.interfaces():
                addrs = netifaces.ifaddresses(interface)
                if netifaces.AF_INET in addrs:
                    for addr_info in addrs[netifaces.AF_INET]:
                        ip = addr_info.get('addr')
                        if ip and ip != '127.0.0.1':
                            return ip
        except Exception as e:
            logger.warning(f"Error determining broker IP: {e}")
        
        # Last resort: use hostname or localhost
        try:
            hostname = socket.gethostname()
            return socket.gethostbyname(hostname)
        except:
            return "127.0.0.1"
    
    async def _start_broker(self):
        """Async function to start the broker."""
        try:
            self.broker = Broker(self.config)
            await self.broker.start()
            logger.info(f"✅ EDGE MQTT broker started on {self.host}:{self.port}")
            self.running = True
        except Exception as e:
            logger.error(f"❌ Broker error: {e}")
            self.running = False
            raise
    
    def _run_broker(self):
        """Run broker in separate thread with its own event loop."""
        try:
            # Create new event loop for this thread
            self.loop = asyncio.new_event_loop()
            asyncio.set_event_loop(self.loop)
            
            # Start broker
            self.loop.run_until_complete(self._start_broker())
            
            # Keep broker running
            if self.running:
                self.loop.run_forever()
            
        except Exception as e:
            logger.error(f"❌ Broker thread error: {e}")
            self.running = False
    
    def _run_discovery(self):
        """Run UDP discovery responder in separate thread."""
        try:
            # Create UDP socket
            self.discovery_socket = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
            self.discovery_socket.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
            self.discovery_socket.setsockopt(socket.SOL_SOCKET, socket.SO_BROADCAST, 1)
            self.discovery_socket.bind(('', BROKER_DISCOVERY_PORT))  # Uses port 1886 from config
            self.discovery_socket.settimeout(1.0)  # 1 second timeout for recvfrom
            
            # Get the actual bound address
            bound_addr = self.discovery_socket.getsockname()
            logger.info(f"✓ UDP discovery responder started on {bound_addr[0]}:{bound_addr[1]}")
            logger.info(f"  Listening for discovery requests on port {BROKER_DISCOVERY_PORT}")
            logger.info(f"  Waiting for magic string: {BROKER_DISCOVERY_MAGIC}")
            
            while self.discovery_running:
                try:
                    # Wait for discovery request
                    data, addr = self.discovery_socket.recvfrom(1024)
                    
                    # Log received data for debugging
                    logger.debug(f"Discovery request received from {addr[0]}:{addr[1]}, data: {data}")
                    
                    # Check if this is a valid discovery request
                    if data == BROKER_DISCOVERY_MAGIC:
                        logger.info(f"Valid discovery request from {addr[0]}, preparing response...")
                        
                        # Get the broker IP (matching the sender's network if possible)
                        broker_ip = self._get_broker_ip(addr[0])
                        
                        # Send response: "ECG_MQTT_BROKER_RESPONSE:IP:PORT"
                        response = f"{BROKER_DISCOVERY_RESPONSE_PREFIX}:{broker_ip}:{self.port}"
                        self.discovery_socket.sendto(response.encode('utf-8'), addr)
                        logger.info(f"Sent discovery response to {addr[0]}: {response}")
                
                except socket.timeout:
                    # Normal timeout, continue
                    continue
                except Exception as e:
                    if self.discovery_running:  # Only log if we're supposed to be running
                        logger.warning(f"Error in discovery responder: {e}")
        
        except Exception as e:
            logger.error(f"Failed to start discovery responder: {e}")
            self.discovery_running = False
    
    def start(self):
        """
        Start the MQTT broker and discovery responder.
        
        Returns:
            True if started successfully, False otherwise
        """
        if self.running:
            logger.warning("Broker is already running")
            return True
        
        try:
            # Start broker in background thread
            self.thread = Thread(target=self._run_broker, daemon=True)
            self.thread.start()
            
            # Wait a bit for broker to start
            import time
            time.sleep(1.0)
            
            if not self.running:
                logger.error("Failed to start broker")
                return False
            
            # Start discovery responder
            self.discovery_running = True
            self.discovery_thread = Thread(target=self._run_discovery, daemon=True)
            self.discovery_thread.start()
            
            logger.info("✅ EDGE MQTT broker and discovery responder started")
            return True
            
        except Exception as e:
            logger.error(f"Failed to start broker: {e}", exc_info=True)
            return False
    
    def stop(self):
        """Stop the MQTT broker and discovery responder."""
        logger.info("Stopping EDGE MQTT broker...")
        
        self.discovery_running = False
        
        if self.discovery_socket:
            try:
                self.discovery_socket.close()
            except:
                pass
            self.discovery_socket = None
        
        self.running = False
        
        if self.loop and self.loop.is_running():
            self.loop.call_soon_threadsafe(self.loop.stop)
        
        if self.thread and self.thread.is_alive():
            self.thread.join(timeout=2.0)
        
        logger.info("✓ EDGE MQTT broker stopped")
    
    def is_running(self) -> bool:
        """Check if broker is running."""
        return self.running

