@echo off
if not exist "%~dp0.venv\Scripts\pythonw.exe" (
    echo Python environment not found.
    echo Run setup.cmd first.
    pause
    exit /b 1
)
wscript.exe "%~dp0start_gui.vbs"
