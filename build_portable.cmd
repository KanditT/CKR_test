@echo off
setlocal
cd /d "%~dp0"

if not exist ".venv\Scripts\python.exe" (
    echo .venv was not found. Run setup.cmd first.
    pause
    exit /b 1
)

echo Installing/updating PyInstaller...
".venv\Scripts\python.exe" -m pip install --upgrade pyinstaller
if errorlevel 1 (
    echo Failed to install PyInstaller.
    pause
    exit /b 1
)

echo.
echo Building portable EXE...
".venv\Scripts\python.exe" -m PyInstaller ^
    --noconfirm ^
    --clean ^
    --onedir ^
    --windowed ^
    --name CookieRunRunner ^
    gui_clicker.py
if errorlevel 1 (
    echo Build failed.
    pause
    exit /b 1
)

echo.
echo Copying config and templates...
copy /Y config.py "dist\CookieRunRunner\config.py" >nul
if errorlevel 1 (
    echo Failed to copy config.py.
    pause
    exit /b 1
)

if exist "dist\CookieRunRunner\templates" rmdir /S /Q "dist\CookieRunRunner\templates"
xcopy /E /I /Y templates "dist\CookieRunRunner\templates" >nul
if errorlevel 1 (
    echo Failed to copy templates.
    pause
    exit /b 1
)

powershell -NoProfile -ExecutionPolicy Bypass -Command ^
    "if (Test-Path 'dist\CookieRunRunner-portable.zip') { Remove-Item 'dist\CookieRunRunner-portable.zip' -Force }; Compress-Archive -Path 'dist\CookieRunRunner\*' -DestinationPath 'dist\CookieRunRunner-portable.zip'"
if errorlevel 1 (
    echo Failed to create zip.
    pause
    exit /b 1
)

echo.
echo Done.
echo Folder: dist\CookieRunRunner
echo Zip:    dist\CookieRunRunner-portable.zip
echo Send the zip to your friend. They run CookieRunRunner.exe.
pause
