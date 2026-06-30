@echo off
set "APP_DIR=%~dp0"
cd /d "%APP_DIR%"
"%APP_DIR%.venv\Scripts\python.exe" "%APP_DIR%gui_clicker.py"
if errorlevel 1 pause
