#!/bin/bash
# Setup script for ECG Simulator

echo "ECG Simulator - Setup Script"
echo "============================="
echo ""

# Check Python version
echo "Checking Python version..."
python3 --version

if [ $? -ne 0 ]; then
    echo "Error: Python 3 is not installed"
    exit 1
fi

# Create virtual environment
echo ""
echo "Creating virtual environment..."
python3 -m venv venv

# Activate virtual environment
echo "Activating virtual environment..."
source venv/bin/activate

# Install dependencies
echo ""
echo "Installing dependencies..."
pip install --upgrade pip
pip install -r requirements.txt

if [ $? -ne 0 ]; then
    echo "Error: Failed to install dependencies"
    exit 1
fi

echo ""
echo "============================="
echo "Setup complete!"
echo ""
echo "To start the application:"
echo "  1. Activate virtual environment: source venv/bin/activate"
echo "  2. Run the application: python main.py"
echo ""
echo "Make sure you have an MQTT broker running (e.g., Mosquitto)"
echo "  macOS: brew install mosquitto && brew services start mosquitto"
echo "  Linux: sudo apt-get install mosquitto && sudo systemctl start mosquitto"
echo ""

