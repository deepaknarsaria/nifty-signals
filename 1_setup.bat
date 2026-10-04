@echo off
cd /d "%~dp0"
if not exist logs mkdir logs
python --version > logs\setup.txt 2>&1
if errorlevel 1 (echo Python is not installed. Install it from python.org and tick "Add python.exe to PATH", then run this again. & echo PYTHON MISSING > logs\setup.txt & pause & exit /b)
python -m pip install -r requirements.txt >> logs\setup.txt 2>&1
type logs\setup.txt
echo.
echo SETUP FINISHED >> logs\setup.txt
echo Setup finished. You can close this window.
pause
