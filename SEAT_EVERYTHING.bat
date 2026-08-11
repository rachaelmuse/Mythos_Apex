@echo off
title MYTHOS SEAT EVERYTHING — wire disconnected tools
cd /d "%~dp0"
set PYTHON_EXE=%~dp0.venv\Scripts\python.exe
if not exist "%PYTHON_EXE%" set PYTHON_EXE=python

echo.
echo  Seating Playwright Chromium, edge-tts, Composio, screen memory, TTS...
echo  This can take several minutes (browser download).
echo.

"%PYTHON_EXE%" "%~dp0mythos_seat_tools.py"
echo.
echo  Report: mythos_state\SEAT_REPORT.json
echo  Composio key: config\composio.env  (https://dashboard.composio.dev/settings)
pause
