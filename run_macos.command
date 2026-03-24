#!/bin/bash
# YILDIZ Ders Otomasyonu - macOS Launcher
# v1.0 (with venv support)

echo "========================================"
echo "YILDIZ Ders Otomasyonu v1.0"
echo "========================================"
echo ""

# Change to script directory
cd "$(dirname "$0")" || exit 1

# Check if Python 3 is installed
if ! command -v python3 &> /dev/null; then
    echo "[ERROR] Python 3 not found!"
    echo ""
    echo "Please install Python 3.9+ from:"
    echo "https://www.python.org/downloads/"
    echo ""
    echo "Or use Homebrew:"
    echo "  brew install python@3.12"
    echo ""
    read -p "Press Enter to exit..."
    exit 1
fi

echo "[OK] Python found: $(python3 --version)"
echo ""

# Virtual environment directory
VENV_DIR="venv"

# Check if venv exists, if not create it
if [ ! -d "$VENV_DIR" ]; then
    echo "[INFO] Creating virtual environment..."
    python3 -m venv "$VENV_DIR"
    if [ $? -ne 0 ]; then
        echo "[ERROR] Failed to create virtual environment"
        read -p "Press Enter to exit..."
        exit 1
    fi
    echo "[OK] Virtual environment created"
    echo ""

    # Upgrade pip immediately after venv creation
    echo "[INFO] Upgrading pip..."
    source "$VENV_DIR/bin/activate"
    python -m pip install --upgrade pip
    echo "[OK] pip upgraded"
    echo ""
fi

# Activate virtual environment
echo "[INFO] Activating virtual environment..."
source "$VENV_DIR/bin/activate"
if [ $? -ne 0 ]; then
    echo "[ERROR] Failed to activate virtual environment"
    read -p "Press Enter to exit..."
    exit 1
fi
echo "[OK] Virtual environment activated"
echo ""

# Check if dependencies need to be installed
echo "Checking dependencies..."
if ! pip show requests beautifulsoup4 lxml &> /dev/null; then
    echo ""
    echo "[INFO] Installing dependencies..."
    pip install -r requirements.txt
    if [ $? -ne 0 ]; then
        echo "[ERROR] Failed to install dependencies"
        read -p "Press Enter to exit..."
        exit 1
    fi
    echo "[OK] Dependencies installed"
fi

echo "[OK] Dependencies ready"
echo ""
echo "Starting automation..."
echo ""

# Run main.py
python main.py

# Check exit code
if [ $? -ne 0 ]; then
    echo ""
    echo "[ERROR] Program exited with error"
    read -p "Press Enter to exit..."
fi

# Deactivate venv on exit
deactivate 2>/dev/null
