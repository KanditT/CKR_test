@echo off
setlocal
cd /d "%~dp0"

if not exist .venv\Scripts\python.exe (
  echo Missing .venv. Run setup.cmd first.
  pause
  exit /b 1
)

.\.venv\Scripts\python.exe -m pip install -r agent\requirements.txt pyinstaller
if errorlevel 1 exit /b 1

.\.venv\Scripts\python.exe -m PyInstaller CookieRunAgent.spec --clean --noconfirm
if errorlevel 1 exit /b 1

copy /Y agent\config.example.json dist\CookieRunAgent\config.local.json >nul
copy /Y config.py dist\CookieRunAgent\config.py >nul
copy /Y config_loader.py dist\CookieRunAgent\config_loader.py >nul
copy /Y adb_client.py dist\CookieRunAgent\adb_client.py >nul
copy /Y auto_clicker.py dist\CookieRunAgent\auto_clicker.py >nul

if exist dist\CookieRunAgent\templates rmdir /S /Q dist\CookieRunAgent\templates
xcopy /E /I /Y templates dist\CookieRunAgent\templates >nul

powershell -NoProfile -ExecutionPolicy Bypass -Command ^
  "if (Test-Path 'dist\CookieRunAgent-portable.zip') { Remove-Item 'dist\CookieRunAgent-portable.zip' -Force }; Compress-Archive -Path 'dist\CookieRunAgent\*' -DestinationPath 'dist\CookieRunAgent-portable.zip'"
if errorlevel 1 exit /b 1

echo.
echo Built: dist\CookieRunAgent\CookieRunAgent.exe
echo Edit:  dist\CookieRunAgent\config.local.json
echo Zip:   dist\CookieRunAgent-portable.zip
echo.
pause
