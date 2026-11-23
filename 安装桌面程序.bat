@echo off
setlocal
cd /d "%~dp0"
if not exist ".venv\Scripts\python.exe" (
    python -m venv .venv
    if errorlevel 1 goto failed
)
".venv\Scripts\python.exe" -m pip install -r requirements_desktop.txt
if errorlevel 1 goto failed
pushd frontend
call npm ci
if errorlevel 1 goto frontend_failed
call npm run build
if errorlevel 1 goto frontend_failed
popd
echo Desktop setup completed. Run the desktop launcher to start.
pause
exit /b 0
:frontend_failed
popd
:failed
echo Setup failed. Check the error above. Python and Node.js are required.
pause
exit /b 1
