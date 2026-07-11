@echo off
echo ================================================================================
echo SMARKAFRICA E-Commerce Platform
echo Starting with Security ^& AI Chatbot Features
echo ================================================================================
echo.

REM Check if virtual environment exists
if not exist "venv\Scripts\python.exe" (
    echo [ERROR] Virtual environment not found!
    echo Please run: python -m venv venv
    echo Then: venv\Scripts\pip install -r requirements.txt
    pause
    exit /b 1
)

echo [INFO] Using Python from virtual environment
venv\Scripts\python.exe --version
echo.

echo [INFO] Starting Flask application...
echo [INFO] Server will be available at: http://127.0.0.1:5000
echo [INFO] Press Ctrl+C to stop the server
echo.
echo ================================================================================
echo.

REM Run the application
venv\Scripts\python.exe main.py

pause
