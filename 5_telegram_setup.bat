@echo off
cd /d "%~dp0"
if not exist logs mkdir logs
python telegram_setup.py > logs\telegram.txt 2>&1
type logs\telegram.txt
pause
