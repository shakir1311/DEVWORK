#!/bin/bash
# Doctor's Portal Startup Script

# Configuration
VENV_DIR="venv"
PORT=8000
HOST="0.0.0.0"

# Colors
GREEN='\033[0;32m'
BLUE='\033[0;34m'
NC='\033[0m' # No Color

echo -e "${BLUE}========================================${NC}"
echo -e "${BLUE}       Doctor's Portal Startup          ${NC}"
echo -e "${BLUE}========================================${NC}"

# Check for Python 3
if command -v python3 &>/dev/null; then
    PYTHON_CMD=python3
else
    echo "Python 3 could not be found."
    exit 1
fi

# Create Virtual Environment if it doesn't exist
if [ ! -d "$VENV_DIR" ]; then
    echo -e "${GREEN}Creating virtual environment...${NC}"
    $PYTHON_CMD -m venv $VENV_DIR
fi

# Activate Virtual Environment
source $VENV_DIR/bin/activate

# Install requirements
if [ -f "requirements.txt" ]; then
    echo -e "${GREEN}Checking dependencies...${NC}"
    pip install -r requirements.txt > /dev/null 2>&1
    if [ $? -eq 0 ]; then
        echo -e "${GREEN}✓ Dependencies installed${NC}"
    else
        echo "Error installing dependencies."
        exit 1
    fi
else
    echo "requirements.txt not found!"
    exit 1
fi

# Initialize Database
echo -e "${GREEN}Initializing Database...${NC}"
python init_db.py

# Run the server
echo -e "${BLUE}Starting Portal Server on http://$HOST:$PORT${NC}"
echo -e "${BLUE}Press Ctrl+C to stop${NC}"
echo -e "${BLUE}========================================${NC}"

# Use uvicorn to run the app
python -m uvicorn main:app --host $HOST --port $PORT --reload
