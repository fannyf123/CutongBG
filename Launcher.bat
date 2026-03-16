@echo off
chcp 65001 >nul
title CutongBG Launcher

echo ================================================
echo           CutongBG - Background Remover
echo ================================================
echo.

:: Check Python
python --version >nul 2>&1
if errorlevel 1 (
    echo [ERROR] Python tidak ditemukan!
    echo Silakan install Python dari https://www.python.org/downloads/
    echo Pastikan centang "Add Python to PATH" saat instalasi.
    pause
    exit /b 1
)

echo [OK] Python ditemukan.

:: Check pip
pip --version >nul 2>&1
if errorlevel 1 (
    echo [ERROR] pip tidak ditemukan!
    pause
    exit /b 1
)

:: Install dependencies
echo.
echo [INFO] Memeriksa dan menginstall dependencies...
pip install -r requirements.txt --quiet
if errorlevel 1 (
    echo [WARNING] Beberapa dependency mungkin gagal diinstall.
)

:: Check ChromeDriver
echo.
echo [INFO] Memeriksa ChromeDriver...
python main.py --check-driver
if errorlevel 1 (
    echo [INFO] ChromeDriver akan diunduh otomatis saat pertama kali dijalankan.
)

:: Run app
echo.
echo [INFO] Menjalankan CutongBG...
echo.
python main.py

if errorlevel 1 (
    echo.
    echo [ERROR] Aplikasi berhenti dengan error.
    pause
)
