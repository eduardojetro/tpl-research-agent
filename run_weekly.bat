@echo off
REM Launched by Windows Task Scheduler once a week. Same pattern as
REM run_daily.bat -- reads DOPPLER_TOKEN from the environment and runs the
REM Instagram+TikTok collection pass unattended.

set DOPPLER_EXE=%LOCALAPPDATA%\Microsoft\WinGet\Packages\Doppler.doppler_Microsoft.Winget.Source_8wekyb3d8bbwe\doppler.exe
cd /d "%~dp0"

if "%DOPPLER_TOKEN%"=="" (
    echo ERROR: DOPPLER_TOKEN environment variable not set.
    exit /b 1
)

"%DOPPLER_EXE%" run --token %DOPPLER_TOKEN% --project tpl-brain --config dev -- python run_weekly.py
