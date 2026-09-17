@echo off
chcp 65001 >nul
setlocal
set "APP_DIR=%~dp0"
set "LOCAL_PY=%APP_DIR%.venv\Scripts\python.exe"

if exist "%LOCAL_PY%" (
    "%LOCAL_PY%" "%APP_DIR%image_preclassifier.py"
) else (
    py -3 "%APP_DIR%image_preclassifier.py"
)

if errorlevel 1 pause
endlocal

