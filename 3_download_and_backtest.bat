@echo off
cd /d "%~dp0"
if not exist logs mkdir logs
echo Downloading 3 years of NIFTY data. This takes a few minutes...
python angel_data.py download --symbol NIFTY --years 3 > logs\download.txt 2>&1
type logs\download.txt
python backtest.py --symbol NIFTY > logs\backtest.txt 2>&1
type logs\backtest.txt
pause
