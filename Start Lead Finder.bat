@echo off
rem Double-click to start Lead Finder. Keep the window that opens; closing it stops the app.
title Lead Finder
cd /d "%~dp0"

where python >nul 2>nul
if %errorlevel%==0 (
    python analyze.py
    goto done
)

where py >nul 2>nul
if %errorlevel%==0 (
    py analyze.py
    goto done
)

echo Python 3 is needed to run Lead Finder but was not found.
echo Install it from https://www.python.org/downloads/ and tick "Add Python to PATH".

:done
pause
