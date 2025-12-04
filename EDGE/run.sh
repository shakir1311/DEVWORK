#!/bin/bash
# Run script for EDGE Layer (ECG Data Processor)
# Checks/installs requirements and runs the application

set -e  # Exit on error

echo "╔════════════════════════════════════════════════════════════╗"
echo "║         EDGE Layer - ECG Data Processor                   ║"
echo "║         Auto Run Script                                    ║"
echo "╚════════════════════════════════════════════════════════════╝"
echo ""

# Get the script directory (works even if called from elsewhere)
SCRIPT_DIR="$( cd "$( dirname "${BASH_SOURCE[0]}" )" && pwd )"
cd "$SCRIPT_DIR"

echo "📁 Working directory: $SCRIPT_DIR"
echo ""

# ============================================================================
# 1. Check Python Installation
# ============================================================================
echo "🔍 Step 1: Checking Python installation..."

if ! command -v python3 &> /dev/null; then
    echo "❌ Error: Python 3 is not installed"
    echo ""
    echo "Please install Python 3.8 or higher:"
    echo "  macOS:   brew install python3"
    echo "  Ubuntu:  sudo apt-get install python3 python3-pip python3-venv"
    echo "  Pi4:     sudo apt-get install python3 python3-pip python3-venv"
    exit 1
fi

PYTHON_VERSION=$(python3 --version 2>&1 | awk '{print $2}')
echo "✅ Python found: $PYTHON_VERSION"

# Check Python version (minimum 3.8)
PYTHON_MAJOR=$(python3 -c 'import sys; print(sys.version_info.major)')
PYTHON_MINOR=$(python3 -c 'import sys; print(sys.version_info.minor)')

if [ "$PYTHON_MAJOR" -lt 3 ] || ([ "$PYTHON_MAJOR" -eq 3 ] && [ "$PYTHON_MINOR" -lt 8 ]); then
    echo "❌ Error: Python 3.8+ required, found $PYTHON_VERSION"
    exit 1
fi

echo ""

# ============================================================================
# 2. Check/Create Virtual Environment
# ============================================================================
echo "🔍 Step 2: Checking virtual environment..."

# Set paths to venv Python and pip
PYTHON="${SCRIPT_DIR}/venv/bin/python"
PIP="${SCRIPT_DIR}/venv/bin/pip"

# Check if venv exists and is valid
if [ ! -d "venv" ]; then
    echo "📦 Virtual environment not found. Creating..."
    python3 -m venv venv
    if [ $? -eq 0 ]; then
        echo "✅ Virtual environment created successfully"
    else
        echo "❌ Failed to create virtual environment"
        exit 1
    fi
elif [ ! -f "$PYTHON" ] || [ ! -f "$PIP" ]; then
    echo "⚠️  Virtual environment exists but appears incomplete or broken"
    echo "📦 Recreating virtual environment..."
    rm -rf venv
    python3 -m venv venv
    if [ $? -eq 0 ]; then
        echo "✅ Virtual environment recreated successfully"
    else
        echo "❌ Failed to recreate virtual environment"
        exit 1
    fi
else
    echo "✅ Virtual environment exists and is valid"
fi

echo ""

# ============================================================================
# 3. Activate Virtual Environment
# ============================================================================
echo "🔍 Step 3: Activating virtual environment..."

source venv/bin/activate

if [ $? -eq 0 ]; then
    echo "✅ Virtual environment activated"
else
    echo "❌ Failed to activate virtual environment"
    exit 1
fi

# Verify venv Python and pip exist (double-check after activation)
if [ ! -f "$PYTHON" ]; then
    echo "❌ Error: Virtual environment Python not found at $PYTHON"
    echo "   The virtual environment may be corrupted. Try deleting it and running again."
    exit 1
fi

if [ ! -f "$PIP" ]; then
    echo "❌ Error: Virtual environment pip not found at $PIP"
    echo "   The virtual environment may be corrupted. Try deleting it and running again."
    exit 1
fi

echo ""

# ============================================================================
# 4. Check/Install Dependencies
# ============================================================================
echo "🔍 Step 4: Checking dependencies..."

# Check if requirements.txt exists
if [ ! -f "requirements.txt" ]; then
    echo "❌ Error: requirements.txt not found"
    exit 1
fi

