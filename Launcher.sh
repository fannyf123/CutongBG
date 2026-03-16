#!/bin/bash
echo "================================================"
echo "          CutongBG - Background Remover"
echo "================================================"
echo

# Check Python
if ! command -v python3 &> /dev/null; then
    echo "[ERROR] Python3 tidak ditemukan!"
    echo "Install: sudo apt install python3 python3-pip (Ubuntu/Debian)"
    exit 1
fi

echo "[OK] Python3 ditemukan."

# Install dependencies
echo
echo "[INFO] Memeriksa dan menginstall dependencies..."
pip3 install -r requirements.txt --quiet

# Run app
echo
echo "[INFO] Menjalankan CutongBG..."
echo
python3 main.py
