@echo off
REM Reality Machine — drive-wide heal (finished programs, not reports)
cd /d D:\Mythos_Apex
set PYTHONPATH=D:\Mythos_Apex;%PYTHONPATH%
set PY=D:\Mythos_Apex\.venv\Scripts\python.exe
if not exist "%PY%" set PY=python
echo.
echo === REALITY MACHINE ===
echo Point at drives once. Machine finds + heals programs. Internet ON.
echo Deliverable: fixed programs on disk — not status reports.
echo.
if /I "%~1"=="status" (
  "%PY%" -c "from advanced_shards.reality_machine_limb import RealityMachineLimb; import json; print(json.dumps(RealityMachineLimb().status(), indent=2, default=str))"
  goto :eof
)
if /I "%~1"=="census" (
  "%PY%" -c "from advanced_shards.reality_machine_limb import RealityMachineLimb; import json; print(json.dumps(RealityMachineLimb().census(drives='D,E,G', only_broken=True), indent=2, default=str))"
  goto :eof
)
if /I "%~1"=="autonomy" (
  "%PY%" -c "from advanced_shards.reality_machine_limb import RealityMachineLimb; import json; print(json.dumps(RealityMachineLimb().autonomy(drives='D,E,G', max_projects=3, allow_internet=True, refresh_census=False), indent=2, default=str))"
  goto :eof
)
if /I "%~1"=="continue" (
  "%PY%" -c "from advanced_shards.reality_machine_limb import RealityMachineLimb; import json; print(json.dumps(RealityMachineLimb().continue_solve(max_steps=8), indent=2, default=str))"
  goto :eof
)
if "%~1"=="" (
  echo Default: autonomy heal pass (does NOT re-scan / redo already healed)
  echo Other: START_REALITY_MACHINE.bat status ^| census ^| autonomy ^| continue ^| "custom goal"
  echo.
  "%PY%" -c "from advanced_shards.reality_machine_limb import RealityMachineLimb; import json; print(json.dumps(RealityMachineLimb().autonomy(drives='D,E,G', max_projects=3, allow_internet=True, refresh_census=False), indent=2, default=str))"
  pause
  exit /b 0
)
"%PY%" -c "from advanced_shards.reality_machine_limb import RealityMachineLimb; import json,sys; g=' '.join(sys.argv[1:]); print(json.dumps(RealityMachineLimb().solve(goal=g, max_steps=8, allow_internet=True), indent=2, default=str))" %*
echo.
pause
