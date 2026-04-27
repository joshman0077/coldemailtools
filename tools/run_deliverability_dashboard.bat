@echo off
echo.
echo  Deliverability Dashboard
echo  Starting server...
echo.
cd /d "%~dp0\.."
python tools\deliverability_dashboard.py
pause
