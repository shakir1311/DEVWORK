#!/bin/bash
# Setup script for EDGE Layer (ECG Data Processor)

echo "EDGE Layer - Setup Script"
echo "========================="
echo ""

# Check Python version
echo "Checking Python version..."
python3 --version

if [ $? -ne 0 ]; then
    echo "Error: Python 3 is not installed"
    echo ""
    echo "Please install Python 3.8 or higher:"
    echo "  macOS:   brew install python3"
    echo "  Ubuntu:  sudo apt-get install python3 python3-pip python3-venv"
    echo "  Pi4:     sudo apt-get install python3 python3-pip python3-venv"
    exit 1
fi

# Get script directory
SCRIPT_DIR="$( cd "$( dirname "${BASH_SOURCE[0]}" )" && pwd )"

# Set paths to venv Python and pip
PYTHON="${SCRIPT_DIR}/venv/bin/python"
PIP="${SCRIPT_DIR}/venv/bin/pip"

# Create virtual environment (remove if exists and broken)
echo ""
if [ -d "venv" ]; then
    if [ ! -f "$PYTHON" ] || [ ! -f "$PIP" ]; then
        echo "Virtual environment exists but appears incomplete or broken"
        echo "Removing old virtual environment..."
        rm -rf venv
    else
        echo "Virtual environment already exists and is valid"
        echo "Skipping creation..."
    fi
fi

if [ ! -d "venv" ]; then
    echo "Creating virtual environment..."
    python3 -m venv venv
    
    if [ $? -ne 0 ]; then
        echo "Error: Failed to create virtual environment"
        exit 1
    fi
fi

# Activate virtual environment
echo "Activating virtual environment..."
source venv/bin/activate

# Verify venv Python and pip exist
if [ ! -f "$PYTHON" ]; then
    echo "❌ Error: Virtual environment Python not found at $PYTHON"
    exit 1
fi

if [ ! -f "$PIP" ]; then
    echo "❌ Error: Virtual environment pip not found at $PIP"
    exit 1
fi

# Install dependencies
echo ""
echo "Installing dependencies..."
"$PIP" install --upgrade pip
"$PIP" install -r requirements.txt

if [ $? -ne 0 ]; then
    echo "Error: Failed to install dependencies"
    exit 1
fi

echo ""
echo "========================="
echo "Setup complete!"
echo ""
echo "To start the application:"
echo "  ./run.sh"
echo ""
echo "Or manually:"
echo "  1. Activate virtual environment: source venv/bin/activate"
echo "  2. Run the application: python main.py"
echo ""
echo "The EDGE layer includes an embedded MQTT broker, so no external"
echo "MQTT server setup is needed. The broker will start automatically."
echo ""

