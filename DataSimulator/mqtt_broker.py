"""
Embedded MQTT Broker using aMQTT
Runs a lightweight MQTT broker as part of the application - no external setup needed!
"""

import asyncio
import logging
import socket
from threading import Thread
from amqtt.broker import Broker

logging.basicConfig(level=logging.INFO)  # Changed to INFO to see discovery messages
logger = logging.getLogger(__name__)


class EmbeddedMQTTBroker:
    """Embedded MQTT broker using aMQTT (Python 3.13 compatible)."""
    
    def __init__(self, host: str = "127.0.0.1", port: int = 1883):
        """
        Initialize embedded MQTT broker.
        
        Args:
            host: Bind address (default: 127.0.0.1 for localhost only)
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
    
    async def _start_broker(self):
        """Async function to start the broker."""
        try:
            self.broker = Broker(self.config)
            await self.broker.start()
            logger.info(f"✅ Embedded MQTT broker started on {self.host}:{self.port}")
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
        DISCOVERY_PORT = 1884
        DISCOVERY_MAGIC = b"ECG_MQTT_BROKER"
        DISCOVERY_RESPONSE = "ECG_MQTT_BROKER_RESPONSE"
        
        try:
            # Create UDP socket
            self.discovery_socket = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
            self.discovery_socket.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
            self.discovery_socket.setsockopt(socket.SOL_SOCKET, socket.SO_BROADCAST, 1)
            self.discovery_socket.bind(('', DISCOVERY_PORT))
            self.discovery_socket.settimeout(1.0)  # 1 second timeout for recvfrom
            
            # Get the actual bound address
            bound_addr = self.discovery_socket.getsockname()
            logger.info(f"✓ UDP discovery responder started on {bound_addr[0]}:{bound_addr[1]}")
            logger.info(f"  Listening for discovery requests on port {DISCOVERY_PORT}")
            logger.info(f"  Waiting for magic string: {DISCOVERY_MAGIC}")
            
            while self.discovery_running:
                try:
                    # Wait for discovery request
                    data, addr = self.discovery_socket.recvfrom(1024)
                    
                    # Log received data for debugging
                    logger.info(f"Discovery request received from {addr[0]}:{addr[1]}, data: {data}")
                    
                    # Check if this is a valid discovery request
                    if data == DISCOVERY_MAGIC:
                        logger.info(f"Valid discovery request from {addr[0]}, preparing response...")
                        # Get the actual broker IP (not 0.0.0.0)
                        broker_ip = self.host
                        sender_ip = addr[0]
                        
                        if broker_ip == "0.0.0.0":
                            # If listening on all interfaces, determine which interface received the packet
                            # For local network without internet, use the sender's IP to find matching interface
                            try:
                                # Try using netifaces first (most reliable, no internet needed)
                                try:
                                    import netifaces
                                    # Find the interface that matches the network of the sender
                                    sender_network = '.'.join(sender_ip.split('.')[:-1])  # First 3 octets
                                    
                                    # Try to find matching interface on same network
                                    for interface in netifaces.interfaces():
                                        addrs = netifaces.ifaddresses(interface)
                                        if netifaces.AF_INET in addrs:
                                            for addr_info in addrs[netifaces.AF_INET]:
                                                ip = addr_info.get('addr')
                                                if ip and not ip.startswith('127.'):
                                                    ip_network = '.'.join(ip.split('.')[:-1])
                                                    if ip_network == sender_network:
                                                        broker_ip = ip
                                                        break
                                        if broker_ip != "0.0.0.0":
                                            break
                                    
                                    # Fallback: use first non-loopback interface
                                    if broker_ip == "0.0.0.0":
                                        for interface in netifaces.interfaces():
                                            addrs = netifaces.ifaddresses(interface)
                                            if netifaces.AF_INET in addrs:
                                                for addr_info in addrs[netifaces.AF_INET]:
                                                    ip = addr_info.get('addr')
                                                    if ip and not ip.startswith('127.'):
                                                        broker_ip = ip
                                                        break
                                            if broker_ip != "0.0.0.0":
                                                break
                                except ImportError:
                                    # netifaces not available, use socket method (works on local network)
                                    try:
                                        # Create a UDP socket and "connect" to sender's IP
                                        # This doesn't actually connect, but binds to the correct interface
                                        s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
                                        try:
                                            # Connect to sender's IP to determine which interface to use
                                            s.connect((sender_ip, 1))
                                            broker_ip = s.getsockname()[0]
                                        except:
                                            # Fallback: try router IP (192.168.0.254)
                                            try:
                                                s.connect(("192.168.0.254", 1))
                                                broker_ip = s.getsockname()[0]
                                            except:
                                                # Last fallback: try local broadcast
                                                try:
                                                    s.connect(("255.255.255.255", 1))
                                                    broker_ip = s.getsockname()[0]
                                                except:
                                                    broker_ip = "127.0.0.1"
                                        s.close()
                                    except Exception as e:
                                        logger.warning(f"Socket method failed: {e}")
                                        broker_ip = "127.0.0.1"  # Final fallback
                            except Exception as e:
                                logger.warning(f"Error determining broker IP: {e}")
                                broker_ip = "127.0.0.1"  # Fallback
                        
                        # Send response: "ECG_MQTT_BROKER_RESPONSE:IP:PORT"
                        response = f"{DISCOVERY_RESPONSE}:{broker_ip}:{self.port}"
                        self.discovery_socket.sendto(response.encode('utf-8'), addr)
                        logger.info(f"✓ Sent discovery response to {addr[0]}:{addr[1]}: {response}")
                    else:
                        logger.warning(f"Invalid discovery request from {addr[0]}: expected '{DISCOVERY_MAGIC}', got '{data}'")
                        
                except socket.timeout:
                    # Timeout is expected, continue listening
                    continue
                except Exception as e:
                    if self.discovery_running:
                        logger.warning(f"Discovery responder error: {e}")
                    break
            
        except Exception as e:
            logger.error(f"Failed to start discovery responder: {e}")
        finally:
            if self.discovery_socket:
                try:
                    self.discovery_socket.close()
                except:
                    pass
                self.discovery_socket = None
    
    def start(self) -> bool:
        """
        Start the embedded MQTT broker in background thread.
        
        Returns:
            True if started successfully, False otherwise
        """
        try:
            self.thread = Thread(target=self._run_broker, daemon=True)
            self.thread.start()
            
            # Start UDP discovery responder
            self.discovery_running = True
            self.discovery_thread = Thread(target=self._run_discovery, daemon=True)
            self.discovery_thread.start()
            
            # Wait a bit for broker to initialize
            import time
            time.sleep(1)
            
            if self.running:
                print(f"✅ Embedded MQTT broker running on {self.host}:{self.port}")
                print(f"✅ UDP discovery responder active on port 1884")
                return True
            else:
                print(f"❌ Failed to start embedded MQTT broker")
                return False
                
        except Exception as e:
            logger.error(f"Failed to start broker: {e}")
            print(f"❌ Failed to start broker: {e}")
            return False
    
    def stop(self):
        """Stop the embedded MQTT broker and ensure port is released."""
        try:
            if not self.running:
                logger.info("Broker already stopped")
                return
            
            self.running = False
            
            # Stop discovery responder
            self.discovery_running = False
            if self.discovery_thread and self.discovery_thread.is_alive():
                self.discovery_thread.join(timeout=2)
            if self.discovery_socket:
                try:
                    self.discovery_socket.close()
                except:
                    pass
                self.discovery_socket = None
            self.discovery_thread = None
            
            if self.loop and self.broker:
                # Schedule broker shutdown in its event loop
                future = asyncio.run_coroutine_threadsafe(self.broker.shutdown(), self.loop)
                
                # Wait for shutdown to complete (with timeout)
                try:
                    future.result(timeout=3)  # Wait up to 3 seconds for shutdown
                except Exception as e:
                    logger.warning(f"Broker shutdown timeout or error: {e}")
                
                # Stop the event loop
                if self.loop.is_running():
                    self.loop.call_soon_threadsafe(self.loop.stop)
            
            # Wait for thread to finish (with longer timeout)
            if self.thread and self.thread.is_alive():
                self.thread.join(timeout=5)  # Wait up to 5 seconds
            
            # Clean up references
            if self.loop:
                try:
                    # Close the event loop to ensure all resources are released
                    if not self.loop.is_closed():
                        # Cancel any remaining tasks
                        pending = asyncio.all_tasks(self.loop)
                        for task in pending:
                            task.cancel()
                        # Close the loop
                        self.loop.close()
                except Exception as e:
                    logger.warning(f"Error closing event loop: {e}")
            
            self.broker = None
            self.loop = None
            self.thread = None
            
            logger.info("Embedded MQTT broker stopped and port released")
            
        except Exception as e:
            logger.error(f"Error stopping broker: {e}")
            # Force cleanup even if there was an error
            self.running = False
            self.broker = None
            self.loop = None
            self.thread = None


def main():
    """Test the embedded broker."""
    import time
    
    print("Testing Embedded MQTT Broker (aMQTT)")
    print("=" * 50)
    
    broker = EmbeddedMQTTBroker()
    
    if broker.start():
        print("\nBroker is running!")
        print("You can now connect MQTT clients to localhost:1883")
        print("\nPress Ctrl+C to stop...")
        
        try:
            while True:
                time.sleep(1)
        except KeyboardInterrupt:
            print("\n\nStopping broker...")
            broker.stop()
            print("Broker stopped.")
    else:
        print("Failed to start broker!")


if __name__ == "__main__":
    main()
