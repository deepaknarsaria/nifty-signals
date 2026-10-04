@echo off
cd /d "%~dp0"
if not exist logs mkdir logs
echo NIFTY signal system. Leave this window open until 3:15 pm.
python live_signals.py
pause
