@echo off
setlocal
cd /d "%~dp0"
if not exist ".venv\Scripts\pythonw.exe" goto missing
if not exist "frontend\node_modules\vite\bin\vite.js" goto missing
start "" ".venv\Scripts\pythonw.exe" "%~dp0desktop.pyw" --dev
exit /b 0
:missing
echo Please run the desktop setup script first. Node.js is required for development.
pause
exit /b 1
