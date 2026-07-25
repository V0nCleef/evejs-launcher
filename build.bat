@echo off
echo ============================================================
echo   EveJS Multi-Box Launcher V2 - Build Script
echo ============================================================
echo.
echo Step 1/3: Installing dependencies...
python -m pip install -r requirements.txt --quiet
if errorlevel 1 (
    echo [ERROR] Failed to install dependencies
    pause
    exit /b 1
)

echo Step 2/3: Building optimized executable...
python -m PyInstaller build.spec --clean --noconfirm
if errorlevel 1 (
    echo [ERROR] Build failed
    pause
    exit /b 1
)

echo Step 3/3: Verifying build...
if exist "dist\EveJS-Launcher-V1\EveJS-Launcher-V1.exe" (
    echo.
    echo ============================================================
    echo   BUILD SUCCESSFUL
    echo   Output: dist\EveJS-Launcher-V1\  (folder)
    echo   Run:   dist\EveJS-Launcher-V1\EveJS-Launcher-V1.exe
    echo ============================================================
) else (
    echo [ERROR] Executable not found
    pause
    exit /b 1
)
echo.
echo Done! Share the executable from the 'dist' folder.
pause
