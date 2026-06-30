@echo off
setlocal
cd /d "%~dp0"

set "PY_CMD="

where py >nul 2>nul
if not errorlevel 1 (
    py -3.12 -c "import sys" >nul 2>nul
    if not errorlevel 1 set "PY_CMD=py -3.12"
)

if not defined PY_CMD (
    where py >nul 2>nul
    if not errorlevel 1 (
        py -3 -c "import sys" >nul 2>nul
        if not errorlevel 1 set "PY_CMD=py -3"
    )
)

if not defined PY_CMD (
    where python >nul 2>nul
    if not errorlevel 1 (
        python -c "import sys" >nul 2>nul
        if not errorlevel 1 set "PY_CMD=python"
    )
)

if not defined PY_CMD (
    where python3 >nul 2>nul
    if not errorlevel 1 (
        python3 -c "import sys" >nul 2>nul
        if not errorlevel 1 set "PY_CMD=python3"
    )
)

if not defined PY_CMD (
    echo Python was not found or the Windows Store python alias is enabled.
    echo Install Python 3.12 from https://www.python.org/downloads/
    echo During install, tick "Add python.exe to PATH".
    echo If Python is already installed, disable:
    echo Settings ^> Apps ^> Advanced app settings ^> App execution aliases ^> python.exe / python3.exe
    pause
    exit /b 1
)

echo Using %PY_CMD%
%PY_CMD% -m venv .venv
if errorlevel 1 (
    echo Failed to create .venv
    pause
    exit /b 1
)

".venv\Scripts\python.exe" -m pip install --upgrade pip
if errorlevel 1 (
    echo Failed to upgrade pip
    pause
    exit /b 1
)

".venv\Scripts\python.exe" -m pip install -r requirements.txt
if errorlevel 1 (
    echo Failed to install requirements
    pause
    exit /b 1
)

echo.
echo Setup complete. Open start_gui.cmd or start_gui.vbs to run.
pause
