@echo off
cd /d "%~dp0"
echo UpBain — starting...
python tool__tauto_nostage.py
if errorlevel 1 pause
