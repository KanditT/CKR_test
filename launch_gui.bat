@echo off
cd /d "%~dp0"

:: Find a working Python (not the Microsoft Store stub)
set PY=
for %%C in (py python python3) do (
    if not defined PY (
        %%C -c "import sys; sys.exit(0)" >nul 2>&1
        if not errorlevel 1 set PY=%%C
    )
)

if not defined PY (
    echo.
    echo  Python is not installed.
    echo  Please download and install it from:
    echo  https://www.python.org/downloads/
    echo.
    echo  Make sure to check "Add Python to PATH" during installation.
    echo.
    pause
    exit /b 1
)

%PY% -c "import cv2, numpy, keyboard" 2>nul
if errorlevel 1 (
    echo Installing missing dependencies...
    %PY% -m pip install -r requirements.txt
)
%PY% gui.py
if errorlevel 1 pause
