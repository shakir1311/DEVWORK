#!/bin/bash
# Run script for ECG Simulator
# Checks/installs requirements and runs the application

set -e  # Exit on error

echo "╔════════════════════════════════════════════════════════════╗"
echo "║         ECG Simulator - Auto Run Script                   ║"
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

if [ ! -d "venv" ]; then
    echo "📦 Virtual environment not found. Creating..."
    python3 -m venv venv
    if [ $? -eq 0 ]; then
        echo "✅ Virtual environment created successfully"
    else
        echo "❌ Failed to create virtual environment"
        exit 1
    fi
else
    echo "✅ Virtual environment exists"
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

# Function to check if a package is installed
check_package() {
    python3 -c "import $1" 2>/dev/null
    return $?
}

# Check key packages
NEED_INSTALL=false

echo "  Checking PyQt6..."
if ! check_package "PyQt6"; then
    echo "  ❌ PyQt6 not installed"
    NEED_INSTALL=true
else
    echo "  ✅ PyQt6 installed"
fi

echo "  Checking scipy..."
if ! check_package "scipy"; then
    echo "  ❌ scipy not installed"
    NEED_INSTALL=true
else
    echo "  ✅ scipy installed"
fi

echo "  Checking numpy..."
if ! check_package "numpy"; then
    echo "  ❌ numpy not installed"
    NEED_INSTALL=true
else
    echo "  ✅ numpy installed"
fi

echo "  Checking paho-mqtt..."
if ! check_package "paho.mqtt" "paho"; then
    echo "  ❌ paho-mqtt not installed"
    NEED_INSTALL=true
else
    echo "  ✅ paho-mqtt installed"
fi

# Install dependencies if needed
if [ "$NEED_INSTALL" = true ]; then
    echo ""
    echo "📦 Installing missing dependencies..."
    pip install --upgrade pip -q
    pip install -r requirements.txt
    
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
# 5. MQTT Broker (Embedded - No External Setup Needed!)
# ============================================================================
echo "🔍 Step 5: MQTT broker check..."
echo "✅ Using embedded MQTT broker (no external setup needed!)"
echo ""

# ============================================================================
# 6. Check Application Files
# ============================================================================
echo "🔍 Step 6: Checking application files..."

REQUIRED_FILES=(
    "main.py"
    "ecg_simulator.py"
    "simulator_worker.py"
    "app_controller.py"
    "ecg_gui.py"
    "config.py"
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
# 7. Run Application
# ============================================================================
echo "╔════════════════════════════════════════════════════════════╗"
echo "║              🚀 Starting ECG Simulator                     ║"
echo "╚════════════════════════════════════════════════════════════╝"
echo ""
echo "📝 Notes:"
echo "  - Embedded MQTT broker included (no external setup needed!)"
echo "  - First run will download dataset (~167 MB, takes 2-5 min)"
echo "  - Dataset is cached for future runs"
echo "  - Connect to 'localhost:1883' in the GUI"
echo "  - Press Ctrl+C in this terminal to stop (after closing GUI)"
echo ""
echo "🎯 Application starting..."
echo ""

# Run the application
python3 main.py

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
echo "To view logs: Check the Event Log in the application"
echo ""

exit $EXIT_CODE

