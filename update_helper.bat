@echo off
setlocal enabledelayedexpansion
:: EveJS Launcher V2 — update helper (batch version)
:: Replaces the old exe with the new one, then restarts.
:: Args: %1=old_exe %2=new_exe %3=--restart (optional)

set "OLD_EXE=%~1"
set "NEW_EXE=%~2"
set "RESTART=%~3"

if not exist "%NEW_EXE%" (
    echo [ERROR] New exe not found: %NEW_EXE%
    pause
    exit /b 1
)

:: Wait for old launcher to fully exit
timeout /t 5 /nobreak >nul

:: Try to delete the old exe (may be locked briefly)
:retry_delete
del /f "%OLD_EXE%" 2>nul
if exist "%OLD_EXE%" (
    timeout /t 1 /nobreak >nul
    goto retry_delete
)

:: Move new exe into place
move /y "%NEW_EXE%" "%OLD_EXE%" >nul 2>&1
if errorlevel 1 (
    echo [ERROR] Failed to replace %OLD_EXE%
    pause
    exit /b 1
)

:: Brief pause to let filesystem settle
timeout /t 2 /nobreak >nul

:: Restart if requested — use explorer.exe to launch the new exe
:: as if the user double-clicked it.  This completely isolates the
:: new process from the batch file's environment.
if /i "%~3"=="--restart" (
    timeout /t 2 /nobreak >nul
    explorer.exe "%OLD_EXE%"
)

exit /b 0
