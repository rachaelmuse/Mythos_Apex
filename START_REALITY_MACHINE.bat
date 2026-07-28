@echo off
REM Reality Machine — system Mixture-of-Experts orchestrator
cd /d D:\Mythos_Apex
set PYTHONPATH=D:\Mythos_Apex;%PYTHONPATH%
echo.
echo === REALITY MACHINE ===
echo Resource-aware autonomous problem solver (system MoE)
echo.
if "%~1"=="" (
  echo Usage: START_REALITY_MACHINE.bat "your goal here"
  echo Example: START_REALITY_MACHINE.bat "Fix the broken Mythos Colibri MoE bring-up"
  echo.
  if exist "D:\Mythos_Apex\.venv\Scripts\python.exe" (
    "D:\Mythos_Apex\.venv\Scripts\python.exe" -c "from advanced_shards.reality_machine_limb import RealityMachineLimb; import json; print(json.dumps(RealityMachineLimb().status(), indent=2, default=str))"
  )
  pause
  exit /b 0
)
if exist "D:\Mythos_Apex\.venv\Scripts\python.exe" (
  "D:\Mythos_Apex\.venv\Scripts\python.exe" -c "from advanced_shards.reality_machine_limb import RealityMachineLimb; import json,sys; g=' '.join(sys.argv[1:]); print(json.dumps(RealityMachineLimb().solve(goal=g, max_steps=8), indent=2, default=str))" %*
) else (
  python -c "from advanced_shards.reality_machine_limb import RealityMachineLimb; import json,sys; g=' '.join(sys.argv[1:]); print(json.dumps(RealityMachineLimb().solve(goal=g, max_steps=8), indent=2, default=str))" %*
)
echo.
pause
