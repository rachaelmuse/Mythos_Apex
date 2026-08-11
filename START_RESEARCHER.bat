@echo off
title MYTHOS RESEARCHER — internet sweeps (no chat LLM)
cd /d "%~dp0"
set PYTHON_EXE=%~dp0.venv\Scripts\python.exe
if not exist "%PYTHON_EXE%" set PYTHON_EXE=python

echo.
echo  Mythos Researcher — runs research.web on your queue
echo  Queue: mythos_state\research_queue.json
echo  Output: memory\research_sweeps\
echo.
echo  Add topics like:  {"topics": ["Palia updates", "ComfyUI workflow tips"]}
echo.

"%PYTHON_EXE%" "%~dp0mythos_research_daemon.py" %*
pause
