#!/bin/bash

# BlackBoT launcher script with environment and dependency check

# Exit on error
set -e

# Check for python3-venv
if ! dpkg -s python3-venv >/dev/null 2>&1; then
    echo "📦 Installing python3-venv..."
    sudo apt update && sudo apt install -y python3-venv
fi


echo "🔍 Checking Python virtual environment..."

# Path to your virtual environment
VENV_DIR="environment"
PYTHON_EXEC="$VENV_DIR/bin/python"
PIP_EXEC="$VENV_DIR/bin/pip"

# Required packages list
REQUIRED_PACKAGES=(
  twisted
  pyopenssl
  service_identity
  psutil
  scapy
  bcrypt
  watchdog
  requests
)

# 1. Create venv if not exists
if [ ! -d "$VENV_DIR" ]; then
  echo "📦 Creating virtual environment..."
  python3 -m venv "$VENV_DIR" || { echo "❌ Failed to create venv"; exit 1; }
fi

# 2. Activate venv
source "$VENV_DIR/bin/activate" || { echo "❌ Failed to activate venv"; exit 1; }

# 3. Install missing packages only
echo "📦 Checking for missing packages..."
for package in "${REQUIRED_PACKAGES[@]}"; do
  if ! $PIP_EXEC show "$package" > /dev/null 2>&1; then
    echo "➕ Installing $package..."
    $PIP_EXEC install "$package" || { echo "❌ Failed to install $package"; exit 1; }
  fi
done

# 4. Start the bot
echo "🚀 Starting BlackBoT..."
$PYTHON_EXEC Starter.py
