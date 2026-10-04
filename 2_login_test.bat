@echo off
cd /d "%~dp0"
if not exist logs mkdir logs
python angel_data.py login > logs\login.txt 2>&1
type logs\login.txt
pause
