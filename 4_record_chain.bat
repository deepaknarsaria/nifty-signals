@echo off
cd /d "%~dp0"
if not exist logs mkdir logs
echo Recording NIFTY option chain every 5 minutes. Leave this window open until 3:30 pm.
python chain_recorder.py --symbol NIFTY
pause
