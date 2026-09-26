@echo off
REM Launched by Windows Task Scheduler once a day. Reads DOPPLER_TOKEN from
REM the user's environment (set once via `setx DOPPLER_TOKEN "..."`, see
REM README.md) and runs the full daily research agent pass unattended.

set DOPPLER_EXE=%LOCALAPPDATA%\Microsoft\WinGet\Packages\Doppler.doppler_Microsoft.Winget.Source_8wekyb3d8bbwe\doppler.exe
cd /d "%~dp0"

if "%DOPPLER_TOKEN%"=="" (
    echo ERROR: DOPPLER_TOKEN environment variable not set.
    exit /b 1
)

"%DOPPLER_EXE%" run --token %DOPPLER_TOKEN% --project tpl-brain --config dev -- python run_daily.py
