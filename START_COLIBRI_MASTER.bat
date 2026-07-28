@echo off
REM Colibri Master — keep diagnosing/repairing/trying until MoE answers on :8010
cd /d D:\Mythos_Apex
set COLI_MODEL=D:\glm52_i4
set COLI_PORT=8010
echo.
echo === COLIBRI MASTER ===
echo Diagnose + repair corrupt shards + free RAM + try low-RAM profiles until UP.
echo.

if exist "D:\Mythos_Apex\.venv\Scripts\python.exe" (
  "D:\Mythos_Apex\.venv\Scripts\python.exe" -c "from advanced_shards.colibri_master_limb import ColibriMasterLimb; import json; print(json.dumps(ColibriMasterLimb().master(max_rounds=6, wait_sec=120), indent=2, default=str))"
) else (
  python -c "from advanced_shards.colibri_master_limb import ColibriMasterLimb; import json; print(json.dumps(ColibriMasterLimb().master(max_rounds=6, wait_sec=120), indent=2, default=str))"
)
echo.
pause
