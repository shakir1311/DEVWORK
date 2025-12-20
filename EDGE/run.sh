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
# 1. Check Python & Virtual Environment
# ============================================================================
echo "🔍 Step 1: Checking Python environment..."

get_py_ver() {
    "$1" -c 'import sys; print(f"{sys.version_info.major}.{sys.version_info.minor}")' 2>/dev/null
}

SKIP_CREATION=false

# 1a. Check for existing valid venv
if [ -d "venv" ] && [ -x "venv/bin/python3" ]; then
    VENV_VER=$(get_py_ver "venv/bin/python3")
    
    # Check if version is valid (3.10-3.12 for amqtt + PyTorch)
    MAJOR=$(echo "$VENV_VER" | cut -d. -f1)
    MINOR=$(echo "$VENV_VER" | cut -d. -f2)
    
    if [ "$MAJOR" -eq 3 ] && [ "$MINOR" -ge 10 ] && [ "$MINOR" -le 12 ]; then
        echo "✅ Using existing virtual environment (Python $VENV_VER)"
        SKIP_CREATION=true
    else
        echo "♻️  Existing venv (Python $VENV_VER) needs 3.10-3.12. Recreating..."
        rm -rf venv
    fi
fi

# 1b. If no valid venv, find system python to create one
if [ "$SKIP_CREATION" = false ]; then
    echo "🔍 Searching for compatible system Python..."
    
    # Function to check if python version is valid (3.10-3.12 for amqtt + PyTorch)
    check_python_candidate() {
        local cmd=$1
        if ! command -v "$cmd" &> /dev/null && [ ! -x "$cmd" ]; then return 1; fi
        
        ver=$(get_py_ver "$cmd")
        major=$(echo "$ver" | cut -d. -f1)
        minor=$(echo "$ver" | cut -d. -f2)
        
        # 3.10, 3.11, or 3.12 only (PyTorch doesn't support 3.13 yet)
        if [ "$major" -eq 3 ] && [ "$minor" -ge 10 ] && [ "$minor" -le 12 ]; then
            return 0
        fi
        return 1
    }

    # Search order: prefer 3.12 for best compatibility
    if check_python_candidate "python3.12"; then PYTHON_CMD="python3.12"
    elif check_python_candidate "python3.11"; then PYTHON_CMD="python3.11"
    elif check_python_candidate "python3.10"; then PYTHON_CMD="python3.10"
    elif check_python_candidate "/opt/homebrew/bin/python3.12"; then PYTHON_CMD="/opt/homebrew/bin/python3.12"
    elif check_python_candidate "/usr/local/bin/python3.12"; then PYTHON_CMD="/usr/local/bin/python3.12"
    elif check_python_candidate "python3"; then PYTHON_CMD="python3"
    else
        # No compatible Python found - auto-install Python 3.12 via Homebrew
        echo "⚠️  No compatible Python 3.10-3.12 found."
        echo "   - amqtt 0.11+ requires Python 3.10+"
        echo "   - PyTorch requires Python ≤3.12"
        echo ""
        
        if command -v brew &> /dev/null; then
            echo "🍺 Installing Python 3.12 via Homebrew..."
            brew install python@3.12
            
            # Find the installed python
            if [ -x "/opt/homebrew/bin/python3.12" ]; then
                PYTHON_CMD="/opt/homebrew/bin/python3.12"
            elif [ -x "/usr/local/bin/python3.12" ]; then
                PYTHON_CMD="/usr/local/bin/python3.12"
            else
                echo "❌ Error: Python 3.12 installed but not found in expected paths"
                exit 1
            fi
            echo "✅ Python 3.12 installed successfully"
        else
            echo "❌ Error: Homebrew not found. Cannot auto-install Python 3.12."
            echo "   Please install Homebrew first: /bin/bash -c \"\$(curl -fsSL https://raw.githubusercontent.com/Homebrew/install/HEAD/install.sh)\""
            echo "   Or manually install Python 3.12"
            exit 1
        fi
    fi
    
    FOUND_VER=$("$PYTHON_CMD" --version 2>&1 | awk '{print $2}')
    echo "📦 Creating virtual environment with $PYTHON_CMD ($FOUND_VER)..."
    "$PYTHON_CMD" -m venv venv
    
    if [ $? -ne 0 ]; then
        echo "❌ Failed to create virtual environment"
        exit 1
    fi
    echo "✅ Virtual environment created successfully"
fi

# 2. Activate
echo "🔍 Step 2: Activating virtual environment..."
source venv/bin/activate
if [ $? -ne 0 ]; then
    echo "❌ Failed to activate virtual environment"
    exit 1
fi

# Set paths for subsequent use
PYTHON="python3"
PIP="pip"

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
# Uses importlib.util.find_spec to check existence without loading the module (much faster)
check_package() {
    "$PYTHON" -c "import importlib.util, sys; sys.exit(0 if importlib.util.find_spec('$1') else 1)" 2>/dev/null
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

# ML Inference Dependencies
echo "  Checking torch (ML)..."
if ! check_package "torch"; then
    echo "  ❌ torch not installed"
    NEED_INSTALL=true
else
    echo "  ✅ torch installed"
fi

echo "  Checking sklearn (ML)..."
if ! check_package "sklearn"; then
    echo "  ❌ sklearn not installed"
    NEED_INSTALL=true
else
    echo "  ✅ sklearn installed"
fi

echo "  Checking joblib (ML)..."
if ! check_package "joblib"; then
    echo "  ❌ joblib not installed"
    NEED_INSTALL=true
else
    echo "  ✅ joblib installed"
fi

echo "  Checking scipy (ML)..."
if ! check_package "scipy"; then
    echo "  ❌ scipy not installed"
    NEED_INSTALL=true
else
    echo "  ✅ scipy installed"
fi

echo "  Checking requests..."
if ! check_package "requests"; then
    echo "  ❌ requests not installed"
    NEED_INSTALL=true
else
    echo "  ✅ requests installed"
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
# 6. Setup ECG-DualNet (for standalone deployment)
# ============================================================================
echo "🔍 Step 6: Checking ECG-DualNet setup..."

"$PYTHON" setup_ecg_dualnet.py
if [ $? -ne 0 ]; then
    echo "⚠️  ECG-DualNet setup incomplete - classification may be limited"
else
    echo "✅ ECG-DualNet ready"
fi

echo ""

# ============================================================================
# 7. Parse Command Line Arguments
# ============================================================================
# Pass all arguments to the Python script
ARGS="$@"

# ============================================================================
# 8. Run Application
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