# Function to check if a package is installed (using venv Python)
check_package() {
    "$PYTHON" -c "import $1" 2>/dev/null
    return $?
}

# Check key packages
NEED_INSTALL=false

echo "  Checking paho-mqtt..."
if ! check_package "paho.mqtt.client"; then
    echo "  ❌ paho-mqtt not installed"
    NEED_INSTALL=true
else
    echo "  ✅ paho-mqtt installed"
fi

echo "  Checking numpy..."
if ! check_package "numpy"; then
    echo "  ❌ numpy not installed"
    NEED_INSTALL=true
else
    echo "  ✅ numpy installed"
fi

echo "  Checking amqtt..."
if ! check_package "amqtt"; then
    echo "  ❌ amqtt not installed"
    NEED_INSTALL=true
else
    echo "  ✅ amqtt installed"
fi

echo "  Checking netifaces..."
if ! check_package "netifaces"; then
    echo "  ❌ netifaces not installed"
    NEED_INSTALL=true
else
    echo "  ✅ netifaces installed"
fi

echo "  Checking PyQt6 (GUI)..."
if ! check_package "PyQt6"; then
    echo "  ❌ PyQt6 not installed (GUI will be disabled)"
    NEED_INSTALL=true
else
    echo "  ✅ PyQt6 installed"
fi

echo "  Checking pyqtgraph (GUI)..."
if ! check_package "pyqtgraph"; then
    echo "  ❌ pyqtgraph not installed (GUI will be disabled)"
    NEED_INSTALL=true
else
    echo "  ✅ pyqtgraph installed"
fi

# Install dependencies if needed
if [ "$NEED_INSTALL" = true ]; then
    echo ""
    echo "📦 Installing missing dependencies..."
    "$PIP" install --upgrade pip -q
    "$PIP" install -r requirements.txt
    
    if [ $? -eq 0 ]; then
        echo "✅ Dependencies installed successfully"
    else
        echo "❌ Failed to install dependencies"
        exit 1
    fi
else
    echo "✅ All dependencies already installed"
fi

echo ""

# ============================================================================
# 5. Check Application Files
# ============================================================================
echo "🔍 Step 5: Checking application files..."

REQUIRED_FILES=(
    "main.py"
    "config.py"
    "mqtt_broker.py"
    "mqtt_client.py"
    "mqtt_discovery.py"
    "chunk_receiver.py"
    "ecg_processor.py"
    "data_storage.py"
)

ALL_FILES_OK=true
for file in "${REQUIRED_FILES[@]}"; do
    if [ ! -f "$file" ]; then
        echo "❌ Missing required file: $file"
        ALL_FILES_OK=false
    fi
done

if [ "$ALL_FILES_OK" = true ]; then
    echo "✅ All application files present"
else
    echo "❌ Some application files are missing"
    exit 1
fi

echo ""

# ============================================================================
# 6. Parse Command Line Arguments
# ============================================================================
# Pass all arguments to the Python script
ARGS="$@"

# ============================================================================
# 7. Run Application
# ============================================================================
echo "╔════════════════════════════════════════════════════════════╗"
echo "║              🚀 Starting EDGE Layer                        ║"
echo "╚════════════════════════════════════════════════════════════╝"
echo ""
echo "📝 Notes:"
echo "  - Embedded MQTT broker will start automatically"
echo "  - Broker listens on port 1885 (all interfaces) - different from simulator (1883)"
echo "  - UDP discovery responder on port 1886 - different from simulator (1884)"
echo "  - ESP32 will automatically discover and connect"
echo "  - Received ECG data will be processed and saved"
echo "  - Press Ctrl+C to stop"
echo ""
echo "🎯 Application starting..."
echo ""

# Run the application with all arguments (using venv Python)
"$PYTHON" main.py $ARGS

# Capture exit code
EXIT_CODE=$?

echo ""
echo "╔════════════════════════════════════════════════════════════╗"
echo "║              Application Stopped                           ║"
echo "╚════════════════════════════════════════════════════════════╝"

if [ $EXIT_CODE -eq 0 ]; then
    echo "✅ Application exited normally"
else
    echo "⚠️  Application exited with code: $EXIT_CODE"
fi

echo ""
echo "To run again: ./run.sh"
echo "To use external broker: ./run.sh --no-broker --broker-ip <IP>"
echo "To see help: ./run.sh --help"
echo ""

exit $EXIT_CODE

