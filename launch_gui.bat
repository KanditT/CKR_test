@echo off
cd /d "%~dp0"
py -c "import cv2, numpy, keyboard" 2>nul
if errorlevel 1 (
    echo Installing missing dependencies...
    py -m pip install -r requirements.txt
)
py gui.py
if errorlevel 1 pause
