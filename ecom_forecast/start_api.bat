@echo off
cmd /k "cd /d "%~dp0" && pip install -r requirements_api.txt -q && uvicorn api.main:app --port 8000 --host 0.0.0.0"
