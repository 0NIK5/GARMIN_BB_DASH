@echo off
set ROOT=%~dp0

start "Backend" cmd /k "cd /d %ROOT%backend && python -m uvicorn app.main:app --reload --host 0.0.0.0 --port 8000"
start "Frontend" cmd /k "cd /d %ROOT%frontend && python -m http.server 5500"
start "Worker" cmd /k "cd /d %ROOT% && python -m worker.worker"

timeout /t 7 /nobreak >nul
start http://127.0.0.1:5500
