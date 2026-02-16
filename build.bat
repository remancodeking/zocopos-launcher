@echo off
set APP_NAME=ZocoPOS_Launcher
echo ========================================
echo   ZOCO POS LAUNCHER - BUILD
echo ========================================

echo Building Launcher EXE...
pyinstaller --noconsole --onefile --name "%APP_NAME%" ^
    --icon="assets/icon.png" ^
    --add-data "assets;assets" ^
    --add-data "ui;ui" ^
    main.py

if %errorlevel% neq 0 (
    echo [ERROR] Build failed!
    pause
    exit /b %errorlevel%
)

echo ========================================
echo   LAUNCHER BUILD COMPLETE!
echo   Output: dist\%APP_NAME%.exe
echo ========================================
pause
