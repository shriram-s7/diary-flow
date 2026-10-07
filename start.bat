@echo off
rem DairyFlow (Windows): backend on 127.0.0.1:8600, frontend on :5600 with the Vite proxy (/api and /ws -> backend).
rem   start.bat               dev: the backend reloads when backend\*.py changes
rem   start.bat --no-reload   demo: the backend never restarts
rem Ports: set DAIRYFLOW_API_PORT / DAIRYFLOW_UI_PORT before running. Route cache and logs: .cache\
setlocal EnableExtensions
set "ROOT=%~dp0"
if "%DAIRYFLOW_API_PORT%"=="" set "DAIRYFLOW_API_PORT=8600"
if "%DAIRYFLOW_UI_PORT%"=="" set "DAIRYFLOW_UI_PORT=5600"
set "DAIRYFLOW_API_HOST=127.0.0.1"
set "RELOAD_FLAG="
if /I "%~1"=="--no-reload" set "RELOAD_FLAG=--no-reload"

for %%P in (%DAIRYFLOW_API_PORT% %DAIRYFLOW_UI_PORT%) do (
  netstat -ano | findstr /R /C:"LISTENING" | findstr /C:":%%P " >nul && (
    echo Port %%P is already in use ^(an earlier DairyFlow still running?^). Close it, or set DAIRYFLOW_API_PORT / DAIRYFLOW_UI_PORT.
    exit /b 1
  )
)

set "PY=%ROOT%backend\venv\Scripts\python.exe"
if not exist "%PY%" (
  echo Creating backend\venv ...
  python -m venv "%ROOT%backend\venv" || exit /b 1
  "%PY%" -m pip install -r "%ROOT%backend\requirements-dev.txt" || exit /b 1
)
if not exist "%ROOT%frontend\node_modules" (
  echo Installing frontend packages ...
  pushd "%ROOT%frontend" && call npm install && popd
)

echo Starting backend on http://127.0.0.1:%DAIRYFLOW_API_PORT% %RELOAD_FLAG%
start "DairyFlow backend :%DAIRYFLOW_API_PORT%" /D "%ROOT%backend" cmd /k ""%PY%" main.py %RELOAD_FLAG%"

rem Wait for the backend before starting the UI, so the proxy never sees "connection refused".
set /a WAITED=0
:wait_backend
curl -s -o nul -m 2 "http://127.0.0.1:%DAIRYFLOW_API_PORT%/health" && goto backend_up
set /a WAITED+=1
if %WAITED% GEQ 60 (
  echo Backend did not answer on port %DAIRYFLOW_API_PORT% within 60 s - see the backend window or .cache\logs\backend.log
  exit /b 1
)
ping -n 2 127.0.0.1 >nul
goto wait_backend
:backend_up
echo Backend is up.

start "DairyFlow frontend :%DAIRYFLOW_UI_PORT%" /D "%ROOT%frontend" cmd /k "npm run dev"
echo.
echo DairyFlow is starting:
echo   App        http://localhost:%DAIRYFLOW_UI_PORT%
echo   API docs   http://127.0.0.1:%DAIRYFLOW_API_PORT%/docs
echo   Health     http://127.0.0.1:%DAIRYFLOW_API_PORT%/health
echo Close the two DairyFlow windows to stop.
endlocal
