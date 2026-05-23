@echo off
cd /d "%~dp0"
title SMAILO Bot
color 0A
echo ================================
echo        SMAILO Bot Starting
echo        Web: http://localhost:12337
echo ================================
python main.py
echo.
echo Bot stopped. Press any key to exit...
pause >nul
