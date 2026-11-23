@echo off
setlocal
cd /d "%~dp0"
if not exist ".venv\Scripts\pythonw.exe" goto missing
if not exist "frontend\dist\index.html" goto missing
start "" ".venv\Scripts\pythonw.exe" "%~dp0desktop.pyw"
exit /b 0
:missing
echo Please run the desktop setup script first.
pause
exit /b 1
